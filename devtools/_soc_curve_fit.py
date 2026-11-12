"""把 E74 的 1C 放电曲线（Excel 原始 62 点）与代码里的 OCV 表做逐点对照。

目的
----
`soc_est.OCV_TABLE` 是从工步 5 的 62 个点按 ``OCV = V + 82 mV`` 平移后
**稀疏插值到 26 点**得到的。稀疏网格在曲线弯曲段（尤其 0~10% 与 90~100%）
会引入插值误差。本脚本把 62 点原始折线当作真值，做：

* 正向扫描：SOC 域 0~100 每 0.05%，``ocv_from_soc(表)`` 与真值的差（mV）
* 反向扫描：OCV 域逐 0.2 mV，``soc_from_ocv(表)`` 与真值反查的差（%SOC）
  —— 这才是 App 底栏真正显示的误差
* 台架实测工作点 4.177~4.208 V 的误差
* 候选高精度表（原始 62 点 / 1% 等距重采样 / 自适应加密）的误差对比
* 斜率（κ 权重）抖动评估：表加密后 ``slope_at`` 会不会变得不稳

只读：不改任何产品源码，不写 QSettings。产物写到 ``_check/soc_fit/``。

用法
----
    .venv/Scripts/python.exe devtools/_soc_curve_fit.py
    .venv/Scripts/python.exe devtools/_soc_curve_fit.py --emit   # 额外导出候选表
"""

from __future__ import annotations

import argparse
import bisect
import math
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import soc_est as S  # noqa: E402

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XLSX = os.path.join(ROOT, "_check", "soc_fit", "12Ah_curve_uploaded.xlsx")
OUT_DIR = os.path.join(ROOT, "_check", "soc_fit")
IR_MV = 82.0  # 1C(12A) x R0(6.8mOhm) = 81.6mV，取整 82mV

# v2.7 基线表（26 点）。固化在这里，是为了让本脚本在产品表被替换之后
# 仍然能复现「旧表 vs 新表 vs 原始曲线」的对照，不受 soc_est.py 当前内容影响。
BASE_TABLE: tuple[tuple[float, float], ...] = (
    (0, 3.0818), (2, 3.4516), (4, 3.5595), (6, 3.6099), (8, 3.6385),
    (10, 3.6581), (15, 3.6908), (20, 3.7128), (25, 3.7337), (30, 3.7582),
    (35, 3.7872), (40, 3.8208), (45, 3.8595), (50, 3.9038), (55, 3.9546),
    (60, 4.0118), (65, 4.0744), (70, 4.1400), (75, 4.2066), (80, 4.2695),
    (85, 4.3236), (90, 4.3672), (95, 4.4016), (97, 4.4155), (99, 4.4558),
    (100, 4.4981),
)


# ------------------------------------------------------------------ xlsx 读取


def _col_idx(ref: str) -> int:
    s = re.match(r"([A-Z]+)", ref).group(1)
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _read_xlsx(path: str):
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
            shared.append("".join(t.text or "" for t in si.iter(NS + "t")))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    relmap = {r.get("Id"): r.get("Target") for r in rels}
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheets = []
    for sh in wb.find(NS + "sheets"):
        rid = sh.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        tgt = relmap.get(rid, "")
        if tgt and not tgt.startswith("xl/"):
            tgt = "xl/" + tgt.lstrip("/")
        sheets.append((sh.get("name"), tgt))

    def read_sheet(target):
        out = []
        for row in ET.fromstring(z.read(target)).find(NS + "sheetData"):
            d = {}
            for c in row:
                k = _col_idx(c.get("r"))
                v, ise = c.find(NS + "v"), c.find(NS + "is")
                if ise is not None:
                    val = "".join(x.text or "" for x in ise.iter(NS + "t"))
                elif v is None:
                    val = None
                elif c.get("t") == "s":
                    val = shared[int(v.text)]
                else:
                    try:
                        val = float(v.text)
                    except ValueError:
                        val = v.text
                d[k] = val
            if d:
                out.append([d.get(i) for i in range(max(d) + 1)])
        return out

    return z, sheets, read_sheet


