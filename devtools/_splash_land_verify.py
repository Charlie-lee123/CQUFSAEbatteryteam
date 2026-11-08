"""Plan B 落位「跨机型精确重合」验证（工具 venv）。

2026-11-04 之后改版：Plan B 不再烘焙「本机真值帧」，改成**播放器运行时纠正**——
视频里 logo 墨迹盒（机型无关，burn 进视频）按真实屏幕 + 真实 UI 墨迹盒算一个相似
变换，拉到真实墨迹盒上。这样「任意分辨率 / 缩放 / DPI 的机器」都能精确重合。

两层验收：

  1. 成片几何（真实像素）：把 4K mp4 末帧按 cover 映射回参考屏幕物理坐标，量 logo
     墨迹 bbox，与实机探针 UI 真值比对 —— 确认视频内容本身就把墨迹放到正确位置
     （机型无关的内容层）。

  2. **运行时纠正模型**（本脚本核心）：复现 splash_anim._correction_transform 的几何，
     在参考机器上断言「视频墨迹盒 V ≈ 真实 UI 墨迹盒 R」（纠正 ≈ 恒等、无回归）；
     再换多组屏幕分辨率断言 V 与 R **宽高比恒等**（= 1060/168）—— 这是相似变换能
     在任意机型把四角精确对齐的充要条件（单一缩放 + 平移即可四角对齐）。

用法：
    F:/WorkBuddyTools/venv/Scripts/python.exe devtools/_splash_land_verify.py
"""

from __future__ import annotations

import json
import os
import subprocess

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(ROOT, "_check", "splash", "landverify")
MASTERS = os.path.join(ROOT, "_check", "splash", "masters")
SPLASH = os.path.join(ROOT, "assets", "splash")

# 实机探针真值（devtools/_splash_align_probe.py 输出）：参考屏幕 3200x2000 物理
SCREEN = (3200, 2000)
UI_INK_PHYS = (97.26, 158.20, 1059.94, 167.0)   # left, top, w, h（物理 px）

# 跨机型模拟：换这几组逻辑分辨率验证相似变换恒等（宽高比一致 → 四角精确对齐）
CROSS_SCREENS = [
    (1600, 1000),   # 参考机 3200x2000@200%
    (1920, 1080),   # 常见 1080p@100%
    (1280, 800),
    (2560, 1440),   # 1440p@100%
    (1366, 768),
    (3000, 2000),   # 异形 / 带外显
]

CASES = [
    ("assets/splash/splash_light_planb_4k60.mp4", "light", 100),
    ("assets/splash/splash_dark_planb_4k60.mp4", "dark", 100),
]


def cover_to_screen(img: Image.Image) -> Image.Image:
    """3840x2160 → cover 铺满 3200x2000（等比放大到高 2000，左右各裁 177.78）。"""
    sw, sh = SCREEN
    scale = max(sw / img.width, sh / img.height)
    disp = (round(img.width * scale), round(img.height * scale))
    big = img.resize(disp, Image.Resampling.LANCZOS)
    x0 = (disp[0] - sw) // 2
    y0 = (disp[1] - sh) // 2
    return big.crop((x0, y0, x0 + sw, y0 + sh))


def ink_bbox(arr: np.ndarray, dark_bg: bool, th: int) -> tuple[int, int, int, int]:
    """浅底找暗墨 / 深底找亮墨的 bbox。"""
    lum = arr.mean(axis=2)
    mask = (lum < th) if not dark_bg else (lum > th)
    ys, xs = np.where(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def verify_video(path: str, variant: str, th: int) -> bool:
    """① mp4 末帧：cover 回屏幕后的墨迹 bbox（确认视频内容层正确）。"""
    out = os.path.join(CHECK, f"land_{variant}.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-sseof", "-0.15", "-i", path, "-frames:v", "1", out],
                   check=True)
    scr = cover_to_screen(Image.open(out).convert("RGB"))
    scr.save(os.path.join(CHECK, f"land_{variant}_screen.png"))
    x0, y0, x1, y1 = ink_bbox(np.asarray(scr)[:700, :1400], variant == "dark", th)
    got = (x0, y0, x1 - x0, y1 - y0)
    exp = UI_INK_PHYS
    dpx = [got[0] - exp[0], got[1] - exp[1],
           got[2] - exp[2], got[3] - exp[3]]
    print(f"[{variant}] mp4 末帧墨迹 bbox = {got}")
    print(f"[{variant}] UI 真值            = "
          f"({exp[0]:.1f}, {exp[1]:.1f}, {exp[2]:.1f}, {exp[3]:.1f})")
    print(f"[{variant}] 偏差 dx={dpx[0]:+.0f} dy={dpx[1]:+.0f} "
          f"dw={dpx[2]:+.0f} dh={dpx[3]:+.0f}  (物理 px)")
    ok = max(abs(v) for v in dpx[:2]) <= 2 and max(abs(v) for v in dpx[2:]) <= 4
    print(f"[{variant}] {'✓' if ok else '⛔'} 成片几何（机型无关内容层）")
    return ok


