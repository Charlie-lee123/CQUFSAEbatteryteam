# -*- coding: utf-8 -*-
r"""录屏诊断②：开屏动画「logo 时刻卡」（2026-11-04 用户报，附截图）。

直接录**部署 exe 的真实启动**（Popen 发布/SerialAssistant.exe，无参数 → 完整开屏流程），
ffmpeg gdigrab 60fps 先起录、0.35s 后拉起 exe，录 9.5s 覆盖整个开屏 + 主窗出现。
结束后**强杀**该实例（不优雅退出 → 不写 QSettings，不污染配置）。
产物 _check/anim_record/splash_launch.mp4。
"""
import os
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_ROOT, "_check", "anim_record")
OUT_MP4 = os.path.join(OUT_DIR, "splash_launch.mp4")
EXE = os.path.join(_ROOT, "发布", "SerialAssistant.exe")
RECORD_SECONDS = 9.5
LAUNCH_DELAY = 0.35


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.monotonic()
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "gdigrab", "-framerate", "60",
         "-i", "desktop", "-t", str(RECORD_SECONDS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", OUT_MP4],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(LAUNCH_DELAY)
    proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
    launch_at = time.monotonic() - t0
    print(f"exe launched at t={launch_at:.3f}s pid={proc.pid}")
    time.sleep(RECORD_SECONDS - LAUNCH_DELAY + 0.5)
    proc.terminate()                     # 强杀：不走优雅退出，不写 QSettings
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    ff.wait(timeout=25)
    size = os.path.getsize(OUT_MP4) if os.path.exists(OUT_MP4) else -1
    print(f"RECORD rc={ff.returncode} size={size} -> {OUT_MP4}")
    return 0 if size > 200_000 else 1


if __name__ == "__main__":
    sys.exit(main())
