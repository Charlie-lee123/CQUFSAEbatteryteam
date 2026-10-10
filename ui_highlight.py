"""接收区里"时间戳 / 序号 / 电压分级 / BMS 状态"的着色。

为什么用 ``QSyntaxHighlighter`` 而不是把 HTML 塞进文档：
- 文档里的**文本本身一点没变** —— 保存的日志、搜索命中、复制拿到的都是纯文本，
  颜色只是屏幕上的装饰层，复制出来不带任何标记；
- ``maximumBlockCount`` 裁剪、增量追加这些行为都不受影响。

颜色跟着主题走（``ui_theme`` 的 token），换主题或改电压范围后调 ``refresh()``
重刷一遍。时间戳紫、序号蓝、超限红/欠压绿、BMS ERROR 黄 —— 扫一眼就能分清
"什么时候 / 第几个点 / 哪块电池有问题"。

电压分级只作用于 **Voltage 段**内的采样值：Temperature（温度）等其它段落的
数值长得一样但不能按毫伏阈值比 —— 用一个"当前在哪个段"的状态机跟着块序走
（``highlightBlock`` 按文档顺序逐块调用，状态自然连续；文档清空重排时从
False 重新开始）。
"""

from __future__ import annotations

import re

from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat

import customization
from ui_theme import qcolor, rx_accent, stamp_accent

# 时间戳：形如 [21:47:14] 或 [2026-10-10 21:47:14]（年/月/日三段各有独立
# 开关，见 customization），永远落在块首（每轮只有一个，见 main_window.RX_STAMP_ANCHOR）。
# 只认块首 —— 数据里也可能出现方括号，别把正文里的东西误染成时间戳。
STAMP_RE = re.compile(r"^\[(?:(?:\d{4}|\d{2})(?:-\d{2})* )?\d{2}:\d{2}:\d{2}\]")

# 采样点："1.3820" = 第 1 点、值 3820（mV）。组 1 是序号、组 2 是 ".值"。
# 两条边界都是拿真实数据逼出来的：
# - ``(?<!\S)``：前面必须是**空白或行首**，挡住 "12.3823" 里的 "2"、
#   "I=-250.00" 里的 250、"V=0.00" 里的 0 —— 那些都是**值**，不是序号；
# - 后面必须真的是小数，否则 "Voltage1" 的 1、"COM0=0" 的 0 都会被染上。
SAMPLE_RE = re.compile(r"(?<!\S)(\d+)(\.\d+)")

# 段标题："Voltage1:" 开启电压段；"Temperature1:" 开启温度段；其它段标题都关掉。
# 判段顺序不能反：OTHER 那条匹配任何 [A-Za-z]+\d*:，Temperature 也落进去。
VOLT_HEADER_RE = re.compile(r"\s*Voltage\d*\s*:")
TEMP_HEADER_RE = re.compile(r"\s*Temperature\d*\s*:")
OTHER_HEADER_RE = re.compile(r"\s*[A-Za-z]+\d*\s*:")
# BMS 那一行（"BMS ERROR" / "BMS OK" …）—— 它既是异常标记，也是**段的右边界**
BMS_LINE_RE = re.compile(r"\s*BMS\b")

# 段名：voltage / temperature / other（other 包含"还没见过任何段标题"的初始态）
SECTION_VOLT = "voltage"
SECTION_TEMP = "temperature"
SECTION_OTHER = "other"

BMS_ERROR_TEXT = "BMS ERROR"


def section_of(text: str, current: str = SECTION_OTHER) -> str:
    """这一块把"当前是哪个段"更新成什么。数据行不动状态。

    段落边界照**设备实际的数据布局**定（用户点名）：
    一轮是 ``[时间戳] → 电压数据 → Temperature1: → 温度数据 → BMS …``，
    也就是"电压数据在时间戳到 Temperature1 之间，温度数据在 Temperature1 到 BMS 之间"。

    由此三条规则：

    - **时间戳行 = 新的一轮开始 = 电压段重新开始**。设备不保证每轮都发
      "Voltage1:" 标题（有的轮直接就是数据），只认标题会把那些轮整个漏掉 ——
      时间戳是每轮必有的那个锚，用它切段最稳。
    - ``Temperature1:`` 起是温度段。
    - ``BMS`` 起两个段都关掉（别的段/无段）：这段数据既不是电压也不是温度，
      不停的话后面的行会被当成温度接着染。

    ``highlightBlock`` 按文档顺序逐块调用，所以状态是连续推进的；文档被清空重排
    时从初始态重新开始，不会串段（``QSyntaxHighlighter`` 的行为）。
    """
    if stamp_span(text):
        return SECTION_VOLT
    if TEMP_HEADER_RE.match(text):
        return SECTION_TEMP
    if BMS_LINE_RE.match(text):
        return SECTION_OTHER
    if VOLT_HEADER_RE.match(text):
        return SECTION_VOLT
    if OTHER_HEADER_RE.match(text):
        return SECTION_OTHER
    return current


