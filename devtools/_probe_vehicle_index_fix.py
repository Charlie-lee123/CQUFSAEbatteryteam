# -*- coding: utf-8 -*-
"""``vehicle_share`` 写后读缓存不一致修复的**独立离线探针**（只读，确定性）。

与 QA 的 ``devtools/_verify_vehicle_index.py`` 相互独立：本探针不改任何产品 / QA
源码，不触真实外网，不开主窗口，不写 QSettings，不跑全量 ``selftest.py``。

QA harness 的用例不覆盖两个面，本探针专门补上：

1. ``download_remote_pack()`` 的残留面 —— 只修 ``fetch_remote_index()`` 会出现
   「索引里能看见车、点下载却拿到 300s 前的旧包」。这里固定「权威源返回新包、
   镜像恒返回旧包、权威源故意比 CDN 慢 120ms」，断言最终导入的是新包。
2. 「刚写过」窗口内的**写后读一致性**（K1 / PRD 语义）—— 用户刚上传成功，进程
   自己刚写进去的包是已知事实；90s TTL 内**任何一次**刷新，即使 contents API
   不可达、镜像仍是 ``packs=0`` 旧缓存，也不该把在线库显示成空的。这里覆盖
   「第1次刷新 API OK → 第2次刷新 API 挂」「连续两次刷新 API 都挂」等序列，
   并反向验证「TTL 是唯一闸门：reset / 过期后不再回填」+「权威源真返回有效
   文档时不拿本地快照盖掉」。⛔ 早期的「一次性消费」断言是错的，已按 PRD 修正。

其余检查逐条对齐 team-lead 的 AC：权威源优先 / 上传后立刷 / 权威源全挂降级 /
时间预算 / token 零泄露 / UA 与 ``_open`` 单一出口 / MIRROR_PREFIXES 单一出处 /
零 QSettings 写入。

用法（项目根目录）：

    .venv/Scripts/python.exe devtools/_probe_vehicle_index_fix.py

产物：``_check/vehicle_index_fix_probe.txt``（真实输出全文 + IS_PASS 结论）。
"""

from __future__ import annotations

import base64
import contextlib
import email.message
import hashlib
import io
import json
import platform
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vehicle_share as vs  # noqa: E402 - 需在 sys.path 注入之后

CHECK = ROOT / "_check"
CHECK.mkdir(parents=True, exist_ok=True)
OUT = CHECK / "vehicle_index_fix_probe.txt"

# 合成 token：刻意长得像真 GitHub PAT，用来检出「token 被拼进 URL / 日志 / 异常」。
# 绝不读取用户真实 token（不调用 load_token / _settings）。
SENTINEL = "ghp_PROBESENTINEL9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1"

STALE = {"schema": 1, "updated_at": "2026-11-09T20:00:00", "packs": []}
FRESH = {
    "schema": 1,
    "updated_at": "2026-11-09T21:00:00",
    "packs": [
        {"name": "E74-2026", "file": "E74-2026.cqv.json", "modules": 3, "note": ""},
        {"name": "A08-2026", "file": "A08-2026.cqv.json", "modules": 3, "note": ""},
    ],
}
PACK_NEW = {"name": "QA-2026", "file": "QA-2026.cqv.json",
            "modules": 2, "note": "刚上传"}

PACK_FRESH = {
    "schema": 1, "app": vs.APP_NAME, "exported_at": "2026-11-09T23:00:00",
    "vehicles": [{"name": "FRESH-CAR", "cells": [25, 25, 25],
                  "temps": [8, 8, 8]}],
}
PACK_STALE = {
    "schema": 1, "app": vs.APP_NAME, "exported_at": "2026-11-09T20:00:00",
    "vehicles": [{"name": "STALE-CAR", "cells": [10, 10],
                  "temps": [4, 4]}],
}

