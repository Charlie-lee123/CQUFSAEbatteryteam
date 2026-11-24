# -*- coding: utf-8 -*-
r"""QA 独立证伪：历史数据 / 近期数据也支持标记 = **全程只有一套 MarkStore**。

不改产品源码、不写用户真实历史 / 数据库 / 配置（全程隔离 + finally 还原）。
每条都是**行为级**读数（真实 MainWindow + 真平台 QT_QPA_PLATFORM=windows）。

运行：
  QT_QPA_PLATFORM=windows .venv\Scripts\python.exe devtools\_qa_markshared_flow.py
产物：
  _check/qa_markshared_run.log / qa_markshared_report.txt / qa_markshared_report.md
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import numpy as np
from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QImage, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import marks_model
import rx_history
import rx_store
from ui_highlight import stamp_span

_CHECK = Path(_ROOT) / "_check"
_RESULTS: list[tuple[str, bool, str]] = []
_NOTES: list[str] = []
_ART: dict[str, object] = {}


# ---------------------------------------------------------------- 记账
def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((name, bool(ok), detail))
    line = f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else "")
    print(line, flush=True)
    return bool(ok)


def note(text: str) -> None:
    _NOTES.append(text)
    print(f"NOTE  {text}", flush=True)


def art(key: str, value) -> None:
    _ART[key] = value


# ---------------------------------------------------------------- 读数工具
def doc(win):
    return win.txt_rx.document()


def block_texts(win) -> list[str]:
    d = doc(win)
    return [d.findBlockByNumber(b).text() for b in range(d.blockCount())]


def round_spans(win) -> list[tuple[int, int]]:
    """文档里每一轮（时间戳行起，到下一个时间戳行前）的块区间。"""
    d = doc(win)
    starts = [b for b in range(d.blockCount())
              if stamp_span(d.findBlockByNumber(b).text())]
    return [(starts[i], starts[i + 1] if i + 1 < len(starts) else d.blockCount())
            for i in range(len(starts))]


def span_of_stamp(win, stamp: str) -> tuple[int, int] | None:
    for start, end in round_spans(win):
        if doc(win).findBlockByNumber(start).text() == stamp:
            return start, end
    return None


def round_text(win, span: tuple[int, int]) -> str:
    d = doc(win)
    return "\n".join(d.findBlockByNumber(b).text() for b in range(span[0], span[1]))


def list_texts(win) -> list[str]:
    """列表项文字。⚠️ 列表活在「查看标记」弹窗里（平时 `_mark_list is None`）。"""
    lst = getattr(win, "_mark_list", None)
    if lst is None:
        return [f"{m.stamp} {m.summary}".strip() for m in win._marks.marks]
    return [lst.item(i).text() for i in range(lst.count())]


def open_marks(win, app) -> None:
    """确保弹窗**可见**（列表在弹窗里）。

    ⚠️ `_mark_list` 一旦建过就不再是 None（弹窗 accept 只是 hide），所以判据必须是
    "弹窗是否可见"，否则第二次点击会落在隐藏控件上、静默无效。
    """
    if not win._marks.marks:
        return
    dlg = getattr(win, "_marks_dialog", None)
    if dlg is None or not dlg.isVisible():
        win._show_marks_dialog()
        app.processEvents()


def store_snapshot(win) -> list[tuple]:
    return [(m.key, m.start_block, m.stamp, m.summary) for m in win._marks.marks]


def block_visible(win, block_no: int) -> bool:
    """该块的首行当前是否落在视口内（cursorRect 在视口坐标系）。"""
    d = doc(win)
    cur = QTextCursor(d.findBlockByNumber(block_no))
    rect = win.txt_rx.cursorRect(cur)
    vp = win.txt_rx.viewport().rect()
    return vp.top() <= rect.top() < vp.bottom()


def img_arr(pixmap) -> np.ndarray:
    img = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB32)
    buf = img.constBits()
    arr = np.frombuffer(buf, dtype=np.uint8)
    arr = arr.reshape(img.height(), img.bytesPerLine() // 4, 4)
    return arr[:, : img.width(), :3].astype(np.int16)


def diff_pixels(before, after) -> int:
    a, b = img_arr(before), img_arr(after)
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    a, b = a[:h, :w], b[:h, :w]
    return int((np.abs(a - b).max(axis=2) > 6).sum())


def settle(app, ms: int = 320) -> None:
    """转一会儿事件循环：回放提示条是 220ms 补间，视口高度会变 —— 抓图前必须等它收敛。"""
    import time
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000.0 < ms:
        app.processEvents()
        time.sleep(0.008)


# ---------------------------------------------------------------- 灌数
def feed_round(win, stamp: str, base: int) -> None:
    """灌一轮（显式时间戳行，`chk_timestamp` 关闭 → 屏上就是这一行）。"""
    win._emit_block(
        f"[{stamp}]\n"
        f"Voltage1:\n{base:02d}.3800 {base + 1:02d}.3820 {base + 2:02d}.3900\n"
        f"Temperature1:\n1.25 2.25 3.25\nBMS OK")


def click_mark_item(win, app, idx: int) -> str:
    """点列表项：优先真实鼠标点击，点不到（不在视口内）就退回 itemClicked 信号。"""
    open_marks(win, app)
    item = win._mark_list.item(idx)
    win._mark_list.scrollToItem(item)
    app.processEvents()
    rect = win._mark_list.visualItemRect(item)
    if win._mark_list.viewport().rect().contains(rect.center()):
        QTest.mouseClick(win._mark_list.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, rect.center())
        app.processEvents()
        return "mouse"
    win._mark_list.itemClicked.emit(item)
    app.processEvents()
    return "signal"


def press(win, app, key, mod=Qt.KeyboardModifier.NoModifier, target=None) -> None:
    target = target if target is not None else win
    QTest.keyClick(target, key, mod)
    app.processEvents()


# ---------------------------------------------------------------- 主流程
def main() -> int:                                    # noqa: C901 - 探针
    app = QApplication.instance() or QApplication(sys.argv)
    from ui_theme import ui_font
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))

    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}
    soc_before = qs.value("custom/soc_source")
    art("qsettings_keys_before", len(snap))
    art("soc_source_before", soc_before)

    hist_dir = Path(tempfile.mkdtemp(prefix="qa_ms_hist_", dir=_CHECK))
    db_dir = Path(tempfile.mkdtemp(prefix="qa_ms_db_", dir=_CHECK))
    real_resolver = rx_history._resolve_dir
    real_db = rx_store.db_path()
    toasts: list[str] = []
    win = None

    # ---- 0 静态核对（工程师声称的"只有一套"先看代码面）----
    src = (_CHECK.parent / "main_window.py").read_text(encoding="utf-8")
    check("0a 源码只有一处 MarkStore() 实例化",
          src.count("self._marks = MarkStore()") == 1,
          f"count={src.count('self._marks = MarkStore()')}")
    check("0b 无 _marks_live/_marks_recent/_marks_long 残留",
          not any(t in src for t in ("_marks_live", "_marks_recent", "_marks_long")))
    check("0c 无 _set_mark_scope 多套调度", "_set_mark_scope" not in src)
    _mm = (_CHECK.parent / "marks_model.py").read_text(encoding="utf-8")
    check("0d marks_model 文档改为单套共享", "单套共享" in _mm)

    try:
        rx_history._resolve_dir = lambda: hist_dir
        rx_store.set_db_path(str(db_dir / "sessions.db"))
        import main_window
        main_window.REPLAY_PAGE_LINES  # noqa: B018 - 触发 import 完成

        win = main_window.MainWindow()
        win.resize(1200, 800)
        win.show()
        win.raise_()
        win.activateWindow()
        app.processEvents()
        # 屏上时间戳关掉：本轮探针要**自己控制锚点**（自动戳同秒会重名，
        # 让"按 stamp 全文重定位"退化成找第一处，掩盖真实语义）。
        win.chk_timestamp.setChecked(False)
        win._marks.clear()
        win._refresh_mark_list()
        app.processEvents()
        # 记录 toast（保留真实行为，只旁听）
        _real_toast = win._toast
        win._toast = lambda message, ms=3000: (toasts.append(message),
                                               _real_toast(message, ms))[1]

        store_id0 = id(win._marks)
        art("store_id", store_id0)
        _sib = [a for a in vars(win)
                if a.startswith("_marks_") and a not in ("_marks_model", "_marks_dialog")]
        check("0e 实例属性里只有一套 MarkStore（只允许 _marks_dialog 这个弹窗句柄）",
              not _sib, str(_sib))

        # ================= 1 实时：灌数 + 打 1 标 =================
        live_stamps = [f"2026-11-24 10:00:0{i}" for i in range(4)]
        for i, st in enumerate(live_stamps):
            feed_round(win, st, 1 + i * 3)
        app.processEvents()
        art("live_doc_lines", len(block_texts(win)))
        art("live_rounds", len(round_spans(win)))
        check("1a 实时灌进 4 轮", len(round_spans(win)) == 4,
              f"rounds={len(round_spans(win))} blocks={doc(win).blockCount()}")
        check("1b 新窗标记为空", len(win._marks.marks) == 0
              and win._mark_list is None)
        check("1c 实时态 btn_mark / _mark_list 可用",
              win.btn_mark.isEnabled() and win.btn_marks.isEnabled())

        win.btn_mark.click()                       # 真实按钮信号（clicked(bool)）
        app.processEvents()
        live_mark = win._marks.marks[-1] if win._marks.marks else None
        check("1d 实时打 1 标（点按钮）→ 列表 1 条",
              live_mark is not None and len(win._marks.marks) == 1,
              f"list={list_texts(win)}")
        check("1e 标记锚点是文档里真实的时间戳行",
              live_mark is not None
              and any(block_texts(win)[b] == live_mark.stamp
                      for b, _e in round_spans(win)),
              f"stamp={getattr(live_mark, 'stamp', None)!r}")
        live_mark_stamp = getattr(live_mark, "stamp", "")
        art("live_mark_stamp", live_mark_stamp)

        # ================= 2 近期回放：同一套、不清空 =================
        rx_history.flush()
        rx_history._pending.clear()
        # 把近期留存换成**另一批时间戳**（模拟"更早/另一段"留存）：
        # 实时那 1 条标记的轮在回放文档里**不存在** → 同时能验"跳到不存在的标"。
        # ⚠️ 10 轮（≈90 行）而不是 3 轮：配置卡里的常驻列表改成按钮后接收视口高了
        #    ~120px，3 轮的文档装得下 → 滚动条 maximum=0，"滚动前后像素必须变化"
        #    的自检会恒 0（探针侧假红，不是产品问题）。
        replay_stamps = [f"2026-12-01 08:00:{10 * i:02d}" for i in range(10)]
        rows: list[str] = []
        for i, st in enumerate(replay_stamps):
            rows += [f"[{st}]", "",
                     "Voltage1:", f"{i + 1:02d}.3800 {i + 2:02d}.3820", "",
                     "Temperature1:", "1.25 2.25", "", "BMS OK"]
        if rx_history._path is not None:
            rx_history._path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            rx_history._lines.clear()
            rx_history._lines.extend(rows)
            rx_history._file_lines = len(rows)

        before_texts = list_texts(win)
        win._open_history()
        settle(app)                       # 等回放提示条 220ms 补间收尾（视口高度会变）
        check("2a 进了近期回放（真实 _open_history）",
              win._replay_active and win._replay_kind == "recent",
              f"active={win._replay_active} kind={win._replay_kind!r} "
              f"blocks={doc(win).blockCount()}")
        check("2b 回放文档里确实没有实时那条标记的轮（前提）",
              span_of_stamp(win, live_mark_stamp) is None,
              f"stamps={[block_texts(win)[b] for b, _e in round_spans(win)]}")
        check("2c 进回放列表**仍是那 1 条**（未被清）",
              list_texts(win) == before_texts and len(win._marks.marks) == 1,
              f"{before_texts} -> {list_texts(win)}")
        check("2d 还是同一个 MarkStore 对象", id(win._marks) == store_id0)
        check("2e 回放态 btn_mark / btn_marks（查看标记）isEnabled() 均为 True",
              win.btn_mark.isEnabled() and win.btn_marks.isEnabled(),
              f"btn={win.btn_mark.isEnabled()} btn_marks={win.btn_marks.isEnabled()}")
        check("2f 回放态 tooltip 不再写「回放中不支持标记」",
              "不支持标记" not in win.btn_mark.toolTip(), win.btn_mark.toolTip())
        check("2f-2 回放态打标按钮可见、「查看标记」按钮未被隐藏",
              win.btn_mark.isVisible() and not win.btn_marks.isHidden(),
              f"btn_visible={win.btn_mark.isVisible()} "
              f"btn_marks_hidden={win.btn_marks.isHidden()} "
              f"config_card_visible={win._config_card.isVisible()}")
        check("2f-3 列表项无来源前缀（不是「近期 · / 历史 ·」各一套）",
              not any(t.startswith(("近期 ·", "历史 ·", "近期·", "历史·"))
                      for t in list_texts(win)),
              str(list_texts(win)))

        # 2g 回放态 Alt+M 真按键
        n0 = len(win._marks.marks)
        for target, label in ((win, "window"), (win.txt_rx, "txt_rx")):
            press(win, app, Qt.Key.Key_M, Qt.KeyboardModifier.AltModifier, target)
            if len(win._marks.marks) > n0:
                art("altm_target", label)
                break
        check("2g 回放态 Alt+M 打上第 2 标（真实按键）",
              len(win._marks.marks) == 2,
              f"marks={len(win._marks.marks)} list={list_texts(win)}")
        replay_mark = win._marks.marks[-1] if len(win._marks.marks) >= 2 else None
        art("replay_mark_stamp", getattr(replay_mark, "stamp", ""))
        check("2h 回放态打的那条锚在回放文档里的轮上",
              replay_mark is not None
              and span_of_stamp(win, replay_mark.stamp) is not None,
              f"stamp={getattr(replay_mark, 'stamp', None)!r} "
              f"span={span_of_stamp(win, getattr(replay_mark, 'stamp', ''))}")

        # 观察项：同一轮连按两次 Alt+M 会得到两条文案相同的条目（MarkStore 不去重，
        # 这是改动前就有的行为，不属本次需求判据）。
        _dup_before = len(win._marks.marks)
        win._mark_current()
        app.processEvents()
        win._mark_current()
        app.processEvents()
        _dup = list_texts(win)
        note(f"同一轮连按两次 Alt+M：{_dup_before} 条 -> {len(_dup)} 条，"
             f"文案重复={len(set(_dup)) != len(_dup)}（MarkStore 无去重，改动前既有行为）")
        while len(win._marks.marks) > _dup_before:      # 还原，别污染后续计数
            win._marks.marks.pop()
        win._marks.current = len(win._marks.marks) - 1
        win._refresh_mark_list()
        app.processEvents()

        # ================= 3 回放态 Alt+↑/↓ 与点击跳转 =================
        # 抓图机制自检：像素判据只在"抓得到真实画面"时才成立，先证伪掉假绿。
        bar = win.txt_rx.verticalScrollBar()
        bar.setValue(0)
        settle(app, 120)
        _top = win.txt_rx.viewport().grab()
        bar.setValue(bar.maximum())
        settle(app, 120)
        _bot = win.txt_rx.viewport().grab()
        _sanity = diff_pixels(_top, _bot)
        check("3-pix 抓图机制自检（滚动前后视口像素必须变化）", _sanity > 1000,
              f"changed_pixels={_sanity}（<=1000 则后面像素判据一律不可信）")

        win._mark_selection = None                 # 清干净，单看这一下
        win._apply_extra_selections()
        app.processEvents()
        cur0, val0 = win._marks.current, bar.value()
        press(win, app, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
        cur_up, val_up = win._marks.current, bar.value()
        press(win, app, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
        cur_dn, val_dn = win._marks.current, bar.value()
        check("3a 回放态 Alt+↑ / Alt+↓ 有响应（游标变化）",
              cur_up != cur0, f"current {cur0} -> {cur_up} -> {cur_dn}")
        _cur_mark = win._marks.marks[win._marks.current]
        _cur_span = span_of_stamp(win, _cur_mark.stamp)
        _hit_span = None
        if win._mark_selection is not None:
            _b = doc(win).findBlock(
                win._mark_selection.cursor.selectionStart()).blockNumber()
            _hit_span = win._round_span(_b)
        check("3b Alt+↑/↓ 跳到了**当前游标那条标记的那一轮**（高亮落位正确）",
              _cur_span is not None and _hit_span == _cur_span
              and block_visible(win, _cur_span[0]),
              f"bar {val0} -> {val_up} -> {val_dn} cur={_cur_mark.stamp!r} "
              f"mark_span={_cur_span} highlighted={_hit_span}")

        # 3c 点"当前文档里不存在"的标记：只提示、不崩、不误跳
        win._clear_mark_highlight()                # 清掉 3b 装上的高亮，免得盖住画面差异
        settle(app)
        before_pix = win.txt_rx.viewport().grab()
        before_val = bar.value()
        sel_before = win._mark_selection
        toasts.clear()
        path_used = click_mark_item(win, app, 0)    # 0 = 实时那条（回放文档里没有）
        sel_after = win._mark_selection
        after_pix = win.txt_rx.viewport().grab()
        check("3c 点不存在于当前文档的标记 → 提示「尚未加载或已被裁剪」",
              any("尚未加载或已被裁剪" in t for t in toasts),
              f"click={path_used} toasts={toasts}")
        check("3d 该下只提示：视口不跳、高亮不变、画面不变、不崩",
              bar.value() == before_val and sel_after is sel_before
              and diff_pixels(before_pix, after_pix) == 0,
              f"bar {before_val}->{bar.value()} sel_same={sel_after is sel_before} "
              f"pix_diff={diff_pixels(before_pix, after_pix)}")

        # 3e 点"当前文档里存在"的标记：整轮滚进视口 + 整轮 ExtraSelection
        idx_present = 1
        target_mark = win._marks.marks[idx_present]
        tspan = span_of_stamp(win, target_mark.stamp)
        settle(app)
        pre = win.txt_rx.viewport().grab()
        path_used = click_mark_item(win, app, idx_present)
        post = win.txt_rx.viewport().grab()
        sel = win._mark_selection
        sel_text = ""
        if sel is not None:
            sel_text = sel.cursor.selectedText().replace("\u2029", "\n").rstrip("\n")
        want = round_text(win, tspan) if tspan else ""
        check("3e 点存在的标记 → 该轮滚进视口",
              tspan is not None and block_visible(win, tspan[0]),
              f"click={path_used} span={tspan} bar={bar.value()}")
        # sel_len 比 want 少 1 = 末块段落分隔符不进 selectedText（视觉上覆盖整行），不是缺行
        check("3f 整轮 ExtraSelection 挂上且**范围正好是整轮**",
              sel is not None and sel_text.strip() == want.strip() and want.strip() != "",
              f"sel_len={len(sel_text)} want_len={len(want)} "
              f"has_sel={getattr(sel.cursor, 'hasSelection', lambda: None)() if sel else None}")
        d_pix = diff_pixels(pre, post)
        check("3g 高亮是**真的画出来了**（视口像素有变化）", d_pix > 0,
              f"changed_pixels={d_pix}")

        # 3h 悬空高亮嗅探（一）：高亮装着的时候**退回放**，旧 selection 会不会画到新内容上
        #     （_enter_replay/_exit_replay 都没清 _mark_selection）
        win._exit_replay()
        settle(app)
        exit_text = win.txt_rx.toPlainText().strip()
        check("4a 退出回放后标记仍 2 条（_exit_replay 不清）",
              len(win._marks.marks) == 2,
              f"marks={len(win._marks.marks)} list={list_texts(win)}")
        check("4b 退出回放确实清了画面（_clear_receive(clear_marks=False) 生效）",
              exit_text == "", f"text_len_after_exit={len(exit_text)}")
        check("4c 退出回放后 btn_mark / _mark_list 仍可用",
              win.btn_mark.isEnabled() and win.btn_marks.isEnabled())
        for i in range(2):                        # 退出回放后在实时画面灌新数据
            feed_round(win, f"2026-11-24 23:00:0{i}", 20 + i * 3)
        settle(app)
        stale = win._mark_selection
        stale_sel = stale.cursor.selectedText() if stale is not None else None
        pix_with = win.txt_rx.viewport().grab()
        win._clear_mark_highlight()
        app.processEvents()
        pix_without = win.txt_rx.viewport().grab()
        d_stale = diff_pixels(pix_with, pix_without)
        check("3h 退回实时后旧标记高亮不残留（无幽灵高亮）", d_stale == 0,
              f"stale_selection_len={len(stale_sel or '')} phantom_pixels={d_stale}")

        # ================= 4 退出回放不清零（续） =================
        sel_before = win._mark_selection
        toasts.clear()
        path_used = click_mark_item(win, app, 0)
        check("4d 实时画面（空）里点旧标记 → 只提示不崩",
              any("尚未加载或已被裁剪" in t for t in toasts),
              f"click={path_used} toasts={toasts}")

        # ================= 5 历史回放（rx_store）同一套 =================
        rx_store.flush(8.0)
        before_texts = list_texts(win)
        win._open_long_history()
        settle(app)
        check("5a 进了历史回放（真实 _open_long_history）",
              win._replay_active and win._replay_kind == "long",
              f"active={win._replay_active} kind={win._replay_kind!r} "
              f"blocks={doc(win).blockCount()}")
        check("5b 进历史回放列表**仍是那 2 条**",
              list_texts(win) == before_texts and len(win._marks.marks) == 2,
              f"{before_texts} -> {list_texts(win)}")
        check("5c 历史回放态 btn_mark / _mark_list 可用",
              win.btn_mark.isEnabled() and win.btn_marks.isEnabled())
        win.btn_mark.click()
        app.processEvents()
        long_mark = win._marks.marks[-1] if win._marks.marks else None
        check("5d 历史回放里打第 3 标 → 同一套 3 条",
              len(win._marks.marks) == 3,
              f"list={list_texts(win)}")
        check("5e 第 3 条锚在历史回放文档里的轮上",
              long_mark is not None
              and span_of_stamp(win, long_mark.stamp) is not None,
              f"stamp={getattr(long_mark, 'stamp', None)!r}")
        win._exit_replay()
        app.processEvents()
        check("5f 退出历史回放仍 3 条",
              len(win._marks.marks) == 3,
              f"list={list_texts(win)}")
        check("5g 全程同一个 MarkStore 对象", id(win._marks) == store_id0)

        # ================= 6 清零语义 =================
        win.btn_clear.click()                     # ⚠️ clicked(bool) 真实路径
        app.processEvents()
        check("6a 点「清空接收区」（clicked(bool) 路径）→ 标记清零",
              len(win._marks.marks) == 0,
              f"marks={len(win._marks.marks)} list={list_texts(win)}")
        # 6b Ctrl+L 快捷路径
        feed_round(win, "2026-11-25 09:00:00", 30)
        app.processEvents()
        win._mark_current()
        app.processEvents()
        n_before = len(win._marks.marks)
        press(win, app, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
        check("6b Ctrl+L 也清标记（无参 QShortcut 路径）",
              n_before == 1 and len(win._marks.marks) == 0,
              f"{n_before} -> {len(win._marks.marks)}")
        # 6c 直接调 _clear_receive(clear_marks=False) 不清
        feed_round(win, "2026-11-25 09:10:00", 33)
        app.processEvents()
        win._mark_current()
        app.processEvents()
        n_before = len(win._marks.marks)
        win._clear_receive(clear_marks=False)
        app.processEvents()
        check("6c _clear_receive(clear_marks=False) 只清画面不清标记",
              n_before == 1 and len(win._marks.marks) == 1
              and win.txt_rx.toPlainText().strip() == "",
              f"{n_before} -> {len(win._marks.marks)} "
              f"text_len={len(win.txt_rx.toPlainText())}")

        # ================= 7 MarkStore 纯逻辑边界 =================
        ms = marks_model.MarkStore()
        a = ms.add(3, "[t1]", "s1")
        b = ms.add(9, "[t1]", "s2")
        check("7a 同 stamp 两次打标 key 不撞（seq 区分）", a.key != b.key, f"{a.key} {b.key}")
        check("7b next/prev 循环", ms.next() is not None and ms.prev() is not None)
        ms.clear()
        check("7c clear 后 next() 为 None", ms.next() is None and ms.marks == [])

        # ---- 观察项：同 stamp 出现两轮 + 块号漂移 → 全文重定位只认第一处 ----
        # 不是本次需求的判据（改动前就存在），但"标记现在能跨文档存活"会把漂移
        # 变成常态，所以量化一下会不会"误跳到隔壁那轮"。
        import dataclasses
        win._marks.clear()
        win._refresh_mark_list()
        win._clear_receive()                       # 清画面（顺带清空标记）
        feed_round(win, "2026-11-30 07:00:00", 40)
        feed_round(win, "2026-11-30 07:00:05", 43)
        feed_round(win, "2026-11-30 07:00:05", 46)     # 同秒两轮 = 同一个 stamp
        feed_round(win, "2026-11-30 07:00:10", 49)
        app.processEvents()
        _spans = round_spans(win)
        if len(_spans) == 4:
            _b2 = _spans[2]
            _stamp_b = doc(win).findBlockByNumber(_b2[0]).text()
            win._marks.add(_b2[0], _stamp_b, "dup-stamp")
            win._refresh_mark_list()
            win._mark_selection = None
            win._apply_extra_selections()
            open_marks(win, app)
            win._on_mark_item_clicked(win._mark_list.item(0))
            app.processEvents()
            _fast_ok = (win._mark_selection is not None
                        and doc(win).findBlock(
                            win._mark_selection.cursor.selectionStart()).blockNumber()
                        == _b2[0])
            win._marks.marks[0] = dataclasses.replace(win._marks.marks[0],
                                                     start_block=0)   # 模拟块号漂移
            open_marks(win, app)
            win._on_mark_item_clicked(win._mark_list.item(0))
            app.processEvents()
            _got = (doc(win).findBlock(
                win._mark_selection.cursor.selectionStart()).blockNumber()
                if win._mark_selection is not None else -1)
            note(f"同 stamp 两轮（{_stamp_b!r}）：块号未漂时点标记 → "
                 f"{'命中标记那轮' if _fast_ok else '已跳错'}（block {_b2[0]}）；"
                 f"人为把 start_block 弄坏（模拟回放换文档/裁剪）后重扫 → "
                 f"落到 block {_got}（标记那轮是 {_b2[0]}，"
                 f"{'误跳到同 stamp 的隔壁轮' if _got != _b2[0] else '仍命中'}）。"
                 f"属改动前既有局限：重定位只按 stamp 文本，同秒重复戳无法区分。")

        art("final_marks", len(win._marks.marks))
        art("toBeDetermined", True)
    except Exception:                             # noqa: BLE001 - 探针要留证据
        print("EXCEPTION:\n" + traceback.format_exc(), flush=True)
        check("!! 探针异常中断（上面既有结论都有效）", False,
              traceback.format_exc(limit=3).replace("\n", " | ")[:400])
    finally:
        if win is not None:
            try:
                win.close()
                win.deleteLater()
            except Exception:                     # noqa: BLE001
                pass
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        rx_history._resolve_dir = real_resolver
        try:
            rx_store.shutdown()
            rx_store.set_db_path(real_db)
            rx_store.reset_for_tests()
        except Exception:                         # noqa: BLE001
            pass
        for k in list(qs.allKeys()):
            if k not in snap:
                qs.remove(k)
        for k, v in snap.items():
            qs.setValue(k, v)
        qs.sync()
        shutil.rmtree(hist_dir, ignore_errors=True)
        shutil.rmtree(db_dir, ignore_errors=True)

    # ================= 8 QSettings 零污染 =================
    now = {k: qs.value(k) for k in qs.allKeys()}
    soc_after = qs.value("custom/soc_source")
    art("qsettings_keys_after", len(now))
    art("soc_source_after", soc_after)
    check("8a QSettings 全量快照前后完全一致",
          now == snap, f"{len(snap)} 键 -> {len(now)} 键")
    check("8b custom/soc_source 不变（read 仍是 read）",
          soc_before == soc_after, f"{soc_before!r} -> {soc_after!r}")
    check("8c 注册表里没有任何 mark 相关键",
          not [k for k in now if "mark" in k.lower()],
          str([k for k in now if "mark" in k.lower()]))

    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    tail = (f"\n合计 {passed}/{total} PASS  ->  "
            f"IS_PASS: {'YES' if passed == total else 'NO'}")
    print(tail, flush=True)

    lines = [f"{'PASS' if ok else 'FAIL'}  {n}" + (f"   [{d}]" if d else "")
             for n, ok, d in _RESULTS]
    (_CHECK / "qa_markshared_report.txt").write_text(
        "\n".join(lines) + "\n\nNOTES\n" + "\n".join(_NOTES) + "\n" + tail + "\n",
        encoding="utf-8")
    md = ["# QA 独立验收：实时 / 近期 / 历史共用同一套标记", "",
          f"- 结果：**{passed}/{total} PASS**（IS_PASS={'YES' if passed == total else 'NO'}）",
          f"- QSettings：{len(snap)} 键 → {len(now)} 键；"
          f"soc_source {soc_before!r} → {soc_after!r}", "",
          "| # | 判据 | 结果 | 证据 |", "|---|---|---|---|"]
    for n, ok, d in _RESULTS:
        md.append(f"| {n.split(' ')[0]} | {' '.join(n.split(' ')[1:])} | "
                  f"{'PASS' if ok else '**FAIL**'} | {d.replace('|', '/')} |")
    if _NOTES:
        md += ["", "## NOTES"] + [f"- {t}" for t in _NOTES]
    md += ["", "## 关键读数", "```"] + [f"{k} = {v}" for k, v in _ART.items()] + ["```"]
    (_CHECK / "qa_markshared_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    app.quit()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
