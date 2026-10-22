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
    QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QScrollArea, QVBoxLayout,
    QWidget,
)

import app_log
import customization
from ui_theme import apply_titlebar_theme
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QIntValidator
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
# 「模组 i」里两个"数量"输入框的宽度。**手动输入**（用户 2026-10-22 明确），
# 所以只要装得下 3 位数 + 内边距；左边写"电芯数量："、右边跟一个"个"。
MODULE_INPUT_W = 64


class _NameDialog(QDialog):
    """单行文本输入的小模态框（新建车辆 / 改名用）。

    为什么自己搭一个而不是 ``QInputDialog``：QInputDialog 在 Fusion 下出来是
    系统样式的白框，和这套黑白灰 + 自绘标题栏的面板放一起像另一个程序。这里
    沿用同一套积木（``InkButton`` 的 primary/ghost 两档、``GAP`` 边距、标题栏
    主题走 ``apply_titlebar_theme``），尺寸也与设置面板同源。
    """

    def __init__(self, title: str, value: str, scheme: str = "dark",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(380)
        self._scheme = scheme

        root = QVBoxLayout(self)
        root.setContentsMargins(GAP + 6, GAP + 6, GAP + 6, GAP + 6)
        root.setSpacing(GAP)

        self.edit = QLineEdit(value)
        self.edit.setFont(ui_font(12.5))
        self.edit.selectAll()               # 预填名整段选中：直接打字就是替换
        self.edit.returnPressed.connect(self.accept)
        root.addWidget(self.edit)

        row = QHBoxLayout()
        row.setSpacing(GAP)
        row.addStretch(1)
        cancel = InkButton("取消", variant="ghost")
        cancel.clicked.connect(self.reject)
        confirm = InkButton("确定", variant="primary")
        confirm.clicked.connect(self.accept)
        confirm.setDefault(True)
        row.addWidget(cancel)
        row.addWidget(confirm)
        root.addLayout(row)

    def value(self) -> str:
        """去掉首尾空白后的输入（面板只认这个名字，空串由调用方忽略）。"""
        return self.edit.text().strip()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 与设置面板同一套 DWM 深色标题栏 / 圆角（否则深色面板配白标题栏撞色）
        apply_titlebar_theme(self, self._scheme)
        self.edit.setFocus()



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
        # 最小宽度 580 → 540（2026-10-22 第五轮）：删掉数量框后面的量词"个"之后，
        # 逐模组那一行窄了 32px，滚动区正文的 sizeHint 从 519 降到 475。
        # 540 时视口 480 ≥ 475，整行仍放得下且不留大片空白。
        # （历史：500 → 524 → 580 都是被"右侧内容被视口裁掉"逼上去的，别随手调小。）
        self.setMinimumWidth(540)
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

        # 定制项的控件登记表要在**这一节之前**建好：「界面材质」虽然是
        # customization 注册表里的枚举项，但它的行跟着「材质强度」一起摆在上面
        # （见文件头说明），会先于 _build_custom_section 登记进这两张表。
        self._custom_widgets: dict[str, QWidget] = {}
        self._custom_rows: dict[str, tuple[QLabel, QWidget]] = {}

        # 材质强度：0~100 无级，**两种材质共用**这一根滑杆。
        # 0 = 清玻璃（几乎只剩一层很淡的底色），100 = 材质拉满（各自特征最明确）。
        # 毛玻璃动的是颗粒与透明度，液态玻璃动的是镜面高光 / 内发光 / 中心提亮。
        self.slider_glass = GlassSlider(Qt.Orientation.Horizontal)
        self.slider_glass.setRange(0, 100)
        self.slider_glass.setValue(max(0, min(100, int(glass))))
        self.slider_glass.setMinimumWidth(190)
        self.slider_glass.setToolTip(
            "拖动即可实时调整整个界面材质的强度（毛玻璃与液态玻璃共用）")
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
        form.addRow(self._label("材质强度"), self._row(glass_row))

        # 「界面材质」：从「定制功能」里提到这里，紧跟在「材质强度」**下面**
        # （用户 2026-10-22："把材质强度放到界面材质的上面"）。这两个是一对 ——
        # 一个选材质、一个调它的强度，中间隔着一整节车辆档案就说不通了。
        # 存储/默认值仍在 customization 的 register_choice("material")，
        # 这里只是把它的那一行提前生成（_build_custom_section 会跳过它）。
        material_spec = next((spec for spec in customization.specs()
                              if spec["key"] == "material"), None)
        if material_spec is not None:
            self.cmb_material = ChevronComboBox()
            for option_label, stored in material_spec["options"]:
                self.cmb_material.addItem(option_label, stored)
            material_index = self.cmb_material.findData(customization.get("material"))
            self.cmb_material.setCurrentIndex(material_index if material_index >= 0 else 0)
            if material_spec.get("tip"):
                self.cmb_material.setToolTip(material_spec["tip"])
            self.cmb_material.setMinimumWidth(190)
            self.cmb_material.currentIndexChanged.connect(
                lambda _idx, s=material_spec, c=self.cmb_material:
                self._on_choice_changed(s, c))
            material_label = self._label(material_spec["label"])
            form.addRow(material_label, self.cmb_material)
            self._custom_widgets["material"] = self.cmb_material
            self._custom_rows["material"] = (material_label, self.cmb_material)

        # 「车辆」：一套电池箱结构 = 一台车（模组个数 + 每个模组的电芯/温度采集数）。
        # 放在「定制功能」**之前**：选车是"我用的是哪台电池箱"这个前提，下面那些
        # 定制项是它的细节；而且选车一换，定制项里那三个旧的全局网格参数就整体
        # 隐藏（见 _sync_vehicle_rows）——
        #  ⚠️ 与 customize 的说明一致：车辆档案**不走 register_* 注册表**（它不是
        #     标量），所以这一节是手写的，其余定制项仍按 specs() 自动生成。
        self._build_vehicle_section(form)

        # 「定制功能」：BMS 专用配置的统一管理入口。控件**按注册表动态生成**
        # （customization.specs()），以后加定制配置这里一行都不用改。
        self._build_custom_section(form)
        # 车辆一节的**首次同步**必须等「定制功能」建完：选车后要隐藏旧的三件套
        # （module_count / cells_per_module / temps_per_module），那三颗控件是
        # 上面按注册表生成的（见 _build_custom_section 里的 _custom_rows）。
        self._sync_vehicle_rows()

        if button_specs:
            self._build_button_section(form, button_specs, set(hidden_buttons))

        # 日志一节放在**界面按钮的下方**（用户点名），整段一起随面板滚动
        self._build_log_section(form)

        # 「MCP Service」：把 MCP 服务交给 Agent 的入口（2.1 新增，2.2 起这么叫）
        self._build_mcp_section(form)

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
        # ⛔ 不直接接 reject/accept：QDialog.done() 会**当场 hide()**，收场动画
        #    （面板缩回设置按钮）就跑在已经隐藏的窗口上 —— 用户看到的是"面板突然
        #    消失"，根本没有"回按钮去"这一段。统一走 done()，由它决定是否延后
        #    真正关闭（见本类末尾的 done()）。
        cancel.clicked.connect(self._request_close)
        confirm = InkButton("确定", variant="primary")
        confirm.clicked.connect(self._request_close)
        confirm.setDefault(True)
        self.btn_confirm = confirm
        buttons.addWidget(cancel)
        buttons.addWidget(confirm)
        root.addLayout(buttons)

    def _request_close(self) -> None:
        """按钮要求关闭：确定/取消按发起者区分，其余（Esc / 窗口 X）算取消。"""
        sender = self.sender()
        accepted = sender is self.btn_confirm
        self.done(self.DialogCode.Accepted if accepted
                  else self.DialogCode.Rejected)

    def done(self, result: int) -> None:
        """关闭钩子：``_finish_hook`` 在场时**延后**真正关闭。

        主窗口（``_close_settings_dialog``）要先把面板缩回设置按钮再关，所以这里
        不直接调 ``super().done()``（那会立刻 hide）；只把结果转交出去。
        没有钩子时（自测直接构造面板）照旧立刻关闭。
        """
        hook = getattr(self, "_finish_hook", None)
        if hook is not None and self.isVisible():
            hook(bool(result))
            return
        super().done(result)

    # ------------------------------------------------------------ 车辆

    # 旧的全局网格三件套：选车后整行隐藏（一套参数说话，见 _sync_vehicle_rows）。
    # 名字与 customization 里那三条 register_int 一一对应。
    _LEGACY_GRID_KEYS = ("module_count", "cells_per_module", "temps_per_module")

    def _build_vehicle_section(self, form: QFormLayout) -> None:
        """「车辆」一节：选车 / 新建 / 删除 / 改名 + 逐模组的电芯与温度采集数。

        数据全在 ``customization`` 的两个键里（vehicles JSON + vehicle 当前名），
        面板只是它的编辑器。三条设计取舍：

        - **车辆档案是"改一下立刻落盘"的**，不像别的定制项那样攒到按确定：
          它同时驱动接收区重排（改一格就该看到效果），而"确定"是可取消的 ——
          让重排效果与磁盘状态在一个可取消的事务里，用户永远说不清自己看到的是
          哪一版。代价是取消面板也回不去（文档在 customization / 本节说明）。
        - **模组个数=0 或没选车时，逐模组行整段不生成**，退回「定制功能」里那三个
          全局参数；选车后那三行**整行隐藏**，避免两套参数同时存在互相打架。
        - 每一行都是"左侧行标签 + 右侧控件"，与上面几节共用同一张表单，左边缘
          因此严格对齐（QFormLayout 的标签列宽按全表最宽算）。
        """
        form.addRow(self._caption("车辆（电池箱）"))

        # ---- 选车行：**可编辑**下拉（就地改名）+ 新建 + 删除 ----
        self.cmb_vehicle = ChevronComboBox()
        # 用户 2026-10-22："有了车辆选项就不需要车辆名称了，选择的时候就需要可以
        # 编辑车辆名称" —— 选车与改名是同一件事的两半，合成一行：直接在框里改写
        # 就是给当前车辆改名（回车/失焦生效，重名或空名回滚）。
        self.cmb_vehicle.setEditable(True)
        self.cmb_vehicle.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # ⚠️ 最小宽度只给 110：这一行是「下拉 + 新建 + 删除」，下拉按 190 兜底会把
        #    整行撑到 370，body 就比滚动区视口宽 30 多 px —— 面板右侧被裁掉一条、
        #    横向还能偷偷滚（实测 body 532 / 视口 498）。下拉本来就有 stretch=1，
        #    宽度会吃掉整行的剩余空间，兜底给小一点反而不会顶破面板。
        self.cmb_vehicle.setMinimumWidth(110)
        self.cmb_vehicle.setToolTip(
            "一台车 = 一套电池箱结构（模组个数与每个模组的电芯/温度采集数）。\n"
            "直接改写框里的名字 = 给当前车辆改名（回车或点别处生效）；\n"
            "选「（未选择）」则退回「定制功能」里的三个全局参数")
        self.cmb_vehicle.currentIndexChanged.connect(self._on_vehicle_picked)
        _vehicle_line = self.cmb_vehicle.lineEdit()
        if _vehicle_line is not None:
            _vehicle_line.editingFinished.connect(self._on_vehicle_rename_typed)

        self.btn_vehicle_new = InkButton("新建", variant="ghost")
        self.btn_vehicle_new.setToolTip(
            "新建一台车：默认名「新车 N」，结构按当前配置预填"
            "（旧模组个数 >0 就用它，否则 3 模组 × 25 电芯 × 8 温度）")
        self.btn_vehicle_new.clicked.connect(self._on_vehicle_new)

        self.btn_vehicle_delete = InkButton("删除", variant="ghost")
        self.btn_vehicle_delete.setToolTip("删除当前这台车（只删结构，已接收的数据不受影响）")
        self.btn_vehicle_delete.clicked.connect(self._on_vehicle_delete)

        # 改名失败时的短提示：平时藏起来（不占宽度），出错那两秒才出现
        # ⚠️ 提示文字必须**短**（同 MCP 那行），长文案会被裁掉
        self.lbl_vehicle_hint = QLabel("")
        self.lbl_vehicle_hint.setFont(ui_font(10.5))
        self.lbl_vehicle_hint.setObjectName("groupCaption")
        self.lbl_vehicle_hint.setMinimumWidth(1)      # 不能是 0（同日志行，见上）
        self.lbl_vehicle_hint.setVisible(False)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(10)
        pick_row.addWidget(self.cmb_vehicle, 1)
        pick_row.addWidget(self.btn_vehicle_new)
        pick_row.addWidget(self.btn_vehicle_delete)
        pick_row.addWidget(self.lbl_vehicle_hint)

        # ---- 模组个数行：0 = 不配（退回旧的三个全局参数） ----
        self.spin_vehicle_modules = ChevronSpinBox()
        self.spin_vehicle_modules.setRange(0, 64)
        self.spin_vehicle_modules.setFixedWidth(SPIN_COLUMN_W)
        self.spin_vehicle_modules.setToolTip(
            "模组个数 = 下面「模组 i」的行数；新增的模组沿用最后一个模组的配置，"
            "缩短则从末尾截断")
        self.spin_vehicle_modules.valueChanged.connect(self._on_vehicle_modules_changed)

        self._vehicle_rows: dict[str, tuple[QLabel, QWidget]] = {
            "pick": (self._label("车辆"), self._row(pick_row)),
            "modules": (self._label("模组个数"), self.spin_vehicle_modules),
        }
        form.addRow(*self._vehicle_rows["pick"])
        form.addRow(*self._vehicle_rows["modules"])

        # 逐模组行插在这个位置（= 本节之后、「定制功能」之前）。
        # ⚠️ 用 insertRow/removeRow 按**行号**增删，不用容器套布局：套一层容器的话
        #    里面的标签会落到字段列那一列上，跟本节其它行标签错开一大截。
        self._module_row_base = form.rowCount()
        self._module_rows: list[tuple[QLabel, QWidget]] = []

    def _refresh_vehicle_combo(self) -> None:
        """按磁盘上的车辆列表重建下拉（当前项跟随 customization.current_vehicle）。"""
        names = customization.vehicle_names()
        current = customization.current_vehicle()
        combo = self.cmb_vehicle
        combo.blockSignals(True)          # 重建期间别触发 currentIndexChanged
        combo.clear()
        if not names or not current:
            # 没有车辆、或"有车但一台都没选"（刚删掉当前车）时给一个明确的空项 ——
            # 少了它，下拉会自己停在第一台车上，用户会以为已经选上了
            combo.addItem("（未选择）", "")
        for name in names:
            combo.addItem(name, name)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _sync_vehicle_rows(self) -> None:
        """按"当前有没有选车"整体摆一次：整行显隐 + 填值 + 重建逐模组行。

        这是本节**唯一**的刷新入口（选车 / 新建 / 删除 / 改名 / 改模组个数之后
        都调它）：分散着各改一半，最容易出现"下拉是新名字、数字框还是旧结构"。
        """
        current = customization.current_vehicle()
        profile = customization.active_profile()
        has_vehicle = bool(current)
        cells, temps = profile if profile else ([], [])

        self._refresh_vehicle_combo()

        # 选车行永远在；改名 / 模组个数两行只在选了车时才有意义。
        self._vehicle_rows["pick"][0].setVisible(True)
        self._vehicle_rows["pick"][1].setVisible(True)
        for key in ("modules",):
            label, field = self._vehicle_rows[key]
            label.setVisible(has_vehicle)
            field.setVisible(has_vehicle)
        self._sync_grid_params_visibility()

        self.btn_vehicle_delete.setEnabled(has_vehicle)
        spin = self.spin_vehicle_modules
        spin.blockSignals(True)
        # 没选车时这颗框**绑旧的 module_count**（老用户在同一位置看到同一个数，
        # 不会出现"设置里有两个模组个数、数值还不一样"）
        spin.setValue(len(cells) if has_vehicle
                      else int(customization.get("module_count")))
        spin.blockSignals(False)
        self._rebuild_module_rows(cells, temps)

    def _sync_grid_params_visibility(self) -> None:
        """网格参数二选一的**唯一判据**：接收区此刻真正吃哪一套，就只显示哪一套。

        判据直接取 ``customization.active_profile() is None`` —— 与主窗口
        ``_rx_grid_hints`` 完全同一条（不是"选没选车"）：选了车但模组个数还是 0
        时，主窗口走的就是旧的三件套，那面板也得把旧的三件套露出来，否则用户
        面对一个"选了车、却没有任何参数能调"的面板。
        ⚠️ 显隐要**连着行标签一起**设：只藏字段会在表单里留一个孤零零的文字。
        """
        legacy_on = customization.active_profile() is None
        for key in self._LEGACY_GRID_KEYS:
            pair = self._custom_rows.get(key)
            if pair is not None:
                for widget in pair:
                    widget.setVisible(legacy_on)

    def _make_count_field(self, value: int, prefix: str, tip: str, on_apply) -> QWidget:
        """一个"数量"输入框：**手动输入**（文本框 + 整数校验，回车/失焦生效）。

        用户 2026-10-22 两轮口径：先要"可以自定义数量"，再说"数量都需要手动输入"
        —— 所以不用下拉预置值、也不用只能按箭头的数字框，就是一个能直接敲的输入框
        （0~999，非法输入自动回滚）。

        用户 2026-10-22 晚定文案：一行读作「电芯数量：〔可填〕」——
        前缀（"电芯数量：" / "温度采集数量："）**在框左边**，数字就是框里那个
        自定义值。后来又说"全是个太难看了"，把框后面的量词"个"**删掉**了。
        """
        box = QLineEdit()
        # 挂 objectName 走 ui_theme 里的 QSS（QLineEdit#countEdit）：和下拉框/
        # 数字框同族外观（surface_2 底 + line 描边 + CONTROL_RADIUS 圆角）。
        # ⛔ 不能用裸 QLineEdit 规则——可编辑下拉框内部也藏着 QLineEdit。
        box.setObjectName("countEdit")
        box.setValidator(QIntValidator(0, 999, box))
        box.setText(str(int(value)))
        # 数字**居中**（用户 2026-10-22："数据要居中啊"）：框很窄，右对齐的话
        # 一位数的"8"会贴在右壁、左边空一大块，看着像没对齐。
        box.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        box.setFixedWidth(MODULE_INPUT_W)
        box.setToolTip(tip)

        def caption(text: str) -> QLabel:
            lab = QLabel(text)
            lab.setFont(ui_font(10.5))
            lab.setObjectName("groupCaption")
            # ⛔ 最小宽度必须钉住**文字宽度**：字段区不够宽时 QLayout 会从
            # min=1 的标签开始压，实测"个"被压到 2px（等于没画）、"："被挤没。
            # 钉住之后压不下去，行会反过来要求面板加宽（见 _build_vehicle_section
            # 里的面板最小宽），文字才完整。
            lab.setMinimumWidth(
                lab.fontMetrics().horizontalAdvance(text) + 2)
            return lab

        prefix_label = caption(prefix)

        # 回滚目标跟着"最近一次成功值"走：改了 24→30 之后又敲了非法内容，
        # 应当退回 30，而不是退回建行时的 24（否则用户会以为自己的修改丢了）。
        last_ok = [int(value)]

        def apply(box_widget: QLineEdit = box) -> None:
            raw = box_widget.text().strip()
            try:
                number = int(raw)
            except ValueError:
                box_widget.setText(str(last_ok[0]))      # 空/非法 → 回滚
                return
            number = max(0, min(999, number))
            if raw != str(number):
                box_widget.setText(str(number))          # "007" → "7"
            last_ok[0] = number
            on_apply(number)

        box.editingFinished.connect(apply)

        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(prefix_label)
        lay.addWidget(box)
        lay.addStretch(1)
        return holder

    def _rebuild_module_rows(self, cells: list[int], temps: list[int]) -> None:
        """重建「模组 1..N」逐行控件（每行：电芯数量：N 个 + 温度采集数量：N 个）。"""
        while self._module_rows:
            self._module_rows.pop()
            # 先 pop 再按"剩下的行数"算最后一行的行号：删一行后后面的行号整体上移
            self.form.removeRow(self._module_row_base + len(self._module_rows))
        for index in range(min(len(cells), len(temps))):
            field_cells = self._make_count_field(
                int(cells[index]), "电芯数量：",
                f"第 {index + 1} 个模组的电芯数量 → 电压段这一排放几个点"
                "（0 = 这排不排）；直接输入数字，回车或点别处生效",
                lambda number, i=index: self._on_module_value(i, False, number))
            field_temps = self._make_count_field(
                int(temps[index]), "温度采集数量：",
                f"第 {index + 1} 个模组的温度采集数量 → 温度段这一排放几个点"
                "（0 = 这排不排）；直接输入数字，回车或点别处生效",
                lambda number, i=index: self._on_module_value(i, True, number))

            row = QHBoxLayout()
            row.setSpacing(14)
            row.addWidget(field_cells)
            row.addWidget(field_temps)
            row.addStretch(1)
            label = self._label(f"模组 {index + 1}")
            field = self._row(row)
            self.form.insertRow(self._module_row_base + index, label, field)
            self._module_rows.append((label, field))

    def _on_module_value(self, index: int, is_temp: bool, value: int) -> None:
        """某个模组的电芯/温度数改了：**立刻写回这台车**，并请主窗口重排。"""
        name = customization.current_vehicle()
        profile = customization.active_profile()
        if not name or profile is None:
            return
        cells, temps = list(profile[0]), list(profile[1])
        if not (0 <= index < len(cells) and index < len(temps)):
            return
        if is_temp:
            temps[index] = int(value)
        else:
            cells[index] = int(value)
        customization.save_vehicle(name, cells, temps)
        self._notify_vehicle_changed()

    def _on_vehicle_modules_changed(self, value: int) -> None:
        """模组个数改了：改**当前车辆的结构长度**（没选车时绑旧的 module_count）。

        增删规则（用户按了才看得见，所以要有确定的语义）：新增的模组沿用**最后
        一个模组**的配置（没有就默认 25 电芯 / 8 温度）；缩短从末尾截断 —— 与
        "一排 = 一个模组"的下标语义一致，改第 1 个模组不会因为改了总数而错位。
        """
        count = max(0, min(64, int(value)))
        name = customization.current_vehicle()
        if not name:
            legacy = self._custom_widgets.get("module_count")
            if legacy is not None:
                legacy.setValue(count)
            return
        profile = customization.active_profile()
        cells, temps = (list(profile[0]), list(profile[1])) if profile else ([], [])
        last_cell = cells[-1] if cells else customization.DEFAULT_CELLS
        last_temp = temps[-1] if temps else customization.DEFAULT_TEMPS
        cells = cells[:count] + [last_cell] * max(0, count - len(cells))
        temps = temps[:count] + [last_temp] * max(0, count - len(temps))
        customization.save_vehicle(name, cells, temps)
        # ⚠️ 这里不能用 _sync_vehicle_rows：那会把下拉框重建一遍，正在编辑的
        #    数字框会丢焦点（用户连点箭头就被打断）。只重建逐模组行 + 重摆
        #    "网格参数显隐"（模组个数从 0 变 1 或反过来时，旧三件套要跟着让位/露面）。
        self._rebuild_module_rows(cells, temps)
        self._sync_grid_params_visibility()
        self._notify_vehicle_changed()

    def _on_vehicle_picked(self, index: int) -> None:
        """下拉换了一台车（或换回「（未选择）」）。

        ⚠️ ``index < 0`` = 用户正在框里**改写名字**（可编辑下拉把 currentIndex
        打成 -1），那不是"换车"，改名交给 editingFinished 处理 —— 这里必须直接
        返回，否则输到一半就被当成"取消选车"，下拉内容会被清回（未选择）。
        """
        if index < 0:
            return
        name = str(self.cmb_vehicle.itemData(index) or "")
        if name == customization.current_vehicle():
            return
        customization.select_vehicle(name)
        self._sync_vehicle_rows()
        self._notify_vehicle_changed()

    def _on_vehicle_new(self) -> None:
        """新建车辆：弹一个同风格的单行输入框，确认后建好并自动选中。"""
        default = self._next_new_vehicle_name(customization.vehicle_names())
        dialog = _NameDialog("新建车辆", default, self._scheme, self)
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            name = dialog.value()
        finally:
            dialog.deleteLater()
        if not accepted or not name:
            return
        self._create_vehicle(name)

    def _create_vehicle(self, name: str) -> None:
        """按当前配置预填一台新车并选中它（``_on_vehicle_new`` 的实际动作）。"""
        name = str(name or "").strip()
        if not name:
            return
        if name in customization.vehicle_names():
            self._flash_vehicle_hint("已存在同名车辆")
            return
        # 预填口径（用户点名）：旧 module_count > 0 就用它，否则默认 3 × 25 × 8。
        # 选车之后那三个旧键就不再参与排版了，但"接着上次的模组个数建新车"是
        # 顺手的事 —— 用户刚在这台车/旧配置上填过的数不该白填。
        count = int(customization.get("module_count"))
        total = count if count > 0 else customization.DEFAULT_MODULES
        total = max(1, min(64, total))
        cells = [customization.DEFAULT_CELLS] * total
        temps = [customization.DEFAULT_TEMPS] * total
        customization.save_vehicle(name, cells, temps)
        customization.select_vehicle(name)
        self._sync_vehicle_rows()
        self._notify_vehicle_changed()

    def _on_vehicle_delete(self) -> None:
        """删除当前车：先确认（删的是整套结构，误点一次就得重填）。"""
        name = customization.current_vehicle()
        if not name:
            return
        answer = QMessageBox.question(
            self, "删除车辆",
            f"确定删除「{name}」吗？\n\n只删这台车的模组结构，已接收的数据不受影响。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        customization.delete_vehicle(name)
        self._sync_vehicle_rows()
        self._notify_vehicle_changed()

    def _on_vehicle_rename_typed(self) -> None:
        """在选车框里改写名字 = 给**当前车辆**改名（用户 2026-10-22 的口径）。

        重名 / 空名一律**拒绝并回滚文本**（不留一个假的成功态）；没有选中车时
        直接把文本还原成「（未选择）」，不凭空造一台车出来。
        """
        old = customization.current_vehicle()
        typed = self.cmb_vehicle.currentText().strip()
        if not old:
            self._refresh_vehicle_combo()
            return
        if not typed:
            self.cmb_vehicle.setEditText(old)
            self._flash_vehicle_hint("名称不能为空")
            return
        if typed == old:
            return
        if not customization.rename_vehicle(old, typed):
            self.cmb_vehicle.setEditText(old)
            self._flash_vehicle_hint("名称已存在")
            return
        self._refresh_vehicle_combo()
        self._sync_vehicle_rows()
        self._notify_vehicle_changed()

    @staticmethod
    def _next_new_vehicle_name(names: list[str]) -> str:
        """下一个可用的默认名：新车 1、新车 2……（永远不撞已有名字）。"""
        used = set(names)
        index = 1
        while f"新车 {index}" in used:
            index += 1
        return f"新车 {index}"

    def _flash_vehicle_hint(self, text: str, ms: int = 2500) -> None:
        """在选车行右侧原位显示一句短提示，几秒后自己消失（平时不占宽度）。"""
        self.lbl_vehicle_hint.setText(text)
        self.lbl_vehicle_hint.setVisible(bool(text))

        def _clear() -> None:
            try:
                self.lbl_vehicle_hint.setText("")
                self.lbl_vehicle_hint.setVisible(False)
            except RuntimeError:          # 面板已析构，别再碰 C++ 对象
                pass

        QTimer.singleShot(ms, _clear)

    def _notify_vehicle_changed(self) -> None:
        """告诉主窗口"车辆结构变了"（它负责合并 200ms 后重排接收区）。

        复用既有的 ``on_custom`` 回调通道（不新造信号/事件总线）：面板认识的
        永远是"一个 key 一个值"，怎么生效由主窗口决定（见 _preview_custom）。
        """
        if self._on_custom is not None:
            self._on_custom("vehicle", customization.current_vehicle())

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

        # ⚠️ 这两张表在「主题」一节之前就建好了（见 _build_theme_section 上方）：
        #    「界面材质」会先登记进去，这里**不能重新赋值**，否则会被冲掉。
        # 每一行的（左侧行标签, 字段控件）。选车后要整行隐藏旧的三件套网格参数
        # （module_count / cells_per_module / temps_per_module）—— 只藏字段不藏
        # 标签会在表单里留一个孤立文字，看着像坏了。
        # 滑杆型配置的预览标签（"拖动即时看效果"的那种），key -> QLabel
        self._custom_previews: dict[str, QLabel] = {}
        for spec in specs:
            if spec["key"] == "material":
                # 已经在「主题 / 材质强度」那一对里生成（见 _build_theme_section）
                continue
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
                # 右端可以按运行环境现算（``maximum_fn``）：动画帧率的上限就是
                # **当前显示器的最高刷新率** —— 每开一次面板都重算，换屏不用重启。
                maximum = int(spec["maximum"])
                maximum_fn = spec.get("maximum_fn")
                if callable(maximum_fn):
                    try:
                        maximum = int(maximum_fn())
                    except Exception:      # noqa: BLE001 - 取不到就用注册表里的上限
                        maximum = int(spec["maximum"])
                maximum = max(int(spec["minimum"]), min(int(spec["maximum"]), maximum))
                slider.setRange(int(spec["minimum"]), maximum)
                # 老配置里存过 240、而当前屏幕只有 165 → 夹进量程，别让滑杆
                # 显示一个够不着的值（真正生效值也会被 ui_anim 再夹一次）。
                slider.setValue(max(int(spec["minimum"]),
                                    min(maximum, int(customization.get(spec["key"])))))
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
            elif spec["kind"] == "choice":
                # 枚举（二选一起步）：下拉框。选中即回调 on_custom 做实时预览 ——
                # 换材质要**当场**看到，不能等按确定（用户点名"实时生效"）。
                combo = ChevronComboBox()
                for label, stored in spec["options"]:
                    combo.addItem(label, stored)
                index = combo.findData(customization.get(spec["key"]))
                combo.setCurrentIndex(index if index >= 0 else 0)
                if spec.get("tip"):
                    combo.setToolTip(spec["tip"])
                combo.setMinimumWidth(190)
                combo.currentIndexChanged.connect(
                    lambda _idx, s=spec, c=combo: self._on_choice_changed(s, c))
                field = combo
                widget = combo
                row_label = self._label(spec["label"])
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
            self._custom_rows[spec["key"]] = (row_label, field)
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

    def _on_choice_changed(self, spec: dict, combo) -> None:
        """下拉框（枚举配置）改了：把**存储值**交给主窗口做实时预览。"""
        if self._on_custom is not None:
            self._on_custom(spec["key"], combo.currentData())

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
            elif spec["kind"] == "choice":
                value = widget.currentData()
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
        # 按钮**紧跟**在路径文字后面，不要被推到行尾：标签不给拉伸因子，
        # 按钮接在它后面，剩下的空白交给末尾那根 stretch（用户点名："打开记录的
        # 按钮应该紧挨着相邻文字，去掉它们之间多余的空白间距"——原来标签吃 stretch
        # 撑满整行，文字结束到按钮之间就会空出一大段）。
        row.addWidget(path_label)
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
        row.addStretch(1)          # 同上：按钮串在文字后面，空白推到行尾
        form.addRow(self._row(row))

        # 接收历史行（自动保存的最近 5000 行接收数据，与日志同目录）：
        # 路径没定位到（只读盘符等）就整行不显示，别给一个点不动的按钮。
        import rx_history
        history_path = rx_history.path()
        if history_path:
            form.addRow(self._caption("接收历史"))
            hrow = QHBoxLayout()
            hrow.setSpacing(10)
            hpath_label = QLabel(self._elide_middle(history_path))
            hpath_label.setFont(ui_font(10.5))
            hpath_label.setObjectName("groupCaption")
            hpath_label.setMinimumWidth(1)      # 同日志行的坑（见上）
            hpath_label.setToolTip(history_path)
            hpath_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            hrow.addWidget(hpath_label)          # 不拉伸，见上面日志行的说明
            hist_button = InkButton("打开记录", variant="ghost")
            hist_button.setToolTip(
                f"打开历史接收数据（自动保存最近 {rx_history.max_lines()} 行，"
                "含时间戳；行数与接收区「保留行数」一致，到量自动滚动覆盖最早的数据）")
            hist_button.clicked.connect(
                lambda: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(history_path)))
            hrow.addWidget(hist_button)
            hrow.addStretch(1)         # 同上：按钮紧挨文字，空白推到行尾
            form.addRow(self._row(hrow))

    # -------------------------------------------------------- MCP Service

    def _build_mcp_section(self, form: QFormLayout) -> None:
        """「MCP Service」一行：一键复制自包含的 MCP 部署包。

        为什么是"复制一段话"而不是"帮我装好"：装 MCP 要在**用户自己的**机器上
        建 venv、写文件、改客户端的 mcp.json —— 这些都是 Agent 擅长且被沙箱授权的
        事，软件自己去改别人的配置文件反而是越界。所以这里只负责把**完整上下文**
        （依赖、源码、注册片段、自检三条）一次性交出去，复制 → 粘给 Agent 即可。
        """
        form.addRow(self._caption("MCP Service"))
        row = QHBoxLayout()
        row.setSpacing(10)
        # ⚠️ 提示文字必须短：这行给按钮留位之后，剩余宽度有限，长文案会被
        #   裁在按钮底下（实测"直接读写串口"半个字压进按钮）。全句放 tooltip。
        hint = QLabel("让 AI Agent 直接读写串口")
        hint.setFont(ui_font(10.5))
        hint.setObjectName("groupCaption")
        # 同日志行：必须给 1 而不是 0，否则 minimumSizeHint 会把面板撑宽
        hint.setMinimumWidth(1)
        hint.setToolTip(
            "复制的内容自包含：装依赖的命令、MCP 服务全部源码、客户端注册片段、"
            "三条自检。粘给任意 Agent（WorkBuddy / Claude / Cursor…），"
            "它就能自动装好并把串口接到自己手上")
        row.addWidget(hint)
        self.btn_mcp_copy = InkButton("复制接入配置", variant="ghost")
        self.btn_mcp_copy.setToolTip(
            "复制一整段（含 MCP 服务源码与注册片段）—— 直接粘给任意 Agent，"
            "它会自动装依赖、写服务、注册 MCP 并自检")
        self.btn_mcp_copy.clicked.connect(self._copy_mcp_payload)
        row.addWidget(self.btn_mcp_copy)
        row.addStretch(1)          # 按钮紧挨文字，空白推到行尾（同日志行）
        form.addRow(self._row(row))

    def _copy_mcp_payload(self) -> None:
        """把接入包塞进剪贴板；剪贴板不可用时落到文件并告诉用户路径。

        ⚠️ 非交互/远程会话里 ``clipboard().text()`` 会读回空串（写过再读一次是
        唯一可靠的探测方式）。这时不能假装"已复制"——用户粘出来是空的，
        所以退回"写文件 + 在按钮上显示文件名"。
        """
        import mcp_bridge
        text = mcp_bridge.deploy_payload()
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(text)
        if clipboard.text() == text:
            self._flash_button(self.btn_mcp_copy, "已复制，粘给 Agent 即可",
                               "复制接入配置", 4000)
            app_log.info("MCP 接入包已复制到剪贴板")
            return
        try:
            path = mcp_bridge.write_payload()
        except OSError as exc:
            app_log.warning(f"MCP 接入包保存失败：{exc}")
            self._flash_button(self.btn_mcp_copy, "复制失败", "复制接入配置", 4000)
            return
        self._flash_button(self.btn_mcp_copy, Path(path).name, "复制接入配置", 6000)
        app_log.info(f"MCP 接入包已保存：{path}")

    def _flash_button(self, button, text: str, restore: str, ms: int = 4000) -> None:
        """在按钮上原地显示结果，几秒后复原（同「检查更新」的反馈方式）。"""
        button.setText(text)

        def _restore() -> None:
            try:
                button.setText(restore)
            except RuntimeError:      # 面板已析构，别再碰 C++ 对象
                pass

        QTimer.singleShot(ms, _restore)

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
    def _local_version() -> tuple:
        """本地版本号，同样补齐到三段，跟远端 tag 的口径一致。"""
        from version import VERSION
        parts = tuple(int(p) for p in VERSION.split("."))
        return parts + (0,) * (3 - len(parts))

    def _check_update(self) -> None:
        """多源检查更新（源链见 updater.version_sources）。

        无代理的网络走 jsDelivr/镜像（国内可直连）；开着代理的用户走链尾的
        GitHub 直连。整条链失败且还没试过本机代理时，把同一条链经本机代理
        再跑一遍。发现新版本 → 弹更新对话框（立即更新/暂不更新/前往仓库），
        不再直接跳浏览器。
        """
        self._proxy_attempt = 0
        self._update_src_idx = 0
        self._update_pass = 0
        self._start_update_request()

    def _start_update_request(self) -> None:
        import updater
        sources = updater.version_sources()
        if self._update_src_idx >= len(sources):
            self._update_pass += 1
            self._update_src_idx = 0
            if self._update_pass >= updater.CHECK_PASSES:
                if self._proxy_attempt == 0:
                    # 整条链直连都不通：换本机代理把链再跑一遍（代理开着的用户）
                    self._proxy_attempt = 1
                    self._update_src_idx = 0
                    self._update_pass = 0
                else:
                    self._finish_update_check()
                    self._update_failed()
                    return
        if self._proxy_attempt == 1 and self._update_src_idx == 0 \
                and self._update_pass == 0:
            from PySide6.QtNetwork import QNetworkProxy
            QNetworkProxy.setApplicationProxy(QNetworkProxy(
                QNetworkProxy.ProxyType.HttpProxy, *self.FALLBACK_PROXY))
        _name, url, parser = sources[self._update_src_idx]
        self._update_parser = parser
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText("检查中…")
        self._pending_reply = self._nam.get(QNetworkRequest(QUrl(url)))
        self._pending_reply.finished.connect(self._on_update_reply)
        self._update_timer.start(updater.SOURCE_TIMEOUT_MS)

    def _on_update_reply(self) -> None:
        reply = self.sender()
        if reply is not getattr(self, "_pending_reply", None):
            return                      # 超时后迟到的回应，忽略
        self._update_timer.stop()
        error = reply.error()
        data = bytes(reply.readAll())
        reply.deleteLater()
        self._pending_reply = None
        if error != QNetworkReply.NetworkError.NoError:
            # 该源失败：回退下一源 / 下一轮 / 本机代理层
            self._update_src_idx += 1
            self._start_update_request()
            return
        try:
            info = self._update_parser(data.decode("utf-8", errors="replace"))
        except Exception:
            import traceback
            app_log.error("检查更新解析异常: " + traceback.format_exc(limit=3))
            info = None
        if not info or not info.get("version"):
            self._update_src_idx += 1   # 解析不出当源失败，回退下一源
            self._start_update_request()
            return
        self._finish_update_check()
        self._handle_update_info(info)

    def _handle_update_info(self, info: dict) -> None:
        from updater import UpdateDialog, version_tuple
        from version import VERSION
        latest = version_tuple(info["version"])
        if latest <= self._local_version():
            self.lbl_version.setText(f"已是最新版本 v{VERSION}")
            self._flash_button_result("✓ 已是最新")
            return
        latest_str = info["version"]
        self.lbl_version.setText(f"发现新版本 v{latest_str}")
        self._flash_button_result(f"发现 v{latest_str}！")
        dialog = UpdateDialog(latest=latest_str, current=VERSION,
                              sha256=info.get("sha256"),
                              tag=f"v{latest_str}", auto=False, parent=self)
        dialog.exec()

    def _update_timed_out(self) -> None:
        if getattr(self, "_pending_reply", None) is None:
            return
        self._pending_reply.disconnect(self)
        self._pending_reply.abort()
        self._pending_reply = None
        self._update_src_idx += 1       # 单源超时 → 回退下一源/下一轮/代理层
        self._start_update_request()

    def _update_failed(self) -> None:
        self.lbl_version.setText("检查失败：所有更新源都不可达（已尝试直连与本机代理）")
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