RESULT_KEYS = {"ok", "items", "error", "source"}
DOWNLOAD_KEYS = {"ok", "added", "updated", "skipped", "errors", "error", "source"}
ITEM_KEYS = {"name", "file", "modules", "note"}

_LINES: list[str] = []


def emit(text: str = "") -> None:
    _LINES.append(str(text))
    print(text)


# ------------------------------------------------------------------ 离线假网络


def _msg(headers: dict) -> email.message.Message:
    message = email.message.Message()
    for key, value in headers.items():
        message[key] = str(value)
    return message


class _Resp:
    """``with _open(...) as r: r.read()`` 需要的最小响应对象。"""

    def __init__(self, body: bytes, headers: dict | None = None,
                 url: str = "") -> None:
        self._body = bytes(body)
        self.status = 200
        self.code = 200
        self.headers = _msg(headers or {})
        self.url = url

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *args) -> bool:
        return False

    def read(self, *args) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def info(self) -> email.message.Message:
        return self.headers


class FakeNet:
    """确定性「服务器」：权威源可延迟 / 全挂，镜像恒返回冻结的旧内容。

    - 权威源（``api.github.com`` 或路径含 ``/contents/``）返回 ``{content: b64,
      encoding, sha}`` 包装，解码后是 ``auth_doc``；
    - 镜像 / raw 一律返回 ``mirror_doc`` 裸 JSON，附 ``max-age=300``；
    - PUT 解析 base64 更新 ``auth_doc``，模拟「上传后权威源立刻反映写入」。
    """

    def __init__(self, auth_doc: dict, mirror_doc: dict, *,
                 auth_delay: float = 0.0, auth_fail: str = "",
                 mirror_fail: bool = False) -> None:
        self.auth_doc = auth_doc
        self.mirror_doc = mirror_doc
        self.auth_delay = auth_delay
        self.auth_fail = auth_fail          # "" | "timeout" | "http500"
        self.mirror_fail = mirror_fail
        self.sha = "deadbeef0000000000000000000000000000"
        self.requests: list[dict] = []
        self.by_kind = {"authoritative": 0, "mirror": 0}
        self.exception_texts: list[str] = []
        self.put_count = 0
        self.put_errors: list[str] = []

    @staticmethod
    def classify(url: str) -> str:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        if (host == "api.github.com" or host.endswith(".api.github.com")
                or "/contents/" in parts.path):
            return "authoritative"
        return "mirror"

    def _wrapper(self, doc: dict) -> dict:
        text = json.dumps(doc, ensure_ascii=False, indent=1)
        return {
            "name": "index.json", "path": "vehicles/index.json",
            "sha": self.sha, "size": len(text.encode("utf-8")),
            "type": "file", "encoding": "base64",
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        }

    def _auth_error(self, url: str) -> Exception:
        if self.auth_fail == "http500":
            return urllib.error.HTTPError(
                url, 500, "Internal Server Error",
                _msg({"Content-Type": "application/json"}),
                io.BytesIO(b'{"message":"Server Error"}'))
        return urllib.error.URLError("simulated contents API timeout")

    def _put(self, url: str, payload) -> _Resp:
        self.put_count += 1
        try:
            body = json.loads(bytes(payload or b"").decode("utf-8"))
            raw = base64.b64decode(str(body.get("content") or ""))
            doc = json.loads(raw.decode("utf-8-sig", errors="replace"))
            if isinstance(doc, dict):
                self.auth_doc = doc
        except Exception as exc:           # noqa: BLE001 - 假服务器自己记一笔
            self.put_errors.append(f"{type(exc).__name__}: {exc}")
        response = {
            "content": {
                "name": "index.json", "path": "vehicles/index.json",
                "sha": "newsha",
                "html_url": ("https://github.com/Charlie-lee123/"
                             "CQUFSAEbatteryteam/blob/main/vehicles/index.json"),
            },
            "commit": {"sha": "newsha"},
        }
        return _Resp(json.dumps(response).encode("utf-8"), url=url)

    def handle(self, fullurl, data=None, timeout=None) -> _Resp:
        request = fullurl if isinstance(fullurl, urllib.request.Request) else None
        url = request.full_url if request is not None else str(fullurl)
        method = (request.get_method() if request is not None
                  else ("POST" if data else "GET"))
        headers = ({key.lower(): value for key, value in request.header_items()}
                   if request is not None else {})
        payload = (request.data if request is not None and request.data is not None
                   else data)
        self.requests.append({"url": url, "method": method,
                              "headers": dict(headers), "timeout": timeout})
        kind = self.classify(url)
        self.by_kind[kind] += 1
        if kind == "authoritative":
            if self.auth_delay:
                time.sleep(self.auth_delay)
            if self.auth_fail:
                exc = self._auth_error(url)
                self.exception_texts.append(f"url={url} :: {exc!r}")
                raise exc
            if method == "PUT":
                return self._put(url, payload)
            return _Resp(json.dumps(self._wrapper(self.auth_doc),
                                    ensure_ascii=False).encode("utf-8"), url=url)
        if self.mirror_fail:
            exc = urllib.error.URLError("simulated mirror network failure")
            self.exception_texts.append(f"url={url} :: {exc!r}")
            raise exc
        return _Resp(json.dumps(self.mirror_doc, ensure_ascii=False).encode("utf-8"),
                     url=url,
                     headers={"Cache-Control": "max-age=300", "Age": "137"})


