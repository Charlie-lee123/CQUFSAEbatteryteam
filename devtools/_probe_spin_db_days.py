# -*- coding: utf-8 -*-
"""量「近 N 天」数字框（spin_db_days）在不同最小宽度下能否完整显示「365 天」。

⛔ 不写任何配置、不开主窗口。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

import ui_theme  # noqa: E402
from ui_controls import ChevronSpinBox  # noqa: E402


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_theme.ui_font(12.5))
    ui_theme.apply_scheme("dark")

    spin = ChevronSpinBox()
    spin.setRange(1, 365)
    spin.setValue(7)
    spin.setSuffix(" 天")
    spin.show()
    for _ in range(20):
        app.processEvents()

    fm = spin.fontMetrics()
    print("[字体] point=%.2f family=%s" % (spin.font().pointSizeF(),
                                            spin.font().family()))
    for s in ("7 天", "365 天", "999 天"):
        print("  文本宽 %-8r = %d px" % (s, fm.horizontalAdvance(s)))
    print("[几何] sizeHint().width()=%d  minimumSizeHint().width()=%d"
          % (spin.sizeHint().width(), spin.minimumSizeHint().width()))
    print("[编辑框] _edit.w=%d margins=%s arrows=%s inset=%d"
          % (spin._edit.width(), (spin._edit.textMargins().left(),
             spin._edit.textMargins().top(), spin._edit.textMargins().right(),
             spin._edit.textMargins().bottom()),
             spin._arrows, ChevronSpinBox.ARROW_W))

    # 在各种宽度下，看实际渲染宽度 + 「365 天」是否完整可见
    for w in (64, 86, 100, 120, 130, 138):
        s = ChevronSpinBox()
        s.setRange(1, 365)
        s.setValue(365)
        s.setSuffix(" 天")
        s.setFixedWidth(w)
        s.show()
        app.processEvents()
        m = (s._edit.textMargins().left(), s._edit.textMargins().top(),
             s._edit.textMargins().right(), s._edit.textMargins().bottom())
        avail = s._edit.width() - m[0] - m[2]
        text_w = fm.horizontalAdvance("365 天")
        print("fix=%3d 实际w=%3d edit_w=%3d 可用=%3d 文本=%.0f %s"
              % (w, s.width(), s._edit.width(), avail, text_w,
                 "完整" if avail >= text_w else "← 截"))
    # 像素确认：动态公式给 365 天 → w=123，确认「365 天」最右墨迹不出编辑框
    for w in (122, 123, 124, 130):
        s = ChevronSpinBox()
        s.setRange(1, 365)
        s.setValue(365)
        s.setSuffix(" 天")
        s.setFixedWidth(w)
        s.show()
        app.processEvents()
        img = s.grab().toImage()
        ww, hh = img.width(), img.height()
        bg = img.pixelColor(int(ww * 0.5), 5)
        right_ink = 0
        for y in range(6, hh - 6):
            for x in range(ww - ChevronSpinBox.ARROW_W - 1, -1, -1):
                c = img.pixelColor(x, y)
                if abs(c.red() - bg.red()) + abs(c.green() - bg.green()) \
                        + abs(c.blue() - bg.blue()) > 60:
                    right_ink = max(right_ink, x)
                    break
        print("[像素] fix=%3d 最右墨迹 x=%d / 编辑框右界=%d (箭头 %d..%d) %s"
              % (w, right_ink, s._edit.width(),
                 ww - ChevronSpinBox.ARROW_W, ww,
                 "完整" if right_ink <= s._edit.width() - 1 else "← 出界"))


if __name__ == "__main__":
    main()
