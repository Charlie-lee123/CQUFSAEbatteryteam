# -*- coding: utf-8 -*-
r"""会话数据库（SQLite）：**每帧一行**、append-only，供跨天 / 按圈次 / 按模组检索。

## 为什么（用户："SQLite 会话库"）

文本日志是给人看的、CSV/JSON 是一次性快照 —— 跨天对比、按车号/模组翻旧数据
很弱。这里把每一帧落进 ``<基准目录>\历史数据\<车号>.db``（与近期数据
``近期数据\<车号>.log`` 平级分文件夹，用户 2026-11-21 要求），设置面板里看
统计、按天数导出。

## 一辆车一本库

``vehicle`` 列仍然照记（老库兼容 + 单库导出），但**文件按车号拆开**：
未选车 = ``未选择车号.db``，其余 = 车号安全名（非法字符→``_``、限长 80）。
好处是单本库小、打开快、删一台车不用动别人的数据。``set_db_path`` 一旦生效
（自测隔离用），**所有车号都读写那一本**。

## 数据从哪来（与 rx_export **同源**，不重写解析）

主窗口在 ``_emit_block`` 里、``align_indexes`` **之前**把**原始文本**交给
``rx_export.session().feed(...)`` 和本模块的 ``record_block(...)`` ——
同一份文本、同一个位置（对齐后的文本有补零/网格重排/断线 X，反解析必错）。

帧解析**不重写**：``_TapSession`` 继承 ``rx_export.Session``，只覆写
``_close_frame``，在解析器"收一帧"的那一刻把成品（电芯/温度整数数组）交给
回调。协议规则（序号.值、电压 4 位 mV / 温度 2 位℃、按模组切帧）仍然只有
``rx_export`` 一份。

## 不拖慢界面（硬需求）

* 主线程只做**入队**：``record_block`` 里最多读一次当前车号 + 取一次 ISO 时间，
  不碰数据库、不解析。
* **后台线程**解析 + **批量提交**：攒满 ``BATCH_FRAMES`` 帧或超过 ``BATCH_MS``
  就提交一次；库开 WAL（写不挡读）。每本库一个连接（``conns``），提交时按
  车号分组，各组写自己的库。
* 任何异常都吞掉并记 ``app_log``：落库是附赠功能，接收是主功能。

## 表

``frames(id, ts, vehicle, module, cells, temps, raw)``

* ``ts``      ISO 本地时间（毫秒精度），收帧那一刻
* ``vehicle`` 当前车号（``customization.current_vehicle()``），没选车是空串
* ``module``  该帧在其段（电压 / 温度）里的模组序号（1 起）；没有结构时为 NULL
* ``cells``   电芯 mV 整数 JSON 数组（温度帧是空数组）
* ``temps``   温度 ℃ 整数 JSON 数组（电压帧是空数组）
* ``raw``     这一帧所在**块**的原始文本（跨块续传的帧取"收帧块"的原文）
"""

from __future__ import annotations

import csv
import heapq
import json
import os
import queue
import re
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timedelta

import app_log
import customization
import rx_export

SUB_DIR = "历史数据"          # 历史数据独占的子目录（与近期数据平级分开）
NO_VEHICLE_NAME = "未选择车号"  # 未选车时的那本（与 rx_history 同口径）
DB_NAME = "sessions.db"      # ⚠️ **老版本**的单库名：只用于识别/迁移，不再写入
MIGRATED_MARK = ".migrated-v1"  # 迁移完成标记（放在 历史数据 子目录里）
BATCH_FRAMES = 50            # 攒够这么多帧就提交
BATCH_MS = 500.0             # 或安静这么久也提交（streaming 断流时不丢尾巴）
MAX_QUEUE = 2000             # 队列上限（≈8s 的块量）；满了丢新块，绝不阻塞接收
DROP_LOG_MS = 5000.0         # "队列满丢块"日志的最小间隔（免得刷爆日志）
_IN_BATCH = 64               # 历史分页"回表取 raw"每条 SQL 带多少个 ts

# 建表脚本：{s} 是 schema 前缀（正常建库为空串，迁移时是 "tgt."）。
_SCHEMA_TMPL = """
CREATE TABLE IF NOT EXISTS {s}frames (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    NOT NULL,
    vehicle TEXT    NOT NULL DEFAULT '',
    module  INTEGER,
    cells   TEXT    NOT NULL DEFAULT '[]',
    temps   TEXT    NOT NULL DEFAULT '[]',
    raw     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS {s}idx_frames_ts ON frames(ts);
CREATE INDEX IF NOT EXISTS {s}idx_frames_vehicle_ts ON frames(vehicle, ts);
"""

SCHEMA = _SCHEMA_TMPL.format(s="") + "PRAGMA user_version = 1;"

_INSERT_SQL = ("INSERT INTO frames(ts, vehicle, module, cells, temps, raw)"
               " VALUES(?,?,?,?,?,?)")

_lock = threading.Lock()
_q: queue.Queue | None = None
_worker: threading.Thread | None = None
_db_override: str | None = None
_base_override: str | None = None   # 自测用：把"基准目录"指到临时目录（见 _base_dir）
_migrate_lock = threading.Lock()
_migrated = False                   # 迁移只做一次（标记文件是跨进程的那一层）
_profile: tuple[list[int], list[int]] = ([], [])
_pending: list[tuple] = []           # 待提交的行（只由工作线程读写）
_last_drop_log = 0.0


