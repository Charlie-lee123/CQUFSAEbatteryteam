# -*- coding: utf-8 -*-
r"""QA 独立证伪：标记「同轮去重」+「内容签名主锚 content_sig / 反向序号兜底 stamp_rev」。

只读驱动真实 MainWindow，不改产品源码、不写用户真实历史/数据库/配置
（全程隔离 + finally 还原 QSettings 全量快照）。

攻击点（不是 happy path）：
  1 同秒两轮（stamp 文本完全相同、内容不同）各打一次标 → 必须 2 条（sig 不同），
    第 2 次**不能**弹「已经标记过」。
  1k 同秒两轮**内容完全相同**（签名相同）→ 记录实际行为（去重键是 (stamp, sig)）。
  2 同一轮连按两次 Alt+M → 列表仍 1 条 + toast + 列表当前行指向该条 + 已有高亮不清。
  3 把 start_block 弄坏 → 定位**不该再依赖它**（改为内容签名主锚）。
  4a 只有 1 处同 stamp、标记无签名 + rev=1（候选耗尽）→ 不崩。
  4b 人为裁掉组内前一处（位移恰好一整轮行数，上一轮曾因快速路径误命中 R3）。
  4c 组中间被裁后对同一轮再打标 → 去重键是否还能认出「同一轮」。
  4d 同上，但用**产品自己的** setMaximumBlockCount 从头裁。
  4e 真实裁剪 + 位移**正好等于一整轮行数**（上一轮 FAIL 的那条）。
  5a 本 QA 自设：**连续两次**头裁。
  5b 本 QA 自设：头裁 **+ 顶部前插同 stamp 轮**（回放翻页方向）叠加。
  5c 本 QA 自设：三处同 stamp、裁到**只剩被标那一轮**（极端）。
  6a 本 QA 自设：标记**末轮**后同秒下一轮到达（签名是否会失配 → 退化到过期 rev？）。
  6b 本 QA 自设：标记后该轮**继续增长**（内容变了 → 签名失配，应仍落在原轮）。
  7 QSettings 全量快照前后相等（71 键）、custom/soc_source 不变。

运行（真平台，产物重定向到 run.log，⛔ 别 | head）：
  QT_QPA_PLATFORM=windows .venv\Scripts\python.exe devtools\_qa_markfix_dedupe_ordinal.py > _check\qa_markfix_run.log 2>&1
"""
from __future__ import annotations

import dataclasses
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

_CHECK = Path(_ROOT) / "_check"
_RESULTS: list[tuple[str, bool, str]] = []
_NOTES: list[str] = []
_ART: dict[str, object] = {}

#: 两轮共用的时间戳文本（用户最容易踩的"同一秒读到两轮"）
STAMP = "2026-11-24 12:00:00"
STAMP_LINE = f"[{STAMP}]"
TOKENS = ("TOKEN_R0", "TOKEN_R1", "TOKEN_R2", "TOKEN_R3", "TOKEN_R4",
          "TOKEN_PRE0", "TOKEN_PRE1")


# ---------------------------------------------------------------- 记账
def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""),
          flush=True)
    return bool(ok)


def note(text: str) -> None:
    _NOTES.append(text)
    print(f"NOTE  {text}", flush=True)


def art(key: str, value) -> None:
    _ART[key] = value


def settle(app, ms: int = 260) -> None:
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000.0 < ms:
        app.processEvents()
        time.sleep(0.006)


# ---------------------------------------------------------------- 读数
def doc_of(win):
    return win.txt_rx.document()


def stamp_blocks(win, stamp_line: str = STAMP_LINE) -> list[int]:
    """文档里文本**完全相同**的块号（同 stamp 的所有"处"）。"""
    d = doc_of(win)
    return [b for b in range(d.blockCount())
            if d.findBlockByNumber(b).text() == stamp_line]


def round_text(win, start: int) -> str:
    span = win._round_span(start)
    if span is None:
        return ""
    d = doc_of(win)
    return "\n".join(d.findBlockByNumber(i).text() for i in range(span[0], span[1]))


def round_sig(win, start: int) -> str:
    span = win._round_span(start)
    return "" if span is None else win._round_content_sig(span)


def stamp_block_with_token(win, token: str) -> int:
    """某个 token 所在那一轮的**时间戳块号**（块号会因裁剪/前插漂移，别写死）。"""
    for b in stamp_blocks(win):
        if token in round_text(win, b):
            return b
    return -1


def hl_probe(win) -> dict:
    """读**整轮高亮**（_mark_selection）落在哪一轮。

    ⛔ 不能用 textCursor()：_jump_to_mark 只动滚动条 + ExtraSelection，**不**移动
    文本光标，读 textCursor 拿到的是旧位置（本探针上一版踩过）。高亮游标才是真落点。
    """
    sel = win._mark_selection
    d = doc_of(win)
    if sel is None:
        return {"block": -1, "token": "", "lines": 0}
    block = d.findBlock(sel.cursor.selectionStart()).blockNumber()
    span = win._round_span(block)
    if span is None:
        return {"block": block, "token": "", "lines": 0}
    text = "\n".join(d.findBlockByNumber(i).text() for i in range(span[0], span[1]))
    token = next((t for t in TOKENS if t in text), "")
    return {"block": block, "token": token, "lines": span[1] - span[0]}


def hl_signature(win):
    sel = win._mark_selection
    if sel is None:
        return None
    return (sel.cursor.selectionStart(), sel.cursor.selectionEnd())


def list_texts(win) -> list[str]:
    return [win._mark_list.item(i).text() for i in range(win._mark_list.count())]


def revs(win) -> list[int]:
    return [m.stamp_rev for m in win._marks.marks]


def sigs(win) -> list[str]:
    return [m.content_sig for m in win._marks.marks]


