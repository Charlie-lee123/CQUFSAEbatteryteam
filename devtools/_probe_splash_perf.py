"""性能探针：加载【已发布 exe】里的 splash_anim，用计时器包装 _decode_frame /
_show_frame，在真实事件循环里跑 ~3s，测出：

- 实际显示帧率（相邻 _show_frame 墙钟间隔的中位数 / P95 / 最大）
- 主线程同步解码回退次数（sync fallback = 卡顿真凶：预解窗口没跟上，
  _show_frame 被迫在主线程同步解码，阻塞事件循环）
- 单帧解码耗时（worker 侧）

用法：EXE_PATH=... FRAMES_MP4=... .venv/Scripts/python.exe devtools/_probe_splash_perf.py
"""
import os
import sys
import types
import time
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.environ.get("EXE_PATH", os.path.join(ROOT, "发布", "SerialAssistant.exe"))
DEP_MP4 = os.environ.get("FRAMES_MP4",
                         os.path.join(ROOT, "发布", "_internal", "assets", "splash",
                                       "splash_dark_planb_4k60.mp4"))


def _stub(modname, **attrs):
    m = types.ModuleType(modname)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[modname] = m


def main() -> int:
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    arc = CArchiveReader(EXE)
    pyz = None
    for n in arc.toc:
        if n == "PYZ.pyz":
            pyz = arc.extract(n)
            break
    p = os.path.join(ROOT, "devtools", "_perf_pyz_tmp")
    os.makedirs(p, exist_ok=True)
    open(os.path.join(p, "PYZ.pyz"), "wb").write(pyz)
    z = ZlibArchiveReader(os.path.join(p, "PYZ.pyz"))
    code = z.extract("splash_anim")

    class _Log:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
    _stub("app_log", get_logger=lambda *a, **k: _Log(), info=print, warning=print)
    _stub("ui_theme", current_scheme=lambda: "dark", schemes=lambda: ["dark", "light"])

    mod = types.ModuleType("splash_anim_deployed")
    mod.__file__ = "<deployed>"
    exec(code, mod.__dict__)

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer

    # ---- 仪表 ----
    disp = []          # (t, idx) 每次真正显示一帧
    sync_fb = {"n": 0}
    dec_dur = []

    orig_decode = mod._SplashWindow._decode_frame
    def wrap_decode(self, idx):
        is_main = threading.current_thread().name == "MainThread"
        if is_main:
            sync_fb["n"] += 1
        t0 = time.perf_counter()
        r = orig_decode(self, idx)
        if is_main:                       # 只记主线程同步解码耗时（真凶）
            dec_dur.append(time.perf_counter() - t0)
        return r
    mod._SplashWindow._decode_frame = wrap_decode

    orig_show = mod._SplashWindow._show_frame
    def wrap_show(self, idx):
        if idx >= 0:
            disp.append((time.perf_counter(), idx))
        return orig_show(self, idx)
    mod._SplashWindow._show_frame = wrap_show

    app = QApplication(sys.argv)
    window = mod.play({"id": "p", "label": "p", "path": DEP_MP4,
                       "kind": "video", "theme": "dark"})
    if window is None:
        print("play() 返回 None")
        return 3

    # 模拟真实屏幕分辨率：offscreen 默认窗口很小，_pix_size 偏小会低估解码成本。
    # 用 PIX_SIZE=WxH 覆盖 window._pix_size，让解码 worker 按真实分辨率缩放。
    pix = os.environ.get("PIX_SIZE")
    if pix:
        w, h = map(int, pix.split("x"))
        window._pix_size = (w, h)
        print(f"[模拟分辨率 {w}x{h}]")

    done = []
    def sample():
        done.append((time.perf_counter(), getattr(window, "_fr_idx", -99)))
    t = QTimer(); t.setInterval(16); t.timeout.connect(sample); t.start()
    end = QTimer(); end.setSingleShot(True)
    end.timeout.connect(lambda: (t.stop(), app.quit())); end.start(3000)
    app.exec()

    # ---- 统计 ----
    print(f"显示的帧数: {len(disp)} (3s 内, 目标 60fps≈180)")
    if len(disp) >= 2:
        iv = [(disp[i+1][0]-disp[i][0])*1000 for i in range(len(disp)-1)]
        iv_s = sorted(iv)
        med = iv_s[len(iv_s)//2]
        p95 = iv_s[min(len(iv_s)-1, int(len(iv_s)*0.95))]
        mx = iv_s[-1]
        print(f"帧间隔(ms): 中位={med:.1f}  P95={p95:.1f}  最大={mx:.1f}  "
              f"-> 等效 fps≈{1000/med:.1f}")
        stalls = [x for x in iv if x > 33]   # >33ms = 掉到 <30fps 的卡顿
        print(f"卡顿帧间隔(>33ms) 数: {len(stalls)}  "
              f"(占比 {100*len(stalls)/len(iv):.1f}%)")
    print(f"主线程同步解码回退次数(sync fallback): {sync_fb['n']}")
    if dec_dur:
        ds = sorted(dec_dur)
        print(f"主线程同步解码单帧耗时(ms): 中位={ds[len(ds)//2]*1000:.1f}  "
              f"最大={ds[-1]*1000:.1f}  (共 {len(ds)} 次)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