# ---------------------------------------------------------------- 路径

def _safe_name(vehicle: str | None) -> str:
    """车号 → 文件名安全段（非法字符换成 ``_``，限长 80）。

    ⛔ 与 ``rx_history._safe_key`` **同规则**，但这里自己写一份：两个模块保持
    独立（谁都不 import 谁），改动互不牵连。
    """
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(vehicle or "").strip())
    return text.strip(" .")[:80]


def _base_dir() -> str | None:
    """基准目录 = 日志文件所在目录（``app_log.path()`` 的父目录）。

    ``_base_override`` 只在自测里指到临时目录（否则迁移用例就得碰用户真实库）。
    """
    if _base_override:
        return _base_override
    base = app_log.path()
    if not base:
        return None
    return os.path.dirname(base) or None


def _db_dir(create: bool = False) -> str | None:
    """历史数据目录 ``<基准目录>\\历史数据``；定位不到用户目录 → None。"""
    base = _base_dir()
    if not base:
        return None
    folder = os.path.join(base, SUB_DIR)
    if create:
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            return None
    return folder


def vehicle_db_path(vehicle: str | None) -> str | None:
    """某车号的库文件绝对路径；定位不到用户目录 → None（功能静默关闭）。

    ``set_db_path`` 生效时**所有车号都指向那一本**（自测隔离靠它）。
    """
    if _db_override:
        return _db_override
    folder = _db_dir()
    if not folder:
        return None
    name = _safe_name(vehicle) or NO_VEHICLE_NAME
    return os.path.join(folder, name + ".db")


def db_path() -> str | None:
    """当前库路径（测试覆盖优先；没有用户目录时为 None）。

    ⚠️ 只是"未选车那本"：真实写入按车号各走各的（``vehicle_db_path``）。
    保留这个无参签名是给设置面板显示路径、自测断言用的。
    """
    return _db_override or vehicle_db_path(None)


def _db_files() -> list[str]:
    """要读的所有库文件（覆盖生效时只有那一本；否则 历史数据 下全部 ``*.db``）。"""
    if _db_override:
        return [_db_override] if os.path.exists(_db_override) else []
    folder = _db_dir()
    if not folder:
        return []
    out: list[str] = []
    try:
        for entry in os.scandir(folder):
            if entry.name.endswith(".db") and entry.is_file():
                out.append(entry.path)
    except OSError:
        return []
    out.sort()
    return out


def set_db_path(path: str | None) -> None:
    """换库 / 复位：``None`` = 回到"按车号分文件夹"的默认布局。

    测试专用入口（自测**必须**写到临时库，绝不能碰用户真实库）。一旦设了
    ``path``，**所有车号都读写这一本**（隔离最彻底）。工作线程会先提交、再关掉
    所有旧连接；下次写入时按新路径惰性重开。
    """
    global _db_override
    with _lock:
        _db_override = str(path) if path else None
    _command("rebind", timeout=3.0)


def reset_for_tests() -> None:
    """清空解析器的续传状态（自测在换库/换用例之间调用）。"""
    global _profile
    with _lock:
        _profile = ([], [])
    _command("reset", timeout=3.0)


def warmup() -> None:
    """把写线程提前拉起来（**不等**任何回执，立刻返回）。

    为什么要提前：老库（单个 263 MB 的 ``sessions.db``）按车号拆分是一趟
    ``INSERT…SELECT`` 重活，写线程起来的第一步就做（见 ``_worker_main``）。
    如果等到"第一块数据到达"才起线程，那趟重活会正好压在接收路径上 ——
    队列只有 ``MAX_QUEUE`` 那么深，接得久一点就会丢块。主窗口构造完就调这里，
    让拆分在用户还在连串口的时候先跑完。
    """
    _ensure_worker()


# ---------------------------------------------------------------- 采集

def configure(cells: list[int] | None, temps: list[int] | None) -> None:
    """每模组的电芯/温度点数 —— 与 ``rx_export.session().configure`` 同一份口径。

    只在"一帧发了多个模组"的固件上才用得到（真机是一帧一个模组）；不同步的话
    这种固件在库里会被记成一行超长数组。
    """
    global _profile
    profile = ([int(c) for c in (cells or []) if int(c) > 0],
               [int(t) for t in (temps or []) if int(t) > 0])
    with _lock:
        if profile == _profile:
            return
        _profile = profile
        q = _q if (_worker is not None and _worker.is_alive()) else None
    if q is None:
        return              # 工作线程还没起：它启动时会读 _profile，不丢
    try:
        q.put_nowait(("configure", threading.Event(), profile))
    except queue.Full:
        pass                # 队列满就靠"下次 configure / 重启线程"读全局补上


def record_block(text: str, stamp: str | None = None) -> None:
    """入队一块**原始文本**（非阻塞，永不等 IO）。

    ``stamp`` 为空时用当前本地时间（ISO、毫秒）—— 测试可以传固定值。
    """
    if not text:
        return
    try:
        vehicle = customization.current_vehicle()
    except Exception:               # noqa: BLE001 - 配置读坏了也不能挡落库
        vehicle = ""
    if vehicle_db_path(vehicle) is None:
        return                      # 定位不到用户目录 = 功能静默关闭
    ts = stamp or datetime.now().isoformat(timespec="milliseconds")
    _enqueue(("block", ts, vehicle, text))