_ACTIVE: FakeNet | None = None
_ALL_REQUESTS: list[dict] = []
_ALL_EXCEPTIONS: list[str] = []
_ESCAPES = [0]

# `_gather_sources` / `_race` 起的都是 daemon 线程：主流程拿到结果就返回，迟到的
# 线程还可能在 install() 退出后继续请求。补丁整进程常驻 + 常驻离线 sink，保证这些
# 迟到请求也只打到假服务器，绝不落到真实外网（与 QA harness 的
# ensure_network_patched「只装一次、整进程不卸」同一手法）。
_SINK = FakeNet(json.loads(json.dumps(STALE)), json.loads(json.dumps(STALE)))


def _guarded_open(self, fullurl, data=None, timeout=None, *args, **kwargs):
    """进程级 fail-closed：没有活动 FakeNet 就报错，绝不放行真实外网。"""
    fake = _ACTIVE
    if fake is None:
        _ESCAPES[0] += 1
        raise RuntimeError("离线探针禁止真实网络请求（没有活动的 FakeNet）")
    return fake.handle(fullurl, data, timeout)


@contextlib.contextmanager
def install(fake: FakeNet):
    global _ACTIVE
    _ACTIVE = fake
    try:
        yield fake
    finally:
        _ACTIVE = _SINK                # 不置 None：迟到线程继续被离线 sink 吸收
        _ALL_REQUESTS.extend(fake.requests)
        _ALL_EXCEPTIONS.extend(fake.exception_texts)


class _CustomizationSpy:
    """替换 ``customization`` 的写接口：纯 Python 记录，⛔ 绝不碰 QSettings。"""

    def __init__(self) -> None:
        self.saved: list[tuple[str, list[int], list[int]]] = []

    def vehicle_names(self) -> list[str]:
        return [name for name, _cells, _temps in self.saved]

    def save_vehicle(self, name, cells, temps) -> None:
        self.saved.append((str(name), list(cells), list(temps)))

    def reset(self) -> None:
        self.saved.clear()

    def names(self) -> list[str]:
        return [name for name, _cells, _temps in self.saved]


SPY = _CustomizationSpy()
_RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    ok = bool(ok)
    _RESULTS.append((label, ok, detail))
    emit(f"  [{'PASS' if ok else 'FAIL'}] {label}"
         + (f" — {detail}" if detail else ""))
    return ok


def section(title: str) -> None:
    emit()
    emit("-" * 92)
    emit(title)
    emit("-" * 92)


