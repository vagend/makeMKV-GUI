# MKV Ripper

A small Windows front end for MakeMKV that rips the titles you pick and names them for Plex.

- **TV mode:** give a prefix (e.g. `S1E` or `Belgravia (2020) - S01E`) and a first episode number. Checked titles are saved as `S1E1.mkv`, `S1E2.mkv`, …
- **Split titles:** if a disc stores several episodes as one big title, enter the chapters where each new episode starts (e.g. `6,11`). After ripping, MKVToolNix splits it losslessly and each piece gets the next episode number.
- **Movie mode:** give the movie name and year (e.g. `The Matrix (1999)`). The first checked title is saved as `The Matrix (1999)\The Matrix (1999).mkv`; any other checked titles are saved beside it as Plex extras (`Extra 1-other.mkv`, …).

## Download and run

1. Go to [Releases](https://github.com/vagend/makeMKV-GUI/releases/latest) and download **MKVRipper-setup.exe**. No Python needed.
2. Run it. It installs just for you (no admin prompt), adds **MKV Ripper** to the Start menu, and can be removed from **Settings > Apps**. (Or download **MKVRipper.exe** to run it without installing.)
3. Open MKV Ripper. If MakeMKV or MKVToolNix isn't installed, the app offers to install it with Windows Package Manager (winget). Windows may ask for permission.

Windows SmartScreen may warn about the download because it isn't code-signed. Click **More info > Run anyway**.

### Staying up to date

- **The app** checks GitHub for a newer release when it starts and offers to update and restart.
- **MakeMKV and MKVToolNix:** use **Tools > Install / update …** to upgrade them through winget.

winget comes with Windows 11 and current Windows 10 as part of "App Installer". If it's missing, install **App Installer** from the Microsoft Store.

### Running from source (optional)

With Python 3 installed: `pip install pywebview`, then `python mkv_ripper.py`.

The window is a web page (`web/index.html`) shown by pywebview using Windows' built-in WebView2; `mkv_core.py` holds the disc, rip, split and update logic.

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