def flush(timeout: float = 3.0) -> bool:
    """等"队列里的块全部解析 + 提交落盘"（测试/退出用）。

    返回 True = 已落盘；False = 超时（工作线程忙或队列满）。
    """
    with _lock:
        alive = _worker is not None and _worker.is_alive()
    if not alive:
        return True                 # 从来没写过 = 没有待落盘的东西
    return _command("flush", timeout=timeout) is not None


# ---------------------------------------------------------------- 查询

def _read_conn(path: str | None = None) -> sqlite3.Connection | None:
    """只读连接（短命）。``path`` 为空 = ``db_path()``；库不存在直接 None。"""
    path = path or db_path()
    if not path or not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path, timeout=3.0)
        conn.execute("PRAGMA busy_timeout=3000")
        return conn
    except sqlite3.Error as exc:
        app_log.error(f"会话库打不开（读）: {exc}")
        return None


def count() -> int:
    """总帧数（所有车号的库累加；库不存在 = 0，不报错）。"""
    total = 0
    for path in _db_files():
        conn = _read_conn(path)
        if conn is None:
            continue
        try:
            row = conn.execute("SELECT COUNT(*) FROM frames").fetchone()
            total += int(row[0]) if row else 0
        except sqlite3.Error as exc:
            app_log.error(f"会话库统计失败: {exc}")
        finally:
            conn.close()
    return total


def stats() -> dict:
    """总帧数 + 覆盖时间范围 + 车号数（设置面板里**点了才查**，打开面板不查）。

    多本库累加：帧数求和、时间范围取并集、车号数求和（一台车只在自己那本库里，
    相加就是全部车号数；覆盖生效时只有一本，口径与旧版一致）。
    """
    out = {"count": 0, "first": "", "last": "", "vehicles": 0}
    for path in _db_files():
        conn = _read_conn(path)
        if conn is None:
            continue
        try:
            row = conn.execute(
                "SELECT COUNT(*), MIN(ts), MAX(ts), COUNT(DISTINCT vehicle)"
                " FROM frames").fetchone()
            if row:
                out["count"] += int(row[0] or 0)
                first, last = row[1] or "", row[2] or ""
                if first and (not out["first"] or first < out["first"]):
                    out["first"] = first
                if last and (not out["last"] or last > out["last"]):
                    out["last"] = last
                out["vehicles"] += int(row[3] or 0)
        except sqlite3.Error as exc:
            app_log.error(f"会话库统计失败: {exc}")
        finally:
            conn.close()
    return out


_STAMP_RE = re.compile(r"^\[[^\]]*\d[^\]]*\]$")


def _bucket_of(ts: str, interval: int) -> int | None:
    """ISO 时间 → **绝对**桶号 ``int(unix_ts // interval)``。

    ⛔ 不用"最早一帧为原点"：分页要靠前后两次查询的桶号一致，只有绝对桶号才
    前后一致（10/20/30/60/120/300 都整除 3600，桶边界正好落在整分钟上）。
    """
    try:
        return int(datetime.fromisoformat(ts).timestamp()) // interval
    except (ValueError, TypeError, OSError):
        return None


