# -*- coding: utf-8 -*-
"""历史接收数据：自动保存最近的接收内容（含时间戳行）。

- **存哪**：``<基准目录>\\近期数据\\<车号>.log``（用户 2026-11-21："把近期数据和
  历史数据分成不同的文件夹，也要按照车号分"）。基准目录默认与日志文件同一目录
  （``app_log`` 的 logs 目录，跟着软件走）；用户在设置面板选了别的目录就用那个
  （``set_directory`` 持久化到 ``custom/rx_history_dir``，``directory()`` 是唯一
  读入口）。未选车 = ``未选择车号.log``；老版本的 ``接收历史-<车号>.log`` 会在
  首次定位时**自动搬进**子目录（见 ``_claim_legacy``），搬一次就完。
- **存什么**：接收区实际显示的行——已含时间戳行、段重排后的数据行；
  HEX 视图存的就是 HEX 文本。逐帧喂 ``feed()``，尾部换行由这里统一收口。
- **存多少**：**跟随接收区的「保留行数」**（用户 2026-10-13 点名："本地留存行数
  数量与接收区设置的行数保持一致"）。接收区改了行数会调 ``set_max_lines()``
  同步。默认值与接收区默认一致（5000）。
- **怎么滚**：内存 deque 是唯一事实来源；启动时把文件里已有的尾巴读回来接着用。
  落盘是**追加**，文件超阈值（上限 + COMPACT_EXTRA）才整体重写一次，平时就是
  几行的 append，不构成接收路径上的开销。
- **为什么不直接每帧写盘**：接收路径上的任何 IO 都可能撞上磁盘抖动
  （杀毒扫描、机械盘），把收包线程卡出毛刺。这里只在内存里入队，
  由 1 秒定时器批量刷盘 —— 接收性能零影响，最多丢最后一秒的尾巴（可接受）。
- **谁在用**：main_window._append_display（显示帧的唯一入口，暂停恢复的
  批量刷帧也走它）与 _on_rx_lines_changed（同步行数）；ui_settings 的
  「接收历史」行打开记录文件；main_window 的「打开历史数据」按钮回放。
"""

from __future__ import annotations

import os
import re
from collections import deque
from pathlib import Path

DEFAULT_MAX_LINES = 5000
COMPACT_EXTRA = 500      # 文件比上限多出这么多行才整体重写一次
FLUSH_MS = 1000          # 批量刷盘周期
SUB_DIR = "近期数据"      # 近期数据独占的子目录（与历史数据平级分开）
FILE_NAME = "未选择车号.log"   # 未选车时的那本
LEGACY_PREFIX = "接收历史"     # 老版本的单文件前缀（只用于迁移/兼容扫描）

_ORG, _APP = "MimoWorks", "SerialAssistant"
DIR_KEY = "custom/rx_history_dir"      # 用户手动指定的历史目录（空 = 跟日志同目录）

_max_lines = DEFAULT_MAX_LINES
_lines: deque[str] = deque(maxlen=_max_lines)
_pending: list[str] = []
_file_lines = 0          # 文件当前有多少行（内存记账，免得反复数文件）
_path: Path | None = None
_current_key: str | None = None         # load() 记住的车号，切目录时用它重新定位
_timer = None            # QTimer，惰性创建（要等 QApplication 存在）


def max_lines() -> int:
    """当前留存上限（行）。"""
    return _max_lines


def set_max_lines(value: int) -> None:
    """把留存上限改成 ``value`` 行（跟随接收区「保留行数」）。

    deque 的 maxlen 不能原地改，换一个新 deque 并把尾巴搬过去（旧的那头自然
    丢掉）；已经在文件里的多余行标脏，下一次 compact 收回来。
    """
    global _max_lines, _lines, _file_lines
    value = max(1, int(value))
    if value == _max_lines:
        return
    # 先取旧行、再换 deque：不能写成 deque(_lines, maxlen=value) 之外的
    # "复用 _lines 自身"写法，否则 maxlen 取的是旧值（实测踩过）。
    kept = list(_lines)[-value:]
    _max_lines = value
    _lines = deque(kept, maxlen=value)
    # 上限调小后文件里可能还有多余行：标脏，下次 flush 必触发一次 compact
    if _file_lines > value + COMPACT_EXTRA:
        _file_lines = value + COMPACT_EXTRA + 1
    return None


def _resolve_dir() -> Path:
    """与日志同目录：直接取日志文件的父目录；日志没起来就退回同一套候选。"""
    import app_log
    log_path = app_log.path()
    if log_path:
        return Path(log_path).parent
    return Path.home() / ".serialassistant" / "logs"


