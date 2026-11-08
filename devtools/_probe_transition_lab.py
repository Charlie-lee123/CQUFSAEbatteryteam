# -*- coding: utf-8 -*-
"""
窗口转场方案离屏量化评估（S0~S4）。

纯像素级模拟：不启动任何 Qt 窗口、不占用 _check/.app_run.lock、不改产品源码。
用真机取证到的 A/B 帧 PNG 作为「版式内容源」，用 cover_stretch/stretch.json 的
真实 label 几何序列驱动裁切，逐帧复现 _CoverLabel.paintEvent 的
KeepAspectRatioByExpanding 行为，再对每种换源策略算相邻帧突变曲线。

建模经真实帧校准（见 CALIB）：render(A源, label_ar=1.3829) 与真实 w035 仅差
mean=0.42 / area=0.25%，与 w036 差 8.25/4.80% ≈ 实测 w035→w036 的 8.14/4.72%。

用法：  .venv/Scripts/python.exe devtools/_probe_transition_lab.py
产物：  _check/transition_lab/{metrics.json, sheet_S*.png, sheet_enter.png, curves.png}
"""
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EV = os.path.join(ROOT, "_check", "evidence_window")
STRETCH = os.path.join(ROOT, "_check", "cover_stretch", "stretch.json")
OUT = os.path.join(ROOT, "_check", "transition_lab")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, ROOT)
from splash_motion import _gaussian  # noqa: E402  真高斯（可分离卷积，ndarray 接口）

CANVAS = (1400, 1012)  # 统一画布（末段 label ar≈1.383 的取证帧尺寸），全分辨率口径

# 时间轴：前段稀疏（几何运动），末段 0.85~1.00 密集（换源评估窗口）
TS = [0.0, 0.2, 0.4, 0.6, 0.8] + [round(0.85 + 0.01 * i, 2) for i in range(16)]

# 关键帧（contact sheet 用）
KEY_T = [0.0, 0.85, 0.90, 0.94, 0.95, 0.97, 0.99, 1.0]

DIRECTIONS = {
    "restore": dict(
        A="w026_cover_tick.png",           # A 源：起手全屏版式快照（ar 1.6，crop=0）
        B="w036_cover_tick.png",           # B 源：目标普通窗口版式快照（ar 1.383）
        real_start="w022_finish_pre_main.png",  # 起手真窗：全屏状态
        real_end="w039_finish_post_0.png",      # 收尾真窗：普通窗口 REAL
        b_available=True, seg="restore",
    ),
    "enter": dict(
        A="w005_cover_tick.png",           # A 源：普通窗口版式快照（ar 1.383）
        # 真实代码进全屏时 B=None（无目标版式快照）。这里用「全屏真窗画面」作为
        # 假想 B 的最佳代理，用于评估「若全程 B」是否可行（S4）。
        B="w016_finish_post_0.png",
        real_start="w002_finish_post_0.png",    # 起手真窗：普通窗口 REAL
        real_end="w016_finish_post_0.png",      # 收尾真窗：全屏 REAL
        b_available=False, seg="enter",
    ),
}

SCHEMES = [
    ("S0", {}),
    ("S1a", {"theta": 0.90}),
    ("S1b", {"theta": 0.94}),
    ("S1c", {"theta": 0.97}),
    ("S2", {"t0": 0.90}),          # 交叉溶解窗口 [t0,1.0]
    ("S2b", {"t0": 0.94}),
    ("S2c", {"t0": 0.97}),
    ("S3_s6", {"sigma_max": 6.0}),
    ("S3_s10", {"sigma_max": 10.0}),
    ("S3_s14", {"sigma_max": 14.0}),
    ("S4", {}),
]

# ---------------------------------------------------------------- 基础工具
def raw(name):
    """加载源图，保留原始宽高比（不预缩放，供 render 按真实比例裁切）。"""
    return np.asarray(Image.open(os.path.join(EV, name)).convert("RGB")).astype(np.float32)


