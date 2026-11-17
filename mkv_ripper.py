r"""
MKV Ripper - a small front end for MakeMKV's command-line tool (makemkvcon).

1. Pick your drive and click "Scan disc".
2. Check the titles you want.
3. Type a prefix (e.g.  S1E  or  Belgravia (2020) - S01E ) and a starting episode number.
4. Pick an output folder and click "Rip checked titles".

Checked titles are ripped in list order and named  <prefix><number>.mkv,
counting up from your starting number: S1E1.mkv, S1E2.mkv, S1E3.mkv ...

Some TV discs store several episodes as ONE title. Select that title and click
"Split title..." (or double-click it), then enter the chapter numbers where episodes
2, 3, ... start (e.g. 6,11). After ripping, MKVToolNix splits it (no re-encoding)
and each piece gets the next episode number.

Movie mode: type the movie name and year instead, e.g.  The Matrix (1999).
The first checked title is saved as  The Matrix (1999)\The Matrix (1999).mkv
and any other checked titles go in the same folder as Plex extras (Extra 1-other.mkv ...).

Requires Windows. The released MKVRipper.exe needs no Python. The Tools menu installs
and updates MakeMKV and MKVToolNix through winget, and the app checks GitHub for
newer versions of itself.
"""

import re

import csv
import json
import urllib.request
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import sv_ttk              # Windows 11 look
except ImportError:
    sv_ttk = None
try:
    import darkdetect          # follow Windows light/dark setting
except ImportError:
    darkdetect = None

DEFAULT_MAKEMKVCON = [
    r"C:\Program Files (x86)\MakeMKV\makemkvcon64.exe",
    r"C:\Program Files (x86)\MakeMKV\makemkvcon.exe",
    r"C:\Program Files\MakeMKV\makemkvcon64.exe",
    r"C:\Program Files\MakeMKV\makemkvcon.exe",
]

DEFAULT_MKVMERGE = [
    r"C:\Program Files\MKVToolNix\mkvmerge.exe",
    r"C:\Program Files (x86)\MKVToolNix\mkvmerge.exe",
]

# TINFO attribute ids (AP_ItemAttributeId in MakeMKV's apdefs.h)
ATTR_NAME = 2
ATTR_CHAPTERS = 8
ATTR_DURATION = 9
ATTR_SIZE = 10
ATTR_SOURCE = 16
ATTR_SOURCE_TITLE = 24   # the disc's own title number on DVDs (what VLC's Title menu shows)
ATTR_OUTPUT_NAME = 27

INVALID_FILENAME_CHARS = '<>:"/\\|?*'


# ----------------------------------------------------------------------------
# makemkvcon helpers (no GUI code here, so they can be tested on their own)
# ----------------------------------------------------------------------------

def find_makemkvcon():
    for p in DEFAULT_MAKEMKVCON:
        if os.path.isfile(p):
            return p
    found = shutil.which("makemkvcon64") or shutil.which("makemkvcon")
    return found or ""


def find_mkvmerge():
    for p in DEFAULT_MKVMERGE:
        if os.path.isfile(p):
            return p
    return shutil.which("mkvmerge") or ""


# ----------------------------------------------------------------------------
# Dependencies (winget) and app self-update (GitHub Releases)
# ----------------------------------------------------------------------------

def _read_build():
    """Build number stamped in by GitHub Actions (0 when running the .py from source)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(base, "APP_BUILD")) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


APP_BUILD = _read_build()
GITHUB_REPO = "vagend/makeMKV-GUI"
EXE_ASSET = "MKVRipper.exe"
WINGET_IDS = {"MakeMKV": "GuinpinSoft.MakeMKV", "MKVToolNix": "MoritzBunkus.MKVToolNix"}


def find_winget():
    w = shutil.which("winget")
    if w:
        return w
    p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "winget.exe")
    return p if os.path.isfile(p) else ""


def winget_run(action, package, on_line=None):
    """action is 'install' or 'upgrade'. Returns (exit code, last lines of output)."""
    winget = find_winget()
    if not winget:
        raise RuntimeError("winget (Windows Package Manager) isn't available. Install or update "
                           "'App Installer' from the Microsoft Store, then try again.")
    args = [winget, action, "--id", WINGET_IDS[package], "-e", "--silent",
            "--accept-package-agreements", "--accept-source-agreements"]
    proc = _popen(args)
    tail = []
    for line in proc.stdout:
        line = line.strip().strip("\x08").strip()
        if not line or set(line) <= set("-\\|/ ") or any(c in line for c in "█▒░"):  # spinner/progress noise
            continue
        tail = (tail + [line])[-6:]
        if on_line:
            on_line(line)
    proc.wait()
    return proc.returncode, tail


def parse_build(title):
    m = re.search(r"build\s+(\d+)", title or "", re.I)
    return int(m.group(1)) if m else 0


def latest_release():
    """Return (build number, download URL of MKVRipper.exe or None) from GitHub's latest release."""
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "MKVRipper"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    url = next((a["browser_download_url"] for a in data.get("assets", [])
                if a.get("name", "").lower() == EXE_ASSET.lower()), None)
    return parse_build(data.get("name", "")), url


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def cleanup_old_exe():
    if is_frozen():
        try:
            os.remove(sys.executable + ".old")
        except OSError:
            pass


def install_update(url):
    """Download the new exe, swap it in for the running one, and start it."""
    exe = sys.executable
    new, old = exe + ".new", exe + ".old"
    req = urllib.request.Request(url, headers={"User-Agent": "MKVRipper"})
    with urllib.request.urlopen(req, timeout=120) as r, open(new, "wb") as f:
        shutil.copyfileobj(r, f)
    if os.path.getsize(new) < 1_000_000:
        os.remove(new)
        raise RuntimeError("The downloaded update looks incomplete; nothing was changed.")
    if os.path.exists(old):
        os.remove(old)
    os.replace(exe, old)        # Windows allows renaming a running exe
    os.replace(new, exe)
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen([exe], env=env, close_fds=True)


def parse_split_points(text, chapters=None):
    """'6, 11' -> [6, 11]. Each number is a chapter where a NEW episode starts."""
    text = text.strip()
    if not text:
        return []
    points = []
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(f"'{part}' isn't a chapter number.")
        points.append(int(part))
    points = sorted(set(points))
    if points[0] < 2:
        raise ValueError("Chapter 1 is already the start of the first episode; list where the NEXT episodes start (2 or higher).")
    if chapters and points[-1] > chapters:
        raise ValueError(f"This title only has {chapters} chapters.")
    return points


def guess_split_points(chapters, episodes):
    """Even split by chapter count, or None if chapters don't divide evenly."""
    if episodes < 2 or chapters < episodes or chapters % episodes:
        return None
    step = chapters // episodes
    return [1 + step * k for k in range(1, episodes)]