def _bucket_stream(path: str, vehicle: str | None, before_ts: str | None,
                   interval: int, limit: int | None = None):
    """逆序扫一本库：桶号**递减**，每桶只产出一次（= 该桶末帧）。

    逆序扫 + "每桶第一次遇到就收" = 该桶 ts 最大的那条，所以凑够本页要的桶数
    就能停 —— 查询成本是 O(页内帧数)，不再全表读进内存。

    ---- 两阶段（2026-11-21，实测 SQL 170.8ms → 52.6ms，结果逐行相同）---------
    老实现一条 SQL 边扫边取 ``raw``：为 300 个采样点要拖出 5.7 万行、每行 1.7 KB
    的正文，有效率 0.5%（见 ``devtools/_perf_replay_hist.py``）。改成两段：
      ① **只读 ts**：走覆盖索引 ``idx_frames_vehicle_ts``（查询计划是
         ``SEARCH frames USING COVERING INDEX``），Python 侧按绝对桶号去重，
         凑够 ``limit`` 个桶立刻停；
      ② 只对这 ``limit`` 个 ts **回表**取 ``raw``（按 ts 分批 IN 查询，
         ``ORDER BY ts DESC, id DESC`` 保证同一 ts 取 id 最大那条）。
    ⛔ 阶段 ① 只许 ``SELECT ts``：id 虽然跟在索引条目后面，但一把它写进选择列
       就容易被读成回表，整个优化白做；"同 ts 取最大 id"交给阶段 ②。
    ``limit`` 为空 = 不限制（保留老的"一条 SQL 拿全"路径，一字不改）。

    ``before_ts`` 是 history_page 上一页**最老桶的末帧 ts**（``page[0][1]``，
    不是桶边界）。下一页的 ``WHERE ts < before_ts`` 会把这一桶里更早的帧重新
    扫进来，而桶去重集合（``seen`` / ``seen_all``）是**每次扫描新建**的，跨页
    失效 —— 同一个 10 秒桶就会被反复吐出，采样间隔形同虚设。按采样间隔语义
    同一桶只该出一帧，所以这里把 ``before_ts`` 所在的整桶跳过（``skip_bucket``）：
    两条扫描路径（两阶段与老路径）都跳过。
    """
    conn = _read_conn(path)
    if conn is None:
        return
    where: list[str] = []
    args: list = []
    if vehicle:
        where.append("vehicle = ?")
        args.append(vehicle)
    if before_ts:
        where.append("ts < ?")
        args.append(before_ts)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    # before_ts 落在桶内部；下一页要跳过它所在的整桶（见 docstring）。
    skip_bucket = _bucket_of(before_ts, interval) if before_ts else None
    try:
        if limit is None:
            # 老路径：一次读全（只有 limit=None 的调用会走到）
            seen_all: set[int] = set()
            for ts, raw in conn.execute(
                    "SELECT ts, raw FROM frames" + clause
                    + " ORDER BY ts DESC, id DESC", args):
                bucket = _bucket_of(ts, interval)
                if bucket is None or bucket == skip_bucket or bucket in seen_all:
                    continue
                seen_all.add(bucket)
                yield bucket, str(ts), str(raw or "")
            return
        # ① 覆盖索引扫 ts，凑够 limit 个桶就停
        #    ⚠️ 多取**一个**桶（`> limit`）：`history_page` 靠"还能取到第 limit+1 个"
        #    才把 has_more 判成 True；只取到 limit 的话，凑巧正好 limit 个桶时会被
        #    误判成"没有更早的了"（往上翻页就断了）。多一个桶的成本 ≈ 一条采样点。
        picks: list[tuple[int, str]] = []
        seen: set[int] = set()
        sql_ts = "SELECT ts FROM frames" + clause + " ORDER BY ts DESC"
        for (ts,) in conn.execute(sql_ts, args):
            bucket = _bucket_of(ts, interval)
            if bucket is None or bucket == skip_bucket or bucket in seen:
                continue
            seen.add(bucket)
            picks.append((bucket, str(ts)))
            if len(picks) > limit:
                break
        if not picks:
            return
        # ② 只回表取这些 ts 的 raw（分批，消费者提前 break 就少取几批）
        #    过滤条件与 ① 完全同一组（vehicle、ts<?），参数字典序也一样 → 直接复用 args
        for start in range(0, len(picks), _IN_BATCH):
            batch = [ts for _b, ts in picks[start:start + _IN_BATCH]]
            marks = ",".join("?" * len(batch))
            sql2 = ("SELECT ts, raw FROM frames"
                    + (" WHERE " + " AND ".join(where + [f"ts IN ({marks})"]))
                    + " ORDER BY ts DESC, id DESC")
            got: dict[str, str] = {}
            for ts, raw in conn.execute(sql2, args + batch):
                key = str(ts)
                if key not in got:              # 同 ts：DESC 下第一条 = 最大 id
                    got[key] = str(raw or "")
            for bucket, ts in picks[start:start + _IN_BATCH]:
                if ts in got:
                    yield bucket, ts, got[ts]
    except sqlite3.Error as exc:
        app_log.error(f"历史数据查询失败: {exc}")
    finally:
        conn.close()


def _merge_streams(streams: list):
    """k 路归并（桶号从大到小）：同一桶来自多本库时取 ts 最大的那条。

    每本库各自已按桶号递减产出，堆里放各路"当前桶"，弹出的就是全局当前最大桶；
    同桶的多条相邻出现，顺手比 ts 取末帧。
    """
    heap: list[tuple] = []

    def _push(index: int) -> None:
        try:
            bucket, ts, raw = next(streams[index])
        except StopIteration:
            return
        heapq.heappush(heap, (-bucket, index, ts, raw))

    for i in range(len(streams)):
        _push(i)
    current: int | None = None
    best: tuple[str, str] | None = None
    while heap:
        neg, index, ts, raw = heapq.heappop(heap)
        bucket = -neg
        if current is not None and bucket != current:
            yield current, best[0], best[1]     # type: ignore[misc]
            best = None
        if best is None or ts > best[0]:
            best = (ts, raw)
        current = bucket
        _push(index)
    if current is not None and best is not None:
        yield current, best[0], best[1]


def _rows_to_lines(page: list[tuple[int, str, str]]) -> list[str]:
    """``[(桶号, ts, raw)]``（**老的在前**）→ 回放用的行列表。

    ``raw`` 可能是多行文本：按行拆开；若它自身不含时间戳行，则在最前面补一行
    ``[YYYY-MM-DD HH:MM:SS]``（取该帧 ts）—— ``_enter_replay`` 靠时间戳认"轮"、
    按轮切段排版。
    """
    out: list[str] = []
    for _bucket, ts, raw in page:
        lines = raw.splitlines()
        if not any(_STAMP_RE.match(line.strip()) for line in lines):
            try:
                stamp = datetime.fromisoformat(ts).strftime("[%Y-%m-%d %H:%M:%S]")
            except (ValueError, TypeError):
                stamp = f"[{ts}]"
            lines.insert(0, stamp)
        out.extend(lines)
    return out


