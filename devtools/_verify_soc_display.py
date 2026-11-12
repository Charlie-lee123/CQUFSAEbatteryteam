"""SOC 显示门控纯函数单测 —— 不开 QApplication、不读 QSettings、零窗口副作用。

跑法：``.venv/Scripts/python.exe devtools/_verify_soc_display.py``

覆盖：open 完全冻结 + 大幅偏离安全阀 / 106 轮 E74 日志回放（零波动）/
门控退化反证 / 会话库 435 轮长连续段 / 显示文本与数值「零改动」契约。
"""
from __future__ import annotations

import copy
import datetime as dt
import io
import os
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import soc_display as D
import soc_est as S
import soc_feed

LOG = ("C:/Users/Lenovo/AppData/Local/CQUFSAE-SerialAssistant/logs/"
       "接收历史-E74-2026.log")
DB = ("C:/Users/Lenovo/AppData/Local/CQUFSAE-SerialAssistant/logs/sessions.db")

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  OK  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def section(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def _changes(seq) -> int:
    return sum(1 for k in range(1, len(seq)) if seq[k] != seq[k - 1])


def _ptp(vals) -> float:
    vals = [v for v in vals if v is not None]
    return (max(vals) - min(vals)) if vals else 0.0


# --------------------------------------------------------------- 1 规则
def part_rules() -> None:
    section("1. 门控规则（open 完全冻结 + 安全阀，纯函数不开窗口）")
    # 首次建立基准
    g = D.SocDisplayGate()
    check("首次调用直接建立基准",
          g.decide("open", "", 75.0, 77.0) == (75.0, 77.0))

    # 首个残缺轮不建立基准，等第一个有效轮
    g = D.SocDisplayGate()
    r = g.decide("open", "WEAK", 80.0, 82.0)
    check("首轮残缺不建立基准（返回 None 基准）", r == (None, None), f"got={r}")
    r = g.decide("open", "", 75.0, 77.0)
    check("第二个有效轮才建立基准", r == (75.0, 77.0), f"got={r}")

    # mode 非 open 立即全量刷新
    g = D.SocDisplayGate()
    g.decide("open", "", 75.0, 77.0)
    check("mode=idle 立即全量刷新",
          g.decide("idle", "", 70.0, 72.0) == (70.0, 72.0))

    # 零迟钝：open 冻结中 -> discharge 下一轮立即跟随
    g = D.SocDisplayGate()
    g.decide("open", "", 75.0, 77.0)
    g.decide("open", "", 75.1, 77.1)          # 小噪声，沿用
    check("open -> discharge 下一轮立即跟随（真实带载零迟钝）",
          g.decide("discharge", "", 60.0, 62.0) == (60.0, 62.0))

    # 残缺轮
    for flag in ("WEAK", "NO_DATA", "RESIDUAL"):
        g = D.SocDisplayGate()
        g.decide("open", "", 75.0, 77.0)
        r = g.decide("open", flag, 80.0, 82.0)
        check(f"残缺轮 flag={flag} 不刷新", r == (75.0, 77.0), f"got={r}")
    g = D.SocDisplayGate()
    g.decide("open", "", 75.0, 77.0)
    check("soc_min/soc_max=None 不刷新",
          g.decide("open", "", None, None) == (75.0, 77.0))

    # 连续小噪声
    g = D.SocDisplayGate()
    g.decide("open", "", 75.0, 77.0)
    ok = True
    for d in (0.05, 0.1, 0.4, -0.05, -0.1, -0.4):
        ok = ok and g.decide("open", "", 75.0 + d, 77.0 + d) == (75.0, 77.0)
    check("连续小噪声 ±0.05 / ±0.1 / ±0.4% 全不刷新", ok)

    # open 期间持续 4 轮 +1.0% 台阶 → 完全冻结，不刷新
    g = D.SocDisplayGate()
    g.decide("open", "", 75.0, 77.0)
    ok = True
    for _ in range(4):
        ok = ok and g.decide("open", "", 76.0, 78.0) == (75.0, 77.0)
    check("open 期间持续 4 轮 +1.0% 台阶全程不刷新（完全冻结）", ok)

    # 大幅偏离安全阀：持续 3 轮 +6.0% → 第 1 轮不刷新、第 2 轮刷新、第 3 轮保持
    g = D.SocDisplayGate()
    g.decide("open", "", 75.0, 77.0)
    r1 = g.decide("open", "", 81.0, 83.0)
    check("持续 3 轮 +6.0% 第 1 轮不刷新（pending）",
          r1 == (75.0, 77.0), f"got={r1}")
    r2 = g.decide("open", "", 81.0, 83.0)
    check("持续 3 轮 +6.0% 第 2 轮刷新（安全阀生效）",
          r2 == (81.0, 83.0), f"got={r2}")
    r3 = g.decide("open", "", 81.0, 83.0)
    check("持续 3 轮 +6.0% 第 3 轮保持新基准（不再跳变）",
          r3 == (81.0, 83.0), f"got={r3}")

    # 安全阀：单轮 +6.0% 后回旧值清 pending，永不刷新
    g = D.SocDisplayGate()
    g.decide("open", "", 75.0, 77.0)
    r = g.decide("open", "", 81.0, 83.0)      # 单轮，第 1 轮 pending
    check("单轮 +6.0% 剧变不刷新（第 1 轮 pending）",
          r == (75.0, 77.0), f"got={r}")
    r = g.decide("open", "", 75.0, 77.0)      # 回旧值，清 pending
    check("安全阀尖峰后回到旧值不刷新", r == (75.0, 77.0), f"got={r}")
    r = g.decide("open", "", 81.0, 83.0)      # 又是第 1 轮 pending
    check("回旧值后单轮剧变仍不刷新（pending 已清）",
          r == (75.0, 77.0), f"got={r}")


# --------------------------------------------------------------- 回放
def _rounds_from_log():
    if not os.path.exists(LOG):
        return []
    text = io.open(LOG, encoding="utf-8", errors="replace").read()
    return soc_feed.parse_text(text)


def _rounds_from_db_longest():
    """会话库最长连续段（与 _probe_soc_idle_drift.py 同口径）。"""
    if not os.path.exists(DB):
        return [], []
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
    if not seq:
        return [], []
    segs, cur = [], [seq[0]]
    for k in range(1, len(seq)):
        if seq[k][0] - seq[k - 1][0] <= S.DT_MAX_S:
            cur.append(seq[k])
        else:
            segs.append(cur)
            cur = [seq[k]]
    segs.append(cur)
    segs.sort(key=len, reverse=True)
    best = segs[0]
    return [d for _, d in best], [t for t, _ in best]


def _replay(rounds, stamps, gate):
    """按 _feed_soc 真实调用方式回放，返回 (显示文本, 原始数值, 工况, 总压)。"""
    est = S.SocEstimator(S.SocCfg("E74"))
    texts, raws, modes, packs = [], [], [], []
    for r, ts in zip(rounds, stamps):
        res = est.update(r.cells, r.temps, r.i_meas, ts=ts,
                         bad_mask=r.bad,
                         contactor_open=r.contactor_open,
                         bms_ok=r.bms_ok)
        if res.clean is not None and res.clean.ok:
            g_min, g_max = gate.decide(res.mode, res.flag,
                                       res.soc_min, res.soc_max)
            if g_min is None or g_max is None:
                t_min, t_max = "--", "--"
            else:
                t_min, t_max = f"{g_min:.1f}", f"{g_max:.1f}"
        else:
            gate.decide(res.mode, res.flag, None, None)
            t_min, t_max = "--", "--"
        texts.append((t_min, t_max))
        raws.append((res.soc_min, res.soc_max))
        modes.append(res.mode)
        packs.append(res.pack_v)
    return texts, raws, modes, packs


def part_replay() -> None:
    section("2. 106 轮 E74 日志回放（显示冻结 vs 数值零改动）")
    rounds = _rounds_from_log()
    if not rounds:
        check("E74 日志文件存在", False)
        return
    stamps = [float(i) * 1.5 for i in range(len(rounds))]
    texts, raws, modes, packs = _replay(rounds, stamps, D.SocDisplayGate())
    ch_min = _changes([t[0] for t in texts])
    ch_max = _changes([t[1] for t in texts])
    print(f"  轮数={len(rounds)}  工况分布={ {m: modes.count(m) for m in set(modes)} }")
    print(f"  门控后显示文本变化次数: min={ch_min} / max={ch_max}"
          f"（{len(texts) - 1} 次相邻比较；现状 2 / 1，目标 0 / 0）")
    check("106 轮回放 SOC min 显示文本变化 = 0（零波动）",
          ch_min == 0, f"实际 {ch_min}")
    check("106 轮回放 SOC max 显示文本变化 = 0（零波动）",
          ch_max == 0, f"实际 {ch_max}")

    ptp_lo = _ptp([v[0] for v in raws])
    ptp_hi = _ptp([v[1] for v in raws])
    ptp_v = _ptp(packs)
    print(f"  原始 SocResult 数值极差: min={ptp_lo:.4f}%  max={ptp_hi:.4f}%  "
          f"总压={ptp_v:.4f} V")
    check("原始 soc_min 极差仍 1.5835%（数值零改动）",
          abs(ptp_lo - 1.5835) < 0.01, f"实际 {ptp_lo:.4f}")
    check("原始 soc_max 极差仍 0.7855%（数值零改动）",
          abs(ptp_hi - 0.7855) < 0.01, f"实际 {ptp_hi:.4f}")
    check("总压保持实时，极差仍 1.3108 V",
          abs(ptp_v - 1.3108) < 0.02, f"实际 {ptp_v:.4f}")
    g_lo = _ptp([float(t[0]) for t in texts if t[0] != "--"])
    g_hi = _ptp([float(t[1]) for t in texts if t[1] != "--"])
    print(f"  门控后显示数值极差: min={g_lo:.4f}%  max={g_hi:.4f}%")

    # 工况标记 / 总压 / "  ±" 告警后缀逐字不变（主窗用浅拷贝替身走原格式化）
    cfg = S.SocCfg("E74")
    est2 = S.SocEstimator(cfg)
    contract_ok = True
    for r, ts in zip(rounds, stamps):
        res = est2.update(r.cells, r.temps, r.i_meas, ts=ts,
                          bad_mask=r.bad,
                          contactor_open=r.contactor_open,
                          bms_ok=r.bms_ok)
        if res.clean is None or not res.clean.ok:
            continue
        shown = copy.copy(res)
        shown.soc_min = 1.0
        shown.soc_max = 2.0
        p0 = S.format_soc_parts(res, cfg)
        p1 = S.format_soc_parts(shown, cfg)
        if (p0[0] != p1[0] or p0[3] != p1[3]
                or p0[2].endswith("±") != p1[2].endswith("±")):
            contract_ok = False
            break
    check("工况 / 总压 / ±告警后缀逐字不变（浅拷贝替身）", contract_ok)
    with io.open(D.__file__, encoding="utf-8") as f:
        gate_src = f.read()
    check("soc_display 未 import soc_est（零算法耦合）",
          "soc_est" not in gate_src)


def part_counterproof() -> None:
    section("3. 反证：门控退化为透明，必须逐字复现现状")
    rounds = _rounds_from_log()
    if not rounds:
        check("E74 日志文件存在", False)
        return
    stamps = [float(i) * 1.5 for i in range(len(rounds))]
    saved = D.SOC_DISPLAY_FREEZE_OPEN
    D.SOC_DISPLAY_FREEZE_OPEN = False
    try:
        texts, raws, _, _ = _replay(rounds, stamps, D.SocDisplayGate())
    finally:
        D.SOC_DISPLAY_FREEZE_OPEN = saved
    base_txt = [(("--" if v[0] is None else f"{v[0]:.1f}"),
                 ("--" if v[1] is None else f"{v[1]:.1f}")) for v in raws]
    check("透明模式下门控文本逐字等于原始文本", texts == base_txt)
    ch_min = _changes([t[0] for t in texts])
    ch_max = _changes([t[1] for t in texts])
    print(f"  反证显示文本变化次数: min={ch_min} / max={ch_max}")
    check("反证复现 min 2 次跳变", ch_min == 2, f"实际 {ch_min}")
    check("反证复现 max 1 次跳变", ch_max == 1, f"实际 {ch_max}")
    ptp_lo = _ptp([v[0] for v in raws])
    ptp_hi = _ptp([v[1] for v in raws])
    print(f"  反证原始数值极差: min={ptp_lo:.4f}%  max={ptp_hi:.4f}%")
    check("反证复现 1.5835% 极差", abs(ptp_lo - 1.5835) < 0.01,
          f"实际 {ptp_lo:.4f}")
    check("反证复现 0.7855% 极差", abs(ptp_hi - 0.7855) < 0.01,
          f"实际 {ptp_hi:.4f}")


def part_db() -> None:
    section("4. 会话库最长连续段（435 轮 / 217.9 s）")
    rounds, stamps = _rounds_from_db_longest()
    if not rounds:
        check("会话库可用", False)
        return
    print(f"  最长连续段 {len(rounds)} 轮，跨 {stamps[-1] - stamps[0]:.1f}s")
    texts, raws, modes, packs = _replay(rounds, stamps, D.SocDisplayGate())
    ch_min = _changes([t[0] for t in texts])
    ch_max = _changes([t[1] for t in texts])
    print(f"  会话库显示文本变化次数: min={ch_min} / max={ch_max}")
    check("会话库连续段 SOC min 变化次数 = 0", ch_min == 0, f"实际 {ch_min}")
    check("会话库连续段 SOC max 变化次数 = 0", ch_max == 0, f"实际 {ch_max}")
    big = 0
    raw_big = 0
    for k in range(1, len(texts)):
        if texts[k][0] != "--" and texts[k - 1][0] != "--":
            if abs(float(texts[k][0]) - float(texts[k - 1][0])) >= 0.5:
                big += 1
        if texts[k][1] != "--" and texts[k - 1][1] != "--":
            if abs(float(texts[k][1]) - float(texts[k - 1][1])) >= 0.5:
                big += 1
        for j in (0, 1):
            a, b = raws[k - 1][j], raws[k][j]
            if a is not None and b is not None and abs(b - a) >= 0.5:
                raw_big += 1
    print(f"  门控后相邻跳变 >=0.5% 次数 = {big}；原始数值 = {raw_big}")
    print(f"  工况分布={ {m: modes.count(m) for m in set(modes)} }  "
          f"总压极差={_ptp(packs):.4f} V")
    check("会话库连续段门控后 >=0.5% 跳变次数 = 0", big == 0)


def main() -> int:
    part_rules()
    part_replay()
    part_counterproof()
    part_db()
    section("结论")
    if _FAIL:
        print(f"FAILED: {_FAIL} 项 / PASSED: {_PASS} 项")
        print("IS_PASS: NO")
        return 1
    print(f"PASSED: {_PASS} / {_PASS}")
    print("IS_PASS: YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
