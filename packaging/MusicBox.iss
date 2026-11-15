#define AppVersion "1.0.6"
[Setup]
AppId={{D8FCA820-B6CA-4B30-8DF9-8D246848FB22}
AppName=MusicBox
AppVersion={#AppVersion}
AppPublisher=MusicBox contributors
DefaultDirName={localappdata}\Programs\MusicBox
DefaultGroupName=MusicBox
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=MusicBox-Setup-unsigned
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\MusicBox.Desktop.exe
CloseApplications=no
SetupLogging=yes
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
[Files]
Source: "..\dist\MusicBox\*"; DestDir: "{app}"; Excludes: "data\*,portable-mode,*.WebView2\*,EBWebView\*,__pycache__\*,.secret.key,*.pyc,*.log,*.db,*.sqlite3"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\MusicBox"; Filename: "{app}\MusicBox.Desktop.exe"
Name: "{autodesktop}\MusicBox"; Filename: "{app}\MusicBox.Desktop.exe"; Tasks: desktopicon
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\MusicBox.Desktop.exe"; Description: "Launch MusicBox"; Flags: nowait postinstall skipifsilent
