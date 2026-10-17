# v2.1 —— MCP 一键接入 + 包体瘦身 33%

## 🤖 新增：MCP 接入（把串口交给 AI Agent）

软件自带一个 **MCP 服务**（9 个工具），装好后 WorkBuddy / Claude Desktop / Cursor
等 Agent 可以**直接开关串口、收发数据、发指令等回包**——排查 BMS、跑自动化测试
再也不用手抄数字。

**一键使用**：设置 → 「AI 接入」→「复制接入配置」→ 粘给任意 Agent，说一句"按这个部署"。

复制出来的内容**完全自包含**：装依赖的命令、MCP 服务的**全部源码**（随 exe 分发，
不需要 clone 仓库）、客户端注册片段、三条自检标准、串口独占的注意事项。
Agent 照着执行就会：建独立 venv → 落盘服务 → 注册 `mcp.json` → 自检出 9 个工具。

命令行同样能导出（无 GUI 自动化用）：

```
SerialAssistant.exe --mcp-payload [输出路径]
```

9 个工具：`list_ports` / `open_port` / `close_port` / `status` / `send` / `read` /
`send_and_wait` / `clear_buffer` / `send_repl`（逐条发送逐条收）。

## 📦 包体 52.6 MB → 35.2 MB（−33%）

打包钩子此前按 Qt 全量收运行时，本版本按实际使用裁掉 41 MB（原始体积）无用 DLL：

| 剔除 | 体积 | 原因 |
| --- | --- | --- |
| opengl32sw.dll | 20.6 MB | 软件 OpenGL 兜底，程序无 OpenGL 绘制 |
| Qt6Quick / Qml 全家 | 13.7 MB | 没有一行 QML |
| Qt6Pdf.dll | 4.6 MB | 无 PDF 功能 |
| Qt6OpenGL.dll | 2.0 MB | 同上 |
| qtiff / qwebp 图像插件 | 1.6 MB | 只用 png / ico / svg / jpeg |

## ⚡ 更快

- 冷启动模块导入 298 ms → 230 ms（**−23%**）：蓝牙模块改为**用到才加载**
  （`ble_io` 会连带拉起 asyncio，串口用户此前一直在替它付 76 ms）。

## 🧹 代码清理

- 删除 5 个无调用者的窗口方法、3 个无调用者的模块函数、一条从未接线的
  focus_boost 渲染通道与若干死常量/死计数（全部经静态分析逐一核对）。

## 🔧 修复

- 普通窗口几何此前可能被写成无效值（`@Invalid()`），导致非全屏启动时回到
  默认尺寸；现在能正确恢复。

---

**下载**：[安装版 Setup](https://github.com/Charlie-lee123/CQUFSAEbatteryteam/releases/latest/download/SerialAssistant-setup-2.1.exe)
· [免安装单文件](https://github.com/Charlie-lee123/CQUFSAEbatteryteam/releases/latest/download/SerialAssistant.exe)

配置全部存注册表，覆盖安装/替换 exe 都不丢设置。完整变更见 README 版本表。