def load_truth():
    """返回 (truth_soc, truth_ocv, q_ah, raw_points)，曲线已按 SOC 升序。"""
    z, sheets, read_sheet = _read_xlsx(XLSX)
    detail = dict(sheets).get("Detail_222_1_6")
    if not detail:
        raise RuntimeError("找不到 Detail_222_1_6 工作表")
    rows = read_sheet(detail)
    hdr = rows[0]
    ci = {name: i for i, name in enumerate(hdr) if isinstance(name, str)}
    c_step, c_cap, c_volt = ci["步次"], ci["容量(Ah)"], ci["电压(V)"]
    pts = []
    for r in rows[1:]:
        if len(r) <= max(c_step, c_cap, c_volt):
            continue
        if r[c_step] == 5.0 and isinstance(r[c_cap], float) and isinstance(r[c_volt], float):
            pts.append((r[c_cap], r[c_volt]))
    q_ah = pts[-1][0]
    # SOC = (1 - cap/Q)*100，OCV = V + 82mV；原始点是容量升序 -> SOC 降序，翻转
    trip = [((1.0 - cap / q_ah) * 100.0, volt + IR_MV / 1000.0) for cap, volt in pts]
    trip.sort(key=lambda p: p[0])
    xs = [p[0] for p in trip]
    ys = [p[1] for p in trip]
    z.close()
    return xs, ys, q_ah, pts


# ------------------------------------------------------------------ 折线工具


def interp(xs, ys, x):
    """严格单调折线插值（不外推）。"""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_left(xs, x)
    if xs[i] == x:
        return ys[i]
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def inverse(xs, ys, y):
    """折线反查：y -> x（不外推）。"""
    if y <= ys[0]:
        return xs[0]
    if y >= ys[-1]:
        return xs[-1]
    i = bisect.bisect_left(ys, y)
    if ys[i] == y:
        return xs[i]
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return x0 + (x1 - x0) * (y - y0) / (y1 - y0)


def as_table(xs, ys):
    return tuple((round(a, 4), round(b, 6)) for a, b in zip(xs, ys))


# ------------------------------------------------------------------ 误差度量


def measure(table, txs, tys):
    """正向 mV 误差 + 反向 %SOC 误差 + 台架工作点误差。"""
    fwd_x, fwd_e = [], []
    s = 0.0
    while s <= 100.0 + 1e-9:
        got = S.ocv_from_soc(s, table) * 1000.0
        ref = interp(txs, tys, s) * 1000.0
        fwd_x.append(s)
        fwd_e.append(got - ref)
        s += 0.05
    fwd_abs = [abs(e) for e in fwd_e]
    i_fwd = max(range(len(fwd_e)), key=lambda k: fwd_abs[k])

    rev_x, rev_e = [], []
    v = tys[0] * 1000.0
    vmax = tys[-1] * 1000.0
    while v <= vmax + 1e-9:
        vv = v / 1000.0
        got = S.soc_from_ocv(vv, table)
        ref = inverse(txs, tys, vv)
        rev_x.append(vv)
        rev_e.append(got - ref)
        v += 0.2
    rev_abs = [abs(e) for e in rev_e]
    i_rev = max(range(len(rev_e)), key=lambda k: rev_abs[k])

    def rms(a):
        return math.sqrt(sum(x * x for x in a) / len(a))

    bench = [(vv, S.soc_from_ocv(vv, table) - inverse(txs, tys, vv))
             for vv in (4.177, 4.184, 4.189, 4.200, 4.208)]

    return {
        "npts": len(table),
        "fwd_max_mv": max(fwd_abs),
        "fwd_at_soc": fwd_x[i_fwd],
        "fwd_signed_mv": fwd_e[i_fwd],
        "fwd_rms_mv": rms(fwd_e),
        "rev_max_soc": max(rev_abs),
        "rev_at_v": rev_x[i_rev] * 1000.0,
        "rev_signed_soc": rev_e[i_rev],
        "rev_rms_soc": rms(rev_e),
        "bench": bench,
        "bench_max_soc": max(abs(e) for _, e in bench),
        "fwd_x": fwd_x, "fwd_e": fwd_e,
        "rev_x": rev_x, "rev_e": rev_e,
    }


def slope_jitter(table):
    """相邻段斜率跳变（mV/%）的标准差 —— 衡量 κ 权重会不会抖。"""
    slopes = []
    for i in range(1, len(table)):
        x0, y0 = table[i - 1]
        x1, y1 = table[i]
        if x1 > x0:
            slopes.append((y1 - y0) * 1000.0 / (x1 - x0))
    dif = [abs(slopes[i] - slopes[i - 1]) for i in range(1, len(slopes))]
    if not dif:
        return 0.0, 0.0, 0.0
    mean = sum(dif) / len(dif)
    sd = math.sqrt(sum((d - mean) ** 2 for d in dif) / len(dif))
    return mean, max(dif), sd


