"""把 SVG 按指定 viewBox 精确栅格化成 PNG（矢量渲染，非位图放大）。

用法：python _svg_to_png.py jobs.json
jobs.json = {"jobs": [{"svg": "<svg .../>", "out": "x.png", "width": 2020, "height": 790}, ...]}

必须用项目 venv（含 PySide6）；渲染走 QSvgRenderer + QPainter 抗锯齿，
离屏平台，不弹窗。
"""

from __future__ import annotations

import json
import os
import sys

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: _svg_to_png.py <jobs.json>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        jobs = json.load(fh)["jobs"]

    app = QGuiApplication(sys.argv)  # noqa: F841  QImage/QPainter 需要
    failed = 0
    for job in jobs:
        width = int(job["width"])
        height = int(job["height"])
        renderer = QSvgRenderer(QByteArray(job["svg"].encode("utf-8")))
        if not renderer.isValid():
            print("invalid svg: %s" % job["out"], file=sys.stderr)
            failed += 1
            continue
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        renderer.render(painter)
        painter.end()
        # 预乘 -> 直通 alpha，存 PNG 时避免边缘发暗
        out = image.convertToFormat(QImage.Format_ARGB32)
        os.makedirs(os.path.dirname(os.path.abspath(job["out"])), exist_ok=True)
        if not out.save(job["out"], "PNG"):
            print("save failed: %s" % job["out"], file=sys.stderr)
            failed += 1
        else:
            print("  rendered %-46s %dx%d" % (os.path.basename(job["out"]), width, height))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
