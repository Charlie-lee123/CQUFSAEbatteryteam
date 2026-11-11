"""soc_ekf.SocEKF4 独立自测：零依赖、可脱离 Qt 直接跑。

用法::

    .venv\\Scripts\\python.exe devtools\\_verify_soc_ekf.py

输出 ``N/N 项通过`` + ``IS_PASS: YES/NO``，退出码 0/1。

真值轨迹用**同一套 RC-PNGV 参数**合成：先按精确指数离散推进状态，再用
``V_cell = OCV(z) - I*r0 - U1 - U2 - Uc`` 造端电压，因此干净量测下新息≈0，
EKF 只需靠安时积分即可跟踪 —— 这正是考核「跟踪 / 防发散」而非「参数辨识」。
"""

from __future__ import annotations

import math
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from soc_ekf import EkfParams, EkfStep, SocEKF4
from soc_est import SocCfg, ocv_from_soc, soc_from_ocv

Q_AH = 12.041
TABLE = SocCfg().ocv_table


class Truth:
    """按 soc_ekf 的同一套方程合成真值状态与端电压。"""

    def __init__(self, z0: float, p: EkfParams):
        self.z = z0
        self.u1 = 0.0
        self.u2 = 0.0
        self.uc = 0.0
        self.p = p

    def advance(self, i: float, dt: float) -> None:
        p = self.p
        a1 = math.exp(-dt / p.tau1_s)
        a2 = math.exp(-dt / p.tau2_s)
        a4 = math.exp(-dt / p.drift_tau_s)
        self.u1 = a1 * self.u1 + p.r1_ohm * (1.0 - a1) * i
        self.u2 = a2 * self.u2 + p.r2_ohm * (1.0 - a2) * i
        self.uc = a4 * self.uc + p.drift_gain_v_per_as * i * dt
        self.z -= i * dt / (3600.0 * Q_AH)

    def voltage(self, i: float) -> float:
        p = self.p
        return (ocv_from_soc(self.z * 100.0, TABLE)
                - i * p.r0_ohm - self.u1 - self.u2 - self.uc)


def _p_diag_ok(ekf: SocEKF4) -> bool:
    for d in range(4):
        v = ekf.P[d][d]
        if not math.isfinite(v) or v < 1e-12 or v > 1e2:
            return False
    for r in range(4):
        for c in range(4):
            if not math.isfinite(ekf.P[r][c]):
                return False
    return True


# ---------------------------------------------------------------- 各项判据


def t01_1c_tracking():
    """合成 1C 恒流放电，SOC 跟踪误差 < 2%。"""
    p, cfg = EkfParams(), SocCfg()
    ekf = SocEKF4(cfg, p)
    z0 = 0.90
    ekf.reset(z=z0)
    truth = Truth(z0, p)
    rng = random.Random(7)
    i, dt = 12.0, 1.0
    max_err = 0.0
    for _ in range(600):
        truth.advance(i, dt)
        v = truth.voltage(i) + rng.uniform(-0.0005, 0.0005)  # ±0.5 mV 噪声
        st = ekf.step(v, i, dt)
        max_err = max(max_err, abs(st.z - truth.z))
    return max_err < 0.02, f"1C/600 轮最大跟踪误差 {max_err * 100:.4f} %SOC"


def t02_plateau_200():
    """平台段 10~30% 连续 200 轮不发散、P 对角始终有限。"""
    p, cfg = EkfParams(), SocCfg()
    ekf = SocEKF4(cfg, p)
    z0 = 0.25
    ekf.reset(z=z0)
    truth = Truth(z0, p)
    i, dt = 2.0, 1.0
    ok = True
    for _ in range(200):
        truth.advance(i, dt)
        st = ekf.step(truth.voltage(i), i, dt)
        ok = ok and _p_diag_ok(ekf)
        ok = ok and math.isfinite(st.z) and 0.10 <= st.z <= 0.30
    return ok, "平台段 200 轮 P 对角有限 / SOC 稳定在 10~30%"


def t03_dt_jitter():
    """dt 抖动 0.9~2.5 s（随机）仍能跟踪。"""
    p, cfg = EkfParams(), SocCfg()
    ekf = SocEKF4(cfg, p)
    z0 = 0.85
    ekf.reset(z=z0)
    truth = Truth(z0, p)
    rng = random.Random(11)
    i = 8.0
    max_err = 0.0
    for _ in range(400):
        dt = rng.uniform(0.9, 2.5)
        truth.advance(i, dt)
        st = ekf.step(truth.voltage(i), i, dt)
        max_err = max(max_err, abs(st.z - truth.z))
    return max_err < 0.02, f"dt 抖动 400 轮最大跟踪误差 {max_err * 100:.4f} %SOC"


