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
            elif attr == ATTR_SOURCE:
                t["source"] = value
            elif attr == ATTR_OUTPUT_NAME:
                t["output"] = value
    return [titles[k] for k in sorted(titles)]


def build_names(prefix, start, count, ext=".mkv"):
    return [f"{prefix}{start + i}{ext}" for i in range(count)]


def build_movie_names(movie, count, ext=".mkv"):
    """First checked title is the movie; any others become Plex 'other' extras.
    Paths are relative to the output folder: '<movie>/<movie>.mkv', '<movie>/Extra 1-other.mkv', ..."""
    names = [os.path.join(movie, f"{movie}{ext}")] if count else []
    names += [os.path.join(movie, f"Extra {i}-other{ext}") for i in range(1, count)]
    return names


def check_prefix(prefix):
    bad = [c for c in prefix if c in INVALID_FILENAME_CHARS]
    return "".join(sorted(set(bad)))


def rip_title(exe, disc_index, minlength, title_id, final_paths, on_line=None,
              mkvmerge=None, split_points=None, on_split_progress=None):
    """Rip one title into a temp folder, optionally split it at chapters, then move the
    result(s) to final_paths (one path, or one per episode when splitting)."""
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
            return

        parts = split_file(mkvmerge, ripped, split_points, os.path.join(tmp, "parts"), on_split_progress)
        if len(parts) != len(final_paths):
            # Don't lose the rip: keep the unsplit file so it can be split by hand.
            base, ext = os.path.splitext(final_paths[0])
            keep = f"{base} (unsplit){ext}"
            os.replace(ripped, keep)
            raise RuntimeError(f"Expected {len(final_paths)} episodes from title {title_id} but the split "
                               f"made {len(parts)}. The unsplit rip was kept as:\n{keep}")
        for part, dest in zip(parts, final_paths):
            os.replace(part, dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------

CHECK_ON, CHECK_OFF = "\u2611", "\u2610"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.geometry("940x640")
        self.minsize(760, 500)

        self.exe_var = tk.StringVar(value=find_makemkvcon())
        self.mkvmerge_var = tk.StringVar(value=find_mkvmerge())
        self.drive_var = tk.StringVar()
        self.minlen_var = tk.StringVar(value="120")
        self.mode_var = tk.StringVar(value="tv")
        self.prefix_var = tk.StringVar(value="S1E")
        self.movie_var = tk.StringVar(value="")
        self.start_var = tk.StringVar(value="1")
        self.out_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready.")

        self.drives = []          # [(index, drive_name, disc_name)]
        self.titles = []          # title dicts
        self.checked = set()      # title ids
        self.splits = {}          # title id -> [chapters where a new episode starts]
        self.scanned_minlen = 120
        self.scanned_disc = 0
        self.busy = False
        self.msgs = queue.Queue()

        cleanup_old_exe()
        self.title(f"MKV Ripper (build {APP_BUILD})" if APP_BUILD else "MKV Ripper (from source)")
        self._build_menu()
        self._build()
        for v in (self.prefix_var, self.start_var, self.movie_var, self.mode_var):
            v.trace_add("write", lambda *_: self._refresh_names())
        self.after(100, self._pump)
        if self.exe_var.get():
            self.refresh_drives()
        self.after(800, self._startup_checks)

    # ---- dependencies & updates ----
    def _build_menu(self):
        bar = tk.Menu(self)
        tools = tk.Menu(bar, tearoff=False)
        tools.add_command(label="Install / update MakeMKV", command=lambda: self.dep_action("MakeMKV"))
        tools.add_command(label="Install / update MKVToolNix", command=lambda: self.dep_action("MKVToolNix"))
        tools.add_separator()
        tools.add_command(label="Check for MKV Ripper update", command=lambda: self.check_app_update(quiet=False))
        bar.add_cascade(label="Tools", menu=tools)
        self.configure(menu=bar)

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
        pad = dict(padx=6, pady=4)
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="makemkvcon:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.exe_var).grid(row=0, column=1, columnspan=4, sticky="ew", padx=4)
        ttk.Button(top, text="Browse...", command=self.browse_exe).grid(row=0, column=5)
        ttk.Label(top, text="mkvmerge:").grid(row=2, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.mkvmerge_var).grid(row=2, column=1, columnspan=4, sticky="ew", padx=4)
        ttk.Button(top, text="Browse...", command=self.browse_mkvmerge).grid(row=2, column=5)
        ttk.Label(top, text="(only needed for splitting)", foreground="#555").grid(row=2, column=6, sticky="w")

        ttk.Label(top, text="Drive:").grid(row=1, column=0, sticky="w")
        self.drive_box = ttk.Combobox(top, textvariable=self.drive_var, state="readonly", width=50)
        self.drive_box.grid(row=1, column=1, columnspan=2, sticky="ew", padx=4)
        ttk.Button(top, text="Refresh drives", command=self.refresh_drives).grid(row=1, column=3, padx=2)
        ttk.Label(top, text="Min length (sec):").grid(row=1, column=4, sticky="e")
        ttk.Entry(top, textvariable=self.minlen_var, width=6).grid(row=1, column=5, sticky="w")
        self.scan_btn = ttk.Button(top, text="Scan disc", command=self.scan)
        self.scan_btn.grid(row=1, column=6, padx=4)
        top.columnconfigure(1, weight=1)

        # titles table
        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, **pad)
        cols = ("chk", "id", "name", "duration", "chapters", "size", "split", "newname")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        heads = {"chk": "Rip", "id": "#", "name": "Title name", "duration": "Length",
                 "chapters": "Chapters", "size": "Size", "split": "Split at chapters",
                 "newname": "Will be saved as"}
        widths = {"chk": 40, "id": 40, "name": 190, "duration": 70, "chapters": 65, "size": 80,
                  "split": 110, "newname": 300}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor="center" if c in ("chk", "id", "chapters") else "w")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.on_click)
        self.tree.bind("<space>", self.on_space)
        self.tree.bind("<Double-1>", self.on_double)

        sel = ttk.Frame(self)
        sel.pack(fill="x", padx=6)
        ttk.Button(sel, text="Check all", command=lambda: self.set_all(True)).pack(side="left")
        ttk.Button(sel, text="Uncheck all", command=lambda: self.set_all(False)).pack(side="left", padx=4)
        ttk.Button(sel, text="Split title...", command=self.edit_split).pack(side="left", padx=4)
        ttk.Label(sel, text="Click a box (or Space) to check a title. Double-click a title holding "
                            "several episodes to split it.").pack(side="left", padx=8)

        # naming + output
        nm = ttk.LabelFrame(self, text="Naming")
        nm.pack(fill="x", **pad)
        modes = ttk.Frame(nm)
        modes.grid(row=0, column=0, columnspan=6, sticky="w", padx=4, pady=(4, 0))
        ttk.Radiobutton(modes, text="TV show (prefix + episode number)", value="tv",
                        variable=self.mode_var).pack(side="left")
        ttk.Radiobutton(modes, text="Movie", value="movie",
                        variable=self.mode_var).pack(side="left", padx=16)

        # TV row
        self.tv_row = ttk.Frame(nm)
        self.tv_row.grid(row=1, column=0, columnspan=6, sticky="ew", padx=4, pady=4)
        ttk.Label(self.tv_row, text="Prefix:").pack(side="left")
        ttk.Entry(self.tv_row, textvariable=self.prefix_var, width=30).pack(side="left", padx=4)
        ttk.Label(self.tv_row, text="First episode #:").pack(side="left", padx=(16, 4))
        ttk.Spinbox(self.tv_row, from_=0, to=9999, textvariable=self.start_var, width=6).pack(side="left")

        # Movie row
        self.movie_row = ttk.Frame(nm)
        ttk.Label(self.movie_row, text="Movie name (Year):").pack(side="left")
        ttk.Entry(self.movie_row, textvariable=self.movie_var, width=40).pack(side="left", padx=4)

        self.example_lbl = ttk.Label(nm, text="", foreground="#555")
        self.example_lbl.grid(row=2, column=0, columnspan=6, sticky="w", padx=4)

        ttk.Label(nm, text="Output folder:").grid(row=3, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(nm, textvariable=self.out_var).grid(row=3, column=1, columnspan=4, sticky="ew")
        ttk.Button(nm, text="Browse...", command=self.browse_out).grid(row=3, column=5, padx=4)
        nm.columnconfigure(4, weight=1)

        # progress + log
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", **pad)
        self.rip_btn = ttk.Button(bottom, text="Rip checked titles", command=self.rip)
        self.rip_btn.pack(side="left")
        self.pbar = ttk.Progressbar(bottom, maximum=65536)
        self.pbar.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(self, textvariable=self.status_var).pack(fill="x", padx=8)

        self.log = tk.Text(self, height=7, state="disabled", wrap="word")
        self.log.pack(fill="x", padx=6, pady=(2, 6))
        self._refresh_names()

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
        self.tree.delete(*self.tree.get_children())
        for t in titles:
            self.tree.insert("", "end", iid=str(t["id"]), values=(
                CHECK_OFF, t["id"], t.get("name", ""), t.get("duration", ""),
                t.get("chapters", ""), t.get("size", ""), "", ""))
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
        if iid and self.tree.identify_column(event.x) == "#1":
            self.toggle(iid)

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
            return [(t, [n], []) for t, n in zip(order, names)]   # splitting is a TV-only feature
        start = self._start()
        if start is None:
            return None
        plan, ep = [], start
        for t in order:
            pts = self.splits.get(t["id"], [])
            count = len(pts) + 1
            plan.append((t, build_names(self.prefix_var.get(), ep, count), pts))
            ep += count
        return plan

    def _refresh_names(self):
        movie_mode = self.mode_var.get() == "movie"
        if movie_mode:
            self.tv_row.grid_remove()
            self.movie_row.grid(row=1, column=0, columnspan=6, sticky="ew", padx=4, pady=4)
            self.example_lbl.configure(
                text="e.g. The Matrix (1999) -> saved as  The Matrix (1999)\\The Matrix (1999).mkv.  "
                     "The first checked title is the movie; any others are saved as extras.")
        else:
            self.movie_row.grid_remove()
            self.tv_row.grid()
            start = self._start()
            if start is None:
                self.example_lbl.configure(text="(first episode # must be a number)")
            else:
                self.example_lbl.configure(
                    text="e.g. " + ", ".join(build_names(self.prefix_var.get(), start, 3)) + " ...")
        order = [t for t in self.titles if t["id"] in self.checked]
        plan = self._plan(order) or []
        name_for = {t["id"]: names for t, names, _ in plan}
        for t in self.titles:
            iid = str(t["id"])
            on = t["id"] in self.checked
            pts = self.splits.get(t["id"], [])
            self.tree.set(iid, "chk", CHECK_ON if on else CHECK_OFF)
            self.tree.set(iid, "split", ",".join(map(str, pts)) + (" (off in Movie mode)" if pts and movie_mode else ""))
            names = name_for.get(t["id"], [])
            if len(names) > 1:
                shown = f"{names[0]} ... {names[-1]}  ({len(names)} files)"
            else:
                shown = names[0] if names else ""
            self.tree.set(iid, "newname", shown)

    # ---- splitting ----
    def on_double(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and self.tree.identify_column(event.x) != "#1":
            self.tree.focus(iid)
            self.tree.selection_set(iid)
            self.edit_split()

    def edit_split(self):
        iid = self.tree.focus()
        if not iid:
            messagebox.showinfo("MKV Ripper", "Click a title in the list first, then Split title...")
            return
        t = next(x for x in self.titles if str(x["id"]) == iid)
        try:
            chapters = int(t.get("chapters", "0"))
        except ValueError:
            chapters = 0

        win = tk.Toplevel(self)
        win.title(f"Split title {t['id']}")
        win.transient(self)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"Title {t['id']}: {t.get('name', '')}   {t.get('duration', '')},  "
                            f"{chapters or '?'} chapters", font=("", 10, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(frm, justify="left", wraplength=440, text=(
            "Enter the chapter numbers where episode 2, 3, ... START. Chapter 1 is always the start "
            "of the first episode. Example for 3 episodes: 6,11\n\n"
            "To find them, play the disc (or a test rip) in VLC and use Playback > Chapter to see "
            "which chapter each episode's opening begins on.")).grid(row=1, column=0, columnspan=4, sticky="w", pady=8)

        ttk.Label(frm, text="New episode starts at chapters:").grid(row=2, column=0, sticky="w")
        pts_var = tk.StringVar(value=",".join(map(str, self.splits.get(t["id"], []))))
        entry = ttk.Entry(frm, textvariable=pts_var, width=24)
        entry.grid(row=2, column=1, columnspan=3, sticky="w", padx=4)

        ttk.Label(frm, text="Or guess from episode count:").grid(row=3, column=0, sticky="w", pady=(8, 0))
        eps_var = tk.StringVar(value=str(max(2, len(self.splits.get(t["id"], [])) + 1)))
        ttk.Spinbox(frm, from_=2, to=50, textvariable=eps_var, width=5).grid(row=3, column=1, sticky="w", padx=4, pady=(8, 0))
        hint = ttk.Label(frm, text="", foreground="#555", wraplength=440, justify="left")
        hint.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))

        def guess():
            try:
                n = int(eps_var.get())
            except ValueError:
                return
            g = guess_split_points(chapters, n)
            if g is None:
                hint.configure(text=f"{chapters} chapters don't divide evenly into {n} episodes. "
                                    "Check the chapters in VLC and type them in.")
            else:
                pts_var.set(",".join(map(str, g)))
                hint.configure(text=f"Guessed {chapters // n} chapters per episode. This is only a guess: "
                                    "check one episode boundary in VLC before ripping.")
        ttk.Button(frm, text="Guess", command=guess).grid(row=3, column=2, sticky="w", pady=(8, 0))

        def save():
            try:
                pts = parse_split_points(pts_var.get(), chapters or None)
            except ValueError as e:
                messagebox.showerror("MKV Ripper", str(e), parent=win)
                return
            if pts:
                self.splits[t["id"]] = pts
                self.checked.add(t["id"])
            else:
                self.splits.pop(t["id"], None)
            self._refresh_names()
            win.destroy()

        def clear():
            pts_var.set("")
            save()

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Don't split", command=clear).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=4)
        ttk.Button(btns, text="OK", command=save).pack(side="left", padx=4)
        entry.focus_set()
        win.bind("<Return>", lambda _e: save())
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

        plan = [(t, [os.path.join(out, n) for n in names], pts) for t, names, pts in self._plan(order)]
        mkvmerge = self.mkvmerge_var.get().strip()
        if any(pts for _, _, pts in plan) and not os.path.isfile(mkvmerge):
            messagebox.showerror("MKV Ripper", "Splitting needs MKVToolNix's mkvmerge.exe. Install MKVToolNix "
                                 "(free, mkvtoolnix.download) or use the mkvmerge Browse... button.")
            return
        for _, paths, _ in plan:
            for p in paths:
                os.makedirs(os.path.dirname(p), exist_ok=True)
        existing = [os.path.basename(p) for _, paths, _ in plan for p in paths if os.path.exists(p)]
        if existing and not messagebox.askyesno(
                "MKV Ripper", "These files already exist and will be replaced:\n\n"
                + "\n".join(existing) + "\n\nContinue?"):
            return

        disc, minlen = self.scanned_disc, self.scanned_minlen
        self._set_busy(True)

        def split_progress(pct):
            self.post("progress", pct * 65536 // 100)

        def work():
            done = 0
            try:
                for n, (t, paths, pts) in enumerate(plan, 1):
                    label = os.path.basename(paths[0]) + (f" ... {os.path.basename(paths[-1])}" if len(paths) > 1 else "")
                    self.post("status", f"Ripping title {t['id']} -> {label} ({n} of {len(plan)})")
                    self.post("log", f"--- Title {t['id']} -> {label}")
                    self.post("progress", 0)
                    if pts:
                        self.post("log", f"Will split before chapters {','.join(map(str, pts))}")
                    rip_title(exe, disc, minlen, t["id"], paths, self._robot_handler,
                              mkvmerge=mkvmerge, split_points=pts, on_split_progress=split_progress)
                    done += len(paths)
                self.post("progress", 65536)
                self.post("status", f"Done. Saved {done} file(s) to {out}")
                self.post("info", f"Finished. {done} file(s) saved to:\n{out}")
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
