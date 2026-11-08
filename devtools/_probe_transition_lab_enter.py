# -*- coding: utf-8 -*-
"""
续单验证：进全屏方向 S2 落地细节（纯离屏，不启动主窗口、不占 .app_run.lock）。

验证项：
  1) 进全屏 S2 收尾 diff：用真实全屏版式帧当 B，模拟 t=1.0 覆盖层显示 B 后
     hide 露真窗，对比现状 S0 的 7.90/4.73%/-2.60，给改善幅度。
  2) 缓存陈旧度：11 张真实全屏帧两两互差按 wall 间隔分箱，给「B 必须多新鲜」门槛。
  3) S2 两种 paintEvent 实现：(a) 两次 drawPixmap+setOpacity 的正确性与
     (b) numpy 预合成的帧成本，含 3200x2000 离屏 QPainter 基准（无 QApplication）。
  4) alpha 证据链：真实帧 alpha 全 255 + 源码后台快照 Format_RGB32。

用法：  .venv/Scripts/python.exe devtools/_probe_transition_lab_enter.py
产物：  _check/transition_lab/enter_verify.json + enter_*.png
"""
import json
import os
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EV = os.path.join(ROOT, "_check", "evidence_window")
OUT = os.path.join(ROOT, "_check", "transition_lab")
os.makedirs(OUT, exist_ok=True)

# 进全屏方向：t=1.0 的 label=1600x1000(ar 1.6)，真窗全屏帧=1400x875(ar 1.6)。
# 画布取 1400x875，与全屏帧同比例，避免跨 ar 非等比拉伸。
CANVAS = (1400, 875)
FPS_BUDGET_MS = 1000.0 / 165.0     # 6.06ms/帧

# 真实全屏态帧（全部 1400x875, fs=True；wall 来自 frames.json）
FS_FRAMES = [
    ("w015_finish_pre_main.png", 3058.3, "finish_pre_main"),
    ("w016_finish_post_0.png", 3130.8, "REAL"),
    ("w017_finish_post_1.png", 3168.0, "REAL"),
    ("w018_finish_post_2.png", 3204.8, "REAL"),
    ("w019_finish_post_3.png", 3244.2, "REAL"),
    ("w020_finish_post_4.png", 3282.1, "REAL"),
    ("w021_finish_pre_main.png", 4472.9, "finish_pre_main"),
    ("w022_finish_pre_main.png", 4515.7, "finish_pre_main"),
    ("w023_finish_post_3.png", 4563.6, "REAL"),
    ("w024_finish_post_4.png", 4599.6, "REAL"),
    ("w025_finish_post_3.png", 4635.2, "REAL"),
]
# B 缓存候选：领队点名的 finish_pre_main 全屏版式系列（fs=True）
B_CANDIDATES = ["w015_finish_pre_main.png", "w021_finish_pre_main.png",
                "w022_finish_pre_main.png"]
# 真窗候选：动画收尾后的 REAL 全屏帧
REAL_CANDIDATES = ["w016_finish_post_0.png", "w018_finish_post_2.png",
                   "w020_finish_post_4.png", "w023_finish_post_3.png",
                   "w025_finish_post_3.png"]

# ---------------------------------------------------------------- 工具
def raw(name, size=None):
    im = Image.open(os.path.join(EV, name)).convert("RGB")
    if size is not None and im.size != size:
        im = im.resize(size, Image.LANCZOS)
    return np.asarray(im).astype(np.float32)


def luma(a):
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def metrics(a, b):
    d = np.abs(a - b)
    return dict(
        mean=round(float(d.mean()), 3),
        max=round(float(d.max()), 1),
        area=round(float((np.abs(luma(b) - luma(a)) > 25).mean() * 100), 3),
        dluma=round(float(luma(b).mean() - luma(a).mean()), 3),
    )


