r"""
MKV Ripper - rip DVDs/Blu-rays with MakeMKV and name the files for Plex.

The window is a web page (web/index.html) shown in a native window by pywebview, which uses
Windows' built-in WebView2 (the Edge engine). The page calls the Api class below for everything
that touches the disc, files, or the internet; the disc/rip/split/update logic is in mkv_core.py.

Run from source:  pip install pywebview  then  python mkv_ripper.py
The released app (MKVRipper.exe, or "MKV Ripper.app" on a Mac) needs no Python. The tools are installed/updated through
winget (Windows) or Homebrew/download pages (Mac) from Settings, and the app updates itself from GitHub Releases.
"""

import os
import sys
import threading
import time
import traceback

import mkv_core as core

RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
WEB_INDEX = os.path.join(RES_DIR, "web", "index.html")


class Api:
    """Everything the page can ask for. Public methods are callable from JavaScript as
    pywebview.api.<name>(...). Attributes start with _ so pywebview doesn't expose them."""

    def __init__(self):
        self._lock = threading.Lock()
        self._window = None
        self._settings = core.load_settings()
        self._makemkv = self._settings.get("makemkv") if os.path.isfile(self._settings.get("makemkv", "")) else core.find_makemkvcon()
        self._mkvmerge = self._settings.get("mkvmerge") if os.path.isfile(self._settings.get("mkvmerge", "")) else core.find_mkvmerge()
        self._log = []
        self._notice_id = 0
        self._s = {
            "build": core.APP_BUILD,
            "platform": "mac" if core.IS_MAC else "windows",
            "frozen": core.is_frozen(),
            "drives": [],
            "titles": [],
            "play_all": {},
            "scan_id": 0,
            "scanned": None,          # {"disc": n, "minlen": n}
            "busy": False,
            "task": "",
            "status": "Ready",
            "progress": None,         # 0..1, or None for "working, no %"
            "notices": [],
            "result": None,
            "update": None,           # {"build": n} when a newer build is on GitHub
            "review": None,           # a split waiting for the person to check episode lengths
            "move_offer": core.mac_outside_applications(),   # Mac: offer to move into Applications
            "track_rule": None,       # {"copies_all": bool|None, "can_set": bool, "changed": bool}
        }
        self._review_event = threading.Event()
        self._review_answer = None

    # ---- plumbing ----
    def _set(self, **kw):
        with self._lock:
            self._s.update(kw)

    def _logline(self, text):
        with self._lock:
            self._log.append(str(text))
            if len(self._log) > 2000:
                del self._log[:500]

    def _notice(self, kind, text):
        with self._lock:
            self._notice_id += 1
            self._s["notices"] = (self._s["notices"] + [{"id": self._notice_id, "kind": kind, "text": text}])[-20:]

    def _tools(self):
        # look again whenever a tool is missing, so one installed outside the app is picked up
        if not (self._makemkv and os.path.isfile(self._makemkv)):
            self._makemkv = core.find_makemkvcon()
        if not (self._mkvmerge and os.path.isfile(self._mkvmerge)):
            self._mkvmerge = core.find_mkvmerge()
        return {
            "makemkv": self._makemkv or "",
            "mkvmerge": self._mkvmerge or "",
            "winget": False if core.IS_MAC else bool(core.find_winget()),
            "installer": {"MakeMKV": core.installer_for("MakeMKV"), "MKVToolNix": core.installer_for("MKVToolNix")},
        }

    def _run(self, task, fn):
        """Run fn in the background as the one current task."""
        with self._lock:
            if self._s["busy"]:
                return False
            self._s.update(busy=True, task=task, progress=None)

        def go():
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - shown to the user
                self._logline(traceback.format_exc())
                self._notice("error", str(e))
                self._set(status="Stopped with an error")
            finally:
                self._set(busy=False, task="", progress=None)
        threading.Thread(target=go, daemon=True).start()
        return True

    def _robot(self, kind, f):
        if kind == "MSG" and len(f) >= 4:
            self._logline(f[3])
        elif kind == "PRGV" and len(f) >= 3:
            try:
                total, mx = int(f[1]), int(f[2])
                self._set(progress=(total / mx) if mx else None)
            except ValueError:
                pass
        elif kind in ("PRGT", "PRGC") and len(f) >= 3:
            self._set(status=f[2])

    # ---- called by the page ----
    def _track_rule(self):
        try:
            rule = core.makemkv_rule()
        except Exception:  # noqa: BLE001
            rule = None
        return {"copies_all": None if rule is None else core.rule_copies_all(rule),
                "can_set": sys.platform == "win32",
                "changed": "makemkv_rule_before" in self._settings}

    def set_makemkv_copy_all(self, on=True):
        """Windows: make MakeMKV rip every track (so any can be chosen), or put its old rule back."""
        if sys.platform != "win32":
            return {"error": "Change it in MakeMKV: Preferences > Advanced (turn on Expert mode first)."}
        try:
            if on:
                old = core.set_makemkv_rule(core.COPY_ALL_RULE)
                if "makemkv_rule_before" not in self._settings:
                    self.save_settings({"makemkv_rule_before": old})
            else:
                core.set_makemkv_rule(self._settings.get("makemkv_rule_before", ""))
                self._settings.pop("makemkv_rule_before", None)
                core.save_settings(self._settings)
        except OSError as e:
            return {"error": f"Couldn't change MakeMKV's setting: {e}"}
        self._set(track_rule=self._track_rule())
        return {"ok": True, "track_rule": self._s["track_rule"]}

    def get_state(self, log_from=0):
        with self._lock:
            st = dict(self._s)
            st["log"] = self._log[log_from:] if log_from <= len(self._log) else self._log[:]
            st["log_end"] = len(self._log)
        st["tools"] = self._tools()
        st["settings"] = self._settings
        return st

    def startup(self):
        core.cleanup_old_exe()
        if core.is_frozen() and self._settings.get("icons_build") != core.APP_BUILD:
            # first run of a new build: get Windows to pick up the exe's current icon
            threading.Thread(target=core.refresh_icons, daemon=True).start()
            self.save_settings({"icons_build": core.APP_BUILD})
        if self._tools()["makemkv"]:
            self.refresh_drives()
        if core.is_frozen() and not self._s["move_offer"]:   # can't update until it's in Applications
            threading.Thread(target=self._check_update, daemon=True).start()
        return self.get_state()

    def save_settings(self, data):
        self._settings.update(data or {})
        core.save_settings(self._settings)
        return True

    def refresh_drives(self):
        exe = self._tools()["makemkv"]
        if not exe:
            self._notice("error", "MakeMKV isn't installed. Install it from Settings.")
            return False

        def work():
            self._set(status="Looking for drives...")
            drives = core.list_drives(exe)
            self._set(drives=[{"index": i, "name": n, "disc": d, "path": p} for i, n, d, p in drives],
                      status="Ready" if drives else "No disc drives found")
        return self._run("drives", work)

    def scan(self, disc, minlen):
        exe = self._tools()["makemkv"]
        if not exe:
            self._notice("error", "MakeMKV isn't installed. Install it from Settings.")
            return False
        disc, minlen = int(disc), int(minlen)

        def work():
            self._set(status="Reading the disc (this can take a minute)...", result=None)
            self._logline(f"--- Scanning disc:{disc}")
            titles = core.scan_titles(exe, disc, minlen, self._robot)
            for t in titles:
                t["label"] = core.tlabel(t)
                t["secs"] = core.duration_seconds(t.get("duration", ""))
                try:
                    t["chapters"] = int(t.get("chapters", 0))
                except ValueError:
                    t["chapters"] = 0
            play_all = {str(k): v for k, v in core.find_play_all(titles).items()}
            drive = next((d for d in self._s["drives"] if d["index"] == disc), {})
            root = core.dvd_root(drive.get("path", ""), drive.get("disc", ""))
            got = 0
            for t in titles:
                if root and t.get("dvd"):
                    ct = core.dvd_chapter_times(root, t["dvd"])
                    if ct and len(ct[0]) == t["chapters"]:
                        t["chapter_times"], t["chapter_total"] = ct
                        got += 1
            self._logline(f"Chapter times read from the disc for {got} title(s)" if got
                          else "Chapter times will be read after ripping (couldn't read them from the disc)")
            self._s["track_rule"] = self._track_rule()
            with self._lock:
                self._s.update(titles=titles, play_all=play_all, scanned={"disc": disc, "minlen": minlen},
                               scan_id=self._s["scan_id"] + 1,
                               status=f"Found {len(titles)} title{'s' if len(titles) != 1 else ''}"
                               if titles else "No titles found. Try a lower minimum length in Settings.")
        return self._run("scan", work)

    def preview(self, req):
        try:
            plan = self._plan(req)
        except ValueError as e:
            return {"error": str(e), "names": {}}
        return {"error": "", "names": {str(tid): names for tid, names, _, _ in plan}}

    def _plan(self, req):
        """[(title id, [file names relative to the output folder], split chapters, fallback name)]"""
        rows = req.get("titles", [])
        if req.get("mode") == "movie":
            movie = (req.get("movie") or "").strip()
            if not movie:
                raise ValueError("Type the movie name, e.g. The Matrix (1999)")
            bad = core.check_prefix(movie)
            if bad:
                raise ValueError(f"The movie name can't contain {bad}")
            names = core.build_movie_names(movie, len(rows), folder=req.get("movie_folder", True))
            return [(r["id"], [n], [], None) for r, n in zip(rows, names)]
        prefix = req.get("prefix") or ""
        bad = core.check_prefix(prefix)
        if bad:
            raise ValueError(f"The name prefix can't contain {bad}")
        try:
            ep = int(req.get("start"))
        except (TypeError, ValueError):
            raise ValueError("First episode must be a number")
        plan = []
        for r in rows:
            how, eps, pts = r.get("how", "single"), int(r.get("eps") or 1), list(r.get("pts") or [])
            t = next((x for x in self._s["titles"] if x["id"] == r["id"]), {})
            if how == "split" and (pts or (r.get("auto") and eps > 1)):
                n = len(pts) + 1 if pts else eps
                known = t.get("chapter_times")
                if known and not pts:                # same automatic cuts the episodes dialog showed
                    pts = core.auto_split_points(known, t.get("chapter_total") or t.get("secs", 0), n) or []
                if pts and known:
                    spec = pts                       # lengths were shown before ripping
                else:                                # check the lengths after ripping, before splitting
                    spec = {"episodes": n, "pts": pts or None, "total": t.get("secs", 0), "review": True}
                plan.append((r["id"], core.build_names(prefix, ep, n), spec, core.build_multi_name(prefix, ep, ep + n - 1)))
                ep += n
            elif how == "whole" and eps > 1:
                plan.append((r["id"], [core.build_multi_name(prefix, ep, ep + eps - 1)], [], None))
                ep += eps
            else:
                plan.append((r["id"], core.build_names(prefix, ep, 1), [], None))
                ep += 1
        return plan

    def rip(self, req, force=False):
        exe = self._tools()["makemkv"]
        if not exe:
            return {"error": "MakeMKV isn't installed. Install it from Settings."}
        sc = self._s.get("scanned")
        if not sc or not self._s["titles"]:
            return {"error": "Scan a disc first."}
        if not req.get("titles"):
            return {"error": "Check at least one title to rip."}
        out = (req.get("out") or "").strip()
        if not out:
            return {"error": "Choose a folder to save to."}
        try:
            plan = self._plan(req)
        except ValueError as e:
            return {"error": str(e)}
        plan = [(tid, [os.path.join(out, n) for n in names], pts, fb and os.path.join(out, fb))
                for tid, names, pts, fb in plan]
        mkvmerge = self._tools()["mkvmerge"]
        if not force:
            existing = [os.path.basename(p) for _, paths, _, _ in plan for p in paths if os.path.exists(p)]
            if existing:
                return {"confirm": "overwrite", "files": existing}
            if any(pts for _, _, pts, _ in plan) and not mkvmerge:
                return {"confirm": "nosplit"}
        by_id = {t["id"]: t for t in self._s["titles"]}
        chosen_tracks = {r["id"]: r["tracks"] for r in req.get("titles", []) if isinstance(r.get("tracks"), list)}
        disc, minlen = sc["disc"], sc["minlen"]

        def work():
            for _, paths, _, _ in plan:
                for p in paths:
                    os.makedirs(os.path.dirname(p), exist_ok=True)
            done, notes = 0, []
            self._set(result=None)
            for n, (tid, paths, pts, fallback) in enumerate(plan, 1):
                lab = by_id[tid]["label"] if tid in by_id else str(tid)
                names = os.path.basename(paths[0]) + (f" … {os.path.basename(paths[-1])}" if len(paths) > 1 else "")
                self._set(status=f"Ripping title {lab} ({n} of {len(plan)})", progress=0)
                self._logline(f"--- Title {lab} -> {names}")
                if isinstance(pts, dict):
                    self._logline(f"Will split into {pts['episodes']} episodes; you'll check the lengths before it splits")
                elif pts:
                    self._logline(f"Will split before chapters {','.join(map(str, pts))}")
                tnotes = []
                tr = None
                if tid in chosen_tracks and by_id.get(tid, {}).get("tracks"):
                    tr = (by_id[tid]["tracks"], chosen_tracks[tid])
                result = core.rip_title(exe, disc, minlen, tid, paths, self._robot, mkvmerge=mkvmerge,
                                        tracks=tr, notes=tnotes,
                                        split_points=pts, fallback_path=fallback,
                                        on_review=lambda times, total, p, n, lab=lab, names=paths: self._ask_review(
                                            lab, times, total, p, n, [os.path.basename(x) for x in names]),
                                        on_split_progress=lambda pct: self._set(progress=pct / 100,
                                                                                status=f"Splitting title {lab}..."))
                for t in tnotes:
                    self._logline(f"Title {lab}: {t}")
                    notes.append(f"Title {lab}: {t}")
                if result == "whole":
                    self._logline(f"Title {lab} kept as one file: {os.path.basename(fallback)}")
                    done += 1
                elif result.startswith("fallback"):
                    msg = (f"Title {lab} couldn't be split ({result.split(': ', 1)[1]}), so it was saved as one "
                           f"file: {os.path.basename(fallback)}")
                    self._logline(msg)
                    notes.append(msg)
                    done += 1
                else:
                    done += len(paths)
            self._set(status=f"Done. Saved {done} file{'s' if done != 1 else ''}.", progress=1,
                      result={"done": done, "notes": notes, "out": out})
        if not self._run("rip", work):
            return {"error": "Something else is still running."}
        return {"ok": True}

    def _ask_review(self, label, times, total, pts, episodes, names):
        """Pause the rip until the person checks the episode lengths (answered by review_answer)."""
        self._review_event.clear()
        self._review_answer = None
        self._set(review={"label": label, "times": times, "total": total, "pts": pts or [],
                          "episodes": episodes, "names": names},
                  status=f"Check the episode lengths for title {label}", progress=None)
        self._review_event.wait()
        ans = self._review_answer
        self._set(review=None, status=f"Splitting title {label}...")
        if ans == "whole":
            return "whole"
        return ans

    def review_answer(self, answer):
        """From the page: {"pts": [7, 13]} to split there, or {"whole": true} to keep one file."""
        if answer.get("whole"):
            self._review_answer = "whole"
        else:
            self._review_answer = [int(x) for x in answer.get("pts", [])]
        self._review_event.set()
        return True

    def choose_folder(self, start=""):
        return self._dialog("folder", start)

    def browse_tool(self, kind):
        path = self._dialog("exe", os.path.dirname((self._makemkv if kind == "makemkv" else self._mkvmerge) or ""))
        if path:
            self.set_tool(kind, path)
        return path

    def set_tool(self, kind, path):
        path = core.tool_in_app((path or "").strip().rstrip("/"), kind)
        if kind == "makemkv":
            self._makemkv = path
        else:
            self._mkvmerge = path
        self.save_settings({kind: path})
        return self._tools()

    def _dialog(self, kind, start):
        import webview
        try:
            dlg = getattr(webview, "FileDialog", None)
            if kind == "folder":
                typ = dlg.FOLDER if dlg else webview.FOLDER_DIALOG
                res = self._window.create_file_dialog(typ, directory=start or "")
            else:
                typ = dlg.OPEN if dlg else webview.OPEN_DIALOG
                types = () if core.IS_MAC else ("Programs (*.exe)", "All files (*.*)")
                res = self._window.create_file_dialog(typ, directory=start or ("/Applications" if core.IS_MAC else ""),
                                                      file_types=types)
        except Exception as e:  # noqa: BLE001
            self._logline(f"File dialog failed: {e}")
            return ""
        if not res:
            return ""
        return str(res[0] if isinstance(res, (list, tuple)) else res)

    def install_tool(self, package):
        how = core.installer_for(package)
        if how == "page":
            import webbrowser
            webbrowser.open(core.TOOL_PAGES[package])
            self._notice("info", f"The {package} download page opened in your browser. Install it, then come back: "
                                 "MKV Ripper finds it by itself.")
            return True
        tools = self._tools()
        installed = bool(tools["makemkv"] if package == "MakeMKV" else tools["mkvmerge"])
        action = "upgrade" if installed else "install"

        def work():
            self._set(status=f"{'Updating' if installed else 'Installing'} {package} with {how} "
                             f"(this can take a few minutes)...")
            if how == "brew":
                self._logline(f"--- brew {action} --cask {core.BREW_CASKS[package]}")
                code, tail = core.brew_run(action, package, self._logline)
            else:
                self._logline(f"--- winget {action} {core.WINGET_IDS[package]}")
                code, tail = core.winget_run(action, package, self._logline)
            if package == "MakeMKV" and not self._tools()["makemkv"]:
                self._makemkv = core.find_makemkvcon()
            if package == "MKVToolNix" and not self._tools()["mkvmerge"]:
                self._mkvmerge = core.find_mkvmerge()
            now = self._tools()["makemkv" if package == "MakeMKV" else "mkvmerge"]
            last = tail[-1] if tail else ""
            if now and (code == 0 or installed):
                self._notice("info", f"{package}: {last or 'done'}")
            else:
                self._notice("error", f"{package} wasn't installed. {last or f'{how} exit code {code}'}")
            self._set(status="Ready")
            if package == "MakeMKV" and now and not self._s["drives"]:
                threading.Timer(0.5, self.refresh_drives).start()
        return self._run("install", work)

    def _check_update(self):
        try:
            build, url = core.latest_release()
        except Exception as e:  # noqa: BLE001
            return {"error": f"Couldn't reach GitHub: {e}"}
        newer = bool(url) and build > core.APP_BUILD
        self._set(update={"build": build} if newer else None)
        self._update_url = url if newer else None
        return {"latest": not newer, "build": build}

    def check_update(self):
        return self._check_update()

    def apply_update(self):
        url = getattr(self, "_update_url", None)
        if not url or not core.is_frozen():
            return {"error": "No update to install."}
        if core.mac_outside_applications():
            # macOS runs a downloaded app from a read-only copy until it's moved, so it can't replace itself
            return {"move": True}

        def work():
            self._set(status="Downloading the update...")
            try:
                core.install_update(url)
            except Exception as e:  # noqa: BLE001
                self._logline(f"Update failed: {e}")
                what = (f"download {core.update_asset().replace('.zip', '.dmg')} and drag MKV Ripper into Applications" if core.IS_MAC
                        else "run MKVRipper-setup.exe from there")
                self._notice("error", f"Couldn't install the update ({e}). The download page is opening in "
                                      f"your browser: {what}.")
                self.open_releases()
                self._set(status="Ready")
                return
            self._set(status="Restarting...")
            time.sleep(0.5)
            self._window.destroy()
        self._run("update", work)
        return {"ok": True}

    def move_to_applications(self):
        """Mac: copy into Applications, open that copy, and close this one."""
        try:
            new = core.move_to_applications()
        except Exception as e:  # noqa: BLE001
            return {"error": f"{e}. You can drag MKV Ripper into Applications in the Finder instead."}
        import subprocess
        subprocess.Popen(["/usr/bin/open", "-n", new])
        threading.Timer(0.8, lambda: self._window and self._window.destroy()).start()
        return {"ok": True, "path": new}

    def open_releases(self):
        import webbrowser
        webbrowser.open(core.RELEASES_PAGE)
        return True

    def open_folder(self, path):
        try:
            if core.IS_MAC:
                import subprocess
                subprocess.run(["/usr/bin/open", path], check=True)
            else:
                os.startfile(path)  # noqa: S606 - opens Explorer on the user's own folder
        except (AttributeError, OSError, Exception) as e:  # noqa: BLE001
            self._notice("error", f"Couldn't open the folder: {e}")


