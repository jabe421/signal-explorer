#ifndef BuildVersion
  #define BuildVersion "0.1.0-beta.1"
#endif
#ifndef SourceRoot
  #define SourceRoot "..\.."
#endif

[Setup]
; Keep this ID stable across upgrades.
AppId={{B60F325F-191B-4A83-BB64-617829B782E8}
AppName=Signal Explorer
AppVersion={#BuildVersion}
AppPublisher=Signal Explorer contributors
AppPublisherURL=https://github.com/jabe421/signal-explorer
AppSupportURL=https://github.com/jabe421/signal-explorer/issues
DefaultDirName={autopf}\Signal Explorer
DefaultGroupName=Signal Explorer
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.19041
OutputDir={#SourceRoot}\dist\installer
OutputBaseFilename=SignalExplorer-Setup-{#BuildVersion}-windows-x64
SetupIconFile={#SourceRoot}\src\signal_explorer\assets\signal-explorer.ico
UninstallDisplayIcon={app}\SignalExplorer.exe
LicenseFile={#SourceRoot}\LICENSE
InfoAfterFile={#SourceRoot}\packaging\windows\Driver Setup.txt
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
Uninstallable=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceRoot}\dist\SignalExplorer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Signal Explorer"; Filename: "{app}\SignalExplorer.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Signal Explorer"; Filename: "{app}\SignalExplorer.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\Signal Explorer User Guide"; Filename: "{app}\Users Guide.pdf"
Name: "{group}\Signal Explorer Driver Setup"; Filename: "{app}\Driver Setup.txt"

[Run]
Filename: "{app}\SignalExplorer.exe"; Description: "Launch Signal Explorer"; Flags: nowait postinstall skipifsilent runasoriginaluser

; No wildcard uninstall deletes: user-created files/settings and shared drivers
; remain intact. Inno removes only files managed by this installer.
