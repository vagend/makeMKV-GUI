; Inno Setup script for the Windows installer (built by .github/workflows/release.yml):
;   iscc /DAppVersion=1.0.5 /DSourceExe="dist\MKVRipper.exe" packaging\windows-installer.iss
; Installs for the current user only (no admin prompt) into %LOCALAPPDATA%\Programs\MKV Ripper,
; adds a Start-menu entry, and lists the app under Settings > Apps with an Uninstall button.
; MakeMKV, MKVToolNix and your ripped files are left alone on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceExe
  #define SourceExe "..\dist\MKVRipper.exe"
#endif

[Setup]
AppId={{271E2000-3C63-4147-999D-D0C4B6DF3C14}
AppName=MKV Ripper
AppVersion={#AppVersion}
AppVerName=MKV Ripper {#AppVersion}
AppPublisher=MKV Ripper
AppPublisherURL=https://github.com/vagend/makeMKV-GUI
DefaultDirName={userpf}\MKV Ripper
DefaultGroupName=MKV Ripper
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=MKVRipper-setup
UninstallDisplayIcon={app}\MKVRipper.exe
UninstallDisplayName=MKV Ripper
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; DestName: "MKVRipper.exe"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\MKV Ripper"; Filename: "{app}\MKVRipper.exe"
Name: "{userdesktop}\MKV Ripper"; Filename: "{app}\MKVRipper.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"; Flags: unchecked

[Run]
Filename: "{app}\MKVRipper.exe"; Description: "Open MKV Ripper"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; the previous exe kept after a self-update, then the folder if nothing else is in it
Type: files; Name: "{app}\MKVRipper.exe.old"
Type: files; Name: "{app}\MKVRipper.exe.new"
Type: dirifempty; Name: "{app}"
