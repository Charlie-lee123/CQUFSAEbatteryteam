# SerialAssistant 架构 —— 可执行的改动地图

> **这份文档怎么用**：动手改代码前，先到 §3「改动半径表」按你的**改动意图**查一行，它会告诉你「只该动哪几个文件:行 / 会连带影响谁 / 只需跑哪几条自测 / 要不要重新打包」。查完直接用 §4.2 的 `--only` 命令跑子集，不要全量重跑。
>
> 生成于 2026-11-27（AST 实测，非手写推测）。行号随代码演进会漂，**以函数名/常量为准**（`grep -n` 一下比死记行号可靠）。结构性结论（分层、无环、三条数据流的顺序铁律）比行号稳定得多。
> 分析脚本与原始数据在 `_check/_arch_ast.py`、`_check/_arch_graph.py`、`_check/_arch_slow.py`（`_check/` 不入库，脚本丢了可照本文描述重写）。
>
> **一句话结论**：这是一个「严格单向分层 + 单一入口汇聚」的项目，**没有循环 import**。绝大多数改动都能用「只动 1~2 个文件 + 跑 2~4 条 check（`selftest.py --only`）+ 不打包」完成。真正的坑不在依赖结构，而在 **① 子集跑红多半是「跨 check 状态依赖」而非产品 bug**、**② `_emit_block` 的三条下游吃三个不同版本文本**、**③ `QTimer.singleShot` 捕获控件不带 context 会在宿主析构后打 traceback**（§7 第 4 条）。

---

## 1. 分层与依赖方向（AST 实测）

### 1.1 结论先行

| 问题 | 结论 | 证据 |
|---|---|---|
| 有反向依赖（底层 import 上层）吗？ | **没有**。39 个模块的完整 import 图（含函数内延迟 import）是 DAG | `_check/_arch_graph.py` 输出 `环 = 无环` |
| 有循环 import 吗？ | **没有**，一个都没有 | 同上，DFS 三色标记扫全图 |
| `capability_api` 真的零 Qt 吗？ | **是**，顶层只 import `rx_export/soc_ekf/soc_est/soc_feed/version`；实测 `import capability_api` 后 `PySide6.QtWidgets not in sys.modules` | `capability_api.py:1-25`；实测输出 `qt_loaded=False` |
| 哪些模块能在无 GUI 环境裸 import？ | 见下表 ①②③ | 实测 19 个模块逐个 `import` 全部 OK |

### 1.2 实测层级（L0 = 最底层，无本地依赖）

```
L0  app_log  version  ui_theme  ui_anim  soc_est  rx_export  perf_mode
    serial_io  ble_io  marks_model  soc_display  wake_lock  _snap_worker
L1  customization  rx_history  rx_protocol  soc_feed  soc_ekf  soc_profiles
    ui_controls  ui_backdrop  ui_monitor  ui_perf_mode  dlc_host  drv_assist  mcp_bridge
L2  rx_store  ui_brand  ui_highlight  ui_surface  updater  capability_api
L3  splash_motion  vehicle_share
L4  splash_anim
L5  ui_settings
L6  main_window
L7  main  selftest
```

> 注：`customization` 在 L1（只依赖 QtCore 的 QSettings，且对 `ui_anim` 是 **:291 函数内延迟 import**，见 `customization.py:285-293` 的注释"customization 别把 ui_anim/QtWidgets 拉进来"）。这是全项目**唯一**为了"不被上层污染"而刻意做的延迟 import，别改成顶层。

### 1.3 谁 import 谁（两层深度文本树）

```
ui_theme (1164)            ← 10 个模块（底层，只依赖 QtGui）
├── ui_anim (614)          ← ui_controls, ui_surface, ui_perf_mode, main_window
│   └── ui_controls (2244) ← ui_brand, ui_settings, updater, main_window
│       └── ui_brand (506) ← splash_motion, main_window
├── ui_backdrop (162)      ← ui_surface
│   └── ui_surface (598)   ← main_window
├── customization (796)    ← ui_highlight, ui_settings, rx_store, soc_profiles, vehicle_share, main, main_window
├── ui_highlight (1305)    ← main_window            （依赖 app_log + customization + rx_protocol + ui_theme）
└── ui_settings (4305)     ← main_window            （依赖 app_log + customization + ui_controls + ui_theme）

soc_est (1223)             ← soc_feed, soc_ekf, soc_profiles, capability_api, main_window
├── soc_feed (363)         ← capability_api, main_window
├── soc_ekf (488)          ← capability_api, main_window（仅函数内）
└── soc_profiles (126)     ← main_window

app_log (128)              ← 9 个模块（日志，谁都能用，无反向）
version (40)               ← main, dlc_host, mcp_bridge, capability_api（+ 函数内 updater/vehicle_share/ui_settings/main_window）

rx_export (323)            ← rx_store, capability_api, main_window
rx_store (1046)            ← main_window
rx_protocol (253)          ← ui_highlight, main_window
mx_history (457)           ← main_window（**全部函数内 import**）
main_window (11260)        ← 无人 import（除 selftest/main 外的终点）
main (747) / selftest (8691)  ← 进程入口，无人 import
```

**重要观察**：`rx_history` 对 `main_window` 是**反向**的——`main_window` 在 6 处函数内 `import rx_history`（`:1838, :7480, :8291, :8533, :9207, :10377, :10723`）。这是因为 `rx_history` 顶层要在模块加载期就读 `custom/rx_history_dir`（`rx_history.py:92-94` 函数内 `QSettings`），而 `customization` 已在 L1，所以它可以被顶层 import —— 但项目仍选了函数内 import，属于历史保守写法，不影响正确性。

### 1.4 Qt 依赖精确分类（实测）

| 类别 | 模块 | 说明 |
|---|---|---|
| **纯 Python（零 Qt，可裸 import/单测）** | `soc_est`, `soc_feed`, `soc_ekf`, `soc_display`, `perf_mode`, `rx_export`, `rx_store`, `marks_model`, `wake_lock`, `app_log`, `version`, `drv_assist`, `dlc_host`, `capability_api`, `mcp_bridge` | 15 个。**`rx_history` 不算**（`:92-94` 函数内 `QSettings`、`:344` `QTimer`） |
| **只用 QtCore（QSettings/QTimer/QThread，无 QWidget）** | `customization`, `soc_profiles`, `rx_protocol`, `serial_io`, `ble_io`, `rx_history`, `dlc_host`(no Qt), `_snap_worker`(QtCore+QtGui/QImage) | 需 QApplication 存在才能跑，但不开窗 |
| **要 QWidget（必须 QApplication + 平台插件）** | `ui_theme`, `ui_anim`, `ui_controls`, `ui_backdrop`, `ui_surface`, `ui_brand`, `ui_highlight`, `ui_settings`, `ui_monitor`, `ui_perf_mode`, `splash_anim`, `splash_motion`, `updater`, `main_window`, `main` | 13 个 |
| **第三方非 Qt** | `serial_io` → `pyserial`；`splash_anim` → `numpy` | 全项目只有这两个非 Qt 三方依赖 |

> ⚠️ `rx_protocol.py` **不是**纯 Python（`QSettings` at 顶层 import 之后），`soc_profiles.py` 同理。任务书里猜的"rx_protocol 用了 QSettings"是**对的**。

---

## 2. 模块职责 + 关键入口符号 + 归属

