#define MyAppName "NEXDROP"
#define MyAppVersion "1.0"
#define MyAppPublisher "A.I.M.S"

[Setup]
AppId={{D2F19B86-DA8E-43BD-99D4-9A28A87F466A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\NEXDROP
DefaultGroupName={#MyAppName}
PrivilegesRequired=admin
OutputDir=release
OutputBaseFilename=NEXDROP-Setup
SetupIconFile=nexdrop_icon.ico
UninstallDisplayIcon={app}\NEXDROP.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Dirs]
Name: "{app}\NEXDROP_Data"; Permissions: users-modify; Flags: uninsneveruninstall

[Files]
Source: "release\NEXDROP-Portable.exe"; DestDir: "{app}"; DestName: "NEXDROP.exe"; Flags: ignoreversion

[Icons]
Name: "{group}\NEXDROP"; Filename: "{app}\NEXDROP.exe"
Name: "{autodesktop}\NEXDROP"; Filename: "{app}\NEXDROP.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\NEXDROP.exe"; Description: "Launch NEXDROP"; Flags: postinstall nowait skipifsilent