def to_canvas(a):
    """把参考帧统一到 CANVAS（仅用于与参考真窗/真值帧比较）。"""
    im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    if im.size != CANVAS:
        im = im.resize(CANVAS, Image.LANCZOS)
    return np.asarray(im).astype(np.float32)


def luma(a):
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def metrics(a, b):
    """口径（已复现取证基准）：全分辨率 RGB |diff|.mean，
    >25 面积 = Rec709 灰度 |Δ|>25 的像素占比，Δluma = Rec709 均值差（signed）。"""
    d = np.abs(a - b)
    return dict(
        mean=round(float(d.mean()), 3),
        max=round(float(d.max()), 1),
        area=round(float((np.abs(luma(b) - luma(a)) > 25).mean() * 100), 3),
        dluma=round(float(luma(b).mean() - luma(a).mean()), 3),
    )


def render(src, label_ar):
    """复现 _CoverLabel.paintEvent 的 KeepAspectRatioByExpanding：
    源图按自身比例铺满 label，溢出的边被对称裁掉，label 截图再归一到 CANVAS。
    经真实帧校准：render(w026, 1.3829) ≈ w035（mean=0.42）。"""
    h, w = src.shape[:2]
    sar = w / h
    im = Image.fromarray(np.clip(src, 0, 255).astype(np.uint8))
    if sar > label_ar:                       # 源更宽 → 左右对称裁
        nw = int(round(h * label_ar))
        x = (w - nw) // 2
        crop = im.crop((x, 0, x + nw, h))
    else:                                    # 源更窄 → 上下对称裁
        nh = int(round(w / label_ar))
        y = (h - nh) // 2
        crop = im.crop((0, y, w, y + nh))
    return np.asarray(crop.resize(CANVAS, Image.LANCZOS)).astype(np.float32)


def edge_energy(a):
    """灰度梯度幅值均值，用作双影（边缘叠加）判据。"""
    g = luma(a)
    return float(np.abs(np.diff(g, axis=0)).mean() + np.abs(np.diff(g, axis=1)).mean())


def blur_rgb(frame, sigma):
    """对 RGB 帧做真高斯（复用 splash_motion._gaussian 的 ndarray 接口）。"""
    if sigma <= 0.05:
        return frame
    u = np.clip(frame, 0, 255).astype(np.uint8)
    rgba = np.ascontiguousarray(
        np.concatenate([u, np.full(u.shape[:2] + (1,), 255, np.uint8)], axis=2))
    out = _gaussian(rgba, float(sigma))
    return out[..., :3].astype(np.float32)


def smoothstep(s):
    s = min(1.0, max(0.0, s))
    return s * s * (3.0 - 2.0 * s)


def sigma_at(t, sigma_max):
    """模糊脉冲：0.90→0.94 升、0.94→0.96 平顶(σmax)、0.96→1.0 降。
    换源点置于平顶中央 t=0.95（最模糊那一帧）。"""
    if t < 0.90:
        return 0.0
    if t < 0.94:
        return sigma_max * (t - 0.90) / 0.04
    if t <= 0.96:
        return sigma_max
    return sigma_max * max(0.0, (1.0 - t) / 0.04)


def quantize_sigma(sig, sigma_max):
    """预生成 5 档 σ：0 / 0.25 / 0.5 / 0.75 / 1.0 × σmax，取最近档。"""
    levels = [0.0, 0.25, 0.5, 0.75, 1.0]
    return min(levels, key=lambda q: abs(sig - q * sigma_max)) * sigma_max

# ---------------------------------------------------------------- 几何序列
_STRETCH = json.load(open(STRETCH, encoding="utf-8"))


def build_geom(seg):
    """取 stretch.json 对应方向段，wall 时间归一化到动画进度 t∈[0,1]，
    返回 (tns, ars) 供 np.interp 得 label_ar(t)。"""
    pts = [p for p in _STRETCH if (p["fs"] if seg == "enter" else not p["fs"])]
    t0, t1 = pts[0]["t"], pts[-1]["t"]
    tns = np.array([(p["t"] - t0) / (t1 - t0) for p in pts])
    ars = np.array([p["label_w"] / p["label_h"] for p in pts])
    return tns, ars


