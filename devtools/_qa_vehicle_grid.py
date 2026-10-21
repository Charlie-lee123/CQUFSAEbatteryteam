# -*- coding: utf-8 -*-
r"""QA 独立探针：逐模组网格在 **1600×952** 视口下的落地效果（只读验收用）。

与 devtools/_probe_vehicle_grid.py 的区别：
  · 窗口固定 1600×952（lead 指定的验收尺寸，比用户默认 1180 宽 —— 更容易出现
    "视口够宽、单模组一排装得下"与"装不下要拆"两种分支，能同时验）；
  · 断言而非打印：每视觉行 token 数 ≤ 视口容量、合计一点不丢；
  · 额外做**新旧代码逐行等价**：把 HEAD 版 ui_highlight.py 取到内存里，与当前
    工作区版本在"没选车"路径上逐字比较（这是"旧行为完全不变"最硬的证据）。

用法：QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_qa_vehicle_grid.py
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                              # noqa: E402
from PySide6.QtWidgets import QApplication                        # noqa: E402

WIN_W, WIN_H = 1600, 952
CELLS = [25, 24, 25]
TEMPS = [8, 5, 6]
FAILS: list[str] = []


def pump(app, ms: int) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.003)


def volt_points(count: int) -> str:
    return " ".join(f"{i}.3700" for i in range(1, count + 1))


def temp_points(count: int) -> str:
    return " ".join(f"{i}.25" for i in range(1, count + 1))


def load_old_highlight():
    """把 HEAD 版 ui_highlight.py 载入成独立模块（用于新旧逐字等价比较）。"""
    path = os.path.join(_ROOT, "_check", "_qa_old_ui_highlight.py")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    blob = subprocess.run(["git", "show", "HEAD:ui_highlight.py"], cwd=_ROOT,
                          capture_output=True, check=True).stdout
    with open(path, "wb") as handle:
        handle.write(blob)
    spec = importlib.util.spec_from_file_location("_qa_old_ui_highlight", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rendered_rows(window) -> list[str]:
    document = window.txt_rx.document()
    return [document.findBlockByNumber(i).text() for i in range(document.blockCount())]


def feed(window, app, payload: str) -> None:
    window._clear_receive()
    window._on_received(payload.encode("utf-8"))
    window._flush_rx()
    pump(app, 450)


def check_group(window, app, tag: str, payload: str, expect_total: int,
                fit: int, max_chars: int) -> list[int]:
    from ui_highlight import SAMPLE_RE
    feed(window, app, payload)
    rows = [line for line in rendered_rows(window) if SAMPLE_RE.search(line)]
    counts = [len(SAMPLE_RE.findall(line)) for line in rows]
    longest = max((len(line) for line in rows), default=0)
    print(f"\n[{tag}] 数据排 {len(rows)} 行，每排点数 = {counts}")
    print(f"        最长行 {longest} 字符 / 视口容量 {max_chars} 字符 / fit {fit} 格")
    for line in rows:
        print("        | " + (line if len(line) <= 100 else line[:100] + " …"))
    if sum(counts) != expect_total:
        FAILS.append(f"{tag}: 合计 {sum(counts)} != {expect_total}（丢点或多点）")
    over = [c for c in counts if fit and c > fit]
    if over:
        FAILS.append(f"{tag}: 有 {len(over)} 排超过视口容量 {fit} 格：{over}")
    if max_chars and longest > max_chars:
        FAILS.append(f"{tag}: 最长行 {longest} 字符 > 视口容量 {max_chars}")
    # 必须是"连续整段"：每排都在上一排正下方，没有中间夹一行空数据行
    return counts


def legacy_equivalence(old, new) -> None:
    print("\n=== 新旧等价（没选车路径，HEAD vs 工作区）===")
    cases: list[tuple[str, dict]] = []
    for count in (0, 1, 7, 24, 25, 40, 74, 75, 90, 128):
        text = "Voltage1:\n" + volt_points(count) if count else "Voltage1:"
        for chars in (0, 60, 97, 120, 180, 230, 400):
            for rows_hint, volt_hint, temp_hint in ((0, 0, 0), (3, 25, 8),
                                                   (5, 0, 0), (0, 24, 0),
                                                   (7, 25, 8), (2, 32, 16)):
                cases.append((text, {"max_row_chars": chars,
                                     "rows_hint": rows_hint,
                                     "volt_cols_hint": volt_hint,
                                     "temp_cols_hint": temp_hint}))
    temps = "Temperature1:\n" + temp_points(19)
    for chars in (0, 120, 230):
        cases.append((temps, {"max_row_chars": chars, "rows_hint": 3,
                              "temp_cols_hint": 8}))
    diff = 0
    first = None
    for text, kwargs in cases:
        a = old.align_indexes(text, **kwargs)
        b = new.align_indexes(text, **kwargs)
        if a != b:
            diff += 1
            if first is None:
                first = (text[:40], kwargs, a, b)
    print(f"  比较 {len(cases)} 组（文本 × 视口宽度 × 三件套组合）→ 不一致 {diff} 组")
    if diff:
        FAILS.append(f"没选车路径有 {diff}/{len(cases)} 组与 HEAD 不一致")
        text, kwargs, a, b = first
        print("  首个差异：", text, kwargs)
        print("   HEAD:", a[:160].replace("\n", "\\n"))
        print("   现在:", b[:160].replace("\n", "\\n"))

    # grid_row_sizes 本体：全参数网格
    bad = []
    for count in range(0, 130):
        for rows in (0, 1, 2, 3, 5, 8):
            for cols in (0, 1, 5, 24, 25, 32):
                if old.grid_row_sizes(count, rows, cols) != \
                        new.grid_row_sizes(count, rows, cols):
                    bad.append((count, rows, cols))
    print(f"  grid_row_sizes(count,rows,cols) 全网格（0..129 × rows × cols）"
          f" → 不一致 {len(bad)} 组")
    if bad:
        FAILS.append(f"grid_row_sizes 有 {len(bad)} 组变了：{bad[:5]}")


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, ui_font
    apply_scheme("dark")
    app.setFont(ui_font(12.5))

    import customization
    import ui_highlight as new_mod
    from selftest import restore_settings

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    window = None
    try:
        old_mod = load_old_highlight()
        legacy_equivalence(old_mod, new_mod)

        # ---- 造车 / 选车 ----
        for name in customization.vehicle_names():
            customization.delete_vehicle(name)
        customization.save_vehicle("QA-25/24/25", CELLS, TEMPS)
        customization.select_vehicle("QA-25/24/25")
        print("\n=== 车辆档案 ===")
        print("  current =", customization.current_vehicle(),
              " profile =", customization.active_profile())

        from main_window import MainWindow
        window = MainWindow()
        window._port_watch.stop()
        window.setGeometry(0, 0, WIN_W, WIN_H)
        window.show()
        window.apply_startup_state()
        pump(app, 400)
        # 启动状态是**整窗全屏**（用户配置 startup_maximized=true，且 180/600ms
        # 还有两次 _verify_fullscreen 会把人钉回全屏）→ 先明确退出，再钉尺寸
        window._want_fullscreen = False
        window._expect_normal = True
        window.showNormal()
        window.setGeometry(0, 0, WIN_W, WIN_H)
        pump(app, 800)
        print("\n=== 窗口 ===")
        print(f"  size = {window.width()}x{window.height()}")
        max_chars = window._rx_max_row_chars()
        print("  视口一行字符容量 =", max_chars)
        hints = window._rx_grid_hints()
        print("  _rx_grid_hints() =", hints)
        if hints.get("volt_row_sizes") != CELLS:
            FAILS.append(f"选了车但 _rx_grid_hints 没给逐模组尺寸：{hints}")

        cell_w = len("01.3700")
        gap = len(new_mod.CELL_GAP)
        fit = max(1, (max_chars + gap) // (cell_w + gap)) if max_chars else 0
        print(f"  cell_w={cell_w} gap={gap} → fit={fit} 格/排")

        check_group(window, app, "74 点 / [25,24,25]", "Voltage1:\n" + volt_points(74),
                    74, fit, max_chars)
        check_group(window, app, "90 点 / [25,24,25]", "Voltage1:\n" + volt_points(90),
                    90, fit, max_chars)
        check_group(window, app, "40 点 / [25,24,25]", "Voltage1:\n" + volt_points(40),
                    40, fit, max_chars)
        check_group(window, app, "温度 19 点 / [8,5,6]", "Temperature1:\n" + temp_points(19),
                    19, 0, max_chars)

        # ---- 没选车：接收区实际渲染必须与 HEAD 版逐字一致 ----
        customization.select_vehicle("")
        pump(app, 200)
        hints2 = window._rx_grid_hints()
        print("\n=== 取消选择后 ===")
        print("  _rx_grid_hints() =", hints2)
        if set(hints2) != {"rows_hint", "volt_cols_hint", "temp_cols_hint"}:
            FAILS.append(f"没选车时不该带逐模组键：{hints2}")
        from ui_highlight import STAMP_RE
        for count in (74, 90, 40):
            payload = "Voltage1:\n" + volt_points(count)
            feed(window, app, payload)
            # 时间戳是主窗口加的前缀（独立成块），不在 align_indexes 的职责里 →
            # 去掉前缀与空块再比
            live_lines = [STAMP_RE.sub("", line).lstrip()
                          for line in rendered_rows(window)]
            live = "\n".join(line for line in live_lines if line.strip())
            old_text = old_mod.align_indexes(
                payload, max_row_chars=window._rx_max_row_chars(),
                rows_hint=hints2["rows_hint"],
                volt_cols_hint=hints2["volt_cols_hint"],
                temp_cols_hint=hints2["temp_cols_hint"])
            same = live == old_text
            print(f"  {count} 点：实际渲染 vs HEAD align_indexes 全文一致 = {same}")
            if not same:
                FAILS.append(f"没选车 {count} 点：渲染结果与 HEAD 不一致")
                for a, b in zip(old_text.split("\n"), live.split("\n")):
                    if a != b:
                        print("    HEAD:", a[:120])
                        print("    现在:", b[:120])
                        break
                print(f"    HEAD 行数 {len(old_text.splitlines())} / "
                      f"实际 {len(live.splitlines())}")
            counts = [len(new_mod.SAMPLE_RE.findall(line))
                      for line in live.split("\n") if new_mod.SAMPLE_RE.search(line)]
            print(f"    每排点数 = {counts}  合计 {sum(counts)}")
            if sum(counts) != count:
                FAILS.append(f"没选车 {count} 点：合计 {sum(counts)} 丢点")
    finally:
        if window is not None:
            window.setGeometry(0, 0, 1134, 820)
            window.close()
            app.processEvents()
        pump(app, 150)
        restore_settings(settings, snapshot)
        settings.sync()

    print("\n================ QA 判定 ================")
    if FAILS:
        for item in FAILS:
            print("FAIL  " + item)
        return 1
    print("PASS  逐模组 74/90/40 点均不丢不溢；没选车路径与 HEAD 逐字一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
