# heartbeatDLC · 心率广播 DLC（多设备）

**可选插件**，不随主程序一起打包。需要的人单独下载 `heartbeatDLC-1.0.0.zip` 自行安装；
不装的人，主程序体积与行为完全不变。

- **重点支持：小米手环 10 Pro，含陶瓷特别版**（用户实机型号）。陶瓷版仅外壳 / 表带 /
  重量不同（28.7 g vs 21.6 g），内部硬件与固件一致，心率广播协议完全相同。
- **同时支持多台设备**：同屏总览所有手环的 BPM，每台另有独立详情标签，可分别查看。
- 适用范围：小米手环 8 代及以后（8 / 8 Pro / 9 / 9 Pro / 10 / 10 Pro 等）。
  4~7 代使用另一套厂商广播格式，本插件**不支持**。

---

## 1. 它做什么

小米手环在「蓝牙心率广播」开启后，会在 BLE 广播里声明标准 **Heart Rate Service
`0x180D`**。插件扫描到该服务后连接 GATT、订阅 **Heart Rate Measurement `0x2A37`** 通知。

- **左侧设备列表**：勾选即连接，取消即断开，任意组合；扫描只列心率设备。
- **「全部设备」标签**：每台一张卡片，大字 BPM 同屏对比；点卡片跳到对应详情。
- **每台设备一个独立标签**：默认进入**沉浸可视化**——纯黑底，左上红色大字 BPM（带红光晕）
  配「次/分」单位，中部红色网格 ECG 带，满屏红/白星点场在每次心跳时整体放大约 30% 并闪红，
  完全还原网页「心宿」（`jcjt.uno`）的同款观感。可一键切回 BPM 趋势折线。各设备样本、
  统计、趋势**完全隔离**，停止后历史数据仍保留可回看。
- 断线按设备独立自动重连（2 秒间隔），同一台设备的相同错误 10 秒内只报一次。
- 可选把每条心率追加到本地 CSV（带设备地址、名称、电量、佩戴位置）。

> 「广播」是**发现机制**，心率数值本身走 GATT 通知。这与主程序原有的「蓝牙广播接收」
> （纯被动扫 manufacturer data、不连接）是两条不同通道，插件不改动也不占用后者。

## 2. 手环端准备（必做，10 Pro 尤其注意）

1. 手环与「小米运动健康 / Mi Fitness / Zepp Life」完成绑定，固件更新到较新版本。
2. 在**手环上**打开：**设置 → 心率 → 蓝牙心率广播**（部分固件叫 Share HR / 心率共享），
   该功能**默认关闭**。
3. ⚠️ **开启后请停留在广播页面**。广播通常在手环屏幕回到表盘时停止 —— 这是 10 Pro
   最常见的「刚连上就断 / 扫不到」原因。
4. ⚠️ 广播期间手环会**主动断开与手机的连接**；反过来，手机先占住 BLE 链路时电脑也
   看不到广播。BLE 是单连接，两边只能选一个。
5. ⚠️ **不要在 Windows 设置里配对手环**：已配对但未广播的设备会导致连接卡住。
   本插件走免配对 GATT 连接。
6. 开始一次测量或进入运动状态；部分型号仅在测量期间持续推送心率。
7. 电脑需支持 BLE 且蓝牙已开启；Windows 10 2004+ / Windows 11。

> 小米官方规格对 10 Pro 的措辞是「请确保接收设备支持 BLE 心率协议，并已启用心率
> 接收功能」，即标准 0x180D 路径，不依赖任何私有服务或 AuthKey。

## 3. 安装

### 3.1 从主程序安装（推荐）

1. 主程序右侧「串口配置」栏底部 → **「扩展 / DLC」**
2. 点 **「从 ZIP 安装 …」**，选下载到的 `heartbeatDLC-1.0.0.zip`
   （若同目录有 `.sha256` 会自动校验完整性）
3. 列表里出现「心率广播 DLC」，启用 → 配置栏多出 **「心率 DLC」** 按钮
4. 「打开下载页」可跳到 Releases 页自行选择版本

安装位置：`%LOCALAPPDATA%\CQUFSAE-SerialAssistant\dlc\heartbeatDLC\`。
便携版也可把解压后的 `heartbeatDLC` 目录直接放到 exe 同级的 `dlc\` 下。

### 3.2 独立运行（不需要主程序）

```bash
python -m heartbeatDLC                 # 打开面板
python -m heartbeatDLC --scan 8        # 只扫描 8 秒并列出心率设备
python -m heartbeatDLC --selftest      # 无硬件协议自测
python -m heartbeatDLC --check         # 检查 PySide6 / bleak 依赖
```

## 4. 多设备使用

1. 打开面板 → **「扫描设备」**（约 6 秒），左侧列出所有带 `0x180D` 的设备；
   10 Pro 即使广播里暂未声明服务 UUID，也会按设备名兜底列出并标注「待校验」。
2. **勾选任意多台** → 每台各自建详情标签与总览卡片，独立连接、独立重连。
3. **「全部设备」** 标签同屏对比；点任意卡片或切到该设备标签即可**分别查看**。
4. **「停止全部」** 断开所有连接，但各设备历史数据与趋势保留，可继续查看。
5. 开启 **「自动恢复上次设备」**：下次扫描完成会自动勾选并连接上次选中的设备。
6. 开启 **「记录到 CSV」**：所有设备的数据写进同一个 `hr_log.csv`，用 `address` 列区分。

> 并发上限取决于电脑的蓝牙适配器（Windows 通常可同时维持数个 BLE 连接）。
> 设备多时，每台的连接 / 重连互不阻塞：Hub 用单个 asyncio 事件循环、每台一个独立
> Task，设备发现用锁串行以避免并发扫描互相抢。

## 5. 技术说明

### 发现 → 连接 → 通知

| 阶段 | 依据 |
| --- | --- |
| 发现 | 广播 `service_uuids` 含 `0000180d-0000-1000-8000-00805f9b34fb`；10 Pro 名称兜底 |
| 校验 | 连接后遍历 GATT，确认 `0x2A37` 存在，否则给出针对手环端的操作提示 |
| 订阅 | `start_notify("00002a37-0000-1000-8000-00805f9b34fb", callback)` |
| 附加 | 尽力读取 `0x2A19` 电量、`0x2A38` 佩戴位置（10 Pro 提供），读不到静默跳过 |

### Heart Rate Measurement（0x2A37）字节格式

```
Byte 0  Flags
        bit0  心率值格式        0 = UINT8，1 = UINT16（小端）
        bit1  Sensor Contact Detected
        bit2  Sensor Contact Supported（bit1 仅在此位为 1 时有效）
        bit3  Energy Expended Present   后续 2 字节 UINT16，单位 kJ
        bit4  RR-Interval Present      后续每 2 字节一条，单位 1/1024 秒
