"""在线车辆库「上传成功但刷新为空」的独立复现 + 回归用例（只读验收脚本）。

⛔ 铁律（本脚本自我约束，改动前先读）：
- 只 import / 调用 ``vehicle_share`` 这一个产品模块；绝不 import ``main_window``、
  绝不构造 QMainWindow / QApplication、绝不打开主窗口、绝不启动第二个 exe；
- 不改任何产品源码、不 git、不打包、不动 ``发布/`` 与 ``dist*``、不跑全量
  ``selftest.py``（会污染 QSettings）；
- 复现段（``repro``）是**唯一**接触真实网络的地方，全部是 GET，带 User-Agent，
  代理沿用 ``vehicle_share._proxy_url()``（即环境 HTTPS_PROXY）；
- 回归段（``test``）全程离线：在 ``urllib.request.OpenerDirector.open`` 层拦截
  所有 HTTP，因此无论修复走 ``vehicle_share._open`` 还是裸 ``urlopen`` 都不会打到外网；
- 读链路不得写 QSettings：``setUp`` 会把 ``save_token`` / ``clear_token`` /
  ``customization.save_vehicle`` 等写接口换成会抛异常的守卫。

用法（项目根目录执行）：

    .venv/Scripts/python.exe devtools/_verify_vehicle_index.py repro
    .venv/Scripts/python.exe devtools/_verify_vehicle_index.py test
    .venv/Scripts/python.exe devtools/_verify_vehicle_index.py report
    .venv/Scripts/python.exe devtools/_verify_vehicle_index.py all   # 默认

产物统一写 ``_check/``：
    vehicle_index_repro.txt / .json   真实网络复现证据
    vehicle_index_regression.txt      离线回归用例运行输出
    vehicle_index_report.txt          基线结论 + 验收标准清单 + 踩坑清单
"""

from __future__ import annotations

import base64
import contextlib
import copy
import email.message
import io
import json
import logging
import re
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 唯一被 import / 调用的产品模块（放在 sys.path 注入之后）
import vehicle_share as vs

CHECK = ROOT / "_check"
CHECK.mkdir(parents=True, exist_ok=True)

# 复现段统一走 vehicle_share._open()，UA 由它自动加上（不要自己拼一份再声称用了）。
PROBE_UA = vs._UA
PROBE_TIMEOUT_S = 6.0

# 合成 token：刻意长得像真 GitHub PAT，用来检出「token 被拼进 URL / 日志 / 异常」。
# 绝不读取用户真实 token（不调用 load_token / _settings）。
SENTINEL_TOKEN = "ghp_QASENTINEL7f3c9b2a1d0e6f5c4b3a291807f6e5d4c3b2a1"

STALE_DOC = {"schema": 1, "updated_at": "2026-11-09T20:00:00", "packs": []}
FRESH_DOC = {
    "schema": 1,
    "updated_at": "2026-11-09T21:00:00",
    "packs": [
        {"name": "E74-2026", "file": "E74-2026.cqv.json", "modules": 3, "note": ""},
        {"name": "A08-2026", "file": "A08-2026.cqv.json", "modules": 3, "note": ""},
    ],
}
PACK_NEW = {"name": "QA-2026", "file": "QA-2026.cqv.json",
            "modules": 2, "note": "刚上传"}

INDEX_KEYS = {"name", "file", "modules", "note"}
RESULT_KEYS = {"ok", "items", "error", "source"}

FIX_TEST_NAMES = {
    "test_fix_01_fresh_index_exposes_two_packs",
    "test_fix_02_refresh_right_after_upload_never_returns_stale_empty",
    "test_fix_03_authoritative_source_is_consulted",
}

_SECRET_RE = re.compile(r"(ghp_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,}|Bearer\s+\S+)")
_URL_SECRET_RE = re.compile(r"(?i)(access_token|token=|bearer|authorization|ghp_|github_pat_)")


# ------------------------------------------------------------------ 通用小工具

def redact(text: str) -> str:
    """所有对外输出都过一遍脱敏，避免把任何凭据带进报告。"""
    return _SECRET_RE.sub("***REDACTED***", str(text))


def safe_proxy() -> str:
    """打印代理时只留 scheme://host:port，丢掉可能存在的用户名/密码。"""
    proxy = vs._proxy_url() or "(直连，无代理环境变量)"
    parts = urllib.parse.urlsplit(proxy)
    if parts.scheme and parts.hostname:
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{parts.hostname}{port}"
    return "(直连，无代理环境变量)"


def header_view(headers) -> dict:
    """只挑与「缓存旧数据」和「限流」有关的头，避免把整包响应头打出来。"""
    wanted = ("Cache-Control", "Age", "ETag", "Content-Type",
              "X-GitHub-Request-Id", "X-RateLimit-Remaining", "Last-Modified")
    out = {}
    for key in wanted:
        try:
            value = headers.get(key)
        except Exception:  # noqa: BLE001 - 假响应/异常对象可能没有 headers
            value = None
        if value is not None:
            out[key] = str(value)[:120]
    return out


