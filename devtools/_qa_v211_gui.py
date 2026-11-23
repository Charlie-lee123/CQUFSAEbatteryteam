# -*- coding: utf-8 -*-
"""独立验收者（QA qa-v211）证伪探针 —— v2.11 真窗口交互层。

真平台（QT_QPA_PLATFORM=windows）建真实 MainWindow，逐条证明：
  * 电压来源 read：底栏总压槽确实显示报文 V= 原文 0.00，不回退计算值；calc 恢复计算总压。
  * tooltip 含「恒为 0.00 / 照实显示」提示。
  * 默认 keep_awake_on_rx=False 时，构造窗口/收发不调用任何 OS setter（不污染本机电源策略）。
  * 标记：Alt+M 打标、列表出现条目；跳转变焦轮首并整轮高亮（_mark_selection / ExtraSelection）；
    Alt+Down 循环；清屏清零；回放态按钮禁用且不产新标；标记不写任何 QSettings 键。
  * 全程前后 QSettings 全量快照比对：真实注册表零改动。

⛔ 不改产品源码、不写真实 QSettings（QSettings shim 把 2 参数构造全部重定向到隔离 INI）、
   不 os._exit()，收尾 win.close() + app.quit()。

用法：QT_QPA_PLATFORM=windows .venv\\Scripts\\python.exe devtools/_qa_v211_gui.py
产物：_check/_qa_v211_gui.log（调用方重定向）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

_log: list[str] = []
_failures: list[str] = []


def emit(line: str = "") -> None:
    print(line)
    _log.append(line)


def check(name: str, ok: bool, detail: str = "") -> bool:
    emit(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  | {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


# -------------------------------------------------- QSettings shim（隔离 2 参数构造）
ISO_INI = str(Path(ROOT) / "_check" / "_qa_v211_qsettings_gui.ini")


def install_shim():
    """把 PySide6.QtCore.QSettings 的 2 字符串参数构造重定向到隔离 INI。

    必须在 import 任何产品模块之前调用：产品模块都写 ``from PySide6.QtCore import QSettings``，
    先替换再 import 才能让它们绑定到 shim。保留原类引用 RealQSettings 供"真实注册表"快照用。
    """
    import PySide6.QtCore as C
    Real = C.QSettings

    # 先清掉上轮隔离 INI
    p = Path(ISO_INI)
    p.parent.mkdir(exist_ok=True)
    if p.exists():
        p.unlink()

    class Shim(Real):
        def __init__(self, *a, **k):
            if len(a) in (1, 2) and a and all(isinstance(x, str) for x in a):
                super().__init__(ISO_INI, Real.Format.IniFormat)
            else:
                super().__init__(*a, **k)

    C.QSettings = Shim
    return Real


def snapshot(RealQSettings) -> dict:
    s = RealQSettings("MimoWorks", "SerialAssistant")
    return {k: s.value(k) for k in s.allKeys()}


def iso_keys() -> list[str]:
    from PySide6.QtCore import QSettings
    s = QSettings("MimoWorks", "SerialAssistant")
    return list(s.allKeys())


# -------------------------------------------------- 报文：把真实整轮的 V 行改成 0.00
def volt_block() -> str:
    """取仓库真实 125 节整轮，把 V=0.31 改成 V=0.00（用户场景：传感器恒 0）。"""
    txt = (Path(ROOT) / "_check" / "_raw_round.txt").read_text(encoding="utf-8")
    import re
    out, n = re.subn(r"V\s*=\s*-?\d+(?:\.\d+)?", "V=0.00", txt, count=1)
    if n == 0:
        out = "V=0.00,I=-0.14\n" + txt
    return out


def soc_labels(win) -> tuple[str, str, str, str]:
    return tuple(lbl.text() for lbl in win._soc_labels)


# -------------------------------------------------- 标记用多轮文本
def mark_text(rounds: int = 6) -> str:
    lines: list[str] = []
    for i in range(rounds):
        lines.append(f"[12:00:{i * 5:02d}]")
        lines.append(f"Voltage1: 01.{3700 + i} 02.{3800 + i}")
        lines.append("BMS OK")
        lines.append(f"V=0.00,I={-1.5 if i % 2 else 1.5}")
        lines.append(f"CVMax={3800 + i}, Min={3700 + i}")
        lines.append("")
    return "\n".join(lines)


def probe_voltage(app, win):
    emit("\n=== [1] 电压来源：真窗口 read 显示 V=0.00 原文、calc 恢复计算值 ===")
    import soc_est
    import soc_feed
    import soc_profiles

    # 这台机器当前车可能没配 SOC 参数：探针内临时搭 E74 链路（不写 QSettings）
    if win._soc_est is None or win._soc_feed is None:
        cfg = soc_profiles.cfg_for("E74-2026")
        check("E74-2026 车辆参数可用", cfg is not None, str(cfg))
        if cfg is None:
            return
        per = max(1, cfg.cells_per_module)
        win._soc_cfg = cfg
        win._soc_est = soc_est.SocEstimator(cfg)
        win._soc_feed = soc_feed.RoundCollector(
            n_modules=max(1, cfg.n_cells // per), cells_per_module=per)
        emit("  [env] 临时搭 E74 链路（进程内，不落盘）")
    else:
        emit("  [env] 主窗自带 SOC 链路")

    block = volt_block()
    win._set_soc_source("calc")
    win._set_volt_source("calc")
    win._feed_soc(block)
    app.processEvents()

    emit(f"  _volt_read_last={win._volt_read_last!r}  _volt_source={win._volt_source!r}")
    check("_feed_soc 同步了 V= 原文 0.00", win._volt_read_last == "0.00",
          repr(win._volt_read_last))

    calc = soc_labels(win)
    emit(f"  [calc] 四槽={calc}")
    check("calc: 总压槽是计算值（非空、非 0.00）",
          len(calc) == 4 and calc[3].startswith("总电压 ")
          and "0.00" not in calc[3], calc[3] if len(calc) > 3 else str(calc))
    calc_volt = calc[3]

    # 切到 read：总压槽必须变成原文 0.00
    win._set_volt_source("read")
    app.processEvents()
    read = soc_labels(win)
    emit(f"  [read] 四槽={read}  _volt_source={win._volt_source!r}")
    check("read: 总压槽 == '总电压 0.00 V'（照实，不回退计算）",
          read[3] == "总电压 0.00 V", read[3])
    check("read: 与计算值确实不同（证伪假绿）", read[3] != calc_volt,
          f"calc={calc_volt!r} read={read[3]!r}")
    check("read: 只动第 4 槽，前 3 槽原样", read[:3] == calc[:3],
          f"{calc[:3]} -> {read[:3]}")

    # 无读数（换车作废 V=）：read 模式应显示 总电压 -- V，不回退计算值
    saved = win._volt_read_last
    win._volt_read_last = None
    win._refresh_soc_now()
    app.processEvents()
    empty = soc_labels(win)
    emit(f"  [read/无读数] 四槽={empty}")
    check("read: 无 V= 读数显示 '总电压 -- V'（不回退）",
          empty[3] == "总电压 -- V", empty[3])
    win._volt_read_last = saved

    # tooltip
    tip = win.lbl_soc_volt.toolTip()
    emit(f"  tooltip={tip!r}")
    check("tooltip 含 0.00 / 照实 / 不回退 提示",
          ("0.00" in tip and "照实" in tip and "回退" in tip), tip)

    # 切回 calc：恢复计算总压
    win._set_volt_source("calc")
    app.processEvents()
    back = soc_labels(win)
    emit(f"  [calc/切回] 四槽={back}")
    check("切回 calc: 总压恢复计算值且与首次一致", back[3] == calc_volt,
          f"{calc_volt!r} -> {back[3]!r}")

    # 独立性：soc 来源切 read，总压 calc 仍是计算值（volt 不复用 soc_source）
    win._set_soc_source("read")
    app.processEvents()
    ind = soc_labels(win)
    emit(f"  [soc=read, volt=calc] 四槽={ind}")
    check("独立: soc=read 时 volt=calc 总压仍是计算值",
          ind[3] == calc_volt, f"{ind[3]!r} vs {calc_volt!r}")
    win._set_soc_source("calc")


def probe_wake_default(app, win, wake_mod, calls: list[int]):
    emit("\n=== [2] 防息屏：默认 keep_awake_on_rx=False 不调用 OS setter ===")
    check("窗口构造后 OS setter 调用数 == 0（默认关）", calls == [], str(calls))
    check("窗口内 _wake_lock.enabled == False",
          win._wake_lock.enabled is False, str(win._wake_lock.enabled))
    # 模拟串口在收（默认关）：仍不得 acquire
    win._wake_lock.set_receiving(True)
    app.processEvents()
    check("默认关时 set_receiving(True) 仍不调 OS", calls == [], str(calls))
    win._wake_lock.set_receiving(False)
    # 正向对照：打开后确实 acquire（用 fake，不碰真实电源策略）
    win._wake_lock.configure(True)
    win._wake_lock.set_receiving(True)
    check("正向对照: 开+收 → acquire 一次",
          calls == [wake_mod.ACQUIRE_FLAGS], str(calls))
    win._wake_lock.configure(False)
    check("关闭 → release", calls[-1] == wake_mod.RELEASE_FLAGS, str(calls))


def probe_marks(app, win):
    emit("\n=== [3] 标记：打标/列表/整轮高亮/循环/清屏/回放禁用/零 QSettings ===")
    from PySide6.QtGui import QShortcut
    from ui_highlight import stamp_span

    # 快捷键注册证据
    keys = []
    for sc in win.findChildren(QShortcut):
        try:
            keys.append(sc.key().toString())
        except Exception:
            pass
    emit(f"  已注册快捷键: {sorted(set(keys))}")
    check("Alt+M 已注册", "Alt+M" in keys, str(sorted(set(keys))))
    check("Alt+Down 已注册", "Alt+Down" in keys, str(sorted(set(keys))))

    # 造多轮文本（真实文档），滚到顶：视口短时 _viewed_block_number 回退到首块
    doc = win.txt_rx.document()
    win.txt_rx.setPlainText(mark_text(6))
    win.txt_rx.verticalScrollBar().setValue(0)
    app.processEvents()
    emit(f"  文档块数={doc.blockCount()}  首块={doc.findBlockByNumber(0).text()!r}")
    check("首块是时间戳行（轮锚点）", bool(stamp_span(doc.findBlockByNumber(0).text())))

    keys_before = iso_keys()
    mark_keys_before = [k for k in keys_before if "mark" in k.lower()]

    # Alt+M 打标（直接调快捷键连接的槽，等价于按键触发）
    win._mark_current()
    app.processEvents()
    emit(f"  marks={[m.key for m in win._marks.marks]}")
    check("打标后 MarkStore 有 1 条", len(win._marks.marks) == 1, str(len(win._marks.marks)))
    check("列表出现 1 个条目", win._mark_list.count() == 1,
          str(win._mark_list.count()))

    # 标记不写 QSettings
    keys_after1 = iso_keys()
    check("打标不写任何 QSettings 键", keys_after1 == keys_before,
          f"新增={set(keys_after1) - set(keys_before)}")

    # 再打两轮（滚动到不同位置）以便循环
    for val in (10, 22):
        win.txt_rx.verticalScrollBar().setValue(val)
        app.processEvents()
        win._mark_current()
        app.processEvents()
    emit(f"  marks(3)={[m.key for m in win._marks.marks]}")
    check("连打 3 标 → 列表 3 条", win._mark_list.count() == 3,
          str(win._mark_list.count()))

    # 跳转：跳到第 1 条，断言整轮高亮 + 视口落在轮首
    first = win._marks.marks[0]
    win._jump_to_mark(first.key)
    app.processEvents()
    check("跳转后 _mark_selection 非空", win._mark_selection is not None)
    extra = list(win.txt_rx.extraSelections())
    # ExtraSelection 在 PySide6 无值相等语义，按 cursor 的 anchor/position 比对
    msel = win._mark_selection
    same = [s for s in extra
            if s.cursor.anchor() == msel.cursor.anchor()
            and s.cursor.position() == msel.cursor.position()]
    check("整轮高亮进入 ExtraSelection（不框选文本）", len(same) == 1,
          f"extra={len(extra)} 匹配={len(same)}")
    check("不框选文本：真实 textCursor 无选区",
          not win.txt_rx.textCursor().hasSelection())
    if msel is not None:
        sel_text = msel.cursor.selectedText()
        emit(f"  高亮覆盖文本={sel_text!r}  first.stamp={first.stamp!r}")
        check("高亮覆盖整轮（含该轮时间戳行 + 数据行）",
              first.stamp in sel_text and "Voltage1:" in sel_text
              and "CVMax=" in sel_text, sel_text[:80])
        check("高亮背景色是 temp_warn + ALERT_FILL_ALPHA",
              msel.format.background().color().alpha() == 46,
              str(msel.format.background().color().alpha()))
        # 滚动位置应按 _visual_line_index(span0) - ALERT_TOP_LINES 计算，非写死
        span0 = win._round_span(first.start_block)
        check("跳转锚点块仍是该轮时间戳（未漂移）",
              span0 is not None
              and doc.findBlockByNumber(span0[0]).text() == first.stamp,
              str(span0))
        if span0 is not None:
            expected = max(0, win._visual_line_index(
                span0[0], win._single_line_height()) - 3)  # ALERT_TOP_LINES=3
            bar = win.txt_rx.verticalScrollBar()
            got = bar.value()
            emit(f"  滚动诊断: expected(raw)={expected} bar.maximum={bar.maximum()}"
                 f" got={got}  round首块={span0[0]}")
            # Qt 把 setValue 夹到 [0, maximum]：断言应含夹取，而非写死 raw 值
            check("视口滚到该轮首行（= visual_line_index-ALERT_TOP_LINES，含夹取）",
                  got == min(expected, bar.maximum()),
                  f"got={got} expected={expected} max={bar.maximum()}")
            # 真·验收：该轮首块必须落在视口可见范围（首块..末块之间）
            top = win._top_block_number()
            vh = win.txt_rx.viewport().height()
            blk = doc.findBlockByNumber(span0[0])
            y = win.txt_rx.blockBoundingGeometry(blk).translated(
                win.txt_rx.contentOffset()).y()
            visible = (top <= span0[0] and y <= vh)
            check("跳转后该轮首块在视口可见范围内", visible,
                  f"top块={top} 轮首块={span0[0]} y={y:.0f} viewportH={vh}")
    check("跳转后 _search_pinned=True（实时数据不甩走）",
          win._search_pinned is True, str(win._search_pinned))

    # Alt+Down 循环：从 current=2（最后 add）→ next→0 → next→1
    win._marks.current = 2
    win._goto_mark(1)
    cur1 = win._marks.current
    win._goto_mark(1)
    cur2 = win._marks.current
    emit(f"  Alt+Down 循环 current: 2 -> {cur1} -> {cur2}")
    check("Alt+Down 循环 2→0→1（模 3）", cur1 == 0 and cur2 == 1,
          f"{cur1},{cur2}")

    # 清屏：标记清零
    win._clear_receive()
    app.processEvents()
    check("清屏后 MarkStore 空", win._marks.marks == [], str(len(win._marks.marks)))
    check("清屏后列表空", win._mark_list.count() == 0,
          str(win._mark_list.count()))
    check("清屏后 _mark_selection=None", win._mark_selection is None)

    # 回放态：按钮禁用 + 不产新标
    win.txt_rx.setPlainText(mark_text(3))
    app.processEvents()
    win._replay_active = True
    win._refresh_replay_ui()
    app.processEvents()
    check("回放态 btn_mark 禁用", not win.btn_mark.isEnabled())
    check("回放态 _mark_list 禁用", not win._mark_list.isEnabled())
    tip = win.btn_mark.toolTip()
    check("回放态按钮 tooltip 说明原因", "回放" in tip, tip)
    n_before = len(win._marks.marks)
    win._mark_current()
    win._goto_mark(1)
    app.processEvents()
    check("回放态 Alt+M/Alt+Down 不产生新标记",
          len(win._marks.marks) == n_before,
          f"{n_before} -> {len(win._marks.marks)}")
    win._replay_active = False
    win._refresh_replay_ui()

    # 全程 mark 相关 QSettings 键为零
    keys_final = iso_keys()
    mark_keys_final = [k for k in keys_final if "mark" in k.lower()]
    check("全程无 mark 相关 QSettings 键",
          mark_keys_final == mark_keys_before,
          f"before={mark_keys_before} after={mark_keys_final}")


def main() -> int:
    emit("QA qa-v211 独立证伪探针 —— v2.11 真窗口交互层")
    RealQSettings = install_shim()
    emit(f"QSettings shim 已装；隔离 INI={ISO_INI}")

    real_before = snapshot(RealQSettings)
    emit(f"真实注册表快照: {len(real_before)} 键")

    app = None
    win = None
    try:
        from PySide6.QtWidgets import QApplication
        import wake_lock as wake_mod

        # 注入 fake OS setter：构造窗口/交互绝不改本机电源策略
        calls: list[int] = []
        orig = wake_mod.default_os_setter
        wake_mod.default_os_setter = calls.append

        app = QApplication.instance() or QApplication(sys.argv)
        from main_window import MainWindow
        win = MainWindow()
        win.resize(1600, 1000)
        win.show()
        app.processEvents()

        probe_voltage(app, win)
        probe_wake_default(app, win, wake_mod, calls)
        probe_marks(app, win)

        wake_mod.default_os_setter = orig
    finally:
        if win is not None:
            try:
                win.close()
            except Exception:
                pass
        if app is not None:
            app.processEvents()
            app.quit()

    # 真实注册表前后比对
    real_after = snapshot(RealQSettings)
    added = {k: real_after[k] for k in real_after if k not in real_before}
    removed = {k: real_before[k] for k in real_before if k not in real_after}
    changed = {k: (real_before[k], real_after[k]) for k in real_before
               if k in real_after and real_before[k] != real_after[k]}
    emit("\n=== [4] QSettings 零污染：真实注册表前后全量比对 ===")
    emit(f"  before={len(real_before)}  after={len(real_after)}")
    check("真实注册表无新增键", not added, str(added))
    check("真实注册表无删除键", not removed, str(removed))
    check("真实注册表无值改动", not changed, str(changed))

    emit("\n=== 汇总 ===")
    if _failures:
        emit(f"FAIL {len(_failures)} 项:")
        for f in _failures:
            emit(f"  - {f}")
        return 1
    emit("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