GEOM = {seg: build_geom(seg) for seg in ("restore", "enter")}

# ---------------------------------------------------------------- 方案帧生成
def gen_frames(direction, sid, params):
    cfg = DIRECTIONS[direction]
    tns, ars = GEOM[cfg["seg"]]
    A_src, B_src = raw(cfg["A"]), raw(cfg["B"])
    ar_t = {t: float(np.interp(t, tns, ars)) for t in TS}
    FA = {t: render(A_src, ar_t[t]) for t in TS}
    FB = {t: render(B_src, ar_t[t]) for t in TS}

    frames, tags, lvl_t = {}, {}, {}
    bc = {}                                  # 模糊帧缓存

    def do_blur(t, tag, lvl):
        key = (t, tag, round(lvl, 3))
        if key not in bc:
            bc[key] = blur_rgb(FA[t] if tag == "A" else FB[t], lvl)
        return bc[key]

    sm = params.get("sigma_max", 10.0)
    for t in TS:
        a, b = FA[t], FB[t]
        lvl = 0.0                            # 该帧实际采用的高斯 σ 档（仅 S3 非 0）
        if sid == "S0":
            # 现状：t>=0.99 且 B 存在才换；进全屏 B=None 时全程 A
            if t < 0.99 or not cfg["b_available"]:
                f, tag = a, "A"
            else:
                f, tag = b, "B"
        elif sid.startswith("S1"):
            if t < params["theta"]:
                f, tag = a, "A"
            else:
                f, tag = b, "B"
        elif sid.startswith("S2"):
            t0 = params.get("t0", 0.90)
            w = smoothstep((t - t0) / (1.0 - t0))
            f = a * (1.0 - w) + b * w
            tag = "A" if w < 1e-6 else ("B" if w > 1 - 1e-6 else "mix")
        elif sid.startswith("S3"):
            sig = sigma_at(t, sm)
            lvl = quantize_sigma(sig, sm)
            tag = ("A" if t < 0.95 else "B") + ("/blur" if lvl > 0.05 else "")
            f = do_blur(t, "A" if t < 0.95 else "B", lvl)
        elif sid == "S4":
            f, tag = b, "B"
        else:
            raise ValueError(sid)
        frames[t], tags[t], lvl_t[t] = f, tag, lvl
    return dict(frames=frames, tags=tags, FA=FA, FB=FB, ar_t=ar_t, lvl_t=lvl_t)

