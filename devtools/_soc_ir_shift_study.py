"""回答「把 82 mV 缩小 / 加密采样点能否提升 SOC 精度」的三组实验。

结论（OCV 表本体见 ``soc_profiles.BUILTIN_PACKS["E74"]``；标定对照文档已随电控交接资料一并下线）：

* 82 mV **不是采样间隔**，而是 1C(12 A) × R0(6.8 mΩ) = 81.6 mV 的 IR
  压降补偿量，即带载曲线与 OCV 表之间的垂直偏移。
* 缩小它会引入**系统性高估**（α=60 → +2.25 %SOC，α=0 → +7.80 %SOC），
  因为运行时 IR 补偿仍是 81.6 mV，表却整体下移了。
* 加密采样点**零收益**：折线段内本就是线性插值，只要保留原始 62 个
  拐点，加密到 680 点误差仍恒为 0；等距整数 SOC 网格反而要 >1000 点
  才逼近，因为 0.3654 % / 98.3395 % 的拐点不在整数 SOC 上。
* 真正的精度瓶颈是 AFE 噪声（±2.2 mV → 平台段 ±0.5 %SOC）与 82 mV
  常数 IR 模型忽略的极化/温度/老化，量级比表插值误差大两个数量级。

只读：不改任何产品源码，不写用户配置，产物仅标准输出。
用法：``.venv/Scripts/python.exe devtools/_soc_ir_shift_study.py``
数据源优先用 ``_check/soc_fit/12Ah_curve_uploaded.xlsx``（用户上传副本），
读不到时回退到 ``C:/Users/Lenovo/Downloads/12Ah放电曲线图(1).xlsx``。
"""

from __future__ import annotations

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
XLSX_CANDIDATES = (
    os.path.join(ROOT, "_check", "soc_fit", "12Ah_curve_uploaded.xlsx"),
    "C:/Users/Lenovo/Downloads/12Ah放电曲线图(1).xlsx",
)
IR_MV = 82.0
RUNTIME_IR_MV = 12.0 * 6.8  # 运行时 i_eff × r0_mohm = 81.6 mV


def _col_idx(ref: str) -> int:
    s = re.match(r"([A-Z]+)", ref).group(1)
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def load_curve():
    """返回 (soc_asc, loaded_v_asc, capacity_ah, raw_points)。"""
    path = next((p for p in XLSX_CANDIDATES if os.path.exists(p)), None)
    if path is None:
        raise FileNotFoundError("找不到放电曲线 xlsx，请先放到 _check/soc_fit/")
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
            shared.append("".join(t.text or "" for t in si.iter(NS + "t")))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    relmap = {r.get("Id"): r.get("Target") for r in rels}
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheetmap = {}
    for sh in wb.find(NS + "sheets"):
        rid = sh.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        tgt = relmap.get(rid, "")
        if tgt and not tgt.startswith("xl/"):
            tgt = "xl/" + tgt.lstrip("/")
        sheetmap[sh.get("name")] = tgt

    target = sheetmap.get("Detail_222_1_6")
    if not target:
        raise RuntimeError("找不到 Detail_222_1_6 工作表")
    rows = []
    for row in ET.fromstring(z.read(target)).find(NS + "sheetData"):
        d = {}
        for c in row:
            k = _col_idx(c.get("r"))
            v = c.find(NS + "v")
            if v is None:
                continue
            if c.get("t") == "s":
                d[k] = shared[int(v.text)]
            else:
                try:
                    d[k] = float(v.text)
                except ValueError:
                    d[k] = v.text
        if d:
            rows.append([d.get(i) for i in range(max(d) + 1)])
    z.close()

    hdr = rows[0]
    ci = {name: i for i, name in enumerate(hdr) if isinstance(name, str)}
    c_step, c_cap, c_volt = ci["步次"], ci["容量(Ah)"], ci["电压(V)"]
    pts = [r for r in rows[1:]
           if len(r) > max(c_step, c_cap, c_volt)
           and r[c_step] == 5.0
           and isinstance(r[c_cap], float)
           and isinstance(r[c_volt], float)]
    q_ah = pts[-1][c_cap]
    raw = [(r[c_cap], r[c_volt]) for r in pts]
    trip = sorted(((1.0 - cap / q_ah) * 100.0, volt) for cap, volt in raw)
    return [p[0] for p in trip], [p[1] for p in trip], q_ah, raw


def interp(xs, ys, x):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_left(xs, x)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def inverse(xs, ys, y):
    if y <= ys[0]:
        return xs[0]
    if y >= ys[-1]:
        return xs[-1]
    i = bisect.bisect_left(ys, y)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return x0 + (x1 - x0) * (y - y0) / (y1 - y0)


