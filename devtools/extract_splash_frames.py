"""把 assets/splash/*.mp4 抽成帧序列，供开屏动画直接逐帧播放（绕开 QMediaPlayer
的 H.264 解码预热 ~1s，启动即动、且按显示器刷新率丝滑）。

分辨率策略（2026-11-03 晚，用户：「有点模糊和卡顿」）
------------------------
- 主体帧 = **NATIVE 3200x2000（cover 裁剪，与屏幕 1:1）**：用户定指标
  「帧率≥60、画质高于 2K 最好 4K」。4K/1440p 帧序列实测均到不了 60fps
  （Python 解码 + 28MB/帧绘制的内存带宽墙）；1:1 帧解码量更小（cv2 6 worker
  并行，释放 GIL）且**播放零缩放**，主线程 drawImage 1:1 拷贝 ~2ms。
- **最后 NATIVE_TAIL 帧按参考屏幕原生分辨率抽**（3200x2000@200% 的 cover 可视区，
  从 4K 原片 crop=3456:2160:192:0 后 1:1 缩放）：Plan B 的收尾定格帧是交棒帧 ——
  开屏窗口淡出时它与主窗口的 Qt logo 同屏，1080p 放大 1.85x 的软边 logo 叠在
  清晰 Qt logo 上 = 用户报的"残影"。定格段画面静止，35ms 解码阻塞不可见，
  只有这批帧能原生锐利。⚠️ 换参考屏幕要同步改 NATIVE 几何。
- 海报（xxx.poster.png）同样按原生分辨率截在 0.5s（对齐 splash_anim._POSTER_MS）。
- manifest 附带 **still_at_ms/still_len_ms**（逐帧 diff 找出的 ≥0.8s 天然静止段）：
  主窗构造的 ~0.94s 由 splash_anim 排 QTimer 在这段进行，画面本来就不动，
  用户看不出停顿（「开屏之前停住了这么久」）。

用法：
    python devtools/extract_splash_frames.py
    python devtools/extract_splash_frames.py assets/splash/splash_dark_4k60.mp4

产物：assets/splash/<stem>_frames/frame_00001.jpg ... + manifest.json
帧序列全为 JPEG（Plan B 不再烘焙无损真值帧，改由播放端运行时按真实屏幕 +
真实 UI logo 算纠正变换贴合，见 splash_anim）。manifest 的 ``land_box``（Plan B）
是 lockup 整张内容盒在**帧空间 [3200,2000]** 的归一化坐标，机型无关。
（播放端 splash_anim 优先用帧序列，缺帧时自动回退 QMediaPlayer，便于调试新 mp4）
"""
import json
import math
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SPLASH = ROOT / "assets" / "splash"
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 参考屏幕（3200x2000 物理 @200%）：4K 视频按 cover 铺满时左右各裁 177.78 物理px
# → 视频内可视区 = x∈[192,3648) 的 3456x2160，1:1 缩到 3200x2000。
# ⛔ 换屏幕/缩放比例要重算（见 devtools/_splash_align_probe.py 的推导）。
NATIVE = {"crop_w": 3456, "crop_h": 2160, "crop_x": 192, "w": 3200, "h": 2000}
NATIVE_TAIL = 12          # 末尾定格段按原生分辨率抽的帧数（≈0.2s，覆盖交棒冻结帧）
POSTER_MS = 500           # 与 splash_anim._POSTER_MS 一致

# —— Plan B 交棒真值（2026-11-04 用户「要完美重合」）——
# 以前落定帧是"从 SVG 母版 LANCZOS 缩小的合成图再经 4K→3200 重采样"，
# 与软件 UI 里 QSvgRenderer 直接画出来的 logo 差 1px 边缘相位（实测 bbox 虽
# 只差 0/+1px，ROI 内 mean|d|=1.47、1.7% 像素 >24）——交棒淡出时两层不同
# 像素的软边叠在一起就是"没完全重合"。
# ui_truth_<scheme>.png 由 devtools/_splash_ui_truth.py 从 BrandHeader 实际
# drawPixmap 的那张 lockup 矢量位图生成，与 UI 参考图**逐像素相同**（浅色
# diff=0）。这里把落定静止段直接换成它，并写无损 PNG（JPEG q2 会把深底
# #0D0D0F 压成 #010101，反而制造整屏色差）。
def _load_land_box(mp4_stem: str) -> list[float] | None:
    """读机型无关的归一化墨迹落点（由 _splash_render.land_box_for 生成）。"""
    lb_path = SPLASH / "_land_box.json"
    if not lb_path.is_file():
        return None
    try:
        data = json.loads(lb_path.read_text(encoding="utf-8"))
        key = "%s_planb" % ("dark" if "dark" in mp4_stem else "light")
        box = data.get(key)
        if isinstance(box, (list, tuple)) and len(box) == 4:
            return [float(x) for x in box]
    except Exception:                 # noqa: BLE001
        pass
    return None


