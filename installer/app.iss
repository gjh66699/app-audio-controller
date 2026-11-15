; 应用音量控制器 - Inno Setup 安装脚本
;
; 构建：先 pyinstaller 打包，再 iscc installer\app.iss
; 或直接运行 build_installer.py（两件事一起做）
;
; 说明：
;   PrivilegesRequired=lowest  -> 不需要管理员权限，装到 %LOCALAPPDATA%\Programs
;   {autopf} 在 lowest 模式下自动解析为 {localappdata}\Programs

#define MyAppName "应用音量控制器"
#define MyAppVersion "1.0.1"
#define MyAppPublisher "gjh66699"
#define MyAppURL "https://github.com/gjh66699/app-volume-controller"
#define MyAppExeName "AppVolumeController.exe"

[Setup]
AppId={{4FFFB87F-20B3-491C-BD9E-4568A41AF6B8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}
DefaultDirName={autopf}\AppVolumeController
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=Output
OutputBaseFilename=AppVolumeController-Setup-{#MyAppVersion}
SetupIconFile=..\app_volume_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
AllowNoIcons=yes

[Languages]
; 中文放第一个 = 默认语言。第三方翻译，来源见 installer/ChineseSimplified.isl 文件头
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; 整个 PyInstaller onedir 产物，含 _internal 里的 Python 运行时和 tcl/tk
Source: "..\dist\AppVolumeController\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "单独调节每个程序的音量"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "单独调节每个程序的音量"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller 运行时会在这里留缓存，卸载时一并清掉
Type: filesandordirs; Name: "{app}\_internal"
