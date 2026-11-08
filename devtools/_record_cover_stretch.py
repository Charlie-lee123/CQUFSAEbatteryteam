# -*- coding: utf-8 -*-
r"""最大化/还原覆盖层「拉伸变形」取证（2026-11-07 用户点名「拉伸变形」）。

机械指标：每个 cover tick 记录 标签矩形宽高比(label_w/label_h) ÷ 源位图宽高比
(src_w/src_h)。该比值 ≠ 1 即表示源位图被**非均匀缩放**（内容被压扁/拉伸）。
  ratio < 1 → 内容横向被压窄；ratio > 1 → 内容横向被拉宽。
正确做法（uniform scale）下应恒 = 1.0。

同时 ffmpeg gdigrab 录屏供人工目检。黑底垫底窗防被 WorkBuddy 抢镜。
产物：_check/cover_stretch/{stretch.json, cover_stretch.mp4}
"""
import os
import subprocess
import sys
import threading
import time

_T0 = time.perf_counter()


def _ms() -> float:
    return (time.perf_counter() - _T0) * 1000.0


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import numpy as np  # noqa: E402
from PySide6.QtCore import QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_SNAPSHOT: dict = {}
OUT = os.path.join(_ROOT, "_check", "cover_stretch")
os.makedirs(OUT, exist_ok=True)
_SAMPLES: list[dict] = []
_EVENTS: list[tuple[float, str]] = []


def _log_event(tag: str) -> None:
    _EVENTS.append((_ms(), tag))
    print(f"EVENT {_ms():7.1f} {tag}", flush=True)


