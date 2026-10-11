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
# 以对齐占位宽度，并让序号以'.'对齐" —— 他的数据每段就 20~40 个点，两位足够。
# 只作用于**电压段**；温度段、BMS 之后的错误明细行都保持设备原样（用户
# 2026-10-11 点名"温度区域序号不用加0"、"BMSERROR后面的序号只有一位数就
# 不需要再加0"）。
INDEX_PAD_WIDTH = 2

# 网格重排的列间隔：单元格先补空格到段内最宽，再用 3 个空格拼接 —— 每一列的
# 起点就完全一致了。设备自己按"序号位数收窄间隔"排版，我们补零后单元格变宽、
# 设备间隔不动，各行的列起点会漂移（用户截图点名的"没有对齐"）；间隔归一后
# 无论软换行从哪断，整列都是齐的。
CELL_GAP = "   "

# 整词采样点："12.6553" 是、"V=0.00" 不是。用来识别"整行都是采样点"的**纯数据
# 行** —— 只有纯数据行参与网格重排，混着别的字的行只做原地补零。
_SAMPLE_TOKEN_RE = re.compile(r"\d+\.\d+")


def _is_data_line(line: str) -> bool:
    tokens = line.split()
    return bool(tokens) and all(_SAMPLE_TOKEN_RE.fullmatch(t) for t in tokens)


def _pad_line(line: str, section: str, widths: dict[str, int],
              index_width: int) -> str:
    """非数据行的原地补零（老规则）：**电压段**的序号补到 index_width
    （0 号点除外），其它段（温度 / BMS 之后的错误明细等）序号保持设备原样
    （用户 2026-10-11 点名"温度区域序号不用加0"、"BMSERROR后面的序号
    只有一位数就不需要加0"）。电压/温度段的值补到段内最宽。混着其它字符的
    行不重排 —— 万一是判定不了结构的行，保持原布局最稳。"""
    def pad(match: re.Match, section: str = section) -> str:
        index = match.group(1)
        value = match.group(2)[1:]
        value = "0" * max(0, widths.get(section, 0) - len(value)) + value
        # 序号为 0 不补占位：0 号点是设备数据里的特殊行，补成 "00" 反而
        # 在序号前面多出一个占位字符（用户点名"序号为 0 时，序号前面不应
        # 再保留一个空格，去掉该多余空格"）—— 保持 "0.xxx" 原样。
        if section == SECTION_VOLT and index != "0":
            pad_w = max(0, index_width - len(index))
        else:
            pad_w = 0
        return "0" * pad_w + index + "." + value

    return SAMPLE_RE.sub(pad, line)


