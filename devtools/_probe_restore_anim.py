# -*- coding: utf-8 -*-
"""验证「还原闪」根因：Windows 系统还原动画（MinAnimate）与自研 setGeometry
补间打架（2026-11-04）。

录屏证据：还原时窗口矩形非单调弹跳（799→669→743→687→670）—— 系统动画
按自己的轨迹拖窗口，我们每帧 setGeometry 也在拖，两个力互相拉扯。
showNormal 单次 42~55ms 也符合「系统动画在 ShowWindow 里同步等首帧」。

对照实验：A 组现状（系统动画开）；B 组 showNormal 前临时 SPI_SETANIMATION
禁用、补间收尾后还原。每组采样还原后 ~700ms 的窗口几何，看是否单调收敛。
QSettings 全量快照/还原；结束强退不写配置。
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_SNAPSHOT: dict = {}
LOG: list[str] = []
_T0 = time.perf_counter()
_Tween = None


class _ANIMATIONINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("iMinAnimate", wintypes.INT)]


_SPI_GETANIMATION = 0x0048
_SPI_SETANIMATION = 0x0049
_SPIF_UPDATEINIFILE = 0x01
_SPIF_SENDCHANGE = 0x02


def _get_anim() -> int:
    ai = _ANIMATIONINFO(ctypes.sizeof(_ANIMATIONINFO))
    ctypes.windll.user32.SystemParametersInfoW(
        _SPI_GETANIMATION, 0, ctypes.byref(ai), 0)
    return ai.iMinAnimate


def _set_anim(v: int) -> None:
    ai = _ANIMATIONINFO(ctypes.sizeof(_ANIMATIONINFO))
    ai.iMinAnimate = v
    ok = ctypes.windll.user32.SystemParametersInfoW(
        _SPI_SETANIMATION, ctypes.sizeof(_ANIMATIONINFO),
        ctypes.byref(ai), _SPIF_UPDATEINIFILE | _SPIF_SENDCHANGE)
    if not ok:
        print("!! SPI_SETANIMATION failed")


def _ms() -> float:
    return (time.perf_counter() - _T0) * 1000.0


def _log(msg: str) -> None:
    line = f"{_ms():8.1f}ms  {msg}"
    LOG.append(line)
    print(line)


def main() -> int:
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    from main_window import MainWindow

    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    global _Tween
    _Tween = ui_anim.Tween

    window = MainWindow()
    window._port_watch.stop()
    scr = app.primaryScreen().availableGeometry()
    w, h = 1134, 820
    window.setGeometry(scr.x() + (scr.width() - w) // 2,
                       scr.y() + max(60, (scr.height() - h) // 3), w, h)
    window.show()
    window.raise_()

    samples: list[tuple[float, int, int]] = []
    sampler = QTimer()
    sampler.setInterval(8)

    def _sample():
        g = window.geometry()
        samples.append((_ms(), g.width(), g.height()))

    sampler.timeout.connect(_sample)

    def _dump(tag: str):
        _log(f"--- {tag} 采样 ---")
        prev_w = None
        for ts, cw, ch in samples:
            arrow = ""
            if prev_w is not None:
                if cw > prev_w + 2:
                    arrow = "  <== 反弹(变大)"
                elif abs(cw - prev_w) <= 1 and ch == samples_prev_ch[0]:
                    pass
            samples_prev_ch[0] = ch
            _log(f"  {ts - _dump_t0[0]:6.1f}ms  {cw}x{ch}{arrow}")
            prev_w = cw
        samples.clear()

    samples_prev_ch = [None]
    _dump_t0 = [0.0]

    def restore_now(tag: str, suppress: bool):
        samples_prev_ch[0] = None
        _dump_t0[0] = _ms()
        _log(f"### {tag} (suppress_anim={suppress}) "
             f"normal_geo={window._normal_geometry} "
             f"now={window.geometry()}")
        saved_anim = _get_anim()
        _log(f"  系统 MinAnimate={saved_anim}")
        if suppress and saved_anim:
            _set_anim(0)
        t0 = time.perf_counter()
        window._on_escape()
        _log(f"  _on_escape took {(time.perf_counter() - t0) * 1000.0:.1f}ms")
        if suppress and saved_anim:
            # 补间收尾后再还原系统动画
            def _restore_anim():
                _set_anim(saved_anim)
                _log("  系统动画已还原")
            QTimer.singleShot(400, _restore_anim)
        sampler.start()

    def run_pass(label: str, suppress: bool, t_max: int, t_restore: int):
        def _max():
            _log(f"### max({label})")
            window.startup_fullscreen()
        def _restore():
            sampler.stop()
            restore_now(label, suppress)
        def _stop():
            sampler.stop()
            _dump(f"{label} 结果")
        QTimer.singleShot(t_max, _max)
        QTimer.singleShot(t_restore, _restore)
        QTimer.singleShot(t_restore + 700, _stop)

    at_quit = [5200]

    def _quit():
        app.quit()

    # A 组：现状
    run_pass("A现状", False, 1000, 2200)
    # B 组：临时禁用系统动画
    run_pass("B禁系统动画", True, 3600, 4800)
    at_quit[0] = 6200
    QTimer.singleShot(at_quit[0], _quit)

    app.exec()
    _log(f"收尾几何={window.geometry()} normal_geo={window._normal_geometry}")

    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_SNAPSHOT):
        st2.remove(k)
    for k, v in _SNAPSHOT.items():
        st2.setValue(k, v)
    st2.sync()
    # 保险：无论走哪条退出路径，系统动画必须还原
    _set_anim(1 if _get_anim() == 0 and _SNAPSHOT.get("dummy") is None else _get_anim())
    return 0


if __name__ == "__main__":
    sys.exit(main())
