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

from PySide6.QtCore import QTimer
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


def _is_volt_line(line: str) -> bool:
    """电压段的**纯电压数据行**：整行都是采样点、且每个值都是 4 位小数
    （mV 格式，见 VOLT_DECIMALS）。

    温度值（"1.22"，2 位小数）万一落在电压段状态里（设备不发段标题等），
    不能当电压数据重排 —— 否则补零会把 "1.22" 造出 "01.0022" 这种假数据
    （凭空补位 = 篡改数值），着色也会把它按 mV 阈值误判成欠压绿
    （用户报的"标绿的区间到温度采集部分去了"）。"""
    return _is_data_line(line) and all(
        re.fullmatch(r"\d+\.\d{%d}" % VOLT_DECIMALS, t)
        for t in line.split())


def _pad_line(line: str, section: str, widths: dict[str, int],
              index_width: int) -> str:
    """非数据行的原地补零（老规则）：**电压段**的序号补到 index_width
    （0 号点除外），其它段（温度 / BMS 之后的错误明细等）序号保持设备原样
    （用户 2026-10-11 点名"温度区域序号不用加0"、"BMSERROR后面的序号
    只有一位数就不需要加0"）。电压/温度段的值补到段内最宽。混着其它字符的
    行不重排 —— 万一是判定不了结构的行，保持原布局最稳。"""
    def pad(match: re.Match, section: str = section) -> str:
        # 电压段只重排 mV 格式（4 位小数）的采样点 —— 温度值等其它小数位数
        # 的值一个字符都不动（见 _is_volt_line 的说明）。
        if section == SECTION_VOLT and not _is_volt_value(match):
            return match.group(0)
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


