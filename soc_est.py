"""E74（125 串 / 12 Ah 高电压 NCM）SOC 估算核心：纯 Python，不 import Qt。

为什么用这套算法
----------------
E74 车上能拿到的信号只有三样：**125 节端电压**、**模组温度**、**母线电流读数**
（报文尾部的 ``V=<x>,I=<y>``）。没有能量计量芯片、也没有实验室级库仑计，所以
唯一能在车规成本下长期收敛的组合是「**安时积分做骨架 + 端电压做回拉**」：

* 安时积分短期极准，长期会因容量衰减、电流零漂而单向跑偏；
* 开路电压（OCV）查表短期会被极化/回弹带偏，长期却是**唯一没有累积误差**的
  绝对参照。

两者按「此刻电压到底含多少 SOC 信息」加权融合 —— 这是 Plett 2004 那组 EKF
三篇（*J. Power Sources* 134: 252 / 262 / 277）的核心结论，也是 Xiong 2018
*IEEE Access* 综述对车载 BMS 的首选；国内工程落地可对照陈息坤 2015（电工技术
学报，实测 ~3%）与林成涛 2004 的 OCV-SOC 标定法。

**不上完整 EKF**：E74 的采样周期实测抖动在 0.9~2.5 s，电流读数还带零漂，EKF
的协方差在这种输入上会发散；改成「固定增益 + 可信度加权」在数据质量差时反而
更稳，而且整条链路可以脱离 Qt 单测。这是有意的取舍，不是简化。

关键数字从哪来
--------------
常量全部来自 E74 实测 Excel（``12Ah放电曲线图.xlsx`` → ``Detail_222_1_6`` 工步 5：
1C 恒流放电 12 A，4.4161 V → 2.9998 V，累计 **12.040983 Ah**）：

* ``Q_NOM_AH``：工步 5 的累计容量。**不是**铭牌 12 Ah 的圆整值，更**不是**
  ``Statis`` 表里那个 15 Ah —— 后者是几轮循环的累加，拿它当容量会把 SOC 算低
  约 25%。
* ``R0_MOHM``：1C 放电 IR 压降 12 A × 6.8 mΩ ≈ 82 mV，与「OCV = 带载电压 +
  0.082 V」在两端都能对上（4.4957 vs 4.498、3.0811 vs 3.082，误差 < 1 mV），
  说明这组数自洽。
* ``OCV_TABLE``：工步 5 的 62 个点按 ``OCV = V + 82 mV`` 平移后插值到 26 点。
  低 SOC 段（0~6%）斜率 50~185 mV/%，高 SOC 段（95~100%）20~42 mV/%，中间
  10~30% 是平台（4~10 mV/%）—— 典型 NCM 曲线。平台段电压几乎不含 SOC 信息，
  所以必须 κ 加权：硬拿平台电压纠偏只会把误差拉大。

清洗口径（用户明确要求）
------------------------
1. 判坏：设备打 ``X`` 的、超量程的、缺失的，一律算坏；
2. **坏点的值用健康电芯的平均值回填**，总压按「平均值 × 串数」估算（E74 的
   ``V=`` 传感器已坏，恒为 0.00，没有别的选择）；
3. 回填之后还要看 **均值与中位数的差**：差得太多说明还有断线没剔干净，置
   ``RESIDUAL`` 标记，此时**不采信**电压回拉（只用安时积分）。
"""

from __future__ import annotations

import math
import statistics

# ---------------------------------------------------------------- 实测常量

#: 1C 放电实测容量（Ah）。见模块注释：工步 5 累计值，不是铭牌值。
Q_NOM_AH = 12.041

#: E74 是 5 模组 × 25 电芯 = 125 串。
N_MODULES = 5
CELLS_PER_MODULE = 25
N_CELLS = N_MODULES * CELLS_PER_MODULE      # 125

#: 单节直流内阻（mΩ），由 1C 放电 IR 压降反推。
R0_MOHM = 6.8

#: 电芯量程（mV）。NCM：放空 3.00 V、满充 4.50 V；这里留 ±200/±100 mV 余量。
CELL_MIN_MV = 2500
CELL_MAX_MV = 4600

#: OCV-SOC 表 ``(SOC%, OCV V)``，严格单调递增。由工步 5 平移 +82 mV 而来。
OCV_TABLE: tuple[tuple[float, float], ...] = (
    (0, 3.081800),
    (0.3654, 3.210200),
    (2.026, 3.455400),
    (3.6867, 3.550400),
    (5.3473, 3.598800),
    (7.0078, 3.627000),
    (8.6684, 3.646200),
    (10.329, 3.661100),
    (11.9895, 3.673300),
    (13.6501, 3.683600),
    (15.3107, 3.692500),
    (16.9713, 3.700300),
    (18.6319, 3.707200),
    (20.2925, 3.714000),
    (21.953, 3.720700),
    (23.6136, 3.727700),
    (25.2742, 3.734900),
    (26.9347, 3.742500),
    (28.5953, 3.750900),
    (30.2559, 3.759500),
    (31.9165, 3.768700),
    (33.5771, 3.778300),
    (35.2376, 3.788700),
    (36.8982, 3.799300),
    (38.5588, 3.810600),
    (40.2194, 3.822400),
    (41.88, 3.834600),
    (43.5405, 3.847500),
    (45.2011, 3.861100),
    (46.8617, 3.875300),
    (48.5223, 3.890100),
    (50.1829, 3.905500),
    (51.8435, 3.921600),
    (53.504, 3.938700),
    (55.1646, 3.956300),
    (56.8252, 3.974700),
    (58.4858, 3.993700),
    (60.1464, 4.013500),
    (61.807, 4.033900),
    (63.4676, 4.054700),
    (65.1282, 4.076000),
    (66.7888, 4.097600),
    (68.4494, 4.119500),
    (70.11, 4.141500),
    (71.7706, 4.163700),
    (73.4312, 4.185800),
    (75.0918, 4.207800),
    (76.7524, 4.229400),
    (78.413, 4.250400),
    (80.0735, 4.270400),
    (81.7341, 4.289600),
    (83.3947, 4.307500),
    (85.0552, 4.324200),
    (86.7158, 4.339700),
    (88.3763, 4.354200),
    (90.0369, 4.367500),
    (91.6974, 4.379700),
    (93.3579, 4.391000),
    (95.0185, 4.401700),
    (96.679, 4.412600),
    (98.3395, 4.427800),
    (100, 4.498100),
)

# ---------------------------------------------------------------- 清洗常量

#: 稳健离群筛：|v - 中位数| > max(MAD_K × 1.4826 × MAD, MAD_FLOOR_MV, IQR_K × IQR)
#: 判坏。用 MAD 而不是标准差 —— 标准差本身就被离群点污染，断线越多门槛越松。
MAD_K = 5.0
MAD_FLOOR_MV = 20.0
#: 四分位距下限：**必须有**。MAD 在"少数派整体偏移"时会塌成 0（例：40 节读
#: 3300、85 节读 3200，中位数仍是 3200，MAD=0），此时只剩 20 mV 的死门槛，
#: 那一整个模组会被当成坏点全部回填掉 —— 而它是真实的模组间不均衡，不是断线。
#: 用 IQR 把门槛撑到分布自身的宽度上，才不会把"不均衡"误判成"坏点"。
#: ⚠️ 但 IQR 只在少数派 >25% 时有效：`quantiles(n=4)` 的 q3 落在多数派里时
#: IQR 还是 0 ——「一个整模组」恰好是 125 的 20%，正好踩空。所以真正兜住整模组
#: 偏移的是下面的**连续段判据**，IQR 只是第一道。
IQR_K = 3.0

#: 「连续同向离群」多长才算整模组偏移（而不是几节断线）。
#: 断线在物理上是**孤立的 1~2 节**（接插件松、采样线断），而模组级不均衡一定是
#: **一整段连续的电芯同向偏移**。用长度区分，比任何分布统计都可靠 —— 分布统计
#: 在"少数派 <25%"时会塌，而"连续 12 节以上同向偏"这件事跟占比无关。
#: 取半个模组（25 节 → 12）当下限：既能抓住整模组，也不会把"连续三节都坏了"
#: 这种极端巧合误判成模组故障。
MODULE_RUN_RATIO = 0.5

