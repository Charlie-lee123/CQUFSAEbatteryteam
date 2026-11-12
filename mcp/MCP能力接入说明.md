# SerialAssistant「内部能力」MCP 接入说明

与已发布的 **`serial-assistant`（9 个工具、直连硬件串口）并列** 的第二个 stdio
服务：**`serial-assistant-capabilities`**。它只暴露软件内部的**只读**计算与查询
能力，不碰串口、不碰硬件，两者可同时配置、互不干扰。

---

## 1. 零安装启动（推荐，无需 pip install）

本机两个 venv 各带「一半」依赖，用 `PYTHONPATH` 跨 venv 借 PySide6 即可跑通：

| venv | Python | 自带 | 缺 |
|---|---|---|---|
| `C:\Users\Lenovo\.serialassistant\mcp\venv` | 3.13 | `mcp` 2.x SDK（`MCPServer`） | PySide6 |
| `F:\MimoCodeworks\SerialAssistant\.venv` | 3.12 | PySide6 | `mcp` SDK |

用**独立 MCP venv 的 python** 启动服务脚本，并把「项目根 + 项目 `.venv` 的
site-packages」写进 `PYTHONPATH`：前者让 `capability_api.py` 及纯逻辑模块可导入，
后者让 P1 能力跨 venv 借到 `PySide6.QtCore.QSettings`（实测读到 56 键真实配置）。

把下面这段粘贴进 MCP 客户端的 `mcpServers` 配置（如 `~/.workbuddy/mcp.json`、
`~/.cursor/mcp.json`，与已有的 `serial-assistant` 条目**合并**，别覆盖别人）：

```json
{
  "mcpServers": {
    "serial-assistant-capabilities": {
      "command": "C:\\Users\\Lenovo\\.serialassistant\\mcp\\venv\\Scripts\\python.exe",
      "args": ["F:\\MimoCodeworks\\SerialAssistant\\mcp\\serial_assistant_capabilities.py"],
      "env": {
        "PYTHONPATH": "F:\\MimoCodeworks\\SerialAssistant;F:\\MimoCodeworks\\SerialAssistant\\.venv\\Lib\\site-packages"
      }
    }
  }
}
```

> 零安装、不污染项目 `.venv`、不写系统 Python；依赖都在 F 盘 / 用户目录。
> 服务对 mcp 1.x（FastMCP）与 2.x（MCPServer）都兼容（本机走 2.x 分支）。

---

## 2. 暴露的能力：28 个，全部只读

工具从 `capability_api.list_tools()` **动态注册**；每个工具转调
`capability_api.call(name, params)`，`ok=False` 时以工具错误形式返回。
所有能力的 `mutating` 均为 `False`。

### P0（16 个，零 Qt，顶层直接 import）

纯计算 / 纯常量，无 PySide6 依赖，任何 Python 都能跑：

| 分组 | 能力 |
|---|---|
| 常量/版本 | `version_info` |
| OCV 查表 | `ocv_from_soc`、`soc_from_ocv`、`slope_at`、`kappa_at`、`table_ok` |
| 清洗/格式化 | `clean_cells`、`format_soc_parts`、`format_soc` |
| 配置 | `soc_cfg_to_dict`、`soc_cfg_from_dict` |
| 估算器 | `soc_estimator_update`、`soc_estimator_reset` |
| 报文解析 | `parse_text` |
| EKF | `soc_ekf4_step` |
| 结构化缓存 | `rx_export_session`（每次新建 `Session`，不碰模块级单例、不落盘） |

### P1（12 个，需 PySide6，函数内延迟 import）

只读查询，模块顶层依赖 `PySide6.QtCore.QSettings`；缺失时该能力返回
`ok=False / error.type=Unavailable`（门面本身不受影响）：

| 分组 | 能力 |
|---|---|
| SOC 参数表 | `builtin_packs`、`cfg_for` |
| 车辆/配置档案 | `customization_vehicles`、`customization_vehicle_names`、`customization_current_vehicle`、`customization_active_profile`、`customization_get`、`customization_specs` |
| DLC | `dlc_discover` |
| 会话库 | `rx_store_info`（`count`/`stats` 只读查询） |
| 接收历史 | `rx_history_labels` |
| 串口驱动（2026-11-12） | `driver_status`：随包带了哪些芯片驱动、本机驱动库缺哪些、当前插着哪些设备没驱动（**只读，不安装**；安装要走界面里用户点 UAC） |

---

## 3. 只读保证（硬约束）

- 门面**不暴露任何写函数**：`customization.set/select_vehicle/save_vehicle/...`、
  `soc_profiles.save_pack/delete_pack`、`rx_store.record_block/set_db_path/flush`、
  `rx_history.set_directory/feed/flush`、`dlc_host.install_zip/uninstall/set_enabled/load`、
  `rx_export.write_csv/write_json` 全部不在接口内。
- 顶层禁止导入 `QtWidgets` / `main_window` / `ui_*` / `updater`，保证「门面 import 不失败」。
- 每个能力独立异常隔离（单个失败只返回结构化 error，不拖垮服务）；输出统一
  转 dict 并 `json.dumps(allow_nan=False)` 硬保证可序列化。
- 入参有字符串长度/列表长度/NaN·Inf/soc 范围/路径穿越校验；未知能力返回
  `ok=False` 而不抛异常。
- **QSettings 零污染**：验证脚本全量快照（含所有 `allKeys()`）+ `finally` 无条件
  还原，实测前后完全一致（56 键 → 56 键，新增/丢失键均为空）；全程不
  `setValue/sync/remove` 写真实配置。

---

## 4. 与 9 工具服务并存

| | `serial-assistant`（已发布） | `serial-assistant-capabilities`（本服务） |
|---|---|---|
| 工具数 | 正好 9 个 | 28 个 |
| 作用 | 开关串口、收发字节、直连 pyserial 硬件 | 软件内部只读计算/查询 |
| venv | 独立 MCP venv | 独立 MCP venv（+ PYTHONPATH 借 PySide6） |
| 触碰硬件 | 是 | **否** |

`mcp_bridge.py` 只引用 `mcp/serial_assistant_mcp.py`，不枚举 `mcp/` 目录，
新增本服务**不改变已发布的 9 工具契约**（`README.md` 与 `mcp_bridge.py` 均未改）。

---

## 5. 自检 / 验证命令

```bash
# 只读契约验证（33 条断言，含数值回归、异常隔离、QSettings 零污染）
.venv/Scripts/python.exe devtools/_verify_capability_api.py

# 静态检查
/f/WorkBuddyTools/venv/Scripts/ruff.exe check --select F,E7,E9,W6 capability_api.py mcp/serial_assistant_capabilities.py devtools/_verify_capability_api.py
/f/WorkBuddyTools/venv/Scripts/vulture.exe capability_api.py devtools/_verify_capability_api.py --min-confidence 60

# 门面 CLI（源码态，不接 main.py）
.venv/Scripts/python.exe capability_api.py --list
.venv/Scripts/python.exe capability_api.py --call ocv_from_soc --params-json "{\"soc\": 50}"
```