def stamp_span(text: str) -> tuple[int, int] | None:
    """时间戳的染色区间 ``(起点, 长度)``，没有就是 None。"""
    match = STAMP_RE.match(text)
    if match:
        return match.start(), match.end() - match.start()
    return None


def index_spans(text: str) -> list[tuple[int, int]]:
    """序号的染色区间 ``[(起点, 长度), ...]``。

    单独拎出来是为了能**脱离控件**推敲边界（自测直接断言它）。
    """
    return [(m.start(1), m.end(1) - m.start(1)) for m in SAMPLE_RE.finditer(text)]


# 序号补齐到几位（"1.3820" -> "01.3820"）。用户点名："一位数的数值在前方补零
# 以对齐占位，并让序号以'.'对齐" —— 他的数据每段就 20~40 个点，两位足够。
INDEX_PAD_WIDTH = 2


def align_indexes(text: str, width: int = INDEX_PAD_WIDTH) -> str:
    """把采样点的**序号**补零到固定宽度，让小数点上下对齐。

    "1.3820   2.3824   10.3824" 直接显示时，一位数的点会比两位数的点整体左移一格，
    竖着扫一列数值就参差不齐；补一个前导 **0**（接收区是**等宽字体**，一位数字
    正好一格）之后，所有序号都是 `width` 位，"序号" 后面那个 "." 就落在同一列。

    ⚠️ 补的是数字 0 而不是空格（用户点名"在前方补零"）。前导零不影响数值语义，
    但会让复制出去的文本与设备原文**不完全相同** —— 所以只动序号前面，
    值一个字符都不碰（自测里有一条"去掉空白后必须逐字相同"兜着）。
    """
    if width <= 1:
        return text

    def pad(match) -> str:
        index = match.group(1)
        return "0" * max(0, width - len(index)) + index + match.group(2)

    return SAMPLE_RE.sub(pad, text)


def _ordered(low, high) -> tuple[int, int]:
    """把用户可能填反的上下限摆正 —— 否则整片数据都会被判成越界。"""
    low, high = int(low), int(high)
    return (high, low) if low > high else (low, high)


def value_spans(text: str, low: int, high: int) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """电压采样值的分级区间 ``(超高, 超低)``，各自 ``[(起点, 长度), ...]``。

    值取小数点后的整数部分（"1.3820" 的 3820，单位 mV）。脱离控件可测。
    """
    high_spans: list[tuple[int, int]] = []
    low_spans: list[tuple[int, int]] = []
    for m in SAMPLE_RE.finditer(text):
        value = int(m.group(2)[1:])
        if value > high:
            high_spans.append((m.start(2) + 1, m.end(2) - m.start(2) - 1))
        elif value < low:
            low_spans.append((m.start(2) + 1, m.end(2) - m.start(2) - 1))
    return high_spans, low_spans


def temp_spans(text: str, low: int, high: int) -> list[tuple[int, int]]:
    """温度采样值里**越界**的那些区间 ``[(起点, 长度), ...]``。

    温度不分"偏高/偏低"两种颜色 —— 用户点名"凡超出该范围的数值均以黄色标注"，
    所以越界就是一个列表。值同样取小数点后的整数部分（"1.22" 的 22，单位 ℃）。
    """
    spans: list[tuple[int, int]] = []
    for m in SAMPLE_RE.finditer(text):
        value = int(m.group(2)[1:])
        if value > high or value < low:
            spans.append((m.start(2) + 1, m.end(2) - m.start(2) - 1))
    return spans