# ------------------------------------------------------------------ 候选表


def cands(txs, tys):
    out = []
    out.append(("v2.7 基线 26 点表", BASE_TABLE))
    out.append(("当前生效表 S.OCV_TABLE", S.OCV_TABLE))
    out.append(("原始 62 点表（直接平移）", as_table(txs, tys)))

    # 1% 等间距重采样（含 0 与 100，端点强制对齐真值端点）
    xs1 = [float(i) for i in range(101)]
    ys1 = [interp(txs, tys, x) for x in xs1]
    ys1[0], ys1[-1] = tys[0], tys[-1]
    out.append(("1% 等距 101 点", as_table(xs1, ys1)))

    # 2% 等间距（含 100）
    xs2 = [float(i) for i in range(0, 101, 2)]
    ys2 = [interp(txs, tys, x) for x in xs2]
    ys2[0], ys2[-1] = tys[0], tys[-1]
    out.append(("2% 等距 51 点", as_table(xs2, ys2)))

    # 自适应加密：从 26 点网格起步，误差最大的段反复插中点，直到
    # 正向最大电压误差 < 0.5 mV 或点数到 80
    grid = sorted(set([0.0, 100.0] + [float(p[0]) for p in BASE_TABLE]))
    for _ in range(120):
        tbl = tuple((x, interp(txs, tys, x)) for x in grid)
        errs = []
        for i in range(1, len(grid)):
            a, b = grid[i - 1], grid[i]
            mid = (a + b) / 2.0
            got = S.ocv_from_soc(mid, tbl) * 1000.0
            ref = interp(txs, tys, mid) * 1000.0
            errs.append((abs(got - ref), i, mid))
        worst = max(errs)
        if worst[0] < 0.5 or len(grid) >= 80:
            break
        grid.append(worst[2])
        grid.sort()
    out.append(("自适应加密表", as_table(grid, [interp(txs, tys, x) for x in grid])))

    # 混合加密表：只把两端（0~10%、95~100%）换成真值折线的高密度点，
    # 中间 10~95% 逐点保留当前 26 点表的原 SOC / 原 OCV。
    # 目的：静态查表误差大幅下降，但 slope_at / kappa_at / EKF 的 H[0]
    # 在 10~95% 工作区内逐位不变 —— 已验证过的动态行为零改动。
    # 只替换 0~8% 与 95~100%：8%、10%、15%...90%、95% 这些原表点全部保留，
    # 于是 slope_at(10) 仍落在原表的 8~10 段、slope_at(95) 仍落在 90~95 段，
    # 10~95% 工作区的斜率/kappa 逐位不变。真值在 8% 与 10% 处与原表值
    # 分别只差 0.0 / 0.1 mV，所以边界天然连续。
    lo_pts = [(x, y) for x, y in zip(txs, tys) if 0 < x < 8]
    hi_pts = [(x, y) for x, y in zip(txs, tys) if 95 < x < 100]
    mid_pts = [(float(a), float(b)) for a, b in BASE_TABLE if 8 <= a <= 95]
    hybrid = [(0.0, BASE_TABLE[0][1])] + lo_pts + mid_pts + hi_pts
    hybrid.append((100.0, BASE_TABLE[-1][1]))
    hybrid.sort(key=lambda p: p[0])
    # 去掉可能因浮点造成的重复 SOC
    dedup = []
    for x, y in hybrid:
        if dedup and abs(x - dedup[-1][0]) < 1e-6:
            dedup[-1] = (x, y)
        else:
            dedup.append((x, y))
    out.append(("两端加密混合表", as_table([p[0] for p in dedup], [p[1] for p in dedup])))
    return out


