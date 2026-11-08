# -*- coding: utf-8 -*-
"""进程内量化最大化/还原补间的真实节拍（2026-11-04 录屏定位后续）。

录屏（gdigrab 60fps）显示两个方向都只有 ~4 个离散位置/260ms、每步间冻结
60~100ms。为分辨「录屏掉帧假象」vs「真实事件循环阻塞」，这里在进程内：
  ① 包一层 MainWindow._on_win_geo_frame —— 记每次 setGeometry 的 wall time；
  ② 包一层 Tween.advance —— 记 tick 实际间隔（事件循环是否被挤）；
  ③ 包 showNormal / _begin_window_cover / _grab_window_screen /
     _refresh_fs_snap_cache —— 记还原前各步骤耗时。
跑与录屏相同的时间线（慢速两切 + 快速连点），输出逐帧日志与统计。
QSettings 全量快照/还原；结束强退不写配置。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_SNAPSHOT: dict = {}
LOG: list[str] = []
_T0 = time.perf_counter()


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

    # ⚠️ 真机走 main.py：anim_fps 未改过 → display_max_hz()。探针必须一致，
    # 否则量到的是 33fps 默认节拍（此前探针 tickGap≈30ms 的来源）。
    _hz = ui_anim.set_frame_hz(ui_anim.display_max_hz())
    print(f"anim hz = {_hz}")

    # ---- ① 包 Tween.advance：记 tick 间隔与逐补间回调耗时
    _orig_advance = ui_anim.Tween.advance

    def _wrap_advance(self, now):
        t0 = time.perf_counter()
        _orig_advance(self, now)
        dt = (time.perf_counter() - t0) * 1000.0
        if dt > 8.0:  # 只记慢回调（正常 <2ms）
            _log(f"Tween.advance slow {dt:.1f}ms")

    ui_anim.Tween.advance = _wrap_advance

    window = MainWindow()

    # ---- ② 包 _on_win_geo_frame / _on_win_geo_done
    _orig_frame = MainWindow._on_win_geo_frame
    _last_tick = {"t": None}

    def _wrap_frame(self, t):
        g0 = self.geometry()
        t0 = time.perf_counter()
        _orig_frame(self, t)
        dt = (time.perf_counter() - t0) * 1000.0
        now = time.perf_counter()
        gap = (now - _last_tick["t"]) * 1000.0 if _last_tick["t"] else -1.0
        _last_tick["t"] = now
        g1 = self.geometry()
        moved = (g1.width() - g0.width(), g1.height() - g0.height())
        _log(f"frame t={t:.3f} setGeo={dt:.1f}ms tickGap={gap:.1f}ms "
             f"w={g1.width()} dWH={moved}")

    MainWindow._on_win_geo_frame = _wrap_frame

    _orig_done = MainWindow._on_win_geo_done

    def _wrap_done(self):
        _log(f"geo done -> {self.geometry()}")
        _orig_done(self)

    MainWindow._on_win_geo_done = _wrap_done

    # ---- ③ 包还原链各步骤
    _orig_shownormal = MainWindow.showNormal

    def _wrap_shownormal(self):
        t0 = time.perf_counter()
        _orig_shownormal(self)
        _log(f"showNormal {(time.perf_counter() - t0) * 1000.0:.1f}ms")

    MainWindow.showNormal = _wrap_shownormal

    _orig_cover = MainWindow._begin_window_cover

    def _wrap_cover(self, pix, start):
        t0 = time.perf_counter()
        r = _orig_cover(self, pix, start)
        _log(f"_begin_window_cover "
             f"{(time.perf_counter() - t0) * 1000.0:.1f}ms pix={'Y' if pix is not None else 'N'}")
        return r

    MainWindow._begin_window_cover = _wrap_cover

    _orig_grab = MainWindow._grab_window_screen

    def _wrap_grab(self):
        t0 = time.perf_counter()
        r = _orig_grab(self)
        _log(f"_grab_window_screen "
             f"{(time.perf_counter() - t0) * 1000.0:.1f}ms")
        return r

    MainWindow._grab_window_screen = _wrap_grab

    # ---- 摆位（同录屏脚本）
    window._port_watch.stop()
    scr = app.primaryScreen().availableGeometry()
    w, h = 1134, 820
    window.setGeometry(scr.x() + (scr.width() - w) // 2,
                       scr.y() + max(60, (scr.height() - h) // 3), w, h)
    window.show()
    window.raise_()

    def at(ms: int, name: str, fn):
        def fire():
            _log(f"### {name}")
            t0 = time.perf_counter()
            fn()
            dt = (time.perf_counter() - t0) * 1000.0
            if dt > 5.0:
                _log(f"### {name} handler took {dt:.1f}ms")
        QTimer.singleShot(ms, fire)

    at(1000, "max#1", window.startup_fullscreen)
    at(2000, "restore#1", window._on_escape)
    at(2900, "max#2", window.startup_fullscreen)
    at(3900, "restore#2", window._on_escape)
    at(4900, "quit", app.quit)

    app.exec()

    slow = [ln for ln in LOG if "slow" in ln or "took" in ln]
    print(f"\n== 慢回调 {len(slow)} 条 / 总帧 {_ms():.0f}ms ==")
    st2 = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st2.allKeys()) - set(_SNAPSHOT):
        st2.remove(k)
    for k, v in _SNAPSHOT.items():
        st2.setValue(k, v)
    st2.sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