# 原函数备份：``directory()`` 靠它判断"这个函数有没有被自测替换过"（见那里的说明）。
_DEFAULT_RESOLVER = _resolve_dir


def _settings():
    """QSettings("MimoWorks","SerialAssistant") —— 与 customization 同一个存储。"""
    from PySide6.QtCore import QSettings
    return QSettings(_ORG, _APP)


def custom_directory() -> str | None:
    """用户手动指定的历史目录；没设过（或写成了 null）返回 None。"""
    try:
        value = _settings().value(DIR_KEY, "")
    except Exception:                   # noqa: BLE001 - 读不到配置就当没设过
        return None
    if value is None:
        return None                     # 显式写成 null（快照恢复会这样）→ 当没设过
    text = str(value).strip()
    return text or None


def directory() -> str | None:
    """当前历史目录的绝对路径（**默认值保持现状**：跟日志同目录）。

    手动选过目录就走用户那个（``set_directory``），否则原样返回 ``_resolve_dir()``。
    ⚠️ 每次现调 ``_resolve_dir()``（不要在 import 期缓存）：selftest 就是靠临时
    替换这个函数把历史写到隔离目录的。
    """
    # ⛔ 自测会把 ``_resolve_dir`` 指到临时目录（MEMORY：「历史污染」是硬约定）。
    #    被换过 = **隔离优先**，无视用户的自定义目录 —— 否则跑一次自测就把测试帧
    #    写进用户真正的历史文件夹里了。
    if _resolve_dir is not _DEFAULT_RESOLVER:
        try:
            return str(_resolve_dir())
        except OSError:
            return None
    custom = custom_directory()
    if custom:
        return custom
    try:
        return str(_resolve_dir())
    except OSError:
        return None


def _data_dir() -> Path | None:
    """近期数据目录 ``<基准目录>\\近期数据``（建不出来返回 None → 功能静默降级）。

    ⛔ 只做"子目录"这一层：基准目录本身仍由 ``directory()`` 决定（自定义目录
    优先、默认跟日志同目录），语义一个字没改。
    """
    folder = directory()
    if not folder:
        return None
    try:
        sub = Path(folder) / SUB_DIR
        sub.mkdir(parents=True, exist_ok=True)
        return sub
    except (OSError, TypeError):
        return None


def set_directory(path: str) -> bool:
    """切换历史目录并持久化。返回 True = 成功；不可写 / 存不下 → False（一个字不改）。

    传空串 = 清掉自定义目录、回到默认（日志同目录）。切换时先把当前文件的尾巴
    落盘，再按**当前车号**在新目录里重新定位文件 —— 用户换目录之后接着收包，
    新数据就落在新目录下；旧目录里的老历史**原样留着**，不会被搬走或删掉。
    """
    text = str(path or "").strip()
    target: Path | None = Path(text) if text else None
    if target is not None:
        probe = target / f".write_test{os.getpid()}"
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            return False
    flush()                             # 旧目录里的待写行先落盘，别丢最后一秒
    try:
        settings = _settings()
        if target is None:
            settings.remove(DIR_KEY)
        else:
            settings.setValue(DIR_KEY, str(target))
        settings.sync()
    except Exception:                   # noqa: BLE001 - 写配置失败 = 切换失败
        return False
    load(_current_key)
    return True


def labels_of(folder: str | Path | None) -> list[tuple[str, str]]:
    """某个目录下的近期历史文件 → ``[(标签, 绝对路径)]``（**新的在前**）。

    新的布局是按文件夹分车号，所以标签就是文件名去掉 ``.log``
    （``E74-2026.log`` → ``E74-2026``、``未选择车号.log`` → ``未选择车号``）。

    ⛔ **两处都要扫**：``<folder>/近期数据/*.log`` 是新家；``<folder>/接收历史*.log``
    是还没搬家的老文件（`load()` 只搬**当前车号**那一本，别的车的要等切过去才搬）。
    只扫子目录的话，升级之后"接收历史"下拉里会突然只剩当前这台车 —— 用户会以为
    别的车数据丢了。老文件的标签剥掉 ``接收历史`` 前缀；同标签时新家优先。
    """
    if not folder:
        return []
    base = Path(folder)
    found: list[tuple[str, str, float]] = []
    seen: set[str] = set()

    def _scan(entries, legacy: bool) -> None:
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                stamp = entry.stat().st_mtime
                full = str(entry.resolve())
            except OSError:
                continue
            name = entry.name
            if legacy and name.startswith(LEGACY_PREFIX):
                name = name[len(LEGACY_PREFIX):].lstrip("-")
            label = label_of(name)
            if label in seen:            # 新家已经有一份同标签的 → 老的忽略
                continue
            seen.add(label)
            found.append((label, full, stamp))

    try:
        _scan(list((base / SUB_DIR).glob("*.log")), legacy=False)
    except OSError:
        pass
    try:
        _scan(list(base.glob(f"{LEGACY_PREFIX}*.log")), legacy=True)
    except OSError:
        pass
    found.sort(key=lambda item: (-item[2], item[0]))
    return [(label, full) for label, full, _stamp in found]