def ui_ink_box(variant: str) -> tuple[float, float, float, float]:
    """复现 BrandHeader.ink_global_rect()：UI 墨迹盒在全局逻辑坐标。

    路径：window_origin → brand_header → lockup_rect → ink 分数（均来自 ui_truth.json，
    与渲染器实际测量一致）。开屏窗口全屏且在 (0,0)，故全局坐标即开屏窗口本地坐标。
    """
    doc = json.load(open(os.path.join(MASTERS, "ui_truth.json"),
                        encoding="utf-8"))
    sc = doc["schemes"][variant]
    ox, oy = doc["window_origin_logical"]
    bx, by = doc["brand_header_logical"][0], doc["brand_header_logical"][1]
    # logo_box_physical 是 lockup 位图物理尺寸（1120x263），ink_local 在该位图空间
    # 内测量；logo_box_logical 是同一盒子换算成逻辑像素的位姿。两者都来自同一份
    # 测量，用它俩把墨迹从「位图物理像素」换算成「界面逻辑像素」。
    pw, ph = sc["logo_box_physical"][2], sc["logo_box_physical"][3]
    lx, ly, lw, lh = sc["logo_box_logical"]
    ix0, iy0, ix1, iy1 = sc["ink_local"]
    fx, fy = ix0 / pw, iy0 / ph          # 墨迹在 lockup 盒内的物理分数
    fw, fh = (ix1 - ix0) / pw, (iy1 - iy0) / ph
    ax = ox + bx + lx + fx * lw          # 逻辑：先到 lockup 逻辑盒，再按分数偏移
    ay = oy + by + ly + fy * lh
    return (ax, ay, fw * lw, fh * lh)


def video_ink_box(lb: list[float], screen: tuple[int, int]
                  ) -> tuple[float, float, float, float]:
    """复现 splash_anim._correction_transform 里的 V：视频墨迹盒 → 窗口逻辑坐标。

    lb = manifest.land_box（帧空间 [3200,2000] 归一化的墨迹盒）。K2 = cover 缩放。
    """
    sw, sh = screen
    K2 = max(sw / 3200.0, sh / 2000.0)
    vx = lb[0] * 3200.0 * K2 - (sw - 3200.0 * K2) / 2.0
    vy = lb[1] * 2000.0 * K2 - (sh - 2000.0 * K2) / 2.0
    vw = lb[2] * 3200.0 * K2
    vh = lb[3] * 2000.0 * K2
    return (vx, vy, vw, vh)


def verify_correction(variant: str) -> bool:
    """② 运行时纠正模型：参考机 ≈ 恒等；任意机型宽高比恒等 → 相似变换精确。"""
    stem = f"splash_{variant}_planb_4k60"
    man = json.load(open(os.path.join(SPLASH, stem + "_frames", "manifest.json"),
                        encoding="utf-8"))
    lb = man.get("land_box")
    if not lb or len(lb) != 4:
        print(f"[{variant}] ⛔ manifest 缺 land_box（墨迹盒未生成）")
        return False
    R = ui_ink_box(variant)
    R_asp = R[2] / R[3]
    print(f"[{variant}] UI 墨迹盒 R = {tuple(round(v, 3) for v in R)}  "
          f"宽高比={R_asp:.5f}")

    # —— 参考机器：纠正应≈恒等（无回归）——
    ref = (1600, 1000)  # 对应 ui_truth.screen_logical
    V = video_ink_box(lb, ref)
    V_asp = V[2] / V[3]
    dpos = (V[0] - R[0], V[1] - R[1])
    dsz = (V[2] - R[2], V[3] - R[3])
    print(f"[{variant}] 参考机 V = {tuple(round(v, 3) for v in V)}  "
          f"宽高比={V_asp:.5f}")
    print(f"[{variant}] 参考机 |V-R| 位置={tuple(round(v, 3) for v in dpos)} "
          f"尺寸={tuple(round(v, 3) for v in dsz)} (逻辑 px)")
    ref_ok = (max(abs(v) for v in dpos) <= 1.0
              and max(abs(v) for v in dsz) <= 1.0)
    print(f"[{variant}] {'✓' if ref_ok else '⛔'} 参考机纠正≈恒等（无回归）")

    # —— 跨机型：宽高比必须恒等（相似变换精确对齐的充要条件）——
    worst = 0.0
    for sw, sh in CROSS_SCREENS:
        Vv = video_ink_box(lb, (sw, sh))
        # 相似变换 s = R.w / Vv.w，要求映射后高度 == R.h ⇒ Vv.h/Vv.w == R.h/R.w
        asp_match = abs((Vv[3] / Vv[2]) - (R[3] / R[2]))
        worst = max(worst, asp_match)
    print(f"[{variant}] 跨机型墨迹盒宽高比最大偏差 = {worst:.2e} "
          f"（{'✓' if worst < 1e-9 else '⛔'} 相似变换四角精确对齐）")

    # —— 复现播放器相似变换，确认映射后 == R（任意机型都精确）——
    # 播放器变换 translate(R.x - s·vx, R.y - s·vy)·scale(s)：把 V 顶左点 (vx,vy)
    # 映到 (R.x, R.y)，V 底右点映到 (R.x + s·vw, R.y + s·vh)。因 s = R.w/vw，
    # 宽恒为 R.w；高 = s·vh，精确对齐的充要条件是 s·vh == R.h（即宽高比恒等，
    # 已在上面用 1e-9 验证）。这里复算确认。
    map_ok = True
    for sw, sh in CROSS_SCREENS:
        Vv = video_ink_box(lb, (sw, sh))
        s = R[2] / Vv[2]
        mh = s * Vv[3]
        if abs(mh - R[3]) > 1e-6:
            map_ok = False
            break
    print(f"[{variant}] {'✓' if map_ok else '⛔'} 相似变换把任意机型视频墨迹盒"
          f"精确映射到 UI 墨迹盒")
    return ref_ok and worst < 1e-9 and map_ok


def main() -> int:
    os.makedirs(CHECK, exist_ok=True)
    ok = True
    for path, variant, th in CASES:
        ok = verify_video(path, variant, th) and ok
        ok = verify_correction(variant) and ok
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
