# CQUFSAE 串口调试助手

重庆大学方程式赛车队（CQUFSAE）电池组的 **BMS 串口数据读取工具**。

针对车队电池管理系统的实际数据格式做了深度定制：一轮数据里时间戳、电压、温度、
BMS 状态混在一起，这个工具负责把它们**按段正确着色、对齐、并能在出问题时一键定位**。

当前版本：**v1.3**（2026-10-11）

## 一键下载

[![下载 SerialAssistant.exe](https://img.shields.io/badge/%E4%B8%80%E9%94%AE%E4%B8%8B%E8%BD%BD-SerialAssistant.exe-2ea44f?logo=github&logoColor=white)](https://github.com/Charlie-lee123/CQUFSAEbatteryteam/releases/latest/download/SerialAssistant.exe)

也可以直接打开：
<https://github.com/Charlie-lee123/CQUFSAEbatteryteam/releases/latest/download/SerialAssistant.exe>

> 链接始终指向**最新 Release** 里的 `SerialAssistant.exe`（单文件、免安装，
> 下载后双击即用，日志写在 exe 旁的 `logs/` 里）。
> 注：链接在仓库发布第一个 Release 并上传该 exe 后生效。

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

- 串口自动扫描、插拔自动识别；打不开串口只提示、绝不弹窗打扰
- 串口开关、波特率 / 校验位 / 数据位 / 停止位 / 编码 / HEX 视图与发送、定时发送

**界面**

- 黑白双主题，跟随系统并实时切换；无边框窗口 + 自绘标题栏
  （最小化 / 最大化还原 / 关闭三键在任何状态下都在、位置一致）
- 深浅色队标（车队 VIS 素材本地化，赛场断网也能用）
- 界面按钮可按清单隐藏，防止队友误触

**工程化**

- 全部定制项走注册表（`customization.py`），设置面板按注册表自动生成
- 分级日志（`logs/serialassistant.log`，滚动 1MB × 3）
- 自测框架 `selftest.py`：19 项用例，覆盖编码 / 收发 / 着色 / 全屏 / 标题栏等，
  支持 offscreen 无界面运行

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
├── app_log.py            # 分级日志
├── selftest.py           # 19 项自测（offscreen 可跑）
├── assets/               # 图标与车队 VIS 素材（打包必须带上）
├── devtools/             # 开发工具：打包产物实测、渲染出图、探针、反向验证补丁
│   └── archive/          # 历史诊断脚本（保留备查）
├── docs/images/          # README 截图
├── build.bat             # 一键打包（产物输出到 发布/）
├── run.bat               # 从源码启动
└── requirements.txt      # 依赖清单
```

## 安装与使用

### 方式一：直接运行打包好的 exe

到 [Releases](https://github.com/Charlie-lee123/CQUFSAEbatteryteam/releases)
下载 `SerialAssistant.exe`（单文件、免安装），双击即用。日志写在 exe 旁的
`logs/serialassistant.log`。

### 方式二：从源码运行

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

### 自测

```bat
.venv\Scripts\python.exe selftest.py                    # offscreen，无界面
set QT_QPA_PLATFORM=windows && .venv\Scripts\python.exe selftest.py   # 真机窗口
```

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

## License

[MIT](LICENSE)
