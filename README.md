# CQUFSAE 串口调试助手

重庆大学方程式赛车队（CQUFSAE）电池组的 **BMS 串口数据读取工具**。

针对车队电池管理系统的实际数据格式做了深度定制：一轮数据里时间戳、电压、温度、
BMS 状态混在一起，这个工具负责把它们**按段正确着色、对齐、并能在出问题时一键定位**。

当前版本：**v2.0**（2026-10-15）

## 一键下载

**推荐：安装版**（可选安装位置、自动建桌面快捷方式、带卸载程序）

[![下载安装包](https://img.shields.io/badge/%E4%B8%80%E9%94%AE%E4%B8%8B%E8%BD%BD-%E5%AE%89%E8%A3%85%E7%89%88%20Setup-2ea44f?logo=github&logoColor=white)](https://github.com/Charlie-lee123/CQUFSAEbatteryteam/releases/latest/download/SerialAssistant-setup-2.0.exe)

**免安装版**（单文件，下载后双击即用）

[![下载 SerialAssistant.exe](https://img.shields.io/badge/%E5%85%8D%E5%AE%89%E8%A3%85%E7%89%88-SerialAssistant.exe-0969da?logo=github&logoColor=white)](https://github.com/Charlie-lee123/CQUFSAEbatteryteam/releases/latest/download/SerialAssistant.exe)

也可以直接打开：
- 安装版：<https://github.com/Charlie-lee123/CQUFSAEbatteryteam/releases/latest/download/SerialAssistant-setup-2.0.exe>
- 免安装版：<https://github.com/Charlie-lee123/CQUFSAEbatteryteam/releases/latest/download/SerialAssistant.exe>

> 两个链接都指向**最新 Release**。安装版适合日常使用（能自选目录、生成桌面
> 与开始菜单快捷方式、卸载时可选保留设置）；免安装版是单文件，日志写在 exe
> 旁的 `logs/` 里。
> 注：链接在仓库发布对应 Release 并上传资产后生效。

## 界面

| 深色主题（默认全屏起手） | 浅色主题 |
| --- | --- |
| ![主界面](docs/images/main-window.png) | ![浅色主题](docs/images/main-light.png) |

数据着色特写——电压超限红 / 欠压绿、温度越界黄、BMS ERROR 橙，序号与数值自动补零，
小数点竖向对齐：

![数据着色](docs/images/data-coloring.png)

## 功能

**数据接收与显示**

- 流式收包合并：按 30ms 静默窗口成帧（另有 4096B / 200ms 双兜底），一帧只显示一条，
  数字不会被拦腰截断
- 每轮数据一个时间戳（紫色），年 / 月 / 日各有独立显示开关
- 采样点序号自动补零到两位，小数点竖向对齐；序号染蓝
- 电压分级着色：超出上限红、低于下限绿（上下限可在设置里改，单位 mV）
- 温度越界标黄（上下限可在设置里改，单位 ℃）
- BMS ERROR 整行标橙（温度越界保持黄色，两者按色区分）
- 「定位最近异常」按钮：跳到最近一次电压 / 温度越界的位置并整行高亮
  （BMS ERROR 作为兜底），高亮与搜索互不干扰
- 暂停 = 冻结画面（新数据进后台队列），恢复时一次性刷入并追底；暂停时自动折叠
  发送区与工具栏、自动定位到当前轮的时间戳（停留行数可配置）
- 行内搜索（Ctrl+F）、复制"正在查看的一轮"、保存日志
- 接收区一键展开全屏（Esc 退出），窗口级全屏会盖住任务栏

**连接**

- 连接方式可选**串口**或**蓝牙广播**
- 串口自动扫描、插拔自动识别；打不开串口只提示、绝不弹窗打扰
- 串口开关、波特率 / 校验位 / 数据位 / 停止位 / 编码 / HEX 视图与发送、定时发送
- 建链不卡界面：慢连接（如蓝牙虚拟串口）期间状态栏显示「正在连接 …」，
  再拨一下开关即可取消
- **蓝牙广播**（v2.0）：只被动扫描 BLE 广播包，**不配对、不连接、不绑定**，
  开机就能读；设备把数据放进广播包（manufacturer / service data 都认）即可。
  代价是单向且带宽低（BLE 4.x 单包 31 字节），所以广播模式下发送区禁用。
  附命令行探针 `SerialAssistant.exe --ble-scan [秒数]`：不进界面扫一遍，
  把设备名 / 地址 / 广播负载十六进制写到 `logs\蓝牙扫描结果.txt`，便于查自己的设备

**界面**

- 黑白双主题，跟随系统并实时切换；无边框窗口 + 自绘标题栏
  （最小化 / 最大化还原 / 关闭三键在任何状态下都在、位置一致）
- 深浅色队标（车队 VIS 素材本地化，赛场断网也能用）
- 界面按钮可按清单隐藏，防止队友误触

**工程化**

- 全部定制项走注册表（`customization.py`），设置面板按注册表自动生成
- 分级日志（`logs/serialassistant.log`，滚动 1MB × 3）
- 自测框架 `selftest.py`：26 项用例，覆盖编码 / 收发 / 着色 / 全屏 / 标题栏 /
  串口建链 / 蓝牙广播等，支持 offscreen 无界面运行

## 目录结构

```
SerialAssistant/
├── main.py               # 入口：日志 → 主题 → 主窗口 → 起手姿态
├── main_window.py        # 主窗口：布局、串口配置、接收区、全屏/暂停/搜索/定位
├── serial_io.py          # 串口读写线程、流式解码、HEX 编解码（纯逻辑，无界面）
├── ui_theme.py           # 主题 token、全局样式表、DWM 标题栏主题
├── ui_anim.py            # 120fps 非线性补间引擎（Tween / Reveal / 共享计时器）
├── ui_backdrop.py        # 页面底色与磨砂颗粒
├── ui_surface.py         # 平面卡片、圆角、柔和阴影
├── ui_brand.py           # 车队队标页头 + 自绘标题栏（三键 + 窗口图标）
├── ui_controls.py        # 自绘控件：按钮 / 开关 / 数字框 / 下拉框 / 警示按钮 …
├── ui_highlight.py       # 接收区着色（时间戳 / 序号 / 电压 / 温度 / BMS）与序号补齐
├── ui_settings.py        # 设置面板（按 customization 注册表动态生成）
├── customization.py      # 定制功能配置注册表（持久化 + 默认值迁移）
├── rx_history.py         # 历史接收数据：跟随「保留行数」的滚动留存与回放读取
├── updater.py            # 检查更新 / 下载校验 / 安装器或静默替换
├── version.py            # 版本号唯一出处（含 Release 产物 SHA256）
├── app_log.py            # 分级日志
├── selftest.py           # 24 项自测（offscreen 可跑）
├── assets/               # 图标与车队 VIS 素材（打包必须带上）
├── installer/            # Inno Setup 安装脚本（SerialAssistant.iss）
├── devtools/             # 开发工具：打包产物实测、渲染出图、探针、安装包构建
│   └── archive/          # 历史诊断脚本（保留备查）
├── docs/images/          # README 截图
├── build.bat             # 一键打包（产物输出到 发布/）
├── run.bat               # 从源码启动
└── requirements.txt      # 依赖清单
```

## 安装与使用

### 方式一：安装版（推荐，带安装向导）

到 [Releases](https://github.com/Charlie-lee123/CQUFSAEbatteryteam/releases)
下载 `SerialAssistant-setup-x.y.exe`，双击进入安装向导：

- **可自定义安装位置**（默认 `%LOCALAPPDATA%\Programs\SerialAssistant`，免管理员权限）；
- **自动创建桌面快捷方式**（默认勾选）与开始菜单项；
- 升级时**沿用上次选的安装目录**，不再重复询问；
- 「应用和功能」里有卸载入口，卸载时可选择保留或删除本地设置与日志。

安装版收到新版本时，程序内「立即更新」会下载新版**安装包**并启动安装向导接管，
不必手动去 GitHub。

> 若本机没装 Inno Setup，可直接用下面的免安装版 —— 两者功能完全一致。

### 方式二：免安装单文件版

到 [Releases](https://github.com/Charlie-lee123/CQUFSAEbatteryteam/releases)
下载 `SerialAssistant.exe`（单文件、免安装），双击即用。日志写在 exe 旁的
`logs/serialassistant.log`。这个形态的自动更新是**静默替换 exe**（不弹安装向导）。

### 方式三：从源码运行

```bat
git clone https://github.com/Charlie-lee123/CQUFSAEbatteryteam.git
cd CQUFSAEbatteryteam
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
run.bat
```

或手动启动：`.venv\Scripts\python.exe main.py`

### 打包

```bat
build.bat
```

产物输出到 **`发布\SerialAssistant.exe`**（脚本会顺带把桌面快捷方式重新指到新产物）。

### 制作安装包（可选）

```bat
.venv\Scripts\python.exe devtools\_build_installer.py --check   # 体检：查 exe / 编译器
.venv\Scripts\python.exe devtools\_build_installer.py           # 编译安装包
```

需要先装 [Inno Setup 6](https://jrsoftware.org/isdl.php)。产物是
`发布\SerialAssistant-setup-<版本>.exe`；脚本会从 `version.py` 现读版本号注入。
安装脚本源码在 `installer\SerialAssistant.iss`（`AppId` 是固定 GUID，升级识别依赖它，
**不可更改**）。

### 自测

```bat
.venv\Scripts\python.exe selftest.py                    # offscreen，无界面
set QT_QPA_PLATFORM=windows && .venv\Scripts\python.exe selftest.py   # 真机窗口
```

> 说明：`全屏展开与复制全部` 一项会验证「复制」链路，需要可用的系统剪贴板；
> 在非交互会话（无桌面剪贴板所有权）里这一项会自动跳过而非报错。

## 数据格式约定

工具按如下边界切分段落（与车队 BMS 的实际输出一致）：

```
[时间戳]                ← 新的一轮开始，电压段从此处起
1.3820 2.3824 …         ← 电压数据（按 电压上下限 判红 / 绿）
Temperature1:
1.22 2.95 …             ← 温度数据（按 温度上下限 判黄）
BMS ERROR               ← BMS 状态（橙），到此为止：其后的数值不再参与分段
```

- 采样点格式为 `序号.值`，电压值的单位是 mV、温度是 ℃（均取小数点后的整数部分）
- 段落边界以**时间戳**和 `Temperature1:` / `BMS` 为准，不依赖 `Voltage1:` 标题是否存在

## 版本

| 版本 | 日期 | 说明 |
| --- | --- | --- |
| v1.0 | 2026-10-10 | 首个发布版本，项目备份基线 |
| v1.1 | 2026-10-10 | 异常定位优先电压；数值段补零对齐；BMS ERROR 改橙色；修复设置面板滑轨与内容重叠；README 一键下载 |
| v1.2 | 2026-10-11 | 序号 0 不再补占位空格；修复拨动开关点不动/滑轨错位；开关改为按下即翻转；设置面板底部新增版本号、检查更新（自动比对 GitHub 最新 tag）与仓库地址链接 |
| v1.3 | 2026-10-11 | 电压/温度数据等宽网格对齐（列距统一、短行并回）；折行按视口宽度封顶；分包劈开的半行自动接回；温度与 BMS 明细行序号不再补 0；BMS 错误明细整块标橙 |
| v1.4 | 2026-10-12 | 新增「默认折叠发送区」定制项（可关，关掉后开局展开，设置面板统一开关样式且带读屏名称）；修复拨动开关命中区过大（此前停在开关同一高度任意位置都能切换，现需点在圆角轨道或文字上）；修复保存退出设置后最大化窗口四角出现缝隙（标题栏主题误作用到主窗口） |
| v1.5 | 2026-10-12 | 修复「复制本轮」数据不完整与时间戳出现在非开头位置（电压数据分包成多帧、每帧都带 `Voltage1:` 标题时，一轮会打出多个时间戳，复制时从轮内第二个起算导致前半段丢失；现在一轮只打一个）；新增「高亮异常数据」开关（关掉后不再标红/绿/黄/橙，只留时间戳与序号着色）；异常高亮改为精确覆盖有问题的**整条**采样点（序号+值），不再只染值、也不波及同行的正常数据；新增「接收区字体大小」滑动无级调节（带等宽 `CQUFSAE.2026` 实时预览，颜色与接收区正文一致）；设置面板新增「导出日志」一键另存；修复从最大化退出后窗口边缘没有拖拽光标提示（现在左/右/上/下与四角分别显示对应缩放光标，全屏时不提示）；串口配置栏五行控件的文字左端对齐、并让全部输入类控件的文字按**实测墨迹**竖直居中（数字框、可编辑/不可编辑下拉框、按钮统一处理，任意字号/DPI 下上下留白相等） |
| v1.6 | 2026-10-12 | 下拉选择全面统一成波特率那套展开交互：**「保留行数」从数字框改为可编辑下拉**（预设 500～500000 行展开选，也可直接输入任意值）；**弹层宽度按最宽选项自适应**（修复编码弹层把 `latin-1` 截成 `l...1`）；**所有下拉弹层加 150ms 展开动画**（从锚点一侧展开到全高并淡入，朝下/朝上开各自锚定）；修复非编辑类下拉弹层底部残留黑条、四角被裁成直角的问题（现与波特率弹层完全同观感） |
| v1.7 | 2026-10-12 | **检查更新重构**：版本检测走国内可直连多源链（jsDelivr → ghfast → gh-proxy → GitHub raw → API，每源 8 秒超时自动回退，开着代理走 GitHub 直连，整链失败还会经本机代理再试一遍）；发现新版本弹窗（立即更新 / 暂不更新 / 前往仓库）；应用内自动更新（镜像源下载 + SHA256 完整性校验 + 静默替换重启，配置完整保留）。**历史接收数据**：自动保存最近 5000 行接收数据（含时间戳）到与日志同目录的 `接收历史.log`，到量滚动覆盖最早数据，纯内存入队 + 定时批量落盘不影响接收性能；设置面板可一键打开记录文件。**修复欠压绿色高亮越界**：标绿区间严格限定在电压数据读取部分，不再染到温度采集区，且温度值不会被对齐补零篡改成假数据 |
| v1.8 | 2026-10-13 | **安装程序与卸载程序**（Inno Setup）：可自定义安装位置、装完自动创建桌面快捷方式、升级沿用上次目录、卸载可选保留设置；安装版收到新版时由安装向导接替更新。**本地留存行数与接收区一致**：`接收历史.log` 的留存行数跟随「保留行数」设置实时同步，改小立即裁剪、改大不复活旧行。**软件内回放留存数据**：串口配置栏新增「打开历史数据」，把本地留存内容灌进同一个接收区，时间戳/序号/电压红绿/温度/BMS 着色与搜索、定位异常、复制全部等能力与实时完全一致。 |
| v2.0 | 2026-10-15 | **蓝牙广播接收**（只扫描、不配对、不绑定）：新增「连接方式」切换与蓝牙设备扫描列表，设备把数据放进 BLE 广播包即可随时读取（manufacturer / service data 都认），复用同一套接收与着色管线；广播为单向，广播模式下发送区禁用。附 `--ble-scan` 命令行探针用于自查设备广播。**建链不再卡界面**：串口/蓝牙建链挪到工作线程，慢连接期间显示「正在连接 …」，再拨一次开关即可取消（此前蓝牙虚拟串口建链的几秒钟界面是冻住的）。**整项目瘦身**：清理旧构建快照与一次性探针（体积 3.7GB → 0.9GB，devtools 48 → 14 个脚本），删除死代码并抽出 14 处重复的配置还原逻辑。**修复**：自动更新下载完成后的「退出并重启」因缺少导入会报错（手动更新的 exe 替换路径会卡在旧进程里）、更新超时分支的日志调用会 NameError、自测的配置还原漏处理 `None` 值（可能污染用户设置）。 |

> v2.0 计划的第 5/6/7 项（安装器、留存行数同步、软件内回放）已在 **v1.8** 落地，
> 故 1.8 即这三项的正式发布版。

## License

[MIT](LICENSE)