def probe_fps(path: Path) -> int:
    # ffmpeg -i 只是打印信息、退出码恒为 1，不能 check
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path)],
        capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    for line in out.splitlines():
        if "Stream #0" in line and "fps" in line:
            import re
            m = re.search(r"(\d+)\s*fps", line)
            if m:
                return int(m.group(1))
    return 60


def find_still_at(out_dir: Path, fps: int, count: int) -> tuple[int, int]:
    """找主窗构造可以"藏进去"的静止窗口，写进 manifest 的 still_at_ms/still_len_ms。

    ⛔ 2026-11-04 重写（用户：「开屏动画会在两个 logo 变换的时候卡一两秒」）：
    旧实现按**相邻帧 diff** 找 ≥0.8s 的连续小变化段 —— 慢速文字揭示每帧只动一
    点点，被误判成"静止"（实测标在 1883ms = 图标→横排字变换段），主窗构造的
    ~1.2s 冻结正好砸在运动段上 = 肉眼"卡一两秒"。

    新判定：
      1. 用**窗口首尾的累积差异**（160x90 灰度 mean|Δ|）—— 0.35s 内内容真的
         没变才算静止（实测：A 定版段 cum=0.000；慢速揭示 cum=2~6，被排除）；
      2. 合格窗口取**最靠后**的一个 —— 冻结落在定版/落定/收尾段，观感是
         "logo 定版/落定，等一下就绪"，整段动画先完整播完；
      3. 末端窗口允许截断到最后一帧（B 的落定尾只有 0.18s，不截断凑不出窗口，
         旧版会退化到揭示段 = 卡顿砸在半途）；
      4. 找不到真静止就退化为"后半段累积差异最小的窗口"。
    """
    import numpy as np
    from PIL import Image
    # 落定段是无损 PNG、其余是 JPEG（见 apply_landing_truth），两种一起按编号收
    files = sorted(out_dir.glob("frame_*.*"),
                   key=lambda p: int(p.stem.split("_")[1]))[:count]
    # 缩到 160x90 灰度，只为判"画面动没动"；用 draft 模式加速 JPEG 解码
    small = []
    for f in files:
        im = Image.open(f)
        im.draft("L", (160, 90))
        small.append(np.asarray(im.convert("L").resize((160, 90)), dtype=np.float32))
    n = len(small)
    min_run = max(6, int(0.35 * fps))          # 窗口基准长度 ≥0.35s
    thr = 0.05                                 # 真静止阈值（实测 0.000~0.05）

    best_late = None                            # 真静止窗口里最靠后的
    best_any = None                             # 兜底：后半段累积差异最小
    for i in range(0, n - 6):
        j = min(i + min_run, n - 1)             # 末端窗口允许截断
        cum = float(np.abs(small[j] - small[i]).mean())
        if i >= n * 0.4 and (best_any is None or cum < best_any[1]):
            best_any = (i, cum)
        if cum < thr:
            best_late = (i, cum)                # 持续覆盖 → 循环结束即"最靠后"
    pick = best_late or best_any
    if pick is None:
        return (0, 0)
    return (int(round(pick[0] / fps * 1000)), int(round(min_run / fps * 1000)))


