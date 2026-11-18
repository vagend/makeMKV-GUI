# dmgbuild settings for the Mac disk image (used by .github/workflows/release.yml):
#   dmgbuild -s packaging/mac/dmg-settings.py -D app="dist/MKV Ripper.app" -D background=bg.tiff "MKV Ripper" out.dmg
# The window shows the app and an Applications shortcut over a background that says what to do.
import os.path

application = defines["app"]            # noqa: F821 - provided by dmgbuild
appname = os.path.basename(application)

format = "UDZO"
files = [application]
symlinks = {"Applications": "/Applications"}
background = defines["background"]      # noqa: F821 - 640x420 pt, with a @2x version inside the .tiff

# window: no toolbar or sidebar, just the picture and the two icons
window_rect = ((200, 140), (640, 448))   # 420 of picture + the title bar
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
show_icon_preview = False
arrange_by = None
icon_size = 128
text_size = 14
label_pos = "bottom"
icon_locations = {appname: (160, 200), "Applications": (480, 200)}
