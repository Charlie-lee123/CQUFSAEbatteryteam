"""金标准探针：直接抽取【已发布 exe】里的 splash_anim 模块字节码，桩掉 app_log /
ui_theme 等依赖，指向【已发布】的帧目录，在真实事件循环里驱动，确认帧真的随墙钟
推进。如果这里帧推进 = True，则『部署包里的开屏动画确实能动』，用户看到的『不动』
只可能是更早的旧包 / 观感问题。

用法：.venv/Scripts/python.exe devtools/_probe_deployed_playback.py
"""
import os
import sys
import types
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.environ.get("EXE_PATH",
                     os.path.join(ROOT, "发布", "SerialAssistant.exe"))
DEP_MP4 = os.environ.get("FRAMES_MP4",
                         os.path.join(ROOT, "发布", "_internal", "assets", "splash",
                                       "splash_dark_planb_4k60.mp4"))


def _stub(modname, **attrs):
    m = types.ModuleType(modname)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[modname] = m


def main() -> int:
    # 1) 抽取 PYZ
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    arc = CArchiveReader(EXE)
    pyz = None
    for name in arc.toc:
        if name == "PYZ.pyz":
            pyz = arc.extract(name)
            break
    if pyz is None:
        print("PYZ.pyz 未找到")
        return 3
    tmp = os.path.join(ROOT, "devtools", "_dep_pyz_tmp")
    os.makedirs(tmp, exist_ok=True)
    p = os.path.join(tmp, "PYZ.pyz")
    open(p, "wb").write(pyz)
    z = ZlibArchiveReader(p)
    code = z.extract("splash_anim")

    # 2) 桩依赖
    class _Log:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
    _stub("app_log", get_logger=lambda *a, **k: _Log(), info=print, warning=print)
    _stub("ui_theme", current_scheme=lambda: "dark",
          schemes=lambda: ["dark", "light"])

    # 3) 载入部署模块
    mod = types.ModuleType("splash_anim_deployed")
    mod.__file__ = "<deployed>"
    exec(code, mod.__dict__)

    # 4) 真实事件循环
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    app = QApplication(sys.argv)

    preset = {"id": "p", "label": "p", "path": DEP_MP4,
              "kind": "video", "theme": "dark"}
    window = mod.play(preset)
    if window is None:
        print("play() 返回 None —— 部署包没播")
        return 4
    fr = window._frames_for(DEP_MP4)
    print(f"部署模块 _frames_for 命中 = {fr is not None}"
          f"  (count={fr['count'] if fr else '-'})")

    samples = []
    def sample():
        samples.append((time.perf_counter(), getattr(window, "_fr_idx", -99)))
    t = QTimer(); t.setInterval(120); t.timeout.connect(sample); t.start()
    end = QTimer(); end.setSingleShot(True)
    end.timeout.connect(lambda: (t.stop(), app.quit())); end.start(2500)
    app.exec()

    uniq = []
    for tt, idx in samples:
        if not uniq or uniq[-1][1] != idx:
            uniq.append((round(tt - samples[0][0], 2), idx))
    print(f"去重后帧序列 {len(uniq)} 跳：")
    for tt, idx in uniq[:30]:
        print(f"  t={tt:5.2f}s idx={idx:4d}")
    advanced = len(uniq) > 3 and uniq[-1][1] != uniq[0][1]
    print(f"\n结论：部署包开屏帧序列是否随墙钟推进 = {advanced}")
    return 0 if advanced else 5


if __name__ == "__main__":
    sys.exit(main())
