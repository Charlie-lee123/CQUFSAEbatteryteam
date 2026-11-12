"""SOC 静置波动诊断探针（只读研究，不修改任何产品源码）。

跑法： .venv/Scripts/python.exe devtools/_probe_soc_idle_drift.py

目的：复现「没接负载时 SOC 有很小波动」，把波动逐项拆到具体来源，量化
      「电压噪声 -> SOC 抖动」传递系数，并给出冻结粒度的实测依据。

纪律：
  * 只 import soc_est / soc_feed，不 import Qt、不碰 QSettings、不启主窗口；
  * 所有对照实验用 monkey-patch 改**运行时**模块常量 / 方法，try/finally 还原；
  * 不改 soc_est.py / soc_feed.py / main_window.py 任何一个字符；
  * 逐轮明细落到 _check/soc_idle_drift/rounds_<tag>.csv 供复核。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import os
import re
import sqlite3
import statistics as st
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import soc_est as S
import soc_feed

LOG = ("C:/Users/Lenovo/AppData/Local/CQUFSAE-SerialAssistant/logs/"
       "接收历史-E74-2026.log")
DB = ("C:/Users/Lenovo/AppData/Local/CQUFSAE-SerialAssistant/logs/sessions.db")
OUT_DIR = os.path.join(ROOT, "_check", "soc_idle_drift")
os.makedirs(OUT_DIR, exist_ok=True)


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def stats(vals) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return dict(n=0, min=0.0, max=0.0, ptp=0.0, std=0.0,
                    max_step=0.0, mean=0.0)
    steps = [abs(vals[k] - vals[k - 1]) for k in range(1, len(vals))]
    return dict(n=len(vals), min=min(vals), max=max(vals),
                ptp=max(vals) - min(vals), std=st.pstdev(vals),
                max_step=max(steps) if steps else 0.0, mean=st.mean(vals))


def col(rows, key, require_ok=True):
    return [r[key] for r in rows
            if (not require_ok or r["ok"]) and r[key] is not None]


def show_stat(name, s, unit="%"):
    print(f"  {name:<22s} n={s['n']:3d}  "
          f"min={s['min']:9.4f}  max={s['max']:9.4f}  "
          f"极差={s['ptp']:9.4f}{unit}  "
          f"相邻最大跳变={s['max_step']:8.4f}{unit}  "
          f"std={s['std']:8.4f}{unit}")


def save_csv(rows, tag):
    path = os.path.join(OUT_DIR, f"rounds_{tag}.csv")
    if not rows:
        return
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  (逐轮明细 -> {path})")


# ---------------------------------------------------------------- 数据
def load_text() -> str:
    return io.open(LOG, encoding="utf-8", errors="replace").read()


def load_rounds(text):
    return soc_feed.parse_text(text)


def uniform_ts(n, step):
    return [i * step for i in range(n)]


# ---------------------------------------------------------------- 跑一遍
def _noop_anchor(self, clean, i_eff, i_valid, ts, temp_c):
    """(b) 关掉 _rest_anchor：no-op，返回 False，不动 self.soc。"""
    return False


def run(rounds, ts_list, *, cells_fn=None, k_volt=None, anchor_off=False):
    """逐轮喂 SocEstimator，记录每轮输出 + 清洗内部量。

    cells_fn(i, r) -> (cells, bad)；None = 用原始轮数据。
    k_volt / anchor_off 用运行时 monkey-patch，finally 原样还原。
    """
    saved_k = S.K_VOLT
    saved_anchor = S.SocEstimator._rest_anchor
    saved_ec = S.SocEstimator._effective_current
    iv_log: list = []
    try:
        if k_volt is not None:
            S.K_VOLT = k_volt
        if anchor_off:
            S.SocEstimator._rest_anchor = _noop_anchor

        def wrapped_ec(self, i_meas, contactor_open, bms_ok):
            ie, iv = saved_ec(self, i_meas, contactor_open, bms_ok)
            iv_log.append(iv)
            return ie, iv
        S.SocEstimator._effective_current = wrapped_ec

        est = S.SocEstimator(S.SocCfg("E74"))
        rows = []
        for i, (r, ts) in enumerate(zip(rounds, ts_list)):
            if cells_fn is None:
                cells, bad = r.cells, r.bad
            else:
                cells, bad = cells_fn(i, r)
            res = est.update(cells, r.temps, r.i_meas, ts=ts,
                             bad_mask=bad,
                             contactor_open=r.contactor_open,
                             bms_ok=r.bms_ok)
            c = res.clean
            # 朴素统计量：不做 MAD/连续段/极值剔除，只看量程内合法读数
            valid = []
            for k, v in enumerate(cells):
                if v is None:
                    continue
                if bad is not None and k < len(bad) and bad[k]:
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if S.CELL_MIN_MV <= fv <= S.CELL_MAX_MV:
                    valid.append(fv)
            rows.append({
                "i": i, "ts": round(float(ts), 3),
                "soc": res.soc, "soc_min": res.soc_min, "soc_max": res.soc_max,
                "pack_v": res.pack_v, "i_eff": res.i_eff,
                "i_valid": iv_log[-1] if iv_log else None,
                "mode": res.mode, "anchored": res.anchored,
                "flag": res.flag, "ok": c.ok,
                "mean_mv": c.mean_mv, "median_mv": c.median_mv,
                "gap_mv": c.gap_mv, "min_mv": c.min_mv, "max_mv": c.max_mv,
                "n_bad": len(c.bad_idx), "n_raw": c.n_raw,
                "n_unusable": c.n_unusable,
                "n_extreme_dropped": c.n_extreme_dropped,
                "residual": c.residual, "module_offset": c.module_offset,
                "naive_mean": (sum(valid) / len(valid)) if valid else 0.0,
                "raw_min": min(valid) if valid else 0.0,
                "raw_max": max(valid) if valid else 0.0,
                "bms_ok": r.bms_ok, "contactor_open": r.contactor_open,
                "i_meas": r.i_meas, "n_samples": r.n_samples,
            })
        return rows
    finally:
        S.K_VOLT = saved_k
        S.SocEstimator._rest_anchor = saved_anchor
        S.SocEstimator._effective_current = saved_ec


def drift_summary(rows, label):
    """打印中心 soc / soc_min / soc_max / pack_v 的波动统计。"""
    ok = [r for r in rows if r["ok"]]
    soc = stats([r["soc"] for r in ok])
    smin = stats([r["soc_min"] for r in ok])
    smax = stats([r["soc_max"] for r in ok])
    pv = stats([r["pack_v"] for r in ok])
    mean = stats([r["mean_mv"] for r in ok])
    anchored = sum(1 for r in ok if r["anchored"])
    print(f"  [{label}] 参与统计 {len(ok)}/{len(rows)} 轮，"
          f"锚定触发 {anchored} 轮，"
          f"工况分布={ {m: sum(1 for r in ok if r['mode'] == m) for m in sorted({r['mode'] for r in ok})} }")
    show_stat("中心 soc", soc)
    show_stat("soc_min(显示)", smin)
    show_stat("soc_max(显示)", smax)
    show_stat("pack_v", pv, "V")
    show_stat("clean.mean_mv", mean, "mV")
    return dict(soc=soc, soc_min=smin, soc_max=smax, pack_v=pv,
                mean=mean, anchored=anchored, n_ok=len(ok))


# ---------------------------------------------------------------- 0 环境
def part_env(text, rounds):
    section("0. 环境与数据勘察")
    print(f"  日志 {os.path.getsize(LOG)} B / {len(text)} 字符 / "
          f"{text.count(chr(10))} 行")
    print(f"  parse_text -> {len(rounds)} 轮")
    print(f"  Voltage1 出现 {text.count('Voltage1')} 次 / "
          f"CVMax 出现 {text.count('CVMax')} 次")
    print(f"  BMS Error 出现 {len(re.findall(r'BMS Error', text, re.I))} 次 / "
          f"X 标记 {len(re.findall(r'\d\s+X\b', text))} 次")
    stamps = re.findall(r"^\[[^\]]*\d[^\]]*\]", text, re.M)
    print(f"  时间戳行 {len(stamps)} 条，样例 {stamps[:2]} ... {stamps[-2:] if stamps else ''}")
    opens = [r.contactor_open for r in rounds]
    print(f"  contactor_open: True={sum(1 for o in opens if o is True)} "
          f"False={sum(1 for o in opens if o is False)} "
          f"None={sum(1 for o in opens if o is None)}")
    print(f"  bms_ok: True={sum(1 for r in rounds if r.bms_ok)} "
          f"False={sum(1 for r in rounds if not r.bms_ok)}")
    ims = [r.i_meas for r in rounds if r.i_meas is not None]
    print(f"  i_meas: n={len(ims)} 取值集合(前8)={sorted(set(ims))[:8]} "
          f"min={min(ims) if ims else None} max={max(ims) if ims else None}")
    print(f"  n_samples: min={min(r.n_samples for r in rounds)} "
          f"max={max(r.n_samples for r in rounds)} "
          f"中位={st.median([r.n_samples for r in rounds]):.0f}")
    print(f"  OCV_TABLE 点数={len(S.OCV_TABLE)}  端点="
          f"{S.OCV_TABLE[0][1]}/{S.OCV_TABLE[-1][1]} V  "
          f"slope_at(30)={S.slope_at(30):.3f} mV/%  "
          f"kappa_at(30)={S.kappa_at(30):.4f}  K_VOLT={S.K_VOLT}")


# ---------------------------------------------------------------- 1 基线
def part_baseline(rounds):
    section("1. 基线：按真实调用方式逐轮喂（ts 均匀 1~2 s）")
    out = {}
    for step in (1.0, 1.5, 2.0):
        rows = run(rounds, uniform_ts(len(rounds), step))
        out[step] = drift_summary(rows, f"step={step}s, 总时长={len(rounds)*step:.0f}s")
        if step == 1.5:
            save_csv(rows, "baseline_1p5s")
            # 锚定触发轮号
            first = next((r["i"] for r in rows if r["anchored"]), None)
            print(f"  step=1.5s 首个锚定轮号 = {first}")
    # 锚定触发前 / 后分段（step=1.5）
    rows = run(rounds, uniform_ts(len(rounds), 1.5))
    first = next((r["i"] for r in rows if r["anchored"]), len(rows))
    pre = [r for r in rows if r["ok"] and r["i"] < first]
    post = [r for r in rows if r["ok"] and r["i"] >= first]
    print(f"\n  --- step=1.5s 分段（分界轮 {first}）---")
    for tag, seg in (("锚定触发前", pre), ("锚定触发后", post)):
        if not seg:
            continue
        s = stats([r["soc"] for r in seg])
        smin = stats([r["soc_min"] for r in seg])
        print(f"  {tag} n={len(seg)}: 中心soc 极差={s['ptp']:.4f}% "
              f"相邻最大={s['max_step']:.4f}% std={s['std']:.4f}% | "
              f"soc_min 极差={smin['ptp']:.4f}% 相邻最大={smin['max_step']:.4f}%")
    return out


# ---------------------------------------------------------------- 2 归因
def part_attribution(rounds):
    section("2. 逐项归因（每次只关掉/改一个来源，比 SOC 极差）")
    # 主口径 step=1.0s（全程静置锚定不触发，隔离 κ 路径）；
    # 副口径 step=1.5s（后段锚定触发，看锚定放大作用）。
    for step in (1.0, 1.5):
        ts = uniform_ts(len(rounds), step)
        print(f"\n  ######## 时间轴 step={step}s（总 {len(rounds)*step:.0f}s）########")
        results = {}

        results["基线"] = drift_summary(run(rounds, ts), "基线")

        # (a) 关 κ 电压回拉
        results["(a) K_VOLT=0"] = drift_summary(
            run(rounds, ts, k_volt=0.0), "(a) 关κ电压回拉")

        # (b) 关 _rest_anchor
        results["(b) 关锚定"] = drift_summary(
            run(rounds, ts, anchor_off=True), "(b) 关_rest_anchor")

        # (c) 每轮电芯固定为同一常数（全局代表均值）
        base_mean = st.median([r["mean_mv"] for r in results["基线"] and
                               run(rounds, ts) if r["ok"]])
        const_cells = ([float(base_mean)] * 125, None)
        results["(c) 全局常数"] = drift_summary(
            run(rounds, ts, cells_fn=lambda i, r, cc=const_cells: cc),
            f"(c) 全轮固定 {base_mean:.2f}mV")

        # (c2) 每轮内部所有电芯 = 该轮 clean.mean_mv（保留跨轮均值变化，
        #      消除单体离散、坏点回填、端点剔除对 min/max 的影响）
        base_rows = run(rounds, ts)
        mean_seq = [r["mean_mv"] for r in base_rows]
        def c2(i, r, seq=mean_seq):
            return ([float(seq[i])] * 125, None)
        results["(c2) 每轮=该轮均值"] = drift_summary(
            run(rounds, ts, cells_fn=c2), "(c2) 每轮125节=该轮mean_mv")

        # 归因对比表
        print(f"\n  --- 归因对比表 (step={step}s) ---")
        hdr = f"  {'实验':<22s} {'soc极差%':>10s} {'soc相邻max%':>12s} " \
              f"{'soc_min极差%':>13s} {'soc_max极差%':>13s} {'pack极差V':>11s} {'mean极差mV':>12s}"
        print(hdr)
        for k, d in results.items():
            print(f"  {k:<22s} {d['soc']['ptp']:>10.4f} "
                  f"{d['soc']['max_step']:>12.4f} "
                  f"{d['soc_min']['ptp']:>13.4f} "
                  f"{d['soc_max']['ptp']:>13.4f} "
                  f"{d['pack_v']['ptp']:>11.4f} "
                  f"{d['mean']['ptp']:>12.4f}")
        # (a)(b) 相对基线的削减量
        b = results["基线"]["soc"]["ptp"]
        for k in ("(a) K_VOLT=0", "(b) 关锚定", "(c) 全局常数", "(c2) 每轮=该轮均值"):
            d = results[k]["soc"]["ptp"]
            print(f"    {k:<20s} soc极差 {b:.4f} -> {d:.4f} "
                  f"(削减 {(b-d)/b*100 if b else 0:.1f}%)")
    return base_rows, mean_seq


# ---------------------------------------------------------------- 3 传递系数
def part_transfer(rounds, mean_seq):
    section("3. 「电压噪声 -> SOC 抖动」传递系数")
    base = st.median(mean_seq)
    soc0 = S.soc_from_ocv(base / 1000.0)
    slope = S.slope_at(soc0)
    print(f"  工作点：mean_mv 中位={base:.2f} mV -> OCV={base/1000:.4f} V "
          f"-> SOC={soc0:.3f}%")
    print(f"  该处 slope_at={slope:.3f} mV/%  => 理论 dSOC/dV = "
          f"1/slope = {1.0/slope:.5f} %/mV")
    print(f"  对照：slope_at(30)={S.slope_at(30):.3f} mV/% -> "
          f"{1.0/S.slope_at(30):.5f} %/mV（30% 平台段）")
    # 直接反查差分
    for d in (1, 2, 5):
        lo = S.soc_from_ocv((base - d) / 1000.0)
        hi = S.soc_from_ocv((base + d) / 1000.0)
        print(f"  查表 ±{d} mV: SOC {lo:.4f}% ~ {hi:.4f}%  "
              f"跨度={hi-lo:.4f}%  系数={(hi-lo)/(2*d):.5f} %/mV")

    print("\n  --- 合成注入（125 节全等于 base±d 交替，ts=1s 避开锚定）---")
    n = 120
    fake = [object() for _ in range(n)]
    # 造一个只有 cells/temps/i_meas/contactor_open/bms_ok/n_samples 的鸭子轮
    class R:
        temps = [45] * 40
        i_meas = -250.0
        contactor_open = True
        bms_ok = True
        n_samples = 125
    for d in (1, 2, 5):
        def inj(i, r, dd=d):
            v = float(base) + (dd if i % 2 == 0 else -dd)
            return ([v] * 125, None)
        rows = run([R() for _ in fake], uniform_ts(n, 1.0), cells_fn=inj)
        steady = [r for r in rows if r["ok"] and r["i"] >= 30]
        smin = stats([r["soc_min"] for r in steady])
        soc = stats([r["soc"] for r in steady])
        print(f"  注入 ±{d} mV: soc_min 稳态峰峰={smin['ptp']:.4f}% "
              f"(系数 {smin['ptp']/(2*d):.5f} %/mV) | "
              f"中心soc 稳态峰峰={soc['ptp']:.4f}% "
              f"(系数 {soc['ptp']/(2*d):.5f} %/mV) "
              f"抑制比={smin['ptp']/soc['ptp'] if soc['ptp'] else float('inf'):.1f}x")


# ---------------------------------------------------------------- 4 清洗内部
def part_clean(rounds):
    section("4. (e)(f) clean_cells / _robust_extremes 内部量逐轮跳变")
    rows = run(rounds, uniform_ts(len(rounds), 1.0))
    ok = [r for r in rows if r["ok"]]
    print(f"  ok 轮数 {len(ok)}")
    keys = ("mean_mv", "median_mv", "gap_mv", "naive_mean", "min_mv",
            "max_mv", "raw_min", "raw_max", "n_bad", "n_raw",
            "n_unusable", "n_extreme_dropped", "pack_v", "soc_min", "soc_max")
    for k in keys:
        s = stats([r[k] for r in ok])
        print(f"  {k:<20s} min={s['min']:10.3f} max={s['max']:10.3f} "
              f"极差={s['ptp']:9.3f} 相邻最大跳变={s['max_step']:9.3f} "
              f"std={s['std']:8.3f}")
    # mean_mv 与 naive_mean 的差（清洗引入的偏移）
    diff = [r["mean_mv"] - r["naive_mean"] for r in ok]
    print(f"\n  [e] mean_mv - naive_mean（清洗判定引入的偏移）: "
          f"极差={stats(diff)['ptp']:.4f} mV "
          f"相邻最大跳变={stats(diff)['max_step']:.4f} mV "
          f"std={stats(diff)['std']:.4f} mV")
    print(f"  [e] n_bad（回填节数）: 取值集合={sorted(set(r['n_bad'] for r in ok))} "
          f"n_raw 集合大小={len(set(r['n_raw'] for r in ok))} "
          f"n_raw min/max={min(r['n_raw'] for r in ok)}/{max(r['n_raw'] for r in ok)}")
    print(f"  [e] n_unusable（缺失/超量程）集合="
          f"{sorted(set(r['n_unusable'] for r in ok))[:12]}")
    # 相关系数：mean_mv 相邻跳变 vs n_bad 相邻跳变
    dmean = [ok[k]["mean_mv"] - ok[k-1]["mean_mv"] for k in range(1, len(ok))]
    dbad = [ok[k]["n_bad"] - ok[k-1]["n_bad"] for k in range(1, len(ok))]
    def corr(a, b):
        if len(a) < 2:
            return 0.0
        ma, mb = st.mean(a), st.mean(b)
        num = sum((x-ma)*(y-mb) for x, y in zip(a, b))
        da = math.sqrt(sum((x-ma)**2 for x in a))
        dbb = math.sqrt(sum((y-mb)**2 for y in b))
        return num/(da*dbb) if da and dbb else 0.0
    print(f"  [e] corr(Δmean_mv, Δn_bad) = {corr(dmean, dbad):+.3f}")
    print(f"  [e] corr(Δmean_mv, Δn_raw) = "
          f"{corr(dmean, [ok[k]['n_raw']-ok[k-1]['n_raw'] for k in range(1,len(ok))]):+.3f}")

    # [f] 端点剔除
    drops = [r["n_extreme_dropped"] for r in ok]
    print(f"\n  [f] n_extreme_dropped 取值集合={sorted(set(drops))} "
          f"剔除发生的轮数={sum(1 for d in drops if d)}")
    dmin = [r["min_mv"] - r["raw_min"] for r in ok]
    dmax = [r["raw_max"] - r["max_mv"] for r in ok]
    print(f"  [f] min_mv - raw_min（低端被抬高）: 极差={stats(dmin)['ptp']:.3f} mV "
          f"相邻最大跳变={stats(dmin)['max_step']:.3f} mV "
          f"最大={max(dmin):.3f} mV")
    print(f"  [f] raw_max - max_mv（高端被压低）: 极差={stats(dmax)['ptp']:.3f} mV "
          f"相邻最大跳变={stats(dmax)['max_step']:.3f} mV "
          f"最大={max(dmax):.3f} mV")
    print(f"  [f] clean.min_mv 相邻最大跳变="
          f"{stats([r['min_mv'] for r in ok])['max_step']:.3f} mV, "
          f"clean.max_mv 相邻最大跳变="
          f"{stats([r['max_mv'] for r in ok])['max_step']:.3f} mV")
    print(f"  [f] soc_min 相邻最大跳变="
          f"{stats([r['soc_min'] for r in ok])['max_step']:.4f}%, "
          f"soc_max 相邻最大跳变="
          f"{stats([r['soc_max'] for r in ok])['max_step']:.4f}%")
    # 逐轮最大跳变发生在哪一轮
    for key in ("mean_mv", "min_mv", "max_mv", "soc_min", "soc_max"):
        vals = [r[key] for r in ok]
        k = max(range(1, len(vals)), key=lambda j: abs(vals[j]-vals[j-1]))
        print(f"       {key:<10s} 最大相邻跳变在第 {ok[k]['i']} 轮: "
              f"{vals[k-1]:.3f} -> {vals[k]:.3f} "
              f"(Δ={vals[k]-vals[k-1]:+.3f}), 该轮 n_bad={ok[k]['n_bad']} "
              f"drop={ok[k]['n_extreme_dropped']} raw_min={ok[k]['raw_min']:.0f} "
              f"raw_max={ok[k]['raw_max']:.0f}")
    save_csv(rows, "clean_detail_1s")


# ---------------------------------------------------------------- 5 会话库
def part_db():
    section("5. 会话库真实时间轴（长时间静置，锚定会真正生效）")
    if not os.path.exists(DB):
        print("  sessions.db 不存在，跳过")
        return
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "select ts, raw from frames where vehicle='E74-2026' order by ts, id"
        ).fetchall()
    finally:
        conn.close()
    groups: dict = {}
    for ts, raw in rows:
        if not raw:
            continue
        groups.setdefault(ts, [])
        if raw not in groups[ts]:
            groups[ts].append(raw)
    collector = soc_feed.RoundCollector()
    seq = []
    for ts in sorted(groups):
        try:
            stamp = dt.datetime.fromisoformat(ts).timestamp()
        except ValueError:
            continue
        for data in collector.feed("\n".join(groups[ts])):
            seq.append((stamp, data))
    tail = collector.flush()
    if tail is not None and seq:
        seq.append((seq[-1][0], tail))
    print(f"  frames 行(去重后块)={len(groups)}  还原整轮={len(seq)}")
    if not seq:
        return
    # 只取最长的一段连续采样（相邻 ts 差 <= DT_MAX_S=10s）
    segs, cur = [], [seq[0]]
    for k in range(1, len(seq)):
        if seq[k][0] - seq[k-1][0] <= S.DT_MAX_S:
            cur.append(seq[k])
        else:
            segs.append(cur)
            cur = [seq[k]]
    segs.append(cur)
    segs.sort(key=len, reverse=True)
    print(f"  连续段数={len(segs)}  最长段={len(segs[0])} 轮，"
          f"跨 {segs[0][-1][0]-segs[0][0][0]:.1f}s")
    best = segs[0]
    rounds = [d for _, d in best]
    stamps = [t for t, _ in best]
    rows = run(rounds, stamps)
    d = drift_summary(rows, "最长连续段(真实ts)")
    # 逐轮相邻 SOC 跳变分布（这才是用户看到的"抖动"）
    okr = [r for r in rows if r["ok"]]
    jumps = [abs(okr[k]["soc_min"] - okr[k-1]["soc_min"])
             for k in range(1, len(okr))]
    jsoc = [abs(okr[k]["soc"] - okr[k-1]["soc"])
            for k in range(1, len(okr))]
    print(f"  soc_min 相邻跳变: 中位={st.median(jumps):.4f}% "
          f"P90={sorted(jumps)[int(len(jumps)*0.9)]:.4f}% "
          f"最大={max(jumps):.4f}%")
    print(f"  中心soc 相邻跳变: 中位={st.median(jsoc):.4f}% "
          f"P90={sorted(jsoc)[int(len(jsoc)*0.9)]:.4f}% "
          f"最大={max(jsoc):.4f}%")
    save_csv(rows, "db_longest_segment")

    # ---- 6. what-if：强制 P1 语义生效，看静置锚定放大多少 ----------
    section("6. what-if：若 P1 语义生效（i_valid=True），静置锚定放大多少波动")
    saved_ec = S.SocEstimator._effective_current

    def force_valid(self, i_meas, contactor_open, bms_ok):
        return 0.0, True
    try:
        S.SocEstimator._effective_current = force_valid
        r1 = run(rounds, stamps)
        d1 = drift_summary(r1, "强制 i_valid=True（P1 生效）")
        r2 = run(rounds, stamps, anchor_off=True)
        d2 = drift_summary(r2, "强制 i_valid=True + 关锚定")
    finally:
        S.SocEstimator._effective_current = saved_ec
    r0 = run(rounds, stamps)
    d0 = drift_summary(r0, "现状 i_valid=False（锚定 0 轮）")
    print(f"\n  中心 soc 极差：现状 {d0['soc']['ptp']:.4f}%  ->  "
          f"P1生效 {d1['soc']['ptp']:.4f}%  ->  "
          f"P1生效且关锚定 {d2['soc']['ptp']:.4f}%")
    print(f"  中心 soc 相邻最大跳变：现状 {d0['soc']['max_step']:.4f}%  ->  "
          f"P1生效 {d1['soc']['max_step']:.4f}%")
    print(f"  锚定单独贡献极差 = {d1['soc']['ptp']-d2['soc']['ptp']:.4f}% "
          f"（触发 {d1['anchored']} 轮）")


def part_detail_102(rounds):
    section("补充：最大跳变邻域逐轮明细 + 坏点数量分组")
    rows = run(rounds, uniform_ts(len(rounds), 1.0))
    keys = ("i", "mean_mv", "naive_mean", "min_mv", "raw_min", "max_mv",
            "raw_max", "n_bad", "n_raw", "n_unusable", "n_samples",
            "soc_min", "soc_max", "flag")
    print("  " + " ".join(f"{k:>11s}" for k in keys))
    for r in rows:
        if 99 <= r["i"] <= 104:
            cells = []
            for k in keys:
                v = r[k]
                cells.append(f"{v:>11.2f}" if isinstance(v, float)
                             else f"{str(v):>11s}")
            print("  " + " ".join(cells))
    groups = {}
    for r in rows:
        groups.setdefault(r["n_bad"], []).append(r["mean_mv"])
    for k in sorted(groups):
        print(f"  n_bad={k:2d}: n={len(groups[k]):3d}  mean_mv 均值="
              f"{st.mean(groups[k]):.3f} mV  区间="
              f"{min(groups[k]):.2f}~{max(groups[k]):.2f} mV")


def main():
    text = load_text()
    rounds = load_rounds(text)
    part_env(text, rounds)
    part_baseline(rounds)
    _, mean_seq = part_attribution(rounds)
    part_transfer(rounds, mean_seq)
    part_clean(rounds)
    part_detail_102(rounds)
    part_db()
    section("探针结束（未修改任何产品源码）")


if __name__ == "__main__":
    main()
