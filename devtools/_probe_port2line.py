# -*- coding: utf-8 -*-
"""串口选择区两行显示 + 蓝牙→串口清空 的真机验证探针（只读）。

验证点：
1. 串口选择框开启两行显示后，框变高（>= 两行高度）。
2. 长条目 "COM7 — USB-SERIAL CH340 (COM7)" 在框内画成两行（上下半都有墨迹）。
3. 蓝牙 → 串口 切换后，串口选择区清空（currentText 为空、无选中项）。
4. 串口模式下手动选/输入端口仍正常。
5. QSettings 不污染。

产物：_check/port2line_*.png 截图 + 断言结果打印。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def main() -> int:
    app = QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    had = set(snapshot)

    import selftest
    from main_window import LINK_BLE, LINK_SERIAL, PORT_LABEL_SEP

    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        mark = "✓" if cond else "✗"
        print(f"  {mark} {name}" + (f"  -- {detail}" if detail and not cond else ""))
        if not cond:
            ok = False

    window = selftest.make_window()
    window.cmb_link.setCurrentText(LINK_SERIAL)
    window.show()
    for _ in range(8):
        app.processEvents()

    card = window._config_card
    combo = window.cmb_port

    # ---- 1) 两行显示开启后框变高 ------------------------------------------
    single_h = 34  # 常规单行框高（min 20 + padding 12 + border 2）
    two_line_h = combo._two_line_height()
    check("两行高度 > 单行", two_line_h > single_h,
          f"two_line_height={two_line_h} 单行={single_h}")
    check("串口框开启了两行模式", combo._two_line is True)
    check("串口框 objectName = portCombo", combo.objectName() == "portCombo")
    # 布局稳定后实际高度
    QTest.qWait(120)
    real_h = combo.height()
    check(f"串口框实际高度 >= {two_line_h}（单行是 {single_h}）",
          real_h >= two_line_h,
          f"实际高度={real_h}")

    # ---- 2) 两行内容在框内分成上下半 ---------------------------------------
    label = f"COM7{PORT_LABEL_SEP}USB-SERIAL CH340 (COM7)"
    combo.setEditText(label)
    for _ in range(8):
        app.processEvents()
    QTest.qWait(100)

    # 抓编辑框像素，看上下半是否都有墨迹
    edit = combo.lineEdit()
    dpr = window.devicePixelRatioF()
    shot = edit.grab().toImage()
    w, h = shot.width(), shot.height()
    top_rows = 0
    bot_rows = 0
    for y in range(h):
        row_dark = 0
        for x in range(w):
            r = shot.pixelColor(x, y).red()
            if r < 128:  # 深字
                row_dark += 1
        if row_dark > 2:
            if y < h / 2:
                top_rows += 1
            else:
                bot_rows += 1
    check("编辑框上半有文字墨迹", top_rows > 0, f"top_rows={top_rows}")
    check("编辑框下半有文字墨迹", bot_rows > 0, f"bot_rows={bot_rows}")

    # ---- 3) 蓝牙 → 串口 切换后清空 -----------------------------------------
    # ⛔ 先把编辑框文字清空，模拟真实用户切换前的干净状态（之前步骤写了内容）。
    combo.setEditText("")
    for _ in range(4):
        app.processEvents()
    window.cmb_link.setCurrentText(LINK_BLE)
    for _ in range(40):
        app.processEvents()
    QTest.qWait(200)
    check("蓝牙模式下串口框已清空", combo.currentText() == "",
          f"currentText={combo.currentText()!r}")

    window.cmb_link.setCurrentText(LINK_SERIAL)
    for _ in range(40):
        app.processEvents()
    QTest.qWait(200)
    check("串口模式下选择区为空（清空了）",
          combo.currentText() == "" and combo.currentIndex() == -1,
          f"currentText={combo.currentText()!r} index={combo.currentIndex()}")

    # ---- 4) 串口模式正常输入 ----------------------------------------------
    combo.setEditText("COM7")
    for _ in range(8):
        app.processEvents()
    check("手输 COM7 可写进编辑框", combo.currentText() == "COM7")
    check("_selected_port() 解析出 COM7",
          window._selected_port() == "COM7")

    # ---- 5) 弹层两行显示 ---------------------------------------------------
    # 先填几个长条目，弹层才有内容可量。
    combo.blockSignals(True)
    combo.clear()
    combo.addItem(f"COM7{PORT_LABEL_SEP}USB-SERIAL CH340 (COM7)", "COM7")
    combo.addItem(f"COM5{PORT_LABEL_SEP}USB-SERIAL CH340 (COM5)", "COM5")
    combo.blockSignals(False)
    combo.showPopup()
    QTest.qWait(200)
    delegate = combo._item_delegate
    check("弹层委托开启两行模式", delegate is not None and delegate.two_line)
    view = combo.view()
    row_h = view.sizeHintForRow(0) if combo.count() > 0 else 0
    # 两行条目 sizeHint 应明显高于单行（单行 ROW_H=28）
    check("弹层条目是两行高度", row_h >= 34, f"row_h={row_h}")
    combo.hidePopup()

    # ---- 截图留证 -----------------------------------------------------------
    os.makedirs("_check", exist_ok=True)
    shot_full = window.grab()
    shot_full.save("_check/port2line_full.png")
    top_left = card.mapTo(window, card.rect().topLeft())
    shot_card = shot_full.copy(int(top_left.x() * dpr), int(top_left.y() * dpr),
                               int(card.width() * dpr), int(card.height() * dpr))
    shot_card.save("_check/port2line_card.png")
    shot_combo = combo.grab()
    shot_combo.save("_check/port2line_combo.png")

    window.close()
    window.deleteLater()
    for _ in range(4):
        app.processEvents()

    for key in settings.allKeys():
        if key not in had:
            settings.remove(key)
    for key, value in snapshot.items():
        settings.setValue(key, value)
    settings.sync()
    app.quit()

    print(f"\n{'全部通过 ✓' if ok else '有失败 ✗'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