#: 均值与中位数的容许差（mV）。**这是实测标定出来的，不是拍的**：把 E74 的
#: 118 轮真机日志全部跑一遍清洗，差值中位数 0.14 mV、P99 0.32 mV、最大
#: 0.63 mV。而单节 3200 mV 的电芯若读成 5000 mV，均值会被拉偏
#: (5000-3200)/125 ≈ +14.4 mV。所以 8 mV 是「约 13 倍噪声上限、又低于单节
#: 失准一半」的位置：既能抓到残留断线，又不会被正常的不一致性误触发。
MEAN_MED_MAX_MV = 8.0

#: 单体极值孤立度门限（mV）。**用户点名"注意排除有误数据"**：清洗已经剔了
#: 超量程 / X / 缺失 / 孤立离群，但极值位置仍可能残留一个"量程内却明显孤立"
#: 的坏点。若最低值比次低值还低超过这个数（或最高值比次高值高超过），就判定
#: 它是残余坏点，每侧最多再剔 2 个。
#: 标定依据：E74 真机 118 轮 |均值−中位数| 最大 1.46 mV、单体一致性很好；
#: 30 mV 约 20 倍噪声，又远小于"整模组偏移"那种成片偏移（次低值同样是偏移值，
#: 相邻差很小，不会被误杀）。
EXTREME_GAP_MV = 30.0
EXTREME_DROP_MAX = 2

#: 健康电芯占比低于这个值就认为整包数据不可用（不更新 SOC，只报错）。
MIN_HEALTHY_RATIO = 0.6

# ---------------------------------------------------------------- 弱芯 vs 断线（卡方）
# 用户 2026-11-12：「弱芯可以通过卡方计算区分」。
#
# 判据：同一轮里，某节相对**中位数**的偏差 Δ 构造 χ² = (Δ/σ0)²，1 自由度、
# 显著性 α = 0.001 的临界值 χ²_crit = 10.828 → Δ_crit = 3.29·σ0。
# 超过 Δ_crit 才认定"物理上不可能是一节电芯"（= 断线/坏点，回填）；
# 在 Δ_crit 以内的一律按**真实弱芯**保留读值。
#
# ⛔ 为什么 σ0 **不能用整包实测离散**（这条是本设计的关键）：
#    E74 真机 101 轮实测，正常节的逐轮残差标准差只有 0~0.3 mV，整包 σ 约 1~3 mV。
#    若拿 σ 当 σ0，一个 10 mV 的偏差就 χ²≈11 已经"显著"—— 卡方这时回答的只是
#    「偏离是否可测」，不是「是不是一节电芯」。真弱芯 #112 恒 −32 mV 会 χ²≈114
#    照样被判死。把 σ0 换成**物理允许的单体不均衡**，卡方问的才变成
#    「这个偏差大到不可能是电芯了吗」—— 这才是要回答的问题。
#
# 标定（E74 101 轮真实日志，残差相对当轮中位数）：
#   · 弱芯：#112 = −32.0 mV（逐轮 sd **0.00**）、#73 = −27.0 mV（sd 0.00）；
#   · 断线/坏通道：#97 = −1713、#29 = −1049、#98 = +742、#30 = +884、#99 = +1027 mV；
#   两族之间 **23 倍**的空档（32 → 742），任何 σ0 ∈ [15, 100] mV 都能干净切开。
#   取 30 mV（12 Ah LFP 静置下合理的单体不均衡量级）→ Δ_crit ≈ 98.7 mV：
#   弱芯 χ² = (32/30)² = 1.14（远小于 10.83，保留），
#   断线 χ² ≥ (742/30)² = 611（远超临界，回填）。
CHI2_CRIT_1DOF_0001 = 10.828
CELL_IMBALANCE_TOL_MV = 30.0

# ---------------------------------------------------------------- 工况判别
# 用户 2026-11-11 提问「你有办法判断电池箱是在充电还是放电吗，有时候并没有
# 接负载」—— 真机 98 轮报文里接触器**全部全断**、`I=` 全是 -250 A 传感器
# 轨值，闭合轮数 0。只靠电流符号的话，高亮逻辑在真机上一次都不会生效。
# 所以改成多信号分层：接触器状态（物理硬约束） > 有效电流符号 > 电压趋势。

#: 电压趋势窗口（s）与窗口内最小变化量（mV）。
#: 静置实测 120 s 窗口极差 ≤1.25 mV → 3 mV 约 2.4 倍噪声；
#: 1C 放电 60 s 约 1.67% SOC，平台段折合 7~17 mV，能检出。
MODE_V_WINDOW_S = 60.0
MODE_V_DELTA_MV = 3.0
#: 判定为放电/充电后的最短保持时间（s），避免阈值附近来回跳
MODE_HOLD_S = 30.0
#: 电流达到这个绝对值才认为"显著"（与下面的 REST_I_MAX_A=1.2 同口径；
#: ⛔ 这里写死而不是引用它 —— 那个常量定义在后面的融合常量区）
MODE_I_MIN_A = 1.2

MODE_LABELS = {
    "discharge": "放电",
    "charge": "充电",
    "idle": "静置",
    "open": "母线断开",
    "unknown": "工况未知",
}


class WorkModeClassifier:
    """判断电池箱此刻是放电 / 充电 / 静置 / 未接负载 / 未知。

    ⛔ 优先级不是随便排的：
      1. ``contactor_open is True`` 是**物理硬约束**（母线断开不可能有功率流），
         比任何电流读数都可靠，也能天然挡住「满放后静置回弹」被误判成充电；
      2. 电流有效且显著时按符号，响应最快；
      3. 电流缺字段 / 轨值 / 太小时，用**单体平均电压的长窗口趋势**兜底
         （这路完全不依赖电流传感器）；
      4. 其余按静置处理，并带 ``MODE_HOLD_S`` 滞回。
    """

    def __init__(self):
        self.mode = "unknown"
        self._hist: list[tuple[float, float]] = []
        self._hold_until = 0.0

    def reset(self) -> None:
        self.mode = "unknown"
        self._hist.clear()
        self._hold_until = 0.0

    def _trend(self, ts, mean_mv: float) -> str:
        """返回 ``"down"`` / ``"up"`` / ``"flat"``。"""
        if ts is None or not math.isfinite(float(ts)):
            return "flat"
        now = float(ts)
        self._hist.append((now, float(mean_mv)))
        cut = now - MODE_V_WINDOW_S
        while self._hist and self._hist[0][0] < cut:
            self._hist.pop(0)
        if len(self._hist) < 2:
            return "flat"
        span = self._hist[-1][0] - self._hist[0][0]
        if span < MODE_V_WINDOW_S * 0.5:
            return "flat"                  # 窗口还不够长，不急判
        delta = self._hist[-1][1] - self._hist[0][1]
        if delta <= -MODE_V_DELTA_MV:
            return "down"
        if delta >= MODE_V_DELTA_MV:
            return "up"
        return "flat"

    def _set(self, new: str, ts, force: bool = False) -> str:
        if not force and new != self.mode:
            # 想从"在跑"退回静置/未知时，保持期内先别切，避免读数抖动
            if (ts is not None and float(ts) < self._hold_until
                    and new in ("idle", "open", "unknown")):
                return self.mode
        # ⚠️ 首次判定成放电/充电时**也要**写 _hold_until：
        #    早期版本只在 ``new == self.mode`` 分支里写，结果第一次切到
        #    discharge 时保持期还是 0，下一轮立刻退回 idle（M8 抓到）。
        self.mode = new
        if new in ("discharge", "charge") and ts is not None:
            self._hold_until = float(ts) + MODE_HOLD_S
        return new

    def classify(self, ts, mean_mv: float, i_eff: float, i_valid: bool,
                 contactor_open, bms_ok: bool) -> str:
        """喂一轮，返回工况字符串。"""
        if mean_mv <= 0:
            return self._set("unknown", ts, force=True)
        # ⚠️ 接触器放在 BMS 检查**之前**：真机 98 轮报文里既有 `BMS Error`
        #    又全是 AIR+/AIR-/PRE 全 0。BMS 报错不代表母线没断开，而"母线
        #    已断"是硬件级事实，对用户判断"有没有接负载"更有用。
        if contactor_open is True:
            return self._set("open", ts, force=True)
        if not bms_ok:
            return self._set("unknown", ts, force=True)
        if i_valid and abs(i_eff) > MODE_I_MIN_A:
            return self._set("discharge" if i_eff > 0 else "charge", ts)
        trend = self._trend(ts, mean_mv)
        if trend == "down":
            return self._set("discharge", ts)
        if trend == "up":
            return self._set("charge", ts)
        return self._set("idle", ts)