def measure_error_spans(text: str, section: str, volt: tuple[int, int],
                        temp: tuple[int, int]) -> list[tuple[int, int]]:
    """一行里**电压/温度越界**的区间（不含 BMS ERROR）。

    与 ``RxHighlighter`` 的着色口径共用同一批判定函数，免得"染成红/绿/黄的那行"
    和"点按钮跳过去的那行"对不上。
    """
    spans: list[tuple[int, int]] = []
    if section == SECTION_VOLT:
        high, low = value_spans(text, volt[0], volt[1])
        spans += high + low
    elif section == SECTION_TEMP:
        spans += temp_spans(text, temp[0], temp[1])
    return spans


def error_spans(text: str, section: str, volt: tuple[int, int],
                temp: tuple[int, int]) -> list[tuple[int, int]]:
    """一行里所有"有误"的区间：电压/温度越界 **加上** BMS ERROR。

    BMS 那行是"整包报错"的标记（用户要它标黄），所以也算有误；不过定位按钮
    **优先**找电压/温度越界 —— 用户的说法是"定位到最近一次读取到的电压有误或
    温度有误的位置"，BMS 只在前两者都没有时兜底（见 main_window._last_error_block）。
    """
    return measure_error_spans(text, section, volt, temp) + bms_error_spans(text)


def bms_error_spans(text: str) -> list[tuple[int, int]]:
    """"BMS ERROR" 字样的染色区间。"""
    spans = []
    start = 0
    while True:
        index = text.find(BMS_ERROR_TEXT, start)
        if index < 0:
            return spans
        spans.append((index, len(BMS_ERROR_TEXT)))
        start = index + len(BMS_ERROR_TEXT)


class RxHighlighter(QSyntaxHighlighter):
    """接收区的着色：时间戳（紫）/ 序号（蓝）/ 电压分级（红绿）/ 温度越界（黄）/ BMS（黄）。"""

    def __init__(self, document) -> None:
        super().__init__(document)
        self._stamp_fmt = QTextCharFormat()
        self._index_fmt = QTextCharFormat()
        self._high_fmt = QTextCharFormat()
        self._low_fmt = QTextCharFormat()
        self._bms_fmt = QTextCharFormat()
        self._temp_fmt = QTextCharFormat()
        self._limits = (0, 0)
        self._temp_limits = (0, 0)
        self._section = SECTION_OTHER
        self._recolor()

    def _recolor(self) -> None:
        """按**当前**主题取色，并重读电压/温度范围。换主题/改范围后必须 refresh()。"""
        self._stamp_fmt.setForeground(stamp_accent())
        self._index_fmt.setForeground(rx_accent())
        self._high_fmt.setForeground(qcolor("volt_high"))
        self._low_fmt.setForeground(qcolor("volt_low"))
        self._bms_fmt.setForeground(qcolor("bms_error"))
        self._temp_fmt.setForeground(qcolor("temp_warn"))
        self._limits = _ordered(customization.get("volt_limit_low"),
                                customization.get("volt_limit_high"))
        self._temp_limits = _ordered(customization.get("temp_limit_low"),
                                     customization.get("temp_limit_high"))

    # 定位按钮（main_window）要按同一批阈值去找"有误"的位置，所以开两个读口
    def volt_limits(self) -> tuple[int, int]:
        return self._limits

    def temp_limits(self) -> tuple[int, int]:
        return self._temp_limits

    def refresh(self) -> None:
        """主题或（电压/温度）范围变了调一次。"""
        self._recolor()
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        # 段状态机：Voltage 标题开电压段、Temperature 标题开温度段，其它段标题关掉。
        # 数据行不动状态 —— 一个段里的多行数据都按同一套阈值处理。
        self._section = section_of(text, self._section)

        stamp = stamp_span(text)
        if stamp:
            self.setFormat(stamp[0], stamp[1], self._stamp_fmt)

        volt_low, volt_high = self._limits
        temp_low, temp_high = self._temp_limits
        for match in SAMPLE_RE.finditer(text):
            self.setFormat(match.start(1), match.end(1) - match.start(1),
                           self._index_fmt)
            start = match.start(2) + 1
            length = match.end(2) - match.start(2) - 1
            if self._section == SECTION_VOLT and volt_high > 0:
                value = int(match.group(2)[1:])
                if value > volt_high:
                    self.setFormat(start, length, self._high_fmt)
                elif value < volt_low:
                    self.setFormat(start, length, self._low_fmt)
            elif self._section == SECTION_TEMP:
                value = int(match.group(2)[1:])
                if value > temp_high or value < temp_low:
                    self.setFormat(start, length, self._temp_fmt)

        for start, length in bms_error_spans(text):
            self.setFormat(start, length, self._bms_fmt)
