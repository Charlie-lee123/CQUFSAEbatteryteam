"""SOC 底栏 min/max 显示门控：母线断开期间完全冻结，实现肉眼零波动。

为什么 open 期间完全冻结
------------------------
``mode == "open"`` 表示 AIR+/AIR-/PRE 全 0、母线**硬断开、无功率流**，
SOC 严格不变（自放电要到几小时才有 0.0x% 量级）。E74 台架实测
（``devtools/_probe_soc_idle_drift.py``）：217.9 s 连续静置段里 125 节
均值极差仅 0.5726 mV（约 0.044% SOC）、逐轮相邻最大 0.0855 mV
（约 0.007% SOC）；但 106 轮回看日志里 ``soc_min`` 却有 1.5835% 极差 ——
那不是真实 SOC 变化，而是报文掉帧（99/125 → 125/125 采样点重组）
与低端孤立电芯被 MAD 判据摆动剔除造成的采集伪变化；静置回弹也属极化
消退而非 SOC 变化。因此 **open 期间任何 min/max 变化都不该上屏**。

冻结范围与边界
--------------
* ⛔ 工况标记与总压**保持实时**：总压是独立物理量，1.31 V 跳变对应真实
  10 mV 均值阶跃，不纳入冻结；
* ⛔ 不改 ``SocResult`` 数值，导出 / 历史 / 回测 / 仿真链路一律不变；
* 母线一旦闭合，``WorkModeClassifier`` 把 ``contactor_open`` 放在第 1 层，
  ``mode`` 立刻离开 ``open`` → 冻结立即解除并清基准，真实带载零迟钝；
* 唯一例外是「大幅偏离安全阀」：连续 2 轮 |Δ| ≥ 5% 视为真实剧变
  （母线断开期间外接充电 / 长时间自放电），允许刷新基准，单轮尖峰不触发。

本模块零 Qt、零副作用、纯状态机，可脱离 ``QApplication`` 单测。
"""
from __future__ import annotations

#: open 期间「大幅偏离」安全阀门限（百分点）。|Δ| ≥ 此值才启动确认，
#: 用来放行真实剧变；实测采集伪台阶最大约 1.6%，远低于 5%。
SOC_DISPLAY_HARD_JUMP = 5.0

#: 安全阀需要连续确认的轮数，防单轮尖峰误触发刷新基准。
SOC_DISPLAY_CONFIRM = 2

#: 安全阀待确认值的稳定性容差（百分点）：第 2 轮目标值与第 1 轮 pending
#: 差不超过它，才算「稳定在同一新值」。
SOC_DISPLAY_HYST = 0.5

#: 残缺轮标记：这几类读数不采信，沿用基准（且首次残缺不建立基准）。
SOC_DISPLAY_BAD_FLAGS = ("WEAK", "NO_DATA", "RESIDUAL")

#: 总开关：置 False 时 decide 完全直通（透明），单测「反证」用它逐字复现
#: 未门控的原始 2/1 次跳变，证明判据能区分新旧。
SOC_DISPLAY_FREEZE_OPEN = True


class SocDisplayGate:
    """母线断开时冻结 SOC min/max 显示，实现肉眼零波动。"""

    __slots__ = ("_shown_min", "_shown_max",
                 "_pending_min", "_pending_max", "_pending_count")

    def __init__(self):
        self._shown_min: float | None = None
        self._shown_max: float | None = None
        self._pending_min: float | None = None
        self._pending_max: float | None = None
        self._pending_count = 0

    def _reset_pending(self) -> None:
        self._pending_min = None
        self._pending_max = None
        self._pending_count = 0

    def decide(self, mode: str, flag: str,
               soc_min: float | None,
               soc_max: float | None) -> tuple[float | None, float | None]:
        """返回本轮应显示的 ``(min, max)``。

        规则按顺序：① 透明开关（反证用）；② 非 open 立即全量刷新并清基准；
        ③ 残缺轮沿用且不建基准；④ 首个有效轮建立基准；⑤ open 期间一律
        沿用基准（完全冻结）；⑥ 仅 |Δ| ≥ HARD_JUMP 且连续 CONFIRM 轮稳定
        才走安全阀刷新基准。
        """
        # 1. 透明模式（单测反证用）：完全直通，不动任何状态
        if not SOC_DISPLAY_FREEZE_OPEN:
            return soc_min, soc_max

        # 2. 非 open（真实带载 / 工况改变）→ 立即全量刷新，保证零迟钝
        if mode != "open":
            self._shown_min = soc_min
            self._shown_max = soc_max
            self._reset_pending()
            return soc_min, soc_max

        # 3. 残缺轮 → 不采信：沿用基准（未建立则为 None），清待确认。
        #    首次调用若就是残缺轮，不建立基准，等第一个有效轮。
        if (soc_min is None or soc_max is None
                or flag in SOC_DISPLAY_BAD_FLAGS):
            self._reset_pending()
            return self._shown_min, self._shown_max

        # 4. 基准未建立（首个有效轮）→ 建立基准
        if self._shown_min is None or self._shown_max is None:
            self._shown_min = soc_min
            self._shown_max = soc_max
            self._reset_pending()
            return soc_min, soc_max

        delta = max(abs(soc_min - self._shown_min),
                    abs(soc_max - self._shown_max))

        # 5. open 期间一律沿用基准：小于安全阀门限的采集/清洗伪变化全滤掉
        if delta < SOC_DISPLAY_HARD_JUMP:
            self._reset_pending()
            return self._shown_min, self._shown_max

        # 6. 大幅偏离安全阀：连续 SOC_DISPLAY_CONFIRM 轮稳定在同一新值才刷新。
        #    单轮尖峰只记 pending；任何一轮回到门限内或残缺都会清 pending。
        stable = (self._pending_count > 0
                  and abs(soc_min - self._pending_min) <= SOC_DISPLAY_HYST
                  and abs(soc_max - self._pending_max) <= SOC_DISPLAY_HYST)
        if stable:
            self._pending_count += 1
        else:
            self._pending_min = soc_min
            self._pending_max = soc_max
            self._pending_count = 1
        if self._pending_count >= SOC_DISPLAY_CONFIRM:
            self._shown_min = soc_min
            self._shown_max = soc_max
            self._reset_pending()
            return self._shown_min, self._shown_max
        return self._shown_min, self._shown_max