# ---------------------------------------------------------------- 方案评估
def evaluate(direction, sid, params, G):
    cfg = DIRECTIONS[direction]
    frames, tags, FA, FB = G["frames"], G["tags"], G["FA"], G["FB"]
    lvl_t = G["lvl_t"]

    # 1) 相邻帧曲线。标注 dt：前段为稀疏采样（dt=0.2），其"大跳"只是采样间隔大，
    #    不参与峰值判定；仅 dt<=0.02 的末段均匀采样段可比。
    curve = []
    for i in range(len(TS) - 1):
        t0, t1 = TS[i], TS[i + 1]
        m = metrics(frames[t0], frames[t1])
        curve.append(dict(t_from=t0, t_to=t1, dt=round(t1 - t0, 3),
                          tag_from=tags[t0], tag_to=tags[t1], **m))

    fair = [c for c in curve if c["dt"] <= 0.02]
    peak = max(fair, key=lambda c: c["mean"])                     # 公平峰值帧
    tail = [c for c in fair if c["t_from"] >= 0.85 - 1e-9]
    tail_peak = max(tail, key=lambda c: c["mean"])               # 收尾段峰值（含各换源点）
    geom_bg = max((c for c in curve if c["dt"] > 0.02),
                  key=lambda c: c["mean"], default=None)         # 前段几何背景（仅参考）

    # 2) 换源帧突变：硬切取源标签变化处；交叉溶解/全程单源无硬切，
    #    统一取「收尾段最大单步 mean」作为等效换源突变，便于与 S0 公平比较。
    swaps = [c for c in curve if c["tag_from"] != c["tag_to"]]
    if sid.startswith("S2"):
        swap = tail_peak
        swap_kind = "dissolve(无硬切)"
    elif swaps:
        swap = max(swaps, key=lambda c: c["mean"])
        swap_kind = "hard"
    else:
        swap = tail_peak
        swap_kind = "none(无硬切)"

    # 3) 双影判据（对末段中间帧）
    # dual = min(|M−refA|,|M−refB|)/|refA−refB|：纯源/硬切≈0，线性混合 w=0.5 时≈0.5。
    #   refA/refB 取「该帧所处模糊档」下的纯源：S3 用同 σ 模糊参照，避免把"变糊"
    #   误判成双影；其余方案用清晰 FA/FB。
    # edge_excess = edge(M) − max(edge(refA),edge(refB))：>0 才表示两套边缘叠加。
    ghosts = []
    _bc = {}
    def _bl(t, which, lvl):
        k = (t, which, round(lvl, 3))
        if k not in _bc:
            _bc[k] = blur_rgb(FA[t] if which == "A" else FB[t], lvl)
        return _bc[k]
    for t in TS:
        if t < 0.90 - 1e-9:
            continue
        m = frames[t]
        lvl = lvl_t[t]
        if lvl > 0.05:
            a, b = _bl(t, "A", lvl), _bl(t, "B", lvl)
        else:
            a, b = FA[t], FB[t]
        denom = float(np.abs(a - b).mean()) or 1.0
        dual = min(float(np.abs(m - a).mean()), float(np.abs(m - b).mean())) / denom
        ex = edge_energy(m) - max(edge_energy(a), edge_energy(b))
        # 双影可见面积：两套内容在该像素同时显著（min 残差 > 12.5）的像素占比
        ghost_map = np.minimum(np.abs(m - a), np.abs(m - b)).max(axis=2)
        darea = float((ghost_map > 12.5).mean() * 100)
        ghosts.append(dict(t=t, dual=round(dual, 4), edge_excess=round(float(ex), 4),
                           dual_area=round(darea, 3)))
    ghost_peak = max(ghosts, key=lambda g: g["dual"])
    edge_peak = max(ghosts, key=lambda g: g["edge_excess"])
    dual_area_peak = max(ghosts, key=lambda g: g["dual_area"])

    # 4) 起手 / 收尾 vs 真窗画面
    real_start = to_canvas(raw(cfg["real_start"]))
    real_end = to_canvas(raw(cfg["real_end"]))
    start_vs_real = metrics(frames[0.0], real_start)
    end_vs_real = metrics(frames[1.0], real_end)

    return dict(
        direction=direction, scheme=sid, params=params,
        curve=curve,
        peak=peak, tail_peak=tail_peak, geom_bg=geom_bg,
        swap=swap, swap_kind=swap_kind,
        ghost_peak=ghost_peak, edge_peak=edge_peak,
        dual_area_peak=dual_area_peak, ghosts=ghosts,
        start_vs_real=start_vs_real, end_vs_real=end_vs_real,
    )

# ---------------------------------------------------------------- 绘图
def font(sz):
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
              "C:/Windows/Fonts/msyhbd.ttc"):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()


def thumb(a, size=(300, 217)):
    im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGB")
    return im.resize(size, Image.LANCZOS)