def label_of(filename: str) -> str:
    """历史文件名 → 显示标签（``X.log`` → ``X``；通用文件 → ``默认``）。

    不再剥 ``接收历史`` 前缀 —— 新布局靠文件夹区分，文件名本身就是车号。
    """
    stem = Path(str(filename)).name
    if stem.endswith(".log"):
        stem = stem[: -len(".log")]
    stem = stem.strip(" .")
    return stem or "默认"


def available_labels() -> list[tuple[str, str]]:
    """当前历史目录里可选的历史文件（新的在前）。"""
    return labels_of(directory())


def path_for_label(label: str) -> str | None:
    """标签 → 该历史文件的绝对路径；找不到返回 None。"""
    want = str(label or "").strip() or "默认"
    for name, full in available_labels():
        if name == want:
            return full
    return None


def path() -> str | None:
    return str(_path) if _path else None


def _safe_key(key: str | None) -> str:
    """车辆名 → 文件名安全段（非法字符换成 _，限长 80）。"""
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(key or "").strip())
    return text.strip(" .")[:80]


def file_for(key: str | None) -> str:
    """车辆名 → 近期数据文件名；key 为空 = 未选车，落到 ``未选择车号.log``。

    ⛔ 文件名**不再带** ``接收历史-`` 前缀（用户 2026-11-21）：已经按
    ``近期数据`` 文件夹分了，前缀纯属重复。
    """
    safe = _safe_key(key)
    return f"{safe}.log" if safe else FILE_NAME


def _legacy_name_for(key: str | None) -> str:
    """老版本的文件名（只在迁移/兼容扫描时用，新文件不再这么命名）。"""
    safe = _safe_key(key)
    return f"{LEGACY_PREFIX}-{safe}.log" if safe else f"{LEGACY_PREFIX}.log"


def _claim_legacy(base: Path, key: str | None, target: Path) -> None:
    """把老位置的单文件搬进 ``近期数据``（目标已存在就不动，绝不覆盖新数据）。

    跨盘 / 文件被占用时 ``os.replace`` 会失败，退化成"读+写+删"。任何一步失败都
    静默 —— 老文件留着还能用（下次 load 再试），不能因为搬家把数据搞丢。
    """
    try:
        if target.exists():
            return
        legacy = base / _legacy_name_for(key)
        if not legacy.is_file():
            return
    except OSError:
        return
    try:
        os.replace(legacy, target)
    except OSError:
        try:
            target.write_bytes(legacy.read_bytes())
            legacy.unlink()
        except OSError:
            return
    import app_log
    app_log.info(f"接收历史迁移：{legacy.name} → {SUB_DIR}/{target.name}")


def switch_target(key: str | None) -> str | None:
    """把历史记录**切到某台车**（key=None = 未选车，用通用文件）。

    不同电池箱的历史数据**分开放**（用户 2026-10-23 要求，2026-11-21 升级成
    「按文件夹 + 车号」）：每台车一个 ``近期数据/<车名>.log``，切车前先把当前
    文件的尾巴落盘，内存里换上新文件的最近 _max_lines 行。删车**不删文件**
    —— 已接收的数据不受影响。
    """
    flush()
    return load(key)


def load(key: str | None = None) -> str | None:
    """启动/切车时调用：定位文件、把已有尾巴读回内存。返回路径（可能没建成）。

    ``key`` = 当前车辆名（"E74-2026" 这种）或 None（未选车 → 未选择车号.log）。
    定位前先把老位置的 ``接收历史*.log`` 搬进子目录（只搬一次）。
    """
    global _path, _file_lines, _timer, _current_key
    _pending.clear()
    _lines.clear()
    _file_lines = 0
    _current_key = key
    folder = _data_dir()                 # 自定义目录优先，默认仍是"跟日志同目录"
    if folder is None:
        _path = None
        return None
    _path = folder / file_for(key)
    _claim_legacy(folder.parent, key, _path)
    try:
        if _path.exists():
            text = _path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines()[-_max_lines:]:
                _lines.append(line)
    except OSError:
        pass                    # 读不回来就当空档开始，文件照常续写
    _file_lines = len(_lines)
    if _timer is None:
        from PySide6.QtCore import QTimer
        _timer = QTimer()
        _timer.setInterval(FLUSH_MS)
        _timer.timeout.connect(flush)
        _timer.start()
    return str(_path)


