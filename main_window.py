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

# 建链挪进 SerialWorker 之后，界面层不再直接调 serial.* 了；这个 import 留着
# 是因为串口异常的**语义**仍来自这个模块（后续在界面层写 except serial.SerialException
# 时不必再想起来补 import）。
from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFontMetricsF, QGuiApplication, QImage, QKeySequence, QPainter,
    QPixmap, QShortcut, QTextCharFormat, QTextCursor,
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
from ui_anim import (EASE, STANDARD, Reveal, Tween, animations_enabled,
                     in_out_quint, out_cubic)
import ui_anim
from ui_backdrop import frost_tile, paint_backdrop
from ui_brand import BrandHeader, CaptionBar
from ui_highlight import (
    SECTION_OTHER, SECTION_TEMP, SECTION_VOLT, VOLT_HEADER_RE, RxHighlighter,
    align_indexes, bms_error_spans, section_of, stamp_span, temp_spans,
    value_spans,
)
from ui_controls import (
    AlertButton, ChevronComboBox, ChevronSpinBox, FlashLabel, IconButton,
    IconLabel, InkButton, LabeledField, PulseDot, SearchEdit, Switch,
)
from ui_settings import SettingsDialog
from ui_surface import Card, fade_in, paint_glass
from ui_theme import (
    CARD_PAD, CARD_RADIUS,
    DEFAULT_GLASS, GAP, GLYPH_CHEVRON_DOWN, GLYPH_CHEVRON_UP, GLYPH_CLEAR,
    GLYPH_CLOSE, GLYPH_COLLAPSE, GLYPH_COPY, GLYPH_EXPAND, GLYPH_EXPAND_CHEVRON,
    GLYPH_PAUSE,
    GLYPH_PLAY, GLYPH_REFRESH, GLYPH_RESTORE, GLYPH_SAVE, GLYPH_SEARCH,
    GLYPH_SEND, GLYPH_SETTINGS, LEGACY_GLASS, SHADOW_PAD, TITLE_H, apply_scheme,
    apply_titlebar_theme,
    current_scheme, detect_system_scheme, frost_gain, glass_strength, match_fill,
    material, mono_font, num_font, qcolor, set_glass_strength, set_material,
    set_scheme_mix, ui_font,
)

# ``ble_io`` 只在**真的要连蓝牙**时才导入：它会连带拉起 ``asyncio``
# （实测冷启动 +76ms，占全部 import 时间的 1/4），而串口用户一辈子用不到。
# 类型注解写成字符串（`from __future__ import annotations`），不在这里引入名字。
_BLE_WORKER_CLS: type | None = None


def _ble_worker_cls() -> type:
    """惰性拿蓝牙广播 worker 类（首次调用才付 ``ble_io``/``asyncio`` 的导入成本）。"""
    global _BLE_WORKER_CLS
    if _BLE_WORKER_CLS is None:
        from ble_io import BleBroadcastWorker
        _BLE_WORKER_CLS = BleBroadcastWorker
    return _BLE_WORKER_CLS


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
# 发送区**手动**折叠/展开的时长（用户原话"有点傻快"：原来 240ms 在 300→54 这段
# 长距离上收得太急，看着像被弹走）。300ms 落在要求的 260~320ms 区间内，曲线仍用
# 项目曲线词表里的 in_out_quint（两头慢、中段果断，不过冲 —— 苹果 §4 的临界阻尼）。
# ⚠️ 只作用于**手动**折叠/展开：最大化让位那两条（Reveal）必须和配置卡同一拍
# （220ms），那是用户点名的"发送区与配置卡同时消失"，不能跟着一起放慢。
TX_ANIM_MS = 300
# 动画期间的日志追加合并窗口（毫秒）。见 _set_rx_coalesce / _append_display。
# 60ms 远低于"一眨眼下"的感知阈值，却能把追加频率压掉近一个数量级 ——
# 250 行/秒 + 同时最大化的场景里，P50 从 ~65fps 抬回 ~118fps（探针实测）。
RX_COALESCE_MS = 24
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

# 连接方式：串口 / 蓝牙广播。
# 蓝牙广播那条路**只被动扫描 advertisement**，不配对、不连接（用户 2026-10-14
# 点名要的用法："只需要通过蓝牙广播随时读取数据，不需要绑定"）。代价是单向
# （发不了指令）且带宽低，细节见 ble_io 的模块说明。
LINK_CHOICES = ("串口", "蓝牙广播")
LINK_SERIAL, LINK_BLE = LINK_CHOICES
# 点刷新时扫多久（秒）。广播包密度不高（每个设备每秒几包），3.5 秒足够把周围
# 设备列全；再长用户就要等得心急了。
BLE_SCAN_SECONDS = 3.5

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


# 抓设置面板快照时把它挪到这里的坐标：屏幕外，用户看不见，但窗口对 DWM 仍是
# "可见"状态（PrintWindow 要靠 DWM 重定向才有内容，见 _capture_native_window）。
_SETTINGS_OFFSCREEN = -4000

# 历史回放提示条展开/收起的高度补间时长（ms）。和搜索条那套（Reveal 200ms）
# 同一量级：一帧里凭空多出 50px 会把下面的日志框猛地顶下去，必须有一条坡。
_REPLAY_BAR_MS = 220
# 补间的最高发值频率（ms/帧）。日志区每改一次高度要整块重绘（实测 7~14ms），
# 125Hz 送值等于连环重排 —— 一帧只渲染最后一次，条就成台阶了。
# 锁到 60fps：220ms / 16ms ≈ 13 个台阶，肉眼就是坡。
_REPLAY_BAR_MIN_MS = 16
# 回放 ←→ 实时切换时接收区文字的交叉淡入时长（ms）。只做"旧内容淡出"，
# 新内容一开始就是完全不透明的（它已经在底下了）。
_RX_XFADE_MS = 160
# Qt 里"没有上限"的那个值（qwidget.h 的 QWIDGETSIZE_MAX）。用来把动画期间
# 钉死的 maximumHeight 还原回去 —— 写 16777215 而不是留个 0/50，是为了之后
# 换字体、换主题时条能重新按新的 sizeHint 长。
_WIDGET_MAX = 16777215


def _window_rect_physical(hwnd: int) -> tuple[int, int, int, int] | None:
    """``GetWindowRect``：窗口**扩展边框**的物理像素矩形（含 DWM 那圈不可见边框）。"""
    if sys.platform != "win32" or not hwnd:
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        rect = wt.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return (rect.left, rect.top,
                rect.right - rect.left, rect.bottom - rect.top)
    except Exception:                               # noqa: BLE001
        return None


def _capture_native_window(hwnd: int) -> QPixmap:
    """用 ``PrintWindow(PW_RENDERFULLCONTENT)`` 抓一个**原生窗口**，返回位图。

    为什么非要这么绕：``QWidget.grab()`` 只渲染**客户区**，原生标题栏（设置面板
    那 29px）不在其中 —— 入场动画里长出来的是"没有标题栏的面板"，落地那一下标题栏
    凭空出现、内容还被顶下 29px，肉眼就是闪（用户连报两次）。PrintWindow 走 DWM
    重定向，**连非客户区一起渲染**，抓回来的图与 ``frameGeometry()`` 对齐。

    ⚠️ 窗口必须可见（哪怕挪到屏幕外）：``WA_DontShowOnScreen`` 下实测只能拿到全黑。
    ⚠️ 返回的是**物理像素**，调用方负责裁掉不可见缩放边框并补 devicePixelRatio。

    非 Windows 或任何一步失败都返回空位图，由调用方退回 ``grab()``。
    """
    if sys.platform != "win32" or not hwnd:
        return QPixmap()
    try:
        import ctypes
        import ctypes.wintypes as wt

        class _BI(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                        ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                        ("biClrImportant", wt.DWORD)]

        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return QPixmap()
        hdc = user32.GetWindowDC(hwnd)
        memdc = gdi32.CreateCompatibleDC(hdc)
        bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
        gdi32.SelectObject(memdc, bitmap)
        try:
            # 2 = PW_RENDERFULLCONTENT：连非客户区（标题栏/边框）一起渲染
            if not user32.PrintWindow(hwnd, memdc, 2):
                return QPixmap()
            info = _BI()
            info.biSize = ctypes.sizeof(_BI)
            info.biWidth, info.biHeight = width, -height
            info.biPlanes, info.biBitCount, info.biCompression = 1, 32, 0
            buf = ctypes.create_string_buffer(width * height * 4)
            gdi32.GetDIBits(memdc, bitmap, 0, height, buf, ctypes.byref(info), 0)
        finally:
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memdc)
            user32.ReleaseDC(hwnd, hdc)
        image = QImage(bytes(buf.raw), width, height, QImage.Format.Format_ARGB32)
        return QPixmap.fromImage(image.copy())      # copy()：脱离 buf 的内存
    except Exception:                               # noqa: BLE001
        return QPixmap()


class _SettingsShot(QWidget):
    """设置面板"变大/变小"动画里那张**整块内容快照**。

    为什么不用 geometry 插值 + 每帧内容重排：那样只是外框在滑，内容是被拉伸的
    边框和重新回流的文字 —— 用户原话"不是这样的，要像 iOS 打开应用那样**整块
    均匀放大**"。所以这里把面板抓成一张位图（见 ``_capture_native_window``，
    **含原生标题栏**），动画期间只负责把这张位图用 ``drawPixmap`` 画进当前矩形：
    图标、文字、间距全部等比缩放，是"鼓大"。

    ⛔ 不用 ``QGraphicsOpacityEffect`` / ``QGraphicsScale``：离屏合成会掉帧抽搐
    （项目铁律）。每帧一次 ``drawPixmap``，不触发离屏 surface。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shot: QPixmap | None = None
        self._alpha = 1.0
        self.setWindowFlags(Qt.WindowType.ToolTip)      # 无框、不抢焦点
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def set_shot(self, shot: QPixmap | None) -> None:
        self._shot = shot
        self.update()

    def set_alpha(self, value: float) -> None:
        """整块快照的不透明度（0~1）—— 换帧收尾时**渐隐**用。

        ⚠️ 走 **``setWindowOpacity``**（窗口级合成），**不是** ``painter.setOpacity``
        （控件级绘制）—— 后者在实测里是"把整块调暗成黑"，不是"透出底下的真面板"：

            devtools/_probe_settings_zorder.py 实测（360x240 物理区域）
            alpha 1.000 → 0.461 → 0.067 → 0.017 → 0.000
            均亮  45.1  → 20.8  →  3.0  →  0.2  →  0.0     ← 线性趋黑
            撤掉快照那一帧：均亮 0.0 → 45.1（一整块黑 → 真面板）

        原因：这个窗口**不带 ``WS_EX_LAYERED``**（实测 exstyle=0x88 = TOPMOST+
        TOOLWINDOW）。Qt 把内容按**预乘 alpha** 写进重定向表面，而 DWM 对非分层
        窗口**忽略 alpha 通道**、当不透明来合 —— 于是屏幕上就是 ``alpha × 内容``
        压在**黑底**上。``painter.setOpacity`` 只改了预乘系数，改不了"底下是谁"。
        要让 DWM 按 alpha 去和桌面混合，窗口必须是 layered —— 而
        ``setWindowOpacity(<1)`` 正是 Qt 把它变成 layered 的那条路。

        ⛔ 铁律禁的是 **``dialog``（真面板）** 调 setWindowOpacity：它一变 layered，
           DWM 就停画它的阴影，回到 1.0 时阴影"啪"地出现。快照是 ToolTip、
           没有系统阴影，用它不涉及那条坑（真面板始终是 opacity 1.0 不透明上屏）。
        ⛔ 不是 ``QGraphicsOpacityEffect``（控件级离屏合成，项目铁律禁）。
        """
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._alpha) > 1e-3:
            self._alpha = value
            # 回到 1.0 要**显式**置一次：Qt 只在 <1 时维持 WS_EX_LAYERED，
            # 不置的话 layered 摘掉的那一帧可能和"撤快照"错开，多一处闪。
            self.setWindowOpacity(max(0.0, min(1.0, value)))
            self.update()

    def paintEvent(self, event) -> None:
        if self._shot is None or self._shot.isNull() or self._alpha <= 0.0:
            return
        painter = QPainter(self)
        # 缩放质量必须开：不开的话放大过程全是马赛克阶跃（那也是"跳变"）
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # ⚠️ 这里**不再** setOpacity：透明度已经由窗口级合成负责（见 set_alpha）。
        #    两处都做的话会被乘两次（alpha²），淡出比曲线快得多。
        painter.drawPixmap(self.rect(), self._shot)


class _RxShot(QWidget):
    """接收区文字切换（回放 ⇄ 实时）时，垫在**新内容上面**的那张旧内容快照。

    为什么需要它：``txt_rx.clear() + setPlainText(...)`` 是在**一帧之内**把整块
    文字换掉 —— 长文档上肉眼就是"啪"地硬切一下。这里把切换前 viewport 的当前
    画面抓成一张图盖上去，然后让这张旧图在 160ms 里淡出：视觉上是旧内容**溶进**
    新内容，而不是突然替换。

    ⛔ 不用 ``QGraphicsOpacityEffect``：控件级离屏合成 = 掉帧抽搐（项目铁律）。
    这里直接在 ``paintEvent`` 里 ``painter.setOpacity()`` 后 ``drawPixmap`` ——
    每帧一次带透明度的贴图，不建离屏 surface。

    挂在 ``txt_rx.viewport()`` 下面（子控件画在父控件 paintEvent 之后，天然盖住
    正文），并 ``WA_TransparentForMouseEvents`` —— 淡出期间用户点选 / 滚动 / 搜索
    跳转一律不受影响，事件直接穿过去落到编辑器上。
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._shot: QPixmap | None = None
        self._alpha = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def set_shot(self, shot: QPixmap | None) -> None:
        self._shot = shot
        self.update()

    def get_shot(self) -> QPixmap | None:
        """当前挂着的那张图（None = 没有）。淡出前用它判"还有没有东西可淡"。"""
        return self._shot

    def set_alpha(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._alpha) > 1e-3:
            self._alpha = value
            self.update()

    def alpha(self) -> float:
        """当前不透明度（0~1）—— 淡出中途被打断时从这里接着走。"""
        return self._alpha

    def sync_geometry(self) -> None:
        """铺满父控件（viewport 会随窗口尺寸/滚动条显隐变化，每帧同步一次）。"""
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())

    def paintEvent(self, event) -> None:
        if self._shot is None or self._shot.isNull() or self._alpha <= 0.0:
            return
        painter = QPainter(self)
        if self._alpha < 1.0:
            painter.setOpacity(self._alpha)
        painter.drawPixmap(self.rect(), self._shot)