def split_file(mkvmerge, src, points, work_dir, on_progress=None):
    """Split src before the given chapters (lossless). Returns the part files in order."""
    os.makedirs(work_dir, exist_ok=True)
    out = os.path.join(work_dir, "part.mkv")
    args = [mkvmerge, "-o", out, "--split", "chapters:" + ",".join(map(str, points)), src]
    proc = _popen(args)
    tail = []
    for line in proc.stdout:
        line = line.strip()
        m = re.search(r"(\d+)%", line)
        if m and on_progress:
            on_progress(int(m.group(1)))
        elif line:
            tail = (tail + [line])[-5:]
    proc.wait()
    if proc.returncode >= 2:  # mkvmerge: 0 ok, 1 warnings, 2 error
        raise RuntimeError("mkvmerge failed:\n" + "\n".join(tail))
    return sorted(os.path.join(work_dir, f) for f in os.listdir(work_dir)
                  if f.startswith("part-") and f.lower().endswith(".mkv"))


def parse_robot_line(line):
    """Split one robot-mode line into (kind, [fields]). Returns (None, []) if unparseable."""
    line = line.rstrip("\r\n")
    if ":" not in line:
        return None, []
    kind, rest = line.split(":", 1)
    try:
        fields = next(csv.reader([rest], delimiter=",", quotechar='"',
                                 escapechar="\\", doublequote=False))
    except (csv.Error, StopIteration):
        return None, []
    return kind, fields


def _popen(args):
    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                  text=True, encoding="utf-8", errors="replace", bufsize=1)
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: no console flashing
    return subprocess.Popen(args, **kwargs)


def run_robot(args, on_line=None):
    """Run makemkvcon, yield parsed lines to on_line, return (returncode, all parsed lines)."""
    proc = _popen(args)
    lines = []
    for raw in proc.stdout:
        kind, fields = parse_robot_line(raw)
        if kind is None:
            continue
        lines.append((kind, fields))
        if on_line:
            on_line(kind, fields)
    proc.wait()
    return proc.returncode, lines


def list_drives(exe):
    """Return [(index, drive_name, disc_name)] for drives that exist."""
    _, lines = run_robot([exe, "-r", "--cache=1", "info", "disc:9999"])
    drives = []
    for kind, f in lines:
        # DRV:index,visible,enabled,flags,drive name,disc name
        if kind == "DRV" and len(f) >= 6 and f[4].strip():
            drives.append((int(f[0]), f[4], f[5]))
    return drives


def scan_titles(exe, disc_index, minlength, on_line=None):
    """Return list of title dicts. 'id' is the index makemkvcon's mkv command expects."""
    args = [exe, "-r", f"--minlength={minlength}", "info", f"disc:{disc_index}"]
    _, lines = run_robot(args, on_line)
    titles = {}
    for kind, f in lines:
        # TINFO:title_index,attribute_id,code,value
        if kind == "TINFO" and len(f) >= 4:
            try:
                tid, attr = int(f[0]), int(f[1])
            except ValueError:
                continue
            t = titles.setdefault(tid, {"id": tid})
            value = f[3]
            if attr == ATTR_NAME:
                t["name"] = value
            elif attr == ATTR_CHAPTERS:
                t["chapters"] = value
            elif attr == ATTR_DURATION:
                t["duration"] = value
            elif attr == ATTR_SIZE:
                t["size"] = value
            elif attr == ATTR_SOURCE_TITLE and value.strip().isdigit():
                t["dvd"] = int(value)
            elif attr == ATTR_SOURCE:
                t["source"] = value
            elif attr == ATTR_OUTPUT_NAME:
                t["output"] = value
    return [titles[k] for k in sorted(titles)]


def build_names(prefix, start, count, ext=".mkv"):
    return [f"{prefix}{start + i}{ext}" for i in range(count)]


def build_multi_name(prefix, first, last, ext=".mkv"):
    """One file holding several episodes, in Plex's multi-episode form: S1E1-E3, S01E01-E03."""
    m = re.search(r"[Ee](0*)$", prefix)
    pad = m.group(1) if m else ""
    return f"{prefix}{first}-E{pad}{last}{ext}"


def build_movie_names(movie, count, ext=".mkv"):
    """First checked title is the movie; any others become Plex 'other' extras.
    Paths are relative to the output folder: '<movie>/<movie>.mkv', '<movie>/Extra 1-other.mkv', ..."""
    names = [os.path.join(movie, f"{movie}{ext}")] if count else []
    names += [os.path.join(movie, f"Extra {i}-other{ext}") for i in range(1, count)]
    return names


def find_play_all(titles):
    """Find a 'Play all' title: a long title whose length matches a run of shorter titles
    back to back (e.g. title 1 = titles 3+4+5). Returns {play_all_id: [episode ids]}."""
    secs = {t["id"]: duration_seconds(t.get("duration", "")) for t in titles}
    ordered = sorted(titles, key=lambda t: t.get("dvd", t["id"]))
    found = {}
    for big in titles:
        total = secs[big["id"]]
        if total < 50 * 60:
            continue
        cands = [t for t in ordered if t["id"] != big["id"] and 10 * 60 <= secs[t["id"]] <= total * 0.75]
        best = None
        for i in range(len(cands)):
            run = 0
            for j in range(i, len(cands)):
                run += secs[cands[j]["id"]]
                if run > total + 120:
                    break
                if j > i and abs(run - total) <= max(90, total * 0.02):
                    if best is None or abs(run - total) < best[0]:
                        best = (abs(run - total), [c["id"] for c in cands[i:j + 1]])
        if best:
            found[big["id"]] = best[1]
    return found


def check_prefix(prefix):
    bad = [c for c in prefix if c in INVALID_FILENAME_CHARS]
    return "".join(sorted(set(bad)))


def rip_title(exe, disc_index, minlength, title_id, final_paths, on_line=None,
              mkvmerge=None, split_points=None, on_split_progress=None, fallback_path=None):
    """Rip one title into a temp folder, optionally split it at chapters, then move the
    result(s) to final_paths (one path, or one per episode when splitting).
    If splitting fails and fallback_path is given, the whole rip is saved there instead
    (a multi-episode file like S1E1-E3.mkv). Returns 'ok' or 'fallback: <reason>'."""
    if isinstance(final_paths, str):
        final_paths = [final_paths]
    out_dir = os.path.dirname(final_paths[0])
    tmp = tempfile.mkdtemp(prefix=".mkvrip_", dir=out_dir)
    try:
        args = [exe, "-r", "--progress=-same", f"--minlength={minlength}",
                "mkv", f"disc:{disc_index}", str(title_id), tmp]
        code, _ = run_robot(args, on_line)
        mkvs = [f for f in os.listdir(tmp) if f.lower().endswith(".mkv")]
        if not mkvs:
            raise RuntimeError(f"MakeMKV produced no file for title {title_id} (exit code {code}).")
        ripped = os.path.join(tmp, max(mkvs, key=lambda f: os.path.getsize(os.path.join(tmp, f))))

        if not split_points:
            os.replace(ripped, final_paths[0])
            return "ok"

        try:
            if not mkvmerge or not os.path.isfile(mkvmerge):
                raise RuntimeError("MKVToolNix (mkvmerge) isn't installed")
            parts = split_file(mkvmerge, ripped, split_points, os.path.join(tmp, "parts"), on_split_progress)
            if len(parts) != len(final_paths):
                raise RuntimeError(f"expected {len(final_paths)} episodes but the split made {len(parts)}")
        except Exception as e:
            if not fallback_path:
                raise
            os.replace(ripped, fallback_path)
            return f"fallback: {e}"
        if len(parts) != len(final_paths):
            # Don't lose the rip: keep the unsplit file so it can be split by hand.
            base, ext = os.path.splitext(final_paths[0])
            keep = f"{base} (unsplit){ext}"
            os.replace(ripped, keep)
            raise RuntimeError(f"Expected {len(final_paths)} episodes from title {title_id} but the split "
                               f"made {len(parts)}. The unsplit rip was kept as:\n{keep}")
        for part, dest in zip(parts, final_paths):
            os.replace(part, dest)
        return "ok"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------

