# -*- coding: utf-8 -*-
r"""SerialAssistant「内部能力」MCP stdio 服务（与 9 工具硬件服务**并列**）。

⛔ 与现有服务的边界
--------------------
* ``mcp/serial_assistant_mcp.py`` 是**已发布契约**：9 个工具、独立 venv、直连
  硬件串口（pyserial）。本服务**不改它一个字符**，也不复用它的进程/端口。
* 本服务**不碰硬件**，只把 ``capability_api.py`` 暴露的软件内部只读能力
  （SOC 算法、报文解析、车辆/配置/DLC/会话库只读查询等）动态注册成 MCP 工具。
* 两个服务可同时被 MCP 客户端配置，互不干扰。

⚙️ 启动方式（零安装，推荐）
--------------------------
本机两个 venv 各带「一半」依赖，用 ``PYTHONPATH`` 借 PySide6 即可**零安装**跑通：

* 独立 MCP venv（Python 3.13）：自带 ``mcp`` 2.x SDK，但没有 PySide6；
* 项目 ``.venv``（Python 3.12）：自带 PySide6，但没有 mcp SDK。

用独立 MCP venv 的 python 启动本脚本，并把「项目根 + 项目 .venv 的
site-packages」写进 ``PYTHONPATH``：前者让 ``capability_api`` 及纯逻辑模块
可 import，后者让 P1 能力跨 venv 借到 ``PySide6.QtCore.QSettings``。
脚本内部也会自动把项目根 append 进 sys.path（不 insert(0)，避免把项目内
``mcp/`` 命名空间包顶到已安装 SDK 前面）。

MCP 客户端 ``mcpServers`` 片段（可直接粘贴；Windows 路径反斜杠按 JSON 规则转义）::

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

⛔ 无需 ``pip install mcp``、不污染项目 .venv、与已发布的 ``serial-assistant``
   （9 个硬件串口工具）并列、互不干扰；依赖都在 F 盘 / 用户目录，不写系统 Python。

本服务对 mcp 1.x（FastMCP）与 2.x（MCPServer）两种 API 都做了兼容；
若 SDK 路径不对，启动会给出明确中文报错而不是半死不活地挂着。

契约
----
* 工具**从 ``capability_api.list_tools()`` 动态注册**（不手写 N 个重复函数）；
* 每个工具转调 ``capability_api.call(name, params)``，把 ``data`` 转 JSON 字符串
  返回；``ok=False`` 时抛 ``ValueError(error.message)`` 让 MCP 客户端看到失败。
* 详见同目录 ``MCP能力接入说明.md``（P0/P1 分层、28 个能力、只读保证）。
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from typing import Any, Optional

# 项目根加入 sys.path（capability_api.py 在根目录）。用 append 而非 insert(0)：
# 避免把项目内 mcp/ 目录（命名空间包）顶到已安装的 mcp SDK 前面导致 import 错乱。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

import capability_api  # noqa: E402

# mcp SDK：兼容 1.x FastMCP / 2.x MCPServer；全失败给明确中文提示。
try:
    from mcp.server import MCPServer as FastMCP                    # mcp 2.x
except ImportError:
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP       # mcp 2.x 显式
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP                  # mcp 1.x
        except ImportError as _exc:
            raise SystemExit(
                "启动失败：本 python 没找到 mcp SDK。请用「独立 MCP venv + "
                "PYTHONPATH 借 PySide6」的零安装方式启动（见本文件顶部 docstring "
                "的 mcpServers 片段），不要在此 python 里 pip install。\n"
                f"原始错误：{_exc}"
            ) from _exc

mcp = FastMCP("serial-assistant-capabilities")

# list_tools() 里的字符串类型名 → 生成函数签名用的 Python 类型
_TYPE_MAP: dict[str, Any] = {
    "str": str, "int": int, "float": float, "bool": bool,
    "list": list, "dict": dict, "any": Any,
}


def _make_handler(spec: dict):
    """为一个能力造一个带**真实参数签名**的闭包，供 MCP 生成工具 schema。"""
    name = spec["name"]
    params = []
    annotations: dict[str, Any] = {"return": str}
    for field, type_name in spec["params"].items():
        py_type = _TYPE_MAP.get(type_name, Any)
        annotation = Optional[py_type]
        annotations[field] = annotation
        params.append(inspect.Parameter(
            field,
            kind=inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=annotation,
        ))

    def _invoke(**kwargs):
        # 没传的参数是 None，丢掉，让 capability_api 用各能力自己的默认值
        clean = {k: v for k, v in kwargs.items() if v is not None}
        result = capability_api.call(name, clean)
        if not result["ok"]:
            raise ValueError(result["error"]["message"])
        return json.dumps(result["data"], ensure_ascii=False, allow_nan=False)

    # 这几个属性运行时由 inspect.Signature / MCP SDK 读取以生成工具参数 schema。
    # 用 setattr 循环统一挂：既保留全部属性（⛔ 少了 schema 会退化成无参数），
    # 又让 vulture 不再把每个动态属性名误报成 unused attribute（静态看不到读取方）。
    for _attr, _val in (
        ("__name__", name),
        ("__qualname__", name),
        ("__doc__", spec["description"]),
        ("__signature__", inspect.Signature(params, return_annotation=str)),
        ("__annotations__", annotations),
    ):
        setattr(_invoke, _attr, _val)
    return _invoke


for _spec in capability_api.list_tools():
    mcp.add_tool(_make_handler(_spec), name=_spec["name"],
                 description=_spec["description"])


if __name__ == "__main__":
    mcp.run()