| 模块 | 一句话职责 | 主入口（file:line） | 归属 |
|---|---|---|---|
| `main.py` 747 | 进程入口：命令行分派（`--check-net/--apply-update/--ble-scan/--mcp-payload/--install-drivers/--check-dlc/--check-splash`）+ 建 QApplication + 播开屏 + 建主窗 | `main():349`、`_build_window():582`、`_preload_main_module():538`、`_prewarm_font_engine():74` | 入口层 |
| `main_window.py` 11260 | 主窗（`MainWindow`，353 个方法）：**唯一编排层**，所有链路的汇聚点 | `MainWindow:953`、`__init__:967`、`_build_ui:1566`、`_build_config_panel:2156`、`_build_receive_area:2481`、`_on_received:7072`、`_emit_block:7355` | 编排层 |
| `ui_settings.py` 4335 | 设置面板 `SettingsDialog`：**只负责布局与控件生成**，按 `customization.specs()` 动态建行；**不直接写 QSettings**（写值交回主窗的 `_apply_settings_result`） | `SettingsDialog:1131`、`_build_custom_section:2915`、`custom_values():3310`、`_build_vehicle_section:1715` | UI 组件层（面板） |
| `ui_controls.py` 2244 | 自绘控件库：`InkButton/RowButton/IconButton/CaptionButton/AlertButton/Switch/GlassSlider/SearchEdit/ChevronComboBox/ChevronSpinBox/FlashLabel/PulseDot` | `InkButton:193`、`Switch:693`、`ChevronComboBox:1209`、`ChevronSpinBox:1923` | UI 组件层（控件） |
| `ui_theme.py` 1164 | 唯一真值源：调色板 `LIGHT/DARK`（:21/:59）、QSS（`_QSS:533`）、token 替换（`stylesheet():1075`）、字体（`ui_font:301`）、圆角/间距常量（:268-275）、材质参数（:165-245） | `apply_scheme:1099`、`token:441`、`qcolor:448` | 表现层（最底层） |
| `ui_anim.py` 614 | 补间引擎：`Tween`、`Reveal`、缓动函数表 `EASINGS:204`、帧率（`set_frame_hz:82`）、显示器刷新率探测 | `Tween:260`、`Reveal:457`、`out_cubic:111`、`in_out_quint:133` | 表现层 |
| `ui_surface.py` 598 | 材质绘制：`paint_glass:72`（分流毛玻璃/液态玻璃）、`Card:386`（卡片容器）、阴影 `card_shadow:266`、`fade_in:319` | `Card:386`、`paint_glass:72` | 表现层 |
| `ui_backdrop.py` 162 | 磨砂颗粒贴图生成（`frost_tile:100`）+ 背景模糊（`gaussian_blur:50`） | `frost_tile:100` | 表现层 |
| `ui_brand.py` 506 | 页头 `BrandHeader:256`（logo + 队标）+ 自绘标题栏 `CaptionBar:133`（三键） | `BrandHeader:256`、`CaptionBar:133`、`app_icon:116` | UI 组件层 |
| `ui_highlight.py` 1305 | 接收区**排版 + 着色**：纯函数为主（`align_indexes:737`、`gap_lines:955`、`module_group_plans:390`、`collapse_history_rows:991`、`repair_split_samples:316`）+ 语法高亮器 `RxHighlighter:1158` | `align_indexes:737`、`RxHighlighter:1158` | 数据→文本层（纯函数 90%） |
| `ui_monitor.py` 638 | 数据监控浮窗（`MonitorPanel:220` 是 `QDialog`+`Qt.Tool`，独立 `settings.json` 存几何） | `MonitorPanel:220`、`MonitorChart:103` | UI 组件层 |
| `ui_perf_mode.py` 244 | 性能模式的屏上提示浮层 `PerfModeOverlay:107`（暗门用） | `PerfModeOverlay:107` | UI 组件层 |
| `splash_anim.py` 1764 | 开屏动画播放器：`_SplashWindow:388`、`play():1618`、`play_configured():1654`、`configured():324`、预设扫描 | `play:1618`、`configured:324` | 表现层 |
| `splash_motion.py` 1026 | 开屏运动渲染（`MotionSpec:99`、`MotionRenderer:366`，planA/planB 两套） | `MotionRenderer:366` | 表现层 |
| `customization.py` 796 | **配置注册表 + 车辆档案 CRUD**（唯一写 QSettings 的配置模块） | `register_bool/int/slider/choice:43/49/57/82`、`get:98`、`set:132`、`specs:93`、`vehicles:585`、`active_profile:780`、`save_vehicle:663` | 数据层（配置） |
| `rx_store.py` 1046 | 历史数据 SQLite：append-only `frames` 表（`_SCHEMA_TMPL:79`）、后台写线程（`record_block:247`）、分页读（`history_page:499`）、导出（`export_csv`） | `record_block:247`、`configure:225`、`history_page:499`、`stats:309` | 数据层（历史） |
| `rx_history.py` 457 | 近期数据纯文本 `.log`：内存 deque + 1s 批量 flush（`FLUSH_MS:35`）、按车号分文件（`file_for:264`） | `feed:352`、`flush:374`、`load`、`set_max_lines:57`、`switch_target:305` | 数据层（近期） |
| `rx_export.py` 323 | 结构化导出 CSV/JSON：模块级单例 `Session`，`feed:110` 吃原始文本 | `Session:87`、`session():317` | 数据层（导出） |
| `rx_protocol.py` 253 | 协议规则字典（7 个正则/位数）+ 热切换 | `compile_profile`、`active`、`validate`、`save_profile` | 数据层（协议） |
| `soc_est.py` 1223 | SOC 算法：OCV 表（`OCV_TABLE:70`，62 点）、清洗（`clean_cells:636`）、`WorkModeClassifier:232`、`SocEstimator:824`、文案格式化（`format_soc_parts:1201`） | `SocEstimator.update:873`、`format_soc_parts:1201` | 数据层（算法） |
| `soc_feed.py` 363 | 报文 → 轮：`RoundCollector.feed:172` 解析 `VoltageN/TemperatureN/CVMax/BMS`，粘滞 `last_soc_read`/`last_v_read` | `RoundCollector:120`、`RoundData:99` | 数据层（解析） |
| `soc_profiles.py` 126 | 按车号取 SOC 参数（`custom/soc_packs`） | `cfg_for`、`save_pack`、`delete_pack` | 数据层（配置） |
| `soc_ekf.py` 488 | 可选四状态 EKF（默认关，`custom/soc_ekf4`） | `SocEKF4`、`EkfParams:--` | 数据层（算法） |
| `soc_display.py` 124 | 显示门控：母线断开期冻结 min/max（`SOC_DISPLAY_FREEZE_OPEN:44`） | `SocDisplayGate.decide:65` | 数据层（纯状态机） |
| `perf_mode.py` 330 | 暗门性能模式：文本整流（电压/温度越界替换、BMS 统一 OK），幂等 | `Sanitizer` | 数据层（纯文本） |
| `marks_model.py` 143 | 整轮标记内存模型（**不持久化**）：三层锚 `stamp → content_sig → stamp_rev` | `MarkStore:55`、`RoundMark:38` | 数据层（内存） |
| `wake_lock.py` 107 | 防息屏（`SetThreadExecutionState`）幂等状态机，可注入 fake setter | `WakeLock`、`default_os_setter` | 工具层 |
| `serial_io.py` 244 | 串口：`SerialWorker:127`（QThread）、`StreamDecoder:100`（增量解码）、`list_serial_ports:69`、HEX 编解码、校验位归一 | `SerialWorker:127`、`StreamDecoder:100` | 数据层（IO） |
| `ble_io.py` 182 | BLE 广播扫描 worker（只扫描不配对） | `BleBroadcastWorker:54` | 数据层（IO） |
| `app_log.py` 128 | 分级日志（滚动 1MB×3，程序旁/用户目录降级） | `init`、`debug/info/warning/error` | 工具层 |
| `version.py` 40 | 版本号与 SHA256 唯一出处 | `VERSION:8` | 工具层 |
| `updater.py` 870 | 自动更新：版本源链（jsDelivr→镜像→直连）、`UpdateChecker:321`、`UpdateDialog:586`、完整性校验、静默替换 | `UpdateChecker:321`、`version_sources:101`、`verify_integrity:287` | 工具层 |
| `vehicle_share.py` 1093 | 车辆档案分享：`.cqv.json` 导出/导入/在线库（GitHub contents API + 镜像竞争）、token DPAPI 加密 | `export_to_file:256`、`import_from_file:352`、`fetch_remote_index` | 数据层（网络） |
| `capability_api.py` 588 | 只读能力门面（给 MCP）：5 个零 Qt adapter + per-adapter try/except + JSON 可序列化保证 | `--list`/`--call` CLI（模块尾） | 工具层（对外 API） |
| `mcp_bridge.py` 243 | MCP 接入包生成（把 `mcp/serial_assistant_mcp.py` 源码拼成自包含指令） | `server_source`、`deploy_payload` | 工具层 |
| `dlc_host.py` 551 | 可选 DLC 插件宿主：发现/校验/加载，不碰 QSettings | `discover:166`、`load:471`、`HostContext:493` | 工具层（插件） |
| `drv_assist.py` 506 | USB 转串口驱动体检 + 提权安装（pnputil） | `needed_keys:262`、`install:363` | 工具层（系统） |
| `_snap_worker.py` 180 | 后台 BitBlt 抓窗口快照（160ms 周期，零主线程阻塞），最大化/还原覆盖层用 | `SnapWorker:147`、`_capture_window_qimage:39` | 工具层（性能） |
| **`selftest.py` 8691** | 41 条无硬件自测（offscreen + loop:// 回环），带全套 QSettings/历史/会话库隔离；**支持 `--only/--skip/--changed/--list`** | `main():8479`、`CHECKS:7944`、`_parse_args:8271` | 测试层 |

### 2.1 四对最容易搞混的边界（务必记住）

| 易混对 | 正确切分 | 判据（改代码时问自己） |
|---|---|---|
| `customization.py` vs `ui_settings.py` | customization = **注册表 + 读写 QSettings + 车辆档案 CRUD**；ui_settings = **只管面板布局与控件** | 我要"加一个配置项/改默认值/改范围"→ customization；我要"这一行摆哪、框多宽、标签写什么"→ ui_settings。⛔ ui_settings **不直接写** QSettings，只把 `custom_values()` 交回主窗 |
| `rx_history.py` vs `rx_store.py` | rx_history = **近期数据**，纯文本 `近期数据\<车号>.log`，内存 deque + 1s flush，跟随接收区行数、有上限；rx_store = **历史数据**，SQLite `历史数据\<车号>.db`，append-only 永不删、按 interval 分桶 | 我要"存多久/写多快/文件名"→ rx_history；我要"表结构/分页/统计/CSV 全量导出"→ rx_store |
| `rx_export.py` vs `rx_protocol.py` | rx_export = **输出格式**（CSV 宽表 / JSON 嵌套），消费已解析的轮；rx_protocol = **输入规则**（怎么从文本里认出标题/采样点/错误字样），影响 `ui_highlight` 与 `soc_feed` 的正则 | 我要"多导一列/换个 JSON 结构"→ rx_export；我要"接一台标题写法不同的 BMS"→ rx_protocol（且必须同时检查 ui_highlight 的 `_LivePattern`，见 :54-93） |
| `ui_highlight.py` vs `main_window.py` | ui_highlight = **纯函数排版/着色规则**（可脱离窗口单测）；main_window = **谁在什么时候调它**（维护跨块段状态 `_align_section`、`_rx_prev_line`、`_rx_half_sample`） | 我要"补零几位/每排几个点/断线插 X"→ ui_highlight；我要"什么时候重排/状态怎么跨块传"→ main_window |
| `main.py` vs `main_window.py` | main.py = **进程入口**（argv 分派、QApplication、开屏、预热、异常兜底）；main_window.py = **主窗**（构造后的一切） | 我要"加一个命令行参数/改启动顺序/改开屏时机"→ main.py；其它全在 main_window |

### 2.2 任务书里漏掉的 5 个小模块