def _grid_rows(tokens: list[tuple[str, str]], section: str,
               widths: dict[str, int], index_width: int, cols: int,
               max_row_chars: int = 0) -> list[str]:
    """把一组采样点排成等宽网格：值补零到段内最宽；电压段序号补零到
    index_width（0 号点、温度段都不补）；单元格补空格到等宽后以 CELL_GAP 拼接，
    按.cols 个一列折行（cols 取这组数据里最宽一行的列数 —— 设备的排版意图）。
    短行（分包劈出来的孤行）因此被并回网格，不再单独占一行。

    ``max_row_chars``：视口一行能放下的字符数（主窗口量出来传入）。行宽超过
    视口的话软换行会在行尾甩出一个孤零零的单元格（用户截图里"13.6553"单独
    占一行），所以列数取 min(设备列数, 视口装得下的列数)。0 = 不设上限。
    """
    cells: list[str] = []
    for index, value in tokens:
        value = "0" * max(0, widths.get(section, 0) - len(value)) + value
        if section == SECTION_VOLT and index != "0":
            index = "0" * max(0, index_width - len(index)) + index
        cells.append(index + "." + value)
    cell_w = max(len(cell) for cell in cells)
    if max_row_chars > 0:
        pitch = cell_w + len(CELL_GAP)
        fit = max(1, (max_row_chars + len(CELL_GAP)) // pitch)
        cols = min(cols, fit)
    rows = []
    for start in range(0, len(cells), cols):
        row = cells[start:start + cols]
        rows.append(CELL_GAP.join(cell.ljust(cell_w) for cell in row).rstrip())
    return rows


def align_indexes(text: str, index_width: int = INDEX_PAD_WIDTH,
                  section: str = SECTION_OTHER,
                  max_row_chars: int = 0) -> str:
    """采样点排版对齐：电压/温度段的连续数据行按**等宽网格**重排。

    - 序号：**只有电压段**补 —— `"1.3820"` -> `"01.3820"`（0 号点除外）；
      温度段（用户点名"温度区域序号不用加0"）、BMS 之后的错误明细行
      （用户点名"BMSERROR后面的序号只有一位数就不需要加0"）都保持原样；
    - 值：电压/温度段里补零到该段内最宽值的位数；段外（BMS 之后的散值、
      判定不了单位的行）**不动值** —— 不知道单位语义，盲补反而会摆出假数据的
      样子；
    - 布局：整行都是采样点的连续数据行并成一组，单元格等宽、列距统一
      （CELL_GAP），按这组里最宽一行的列数折行；``max_row_chars``（视口一行
      的字符数，主窗口量出来）更小时按视口封顶 —— 行宽超视口会被软换行甩出
      孤单元格（"13.6553" 单独一行，用户截图点名）。

    为什么要重排而不只补零（用户 2026-10-11 的两张截图）：设备按"序号位数
    收窄间隔"排版，补零把单元格撑宽后各行列起点漂移（"这种地方没有对齐"）；
    分包抖动还会把一行数据劈成短行，温度区出现孤零零的 " 4.87"（"不该换行的
    地方不要换行"）。等宽网格下列距全等，软换行从哪断整列都齐；短行并回
    网格，不再有孤行。

    ``section`` 是这块文本**之前**所处的段（``section_of`` 的状态，由调用方在块间
    维护）。值宽按"帧内该段的最大值宽"算 —— 一帧数据各点位数一致，跨帧各自对齐。

    ⚠️ 数字本身一个都不动：只在前导位置补数字 0（用户："在前方补零"）、重排
    空白 —— 自测用"摘掉前导 0、压平空白后逐字等于原文"兜底。
    """
    lines = text.split("\n")

    # 逐行推进段状态，同时标记"电压/温度段的纯数据行"（整行都是采样点）。
    current = section
    marks: list[tuple[str, bool]] = []
    for line in lines:
        current = section_of(line, current)
        marks.append((current, current in (SECTION_VOLT, SECTION_TEMP)
                      and _is_data_line(line)))

    # 第一遍：段内（帧内）最宽的值有几位 —— 宽度口径与旧版一致。
    widths: dict[str, int] = {}
    for (sec, is_data), line in zip(marks, lines):
        if is_data:
            for match in SAMPLE_RE.finditer(line):
                widths[sec] = max(widths.get(sec, 0), len(match.group(2)) - 1)

    # 第二遍：连续纯数据行并成一组做网格重排；其余行按老规则原地补零。
    out: list[str] = []
    current = section
    i = 0
    while i < len(lines):
        sec, is_data = marks[i]
        current = sec
        if not is_data:
            out.append(_pad_line(lines[i], current, widths, index_width))
            i += 1
            continue
        tokens: list[tuple[str, str]] = []
        cols = 1
        j = i
        while j < len(lines) and marks[j][1]:
            found = [(m.group(1), m.group(2)[1:])
                     for m in SAMPLE_RE.finditer(lines[j])]
            cols = max(cols, len(found))
            tokens.extend(found)
            j += 1
        out.extend(_grid_rows(tokens, sec, widths, index_width, cols,
                              max_row_chars))
        i = j
    return "\n".join(out)


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
        # BMS 错误块状态：BMS ERROR 那行起，**后面的错误明细行**（"1.1 Over
        # Voltage" 这些）也要跟着标橙（用户 2026-10-11 点名），直到下一轮
        # 时间戳 / 新段标题为止。
        self._in_bms_error = False
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

        # BMS 错误块：BMS ERROR 开块；下一轮时间戳（新的一轮开始）、温度/电压
        # 等新段标题、或 BMS OK（这轮没报错）关块。明细行因此整块标橙。
        if stamp or TEMP_HEADER_RE.match(text) or VOLT_HEADER_RE.match(text) \
                or OTHER_HEADER_RE.match(text):
            self._in_bms_error = False
        if bms_error_spans(text):
            self._in_bms_error = True
        elif BMS_LINE_RE.match(text):
            self._in_bms_error = False        # BMS OK：这轮没报错
        if self._in_bms_error:
            # 整行先铺橙 —— 序号由下面的采样循环再染回蓝，跟电压/温度行的
            # 观感一致（序号蓝、正文按状态着色）。
            self.setFormat(0, len(text), self._bms_fmt)

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