# ---------------------------------------------------------------- 融合常量

#: 每轮固定增益的电压回拉系数（1 Hz 采样下时间常数约 50 s）。
K_VOLT = 0.02

#: κ 的下限/上限：平台段给 0.1（几乎不信电压），驼峰段给 1.0（完全信）。
KAPPA_LO = 0.1
KAPPA_HI = 1.0

#: κ 的归一化尺度：OCV 斜率 10 mV/% 视为"完全可信"。
KAPPA_SLOPE_REF = 10.0

#: 状态相关 κ 调制（乘到 ``K_VOLT`` 上）——
#: · 接近静置（|I| ≤ REST_I_MAX_A）：电压最可信 → 提速回拉；
#: · 大电流放电/充电：OCV 估计受极化污染 → 更信安时积分，调低 κ；
#: · 母线断开（open）：冻结快回拉（见 ``update`` 内 open 分支），只靠静置锚定慢校。
#: 系数都贴着 119 轮 + 499 轮会话库标定的 ``K_VOLT=0.02`` 做小幅调制，
#: 不推翻已验证的跟踪精度（C3/C16 仍须通过）。
KAPPA_IDLE_BOOST = 1.3          # 接近静置时 κ 倍率
KAPPA_HI_I_A = 100.0            # 电流超过此值（A）按最低 κ
KAPPA_HI_CURRENT = 0.5          # 大电流放电/充电时的 κ 倍率（更信积分）

#: 静置锚定的增益与单次幅度上限（%）。
REST_GAIN = 0.3
REST_CAP_PCT = 15.0

#: 静置判据：电流足够小、电压足够稳、时间足够长。
REST_I_MAX_A = 1.2
REST_WINDOW_S = 120.0
#: 「稳」的门槛（mV，窗口内极差）。**按真机噪声标定**：499 轮实测数据里，
#: 120 s 窗口内均值电压极差的中位数 0.29 mV、P90 1.17 mV、最大 1.25 mV。
#: 取 1.0 只会放行 84% 的安静窗口（白白丢掉六分之一机会），取 2.0 既覆盖了
#: 实测噪声上限、又远小于真正在放电时的变化（1C 下 2 min 就是几十上百 mV），
#: 不会把"还在动"误判成"静止"。
REST_STABLE_MV = 2.0

#: 温度参与判定的区间（静置锚定只在"没有极端温度极化"时才可信）。
#: 不从 15 °C 起：实测这包常年 41~47 °C，按 15~35 卡会让锚定**永不触发**，
#: 等于白写。NCM 的 OCV 温度系数只有 ~0.2 mV/°C，25 °C 跨度折合 SOC 误差
#: ~0.5%，远小于锚定本身 ±15% 的封顶，所以放宽到 0~60 °C 只在两端设防。
REST_T_MIN_C = 0.0
REST_T_MAX_C = 60.0

#: 单次积分的时间步长上限（s）。暂停/回放/断线重连后 dt 可能是几分钟，
#: 直接积分会把 SOC 一次拉爆，超过上限就当成"没有这段时间"。
DT_MAX_S = 10.0

#: 中断超过这么久（s）就重新用 OCV 锚定 SOC。
#: 为什么需要它：两次上电之间电池可能被充过/换过，那一整段安时积分是**缺的**。
#: 只靠每轮 2% 的慢回拉要几百轮才追得回来，中间一直显示错的数。停了十分钟以上
#: 又处于静置，端电压就是平衡后的开路电压 —— 那才是唯一可信的绝对基准，直接
#: 重新锚定比慢慢拖过去诚实得多。真车上采样是 1~2 s 一轮，永远碰不到这条。
REINIT_GAP_S = 600.0

#: 电流零漂的学习速率（接触器全断时真实电流必为 0，此时读数就是零漂）。
BIAS_GAIN = 0.25
BIAS_LIMIT_A = 5.0
#: 只接受这个范围内的读数去学零漂。零漂是个"零点附近的偏置"，不可能有几十
#: 上百安 —— 真机数据里就存在 -250 A 这种传感器轨值（见 I_LIMIT_A），拿它去
#: 学零漂会把整条链路毒死。
BIAS_SAMPLE_MAX_A = 20.0

#: 电流读数的物理上限（A）。超过就当这一帧没有电流。
#: E74 是 12 Ah 的包，FSAE 规则把牵引功率限在 80 kW，按 450 V 算约 178 A，
#: 取 200 A 留余量。**这不是保守，是必需**：真机会话库里 40% 的电流样本是
#: -250 A 附近的一段平滑轨值（不是噪声，是传感器另一种状态下的饱和值），
#: 不挡住它，几帧就能把 SOC 推出十几个百分点。
I_LIMIT_A = 200.0

#: 电流方向：+1 = 设备读数为正代表**放电**（开车时 SOC 下降，符合直觉）。
#: 若现场发现方向反了（开车 SOC 反涨），把这个改成 -1 即可，不用动别的。
I_SIGN = 1.0

#: 充电库仑效率（< 1）。充电时存进电池的比安时积分的少（副反应 / 自放电 /
#: 被动均衡偷走的电流霍尔看不到），所以充电态 SOC 增量要按此系数打折；放电视
#: 为可逆（= 1），只有充电打折。NCM 取 0.99，具体车可在 ``SocCfg`` / 内置表里覆盖。
#: 旧配置缺这个字段时回落到它，不会崩。
CHARGE_EFF_DEFAULT = 0.99

# 温度-OCV 补偿（BMS 实测每模组 8 路 NTC，已通到 SocEstimator.update 的 temps_c）。
# coef=0 即关闭，完全保留原行为；NCM 典型值约 -0.5 mV/°C（温度越高 OCV 越低）。
OCV_TEMP_COEF_DEFAULT = 0.0
OCV_TEMP_REF_C = 25.0

# ---------------------------------------------------------------- 数据结构


class SocCfg:
    """一台车的 SOC 参数。换车只换这个对象，估算器本身不用改。"""

    __slots__ = ("number", "q_ah", "n_cells", "cells_per_module", "r0_mohm",
                 "ocv_table", "i_sign", "chemistry", "charge_eff",
                 "ocv_temp_coef_mv_per_c", "ocv_temp_ref_c",
                 "imbalance_tol_mv")

    def __init__(self, number: str = "", q_ah: float = Q_NOM_AH,
                 n_cells: int = N_CELLS, cells_per_module: int = CELLS_PER_MODULE,
                 r0_mohm: float = R0_MOHM,
                 ocv_table=OCV_TABLE, i_sign: float = I_SIGN,
                 chemistry: str = "NCM",
                 charge_eff: float = CHARGE_EFF_DEFAULT,
                 ocv_temp_coef_mv_per_c: float = OCV_TEMP_COEF_DEFAULT,
                 ocv_temp_ref_c: float = OCV_TEMP_REF_C,
                 imbalance_tol_mv: float = CELL_IMBALANCE_TOL_MV):
        self.number = number
        self.q_ah = float(q_ah)
        self.n_cells = int(n_cells)
        self.cells_per_module = int(cells_per_module) or 1
        self.r0_mohm = float(r0_mohm)
        self.ocv_table = tuple((float(s), float(v)) for s, v in ocv_table)
        self.i_sign = float(i_sign)
        self.chemistry = chemistry
        # 充电库仑效率；落在 (0, 1] 才合理，越界回落到默认，别让一条坏值把
        # 充电态 SOC 推反方向或推爆。
        ce = float(charge_eff)
        self.charge_eff = ce if 0.0 < ce <= 1.0 else CHARGE_EFF_DEFAULT
        # 温度-OCV 补偿系数（mV/°C）与参考温度（°C）。coef 可为任意实数，
        # 0 = 关闭补偿；NCM 典型约 -0.5（温度越高 OCV 越低）。不做 (0,1] 限制。
        self.ocv_temp_coef_mv_per_c = float(ocv_temp_coef_mv_per_c)
        self.ocv_temp_ref_c = float(ocv_temp_ref_c)
        # 单体允许不均衡 σ0（mV），卡方判"弱芯 vs 断线"的尺度（见
        # CELL_IMBALANCE_TOL_MV）。必须 > 0，坏值回落默认。
        it = float(imbalance_tol_mv)
        self.imbalance_tol_mv = it if it > 0 else CELL_IMBALANCE_TOL_MV

    def to_dict(self) -> dict:
        return {"number": self.number, "q_ah": self.q_ah, "n_cells": self.n_cells,
                "cells_per_module": self.cells_per_module, "r0_mohm": self.r0_mohm,
                "ocv_table": [list(p) for p in self.ocv_table],
                "i_sign": self.i_sign, "chemistry": self.chemistry,
                "charge_eff": self.charge_eff,
                "ocv_temp_coef_mv_per_c": self.ocv_temp_coef_mv_per_c,
                "ocv_temp_ref_c": self.ocv_temp_ref_c,
                "imbalance_tol_mv": self.imbalance_tol_mv}

    @classmethod
    def from_dict(cls, data: dict) -> "SocCfg | None":
        """从配置字典还原；坏配置返回 None（**不抛异常**，别让一条坏配置搞挂
        接收链路）。表不合法（少于 2 点 / 不单调）也当坏配置。"""
        if not isinstance(data, dict):
            return None
        try:
            table = [(float(p[0]), float(p[1])) for p in data.get("ocv_table") or ()]
            cfg = cls(str(data.get("number") or ""),
                      float(data.get("q_ah", Q_NOM_AH)),
                      int(data.get("n_cells", N_CELLS)),
                      int(data.get("cells_per_module", CELLS_PER_MODULE)),
                      float(data.get("r0_mohm", R0_MOHM)),
                      table or OCV_TABLE,
                      float(data.get("i_sign", I_SIGN)),
                      str(data.get("chemistry") or "NCM"),
                      float(data.get("charge_eff", CHARGE_EFF_DEFAULT)),
                      float(data.get("ocv_temp_coef_mv_per_c", OCV_TEMP_COEF_DEFAULT)),
                      float(data.get("ocv_temp_ref_c", OCV_TEMP_REF_C)),
                      float(data.get("imbalance_tol_mv",
                                     CELL_IMBALANCE_TOL_MV)))
        except (TypeError, ValueError, IndexError):
            return None
        if cfg.q_ah <= 0 or cfg.n_cells <= 0 or not table_ok(cfg.ocv_table):
            return None
        return cfg

    def __repr__(self) -> str:
        return (f"SocCfg(number={self.number!r}, q_ah={self.q_ah}, "
                f"n_cells={self.n_cells}, chem={self.chemistry})")


