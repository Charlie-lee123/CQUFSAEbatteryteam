# -*- coding: utf-8 -*-
r"""会话数据库（SQLite）：**每帧一行**、append-only，供跨天 / 按圈次 / 按模组检索。

## 为什么（用户："SQLite 会话库"）

文本日志是给人看的、CSV/JSON 是一次性快照 —— 跨天对比、按车号/模组翻旧数据
很弱。这里把每一帧落进 ``sessions.db``（与日志同一个目录），设置面板里看统计、
按天数导出。

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
  就提交一次；库开 WAL（写不挡读）。
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
import json
import os
import queue
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timedelta

import app_log
import customization
import rx_export

DB_NAME = "sessions.db"
BATCH_FRAMES = 50            # 攒够这么多帧就提交
BATCH_MS = 500.0             # 或安静这么久也提交（streaming 断流时不丢尾巴）
MAX_QUEUE = 2000             # 队列上限（≈8s 的块量）；满了丢新块，绝不阻塞接收
DROP_LOG_MS = 5000.0         # "队列满丢块"日志的最小间隔（免得刷爆日志）

SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    NOT NULL,
    vehicle TEXT    NOT NULL DEFAULT '',
    module  INTEGER,
    cells   TEXT    NOT NULL DEFAULT '[]',
    temps   TEXT    NOT NULL DEFAULT '[]',
    raw     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_frames_ts ON frames(ts);
CREATE INDEX IF NOT EXISTS idx_frames_vehicle_ts ON frames(vehicle, ts);
PRAGMA user_version = 1;
"""

_lock = threading.Lock()
_q: queue.Queue | None = None
_worker: threading.Thread | None = None
_db_override: str | None = None
_profile: tuple[list[int], list[int]] = ([], [])
_pending: list[tuple] = []           # 待提交的行（只由工作线程读写）
_last_drop_log = 0.0


# ---------------------------------------------------------------- 路径

def _default_db_path() -> str | None:
    """默认库位置：与文本日志**同一个目录**（``app_log.path()`` 的父目录）。

    定位不到用户目录（app_log 打不开）就返回 None —— 整个功能静默关闭，
    绝不往当前工作目录里乱写。
    """
    base = app_log.path()
    if not base:
        return None
    return os.path.join(os.path.dirname(base), DB_NAME)


def db_path() -> str | None:
    """当前库路径（测试覆盖优先；没有用户目录时为 None）。"""
    return _db_override or _default_db_path()


def set_db_path(path: str | None) -> None:
    """换库 / 复位：``None`` = 回到默认目录下的 ``sessions.db``。

    测试专用入口（自测**必须**写到临时库，绝不能碰用户真实库）。工作线程会
    先提交、再关掉旧连接；下次写入时按新路径惰性重开。
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
    if db_path() is None:
        return                      # 定位不到用户目录 = 功能静默关闭
    ts = stamp or datetime.now().isoformat(timespec="milliseconds")
    try:
        vehicle = customization.current_vehicle()
    except Exception:               # noqa: BLE001 - 配置读坏了也不能挡落库
        vehicle = ""
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

def _read_conn() -> sqlite3.Connection | None:
    """只读连接（短命）。库还不存在就直接 None —— 老版本没有这个库是正常状态。"""
    path = db_path()
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
    """总帧数（库不存在 = 0，不报错）。"""
    conn = _read_conn()
    if conn is None:
        return 0
    try:
        row = conn.execute("SELECT COUNT(*) FROM frames").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error as exc:
        app_log.error(f"会话库统计失败: {exc}")
        return 0
    finally:
        conn.close()


def stats() -> dict:
    """总帧数 + 覆盖时间范围 + 车号数（设置面板里**点了才查**，打开面板不查）。"""
    out = {"count": 0, "first": "", "last": "", "vehicles": 0}
    conn = _read_conn()
    if conn is None:
        return out
    try:
        row = conn.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts), COUNT(DISTINCT vehicle) FROM frames"
        ).fetchone()
        if row:
            out = {"count": int(row[0] or 0), "first": row[1] or "",
                   "last": row[2] or "", "vehicles": int(row[3] or 0)}
    except sqlite3.Error as exc:
        app_log.error(f"会话库统计失败: {exc}")
    finally:
        conn.close()
    return out


def export_csv(path: str, days: int = 7) -> int:
    """把最近 ``days`` 天的帧导成 CSV（一行 = 一帧），返回数据行数。

    UTF-8-BOM（Excel 双击不乱码）；电芯/温度展开成**空格分隔的整数串**，
    和真机原文的排法一致、也方便 split。
    """
    conn = _read_conn()
    if conn is None:
        return 0
    span = max(1, int(days))
    since = (datetime.now() - timedelta(days=span)).isoformat(timespec="milliseconds")
    rows = 0
    try:
        cur = conn.execute(
            "SELECT ts, vehicle, module, cells, temps, raw FROM frames"
            " WHERE ts >= ? ORDER BY id", (since,))
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["时间", "车号", "模组", "电芯(mV)", "温度(℃)", "原始文本"])
            for ts, vehicle, module, cells, temps, raw in cur:
                writer.writerow([ts, vehicle, "" if module is None else module,
                                 _join_values(cells, 4), _join_values(temps), raw])
                rows += 1
    except (sqlite3.Error, OSError) as exc:
        app_log.error(f"会话库导出失败: {exc}")
        return rows
    finally:
        conn.close()
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


def _open_conn() -> sqlite3.Connection | None:
    """工作线程独占的写连接（WAL；表不存在就建 —— 老版本升级上来不报错）。"""
    path = db_path()
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


def _commit(conn: sqlite3.Connection | None, rows: list[tuple]) -> None:
    if not rows:
        return
    if conn is None:
        rows.clear()
        return
    try:
        conn.executemany(
            "INSERT INTO frames(ts, vehicle, module, cells, temps, raw)"
            " VALUES(?,?,?,?,?,?)", rows)
        conn.commit()
    except sqlite3.Error as exc:
        app_log.error(f"会话库写入失败: {exc}")
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
    finally:
        rows.clear()


def _worker_main() -> None:
    global _profile
    conn: sqlite3.Connection | None = None
    tap = _TapSession(_sink_row)
    with _lock:
        profile = _profile
    tap.configure(*profile)
    last_commit = time.monotonic()
    while True:
        with _lock:
            q = _q
        if q is None:
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
        elif tag in ("flush", "rebind", "stop"):
            # ① 静默流的尾巴帧（还在等下一个标题）先收掉 —— 否则最后一帧丢
            try:
                tap.flush_pending()
            except Exception:                    # noqa: BLE001
                app_log.error("会话库收尾异常: " + traceback.format_exc(limit=2))
            # ② "待提交但还没开过连接"的行补一次连接：安静流的尾巴必须能落盘
            if _pending and conn is None:
                conn = _open_conn()
            _commit(conn, _pending)
            if tag in ("rebind", "stop") and conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
                conn = None
            item[1].set()
            if tag == "stop":
                return
        # 批量提交：帧数够 或 距上次提交超过 BATCH_MS（安静时也收尾）
        if _pending and (len(_pending) >= BATCH_FRAMES
                         or (time.monotonic() - last_commit) * 1000.0 >= BATCH_MS):
            if conn is None:
                conn = _open_conn()
            _commit(conn, _pending)
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