def history_page(interval_sec: int, vehicle: str | None = None,
                 limit: int | None = None,
                 before_ts: str | None = None,
                 max_lines: int = 0) -> tuple[list[str], str | None, bool]:
    """**分页**读历史数据 → ``(行列表, 本页最早的 ts, 还有更早的吗)``。

    数据、分桶口径与 ``history_rows`` 完全一致（绝对桶号、每桶末帧、补时间戳），
    区别只在于它读的是"一页"：

    * ``limit``      本页最多几个采样点（``None`` = 全部）
    * ``before_ts``  只扫 ``ts < before_ts``（往更早翻页）
    * ``max_lines``  本页最多多少**行**（0 = 不限）。按"采样点"整块追加，
      所以实际行数最多超出 ``max_lines`` 一条采样点的行数 —— **绝不切半轮**。
      为什么要有它：一条采样点几行完全由数据源决定（E74 实测 24 行/点，
      别的车可能 8 行/点），只卡点数时"一页有多高、要花多久"是不可预测的。
    * 返回值 ② 是本页**实际收进去的最后一个（最老）**采样点的 ts —— 原样当下一次
      的 ``before_ts`` 就能往前翻，两页**不重复**（更老的桶 ts 一定小于它）。

    ⛔ 性能就是"打开快"的全部：逆序扫、凑够 ``limit`` 个桶立刻停，不全表读进
    内存。``vehicle`` 为空 = 全部车号（每本库一路游标做 k 路归并）。
    """
    try:
        interval = max(1, int(interval_sec or 10))
    except (TypeError, ValueError):
        interval = 10
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = None
        if limit is not None and limit < 0:
            limit = None                    # 旧口径：负数 = 不限制
    try:
        max_lines = int(max_lines or 0)
    except (TypeError, ValueError):
        max_lines = 0
    if max_lines < 0:
        max_lines = 0                       # 负数 = 不限（0 才是"不限"的唯一写法）
    if limit == 0:
        return [], None, False

    paths = _db_files()
    if vehicle:
        own = vehicle_db_path(vehicle)
        if own and own in paths:
            paths = [own]                   # 该车号自己的库（快路径）
    if not paths:
        return [], None, False

    streams = [_bucket_stream(p, vehicle, before_ts, interval, limit)
               for p in paths]
    page: list[tuple[int, str, str]] = []
    chunks: list[list[str]] = []
    total_lines = 0
    has_more = False
    try:
        for item in _merge_streams(streams):
            if limit is not None and len(page) >= limit:
                has_more = True             # 还有更早的桶没放进本页
                break
            if max_lines and total_lines >= max_lines:
                has_more = True             # 行数够了（整点收完才断，不切半轮）
                break
            page.append(item)
            chunk = _rows_to_lines([item])
            chunks.append(chunk)
            total_lines += len(chunk)
    finally:
        for stream in streams:
            stream.close()                  # 提前 break 也要把库连接关掉
    if not page:
        return [], None, False
    page.reverse()                          # 归并是"新→老"，回放要"老→新"
    lines: list[str] = []
    for chunk in reversed(chunks):
        lines.extend(chunk)
    return lines, page[0][1], has_more


def history_rows(interval_sec: int, vehicle: str | None = None,
                 limit: int | None = None) -> list[str]:
    """永不删除的**历史数据**：按采样间隔从 ``frames`` 抽帧，返回可回放的行列表。

    与 ``rx_history``（近期数据，每轮一行、按保留行数裁剪、看瞬时报错）严格分层：
    这里读的是 append-only 的会话库，**从不 DELETE / TRUNCATE** 任何帧；采样只在
    **查询侧**做，所以改采样间隔不用动已存数据（改间隔立刻生效、历史不丢）。

    分桶口径见 ``history_page``（绝对桶号、每桶取末帧）；``vehicle`` 为空表示全部
    车号；``limit`` 非空只留最近 N 个采样点（回放默认停最新）。要分页请直接用
    ``history_page``。
    """
    lines, _earliest, _more = history_page(interval_sec, vehicle, limit)
    return lines


def export_csv(path: str, days: int = 7) -> int:
    """把最近 ``days`` 天的帧导成 CSV（一行 = 一帧），返回数据行数。

    UTF-8-BOM（Excel 双击不乱码）；电芯/温度展开成**空格分隔的整数串**，
    和真机原文的排法一致、也方便 split。多本库按文件名顺序依次追加（车号列
    照常区分），一本都没有时不建文件、返回 0。
    """
    span = max(1, int(days))
    since = (datetime.now() - timedelta(days=span)).isoformat(timespec="milliseconds")
    files = _db_files()
    if not files:
        return 0
    rows = 0
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["时间", "车号", "模组", "电芯(mV)", "温度(℃)", "原始文本"])
            for db in files:
                conn = _read_conn(db)
                if conn is None:
                    continue
                try:
                    cur = conn.execute(
                        "SELECT ts, vehicle, module, cells, temps, raw FROM frames"
                        " WHERE ts >= ? ORDER BY id", (since,))
                    for ts, vehicle, module, cells, temps, raw in cur:
                        writer.writerow([ts, vehicle, "" if module is None else module,
                                         _join_values(cells, 4), _join_values(temps),
                                         raw])
                        rows += 1
                except sqlite3.Error as exc:
                    app_log.error(f"会话库导出失败: {exc}")
                finally:
                    conn.close()
    except OSError as exc:
        app_log.error(f"会话库导出失败: {exc}")
    return rows