def table_ok(table) -> bool:
    """OCV 表是否可用：至少 2 点、SOC 与 OCV 都严格单调递增。"""
    if len(table) < 2:
        return False
    for i in range(1, len(table)):
        if table[i][0] <= table[i - 1][0] or table[i][1] <= table[i - 1][1]:
            return False
    return True


class CleanResult:
    """一轮电压清洗的结果。"""

    __slots__ = ("cells", "bad_idx", "mean_mv", "median_mv", "gap_mv",
                 "residual", "module_offset", "ok", "n_raw", "n_unusable",
                 "min_mv", "max_mv", "n_extreme_dropped", "weak_idx")

    def __init__(self, cells, bad_idx, mean_mv, median_mv, gap_mv,
                 residual, ok, n_raw, n_unusable, module_offset=False,
                 min_mv=0.0, max_mv=0.0, n_extreme_dropped=0, weak_idx=None):
        self.cells = cells            # 回填后的整串（长度 = n_cells）
        self.bad_idx = bad_idx        # 被判坏/回填的序号（1 基）
        self.mean_mv = mean_mv        # 健康电芯均值
        self.median_mv = median_mv    # 健康电芯中位数
        self.gap_mv = gap_mv          # |均值 - 中位数|
        # gap 超限。含义是"均值被少数几节拽走了"，说明还有没剔干净的断线/坏点。
        # 这是**告知性**的（界面加 ±），不阻断估算。
        self.residual = residual
        # 存在"连续一整段同向偏移"——模组级不均衡，不是断线。这些电芯的读数是
        # **真实值，不回填**（回填会把整模组那部分总压凭空抹掉），但要提示。
        self.module_offset = module_offset
        self.ok = ok                  # 可用（健康电芯足够多）
        self.n_raw = n_raw            # 实际收到的采样点个数
        self.n_unusable = n_unusable    # 缺失/超量程（不可用）的个数
        # ⛔ 下面两个是 **清洗后真实读值** 的最低/最高（不含回填值）。
        #    min/max SOC 必须拿它们算：回填值就是均值，用回填后的整串取极值
        #    会把两端抹平、失去"最弱/最强电芯"的物理意义。
        self.min_mv = min_mv
        self.max_mv = max_mv
        self.n_extreme_dropped = n_extreme_dropped  # 极值侧又剔掉的残余坏点数
        # 被判定为**真实弱芯**（超出统计离群限、但偏差在物理允许不均衡内）的序号
        # （1 基）。这些节的读值**保留、不回填**，只是标出来供界面/诊断区分
        # "电池真的弱" 与 "采样线断了"。
        self.weak_idx = list(weak_idx or ())

    @property
    def pack_mv(self) -> float:
        """整包电压（mV）。

        按用户要求用平均值估算：坏点全部回填成健康均值后，整串之和**恒等于**
        ``健康均值 × 串数`` —— 所以这里直接乘，避免浮点累加噪声。
        """
        return self.mean_mv * len(self.cells) if self.cells else 0.0

    def __repr__(self) -> str:
        extra = ", module_offset" if self.module_offset else ""
        return (f"CleanResult(mean={self.mean_mv:.1f}mV, med={self.median_mv:.1f}, "
                f"gap={self.gap_mv:.2f}, bad={len(self.bad_idx)}, "
                f"ok={self.ok}{extra})")


class SocResult:
    """一轮 SOC 估算结果，UI 只读这个对象。"""

    __slots__ = ("soc", "pack_v", "clean", "i_eff", "i_bias", "temp_c",
                 "flag", "anchored", "initialised", "soc_min", "soc_max",
                 "emphasis", "polarization_mv", "ekf_recovered", "mode")

    def __init__(self, soc, pack_v, clean, i_eff, i_bias, temp_c,
                 flag, anchored, initialised, soc_min=None, soc_max=None,
                 emphasis="", polarization_mv=0.0, ekf_recovered=False,
                 mode="unknown"):
        self.soc = soc                  # 0~100，单位 %
        self.pack_v = pack_v            # 总电压（V）
        self.clean = clean              # CleanResult
        self.i_eff = i_eff              # 去零漂后的电流（A，放电为正）
        self.i_bias = i_bias            # 当前零漂估计（A）
        self.temp_c = temp_c            # 平均温度（°C），未知为 None
        self.flag = flag                # "" / NO_DATA / WEAK / RESIDUAL / MODULE
        self.anchored = anchored        # 本轮是否做了静置锚定
        self.initialised = initialised  # 本轮是否刚从 OCV 冷启动
        # min/max：由清洗后最低/最高**真实**单体电压经 OCV 反演查表得到。
        # 放电看 min（最弱节先到截止），充电看 max（最高节先到截止）。
        self.soc_min = soc_min          # 0~100；clean.ok=False 时为 None
        self.soc_max = soc_max
        self.emphasis = emphasis        # "min" / "max" / ""（静置不高亮）
        self.polarization_mv = polarization_mv  # EKF 估出的 U1+U2+Uc（mV）
        self.ekf_recovered = ekf_recovered      # 本轮是否触发了发散回退
        self.mode = mode                        # discharge/charge/idle/open/unknown

    def __repr__(self) -> str:
        return (f"SocResult(soc={self.soc:.1f}%, pack={self.pack_v:.1f}V, "
                f"flag={self.flag!r})")


# ---------------------------------------------------------------- 查表函数


def ocv_from_soc(soc: float, table=OCV_TABLE) -> float:
    """SOC(%) → OCV(V)，表内线性插值，表外**不外推**直接取端点。"""
    if soc <= table[0][0]:
        return table[0][1]
    if soc >= table[-1][0]:
        return table[-1][1]
    for i in range(1, len(table)):
        x1, y1 = table[i]
        if soc <= x1:
            x0, y0 = table[i - 1]
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (soc - x0) / (x1 - x0)
    return table[-1][1]


def soc_from_ocv(ocv_v: float, table=OCV_TABLE) -> float:
    """OCV(V) → SOC(%)，反查。同样不外推。"""
    if ocv_v <= table[0][1]:
        return table[0][0]
    if ocv_v >= table[-1][1]:
        return table[-1][0]
    for i in range(1, len(table)):
        x1, y1 = table[i]
        if ocv_v <= y1:
            x0, y0 = table[i - 1]
            if y1 == y0:
                return x1
            return x0 + (x1 - x0) * (ocv_v - y0) / (y1 - y0)
    return table[-1][0]


