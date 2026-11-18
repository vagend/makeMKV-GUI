# MKV Ripper

A small Windows and Mac front end for MakeMKV that rips the titles you pick and names them for Plex.

- **TV mode:** give a prefix (e.g. `S1E` or `Belgravia (2020) - S01E`) and a first episode number. Checked titles are saved as `S1E1.mkv`, `S1E2.mkv`, …
- **Split titles:** if a disc stores several episodes as one big title, say how many episodes it holds. MKV Ripper picks the chapters that give the most even episodes, or you type them yourself (e.g. `7,13`). Either way it shows each episode's length before anything is split, and flags an episode that's much longer or shorter than the rest. On a DVD the chapter times come from the disc's own files when you scan, so you see the lengths before ripping. If they can't be read, the rip pauses after copying the title so you can check the lengths (and change the chapters) before it splits. MKVToolNix then splits losslessly and each piece gets the next episode number.
- **Movie mode:** give the movie name and year (e.g. `The Matrix (1999)`). The first checked title is saved as `The Matrix (1999)/The Matrix (1999).mkv`; any other checked titles are saved beside it as Plex extras (`Extra 1-other.mkv`, …).

## Download and run (Windows)

1. Go to [Releases](https://github.com/vagend/makeMKV-GUI/releases/latest) and download **MKVRipper-setup.exe**. No Python needed.
2. Run it. It installs just for you (no admin prompt), adds **MKV Ripper** to the Start menu, and can be removed from **Settings > Apps**. (Or download **MKVRipper.exe** to run it without installing.)
3. Open MKV Ripper. If MakeMKV or MKVToolNix isn't installed, the app offers to install it with Windows Package Manager (winget). Windows may ask for permission.

Windows SmartScreen may warn about the download because it isn't code-signed. Click **More info > Run anyway**.

## Download and run (Mac)

1. Go to [Releases](https://github.com/vagend/makeMKV-GUI/releases/latest) and download **MKVRipper-mac-AppleSilicon.zip** for an M1 or newer Mac, or **MKVRipper-mac-Intel.zip** for an Intel Mac (Apple menu > About This Mac shows which you have).
2. Open the zip and drag **MKV Ripper** into **Applications**.
3. The first time you open it, macOS says it can't verify the app (it isn't signed with a paid Apple developer account). Click **Done**, open **System Settings > Privacy & Security**, scroll down and click **Open Anyway**. You only do this once; updates install themselves.
4. Install **MakeMKV** from [makemkv.com](https://www.makemkv.com/download/) (Homebrew no longer offers it). For splitting, the app installs **MKVToolNix** with Homebrew if you have it, or opens its download page.

macOS may ask once for permission to read the disc or the folder you save to; click **Allow**.

### Staying up to date

- **The app** checks GitHub for a newer release when it starts and offers to update and restart.
- **MakeMKV and MKVToolNix:** use the buttons in **Settings** to upgrade them (winget on Windows; Homebrew or the download page on a Mac).

winget comes with Windows 11 and current Windows 10 as part of "App Installer". If it's missing, install **App Installer** from the Microsoft Store.

### Running from source (optional)

With Python 3 installed: `pip install pywebview`, then `python mkv_ripper.py`.

The window is a web page (`web/index.html`) shown by pywebview (WebView2 on Windows, WebKit on a Mac); `mkv_core.py` holds the disc, rip, split and update logic.

### How releases work

Every push to `main` (except README-only changes) builds `MKVRipper.exe` on GitHub Actions and replaces the **latest** release with it, titled with the build number. Copies of the app compare their build number to that release and offer to update. Nothing to tag by hand.

## Usage

1. Pick the drive and click **Scan disc**.
2. Click the box beside each title to rip. Double-click a title to split it into episodes.
3. Choose TV or Movie and fill in the naming fields. The **Will be saved as** column previews the file names.
4. Pick an output folder and click **Rip checked titles**.

For a multi-disc set, set **First episode #** to continue from the last disc (e.g. 4 for disc 2 of a 3-per-disc set).

## Notes

- **Min length** hides titles shorter than that many seconds (default 120). Lower it if a short episode is missing.
- If a split produces a different number of pieces than expected, the app stops and keeps the whole unsplit rip as `… (unsplit).mkv` so nothing has to be ripped again.
- mkvmerge can only cut on a key frame, so split points may be off by a second or so.