- `soc_display.py`（124）：母线断开期 min/max 完全冻结的纯状态机，**不改 SocResult**，只改显示文本 → `main_window._apply_soc:9384` 用它。
- `marks_model.py`（143）：整轮标记的**内存模型**（不持久化、三套回放共用一套），`MarkStore.add_unique` 判重键 `(stamp, content_sig)`。
- `wake_lock.py`（107）：防息屏，**每线程**语义（必须在主线程 acquire/release），注入式 `os_setter` 让 selftest 不真改电源策略。
- `_snap_worker.py`（180）：后台 BitBlt 抓窗口，**桌面 DC 而非 GetWindowDC**（后者对 DWM 合成窗口是纯黑），`[B,G,R,X]` 与 `Format_RGB32` 一致**绝不能 rgbSwapped**。
- `app_log.py`（128）：分级日志，写不进去就静默降级，**日志永远不能影响功能**。

---

## 3. 改动半径表（核心）

> 图例：**必跑** 列里用的是 `CHECKS` 中的**中文字符串**（可直接用于 `--only` 过滤，见 §6.2）。"打包"列：`否` = 只需源码验证；`是` = 必须重打包才能给用户（用户可见的行为/外观/文案变化都要打包才生效）。

| # | 改动意图 | 只该动 | 会连带影响 | 必跑 check | 打包 |
|---|---|---|---|---|---|
| 1 | 改设置项**文案**（label / tip） | `customization.py` 的 `register_*` 调用（:187-410，共 23 处）；`tip=` 参数同处 | 只影响面板显示。⚠️ 若改的是 `_MERGE_GROUPS` 的行名（`ui_settings.py:1577-1612`）则同时影响合并行标签 | `定制功能注册表`、`提示字号与按钮显隐` | 是 |
| 2 | 改设置项**取值范围/默认值** | `customization.py` 对应 `register_*` 的 `minimum/maximum/default` | 若改 `default` 且老用户已存过该键 → 需在 `_LEGACY_DEFAULTS`（:29）登记旧值 + `migrate_defaults():157` 才会迁移；否则老用户不生效 | `定制功能注册表`、`校验位/保留行数/时钟/发送区一排` | 是 |
| 3 | **新增一个设置项** | ① `customization.py` 加一行 `register_*`（并归组）② `ui_settings.py:_CUSTOM_GROUPS:1533` 加 key ③ 消费点在 `main_window._apply_settings_result:10222` 加分支（若需即时生效） | 若控件是下拉/数字框，宽度会按 §2.1 的 `COMBO_W/SPIN_*` 自动；若是 bool，`_assert_switch_hit_geometry` 会检查命中区 | `定制功能注册表`、`提示字号与按钮显隐` + 若有消费点则跑对应链路 check | 是 |
| 4 | 调整设置面板**行顺序/分组/合并两行** | `ui_settings.py:_CUSTOM_GROUPS:1533-1569`（组内顺序）、`_MERGE_GROUPS:1577-1612`（合并）、`_MERGE_PREFIXES:1617-1630`（小字）、`form.setVerticalSpacing(8):1196` | ⛔ 合并行的小标签必须 `Fixed`+`Preferred` 否则吃光余量顶出 670px 空白缝；⛔ `addStretch(1)` 必须在所有 spec 迭代后统一加 | `定制功能注册表`、`提示字号与按钮显隐`、`连接方式切换几何不跳` | 是 |
| 5 | 改设置面板**控件宽度** | `ui_settings.py:56 SPIN_COLUMN_W / :65 SPIN_COMPACT_W / :86 COMBO_W / :93 SHORT_CHOICE_W / :99 GLASS_ROW_W / :112 MODULE_COUNT_W / :71 MODULE_INPUT_W` | 只影响面板，不影响主窗（主窗有自己的一档 `CONFIG_COMBO_MAX_W:262`） | `定制功能注册表`、`提示字号与按钮显隐` | 是 |
| 6 | 改**主题配色 / QSS token** | `ui_theme.py`：`LIGHT:21`/`DARK:59`（色值）、`_QSS:533`（样式）、`stylesheet():1075` 的 token 表（**漏一个 token 全静默失效**） | 换主题时 `main_window._apply_theme:1501` 会全树 unpolish/polish（**145ms**，别省）；`_rx_highlighter.refresh()` 也在那里 | `QSS token 全量可替换`、`浅色纯白背景与磨砂颗粒`、`毛玻璃强度与箭头控件`、`定制功能注册表` | 是 |
| 7 | 改**材质效果**（毛玻璃/液态玻璃） | `ui_theme.py:165-245`（`set_material/material/liquid_*`/`frost_gain`）、`FROST_GRAIN:235`、`GLASS_ALPHA_RANGE:115`、`LIQUID_*:180-200`；绘制实现在 `ui_surface.py:paint_glass:72` | `main_window._apply_glass:10660` / `_apply_material:10676` 只 `update()`，不重建；换材质/强度都是绘制时现取 → **不重启** | `毛玻璃强度与箭头控件`、`浅色纯白背景与磨砂颗粒` | 是 |
| 8 | 改**动画曲线/时长** | `ui_anim.py:EASINGS:204`（曲线表）、各调用点的 `Tween(duration=...)`；主窗常量：`TX_ANIM_MS:151`、`_SETTINGS_ANIM_MS:394`、`_SETTINGS_SWAP_DELAY_MS:401`、`RX_COALESCE_MS:157`、`RX_SCROLL_MS_SLOW/FAST:165-166` | ⛔ 帧率 `anim_fps` 与时长**解耦**（补间按真实时间算进度），改 `set_frame_hz` 不影响时长 | `动画引擎 120fps 与非线性` + 改哪条动画就跑对应业务 check | 是 |
| 9 | 改**接收区排版**（每模组 N 排 / 对齐 / 补零） | `ui_highlight.py`：`align_indexes:737`（总入口）、`module_group_plans:390`（每排点数）、`grid_row_sizes:433`（行数分配）、`_cap_rows:508`（视口封顶）、`_pad_line:266`（补零）、`INDEX_PAD_WIDTH:196`、`CELL_GAP:202`、`WIRE_BREAK:210` | 调用点：`main_window._emit_block:7421`（实时）、`_relayout_rx_document:7235`（改设置后重排已显示）、`_replay_text_for:8674`（回放）。⛔ 每排宽度必须按**档案点数**算，用本块实际点数会 4+4 排成 2+2+2+2 | `显示行数（每模组 N 排）`、`收包合并与暂停继续`、`历史数据回放`、`车辆与逐模组配置` | 是 |
| 10 | 改**高亮配色**（序号/时间戳/越界/BMS） | `ui_highlight.py:_recolor:1184-1203`（取值点）；色值本身在 `ui_theme.py`：`RX_ACCENT:391`、`STAMP_ACCENT:392`、`volt_high/volt_low/bms_error/temp_warn/wire_break`（`LIGHT:21`/`DARK:59` 里） | 改色后 `RxHighlighter.refresh()`（由 `_apply_settings_result:10287` 触发）；⛔ `highlight_errors` 开关关掉后只留时间戳紫 + 序号蓝 | `回车发送/时间戳轮首/序号染蓝`、`收包合并与暂停继续`、`QSS token 全量可替换` | 是 |
| 11 | 改**SOC 算法**（OCV 表/清洗/安时积分） | `soc_est.py`：`OCV_TABLE:70`、`clean_cells:636`、`SocEstimator.update:873`、`I_LIMIT_A:353`、`EXTREME_GAP_MV:172`、`run_min` 清洗参数 | ⚠️ **OCR 表有两份副本**：`soc_est.py:70` 与 `devtools/_verify_soc.py` / `_verify_soc_ekf.py` 的断言表，改一处必须同步（MEMORY.md:41-43 的"修 2 条测试硬编码并同步所有表副本"） | `v2.11 四功能`（工况分类）、`定制功能注册表`（若改 soc_source/volt_source 选项文案） | 是 |
| 12 | 改**SOC 显示文案/来源开关** | 文案：`soc_est.format_soc_parts:1201`、`main_window._soc_placeholder:9455`、`_soc_read_parts:9435`；开关：`customization.py:385 soc_source / :393 volt_source`、`main_window._load_soc_source:9484 / _set_soc_source:9492 / _load_volt_source:9465 / _set_volt_source:9476`、`_render_soc:9403`（**顺序铁律：先 SOC 来源再电压来源**） | ⛔ 文案不许出现「车端」「BMS」（`check_v211_features` 硬断言）；⛔ 底栏标签摆位 `_place_soc:9547` | `v2.11 四功能（电压来源/工况/防息屏/标记）`、`定制功能注册表` | 是 |
| 13 | 改**串口层**（波特率/校验位/开关） | `serial_io.py:21 BAUD_PRESETS / :29 PARITY_CHOICES / :34 PARITY_LABELS / :43 parity_key`；主窗 `_open_port:6662`（构造 worker）、`_close_port:6834`、`_poll_ports:6606`、`_on_port_opened:6726` | ⛔ 建链异步（worker 线程 open），`_open_port` 只发起；`_config_widgets:6888` 在建链期禁用 | `串口建链异步化（不卡界面/可取消）`、`loop:// 收发回环`、`串口枚举`、`校验位/保留行数/时钟/发送区一排` | 是 |
| 14 | 改**连接方式切换**（串口↔蓝牙） | `main_window._on_link_mode_changed:6897`（换标签/清空/播过渡）、`_refresh_config_visibility:10415`（**唯一显隐出口**）、`_apply_config_visibility:10470`（先藏后显）、`_config_blur_zone:10530`、`_play_config_blur_swap:10564`；`ble_io.py` | ⛔⛔ 不许改窗口最小高（`check_link_switch_stable` 第 40 条守）；⛔ 蓝牙扫描收尾必须是**可取消 QTimer** `_ble_scan_timer`（`QTimer.singleShot` 撤不掉，切回串口 3.5s 后会把 MAC 填进串口框） | `连接方式切换几何不跳`、`蓝牙广播接收（只扫描不配对）`、`串口插拔扫描与电源开关按钮` | 是 |
| 15 | 改**近期数据落盘**（文件名/保留行数/写入频率） | `rx_history.py:35 FLUSH_MS / :36 SUB_DIR / :264 file_for / :280 _claim_legacy / :57 set_max_lines / :374 flush`；行数来源：`main_window._on_rx_lines_changed:9196` → `rx_history.set_max_lines` | ⛔ 新文件名不许用「接收历史*」前缀（会和老迁移逻辑撞）；⛔ `labels_of()` 新旧两处都要扫 + 新家优先（:181） | `历史接收数据`、`接收历史目录可切换`、`校验位/保留行数/时钟/发送区一排` | 是 |
| 16 | 改**历史数据 SQLite**（表结构/采样间隔/分页） | `rx_store.py:79 _SCHEMA_TMPL`（表 + 索引 + `PRAGMA user_version=1`）、`record_block:247`、`history_page:499`、`_bucket_stream:354`、`configure:225`；采样间隔在 `customization.py:408 history_interval` | 改表结构必须写迁移（`_migrate_lock`/`.migrating`/`os.replace`）；⛔ `history_page` 的 limit 单位是**轮**不是行；⛔ 走 vehicles 多库 k 路归并 | `会话数据库（SQLite）`、`回放分页按需加载`、`历史数据回放` | 是 |
| 17 | 改**历史/近期回放**交互（按钮文案/分页/按需加载） | 文案：`main_window._refresh_replay_ui:8858`（**六个文案都是 6 字，切换时按钮宽不变**）；分页：`REPLAY_PAGE_LINES:125 / REPLAY_PAGE_POINTS:126 / REPLAY_PAGE_MAX_LINES:133 / REPLAY_PREFETCH_PX:131 / REPLAY_MAX_BLOCKS:137`；按需加载：`_maybe_replay_load_earlier:8756`、`_replay_prepend:8784`、`_replay_load_earlier_recent:8808 / _long:8827` | `_enter_replay:8689`（三步顺序刻意：抓快照→改文本→起动画）、`_exit_replay:8738`（`setMaximumBlockCount` 收回）、`_replay_text_for:8616`（8 份缓存） | `历史数据回放`、`回放分页按需加载` | 是 |
| 18 | 改**车辆档案**（增删车/逐模组/导入导出） | 数据：`customization.py:585 vehicles / :654 select_vehicle / :663 save_vehicle / :686 set_vehicle_rows / :709 delete_vehicle / :757 rename_vehicle / :780 active_profile / :722 active_row_hints`；面板：`ui_settings.py:1715 _build_vehicle_section / :2123 _on_rows_hint_changed / :2079 _sync_row_hint_widgets`；分享：`vehicle_share.py:256 export_to_file / :352 import_from_file / :490 VehicleLibraryDialog（在 ui_settings.py:490）` | 主窗消费：`_rx_grid_hints:7150`、`_schedule_vehicle_relayout:7192`、`_refresh_vehicle_badge:7212`、`_init_soc:9262`（换车=重算 SOC） | `车辆与逐模组配置`、`显示行数（每模组 N 排）`、`车辆档案导出导入与令牌` | 是 |
| 19 | 改**标记功能**（MarkStore） | `marks_model.py:55 MarkStore / :63 add / :96 add_unique / :109 clear`；主窗：`_round_content_sig:7910`、`_mark_current:7929`、`_refresh_mark_list:7963`、`_show_marks_dialog:7992`、`_goto_mark:8074`、`_jump_to_mark:8082`、`_clear_mark_highlight:8138` | ⛔ 判重键 `(stamp, content_sig)`，别退回 `start_block`/块号；⛔ `_clear_receive(clear_marks=True)` 是唯一清零口（keyword-only 防误绑） | `v2.11 四功能（电压来源/工况/防息屏/标记）`、`历史数据回放` | 是 |
| 20 | 改**开屏动画/启动耗时** | 时间轴：`splash_anim.py:78 _FADE_MS`、`configured():324`；运动：`splash_motion.py:99 MotionSpec / :366 MotionRenderer`；入口：`main.py:529 _preload_main_module / :547 play_configured / :74 _prewarm_font_engine` | ⛔⛔ 字体预热必须与 splash import 重叠，挪到首帧后 2.2s→3.9s；onefile 解包固定 ~2.4s 改不了 | 无 selftest 覆盖（靠 `--check-splash` + `splash_perf.log` 的 `big_gaps`） | 是 |
| 21 | 改**自动更新/版本号** | `version.py:7 VERSION`（**唯一出处**，升版必须先清空 `LATEST_EXE_SHA256`/`LATEST_SETUP_SHA256`）；`updater.py:101 version_sources`（源链）、`:321 UpdateChecker`、`:586 UpdateDialog`、`:287 verify_integrity` | `main.py:112 _run_apply_update / :144 _run_check_net`；`ui_settings` 的检查更新按钮 | `自动更新模块` + `改名无残留与安装版判定` | 是（且需发 Release） |
| 22 | 改**界面按钮显隐** | `main_window.py:334 BUTTON_SPECS`（key 必须 = 属性名）、`:369 _CONFIG_VIS_KEYS`、`:376 _LEGACY_HIDDEN_EXPAND`、`_apply_button_visibility:10390`、`_refresh_config_visibility:10415`；面板侧 `ui_settings._build_button_section:4122` | ⛔ 「串口开关」「连接方式」**故意不在表里**（藏了锁死用户） | `提示字号与按钮显隐`、`连接方式切换几何不跳` | 是 |
| 23 | 改**主窗布局**（分屏紧凑档） | `main_window.py:COMPACT_L1_W/L2_W/L3_W:210-212 / COMPACT_HYST:213 / COMPACT_SPECS:223-247`、`_compact_level_for:4383`、`_apply_compact:4655`、`_set_opts_vertical:4583` | `resizeEvent:4346` → `_sync_compact:4403` | `自绘标题栏三键`、`连接方式切换几何不跳`、`提示字号与按钮显隐` | 是 |
| 24 | 改**收包合并/暂停** | `main_window.py:RX_MERGE_MS:268 / RX_MERGE_MAX_BYTES:270 / RX_MERGE_MAX_MS:273 / RX_HOLD_MAX_MS:277`；`_on_received:7072`、`_flush_rx:7094`、`_toggle_pause:2818`、`_flush_paused_frames:7643` | `_append_display:7495` 的合并（`RX_COALESCE_MS:157`）是**动画期间**的另一套，别混 | `收包合并与暂停继续`、`界面端到端收发` | 是 |

