# -*- coding: utf-8 -*-
r"""残影检测 v3：**屏幕合成图 vs 面板自绘** 逐像素差异。

为什么之前抓不到：`_SettingsShot` 是**独立顶层 ToolTip 窗**，`dialog.grab()` 不含它；
只有抓**屏幕合成**才能看到。这里把「屏幕上的对话框区域」和「dialog.grab()」对齐后
做逐像素差，差得多 = 屏幕上多了一层东西（残影）。

不设 QT_SCALE_FACTOR，用本机真实 200% OS 缩放，贴近用户环境。
在多个时机各测一次：打开后 / 滚动后 / 换车后 / 静置 2s 后。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import numpy as np  # noqa: E402
from PySide6.QtCore import QPoint, QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

OUT = os.environ.get("GHOST_OUT") or os.path.join(_ROOT, "_check", "ghost_diff")
os.makedirs(OUT, exist_ok=True)
_SNAP: dict = {}


def _np(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_RGB32)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), np.uint8)
    stride = img.bytesPerLine()
    arr = arr[: stride * h].reshape(h, stride // 4, 4)[:, :w, :3]
    return arr.copy()


def run() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(90, exit=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAP.update({k: st.value(k) for k in st.allKeys()})

    import customization
    import ui_anim
    from main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    scr = app.primaryScreen()
    sg = scr.geometry()
    print(f"screen={sg.width()}x{sg.height()} dpr={scr.devicePixelRatio():.2f}", flush=True)

    if "T9-2026" not in customization.vehicle_names():
        customization.save_vehicle("T9-2026", [25] * 7, [8] * 7)

    window = MainWindow()
    window._port_watch.stop()
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    # ★ 用**最大化**（用户真实状态 startup_maximized=true），而不是窗口化：
    #   最大化下设置齿轮在全屏窗口左下角，飞入快照从很远的小矩形放大，几何完全不同。
    window.showMaximized()
    window.raise_()
    window.activateWindow()

    results = []

    def check(tag: str) -> None:
        d = window._settings_dialog
        if d is None:
            print(f"[{tag}] no dialog", flush=True)
            return
        try:
            QApplication.processEvents()
            tl = d.mapToGlobal(QPoint(0, 0))
            sz = d.size()
            dpr = scr.devicePixelRatio()
            px = int(round(tl.x() * dpr)); py = int(round(tl.y() * dpr))
            pw = int(round(sz.width() * dpr)); ph = int(round(sz.height() * dpr))
            # ★ 用 PIL ImageGrab（GDI BitBlt）抓**真实屏幕合成**，才看得到独立残留窗/合成残影；
            #   QScreen.grabWindow 在 Qt 里是重新渲染控件，抓不到 DWM 层。
            from PIL import ImageGrab
            img = ImageGrab.grab(bbox=(px, py, px + pw, py + ph), all_screens=True)
            a = np.array(img.convert("RGB")).astype(np.int16)
            dg = d.grab()
            if dg.isNull():
                print(f"[{tag}] grab null", flush=True)
                return
            b = _np(dg.toImage()).astype(np.int16)
            # 统一到同一尺寸
            if a.shape[:2] != b.shape[:2]:
                from PySide6.QtCore import QSize
                dg2 = dg.scaled(QSize(a.shape[1], a.shape[0]),
                                Qt.AspectRatioMode.IgnoreAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
                b = _np(dg2.toImage()).astype(np.int16)
            h = min(a.shape[0], b.shape[0]); w = min(a.shape[1], b.shape[1])
            a = a[:h, :w]; b = b[:h, :w]
            dmax = np.abs(a - b).max(axis=2)
            big = float((dmax > 40).mean()) * 100.0     # 明显不同的像素占比
            mean = float(dmax.mean())
            img.save(os.path.join(OUT, f"{tag}_screen.png"))
            dg.save(os.path.join(OUT, f"{tag}_widget.png"))
            results.append((tag, big, mean))
            print(f"[{tag}] DIFF big%={big:.2f}  mean={mean:.2f}  size={w}x{h}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[{tag}] err {exc!r}", flush=True)

    def step_open():
        window._open_settings()
        print("opened", flush=True)

    def step_after_open():
        check("01_after_open")

    def step_scroll1():
        d = window._settings_dialog
        sa = d.findChild(QScrollArea) if d else None
        if sa is not None:
            sa.verticalScrollBar().setValue(320)
        QTimer.singleShot(300, lambda: check("02_scroll320"))

    def step_switch():
        d = window._settings_dialog
        if d is not None:
            idx = d.cmb_vehicle_number.findData("T9-2026")
            if idx >= 0:
                d.cmb_vehicle_number.setCurrentIndex(idx)
        QTimer.singleShot(1100, lambda: check("03_after_switch"))

    def step_scroll2():
        d = window._settings_dialog
        sa = d.findChild(QScrollArea) if d else None
        if sa is not None:
            sa.verticalScrollBar().setValue(520)
        QTimer.singleShot(300, lambda: check("04_scroll520"))

    def step_idle():
        check("05_idle2s")

    def _quit():
        try:
            d = window._settings_dialog
            if d is not None:
                d._veh_allow = False
                d.reject()
        except Exception:
            pass
        st2 = QSettings("MimoWorks", "SerialAssistant")
        for k in set(st2.allKeys()) - set(_SNAP):
            st2.remove(k)
        for k, v in _SNAP.items():
            st2.setValue(k, v)
        st2.sync()
        print("=== SUMMARY ===", flush=True)
        for tag, big, mean in results:
            print(f"  {tag}: big%={big:.2f} mean={mean:.2f}", flush=True)
        os._exit(0)

    QTimer.singleShot(1200, step_open)
    QTimer.singleShot(2600, step_after_open)
    QTimer.singleShot(3200, step_scroll1)
    QTimer.singleShot(4200, step_switch)
    QTimer.singleShot(6000, step_scroll2)
    QTimer.singleShot(7500, step_idle)
    QTimer.singleShot(8500, _quit)
    app.exec()
    return 0


if __name__ == "__main__":
    _st = QSettings("MimoWorks", "SerialAssistant")
    _SNAP.update({k: _st.value(k) for k in _st.allKeys()})
    try:
        run()
    finally:
        try:
            _st2 = QSettings("MimoWorks", "SerialAssistant")
            for _k in set(_st2.allKeys()) - set(_SNAP):
                _st2.remove(_k)
            for _k, _v in _SNAP.items():
                _st2.setValue(_k, _v)
            _st2.sync()
        except Exception:
            pass
    sys.exit(0)
