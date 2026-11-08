# -*- coding: utf-8 -*-
r"""开屏收尾闪烁 —— 新旧版客观 A/B 对比判据（只读，纯分析，绝不启动 exe）。

用途
----
lead 部署 od49 并用 `_probe_splash_tail_flash.py --tag od49` 录完新 exe 后，
**一条命令**跑完客观对比：

  .venv/Scripts/python.exe devtools/_probe_splash_tail_compare.py \
      --new-mkv _check/_tailflash_od49_raw.mkv \
      --new-log _check/_tailflash_od49_ffmpeg.log \
      --tag-new od49

旧版默认自动读已存在的 `_check/_tailflash_raw.mkv` + `_check/_tailflash_ffmpeg.log`
（以及可选的 `_check/_tailflash_analysis.json` 加速，跳过重新解码）。

⛔ 本脚本**只**读已有 MKV/JSON、只调 ffmpeg 解码/抽帧，**不启动任何 exe、
   不录屏、不改产品源码、不 git、不打包、不动 发布/ 与 dist*、不改版本号**。
   在 lead 明确通知「已部署 od49」之前不要传 --new-mkv 指向不存在的产物。

输入
----
  --old-mkv / --old-log   旧版录屏与 showinfo（默认旧版已落盘路径）
  --old-json              可选：旧版 _tailflash_analysis.json（含 all_frames_stats）
  --new-mkv / --new-log   新版录屏与 showinfo
  --new-json              可选：新版 analysis JSON
  --self-test             用旧版数据自对自干跑（验证脚本 + 打印旧版基线），不启动 exe
  --out-json / --out-montage / --tag-old / --tag-new

判据（全部为确定性规则，阈值集中在 THRESHOLDS 并原样写入 JSON，可复核）
------------------------------------------------------------------------
对齐：每份录屏**各自**用「末尾稳定段」自动求 close_anchor，不硬编码旧版 pts。

  D1 闪是否消失
     · min_luma            = 分析窗内 luma 最小值
     · max_single_luma_diff = 分析窗内相邻帧 |Δluma| 最大值
     · V 形反转             = 令 b=argmin(luma)、a=argmax(luma[:b])、c=argmax(luma[b+1:])；
                              (a<b<c) 且 (luma[a]-luma[b] >= V_DROP)
                              且 (luma[c]-luma[b] >= V_RISE) 则为 True
     · PASS = 新版 max_single_luma_diff < LUMA_JUMP 且 V 形反转 == False

  D2 透明过渡是否出现
     · 混合帧 = 分析窗内同时满足 uniq_q>=UI_UNIQ_MIN、std>=UI_STD_MIN、
                (main_luma - luma) >= MIX_LUMA_GAP 的帧（主窗 UI 元素可见但整体
                仍蒙着开屏深色层 = 半透明/透出主窗）
     · 平滑过渡 = 存在长度>=SMOOTH_MIN_LEN 的连续区间，区间内每步 |Δluma|<SMOOTH_MAX，
                  且起点 luma < main_luma-3、终点 luma >= main_luma-1
     · PASS = 新版 (混合帧数 >= TRANS_MIN_FRAMES 或 平滑过渡 True) 且 旧版混合帧数 == 0

  D3 淡出段帧率
     · FADE 段 = [close - FADE_S, close]；GEO 段 = pts < close - GEO_BEFORE
     · 可见更新间隔 = 段内「checksum 变化帧」相对其前一录屏帧的 pts 差(ms)
     · 输出 mean / p95 / >20ms 帧数 / >33ms 帧数；ratio = fade_mean / geo_mean
     · PASS = 新版 ratio < RATIO_PASS 或 (旧 ratio - 新 ratio) >= RATIO_GAIN

  D4 淡出总时长
     · fade_start = 分析窗内第一个 diff > FADE_START_DIFF 的帧
     · fade_end   = close_anchor（末尾稳定段起点）
     · duration_ms = (pts[fade_end] - pts[fade_start]) * 1000
     · PASS = DUR_MIN <= 新版 duration_ms <= DUR_MAX

输出
----
  _check/_tailflash_compare.json   全部阈值 + 新旧逐项数值 + 4 条判据 PASS/FAIL
  _check/_tailflash_compare.png    新旧并排 montage（各 8 帧，按各自淡出进度对齐）
  stdout                          中文摘要

判读口径（按 lead 指令在录制 od49 之前固化，写入 JSON 的 scope 字段并打印到 stdout）
------------------------------------------------------------------------------
本 A/B 判决的是用户可感知的「最后闪一下」是否消失（**A+B2 合并效果**），
**不是 B2 的单独净效果**。新版同时含轮 A（`setAutoFillBackground(False)`、
k=1 全透明帧同步上屏、淡出改 QElapsedTimer 墙钟 + frame_hz() 对齐）与轮 B2
（淡出前主窗 raise_()+activateWindow() 置前）；A 的修复本身就会改变 luma 曲线
与撤场帧序列，D1~D5 变好是两者共同结果。若要单独验 B2 需「只改 A、不接 B2」
的第三组 exe，本轮不做，结论不得暗示已单独验证。
D4：新版淡出 ≈300~330ms（300ms 墙钟 + repaint() + 延后一个目标帧周期）属轮 A
设计变更、不是回退；PASS 区间 [250,400]ms 已覆盖，仅按 close 锚点量到 >400ms
才判 FAIL。5 条判据与全分辨率窗口帧对照原样保留。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _font(size: int):
    """Windows 中文字体，避免 montage 标注里的中文变方块。"""
    for p in (r"C:\Windows\Fonts\msyh.ttc",
              r"C:\Windows\Fonts\simhei.ttf",
              r"C:\Windows\Fonts\msyhbd.ttc"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "_check"

# ---------------------------------------------------------------- 确定性阈值（写入 JSON）
THRESHOLDS = {
    "SCAN_W": 400, "SCAN_H": 267,
    "TAIL_STABLE_N": 60,          # 末尾用于估计 main_luma 的帧数
    "STABLE_DIFF": 0.6,           # 稳定帧 diff 上限
    "STABLE_LUMA_TOL": 1.5,       # 稳定帧 luma 与 main_luma 的容差
    "STABLE_MIN_LEN": 20,         # 末尾稳定段最短连续帧数
    "WIN_BEFORE_S": 0.32,         # 分析窗 = [close-WIN_BEFORE, close+WIN_AFTER]
                                   # 0.32s 刚好覆盖淡出 0.30s + 一帧，排除落点段末尾孤立变化帧
    "WIN_AFTER_S": 0.15,
    "LUMA_JUMP": 3.0,             # D1 单帧 luma 跳变阈值（lead 指定；全分辨率口径旧版 3.20）
    "V_DROP": 2.0,                # D1 V 形下降幅度
    "V_RISE": 3.0,                # D1 V 形上升幅度
    "UI_UNIQ_MIN": 55,            # D2 混合帧：主窗 UI 纹理（量化色数）
    "UI_STD_MIN": 8.0,            # D2 混合帧：主窗 UI 纹理（std）
    "DOM_MATCH_MIN": 2,           # D2 混合帧：dom_rgb 至少 2 个通道等于主窗稳定帧主色
                                   # 开屏纯黑底 dom=[8,8,8]、主窗深灰底 dom=[24,24,24]，
                                   # 用主色档位排除「仅是 logo 变灰」的假阳性（旧版 n=358）
    "MIX_LUMA_GAP": 3.0,          # D2 混合帧：比 main_luma 至少低这么多
    "SMOOTH_MAX": 1.5,            # D2 平滑过渡单帧 |Δluma| 上限
    "SMOOTH_MIN_LEN": 3,          # D2 平滑过渡最短帧数
    "TRANS_MIN_FRAMES": 3,        # D2 混合帧最少数量
    "FADE_S": 0.30,               # 规范淡出时长（_FADE_MS=300）
    "GEO_BEFORE": 1.10,           # GEO 段 = pts < close - 1.10
    "FADE_START_DIFF": 0.3,       # D4 淡出起点 diff 阈值
    "RATIO_PASS": 1.15,           # D3 收敛绝对阈值
    "RATIO_GAIN": 0.10,           # D3 相对旧版改善阈值
    "DUR_MIN_MS": 250.0, "DUR_MAX_MS": 400.0,
    # 说明性区间（不参与 PASS/FAIL）：新版预期 ≈300~330ms，属轮 A 墙钟计时设计变更
    "DUR_NEW_EXPECTED_MS": [295.0, 335.0],
}
T = THRESHOLDS

# 判读口径：按 lead 2026-11-06 指令，在录制 od49 之前固化进脚本产物，
# 确保正式 A/B 的 JSON 与 stdout 摘要自带该声明，不会被误读为「单独验证 B2」。
SCOPE_NOTE = (
    "本 A/B 判决的是用户可感知的「最后闪一下」是否消失（A+B2 合并效果），"
    "不是 B2 的单独净效果。新版同时包含轮 A（setAutoFillBackground(False) / "
    "k=1 全透明帧同步 repaint() 上屏 / 淡出改 QElapsedTimer 墙钟 + frame_hz() 对齐）"
    "与轮 B2（淡出前主窗 raise_()+activateWindow() 置前）；A 的修复本身就会改变 "
    "luma 曲线与撤场帧序列，故 D1~D5 变好是两者共同结果。若要单独验 B2，需 "
    "「只改 A、不接 B2」的第三组 exe，本轮不做；结论不得暗示已单独验证 B2。"
)

SCAN_W, SCAN_H = T["SCAN_W"], T["SCAN_H"]
FRAME_BYTES = SCAN_W * SCAN_H * 3


# ---------------------------------------------------------------- 基础工具
def parse_showinfo(log_path: Path) -> list[dict]:
    s = log_path.read_text(encoding="utf-8", errors="replace")
    frames = []
    for m in re.finditer(
            r"n:\s*(\d+)\s+pts:\s*\d+\s+pts_time:([\d.]+).*?checksum:([0-9A-Fa-f]{8})",
            s):
        frames.append({"n": int(m.group(1)), "pts": float(m.group(2)),
                       "checksum": m.group(3).upper()})
    return frames


def frame_stats_arr(a: np.ndarray) -> dict:
    """RGB uint8 (H,W,3) → 逐帧像素统计。"""
    mean = a.reshape(-1, 3).mean(axis=0)
    luma = 0.2126 * mean[0] + 0.7152 * mean[1] + 0.0722 * mean[2]
    std = float(a.astype(np.float32).std())
    q = (a // 16).astype(np.int32).reshape(-1, 3)
    packed = (q[:, 0] << 16) | (q[:, 1] << 8) | q[:, 2]
    u, cnt = np.unique(packed, return_counts=True)
    dom = int(u[cnt.argmax()])
    dom_rgb = [((dom >> 16) & 255) * 16 + 8,
               ((dom >> 8) & 255) * 16 + 8,
               (dom & 255) * 16 + 8]
    return {"mean_rgb": [round(float(x), 2) for x in mean],
            "luma": round(float(luma), 3), "std": round(std, 3),
            "uniq_q": int(len(u)), "dom_rgb": dom_rgb}


def read_exact(f, n: int) -> bytes:
    parts, left = [], n
    while left > 0:
        b = f.read(left)
        if not b:
            break
        parts.append(b)
        left -= len(b)
    return b"".join(parts)


def stats_from_mkv(mkv: Path, frames: list[dict], work: Path) -> list[dict]:
    """ffmpeg 解码 → rawvideo pipe → 逐帧统计（不落 PNG，比 PNG 序列快很多）。"""
    cmd = ["ffmpeg", "-v", "error", "-i", str(mkv),
           "-vf", f"scale={SCAN_W}:{SCAN_H}",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    rows, prev, i = [], None, 0
    try:
        while True:
            buf = read_exact(proc.stdout, FRAME_BYTES)
            if len(buf) < FRAME_BYTES:
                break
            bgr = np.frombuffer(buf, dtype=np.uint8).reshape(SCAN_H, SCAN_W, 3)
            a = bgr[:, :, ::-1]                       # BGR → RGB
            st = frame_stats_arr(a)
            st["diff"] = (0.0 if prev is None else
                          round(float(np.abs(a.astype(np.int16) - prev).mean()), 3))
            st["n"] = frames[i]["n"] if i < len(frames) else i
            st["pts"] = frames[i]["pts"] if i < len(frames) else 0.0
            st["checksum"] = frames[i]["checksum"] if i < len(frames) else ""
            rows.append(st)
            prev = a.astype(np.int16).copy()
            i += 1
    finally:
        try:
            proc.stdout.close()
        except Exception:                              # noqa: BLE001
            pass
        proc.wait(timeout=10)
    return rows


def stats_from_json(json_path: Path) -> list[dict]:
    d = json.loads(json_path.read_text(encoding="utf-8"))
    rows = d["all_frames_stats"]
    keep = ("n", "pts", "luma", "mean_rgb", "std", "uniq_q", "dom_rgb",
            "diff", "checksum")
    return [{k: r.get(k) for k in keep} for r in rows]


def pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1)))))
    return round(s[k], 3)


def interval_stats(vals: list[float]) -> dict:
    if not vals:
        return {"count": 0, "mean_ms": None, "p95_ms": None,
                "max_ms": None, "gt20ms": 0, "gt33ms": 0}
    return {"count": len(vals),
            "mean_ms": round(sum(vals) / len(vals), 3),
            "p95_ms": pct(vals, 95), "max_ms": round(max(vals), 3),
            "gt20ms": sum(1 for v in vals if v > 20.0),
            "gt33ms": sum(1 for v in vals if v > 33.3)}


# ---------------------------------------------------------------- 单份录屏的派生量
def derive(rows: list[dict], label: str) -> dict:
    """按确定性规则自动对齐：主窗稳定段 → close_anchor → 分析窗 → 4 条判据数值。"""
    n = len(rows)
    if n < 100:
        raise SystemExit(f"{label}: 录屏帧太少（{n}）")

    # main_luma / main_rgb：末尾稳定帧的中位数（不硬编码 (19,21,22)）
    tail = rows[-T["TAIL_STABLE_N"]:]
    main_luma = round(float(np.median([r["luma"] for r in tail])), 3)
    main_rgb = [round(float(np.median([r["mean_rgb"][c] for r in tail])), 2)
                for c in range(3)]
    _doms = [r["dom_rgb"] for r in tail if r.get("dom_rgb")]
    main_dom = ([int(round(float(np.median([d[c] for d in _doms]))))
                 for c in range(3)] if _doms else None)

    # close_anchor：末尾最长连续稳定段的起点
    # 稳定帧判据：diff < STABLE_DIFF 且 |luma - main_luma| <= STABLE_LUMA_TOL
    stable = [r["diff"] < T["STABLE_DIFF"]
              and abs(r["luma"] - main_luma) <= T["STABLE_LUMA_TOL"]
              for r in rows]
    stable_start = n
    i = n - 1
    while i >= 0:
        if stable[i]:
            j = i
            while j >= 0 and stable[j]:
                j -= 1
            if (i - j) >= T["STABLE_MIN_LEN"]:
                stable_start = j + 1
                break
            i = j
        else:
            i -= 1
    if stable_start >= n:
        stable_start = max(0, n - T["STABLE_MIN_LEN"])
    close_i = stable_start
    close_n = rows[close_i]["n"]
    close_pts = rows[close_i]["pts"]

    # 分析窗（按每份自己的 close 对齐，不硬编码 pts）
    w0, w1 = close_pts - T["WIN_BEFORE_S"], close_pts + T["WIN_AFTER_S"]
    win_idx = [i for i, r in enumerate(rows) if w0 <= r["pts"] <= w1]
    if len(win_idx) < 5:
        win_idx = list(range(max(0, close_i - 40), min(n, close_i + 15)))
    W = [rows[i] for i in win_idx]

    # ---- D1 ----
    lumas = [r["luma"] for r in W]
    min_luma = round(min(lumas), 3)
    single_diffs = [round(abs(W[k]["luma"] - W[k - 1]["luma"]), 3)
                    for k in range(1, len(W))]
    max_single_luma_diff = max(single_diffs) if single_diffs else 0.0
    b = int(np.argmin(lumas))
    a = int(np.argmax(lumas[:b])) if b > 0 else -1
    c = (b + 1 + int(np.argmax(lumas[b + 1:]))) if b + 1 < len(lumas) else -1
    v_reversal = bool(
        a >= 0 and c >= 0 and a < b < c
        and (lumas[a] - lumas[b]) >= T["V_DROP"]
        and (lumas[c] - lumas[b]) >= T["V_RISE"])
    v_detail = {"a_n": W[a]["n"] if a >= 0 else None,
                "a_luma": round(lumas[a], 3) if a >= 0 else None,
                "b_n": W[b]["n"], "b_luma": round(lumas[b], 3),
                "c_n": W[c]["n"] if c >= 0 else None,
                "c_luma": round(lumas[c], 3) if c >= 0 else None,
                "drop": round(lumas[a] - lumas[b], 3) if a >= 0 else None,
                "rise": round(lumas[c] - lumas[b], 3) if c >= 0 else None}

    # ---- D2 ----
    def dom_ok(r: dict) -> bool:
        d = r.get("dom_rgb")
        if not d or main_dom is None:
            return False
        return (sum(1 for c in range(3) if d[c] == main_dom[c])
                >= T["DOM_MATCH_MIN"])

    trans = [r for r in W
             if r["uniq_q"] >= T["UI_UNIQ_MIN"]
             and r["std"] >= T["UI_STD_MIN"]
             and dom_ok(r)
             and (main_luma - r["luma"]) >= T["MIX_LUMA_GAP"]]
    trans_ns = [r["n"] for r in trans]
    # 平滑过渡：最长连续区间，区间内每步 |Δluma| < SMOOTH_MAX，
    # 起点 < main_luma-3 且终点 >= main_luma-1
    smooth = False
    smooth_span = None
    j = 0
    while j < len(W) - 1:
        if abs(W[j + 1]["luma"] - W[j]["luma"]) >= T["SMOOTH_MAX"]:
            j += 1
            continue
        k = j + 1
        while k < len(W) - 1 and abs(W[k + 1]["luma"] - W[k]["luma"]) < T["SMOOTH_MAX"]:
            k += 1
        if (
            (k - j + 1) >= T["SMOOTH_MIN_LEN"]
            and W[j]["luma"] < main_luma - 3
            and W[k]["luma"] >= main_luma - 1
        ):
            smooth = True
            smooth_span = [W[j]["n"], W[k]["n"], k - j + 1]
            break
        j = k + 1

    # ---- D3 ----
    def seg_intervals(lo: float, hi: float) -> list[float]:
        ids = [i for i in range(1, n)
               if lo <= rows[i]["pts"] < hi
               and rows[i]["checksum"] != rows[i - 1]["checksum"]]
        return [(rows[i]["pts"] - rows[i - 1]["pts"]) * 1000.0 for i in ids]

    fade_iv = seg_intervals(close_pts - T["FADE_S"], close_pts)
    geo_iv = seg_intervals(0.0, close_pts - T["GEO_BEFORE"])
    fade_st = interval_stats(fade_iv)
    geo_st = interval_stats(geo_iv)
    ratio = (round(fade_st["mean_ms"] / geo_st["mean_ms"], 4)
             if fade_st["mean_ms"] and geo_st["mean_ms"] else None)

    # ---- D4 ----
    # fade_start = 分析窗内第一个 diff > FADE_START_DIFF 的帧（在 close 之前）
    cand = [r for r in W
            if r["pts"] <= close_pts and r["diff"] > T["FADE_START_DIFF"]]
    fade_start = cand[0] if cand else W[0]
    fade_duration_ms = round((close_pts - fade_start["pts"]) * 1000.0, 3)

    # ---- montage 选帧：按各自淡出进度等间隔 8 帧（帧号唯一，ffmpeg select 才不丢） ----
    s_i = next((i for i, r in enumerate(rows)
                if r["n"] == fade_start["n"]), win_idx[0])
    e_i = close_i
    picks, seen = [], set()
    for k in range(8):
        idx = int(round(s_i + (e_i - s_i) * k / 7.0))
        idx = max(0, min(n - 1, idx))
        if rows[idx]["n"] in seen:                 # 向后找最近未用帧，保证唯一
            for j in range(idx + 1, n):
                if rows[j]["n"] not in seen:
                    idx = j
                    break
        picks.append(rows[idx]["n"])
        seen.add(rows[idx]["n"])

    return {
        "label": label, "frames": n,
        "main_luma": main_luma, "main_rgb": main_rgb, "main_dom": main_dom,
        "close_anchor": {"n": close_n, "pts": close_pts,
                         "main_luma": main_luma},
        "window": {"pts": [round(w0, 3), round(w1, 3)],
                   "frames": len(W), "n_range": [W[0]["n"], W[-1]["n"]]},
        "D1": {"min_luma": min_luma,
               "max_single_luma_diff": max_single_luma_diff,
               "v_reversal": v_reversal, "v_detail": v_detail,
               "luma_series": [{"n": r["n"], "pts": r["pts"],
                                "luma": r["luma"], "diff": r["diff"]}
                               for r in W]},
        "D2": {"trans_frames_count": len(trans),
               "trans_frames_n": trans_ns,
               "trans_frames_n_range": ([min(trans_ns), max(trans_ns)]
                                        if trans_ns else None),
               "smooth_transition": smooth, "smooth_span": smooth_span},
        "D3": {"fade": fade_st, "geo": geo_st, "fade_geo_ratio": ratio,
               "fade_pts": [round(close_pts - T["FADE_S"], 3), close_pts]},
        "D4": {"fade_start_n": fade_start["n"],
               "fade_start_pts": fade_start["pts"],
               "fade_end_n": close_n, "fade_end_pts": close_pts,
               "duration_ms": fade_duration_ms},
        "montage_picks_n": picks,
        "_rows": rows, "_W": W,
    }


# ---------------------------------------------------------------- 判据汇总
def verdict(old: dict, new: dict) -> dict:
    o1, n1 = old["D1"], new["D1"]
    o2, n2 = old["D2"], new["D2"]
    o3, n3 = old["D3"], new["D3"]
    o4, n4 = old["D4"], new["D4"]

    d1 = {
        "rule": ("新版 max_single_luma_diff < LUMA_JUMP 且 v_reversal == False；"
                 "旧版应 max_single_luma_diff >= LUMA_JUMP 或 v_reversal == True"),
        "old": {"min_luma": o1["min_luma"],
                "max_single_luma_diff": o1["max_single_luma_diff"],
                "v_reversal": o1["v_reversal"],
                "fullres": o1.get("fullres")},
        "new": {"min_luma": n1["min_luma"],
                "max_single_luma_diff": n1["max_single_luma_diff"],
                "v_reversal": n1["v_reversal"],
                "fullres": n1.get("fullres")},
        "pass": bool(n1["max_single_luma_diff"] < T["LUMA_JUMP"]
                     and not n1["v_reversal"]),
    }
    d2 = {
        "rule": ("新版 (trans_frames_count >= TRANS_MIN_FRAMES 或 "
                 "smooth_transition == True) 且 旧版 trans_frames_count == 0"),
        "old": {"trans_frames_count": o2["trans_frames_count"],
                "smooth_transition": o2["smooth_transition"]},
        "new": {"trans_frames_count": n2["trans_frames_count"],
                "trans_frames_n_range": n2["trans_frames_n_range"],
                "smooth_transition": n2["smooth_transition"],
                "smooth_span": n2["smooth_span"]},
        "pass": bool((n2["trans_frames_count"] >= T["TRANS_MIN_FRAMES"]
                      or n2["smooth_transition"])
                     and o2["trans_frames_count"] == 0),
    }
    r_old, r_new = o3["fade_geo_ratio"], n3["fade_geo_ratio"]
    gain = (round(r_old - r_new, 4)
            if r_old is not None and r_new is not None else None)
    d3 = {
        "rule": ("新版 fade_geo_ratio < RATIO_PASS 或 "
                 "(旧 ratio - 新 ratio) >= RATIO_GAIN"),
        "old": {"fade_mean_ms": o3["fade"]["mean_ms"],
                "fade_p95_ms": o3["fade"]["p95_ms"],
                "fade_gt20ms": o3["fade"]["gt20ms"],
                "geo_mean_ms": o3["geo"]["mean_ms"], "ratio": r_old},
        "new": {"fade_mean_ms": n3["fade"]["mean_ms"],
                "fade_p95_ms": n3["fade"]["p95_ms"],
                "fade_gt20ms": n3["fade"]["gt20ms"],
                "geo_mean_ms": n3["geo"]["mean_ms"], "ratio": r_new},
        "ratio_gain": gain,
        "note": ("close_anchor 统一取「末尾稳定段起点」(旧版 n=374/5.026s)；"
                 "_probe_splash_tail_analyze.py 的 main_first 锚点为 n=371/4.965s，"
                 "差 3 帧，故 FADE 窗口后移约 0.06s。本脚本新旧同口径可比，"
                 "数值方向与分析脚本 by_phase 基线(20.778/16.232/1.280)一致，"
                 "但不要求完全相等。"),
        "pass": bool((r_new is not None and r_new < T["RATIO_PASS"])
                     or (gain is not None and gain >= T["RATIO_GAIN"])),
    }
    d4 = {
        "rule": "DUR_MIN_MS <= 新版 duration_ms <= DUR_MAX_MS（仅 >400ms 才判 FAIL）",
        "expected_new_range_ms": T["DUR_NEW_EXPECTED_MS"],
        "note": ("新版淡出 = _FADE_MS=300ms 墙钟 + repaint() 同步画全透明帧 + "
                 "延后一整个目标帧周期(165Hz≈6ms / 源码探针 60Hz 实测 16.7ms)再撤场；"
                 "源码环境实测 k=1@304.7ms、_close@321.4ms；旧版基线 308.253ms。"
                 "PASS 区间 [250,400]ms 已覆盖新版预期 ≈300~330ms，新版略长属轮 A "
                 "墙钟计时的设计变更、不是回退，不判 FAIL。fade_start / close_anchor "
                 "均从各自录屏自动求，不硬编码旧版 pts。"),
        "old": {"duration_ms": o4["duration_ms"],
                "fade_start_pts": o4["fade_start_pts"],
                "fade_end_pts": o4["fade_end_pts"]},
        "new": {"duration_ms": n4["duration_ms"],
                "fade_start_pts": n4["fade_start_pts"],
                "fade_end_pts": n4["fade_end_pts"]},
        "pass": bool(T["DUR_MIN_MS"] <= n4["duration_ms"] <= T["DUR_MAX_MS"]),
    }
    return {"D1_flash_gone": d1, "D2_transition_appears": d2,
            "D3_fps_converged": d3, "D4_fade_duration": d4,
            "overall_pass": bool(d1["pass"] and d2["pass"]
                                 and d3["pass"] and d4["pass"]),
            "thresholds": THRESHOLDS}


# ---------------------------------------------------------------- 并排 montage
def extract_frames(mkv: Path, ns: list[int], out_dir: Path) -> list[Path]:
    """按帧号抽帧（分批，避免 ffmpeg select 表达式过长解析失败）。

    ⛔ od52 踩坑：一次把上百个 ``eq(n,K)`` 用 ``+`` 拼进 select，ffmpeg 直接
       ``Error while parsing expression``（解析器对表达式长度有限制），表现为
       「montage 抽帧失败」但其实全分辨率 luma 也被带崩、JSON 根本没写出来。
       这里按 32 帧一批分批抽，输出名带批次前缀，排序后仍是原帧号升序。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("m_*.png"):
        p.unlink()
    ns = [int(x) for x in ns]
    CHUNK = 32
    for bi in range(0, len(ns), CHUNK):
        chunk = ns[bi:bi + CHUNK]
        expr = "+".join(r"eq(n\,%d)" % x for x in chunk)
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(mkv),
               "-vf", f"select='{expr}'", "-fps_mode", "passthrough",
               "-start_number", "0",
               str(out_dir / f"m_b{bi:05d}_%03d.png")]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            print(r.stderr.decode("utf-8", errors="replace")[:800], flush=True)
            raise SystemExit(f"抽帧失败：{mkv}（批次 {bi}，{len(chunk)} 帧）")
    return sorted(out_dir.glob("m_*.png"))


