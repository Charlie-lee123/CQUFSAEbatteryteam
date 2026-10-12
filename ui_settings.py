"""设置面板。

- **主题模式**：界面默认跟随 Windows，但有时需要固定成某一套（晚上用深色、
  录屏演示时固定浅色）。这是这里最有价值的一项，也是「设置」按钮该有的第一件事。
- **毛玻璃强度**：无级滑杆，同时改模糊半径和面板透明度 —— 往右越糊、越透。
- **界面按钮**：逐个决定哪些**带文字的**控件留在界面上（开关、以及"编码""历史"
  这类标签 + 下拉框的一整行）。

不加用不到的选项 —— 设置项一多，面板本身就成了负担。
「接收区保留行数」原本在这里，后来挪到了右侧「串口配置」栏：它和波特率、校验位
一样是"整机参数"，放在参数栏里跟其它配置挨着更顺手，设置面板只留和外观有关的项。

「界面按钮」这一项的清单由调用方给（``button_specs``），面板自己不认识主窗口。
这样面板能单独构造和测试，也不用跟着主窗口的控件增删一起改。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtNetwork import (
    QNetworkAccessManager, QNetworkReply, QNetworkRequest,
)
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QMessageBox, QScrollArea, QVBoxLayout, QWidget,
)

import app_log
import customization
from ui_theme import apply_titlebar_theme
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtCore import QUrl
from ui_controls import (
    ChevronComboBox, ChevronSpinBox, GlassSlider, IconButton, InkButton, Switch,
)
from ui_theme import (
    DEFAULT_GLASS, GAP, GLYPH_REFRESH, detect_system_scheme, mono_font, qcolor,
    rx_accent, stamp_accent, ui_font,
)

# (显示名, 存进配置的值)
THEME_MODES = (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark"))
# 「定制功能」里那一列数字框的统一宽度。宽到装得下最长的一条（"10000 mV" +
# 左右内边距 + 右侧 chevron），又不至于让 "7" 这种一位数拖出一大片空框。
SPIN_COLUMN_W = 158
# 按钮开关排几列。三列是为了"一屏放完"：
# 两列时最后一行会被滚动区截掉半颗开关，看着像画坏了。
BUTTON_COLUMNS = 3


class SettingsDialog(QDialog):
    """应用设置。确定后由调用方读 values() / hidden_buttons() 落盘并生效。

    ``on_glass`` / ``on_buttons`` / ``on_custom`` 是拖动滑杆、拨动开关时的
    实时回调 —— 面板是模态的，但主窗口仍然能重绘，所以背后的界面会立刻跟着
    变，所见即所得。取消时调用方负责回退。
    ``on_custom(key, value)`` 是**注册表里滑杆**的通用实时回调（值已按 scale
    换算成显示值）；面板不认识任何具体配置项，要不要实时生效由主窗口决定。
    """

    def __init__(self, theme_mode: str, glass: int = DEFAULT_GLASS,
                 button_specs=(), hidden_buttons=(), on_glass=None, on_buttons=None,
                 parent: QWidget | None = None, on_custom=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.setMinimumWidth(500)
        self._on_glass = on_glass
        self._on_buttons = on_buttons
        self._on_custom = on_custom
        self._button_switches: dict[str, Switch] = {}
        # 标题栏主题跟着本次打开的方案走（跟随系统则当场解析），showEvent 里应用
        self._scheme = (detect_system_scheme() if theme_mode == "system"
                        else theme_mode)

        root = QVBoxLayout(self)
        root.setContentsMargins(GAP + 6, GAP + 6, GAP + 6, GAP + 6)
        root.setSpacing(GAP)

        # 整个设置内容放进**一条**滚动区：面板矮屏放不下时整体滚动，
        # 不再是只有按钮清单自己滚（用户点名"统一的滚动条"）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 视口透明由全局 QSS（QScrollArea > QWidget > QWidget）负责，这里只放内容
        body = QWidget()
        body_layout = QVBoxLayout(body)
        # ⚠️ 右侧必须给滚动条留出空隙（12px）：widgetResizable 会把 body 拉到
        # 视口宽，字段（开关/下拉框）跟着拉伸到最右 —— 滑轨就贴着内容边缘画，
        # 看起来是"滑轨压住内容"（用户报的"滑轨与内容区域重叠干涉"）。
        # 实测字段右缘与滚动条左缘间隙为 0，留出 12px 后各归其位。
        body_layout.setContentsMargins(0, 0, 12, 0)
        body_layout.setSpacing(GAP)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        # ⚠️ 整个面板**共用一张表单**。以前是「主题 / 毛玻璃」一张、下面每个分节
        # 再各起一张，而 QFormLayout 的标签列宽是按**本表内**最宽的标签算出来的
        # —— 几张表的字段左边缘于是落在几个不同的 x 上（真机实测 127 / 136 / 153），
        # 用户看到的"整体不整齐"就是它。合成一张之后所有字段共用同一个左边缘；
        # 分节只靠「跨两列的小标题」区分。
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        self.form = form
        body_layout.addLayout(form)

        self.cmb_theme = ChevronComboBox()
        for label, value in THEME_MODES:
            self.cmb_theme.addItem(label, value)
        index = self.cmb_theme.findData(theme_mode)
        self.cmb_theme.setCurrentIndex(index if index >= 0 else 0)
        form.addRow(self._label("主题"), self.cmb_theme)

        # 毛玻璃强度：0~100 无级。左端几乎不透明、不糊；右端最糊、最透。
        self.slider_glass = GlassSlider(Qt.Orientation.Horizontal)
        self.slider_glass.setRange(0, 100)
        self.slider_glass.setValue(max(0, min(100, int(glass))))
        self.slider_glass.setMinimumWidth(190)
        self.slider_glass.setToolTip("拖动即可实时调整整个界面的磨砂玻璃强度")
        self.lbl_glass = QLabel()
        self.lbl_glass.setFont(ui_font(11.5))
        self.lbl_glass.setMinimumWidth(40)
        self.lbl_glass.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._refresh_glass_label(self.slider_glass.value())
        self.slider_glass.valueChanged.connect(self._on_glass_changed)

        glass_row = QHBoxLayout()
        glass_row.setSpacing(10)
        glass_row.addWidget(self.slider_glass, 1)
        glass_row.addWidget(self.lbl_glass)
        form.addRow(self._label("毛玻璃强度"), self._row(glass_row))

        # 「定制功能」：BMS 专用配置的统一管理入口。控件**按注册表动态生成**
        # （customization.specs()），以后加定制配置这里一行都不用改。
        self._build_custom_section(form)

        if button_specs:
            self._build_button_section(form, button_specs, set(hidden_buttons))

        # 日志一节放在**界面按钮的下方**（用户点名），整段一起随面板滚动
        self._build_log_section(form)

        body_layout.addStretch(1)

        # 版本号：面板最底部**居中的一行小灰字**，右边跟「检查更新」按钮。
        # 取自 version.py（唯一出处）。不做成表单里的一节 —— 那样它会跟设置项
        # 抢视觉、看着像多了一个配置分类（用户反馈"显示位置有点奇怪"）；
        # 沉到滚动区末尾居中，是常见桌面软件的惯例位置。
        from version import VERSION
        version_row = QHBoxLayout()
        version_row.setSpacing(10)
        version_row.addStretch(1)
        self.lbl_version = QLabel(f"CQUFSAE 串口调试助手 v{VERSION}")
        self.lbl_version.setFont(ui_font(10.5))
        self.lbl_version.setObjectName("groupCaption")
        version_row.addWidget(self.lbl_version)
        self.btn_check_update = InkButton("检查更新", variant="ghost")
        self.btn_check_update.clicked.connect(self._check_update)
        version_row.addWidget(self.btn_check_update)
        version_row.addStretch(1)
        body_layout.addLayout(version_row)

        # 仓库地址就挂在版本行正下方：点一下进 GitHub（用户点名要附上）。
        from version import REPO_URL
        repo_label = QLabel(
            f'<a href="{REPO_URL}">github.com/Charlie-lee123/CQUFSAEbatteryteam</a>')
        repo_label.setFont(ui_font(10.5))
        repo_label.setObjectName("groupCaption")
        repo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 链接默认在 QLabel 里是"看着能点其实不动"——必须开 openExternalLinks
        # 才会交给系统浏览器打开。
        repo_label.setOpenExternalLinks(True)
        body_layout.addWidget(repo_label)

        # 检查更新的网络会话：复用同一个 manager（QNetworkAccessManager 应当
        # 常驻而不是每次现建）。超时兜底 8 秒 —— GitHub 连不上（代理没开、
        # 内网）时不能让按钮永远停在"检查中…"。
        self._nam = QNetworkAccessManager(self)
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(8000)
        self._update_timer.timeout.connect(self._update_timed_out)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = InkButton("取消", variant="ghost")
        cancel.clicked.connect(self.reject)
        confirm = InkButton("确定", variant="primary")
        confirm.clicked.connect(self.accept)
        confirm.setDefault(True)
        buttons.addWidget(cancel)
        buttons.addWidget(confirm)
        root.addLayout(buttons)

    # ------------------------------------------------------------ 定制功能

    def _build_custom_section(self, form: QFormLayout) -> None:
        """「定制功能」一节：控件全部按 customization 的注册表生成。

        面板本身不认识任何具体定制项 —— 加配置只要在注册表 register 一行。
        确定时的取值走 ``custom_values()``，由主窗口统一落盘。

        每一行都是同一个形状：**左边行标签 + 右边控件**，行的左边缘与前两节
        （主题 / 毛玻璃强度）严格对齐。
        """
        specs = customization.specs()
        if not specs:
            return
        form.addRow(self._caption("定制功能"))

        self._custom_widgets: dict[str, QWidget] = {}
        # 滑杆型配置的预览标签（"拖动即时看效果"的那种），key -> QLabel
        self._custom_previews: dict[str, QLabel] = {}
        for spec in specs:
            preview = None
            if spec["kind"] == "bool":
                # 开关**自己不带文字**：名字交给左侧的行标签。以前开关带文字、
                # 左侧塞一个全角空格当占位，结果开关起点跟同一列里的数字框差
                # 9px（那是包装布局的样式默认边距，见 _row 的说明）——
                # 现在开关的轨道和数字框的左边缘落在同一条竖线上。
                toggle = Switch("")
                toggle.setFont(ui_font(11.0))
                toggle.setChecked(bool(customization.get(spec["key"])))
                # 无障碍：这枚开关自己不带文字（名字在左侧行标签上），读屏
                # 只看到一个无名开关 —— 显式把行标签与说明交给它。
                toggle.set_accessible_label(spec["label"], spec.get("tip", ""))
                if spec["tip"]:
                    toggle.setToolTip(spec["tip"])
                field: QWidget = toggle
                widget = toggle
                # ⚠️ 行标签**不再**转发点击（2026-10-12 用户点名："点击同一高度的
                # 位置拨动开关就会被触发，这样不行，要点击拨动开关内的地方才
                # 触发"）。早先为了让"顺手点文字"也有反应，给标签挂了转发 ——
                # 结果整行同高的文字区都成了热区，比轨道本身大好几倍，误触极多。
                # 现在只有开关自己的命中区（= 自绘轨道，见 Switch.hitButton）才算。
                row_label = self._label(spec["label"])
            elif spec.get("widget") == "slider":
                # 滑动**无级**调节（用户点名）。取值类型仍是 int（见 customization
                # 的 register_slider），只是控件换成滑杆 + 右侧数值标签；要表达
                # 小数时按 spec["scale"] 换算（字号存的是半磅）。
                slider = GlassSlider(Qt.Orientation.Horizontal)
                slider.setRange(spec["minimum"], spec["maximum"])
                slider.setValue(int(customization.get(spec["key"])))
                slider.setMinimumWidth(190)
                if spec["tip"]:
                    slider.setToolTip(spec["tip"])
                value_label = QLabel()
                value_label.setFont(ui_font(11.5))
                value_label.setMinimumWidth(56)
                value_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                         | Qt.AlignmentFlag.AlignVCenter)
                self._refresh_slider_label(spec, slider.value(), value_label)
                slider.valueChanged.connect(
                    lambda value, s=spec, lab=value_label:
                    self._on_slider_changed(s, value, lab))
                slider_row = QHBoxLayout()
                slider_row.setSpacing(10)
                slider_row.addWidget(slider, 1)
                slider_row.addWidget(value_label)
                # 「复原」：一键回到注册表里的默认值。setValue 会触发
                # valueChanged，数值标签 / 预览 / 主窗口的实时预览全部跟着走。
                # 用**图标**（环形箭头 = 重置的通用符号，用户点名"还原按键用图标"）
                # —— 图标按钮必须带 tooltip 与读屏名称，否则只能靠猜。
                reset_btn = IconButton(GLYPH_REFRESH, "恢复默认值", size=24,
                                       glyph_size=13)
                reset_btn.setAccessibleName("恢复默认值")
                reset_btn.clicked.connect(
                    lambda _=False, s=spec, sl=slider: sl.setValue(int(s["default"])))
                slider_row.addWidget(reset_btn)
                field = self._row(slider_row)
                widget = slider
                row_label = self._label(spec["label"])
                if spec.get("preview"):
                    preview = self._build_slider_preview(spec, slider)
            else:
                spin = ChevronSpinBox()
                spin.setRange(spec["minimum"], spec["maximum"])
                spin.setValue(int(customization.get(spec["key"])))
                if spec.get("suffix"):
                    spin.setSuffix(spec["suffix"])
                if spec["tip"]:
                    spin.setToolTip(spec["tip"])
                # 一列数字框**共用一个宽度** —— 宽窄不一最显乱（"7" 与
                # "10000 mV" 各占一块，右边缘参差不齐）。
                spin.setFixedWidth(SPIN_COLUMN_W)
                field = spin
                widget = spin
                row_label = self._label(spec["label"])
            # 记**输入控件本身**（不是包装容器）：custom_values() 要在它身上取值
            self._custom_widgets[spec["key"]] = widget
            form.addRow(row_label, field)
            if preview is not None:
                # 预览行整行跨两列，紧贴在它那根滑杆下面
                form.addRow(preview)

    @staticmethod
    def _slider_display_value(spec: dict, raw: int) -> float:
        """滑杆的**显示值** = 存储值 × scale（字号是半磅存储 → 显示 pt）。"""
        return raw * float(spec.get("scale", 1.0))

    def _refresh_slider_label(self, spec: dict, raw: int, label: QLabel) -> None:
        value = self._slider_display_value(spec, raw)
        label.setText(f"{value:g}{spec.get('suffix', '')}")

    def _on_slider_changed(self, spec: dict, raw: int, label: QLabel) -> None:
        """滑杆动了：更新数值标签与预览字号，并把显示值交给主窗口做实时预览。"""
        self._refresh_slider_label(spec, raw, label)
        value = self._slider_display_value(spec, raw)
        preview = self._custom_previews.get(spec["key"])
        if preview is not None:
            preview.setFont(mono_font(value))
            # 预览的逐字符配色取主题 token，重设一遍确保换主题后颜色跟上
            preview.setText(self._preview_html(spec, raw))
        if self._on_custom is not None:
            self._on_custom(spec["key"], value)

    @classmethod
    def _preview_mark_bg(cls, color: str) -> str:
        """高亮底色：把文字色与**面板底色**按 3:7 混出来的淡色。

        用不透明的混色而不是 CSS 的 rgba —— Qt 富文本对 alpha 的支持参差，
        混出来的实色任何渲染路径都是同一个样子。视觉上就是"这支笔的淡色荧光"。
        """
        ink_color = QColor(color)
        panel = qcolor("surface")
        mixed = QColor(
            round(ink_color.red() * 0.3 + panel.red() * 0.7),
            round(ink_color.green() * 0.3 + panel.green() * 0.7),
            round(ink_color.blue() * 0.3 + panel.blue() * 0.7),
        )
        return mixed.name()

    @classmethod
    def _preview_html(cls, spec: dict, raw: int) -> str:
        """预览文字的**逐字符配色**（用户 2026-10-12 点名，含同日纠正）。

        CQUFSAE 六个字母 = 电压超限红 / BMS 橙 / 温度黄 / 欠压绿 / 序号蓝 /
        时间戳紫，全取**主题 token** —— 换主题时和接收区一起变；**"E."（E 和
        点）都是序号蓝且带高亮**（用户二次纠正：是这两个字）。".2026" 里
        前一个 2 标红、0 标橙、后一个 2 用**主题正文色**（都带高亮），6 用
        **主题反色**（正文字色的反转、不上底）—— 2 和 6 的归属是用户纠正过
        的，别再对调。
        """
        high = qcolor("volt_high").name()      # 红
        orange = qcolor("bms_error").name()    # 橙
        yellow = qcolor("temp_warn").name()    # 黄
        green = qcolor("volt_low").name()      # 绿
        blue = rx_accent().name()              # 蓝
        purple = stamp_accent().name()         # 紫
        body = qcolor("ink").name()            # 正文
        inverted = QColor(255 - qcolor("ink").red(),
                          255 - qcolor("ink").green(),
                          255 - qcolor("ink").blue()).name()

        def span(ch: str, color: str, mark: bool = False) -> str:
            if mark:
                return (f'<span style="color:{color};'
                        f'background-color:{cls._preview_mark_bg(color)}">{ch}</span>')
            return f'<span style="color:{color}">{ch}</span>'

        return "".join((
            span("C", high), span("Q", orange), span("U", yellow),
            span("F", green), span("S", blue), span("A", purple),
            # "E."（E 和点，用户 2026-10-12 二次纠正：是这两个字都要高亮）
            span("E", blue, mark=True),
            span(".", blue, mark=True),
            span("2", high, mark=True),      # 2026 的前一个 2：红 + 高亮
            span("0", orange, mark=True),    # 0：橙 + 高亮
            span("2", body, mark=True),      # 后一个 2：主题颜色 + 高亮
            span("6", inverted),             # 6：主题反色（2026-10-12 用户纠正：2 和 6 反了）
        ))

    def _build_slider_preview(self, spec: dict, slider: GlassSlider) -> QLabel:
        """滑杆下方的预览行。

        字号预览用**等宽字体**（跟接收区同一个 mono_font），字号随滑杆当场变；
        颜色走富文本逐字符配色（见 ``_preview_html``），换主题后重新拉一遍
        HTML 就能跟上（``_on_slider_changed`` 里做了）。
        """
        label = QLabel(self._preview_html(spec, slider.value()))
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setFont(mono_font(self._slider_display_value(spec, slider.value())))
        # 上 1 下 1：这个标签的行高贴着文字，默认（上0下2）会把墨迹顶到上边缘、
        # 看着偏高 —— 上下各留 1px 缓冲，总高不变（布局不受影响）。
        label.setContentsMargins(2, 1, 0, 1)
        self._custom_previews[spec["key"]] = label
        return label

    def custom_values(self) -> dict:
        """「定制功能」区里被**改动过**的项 {key: value}，由主窗口统一落盘。"""
        changes: dict = {}
        for spec in customization.specs():
            widget = self._custom_widgets.get(spec["key"])
            if widget is None:
                continue
            if spec["kind"] == "bool":
                value = widget.isChecked()
            else:
                value = int(widget.value())
            if value != customization.get(spec["key"]):
                changes[spec["key"]] = value
        return changes

    # ------------------------------------------------------------ 日志

    def _build_log_section(self, form: QFormLayout) -> None:
        """「日志」一行：显示日志文件位置 + 打开文件夹 + **一键导出**。给维护和迭代用。"""
        log_path = app_log.path()
        if not log_path:
            return
        form.addRow(self._caption("日志"))

        row = QHBoxLayout()
        row.setSpacing(10)
        path_label = QLabel(self._elide_middle(log_path))
        path_label.setFont(ui_font(10.5))
        path_label.setObjectName("groupCaption")
        # ⚠️ 必须 setMinimumWidth(**1**)，不能是 0：Qt 布局算最小宽度时把 0 当成
        # "没设过"，会退回 minimumSizeHint —— 那正是整条绝对路径的宽度。日志行
        # 于是把面板撑得比视口还宽（实测 body 635 / 视口 530），而横向滚动条是
        # 关着的，右半边连按钮一起被裁掉。设成 1 布局才敢把它压窄。
        path_label.setMinimumWidth(1)
        # 完整路径放 tooltip 里（显示的是中间省略版）
        path_label.setToolTip(log_path)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(path_label, 1)
        # 一键导出（用户点名）：把日志另存到用户挑的位置，方便发给别人排查。
        self.btn_export_log = InkButton("导出日志", variant="ghost")
        self.btn_export_log.setToolTip("把日志文件另存为一份，方便发给别人排查问题")
        self.btn_export_log.clicked.connect(self._export_log)
        row.addWidget(self.btn_export_log)
        open_button = InkButton("打开文件夹", variant="ghost")
        open_button.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(Path(log_path).parent))))
        row.addWidget(open_button)
        form.addRow(self._row(row))

    @staticmethod
    def _elide_middle(text: str, limit: int = 52) -> str:
        """超长的绝对路径在**中间**省略一段（头尾都留着，一眼能认出来是哪个盘/哪个文件）。

        面板宽度有限，而横向滚动条是关着的 —— 整条路径铺开会把面板撑宽、
        右半边被裁；这里按字符数先收一道。
        """
        if len(text) <= limit:
            return text
        keep = (limit - 1) // 2
        return f"{text[:keep]}…{text[-(limit - 1 - keep):]}"

    def _export_log(self) -> None:
        """把日志文件复制一份到用户选定的位置。

        用**复制**而不是改日志输出位置：程序后续照常往原位置追加，导出的那份
        是按下按钮那一刻的快照。结果在按钮上原地显示（跟「检查更新」同一套
        手感）—— 只弹个框、按钮没反应的话，用户会以为没点中。
        """
        source = app_log.path()
        if not source:
            return
        src = Path(source)
        target, _ = QFileDialog.getSaveFileName(
            self, "导出日志", str(Path.home() / src.name),
            "日志文件 (*.log *.txt);;所有文件 (*)")
        if not target:
            return
        try:
            shutil.copyfile(src, target)
        except OSError as exc:
            app_log.error(f"导出日志失败: {exc}")
            self.btn_export_log.setText("导出失败")
            QTimer.singleShot(4000, self._restore_export_text)
            QMessageBox.warning(self, "导出日志", f"导出失败：\n{exc}")
            return
        app_log.info(f"日志已导出: {target}")
        self.btn_export_log.setText("✓ 已导出")
        QTimer.singleShot(4000, self._restore_export_text)

    def _restore_export_text(self) -> None:
        self.btn_export_log.setText("导出日志")

    # ------------------------------------------------------------ 按钮显隐

    def _build_button_section(self, form: QFormLayout, specs, hidden: set[str]) -> None:
        form.addRow(self._caption("界面按钮"))

        # 按分组归拢，保持 specs 给的顺序（dict 自 Python 3.7 起保序）
        groups: dict[str, list[tuple[str, str]]] = {}
        for key, label, group in specs:
            groups.setdefault(group, []).append((key, label))

        body = QWidget()
        body.setAutoFillBackground(False)
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(9)

        row = 0
        for group, items in groups.items():
            tag = QLabel(group)
            tag.setObjectName("groupCaption")
            tag.setFont(ui_font(10.5))
            grid.addWidget(tag, row, 0, 1, BUTTON_COLUMNS)
            row += 1
            for index, (key, label) in enumerate(items):
                toggle = Switch(label)
                toggle.setFont(ui_font(11.0))
                toggle.setChecked(key not in hidden)
                toggle.toggled.connect(self._on_buttons_changed)
                self._button_switches[key] = toggle
                grid.addWidget(toggle, row + index // BUTTON_COLUMNS,
                               index % BUTTON_COLUMNS)
            row += (len(items) + BUTTON_COLUMNS - 1) // BUTTON_COLUMNS
        grid.setColumnStretch(BUTTON_COLUMNS, 1)    # 开关都靠左，右边留白

        # 原来这里有一条按钮清单专用的滚动区；现在整个面板共用一条滚动条，
        # 网格直接跨两列铺进表单（用户点名"移除原先单独为界面按钮设置的滑轨"）。
        form.addRow(body)

        hint = QLabel("只列出带文字的按钮和下拉框（编码、历史等）；"
                      "纯图标按钮（打开串口、发送等）保持显示，左下角的设置按钮固定显示")
        hint.setFont(ui_font(10.5))
        hint.setWordWrap(True)
        form.addRow(hint)

    def _on_buttons_changed(self, _checked: bool) -> None:
        if self._on_buttons is not None:
            self._on_buttons(self.hidden_buttons())

    def hidden_buttons(self) -> list[str]:
        """被关掉的按钮键名。顺序跟着 button_specs 走，便于比较。"""
        return [key for key, toggle in self._button_switches.items()
                if not toggle.isChecked()]

    # ------------------------------------------------------------ 检查更新

    # 本机 GitHub 直连通常不通（要靠 Clash）：第一遍走系统代理失败后，
    # 第二遍显式走本机 Clash 常用端口再试一次，还不行才报失败。
    FALLBACK_PROXY = ("127.0.0.1", 7897)

    @staticmethod
    def _latest_tag_version(tags) -> tuple | None:
        """从 GitHub tags 接口的 JSON 里解析出最新版本三元组。

        返回统一补齐到三段的 (major, minor, patch)（"v1.2" 视作 1.2.0）；
        没有任何合法版本 tag 时返回 None。**长度必须补齐再比** —— 否则
        远端 v1.2 的 (1,2,0) 会"大于"本地 1.2 的 (1,2)，把当前版本误判成
        有更新（实测踩过：v1.2 发出去当天检查更新就提示有新版）。
        """
        import re
        latest = None
        for item in tags if isinstance(tags, list) else []:
            m = re.fullmatch(r"v(\d+)\.(\d+)(?:\.(\d+))?",
                             str(item.get("name", "")))
            if not m:
                continue
            version = tuple(int(g or 0) for g in m.groups())
            if latest is None or version > latest:
                latest = version
        return latest

    @staticmethod
    def _local_version() -> tuple:
        """本地版本号，同样补齐到三段，跟远端 tag 的口径一致。"""
        from version import VERSION
        parts = tuple(int(p) for p in VERSION.split("."))
        return parts + (0,) * (3 - len(parts))

    def _check_update(self) -> None:
        """拉 GitHub 上的标签列表，找出最新版本 tag 跟本地 VERSION 比对。

        tag 形如 v1.1 / v1.0，按数值比大小（不是字符串序 —— v1.10 得排在
        v1.9 后面），双方都补齐到三段再比。比本地新 → 打开仓库主页；
        相同或更旧 → 提示已最新；网络失败/超时 → 提示并复原按钮，绝不静默。
        """
        self._proxy_attempt = 0
        self._start_update_request()

    def _start_update_request(self) -> None:
        if self._proxy_attempt == 1:
            from PySide6.QtNetwork import QNetworkProxy
            QNetworkProxy.setApplicationProxy(QNetworkProxy(
                QNetworkProxy.ProxyType.HttpProxy, *self.FALLBACK_PROXY))
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText("检查中…")
        from version import REPO_API_TAGS
        self._pending_reply = self._nam.get(QNetworkRequest(QUrl(REPO_API_TAGS)))
        self._pending_reply.finished.connect(self._on_update_reply)
        self._update_timer.start()

    def _on_update_reply(self) -> None:
        reply = self.sender()
        if reply is not getattr(self, "_pending_reply", None):
            return                      # 超时后迟到的回应，忽略
        self._update_timer.stop()
        error = reply.error()
        reply.deleteLater()
        if error != QNetworkReply.NetworkError.NoError:
            if self._proxy_attempt == 0:
                # 直连不通：换本机代理再试一遍
                self._proxy_attempt = 1
                self._start_update_request()
                return
            self._finish_update_check()
            self._update_failed()
            return
        self._finish_update_check()
        try:
            import json
            tags = json.loads(bytes(reply.readAll()))
            from version import VERSION
            latest = self._latest_tag_version(tags)
            if latest is None:
                # 仓库还没有任何版本 tag（比如刚建仓）
                self.lbl_version.setText(
                    f"当前版本 v{VERSION}（仓库还没有发布过版本）")
                return
            if latest <= self._local_version():
                self.lbl_version.setText(f"已是最新版本 v{VERSION}")
                self._flash_button_result("✓ 已是最新")
                return
            latest_str = ".".join(str(p) for p in latest)
            # 有新版本：打开仓库主页（用户指定 —— releases/latest 在没建过
            # Release 的仓库上是 404，不能用）
            from version import REPO_URL
            self.lbl_version.setText(f"发现新版本 v{latest_str}，已打开仓库主页")
            self._flash_button_result(f"发现 v{latest_str}！")
            QDesktopServices.openUrl(QUrl(REPO_URL))
        except Exception:
            # 兜底：任何解析/处理异常都不能变成"点了没反应"（实测 NameError
            # 被槽函数吞掉，用户看到的就是检查一秒结束、毫无反馈）
            import traceback
            app_log.error("检查更新处理异常: "
                          + traceback.format_exc(limit=3))
            self.lbl_version.setText("检查失败：更新信息解析出错")

    def _update_timed_out(self) -> None:
        if getattr(self, "_pending_reply", None) is None:
            return
        self._pending_reply.disconnect(self)
        self._pending_reply.abort()
        self._pending_reply = None
        self._finish_update_check()
        self._update_failed()

    def _update_failed(self) -> None:
        self.lbl_version.setText("检查失败：连不上 GitHub（代理开了吗？）")
        self._flash_button_result("网络失败")

    def _flash_button_result(self, text: str) -> None:
        """结果在**按钮上**原地显示 4 秒再复原。

        只改版本行那行小灰字用户根本注意不到（"点了没反应"的体感就是
        这么来的）—— 结果必须出现在手指按下过的地方。
        """
        self.btn_check_update.setText(text)
        QTimer.singleShot(4000, self._restore_button_text)

    def _restore_button_text(self) -> None:
        if self.btn_check_update.text() != "检查中…":
            self.btn_check_update.setText("检查更新")

    def _finish_update_check(self) -> None:
        self._pending_reply = None
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("检查更新")

    # ------------------------------------------------------------ 杂项

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 深色面板配系统白标题栏是全界面最扎眼的撞色（用户截图点名）——
        # 对话框也要走一遍 DWM 深色标题栏 + 圆角，和主窗口同一套处理
        apply_titlebar_theme(self, self._scheme)

    def _row(self, layout) -> QWidget:
        """把一个横向布局包成控件，当表单的"字段"用。

        ⚠️ 必须紧跟一句 ``setContentsMargins(0, 0, 0, 0)``：``QWidget.setLayout()``
        会把布局的内边距设成**样式默认值**（本机 9px），包出来的字段就整体右移
        9px，跟同一张表单里别的字段对不上（毛玻璃那一行的滑杆就是这么比主题
        下拉框多出 9px 的）。
        """
        holder = QWidget()
        holder.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return holder

    def _caption(self, text: str) -> QLabel:
        """分节小标题。跨两列，左边缘和左侧的行标签对齐；
        上面的留白靠 QLabel 自己的 contentsMargins 撑（QFormLayout 不支持
        "某两行之间多留一点"），让一节与一节之间有呼吸。"""
        label = QLabel(text)
        label.setFont(ui_font(12.5))
        label.setContentsMargins(0, 8, 0, 0)
        return label

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(ui_font(12.5))
        return label

    def _refresh_glass_label(self, value: int) -> None:
        self.lbl_glass.setText(f"{value}%")

    def _on_glass_changed(self, value: int) -> None:
        self._refresh_glass_label(value)
        if self._on_glass is not None:
            self._on_glass(int(value))

    def values(self) -> tuple[str, int]:
        """返回 (主题模式, 毛玻璃强度)。"""
        return (str(self.cmb_theme.currentData()), int(self.slider_glass.value()))
