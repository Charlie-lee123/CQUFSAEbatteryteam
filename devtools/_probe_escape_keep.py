# -*- coding: utf-8 -*-
r"""验收 keep 模式下 Esc / 还原键退出的行为（window-s2 网络失败，team-lead 接手验证）。

要验的改动：main_window.py `_on_escape` 的铺满分支在 `_keep_taskbar()` 为真时
直接 showNormal + 落位，跳过 S2 覆盖层（_begin_window_cover 等）。

三个场景：
  1. keep=True  最大化 → Esc            → 回普通、任务栏可见、几何=_normal_geometry、无覆盖层
  2. keep=False 最大化 → Esc            → 仍走原 S2 覆盖层路径（回归底线）
  3. keep=True  最大化 → 点还原键      → 同 1

⛔ QSettings 全量快照 + finally 还原（新键删除）；app.quit()，绝不 os._exit()。
用法（重定向到文件）：
    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_escape_keep.py > _check/escape_keep.log 2>&1
"""
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import ctypes  # noqa: E402
from ctypes import wintypes  # noqa: E402

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ORG, APP = "MimoWorks", "SerialAssistant"
OUT = os.path.join(_ROOT, "_check", "escape_keep")
_T0 = time.perf_counter()
_LINES: list = []
u = ctypes.windll.user32


def log(tag, **kw):
    line = {"t_ms": round((time.perf_counter() - _T0) * 1000, 1), "tag": tag, **kw}
    _LINES.append(line)
    print(json.dumps(line, ensure_ascii=False), flush=True)


def taskbar_state(hwnd):
    """任务栏可见性 + 主窗是否覆盖任务栏。"""
    tb = u.FindWindowW("Shell_TrayWnd", None)
    if not tb:
        return {"found": False}
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    r = wintypes.RECT()
    u.GetWindowRect(wintypes.HWND(tb), ctypes.byref(r))
    wr = wintypes.RECT()
    u.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(wr))
    covered = (wr.bottom >= r.bottom and wr.top <= r.top)
    return {"found": True, "visible": bool(u.IsWindowVisible(wintypes.HWND(tb))),
            "tb_rect": [r.left, r.top, r.right, r.bottom],
            "win_rect": [wr.left, wr.top, wr.right, wr.bottom],
            "covered": covered}


def win_rect(hwnd):
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    r = wintypes.RECT()
    u.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return [r.left, r.top, r.right, r.bottom]


def pump(app, ms=700):
    """跑一段事件循环，让状态落地。"""
    box = {"t0": time.perf_counter()}
    def tick():
        if (time.perf_counter() - box["t0"]) * 1000 >= ms:
            app.quit()
        else:
            QTimer.singleShot(20, tick)
    QTimer.singleShot(20, tick)
    app.exec()


def snapshot(win, label):
    hwnd = int(win.winId())
    log(label,
        full=win.isFullScreen(), max=win.isMaximized(),
        geo=[win.geometry().x(), win.geometry().y(),
             win.geometry().width(), win.geometry().height()],
        phys=win_rect(hwnd),
        taskbar=taskbar_state(hwnd),
        cover=(getattr(win, "_cover_label", None) is not None),
        normal_geo=(None if win._normal_geometry is None
                    else [win._normal_geometry.x(), win._normal_geometry.y(),
                          win._normal_geometry.width(), win._normal_geometry.height()]))


def run():
    os.makedirs(OUT, exist_ok=True)
    st = QSettings(ORG, APP)
    snap = {k: st.value(k) for k in st.allKeys()}
    try:
        import customization
        app = QApplication.instance() or QApplication(sys.argv)
        from main_window import MainWindow

        win = MainWindow()
        win.show()
        pump(app, 400)
        log("GEOM_INIT", normal=[win._normal_geometry.x(), win._normal_geometry.y(),
                                 win._normal_geometry.width(),
                                 win._normal_geometry.height()]
            if win._normal_geometry else None)

        # ---------- 场景 1：keep=True 最大化 → Esc ----------
        customization.set("maximize_keep_taskbar", True)
        win.startup_fullscreen()
        pump(app, 800)
        snapshot(win, "S1_KEEP_MAX")
        win._on_escape()
        pump(app, 600)
        snapshot(win, "S1_AFTER_ESC")

        # ---------- 场景 2：keep=False 最大化 → Esc（回归底线）----------
        customization.set("maximize_keep_taskbar", False)
        win.startup_fullscreen()
        pump(app, 800)
        snapshot(win, "S2_DEFAULT_MAX")
        win._on_escape()
        pump(app, 900)
        snapshot(win, "S2_AFTER_ESC")

        # ---------- 场景 3：keep=True 最大化 → 点还原键 ----------
        customization.set("maximize_keep_taskbar", True)
        win.startup_fullscreen()
        pump(app, 800)
        snapshot(win, "S3_KEEP_MAX")
        win._toggle_fullscreen()          # 标题栏那颗键
        pump(app, 600)
        snapshot(win, "S3_AFTER_TOGGLE")

        log("DONE")
    except Exception as exc:
        import traceback
        log("FATAL", err=repr(exc), tb=traceback.format_exc(limit=6))
    finally:
        for k, v in snap.items():
            st.setValue(k, v)
        st.sync()
        del st
        st2 = QSettings(ORG, APP)
        for k in st2.allKeys():
            if k not in snap:
                st2.remove(k)
        st2.sync()
        del st2
        log("RESTORE_OK")

    with open(os.path.join(OUT, "trace.json"), "w", encoding="utf-8") as fh:
        json.dump(_LINES, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(run())