CHECK_ON, CHECK_OFF = "\u2611", "\u2610"

SETTINGS_FILE = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "MKVRipper", "settings.json")


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def system_theme():
    try:
        return "dark" if darkdetect and darkdetect.isDark() else "light"
    except Exception:
        return "light"


def set_dark_titlebar(win, dark):
    """Match the Windows title bar to the theme (Windows 10 20H1+ / 11); harmless elsewhere."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (new id, then pre-20H1 id)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def tlabel(t):
    """Title number to show: the disc's own number (matches VLC) when MakeMKV reports it."""
    return str(t.get("dvd", t["id"]))


def duration_seconds(text):
    try:
        parts = [int(p) for p in text.split(":")]
    except ValueError:
        return 0
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return secs


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.geometry(self.settings.get("geometry", "1040x760"))
        self.minsize(860, 620)

        self.exe_var = tk.StringVar(value=find_makemkvcon())
        self.mkvmerge_var = tk.StringVar(value=find_mkvmerge())
        self.drive_var = tk.StringVar()
        self.minlen_var = tk.StringVar(value=str(self.settings.get("minlen", 120)))
        self.mode_var = tk.StringVar(value=self.settings.get("mode", "tv"))
        self.prefix_var = tk.StringVar(value=self.settings.get("prefix", "S1E"))
        self.movie_var = tk.StringVar(value="")
        self.start_var = tk.StringVar(value="1")
        self.out_var = tk.StringVar(value=self.settings.get("out", ""))
        self.theme_var = tk.StringVar(value=os.environ.get("MKVRIPPER_THEME") or self.settings.get("theme", "System"))
        self.status_var = tk.StringVar(value="Ready.")
        self.showlog_var = tk.BooleanVar(value=False)

        self.drives = []          # [(index, drive_name, disc_name)]
        self.titles = []          # title dicts
        self.checked = set()      # title ids
        self.splits = {}          # title id -> [chapters where a new episode starts]
        self.whole = {}           # title id -> episode count kept in ONE file (S1E1-E3)
        self.scanned_minlen = 120
        self.scanned_disc = 0
        self.busy = False
        self.msgs = queue.Queue()

        cleanup_old_exe()
        self.title(f"MKV Ripper (build {APP_BUILD})" if APP_BUILD else "MKV Ripper (from source)")
        self.apply_theme()
        self._build()
        for v in (self.prefix_var, self.start_var, self.movie_var, self.mode_var):
            v.trace_add("write", lambda *_: self._refresh_names())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._pump)
        if os.environ.get("MKVRIPPER_SCREENSHOT"):
            self.after(1500, self._selftest)
            return
        if self.exe_var.get():
            self.refresh_drives()
        self.after(800, self._startup_checks)

    def _selftest(self):
        """CI only: fill in sample titles, screenshot the main window and the split dialog, then quit."""
        from PIL import ImageGrab
        out = os.environ["MKVRIPPER_SCREENSHOT"]
        self.drives = [(0, "HL-DT-ST BD-RE WH16NS40", "BELGRAVIA_D1")]
        self._set_drives(self.drives)
        self._set_titles([
            {"id": 0, "dvd": 1, "duration": "2:15:01", "chapters": "15", "size": "4.1 GB"},
            {"id": 1, "dvd": 2, "duration": "0:02:31", "chapters": "1", "size": "64.0 MB"},
            {"id": 2, "dvd": 3, "duration": "0:45:10", "chapters": "5", "size": "1.4 GB"},
            {"id": 3, "dvd": 4, "duration": "0:44:58", "chapters": "5", "size": "1.4 GB"},
            {"id": 4, "dvd": 5, "duration": "0:44:53", "chapters": "5", "size": "1.3 GB"},
            {"id": 5, "dvd": 7, "duration": "0:12:04", "chapters": "3", "size": "310.2 MB"},
        ])
        self.prefix_var.set("Belgravia (2020) - S01E0")
        self.out_var.set(r"D:\Plex\TV Shows\Belgravia (2020)\Season 01")
        self.status_var.set("Found 3 title(s). Check the ones to rip.")
        self._refresh_names()

        def grab(name, win):
            win.update()
            x, y = win.winfo_rootx(), win.winfo_rooty()
            ImageGrab.grab((x - 8, y - 32, x + win.winfo_width() + 8, y + win.winfo_height() + 8)).save(out + name)

        def step2():
            grab("-main.png", self)
            self.edit_split()
            self.after(1200, step3)

        def step3():
            dlg = [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)][-1]
            grab("-split.png", dlg)
            self.destroy()
        self.after(1200, step2)

    # ---- look & feel ----
    def apply_theme(self):
        choice = self.theme_var.get()
        mode = system_theme() if choice == "System" else choice.lower()
        self.dark = mode == "dark"
        if sv_ttk:
            sv_ttk.set_theme(mode)
        st = ttk.Style(self)
        base = ("Segoe UI", 10)
        muted = "#a0a0a0" if self.dark else "#606060"
        self.accent = "#4cc2ff" if self.dark else "#005fb8"
        self.warn = "#fce100" if self.dark else "#9d5d00"
        st.configure("Title.TLabel", font=("Segoe UI Semibold", 20))
        st.configure("Section.TLabel", font=("Segoe UI Semibold", 12))
        st.configure("Muted.TLabel", foreground=muted, font=("Segoe UI", 9))
        st.configure("Hint.TLabel", foreground=self.warn, font=("Segoe UI", 10))
        st.configure("Treeview", rowheight=32, font=base)
        st.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))
        if hasattr(self, "tree"):
            self.tree.tag_configure("hint", foreground=self.warn)
            self._style_log()
        set_dark_titlebar(self, self.dark)

    def _style_log(self):
        bg = "#1c1c1c" if self.dark else "#fafafa"
        fg = "#d0d0d0" if self.dark else "#202020"
        self.log.configure(bg=bg, fg=fg, insertbackground=fg, relief="flat",
                           highlightthickness=0, font=("Cascadia Mono", 9))

    def on_close(self):
        self.settings.update(geometry=self.geometry(), minlen=self.minlen_var.get().strip() or "120",
                             mode=self.mode_var.get(), prefix=self.prefix_var.get(),
                             out=self.out_var.get(), theme=self.theme_var.get())
        save_settings(self.settings)
        self.destroy()

    def open_settings(self):
        win = tk.Toplevel(self)
        win.title("Settings")
        win.transient(self)
        win.resizable(False, False)
        set_dark_titlebar(win, self.dark)
        frm = ttk.Frame(win, padding=20)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        def tool_row(r, label, var, browse, pkg, note):
            ttk.Label(frm, text=label, style="Section.TLabel").grid(row=r, column=0, columnspan=4, sticky="w", pady=(0 if r == 0 else 14, 2))
            ttk.Entry(frm, textvariable=var, width=60).grid(row=r + 1, column=0, columnspan=2, sticky="ew")
            ttk.Button(frm, text="Browse...", command=browse).grid(row=r + 1, column=2, padx=(8, 0))
            ttk.Button(frm, text="Install / update", command=lambda: self.dep_action(pkg)).grid(row=r + 1, column=3, padx=(8, 0))
            ttk.Label(frm, text=note, style="Muted.TLabel").grid(row=r + 2, column=0, columnspan=4, sticky="w")

        tool_row(0, "MakeMKV", self.exe_var, self.browse_exe, "MakeMKV", "Reads the disc. Found automatically when installed normally.")
        tool_row(3, "MKVToolNix", self.mkvmerge_var, self.browse_mkvmerge, "MKVToolNix", "Only needed to split a title into episodes.")

        ttk.Label(frm, text="Scanning", style="Section.TLabel").grid(row=6, column=0, columnspan=4, sticky="w", pady=(14, 2))
        row = ttk.Frame(frm)
        row.grid(row=7, column=0, columnspan=4, sticky="w")
        ttk.Label(row, text="Hide titles shorter than").pack(side="left")
        ttk.Spinbox(row, from_=0, to=3600, increment=30, textvariable=self.minlen_var, width=6).pack(side="left", padx=6)
        ttk.Label(row, text="seconds (lower it if a short episode is missing)").pack(side="left")

        ttk.Label(frm, text="Appearance", style="Section.TLabel").grid(row=8, column=0, columnspan=4, sticky="w", pady=(14, 2))
        row = ttk.Frame(frm)
        row.grid(row=9, column=0, columnspan=4, sticky="w")
        for t in ("System", "Light", "Dark"):
            ttk.Radiobutton(row, text=t, value=t, variable=self.theme_var,
                            command=lambda: (self.apply_theme(), set_dark_titlebar(win, self.dark))).pack(side="left", padx=(0, 16))

        ttk.Separator(frm).grid(row=10, column=0, columnspan=4, sticky="ew", pady=16)
        bottom = ttk.Frame(frm)
        bottom.grid(row=11, column=0, columnspan=4, sticky="ew")
        ttk.Label(bottom, text=f"MKV Ripper build {APP_BUILD}" if APP_BUILD else "MKV Ripper (from source)",
                  style="Muted.TLabel").pack(side="left")
        ttk.Button(bottom, text="Done", style="Accent.TButton", command=win.destroy).pack(side="right")
        ttk.Button(bottom, text="Check for app update",
                   command=lambda: self.check_app_update(quiet=False)).pack(side="right", padx=8)
        win.grab_set()

    # ---- dependencies & updates ----
    def _startup_checks(self):
        missing = [name for name, var in (("MakeMKV", self.exe_var), ("MKVToolNix", self.mkvmerge_var))
                   if not var.get() or not os.path.isfile(var.get())]
        if missing and not self.busy and messagebox.askyesno(
                "MKV Ripper",
                "Not found on this PC: " + " and ".join(missing) + ".\n\n"
                "Install now with Windows Package Manager (winget)? Windows may ask for permission. "
                "MakeMKV has its own license and beta key; installing it means you accept its terms."
                + ("\n\n(MKVToolNix is only needed for splitting titles into episodes.)"
                   if missing == ["MKVToolNix"] else "")):
            self._run_winget(missing, then_check_update=True)
        elif is_frozen():
            self.check_app_update(quiet=True)

    def _redetect(self):
        if not os.path.isfile(self.exe_var.get()):
            self.exe_var.set(find_makemkvcon())
        if not os.path.isfile(self.mkvmerge_var.get()):
            self.mkvmerge_var.set(find_mkvmerge())

    def dep_action(self, package):
        installed = os.path.isfile((self.exe_var if package == "MakeMKV" else self.mkvmerge_var).get())
        what = "check for updates to" if installed else "install"
        if messagebox.askyesno("MKV Ripper", f"Use winget to {what} {package}? "
                                             "Windows may ask for permission."):
            self._run_winget([package], upgrade=installed)

    def _run_winget(self, packages, upgrade=False, then_check_update=False):
        if self.busy:
            return
        self._set_busy(True)

        def work():
            msgs = []
            try:
                for pkg in packages:
                    action = "upgrade" if upgrade else "install"
                    self.post("status", f"winget: {action} {pkg}... (this can take a few minutes)")
                    self.post("log", f"--- winget {action} {WINGET_IDS[pkg]}")
                    code, tail = winget_run(action, pkg, lambda l: self.post("log", l))
                    last = tail[-1] if tail else ""
                    msgs.append(f"{pkg}: {last or ('done' if code == 0 else f'exit code {code}')}")
                self.after(0, self._redetect)
                self.post("status", "Ready.")
                self.post("info", "\n".join(msgs))
                if then_check_update and is_frozen():
                    self.after(0, lambda: self.check_app_update(quiet=True))
                self.after(500, self.refresh_drives)
            except Exception as e:
                self.post("error", str(e))
                self.post("status", "Ready.")
            finally:
                self.post("done")
        threading.Thread(target=work, daemon=True).start()

    def check_app_update(self, quiet=True):
        def work():
            try:
                build, url = latest_release()
            except Exception as e:
                if not quiet:
                    self.post("error", f"Couldn't check GitHub for updates:\n{e}")
                return
            if build <= APP_BUILD or not url:
                if not quiet:
                    self.post("info", f"You have the latest build ({APP_BUILD}).")
                return
            self.after(0, lambda: self._offer_update(build, url))
        threading.Thread(target=work, daemon=True).start()

    def _offer_update(self, build, url):
        if not is_frozen():
            messagebox.showinfo("MKV Ripper", f"Build {build} is on GitHub. You're running the .py "
                                              "script; pull the repo or download MKVRipper.exe from Releases.")
            return
        if self.busy or not messagebox.askyesno(
                "MKV Ripper", f"MKV Ripper build {build} is available (you have build {APP_BUILD}). Update now? "
                              "The app will restart."):
            return
        self._set_busy(True)
        self.status_var.set(f"Downloading MKV Ripper build {build}...")

        def work():
            try:
                install_update(url)
                self.after(0, self.destroy)
            except Exception as e:
                self.post("error", f"Update failed:\n{e}")
                self.post("status", "Ready.")
                self.post("done")
        threading.Thread(target=work, daemon=True).start()

    # ---- layout ----
    def _build(self):
        root = ttk.Frame(self, padding=(20, 16, 20, 16))
        root.pack(fill="both", expand=True)

        # header
        head = ttk.Frame(root)
        head.pack(fill="x", pady=(0, 12))
        ttk.Label(head, text="MKV Ripper", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="  Rip discs and name them for Plex", style="Muted.TLabel").pack(side="left", pady=(10, 0))
        ttk.Button(head, text="\u2699  Settings", command=self.open_settings).pack(side="right")

        # 1  disc
        c1 = ttk.Frame(root, style="Card.TFrame", padding=14)
        c1.pack(fill="x", pady=(0, 10))
        ttk.Label(c1, text="1   Disc", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        r = ttk.Frame(c1)
        r.pack(fill="x")
        self.drive_box = ttk.Combobox(r, textvariable=self.drive_var, state="readonly")
        self.drive_box.pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="\u27f3", width=3, command=self.refresh_drives).pack(side="left", padx=8)
        self.scan_btn = ttk.Button(r, text="Scan disc", style="Accent.TButton", width=14, command=self.scan)
        self.scan_btn.pack(side="left")

        # 2  titles
        c2 = ttk.Frame(root, style="Card.TFrame", padding=14)
        c2.pack(fill="both", expand=True, pady=(0, 10))
        top2 = ttk.Frame(c2)
        top2.pack(fill="x", pady=(0, 8))
        ttk.Label(top2, text="2   Pick titles", style="Section.TLabel").pack(side="left")
        ttk.Button(top2, text="Select none", command=lambda: self.set_all(False)).pack(side="right")
        ttk.Button(top2, text="Select all", command=lambda: self.set_all(True)).pack(side="right", padx=8)
        self.split_btn = ttk.Button(top2, text="\u2702  Split into episodes...", command=self.edit_split)
        self.split_btn.pack(side="right", padx=(0, 8))

        tf = ttk.Frame(c2)
        tf.pack(fill="both", expand=True)
        cols = ("chk", "id", "duration", "chapters", "size", "episodes", "newname")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse", height=7)
        heads = {"chk": "Rip", "id": "Disc title", "duration": "Length", "chapters": "Chapters", "size": "Size",
                 "episodes": "Episodes", "newname": "Saved as"}
        widths = {"chk": 50, "id": 85, "duration": 90, "chapters": 80, "size": 90, "episodes": 150, "newname": 380}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], minwidth=40, stretch=(c == "newname"),
                             anchor="w" if c in ("newname", "episodes") else "center")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.on_click)
        self.tree.bind("<space>", self.on_space)
        self.tree.bind("<Double-1>", self.on_double)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_split_btn())
        self.tree.tag_configure("hint", foreground=self.warn)

        self.empty_lbl = ttk.Label(c2, style="Muted.TLabel",
                                   text="Insert a disc and click Scan disc. Click a row's box to rip it; "
                                        "click its Episodes cell to split it into episodes.")
        self.empty_lbl.pack(anchor="w", pady=(8, 0))
        self.info_lbl = ttk.Label(c2, style="Muted.TLabel", wraplength=900, justify="left")
        self.hint_row = ttk.Frame(c2)
        self.hint_lbl = ttk.Label(self.hint_row, style="Hint.TLabel")
        self.hint_lbl.pack(side="left")
        self.hint_btn = ttk.Button(self.hint_row, text="Split it...", command=self._split_hinted)
        self.hint_btn.pack(side="left", padx=10)

        # 3  naming
        c3 = ttk.Frame(root, style="Card.TFrame", padding=14)
        c3.pack(fill="x", pady=(0, 10))
        top3 = ttk.Frame(c3)
        top3.pack(fill="x", pady=(0, 8))
        ttk.Label(top3, text="3   Name and save", style="Section.TLabel").pack(side="left")
        ttk.Radiobutton(top3, text="Movie", value="movie", variable=self.mode_var).pack(side="right")
        ttk.Radiobutton(top3, text="TV show", value="tv", variable=self.mode_var).pack(side="right", padx=16)

        self.name_area = ttk.Frame(c3)
        self.name_area.pack(fill="x")
        self.tv_row = ttk.Frame(self.name_area)
        ttk.Label(self.tv_row, text="Name prefix").grid(row=0, column=0, sticky="w")
        ttk.Label(self.tv_row, text="First episode").grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Entry(self.tv_row, textvariable=self.prefix_var).grid(row=1, column=0, sticky="ew")
        ttk.Spinbox(self.tv_row, from_=0, to=9999, textvariable=self.start_var, width=6).grid(row=1, column=1, sticky="w", padx=(12, 0))
        self.tv_row.columnconfigure(0, weight=1)
        self.movie_row = ttk.Frame(self.name_area)
        ttk.Label(self.movie_row, text="Movie name (Year)").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.movie_row, textvariable=self.movie_var).grid(row=1, column=0, sticky="ew")
        self.movie_row.columnconfigure(0, weight=1)
        self.example_lbl = ttk.Label(c3, style="Muted.TLabel")
        self.example_lbl.pack(anchor="w", pady=(4, 8))

        ttk.Label(c3, text="Save to folder").pack(anchor="w")
        r = ttk.Frame(c3)
        r.pack(fill="x")
        ttk.Entry(r, textvariable=self.out_var).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Browse...", command=self.browse_out).pack(side="left", padx=(8, 0))

        # footer
        foot = ttk.Frame(root)
        foot.pack(fill="x")
        self.rip_btn = ttk.Button(foot, text="Rip checked titles", style="Accent.TButton", width=22, command=self.rip)
        self.rip_btn.pack(side="left", ipady=4)
        right = ttk.Frame(foot)
        right.pack(side="left", fill="x", expand=True, padx=(16, 0))
        self.pbar = ttk.Progressbar(right, maximum=65536)
        self.pbar.pack(fill="x", pady=(2, 4))
        sr = ttk.Frame(right)
        sr.pack(fill="x")
        ttk.Label(sr, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")
        ttk.Checkbutton(sr, text="Show details", variable=self.showlog_var, style="Switch.TCheckbutton",
                        command=self._toggle_log).pack(side="right")

        self.log = tk.Text(root, height=8, state="disabled", wrap="word")
        self._style_log()
        self._refresh_names()
        self._update_split_btn()

    def _toggle_log(self):
        if self.showlog_var.get():
            self.log.pack(fill="x", pady=(10, 0))
        else:
            self.log.pack_forget()

    def _update_split_btn(self):
        ok = bool(self.tree.focus()) and self.mode_var.get() == "tv" and not self.busy
        self.split_btn.configure(state="normal" if ok else "disabled")

    # ---- helpers ----
    def log_line(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def post(self, kind, *data):
        """Thread-safe: queue something for the GUI thread."""
        self.msgs.put((kind, data))

    def _pump(self):
        try:
            while True:
                kind, data = self.msgs.get_nowait()
                if kind == "log":
                    self.log_line(data[0])
                elif kind == "status":
                    self.status_var.set(data[0])
                elif kind == "progress":
                    self.pbar["value"] = data[0]
                elif kind == "drives":
                    self._set_drives(data[0])
                elif kind == "titles":
                    self._set_titles(data[0])
                elif kind == "done":
                    self._set_busy(False)
                elif kind == "error":
                    messagebox.showerror("MKV Ripper", data[0])
                elif kind == "info":
                    messagebox.showinfo("MKV Ripper", data[0])
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _robot_handler(self, kind, f):
        if kind == "MSG" and len(f) >= 4:
            self.post("log", f[3])
        elif kind == "PRGV" and len(f) >= 3:
            try:
                cur, total, mx = int(f[0]), int(f[1]), int(f[2])
                self.post("progress", total * 65536 // mx if mx else 0)
            except ValueError:
                pass
        elif kind in ("PRGT", "PRGC") and len(f) >= 3:
            self.post("status", f[2])

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.scan_btn.configure(state=state)
        self.rip_btn.configure(state=state)
        self._update_split_btn()

    def _exe(self):
        exe = self.exe_var.get().strip()
        if not exe or not os.path.isfile(exe):
            messagebox.showerror("MKV Ripper", "Can't find makemkvcon. Use Browse... to point at "
                                 "makemkvcon64.exe in your MakeMKV folder.")
            return None
        return exe

    def _disc_index(self):
        i = self.drive_box.current()
        if i < 0 or i >= len(self.drives):
            return None
        return self.drives[i][0]

    # ---- actions ----
    def browse_exe(self):
        p = filedialog.askopenfilename(title="Find makemkvcon64.exe",
                                       filetypes=[("makemkvcon", "makemkvcon*.exe"), ("All files", "*.*")])
        if p:
            self.exe_var.set(p)
            self.refresh_drives()

    def browse_out(self):
        p = filedialog.askdirectory(title="Choose output folder")
        if p:
            self.out_var.set(os.path.normpath(p))

    def refresh_drives(self):
        exe = self._exe()
        if not exe or self.busy:
            return
        self._set_busy(True)
        self.status_var.set("Looking for drives...")

        def work():
            try:
                self.post("drives", list_drives(exe))
                self.post("status", "Ready.")
            except Exception as e:
                self.post("error", f"Couldn't list drives:\n{e}")
            finally:
                self.post("done")
        threading.Thread(target=work, daemon=True).start()

    def _set_drives(self, drives):
        self.drives = drives
        labels = [f"disc:{i}  {name}  —  {disc or '(no disc)'}" for i, name, disc in drives]
        self.drive_box["values"] = labels
        if labels:
            with_disc = [k for k, d in enumerate(drives) if d[2]]
            self.drive_box.current(with_disc[0] if with_disc else 0)
        else:
            self.drive_var.set("")
            self.log_line("No drives found.")

    def scan(self):
        exe = self._exe()
        if not exe or self.busy:
            return
        disc = self._disc_index()
        if disc is None:
            messagebox.showerror("MKV Ripper", "Pick a drive first (click Refresh drives).")
            return
        try:
            minlen = int(self.minlen_var.get())
        except ValueError:
            messagebox.showerror("MKV Ripper", "Min length must be a whole number of seconds.")
            return
        self._set_busy(True)
        self.status_var.set("Scanning disc (this can take a minute)...")
        self.pbar["value"] = 0

        def work():
            try:
                titles = scan_titles(exe, disc, minlen, self._robot_handler)
                self.scanned_minlen, self.scanned_disc = minlen, disc
                self.post("titles", titles)
                self.post("status", f"Found {len(titles)} title(s). Check the ones to rip.")
            except Exception as e:
                self.post("error", f"Scan failed:\n{e}")
            finally:
                self.post("done")
        threading.Thread(target=work, daemon=True).start()

    def _set_titles(self, titles):
        self.titles = titles
        self.checked = set()
        self.splits = {}
        self.whole = {}
        self.tree.delete(*self.tree.get_children())
        self.play_all = find_play_all(titles)
        for t in titles:
            self.tree.insert("", "end", iid=str(t["id"]), values=(
                CHECK_OFF, tlabel(t), t.get("duration", ""),
                t.get("chapters", ""), t.get("size", ""), "", ""))
        self.info_lbl.pack_forget()
        if self.play_all:
            big, eps = next(iter(self.play_all.items()))
            by_id = {t["id"]: t for t in titles}
            nums = ", ".join(tlabel(by_id[e]) for e in eps)
            if self.mode_var.get() == "tv":
                self.checked = set(eps)
                msg = (f"\u2714  Title {tlabel(by_id[big])} is 'Play all': titles {nums} back to back. "
                       f"Those episode titles are selected for you, in order.")
            else:
                msg = f"Title {tlabel(by_id[big])} looks like 'Play all' (titles {nums} back to back)."
            self.info_lbl.configure(text=msg)
            self.info_lbl.pack(anchor="w", pady=(8, 0))
        if titles:
            first = str(titles[0]["id"])
            self.tree.focus(first)
            self.tree.selection_set(first)
        self._refresh_names()

    def toggle(self, iid):
        tid = int(iid)
        if tid in self.checked:
            self.checked.discard(tid)
        else:
            self.checked.add(tid)
        self._refresh_names()

    def on_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if iid and col == "#1":
            self.toggle(iid)
        elif iid and col == "#6" and self.mode_var.get() == "tv":
            self.tree.focus(iid)
            self.tree.selection_set(iid)
            self.after(10, self.edit_split)

    def on_space(self, _event):
        iid = self.tree.focus()
        if iid:
            self.toggle(iid)

    def set_all(self, on):
        self.checked = {t["id"] for t in self.titles} if on else set()
        self._refresh_names()

    def _start(self):
        try:
            return int(self.start_var.get())
        except ValueError:
            return None

    def _plan(self, order):
        """For each checked title: (title, [file names relative to output folder], split points).
        Returns None if the naming input is incomplete."""
        if self.mode_var.get() == "movie":
            movie = self.movie_var.get().strip()
            if not movie:
                return None
            names = build_movie_names(movie, len(order))
            return [(t, [n], [], None) for t, n in zip(order, names)]   # splitting is a TV-only feature
        start = self._start()
        if start is None:
            return None
        plan, ep = [], start
        prefix = self.prefix_var.get()
        for t in order:
            pts = self.splits.get(t["id"], [])
            whole = self.whole.get(t["id"], 0)
            if whole > 1:
                plan.append((t, [build_multi_name(prefix, ep, ep + whole - 1)], [], None))
                ep += whole
                continue
            count = len(pts) + 1
            fallback = build_multi_name(prefix, ep, ep + count - 1) if pts else None
            plan.append((t, build_names(prefix, ep, count), pts, fallback))
            ep += count
        return plan

    def _likely_multi(self, t):
        """A long TV title with several chapters probably holds more than one episode."""
        try:
            chapters = int(t.get("chapters", "0"))
        except ValueError:
            chapters = 0
        return (self.mode_var.get() == "tv" and duration_seconds(t.get("duration", "")) >= 70 * 60
                and chapters >= 4 and not self.splits.get(t["id"]) and not self.whole.get(t["id"])
                and t["id"] not in getattr(self, "play_all", {}))

    def _refresh_names(self):
        movie_mode = self.mode_var.get() == "movie"
        if movie_mode:
            self.tv_row.pack_forget()
            self.movie_row.pack(fill="x")
            self.example_lbl.configure(
                text="Saved as  The Matrix (1999)\\The Matrix (1999).mkv.  The first checked title is the movie; "
                     "any others are saved beside it as Plex extras.")
        else:
            self.movie_row.pack_forget()
            self.tv_row.pack(fill="x")
            start = self._start()
            if start is None:
                self.example_lbl.configure(text="First episode must be a number.")
            else:
                self.example_lbl.configure(
                    text="Checked titles are numbered top to bottom:  "
                         + ",  ".join(build_names(self.prefix_var.get(), start, 3)) + "  ...")
        order = [t for t in self.titles if t["id"] in self.checked]
        plan = self._plan(order) or []
        name_for = {t["id"]: names for t, names, _, _ in plan}
        self.hinted = None
        for t in self.titles:
            iid = str(t["id"])
            on = t["id"] in self.checked
            pts = self.splits.get(t["id"], [])
            self.tree.set(iid, "chk", CHECK_ON if on else CHECK_OFF)
            multi = self._likely_multi(t)
            if movie_mode:
                eps = "\u2014"
            elif t["id"] in getattr(self, "play_all", {}):
                eps = "Play all (skip)"
            elif pts:
                eps = f"{len(pts) + 1}   \u2702 at ch. {','.join(map(str, pts))}"
            elif self.whole.get(t["id"], 0) > 1:
                eps = f"{self.whole[t['id']]}   in one file"
            elif multi:
                eps = "1   \u26a0 split?"
            else:
                eps = "1   \u2702"
            self.tree.set(iid, "episodes", eps)
            self.tree.item(iid, tags=("hint",) if multi else ())
            if multi and on and self.hinted is None:
                self.hinted = t
            names = name_for.get(t["id"], [])
            if len(names) > 1:
                shown = f"{names[0]}  \u2192  {names[-1]}"
            else:
                shown = names[0] if names else ""
            self.tree.set(iid, "newname", shown)

        if self.titles:
            self.empty_lbl.pack_forget()
        if self.hinted is not None:
            t = self.hinted
            self.hint_lbl.configure(text=f"\u26a0  Title {tlabel(t)} is {t.get('duration', '')} long. It probably holds "
                                         f"several episodes.")
            self.hint_row.pack(anchor="w", pady=(8, 0))
        else:
            self.hint_row.pack_forget()
        self._update_split_btn()

    def _split_hinted(self):
        if self.hinted is not None:
            iid = str(self.hinted["id"])
            self.tree.focus(iid)
            self.tree.selection_set(iid)
            self.edit_split()

    # ---- splitting ----
    def on_double(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and self.tree.identify_column(event.x) not in ("#1", "#6") and self.mode_var.get() == "tv":
            self.tree.focus(iid)
            self.tree.selection_set(iid)
            self.edit_split()

    def _first_episode(self, tid):
        """Episode number the given title would start at, counting checked titles above it."""
        ep = self._start() or 1
        for t in self.titles:
            if t["id"] == tid:
                return ep
            if t["id"] in self.checked:
                ep += self.whole.get(t["id"], 0) or (len(self.splits.get(t["id"], [])) + 1)
        return ep

    def edit_split(self):
        iid = self.tree.focus()
        if not iid or self.busy:
            return
        t = next(x for x in self.titles if str(x["id"]) == iid)
        tid = t["id"]
        try:
            chapters = int(t.get("chapters", "0"))
        except ValueError:
            chapters = 0
        mins = duration_seconds(t.get("duration", "")) // 60

        win = tk.Toplevel(self)
        win.title(f"Episodes in title {tlabel(t)}")
        win.transient(self)
        win.resizable(False, False)
        set_dark_titlebar(win, self.dark)
        frm = ttk.Frame(win, padding=20)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"Title {tlabel(t)} holds several episodes", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frm, text=f"{t.get('duration', '')} long  \u00b7  {chapters or '?'} chapters",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 14))

        ttk.Label(frm, text="How many episodes are on this title?").grid(row=2, column=0, sticky="w")
        if self.whole.get(tid):
            guess_eps = self.whole[tid]
        elif self.splits.get(tid):
            guess_eps = len(self.splits[tid]) + 1
        else:
            guess_eps = max(2, round(mins / 45)) if mins >= 70 else 2   # hour-slot dramas run ~45 min
        eps_var = tk.StringVar(value=str(guess_eps))
        ttk.Spinbox(frm, from_=2, to=50, textvariable=eps_var, width=5).grid(row=2, column=1, sticky="w", padx=8)

        how = tk.StringVar(value="whole" if self.whole.get(tid) else "split")

        # option 1: split
        ttk.Radiobutton(frm, text="Split into separate episode files", value="split", variable=how,
                        command=lambda: sync()).grid(row=3, column=0, columnspan=3, sticky="w", pady=(16, 4))
        sp = ttk.Frame(frm)
        sp.grid(row=4, column=0, columnspan=3, sticky="w", padx=(28, 0))
        ttk.Label(sp, text="New episodes start at chapters").pack(side="left")
        pts_var = tk.StringVar(value=",".join(map(str, self.splits.get(tid, []))))
        entry = ttk.Entry(sp, textvariable=pts_var, width=14)
        entry.pack(side="left", padx=8)
        fill_btn = ttk.Button(sp, text="Fill in", command=lambda: guess())
        fill_btn.pack(side="left")
        hint = ttk.Label(frm, text="", style="Muted.TLabel", wraplength=470, justify="left")
        hint.grid(row=5, column=0, columnspan=3, sticky="w", padx=(28, 0), pady=(4, 0))
        ttk.Label(frm, style="Muted.TLabel", wraplength=470, justify="left", text=(
            "Episode 1 always starts at chapter 1. If unsure, play the disc in VLC and use Playback > Chapter. "
            "If the split doesn't work out during the rip, the title is saved as one file instead "
            "(see below), so nothing is lost.")).grid(row=6, column=0, columnspan=3, sticky="w", padx=(28, 0), pady=(4, 0))

        # option 2: keep whole
        ttk.Radiobutton(frm, text="Keep as one file with all the episodes", value="whole", variable=how,
                        command=lambda: sync()).grid(row=7, column=0, columnspan=3, sticky="w", pady=(16, 4))
        whole_lbl = ttk.Label(frm, text="", wraplength=470, justify="left")
        whole_lbl.grid(row=8, column=0, columnspan=3, sticky="w", padx=(28, 0))
        ttk.Label(frm, style="Muted.TLabel", wraplength=470, justify="left", text=(
            "Plex lists every episode, but playing any of them plays the whole file. No MKVToolNix needed.")
                  ).grid(row=9, column=0, columnspan=3, sticky="w", padx=(28, 0), pady=(2, 0))

        def n_eps():
            try:
                return max(2, int(eps_var.get()))
            except ValueError:
                return 2

        def guess():
            n = n_eps()
            g = guess_split_points(chapters, n)
            if g is None:
                hint.configure(text=f"{chapters} chapters don't divide evenly into {n} episodes, so type "
                                    "the chapters in yourself.")
            else:
                pts_var.set(",".join(map(str, g)))
                hint.configure(text=f"Filled in {chapters // n} chapters per episode. This is a guess: check one "
                                    "episode change in VLC if you can.")

        def sync(*_):
            first = self._first_episode(tid)
            name = build_multi_name(self.prefix_var.get(), first, first + n_eps() - 1)
            whole_lbl.configure(text=f"Saved as  {name}")
            state = "normal" if how.get() == "split" else "disabled"
            entry.configure(state=state)
            fill_btn.configure(state=state)
        eps_var.trace_add("write", sync)
        if not pts_var.get():
            guess()
        sync()

        def save():
            self.splits.pop(tid, None)
            self.whole.pop(tid, None)
            if how.get() == "whole":
                self.whole[tid] = n_eps()
            else:
                try:
                    pts = parse_split_points(pts_var.get(), chapters or None)
                except ValueError as e:
                    messagebox.showerror("MKV Ripper", str(e), parent=win)
                    return
                if not pts:
                    messagebox.showerror("MKV Ripper", "Enter the chapters where episodes start, or click Fill in.",
                                         parent=win)
                    return
                self.splits[tid] = pts
            self.checked.add(tid)
            self._refresh_names()
            win.destroy()

        def single():
            self.splits.pop(tid, None)
            self.whole.pop(tid, None)
            self._refresh_names()
            win.destroy()

        btns = ttk.Frame(frm)
        btns.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(20, 0))
        ttk.Button(btns, text="It's just one episode", command=single).pack(side="left")
        ttk.Button(btns, text="Save", style="Accent.TButton", width=10, command=save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=8)
        win.bind("<Return>", lambda _e: save())
        win.bind("<Escape>", lambda _e: win.destroy())
        win.grab_set()

    def rip(self):
        exe = self._exe()
        if not exe or self.busy:
            return
        if not self.titles:
            messagebox.showerror("MKV Ripper", "Scan a disc first.")
            return
        if self._disc_index() != self.scanned_disc or str(self.scanned_minlen) != self.minlen_var.get().strip():
            messagebox.showerror("MKV Ripper", "The drive or min length changed since the last scan. "
                                 "Click Scan disc again so the title numbers match.")
            return
        order = [t for t in self.titles if t["id"] in self.checked]
        if not order:
            messagebox.showerror("MKV Ripper", "Check at least one title.")
            return
        if self.mode_var.get() == "movie":
            text, what = self.movie_var.get().strip(), "movie name"
            if not text:
                messagebox.showerror("MKV Ripper", "Type the movie name, e.g.  The Matrix (1999)")
                return
        else:
            text, what = self.prefix_var.get(), "prefix"
            if self._start() is None:
                messagebox.showerror("MKV Ripper", "First episode # must be a number.")
                return
        bad = check_prefix(text)
        if bad:
            messagebox.showerror("MKV Ripper", f"The {what} can't contain these characters: {bad}")
            return
        out = self.out_var.get().strip()
        if not out:
            messagebox.showerror("MKV Ripper", "Choose an output folder.")
            return

        plan = [(t, [os.path.join(out, n) for n in names], pts, fb and os.path.join(out, fb))
                for t, names, pts, fb in self._plan(order)]
        mkvmerge = self.mkvmerge_var.get().strip()
        if any(pts for _, _, pts, _ in plan) and not os.path.isfile(mkvmerge):
            if not messagebox.askyesno(
                    "MKV Ripper", "Splitting needs MKVToolNix, which isn't installed (Settings > Install / update).\n\n"
                                  "Rip anyway and keep those titles as one multi-episode file each (like S1E1-E3)?"):
                return
        for _, paths, _, _ in plan:
            for p in paths:
                os.makedirs(os.path.dirname(p), exist_ok=True)
        existing = [os.path.basename(p) for _, paths, _, _ in plan for p in paths if os.path.exists(p)]
        if existing and not messagebox.askyesno(
                "MKV Ripper", "These files already exist and will be replaced:\n\n"
                + "\n".join(existing) + "\n\nContinue?"):
            return

        disc, minlen = self.scanned_disc, self.scanned_minlen
        self._set_busy(True)

        def split_progress(pct):
            self.post("progress", pct * 65536 // 100)

        def work():
            done, notes = 0, []
            try:
                for n, (t, paths, pts, fallback) in enumerate(plan, 1):
                    label = os.path.basename(paths[0]) + (f" ... {os.path.basename(paths[-1])}" if len(paths) > 1 else "")
                    self.post("status", f"Ripping title {tlabel(t)} -> {label} ({n} of {len(plan)})")
                    self.post("log", f"--- Title {tlabel(t)} -> {label}")
                    self.post("progress", 0)
                    if pts:
                        self.post("log", f"Will split before chapters {','.join(map(str, pts))}")
                    result = rip_title(exe, disc, minlen, t["id"], paths, self._robot_handler,
                                       mkvmerge=mkvmerge, split_points=pts, on_split_progress=split_progress,
                                       fallback_path=fallback)
                    if result.startswith("fallback"):
                        msg = (f"Title {tlabel(t)}: couldn't split ({result.split(': ', 1)[1]}), so it was saved as "
                               f"one file: {os.path.basename(fallback)}")
                        self.post("log", msg)
                        notes.append(msg)
                        done += 1
                    else:
                        done += len(paths)
                self.post("progress", 65536)
                self.post("status", f"Done. Saved {done} file(s) to {out}")
                self.post("info", f"Finished. {done} file(s) saved to:\n{out}" + ("\n\n" + "\n".join(notes) if notes else ""))
            except Exception as e:
                self.post("error", f"Stopped after {done} file(s):\n{e}")
                self.post("status", "Stopped with an error.")
            finally:
                self.post("done")
        threading.Thread(target=work, daemon=True).start()

    def browse_mkvmerge(self):
        p = filedialog.askopenfilename(title="Find mkvmerge.exe",
                                       filetypes=[("mkvmerge", "mkvmerge*.exe"), ("All files", "*.*")])
        if p:
            self.mkvmerge_var.set(p)

if __name__ == "__main__":
    App().mainloop()