def parse_packs(body: bytes) -> dict:
    """一份响应体 → {raw_packs, valid_items, names, note}。

    同时兼容两种格式：① 裸 index JSON；② GitHub contents API 的
    ``{content: <base64>, encoding, sha}`` 包装。
    """
    out = {"raw_packs": None, "valid_items": None, "names": [], "note": ""}
    if not body:
        out["note"] = "空响应体"
        return out
    try:
        doc = json.loads(body.decode("utf-8-sig", errors="replace"))
    except (ValueError, UnicodeError) as exc:
        out["note"] = f"JSON 解析失败：{exc}"
        return out
    if isinstance(doc, dict) and "content" in doc and "encoding" in doc:
        try:
            inner = base64.b64decode(str(doc.get("content") or ""))
            doc = json.loads(inner.decode("utf-8-sig", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            out["note"] = f"contents.content 解不开：{exc}"
            return out
    if isinstance(doc, dict):
        raw = doc.get("packs")
        if isinstance(raw, (list, tuple)):
            out["raw_packs"] = len(raw)
    elif isinstance(doc, list):
        out["raw_packs"] = len(doc)
    items = vs._index_items(doc)
    if items is None:
        out["note"] = "_index_items 返回 None（缺 packs 列表 / 顶层结构不对）"
        return out
    out["valid_items"] = len(items)
    out["names"] = [item["name"] for item in items]
    return out


# ------------------------------------------------------------------ ① 真实网络复现

def probe_one(url: str) -> dict:
    """单源 GET 取证。用 ``vehicle_share._open`` 保证 UA / 代理 / 超时口径一致。"""
    rec: dict = {"url": url, "host": vs._host(url)}
    started = time.perf_counter()
    try:
        with vs._open(url, PROBE_TIMEOUT_S) as response:
            body = response.read()
            rec["status"] = int(getattr(response, "status", 0) or response.getcode())
            rec["headers"] = header_view(response.headers)
    except urllib.error.HTTPError as exc:
        rec["status"] = int(exc.code)
        rec["headers"] = header_view(getattr(exc, "headers", None))
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            body = b""
    except Exception as exc:  # noqa: BLE001 - 单源失败只记一行，不拖垮整轮取证
        rec["status"] = None
        rec["headers"] = {}
        rec["error"] = vs.describe_error(exc)
        rec["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return rec
    rec["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    rec.update(parse_packs(body))
    return rec


def run_repro() -> dict:
    """逐源打印真实 HTTP 状态 / Cache-Control / Age / packs，并调真实 fetch_remote_index。"""
    urls = list(vs.remote_index_urls())
    api = (f"https://api.github.com/repos/{vs.repo_slug()}/contents/"
           f"{vs.INDEX_PATH}?ref=main")
    raw_direct = vs._raw_url(vs.INDEX_PATH)
    bust = f"{raw_direct}?t={int(time.time())}"
    targets = [(f"镜像/直连源 #{i + 1}", url) for i, url in enumerate(urls)]
    targets += [("权威源 contents API（写链路用的那个）", api),
                ("raw 直连 + cache-buster 对照", bust)]

    lines: list[str] = []

    def emit(text: str = "") -> None:
        lines.append(redact(text))

    emit("=" * 100)
    emit("在线车辆库「上传成功 / 刷新为空」— 真实网络复现证据（只读，全部 GET）")
    emit(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    emit(f"Python: {sys.version.split()[0]}   项目: {ROOT}")
    emit(f"repo_slug: {vs.repo_slug()}   INDEX_PATH: {vs.INDEX_PATH}")
    emit(f"代理: {safe_proxy()}   UA: {PROBE_UA}（由 vehicle_share._open 自动添加）")
    emit(f"单源超时: {PROBE_TIMEOUT_S}s   并行探测: {len(targets)} 条")
    emit("=" * 100)
    emit()
    emit("[A] remote_index_urls() —— 当前读链路的源链")
    for i, url in enumerate(urls, start=1):
        emit(f"  {i}. {url}")
    emit(f"  共 {len(urls)} 条；含 api.github.com 的权威源? "
         f"{any('api.github.com' in u for u in urls)}")
    emit("  说明: 这是镜像/raw 兜底源链，本身不含 contents API；权威源是否另查")
    emit("        以 [C] 的 winner 和 [E] 的请求分布为准（修复后应另查 api.github.com）。")
    emit()

    emit("[B] 逐源取证（status / Cache-Control / Age / packs 数量与名字）")
    emit("-" * 100)
    with ThreadPoolExecutor(max_workers=min(10, len(targets))) as pool:
        probes = list(pool.map(lambda pair: (pair[0], probe_one(pair[1])), targets))
    for label, rec in probes:
        emit(f"● {label}")
        emit(f"  URL    : {rec['url']}")
        hdrs = rec.get("headers") or {}
        emit(f"  status : {rec.get('status')}   {rec.get('elapsed_ms')}ms"
             f"   Cache-Control={hdrs.get('Cache-Control', '-')}"
             f"   Age={hdrs.get('Age', '-')}")
        if rec.get("error"):
            emit(f"  ERROR  : {rec['error']}")
        else:
            emit(f"  packs  : 原始={rec.get('raw_packs')}  有效条目={rec.get('valid_items')}")
            emit(f"  names  : {rec.get('names')}")
            if rec.get("note"):
                emit(f"  note   : {rec['note']}")
        emit("-" * 100)
    emit()

    emit("[C] 直接调 vehicle_share.fetch_remote_index()（默认 timeout_ms=8000）")
    t0 = time.perf_counter()
    try:
        result = vs.fetch_remote_index()
    except Exception as exc:  # noqa: BLE001 - 实现不该抛，但取证要如实记
        result = {"ok": False, "items": [], "error": f"抛异常：{type(exc).__name__}: {exc}",
                  "source": ""}
    elapsed = round((time.perf_counter() - t0) * 1000, 1)
    emit(f"  耗时: {elapsed}ms")
    emit(f"  ok      = {result.get('ok')}")
    emit(f"  source  = {result.get('source')}")
    emit(f"  error   = {result.get('error')}")
    emit(f"  items   = {len(result.get('items') or [])} 条")
    for item in (result.get("items") or []):
        emit(f"      {item}")
    emit()
    emit("[D] 判定（真实网络，注意这是有 300s TTL 的时间窗口现象）")
    contents = next((rec for label, rec in probes if "contents API" in label), {})
    c_packs = contents.get("valid_items")
    f_packs = len(result.get("items") or [])
    winner = next((rec for _label, rec in probes
                   if rec.get("host") == result.get("source")), {})
    winner_cc = (winner.get("headers") or {}).get("Cache-Control", "-")
    contents_cc = (contents.get("headers") or {}).get("Cache-Control", "-")
    emit(f"  权威源 contents API: packs={c_packs} names={contents.get('names')}"
         f"   Cache-Control={contents_cc}")
    emit(f"  当前 fetch_remote_index(): packs={f_packs} winner={result.get('source')}"
         f"   Cache-Control={winner_cc}")
    raw_probe = next((rec for _label, rec in probes
                      if rec.get("host") == "raw.githubusercontent.com"
                      and "?" not in rec.get("url", "")), {})
    emit(f"  raw 直连缓存策略: "
         f"Cache-Control={(raw_probe.get('headers') or {}).get('Cache-Control', '-')}"
         f"（这就是「刚上传完 300s 内仍是 packs=0」的窗口）")
    winner_is_api = "api.github.com" in str(result.get("source") or "")
    if c_packs and not f_packs:
        emit("  → 复现成立：库里确实有包，但读链路稳定拿到旧缓存的空索引；")
        emit("    ui_settings.py 的「在线库还是空的」正是这条空 items 触发的。")
    elif winner_is_api and f_packs:
        emit("  → 修复后生效：赢家已是权威源 api.github.com，读链路不再依赖 CDN 旧缓存，")
        emit("    拿到的 packs 与 contents API 一致。⚠ 真实网络仍不能单独证明")
        emit("    「刚上传完立刻刷新」—— 该判据以 [E] 离线固定响应为准。")
    else:
        emit(f"  → 本轮真实网络**未复现**（c_packs={c_packs}, f_packs={f_packs}）：")
        emit("    距用户上传已超过 raw.githubusercontent.com 的 max-age=300 缓存窗口，")
        emit(f"    CDN 已经回源拿到新索引；本轮赢家 {result.get('source')}"
             f"(Cache-Control={winner_cc}) 也是新的。")
        emit("    ⚠ 这不代表 bug 不存在：读链路不问权威源时，赢家由 _race")
        emit("       「先到先赢」随机决定。窗口内赢家若命中旧 packs=0 的 raw/镜像")
        emit("       → 空库；窗口外碰巧命中已刷新源 → 看起来正常。同一个缺陷，")
        emit("       不同时刻不同表现，正是必须改用离线固定响应钉死回归的原因。")
    emit()

    # ---------------------------------------------------------------- [E] 离线回放
    emit("[E] 离线确定性复现：回放「刚上传完 / CDN 仍是 packs=0」的 300s 窗口")
    emit("     （不触外网；把上面实测到的 headers/结构固定成假响应）")
    net = FakeNet()
    net.authoritative_doc = copy.deepcopy(FRESH_DOC)   # 写链路刚登记完两包
    net.mirror_doc = copy.deepcopy(STALE_DOC)          # CDN 还是旧 packs=0
    with install_fake(net):
        r1 = vs.fetch_remote_index(timeout_ms=1200)
        # K4: _gather_sources/_race 的 daemon 线程会在 fetch 返回后继续收尾；
        # 0.15s 不够稳定（会把上一阶段迟到的镜像请求计进下一阶段），放宽到 0.35s。
        time.sleep(0.35)
        read1 = dict(net.by_kind)
        net.by_kind = {"authoritative": 0, "mirror": 0}
        uploaded = vs.update_index(PACK_NEW, SENTINEL_TOKEN)
        write_kinds = dict(net.by_kind)
        net.by_kind = {"authoritative": 0, "mirror": 0}
        r2 = vs.fetch_remote_index(timeout_ms=1200)
        time.sleep(0.35)
        read2 = dict(net.by_kind)
    # K2 隔离钩子：repro 段先调过 update_index，退出后必须清掉进程内「刚写过」
    # 标记，避免同进程后续的回归用例被跨场景状态污染（纯内存，不碰 QSettings）。
    vs.reset_written_marker()
    n1 = len(r1.get("items") or [])
    n2 = len(r2.get("items") or [])
    emit(f"  权威源实际 packs = {len(FRESH_DOC['packs'])} "
         f"({[p['name'] for p in FRESH_DOC['packs']]})")
    emit(f"  第1次 fetch_remote_index() -> ok={r1.get('ok')} items={n1} "
         f"source={r1.get('source')}")
    emit(f"  update_index() 模拟刚上传 -> ok={uploaded.get('ok')} "
         f"count={uploaded.get('count')} PUT次数={net.put_count}")
    emit(f"  立刻再 fetch_remote_index() -> ok={r2.get('ok')} "
         f"items={n2} source={r2.get('source')}")
    emit(f"  读链路请求分布: 第1次刷新 权威源={read1['authoritative']} "
         f"镜像/raw={read1['mirror']}；第2次刷新 权威源={read2['authoritative']} "
         f"镜像/raw={read2['mirror']}")
    emit(f"  写链路请求分布: 权威源={write_kinds['authoritative']} "
         f"镜像/raw={write_kinds['mirror']}"
         f"（镜像数为 _race daemon 线程收尾的计数伪影，非写链路多发请求）")
    emit("  注：请求分布按时间窗切片统计，跨阶段迟到的 daemon 线程可能被计入相邻阶段；")
    emit("      仅作量级参考，判定一律以 items / source / 回归用例为准（K4）。")
    if n1 and n2 and read1["authoritative"] and read2["authoritative"]:
        emit("  → 修复后生效：权威源 packs=2、镜像恒 packs=0 时，两次刷新都从权威源")
        emit("    拿到非空索引；上传后立刷 items 增加，不再退回 CDN 旧缓存。")
    elif not n1 and not n2 and not read1["authoritative"]:
        emit("  → 复现成立（修复前）：写成功(contents API) 与 读为空(镜像/raw 旧缓存)")
        emit("    是同一份索引的两个新鲜度视图；_race 先到先赢让旧视图稳定胜出。")
    else:
        emit("  → 结果介于两者之间，请逐项核对上面 4 行数值与请求分布。")
    emit()
    emit("  ⚠ 本段为离线固定响应复现/验证，不是真实网络验收；"
         "正式判定以回归用例与验收清单为准。")
    emit("=" * 100)

    text = "\n".join(lines) + "\n"
    (CHECK / "vehicle_index_repro.txt").write_text(text, encoding="utf-8")
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "repo_slug": vs.repo_slug(),
        "index_path": vs.INDEX_PATH,
        "proxy": safe_proxy(),
        "user_agent": PROBE_UA,
        "remote_index_urls": urls,
        "has_contents_api_in_chain": any("api.github.com" in u for u in urls),
        "probes": [{"label": label, **rec} for label, rec in probes],
        "fetch_remote_index": result,
        "fetch_elapsed_ms": elapsed,
        "winner": {"host": result.get("source"), "cache_control": winner_cc,
                   "packs": f_packs},
        "contents_cache_control": contents_cc,
        "cached_window_replay": {
            "authoritative_packs": len(FRESH_DOC["packs"]),
            "mirror_packs": len(STALE_DOC["packs"]),
            "first_fetch": r1,
            "update_index": uploaded,
            "refresh_after_upload": r2,
            "read1_request_kinds": read1,
            "write_request_kinds": write_kinds,
            "read2_request_kinds": read2,
        },
    }
    (CHECK / "vehicle_index_repro.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stdout.write(text)
    return {"lines": lines, "probes": probes, "result": result,
            "urls": urls, "elapsed_ms": elapsed, "payload": payload}


# ------------------------------------------------------------------ ② 离线假网络

def _msg(headers: dict) -> email.message.Message:
    message = email.message.Message()
    for key, value in headers.items():
        message[key] = str(value)
    return message


class FakeResponse:
    """最小响应对象：``with _open(...) as r: r.read()`` 需要的能力都在这儿。"""

    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None,
                 url: str = "") -> None:
        self._body = bytes(body)
        self.status = status
        self.code = status
        self.headers = _msg(headers or {})
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *args):
        return self._body

    def getcode(self):
        return self.status

    def info(self):
        return self.headers


class QSettingsTouched(RuntimeError):
    """守卫被触发 = 读链路偷偷写了用户配置，属于验收不通过。"""


class FakeNet:
    """离线「服务器」模型。

    - 权威源（``api.github.com`` 或路径含 ``/contents/``）：返回**刚写入**的索引，
      可选人为延迟，模拟它比 CDN 慢；
    - 镜像 / raw 直连：永远返回**冻结的旧缓存** ``packs=0``（附 max-age=300 / Age）；
    - ``contents_fail``：所有权威源超时/500 → 检验降级；
    - PUT：解析 base64 content 更新权威源，模拟真实「上传后立刻刷新」时序。

    ⚠ 这是本 harness 的显式 oracle（见报告 AC-1/AC-3）：修复若改用**别的**权威源
    （非 api.github.com / 非 /contents/），请同步调整 ``classify()`` 并在报告里说明，
    不要为了让用例变绿而把镜像也改成返回新数据 —— 那会把回归用例的意义抹掉。
    """

    def __init__(self) -> None:
        self.authoritative_doc: dict = copy.deepcopy(STALE_DOC)
        self.mirror_doc: dict = copy.deepcopy(STALE_DOC)
        self.mirror_body: bytes | None = None
        self.contents_fail = False
        self.contents_fail_mode = "timeout"     # timeout | http500
        self.mirror_fail = False
        self.authoritative_delay = 0.0
        self.mirror_delay = 0.0
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
        if host == "api.github.com" or host.endswith(".api.github.com") \
                or "/contents/" in parts.path:
            return "authoritative"
        return "mirror"

    @staticmethod
    def doc_bytes(doc) -> bytes:
        return json.dumps(doc, ensure_ascii=False).encode("utf-8")

    def contents_bytes(self, doc) -> bytes:
        text = json.dumps(doc, ensure_ascii=False, indent=1)
        wrapper = {
            "name": "index.json", "path": f"vehicles/{vs.INDEX_PATH.split('/')[-1]}",
            "sha": self.sha, "size": len(text.encode("utf-8")),
            "type": "file", "encoding": "base64",
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        }
        return json.dumps(wrapper, ensure_ascii=False).encode("utf-8")

    def _contents_error(self, url: str) -> Exception:
        if self.contents_fail_mode == "http500":
            return urllib.error.HTTPError(
                url, 500, "Internal Server Error",
                _msg({"Content-Type": "application/json"}),
                io.BytesIO(b'{"message":"Server Error"}'))
        return urllib.error.URLError("simulated contents API timeout")

    def _record_exception(self, exc: BaseException, url: str) -> None:
        self.exception_texts.append(f"{type(exc).__name__}: {exc}")
        self.exception_texts.append(f"url={url}")

    def _put(self, url: str, payload) -> FakeResponse:
        self.put_count += 1
        try:
            body = json.loads(bytes(payload or b"").decode("utf-8"))
            content = base64.b64decode(str(body.get("content") or ""))
            doc = json.loads(content.decode("utf-8-sig", errors="replace"))
            if isinstance(doc, dict):
                self.authoritative_doc = doc
        except Exception as exc:  # noqa: BLE001 - 假服务器自己记一笔即可
            self.put_errors.append(str(exc))
        response = {"content": {
            "name": "index.json", "path": "vehicles/index.json", "sha": "newsha",
            "html_url": ("https://github.com/Charlie-lee123/CQUFSAEbatteryteam/"
                         "blob/main/vehicles/index.json")},
            "commit": {"sha": "newsha"}}
        return FakeResponse(json.dumps(response).encode("utf-8"), url=url)

    def dispatch(self, url: str, method: str, headers: dict, payload) -> FakeResponse:
        kind = self.classify(url)
        self.by_kind[kind] += 1
        if kind == "authoritative":
            if self.authoritative_delay:
                time.sleep(self.authoritative_delay)
            if self.contents_fail:
                exc = self._contents_error(url)
                self._record_exception(exc, url)
                raise exc
            if method == "PUT":
                return self._put(url, payload)
            accept = str(headers.get("accept", ""))
            if "vnd.github.raw" in accept:
                # 修复也可能走 Accept: application/vnd.github.raw 拿裸 JSON
                return FakeResponse(self.doc_bytes(self.authoritative_doc), url=url,
                                    headers={"Cache-Control": "no-store"})
            return FakeResponse(self.contents_bytes(self.authoritative_doc), url=url,
                                headers={"Cache-Control": "no-store"})
        if self.mirror_delay:
            time.sleep(self.mirror_delay)
        if self.mirror_fail:
            exc = urllib.error.URLError("simulated mirror network failure")
            self._record_exception(exc, url)
            raise exc
        if self.mirror_body is not None:
            return FakeResponse(self.mirror_body, url=url,
                                headers={"Cache-Control": "max-age=300", "Age": "137"})
        return FakeResponse(self.doc_bytes(self.mirror_doc), url=url,
                            headers={"Cache-Control": "max-age=300", "Age": "137"})

    def handle(self, fullurl, data=None, timeout=None) -> FakeResponse:
        request = fullurl if isinstance(fullurl, urllib.request.Request) else None
        url = request.full_url if request is not None else str(fullurl)
        method = request.get_method() if request is not None else ("POST" if data else "GET")
        headers = ({key.lower(): value for key, value in request.header_items()}
                   if request is not None else {})
        payload = (request.data if request is not None and request.data is not None
                   else data)
        self.requests.append({
            "url": url, "method": method, "headers": dict(headers),
            "data_len": len(bytes(payload)) if payload else 0,
            "timeout": timeout,
        })
        return self.dispatch(url, method, headers, payload)


_ACTIVE_FAKE: FakeNet | None = None
_NETWORK_PATCHED = False


def _guarded_open(self, fullurl, data=None, timeout=None, *args, **kwargs):
    """进程级 fail-closed 闸门：没有活动 FakeNet 时宁可报错，也不许打真实外网。"""
    fake = _ACTIVE_FAKE
    if fake is None:
        raise RuntimeError("离线阶段禁止真实网络请求（没有活动的 FakeNet）")
    return fake.handle(fullurl, data, timeout)


def ensure_network_patched() -> None:
    """只装一次、整进程不卸。

    ``_race()`` 拿到结果后就返回，其余 daemon 线程还会继续跑；若在用例 teardown
    时把补丁撤掉，那些迟到的线程会打到真实外网。这里改成进程级常驻 +
    ``_ACTIVE_FAKE`` 控制，没有假服务器时直接抛错（被 _race 的 except 吞掉）。
    """
    global _NETWORK_PATCHED
    if not _NETWORK_PATCHED:
        mock.patch.object(urllib.request.OpenerDirector, "open",
                          new=_guarded_open).start()
        _NETWORK_PATCHED = True


@contextlib.contextmanager
def install_fake(fake: FakeNet):
    """在 OpenerDirector.open 层拦截，覆盖 ``_open`` / 裸 urlopen 两条路径。"""
    global _ACTIVE_FAKE
    ensure_network_patched()
    previous = _ACTIVE_FAKE
    _ACTIVE_FAKE = fake
    try:
        yield fake
    finally:
        _ACTIVE_FAKE = previous


class _Base(unittest.TestCase):
    """公共夹具：离线网络 + QSettings 写入守卫。"""

    def setUp(self) -> None:
        # K2 隔离钩子：每个用例从「无刚写过标记」的干净进程内状态起跑。
        # ⛔ 测试隔离由 harness 承担，不该让产品把回填资格裁成一次性来迁就测试。
        #    reset_written_marker() 是纯内存操作，不碰 QSettings。
        vs.reset_written_marker()
        self.net = FakeNet()
        self.enterContext(install_fake(self.net))
        self._guards: list = []

        def forbid(obj, name: str, label: str) -> None:
            patcher = mock.patch.object(obj, name,
                                        side_effect=QSettingsTouched(label))
            patcher.start()
            self._guards.append(patcher)

        forbid(vs, "save_token", "vehicle_share.save_token")
        forbid(vs, "clear_token", "vehicle_share.clear_token")
        # 经 sys.modules 观察，不额外 import 产品模块
        customization_mod = sys.modules.get("customization")
        if customization_mod is not None:
            for name in ("save_vehicle", "set", "select_vehicle",
                         "delete_vehicle", "rename_vehicle"):
                if hasattr(customization_mod, name):
                    forbid(customization_mod, name, f"customization.{name}")
        self.addCleanup(self._stop_guards)

    def _stop_guards(self) -> None:
        for patcher in self._guards:
            patcher.stop()

    def assert_result_contract(self, result) -> None:
        """fetch_remote_index 的对外结构必须是干净的扁平 dict，不得漏脏结构。"""
        self.assertIsInstance(result, dict, "返回值必须是 dict")
        self.assertTrue(RESULT_KEYS.issubset(result),
                        f"缺契约字段: {sorted(RESULT_KEYS - set(result))}")
        self.assertIsInstance(result["ok"], bool)
        self.assertIsInstance(result["items"], list)
        self.assertIsInstance(result["error"], str)
        self.assertIsInstance(result["source"], str)
        for item in result["items"]:
            self.assertIsInstance(item, dict, "items 里混进了非 dict")
            self.assertEqual(set(item), INDEX_KEYS, "_index_items 的键集合被改了")
            self.assertIsInstance(item["name"], str)
            self.assertIsInstance(item["file"], str)
            self.assertIsInstance(item["modules"], int)
            self.assertIsInstance(item["note"], str)


# ------------------------------------------------- 修复相关（修复前预期 FAIL）

class FixDependentTests(_Base):
    """断言的是 PRD 期望行为，不是当前实现。

    修复前这三条**按预期 FAIL**：断言正确、实现给错结果 → 属于源码缺陷，
    路由 Engineer；不是测试写错。
    """

    def test_fix_01_fresh_index_exposes_two_packs(self) -> None:
        # Arrange: 权威源已有两包；镜像/raw 全是 packs=0 旧缓存；权威源比 CDN 慢 120ms
        self.net.authoritative_doc = copy.deepcopy(FRESH_DOC)
        self.net.authoritative_delay = 0.12
        # Act
        result = vs.fetch_remote_index()
        # Assert
        self.assert_result_contract(result)
        self.assertTrue(result["ok"], f"应能拉到索引，实际: {result}")
        names = sorted(item["name"] for item in result["items"])
        self.assertEqual(names, ["A08-2026", "E74-2026"],
                         "修复后必须能看到用户上传的 E74-2026 / A08-2026 两个包")
        self.assertEqual(result["error"], "")
        for item in result["items"]:
            self.assertTrue(item["file"].endswith(".cqv.json"))
            self.assertTrue(item["file"])

    def test_fix_02_refresh_right_after_upload_never_returns_stale_empty(self) -> None:
        # Arrange: 初始权威源 = 空索引；镜像永远冻结在 packs=0
        # Act 1: 走写链路真实登记一个包（PUT 会让假服务器更新权威源）
        uploaded = vs.update_index(PACK_NEW, SENTINEL_TOKEN)
        self.assertTrue(uploaded["ok"], f"上传登记应当成功: {uploaded}")
        self.assertEqual(uploaded["count"], 1)
        self.assertEqual(self.net.put_count, 1, "PUT 必须真的发生")
        # Act 2: 同一进程、立刻刷新
        result = vs.fetch_remote_index()
        # Assert
        self.assert_result_contract(result)
        self.assertTrue(result["items"],
                        "刚上传完立刻刷新不得返回空列表（packs=0 旧缓存）")
        names = [item["name"] for item in result["items"]]
        files = [item["file"] for item in result["items"]]
        self.assertIn("QA-2026", names, f"刚登记的包不见了: {names}")
        self.assertIn("QA-2026.cqv.json", files)

    def test_fix_03_authoritative_source_is_consulted(self) -> None:
        # 读链路必须真的去问权威源；只把 contents URL 追加进 _race 并发列表也不行，
        # test_fix_01 的 120ms 延迟会专门证伪「先到先赢」式修法。
        self.net.authoritative_doc = copy.deepcopy(FRESH_DOC)
        self.net.authoritative_delay = 0.12
        result = vs.fetch_remote_index()
        self.assert_result_contract(result)
        self.assertGreaterEqual(
            self.net.by_kind["authoritative"], 1,
            "读链路没有访问权威源（api.github.com / /contents/）；"
            "仅靠镜像链无法绕开 300s CDN 缓存")


# ------------------------------------------------- 契约/降级（修复前后都必须 PASS）

class ContractTests(_Base):

    def test_c01_contents_api_down_degrades_to_mirror_chain(self) -> None:
        self.net.contents_fail = True
        self.net.contents_fail_mode = "timeout"
        result = vs.fetch_remote_index(timeout_ms=1500)
        self.assert_result_contract(result)
        self.assertGreaterEqual(self.net.by_kind["mirror"], 1,
                                "权威源全挂时必须仍然尝试镜像/raw 兜底")
        self.assertIsInstance(result["items"], list)

    def test_c02_contents_api_http500_degrades_without_exception(self) -> None:
        self.net.contents_fail = True
        self.net.contents_fail_mode = "http500"
        result = vs.fetch_remote_index(timeout_ms=1500)
        self.assert_result_contract(result)
        self.assertGreaterEqual(self.net.by_kind["mirror"], 1)

    def test_c03_all_sources_fail_is_bounded_and_clean(self) -> None:
        self.net.contents_fail = True
        self.net.mirror_fail = True
        started = time.perf_counter()
        result = vs.fetch_remote_index(timeout_ms=1000)
        elapsed = time.perf_counter() - started
        self.assert_result_contract(result)
        self.assertLess(elapsed, 4.0,
                        f"新增权威源后全挂耗时 {elapsed:.2f}s，预算应受 timeout_ms 约束")
        self.assertFalse(result["ok"])

    def test_c04_default_timeout_budget_not_blown(self) -> None:
        # 现 _race 的 done 只在成功时 set，全源失败会白等满 total_s；
        # 修复不得让默认 8000ms 预算线性叠加到 12s 以上。
        self.net.contents_fail = True
        self.net.mirror_fail = True
        started = time.perf_counter()
        result = vs.fetch_remote_index()          # 默认 8000ms
        elapsed = time.perf_counter() - started
        self.assert_result_contract(result)
        self.assertLess(elapsed, 12.0, f"默认预算被撑到 {elapsed:.2f}s")

    def test_c05_bad_json_missing_packs_missing_file(self) -> None:
        # 权威源全挂 + 镜像回落，镜像喂各种脏数据 → 一律按 _index_items 契约处理，不崩
        payloads = [
            ("坏 JSON", b"{ this is not json"),
            ("缺 packs", json.dumps({"schema": 1}).encode("utf-8")),
            ("packs 非列表", json.dumps({"packs": "oops"}).encode("utf-8")),
            ("顶层 null", b"null"),
            ("顶层数字", b"42"),
            ("顶层 list 合法", json.dumps([{"file": "x.cqv.json"}]).encode("utf-8")),
            ("条目缺 file / 非 dict 混入",
             json.dumps({"packs": [
                 {"name": "NoFile"},
                 42,
                 {"file": "ok.cqv.json", "name": "Good", "modules": "bad",
                  "note": None}]}).encode("utf-8")),
        ]
        for label, body in payloads:
            with self.subTest(case=label):
                net = FakeNet()
                net.contents_fail = True
                net.mirror_body = body
                with install_fake(net):
                    result = vs.fetch_remote_index(timeout_ms=1200)
                self.assert_result_contract(result)
                if label == "顶层 list 合法":
                    self.assertTrue(result["ok"])
                    self.assertEqual([i["name"] for i in result["items"]],
                                     ["x.cqv.json"])
                elif label == "条目缺 file / 非 dict 混入":
                    self.assertTrue(result["ok"])
                    self.assertEqual(len(result["items"]), 1)
                    self.assertEqual(result["items"][0]["name"], "Good")
                    self.assertEqual(result["items"][0]["modules"], 0)
                    self.assertEqual(result["items"][0]["note"], "")
                else:
                    self.assertEqual(result["items"], [],
                                     f"{label} 不应产出脏条目")

    def test_c06_index_items_exact_contract(self) -> None:
        self.assertIsNone(vs._index_items({"schema": 1}))
        self.assertIsNone(vs._index_items({"packs": "x"}))
        self.assertIsNone(vs._index_items(None))
        self.assertIsNone(vs._index_items(7))
        self.assertEqual(
            vs._index_items({"packs": [
                {"name": "N", "file": "n.cqv.json", "modules": "7", "note": None},
                {"name": "NoFile"},
                42,
                {"file": "fallback.cqv.json", "modules": None, "note": 3}]}),
            [{"name": "N", "file": "n.cqv.json", "modules": 7, "note": ""},
             {"name": "fallback.cqv.json", "file": "fallback.cqv.json",
              "modules": 0, "note": "3"}])
        self.assertEqual(vs._index_items([{"file": "x.cqv.json"}]),
                         [{"name": "x.cqv.json", "file": "x.cqv.json",
                           "modules": 0, "note": ""}])

    def test_c07_user_agent_present_on_every_request(self) -> None:
        self.net.contents_fail = True
        vs.fetch_remote_index(timeout_ms=1200)
        self.assertTrue(self.net.requests, "假服务器没收到任何请求")
        for record in self.net.requests:
            self.assertIn("user-agent", record["headers"],
                          f"请求缺 UA（GitHub raw 会回 403）: {record['url']}")

    def test_c08_token_never_in_url_log_or_exception_text(self) -> None:
        capture = _LogCapture()
        root = logging.getLogger()
        root.addHandler(capture)
        old_level = root.level
        root.setLevel(logging.DEBUG)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                first = vs.update_index(PACK_NEW, SENTINEL_TOKEN)
                self.net.contents_fail = True
                self.net.contents_fail_mode = "http500"
                second = vs.update_index(PACK_NEW, SENTINEL_TOKEN)
                result = vs.fetch_remote_index(timeout_ms=1200)
        finally:
            root.removeHandler(capture)
            root.setLevel(old_level)
        urls = [record["url"] for record in self.net.requests]
        blob = "\n".join(
            urls
            + [str(first.get("error", "")), str(second.get("error", "")),
               str(result.get("error", "")), str(first), str(second), str(result),
               vs.LAST_TOKEN_ERROR, out.getvalue(), err.getvalue()]
            + [record.getMessage() for record in capture.records]
            + self.net.exception_texts)
        self.assertNotIn(SENTINEL_TOKEN, blob,
                         "token 泄漏到了 URL / 日志 / 错误文本 / 异常文本")
        self.assertIsNone(_SECRET_RE.search(blob),
                          "URL/日志/异常里出现了 token 形制的字符串")
        for url in urls:
            self.assertIsNone(_URL_SECRET_RE.search(url),
                              f"URL 里带了凭据（查询参数也会进代理日志）: {url}")
        # 正向对照：Authorization 头是唯一允许出现 token 的位置，确认确实走了鉴权写链路
        self.assertTrue(any(SENTINEL_TOKEN in str(r["headers"].get("authorization", ""))
                            for r in self.net.requests),
                        "假服务器没收到 Authorization 头，用例失去意义")
        self.assertEqual(set(first), {"ok", "error", "count", "file", "url"})

    def test_c09_read_path_never_writes_qsettings(self) -> None:
        self.net.authoritative_doc = copy.deepcopy(FRESH_DOC)
        vs.fetch_remote_index()
        vs._index_items(FRESH_DOC)
        vs._merge_index(FRESH_DOC, PACK_NEW)
        self.assertEqual(self.net.put_count, 0, "纯读链路不该发 PUT")
        # setUp 里的守卫若被触发会抛 QSettingsTouched，用例自然失败

    def test_c10_pure_functions_make_zero_network_calls(self) -> None:
        vs._merge_index(STALE_DOC, PACK_NEW)
        vs._index_items(FRESH_DOC)
        vs.pack_filename({"vehicles": [{"name": "E74-2026"}]})
        self.assertEqual(self.net.requests, [], "_merge_index/_index_items 应保持纯函数")

    def test_c11_mirror_prefixes_not_copied_into_vehicle_share(self) -> None:
        urls_before = list(vs.remote_index_urls())
        self.assertTrue(urls_before, "remote_index_urls() 不能返回空链")
        # 经 sys.modules 观察 updater，不额外 import 产品模块
        updater_mod = sys.modules.get("updater")
        self.assertIsNotNone(updater_mod, "updater 应已被 vehicle_share 惰性加载")
        custom = ("https://mirror-alpha.invalid/", "https://mirror-beta.invalid/")
        with mock.patch.object(updater_mod, "MIRROR_PREFIXES", custom):
            urls = list(vs.remote_index_urls())
        joined = " ".join(urls)
        self.assertIn("mirror-alpha.invalid", joined)
        self.assertIn("mirror-beta.invalid", joined)
        self.assertNotIn("ghfast.top", joined,
                         "修复里疑似复制了一份镜像字面量，破坏了 MIRROR_PREFIXES 单一出处")
        self.assertIn(vs._raw_url(vs.INDEX_PATH), urls, "raw 直连兜底不能丢")
        self.assertIsNone(_URL_SECRET_RE.search(joined), "源链 URL 里出现凭据")
        self.assertFalse(any("api.github.com" in u and "token=" in u for u in urls))

    def test_c12_writeback_persists_within_ttl_but_never_overrides_remote(self) -> None:
        """PRD：刚上传成功后，90s「刚写过」窗口内的**任何一次**刷新都不得把在线库
        显示成空的；但远端可达且明确给出真实状态时，不得用本地快照盖掉远端。

        K2 修正：上一版断言「资格消费后降级必须返回 items=[]」把「一次性消费」
        当成了正确行为，与 PRD 冲突（Round 2 探针 P1/P3 已实跑证实会造成用户
        可见空库）。这里按 PRD 改写；断言的是用户可见行为，不依赖内部函数名。
        """
        # --- A：上传 → 刷新1(API OK) → TTL 内刷新2(API 全挂 + 镜像恒 packs=0)
        net = FakeNet()
        net.authoritative_doc = copy.deepcopy(STALE_DOC)   # 上传前：空索引
        net.mirror_doc = copy.deepcopy(STALE_DOC)          # CDN 恒为旧 packs=0
        with install_fake(net):
            uploaded = vs.update_index(PACK_NEW, SENTINEL_TOKEN)
            self.assertTrue(uploaded["ok"], f"上传登记应当成功: {uploaded}")
            r1 = vs.fetch_remote_index(timeout_ms=1500)
            time.sleep(0.35)
            net.by_kind = {"authoritative": 0, "mirror": 0}
            net.contents_fail = True                       # 权威源全挂
            r2 = vs.fetch_remote_index(timeout_ms=1500)
            time.sleep(0.35)
        # 正向对照：紧跟 PUT 的刷新确实拿到了刚登记的包
        self.assert_result_contract(r1)
        self.assertIn("QA-2026", [item["name"] for item in r1["items"]],
                      "刚上传完的刷新应能看到新包（否则本用例失去意义）")
        # 核心断言（K2 修正点）：TTL 内第 2 次刷新，即使 API 不可达 + CDN 仍是
        # packs=0，也不得掉回空库 —— 进程刚写进去的包是已知事实。
        self.assert_result_contract(r2)
        self.assertTrue(r2["items"],
                        "TTL 窗口内第 2 次刷新（权威源全挂 + 镜像恒 packs=0）"
                        "不得返回空列表：用户刚上传的包必须仍可见")
        self.assertIn("QA-2026", [item["name"] for item in r2["items"]],
                      "TTL 内回填应持续有效，不能只给紧跟 PUT 的那一次刷新")
        self.assertGreaterEqual(net.by_kind["authoritative"], 1,
                                "第二次刷新仍应尝试权威源后优雅降级")

        # --- B：远端可达且明确给出空索引 → 不得用本地快照盖掉远端真实变更
        vs.reset_written_marker()
        net_b = FakeNet()
        net_b.authoritative_doc = copy.deepcopy(STALE_DOC)
        net_b.mirror_doc = copy.deepcopy(STALE_DOC)
        with install_fake(net_b):
            up_b = vs.update_index(PACK_NEW, SENTINEL_TOKEN)
            self.assertTrue(up_b["ok"], f"上传登记应当成功: {up_b}")
            # 模拟「别人把远端索引清空」这种真实远端变更（PUT 之后权威源变了）
            net_b.authoritative_doc = copy.deepcopy(STALE_DOC)
            r_b = vs.fetch_remote_index(timeout_ms=1500)
            time.sleep(0.35)
        self.assert_result_contract(r_b)
        self.assertEqual(r_b["source"], "api.github.com",
                         "权威源可达时应以远端为准")
        self.assertEqual(r_b["items"], [],
                         "权威源可达且明确给出空索引时，不得用本地刚写快照"
                         "盖掉远端真实变更")


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


# ------------------------------------------------- _merge_index 语义不回归

BASE_INDEX = {
    "schema": 2, "updated_at": "2026-11-09T19:00:00",
    "packs": [
        {"name": "A", "file": "a.cqv.json", "modules": 1, "note": "old-a"},
        {"name": "B", "file": "b.cqv.json", "modules": 2, "note": "old-b"},
        {"name": "A", "file": "a-dup.cqv.json", "modules": 9, "note": "dup"},
        "not-a-dict",
    ],
}


class MergeIndexTests(_Base):

    def test_m01_same_name_replaces_in_place(self) -> None:
        merged = vs._merge_index(copy.deepcopy(BASE_INDEX),
                                 {"name": "B", "file": "b2.cqv.json",
                                  "modules": 5, "note": "new-b"})
        packs = merged["packs"]
        # 老索引里的重名 A 会被去重清掉，所以同名替换后是 [A, newB] 共 2 条。
        self.assertEqual(len(packs), 2)
        self.assertEqual([item["name"] for item in packs], ["A", "B"])
        self.assertEqual(packs[0]["file"], "a.cqv.json")
        self.assertEqual(packs[1]["file"], "b2.cqv.json", "同名应就地替换，位置不变")
        self.assertEqual(packs[1]["modules"], 5)
        self.assertEqual(packs[1]["note"], "new-b")
        self.assertNotIn("a-dup.cqv.json", [item["file"] for item in packs],
                         "老索引里的重名条目应被去重")

    def test_m02_existing_duplicate_names_are_deduplicated(self) -> None:
        merged = vs._merge_index(copy.deepcopy(BASE_INDEX),
                                 {"name": "C", "file": "c.cqv.json"})
        names = [item["name"] for item in merged["packs"]]
        self.assertEqual(names, ["A", "B", "C"], "老索引的重名应只留第一条")
        self.assertNotIn("a-dup.cqv.json", [i["file"] for i in merged["packs"]])
        self.assertNotIn("not-a-dict", merged["packs"])

    def test_m03_bad_index_doc_restarts_clean(self) -> None:
        for bad in (None, [], "junk", 42, {"packs": "x"}, {"schema": "bad"}):
            with self.subTest(bad=repr(bad)):
                merged = vs._merge_index(bad, PACK_NEW)
                self.assertIsInstance(merged, dict)
                self.assertEqual(set(merged), {"schema", "updated_at", "packs"})
                self.assertEqual(len(merged["packs"]), 1)
                self.assertEqual(merged["packs"][0]["name"], "QA-2026")
                self.assertEqual(merged["schema"], vs.SCHEMA)

    def test_m04_schema_preserved_and_name_falls_back_to_file(self) -> None:
        merged = vs._merge_index(copy.deepcopy(BASE_INDEX),
                                 {"name": "  ", "file": "f.cqv.json"})
        self.assertEqual(merged["schema"], 2, "已有索引的 schema 应被继承")
        self.assertEqual(merged["packs"][-1]["name"], "f.cqv.json")
        self.assertEqual(set(merged["packs"][-1]), INDEX_KEYS)

    def test_m05_pure_function_does_not_mutate_inputs(self) -> None:
        doc = copy.deepcopy(BASE_INDEX)
        pack = copy.deepcopy(PACK_NEW)
        snapshot_doc = copy.deepcopy(doc)
        snapshot_pack = copy.deepcopy(pack)
        first = vs._merge_index(doc, pack)
        self.assertEqual(doc, snapshot_doc)
        self.assertEqual(pack, snapshot_pack)
        self.assertEqual(first["packs"], vs._merge_index(doc, pack)["packs"])
        self.assertEqual(self.net.requests, [], "_merge_index 不得发网络请求")


# ------------------------------------------------------------------ ③ 回归运行 / 报告

def run_regression() -> dict:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (FixDependentTests, ContractTests, MergeIndexTests):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    raw = redact(stream.getvalue())

    fix_failures: list[str] = []
    unexpected: list[str] = []
    for test, _trace in list(result.failures) + list(result.errors):
        name = test.id().rsplit(".", 1)[-1]
        (fix_failures if name in FIX_TEST_NAMES else unexpected).append(name)

    passed = result.testsRun - len(result.failures) - len(result.errors)
    all_green = (len(result.failures) == 0 and len(result.errors) == 0)
    if all_green:
        phase = "修复后回归验收"
        note = ("说明: 全部用例通过。FixDependentTests 已转绿，契约/降级/合并用例"
                "保持全绿，判定为验收通过（Send To: NoOne）。")
        routing = "NoOne（全部用例通过，修复验收通过）"
    elif fix_failures and not unexpected:
        phase = "Round 1 基线（修复前）"
        note = ("说明: 本轮不是「修复后」验收。FixDependentTests 的 FAIL 是预期的实现缺陷，"
                "断言写的是 PRD 期望行为，路由 Engineer；"
                "ContractTests/MergeIndexTests 必须全绿。")
        routing = ("Engineer（源码缺陷：读链路只查镜像/raw，不查权威 contents API；"
                   "_race 先到先赢稳定拿到 packs=0 旧缓存）")
    else:
        phase = "回归复测（存在非预期失败）"
        note = ("说明: 出现非预期失败，需逐条归因（实现缺陷→Engineer；"
                "断言写错→QA 自修）。按 2 轮上限，剩余项列入 Known Issues。")
        routing = "Engineer / QA-self（存在非预期失败，需逐条归因）"
    header = [
        "=" * 100,
        f"在线车辆库回归用例 — {phase}（离线确定性）",
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        note,
        "=" * 100,
        "",
        raw,
        "-" * 100,
        (f"Total: {result.testsRun} | Passed: {passed} | "
         f"Failed: {len(result.failures)} | Errors: {len(result.errors)}"),
        (f"修复相关用例 FAIL ({len(fix_failures)}): "
         f"{sorted(fix_failures) or '无'}"),
        f"非预期失败 ({len(unexpected)}): {sorted(unexpected) or '无 —— 用例本身无 bug'}",
        f"Routing Decision: {routing}",
        "=" * 100,
    ]
    text = "\n".join(header) + "\n"
    (CHECK / "vehicle_index_regression.txt").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return {"result": result, "text": text, "total": result.testsRun,
            "passed": passed, "failed": len(result.failures),
            "errors": len(result.errors), "fix_failures": fix_failures,
            "unexpected": unexpected}


ACCEPTANCE = """\
【验收标准清单】—— 修复方必须逐条满足，才算通过（Round 2 由本脚本复跑判定）

AC-01 [硬门槛] 权威源优先：在「contents API 有 E74-2026 / A08-2026 两包、所有
      镜像/raw 都是 packs=0 旧缓存」的场景下，fetch_remote_index() 返回 ok=True，
      items 恰好含这两个名字，每项 exactly {name,file,modules,note}，file 非空且
      以 .cqv.json 结尾，error 为空。
AC-02 [硬门槛] 上传后立刷：同一进程内先 update_index()/upload_pack() 成功登记，
      紧接着 fetch_remote_index() 不得返回 items == []；必须能看到刚登记的包名与
      file。镜像侧模拟固定返回旧 packs=0，不许靠「假装 CDN 已刷新」过关。
AC-03 [硬门槛] 不许只把 contents URL 塞进 _race 并发列表：当权威源比镜像慢
      120ms 时仍须拿到权威内容。_race() 的「先到先赢」正是本次根因，追加一个
      更慢的源等于没修。
AC-04 优雅降级：contents API 全部超时 / HTTP 500 / URLError 时，fetch_remote_index()
      不抛异常，返回结构化 {"ok","items","error","source"}，且至少尝试过 1 条
      镜像/raw；items 必须是扁平 list[dict]，不得漏出 base64 / contents 包装 /
      嵌套结构。
AC-05 时间预算：全源失败时 fetch_remote_index(timeout_ms=1000) 墙钟 < 4s，
      默认 8000ms 调用 < 12s。新增串行请求不得与原有 _race 预算线性叠加。
      注意现 _race 的 done 只在成功时 set，全源失败本就会白等满 total_s。
AC-06 脏数据契约：坏 JSON / 缺 packs / packs 非列表 / 顶层 list / 顶层 null /
      条目缺 file / 条目非 dict / modules 非整数 —— 一律不崩；_index_items()
      既有语义（跳过非 dict、跳过无 file、name 回落 file、modules 归 0、
      note 空串化）不得改变。
AC-07 _merge_index 不回归：同名就地替换（位置不变）、老索引重名去重（只留第一条）、
      坏结构当空索引重来、schema 继承、无 name 回落 file；纯函数、零网络、
      不改入参、零 QSettings 写入。
AC-08 token 零泄露：合成 token 不得出现在任何 URL（含 query string）、任何日志
      / stdout / stderr、任何返回 error 字符串、任何异常文本里。唯一允许出现的位置
      是 _contents_headers() 的 Authorization: Bearer ... 请求头。
      严禁 ?access_token= / &token= 这类把凭据拼进 URL 的写法。
AC-09 镜像链单一出处：remote_index_urls()（或等价出口）必须仍从
      updater.MIRROR_PREFIXES 取；monkeypatch 该元组后 URL 链必须跟着变，
      不得出现复制的字面量（如 ghfast.top 硬编码）。raw 直连兜底必须保留。
AC-10 零副作用：读链路不得调用 save_token / clear_token /
      customization.save_vehicle 等写 QSettings 的接口；不得开 QMainWindow、
      不得启动第二个 exe、不得写 发布/ 与 dist*、不得跑全量 selftest.py。
AC-11 网络出口统一：所有 I/O 仍走 vehicle_share._open（统一 UA、HTTPS_PROXY、
      单源超时），不得绕过到裸 urlopen / 第三方库。本 harness 在
      OpenerDirector.open 层拦截，绕过会让离线用例打真实网络而失去意义。
AC-12 每个 HTTP 请求都带 User-Agent（GitHub raw 对空 UA 回 403）。
AC-13 [Round 2 追加 / K2 修正] 「刚写过」回填在 TTL 窗口内**持续有效**：刚上传
      成功后，90s 内的任何一次刷新即使权威源不可达、镜像恒 packs=0，也不得把
      在线库显示成空的 —— 进程刚写进去的包是已知事实。但权威源可达且明确给出
      真实状态（含空索引）时，必须以远端为准，不得用本地快照盖掉远端变更。
      标记只许活在进程内存、TTL 60~120s、绝不写 QSettings；测试隔离由 harness
      的 reset_written_marker() 承担，不得为迁就测试把生产语义裁成一次性。
      （对应 ``test_c12_writeback_persists_within_ttl_but_never_overrides_remote``）
AC-14 [Round 2 追加] ``download_remote_pack()`` 与索引同源治理：包下载也要走
      contents API 权威源 + 镜像 cache-busting，优先含 ``vehicles`` 的权威候选，
      权威全挂退回镜像链。只修 ``fetch_remote_index()`` 会留下「清单看得见车、
      点下载拿到 300s 前旧包」的残留面。（本 harness 未直接覆盖，由工程师的
      ``devtools/_probe_vehicle_index_fix.py`` 独立探针覆盖；Round 2 抽查。）

【Round 1 用例自修记录（QA self-route，非源码缺陷）】
 · test_m01 初版断言「同名替换后应有 3 条」写错了：_merge_index 在替换的同时
   还会把老索引里的重名条目去重，正确结果是 2 条。断言本身错 → 自行修正测试，
   复跑后「非预期失败 = 0」。没有把锅甩给 Engineer。
 · test_c12（Round 2 收尾 / K2）：初版断言「一次性消费后降级必须 items=[]」把
   「一次性回填」当成正确行为，与 PRD 冲突；Round 2 独立探针 P1/P3 实跑证实这会
   让用户刚上传后第 2 次刷新掉回空库。断言本身错 → 按 PRD 改为「TTL 内持续有效、
   但不盖掉远端真实变更」，并补 setUp / repro [E] 的 reset_written_marker() 隔离钩子。
   这是**收紧**（要求更多刷新都非空），不是放宽；AC-13 同步更正，AC-14 未动。

【harness 的显式 oracle（避免误判）】
 · 真实网络的「空库」是 300s CDN TTL 窗口现象：本轮 21:40 取证时 TTL 已过期，
   所有在线源都是 packs=2，因此**不要**拿「现在刷新一下有包了」当作已修复。
   判据一律以离线固定响应（FakeNet 镜像恒为 packs=0）为准。
 · FakeNet 只把 host=api.github.com 或 path 含 /contents/ 的请求视为权威源并返回
   「刚写入」的索引；其余一律返回冻结的 packs=0 旧缓存。若修复改用别的权威源，
   请同步改 FakeNet.classify() 并在报告里说明，不要改「镜像返回新数据」来凑绿。
 · contents API 同时支持两种响应：默认 {content: base64, sha} 包装，以及
   Accept: application/vnd.github.raw 的裸 JSON，两种修法都能被测到。
 · 修复后请原样复跑：.venv/Scripts/python.exe devtools/_verify_vehicle_index.py all
   通过判据 = ContractTests + MergeIndexTests 全绿 且 FixDependentTests 全绿
   且「非预期失败」为 0。

【最容易踩的坑】
 1. token 泄露（最危险）：为了「绕过缓存」或「给 CDN 请求鉴权」把 token 拼进
    URL query。一旦拼进去，describe_error(URLError) 会回显 exc.reason 里的 URL、
    _http_detail() 会回显响应正文、本机/服务端代理 access log 也会留存，
    三处同时漏。读公开仓库索引根本不需要 token；只用 Authorization 头。
 2. 超时变慢：新增一个串行 contents 请求再叠原有 8s _race → 最坏 10.5s+；
    且现 _race 全源失败会白等满 total_s。要共享预算，别简单相加。
 3. 破坏 MIRROR_PREFIXES 复用：图快把 5 个镜像复制一份字面量进 vehicle_share，
    会与 updater 的 best_prefix() 测速结果分叉、镜像挂了也改不动两处。
 4. QSettings 污染：验收/修复代码若调用 save_token/clear_token 或
    customization.save_vehicle，会改用户真实配置；全量 selftest.py 同理。
 5. 只加 URL 不改「先到先赢」：CDN 几乎总比 API 快，把 contents URL 追加进
    _mirrored() 的并发列表里必输，症状一模一样（仍显示「在线库还是空的」）。
 6. 缓存键没处理：contents API 也要防中间代理缓存（?ref=main + 时间戳 /
    Cache-Control: no-cache / Accept: raw）；别把响应的 sha 当成 index 内容。
 7. 响应格式分叉：contents API 默认返回 {content, encoding, sha} 包装而不是裸
    index JSON，解析分支写错会得到「索引结构不对（缺 packs 列表）」的假失败。
 8. 顺手「重构」_index_items / _merge_index：这两个是导入与上传链路共用契约，
    改返回值形状会连带炸 download_remote_pack() / update_index()。
 9. 绕过 _open：裸 urlopen / 新起 QNetworkAccessManager 会丢 UA 与 HTTPS_PROXY，
    在国内网络下 403/超时，还会让离线 harness 失效。
10. 只修 fetch_remote_index()，忘了 download_remote_pack() 仍走旧镜像链：
    索引能看见了，但点「下载」拿到 300s 前的旧包，属于同一根因的残留面。
11. 拿真实网络当验收：raw 的 max-age=300 一过，所有源都变新，未修的代码也会
    「看起来正常」；反过来镜像 #1/#3/#4/#5 常年超时，赢家随机。必须固定响应。
"""


def write_report(repro: dict | None, reg: dict | None) -> None:
    """报告随结果自适应：全绿=验收通过，修复用例 FAIL=基线，其余=待归因。

    ⚠ 报告措辞不能写死「Round 1 基线 / Routing: Engineer」—— 修复后复跑同一个
      脚本时，那会变成一份自相矛盾的验收结论（曾因此被工程师的 19/19 跑暴露）。
    """
    all_green = bool(reg) and reg["failed"] == 0 and reg["errors"] == 0
    baseline = bool(reg) and bool(reg["fix_failures"])
    if reg is None:
        phase = "交付报告（本进程未跑用例）"
        routing = "未判定（本次仅依据已有产物重生成报告）"
        gate = "  ⚠ 本次仅重生成报告，未执行 repro / test；结论见下方已存产物。"
    elif all_green:
        phase = "修复后回归验收（Round 2 判定）"
        routing = "NoOne（全部用例通过，修复验收通过）"
        gate = ("  ✔ 全部用例通过：FixDependentTests 已转绿，契约/降级/合并用例全绿，"
                "非预期失败 0。")
    elif baseline and not reg["unexpected"]:
        phase = "Round 1 基线（修复前）"
        routing = ("Engineer —— 断言符合 PRD，是实现给错结果（源码缺陷：读链路"
                   "只查镜像/raw，不查权威 contents API）。")
        gate = ("  ⚠ 按 team-lead 指示：代码尚未改完，本轮不跑修复后验证；"
                "Round 2 复跑需等通知。")
    else:
        phase = "回归复测（存在非预期失败）"
        routing = "Engineer / QA-self —— 存在非预期失败，需逐条归因。"
        gate = "  ⚠ 按 2 轮上限：剩余问题一律列入 Known Issues，不进入第 3 轮。"

    lines = [
        "=" * 100,
        f"在线车辆库「上传成功但刷新为空」— QA {phase} 报告（只读）",
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 100,
        "",
        "【交付物】",
        "  devtools/_verify_vehicle_index.py          本脚本（只 import  vehicle_share）",
        "  _check/vehicle_index_repro.txt/.json      真实网络逐源取证",
        "  _check/vehicle_index_regression.txt       离线回归用例运行输出",
        "  _check/vehicle_index_report.txt           本报告",
        "",
        f"【{phase} 结论】",
    ]
    if repro:
        result = repro["result"]
        contents = next((r for label, r in repro["probes"]
                         if "contents API" in label), {})
        replay = repro["payload"]["cached_window_replay"]
        winner = repro["payload"]["winner"]
        winner_is_api = "api.github.com" in str(winner.get("host") or "")
        first_items = len(replay["first_fetch"].get("items") or [])
        after_items = len(replay["refresh_after_upload"].get("items") or [])
        read1 = replay["read1_request_kinds"]["authoritative"]
        read2 = replay["read2_request_kinds"]["authoritative"]
        writes = replay["write_request_kinds"]["authoritative"]
        lines += [
            (f"  remote_index_urls() 共 {len(repro['urls'])} 条，"
             f"含 contents API? {any('api.github.com' in u for u in repro['urls'])}"),
            (f"  权威源 contents API: packs={contents.get('valid_items')} "
             f"names={contents.get('names')}  "
             f"Cache-Control={repro['payload']['contents_cache_control']}"),
            (f"  fetch_remote_index() 实测: ok={result.get('ok')} "
             f"items={len(result.get('items') or [])} "
             f"source={result.get('source')} ({repro['elapsed_ms']}ms)"),
        ]
        if winner_is_api and all_green:
            lines += [
                "  ✔ 真实网络赢家已是权威源 api.github.com，读链路不再依赖 CDN 旧缓存。",
                "    ⚠ 真实网络仍不能单独证明「刚上传完立刻刷新」—— 该判据以",
                "      离线固定响应与回归用例为准（TTL 一过未修代码也会看起来正常）。",
            ]
        else:
            lines += [
                "  ⚠ 本轮真实网络未复现空库：距用户上传已超过 raw 的 300s TTL，",
                (f"    CDN 已回源；赢家 {winner['host']}"
                 f"(Cache-Control={winner['cache_control']}) 也是新索引。"
                 "这是时间窗口现象，"),
                "    不是 bug 消失 —— 读链路不问权威源时，_race 先到先赢使赢家随机。",
            ]
        lines += [
            "  ✔ 离线固定响应回放 300s 窗口（权威源 packs=2 / 镜像恒 packs=0）：",
            (f"    第1次刷新 items={first_items}；update_index() 成功后立刻再刷新 "
             f"items={after_items}；两次刷新访问权威源次数={read1}/{read2}，"
             f"写链路={writes}。"),
        ]
        if all_green:
            lines += [
                "  → 修复已落地：读链路改为「contents API 权威源 + 镜像链并行 + 新鲜度择优」，",
                "    写后立刷不再受 300s CDN 缓存影响；根因已消除。",
            ]
        else:
            lines += [
                "  → 根因：写链路 update_index() 写 contents API（权威），读链路只查镜像/raw",
                "    （Cache-Control: max-age=300 的旧缓存），_race 先到先赢稳定拿 packs=0。",
                "    这就是「上传成功但刷新为空」/「在线库还是空的」的全部根因。",
            ]
        lines.append("  → 回归判据必须以离线固定响应为准，不能依赖外网 TTL 碰运气。")
    else:
        lines.append("  （本次未运行 repro，详见 _check/vehicle_index_repro.txt）")
    if reg:
        lines += [
            (f"  回归用例: Total {reg['total']} | Passed {reg['passed']} | "
             f"Failed {reg['failed']} | Errors {reg['errors']}"),
            f"  修复相关用例 FAIL: {sorted(reg['fix_failures']) or '无'}",
            f"  非预期失败: {sorted(reg['unexpected']) or '无（用例本身无 bug）'}",
            f"  Routing Decision: {routing}",
        ]
    else:
        lines.append("  （本次未运行 test，详见 _check/vehicle_index_regression.txt）")
    lines += [
        "",
        gate,
        "  最多 2 轮；Round 2 后仍失败的项目一律列入 Known Issues。",
        "",
        "=" * 100,
        "",
        ACCEPTANCE,
        "=" * 100,
    ]
    text = redact("\n".join(lines))
    (CHECK / "vehicle_index_report.txt").write_text(text, encoding="utf-8")
    sys.stdout.write(text)


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "all"
    if command not in {"all", "repro", "test", "report"}:
        sys.stderr.write(f"未知命令 {command!r}；可用: all | repro | test | report\n")
        return 2
    repro = run_repro() if command in {"all", "repro"} else None
    reg = run_regression() if command in {"all", "test"} else None
    if command in {"all", "report"}:
        write_report(repro, reg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