def slope_at(soc: float, table=OCV_TABLE) -> float:
    """该 SOC 处 OCV 曲线斜率（mV/%）。平台段小、两端大，用来定 κ。"""
    x = max(table[0][0], min(table[-1][0], soc))
    for i in range(1, len(table)):
        x1, y1 = table[i]
        if x <= x1:
            x0, y0 = table[i - 1]
            if x1 == x0:
                return 0.0
            return (y1 - y0) * 1000.0 / (x1 - x0)
    return 0.0


def kappa_at(soc: float, table=OCV_TABLE) -> float:
    """电压可信度权重。

    斜率高 = 电压对 SOC 敏感 = 值得信；平台段斜率低 = 电压几乎不含信息 =
    不该信。所以 κ 直接跟着斜率走，再夹到 [KAPPA_LO, KAPPA_HI]。
    """
    k = slope_at(soc, table) / KAPPA_SLOPE_REF
    return max(KAPPA_LO, min(KAPPA_HI, k))


# ---------------------------------------------------------------- 清洗


def _robust_extremes(values) -> tuple[float, float, int]:
    """从**真实读值**里取稳健的最低/最高，返回 ``(min, max, 剔掉几个)``。

    只剔"孤立到离谱"的端点：若最低值比次低值还低超过 ``EXTREME_GAP_MV``，
    说明它多半是漏网的坏点，丢掉再看下一个（每侧最多 ``EXTREME_DROP_MAX`` 个）。
    整模组偏移不会被误杀 —— 那种情况次低值同样是偏移值，相邻差很小。
    """
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return 0.0, 0.0, 0
    if len(ordered) == 1:
        return ordered[0], ordered[0], 0
    lo, hi = 0, len(ordered) - 1
    dropped = 0
    for _ in range(EXTREME_DROP_MAX):
        if hi - lo < 2 or ordered[lo + 1] - ordered[lo] <= EXTREME_GAP_MV:
            break
        lo += 1
        dropped += 1
    for _ in range(EXTREME_DROP_MAX):
        if hi - lo < 2 or ordered[hi] - ordered[hi - 1] <= EXTREME_GAP_MV:
            break
        hi -= 1
        dropped += 1
    return ordered[lo], ordered[hi], dropped


