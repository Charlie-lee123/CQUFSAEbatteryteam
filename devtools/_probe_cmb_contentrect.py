# -*- coding: utf-8 -*-
"""用 QStyle.subControlRect 精确量 cmb_history 的**文字内容区**宽度，
算出"完整显示最长条目"所需的最小框宽与 chrome（在默认字体与真实 12.5pt 各量一次）。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QStyle, QStyleOptionComboBox)
import ui_theme  # noqa: E402
import rx_history  # noqa: E402
import rx_store  # noqa: E402
import app_log  # noqa: E402
import ui_settings  # noqa: E402


def probe(tag, real_font):
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    if real_font:
        app.setFont(ui_theme.ui_font(12.5))
    ui_theme.apply_scheme("dark")
    if not app_log.path():
        app_log.init()
    rx_history.load(None)
    rx_store.set_db_path(os.path.join(ROOT, "_check", "_probe_not_a_real.db"))
    dialog = ui_settings.SettingsDialog("dark", 55)
    dialog.resize(dialog.minimumWidth(), 700)
    dialog.show()
    for _ in range(30):
        app.processEvents()
    c = dialog.cmb_history
    fm = c.fontMetrics()
    # 最长条目
    texts = [c.itemText(i) for i in range(c.count())]
    longest = max(texts, key=lambda t: fm.horizontalAdvance(t)) if texts else ""
    tw = fm.horizontalAdvance(longest)
    # 当前宽度下的文字内容区（SC_ComboBoxEditField）
    opt = QStyleOptionComboBox()
    opt.initFrom(c)
    opt.currentText = c.currentText()
    cr = c.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, opt,
                                  QStyle.SubControl.SC_ComboBoxEditField, c)
    chrome = c.width() - cr.width()
    print("[%s font=%dpx] w=%d 最长=%r 文字=%dpx 内容区=%dpx chrome=%d"
          % (tag, fm.height(), c.width(), longest, tw, cr.width(), chrome))
    print("   → 完整显示需最小宽 = %d（文字 %d + chrome %d）"
          % (tw + chrome, tw, chrome))
    print("   → 该宽下 空(w-text) = %d" % chrome)
    dialog.close()
    dialog.deleteLater()
    for _ in range(5):
        app.processEvents()


probe("默认", False)
probe("真实12.5", True)
