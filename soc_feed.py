"""把接收区的原始报文拼成「整轮」，交给 :mod:`soc_est` 估算。

为什么单独一个模块
------------------
接收区那一侧（``main_window._emit_block``）是**流式**的：一帧可能是半轮、一轮、
也可能一轮都不完整（分包抖动、暂停恢复、断线重连）。SOC 估算要的是**整轮
125 节**才有意义，所以中间必须有一层把碎片拼起来、并明确回答"这一轮到底收到
了什么、缺了哪些"的东西。这层不碰 Qt，能脱离界面单测（``devtools/_verify_soc.py``
就是拿真机日志直接喂它）。

判「新一轮」为什么要看段状态
----------------------------
真机实测：电压数据会被**分包成多帧，每帧都带自己的 ``Voltage1:`` 标题**
（见 ``main_window._emit_block`` 里同一条注释）。所以"见到 ``Voltage1:`` 就是新
一轮"是错的 —— 那样一轮会被劈成好几段，每段只有几个电芯，SOC 全是垃圾。
正确判据和主窗口一致：**段状态从"非电压段"回到"电压段"** 才算新一轮（一轮的
布局固定是 电压 → 温度 → BMS → 接触器 → V/I → CVMax）。

电芯怎么定位
------------
报文里每个采样点长 ``序号.值``（``01.3180`` = 第 1 节 3180 mV），而**序号在每个
模组内部从 01 重新开始**。所以键必须是 ``(模组号, 序号)``：只按序号存会让模组 2
覆盖模组 1，只剩 25 节数据，而且看起来一切正常 —— 这是本模块最容易踩且最难
发现的坑。落到整串的下标是 ``模组号 × 每模组节数 + 序号 - 1``。
"""

from __future__ import annotations

import re

from soc_est import CELLS_PER_MODULE, N_MODULES

#: ``Voltage3:`` / ``Temperature5:``（设备偶尔多打空格，所以允许 ``\s*``）
VOLT_HEADER = re.compile(r"^Voltage\s*(\d+)\s*:")
TEMP_HEADER = re.compile(r"^Temperature\s*(\d+)\s*:")

#: 采样点 ``01.3180``、``23.2744 X``。值可能是 3~4 位（掉字符的坏点就是 3 位），
#: 坏点后面跟一个 ``X``。前后都要卡住，免得把 ``13.3184`` 吃成 ``3.3184``。
SAMPLE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,4})(?![\d.])\s*(X)?")

#: ``BMS OK`` / ``BMS ERROR``
BMS_RE = re.compile(r"^BMS\s+(OK|ERR)", re.IGNORECASE)

#: ``AIR+=0,AIR-=0,PRE=0``
CTRL_RE = re.compile(r"AIR\+\s*=\s*(-?\d+).*?AIR-\s*=\s*(-?\d+).*?PRE\s*=\s*(-?\d+)",
                     re.IGNORECASE)

#: ``V=0.00,I=1.89``
CVI_RE = re.compile(r"V\s*=\s*(-?\d+(?:\.\d+)?)\s*,\s*I\s*=\s*(-?\d+(?:\.\d+)?)",
                    re.IGNORECASE)

#: ``CVMax=5446, Min=0`` —— 一轮的收尾行
END_RE = re.compile(r"^CVMax\s*=")

_TEMP_PER_MODULE = 8

#: 接触器状态最多继承几轮。真实报文里 ``AIR+=…`` 那行不是每块都发（实测会话库
#: 638 个块里只有 380 个带它），而**物理状态不会因为某一块没带这行就消失** ——
#: 不继承的话，剩下的块全成了"状态未知"，估算器只好拿带零漂的电流去积分，
#: SOC 就会被那点偏置慢慢推走。给个上限是因为反过来也危险：万一接触器真的
#: 闭合了而那行此后再不出现，继承太久会把 SOC 冻住。
STICKY_ROUNDS = 10