> **未列出但同类的**：改 `DEFAULT_RX_BLOCKS:114` / `RX_LINES_MIN/MAX:115` / `RX_LINE_PRESETS:117` → `校验位/保留行数/时钟/发送区一排`；改 `_stamp`:7667 / `_stamp_needed:7698` / `RX_STAMP_ANCHOR` → `回车发送/时间戳轮首/序号染蓝`；改 `PORT_WATCH_MS:196` → 无 check（只影响轮询频率）。

---

## 4. selftest 41 条 check 覆盖矩阵

`CHECKS` 列表在 `selftest.py:7944-7990`（41 条）。`main():8479` 现在**有完整的选集参数**（2026-11-27 新增，未提交）：`--list` / `--only <关键词|序号>` / `--skip` / `--changed`，见 §6.2。

⚠️ **本文行号以当前工作区为准**：`selftest.py` 比 HEAD 多 360 行（选集功能），`ui_settings.py` 多 40 行（合并行注释）。§4.1 下表行号已按当前工作区重算。

### 4.1 逐条矩阵

| # | check 名（CHECKS 字符串） | 函数:行 | 主要验证 | 只改 X 时必跑？ |
|---|---|---|---|---|
| 1 | v2.11 四功能（电压来源/工况/防息屏/标记） | `check_v211_features:7817` | soc_feed 粘滞 / soc_est 工况分类 / wake_lock 状态机 / MarkStore（**纯逻辑，不建窗**） | 改 SOC 来源、工况、防息屏、标记 → **是** |
| 2 | HEX 编解码 | `check_hex_codec:419` | `serial_io.parse_hex/to_hex` | 改 HEX → 是 |
| 3 | 增量文本解码 | `check_stream_decoder:435` | `serial_io.StreamDecoder` | 改解码 → 是 |
| 4 | 串口枚举 | `check_port_enumeration:448` | `serial_io.list_serial_ports` | 改端口枚举 → 是 |
| 5 | 串口插拔扫描与电源开关按钮 | `check_port_hotplug:492` | 插拔只发现不自动打开；`btn_open` 可点 | 改 `_poll_ports`/`btn_open` → 是 |
| 6 | 串口建链异步化（不卡界面/可取消） | `check_open_async:223` | `_open_port` 立即返回、可 `_close_port` 取消 | 改建链 → 是 |
| 7 | 蓝牙广播接收（只扫描不配对） | `check_ble_broadcast:306` | `ble_io` 扫描 + 广播负载 | 改 BLE → 是 |
| 8 | 回车发送/时间戳轮首/序号染蓝 | `check_send_enter_and_stamp:5225` | 回车发送、`_stamp_needed` 只打轮首、`RxHighlighter` 染色 | 改时间戳/高亮/发送 → 是 |
| 9 | loop:// 收发回环 | `check_loopback:459` | 纯通信层（不开窗） | 改 `serial_io` 读循环 → 是 |
| 10 | 界面端到端收发 | `check_ui_roundtrip:623` | 主窗 + 文本/定时/HEX 收发 | 改收发链路 → 是 |
| 11 | 毛玻璃强度与箭头控件 | `check_glass_controls:727` | `GlassSlider` 拖动真改变效果 | 改材质/滑杆 → 是 |
| 12 | 浅色纯白背景与磨砂颗粒 | `check_light_pure_white:799` | 两主题纯色 + 颗粒糊过 | 改主题/颗粒 → 是 |
| 13 | 接收区搜索 | `check_receive_search:940` | 搜索命中/跳转/计数 | 改搜索 → 是 |
| 14 | 全屏展开与复制全部 | `check_expand_and_copy_all:2692` | `_expand_receive` / `_collapse_receive` / `btn_copy_all`（**最慢的一条，46 个 wait_for**） | 改全屏/展开/复制 → 是 |
| 15 | 定制功能注册表 | `check_customization:3282` | 注册表 → 面板动态生成、滑杆、字号预览、弹层动画 | 改 settings 任何一项 → **几乎必跑** |
| 16 | 校验位/保留行数/时钟/发送区一排 | `check_config_parity_clock:4179` | 校验位写法、`rx_blocks=5000`、时钟、发送区一行 | 改串口参数/保留行数/时钟 → 是 |
| 17 | 自动更新模块 | `check_updater:1866` | 源链解析、完整性校验、弹窗（**全离线，用 fake NAM**） | 改 updater → 是 |
| 18 | 结构化导出（CSV/JSON） | `check_export_data:6572` | `rx_export` 数值与分模组逐项 | 改导出 → 是 |
| 19 | MCP 手动填写参数 | `check_mcp_manual:6675` | `mcp_bridge` 参数真实性 | 改 MCP → 是 |
| 20 | 改名无残留与安装版判定 | `check_rename_and_install:6739` | 无旧名残留 + `updater.is_installed` | 改产品名/安装判定 → 是 |
| 21 | QSS token 全量可替换 | `check_qss_tokens:6794` | `ui_theme.stylesheet()` 全部 token 都能替换 | **改 QSS/加 token → 必跑** |
| 22 | 车辆档案导出导入与令牌 | `check_vehicle_share:6821` | `vehicle_share` 导出/导入/DPAPI 令牌（离线） | 改车辆分享 → 是 |
| 23 | 接收历史目录可切换 | `check_history_dir:6916` | `rx_history.set_directory` 且旧文件不搬 | 改近期数据目录 → 是 |
| 24 | 会话数据库（SQLite） | `check_session_store:6989` | 建表/入库/统计/导出/主窗挂钩/复位 | 改 `rx_store` → 是 |
| 25 | 协议脚本化（规则校验/试跑/热切换） | `check_protocol_rules:7278` | `rx_protocol` 校验 + 热切换 | 改协议 → 是 |
| 26 | MCP 接入入口 | `check_mcp_payload:2193` | 源码随包走 + 接入包自包含 | 改 MCP → 是 |
| 27 | 安装包脚本 | `check_installer_script:2238` | Inno Setup 关键约定 | 改打包脚本 → 是 |
| 28 | 历史接收数据 | `check_rx_history:1266` | 入队/行数跟随/批量落盘 | 改近期数据 → 是 |
| 29 | 历史数据回放 | `check_history_replay:1461` | 回放格式/着色/标记/欠压 | 改回放 → 是 |
| 30 | 回放分页按需加载 | `check_replay_paging:4510` | 只灌最后一页 + 滚顶翻页 | 改分页 → 是 |
| 31 | 收包合并与暂停继续 | `check_rx_merge_and_pause:4596` | 合并窗口/暂停冻结/恢复 | 改 `_on_received`/暂停 → 是 |
| 32 | 动画引擎 120fps 与非线性 | `check_animation_engine:1051` | ticker 帧率 + 曲线非线性 + 静止无计时器 | 改 `ui_anim` → 是 |
| 33 | 提示字号与按钮显隐 | `check_tooltip_and_buttons:2310` | tooltip 字号 + `BUTTON_SPECS` 显隐 | 改按钮显隐/tooltip → 是 |
| 34 | 自绘标题栏三键 | `check_caption_bar:5690` | 三键在任何状态都在、位置不变 | 改 `CaptionBar`/窗口状态 → 是 |
| 35 | 启动时最大化开关 | `check_startup_state:6068` | `startup_maximized` 开/关起手姿态 | 改启动/全屏 → 是 |
| 36 | 默认折叠发送区开关 | `check_send_collapsed_startup:6002` | `send_collapsed_startup` 生效 | 改发送区折叠 → 是 |
| 37 | 定位最近异常按钮 | `check_alert_locate:6170` | `_locate_last_error` 跳最近越界/BMS | 改定位/高亮阈值 → 是 |
| 38 | 车辆与逐模组配置 | `check_vehicle_profiles:6339` | 档案读写/改名/重名/删当前车/逐排排版 | 改车辆 → 是 |
| 39 | 显示行数（每模组 N 排） | `check_rows_hint:7367` | `module_group_plans` 严格 N 排 + 视口封顶 | **改排版 → 必跑** |
| 40 | 连接方式切换几何不跳 | `check_link_switch_stable:7598` | 窗口高/卡高/发送区 y 全程不乱 | 改显隐/配置卡 → 是 |
| 41 | 数据监控面板（独立窗口/样本/回看） | `check_monitor_panel:7694` | `MonitorPanel` 独立窗口 + 图表 + 落盘 | 改监控 → 是 |

