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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea,
    QSlider, QVBoxLayout, QWidget,
)

import app_log
import customization
from ui_theme import apply_titlebar_theme
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from ui_controls import ChevronComboBox, ChevronSpinBox, InkButton, Switch
from ui_theme import DEFAULT_GLASS, GAP, detect_system_scheme, ui_font

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

    ``on_glass`` / ``on_buttons`` 是拖动滑杆、拨动开关时的实时回调 —— 面板是模态的，
    但主窗口仍然能重绘，所以背后的界面会立刻跟着变，所见即所得。取消时调用方负责回退。
    """

    def __init__(self, theme_mode: str, glass: int = DEFAULT_GLASS,
                 button_specs=(), hidden_buttons=(), on_glass=None, on_buttons=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.setMinimumWidth(500)
        self._on_glass = on_glass
        self._on_buttons = on_buttons
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
        self.slider_glass = QSlider(Qt.Orientation.Horizontal)
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
        for spec in specs:
            if spec["kind"] == "bool":
                # 开关**自己不带文字**：名字交给左侧的行标签。以前开关带文字、
                # 左侧塞一个全角空格当占位，结果开关起点跟同一列里的数字框差
                # 9px（那是包装布局的样式默认边距，见 _row 的说明）——
                # 现在开关的轨道和数字框的左边缘落在同一条竖线上。
                toggle = Switch("")
                toggle.setFont(ui_font(11.0))
                toggle.setChecked(bool(customization.get(spec["key"])))
                if spec["tip"]:
                    toggle.setToolTip(spec["tip"])
                field: QWidget = toggle
                widget = toggle
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
            # 记**输入控件本身**（不是包装容器）：custom_values() 要在它身上取值
            self._custom_widgets[spec["key"]] = widget
            form.addRow(self._label(spec["label"]), field)

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
        """「日志」一行：显示日志文件位置 + 打开文件夹。给维护和迭代用。"""
        log_path = app_log.path()
        if not log_path:
            return
        form.addRow(self._caption("日志"))

        row = QHBoxLayout()
        row.setSpacing(10)
        path_label = QLabel(log_path)
        path_label.setFont(ui_font(10.5))
        path_label.setObjectName("groupCaption")
        # 超长截中间：路径可能很长，别把面板撑破
        path_label.setMinimumWidth(0)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(path_label, 1)
        open_button = InkButton("打开文件夹", variant="ghost")
        open_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(
                str(__import__("pathlib").Path(log_path).parent))))
        row.addWidget(open_button)
        form.addRow(self._row(row))

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
