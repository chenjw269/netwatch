; Inno Setup 脚本。由 build.ps1 调用，生成给小白用的安装包。

#define MyAppName "网络波动"
#define MyAppVersion "1.0.0"
#define MyAppExeName "NetWatch.exe"

[Setup]
AppId={{7E4A1C92-5B38-4F0E-9C6D-2A8B7F15E4D3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=NetWatch
DefaultDirName={localappdata}\NetWatch
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=NetWatch-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: checkedonce

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\网络波动"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\网络波动"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动网络波动"; Flags: nowait postinstall skipifsilent
