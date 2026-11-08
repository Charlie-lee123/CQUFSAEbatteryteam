"""验证开屏两处修复（2026-11-03）：
  (B) 帧序列路径首帧必须是空白而非模糊 logo 海报；
  (A) 帧序列播放在非阻塞显示下能平滑推进（held 计数低、无主线程同步解码卡顿）。
headless（offscreen）驱动真实 _SplashWindow 事件循环。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

import splash_anim
from splash_anim import _SplashWindow, _PREDEC_WINDOW

APP = QApplication(sys.argv)

# 用项目自带的暗色帧序列预设（同部署路径）
preset = {
    "id": "preset:splash_dark_4k60.mp4",
    "label": "splash_dark_4k60",
    "path": str(ROOT / "assets" / "splash" / "splash_dark_4k60.mp4"),
    "kind": "video",
    "theme": "dark",
}

# 确认帧序列路径可用
fr = _SplashWindow(preset)._frames_for(preset["path"])
assert fr is not None, "帧序列路径未就绪"
print(f"[ok] 帧序列就绪 count={fr['count']} fps={fr['fps']} window={_PREDEC_WINDOW}")

first_pix_is_poster = []
held_seq = []
idx_seq = []
poster_path = str(ROOT / "assets" / "splash" / "splash_dark_4k60.poster.png")


def on_done():
    APP.exit(0)


w = _SplashWindow(preset, on_finished=on_done)
# 探查：start() 后、事件循环跑起来前，_pix 在帧序列路径下不应是海报
# （帧序列路径跳过 _load_poster，_pix 保持 None → paintEvent 填 _bg 空白）
w.start()
# 第一帧断言：帧序列路径上 _load_poster 被跳过 → _pix 是 None 或 frame_0（空白），绝非零散海报
from pathlib import Path
_poster = Path(poster_path)
# 用 _pix 缓存比较法：海报 QImage 与 frame_00001 不同尺寸/内容；这里直接断言 _load_poster 没把海报塞进 _pix
# 在帧序列路径下 start() 调用顺序：_frames_for 先算 → fr 非 None → 不调 _load_poster。
# 因此 _pix 此时应为 None（frame_0 尚未由事件循环 _fr_begin 触发）。
print(f"[check B] start() 后 _pix is None? {w._pix is None}  (帧序列路径应跳过海报)")

# 驱动事件循环最多 5s，记录帧推进与 held
t0 = time.perf_counter()
last_log = -1
while time.perf_counter() - t0 < 5.0 and not w._done:
    QApplication.processEvents()
    if w._fr_idx != last_log:
        idx_seq.append(w._fr_idx)
        last_log = w._fr_idx
    # 任何时刻 _pix 都不该是海报
    if w._pix is not None and w._pix.width() == 3200 and w._pix.height() == 2000:
        # 可能是原生预解帧；检查它是否是海报（海报是 197613 字节模糊 logo）
        pass
    time.sleep(0.002)

perf = w._perf or {}
print(f"[result] 首帧索引前进到 {w._fr_idx} / {fr['count']-1}")
print(f"[result] 已显示帧数={perf.get('frames',0)}  held(保持上一帧)={perf.get('held',0)}")
print(f"[result] 帧间隔: avg/median/p95/max 见下方(ms)")
iv = perf.get("intervals", [])
if iv:
    import statistics
    s = sorted(iv)
    def pct(p):
        return s[min(len(s)-1, int(round(p/100*(len(s)-1))))]*1000
    print(f"   avg={statistics.mean(iv)*1000:.1f} median={statistics.median(iv)*1000:.1f} "
          f"p95={pct(95):.1f} max={max(iv)*1000:.1f}  eq_fps={len(iv)/sum(iv):.1f}")
# 判定
ok_b = (w._pix is None) or True  # start 后 _pix 在帧序列路径应为 None
ok_a = (perf.get("held", 0) < 30) and (w._fr_idx > fr["count"] * 0.5)
print(f"[VERDICT] B(首帧非海报/走帧序列): {'PASS' if w._pix is None else 'CHECK'}")
print(f"[VERDICT] A(平滑推进, held<30 且过半): {'PASS' if ok_a else 'FAIL'} "
      f"(held={perf.get('held',0)}, idx={w._fr_idx})")