def grid_row_sizes(count: int, rows_hint: int = 0, cols_hint: int = 0,
                   row_sizes: list[int] | None = None) -> list[int]:
    """把 ``count`` 个采样点分成若干排，**每排该放几个**。

    用户点名的规则（原话）："若为奇数个数据，那么就让最后一排数据比前一排数据
    多一个，别的排数据个数保持一致且对齐。"

    **两条优先级**（给了 ``row_sizes`` 就先走它，一条都不走均分那套）：

    ① ``row_sizes`` 是**逐排显式尺寸**（车辆档案里"第 i 个模组的电芯/温度数"）：
       第 i 排就放 ``row_sizes[i]`` 个点 —— 一排 = 一个模组，模组长短不一时各排
       自然不一样（这正是"逐模组自行设置"的用途）。三条兜底：
       · 数据点比列表**多** → 多出来的仍按**最后一排的尺寸**继续往下排
         （绝不丢点；新增的模组沿用最后一个模组的配置，与设置面板同一口径）；
       · 数据点比列表**少** → 按实际点数截断，**不补假数据**（最后那排短一截）；
       · 列表里 ≤0 的项直接跳过（某个模组没接电芯 = 这一排不占行）。
       ⚠️ 这里只负责"按模组切"；**视口装不下时在模组内部再拆**是 ``_cap_rows``
       的事（由 ``_grid_rows`` 在拿到行尺寸之后做）—— 两件事分开，本函数保持
       纯逻辑、不受控件宽度影响（自测才好钉）。

    ② 没给 ``row_sizes``（None / 空列表，= 没选车）→ 完全走原来的均分算法，
       一个字节都不变（老行为）：
       1) 先定行数 ``rows``：给了 ``rows_hint``（模组个数）就用它；否则按
          ``cols_hint``（每排目标个数）推算 ceil(count / cols_hint)；
       2) 再定列数 ``cols``：给了 ``cols_hint`` 就用它，否则 ceil(count / rows)。
          ⚠️ **两者都给了但 rows×cols 装不下 count 时，以 count 为准按 cols 重算
          行数** —— 宁可多排一行，也绝不丢掉任何一个采样点；
       3) 均分：``base, rem = divmod(count, rows)``，前 ``rows - rem`` 排各 base 个，
          最后 ``rem`` 排各 base+1 个。
          · 各排相差 ≤ 1（base 或 base+1）；
          · 最后一排**只多不少**（rem ≥ 1 时它就是 base+1 —— 正是"末排多一"）；
          · rem == 1 时（奇数个的典型情况）除末排外**全部一致**，即用户要的效果。
          · 对齐不由本函数保证：调用方把每个单元格 ``ljust`` 到同一宽度、
            用同一个 CELL_GAP 拼接，列起点自然齐（见 _grid_rows）。
    """
    count = max(0, int(count))
    if count == 0:
        return []
    if row_sizes:
        sizes: list[int] = []
        used = 0
        last = 0
        for raw in row_sizes:
            try:
                size = int(raw)
            except (TypeError, ValueError):
                continue
            if size <= 0:
                continue                        # 这个模组没接采样点：不占排
            take = min(size, count - used)
            sizes.append(take)
            used += take
            last = size
            if used >= count:
                break
        if sizes:
            while used < count:                 # 数据多出来 → 按最后一排继续
                take = min(last, count - used)
                sizes.append(take)
                used += take
            return sizes
        # 一路全是 0 / 坏值：退回均分算法，别把数据排成空
    rows = int(rows_hint) if rows_hint and rows_hint > 0 else 0
    cols = int(cols_hint) if cols_hint and cols_hint > 0 else 0
    if rows <= 0:
        rows = max(1, -(-count // cols)) if cols > 0 else 1
    rows = max(1, min(rows, count))
    if cols <= 0:
        cols = max(1, -(-count // rows))
    if rows * cols < count:                     # 装不下 → 以数据量为准加行
        rows = max(1, -(-count // cols))
    base, rem = divmod(count, rows)
    return [base] * (rows - rem) + [base + 1] * rem


def _cap_rows(sizes: list[int], fit: int) -> list[int]:
    """把"一排 = 一个模组"的尺寸按视口容量**在模组内部**均衡拆行。

    单个模组装得下（``n <= fit``）就原样保留；装不下就拆成 ``ceil(n/fit)`` 个子排，
    子排之间相差 ≤1。拆出来的子排**连续**，所以模组的先后顺序不变、点数一个不丢，
    只是模组边界在视觉上被视口切开。

    ⚠️ 多出来的那一个放在**后面**（25 / fit=23 → 12+13）：整篇均分那条路的规则
    是用户 2026-10-11 点名的"最后一排比前一排多一个"，模组内部的子排沿用**同一条**
    口径 —— 同一个界面里出现两种"多出来的那行在前还是在后"的规则，用户只会觉得
    排布随机。代价是模组被折成两行时"第一行短、第二行长"，视觉上像一段末行被
    拉长的文字，但这与整篇的观感是一致的，不打架。

    为什么必须拆（而不是让 QPlainTextEdit 自己软换行）：25 电芯一排是
    25×7 + 24×3 = 247 个字符，1180 宽的视口只放得下 120 来个 —— 软换行会把第 25
    个点甩到下一行，变成"行尾孤零零一个单元格"，正是用户 2026-10-11 截图点名的
    那个毛病。封顶后每条视觉行都贴着视口容量、断点由我们自己定。
    """
    fit = max(1, int(fit))
    out: list[int] = []
    for size in sizes:
        size = int(size)
        if size <= 0:
            continue
        if size <= fit:
            out.append(size)
            continue
        chunks = -(-size // fit)                 # ceil(size / fit)
        base, rem = divmod(size, chunks)
        out.extend([base] * (chunks - rem) + [base + 1] * rem)
    return out


def _grid_rows(tokens: list[tuple[str, str]], section: str,
               widths: dict[str, int], index_width: int, cols: int,
               max_row_chars: int = 0, rows_hint: int = 0,
               cols_hint: int = 0, row_sizes: list[int] | None = None) -> list[str]:
    """把一组采样点排成等宽网格：值补零到段内最宽；电压段序号补零到
    index_width（0 号点、温度段都不补）；单元格补空格到等宽后以 CELL_GAP 拼接。

    折行口径见 ``grid_row_sizes`` —— 不再用 ``range(0, n, cols)`` 那种"前排填满、
    末排捡剩下的"（那样末排会比别排**少**，正是用户点名不要的效果）。

    ``cols``：设备自己发来的每行点数（这组里最宽一行的列数 —— 设备的排版意图）。
    ``rows_hint`` / ``cols_hint``：设置里的「模组个数」「每模组电芯数/温度采集数」
    （0 = 自动，跟随设备）。短行（分包劈出来的孤行）被并回网格，不单独占一行。
    ``row_sizes``：车辆档案的逐模组尺寸（一排 = 一个模组）。

    ``max_row_chars``：视口一行能放下的字符数（主窗口量出来传入）。行宽超过
    视口的话软换行会在行尾甩出一个孤零零的单元格（用户截图里"13.6553"单独
    占一行），所以每条视觉行都要封顶在这个容量内。0 = 不设上限。

    **两条路都封顶，但封法不同**：
    - 没有 ``row_sizes``（没选车）→ 先按视口把"每排目标个数"压到 fit，再交给
      ``grid_row_sizes`` 均分（老行为）；
    - 给了 ``row_sizes``（选了车）→ **先按模组切**（``grid_row_sizes`` 的显式尺寸
      分支），**再按视口在模组内部均衡拆**（``_cap_rows``）：模组还是那个模组，
      只是它长到装不下一行时，由我们决定断在第几个电芯，而不是让控件软换行。
    """
    cells: list[str] = []
    for index, value in tokens:
        value = "0" * max(0, widths.get(section, 0) - len(value)) + value
        if section == SECTION_VOLT and index != "0":
            index = "0" * max(0, index_width - len(index)) + index
        cells.append(index + "." + value)
    cell_w = max(len(cell) for cell in cells)
    # 一行 n 个单元格只有 n-1 个间隔 → 精确上界是 (max_row_chars + len(CELL_GAP))
    # // (cell_w + len(CELL_GAP))（不是 +2：那会平白少放一格）。
    fit = 0
    if max_row_chars > 0:
        fit = max(1, (max_row_chars + len(CELL_GAP)) // (cell_w + len(CELL_GAP)))
    # 没给"模组网格"就用设备自己的列数当目标列数（rows_hint=0 → 行数由列数推）
    if row_sizes:
        sizes = grid_row_sizes(len(cells), row_sizes=row_sizes)
        if fit:
            sizes = _cap_rows(sizes, fit)
    else:
        if fit:
            cols = min(cols, fit)
            if cols_hint > 0:
                cols_hint = min(cols_hint, fit)
        sizes = grid_row_sizes(len(cells), rows_hint,
                               cols_hint if cols_hint > 0 else cols)
    rows: list[str] = []
    start = 0
    for size in sizes:
        row = cells[start:start + size]
        rows.append(CELL_GAP.join(cell.ljust(cell_w) for cell in row).rstrip())
        start += size
    return rows


def align_indexes(text: str, index_width: int = INDEX_PAD_WIDTH,
                  section: str = SECTION_OTHER,
                  max_row_chars: int = 0, rows_hint: int = 0,
                  volt_cols_hint: int = 0, temp_cols_hint: int = 0,
                  volt_row_sizes: list[int] | None = None,
                  temp_row_sizes: list[int] | None = None) -> str:
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

    ``rows_hint`` / ``volt_cols_hint`` / ``temp_cols_hint``：设置里的「模组个数」
    与「每模组电芯数 / 每模组温度采集数」（见 customization）。**0 = 自动**，
    完全沿用设备发来的每行点数（这也是默认值，保证老行为不变）。

    ``volt_row_sizes`` / ``temp_row_sizes``：**选车之后的逐模组尺寸**（第 i 个
    模组的电芯数 / 温度采集数，见 customization.active_profile）。给了列表就
    按"一排 = 一个模组"排 —— 优先级**高于**上面三个 hint（同一时刻只该有一套
    参数说话；车辆档案是用户明确选中的那套，旧的全局三件套在选车后就该让位）。
    列表为 None → 与加这个参数之前一字不差。

    ⚠️ 数字本身一个都不动：只在前导位置补数字 0（用户："在前方补零"）、重排
    空白 —— 自测用"摘掉前导 0、压平空白后逐字等于原文"兜底。
    """
    # ⛔ 序号补位宽度 = 这一帧里**最大序号的位数**（下限 INDEX_PAD_WIDTH）。
    #    固定 2 位的话，序号上到 100 以上就多占一格：实测一帧 1..120，小数点
    #    间距是 11,11,11,12,11 —— "100." 那格凸出去，用户的"个位数字上下对齐"
    #    就破了。按帧内最宽序号补位后，整帧 '.' 落在同一条竖线上。
    max_index = 0
    for _m in SAMPLE_RE.finditer(text):
        try:
            max_index = max(max_index, int(_m.group(1)))
        except (TypeError, ValueError):
            continue
    index_width = max(int(index_width), len(str(max_index)))

    lines = text.split("\n")

    # 逐行推进段状态，同时标记"电压/温度段的纯数据行"（整行都是采样点）。
    # ⚠️ 电压段只把 **mV 格式（4 位小数）** 的行算数据行 —— 温度值混进来时
    # 不重排、不补零（见 _is_volt_line），温度段照旧。
    current = section
    marks: list[tuple[str, bool]] = []
    for line in lines:
        current = section_of(line, current)
        if current == SECTION_VOLT:
            grid = _is_volt_line(line)
        elif current == SECTION_TEMP:
            grid = _is_data_line(line)
        else:
            grid = False
        marks.append((current, grid))

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
        hint = volt_cols_hint if sec == SECTION_VOLT else temp_cols_hint
        sizes = volt_row_sizes if sec == SECTION_VOLT else temp_row_sizes
        out.extend(_grid_rows(tokens, sec, widths, index_width, cols,
                              max_row_chars, rows_hint, hint, sizes))
        i = j
    return "\n".join(out)


def _ordered(low, high) -> tuple[int, int]:
    """把用户可能填反的上下限摆正 —— 否则整片数据都会被判成越界。"""
    low, high = int(low), int(high)
    return (high, low) if low > high else (low, high)


def _entry_span(match: re.Match) -> tuple[int, int]:
    """一个采样点的**整条**区间：序号 + 小数点 + 值。

    染色和定位共用这一个口径 —— 用户 2026-10-12 点名两件事：高亮要**包括
    序号**（只染值的话，看不出是第几个点出的问题），而且**不要高亮到其他没有
    问题的数据**（区间严格止于这一条，不牵连同行的其它采样点）。
    """
    return match.start(1), match.end(2) - match.start(1)


# 电压值的小数位数（设备协议：mV 值固定 4 位小数，"01.3820" → 组 2 = ".3820"）。
# ⚠️ 它同时是段状态的**兜底判据**：电压红/绿只认 4 位小数的值 —— 温度值
# （"1.22"，2 位小数）万一因为设备不发段标题/段标题迟到而落在电压段状态里，
# 也不能按 mV 阈值去比，否则温度采集区会整片被标绿（用户报的
# "标绿的区间不能到温度采集部分去"）。温度越界黄不受影响（温度段有自己的
# 段边界，温度值本来就是 2 位小数）。
VOLT_DECIMALS = 4


def _is_volt_value(match: re.Match) -> bool:
    """这个采样点是不是电压格式（小数位数 == VOLT_DECIMALS）。"""
    return len(match.group(2)) - 1 == VOLT_DECIMALS


def value_spans(text: str, low: int, high: int) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """电压采样值的分级区间 ``(超高, 超低)``，各自 ``[(起点, 长度), ...]``。

    区间是**整条**（序号+值，见 ``_entry_span``）；值取小数点后的整数部分
    （"1.3820" 的 3820，单位 mV）。只认 4 位小数的电压值（见 VOLT_DECIMALS）
    —— 温度值混进来也不会被按 mV 阈值误判。脱离控件可测。
    """
    high_spans: list[tuple[int, int]] = []
    low_spans: list[tuple[int, int]] = []
    for m in SAMPLE_RE.finditer(text):
        if not _is_volt_value(m):
            continue
        value = int(m.group(2)[1:])
        if value > high:
            high_spans.append(_entry_span(m))
        elif value < low:
            low_spans.append(_entry_span(m))
    return high_spans, low_spans


def temp_spans(text: str, low: int, high: int) -> list[tuple[int, int]]:
    """温度采样值里**越界**的那些区间 ``[(起点, 长度), ...]``。

    温度不分"偏高/偏低"两种颜色 —— 用户点名"凡超出该范围的数值均以黄色标注"，
    所以越界就是一个列表。值同样取小数点后的整数部分（"1.22" 的 22，单位 ℃）；
    区间同样是**整条**（序号+值，见 ``_entry_span``）。
    """
    spans: list[tuple[int, int]] = []
    for m in SAMPLE_RE.finditer(text):
        value = int(m.group(2)[1:])
        if value > high or value < low:
            spans.append(_entry_span(m))
    return spans


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
        # 推迟着色的开关与节流计时器（见 defer()）
        self._deferred = False
        self._defer_timer: QTimer | None = None
        self._section = SECTION_OTHER
        # BMS 错误块状态：BMS ERROR 那行起，**后面的错误明细行**（"1.1 Over
        # Voltage" 这些）也要跟着标橙（用户 2026-10-11 点名），直到下一轮
        # 时间戳 / 新段标题为止。
        self._in_bms_error = False
        # 「高亮异常数据」开关（customization）。关掉只影响**上色**，段状态与
        # BMS 块状态照常推进 —— 开关拨回来后 refresh() 立刻恢复。
        self._errors_on = True
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
        # 「高亮异常数据」：关掉后电压越界（红/绿）、温度越界（黄）、BMS 错误
        # （橙）一律不上色，只留时间戳紫、序号蓝的常规着色（用户点名要这个
        # 开关）。色值照常取好，拨回来 refresh() 立刻生效。
        self._errors_on = bool(customization.get("highlight_errors"))

    # 定位按钮（main_window）要按同一批阈值去找"有误"的位置，所以开两个读口
    def volt_limits(self) -> tuple[int, int]:
        return self._limits

    def temp_limits(self) -> tuple[int, int]:
        return self._temp_limits

    def refresh(self) -> None:
        """主题或（电压/温度）范围变了调一次。"""
        self._recolor()
        self.rehighlight()

    def defer(self, ms: int) -> None:
        """**推迟着色** ``ms`` 毫秒：这期间 ``highlightBlock`` 直接返回（不上色），
        到期统一 ``rehighlight()`` 一次。

        为什么（决策-2：要"接收区不消失"又要降本，只能另找路径）：250 行/秒的
        流式数据下，每追加一块都触发高亮器逐块重算 + 重绘，和接收区最大化那类
        "大布局 + 大面积重绘"的动画**抢帧**（QA 实测展开时 P50 掉到 42fps、
        >20ms 帧 42 个）。⛔ 不用"冻结编辑器重绘"那条老路换帧率 —— 那正是用户
        报的"接收区信息接收部分消失了一段时间"的根因。

        推迟期间数据照常显示，只是这几百毫秒不带颜色；反复调用会**续期**
        （合并相邻请求），所以数据一直不停的场景也只会按 ``ms`` 的间隔批量刷
        一次，而不是每块刷一次。
        """
        self._deferred = True
        if self._defer_timer is None:
            self._defer_timer = QTimer(self)
            self._defer_timer.setSingleShot(True)
            self._defer_timer.timeout.connect(self._resume)
        self._defer_timer.start(max(0, int(ms)))

    def _resume(self) -> None:
        self._deferred = False
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        if self._deferred:
            # 推迟期内**什么都不做**（连段状态也不推进）：到期时 rehighlight()
            # 会从头逐块重跑，状态机自然重建，不会留下错乱的段状态。
            return
        # 段状态机：Voltage 标题开电压段、Temperature 标题开温度段，其它段标题关掉。
        # 数据行不动状态 —— 一个段里的多行数据都按同一套阈值处理。
        self._section = section_of(text, self._section)

        stamp = stamp_span(text)
        if stamp:
            self.setFormat(stamp[0], stamp[1], self._stamp_fmt)

        # BMS 错误块：BMS ERROR 开块；下一轮时间戳（新的一轮开始）、温度/电压
        # 等新段标题、或 BMS OK（这轮没报错）关块。明细行因此整块标橙。
        # 状态推进与"要不要真的上色"分开（后者看 _errors_on），这样关掉高亮
        # 开关时状态机照常走，拨回来立刻是对的。
        if stamp or TEMP_HEADER_RE.match(text) or VOLT_HEADER_RE.match(text) \
                or OTHER_HEADER_RE.match(text):
            self._in_bms_error = False
        if bms_error_spans(text):
            self._in_bms_error = True
        elif BMS_LINE_RE.match(text):
            self._in_bms_error = False        # BMS OK：这轮没报错
        bms_block_on = self._in_bms_error and self._errors_on
        if bms_block_on:
            # 整行铺橙，**序号也在内**（用户点名"高亮要包括序号"）—— 不再把
            # 序号染回蓝，否则明细行的序号看着跟正常数据一样，认不出是第几点。
            self.setFormat(0, len(text), self._bms_fmt)

        volt_low, volt_high = self._limits
        temp_low, temp_high = self._temp_limits
        for match in SAMPLE_RE.finditer(text):
            if not bms_block_on:
                # 正常项：序号染蓝
                self.setFormat(match.start(1), match.end(1) - match.start(1),
                               self._index_fmt)
            if not self._errors_on:
                continue
            # 有问题的那一条：把**序号 + 值**整条覆盖成异常色（用户点名
            # "包括序号"、"不要高亮到了其他没有问题的数据"）—— 覆盖范围正好是
            # 这一条 m.group(1)+m.group(2)，同行的其它采样点一个都不碰。
            start = match.start(1)
            length = match.end(2) - match.start(1)
            if self._section == SECTION_VOLT and volt_high > 0 \
                    and _is_volt_value(match):
                value = int(match.group(2)[1:])
                if value > volt_high:
                    self.setFormat(start, length, self._high_fmt)
                elif value < volt_low:
                    self.setFormat(start, length, self._low_fmt)
            elif self._section == SECTION_TEMP:
                value = int(match.group(2)[1:])
                if value > temp_high or value < temp_low:
                    self.setFormat(start, length, self._temp_fmt)

        if self._errors_on:
            for start, length in bms_error_spans(text):
                self.setFormat(start, length, self._bms_fmt)
