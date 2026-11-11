"""E74 历史数据回测：用真机日志 + 会话库跑一遍 SOC 链路，报可验证的指标。

**先把话说清楚**：E74 没有任何一次"带着真实 SOC 标签"的放电记录（没有实验室
库仑计、没有分容报告），所以下面**没有一项是"绝对精度"**。能证明的只有四类：

* **自洽性**：把 Excel 里那条 1C 放电曲线当成已知 SOC 重放，估算器能不能跟住。
  注意 OCV 表本来就是从这条曲线来的，所以这是**回归**（证明积分与查表接对了），
  不是独立验证。
* **一致性**：同一台车、同一个静止电压平台上的多轮数据，SOC 读数应当稳定；
  不同平台之间应当单调。
* **量程与越界**：SOC 永远不会跑出 0~100，也不会因为脏数据跳变。
* **告警对不对**：干净数据不该触发 RESIDUAL；植入坏点必须触发。

跑法：``.venv/Scripts/python.exe devtools/_soc_backtest.py``
"""

from __future__ import annotations

import datetime as dt
import io
import os
import sqlite3
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import soc_est as S
import soc_feed

LOG = ("C:/Users/Lenovo/AppData/Local/CQUFSAE-SerialAssistant/logs/"
       "接收历史-E74-2026.log")
DB = ("C:/Users/Lenovo/AppData/Local/CQUFSAE-SerialAssistant/logs/sessions.db")

_ISSUES: list[str] = []


def issue(text: str) -> None:
    _ISSUES.append(text)
    print(f"  ✗ {text}")


def _rounds_from_log() -> list[soc_feed.RoundData]:
    if not os.path.exists(LOG):
        return []
    text = io.open(LOG, encoding="utf-8", errors="replace").read()
    return soc_feed.parse_text(text)


def _rounds_from_db() -> list[tuple[float, soc_feed.RoundData]]:
    """会话库里一个 ts 是一块报文；按时间顺序喂同一个收集器还原整轮。"""
    if not os.path.exists(DB):
        return []
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "select ts, raw from frames where vehicle='E74-2026' order by ts, id"
        ).fetchall()
    finally:
        conn.close()
    groups: dict[str, list[str]] = {}
    for ts, raw in rows:
        if not raw:
            continue
        groups.setdefault(ts, [])
        if raw not in groups[ts]:
            groups[ts].append(raw)
    collector = soc_feed.RoundCollector()
    out: list[tuple[float, soc_feed.RoundData]] = []
    for ts in sorted(groups):
        stamp = dt.datetime.fromisoformat(ts).timestamp()
        for data in collector.feed("\n".join(groups[ts])):
            out.append((stamp, data))
    tail = collector.flush()
    if tail is not None:
        out.append((out[-1][0] if out else 0.0, tail))
    return out


def section(title: str) -> None:
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


# ---------------------------------------------------------------- 1 闭环

def part_closed_loop() -> None:
    section("1. 闭环回归（Excel 1C 放电曲线 → 已知 SOC 重放）")
    cfg = S.SocCfg("E74")
    est = S.SocEstimator(cfg)
    est.reset(soc=100.0)
    t = 0.0
    worst = 0.0
    samples = []
    for k in range(51):
        true_soc = 100.0 - 2.0 * k
        volts = (S.ocv_from_soc(true_soc) - 12.0 * 0.0068) * 1000.0
        steps = int(round(0.02 * 12.041 / 12.0 * 3600 / 2.0))
        for _ in range(steps):
            res = est.update([volts] * 125, [45] * 40, i_meas=12.0, ts=t,
                             contactor_open=False)
            t += 2.0
        worst = max(worst, abs(res.soc - true_soc))
        samples.append((true_soc, res.soc))
    print("  51 个 2% 台阶，2 s 采样，1C 恒流")
    print(f"  末端：估计 {est.soc:.2f}% / 真值 0.00%")
    print(f"  最大偏差 {worst:.2f}%（注意：OCV 表即出自这条曲线，属回归而非独立验证）")
    if worst > 2.5:
        issue(f"闭环偏差 {worst:.2f}% 超 2.5%")


# ---------------------------------------------------------------- 2 静置