class ReplayBar(QWidget):
    """历史回放提示条：**自绘**圆角面。

    ⛔ 这个面不能用 QSS 画。2026-10-14 用户报"边框没有圆角"：QSS 里
    `QWidget#replayBar { border-radius: 26px }` 对**普通 QWidget** 会被整条丢掉，
    渲染成直角（主窗口里稳定复现；同一份样式表放进隔离窗口却是圆的，
    强制 unpolish/polish 也救不回来）。卡片本来就是自绘的 —— 这里跟着卡片走
    同一条路（``ui_surface.paint_glass`` + ``CARD_RADIUS``），圆角和描边才
    真正和卡片一致，也不再受 QSS 那套脾气影响。

    左边那条"主色竖条"是旧装饰，已按用户要求去掉：文案本身已经写明这是回放数据。
    """

    # 圆角半径：直接取卡片的常量 —— 这条的面和卡片是同一个视觉语言，
    # 自测按它断言"圆角是否和卡片统一"，别在 paintEvent 里另写一个字面量。
    RADIUS = CARD_RADIUS

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 内缩 0.5：1px 描边正好落在控件边界之内（半像素压在边界上会被裁掉一半）
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # 走材质入口（和卡片同一条路）：换材质时回放条也跟着变，
        # 底色仍用次级面 surface_2，只用 fill/border override 顶掉材质主体色。
        paint_glass(painter, box, self.RADIUS,
                    fill_override=qcolor("surface_2"),
                    border_override=qcolor("line"))


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
        # 建链是否**已经**完成（区别于"正在连接"）：_worker 一请求打开就有值，
        # 但蓝牙口要等几秒才真正连上，这两件事必须分开记（见 _open_port）。
        self._port_connected = False
        # "建链中被取消"、线程还卡在 open() 里的 worker。**必须留强引用** ——
        # PySide 的信号连接不保证对象存活，若 Python 先把它回收掉，QThread 的
        # C++ 对象会在线程还在跑时被析构，Qt 直接 fail-fast（实测 0xC0000409）。
        self._retiring: list[SerialWorker] = []
        # 蓝牙广播：列表扫描器（只在"点刷新"时活几秒）、扫到的设备、上次选的目标
        self._ble_scanner: object | None = None      # BleBroadcastWorker（惰性导入，见 _ble_worker_cls）
        self._ble_seen: dict[str, dict] = {}
        self._ble_target = ""
        self._decoder = StreamDecoder()
        # 自动扫描：只记"上次看到的端口名单"，名单一变就说明插了或拔了东西（见
        # _poll_ports）。**扫到什么都不自动打开** —— 用户明确要求"我自己有手"。
        self._known_ports: list[tuple[str, str]] = []
        self._rx_bytes = 0
        self._tx_bytes = 0
        self._history: list[str] = []
        self._settings = QSettings("MimoWorks", "SerialAssistant")
        self._cards: list[Card] = []
        # 主题过渡（见 _animate_theme）：补间惰性创建，起止方案记在这里
        self._theme_tween: Tween | None = None
        self._theme_from = ""
        self._theme_to = ""
        # 接收区"跟随到底"的平滑滚动补间（见 _follow_rx_bottom）
        self._rx_scroll_tween: Tween | None = None
        # 暂停时「时间戳/HEX/编码」工具栏的收展补间（见 _animate_opts_row）
        self._opts_tween: Tween | None = None
        self._opts_from = 0.0
        self._opts_to = 0.0
        # 设置面板的"从哪里来往哪里去"动画状态（见 _open_settings / 苹果 §7）
        self._settings_dialog = None
        self._settings_geom_tween: Tween | None = None
        self._settings_frame_margins = (0, 0, 0, 0)
        self._settings_geom_from = QRect()
        self._settings_geom_to = QRect()
        self._settings_geom_opening = True
        self._settings_result = False
        self._settings_values = ("system", 0)
        self._settings_custom: dict = {}
        self._settings_hidden: list = []
        self._settings_previous = (0, "frosted", set(), 21)
        # 设置面板"整块放大/缩小"动画里的那张内容快照（见 _SettingsShot）
        self._settings_shot = _SettingsShot(self)
        # 入场落定时"同一拍真面板上屏、撤快照"的临时目标。硬切是**同步**的
        # （落定即清），正常不会停在非空；保留它只是给诊断探针一个可读状态。
        self._settings_swap_target = None
        # 面板**静止态**的最小尺寸（尺寸对象，见 _open_settings / A3）。
        # 入场动画期间必须把 min 放开成 (0, 0) 才缩得到按钮那一小块；落定时要
        # 把整份 minimumSize 还回去 —— 只还宽度会留下高度那一半的坑（面板内容
        # 撑出来的最小高没恢复，之后任何一次重排都会按"能缩到很矮"算）。
        self._settings_min_size = None
        # 动画期间的日志追加合并（见 _set_rx_coalesce）：0 = 不合并（常态）
        self._rx_coalesce_ms = 0
        self._rx_coalesce_buffer: list[tuple[str, str]] = []
        self._rx_coalesce_timer = QTimer(self)
        self._rx_coalesce_timer.setSingleShot(True)
        self._rx_coalesce_timer.timeout.connect(self._flush_rx_coalesced)
        # 分割器尺寸动画（发送区收展 / 接收区最大化共用，见 _animate_splitter）
        # 展开前本来就隐藏的块（收起时不要把它们显回来）
        self._pre_hidden_blocks: list = []
        # 展开前发送区是不是折叠的（收起时原样还原）
        self._pre_send_collapsed = False
        # 展开接收区之前，发送卡的真实高度（退出时原样还原）
        self._pre_send_height = 0
        # 接收区转场：**一条时间线**驱动所有几何（见 _rx_transition）。
        #   self._rx_t     当前进度（0=普通态，1=最大化态），连续、可打断；
        #   self._rx_target 目标进度（1 展开 / 0 收起）；
        #   self._rx_full   普通态满量程（展开时现测、收起时复用）；
        #   self._rx_*      发送区高、功能列宽、开关尺寸等派生量。
        self._rx_tween: Tween | None = None
        self._rx_t = 0.0
        self._rx_target = 0.0
        self._rx_full: dict = {}
        self._rx_total = 900
        self._rx_tx_normal = 0
        self._rx_rail_normal = 48
        self._rx_rail_expanded = 48
        self._rx_row_w = 40
        self._rx_row_h = 30
        self._rx_hidden: set = set()
        self._rx_switch_active = False
        # 开关飞走期间占住它在配置卡里的那一格（见 _rx_place_home_gap）：
        # 有它，卡内版式整段零位移；没它，开关进出会让下面控件整体上下窜 41px。
        self._rx_home_gap: QWidget | None = None
        # 开关那两格的**静止态**矩形（见 _rx_remember_home_slot / _rx_place_switch）。
        # home：普通态卡片里那一格（x/宽冻结）；rail：收起方向起点那一格。
        self._rx_home_slot: QRect | None = None
        self._rx_switch_rail: QRect | None = None
        # 展开方向的 rail 落点：最大化态实测值，落定时刷新（见 _rx_finish_expanded）。
        # ⛔ 不要复用 _rx_switch_rail —— 那个是"本次转场的冻结值"，_rx_finish_normal
        #    会把它清掉（见该函数末尾），而跨转场要保持的是最大化态那份。
        self._rx_rail_rect_expanded: QRect | None = None
        # 转场里每帧要**批量开关**的布局清单（见 _rx_collect_layouts / _rx_layouts_enabled）
        self._rx_layouts: list[QLayout] = []
        self._split_tween: Tween | None = None
        self._split_from: list[int] = []
        self._split_to: list[int] = []
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
        # 历史回放模式：为真时接收区显示的是本地留存数据，实时帧不写进画面
        # （但照常进 rx_history 与计数）。见 _open_history / _exit_replay。
        self._replay_active = False
        # 提示条高度补间的句柄（见 _replay_bar_anim）：None = 当前没有在跑。
        # 中途反向点击要"从当前高度折返"，所以必须留着上一段好打断。
        self._replay_tween: Tween | None = None
        # 接收区文字交叉淡入的补间句柄（见 _cross_fade_rx）
        self._rx_shot_tween: Tween | None = None
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

    def _animate_theme(self, scheme: str) -> None:
        """换主题的**过渡**入口：自绘大色块做颜色渐变（苹果 §14：避免明暗突变）。

        ⚠️ ``_apply_theme`` 本身仍是**同步**的（一次到位）—— 自测里多处直接调它并
        立刻读像素，过渡必须只发生在这条新入口上。这里先在 t=0 把样式表一次切到
        目标主题（QSS 驱动的控件层没法逐帧插值：那要每帧重刷整张 QSS 并 re-polish
        所有控件，125Hz 下直接卡死），再对**占屏面积最大的自绘面**（页面背景 /
        卡片 / 页头 / 回放条）做 token 插值 —— 渐变它们就足以消掉"啪一下全黑/全白"
        的硬切感。
        """
        from ui_anim import animations_enabled
        if scheme == current_scheme() or not animations_enabled():
            self._apply_theme(scheme)
            return
        previous = current_scheme()
        # ① 样式表 / 调色板先一步切到目标（文字与原生控件走 QSS+palette，无法插值）
        self._apply_theme(scheme)
        # ② 自绘面从**旧色**起步，逐帧插值到新色
        self._theme_from = previous
        self._theme_to = scheme
        if self._theme_tween is None:
            self._theme_tween = Tween(280, in_out_quint, self._on_theme_mix,
                                      self._on_theme_done, parent=self)
        set_scheme_mix(previous, scheme, 0.0)
        self._repaint_self_painted()
        self._theme_tween.start(0.0, 1.0, 280)

    def _on_theme_mix(self, t: float) -> None:
        set_scheme_mix(self._theme_from, self._theme_to, float(t))
        self._repaint_self_painted()

    def _on_theme_done(self) -> None:
        # 落到目标主题的**原生** token（插值到 1.0 理论上已相等，这里再钉一次，
        # 防浮点往返造成 ±1 的色差残留）。
        set_scheme_mix(self._theme_to, self._theme_to, 1.0)
        self._repaint_self_painted()

    def _repaint_self_painted(self) -> None:
        """重绘所有**自绘面**（主题过渡逐帧调它）。"""
        for card in self._cards:
            card.update()
        self.brand_header.update()
        self._caption_bar.update()
        self.update()

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
        # ⚠️ 标题栏这一条要**出血**到窗口左右边缘：它是自绘的系统标题栏，三颗键
        # 得贴着右上角（见 ui_theme.CAPTION_INSET），不能跟着内容一起往里缩。
        # Qt 会把布局的**负外边距直接夹成 0**（实测：想要把子项往外顶 12px，
        # 拿到的是 12px 的内缩，等于没写），所以这里换个方向做 —— 根布局自己
        # 不留左右白边，把标题栏整条贴到窗口上/左/右，**其余内容**包一层带白边的
        # 容器（下面那个 content）。白边值不变，仍是为了卡片自带的那圈阴影空隙。
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(GAP - SHADOW_PAD * 2)

        # 自绘标题栏：三颗窗口键 + 可拖拽区。**不进任何会被隐藏的分组**——
        # 接收区展开时页头/配置卡/发送卡都会藏，标题栏必须留着（用户要求
        # "最大化与还原两种状态下保持一致的可点击性和显示效果"）。
        self._caption_bar = CaptionBar(self.windowTitle())
        root.addWidget(self._caption_bar)
        self._caption_bar.btn_min.clicked.connect(self.showMinimized)
        self._caption_bar.btn_max.clicked.connect(self._toggle_fullscreen)
        self._caption_bar.btn_close.clicked.connect(self.close)

        # 标题栏以下的内容容器：左右/下边距还是原来的 GAP - SHADOW_PAD
        # （卡片自己带了 SHADOW_PAD 的阴影空隙，所以外间距要相应收小，
        # 否则视觉上会比设定的 GAP 宽出一圈）。
        content = QWidget()
        root.addWidget(content, 1)
        inner = QVBoxLayout(content)
        inner.setContentsMargins(GAP - SHADOW_PAD, 0,
                                 GAP - SHADOW_PAD, GAP - SHADOW_PAD)
        inner.setSpacing(GAP - SHADOW_PAD * 2)

        self.brand_header = BrandHeader("串口调试助手")
        inner.addWidget(self.brand_header)

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
        # 实时时钟走 FlashLabel：秒一跳时整行做一次很短的高光（见 FlashLabel 的说明），
        # "活时钟"的手感由此而来，而不是硬邦邦地换数字。
        self.lbl_clock = FlashLabel()
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
        # 「定宽容器 + 卡片裁切」—— 卡内表单在转场里**一帧都不重排**。
        #
        # 原先直接把表单布局交给 card.body（addLayout）：卡一窄，QVBoxLayout 就按
        # 当前可用宽度**重新排版** —— setFixedWidth(64) 的标签被压、可编辑下拉框
        # 缩到 minimumWidth(140) 以下开始横向滚动，宽度过某个临界值时又"啪"地弹回
        # 最终版式。用户第三次反馈原话："过程中字体不要有干涉"。
        #
        # 现在表单住进一个**宽度写死**的容器：卡比容器窄时是**纯裁切**（Qt 天然
        # 把子控件裁在父控件矩形内），容器里的控件几何恒定 → 没有重排、没有挤压、
        # 没有"到某个宽度突然弹一下"。
        # 附带收益：``_open_row_home``（= 表单的 col 布局）几何也钉死了，开关
        # "回家"的落点不再随卡片宽度**每帧漂移**（原来落点一路变，尾巴上看得见
        # 开关被往回拽一下）。
        # ⚠️ 宽度 = 卡片宽 − 左右内边距（Card 的 SHADOW_PAD + CARD_PAD 各一份），
        #    和原先 addLayout 时子布局拿到的宽度**一像素不差**；只在构造时算一次，
        #    此后永不改变。
        self._config_content = QWidget()
        self._config_content.setFixedWidth(
            310 - 2 * (SHADOW_PAD + CARD_PAD))
        panel = self._build_config_panel()
        self._config_content.setLayout(panel)
        # ⚠️ setLayout 会把布局内边距改成样式默认值（本机 9px，白撑 18px）。原来的
        #    嵌套子布局内边距是 0，这里必须跟着归零，否则内容整体缩进 9px。
        panel.setContentsMargins(0, 0, 0, 0)
        self._config_card.body.addWidget(self._config_content, 1)
        body.addWidget(self._config_card)

        inner.addLayout(body, 1)

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
        # 收发计数走 FlashLabel：数值一变整行亮一下 —— 数字**立刻**是真值，
        # 只是视觉上多一次高光衰减（见 FlashLabel 的说明，这也是自测能稳定读到
        # 真值的原因）。
        self.lbl_count = FlashLabel()
        # 计数属于「参数」，VIS 规范指定用 Bahnschrift
        self.lbl_count.setFont(num_font(11.5))
        self.lbl_count.setContentsMargins(0, 0, 2, 0)
        bar_row.addWidget(self.pulse)
        bar_row.addWidget(self.lbl_state)
        bar_row.addWidget(self.lbl_count)
        inner.addWidget(bar)

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
        text = (f"{now:%Y-%m-%d %H:%M:%S}" if with_date
                else f"{now:%H:%M:%S}")
        if text != self.lbl_clock.text():
            self.lbl_clock.setText(text)
            # 秒一跳：一次很短的高光（240ms / 0.42），既让"时间在走"看得见，
            # 高光又不会常驻 —— 时钟每秒都触发，时长得收着点（见 FlashLabel）。
            self.lbl_clock.flash(0.42, duration=240)

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

        # 「连接方式」（用户 2026-10-14 点名要蓝牙广播）：串口 / 蓝牙广播。
        # 广播那条路**不配对、不连接**，设备把数据放进广播包、这边被动扫描收；
        # 代价是单向（只读）且带宽低（见 ble_io 的模块说明）。
        self.cmb_link = ChevronComboBox()
        self.cmb_link.addItems(list(LINK_CHOICES))
        self.cmb_link.setCurrentText(LINK_SERIAL)
        self.cmb_link.setToolTip(
            "串口：普通有线/蓝牙虚拟串口，需要配对或插线\n"
            "蓝牙广播：只被动扫描广播包，不配对不连接（只能读，发不了）")
        self.cmb_link.currentTextChanged.connect(self._on_link_mode_changed)
        link_row = QHBoxLayout()
        link_row.setSpacing(10)
        link_tag = self._field_label("连接方式")
        link_tag.setFixedWidth(64)
        link_row.addWidget(link_tag)
        link_row.addWidget(self.cmb_link, 1)
        col.addLayout(link_row)

        # 串口选择独占一行，刷新按钮跟在右边。
        # ⚠️ 这一行**两种模式共用**：串口模式下它是串口列表，蓝牙广播模式下换成
        #    蓝牙设备列表（标签文字和刷新按钮的提示跟着换）—— "选目标"这件事
        #    在两种模式下是一回事，没必要摆两套控件。
        self._port_label = self._field_label("串口选择")
        self.cmb_port = ChevronComboBox()
        # 可编辑：没有物理串口时也能手输端口名，或直接输入 loop:// 做无硬件回环演示
        self.cmb_port.setEditable(True)
        # 宽度由侧栏给出，这里只保底。可编辑下拉框宽度不足会滚动显示，波特率显示错位的老坑
        self.cmb_port.setMinimumWidth(140)
        self.btn_rescan = IconButton(GLYPH_REFRESH, "重新扫描串口", size=30, glyph_size=15)
        # 用 lambda 而不是直接连接：clicked 会带一个 checked=False 传进来，
        # 会把 announce 形参顶掉。走 _refresh_targets：按当前连接方式刷对应列表
        # （串口 or 蓝牙设备），两种模式共用这一颗按钮。
        self.btn_rescan.clicked.connect(lambda: self._refresh_targets())

        col.addWidget(self._port_label)
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
        # 64px 放得下「保留行数」四字，三字标签也因此对齐。
        # ⚠️ 前四行是**串口专有**的（蓝牙广播下没有波特率/校验位可言），包成一个
        #    容器整体显隐；「保留行数」是显示侧的事，两种模式都要，留在外面。
        self._serial_params = QWidget()
        params_col = QVBoxLayout(self._serial_params)
        params_col.setContentsMargins(0, 0, 0, 0)
        params_col.setSpacing(12)
        for label, widget in (("波特率", self.cmb_baud), ("停止位", self.cmb_stopbits),
                              ("数据位", self.cmb_bytesize), ("校验位", self.cmb_parity)):
            row = QHBoxLayout()
            row.setSpacing(10)
            tag = self._field_label(label)
            tag.setFixedWidth(64)
            row.addWidget(tag)
            row.addWidget(widget, 1)
            params_col.addLayout(row)
        col.addWidget(self._serial_params)

        line_row = QHBoxLayout()
        line_row.setSpacing(10)
        line_tag = self._field_label("保留行数")
        line_tag.setFixedWidth(64)
        line_row.addWidget(line_tag)
        line_row.addWidget(self.cmb_rx_lines, 1)
        col.addLayout(line_row)

        # 「打开历史数据」（用户 2026-10-13 点名）：在软件内打开本地留存的
        # 接收数据，格式/风格/功能与实时接收一致。放在参数栏最下面 —— 它读的
        # 正是上面"保留行数"落在本地的那份备份，挨着最顺。
        # ⚠️ 它是**开关**（回放态下再点 = 返回实时），文案随状态在「打开历史数据」
        #    /「关闭历史数据」之间切（见 _refresh_replay_ui）。两个文案都是 6 个字，
        #    切换时按钮宽度不变，布局不会抖。
        self.btn_open_history = InkButton("打开历史数据", variant="ghost")
        self.btn_open_history.setToolTip(
            "在接收区打开本地留存的接收数据；回放中再点一次返回实时"
            "（格式与实时一致，可搜索 / 定位异常 / 复制）")
        self.btn_open_history.clicked.connect(self._open_history)
        hist_row = QHBoxLayout()
        hist_row.setSpacing(10)
        spacer = QWidget()
        spacer.setFixedWidth(64)
        hist_row.addWidget(spacer)
        hist_row.addWidget(self.btn_open_history, 1)
        col.addLayout(hist_row)

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
        #
        # 这排工具栏的收展**并入 _rx_transition 的统一时间线**（不再单独建 Reveal）：
        # 多股补间并行、各停各的正是"最大化时工具栏啪一下没了 / 结尾跳一下"的根因。
        #
        # 「定高内容 + 行裁切」，和配置卡同一个套路：行高被压缩时若让 QHBoxLayout
        # 自己重排，里面的开关/下拉框会被**竖直居中** —— 高度一小它们就整体上下
        # 滑动（逐帧实测相对行顶漂 25px，看着就是"图标滑位、文字被切一半"）。
        # 内容钉成自己的自然高度、贴着行的上沿，行高度再小也只是把下沿裁掉。
        self._rx_opts_row = QWidget()
        self._opts_content = QWidget()
        self._opts_content.setSizePolicy(QSizePolicy.Policy.Preferred,
                                         QSizePolicy.Policy.Fixed)
        self._opts_content.setLayout(opts)
        opts.setContentsMargins(0, 0, 0, 0)
        opts_row_lay = QVBoxLayout(self._rx_opts_row)
        opts_row_lay.setContentsMargins(0, 0, 0, 0)
        opts_row_lay.setSpacing(0)
        # AlignTop：只做竖直对齐，不给它拉伸 —— 高度富余时也保持自然高度贴顶。
        opts_row_lay.addWidget(self._opts_content, 0, Qt.AlignmentFlag.AlignTop)
        self._rx_opts_row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        left.addWidget(self._rx_opts_row)

        self.txt_rx = QPlainTextEdit()
        self.txt_rx.setReadOnly(True)
        # 字号来自定制配置（设置面板里那根滑杆，见 _apply_rx_font_size）
        self.txt_rx.setFont(mono_font(customization.display_value("rx_font_size")))
        self.txt_rx.setMaximumBlockCount(self._rx_blocks)
        self.txt_rx.setUndoRedoEnabled(False)
        # 空态提示：回到原生占位文字。
        # ⚠️ 方案 2（写进文档的实文字）试过并**已回退**：提示与数据同处一个文档，
        #    "清提示"的时机稍有不慎就会把刚到的新数据一起抹掉（实测连挂 4 条
        #    端到端自测：接收区拿不到回显）。QLabel 浮层也不行 —— 挂 viewport
        #    会被重建丢弃、挂控件上会被 viewport 盖住，两次都画不出来。
        #    占位文字"比正文暗一半"是 Qt 的既定行为（按文字色 ×≈0.55 画），
        #    要改它得动编辑器控件的样式机制，需要单独一轮、带足验证再做。
        # ⛔ **不设占位提示**（2026-10-16 用户第五次反馈后决定去掉）。
        # 原因：QPlainTextEdit 的占位文字被 Qt 按"文字色 × ≈0.55"绘制（实测亮度
        # 132，而同一张卡里其它字是 242），且只要控件有 QSS，调色板里的
        # PlaceholderText 就完全不参与 —— 想让那句字变亮一共试了五种方案
        # （调色板 / QLabel 挂 viewport / 挂控件 / 挂卡片 / 写进文档 / 删 QSS），
        # 不是画不出来就是把数据流搞坏。空面板本身就足够说明"还没数据"，
        # 与其留一句永远比别处暗一半的字，不如不要。
        # 详见 .workbuddy/memory/2026-10-16.md 的"别再走这些路"那张表。
        # 时间戳和每个采样点的"序号"染蓝（见 ui_highlight）。纯装饰层：
        # 文档里的文字一个字符都没改，保存/搜索/复制走的仍是纯文本。
        self._rx_highlighter = RxHighlighter(self.txt_rx.document())
        # 回放 ←→ 实时切换时那张"旧内容"淡出层（见 _RxShot / _cross_fade_rx）。
        # 挂 viewport 才能盖住正文；常隐藏，只在切换的那 160ms 里出现。
        self._rx_shot = _RxShot(self.txt_rx.viewport())
        # 历史回放提示条（回放时才显示，见 _refresh_replay_ui）
        self._replay_bar = self._build_replay_bar()
        # 初始是"收起"态：高度钉 0 + 隐藏，布局从一开始就把它当不存在。
        # 后面所有显隐都走 _replay_bar_anim（0 ⇄ sizeHint 高度，可打断）。
        self._replay_bar.setMinimumHeight(0)
        self._replay_bar.setMaximumHeight(0)
        self._replay_bar.hide()
        left.addWidget(self._replay_bar)
        self._search_bar = self._build_search_bar()
        # 展开/收起走连续过渡（高度 + 不透明度，120fps）。直接 show/hide 是一帧
        # 跳变，日志框会被猛地顶下去，在高刷屏上很扎眼。
        # ⚠️ 曲线用 ``EASE``、时长 220ms，和 ``_replay_bar_anim`` 对齐：这条动画
        #    动的也是**版式占位**（会把日志框顶走），项目定论是这类动画用 EASE
        #    而不是 ``in_out_*``（逐帧对照表见 ``ui_anim.EASE`` 上方注释）——
        #    ``in_out_quint`` 前 72ms 全在 3px 以内（用户报"前 80ms 纹丝不动"），
        #    中段单步 8~9px 又是一跳；换成 EASE 后 13 个台阶、单步 ≤8px、首个
        #    ≥2px 在 24ms，全程每一帧都看得见在走。
        self._search_reveal = Reveal(self._search_bar, 220, curve=EASE,
                                     parent=self)
        self._search_bar.hide()
        left.addWidget(self._search_bar)
        left.addWidget(self.txt_rx, 1)

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
        # ⛔ **2026-10-17 用户口径（最终版）**：图标**贴列的左边**（AlignLeft）——
        #    "距离左边框的距离不要改变，改变距离右边框的距离"：最大化态开关进列顶、
        #    列被撑宽到开关宽度，图标跟着列的**左边**走（左边距不变），离卡片右缘
        #    自然变远。别再改回 AlignRight。
        for button in (self.btn_search, self.btn_alert, self.btn_pause,
                       self.btn_copy_all, self.btn_clear, self.btn_save,
                       self.btn_expand):
            rail.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        rail.addStretch(1)
        # 包一层容器，把整列当成一个控件交给布局（间距、对齐都只算一次）
        self._rx_rail = QWidget()
        self._rx_rail.setLayout(rail)
        # 右侧整列 = [串口开关（展开态才进列顶，普通态在配置卡）] + [7 颗图标居中]，
        # 日志数据栏在列的**左侧**。开关进列/出列见 _move_open_row_to_rx / _home。
        area.addLayout(left, 1)
        area.addWidget(self._rx_rail)
        return area

    def _build_replay_bar(self) -> QWidget:
        """历史回放提示条：告诉用户"现在看的是本地留存数据"，并给一个返回实时的出口。

        圆角/描边由 ``ReplayBar.paintEvent`` 自绘（不能用 QSS，见那个类的说明）。
        """
        bar = ReplayBar()
        bar.setObjectName("replayBar")
        row = QHBoxLayout(bar)
        # 右内边距 12 不是随便取的：条的圆角是 CARD_RADIUS(26)，但条高只有
        # 50 → 实际生效半径 = 25，两端成了**半圆**；里面这颗「返回实时」按钮
        # 高 38，四角必须落在那个半圆之内，否则按钮描边会戳出条的底色。
        # 反解：内边距 >= 25 - sqrt(25² - (25-6)²) ≈ 8.75（实测 6px 时戳出
        # 1.9px），取 12 留余量。左侧那颗小图标（13px）离角远得多，10 够用。
        row.setContentsMargins(10, 6, 12, 6)
        row.setSpacing(8)
        icon = IconLabel(GLYPH_SAVE, 13)
        row.addWidget(icon)
        label = QLabel("正在查看本地留存的接收数据（格式与实时一致，可搜索 / 定位异常 / 复制）")
        label.setFont(ui_font(11))
        label.setObjectName("replayCaption")
        row.addWidget(label, 1)
        back = InkButton("返回实时", variant="ghost")
        back.clicked.connect(self._on_replay_close)
        row.addWidget(back)
        return bar

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
        area.addLayout(left, 1)
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
            self._animate_opts_row(False)
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
                self._animate_opts_row(True)
            self._opts_visible_on_resume = False
            # 恢复 = 把暂停期间攒下的帧一次性刷进来再追底 —— 视图直接落在
            # 最新读取的数据上（少这一步，新数据全堆在看不见的队列里）。
            self._flush_paused_frames()

    def _refresh_pause_ui(self) -> None:
        self.btn_pause.set_glyph(GLYPH_PLAY if self._paused else GLYPH_PAUSE)
        self.btn_pause.set_active(self._paused)
        self.btn_pause.setToolTip("继续滚动（空格）" if self._paused
                                  else "暂停滚动（空格）")

    # ---- 暂停时工具栏的收展（走补间，不再一步 hide） ----

    def _opts_row_natural_height(self) -> int:
        """工具栏的自然行高。

        行被压扁/隐藏时 ``height()`` 是旧值或 0，所以取内容控件的 sizeHint；
        都拿不到（还没上过屏）就退回实测行高 34。
        """
        for probe in (self._opts_content, self._rx_opts_row):
            try:
                hint = probe.sizeHint().height()
            except RuntimeError:
                hint = 0
            if hint > 4:
                return hint
        return 34

    def _animate_opts_row(self, show: bool, duration: int = 240) -> None:
        """把「显示时间戳 / HEX / 编码」这排工具栏**动画**收起来或展开。

        以前是 ``hide()`` / ``show()`` 一步到位：接收区视口当场多出（或少掉）
        44px（34 行高 + 10 布局间距），紧挨着 240ms 的发送区补间就是"啪"一下
        的硬跳 —— 用户说的"暂停的动画不够顺滑"，一半来自这一下。现在按行高
        插值、和发送区同一条时间线（240ms / in_out_quint），落定再把 min/max
        交还布局（与 ``_rx_finish_normal`` 同一个收尾口径）。
        """
        row = self._rx_opts_row
        target = self._opts_row_natural_height() if show else 0
        if show:
            # ⛔ 先压到 0 再 show：否则 show 的瞬间行就顶到满高，等于没动画
            row.setFixedHeight(0)
            row.show()
            begin = 0
        else:
            begin = row.height() if row.isVisible() else 0
            if begin <= 0:
                return
            row.setFixedHeight(begin)
        self._opts_from = float(begin)
        self._opts_to = float(target)
        if self._opts_tween is None:
            self._opts_tween = Tween(duration, in_out_quint, self._on_opts_anim,
                                     self._finish_opts_anim, parent=self)
        self._opts_tween.start(0.0, 1.0, duration)

    def _on_opts_anim(self, t: float) -> None:
        height = int(round(self._opts_from + (self._opts_to - self._opts_from) * t))
        try:
            self._rx_opts_row.setFixedHeight(max(0, height))
        except RuntimeError:
            pass

    def _finish_opts_anim(self) -> None:
        """落定：收起方向停在 0 高，展开方向把 min/max 还给布局。

        ⛔ 收起方向**不要 hide()**：hide 会把布局间距（10px）一起收走，落定那
        一帧接收区视口又要跳 10px（44px 的硬跳改动画后只剩这条尾巴）。留一个
        0 高的**可见**行，间距原地不动，开合两头都连续。
        """
        row = self._rx_opts_row
        try:
            if self._opts_to <= 0:
                row.setFixedHeight(0)
            else:
                row.setMinimumHeight(0)
                row.setMaximumHeight(WIDGETSIZE_MAX)
        except RuntimeError:
            pass

    def _cancel_opts_anim(self) -> None:
        """打断工具栏补间并把尺寸约束还给布局（接收区全屏过渡要重算行高）。"""
        tween = self._opts_tween
        if tween is not None:
            tween.stop()
        try:
            self._rx_opts_row.setMinimumHeight(0)
            self._rx_opts_row.setMaximumHeight(WIDGETSIZE_MAX)
        except RuntimeError:
            pass

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
        # 窗口尺寸变过之后，最大化态 rail 落点的缓存就过期了 —— 清掉，下次展开
        # 落定时重新量（缓存未命中时退化成就地现算，不会更差）。
        self._rx_rail_rect_expanded = None
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
        # ⛔ 先把在途的转场补间**冻结**在当前进度：下面 showFullScreen + 等窗口落地
        #    会阻塞（最多 160ms），不冻结的话反向点击时旧补间会在这段时间里继续跑
        #    —— 就是用户说的"先跑完再反向"。_rx_transition 会从冻结值起跑。
        if self._rx_tween is not None and self._rx_tween.running:
            self._rx_tween.stop()
        # ⚠️ 动画期间**推迟着色 + 合并日志追加**（见 RxHighlighter.defer 与
        #    _set_rx_coalesce）：250 行/秒下这两项会和这次大布局 + 大面积重绘抢帧
        #    （QA 实测展开时 P50 掉到 42fps、>20ms 帧 42 个）。
        #    ⛔ 不是冻结重绘 —— 那条路正是用户报的"接收区消失一段时间"的根因：
        #    已有内容全程可见，新数据最多晚 60ms 成批出现。
        self._rx_highlighter.defer(420)
        self._set_rx_coalesce(RX_COALESCE_MS)
        QTimer.singleShot(300, self._end_rx_coalesce)
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
        # ⛔ 动画必须在**窗口尺寸已经落地**之后才起跑，否则中途会整屏跳一次
        #    （见 _settle_window_state 的实测数据）。
        self._settle_window_state(True)
        # 功能键列改成"贴着数据卡片"（不再整列垂直居中）—— 见 _set_rail_compact
        self._set_rail_compact(True)
        # 全屏时接收区上方的工具栏（显示时间戳/HEX/编码那排）整个藏掉，
        # 搜索条若开着也收起 —— 全屏就是纯看数据。
        # ⚠️ 这排的收展由 _rx_transition 统一驱动（和卡片让位同一条时间线），
        #    不再单独起一条 Reveal —— 多股补间各停各的 = 速度折点 = "跳一下"。
        # 用户 22:29 明确：**不要改最大化后的主题颜色**。日志底色保持不变，
        # 只保留下面这几件真正必要的（卡片让位、发送区压零、开关搬家）。
        if self._search_bar.isVisible():
            self._close_search()
        # ⚠️ 记下"展开前本来就隐藏"的块（用户可以在定制里把某块关掉）：收起时
        #    不能无条件把它们 to(True) 显示回来 —— 那会把布局最小宽度顶大，
        #    窗口几何也回不到原位（自测里 839 被顶成 1134 就是这个）。
        self._pre_hidden_blocks = [w for w in (self.brand_header, self._config_card,
                                               self._tx_card) if not w.isVisible()]
        # ⚠️ 记住发送区"展开前是不是折叠的"：开局默认就是折叠（定制项）。
        self._pre_send_collapsed = self._tx_collapsed
        # 连同**真实高度**一起记：用户可能手工拖过分割器，收起时要原样还回去
        # （只按 TX_COLLAPSED_H / tx_min_height 推是猜的，测试里量到对不上）。
        self._pre_send_height = max(1, self._tx_card.height())
        # ⚠️ 动画**与窗口几何同步起跑**（不再"等几何落地再起"）。
        #    曾经为了躲开那帧整窗重绘，先轮询等全屏几何落地（最多 ~96ms）再起动画
        #    —— 那段等待里画面是"窗口已经全屏、卡片却还没动"的空档，用户看到的
        #    正是"接收区消失了一段时间" + "先跳一下再动"。
        #    现在 showFullScreen 与卡片让位同一拍开始，整段是一次连续的运动，
        #    接收区全程在屏上（逐帧采样验证见 devtools/_probe_fps.py）。
        # 所有几何（brand/config/opts/分割器/功能列/开关）由**一条时间线**驱动，
        # 每帧一次布局 —— 见 _rx_transition。
        self._rx_transition(True)
        self.btn_expand.set_glyph(GLYPH_RESTORE)
        self.btn_expand.setToolTip("退出全屏（Esc）")

    # ⛔ 这里曾有 ``_suspend_editors`` / ``_resume_editors`` 一对空实现（用
    #    ``setUpdatesEnabled(False)`` 冻结日志区重绘的旧方案）。**已彻底删除**：
    #    冻结换不来帧率（实测 P95 130~243fps、>20ms 帧为 0），却会让"接收区信息
    #    消失一段时间"—— 谁想再加这条优化，先看 2026-10-17 的帧率数据。

    def _settle_window_state(self, fullscreen: bool, timeout_ms: int = 160) -> None:
        """把**挂起的**窗口状态变更压到动画起跑之前落地。

        ⛔ ``showFullScreen()`` / ``showNormal()`` 是异步的：调用后窗口尺寸要过
        一会儿才真正变。动画先起跑、窗口后落地的话，落地那一帧整个布局会跳一次
        —— 逐帧实测 rx_card 单帧 +207px、txt_rx +223px（"最大化/退出时抽搐一下"）。
        这里只等"客户区尺寸到位"，最多 160ms；窗口本来就是目标状态时立刻返回
        （用户平时的路径 —— 开机即全屏 —— 等待为 0）。
        """
        app = QApplication.instance()
        if app is None:
            return
        if fullscreen:
            want = self.screen().geometry().size() if self.screen() else None
        else:
            want = self._pre_expand_geometry.size() if self._pre_expand_geometry else None
        deadline = time.perf_counter() + timeout_ms / 1000.0
        while time.perf_counter() < deadline:
            got = self.size()
            if want is None:
                # 没有明确目标尺寸：以"状态真的切过去了"为准
                if self.isFullScreen() == fullscreen:
                    break
            elif got == want:
                break
            app.processEvents()
            time.sleep(0.004)
        app.processEvents()

    # ---------------------------------------------------------- 接收区转场（统一时间线）
    #
    # 2026-10-18 用户第三次反馈"仍跳变"，要求**根因修复**。上一版这里同时跑
    # 四~五条补间（brand/config/opts 三块 Reveal、发送区分割器、开关飞行），
    # 各有各的时长/曲线/起跑时刻：速度在合成处相加、短的那条先停 → 折点；
    # 各自 _on_finished 又落在不同帧 → 收尾一连串小跳（逐帧实测单帧 60~110px）。
    #
    # 现在只有一条 Tween(260, STANDARD)：_on_rx_transition(t) 里一次性把所有
    # 几何算出来并应用（每帧只触发一次布局），收尾也只有一个 _on_rx_done。
    # t 连续 → 中途再次点击从当前 t 反向（Tween.start(self._rx_t, target)），
    # 不跳回起点、也不跑完再反向。展开/退出用同一条公式（t 反向即镜像曲线）。
    _RX_TRANSITION_MS = 260

    # 配置卡"提前到位"的进度占比：q（普通态进度 0→1）走到 0.85 时卡片已是满宽。
    #
    # ⚠️ 这个数按**时间**标定过，不是拍的：基准曲线 STANDARD 前段极快 ——
    #    它的 q 到 0.45 只花了 **18% 的时间**（≈48ms）。照字面用 0.45 当阈值，
    #    310px 的卡会在 48ms、也就是三四帧里长完：逐帧峰值 **10.5px/ms**
    #    （≈168px/帧），比现在还像"跳"。0.85 的结果（同一套数值微分算出）：
    #      · 卡片在 t≈47%（≈124ms）长到满宽 → 后一半时间它**停住**等开关回来；
    #      · 峰值 5.56px/ms（原线性 4.72，只 +18%）；
    #      · max/中位 反而从 6.87 降到 2.48 —— 同样 310px 摊在更少的帧里，
    #        中位速度被抬高，尖峰不再孤立。
    #    既满足"提前预留开关位"，又不制造新的尖峰。
    _RX_CARD_AHEAD = 0.85

    def _rx_transition(self, to_expanded: bool) -> None:
        """接收区最大化 / 退出：**一条时间线**驱动全部几何（见上方说明）。"""
        target = 1.0 if to_expanded else 0.0
        self._expanded = to_expanded
        if to_expanded and self._rx_t <= 0.01:
            # 只在与普通态交界处现测一次满量程；打断途中沿用旧值，不重测
            self._rx_capture_full()
        elif not to_expanded and not self._rx_full:
            self._rx_capture_fallback()
        self._rx_prepare(to_expanded)
        self._rx_target = target
        if self._rx_tween is None:
            # ⚠️ min_interval_ms=16：接收区最大化每帧都要重排三张大卡 + 磨砂重绘，
            #    单帧能到 13~22ms，扛不住 165Hz 的 6ms 节拍。不节流的话计时器会在
            #    一次重绘卡顿后**连发两拍**（实测两次 tick 只隔 1.7~3.5ms），把攒下
            #    的进度一口气推完 —— 逐帧看就是一帧滑出去 60~86px 的"跳"，且高分
            #    辨率屏上这种连发更多、尾巴更长（时长 60Hz 275ms vs 165Hz 320ms）。
            #    限到 ≈60fps 稳定节拍后：进度仍按 perf_counter 算（时长不变、终点帧
            #    不吞），但不再有"追赶式连发"，每帧位移摊在真实 dt 上 → 速度连续。
            #    这是 Tween 专门为"每帧改布局的重动画"留的档，不是调阈值糊。
            self._rx_tween = Tween(self._RX_TRANSITION_MS, STANDARD,
                                   self._on_rx_transition, self._on_rx_done,
                                   parent=self, min_interval_ms=16)
        self._rx_tween.start(self._rx_t, target)

    def _rx_card_slot_w(self) -> int:
        """开关"占位"需要的卡片宽度：左右内边距 + 开关本身宽。

        卡片窄于这个值时，正在飞行的开关就会比卡还宽、戳到卡外面去。
        """
        return 2 * (SHADOW_PAD + CARD_PAD) + max(1, self._rx_row_w)

    def _rx_card_width(self, q: float) -> int:
        """配置卡这一帧该有多宽（0=收起，满宽=普通态满宽）。

        「先让位 / 再归位 / 最后收尾」：卡片用自己**前约 47% 的时间**长到满宽，
        之后停住等开关飞回来落位 —— 这就是用户要的"提前预留好串口开关的位置"，
        也是"动画讲求逻辑性"：卡片先把位置腾好，开关再回到位。
        展开方向同一条公式（q 反向递减）→ 卡片先撤走，开关随后飞出去，严格镜像。

        宽度是 q 的**单调不减**函数（``min`` / ``max`` 组合），所以不会回抽、
        不会"长到 90% 又缩回去再长"。
        """
        full = max(1, int(self._rx_full.get("config_w", 310)))
        ratio = min(1.0, max(0.0, q) / self._RX_CARD_AHEAD)
        cw = int(round(full * ratio))
        # 硬保底：开关还在飞的**后半程**（q ≥ 0.5，它已经在卡片这一侧了）时，
        # 卡片不得窄于"一个开关位"，免得出现开关比卡还宽、戳出卡外的帧。
        # 正常几何下这条不生效（q ≥ 0.5 时按上面的比值卡片早已满宽），留着是防
        # 字体/DPI 把开关撑宽之后，这条不变量被悄悄破坏。
        if self._rx_switch_active and q >= 0.5:
            cw = max(cw, self._rx_card_slot_w())
        return cw

    def _rx_capture_full(self) -> None:
        """在**普通态**下量出各块的满量程（展开时现测，收起时复用）。"""
        self._rx_full = {
            "brand_h": max(1, self.brand_header.height()),
            "config_w": max(1, self._config_card.width()),
            "opts_h": max(1, self._rx_opts_row.height()),
            "rail_w": max(1, self._rx_rail.width()),
        }
        # 工具栏内容钉成**普通态的自然高度**：不钉的话行高被压缩时它的布局会跟着
        # 重排（逐帧实测行高 0→34 的过程里，里面的开关相对行顶漂了 29px —— 看着
        # 就是图标在滑）。钉住之后行再矮也只是把内容下沿裁掉。
        self._opts_content.setFixedHeight(self._rx_full["opts_h"])
        if self._pre_send_collapsed:
            self._rx_tx_normal = TX_COLLAPSED_H
        else:
            self._rx_tx_normal = max(TX_COLLAPSED_H,
                                     int(self._pre_send_height or self._tx_min_height))
        self._rx_rail_normal = self._rx_full["rail_w"]
        row = self._open_row
        self._rx_row_w = max(1, row.sizeHint().width())
        self._rx_row_h = max(1, row.sizeHint().height())
        lay = self._rx_rail.layout()
        m = lay.contentsMargins() if lay is not None else None
        pad = (m.left() + m.right()) if m is not None else 0
        self._rx_rail_expanded = max(self._rx_rail_normal, self._rx_row_w + pad)
        self._rx_hidden = {w for w in (self.brand_header, self._config_card,
                                       self._tx_card) if w in self._pre_hidden_blocks}

    def _rx_capture_fallback(self) -> None:
        """没有展开前记录（如开机即最大化）时的兜底满量程。"""
        self._rx_full = {
            "brand_h": max(1, self.brand_header.sizeHint().height()),
            "config_w": max(1, self._config_card.width()) or 310,
            "opts_h": max(1, self._rx_opts_row.sizeHint().height()),
            "rail_w": max(1, self._rx_rail_normal),
        }
        # 同上：内容高度钉死（这条路径没在普通态量过，用 sizeHint 兜底）
        self._opts_content.setFixedHeight(self._rx_full["opts_h"])
        self._rx_tx_normal = (TX_COLLAPSED_H if self._pre_send_collapsed
                              else max(TX_COLLAPSED_H, self._tx_min_height))
        self._rx_row_w = max(1, self._open_row.sizeHint().width())
        self._rx_row_h = max(1, self._open_row.sizeHint().height())
        self._rx_rail_expanded = max(self._rx_rail_normal, self._rx_rail.width())

    def _rx_prepare(self, to_expanded: bool) -> None:
        """转场起跑前的准备：掐掉手动分割器补间、接管开关、钉住发送卡策略。

        ⚠️ 必须对**中途反向**安全：不去重置"已在该值上"的东西（否则反向那一下
        会跳）。分割器尺寸、发送卡上限都由 _on_rx_transition 每帧按 t 现算。
        """
        if self._split_tween is not None and self._split_tween.running:
            self._split_tween.stop()
        # 暂停时工具栏可能正在收/展（见 _animate_opts_row）：先把行高还给布局，
        # 下面 _rx_full["opts_h"] 才是真值（否则会把半路的 0~34 记成"自然高度"）。
        self._cancel_opts_anim()
        self._rx_total = sum(self._splitter.sizes()) or 900
        self._set_tx_vpolicy(QSizePolicy.Policy.Ignored)
        self._tx_card.setMinimumHeight(0)
        if self._tx_card.isHidden() and not to_expanded:
            # 从最大化态收起：发送卡藏着 → 先钉 0 上限再 show（否则布局当场
            # 抢走 64px，动画没有起点），之后由每帧抬上限把它喂大。
            self._tx_card.setMaximumHeight(0)
            self._rx_card_show_tx()
        else:
            self._tx_card.setMaximumHeight(max(0, self._tx_card.height()))
        # ⚠️ 顺序要紧：先在**还看得见、还没被钉宽**的这一刻把"卡片那一格"记下来
        #    （收起方向此刻卡是藏着的，量不到，只能用上次静止态记下的值）。
        already_flying = (self._rx_switch_active
                          and self._open_row.parentWidget() is self)
        self._rx_remember_home_slot(to_expanded)
        # 功能列那一格的"冻结值"：
        #   收起方向：此刻还是最大化版式，直接量（和原来一样准）。
        #   展开方向：此刻是普通版式，最大化那一格还不存在 → 用上次落定时缓存的
        #   值；没有缓存（首次展开 / 冷启动即最大化）才回退逐帧现算。
        # ⚠️ 展开方向为什么不能逐帧现算：rail 每帧被 setFixedWidth 改列宽，它的
        #    x 跟着漂（起手那一瞬窄列甚至落在开关家格左边）→ 开关追着一个正在
        #    移动的落点飞，头部先被拽回去 14~17px 再折返（用户报的"回抽"，
        #    量化见 devtools/_probe_rx_switch_retract.py 的阳性对照）。
        if not already_flying:
            self._rx_switch_rail = (self._rx_rail_rect() if not to_expanded
                                    else self._rx_rail_rect_expanded)
        self._rx_detach_switch()
        # 配置卡宽度：**两个方向都先钉住**（原来只在展开方向钉）。
        # 开关离开卡片的那一瞬间卡的 sizeHint 就变小了，布局会当场按内容换一次
        # 宽度；收起方向也一样 —— 卡刚从隐藏态长回来时，布局先按内容算一次的宽度
        # 和随后时间线给的宽度不是一个值，那就是"起手抖一下"。
        # 钉成当前真实宽之后，宽度**只由时间线**逐帧决定，起手/落定都不跳。
        self._rx_collect_layouts()      # 本段每帧批量开关的那份布局清单（见该函数）
        self._config_card.setMinimumWidth(0)
        self._config_card.setFixedWidth(max(1, self._config_card.width()))
        # 起手这一帧就先把布局落地：上面「摘开关 + 插占位」改的是卡内的 col 布局，
        # 不落地的话第一帧画出来是"少一格"的中间态（逐帧实测标签差 29px，
        # 要等下一帧才补齐 —— 用户看到的就是卡内文字闪一下）。
        self._rx_apply_layout_now()

    def _rx_card_show_tx(self) -> None:
        """显示发送卡（内容按需），供收起路径把 pane 从 0 喂起来。"""
        if self._tx_content.isHidden():
            self._tx_content.show()
        self._tx_card.show()

    def _rx_remember_home_slot(self, to_expanded: bool) -> None:
        """记下"开关在普通态那一格"的矩形（只在**普通态、开关还在位**时记）。

        为什么要有它：卡片在动画里是**右对齐长宽**的，`home.x` = 卡左沿 + 内边距，
        于是它的 x 会跟着卡片宽度一路漂 —— 逐帧实测收起段从 1607 漂到 1302
        （305px）。开关如果追着这个漂移的落点飞，路径上就会先往右窜一下再往回
        走（用户说的"没预留位置、被往回拽"）。所以 x/宽用这份**静止态**记下的
        值，y 仍逐帧现算（纵向是整页布局在变，那是真实世界在动，该跟着走）。
        """
        if not to_expanded:
            return                       # 收起方向此刻卡是藏着的，量不到，沿用旧值
        row = self._open_row
        if row.parentWidget() is self:   # 正飞着（中途反向）：不是静止态
            return
        self._rx_home_slot = QRect(row.mapTo(self, QPoint(0, 0)), row.size())

    def _rx_home_target(self) -> QRect:
        """开关在卡片里那一格的**落点**（x/宽冻结、y 现算，见上）。"""
        live = self._rx_home_rect()
        slot = self._rx_home_slot
        if slot is None:                 # 冷启动即最大化：没有静止态记录，退化为现算
            return live
        return QRect(slot.x(), live.y(), max(1, slot.width()), self._rx_row_h)

    def _rx_detach_switch(self) -> None:
        """把串口开关从布局里摘出来挂到窗口上（飞行由每条帧摆位）。

        中途反向时它已经在窗口上飞着，直接复用即可（_rx_switch_active）。

        ⭐ 摘走的同时在**原位补一个同尺寸占位**：这一格就是用户说的
        "提前预留好串口开关的位置" ——
          · 不给占位：开关一离开 col 布局，下面所有控件立刻整体上移 41px，
            落定时开关插回去又整体下移 41px（逐帧实测：``串口选择`` 标签相对
            卡片的位置变了 41px）。用户眼里就是"文字突然窜一下 / 字体在打架"。
          · 给了占位：整段动画里这一格始终在，卡片里的版式一帧都不变；开关
            飞回来的落点就是这一格，落定换回真控件时零位移。
        """
        if self._rx_switch_active and self._open_row.parentWidget() is self:
            return
        row = self._open_row
        rail_lay = self._rx_rail.layout()
        in_rail = rail_lay is not None and rail_lay.indexOf(row) >= 0
        if row.parentWidget() is self:
            start = QRect(row.geometry())
        else:
            start = QRect(row.mapTo(self, QPoint(0, 0)), row.size())
        start.setHeight(max(1, start.height()))
        if in_rail and rail_lay is not None:
            rail_lay.removeWidget(row)
        elif self._open_row_home.indexOf(row) >= 0:
            self._open_row_home.removeWidget(row)
        self._rx_place_home_gap()
        row.setParent(self)
        row.setGeometry(start)
        row.show()
        row.raise_()
        self._rx_switch_active = True

    def _rx_place_home_gap(self) -> None:
        """在开关的原始格位放一个**同尺寸占位**（原因见 _rx_detach_switch）。

        高度取 ``_rx_row_h``（= 开关行的自然高度，转场开始时现测）；每次转场都
        重新对齐一次，主题/DPI 变了也不会停在旧高度上。
        """
        height = max(1, self._rx_row_h)
        gap = self._rx_home_gap
        if gap is None:
            gap = QWidget()
            gap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._rx_home_gap = gap
            # ⚠️ 顺序：**先定高 → 再进布局 → 最后 show()**。新建 QWidget 的
            #    sizeHint 无效（进布局那一瞬高度算 0），而插入/show 都可能**同步**
            #    触发一次布局 —— 顺序一颠倒就会留下一帧"占位 0 高"的中间态
            #    （逐帧实测卡内标签先少 29px、下一帧再补回来，看着就是闪一下）。
            gap.setFixedHeight(height)
            self._open_row_home.insertWidget(0, gap)
            # ⚠️ 必须 show()：**隐藏的控件会被布局直接忽略**，占位等于没放
            #    （首轮实测：少了这一句，第一次展开时卡内版式整体上移 41px）。
            gap.show()
        else:
            gap.setFixedHeight(height)

    def _rx_clear_home_gap(self) -> None:
        """收掉占位（真开关要插回同一格之前必须清掉，否则会占两格）。"""
        gap = self._rx_home_gap
        if gap is None:
            return
        self._open_row_home.removeWidget(gap)
        gap.setParent(None)
        gap.deleteLater()
        self._rx_home_gap = None

    def _on_rx_transition(self, t: float) -> None:
        """每一帧：按进度 t 一次性算出并应用**全部**几何（0=普通, 1=最大化）。"""
        self._rx_t = t
        p = t
        q = 1.0 - p
        # ⛔ **这一帧的布局只许落地一次**。
        #    实测：光靠"先设好所有 fixed*、最后 activate 一次"是不够的 —— 每个
        #    setFixedHeight/setFixedWidth/show/hide 都会让某层布局**当场**重排一次，
        #    于是同一帧里 rx_card / txt_rx / opts 各自落地两三遍，彼此间隔 1.6~4.3ms。
        #    第二次（也是最正确的一次）永远来自末尾的 _rx_apply_layout_now，前面
        #    那些都是拿"半更新的输入"算出来的中间态 —— 165Hz 逐帧取样就会量到
        #    "100px / 6.1ms"（理论峰值 4.91 的 3.4 倍）。
        #    解法是 Qt 自己的批处理开关：把这段用到的布局全部 setEnabled(False)，
        #    期间所有 LayoutRequest 都不响应；收尾 setEnabled(True) + 一次
        #    _rx_apply_layout_now，整帧只有一次落地。
        self._rx_layouts_enabled(False)
        try:
            # brand 页头（竖向收放）
            if self.brand_header not in self._rx_hidden:
                bh = int(round(self._rx_full["brand_h"] * q))
                if bh <= 3:
                    self.brand_header.hide()
                else:
                    self.brand_header.setFixedHeight(bh)
                    self.brand_header.show()
            # config 配置卡（横向收放）。宽度走 _rx_card_width：**提前到位**
            # （展开方向先撤走、收起方向先让位），后半程停住等开关飞回来。
            # 卡内是定宽容器，卡窄时只是裁切，表单不重排（见 _build_ui）。
            if self._config_card not in self._rx_hidden:
                cw = self._rx_card_width(q)
                if cw <= 3:
                    self._config_card.hide()
                else:
                    self._config_card.setFixedWidth(cw)
                    self._config_card.show()
            # opts 工具栏（竖向收放）。收起方向：一开始就 show（0 高、逐帧长回来，
            # isVisible 立即为真 —— 和旧行为一致）；展开方向：收到 ≤3px 才 hide。
            # 暂停中收起保持藏（沿用旧的守卫）。
            collapsing = self._rx_target < 0.5
            oh = int(round(self._rx_full["opts_h"] * q))
            if self._paused and collapsing:
                self._rx_opts_row.hide()
            elif collapsing:
                self._rx_opts_row.setFixedHeight(max(0, oh))
                self._rx_opts_row.show()
            elif oh <= 3:
                self._rx_opts_row.hide()
            else:
                self._rx_opts_row.setFixedHeight(oh)
                self._rx_opts_row.show()
            # 功能列宽度：普通宽 → 最大化宽（容下开关）
            # ⚠️ 必须在 setSizes **之前**设：``setFixedWidth`` 只把 LayoutRequest 排进
            #    队列，setSizes 却是**同步**重排 —— 反过来的话分割器先按旧列宽摆一遍，
            #    _rx_apply_layout_now 再按新列宽摆第二遍，同一帧落地两次。
            rail_w = int(round(self._rx_rail_normal
                               + (self._rx_rail_expanded - self._rx_rail_normal) * p))
            self._rx_rail.setFixedWidth(max(1, rail_w))
            # 分割器：发送 pane = 普通高 × (1-p)，接收区吃掉其余。
            # ⚠️ 真正把尺寸交给分割器要放到**这一帧的最后**（见收尾那段）：
            #    QSplitter 对子控件的尺寸约束变化会**当场**重排（它自己有子控件事件
            #    过滤器，绕过 QLayout 的 enable 开关），放在中间就会先生成一个
            #    "宽度还是旧的、高度已经新了"的中间态，随后宽度落地再排第二次。
            tx = max(0, min(self._rx_tx_normal,
                            int(round(self._rx_tx_normal * q))))
            rx = max(160, self._rx_total - tx)
            # ⛔ **不再调 setSizes**：``QSplitter.setSizes`` 是**同步**重排，但排的是
            #    "分割器**当前**矩形里的两个子控件"—— 而分割器自己的 y/高 要等外层
            #    布局跑完才更新（上面 brand 这一帧刚变高）。于是同一帧里必然落地两次：
            #    先按旧外框摆一遍，外层布局跑完再摆第二遍。逐帧实测两次相隔 0.7~2.2ms，
            #    第二次位移最大 108px（配置卡 y 56→104、高 894→846），在 165Hz 逐帧
            #    取样里就是"100px / 6.1ms"的跳变。
            #    改成**把上下限钉死**：tx 卡同时钉 min/max，分割器在任何一次布局里都
            #    只能给它这个高度 —— 于是整帧只剩**一次**落地（下面那次 apply）。
            if self._tx_card in self._rx_hidden:
                pass
            elif tx <= 3:
                self._tx_card.hide()
            else:
                self._tx_card.show()
            # 收尾，顺序不能换：
            #   ① 放开布局、当场落地一次 —— 宽度（配置卡/功能列）在这一步生效；
            #   ② 再把这一帧的发送区高度交给分割器 —— 这是**最后一笔**几何修改，
            #      之后不再有任何布局动作，所以整帧只有一次"最终落地"。
            self._rx_layouts_enabled(True)
            self._rx_apply_layout_now()
            # ⚠️ 发送卡的**高度上限必须跟着 tx 同帧抬**：分割器给了 pane 多少高，
            #    卡就得能长到多少。缺这一句的话，`_rx_prepare` 钉的 maxHeight=0 会
            #    一直钉到落定 —— 整个退出过程 pane 是空的（底部一条 168px / 折叠态
            #    54px 的空带），落定那一帧 `setMaximumHeight(max(current, target_h))`
            #    才一次性放开，发送卡整块 pop 进来、接收区同时缩一截（用户报的
            #    "取消最大化还有跳变"）。逐帧实测：补之前 tx.h 是 `0 0 0 … 0 168`
            #    （有效步 1、单步 +168px = 100% 行程）；补之后是逐帧单调的坡。
            # ⚠️ 位置：**紧挨 setSizes 之前**，不能挪到 `_rx_apply_layout_now()` 之前。
            #    `_tx_card` 是分割器的**直接子控件**（:888 addWidget），而 QSplitter
            #    靠自己的子控件事件过滤器监听尺寸约束变化、会**当场**重排（绕过
            #    QLayout 的 enable 开关，见本函数里给功能列设宽度那段注释）——
            #    放到前面只会多一次"旧尺寸 + 新上限"的中间重排。
            self._tx_card.setMaximumHeight(max(0, tx))
            self._splitter.setSizes([rx, tx])
            # 串口开关位置：rail(1) ↔ home(0)
            self._rx_place_switch(p)
        except RuntimeError:                     # 窗口收尾，控件已析构
            if self._rx_tween is not None:
                self._rx_tween.stop()
        finally:
            self._rx_layouts_enabled(True)       # 出错也要放开，别让界面永久冻死

    def _rx_layouts_enabled(self, enabled: bool) -> None:
        """整段转场用的布局**批处理开关**（幂等）。

        ``QLayout.setEnabled(False)`` 期间该布局不响应任何 LayoutRequest —— 这正是
        我们要的"这一帧先别排，等我把输入改完"。逐帧实测：不批处理时一帧里
        rx_card 会被排 2~3 遍（间隔 1.6~4.3ms），其中只有最后一遍用的是正确输入，
        前面那些"半成品"在 165Hz 上就是肉眼级的抽动。
        """
        if not self._rx_layouts:
            self._rx_collect_layouts()
        for lay in self._rx_layouts:
            try:
                lay.setEnabled(enabled)
            except RuntimeError:
                pass

    def _rx_collect_layouts(self) -> None:
        """把转场会碰到的**所有**嵌套布局收集起来（每帧 invalidate 用）。

        ⚠️ 只 invalidate 最外层那几层是不够的：``QLayout.invalidate()`` 只清自己那份
        缓存，**不会**递归到子布局。而 ``setSizes`` 判定上下限时问的是子控件的
        ``minimumSizeHint()`` → 子布局里那份**没被清掉**的旧缓存 → 分割器仍按上一帧
        的上下限分配一趟，``_rx_apply_layout_now`` 再按新的分配一趟，同一帧落地两次
        （实测间隔 1.5~2.2ms、第二次最大位移 93px）。
        每帧现算 ``findChildren`` 太贵，所以在 ``_rx_prepare`` 里收一次、整段复用。
        """
        roots = (self._rx_card, self._tx_card, self._splitter, self.centralWidget())
        found: list[QLayout] = []
        for root in roots:
            if root is None:
                continue
            own = root.layout()
            if own is not None and own not in found:
                found.append(own)
            for lay in root.findChildren(QLayout):
                if lay not in found:
                    found.append(lay)
        self._rx_layouts = found

    def _rx_apply_layout_now(self) -> None:
        """把这一帧设下的所有 ``fixed*`` / ``setSizes`` **当场**落成布局。

        ⚠️ 只调 ``centralWidget().layout().activate()`` **不够**：那一层布局认为
        自己是"已激活"的，直接早退，卡片的位置要等下一次事件循环才更新 —— 逐帧
        实测卡片左沿比宽度慢整整一帧（宽度已经 79px 时左沿还停在上帧 1581 的
        位置，于是卡片从窗口右边缘"戳"出去 79px 被裁掉，看上去像卡片在抖）。
        这里从卡片自己开始**逐级** invalidate + activate 一直到中央控件：每层都
        真的重排一次，几何与 t 严格同帧。
        """

        node = self._config_card.parentWidget()
        while node is not None:
            lay = node.layout()
            if lay is not None:
                lay.invalidate()
                lay.activate()
            if node is self.centralWidget():
                break
            node = node.parentWidget()

    def _rx_place_switch(self, p: float) -> None:
        """把开关摆到 p 对应的位置（p=1 在功能列那侧，p=0 落在卡片那一格）。

        ⚠️ 两端都尽量用**固定目标**：起点/落点逐帧现算的话，开关会去追一个正在
        移动的点 —— 落点跟着卡片长宽漂（305px）、起点跟着功能列宽度飘，人眼看
        就是"飞到一半被拽一下"。冻结的端点见 `_rx_remember_home_slot` 与
        `_rx_prepare`（功能列那一格只在收起方向冻结）。
        """
        row = self._open_row
        if row.parentWidget() is not self:
            return
        home = self._rx_home_target()
        rail = self._rx_switch_rail if self._rx_switch_rail is not None \
            else self._rx_rail_rect()
        x = int(round(home.x() + (rail.x() - home.x()) * p))
        y = int(round(home.y() + (rail.y() - home.y()) * p))
        w = int(round(home.width() + (rail.width() - home.width()) * p))
        row.setGeometry(x, y, max(1, w), self._rx_row_h)

    def _rx_home_rect(self) -> QRect:
        home = self._open_row_home
        host = home.parentWidget()
        box = home.geometry()
        origin = host.mapTo(self, QPoint(0, 0)) if host is not None else QPoint(0, 0)
        return QRect(origin.x() + box.x(), origin.y() + box.y(),
                     max(1, box.width()), self._rx_row_h)

    def _rx_rail_rect(self) -> QRect:
        rail = self._rx_rail
        lay = rail.layout()
        m = lay.contentsMargins() if lay is not None else None
        pos = rail.mapTo(self, QPoint(0, 0))
        left = m.left() if m is not None else 0
        top = m.top() if m is not None else 0
        return QRect(pos.x() + left, pos.y() + top, self._rx_row_w, self._rx_row_h)

    def _on_rx_done(self) -> None:
        try:
            if self._rx_target >= 0.5:
                self._rx_finish_expanded()
            else:
                self._rx_finish_normal()
        except RuntimeError:
            pass

    def _rx_finish_expanded(self) -> None:
        """落定到最大化态：藏掉让位的块、发送区钉 0、开关塞进功能列顶。"""
        self._rx_t = 1.0
        self._expanded = True
        for w in (self.brand_header, self._config_card):
            if w not in self._rx_hidden:
                w.hide()
        self._rx_opts_row.hide()
        self._set_tx_vpolicy(QSizePolicy.Policy.Preferred)
        self._tx_card.setMinimumHeight(0)
        self._tx_card.setMaximumHeight(0)
        if self._tx_card not in self._rx_hidden:
            self._tx_card.hide()
        self._splitter.setSizes([max(160, self._rx_total), 0])
        rail_lay = self._rx_rail.layout()
        if rail_lay is not None:
            self._rx_rail.setMinimumWidth(0)
            self._rx_rail.setMaximumWidth(WIDGETSIZE_MAX)
            rail_lay.insertWidget(0, self._open_row)
            self._open_row.show()
        # 此刻是真正的最大化版式、尺寸已还原、开关也已插进功能列顶 —— 量一次
        # rail 落点缓存下来，供**下一次展开**用（展开方向 prepare 时还在普通
        # 版式，量不到最大化那一格，只能借用上次落定时实测的值）。
        self._rx_apply_layout_now()
        self._rx_rail_rect_expanded = self._rx_rail_rect()
        self._rx_switch_active = False

    def _rx_finish_normal(self) -> None:
        """落定到普通态：三块还原、发送区按记录还原、开关回配置卡。"""
        self._rx_t = 0.0
        self._expanded = False
        if self.brand_header not in self._rx_hidden:
            self.brand_header.setMinimumHeight(0)
            self.brand_header.setMaximumHeight(WIDGETSIZE_MAX)
            self.brand_header.show()
        if self._config_card not in self._rx_hidden:
            self._config_card.setMinimumWidth(0)
            self._config_card.setMaximumWidth(WIDGETSIZE_MAX)
            # ⚠️ 落定这一帧的宽度**必须正好等于**时间线最后一帧算出的那个值，
            #    否则收尾会跳一下（最容易漏的一处）。q=1 时 _rx_card_width 返回
            #    的正是满宽（min(1, 1/0.85)=1），所以这里用**同一个函数**取值，
            #    而不是另写一个常量。逐帧探针会核对：最后一帧宽 == 落定后宽。
            self._config_card.setFixedWidth(self._rx_card_width(1.0))
            self._config_card.show()
        self._rx_opts_row.setMinimumHeight(0)
        self._rx_opts_row.setMaximumHeight(WIDGETSIZE_MAX)
        if not self._paused:
            self._rx_opts_row.show()
        target_h = self._rx_tx_normal
        content = not self._pre_send_collapsed
        if self._tx_card not in self._rx_hidden:
            self._finish_reveal_send(target_h, content)
        self._splitter.setSizes([max(160, self._rx_total - target_h), target_h])
        # 先把"预留的那一格"撤掉，再把真开关插回同一格：两件都发生在**同一帧**，
        # 且占位与真控件同高 → 卡内版式零位移（见 _rx_place_home_gap）。
        self._rx_clear_home_gap()
        self._open_row_home.insertWidget(0, self._open_row)
        self._open_row.show()
        # 落定后重记一次"那一格"（窗口尺寸/DPI 变过之后仍然准）
        self._rx_apply_layout_now()
        self._rx_remember_home_slot(True)
        self._rx_switch_rail = None
        self._rx_rail.setMinimumWidth(0)
        self._rx_rail.setMaximumWidth(WIDGETSIZE_MAX)
        self._rx_switch_active = False

    def _set_rail_compact(self, compact: bool) -> None:
        """功能键列的尺寸策略：两种状态**完全一样**（2026-10-17 起）。

        曾经这里在最大化态把列 ``AlignTop`` 顶到卡片顶部（外加 ``Maximum`` 夹成
        内容高），本意是让串口开关落在列顶。但那会把列里 7 颗功能键**一起**顶到
        屏幕顶部去 —— 用户明确要求"别的功能键位置不动"。最终版式（用户 01:29
        截图"原来长这样"）：整列填满数据区、首尾两根 ``addStretch(1)`` 把图标
        垂直居中、**贴着数据**（列宽只吃图标内容）；串口开关单独进数据区 left
        VBox 最上面一格（见 ``_move_open_row_to_rx``），与图标列彻底分离。

        ⛔ 不要再让开关进 rail 布局/当浮层/撑列宽 —— 图标与数据之间会凭空多出
        一截空白，用户立刻能看出来（2026-10-17 来回改了四轮的教训）。
        ⛔ 不要再用固定下标去动 stretch：首尾两根 ``addStretch(1)`` 永远保持 1/1。

        保留这个方法名与签名：调用点仍在（``_expand_receive`` / ``_collapse_receive``），
        以后若真要区分两态，只需要改这一处。
        """
        del compact                      # 两态无差别
        rail = self._rx_rail.layout()
        if rail is None:
            return
        policy = self._rx_rail.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Maximum)
        policy.setVerticalPolicy(QSizePolicy.Policy.Preferred)
        self._rx_rail.setSizePolicy(policy)
        self._rx_rail.updateGeometry()

    def _restore_window_state(self) -> None:
        """退出展开时把窗口状态还原到"点全屏按钮之前"，并**等它落地**。"""
        if self._pre_expand_fullscreen:
            self.showFullScreen()
            self.raise_()
            self.activateWindow()
            self._enforce_native_fullscreen()
            self._settle_window_state(True)
            return
        # 回到普通窗口 —— "想要全屏"跟着降下来，别让延迟核对又把它拽回去
        self._want_fullscreen = False
        self._expect_normal = True
        self.showNormal()
        if self._pre_expand_geometry is not None:
            self.setGeometry(self._pre_expand_geometry)
        self._settle_window_state(False)
        self._expect_normal = False
        self._set_corner_preference(int(self.winId()), round_=True)
        if self._pre_expand_geometry is not None:
            # ⚠️ 这一刻配置卡还在做"从右长回来"的动画，布局的最小宽度偏高，
            #    窗口会被**夹到比目标更宽**（实测 839 被顶成 1134）。动画跑完
            #    再重申一次几何，保证和展开前完全一致。
            QTimer.singleShot(320, self._reassert_pre_expand_geometry)

    def _collapse_receive(self) -> None:
        """退出接收区展开：卡片们回来，窗口保持在全屏（软件是全屏应用）。"""
        self._expanded = False
        # ⛔ 冻结在途转场补间（原因同 _expand_receive）：下面 _restore_window_state
        #    会阻塞等窗口落地，不冻结的话展开会先跑完再反向。_rx_transition 从冻结值起跑。
        if self._rx_tween is not None and self._rx_tween.running:
            self._rx_tween.stop()
        self._rx_highlighter.defer(420)   # 同上：退场动画期间也合并高亮重刷
        self._set_rx_coalesce(RX_COALESCE_MS)
        QTimer.singleShot(300, self._end_rx_coalesce)
        self._set_rail_compact(False)   # 功能列还原成整列居中（见 _set_rail_compact）
        self.btn_expand.set_glyph(GLYPH_EXPAND)
        self.btn_expand.setToolTip("接收区全屏显示（Esc 退出）")
        # ⛔ 窗口状态**先落地、再起动画**：原先这步排在函数末尾，动画跑到 80ms
        #    时窗口才变，整屏当场跳 12px（逐帧探针抓到过）。见 _restore_window_state。
        self._restore_window_state()
        # 所有几何（brand/config/opts/分割器/功能列/开关）由**一条时间线**反向驱动
        # （和展开严格镜像），见 _rx_transition。
        self._rx_transition(False)

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

    def _reassert_pre_expand_geometry(self) -> None:
        """收起动画结束后再确认一次窗口几何（见 _collapse_receive 的说明）。"""
        if self._expanded or self.isFullScreen() or self._pre_expand_geometry is None:
            return
        if self.geometry() != self._pre_expand_geometry:
            self.setGeometry(self._pre_expand_geometry)



    def _animate_splitter(self, target: list[int], duration: int = 240, on_done=None,
                          start: list[int] | None = None) -> None:
        """把分割器尺寸**逐帧**推到目标（发送区收展、接收区最大化都用它）。

        直接 ``setSizes`` 是一帧跳变；这里插值成一段连续动画。
        ⚠️ 不要改用 ``Reveal`` 去夹卡片的 maximumHeight —— 那是另一套机制，会和
        分割器的尺寸分配**互相打架**：一边按动画压高度、另一边按比例重分配，
        观感就是"很奇怪"（用户点名的"发送区展开时接收区最大化动画很奇怪"）。
        """
        # ⛔ 起点默认取分割器的 sizes()，但它**不等于卡片的真实高度**：卡片被
        #    setMaximumHeight 钉住时（折叠态 54），分割器仍然报展开尺寸 168。
        #    调用方知道真实高度就把 start 传进来，否则"展开"会因为
        #    start == target 直接跳到终态（实测单帧 54→168，一点动画都没有）。
        begin = list(start) if start is not None else self._splitter.sizes()
        if len(begin) != len(target):
            self._splitter.setSizes(target)
            if on_done:
                on_done()
            return
        self._split_from = list(begin)
        if begin == list(target):
            # 真的没有位移：直接落终态（也要给 on_done 一次，钉高度靠它）
            self._splitter.setSizes(target)
            if on_done:
                on_done()
            return
        self._split_to = list(target)
        # ⛔ Tween 只建一次、反复复用，on_finished **不能**在构造时钉死 —— 否则
        #   第一段动画的回调（开局折叠那次：钉回 54）会跟着这个 Tween 过一辈子，
        #   之后每段动画结束触发的都是它：展开发送区放完动画会被当场夹回 54px
        #   （selftest「拖大发送区后输入框没被拉伸：54 -> 54」的根因）。
        #   回调改成挂窗口上的槽位，每段动画起跑前换一次。
        self._split_on_done = on_done
        if self._split_tween is None:
            self._split_tween = Tween(240, in_out_quint, self._on_split_anim,
                                      self._on_split_finished, parent=self)
        self._split_tween.start(0.0, 1.0, duration)

    def _on_split_finished(self) -> None:
        callback, self._split_on_done = self._split_on_done, None
        if callback:
            callback()

    def _on_split_anim(self, t: float) -> None:
        sizes = [int(round(a + (b - a) * t))
                 for a, b in zip(self._split_from, self._split_to)]
        try:
            self._splitter.setSizes(sizes)
            # 接收区最大化：发送区压到 1/8 以下**当场隐藏**，和配置卡同一时刻消失
            # （停在半高再突然 hide 就是用户看到的跳变）。
            # ⚠️ 必须按**实际尺寸**判，不能按 t：第一帧 t=0 就 ≤0.125，那时隐藏会让
            #    分割器当场把它的空间收走 —— 观感就是"嗖一下没了"的跳变（实测踩过）。
            # 同 Reveal 的阈值：1/8 太早（54px 时是 6px、168px 时是 21px），
            # 收到 3px 以内再藏，收尾那一帧就只剩几像素的位移。
            if (self._expanded and len(sizes) > 1 and self._split_to[1] == 0
                    and self._split_from[1] > 0
                    and sizes[1] <= 3):
                self._tx_card.setVisible(False)
        except RuntimeError:            # 窗口收尾时分割器可能已析构
            if self._split_tween is not None:
                self._split_tween.stop()

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
            self.btn_minimize.set_glyph(GLYPH_EXPAND_CHEVRON)
            self.btn_minimize.setToolTip("展开发送区")
            # ⛔ 顺序是量出来的（2026-10-17 逐帧）：**先钉住当前高度，再放开下限**。
            #    反过来写（min=0 再 max=WIDGETSIZE_MAX）的话，放开的那一瞬布局会按
            #    sizeHint 把卡片顶回展开态 —— 实测单帧 70→168px 的跳变，然后才被
            #    分割器动画压回去（用户看到的"收起点一下"）。
            current = max(TX_COLLAPSED_H, self._tx_card.height())
            self._tx_card.setMaximumHeight(current)
            self._tx_card.setMinimumHeight(0)
            # ⛔ 光放开 min/max 还不够：卡片里还有内容，它的 minimumSizeHint 会
            #    经 qSmartMinSize 变成分割器的下限 —— 动画压到 168 就压不动了，
            #    最后 on_done 一钉才"啪"地跳成 54。动画期间把竖直策略设成
            #    Ignored（布局不再看它的 sizeHint/最小值），收完再还原。
            self._set_tx_vpolicy(QSizePolicy.Policy.Ignored)
            sizes = self._splitter.sizes()
            total = sum(sizes) or 900
            begin = ([max(160, total - current), current]
                     if len(sizes) == 2 else None)
            self._animate_splitter([max(160, total - TX_COLLAPSED_H), TX_COLLAPSED_H],
                                   duration=TX_ANIM_MS,
                                   on_done=self._finish_send_collapsed,
                                   start=begin)
            # 收起后"展开发送区"那颗键必须**始终可见可点**（用户点名）—— 卡片收
            # 到只剩标题栏，那颗键就在标题行上，这里显式钉一次，任何一条路径把
            # 它藏过都能自动恢复。
            self.btn_minimize.setVisible(True)
        else:
            self._tx_collapsed = False
            self.btn_minimize.set_glyph(GLYPH_COLLAPSE)
            self.btn_minimize.setToolTip("最小化发送区")
            target = self._tx_saved_sizes or self._tx_expanded_sizes()
            # 上限钉在**目标高度**（不是当前 54）：既让分割器能长上去，又不放开到
            # sizeHint —— 放开就是"先弹到展开尺寸再缩回来"的那一跳。
            current = self._tx_card.height()
            target_h = target[-1] if target else current
            self._tx_card.setMaximumHeight(max(current, target_h))
            self._tx_card.setMinimumHeight(0)  # 放开下限，动画结束再钉回
            # 同收起：内容一显示出来，卡片的 minimumSizeHint 就把分割器顶到展开
            # 高度，动画根本没法从 54 起步（实测 cardH 一直是 168）—— 动画期间
            # 同样把竖直策略设成 Ignored，收尾还原。
            self._set_tx_vpolicy(QSizePolicy.Policy.Ignored)
            self._tx_content.show()
            sizes = self._splitter.sizes()
            total = sum(sizes) or 900
            begin = ([max(160, total - current), current]
                     if len(sizes) == 2 else None)
            self._animate_splitter(target, duration=TX_ANIM_MS,
                                   on_done=self._finish_send_expanded,
                                   start=begin)
            self.btn_minimize.setVisible(True)
        # 折叠/展开改变接收区视口高度 —— 布局的几何重排是**异步**激活的，
        # 不在这里强制落地的话，紧跟着的暂停定位（_snap_to_round_start）拿到
        # 的视口几何是旧的，时间戳会被顶到视口顶（真机实测：应在 7 行下、
        # 实际 0px）。activate() 同步重排，不用进事件循环。
        if self.centralWidget() is not None and self.centralWidget().layout():
            self.centralWidget().layout().activate()

    def _finish_reveal_send(self, target_h: int, content: bool) -> None:
        """退出最大化时把发送卡从 0 长回来的收尾：钉回上下限、还原策略。"""
        # 兜底：万一这条路上有别的分支把卡片藏过（自测里偶发过一次），
        # 收尾必须把它**拉回可见**并把分割器尺寸补齐，状态不能留在半路。
        if self._tx_card.isHidden():
            self._tx_card.show()
            sizes = self._splitter.sizes()
            total = sum(sizes) or 900
            self._splitter.setSizes([max(160, total - target_h), target_h])
        self._set_tx_vpolicy(QSizePolicy.Policy.Preferred)
        if content:
            self._tx_card.setMinimumHeight(self._tx_min_height)
            self._tx_card.setMaximumHeight(WIDGETSIZE_MAX)
        else:
            self._tx_card.setMinimumHeight(TX_COLLAPSED_H)
            self._tx_card.setMaximumHeight(TX_COLLAPSED_H)
            self._tx_content.hide()
        self._repin_rx_bottom()

    def _set_tx_vpolicy(self, policy) -> None:
        """临时改发送卡的竖直尺寸策略（动画期间用 Ignored 让分割器能压下去）。"""
        sp = self._tx_card.sizePolicy()
        if sp.verticalPolicy() == policy:
            return
        sp.setVerticalPolicy(policy)
        self._tx_card.setSizePolicy(sp)
        self._tx_card.updateGeometry()

    def _repin_rx_bottom(self) -> None:
        """视口高度变化落定后重新压到底（追底的一次性动作会被后续高度变化作废）。

        追底 = 把滚动条推到 maximum；如果**之后**视口又变了（发送区收/展的动画
        落定），maximum 跟着变，视图就停在半路 —— 实测最新 4 行被挤出可视区
        （自测「收包合并与暂停继续」就挂在这）。暂停中/搜索定位中不动，免得
        把用户自己滚到的地方拽走。

        推的时候走 ``_follow_rx_bottom``（180ms 补间）：这里是"收尾补一下"，
        一步 setValue 就是最后一帧的硬跳（恢复暂停时实测差 8 行 ≈150px）。
        """
        if self._paused or self._search_pinned:
            return
        self._follow_rx_bottom()

    def _finish_send_collapsed(self) -> None:
        """收起动画落定：钉成标题栏高度，**这时**才隐藏卡片内容。

        ⛔ 内容不能在动画开始时 hide：卡片那一刻还是满高的，文字整块消失 =
        一次"内容突然没了"的跳变。留着它，让高度动画把内容一路裁下去，
        落到 54px 再藏 —— 全程是"被卡片收走"，不是一个消失的瞬间。
        """
        self._set_tx_vpolicy(QSizePolicy.Policy.Preferred)
        self._tx_card.setMinimumHeight(TX_COLLAPSED_H)
        self._tx_card.setMaximumHeight(TX_COLLAPSED_H)
        self._tx_content.hide()

    def _finish_send_expanded(self) -> None:
        """展开动画落定：策略/上下限都还原，之后高度归分割器与用户拖拽。"""
        self._set_tx_vpolicy(QSizePolicy.Policy.Preferred)
        self._tx_card.setMinimumHeight(self._tx_min_height)
        self._tx_card.setMaximumHeight(WIDGETSIZE_MAX)
        self._repin_rx_bottom()
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
        # ⛔ 可编辑下拉在 setCurrentIndex/setEditText 之后会把光标放到**末尾**，
        #    条目文字比框宽时（蓝牙设备那一长串"MAC — 名字 — 信号 — 负载"）
        #    框里显示的就是**尾巴**（"…46dBm（有广播负载）"），设备名整个看不见。
        #    把光标拨回开头，用户看到的是 MAC 和设备名。
        line = self.cmb_port.lineEdit()
        if line is not None:
            line.setCursorPosition(0)

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
        if self._link_is_ble():
            # 蓝牙广播模式：这一行轮的是**串口**，会把设备列表冲掉。广播模式下
            # 设备列表由「刷新」按钮按需扫描，不走这个定时器。
            return
        self._reap_retired()      # 顺手给取消建链的 worker 收尸（见 _reap_retired）
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
        """打开串口。**建链是异步的**：这里只发起请求，结果由 worker 的
        ``opened`` / ``open_failed`` 信号回来（见 _on_port_opened / _on_open_failed）。

        为什么异步：蓝牙串口在 Windows 上要先建 RFCOMM 链路，同步 open 会把界面
        卡死好几秒（用户 2026-10-14 点名优化）。发起时先把开关拨到"开"、状态栏
        写"正在连接 …"，用户随时可以再按一下取消（走 _close_port）。
        """
        port = port or self._selected_port()
        if not port:
            self._abort_open("请先选择或输入串口名" if not self._link_is_ble()
                             else "请先点右边的刷新按钮扫描，再选一个蓝牙设备")
            return
        try:
            baudrate = int(self.cmb_baud.currentText())
        except ValueError:
            baudrate = 115200            # 蓝牙广播模式用不到波特率，别为它拦人
            if not self._link_is_ble():
                self._abort_open("波特率必须是整数")
                return

        if self._link_is_ble():
            # 广播模式：目标是设备地址，只扫描不连接（见 ble_io 的模块说明）。
            # source="auto"：优先取 manufacturer data，没有就退到 service data ——
            # 设备侧把数据放哪个表里都行，用户不用去配。
            worker = _ble_worker_cls()(port, source="auto")
        else:
            worker = SerialWorker(
                port,
                baudrate=baudrate,
                bytesize=BYTESIZE_CHOICES[self.cmb_bytesize.currentText()],
                parity=PARITY_CHOICES[self.cmb_parity.currentText()],
                stopbits=STOPBIT_CHOICES[self.cmb_stopbits.currentText()],
            )
        worker.received.connect(self._on_received)
        worker.failed.connect(self._on_failed)
        worker.opened.connect(self._on_port_opened)
        worker.open_failed.connect(self._on_open_failed)
        # ⚠️ 现在就认它：建链期间用户再拨一次开关要走 _close_port 把它取消掉，
        #    "正在连接"也必须有东西可指（不然第二下点击会以为没事发生）。
        self._worker = worker
        self._port_connected = False
        self._decoder.reset()
        # 换了个口就是换了套数据，"轮首标记见过没有"要重新判（见 _stamp_needed）
        self._stamp_anchor_seen = False
        self._stamp_last_monotonic = 0.0
        self.btn_open.setChecked(True)
        # 建链期间锁住配置项（和已打开时一致）：不然用户能在"半连着"的状态下
        # 改串口/波特率，界面显示的和实际连的对不上。
        for widget in self._config_widgets():
            widget.setEnabled(False)
        self._set_state(f"正在连接 {port} …", busy=True)
        self.pulse.pulse(0.35)
        worker.open()

    def _abort_open(self, message: str) -> None:
        """打开请求还没发出去就被挡下来（没选口 / 波特率不是数字）。

        开关多半已经被拨到"开"，要弹回去，不然开关是通的实际没连上。这次
        setChecked 触发的 toggled 里 worker 是 None，不会再打开。
        """
        self.btn_open.setChecked(False)
        self._warn(message)

    def _on_port_opened(self) -> None:
        """建链完成（可能是发起后的几秒，蓝牙口尤其慢）。"""
        worker = self.sender()
        if worker is not self._worker:
            # 建链期间被取消 / 又换了口：这次结果作废（worker 会自己收尾退出）
            return
        self._port_connected = True
        if isinstance(worker, _ble_worker_cls()):
            # 广播模式没有波特率/校验位这回事，状态栏按"正在接收广播"来写
            self._ble_target = worker.port
            self._settings.setValue("ble_target", worker.port)
            self._set_state(f"正在接收蓝牙广播 · {worker.port}")
            app_log.info(f"蓝牙广播接收中：{worker.port}")
            self.pulse.pulse(1.0)
            return
        baudrate = self.cmb_baud.currentText()
        self._set_state(f"已打开 {worker.port} @ {baudrate} · "
                               f"{self.cmb_bytesize.currentText()}"
                               f"{PARITY_LETTERS[self.cmb_parity.currentText()]}"
                               f"{self.cmb_stopbits.currentText()}")
        app_log.info(f"串口已打开 {worker.port} @ {baudrate} "
                     f"{self.cmb_bytesize.currentText()}"
                     f"{PARITY_LETTERS[self.cmb_parity.currentText()]}"
                     f"{self.cmb_stopbits.currentText()}")
        self.pulse.pulse(1.0)
        # 这里不弹临时提示：图标、状态标签和状态灯已经给了反馈，
        # 再弹一条内容相近的提示只会在状态栏里重复一遍。

    def _on_open_failed(self, message: str) -> None:
        """建链失败（端口不存在 / 被占用 / 蓝牙对端不在）。"""
        worker = self.sender()
        if worker is not self._worker:
            return
        port = worker.port
        self._worker = None
        self._port_connected = False
        worker.deleteLater()
        app_log.error(f"串口打开失败 {port}: {message}")
        self.btn_open.setChecked(False)
        for widget in self._config_widgets():
            widget.setEnabled(True)
        self._set_state("未打开")
        self._warn(f"打不开 {port}：\n{message}\n\n"
                   "端口可能被占用，或设备已拔出、不在范围内。")

    def _close_port(self, message: str = "") -> None:
        # 先把关口里剩下的那一批吐出来，别丢掉设备最后发的那几个字节
        # （force：没有下一批了，没等到换行的半行也要显示出来）
        self._flush_rx(force=True)
        worker, self._worker = self._worker, None
        self._port_connected = False
        self._send_timer.stop()
        self.chk_timer_send.setChecked(False)
        if worker is not None:
            for signal in (worker.received, worker.failed,
                           worker.opened, worker.open_failed):
                signal.disconnect()
            worker.stop()
            if worker.isRunning():
                # ⚠️ **建链中**被取消：线程还卡在 open() 里（蓝牙口可能等十几秒），
                #    stop() 的短等待没等到它。这里绝不能撒手 —— Python 一旦回收掉
                #    这个包装对象，QThread 的 C++ 对象会在**线程还在跑**的时候被
                #    析构，Qt 直接 fail-fast（实测退出码 0xC0000409，什么都不打印，
                #    极难查）。存进 _retiring 保住强引用，等它自己发现 _running
                #    已置假、关完句柄退出之后，再由 _reap_retired 收尸。
                self._retiring.append(worker)
                worker.finished.connect(self._reap_retired)
            else:
                worker.deleteLater()
        # 开关弹回"断"。若它本来就被拨到断（多数路径如此），toggled 不会发；
        # 自动断开（设备拔掉）时它还在通，这次 setChecked 触发的 toggled 里
        # worker 已经是 None，不会再触发关闭。
        self.btn_open.setChecked(False)
        for widget in self._config_widgets():
            widget.setEnabled(True)
        self._set_state("未打开")
        if message:
            self._toast(message, 4000)
        app_log.info("串口已关闭")
        # 关掉之后重扫一次：设备可能已经拔了，下拉框不该继续留着它
        # （蓝牙广播模式下不扫串口 —— 否则会把设备列表冲掉）
        if not self._link_is_ble():
            self._fill_port_combo(list_serial_ports(), self._selected_port())

    def _reap_retired(self) -> None:
        """给"建链中被取消"的 worker 收尸：**确认线程真的结束了**再销毁。

        为什么要专门一道收尸：留强引用（见 _close_port）只保证对象不被 Python
        提前回收；真正销毁得等线程跑完。finished 是从线程内部发出来的，那会儿
        线程还差最后几步，所以这里以 isRunning() 为准，没退干净就留着，
        下次 finished 或 _poll_ports 再来收（多留几毫秒引用毫无代价）。
        """
        for worker in list(self._retiring):
            if worker.isRunning():
                continue
            self._retiring.remove(worker)
            worker.deleteLater()

    def _config_widgets(self) -> list[QWidget]:
        return [self.cmb_link, self.cmb_port, self.cmb_baud, self.cmb_bytesize,
                self.cmb_parity, self.cmb_stopbits, self.btn_rescan]

    # ------------------------------------------------------------ 连接方式 / 蓝牙

    def _link_is_ble(self) -> bool:
        return self.cmb_link.currentText() == LINK_BLE

    def _on_link_mode_changed(self, _text: str = "") -> None:
        """连接方式切换：换标签与提示、显隐串口专有参数、刷一遍目标列表。

        两种模式**共用「选目标」那一行**（串口列表 / 蓝牙设备列表）—— "选一个
        要连的东西"在两种模式下是一回事，没必要摆两套控件。
        """
        ble = self._link_is_ble()
        if not ble and self._ble_scanner is not None:
            self._finish_ble_scan(fill=False)      # 离开广播模式就别接着扫了
        self._port_label.setText("蓝牙设备" if ble else "串口选择")
        self.btn_rescan.setToolTip("重新扫描蓝牙设备" if ble else "重新扫描串口")
        self.cmb_port.setToolTip(
            "只被动扫描广播，不需要配对；设备要把数据放进广播包才收得到"
            if ble else "")
        self._serial_params.setVisible(not ble)
        # 广播是**单向**的（只能听，没有"发给设备"这条路，见 _on_send 的说明），
        # 所以发送区在广播模式下直接禁用 —— 比"点了才弹报错"友好。
        self.txt_tx.setEnabled(not ble)
        self.txt_tx.setPlaceholderText(
            "蓝牙广播只能接收，不能发送" if ble
            else "要发送的内容，回车发送；HEX 模式下写 AA 01 FF")
        self.btn_send.setEnabled(not ble)
        self.btn_send.setToolTip(
            "蓝牙广播只能接收，发不出去" if ble
            else "发送（Enter，Ctrl+Enter 同效）")
        if ble:
            self.cmb_port.clear()
            self._set_state("蓝牙广播：未扫描")
            self._scan_ble_devices()
        else:
            self._set_state("未打开")
            self._refresh_ports(announce=False)
        self._settings.setValue("link_mode", "ble" if ble else "serial")

    def _refresh_targets(self) -> None:
        """刷新按钮：按当前连接方式刷对应的目标列表。"""
        if self._link_is_ble():
            self._scan_ble_devices()
        else:
            self._refresh_ports()

    def _scan_ble_devices(self) -> None:
        """扫一遍周围的 BLE 广播，把设备列进下拉框（只列表，不接收）。"""
        if self._ble_scanner is not None:
            return                                  # 已经在扫，别叠第二个
        self._ble_seen.clear()
        scanner = _ble_worker_cls()("")             # 空目标 = 只列设备
        scanner.found.connect(self._on_ble_found)
        scanner.open_failed.connect(self._on_ble_scan_failed)
        # 扫描要等 3.5 秒，也是"在等一件事" → 让状态灯转弧
        scanner.opened.connect(lambda: self._set_state("蓝牙广播：扫描中 …",
                                                      busy=True))
        self._ble_scanner = scanner
        scanner.open()
        QTimer.singleShot(int(BLE_SCAN_SECONDS * 1000), self._finish_ble_scan)

    def _on_ble_found(self, info: dict) -> None:
        self._ble_seen[info["address"]] = info

    def _on_ble_scan_failed(self, message: str) -> None:
        self._finish_ble_scan(fill=False)
        self._set_state("蓝牙广播：不可用")
        self._warn(f"蓝牙扫描起不来：\n{message}")

    def _finish_ble_scan(self, fill: bool = True) -> None:
        scanner, self._ble_scanner = self._ble_scanner, None
        if scanner is not None:
            scanner.found.disconnect()
            scanner.opened.disconnect()
            scanner.open_failed.disconnect()
            scanner.stop()
            if scanner.isRunning():
                scanner.finished.connect(scanner.deleteLater)
            else:
                scanner.deleteLater()
        if fill:
            self._fill_ble_combo()

    def _fill_ble_combo(self) -> None:
        """把扫到的设备填进下拉框（按信号强度排，强的在前）。"""
        rows = sorted(self._ble_seen.values(), key=lambda i: -(i["rssi"] or -999))
        self.cmb_port.blockSignals(True)
        self.cmb_port.clear()
        for info in rows:
            name = info["name"] or "未命名"
            note = "有广播负载" if info.get("payload") else "无负载"
            self.cmb_port.addItem(
                f"{info['address']} — {name} — {info['rssi']}dBm（{note}）",
                info["address"])
        self.cmb_port.blockSignals(False)
        if self._ble_target:
            self._select_port(self._ble_target)     # 尽量保持上次选的那台
        self._set_state(
            f"蓝牙广播：扫到 {len(rows)} 个设备" if rows else "蓝牙广播：没扫到设备")
        self._toast(f"扫到 {len(rows)} 个蓝牙设备" if rows else
                    "没扫到蓝牙设备（确认蓝牙已打开、设备在广播）", 3500)

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

    def _rx_grid_hints(self) -> dict:
        """数据网格的排版参数（设置 → 模组个数 / 每模组电芯数 / 每模组温度采集数）。

        三个值**默认都是 0**：那时"每排个数"跟随设备发来的每行点数，但
        "末排多一、其余各排一致"的分配规则**始终生效**（见 customization 与
        ui_highlight.grid_row_sizes）。每次排版都现取，所以改完设置**下一帧就按
        新布局排**。
        """
        return {
            "rows_hint": int(customization.get("module_count")),
            "volt_cols_hint": int(customization.get("cells_per_module")),
            "temp_cols_hint": int(customization.get("temps_per_module")),
        }

    def _relayout_rx_document(self) -> bool:
        """把**已显示**的接收文本按当前排版配置重排（改完设置立刻看到效果）。

        ⚠️ 只重排**空白**（补零 + 折行），不动数字、不动时间戳、不动行数语义；
        滚动位置、光标/选中区、暂停态全部保留。异常高亮先撤掉 —— 折行数一变，
        旧的行号指针就不再指向原来那一条，留着反而会指错。

        返回 True 表示真的重排过（内容有变化）。
        """
        box = self.txt_rx
        document = box.document()
        text = box.toPlainText()
        if not text or document.isEmpty():
            return False
        bar = box.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 1
        scroll = bar.value()
        cursor = box.textCursor()
        position, anchor = cursor.position(), cursor.anchor()
        try:
            new_text = align_indexes(text, section=SECTION_OTHER,
                                     max_row_chars=self._rx_max_row_chars(),
                                     **self._rx_grid_hints())
        except Exception as exc:            # noqa: BLE001 - 排版失败别把设置流程带走
            app_log.error(f"接收区重排失败: {exc}")
            return False
        if new_text == text:
            return False
        self._clear_alert_highlight()
        box.blockSignals(True)
        box.setPlainText(new_text)
        box.blockSignals(False)
        # 光标/选区按字符偏移还原（内容长度可能变，夹到新长度内）
        limit = len(new_text)
        restored = box.textCursor()
        restored.setPosition(min(anchor, limit))
        restored.setPosition(min(position, limit), QTextCursor.KeepAnchor)
        box.setTextCursor(restored)
        # 滚动位置还原：本来贴着底部就还贴底部（流式数据的常态）
        if at_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(scroll, bar.maximum()))
        # 段状态机按重排后的全文重新走一遍，下一帧的时间戳判据才准
        state = SECTION_OTHER
        for line in new_text.split("\n"):
            state = section_of(line, state)
        self._align_section = state
        self._rx_highlighter.refresh()
        return True

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
                             max_row_chars=self._rx_max_row_chars(),
                             **self._rx_grid_hints())
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
        # ⛔ 历史接收数据在**收到数据这一刻**就记，不等"显示"。
        #    以前 feed 挂在 _flush_rx_display 上：暂停期间数据只进 _paused_frames
        #    不喂历史，用户"看了半天数据，历史文件却一直停在暂停那一刻"
        #    （2026-10-18 用户点名："每次读取到串口数据就要更新"）。
        #    合并期（动画期间 60ms 批）同理：数据到了就该进历史。
        import rx_history
        rx_history.feed(display)
        if self._paused:
            # 暂停 = **冻结显示**：新数据连同到达时刻的时间戳一起进后台队列，
            # 画面一个像素都不动 —— 这就是"停留在当前数据页面"的严格实现。
            # （试过"照常追加 + 滚动锚定"，但 maximumBlockCount 裁剪下块坐标
            # 和滚动条都是惰性且错位的，怎么补都稳不住，索性不追加。）
            self._paused_frames.append((text, display))
            return
        # 历史回放中：实时数据照常记进本地留存（不丢），但不写进这块正在
        # 展示历史数据的只读画面 —— 否则用户翻看的内容会被新帧冲走。
        if self._replay_active:
            return                      # 历史已在上游记过（见上），别重复写
        self._append_display(text, display)

    def _append_display(self, text: str, display: str) -> None:
        """正常显示一帧：追加、追底、记账。暂停恢复时批量刷帧也走这里。

        ⚠️ **合并期**（动画期间，见 ``_set_rx_coalesce``）只往缓冲区里堆，真正的
        ``appendPlainText`` 交给 60ms 的定时器一次做完 —— 250 行/秒的流式数据下，
        每帧追加 + 文档重排 + 大面积重绘会和动画**抢帧**（实测 P50 从 ~118 掉到
        ~65fps，见 devtools/_probe_fps.py 的高压场景对比）。
        ⛔ 不是"冻结重绘"那条老路（那正是用户报的"接收区信息接收部分消失了
        一段时间"的根因）：已有内容全程可见可交互，只是新数据最多晚 60ms 成批
        出现 —— 60ms 低于"一眨眼下"的感知阈值，肉眼无感。
        """
        if self._rx_coalesce_ms > 0:
            self._rx_coalesce_buffer.append((text, display))
            if not self._rx_coalesce_timer.isActive():
                self._rx_coalesce_timer.start(self._rx_coalesce_ms)
            return
        self._flush_rx_display(text, display)

    def _flush_rx_display(self, text: str, display: str) -> None:
        """真正往接收区写一帧（合并期由 ``_flush_rx_coalesced`` 批量调它）。"""
        self.txt_rx.appendPlainText(display)
        # 历史已在 _emit_block 入口记过（暂停/回放期间也记），这里不再喂 ——
        # 再喂一次会把每一帧写两遍。
        # appendPlainText 只在"视图本来就在底部"时才跟着走。暂停过又恢复时
        # 视图停在半路，新数据会静静地堆在下面看不见 —— 所以显式滚到底。
        # 搜索钉住时例外：用户正盯着某条命中看，不能把视图拽走。
        if not self._search_pinned:
            self._follow_rx_bottom()
        # 搜索条开着的话，高亮要跟着重算（追加会让所有区间前移）。
        # 走防抖计时器：数据不停时它被反复推后，等数据歇下来才真扫一遍。
        if self._search_bar.isVisible():
            self._search_timer.start()
        # 异常定位的高亮钉在某个块上：块被裁掉/内容变了就撤掉（别飘到别的行）
        self._prune_alert_highlight()

    def _set_rx_coalesce(self, ms: int) -> None:
        """开/关动画期间的日志追加合并（``ms`` 为合并窗口，0 = 关闭）。

        关的时候**立即把缓冲区刷掉** —— 合并期不允许留下没显示的帧。
        """
        if ms <= 0:
            self._rx_coalesce_ms = 0
            self._flush_rx_coalesced()
            return
        self._rx_coalesce_ms = int(ms)

    def _flush_rx_coalesced(self) -> None:
        """把合并期内攒下的帧一次性写进接收区。

        历史**逐帧**记（不丢），但 ``appendPlainText`` / 追底 / 搜索重算 / 异常
        高亮修剪都只做一次 —— 这几项才是和动画抢帧的大头。
        """
        buffered, self._rx_coalesce_buffer = self._rx_coalesce_buffer, []
        self._rx_coalesce_timer.stop()
        if not buffered:
            return
        self.txt_rx.appendPlainText("\n".join(display for _text, display in buffered))
        if not self._search_pinned:
            self._follow_rx_bottom()
        if self._search_bar.isVisible():
            self._search_timer.start()
        self._prune_alert_highlight()

    def _end_rx_coalesce(self) -> None:
        """合并窗口结束：把攒下的帧立刻刷掉，恢复逐帧追加。"""
        self._set_rx_coalesce(0)

    def _follow_rx_bottom(self) -> None:
        """平滑跟随到底。

        原来是一步 ``setValue(maximum())`` —— 在高刷屏上就是"咔"地跳一格，
        数据一密就抖。这里改走 ``ui_anim`` 的补间（180ms / out_cubic）：每次追加都
        **从当前位置**起一段新的到最新底部的补间，数据不停时是一条连续的滚动。
        系统关掉动效（``animations_enabled()``）时直接落底，不强行动画。
        """
        bar = self.txt_rx.verticalScrollBar()
        target = bar.maximum()
        if bar.value() >= target:
            return
        if not animations_enabled():
            bar.setValue(target)
            return
        if self._rx_scroll_tween is None:
            self._rx_scroll_tween = Tween(180, out_cubic, self._on_rx_scroll,
                                          parent=self)
        # 起点是**当前值**，随数据不断改向也不会"咔"一下（可打断）。
        self._rx_scroll_tween.start(bar.value(), target, 180)

    def _on_rx_scroll(self, value) -> None:
        try:
            self.txt_rx.verticalScrollBar().setValue(int(round(value)))
        except RuntimeError:
            pass

    def _flush_paused_frames(self) -> None:
        """恢复滚动：把暂停期间攒下的帧一次性刷进接收区，然后追底。

        ⚠️ 追底在这里是**一步到位**（不是平滑滚动）：这是"暂停→恢复"的批量
        catch-up，用户要的是立刻看到最新数据。逐帧的平滑跟随由 ``_follow_rx_bottom``
        负责（见那里），批量刷入这一下必须先停掉可能还在跑的滚动补间、再直接落底。
        """
        frames, self._paused_frames = self._paused_frames, []
        for text, display in frames:
            # ⚠️ 直接走 _flush_rx_display（不过合并缓冲）：批量 catch-up 就是要
            # 一次性落地，再攒 60ms 就又把"恢复后立刻看到最新数据"拖慢了。
            self._flush_rx_display(text, display)
        if self._rx_scroll_tween is not None:
            self._rx_scroll_tween.stop()
        if not self._search_pinned:
            # 追底走**平滑补间**（180ms，见 _follow_rx_bottom）：暂停恢复时视口还在
            # 变长（发送区展开 + 工具栏长回来），一步 setValue 只会落在"旧底部"，
            # 等布局落地后再"啪"地补一下 —— 那一下正是恢复动画的最后一跳。
            self._follow_rx_bottom()
            # ⚠️ 追底之后再补一次（340ms > 发送区收展的 300ms 动画）：恢复时如果
            #    同时还在长回发送区，视口会在**这之后**变矮，maximum 变大，刚才
            #    那一下就被作废了 —— 实测最新 4 行被挤出可视区（151/155）。
            QTimer.singleShot(340, self._repin_rx_bottom)

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
        target = max(0, self._visual_line_index(stamp_start, row_height) - keep)
        # 暂停定位这一下以前是 ``setValue`` **硬跳**：实测整跳 10 行（≈190px），
        # 又紧挨着 240ms 的发送区折叠补间，观感就是"平滑收着突然窜一下" ——
        # 用户说的"暂停的动画不够顺滑"另一半就在这儿。改成和"跟随到底"同一条
        # 补间（200ms / out_cubic，可随时打断）：起点是当前值，落点精确到 target。
        # ⚠️ 目标只由**行号**决定、与视口高度无关，所以折叠过程中视口在长也不
        #    影响落点（视口变高只会让 maximum 变小，target 在它下面，不会被夹）。
        smooth = animations_enabled() and abs(bar.value() - target) >= 3
        if smooth:
            if self._rx_scroll_tween is None:
                self._rx_scroll_tween = Tween(180, out_cubic, self._on_rx_scroll,
                                              parent=self)
            self._rx_scroll_tween.start(bar.value(), target, 200)
        else:
            if self._rx_scroll_tween is not None:
                self._rx_scroll_tween.stop()
            bar.setValue(target)
        # 光标放回本轮时间戳行（焦点/选区别留在半路）。
        cursor = QTextCursor(document)
        cursor.setPosition(document.findBlockByNumber(stamp_start).position())
        self.txt_rx.setTextCursor(cursor)
        if smooth:
            # ⛔ 动画途中**不能** ensureCursorVisible：它会把滚动条当场拽到光标
            #    那里，补间就白跑了。等落点到位再兜一次（此时通常已是空操作）。
            QTimer.singleShot(220, self._ensure_pause_cursor_visible)
        else:
            self.txt_rx.ensureCursorVisible()

    def _ensure_pause_cursor_visible(self) -> None:
        """暂停定位落点之后兜一次光标可见（已恢复滚动就不管了）。"""
        if not self._paused:
            return
        try:
            self.txt_rx.ensureCursorVisible()
        except RuntimeError:
            pass

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

    # -------------------------------------------------------------- 历史数据回放

    def _open_history(self) -> None:
        """「打开 / 关闭历史数据」—— 这颗键是**开关**，不是单向入口。

        用户点名两次（先"要能打开本地留存数据"，再"再点一次就该关掉"）：所以
        回放态下再点它 = 返回实时，不是再灌一遍同一个文件。文案随状态变（见
        ``_refresh_replay_ui``），否则用户看不到这一下会"关掉"。

        格式、风格、功能与实时接收**完全一致** —— 不复刻一套渲染，而是把文件
        内容灌进同一个接收区、同一套着色器/排版管线，所以时间戳紫、序号蓝、
        电压红/绿、温度黄、BMS 橙、搜索、定位异常、复制、保存全都照旧可用。
        回放期间实时数据不覆盖画面（串口若开着，帧照样进历史文件，只是不往
        这块只读画面里写）——顶部给一条返回实时的提示条。
        """
        if self._replay_active:
            self._exit_replay()
            return
        import rx_history
        rows = rx_history.all_lines()
        if not rows:
            self._toast("还没有本地留存的接收数据", 3000)
            return
        self._enter_replay(rows)
        self._toast(f"已载入本地留存数据 {len(rows)} 行（可搜索/定位异常/复制；"
                    "点右上角返回实时）", 5000)

    def _enter_replay(self, rows) -> None:
        """把给定行灌进接收区并进入回放模式。

        三步的顺序都是刻意排的：
          1. 先抓"当前画面"并**立刻铺上去**（见 ``_show_rx_shot``）——
             必须在改文本**之前**，否则后面那次同步重绘会把新内容直接亮出来，
             交叉淡入就没了；
          2. ``clear() + setPlainText()`` 换内容，然后 ``viewport().repaint()``
             把"塞进几千行之后的第一帧重排 + 高亮 + 绘制"**当场付掉**；
          3. 最后才起两条动画（提示条高度补间、快照淡出）。

        ⚠️ 第 2 步那个 ``repaint()`` 不是多余的：实测把它去掉的话，那一帧的重绘
        会落在补间**起跑之后**，把计时器堵住 100ms 以上 —— 220ms 的坡被吃掉头
        三分之一，条从 0 直接跳到 43px（QA 实测"只有 3 个高度变化帧"就是这么来
        的）。在快照盖住的情况下先画，用户看到的仍然是旧画面。
        """
        shot = self._grab_rx_shot()
        self._show_rx_shot(shot)
        self.txt_rx.clear()
        self._reset_matches()
        self._clear_alert_highlight()
        # 回放内容走与实时相同的着色/排版管线：_align_section 从头推（历史文件
        # 里每轮都带时间戳，段状态自洽），着色的段状态由 QSyntaxHighlighter
        # 自己按块序推，这里只需要把文本塞进去。
        self._align_section = SECTION_OTHER
        self._stamp_anchor_seen = False
        self._stamp_last_monotonic = 0.0
        text = "\n".join(rows)
        self.txt_rx.setPlainText(text)
        self.txt_rx.moveCursor(QTextCursor.MoveOperation.Start)
        self._settle_rx_layout()
        self._replay_active = True
        self._refresh_replay_ui()
        self._fade_rx_shot()

    def _exit_replay(self) -> None:
        """返回实时：清掉回放画面，回到"等待实时数据"的姿态。"""
        shot = self._grab_rx_shot()
        self._show_rx_shot(shot)
        self._replay_active = False
        self._refresh_replay_ui()
        self._clear_receive()
        self._settle_rx_layout()
        self._fade_rx_shot()

    def _settle_rx_layout(self) -> None:
        """把接收区的"第一帧大重排"当场画掉（同步，不等事件循环）。

        回放的几千行刚塞进 ``QPlainTextEdit`` 时，文档重排 + 高亮 + 整块绘制要
        几十到上百毫秒，而且**发生在下一次绘制时**。让它落在动画起跑之前，
        动画才不会被堵成两三个台阶。
        """
        try:
            self.txt_rx.viewport().repaint()
        except RuntimeError:                # 窗口已析构（退出流程里会走到）
            pass

    def _refresh_replay_ui(self) -> None:
        """回放模式下：显示返回实时的提示条、按钮文案改成"关闭"、换占位提示。

        提示条的显隐**不直接 show/hide**，走 ``_replay_bar_anim`` 的高度补间：
        一帧里凭空多出 50px 会把下面的日志框猛地顶下去。
        """
        if self._replay_active:
            self.btn_open_history.setText("关闭历史数据")
            self._replay_bar_anim(True)
            self.txt_rx.setPlaceholderText("正在查看本地留存数据（点右上角返回实时）")
        else:
            self.btn_open_history.setText("打开历史数据")
            self._replay_bar_anim(False)
            # 与 __init__ 一致：**不设**占位提示（Qt 只按"正文色×≈0.5"画它，
            # 永远比别处暗一半；详见 __init__ 里那段说明）。
            self.txt_rx.setPlaceholderText("")

    def _replay_bar_height(self) -> int:
        """提示条展开后的高度（≈50）—— 按布局的 sizeHint 现算，别写死。

        ⚠️ 用 ``layout().sizeHint()`` 而不是 ``widget.sizeHint()``：条的高度在
        动画期间被 ``setMaximumHeight`` 钉着，控件级的 sizeHint 会被那些约束
        夹住；布局的 sizeHint 只跟内容（内边距 + 按钮高）有关，是稳的。
        """
        bar = self._replay_bar
        layout = bar.layout()
        hint = layout.sizeHint().height() if layout is not None else bar.sizeHint().height()
        return max(1, int(hint) if hint > 0 else 50)

    def _replay_bar_anim(self, expand: bool) -> None:
        """提示条高度补间：0 ⇄ ``_replay_bar_height()``，220ms ``EASE``。

        为什么补间：一帧里凭空多出 50px，日志框会被猛地顶下去 —— 高刷屏上非常
        扎眼（搜索条就是同一个理由用的 Reveal）。

        ⚠️ 曲线用 ``ui_anim.EASE``（``cubic-bezier(0.25, 0.1, 0.25, 1)``，即 CSS
        的 ``ease``），**不是** ``STANDARD``／``in_out_cubic``／``in_out_quint``。
        这条动画动的是**版式占位**（50px 高度），不是透明度/缩放，验收口径是
        "每一帧都看得出来在走"。同一套补间引擎（120Hz 节拍、``min_interval_ms=16``、
        ``max(1, round(v))`` 量化地板）下逐帧高度实测/推演：
          · ``STANDARD`` = ``bezier(0.2, 0, 0, 1)``：1,10,23,31,36,40,... —— 第一帧
            就 1→10px，像"被甩进来"，后段又几乎不动（12 个值但前 40ms 走完 62%）；
          · ``in_out_quint``：1,3,8,19,34,44,48,49,50 —— 前 72ms 全在 3px 以内，
            然后 60ms 里窜到 44px，更难看；
          · ``in_out_cubic``：1,3,7,13,21,31,39,44,47,49,50 —— 比 quint 好，但前
            56ms 只到 3px；165Hz 屏上这就是"顿一下再开始"（用户原话）；
          · ``EASE``：1,5,13,21,28,34,39,42,45,47,48,49,50 —— **13 个台阶、单步
            ≤8px、首个 ≥2px 在 24ms、末变仍在 200ms**（220ms ± 一帧内跑满）。
        注意 ``t=0`` 那一帧高度就是 1px（``max(1, round(0.0))`` 的地板），所以
        "首帧有没有变化"按 1px 算是 0ms、按 ≥2px 算是 24ms。

        ⛔ 不要在调用处写 ``bezier(...)``：曲线是**调参单点**，住在 ``ui_anim.EASE``。

        **可打断**：起点取条的**当前真实高度**，中途反向点击就是平滑折返，
        不会先跳回 0 再重新长。

        **为什么要节流到 60fps**（``min_interval_ms=16``）：这条动画每帧都要
        把日志区高度改一次，而日志区重绘一次实测 7~14ms（2x DPR、3000 行文本
        整块重画，见 devtools 里那组基准）。125Hz 的共享计时器每 6ms 就送一个
        新值，布局根本跟不上 —— 一帧里连推 3 次高度，最终只渲染最后一次，
        条就变成几个大台阶。锁到 16ms 之后每送一次就渲染一次。

        收尾还原 min/max（见 ``_replay_bar_settle``）：动画期间高度由 max 独占，
        钉死了不还回去，以后换字号/换主题条就再也长不高了。
        """
        bar = self._replay_bar
        full = float(self._replay_bar_height())
        target = full if expand else 0.0
        # 藏着的时候真实高度是"上一次的残留"，一律当 0 看
        start = 0.0 if bar.isHidden() else float(bar.height())
        if self._replay_tween is not None:
            self._replay_tween.stop()
            # 每段补间都是新对象（起点不同），旧的不回收就会一直挂在 self 下
            self._replay_tween.deleteLater()
            self._replay_tween = None
        if abs(start - target) < 0.5:
            self._replay_bar_settle(expand)
            return
        if expand:
            bar.show()
        # 补间期间 min 一律放开、只由 max 说话（见 _replay_bar_step 的说明）
        bar.setMinimumHeight(0)
        bar.setMaximumHeight(max(1, round(full)))
        tween = Tween(_REPLAY_BAR_MS, EASE,
                      self._replay_bar_step if expand else self._replay_bar_step_down,
                      lambda: self._replay_bar_settle(expand),
                      parent=self, min_interval_ms=_REPLAY_BAR_MIN_MS)
        self._replay_tween = tween
        tween.start(start, target, _REPLAY_BAR_MS)

    def _replay_bar_step(self, value: float) -> None:
        """补间的一帧：**只改 maximumHeight**，把高度压到当前值。

        为什么不同时改 minimumHeight（那样才是"钉死高度"）：
          · 实测只改 max、min 恒 0 时单帧成本 **6.5ms**，两个都改是 **13~14ms**
            —— 一帧 220ms 的动画只剩 15 个台阶，还不如不补间；
          · 效果完全一样：条的 sizeHint 是 50、max 压到 h，布局给的就是 h。
        数据见 ``devtools`` 里那组 `setFixedHeight(h)+processEvents` 基准。

        ⚠️ ``max`` 不许写 0：``_refresh_replay_ui`` 之后马上断
        ``maximumHeight() > 0`` 会假挂（补间 ``start()`` 会立刻送一次起点值）。
        收起方向那根 1px 亮线由 ``_replay_bar_step_down`` 提前 hide 掉。
        """
        self._replay_bar.setMaximumHeight(max(1, round(value)))

    def _replay_bar_step_down(self, value: float) -> None:
        """收起方向的一帧：落到 ≤2px 就直接藏起来。

        1px 高的自绘圆角（``ReplayBar.paintEvent`` 按 ``min(RADIUS, h/2)`` 取半径）
        整条横向都是描边，会在收尾挂一根亮线；2px 高时肉眼已经看不出来，从这里
        hide 不产生任何可见跳变（补间照旧跑完，收尾还会再 hide 一次）。
        """
        self._replay_bar_step(value)
        if round(value) <= 2:
            self._replay_bar.hide()

    def _replay_bar_settle(self, expand: bool) -> None:
        """补间收尾：展开 → 还原 min/max（回到按 sizeHint 自适应）；收起 → 归零并 hide。"""
        tween = self._replay_tween
        self._replay_tween = None
        if tween is not None:
            tween.deleteLater()      # 这一段已经跑完，别一直挂在 self 底下
        bar = self._replay_bar
        if expand:
            bar.setMinimumHeight(0)
            bar.setMaximumHeight(_WIDGET_MAX)
            bar.show()
            return
        bar.setMinimumHeight(0)
        bar.setMaximumHeight(0)
        bar.hide()

    def _grab_rx_shot(self) -> QPixmap | None:
        """抓接收区**当前画面**（viewport）—— 交叉淡入的底图。

        抓不到（窗口没建/尺寸为 0/已被析构）就返回 None，调用方直接跳过淡入
        —— 宁可硬切，也不能因为抓图失败把文字切换本身搞坏。
        """
        try:
            viewport = self.txt_rx.viewport()
        except RuntimeError:                    # 窗口已析构（退出流程里会走到）
            return None
        if viewport is None or viewport.width() <= 0 or viewport.height() <= 0:
            return None
        try:
            shot = viewport.grab()
        except RuntimeError:
            return None
        if shot.isNull():
            return None
        # 抓回来的是物理像素，标好比例，贴图时才不会被 Qt 按 1x 缩放一次
        shot.setDevicePixelRatio(viewport.devicePixelRatioF())
        return shot

    def _show_rx_shot(self, shot: QPixmap | None) -> None:
        """把旧画面**铺上去并停在完全不透明**，等 ``_fade_rx_shot`` 淡出。

        分成"铺"和"淡"两步是因为中间要夹一次同步重绘（见 ``_settle_rx_layout``）：
        换内容的瞬间就必须有东西盖着，否则那次重绘会把新内容直接亮出来。

        打断规则：上一段还没跑完就从**它当前的 alpha** 接着走（不重置回 1），
        否则连点两下会看到旧画面"闪回去"再淡一次。
        """
        layer = self._rx_shot
        if self._rx_shot_tween is not None:
            self._rx_shot_tween.stop()
            self._rx_shot_tween.deleteLater()
            self._rx_shot_tween = None
        if shot is None:
            layer.hide()
            layer.set_shot(None)
            layer.set_alpha(1.0)
            return
        # ⚠️ 用 isHidden() 而不是 isVisible()：窗口本身没显示时（自测就是）
        #    isVisible() 恒为 False，那样每次都会当成"没有正在跑的淡出"、从 1.0
        #    重新开始 —— 连点两下就会看到旧画面闪回去。isHidden() 才是"这一层
        #    是否被要求显示"。
        layer.set_shot(shot)
        layer.set_alpha(layer.alpha() if not layer.isHidden() else 1.0)
        layer.sync_geometry()
        layer.show()
        layer.raise_()

    def _fade_rx_shot(self) -> None:
        """旧画面 160ms 淡出（从它**当前**的 alpha 起跑，可打断）。

        新内容已经在底下了、一开始就是完全不透明的，所以这里只做"旧内容淡出"
        —— 视觉上是一次交叉淡入，没有中间的空帧。
        """
        layer = self._rx_shot
        if layer.isHidden() or layer.get_shot() is None:
            return
        start = layer.alpha()
        if start <= 0.001:
            self._finish_rx_xfade()
            return
        tween = Tween(_RX_XFADE_MS, STANDARD, self._rx_xfade_step,
                      self._finish_rx_xfade, parent=self)
        self._rx_shot_tween = tween
        tween.start(start, 0.0, _RX_XFADE_MS)

    def _rx_xfade_step(self, value: float) -> None:
        """交叉淡入的一帧：几何跟着 viewport（滚动条可能刚显隐），再推透明度。"""
        self._rx_shot.sync_geometry()
        self._rx_shot.set_alpha(value)

    def _finish_rx_xfade(self) -> None:
        """淡完收尾：撤图、藏层、alpha 复位（下一段从全不透明重新开始）。"""
        tween = self._rx_shot_tween
        self._rx_shot_tween = None
        if tween is not None:
            tween.deleteLater()
        self._rx_shot.hide()
        self._rx_shot.set_shot(None)
        self._rx_shot.set_alpha(1.0)

    def _on_replay_close(self) -> None:
        self._exit_replay()

    # --------------------------------------------------------------------- 发送

    def _on_send(self) -> None:
        if self._worker is None:
            self._warn("串口还没打开")
            self._send_timer.stop()
            self.chk_timer_send.setChecked(False)
            return
        if isinstance(self._worker, _ble_worker_cls()):
            # 广播是**单向**的：只扫描、不连接，压根没有"发给设备"这条路
            #（BLE 的写操作必须建立在连接之上，而广播模式没有连接）。
            self._warn("蓝牙广播只能接收，发不出去。\n"
                       "要发指令给设备，请在「连接方式」里选串口（需要先配对）。")
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

        同时把**本地留存行数**调到一致（用户 2026-10-13 点名"本地留存接收行数
        备份改为与软件内接收区保留行数一致，并在用户修改后同步更新"）。
        """
        self._rx_blocks = max(RX_LINES_MIN, min(RX_LINES_MAX, int(value)))
        self.txt_rx.setMaximumBlockCount(self._rx_blocks)
        import rx_history
        rx_history.set_max_lines(self._rx_blocks)

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

    def _set_state(self, text: str, busy: bool = False) -> None:
        """状态行**唯一**的写法：文字和左边那颗灯一起定。

        以前各处直接 ``lbl_state.setText(...)``，灯的状态靠各自记 —— 加了"连接中"
        的矢量弧之后两边必须同源，否则会出现"弧还在转、其实早就连上了"。

        busy=True 只用于"正在连接"：那是唯一一个"在等某件事"的状态。
        """
        self.lbl_state.setText(text)
        self.pulse.set_busy(busy)

    def _update_counters(self) -> None:
        # ⚠️ 数值**立刻**写成真值，只在变化时补一次高光 —— 不做逐位滚动，
        #    因为任何地方读 .text() 都必须拿到真数（自测里就有"接收 4 字节"这条）。
        text = f"接收 {self._rx_bytes} 字节 · 发送 {self._tx_bytes} 字节"
        if text != self.lbl_count.text():
            self.lbl_count.setText(text)
            self.lbl_count.flash(0.8, duration=360)

    def _toast(self, message: str, ms: int = 3000) -> None:
        """底部信息条里的临时提示。到点自动清空。"""
        self.lbl_toast.setText(message)
        self._toast_timer.start(ms)

    def _open_settings(self) -> None:
        """设置键 = **开关**：开着就关、关着就开（用户点名"已经打开的情况下
        再点击设置按钮让设置界面关闭"）。

        动画是 iOS 开应用那种"**整块均匀放大**"：面板先 ``grab()`` 成一张位图，
        再由 ``_SettingsShot`` 把这张位图从设置按钮矩形等比放大到面板最终矩形
        （340ms / ``STANDARD`` 临界阻尼），落地时换回真面板；关闭走同一条路反向往回。

        ⛔ 不用 ``exec()``：模态阻塞一来没有动画，二来动画期间会卡住事件循环。
        ⛔ 不用 geometry 插值 + 每帧内容重排（那只是外框在滑），也不用
        ``QGraphicsOpacityEffect`` / ``QGraphicsScale``（离屏合成 = 抽搐）。

        状态机是"**目标态 + 当前矩形**"：点一下只改目标态，补间从当前矩形继续
        插值 —— 所以动画中途再点不会跳，而是平滑折返（苹果的可打断原则）。
        """
        if self._settings_dialog is not None:
            # 已经开着（或正在开/关的动画里）：目标态取反
            self._close_settings_dialog(accepted=False)
            return
        previous_glass = glass_strength()
        previous_material = material()
        previous_hidden = set(self._hidden_buttons)
        previous_font = customization.display_value("rx_font_size")
        # 帧率也要记：取消时得还回去（它是**立即生效**的，不还就"点取消也变了"）
        previous_fps = ui_anim.frame_hz()
        self._settings_previous = (previous_glass, previous_material,
                                   previous_hidden, previous_font, previous_fps)
        dialog = SettingsDialog(self._theme_mode, previous_glass,
                                BUTTON_SPECS, sorted(self._hidden_buttons),
                                on_glass=self._apply_glass,
                                on_buttons=self._preview_buttons,
                                on_custom=self._preview_custom, parent=self)
        # ⚠️ 必须**非模态**：模态会自己起一个局部事件循环，把补间挡在外面。
        dialog.setModal(False)
        # 记下**静止态**的最小尺寸（A3）：``_animate_settings_geometry`` 会把它
        # 放开成 (0, 0)，落定时这里整份还回去（不只是宽度，见 __init__ 里的说明）。
        self._settings_min_size = dialog.minimumSize()
        self._settings_dialog = dialog
        dialog.accepted.connect(self._on_settings_accepted)
        dialog.rejected.connect(self._on_settings_rejected)
        # 关闭钩子：面板的确定/取消/Esc/X 都经 done() → 这里起收场动画，
        # **不立刻 hide**（否则收场动画跑在隐藏窗口上，用户看不到"回按钮去"）。
        dialog._finish_hook = self._close_settings_dialog
        # 目标几何：整窗矩形（**含原生标题栏**），居中到主窗口；起点是设置按钮那一小块。
        # ⚠️ 必须在 show() **之后**再取 frameGeometry：没显示过的话拿到的是还没算稳的
        #    尺寸（用户真实配置会影响布局最小宽）。
        # ⚠️ 抓图窗口要**挪到屏幕外**：PrintWindow 必须窗口可见才有 DWM 重定向内容
        #    （WA_DontShowOnScreen 实测只能抓到全黑），但真让它在屏幕中央 show 一下
        #    就是用户报的那个"闪一下"。挪到 -4000,-4000 再 show，用户什么都看不到。
        # ⚠️ **不要**用 WA_ShowWithoutActivating：那会让快照里的标题栏是"非激活态"，
        #    而动画结束真面板上屏时是激活态 —— 两态标题栏亮度不同，换帧那一下就是
        #    用户说的"动画结束时闪一下"（快照其他部分逐像素一致也没用，就差这一块）。
        #    让它抓图时就激活（在屏幕外，主窗口焦点丢一下看不见），上屏时两态一致。
        dialog.move(_SETTINGS_OFFSCREEN, _SETTINGS_OFFSCREEN)
        dialog.show()
        dialog.activateWindow()
        self._pump_until_exposed(dialog)
        target = dialog.frameGeometry()
        target.moveCenter(self.frameGeometry().center())
        self._settings_geom_from = self._settings_button_rect()
        self._settings_geom_to = QRect(target)
        self._settings_geom_opening = True
        # 确保快照以**全不透明、非分层**状态开始：入场是「硬切」，落定那拍
        # 真面板以不透明上屏、快照同拍撤走，动画期间没有任何亚满 alpha 操作，
        # 所以这道窗口始终是普通非分层窗口（见 _SettingsShot 的 exstyle 说明），
        # 不会触发「非分层↔分层」的 DWM 合成切换 —— 那次切换正是当初
        # 「打开时最后一帧闪」的根因（现由硬切 + 不带 layered 一起根除）。
        self._settings_shot.set_alpha(1.0)
        self._show_settings_shot(self._grab_settings_frame())
        dialog.hide()
        self._animate_settings_geometry(self._settings_geom_from,
                                        self._settings_geom_to, opening=True)

    def _pump_until_exposed(self, dialog, timeout_ms: int = 200) -> None:
        """等屏幕外那个窗口**画出首帧**（PrintWindow 要 DWM 重定向内容才抓得到）。

        一次 ``processEvents`` 只能让它完成布局并映射，未必已经 paint —— 那时抓回来
        是全黑。这里小步轮询到 exposed 为真（或超时），窗口在屏幕外，用户看不见这段。
        """
        app = QApplication.instance()
        if app is None:
            return
        deadline = time.perf_counter() + timeout_ms / 1000.0
        while time.perf_counter() < deadline:
            app.processEvents()
            handle = dialog.windowHandle()
            if handle is not None and handle.isExposed():
                # 再多给一拍：exposed 为真时这一帧的内容可能还没进重定向表面
                app.processEvents()
                return
            time.sleep(0.004)

    def _settings_button_rect(self) -> QRect:
        """设置按钮的矩形 —— 面板从这里长出来，也收回这里。

        ⚠️ 返回的是**屏幕坐标**（``mapToGlobal``），不是主窗口坐标。

        为什么非得是屏幕坐标：``_SettingsShot`` 构造时被设成
        ``Qt.WindowType.ToolTip``（要无边框、不抢焦点），它就**变成了顶层窗口**
        —— 不再是主窗口的 child widget 意义上的绘制层，``setGeometry`` 收的是
        **屏幕坐标**。而动画终点 ``_settings_geom_to`` 取的是
        ``dialog.frameGeometry()``，那本来就是屏幕坐标。起点用主窗口坐标、
        终点用屏幕坐标 = 两套坐标系混着插值，主窗口只要不在屏幕左上角
        (0, 0)，面板就会"斜着滑一段再稳住"，收场则缩到离按钮很远的地方。

        早先这里写的是 ``mapTo(self, QPoint(0, 0))``（返主窗口坐标），是因为
        那时误以为快照是主窗口的子控件 —— 它其实是顶层窗口，所以必须换过来。
        """
        return QRect(self.btn_settings.mapToGlobal(QPoint(0, 0)),
                     self.btn_settings.size())

    def _animate_settings_geometry(self, start: QRect, end: QRect,
                                   opening: bool) -> None:
        """面板"变大 / 变小"的连续过渡：**整块内容等比缩放**，不是外框滑过去。

        做法：把面板 grab 成一张位图，动画期间只把这张位图 ``drawPixmap`` 进
        当前矩形 —— 图标、文字、间距一起等比放大，正是用户要的"像 iOS 打开
        应用那样变大"。⛔ 不用 geometry 插值 + 每帧内容重排（那只是外框在滑、
        内容被拉伸），也不用 ``QGraphicsOpacityEffect`` / ``QGraphicsScale``
        （离屏合成 = 抽搐，项目铁律）。

        ⚠️ 动画期间要把面板的最小尺寸放开成 (0, 0)：``ui_settings`` 给面板钉了
        ``setMinimumWidth(500)``（外加内容的最小高），不放开的话面板永远缩不到
        按钮那一小块。500 只在**静止态**保留（落地后恢复，见
        ``_on_settings_geometry_done``）。
        """
        if self._settings_dialog is None:
            return
        self._settings_geom_from = QRect(start)
        self._settings_geom_to = QRect(end)
        self._settings_geom_opening = bool(opening)
        self._settings_dialog.setMinimumSize(0, 0)
        if self._settings_geom_tween is None:
            self._settings_geom_tween = Tween(260, STANDARD,
                                              self._on_settings_geometry,
                                              self._on_settings_geometry_done,
                                              parent=self)
        self._settings_geom_tween.start(0.0, 1.0, 260)

    def _grab_settings_frame(self) -> QPixmap:
        """把设置面板**整个窗口**（含原生标题栏）抓成位图。

        ⛔ 不能再只用 ``dialog.grab()``：它只抓**客户区**，原生标题栏（实测 29px）
        不在里面。于是入场动画里长出来的是"没有标题栏的面板"，动画结束、真面板
        落地那一刻标题栏凭空多出来一条 —— 内容还被顶下 29px，肉眼就是**闪**
        （用户连报两次"打开设置还是会有闪"）。查闪烁时先后排除了状态机本身
        （无空洞帧 / 不提前上屏，见 devtools/_probe_settings_flicker.py），
        根因就是这张图缺了标题栏。

        所以走 ``PrintWindow(PW_RENDERFULLCONTENT)``：经 DWM 重定向，**连非客户区
        一起渲染**，抓回来的图与 ``frameGeometry()`` 逐像素对齐 —— 动画终点就是真
        面板的落点，换帧时没有任何错位。

        ⚠️ 必须窗口**可见**时抓：实测 ``WA_DontShowOnScreen`` 下 PrintWindow 只拿到
        全黑（没有 DWM 重定向内容）。所以入场那一步把窗口挪到屏幕外再抓
        （见 ``_open_settings``），收场那步直接抓可见窗口。

        按 ``frameGeometry()`` 裁掉 DWM 那圈不可见缩放边框（GetWindowRect 比它大
        6~7px，留着动画里会露出一圈黑边），并原样带上 devicePixelRatio。
        """
        dialog = self._settings_dialog
        if dialog is None:
            return QPixmap()
        dpr = dialog.devicePixelRatioF()
        frame = dialog.frameGeometry()
        self._settings_frame_margins = self._dialog_frame_margins(dialog)
        pixmap = _capture_native_window(int(dialog.winId()))
        if pixmap.isNull():
            # 兜底：万一 PrintWindow 失败（非 Windows / 被拦截），退回客户区抓图 ——
            # 宁可退回"标题栏晚出现"，也不能整个动画空掉。
            fallback = dialog.grab()
            fallback.setDevicePixelRatio(dpr)
            return fallback
        pixmap.setDevicePixelRatio(dpr)
        want_w = int(round(frame.width() * dpr))
        want_h = int(round(frame.height() * dpr))
        # ⚠️ 裁剪基准是**扩展边框矩形**（GetWindowRect）与 frameGeometry 的差 ——
        #    两者之间只差 DWM 那圈不可边（实测每边 6~7px）；标题栏**在** frame
        #    高度之内（frame.height() = 客户区 + 标题栏），别拿标题栏高度当外边框，
        #    那样会多裁掉一个标题栏（实测位图矮了 16px）。
        window_rect = _window_rect_physical(int(dialog.winId()))
        if window_rect is not None:
            # ⚠️ GetWindowRect（物理）**包住** frameGeometry：DWM 那圈不可见缩放
            #    边框长在 frame 的**外侧**（实测 dpr=2 时：左/右/下各 13 物理px，
            #    上边 0 —— 顶部是标题栏，阴影不往上长）。所以要裁掉的是
            #    **frame 物理左 − WindowRect 左**（正数 13）。
            # ⛔ 千万别写成 `window_rect[0] - frame`（反了）：那得 −13，被
            #    max(0, ·) 夹成 0 = **根本没裁** —— 快照内容整体右偏一个边框宽
            #    （dpr=2 → 约 7 逻辑px），撤快照那一下真面板相对快照**向左跳 7px**。
            #    这正是用户报的「打开设置最后一帧整体往左闪一下」（2026-10-18
            #    用 `devtools/_probe_settings_swaplr.py` 前后帧对比实测 dx=−7 定位）。
            crop_x = max(0, int(round(frame.x() * dpr)) - window_rect[0])
            crop_y = max(0, int(round(frame.y() * dpr)) - window_rect[1])
            crop_w = min(want_w, pixmap.width() - crop_x)
            crop_h = min(want_h, pixmap.height() - crop_y)
            if crop_w > 8 and crop_h > 8 and (crop_x or crop_y
                                              or crop_w != pixmap.width()
                                              or crop_h != pixmap.height()):
                cropped = pixmap.copy(crop_x, crop_y, crop_w, crop_h)
                cropped.setDevicePixelRatio(dpr)
                return cropped
        return pixmap

    def _dialog_frame_margins(self, dialog) -> tuple[int, int, int, int]:
        """窗口边框四周的宽度（左, 上, 右, 下）= 客户区相对 frameGeometry 的偏移。

        动画全程以 **frame 矩形**为坐标（位图含标题栏，尺寸就是整窗），而
        ``setGeometry`` 收的是**客户区**矩形 —— 两者差这圈边框。不换算的话落地时
        窗口会整体偏一个标题栏的高度（实测 29px），又是一下看得见的跳。
        """
        frame, rect = dialog.frameGeometry(), dialog.geometry()
        return (rect.x() - frame.x(), rect.y() - frame.y(),
                frame.right() - rect.right(), frame.bottom() - rect.bottom())

    def _place_dialog_frame(self, dialog, frame_rect: QRect) -> None:
        """把窗口摆到"**边框矩形** = frame_rect"的位置（换算成客户区再 setGeometry）。

        换算方向别搞反：``frame.x = client.x - left``、``frame.height = client.height
        + top + bottom``，所以由 frame 反推 client 是 **加** left/top、**减** 边框宽高。
        （第一版这里符号写反，落地时窗口反而又高又往上挪了 58px —— 正好是标题栏
        的两倍，用户看到的就是"面板抖一下再稳住"。）
        """
        left, top, right, bottom = self._settings_frame_margins
        dialog.setGeometry(QRect(frame_rect.x() + left,
                                 frame_rect.y() + top,
                                 max(1, frame_rect.width() - left - right),
                                 max(1, frame_rect.height() - top - bottom)))

    def _show_settings_shot(self, shot: QPixmap) -> None:
        """把快照容器摆到当前动画矩形上，准备开始放大/缩小。"""
        holder = self._settings_shot
        holder.set_shot(shot)
        holder.setGeometry(self._settings_geom_from)
        holder.show()
        holder.raise_()

    def _on_settings_geometry(self, t: float) -> None:
        """把快照画进当前矩形：**以按钮中心为锚**缩放，中心同时漂向面板中心。

        左上角对齐的插值看着是"滑过去"；改成"尺寸按 t 插值 + 中心按 t 插值"
        之后，面板是从设置按钮那个点**鼓大**并漂到居中的。

        ⚠️ 中心一律用**浮点**算（``x + width / 2.0``），⛔ 不要读
        ``QRect.center()``：它是 ``left + (width - 1) // 2``，宽高为**奇数**时
        比几何中心少 0.5px。起点是按钮（宽 30）、终点是面板（宽 500），两者的
        奇偶不同，于是"中心"这条曲线在 t=1 那端差半个像素 —— 落地前的最后
        一帧快照比真面板偏 1px，换帧时就是一条横线在抖。探针
        ``_probe_settings_residual.py`` 量到过这一条：客户区 y≈660 那一带
        2269 个点全变（占该带 90%），正是面板的底边线。

        ⚠️ ``t >= 1.0`` 直接**精确落位**成 ``_settings_geom_to``：兜住上面那个
        取整残差，保证换帧那一刻快照与真面板是**同一个矩形**（逐像素可比）。
        """
        holder = self._settings_shot
        if not holder.isVisible():
            return
        a, b = self._settings_geom_from, self._settings_geom_to
        if t >= 1.0:
            holder.setGeometry(b)
            return
        width = a.width() + (b.width() - a.width()) * t
        height = a.height() + (b.height() - a.height()) * t
        ax, ay = a.x() + a.width() / 2.0, a.y() + a.height() / 2.0
        bx, by = b.x() + b.width() / 2.0, b.y() + b.height() / 2.0
        center_x = ax + (bx - ax) * t
        center_y = ay + (by - ay) * t
        holder.setGeometry(QRect(round(center_x - width / 2.0),
                                 round(center_y - height / 2.0),
                                 max(1, round(width)), max(1, round(height))))

    def _on_settings_geometry_done(self) -> None:
        """入场跑完 → 换回真面板；收场跑完 → 落地结果并关闭。

        ⚠️ 换回的那一刻必须**逐像素一致**：容器（位图）与真面板同一几何、同一
        内容，用户看不出"闪了一下"。探针 ``_probe_settings_anim.py`` 会抓两张
        图逐点比对中心与四角。
        """
        try:
            dialog = self._settings_dialog
        except RuntimeError:       # 窗口先被析构（退出时补间才回调），直接收手
            return
        if dialog is None:
            return
        if self._settings_geom_opening:
            # ⚠️ 先恢复最小尺寸**再**落位：反过来会先按 0 宽定位、show 后又触发
            #    一次重排，首帧尺寸会跳一下（用户说的"闪"的另一处来源）。
            # ⚠️ 必须**整份**还原（A3）：早先只写 setMinimumWidth(500)，面板内容
            #    撑出来的**最小高**一直停在动画期间的 0 —— 之后任何一次重排
            #    （改字号、切主题）都会按"能缩到很矮"重新算，面板高度会抖一下。
            self._restore_settings_min_size(dialog)
            # ⚠️ 落位用"边框矩形"换算：动画全程（含标题栏）的坐标是整窗矩形，
            #    而 setGeometry 收客户区 —— 不换算窗口会整体偏一个标题栏的高度
            #    （实测 29px），落地那一下内容会往上跳一截。
            self._place_dialog_frame(dialog, self._settings_geom_to)
            # 以**不透明**上屏：setWindowOpacity(1.0) 是 DWM 合成属性（不是被禁的
            # QGraphicsOpacityEffect），窗口从第一帧就是非分层、系统阴影同一拍到位，
            # 不会「先没阴影→啪一下出现」。快照先 raise_ 垫在最顶，真面板 show
            # 之后同拍用硬切换掉它（见下方「为什么是硬切」），全程只有一份面板像素。
            self._settings_shot.raise_()
            dialog.setWindowOpacity(1.0)
            dialog.show()
            dialog.raise_()
            # ⚠️ show() 只是"请求映射"，真正的首帧要等这一拍的事件循环。
            #    所以先**同步**把首帧画出来（repaint 不等事件循环），再让 DWM
            #    立刻合成一遍。
            dialog.repaint()
            self._dwm_flush()
            # 快照抬到最顶，盖住 dialog：dialog 首帧 DWM 会先呈白底（见下）。
            self._settings_shot.raise_()
            # 换帧 = **硬切**，但快照延迟一拍撤：dialog 渲染本身正确
            # （dialog.grab() 均值≈34，深色），白底是 DWM 首帧合成时序竞态——
            # show() 把窗口映射、repaint()+dwm_flush() 把深色背板刷好，但 DWM
            # 把「白底默认帧」呈现给屏幕往往比深色背板快一拍（纯合成时序，非渲染）。
            # 同拍撤快照→那一白帧漏出来（devtools/_frames_open_sync/f007_settled.png
            # 整片白、均值 209）。故把**不透明、与真面板逐像素一致**的快照多留约
            # 2 帧盖住它，等 dialog 合成出深色再硬切——既无白闪，也无旧版 96ms
            # 半透明叠加鬼影（快照全程 alpha=1.0，不碰分层/亚满透明度）。
            self._settings_swap_target = dialog
            QTimer.singleShot(50, lambda: self._finish_settings_swap(dialog))
            return
        accepted = bool(self._settings_result)
        self._settings_dialog = None
        self._settings_shot.hide()
        self._settings_shot.set_shot(None)
        try:
            dialog.close()
            dialog.deleteLater()
        except RuntimeError:
            pass
        # ⚠️ 退出时窗口可能**先被析构**，几何补间才回调到这里（Qt 拆窗口时会把
        #    还在跑的补间 stop(finished=True) 一次）—— 那时 self 已经是"内部
        #    C++ 对象已删除"的空壳，_apply_settings_result 里任何 self.xxx() 都会
        #    抛 RuntimeError。除了 traceback 什么都不会坏，所以只吞这一种。
        try:
            self._apply_settings_result(accepted)
        except RuntimeError:
            pass

    def _restore_settings_min_size(self, dialog) -> None:
        """把面板的最小尺寸还原成**静止态**那一份（A3）。

        ⚠️ 只还宽度不够：``ui_settings`` 给面板钉了 ``setMinimumWidth(500)``，但
        内容布局还会撑出一个最小高 —— 动画期间两者都被放开成 0，落定只还宽度
        的话，高度那一半会永远停在"能缩到很矮"的状态，之后任何一次重排（切主题、
        改字号）都会让面板高度抖一下。

        ``_settings_min_size`` 拿不到（极端时序 / 面板已析构）时退回原来那条
        ``setMinimumWidth(500)``，行为不比改动前差。
        """
        size = self._settings_min_size
        try:
            if size is not None and size.isValid():
                dialog.setMinimumSize(size)
            else:
                dialog.setMinimumWidth(500)
        except RuntimeError:                    # 面板已经被析构掉了
            pass

    @staticmethod
    def _dwm_flush() -> None:
        """把 DWM 待合成的队列**当场刷出去**（仅 Windows，失败静默）。

        Qt 的 ``show()`` / ``repaint()`` 只保证"像素进了窗口表面"，真正上屏还要
        等 DWM 合成。不等它就直接起渐隐坡，坡的第一帧会跑在**上一帧**的内容上。
        ``DwmFlush()`` 是同步的（阻塞到合成完成），代价约一两毫秒，只在换帧这
        一次调用，不进任何动画循环。

        非 Windows、或 dwmapi 里没有这个符号（很老的系统 / 某些远程桌面会话）
        就跳过 —— 这一步只负责"首帧早点上屏"，缺了不影响正确性。
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            ctypes.windll.dwmapi.DwmFlush()
        except Exception:               # noqa: BLE001, S110  ← 拿不到 dwmapi 就算了
            pass

    def _finish_settings_swap(self, dialog) -> None:
        """撤快照收尾（入场落定硬切时触发）。

        硬切是同步的：调用方在落定那一拍里先 ``show()`` + ``repaint()`` +
        ``_dwm_flush()`` 把真面板推上屏，再调这里把垫着的快照撤掉 —— 全程
        屏幕上只有一份面板像素，没有「半透明快照叠真面板」的 1 帧双击曝光鬼影。
        """
        if self._settings_swap_target is not dialog:
            return
        try:
            if dialog.windowOpacity() < 1.0:
                dialog.setWindowOpacity(1.0)    # 兜底：落定必须全不透明
        except RuntimeError:
            pass
        self._settings_swap_target = None
        # 只有"入场落定"这一步负责撤快照。若已转入关闭动画（_settings_geom_opening
        # 被置 False），快照由关闭链路自己管，这里不动它 —— 否则会把正在缩回的
        # 动画 blanks 掉，又多一处闪。
        if self._settings_geom_opening and self._settings_shot is not None:
            self._settings_shot.hide()
            self._settings_shot.set_shot(None)
            self._settings_shot.set_alpha(1.0)

    def _on_settings_accepted(self) -> None:
        self._close_settings_dialog(accepted=True)

    def _on_settings_rejected(self) -> None:
        self._close_settings_dialog(accepted=False)

    def _close_settings_dialog(self, accepted: bool) -> None:
        """关闭：先**原路缩回按钮**，动画结束才真正落地结果。"""
        dialog = self._settings_dialog
        if dialog is None:
            return
        # 防御性收尾：入场换帧现在是**硬切**（落定那拍同步 show+repaint+
        # _dwm_flush+撤快照，没有挂着的渐隐补间），正常情况下用户能点关闭时
        # ``_settings_swap_target`` 早已被清。这里只兜底清状态，并确保快照是
        # 全不透明的——收场动画拿它当底图缩回按钮，半透明会整段漏底（"淡着消失"）。
        if self._settings_swap_target is dialog:
            self._settings_swap_target = None
        self._settings_shot.set_alpha(1.0)
        self._settings_result = bool(accepted)
        # ⚠️ 面板会在关闭动画结束时析构，结果必须**现在**抓下来（含"定制功能"
        # 里被改过的项），否则应用阶段会踩到已删除的控件。
        self._settings_values = dialog.values()
        self._settings_custom = dialog.custom_values()
        self._settings_hidden = dialog.hidden_buttons()
        # 抓一张"当前内容"的整窗快照（含标题栏）→ 藏起真面板 → 容器画同一张位图缩回按钮。
        # 抓图与隐藏在同一次调用里同步完成，用户看不到中间的闪烁。
        self._show_settings_shot(self._grab_settings_frame())
        dialog.hide()
        self._animate_settings_geometry(dialog.frameGeometry(),
                                        self._settings_button_rect(),
                                        opening=False)

    def _apply_settings_result(self, accepted: bool) -> None:
        """面板收起来之后：确定 → 落盘生效；取消 → 把实时预览全部还原。

        ⚠️ 面板在关闭动画结束时已经析构，所以这份结果必须在**决定关闭的那一刻**
        抓下来（见 ``_close_settings_dialog``），这里只负责应用。
        """
        if not accepted:
            # 取消：把实时预览（强度、材质、按钮显隐、字号）一并还原 ——
            # 少还原一样，界面就会停在预览状态里。
            (previous_glass, previous_material, previous_hidden, previous_font,
             previous_fps) = self._settings_previous
            self._apply_glass(previous_glass)
            self._apply_material(previous_material)
            self._hidden_buttons = previous_hidden
            self._apply_button_visibility()
            self._apply_rx_font_size(previous_font)
            ui_anim.set_frame_hz(previous_fps)
            return
        theme_mode, glass = self._settings_values
        # 「定制功能」由注册表统一管理：面板按 specs() 生成控件，这里统一落盘。
        # 生效不需要额外动作 —— pause_snap_lines 在暂停时现取，时间戳开关只
        # 影响之后的时间戳（已显示的历史不动）。
        custom_changes = self._settings_custom
        for key, value in custom_changes.items():
            customization.set(key, value)
            # 同一时刻打上"用户选过"的标记：以后改默认值时不会再把他选的值迁走
            customization.mark_chosen(key)
        if custom_changes:
            app_log.info("定制配置更新: "
                         + ", ".join(f"{k}={v}" for k, v in custom_changes.items()))
        self._theme_mode = theme_mode
        self._hidden_buttons = set(self._settings_hidden)
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
        if "material" in custom_changes:
            # 材质在拖动/选择时已实时预览过，这里按**落盘后的值**再走一次正式入口
            self._apply_material(customization.get("material"))
        # 主题切换走**过渡**入口（自绘面颜色渐变，见 _animate_theme）；
        # _apply_theme 本身仍是同步的，直接调它不经过渡（自测要的就是这个）。
        if theme_mode == "system":
            self._animate_theme(detect_system_scheme())
        else:
            self._animate_theme(theme_mode)
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
        if any(k in custom_changes for k in ("module_count", "cells_per_module",
                                             "temps_per_module")):
            # 决策-3：排版配置改完，**已显示的旧文本**也要按新网格重排（滚动位置、
            # 光标/选中区、暂停态都保留，见 _relayout_rx_document）
            if self._relayout_rx_document():
                app_log.info("接收区已按新排版配置重排")
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
        elif key == "anim_fps":
            # 帧率**实时生效**：拖滑杆当场就能感觉到顺滑度变化（用户点名要
            # "无级调节"）。⛔ 只改每帧间隔，动画时长不变（见 ui_anim.set_frame_hz）。
            ui_anim.set_frame_hz(int(value))
        elif key == "material":
            # 换材质**当场**生效（用户点名"实时生效、不用重启"）
            self._apply_material(str(value))

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
        self._caption_bar.update()      # 标题栏是自绘面，换肤/换材质后也要重绘
        self.update()

    def _apply_material(self, name: str) -> None:
        """切换界面材质（毛玻璃 / 液态玻璃）并立刻重绘所有自绘面。

        材质只是绘制时换一条渲染路径（见 ``ui_surface.paint_glass``），和强度一样
        是绘制时现取的，所以重绘即可 —— 切换后**不需要重启**，卡片/页头/回放条
        当场换材质。设置面板里换材质走的是同一个入口（实时预览）。
        """
        set_material(name)
        # 切到毛玻璃、且强度 > 0 时颗粒贴图可能还没烘过（启动即液态玻璃、之后
        # 又切回毛玻璃就会这样）—— 补烘一次，别让第一帧卡在切换里。
        if material() != "liquid" and frost_gain() > 0.0:
            frost_tile(current_scheme(), self.devicePixelRatioF())
        for card in self._cards:
            card.update()
        self.brand_header.update()
        self._caption_bar.update()      # 标题栏是自绘面，换肤/换材质后也要重绘
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
        # 的上限一并定下来（下面的 setMaximumBlockCount 是无信号时的兜底）。
        # 本地留存行数也在 _on_rx_lines_changed 里同步（无信号时这里补一次）。
        self.cmb_rx_lines.setEditText(str(self._rx_blocks))
        self.txt_rx.setMaximumBlockCount(self._rx_blocks)
        import rx_history
        rx_history.set_max_lines(self._rx_blocks)
        # 毛玻璃：默认档现在是 0（不要磨砂）。历史默认值（68 / 100）几乎不可能是
        # 用户特意调的 —— 何况 glass 每次关窗都会把当前值写盘，"值等于旧默认"根本
        # 说明不了用户动没动过滑杆。所以判据是**用户有没有在设置面板里选过**：
        # 没选过 → 一律迁到当前默认；选过 → 哪怕是 100 这个旧默认值也照样尊重。
        stored_glass = settings.value("glass", None)
        glass_chosen = settings.value("glass_chosen", False, type=bool)
        if stored_glass is None or (not glass_chosen and int(stored_glass) in LEGACY_GLASS):
            stored_glass = DEFAULT_GLASS
        set_glass_strength(int(stored_glass))
        # 界面材质（毛玻璃 / 液态玻璃）走定制注册表 —— 老配置没这个键就取注册默认
        # （毛玻璃），用户存过的值原样尊重。
        set_material(str(customization.get("material")))
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

        # 连接方式（2026-10-14 加的蓝牙广播）。放在这里恢复：_build_ui 已经跑完，
        # 切模式要用到的控件都在；设成广播模式会顺手扫一次设备，用户打开就能选。
        self._ble_target = str(settings.value("ble_target", "") or "")
        if str(settings.value("link_mode", "serial")) == "ble":
            self.cmb_link.setCurrentText(LINK_BLE)
        else:
            self.cmb_link.setCurrentText(LINK_SERIAL)

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
        # ⛔ geometry 可能算出 None（开局就全屏、从没当过普通窗口 →
        #    _normal_geometry 还是初始值）。这时**不能** setValue(None)：
        #    那会把注册表里本来好好的几何覆盖成 "@Invalid()"（或删掉键），
        #    下次非全屏启动就回到默认尺寸 —— 用户 2026-10-17 实际中招。
        #    没有可存的值就**跳过**，保住上次存下来的。
        if geometry is not None:
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
        # 蓝牙列表扫描器也停掉（它和"连接"无关，主开关管不到它）
        if self._ble_scanner is not None:
            self._finish_ble_scan(fill=False)
        self._save_settings()
        if self._worker is not None:
            worker, self._worker = self._worker, None
            worker.stop()
        super().closeEvent(event)



