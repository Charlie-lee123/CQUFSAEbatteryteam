; ============================================================================
;  CQUFSAE 串口调试助手 —— Inno Setup 安装脚本
;
;  设计目标（用户 2026-10-13 点名的第 5 项）：
;   - "对标应用商店下载体验"：有安装向导、有进度、装完即可用。
;   - 支持**自定义安装位置**，且升级时记住用户上次选的位置不再问。
;   - 安装完成后**自动创建桌面快捷方式**（可选，默认勾上），
;     开始菜单同样自动建，卸载时一并清干净。
;   - 卸载程序注册到「应用和功能」，可完整卸载。
;
;  为什么默认装到 %LOCALAPPDATA%\Programs\...：
;   免管理员权限即可安装/升级（per-user 安装），用户双击下一步到底就行 ——
;   这正是"应用商店式"的顺手感。用户仍可在向导里改成任意目录。
;
;  更新方式（用户选择"安装器接管更新"）：
;   软件内点「立即更新」→ 下载新版安装器 → 运行它 → 本脚本用
;   /SILENT 或向导完成覆盖安装。升级时 AppId 不变，Inno 识别为"已有安装"，
;   自动沿用上次的安装目录，并跳过已存在的快捷方式询问。
;
;  编译（需先装 Inno Setup 6）：
;     iscc installer\SerialAssistant.iss
;   或走 devtools\_build_installer.py（会先校验 exe 存在、再调 iscc）。
; ============================================================================

#define AppName        "CQUFSAE串口调试助手"
#define AppShortName   "SerialAssistant"
#define AppPublisher   "重庆大学方程式赛车队 (CQUFSAE)"
#define AppURL         "https://github.com/Charlie-lee123/CQUFSAEbatteryteam"

; 版本号由 devtools\_build_installer.py 读 version.py 后经 /DAppVersion=x.y 注入
; （version.py 是版本号唯一出处，不许在别处硬编码）。直接 iscc 编译时下面的
; 兜底值会生效，但那种用法会打出 "0.0" 的包，正式流程一律走构建脚本。
#ifndef AppVersion
  #define AppVersion "0.0"
#endif

#define ExeName        "SerialAssistant.exe"
#define SrcExe         AddBackslash(SourcePath) + "..\发布\" + ExeName
#define SrcIcon        AddBackslash(SourcePath) + "..\发布\app.ico"
#define OutDir         AddBackslash(SourcePath) + "..\发布"

[Setup]
; AppId 一旦定下就**永远不要改**：它是 Inno 判断"这是同一个软件的升级"
; 的唯一依据，改了就会变成两个互不相干的程序（升级装成并存、卸载卸不干净）。
AppId={{8F3C1B42-7A6E-4D91-9C15-2E7B4A0D5F31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}

; ---- 安装位置：默认 per-user，免管理员；用户可改 ----
DefaultDirName={localappdata}\Programs\{#AppShortName}
DefaultGroupName={#AppName}
; 允许用户改目录（"支持自定义软件安装位置"）
DisableDirPage=no
; 升级时记住上次选的目录，不再反复问（应用商店不会每次都问你装哪儿）
UsePreviousAppDir=yes
; 目录为空才允许装（避免覆盖别人的东西）；升级时 Inno 自行处理
DirExistsWarning=auto
AllowNoIcons=yes
; 不要求管理员：per-user 安装，双击即可完成
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; ---- 产物 ----
OutputDir={#OutDir}
OutputBaseFilename=SerialAssistant-setup-{#AppVersion}
SetupIconFile={#SrcIcon}
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppName}

; 压缩：安装包要小、装得快
Compression=lzma2/max
SolidCompression=yes
; 界面用现代样式（Win11 圆角观感）
WizardStyle=modern
; 关掉"欢迎"页，减少一步点击 —— 更接近应用商店的"点两下就装好"
DisableWelcomePage=yes
; 允许在安装向导里选择是否创建桌面图标
DisableProgramGroupPage=yes

; ---- 卸载 ----
; 卸载时若程序还在运行，让用户关掉（避免留一个删不掉的文件）
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinese.CreateDesktopIcon=创建桌面快捷方式
chinese.AdditionalIcons=附加快捷方式：
chinese.LaunchApp=立即运行 {#AppName}
chinese.UninstallKeepData=是否同时删除本地设置与日志？
chinese.UninstallKeepDataSub=选「否」将保留你的串口配置、主题与日志，下次安装后立即恢复。请选择：
chinese.RemoveData=删除设置与日志
chinese.KeepData=保留（推荐）
english.CreateDesktopIcon=Create a &desktop shortcut
english.AdditionalIcons=Additional shortcuts:
english.LaunchApp=Launch {#AppName}
english.UninstallKeepData=Also remove local settings and logs?
english.UninstallKeepDataSub=Choose No to keep your serial settings, theme and logs. Please choose:
english.RemoveData=Remove settings and logs
english.KeepData=Keep (recommended)

[Tasks]
; 桌面快捷方式：默认勾上（用户点名"安装完成后自动创建桌面快捷方式"）
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "{#SrcExe}"; DestDir: "{app}"; Flags: ignoreversion
; 图标单独放一份：快捷方式指它而不是 50MB 的 exe（Windows 从大 exe 抽图标
; 会超时退化成白纸，见 devtools\_desktop_shortcut.py 的说明）
Source: "{#SrcIcon}"; DestDir: "{app}"; Flags: ignoreversion
; 说明文档（有就带上，没有不报错）
Source: "{#SourcePath}..\README.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
; 开始菜单（卸载入口也在这里）
Name: "{group}\{#AppName}"; Filename: "{app}\{#ExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
; 桌面快捷方式（受 Tasks 勾选控制）
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#ExeName}"; \
    WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Run]
; 装完可以直接运行（默认勾上）
Filename: "{app}\{#ExeName}"; Description: "{cm:LaunchApp}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清掉安装目录里可能残留的日志（程序自己写的）
Type: filesandordirs; Name: "{app}\logs"

[Code]
// ---------------------------------------------------------------------------
// 卸载时询问是否一并删除本地设置与日志。
// 设置存在注册表 HKCU\Software\MimoWorks；日志在 ~/.serialassistant。
// 默认**保留**（推荐）—— 用户重装/升级后配置立刻回来。
// ---------------------------------------------------------------------------
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Answer: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Answer := MsgBox(ExpandConstant('{cm:UninstallKeepDataSub}'),
                     mbConfirmation, MB_YESNO);
    if Answer = IDYES then
    begin
      // 用户选择"删除"：清注册表配置 + 日志目录
      RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, 'Software\MimoWorks');
      DelTree(ExpandConstant('{%USERPROFILE}\.serialassistant'), True, True, True);
    end;
  end;
end;
