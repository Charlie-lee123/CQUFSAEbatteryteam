# -*- coding: utf-8 -*-
r"""验证「无论接收时有没有勾选显示时间戳，回看历史都要有时间戳」。

用户 2026-11-11 原话：「无论接受的时候有没有选择显示时间戳在回看历史数据
的时候都要显示时间戳」。

两层实现分别验：
  A. **今后**：``_emit_block`` 把落盘文本与屏上文本拆开，落盘恒带真时间戳；
  B. **老文件**：根本没生成过时间戳的历史，回看时补显式占位 ``[--:--:--]``，
     ⛔ 绝不拿文件 mtime / 当前时间伪造。

⛔ 会构造 MainWindow：开窗前对 QSettings 全量快照，finally 删新键 + 逐键写回
   + sync()，并用前后比对证明零污染（2026-11-10 踩过覆盖 geometry 的坑）。

跑法：``.venv/Scripts/python.exe devtools/_verify_history_stamp.py``
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name
          + (("  —— " + detail) if detail else ""))


def main() -> int:
    import customization
    import main_window
    import rx_history

    app = QApplication.instance() or QApplication(sys.argv)
    qsettings = QSettings("MimoWorks", "SerialAssistant")
    before = {k: qsettings.value(k) for k in qsettings.allKeys()}

    tmp = ROOT / "_check" / "verify_history_stamp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    real_dir = rx_history._resolve_dir
    real_vehicle = customization.current_vehicle
    rx_history._resolve_dir = lambda: tmp
    customization.current_vehicle = lambda: "E74-2026"

    stamp_line = lambda rows: [  # noqa: E731
        str(x).strip() for x in rows
        if str(x).strip().startswith("[") and str(x).strip().endswith("]")
        and any(c.isdigit() for c in str(x))]

    try:
        win = main_window.MainWindow()
        win.show()
        app.processEvents()

        frame = ("Voltage1:\n01.3200   02.3201   03.3202\n"
                 "Temperature1:\n01.40   02.40\n"
                 "BMS OK\nV=0.00,I=1.89\nCVMax=3202, Min=3200")

        # ---- A1 关闭「显示时间戳」：屏上无戳，历史必须有真戳 ----------
        win.chk_timestamp.setChecked(False)
        win._clear_receive()
        rx_history._lines.clear()
        rx_history._pending.clear()
        win._emit_block(frame)
        app.processEvents()
        rows = rx_history.all_lines()
        shown = win.txt_rx.toPlainText()
        check("A1 关闭开关时屏上不带时间戳",
              "[" not in shown, repr(shown[:60]))
        check("A2 关闭开关时历史仍记录真时间戳",
              len(stamp_line(rows)) >= 1, repr(rows[:4]))

        # ---- A3 打开开关：屏上和历史都带戳 ----------
        win.chk_timestamp.setChecked(True)
        win._clear_receive()
        rx_history._lines.clear()
        rx_history._pending.clear()
        win._emit_block(frame)
        app.processEvents()
        rows_on = rx_history.all_lines()
        check("A3 打开开关时历史带戳",
              len(stamp_line(rows_on)) >= 1, repr(rows_on[:4]))
        check("A4 打开开关时屏上带戳",
              "[" in win.txt_rx.toPlainText(), repr(win.txt_rx.toPlainText()[:60]))

        # ---- B 老文件（完全没有时间戳）回看时补显式占位 ----------
        legacy = ["Voltage1:", "01.3200   02.3201", "Temperature1:", "01.40",
                  "CVMax=3201, Min=3200", "", "Voltage1:", "01.3200"]
        filled_rows, n = win._rows_with_stamps(legacy)
        check("B1 老文件按轮首补占位时间戳", n == 2,
              f"补了 {n} 个；前 4 行 {filled_rows[:4]}")
        check("B2 占位是显式的 [--:--:--]，不是伪造时刻",
              filled_rows.count("[--:--:--]") == 2, repr(filled_rows[:2]))
        check("B3 补位不改动原有数据行",
              [x for x in filled_rows if x != "[--:--:--]"] == legacy, "")

        modern = ["[00:10:27]", "Voltage1:", "01.3200", "",
                  "[00:10:29]", "Voltage1:", "01.3201"]
        same, n2 = win._rows_with_stamps(modern)
        check("B4 已有真戳的文件原样返回", same == modern and n2 == 0,
              f"n={n2}")

        # B5 占位本身不含数字，不会被误判成"已有时间戳"
        _, n3 = win._rows_with_stamps(["[--:--:--]", "Voltage1:", "01.3200"])
        check("B5 占位不会被当成真戳", n3 == 1, f"n={n3}")

        win.close()
        win.deleteLater()
        app.processEvents()
    finally:
        rx_history._resolve_dir = real_dir
        customization.current_vehicle = real_vehicle
        for key in list(qsettings.allKeys()):
            if key not in before:
                qsettings.remove(key)
        for key, value in before.items():
            qsettings.setValue(key, value)
        qsettings.sync()

    after = {k: qsettings.value(k) for k in qsettings.allKeys()}
    check("C1 用户 QSettings 零污染", before == after,
          f"before={len(before)} after={len(after)}")

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    print(f"\n{passed}/{len(_RESULTS)} 项通过")
    print("IS_PASS:", "YES" if passed == len(_RESULTS) else "NO")
    return 0 if passed == len(_RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
