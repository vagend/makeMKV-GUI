"""
MKV Ripper core: talks to MakeMKV (makemkvcon) and MKVToolNix (mkvmerge), names files for Plex,
installs/updates the tools with winget, and updates the app from GitHub Releases.
No GUI code here, so it can be tested on its own.
"""

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

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


def find_mkvextract(mkvmerge=""):
    """mkvextract comes with mkvmerge in MKVToolNix."""
    if mkvmerge:
        p = os.path.join(os.path.dirname(mkvmerge), "mkvextract.exe" if sys.platform == "win32" else "mkvextract")
        if os.path.isfile(p):
            return p
    return shutil.which("mkvextract") or ""


def chapter_times(mkvextract, src):
    """Start time in seconds of each chapter in an MKV, in order (chapter 1 first)."""
    out = os.path.join(os.path.dirname(src), "chapters.txt")
    proc = _popen([mkvextract, src, "chapters", "--simple", out])
    proc.communicate()
    times = []
    try:
        with open(out, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"CHAPTER(\d+)=(\d+):(\d+):(\d+(?:\.\d+)?)", line.strip())
                if m:
                    times.append(int(m.group(2)) * 3600 + int(m.group(3)) * 60 + float(m.group(4)))
    except OSError:
        pass
    return times


def media_duration(mkvmerge, src):
    """Length of an MKV in seconds from mkvmerge's JSON identification (0 if unknown)."""
    try:
        proc = _popen([mkvmerge, "-J", src])
        out, _ = proc.communicate()
        ns = json.loads(out).get("container", {}).get("properties", {}).get("duration") or 0
        return ns / 1e9
    except Exception:  # noqa: BLE001
        return 0


def auto_split_points(times, total, episodes):
    """Chapters (1-based) where episodes 2..N start, picked as the chapter starts closest to
    equal-length episodes. E.g. a 2:22:07 title with chapters at 0:47:19 and 1:34:19 -> [7, 13]."""
    if episodes < 2 or len(times) < episodes or total <= 0:
        return None
    points, prev = [], 1
    for k in range(1, episodes):
        target = total * k / episodes
        # only chapters after the previous cut, leaving room for the remaining episodes
        choices = range(prev + 1, len(times) - (episodes - 1 - k) + 1)
        if not choices:
            return None
        best = min(choices, key=lambda c: abs(times[c - 1] - target))
        points.append(best)
        prev = best
    return points


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
            if isinstance(split_points, dict):          # {"auto": episodes, "total": seconds}
                extract = find_mkvextract(mkvmerge)
                if not extract:
                    raise RuntimeError("mkvextract (part of MKVToolNix) wasn't found")
                times = chapter_times(extract, ripped)
                total = media_duration(mkvmerge, ripped) or split_points["total"] or (times[-1] if times else 0)
                pts = auto_split_points(times, total, split_points["auto"])
                if not pts:
                    raise RuntimeError(f"couldn't find {split_points['auto']} episodes in {len(times)} chapters")
                if on_line:
                    fmt = lambda t: f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"
                    on_line("MSG", ["", "", "", "Splitting before chapters " + ", ".join(
                        f"{c} ({fmt(times[c - 1])})" for c in pts)])
                split_points = pts
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