class RoundData:
    """一轮完整的采样。字段名与 :meth:`soc_est.SocEstimator.update` 对齐。"""

    __slots__ = ("cells", "bad", "temps", "i_meas",
                 "contactor_open", "bms_ok", "complete", "n_samples")

    def __init__(self):
        self.cells: list = []          # 端电压（mV），没读到的位置是 None
        self.bad: list = []            # 设备打 X 的位置
        self.temps: list = []          # 温度（°C）
        self.i_meas = None             # 设备上报电流（A）
        self.contactor_open = None     # 接触器是否全断（True/False/None=没这行）
        self.bms_ok = True
        self.complete = False          # 是否收到了 CVMax 收尾行
        self.n_samples = 0             # 实际收到的电芯采样点个数

    def __repr__(self) -> str:
        return (f"RoundData(cells={self.n_samples}/{len(self.cells)}, "
                f"bad={sum(1 for b in self.bad if b)}, I={self.i_meas}, "
                f"open={self.contactor_open}, done={self.complete})")


class RoundCollector:
    """把流式文本拼成整轮。``feed()`` 一个块可能吐出 0 个、1 个或多个整轮。"""

    def __init__(self, n_modules: int = N_MODULES,
                 cells_per_module: int = CELLS_PER_MODULE,
                 temps_per_module: int = _TEMP_PER_MODULE):
        self.n_modules = max(1, int(n_modules))
        self.cells_per_module = max(1, int(cells_per_module))
        self.temps_per_module = max(0, int(temps_per_module))
        self.n_cells = self.n_modules * self.cells_per_module
        self.n_temps = self.n_modules * self.temps_per_module
        self._section = "other"        # "volt" / "temp" / "other"
        self._mod = 0
        self._last_seq = 0             # 当前模组内已收的最大序号（防错位回填）
        self._started = False
        self._round = RoundData()
        # 接触器状态的粘滞继承（见 STICKY_ROUNDS）
        self._last_open = None
        self._open_misses = 0

    # -- 对外 ---------------------------------------------------------

    def reset(self) -> None:
        """丢掉半轮并忘掉粘滞状态。切换车辆 / 换串口 / 停接收时调用。"""
        self._section = "other"
        self._mod = 0
        self._last_seq = 0
        self._started = False
        self._round = RoundData()
        self._last_open = None
        self._open_misses = 0

    def feed(self, text: str) -> list[RoundData]:
        """喂一个文本块，返回这个块里**收尾完成**的整轮列表。"""
        out: list[RoundData] = []
        if not text:
            return out
        for raw in str(text).split("\n"):
            line = raw.strip()
            if not line:
                continue
            done = self._feed_line(line, out)
            if done:
                out.append(done)
        return out

    def flush(self) -> RoundData | None:
        """把手上这半轮交出去（关串口 / 停止接收时清尾巴用）。"""
        if not self._started or self._round.n_samples <= 0:
            self.reset()
            return None
        data = self._finish(complete=False)
        self.reset()
        return data

    # -- 内部 ---------------------------------------------------------

    def _feed_line(self, line: str, out: list[RoundData]) -> RoundData | None:
        head = VOLT_HEADER.match(line)
        if head:
            module = int(head.group(1)) - 1
            # ⛔ 只有"从非电压段回到电压段"才是新一轮（见模块注释）
            done = None
            fresh = module == 0 and self._section != "volt"
            if fresh and self._started and self._round.n_samples > 0:
                done = self._finish(complete=False)
                self._begin()
            elif not self._started or not self._round.cells:
                self._begin()          # 首轮：先把 125 个槽位铺出来
            self._section = "volt"
            self._mod = max(0, min(self.n_modules - 1, module))
            self._last_seq = 0
            return done

        head = TEMP_HEADER.match(line)
        if head:
            self._section = "temp"
            self._mod = max(0, min(self.n_modules - 1, int(head.group(1)) - 1))
            self._last_seq = 0
            return None

        if END_RE.match(line):
            if self._started and self._round.n_samples > 0:
                done = self._finish(complete=True)
                # ⛔ 这里不能调 reset()：那会把接触器粘滞状态一起忘掉（每轮都忘，
                #    等于没做粘滞）。只开新一轮。
                self._begin()
                self._section = "other"
                return done
            return None

        if BMS_RE.match(line):
            self._section = "other"
            self._round.bms_ok = BMS_RE.match(line).group(1).upper() == "OK"
            return None

        ctrl = CTRL_RE.search(line)
        if ctrl:
            # 三个接触器全 0 = 母线断开 = 真实电流必为 0（学零漂的唯一时机）
            self._round.contactor_open = all(g.strip() == "0" for g in ctrl.groups())
            return None

        cvi = CVI_RE.search(line)
        if cvi:
            try:
                # 设备上报总压（group 1）传感器已坏、无人消费，但保留解析作
                # 格式校验：坏行仍走 ValueError 分支，不污染 i_meas。
                float(cvi.group(1))
                self._round.i_meas = float(cvi.group(2))
            except ValueError:
                pass
            return None

        if self._section == "volt":
            self._take_cells(line)
        elif self._section == "temp":
            self._take_temps(line)
        return None

    def _take_cells(self, line: str) -> None:
        if not self._round.cells:
            self._begin()          # 没见过 Voltage1: 就先铺槽位，别越界
        base = self._mod * self.cells_per_module
        for seq_s, val_s, mark in SAMPLE.findall(line):
            seq = int(seq_s)
            if not 1 <= seq <= self.cells_per_module:
                continue
            # 序号必须单调递增：掉字符会造出"值合法但序号错位"的假样本
            # （``13.3184`` 被劈成 ``1`` + ``3.3184`` 就会冒充第 3 节），
            # 用递增判据把它挡掉。
            if seq <= self._last_seq:
                continue
            self._last_seq = seq
            idx = base + seq - 1
            if idx >= self.n_cells:
                continue
            self._round.cells[idx] = int(val_s)
            if mark:
                self._round.bad[idx] = True
            self._round.n_samples += 1

    def _take_temps(self, line: str) -> None:
        if self.temps_per_module <= 0:
            return
        if not self._round.temps:
            self._begin()          # 同上：没见过 Temperature1: 也要有槽位
        base = self._mod * self.temps_per_module
        for seq_s, val_s, _mark in SAMPLE.findall(line):
            seq = int(seq_s)
            if not 1 <= seq <= self.temps_per_module:
                continue
            if seq <= self._last_seq:
                continue
            self._last_seq = seq
            idx = base + seq - 1
            if idx >= self.n_temps:
                continue
            self._round.temps[idx] = int(val_s)

    def _begin(self) -> None:
        self._round = RoundData()
        self._round.cells = [None] * self.n_cells
        self._round.bad = [False] * self.n_cells
        self._round.temps = [None] * self.n_temps
        self._started = True

    def _finish(self, complete: bool) -> RoundData:
        if not self._round.cells:
            self._begin()
        self._round.complete = complete
        self._apply_sticky()
        return self._round

    def _apply_sticky(self) -> None:
        """这一轮没带接触器状态时，沿用上一次已知的状态（有次数上限）。"""
        now = self._round.contactor_open
        if now is not None:
            self._last_open = now
            self._open_misses = 0
            return
        if self._last_open is not None and self._open_misses < STICKY_ROUNDS:
            self._round.contactor_open = self._last_open
            self._open_misses += 1
        else:
            self._last_open = None      # 太久没更新，放弃继承（宁可未知）


def parse_text(text: str, **kwargs) -> list[RoundData]:
    """一次性把一整段日志解析成整轮列表（回测 / 单测用）。"""
    collector = RoundCollector(**kwargs)
    rounds = collector.feed(text)
    tail = collector.flush()
    if tail is not None:
        rounds.append(tail)
    return rounds