def _join_values(payload: str, width: int = 0) -> str:
    """``"[3851, 3849]"`` → ``"3851 3849"``（坏 JSON 原样返回，不炸导出）。

    ``width > 0`` 时对每个数值按该宽度左零补齐（电压 4 位补齐用），温度等
    传 ``0`` 保持原样。``v`` 已是 ``int``（0..9999），``:0{width}d`` 只补零不截断。
    """
    try:
        values = json.loads(payload or "[]")
    except (TypeError, ValueError):
        return payload or ""
    if not isinstance(values, list):
        return payload or ""
    return " ".join(f"{v:0{width}d}" if width else str(v) for v in values)


# ---------------------------------------------------------------- 老库迁移

def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _legacy_summary(legacy: str) -> tuple[int, dict[str, int]]:
    """老库 → ``(总行数, {车号: 行数})``。"""
    conn = sqlite3.connect(legacy, timeout=10.0)
    try:
        rows = conn.execute(
            "SELECT vehicle, COUNT(*) FROM frames GROUP BY vehicle").fetchall()
    finally:
        conn.close()
    groups = {str(vehicle or ""): int(count or 0) for vehicle, count in rows}
    return sum(groups.values()), groups


def _copy_vehicle(legacy: str, vehicle: str, tmp: str) -> int:
    """把一个车号的帧从老库搬进**临时库**，返回实际写入行数（失败清掉临时文件）。

    ⛔ 先写 ``<目标>.migrating`` 再 ``os.replace`` 成正式库：中途失败绝不留半本
    库，重跑时覆盖重建，也不会产生重复行。
    """
    _remove_file(tmp)
    try:
        conn = sqlite3.connect(tmp, timeout=10.0)
        try:
            conn.executescript(SCHEMA)               # 全新库：建表 + 索引
            conn.execute("ATTACH DATABASE ? AS legacy", (legacy,))
            conn.execute(
                "INSERT INTO frames(ts, vehicle, module, cells, temps, raw)"
                " SELECT ts, vehicle, module, cells, temps, raw FROM legacy.frames"
                " WHERE vehicle = ? ORDER BY ts, id", (vehicle,))
            conn.commit()
            row = conn.execute("SELECT COUNT(*) FROM frames").fetchone()
            count = int(row[0]) if row else 0
            conn.execute("DETACH DATABASE legacy")
            return count
        finally:
            conn.close()
    except sqlite3.Error:
        _remove_file(tmp)
        raise