def part_rest() -> None:
    section("2. 静置回弹的低端偏差（独立于建表数据的实测）")
    # Excel 工步 6：满放后搁置，3.0811 V 一路回弹到 3.4303 V
    import math
    est = S.SocEstimator(S.SocCfg("E74"))
    est.reset(soc=0.0)
    anchored = False
    for i in range(600):
        v = 3.0811 + (3.4303 - 3.0811) * (1 - math.exp(-i * 2.0 / 60.0))
        res = est.update([v * 1000.0] * 125, [45] * 40, i_meas=0.0, ts=i * 2.0,
                         contactor_open=False)
        anchored = anchored or res.anchored
    print("  满放后搁置 20 min：3.0811 V → 3.4303 V")
    print(f"  估算 SOC 收敛到 {est.soc:.2f}%（真值 0%），锚定触发={anchored}")
    print(f"  ⇒ 低 SOC 端已知偏乐观约 {est.soc:.1f}%（回弹 348 mV ÷ 185 mV/%）")
    if est.soc > 4.0:
        issue(f"低端静置偏差 {est.soc:.1f}% 超过 4%")


# ---------------------------------------------------------------- 3 真机

def part_real() -> None:
    section("3. 真机数据（接收日志 + 会话库）")
    log_rounds = _rounds_from_log()
    db_rounds = _rounds_from_db()
    print(f"  接收日志：{len(log_rounds)} 轮（含不完整轮）")
    print(f"  会话库：  {len(db_rounds)} 轮，跨 "
          f"{len({dt.date.fromtimestamp(t) for t, _ in db_rounds})} 天" if db_rounds
          else "  会话库：  0 轮")

    def run(seq, label: str):
        est = S.SocEstimator(S.SocCfg("E74"))
        socs: list[float] = []
        flags: dict[str, int] = {}
        gaps: list[float] = []
        bads: list[int] = []
        out_of_range = 0
        skipped = 0
        for stamp, data in seq:
            res = est.update(data.cells, data.temps, data.i_meas, ts=stamp,
                             bad_mask=data.bad,
                             contactor_open=data.contactor_open,
                             bms_ok=data.bms_ok)
            flags[res.flag] = flags.get(res.flag, 0) + 1
            gaps.append(res.clean.gap_mv)
            bads.append(len(res.clean.bad_idx))
            # ⚠️ 只统计"这一轮真的算出 SOC"的样本：半轮/无可用电芯时估算器
            #    按设计保持上一值（初值 50%），那是占位符不是漂移，混进统计会
            #    把"静置漂移"这个指标直接污染掉。
            if res.clean.ok:
                socs.append(res.soc)
                if not 0.0 <= res.soc <= 100.0:
                    out_of_range += 1
            else:
                skipped += 1
        print(f"\n  --- {label} ---")
        if not socs:
            issue(f"{label}: 没有一轮算出 SOC")
            return []
        print(f"  参与统计 {len(socs)} 轮（另有 {skipped} 轮无可用电芯，按设计跳过）")
        print(f"  SOC 区间 {min(socs):.1f}% ~ {max(socs):.1f}%"
              f"  中位 {st.median(socs):.1f}%")
        print(f"  每轮回填节数：中位 {st.median(bads):.0f} / 最大 {max(bads)}")
        print(f"  |均值-中位数|：中位 {st.median(gaps):.2f} mV / "
              f"最大 {max(gaps):.2f} mV")
        print(f"  标记分布：{ {k or '正常': v for k, v in flags.items()} }")
        print(f"  零漂收敛：{est.i_bias:.2f} A")
        print(f"  SOC 越界次数：{out_of_range}")
        if out_of_range:
            issue(f"{label}: SOC 越界 {out_of_range} 次")
        resid = flags.get("RESIDUAL", 0)
        if resid:
            issue(f"{label}: 干净数据触发 RESIDUAL {resid} 次（阈值可能偏紧）")
        return socs

    if log_rounds:
        seq = [(float(i), r) for i, r in enumerate(log_rounds)]
        socs = run(seq, "接收日志")
        # 同一平台上 SOC 应当稳定
        plateau = [s for s in socs]
        print(f"  全程 SOC 变化 {max(plateau) - min(plateau):.2f}%"
              "（接触器全断，真实电流为 0，应当几乎不动）")
        if max(plateau) - min(plateau) > 3.0:
            issue("接收日志：静置状态下 SOC 漂移超过 3%")

    if db_rounds:
        socs = run(db_rounds, "会话库（跨 9 天）")
        # 平台稳定性：细桶（10 mV）。**桶必须比曲线自身的斜率尺度细** ——
        # 用 100 mV 分桶时，3950~4050 那一段斜率约 10 mV/%，100 mV 本来就对应
        # 9.5% 的 SOC 差，量出来的"跨度大"是切桶太粗，不是算法错。
        levels: dict[int, list[float]] = {}
        est = S.SocEstimator(S.SocCfg("E74"))
        off_ocv = 0.0
        n_rest = 0
        prev = None
        monotone_breaks = 0
        for stamp, data in db_rounds:
            res = est.update(data.cells, data.temps, data.i_meas, ts=stamp,
                             bad_mask=data.bad,
                             contactor_open=data.contactor_open,
                             bms_ok=data.bms_ok)
            if not res.clean.ok:
                continue
            key = int(round(res.clean.mean_mv / 10.0))
            levels.setdefault(key, []).append(res.soc)
            # 静置轮：SOC 应当**就是**该电压的 OCV 查表值
            if abs(res.i_eff) < 1.2:
                expect = S.soc_from_ocv(res.clean.mean_mv / 1000.0)
                off_ocv = max(off_ocv, abs(res.soc - expect))
                n_rest += 1
            # SOC 反降只在**连续采样内**才算缺陷：跨会话重锚定跟着电池真实状态
            # 走，电池被放过电当然会降，那是正确行为不是抖动。
            if prev is not None and stamp - prev[0] < 60.0 \
                    and res.soc < prev[1] - 0.5:
                monotone_breaks += 1
            prev = (stamp, res.soc)

        print("\n  各电压平台的 SOC 读数（10 mV 细桶，桶内应稳定）：")
        prev = None
        for key in sorted(levels):
            vals = levels[key]
            spread = max(vals) - min(vals)
            if len(vals) >= 3:
                print(f"    {key * 10:5d} mV  n={len(vals):3d}  "
                      f"SOC {st.median(vals):5.1f}%  跨度 {spread:4.2f}%")
            if spread > 2.0:
                issue(f"平台 {key * 10} mV 内 SOC 跨度 {spread:.2f}% 过大")
            med = st.median(vals)
            if prev is not None and med < prev - 0.5:
                issue(f"平台 {key * 10} mV 的 SOC 低于更低电压的平台（非单调）")
            prev = med

        print(f"\n  静置轮一致性：{n_rest} 轮里，|SOC - OCV 查表值| 最大 {off_ocv:.2f}%")
        if off_ocv > 1.5:
            issue(f"静置轮 SOC 与 OCV 查表不一致，最大偏差 {off_ocv:.2f}%")
        if monotone_breaks:
            issue(f"时间序列上出现了 {monotone_breaks} 次 SOC 反降（≥0.5%）")