# ---------------------------------------------------------------- 驱动
def feed_round(win, tag: str, base: int = 1, filler: int = 24,
               stamp_line: str = STAMP_LINE) -> None:
    """灌一轮：**自带时间戳文本**（chk_timestamp 关掉时屏上就是这一行）。

    filler 让每轮足够高，才能把任意一轮滚到视口中点（_viewed_block_number 用它判轮）。
    """
    lines = [stamp_line, "Voltage1:",
             f"{base:02d}.3800 {base + 1:02d}.3820 {base + 2:02d}.3900",
             "Temperature1:", "1.25 2.25 3.25", "BMS OK", tag]
    lines += [f"filler {i:02d} {tag}" for i in range(filler)]
    win._emit_block("\n".join(lines))


def prepend_lines(win, app, count: int = 12) -> None:
    """在文档顶部前插**纯垫料行**（不含时间戳 → 不构成新的一轮）。"""
    text = "\n".join(f"pad {i:02d}" for i in range(count))
    cursor = QTextCursor(doc_of(win))
    cursor.setPosition(0)
    cursor.insertText(text + "\n")
    app.processEvents()


def prepend_round(win, app, tag: str, filler: int = 6) -> None:
    """在文档**顶部**前插一轮（等价回放向上翻页 `_insert_history_page` 的插入方向）。"""
    text = "\n".join([STAMP_LINE, "Voltage1:", "01.3800", "Temperature1:", "1.25",
                      "BMS OK", tag]
                     + [f"pre {i:02d} {tag}" for i in range(filler)])
    cursor = QTextCursor(doc_of(win))
    cursor.setPosition(0)
    cursor.insertText(text + "\n")
    app.processEvents()