def t04_dt_anomaly():
    """dt=0 / dt=-1 / dt=600 / dt=nan 不炸。"""
    p, cfg = EkfParams(), SocCfg()
    ekf = SocEKF4(cfg, p)
    ekf.reset(z=0.6)
    v = ocv_from_soc(60.0, TABLE)
    ok = True
    for dt in (0.0, -1.0, 600.0, float("nan")):
        st = ekf.step(v, 3.0, dt)
        ok = ok and isinstance(st, EkfStep)
        ok = ok and math.isfinite(st.z) and math.isfinite(st.ocv_offset_v)
        ok = ok and _p_diag_ok(ekf)
    return ok, "四种异常 dt 均返回有限 EkfStep / P 有限"


def t04b_dt_over10_no_integration():
    """dt>10 不做时间更新：连喂 10 轮 dt=600 大电流，SOC 不被积分拉跑。"""
    p, cfg = EkfParams(), SocCfg()
    ekf = SocEKF4(cfg, p)
    z0 = 0.8
    ekf.reset(z=z0)
    v = ocv_from_soc(z0 * 100.0, TABLE) - 12.0 * p.r0_ohm
    st = None
    for _ in range(10):
        st = ekf.step(v, 12.0, 600.0)
    ok = st is not None and abs(st.z - z0) < 0.05
    return ok, f"dt=600×10 后 z={st.z:.6f}（起点 {z0}，无库仑积分）"


def t05_current_rail():
    """电流零漂 ±2 A、-250 A 轨值注入不炸。"""
    p, cfg = EkfParams(), SocCfg()
    ekf = SocEKF4(cfg, p)
    ekf.reset(z=0.5)
    v = ocv_from_soc(50.0, TABLE)
    seq = [2.0, -2.0] * 20 + [-250.0] * 20 + [250.0, -250.0, 2.0]
    ok = True
    for i in seq:
        st = ekf.step(v, i, 1.0)
        ok = ok and math.isfinite(st.z) and 0.0 <= st.z <= 1.0
        ok = ok and _p_diag_ok(ekf)
    return ok, "±2A 零漂 / -250A 轨值共 63 轮无异常 / P 有限"


def _bad_point_delta(gate: float):
    """收敛 400 轮后注入单个 500 mV 坏点，返回本轮 SOC 拽动（%SOC）与 step。"""
    p = EkfParams(innov_gate_v=gate)
    cfg = SocCfg()
    ekf = SocEKF4(cfg, p)
    z0 = 0.90
    ekf.reset(z=z0)
    truth = Truth(z0, p)
    i, dt = 2.0, 1.0
    for _ in range(400):
        truth.advance(i, dt)
        ekf.step(truth.voltage(i), i, dt)
    z_before = ekf.x[0]
    pz_before = ekf.P[0][0]
    truth.advance(i, dt)
    st = ekf.step(truth.voltage(i) + 0.5, i, dt)
    return abs(st.z - z_before) * 100.0, pz_before, st


def t06_bad_point():
    """500 mV 电压跳变（坏点）单轮把 SOC 拽动 < 0.5%。"""
    delta, pz, st = _bad_point_delta(0.15)
    ok = delta < 0.5 and not st.recovered and st.valid
    return ok, (f"单轮 SOC 拽动 {delta:.4f} %SOC（收敛后 P_z={pz:.3e}），"
               f"valid={st.valid} recovered={st.recovered}")


def t06b_gate_effective():
    """新息门限软降权生效：门限越小（降权越强）单轮拽动越小。"""
    d_gate, _, _ = _bad_point_delta(0.15)
    d_none, _, _ = _bad_point_delta(1e6)   # 门限极大 → 不降权
    ok = d_gate < d_none
    return ok, f"gate=0.15 拽动 {d_gate:.4f} %SOC < 无门限 {d_none:.4f} %SOC"


