; RyuuMD Windows 安装包（Inno Setup 6）。
; 输入 dist\RyuuMD\（build.bat onedir 产物），输出 dist\installer\RyuuMD-Setup-<版本>.exe。
; 构建：build.bat installer，或 ISCC packaging\RyuuMD.iss。
; 按用户安装（%LOCALAPPDATA%\Programs\RyuuMD），无需管理员；注册表全部写 HKCU，
; 与 app/core/file_assoc.py 的 register() 写同一组键，卸载时一并清理。
; 用户数据 %APPDATA%\RyuuMD 不随卸载删除。

#define AppName "RyuuMD"
#define AppExe "RyuuMD.exe"
#define SrcDir SourcePath + "..\dist\RyuuMD"
#define ProgId "RyuuMD.Document"

; 版本号取自 exe 版本资源（version_info.txt），不另行维护
#define VerMajor
#define VerMinor
#define VerRev
#define VerBuild
#expr GetVersionComponents(SrcDir + "\" + AppExe, VerMajor, VerMinor, VerRev, VerBuild)
#define AppVersion Str(VerMajor) + "." + Str(VerMinor) + "." + Str(VerRev)

[Setup]
; AppId 固定不变：升级安装与卸载靠它识别同一应用
AppId={{6531152D-A22A-4151-AF1B-7386C7895A62}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=ryuuji
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
CloseApplications=yes
SetupIconFile=..\assets\icon.ico
WizardImageFile=wizard.bmp
WizardSmallImageFile=wizard-small.bmp
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist\installer
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; 「打开方式」注册：ProgID + 各扩展名 OpenWithProgids + Capabilities（与 file_assoc.register() 一致）
Root: HKCU; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueData: "Markdown 文档 (RyuuMD)"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueData: "{app}\{#AppExe},0"
Root: HKCU; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\.markdown\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.md\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.mdown\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.mdx\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.mkd\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\RyuuMD"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\RyuuMD\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "RyuuMD"
Root: HKCU; Subkey: "Software\RyuuMD\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "轻量、全本地、极速的 Markdown 编辑与阅读工具"
Root: HKCU; Subkey: "Software\RyuuMD\Capabilities\FileAssociations"; ValueType: string; ValueName: ".markdown"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\RyuuMD\Capabilities\FileAssociations"; ValueType: string; ValueName: ".md"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\RyuuMD\Capabilities\FileAssociations"; ValueType: string; ValueName: ".mdown"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\RyuuMD\Capabilities\FileAssociations"; ValueType: string; ValueName: ".mdx"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\RyuuMD\Capabilities\FileAssociations"; ValueType: string; ValueName: ".mkd"; ValueData: "{#ProgId}"
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "RyuuMD"; ValueData: "Software\RyuuMD\Capabilities"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
