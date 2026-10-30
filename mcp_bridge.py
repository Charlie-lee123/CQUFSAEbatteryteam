# -*- coding: utf-8 -*-
"""MCP 接入 —— 生成「丢给 Agent 就能自动部署」的自包含接入包。

背景
----
``mcp/serial_assistant_mcp.py`` 是给 Agent 用的串口 MCP 服务（9 个工具）。
它本身早就能跑，但只存在于作者的机器上：没进安装包、没有文档入口，别人拿不到。
这个模块解决"交付"：

* **源码随包走**：``SerialAssistant.spec`` 把 ``mcp/serial_assistant_mcp.py``
  作为 data 打进 exe，运行时用 :func:`server_source` 读出来。
* **接入包自包含**：:func:`deploy_payload` 生成一整段中文指令 —— 装依赖、写服务、
  注册 MCP、自检，四步加**完整源码**。复制走发给任意 Agent（WorkBuddy / Claude
  Desktop / Cursor…）即可，不需要联网、不需要 clone 仓库。
* **两条出口**：设置面板的按钮（剪贴板）、以及命令行
  ``SerialAssistant.exe --mcp-payload [输出路径]``（写文件，给无 GUI 的自动化用）。

⚠️ 串口是独占资源：MCP 打开端口期间，GUI 不能同时占用同一个口 —— 这一条写进了
接入包，Agent 读完就知道要先确认端口空闲。
"""

from __future__ import annotations

import os
import sys

from version import REPO_URL, VERSION

# 接入包里的默认安装目录（Agent 可以整体替换成别的盘）。
DEFAULT_DIR = r"%USERPROFILE%\.serialassistant\mcp"

# 服务端源码在包内的相对路径（spec 的 datas 与之对应）。
SERVER_REL = os.path.join("mcp", "serial_assistant_mcp.py")

_SRC_MARK_BEGIN = "-----BEGIN serial_assistant_mcp.py-----"
_SRC_MARK_END = "-----END serial_assistant_mcp.py-----"


def _bundle_base() -> str:
    """资源根目录：打包后是解包临时目录，开发时是项目根。

    与 ``ui_brand._asset_dir`` 同一套判断（``sys._MEIPASS`` 只有 onefile 才有）。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return base
    return os.path.dirname(os.path.abspath(__file__))


def server_source() -> str:
    """打进包里的 MCP 服务源码；读不到时返回空串（调用方负责降级）。"""
    path = os.path.join(_bundle_base(), SERVER_REL)
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


# ---------------------------------------------------------------- 手动填写

# 服务文件的落盘位置（与「复制接入配置」里给 Agent 的指令保持一致）
MCP_HOME = os.path.join(os.path.expanduser("~"), ".serialassistant", "mcp")
SERVICE_NAME = "serial_assistant_mcp.py"


def service_path() -> str:
    """服务源码应该存在的绝对路径（不保证已存在，见 ``ensure_service()``）。"""
    return os.path.join(MCP_HOME, SERVICE_NAME)


def ensure_service() -> tuple[str, bool]:
    """把服务源码写到 ``service_path()``；返回 (路径, 是否本次新写/更新)。

        内容与内置源码**逐字节比对**：一致就不动（别每次开面板都写盘），
        不一致（版本升级）才覆盖 —— 用户手改过也会被覆盖，这是刻意的：
        这份文件是"软件的一部分"，不是用户配置。
    """
    target = service_path()
    source = server_source()
    if not source:
        return (target, False)
    try:
        if os.path.isfile(target):
            with open(target, encoding="utf-8") as handle:
                if handle.read() == source:
                    return (target, False)
        os.makedirs(MCP_HOME, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(source)
        return (target, True)
    except OSError:
        return (target, False)


def _venv_python() -> str:
    """优先指向本机安装时建的那个 venv（它里面一定有 mcp + pyserial）。"""
    candidate = os.path.join(MCP_HOME, "venv", "Scripts", "python.exe")
    return candidate if os.path.isfile(candidate) else ""


def _current_python() -> str:
    """没装 venv 时给一个"大概率能用"的 python。

    打包后 ``sys.executable`` 就是 SerialAssistant.exe 自己（不能用）；开发环境
    下它就是 venv 里的 python（装了 mcp 吗？不一定）—— 所以只是兜底，UI 里会
    写清楚"这个 python 得先 pip install mcp pyserial"。
    """
    if getattr(sys, "frozen", False):
        return ""
    return sys.executable or ""


def manual_fields() -> dict:
    """「连接至自定义 MCP」对话框**逐项要填的值**（本机真实路径，可直接复制）。

    返回：
        name      名称        serial-assistant
        transport 类型        stdio
        command   命令        venv 里的 python.exe（没有 venv 就退回当前 python）
        args      参数        [服务 .py 的绝对路径]
        env       环境变量    {}（本服务不需要）
        notes     说明        给 UI 的提示（命令是否已就绪、缺依赖怎么办）
    """
    path, _wrote = ensure_service()
    command = _venv_python() or _current_python()
    notes = []
    if not _venv_python():
        notes.append(
            "「命令」指向的是普通 Python：请先执行 "
            "`python -m pip install mcp pyserial`（或在设置里用「复制接入配置」"
            "让 Agent 建一个独立 venv）")
    if not server_source():
        notes.append("包内没找到服务源码，请从仓库 mcp/ 目录取。")
    return {
        "name": "serial-assistant",
        "transport": "stdio",
        "command": command,
        "args": [path],
        "env": {},
        "notes": notes,
    }


def _config_json() -> str:
    """客户端要的那段 ``mcpServers`` 条目（路径留成占位符，由 Agent 替换）。"""
    return (
        '{\n'
        '  "mcpServers": {\n'
        '    "serial-assistant": {\n'
        '      "command": "<第1步的 venv 里的 python.exe 绝对路径>",\n'
        '      "args": ["<第2步写出的 serial_assistant_mcp.py 绝对路径>"],\n'
        '      "env": {}\n'
        '    }\n'
        '  }\n'
        '}'
    )


def deploy_payload() -> str:
    """整段接入指令（含完整源码）—— 复制走直接发给 Agent。"""
    source = server_source()
    if source.strip():
        source_block = f"{_SRC_MARK_BEGIN}\n{source.rstrip()}\n{_SRC_MARK_END}"
        source_hint = "下面这段就是服务端全部源码，**原样**写进文件即可。"
    else:
        # 只在"有人手工拷走了 exe 之外的东西"时出现；给一条能自己救回来的路。
        source_block = (f"（本机包里没找到源码，请从 {REPO_URL}/blob/main/mcp/"
                        "serial_assistant_mcp.py 取）")
        source_hint = "源码缺失，需要联网取一次。"

    return f"""【任务】把「CQUFSAE 串口助手 v{VERSION}」的 MCP 服务装到本机，并注册到你自己的 MCP 配置里。