def clean_state() -> None:
    """每条独立检查前清掉进程内标记与导入记录，避免互相污染。"""
    vs.reset_written_marker()
    SPY.reset()


# ------------------------------------------------------------------ 检查项


def check_contract() -> None:
    section("[1] 源链契约：MIRROR_PREFIXES 单一出处 + raw 直连兜底（AC-09）")
    clean_state()
    urls = list(vs.remote_index_urls())
    updater_mod = sys.modules.get("updater")
    if updater_mod is None:
        import importlib
        updater_mod = importlib.import_module("updater")
    custom = ("https://probe-alpha.invalid/", "https://probe-beta.invalid/")
    with mock.patch.object(updater_mod, "MIRROR_PREFIXES", custom):
        patched = list(vs.remote_index_urls())
    joined = " ".join(patched)
    check("remote_index_urls() 非空且末位是 raw 直连",
          bool(urls) and urls[-1].startswith("https://raw.githubusercontent.com/"),
          f"{len(urls)} 条，末位={urls[-1][:60] if urls else '-'}...")
    check("monkeypatch MIRROR_PREFIXES 后源链跟着变",
          "probe-alpha.invalid" in joined and "probe-beta.invalid" in joined
          and "ghfast.top" not in joined,
          f"patched={len(patched)} 条")
    check("源链原始返回值不含 api.github.com（契约保持稳定）",
          not any("api.github.com" in u for u in urls))


def check_authoritative_wins() -> None:
    section("[2] 权威源优先且比 CDN 慢 120ms 仍胜出（AC-01 / AC-03）")
    clean_state()
    net = FakeNet(json.loads(json.dumps(FRESH)), json.loads(json.dumps(STALE)),
                  auth_delay=0.12)
    with install(net):
        started = time.perf_counter()
        result = vs.fetch_remote_index()
        elapsed = time.perf_counter() - started
        time.sleep(0.15)                 # 等 daemon 线程收尾，计数才准
    names = sorted(item["name"] for item in (result.get("items") or []))
    check("items 恰好是 E74-2026 / A08-2026",
          result.get("ok") is True and names == ["A08-2026", "E74-2026"],
          f"ok={result.get('ok')} names={names} elapsed={elapsed:.3f}s")
    check("每项 exactly {name,file,modules,note} 且 file 以 .cqv.json 结尾",
          all(set(item) == ITEM_KEYS and item["file"].endswith(".cqv.json")
              for item in (result.get("items") or [])),
          f"items={len(result.get('items') or [])}")
    check("error 为空、source 指向权威源",
          result.get("error") == "" and "api.github.com" in str(result.get("source")),
          f"source={result.get('source')}")
    check("读链路确实访问了权威源 ≥1 次",
          net.by_kind["authoritative"] >= 1,
          f"权威源={net.by_kind['authoritative']} 镜像={net.by_kind['mirror']}")
    check("镜像/raw 也被并行尝试（降级链未丢）",
          net.by_kind["mirror"] >= 1)


def check_upload_then_refresh() -> None:
    section("[3] 上传成功后立刻刷新不返回空（AC-02）")
    clean_state()
    net = FakeNet(json.loads(json.dumps(STALE)), json.loads(json.dumps(STALE)))
    with install(net):
        uploaded = vs.update_index(PACK_NEW, SENTINEL)
        result = vs.fetch_remote_index()
        time.sleep(0.15)
    names = [item["name"] for item in (result.get("items") or [])]
    files = [item["file"] for item in (result.get("items") or [])]
    check("update_index() 成功且真的发了 PUT",
          uploaded.get("ok") is True and net.put_count == 1,
          f"ok={uploaded.get('ok')} count={uploaded.get('count')} PUT={net.put_count}")
    check("紧接着刷新 items 非空",
          bool(result.get("items")) and result.get("ok") is True,
          f"items={len(result.get('items') or [])} source={result.get('source')}")
    check("能看到刚登记的 QA-2026 与其 .cqv.json 文件",
          "QA-2026" in names and "QA-2026.cqv.json" in files,
          f"names={names}")


