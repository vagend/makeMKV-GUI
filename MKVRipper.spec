# PyInstaller recipe for MKV Ripper (GitHub builds it with this; see .github/workflows/release.yml).
# By hand:  pip install -r requirements-build.txt  then  pyinstaller --noconfirm --clean MKVRipper.spec
#   Windows -> dist/MKVRipper.exe (one file)      Mac -> "dist/MKV Ripper.app"
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH)
MAC = sys.platform == "darwin"
build_file = ROOT / "APP_BUILD"                 # written by the GitHub build
BUILD = build_file.read_text().strip() if build_file.exists() else "0"

datas = [(str(ROOT / "web"), "web"), (str(ROOT / "assets" / "icon.ico"), "assets")]
datas += [(str(build_file), ".")] if build_file.exists() else []
datas += collect_data_files("webview")          # pywebview's own scripts
datas += collect_data_files("certifi")          # backup certificate list for HTTPS
binaries = [] if MAC else collect_dynamic_libs("webview")   # its WebView2 helper DLLs (Windows)
hidden = ["truststore", "certifi"] + ([] if MAC else ["clr"])   # clr = pythonnet, pywebview's Windows bridge

a = Analysis(
    [str(ROOT / "mkv_ripper.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi", "cefpython3"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if not MAC:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="MKVRipper", console=False, upx=False,
              icon=str(ROOT / "assets" / "icon.ico"))
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MKVRipper", console=False, upx=False,
              argv_emulation=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="MKVRipper", upx=False)
    app = BUNDLE(
        coll,
        name="MKV Ripper.app",
        icon=str(ROOT / "assets" / "icon.icns"),
        bundle_identifier="io.github.vagend.mkvripper",
        info_plist={
            "CFBundleName": "MKV Ripper",
            "CFBundleDisplayName": "MKV Ripper",
            "CFBundleShortVersionString": f"1.0.{BUILD}",
            "CFBundleVersion": BUILD,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.video",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,          # follow Dark Mode
            # shown by macOS when the app first reads a disc or a folder
            "NSRemovableVolumesUsageDescription": "MKV Ripper reads the disc's chapter list to show episode lengths.",
            "NSNetworkVolumesUsageDescription": "MKV Ripper saves ripped files to the folder you choose.",
            "NSDocumentsFolderUsageDescription": "MKV Ripper saves ripped files to the folder you choose.",
            "NSDesktopFolderUsageDescription": "MKV Ripper saves ripped files to the folder you choose.",
            "NSDownloadsFolderUsageDescription": "MKV Ripper saves ripped files to the folder you choose.",
        },
    )