def make_sheet(direction, sid, params, G, R, path):
    """每方案一张关键帧 contact sheet：restore/enter 方向 KEY_T 帧 + 真窗收尾对照。"""
    cfg = DIRECTIONS[direction]
    frames, tags = G["frames"], G["tags"]
    real_end = to_canvas(raw(cfg["real_end"]))
    cols = 4
    items = [(t, frames[t], "t=%.2f  src=%s" % (t, tags[t])) for t in KEY_T]
    items.append((1.0, real_end, "REAL 真窗(收尾)"))
    rows = (len(items) + cols - 1) // cols
    TW, TH, BAR, HEAD = 300, 217, 24, 46
    W, H = cols * TW, HEAD + rows * (TH + BAR)
    sheet = Image.new("RGB", (W, H), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    f_title, f_bar = font(18), font(13)
    title = "%s / %s  |  换源突变 mean=%.2f area=%.2f%%  |  峰值帧 %.2f→%.2f mean=%.2f  |  双影 dual=%.3f" % (
        sid, direction, R["swap"]["mean"], R["swap"]["area"],
        R["peak"]["t_from"], R["peak"]["t_to"], R["peak"]["mean"], R["ghost_peak"]["dual"])
    d.text((10, 10), title, fill=(235, 235, 240), font=f_title)
    for i, (t, img, label) in enumerate(items):
        r, c = divmod(i, cols)
        x, y = c * TW, HEAD + r * (TH + BAR)
        sheet.paste(thumb(img), (x, y))
        d.rectangle((x, y + TH, x + TW, y + TH + BAR), fill=(16, 16, 20))
        d.text((x + 6, y + TH + 4), label, fill=(200, 220, 255), font=f_bar)
    sheet.save(path)


def make_enter_sheet(results, path):
    """enter 方向：各方案收尾帧 vs 全屏真窗（专门展示亮度塌陷 / 露底）。"""
    cfg = DIRECTIONS["enter"]
    real_end = to_canvas(raw(cfg["real_end"]))
    sid_list = ["S0", "S1a", "S1b", "S1c", "S2", "S2b", "S2c",
                "S3_s6", "S3_s10", "S3_s14", "S4"]
    cols = 5
    items = []
    for sid in sid_list:
        G = GEN[("enter", sid)]
        ev = results[("enter", sid)]["end_vs_real"]
        items.append((sid, G["frames"][1.0], ev))
    items.append(("REAL", real_end, None))
    rows = (len(items) + cols - 1) // cols
    TW, TH, BAR, HEAD = 260, 188, 22, 60
    W, H = cols * TW, HEAD + rows * (TH + BAR)
    sheet = Image.new("RGB", (W, H), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    d.text((10, 8), "ENTER 方向收尾帧（覆盖层 hide 前最后显示）对照 —— 最右为全屏真窗 REAL",
           fill=(235, 235, 240), font=font(16))
    d.text((10, 32), "S0 全程 A（B=None）与真窗版式/亮度不符 → 收尾 hide 露真窗即 7.9/4.7% / Δluma−2.6 亮度塌陷",
           fill=(255, 180, 120), font=font(13))
    for i, (sid, img, ev) in enumerate(items):
        r, c = divmod(i, cols)
        x, y = c * TW, HEAD + r * (TH + BAR)
        sheet.paste(thumb(img, (TW, TH)), (x, y))
        d.rectangle((x, y + TH, x + TW, y + TH + BAR), fill=(16, 16, 20))
        if ev is None:
            label = "REAL 全屏真窗(基准)"
        else:
            label = "%s  Δ真窗 m=%.1f/%.1f%%" % (sid, ev["mean"], ev["area"])
        d.text((x + 6, y + TH + 3), label,
               fill=(200, 220, 255), font=font(12))
    sheet.save(path)


def make_curves(results, path):
    """各方案 restore 方向相邻帧 mean 曲线 + S0 基准线。"""
    W, H, PAD = 1000, 460, 60
    img = Image.new("RGB", (W, H), (20, 20, 24))
    d = ImageDraw.Draw(img)
    f = font(12)
    x0, x1, y0, y1 = PAD, W - 20, 40, H - 40
    ymax = 10.0
    def X(t): return x0 + (x1 - x0) * (t - 0.85) / 0.15
    def Y(v): return y1 - (y1 - y0) * min(1.0, v / ymax)
    for gy in range(0, 11, 2):
        yy = Y(gy)
        d.line((x0, yy, x1, yy), fill=(55, 55, 62))
        d.text((10, yy - 7), "%d" % gy, fill=(150, 150, 160), font=f)
    for gx in (0.85, 0.90, 0.94, 0.97, 0.99, 1.0):
        xx = X(gx)
        d.line((xx, y0, xx, y1), fill=(45, 45, 52))
        d.text((xx - 10, y1 + 6), "%.2f" % gx, fill=(150, 150, 160), font=f)
    colors = {"S0": (255, 90, 90), "S1a": (255, 180, 60), "S1b": (255, 210, 90),
              "S1c": (255, 235, 120), "S2": (120, 200, 255),
              "S2b": (90, 160, 255), "S2c": (60, 120, 255),
              "S3_s6": (140, 255, 160), "S3_s10": (90, 235, 120),
              "S3_s14": (60, 200, 90), "S4": (200, 140, 255)}
    for sid, col in colors.items():
        cv = [c for c in results[("restore", sid)]["curve"] if c["dt"] <= 0.02]
        pts = [(X(c["t_to"]), Y(c["mean"])) for c in cv]
        d.line(pts, fill=col, width=2)
    # 图例
    lx, ly = x0 + 6, 44
    for i, (sid, col) in enumerate(colors.items()):
        d.rectangle((lx, ly + i * 15, lx + 12, ly + i * 15 + 10), fill=col)
        d.text((lx + 16, ly + i * 15 - 2), sid, fill=(210, 210, 215), font=f)
    # 基准线
    d.line((x0, Y(8.14), x1, Y(8.14)), fill=(255, 90, 90))
    d.text((x0 + 4, Y(8.14) - 15), "S0 换源基准 mean=8.14 (真实 w035→w036)",
           fill=(255, 120, 120), font=f)
    d.text((x0, 8), "RESTORE 方向 收尾段 t=0.85~1.00 相邻帧 mean（仅 dt<=0.02 均匀采样，越低越平）",
           fill=(235, 235, 240), font=font(15))
    img.save(path)

# ---------------------------------------------------------------- 主流程
def calibration():
    """固化建模校准：证明 render() 与真实取证帧一致。"""
    A = raw("w026_cover_tick.png")
    w035 = to_canvas(raw("w035_cover_tick.png"))
    w036 = to_canvas(raw("w036_cover_tick.png"))
    w005 = raw("w005_cover_tick.png")
    w013 = to_canvas(raw("w013_cover_tick.png"))
    w016 = to_canvas(raw("w016_finish_post_0.png"))
    return {
        "render(w026,ar1.3829)_vs_真实w035": metrics(render(A, 1.3829), w035),
        "render(w026,ar1.3829)_vs_真实w036(B)": metrics(render(A, 1.3829), w036),
        "真实w035_vs_w036": metrics(w035, w036),
        "真实w013_vs_w016(enter亮度塌陷)": metrics(w013, w016),
        "render(w005,ar1.6)_vs_真窗w016": metrics(render(w005, 1.6), w016),
        "真实w037_vs_w038(换B后收尾)": metrics(
            to_canvas(raw("w037_finish_pre_cover.png")),
            to_canvas(raw("w038_finish_pre_main.png"))),
    }


def main():
    calib = calibration()
    results, GEN_G = {}, {}
    GEN.clear()
    for direction in DIRECTIONS:
        for sid, params in SCHEMES:
            G = gen_frames(direction, sid, params)
            GEN[(direction, sid)] = G
            R = evaluate(direction, sid, params, G)
            results[(direction, sid)] = R

    # ---- 输出结构化数值 ----
    out = {"canvas": CANVAS, "time_axis": TS,
           "metric_def": "mean=|RGB diff|.mean; area=Rec709|Δluma|>25 像素%; dluma=Rec709 均值差; max=|diff|.max",
           "calibration": calib,
           "directions": {}}
    for direction in DIRECTIONS:
        out["directions"][direction] = {}
        for sid, _ in SCHEMES:
            R = results[(direction, sid)]
            out["directions"][direction][sid] = {
                "params": R["params"], "peak": R["peak"], "tail_peak": R["tail_peak"],
                "geom_bg": R["geom_bg"],
                "swap": R["swap"], "swap_kind": R["swap_kind"],
                "ghost_peak": R["ghost_peak"], "edge_peak": R["edge_peak"],
                "dual_area_peak": R["dual_area_peak"],
                "start_vs_real": R["start_vs_real"], "end_vs_real": R["end_vs_real"],
                "curve": R["curve"],
            }
    with open(os.path.join(OUT, "metrics.json"), "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)

    # ---- contact sheets ----
    for sid, params in SCHEMES:
        G = GEN[("restore", sid)]
        R = results[("restore", sid)]
        make_sheet("restore", sid, params, G, R,
                   os.path.join(OUT, "sheet_%s.png" % sid))
    make_enter_sheet(results, os.path.join(OUT, "sheet_enter.png"))
    make_curves(results, os.path.join(OUT, "curves.png"))

    # ---- 控制台摘要 ----
    print("=== 建模校准（与真实取证帧比对）===")
    for k, v in calib.items():
        print("  %-42s mean=%.2f area=%.2f%% dluma=%+.2f" % (k, v["mean"], v["area"], v["dluma"]))
    print("\n=== RESTORE 方向（主战场，S0 真实基准 mean=8.14 area=4.72%）===")
    print("%-7s %-6s %-23s %-20s %-6s %-9s %-13s %-13s" % (
        "方案", "换源", "换源突变 mean/area/dluma", "收尾峰值 t→t/mean",
        "dual", "双影面积%", "起手vs真窗", "收尾vs真窗"))
    s0swap = results[("restore", "S0")]["swap"]
    for sid, _ in SCHEMES:
        R = results[("restore", sid)]
        sw, tp, gp, dp = R["swap"], R["tail_peak"], R["ghost_peak"], R["dual_area_peak"]
        print("%-7s %-6s %6.2f/%5.2f%%/%+.2f      %.2f→%.2f /%.2f       %.3f   %6.2f      %6.2f        %6.2f" % (
            sid, R["swap_kind"][:6], sw["mean"], sw["area"], sw["dluma"],
            tp["t_from"], tp["t_to"], tp["mean"], gp["dual"], dp["dual_area"],
            R["start_vs_real"]["mean"], R["end_vs_real"]["mean"]))
    print("  注：峰值/收尾峰值均取 dt<=0.02 均匀采样段；前段 dt=0.2 的稀疏采样跳变仅为几何背景。")
    print("\n  相对 S0 换源突变改善 (mean / area)：")
    for sid, _ in SCHEMES:
        sw = results[("restore", sid)]["swap"]
        im = (s0swap["mean"] - sw["mean"]) / s0swap["mean"] * 100
        ia = (s0swap["area"] - sw["area"]) / s0swap["area"] * 100
        note = ""
        if results[("restore", sid)]["swap_kind"].startswith("none"):
            note = "  ← 无硬切，取末段等效单步"
        print("    %-8s mean %+6.1f%%   area %+6.1f%%%s" % (sid, im, ia, note))
    print("\n=== ENTER 方向（收尾 hide 露真窗差异；S0 全程 A 亮度塌陷）===")
    for sid, _ in SCHEMES:
        R = results[("enter", sid)]
        print("  %-8s 收尾vs真窗 mean=%.2f area=%.2f%% dluma=%+.2f | 起手vs真窗 mean=%.2f | 峰值 mean=%.2f" % (
            sid, R["end_vs_real"]["mean"], R["end_vs_real"]["area"], R["end_vs_real"]["dluma"],
            R["start_vs_real"]["mean"], R["peak"]["mean"]))
    print("\n产物目录:", OUT)
    print("  metrics.json / sheet_S0..S4.png / sheet_enter.png / curves.png")


GEN = {}
if __name__ == "__main__":
    main()