### 4.2 「改动类型 → 必跑 check 名」速查（可直接复制做 --only）

```
改 settings 面板（任何一项）  → 定制功能注册表 | 提示字号与按钮显隐
改 QSS / token                → QSS token 全量可替换 | 浅色纯白背景与磨砂颗粒
改主题/材质                   → 毛玻璃强度与箭头控件 | 浅色纯白背景与磨砂颗粒 | QSS token 全量可替换
改动画引擎                    → 动画引擎 120fps 与非线性
改接收区排版/补零             → 显示行数（每模组 N 排）| 收包合并与暂停继续 | 历史数据回放
改高亮/阈值                   → 回车发送/时间戳轮首/序号染蓝 | 收包合并与暂停继续 | 定位最近异常按钮
改 SOC 算法/来源              → v2.11 四功能 | 定制功能注册表
改串口层                      → 串口建链异步化 | loop:// 收发回环 | 串口枚举
改连接方式                    → 连接方式切换几何不跳 | 蓝牙广播接收
改近期数据                    → 历史接收数据 | 接收历史目录可切换
改历史 SQLite/分页            → 会话数据库 | 回放分页按需加载 | 历史数据回放
改回放交互                    → 历史数据回放 | 回放分页按需加载
改车辆档案                    → 车辆与逐模组配置 | 显示行数 | 车辆档案导出导入与令牌
改标记                        → v2.11 四功能 | 历史数据回放
改开屏/启动                   → （无 selftest，只能靠 --check-splash + splash_perf.log，见 §6.3）
改版本号/更新                 → 自动更新模块 | 改名无残留与安装版判定
改按钮显隐                    → 提示字号与按钮显隐 | 连接方式切换几何不跳
改紧凑档/分屏                 → 自绘标题栏三键 | 连接方式切换几何不跳
改收包合并/暂停               → 收包合并与暂停继续 | 界面端到端收发
```

> 💡 上面每条都能直接抄成 `selftest.py --only "<关键词>"`（关键词是**子串匹配**，括号里的补充说明可以省掉，更短更好敲）。
> 例：`--only "显示行数"` 命中「显示行数（每模组 N 排）」；`--only "v2.11"` 命中「v2.11 四功能…」。
> 想确认某关键词命中了哪些条 → 先 `--only "<关键词>" --list` 不行（`--list` 优先返回全量），改看 `--changed` 打印的「按改动选中 N 条」，或者直接 `grep -n "\"关键词\"" selftest.py`。

### 4.3 特别慢的 check（AST 实测每条函数体内的等待/建窗调用数）

| check | 慢在哪（AST 实测） | 估量 |
|---|---|---|
| `check_expand_and_copy_all:2692` | **46 个 `wait_for`（timeout 累加 131s）+ 16 个 `processEvents` + 1 个 `sleep`**；内部还有重复展开/收起循环 | **最慢**，正常 10~20s，异常能拖到分钟级 |
| `check_caption_bar:5690` | 9 个 `wait_for`（累加 25s）+ 17 `processEvents` + **2 个 `make_window`** | 次慢 |
| `check_updater:1866` | 4 个 `wait_for`（累加 23s，其中 6.0×3 + 5.0×1）+ 1 个 `SettingsDialog`；全走 fake NAM，**不真联网** | 中等偏慢 |
| `check_ble_broadcast:306` | 7 个 `wait_for`（累加 21s）+ 真实 BLE 扫描（`BLE_SCAN_SECONDS=3.5`） | 慢且**依赖本机蓝牙硬件** |
| `check_startup_state:6068` | 4 个 `wait_for`（累加 12s）+ 1 窗 + 1 SD | 慢 |
| `check_config_parity_clock:4179` | **5 个 `make_window`**（23 次建窗里它独占 5 次，每窗构造 ~1s） | 慢 |
| `check_customization:3282` | 1 窗 + **5 个 `SettingsDialog`** + 16 `processEvents`（弹层动画） | 慢 |
| `check_tooltip_and_buttons:2310` | 2 窗 + 1 SD + 17 `processEvents` | 慢 |
| `check_send_collapsed_startup:6002` | 2 窗 + 1 SD + 1 `sleep`（等 165ms 延迟落盘） | 慢 |
| `check_port_hotplug:492` | 4 个 `wait_for`（累加 12s） | 中偏慢 |
| `check_open_async:223` | 假串口 `open()` 里 `time.sleep(1.2)` + `read()` 每次 `sleep(0.03)` | 固定 1.2s+ |
| `check_ui_roundtrip:623` | 两个 `time.sleep(0.3)`（等定时发送收尾）+ 10 个 `wait_for`（累加 10s） | 固定 +0.6s |