def soc_error_of_table(tbl, cs, cv):
    ts = [p[0] for p in tbl]
    tv = [p[1] for p in tbl]
    truth = [c + IR_MV / 1000.0 for c in cv]
    return max(abs(inverse(ts, tv, v) - inverse(cs, truth, v))
               for v in [truth[0] + i * 0.0014 for i in range(1011)]
               if v <= truth[-1])


def main() -> int:
    cs, cv, q_ah, raw = load_curve()
    truth = [c + IR_MV / 1000.0 for c in cv]
    gaps = [(cv[i + 1] - cv[i]) * 1000.0 for i in range(len(cv) - 1)]
    soc_gaps = [cs[i + 1] - cs[i] for i in range(len(cs) - 1)]
    plat = [g for s, g in zip(cs[:-1], gaps) if 10 <= s <= 90]

    print(f"数据源：工步 5，{len(raw)} 点，Q={q_ah:.6f} Ah")
    print("\n=== 真实采样密度（82 mV 不是采样间隔）===")
    print(f"  容量间隔        {raw[1][0] - raw[0][0]:.5f} Ah")
    print(f"  SOC 间隔        平均 {sum(soc_gaps)/len(soc_gaps):.3f} % "
          f"({min(soc_gaps):.3f} ~ {max(soc_gaps):.3f})")
    print(f"  相邻电压差      全量程 {min(gaps):.1f} ~ {max(gaps):.1f} mV；"
          f"平台段(10~90%) 平均 {sum(plat)/len(plat):.1f} mV")
    print(f"  平台段斜率      约 {sum(plat)/len(plat)/(sum(soc_gaps)/len(soc_gaps)):.2f} mV/%")

    print("\n=== 实验 A：缩小 82 mV 并重建表 ===")
    print("  α(mV)  静置SOC偏差(%)  1C带载SOC偏差(%)  表相对真值偏差(mV)")
    for alpha in (82, 75, 70, 60, 40, 20, 0):
        ts = list(cs)
        tv = [c + alpha / 1000.0 for c in cv]
        v_rest = interp(cs, cv, 50.0) + IR_MV / 1000.0
        v_load = interp(cs, cv, 50.0)
        s_rest = inverse(ts, tv, v_rest)
        s_load = inverse(ts, tv, v_load + RUNTIME_IR_MV / 1000.0)
        print(f"  {alpha:5.0f}   {s_rest-50:+13.2f}   {s_load-50:+15.2f}   "
              f"{abs(IR_MV-alpha):17.2f}")

    print("\n=== 实验 B：加密采样点（真值 = 原始 62 点折线）===")
    print("  方案                                  点数   反向 SOC 误差 max(%)")
    print(f"  原始 62 点（不规则 SOC，当前表）     {len(cs):5d}   "
          f"{soc_error_of_table(list(zip(cs,truth)), cs, cv):.6f}")
    for n in (26, 51, 101, 201, 501, 1001):
        xs = [100.0 * i / (n - 1) for i in range(n)]
        tbl = [(x, interp(cs, truth, x)) for x in xs]
        print(f"  等距 {n:4d} 点（整数 SOC 网格）       {n:5d}   "
              f"{soc_error_of_table(tbl, cs, cv):.6f}")
    for k in (2, 5, 10):
        xs = sorted(set([100.0 * i / (len(cs) * k - 1)
                         for i in range(len(cs) * k)] + cs))
        tbl = [(x, interp(cs, truth, x)) for x in xs]
        print(f"  保留原始拐点 + 加密到 {len(tbl):4d} 点       {len(tbl):5d}   "
              f"{soc_error_of_table(tbl, cs, cv):.6f}")

    print("\n=== 实验 C：AFE 噪声 → SOC 抖动（当前 62 点表）===")
    print(f"  斜率: @25={S.slope_at(25):.2f}  @50={S.slope_at(50):.2f}  "
          f"@75={S.slope_at(75):.2f} mV/%")
    print("  噪声(mV)     @SOC25      @SOC50      @SOC75")
    for mv in (0.5, 1.0, 2.2, 4.4):
        row = []
        for s in (25, 50, 75):
            v = S.ocv_from_soc(s)
            row.append(abs(S.soc_from_ocv(v + mv / 1000.0)
                           - S.soc_from_ocv(v - mv / 1000.0)) / 2.0)
        print(f"  ±{mv:4.1f}     {row[0]:8.3f}%   {row[1]:8.3f}%   {row[2]:8.3f}%")
    mean_err = 2.2 / math.sqrt(125)
    print(f"\n  125 节均值随机噪声: ±{mean_err:.3f} mV → "
          f"平台段 ±{mean_err/S.slope_at(50):.3f} %SOC")
    print("  ⛔ 系统性误差不会被平均：片间台阶 ≤4.4 mV、坏点/回填数变化、")
    print("     82 mV 常数 IR 忽略极化/温度/老化 —— 这些才是真正的精度瓶颈。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
