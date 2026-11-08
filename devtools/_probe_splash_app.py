"""开屏逐帧追踪：**不改产品源码**，运行时 monkeypatch splash_anim 记录每帧状态。

用法（必须用项目 venv、从项目根目录跑）::

    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_splash_app.py \
        --out _check/_splashcap/trace.jsonl --quit-after 5.0

记录内容（JSONL，一行一条）::

    {"k":"boot", ...}            屏幕/窗口/DPR/进程信息
    {"k":"tick", wall, mt, ...}  _motion_tick 每次触发（= 定时器节拍）
    {"k":"paint", wall, mt, ...} paintEvent 每次执行（含 _state 输出与几何换算）
    {"k":"land"/"finish"/"fade"} 关键事件

wall 一律是 ``time.perf_counter()``（Windows 下 = QPC，**系统级单调时钟**，
可与另一个进程（ffmpeg 抓帧）的时间戳直接对齐）。

⚠️ QSettings 由外层驱动用 ``reg export/import`` 整体备份/还原；本脚本只读不写。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

REC: list[dict] = []
_OUT = None
_T0 = time.perf_counter()


def _emit(obj: dict) -> None:
    obj["wall"] = round(time.perf_counter(), 6)
    obj["rel"] = round(time.perf_counter() - _T0, 6)
    REC.append(obj)


def _flush() -> None:
    if _OUT is None:
        return
    try:
        with open(_OUT, "w", encoding="utf-8") as fh:
            for row in REC:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:                    # noqa: BLE001
        print(f"[probe] trace 写盘失败：{exc}", file=sys.stderr)


def _install_patches(quit_after: float) -> None:
    import ctypes
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    import splash_anim

    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetShellWindow.restype = ctypes.c_void_p

    # 落点段每帧 QSvgRenderer.render 的耗时（判断「矢量每帧光栅化」是不是瓶颈）
    from PySide6.QtSvg import QSvgRenderer
    svg_acc = {"ms": 0.0, "n": 0}
    orig_svg_render = QSvgRenderer.render

    def svg_render(self, painter, *a, **kw):
        t0 = time.perf_counter()
        orig_svg_render(self, painter, *a, **kw)
        svg_acc["ms"] += (time.perf_counter() - t0) * 1000.0
        svg_acc["n"] += 1

    QSvgRenderer.render = svg_render

    CLS = splash_anim._SplashWindow

    def _fg() -> dict:
        try:
            hwnd = int(user32.GetForegroundWindow() or 0)
            rect = (ctypes.c_long * 4)()
            user32.GetWindowRect(ctypes.c_void_p(hwnd), rect)
            return {"fg": hwnd, "fg_rect": [rect[0], rect[1], rect[2], rect[3]]}
        except Exception:                       # noqa: BLE001
            return {}

    def _state_of(self) -> dict:
        m = getattr(self, "_motion", None)
        if m is None:
            return {}
        try:
            w, h = int(self.width()), int(self.height())
            xr, yr, hr, clip, sigma, alpha = m._state(
                float(self._mt), w / max(1, h))
            s = m._spec
            ink_h = hr * h
            bmp_w = ink_h * s.lockup_aspect / s.ink_w_frac
            bmp_h = bmp_w / s.bitmap_aspect
            left = xr * w - s.ink_left * bmp_w
            top = yr * h - s.ink_top * bmp_h
            dpr = float(m._dpr)
            return {
                "w": w, "h": h, "dpr": dpr,
                "xr": round(xr, 5), "yr": round(yr, 5), "hr": round(hr, 5),
                "clip": round(clip, 5), "sigma": round(sigma, 5),
                "alpha": round(alpha, 5),
                "bmp_w": round(bmp_w, 2), "bmp_h": round(bmp_h, 2),
                "left": round(left, 2), "top": round(top, 2),
                # 分隔线左边界 / 图标 clip 右边界（逻辑 px）
                "div_x": round(left + s.divider_x * bmp_w, 2),
                "icon_clip_x": round(left + s.divider_x * bmp_w - 1.5, 2),
                "div_y": round(top + s.divider_y * bmp_h, 2),
                "div_h": round(s.divider_h * bmp_h, 2),
                "fade_k": round(float(getattr(m, "_fade_k", 0.0)), 4),
                # 物理 px（抓帧坐标系）
                "div_x_phys": round((left + s.divider_x * bmp_w) * dpr, 1),
                "div_y_phys": round((top + s.divider_y * bmp_h) * dpr, 1),
                "div_h_phys": round(s.divider_h * bmp_h * dpr, 1),
                "icon_clip_x_phys": round(
                    (left + s.divider_x * bmp_w - 1.5) * dpr, 1),
            }
        except Exception as exc:                # noqa: BLE001
            return {"state_err": repr(exc)}

    orig_paint = CLS.paintEvent
    orig_tick = CLS._motion_tick
    orig_start = CLS._start_motion
    orig_land = CLS.set_land_from_window
    orig_finish = CLS.finish
    orig_fade = CLS._fade_step

    def paintEvent(self, ev):                   # noqa: N802
        t0 = time.perf_counter()
        svg_acc["ms"] = 0.0
        svg_acc["n"] = 0
        row = {"k": "paint", "mt": round(float(getattr(self, "_mt", 0.0)), 5),
               "geo": list(self.geometry().getRect()),
               "land_ready": bool(getattr(self, "_land_ready", False)),
               "done": bool(getattr(self, "_done", False))}
        row.update(_state_of(self))
        row.update(_fg())
        orig_paint(self, ev)
        row["paint_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
        row["svg_ms"] = round(svg_acc["ms"], 3)
        row["svg_n"] = svg_acc["n"]
        _emit(row)

    def _motion_tick(self):
        mt0 = float(getattr(self, "_mt", 0.0))
        orig_tick(self)
        _emit({"k": "tick", "mt0": round(mt0, 5),
               "mt": round(float(getattr(self, "_mt", 0.0)), 5)})

    def _start_motion(self):
        orig_start(self)
        m = getattr(self, "_motion", None)
        _emit({"k": "boot", "geo": list(self.geometry().getRect()),
               "dpr_win": float(self.devicePixelRatioF()),
               "screen": [self.screen().geometry().getRect(),
                          float(self.screen().devicePixelRatio())],
               "hz": int(getattr(self, "_hz", 0)),
               "interval_ms": (self._motion_timer.interval()
                               if self._motion_timer else -1),
               "motion_total": float(getattr(self, "_motion_total", 0.0)),
               "renderer_dpr": float(getattr(m, "_dpr", 0.0)) if m else 0.0,
               "clear_w": int(getattr(m, "_clear_w", 0)) if m else 0,
               "blur_w": int(getattr(m, "_blur_w", 0)) if m else 0,
               "hwnd": int(self.winId())})
        QTimer.singleShot(int(quit_after * 1000), QApplication.quit)

    def set_land_from_window(self, win):
        orig_land(self, win)
        _emit({"k": "land", "mt": round(float(self._mt), 5),
               "geo": list(self.geometry().getRect()),
               "main_geo": list(win.geometry().getRect()),
               "main_is_full": bool(win.isFullScreen()),
               "main_visible": bool(win.isVisible()),
               "splash_screen": self.screen().name(),
               "main_screen": win.screen().name() if win.screen() else "",
               "land": [round(v, 5) for v in getattr(self._motion, "_land",
                                                    (0, 0, 0))]})

    def finish(self):
        if not getattr(self, "_done", False):
            _emit({"k": "finish", "mt": round(float(self._mt), 5)})
        orig_finish(self)

    def _fade_step(self):
        orig_fade(self)
        _emit({"k": "fade", "mt": round(float(getattr(self, "_mt", 0.0)), 5),
               "fade_k": round(float(getattr(self._motion, "_fade_k", 0.0)), 4)
               if getattr(self, "_motion", None) else 0.0})

    CLS.paintEvent = paintEvent
    CLS._motion_tick = _motion_tick
    CLS._start_motion = _start_motion
    CLS.set_land_from_window = set_land_from_window
    CLS.finish = finish
    CLS._fade_step = _fade_step

    # 兜底：万一开屏没起来（配置为 none），也要有退出定时器
    orig_play = splash_anim.play_configured

    def play_configured(on_finished=None):
        player = orig_play(on_finished)
        if player is None:
            QTimer.singleShot(int(quit_after * 1000), QApplication.quit)
        return player

    splash_anim.play_configured = play_configured


def main() -> int:
    global _OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "_check" / "_splashcap"
                                         / "trace.jsonl"))
    ap.add_argument("--quit-after", type=float, default=5.0)
    args = ap.parse_args()
    _OUT = Path(args.out)
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _install_patches(args.quit_after)
    import main as app_main
    try:
        rc = app_main.main() or 0
    finally:
        _emit({"k": "exit"})
        _flush()
    return int(rc)


if __name__ == "__main__":
    sys.exit(main())
