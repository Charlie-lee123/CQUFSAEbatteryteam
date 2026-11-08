"""集成验证 _apply_snap_zoom：真实调用该方法（借用 main_window 的实现）。

用最小 stub 对象 + MethodType 绑定真实 _apply_snap_zoom，模拟本机 200% DPI
（dpr=2.0，逻辑 1600x1000 / 工作区 1600x952）下的两种 Snap 场景：

  A. 普通窗口 → 左1/2 分屏：_want_fullscreen=False
  B. 先全屏、再 Snap 左1/2（系统直接改几何、没走退出全屏路径）：
     _want_fullscreen 残留 True（这正是用户「先最大化再分屏不生效」的根因）

验证：
  · 两种场景都接管成功（_snap_zoom=True、blocks 被隐藏、chk_snap_power 亮出）
  · 场景 B 修复后：接管时 _want_fullscreen 必须被清成 False（否则后续
    changeEvent / _verify_fullscreen 会把窗口拽回全屏，分屏白接）
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

import main_window
from main_window import MainWindow

REAL_APPLY = getattr(MainWindow, "_apply_snap_zoom")


class _W(QWidget):
    def __init__(self):
        super().__init__()
        self._was_hidden = False


class SnapStub(QMainWindow):
    def __init__(self, geom, want_fs_residual: bool):
        super().__init__()
        self._body_lay = object()          # 非 None，跳过"未建窗"早退
        self._expanded = False
        self._geo_animating = False
        self._expect_normal = False
        self._in_snap_apply = False
        self._snap_zoom = False
        self._snap_pending = False
        self._snap_zoom_saved = None
        self._state_ts = 0.0               # 很久以前，不进 0.35s 冷却
        self._want_fullscreen = want_fs_residual
        self._geom = geom
        self.brand_header = _W()
        self._config_card = _W()
        self._tx_card = _W()
        self._rx_opts_row = _W()
        self.chk_snap_power = _W()
        self._padding_calls = 0

    def geometry(self):
        return self._geom

    def isVisible(self):
        return True

    def isFullScreen(self):
        return False

    def screen(self):
        return QApplication.primaryScreen()

    def _sync_fullscreen_padding(self):
        self._padding_calls += 1


def run(geom, want_fs_residual: bool, label: str) -> dict:
    stub = SnapStub(geom, want_fs_residual)
    fn = types.MethodType(REAL_APPLY, stub)
    fn()
    blocks_hidden = [w.isHidden() for w in
                     (stub.brand_header, stub._config_card,
                      stub._tx_card, stub._rx_opts_row)]
    return dict(
        label=label,
        snap_zoom=stub._snap_zoom,
        want_fs_after=stub._want_fullscreen,
        blocks_hidden=blocks_hidden,
        power_visible=stub.chk_snap_power.isVisible(),
    )


def main() -> int:
    app = QApplication(sys.argv)
    scr = app.primaryScreen().availableGeometry()
    half_w = scr.width() // 2
    left_half = __import__("PySide6.QtCore").QtCore.QRect(
        scr.x(), scr.y(), half_w, scr.height())

    a = run(left_half, False, "A 普通窗口→左1/2")
    b = run(left_half, True, "B 全屏残留→左1/2")

    ok = True
    for r in (a, b):
        expect_hidden = [True, True, True, True]
        good = (r["snap_zoom"] is True
                and r["blocks_hidden"] == expect_hidden
                and r["power_visible"] is True)
        # 场景 B 关键：修复后接管必须把残留 intent 清掉
        if r["label"].startswith("B"):
            good = good and (r["want_fs_after"] is False)
        ok = ok and good
        print(f"[{'PASS' if good else 'FAIL'}] {r['label']}: "
              f"snap_zoom={r['snap_zoom']} want_fs_after={r['want_fs_after']} "
              f"blocks_hidden={r['blocks_hidden']} power_visible={r['power_visible']}")
    print("=" * 60)
    print("RESULT:", "ALL PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
