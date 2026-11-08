# -*- coding: utf-8 -*-
r"""开屏收尾闪烁诊断 —— 分析器（只读，不 import 任何产品源码模块）。

输入（由 _probe_splash_tail_flash.py 录制）：
  _check/_tailflash_raw.mkv          ffmpeg ddagrab 165fps 目标的全屏合成录屏
  _check/_tailflash_ffmpeg.log       showinfo：每帧 n / pts_time / checksum
  _check/_tailflash_luma.jsonl       GDI BitBlt 高频亮度采样（辅助时钟）
  _check/_tailflash_meta.json        事件时间戳 / QSettings 还原结果

输出（全部在 _check/）：
  splash_tail_frames/                逐帧全分辨率 PNG（close 前 1.2s ~ 后 0.5s）
  _tailflash_scan/                   全程 400x267 缩略帧（用于逐帧统计定位）
  _tailflash_analysis.json           逐帧统计 + 阶段划分 + 闪帧判定 + 帧率分布
  _tailflash_montage.png             关键帧拼图（带帧号/时间戳/像素统计）

判定口径：
  · showinfo 的 checksum 是编码前帧的校验和，相同画面 checksum 相同 →
    "checksum 变化帧" = 屏幕内容真实更新帧，用来测帧率，不受 h264 有损影响。
  · 闪帧前一帧若仍是"不透明深底 + logo"（低 std、低 uniq），即为 A 类
    （窗口还有内容就 close 的硬切）；闪帧本身若是白/黑/桌面色且发生在
    窗口已全透明之后，才是 B 类（DWM 撤场重合成）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "_check"
MKV = CHECK / "_tailflash_raw.mkv"
FFLOG = CHECK / "_tailflash_ffmpeg.log"
LUMA = CHECK / "_tailflash_luma.jsonl"
META = CHECK / "_tailflash_meta.json"
SCAN = CHECK / "_tailflash_scan"
FRAMES = CHECK / "splash_tail_frames"
ANALYSIS = CHECK / "_tailflash_analysis.json"
MONTAGE = CHECK / "_tailflash_montage.png"

FADE_S = 0.30          # 淡出静态时长（splash_anim._FADE_MS=300）
LAND_S = 0.80          # 落点段（spec 2.40~3.20s）
PRE_S = 1.35           # 逐帧落盘窗口：close 前 1.35s（覆盖落点段 0.8s + 淡出 0.3s）
POST_S = 0.55          # close 后 0.55s（主窗上屏余波）


def parse_showinfo() -> list[dict]:
    s = FFLOG.read_text(encoding="utf-8", errors="replace")
    frames = []
    for m in re.finditer(
            r"n:\s*(\d+)\s+pts:\s*\d+\s+pts_time:([\d.]+).*?checksum:([0-9A-Fa-f]{8})",
            s):
        frames.append({"n": int(m.group(1)), "pts": float(m.group(2)),
                       "checksum": m.group(3).upper()})
    return frames


def frame_stats(png: Path) -> dict:
    a = np.asarray(Image.open(png).convert("RGB"))
    mean = a.reshape(-1, 3).mean(axis=0)
    luma = float(0.2126 * mean[0] + 0.7152 * mean[1] + 0.0722 * mean[2])
    std = float(a.astype(np.float32).std())
    q = (a // 16).reshape(-1, 3)
    uniq, cnt = np.unique(q, axis=0, return_counts=True)
    dom = (uniq[cnt.argmax()] * 16 + 8).astype(int).tolist()
    return {"mean_rgb": [round(float(x), 2) for x in mean],
            "luma": round(luma, 3), "std": round(std, 3),
            "uniq_q": int(len(uniq)), "dom_rgb": dom}


def extract_scan(frames: list[dict]) -> None:
    SCAN.mkdir(parents=True, exist_ok=True)
    for p in SCAN.glob("s_*.png"):
        p.unlink()
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(MKV),
           "-vf", "scale=400:267", "-fps_mode", "passthrough",
           "-start_number", "0", str(SCAN / "s_%06d.png")]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print(r.stderr.decode("utf-8", errors="replace")[:1500])
        raise SystemExit("缩略帧抽取失败")
    got = len(list(SCAN.glob("s_*.png")))
    print(f"[scan] 缩略帧 {got} 张 / showinfo {len(frames)} 帧", flush=True)


def extract_full_window(n0: int, n1: int) -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    for p in FRAMES.glob("f_*.png"):
        p.unlink()
    # 按 showinfo 帧号精确选帧，输出全分辨率 1600x1068
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(MKV),
           "-vf", f"select='between(n\\,{n0}\\,{n1})'",
           "-fps_mode", "passthrough", "-start_number", "0",
           str(FRAMES / "f_%06d.png")]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print(r.stderr.decode("utf-8", errors="replace")[:1500])
        raise SystemExit("逐帧全分辨率抽取失败")
    print(f"[frames] 落盘 {len(list(FRAMES.glob('f_*.png')))} 张 "
          f"(n={n0}..{n1}) → {FRAMES}", flush=True)


def pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return round(s[max(0, min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1)))))], 3)


def interval_stats(vals: list[float]) -> dict:
    if not vals:
        return {"count": 0}
    return {"count": len(vals),
            "mean_ms": round(sum(vals) / len(vals), 3),
            "p95_ms": pct(vals, 95), "max_ms": round(max(vals), 3),
            "gt20ms": sum(1 for v in vals if v > 20.0),
            "gt33ms": sum(1 for v in vals if v > 33.3)}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="开屏收尾录屏逐帧分析（只读，不 import 产品源码）")
    ap.add_argument("--tag", default="",
                    help="与录制脚本 --tag 对齐（如 od49）；留空=旧版默认路径，"
                         "带 tag 时产物写为 _tailflash_<tag>_*，避免覆盖旧基线")
    args = ap.parse_args()
    suf = f"_{args.tag}" if args.tag else ""
    # 重绑定路径全局量（函数按调用时的值读取这些模块级名字）
    global FFLOG, LUMA, META, SCAN, FRAMES, ANALYSIS, MONTAGE, MKV
    FFLOG = CHECK / f"_tailflash{suf}_ffmpeg.log"
    LUMA = CHECK / f"_tailflash{suf}_luma.jsonl"
    META = CHECK / f"_tailflash{suf}_meta.json"
    SCAN = CHECK / f"_tailflash{suf}_scan"
    FRAMES = CHECK / f"splash_tail_frames{suf}"
    ANALYSIS = CHECK / f"_tailflash{suf}_analysis.json"
    MONTAGE = CHECK / f"_tailflash{suf}_montage.png"
    MKV = CHECK / f"_tailflash{suf}_raw.mkv"

    frames = parse_showinfo()
    if len(frames) < 100:
        raise SystemExit(f"showinfo 帧太少：{len(frames)}")
    print(f"[info] showinfo {len(frames)} 帧，pts {frames[0]['pts']:.3f} ~ "
          f"{frames[-1]['pts']:.3f}s", flush=True)

    extract_scan(frames)
    scan_files = sorted(SCAN.glob("s_*.png"))
    rows = []
    prev = None
    for i, png in enumerate(scan_files):
        st = frame_stats(png)
        if prev is not None:
            a = np.asarray(Image.open(png).convert("RGB"), dtype=np.int16)
            b = np.asarray(Image.open(scan_files[i - 1]).convert("RGB"), dtype=np.int16)
            st["diff"] = round(float(np.abs(a - b).mean()), 3)
        else:
            st["diff"] = 0.0
        st["n"] = frames[i]["n"] if i < len(frames) else i
        st["pts"] = frames[i]["pts"] if i < len(frames) else i / 69.0
        st["checksum"] = frames[i]["checksum"] if i < len(frames) else ""
        rows.append(st)
        prev = st
    print(f"[scan] 逐帧统计完成 {len(rows)} 帧", flush=True)

    # ---- 定位 close：主窗稳定画面首次出现的帧 ----
    # 不用 diff 峰值（开屏→主窗是深色→深色，整屏 diff 只有 ~8，会被 logo 动画的
    # 局部变化压过，实测误定位到 n=302）。改用主窗深色 UI 的稳定特征：
    # uniq_q>=100、std∈[13,22]、diff<0.6，且此后连续多帧保持 —— 即开屏撤场完成、
    # 主窗完整上屏的第一帧。本例落在 n=374 / pts=5.026s，往前 0.30s 正好是
    # logo 开始变灰的 pts=4.718s，与 _FADE_MS=300 完全吻合。
    main_first = None
    for i, r in enumerate(rows):
        if r["pts"] < 3.0:
            continue
        if r["uniq_q"] >= 100 and 13 <= r["std"] <= 22 and r["diff"] < 0.6:
            main_first = i
            break
    if main_first is None:
        main_first = len(rows) - 20
    close = rows[main_first]
    nc, pc = close["n"], close["pts"]
    print(f"[locate] close n={nc} pts={pc:.3f}s diff={close['diff']} "
          f"mean_rgb={close['mean_rgb']} std={close['std']} uniq={close['uniq_q']}",
          flush=True)

    # ---- 窗口帧号 ----
    n0 = max(0, min(r["n"] for r in rows if r["pts"] >= pc - PRE_S))
    n1 = max(r["n"] for r in rows if r["pts"] <= pc + POST_S)
    extract_full_window(n0, n1)

    win_files = sorted(FRAMES.glob("f_*.png"))
    win = []
    prev = None
    for i, png in enumerate(win_files):
        st = frame_stats(png)
        if prev is not None:
            a = np.asarray(Image.open(png).convert("RGB"), dtype=np.int16)
            b = np.asarray(Image.open(win_files[i - 1]).convert("RGB"), dtype=np.int16)
            st["diff"] = round(float(np.abs(a - b).mean()), 3)
        else:
            st["diff"] = 0.0
        idx = n0 + i
        st["idx"] = i
        st["n"] = idx
        st["pts"] = frames[idx]["pts"] if idx < len(frames) else 0.0
        st["checksum"] = frames[idx]["checksum"] if idx < len(frames) else ""
        win.append(st)
        prev = st

    # ---- 逐帧帧率：checksum 变化帧 ----
    def phase_of(pts: float) -> str:
        if pts < pc - FADE_S - LAND_S:
            return "GEO"
        if pts < pc - FADE_S:
            return "LAND"
        if pts < pc:
            return "FADE"
        return "AFTER"

    for r in rows:
        r["phase"] = phase_of(r["pts"])
    # 所有录屏帧 pts 间隔（DWM 呈现节奏）
    all_iv = [(rows[i]["pts"] - rows[i - 1]["pts"]) * 1000.0
              for i in range(1, len(rows))]
    # 内容更新帧：checksum 与前一帧不同
    chg_idx = [i for i in range(1, len(rows))
               if rows[i]["checksum"] != rows[i - 1]["checksum"]]
    chg_iv = [(rows[i]["pts"] - rows[i - 1]["pts"]) * 1000.0 for i in chg_idx]
    chg_gap = [(rows[chg_idx[j]]["pts"] - rows[chg_idx[j - 1]]["pts"]) * 1000.0
               for j in range(1, len(chg_idx))]

    by_phase = {}
    for ph in ("GEO", "LAND", "FADE", "AFTER"):
        ids = [i for i in chg_idx if rows[i]["phase"] == ph]
        iv = [(rows[i]["pts"] - rows[i - 1]["pts"]) * 1000.0 for i in ids]
        gap = [(rows[ids[j]]["pts"] - rows[ids[j - 1]]["pts"]) * 1000.0
               for j in range(1, len(ids))]
        by_phase[ph] = {
            "frames_in_phase": sum(1 for r in rows if r["phase"] == ph),
            "content_change_frames": len(ids),
            "unique_checksums": len({rows[i]["checksum"] for i in ids}),
            "rec_frame_interval": interval_stats(iv),
            "content_gap": interval_stats(gap),
        }

    # ---- 闪帧判定 ----
    def classify(r: dict) -> str:
        m = r["mean_rgb"]
        mx, mn = max(m), min(m)
        if mx > 200 and mn > 180:
            return "WHITE"
        if mx < 5:
            return "BLACK"
        if r["std"] < 6 and mx < 20:
            return "SPLASH_PURE_BG"          # 开屏纯深底
        if 13 <= r["std"] <= 22 and r["uniq_q"] >= 95:
            return "MAIN_UI"                 # 主窗深色 UI（稳定）
        if r["std"] >= 25:
            return "SPLASH_LOGO"             # 开屏带清晰 logo
        if abs(mx - 30) < 8 and abs(mn - 30) < 8 and r["std"] > 18:
            return "DESKTOP"                 # 录制初期的桌面/聊天窗
        return "FADE_MIX"                    # 淡出/撤场的中间态

    for r in win:
        r["class"] = classify(r)

    # 淡出段：close 前 0.32s ~ close 后 0.06s。
    # 核心闪 = 淡出把画面压到最暗的"谷底帧"（logo 渐隐到几乎纯黑、底色仍不透明）
    # 之后亮度首次回升 >1 的"反弹帧"（开屏窗口撤场、主窗开始上屏）。
    fade_rows = [(i, r) for i, r in enumerate(win)
                 if pc - 0.32 <= r["pts"] <= pc + 0.06]
    valley_i = min(fade_rows, key=lambda t: t[1]["luma"])[0] if fade_rows else 0
    rebound_i = valley_i
    for j in range(valley_i + 1, min(len(win), valley_i + 10)):
        if win[j]["luma"] - win[valley_i]["luma"] > 1.0:
            rebound_i = j
            break
    peak_i = valley_i

    flash = []
    for j in range(max(0, valley_i - 2), min(len(win), valley_i + 9)):
        r = win[j]
        role = ("valley" if j == valley_i else
                "rebound" if j == rebound_i else
                "pre_flash" if j < valley_i else "post_flash")
        flash.append({"idx": r["idx"], "n": r["n"], "pts": r["pts"],
                      "role": role, "diff": r["diff"], "class": r["class"],
                      "mean_rgb": r["mean_rgb"], "luma": r["luma"],
                      "std": r["std"], "uniq_q": r["uniq_q"]})
    extreme = [f for f in flash if f["class"] in ("WHITE", "BLACK", "DESKTOP")]

    # close 前一帧内容（判定 A 的关键证据）
    pre = win[max(0, valley_i - 1)]
    at = win[valley_i]
    post = win[min(len(win) - 1, rebound_i)]

    # ---- montage ----
    picks = []
    # close 前后密集 + 窗口等间隔
    dense = list(range(max(0, peak_i - 6), min(len(win), peak_i + 4)))
    even = [int(round(k * (len(win) - 1) / 7)) for k in range(8)]
    for i in sorted(set(dense + even)):
        picks.append(i)
    picks = picks[:16]
    TW, TH = 400, 290
    cols = 4
    rws = (len(picks) + cols - 1) // cols
    sheet = Image.new("RGB", (TW * cols, TH * rws), (18, 18, 20))
    d = ImageDraw.Draw(sheet)
    for k, i in enumerate(picks):
        r = win[i]
        im = Image.open(win_files[i]).convert("RGB").resize((400, 267))
        x, y = (k % cols) * TW, (k // cols) * TH
        sheet.paste(im, (x, y + 23))
        tag = (f"n={r['n']} pts={r['pts']:.3f}s d={r['diff']:.1f} "
               f"rgb=({int(r['mean_rgb'][0])},{int(r['mean_rgb'][1])},"
               f"{int(r['mean_rgb'][2])}) {r['class']}")
        col = (255, 80, 80) if i == peak_i else (255, 235, 90)
        d.text((x + 3, y + 6), tag, fill=col)
    sheet.save(MONTAGE)

    # ---- 输出 ----
    out = {
        "source": {"mkv": str(MKV), "showinfo_frames": len(frames),
                   "pts_range": [frames[0]["pts"], frames[-1]["pts"]],
                   "luma_rows": sum(1 for _ in LUMA.open(encoding="utf-8"))},
        "locate": {"close_n": nc, "close_pts": pc, "close_diff": close["diff"],
                   "window_n": [n0, n1], "window_frames": len(win),
                   "fade_pts": [pc - FADE_S, pc],
                   "land_pts": [pc - FADE_S - LAND_S, pc - FADE_S]},
        "flash_core": {
            "valley_n": at["n"], "valley_pts": at["pts"],
            "valley_luma": at["luma"], "valley_rgb": at["mean_rgb"],
            "valley_class": at["class"], "valley_std": at["std"],
            "rebound_n": win[rebound_i]["n"], "rebound_pts": win[rebound_i]["pts"],
            "rebound_luma": win[rebound_i]["luma"],
            "rebound_rgb": win[rebound_i]["mean_rgb"],
            "rebound_class": win[rebound_i]["class"],
            "flash_span_frames": rebound_i - valley_i,
            "extreme_color_frames": extreme,
        },
        "peak": {"idx": peak_i, "n": at["n"], "pts": at["pts"],
                 "diff": at["diff"], "class": at["class"],
                 "mean_rgb": at["mean_rgb"], "luma": at["luma"]},
        "pre_close_frame": {"n": pre["n"], "pts": pre["pts"],
                            "class": pre["class"], "mean_rgb": pre["mean_rgb"],
                            "luma": pre["luma"], "std": pre["std"],
                            "uniq_q": pre["uniq_q"], "diff": pre["diff"]},
        "post_close_frame": {"n": post["n"], "pts": post["pts"],
                             "class": post["class"], "mean_rgb": post["mean_rgb"],
                             "luma": post["luma"], "std": post["std"],
                             "uniq_q": post["uniq_q"], "diff": post["diff"]},
        "flash_frames": flash,
        "frame_rate": {
            "all_rec_interval_ms": interval_stats(all_iv),
            "content_change_interval_ms": interval_stats(chg_iv),
            "content_gap_ms": interval_stats(chg_gap),
            "by_phase": by_phase,
        },
        "window_frames_stats": win,
        "all_frames_stats": rows,
    }
    ANALYSIS.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    # ---- 摘要 ----
    print("\n========== 摘要 ==========", flush=True)
    print(f"close 帧 n={nc} pts={pc:.3f}s diff={close['diff']}", flush=True)
    print(f"逐帧窗口 n={n0}..{n1} 共 {len(win)} 帧 → {FRAMES}", flush=True)
    print(f"谷底帧(淡出最暗) n={at['n']} pts={at['pts']:.3f}s "
          f"class={at['class']} rgb={at['mean_rgb']} luma={at['luma']} "
          f"std={at['std']}", flush=True)
    rb = win[rebound_i]
    print(f"反弹帧(撤场/主窗上屏) n={rb['n']} pts={rb['pts']:.3f}s "
          f"class={rb['class']} rgb={rb['mean_rgb']} luma={rb['luma']} "
          f"闪跨度={rebound_i - valley_i} 帧", flush=True)
    print(f"前一帧 n={pre['n']} pts={pre['pts']:.3f}s class={pre['class']} "
          f"rgb={pre['mean_rgb']} std={pre['std']} uniq_q={pre['uniq_q']}", flush=True)
    print(f"后一帧 n={post['n']} pts={post['pts']:.3f}s class={post['class']} "
          f"rgb={post['mean_rgb']} std={post['std']} uniq_q={post['uniq_q']}", flush=True)
    print(f"极端色闪帧(白/黑/桌面色) {len(extreme)} 帧", flush=True)
    print(f"闪帧序列 {len(flash)} 帧：", flush=True)
    for f in flash:
        print(f"  n={f['n']} pts={f['pts']:.3f}s [{f['role']}] diff={f['diff']} "
              f"class={f['class']} rgb={f['mean_rgb']} luma={f['luma']}",
              flush=True)
    print("帧率（ms）：", flush=True)
    print("  全程录屏帧间隔", interval_stats(all_iv), flush=True)
    print("  内容变化帧间隔", interval_stats(chg_iv), flush=True)
    for ph, v in by_phase.items():
        print(f"  {ph}: frames={v['frames_in_phase']} "
              f"chg={v['content_change_frames']} uniq={v['unique_checksums']} "
              f"rec_iv={v['rec_frame_interval']} gap={v['content_gap']}",
              flush=True)
    print(f"montage → {MONTAGE}", flush=True)
    print(f"analysis → {ANALYSIS}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