def check_degrade() -> None:
    section("[4] 权威源全挂 → 优雅降级（AC-04）")
    clean_state()
    for mode in ("timeout", "http500"):
        net = FakeNet(json.loads(json.dumps(FRESH)),
                      json.loads(json.dumps(STALE)), auth_fail=mode)
        with install(net):
            try:
                result = vs.fetch_remote_index(timeout_ms=1500)
                errored = None
            except Exception as exc:      # noqa: BLE001 - 抛异常即判失败
                result, errored = {}, exc
            time.sleep(0.15)
        check(f"{mode}: 不抛异常", errored is None,
              repr(errored) if errored else "")
        check(f"{mode}: 返回干净的 4 键结构",
              isinstance(result, dict) and RESULT_KEYS.issubset(result)
              and isinstance(result.get("items"), list)
              and isinstance(result.get("error"), str),
              f"keys={sorted(result) if isinstance(result, dict) else '-'}")
        check(f"{mode}: 至少尝试 1 条镜像/raw",
              net.by_kind["mirror"] >= 1,
              f"镜像={net.by_kind['mirror']}")
        check(f"{mode}: items 不漏 base64 / contents 包装 / 嵌套结构",
              all(isinstance(item, dict) and set(item) == ITEM_KEYS
                  for item in (result.get("items") or [])))


def check_budget() -> None:
    section("[5] 全源失败的时间预算（AC-05）")
    clean_state()
    net = FakeNet(json.loads(json.dumps(STALE)), json.loads(json.dumps(STALE)),
                  auth_fail="timeout", mirror_fail=True)
    with install(net):
        started = time.perf_counter()
        result = vs.fetch_remote_index(timeout_ms=1000)
        elapsed = time.perf_counter() - started
    check("timeout_ms=1000 墙钟 < 4s 且 ok=False",
          elapsed < 4.0 and result.get("ok") is False,
          f"elapsed={elapsed:.3f}s ok={result.get('ok')}")
    check("返回结构干净（items=[]、error 非空）",
          result.get("items") == [] and isinstance(result.get("error"), str)
          and bool(result.get("error")),
          f"error={str(result.get('error'))[:60]}")


def check_token() -> None:
    section("[6] token 零泄露（AC-08）")
    clean_state()
    net = FakeNet(json.loads(json.dumps(STALE)), json.loads(json.dumps(STALE)))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        with install(net):
            first = vs.update_index(PACK_NEW, SENTINEL)
            net.auth_fail = "http500"
            second = vs.update_index(PACK_NEW, SENTINEL)
            result = vs.fetch_remote_index(timeout_ms=1200)
            time.sleep(0.15)
    urls = [record["url"] for record in net.requests]
    blob = "\n".join(
        urls
        + [str(first), str(second), str(result), str(first.get("error", "")),
           str(second.get("error", "")), str(result.get("error", "")),
           vs.LAST_TOKEN_ERROR, out.getvalue(), err.getvalue()]
        + net.exception_texts)
    check("URL / 日志 / 异常 / 返回文本里都没有 token",
          SENTINEL not in blob and "ghp_" not in blob,
          f"请求数={len(urls)}")
    check("URL query 不含 access_token / token= / bearer / authorization",
          all(not any(word in url.lower() for word in
                      ("access_token", "token=", "bearer", "authorization",
                       "ghp_", "github_pat_"))
              for url in urls))
    check("正向对照：写链路确实带 Authorization 头（用例有效）",
          any(SENTINEL in str(r["headers"].get("authorization", ""))
              for r in net.requests))
    check("读链路请求不带 Authorization（读公开索引不需要 token）",
          not any("authorization" in r["headers"] and r["method"] == "GET"
                  and "_=" in r["url"] for r in net.requests))


