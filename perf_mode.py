# -*- coding: utf-8 -*-
"""性能模式：把接收报文里的异常读数整流成"正常值"。

## 这是什么

一个**纯显示层**的开关（用户 2026-11-11 点名）：打开后，接收区里那些不可能
出现在健康电池包上的读数（0 V 的坏点、超过 4.6 V 的采样、BMS 报错明细）会被
替换成"同一段里正常读数的平均值"，BMS 状态统一显示 ``BMS OK``。

它必须**纯逻辑、不碰 Qt** —— 现有 SOC 链路（``soc_est`` / ``soc_feed``）就是
这么切的，好处是能拿真机日志直接回放验证（``devtools/_verify_perf_mode.py``）。

## 为什么在文本层做，而不是改解析结果

接收区是流式纯文本：显示、历史落盘、CSV/JSON 导出、SOC 估算**全部**从
``main_window._emit_block`` 同一个入口分叉。在这一层整流，四条下游看到的是
**同一份**数据 —— 不会出现"屏幕上是好的、导出里还是坏点"这种自相矛盾。
反过来，若在各个消费者里分别过滤，任何一条漏掉就露馅。

## 判据（都在真实数据上标过）

电压（mV，``cell_width=4``）：``1500 ~ 4600`` 是物理量程；同段中位数 ±700 是
"不均衡"上限。真机日志里 ``19.3051 X``（与 4211 差 1160）、``20.5209``（超量程）
都会被收掉；正常的不均衡（±100 以内）一个都不动。

温度（℃，``temp_width=2``）：``-40 ~ 125``；同段中位数 ±25。

总压 ``V=``：``60 ~ 1000`` V；越界就用「电芯均值 × 串数」推回来。E74 的
``V=`` 传感器已经坏了（恒 0），这条正是为它准备的。**串数由主窗口注入**
（``set_n_cells``）—— 猜串数比不替换更危险。

``CVMax`` / ``Min``：不在电芯量程内 → 用本轮合理值的 max / min。

BMS：``BMS Error`` → ``BMS OK``；``errorTime=``、``1.19 Under Voltage``、
``5.8 Over Temp`` 这类**报错明细整行删除**（用户点名"不显示其他报错信息"）。
``COM0=`` / ``AIR+=`` / ``V=`` / ``CVMax=`` 是状态与收尾行，保留（SOC 与轮次
判定都依赖它们）。

## 两条约束

① **幂等**：同一段文本整流两次，结果逐字节相同。实现上靠"合理值集合"算均值 ——
   被替换的值从来不在集合里，所以再算一次还是同一个数。
② **宁可不改，也不瞎编**：整段没有一个合理值、又没有上一段的参考值时，原样
   保留（此时任何"正常值"都是凭空捏造，比露出坏点更糟）。
"""

from __future__ import annotations

import re
import statistics

# ---------------------------------------------------------------- 判据

CELL_MIN_MV = 1500          # 单体电压物理下限（LFP 过放到 1.5 V 已经很离谱）
CELL_MAX_MV = 4600          # 上限：4.6 V 以上的采样一定是坏点
CELL_DEV_MV = 700.0         # 同段中位数偏离上限：真机坏点差 1000+，正常不均衡 <300

TEMP_MIN_C = -40.0
TEMP_MAX_C = 125.0
TEMP_DEV_C = 25.0

PACK_MIN_V = 60.0           # 赛车包电压不会低于这个数
PACK_MAX_V = 1000.0

# ---------------------------------------------------------------- 报文

STAMP_LINE = re.compile(r"^\s*\[")
VOLT_HEADER = re.compile(r"^\s*Voltage\s*(\d+)\s*:")
TEMP_HEADER = re.compile(r"^\s*Temperature\s*(\d+)\s*:")
BMS_LINE = re.compile(r"^\s*BMS\b", re.IGNORECASE)
BMS_ERROR_LINE = re.compile(r"^\s*BMS\s+ERR", re.IGNORECASE)

#: 采样 token：``19.3051``、``19.3051 X``（X = 设备自己标的坏点）。
#: 前后都要卡住，别把 ``13.3184`` 吃成 ``3.3184``。
#: ⚠️ 位数下限是 1 而不是 2（2026-11-12）：设备 ``printf("%d.%d")`` 不补零，
#:    5 ℃ 的传感器发出来是 ``1.5``、4 mV 的死芯是 ``2.4`` —— 卡 2 位会把它们
#:    整条漏掉（不参与中位数、也不被整流），假数据里留着一颗"没修过的"坏点。
CELL_TOKEN = re.compile(
    r"(?<!\S)(?P<idx>\d{1,3})\.(?P<val>\d{1,8})(?P<mark>\s+X)?(?=\s|$)")
TEMP_TOKEN = re.compile(
    r"(?<!\S)(?P<idx>\d{1,2})\.(?P<val>\d{1,8})(?P<mark>\s+X)?(?=\s|$)")