**41 条合计建 23 个 MainWindow**（`make_window` 调用计数），单这一项就 ~20s+。

**每条 check 之后**还有统一的 `_close_top_windows(app, wait_ms=220)`（`main():8613`），41 × 0.22s ≈ **9s 固定开销**，收尾再 `wait_ms=500` 一次（`:8634`）。

### 4.4 全局前提敏感项（`main()` 里被钉死的）

`main():8479` 在 CHECKS 循环（`:8597`）**之前**钉了这些（都在 `global_snapshot` 之后，收尾无条件还原）：

| 前提 | 值 | 钉的行 | 影响的 check（不钉会假挂） |
|---|---|---|---|
| `custom/module_count` / `cells_per_module` / `temps_per_module` / `volt_rows` / `temp_rows` | `0` | `:8533-8535` | 「回车发送/时间戳轮首/序号染蓝」「收包合并与暂停继续」「定位最近异常按钮」 |
| `custom/pad_zero` | `True` | `:8546` | 「收包合并与暂停继续」（关掉后值前补空格，`" 1.3820"` 不匹配 `"01.3820"`） |
| 顶层 `rx_blocks` | `DEFAULT_RX_BLOCKS=5000` | `:8547` | 「校验位/保留行数/时钟/发送区一排」（断言 `currentText()==5000`） |
| `custom/highlight_errors` | `True` | `:8556` | 「历史数据回放」「收包合并与暂停继续」「回车发送/…」 |
| `custom/vehicle` + `vehicles/index.json.current` | `""` | `:8564-8565`（`select_vehicle("")`） | 所有依赖"没选车=自动排版"的用例 |
| `link_mode` 原值 | 快照 | `:8573-8574` | 收尾还原（否则用户 ble 配置被改成 serial） |
| `rx_history._resolve_dir` | 临时目录 | `:8591-8593` | 所有建窗并 `_emit_block` 的用例（防污染真实近期数据） |
| `rx_store` 库路径 | 临时库 | `:8591-8593` | 同上（防污染 sessions.db） |
| 每条 check 后 `rx_blocks` 重钉 | `5000` | `_reset_pinned_state():147` ← 循环内调用 | 「校验位/保留行数/…」（`_on_rx_lines_changed` 把 30/300 夹到 500） |

**未在 `main()` 里钉、但各 check 自己钉的**：`check_rx_merge_and_pause:4616`、`check_send_enter_and_stamp:5241`、`check_alert_locate:6185`、`check_vehicle_profiles:6377` 各自调 `pin_rx_grid_defaults()`（定义在 `:90`）。

**`_POLLUTION_GUARDS:8004-8013`** 是"历史污染自愈"白名单，**现在有两条**：`("custom/send_collapsed_startup", "false", "true")` 与 `("rx_blocks", "500", "5000")`。只在启动时（`:8134`）救**上一次**被写坏的值，救不了同一轮里的下一条。

**`_UNDO_PREFIX="selftest_undo/"`（`:8022`）** 是"写前日志"旁路：钉住前先把原始值写进 `selftest_undo/<key>` 并 `sync`，全部跑完且还原成功才删（`_mark_undo:8025` / `_mark_undo_vehicles:8035` / `_apply_undo_vehicles` / 收尾 `:8095`）。用途是扛**进程被硬杀**（Ctrl-C / 超时 / 断言里 `sys.exit`）——那种情况下 `main()` 收尾的还原根本跑不到。注释里记着两次真机事故：车号被清空、`custom/highlight_errors` 被拨成 true 没还原。

**重要缺口（我核实补全的）**：`main()` **没有**钉 `custom/soc_source` / `custom/volt_source` / `custom/material` / `theme_mode` / `glass` / `custom/anim_fps`。逐条核实结果：
- `soc_source`/`volt_source`：`check_v211_features:7829-7831` 断言的是**下拉选项文案** `== ["本地","主控"]`，`:7833` 只断言取值 `in ("calc","read")` —— **不改用户值，所以安全**；
- `anim_fps`：`check_customization` 会读它，但被 `maximum_fn` 夹到显示器刷新率，`_custom_baseline:3290` 已处理（注释在 `:3294`）；
- `theme_mode`/`glass`：`check_light_pure_white` 自己 `for scheme in ("light","dark")` + 直接调 `set_scheme/set_glass_strength`（`:805-875`），**不读用户值**，安全。

→ 结论：**目前没有遗漏的致命前提**。`devtools/_run_selftest_clean.py` 属历史遗留（`main():8556` 已钉 `highlight_errors=True`，wrapper 是它之前的老办法）。

---

## 5. 三条关键数据流（每步带 file:line）

### A. 串口字节 → 屏上文字

```
[IO 线程] SerialWorker.run → self.received.emit(bytes)
          serial_io.py:127（SerialWorker），信号定义在 :127 类体内
   ↓ 跨线程信号（Qt 自动排队到主线程）
MainWindow._on_received(data)                  main_window.py:7072
   ① 计数/闪卡/脉冲                              :7073-7077
   ② 攒批：_rx_pending += data                  :7083-7085
      触发条件：len>=RX_MERGE_MAX_BYTES(4096) 或 span>=RX_MERGE_MAX_MS(200ms)，
      否则 _rx_flush.start(RX_MERGE_MS=30) 静默兜底    :7087-7092
   ↓
MainWindow._flush_rx(force=False)              main_window.py:7094
   ③ 半行缓发：非 HEX 且不以 \n/\r 收尾 → 再等最多 RX_HOLD_MAX_MS=300ms  :7107-7112
   ④ HEX 视图分支 → _emit_block(to_hex(data))   :7116-7118
   ⑤ 文本：_decoder.feed(data) → rstrip("\r\n") → _emit_block  :7121-7124
   ↓
MainWindow._emit_block(text)                   main_window.py:7355   ← ★ 唯一汇聚点
   ⑥ strip("\r\n")，空帧 return                  :7359-7362
   ⑦ perf 整流（暗门）                           :7366-7370
   ⑧ 半截采样点拼接（块首贴回 / 块尾暂存）        :7369-7395
   ⑨ 帧内空行去掉                                :7398-7403
   ⑩ hints = _rx_grid_hints()（一帧只取一次）     :7416
   ⑪ _feed_export(text, hints)  → 导出 + 会话库  :7417   ← ★ 分叉 1（原始文本）
   ⑫ _feed_soc(text)            → SOC 链路       :7420   ← ★ 分叉 2（原始文本）
   ⑬ text = align_indexes(...)  → 排版对齐       :7421   ← ★ 从这里起文本被改
   ⑭ 逐行推进段状态，判 new_round                :7431-7434
   ⑮ need_stamp = _stamp_needed(text, new_round) :7447
   ⑯ stamped / shown 拆分（落盘恒带戳）          :7456-7459
   ⑰ gap_lines(shown) / gap_lines(stamped)      :7467 / :7473-7474
   ⑱ rx_history.feed(history_display)           :7481   ← ★ 分叉 3（落盘）
   ⑲ 暂停 → 入 _paused_frames return            :7485-7490
   ⑳ 回放中 → return（已记历史）                 :7492-7493
   ↓
MainWindow._append_display(text, display)      main_window.py:7495
   ㉑ 合并期 → 缓冲 + 24ms 定时器                 :7505-7509
   ㉒ 否则 _flush_rx_display                     :7511
   ↓
MainWindow._flush_rx_display(display)          main_window.py:7515
   ㉓ txt_rx.appendPlainText(display)            :7516
   ㉔ _note_rx_append()（记账，供追底快慢档）     :7517
   ↓
[Qt 文档重排 + RxHighlighter.highlightBlock]  ui_highlight.py:1158
   ㉕ _recolor 取色（主题/阈值/开关）             :1184-1203
   ㉖ 时间戳紫 / 序号蓝 / 越界红绿 / BMS 橙 / 断线洋红  :1285-1305
```

**改哪一步只需要动哪个文件**：
- 改 ⑥⑦（清洗规则）→ `perf_mode.py`（且必须保持幂等）
- 改 ⑧（半截点规则）→ `ui_highlight.repair_split_samples:316` + `main_window.py:7369-7395`（两处**必须**对称，实时和回放各一套）
- 改 ⑬（排版）→ **只改** `ui_highlight.align_indexes:737`（及其子函数），main_window 只是传参
- 改 ⑮（时间戳判据）→ `main_window._stamp_needed:7698`（`RX_STAMP_ANCHOR` 在 :300 附近）
- 改 ㉕㉖（配色）→ `ui_highlight._recolor:1184` + `ui_theme` 色值

### B. 同一份数据 → 落盘（**顺序是关键**）

```
_emit_block(text)                               main_window.py:7355
   │
   ├─(⑪ :7417) _feed_export(text, hints)        → 原始文本（align 之前）
   │     ├─ rx_export.session().feed(text)      rx_export.py:110    → CSV/JSON 缓存
   │     └─ rx_store.record_block(text)         rx_store.py:247     → 入队即返（后台线程解析+批量落盘）
   │
   ├─(⑫ :7420) _feed_soc(text)                  → 原始文本（align 之前）
   │
   ├─(⑬ :7421) text = align_indexes(...)        → ★ 文本从这里被改（补零/重排/插 X）
   │
   └─(⑱ :7481) rx_history.feed(history_display) → 屏上文本（gap_lines 之后、align 之后）
```