def check_write_window_consistency() -> None:
    section("[7] 「刚写过」窗口内每次刷新都能看到刚登记的包（K1 / PRD 语义）")

    # 场景 A（QA P1）：上传 → 第1次刷新(API OK) → TTL 内第2次刷新(API 挂/CDN 空)
    clean_state()
    net_a = FakeNet(json.loads(json.dumps(STALE)),
                    json.loads(json.dumps(STALE)))
    with install(net_a):
        up_a = vs.update_index(PACK_NEW, SENTINEL)
        r1 = vs.fetch_remote_index()
        net_a.auth_fail = "timeout"       # 第二次刷新时 API 突然不可达
        r2 = vs.fetch_remote_index()
        time.sleep(0.15)
    check("A 上传成功且第1次刷新(API OK) 看到刚登记的包",
          up_a.get("ok") is True and any(
              item["name"] == "QA-2026" for item in (r1.get("items") or [])),
          f"r1 items={[i['name'] for i in (r1.get('items') or [])]} "
          f"source={r1.get('source')}")
    check("A TTL 内第2次刷新(API 挂/CDN packs=0) 仍看到 QA-2026（不再掉回空库）",
          any(item["name"] == "QA-2026" for item in (r2.get("items") or []))
          and r2.get("source") == "本地（刚上传）",
          f"r2 items={[i['name'] for i in (r2.get('items') or [])]} "
          f"source={r2.get('source')}")
    check("A 第2次刷新仍是干净 4 键结构（不是 ok=False 的假失败）",
          RESULT_KEYS.issubset(r2) and r2.get("ok") is True
          and all(set(i) == ITEM_KEYS for i in (r2.get("items") or [])))

    # 场景 B（QA P3）：上传 → 连续两次刷新 API 都挂 → 两次都非空
    clean_state()
    net_b = FakeNet(json.loads(json.dumps(STALE)),
                    json.loads(json.dumps(STALE)))
    with install(net_b):
        up_b = vs.update_index(PACK_NEW, SENTINEL)
        net_b.auth_fail = "timeout"
        b1 = vs.fetch_remote_index()
        b2 = vs.fetch_remote_index()
        time.sleep(0.15)
    check("B 连续两次刷新 API 全挂，两次都非空（资格不消费归零）",
          up_b.get("ok") is True
          and any(i["name"] == "QA-2026" for i in (b1.get("items") or []))
          and any(i["name"] == "QA-2026" for i in (b2.get("items") or [])),
          f"b1={[i['name'] for i in (b1.get('items') or [])]} "
          f"b2={[i['name'] for i in (b2.get('items') or [])]}")

    # 场景 C：TTL / reset 是唯一闸门 —— QA 侧在用例边界调 reset_written_marker()
    # 做隔离后，权威全挂 + 镜像空必须回落成干净空结果，不凭空造库
    clean_state()
    net_c = FakeNet(json.loads(json.dumps(STALE)),
                    json.loads(json.dumps(STALE)))
    with install(net_c):
        vs.update_index(PACK_NEW, SENTINEL)
        vs.reset_written_marker()         # 模拟 harness setUp 的测试隔离
        net_c.auth_fail = "timeout"
        rc = vs.fetch_remote_index()
        time.sleep(0.15)
    check("C 标记 reset/TTL 到期后：权威全挂 + 镜像空 → 干净 items=[]，不注入",
          rc.get("items") == [] and RESULT_KEYS.issubset(rc)
          and isinstance(rc.get("items"), list),
          f"items={len(rc.get('items') or [])} source={rc.get('source')}")

    # 场景 D：not has_api 门仍生效 —— 权威源真给出有效文档时不拿本地快照盖掉
    clean_state()
    net_w = FakeNet(json.loads(json.dumps(STALE)),
                    json.loads(json.dumps(STALE)))
    with install(net_w):
        vs.update_index(PACK_NEW, SENTINEL)   # arms marker（刚写 QA-2026）
    net_d = FakeNet(json.loads(json.dumps(FRESH)),
                    json.loads(json.dumps(STALE)))
    with install(net_d):
        rd = vs.fetch_remote_index()
        time.sleep(0.15)
    check("D 权威源真返回有效新索引时以权威为准（not has_api 门不盖掉远端）",
          rd.get("ok") is True
          and sorted(i["name"] for i in (rd.get("items") or []))
          == ["A08-2026", "E74-2026"]
          and "api.github.com" in str(rd.get("source")),
          f"items={[i['name'] for i in (rd.get('items') or [])]} "
          f"source={rd.get('source')}")


