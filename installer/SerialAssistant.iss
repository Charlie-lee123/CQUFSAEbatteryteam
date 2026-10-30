; ============================================================================
;  CQUFSAE 串口调试助手 —— Inno Setup 安装脚本
;
;  设计目标：
;   - "对标应用商店下载体验"：有安装向导、有进度、装完即可用（用户 2026-10-13 点名）。
;   - 支持**自定义安装位置**，升级时记住上次位置不再问。
;   - 装完自动建桌面快捷方式（默认勾上）+ 开始菜单，卸载一并清干净。
;   - **与主程序同一套 UI 语言**（用户 2026-10-15 点名）：黑白灰配色 + 车队队标，
;     明暗两套图跟着系统主题切。配色取自 ui_theme，不在这里另抄色值。
;   - **装新版自动卸掉旧版**（用户 2026-10-15 点名）：见 PrepareToInstall 的说明。
;
;  为什么默认装到 %LOCALAPPDATA%\Programs\...：
;   免管理员权限即可安装/升级（per-user 安装），双击下一步到底就行。
;
;  更新方式（用户选择"安装器接管更新"）：
;   软件内点「立即更新」→ 下载新版安装器 → 运行它 → 覆盖安装。AppId 不变，
;   Inno 识别为"已有安装"，沿用上次目录。
;
;  编译（需先装 Inno Setup 6.2+；本机是 6.7）：
;     python devtools\_make_installer_art.py   ; 先出向导图（改了主题要重跑）
;     python devtools\_build_installer.py       ; 再出包（版本号从 version.py 现读）
; ============================================================================

#define AppName        "CQUFSAE串口助手"
#define AppShortName   "SerialAssistant"
#define AppPublisher   "重庆大学方程式赛车队 (CQUFSAE)"
#define AppURL         "https://github.com/Charlie-lee123/CQUFSAEbatteryteam"

; 版本号由 devtools\_build_installer.py 读 version.py 后经 /DAppVersion=x.y 注入
; （version.py 是版本号唯一出处，不许在别处硬编码）。直接 iscc 编译时下面的
; 兜底值会生效，但那种用法会打出 "0.0" 的包，正式流程一律走构建脚本。
#ifndef AppVersion
  #define AppVersion "0.0"
#endif

; AppId 去掉双花括号转义的裸值：Inno 的注册表项是 <AppId>_is1，[Code] 里要用裸值
#define AppIdPlain     "8F3C1B42-7A6E-4D91-9C15-2E7B4A0D5F31"

#define ExeName        "SerialAssistant.exe"
#define SrcExe         AddBackslash(SourcePath) + "..\发布\" + ExeName
#define SrcIcon        AddBackslash(SourcePath) + "..\发布\app.ico"
#define ArtDir         AddBackslash(SourcePath) + "art"
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
DisableDirPage=no
UsePreviousAppDir=yes
DirExistsWarning=auto
AllowNoIcons=yes
PrivilegesRequired=lowest
; dialog=装的时候问一次"给谁装"；commandline=允许 /CURRENTUSER 直接指定
; （软件内自动更新拉起安装器时就走这个，免得多一个框）
PrivilegesRequiredOverridesAllowed=dialog commandline

