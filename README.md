# MKV Ripper

A small Windows front end for MakeMKV that rips the titles you pick and names them for Plex.

- **TV mode:** give a prefix (e.g. `S1E` or `Belgravia (2020) - S01E`) and a first episode number. Checked titles are saved as `S1E1.mkv`, `S1E2.mkv`, …
- **Split titles:** if a disc stores several episodes as one big title, enter the chapters where each new episode starts (e.g. `6,11`). After ripping, MKVToolNix splits it losslessly and each piece gets the next episode number.
- **Movie mode:** give the movie name and year (e.g. `The Matrix (1999)`). The first checked title is saved as `The Matrix (1999)\The Matrix (1999).mkv`; any other checked titles are saved beside it as Plex extras (`Extra 1-other.mkv`, …).

## Requirements

- Windows
- [Python 3](https://www.python.org/downloads/) (the python.org installer includes tkinter)
- [MakeMKV](https://www.makemkv.com/) — found automatically in `C:\Program Files (x86)\MakeMKV`
- [MKVToolNix](https://mkvtoolnix.download/) — only needed for splitting; found automatically in `C:\Program Files\MKVToolNix`

If either tool is installed somewhere else, use its **Browse...** button in the app.

## Run

```
python mkv_ripper.py
```

or double-click `mkv_ripper.py`.

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