# ---------------------------------------------------------------- 4 告警

def part_guard() -> None:
    section("4. 告警对不对（植入坏点必须报，干净数据不许报）")
    cfg = S.SocCfg("E74")

    est = S.SocEstimator(cfg)
    est.reset(soc=50.0)
    res = est.update([3200.0] * 125, [40] * 40, i_meas=0.0, ts=0.0,
                     contactor_open=True)
    ok_clean = res.flag == "" and not res.clean.residual
    print(f"  干净 125 节 → flag={res.flag!r} residual={res.clean.residual}")
    if not ok_clean:
        issue("干净数据被误报")

    # 两节断线（用户点名的场景）：值读成 0 / 读成 5 V
    cells = [3200.0] * 125
    cells[11] = 0.0
    cells[22] = 5000.0
    res = est.update(cells, [40] * 40, i_meas=0.0, ts=2.0, contactor_open=True)
    caught = res.clean.bad_idx == [12, 23]
    print(f"  植入 2 节坏点 → 回填 {res.clean.bad_idx}，总压 "
          f"{res.pack_v:.1f} V（应≈400.0）")
    if not caught:
        issue(f"两节坏点没有被准确定位：{res.clean.bad_idx}")
    if abs(res.pack_v - 400.0) > 0.5:
        issue(f"坏点回填后总压偏离 {res.pack_v - 400.0:+.2f} V")

    # 整模组偏移：不是坏点，不该被回填，但值得提示
    cells = [3300.0] * 40 + [3200.0] * 85
    res = est.update(cells, [40] * 40, i_meas=0.0, ts=4.0, contactor_open=True)
    print(f"  整模组高 100 mV → 回填 {len(res.clean.bad_idx)} 节、"
          f"residual={res.clean.residual}（gap={res.clean.gap_mv:.1f} mV）")
    if len(res.clean.bad_idx) != 0:
        issue("整模组偏移被误判成坏点")
    if not res.clean.residual:
        issue("整模组偏移没有触发告知标记")


def main() -> int:
    print("E74 SOC 回测")
    print("⚠️ E74 没有带 SOC 标签的历史数据，下面**没有一项是绝对精度**，")
    print("   只有回归自洽性、平台一致性、量程与告警正确性。")
    part_closed_loop()
    part_rest()
    part_real()
    part_guard()

    section("结论")
    if _ISSUES:
        print(f"发现 {len(_ISSUES)} 个问题：")
        for text in _ISSUES:
            print(f"  - {text}")
        print("IS_PASS: NO")
        return 1
    print("全部检查通过。")
    print("IS_PASS: YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