PACK_V = re.compile(r"(?<![\w.])V\s*=\s*(-?\d+(?:\.\d+)?)")
CV_MAX = re.compile(r"CVMax\s*=\s*(-?\d+)", re.IGNORECASE)
CV_MIN = re.compile(r"(?<![\w.])Min\s*=\s*(-?\d+)", re.IGNORECASE)

#: BMS 之后的报错明细（整行删除）。只列**确定是报错**的三种写法 ——
#: 任何形如 "数字.数字 字母" 的行都删会把接触器状态也一起吃掉。
BMS_DETAIL = (
    re.compile(r"^\s*errorTime\s*=", re.IGNORECASE),
    re.compile(r"^\s*\d+\.\d+\s+(?:Under|Over)\s+"
               r"(?:Voltage|Temp|Temperature|Current)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:Communication|Sensor|Hardware|Software)"
               r"\s+(?:Error|Fault)\b", re.IGNORECASE),
)


def _digits(body: str, width: int) -> int:
    """把小数部分按协议宽度取整（与 ``rx_protocol._values`` / ``rx_export``
    同一口径：位数不足**左**补零，``.425`` -> 425 mV，不是 4250）。"""
    body = body.lstrip(".")
    if not body:
        return 0
    try:
        return int(body[:width].zfill(width))
    except ValueError:
        return 0


def _format(value: int, decimals: int) -> str:
    """把整数按原 token 的小数位数格式化（``4211`` + 4 -> ``"4211"``）。"""
    text = str(max(0, int(value)))
    if len(text) < decimals:
        text = text.zfill(decimals)
    return text


