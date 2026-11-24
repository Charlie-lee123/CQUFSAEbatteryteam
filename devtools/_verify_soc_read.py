"""只读探针：验证「SOC 数据来源」可选项（算出来的 / 读取到的）整条链路。

用户 2026-11-23 需求："SOC 显示来源可选" —— 底栏那组读数要么显示**本地估算**
（安时积分 + OCV 回拉 / 可选 EKF），要么显示**SOC 报文读取**的 ``SOC=`` 那一行。

链路（改动后）：
    serial_io 原始块
      → main_window._feed_soc
          → soc_feed.RoundCollector.feed()   ← 这里新解析 SOC= / SOCAdjusted=
              · ``last_soc_read``  粘滞状态（SOC= 行在 CVMax= **之后**，慢一轮）
              · ``RoundData.soc_read``     本轮自带（SOC 行先于 CVMax 时）
          → 同步 ``win._soc_read_last``（⛔ 必须在 _apply_soc 之前）
          → soc_est.SocEstimator.update()（照常跑，切回来才连续）
          → _apply_soc → _render_soc → 按 _soc_source 分流
                calc → 原 min / max / ± / 显示门控，一字不改
                read → 工况 + "SOC xx.xx%" + 空 + 总电压

本探针**只读**：不写任何 QSettings（快照后 finally 全量还原），不改产品源码。
收尾 ``app.quit()``，绝不 ``os._exit()``（否则 QSettings 缓冲与 Qt 清理都会丢）。

用法（在 SerialAssistant 目录下）：
    .venv\\Scripts\\python.exe devtools/_verify_soc_read.py
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

#: 历史会话库（按车号一本书，永不删除）
DB = (Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
      / "CQUFSAE-SerialAssistant" / "logs" / "历史数据" / "E74-2026.db")

#: 仓库里现成的一整块真实报文（含 Voltage1..5 + 温度 + BMS + CVMax + SOC 两行）
RAW_ROUND_TXT = Path(ROOT) / "_check" / "_raw_round.txt"

#: 探针自己造的两行（只用来验解析，不参与界面断言）
SOC_LINE_RE = re.compile(r"^\s*SOC\s*=\s*(-?\d+(?:\.\d+)?)", re.MULTILINE)
SOC_ADJ_LINE_RE = re.compile(r"^\s*SOCAdjusted\s*=\s*(-?\d+(?:\.\d+)?)", re.MULTILINE)

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    """打一行 PASS/FAIL 并记账（最后统一给退出码）。"""
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


# --------------------------------------------------------------- QSettings 快照

def snapshot_settings() -> dict:
    from PySide6.QtCore import QSettings
    s = QSettings("MimoWorks", "SerialAssistant")
    return {key: s.value(key) for key in s.allKeys()}


def restore_settings(data: dict) -> None:
    """把 QSettings 还原到快照：多余的键删掉、记过的写回（None 的键也删）。"""
    from PySide6.QtCore import QSettings
    s = QSettings("MimoWorks", "SerialAssistant")
    for key in list(s.allKeys()):
        if key not in data:
            s.remove(key)
    for key, value in data.items():
        if value is None:
            s.remove(key)          # 绝不能 setValue(key, None)：那是往注册表写 null
        else:
            s.setValue(key, value)
    s.sync()


# --------------------------------------------------------------- 真实报文取样

def read_db_block() -> tuple[str, str]:
    """从会话库取**一整轮**原文（电压 + 温度 + CVMax + SOC 两行）。

    报文里一轮分两帧：前一帧是 125 节的电压/温度，后一帧是 BMS/接触器/V-I/
    CVMax + 紧随其后的 ``SOC=`` / ``SOCAdjusted=``。所以取"带 SOC 的尾帧 + 它
    前一条帧"拼起来，才是一轮完整的输入。

    取**最新**的那一轮（``ORDER BY id DESC``）：最早的几条是台架早期的坏数据
    （BMS恒报 100.00%），拿它验解析可以，但验界面读数会让人误以为读数就是这
    副样子。
    """
    if not DB.exists():
        return "", "库文件不存在"
    uri = DB.as_uri() + "?mode=ro"          # 路径含中文 —— 必须走 as_uri + uri 模式
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    try:
        row = conn.execute(
            "SELECT id FROM frames WHERE raw LIKE '%SOC=%' AND raw LIKE '%CVMax=%'"
            " ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return "", "库里没有带 SOC= 的尾帧"
        tail_id = int(row[0])
        tail = conn.execute("SELECT raw FROM frames WHERE id=?",
                            (tail_id,)).fetchone()[0]
        head = conn.execute("SELECT id, raw FROM frames WHERE id<?"
                            " ORDER BY id DESC LIMIT 1", (tail_id,)).fetchone()
    finally:
        conn.close()
    if head is None:
        return "", f"尾帧 {tail_id} 前没有电压帧"
    return f"{head[1]}\n{tail}\n", f"frames {head[0]}+{tail_id}"


def read_txt_block() -> tuple[str, str]:
    """仓库里现成的一整块真实报文（没有库文件时的兜底取样源）。"""
    if not RAW_ROUND_TXT.exists():
        return "", "文件不存在"
    text = RAW_ROUND_TXT.read_text(encoding="utf-8", errors="replace")
    return text, str(RAW_ROUND_TXT)


def expected_pcts(block: str) -> tuple[float | None, float | None]:
    """从原文里解析出 ``(SOC, SOCAdjusted)`` —— 期望值不写死，按真数据走。"""
    def first(regex) -> float | None:
        m = regex.search(block or "")
        return float(m.group(1)) if m else None
    return first(SOC_LINE_RE), first(SOC_ADJ_LINE_RE)


# --------------------------------------------------------------- 采集层断言

def verify_collector(block: str, name: str) -> None:
    """解析层：粘滞值、SOCAdjusted 隔离、非法值丢弃、电量点数守恒。"""
    import soc_feed

    want_soc, want_adj = expected_pcts(block)
    print(f"\n=== 采集层（{name}）===")
    print(f"  原文声明：SOC={want_soc}  SOCAdjusted={want_adj}")
    if want_soc is None:
        check(f"{name}: 原文里有 SOC= 行", False, "取到的块里没有 SOC= 行")
        return

    collector = soc_feed.RoundCollector()
    rounds = collector.feed(block)
    print(f"  feed() 吐出 {len(rounds)} 整轮；"
          f"last_soc_read={collector.last_soc_read}  "
          f"last_soc_adj={collector.last_soc_adj}")
    for data in rounds:
        filled = sum(1 for c in data.cells if c is not None)
        print(f"    {data!r}  非空电芯={filled}")

    check(f"{name}: last_soc_read == {want_soc}",
          collector.last_soc_read is not None
          and abs(collector.last_soc_read - want_soc) < 1e-9,
          f"实际 {collector.last_soc_read}")
    check(f"{name}: 取的是 SOC= 而不是 SOCAdjusted=",
          collector.last_soc_read is None
          or want_adj is None
          or abs(collector.last_soc_read - want_adj) > 1e-9,
          f"SOC={collector.last_soc_read} / Adjusted={want_adj}")
    check(f"{name}: SOCAdjusted 只记录不上界面（last_soc_adj）",
          (want_adj is None and collector.last_soc_adj is None)
          or (collector.last_soc_adj is not None
              and abs(collector.last_soc_adj - want_adj) < 1e-9),
          f"实际 {collector.last_soc_adj}")

    # 电量数据不能被这两行污染：一整轮 125 点全在，且没有 80/79 mV 这种鬼值
    if rounds:
        data = rounds[-1]
        filled = [c for c in data.cells if c is not None]
        check(f"{name}: 电芯槽位数 = 125（点数守恒）", len(data.cells) == 125,
              f"实际 {len(data.cells)}")
        check(f"{name}: 收到的采样点 = 125（没被 SOC 行挤掉）",
              data.n_samples == 125 and len(filled) == 125,
              f"n_samples={data.n_samples} 非空={len(filled)}")
        check(f"{name}: 没有越界索引冒充电芯（80/79 mV 这类鬼值）",
              bool(filled) and min(filled) > 1000,
              f"最小电芯值={min(filled) if filled else None}")

    # 非法值/越界值必须丢弃（150% 那种明显不可能的读数）
    baseline = collector.last_soc_read
    collector.feed("SOC=150.00%\n")
    check(f"{name}: SOC=150.00% 被丢弃（last_soc_read 不变）",
          collector.last_soc_read == baseline,
          f"{baseline} -> {collector.last_soc_read}")
    collector.feed("SOC=-3%\n")
    check(f"{name}: SOC=-3% 被丢弃",
          collector.last_soc_read == baseline,
          f"{baseline} -> {collector.last_soc_read}")
    # reset() 必须清回 None（换车 / 换串口时不能带着旧车的BMS读数）
    collector.reset()
    check(f"{name}: reset() 清空粘滞状态",
          collector.last_soc_read is None and collector.last_soc_adj is None,
          f"soc={collector.last_soc_read} adj={collector.last_soc_adj}")


# --------------------------------------------------------------- 界面层断言

def soc_label_texts(win) -> tuple[str, str, str, str]:
    """底栏四枚 SOC 标签的**实际文本**（顺序：工况 / min 槽 / max 槽 / 总压槽）。"""
    return tuple(lbl.text() for lbl in win._soc_labels)


def check_geometry(win, title: str) -> None:
    """非空标签必须都在底栏矩形内、左右不重叠、从左到右顺序正确。"""
    bar = win._bottom_bar
    labels = win._soc_labels
    win._place_soc()
    shown = [(lbl.objectName(), lbl, lbl.geometry())
             for lbl in labels if lbl.text() and lbl.isVisibleTo(bar)]
    print(f"  [{title}] 参与摆位的标签 {len(shown)} 枚：")
    for name, _lbl, geo in shown:
        print(f"    {name:10s} x={geo.x():4d} w={geo.width():4d} "
              f"right={geo.right():4d}  text={_lbl.text()!r}")
    if not check(f"{title}: 有标签参与摆位（几何断言不是空跑）", len(shown) >= 3,
                 f"实际 {len(shown)} 枚"):
        return
    inside = all(0 <= geo.x() and geo.right() < bar.width() for _n, _l, geo in shown)
    check(f"{title}: 都在底栏矩形内", inside, f"底栏宽 {bar.width()}px")
    overlap = []
    for i in range(len(shown)):
        for j in range(i + 1, len(shown)):
            if shown[i][2].intersects(shown[j][2]):
                overlap.append(f"{shown[i][0]}/{shown[j][0]}")
    check(f"{title}: 互不重叠", not overlap, f"重叠={overlap}" if overlap else "")
    ordered = all(shown[i][2].left() < shown[i + 1][2].left()
                  for i in range(len(shown) - 1))
    check(f"{title}: 从左到右顺序正确", ordered,
          " -> ".join(name for name, _l, _g in shown))


def show_soc_labels(win) -> list[bool]:
    """把四枚 SOC 标签临时放出来（用户配置里可能关掉了「显示 SOC 读数」）。

    ⛔ 只改本进程里的可见性，**不写 QSettings**；返回值是原始可见性，收尾还原。
    为什么要放出来：``soc_show=False`` 时 ``_place_soc`` 会跳过全部标签，几何断言
    就成了空跑（"0 枚参与摆位也算 PASS"）—— 那正是探针最不该有的假绿。
    """
    saved = [lbl.isVisible() for lbl in win._soc_labels]
    for lbl in win._soc_labels:
        lbl.setVisible(True)
    return saved


def restore_soc_labels(win, saved: list[bool]) -> None:
    for lbl, visible in zip(win._soc_labels, saved):
        lbl.setVisible(visible)
    win._place_soc()


def verify_window(block: str) -> None:
    """界面层：两种来源下的文本 / 状态 / 几何，以及"切回来一字不差"。"""
    from PySide6.QtWidgets import QApplication

    import customization
    import soc_est
    import soc_feed
    import soc_profiles

    want_soc, _want_adj = expected_pcts(block)
    app = QApplication.instance() or QApplication(sys.argv)
    from main_window import MainWindow

    print("\n=== 界面层（真实 MainWindow）===")
    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    app.processEvents()
    print(f"  [env] 设置里的 soc_show={customization.get('soc_show')}"
          f"（探针会临时放出四枚标签，只为量几何；不改 QSettings）")
    labels_visible = show_soc_labels(win)

    # 这台机器上当前车可能没配 SOC 参数（那就没有估算链路）。探针不写设置，
    # 所以退一步：直接用 E74-2026 的车辆参数在本进程里搭一套（不改 QSettings）。
    if win._soc_est is None:
        cfg = soc_profiles.cfg_for("E74-2026")
        if cfg is None:
            check("E74-2026 车辆参数可用", False, "soc_profiles.cfg_for 返回 None")
            win.close()
            return
        per = max(1, cfg.cells_per_module)
        win._soc_cfg = cfg
        win._soc_est = soc_est.SocEstimator(cfg)
        win._soc_feed = soc_feed.RoundCollector(
            n_modules=max(1, cfg.n_cells // per), cells_per_module=per)
        print("  [env] 当前车无 SOC 参数 → 探针内临时用 E74-2026 参数搭链（不落盘）")
    else:
        print(f"  [env] 主窗口自带 SOC 链路（车辆={customization.current_vehicle()!r}）")

    # ---- 模式 A：算出来的（原有行为）
    win._set_soc_source("calc")
    win._feed_soc(block)
    app.processEvents()
    calc_parts = soc_label_texts(win)
    print(f"  [calc] 四枚标签: {calc_parts}")
    check("calc: min 槽是本地估算（SOC min …）",
          calc_parts[1].startswith("SOC min "), calc_parts[1])
    check("calc: max 槽是本地估算（max …）",
          calc_parts[2].startswith("max "), calc_parts[2])
    check("calc: 总压槽照旧", calc_parts[3].startswith("总电压 "), calc_parts[3])
    check_geometry(win, "calc")

    # ---- 模式 B：读取到的（BMS BMS）
    read_seen = win._soc_read_last
    check("读取链路: _feed_soc 已把 SOC 读数同步进窗口",
          read_seen is not None and want_soc is not None
          and abs(read_seen - want_soc) < 1e-9,
          f"_soc_read_last={read_seen} 期望={want_soc}")
    win._set_soc_source("read")
    app.processEvents()
    read_parts = soc_label_texts(win)
    print(f"  [read] 四枚标签: {read_parts}")
    check("read: min 槽是BMS读数",
          read_parts[1] == f"SOC {want_soc:.2f}%", read_parts[1])
    check("read: max 槽为空串（_place_soc 会跳过它）", read_parts[2] == "",
          repr(read_parts[2]))
    check("read: 总压槽照旧", read_parts[3].startswith("总电压 "), read_parts[3])
    check("read: 工况槽照旧（[放电]/[静置]/…）",
          read_parts[0].startswith("[") or read_parts[0] == "", read_parts[0])
    states = [lbl.property("state") for lbl in win._soc_labels]
    print(f"  [read] state 属性: {states}")
    check("read: 有读数时状态为 ok（不因本地健康度转橙）",
          states[1] == "ok", str(states))
    emph = [lbl.property("emphasis") for lbl in win._soc_labels]
    print(f"  [read] emphasis 属性: {emph}")
    check("read: 没有 min/max 焦点语义（min 槽不是 focus）",
          emph[1] != "focus", str(emph))
    check_geometry(win, "read")

    # ---- BMS从没报过 SOC：必须显示 "--"，不许拿本地估算顶上
    saved_read = win._soc_read_last
    win._soc_read_last = None
    win._refresh_soc_now()
    app.processEvents()
    empty_parts = soc_label_texts(win)
    print(f"  [read/无读数] 四枚标签: {empty_parts}")
    check("read: 无读数时显示 'SOC --'",
          empty_parts[1] == "SOC --", empty_parts[1])
    check("read: 无BMS读数时状态为 off",
          win._soc_labels[1].property("state") == "off",
          str(win._soc_labels[1].property("state")))
    check_geometry(win, "read/无读数")

    # ---- 一次都没算过（占位路径）也要按来源选措辞
    saved_calc = win._soc_calc_last
    win._soc_calc_last = None
    win._refresh_soc_now()
    app.processEvents()
    blank_parts = soc_label_texts(win)
    print(f"  [read/从未计算] 四枚标签: {blank_parts}")
    check("read: 还没算过时占位文本是 'SOC --'",
          blank_parts == ("", "SOC --", "", ""), str(blank_parts))
    win._soc_read_last = saved_read
    win._soc_calc_last = saved_calc          # ⛔ 必须还回来，否则切回 calc 是空的

    # ---- 切回算出来的：必须一字不差地恢复
    win._set_soc_source("calc")
    app.processEvents()
    back_parts = soc_label_texts(win)
    print(f"  [calc/切回] 四枚标签: {back_parts}")
    check("切回 calc: 文本与切换前完全一致", back_parts == calc_parts,
          f"{calc_parts} -> {back_parts}")
    check_geometry(win, "calc/切回")

    restore_soc_labels(win, labels_visible)
    win.close()


def main() -> int:
    print("SOC 数据来源（calc / read）只读验证")
    print(f"  DB          = {DB}  exists={DB.exists()}")
    print(f"  raw_round   = {RAW_ROUND_TXT}  exists={RAW_ROUND_TXT.exists()}")

    before = snapshot_settings()
    try:
        block, src = read_db_block()
        if not block:
            block, src = read_txt_block()
        print(f"  取样来源    = {src}  长度={len(block)} 字符")
        if not block:
            check("取到真实报文块", False, "库与 txt 都取不到")
            return 2
        verify_collector(block, "会话库真实块")
        txt_block, txt_src = read_txt_block()
        if txt_block and txt_block != block:
            print(f"  （附带）另一份取样 = {txt_src}")
            verify_collector(txt_block, "_check/_raw_round.txt")
        verify_window(block)
    finally:
        restore_settings(before)
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
            app.quit()

    print("\n=== 汇总 ===")
    if _failures:
        print(f"FAIL {len(_failures)} 项: {_failures}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