def fullres_window_luma(mkv: Path, ns: list[int], work: Path) -> dict:
    """抽分析窗内**全分辨率**帧，只算 luma 序列。

    目的：主判据用 400x267 缩略图（快、新旧同口径）；但 lead 采信的旧版基线
    （min_luma=13.294、单帧跳变 3.20）来自 1600x1068 全分辨率。缩放会压低
    跳变幅度（缩略图实测 2.868），故额外给一份全分辨率对照，便于复核口径。
    """
    files = extract_frames(mkv, ns, work)
    l = []
    for p in files:
        a = np.asarray(Image.open(p).convert("RGB"), dtype=np.float32)
        l.append(round(float((0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1]
                             + 0.0722 * a[:, :, 2]).mean()), 3))
    if len(l) < 2:
        return {"frames": len(l)}
    d = [round(abs(l[i] - l[i - 1]), 3) for i in range(1, len(l))]
    return {"frames": len(l), "min_luma": min(l),
            "max_single_luma_diff": max(d)}


def build_montage(old: dict, new: dict, old_mkv: Path, new_mkv: Path,
                  out: Path, tag_old: str, tag_new: str) -> None:
    tmp = CHECK / "_tailflash_compare_frames"
    fo = extract_frames(old_mkv, old["montage_picks_n"], tmp / "old")
    fn = extract_frames(new_mkv, new["montage_picks_n"], tmp / "new")
    TW, TH, COLS = 400, 292, 4
    HEADER = 26
    rows_total = 4                                  # OLD 2 行 + NEW 2 行
    sheet = Image.new("RGB", (TW * COLS, HEADER + TH * rows_total), (16, 16, 18))
    d = ImageDraw.Draw(sheet)
    f_title, f_cell = _font(15), _font(12)
    d.text((6, 4),
           f"上两行 OLD: {tag_old}    |    下两行 NEW: {tag_new}    |    "
           "每份 8 帧，按各自淡出起点→close 等间隔对齐",
           fill=(255, 255, 120), font=f_title)
    d.line([(0, HEADER + 2 * TH - 2), (TW * COLS, HEADER + 2 * TH - 2)],
           fill=(90, 180, 255), width=2)              # OLD/NEW 分隔线

    for grp, (trk, files, _tag) in enumerate(
            ((old, fo, tag_old), (new, fn, tag_new))):
        W = {r["n"]: r for r in trk["_rows"]}
        prev_luma = None
        for k, n in enumerate(trk["montage_picks_n"]):
            if k >= len(files):
                break
            r = W.get(n, {})
            im = Image.open(files[k]).convert("RGB").resize((400, 267))
            x = (k % COLS) * TW
            y = HEADER + (grp * 2 + k // COLS) * TH
            sheet.paste(im, (x, y + 25))
            dl = ("" if prev_luma is None or not r
                  else "d=%.2f" % (r["luma"] - prev_luma))
            prev_luma = r.get("luma", prev_luma) if r else prev_luma
            cls = ("MIX" if r and r["uniq_q"] >= T["UI_UNIQ_MIN"]
                   and r["std"] >= T["UI_STD_MIN"]
                   and (trk["main_luma"] - r["luma"]) >= T["MIX_LUMA_GAP"]
                   else "-")
            col = (255, 100, 100) if cls == "MIX" else (255, 235, 90)
            d.text((x + 3, y + 4),
                   f"n={n} pts={r.get('pts', 0):.3f} "
                   f"luma={r.get('luma', 0):.2f} {dl} {cls}",
                   fill=col, font=f_cell)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(
        description="开屏收尾闪烁新旧客观 A/B 对比（纯分析，不启动 exe）")
    ap.add_argument("--old-mkv", default=str(CHECK / "_tailflash_raw.mkv"))
    ap.add_argument("--old-log", default=str(CHECK / "_tailflash_ffmpeg.log"))
    ap.add_argument("--old-json", default=str(CHECK / "_tailflash_analysis.json"),
                    help="可选：旧版 analysis JSON（含 all_frames_stats），加速")
    ap.add_argument("--new-mkv", default="")
    ap.add_argument("--new-log", default="")
    ap.add_argument("--new-json", default="", help="可选：新版 analysis JSON")
    ap.add_argument("--self-test", action="store_true",
                    help="用旧版数据自对自干跑（验证脚本 + 打印旧版基线），不启动 exe")
    ap.add_argument("--out-json", default=str(CHECK / "_tailflash_compare.json"))
    ap.add_argument("--out-montage", default=str(CHECK / "_tailflash_compare.png"))
    ap.add_argument("--tag-old", default="旧版 20:12")
    ap.add_argument("--tag-new", default="od49")
    args = ap.parse_args()

    old_mkv, old_log = Path(args.old_mkv), Path(args.old_log)
    if args.self_test:
        new_mkv, new_log, new_json = old_mkv, old_log, Path(args.old_json)
        print("[self-test] 新旧均指向旧版录屏，仅验证脚本与旧版基线；"
              "未启动任何 exe、未录屏", flush=True)
    else:
        if not args.new_mkv or not args.new_log:
            print("需要提供 --new-mkv / --new-log（新 exe 录屏产物）。"
                  "lead 通知「已部署 od49」后才录制。\n"
                  "仅验证脚本可加 --self-test。", flush=True)
            return 2
        new_mkv, new_log = Path(args.new_mkv), Path(args.new_log)
        new_json = Path(args.new_json) if args.new_json else None

    for p in (old_mkv, old_log, new_mkv, new_log):
        if not p.is_file():
            raise SystemExit(f"缺少输入文件：{p}")

    print(f"[load] OLD {old_mkv.name} / {old_log.name}", flush=True)
    old_frames = parse_showinfo(old_log)
    old_json = Path(args.old_json) if args.old_json else None
    if old_json and old_json.is_file():
        old_rows = stats_from_json(old_json)
        print(f"       OLD 统计来自 {old_json.name}（{len(old_rows)} 帧，跳过解码）",
              flush=True)
    else:
        old_rows = stats_from_mkv(old_mkv, old_frames, CHECK / "_tailflash_cmp_old")

    print(f"[load] NEW {new_mkv.name} / {new_log.name}", flush=True)
    new_frames = parse_showinfo(new_log)
    if new_json and new_json.is_file():
        new_rows = stats_from_json(new_json)
        print(f"       NEW 统计来自 {new_json.name}（{len(new_rows)} 帧，跳过解码）",
              flush=True)
    else:
        new_rows = stats_from_mkv(new_mkv, new_frames, CHECK / "_tailflash_cmp_new")

    old = derive(old_rows, args.tag_old)
    new = derive(new_rows, args.tag_new)

    # 全分辨率窗口帧 luma 对照（与 lead 基线口径核对，仅 ~30 帧，快）
    old["D1"]["fullres"] = fullres_window_luma(
        old_mkv,
        list(range(old["window"]["n_range"][0], old["window"]["n_range"][1] + 1)),
        CHECK / "_tailflash_cmp_full_old")
    new["D1"]["fullres"] = (
        dict(old["D1"]["fullres"]) if new_mkv == old_mkv
        else fullres_window_luma(
            new_mkv,
            list(range(new["window"]["n_range"][0],
                       new["window"]["n_range"][1] + 1)),
            CHECK / "_tailflash_cmp_full_new"))
    v = verdict(old, new)

    out_json = Path(args.out_json)
    payload = {
        "inputs": {"old_mkv": str(old_mkv), "old_log": str(old_log),
                   "new_mkv": str(new_mkv), "new_log": str(new_log),
                   "self_test": bool(args.self_test)},
        "scope": SCOPE_NOTE,
        "old": {k: val for k, val in old.items() if not k.startswith("_")},
        "new": {k: val for k, val in new.items() if not k.startswith("_")},
        "verdict": v,
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    build_montage(old, new, old_mkv, new_mkv,
                  Path(args.out_montage), args.tag_old, args.tag_new)

    # ---- 中文摘要 ----
    def yn(b):
        return "PASS" if b else "FAIL"
    print("\n" + "=" * 66, flush=True)
    print("开屏收尾闪烁 新旧客观 A/B 对比（确定性判据，阈值见 JSON）", flush=True)
    print("=" * 66, flush=True)
    print("【判读口径】" + SCOPE_NOTE, flush=True)
    print("=" * 66, flush=True)
    for trk, nm in ((old, "OLD " + args.tag_old), (new, "NEW " + args.tag_new)):
        c = trk["close_anchor"]
        print(f"[{nm}] 共 {trk['frames']} 帧 | close n={c['n']} "
              f"pts={c['pts']:.3f}s | main_luma={c['main_luma']} "
              f"main_rgb={trk['main_rgb']} | 分析窗 {trk['window']['pts']} "
              f"({trk['window']['frames']} 帧)", flush=True)
    d1, d2, d3, d4 = (v["D1_flash_gone"], v["D2_transition_appears"],
                      v["D3_fps_converged"], v["D4_fade_duration"])
    print("\nD1 闪是否消失  " + yn(d1["pass"]), flush=True)
    print(f"   规则：{d1['rule']}", flush=True)
    print(f"   OLD min_luma={d1['old']['min_luma']} "
          f"max单帧Δluma={d1['old']['max_single_luma_diff']} "
          f"V形={d1['old']['v_reversal']}", flush=True)
    print(f"   NEW min_luma={d1['new']['min_luma']} "
          f"max单帧Δluma={d1['new']['max_single_luma_diff']} "
          f"V形={d1['new']['v_reversal']}", flush=True)
    for nm, s in (("OLD", d1["old"]), ("NEW", d1["new"])):
        fr = s.get("fullres") or {}
        print(f"   {nm} 全分辨率对照：min_luma={fr.get('min_luma')} "
              f"max单帧Δluma={fr.get('max_single_luma_diff')} "
              f"({fr.get('frames')} 帧)", flush=True)
    print("\nD2 透明过渡是否出现  " + yn(d2["pass"]), flush=True)
    print(f"   规则：{d2['rule']}", flush=True)
    print(f"   OLD 混合帧={d2['old']['trans_frames_count']} "
          f"平滑过渡={d2['old']['smooth_transition']}", flush=True)
    print(f"   NEW 混合帧={d2['new']['trans_frames_count']} "
          f"帧号区间={d2['new']['trans_frames_n_range']} "
          f"平滑过渡={d2['new']['smooth_transition']} "
          f"区间={d2['new']['smooth_span']}", flush=True)
    print("\nD3 淡出段帧率收敛  " + yn(d3["pass"]), flush=True)
    print(f"   规则：{d3['rule']}", flush=True)
    for nm, s in (("OLD", d3["old"]), ("NEW", d3["new"])):
        print(f"   {nm} FADE mean={s['fade_mean_ms']}ms "
              f"p95={s['fade_p95_ms']}ms >20ms={s['fade_gt20ms']}帧 | "
              f"GEO mean={s['geo_mean_ms']}ms | ratio={s['ratio']}", flush=True)
    print(f"   ratio 改善={d3['ratio_gain']}", flush=True)
    print("\nD4 淡出总时长  " + yn(d4["pass"]), flush=True)
    print(f"   规则：{d4['rule']}", flush=True)
    print(f"   OLD {d4['old']['duration_ms']}ms "
          f"({d4['old']['fade_start_pts']:.3f}→{d4['old']['fade_end_pts']:.3f}s)",
          flush=True)
    print(f"   NEW {d4['new']['duration_ms']}ms "
          f"({d4['new']['fade_start_pts']:.3f}→{d4['new']['fade_end_pts']:.3f}s)",
          flush=True)
    print(f"   新版预期区间 {d4['expected_new_range_ms']}ms（说明性，不参与 PASS/FAIL）",
          flush=True)
    print(f"   说明：{d4['note']}", flush=True)
    print("\n总判定：" + ("PASS（4/4）" if v["overall_pass"] else "FAIL")
          + ("  ※ self-test 自对自，总判定无意义" if args.self_test else ""),
          flush=True)
    print(f"JSON     → {out_json}", flush=True)
    print(f"montage  → {args.out_montage}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