def extract(mp4: Path, height: int = 2160, q: int = 2) -> None:
    stem = mp4.stem
    out_dir = SPLASH / f"{stem}_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    fps = probe_fps(mp4)
    # 逐帧抽成 jpg（image2 多文件）。
    # ⛔ 2026-11-03 晚（用户：「有点模糊和卡顿」）分辨率从 1080p 升到 **1440p**：
    #    1080p 在 4K 屏放大 1.67x = 必糊；1440p 只放大 1.25x，肉眼接近原生。
    #    代价是单帧解码 14.6ms（>1080p 的 10ms），同步解码会掉帧——splash_anim
    #    已配套**后台解码线程**（worker 解码，主线程只取帧+绘制），实测 54.7fps
    #    稳定，消化得了。画质 q 从 5 提到 2（最高档，消除压缩噪点）。
    pattern = out_dir / "frame_%05d.jpg"
    # ⛔ 2026-11-03 晚（用户：「分辨率也不是很对」）：主体帧也按 **NATIVE
    #    （3200x2000 cover 裁剪）** 抽——1:1 匹配参考屏幕，播放时零缩放
    #    （drawImage 1:1 拷贝 ~2ms），分辨率即屏幕原生（高于 2K）。
    #    1440p/4K 帧序列实测都到不了 60fps（Python 解码+28MB/帧拷贝的内存
    #    带宽墙），1:1 帧反而最快最清晰。
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(mp4),
           "-vf", (f"crop={NATIVE['crop_w']}:{NATIVE['crop_h']}:{NATIVE['crop_x']}:0,"
                   f"scale={NATIVE['w']}:{NATIVE['h']},fps={fps}"),
           "-f", "image2", "-q:v", str(q), str(pattern)]
    subprocess.run(cmd, check=True)
    frames = sorted(out_dir.glob("frame_*.jpg"))
    total = len(frames)

    # —— 末尾定格段按屏幕原生分辨率重抽（交棒帧要锐利，见模块 docstring）——
    # 画面静止，35ms 解码阻塞不可见；运动段由后台线程解码（1440p 14.6ms）。
    start = total - NATIVE_TAIL + 1
    t0 = (start - 1) / fps
    ncmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(mp4), "-ss", f"{t0:.6f}",
            "-vf", (f"crop={NATIVE['crop_w']}:{NATIVE['crop_h']}:{NATIVE['crop_x']}:0,"
                    f"scale={NATIVE['w']}:{NATIVE['h']},fps={fps}"),
            "-start_number", str(start),
            "-f", "image2", "-q:v", "2", str(pattern)]
    subprocess.run(ncmd, check=True)

    # ⛔ 清掉历史遗留的超编号帧（旧版帧数可能更多，残留会被 glob 误收）
    for f in out_dir.glob("frame_*.*"):
        if int(f.stem.split("_")[1]) > total:
            f.unlink()

    # ⚠️ 必须在所有 ffmpeg 抽帧之后：Plan B 不再烘焙「本机真值帧」——
    #    改由播放器运行时按真实屏幕 + 真实 UI logo 算纠正变换贴合（见 splash_anim）。
    still_at, still_len = find_still_at(out_dir, fps, total)

    # ⛔ 清掉旧版遗留的无损 PNG 落定帧（frame_*.png）：本版帧序列全为 JPEG，
    #    残留的 png 会被 find_still_at / 播放端 glob 误收，造成重复编号帧。
    for f in out_dir.glob("frame_*.png"):
        try:
            f.unlink()
        except OSError:
            pass

    manifest = {"fps": fps, "count": total, "height": height,
                "ext": "jpg", "source": mp4.name,
                "still_at_ms": still_at, "still_len_ms": still_len,
                "native_tail": NATIVE_TAIL,
                "native_size": [NATIVE["w"], NATIVE["h"]]}
    # Plan B：把机型无关的 4K 落点盒（视频坐标 / [3840,2160]，lockup 整张内容）
    # 按本机 NATIVE 抽帧几何（crop + scale）重映射成**帧空间 [3200,2000]**，
    # 写进 manifest。播放端只需再按真实窗口 cover 把它落到真实 UI logo 上，
    # 即可在任意机型精确贴合（见 splash_anim._correction_transform）。
    if "planb" in mp4.stem:
        lb4k = _load_land_box(mp4.stem)
        if lb4k is not None:
            cx, cw = NATIVE["crop_x"], NATIVE["crop_w"]
            cy, ch = NATIVE.get("crop_y", 0), NATIVE["crop_h"]
            manifest["land_box"] = [
                (lb4k[0] * 3840.0 - cx) / cw,       # 帧空间 x0  / 3200
                (lb4k[1] * 2160.0 - cy) / ch,       # 帧空间 y0  / 2000
                lb4k[2] * 3840.0 / cw,              # 帧空间 w   / 3200
                lb4k[3] * 2160.0 / ch,              # 帧空间 h   / 2000
            ]
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    total_sz = sum(f.stat().st_size for f in out_dir.glob("frame_*.*")
                   if f.suffix.lower() in (".jpg", ".png"))
    print(f"  {stem}: {total} 帧 @ {fps}fps {height}p"
          f"（末 {NATIVE_TAIL} 帧 {NATIVE['w']}x{NATIVE['h']}）, "
          f"静止段 {still_at}ms+{still_len}ms, "
          f"总 {total_sz/1_048_576:.1f} MB -> {out_dir.name}")


def poster(mp4: Path) -> None:
    """首帧海报：截在 _POSTER_MS，按参考屏幕原生分辨率（cover 可视区 1:1）。"""
    out = SPLASH / f"{mp4.stem}.poster.png"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{POSTER_MS / 1000.0:.3f}", "-i", str(mp4),
           "-frames:v", "1",
           "-vf", (f"crop={NATIVE['crop_w']}:{NATIVE['crop_h']}:{NATIVE['crop_x']}:0,"
                   f"scale={NATIVE['w']}:{NATIVE['h']}"),
           str(out)]
    subprocess.run(cmd, check=True)


def main() -> None:
    if len(sys.argv) > 1:
        targets = [Path(p) for p in sys.argv[1:]]
    else:
        targets = sorted(SPLASH.glob("*.mp4"))
    print(f"抽取 {len(targets)} 个开屏视频的帧序列…")
    for mp4 in targets:
        if ".poster." in mp4.name:
            continue
        extract(mp4)
        poster(mp4)


if __name__ == "__main__":
    main()