def feed(display_text: str) -> None:
    """把接收区实际显示的一帧（可多行）记进历史。

    ⛔ **空行也要记**（2026-10-24 用户点名："怎么出现了该空行的地方没有空行…
    历史数据格式得和接收时一模一样"）。接收区里轮与轮之间那一行空行是
    ``_emit_block`` 用 ``f"\\n{stamp}"`` 造出来的**真实的一行**，以前这里
    ``if ln.strip()`` 把它滤掉了 —— 于是历史里每轮的时间戳都紧贴着上一轮的
    数据，回放出来跟实时看到的不是一个样子。

    存的就是 ``display`` 这个字符串原样拆行：它已经过 ``_emit_block`` 的
    align_indexes 排版、也已含时间戳行，观感与屏上一致。只做内存入队，
    O(帧行数) 且无 IO。
    """
    if not display_text:
        return
    got = display_text.split("\n")
    if not any(line.strip() for line in got):
        return                       # 整帧都是空行：不值得占位
    _lines.extend(got)
    _pending.extend(got)


def flush() -> None:
    """把积攒的行批量追加进文件；超阈值时整体重写收回到上限。

    ``_file_lines`` 被 ``set_max_lines`` 标脏（> 上限+COMPACT_EXTRA）时，
    即使本轮没有待写行也要走一次 compact —— 否则用户把保留行数调小之后，
    文件会一直停在旧的大行数上，跟接收区对不上（用户点名要求一致）。
    """
    global _file_lines
    if _path is None:
        # ⛔ 历史目录建不出来时 load() 会 early-return（_path 保持 None），但
        #    _emit_block 仍然每帧 feed()。_pending 是普通 list、**没有上限**，
        #    不清就会随接收时长一路涨（_lines 有 maxlen 兜住，_pending 没有）。
        _pending.clear()
        return
    dirty = _file_lines > _max_lines + COMPACT_EXTRA
    if not _pending:
        if dirty:
            _compact()
        return
    batch, _pending[:] = _pending[:], []
    try:
        with open(_path, "a", encoding="utf-8") as f:
            f.write("\n".join(batch) + "\n")
        _file_lines += len(batch)
    except OSError:
        return                  # 文件系统抖动静默降级，绝不影响接收
    if _file_lines > _max_lines + COMPACT_EXTRA:
        _compact()


def _compact() -> None:
    """整体重写：只留内存 deque 里的最近 _max_lines 行（临时文件 + 原子替换）。"""
    global _file_lines
    tmp = _path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
        os.replace(tmp, _path)
    except OSError:
        return                  # 重写失败就维持原文件，下次 flush 再试
    _file_lines = len(_lines)


def lines() -> tuple[str, ...]:
    """当前内存里的历史行（自测用）。"""
    return tuple(_lines)


def _timer_stop() -> None:
    """自测用：暂停定时刷盘，免得测试期间把临时文件写脏或撞上真实文件。"""
    if _timer is not None:
        _timer.stop()


def _resume_timer() -> None:
    """自测用：恢复定时刷盘。"""
    if _timer is not None:
        _timer.start()


def all_lines() -> tuple[str, ...]:
    """回放用：整个文件 + **未落盘的尾巴**，读不到文件才退回内存副本。

    文件可能比内存 deque 更长/更全（没 compact 时文件留着上限+COMPACT_EXTRA
    行，deque 只留上限行），所以以文件为底。但落盘是 1 秒批量 flush
    （``FLUSH_MS``），最近一次 flush 之后 ``feed()`` 进来的行只在 ``_pending``
    里、还没写进文件 —— 只读文件会**永远少掉最新 1 秒的帧**（用户 2026-11-21
    报"近期数据没有最新的"）。

    不变式：``_pending`` 恰好是"上次 flush 之后新喂的行"，是文件内容的**严格
    后缀**（flush 先清空 pending 再整批 append，写失败也是丢批不回填，不会和
    文件重复）。所以文件行 + pending 就是当下完整的本地留存。
    """
    if _path is not None:
        try:
            if _path.exists():
                text = _path.read_text(encoding="utf-8", errors="replace")
                rows = text.splitlines()
                if _pending:
                    rows.extend(_pending)
                return tuple(rows)
        except OSError:
            pass
    return tuple(_lines)