def render(src, label_ar, canvas=CANVAS):
    """复刻 _CoverLabel.paintEvent 的 KeepAspectRatioByExpanding。"""
    h, w = src.shape[:2]
    sar = w / h
    im = Image.fromarray(np.clip(src, 0, 255).astype(np.uint8))
    if sar > label_ar:
        nw = int(round(h * label_ar)); x = (w - nw) // 2
        crop = im.crop((x, 0, x + nw, h))
    else:
        nh = int(round(w / label_ar)); y = (h - nh) // 2
        crop = im.crop((0, y, w, y + nh))
    return np.asarray(crop.resize(canvas, Image.LANCZOS)).astype(np.float32)


def to_qimage(arr):
    """把 (h,w,3) uint8 转 QImage Format_RGB32（不透明）。"""
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    h, w = arr.shape[:2]
    rgba = np.ascontiguousarray(
        np.dstack([arr, np.full((h, w), 255, np.uint8)]))
    return QImage(rgba.data, w, h, 4 * w, QImage.Format.Format_RGB32).copy()


def to_qimage_argb(arr, alpha_map):
    """把 (h,w,3) + alpha_map 转 QImage Format_ARGB32（可含透明像素）。"""
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    h, w = arr.shape[:2]
    rgba = np.ascontiguousarray(np.dstack([arr, alpha_map.astype(np.uint8)]))
    return QImage(rgba.data, w, h, 4 * w, QImage.Format.Format_ARGB32).copy()


def qimage_to_rgb(qimg):
    h, w = qimg.height(), qimg.width()
    buf = np.frombuffer(qimg.constBits(), dtype=np.uint8).reshape(h, w, 4)
    return buf[..., :3].astype(np.float32)


def font(sz):
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()


# ---------------------------------------------------------------- 验证 1+2
def landing_and_staleness():
    # --- S0 现状基线：进全屏 B=None 全程 A；t=1.0 覆盖层=A 普通版式铺到全屏 label
    A = raw("w005_cover_tick.png")                 # 1400x1012, ar 1.383
    s0_cover = render(A, 1.6)                      # A 被上下对称裁到全屏 ar
    real_ref = raw("w016_finish_post_0.png", CANVAS)
    s0 = metrics(s0_cover, real_ref)

    # 真实全屏帧集结（统一 CANVAS，本身即 1400x875，不缩放）
    frames = {n: raw(n, CANVAS) for n, _, _ in FS_FRAMES}
    walls = {n: w for n, w, _ in FS_FRAMES}

    # --- 验证 1：S2 收尾 diff 矩阵（B × 真窗）。
    #     S2 t=1.0 w=1 → 覆盖层显示 B（全屏版式）；B 源 ar=1.6 与 label ar=1.6 一致，
    #     无裁切，铺满即 B 本身。hide 后真窗 = real。收尾 diff = |B − real|。
    matrix = []
    for bn in B_CANDIDATES:
        row = []
        for rn in REAL_CANDIDATES:
            m = metrics(frames[bn], frames[rn])
            row.append(dict(B=bn, real=rn, dt_ms=round(walls[rn] - walls[bn], 1), **m))
        matrix.append(dict(B=bn, cells=row))
    # 理想下界：B 与真窗同一帧（新鲜到极致）
    ideal = metrics(frames["w016_finish_post_0.png"], frames["w016_finish_post_0.png"])
    # 「B 早于真窗」的合理组合（模拟「上次全屏缓存 → 本次真窗」）
    forward = [c for row in matrix for c in row["cells"] if c["dt_ms"] > 0]
    s2_typical = min(forward, key=lambda c: c["dt_ms"])      # 最鲜的现实组合
    s2_worst = max(forward, key=lambda c: c["mean"])         # 最陈的现实组合
    improve = dict(
        mean=round((s0["mean"] - s2_typical["mean"]) / s0["mean"] * 100, 1),
        area=round((s0["area"] - s2_typical["area"]) / s0["area"] * 100, 1),
        dluma_abs=round(abs(s0["dluma"]) - abs(s2_typical["dluma"]), 2),
    )

    # --- 验证 2：11 张全屏帧两两互差，按 |Δwall| 分箱 → 陈旧度曲线
    pairs = []
    names = [n for n, _, _ in FS_FRAMES]
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            m = metrics(frames[a], frames[b])
            pairs.append(dict(a=a, b=b, dt_ms=round(abs(walls[a] - walls[b]), 1), **m))
    bins = [(0, 100), (100, 300), (300, 1600)]
    staleness = []
    for lo, hi in bins:
        sel = [p for p in pairs if lo <= p["dt_ms"] < hi]
        if sel:
            staleness.append(dict(
                bin="%d~%dms" % (lo, hi), n=len(sel),
                mean=round(float(np.mean([p["mean"] for p in sel])), 3),
                mean_max=round(max(p["mean"] for p in sel), 3),
                area=round(float(np.mean([p["area"] for p in sel])), 3),
                area_max=round(max(p["area"] for p in sel), 3),
            ))
    # 差异位置：取最大 dt 的一对，看 >25 区域的 bounding box 与面积（判断是否局部）
    longest = max(pairs, key=lambda p: p["dt_ms"])
    d = np.abs(luma(frames[longest["a"]]) - luma(frames[longest["b"]]))
    ys, xs = np.where(d > 25)
    if len(xs):
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
        spread = round(100.0 * (xs.max() - xs.min()) * (ys.max() - ys.min()) /
                       (CANVAS[0] * CANVAS[1]), 2)
    else:
        bbox, spread = None, 0.0

    return dict(
        s0_baseline=s0, s2_ideal=ideal, s2_typical=s2_typical, s2_worst=s2_worst,
        improve=improve, matrix=matrix, pairs=pairs, staleness=staleness,
        longest_pair=longest, longest_bbox=bbox, longest_bbox_spread_pct=spread,
        frames=s0_cover, real=real_ref,
    )