def run_app() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(40, exit=True)
    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    from main_window import MainWindow

    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    scr = app.primaryScreen()
    sg = scr.geometry()

    backdrop = QWidget(None, Qt.Window | Qt.FramelessWindowHint
                       | Qt.WindowStaysOnTopHint)
    backdrop.setAttribute(Qt.WA_ShowWithoutActivating)
    backdrop.setAutoFillBackground(True)
    pal = backdrop.palette()
    pal.setColor(backdrop.backgroundRole(), QColor(0, 0, 0))
    backdrop.setPalette(pal)
    backdrop.setWindowFlag(Qt.WindowTransparentForInput, True)
    backdrop.setGeometry(sg)
    backdrop.show()
    backdrop.raise_()

    window = MainWindow()
    window._port_watch.stop()
    w, h = 1134, 820
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.setGeometry(sg.x() + (sg.width() - w) // 2,
                       sg.y() + max(60, (sg.height() - h) // 3), w, h)
    window.show()
    window.raise_()
    window.activateWindow()

    # 包装 _on_cover_tick：记录每 tick 的 标签宽高比 ÷ 源位图宽高比
    _orig_tick = MainWindow._on_cover_tick

    def _wrap_tick(self):
        try:
            lab = self._cover_label
            if lab is not None and getattr(lab, "_src", None) is not None \
                    and not lab._src.isNull():
                sw, sh = lab._src.width(), lab._src.height()
                g = lab.geometry()
                if sw > 0 and sh > 0 and g.height() > 0:
                    label_ar = g.width() / g.height()
                    src_ar = sw / sh
                    # 旧指标：label 比例 ÷ 源比例（旧实现会≠1 = 拉伸）
                    stretch = label_ar / src_ar
                    # 新指标：paintEvent 用 KeepAspectRatioByExpanding 画出来的
                    # **内容矩形**宽高比（统一缩放 → 恒等于源图比例；若它随帧变化
                    # 才说明还在畸变）。
                    rect_ar = g.width() / max(1, g.height())
                    if src_ar > rect_ar:
                        dw = int(round(g.height() * src_ar)); dh = g.height()
                    else:
                        dw = g.width(); dh = int(round(g.width() / src_ar))
                    content_ar = dw / max(1, dh)  # 应恒 = src_ar
                    _SAMPLES.append({
                        "t": _ms(), "label_w": g.width(), "label_h": g.height(),
                        "src_w": sw, "src_h": sh, "stretch": stretch,
                        "content_ar": content_ar, "fs": self.isFullScreen(),
                    })
        except Exception:  # noqa: BLE001
            pass
        return _orig_tick(self)

    MainWindow._on_cover_tick = _wrap_tick

    def _max():
        _log_event("startup_fullscreen() 调用")
        window.startup_fullscreen()
        _log_event("startup_fullscreen() 返回")

    def _restore():
        _log_event("_on_escape() 调用")
        window._on_escape()
        _log_event("_on_escape() 返回")

    def _quit():
        _log_event("quit")
        try:
            import json
            with open(os.path.join(OUT, "stretch.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(_SAMPLES, fh)
            print("STAGE: stretch.json saved", flush=True)
        except Exception as exc:      # noqa: BLE001
            print(f"STAGE: stretch.json 失败 {exc}", flush=True)
        st2 = QSettings("MimoWorks", "SerialAssistant")
        for k in set(st2.allKeys()) - set(_SNAPSHOT):
            st2.remove(k)
        for k, v in _SNAPSHOT.items():
            st2.setValue(k, v)
        st2.sync()
        sys.stdout.flush()
        os._exit(0)

    _wd = threading.Timer(20.0, lambda: os._exit(3))
    _wd.daemon = True
    _wd.start()

    QTimer.singleShot(1200, _max)
    QTimer.singleShot(3200, _restore)
    QTimer.singleShot(5000, _quit)
    app.exec()
    _wd.cancel()
    return 0


def record() -> str:
    rec = os.path.join(OUT, "cover_stretch.mp4")
    if os.path.exists(rec):
        os.remove(rec)
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "gdigrab", "-framerate", "60",
         "-offset_x", "0", "-offset_y", "0",
         "-video_size", "3200x2000", "-i", "desktop", "-t", "12",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
         rec],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    app = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--app"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env=env)
    try:
        out, _ = app.communicate(timeout=30)
        for line in out.decode("utf-8", "replace").splitlines():
            print(line)
    except subprocess.TimeoutExpired:
        app.kill()
        print("⛔ app 子进程超时被杀")
    time.sleep(2.0)
    try:
        ff.stdin.write(b"q")
        ff.stdin.close()
    except Exception:                 # noqa: BLE001
        pass
    try:
        ff.wait(timeout=15)
    except subprocess.TimeoutExpired:
        ff.terminate()
    print("app rc:", app.returncode)
    return rec


if __name__ == "__main__":
    if "--app" in sys.argv:
        run_app()
        os._exit(0)
    _st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: _st.value(k) for k in _st.allKeys()})
    _rc = 1
    try:
        record()
        import json
        sp = os.path.join(OUT, "stretch.json")
        if os.path.exists(sp):
            with open(sp, encoding="utf-8") as fh:
                data = json.load(fh)
            print(f"\n=== 覆盖层拉伸指标：{len(data)} 个采样 ===")
            if data:
                maxs = max(d["stretch"] for d in data)
                mins = min(d["stretch"] for d in data)
                print(f"  (旧) stretch 范围：{mins:.4f} ~ {maxs:.4f}")
                cas = [d["content_ar"] for d in data]
                cmin, cmax = min(cas), max(cas)
                src_ars = {round(d["src_w"] / d["src_h"], 6) for d in data}
                print(f"  源图比例集合：{sorted(src_ars)}")
                print(f"  内容矩形比例范围：{cmin:.4f} ~ {cmax:.4f}"
                      f"（应恒等于源图比例 = 无畸变）")
                cavar = max(abs(cmax - cmin), 0.0)
                print(f"  内容比例跨帧最大变化：{cavar:.6f}"
                      f"（≈0 ⇒ 全程无拉伸/压扁）")
                for i, d in enumerate(data):
                    if i % 3 == 0 or i == len(data) - 1:
                        print(f"  t={d['t']:7.0f}ms label={d['label_w']}x"
                              f"{d['label_h']} src={d['src_w']}x{d['src_h']}"
                              f" stretch={d['stretch']:.4f}"
                              f" content_ar={d['content_ar']:.4f} fs={d['fs']}")
            cavar = max(abs(max(d["content_ar"] for d in data)
                            - min(d["content_ar"] for d in data)), 0.0) \
                if data else 1.0
            _rc = 0 if cavar < 0.002 else 1
        else:
            print("⛔ 没有 stretch.json")
    finally:
        try:
            _st2 = QSettings("MimoWorks", "SerialAssistant")
            for _k in set(_st2.allKeys()) - set(_SNAPSHOT):
                _st2.remove(_k)
            for _k, _v in _SNAPSHOT.items():
                _st2.setValue(_k, _v)
            _st2.sync()
        except Exception as _exc:      # noqa: BLE001
            print(f"[warn] QSettings 兜底恢复失败：{_exc}")
    sys.exit(_rc)
