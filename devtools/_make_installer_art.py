# -*- coding: utf-8 -*-
r"""生成安装向导要用的位图（和主程序**同一套配色 + 同一份队标**）。

Inno Setup 的向导图只吃 BMP，所以这里用 Qt 现画：

  · 右上角小图 55x55 + 110x110 —— 用应用图标（该有 logo 的地方就放 logo）
  · 明（on_light）/ 暗（on_dark）两套，交给 Inno 按系统主题自动切换
  （modern 样式没有左侧大图位，wizard_* 大图已删；换 classic 样式时再加回来）

⚠️ 配色**直接读 ui_theme 的 token**，不在这里另抄一份色值 —— 主程序改了主题，
   重跑本脚本，安装向导跟着一起变。

用法：
    python devtools\_make_installer_art.py
输出：
    installer\art\wizard_{dark,light}{,@2x}.bmp
    installer\art\small_{dark,light}{,@2x}.bmp
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ⚠️ 必须用 windows 平台渲染：offscreen 平台在本机**一个字体都没有**
#    （QFontDatabase.families() 返回空），中文会全渲染成方框并被烤进位图里。
#    QImage 的绘制不需要可见窗口，所以用真平台也没关系。
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
from PySide6.QtGui import QColor, QImage, QPainter                 # noqa: E402
from PySide6.QtWidgets import QApplication                 # noqa: E402

import ui_brand                                            # noqa: E402
import ui_theme                                            # noqa: E402

ART_DIR = os.path.join(ROOT, "installer", "art")
SMALL = 55                             # 右上角小图（100%）


def palette(scheme: str) -> dict:
    ui_theme.apply_scheme(scheme)
    return {
        "bg": ui_theme.qcolor("bg"),
        "ink": ui_theme.qcolor("ink"),
        "ink_3": ui_theme.qcolor("ink_3"),
        "line": ui_theme.qcolor("line"),
    }


def make_small(scheme: str, scale: int) -> str:
    """右上角小图：应用图标（透明底，交给向导自己铺底色）。"""
    size = SMALL * scale
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    icon = ui_brand.app_icon()
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    icon.paint(painter, 0, 0, size, size)
    painter.end()
    path = os.path.join(ART_DIR, f"small_{scheme}{'@2x' if scale > 1 else ''}.bmp")
    image.save(path, "BMP")
    return path


def main() -> int:
    os.makedirs(ART_DIR, exist_ok=True)
    app = QApplication(sys.argv)          # noqa: F841  QApplication 是画图的前置条件
    made = []
    for scheme in ("dark", "light"):
        made.append(make_small(scheme, 1))
        made.append(make_small(scheme, 2))
    for path in made:
        print(f"  {os.path.relpath(path, ROOT)}  {os.path.getsize(path):,} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