# ---------------------------------------------------------------- 验证 3
def correctness_and_cost():
    W, H = 3200, 2000                              # 整屏物理尺寸（领队指定）
    a = raw("w005_cover_tick.png", (W, H))        # A：普通版式（放大到整屏）
    b = raw("w021_finish_pre_main.png", (W, H))   # B：全屏版式
    qa, qb = to_qimage(a), to_qimage(b)
    WGT = 0.5

    # (a) 两次 drawImage + setOpacity；取回像素
    out = QImage(W, H, QImage.Format.Format_RGB32); out.fill(0)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawImage(QRect(0, 0, W, H), qa)
    p.setOpacity(WGT)
    p.drawImage(QRect(0, 0, W, H), qb)
    p.end()
    out_a = qimage_to_rgb(out)
    # (b) numpy 线性混合
    out_np = a * (1 - WGT) + b * WGT
    correct_opaque = metrics(out_a, out_np)

    # 反例：B 人为带 alpha（左半 alpha=128），验证「透明会破坏 (a) 线性混合」
    alpha_map = np.full((H, W), 255, np.uint8)
    alpha_map[:, : W // 2] = 128
    qb_alpha = to_qimage_argb(b, alpha_map)
    out2 = QImage(W, H, QImage.Format.Format_RGB32); out2.fill(0)
    p = QPainter(out2)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawImage(QRect(0, 0, W, H), qa)
    p.setOpacity(WGT)
    p.drawImage(QRect(0, 0, W, H), qb_alpha)
    p.end()
    out_alpha = qimage_to_rgb(out2)
    correct_alpha = metrics(out_alpha, out_np)
    # 只统计 B 透明半区的偏离
    half = metrics(out_alpha[:, : W // 2], out_np[:, : W // 2])

    # --- 帧成本基准（多次取中位数，丢弃首次冷启动）---
    a8 = np.clip(a, 0, 255).astype(np.uint8)
    b8 = np.clip(b, 0, 255).astype(np.uint8)
    a32 = a.astype(np.float32); b32 = b.astype(np.float32)

    def bench(fn, n=6):
        ts = []
        for i in range(n):
            t0 = time.perf_counter(); fn(); t1 = time.perf_counter()
            if i > 0:
                ts.append((t1 - t0) * 1000)
        return round(float(np.median(ts)), 2)

    def single(hint):
        def f():
            o = QImage(W, H, QImage.Format.Format_RGB32); o.fill(0)
            pp = QPainter(o)
            if hint is not None:
                pp.setRenderHint(hint, True)
            pp.drawImage(QRect(0, 0, W, H), qa); pp.end()
            return o
        return f

    def double_op(hint):
        def f():
            o = QImage(W, H, QImage.Format.Format_RGB32); o.fill(0)
            pp = QPainter(o)
            if hint is not None:
                pp.setRenderHint(hint, True)
            pp.drawImage(QRect(0, 0, W, H), qa)
            pp.setOpacity(WGT)
            pp.drawImage(QRect(0, 0, W, H), qb)
            pp.end()
            return o
        return f

    def double_no_op():
        def f():
            o = QImage(W, H, QImage.Format.Format_RGB32); o.fill(0)
            pp = QPainter(o)
            pp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            pp.drawImage(QRect(0, 0, W, H), qa)
            pp.drawImage(QRect(0, 0, W, H), qb)
            pp.end()
            return o
        return f

    def numpy_float():
        return (a32 * (1 - WGT) + b32 * WGT).astype(np.uint8)

    def numpy_u16():
        return ((a8.astype(np.uint16) * 128 + b8.astype(np.uint16) * 128) >> 8).astype(np.uint8)

    def numpy_full():
        # (b) 全链路：混合 → QImage → 单次 drawImage
        mix = ((a8.astype(np.uint16) * 128 + b8.astype(np.uint16) * 128) >> 8).astype(np.uint8)
        qi = to_qimage(mix)
        o = QImage(W, H, QImage.Format.Format_RGB32); o.fill(0)
        pp = QPainter(o); pp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        pp.drawImage(QRect(0, 0, W, H), qi); pp.end()
        return o

    costs = {
        "单次 drawImage Smooth（现状基准）": bench(single(QPainter.RenderHint.SmoothPixmapTransform)),
        "单次 drawImage Fast": bench(single(None)),
        "(a) 两次 drawImage + setOpacity Smooth": bench(double_op(QPainter.RenderHint.SmoothPixmapTransform)),
        "(a) 两次 drawImage + setOpacity Fast": bench(double_op(None)),
        "两次 drawImage 都 opacity=1（对照）": bench(double_no_op()),
        "(b) numpy 混合 float32": bench(numpy_float),
        "(b) numpy 混合 uint16": bench(numpy_u16),
        "(b) 全链路 混合+QImage+单次绘制": bench(numpy_full),
    }
    # (c) 预生成档位：每 tick 只 set_source(预生成档)，成本 = 单次 drawImage
    costs["(c) 预生成档位 每tick仅单次绘制"] = costs["单次 drawImage Smooth（现状基准）"]

    return dict(
        canvas=[W, H], fps_budget_ms=round(FPS_BUDGET_MS, 2),
        correct_opaque=correct_opaque, correct_alpha=correct_alpha,
        correct_alpha_half=half, costs=costs,
    )


# ---------------------------------------------------------------- 绘图
def draw_staleness(V, path):
    W, H, PAD = 1000, 460, 70
    img = Image.new("RGB", (W, H), (20, 20, 24)); d = ImageDraw.Draw(img)
    f = font(12)
    x0, x1, y0, y1 = PAD, W - 30, 50, H - 50
    def X(dt): return x0 + (x1 - x0) * (dt / 1600.0)
    def Y(v): return y1 - (y1 - y0) * min(1.0, v / 8.0)
    for gy in range(0, 9, 2):
        yy = Y(gy); d.line((x0, yy, x1, yy), fill=(55, 55, 62))
        d.text((12, yy - 7), str(gy), fill=(150, 150, 160), font=f)
    for gx in (0, 100, 300, 800, 1600):
        xx = X(gx); d.line((xx, y0, xx, y1), fill=(45, 45, 52))
        d.text((xx - 12, y1 + 6), "%d" % gx, fill=(150, 150, 160), font=f)
    for pr in V["pairs"]:
        xx, yy = X(pr["dt_ms"]), Y(pr["mean"])
        d.ellipse((xx - 3, yy - 3, xx + 3, yy + 3), fill=(120, 200, 255))
    # 门槛线 mean=1.5
    d.line((x0, Y(1.5), x1, Y(1.5)), fill=(255, 120, 90))
    d.text((x0 + 6, Y(1.5) - 16), "收尾可接受门槛 mean=1.5（对齐换B后 1.09/1.60）",
           fill=(255, 150, 120), font=f)
    # S0 基准
    d.line((x0, Y(V["s0_baseline"]["mean"]), x1, Y(V["s0_baseline"]["mean"])), fill=(255, 90, 90))
    d.text((x0 + 6, Y(V["s0_baseline"]["mean"]) - 16), "S0 收尾 7.90", fill=(255, 120, 120), font=f)
    d.text((x0, 10), "全屏帧两两互差 vs 陈旧度 |Δwall|（每点=一对真实帧；越低于门槛越安全）",
           fill=(235, 235, 240), font=font(15))
    d.text((x0, 30), "x=时间间隔(ms)  y=相邻帧 mean 差异", fill=(160, 160, 170), font=f)
    img.save(path)


def draw_cost(C, path):
    W, H, PAD = 1100, 520, 230
    img = Image.new("RGB", (W, H), (20, 20, 24)); d = ImageDraw.Draw(img)
    f, fb = font(12), font(13)
    x0, y0 = PAD, 60
    items = list(C["costs"].items())
    rowh = 44
    maxv = max(v for _, v in items)
    def X(v): return x0 + (W - 40 - x0) * (v / maxv)
    # 预算线
    bx = X(C["fps_budget_ms"])
    d.line((bx, y0 - 20, bx, y0 + rowh * len(items)), fill=(255, 210, 90))
    d.text((bx - 60, y0 - 38), "165fps 预算 6.06ms", fill=(255, 220, 120), font=f)
    for i, (k, v) in enumerate(items):
        yy = y0 + i * rowh
        col = (120, 200, 255) if "预算" not in k else (255, 220, 120)
        if "(a)" in k: col = (255, 160, 90)
        if "(b)" in k: col = (200, 140, 255)
        if "(c)" in k: col = (120, 235, 140)
        d.text((12, yy + 6), k, fill=(215, 215, 220), font=fb)
        d.rectangle((x0, yy + 4, X(v), yy + 26), fill=col)
        d.text((X(v) + 6, yy + 6), "%.1f ms  (%.1fx 预算)" % (v, v / C["fps_budget_ms"]),
               fill=(230, 230, 235), font=f)
    d.text((12, 12), "3200x2000 离屏 QPainter(QImage) 帧成本基准（CPU 光栅，中位数/6 次）",
           fill=(235, 235, 240), font=font(15))
    d.text((12, 34), "ⓘ 真机 QPixmap 可能更快；相对量级可信，绝对值需真机实测",
           fill=(160, 160, 170), font=f)
    img.save(path)


def draw_landing(V, path):
    """S0 vs S2 收尾帧目检 + 差异热力 + S2 溶解中间帧双影。"""
    A = raw("w005_cover_tick.png")
    s0_cover = render(A, 1.6)
    real = raw("w016_finish_post_0.png", CANVAS)
    B = raw("w021_finish_pre_main.png", CANVAS)
    # S2 中间帧 w=0.5（A/B 各半，展示双影）；A 先铺到全屏 label
    mix = 0.5 * s0_cover + 0.5 * B

    def diff_heat(a, b):
        d = np.abs(luma(b) - luma(a))
        g = np.clip(d * 3, 0, 255).astype(np.uint8)
        return np.dstack([g, np.clip(g * 0.35, 0, 255).astype(np.uint8),
                          np.clip(255 - g, 0, 255).astype(np.uint8)])

    items = [
        ("S0 覆盖层末帧(A 普通版式裁到全屏)", s0_cover),
        ("S2 t=1.0 覆盖层(B 全屏版式)", B),
        ("真窗 REAL(全屏)", real),
        ("S0 收尾 |覆盖层−真窗| x3", diff_heat(s0_cover, real)),
        ("S2 收尾 |B−真窗| x3", diff_heat(B, real)),
        ("S2 中间帧 w=0.5（双影）", mix),
    ]
    TW, TH, BAR, HEAD = 420, 263, 24, 44
    cols = 3
    rows = 2
    sheet = Image.new("RGB", (cols * TW, HEAD + rows * (TH + BAR)), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    d.text((10, 10), "进全屏方向 S2 落地目检：S0 收尾 %.2f/%.2f%%  →  S2 收尾 %.2f/%.2f%%" % (
        V["s0_baseline"]["mean"], V["s0_baseline"]["area"],
        V["s2_typical"]["mean"], V["s2_typical"]["area"]),
        fill=(235, 235, 240), font=font(16))
    for i, (label, arr) in enumerate(items):
        r, c = divmod(i, cols)
        x, y = c * TW, HEAD + r * (TH + BAR)
        im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).resize((TW, TH), Image.LANCZOS)
        sheet.paste(im, (x, y))
        d.rectangle((x, y + TH, x + TW, y + TH + BAR), fill=(16, 16, 20))
        d.text((x + 6, y + TH + 4), label, fill=(200, 220, 255), font=font(12))
    sheet.save(path)


# ---------------------------------------------------------------- 主流程
def main():
    V = landing_and_staleness()
    C = correctness_and_cost()

    # alpha 证据链
    alpha_stat = {}
    for n in ["w005_cover_tick.png", "w021_finish_pre_main.png",
              "w015_finish_pre_main.png", "w016_finish_post_0.png"]:
        im = np.array(Image.open(os.path.join(EV, n)).convert("RGBA"))
        al = im[..., 3]
        alpha_stat[n] = dict(min=int(al.min()), max=int(al.max()),
                             lt255_pct=round(float((al < 255).mean() * 100), 4))
    out = {
        "canvas": CANVAS, "fps_budget_ms": round(FPS_BUDGET_MS, 2),
        "metric_def": "mean=|RGB diff|.mean; area=Rec709|Δluma|>25 像素%; dluma=Rec709 均值差",
        "frames_used": [dict(name=n, wall=w, tag=t) for n, w, t in FS_FRAMES],
        "alpha_evidence": {
            "real_frames": alpha_stat,
            "source_chain": "_fresh_snapshot_pixmap→_bg_img(后台SnapWorker, QImage.Format_RGB32 不透明) 或 _grab_window_rect(QScreen.grabWindow); _cover_prescale 仅 scaled 不改格式。GDI Format_ARGB32(364) 是 _capture_native_window 兜底，不用于覆盖层 A/B。",
            "verdict": "A/B 源图全不透明，(a) 的 setOpacity source-over 等价于线性混合",
        },
        "verification_1": dict(
            s0_baseline=V["s0_baseline"], s2_ideal=V["s2_ideal"],
            s2_typical=V["s2_typical"], s2_worst=V["s2_worst"],
            improve=V["improve"], matrix=V["matrix"]),
        "verification_2": dict(
            bins=V["staleness"], n_pairs=len(V["pairs"]),
            longest_pair=V["longest_pair"], longest_bbox=V["longest_bbox"],
            longest_bbox_spread_pct=V["longest_bbox_spread_pct"],
            threshold="mean<=1.5 且 area<=1.5% 视为收尾不可见跳变（对齐换B后 w037→w038=1.09/1.19%、各方案收尾=1.60）"),
        "verification_3": C,
    }
    with open(os.path.join(OUT, "enter_verify.json"), "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    draw_staleness(V, os.path.join(OUT, "enter_staleness.png"))
    draw_cost(C, os.path.join(OUT, "enter_frame_cost.png"))
    draw_landing(V, os.path.join(OUT, "enter_landing.png"))

    # ---- 摘要 ----
    print("=== 验证1 进全屏收尾 diff ===")
    s0, st, sw, im = V["s0_baseline"], V["s2_typical"], V["s2_worst"], V["improve"]
    print("  S0 现状(A 普通版式→真窗全屏): mean=%.2f area=%.2f%% dluma=%+.2f" % (
        s0["mean"], s0["area"], s0["dluma"]))
    print("  S2 理想(B=真窗同帧)      : mean=%.2f area=%.2f%%" % (
        V["s2_ideal"]["mean"], V["s2_ideal"]["area"]))
    print("  S2 最鲜现实组合 %s→%s (Δt=%.0fms): mean=%.2f area=%.2f%% dluma=%+.2f" % (
        st["B"], st["real"], st["dt_ms"], st["mean"], st["area"], st["dluma"]))
    print("  S2 最陈现实组合 %s→%s (Δt=%.0fms): mean=%.2f area=%.2f%%" % (
        sw["B"], sw["real"], sw["dt_ms"], sw["mean"], sw["area"]))
    print("  改善(相对S0): mean %+.1f%%  area %+.1f%%  |dluma| %.2f→%.2f" % (
        im["mean"], im["area"], abs(s0["dluma"]), abs(st["dluma"])))
    print("\n=== 验证2 缓存陈旧度（11帧两两互差，共%d对）===" % len(V["pairs"]))
    for b in V["staleness"]:
        print("  %-12s n=%2d  mean均=%.3f 最大=%.3f | area均=%.3f%% 最大=%.3f%%" % (
            b["bin"], b["n"], b["mean"], b["mean_max"], b["area"], b["area_max"]))
    lp = V["longest_pair"]
    print("  最长间隔对 %s→%s Δt=%.0fms: mean=%.2f area=%.2f%% | 差异bbox=%s 占画面%.1f%%" % (
        lp["a"], lp["b"], lp["dt_ms"], lp["mean"], lp["area"],
        V["longest_bbox"], V["longest_bbox_spread_pct"]))
    print("\n=== 验证3 正确性与帧成本 ===")
    print("  (a)不透明A/B vs numpy线性混合: mean|diff|=%.3f (正确性成立)" % C["correct_opaque"]["mean"])
    print("  (a)B带alpha vs numpy线性混合 : mean|diff|=%.3f ; 透明半区=%.3f (透明破坏(a))" % (
        C["correct_alpha"]["mean"], C["correct_alpha_half"]["mean"]))
    print("  165fps 预算 = %.2f ms/帧" % C["fps_budget_ms"])
    for k, v in C["costs"].items():
        print("  %-38s %7.2f ms  (%.1fx 预算)" % (k, v, v / C["fps_budget_ms"]))
    print("\n=== alpha 证据 ===")
    for n, s in alpha_stat.items():
        print("  %-28s alpha min=%d max=%d <255=%.4f%%" % (n, s["min"], s["max"], s["lt255_pct"]))
    print("\n产物:", os.path.join(OUT, "enter_verify.json"),
          "enter_staleness.png / enter_frame_cost.png / enter_landing.png")


if __name__ == "__main__":
    main()
