# -*- coding: utf-8 -*-
"""车辆（电池箱结构）档案的**分享**：导出成文件、从文件导入、在线分发与上传。

为什么要有这一层（用户 2026-10-31 要求）：每台车的"逐模组电芯数 / 温度采集数"
都得在设置面板里一格一格填，队里几个人各配一遍、配错了还看不出 —— 把这套结构
存成一个可传递的文件（``.cqv.json``），谁拿到都能一键导进来。

三条通道：
- **本地文件**：``export_to_file`` / ``import_from_file``（离线、最稳）；
- **在线下载**：仓库里的 ``vehicles/index.json`` + 各个 ``*.cqv.json`` 包。
  索引与包都走 **镜像优先 → 直连** 的多源抢跑（复用 ``updater.MIRROR_PREFIXES``
  那套思路：国内直连 raw.githubusercontent.com 基本不通，但镜像一路 200）；
- **在线上传**：GitHub contents API（PUT）。需要用户自己的 token —— 没有 token
  时退回 ``upload_url_for_web()``（浏览器里拖文件上传）。token 用 **Windows DPAPI**
  加密后存注册表（``custom/gh_token_enc``），**绝不明文**；DPAPI 不可用时只活在
  进程内存里（``LAST_TOKEN_ERROR`` 会说明"本次会话有效"）。
- **上传后自动登记索引**：``upload_pack`` 成功后调 ``update_index`` 把包写进
  ``vehicles/index.json``，否则别人在"在线车辆库"里根本看不到这个包。

文件格式（**要能长期演进**，所以每个包都自带 schema）：

    {"schema": 1, "app": "CQUFSAE串口助手", "exported_at": "2026-10-31T11:30:00",
     "vehicles": [{"name": "E74-2026", "cells": [25, 25, 25], "temps": [8, 8, 8]}]}

导入一律**宽松**：schema 缺失/更新、cells 与 temps 不等长、单条坏掉 —— 能导入的
都导入，问题全部记在返回值的 ``errors`` 里给用户看，绝不整包拒绝、也绝不抛异常。

⛔ 代理：**默认直连**，只有环境变量 ``HTTPS_PROXY`` / ``https_proxy`` 存在时才用
（这台机器 Clash 的 7897 时有时无，写死端口必踩空；见 MEMORY.md）。
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import customization

SUFFIX = ".cqv.json"
SCHEMA = 1
APP_NAME = "CQUFSAE串口助手"
INDEX_PATH = "vehicles/index.json"        # 仓库里的索引（相对仓库根）
UPLOAD_DIR = "vehicles"                   # 包放在仓库的这个目录下
DEFAULT_REPO = "Charlie-lee123/CQUFSAEbatteryteam"
WEB_UPLOAD_URL = f"https://github.com/{DEFAULT_REPO}/upload/main/{UPLOAD_DIR}"

PER_SOURCE_S = 2.5                        # 单源超时（多源并行，等最慢的那条没意义）
MAX_MODULES = 64                          # 与 customization 的上限一致（多的截掉）
_UA = ("SerialAssistant-VehicleShare/1.0 "
       "(+https://github.com/Charlie-lee123/CQUFSAEbatteryteam)")

# token 的 QSettings 键 + 给 UI 看的最近一次错误/降级说明
TOKEN_KEY = "custom/gh_token_enc"
ORG, APP = "MimoWorks", "SerialAssistant"
LAST_TOKEN_ERROR = ""                     # 最近一次 token 读写的问题（空串 = 没问题）
_MEMORY_TOKEN = ""                        # DPAPI 不可用时的进程内兜底（不落盘）


# ------------------------------------------------------------------ 仓库 / 网络

def repo_slug() -> str:
    """``owner/repo``（以 version.py 为准，读不到就用写死的常量）。"""
    try:
        from version import OWNER_REPO
        return str(OWNER_REPO)
    except Exception:                     # noqa: BLE001 - 打包裁剪/单测替换都别炸
        return DEFAULT_REPO


def _raw_url(path: str) -> str:
    return (f"https://raw.githubusercontent.com/{repo_slug()}/main/"
            f"{path.lstrip('/')}")


def _mirror_prefixes() -> tuple[str, ...]:
    """镜像前缀表（**唯一出处是 updater.MIRROR_PREFIXES**，这里不复制一份）。"""
    try:
        from updater import MIRROR_PREFIXES
        return tuple(MIRROR_PREFIXES)
    except Exception:                     # noqa: BLE001 - 取不到就退化成直连
        return ()


def _best_prefix() -> str | None:
    """本次会话测出来的最快镜像（检查更新时顺手测的）。"""
    try:
        from updater import best_prefix
        return best_prefix()
    except Exception:                     # noqa: BLE001
        return None


def _mirrored(raw: str) -> tuple[str, ...]:
    """一条 raw 地址 → 源链（最快镜像在前，其余镜像随后，直连兜底）。"""
    prefixes = list(_mirror_prefixes())
    best = _best_prefix()
    if best in prefixes:
        prefixes.remove(best)
        prefixes.insert(0, best)
    return tuple([f"{prefix}{raw}" for prefix in prefixes] + [raw])


def remote_index_urls() -> tuple[str, ...]:
    """在线索引的源链（镜像优先 → GitHub raw 直连）。"""
    return _mirrored(_raw_url(INDEX_PATH))


def _proxy_url() -> str | None:
    """环境变量里的代理（没有就 None = 直连）。⛔ 不写死任何端口。"""
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def _open(url: str, timeout: float, data: bytes | None = None,
          headers: dict | None = None, method: str | None = None):
    """发一个请求（默认**直连**；UA 必须带，GitHub raw 对空 UA 回 403）。"""
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("User-Agent", _UA)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    proxy = _proxy_url()
    handler = (urllib.request.ProxyHandler({"http": proxy, "https": proxy})
               if proxy else urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(handler).open(request, timeout=timeout)


def _host(url: str) -> str:
    try:
        return url.split("/")[2]
    except IndexError:
        return url


def describe_error(exc: BaseException) -> str:
    """异常 → 给用户看的一行说明（HTTP 状态码优先）。"""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"网络不可达（{exc.reason}）"
    return f"{type(exc).__name__}: {exc}"


def _http_detail(exc: urllib.error.HTTPError) -> str:
    """HTTP 错误 + 前 200 字正文（GitHub 的报错原因都在正文里）。"""
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:                     # noqa: BLE001
        body = ""
    text = f"HTTP {exc.code} {getattr(exc, 'reason', '') or ''}".strip()
    return f"{text}：{body[:200]}" if body else text


def _race(urls: tuple[str, ...], total_s: float,
          per_s: float = PER_SOURCE_S) -> tuple[bytes | None, str, str]:
    """多源**并行抢跑**：任一源拿到内容即返回 ``(bytes, 源, "")``。

    全失败返回 ``(None, "", 说明)``。用裸线程而不是线程池：拿到结果就能立刻
    返回，不用等其余源把超时耗完（线程都是 daemon，请求自带超时，不会吊住进程）。
    """
    if not urls:
        return None, "", "没有可用的下载源"
    lock = threading.Lock()
    done = threading.Event()
    box: dict = {}
    errors: list[str] = []

    def worker(url: str) -> None:
        try:
            with _open(url, per_s) as response:
                payload = response.read()
        except Exception as exc:          # noqa: BLE001 - 单源失败就记一笔，等别人
            with lock:
                if not done.is_set():
                    errors.append(f"{_host(url)}：{describe_error(exc)}")
            return
        with lock:
            if done.is_set() or not payload:
                return
            box["data"] = payload
            box["source"] = _host(url)
            done.set()

    for url in urls:
        threading.Thread(target=worker, args=(url,), daemon=True).start()
    done.wait(max(1.0, total_s))
    with lock:
        if box.get("data"):
            return bytes(box["data"]), str(box.get("source") or ""), ""
        detail = "；".join(errors) if errors else "全部源都超时"
    return None, "", detail


# ------------------------------------------------------------------ 导出 / 导入

def _clean_list(raw) -> list[int] | None:
    """JSON 里的一段数字 → int 列表；**不是列表**返回 None（与"空列表"区分开）。"""
    if not isinstance(raw, (list, tuple)):
        return None
    out: list[int] = []
    for value in raw:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            try:
                out.append(int(float(value)))
            except (TypeError, ValueError):
                out.append(0)             # 单个坏值归 0（= 该模组不参与分组）
    return out


def export_payload(names: list[str] | None = None) -> dict:
    """当前档案 → 分享用的 doc（``names`` 为空 = 全部车辆）。"""
    items = customization.vehicles()
    if names:
        wanted = [str(name).strip() for name in names if str(name).strip()]
        items = [item for item in items if item["name"] in wanted]
    return {
        "schema": SCHEMA,
        "app": APP_NAME,
        "exported_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "vehicles": [{"name": item["name"],
                      "cells": list(item["cells"]),
                      "temps": list(item["temps"])} for item in items],
    }


def export_to_file(path: str, names: list[str] | None = None) -> int:
    """把车辆结构写成 ``.cqv.json``，返回导出的车辆数（UTF-8 **无 BOM**）。"""
    doc = export_payload(names)
    target = Path(str(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, indent=1)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text + "\n")
    return len(doc["vehicles"])


def _empty_result(message: str | None = None) -> dict:
    return {"added": 0, "updated": 0, "skipped": 0,
            "errors": [message] if message else []}


def import_payload(doc: dict) -> dict:
    """导入一份 doc，返回 ``{"added", "updated", "skipped", "errors"}``。

    语义：**同名 = 更新**（覆盖结构、计数进 ``updated``），不同名 = 新增。
    用户原有的其它车辆一律保留（``customization.save_vehicle`` 就是"同名覆盖、
    否则追加"，天然不动别人）。

    宽松到什么程度：顶层不是对象、缺 schema、schema 比本版新、cells 与 temps
    不等长、单条坏掉 —— 都不会抛异常，能救的照救，问题全进 ``errors``。
    """
    if not isinstance(doc, dict):
        return _empty_result("文件内容不是 JSON 对象（顶层应该是 {...}）")
    added = updated = skipped = 0
    errors: list[str] = []

    schema = doc.get("schema")
    if schema is None:
        errors.append("缺少 schema 字段：按旧格式尽力导入")
    else:
        version = None
        try:
            version = int(schema)
        except (TypeError, ValueError):
            errors.append(f"schema 不是整数（{schema!r}）：按旧格式尽力导入")
        if version is not None and version > SCHEMA:
            errors.append(f"文件 schema={version} 比本版支持的 {SCHEMA} 新："
                          "只导入能识别的字段，建议升级软件")

    raw = doc.get("vehicles")
    if raw is None:
        raw = doc.get("items")            # 兼容性兜底：别的工具可能叫 items
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, (list, tuple)):
        errors.append("找不到车辆列表（vehicles）：这份文件里没有可导入的内容")
        return {"added": 0, "updated": 0, "skipped": 0, "errors": errors}

    known = {name.strip() for name in customization.vehicle_names()}
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            skipped += 1
            errors.append(f"第 {index} 条不是对象，已跳过")
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            skipped += 1
            errors.append(f"第 {index} 条没有车名，已跳过")
            continue
        cells = _clean_list(entry.get("cells"))
        temps = _clean_list(entry.get("temps"))
        if cells is None and temps is None:
            skipped += 1
            errors.append(f"「{name}」没有 cells/temps，已跳过")
            continue
        cells = (cells or [])[:MAX_MODULES]
        temps = (temps or [])[:MAX_MODULES]
        if len(cells) != len(temps):
            short = min(len(cells), len(temps))
            errors.append(f"「{name}」cells({len(cells)}) 与 temps({len(temps)})"
                          f" 长度不一致：已截断到 {short} 个模组")
            cells, temps = cells[:short], temps[:short]
        if not cells:
            skipped += 1
            errors.append(f"「{name}」一个模组都没有，已跳过")
            continue
        try:
            customization.save_vehicle(name, cells, temps)
        except Exception as exc:          # noqa: BLE001 - 单条失败不拖累整包
            skipped += 1
            errors.append(f"「{name}」写入失败（{describe_error(exc)}）")
            continue
        if name in known:
            updated += 1
        else:
            added += 1
            known.add(name)
    return {"added": added, "updated": updated,
            "skipped": skipped, "errors": errors}


def import_from_file(path: str) -> dict:
    """读一个 ``.cqv.json`` 并导入（文件读不了/JSON 坏了也只返回 errors）。"""
    target = Path(str(path))
    try:
        text = target.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return _empty_result(f"读不了文件（{describe_error(exc)}）")
    if not text.strip():
        return _empty_result("文件是空的，没有可导入的内容")
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return _empty_result(f"JSON 解析失败：{exc}")
    return import_payload(doc)


# ------------------------------------------------------------------ 在线索引 / 下载

def _index_items(doc) -> list[dict] | None:
    """索引 doc → ``[{name, file, modules, note}]``；结构不对返回 None。"""
    if isinstance(doc, dict):
        raw = doc.get("packs")
    elif isinstance(doc, list):
        raw = doc
    else:
        raw = None
    if not isinstance(raw, (list, tuple)):
        return None
    out: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        file = str(entry.get("file") or "").strip()
        if not file:
            continue
        name = str(entry.get("name") or "").strip() or file
        try:
            modules = int(entry.get("modules") or 0)
        except (TypeError, ValueError):
            modules = 0
        out.append({"name": name, "file": file, "modules": modules,
                    "note": str(entry.get("note") or "").strip()})
    return out


def fetch_remote_index(timeout_ms: int = 8000) -> dict:
    """拉在线索引（**阻塞**，多源并行）。返回 ``{"ok", "items", "error", "source"}``。"""
    urls = remote_index_urls()
    total = max(1.0, timeout_ms / 1000.0)
    payload, source, error = _race(urls, total, min(PER_SOURCE_S, total))
    if payload is None:
        return {"ok": False, "items": [], "error": f"索引拉取失败：{error}",
                "source": ""}
    try:
        doc = json.loads(payload.decode("utf-8-sig", errors="replace"))
    except ValueError as exc:
        return {"ok": False, "items": [],
                "error": f"索引不是合法 JSON（{source}：{exc}）", "source": source}
    items = _index_items(doc)
    if items is None:
        return {"ok": False, "items": [],
                "error": f"索引结构不对（{source}：缺 packs 列表）", "source": source}
    return {"ok": True, "items": items, "error": "", "source": source}


def _safe_file(name: str) -> str:
    """车名/文件名 → 安全的 ``*.cqv.json`` 文件名。"""
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name or "").strip()).strip(" .")
    if text.endswith(".json") and not text.endswith(SUFFIX):
        text = text[: -len(".json")]
    if not text.endswith(SUFFIX):
        text += SUFFIX
    return text[:120]


def pack_filename(doc: dict) -> str:
    """一份 doc 上传到仓库时该叫什么名字。"""
    if isinstance(doc, dict):
        given = str(doc.get("file") or "").strip()
        if given:
            return _safe_file(given)
        vehicles = doc.get("vehicles")
        if isinstance(vehicles, list) and vehicles and isinstance(vehicles[0], dict):
            name = str(vehicles[0].get("name") or "").strip()
            if name:
                return _safe_file(name)
    return _safe_file(datetime.now().strftime("pack-%Y%m%d-%H%M"))


def download_remote_pack(entry: dict) -> dict:
    """下载索引里的一个包并直接导入。

    返回 ``{"ok", "added", "updated", "skipped", "errors", "error", "source"}``；
    ``ok`` = 确实导入了东西（新增或更新了车辆）。
    """
    file = ""
    if isinstance(entry, dict):
        file = str(entry.get("file") or "").strip()
        if not file:
            name = str(entry.get("name") or "").strip()
            file = _safe_file(name) if name else ""
    if not file:
        message = "索引条目里没有 file 字段"
        return {"ok": False, "added": 0, "updated": 0, "skipped": 0,
                "errors": [message], "error": message, "source": ""}
    urls = _mirrored(_raw_url(f"{UPLOAD_DIR}/{file}"))
    payload, source, error = _race(urls, 8.0)
    if payload is None:
        return {"ok": False, "added": 0, "updated": 0, "skipped": 0,
                "errors": [f"下载失败：{error}"], "error": f"下载失败：{error}",
                "source": ""}
    try:
        doc = json.loads(payload.decode("utf-8-sig", errors="replace"))
    except ValueError as exc:
        message = f"下载到的包不是合法 JSON（{exc}）"
        return {"ok": False, "added": 0, "updated": 0, "skipped": 0,
                "errors": [message], "error": message, "source": source}
    result = import_payload(doc)
    imported = result["added"] + result["updated"]
    result["ok"] = imported > 0
    result["error"] = "" if imported else (
        "；".join(result["errors"]) or "包里没有可导入的车辆")
    result["source"] = source
    return result


# ------------------------------------------------------------------ token 安全存储
#
# ⛔ token **绝不能明文进注册表**（注册表整盘可读，还会被备份/漫游同步带走）。
#    Windows 自带 DPAPI（``crypt32.CryptProtectData``）：按**当前用户**加密，
#    换账户/换机器都解不开，还不用自己管密钥。全程走 ctypes ——
#    ⛔ 不要用 PowerShell 的 ``Add-Type``（沙箱会拦；见 MEMORY.md 的 .lnk 那条）。
#    DPAPI 不可用时**退回进程内内存**（``save_token`` 返回 False +
#    ``LAST_TOKEN_ERROR`` 说明"本次会话有效"），绝不降级成明文落盘。


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


_CRYPTO: tuple | None = None


def _crypto():
    """惰性加载 ``(crypt32, kernel32)`` 并设好签名（每进程一次）。"""
    global _CRYPTO
    if _CRYPTO is not None:
        return _CRYPTO
    from ctypes import wintypes
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = (
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB))
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = (
        ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB))
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p
    _CRYPTO = (crypt32, kernel32)
    return _CRYPTO


def _dpapi(data: bytes, protect: bool) -> bytes:
    """DPAPI 加/解密（``protect=True`` 加密）。失败抛 OSError。"""
    crypt32, kernel32 = _crypto()
    buffer_in = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(len(data),
                         ctypes.cast(buffer_in, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(blob_in), "SerialAssistant token",
                                     None, None, None, 0, ctypes.byref(blob_out))
        name = "CryptProtectData"
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None,
                                        None, None, 0, ctypes.byref(blob_out))
        name = "CryptUnprotectData"
    if not ok:
        raise OSError(ctypes.get_last_error(), f"{name} 失败")
    try:
        return ctypes.string_at(blob_out.pbData, int(blob_out.cbData))
    finally:
        kernel32.LocalFree(blob_out.pbData)     # DPAPI 用 LocalAlloc，必须自己释放


def _settings():
    from PySide6.QtCore import QSettings
    return QSettings(ORG, APP)


def save_token(token: str) -> bool:
    """把 token 加密后存进注册表（``custom/gh_token_enc``）。空串 = 删除。

    返回 True = 已加密落盘；False = DPAPI 不可用，只存在**本次运行的内存**里
    （原因写在 ``LAST_TOKEN_ERROR``，UI 可以显示成"本次会话有效"）。
    """
    global LAST_TOKEN_ERROR, _MEMORY_TOKEN
    text = str(token or "").strip()
    LAST_TOKEN_ERROR = ""
    if not text:
        clear_token()
        return True
    try:
        blob = _dpapi(text.encode("utf-8"), protect=True)
    except Exception as exc:              # noqa: BLE001 - 系统加密不可用就退内存
        _MEMORY_TOKEN = text
        LAST_TOKEN_ERROR = (f"系统加密不可用（{describe_error(exc)}）："
                            "令牌只在本次运行有效，关闭软件后需重新填写")
        return False
    try:
        settings = _settings()
        settings.setValue(TOKEN_KEY, base64.b64encode(blob).decode("ascii"))
        settings.sync()
    except Exception as exc:              # noqa: BLE001
        _MEMORY_TOKEN = text
        LAST_TOKEN_ERROR = f"令牌存不进配置（{describe_error(exc)}）：本次运行有效"
        return False
    _MEMORY_TOKEN = text
    return True


def load_token() -> str:
    """读回 token（解密失败/不存在 → 空串，原因见 ``LAST_TOKEN_ERROR``）。"""
    global LAST_TOKEN_ERROR
    LAST_TOKEN_ERROR = ""
    raw = ""
    try:
        raw = str(_settings().value(TOKEN_KEY, "") or "").strip()
    except Exception as exc:              # noqa: BLE001
        LAST_TOKEN_ERROR = f"读不了配置里的令牌（{describe_error(exc)}）"
    if not raw:
        return _MEMORY_TOKEN
    try:
        data = base64.b64decode(raw.encode("ascii"), validate=False)
        return _dpapi(data, protect=False).decode("utf-8", errors="replace")
    except Exception as exc:              # noqa: BLE001 - 换账户/配置被拷走都会这样
        LAST_TOKEN_ERROR = (f"已保存的令牌解不开（{describe_error(exc)}）："
                            "请重新填写")
        return _MEMORY_TOKEN


def clear_token() -> None:
    """删掉已保存的 token（配置里的密文 + 内存副本）。"""
    global LAST_TOKEN_ERROR, _MEMORY_TOKEN
    LAST_TOKEN_ERROR = ""
    _MEMORY_TOKEN = ""
    try:
        settings = _settings()
        settings.remove(TOKEN_KEY)
        settings.sync()
    except Exception as exc:              # noqa: BLE001
        LAST_TOKEN_ERROR = f"清不掉已保存的令牌（{describe_error(exc)}）"


# ------------------------------------------------------------------ 上传

def upload_url_for_web() -> str:
    """浏览器上传页（没有 token 时的降级通道：拖文件进去提交 PR）。"""
    return WEB_UPLOAD_URL


def _contents_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_contents(api: str, headers: dict,
                  timeout: float = 10.0) -> tuple[dict | None, str | None, str]:
    """GitHub contents 单文件 → ``(doc, sha, error)``；文件不存在 = ``(None, None, "")``。"""
    try:
        with _open(f"{api}?ref=main", timeout, headers=headers) as response:
            info = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:               # 还不存在 = 正常（首次写）
            return None, None, ""
        return None, None, _http_detail(exc)
    except Exception as exc:              # noqa: BLE001 - 网络/解析异常都当失败
        return None, None, describe_error(exc)
    if not isinstance(info, dict):
        return None, None, "GitHub 返回的内容不是 JSON 对象"
    sha = str(info.get("sha") or "").strip() or None
    doc = None
    raw = str(info.get("content") or "").strip()
    if raw:
        try:
            doc = json.loads(base64.b64decode(raw).decode("utf-8-sig",
                                                          errors="replace"))
        except Exception:                 # noqa: BLE001 - 正文坏了交给调用方判断
            doc = None
    return doc, sha, ""


def _put_contents(api: str, headers: dict, message: str, doc: dict,
                  sha: str | None, timeout: float = 15.0) -> tuple[dict | None, str]:
    """PUT 一个 JSON 文件 → ``(响应体, error)``。"""
    text = json.dumps(doc, ensure_ascii=False, indent=1)
    body = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": "main",
    }
    if sha:
        body["sha"] = sha                # 已存在时必须带 sha，否则 409
    try:
        with _open(api, timeout,
                   data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                   headers=headers, method="PUT") as response:
            return json.loads(response.read().decode("utf-8", errors="replace")), ""
    except urllib.error.HTTPError as exc:
        return None, _http_detail(exc)
    except Exception as exc:              # noqa: BLE001
        return None, describe_error(exc)


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _merge_index(index_doc, pack: dict) -> dict:
    """把一条包登记进索引（**纯函数**，不碰网络；同名替换、老索引里的重名去重）。

    ``index_doc`` 可以是：正常索引 / ``None``（文件还不存在）/ 任意坏结构 ——
    坏结构一律当空索引重来，绝不因为一条脏数据把整个在线库弄丢。
    """
    schema = SCHEMA
    packs_in: list[dict] = []
    if isinstance(index_doc, dict):
        version = _as_int(index_doc.get("schema"))
        if version > 0:
            schema = version
        raw = index_doc.get("packs")
        if isinstance(raw, list):
            packs_in = [dict(item) for item in raw if isinstance(item, dict)]
    name = (str(pack.get("name") or "").strip()
            or str(pack.get("file") or "").strip())
    entry = {
        "name": name,
        "file": str(pack.get("file") or "").strip(),
        "modules": _as_int(pack.get("modules")),
        "note": str(pack.get("note") or "").strip(),
    }
    merged: list[dict] = []
    seen: set[str] = set()
    replaced = False
    for item in packs_in:
        key = str(item.get("name") or item.get("file") or "").strip()
        if key and key in seen:
            continue                      # 老索引里本来就有重名 → 只留第一条
        if key and key == name and not replaced:
            merged.append(entry)          # 同名 = 就地替换（位置不变，下拉顺序稳定）
            replaced = True
        else:
            merged.append(item)
        if key:
            seen.add(key)
    if not replaced:
        merged.append(entry)
    return {"schema": schema,
            "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "packs": merged}


def update_index(pack: dict, token: str) -> dict:
    """把一条包登记进仓库的 ``vehicles/index.json``（GET → 合并 → PUT）。

    没有这个文件（404）就用空壳开张；**已有但内容不是合法 JSON 时拒绝写入**
    （宁可报错，也不能把别人登记的包一次抹掉）。
    返回 ``{"ok", "error", "count", "file"}``；``count`` = 合并后的包总数。
    """
    token = str(token or "").strip()
    if not token:
        return {"ok": False, "error": "还没有填写 GitHub 令牌（token）",
                "count": 0, "file": ""}
    if not isinstance(pack, dict) or not str(pack.get("file") or "").strip():
        return {"ok": False, "error": "索引条目缺少 file 字段",
                "count": 0, "file": ""}
    repo = repo_slug()
    api = f"https://api.github.com/repos/{repo}/contents/{INDEX_PATH}"
    headers = _contents_headers(token)
    doc, sha, error = _get_contents(api, headers)
    if error:
        return {"ok": False, "error": f"索引读取失败：{error}", "count": 0, "file": ""}
    if sha and doc is None:
        return {"ok": False,
                "error": "vehicles/index.json 内容不是合法 JSON，已放弃写入（避免抹掉已有登记）",
                "count": 0, "file": ""}
    merged = _merge_index(doc, pack)
    info, error = _put_contents(api, headers, "更新车辆索引", merged, sha)
    if error:
        return {"ok": False, "error": f"索引写入失败：{error}",
                "count": 0, "file": ""}
    url = ""
    content = info.get("content") if isinstance(info, dict) else None
    if isinstance(content, dict):
        url = str(content.get("html_url") or "")
    return {"ok": True, "error": "", "count": len(merged["packs"]),
            "file": str(pack.get("file") or ""), "url": url}


def _index_entry(doc: dict, file: str) -> dict:
    """一份包 → 索引条目（模组数取第一台车的电芯数，多车时在 note 里说明）。"""
    vehicles = doc.get("vehicles")
    modules = 0
    note = ""
    if isinstance(vehicles, list) and vehicles and isinstance(vehicles[0], dict):
        cells = vehicles[0].get("cells")
        if isinstance(cells, (list, tuple)):
            modules = len(cells)
        if len(vehicles) > 1:
            note = f"含 {len(vehicles)} 台车"
    name = file[: -len(SUFFIX)] if file.endswith(SUFFIX) else file
    return {"name": name, "file": file, "modules": modules, "note": note}


def upload_pack(doc: dict, token: str, overwrite: bool = False) -> dict:
    """用 GitHub contents API 把一个包 PUT 进仓库的 ``vehicles/``，成功后登记索引。

    - ``token`` 空 → 直接报错（UI 引导走 ``upload_url_for_web()``）；
    - 仓库里**已有同名文件且 ``overwrite=False``** → **不写**，返回
      ``{"ok": False, "need_confirm": True, "file": ..., "url": ...}``，由 UI 弹确认；
    - ``overwrite=True`` 才带上 GET 到的 ``sha`` 覆盖；
    - 上传成功会**自动**调 ``update_index``，结果放在 ``"index"`` 字段里 ——
      索引失败不影响"包已上传"这个结论（``ok`` 仍为 True），但错误如实带回。

    返回 ``{"ok", "url", "error", "need_confirm", "file", "index"}``。
    """
    token = str(token or "").strip()
    empty = {"ok": False, "url": "", "need_confirm": False, "file": "", "index": None}
    if not token:
        return dict(empty, error="还没有填写 GitHub 令牌（token），无法直接上传")
    if not isinstance(doc, dict):
        return dict(empty, error="要上传的内容不是 JSON 对象")
    repo = repo_slug()
    file = pack_filename(doc)
    api = f"https://api.github.com/repos/{repo}/contents/{UPLOAD_DIR}/{file}"
    headers = _contents_headers(token)
    web_url = f"https://github.com/{repo}/blob/main/{UPLOAD_DIR}/{file}"
    _doc, sha, error = _get_contents(api, headers)
    if error:
        return dict(empty, file=file, error=error)
    if sha and not overwrite:
        return {"ok": False, "url": web_url, "need_confirm": True, "file": file,
                "index": None,
                "error": f"仓库里已有同名文件 {file}（覆盖会替换服务器上的版本）"}

    info, error = _put_contents(api, headers, f"车辆档案：{file}", doc, sha)
    if error:
        return dict(empty, file=file, error=error)
    content = info.get("content") if isinstance(info, dict) else None
    url = str((content or {}).get("html_url") or "") if isinstance(content, dict) else ""
    if not url:
        url = web_url
    index = update_index(_index_entry(doc, file), token)
    return {"ok": True, "url": url, "error": "", "need_confirm": False,
            "file": file, "index": index}