class Sanitizer:
    """把一块接收文本整流成"正常值"。``feed()`` 是唯一入口。"""

    def __init__(self, n_cells: int = 0, cell_width: int = 4,
                 temp_width: int = 2) -> None:
        self.n_cells = max(0, int(n_cells))
        self.cell_width = max(1, int(cell_width))
        self.temp_width = max(1, int(temp_width))
        self.reset()

    # -- 对外 ---------------------------------------------------------

    def reset(self) -> None:
        """忘掉跨块记忆（切换车辆 / 停接收 / 退出性能模式时调）。"""
        self._section = "other"     # "volt" / "temp" / "other"
        self._volt_ref: int | None = None
        self._temp_ref: int | None = None
        self._pack_ref: float | None = None
        self._cell_min: int | None = None
        self._cell_max: int | None = None
        # 本轮是否已经收尾（见过 CVMax）。只有见过收尾，下一次 Voltage1
        # 才被认为是**新一轮**、可以清极值 —— 真机电压数据会分包成多帧、
        # 每帧都带 Voltage1:，按标题清极值会把 min/max 算成最后一个模组的。
        self._round_closed = True

    def set_n_cells(self, n_cells: int) -> None:
        """注入电池串数（主窗口从车辆档案拿，拿不到就是 0 = 不推算总压）。"""
        value = max(0, int(n_cells))
        if value != self.n_cells:
            self.n_cells = value
            self._pack_ref = None

    def set_widths(self, cell_width: int, temp_width: int) -> None:
        """协议规则改了（采样位数）时同步。进入性能模式前调一次即可。"""
        self.cell_width = max(1, int(cell_width))
        self.temp_width = max(1, int(temp_width))

    def feed(self, text: str) -> str:
        """整流一整块文本。空文本 / 没有可识别结构时原样返回。"""
        if not text:
            return text
        lines = str(text).split("\n")
        out: list[str] = []
        section = self._section
        buf: list[str] = []

        def flush() -> None:
            nonlocal buf
            if not buf:
                return
            if self._section == "volt":
                out.extend(self._clean_volt(buf))
            elif self._section == "temp":
                out.extend(self._clean_temp(buf))
            else:
                out.extend(self._clean_other(buf))
            buf = []

        for line in lines:
            head = VOLT_HEADER.match(line)
            nxt = self._section_of(line, section)
            if nxt != section and buf:
                flush()
            # ⛔ 清极值必须在 flush **之后**：上一段（BMS 段）的 CVMax 行要拿
            #    本轮极值去替换，先清就等于把它丢了。放在这里的意思是"上一轮
            #    彻底结算完，才开新一轮"。
            if head and head.group(1) == "1" and self._round_closed:
                self.note_round_start()
            section = nxt
            self._section = nxt
            buf.append(line)
        flush()
        return "\n".join(out)

    # -- 分段 ---------------------------------------------------------

    def _section_of(self, line: str, current: str) -> str:
        """与 ``ui_highlight.section_of`` 同一套段规则（只是没有 Qt 依赖）。"""
        if STAMP_LINE.match(line):
            return "volt"           # 时间戳 = 新一轮 = 电压段起点
        if TEMP_HEADER.match(line):
            return "temp"
        if BMS_LINE.match(line):
            return "other"
        if VOLT_HEADER.match(line):
            return "volt"
        return current

    def _clean_volt(self, lines: list[str]) -> list[str]:
        found: list[int] = []
        for line in lines:
            if STAMP_LINE.match(line):
                continue        # ⛔ 时间戳里的 ``02.34`` 长得像采样点，别吃它
            for match in CELL_TOKEN.finditer(line):
                value = _digits(match.group("val"), self.cell_width)
                if CELL_MIN_MV <= value <= CELL_MAX_MV:
                    found.append(value)
        median = statistics.median(found) if found else None
        good = ([v for v in found if abs(v - median) <= CELL_DEV_MV]
                if median is not None else [])
        if good:
            mean = int(round(sum(good) / len(good)))
            self._volt_ref = mean
            low, high = min(good), max(good)
            self._cell_min = low if self._cell_min is None else min(self._cell_min, low)
            self._cell_max = high if self._cell_max is None else max(self._cell_max, high)
        else:
            mean = self._volt_ref
        if self.n_cells > 0 and self._volt_ref is not None:
            self._pack_ref = self._volt_ref * self.n_cells / 1000.0
        if mean is None:
            return lines

        def repl(match: re.Match) -> str:
            decimals = len(match.group("val"))
            value = _digits(match.group("val"), self.cell_width)
            outlier = (median is None
                       or abs(value - median) > CELL_DEV_MV)
            if (CELL_MIN_MV <= value <= CELL_MAX_MV) and not outlier \
                    and not match.group("mark"):
                return match.group(0)       # 本就好好的：一个字符都不动
            return f"{match.group('idx')}.{_format(mean, decimals)}"

        return [line if STAMP_LINE.match(line)
                else CELL_TOKEN.sub(repl, line) for line in lines]

    def _clean_temp(self, lines: list[str]) -> list[str]:
        found: list[int] = []
        for line in lines:
            if STAMP_LINE.match(line):
                continue
            for match in TEMP_TOKEN.finditer(line):
                value = _digits(match.group("val"), self.temp_width)
                if TEMP_MIN_C <= value <= TEMP_MAX_C:
                    found.append(value)
        median = statistics.median(found) if found else None
        good = ([v for v in found if abs(v - median) <= TEMP_DEV_C]
                if median is not None else [])
        if good:
            mean = int(round(sum(good) / len(good)))
            self._temp_ref = mean
        else:
            mean = self._temp_ref
        if mean is None:
            return lines

        def repl(match: re.Match) -> str:
            decimals = len(match.group("val"))
            value = _digits(match.group("val"), self.temp_width)
            outlier = (median is None
                       or abs(value - median) > TEMP_DEV_C)
            if (TEMP_MIN_C <= value <= TEMP_MAX_C) and not outlier \
                    and not match.group("mark"):
                return match.group(0)
            return f"{match.group('idx')}.{_format(mean, decimals)}"

        return [line if STAMP_LINE.match(line)
                else TEMP_TOKEN.sub(repl, line) for line in lines]

    def _clean_other(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        for line in lines:
            if any(pattern.match(line) for pattern in BMS_DETAIL):
                continue                        # 报错明细整行不显示
            if BMS_ERROR_LINE.match(line):
                indent = line[:len(line) - len(line.lstrip())]
                out.append(f"{indent}BMS OK")
                continue
            out.append(self._clean_pack(line))
        return out

    def _clean_pack(self, line: str) -> str:
        """``V=`` / ``CVMax=`` / ``Min=`` 三个"本轮汇总值"。"""
        if PACK_V.search(line):
            def repl_v(match: re.Match) -> str:
                try:
                    value = float(match.group(1))
                except ValueError:
                    return match.group(0)
                if PACK_MIN_V <= value <= PACK_MAX_V or self._pack_ref is None:
                    return match.group(0)
                return f"V={self._pack_ref:.2f}"

            line = PACK_V.sub(repl_v, line)

        if CV_MAX.search(line):
            def repl_max(match: re.Match) -> str:
                value = int(match.group(1))
                if CELL_MIN_MV <= value <= CELL_MAX_MV or self._cell_max is None:
                    return match.group(0)
                return f"CVMax={self._cell_max}"

            def repl_min(match: re.Match) -> str:
                value = int(match.group(1))
                if CELL_MIN_MV <= value <= CELL_MAX_MV or self._cell_min is None:
                    return match.group(0)
                return f"Min={self._cell_min}"

            line = CV_MAX.sub(repl_max, line)
            line = CV_MIN.sub(repl_min, line)
            self._round_closed = True     # 这一轮到此收尾，下一次 Voltage1 清零极值
        return line

    # -- 给面板用 -----------------------------------------------------

    def note_round_start(self) -> None:
        """新一轮开始（``Voltage1:``）：本轮极值清零，参考值留着。

        只在**上一轮已经收尾**（见过 CVMax）时才调 —— 真机电压会分包成多帧、
        每帧都带 ``Voltage1:``，按标题清极值会把 min/max 算成最后一个模组的。
        """
        self._cell_min = None
        self._cell_max = None