def _dark_titlebar(window):
    """Dark Windows title bar when Windows is in dark mode (the page itself follows the setting)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            dark = winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        if not dark:
            return
        hwnd = window.native.Handle.ToInt32()
        val = ctypes.c_int(1)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (current id, then pre-20H1 id)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


def main():
    try:
        import webview
    except ImportError:
        print("MKV Ripper needs pywebview: pip install pywebview")
        sys.exit(1)
    api = Api()
    geo = api._settings.get("window") or {}
    window = webview.create_window(
        "MKV Ripper", WEB_INDEX, js_api=api,
        width=int(geo.get("w", 1120)), height=int(geo.get("h", 860)), min_size=(760, 560),
        background_color="#f3f4f6", text_select=False)
    api._window = window

    def on_closing():
        try:
            api.save_settings({"window": {"w": window.width, "h": window.height}})
        except Exception:  # noqa: BLE001
            pass
    window.events.closing += on_closing
    window.events.shown += lambda: _dark_titlebar(window)

    smoke = os.environ.get("MKVRIPPER_SMOKE")   # CI check: the page loads and the JS bridge works
    if smoke:
        def check():
            time.sleep(2)
            result = window.evaluate_js("document.title + ' / api:' + typeof (window.pywebview && window.pywebview.api)")
            # the update check must work from the built app: HTTPS through the system's certificate check.
            # An HTTP error status still proves the secure connection worked (GitHub may rate-limit CI).
            result = f"{result} / https:{type(core._ssl_context()).__module__}"
            try:
                result = f"{result} / latest:{core.latest_release()[0]}"
            except core.urllib.error.HTTPError as e:
                result = f"{result} / http-status:{e.code}"
            except Exception as e:  # noqa: BLE001
                result = f"{result} / https-error:{e}"
            with open(smoke, "w", encoding="utf-8") as f:
                f.write(str(result))
            window.destroy()
        window.events.loaded += lambda: threading.Thread(target=check, daemon=True).start()
    try:
        ico = os.path.join(RES_DIR, "assets", "icon.ico")
        webview.start(icon=ico if os.path.isfile(ico) else None)
    except Exception as e:  # noqa: BLE001
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, "MKV Ripper couldn't open its window. It needs Microsoft Edge WebView2, which comes with "
                      "Windows 10 and 11. Install it from https://developer.microsoft.com/microsoft-edge/webview2/ "
                      f"and try again.\n\n{e}", "MKV Ripper", 0x10)
        raise


if __name__ == "__main__":
    main()
