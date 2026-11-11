"""独立验收（证伪向）：SOC 纯逻辑链路 —— 查表 / 清洗 / 解析器 / 边界 / 电流 / 粘滞。

不 import 任何 Qt Widgets，不读写用户配置。只跑 soc_est / soc_feed。
产物：仅标准输出。退出码 0（即使有 FAIL 也 0，本脚本是取证，不是门禁）。

跑法：.venv/Scripts/python.exe devtools/_qa_soc_logic.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import soc_est as S
import soc_feed as F

ROWS: list[tuple[str, str, str]] = []


def rec(tag: str, ok: bool, detail: str) -> None:
    ROWS.append((tag, "PASS" if ok else "FAIL", detail))


# ============================================================ 1. 查表
def q1_table() -> None:
    tbl = S.OCV_TABLE
    # 严格单调（SOC 与 OCV 都严格递增）
    soc_mono = all(tbl[i][0] > tbl[i - 1][0] for i in range(1, len(tbl)))
    ocv_mono = all(tbl[i][1] > tbl[i - 1][1] for i in range(1, len(tbl)))
    rec("1.1 表严格单调（SOC↑ 且 OCV↑）", soc_mono and ocv_mono,
        f"{len(tbl)} 点 soc_mono={soc_mono} ocv_mono={ocv_mono}")

    # 互逆：soc_from_ocv(ocv_from_soc(s)) 在每个结点/中点都该回到 s
    worst = 0.0
    worst_at = 0.0
    for i in range(0, 100001):
        s = i / 1000.0
        back = S.soc_from_ocv(S.ocv_from_soc(s))
        d = abs(back - s)
        if d > worst:
            worst, worst_at = d, s
    rec("1.2 查表互逆（10 万点最大往返误差 < 1e-6 %）", worst < 1e-6,
        f"最大 {worst:.3e}% @ SOC={worst_at}")

    # 表外不外推
    lo = S.ocv_from_soc(-999.0)
    hi = S.ocv_from_soc(999.0)
    rec("1.3 ocv_from_soc 低端不外推（取 3.0818）", abs(lo - 3.0818) < 1e-12, f"{lo}")
    rec("1.4 ocv_from_soc 高端不外推（取 4.4981）", abs(hi - 4.4981) < 1e-12, f"{hi}")
    lo2 = S.soc_from_ocv(0.0)
    hi2 = S.soc_from_ocv(9.0)
    rec("1.5 soc_from_ocv 低端不外推（取 0）", abs(lo2 - 0.0) < 1e-12, f"{lo2}")
    rec("1.6 soc_from_ocv 高端不外推（取 100）", abs(hi2 - 100.0) < 1e-12, f"{hi2}")

    # 非单调表：from_dict 必须拒绝（返回 None）
    bad = [(0, 3.0), (10, 3.2), (20, 3.1), (30, 3.4)]          # OCV 回折
    cfg = S.SocCfg.from_dict({"number": "X", "ocv_table": [[a, b] for a, b in bad]})
    rec("1.7 非单调 OCV 表被 from_dict 拒绝", cfg is None, f"返回 {cfg!r}")
    # SOC 非单调
    bad2 = [(0, 3.0), (10, 3.2), (10, 3.4), (30, 3.6)]
    cfg2 = S.SocCfg.from_dict({"number": "X", "ocv_table": [[a, b] for a, b in bad2]})
    rec("1.8 SOC 非单调表被拒绝", cfg2 is None, f"返回 {cfg2!r}")
    # 单点表被拒绝
    cfg3 = S.SocCfg.from_dict({"number": "X", "ocv_table": [[0, 3.0]]})
    rec("1.9 单点表被拒绝", cfg3 is None, f"返回 {cfg3!r}")
    # 好表可装载
    cfg4 = S.SocCfg.from_dict(S.SocCfg("E74").to_dict())
    rec("1.10 正式表往返可装载", cfg4 is not None and cfg4.n_cells == 125,
        f"{cfg4!r}")


# ============================================================ 2. 清洗
def _unbalanced(n: int, hi: int, lo: int) -> list[int]:
    return [hi] * n + [lo] * (125 - n)


def q2_clean() -> None:
    # 2a 整模组偏移（40x3300 + 85x3200）：不得把 40 节当坏点全部回填
    r = S.clean_cells(_unbalanced(40, 3300, 3200), n_cells=125)
    rec("2.1 40节3300+85节3200：坏点数=0（不整模组回填）", len(r.bad_idx) == 0,
        f"bad={len(r.bad_idx)} mean={r.mean_mv:.1f} gap={r.gap_mv:.2f} residual={r.residual}")
    rec("2.2 同场景 total≈404V", abs(r.pack_mv / 1000.0 - 404.0) < 0.01,
        f"{r.pack_mv / 1000.0:.3f}V（真值 404.000）")

    # 2b 单模组偏移（25x3300 + 100x3200）：IQR 会不会塌成 0 把整模组回填？
    r = S.clean_cells(_unbalanced(25, 3300, 3200), n_cells=125)
    rec("2.3 25节3300+100节3200：坏点数=0（单模组真实不均衡不回填）",
        len(r.bad_idx) == 0,
        f"bad={len(r.bad_idx)}（若=25 说明整模组被误回填）gap={r.gap_mv:.2f} residual={r.residual}")

    # 2c 两节断线：一节 0、一节 5000
    base = [3200] * 125
    base[7] = 0
    base[60] = 5000
    r = S.clean_cells(base, n_cells=125)
    rec("2.4 两节断线定位（bad_idx=[8,61]）", r.bad_idx == [8, 61], f"bad_idx={r.bad_idx}")
    rec("2.5 断线回填为健康均值", abs(r.cells[7] - r.mean_mv) < 1e-9
        and abs(r.cells[60] - r.mean_mv) < 1e-9,
        f"cell8={r.cells[7]:.1f} cell61={r.cells[60]:.1f} mean={r.mean_mv:.1f}")
    rec("2.6 断线后 total≈400V", abs(r.pack_mv / 1000.0 - 400.0) < 0.05,
        f"{r.pack_mv / 1000.0:.3f}V")

    # 2d 量程边界：整串取端点值时若被算作"健康"说明 ≥/≤ 是闭区间
    #    （用整串同值避开 MAD 二级筛，纯看量程谓词）
    r = S.clean_cells([2500] * 125, n_cells=125)
    rec("2.7a 量程下界 2500 视为健康（闭区间）", r.ok and r.n_raw == 125,
        f"n_raw={r.n_raw} ok={r.ok}")
    r = S.clean_cells([4600] * 125, n_cells=125)
    rec("2.7b 量程上界 4600 视为健康（闭区间）", r.ok and r.n_raw == 125,
        f"n_raw={r.n_raw} ok={r.ok}")
    r = S.clean_cells([2499] * 125, n_cells=125)
    rec("2.7c 2499 超下界 → 不可用", not r.ok and r.n_raw == 0, f"n_raw={r.n_raw} ok={r.ok}")
    r = S.clean_cells([4601] * 125, n_cells=125)
    rec("2.7d 4601 超上界 → 不可用", not r.ok and r.n_raw == 0, f"n_raw={r.n_raw} ok={r.ok}")

    # 2e 定位 2.3 的悬崖：整模组(25节)偏移量多大开始被误判/回填
    for delta in (5, 10, 21, 50, 100):
        cells = [3200 + delta] * 25 + [3200] * 100
        r = S.clean_cells(cells, n_cells=125)
        rec(f"2.8 单模组+{delta}mV: 坏点数", len(r.bad_idx) == 0,
            f"bad={len(r.bad_idx)} residual={r.residual} total={r.pack_mv/1000:.3f}V"
            f"（真值 {(25*(3200+delta)+100*3200)/1000:.3f}V）")


# ============================================================ 3. 解析器
def q3_parser() -> None:
    # 3a 模组 2 不得覆盖模组 1（同序号）
    txt = (
        "Voltage1:\n" + " ".join(f"{i:02d}.31{i:02d}" for i in range(1, 26)) + "\n"
        "Voltage2:\n" + " ".join(f"{i:02d}.32{i:02d}" for i in range(1, 26)) + "\n"
        "CVMax=5446, Min=0\n"
    )
    rounds = F.parse_text(txt, n_modules=5, cells_per_module=25)
    r = rounds[0]
    mod1_ok = all(r.cells[i] == 3100 + (i + 1) for i in range(25))
    mod2_ok = all(r.cells[25 + i] == 3200 + (i + 1) for i in range(25))
    rec("3.1 模组2不覆盖模组1（键=(模组,序号)）",
        mod1_ok and mod2_ok and r.n_samples == 50,
        f"mod1[0]={r.cells[0]} mod2[0]={r.cells[25]} n={r.n_samples}")

    # 3b 同一轮 Voltage1: 重复出现（真机分包），不得劈成两轮
    txt = (
        "Voltage1:\n01.3180 02.3176\n03.3206\n"
        "Voltage1:\n04.3212 05.3207\n"
        "Temperature1:\n1.40 2.41\n"
        "CVMax=5446, Min=0\n"
    )
    rounds = F.parse_text(txt, n_modules=5, cells_per_module=25)
    rec("3.2 同轮 Voltage1 重复不劈轮", len(rounds) == 1, f"{len(rounds)} 轮")
    if rounds:
        rec("3.3 分包后 5 节齐全", rounds[0].n_samples == 5,
            f"n_samples={rounds[0].n_samples} cells[:6]={rounds[0].cells[:6]}")

    # 3c 一行被劈成两半：13.3184 → "…1" + "3.3184 …"，看是否冒充第 3 节
    # 正确行为：cell 13 应缺（None），第 3 节保持它自己的值，不被 3.3184 冒充
    good = ["%02d.%04d" % (i, 3100 + i) for i in range(1, 26)]
    row = " ".join(good)                     # 第 13 节是 "13.3113"
    # 在第 13 节数字内部劈开：保留到 "…12.3112 1"，余下 "3.3113 14.3114 …"
    cut = row.index("13.3113") + 1           # 落在 "1" 之后
    head, tail = row[:cut], row[cut:]
    chunk1 = "Voltage1:\n" + head
    chunk2 = tail + "\nTemperature1:\n1.40\nCVMax=1, Min=0\n"
    col = F.RoundCollector(n_modules=5, cells_per_module=25)
    out = col.feed(chunk1) + col.feed(chunk2)
    if out:
        r = out[0]
        cell3 = r.cells[2]
        cell13 = r.cells[12]
        rec("3.4 劈行：第3节未被 3.3113 冒充", cell3 in (None, 3103),
            f"cell3={cell3}（真值 3103；若=3113 则被冒充）")
        rec("3.5 劈行：第13节不因劈行而错值", cell13 in (None, 3113),
            f"cell13={cell13}（真值 3113）")

    # 3d 序号单调：真实升序正常收下
    txt = "Voltage1:\n01.3180 02.3176 03.3206 04.3212\nCVMax=1, Min=0\n"
    rounds = F.parse_text(txt, n_modules=5, cells_per_module=25)
    rec("3.6 升序序列全部收下", rounds and rounds[0].n_samples == 4,
        f"n={rounds[0].n_samples if rounds else 'NA'}")

    # 3e 段状态判新一轮：两整轮应切出 2 轮
    one = ("Voltage1:\n01.3180\nTemperature1:\n1.40\nBMS OK\n"
           "V=0.00,I=1.0\nCVMax=1, Min=0\n")
    rounds = F.parse_text(one + one, n_modules=5, cells_per_module=25)
    rec("3.7 段状态判新一轮（两整轮→2）", len(rounds) == 2, f"{len(rounds)} 轮")

    # 3f 冷启动：没有 Voltage 头就喂半行 —— 应被整段忽略（段落式采集的安全边界）
    col = F.RoundCollector(n_modules=5, cells_per_module=25)
    out = col.feed("3.3113 14.3114 15.3115\nCVMax=1, Min=0\n")
    rec("3.8 无 Voltage 头时半行被忽略（无冒充）",
        len(out) == 0, f"产出轮数={len(out)}")


# ============================================================ 4. 边界
def q4_bounds() -> None:
    cfg = S.SocCfg("E74")
    cases = [
        ("空列表", [], None, None),
        ("全 0", [0] * 125, None, None),
        ("全 None", [None] * 125, None, None),
        ("全 NaN", [float("nan")] * 125, None, None),
        ("单电芯", [3300], None, None),
        ("电芯数不足(60)", [3300] * 60, None, None),
        ("部分 NaN", [3300] * 124 + [float("nan")], None, None),
    ]
    for name, cells, _, _ in cases:
        est = S.SocEstimator(cfg)
        try:
            r = est.update(cells, [40] * 40, i_meas=1.0, ts=0.0)
            ok = 0.0 <= r.soc <= 100.0
            rec(f"4.x {name}: 不抛且 soc∈[0,100]", ok, f"soc={r.soc:.3f} flag={r.flag!r}")
        except Exception as exc:              # noqa: BLE001
            rec(f"4.x {name}: 不抛且 soc∈[0,100]", False, f"抛出 {exc!r}")

    # 超大 dt=1e9
    est = S.SocEstimator(cfg)
    est.update([3900] * 125, [40] * 40, i_meas=0.0, ts=0.0, contactor_open=True)
    try:
        r = est.update([3900] * 125, [40] * 40, i_meas=0.0, ts=1e9, contactor_open=True)
        rec("4.8 超大 dt=1e9 不抛且 soc∈[0,100]", 0 <= r.soc <= 100,
            f"soc={r.soc:.3f} init={r.initialised}")
    except Exception as exc:                  # noqa: BLE001
        rec("4.8 超大 dt=1e9 不抛且 soc∈[0,100]", False, f"抛出 {exc!r}")

    # ts 倒退
    est = S.SocEstimator(cfg)
    est.update([3900] * 125, [40] * 40, i_meas=50.0, ts=100.0)
    try:
        r = est.update([3900] * 125, [40] * 40, i_meas=50.0, ts=10.0)
        rec("4.9 ts 倒退 不抛且 soc∈[0,100]", 0 <= r.soc <= 100, f"soc={r.soc:.3f}")
    except Exception as exc:                  # noqa: BLE001
        rec("4.9 ts 倒退 不抛且 soc∈[0,100]", False, f"抛出 {exc!r}")

    # NaN ts
    est = S.SocEstimator(cfg)
    est.update([3900] * 125, [40] * 40, i_meas=50.0, ts=100.0)
    try:
        r = est.update([3900] * 125, [40] * 40, i_meas=50.0, ts=float("nan"))
        rec("4.10 NaN ts 不抛且 soc∈[0,100]", 0 <= r.soc <= 100,
            f"soc={r.soc:.3f}（NaN? {r.soc != r.soc}）")
    except Exception as exc:                  # noqa: BLE001
        rec("4.10 NaN ts 不抛且 soc∈[0,100]", False, f"抛出 {exc!r}")

    # 长跑：400 轮恒流放电，SOC 单调不越界
    est = S.SocEstimator(cfg)
    est.reset(soc=99.0)
    lo, hi = 100.0, 0.0
    for i in range(400):
        r = est.update([3990] * 125, [40] * 40, i_meas=12.0, ts=i * 1.0)
        lo = min(lo, r.soc)
        hi = max(hi, r.soc)
    rec("4.11 400 轮 12A 放电 soc 恒在 [0,100]", 0.0 <= lo and hi <= 100.0,
        f"[{lo:.2f}, {hi:.2f}] 末={r.soc:.2f}")


# ============================================================ 5. 电流
def q5_current() -> None:
    cfg = S.SocCfg("E74")
    # -250A 轨值：不积分
    est = S.SocEstimator(cfg)
    est.reset(soc=50.0)
    for i in range(30):
        est.update([3903.8] * 125, [40] * 40, i_meas=-250.0, ts=i * 2.0,
                   contactor_open=False)
    rec("5.1 -250A 不积分（soc 恒 50）", abs(est.soc - 50.0) < 1e-6, f"soc={est.soc:.4f}")
    rec("5.2 -250A 不污染零漂（bias 恒 0）", abs(est.i_bias) < 1e-12, f"bias={est.i_bias:.4f}")

    # -250A + 接触器全断：仍不得学零漂
    est = S.SocEstimator(cfg)
    est.reset(soc=50.0)
    for i in range(30):
        est.update([3903.8] * 125, [40] * 40, i_meas=-250.0, ts=i * 2.0,
                   contactor_open=True)
    rec("5.3 -250A 接触器全断也不学零漂", abs(est.i_bias) < 1e-12, f"bias={est.i_bias:.4f}")

    # I_LIMIT 拦不住时（正好 200 / 199.9）会发生什么：应正常积分、有界
    est = S.SocEstimator(cfg)
    est.reset(soc=80.0)
    for i in range(60):
        est.update([3990] * 125, [40] * 40, i_meas=200.0, ts=i * 1.0)
    rec("5.4 正好 200A 不拦：正常积分且不越界",
        0 <= est.soc <= 100 and est.soc != 80.0, f"60s@200A→soc={est.soc:.2f}")
    # 5.5 超限整帧作废：与 i_meas=None（同样不积分）跑出**完全相同**的 soc，
    #     证明超限电流一丝都没进积分（电压回拉对两者一样）
    est_lim = S.SocEstimator(cfg)
    est_lim.reset(soc=80.0)
    est_none = S.SocEstimator(cfg)
    est_none.reset(soc=80.0)
    for i in range(60):
        est_lim.update([3990] * 125, [40] * 40, i_meas=200.1, ts=i * 1.0)
        est_none.update([3990] * 125, [40] * 40, i_meas=None, ts=i * 1.0)
    rec("5.5 超限(200.1A)与无电流帧等价（整帧作废）",
        abs(est_lim.soc - est_none.soc) < 1e-9,
        f"lim={est_lim.soc:.6f} none={est_none.soc:.6f}")

    # 小零漂（接触器全断、读数 2.5A）应被学掉
    est = S.SocEstimator(cfg)
    est.reset(soc=50.0)
    for i in range(40):
        est.update([3903.8] * 125, [40] * 40, i_meas=2.5, ts=i * 2.0,
                   contactor_open=True)
    rec("5.6 全断时 2.5A 被学成零漂（bias→≈2.5）", abs(est.i_bias - 2.5) < 0.2,
        f"bias={est.i_bias:.3f}")


# ============================================================ 6. 粘滞
def q6_sticky() -> None:
    def rnd(with_air: bool, open_: bool = True) -> str:
        lines = ["Voltage1:"] + [f"{i:02d}.3180" for i in range(1, 26)]
        lines += ["Temperature1:"] + ["1.40"] * 8 + ["BMS OK"]
        if with_air:
            lines += ["AIR+=%d,AIR-=%d,PRE=%d" % ((0, 0, 0) if open_ else (1, 1, 1))]
        lines += ["V=0.00,I=1.0", "CVMax=1, Min=0"]
        return "\n".join(lines)

    col = F.RoundCollector(n_modules=1, cells_per_module=25)
    got = col.feed(rnd(True, True))
    for _ in range(5):
        got += col.feed(rnd(False))
    rec("6.1 缺 AIR 行继承上一轮（连续 5 轮仍 True）",
        all(r.contactor_open is True for r in got),
        f"{[r.contactor_open for r in got]}")

    # 超过 STICKY_ROUNDS 回到未知
    col = F.RoundCollector(n_modules=1, cells_per_module=25)
    col.feed(rnd(True, True))
    tail = []
    for _ in range(F.STICKY_ROUNDS + 3):
        tail += col.feed(rnd(False))
    rec("6.2 超过 STICKY_ROUNDS 回到未知（None）", tail[-1].contactor_open is None,
        f"末轮={tail[-1].contactor_open} 上限={F.STICKY_ROUNDS}")

    # AIR 行明确给出"未断开"(False) 应覆盖粘滞，而不是继续继承 True
    col = F.RoundCollector(n_modules=1, cells_per_module=25)
    got = col.feed(rnd(True, True)) + col.feed(rnd(True, False)) + col.feed(rnd(False))
    rec("6.3 明确 AIR=1 覆盖粘滞（False 后继承 False）",
        got[1].contactor_open is False and got[2].contactor_open is False,
        f"{[r.contactor_open for r in got]}")

    # reset() 后粘滞应被忘掉
    col = F.RoundCollector(n_modules=1, cells_per_module=25)
    col.feed(rnd(True, True))
    col.reset()
    got = col.feed(rnd(False))
    rec("6.4 reset 后不再继承（None）", got[0].contactor_open is None,
        f"{got[0].contactor_open}")


def main() -> int:
    for fn in (q1_table, q2_clean, q3_parser, q4_bounds, q5_current, q6_sticky):
        try:
            fn()
        except Exception as exc:              # noqa: BLE001
            import traceback
            traceback.print_exc()
            rec(fn.__name__ + " 整体异常", False, repr(exc))

    n_fail = sum(1 for _, s, _ in ROWS if s == "FAIL")
    for tag, status, detail in ROWS:
        print(f"[{status}] {tag}  —— {detail}")
    print(f"\n{len(ROWS) - n_fail}/{len(ROWS)} 通过；FAIL={n_fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