def t07_rest_converge():
    """静置 I=0 100 轮收敛到 OCV 查表值，|z - soc_from_ocv(v)/100| < 0.005。

    这是 team-lead 点名的硬判据：平台段 H[0] 小、K_z 被 P 压小，光靠卡尔曼
    增益收敛极慢，必须靠静置软锚定拉回来；且锚定必须确实启用（不是靠放宽阈值）。
    """
    p, cfg = EkfParams(), SocCfg()
    ekf = SocEKF4(cfg, p)
    v = ocv_from_soc(20.0, TABLE)
    z_target = soc_from_ocv(v, TABLE) / 100.0
    ekf.reset(z=0.35)
    st = None
    anchored_seen = False
    for _ in range(100):
        st = ekf.step(v, 0.0, 1.0)
        anchored_seen = anchored_seen or ekf.anchored
    z_err = abs(st.z - z_target)          # z 单位；0.005 = 0.5 %SOC
    ok = z_err < 0.005 and anchored_seen and st.valid and not st.recovered
    return ok, (f"静置 100 轮后 z={st.z:.6f} / 查表 {z_target:.6f}，"
                f"|z-查表|={z_err:.6f} < 0.005；锚定启用={anchored_seen}")


def t07b_rest_badpoint_not_welded():
    """连续静置期间喂单个 500 mV 坏点：锚定不把坏点电压焊进 SOC。

    判据（对齐 team-lead 要求「应仍受新息门限保护」）：
    * 坏点的 OCV 反查值 z_bad≈0.755，而开启门限时 z 不会跳到它附近；
    * 门限开启的单轮拽动 << 门限关闭（innov_gate_v=1e6）的拽动；
    * 坏点过后恢复静置，z 收敛回 OCV 查表值（不永久污染）。
    """
    v = ocv_from_soc(20.0, TABLE)
    z_target = soc_from_ocv(v, TABLE) / 100.0
    z_bad = soc_from_ocv(v + 0.5, TABLE) / 100.0

    def trial(gate):
        p = EkfParams(innov_gate_v=gate)
        ekf = SocEKF4(SocCfg(), p)
        ekf.reset(z=z_target)
        for _ in range(10):                  # 连续静置，锚定已启用
            ekf.step(v, 0.0, 1.0)
        z_before = ekf.x[0]
        anchored_before = ekf.anchored
        st_bad = ekf.step(v + 0.5, 0.0, 1.0)
        z_after = st_bad.z
        for _ in range(30):                 # 坏点过后继续静置
            st_rec = ekf.step(v, 0.0, 1.0)
        return (z_before, z_after, st_rec.z, st_bad, anchored_before, ekf)

    z0, z_on, z_rec, st_on, anch_on, ekf_on = trial(0.15)
    _, z_off, _, _, _, _ = trial(1e6)

    jump_on = abs(z_on - z0)
    jump_off = abs(z_off - z0)
    ok = (
        anch_on                                   # 坏点前锚定确实启用
        and st_on.valid and not st_on.recovered   # 正常数值、无自愈
        and abs(z_on - z_bad) > 0.30              # 没被焊到坏点反查值附近
        and jump_on < jump_off * 0.5              # 门限开明显比门限关拽得小
        and abs(z_rec - z_target) < 0.005         # 坏点过后恢复、不永久污染
        and _p_diag_ok(ekf_on)
    )
    return ok, (f"门限开: z {z0:.4f}->{z_on:.4f} (Δ={jump_on*100:.2f}%SOC), "
                f"锚定启用={anch_on}; 门限关: Δ={jump_off*100:.2f}%SOC; "
                f"坏点反查 z_bad={z_bad:.4f}; 恢复后 |z-查表|={abs(z_rec-z_target)*100:.4f}%SOC")