def clean_cells(cells_mv, bad_mask=None, n_cells: int = 0,
                cells_per_module: int = 0,
                imbalance_tol_mv: float = CELL_IMBALANCE_TOL_MV) -> CleanResult:
    """把一轮端电压清洗成可用的整串。

    ``cells_mv``：按序号排好的序列（元素可为 None / 0，表示这一节没读到）。
    ``bad_mask``：同样长度的布尔序列，True = 设备已标记为坏（报文里的 ``X``）。
    ``cells_per_module``：每个模组几节，用来判断"连续同向离群"是不是一整段
    模组偏移（0 = 不知道，按 ``n_cells // 8`` 猜）。
    ``imbalance_tol_mv``：单体**允许不均衡**（σ0，见 ``CELL_IMBALANCE_TOL_MV``），
    卡方判据的尺度；按车型可调（``SocCfg.imbalance_tol_mv``）。

    步骤：量程筛 → MAD/IQR 定离群门槛 → **按连续长度分"孤立断线"与"整段偏移"**
    → 孤立离群再用**卡方**分"真弱芯（保真值）"与"断线（回填）"。
    回填**只影响整串之和与显示**，不参与均值的二次计算（否则均值会被自己稀释，
    越替越偏）。
    """
    values = list(cells_mv or ())
    n = n_cells or len(values)
    if n <= 0:
        return CleanResult([], [], 0.0, 0.0, 0.0, False, False, 0, 0)
    # 长度对不上就按 n 截/补（设备少发一帧时不要整轮作废）
    if len(values) < n:
        values = values + [None] * (n - len(values))
    elif len(values) > n:
        values = values[:n]
    mask = list(bad_mask or ())
    if len(mask) < n:
        mask = mask + [False] * (n - len(mask))

    n_raw = 0
    healthy: list[float] = []
    for i, value in enumerate(values):
        if value is None or mask[i]:
            continue
        try:
            val = float(value)
        except (TypeError, ValueError):
            continue
        if not (CELL_MIN_MV <= val <= CELL_MAX_MV):
            continue                      # 超量程：掉字符/错位最常见的表现
        n_raw += 1
        healthy.append(val)

    if not healthy:
        return CleanResult([0.0] * n, list(range(1, n + 1)), 0.0, 0.0, 0.0,
                           False, False, 0, n)

    median = statistics.median(healthy)
    # 稳健离群：MAD 本身不怕离群点，但"少数派整体偏移"时会塌成 0，所以再压
    # 一个死下限，并用四分位距兜住"分布本身就很宽"的情况（见 IQR_K 的说明）。
    mad = statistics.median([abs(v - median) for v in healthy])
    try:
        _q1, _q2, q3 = statistics.quantiles(healthy, n=4)
        iqr = max(0.0, q3 - _q1)
    except statistics.StatisticsError:
        iqr = 0.0
    limit = max(MAD_K * 1.4826 * mad, MAD_FLOOR_MV, IQR_K * iqr)

    # ---- 把"偏离"分成两类：孤立断线 vs 整段模组偏移 --------------------
    # 逐位置打标：None = 不可用（缺失/超量程/坏标记）；0 = 在限内；
    # +1/-1 = 超出限、且方向朝上/朝下。
    flag: list[int | None] = []
    for i, value in enumerate(values):
        if value is None or mask[i]:
            flag.append(None)
            continue
        try:
            val = float(value)
        except (TypeError, ValueError):
            flag.append(None)
            continue
        if not (CELL_MIN_MV <= val <= CELL_MAX_MV):
            flag.append(None)
            continue
        if abs(val - median) > limit:
            flag.append(1 if val > median else -1)
        else:
            flag.append(0)

    # 「连续同向离群」够长 → 模组级不均衡（真实读值，保留）
    # 短 → 孤立断线（坏点，回填）
    #
    # ⛔ 这里的 run_min 是 12 节（= 半个 25 节模组），是一条**故意留的悬崖**：
    #    · 11 节连续 +100 mV 仍会被回填，总压少报约 1.1 V（0.28%），SOC 看不出；
    #    · 12 节及以上按真值保留。
    #    ⛔ 别为了"11 节也报出来"把 MODULE_RUN_RATIO 调低 —— E74 真机每轮稳定有
    #    8 个坏点（掉字符/超量程），门槛一降就可能把一片正常格子判成"模组偏移"
    #    从而**保留错值**，那比少报 0.28% 严重得多。要改必须用 119 轮历史日志
    #    + 会话库 499 轮重新标定，并确认 residual/module_offset 误报仍为 0 轮。
    #
    # ⚠️ 关键的非对称防护（别改坏）：超量程（0 mV / >4600）在打标阶段就是
    #    flag=None，而 None 一律 keep=False → **必然回填**。所以"整个模组采样线
    #    断了、25 节全读 0 V"不会被当成模组偏移保留下来（否则总压会塌成 320 V、
    #    SOC 直接显示 0%，把接线故障伪装成"电池没电"）。
    #    只有**量程内**的同向偏移才可能被判成 module_offset。
    per_module = cells_per_module if cells_per_module > 0 else max(1, n // 8)
    run_min = max(2, int(per_module * MODULE_RUN_RATIO))
    keep = [False] * n                 # True = 这个位置的读值算"可用"
    module_offset = False
    i = 0
    while i < n:
        sign = flag[i]
        if sign is None or sign == 0:
            keep[i] = sign == 0
            i += 1
            continue
        j = i
        while j < n and flag[j] == sign:
            j += 1
        if j - i >= run_min:
            module_offset = True
            for k in range(i, j):
                keep[k] = True         # 整段偏移：读值是真的，别回填
        i = j

    # ---- 孤立离群再分一次：断线（回填）vs 真弱芯（保真值）-----------------
    # 上面只按"连续长度"分出了模组级偏移；剩下的孤立离群以前一律当断线回填，
    # 于是**真的弱芯也被抹成均值**（E74 的 #112 恒 −32 mV，被回填成 4350，
    # 界面只能从别的节里挑 min，既跳又少报）。
    # 现在用卡方分开：χ²=(Δ/σ0)²，α=0.001（1 自由度）→ Δ_crit = 3.29·σ0。
    #   · |Δ| ≤ Δ_crit：在"一节电芯可能有多不均衡"的物理范围里 → **是弱芯**，
    #     保留真值（同时记进 weak_idx 供诊断）；
    #   · |Δ| > Δ_crit：不可能是一节电芯（断线/坏通道读到轨值）→ 照旧回填。
    # ⛔ 超量程 / 缺失（flag is None）**不参与**这一步：它们必然回填，这是
    #    "整个模组采样线断了不能被当成真值"的非对称防护，别在这里放开。
    # ⛔ σ0 不是整包实测离散（那只有 1~3 mV，10 mV 就"显著"），而是物理允许的
    #    单体不均衡 —— 详见 CELL_IMBALANCE_TOL_MV 的标定说明。
    tol = float(imbalance_tol_mv) if float(imbalance_tol_mv) > 0 else \
        CELL_IMBALANCE_TOL_MV
    broken_delta_mv = math.sqrt(CHI2_CRIT_1DOF_0001) * tol
    weak_idx: list[int] = []
    for k in range(n):
        if keep[k] or flag[k] is None:
            continue
        if abs(float(values[k]) - median) <= broken_delta_mv:
            keep[k] = True             # 弱芯：读值是真的，别回填
            weak_idx.append(k + 1)

    accepted = [float(values[k]) for k in range(n) if keep[k]]
    if not accepted:                   # 全被判成孤立坏点：退回量程内全集
        accepted = list(healthy)
    mean = sum(accepted) / len(accepted)
    med = statistics.median(accepted)
    gap = abs(mean - med)
    ok = len(accepted) >= int(n * MIN_HEALTHY_RATIO)

    # 回填：凡是 keep=False 的位置（缺失 / 坏 / 超量程 / 孤立离群）一律写均值
    fill: list[float] = []
    bad_idx: list[int] = []
    for k in range(n):
        if keep[k]:
            fill.append(float(values[k]))
        else:
            fill.append(mean)
            bad_idx.append(k + 1)

    # 极值从**量程内的真实读值 `healthy`** 里取，绝不用上面的 fill（回填值是均值）。
    # ⛔ 为什么不是 `accepted`（=keep=True）：真机实测暴露一个跳动源 —— 某个真实
    #    弱芯（如 4318mV，比一片 4350 的 pack 低 32mV）正好卡在 MAD/IQR 门槛边上，
    #    `mad`/`iqr` 逐轮微小波动会让它**时而被判孤立离群(keep=False)、时而又保留**，
    #    于是 min 在 4324↔4336 之间反复跳（实测 100 轮里 50 次，约 1.25% SOC），
    #    底栏 min 肉眼可见地抖。`accepted` 会把这些**真实弱芯**一起剔掉，既跳又
    #    不准（把 4318 报成 4324）。
    #    `healthy` 只排除「超量程 / 设备打 X / 缺失」，保留所有在量程内的真值；
    #    而 `_robust_extremes` 自带的「相邻间隙 > EXTREME_GAP_MV(30mV) 就丢」正是
    #    用来挡断线野值（3900 那种离群会被丢），所以不会把故障读值当极值。
    lo_mv, hi_mv, n_drop = _robust_extremes(healthy)
    # 已经用卡方**确认为真弱芯**的节，不能被上面的"相邻间隙 > 30 mV 就丢"再踢掉：
    # 间隙规则的目的是挡残留坏点，而弱芯是被证明"物理上就是电芯"的读数。
    # 若不放行，一个 −90 mV 的真弱芯（与次弱间隙 63 mV）会被丢出极值 → 界面
    # min 报成次弱那一节，SOC min 直接**高报**约 9%。实测 E74 的 #112 只低 32 mV、
    # 与次弱 #73 差 5 mV，所以这条目前不改变任何读数；它是为更弱的情形兜底。
    if weak_idx:
        weak_vals = [float(values[k - 1]) for k in weak_idx
                     if 0 < k <= len(values)]
        if weak_vals:
            lo_mv = min(lo_mv, min(weak_vals))
            hi_mv = max(hi_mv, max(weak_vals))

    return CleanResult(fill, bad_idx, mean, med, gap, gap > MEAN_MED_MAX_MV,
                       ok, n_raw, n - n_raw, module_offset,
                       lo_mv, hi_mv, n_drop, weak_idx)


# ---------------------------------------------------------------- 估算器


class SocEstimator:
    """按轮喂数据、逐轮出 SOC。**有状态**：内部记安时积分与零漂估计。

    典型用法::

        est = SocEstimator(SocCfg("E74"))
        for round in rounds:
            res = est.update(round.cells, round.temps, round.i, round.ts,
                             contactor_open=round.open)

    换车 = 换 ``SocCfg``；不认识的车型给 ``None`` 就整个功能不启用（界面显示
    ``--``），不会影响其它任何功能。
    """

    def __init__(self, cfg: SocCfg | None = None, ekf=None):
        """``ekf`` 给一个 ``SocEKF4`` 实例就走四状态 EKF 分支；None = 原方法。

        ⛔ 这里**故意不 import soc_ekf**：算法模块与估算器解耦，用鸭子类型
        （只要有 ``reset()`` / ``step()`` 即可），这样原方法路径不依赖 EKF 模块
        是否可用，也方便单测注入假的滤波器。
        """
        self.cfg = cfg or SocCfg()
        self._ekf = ekf
        self._mode = WorkModeClassifier()
        self.soc = 50.0
        self.known = False          # 是否已冷启动（第一次有数据时按 OCV 定初值）
        self.i_bias = 0.0
        self._last_ts: float | None = None
        self._u_hist: list[tuple[float, float]] = []
        self._rest_since: float | None = None
        self.last: SocResult | None = None
        self._cur_temp_c: float | None = None   # 本轮平均温度（°C），None=无温度

    # -- 对外 ---------------------------------------------------------

    def reset(self, soc: float | None = None) -> None:
        """清空积分状态。``soc`` 给了就按它当起点（用户手动校准时用）。"""
        self.soc = 50.0 if soc is None else max(0.0, min(100.0, float(soc)))
        self.known = soc is not None
        self.i_bias = 0.0
        self._last_ts = None
        self._u_hist.clear()
        self._rest_since = None
        self.last = None
        self._mode.reset()
        if self._ekf is not None and hasattr(self._ekf, "reset"):
            self._ekf.reset(z=None if soc is None
                            else max(0.0, min(1.0, float(soc) / 100.0)))

    def update(self, cells_mv, temps_c=None, i_meas=None, ts=None,
               bad_mask=None, contactor_open=None, bms_ok=True) -> SocResult:
        """喂一轮数据，返回本轮 SOC。

        ``cells_mv``  按序号排好的端电压（mV，可为 None）；
        ``temps_c``   温度（°C），用于静置判定的常温门；
        ``i_meas``    设备上报电流（A）；None = 本轮没有电流字段（不积分）；
        ``ts``        时间戳（秒，单调递增）；None = 用上一次的时间（dt 记 0）；
        ``bad_mask``  设备标记为坏的位置（``X``）；
        ``contactor_open`` 母线接触器是否**全断**。全断时真实电流必为 0，
                      读数就纯是零漂 —— 这是唯一能可靠学到零漂的时机。
        """
        cfg = self.cfg
        clean = clean_cells(cells_mv, bad_mask, cfg.n_cells, cfg.cells_per_module,
                            cfg.imbalance_tol_mv)

        if not clean.ok:
            flag = "NO_DATA" if clean.mean_mv <= 0 else "WEAK"
            res = SocResult(self.soc, clean.pack_mv / 1000.0, clean, 0.0,
                            self.i_bias, _mean_temp(temps_c), flag, False, False)
            self.last = res
            return res

        # --- 电流：零漂、方向 ---------------------------------------
        i_eff, i_valid = self._effective_current(i_meas, contactor_open, bms_ok)

        # --- 工况判别：提前算，下面 κ 调制 / 充电效率要按状态走 ----------
        # 分类器有滞回与趋势状态，**每轮必须只调一次**；后面 SocResult 复用同一结果。
        mode = self._mode.classify(
            ts=ts, mean_mv=clean.mean_mv, i_eff=i_eff, i_valid=i_valid,
            contactor_open=contactor_open, bms_ok=bms_ok)

        # --- 时间步长 -----------------------------------------------
        gap = None
        dt = 0.0
        if ts is not None:
            if self._last_ts is not None:
                gap = float(ts) - self._last_ts
                dt = gap
                if dt < 0 or dt > DT_MAX_S:
                    dt = 0.0            # 断线重连/暂停恢复：当没有这段时间
            self._last_ts = float(ts)

        temp_c = _mean_temp(temps_c)
        self._cur_temp_c = temp_c
        anchored = False
        initialised = False
        pol_mv = 0.0            # EKF 估出的 U1+U2+Uc（mV），原方法路径恒 0
        ekf_recovered = False

        if self._ekf is None:
            # ---------- 原路径：安时积分 + κ 回拉 + 静置锚定 ----------
            # ⛔ 这一段拿 119 轮日志 + 499 轮会话库实测标定过。开关关闭时
            #    必须保持原行为，别顺手"优化"任何一个系数。
            # 长时间中断后重新用 OCV 锚定（见 REINIT_GAP_S）。
            # 判据刻意留宽：只要不是"明知有大电流在流"就重锚。理由是我们**没看见**
            # 这段时间，旧的积分值已经作废；而 _reinit_from_ocv 已经做了 IR 补偿，
            # 即便此刻在带载，端电压加回压降也还是 OCV 的合理估计。
            if self.known and gap is not None and gap > REINIT_GAP_S \
                    and (not i_valid or abs(i_eff) <= REST_I_MAX_A):
                self._reinit_from_ocv(clean, i_eff if i_valid else 0.0)
                self._u_hist.clear()
                self._rest_since = None
                initialised = True

            if not self.known:
                # 冷启动：这一轮的端电压（已做 IR 补偿）直接查表定初值
                self.soc = soc_from_ocv(
                    self._ocv_estimate(clean.mean_mv, i_eff) / 1000.0,
                    cfg.ocv_table)
                self.known = True
                initialised = True
            elif not initialised:
                # 1) 安时积分（骨架）
                if i_valid and dt > 0:
                    d_soc = i_eff * dt / 3600.0 / cfg.q_ah * 100.0
                    if i_eff < 0:                  # 充电：库仑效率 < 1，存进去更少
                        d_soc *= cfg.charge_eff
                    self.soc -= d_soc

                # 2) 电压回拉（κ 加权）。RESIDUAL 时不采信 —— 均值可能还被
                #    没剔干净的断线拽着，这时候纠偏就是把错误焊进 SOC。
                #    ⚠️ 整模组偏移（module_offset）**不**在这里拦住：那些读值是真的，
                #    均值没有偏，拿它查 OCV 是对的；拦了反而会长期失去电压校正，
                #    只剩安时积分漂移。所以只提示、不阻断。
                if not clean.residual:
                    ocv_v = self._ocv_estimate(clean.mean_mv, i_eff) / 1000.0
                    soc_ocv = soc_from_ocv(ocv_v, cfg.ocv_table)
                    if mode == "open":
                        # 母线断开：电流必为 0，OCV 估计本身可信但噪声会逐轮把
                        # 中心 soc 抖来抖去。冻结 κ 快回拉（只靠下方静置锚定做慢校，
                        # 它自带 120 s 稳定窗口 + ±15% 封顶 + 0.3 增益的死区），
                        # 避免放大跳变（探针实测单独放开会把中心 soc 跳变放大数倍）。
                        pass
                    else:
                        k = (kappa_at(self.soc, cfg.ocv_table) * K_VOLT
                             * self._kappa_gain(mode, i_eff, i_valid))
                        self.soc += k * (soc_ocv - self.soc)

                # 3) 静置锚定（唯一的强校正）
                anchored = self._rest_anchor(clean, i_eff, i_valid, ts, temp_c)
        else:
            # ---------- 四状态 EKF 路径 ----------
            # 量测 = **平均单体端电压**。滤波器内部把带载电压里的 IR 压降与
            # U1/U2/Uc 三段极化/漂移剥离，再用 OCV 反查 SOC；估出的极化量
            # 会复用到下面 min/max 的 OCV 反演里（这才是开关真正的差别）。
            z_from_v = soc_from_ocv(
                self._ocv_estimate(clean.mean_mv, i_eff) / 1000.0,
                cfg.ocv_table) / 100.0
            long_gap = (self.known and gap is not None
                        and gap > REINIT_GAP_S
                        and (not i_valid or abs(i_eff) <= REST_I_MAX_A))
            if not self.known or long_gap:
                self._ekf.reset(z=z_from_v)
                self.known = True
                initialised = True
                self._u_hist.clear()
                self._rest_since = None

            step = self._ekf.step(clean.mean_mv / 1000.0,
                                  i_eff if i_valid else 0.0,
                                  dt if dt > 0 else 0.0)
            if step.valid and not step.recovered:
                self.soc = step.z * 100.0
                pol_mv = (step.u1 + step.u2 + step.uc) * 1000.0
            else:
                # 发散自愈：本轮回退到 OCV 查表值，界面转橙色提示
                self.soc = z_from_v * 100.0
                pol_mv = 0.0
                ekf_recovered = True

        self.soc = max(0.0, min(100.0, self.soc))

        # ---- min / max：用清洗后真实读值的最低/最高单体电压估 ----------
        # 物理意义：放电时**最弱那节**先到截止电压 → 看 min；充电时**最高那节**
        # 先到截止 → 看 max。两个值都由 OCV 查表得到，共用同一个 IR + 极化补偿，
        # 所以间距只取决于电芯一致性，不会被主算法的积分漂移带跑。
        ocv_bias_mv = (i_eff if i_valid else 0.0) * cfg.r0_mohm + pol_mv \
            - self._ocv_temp_shift_mv()
        soc_min = soc_from_ocv((clean.min_mv + ocv_bias_mv) / 1000.0,
                               cfg.ocv_table)
        soc_max = soc_from_ocv((clean.max_mv + ocv_bias_mv) / 1000.0,
                               cfg.ocv_table)
        soc_min = max(0.0, min(100.0, soc_min))
        soc_max = max(0.0, min(100.0, soc_max))
        if soc_min > soc_max:
            soc_min, soc_max = soc_max, soc_min
        # ---- 工况判别：见 update 开头（每轮只调一次，结果复用到这里）----
        # 用户 2026-11-11 定：只有**明确充电**才突出 max；放电、静置、
        # 母线断开、工况未知一律默认突出 min（保守口径：FSAE 更关心
        # "还能放多少电"）。底栏会同时标出工况，不会让人误以为在跑车。
        emphasis = "max" if mode == "charge" else "min"

        flag = ""
        if clean.residual:
            flag = "RESIDUAL"
        elif clean.module_offset:
            # 模组级不均衡：读数可信，但用户该知道某个模组整段偏高/偏低
            flag = "MODULE"
        elif ekf_recovered:
            flag = "EKF_RECOVER"
        elif clean.n_unusable > 0 and clean.n_unusable > clean.n_raw // 4:
            flag = "WEAK"

        res = SocResult(self.soc, clean.pack_mv / 1000.0, clean, i_eff,
                        self.i_bias, temp_c, flag, anchored, initialised,
                        soc_min, soc_max, emphasis, pol_mv, ekf_recovered,
                        mode)
        self.last = res
        return res

    # -- 内部 ---------------------------------------------------------

    def _effective_current(self, i_meas, contactor_open, bms_ok):
        """返回 ``(i_eff, i_valid)``。``i_eff`` 已扣零漂、已按方向归一。"""
        if i_meas is None:
            return 0.0, False
        try:
            raw = float(i_meas)
        except (TypeError, ValueError):
            return 0.0, False
        # ⛔⛔ 判序是刻意的，**接触器全断必须排在轨值判断之前**（2026-11-11 改）：
        #    · "母线全断 ⇒ 真实电流必为 0" 是**硬件级事实**，比任何电流读数都硬，
        #      也**不依赖 bms_ok** —— 真机常态是 BMS 报错（bms_ok=False）但接触器
        #      已断开，母线断开⇒电流为 0 仍成立；
        #    · 而 `HALL_I` 上有 R71(20k) 下拉 —— 传感器没接 / 没供电 / 线断时
        #      ADC 必然读 0 V，固件把它换算成 **−250 A 轨值**（就是 ADC 码 0；
        #      实测取值只有 −250.00 / −249.88 / −249.76 / −249.63 / −249.51，
        #      量化步进 0.12 A = 1 LSB）；
        #    · 旧判序先判轨值就 `return 0.0, False`，于是 `i_valid=False` →
        #      `_rest_anchor` 首行 `if not i_valid: return False` 直接退出，
        #      **静置锚定在整段轨值会话里一次都不跑**（真机 3099/4929 帧、
        #      累计约 3 小时），SOC 只剩 κ 加权电压回拉；
        #    · 「接触器全断」分支不要求 bms_ok：母线断开 + 轨值帧返回 ``(0.0, True)``，
        #      不学零漂（|raw|=250 > BIAS_SAMPLE_MAX_A=20）、不积分（i_eff 恒 0），
        #      但**静置锚定能恢复**，这正是要的效果。
        if contactor_open is True:
            # 母线全断 = 真实电流为 0，读数就是零漂，学它（不依赖 bms_ok）
            if math.isfinite(raw) and abs(raw) <= BIAS_SAMPLE_MAX_A:
                self.i_bias += BIAS_GAIN * (raw - self.i_bias)
                self.i_bias = max(-BIAS_LIMIT_A, min(BIAS_LIMIT_A, self.i_bias))
            return 0.0, True

        if not math.isfinite(raw) or abs(raw) > I_LIMIT_A:
            # 超出物理上限：这一帧的电流不可用。**不积分**（而不是钳到限值），
            # 因为钳位等于替设备编了一个数；宁可当作没读到。
            return 0.0, False

        return (raw - self.i_bias) * self.cfg.i_sign, True

    def _ocv_temp_shift_mv(self) -> float:
        """温度-OCV 补偿量（mV）：查表前要从电压里**减去**它。

        物理：``OCV(T) = OCV(T_ref) + coef*(T - T_ref)``，NCM 的 coef 为负
        （温度越高 OCV 越低）。要把测得（已做 IR 修正）的电压映射回 T_ref 表，
        需要 ``+coef*(T-T_ref)`` 的修正；这里返回要**减去**的量
        ``shift = coef*(T - T_ref)``，即查表电压用 ``V_lookup = V_meas - shift``。

        仅在 (a) 配置了非零系数 且 (b) 本轮有温度读数时生效；否则返回 0
        （完全保留原行为，coef 默认 0）。
        """
        coef = self.cfg.ocv_temp_coef_mv_per_c
        if coef == 0.0 or self._cur_temp_c is None:
            return 0.0
        return coef * (self._cur_temp_c - self.cfg.ocv_temp_ref_c)

    def _ocv_estimate(self, mean_mv: float, i_eff: float) -> float:
        """端电压 → OCV（mV）。

        放电（``i_eff > 0``）时端电压低于 OCV，把 IR 压降加回去；充电反过来。
        这就是 Excel 里那条 ``OCV = V + 82 mV`` 的同一个式子。
        温度补偿：减掉 ``_ocv_temp_shift_mv``（见其文档），把电压折算回
        OCV 表标定的参考温度。
        """
        return mean_mv + i_eff * self.cfg.r0_mohm - self._ocv_temp_shift_mv()

    def _kappa_gain(self, mode: str, i_eff: float, i_valid: bool) -> float:
        """状态相关 κ 调制系数（乘到 ``K_VOLT`` 上）。

        * ``open``：母线断开，冻结快回拉（见 ``update`` 内 open 分支说明）。
        * 接近静置（``|I| ≤ REST_I_MAX_A``）：电压最可信 → 提速回拉；
        * 大电流放电/充电：OCV 估计受极化污染 → 更信安时积分，调低 κ；
        * 有电流读数但介于二者之间：在 1.0 与 ``KAPPA_HI_CURRENT`` 间线性过渡；
        * 无电流读数（``i_valid=False``）：维持原 ``K_VOLT``（= 1.0）。

        系数都贴着 119 轮 + 499 轮会话库标定的 ``K_VOLT=0.02`` 做小幅调制，
        不推翻已验证的跟踪精度。
        """
        if mode == "open":
            return 0.0
        if not i_valid:
            return 1.0
        a = abs(i_eff)
        if a <= REST_I_MAX_A:
            return KAPPA_IDLE_BOOST
        if a >= KAPPA_HI_I_A:
            return KAPPA_HI_CURRENT
        t = (a - REST_I_MAX_A) / (KAPPA_HI_I_A - REST_I_MAX_A)
        return 1.0 + t * (KAPPA_HI_CURRENT - 1.0)

    def _reinit_from_ocv(self, clean: CleanResult, i_eff: float) -> None:
        """静置中断后按开路电压重新锚定。

        和静置锚定（``_rest_anchor``，增益 0.3、单次封顶 ±15%）的区别：那条是
        "小步纠偏"，这条是"我们从没看见这段时间，原先的积分值已经作废"。所以
        直接赋值 —— 但只在**确认静置**时走，动着的电压里全是极化，不能当 OCV。
        """
        ocv_v = self._ocv_estimate(clean.mean_mv, i_eff) / 1000.0
        self.soc = soc_from_ocv(ocv_v, self.cfg.ocv_table)

    def _rest_anchor(self, clean: CleanResult, i_eff: float, i_valid: bool,
                     ts, temp_c) -> bool:
        """静置锚定：车停了、电压稳了、温度合适，才敢拿 OCV 硬拉一次。"""
        if ts is None or not i_valid or abs(i_eff) > REST_I_MAX_A:
            self._rest_since = None
            return False
        if not (REST_T_MIN_C <= temp_c <= REST_T_MAX_C):
            self._rest_since = None
            return False

        self._u_hist.append((float(ts), clean.mean_mv))
        # 只留窗口内的点（按时间倒着砍）
        cut = float(ts) - REST_WINDOW_S
        while self._u_hist and self._u_hist[0][0] < cut:
            self._u_hist.pop(0)
        if len(self._u_hist) < 2:
            self._rest_since = float(ts)
            return False

        span = max(v for _, v in self._u_hist) - min(v for _, v in self._u_hist)
        if span > REST_STABLE_MV:
            self._rest_since = float(ts)      # 还在动，重新计时
            return False
        if self._rest_since is None:
            self._rest_since = float(ts)
        if float(ts) - self._rest_since < REST_WINDOW_S:
            return False

        soc_ocv = soc_from_ocv(
            (clean.mean_mv - self._ocv_temp_shift_mv()) / 1000.0, self.cfg.ocv_table)
        delta = soc_ocv - self.soc
        delta = max(-REST_CAP_PCT, min(REST_CAP_PCT, delta))
        self.soc += REST_GAIN * delta
        return True


def _mean_temp(temps_c) -> float:
    """整包温度代表值（°C）。

    取**中位数**而不是均值：真机日志里温度采样同样有脏点（实测出现过
    ``8.87`` 这种 87 °C 的孤立值），一两个坏点就能把均值拽出十来度，而温度
    只是用来判"能不能做静置锚定"，用中位数一两度之内不受影响。
    全坏就给 20 °C（常温），不因为没温度就把 SOC 停掉。
    """
    vals = []
    for t in temps_c or ():
        try:
            v = float(t)
        except (TypeError, ValueError):
            continue
        if -40.0 <= v <= 125.0:
            vals.append(v)
    return statistics.median(vals) if vals else 20.0


# ---------------------------------------------------------------- 显示


def format_soc_parts(res: SocResult | None,
                     cfg: SocCfg | None = None) -> tuple[str, str, str, str]:
    """底栏四枚标签的文本 ``(工况, min, max, 总电压)``。

    工况是 2026-11-11 新增：真机上母线常常是断开的、电流传感器又会给
    轨值，不标出来用户根本不知道这两个数该按哪个口径读。
    """
    if res is None or res.clean is None or not res.clean.ok:
        return "", "SOC --", "", ""
    warn = (res.clean.residual or res.clean.module_offset
            or res.flag in ("WEAK", "EKF_RECOVER"))
    mark = "  ±" if warn else ""
    lo = "--" if res.soc_min is None else f"{res.soc_min:.1f}%"
    hi = "--" if res.soc_max is None else f"{res.soc_max:.1f}%"
    label = MODE_LABELS.get(getattr(res, "mode", "unknown"), "工况未知")
    return (f"[{label}]", f"SOC min {lo}", f"max {hi}{mark}",
            f"总电压 {res.pack_v:.1f} V")


def format_soc(res: SocResult | None, cfg: SocCfg | None = None) -> str:
    """完整一行（回测 / 日志用）；界面走 :func:`format_soc_parts` 分三枚标签。"""
    parts = format_soc_parts(res, cfg)
    return "   ".join(x for x in parts if x)
