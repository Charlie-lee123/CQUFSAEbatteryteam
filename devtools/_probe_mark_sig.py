# -*- coding: utf-8 -*-
r"""一次性真平台探针：**轮内容签名**主锚点（消掉快速路径误命中）+ 三层锚点。

打穿 QA 抓的 4e-1 / 4b-3：三处同 stamp、每轮行数固定 35，标记第 2 处 → 用产品
自己的 ``setMaximumBlockCount`` 让「块号位移恰好 == 一整轮行数 35」→ 旧快速路径
（``block.text()==stamp`` 就直接用）会误命中同秒相邻轮（got=R3 期望=R2）。本修复
用轮内容签名做主锚 + 删快速路径，必须落 R2，且再打标不新增。

覆盖（写 _check/mark_sig_probe.txt）：
  A 三位同 stamp、轮行固定 35，标第 2 处 → setMaximumBlockCount 位移=35 → 落 R2
  B 同场景再按 Alt+M 不新增（内容签名去重）
  C start_block 改成 99999（纯漂移）→ 仍落 R2（签名主锚，不靠块号）
  D 回放顶部前插同 stamp 更早页 → 仍落原轮
  E 同秒两轮（内容不同）→ 2 条不误判
  F 清屏清零
  G QSettings 前后零变化
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtWidgets import QApplication

import customization
import main_window
import rx_history

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def token_of(win, span) -> str:
    doc = win.txt_rx.document()
    for b in range(span[0] + 1, span[1]):
        t = doc.findBlockByNumber(b).text().strip()
        if "TOKEN_" in t:
            return t
    return ""


def highlighted_token(win) -> str:
    sel = win._mark_selection
    if sel is None:
        return ""
    pos = sel.cursor.selectionStart()
    bn = win.txt_rx.document().findBlock(pos).blockNumber()
    span = win._round_span(bn)
    return "" if span is None else token_of(win, span)


def build_round(stamp: str, token: str, pad: int = 28) -> str:
    """一轮固定 35 行：stamp + Voltage1: + 值 + token + pad 填充 + BMS OK = 4+pad+... 由调用方调。"""
    body = [stamp, "Voltage1:", " 1.3800", token]
    body += [f"pad{i}" for i in range(pad)]
    body.append("BMS OK")
    return "\n".join(body) + "\n"


def main() -> int:
    import shutil
    import tempfile
    from pathlib import Path

    app = QApplication.instance() or QApplication(sys.argv)
    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}
    real_dir = rx_history._resolve_dir
    real_pin = customization.current_vehicle
    check_dir = Path(_ROOT) / "_check"
    tmpdir = Path(tempfile.mkdtemp(prefix="marksig_", dir=check_dir))
    win = None
    try:
        rx_history._resolve_dir = lambda: tmpdir
        customization.current_vehicle = lambda: "E74-2026"
        win = main_window.MainWindow()
        win.resize(1200, 800)
        win.show()
        app.processEvents()
        win._marks.clear()
        win._refresh_mark_list()

        STAMP = "[2026-11-24 12:00:00]"

        # ---- A 三处同 stamp、轮行数固定 ----
        win._emit_block(build_round(STAMP, "TOKEN_R1"))
        win._emit_block(build_round(STAMP, "TOKEN_R2"))
        win._emit_block(build_round(STAMP, "TOKEN_R3"))
        app.processEvents()
        doc = win.txt_rx.document()
        stamps = [b for b in range(doc.blockCount())
                  if doc.findBlockByNumber(b).text() == STAMP]
        round_len = stamps[1] - stamps[0] if len(stamps) >= 2 else 35
        check("A0 三处同 stamp 且轮行数一致", len(stamps) == 3, f"stamps={stamps} round_len={round_len}")

        # 标记第 2 处（用产品同口径算 content_sig / rev）
        blk2 = stamps[1]
        span2 = win._round_span(blk2)
        sig2 = win._round_content_sig(span2)
        rev2 = sum(1 for b in range(blk2 + 1, doc.blockCount())
                   if doc.findBlockByNumber(b).text() == STAMP)
        win._marks.add_unique(blk2, STAMP, token_of(win, span2), rev2, sig2)
        win._refresh_mark_list()
        mark = win._marks.marks[0]
        check("A1 标记第2处内容=TOKEN_R2、rev=1、有签名",
              token_of(win, span2) == "TOKEN_R2" and mark.stamp_rev == 1
              and bool(mark.content_sig), f"token={token_of(win, span2)} rev={mark.stamp_rev}")

        # 用产品自己的 setMaximumBlockCount 让「块号位移 == 一整轮行数」：
        # 原 stamps=[s0,s1,s2]，裁掉 round_len 块 → R3 的时间戳落到 start_block(blk2)
        # 那个**块号**上（文本仍是 stamp）→ 正是 4e-1 的误命中来源。Qt 对
        # setMaximumBlockCount 会**立刻**从头裁到上限，所以直接设 cur-round_len 即可。
        # ⚠️ 不能再用 _emit_block 触发（那会走整篇 align 重排、块数乱变，测不准）。
        cur_blocks = doc.blockCount()
        win.txt_rx.setMaximumBlockCount(cur_blocks - round_len)
        app.processEvents()
        doc = win.txt_rx.document()
        stamps_after = [b for b in range(doc.blockCount())
                        if doc.findBlockByNumber(b).text() == STAMP]
        if len(stamps_after) == 3:      # 某些 Qt 版本不立即裁，补一行原始文本触发
            win.txt_rx.appendPlainText("Z")
            app.processEvents()
            doc = win.txt_rx.document()
            stamps_after = [b for b in range(doc.blockCount())
                            if doc.findBlockByNumber(b).text() == STAMP]
        check("A2 头裁后同 stamp 位移=整轮行数（剩 2 处）",
              len(stamps_after) == 2, f"after={stamps_after}")

        win._jump_to_mark(mark.key)
        app.processEvents()
        got = highlighted_token(win)
        check("A3 位移=整轮行数仍跳到原第2处(TOKEN_R2)", got == "TOKEN_R2", f"got={got!r}")

        # ---- B 同场景再按 Alt+M 不新增（内容签名去重）----
        # 在 R2 那轮再走一次 add_unique（等价 Alt+M）
        b2 = next(b for b in range(doc.blockCount())
                  if doc.findBlockByNumber(b).text() == STAMP
                  and token_of(win, win._round_span(b)) == "TOKEN_R2")
        sp2 = win._round_span(b2)
        sigb = win._round_content_sig(sp2)
        revb = sum(1 for b in range(b2 + 1, doc.blockCount())
                   if doc.findBlockByNumber(b).text() == STAMP)
        _m, created = win._marks.add_unique(b2, STAMP, token_of(win, sp2), revb, sigb)
        check("B1 再打标不新增(created=False)", created is False,
              f"created={created} count={len(win._marks.marks)}")
        check("B2 列表仍 1 条", win._mark_list.count() == 1,
              str([win._mark_list.item(i).text() for i in range(win._mark_list.count())]))

        # ---- C start_block 改成 99999（纯漂移）仍落 R2 ----
        import dataclasses
        win._marks.marks[0] = dataclasses.replace(win._marks.marks[0], start_block=99999)
        win._jump_to_mark(win._marks.marks[0].key)
        app.processEvents()
        gotC = highlighted_token(win)
        check("C1 start_block=99999 纯漂移仍落 TOKEN_R2", gotC == "TOKEN_R2", f"got={gotC!r}")

        # ⚠️ 探针卫生：A 段为了让头裁位移=整轮行数，把 setMaximumBlockCount 调小了；
        # 这里恢复到产品实时默认（_rx_blocks），否则后面 H/I/J 的末轮会被持续头裁干扰。
        win.txt_rx.setMaximumBlockCount(win._rx_blocks)
        app.processEvents()

        # ---- D 回放顶部前插同 stamp 更早页 → 仍落原轮 ----
        win._clear_receive()
        app.processEvents()
        win._emit_block(build_round(STAMP, "TOKEN_A"))
        win._emit_block(build_round(STAMP, "TOKEN_B"))
        app.processEvents()
        doc = win.txt_rx.document()
        st = [b for b in range(doc.blockCount())
              if doc.findBlockByNumber(b).text() == STAMP]
        blkB = st[-1]
        spanB = win._round_span(blkB)
        sigB = win._round_content_sig(spanB)
        revB = sum(1 for b in range(blkB + 1, doc.blockCount())
                   if doc.findBlockByNumber(b).text() == STAMP)   # 0
        win._marks.clear()
        win._marks.add_unique(blkB, STAMP, token_of(win, spanB), revB, sigB)
        mB = win._marks.marks[0]
        cur_text = win.txt_rx.toPlainText()
        win.txt_rx.setPlainText(build_round(STAMP, "TOKEN_A0").rstrip("\n") + "\n" + cur_text)
        app.processEvents()
        win._jump_to_mark(mB.key)
        app.processEvents()
        gotD = highlighted_token(win)
        check("D1 顶部前插后仍落原轮(TOKEN_B)", gotD == "TOKEN_B", f"got={gotD!r}")

        # ---- E 同秒两轮（内容不同）→ 2 条不误判 ----
        win._clear_receive()
        app.processEvents()
        win._emit_block(build_round(STAMP, "TOKEN_X"))
        win._emit_block(build_round(STAMP, "TOKEN_Y"))
        app.processEvents()
        doc = win.txt_rx.document()
        st2 = [b for b in range(doc.blockCount())
               if doc.findBlockByNumber(b).text() == STAMP]
        win._marks.clear()
        for blk in st2:
            sp = win._round_span(blk)
            sg = win._round_content_sig(sp)
            rv = sum(1 for b in range(blk + 1, doc.blockCount())
                     if doc.findBlockByNumber(b).text() == STAMP)
            win._marks.add_unique(blk, STAMP, token_of(win, sp), rv, sg)
        win._refresh_mark_list()
        check("E1 同秒两轮各成 1 条共 2 条", len(win._marks.marks) == 2,
              f"count={len(win._marks.marks)}")
        check("E2 两条签名不同", win._marks.marks[0].content_sig
              != win._marks.marks[1].content_sig,
              f"{win._marks.marks[0].content_sig[:8]} vs {win._marks.marks[1].content_sig[:8]}")

        # ============ 末轮主路径（QA 抓到的空行污染）============
        # 标记**最新一轮**（此刻它是文档末轮，span 右端=文档末尾）；之后新轮到达时
        # gap_lines 会在新时间戳前插一行空行进末轮 span → 若签名不 strip 尾空行，
        # 就会①跳到相邻轮②去重失效出两条。以下三条专门照这个主路径。

        def mark_last_round() -> object:
            """模拟用户在第末轮按 Alt+M：等价 _mark_current（用真实 span/签名/rev）。"""
            d = win.txt_rx.document()
            sts = [b for b in range(d.blockCount())
                   if d.findBlockByNumber(b).text() == STAMP]
            blk = sts[-1]                       # 最新（最后）一轮
            sp = win._round_span(blk)
            sg = win._round_content_sig(sp)
            rv = sum(1 for b in range(blk + 1, d.blockCount())
                     if d.findBlockByNumber(b).text() == STAMP)
            mk, _c = win._marks.add_unique(blk, STAMP, token_of(win, sp), rv, sg)
            win._refresh_mark_list()
            return mk

        # ---- H 标最新轮 → 同秒再来一轮 → 跳回必须落原轮 ----
        win._clear_receive()
        app.processEvents()
        win._emit_block(build_round(STAMP, "TOKEN_LAST1"))
        app.processEvents()
        mkH = mark_last_round()
        _sigH_at_mark = mkH.content_sig
        win._emit_block(build_round(STAMP, "TOKEN_LAST2"))   # 同秒新轮 → 前插空行
        app.processEvents()
        check("H0 末轮签名未被后续同秒轮的空行污染",
              win._marks.by_key(mkH.key).content_sig == _sigH_at_mark)
        win._jump_to_mark(mkH.key)
        app.processEvents()
        gotH = highlighted_token(win)
        check("H1 标最新轮→同秒来一轮→跳回落原轮(TOKEN_LAST1)",
              gotH == "TOKEN_LAST1", f"got={gotH!r}")

        # ---- I 标最新轮 → 来一轮(不同 stamp) → 对同一轮再 Alt+M 不新增 ----
        win._clear_receive()
        app.processEvents()
        win._emit_block(build_round(STAMP, "TOKEN_ONLY"))
        app.processEvents()
        mark_last_round()
        win._emit_block(build_round("[2026-11-24 12:00:07]", "TOKEN_OTHER"))
        app.processEvents()
        # 再对 TOKEN_ONLY 那轮走一次 add_unique（等价再按 Alt+M）
        d = win.txt_rx.document()
        blkI = next(b for b in range(d.blockCount())
                    if d.findBlockByNumber(b).text() == STAMP
                    and token_of(win, win._round_span(b)) == "TOKEN_ONLY")
        spI = win._round_span(blkI)
        sgI = win._round_content_sig(spI)
        rvI = sum(1 for b in range(blkI + 1, d.blockCount())
                  if d.findBlockByNumber(b).text() == STAMP)
        _mI, createdI = win._marks.add_unique(blkI, STAMP, token_of(win, spI), rvI, sgI)
        check("I1 标最新轮→来不同stamp轮→再打标不新增(created=False)",
              createdI is False, f"created={createdI} count={len(win._marks.marks)}")
        check("I2 列表仍 1 条（无重复）", win._mark_list.count() == 1,
              str([win._mark_list.item(i).text() for i in range(win._mark_list.count())]))

        # ---- J 标最新轮 → 来两轮(其中一轮同秒) → 跳回仍落原轮 ----
        win._clear_receive()
        app.processEvents()
        win._emit_block(build_round(STAMP, "TOKEN_J_ORIG"))
        app.processEvents()
        mkJ = mark_last_round()
        win._emit_block(build_round(STAMP, "TOKEN_J_SAME"))   # 同秒
        win._emit_block(build_round("[2026-11-24 12:00:09]", "TOKEN_J_DIFF"))
        app.processEvents()
        win._jump_to_mark(mkJ.key)
        app.processEvents()
        gotJ = highlighted_token(win)
        check("J1 标最新轮→来两轮(含同秒)→跳回仍落原轮(TOKEN_J_ORIG)",
              gotJ == "TOKEN_J_ORIG", f"got={gotJ!r}")

        # ---- F 清屏清零 ----
        win._clear_receive()
        app.processEvents()
        check("F1 清屏后标记清零", len(win._marks.marks) == 0
              and win._mark_list.count() == 0)

    finally:
        if win is not None:
            win.close()
            win.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        rx_history._resolve_dir = real_dir
        customization.current_vehicle = real_pin
        for k in list(qs.allKeys()):
            if k not in snap:
                qs.remove(k)
        for k, v in snap.items():
            qs.setValue(k, v)
        qs.sync()
        shutil.rmtree(tmpdir, ignore_errors=True)

    now = {k: qs.value(k) for k in qs.allKeys()}
    check("G QSettings 前后零变化", now == snap, f"keys {len(snap)} -> {len(now)}")
    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    tail = f"\n合计 {passed}/{total} PASS  ->  IS_PASS: {'YES' if passed == total else 'NO'}"
    print(tail)
    with open(os.path.join(_ROOT, "_check", "mark_sig_probe.txt"),
              "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                           (f"   [{d}]" if d else "") for n, ok, d in _RESULTS)
                 + tail + "\n")
    app.quit()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