def t07c_anchor_then_load_not_pinned():
    """锚定后切回 1C 带载：SOC 正常下降，P 没被清空、增益还活着。

    「不钉死」判据：静置锚定收敛后给 1C 放电，z 明显下降且仍跟踪真值
    （说明卡尔曼增益没塌死）；P[0][0] 全程有限、不越界、非 reset() 初值。
    """
    p, cfg = EkfParams(), SocCfg()
    ekf = SocEKF4(cfg, p)
    v_rest = ocv_from_soc(20.0, TABLE)
    ekf.reset(z=0.35)
    for _ in range(100):
        ekf.step(v_rest, 0.0, 1.0)
    z0 = ekf.x[0]
    p00_rest = ekf.P[0][0]

    truth = Truth(z0, p)
    i, dt = 12.0, 1.0
    max_err = 0.0
    st = None
    anchored_under_load = False
    p_ok = True
    for _ in range(60):
        truth.advance(i, dt)
        st = ekf.step(truth.voltage(i), i, dt)
        max_err = max(max_err, abs(st.z - truth.z))
        anchored_under_load = anchored_under_load or ekf.anchored
        p_ok = p_ok and _p_diag_ok(ekf)

    q = cfg.q_ah
    expect_drop = i * dt * 60 / (3600.0 * q)     # 理论库仑下降量（z 单位）
    drop = z0 - st.z
    # P 既不是被 reset() 打回初值(_p0[0][0]=1e-2)、也没塌到下限
    p00_end = ekf.P[0][0]
    p_alive = (1e-12 < p00_rest < 1e2 and 1e-12 < p00_end < 1e2
               and abs(p00_end - 1e-2) > 1e-6)
    ok = (drop > 0.6 * expect_drop                  # SOC 正常下降（>理论 60%）
          and drop < 1.4 * expect_drop              # 也没被钉死/超调
          and max_err < 0.02                        # 增益还活着、仍跟踪
          and not anchored_under_load                # 带载不锚定
          and p_ok and p_alive)
    return ok, (f"静置后 z0={z0:.4f} P00={p00_rest:.3e} → 1C 60 轮 z={st.z:.4f} "
                f"(下降 {drop*100:.3f}%SOC / 理论 {expect_drop*100:.3f})；"
                f"跟踪误差={max_err*100:.4f}%SOC；P00_end={p00_end:.3e}；"
                f"带载锚定={anchored_under_load}")


def t08_reset_clean():
    """reset() 后状态干净（含 z / v_cell_v 两种冷启动）。"""
    p, cfg = EkfParams(), SocCfg()
    ekf = SocEKF4(cfg, p)
    ekf.reset(z=0.8)
    for _ in range(30):
        ekf.step(ocv_from_soc(50.0, TABLE) + 0.02, 5.0, 1.0)
    ekf.reset()
    c1 = (abs(ekf.x[0] - 0.5) < 1e-12 and ekf.x[1] == 0.0
          and ekf.x[2] == 0.0 and ekf.x[3] == 0.0
          and ekf._bad_count == 0 and _p_diag_ok(ekf))
    ekf.reset(z=0.73)
    c2 = abs(ekf.x[0] - 0.73) < 1e-12 and ekf.x[1] == 0.0
    ekf.reset(v_cell_v=ocv_from_soc(42.0, TABLE))
    c3 = abs(ekf.x[0] - 0.42) < 1e-9
    # 要求 7：reset() 必须清掉静置计数，且 anchored 复位
    for _ in range(5):
        ekf.step(ocv_from_soc(42.0, TABLE), 0.0, 1.0)
    c4 = ekf._rest_count > 0          # 先确认确实累计过
    ekf.reset()
    c5 = ekf._rest_count == 0 and ekf.anchored is False
    return c1 and c2 and c3 and c4 and c5, (
        "无参/z/v_cell_v 三种 reset 干净；reset() 清静置计数与 anchored")


def t09_nan_recover():
    """连续喂 NaN 触发 recovered=True。"""
    p, cfg = EkfParams(), SocCfg()
    ekf = SocEKF4(cfg, p)
    ekf.reset(z=0.5)
    seen = False
    finite_after = True
    for _ in range(8):
        st = ekf.step(float("nan"), 1.0, 1.0)
        if st.recovered:
            seen = True
        finite_after = finite_after and math.isfinite(st.z) and _p_diag_ok(ekf)
    return seen and finite_after, "连续 NaN 第 5 轮触发 recovered=True 且状态有限"


def t10_ocv_offset():
    """ocv_offset_v 与手工计算 i*r0+u1+u2+uc 一致；正常轮标志位正确。"""
    p, cfg = EkfParams(), SocCfg()
    ekf = SocEKF4(cfg, p)
    ekf.reset(z=0.7)
    i = 7.5
    st = ekf.step(ocv_from_soc(70.0, TABLE), i, 1.0)
    manual = i * p.r0_ohm + st.u1 + st.u2 + st.uc
    ok = (abs(st.ocv_offset_v - manual) < 1e-12
          and st.valid and not st.recovered)
    return ok, (f"ocv_offset_v={st.ocv_offset_v:.8f} V == "
               f"i*r0+u1+u2+uc={manual:.8f} V")


