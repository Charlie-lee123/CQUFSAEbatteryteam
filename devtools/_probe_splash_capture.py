"""真机开屏高频抓帧：ffmpeg ``ddagrab``（Desktop Duplication）→ 灰度原始帧。

用法::

    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_splash_capture.py \
        --band 0,860,3200,680 --secs 3.9 --fps 165 --app source

- ``--band x,y,w,h`` 是**物理像素**区域（本机 3200×2000 @dpr2）。
- ``--app source`` 起 ``python devtools/_probe_splash_app.py``（带逐帧追踪）；
  ``--app exe`` 起 ``发布/SerialAssistant.exe``（纯真机，无追踪）。
- ⛔ 经 pipe 回传会掉帧（每帧 1.4~2.2MB，165fps 实测只剩 ~84fps）；
  让 ffmpeg **直写文件**实测 165fps / 0 drop，故走直写。
- 帧号 i ↔ 时间 ``i/fps``（ddagrab CFR，dup 帧时间戳照常推进）；
  与动画时钟的绝对偏移由内容（图标包围盒）反推，不依赖 wall clock。
- 产物：``<out>.raw``（uint8 灰度，逐帧拼接） + ``<out>.json``（元数据）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "_check" / "_splashcap" / "band"))
    ap.add_argument("--band", default="0,860,3200,680")
    ap.add_argument("--secs", type=float, default=3.9)
    ap.add_argument("--fps", type=int, default=165)
    ap.add_argument("--app", default="source", choices=["source", "exe", "none"])
    ap.add_argument("--app-delay", type=float, default=0.7)
    ap.add_argument("--quit-after", type=float, default=4.2)
    args = ap.parse_args()

    x, y, w, h = (int(v) for v in args.band.split(","))
    out_raw = Path(args.out).with_suffix(".raw")
    out_json = Path(args.out).with_suffix(".json")
    out_raw.parent.mkdir(parents=True, exist_ok=True)

    vf = (f"ddagrab=0:framerate={args.fps},hwdownload,format=bgra,"
          f"crop={w}:{h}:{x}:{y},format=gray")
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-f", "lavfi", "-i", vf, "-t", f"{args.secs}",
           "-c:v", "rawvideo", "-f", "rawvideo", "-y", str(out_raw)]
    frame_bytes = w * h

    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "windows")
    t_ff_start = time.perf_counter()
    ff = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    app_cmd = None
    if args.app == "source":
        app_cmd = [sys.executable,
                   str(ROOT / "devtools" / "_probe_splash_app.py"),
                   "--quit-after", str(args.quit_after),
                   "--out", str(ROOT / "_check" / "_splashcap" / "trace.jsonl")]
    elif args.app == "exe":
        app_cmd = [str(ROOT / "发布" / "SerialAssistant.exe")]

    app = None
    t_app = None
    try:
        deadline = t_ff_start + args.app_delay
        while time.perf_counter() < deadline:
            time.sleep(0.01)
        if app_cmd:
            t_app = time.perf_counter()
            app = subprocess.Popen(app_cmd, cwd=str(ROOT), env=env)
        rc = ff.wait()
        t_ff_end = time.perf_counter()
        err = b""
        try:
            err = ff.stderr.read() or b""
        finally:
            ff.stderr.close()
        if app is not None:
            try:
                app.wait(timeout=30)
            except subprocess.TimeoutExpired:
                app.kill()
    finally:
        if ff.poll() is None:
            ff.kill()

    frames = out_raw.stat().st_size // frame_bytes if out_raw.is_file() else 0
    meta = {
        "band": [x, y, w, h],
        "fps": args.fps,
        "frame_bytes": frame_bytes,
        "frames": int(frames),
        "t_ff_start": round(t_ff_start, 6),
        "t_ff_end": round(t_ff_end, 6),
        "t_app_start": round(t_app, 6) if t_app else None,
        "ffmpeg_rc": rc,
        "ffmpeg_err": (err or b"").decode("utf-8", "replace").strip(),
        "app_cmd": app_cmd,
    }
    out_json.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"[cap] frames={frames} fps_expected={args.fps} "
          f"({frames / args.fps:.2f}s) bytes={out_raw.stat().st_size / 1e6:.1f}MB")
    if meta["ffmpeg_err"]:
        print(f"[cap] ffmpeg: {meta['ffmpeg_err'][:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