def _do_migrate() -> bool:
    """把老的单库 ``<基准目录>/sessions.db`` 拆进 ``历史数据/<车号>.db``。

    返回 True = 状态干净（已迁移过 / 没有老库 / 本次成功）。任何失败都返回 False
    并把老库**原样留着**、不写完成标记（下次启动重试）。

    ⛔ 老库只读不删：成功后只改名成 ``sessions.db.migrated``（留证据）。
    """
    if _db_override:
        return False                 # 自测/隔离库生效：绝不碰真实老库
    base = _base_dir()
    if not base:
        return False                 # 定位不到用户目录：保持原样
    folder = _db_dir()
    if not folder:
        return False
    if os.path.exists(os.path.join(folder, MIGRATED_MARK)):
        return True                  # 迁移过（标记文件是跨进程的那一层）
    legacy = os.path.join(base, DB_NAME)
    if not os.path.exists(legacy):
        return True                  # 新装用户：没有老库要搬

    started = time.monotonic()
    try:
        total, groups = _legacy_summary(legacy)
    except sqlite3.Error as exc:
        app_log.error(f"老会话库读不出来，迁移跳过: {exc}")
        return False

    # 两个车号可能映射到同一个安全名（"A/B" 与 "A_B"）：那就是要互相覆盖 → 拒绝
    names = {}
    for vehicle in groups:
        names.setdefault(_safe_name(vehicle) or NO_VEHICLE_NAME, []).append(vehicle)
    clash = {name: vs for name, vs in names.items() if len(vs) > 1}
    if clash:
        app_log.error(f"会话库迁移中止：车号安全名冲突 {clash}（老库未改动）")
        return False

    folder = _db_dir(create=True)
    if not folder:
        return False

    migrated = 0
    try:
        for vehicle, expected in groups.items():
            name = _safe_name(vehicle) or NO_VEHICLE_NAME
            target = os.path.join(folder, name + ".db")
            tmp = target + ".migrating"
            got = _copy_vehicle(legacy, vehicle, tmp)
            if got != expected:
                raise RuntimeError(
                    f"{vehicle or NO_VEHICLE_NAME}：目标 {got} 行 ≠ 老库 {expected} 行")
            os.replace(tmp, target)
            migrated += got
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        app_log.error(f"会话库迁移失败（老库原样保留，下次重试）: {exc}")
        return False
    if migrated != total:
        app_log.error(f"会话库迁移帧数不匹配：{migrated} ≠ 老库 {total}"
                      "（老库原样保留，下次重试）")
        return False

    # 老库改名留证据（不删）；改名失败也不算灾难：目标库已是完整的，重跑会覆盖重建
    try:
        os.replace(legacy, legacy + ".migrated")
    except OSError as exc:
        app_log.error(f"老会话库改名失败（数据已迁移完成）: {exc}")
        return False
    for suffix in ("-wal", "-shm"):
        _remove_file(legacy + suffix)

    try:
        with open(os.path.join(folder, MIGRATED_MARK), "w", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {total}\n")
    except OSError as exc:
        app_log.error(f"迁移标记写不进去（下次会重跑一遍，幂等）: {exc}")

    elapsed = time.monotonic() - started
    app_log.info(f"会话库迁移完成：{total} 帧 / {len(groups)} 个车号 → "
                 f"{folder}（{elapsed:.1f}s）")
    return True


def _migrate_legacy_inline() -> bool:
    """进程内只做一次（真正干活的是 ``_do_migrate``）。"""
    global _migrated
    with _migrate_lock:
        if _migrated:
            return True
        ok = _do_migrate()
        if ok:
            _migrated = True
        return ok


def migrate_legacy(timeout: float = 60.0) -> bool:
    """让**写线程**把老库拆进 ``历史数据``（幂等；测试/收尾用）。

    正常情况下不用调：写线程起来的第一步自己就会做（见 ``_worker_main``）。
    这里只是给自测一个"同步等它做完"的入口 —— 线程起不来时就地做。
    """
    if _ensure_worker() is not None and _command("migrate", timeout=timeout) is not None:
        with _migrate_lock:
            return _migrated
    return _migrate_legacy_inline()


# ---------------------------------------------------------------- 工作线程

class _TapSession(rx_export.Session):
    """在 ``rx_export`` 的解析器上搭一个**抽头**：收帧那一刻把成品交出去。

    ⛔ 不重写解析：kind 状态机、标题识别、``SAMPLE_RE``、位宽换算、按模组切帧
    全部沿用父类；这里只在 ``_close_frame``（= "一帧收进本轮"的唯一出口）
    前后对比模块数，把新增的模块交给 ``sink``。
    """

    def __init__(self, sink) -> None:
        super().__init__()
        self._sink = sink
        # 当前块的归属（ts / 原文）与**当前帧**的归属 —— 必须分开：
        # 收帧发生在"下一个块"的标题上（``Voltage590`` 的帧是被下一块
        # ``Voltage591:`` 收掉的），用"收帧那一刻的块"打戳会把每帧都记到
        # 下一块头上（ts/raw 整体错一拍，实测抓出来的）。
        self._block_ts = ""
        self._block_raw = ""
        self._frame_ts = ""
        self._frame_raw = ""
        self._vehicle = ""

    def feed_block(self, text: str, ts: str, vehicle: str) -> None:
        self._block_ts, self._block_raw, self._vehicle = ts, text, vehicle
        if not self._frame:
            # 帧从本块开始（非续传）→ 归属本块
            self._frame_ts, self._frame_raw = ts, text
        # 续传帧（上块开始、本块继续）沿用**起始块**的归属，不覆盖
        self.feed(text)

    def flush_pending(self) -> None:
        """把"还在等下一个标题"的尾巴帧收掉（flush / 关窗 / 换库前调用）。

        平时收帧靠"下一个标题 / 段切换 / BMS 行"；静默流最后一帧等不到下一个
        标题 —— 不主动收就会永远停在内存里，最后一次车检的末帧丢失。
        """
        if self._frame:
            self._close_frame()

    def _close_frame(self) -> None:
        before_v = len(self._cur["volt"]) if self._cur else 0
        before_t = len(self._cur["temp"]) if self._cur else 0
        super()._close_frame()
        cur = self._cur
        if cur is None:
            return
        for i, module in enumerate(cur["volt"][before_v:], start=before_v + 1):
            self._sink(self._frame_ts, self._vehicle, i, list(module), [],
                       self._frame_raw)
        for i, module in enumerate(cur["temp"][before_t:], start=before_t + 1):
            self._sink(self._frame_ts, self._vehicle, i, [], list(module),
                       self._frame_raw)
        # 本块里收帧之后若还有新帧（多标题块），归属回到本块
        self._frame_ts, self._frame_raw = self._block_ts, self._block_raw


def _sink_row(ts: str, vehicle: str, module: int,
              cells: list[int], temps: list[int], raw: str) -> None:
    _pending.append((ts, vehicle, module,
                     json.dumps(cells, separators=(",", ":")),
                     json.dumps(temps, separators=(",", ":")),
                     raw))


def _open_conn(path: str | None = None) -> sqlite3.Connection | None:
    """工作线程独占的写连接（WAL；表不存在就建 —— 老版本升级上来不报错）。

    ``path`` 为空 = ``db_path()``（未选车那本）。一辆车一本库，所以这里是
    "某一本库的连接"，由工作线程按车号缓存（见 ``_commit``）。
    """
    path = path or db_path()
    if not path:
        return None
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        conn = sqlite3.connect(path, timeout=3.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=3000")
        conn.executescript(SCHEMA)
        conn.commit()
        return conn
    except (sqlite3.Error, OSError) as exc:
        app_log.error(f"会话库打不开: {exc}")
        return None


def _close_conns(conns: dict[str, sqlite3.Connection]) -> None:
    """关掉所有写连接（换库 / 退出时）。"""
    for conn in conns.values():
        try:
            conn.close()
        except sqlite3.Error:
            pass
    conns.clear()


def _commit(conns: dict[str, sqlite3.Connection], rows: list[tuple]) -> None:
    """按**车号分组**提交：每组写自己那本库（覆盖生效时全部落到同一本）。

    ``conns`` 是"库路径 → 连接"的缓存，缺哪本开哪本。定位不到用户目录
    （``vehicle_db_path`` 为 None）的那批直接丢 —— 与旧版"功能静默关闭"一致；
    写失败也丢这批（落库是附赠功能，绝不回灌重试把队列拖死）。
    """
    if not rows:
        return
    groups: dict[str, list[tuple]] = {}
    for row in rows:
        path = vehicle_db_path(row[1])
        if not path:
            continue
        groups.setdefault(path, []).append(row)
    for path, part in groups.items():
        conn = conns.get(path)
        if conn is None:
            conn = _open_conn(path)
            if conn is None:
                continue
            conns[path] = conn
        try:
            conn.executemany(_INSERT_SQL, part)
            conn.commit()
        except sqlite3.Error as exc:
            app_log.error(f"会话库写入失败: {exc}")
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
    rows.clear()


def _worker_main() -> None:
    global _profile
    conns: dict[str, sqlite3.Connection] = {}
    tap = _TapSession(_sink_row)
    with _lock:
        profile = _profile
    tap.configure(*profile)
    # 老库一次性迁移：写线程起来的第一件事（UI 线程不等；幂等，失败下次再来）
    _migrate_legacy_inline()
    last_commit = time.monotonic()
    while True:
        with _lock:
            q = _q
        if q is None:
            _close_conns(conns)
            return
        wait = max(0.01, BATCH_MS / 1000.0 - (time.monotonic() - last_commit))
        try:
            item = q.get(timeout=wait)
        except queue.Empty:
            item = None
        tag = item[0] if item else ""
        if tag == "block":
            try:
                tap.feed_block(item[3], item[1], item[2])
            except Exception:                    # noqa: BLE001 - 解析异常不拖垮线程
                app_log.error("会话库解析异常: " + traceback.format_exc(limit=2))
        elif tag == "configure":
            try:
                tap.configure(*item[2])
            except Exception:                    # noqa: BLE001
                app_log.error("会话库配置异常: " + traceback.format_exc(limit=2))
            item[1].set()
        elif tag == "reset":
            try:
                tap.clear()
            except Exception:                    # noqa: BLE001
                app_log.error("会话库复位异常: " + traceback.format_exc(limit=2))
            item[1].set()
        elif tag == "migrate":
            _migrate_legacy_inline()
            item[1].set()
        elif tag in ("flush", "rebind", "stop"):
            # ① 静默流的尾巴帧（还在等下一个标题）先收掉 —— 否则最后一帧丢
            try:
                tap.flush_pending()
            except Exception:                    # noqa: BLE001
                app_log.error("会话库收尾异常: " + traceback.format_exc(limit=2))
            # ② 待提交的行（含"安静流的尾巴"）落盘 —— 缺连接由 _commit 惰性补开
            _commit(conns, _pending)
            if tag in ("rebind", "stop"):
                _close_conns(conns)
            item[1].set()
            if tag == "stop":
                return
        # 批量提交：帧数够 或 距上次提交超过 BATCH_MS（安静时也收尾）
        if _pending and (len(_pending) >= BATCH_FRAMES
                         or (time.monotonic() - last_commit) * 1000.0 >= BATCH_MS):
            _commit(conns, _pending)
            last_commit = time.monotonic()


def _ensure_worker() -> queue.Queue | None:
    global _q, _worker
    with _lock:
        if _q is None:
            _q = queue.Queue(maxsize=MAX_QUEUE)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_main, name="rx-store", daemon=True)
            _worker.start()
        return _q


def _enqueue(item) -> bool:
    q = _ensure_worker()
    if q is None:
        return False
    try:
        q.put_nowait(item)
        return True
    except queue.Full:
        global _last_drop_log
        now = time.monotonic() * 1000.0
        if now - _last_drop_log >= DROP_LOG_MS:
            _last_drop_log = now
            app_log.warning("会话库队列已满，丢弃新块（写盘跟不上接收速度）")
        return False


def _command(tag: str, timeout: float = 3.0) -> threading.Event | None:
    """发一条"带应答"的命令并等它执行完；工作线程还没起来时返回 None。"""
    with _lock:
        alive = _q is not None and _worker is not None and _worker.is_alive()
    if not alive:
        return None
    ev = threading.Event()
    q = _ensure_worker()
    if q is None:
        return None
    try:
        q.put((tag, ev), timeout=timeout)
    except queue.Full:
        return None
    return ev if ev.wait(timeout) else None


def shutdown(timeout: float = 3.0) -> None:
    """退出前收尾：提交尾巴并停线程（程序关闭时调用）。"""
    _command("stop", timeout=timeout)