def dynamic_invariance(new_table):
    """验证 10~95% 工作区内 slope_at / kappa_at 逐位不变。"""
    diffs = []
    s = 10.0
    while s <= 95.0 + 1e-9:
        a = S.slope_at(s, BASE_TABLE)
        b = S.slope_at(s, new_table)
        ka = S.kappa_at(s, BASE_TABLE)
        kb = S.kappa_at(s, new_table)
        if a != b or ka != kb:
            diffs.append((s, a, b, ka, kb))
        s += 0.5
    return diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true", help="导出候选表到 _check/soc_fit/")
    args = ap.parse_args()

    txs, tys, q_ah, raw = load_truth()
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"曲线: 工步5 {len(raw)} 点, Q={q_ah:.6f} Ah, "
          f"SOC {txs[0]:.2f}..{txs[-1]:.2f}%, OCV {tys[0]:.4f}..{tys[-1]:.4f} V")
    print(f"当前表: {len(S.OCV_TABLE)} 点, 端点 {S.OCV_TABLE[0]} .. {S.OCV_TABLE[-1]}\n")

    results = []
    for name, tbl in cands(txs, tys):
        m = measure(tbl, txs, tys)
        jm, jx, jsd = slope_jitter(tbl)
        results.append((name, tbl, m, jm, jx))
        print(f"--- {name} ({m['npts']} 点) ---")
        print(f"  正向 OCV 误差: max {m['fwd_max_mv']:6.2f} mV @SOC {m['fwd_at_soc']:5.2f}% "
              f"(有符号 {m['fwd_signed_mv']:+6.2f})  RMS {m['fwd_rms_mv']:5.2f} mV")
        print(f"  反向 SOC 误差: max {m['rev_max_soc']:6.3f} %SOC @{m['rev_at_v']:7.1f} mV "
              f"(有符号 {m['rev_signed_soc']:+6.3f})  RMS {m['rev_rms_soc']:5.3f} %")
        print(f"  台架 4.177~4.208V: max {m['bench_max_soc']:.3f} %SOC  "
              f"逐点 " + " ".join(f"{v*1000:.0f}mV:{e:+.3f}" for v, e in m["bench"]))
        print(f"  κ 斜率跳变: 平均 {jm:5.2f} mV/%, 最大 {jx:5.2f} mV/%, 标准差 {jsd:5.2f} mV/%")
        inv = dynamic_invariance(tbl)
        print(f"  10~95% 工作区 slope/κ 不变性: "
              f"{'逐位完全一致' if not inv else f'{len(inv)} 处改变'}\n")

    # 误差最大的 10 个 SOC 区段（当前表）
    base = results[0][2]
    seg = {}
    for s, e in zip(base["fwd_x"], base["fwd_e"]):
        k = int(s // 5) * 5
        if k not in seg or abs(e) > abs(seg[k]):
            seg[k] = e
    print("=== 当前表：每 5%SOC 区间的最大正向误差（mV） ===")
    for k in sorted(seg):
        bar = "#" * int(min(40, abs(seg[k]) / 0.5))
        print(f"  SOC {k:3d}-{k+5:3d}%: {seg[k]:+7.2f} mV  {bar}")

    print("\n=== 精度提升汇总（以反向 SOC 误差为准） ===")
    b = results[0][2]
    for name, tbl, m, jm, jx in results:
        print(f"  {name:26s} max {m['rev_max_soc']:6.3f} %  "
              f"RMS {m['rev_rms_soc']:6.3f} %  "
              f"相对当前 {m['rev_max_soc']/b['rev_max_soc']*100:5.1f}%  "
              f"点数 {m['npts']:3d}")

    if args.emit:
        path = os.path.join(OUT_DIR, "candidate_tables.txt")
        with open(path, "w", encoding="utf-8") as f:
            for name, tbl, m, jm, jx in results:
                f.write(f"# {name} | {len(tbl)} 点 | maxSOCerr={m['rev_max_soc']:.4f}% "
                        f"| maxVerr={m['fwd_max_mv']:.3f}mV\n")
                f.write("OCV_TABLE = (\n")
                for x, y in tbl:
                    f.write(f"    ({x:g}, {y:.6f}),\n")
                f.write(")\n\n")
        print(f"\n候选表已导出: {path}")

        # 误差曲线 CSV，方便画图
        csv = os.path.join(OUT_DIR, "error_curve.csv")
        with open(csv, "w", encoding="utf-8") as f:
            f.write("soc_pct,cur_mv,raw62_mv,adaptive_mv\n")
            ad = dict((n, t) for n, t, *_ in results)["自适应加密表"]
            r62 = dict((n, t) for n, t, *_ in results)["原始 62 点表（直接平移）"]
            for s in base["fwd_x"]:
                f.write(f"{s:.2f},"
                        f"{S.ocv_from_soc(s, S.OCV_TABLE)*1000-interp(txs,tys,s)*1000:.4f},"
                        f"{S.ocv_from_soc(s, r62)*1000-interp(txs,tys,s)*1000:.4f},"
                        f"{S.ocv_from_soc(s, ad)*1000-interp(txs,tys,s)*1000:.4f}\n")
        print(f"误差曲线已导出: {csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
