"""串口调试助手 —— 主界面。

界面只做三件事：把控件上的配置交给 SerialWorker、把 worker 的信号渲染出来、
把用户的操作变成对 worker 的调用。串口本身的逻辑都在 serial_io.py。

视觉上是「黑白双主题 + 小米式版式 + 车队规范」：
- 主题跟随 Windows 的浅色/深色设置，并且监听系统切换实时跟上（见 _apply_theme）。
- 没有常驻动画循环：界面是静态的平面卡片，只在有数据或状态变化时重绘。
  过场动效（悬停、开关、入场、搜索条展开）统一走 ``ui_anim`` 的共享计时器：
  120fps、非线性缓动，动画跑完立刻停表 —— 静止时零开销，动起来才连续。
  这既省电，也让「串口工具」该有的稳定感回来。
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

import serial
from PySide6.QtCore import QEvent, QPoint, QRect, QSettings, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFontMetricsF, QGuiApplication, QKeySequence, QPainter, QShortcut,
    QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QSizePolicy, QSplitter, QTextEdit,
    QVBoxLayout, QWidget,
)

from serial_io import (
    BAUD_PRESETS, BYTESIZE_CHOICES, ENCODINGS, PARITY_CHOICES, PARITY_LETTERS,
    STOPBIT_CHOICES, SerialWorker, StreamDecoder, list_serial_ports, parse_hex, to_hex,
)
import app_log
import customization
from ui_anim import Reveal
from ui_backdrop import frost_tile, paint_backdrop
from ui_brand import BrandHeader, CaptionBar
from ui_highlight import (
    SECTION_OTHER, SECTION_TEMP, SECTION_VOLT, VOLT_HEADER_RE, RxHighlighter,
    align_indexes, bms_error_spans, section_of, stamp_span, temp_spans,
    value_spans,
)
from ui_controls import (
    AlertButton, ChevronComboBox, ChevronSpinBox, IconButton, IconLabel,
    LabeledField, PulseDot, SearchEdit, Switch,
)
from ui_settings import SettingsDialog
from ui_surface import Card, fade_in
from ui_theme import (
    DEFAULT_GLASS, GAP, GLYPH_CHEVRON_DOWN, GLYPH_CHEVRON_UP, GLYPH_CLEAR,
    GLYPH_CLOSE, GLYPH_COLLAPSE, GLYPH_COPY, GLYPH_EXPAND, GLYPH_EXPAND_CHEVRON,
    GLYPH_PAUSE,
    GLYPH_PLAY, GLYPH_REFRESH, GLYPH_RESTORE, GLYPH_SAVE, GLYPH_SEARCH,
    GLYPH_SEND, GLYPH_SETTINGS, LEGACY_GLASS, SHADOW_PAD, TITLE_H, apply_scheme,
    apply_titlebar_theme,
    current_scheme, detect_system_scheme, frost_gain, glass_strength, match_fill,
    mono_font, num_font, qcolor, set_glass_strength, ui_font,
)

# 接收区保留的最大行数（可在右侧「串口配置」栏里改）。不设上限的话，
# 长时间跑的流式数据会把内存吃满。
DEFAULT_RX_BLOCKS = 5000
RX_LINES_MIN, RX_LINES_MAX = 500, 500_000
# 「保留行数」下拉的预设档位（也可直接输入范围内的任意值）
RX_LINE_PRESETS = (500, 1000, 2000, 3000, 5000, 8000, 10000, 20000, 50000,
                   100000, 200000, 500000)
MAX_HISTORY = 20
TIMER_MIN_MS = 10
# 发送区收起到只剩标题栏时的高度
TX_COLLAPSED_H = TITLE_H + SHADOW_PAD * 2 + 12
# Qt 的 QWIDGETSIZE_MAX 宏（(1<<24)-1）没有导出到 PySide6，自己写一个，
# 用来把"折叠时夹住的最大高度"放开回"不限"。
WIDGETSIZE_MAX = (1 << 24) - 1
# 发送输入框里"文本以外"的竖直开销：QPlainTextEdit 的 QSS 上下内边距与边框。
# 上下内边距是**不等**的（14 / 6，见 ui_theme 的 #txEditor 规则，为了让第一行文字居中），
# 这里要的是它们的**和**：14 + 6 + 边框 1 + 1 = 22。改那两个数就得改这里。
# 单个数据行的自身高度不写死，由文档实际排版量出来（中文要留降部，比 fontMetrics 行距高一点）。
TX_EDITOR_CHROME = 22

# 自动扫描串口的轮询间隔（毫秒）。Windows 上 list_ports 走 SetupAPI，实测单次 7~20ms，
# 一秒一次的开销可以忽略；再快也没有意义 —— 用户插完线到想看到反应，一秒足够。
PORT_WATCH_MS = 1000

# 无边框窗口"边缘缩放"的命中带宽（逻辑 px）。6 是本机 2x 缩放下的手感值：
# 再窄要点得很准，再宽会吃掉页面外圈那 12px 留白里本该给卡片/标题栏的点击。
RESIZE_BAND = 6

# 收包合并（串口没有"帧"的概念，一次 read 给的往往只是一帧里的一小段）。
# 每来一段就显示一行的话，数据会被切成碎块、数字还会被拦腰截断（"12.382" 和
# "4 13.3825" 分在两行），每块再挂一个时间戳 —— 屏幕上完全没法看。
# 所以先攒进缓冲，静默这么久没有新数据了，才把这批当作"一帧"吐出去。
# 30ms 是实测值：USB 串口的帧间隔通常在毫秒级，30ms 足够把一帧的几次 read 收拢；
# 再长会把缓速设备的相邻两帧粘在一起。
RX_MERGE_MS = 30
# 兜底一：攒够这么多字节就先吐出来，免得大流量下缓冲无限涨、界面迟迟不动
RX_MERGE_MAX_BYTES = 4096
# 兜底二：距这批首字节超过这么久就强制吐。设备一直不停发时静默窗口永远等不到，
# 没有这条的话屏幕上会一个字都不出（数据全卡在缓冲里）。
RX_MERGE_MAX_MS = 200
# 半行缓发上限：静默窗到期时若缓冲**没以换行收尾**，多半是分包把一行劈成了
# 两截（"…Over Vol" + "tage"）—— 再接着攒一会儿等后半截，最多攒这么久。
# 300ms 覆盖 USB 读线程的调度抖动；不以换行收尾的设备最坏多等这一拍才见字。
RX_HOLD_MAX_MS = 300

# 时间戳打在哪儿：**只打在每一轮数据的轮首**，也就是用户要的"只放在 Voltage1 前面"。
#
# 一轮数据在传输层会被切成好几段（见 RX_MERGE_MS），"第一段"并不等于"轮首"，
# 所以判据只能落在**内容**上：设备每一轮都从 "Voltage1:" 开始，哪一段里出现了它，
# 就给这一段打时间戳，后面那些段不再重复标。这样无论串口怎么分包，
# 屏幕上每轮都只有开头那一个时刻。
#
# 备用规则（见 _stamp_needed）：如果整条流里从来没出现过这个标记（换台设备、
# 或者用户接的是别的板子），就退回"每帧都打"—— 否则时间戳会静默消失，
# 用户只会以为功能坏了。
RX_STAMP_ANCHOR = "Voltage1"

# 兜底间隔：只发电压、**没有 Temperature/BMS 段**的设备，段状态一直停在电压段，
# 判不出轮边界 —— 隔了这么久还见到轮首标记，就按"换了一轮"处理。
# 取 400ms：明显大于轮内分包间隔（30ms 量级，半行缓发上限 300ms），又小于
# 常见的轮间隔。有段落结构的设备走"段状态回退"那条主判据，用不到这个兜底。
STAMP_ROUND_MIN_MS = 400.0

# 「定位最近一次异常」（标题栏那枚三角按钮）：跳过去之后让那一行落在视口上方第
# 几行 —— 顶格的话用户看不到上下文，不知道这个数是从哪冒出来的。
ALERT_TOP_LINES = 3
# 异常行整行底色的透明度（0~255）。越界的字符本身已经有红/绿/黄的前景色，底色
# 只负责指出"就是这一行"，所以压得比搜索命中还淡一档。
ALERT_FILL_ALPHA = 46

# 下拉框里"端口名 — 描述"的分隔符。端口名和描述要拼在一行显示，但**能打开的是前面
# 那一半**，两处都得按同一个分隔符切（见 _selected_port）。
PORT_LABEL_SEP = " — "

# 可以在设置里隐藏的**带文字**的控件：(实例属性名, 控件上显示的名字, 分组)
#
# 只收带文字的控件 —— 界面上那批纯图标按钮（打开串口、发送、暂停…）不进清单：
# 图标按钮本身没有文字，藏在设置里既认不出来也没必要，用户明确说过不要。
#
# 「编码」「历史」是标签 + 下拉框，用 LabeledField 绑成一个控件再进来：
# 显隐的粒度必须是"整行"，只藏下拉框会剩一个光秃秃的标签。
#
# 左下角那枚设置按钮同样不在表里：它是这个面板唯一的入口，
# 允许它隐藏就等于允许用户把自己锁在外面 —— 想再打开得去改注册表。
BUTTON_SPECS: tuple[tuple[str, str, str], ...] = (
    ("chk_timestamp",   "显示时间戳", "接收区"),
    ("chk_hex_view",    "HEX 显示",   "接收区"),
    ("encoding_box",    "编码",       "接收区"),
    ("chk_hex_send",    "HEX 发送",   "发送区"),
    ("chk_append_crlf", "换行",       "发送区"),
    ("chk_timer_send",  "定时发送",   "发送区"),
    ("history_box",     "历史",       "发送区"),
)


class MainWindow(QMainWindow):
    # 边缘带 → 系统缩放光标。**四个角（两条边的组合）必须排在单边前面**先比，
    # 否则"左|上"会被单边"左"提前命中、给成左右箭头（见 _edge_cursor_shape）。
    _EDGE_CURSORS = (
        (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.CursorShape.SizeFDiagCursor),
        (Qt.Edge.RightEdge | Qt.Edge.BottomEdge, Qt.CursorShape.SizeFDiagCursor),
        (Qt.Edge.RightEdge | Qt.Edge.TopEdge, Qt.CursorShape.SizeBDiagCursor),
        (Qt.Edge.LeftEdge | Qt.Edge.BottomEdge, Qt.CursorShape.SizeBDiagCursor),
        (Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor),
        (Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor),
        (Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor),
        (Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor),
    )

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CQUFSAE串口调试助手")
        self.resize(1060, 920)

        self._worker: SerialWorker | None = None
        self._decoder = StreamDecoder()
        # 自动扫描：只记"上次看到的端口名单"，名单一变就说明插了或拔了东西（见
        # _poll_ports）。**扫到什么都不自动打开** —— 用户明确要求"我自己有手"。
        self._known_ports: list[tuple[str, str]] = []
        self._rx_bytes = 0
        self._tx_bytes = 0
        self._history: list[str] = []
        self._settings = QSettings("MimoWorks", "SerialAssistant")
        self._cards: list[Card] = []
        self._titlebar_scheme = ""
        self._expanded = False
        self._normal_geometry = None
        self._rx_blocks = DEFAULT_RX_BLOCKS
        # system / light / dark —— 默认跟随 Windows，用户也可以钉死某一侧
        self._theme_mode = "system"
        self._tx_collapsed = False
        self._tx_saved_sizes: list[int] = []
        # 用户隐藏掉的按钮（存的是 BUTTON_SPECS 里的键名）。在设置面板里改。
        self._hidden_buttons: set[str] = set()
        # 收包缓冲：还没成"一帧"的数据先存这儿，见 RX_MERGE_MS 那段说明
        self._rx_pending = bytearray()
        self._rx_pending_since = 0.0
        # 数据里出现过"轮首标记"没有 —— 决定时间戳是"每轮一个"还是"每帧一个"，
        # 见 RX_STAMP_ANCHOR / _stamp_needed
        self._stamp_anchor_seen = False
        # 上一次打时间戳的时刻（monotonic 秒）—— "只发电压、没有温度/BMS 段"的
        # 设备靠轮间隔兜底打点，见 _stamp_needed
        self._stamp_last_monotonic = 0.0
        # 滚动：默认自动滚到底，暂停键（或空格）点按切换暂停/继续。
        # 暂停是**冻结显示**：新帧进这个队列不显示，恢复时一次刷入。
        self._paused = False
        self._paused_frames: list[tuple[str, str]] = []
        # 退出窗口全屏时要还原的普通几何。⚠️ 不能在全屏状态下调 saveGeometry
        # （那存下来的是满屏矩形）—— 普通态时由 moveEvent/resizeEvent 持续记忆
        self._normal_geometry: QRect | None = None
        # 接收区展开前的窗口状态：全屏与否 + 普通几何 —— 收起时要**完整恢复
        # 到点全屏按钮之前的位置和大小**（用户点名），不能把窗口留在全屏
        self._pre_expand_fullscreen = True
        self._pre_expand_geometry = None
        # 用户点了最大化按钮的转换标志（changeEvent 里最大化 → 真全屏）
        self._expect_normal = False
        # 用户**想要**的状态：True = 全屏（启动、点最大化都算）。和 _expect_normal
        # 的区别是"持久"——后者只在一次程序性 showNormal 期间有效。延迟核对
        # （_verify_fullscreen）靠它分辨"这次没铺满"是用户主动退出还是被 shell 吃掉了。
        self._want_fullscreen = True
        # 全屏几何收敛核对的重入保护（event 钩子 ↔ _enforce_native_fullscreen）
        self._in_enforce = False
        # 暂停折叠：进暂停时发送区是不是展开的 —— 恢复时只还原它自己动过的
        self._tx_expand_on_resume = False
        # 同理，工具栏（显示时间戳/HEX/编码那排）暂停前是否可见
        self._opts_visible_on_resume = False
        # 搜索：命中区间、当前命中序号、上次搜索词
        self._matches: list[tuple[int, int]] = []
        self._match_index = -1
        self._match_term = ""
        self._match_truncated = False
        # 用户有没有明确挑中某一条匹配（按过上下/回车）。
        # 挑中之后视图就不该再被新数据拽到底部了 —— 否则串口一直在流，
        # 每次找到的那条都会被立刻甩走，搜索等于是废的。
        self._search_pinned = False
        # 异常定位：高亮挂在哪个块上，以及那一块当初的文本（块被
        # maximumBlockCount 裁掉、或内容变了，就把高亮撤掉，别让它飘到别的行）。
        self._alert_selection = None
        self._alert_cursor = None
        self._alert_text = ""
        # 搜索高亮的区间列表。搜索和异常定位**共用** setExtraSelections，
        # 所以两边都先存到自己这儿，再合并出去（见 _apply_extra_selections）。
        self._search_selections: list = []
        # 排版对齐的段状态（跨块延续，规则同着色器，见 ui_highlight.section_of）
        self._align_section = SECTION_OTHER

        self._send_timer = QTimer(self)
        self._send_timer.setTimerType(Qt.PreciseTimer)
        self._send_timer.timeout.connect(self._on_send)

        # 搜索是边打边搜，但要等一下再扫：每敲一个字符就把整篇日志重扫一遍太涩。
        # 串口流式来时也用它 —— 数据不停，重扫就一直往后推，等数据歇下来再算一次。
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self._refresh_matches)

        # 收包合并的"静默窗口"：每收到一段就重新开始计时，静下来了才认为一帧发完
        self._rx_flush = QTimer(self)
        self._rx_flush.setSingleShot(True)
        self._rx_flush.timeout.connect(self._flush_rx)

        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self.lbl_toast.setText(""))

        # 接收区右上角的时钟：每秒跳一次。1Hz 的开销可以忽略，也不去跟系统时钟
        # 对齐到整秒 —— 那点相位差不值得为它多写一套逻辑。
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_clock)

        # 插拔自动识别：定期看一眼端口名单有没有变（见 _poll_ports）。
        # 没有它的话，用户插上串口得自己点刷新按钮，这不叫"自动扫描"。
        self._port_watch = QTimer(self)
        self._port_watch.setInterval(PORT_WATCH_MS)
        self._port_watch.timeout.connect(self._poll_ports)

        # 无边框：系统标题栏交给自绘的 CaptionBar（见 ui_brand.CaptionBar 的说明）。
        # 三个理由必须一起成立才走这条路：
        # 1) 全屏要藏任务栏 → shell 只对**无边框**且覆盖整屏的窗口这么做；
        # 2) 用户要求最小化/最大化还原/关闭三键在**任何状态**都在、位置不变；
        # 3) 系统标题栏本身在无边框下就没了，所以三键必须自己画。
        # ⚠️ 必须在窗口创建之前设：改窗口 flag 会让 Qt 重建原生窗口。
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)

        self._build_ui()
        self._tick_clock()
        self._clock_timer.start()
        self._wire_shortcuts()
        # 空格暂停要挂在应用级：焦点落在按钮/接收区上时也能按
        QApplication.instance().installEventFilter(self)
        self._restore_settings()
        self._refresh_ports(announce=False)
        self._port_watch.start()

        # 主题：用户存的是 system 就跟随系统，否则钉死在他选的那一套，然后监听系统切换。
        # 这里必须按 _restore_settings() 读出来的模式走 —— 以前这行直接写
        # current_scheme()（入口按系统设的那个），等于把用户存的选择丢掉：
        # 每次启动都退回系统主题，设置里的「浅色/深色」要打开面板按一次确定才生效。
        self._apply_theme(detect_system_scheme() if self._theme_mode == "system"
                          else self._theme_mode)
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_system_scheme_changed)

        QTimer.singleShot(50, self._play_intro)

    # ---------------------------------------------------------------- 主题

    def _on_system_scheme_changed(self, scheme) -> None:
        # 只有「跟随系统」模式才响应系统切换；用户手动钉了浅色/深色就不该被系统改动
        if self._theme_mode != "system":
            return
        self._apply_theme("light" if scheme == Qt.ColorScheme.Light else "dark")

    def _apply_theme(self, scheme: str) -> None:
        app_log.info(f"主题应用: {scheme}")
        """换主题：重设 token、重刷样式表、让自绘控件重绘、同步系统标题栏。"""
        apply_scheme(scheme)
        # 磨砂贴图是按主题缓存的，第一次要用时才生成（约 40ms，含一道无缝模糊）。
        # 那一下会落在首帧绘制上、和入场动画抢时间 —— 所以在换主题这一步就先把
        # 本主题的贴图烘出来，绘制路径上只剩一次 setOpacity + drawTiledPixmap。
        # 强度为 0 时根本不铺颗粒（默认档就是 0），别去烘一张永远用不上的贴图。
        if frost_gain() > 0.0:
            frost_tile(scheme, self.devicePixelRatioF())
        self.brand_header.refresh()
        # 关键一步：只调 setStyleSheet 不够。由样式表绘制背景的控件（QPushButton
        # 这类）会保留旧主题的渲染结果，而调色板驱动的文字色已经更新了 ——
        # 症状就是浅色主题下按钮变成"深底 + 深色字"，看着像只错了一部分。
        # 所以这里给每个控件补一遍 unpolish/polish，强制样式表重新求值。
        style = self.style()
        for widget in self.findChildren(QWidget):
            style.unpolish(widget)
            style.polish(widget)
            widget.update()
        self.update()
        # 发送区的最小高度依赖控件 sizeHint，而 sizeHint 里含着 QSS 定的高度 ——
        # 必须等样式表铺上之后再量，见 _refresh_tx_min_height 的说明。
        self._refresh_tx_min_height()
        # 发送键的位置跟着输入框的字体/内边距走，换主题（字体或 QSS 变了）要重算一次
        self._align_send_button()
        # 接收区着色的蓝是分主题的，换主题要按新颜色重刷一遍
        self._rx_highlighter.refresh()
        if self._titlebar_scheme != scheme:
            self._titlebar_scheme = scheme
            # 全屏时必须是**方角**（圆角会在四角切出四块桌面，见 apply_titlebar_theme）
            apply_titlebar_theme(self, scheme, rounded=not self.isFullScreen())
        # 换主题可能把 DWM 的窗口圆角重新打开（apply_titlebar_theme 的默认就是圆角），
        # 全屏窗口四角一圆就漏出桌面 —— 用户报的"改了设置之后四周有缝隙"就是它。
        # 这里再钉一遍：_enforce_native_fullscreen 会关掉圆角并把物理覆盖核对一次。
        if self.isFullScreen() and not self._expect_normal:
            self._enforce_native_fullscreen()

    def paintEvent(self, event) -> None:
        # 页面背景是纯色，直接填一块就行 —— 没有需要缓存的底图，尺寸变化也不用失效什么
        painter = QPainter(self)
        paint_backdrop(painter, self.rect(), current_scheme())
        painter.end()

    # ---------------------------------------------------------------- 界面搭建

    def _build_ui(self) -> None:
        # 主窗口自己铺背景（paintEvent 里画 backdrop），所以中央控件必须是透明的。
        # 给它 setAutoFillBackground(True) 会用纯底色把背景整块盖住，
        # 结果就是毛玻璃有背景可采样、而窗口本身一片死平，两边对不上。
        self.setAutoFillBackground(True)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        # 卡片自己带了 SHADOW_PAD 的阴影空隙，所以外间距和卡片间距都要相应收小，
        # 否则视觉上会比设定的 GAP 宽出一圈。
        # ⚠️ 上边距是 0：自绘标题栏要像系统标题栏那样**贴着窗口上沿**，
        # 留 12px 会看着像"标题栏浮在页面里"。
        root.setContentsMargins(GAP - SHADOW_PAD, 0,
                                GAP - SHADOW_PAD, GAP - SHADOW_PAD)
        root.setSpacing(GAP - SHADOW_PAD * 2)

        # 自绘标题栏：三颗窗口键 + 可拖拽区。**不进任何会被隐藏的分组**——
        # 接收区展开时页头/配置卡/发送卡都会藏，标题栏必须留着（用户要求
        # "最大化与还原两种状态下保持一致的可点击性和显示效果"）。
        self._caption_bar = CaptionBar(self.windowTitle())
        root.addWidget(self._caption_bar)
        self._caption_bar.btn_min.clicked.connect(self.showMinimized)
        self._caption_bar.btn_max.clicked.connect(self._toggle_fullscreen)
        self._caption_bar.btn_close.clicked.connect(self.close)

        self.brand_header = BrandHeader("串口调试助手")
        root.addWidget(self.brand_header)

        # 主体：左侧收发区 + 右侧串口配置栏（配置固定宽度，收发区吃掉剩余空间）
        body = QHBoxLayout()
        body.setSpacing(GAP - SHADOW_PAD * 2)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        # 卡片自带 5px 阴影空隙，手柄 4px 时视觉间距约 14px，正好
        splitter.setHandleWidth(4)

        self._rx_card = self._new_card("接收区")
        self._rx_card.setMinimumHeight(230)
        # 接收区右上角挂一只实时时钟（精确到秒）。放进卡片标题栏右侧 ——
        # 那里才是真正的"右上角"，而且不压住任何一条日志。
        self.lbl_clock = QLabel()
        self.lbl_clock.setObjectName("rxClock")
        self.lbl_clock.setFont(num_font(11.5))
        self.lbl_clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # 宽高都要钉死：宽是为了秒数跳动时整块不左右抖 —— 必须按**最长的那种
        # 写法**量（带日期是 19 个字符），见 ``_clock_width``；高是因为标题栏里的
        # 按钮是按 box 手工摆的（add_header_button 只 move 不改尺寸），不钉死会
        # 拿到无父控件时的默认高度 480px，一摆就跑出卡片外。
        self.lbl_clock.setFixedSize(
            self._clock_width(bool(customization.get("clock_show_date"))),
            self.lbl_clock.fontMetrics().height() + 2)
        self._rx_card.add_header_button(self.lbl_clock)
        self._rx_card.body.addLayout(self._build_receive_area())

        self._tx_card = self._new_card("发送区")
        # 把发送区的整块内容包成一个容器：最小化时只隐藏这一个控件，
        # 布局就会自动把卡片压到只剩标题栏的高度，不用去手工改一堆控件的可见性
        self._tx_content = QWidget()
        tx_body = QVBoxLayout(self._tx_content)
        tx_body.setContentsMargins(0, 0, 0, 0)
        tx_body.setSpacing(GAP - SHADOW_PAD)
        tx_body.addLayout(self._build_send_area(), 1)
        self._tx_card.body.addWidget(self._tx_content, 1)
        # 发送区展开时只留"一排中文"的高度。先给输入框定下一行的高度，再按
        # 实际最小尺寸把卡片锁住 —— 数字自己数出来，不写死 190 那种拍脑袋的值。
        self._tx_min_height = self._measure_tx_min_height()
        self._tx_card.setMinimumHeight(self._tx_min_height)

        # 折叠按钮放进卡片标题栏右侧 —— Windows 那种把折叠/关闭放进标题栏的做法
        self.btn_minimize = IconButton(GLYPH_COLLAPSE, "最小化发送区",
                                       size=24, glyph_size=12)
        self.btn_minimize.clicked.connect(self._toggle_send_area)
        self._tx_card.add_header_button(self.btn_minimize)

        self._splitter = splitter

        splitter.addWidget(self._rx_card)
        splitter.addWidget(self._tx_card)
        # 拉伸因子只给接收区：多出来的高度全归日志，发送区就停在它的最小高度
        # （一排中文）上。用户拖分割手柄仍然可以把发送区拉大，只是窗口变大时
        # 它不会被带着一起长 —— 这正是"初始只留一排"想要的手感。
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        # 开局的分割位置还要显式给一次：QSplitter 默认按 sizeHint 分，而输入框的
        # sizeHint 是好几行高，光把最小高度压下来没用。让发送区就取最小高度
        # （一排中文），余下的全归接收区。
        splitter.setSizes([self._tx_min_height * 3, self._tx_min_height])
        body.addWidget(splitter, 1)

        self._config_card = self._new_card("串口配置")
        self._config_card.setFixedWidth(310)
        self._config_card.body.addLayout(self._build_config_panel(), 1)
        body.addWidget(self._config_card)

        root.addLayout(body, 1)

        # 底部信息条。不用 QStatusBar：它的 showMessage 临时提示会把普通控件隐藏掉，
        # 放在左下角的设置按钮会跟着闪没。自己排一行，各类信息各居其位。
        bar = QWidget()
        bar.setObjectName("bottomBar")
        bar_row = QHBoxLayout(bar)
        bar_row.setContentsMargins(6, 2, 8, 2)
        bar_row.setSpacing(8)

        # 左下角：设置。
        # hover_fill=False：这颗按钮任何时候都不铺底色，悬停只把齿轮本身提亮。
        # 它独占底栏左端、旁边就是状态文字，浮出圆角方块反而像画糊了一块
        # （深色主题下那块浅底尤其扎眼）。
        self.btn_settings = IconButton(GLYPH_SETTINGS, "设置", size=28, glyph_size=14,
                                       hover_fill=False)
        self.btn_settings.clicked.connect(self._open_settings)
        bar_row.addWidget(self.btn_settings)

        # 中间：临时提示（替代 showMessage 的用法）
        self.lbl_toast = QLabel()
        self.lbl_toast.setFont(ui_font(11.5))
        self.lbl_toast.setContentsMargins(4, 0, 0, 0)
        bar_row.addWidget(self.lbl_toast, 1)

        # 右侧：状态灯、串口状态、收发计数
        self.pulse = PulseDot()
        self.pulse.setToolTip("数据流动指示")
        self.lbl_state = QLabel("未打开")
        self.lbl_state.setFont(ui_font(11.5))
        self.lbl_state.setContentsMargins(2, 0, 12, 0)
        self.lbl_count = QLabel()
        # 计数属于「参数」，VIS 规范指定用 Bahnschrift
        self.lbl_count.setFont(num_font(11.5))
        self.lbl_count.setContentsMargins(0, 0, 2, 0)
        bar_row.addWidget(self.pulse)
        bar_row.addWidget(self.lbl_state)
        bar_row.addWidget(self.lbl_count)
        root.addWidget(bar)

        self._update_counters()
        # 发送区开局姿态由「定制功能 → 默认折叠发送区」决定。默认**开** =
        # 保持老行为（用户点名过"将发送区默认折叠起来"）：这台软件是"只看不发"
        # 的 BMS 读取工具，输入框常年闲着，与其占一排中文的高度，不如把这份
        # 高度让给接收区。关掉则开局就展开。
        # remember=False：此刻还没有"用户拖过的尺寸"，留空 → 第一次点展开
        # 按自然尺寸（一排中文）铺开。
        self._set_send_collapsed(
            bool(customization.get("send_collapsed_startup")), remember=False)

        # 窗口边缘的缩放提示：光标状态（见 _apply_resize_cursor）+ 给所有控件
        # 开 mouseTracking（不开就收不到没按键时的 MouseMove，提示永远不会出现）。
        # 放在最后 —— 这时界面上的控件已经全部建好。
        self._cursor_host: QWidget | None = None
        self._saved_cursors: dict = {}
        self._enable_edge_hover()

        # 启动几秒后自动检查一次更新（不阻塞启动）：发现新版本才弹提示，
        # 用户点过「暂不更新」后本次会话内不再自动弹（手动检查不受影响）。
        # ⚠️ 必须放在 __init__ 的**最后**：上面这些实例属性都就位之后才行
        # （方法插进 init 中间会把后半截 init 主体截断成孤儿语句，实例属性
        # 全部丢失——本次 _cursor_host 就是这么丢的，实测踩过）。
        self._update_dismissed = False
        QTimer.singleShot(4000, self._auto_check_update)

        # 历史接收数据：加载已有尾巴并启动批量落盘定时器（纯内存入队 +
        # 1s 批量写，接收路径零 IO 开销；详见 rx_history 模块注释）。
        import rx_history
        rx_history.load()

    def _auto_check_update(self) -> None:
        """启动时的自动检查更新（静默失败，绝不打扰）。"""
        from updater import UpdateChecker
        from version import VERSION  # noqa: F401

        self._update_dismissed = False
        checker = UpdateChecker(self)
        self._update_checker = checker     # 防止被 GC
        checker.checked.connect(self._on_auto_update_checked)
        checker.failed.connect(lambda _reason: None)
        checker.start()

    def _on_auto_update_checked(self, info: dict) -> None:
        from updater import UpdateDialog, version_tuple
        from version import VERSION
        if version_tuple(info["version"]) <= version_tuple(VERSION):
            return
        if self._update_dismissed:
            return
        app_log.info(f"自动检查更新：发现新版本 v{info['version']}（源 {info.get('source')}）")
        dialog = UpdateDialog(latest=info["version"], current=VERSION,
                              sha256=info.get("sha256"),
                              tag=f"v{info['version']}", auto=True, parent=self)
        dialog.dismissed_session.connect(self._on_update_dismissed)
        dialog.exec()

    def _on_update_dismissed(self) -> None:
        self._update_dismissed = True
        app_log.info("用户选择暂不更新：本次会话内不再自动弹出")

    def _new_card(self, title: str) -> Card:
        card = Card(title)
        self._cards.append(card)
        return card

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(ui_font(10.5))
        return label

    def _clock_width(self, with_date: bool) -> int:
        """时钟标签该钉成多宽（按当前格式里**最长**的写法量）。

        ⚠️ 这里量的是"占位符"而不是当前这一刻的真实文本，原因有两条：
        1) 宽度钉死才不抖 —— 比例字体里 "1" 比 "0" 窄，秒数一跳整块就左右抽动；
        2) 必须按**最长**量。以前这里量的是 "00:00:00"（8 字符），一打开
           「时钟显示年月日」真实文本变成 19 个字符，右边的对齐框只有 8 个字符宽，
           左边那一截被裁掉 —— 界面上只剩 "0 18:32:11"（用户报的排版问题）。
           ⚠️ 该开关现在**默认关**，但占位符仍要按带日期的那一档量 ——
           真实文本的长度是随开关跳的，框宽得随时装得下。
        """
        pattern = "0000-00-00 00:00:00" if with_date else "00:00:00"
        return self.lbl_clock.fontMetrics().horizontalAdvance(pattern) + 2

    def _tick_clock(self) -> None:
        """把接收区右上角的时钟刷到当前时刻，精确到秒。

        时刻（时:分:秒）**恒定显示**；年月日那一段由「定制功能 → 时钟显示年月日」
        控制，**默认关**（用户点名："默认必须包含时、分、秒……该选项默认关闭"）。
        """
        now = datetime.now()
        with_date = bool(customization.get("clock_show_date"))
        # 开关一拨，文本长度会在 8 / 19 字符之间跳，框宽跟着换一档；
        # 标题栏里的控件是按 box 手工摆的（只有尺寸变化不触发重排），所以换宽
        # 之后要显式请卡片重排一次。宽度没变就什么都不做（每秒一次的常态）。
        width = self._clock_width(with_date)
        if width != self.lbl_clock.width():
            self.lbl_clock.setFixedWidth(width)
            self._rx_card.relayout_header()
        self.lbl_clock.setText(f"{now:%Y-%m-%d %H:%M:%S}" if with_date
                               else f"{now:%H:%M:%S}")

    def _tx_line_height(self) -> int:
        """发送输入框里"一行中文"的真实高度。

        不能拿 ``fontMetrics().lineSpacing()`` 估：一行的实际高度取决于这一行
        用到了哪些字形，中文字形来自回退字体，比拉丁字体的行距高一大截
        （实测 23 vs 16）—— 按 16 去算，中文那行的降部会被裁掉一截。
        所以直接让控件排一行中文量出来。
        """
        box = self.txt_tx
        keep = box.toPlainText()
        box.setPlainText("中文")
        line = box.blockBoundingGeometry(box.document().firstBlock()).height()
        box.setPlainText(keep)
        return int(line) + 1              # 向下取整再加 1，给小数字形的降部留余量

    def _tx_one_line_height(self) -> int:
        """发送输入框"恰好一行文字"时的高度（行 + 文档边距 + 内边距与边框）。

        两个地方要共用同一个数：一是发送区的最小高度（展开时就只给一行），
        二是发送键的竖直位置（见 ``_align_send_button``）。
        """
        document = self.txt_tx.document()
        return (self._tx_line_height() + int(document.documentMargin()) * 2
                + TX_EDITOR_CHROME)

    def _align_send_button(self) -> None:
        """把发送键竖直对齐到输入框**第一行文字**的中心。

        为什么直接拿"一行高度的一半"当中心：输入框的 QSS 上下内边距是**不等**的
        （上 14 / 下 6），当初就是按"第一行文字在一行高的框里居中"配出来的
        （实测上下留白 19.5 : 19.5）。所以一行高的盒子的竖直中心，就是第一行文字的
        中心 —— 不用再去猜内边距和字形在行盒里的偏移（中文字形在行盒里天生偏上）。

        ⚠️ 位置得用布局的 **contentsMargins** 给，不能让弹簧去顶：这一列的高度等于
        整个发送区（会随分割条变），弹簧顶出来的位置跟着变，锚不住第一行。
        """
        offset = int(round(self._tx_one_line_height() / 2 - self.btn_send.height() / 2))
        margins = self._tx_rail.layout().contentsMargins()
        top = max(0, offset)
        if margins.top() == top:
            return
        self._tx_rail.layout().setContentsMargins(margins.left(), top, margins.right(),
                                                  margins.bottom())

    def _measure_tx_min_height(self) -> int:
        """发送区展开时的高度：输入框一排中文 + 选项行 + 卡片自己的上下留白。

        先把输入框的最小高度定成一行，再让容器的 ``minimumSizeHint`` 把选项行
        和间距一起算出来 —— 这样以后换字体、改内边距，这里不用跟着改常数。
        """
        self.txt_tx.setMinimumHeight(self._tx_one_line_height())
        margins = self._tx_card.layout().contentsMargins()
        chrome = margins.top() + margins.bottom()
        return chrome + self._tx_content.minimumSizeHint().height()

    def _refresh_tx_min_height(self) -> None:
        """样式表铺好之后再量一次发送区的最小高度。

        ``_measure_tx_min_height`` 是拿控件们的 sizeHint 算的，而 sizeHint 里含着
        QSS 定的高度（开关、数字框的上下限都在样式表里）。``_build_ui`` 里那次量的
        时候样式表还没铺上，会比真实值小几像素 —— 症状是展开态下选项行被挤扁，
        「历史」那个下拉框的上下边框会被切掉一条。

        所以主题铺完补量一次。**值没变就什么都不做**：换主题时不能去动用户已经
        拖好的分割位置。
        """
        height = self._measure_tx_min_height()
        if height == self._tx_min_height:
            return
        self._tx_min_height = height
        # ⚠️ 折叠态下**只更新这个数、不动卡片的最小高度**：卡片此刻的最小高度是
        # TX_COLLAPSED_H，在这里把它抬到展开高度会当场把发送区撑开 ——
        # 开局默认折叠时，紧接着就会跑一次 _refresh_tx_min_height（换主题那步），
        # 那一下正好把"默认折叠"顶回去。展开时用的是 self._tx_min_height，不会丢。
        if not self._tx_collapsed:
            self._tx_card.setMinimumHeight(height)

    def _build_config_panel(self) -> QLayout:
        """右侧串口配置栏：竖排表单，标签在左、控件在右（参考常见串口助手布局）。"""
        col = QVBoxLayout()
        col.setSpacing(12)

        # 打开/关闭串口是这一栏的主操作，放**最顶上一行**。用和「显示时间戳」
        # 同款的拨动开关（用户点名换的）—— 通=已打开、断=已关闭，状态不用猜。
        # ⚠️ 这个开关**不进** ``_config_widgets``：串口打开后其余参数锁死，
        # 但它自己要变成"关闭"，必须始终可拨。
        self.btn_open = Switch("串口开关")
        self.btn_open.setToolTip("打开/关闭串口")
        self.btn_open.toggled.connect(self._on_power_toggled)
        open_row = QHBoxLayout()
        open_row.setSpacing(10)
        open_row.addWidget(self.btn_open)
        open_row.addStretch(1)
        # 包一层容器：接收区全屏时要把这行**整体搬进**接收区右侧的功能键列
        #（配置卡藏了，串口开关不能跟着消失 —— 全屏下还得能关串口），
        # 退出全屏再搬回来。col 保存引用就是为了搬家时能插回原位。
        self._open_row = QWidget()
        self._open_row.setLayout(open_row)
        open_row.setContentsMargins(0, 0, 0, 0)
        self._open_row_home = col
        col.insertWidget(0, self._open_row)

        # 串口选择独占一行，刷新按钮跟在右边
        self.cmb_port = ChevronComboBox()
        # 可编辑：没有物理串口时也能手输端口名，或直接输入 loop:// 做无硬件回环演示
        self.cmb_port.setEditable(True)
        # 宽度由侧栏给出，这里只保底。可编辑下拉框宽度不足会滚动显示，波特率显示错位的老坑
        self.cmb_port.setMinimumWidth(140)
        self.btn_rescan = IconButton(GLYPH_REFRESH, "重新扫描串口", size=30, glyph_size=15)
        # 用 lambda 而不是直接连接：clicked 会带一个 checked=False 传进来，
        # 会把 _refresh_ports 的 announce 形参顶掉。
        self.btn_rescan.clicked.connect(lambda: self._refresh_ports())

        col.addWidget(self._field_label("串口选择"))
        port_row = QHBoxLayout()
        port_row.setSpacing(8)
        port_row.addWidget(self.cmb_port, 1)
        port_row.addWidget(self.btn_rescan)
        col.addLayout(port_row)

        self.cmb_baud = ChevronComboBox()
        self.cmb_baud.setEditable(True)
        self.cmb_baud.addItems(BAUD_PRESETS)
        self.cmb_baud.setCurrentText("115200")
        # 宽度必须给够：可编辑下拉框内部是个行内编辑框，宽度不足时它会滚动以
        # 保证光标可见，把开头那位数字滚出可视区 —— 波特率会显示成错的数
        self.cmb_baud.setMinimumWidth(108)
        self.cmb_baud.setFont(num_font(11.5))

        self.cmb_stopbits = ChevronComboBox()
        self.cmb_stopbits.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cmb_stopbits.addItems(list(STOPBIT_CHOICES))

        self.cmb_bytesize = ChevronComboBox()
        self.cmb_bytesize.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cmb_bytesize.addItems(list(BYTESIZE_CHOICES))
        self.cmb_bytesize.setCurrentText("8")

        self.cmb_parity = ChevronComboBox()
        self.cmb_parity.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cmb_parity.addItems(list(PARITY_CHOICES))

        # 接收区最多保留多少行日志。原来是设置面板里的一项，挪到了这栏 ——
        # 它和波特率、校验位一样是"整机参数"，放在参数栏里挨着更顺手。
        # 不设上限的话，长时间跑的流式数据会把内存吃满。
        # 和波特率同一套交互：预设行数下拉展开选，也可以直接输入任意值
        # （用户点名"需要展开选择的都要像波特率一样展开"）。旧版是数字框，
        # 只能打字或点上下小箭头，没有"展开选择"这一说。
        self.cmb_rx_lines = ChevronComboBox()
        self.cmb_rx_lines.setEditable(True)
        self.cmb_rx_lines.addItems(str(n) for n in RX_LINE_PRESETS)
        self.cmb_rx_lines.setCurrentText(str(DEFAULT_RX_BLOCKS))
        self.cmb_rx_lines.setFont(num_font(11.5))
        self.cmb_rx_lines.setToolTip(
            "接收区最多保留的行数，超出后最早的会被丢弃；可选预设，也可直接输入")
        self.cmb_rx_lines.editTextChanged.connect(self._on_rx_lines_text_changed)

        # 标签固定宽度，控件吃掉剩余宽度 —— 右侧表单的经典排法。
        # 64px 放得下「保留行数」四字，三字标签也因此对齐
        for label, widget in (("波特率", self.cmb_baud), ("停止位", self.cmb_stopbits),
                              ("数据位", self.cmb_bytesize), ("校验位", self.cmb_parity),
                              ("保留行数", self.cmb_rx_lines)):
            row = QHBoxLayout()
            row.setSpacing(10)
            tag = self._field_label(label)
            tag.setFixedWidth(64)
            row.addWidget(tag)
            row.addWidget(widget, 1)
            col.addLayout(row)

        col.addStretch(1)

        return col

    def _build_receive_area(self) -> QHBoxLayout:
        """接收区 = 左侧（选项行 + 日志）+ 右侧竖排功能键。"""
        area = QHBoxLayout()
        area.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(10)

        opts = QHBoxLayout()
        opts.setSpacing(14)
        self.chk_timestamp = Switch("显示时间戳")
        self.chk_timestamp.setChecked(True)
        self.chk_hex_view = Switch("HEX 显示")
        self.cmb_encoding = ChevronComboBox()
        self.cmb_encoding.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cmb_encoding.addItems(ENCODINGS)
        self.cmb_encoding.setMinimumWidth(90)
        self.cmb_encoding.currentTextChanged.connect(self._on_encoding_changed)
        # 标签 + 下拉框绑成一行：设置里要隐藏"编码"时得整行一起藏
        self.encoding_box = LabeledField("编码", self.cmb_encoding)
        opts.addWidget(self.chk_timestamp)
        opts.addWidget(self.chk_hex_view)
        opts.addWidget(self.encoding_box)
        opts.addStretch(1)
        # 包一层容器：接收区全屏时要把这排工具栏**整体**藏掉（用户点名）。
        # ⚠️ setLayout 会把布局内边距设成样式默认值（本机 9px，白撑 18px），
        # 必须紧跟一句归零；竖直方向锁死，别让分割条把它拉高。
        self._rx_opts_row = QWidget()
        self._rx_opts_row.setLayout(opts)
        opts.setContentsMargins(0, 0, 0, 0)
        self._rx_opts_row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        left.addWidget(self._rx_opts_row)

        self.txt_rx = QPlainTextEdit()
        self.txt_rx.setReadOnly(True)
        # 字号来自定制配置（设置面板里那根滑杆，见 _apply_rx_font_size）
        self.txt_rx.setFont(mono_font(customization.display_value("rx_font_size")))
        self.txt_rx.setMaximumBlockCount(self._rx_blocks)
        self.txt_rx.setUndoRedoEnabled(False)
        self.txt_rx.setPlaceholderText("收到的数据会显示在这里")
        # 时间戳和每个采样点的"序号"染蓝（见 ui_highlight）。纯装饰层：
        # 文档里的文字一个字符都没改，保存/搜索/复制走的仍是纯文本。
        self._rx_highlighter = RxHighlighter(self.txt_rx.document())
        self._search_bar = self._build_search_bar()
        # 展开/收起走连续过渡（高度 + 不透明度，120fps）。直接 show/hide 是一帧
        # 跳变，日志框会被猛地顶下去，在高刷屏上很扎眼。
        self._search_reveal = Reveal(self._search_bar, 200, parent=self)
        self._search_bar.hide()
        left.addWidget(self._search_bar)
        left.addWidget(self.txt_rx, 1)

        area.addLayout(left, 1)

        # 右侧功能键竖排 —— 搜索/暂停/清空/保存/全屏，全部带中文 tooltip
        self.btn_search = IconButton(GLYPH_SEARCH, "在接收区中查找（Ctrl+F）",
                                     size=30, glyph_size=15)
        self.btn_search.clicked.connect(self._toggle_search)
        # 定位最近一次异常：从文档末尾往前找最近一行"电压/温度越界或 BMS ERROR"，
        # 滚过去并**持久高亮**那一行 —— 用户："点击后定位到最近一次读取到的
        # 电压有误或温度有误的位置，并将该有误位置高亮显示"。
        self.btn_alert = AlertButton("定位最近一次电压 / 温度异常")
        self.btn_alert.clicked.connect(self._locate_last_error)
        self.btn_pause = IconButton(GLYPH_PAUSE, "暂停滚动（空格）", size=30, glyph_size=15)
        self.btn_pause.clicked.connect(self._toggle_pause)
        # 复制全部：把接收区当前显示的内容（含每条的时间戳）整段抄进剪贴板。
        # 光标保持箭头（用户点名）：它指着眼前的数据，不是"要去别处"的动作。
        self.btn_copy_all = IconButton(GLYPH_COPY, "复制当前一轮数据（含时间戳）",
                                       size=30, glyph_size=15, cursor="arrow")
        self.btn_copy_all.clicked.connect(self._copy_all_receive)
        self.btn_clear = IconButton(GLYPH_CLEAR, "清空接收区", size=30, glyph_size=15)
        self.btn_clear.clicked.connect(self._clear_receive)
        self.btn_save = IconButton(GLYPH_SAVE, "保存接收日志", size=30, glyph_size=15)
        self.btn_save.clicked.connect(self._save_log)
        self.btn_expand = IconButton(GLYPH_EXPAND, "接收区全屏显示（Esc 退出）",
                                     size=30, glyph_size=15)
        self.btn_expand.clicked.connect(self._toggle_expand)

        rail = QVBoxLayout()
        rail.setSpacing(10)
        rail.addStretch(1)
        for button in (self.btn_search, self.btn_alert, self.btn_pause,
                       self.btn_copy_all, self.btn_clear, self.btn_save,
                       self.btn_expand):
            rail.addWidget(button, 0, Qt.AlignmentFlag.AlignRight)
        rail.addStretch(1)
        # 包一层容器，把整列当成一个控件交给布局（间距、对齐都只算一次）
        self._rx_rail = QWidget()
        self._rx_rail.setLayout(rail)
        area.addWidget(self._rx_rail)
        return area

    def _build_search_bar(self) -> QWidget:
        """接收区搜索条。放在选项行和日志之间 —— 浏览器里那套 find bar 的位置，
        不压在内容上，也不挡滚动条。"""
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        row.addWidget(IconLabel(GLYPH_SEARCH, 13))

        self.edit_search = SearchEdit()
        # 全局样式表里没有裸 QLineEdit 规则：串口名/波特率那两个可编辑下拉框里各藏着
        # 一个 QLineEdit，裸规则会连它们一起改掉。所以这里认 objectName。
        self.edit_search.setObjectName("searchEdit")
        self.edit_search.setPlaceholderText("在接收区中查找")
        self.edit_search.textChanged.connect(self._on_search_text)
        self.edit_search.next_requested.connect(lambda: self._find_next(True))
        self.edit_search.prev_requested.connect(lambda: self._find_next(False))
        row.addWidget(self.edit_search, 1)

        self.lbl_matches = QLabel("")
        self.lbl_matches.setObjectName("searchCount")
        self.lbl_matches.setFont(num_font(11))
        # 定宽：否则计数从 "1/9" 变成 "1/100" 时右边那几个按钮会左右挪
        self.lbl_matches.setMinimumWidth(66)
        self.lbl_matches.setAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.lbl_matches)

        self.btn_find_prev = IconButton(GLYPH_CHEVRON_UP, "上一个匹配（Shift+Enter）",
                                        size=26, glyph_size=12)
        self.btn_find_prev.clicked.connect(lambda: self._find_next(False))
        self.btn_find_next = IconButton(GLYPH_CHEVRON_DOWN, "下一个匹配（Enter）",
                                        size=26, glyph_size=12)
        self.btn_find_next.clicked.connect(lambda: self._find_next(True))
        self.btn_find_close = IconButton(GLYPH_CLOSE, "关闭搜索（Esc）",
                                         size=26, glyph_size=12)
        self.btn_find_close.clicked.connect(self._close_search)
        for button in (self.btn_find_prev, self.btn_find_next, self.btn_find_close):
            row.addWidget(button)
        return bar

    def _build_send_area(self) -> QHBoxLayout:
        """发送区 = 左侧（输入框 + 选项行）+ 右侧竖排功能键（发送）。"""
        area = QHBoxLayout()
        area.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(10)

        self.txt_tx = QPlainTextEdit()
        # objectName 是给 QSS 用的：这个框的上下内边距和接收区不一样，见 ui_theme 里
        # #txEditor 那条规则（第一行文字要在框内垂直居中）。
        self.txt_tx.setObjectName("txEditor")
        self.txt_tx.setFont(mono_font(10.5))
        # 这里**故意不设最大高度**。原来卡在 118px，用户把分割条往上拖时，多出来的
        # 高度在输入框到顶之后只能去撑下面的选项行 —— 实测下拉框被拉到 122px 高、
        # 整排开关跟着往上跳。用户要的是"拉伸输入框、按钮别动"，所以上限交给分割条
        # 自己（接收区最小 230）去兜。
        self.txt_tx.setPlaceholderText("要发送的内容，回车发送；HEX 模式下写 AA 01 FF")
        left.addWidget(self.txt_tx, 1)

        opts = QHBoxLayout()
        opts.setSpacing(14)
        self.chk_hex_send = Switch("HEX 发送")
        self.chk_append_crlf = Switch("换行")
        self.chk_append_crlf.setChecked(True)
        self.chk_timer_send = Switch("定时发送")
        self.chk_timer_send.toggled.connect(self._on_timer_toggled)

        self.spin_interval = ChevronSpinBox()
        self.spin_interval.setRange(TIMER_MIN_MS, 600000)
        self.spin_interval.setValue(1000)
        self.spin_interval.setSuffix(" ms")
        self.spin_interval.setMinimumWidth(108)
        self.spin_interval.setFont(num_font(11.5))
        self.spin_interval.valueChanged.connect(self._on_interval_changed)

        self.cmb_history = ChevronComboBox()
        self.cmb_history.setMinimumWidth(170)
        self.cmb_history.setToolTip("点选可回填到发送框")
        self.cmb_history.addItem("— 发送历史 —")
        self.cmb_history.activated.connect(self._on_history_picked)
        self.history_box = LabeledField("历史", self.cmb_history)

        opts.addWidget(self.chk_hex_send)
        opts.addWidget(self.chk_append_crlf)
        opts.addWidget(self.chk_timer_send)
        opts.addWidget(self.spin_interval)
        opts.addStretch(1)
        opts.addWidget(self.history_box)
        # 选项行必须"长不高"。它作为子布局塞进竖排时，默认会被多出来的高度撑开 ——
        # 实测分割条拖到 320px 时这一行被撑到 122px 高，里面的开关跟着往下滑。
        # 包一层竖排固定（Fixed）的容器，高度就锁在 sizeHint 上：分割条怎么拖，
        # 它都待在原地不动。
        opts_row = QWidget()
        opts_row.setLayout(opts)
        # setLayout() 会把布局的内边距设成样式默认值（本机 9px），白撑 18px 高度。
        # 这一行本来就是贴着卡片边排的，必须清零 —— 不清零的话发送区最小高度会从
        # 178 虚涨到 196，多出一截空档。
        opts.setContentsMargins(0, 0, 0, 0)
        opts_row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._tx_opts_row = opts_row
        left.addWidget(opts_row)

        area.addLayout(left, 1)

        # 右侧功能键：发送。同样不带底色，只有悬停/按下时的浅底反馈。
        self.btn_send = IconButton(GLYPH_SEND, "发送（Enter，Ctrl+Enter 同效）",
                                   size=40, glyph_size=20, variant="flat")
        self.btn_send.clicked.connect(self._on_send)
        # 发送键摆在**输入框第一行文字**的高度（见 _align_send_button），所以这一列是
        # "顶边留一段间距 + 按钮 + 底下弹簧"。它因此跟着输入框的顶边走：拖分割条时
        # 输入框变高、第一行一起上移，按钮始终贴着那一行 —— 用户要的
        # "拉伸输入框、按钮别动"针对的是底下那排开关，它们仍然钉死在原地。
        rail = QVBoxLayout()
        rail.addWidget(self.btn_send, 0, Qt.AlignmentFlag.AlignRight)
        rail.addStretch(1)
        self._tx_rail = QWidget()
        self._tx_rail.setLayout(rail)
        self._align_send_button()
        area.addWidget(self._tx_rail)
        return area

    def _wire_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+Return"), self.txt_tx, self._on_send)
        QShortcut(QKeySequence("Ctrl+L"), self, self._clear_receive)
        QShortcut(QKeySequence("Ctrl+F"), self, self._toggle_search)
        QShortcut(QKeySequence("Escape"), self, self._on_escape)
        QShortcut(QKeySequence("F11"), self, self._toggle_expand)

    # ------------------------------------------------------------ 滚动暂停

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self._refresh_pause_ui()
        # 暂停/继续必须有看得见的反馈：按钮字形只是换了枚小图标，很容易被忽略，
        # 用户会以为"按了没反应"。所以两头都在底栏说一句。
        if self._paused:
            # 暂停时把发送区折起来：接收区多吃一截高度，看数据更清爽。
            # 先折叠（视口最终高度定了）再做时间戳定位，7 行才是准的。
            self._tx_expand_on_resume = not self._tx_collapsed
            if self._tx_expand_on_resume:
                self._toggle_send_area()
            # 显示时间戳/HEX/编码那排工具栏一并折叠（用户点名）
            self._opts_visible_on_resume = self._rx_opts_row.isVisible()
            self._rx_opts_row.hide()
            self._snap_to_round_start()
            self._toast("滚动已暂停，数据仍在后台接收", 3000)
        else:
            self._toast("已恢复滚动", 2000)
            # 暂停时折叠的发送区**先**还原（视口最终尺寸定了），再刷帧追底
            # —— 反过来的话追底会被随后的高度变化作废（实测 119/126）。
            # 用户暂停前就折叠的，不动。
            if self._tx_expand_on_resume and self._tx_collapsed:
                self._toggle_send_area()
            self._tx_expand_on_resume = False
            if self._opts_visible_on_resume:
                self._rx_opts_row.show()
            self._opts_visible_on_resume = False
            # 恢复 = 把暂停期间攒下的帧一次性刷进来再追底 —— 视图直接落在
            # 最新读取的数据上（少这一步，新数据全堆在看不见的队列里）。
            self._flush_paused_frames()

    def _refresh_pause_ui(self) -> None:
        self.btn_pause.set_glyph(GLYPH_PLAY if self._paused else GLYPH_PAUSE)
        self.btn_pause.set_active(self._paused)
        self.btn_pause.setToolTip("继续滚动（空格）" if self._paused
                                  else "暂停滚动（空格）")

    def _scroll_frozen(self) -> bool:
        """视图要不要"钉住"。暂停键是一类，搜索挑中某条之后是另一类。"""
        return self._paused or self._search_pinned

    def _top_block_number(self) -> int:
        """视口最上面那一行的块号 —— 暂停定位拿它当锚点。

        用 ``firstVisibleBlock()`` 而不是 ``cursorForPosition(QPoint(0,0))``：
        后者在视口顶恰压块边界时归属有歧义，还会被 contentOffset 的几像素
        偏差带着跑（实测小滚动范围下能偏出两块）。
        """
        return self.txt_rx.firstVisibleBlock().blockNumber()

    def _scroll_block_to_top(self, number: int) -> None:
        """把第 ``number`` 块滚到视口顶部（**绝对**定位）。

        锚点必须落在**块号**上而不是滚动条的像素值上：``maximumBlockCount``
        裁掉开头的块之后，同一像素值对应的是别的内容，视图会自己往上漂。
        滚动条的目标值就是块的**文档坐标 y** —— 别加 contentOffset（那是
        视口相对坐标，叠加当前滚动位置后会把视图滚回最顶上）。
        """
        block = self.txt_rx.document().findBlockByNumber(number)
        if not block.isValid():
            return
        y = self.txt_rx.blockBoundingGeometry(block).top()
        bar = self.txt_rx.verticalScrollBar()
        bar.setValue(max(0, int(round(y))))

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress and obj is self.txt_tx:
            # 回车 = 发送。发送区里九成以上的动作就是"敲一条、发出去"，还要把手从
            # 键盘挪到鼠标上去点那颗按钮，纯属白费事。
            # Shift+回车留给换行（想发多行还能发），Ctrl+回车那条老快捷键照旧。
            # 按住不放不重复发（auto-repeat 全吃掉），否则手一压就是一串包。
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    return super().eventFilter(obj, event)
                # 空输入框 + 串口没开时回车什么都不做，否则会白弹一个警告框
                # （空框按回车多半只是误触，不是想发一个空包）
                if (not event.isAutoRepeat()
                        and (self._worker is not None or self.txt_tx.toPlainText())):
                    self._on_send()
                return True

        # 空格点按 = 暂停键，一下暂停、再一下继续。
        # 忽略 auto-repeat，否则按住空格会在暂停/继续之间疯狂翻转。
        # 焦点在可编辑文本框里时不拦截 —— 那个空格是要发出去的内容。
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space:
            if not event.isAutoRepeat() and not self._typing_text():
                self._toggle_pause()
                return True

        # 无边框窗口**没有系统的缩放边框**（FramelessWindowHint 会把 WS_THICKFRAME
        # 去掉，实测 GWL_STYLE 里它和 WS_CAPTION 一起没了），窗口边缘那一圈得自己接：
        # 落在边缘带里的左键按下交给系统的 `startSystemResize`。
        # ⚠️ 只能挂在应用级过滤器上 —— 中央控件铺满整个客户区，事件根本到不了
        # MainWindow 自己身上（父窗口的 mousePressEvent 永远收不到）。
        if (event.type() == QEvent.Type.MouseButtonPress
                and isinstance(obj, QWidget) and obj.window() is self
                and event.button() == Qt.MouseButton.LeftButton):
            edges = self._resize_edges(obj.mapTo(self, event.position().toPoint()))
            if edges:
                handle = self.windowHandle()
                if handle is not None and handle.startSystemResize(edges):
                    return True

        # 边缘/四角的**光标提示**：无边框窗口把系统的缩放边框去掉了，鼠标移到
        # 边缘时没有任何提示，用户不会知道那里能拖（用户点名："需像其他 Windows
        # 软件一样显示边框拖拽提示"）。这里按落在哪条边/哪个角换对应的缩放光标。
        # ⚠️ 只在**普通态**给：_resize_edges 已经把全屏/最大化挡在外面（那时四边
        # 都在屏幕外）—— 顺带也就满足了"从最大化退出后才出现"。
        if (event.type() == QEvent.Type.MouseMove
                and isinstance(obj, QWidget) and obj.window() is self):
            edges = self._resize_edges(obj.mapTo(self, event.position().toPoint()))
            if edges:
                self._apply_resize_cursor(obj, edges)
                # ⚠️ 命中边缘必须**吃掉**这个事件。QWidget 默认的 mouseMoveEvent
                # 是 event->ignore()，不拦的话事件会继续往下投递、并沿父链上传，
                # 同一个鼠标位置于是被处理两次 —— 第二次（父窗口）走的还是同一条
                # 分支，把刚设在子控件上的缩放光标当成"换了个宿主"撤掉，光标
                # 立刻变回箭头（真机实测：先 SizeHorCursor，随后就没了）。
                return True
            self._release_resize_cursor()
        return super().eventFilter(obj, event)

    def _apply_resize_cursor(self, widget: QWidget, edges) -> None:
        """按鼠标落在哪条边/哪个角，给**鼠标所在的那个控件**换上缩放光标。

        两个坑：
        - 光标只能设在鼠标当前所在的那个控件上（设在窗口本身上不生效）——
          中央控件铺满整个客户区，鼠标其实一直在它（或它的 viewport）上面；
        - 离开边缘必须把光标**还原成原来那个**，不能一律 unsetCursor：文本框
          viewport 原本是 I 形光标，unset 之后会退回箭头形（真机踩过）。
          所以进来时把控件自己的 cursor 存一份，换走时还回去。
        """
        shape = self._edge_cursor_shape(edges)
        if shape is None:
            self._release_resize_cursor()
            return
        if self._cursor_host is not None and self._cursor_host is not widget:
            self._release_resize_cursor()
        if widget not in self._saved_cursors:
            self._saved_cursors[widget] = widget.cursor()
        widget.setCursor(shape)
        self._cursor_host = widget

    def _release_resize_cursor(self) -> None:
        """把缩放光标撤掉，恢复控件自己原本的光标。"""
        host = self._cursor_host
        if host is None:
            return
        original = self._saved_cursors.pop(host, None)
        if original is not None:
            host.setCursor(original)
        else:
            host.unsetCursor()
        self._cursor_host = None

    @classmethod
    def _edge_cursor_shape(cls, edges):
        """边缘组合 → 系统缩放光标形状（不是边缘就是 None）。

        ⚠️ 四个角（两条边的组合）必须排在单边前面先比，否则永远匹配不到。
        """
        for want, shape in cls._EDGE_CURSORS:
            if edges == want:
                return shape
        return None

    def _enable_edge_hover(self) -> None:
        """给窗口内所有控件开 mouseTracking —— 边缘光标提示的前提。

        ⚠️ 不开的话，**没按住键时的鼠标移动根本不产生 MouseMove 事件**，
        应用级过滤器也照样收不到，光标提示永远不会出现。中央控件铺满整个
        客户区，边缘那圈事件其实发生在它（或它的 viewport）身上，所以得
        逐个开。纯几何判断，开销可以忽略。
        """
        for widget in [self] + self.findChildren(QWidget):
            widget.setMouseTracking(True)

    def _resize_edges(self, pos: QPoint):
        """窗口内坐标落在哪几条边上（不在边缘带里就是空）。

        只对**普通态**窗口开口子：全屏/最大化时四边都在屏幕外，那条带子既没有
        意义，还会把"贴着屏幕左缘点一下"变成一次缩放。
        """
        if not (self.isVisible() and not self.isFullScreen()
                and not self.isMinimized() and not self.isMaximized()):
            return Qt.Edge(0)
        band = RESIZE_BAND
        edges = Qt.Edge(0)
        if pos.x() <= band:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - band:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= band:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - band:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _typing_text(self) -> bool:
        focused = QApplication.focusWidget()
        if focused is self.txt_tx:
            return True
        return isinstance(focused, QLineEdit) and focused.hasFocus()

    # ------------------------------------------------------------ 接收区搜索

    # 命中数量上限。日志最多几千行，真全扫再全量高亮会拖慢输入；到闸口就停，
    # 计数显示成 "N+"。实际使用上没人会去数第 500 个匹配。
    SEARCH_MATCH_LIMIT = 500

    def _toggle_search(self) -> None:
        if self._search_reveal.expanded:
            self._close_search()
        else:
            self._open_search()

    def _open_search(self) -> None:
        self._search_reveal.to(True)
        self.btn_search.setToolTip("关闭搜索（Esc）")
        self.edit_search.setFocus()
        # 已经有关键词就全选，直接接着改；没有就等用户输入
        self.edit_search.selectAll()
        self._refresh_matches()

    def _close_search(self) -> None:
        # 收起是连续的：先从当前高度压到 0 再隐藏（Reveal 内部做），
        # 所以这里不能直接 hide()，否则那 200ms 的过渡就没了。
        self._search_reveal.to(False)
        self.btn_search.setToolTip("在接收区中查找（Ctrl+F）")
        self._reset_matches()
        self.txt_rx.setFocus()

    def _reset_matches(self) -> None:
        self._matches = []
        self._match_index = -1
        self._match_term = ""
        self._match_truncated = False
        self._search_pinned = False
        self._set_search_selections([])
        self.lbl_matches.setText("")

    def _on_search_text(self, _text: str) -> None:
        # 边打边搜，但等一下再扫 —— 见 _search_timer 的注释
        self._search_timer.start()

    def _refresh_matches(self) -> None:
        """重扫全部命中并刷新高亮。

        数据到达时也要重扫：新行是追加在末尾的，但 ``maximumBlockCount`` 截掉旧行
        之后所有位置都会整体前移，缓存下来的区间会全部失效。
        """
        if not self._search_bar.isVisible():
            return
        term = self.edit_search.text()
        if not term:
            self._reset_matches()
            return

        document = self.txt_rx.document()
        matches: list[tuple[int, int]] = []
        truncated = False
        cursor = QTextCursor(document)
        while True:
            cursor = document.find(term, cursor)
            if cursor.isNull():
                break
            matches.append((cursor.selectionStart(), cursor.selectionEnd()))
            if len(matches) >= self.SEARCH_MATCH_LIMIT:
                # 到闸口了：后面还有没有，得多看一眼才知道计数要不要带 "+"
                truncated = not document.find(term, cursor).isNull()
                break

        # 换了关键词就从头数起（下面的钉住逻辑再按有无命中决定）
        if term != self._match_term:
            self._match_term = term
            self._match_index = 0
        self._matches = matches
        self._match_truncated = truncated
        self._match_index = (min(self._match_index, len(matches) - 1)
                             if matches else -1)
        # 有命中就钉住视图：搜索期间视图跟着**匹配**走，不跟数据走。
        # 否则串口一直在流，每次找到的那条都会被立刻甩到底部去，搜索等于白做。
        self._search_pinned = bool(matches)
        self._render_matches()

    def _render_matches(self) -> None:
        document = self.txt_rx.document()
        selections = []
        for position, (start, end) in enumerate(self._matches):
            cursor = QTextCursor(document)
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setBackground(match_fill(position == self._match_index))
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = fmt
            selections.append(selection)
        self._set_search_selections(selections)

        if not self._matches:
            self.lbl_matches.setText("无匹配")
            return
        suffix = "+" if self._match_truncated else ""
        self.lbl_matches.setText(
            f"{self._match_index + 1}/{len(self._matches)}{suffix}")
        self._scroll_to_match()

    def _find_next(self, forward: bool) -> None:
        """跳到下一个 / 上一个匹配。到头会绕回另一头。"""
        if not self._matches:
            self._refresh_matches()          # 可能只是防抖还没到点
            if not self._matches:
                return
        step = 1 if forward else -1
        self._match_index = (self._match_index + step) % len(self._matches)
        self._render_matches()

    def _scroll_to_match(self) -> None:
        """把当前匹配滚进视野。用折叠光标而不是选中 —— 选中会盖掉
        extraSelections 里那一档更重的底色，当前匹配反而看不出是哪一条。"""
        if not self._matches or self._match_index < 0:
            return
        start = self._matches[self._match_index][0]
        cursor = QTextCursor(self.txt_rx.document())
        cursor.setPosition(start)
        self.txt_rx.setTextCursor(cursor)
        self.txt_rx.ensureCursorVisible()

    # ------------------------------------------------------------ 接收区全屏

    def _on_escape(self) -> None:
        # 接收区展开优先：那是"更重"的状态，用户按 Esc 期待的是退出它。
        # 其次是窗口全屏（软件本身盖任务栏的全屏）；搜索条最后收。
        if self._expanded:
            self._collapse_receive()
            return
        if self.isFullScreen():
            # 普通几何平时由 _remember_normal_geometry 持续记忆（在全屏状态
            # 调 saveGeometry 存下来的是满屏矩形，不能用），直接退出即可
            # ⚠️ 先把"想要全屏"降下来：启动时排的那两次延迟核对可能还没跑，
            # 不降的话它会把用户刚退出的窗口又拽回全屏。
            self._want_fullscreen = False
            self._expect_normal = True
            self.showNormal()
            # ⚠️ 必须**自己**把几何还回去：有系统边框时 ShowWindow(SW_SHOWNORMAL)
            # 会替我们把窗口摆回全屏前的位置，窗口一改成无边框（WS_POPUP 系）
            # Windows 就不再记这个"普通态矩形"了 —— 实测退全屏后窗口仍停在
            # 满屏尺寸上。平时一直在记的 _normal_geometry 正好用在这儿。
            if self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)
            self._expect_normal = False
            self._set_corner_preference(int(self.winId()), round_=True)
            return
        if self._search_bar.isVisible():
            self._close_search()

    def _toggle_fullscreen(self) -> None:
        """自绘标题栏中间那颗键：全屏（铺满+藏任务栏）⇄ 普通窗口。

        ⚠️ 往回收的时候**复用 ``_on_escape`` 那条链**，不要自己写 showNormal：
        接收区展开是"更重"的状态（窗口也是全屏、但页头/配置卡/发送卡都藏着），
        展开时直接 showNormal 会留下一个"窗口是普通态、卡片却全不见了"的破页面。
        Esc 的优先级顺序（展开 → 全屏 → 搜索条）就是为这件事定的。
        """
        if self._expanded or self.isFullScreen():
            self._on_escape()
        else:
            self.startup_fullscreen()
        self._refresh_caption_ui()

    def _refresh_caption_ui(self) -> None:
        """标题栏中间那颗键跟着窗口状态换面孔（最大化 ⇄ 还原）+ 刷新提示语。"""
        bar = getattr(self, "_caption_bar", None)
        if bar is not None:
            bar.set_maximized(self.isFullScreen())

    def _remember_normal_geometry(self) -> None:
        """窗口处于**普通态**时持续记忆几何 —— 退出全屏/还原展开要靠它。

        ⚠️ 记的是 ``geometry()``（**客户区**矩形）而不是 ``saveGeometry()``：
        无边框窗口被 DWM 补了一圈不可见边框之后，"保存时边框还没生效、还原时已经
        生效"会让 ``restoreGeometry`` 平白多出一个 (1, 17) 的偏移（真机实测，
        收起接收区展开时窗口会跳一下）。客户区矩形自己就是精确值，直接
        ``setGeometry`` 不会漂。
        """
        if (self.isVisible() and not self.isFullScreen()
                and not self.isMaximized() and not self._expect_normal):
            self._normal_geometry = self.geometry()

    def moveEvent(self, event) -> None:
        self._remember_normal_geometry()
        super().moveEvent(event)

    def resizeEvent(self, event) -> None:
        self._remember_normal_geometry()
        super().resizeEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            # 用户点了**最大化**按钮：Windows 的最大化永远给任务栏让位，
            # 物理上盖不住它 —— 这个软件是全屏应用，直接转成真全屏
            # （物理像素铺满 + 任务栏隐藏，这正是用户要的"最大化程度"）。
            # singleShot 派发：不在状态切换的回调里再切状态（会重入）。
            if (self.windowState() & Qt.WindowState.WindowMaximized
                    and not self.isFullScreen() and not self._expect_normal):
                QTimer.singleShot(0, self.startup_fullscreen)
            # 全屏 ⇄ 普通的每一次切换都会走到这儿，标题栏那颗键的面孔在这里统一刷
            self._refresh_caption_ui()
        super().changeEvent(event)

    def event(self, event) -> bool:
        # 全屏期间每次几何变化都核对物理覆盖：Qt 偶尔会用**逻辑几何**把全屏
        # 窗口再排一次版，非整数缩放（175% 等）下换算差 1~2 物理 px，四周就
        # 漏出壁纸缝。_enforce_native_fullscreen 里有"已精确覆盖就不再动"
        # 的收敛保护，这里不会死循环。
        if (event.type() in (QEvent.Type.Resize, QEvent.Type.Move)
                and self.isFullScreen() and not self._expect_normal
                and not self._in_enforce):
            self._in_enforce = True
            try:
                self._enforce_native_fullscreen()
            finally:
                self._in_enforce = False
        return super().event(event)

    def _set_corner_preference(self, hwnd: int, round_: bool) -> None:
        """Win11 的窗口圆角在全屏时会在四角露出壁纸 —— 全屏时明确关掉。"""
        try:
            import ctypes
            # DWMWA_WINDOW_CORNER_PREFERENCE=33，DWMWCP_DEFAULT=0/DONOTROUND=1
            pref = ctypes.c_int(0 if round_ else 1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), 33, ctypes.byref(pref),
                ctypes.sizeof(pref))
        except Exception:
            pass            # Win10 没有这个属性，静默忽略

    def _enforce_native_fullscreen(self):
        """游戏式兜底：把窗口精确对到**所在显示器**的物理矩形，帮 shell 认全屏。

        Windows 只在前台窗口无边框且覆盖整台显示器（物理像素级）时才藏
        任务栏。非整数缩放（如 150%/175%）下 Qt 的逻辑几何换算到物理可能
        差出 1px —— 就差这 1px，shell 判定"这不是全屏应用"，任务栏照画、
        四周漏缝。这里按 MonitorFromWindow 拿**本窗口所在显示器**的物理
        矩形显式 SetWindowPos（GetSystemMetrics 只给主屏，多显示器会错）。
        ⚠️ 千万别用 TOPMOST：窗口一带 TOPMOST，shell 的全屏检测直接失效，
        任务栏反而永远显示（上一版兜底就是这么把事情搞砸的）。

        返回值：``((左, 上, 右, 下), 显示器矩形)``（物理像素），非 win32 或无
        句柄时返回 None —— 供 ``_verify_fullscreen`` 把"到底铺没铺满"记进日志。
        """
        # Qt 层重新声明全屏：连续的 showNormal → showFullScreen 存在**落地
        # 竞态**（前者的状态延迟落地会覆盖后者，offscreen 测试必现），不重新
        # 声明的话 isFullScreen 会悄悄变 False、任务栏跟着回来。
        # ⚠️ 用**精确赋值**而不是 `windowState() | FullScreen`：用户刚点过最大化
        # 时 Maximized 位还在，OR 完两位并存，shell 会按"最大化"排版（给任务栏
        # 让位、给窗口边框留几像素）—— 症状就是"点了最大化，四周还能看到桌面"。
        # 精确赋成 FullScreen 顺手把 Maximized 位清掉。
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        if sys.platform != "win32":
            return None
        import ctypes
        from ctypes import wintypes
        hwnd = int(self.winId())
        if not hwnd:
            return None
        user32 = ctypes.windll.user32
        # 64 位下必须显式声明句柄类型：ctypes 默认把 int 按 32 位传，
        # HWND 高位被截断后 API 静默失败（实测踩过）。
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SetWindowPos.restype = wintypes.BOOL

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong),
                        ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT),
                        ("dwFlags", ctypes.c_ulong)]

        user32.MonitorFromWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR,
                                           ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        monitor = user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)  # NEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rc = info.rcMonitor
            left, top = rc.left, rc.top
            width, height = rc.right - left, rc.bottom - top
        else:                                   # 拿不到就退回主屏
            left = top = 0
            width = user32.GetSystemMetrics(0)      # SM_CXSCREEN，物理像素
            height = user32.GetSystemMetrics(1)     # SM_CYSCREEN

        # 收敛保护：已经精确覆盖本显示器就别再动 —— event 钩子每次几何
        # 变化都会进来，没有这条会 SetWindowPos ↔ Resize 死循环
        user32.GetWindowRect.argtypes = [wintypes.HWND,
                                         ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        rect = wintypes.RECT()
        user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
        self._set_corner_preference(hwnd, round_=False)
        target = (left, top, left + width, top + height)
        if (rect.left, rect.top, rect.right, rect.bottom) == target:
            return (rect.left, rect.top, rect.right, rect.bottom), target
        # SWP_NOZORDER=0x0004（z 序交给 Qt 的全屏态，绝不能碰 TOPMOST）
        user32.SetWindowPos(wintypes.HWND(hwnd), None,
                            left, top, width, height, 0x0004)
        # 落定后再量一次，记进日志的才是"真实结果"而不是"我下发的目标"
        user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
        return (rect.left, rect.top, rect.right, rect.bottom), target

    def _verify_fullscreen(self) -> None:
        """延迟核对：该全屏却没铺满本显示器 → 重新钉一遍，并把结果记进日志。

        为什么需要它：Windows 的"最大化 / 全屏"切换带系统动画，shell 也可能在
        动画落定那一刻用**最大化**的排版把我们的全屏覆盖掉 —— 症状就是用户报的
        "点了最大化，窗口四周还能看到桌面"。动画落定后再核一次，没铺满就重新钉。

        只在"用户想要全屏"（``_want_fullscreen``）且不在程序性退普通窗口的窗口期
        （``_expect_normal``）才动手：用户按 Esc 主动退出、或收起接收区展开回到普通
        窗口之后，这里绝不能再把人拽回全屏。
        """
        if not self._want_fullscreen or self._expect_normal:
            return
        result = self._enforce_native_fullscreen()
        if result is not None:
            window_rect, monitor_rect = result
            app_log.info(f"全屏核对：窗口 {window_rect} / 显示器 {monitor_rect}")

    def startup_fullscreen(self) -> None:
        """软件启动即**整窗全屏**（showFullScreen 连任务栏一起盖掉）。

        注意这是**窗口级**全屏：页头、配置卡、发送区都在，只是窗口本身
        覆盖整台显示器、任务栏隐藏 —— "接收区展开"是另一件事，用户自己
        点放大按钮控制。物理像素对齐见 ``_enforce_native_fullscreen``。
        普通几何不用在这里存：进入前它一直被 _remember_normal_geometry 记着。
        """
        self._want_fullscreen = True
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._enforce_native_fullscreen()
        # 动画落定后再核两次：Windows 最大化/全屏切换有系统动画，shell 有可能在
        # 动画结束时用最大化的排版盖掉全屏（"点了最大化四周还有桌面"）。核不上
        # 就重新钉一遍，并把窗口/显示器矩形写进日志（logs/ 里能查）。
        for delay in (180, 600):
            QTimer.singleShot(delay, self._verify_fullscreen)

    def apply_startup_state(self) -> None:
        """按「定制功能 → 启动时最大化」摆起手姿态（入口在 main.py，show() 之后调一次）。

        开（默认）＝ ``startup_fullscreen()``：整窗全屏、盖住任务栏 —— 老行为。
        关 ＝ 就停在普通窗口上；用户想全屏随时点标题栏中间那颗键，或者按 Esc/拖边缘。

        ⚠️ 关掉这条分支**必须**把 ``_want_fullscreen`` 降下来：延迟核对
        （``_verify_fullscreen``）拿它分辨"这次没铺满"是用户主动退出还是被 shell
        吃掉了 —— 留成 True 的话，窗口一旦有什么几何变化就会被钉回全屏。
        """
        if customization.get("startup_maximized"):
            self.startup_fullscreen()
        else:
            self._want_fullscreen = False
            self._refresh_caption_ui()

    def _apply_startup_maximized(self) -> None:
        """设置面板里拨了「启动时最大化」→ 当场按新选择摆一次窗口。

        不这么做的话用户得重启才看得到反馈（这个开关字面上只管"下次打开"，
        但拨完能立刻看到变化更省事），关掉时复用 ``_on_escape`` 那条链 ——
        它顺手把"想要全屏"降下来，且展开态优先收展开。
        """
        if customization.get("startup_maximized"):
            if not self.isFullScreen() and not self._expanded:
                self.startup_fullscreen()
        else:
            self._want_fullscreen = False
            if self.isFullScreen() or self._expanded:
                self._on_escape()
        self._refresh_caption_ui()

    def _toggle_expand(self) -> None:
        if self._expanded:
            self._collapse_receive()
        else:
            self._expand_receive()

    def _expand_receive(self) -> None:
        """接收区展开：藏起品牌页头、配置区和发送区，日志区独占窗口。

        ⚠️ **不做几何动画、也不在展开态碰 setGeometry**：fullscreen 窗口上
        的 setGeometry 会把窗口悄悄打回普通态（实测"退出一次之后再全屏
        就失效、铺不满屏幕"的根源）。窗口全屏是**持久状态**（startup_fullscreen
        进入、Esc 退出），这里只切换卡片显隐，几何一个字节都不动。
        """
        self._expanded = True
        # 记住点全屏按钮**之前**的窗口状态：收起时要完整还原到这个位置和
        # 大小（用户点名）。之前就是全屏 → 回全屏；之前是普通窗口 → 回原位。
        # 同样用**客户区矩形**（与 _remember_normal_geometry 一致），
        # saveGeometry/restoreGeometry 会被 DWM 的不可见边框带上偏移。
        self._pre_expand_fullscreen = self.isFullScreen()
        self._pre_expand_geometry = (None if self._pre_expand_fullscreen
                                     else self.geometry())
        # ⚠️ showFullScreen **必须无条件调用**：收起后 isFullScreen() 会短暂
        # 返回 True（状态标志没落地的窗口期），凭它跳过的话，紧跟着的卡片
        # 显隐切换会让全屏状态悄悄落回普通态 —— 第二次展开就"失效"了。
        # showFullScreen 幂等，重复调用无副作用。
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._enforce_native_fullscreen()
        # 全屏时接收区上方的工具栏（显示时间戳/HEX/编码那排）整个藏掉，
        # 搜索条若开着也收起 —— 全屏就是纯看数据。
        self._rx_opts_row.hide()
        if self._search_bar.isVisible():
            self._close_search()
        # 串口开关跟着搬进接收区的功能键列：全屏下它必须还能点
        self._open_row.setParent(self._rx_rail)
        self._rx_rail.layout().insertWidget(0, self._open_row, 0,
                                            Qt.AlignmentFlag.AlignRight)
        self._open_row.show()
        self.brand_header.hide()
        self._config_card.hide()
        self._tx_card.hide()
        self.btn_expand.set_glyph(GLYPH_RESTORE)
        self.btn_expand.setToolTip("退出全屏（Esc）")

    def _collapse_receive(self) -> None:
        """退出接收区展开：卡片们回来，窗口保持在全屏（软件是全屏应用）。"""
        self._expanded = False
        self.btn_expand.set_glyph(GLYPH_EXPAND)
        self.btn_expand.setToolTip("接收区全屏显示（Esc 退出）")
        self._open_row_home.insertWidget(0, self._open_row)
        self._open_row.show()
        self.brand_header.show()
        self._config_card.show()
        self._tx_card.show()
        if not self._paused:                # 暂停中工具栏是故意藏的，别替它掀开
            self._rx_opts_row.show()
        # 完整还原到点全屏按钮之前：之前是窗口全屏 → 重申全屏（无条件，
        # 见 _expand_receive 的说明）；之前是普通窗口 → showNormal + 还原
        # 记下的几何，不能把窗口留在全屏
        if self._pre_expand_fullscreen:
            self.showFullScreen()
            self.raise_()
            self.activateWindow()
            self._enforce_native_fullscreen()
        else:
            # 回到普通窗口 —— "想要全屏"跟着降下来，别让延迟核对又把它拽回去
            self._want_fullscreen = False
            self._expect_normal = True
            self.showNormal()
            if self._pre_expand_geometry is not None:
                self.setGeometry(self._pre_expand_geometry)
            self._expect_normal = False
            self._set_corner_preference(int(self.winId()), round_=True)

    # ------------------------------------------------------------ 发送区最小化

    def _tx_expanded_sizes(self) -> list[int]:
        """发送区**展开**时分割器该有的尺寸：发送区停在最小高度（一排中文），
        余下的高度全归接收区。

        分割器的总高度跟折叠与否无关（收起来的那份高度只是让给了接收区），
        所以直接拿两张卡当前的高度之和当总量，不用另找地方记。
        """
        total = sum(self._splitter.sizes()) or (self._tx_min_height * 4)
        return [max(160, total - self._tx_min_height), self._tx_min_height]

    def _toggle_send_area(self) -> None:
        self._set_send_collapsed(not self._tx_collapsed)

    def _set_send_collapsed(self, collapsed: bool, *, remember: bool = True) -> None:
        """发送区收起到只剩标题栏 / 再展开。

        收起时把分割器的尺寸记下来再压到标题栏高度，展开时还原 ——
        否则用户拖出来的分割位置会被收起动作改掉，回不去了。

        ``remember=False`` 用于**开局默认折叠**：那时还没有"用户拖过的尺寸"，
        留空表示"按自然尺寸展开"（见 ``_tx_expanded_sizes``）。
        """
        if collapsed == self._tx_collapsed:
            return
        if collapsed:
            if remember:
                self._tx_saved_sizes = self._splitter.sizes()
            self._tx_collapsed = True
            self._tx_content.hide()
            self.btn_minimize.set_glyph(GLYPH_EXPAND_CHEVRON)
            self.btn_minimize.setToolTip("展开发送区")
            self._tx_card.setMinimumHeight(TX_COLLAPSED_H)
            # 除了给分割器尺寸，还把**最大高度**一起夹住。QSplitter.setSizes 存的
            # 是"想要的比例"，等控件真正拿到几何时按比例重新分配 —— 开局折叠时
            # 控件还没有几何（在 __init__ 里、show 之前），光靠比例会把发送区
            # 撑成"整高的百分之几"而不是 54px。夹住上限就没有这个歧义。
            self._tx_card.setMaximumHeight(TX_COLLAPSED_H)
            total = sum(self._splitter.sizes()) or 900
            self._splitter.setSizes([max(160, total - TX_COLLAPSED_H), TX_COLLAPSED_H])
        else:
            self._tx_collapsed = False
            self._tx_content.show()
            self.btn_minimize.set_glyph(GLYPH_COLLAPSE)
            self.btn_minimize.setToolTip("最小化发送区")
            self._tx_card.setMaximumHeight(WIDGETSIZE_MAX)
            self._tx_card.setMinimumHeight(self._tx_min_height)
            self._splitter.setSizes(self._tx_saved_sizes or self._tx_expanded_sizes())
        # 折叠/展开改变接收区视口高度 —— 布局的几何重排是**异步**激活的，
        # 不在这里强制落地的话，紧跟着的暂停定位（_snap_to_round_start）拿到
        # 的视口几何是旧的，时间戳会被顶到视口顶（真机实测：应在 7 行下、
        # 实际 0px）。activate() 同步重排，不用进事件循环。
        if self.centralWidget() is not None and self.centralWidget().layout():
            self.centralWidget().layout().activate()

    def _play_intro(self) -> None:
        # 小米那套入场是短促的淡入，不做位移也不做扫光
        fade_in(self.brand_header, delay_ms=0, duration_ms=220)
        for index, card in enumerate(self._cards):
            card.start_intro(delay_ms=50 + index * 55)

    # ------------------------------------------------------------------ 串口开关

    def _selected_port(self) -> str:
        """当前要打开的端口名。

        ⚠️ **不能直接用 ``currentText()``**。下拉项的文字是给人看的
        （"COM3 — STMicroelectronics STLink Virtual COM Port (COM3)"），
        能交给 pyserial 的端口名存在 userData 里（见 ``_fill_port_combo``）。
        把显示文字当端口名传下去，pyserial 会去开一个叫这串名字的"文件"，
        报 ``FileNotFoundError: 系统找不到指定的文件`` —— 用户实测踩过，
        看着像"驱动没装/端口没了"，其实名字就是错的。

        用户可以在框里手输，所以还要认 ``currentData``：只有当框里的文字**仍然
        等于选中项的文字**时才采信 userData（不然用户选了 COM3 又把文字改成 COM5，
        就会按 COM3 去开）。手输的写法兼容 "COM7" 和 "COM7 — 描述" 两种。
        """
        index = self.cmb_port.currentIndex()
        text = self.cmb_port.currentText().strip()
        if index >= 0 and self.cmb_port.itemText(index) == text:
            data = self.cmb_port.itemData(index)
            if isinstance(data, str) and data:
                return data
        return text.split(PORT_LABEL_SEP, 1)[0].strip()

    def _select_port(self, device: str) -> None:
        """按**设备名**选中下拉项；名单里没有这个设备就把它写进编辑框。

        不能按显示文字选（``setCurrentText``）：名单刷新后描述可能会变，
        按文字找会找不到，选中项就丢了。
        """
        index = self.cmb_port.findData(device)
        if index >= 0:
            self.cmb_port.setCurrentIndex(index)
        else:
            self.cmb_port.setEditText(device)

    def _fill_port_combo(self, ports: list[tuple[str, str]], keep_device: str = "") -> None:
        """把扫描到的端口填进下拉框。``device`` 进 userData，描述只作显示。"""
        self._known_ports = list(ports)
        self.cmb_port.blockSignals(True)
        self.cmb_port.clear()
        for device, description in ports:
            self.cmb_port.addItem(f"{device}{PORT_LABEL_SEP}{description}", device)
            self.cmb_port.setItemData(self.cmb_port.count() - 1, device,
                                      Qt.ItemDataRole.ToolTipRole)
        if keep_device:
            self._select_port(keep_device)
        elif not ports:
            self.cmb_port.setEditText("")
        self.cmb_port.blockSignals(False)

    def _refresh_ports(self, announce: bool = True, keep_device: str = "") -> None:
        """重新扫描串口（刷新按钮，或启动时铺一次）。"""
        ports = list_serial_ports()
        self._fill_port_combo(ports, keep_device or self._selected_port())
        if announce:
            self._toast(f"发现 {len(ports)} 个串口" if ports else "未发现串口", 3000)

    def _poll_ports(self) -> None:
        """定时器：插拔设备时自动**发现**（只刷新名单，不代用户打开）。

        只在名单**真的变了**的时候才动界面 —— 否则每秒刷一次下拉框会把用户
        正在输入/正在展开的列表打断，底部也会一直闪提示。

        以前这里还会顺手把新插上的设备连上（"插上就用"），用户后来明确否掉了：
        "连接好了串口不要自动打开，我自己有手，可以手动打开串口"。
        自动打开还有个副作用是致命的 —— 它可能抢在用户前头把**别的**口打开
        （机器上常插着 STLink 之类），用户再去点电源按钮反而变成"关串口"。
        """
        if QApplication.activeModalWidget() is not None:
            # 弹着对话框（设置面板、文件框、报错框）时别在背后偷偷动界面
            return
        ports = list_serial_ports()
        if ports == self._known_ports:
            return

        devices = {device for device, _ in ports}
        # 正在用的设备被拔掉：关掉它。断开提示要**最后**落 —— _close_port 自己
        # 也会往底栏写一条，顺序反了"已断开"会被"发现 N 个串口"盖掉。
        dropped = self._worker.port if (self._worker is not None
                                        and self._worker.port not in devices) else ""
        self._fill_port_combo(ports, self._selected_port())
        if dropped:
            self._close_port(f"{dropped} 已断开")
        else:
            self._toast(f"发现 {len(ports)} 个串口" if ports else "未发现串口", 3000)

    def _on_power_toggled(self, checked: bool) -> None:
        """「串口开关」被拨动：通=打开，断=关闭。

        程序性 ``setChecked`` 同样会发 toggled —— 那时 worker 已经处于目标状态，
        下面两个分支都不会再动，天然没有回环；``_open_port`` 失败时把开关弹回
        去（见里面的 ``reject``），弹回触发的 toggled 同样是空操作。
        """
        if checked and self._worker is None:
            self._open_port()
        elif not checked and self._worker is not None:
            self._close_port()

    def _open_port(self, port: str = "") -> None:
        """打开串口。"""
        def reject(message: str) -> None:
            # 开关多半已经被拨到"开"，失败要弹回去，不然开关是通的实际没连上。
            # 这次 setChecked 触发的 toggled 里 worker 是 None，不会再打开。
            self.btn_open.setChecked(False)
            self._warn(message)

        port = port or self._selected_port()
        if not port:
            reject("请先选择或输入串口名")
            return
        try:
            baudrate = int(self.cmb_baud.currentText())
        except ValueError:
            reject("波特率必须是整数")
            return

        worker = SerialWorker(
            port,
            baudrate=baudrate,
            bytesize=BYTESIZE_CHOICES[self.cmb_bytesize.currentText()],
            parity=PARITY_CHOICES[self.cmb_parity.currentText()],
            stopbits=STOPBIT_CHOICES[self.cmb_stopbits.currentText()],
        )
        worker.received.connect(self._on_received)
        worker.failed.connect(self._on_failed)

        try:
            worker.open()
        except (serial.SerialException, ValueError) as exc:
            # ValueError 也得接住：serial_for_url 会把带 ":" 的名字当 URL 解析，
            # 手输一个 "COM3 abc" 或者未知协议（"nosuch://x"）时抛的是 ValueError，
            # 只捕获 SerialException 的话它会一路冒到 Qt 的事件循环里。
            worker.deleteLater()
            app_log.error(f"串口打开失败 {port}: {exc}")
            reject(f"打不开 {port}：\n{exc}\n\n端口可能被占用，或设备已拔出。")
            return

        self._worker = worker
        self._decoder.reset()
        # 换了个口就是换了套数据，"轮首标记见过没有"要重新判（见 _stamp_needed）
        self._stamp_anchor_seen = False
        self._stamp_last_monotonic = 0.0
        self.btn_open.setChecked(True)
        for widget in self._config_widgets():
            widget.setEnabled(False)
        self.lbl_state.setText(f"已打开 {port} @ {baudrate} · "
                               f"{self.cmb_bytesize.currentText()}"
                               f"{PARITY_LETTERS[self.cmb_parity.currentText()]}"
                               f"{self.cmb_stopbits.currentText()}")
        app_log.info(f"串口已打开 {port} @ {baudrate} "
                     f"{self.cmb_bytesize.currentText()}"
                     f"{PARITY_LETTERS[self.cmb_parity.currentText()]}"
                     f"{self.cmb_stopbits.currentText()}")
        self.pulse.pulse(1.0)
        # 这里不弹临时提示：图标、状态标签和状态灯已经给了反馈，
        # 再弹一条内容相近的提示只会在状态栏里重复一遍。

    def _close_port(self, message: str = "") -> None:
        # 先把关口里剩下的那一批吐出来，别丢掉设备最后发的那几个字节
        # （force：没有下一批了，没等到换行的半行也要显示出来）
        self._flush_rx(force=True)
        worker, self._worker = self._worker, None
        self._send_timer.stop()
        self.chk_timer_send.setChecked(False)
        if worker is not None:
            worker.received.disconnect()
            worker.failed.disconnect()
            worker.stop()
            worker.deleteLater()
        # 开关弹回"断"。若它本来就被拨到断（多数路径如此），toggled 不会发；
        # 自动断开（设备拔掉）时它还在通，这次 setChecked 触发的 toggled 里
        # worker 已经是 None，不会再触发关闭。
        self.btn_open.setChecked(False)
        for widget in self._config_widgets():
            widget.setEnabled(True)
        self.lbl_state.setText("未打开")
        if message:
            self._toast(message, 4000)
        app_log.info("串口已关闭")
        # 关掉之后重扫一次：设备可能已经拔了，下拉框不该继续留着它
        self._fill_port_combo(list_serial_ports(), self._selected_port())

    def _config_widgets(self) -> list[QWidget]:
        return [self.cmb_port, self.cmb_baud, self.cmb_bytesize,
                self.cmb_parity, self.cmb_stopbits, self.btn_rescan]

    def _on_failed(self, message: str) -> None:
        if self._worker is None:
            return
        self._close_port(message)
        self._warn(message)

    # --------------------------------------------------------------------- 接收

    def _on_received(self, data: bytes) -> None:
        self._rx_bytes += len(data)
        self._update_counters()
        self._rx_card.flash(min(0.8, 0.3 + len(data) / 128.0))
        self.pulse.pulse(min(1.0, 0.45 + len(data) / 64.0))
        if not data:
            return

        # 先攒着，静默下来才算"一帧"。串口一次 read 只给一小段，直接显示的话
        # 数字会被拦腰截断、每段还各挂一个时间戳（见 RX_MERGE_MS 的说明）。
        now = time.perf_counter()
        if not self._rx_pending:
            self._rx_pending_since = now
        self._rx_pending += data

        span_ms = (now - self._rx_pending_since) * 1000.0
        if (len(self._rx_pending) >= RX_MERGE_MAX_BYTES
                or span_ms >= RX_MERGE_MAX_MS):
            self._flush_rx()          # 兜底：攒太多或等太久，先吐出去
        else:
            self._rx_flush.start(RX_MERGE_MS)

    def _flush_rx(self, force: bool = False) -> None:
        """把缓冲里攒的这一批数据当作"一帧"显示出来。

        ``force``：关串口这类"没有下一批了"的场合传 True —— 半行也吐，别吞尾巴。
        """
        self._rx_flush.stop()
        if not self._rx_pending:
            return
        # 半行缓发（用户 2026-10-11 点名"莫名其妙的换行"）：分包抖动会把一行
        # 劈成两帧（"…Over Vol" + "tage"、"1.87" + " 2.87 …"），劈在哪、断行
        # 就在哪。缓冲**没以换行收尾**说明它只是一行的前半截 —— 先别吐，继续
        # 攒（最多 RX_HOLD_MAX_MS，没有后半截就整批照吐）；HEX 视图没有"行"
        # 的概念，照旧直接吐。
        if (not force and not self.chk_hex_view.isChecked()
                and not self._rx_pending.endswith((b"\n", b"\r"))):
            held_ms = (time.perf_counter() - self._rx_pending_since) * 1000.0
            if held_ms < RX_HOLD_MAX_MS and len(self._rx_pending) < RX_MERGE_MAX_BYTES:
                self._rx_flush.start(RX_MERGE_MS)
                return
        data = bytes(self._rx_pending)
        self._rx_pending.clear()

        if self.chk_hex_view.isChecked():
            self._emit_block(to_hex(data))
            return

        app_log.debug(f"接收 {len(data)}B")
        text = self._decoder.feed(data)
        # 一帧显示成一条：整帧去掉尾部换行后交给 appendPlainText，
        # 帧内部的换行仍会正常折成多行，所以带换行和不带换行的设备都能看清楚。
        self._emit_block(text.rstrip("\r\n"))

    def _rx_max_row_chars(self) -> int:
        """接收区一行净宽能放下的等宽字符数（0 = 量不到，不设上限）。

        网格重排的行宽一旦超过视口，软换行就在行尾甩出一个孤零零的单元格
        （用户截图里"13.6553"单独占一行）—— 折行数必须按视口封顶。可用宽度
        用"视口宽 - 2×行首起点"量（行首起点已含文档边距和内边距，两端一减就
        是净宽）；再预留垂直滚动条可能出现的那一条 —— 宁可一行少放一格，也
        不给它甩孤行的机会。
        """
        try:
            cursor = self.txt_rx.textCursor()
            cursor.movePosition(QTextCursor.Start)
            left = self.txt_rx.cursorRect(cursor).x()
            advance = QFontMetricsF(self.txt_rx.font()).horizontalAdvance("0")
            viewport_w = self.txt_rx.viewport().width()
        except RuntimeError:
            return 0
        if left < 0 or advance <= 0 or viewport_w <= 0:
            return 0
        scrollbar = self.txt_rx.verticalScrollBar()
        reserve = 4 if scrollbar.isVisible() else 14
        usable = viewport_w - 2 * left - reserve
        return max(0, int(usable / advance))

    def _emit_block(self, text: str) -> None:
        # 一整帧显示成一条：整块去掉首尾换行后交给 appendPlainText，块内的
        # 换行仍会正常折成多行。首尾换行必须掐掉 —— 分包抖动会把一帧劈成
        # "空尾巴 + 实内容"两段，空那段会造出一行空白（用户点名的"空位"）。
        text = text.strip("\r\n")
        if not text:
            return                      # 空帧（设备只发了个换行）不值得占一行
        # 排版对齐（序号补零 + 电压/温度段值补零）需要知道这块属于哪个段 ——
        # 段状态在块间延续（时间戳重置成电压、Temperature1 切温度、BMS 关闭，
        # 规则见 ui_highlight.section_of），所以由主窗口维护、逐块喂给对齐函数。
        # 放在这个**唯一入口**上，时间戳判断、着色、复制、暂停队列拿到的都是
        # 补齐后的同一份文本。
        text = align_indexes(text, section=self._align_section,
                             max_row_chars=self._rx_max_row_chars())
        # 逐行推进段状态，同时看这一帧里有没有"新一轮的开头"。
        # ⚠️ 判据必须是**段状态从非电压段回到电压段**，不能只看"帧里有没有
        # Voltage1:"：电压数据分包成多帧、每帧都带 "Voltage1:" 标题时（真机
        # 实测），后者会在一轮里打出好几个时间戳 —— 用户报的"有不在开头的
        # 时间戳"就是它；而且「复制本轮」会从轮内第二个时间戳起算，前半段
        # 电压整段丢失（用户报的"复制读取的数据不完整"）。
        # 一轮的数据布局是 电压 → Temperature1: → BMS…，所以段状态绕一圈回到
        # 电压 = 新一轮开始；同一轮里电压续传时状态一直停在电压段，不会误判。
        new_round = False
        for line in text.split("\n"):
            if self._align_section != SECTION_VOLT and VOLT_HEADER_RE.match(line):
                new_round = True
            self._align_section = section_of(line, self._align_section)
        if self.chk_timestamp.isChecked() and self._stamp_needed(text, new_round):
            # 时间戳独占一行、本轮首行（Voltage1:）紧贴在它**下面**；它与
            # 上方（上一轮）的内容之间空一行 —— 轮与轮的视觉分隔（用户点名）。
            # 文档还是空的时候不加前导空行（第一帧顶上不留白）。
            document = self.txt_rx.document()
            first = document.firstBlock()
            empty = document.blockCount() == 1 and first.length() <= 1
            head = self._stamp() if empty else f"\n{self._stamp()}"
            display = f"{head}\n{text}"
        else:
            display = text
        if self._paused:
            # 暂停 = **冻结显示**：新数据连同到达时刻的时间戳一起进后台队列，
            # 画面一个像素都不动 —— 这就是"停留在当前数据页面"的严格实现。
            # （试过"照常追加 + 滚动锚定"，但 maximumBlockCount 裁剪下块坐标
            # 和滚动条都是惰性且错位的，怎么补都稳不住，索性不追加。）
            self._paused_frames.append((text, display))
            return
        self._append_display(text, display)

    def _append_display(self, text: str, display: str) -> None:
        """正常显示一帧：追加、追底、记账。暂停恢复时批量刷帧也走这里。"""
        self.txt_rx.appendPlainText(display)
        # 历史接收数据：display 里已含时间戳行，直接整帧喂给历史模块
        # （纯内存入队，落盘由 1s 定时器批量做 —— 见 rx_history.feed）。
        import rx_history
        rx_history.feed(display)
        # appendPlainText 只在"视图本来就在底部"时才跟着走。暂停过又恢复时
        # 视图停在半路，新数据会静静地堆在下面看不见 —— 所以显式滚到底。
        # 搜索钉住时例外：用户正盯着某条命中看，不能把视图拽走。
        if not self._search_pinned:
            self.txt_rx.verticalScrollBar().setValue(
                self.txt_rx.verticalScrollBar().maximum())
        # 搜索条开着的话，高亮要跟着重算（追加会让所有区间前移）。
        # 走防抖计时器：数据不停时它被反复推后，等数据歇下来才真扫一遍。
        if self._search_bar.isVisible():
            self._search_timer.start()
        # 异常定位的高亮钉在某个块上：块被裁掉/内容变了就撤掉（别飘到别的行）
        self._prune_alert_highlight()

    def _flush_paused_frames(self) -> None:
        """恢复滚动：把暂停期间攒下的帧一次性刷进接收区，然后追底。"""
        frames, self._paused_frames = self._paused_frames, []
        for text, display in frames:
            self._append_display(text, display)

    def _stamp(self) -> str:
        # 精确到秒就够 —— 数据本身的间隔远大于 1ms，毫秒那三位只是噪音。
        # 年/月/日三段各有独立开关（设置面板「定制功能」，见 customization），
        # 各自独立拼接 —— 开关只影响**之后**的时间戳，已经显示的历史不动。
        now = datetime.now()
        parts = []
        if customization.get("stamp_show_year"):
            parts.append(f"{now.year:04d}")
        if customization.get("stamp_show_month"):
            parts.append(f"{now.month:02d}")
        if customization.get("stamp_show_day"):
            parts.append(f"{now.day:02d}")
        date = "-".join(parts)
        if date:
            return f"[{date} {now:%H:%M:%S}]"
        return f"[{now:%H:%M:%S}]"

    def _contains_anchor(self, text: str) -> bool:
        """这一帧里有没有轮首标记（见 RX_STAMP_ANCHOR）。

        只要**任意一行**以它开头就算 —— 一帧可能同时含上一轮的尾巴和这一轮的开头
        （分包合成一帧时就会这样），那种情况下也只打一个时刻，仍然落在轮首前面。
        """
        for line in text.splitlines():
            head = line.lstrip()
            if head.startswith(RX_STAMP_ANCHOR):
                tail = head[len(RX_STAMP_ANCHOR):len(RX_STAMP_ANCHOR) + 1]
                if not tail.isdigit():         # 别把假想中的 "Voltage10" 当轮首
                    return True
        return False

    def _stamp_needed(self, text: str, new_round: bool) -> bool:
        """这一帧要不要打时间戳（目标是**一轮一个**）。

        ``new_round``：这一帧里出现了新一轮的开头（段状态从非电压段回到电压段，
        由 ``_emit_block`` 逐行推进段状态得出）。**这才是轮首的正确判据** ——
        只看"帧里有没有 Voltage1:"是不够的：电压数据分包成多帧、每帧都带标题时，
        那样会一轮打出好几个时间戳，复制本轮就会从轮内第二个时间戳起算、
        前半段数据整段丢失（用户报的两个现象）。
        """
        if new_round:
            self._stamp_anchor_seen = True
            self._stamp_last_monotonic = time.monotonic()
            return True
        # 还没见过轮首标记：先按"每帧都打"来，见到之后自动收窄成"每轮一个" ——
        # 换台设备（数据里根本没有 Voltage1）时时间戳不会静默消失。
        if not self._stamp_anchor_seen:
            return True
        # 兜底：只发电压、没有温度/BMS 段的设备，段状态一直停在电压段，永远
        # 等不到 new_round。这类设备的轮边界只能靠**轮间隔**认 —— 两轮之间必
        # 有停顿，隔了 STAMP_ROUND_MIN_MS 以上就认为换了一轮。
        if (self._contains_anchor(text)
                and (time.monotonic() - self._stamp_last_monotonic) * 1000.0
                >= STAMP_ROUND_MIN_MS):
            self._stamp_last_monotonic = time.monotonic()
            return True
        return False

    def _round_span(self, from_block: int) -> tuple[int, int] | None:
        """找出 ``from_block`` 所在"读取轮"的块区间 ``[起始, 结束)``。

        一轮 = 从它上方最近的**时间戳行**开始，到下一个时间戳行之前为止
        （时间戳行也属于这一轮）。返回 None 表示这段数据里没有时间戳
        （轮的概念不存在，比如普通文本设备）。
        """
        document = self.txt_rx.document()
        stamp_start = None
        for b in range(from_block, -1, -1):
            if stamp_span(document.findBlockByNumber(b).text()):
                stamp_start = b
                break
        if stamp_start is None:
            return None
        stamp_end = document.blockCount()
        for b in range(stamp_start + 1, document.blockCount()):
            if stamp_span(document.findBlockByNumber(b).text()):
                stamp_end = b
                break
        return stamp_start, stamp_end

    def _snap_to_round_start(self) -> None:
        """暂停定位：让视口顶部与当前轮的时间戳之间恰好间隔 7 行。

        "当前轮" = 暂停那一刻视口顶块所属的轮。定位后视口最上面 7 行是
        上一轮的尾巴（和轮间空行），第 8 行就是本轮时间戳 —— 一眼能对上
        "这是哪一轮的数据"。数据里没有时间戳时不定位，停在原地。
        """
        document = self.txt_rx.document()
        span = self._round_span(self._top_block_number())
        if span is None:
            return
        stamp_start, _end = span
        # N 由设置面板「定制功能 → 暂停定位行数」自定义（此前固定 7）。
        # ⚠️ 定位必须走**滚动条的行单位**：QPlainTextEdit 的垂直滚动条一格就是
        # 一个视觉行（含折行），直接 setValue(时间戳行号 − N)，顶上正好留 N 行
        # —— 像素差去修行单位的滚动值，单位和语义都对不上，文档一大就振出不
        # 收敛的跳变（真机实测：4px 的滚动值对应 71px 的画面跳动）。
        keep = max(0, int(customization.get("pause_snap_lines")))
        # 行高用时间戳块自身的实际高度：fontMetrics().lineSpacing() 与真实
        # 行高差 1px/行（leading 的取舍不同），长文档累计起来定位就漂一行开外
        row_height = self.txt_rx.blockBoundingGeometry(
            document.findBlockByNumber(stamp_start)).height()
        bar = self.txt_rx.verticalScrollBar()
        bar.setValue(max(0, self._visual_line_index(stamp_start, row_height) - keep))
        # 光标放回本轮时间戳行（焦点/选区别留在半路），顺带兜底保证可见
        cursor = QTextCursor(document)
        cursor.setPosition(document.findBlockByNumber(stamp_start).position())
        self.txt_rx.setTextCursor(cursor)
        self.txt_rx.ensureCursorVisible()

    def _visual_line_index(self, block_number: int, row_height: float) -> int:
        """块首在文档里的**视觉行号**（折行按多行计）。

        与垂直滚动条同一个单位 —— QPlainTextEdit 的滚动范围按文档总行数计
        （含折行），所以"行号 − keep"可以直接喂给 setValue。
        ``row_height`` 传**实测的单行块高度**（如时间戳行）—— 不要用
        ``fontMetrics().lineSpacing()``，它与真实行高差 1px/行（leading 取舍
        不同），长文档累计起来定位会漂出行外。
        ⚠️ 行数用 ``blockBoundingGeometry`` 的高度累计：``block.layout()
        .lineCount()`` 对**尚未布局**的折行块返回 1（惰性布局），长文档越往后
        少算越多（真机实测偏 2 行起）。blockBoundingGeometry 会顺带强制该块
        的布局，拿到的是真值。
        """
        if row_height <= 0:
            return block_number
        rows = 0.0
        block = self.txt_rx.document().firstBlock()
        while block.isValid() and block.blockNumber() < block_number:
            rows += self.txt_rx.blockBoundingGeometry(block).height() / row_height
            block = block.next()
        return int(round(rows))

    def _viewed_block_number(self) -> int:
        """视口**垂直中点**落在的块号 —— 用户"正在看"的位置。

        用中点而不是视口顶：跟随滚动时视口顶常落在上一轮的尾巴上，按它
        判轮会拿到上一轮；中点永远落在画面里占大头的那一轮上，和"正在
        观察"的直觉一致。
        """
        block = self.txt_rx.firstVisibleBlock()
        y = 0.0
        middle = self.txt_rx.viewport().height() / 2.0
        while block.isValid():
            height = self.txt_rx.blockBoundingRect(block).height()
            if y + height >= middle:
                return block.blockNumber()
            y += height
            block = block.next()
        return self._top_block_number()     # 内容不足半屏：就按视口顶算

    def _copy_all_receive(self) -> None:
        """复制**当前正在查看的一轮**：从时间戳行开始，到下一个时间戳之前。

        "正在查看" = 视口垂直中点落在的轮（见 ``_viewed_block_number``）——
        不管是跟着最新数据看，还是往上翻到某一轮盯着看，复制到的都是
        画面里的那一轮，时间戳行在内。数据里没有时间戳（普通文本设备）
        时退回复制全部显示内容。
        """
        document = self.txt_rx.document()
        span = self._round_span(self._viewed_block_number())
        if span is None:
            text = self.txt_rx.toPlainText()
            if not text.strip():
                self._toast("接收区还没有数据", 2000)
                return
        else:
            start, end = span
            text = "\n".join(document.findBlockByNumber(b).text()
                              for b in range(start, end))
            text = text.rstrip("\n")
            if not text.strip():
                self._toast("接收区还没有数据", 2000)
                return
        try:
            QApplication.clipboard().setText(text)
        except Exception as exc:                      # 剪贴板被占用等
            self._warn(f"复制失败：\n{exc}")
            return
        self._toast(f"已复制本轮 {text.count(chr(10)) + 1} 行（含时间戳）", 2000)

    def _clear_receive(self) -> None:
        self.txt_rx.clear()
        self._rx_bytes = 0
        self._decoder.reset()
        # 还没吐出来的那批也要丢掉，否则清空之后它会被补显出来
        self._rx_flush.stop()
        self._rx_pending.clear()
        self._reset_matches()
        self._clear_alert_highlight()
        self._align_section = SECTION_OTHER      # 画面清空 = 段状态归零
        # 画面清空 = 重新开始数，时间戳的"轮首标记见过没有"也跟着归零；
        # 暂停队列里攒着的帧也作废（清空本身就是"不要之前的东西了"）
        self._paused_frames.clear()
        self._stamp_anchor_seen = False
        self._stamp_last_monotonic = 0.0
        self._update_counters()

    # ---------------------------------------------------------- 最近一次异常

    def _last_error_block(self) -> tuple[int, list[tuple[int, int]]] | None:
        """从文档**末尾往前**找最近一行有误的数据，返回 ``(块号, 有误区间)``。

        优先级（用户点名"电压异常的显示和告警优先级应高于温度异常"）：
        **电压越界 > 温度越界 > BMS ERROR**。也就是说电压和温度同时有问题时，
        一键定位跳的是电压那一行；前两者都没有时才退到 BMS 那一行。
        判据与着色共用同一批函数（``ui_highlight.value_spans`` / ``temp_spans`` /
        ``bms_error_spans``），否则会出现"染成红/绿/黄的那行"和"点按钮跳过去
        的那行"对不上的怪事。

        段落状态机（Voltage / Temperature / 其它）必须**从头正向**推：温度用温度
        阈值、电压用电压阈值，段边界靠时间戳和 Temperature1/BMS 切（见 section_of）。
        """
        document = self.txt_rx.document()
        volt = self._rx_highlighter.volt_limits()
        temp = self._rx_highlighter.temp_limits()
        section = section_of("")
        last_volt = None
        last_temp = None
        last_bms = None
        for number in range(document.blockCount()):
            text = document.findBlockByNumber(number).text()
            section = section_of(text, section)
            if section == SECTION_VOLT:
                over_high, over_low = value_spans(text, volt[0], volt[1])
                if over_high or over_low:
                    last_volt = (number, over_high + over_low)
            elif section == SECTION_TEMP:
                over = temp_spans(text, temp[0], temp[1])
                if over:
                    last_temp = (number, over)
            if bms_error_spans(text):
                last_bms = (number, bms_error_spans(text))
        return last_volt or last_temp or last_bms

    def _locate_last_error(self) -> None:
        """标题栏那枚三角按钮：跳到最近一次异常所在行并把它高亮出来。"""
        found = self._last_error_block()
        if found is None:
            self._toast("接收区里还没有越界的电压或温度", 3000)
            return
        number, spans = found
        document = self.txt_rx.document()
        block = document.findBlockByNumber(number)

        # 定位：让那一行落在视口**上方第 3 行**（顶格的话用户看不到上下文）
        row_height = self._single_line_height()
        bar = self.txt_rx.verticalScrollBar()
        bar.setValue(max(0, self._visual_line_index(number, row_height)
                         - ALERT_TOP_LINES))
        # 视图先钉住：否则串口还在流，刚跳过去就被新数据甩到底部了
        self._search_pinned = True

        # 高亮整行：底色用温度那支黄压淡（越界的字符本身已经有红/绿/黄的前景色，
        # 整行底色只是把"就是这一行"指出来）。
        cursor = QTextCursor(block)
        cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
        fmt = QTextCharFormat()
        fill = QColor(qcolor("temp_warn"))
        fill.setAlpha(ALERT_FILL_ALPHA)
        fmt.setBackground(fill)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = fmt
        self._alert_selection = selection
        self._alert_cursor = cursor
        self._alert_text = block.text()
        self._apply_extra_selections()

        self._toast(f"已定位到第 {number + 1} 行（{len(spans)} 处越界）", 2600)

    def _single_line_height(self) -> float:
        """单行块的**实测**高度（喂给 ``_visual_line_index`` 当尺子）。

        拿文档里的时间戳行当样本：它天生是短行、必然只占一行，字体又和数据行
        一样。不能拿 ``fontMetrics().lineSpacing()`` 顶替 —— 两者差 1px/行，
        长文档累计起来定位会漂出行外（``_visual_line_index`` 的注释里记着）。
        """
        document = self.txt_rx.document()
        block = document.firstBlock()
        while block.isValid():
            if block.text().startswith("["):            # 时间戳行
                height = self.txt_rx.blockBoundingGeometry(block).height()
                if height > 0:
                    return height
                break
            block = block.next()
        height = self.txt_rx.blockBoundingGeometry(document.firstBlock()).height()
        return height if height > 0 else float(self.txt_rx.fontMetrics().lineSpacing())

    def _prune_alert_highlight(self) -> None:
        """新数据进来后保住/清掉异常高亮。

        高亮钉在某个块上，而 ``maximumBlockCount`` 会把开头的块裁掉、块号整体重排 ——
        裁到它头上时 QTextCursor 会自己往前挪，那层黄底就会飘到别的行上去。
        判据很简单：**那一行的文本还是不是当初那一行**，不是就撤掉高亮。
        """
        if self._alert_cursor is None:
            return
        block = self._alert_cursor.block()
        if block.isValid() and block.text() == self._alert_text:
            return
        self._clear_alert_highlight()

    def _clear_alert_highlight(self) -> None:
        if self._alert_selection is None and self._alert_cursor is None:
            return
        self._alert_selection = None
        self._alert_cursor = None
        self._alert_text = ""
        self._apply_extra_selections()

    def _set_search_selections(self, selections: list) -> None:
        self._search_selections = list(selections)
        self._apply_extra_selections()

    def _apply_extra_selections(self) -> None:
        """把手头所有高亮**合并**后一次性交给控件。

        ``QTextEdit.setExtraSelections`` 是**全覆盖**的 —— 搜索那套和异常定位
        各设一次的话，后设的会把先设的抹掉，所以统一从这里出。
        """
        merged = list(self._search_selections)
        if self._alert_selection is not None:
            merged.append(self._alert_selection)
        self.txt_rx.setExtraSelections(merged)

    def _save_log(self) -> None:
        content = self.txt_rx.toPlainText()
        if not content:
            self._toast("接收区是空的，没有可保存的内容", 3000)
            return
        default = f"serial-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        path, _ = QFileDialog.getSaveFileName(self, "保存接收日志", default,
                                              "日志文件 (*.log *.txt);;所有文件 (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
        except OSError as exc:
            self._warn(f"写入失败：\n{exc}")
            return
        self._toast(f"已保存到 {path}", 5000)

    # --------------------------------------------------------------------- 发送

    def _on_send(self) -> None:
        if self._worker is None:
            self._warn("串口还没打开")
            self._send_timer.stop()
            self.chk_timer_send.setChecked(False)
            return

        raw = self.txt_tx.toPlainText()
        try:
            if self.chk_hex_send.isChecked():
                payload = parse_hex(raw)
            else:
                text = raw + ("\r\n" if self.chk_append_crlf.isChecked() else "")
                payload = text.encode(self.cmb_encoding.currentText(), errors="replace")
        except ValueError as exc:
            self._warn(str(exc))
            self._send_timer.stop()
            self.chk_timer_send.setChecked(False)
            return

        if not payload:
            return

        self._worker.write(payload)
        self._tx_bytes += len(payload)
        self._update_counters()
        app_log.info(f"发送 {len(payload)}B: {raw[:60]!r}")
        self._tx_card.flash(min(0.8, 0.3 + len(payload) / 128.0))
        self.pulse.pulse(min(1.0, 0.45 + len(payload) / 64.0))
        if not self._send_timer.isActive():
            self._remember_history(raw)

    def _on_timer_toggled(self, enabled: bool) -> None:
        if not enabled:
            self._send_timer.stop()
            return
        if self._worker is None:
            self._warn("请先打开串口")
            self.chk_timer_send.setChecked(False)
            return
        self._send_timer.start(self.spin_interval.value())

    def _on_interval_changed(self, value: int) -> None:
        if self._send_timer.isActive():
            self._send_timer.start(value)

    def _remember_history(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if text in self._history:
            self._history.remove(text)
        self._history.insert(0, text)
        del self._history[MAX_HISTORY:]
        self.cmb_history.blockSignals(True)
        self.cmb_history.clear()
        self.cmb_history.addItem("— 发送历史 —")
        self.cmb_history.addItems(self._history)
        self.cmb_history.setCurrentIndex(0)
        self.cmb_history.blockSignals(False)

    def _on_history_picked(self, index: int) -> None:
        if index > 0:
            self.txt_tx.setPlainText(self.cmb_history.itemText(index))

    # ------------------------------------------------------------------- 杂项

    def _on_encoding_changed(self, encoding: str) -> None:
        self._decoder.set_encoding(encoding)

    def _on_rx_lines_changed(self, value: int) -> None:
        """串口配置栏的「保留行数」变了：立刻改接收区的上限。

        MaximumBlockCount 会即时裁剪，所以缩小之后超出的旧行马上被丢掉 ——
        这是"保留行数"该有的语义，不需要额外确认。
        """
        self._rx_blocks = max(RX_LINES_MIN, min(RX_LINES_MAX, int(value)))
        self.txt_rx.setMaximumBlockCount(self._rx_blocks)

    def _on_rx_lines_text_changed(self, text: str) -> None:
        """「保留行数」下拉框的文字变了（选预设或手动输入）：解析出整数生效。

        打字的过程中每个中间值都会实时生效（和旧数字框同一语义）；非法输入
        （空、无数字）先不管 —— 等合法值出现再应用，不往控件里回写文字，
        免得打断输入。
        """
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            return
        value = int(digits)
        if value != self._rx_blocks:
            self._on_rx_lines_changed(value)

    def _update_counters(self) -> None:
        self.lbl_count.setText(f"接收 {self._rx_bytes} 字节 · 发送 {self._tx_bytes} 字节")

    def _toast(self, message: str, ms: int = 3000) -> None:
        """底部信息条里的临时提示。到点自动清空。"""
        self.lbl_toast.setText(message)
        self._toast_timer.start(ms)

    def _open_settings(self) -> None:
        """打开设置面板。确定后立刻生效并落盘；取消则把实时预览的效果回退。

        面板里的改动是**实时预览**的（毛玻璃强度、按钮显隐、接收区字号），所以
        取消时必须都还原回去 —— 只还原一部分的话，用户取消时界面会停在预览状态里。
        """
        previous_glass = glass_strength()
        previous_hidden = set(self._hidden_buttons)
        previous_font = customization.display_value("rx_font_size")
        dialog = SettingsDialog(self._theme_mode, previous_glass,
                                BUTTON_SPECS, sorted(self._hidden_buttons),
                                on_glass=self._apply_glass,
                                on_buttons=self._preview_buttons,
                                on_custom=self._preview_custom, parent=self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            self._apply_glass(previous_glass)
            self._hidden_buttons = previous_hidden
            self._apply_button_visibility()
            self._apply_rx_font_size(previous_font)
            return
        theme_mode, glass = dialog.values()
        # 「定制功能」由注册表统一管理：面板按 specs() 生成控件，这里统一落盘。
        # 生效不需要额外动作 —— pause_snap_lines 在暂停时现取，时间戳开关只
        # 影响之后的时间戳（已显示的历史不动）。
        custom_changes = dialog.custom_values()
        for key, value in custom_changes.items():
            customization.set(key, value)
            # 同一时刻打上"用户选过"的标记：以后改默认值时不会再把他选的值迁走
            customization.mark_chosen(key)
        if custom_changes:
            app_log.info("定制配置更新: "
                         + ", ".join(f"{k}={v}" for k, v in custom_changes.items()))
        self._theme_mode = theme_mode
        self._hidden_buttons = set(dialog.hidden_buttons())
        self._apply_button_visibility()
        self._settings.setValue("theme_mode", theme_mode)
        # 「保留行数」不在这个面板里了，别再往设置里写 —— 它由串口配置栏那枚
        # 数字框自己负责落盘（见 _save_settings）。
        # glass_chosen 是"用户在面板里亲手选过一次"的标记：只要它在了，
        # 以后启动就再也不会拿旧默认值去覆盖他的选择（见 _restore_settings）。
        self._settings.setValue("glass", glass)
        self._settings.setValue("glass_chosen", True)
        self._settings.setValue("hidden_buttons", sorted(self._hidden_buttons))
        self._apply_glass(glass)
        if theme_mode == "system":
            self._apply_theme(detect_system_scheme())
        else:
            self._apply_theme(theme_mode)
        if any(k.startswith("clock_") for k in custom_changes):
            self._tick_clock()          # 时钟开关立即生效，不等下一秒的定时器
        if "rx_font_size" in custom_changes:
            # 字号在拖动时已实时预览过，这里按**落盘后的值**再应用一次
            # （面板传的是显示值，落盘的是半磅存储值，换算口径统一走 display_value）
            self._apply_rx_font_size(customization.display_value("rx_font_size"))
        if any(k.startswith(("volt_limit", "temp_limit")) for k in custom_changes) \
                or "highlight_errors" in custom_changes:
            # 电压/温度范围变了、或「高亮异常数据」开关拨过：高亮器重读配置
            # 并重刷整篇着色
            self._rx_highlighter.refresh()
        if "startup_maximized" in custom_changes:
            # 起手姿态当场摆一次（不然用户得重启才看得到自己改了什么）
            self._apply_startup_maximized()
        self._toast("设置已保存", 2500)
        app_log.info(f"设置已保存（主题={theme_mode}, 定制项 {len(custom_changes)} 个）")

    # ------------------------------------------------------------ 按钮显隐

    def _apply_rx_font_size(self, point_size: float) -> None:
        """设置接收区字号（用户点名：滑杆**无级**调节 + 拖动实时预览）。

        只换字体 —— 文档里的文字一个字符都不动（前导补零与网格对齐按**字符
        数**算，跟字号无关）；字号变大后一行能放的字符变少，软换行由
        QPlainTextEdit 自己重排，不需要我们插手。
        """
        self.txt_rx.setFont(mono_font(float(point_size)))

    def _preview_custom(self, key: str, value) -> None:
        """设置面板里拖**滑杆**时的实时预览（不落盘）。

        面板只把 ``key`` 与显示值丢过来 —— "要不要实时生效、怎么生效"由这里
        决定，面板自己不认识任何具体配置项（它只按 customization 注册表生成控件）。
        """
        if key == "rx_font_size":
            self._apply_rx_font_size(value)

    def _preview_buttons(self, hidden: list[str]) -> None:
        """设置面板拖动开关时的实时预览（不落盘）。"""
        self._hidden_buttons = set(hidden)
        self._apply_button_visibility()

    def _apply_button_visibility(self) -> None:
        known = {key for key, _label, _group in BUTTON_SPECS}
        self._hidden_buttons &= known          # 旧配置里已删掉的键不要留着
        for key, _label, _group in BUTTON_SPECS:
            widget = getattr(self, key, None)
            if widget is not None:
                widget.setVisible(key not in self._hidden_buttons)
        # 「定时发送」藏起来时，紧跟其后的间隔时长数字框要一起收 —— 一个孤零零的
        # 「1000 ms」留在选项行里毫无意义，还会让人以为定时发送还开着。
        self.spin_interval.setVisible("chk_timer_send" not in self._hidden_buttons)

    def _apply_glass(self, value: int) -> None:
        """设置毛玻璃强度并立刻重绘。

        强度只影响卡面的透明度和颗粒幅度，两者都是绘制时现取的，所以重绘一下即可。
        """
        set_glass_strength(value)
        # 拖滑杆第一次离开 0 时就会用到颗粒贴图（生成 + 模糊约 40ms），而现在
        # 默认档是 0、启动时不会烘它 —— 在这里补一次，别让第一帧卡在拖动里。
        if frost_gain() > 0.0:
            frost_tile(current_scheme(), self.devicePixelRatioF())
        for card in self._cards:
            card.update()
        self.brand_header.update()
        self.update()

    def _warn(self, message: str) -> None:
        app_log.error(message.splitlines()[0] if message else "")
        QMessageBox.warning(self, "串口调试助手", message)

    # -------------------------------------------------------- 配置持久化

    def _restore_settings(self) -> None:
        settings = self._settings
        # 定制配置的默认值改过之后，先把"用户没选过、还停在旧默认值"的迁到新默认
        # （本轮的 clock_show_date：默认从"带年月日"改成"不带"）。必须在下面各处
        # 读值之前做，否则界面会先按旧值起一次。
        customization.migrate_defaults()
        self._theme_mode = str(settings.value("theme_mode", "system"))
        self._rx_blocks = max(RX_LINES_MIN, min(RX_LINES_MAX,
                                                int(settings.value("rx_blocks", DEFAULT_RX_BLOCKS))))
        # 写回下拉框：editTextChanged → _on_rx_lines_text_changed 会把 txt_rx
        # 的上限一并定下来（下面的 setMaximumBlockCount 是无信号时的兜底）
        self.cmb_rx_lines.setEditText(str(self._rx_blocks))
        self.txt_rx.setMaximumBlockCount(self._rx_blocks)
        # 毛玻璃：默认档现在是 0（不要磨砂）。历史默认值（68 / 100）几乎不可能是
        # 用户特意调的 —— 何况 glass 每次关窗都会把当前值写盘，"值等于旧默认"根本
        # 说明不了用户动没动过滑杆。所以判据是**用户有没有在设置面板里选过**：
        # 没选过 → 一律迁到当前默认；选过 → 哪怕是 100 这个旧默认值也照样尊重。
        stored_glass = settings.value("glass", None)
        glass_chosen = settings.value("glass_chosen", False, type=bool)
        if stored_glass is None or (not glass_chosen and int(stored_glass) in LEGACY_GLASS):
            stored_glass = DEFAULT_GLASS
        set_glass_strength(int(stored_glass))
        self._hidden_buttons = {str(key) for key in
                                settings.value("hidden_buttons", [], type=list)}
        self._apply_button_visibility()
        self.cmb_baud.setCurrentText(str(settings.value("baud", "115200")))
        self.cmb_bytesize.setCurrentText(str(settings.value("bytesize", "8")))
        # 「无」是旧版无校验的写法，现在叫 none；任何对不上的旧值一并归到 none
        parity = str(settings.value("parity", "none"))
        if parity not in PARITY_CHOICES:
            parity = "none"
        self.cmb_parity.setCurrentText(parity)
        self.cmb_stopbits.setCurrentText(str(settings.value("stopbits", "1")))
        self.cmb_encoding.setCurrentText(str(settings.value("encoding", "utf-8")))
        self.chk_timestamp.setChecked(settings.value("timestamp", True, type=bool))
        self.chk_hex_view.setChecked(settings.value("hex_view", False, type=bool))
        self.chk_hex_send.setChecked(settings.value("hex_send", False, type=bool))
        self.chk_append_crlf.setChecked(settings.value("append_crlf", True, type=bool))
        self.spin_interval.setValue(int(settings.value("interval", 1000)))
        self.txt_tx.setPlainText(str(settings.value("last_send", "")))
        for item in settings.value("history", [], type=list):
            self._history.append(str(item))
        if self._history:
            self.cmb_history.addItems(self._history)

        geometry = settings.value("geometry")
        if isinstance(geometry, QRect):
            # 现在存的是**客户区矩形**（见 _remember_normal_geometry 的说明）
            self.setGeometry(geometry)
        elif geometry is not None:
            # 老配置里存的是 saveGeometry() 的字节串，还能用就继续用
            self.restoreGeometry(geometry)

    def _save_settings(self) -> None:
        settings = self._settings
        settings.setValue("theme_mode", self._theme_mode)
        settings.setValue("rx_blocks", self._rx_blocks)
        settings.setValue("glass", glass_strength())
        settings.setValue("hidden_buttons", sorted(self._hidden_buttons))
        settings.setValue("baud", self.cmb_baud.currentText())
        settings.setValue("bytesize", self.cmb_bytesize.currentText())
        settings.setValue("parity", self.cmb_parity.currentText())
        settings.setValue("stopbits", self.cmb_stopbits.currentText())
        settings.setValue("encoding", self.cmb_encoding.currentText())
        settings.setValue("timestamp", self.chk_timestamp.isChecked())
        settings.setValue("hex_view", self.chk_hex_view.isChecked())
        settings.setValue("hex_send", self.chk_hex_send.isChecked())
        settings.setValue("append_crlf", self.chk_append_crlf.isChecked())
        settings.setValue("interval", self.spin_interval.value())
        settings.setValue("last_send", self.txt_tx.toPlainText())
        settings.setValue("history", self._history)
        # 展开成全屏时不能存当前几何，否则下次启动窗口会又停在满屏状态；
        # 展开前来路是普通窗口的，存展开前记下的那个几何
        if self._expanded:
            geometry = (self._pre_expand_geometry if not self._pre_expand_fullscreen
                        and self._pre_expand_geometry is not None
                        else self._normal_geometry)
        elif self.isFullScreen():
            # 全屏状态关窗：saveGeometry 存下来的是满屏矩形，下次启动窗口会
            # 带着"满屏尺寸的普通态"起来 —— 存平时记住的普通几何
            geometry = self._normal_geometry
        else:
            geometry = self.geometry()          # 客户区矩形，见 _remember_normal_geometry
        settings.setValue("geometry", geometry)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 阴影/圆角属性要等窗口真正创建（winId 有效）之后再设 —— 幂等，多设几次无害
        self._enable_native_shadow()
        # 标题栏主题同样要等窗口创建之后；主窗口现在是无边框（没有系统标题栏），
        # 这一步主要作用在 DWM 的边框色/阴影上，以及给设置对话框那类仍有边框的
        # 窗口用同一套处理。
        apply_titlebar_theme(self, self._titlebar_scheme or detect_system_scheme(),
                             rounded=not self.isFullScreen())

    def _enable_native_shadow(self) -> None:
        """给无边框窗口借回系统的阴影与圆角。

        DWM 只给"有边框"的窗口画影子，而 ``FramelessWindowHint`` 把 WS_THICKFRAME
        和 WS_CAPTION 一起摘掉了（实测 GWL_STYLE）。常规做法是
        ``DwmExtendFrameIntoClientArea`` 把 1px 边框"借"进客户区 —— 影子、圆角、
        Win11 的窗口描边色都回来了，看着才像系统窗口而不是一块硬边色块。

        ⚠️ 这 1px 会不会让 shell 不再认全屏？**实测不会**：加上之后
        ``showFullScreen`` 的底部像素仍是页面底色（任务栏照样被藏），
        左上角也没有漏出桌面。
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = int(self.winId())
            if not hwnd:
                return

            class MARGINS(ctypes.Structure):
                _fields_ = [("cxLeftWidth", ctypes.c_int),
                            ("cxRightWidth", ctypes.c_int),
                            ("cyTopHeight", ctypes.c_int),
                            ("cyBottomHeight", ctypes.c_int)]

            margins = MARGINS(1, 1, 1, 1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                wintypes.HWND(hwnd), ctypes.byref(margins))
        except Exception:
            pass        # Win7 / 远程桌面下拿不到 DWM：退回平面无影窗口，功能不受影响

    def closeEvent(self, event) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        # 关窗时先停掉自动扫描：否则收尾这几步里它可能又去开一个串口
        self._port_watch.stop()
        self._save_settings()
        if self._worker is not None:
            worker, self._worker = self._worker, None
            worker.stop()
        super().closeEvent(event)



