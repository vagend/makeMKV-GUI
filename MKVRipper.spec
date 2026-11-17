# PyInstaller recipe for MKV Ripper (GitHub builds it with this; see .github/workflows/release.yml).
# By hand on Windows:  pip install -r requirements-build.txt  then  pyinstaller --noconfirm --clean MKVRipper.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH)

datas = [(str(ROOT / "web"), "web")]
datas += [(str(ROOT / "APP_BUILD"), ".")] if (ROOT / "APP_BUILD").exists() else []   # written by the GitHub build
datas += collect_data_files("webview")          # pywebview's own scripts
binaries = collect_dynamic_libs("webview")      # and its WebView2 helper DLLs

a = Analysis(
    [str(ROOT / "mkv_ripper.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["clr"],                      # pythonnet, which pywebview uses on Windows
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi", "cefpython3"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="MKVRipper", console=False, upx=False,
          icon=str(ROOT / "assets" / "icon.ico"))