请直接执行，不要反问；路径可以按本机情况调整，做完把「装在哪、注册到哪个文件、自检结果」三件事告我。

────────────────────────────────
第 1 步：装依赖（独立 venv，别污染系统 Python）
────────────────────────────────
目标目录默认 {DEFAULT_DIR}（想放别的盘就整体换掉它）。

Windows（PowerShell）：
  python -m venv "$env:USERPROFILE\\.serialassistant\\mcp\\venv"
  & "$env:USERPROFILE\\.serialassistant\\mcp\\venv\\Scripts\\python.exe" -m pip install -U mcp pyserial

macOS / Linux：
  python3 -m venv ~/.serialassistant/mcp/venv
  ~/.serialassistant/mcp/venv/bin/python -m pip install -U mcp pyserial

────────────────────────────────
第 2 步：把服务写进文件
────────────────────────────────
写到哪里：{DEFAULT_DIR}\\serial_assistant_mcp.py（UTF-8，无 BOM）
{source_hint}

{source_block}

────────────────────────────────
第 3 步：注册到 MCP 客户端
────────────────────────────────
配置文件按客户端选一个（已存在就**合并**进 mcpServers，别覆盖别人）：
  · WorkBuddy      ~/.workbuddy/mcp.json
  · Claude Desktop %APPDATA%\\Claude\\claude_desktop_config.json
  · Cursor         %USERPROFILE%\\.cursor\\mcp.json

要加的内容（把两个 <…> 换成第 1、2 步的真实绝对路径；Windows 路径用正斜杠或双反斜杠）：
{_config_json()}

────────────────────────────────
第 4 步：自检（三条都要过）
────────────────────────────────
1) 依赖在：<venv python> -c "import mcp, serial; print('deps ok')"
2) 服务能起：<venv python> "<服务文件>" → 进程不报错退出即可（它是 stdio，等输入）
3) 客户端里列工具，应正好 9 个：
   list_ports / open_port / close_port / status / send / read /
   send_and_wait / clear_buffer / send_repl

────────────────────────────────
上手约定（Agent 自己看）
────────────────────────────────
· 先 list_ports 看有哪些口、是否被占用（in_use 非空说明别的程序开着）。
· 波特率不确定就从 115200 起试；打不开常见原因是**端口被 GUI 占着**（串口是独占资源，
  本服务开着口时串口助手界面不能同时连同一个口）。
· 取数据用 read（非阻塞、增量、只返回"上次 read 之后"的新行）；
  要"发一条等回包"用 send_and_wait（给 expect_regex 更稳）；初始化寄存器用 send_repl。
· 收数据缓冲默认留最近 5000 行，长时间跑不会吃内存。

来源：{REPO_URL}（v{VERSION}，设置面板 →「MCP Service」可再次复制本段）
"""


def write_payload(path: str | None = None) -> str:
    """把接入包写到文件（默认 exe 同级 ``mcp-接入配置.txt``），返回落盘路径。

    命令行出口用它：``SerialAssistant.exe --mcp-payload [输出路径]``。
    console=False 的程序打不出字，写文件才是能验证的落地证据。
    """
    if not path:
        base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
                else _bundle_base())
        path = os.path.join(base, "mcp-接入配置.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(deploy_payload())
    return path