def check_download_residual() -> None:
    section("[8] download_remote_pack() 残留面：权威新包 vs CDN 旧包（QA harness 不覆盖）")
    clean_state()
    net = FakeNet(json.loads(json.dumps(PACK_FRESH)),
                  json.loads(json.dumps(PACK_STALE)), auth_delay=0.12)
    with install(net):
        result = vs.download_remote_pack({"file": "probe.cqv.json"})
        time.sleep(0.15)
    imported = SPY.names()
    check("返回结构完整（下载 + 导入一步到位）",
          isinstance(result, dict) and DOWNLOAD_KEYS.issubset(result),
          f"keys={sorted(result) if isinstance(result, dict) else '-'}")
    check("导入的是权威源的新车 FRESH-CAR",
          result.get("ok") is True and "FRESH-CAR" in imported,
          f"imported={imported} source={result.get('source')}")
    check("没有导入 CDN 的 300s 旧车 STALE-CAR",
          "STALE-CAR" not in imported, f"imported={imported}")
    check("权威源比 CDN 慢 120ms 仍被优先（不是先到先赢）",
          net.by_kind["authoritative"] >= 1 and net.by_kind["mirror"] >= 1
          and "FRESH-CAR" in imported,
          f"权威源={net.by_kind['authoritative']} 镜像={net.by_kind['mirror']}")
    check("包下载也访问了权威源（不是只修索引）",
          net.by_kind["authoritative"] >= 1)

    # 权威源全挂 → 仍退回镜像链，不抛异常、结构干净
    clean_state()
    net_down = FakeNet(json.loads(json.dumps(PACK_FRESH)),
                       json.loads(json.dumps(PACK_STALE)), auth_fail="timeout")
    with install(net_down):
        try:
            degraded = vs.download_remote_pack({"file": "probe.cqv.json"})
            errored = None
        except Exception as exc:          # noqa: BLE001
            degraded, errored = {}, exc
        time.sleep(0.15)
    check("包下载权威源全挂时不抛异常、退回镜像链",
          errored is None and DOWNLOAD_KEYS.issubset(degraded)
          and net_down.by_kind["mirror"] >= 1,
          f"imported={SPY.names()} mirror={net_down.by_kind['mirror']}")
    check("包下载链路不把 token 拼进 URL",
          all(SENTINEL not in r["url"] and "token=" not in r["url"].lower()
              for r in net_down.requests))


def check_io_channel() -> None:
    section("[9] 统一 I/O 出口 / UA / 零真实外网 / 零 QSettings 写入（AC-10/11/12）")
    check("所有请求都带 User-Agent（即都走 vehicle_share._open）",
          bool(_ALL_REQUESTS)
          and all(vs._UA in str(r["headers"].get("user-agent", ""))
                  for r in _ALL_REQUESTS),
          f"累计请求={len(_ALL_REQUESTS)}")
    check("没有任何请求逃出离线假网络（fail-closed 未被触发）",
          _ESCAPES[0] == 0, f"escapes={_ESCAPES[0]}")
    check("每个请求都带超时参数（单源超时语义保留）",
          all(r["timeout"] is not None for r in _ALL_REQUESTS))
    check("异常文本里不含 token",
          all(SENTINEL not in text for text in _ALL_EXCEPTIONS),
          f"异常数={len(_ALL_EXCEPTIONS)}")


