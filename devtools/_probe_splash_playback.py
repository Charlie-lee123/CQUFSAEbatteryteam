"""探针：在真实 QApplication 事件循环里驱动开屏帧序列，验证帧是否随 wall-clock 推进。

用法（项目根目录，QT_QPA_PLATFORM=offscreen）：
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe devtools/_probe_splash_playback.py
"""
import os
import sys
import time

# 把项目根加进 sys.path（脚本在 devtools/ 下，Python 不会自动加项目根）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

import splash_anim
from splash_anim import configured, play


def main() -> int:
    app = QApplication(sys.argv)

    try:
        preset = configured()
    except Exception as exc:  # noqa: BLE001
        print(f"configured() 失败: {exc!r}")
        preset = None
    print(f"preset = {preset!r}")

    if not preset:
        print("没有可用的开屏预设 —— 无法测试帧序列")
        return 2

    window = play(preset)
    if window is None:
        print("play() 返回 None —— 开屏没播（可能回退到 mp4 或失败）")
        return 3

    # 直接探 _frames_for 是否命中
    fr = window._frames_for(str(preset.get("path", "")))
    print(f"_frames_for 命中 = {fr is not None}")
    if fr is not None:
        print(f"  帧数 count={fr['count']} fps={fr['fps']} "
              f"still={fr['still_ms']}+{fr['still_len_ms']}ms")
    else:
        print("  -> 帧序列未命中，回退到 QMediaPlayer/图片路径（可能卡顿/不动）")

    samples = []

    def sample():
        idx = getattr(window, "_fr_idx", -99)
        done = getattr(window, "_done", False)
        samples.append((time.perf_counter(), idx, done))

    timer = QTimer()
    timer.setInterval(120)
    timer.timeout.connect(sample)
    timer.start()

    # 跑 ~2.5s 看帧推进
    end = QTimer()
    end.setSingleShot(True)
    end.timeout.connect(lambda: (timer.stop(), app.quit()))
    end.start(2500)

    app.exec()

    # 去重相邻相同 idx，统计推进
    uniq = []
    for t, idx, done in samples:
        if not uniq or uniq[-1][1] != idx:
            uniq.append((round(t - samples[0][0], 2), idx, done))
    print(f"\n采样点 {len(samples)} 个，去重后帧序列 {len(uniq)} 跳：")
    for t, idx, done in uniq[:40]:
        print(f"  t={t:5.2f}s  idx={idx:4d}  done={done}")
    advanced = any(idx != -99 and idx != uniq[0][1] for _, idx, _ in uniq[1:])
    print(f"\n结论：帧序列是否随墙钟推进 = {advanced}")
    if not advanced:
        print("  ⚠️ 动画未推进 —— 需要排查 _frame_step / _frame_timer 是否真的跑起来")
    return 0 if advanced else 4


if __name__ == "__main__":
    sys.exit(main())
