# -*- coding: utf-8 -*-
"""探针：拨动开关的命中区几何（轨道圆角内才可点？行内留白不应响应）。

REVERSE 标记：只读，可在主工作区安全运行。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from ui_controls import Switch


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    sw = Switch("测试开关文字")
    sw.resize(sw.sizeHint())
    sw.show()
    app.processEvents()
    print(f"控件 {sw.width()}x{sw.height()}  轨道 {sw.TRACK_W}x{sw.TRACK_H}")

    track = sw._track_rect()
    mid_y = int(track.center().y())
    above_y = int(track.top() - 1)          # 轨道正上方的留白
    left_out = int(track.left() - 1)        # 轨道左侧外面（不存在，轨道贴左）
    text_y = mid_y
    text_x = int(sw.TRACK_W + sw.GAP_PX + 6)

    cases = [
        ("轨道中心", QPoint(int(sw.TRACK_W / 2), mid_y), True),
        ("轨道左端", QPoint(2, mid_y), True),
        ("轨道右端-1", QPoint(int(sw.TRACK_W) - 1, mid_y), True),
        ("轨道左上圆角外", QPoint(1, int(track.top())), False),
        ("轨道右上圆角外", QPoint(int(sw.TRACK_W) - 1, int(track.top())), False),
        ("轨道正上方留白", QPoint(int(sw.TRACK_W / 2), above_y), False),
        ("轨道正下方留白", QPoint(int(sw.TRACK_W / 2), int(track.bottom()) + 1), False),
        ("文字区", QPoint(text_x, text_y), True),
        ("控件最右端", QPoint(sw.width() - 2, mid_y), True),
    ]
    bad = 0
    for name, point, expect in cases:
        got = sw.hitButton(point)
        flag = "OK " if got == expect else "!! "
        if got != expect:
            bad += 1
        print(f"{flag}{name}: hitButton={got} 期望={expect}")
    print(f"\n{'全部符合' if not bad else str(bad) + ' 处不符合'}")


if __name__ == "__main__":
    main()
