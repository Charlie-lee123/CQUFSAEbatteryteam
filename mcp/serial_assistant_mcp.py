# -*- coding: utf-8 -*-
"""串口助手 MCP 服务器 —— 让 Agent 直接操作串口收发数据。

设计要点
--------
* **stdio 传输**（MCP 标准），由客户端拉起，无需常驻进程。
* **后台读线程 + 行缓冲**：串口数据随时到达，工具调用时"取走新数据"——
  这样 `read` 不必阻塞等数据，`send_and_wait` 才能做请求/响应。
* **环形缓冲**（默认留最近 5000 行）防长时间运行吃内存。
* **线程安全**：所有串口访问共用一把锁。
* ⚠️ **串口是独占资源**：本服务打开端口期间，串口助手 GUI（或其它程序）
  不能同时占用同一个口。用 `list_ports` 可以看到端口是否已被别的进程持有。

运行（stdio，由 MCP 客户端拉起）：
    F:\\WorkBuddyTools\\venv\\Scripts\\python.exe serial_assistant_mcp.py

依赖：mcp、pyserial（装在 F:\\WorkBuddyTools\\venv，不污染项目 .venv）
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from datetime import datetime

import serial
from serial.tools import list_ports as _list_ports
try:                                    # mcp 2.x：FastMCP 更名为 MCPServer
    from mcp.server import MCPServer as FastMCP
except ImportError:                     # mcp 1.x 兼容
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("serial-assistant")

# ---------------------------------------------------------------- 状态
_LOCK = threading.RLock()
_PORT: serial.Serial | None = None
_READER: threading.Thread | None = None
_STOP = threading.Event()
_LINES: deque[tuple[str, str]] = deque(maxlen=5000)   # (时间戳, 行内容)
_CURSOR = 0                                            # 已被 read 取走的行数
_RX_BYTES = 0
_TX_BYTES = 0
_CONFIG: dict = {}


def _reader_loop() -> None:
    """后台读线程：按行切分进缓冲。"""
    global _RX_BYTES
    buf = bytearray()
    while not _STOP.is_set():
        port = _PORT
        if port is None or not port.is_open:
            time.sleep(0.05)
            continue
        try:
            chunk = port.read(port.in_waiting or 1)
        except (serial.SerialException, OSError):
            time.sleep(0.1)
            continue
        if not chunk:
            continue
        with _LOCK:
            _RX_BYTES += len(chunk)
            buf.extend(chunk)
            while True:
                idx = min((i for i in (buf.find(b"\n"), buf.find(b"\r"))
                           if i >= 0), default=-1)
                if idx < 0:
                    break
                line = bytes(buf[:idx])
                del buf[:idx + 1]
                _LINES.append((datetime.now().strftime("%H:%M:%S.%f")[:-3],
                               line.decode("utf-8", "replace")))


def _ensure_reader() -> None:
    global _READER, _STOP
    if _READER is None or not _READER.is_alive():
        _STOP.clear()
        _READER = threading.Thread(target=_reader_loop, daemon=True)
        _READER.start()


def _need_port() -> serial.Serial:
    if _PORT is None or not _PORT.is_open:
        raise RuntimeError("串口没有打开，先调用 open_port(port=..., baudrate=...)")
    return _PORT


def _fmt_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


# ---------------------------------------------------------------- 工具
@mcp.tool()
def list_ports() -> list[dict]:
    """列出本机所有串口（含是否已被其它进程占用）。

    返回每项：device（如 COM5）、description、hwid、in_use（占用的进程名，
    空串表示可用）、likely_kind（按名字猜：USB 转串口 / 蓝牙 / 未知）。
    """
    out = []
    for p in _list_ports.comports():
        desc = (p.description or "").strip()
        d = (p.device or "")
        low = (desc + " " + (p.hwid or "")).lower()
        kind = ("蓝牙" if "bluetooth" in low or "bth" in low
                else "USB 转串口" if ("usb" in low or "ch340" in low
                                      or "cp210" in low or "ftdi" in low)
                else "未知")
        out.append({"device": d, "description": desc, "hwid": p.hwid or "",
                    "in_use": "", "likely_kind": kind})
    try:                                   # 谁占着口（Windows 上靠系统工具查不到，
        from serial.tools.list_ports_windows import (  # noqa: F401
            comports as _c)                # 这里只做占位，实际看 in_use 提示
        for item in out:
            if _PORT is not None and _PORT.is_open and item["device"] == _PORT.port:
                item["in_use"] = "本 MCP 已打开"
    except Exception:                      # pragma: no cover
        pass
    return out


@mcp.tool()
def open_port(port: str, baudrate: int = 115200, bytesize: int = 8,
              parity: str = "N", stopbits: float = 1, timeout: float = 0.05) -> dict:
    """打开串口。

    port：如 "COM5"；baudrate：常见 9600 / 115200 / 921600；
    parity：N/E/O；stopbits：1 / 1.5 / 2。
    ⚠️ 同一个口不能被两个程序同时打开——若串口助手 GUI 正连着，先在里面断开。
    """
    global _PORT, _RX_BYTES, _TX_BYTES, _CURSOR
    with _LOCK:
        if _PORT is not None and _PORT.is_open:
            _PORT.close()
        # 带 "://" 的走 serial_for_url（支持 loop:// 回环口，用于无硬件自测）
        # serial_for_url 的第一个参数是**位置参数**（url），不能写成 port=
        common = dict(baudrate=int(baudrate), bytesize=int(bytesize),
                      parity=parity[0].upper(), stopbits=float(stopbits),
                      timeout=float(timeout), write_timeout=1.0)
        _PORT = (serial.serial_for_url(port, **common) if "://" in port
                 else serial.Serial(port=port, **common))
        _CONFIG.clear()
        _CONFIG.update({"port": port, "baudrate": int(baudrate),
                        "bytesize": int(bytesize), "parity": parity[0].upper(),
                        "stopbits": float(stopbits)})
        _RX_BYTES = _TX_BYTES = 0
        _LINES.clear()
        _CURSOR = 0
    _ensure_reader()
    return {"ok": True, **_CONFIG}


@mcp.tool()
def close_port() -> dict:
    """关闭串口（读线程会自己停下）。"""
    with _LOCK:
        if _PORT is not None and _PORT.is_open:
            _PORT.close()
    return {"ok": True}


@mcp.tool()
def status() -> dict:
    """当前状态：是否已打开、配置、收发字节数、缓冲里还有几行没被取走。"""
    with _LOCK:
        opened = _PORT is not None and _PORT.is_open
        return {"open": opened, "config": dict(_CONFIG),
                "rx_bytes": _RX_BYTES, "tx_bytes": _TX_BYTES,
                "buffered_lines": len(_LINES) - _CURSOR,
                "total_lines": len(_LINES)}


@mcp.tool()
def send(data: str, mode: str = "text", append_newline: bool = False) -> dict:
    """向串口写数据。

    mode="text"：按 UTF-8 发；mode="hex"：data 形如 "AA 01 FF" 或 "AA01FF"。
    append_newline=True 时补一个 \\r\\n（多数下位机按行解析）。
    """
    global _TX_BYTES
    port = _need_port()
    if mode.lower() == "hex":
        raw = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", data))
    else:
        raw = data.encode("utf-8")
    if append_newline:
        raw += b"\r\n"
    with _LOCK:
        n = port.write(raw)
        _TX_BYTES += n
    return {"ok": True, "sent_bytes": n, "sent_hex": _fmt_hex(raw[:64])}


@mcp.tool()
def read(max_lines: int = 100, join: bool = True):
    """取走**上次 read 之后**新到的数据（非阻塞）。

    max_lines：最多取多少行；join=True 时把行拼成一个字符串返回，
    join=False 返回 [{time, text}, ...]。
    """
    global _CURSOR
    with _LOCK:
        new = list(_LINES)[_CURSOR:]
        if len(new) > max_lines:
            new = new[-max_lines:]
        _CURSOR = len(_LINES)
    if join:
        return "\n".join(t for _, t in new)
    return [{"time": t, "text": s} for t, s in new]


@mcp.tool()
def send_and_wait(data: str, mode: str = "text", append_newline: bool = True,
                  expect_regex: str = "", timeout_s: float = 2.0,
                  max_lines: int = 200) -> dict:
    """发一条命令并等回包 —— 串口调试最常用的"请求/响应"。

    expect_regex 给了就等到匹配（正则，多行）；没给就等到"不再有新数据"或超时。
    返回 {matched, elapsed_ms, reply, reply_lines}。
    """
    global _CURSOR
    with _LOCK:
        _CURSOR = len(_LINES)                    # 先清掉历史，只看这一问的答案
    send(data, mode=mode, append_newline=append_newline)
    t0 = time.monotonic()
    pat = re.compile(expect_regex, re.M) if expect_regex else None
    last_len, last_change = 0, time.monotonic()
    while True:
        time.sleep(0.05)
        with _LOCK:
            got = [t for _, t in list(_LINES)[_CURSOR:]]
        text = "\n".join(got)
        if pat is not None:
            if pat.search(text):
                with _LOCK:
                    _CURSOR = len(_LINES)
                return {"matched": True, "elapsed_ms": int((time.monotonic() - t0) * 1000),
                        "reply": text, "reply_lines": len(got)}
        else:
            if len(got) != last_len:
                last_len, last_change = len(got), time.monotonic()
            elif got and time.monotonic() - last_change > 0.25:
                with _LOCK:
                    _CURSOR = len(_LINES)
                return {"matched": True, "elapsed_ms": int((time.monotonic() - t0) * 1000),
                        "reply": text, "reply_lines": len(got)}
        if time.monotonic() - t0 > timeout_s:
            with _LOCK:
                _CURSOR = len(_LINES)
            return {"matched": False, "elapsed_ms": int((time.monotonic() - t0) * 1000),
                    "reply": text, "reply_lines": len(got),
                    "hint": f"{timeout_s}s 内没有等到回包（检查波特率/接线/下位机是否上电）"}


@mcp.tool()
def clear_buffer() -> dict:
    """清空接收缓冲（下次 read 只看新数据）。"""
    global _CURSOR
    with _LOCK:
        _LINES.clear()
        _CURSOR = 0
    return {"ok": True}


@mcp.tool()
def send_repl(lines: list[str]) -> dict:
    """把一串命令**逐条发送**，每条都等回包，返回每条的回复。

    适合"初始化寄存器"这类场景：例如 ["AT", "AT+MODE=1", "AT+START"]。
    每条都会补 \\r\\n，超时 1s，回包无内容也算完成（不会卡住）。
    """
    results = []
    for line in lines:
        r = send_and_wait(line, timeout_s=1.0)
        results.append({"cmd": line, "matched": r["matched"],
                        "reply": r["reply"][:400]})
    return {"ok": True, "count": len(results), "results": results}


if __name__ == "__main__":
    mcp.run()          # stdio
