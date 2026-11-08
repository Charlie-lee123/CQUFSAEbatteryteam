# -*- coding: utf-8 -*-
r"""从"双击打开"开始录屏，测启动到开屏动画各阶段耗时（2026-11-03 用户要求）。

做法：先起 ffmpeg 录主屏（10fps，够定位 100ms 精度），稳定 1.5s 后
Popen 启动 **发布/SerialAssistant.exe**（与桌面快捷方式同一目标 = 真实双击路径），
录 20s。随后 devtools/_analyze_startup.py 逐帧找「窗口出现 / 开屏开始动」两个转折点。
"""
import os
import subprocess
import sys
import time

ROOT = r"F:\MimoCodeworks\SerialAssistant"
OUT_DIR = os.path.join(ROOT, "_check", "startup_record")
OUT_MP4 = os.path.join(OUT_DIR, os.environ.get("REC_TAG", "startup") + ".mp4")
EXE = os.environ.get("REC_EXE") or os.path.join(ROOT, "发布", "SerialAssistant.exe")
RECORD_SECONDS = 20
PRE_ROLL = 1.5          # 录屏先跑多久再"双击"

os.makedirs(OUT_DIR, exist_ok=True)
if os.path.exists(OUT_MP4):
    os.remove(OUT_MP4)

ff = subprocess.Popen(
    ["ffmpeg", "-y", "-f", "gdigrab", "-framerate", "10",
     "-i", "desktop", "-t", str(RECORD_SECONDS),
     "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
     "-pix_fmt", "yuv420p", OUT_MP4],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

time.sleep(PRE_ROLL)
t_launch = time.monotonic()
print(f"PRE_ROLL={PRE_ROLL}s  启动 exe ...")
proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
print(f"pid={proc.pid}")

ff.wait(timeout=60)
print(f"ffmpeg rc={ff.returncode} size={os.path.getsize(OUT_MP4)}")

# 日志：app_log 的启动时间点（带时间戳的话可对表）
logdir = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                      "CQUFSAE-SerialAssistant", "logs")
if os.path.isdir(logdir):
    files = sorted((os.path.join(logdir, f) for f in os.listdir(logdir)
                    if f.endswith(".log")), key=os.path.getmtime)
    if files:
        print("--- 最新日志尾部 ---")
        with open(files[-1], "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        for ln in lines[-25:]:
            print("   ", ln.rstrip())
print(f"T_LAUNCH_MONO={t_launch}  PRE_ROLL={PRE_ROLL}")
