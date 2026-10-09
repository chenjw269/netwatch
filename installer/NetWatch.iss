; Inno Setup 脚本。由 build.ps1 调用，生成给小白用的安装包。

#define MyAppName "NetWatch"
#define MyAppVersion "2.0.0"
#define MyAppExeName "NetWatch.App.exe"

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

[InstallDelete]
Type: files; Name: "{app}\NetWatch.exe"
Type: files; Name: "{autoprograms}\网络波动.lnk"
Type: files; Name: "{autodesktop}\网络波动.lnk"

[Files]
Source: "..\src\NetWatch.App\bin\x64\Release\net8.0-windows10.0.19041.0\win-x64\publish\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\NetWatch"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\NetWatch"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 NetWatch"; Flags: nowait postinstall skipifsilent
