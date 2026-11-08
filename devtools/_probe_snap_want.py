"""验证 _apply_snap_zoom 的分屏判定「want」在真实 screen() 几何下是否命中。

复刻 main_window._apply_snap_zoom 里的判定公式（2620 行附近），在 primary
屏幕和所有附加屏幕上，逐一模拟 Windows / Win11 的所有 Snap 标准布局几何，
打印每个布局的 want 结果。

目的：先把「判定公式本身会不会漏判」这件事坐实。如果连标准 Snap 布局都
返回 want=False，那就是公式 bug；否则问题在状态标志（_want_fullscreen /
_expect_normal）或调用链，需要另外修。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import QRect, Qt

# ---- 复刻 main_window 里的判定常量与公式 ----
COMPACT_L2_W = 720


def compute_want(g: QRect, scr: QRect) -> dict:
    """逐字复刻 main_window._apply_snap_zoom 的 want 计算。"""
    flush_x = g.x() <= scr.x() + 8 or g.right() >= scr.right() - 8
    flush_y = g.y() <= scr.y() + 8 or g.bottom() >= scr.bottom() - 8
    rw = g.width() / scr.width()
    rh = g.height() / scr.height()
    std_w = any(abs(rw - f) <= 0.025
                for f in (0.25, 1 / 3, 0.5, 2 / 3, 0.75, 1.0))
    std_h = any(abs(rh - f) <= 0.025 for f in (0.5, 1.0))
    full = rw >= 0.97 and rh >= 0.97
    want = flush_x and flush_y and std_w and std_h and not full
    return dict(flush_x=flush_x, flush_y=flush_y, rw=round(rw, 4), rh=round(rh, 4),
                std_w=std_w, std_h=std_h, full=full, want=want)


def snap_layouts(scr: QRect) -> list:
    """生成所有 Windows / Win11 标准 Snap 布局的几何（贴工作区边/角）。"""
    x0, y0, W, H = scr.x(), scr.y(), scr.width(), scr.height()
    return [
        ("左1/2",   QRect(x0, y0, W // 2, H)),
        ("右1/2",   QRect(x0 + W - W // 2, y0, W // 2, H)),
        ("上1/2",   QRect(x0, y0, W, H // 2)),
        ("下1/2",   QRect(x0, y0 + H - H // 2, W, H // 2)),
        ("左上1/4", QRect(x0, y0, W // 2, H // 2)),
        ("右上1/4", QRect(x0 + W - W // 2, y0, W // 2, H // 2)),
        ("左下1/4", QRect(x0, y0 + H - H // 2, W // 2, H // 2)),
        ("右下1/4", QRect(x0 + W - W // 2, y0 + H - H // 2, W // 2, H // 2)),
        ("左1/3",   QRect(x0, y0, W // 3, H)),
        ("右2/3",   QRect(x0 + W - (W * 2) // 3, y0, (W * 2) // 3, H)),
        ("左2/3",   QRect(x0, y0, (W * 2) // 3, H)),
        ("右1/3",   QRect(x0 + W - W // 3, y0, W // 3, H)),
        # Win11 三栏组合：主 2/3 全高 + 右上 1/3 + 右下 1/3
        ("右上1/3", QRect(x0 + (W * 2) // 3, y0, W // 3, H // 2)),
        ("右下1/3", QRect(x0 + (W * 2) // 3, y0 + H // 2, W // 3, H // 2)),
    ]


def main() -> int:
    app = QApplication(sys.argv)
    win = QMainWindow()
    win.show()
    win.hide()
    print("=" * 72)
    for si, scr in enumerate(app.screens()):
        g = scr.geometry()
        av = scr.availableGeometry()
        print(f"屏幕#{si}  geometry={g.width()}x{g.height()}@({g.x()},{g.y()})"
              f"  available={av.width()}x{av.height()}@{av.x(),av.y()}"
              f"  dpr={scr.devicePixelRatio()}")
        # 判定用的是 availableGeometry，模拟 Snap 也是贴工作区
        for name, rect in snap_layouts(av):
            r = compute_want(rect, av)
            flag = "OK " if r["want"] else "MISS"  # 期望命中
            print(f"  [{flag}] {name:8s} g={rect.width()}x{rect.height()}"
                  f"@{rect.x(),rect.y()}  rw={r['rw']} rh={r['rh']}"
                  f"  flush=({int(r['flush_x'])},{int(r['flush_y'])})"
                  f"  std=({int(r['std_w'])},{int(r['std_h'])}) full={int(r['full'])}")
    print("=" * 72)
    # 另外测一个「普通窗口拖到左上角、没贴标准比例」的反例，应 miss
    av = app.primaryScreen().availableGeometry()
    normal = QRect(av.x(), av.y(), 1134, 820)
    r = compute_want(normal, av)
    print(f"反例 普通窗口 1134x820 @(0,0) -> want={r['want']} (期望 False)")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
