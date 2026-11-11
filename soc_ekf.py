"""四状态二阶 RC-PNGV 扩展卡尔曼滤波 SOC 估算（纯 Python / 无 Qt / 无第三方依赖）。

状态向量（文献主流的二阶 RC-PNGV 四状态）::

    x = [z, U1, U2, Uc]^T
      z  in [0, 1]   SOC（0~1，不是百分数）
      U1 in V        快极化 RC 电压
      U2 in V        慢极化 RC 电压
      Uc in V        PNGV 漂移/回弹电容电压（吸收电流零漂与长期回弹）

观测是单体平均端电压 ``V_cell``（V，配合放电为正的电流 ``I``）::

    V_cell = OCV(z) - I*r0 - U1 - U2 - Uc
    =>  OCV = V_cell + (I*r0 + U1 + U2 + Uc) = V_cell + ocv_offset_v

平台段（10~30%）OCV 斜率天然趋近 0，卡尔曼增益会自动不给 SOC 纠偏 —— 这本身就
替代了原固定增益法的 κ 加权，本模块**不再额外乘 κ**。

⛔ ``step()`` 的契约
---------------------
``step(v_cell_v, i_a, dt_s)`` 的 ``i_a`` 必须是**放电为正**的有效电流（已扣零漂、
已按 ``SocCfg.i_sign`` 归一），模块内部**不再乘 i_sign**，避免重复换算；
``v_cell_v`` 是单体平均端电压（V），不是整包总压、不是 mV。

防发散硬要求（逐条落地）
-------------------------
1. ``dt<=0`` / ``dt>10`` 不做时间更新（P 不增长、状态不积分），只做观测更新；
   ``dt`` 非有限值直接返回 ``valid=False``。
2. 新息门限软降权：``|innov|>gate`` 时 ``R_eff = R*(|innov|/gate)**2``，降 K 而
   非硬丢弃、也非照单全收。
3. Joseph 形式更新保证 P 半正定；每轮对称化并把对角限幅到 ``[1e-12, 1e2]``。
4. ``x``/``P`` 非有限、``S<=0``、P 非对称到不可用 → 本轮 ``valid=False,
   recovered=True``，把状态重置到 ``soc_from_ocv(V_cell)/100``、U*=0、P 恢复初值。
5. 连续 ``N=5`` 轮 ``valid=False`` 也强制自愈。
6. 所有输入 ``math.isfinite`` 检查；``V_cell`` 不在 ``[2.0, 5.0]`` V 视为无效量测，
   本轮只做时间更新、``valid=False``。
7. ``step()`` 全程 ``try/except``，任何异常都收敛成一个有限的 ``EkfStep``，
   **绝不抛异常**。

静置软锚定（对齐主链路 ``_rest_anchor``）
------------------------------------------
平台段 H[0] 很小、K_z 被 P 压小，光靠卡尔曼增益向 OCV 反查值收敛极慢。故
``|I|<1.2 A`` 累计连续 >=3 轮后，在标准 EKF 更新之后对 ``x[0]`` 做
``z += 0.3*clamp(soc_from_ocv(V)/100 - z, ±0.15)`` 的小步回拉；增益受新息门限
软降权保护（坏点不会焊进 SOC）。**保留 P**（仅把 ``P[0][0]`` 乘 1.05），不清空
协方差，带载恢复时滤波记忆与增益都还在。不新增 ``EkfStep`` 字段，仅暴露只读
属性 ``anchored`` 供诊断。

局限（必须随报告一并说明）
---------------------------
E74 只有 1C 恒流放电曲线，**没有 HPPC 脉冲工况数据**，``EkfParams`` 里的 R1/R2/
tau1/tau2/漂移增益都是工程占位值，未经参数辨识。因此本模块 numerically 稳定、
可跟踪、不发散，但**不能声称比原「安时积分 + κ 加权 + 静置锚定」更准**。开关关闭时
仍走 ``soc_est`` 原路径、逐字节不变。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from soc_est import OCV_TABLE, SocCfg, ocv_from_soc, slope_at, soc_from_ocv

__all__ = ["EkfParams", "EkfStep", "SocEKF4"]

# ---------------------------------------------------------------- 保护常量
_P_MIN = 1e-12             # P 对角下限
_P_MAX = 1e2               # P 对角上限
_HI_DT = 10.0              # dt 超过此值不做时间更新
_V_LO = 2.0                # 单体端电压有效下限（V）
_V_HI = 5.0                # 单体端电压有效上限（V）
_Z_LO, _Z_HI = 0.0, 1.0
_U_LIM = 0.5               # U1/U2/Uc 限幅（V）
_N_INVALID_RECOVER = 5     # 连续 N 轮 valid=False 强制自愈
_ASYM_TOL = 1e-3           # P 非对称到不可用的相对判据

# 静置软锚定（语义对齐 soc_est.SocEstimator._rest_anchor）
_REST_I_MAX = 1.2          # 静置判据：|I| < 1.2 A（对齐 soc_est.REST_I_MAX_A）
_REST_MIN_ROUNDS = 3       # 连续 >=3 轮静置才启用锚定（躲单轮电流抖动误判）
_REST_GAIN = 0.3           # 对齐 soc_est.REST_GAIN
_REST_CAP = 0.15           # 单次锚定幅度上限（z 单位，= 15% SOC；对齐 REST_CAP_PCT）
_REST_P_BUMP = 1.05        # 锚定轮把 P[0][0] 轻微抬高的系数，免得 K_z 塌死
_REST_U_RELAX = 0.3        # 锚定轮把极化/漂移电压拉向 0 的松弛增益（静置极化最小）
_REST_MAX_COUNT = 1_000_000 # 静置计数封顶，防无界增长

# params 缺省兜底（仅在传入非法数值时启用，默认参数永远走原值）
_R_FALLBACK = 2.5e-5
_GATE_FALLBACK = 0.15
_R0_FALLBACK = 0.0068


def _fin(x: float, default: float) -> float:
    """有限则取 x，否则取 default。"""
    return x if math.isfinite(x) else default


def _mmul(A, B):
    """4x4 矩阵乘（纯 Python，不依赖 numpy）。"""
    return [[sum(A[i][k] * B[k][j] for k in range(4)) for j in range(4)]
            for i in range(4)]


def _trans(A):
    return [[A[j][i] for j in range(4)] for i in range(4)]


def _p0():
    """初始协方差：SOC 给较大不确定度，极化/漂移给较小值。"""
    return [
        [1e-2, 0.0, 0.0, 0.0],
        [0.0, 1e-4, 0.0, 0.0],
        [0.0, 0.0, 1e-4, 0.0],
        [0.0, 0.0, 0.0, 1e-4],
    ]


# ---------------------------------------------------------------- 数据结构


@dataclass
class EkfParams:
    """EKF 可调参数。全部为工程占位值，未经 HPPC 辨识（见模块 docstring）。"""

    r0_ohm: float = 0.0068                 # 6.8 mΩ 单节直流内阻
    r1_ohm: float = 0.0018                 # 快极化内阻
    tau1_s: float = 20.0                   # 快极化时间常数
    r2_ohm: float = 0.0022                 # 慢极化内阻
    tau2_s: float = 180.0                  # 慢极化时间常数
    drift_gain_v_per_as: float = 3.0e-5    # Uc 对 I*dt 的等效增益
    drift_tau_s: float = 900.0              # Uc 回弹时间常数
    q_soc: float = 1e-7                    # 过程噪声：SOC
    q_u1: float = 1e-8                     # 过程噪声：U1
    q_u2: float = 1e-8                     # 过程噪声：U2
    q_uc: float = 1e-9                     # 过程噪声：Uc
    r_v: float = 2.5e-5                    # 量测噪声 (5 mV)^2
    innov_gate_v: float = 0.15              # 新息门限（V）


@dataclass
class EkfStep:
    """一轮 EKF 结果。UI 只读这个对象。"""

    z: float                 # SOC 0~1
    u1: float               # 快极化电压（V）
    u2: float               # 慢极化电压（V）
    uc: float               # 漂移/回弹电压（V）
    ocv_offset_v: float    # = I*r0 + u1 + u2 + uc；OCV = V_cell + ocv_offset_v
    innovation_v: float    # 本轮新息（V）；无效量测轮为 0
    valid: bool             # 本轮数值是否有效
    recovered: bool         # 本轮是否触发了发散自愈重置


# ---------------------------------------------------------------- 估算器


class SocEKF4:
    """按轮喂 ``(V_cell, I, dt)``、逐轮出四状态估计的 EKF。**有状态**。"""

    def __init__(self, cfg, params: EkfParams | None = None):
        self.cfg = cfg if cfg is not None else SocCfg()
        self.params = params if params is not None else EkfParams()
        self._table = self.cfg.ocv_table or OCV_TABLE
        self.reset()

    # -- 对外 ---------------------------------------------------------

    def reset(self, z: float | None = None, v_cell_v: float | None = None) -> None:
        """清空状态。给了 ``z``（0~1）就用它；否则给了 ``v_cell_v`` 就用
        ``soc_from_ocv(v_cell_v)/100`` 冷启动；都没给就置 0.5。"""
        if z is not None:
            try:
                z0 = float(z)
            except (TypeError, ValueError):
                z0 = 0.5
            z0 = _fin(z0, 0.5)
        elif v_cell_v is not None:
            try:
                vv = float(v_cell_v)
                z0 = (soc_from_ocv(vv, self._table) / 100.0
                      if math.isfinite(vv) and _V_LO <= vv <= _V_HI else 0.5)
            except (TypeError, ValueError, IndexError):
                z0 = 0.5
            z0 = _fin(z0, 0.5)
        else:
            z0 = 0.5
        self.x = [max(_Z_LO, min(_Z_HI, z0)), 0.0, 0.0, 0.0]
        self.P = _p0()
        self._bad_count = 0
        self._rest_count = 0       # 连续静置轮数（带载清零）
        self._anchored = False     # 上一轮是否启用了静置软锚定

    @property
    def anchored(self) -> bool:
        """上一轮是否启用了静置软锚定（只读诊断；不进 EkfStep，接口冻结）。"""
        return self._anchored

    def step(self, v_cell_v: float, i_a: float, dt_s: float) -> EkfStep:
        """喂一轮 ``(V_cell[V], I[A, 放电为正], dt[s])``，返回 :class:`EkfStep`。

        ⛔ 任何情况下都不抛异常：最外层兜底会退化成一个有限的自愈结果。
        """
        try:
            return self._step(v_cell_v, i_a, dt_s)
        except Exception:                       # noqa: BLE001 - 绝不拖垮调用方
            try:
                return self._numeric_recover(v_cell_v, 0.0, 0.0)
            except Exception:                   # noqa: BLE001 - 最后兜底
                return EkfStep(0.5, 0.0, 0.0, 0.0, 0.0, 0.0, False, True)

    # -- 主流程 -------------------------------------------------------

    def _step(self, v_cell_v, i_a, dt_s) -> EkfStep:
        v = float(v_cell_v)
        i = float(i_a)
        dt = float(dt_s)

        # 1) 输入有限性。非有限 dt / I / V 一律本轮无效，不积分不更新。
        if not (math.isfinite(v) and math.isfinite(i) and math.isfinite(dt)):
            return self._invalid_finish(v_cell_v, i, dt)

        v_ok = (_V_LO <= v <= _V_HI)
        do_time = (0.0 < dt <= _HI_DT)     # dt<=0 或 dt>10 → 不做时间更新

        # 2) 时间更新（仅在 dt 合法时）。
        if do_time:
            if not self._time_update(i, dt):
                return self._numeric_recover(v_cell_v, i, dt)
            if not self._sanitize():
                return self._numeric_recover(v_cell_v, i, dt)

        # 3) 量测有效性：越界只做时间更新、valid=False（含连续异常自愈）。
        if not v_ok:
            return self._invalid_finish(v_cell_v, i, dt)

        # 4) 观测更新（dt 异常时仍执行，只是 P 不增长）。
        ok, innov = self._measure_update(v, i)
        if not ok:
            return self._numeric_recover(v_cell_v, i, dt)
        if not self._sanitize():
            return self._numeric_recover(v_cell_v, i, dt)

        self._bad_count = 0
        # 5) 统一收尾（静置计数 / 静置软锚定 / 封装结果）。
        return self._finish(v, i, dt, innov, True, False)

    # -- 时间更新 -----------------------------------------------------

    def _time_update(self, i: float, dt: float) -> bool:
        """x = f(x, I, dt)；P = F P F^T + Q。返回基本数值是否成立。"""
        p = self.params
        a1 = self._exp(-dt, p.tau1_s)
        a2 = self._exp(-dt, p.tau2_s)
        a4 = self._exp(-dt, p.drift_tau_s)
        if a1 is None or a2 is None or a4 is None:
            return False

        r1 = _fin(p.r1_ohm, 0.0018)
        r2 = _fin(p.r2_ohm, 0.0022)
        g4 = _fin(p.drift_gain_v_per_as, 3.0e-5)

        z, u1, u2, uc = self.x
        u1 = a1 * u1 + r1 * (1.0 - a1) * i
        u2 = a2 * u2 + r2 * (1.0 - a2) * i
        uc = a4 * uc + g4 * i * dt

        denom = 3600.0 * _fin(self.cfg.q_ah, 12.041)
        if denom <= 0.0 or not math.isfinite(denom):
            return False                     # 容量非法 → 不积分（交由自愈）
        z = z - i * dt / denom
        self.x = [z, u1, u2, uc]

        F = (1.0, a1, a2, a4)
        Q = (_fin(p.q_soc, 1e-7), _fin(p.q_u1, 1e-8),
             _fin(p.q_u2, 1e-8), _fin(p.q_uc, 1e-9))
        old = self.P
        self.P = [[F[r] * F[c] * old[r][c]
                   + (Q[r] if r == c else 0.0)
                   for c in range(4)] for r in range(4)]
        return True

    @staticmethod
    def _exp(neg_dt: float, tau: float):
        try:
            if not math.isfinite(tau) or tau <= 0.0:
                return None
            r = math.exp(neg_dt / tau)
            return r if math.isfinite(r) else None
        except (OverflowError, ValueError, ZeroDivisionError):
            return None

    # -- 观测更新（Joseph 形式） -------------------------------------

    def _measure_update(self, v: float, i: float):
        """返回 ``(ok, innovation)``。ok=False 表示数值失效，需要自愈。"""
        p = self.params
        r0 = _fin(p.r0_ohm, _R0_FALLBACK)
        r_v = _fin(p.r_v, _R_FALLBACK)
        if r_v <= 0.0:
            r_v = _R_FALLBACK
        gate = _fin(p.innov_gate_v, _GATE_FALLBACK)
        if gate <= 0.0:
            gate = _GATE_FALLBACK

        z, u1, u2, uc = self.x
        y_hat = (ocv_from_soc(z * 100.0, self._table)
                 - i * r0 - u1 - u2 - uc)
        innov = v - y_hat

        # H = [dOCV/dz, -1, -1, -1]；slope 是 mV/%，/10 折成 V per unit z。
        h0 = slope_at(z * 100.0, self._table) / 10.0
        H = (h0, -1.0, -1.0, -1.0)

        P = self.P
        PHt = [sum(P[r][c] * H[c] for c in range(4)) for r in range(4)]

        # 门限软降权：R_eff = R*(|innov|/gate)^2（降 K，不丢点、不照单全收）。
        r_eff = r_v
        if abs(innov) > gate:
            ratio = abs(innov) / gate
            r_eff = r_v * ratio * ratio
            if not math.isfinite(r_eff) or r_eff <= 0.0:
                r_eff = _R_FALLBACK

        S = sum(H[c] * PHt[c] for c in range(4)) + r_eff
        if not math.isfinite(S) or S <= 0.0:
            return False, innov

        K = [PHt[r] / S for r in range(4)]
        self.x = [self.x[r] + K[r] * innov for r in range(4)]

        # Joseph: P = (I-KH) P (I-KH)^T + K R K^T
        M = [[(1.0 if r == c else 0.0) - K[r] * H[c]
              for c in range(4)] for r in range(4)]
        MP = _mmul(M, P)
        MPMt = _mmul(MP, _trans(M))
        for r in range(4):
            for c in range(4):
                MPMt[r][c] += r_eff * K[r] * K[c]
        self.P = MPMt
        return True, innov

    # -- 数值卫生 / 自愈 --------------------------------------------

    def _sanitize(self) -> bool:
        """检查 x/P 全有限、P 对称性；再强制对称化 + 对角限幅 + 状态限幅。"""
        for val in self.x:
            if not math.isfinite(val):
                return False
        P = self.P
        for r in range(4):
            for c in range(4):
                if not math.isfinite(P[r][c]):
                    return False
        scale = max(1.0, max(abs(P[k][k]) for k in range(4)))
        for r in range(4):
            for c in range(4):
                if abs(P[r][c] - P[c][r]) > _ASYM_TOL * scale + 1e-9:
                    return False            # P 非对称到不可用
        S = [[0.5 * (P[r][c] + P[c][r]) for c in range(4)] for r in range(4)]
        for r in range(4):
            S[r][r] = max(_P_MIN, min(_P_MAX, S[r][r]))
        self.P = S
        z, u1, u2, uc = self.x
        self.x = [max(_Z_LO, min(_Z_HI, z)),
                  max(-_U_LIM, min(_U_LIM, u1)),
                  max(-_U_LIM, min(_U_LIM, u2)),
                  max(-_U_LIM, min(_U_LIM, uc))]
        return True

    def _recover(self, v_cell_v) -> None:
        """发散自愈：状态重置到 soc_from_ocv(V_cell)/100、U*=0、P 恢复初值。"""
        z0 = 0.5
        try:
            vv = float(v_cell_v)
            if math.isfinite(vv) and _V_LO <= vv <= _V_HI:
                z0 = soc_from_ocv(vv, self._table) / 100.0
        except (TypeError, ValueError, IndexError):
            pass
        z0 = _fin(z0, 0.5)
        self.x = [max(_Z_LO, min(_Z_HI, z0)), 0.0, 0.0, 0.0]
        self.P = _p0()
        self._bad_count = 0
        self._rest_count = 0       # 自愈也是重置，静置计数一并清零

    def _numeric_recover(self, v_cell_v, i: float, dt: float) -> EkfStep:
        """数值失效：本轮 valid=False, recovered=True 并自愈。"""
        self._recover(v_cell_v)
        return self._finish(v_cell_v, i, dt, 0.0, False, True)

    def _invalid_finish(self, v_cell_v, i: float, dt: float) -> EkfStep:
        """无效轮（非有限输入 / 电压越界）：累计连续无效轮数，满 N 强制自愈。"""
        self._bad_count += 1
        recovered = False
        if self._bad_count >= _N_INVALID_RECOVER:
            self._recover(v_cell_v)
            recovered = True
            self._bad_count = 0
        return self._finish(v_cell_v, i, dt, 0.0, False, recovered)

    # -- 静置软锚定 / 统一收尾 -----------------------------------------

    def _finish(self, v_cell_v, i: float, dt: float, innov: float,
                valid: bool, recovered: bool) -> EkfStep:
        """统一收尾：维护静置计数、按需做静置软锚定，再封装 :class:`EkfStep`。

        语义对齐 ``soc_est.SocEstimator._rest_anchor``：平台段 OCV 斜率小、K_z
        被 P 压得很小，光靠卡尔曼增益向 OCV 反查值收敛极慢；静置时 OCV 最可信，
        故在**标准 EKF 更新之后**对 ``x[0]`` 做一次带封顶的小步回拉。

        ⛔ 锚定**不清空 P**（保住带载恢复后的滤波记忆），只把 ``P[0][0]`` 轻微
        抬高，免得 K_z 塌死后再也学不动。锚定增益受新息门限保护，坏点不会把
        电压直接焊进 SOC。
        """
        try:
            v = float(v_cell_v)
        except (TypeError, ValueError):
            v = float("nan")

        gate = _fin(self.params.innov_gate_v, _GATE_FALLBACK)
        if gate <= 0.0:
            gate = _GATE_FALLBACK
        self._anchored = False

        i_fin = math.isfinite(i)
        dt_fin = math.isfinite(dt)

        # 静置计数：带载（或电流非法）立刻清零；无效轮 / dt<=0 不累计；否则 +1。
        if not i_fin or abs(i) >= _REST_I_MAX:
            self._rest_count = 0
        elif not valid or not dt_fin or dt <= 0.0:
            pass                              # 不累计、不清零
        else:
            self._rest_count = min(self._rest_count + 1, _REST_MAX_COUNT)

        # 静置软锚定（仅：有效 + 静置 + dt>0 + 连续 >=3 轮 + 量测在量程内）。
        if (valid and i_fin and abs(i) < _REST_I_MAX
                and dt_fin and dt > 0.0
                and self._rest_count >= _REST_MIN_ROUNDS
                and math.isfinite(v) and _V_LO <= v <= _V_HI):
            z_ocv = soc_from_ocv(v, self._table) / 100.0
            delta = max(-_REST_CAP, min(_REST_CAP, z_ocv - self.x[0]))
            # 受新息门限保护：|innov| 越大（量测越可疑）锚定越弱，用与量测
            # 更新 R 相同的软降权因子，避免把坏点电压直接焊进 SOC。
            a = abs(innov)
            factor = 1.0 if a <= gate else (gate / a) ** 2
            z_new = self.x[0] + _REST_GAIN * delta * factor
            self.x[0] = max(_Z_LO, min(_Z_HI, z_new))
            # 静置极化最小、OCV 最可信：顺带把 U1/U2/Uc 按 (1 - 增益*factor)
            # 松弛向 0。EKF 观测更新会把早期瞬态残留的 U_sum 一直顶住，使
            # y_hat 与 OCV 锚定目标互相拉扯、z 卡在一个偏置不动点（平台段
            # 实测残差 ~0.65% SOC）。松弛后 y_hat ≡ ocv(z*100)，观测更新与
            # 锚定一致，z 精确收敛到查表值。松弛量与锚定同受新息门限降权，
            # 坏点不会借松弛扰动极化状态。不改 P。
            relax = 1.0 - _REST_U_RELAX * factor
            for j in (1, 2, 3):
                self.x[j] *= relax
            # ⛔ 保留 P，不 reset。仅在锚定「刚启用」那一轮把 P[0][0] 轻微
            # 抬高一次（×1.05，受对角上限约束），免得 K_z 塌死后再也学不动；
            # 每轮都乘会复利放大 P、反而把坏点量测的 K 顶大（实测单轮焊入
            # 从 ~5.5% 恶化到 ~10.5%），故严格只在激活轮抬一次。
            if self._rest_count == _REST_MIN_ROUNDS:
                bumped = _fin(self.P[0][0] * _REST_P_BUMP, _P_MIN)
                self.P[0][0] = max(_P_MIN, min(_P_MAX, bumped))
            self._anchored = True

        return self._make_step(innov, valid, recovered, i)

    # -- 结果封装 -----------------------------------------------------

    def _make_step(self, innov: float, valid: bool, recovered: bool,
                   i: float) -> EkfStep:
        z, u1, u2, uc = self.x
        i_s = i if math.isfinite(i) else 0.0
        r0 = _fin(self.params.r0_ohm, _R0_FALLBACK)
        offset = i_s * r0 + u1 + u2 + uc
        return EkfStep(
            z=z, u1=u1, u2=u2, uc=uc,
            ocv_offset_v=offset,
            innovation_v=innov if math.isfinite(innov) else 0.0,
            valid=valid, recovered=recovered,
        )