**必须验证的铁律（已在代码里确认）**：
- `_feed_export` 的 docstring 明写"两个消费者同源同位置：都是 `align_indexes` **之前**的原始文本 —— 对齐会补零、重排网格、断线插 X，反解析必错"（`main_window.py:7330-7342`）。
- `_feed_soc` 的 docstring 同口径（`:9319-9324`，⛔ 开头就是"必须拿**对齐之前**的原始文本"）。
- `hints` **一帧只取一次**（`:7416` 注释说明 `_rx_grid_hints()` 实测 ≈94µs，收发密时每秒几百次重复读注册表）。
- `rx_history.feed` 拿的是 `history_display`（**align 之后**、恒带时间戳），这是**故意的**——近期数据要存"屏上看到的样子"（`rx_history.py:8-10`）。

**改哪一步只需要动哪个文件**：
- 改导出格式/字段 → `rx_export.py`（不要改 `_feed_export` 的调用位置）
- 改 SQLite 表/分页 → `rx_store.py`
- 改近期文件名/保留行数 → `rx_history.py`
- ⛔ **任何情况下都不要把 `_feed_export` / `_feed_soc` 挪到 `align_indexes` 之后**

### C. 报文 → SOC

```
_emit_block(text)                               main_window.py:7355
   ↓ (:7420)
MainWindow._feed_soc(text)                      main_window.py:9319
   ① rounds = self._soc_feed.feed(text)          :9329
      └─ RoundCollector.feed                     soc_feed.py:172
         · 解析 VoltageN/TemperatureN/CVMax/BMS   :172-340
         · 粘滞 last_soc_read / last_v_read       :276（V=）、:265 附近（SOC=）
         · contactor_open / bms_ok 判定            :264, :344-350
   ② 粘滞同步（⛔ 必须在循环之前）                 :9334-9338
   ③ rounds 为空但来源=read 且有新值 → _refresh_soc_now  :9339-9347
   ④ for data in rounds:
        res = self._soc_est.update(cells, temps, i, ts, bad, contactor, bms_ok)  :9354-9358
             └─ SocEstimator.update              soc_est.py:873
                · clean_cells                     :636
                · WorkModeClassifier.classify     :232
                · 安时积分 + κ OCV 回拉 + 静置锚定
        self._apply_soc(res)                     :9359
   ↓
MainWindow._apply_soc(res)                      main_window.py:9364
   ⑤ _push_monitor_sample(res)（**原始 res，门控之前**）  :9368
   ⑥ state = ok/warn/off（perf 模式下 warn→ok）          :9369-9380
   ⑦ parts = soc_est.format_soc_parts(res, cfg)          :9381
      └─ format_soc_parts                          soc_est.py:1201
   ⑧ _soc_display_gate.decide(...) → 浅拷贝替身          :9385-9396
      └─ SocDisplayGate.decide                     soc_display.py:65
   ⑨ _soc_calc_last = (parts, state, emphasis)；_render_soc  :9399-9401
   ↓
MainWindow._render_soc(parts, state, emphasis)  main_window.py:9403
   ⑩ soc_source == "read" → _soc_read_parts              :9412-9416
   ⑪ parts = self._volt_part(parts)  ⛔ 必须在 SOC 来源之后  :9417
   ⑫ 记 _volt_read_last_shown；_set_soc_parts             :9418-9420
   ↓
MainWindow._set_soc_parts(...)                  main_window.py:9515
   ⑬ 四枚 QLabel（lbl_soc_mode/min/max/volt）setText + property  :9515-9545
   ↓
MainWindow._place_soc()                         main_window.py:9547
   ⑭ adjustSize + 只对非空标签计宽                        :9568-9578
   ⑮ 整栏居中 → 越界压 gap(14→6) → 贴右                    :9595-9603
   ⑯ setGeometry + raise_                                :9604-9606
   ↓（再补一帧）
MainWindow._schedule_place_soc() → _place_soc_settled  main_window.py:9608 / :9632
   （0ms 单发复核，因为 resizeEvent 早于兄弟控件排版，实测偏差 1~2px）
```

**改哪一步只需要动哪个文件**：
- 改报文解析规则 → `soc_feed.py`（`RoundCollector.feed`）
- 改算法/清洗/OCV → `soc_est.py`
- 改冻结策略 → `soc_display.py`（纯状态机，无 Qt，改完跑 `check_v211_features` 就够）
- 改文案/槽位 → `soc_est.format_soc_parts:1201` + `main_window._soc_read_parts:9435`
- 改摆位 → `main_window._place_soc:9547`（**只此一处**）

---

## 6. 「以后省 token」的具体做法

### 6.1 用「裸 import 冒烟」代替开主窗（省 ~2s/次 + 不用平台插件）

15 个纯 Python 模块可以这样一行冒烟（**不需要 QApplication**）：

```bash
.venv/Scripts/python.exe -c "import soc_est, soc_feed, soc_ekf, soc_display, perf_mode, rx_export, rx_store, marks_model, wake_lock, app_log, version, drv_assist, dlc_host, capability_api, mcp_bridge; print('ok')"
```

验证改动是否语法/导入级正确，**比跑 selftest 快 2 个数量级**。
- **省什么**：一整套 QSettings 隔离 + 建窗（~1s/窗）+ 41 条 check 的 0.22s 收尾。
- **风险**：只证明"能 import"，不证明行为。**不能替代** check，但能挡住 90% 的低级错误（拼错符号、循环 import、漏 import）。

四个模块要单独验证"真的没拉 Qt"：

```bash
.venv/Scripts/python.exe -c "import sys; import capability_api; assert 'PySide6.QtWidgets' not in sys.modules; print('pure ok')"
```

（`capability_api` 的契约就写在 `capability_api.py:9-11`：顶层禁止 `QtWidgets/main_window/ui_*/updater`，改它之后**必须**跑这条断言。）

### 6.2 ✅ `selftest.py` **已经支持** `--only / --skip / --changed / --list`（2026-11-27 新增，工作区未提交）

**核实结论（推翻本文早前版本）**：当前工作区 `selftest.py` 比 HEAD 多 360 行（8691 行），新增了 `_Selection:8253` + `_parse_args:8271` + `_match_indexes:8317` + `_resolve_selection:8387`，`main():8479` 第一行就 `selection = _parse_args(sys.argv[1:])`（`:8480`），`QApplication([sys.argv[0], *selection.rest])`（`:8496`）只把**没被吃掉的**参数交给 Qt。用法常量在 `_SELFTEST_USAGE:8179-8191`。

```bash
# 精确跑两条（中文子串匹配，大小写不敏感；也可用序号 1-41）
QT_QPA_PLATFORM=windows .venv/Scripts/python.exe selftest.py --only "显示行数（每模组 N 排）" --only "收包合并与暂停继续"
# 逗号写法等价
... selftest.py --only "显示行数,收包合并"
# 全量排除最慢那条
... selftest.py --skip "全屏展开与复制全部"
# 按 git 工作区改动自动推（唯一推荐给"改完不知道跑什么"的场景）
... selftest.py --changed
# 只列名称（不构造 QApplication，0 成本）
.venv/Scripts/python.exe selftest.py --list
```

**`--changed` 的实现（可直接当"改动半径表"的机器版用）**：
- `_CHANGED_MAP:8203-8244` 是**文件→check 关键词**的显式映射（33 个文件，覆盖全部模块）。
- `_FULL_RUN_FILES:8198` = `("main_window.py", "main.py", "selftest.py")` —— 改这三个**直接回落全量**（编排层影响面太大，刻意不猜）。
- 取改动文件用 `git diff --name-only HEAD` + `git status --porcelain` 并集（`_git_changed_files:8329`），**含未跟踪文件**。
- 未映射的文件只打印"忽略"，不报错；映射关键词一个都没匹配上则回落全量（`:8431-8433`）。
- ⛔ **前提照样全钉**：`:8595-8596` 明确注释「只把**循环的输入**换成选集结果；上面那几十行前提一条不少地照跑」——选集**不跳过任何隔离**，所以子集不会污染用户配置。这是它比自写 runner 更安全的地方。

**关键副作用（必须知道）**：
- ⚠️ 每条 check 之后仍执行 `_close_top_windows(app, wait_ms=220)` + `_reset_pinned_state`（`:8613-8614`），**子集里的每条照样付 0.22s**。
- ⚠️ `:8610-8612` 注释明写「这里**不**顺手重钉其它前提（试过，会打断依赖前一条 check 状态的用例：高亮开关 / 车号那几条会假红）」。→ **子集跑红的第一嫌疑是"跨 check 状态依赖"，不是产品代码**。遇到过就补上被跳过的那条一起跑。
- ⚠️ `--only` 与 `--skip` 同时给：先 only 再 skip（`:8435-8456`）。选集为空 / 关键词全没匹配 → **退出码 2**（不会静默跑零条）。

**旧方案已废弃**：`devtools/_selftest_subset.py`（11-12 写的，照抄 main 全套隔离 + `WANT` 元组）现在**不必再用**；`--only` 功能更强且不会因 main 改动而失同步。仓库里已无其它代码引用它。

- **省什么**：41 条 → 2 条，从 ~3-5 分钟降到 ~20s；`--list` 0 成本。
- **风险**：如上"跨 check 状态依赖"。另外 `--changed` 只认 `_CHANGED_MAP` 里登记的文件 —— **新增模块时必须把文件加进 `_CHANGED_MAP`**（`:8197-8198` 的注释就是这么要求的），否则 `--changed` 会静默漏跑。

### 6.3 开屏/启动耗时验证必须真平台 + 真实 exe

- `--check-splash`（`main.py:455`）只验证"能播 + 后端在"，**验证不了时间轴**。时间轴靠 `splash_perf.log` 的 `big_gaps`（`splash_anim.py:60 _aud`）。
- **不能用 offscreen**：`splash_anim` 的帧率/落点依赖 `QScreen` 几何与真实合成器。
- **省什么**：不用为了"改了一行开屏代码"就重打包 90s + 部署。源码态跑 `main.py` + 读 `splash_perf.log` 就够判断 `big_gaps`。
- **风险**：onefile 解包 ~2.4s 只在 exe 里出现，源码态测不到；发版前仍必须跑一次真 exe。