def t11_stress_1000():
    """1000 轮压力：P 不出现 NaN/Inf、对角不越界。"""
    p, cfg = EkfParams(), SocCfg()
    ekf = SocEKF4(cfg, p)
    ekf.reset(z=0.6)
    rng = random.Random(2026)
    v_nom = ocv_from_soc(60.0, TABLE)
    ok = True
    for _ in range(1000):
        dt = rng.uniform(0.9, 2.5)
        i = rng.choice([rng.uniform(-3.0, 15.0), -250.0, 250.0])
        v = v_nom
        roll = rng.random()
        if roll < 0.02:
            v = float("nan")
        elif roll < 0.04:
            v = v_nom + rng.uniform(-0.6, 0.6)
        st = ekf.step(v, i, dt)
        ok = ok and math.isfinite(st.z) and 0.0 <= st.z <= 1.0
        ok = ok and math.isfinite(st.ocv_offset_v)
        ok = ok and _p_diag_ok(ekf)
        if not ok:
            break
    return ok, "1000 轮（含 NaN / 坏点 / 轨值 / dt 抖动）P 全程有限且对角在界"


def t12_signature():
    """接口签名与默认参数符合契约。"""
    import inspect
    init = list(inspect.signature(SocEKF4.__init__).parameters)
    reset = list(inspect.signature(SocEKF4.reset).parameters)
    step = list(inspect.signature(SocEKF4.step).parameters)
    ok = (init == ["self", "cfg", "params"]
          and reset == ["self", "z", "v_cell_v"]
          and step == ["self", "v_cell_v", "i_a", "dt_s"])
    d = EkfParams()
    ok = ok and (abs(d.r0_ohm - 0.0068) < 1e-12
                 and abs(d.r1_ohm - 0.0018) < 1e-12
                 and abs(d.tau1_s - 20.0) < 1e-9
                 and abs(d.r2_ohm - 0.0022) < 1e-12
                 and abs(d.tau2_s - 180.0) < 1e-9
                 and abs(d.drift_gain_v_per_as - 3.0e-5) < 1e-15
                 and abs(d.drift_tau_s - 900.0) < 1e-9
                 and abs(d.r_v - 2.5e-5) < 1e-15
                 and abs(d.innov_gate_v - 0.15) < 1e-12)
    return ok, "签名 __init__/reset/step 与 13 个默认参数逐项核对"


# ---------------------------------------------------------------- 运行


def main() -> int:
    tests = [
        ("1C 恒流放电跟踪误差 < 2%", t01_1c_tracking),
        ("平台段 200 轮不发散 / P 有限", t02_plateau_200),
        ("dt 抖动 0.9~2.5s 仍跟踪", t03_dt_jitter),
        ("dt=0/-1/600/nan 不炸", t04_dt_anomaly),
        ("dt>10 不积分（无库仑拉跑）", t04b_dt_over10_no_integration),
        ("电流零漂±2A / -250A 轨值不炸", t05_current_rail),
        ("500mV 坏点单轮 SOC 拽动 < 0.5%", t06_bad_point),
        ("新息门限软降权生效", t06b_gate_effective),
        ("静置 I=0 100 轮收敛到 OCV 查表值 < 0.005", t07_rest_converge),
        ("静置坏点不焊入 / 受新息门限保护", t07b_rest_badpoint_not_welded),
        ("锚定后切回 1C 带载不被钉死 / P 未清空", t07c_anchor_then_load_not_pinned),
        ("reset() 后状态干净 / 清静置计数", t08_reset_clean),
        ("连续喂 NaN 触发 recovered=True", t09_nan_recover),
        ("ocv_offset_v == i*r0+u1+u2+uc", t10_ocv_offset),
        ("1000 轮压力 P 无 NaN/Inf / 对角不越界", t11_stress_1000),
        ("接口签名与默认参数", t12_signature),
    ]
    rows = []
    for name, fn in tests:
        try:
            cond, detail = fn()
            rows.append((name, bool(cond), detail))
        except Exception as exc:                 # noqa: BLE001
            rows.append((name, False, f"EXC {exc!r}"))

    passed = sum(1 for _, c, _ in rows if c)
    total = len(rows)
    print("=" * 72)
    print("SocEKF4 四状态 EKF 独立自测")
    print("=" * 72)
    for idx, (name, cond, detail) in enumerate(rows, 1):
        tag = "PASS" if cond else "FAIL"
        print(f"[{tag}] {idx:02d}. {name}")
        print(f"        {detail}")
    print("-" * 72)
    is_pass = passed == total
    print(f"{passed}/{total} 项通过")
    print(f"IS_PASS: {'YES' if is_pass else 'NO'}")
    return 0 if is_pass else 1


if __name__ == "__main__":
    sys.exit(main())