def center_on_block(win, app, block_no: int) -> tuple[bool, int]:
    """把 ``block_no`` 滚到视口垂直中点，返回 (视口中点是否已落在该轮, 滚动条值)。

    _viewed_block_number 按视口中点判轮，所以必须真滚到位再打标，不能只 setCursor。
    """
    row = win._single_line_height() or 18.0
    vp_lines = max(1, int(win.txt_rx.viewport().height() / row))
    bar = win.txt_rx.verticalScrollBar()
    target = max(0, win._visual_line_index(block_no, row) - vp_lines // 2)
    for delta in (0, -1, 1, -2, 2, -3, 3, -5, 5, -8, 8, -13, 13, -21, 21):
        bar.setValue(max(0, target + delta))
        app.processEvents()
        span = win._round_span(win._viewed_block_number())
        if span is not None and span[0] == block_no:
            return True, bar.value()
    return False, bar.value()


def delete_blocks_before(win, app, block_no: int) -> int:
    """删掉块 ``0..block_no-1``（人为模拟"更早那一处不在文档里"）。"""
    d = doc_of(win)
    before = d.blockCount()
    cursor = QTextCursor(d)
    cursor.setPosition(0)
    cursor.setPosition(d.findBlockByNumber(block_no).position(),
                       QTextCursor.MoveMode.KeepAnchor)
    cursor.removeSelectedText()
    app.processEvents()
    return before - d.blockCount()


def force_crop(win, app, drop: int) -> tuple[int, int]:
    """用**产品自己的机制**从头裁掉约 ``drop`` 块（setMaximumBlockCount + 一次插入）。"""
    d = doc_of(win)
    before = d.blockCount()
    win.txt_rx.setMaximumBlockCount(max(8, before - drop))
    win.txt_rx.appendPlainText("force-crop")           # 下一次插入触发从头裁剪
    app.processEvents()
    return before, d.blockCount()


# ---------------------------------------------------------------- 主流程
def main() -> int:                                     # noqa: C901 - 探针
    app = QApplication.instance() or QApplication(sys.argv)
    from ui_theme import ui_font
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))

    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}
    soc_before = qs.value("custom/soc_source")
    art("qsettings_keys", len(snap))
    art("soc_source_before", soc_before)

    hist_dir = Path(tempfile.mkdtemp(prefix="qa_mkfix_hist_", dir=_CHECK))
    db_dir = Path(tempfile.mkdtemp(prefix="qa_mkfix_db_", dir=_CHECK))
    real_resolver = None
    toasts: list[str] = []
    win = None

    try:
        import rx_history
        import rx_store
        real_resolver = rx_history._resolve_dir
        real_db = rx_store.db_path()
        rx_history._resolve_dir = lambda: hist_dir
        rx_store.set_db_path(str(db_dir / "sessions.db"))

        import main_window
        win = main_window.MainWindow()
        win.resize(1200, 800)
        win.show()
        win.raise_()
        win.activateWindow()
        settle(app, 400)
        # 屏上时间戳关掉：探针要自己指定 stamp 文本（同秒两轮 = 文本完全相同）。
        win.chk_timestamp.setChecked(False)
        win._marks.clear()
        win._refresh_mark_list()
        app.processEvents()

        _real_toast = win._toast
        win._toast = lambda message, ms=3000: (toasts.append(message),
                                               _real_toast(message, ms))[1]

        # ---- 0 静态核对：字段与定位路径（防改名残留 / 快速路径复活）----
        _src = {p: (Path(_ROOT) / p).read_text(encoding="utf-8")
                for p in ("main_window.py", "marks_model.py", "selftest.py")}
        check("0a 产品源码里已无 stamp_ordinal 残留",
              not any("stamp_ordinal" in t for t in _src.values()),
              str({k: v.count("stamp_ordinal") for k, v in _src.items()}))
        check("0b content_sig 在 marks_model / main_window 都用上了",
              "content_sig" in _src["marks_model.py"]
              and "content_sig" in _src["main_window.py"])
        check("0c 定位不再读 start_block（快速路径已删）",
              "findBlockByNumber(mark.start_block)" not in _src["main_window.py"])
        check("0d 判重键仍是 (stamp, content_sig)",
              "mark.stamp == stamp and mark.content_sig == content_sig"
              in _src["marks_model.py"])

        # ============ 1 同秒两轮不能被误判成重复 ============
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1)
        feed_round(win, "TOKEN_R2", base=4)
        app.processEvents()
        st = stamp_blocks(win)
        art("t1_stamp_blocks", st)
        check("1a 文档里确有 2 处文本完全相同的时间戳（前提）", len(st) == 2, str(st))
        ok_a, _ = center_on_block(win, app, st[0])
        check("1b 视口中点已落到第 1 轮（前提）", ok_a,
              f"block={st[0]} viewed={win._viewed_block_number()}")
        toasts.clear()
        win._mark_current()
        app.processEvents()
        check("1c 第 1 轮打标 → 1 条、rev=1、有内容签名",
              len(win._marks.marks) == 1 and revs(win) == [1]
              and bool(win._marks.marks[0].content_sig),
              f"n={len(win._marks.marks)} rev={revs(win)} sig={sigs(win)}")
        check("1d 第 1 轮打标没有误报「已经标记过」",
              not any("已经标记过" in t for t in toasts), str(toasts))
        m0 = win._marks.marks[0]

        ok_b, _ = center_on_block(win, app, st[1])
        check("1e 视口中点已落到第 2 轮（前提）", ok_b,
              f"block={st[1]} viewed={win._viewed_block_number()}")
        toasts.clear()
        win._mark_current()
        app.processEvents()
        n_after2 = len(win._marks.marks)
        m1 = win._marks.marks[-1] if n_after2 > 1 else None
        check("1f 第 2 轮（同 stamp、内容不同）打标 → 共 2 条",
              n_after2 == 2 and win._mark_list.count() == 2,
              f"marks={n_after2} list={list_texts(win)}")
        check("1g 第 2 条 rev=0、start_block 指向第 2 处、签名与第 1 条不同",
              m1 is not None and m1.stamp_rev == 0 and m1.start_block == st[1]
              and m1.content_sig and m1.content_sig != m0.content_sig,
              f"rev={getattr(m1, 'stamp_rev', None)} "
              f"blk={getattr(m1, 'start_block', None)} 期望={st[1]} "
              f"sig0={m0.content_sig} sig1={getattr(m1, 'content_sig', None)}")
        check("1h 第 2 次打标**没有**弹「这一轮已经标记过」（同秒两轮未被误判成重复）",
              not any("已经标记过" in t for t in toasts), str(toasts))
        check("1i 两条 stamp 文本完全相同（证明只能靠签名/rev 区分）",
              m0.stamp == m1.stamp == STAMP_LINE, f"{m0.stamp!r} / {m1.stamp!r}")

        # ============ 1k 同秒两轮「内容完全相同」→ 记录实际行为 ============
        win._clear_receive()
        feed_round(win, "SAME_CONTENT", base=1, filler=6)
        feed_round(win, "SAME_CONTENT", base=1, filler=6)   # 逐字节相同（静态数据）
        app.processEvents()
        stk = stamp_blocks(win)
        art("t1k_stamp_blocks", stk)
        center_on_block(win, app, stk[0])
        win._mark_current()
        app.processEvents()
        n1k = len(win._marks.marks)
        center_on_block(win, app, stk[1])
        toasts.clear()
        win._mark_current()
        app.processEvents()
        n1k2 = len(win._marks.marks)
        note("同秒两轮**内容逐字节相同**（签名相同）：第 1 处打标后对第 2 处再打 → "
             f"{n1k} 条 → {n1k2} 条，toast={toasts}。"
             "去重键是 (stamp, content_sig)，这是代码注释里写明的有意取舍"
             "（宁可少一条也不出重复）；用户侧表现=第 2 处标不上、跳回第 1 处。")
        art("t1k_counts", (n1k, n1k2))

        # ============ 2 同一轮连按两次 ============
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1)
        feed_round(win, "TOKEN_R2", base=4)
        app.processEvents()
        st2a = stamp_blocks(win)
        center_on_block(win, app, st2a[1])
        win._mark_current()
        app.processEvents()
        m_dup = win._marks.marks[-1]
        win._jump_to_mark(m_dup.key)                   # 先建立一条已知整轮高亮
        app.processEvents()
        centered, _ = center_on_block(win, app, m_dup.start_block)
        sig_before = hl_signature(win)
        n_before = len(win._marks.marks)
        toasts.clear()
        win._mark_current()                            # 同一轮再按一次（Alt+M 等价）
        app.processEvents()
        check("2a 同一轮再按一次 → 列表仍 1 条、不新增",
              len(win._marks.marks) == n_before == 1 and win._mark_list.count() == 1,
              f"marks={len(win._marks.marks)} list={list_texts(win)}")
        check("2b 弹出「这一轮已经标记过」", any("已经标记过" in t for t in toasts),
              str(toasts))
        check("2c 列表当前行指向已有那条（index=0）",
              win._mark_list.currentRow() == 0, f"currentRow={win._mark_list.currentRow()}")
        check("2d 已有整轮高亮没有被清掉",
              hl_signature(win) is not None and hl_signature(win) == sig_before,
              f"{sig_before} -> {hl_signature(win)}")
        check("2e 高亮仍落在第 2 轮", hl_probe(win)["token"] == "TOKEN_R2",
              str(hl_probe(win)))
        art("t2_centered", centered)

        # ============ 3 定位不再依赖 start_block ============
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1)
        feed_round(win, "TOKEN_R2", base=4)
        app.processEvents()
        st3a = stamp_blocks(win)
        center_on_block(win, app, st3a[0])
        win._mark_current()
        app.processEvents()
        center_on_block(win, app, st3a[1])
        win._mark_current()
        app.processEvents()
        check("3-0 两处同 stamp 各一条（rev 1/0、签名不同）",
              len(win._marks.marks) == 2 and revs(win) == [1, 0]
              and sigs(win)[0] != sigs(win)[1],
              f"rev={revs(win)} sig={sigs(win)}")
        win._marks.marks[0] = dataclasses.replace(win._marks.marks[0], start_block=99999)
        win._marks.marks[1] = dataclasses.replace(win._marks.marks[1], start_block=88888)
        win._jump_to_mark(win._marks.marks[0].key)
        app.processEvents()
        g0 = hl_probe(win)
        check("3a start_block 弄坏 + 第 1 条 → 落到第 1 处（签名主锚）",
              g0["token"] == "TOKEN_R1", str(g0))
        win._jump_to_mark(win._marks.marks[1].key)
        app.processEvents()
        g1 = hl_probe(win)
        check("3b start_block 弄坏 + 第 2 条 → 落到**第 2 处**", g1["token"] == "TOKEN_R2",
              str(g1))

        # ============ 4a 候选耗尽（只剩 1 处、无签名 + rev=1）============
        win._clear_receive()
        feed_round(win, "TOKEN_R2", base=4)
        app.processEvents()
        solo = stamp_blocks(win)
        check("4a-0 文档里只剩 1 处同 stamp（前提）", len(solo) == 1, str(solo))
        win._marks.clear()
        win._marks.add_unique(solo[0], STAMP_LINE, "TOKEN_R2", 1)   # content_sig=""
        win._marks.marks[0] = dataclasses.replace(win._marks.marks[0], start_block=99999)
        win._refresh_mark_list()
        toasts.clear()
        win._jump_to_mark(win._marks.marks[0].key)
        app.processEvents()
        g4 = hl_probe(win)
        check("4a 只有 1 处 + 无签名 + rev=1 → 不崩，落到唯一那处（或明确提示）",
              g4["token"] == "TOKEN_R2" or any("尚未加载" in t or "已被裁剪" in t
                                             for t in toasts),
              f"{g4} toasts={toasts}")

        # ============ 4b 人为裁掉组内前一处（位移恰好一整轮行数）============
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1)
        feed_round(win, "TOKEN_R2", base=4)
        feed_round(win, "TOKEN_R3", base=7)
        app.processEvents()
        st3 = stamp_blocks(win)
        check("4b-0 三处同 stamp（前提）", len(st3) == 3, str(st3))
        centered, _ = center_on_block(win, app, st3[1])
        win._mark_current()
        app.processEvents()
        mark_r2 = win._marks.marks[-1]
        check("4b-1 打的是第 2 处（rev=1）",
              mark_r2.stamp_rev == 1 and centered,
              f"rev={mark_r2.stamp_rev} centered={centered} list={list_texts(win)}")
        removed = delete_blocks_before(win, app, st3[1])
        st_left = stamp_blocks(win)
        check("4b-2 裁掉第 1 处后只剩 2 处（前提）", len(st_left) == 2,
              f"removed={removed} left={st_left}")
        win._jump_to_mark(mark_r2.key)
        app.processEvents()
        g4b = hl_probe(win)
        check("4b-3 人为裁剪（位移恰好一整轮）后跳回 → 应落到 TOKEN_R2（上一轮 FAIL 点）",
              g4b["token"] == "TOKEN_R2",
              f"got={g4b['token'] or g4b} 期望=TOKEN_R2 blocks={st_left}")
        # 4b-4 把签名清空（模拟"该轮内容已不在"）→ 只剩 rev 兜底，验证兜底方向
        win._marks.marks[-1] = dataclasses.replace(mark_r2, content_sig="")
        win._jump_to_mark(win._marks.marks[-1].key)
        app.processEvents()
        g4b2 = hl_probe(win)
        check("4b-4 清空签名后（只走 rev 兜底）→ 仍落到 TOKEN_R2",
              g4b2["token"] == "TOKEN_R2", f"got={g4b2['token'] or g4b2}")

        # ============ 4c 裁剪后对同一轮再打标（去重键是否还认得出）============
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1)
        feed_round(win, "TOKEN_R2", base=4)
        app.processEvents()
        st2 = stamp_blocks(win)
        centered, _ = center_on_block(win, app, st2[1])
        toasts.clear()
        win._mark_current()
        app.processEvents()
        m_before = win._marks.marks[-1]
        check("4c-0 第 2 处先打一次（rev=0，最后一处）",
              len(win._marks.marks) == 1 and m_before.stamp_rev == 0 and centered,
              f"rev={getattr(m_before, 'stamp_rev', None)}")
        delete_blocks_before(win, app, st2[1])
        left = stamp_blocks(win)
        centered2, _ = center_on_block(win, app, left[0])
        toasts.clear()
        win._mark_current()
        app.processEvents()
        check("4c-1 裁掉前一处后**对同一轮**再打标 → 不应新增重复条目",
              len(win._marks.marks) == 1,
              f"marks={len(win._marks.marks)} rev={revs(win)} list={list_texts(win)} "
              f"centered={centered2} toasts={toasts}")

        # ============ 4d 用**产品自己的机制**复现（setMaximumBlockCount 从头裁）============
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1)
        feed_round(win, "TOKEN_R2", base=4)
        feed_round(win, "TOKEN_R3", base=7)
        app.processEvents()
        st4 = stamp_blocks(win)
        centered, _ = center_on_block(win, app, st4[1])
        win._mark_current()
        app.processEvents()
        mark_4 = win._marks.marks[-1]
        total_blocks = doc_of(win).blockCount()
        # 让裁剪边界落在「组中间」：只留 R2 时间戳上方 2 块 → R1 的时间戳被裁掉
        new_max = total_blocks - st4[1] + 2
        win.txt_rx.setMaximumBlockCount(new_max)
        win.txt_rx.appendPlainText("force-crop")       # 下一次插入触发从头裁剪
        app.processEvents()
        st4_after = stamp_blocks(win)
        art("t4d_blocks_before", (st4, total_blocks))
        art("t4d_blocks_after", (st4_after, doc_of(win).blockCount()))
        check("4d-0 真实裁剪后同 stamp 只剩 2 处（前提）",
              len(st4_after) == 2 and st4[1] > 0 and mark_4.stamp_rev == 1,
              f"before={st4} max={new_max} after={st4_after} rev={mark_4.stamp_rev}")
        win._jump_to_mark(mark_4.key)
        app.processEvents()
        g4d = hl_probe(win)
        check("4d-1 真实裁剪后跳回「第 2 处」的标记 → 仍应落到 TOKEN_R2",
              g4d["token"] == "TOKEN_R2",
              f"got={g4d['token'] or g4d} 期望=TOKEN_R2")
        centered, _ = center_on_block(win, app, stamp_block_with_token(win, "TOKEN_R2"))
        n_before_4e = len(win._marks.marks)
        toasts.clear()
        win._mark_current()                            # 对同一轮再按一次
        app.processEvents()
        check("4d-2 真实裁剪后对同一轮再按标记 → 不应新增重复条目（去重仍生效）",
              len(win._marks.marks) == n_before_4e,
              f"{n_before_4e} -> {len(win._marks.marks)} rev={revs(win)} "
              f"centered={centered} toasts={toasts}")
        win.txt_rx.setMaximumBlockCount(win._rx_blocks)

        # ============ 4e 真实裁剪 + 位移**正好等于一整轮行数**（上一轮 FAIL 点）============
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1)
        feed_round(win, "TOKEN_R2", base=4)
        feed_round(win, "TOKEN_R3", base=7)
        app.processEvents()
        st8 = stamp_blocks(win)
        round_len = st8[1] - st8[0]              # 每轮固定行数（真实数据里也基本固定）
        center_on_block(win, app, st8[1])
        win._mark_current()
        app.processEvents()
        mark_8 = win._marks.marks[-1]
        force_crop(win, app, round_len - 1)      # 实际移除 round_len 块 = 整整一轮
        hits8 = stamp_blocks(win)
        art("t4e_round_len", round_len)
        art("t4e_hits", hits8)
        check("4e-0 真实裁剪（位移=一轮行数）后 hits 前移到 [0, round_len]（前提）",
              hits8 == [0, round_len] and mark_8.stamp_rev == 1,
              f"round_len={round_len} hits={hits8} rev={mark_8.stamp_rev}")
        win._jump_to_mark(mark_8.key)
        app.processEvents()
        g4e = hl_probe(win)
        check("4e-1 真实裁剪（位移=一整轮）后跳回 → 应落到 TOKEN_R2（上一轮 FAIL 点）",
              g4e["token"] == "TOKEN_R2", f"got={g4e['token'] or g4e} 期望=TOKEN_R2")
        win._marks.marks[-1] = dataclasses.replace(mark_8, content_sig="")
        win._jump_to_mark(win._marks.marks[-1].key)
        app.processEvents()
        g4e2 = hl_probe(win)
        check("4e-2 同场景清空签名（只走 rev 兜底）→ 仍落到 TOKEN_R2",
              g4e2["token"] == "TOKEN_R2", f"got={g4e2['token'] or g4e2}")
        win.txt_rx.setMaximumBlockCount(win._rx_blocks)

        # ============ 5a 本 QA 自设：**连续两次**头裁 ============
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1)
        feed_round(win, "TOKEN_R2", base=4)
        feed_round(win, "TOKEN_R3", base=7)
        prepend_lines(win, app, 12)                   # 组上方垫 12 行（不含时间戳 → 不是轮）
        app.processEvents()
        r2_blk = stamp_block_with_token(win, "TOKEN_R2")
        centered, _ = center_on_block(win, app, r2_blk)
        win._mark_current()
        app.processEvents()
        mark_5 = win._marks.marks[-1]
        check("5a-0 三处同 stamp 且在 R2 上打标（rev=1）",
              len(stamp_blocks(win)) == 3 and mark_5.stamp_rev == 1 and centered,
              f"blocks={stamp_blocks(win)} rev={mark_5.stamp_rev} centered={centered}")
        c1 = force_crop(win, app, 4)                  # 头裁 #1：只吃掉垫料
        st_after1 = stamp_blocks(win)
        check("5a-1 头裁 #1 后三处同 stamp 都还在（前提）", len(st_after1) == 3,
              f"blocks {c1[0]}->{c1[1]} hits={st_after1}")
        win._jump_to_mark(mark_5.key)
        app.processEvents()
        check("5a-2 头裁 #1 后跳回 → 仍落到 TOKEN_R2",
              hl_probe(win)["token"] == "TOKEN_R2", str(hl_probe(win)))
        until_r1 = stamp_block_with_token(win, "TOKEN_R1")
        c2 = force_crop(win, app, until_r1 + 1)       # 头裁 #2：把组内前一处吃掉
        st_after2 = stamp_blocks(win)
        check("5a-3 头裁 #2 后只剩 2 处同 stamp（R1 已被裁）",
              len(st_after2) == 2, f"blocks {c2[0]}->{c2[1]} hits={st_after2}")
        win._jump_to_mark(mark_5.key)
        app.processEvents()
        g5a = hl_probe(win)
        check("5a-4 连续两次头裁后跳回 → 仍落到 TOKEN_R2（签名主锚）",
              g5a["token"] == "TOKEN_R2", f"got={g5a['token'] or g5a}")
        centered, _ = center_on_block(win, app, stamp_block_with_token(win, "TOKEN_R2"))
        n_before_5a = len(win._marks.marks)
        toasts.clear()
        win._mark_current()
        app.processEvents()
        check("5a-5 连续两次头裁后再打标 → 不新增（判重键未被裁乱）",
              len(win._marks.marks) == n_before_5a,
              f"{n_before_5a} -> {len(win._marks.marks)} rev={revs(win)} "
              f"centered={centered} toasts={toasts}")
        win.txt_rx.setMaximumBlockCount(win._rx_blocks)

        # ============ 5b 本 QA 自设：头裁 + 顶部前插同 stamp 轮（回放翻页）叠加 ============
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1)
        feed_round(win, "TOKEN_R2", base=4)
        feed_round(win, "TOKEN_R3", base=7)
        app.processEvents()
        st6 = stamp_blocks(win)
        centered, _ = center_on_block(win, app, st6[1])
        win._mark_current()
        app.processEvents()
        mark_6 = win._marks.marks[-1]
        check("5b-0 三处同 stamp，标第 2 处（rev=1）",
              mark_6.stamp_rev == 1 and centered, f"rev={mark_6.stamp_rev}")
        force_crop(win, app, stamp_block_with_token(win, "TOKEN_R1") + 1)   # 头裁掉 R1
        win.txt_rx.setMaximumBlockCount(win._rx_blocks)   # 先放开上限，否则前插会被立刻裁掉
        prepend_round(win, app, "TOKEN_PRE0", filler=8)   # 顶部前插一条同 stamp 的"更早页"
        st6_after = stamp_blocks(win)
        art("t5b_hits_after_mixed", st6_after)
        check("5b-1 头裁 + 前插后仍是 3 处同 stamp（前插的那处在最前）",
              len(st6_after) == 3 and "TOKEN_PRE0" in round_text(win, st6_after[0]),
              f"hits={st6_after} first_round={round_text(win, st6_after[0])[:40]!r}")
        win._jump_to_mark(mark_6.key)
        app.processEvents()
        g5b = hl_probe(win)
        check("5b-2 头裁 + 顶部前插同 stamp 轮后跳回 → 仍落到 TOKEN_R2（不是前插那处）",
              g5b["token"] == "TOKEN_R2", f"got={g5b['token'] or g5b}")
        n_before_5b = len(win._marks.marks)
        centered, _ = center_on_block(win, app, stamp_block_with_token(win, "TOKEN_R2"))
        toasts.clear()
        win._mark_current()
        app.processEvents()
        check("5b-3 混合漂移后再打标 → 不新增（判重键抗顶部前插）",
              len(win._marks.marks) == n_before_5b,
              f"{n_before_5b} -> {len(win._marks.marks)} rev={revs(win)} "
              f"centered={centered} toasts={toasts}")
        win.txt_rx.setMaximumBlockCount(win._rx_blocks)

        # ============ 5c 本 QA 自设：三处同 stamp，裁到**只剩被标那一轮** ============
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1)
        feed_round(win, "TOKEN_R2", base=4)
        feed_round(win, "TOKEN_R3", base=7)
        app.processEvents()
        st9 = stamp_blocks(win)
        center_on_block(win, app, st9[1])
        win._mark_current()
        app.processEvents()
        mark_9 = win._marks.marks[-1]
        # 头裁掉 R1（整轮），再把 R3 从尾部删掉 → 文档里只剩 R2（被标那轮）
        force_crop(win, app, stamp_block_with_token(win, "TOKEN_R1") + 1)
        win.txt_rx.setMaximumBlockCount(win._rx_blocks)
        tail_start = stamp_block_with_token(win, "TOKEN_R3")
        if tail_start > 0:
            d = doc_of(win)
            cur = QTextCursor(d)
            cur.setPosition(d.findBlockByNumber(tail_start).position())
            cur.setPosition(d.characterCount() - 1, QTextCursor.MoveMode.KeepAnchor)
            cur.removeSelectedText()
            app.processEvents()
        st9_after = stamp_blocks(win)
        art("t5c_hits_after", st9_after)
        check("5c-0 裁到只剩被标那一轮（前提：只剩 1 处同 stamp）",
              len(st9_after) == 1, f"hits={st9_after}")
        win._jump_to_mark(mark_9.key)
        app.processEvents()
        g5c = hl_probe(win)
        check("5c-1 只剩被标那轮时跳回 → 落到那一轮（签名命中，不误跳/不误报）",
              g5c["token"] == "TOKEN_R2",
              f"got={g5c['token'] or g5c} toasts={toasts[-2:]}")

        # ============ 6a 本 QA 自设：标记**末轮**后同秒下一轮到达 ============
        # 场景：用户把正在看的最新一轮标了，紧接着设备送来**同秒**的下一轮。
        # 关注：标记那轮的签名是否仍然算得出来（末轮的 span 右端 = 文档末尾），
        #      以及一旦签名失配，rev=0 的兜底会落到"新的最后一处"= 跳错一轮。
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1, filler=6)
        app.processEvents()
        center_on_block(win, app, stamp_block_with_token(win, "TOKEN_R1"))
        win._mark_current()
        app.processEvents()
        mark_10 = win._marks.marks[-1]
        sig_at_mark = mark_10.content_sig
        text_at_mark = round_text(win, stamp_block_with_token(win, "TOKEN_R1"))
        feed_round(win, "TOKEN_R2", base=4, filler=6)      # 同 stamp 的下一轮到达
        app.processEvents()
        hits10 = stamp_blocks(win)
        sig_now = round_sig(win, stamp_block_with_token(win, "TOKEN_R1"))
        text_now = round_text(win, stamp_block_with_token(win, "TOKEN_R1"))
        art("t6a_sig_at_mark", sig_at_mark)
        art("t6a_sig_now", sig_now)
        art("t6a_text_tail_at_mark", repr(text_at_mark[-32:]))
        art("t6a_text_tail_now", repr(text_now[-32:]))
        art("t6a_text_len", (len(text_at_mark), len(text_now)))
        # 验证"一行修复"是否对症：签名前把首尾空行 strip 掉，两次是否就一致
        import hashlib as _hl

        def _sig_strip(s: str) -> str:
            return _hl.blake2b(s.strip("\n").encode("utf-8", "replace"),
                               digest_size=8).hexdigest()

        art("t6a_fix_strip_equal", _sig_strip(text_at_mark) == _sig_strip(text_now))
        note("末轮签名失配的根因：下一轮到达时 `gap_lines` 会在新时间戳前插入一行空行，"
             "而**末轮的 span 右端 = 文档末尾** → 这行空行落进末轮的签名范围。"
             f"打标时 text 尾={repr(text_at_mark[-18:])}，之后={repr(text_now[-18:])}；"
             "对 joined 做 `.strip(chr(10))` 后两次签名是否相同="
             f"{_sig_strip(text_at_mark) == _sig_strip(text_now)}"
             "（即：一行 strip 就能修掉 6a/6c，且不影响非末轮）。")
        toasts.clear()
        win._jump_to_mark(mark_10.key)
        app.processEvents()
        g6a = hl_probe(win)
        check("6a 标记末轮后同秒下一轮到达 → 跳回仍落到原轮 TOKEN_R1（签名一致）",
              g6a["token"] == "TOKEN_R1" and sig_at_mark == sig_now,
              f"got={g6a['token'] or g6a} sig_match={sig_at_mark == sig_now} "
              f"rev={mark_10.stamp_rev} hits={hits10} "
              f"tail@mark={repr(text_at_mark[-24:])} tail@now={repr(text_now[-24:])}")

        # ============ 6c 本 QA 自设：标记末轮 → 下一轮到达（**不同 stamp**）→ 再打标 ============
        # 只差"中间来了一轮"，没有同秒重复戳。判重键是 (stamp, content_sig)，
        # 若末轮的签名随"下一轮到达"而变，去重就会失效、出重复条目。
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1, filler=6)
        app.processEvents()
        r1b = stamp_block_with_token(win, "TOKEN_R1")
        center_on_block(win, app, r1b)
        win._mark_current()
        app.processEvents()
        mark_12 = win._marks.marks[-1]
        check("6c-0 末轮打一次 → 1 条（前提）",
              len(win._marks.marks) == 1 and bool(mark_12.content_sig),
              f"n={len(win._marks.marks)} sig={mark_12.content_sig}")
        feed_round(win, "TOKEN_R2", base=4, filler=6,
                   stamp_line="[2026-11-24 12:00:05]")     # 下一秒的下一轮（stamp 不同）
        app.processEvents()
        center_on_block(win, app, stamp_block_with_token(win, "TOKEN_R1"))
        toasts.clear()
        win._mark_current()                                # 对**同一轮**再打一次
        app.processEvents()
        check("6c-1 中间来了一轮后再对同一轮打标 → 不应新增重复条目",
              len(win._marks.marks) == 1
              and any("已经标记过" in t for t in toasts),
              f"marks={len(win._marks.marks)} sig={sigs(win)} list={list_texts(win)} "
              f"toasts={toasts}")

        # ============ 7a 本 QA 自设：标末轮 → **连续来 3 轮（其中 2 轮同秒）** → 跳回 ============
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1, filler=6)              # 此刻的末轮 → 打标
        app.processEvents()
        center_on_block(win, app, stamp_block_with_token(win, "TOKEN_R1"))
        win._mark_current()
        app.processEvents()
        mark_13 = win._marks.marks[-1]
        feed_round(win, "TOKEN_R2", base=4, filler=6)              # 同 stamp（同秒）
        feed_round(win, "TOKEN_R3", base=7, filler=6)              # 同 stamp（同秒）
        feed_round(win, "TOKEN_R4", base=10, filler=6,
                   stamp_line="[2026-11-24 12:00:05]")            # 另一秒
        app.processEvents()
        hits13 = stamp_blocks(win)
        art("t7a_hits", hits13)
        check("7a-0 前提：3 处同 stamp（R1/R2/R3）+ 1 处别的秒",
              len(hits13) == 3 and len(stamp_blocks(win, "[2026-11-24 12:00:05]")) == 1,
              f"hits={hits13}")
        win._jump_to_mark(mark_13.key)
        app.processEvents()
        g13 = hl_probe(win)
        check("7a-1 标末轮后连续来 3 轮（2 轮同秒）→ 跳回仍落 TOKEN_R1",
              g13["token"] == "TOKEN_R1", f"got={g13['token'] or g13}")
        center_on_block(win, app, stamp_block_with_token(win, "TOKEN_R1"))
        n13 = len(win._marks.marks)
        toasts.clear()
        win._mark_current()
        app.processEvents()
        check("7a-2 此时对同一轮再打标 → 不新增（判重键未被后续轮冲掉）",
              len(win._marks.marks) == n13,
              f"{n13} -> {len(win._marks.marks)} sig={sigs(win)} toasts={toasts}")

        # ============ 7b 本 QA 自设：标末轮 → 又来一轮（同秒）→ **头裁** → 跳回 ============
        # R0/R1/R2 同 stamp；标 R1（当时末轮，rev=0）→ R2 到达（rev=0 就过期了）
        # → 头裁掉 R0 → 此时 hits=[R1,R2]：rev=0 会指到 R2，只有签名能指回 R1。
        win._clear_receive()
        prepend_lines(win, app, 12)
        feed_round(win, "TOKEN_R0", base=1, filler=8)
        feed_round(win, "TOKEN_R1", base=4, filler=8)              # 此刻的末轮 → 打标
        app.processEvents()
        center_on_block(win, app, stamp_block_with_token(win, "TOKEN_R1"))
        win._mark_current()
        app.processEvents()
        mark_14 = win._marks.marks[-1]
        feed_round(win, "TOKEN_R2", base=7, filler=8)              # 同秒再来一轮
        app.processEvents()
        before14 = doc_of(win).blockCount()
        force_crop(win, app, stamp_block_with_token(win, "TOKEN_R0") + 1)  # 头裁掉垫料+R0轮首
        art("t7b_crop", (before14, doc_of(win).blockCount()))
        hits14 = stamp_blocks(win)
        art("t7b_hits", hits14)
        check("7b-0 前提：头裁后 R0 轮首已消失、仍剩 2 处同 stamp（R1/R2）",
              stamp_block_with_token(win, "TOKEN_R0") < 0 and len(hits14) == 2,
              f"blocks {before14}->{doc_of(win).blockCount()} hits={hits14} "
              f"rev={mark_14.stamp_rev}")
        win._jump_to_mark(mark_14.key)
        app.processEvents()
        g14 = hl_probe(win)
        check("7b-1 标末轮 → 同秒又一轮 → 头裁 → 跳回仍落 TOKEN_R1（只有签名能救）",
              g14["token"] == "TOKEN_R1",
              f"got={g14['token'] or g14}（rev=0 兜底会指到 R2，说明签名未命中）")
        win.txt_rx.setMaximumBlockCount(win._rx_blocks)
        center_on_block(win, app, stamp_block_with_token(win, "TOKEN_R1"))
        n14 = len(win._marks.marks)
        toasts.clear()
        win._mark_current()
        app.processEvents()
        check("7b-2 头裁后再打标 → 不新增", len(win._marks.marks) == n14,
              f"{n14} -> {len(win._marks.marks)} toasts={toasts}")

        # ============ 6b 本 QA 自设：标记后该轮**继续增长** ============
        win._clear_receive()
        feed_round(win, "TOKEN_R1", base=1, filler=6)
        app.processEvents()
        center_on_block(win, app, stamp_block_with_token(win, "TOKEN_R1"))
        win._mark_current()
        app.processEvents()
        mark_11 = win._marks.marks[-1]
        win._emit_block(" 03.3900 04.3900\nBMS OK\nTOKEN_R1_GROWN")   # 同一轮继续收
        app.processEvents()
        sig_after_growth = round_sig(win, stamp_block_with_token(win, "TOKEN_R1"))
        toasts.clear()
        win._jump_to_mark(mark_11.key)
        app.processEvents()
        g6b = hl_probe(win)
        note("标记后该轮继续增长：签名 "
             f"{'仍一致' if sig_after_growth == mark_11.content_sig else '已失配（内容变了）'}"
             f"，跳回落 {g6b['token'] or g6b}。")
        check("6b 标记后同一轮继续增长 → 跳回仍落在这一轮（签名失配时 rev 兜底）",
              g6b["token"] == "TOKEN_R1", f"got={g6b['token'] or g6b}")

        # ============ 收尾：清屏才清零（语义回归）============
        n_sem = len(win._marks.marks)
        win._clear_receive(clear_marks=False)
        app.processEvents()
        check("7-1 _clear_receive(clear_marks=False) 不清标记（进/出回放路径）",
              len(win._marks.marks) == n_sem and n_sem > 0,
              f"{n_sem} -> {len(win._marks.marks)}")
        win._clear_receive()
        app.processEvents()
        check("7-2 主动清空接收区才清零标记",
              len(win._marks.marks) == 0 and win._mark_list.count() == 0,
              f"marks={len(win._marks.marks)} list={win._mark_list.count()}")

    except Exception:                                 # noqa: BLE001 - 探针留证据
        print("EXCEPTION:\n" + traceback.format_exc(), flush=True)
        check("!! 探针异常中断（上面已有结论仍有效）", False,
              traceback.format_exc(limit=3).replace("\n", " | ")[:400])
    finally:
        if win is not None:
            try:
                win.close()
                win.deleteLater()
            except Exception:                          # noqa: BLE001
                pass
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        try:
            import rx_history
            import rx_store
            if real_resolver is not None:
                rx_history._resolve_dir = real_resolver
            rx_store.shutdown()
            rx_store.set_db_path(real_db)
            rx_store.reset_for_tests()
        except Exception:                              # noqa: BLE001
            pass
        for k in list(qs.allKeys()):
            if k not in snap:
                qs.remove(k)
        for k, v in snap.items():
            qs.setValue(k, v)
        qs.sync()
        shutil.rmtree(hist_dir, ignore_errors=True)
        shutil.rmtree(db_dir, ignore_errors=True)

    # ============ 8 QSettings 零污染 ============
    now = {k: qs.value(k) for k in qs.allKeys()}
    soc_after = qs.value("custom/soc_source")
    art("qsettings_keys_after", len(now))
    art("soc_source_after", soc_after)
    check("8a QSettings 全量快照前后完全一致（71 键）",
          now == snap and len(now) == len(snap),
          f"{len(snap)} 键 -> {len(now)} 键；差异="
          f"{sorted(set(now) ^ set(snap)) or [k for k in snap if now.get(k) != snap[k]]}")
    check("8b custom/soc_source 不变（read 仍是 read）",
          soc_before == soc_after == "read", f"{soc_before!r} -> {soc_after!r}")

    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    tail = f"\n合计 {passed}/{total} PASS  ->  IS_PASS: {'YES' if passed == total else 'NO'}"
    print(tail, flush=True)

    lines = [f"{'PASS' if ok else 'FAIL'}  {n}" + (f"   [{d}]" if d else "")
             for n, ok, d in _RESULTS]
    (_CHECK / "qa_markfix_report.txt").write_text(
        "\n".join(lines) + "\n\nNOTES\n" + "\n".join(_NOTES) + "\n" + tail + "\n",
        encoding="utf-8")
    md = ["# QA 独立验收：标记去重 + 双锚点（content_sig 主锚 / stamp_rev 兜底）",
          "", f"- 结果：**{passed}/{total} PASS**（IS_PASS={'YES' if passed == total else 'NO'}）",
          f"- QSettings：{len(snap)} 键 → {len(now)} 键；"
          f"soc_source {soc_before!r} → {soc_after!r}", "",
          "| # | 判据 | 结果 | 证据 |", "|---|---|---|---|"]
    for n, ok, d in _RESULTS:
        head, _, rest = n.partition(" ")
        md.append(f"| {head} | {rest} | {'PASS' if ok else '**FAIL**'} | "
                  f"{d.replace('|', '/')} |")
    if _NOTES:
        md += ["", "## NOTES"] + [f"- {t}" for t in _NOTES]
    md += ["", "## 关键读数", "```"] + [f"{k} = {v}" for k, v in _ART.items()] + ["```"]
    (_CHECK / "qa_markfix_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    app.quit()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