### 6.4 几何/像素类验证必须 `QT_QPA_PLATFORM=windows`

`selftest.py:20` 用 `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`，但 **8 处显式门控**成"只在真平台跑"（`grep QT_QPA_PLATFORM` 精确结果）：

| 行 | 所属 check | 被跳过的验证 |
|---|---|---|
| `:2704` | 全屏展开与复制全部 | 全屏重复声明（offscreen 的窗口状态模拟不可信） |
| `:3314` | 定制功能注册表 | 输入控件文字竖直居中（墨迹度量，offscreen 字体不可信） |
| `:3473` | 定制功能注册表 | 同上（另一段墨迹测量） |
| `:4065` | 定制功能注册表 | 设置面板几何（栏宽/对齐） |
| `:4130` | 定制功能注册表 | 竖直居中 ≤1.5px 断言 → 打印 `SKIP` 后 `continue` |
| `:5708` | 自绘标题栏三键 | 三键几何 |
| `:5796` | 自绘标题栏三键 | 图标字形（`bar.btn_max._glyph == GLYPH_WIN_MAX`）→ 只打印 `SKIP`，**其余几何断言照跑** |
| `:6078` | 启动时最大化开关 | 启动最大化几何 |

- **省什么**：纯逻辑/数据层改动**用 offscreen 就够**（跑得快、不抢桌面焦点、能并行）。
- **风险**：⚠️ **offscreen 下这 8 段静默跳过**，`定制功能注册表` / `自绘标题栏三键` / `全屏展开与复制全部` / `启动时最大化开关` 在 offscreen 下是"半条 check"——几何断言全绿不代表真机绿。**改 UI 几何/字号/宽度必须用 `QT_QPA_PLATFORM=windows` 跑**。
- 💡 门控只在**断言**上，`main()` 的全套 QSettings 隔离/还原**照样执行** —— 所以 offscreen 跑不会污染配置，可以放心用于逻辑回归。

### 6.5 分模块的「最小验证集」模板（可直接抄）

| 我改了什么 | 最小验证（按顺序） | 预计耗时 |
|---|---|---|
| 纯算法（`soc_est`/`perf_mode`/`rx_export`） | ① 裸 import 冒烟 ② `selftest.py --only "v2.11 四功能"`（offscreen 即可） | ~15s |
| 数据层（`rx_store`/`rx_history`） | ① 裸 import 冒烟 ② 直接调函数的 10 行小脚本 ③ `--only "会话数据库"` + `--only "历史接收数据"` | ~30s |
| 排版（`ui_highlight` 纯函数） | ① 裸 import + 直接调 `align_indexes` ② `--only "显示行数"` | ~20s |
| 设置面板 | ① py_compile ② `--only "定制功能注册表"` ③ （改宽度/几何）`QT_QPA_PLATFORM=windows` 重跑同一条 | ~60s |
| 主窗交互 | `--only "界面端到端收发"` + 受影响的那条 | ~40s |
| 全屏/最大化/动画 | 真平台 + `--only "全屏展开"`（最慢，46 个 wait_for） | 分钟级 |
| 不知道要跑什么 | `selftest.py --changed`（读 git 改动自动推，详见 §6.2） | 视改动面 |

### 6.6 别为了"验证一下"就打包

打包（`PyInstaller SerialAssistant.spec`，~90s）+ 部署（cp `发布/`）+ 验 PYZ（`devtools/_verify_odNN_pyz.py`）+ 冒烟，一整套 ~5 分钟。**只有"用户能感知的行为/外观/文案变化"才需要打包**；纯内部重构、日志、注释、devtools 改动**不需要**。

⚠️ 但有一条硬约束：`SerialAssistant.spec:85-100` 的 `hiddenimports` 是"静态分析可能漏"的兜底清单（`splash_motion`/`_snap_worker`/`dlc_host`/`soc_ekf`/`soc_display` 等）。**新增"只被函数内 import"的模块时，必须同步加进 `hiddenimports`**，否则源码态一切正常、exe 里静默降级（EKF 开关失效那种）。

---

## 7. 最反直觉的 4 件事

### ① `selftest.py` 能跑子集（`--only`），但子集**一条前提都不少**，而且 check 之间**有状态依赖**

两件叠加起来很反直觉：

1. `main():8479` 现在读 argv（`--only/--skip/--changed/--list`），但 `:8595-8596` 的注释明写「只把**循环的输入**换成选集结果；上面那几十行前提一条不少地照跑」，`:8173-8177` 更直接写「⛔ 为什么**不**做"按需跳过前提"的优化：那需要维护 41 条 × 十几个前提的依赖矩阵，错一个格子就是**永久污染用户配置**」。→ **子集不会更快地跳过隔离，只少跑 check。**
2. `_reset_pinned_state:151` **故意只重钉 `rx_blocks` 一条**，注释（`:8610-8612`）说"重钉 highlight_errors/车号会打断依赖前一条 check 状态的用例"。→ **子集跑红的第一嫌疑是"被跳过的那条 check 没给状态"，不是产品代码坏了。**

配套事实：`main()` 在跑 CHECKS 前钉死 9 类前提（`module_count/cells_per_module/temps_per_module/volt_rows/temp_rows=0` `:8533-8535`、`pad_zero=True` `:8546`、`rx_blocks=5000` `:8547`、`highlight_errors=True` `:8556`、`select_vehicle("")` `:8564-8565`、`link_mode` 快照 `:8566`、`rx_history._resolve_dir` 临时目录 `:8577`、`rx_store` 临时库 `:8584`），**每一项都对应一次"用户真实配置下假挂"的血案**（注释里逐条写了日期和现象）。另外 `--changed` 改 `main_window.py`/`main.py`/`selftest.py` 会**直接回落全量**（`_FULL_RUN_FILES:8198`）——不是算不出，是刻意不猜。

### ② `_emit_block` 里的三条下游链路吃的是**三个不同版本**的同一份文本

大多数人会以为"一份数据喂三条链路"就是同一份文本。实际是：
- `rx_export` / `rx_store` / `soc_feed` 吃 **align 之前的原始文本**（`:7417/:7420`）；
- 屏上 `txt_rx` 吃 **align + gap_lines + 按显示开关决定带不带时间戳**的文本（`:7467-7471`）；
- `rx_history`（近期数据）吃 **align + gap_lines + 恒带时间戳**的文本（`:7473-7481`）。

三个版本**故意不同**，注释在 `:7330-7336`（导出）、`:7319-7324`（SOC）、`:7443-7450`（stamped/display 拆分）。改任何一处顺序都会造成"屏上正常、导出错位"或"时间戳丢失"。

### ③ `ui_highlight` 的 `VOLT_HEADER_RE` / `TEMP_HEADER_RE` / `BMS_ERROR_RE` 是**"活代理"对象**，不是 `re.Pattern`

`ui_highlight.py:54-93` 的 `_LivePattern` 每次 `match/finditer` 都去问当前协议（`rx_protocol`）。原因写在 `:48-52`：`main_window` 在**模块 import 时**就把 `VOLT_HEADER_RE` 这个名字绑走了（用它判 `new_round`），如果换成 `re.compile` 的结果对象，协议热切换后 `main_window` 那边永远是旧值。**改协议功能时不能"顺手把它换成普通正则"**，否则热切换静默失效。

### ④ `QTimer.singleShot` 捕获控件时**必须带 context 参数**，否则宿主析构后回调打到已删对象

三种写法的实测差别（2026-11-27，PySide6 6.12.0，宿主 `shiboken6.delete` 掉之后看回调是否抛）：

| 写法 | 结论 |
|---|---|
| `singleShot(ms, lambda: w.setText(...))` 闭包捕获 | ⛔ **会抛** `already deleted` |
| `singleShot(ms, lambda b=w: b.setText(...))` 默认参数捕获 | ⛔ **会抛**，和闭包一样 |
| `singleShot(ms, w, callable)` **带 context** | ✅ 宿主一删就自动掐断，不抛 |
| `singleShot(ms, w.bound_method)` 绑定方法 | ✅ 安全（同 context，PySide 自动关联） |

**正确先例在 `main_window.py:5380`**（`QTimer.singleShot(1500, self, self._start_snap_worker)`），注释写明「无 context 的单发定时器不会随窗口销毁而取消……进程静默死掉（selftest 退出码 127 的根因）」。

为什么这条反直觉：**抛异常不等于崩溃**。默认 `sys.excepthook` 只打印一段 traceback 然后**程序继续** —— 功能不受影响，但控制台噪音会掩盖真问题，而且这种写法会一直被复制。修它不是为了防崩，是为了防扩散。

⚠️ 造复现时有个坑：`QDialog.close()` **默认只 hide**（没设 `WA_DeleteOnClose`），要 `deleteLater()` + `sendPostedEvents(None, QEvent.Type.DeferredDelete)` 才真删；或者直接 `shiboken6.delete(obj)`。只 `close() + processEvents()` 会得到「宿主还活着」的假结论（2026-11-27 踩过）。

---

## 附：本次勘察的只读产物

| 文件 | 内容 |
|---|---|
| `_check/_arch_ast.py` | AST 扫描器（39 模块，行数/import/类/函数/Qt 依赖） |
| `_check/_arch_ast.json` | 扫描结果 |
| `_check/_arch_graph.py` | 顶层 + 函数内延迟 import 的完整依赖图 + 环检测 + 拓扑分层 |
| `_check/_arch_graph.json` | 图与分层结果 |
| `_check/_arch_slow.py` | 每条 check 的 wait_for/sleep 统计（慢 check 判据） |
| `_check/arch_survey.md` | 本报告 |

⛔ 全部产物在 `_check/`，**未改动任何产品源码、未跑 selftest、未动 QSettings、未打包**。