; ---- 产物 ----
OutputDir={#OutDir}
OutputBaseFilename=SerialAssistant-setup-{#AppVersion}
SetupIconFile={#SrcIcon}
UninstallDisplayIcon={app}\app.ico
UninstallDisplayName={#AppName}

; 压缩：安装包要小、装得快
Compression=lzma2/max
SolidCompression=yes

; ---- 外观：和主程序同一套 ----
; 现代样式（左右分栏 + 大图），再配上跟系统主题走的队标图。
; 图由 devtools\_make_installer_art.py 生成（配色直接读 ui_theme）。
WizardStyle=modern
; modern 样式没有左侧大图（那是 classic 的），logo 出现在**右上角小图**：
; 黑色标题带 + 白色队标，和主程序的深色标题栏一个气质（实测截图确认）。
; 明暗两套跟系统主题走，图由 devtools\_make_installer_art.py 生成（配色读 ui_theme）。
WizardSmallImageFile={#ArtDir}\small_light.bmp,{#ArtDir}\small_light@2x.bmp
WizardSmallImageFileDynamicDark={#ArtDir}\small_dark.bmp,{#ArtDir}\small_dark@2x.bmp
; 不弹语言选择框（团队工具，装的是中文版；选语言那一步纯属多点一下）
ShowLanguageDialog=no

; 关掉"欢迎"页与"选择开始菜单文件夹"，只留必要步骤：目录 → 任务 → 安装 → 完成
DisableWelcomePage=yes
DisableProgramGroupPage=yes

; ---- 卸载 ----
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinese.CreateDesktopIcon=创建桌面快捷方式
chinese.AdditionalIcons=附加快捷方式：
chinese.LaunchApp=立即运行 {#AppName}
chinese.RemoveOldTitle=发现旧版本
chinese.RemoveOldText=安装位置和这次选的不一样，将先移除旧版本（%1），再装到新位置。%n%n你的串口配置、主题与日志会保留。
chinese.UninstallKeepData=是否同时删除本地设置与日志？
chinese.UninstallKeepDataSub=选「否」将保留你的串口配置、主题与日志，下次安装后立即恢复。请选择：
chinese.RemoveData=删除设置与日志
chinese.KeepData=保留（推荐）
english.CreateDesktopIcon=Create a &desktop shortcut
english.AdditionalIcons=Additional shortcuts:
english.LaunchApp=Launch {#AppName}
english.RemoveOldTitle=Older version found
english.RemoveOldText=The previous installation is in a different folder. It will be removed (%1) before installing here.%n%nYour settings, theme and logs will be kept.
english.UninstallKeepData=Also remove local settings and logs?
english.UninstallKeepDataSub=Choose No to keep your serial settings, theme and logs. Please choose:
english.RemoveData=Remove settings and logs
english.KeepData=Keep (recommended)

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "{#SrcExe}"; DestDir: "{app}"; Flags: ignoreversion
; 图标单独放一份：快捷方式指它而不是 50MB 的 exe（Windows 从大 exe 抽图标
; 会超时退化成白纸，见 devtools\_desktop_shortcut.py 的说明）
Source: "{#SrcIcon}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#ExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#ExeName}"; \
    WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ExeName}"; Description: "{cm:LaunchApp}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清掉安装目录里可能残留的日志（程序自己写的）
Type: filesandordirs; Name: "{app}\logs"

[Code]
const
  UninstKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8F3C1B42-7A6E-4D91-9C15-2E7B4A0D5F31}_is1';
  AppRegKey = 'Software\MimoWorks';

// ---------------------------------------------------------------------------
// 旧版本检测：同一个 AppId 的卸载信息可能在 HKLM（曾经选过"所有用户"安装）
// 也可能在 HKCU（默认的 per-user 安装），两处都要查 —— 只查一处就会出现
// "装完应用和功能里有两条"的经典毛病。
// ---------------------------------------------------------------------------
function QueryUninstValue(const ValueName: String; var Value: String): Boolean;
begin
  Result := RegQueryStringValue(HKLM, UninstKey, ValueName, Value);
  if Result then
  begin
    Log('  旧版卸载表(HKLM) ' + ValueName + ' = ' + Value);
    Exit;
  end;
  Result := RegQueryStringValue(HKCU, UninstKey, ValueName, Value);
  if Result then
    Log('  旧版卸载表(HKCU) ' + ValueName + ' = ' + Value)
  else
    Log('  旧版卸载表里没有 ' + ValueName);
end;

// 旧版装在哪儿：优先读 InstallLocation；老版没写这个值时退回从
// UninstallString（指向 unins000.exe）取它所在目录。
function QueryPrevDir(var Dir: String): Boolean;
var
  Uninst: String;
begin
  Result := QueryUninstValue('InstallLocation', Dir) and (Dir <> '');
  if Result then
    Exit;
  Result := QueryUninstValue('UninstallString', Uninst) and (Uninst <> '');
  if Result then
    Dir := ExtractFileDir(RemoveQuotes(Uninst));
end;

function Normalize(const Path: String): String;
begin
  Result := RemoveBackslashUnlessRoot(Trim(Path));
end;

// ---------------------------------------------------------------------------
// 旧版本拆除。
//
// 为什么不直接调老卸载程序（unins000.exe /VERYSILENT）：**老卸载程序在静默模式
// 下会把"是否删除设置"当成默认值（是）**，一跑就把用户的串口配置抹了。想救回来
// 得先导出注册表，而导出要么用 reg.exe、要么用注册表枚举 —— 前者在受限环境里会
// 被拦、后者 Pascal Script 根本没提供（RegEnumKeyEx 不存在，试过）。
//
// 所以干脆自己拆：旧目录整个删、旧卸载条目和旧快捷方式清掉。**配置位于
// HKCU\Software\MimoWorks，全程不碰** —— 拆完再装，用户的配置一点没少。
// 顺带还更快（不起第二个安装进程）也更好验证（每一步都有落地证据）。
// ---------------------------------------------------------------------------
procedure RemoveOldInstall(const OldDir: String);
var
  Dir, KeepDir: String;
begin
  Dir := Normalize(OldDir);
  Log('移除旧版本：目录=' + Dir);
  if Dir = '' then
    Exit;

  // ⛔ 先把旧目录里的 logs 搬到临时位置再 DelTree：里面是用户的**接收历史**
  //    （2026-10-18 用户点名"更新时用户数据要保留"）。装完由 CurStepChanged
  //    搬回 {app}\logs，新版首次启动会再把它迁到 %LOCALAPPDATA%。
  // ⚠️ Pascal Script 里没有 CopyFiles（那是预处理宏）—— 用 CopyFile 逐个搬，
  //    只搬真正有用的两份：日志与接收历史。
  KeepDir := ExpandConstant('{tmp}\CQUFSAE_keep');
  if DirExists(Dir + '\logs') then
  begin
    ForceDirectories(KeepDir);
    if CopyFile(Dir + '\logs\serialassistant.log',
                KeepDir + '\serialassistant.log', False) then
      Log('  已备份旧日志');
    if CopyFile(Dir + '\logs\接收历史.log',
                KeepDir + '\接收历史.log', False) then
      Log('  已备份旧接收历史');
  end;

  // 只删显式记在注册表里的那个安装目录，别顺手套到别处去
  if DelTree(Dir, True, True, True) then
    Log('  旧目录已删除')
  else
    Log('  旧目录删除失败（可能被占用或没权限）：' + Dir);

  // 卸载条目（「应用和功能」里那两条）：HKLM/HKCU 都清
  RegDeleteKeyIncludingSubkeys(HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8F3C1B42-7A6E-4D91-9C15-2E7B4A0D5F31}_is1');
  RegDeleteKeyIncludingSubkeys(HKLM,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8F3C1B42-7A6E-4D91-9C15-2E7B4A0D5F31}_is1');

  // 旧快捷方式（桌面 + 开始菜单文件夹），新装会重新建
  // ⚠️ 2026-10-30 改名（CQUFSAE串口调试助手 → CQUFSAE串口助手）：老快捷方式是
  //    **旧名字**，不显式删就会和新的并存在桌面上（用户看到两个图标）。
  DeleteFile(ExpandConstant('{autodesktop}\CQUFSAE串口调试助手.lnk'));
  DeleteFile(ExpandConstant('{userprograms}\CQUFSAE串口调试助手.lnk'));
  DeleteFile(ExpandConstant('{autodesktop}\{#AppName}.lnk'));
  DeleteFile(ExpandConstant('{userprograms}\{#AppName}.lnk'));
  DelTree(ExpandConstant('{userprograms}\{#AppName}'), True, True, True);
  Log('  旧快捷方式与卸载条目已清');
end;

// 装新版时把旧版拆掉：只在"旧版装在**别的目录**"时才需要 —— 同目录时 Inno
// 自己就是覆盖安装（新文件盖旧文件、卸载条目就地更新），不该先删。
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  PrevDir, NewDir: String;
begin
  Result := '';
  if not QueryPrevDir(PrevDir) then
  begin
    Log('没有检测到旧版本，按全新安装处理');
    Exit;
  end;
  NewDir := Normalize(ExpandConstant('{app}'));
  Log('检测到旧版本：' + Normalize(PrevDir) + '；本次要装到：' + NewDir);
  if Normalize(PrevDir) = NewDir then
  begin
    Log('  与上次同目录 → 交给 Inno 覆盖安装（不预先删）');
    Exit;
  end;

  SuppressibleMsgBox(FmtMessage(CustomMessage('RemoveOldText'), [PrevDir]),
                     mbInformation, MB_OK, IDOK);
  RemoveOldInstall(PrevDir);
end;

// ---------------------------------------------------------------------------
// 界面：把向导的面板/文字刷成主程序那套黑白灰。
// 配色和 ui_theme 的两个调色板一一对应（暗：#0D0D0F 底 / #F2F3F5 字；
// 亮：#FFFFFF 底 / #111214 字），改主题时别忘了同步 devtools\_make_installer_art.py
// 生成的图。
// ---------------------------------------------------------------------------
function SystemUsesLightTheme(): Boolean;
var
  Value: Cardinal;
begin
  Result := True;                          // 取不到就按亮的来（默认主题）
  if RegQueryDWordValue(HKEY_CURRENT_USER,
     'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize',
     'AppsUseLightTheme', Value) then
    Result := Value <> 0;
end;

procedure ApplyWizardTheme();
var
  Bg, Ink, Ink2: TColor;
begin
  if SystemUsesLightTheme() then
  begin
    Bg := $FFFFFF;  Ink := $141211;  Ink2 := $68635F;
  end
  else
  begin
    Bg := $0F0D0D;  Ink := $F5F3F2;  Ink2 := $A8A19C;
  end;

  WizardForm.MainPanel.Color := Bg;
  WizardForm.PageNameLabel.Font.Color := Ink;
  WizardForm.PageDescriptionLabel.Font.Color := Ink2;
  WizardForm.FinishedLabel.Font.Color := Ink2;
  if WizardForm.WelcomeLabel2 <> nil then
    WizardForm.WelcomeLabel2.Font.Color := Ink2;
end;

procedure InitializeWizard();
begin
  ApplyWizardTheme();
end;

// 卸载程序（unins000.exe）也照着刷一遍，别一边黑白一边彩色
procedure InitializeUninstallProgressForm();
begin
  if SystemUsesLightTheme() then
  begin
    UninstallProgressForm.Color := $FFFFFF;
    UninstallProgressForm.MainPanel.Color := $FFFFFF;
    UninstallProgressForm.PageNameLabel.Font.Color := $141211;
    UninstallProgressForm.PageDescriptionLabel.Font.Color := $68635F;
  end
  else
  begin
    UninstallProgressForm.Color := $0F0D0D;
    UninstallProgressForm.MainPanel.Color := $0F0D0D;
    UninstallProgressForm.PageNameLabel.Font.Color := $F5F3F2;
    UninstallProgressForm.PageDescriptionLabel.Font.Color := $A8A19C;
  end;
end;

// ---------------------------------------------------------------------------
// 卸载时询问是否一并删除本地设置与日志。
// 默认**保留**（推荐）—— 用户重装/升级后配置立刻回来。
// ⚠️ 静默卸载（/VERYSILENT，比如装新版时自动卸旧版）**一律保留**：那种场合
//    没人能回答问句，抹掉配置是纯粹的伤害。
// ---------------------------------------------------------------------------
procedure CurStepChanged(CurStep: TSetupStep);
var
  KeepDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    KeepDir := ExpandConstant('{tmp}\CQUFSAE_keep');
    if DirExists(KeepDir) then
    begin
      ForceDirectories(ExpandConstant('{app}\logs'));
      CopyFile(KeepDir + '\serialassistant.log',
               ExpandConstant('{app}\logs\serialassistant.log'), False);
      CopyFile(KeepDir + '\接收历史.log',
               ExpandConstant('{app}\logs\接收历史.log'), False);
      Log('已把旧日志/接收历史搬回安装目录（新版启动后会迁到用户目录）');
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Answer: Integer;
begin
  if UninstallSilent then
    Exit;
  if CurUninstallStep = usPostUninstall then
  begin
    Answer := SuppressibleMsgBox(ExpandConstant('{cm:UninstallKeepDataSub}'),
                                 mbConfirmation, MB_YESNO, IDNO);
    if Answer = IDYES then
    begin
      RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, AppRegKey);
      // 新版把日志/接收历史放在用户目录（%LOCALAPPDATA%\CQUFSAE-SerialAssistant），
      // 老版本在 ~/.serialassistant —— 两个都清掉。
      DelTree(ExpandConstant('{%LOCALAPPDATA}\CQUFSAE-SerialAssistant'), True, True, True);
      DelTree(ExpandConstant('{%USERPROFILE}\.serialassistant'), True, True, True);
    end;
  end;
end;