def main() -> int:
    """装好守卫 → 跑全部检查 → 写产物 → 返回 0/1。"""
    emit("=" * 92)
    emit("vehicle_share 写后读缓存不一致修复 — 独立离线探针（确定性，不触外网）")
    emit("=" * 92)
    emit(f"时间       : {datetime.now().isoformat(timespec='seconds')}")
    emit(f"Python     : {platform.python_version()}  项目: {ROOT}")
    emit(f"vehicle_share.py md5 : "
         f"{hashlib.md5((ROOT / 'vehicle_share.py').read_bytes()).hexdigest()}")
    emit(f"repo_slug  : {vs.repo_slug()}   INDEX_PATH: {vs.INDEX_PATH}")
    emit(f"UA         : {vs._UA}")

    # 进程级网络补丁（整轮常驻，避免 daemon 线程在 teardown 后打真实外网）
    patcher_net = mock.patch.object(urllib.request.OpenerDirector, "open",
                                    new=_guarded_open)
    patcher_net.start()

    # QSettings / customization 守卫：任何写配置都会抛，读链路不得触发
    class _Guard(RuntimeError):
        pass

    guards = [
        mock.patch.object(vs, "_settings",
                          side_effect=_Guard("读链路碰了 QSettings")),
        mock.patch.object(vs, "save_token",
                          side_effect=_Guard("读链路调用了 save_token")),
        mock.patch.object(vs, "clear_token",
                          side_effect=_Guard("读链路调用了 clear_token")),
        mock.patch.object(vs, "load_token",
                          side_effect=_Guard("读链路调用了 load_token")),
    ]
    custom_mod = sys.modules.get("customization")
    if custom_mod is not None:
        guards.append(mock.patch.object(custom_mod, "save_vehicle",
                                        new=SPY.save_vehicle))
        guards.append(mock.patch.object(custom_mod, "vehicle_names",
                                        new=SPY.vehicle_names))
    for guard in guards:
        guard.start()

    guard_fired: list[str] = []
    try:
        runners = (
            check_contract,
            check_authoritative_wins,
            check_upload_then_refresh,
            check_degrade,
            check_budget,
            check_token,
            check_write_window_consistency,
            check_download_residual,
            check_io_channel,
        )
        for runner in runners:
            try:
                runner()
            except _Guard as exc:
                guard_fired.append(f"{runner.__name__}: {exc}")
                check(f"{runner.__name__} 未触发 QSettings 守卫", False, str(exc))
            except Exception as exc:       # noqa: BLE001 - 单条炸了也要出报告
                check(f"{runner.__name__} 未抛异常", False,
                      f"{type(exc).__name__}: {exc}")
        check("全程零 QSettings / token 写接口调用", not guard_fired,
              "；".join(guard_fired) or "0 次触发")
    finally:
        for guard in reversed(guards):
            guard.stop()
        # ⛔ 网络补丁**整进程不卸**（刻意不调用 patcher_net.stop()）：让收尾前/后的
        #    迟到 daemon 线程继续被 _SINK 吸收，而不是 unpatch 之后打到真实外网。
        #    patcher_net 由 mock 内部的活动补丁表持有，进程退出时才失效。
        vs.reset_written_marker()

    time.sleep(0.20)                     # 让 _race / _gather_sources 的迟到线程收尾
    emit()
    emit("=" * 92)
    passed = sum(1 for _label, ok, _detail in _RESULTS if ok)
    total = len(_RESULTS)
    for label, ok, detail in _RESULTS:
        if not ok:
            emit(f"  FAILED: {label} — {detail}")
    emit(f"Total: {total} | Passed: {passed} | Failed: {total - passed}")
    verdict = "IS_PASS: YES" if passed == total else "IS_PASS: NO"
    emit(verdict)
    emit("=" * 92)
    OUT.write_text("\n".join(_LINES) + "\n", encoding="utf-8")
    emit(f"产物: {OUT}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
