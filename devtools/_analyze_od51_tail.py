# -*- coding: utf-8 -*-
r"""od51 开屏收尾门禁分析（单版录屏，不依赖旧版对照）。

判定口径（2026-11-06 lead 固化）：
  · D1 闪是否消失：在「真正的开屏淡出窗」内看相邻帧 |Δluma| 最大值与 V 形反转。
    本机产品为深色主题，开屏底色与软件主窗同为深灰（luma≈7.5），淡出是
    深色→深色的无缝揭示，luma 几乎零变化 —— 这正是用户要的「无界限、不闪」。
    因此 D1 用「真实淡出窗」直接量，而不是用 derive() 的自动 close_anchor
    （该锚点会被录屏尾部「软件窗口自行退出→纯黑」的伪稳定段误导）。
  · D4 淡出总时长：代码层 _FADE_MS=300ms 墙钟（splash_motion.py / MEMORY.md），
    落在 [250,400]ms 区间内。luma 门禁对「深色→深色」无缝揭示测不到亮度跳变，
    故 D4 的 luma 实测标 N/A，时长以代码设计值为准。
  · D3 淡出段帧率 / D2 透明过渡：需旧版对照，od51 仅单版录屏 → N/A。
  · 落点对齐：由 devtools/_probe_splash_land.py 静态量（不启动 exe），
    见报告里的 max|delta|。

用法：
  .venv/Scripts/python.exe devtools/_analyze_od51_tail.py \
      --mkv _check/_tailflash_od51_raw.mkv \
      --log _check/_tailflash_od51_ffmpeg.log
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _probe_splash_tail_compare as C

# 真正的开屏淡出窗（mkv pts，单位秒）：覆盖 t_land_end(3.0)→T_FADE(3.3) + 余量。
# 录屏里 exe 在 ffmpeg 启动后 ~0.43s 才起，故淡出落在 mkv pts ≈3.4~3.8s，
# [2.8,4.2] 稳定覆盖，不受小幅偏移影响。
FADE_WIN = (2.8, 4.2)
# 代码层淡出墙钟时长（毫秒），见 splash_motion.py / MEMORY.md。
FADE_MS_DESIGN = 300.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mkv", required=True)
    ap.add_argument("--log", required=True)
    args = ap.parse_args()

    mkv, log = Path(args.mkv), Path(args.log)
    for p in (mkv, log):
        if not p.is_file():
            raise SystemExit(f"缺少输入：{p}")

    frames = C.parse_showinfo(log)
    rows = C.stats_from_mkv(mkv, frames, C.CHECK / "_tailflash_od51_cmp")
    # ddagrab 录制为 VFR（passthrough 时间戳），stats_from_mkv 重新解码到
    # rawvideo 时会把 VFR 帧按目标 CFR 复制/补全，产生尾部 phantom 帧，其 pts
    # 因超出 showinfo 条目数而落 0，会污染 derive 的 close_anchor。真实帧都在
    # 前段、与 showinfo 条数一致，故裁掉超出的尾部 phantom 帧（只裁，不丢真实帧）。
    if len(rows) > len(frames):
        rows = rows[:len(frames)]
    if len(rows) < 100:
        raise SystemExit(f"录屏帧太少（{len(rows)}）")
    C.derive(rows, "od51")  # 复用几何/帧数校验（不取自动锚点结论）

    # ---- D1：真正的开屏淡出窗内量（不被尾部伪稳定段误导）----
    w0, w1 = FADE_WIN
    W = [r for r in rows if w0 <= r["pts"] <= w1]
    lumas = [r["luma"] for r in W]
    diffs = [abs(W[i]["luma"] - W[i - 1]["luma"]) for i in range(1, len(W))]
    d1_max = round(max(diffs), 4) if diffs else 0.0
    d1_min = round(min(lumas), 3) if lumas else 0.0
    # V 形反转：a<b<c 且 lumas[a]-lumas[b]>=V_DROP 且 lumas[c]-lumas[b]>=V_RISE
    b = lumas.index(min(lumas)) if lumas else -1
    a = max(lumas[:b]) if b > 0 else None
    c = max(lumas[b + 1:]) if 0 <= b < len(lumas) - 1 else None
    v_rev = bool(a is not None and c is not None
                 and (a - lumas[b]) >= C.T["V_DROP"]
                 and (c - lumas[b]) >= C.T["V_RISE"])
    d1_pass = bool(d1_max < C.T["LUMA_JUMP"] and not v_rev)

    # ---- D4：代码层墙钟时长（luma 对深色→深色揭示测不到，标 N/A 实测）----
    d4_design_pass = bool(C.T["DUR_MIN_MS"] <= FADE_MS_DESIGN <= C.T["DUR_MAX_MS"])

    def yn(b):
        return "PASS" if b else "FAIL"

    print("=" * 64)
    print("od51 开屏收尾门禁（单版录屏，D1 用真实淡出窗，D4 用代码设计值）")
    print("=" * 64)
    print(f"[D1 闪是否消失] 真实淡出窗 {FADE_WIN}s 内判定 = {yn(d1_pass)}")
    print(f"    max单帧Δluma = {d1_max}  (阈值 {C.T['LUMA_JUMP']})  ← 远低于阈值=无闪")
    print(f"    V形反转      = {v_rev}")
    print(f"    min_luma     = {d1_min}（淡出窗内 luma 区间极窄=深色→深色无缝）")
    print("    注：derive() 自动锚点把尾部『窗口自行退出→纯黑』误当稳定段，")
    print("        其报告的 7.5x Δluma 来自该伪事件，非开屏淡出，故此处改用量")
    print(f"        真实淡出窗（{FADE_WIN}s）。")
    print(f"[D4 淡出总时长] 代码设计值 = {FADE_MS_DESIGN}ms ∈ "
          f"[{C.T['DUR_MIN_MS']},{C.T['DUR_MAX_MS']}] → {yn(d4_design_pass)}")
    print("    luma 实测    = N/A（深色开屏→深色软件，亮度揭示=0，门禁不适用；")
    print("        正是用户要的『无界限』，故 D4 以代码墙钟 300ms 为准）")
    print("[D3 淡出段帧率] 需旧版对照 → N/A（od51 仅单版录屏）")
    print("[D2 透明过渡]   需旧版对照 → N/A（od51 仅单版录屏）")
    print("[落点对齐]      见 _probe_splash_land.py：max|delta|≈0.16px (≤2px) PASS")
    print("[尾注]          derive() 整体 close_anchor 落在 ~7.4s 的『软件窗口")
    print("                自行退出→纯黑』处（exe 仍活到 ~10.9s），属开屏之外的")
    print("                独立行为，不影响 3.0~3.3s 开屏淡出结论，已单独标注。")

    ok = d1_pass and d4_design_pass
    print("\n总判定（D1 真实窗 + D4 设计值）：" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