Byte 1  心率（UINT8）或 Byte 1~2（UINT16）
```

小米手环 9 Pro / 10 的实测帧常为 `00 59`（flags=0，89 bpm）。插件同时实现
UINT16 / 佩戴检测 / 能量 / RR 间期，换固件不会读错。

### 线程与退出

`HeartRateHub` 是一个 `QThread`，内部跑 `asyncio.run`；每台设备一个 Task。
`stop()` 只等 200ms，未退干净时保留强引用等 `finished` 再销毁 —— 避免 QThread
仍运行时被 Python 回收导致的 `qFatal`。这一套与宿主 `ble_io.py` /
`main_window._close_port` 一致。

## 6. 限制与边界

- **未逐台实机验证**：实现依据官方规格、公开抓包与开源项目，覆盖标准 HRS 路径。
  10 Pro 陶瓷特别版的协议与普通 10 Pro 一致（同硬件），但仍建议按第 2 节实机确认。
- **BLE 单连接**：手机连着就收不到；多设备时每台手环都不能被手机占用。
- **广播会随表盘切换停止**：10 Pro 需停留在广播页面。
- **不做认证 / AuthKey 路径**：不走 `0xFE95` 加密会话那条「实时心率」通道。
- **非医疗设备**：数值仅供训练与现场参考，不用于诊断。
- **隐私**：不联网、不上传；CSV 与设置只写本地 `%LOCALAPPDATA%`。

## 7. 目录与自测

```
heartbeatDLC/
  manifest.json     插件清单（id / version / api / min_app / entry）
  __init__.py       create(ctx) —— 宿主插件接口
  hr_protocol.py    纯函数层：0x2A37 解析、10 Pro 识别、多设备目标集合、电量/位置
  hr_worker.py      HeartRateScanner + HeartRateHub（多设备并发连接）
  hr_panel.py       设备列表 / 总览卡片 / 每台独立详情（含沉浸可视化切换）/ CSV
  hr_visual.py      沉浸可视化：黑底 + 红色大字 BPM + 红色网格 ECG + 红白星点场心跳闪红（QPainter 实现，心宿 jcjt.uno 同款观感）
  __main__.py       独立运行入口
  selftest.py       无硬件自测
  README.md / NOTICE.md
```

```bash
python -m heartbeatDLC --selftest     # 协议 + 多设备逻辑，需全绿
```

## 8. 许可证与致谢

本插件代码为 MIT（见 `NOTICE.md`）。协议事实参考以下公开资料，未复制其代码：

| 项目 / 资料 | 许可证 | 用途 |
| --- | --- | --- |
| Bluetooth SIG Heart Rate Service 1.0 | SIG 规范 | 0x2A37 位定义、0x2A19 / 0x2A38 |
| Xiaomi 官方 Smart Band 10 Pro 规格页 | 厂商规格 | 确认支持蓝牙心率广播且要求接收端支持 BLE 心率协议；陶瓷版与标准版同硬件 |
| [Tnze/miband-heart-rate](https://github.com/Tnze/miband-heart-rate) | MIT | 小米手环 10 走 0x180D / 0x2A37 notify 的实现验证 |
| [Lniosy/heart-rate-monitor](https://github.com/Lniosy/heart-rate-monitor) | MIT | 9 Pro 抓包（`00 59` = 89 bpm）与 bleak 流程 |
| [TheArmagan/advosc](https://github.com/TheArmagan/advosc) | GPL-3.0（仅参考文档） | 10 Pro 设备名、广播页面/表盘停止广播、免配对、多心率源、自动重连等实机注意事项 |
| [Xianggeok/xioami_heart_rate](https://github.com/Xianggeok/xioami_heart_rate) | 见仓库 | 小米手环 10 按 0x180D 过滤设备的交叉印证 |
| 网页「心宿」(`https://jcjt.uno/`，署名「心宿 · 沐风工作室」) | 公开前端 SPA | **可视化同款视觉来源**：黑底 + 红色大字 BPM + 红色网格 ECG + 红白星点场心跳闪红；已做 clean-room 重写，未复制其代码、未引入 Three.js |

> 可视化风格为网页「心宿」（`jcjt.uno`）同款视觉的 clean-room 功能性复刻，仅参考其公开发布
> 的客户端代码中描述的视觉参数与布局，用 PySide6 `QPainter` 重新实现，未复制其任何源代码、
> 未引入 Three.js / WebGL / 任何外部资源或字体。
