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

import json
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QPoint, QRect, QRectF, QSize, QThread, QTimer, Qt, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

import app_log
import customization
from ui_theme import apply_titlebar_theme
from PySide6.QtGui import (
    QColor, QDesktopServices, QFontMetrics, QGuiApplication, QIntValidator,
    QPainter, QPixmap,
)
from PySide6.QtCore import QUrl
from ui_controls import (
    ChevronComboBox, ChevronSpinBox, GlassSlider, IconButton, InkButton, Switch,
)
from ui_theme import (
    DEFAULT_GLASS, GAP, GLYPH_ADD, GLYPH_REFRESH, detect_system_scheme, mono_font,
    qcolor, rx_accent, stamp_accent, ui_font,
)

# (显示名, 存进配置的值)
THEME_MODES = (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark"))
# 「定制功能」里那一列数字框的统一宽度。宽到装得下最长的一条（"10000 mV" +
# 左右内边距 + 右侧 chevron ≈ 95px），又不至于让 "7" 这种一位数拖出一大片空框。
# 108 → 100：一位数的"暂停定位行数/行数（最大化）/温度下限"等框不再空一大截
# （用户点名"这个框也大了"）。
SPIN_COLUMN_W = 100
# 按钮开关排几列。三列是为了"一屏放完"：
# 两列时最后一行会被滚动区截掉半颗开关，看着像画坏了。
BUTTON_COLUMNS = 4
# 「模组 i」里两个"数量"输入框的宽度。**手动输入**（用户 2026-10-22 明确），
# 所以只要装得下 3 位数 + 内边距；左边写"电芯数量："、右边再没有量词了。
MODULE_INPUT_W = 64
# 独立下拉框（主题 / 界面材质 / 编码…）的宽度：**按内容定宽**，不再拉满整列。
# 这些框里的值都很短（"深色""毛玻璃""utf-8"），铺满整行只会让人觉得空。
FIELD_W = 210
# 面板整体的**兜底下限**（真正的宽度按内容算，见 __init__ 末尾那行）。
# ⚠️ 不能再往下调：body 的最小宽由"最宽标签 187 + 18 + 最宽字段 362"决定，
# 把下限设到 body 之下没用（内容仍然要那么宽），只会让面板在窄屏上被裁。
PANEL_MIN_W = 470
# 「赛季」年份框的宽度：4 位数 + 内边距 + 右侧 chevron
SEASON_SPIN_W = 78
# 车辆那一排里的「模组数」框宽：两颗数字（上限 64）+ 内边距。
# ⚠️ 上限 64 只有两位数的空间，chevron 已隐藏（set_arrows_visible(False)），
#    框里只有居中的数字 —— 86 对"5"整整空一大截，压到 72 刚好不挤（用户点名"框太大"）。
MODULE_COUNT_W = 72
# InkButton 的宽度算法（见 ui_controls.InkButton._apply_size_hint）：文字宽 + 44。
# 这里要"几颗按钮同宽"，就照同一条公式取最大那张 —— 各按自己文字长短长宽的话，
# 一排放四颗就是四个不同宽度（用户点名的"等宽对齐"就没了）。
_INK_PAD = 44


def _equalize_buttons(buttons, extra_texts=()) -> int:
    """把一排按钮锁成同一宽度（取最大者），返回该宽度。

    ``extra_texts`` 是**运行时会临时印在按钮上**的文字（"✓ 123 行" / "✓ +2 台"
    这类原地反馈）：必须一起算进来。否则反馈一长，``InkButton.setText`` 里的
    ``setMinimumSize`` 会把那一颗顶宽 —— 一排里瞬间不等宽，对齐当场破掉。
    """
    live = [button for button in buttons if button is not None]
    if not live:
        return 0
    metrics = QFontMetrics(live[0].font())
    texts = [button.text() for button in live] + [str(text) for text in extra_texts]
    width = max(metrics.horizontalAdvance(text) for text in texts) + _INK_PAD
    for button in live:
        button.setFixedWidth(width)
    return width


class _ElidedPathLabel(QLabel):
    """单行路径标签（用户 2026-11-01：地址要显示成一排）。

    宽度不够时**中间省略**（…），完整路径永远在 tooltip 里；文字可选中复制。
    ⛔ 不用 wordWrap —— 折行就是"两排"，用户点名不要。三处路径标签
    （日志 / 会话数据库 / 接收历史）统一用它，观感一致。
    """

    def __init__(self, text: str, font=None) -> None:
        super().__init__(text)
        self._full = text
        if font is not None:
            self.setFont(font)
        self.setWordWrap(False)
        self.setMinimumWidth(1)
        # 宽度完全交给布局：Ignored 让 sizeHint 不参与拉伸分配
        self.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        self.setToolTip(text)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

    # -- 尺寸：别把整条路径的宽度带进布局 --------------------------------
    def sizeHint(self) -> QSize:                      # noqa: N802 (Qt 命名)
        # 给一个"典型宽度"占位：整条路径（可达 80+ 字符）会把面板撑宽
        return QSize(240, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:               # noqa: N802 (Qt 命名)
        # ⛔ 非折行 QLabel 的 minimumSizeHint = 整条文本宽；不覆写的话布局
        #    压不窄它，超出部分被父容器**硬裁**（用户 2026-11-01 截图：
        #    接收历史路径末尾 ".lo" 被切、且没有省略号）。给 1px 允许压缩，
        #    压缩后由 resizeEvent 里的 elidedText 生成 "…"。
        return QSize(1, super().minimumSizeHint().height())

    # -- 文本：外部改文本（换车号 / 换目录）也要重新省略 ------------------
    def setText(self, text: str) -> None:
        self._full = text or ""
        self.setToolTip(self._full)
        self._refit()

    def _refit(self) -> None:
        fm = QFontMetrics(self.font())
        QLabel.setText(self, fm.elidedText(
            self._full, Qt.TextElideMode.ElideMiddle, max(16, self.width())))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refit()


def _import_summary(result) -> tuple[int, str]:
    """``vehicle_share`` 的导入结果 → （变更台数, 一句话说明）。

    真实返回是 ``{"added", "updated", "skipped", "errors"}``（见
    ``vehicle_share.import_payload``）；同名 = 更新、不同名 = 新增，所以
    "动了多少台" = added + updated。**errors 里的那句人话**直接拿来当失败原因 ——
    那个模块把"文件读不了/JSON 坏了/包是空的"都翻译好了，这里不再自己判。
    """
    if not isinstance(result, dict):
        return 0, ""
    added = int(result.get("added") or 0)
    updated = int(result.get("updated") or 0)
    errors = [str(item) for item in (result.get("errors") or []) if str(item)]
    if added and updated:
        note = f"新增 {added}、更新 {updated}"
    elif updated:
        note = f"更新 {updated} 台"
    elif added:
        note = f"新增 {added} 台"
    else:
        note = errors[0] if errors else ""
    return added + updated, note


def _pack_text(entry) -> tuple[str, str]:
    """远程清单条目 → （一行文字「名称 · N 模组 · 备注」，tooltip）。

    条目形状由 ``vehicle_share._index_items`` 归一过：
    ``{"name", "file", "modules", "note"}``。这里仍然逐个键**软取**（缺字段就
    少一段，而不是整行变成 "（未命名）"）：索引是仓库里的 json，队友手改过也
    不该让清单变成一片空白。
    """
    if isinstance(entry, dict):
        name = str(entry.get("name") or entry.get("file") or "（未命名）")
        parts = [name]
        try:
            modules = int(entry.get("modules") or 0)
        except (TypeError, ValueError):
            modules = 0
        if modules > 0:
            parts.append(f"{modules} 模组")
        note = str(entry.get("note") or "").strip()
        if note:
            parts.append(note)          # 来源/说明：索引里能给的都摊在一行里
        tip_lines = []
        if entry.get("file"):
            tip_lines.append(f"文件：{entry.get('file')}")
        if note:
            tip_lines.append(f"说明：{note}")
        return " · ".join(parts), "\n".join(tip_lines) or " · ".join(parts)
    return str(entry), str(entry)


def _vehicle_doc(names) -> dict:
    """挑几台车 → 一个可上传的文档（结构由 ``vehicle_share.export_payload`` 定）。

    优先用模块自己的打包函数（它才知道远端认什么格式：schema / app /
    exported_at / vehicles）；没有就退回 ``{"vehicles": [...]}`` —— 与本机
    ``customization.vehicles()`` 同形。
    """
    share = _vehicle_share_module()
    builder = getattr(share, "export_payload", None) if share else None
    if callable(builder):
        return builder(list(names))
    wanted = set(names)
    items = [item for item in customization.vehicles() if item["name"] in wanted]
    return {"vehicles": items}


def _vehicle_share_module():
    """惰性取 ``vehicle_share``；没落地（ImportError）就返回 None。

    它是**新模块**，可能比本文件晚到 —— 面板不能因为"还没写完"就整块崩掉，
    所以调用方一律先拿到 None 检查，再给按钮置灰 + tooltip 说明。
    """
    try:
        import vehicle_share
    except ImportError:
        return None
    return vehicle_share


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
        self.edit.setObjectName("fieldEdit")  # 与设置面板其它输入框同一套外观
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




class McpManualDialog(QDialog):
    """「连接至自定义 MCP」那种**逐项手填**的对话框，把本机真实值直接给出。

    用户 2026-10-31 的原话：客户端里那个「连接至自定义 MCP」要一项一项填
    （名称 / 类型 / 命令 / 参数 / 环境变量），而软件原来只给"一整段丢给 Agent"的
    接入包 —— 手填的人还得自己从那段文字里抠路径。这里把每一项**单独一行、
    单独一个复制按钮**，照着填即可。
    """

    def __init__(self, scheme: str = "dark", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        import mcp_bridge

        self.setWindowTitle("MCP 手动填写参数")
        self.setModal(True)
        self.setMinimumWidth(620)
        self._scheme = scheme
        self._fields = mcp_bridge.manual_fields()

        root = QVBoxLayout(self)
        root.setContentsMargins(GAP + 6, GAP + 6, GAP + 6, GAP + 6)
        root.setSpacing(GAP)

        tip = QLabel("照下面逐项填进客户端的「连接至自定义 MCP」对话框；"
                     "每一项都能单独复制。")
        tip.setFont(ui_font(11))
        tip.setObjectName("groupCaption")
        tip.setWordWrap(True)
        root.addWidget(tip)

        form = QFormLayout()
        form.setSpacing(GAP)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignVCenter)
        rows = (
            ("名称", self._fields["name"], "客户端里显示的名字，随便改，但建议保持一致"),
            ("类型", self._fields["transport"], "本服务走 stdio（由客户端拉起，不占端口）"),
            ("命令", self._fields["command"] or "python",
             "装有 mcp + pyserial 的 python.exe 绝对路径"),
            ("参数", self._fields["args"][0], "服务脚本的绝对路径（点「+ 参数」逐条填，只有 1 条）"),
            ("环境变量", "", "本服务不需要环境变量 —— 那个对话框里保持为空即可"),
        )
        for label, value, tip_text in rows:
            # ⛔ 必须用两参数 addRow(标签, 控件)：单参数那版控件会横跨两列，
            #    标签列直接空掉（第一版就是这样，"名称/类型/命令"全没显示）。
            lab = QLabel(label)
            lab.setFont(ui_font(11.5))
            lab.setObjectName("groupCaption")
            lab.setMinimumWidth(58)
            form.addRow(lab, self._manual_row(value, tip_text))
        root.addLayout(form)

        if self._fields["notes"]:
            note = QLabel("\n".join(self._fields["notes"]))
            note.setFont(ui_font(10.5))
            note.setObjectName("groupCaption")
            note.setWordWrap(True)
            root.addWidget(note)

        row = QHBoxLayout()
        row.setSpacing(GAP)
        self.btn_json = InkButton("复制整段 JSON", variant="ghost")
        self.btn_json.setToolTip("给能直接读配置文件的客户端用（Claude / Cursor / WorkBuddy 的 mcp.json）")
        self.btn_json.clicked.connect(self._copy_json)
        row.addWidget(self.btn_json)
        self.btn_service = InkButton("重新导出服务文件", variant="ghost")
        self.btn_service.setToolTip(
            "把 MCP 服务源码重新写到「参数」那个路径（升级后代码可能变了）")
        self.btn_service.clicked.connect(self._re_export)
        row.addWidget(self.btn_service)
        row.addStretch(1)
        close = InkButton("关闭", variant="primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)

    # -- 内部 ------------------------------------------------------------

    def _manual_row(self, value: str, tip_text: str):
        """一行：标签 + 只读取值框 + 复制按钮。值太长就中间省略，完整值在 tooltip。"""
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        edit = QLineEdit(value)
        edit.setObjectName("fieldEdit")      # 圆角/描边走全局 QSS 那一族
        edit.setReadOnly(True)
        edit.setFont(ui_font(11.5))
        edit.setMinimumWidth(1)
        if not value:
            shown = "（留空）"
        elif len(value) <= 56:
            shown = value
        else:
            shown = value[:26] + "…" + value[-26:]
        edit.setText(shown)
        edit.setToolTip(value or "（留空）")
        edit.setCursorPosition(0)            # 别把光标甩到末尾（只显示尾巴）
        lay.addWidget(edit, 1)
        copy = InkButton("复制", variant="ghost")
        copy.setToolTip(tip_text)
        copy.clicked.connect(lambda _=False, v=value, b=copy: self._copy(v, b))
        lay.addWidget(copy)
        return box

    def _copy(self, value: str, button) -> None:
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(value)
        if value and clipboard.text() == value:
            self._flash(button, "✓")
        else:
            # 空值（环境变量那一行）也要给反馈，否则用户以为没点上
            self._flash(button, "✓ 空")

    def _flash(self, button, text: str) -> None:
        button.setText(text)
        QTimer.singleShot(1200, lambda b=button: b.setText("复制"))

    def _copy_json(self) -> None:
        import json

        from PySide6.QtGui import QGuiApplication as _QGui  # noqa: N814 - 局部别名
        cfg = {"mcpServers": {"serial-assistant": {
            "command": self._fields["command"] or "python",
            "args": list(self._fields["args"]),
            "env": dict(self._fields["env"]),
        }}}
        text = json.dumps(cfg, ensure_ascii=False, indent=2)
        _QGui.clipboard().setText(text)
        self._flash(self.btn_json, "✓ 已复制")

    def _re_export(self) -> None:
        import mcp_bridge
        path, wrote = mcp_bridge.ensure_service()
        self._flash(self.btn_service, "✓ 已更新" if wrote else "✓ 已是最新")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_titlebar_theme(self, self._scheme)


class _TaskWorker(QThread):
    """把**一次阻塞调用**（网络 / 文件 IO）挪到后台线程，结果用信号送回 UI。

    为什么自己搓这一层：取清单、下载导入、上传都是"发一次请求拿一个结果"，
    用 QNetworkAccessManager 得写一整套状态机；而界面这边只需要"别卡"。所以最
    薄的一层封装:一个 callable 进、一个结果信号出、一个错误信号出。

    ⛔ 异常必须在这里吞掉转成 ``failed``：槽里抛出去的异常会被 Qt 打印到 stderr，
    而 exe 是 ``console=False`` —— 用户看到的就是"点了没反应"。
    """

    ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:          # 网络/文件/解析一视同仁，交给 UI 显示红字
            self.failed.emit(str(exc) or type(exc).__name__)
            return
        self.ok.emit(result)


class VehicleLibraryDialog(QDialog):
    """在线车辆库：从远端拉车辆包清单、导入到本机，或把自己的车传上去。

    四段式布局（自上而下）：刷新条 / 远程清单 / 导入动作 / 上传区。
    三个阻塞动作（取索引、下载导入、上传）全部走 ``_TaskWorker`` —— 一张"点了
    就定住"的对话框，用户只会以为软件卡死。
    """

    # 导入成功后通知设置面板：车辆下拉 / 逐模组行要重建（参数是新增台数）
    vehicles_changed = Signal(int)

    def __init__(self, scheme: str = "dark", parent: QWidget | None = None,
                 auto_refresh: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("在线车辆库")
        self.setModal(True)
        self.setMinimumWidth(620)
        self._scheme = scheme
        self._entries: list = []
        self._worker: _TaskWorker | None = None
        self._pending_close = False
        # "仓库里已有同名文件"的确认态：true 时「上传」按钮显示"确认覆盖？"，
        # 再点一次才带 overwrite=True 真写（换车/刷新清单会撤回这个态）。
        self._pending_overwrite = False

        root = QVBoxLayout(self)
        root.setContentsMargins(GAP + 6, GAP + 6, GAP + 6, GAP + 6)
        root.setSpacing(GAP)

        tip = QLabel("从在线库挑一台车导入，或把本机的车上传上去（只传车辆结构）。")
        tip.setFont(ui_font(11))
        tip.setObjectName("groupCaption")
        tip.setWordWrap(True)
        root.addWidget(tip)

        # ---- 刷新条 ----
        top = QHBoxLayout()
        top.setSpacing(10)
        self.btn_refresh = InkButton("刷新", variant="ghost")
        self.btn_refresh.setToolTip("重新从在线库拉一次车辆包清单")
        self.btn_refresh.clicked.connect(self._refresh_index)
        top.addWidget(self.btn_refresh)
        # 状态文字必须是**短**的（同 MCP 那行），长文案会被按钮裁掉；全句进 tooltip
        self.lbl_status = QLabel("")
        self.lbl_status.setFont(ui_font(10.5))
        self.lbl_status.setObjectName("groupCaption")
        self.lbl_status.setMinimumWidth(1)      # 0 等于没设（见日志行那条注释）
        top.addWidget(self.lbl_status, 1)
        root.addLayout(top)

        # ---- 远程清单 ----
        self.list_packs = QListWidget()
        self.list_packs.setObjectName("packList")
        self.list_packs.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_packs.setMinimumHeight(190)
        self.list_packs.setStyleSheet(self._list_qss())
        self.list_packs.setToolTip("单选或多选（Ctrl / Shift）后点下面的「导入选中」")
        root.addWidget(self.list_packs, 1)
        self._show_placeholder("点「刷新」从在线库拉取车辆包清单")

        # ---- 导入动作 ----
        import_row = QHBoxLayout()
        import_row.setSpacing(GAP)
        self.btn_import_selected = InkButton("导入选中", variant="primary")
        self.btn_import_selected.clicked.connect(self._import_selected)
        import_row.addWidget(self.btn_import_selected)
        self.btn_import_all = InkButton("导入全部", variant="ghost")
        self.btn_import_all.clicked.connect(self._import_all)
        import_row.addWidget(self.btn_import_all)
        import_row.addStretch(1)
        root.addLayout(import_row)

        # ---- 上传区 ----
        # 一条 1px 分隔线（用 QFrame 的横线形状，不加 QSS）
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setLineWidth(1)
        root.addWidget(line)

        upload_title = QLabel("上传到在线库")
        upload_title.setFont(ui_font(12.5))
        root.addWidget(upload_title)

        form = QFormLayout()
        form.setSpacing(GAP)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignVCenter)
        # ⛔ 两参数 addRow(标签, 控件)：单参数那版控件横跨两列、标签列全空
        self.cmb_upload = ChevronComboBox()
        self.cmb_upload.setMaximumWidth(FIELD_W)
        self._refresh_upload_combo()
        # 换车就等于换目标文件：上一次"确认覆盖"的待定态立刻作废
        self.cmb_upload.currentIndexChanged.connect(
            lambda _index: self._reset_overwrite())
        form.addRow(self._field_label("车辆："), self.cmb_upload)

        self.edit_token = QLineEdit()
        self.edit_token.setObjectName("fieldEdit")   # 与面板其它输入框同一族
        self.edit_token.setFont(ui_font(11.5))
        self.edit_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_token.setPlaceholderText("GitHub Token（点右侧「创建令牌」生成后粘贴）")
        # token 的存取**全交给 vehicle_share**：它用 Windows DPAPI 按当前用户加密
        # 落盘（不是明文，也不进注册表的明文值）。面板自己再存一份只会多出
        # 一个没人管的明文副本。
        share = _vehicle_share_module()
        load_token = getattr(share, "load_token", None) if share else None
        if callable(load_token):
            try:
                self.edit_token.setText(str(load_token() or ""))
            except Exception as exc:
                app_log.warning(f"读取车辆上传令牌失败：{exc}")
        self.edit_token.setToolTip(
            "上传需要一个有 contents:write 权限的 GitHub Token。\n"
            "令牌由 Windows DPAPI 按当前用户加密后存在本机（换账户/换机器都解不开），"
            "不会上传到别处。\n"
            "填一次之后，点「上传」就是**全自动**，不用再开浏览器拖文件。")
        # 「创建令牌」直链：GitHub 的经典 PAT 页面支持 URL 预填 scope，一键带好
        # repo 权限 —— 用户只要点一下、复制、粘贴回来，之后上传就全自动了。
        self.btn_new_token = InkButton("创建令牌", variant="ghost")
        self.btn_new_token.setToolTip(
            "打开 GitHub 创建令牌页面（已预填 repo 权限）；\n"
            "生成后复制粘贴到左边这一格，以后「上传」就全自动了")
        self.btn_new_token.clicked.connect(self._open_new_token_page)
        token_row = QHBoxLayout()
        token_row.setContentsMargins(0, 0, 0, 0)
        token_row.setSpacing(8)
        token_row.addWidget(self.edit_token, 1)
        token_row.addWidget(self.btn_new_token)
        token_holder = QWidget()
        token_holder.setLayout(token_row)
        token_row.setContentsMargins(0, 0, 0, 0)     # setLayout 会塞回样式默认 9px
        form.addRow(self._field_label("Token："), token_holder)
        root.addLayout(form)
        # 令牌状态小字：存不住 / 解不开 / 本次会话有效 —— 都写这一行（红字），
        # 不去挤上面那条总状态行（它还要报清单和导入的结果）。
        self.lbl_note = QLabel("")
        self.lbl_note.setFont(ui_font(10.5))
        self.lbl_note.setObjectName("groupCaption")
        self.lbl_note.setMinimumWidth(1)            # 0 等于没设（同日志行）
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setVisible(False)
        root.addWidget(self.lbl_note)
        self._report_token_state(loaded=bool(self.edit_token.text()))

        upload_row = QHBoxLayout()
        upload_row.setSpacing(GAP)
        self.btn_upload = InkButton("上传", variant="ghost")
        self.btn_upload.setToolTip(
            "把选中的车辆结构推到在线库（Token 由系统加密存在本机）")
        self.btn_upload.clicked.connect(lambda: self._upload())
        upload_row.addWidget(self.btn_upload)
        self.btn_upload_browser = InkButton("在浏览器里上传", variant="ghost")
        self.btn_upload_browser.setToolTip(
            "没有 Token 时的兜底：在浏览器里手动把打包好的文件拖上去\n"
            "（想要全自动，填一次 Token 即可）")
        self.btn_upload_browser.clicked.connect(self._open_browser_upload)
        upload_row.addWidget(self.btn_upload_browser)
        self.btn_clear_token = InkButton("清除令牌", variant="ghost")
        self.btn_clear_token.setToolTip(
            "把本机加密保存的 GitHub 令牌删掉（只影响这台机器，不动仓库）")
        self.btn_clear_token.clicked.connect(self._clear_token)
        upload_row.addWidget(self.btn_clear_token)
        upload_row.addStretch(1)
        close = InkButton("关闭", variant="primary")
        close.clicked.connect(self.accept)
        upload_row.addWidget(close)
        root.addLayout(upload_row)

        # 不认字也不该给个能点的上传按钮：模块没落地就整组置灰
        self._sync_availability()
        if auto_refresh:
            # 事件循环起来之后再发请求：exec() 之前 show 都没出，先拉也没有意义
            QTimer.singleShot(0, self._refresh_index)

    # -- 外观 ------------------------------------------------------------

    @staticmethod
    def _list_qss() -> str:
        """清单的圆角浅描边底（与 fieldEdit 同一族观感）。

        ⛔ 不写进 ``ui_theme`` 的全局 QSS：那份一改就影响全程序的每个窗口，而且
        写错一个 token 会让后面的规则整段静默失效。这里只作用于本对话框的
        ``#packList``，颜色从主题 token **现取**，深浅色都跟得上。
        """
        surface_2 = qcolor("surface_2").name()
        line = qcolor("line").name()
        ink = qcolor("ink").name()
        sel = qcolor("sel").name()
        on_sel = qcolor("on_sel").name()
        return (
            f"QListWidget#packList {{ background: {surface_2};"
            f" border: 1px solid {line}; border-radius: 12px;"
            " padding: 4px; outline: none; }"
            f"QListWidget#packList::item {{ padding: 7px 8px;"
            f" border-radius: 8px; color: {ink}; }}"
            f"QListWidget#packList::item:selected {{ background: {sel};"
            f" color: {on_sel}; }}"
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        apply_titlebar_theme(self, self._scheme)

    @staticmethod
    def _field_label(text: str) -> QLabel:
        """上传区的小标签（字号与设置面板里「赛季：」那一档一致）。

        ⚠️ 最小宽度钉住文字宽度：QFormLayout 的标签列不够宽时会把这个 min 小的
        标签压扁（"Token："被压成两个点，踩过）。
        """
        label = QLabel(text)
        label.setFont(ui_font(10.5))
        label.setObjectName("groupCaption")
        label.setMinimumWidth(label.fontMetrics().horizontalAdvance(text) + 2)
        return label

    # -- 状态 ------------------------------------------------------------

    def _set_status(self, text: str, error: bool = False) -> None:
        """状态行：正常是次要文字色，出错是主题里的警示红（就地显示，不弹框）。"""
        self.lbl_status.setText(text)
        self.lbl_status.setToolTip(text)
        color = qcolor("alert_red").name() if error else qcolor("ink_2").name()
        self.lbl_status.setStyleSheet(f"color: {color};")

    def _sync_availability(self) -> None:
        """按 `vehicle_share` 落了哪些入口决定按钮可用度（缺什么灰什么 + tooltip）。"""
        share = _vehicle_share_module()

        def has(name: str) -> bool:
            return share is not None and callable(getattr(share, name, None))

        if not has("fetch_remote_index"):
            self.btn_refresh.setEnabled(False)
            self.btn_refresh.setToolTip("当前版本还取不到在线清单（车辆共享模块未就绪）")
        # download_remote_pack 是"下载 + 导入"一步到位的入口（内部已调
        # import_payload），面板只需要它这一个。
        if not has("download_remote_pack"):
            for button in (self.btn_import_selected, self.btn_import_all):
                button.setEnabled(False)
                button.setToolTip("当前版本还不支持导入在线车辆")
        if not has("upload_pack"):
            self.btn_upload.setEnabled(False)
            self.btn_upload.setToolTip("当前版本还不支持上传")
        if not has("upload_url_for_web"):
            self.btn_upload_browser.setEnabled(False)
            self.btn_upload_browser.setToolTip("当前版本没有配置浏览器上传地址")
        if not has("clear_token"):
            self.btn_clear_token.setEnabled(False)
            self.btn_clear_token.setToolTip("当前版本没有令牌管理入口")

    # -- 后台任务 --------------------------------------------------------

    def _run_task(self, fn, on_ok, busy: str) -> None:
        """起一个后台任务；同一时刻只允许一个（避免连点刷新堆线程）。"""
        if self._worker is not None and self._worker.isRunning():
            self._set_status("上一个任务还在跑…")
            return
        self._set_status(busy)
        worker = _TaskWorker(fn, self)
        worker.ok.connect(on_ok)
        worker.failed.connect(self._on_task_failed)
        worker.finished.connect(self._on_task_done)
        self._worker = worker
        worker.start()

    def _on_task_failed(self, reason: str) -> None:
        self._set_status(f"✗ {reason}", error=True)

    def _on_task_done(self) -> None:
        """线程结束：放掉引用（不然 QThread 析构在跑着的时候会 fail-fast）。"""
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        if self._pending_close:
            self._pending_close = False
            self.accept()

    def closeEvent(self, event) -> None:
        """后台还在跑时**不许关窗**：析构一个运行中的 QThread 会让程序直接退出。

        不 ``wait()``（下载可能很久，界面就冻了），改成"关窗标记 + 跑完自动关"。
        """
        worker = self._worker
        if worker is not None and worker.isRunning():
            self._pending_close = True
            self._set_status("正在处理，完成后自动关闭…")
            event.ignore()
            return
        super().closeEvent(event)

    # -- 清单 ------------------------------------------------------------

    def _refresh_index(self) -> None:
        share = _vehicle_share_module()
        fetch = getattr(share, "fetch_remote_index", None) if share else None
        if not callable(fetch):
            self._set_status("✗ 当前版本取不到在线清单", error=True)
            return
        self._reset_overwrite()          # 重新拉清单 = 重新开始，覆盖待定态作废
        self._run_task(fetch, self._fill_index, "正在获取在线清单…")

    def _fill_index(self, result) -> None:
        """填表：``fetch_remote_index`` 返回 ``{"ok","items","error","source"}``。

        ``ok=False`` 只说明"这次没拉到"（网络/镜像全挂、索引结构不对），
        **不是异常** —— 所以错误文案直接印在状态行上，清单保持原样（上一次
        拉到的还在，别清空）。
        """
        if not isinstance(result, dict):
            self._set_status("✗ 清单格式不认识", error=True)
            return
        if not result.get("ok"):
            self._set_status(f"✗ {result.get('error') or '清单拉取失败'}", error=True)
            return
        items = [entry for entry in (result.get("items") or [])
                 if isinstance(entry, dict)]
        self._entries = items
        self.list_packs.clear()
        source = str(result.get("source") or "")
        if not items:
            self._show_placeholder("在线库里还没有车辆包")
            self._set_status("在线库还是空的")
            return
        for entry in items:
            text, tip = _pack_text(entry)
            item = QListWidgetItem(text)
            item.setToolTip(tip)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.list_packs.addItem(item)
        self._set_status(f"✓ {len(items)} 个车辆包（可多选后导入）")
        if source:
            self.lbl_status.setToolTip(f"清单来源：{source}")

    def _show_placeholder(self, text: str) -> None:
        """空清单时的一行灰字说明：不可选、不参与导入（data 为空会被过滤掉）。"""
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.list_packs.addItem(item)

    def _selected_entries(self) -> list:
        entries = []
        for item in self.list_packs.selectedItems():
            entry = item.data(Qt.ItemDataRole.UserRole)
            if entry is not None:
                entries.append(entry)
        return entries

    def _import_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            self._set_status("✗ 先在清单里选一个车辆包", error=True)
            return
        self._import_entries(entries)

    def _import_all(self) -> None:
        if not self._entries:
            self._set_status("✗ 清单是空的，先点「刷新」", error=True)
            return
        self._import_entries(self._entries)

    def _import_entries(self, entries) -> None:
        share = _vehicle_share_module()
        download = getattr(share, "download_remote_pack", None) if share else None
        if not callable(download):
            self._set_status("✗ 当前版本不支持导入在线车辆", error=True)
            return

        def job() -> tuple[int, list[str]]:
            # ``download_remote_pack`` **下载 + 导入一步到位**（内部已经调
            # import_payload），返回值是 {"ok","added","updated","errors",...}。
            # 所以这里只累加，别再自己 import 一次（会重复写盘）。
            total = 0
            problems: list[str] = []
            for entry in entries:
                result = download(entry)
                changed, note = _import_summary(result)
                total += changed
                if not changed:
                    problems.append(note or str(entry.get("name") or "?"))
            return total, problems

        self._run_task(job, self._on_imported, f"正在导入 {len(entries)} 个包…")

    def _on_imported(self, payload) -> None:
        total, problems = payload if isinstance(payload, tuple) else (0, [])
        total = int(total or 0)
        # 同一个错误在多个包上会重复（"下载失败：连接超时" × N）—— 去重再数，
        # 否则状态行会变成一句被复读的长文案，面板宽度放不下。
        unique = list(dict.fromkeys(str(item) for item in problems if str(item)))
        if total:
            tail = f"，{len(unique)} 个包没导入" if unique else ""
            self._set_status(f"✓ 已导入 {total} 台{tail}")
        else:
            short = unique[0] if unique else "包里没有可导入的车辆"
            extra = f"（共 {len(unique)} 个包失败）" if len(unique) > 1 else ""
            self._set_status(f"✗ {short}{extra}", error=True)
        if unique:
            self.lbl_status.setToolTip("没导入的包：\n" + "\n".join(unique[:6]))
        self._refresh_upload_combo()
        self.vehicles_changed.emit(total)

    # -- 上传 ------------------------------------------------------------

    def _refresh_upload_combo(self) -> None:
        """上传用的车辆下拉：跟着本机档案走（导入完要重建一次）。"""
        current = self.cmb_upload.currentData()
        self.cmb_upload.blockSignals(True)
        self.cmb_upload.clear()
        for name in customization.vehicle_names():
            self.cmb_upload.addItem(name, name)
        if self.cmb_upload.count() == 0:
            self.cmb_upload.addItem("（本机还没有车辆）", "")
            self.cmb_upload.setEnabled(False)
        else:
            self.cmb_upload.setEnabled(True)
            index = self.cmb_upload.findData(current)
            self.cmb_upload.setCurrentIndex(index if index >= 0 else 0)
        self.cmb_upload.blockSignals(False)

    def _show_note(self, text: str, error: bool = True) -> None:
        """上传区的小字提示（令牌状态 / 索引登记结果）：有问题才出现。

        跟顶上那条 ``lbl_status`` 分开：那条是"这次操作成功还是失败"，
        这条是"有个附带情况要你知道"，两条同时存在时不该互相盖掉。
        """
        self.lbl_note.setText(text)
        self.lbl_note.setToolTip(text)
        color = qcolor("alert_red") if error else qcolor("ink_2")
        self.lbl_note.setStyleSheet(f"color: {color.name()};")
        self.lbl_note.setVisible(True)

    def _report_token_state(self, loaded: bool) -> None:
        """令牌状态的**小字**提示（红字，只在有话说的时候出现）。

        两种要告诉用户的情况：
        - ``load_token()`` 拿回空串但 ``LAST_TOKEN_ERROR`` 非空 → 存过、但这台机器
          解不开（换账户 / 配置被拷走），得重新填；
        - ``save_token()`` 返回 False → 加密不可用，令牌只在本次运行有效。
        """
        share = _vehicle_share_module()
        hint = str(getattr(share, "LAST_TOKEN_ERROR", "") or "") if share else ""
        if not hint:
            self.lbl_note.setVisible(False)
            return
        text = (f"已存的令牌解不开，请重新填写（{hint}）" if not loaded
                else f"令牌只在本次运行有效（{hint}）")
        self._show_note(text)

    def _clear_token(self) -> None:
        """清掉本机保存的令牌（只动这台机器，不动仓库）。"""
        share = _vehicle_share_module()
        clear = getattr(share, "clear_token", None) if share else None
        if not callable(clear):
            self._set_status("✗ 当前版本不支持清除令牌", error=True)
            return
        try:
            clear()
        except Exception as exc:
            self._set_status(f"✗ 清除令牌失败：{exc}", error=True)
            return
        self.edit_token.clear()
        self.lbl_note.setVisible(False)
        self._set_status("✓ 已清除本机保存的令牌")
        self.btn_clear_token.setText("✓ 已清除")
        QTimer.singleShot(1800, lambda: self.btn_clear_token.setText("清除令牌"))

    def _reset_overwrite(self) -> None:
        """撤回"确认覆盖"的待定态（切换车辆 / 刷新清单 / 上一次成功之后）。"""
        if getattr(self, "_pending_overwrite", False):
            self._pending_overwrite = False
            self.btn_upload.setText("上传")
            self.btn_upload.setToolTip(
                "把选中的车辆结构推到在线库（Token 由系统加密存在本机）")

    def _upload(self, overwrite: bool = False) -> None:
        share = _vehicle_share_module()
        upload = getattr(share, "upload_pack", None) if share else None
        if not callable(upload):
            self._set_status("✗ 当前版本不支持上传", error=True)
            return
        name = self.cmb_upload.currentData()
        if not name:
            self._set_status("✗ 先选一台要上传的车", error=True)
            return
        token = self.edit_token.text().strip()
        if not token:
            self._set_status("✗ 先填 GitHub Token（需要 contents:write 权限）", error=True)
            self.edit_token.setFocus()
            return
        # 上一次点出来的"确认覆盖"：正常情形下用户再点一次「上传」就是确认。
        if getattr(self, "_pending_overwrite", False) and not overwrite:
            overwrite = True
        self._reset_overwrite()
        self.lbl_note.setVisible(False)       # 上一次的旧提示先撤掉
        try:
            doc = _vehicle_doc([name])
        except Exception as exc:
            self._set_status(f"✗ 打包失败：{exc}", error=True)
            return
        # token 交给 vehicle_share 存（DPAPI 加密）；存不住时它返回 False 并把
        # 原因放在 LAST_TOKEN_ERROR 里 —— 那种情况下只说"本次运行有效"，
        # **不降级成明文落盘**。
        save_token = getattr(share, "save_token", None)
        if callable(save_token):
            try:
                kept = bool(save_token(token))
            except Exception as exc:
                kept = False
                app_log.warning(f"保存车辆上传令牌失败：{exc}")
            if not kept:
                self._report_token_state(loaded=True)
        self._run_task(lambda: upload(doc, token, overwrite), self._on_uploaded,
                       f"正在上传「{name}」…")

    def _on_uploaded(self, result) -> None:
        """上传结果：同名文件已存在时**在按钮上就地确认**，再点一次才覆盖。

        为什么不用 QMessageBox：那是系统大问号框，跟这套黑白灰面板不是一路；
        而且"再点一次就是确认"这种就地确认不打断手（同「检查更新」那一套手感）。
        """
        if not isinstance(result, dict):
            self._set_status("✓ 已上传")
            return
        if result.get("need_confirm"):
            file = str(result.get("file") or "同名文件")
            self._pending_overwrite = True
            self.btn_upload.setText("确认覆盖？")
            self.btn_upload.setToolTip(
                f"{file} 在仓库里已经存在。再点一次「确认覆盖？」"
                f"就会替换服务器上的版本。\n{result.get('error') or ''}".strip())
            self._set_status(f"仓库里已有 {file}，再点一次即覆盖")
            return
        if not result.get("ok"):
            self._set_status(f"✗ {result.get('error') or '上传失败'}", error=True)
            return
        index = result.get("index")
        if isinstance(index, dict) and index.get("ok") is False:
            # 两条结论分开说：包确实传上去了，只是索引没登记（不影响下次导入？
            # 影响 —— 索引里没有它别人就看不到，所以红字单独一行）。
            self._set_status("✓ 包已上传")
            self._show_note(f"索引登记失败：{index.get('error') or '未知原因'}")
            return
        file = str(result.get("file") or "")
        self._set_status("✓ 已上传并登记到在线车辆库" + (f"（{file}）" if file else ""))

    def _open_browser_upload(self) -> None:
        share = _vehicle_share_module()
        url = ""
        builder = getattr(share, "upload_url_for_web", None) if share else None
        if callable(builder):
            try:
                url = str(builder() or "")
            except Exception as exc:
                app_log.warning(f"取浏览器上传地址失败：{exc}")
        if not url:
            self._set_status("✗ 没有配置浏览器上传地址", error=True)
            return
        QDesktopServices.openUrl(QUrl(url))

    def _open_new_token_page(self) -> None:
        """打开 GitHub 创建令牌页（经典 PAT，URL 预填 repo 权限）。

        为什么用经典 PAT 而不是 fine-grained：经典 PAT 的创建页支持 ``?scopes=``
        预填 —— 点开就已经勾好 ``repo``，用户只要 Generate → 复制 → 粘回来。
        fine-grained 没有官方 URL 预填，反而多好几步。
        """
        QDesktopServices.openUrl(QUrl(
            "https://github.com/settings/tokens/new"
            "?scopes=repo&description=CQUFSAE-SerialAssistant-vehicles"))


# ---------------------------------------------------------------- 选车号动画
# 用户定稿（2026-11-13「推倒重来」）：点击切换车号 / 改模组数时，把「车号以下、
# 屏幕正在显示的设置区」整片抓成位图 → 真高斯模糊 → 以全不透明（α=1）盖在上方，
# 立刻把设置区换成切换后的新数据（清晰、已就位），停留一小段后再让模糊层淡出
# （α→0），露出清晰的新数据。不做任何让位 / 占位行 / 逐模组交叉淡入（那些都是
# 上一代被推翻的方案）。
_VEH_BLUR_TOTAL_MS = 520      # 总时长 = HOLD + FADE
_VEH_BLUR_HOLD_MS = 220       # 整片模糊停留在 α=1 的时长（糊住旧画面）
_VEH_BLUR_FADE_MS = 300       # α 从 1 → 0 的淡出时长（露出清晰新数据）
_VEH_SIGMA_MAX = 24.0         # 高斯强度（物理像素；图像按 1/4 计算，内部 σ/4）
_VEH_BLUR_DIV = 4             # 1/4 降采样：全分辨率 6 档 551ms → 1/4 仅 26~31ms
_VEH_TICK_MS = 8              # 补间节拍（≈120Hz，与旧方案一致）



class _FadeOverlay(QWidget):
    """盖在一小块区域上的位图（可带高斯档），由外部时钟逐帧喂帧。

    为什么需要：车辆节切车号 / 改模组数时 `_rebuild_module_rows` 会把整组行控件
    删掉重建。2026-11-07 用户定稿的观感是「其他元素慢慢下移留出位置，参数整块
    高斯模糊淡入」，所以这里不只做 alpha：外部会把**预生成好的高斯位图**按当前
    sigma 混着喂进来，本控件只负责 fillRect + drawPixmap。

    ⛔ 不用 QGraphicsOpacityEffect / QGraphicsBlurEffect（项目铁律：控件级离屏
       合成会抽搐）。模糊全部在 numpy 里预生成，绘制阶段只有两次 drawPixmap。
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._items: list[tuple[QPixmap, float]] = []
        self._alpha = 0.0
        self._bg: QColor | None = None
        self._visible_h: int | None = None
        # ⛔⛔ 2026-11-07 修「车号选择残影 / 淡化发灰」：覆盖层必须是**透明背景**，
        #    否则 Qt 会先用 palette 的 Window 颜色把整块刷成不透明实底，再叠我们的
        #    α 图 —— 淡化中途看到的是「实底 + 半透明图」而非「真行 + 半透明图」，
        #    既发灰又会在真行与覆盖层错位时显得像残影。透明背景才能和底下内容做
        #    真正的 α 合成。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def show_frame(self, items, rect: QRect, alpha: float,
                   bg: QColor | None = None,
                   visible_h: int | None = None) -> None:
        """items = [(位图, 权重)]；bg 不为 None 时先整块铺底色。

        visible_h 不为 None 时只画位图**顶部**这么多逻辑像素（源图按设备像素
        等比裁剪、1:1 映射，不压扁），其余区域保持透明让底下真控件正常显示。
        None = 整块全画（车号切换动画走这条）。
        """
        try:
            self._items = [(pix, float(w)) for pix, w in items if w > 0.004]
            self._alpha = max(0.0, min(1.0, float(alpha)))
            self._bg = bg
            self._visible_h = (None if visible_h is None
                               else max(0, int(visible_h)))
            if self.geometry() != rect:
                self.setGeometry(rect)
            if not self.isVisible():
                self.show()
            self.raise_()
            self.update()
        except RuntimeError:              # 面板已关，C++ 对象已销毁：静默收尾
            self.clear()

    def clear(self) -> None:
        self._items = []
        self._bg = None
        self._alpha = 0.0
        self._visible_h = None
        try:
            self.hide()
        except RuntimeError:
            pass

    def paintEvent(self, event) -> None:          # noqa: N802 (Qt 命名)
        if not self._items and self._bg is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._bg is not None:
            painter.fillRect(self.rect(), self._bg)
        full_h = self.height()
        for pix, weight in self._items:
            painter.setOpacity(self._alpha * weight)
            if self._visible_h is None or full_h <= 0:
                painter.drawPixmap(self.rect(), pix)
                continue
            h = min(int(self._visible_h), full_h)
            if h <= 0:
                continue
            # pix 是 2× 设备像素位图、self 是逻辑像素：按高度比换算 dpr，
            # 只取源图顶部对应的一段，用三参数重载做 1:1 映射（⛔ 绝不把整张
            # 5 行位图压扁到当前高度 —— 那会变形）。
            dpr = pix.height() / float(full_h)
            target = QRectF(0, 0, self.width(), h)
            source = QRectF(0, 0, pix.width(), h * dpr)
            painter.drawPixmap(target, pix, source)


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
        # 先给个初值，真正的下限在本函数末尾按内容算出来（PANEL_MIN_W 兜底）
        self.setMinimumWidth(PANEL_MIN_W)
        self._on_glass = on_glass
        self._on_buttons = on_buttons
        self._on_custom = on_custom
        self._button_switches: dict[str, Switch] = {}
        # 构造期的首次 _sync_vehicle_rows **不许**跑过渡动画（面板还没上屏，
        # 动画只会让打开面板那一瞬间的行数是"长到一半"的样子）。
        self._veh_allow = False
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
        # ⛔ **不要**改成 WrapLongRows：它确实把 body 最小宽从 579 压到 392
        #    （2026-10-31 实测），但 QFormLayout 只在**字段**放不下时才把标签挪到
        #    上方，标签自己放不下时是**直接截断**—— 面板一窄，"材质强度""暂停定位
        #    行数（最大化）"这些标签就被切掉左边（截图见汇报）。面板的最小宽度
        #    由"最宽标签 187 + 间距 18 + 最宽字段（模组行）362"决定，想真正变窄
        #    只能动这两项，见 _build_vehicle_section 里的说明。
        self.form = form
        body_layout.addLayout(form)

        # 2026-11-03 用户：「不要喧宾夺主，把车辆信息放在设置界面的最上面」——
        # 车辆（电池箱）是整个接收区排版的**前提**，放第一屏；主题/材质/开屏动画
        # 都排到它后面。
        self._build_vehicle_section(form)

        self.cmb_theme = ChevronComboBox()
        self.cmb_theme.setFixedWidth(FIELD_W)
        for label, value in THEME_MODES:
            self.cmb_theme.addItem(label, value)
        index = self.cmb_theme.findData(theme_mode)
        self.cmb_theme.setCurrentIndex(index if index >= 0 else 0)
        form.addRow(self._label("主题"), self.cmb_theme)

        # 定制项的控件登记表要在**这一节之前**建好：「界面材质」虽然是
        # customization 注册表里的枚举项，但它的行跟着「材质强度」一起摆在上面
        # （见文件头说明），会先于 _build_custom_section 登记进这两张表。
        self._custom_widgets: dict[str, QWidget] = {}
        # 面板刚建好时每个控件的**显示值**（custom_values 的基准，见 _snapshot_custom_baseline）
        self._custom_baseline: dict[str, object] = {}
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
            self.cmb_material.setFixedWidth(FIELD_W)
            self.cmb_material.currentIndexChanged.connect(
                lambda _idx, s=material_spec, c=self.cmb_material:
                self._on_choice_changed(s, c))
            material_label = self._label(material_spec["label"])
            form.addRow(material_label, self.cmb_material)
            self._custom_widgets["material"] = self.cmb_material
            self._custom_rows["material"] = (material_label, self.cmb_material)

        # 「开屏动画」：预设（assets/splash/ 下扫出来的）/ 跟随主题 / 自定义文件。
        # 只在这块做 UI；真正的播放/配置解析全在 splash_anim.py（接口层）。
        self._build_splash_section(form)

        # 「车辆」一节已经在**最上面**建好（见本方法开头，用户 2026-11-03：
        # 「不要喧宾夺主，把车辆信息放在设置界面的最上面」）。
        #  ⚠️ 与 customize 的说明一致：车辆档案**不走 register_* 注册表**（它不是
        #     标量），所以这一节是手写的，其余定制项仍按 specs() 自动生成。

        # 「定制功能」：BMS 专用配置的统一管理入口。控件**按注册表动态生成**
        # （customization.specs()），以后加定制配置这里一行都不用改。
        self._build_custom_section(form)
        # 控件全部建好 → 立刻把"显示值"记成基准（见 _snapshot_custom_baseline：
        # 滑杆被量程夹小时，不能让"确定"把夹后的值当成用户改动落盘）。
        self._snapshot_custom_baseline()
        # 车辆一节的**首次同步**必须等「定制功能」建完：选车后要隐藏旧的三件套
        # （module_count / cells_per_module / temps_per_module），那三颗控件是
        # 上面按注册表生成的（见 _build_custom_section 里的 _custom_rows）。
        self._sync_vehicle_rows()

        if button_specs:
            self._build_button_section(form, button_specs, set(hidden_buttons))

        # 日志一节放在**界面按钮的下方**（用户点名），整段一起随面板滚动
        self._build_log_section(form)

        # 「会话数据库」：SQLite 每帧落库的入口（路径 / 近 N 天导出 / 统计）
        self._build_session_db_section(form)

        # 「协议」：解析规则可编辑（换一台 BMS 不用改 Python、不用重启）
        self._build_protocol_section(form)

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
        self.lbl_version = QLabel(f"CQUFSAE 串口助手 v{VERSION}")
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
        # 常驻而不是每次现建）。并发/超时/代理分层统一交给 updater.UpdateChecker
        # （见 _check_update），这里不再自己维护一套计时器。
        self._nam = QNetworkAccessManager(self)
        self._checker = None

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

        # ⛔ 560 只是**下限**，不代表够用：模块行（Module N / 电芯数量 /
        #    温度采集数量）在 560 宽下右侧输入框会被裁掉 —— 实测内容最小宽度
        #    603，探针数出 38 个控件越界 31~43px（devtools/_probe_settings_clip.py）。
        #    滚动区是 widgetResizable + 横向滚动条常关，越界部分直接被切，
        #    用户看到的是"温度采集数量那个框只剩一半"。
        #    这里按 body 的最小宽度把面板撑到刚够，窄屏才由竖向滚动条接管。
        #    ⚠️ body 的最小宽 = 最宽标签(187) + 18 + 最宽字段(模组行 362) + 12，
        #    想再窄必须动这两项（见 _build_vehicle_section 末尾的说明）。
        # 实测「面板宽 − body 宽 = 58」：外层边距 48 + 竖向滚动条 12 − 边框重叠 2。
        # 少算这几十像素的后果就是右侧控件被裁（老坑，见上面那段说明）。
        self._veh_allow = True          # 构造完成：之后的切换才有过渡
        chrome = 58
        self.setMinimumWidth(max(PANEL_MIN_W,
                                 body.minimumSizeHint().width() + chrome))

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
            # 收场：正在跑的"选车号过渡"必须先落到终态（停表 + 撤整片模糊覆盖层），
            # 否则收场动画那 260ms 里会把模糊帧留在面板上。
            self._veh_stop()
            hook(bool(result))
            return
        super().done(result)

    # ------------------------------------------------------------ 车辆

    # 旧的全局网格三件套：选车后整行隐藏（一套参数说话，见 _sync_vehicle_rows）。
    # 名字与 customization 里那三条 register_int 一一对应。
    _LEGACY_GRID_KEYS = ("module_count", "cells_per_module", "temps_per_module",
                         "volt_rows", "temp_rows")

    # ------------------------------------------------------------ 开屏动画

    def _build_splash_section(self, form: QFormLayout) -> None:
        """开屏动画：预设 / 跟随主题 / 自定义文件 + 预览。

        预设是 **扫** ``assets/splash/`` 得来的（用户把新动画丢进去、重开面板就能
        看到）；真正的解析与播放全在 ``splash_anim.py``，这里只做选择与预览。
        """
        import splash_anim
        self._splash_anim = splash_anim
        # 2026-11-03 用户：「既然动画选择里面有无动画选项了那就不需要开屏动画
        # 开关了」—— 去掉总开关，只留下拉（第一项就是「无」）。
        self.cmb_splash = ChevronComboBox()
        self.cmb_splash.addItem("无（直接进主界面）", "none")
        # 只列**方案**（planA / planB）：同一方案的浅色/深色是变体，播放时按当前
        # 主题自动挑（用户 2026-11-03：「只保留 planA、planB 这些选项，然后系统
        # 自动选择当前主题相同的动画」）。
        for plan in splash_anim.plans():
            self.cmb_splash.addItem(plan["label"], plan["id"])
        self.cmb_splash.addItem("自定义文件…", "custom")
        # 宽度按**最宽条目**算：固定 FIELD_W 会把"跟随主题（自动选浅色/深色）"
        # 截成"跟随主题（自动选浅色"（用户 2026-11-03 截图点名"框小了"）。
        fm = self.cmb_splash.fontMetrics()
        widest = max((fm.horizontalAdvance(self.cmb_splash.itemText(i))
                      for i in range(self.cmb_splash.count())), default=0)
        self.cmb_splash.setFixedWidth(max(FIELD_W, widest + 80))
        index = self.cmb_splash.findData(splash_anim.choice())
        self.cmb_splash.setCurrentIndex(index if index >= 0 else 1)
        self.cmb_splash.currentIndexChanged.connect(self._on_splash_changed)
        form.addRow(self._label("开屏动画"), self.cmb_splash)

        path = splash_anim.custom_path()
        self.lbl_splash_path = QLabel(Path(path).name if path else "未选择文件")
        self.lbl_splash_path.setObjectName("groupCaption")
        self.lbl_splash_path.setMinimumWidth(1)
        self.lbl_splash_path.setToolTip(path or "选「自定义文件…」后在这里显示文件名")
        btn_pick = InkButton("选择文件…", variant="ghost")
        btn_pick.clicked.connect(self._on_splash_pick)
        btn_preview = InkButton("预览", variant="ghost")
        btn_preview.clicked.connect(self._on_splash_preview)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.lbl_splash_path, 1)
        row.addWidget(btn_pick)
        row.addWidget(btn_preview)
        form.addRow(self._label("动画文件"), self._row(row))

    def _on_splash_changed(self, index: int) -> None:
        value = str(self.cmb_splash.itemData(index) or "theme")
        self._splash_anim.save_choice(value)

    def _on_splash_pick(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择开屏动画", str(Path.home()),
            "动画文件 (*.mp4 *.mov *.mkv *.webm *.avi *.gif *.webp "
            "*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        self._splash_anim.save_custom_path(path)
        self._splash_anim.save_choice("custom")
        index = self.cmb_splash.findData("custom")
        if index >= 0:
            self.cmb_splash.blockSignals(True)
            self.cmb_splash.setCurrentIndex(index)
            self.cmb_splash.blockSignals(False)
        self.lbl_splash_path.setText(Path(path).name)
        self.lbl_splash_path.setToolTip(path)

    def _on_splash_preview(self) -> None:
        # 预览按下拉里当前选中的主题挑变体（与系统/固定浅深一致），让用户在设置里
        # 切主题后能立刻看到对应的开屏，不用重启软件。
        mode = str(self.cmb_theme.currentData() or "system")
        scheme = detect_system_scheme() if mode == "system" else mode
        preset = self._splash_anim.configured(scheme)
        if preset is None:
            QMessageBox.information(
                self, "开屏动画",
                "当前没有可预览的动画：请选择一个预设，或先「选择文件…」。")
            return
        # 预览自己就是一条独立置顶窗口，点一下/按任意键可跳过；不动主窗口。
        self._splash_anim.play(preset)

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

        # ---- 赛季 / 车号行（用户 2026-10-22 定案）----
        # 原来是「车辆 [E74-2026 ▾] [新建] [删除]」。现在拆成
        #     赛季：[2026]   车号：[E74 ▾]   [＋]
        # 删除挪进「车号」的弹层：每条右端一个 ×，点了要二次确认（见
        # ChevronComboBox.delete_requested / _PopupItemDelegate._paint_delete）。
        # 主行只留"选"和"加"两个正面动作，删除这种破坏性操作不放在手边。
        #
        # ⚠️ **数据格式不变**：车名仍是「车号-赛季」（如 E74-2026），老档案原样
        #    可用；赛季与车号只是从名字里拆出来分别编辑（customization.split_vehicle）。
        self.spin_vehicle_season = ChevronSpinBox()
        self.spin_vehicle_season.setRange(2000, 2100)
        # 默认当年（不是量程下限）：没有赛季的老档案也得显示一个像样的年份，
        # 否则一开面板就是 2000（看着像坏了）。
        self.spin_vehicle_season.setValue(QDate.currentDate().year())
        self.spin_vehicle_season.setFixedWidth(SEASON_SPIN_W)
        self.spin_vehicle_season.set_arrows_visible(False)
        # 数字居中（用户 2026-10-23），和下面的电芯/温度数量框同一观感
        self.spin_vehicle_season.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.spin_vehicle_season.setToolTip(
            "赛季 = 车名的后半段年份（E74-2026 的 2026）。\n"
            "改赛季只决定「车号」里列哪些车，不会自动改到别的车。")
        self.spin_vehicle_season.valueChanged.connect(self._on_season_changed)

        self.cmb_vehicle_number = ChevronComboBox()
        # 就地改写车号 = 改名（沿用"选车与改名合一"的老口径，回车/失焦生效）
        self.cmb_vehicle_number.setEditable(True)
        self.cmb_vehicle_number.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # ⚠️ 最小宽度必须给 **1**（2026-10-31）：可编辑 QComboBox 的
        #    minimumSizeHint 会按**内容**算（实测 102，minWidth 84 也只是把它
        #    压到 84），它自己撑住整行，面板就再也窄不下去。给 1 = 允许布局把
        #    它压到很小，宽度由 stretch 吃剩余空间决定（面板宽时它照样展开）。
        #    ⛔ 别写 0（Qt 把 0 当"没设过"，会退回 minimumSizeHint）。
        self.cmb_vehicle_number.setMinimumWidth(1)
        # 上限让"赛季/车号"两个框看着是一套的：面板拉宽时余量留给右侧空白，
        # 而不是把车号框拉成一条长条（用户 2026-10-22："优化一下这些框的大小"）。
        # 同时**不给 stretch**（下面是 pick_row.addWidget(..., 1) 那处被去掉了）：
        # 车号是"E74"这种短内容，带 stretch 会把框撑到 180px、看着比内容大一圈
        # （用户点名"这个框不需要这么大"）。去掉 stretch 后它按内容定宽，多余
        # 空间统一交给行尾的 addStretch 推到右边。
        self.cmb_vehicle_number.setMaximumWidth(160)
        # 车号居中（用户 2026-10-23）：显示与输入都居中，和旁边两个框一致
        self.cmb_vehicle_number.lineEdit().setAlignment(Qt.AlignmentFlag.AlignHCenter)
        # ⛔ 不设这条，sizeHint 会按最宽条目（「（未选择）」）算，把整行顶宽、
        #    右侧「＋」被裁。按"最短 4 个字"算，宽度由 stretch 吃剩余空间。
        self.cmb_vehicle_number.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_vehicle_number.setMinimumContentsLength(4)
        self.cmb_vehicle_number.set_item_delete_enabled(True)
        self.cmb_vehicle_number.setToolTip(
            "车号 = 车名的前半段（E74-2026 的 E74）。\n"
            "直接改写框里的车号 = 改名（回车或点别处生效）；\n"
            "展开后每条右端的 × 删除该车号（会二次确认）")
        # ⛔⛔ 2026-11-08 根治「车号被反复取消选中（0↔5）」：用 `activated`（**只有用户
        #    真正从下拉里选一项才发**），而不是 `currentIndexChanged`（`_refresh_number_combo`
        #    重建下拉时 setCurrentIndex/clear 也会发，且 blockSignals 挡不住它自激）——
        #    旧连接让"程序化重建 → 误当成用户取消选车 → 又重建"形成死循环，把模组行
        #    反复塌陷成 0 行（用户截图：模组数 5 却没有 Module 行 + 陈旧帧残影）。
        self.cmb_vehicle_number.activated.connect(self._on_vehicle_picked)
        self.cmb_vehicle_number.delete_requested.connect(self._on_vehicle_delete_requested)
        _number_line = self.cmb_vehicle_number.lineEdit()
        if _number_line is not None:
            _number_line.editingFinished.connect(self._on_vehicle_number_typed)

        self.btn_vehicle_new = IconButton(
            GLYPH_ADD, "在当前赛季下新建车号", size=28, glyph_size=14,
            hover_fill=False)
        # ⛔ 用户 2026-10-22：「这个加号不需要有边框」—— 不要用 ghost 药丸按钮
        #    （浅底 + 描边），改成**无静息底、无悬停底**的图标按钮：反馈只靠
        #    字形由 ink_2 提亮到 ink（和底栏那颗齿轮同一档）。
        self.btn_vehicle_new.setToolTip(
            "在当前赛季下新建车号：默认「新车 N」，结构按当前配置预填"
            "（旧模组个数 >0 就用它，否则 3 模组 × 25 电芯 × 8 温度）")
        self.btn_vehicle_new.clicked.connect(self._on_vehicle_new)

        # 改名失败时的短提示：平时藏起来（不占宽度），出错那两秒才出现
        # ⚠️ 提示文字必须**短**（同 MCP 那行），长文案会被裁掉
        self.lbl_vehicle_hint = QLabel("")
        self.lbl_vehicle_hint.setFont(ui_font(10.5))
        self.lbl_vehicle_hint.setObjectName("groupCaption")
        self.lbl_vehicle_hint.setMinimumWidth(1)      # 不能是 0（同日志行，见上）
        self.lbl_vehicle_hint.setVisible(False)

        # 模组个数（用户 2026-10-22 第三轮：赛季 / 车号 / 模组个数**并到一排**；
        # 2026-10-31 又**拆出去**：它顶宽 144px，是选车行里最贵的一格，而它本质
        # 是"这台车有几个模组"，跟下面「逐模组」是同一件事 —— 挪到 Module 1
        # 上方那一行，选车行立刻窄一截（用户点名"减少设置界面的宽度"）。
        self.spin_vehicle_modules = ChevronSpinBox()
        self.spin_vehicle_modules.setRange(0, 64)
        self.spin_vehicle_modules.setFixedWidth(MODULE_COUNT_W)
        self.spin_vehicle_modules.set_arrows_visible(False)
        self.spin_vehicle_modules.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.spin_vehicle_modules.setToolTip(
            "模组个数 = 下面「Module i」的行数；新增的模组沿用最后一个模组的配置，"
            "缩短则从末尾截断")
        self.spin_vehicle_modules.valueChanged.connect(self._on_vehicle_modules_changed)

        # 行内顺序：赛季：[..]  车号：[..]  ＋
        # 另外**去掉左侧的「车辆」行标签** —— 这一排自己已经说清是什么，多一个
        # 行标签只会把整排往右推（它还占着表单的标签列宽）。
        # 模组数（用户 2026-11-01：放回「车号」这一行，与赛季/车号/＋同排均匀分布）
        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        pick_row.addStretch(1)
        pick_row.addWidget(self._inline_caption("赛季："))
        pick_row.addWidget(self.spin_vehicle_season)
        pick_row.addSpacing(2)
        pick_row.addWidget(self._inline_caption("车号："))
        # ⛔ 不给 stretch：车号内容短（"E74"），带 stretch 会把它撑到 160px 上限、
        # 看着比内容大一圈。去掉后按内容定宽。
        pick_row.addWidget(self.cmb_vehicle_number)
        pick_row.addWidget(self.btn_vehicle_new)
        pick_row.addWidget(self.lbl_vehicle_hint)
        # 用户 2026-11-01 第二轮：「模组数要写上」—— 挪进这一行时把行标签
        # 弄丢了，框里只剩数字；补回行内小标签（与"赛季：""车号："同款）。
        pick_row.addWidget(self._inline_caption("模组数："))
        pick_row.addWidget(self.spin_vehicle_modules)
        # 整行均匀分布（两端留白一致）：和下方「导出/导入/在线库」那一排
        # 的分布感对齐（用户 2026-11-01："把这两行均匀分布"）。
        pick_row.addStretch(1)

        # 无行标签：单参 addRow 让它**横跨两列**，左边缘与其它行的标签列对齐
        self._vehicle_field = self._row(pick_row)
        form.addRow(self._vehicle_field)

        # ---- 车辆共享：导出 / 导入 / 在线库（2026-10-31 用户要求）----
        # 摆在"选车行"下面、"模组 i"上面：上面是"我在编辑哪台车"，下面是那台车的
        # 结构；共享是把**整车档案**搬来搬去，夹在中间最顺，也不会把逐模组那一片
        # （长度随模组数变）挤到按钮后面去。
        self.btn_vehicle_export = InkButton("导出车辆", variant="ghost")
        self.btn_vehicle_export.setToolTip(
            "把车辆档案（全部）另存成一个 .cqv.json 包，方便发给队友导入")
        self.btn_vehicle_export.clicked.connect(self._export_vehicles)
        self.btn_vehicle_import = InkButton("导入车辆", variant="ghost")
        self.btn_vehicle_import.setToolTip(
            "从别人给你的 .cqv.json 包导入车辆；同名的车按包里的结构更新")
        self.btn_vehicle_import.clicked.connect(self._import_vehicles)
        self.btn_vehicle_library = InkButton("在线车辆库", variant="ghost")
        self.btn_vehicle_library.setToolTip(
            "打开在线车辆库：浏览/导入别人的车，或把自己的车传上去")
        self.btn_vehicle_library.clicked.connect(self._open_vehicle_library)
        # 三颗**并排同一行**（用户 2026-10-31 明确要求：把这三个弄到同一行），
        # 文案去掉省略号。反馈文案（"✓ N 台"/"导入失败"…）一起算进等宽 ——
        # 否则一显示反馈，那一颗就被 setMinimumSize 顶宽、三等宽当场破掉。
        cell_w = _equalize_buttons(
            (self.btn_vehicle_export, self.btn_vehicle_import,
             self.btn_vehicle_library),
            extra_texts=("✓ 00 台", "✓ +00 台", "导出失败", "导入失败"))
        # 用户 2026-11-01 第二轮：「这个分布要均匀啊」—— 之前三颗锁死等宽后
        # 靠左挤成一团、右侧留一大截空白。改成**三颗等伸长平分整行**：宽度
        # 天然一致、两处间距一致、整行铺满。
        for button in (self.btn_vehicle_export, self.btn_vehicle_import,
                       self.btn_vehicle_library):
            # 2026-11-03 用户：「注意新增的按钮大小问题」—— 不再拉满整行，
            # 按等宽基准给死（反馈文案也不会撑宽），左对齐紧凑排列。
            button.setFixedWidth(cell_w)
        share_row = QHBoxLayout()
        share_row.setContentsMargins(0, 0, 0, 0)
        share_row.setSpacing(10)
        # 2026-11-03 用户：「这个要均匀排布，不要挤到一起」—— 四个 stretch 把三颗
        # 按钮**等距铺开**（按钮本身仍是紧凑等宽，不拉满）。
        share_row.addStretch(1)
        share_row.addWidget(self.btn_vehicle_export)
        share_row.addStretch(1)
        share_row.addWidget(self.btn_vehicle_import)
        share_row.addStretch(1)
        share_row.addWidget(self.btn_vehicle_library)
        share_row.addStretch(1)
        # 单参 addRow：横跨两列（左边缘与本节其它行对齐）
        form.addRow(self._row(share_row))

        # 模块还没落地时按钮置灰 + 说清原因（别让用户点了没反应）
        share = _vehicle_share_module()
        for button, entry, label in (
                (self.btn_vehicle_export, "export_to_file", "导出"),
                (self.btn_vehicle_import, "import_from_file", "导入"),
                (self.btn_vehicle_library, "fetch_remote_index", "在线库")):
            if share is None or not callable(getattr(share, entry, None)):
                button.setEnabled(False)
                button.setToolTip(
                    f"车辆{label}功能还没就绪（vehicle_share.{entry} 未提供），"
                    "当前版本只能在本机使用车辆档案")

        # 「模组数」已挪回上方「车号」那一行（用户 2026-11-01），这里不再单独成行。

        # 逐模组行插在这个位置（= 本节之后、「定制功能」之前）。
        # ⚠️ 用 insertRow/removeRow 按**行号**增删，不用容器套布局：套一层容器的话
        #    里面的标签会落到字段列那一列上，跟本节其它行标签错开一大截。
        self._module_row_base = form.rowCount()
        self._module_rows: list[tuple[QLabel, QWidget]] = []
        # 当前**已经画在屏幕上**的逐模组结构（cells/temps 原样）。用来判断"选车后
        # 结构没变就不用重建"—— 否则每次选同一台/同结构车都会把下面整片重排一遍，
        # 模组数框下面的内容会"跳"一下（用户点名"我选择车辆之后模组数不要跳变"）。
        self._shown_cells: list[int] = []
        self._shown_temps: list[int] = []

    def _current_season(self) -> str:
        return str(self.spin_vehicle_season.value())

    def _refresh_number_combo(self) -> None:
        """按**当前赛季**重建车号下拉（当前项跟随 customization.current_vehicle）。

        第一项永远是「（未选择）」：少了它，下拉会自己停在第一台车上，用户会
        以为"已经选上了"，而实际排版用的是老的三件套参数（踩过）。
        """
        season = self._current_season()
        names = customization.vehicle_names_in_season(season)
        current = customization.current_vehicle()
        combo = self.cmb_vehicle_number
        # ⛔ 保存并**恢复**原来的屏蔽状态（不是硬置 False）：外层 _on_vehicle_picked
        #    在整段同步期间把下拉信号挡住了，这里若硬置 False 会把外层的屏蔽提前解除，
        #    重建后这次 setCurrentIndex 立刻吐 currentIndexChanged → 自激 0↔5 反复。
        _prev_block = combo.blockSignals(True)
        combo.clear()
        # ⚠️ 占位文字要**短**：560 宽的面板里车号框只有 ~96px，编辑框 48px，
        #    "（未选择）"会被截成"（未选"。三个字刚好放得下。
        combo.addItem("未选择", "")
        for name in names:
            combo.addItem(customization.split_vehicle(name)[1], name)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(_prev_block)
        # ⛔ 可编辑下拉 setCurrentIndex/setEditText 之后光标停在**末尾**，条目比框宽
        #    时显示的是尾巴（实测「（未选择）」只剩「择）」）—— 和老坑同一条。
        line = combo.lineEdit()
        if line is not None:
            line.setCursorPosition(0)

    def _sync_vehicle_rows(self) -> None:
        """刷新车辆区（**带重入闸**）。

        ⛔⛔ 2026-11-08 根治「打开设置/切车号时 _veh_start 被连开 5 次」：
        多个信号（赛季 / 模组数 / 车号下拉 / 布局回调）在刷新过程中会互相触发，
        导致本方法**重入**（实测日志：47ms 内 5 次 START，nrows 3→5→2→0→3，
        h0 全为 0），每次都起一整段让位动画、互相打断，最后落在一个**错误的行数**
        上 —— 用户看到「模组数 5 却没有 Module 行 / 行错位 + 残影」。
        用一个重入闸把嵌套调用折叠成一次，彻底断掉这条链。
        """
        if getattr(self, "_veh_syncing", False):
            self._veh_dbg("SYNC_REENTRY_SKIP")
            return
        self._veh_syncing = True
        try:
            self._sync_vehicle_rows_impl()
        finally:
            self._veh_syncing = False

    def _sync_vehicle_rows_impl(self) -> None:
        """按"当前有没有选车"整体摆一次：整行显隐 + 填值 + 重建逐模组行。

        这是本节**唯一**的刷新入口（选赛季 / 选车号 / 新建 / 删除 / 改名 /
        改模组个数之后都调它）：分散着各改一半，最容易出现"下拉是新名字、
        数字框还是旧结构"。
        """
        current = customization.current_vehicle()
        profile = customization.active_profile()
        has_vehicle = bool(current)
        cells, temps = profile if profile else ([], [])

        # 赛季框跟着当前车走；没选车（或那台车没有赛季）就保持用户当前选的年份
        season, _number = customization.split_vehicle(current)
        season_spin = self.spin_vehicle_season
        season_spin.blockSignals(True)
        if season.isdigit():
            season_spin.setValue(int(season))
        season_spin.blockSignals(False)
        self._refresh_number_combo()

        # 这一排没有行标签，永远在；显隐只由 _sync_grid_params_visibility 管
        self._vehicle_field.setVisible(True)
        self._sync_grid_params_visibility()

        spin = self.spin_vehicle_modules
        spin.blockSignals(True)
        # 没选车时这颗框**绑旧的 module_count**（老用户在同一位置看到同一个数，
        # 不会出现"设置里有两个模组个数、数值还不一样"）
        spin.setValue(len(cells) if has_vehicle
                      else int(customization.get("module_count")))
        spin.blockSignals(False)
        # 结构没变就不重建逐模组行：选同一台车 / 同结构车时，重建会触发整表单重排，
        # 模组数框下面的内容"跳"一下（用户点名"选车后模组数不要跳变"）。只有真
        # 的 cell/temp 列表变了才动（改模组个数是另一条路径，不经这里）。
        if (tuple(cells) != tuple(self._shown_cells)
                or tuple(temps) != tuple(self._shown_temps)):
            # 2026-11-02 用户：「选完车号后的动画有很大问题，逐帧分析」——
            # 重建是**同步**的（同一帧删旧行、建新行），数值 25/8→20/6 直接硬切。
            # 先把切换前的行区域抓成图，换完再淡出 = 交叉淡入。
            # 用户第六轮补充：「为什么选择车号之后这三行会跟着移动，没有必要」——
            # 根因就是 `_rebuild_module_rows` 同步删旧行建新行：最终几何虽然一样，
            # 但重建会让整张表单重排一次（上面几行瞬时抖动）。行数没变时**原地改值**：
            # 不重建控件、不碰布局，只换文本 + 交叉淡入。
            same_count = (len(cells) == len(self._module_rows)
                          and len(temps) == len(self._module_rows))
            if same_count:
                # 行数不变：**只原地改值**，不重建、也不盖覆盖层。用户 2026-11-02：
                # 「第二个问题并没有解决」—— 覆盖层虽然是旧数值，但半透明叠在
                # 新数值上仍会被看成"表格在动"；这里干脆什么都不动，只换数字。
                self._update_module_row_values(cells, temps)
            else:
                # 2026-11-13 用户定稿「推倒重来」：车号以下的设置区整片高斯模糊 →
                # 淡出露出切换后的新数据。机制见本文件顶部的 _VEH_* 常量与
                # _veh_start()（一次抓图 + 真高斯 + 覆盖层淡出，不用任何 QGraphics*Effect）。
                self._veh_start(cells, temps)

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

        文案两轮收短：先是用户 2026-10-22 定的「电芯数量：〔可填〕」，后来把框后
        的量词"个"删掉；2026-10-31 为了把面板压窄（用户："减少设置界面的宽度"）
        再把前缀收成「电芯」/「温度」（−96px，模组行从 362 变 240）。完整含义在
        这一排的 tooltip 里（"第 N 个模组的电芯数量 → 电压段这一排放几个点"）。
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
        # 原地改值（切车号但行数不变）时要把回滚目标一起更新，挂在框上最省事
        box._last_ok = last_ok

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

    def _rebuild_module_rows(self, cells: list[int], temps: list[int],
                             reserve: bool = False) -> None:
        """重建「模组 1..N」逐行控件（每行：电芯数量：N 个 + 温度采集数量：N 个）。

        ``reserve=True``：建完立刻用 `setRowVisible(False)` 压成 0 高 —— 给
        ``_veh_start`` 的过渡用（新行先藏起来，由占位行扛高度，P3 起点再显形）。
        """
        while self._module_rows:
            self._module_rows.pop()
            # 先 pop 再按"剩下的行数"算最后一行的行号：删一行后后面的行号整体上移
            self.form.removeRow(self._module_row_base + len(self._module_rows))
        for index in range(min(len(cells), len(temps))):
            field_cells = self._make_count_field(
                int(cells[index]), "电芯个数",
                f"第 {index + 1} 个模组的电芯数量 → 电压段这一排放几个点"
                "（0 = 这排不排）；直接输入数字，回车或点别处生效",
                lambda number, i=index: self._on_module_value(i, False, number))
            field_temps = self._make_count_field(
                int(temps[index]), "温度采集个数",
                f"第 {index + 1} 个模组的温度采集数量 → 温度段这一排放几个点"
                "（0 = 这排不排）；直接输入数字，回车或点别处生效",
                lambda number, i=index: self._on_module_value(i, True, number))

            row = QHBoxLayout()
            row.setSpacing(14)
            row.addWidget(field_cells)
            # 「电芯数量」和「温度采集数量」是两组，中间多让一些空（用户
            # 2026-10-23："之间的空可以稍微大一些"）—— 14px 的行距里两组
            # 标签几乎贴在一起，看不出是两回事。
            row.addSpacing(36)
            row.addWidget(field_temps)
            row.addStretch(1)
            label = self._label(f"Module {index + 1}")
            field = self._row(row)
            self.form.insertRow(self._module_row_base + index, label, field)
            self._module_rows.append((label, field))
            if reserve:
                self.form.setRowVisible(self._module_row_base + index, False)
        # 记下这次真正画上去的结构，供 _sync_vehicle_rows 判断"下次是否要重建"
        # （结构相同就跳过重建，避免选车时的布局跳变）。
        self._shown_cells = list(cells)
        self._shown_temps = list(temps)

    def _below_vehicle_rect(self) -> QRect:
        """对话框坐标系下，车辆区以下、body 底部以上的整块**可见**区域。

        用于「切换车号时整片高斯模糊」覆盖层：只取当前屏幕可见的部分
        （裁剪到滚动视口），与用户「屏幕正在显示的部分都高斯模糊」一致。
        返回 body 局部坐标（与 body.grab / _FadeOverlay 一致）。
        """
        body = self.form.parentWidget()
        if body is None:
            return QRect()
        try:
            veh_bottom = self._vehicle_field.mapTo(
                body, QPoint(0, self._vehicle_field.height())).y()
        except RuntimeError:
            return QRect()
        region_bottom = body.height()
        sa = self.findChild(QScrollArea)
        if sa is not None:
            vp = sa.viewport()
            if vp is not None:
                try:
                    top_local = vp.mapTo(body, QPoint(0, 0)).y()
                    bot_local = vp.mapTo(body, QPoint(0, vp.height())).y()
                except RuntimeError:
                    top_local = 0
                    bot_local = region_bottom
                veh_bottom = max(veh_bottom, top_local)
                region_bottom = min(region_bottom, bot_local)
        top = max(0, int(veh_bottom))
        height = max(0, int(region_bottom - veh_bottom))
        if height <= 0:
            return QRect()
        return QRect(0, top, body.width(), height)

    def _veh_blur_pixmap(self, pix: "QPixmap | None") -> "QPixmap | None":
        """1/4 降采样 + 真高斯（与 splash_motion._gaussian 同口径），返回可直接拉伸绘制的小位图。

        失败返回 None（覆盖层那一段就直接跳过，不崩动画）。
        """
        if pix is None or pix.isNull():
            return None
        try:
            small = pix.scaled(
                max(1, pix.width() // _VEH_BLUR_DIV),
                max(1, pix.height() // _VEH_BLUR_DIV),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            import splash_motion
            blurred = splash_motion._gaussian(
                small, _VEH_SIGMA_MAX / float(_VEH_BLUR_DIV))
            if blurred is None or blurred.isNull():
                return None
            return blurred
        except Exception:            # noqa: BLE001
            return None

    def _update_module_row_values(self, cells: list[int], temps: list[int]) -> None:
        """行数不变时**原地**改两列数字（不重建控件 → 布局不抖）。

        每行的 holder 里两个 QLineEdit（电芯、温度，创建顺序固定）；
        回滚目标 `_last_ok` 一起更新，否则用户改坏输入会退回建行时的旧值。
        """
        for index, (_label, field) in enumerate(self._module_rows):
            boxes = field.findChildren(QLineEdit)
            if len(boxes) < 2:
                continue
            for box, value in ((boxes[0], cells[index]), (boxes[1], temps[index])):
                number = int(value)
                box.blockSignals(True)
                box.setText(str(number))
                box.blockSignals(False)
                last = getattr(box, "_last_ok", None)
                if last is not None:
                    last[0] = number
        self._shown_cells = [int(v) for v in cells]
        self._shown_temps = [int(v) for v in temps]

    # ------------------------------------------------ 选车号过渡（整片高斯模糊淡出）
    #
    # 一条主时钟（_VEH_BLUR_TOTAL_MS）只干一件事：把「车号以下、当前可见的设置区」
    # 整片抓图 → 高斯模糊 → α=1 盖住旧画面 → 立刻换上新数据 → 停留 HOLD → 淡出 FADE。
    # 没有占位行、没有逐模组交叉淡入、没有逐帧重排（那些都是上一代被推翻的方案）。
    # 覆盖层复用 _FadeOverlay（透明背景，绘制阶段只有一次 drawPixmap，无 QGraphics*Effect）。

    def _veh_state(self) -> dict | None:
        return getattr(self, "_veh", None)

    def _veh_dbg(self, tag: str, extra: str = "") -> None:
        """把关键节点写进 logs/vehicle_anim.log（永不抛异常），便于真机排查残影。"""
        try:
            import os as _os, time as _t
            base = _os.environ.get("LOCALAPPDATA") or _os.path.expanduser("~")
            folder = _os.path.join(base, "CQUFSAE-SerialAssistant", "logs")
            _os.makedirs(folder, exist_ok=True)
            parts = [f"{_t.strftime('%m-%d %H:%M:%S')}.{int(_t.time() * 1000) % 1000:03d}", tag]
            fo = getattr(self, "_veh_mask_full", None)
            if fo is not None:
                try:
                    g = fo.geometry()
                    parts.append(
                        f"ov_vis={fo.isVisible()},ov=({g.x()},{g.y()},{g.width()},{g.height()})")
                except Exception:  # noqa: BLE001
                    pass
            if extra:
                parts.append(extra)
            with open(_os.path.join(folder, "vehicle_anim.log"), "a", encoding="utf-8") as fh:
                fh.write(" ".join(parts) + "\n")
        except Exception:  # noqa: BLE001
            pass

    def _veh_start(self, cells: list[int], temps: list[int]) -> None:
        """切换车号 / 改模组数：整片「车号以下可见区」高斯模糊 → 淡出露新数据。

        先抓**当前**屏幕可见的旧画面（车号以下的设置区）→ 真高斯模糊 → 以 α=1 盖在
        上方（糊住旧画面），再把设置区换成切换后的新数据（清晰、已就位，盖在模糊层
        底下），最后让模糊层淡出，露出清晰的新数据。任何一步被打断都落到终态
        （清覆盖层 + 显示新行），绝不残影。
        """
        body = self.form.parentWidget()
        if body is None or not getattr(self, "_veh_allow", False):
            # 面板还没上屏 / 动画被禁用：直接重建，不做过渡。
            self._rebuild_module_rows(cells, temps)
            return
        from ui_anim import animations_enabled
        if not animations_enabled():
            self._rebuild_module_rows(cells, temps)
            return

        # 打断上一段（若还在跑）：先停表 + 清覆盖层，避免两条主时钟同时写覆盖层。
        self._veh_stop()

        # ① 抓「车号以下、当前可见」的旧画面并模糊。
        #   ⛔ 抓图前先清掉上一段残留的覆盖层，否则旧残影会被烤进快照。
        old_overlay = getattr(self, "_veh_mask_full", None)
        if old_overlay is not None:
            try:
                old_overlay.hide()
                old_overlay.update()
            except RuntimeError:
                pass
        rect = self._below_vehicle_rect()
        pix = None
        if not rect.isNull() and rect.height() > 0:
            try:
                grabbed = body.grab(rect)
            except RuntimeError:
                grabbed = None
            if grabbed is not None and not grabbed.isNull():
                pix = self._veh_blur_pixmap(grabbed)

        # ② 把设置区换成切换后的新数据（清晰、已就位，盖在模糊层底下）。
        self._rebuild_module_rows(cells, temps)

        # 没有可模糊的快照（极端：区域为空）→ 直接收尾。
        if pix is None or rect.isNull():
            self._veh_stop()
            return

        # ③ 盖模糊层 α=1，主时钟驱动淡出。
        try:
            overlay = getattr(self, "_veh_mask_full", None)
            if overlay is None or overlay.parentWidget() is not body:
                overlay = _FadeOverlay(body)
                self._veh_mask_full = overlay
        except RuntimeError:
            return
        state = {"rect": QRect(rect), "pix": pix, "overlay": overlay, "clock": None}
        self._veh = state
        self._veh_dbg("START")
        overlay.show_frame([(pix, 1.0)], rect, 1.0)
        from ui_anim import Tween
        clock = Tween(_VEH_BLUR_TOTAL_MS, "linear",
                     lambda k, s=state: self._veh_tick(s, k),
                     lambda s=state: self._veh_finish(s),
                     parent=self, min_interval_ms=_VEH_TICK_MS)
        state["clock"] = clock
        clock.start(0.0, 1.0, _VEH_BLUR_TOTAL_MS)

    def _veh_tick(self, state: dict, k: float) -> None:
        if self._veh_state() is not state:
            return
        try:
            ms = k * _VEH_BLUR_TOTAL_MS
            if ms <= _VEH_BLUR_HOLD_MS:
                alpha = 1.0
            else:
                p = min(1.0, (ms - _VEH_BLUR_HOLD_MS) / max(1, _VEH_BLUR_FADE_MS))
                alpha = 1.0 - (p * p)        # 末段加速清晰
            overlay = state.get("overlay")
            if overlay is None:
                return
            if alpha <= 0.002:
                overlay.clear()
                return
            overlay.show_frame([(state["pix"], 1.0)], state["rect"], alpha)
        except Exception as _exc:  # noqa: BLE001
            self._veh_dbg("TICK_EXC", repr(_exc))
            self._veh_stop()

    def _veh_stop(self) -> None:
        state = self._veh_state()
        self._veh = None
        if state is not None:
            self._veh_dbg("STOP")
            clock = state.get("clock")
            if clock is not None:
                try:
                    clock.stop()
                    clock.deleteLater()
                except RuntimeError:
                    pass
        overlay = getattr(self, "_veh_mask_full", None)
        if overlay is not None:
            try:
                overlay.clear()
            except RuntimeError:
                pass

    def _veh_finish(self, state: dict) -> None:
        """落到终态：清覆盖层 + 整块重绘。新数据已由 _rebuild_module_rows 就位。"""
        if self._veh_state() is not state:
            return
        self._veh_dbg("FINISH")
        self._veh = None
        overlay = getattr(self, "_veh_mask_full", None)
        if overlay is not None:
            try:
                overlay.clear()
            except RuntimeError:
                pass
        clock = state.get("clock")
        if clock is not None:
            try:
                clock.stop()
                clock.deleteLater()
            except RuntimeError:
                pass
        self._veh_repaint_all()

    def _veh_repaint_all(self) -> None:
        """强制**整块同步重绘**（body + 滚动视口）。

        模组区高度变化后，QScrollArea 的 viewport 不会自动把「上移后的新内容」全量
        重画，旧的一帧会淡淡留在屏幕上 = 用户截图那种「每一行上方都有半截重影」。
        repaint() 同步，立刻用正确内容覆盖，不等事件循环。
        """
        try:
            body = self.form.parentWidget()
            if body is not None:
                body.repaint()
            sa = self.findChild(QScrollArea)
            if sa is not None:
                vp = sa.viewport()
                if vp is not None:
                    vp.repaint()
        except RuntimeError:
            pass

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
        # 重建走和选车号同一条过渡（整片高斯模糊淡出），见 _veh_start。
        self._sync_grid_params_visibility()
        self._veh_start(cells, temps)
        self._notify_vehicle_changed()

    def _on_season_changed(self, value: int) -> None:
        """换赛季：只重排「车号」列表，**不自动选车**。

        为什么不自动选第一台：替用户挑一台 = 接着把模组结构按那台车改，
        一不留神就改错了车（这会影响接收区怎么排电池数据）。所以当前车不属于
        新赛季时就直接变成"未选车"，让用户自己点。
        """
        season = str(value)
        name = customization.current_vehicle()
        if name and customization.split_vehicle(name)[0] != season:
            customization.select_vehicle("")
            self._sync_vehicle_rows()
            self._notify_vehicle_changed()
            return
        self._refresh_number_combo()

    def _on_vehicle_picked(self, index: int) -> None:
        """车号下拉换了一台车（或换回「（未选择）」）。

        ⚠️ ``index < 0`` = 用户正在框里**改写车号**（可编辑下拉把 currentIndex
        打成 -1），那不是"换车"，改名交给 editingFinished 处理 —— 这里必须直接
        返回，否则输到一半就被当成"取消选车"，下拉内容会被清回（未选择）。
        """
        # `activated` 有 int/str 两个重载：若来的是文本，先反查下标
        if isinstance(index, str):
            found = self.cmb_vehicle_number.findData(index)
            if found < 0:
                found = self.cmb_vehicle_number.findText(index)
            index = found
        if index is None or index < 0:
            return
        name = str(self.cmb_vehicle_number.itemData(index) or "")
        if name == customization.current_vehicle():
            return
        # ⛔⛔ 2026-11-08 根治「打开设置时车号被 0↔5 反复切换」：整段同步期间屏蔽下拉信号。
        #    `_sync_vehicle_rows → _refresh_number_combo` 会 combo.clear()+setCurrentIndex()
        #    重建下拉，可编辑下拉在重建后可能回吐一次 currentIndexChanged(0/未选择)——若不在
        #    本次同步期间挡住，就会被当成"用户取消选车"→ 又 select → 又 sync，形成
        #    select→sync→(回吐0)→deselect→sync→(回吐5)→select 的自激环。
        #    用户真机日志实测：打开面板 5 秒内 4 段 218↔0 的动画反复。
        combo = self.cmb_vehicle_number
        _prev_block = combo.blockSignals(True)
        try:
            customization.select_vehicle(name)
            self._sync_vehicle_rows()
        finally:
            try:
                combo.blockSignals(_prev_block)
            except RuntimeError:
                pass
        self._notify_vehicle_changed()

    def _on_vehicle_new(self) -> None:
        """在当前赛季下新建车号：同风格单行输入框，确认后建好并自动选中。"""
        season = self._current_season()
        default = self._next_new_number_name(season)
        dialog = _NameDialog("新建车号", default, self._scheme, self)
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            number = dialog.value()
        finally:
            dialog.deleteLater()
        if not accepted or not number:
            return
        self._create_vehicle(customization.vehicle_key(number, season))

    def _create_vehicle(self, name: str) -> None:
        """按当前配置预填一台新车并选中它（``_on_vehicle_new`` 的实际动作）。"""
        name = str(name or "").strip()
        if not name:
            return
        if name in customization.vehicle_names():
            self._flash_vehicle_hint("已存在同车号")
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

    @staticmethod
    def _confirm_vehicle_delete(label: str) -> bool:
        """删除前的二次确认弹层。返回 True = 用户点了「删除」。

        ⛔ 不要 QMessageBox.question 的现成样式：左边一颗大问号、
        Yes/No 带助记下划线，用户 2026-10-23 点名"很难看"——
        改成无图标 + 纯文本按钮（没有 & 助记键，自然没有下划线）。
        拆成独立方法主要为了**可测**：selftest 把它整个换掉即可模拟
        确认/取消，不必去拦 QMessageBox 的模态 ``exec()``（实测一拦
        不到，模态框就会真的弹出来把自测卡死）。
        """
        box = QMessageBox(
            QMessageBox.Icon.NoIcon, "删除车号",
            f"确定删除「{label}」吗？\n\n只删这套模组结构，已接收的数据不受影响。",
            parent=None)
        yes_btn = box.addButton("Yes", QMessageBox.ButtonRole.YesRole)
        no_btn = box.addButton("No", QMessageBox.ButtonRole.NoRole)
        # 默认键给 No：回车不能顺手把一台车的模组结构删掉
        box.setDefaultButton(no_btn)
        box.exec()
        return box.clickedButton() is yes_btn

    def _on_vehicle_delete_requested(self, name: str) -> None:
        """弹层条目右端的 × 被点了：**二次确认**后才真删。

        用户 2026-10-22 明确要求"删除需要二次确认"—— 删除入口从主行挪进弹层，
        鼠标划过列表时误点的概率变高了，确认这一步不能省。删的正好是当前车时，
        选择会被清空（见 customization.delete_vehicle）。
        """
        target = str(name or "").strip()
        if not target:
            return
        season, number = customization.split_vehicle(target)
        label = f"{number}（{season} 赛季）" if season else number
        if not self._confirm_vehicle_delete(label):
            return
        customization.delete_vehicle(target)
        self._sync_vehicle_rows()
        self._notify_vehicle_changed()

    def _on_vehicle_number_typed(self) -> None:
        """在车号框里改写 = 给**当前车辆**改车号（赛季保持不动）。

        重名 / 空车号一律**拒绝并回滚文本**（不留一个假的成功态）；没有选中车时
        直接把文本还原成「（未选择）」，不凭空造一台车出来。
        """
        old = customization.current_vehicle()
        typed = self.cmb_vehicle_number.currentText().strip()
        if not old:
            self._refresh_number_combo()
            return
        if not typed:
            self._refresh_number_combo()
            self._flash_vehicle_hint("车号不能为空")
            return
        season = customization.split_vehicle(old)[0] or self._current_season()
        new = customization.vehicle_key(typed, season)
        if new == old:
            return
        if not customization.rename_vehicle(old, new):
            self._refresh_number_combo()
            self._flash_vehicle_hint("车号已存在")
            return
        self._sync_vehicle_rows()
        self._notify_vehicle_changed()

    @staticmethod
    def _next_new_number_name(season: str) -> str:
        """该赛季下一个可用的默认车号：新车 1、新车 2……（不撞同赛季已有车号）。"""
        used = {customization.split_vehicle(name)[1]
                for name in customization.vehicle_names_in_season(season)}
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

    # -------------------------------------------------------- 车辆共享

    def _export_vehicles(self) -> None:
        """把车辆档案导出成一个 .cqv.json 包（默认全部，用户挑路径）。

        ⚠️ ``names=None`` = 全部：单人维护的场景里"挑几台导出"是少数，默认
        整包出门才符合"发给队友"的意图。
        """
        share = _vehicle_share_module()
        if share is None:
            return
        stamp = datetime.now().strftime("%Y%m%d")
        default = Path.home() / f"vehicles-{stamp}.cqv.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出车辆", str(default),
            "车辆档案 (*.cqv.json *.json);;所有文件 (*)")
        if not path:
            return
        try:
            exported = share.export_to_file(path, names=None)
        except Exception as exc:      # 文件/格式问题一律就地反馈，别抛到控制台
            app_log.error(f"导出车辆失败：{exc}")
            self._flash_btn(self.btn_vehicle_export, "导出失败", 4000)
            self.btn_vehicle_export.setToolTip(f"导出失败：{exc}")
            return
        count = exported if isinstance(exported, int) else len(customization.vehicles())
        self._flash_btn(self.btn_vehicle_export, f"✓ {count} 台", 3200)
        self.btn_vehicle_export.setToolTip(f"已导出 {count} 台车辆到：\n{path}")
        app_log.info(f"车辆档案已导出（{count} 台）：{path}")

    def _import_vehicles(self) -> None:
        """从 .cqv.json 包导入车辆，导入后面板里的车辆列表当场刷新。

        反馈走按钮上的原地文字（``✓ +N 台``），**不弹模态框** —— 导入经常连着
        做几次，每来一个"确定"就断一次手。

        ``import_from_file`` 从不抛异常：读不了/JSON 坏了都会翻译成
        ``{"added":0,…,"errors":["读不了文件（…）"]}``，所以"0 台 + 有 errors"
        就是失败，那句人话直接进 tooltip。
        """
        share = _vehicle_share_module()
        if share is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "导入车辆", str(Path.home()),
            "车辆档案 (*.cqv.json *.json);;所有文件 (*)")
        if not path:
            return
        try:
            result = share.import_from_file(path)
        except Exception as exc:
            app_log.error(f"导入车辆失败：{exc}")
            self._flash_btn(self.btn_vehicle_import, "导入失败", 4000)
            self.btn_vehicle_import.setToolTip(f"导入失败：{exc}")
            return
        changed, note = _import_summary(result)
        self._sync_vehicle_rows()      # 车号下拉 / 逐模组行按新档案重建
        if changed:
            self._flash_btn(self.btn_vehicle_import, f"✓ +{changed} 台", 3600)
            self.btn_vehicle_import.setToolTip(f"{note}：\n{path}")
            app_log.info(f"车辆档案已导入（{note}）：{path}")
            return
        self._flash_btn(self.btn_vehicle_import, "导入失败", 4000)
        self.btn_vehicle_import.setToolTip(f"导入失败：{note or '包里没有可导入的车辆'}\n{path}")
        app_log.warning(f"导入车辆没有变更：{note}（{path}）")

    def _open_vehicle_library(self) -> None:
        """打开在线车辆库（模态；导入成功后回头刷新本面板的车辆列表）。"""
        dialog = VehicleLibraryDialog(self._scheme, parent=self)
        dialog.vehicles_changed.connect(self._on_library_imported)
        dialog.exec()

    def _on_library_imported(self, count: int) -> None:
        self._sync_vehicle_rows()
        if count:
            self._flash_btn(self.btn_vehicle_library, f"✓ +{count} 台", 3600)

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

        # ⚠️ 这两张表在「主题」一节之前就建好了（见 _build_theme_section 上方）：
        #    「界面材质」会先登记进去，这里**不能重新赋值**，否则会被冲掉。
        # 每一行的（左侧行标签, 字段控件）。选车后要整行隐藏旧的三件套网格参数
        # （module_count / cells_per_module / temps_per_module）—— 只藏字段不藏
        # 标签会在表单里留一个孤立文字，看着像坏了。
        # 滑杆型配置的预览标签（"拖动即时看效果"的那种），key -> QLabel
        self._custom_previews: dict[str, QLabel] = {}
        # 数字框统一宽度 = max(基准, **本机字体实测**的最长文本)（用户
        # 2026-11-01 两轮点名"mV 的 V 没显示完全"）：固定 100 没把 suffix +
        # 右侧箭头(22) + 行内 textMargins(10+6) 算进去，"4500 mV" 必截。
        # 按实测自动定宽，字体/缩放变了也不会再截。
        _spin_w = SPIN_COLUMN_W
        _spin_fm = QFontMetrics(self.font())
        for spec in specs:
            if (spec.get("suffix") and spec.get("kind") not in ("bool", "choice")
                    and spec.get("widget") != "slider"):
                need = (_spin_fm.horizontalAdvance(
                            f"{spec['maximum']}{spec['suffix']}")
                        + ChevronSpinBox.ARROW_W
                        + ChevronSpinBox.TEXT_MARGIN[0]
                        + ChevronSpinBox.TEXT_MARGIN[2] + 6)
                _spin_w = max(_spin_w, int(need))
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
                # 160 而不是 190（2026-10-31）：滑杆行 = 滑杆 + 数值 + 复原图标
                # = 290，当时是全表最宽的一行，比模组行还宽、替面板定宽。
                # 160 仍然够拖（0~100 的滑杆，一像素约 0.6 格）。
                slider.setMinimumWidth(160)
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
                combo.setFixedWidth(FIELD_W)
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
                # "10000 mV" 各占一块，右边缘参差不齐）。宽度按上面实测的
                # _spin_w 取，保证最长的 "10000 mV" 连 suffix 一起完整显示。
                spin.setFixedWidth(_spin_w)
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

    def _snapshot_custom_baseline(self) -> None:
        """记下**面板刚建好时**每个控件的显示值，供 ``custom_values`` 判断"改没改"。

        ⛔ 不能拿 ``customization.get(key)`` 当基准：滑杆会被 ``maximum_fn`` 夹进
        量程（``anim_fps`` 存过 240、当前显示器只有 60Hz → 显示 60），拿存的值比
        就会判定"用户改过"—— 用户只是打开面板看了一眼、点了个确定，
        240 就被永久改成 60，还打了 chosen 标记（以后换回高刷屏也不恢复）。
        """
        self._custom_baseline = {}
        for spec in customization.specs():
            widget = self._custom_widgets.get(spec["key"])
            if widget is None:
                continue
            if spec["kind"] == "bool":
                self._custom_baseline[spec["key"]] = bool(widget.isChecked())
            elif spec["kind"] == "choice":
                self._custom_baseline[spec["key"]] = widget.currentData()
            else:
                self._custom_baseline[spec["key"]] = int(widget.value())

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
            baseline = self._custom_baseline.get(spec["key"],
                                                 customization.get(spec["key"]))
            if value != baseline:
                changes[spec["key"]] = value
        return changes

    # ------------------------------------------------------------ 日志

    def _build_log_section(self, form: QFormLayout) -> None:
        """「日志」路径 + 2×2 田字按钮；「接收历史」车号下拉 + 打开记录 + 选择目录。

        2026-10-31 用户点名：原来「日志」一行挤四颗按钮（导出日志 / 打开文件夹 /
        数据 CSV / 数据 JSON），把面板撑宽了一大截。改成**两排田字**：

            导出日志     打开文件夹      ← 这一份文件
            数据 CSV     数据 JSON       ← 这份数据

        四颗同宽、两列左边缘对齐，整块只占原来两颗按钮的宽度 —— 面板最小宽度
        因此跟着降下来。路径文字挪到按钮上方单独一行：挤在左边的话两排按钮的
        左边缘就错开了（用户要的就是"左边缘对齐成两列"）。
        """
        log_path = app_log.path()
        if not log_path:
            return
        form.addRow(self._caption("日志"))

        # 34 个字符（≈270px）：面板压到 470 宽之后，52 字符的省略版还是放不下，
        # 尾巴会被硬切（实测需 413px / 只有 282px）。完整路径在 tooltip 里。
        # 用户 2026-11-01：「设置里面的地址要显示完全」—— 不再做中间省略，改成
        # 完整路径 + 自动换行（面板窄就折成两行，tooltip 仍然是全路径）。
        # 用户 2026-11-01 第二轮：「这个地址要显示成一排」—— 完整路径在窄面板
        # 必然折成两行，改**单行中间省略**；完整路径在 tooltip，悬停可见。
        self.lbl_log_path = _ElidedPathLabel(log_path, ui_font(10.5))
        self.lbl_log_path.setObjectName("groupCaption")
        # ⚠️ 必须 setMinimumWidth(**1**)，不能是 0：Qt 布局算最小宽度时把 0 当成
        # "没设过"，会退回 minimumSizeHint —— 那正是整条绝对路径的宽度，会把面板
        # 撑得比视口还宽（横向滚动条是关着的 → 右半边连按钮一起被裁）。
        self.lbl_log_path.setMinimumWidth(1)
        self.lbl_log_path.setToolTip(log_path)      # 完整路径（显示的是省略版）
        self.lbl_log_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        # 一键导出（用户点名）：把日志另存到用户挑的位置，方便发给别人排查。
        self.btn_export_log = InkButton("导出日志", variant="ghost")
        self.btn_export_log.setToolTip("把日志文件另存为一份，方便发给别人排查问题")
        self.btn_export_log.clicked.connect(self._export_log)
        self.btn_open_log_dir = InkButton("打开文件夹", variant="ghost")
        self.btn_open_log_dir.setToolTip("在资源管理器里打开日志所在的文件夹")
        self.btn_open_log_dir.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(Path(log_path).parent))))
        # 结构化导出（2026-10-30 用户要求："本轮数据 → CSV/JSON，而不是只有日志文本"）：
        # 日志是给人看的，这两颗按钮是给 Excel / pandas 用的 —— 数据从
        # rx_export 的采集缓存来（主窗口在 _emit_block 里喂原始文本）。
        self.btn_export_csv = InkButton("数据 CSV", variant="ghost")
        self.btn_export_csv.setToolTip(
            "把已接收的数据导出成**宽表**：一行 = 某一轮的某个模组，"
            "电芯/温度各自展开成列，直接进 Excel 或 pandas")
        self.btn_export_csv.clicked.connect(lambda: self._export_data("csv"))
        self.btn_export_json = InkButton("数据 JSON", variant="ghost")
        self.btn_export_json.setToolTip(
            "把已接收的数据导出成**嵌套结构**：rounds → modules → cells/temps，"
            "带单位，适合脚本二次处理")
        self.btn_export_json.clicked.connect(lambda: self._export_data("json"))

        # 用户 2026-11-01：「四颗边框一样大」+「地址显示完全」+「排版美观」：
        # 路径**独占一行**（完整、自动换行），四颗按钮统一等宽、**同一行、靠右**。
        # （之前为了压窄面板折成 2×2 田字，现在用户要求并成一行。）
        _equalize_buttons(
            (self.btn_export_log, self.btn_open_log_dir,
             self.btn_export_csv, self.btn_export_json),
            extra_texts=("✓ 已导出", "✓ 0000 行", "✓ 000 轮"))
        log_row = QHBoxLayout()
        log_row.setContentsMargins(0, 0, 0, 0)
        log_row.setSpacing(8)
        log_row.addStretch(1)                    # 整块**靠右**，与其它小节同一条右缘
        log_row.addWidget(self.btn_export_log)
        log_row.addWidget(self.btn_open_log_dir)
        log_row.addWidget(self.btn_export_csv)
        log_row.addWidget(self.btn_export_json)
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        column.addWidget(self.lbl_log_path)      # 完整路径独占一行
        column.addLayout(log_row)                # 四颗等宽、同一行、靠右
        form.addRow(self._row(column))

        # ---- 接收历史 ----
        # 用户 2026-10-31："接收历史的文件夹不只是需要跟着车号改变，还需要能够
        # 手动选择，在打开记录前面加一个可以选择车号的功能。" 于是这一行变成
        # 下拉（已存在的历史文件，新的在前）+ 打开记录 + 选择目录。
        import rx_history
        history_path = rx_history.path() or ""
        can_pick_dir = callable(getattr(rx_history, "set_directory", None))
        if not history_path and not can_pick_dir:
            return          # 定位不到、又不能手动选：整段不显示（别给点不动的按钮）
        form.addRow(self._caption("接收历史"))

        self.lbl_history_path = _ElidedPathLabel(
            history_path if history_path else "（还没有历史文件）", ui_font(10.5))
        self.lbl_history_path.setObjectName("groupCaption")
        self.lbl_history_path.setMinimumWidth(1)    # 同日志行的坑（见上）
        self.lbl_history_path.setToolTip(history_path)
        self.lbl_history_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        # 车号下拉（同其它下拉的 ChevronComboBox）：条目 = 已存在的历史文件，
        # 新的在前；第一项永远是"当前车号那一份"（保持原来"跟着车号走"的行为）。
        self.cmb_history = ChevronComboBox()
        # 宽度交给**最长条目**决定（车号可能更长：E74-2025 / E75-2026，固定宽会裁字）。
        # 2026-11-02 用户第三次点名"不需要这么大的框 / 空太多"，前后实测（windows 平台、
        # 真实 12.5pt MiSans，见 devtools/_probe_cmb_contentrect.py）：
        #   · 之前 minContentsLength(10)+minWidth(96) → 顶到 max 150、右侧空一大片；
        #   · ⚠️ 不能用 `AdjustToMinimumContentsLengthWithIcon` + minimumContentsLength(1)：
        #     它是按"1 个字符"定宽（不是最长条目），实测框只剩 80px、真实字体下把
        #     "E74-2026" 裁成 "E74-"（放大截图确认）。
        #   · 改用 `AdjustToContents`（Qt 的"按内容/最长条目自适应"，正是这里要的）。
        # chrome（chevron+左右 padding+边框）实测 48px，这是"不裁字"的物理下限 ——
        # 空(w−文字) ≈ 48，正好是必需的外框占位，没有多余留白。
        self.cmb_history.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cmb_history.setMinimumWidth(1)
        self.cmb_history.setMaximumWidth(150)
        self.cmb_history.setToolTip(
            "列出已存在的接收历史（每台车一个文件，新的在前）。\n"
            "选中后「打开记录」打开的就是那一个。")

        # 「打开记录」只在**真有文件**时才建：没文件可开就藏掉它（沿用老口径
        # "别给一个点不动的按钮"，自测也钉着这条）；此时「选择目录」仍是这一节
        # 唯一有意义的动作，所以整节照旧显示。
        self.btn_open_history = None
        if history_path:
            self.btn_open_history = InkButton("打开记录", variant="ghost")
            self.btn_open_history.setToolTip(
                f"打开选中的历史接收数据（自动保存最近 {rx_history.max_lines()} 行，"
                "含时间戳；行数与接收区「保留行数」一致，到量自动滚动覆盖最早的数据）")
            self.btn_open_history.clicked.connect(self._open_history_record)

        self.btn_history_dir = InkButton("选择目录", variant="ghost")
        self.btn_history_dir.setToolTip(
            "手动指定接收历史存到哪个文件夹（下次启动沿用）；\n"
            "换目录后上面的列表会立刻按新目录重建")
        self.btn_history_dir.clicked.connect(self._choose_history_dir)
        if not can_pick_dir:
            # rx_history 还没提供 set_directory：按钮别装成能点的样子
            self.btn_history_dir.setEnabled(False)
            self.btn_history_dir.setToolTip(
                "当前版本还改不了历史目录（rx_history.set_directory 未提供）")

        # 路径独占一行（地址显示完全、自动换行）；下方三颗控件**并成一排**
        # （用户 2026-11-01 点名「这几个按钮弄成一行」）：选择目录 / 当前记录 /
        # 打开记录。整排靠右，与「日志」节四颗按钮同一条右缘。
        # ⚠️ 之前为压窄面板把三颗拆成两排；用户明确要并一排，故放开（面板最窄宽
        #    已由「日志」四颗行定为 ~604px，三颗并排约 364px 不会比它更宽）。
        path_row = QHBoxLayout()
        path_row.setSpacing(10)
        path_row.addWidget(self.lbl_history_path, 1)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(10)
        ctrl_row.addStretch(1)         # 整排靠右
        ctrl_row.addWidget(self.btn_history_dir)
        ctrl_row.addWidget(self.cmb_history)
        if self.btn_open_history is not None:
            ctrl_row.addWidget(self.btn_open_history)

        hholder = QWidget()
        hcolumn = QVBoxLayout(hholder)
        hcolumn.setContentsMargins(0, 0, 0, 0)
        hcolumn.setSpacing(6)
        hcolumn.addLayout(path_row)
        hcolumn.addLayout(ctrl_row)
        form.addRow(self._row(hcolumn))

        self._refresh_history_labels()

    def _refresh_history_labels(self) -> None:
        """重建「接收历史」下拉 + 路径文字（车号/目录变化后唯一的刷新入口）。

        条目来自 ``rx_history.available_labels()`` —— ``[(显示标签, 绝对路径)]``，
        **新的在前**。这个函数是另一个模块新加的，还没落地时退化成"只有当前
        那一份"，并把下拉置灰：面板不能因为对方进度而整块崩掉。
        """
        combo = getattr(self, "cmb_history", None)
        if combo is None:
            return
        import rx_history
        current = rx_history.path() or ""
        entries: list[tuple[str, str]] = []
        getter = getattr(rx_history, "available_labels", None)
        if callable(getter):
            try:
                entries = list(getter())
            except Exception as exc:      # 目录读不了也不该炸面板
                app_log.warning(f"列出接收历史文件失败：{exc}")
                entries = []

        # 用户 2026-10-31：「这个选择车辆要我能够选择我配置过的所有车辆的数据」——
        # 下拉不能只列"已经有历史文件"的那几台，要把**所有已配置车辆**都放进去
        # （还没收到数据的车也给条目，打开时提示"文件不存在"，而不是干脆选不到）。
        try:
            vehicles = [str(name) for name in customization.vehicle_names()]
        except Exception as exc:          # 读不到车辆清单也不该炸面板
            app_log.warning(f"读取车辆清单失败：{exc}")
            vehicles = []
        base_dir = ""
        dir_getter = getattr(rx_history, "directory", None)
        if callable(dir_getter):
            try:
                base_dir = str(dir_getter() or "")
            except Exception:
                base_dir = ""
        file_for = getattr(rx_history, "file_for", None)
        by_label = {str(lab): str(p) for lab, p in entries}
        ordered: list[tuple[str, str]] = []
        seen: set[str] = set()
        for name in vehicles:
            path = by_label.get(name)
            if not path and base_dir and callable(file_for):
                path = str(Path(base_dir) / file_for(name))
            if path:
                ordered.append((name, path))
                seen.add(name)
        for lab, path in entries:
            lab = str(lab)
            if lab not in seen:
                ordered.append((lab, str(path)))
                seen.add(lab)

        combo.blockSignals(True)
        combo.clear()
        # ⛔ 不再加一条带「●」的"当前记录"：它和下面的同名车辆重复，而且一条有点、
        #    其它没有，看着不一致（用户 2026-10-31 点名）。直接把所有车辆平铺，
        #    再把当前那份**选中**即可 —— 选中态本身就是"当前"。
        current_index = -1
        if current:
            try:
                vehicle = customization.current_vehicle()
            except Exception:
                vehicle = ""
            combo.addItem(str(vehicle) if vehicle else "当前记录", current)
            current_index = 0
        for label, path in ordered:
            if path == current:
                continue                  # 当前那份已经在第一项了
            combo.addItem(str(label), str(path))
        for index in range(combo.count()):
            combo.setItemData(index, str(combo.itemData(index) or ""),
                              Qt.ItemDataRole.ToolTipRole)
        if combo.count() == 0:
            combo.addItem("（还没有历史文件）", "")
            combo.setEnabled(False)
            combo.setToolTip("这个目录里还没有接收历史；先收一会儿数据再回来")
        else:
            combo.setEnabled(True)
            combo.setCurrentIndex(current_index if current_index >= 0 else 0)
        combo.blockSignals(False)
        # 条目换完后**立刻**把宽度收到"最长条目 + 右侧 chevron"（sizeHint，受 max 150 约束）：
        # 只改 sizeAdjustPolicy 时，控件在**首次布局激活之前**还停在旧宽度上（探针/未 show
        # 时实测仍是 150），这里 adjustSize 让几何当场到位，面板一建出来就是窄的。
        combo.updateGeometry()
        combo.adjustSize()

        if hasattr(self, "lbl_history_path"):
            shown = current if current else "（还没有历史文件）"
            self.lbl_history_path.setText(shown)
            self.lbl_history_path.setToolTip(current)
        open_button = getattr(self, "btn_open_history", None)
        if open_button is not None:
            open_button.setEnabled(bool(current or ordered))

    def _open_history_record(self) -> None:
        """打开下拉里选中的那一份历史；没选就打开当前车号的文件。"""
        import rx_history
        combo = getattr(self, "cmb_history", None)
        target = combo.currentData() if combo is not None else None
        if not target:
            # 兜底：条目只给了标签时走 rx_history.path_for_label(label)
            resolver = getattr(rx_history, "path_for_label", None)
            label = combo.currentText() if combo is not None else ""
            if callable(resolver) and label:
                try:
                    target = resolver(label)
                except Exception as exc:
                    app_log.warning(f"定位历史文件失败：{exc}")
                    target = None
        target = target or rx_history.path()
        if not target:
            self._flash_btn(self.btn_open_history, "没有文件", 2600)
            return
        if not Path(target).exists():
            self._flash_btn(self.btn_open_history, "文件不存在", 2600)
            self.btn_open_history.setToolTip(f"这个文件已经不在了：\n{target}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _choose_history_dir(self) -> None:
        """手动挑接收历史的存放目录（交 ``rx_history.set_directory`` 落地）。

        ⚠️ 只负责"问路 + 转交 + 刷新"，**不自己记配置**：目录要不要持久化、
        怎么迁移老文件，都是 ``rx_history`` 自己的事（它比面板更清楚那些细节）。
        """
        import rx_history
        setter = getattr(rx_history, "set_directory", None)
        if not callable(setter):
            return
        start = ""
        getter = getattr(rx_history, "directory", None)
        if callable(getter):
            try:
                start = str(getter() or "")
            except Exception:
                start = ""
        if not start:
            current = rx_history.path()
            start = str(Path(current).parent) if current else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "选择接收历史目录", start)
        if not chosen:
            return
        try:
            ok = bool(setter(chosen))
        except Exception as exc:
            ok = False
            app_log.warning(f"设置接收历史目录失败：{exc}")
        if not ok:
            self._flash_btn(self.btn_history_dir, "✗ 不可用", 3000)
            self.btn_history_dir.setToolTip(f"这个目录用不了：\n{chosen}")
            return
        self._flash_btn(self.btn_history_dir, "✓ 已切换", 2600)
        self.btn_history_dir.setToolTip(f"接收历史目录：{chosen}")
        self._refresh_history_labels()
        app_log.info(f"接收历史目录已切换到：{chosen}")

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
        hint = QLabel("AI 可直接读写串口")
        hint.setFont(ui_font(10.5))
        hint.setObjectName("groupCaption")
        # 同日志行：必须给 1 而不是 0，否则 minimumSizeHint 会把面板撑宽
        hint.setMinimumWidth(1)
        hint.setToolTip(
            "复制的内容自包含：装依赖的命令、MCP 服务全部源码、客户端注册片段、"
            "三条自检。粘给任意 Agent（WorkBuddy / Claude / Cursor…），"
            "它就能自动装好并把串口接到自己手上")
        row.addWidget(hint)
        self.btn_mcp_copy = InkButton("复制接入", variant="ghost")
        self.btn_mcp_copy.setToolTip(
            "复制一整段（含 MCP 服务源码与注册片段）—— 直接粘给任意 Agent，"
            "它会自动装依赖、写服务、注册 MCP 并自检")
        self.btn_mcp_copy.clicked.connect(self._copy_mcp_payload)
        row.addWidget(self.btn_mcp_copy)
        # 手动填写（2026-10-31 用户要求）：客户端那个「连接至自定义 MCP」是
        # 逐项填的（名称/类型/命令/参数/环境变量），光给"一整段给 Agent"不够用。
        self.btn_mcp_manual = InkButton("手动填写", variant="ghost")
        self.btn_mcp_manual.setToolTip(
            "打开一张逐项参数表（名称/类型/命令/参数/环境变量），每项可单独复制，"
            "照着填进客户端的自定义 MCP 对话框")
        self.btn_mcp_manual.clicked.connect(self._open_mcp_manual)
        row.addWidget(self.btn_mcp_manual)
        row.addStretch(1)          # 按钮紧挨文字，空白推到行尾（同日志行）
        form.addRow(self._row(row))

    def _open_mcp_manual(self) -> None:
        """打开「MCP 手动填写参数」表（模态，与主面板同一套样式）。"""
        dialog = McpManualDialog(self._scheme, parent=self)
        dialog.exec()

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
                               "复制接入", 4000)
            app_log.info("MCP 接入包已复制到剪贴板")
            return
        try:
            path = mcp_bridge.write_payload()
        except OSError as exc:
            app_log.warning(f"MCP 接入包保存失败：{exc}")
            self._flash_button(self.btn_mcp_copy, "复制失败", "复制接入", 4000)
            return
        self._flash_button(self.btn_mcp_copy, Path(path).name, "复制接入", 6000)
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

    # ---------------------------------------------------------- 会话数据库

    def _build_session_db_section(self, form: QFormLayout) -> None:
        """「会话数据库」：路径 + 打开文件夹；近 N 天导出 CSV；统计（点了才查）。

        数据来自 ``rx_store``（SQLite，每帧一行 append-only）。设置面板**打开时
        不碰库**：路径是纯字符串、统计要用户点「刷新」才查 —— 面板本来就要
        秒开，不能因为库里有几十万行就卡在打开动作上。
        """
        import rx_store
        db_path = rx_store.db_path()
        if not db_path:
            return          # 定位不到用户目录：整段不显示（同日志一节的处理）
        form.addRow(self._caption("会话数据库"))

        # 用户 2026-11-01：「地址也要显示完全」—— 不再用 _elide_middle 中间掐成
        # "C:\Users\Lenovo\...\logs\sessions.db"，改成**完整路径 + 自动换行**
        # （面板窄就折行，tooltip 仍是全路径），与「日志 / 接收历史」同款。
        # 用户 2026-11-01 第二轮：「这个地址要显示成一排」—— 三处路径统一
        # 单行中间省略（完整路径在 tooltip），与日志 / 接收历史同款。
        self.lbl_db_path = _ElidedPathLabel(db_path, ui_font(10.5))
        self.lbl_db_path.setObjectName("groupCaption")
        # ⚠️ 最小宽度必须 setMinimumWidth(**1**)，不能是 0（同日志行的坑：
        #    Qt 把 0 当"没设过"、退回整条路径的 minimumSizeHint，面板被撑爆）
        self.lbl_db_path.setMinimumWidth(1)
        self.lbl_db_path.setToolTip(db_path)        # 完整路径（显示的是完整版）
        self.lbl_db_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        self.btn_open_db_dir = InkButton("打开文件夹", variant="ghost")
        self.btn_open_db_dir.setToolTip("在资源管理器里打开会话数据库所在的文件夹")
        self.btn_open_db_dir.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(Path(db_path).parent))))
        # 路径**独占一行**（完整、自动换行），与「日志 / 接收历史」同款；
        # 不再和「打开文件夹」并排 —— 并排时路径被按钮挤窄、又无空格可折行，
        # 末尾 "sessions.db" 会被裁掉（用户 2026-11-01 点名"地址要显示完全"）。
        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(8)
        path_row.addWidget(self.lbl_db_path, 1)

        # 近 N 天导出（1~365，默认 7）：范围交给用户，默认值覆盖"这次车检"。
        self.spin_db_days = ChevronSpinBox()
        self.spin_db_days.setRange(1, 365)
        self.spin_db_days.setValue(7)
        self.spin_db_days.setSuffix(" 天")
        self.spin_db_days.setToolTip("导出最近这么多天的会话数据（按帧，一天可能上万行）")
        # 宽度按**当前文字**动态收紧（用户 2026-11-02 第三次点名"空太多"）：
        # 固定成「365 天」的宽度时，值=7 右侧会空一大片（实测 w=124、空白 73~101px）。
        # 由 _fit_db_days_width 在每次数值变化后按 text() 像素宽重设（下限 64、
        # 上限 124 保证「365 天」放得下）。
        self._fit_db_days_width()
        self.spin_db_days.valueChanged.connect(
            lambda _=0: self._fit_db_days_width())
        self.btn_db_export = InkButton("导出 CSV", variant="ghost")
        self.btn_db_export.setToolTip(
            "把所选天数内的帧导出成 CSV：一行 = 一帧，电芯/温度空格分隔，"
            "可直接进 Excel 或 pandas")
        self.btn_db_export.clicked.connect(self._export_session_csv)
        export_row = QHBoxLayout()
        export_row.setContentsMargins(0, 0, 0, 0)
        export_row.setSpacing(8)
        # 右对齐（与下面「刷新」同一条右缘）：打开文件夹 + 数字框 + 导出，整组贴右。
        # 别给按钮加 stretch，否则会拉成一条长条。
        export_row.addStretch(1)
        export_row.addWidget(self.btn_open_db_dir)
        export_row.addWidget(self.spin_db_days)
        export_row.addWidget(self.btn_db_export)

        # 统计行：**懒加载** —— 打开面板不查库，点「刷新」才 SELECT。
        self.lbl_db_stats = QLabel("共 — 帧（点「刷新」查询，打开面板不读库）")
        self.lbl_db_stats.setFont(ui_font(10.5))
        self.lbl_db_stats.setObjectName("groupCaption")
        self.lbl_db_stats.setMinimumWidth(1)
        self.lbl_db_stats.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_db_stats = InkButton("刷新", variant="ghost")
        self.btn_db_stats.setToolTip("查询总帧数、覆盖时间范围与车号数（只读）")
        self.btn_db_stats.clicked.connect(self._refresh_db_stats)
        stats_row = QHBoxLayout()
        stats_row.setContentsMargins(0, 0, 0, 0)
        stats_row.setSpacing(8)
        stats_row.addWidget(self.lbl_db_stats, 1)
        stats_row.addWidget(self.btn_db_stats)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        column.addLayout(path_row)
        column.addLayout(export_row)
        column.addLayout(stats_row)
        form.addRow(self._row(column))

    def _refresh_db_stats(self) -> None:
        """查库统计（只有点「刷新」才碰库；失败只是显示"查不到"，不弹框）。"""
        import rx_store
        st = rx_store.stats()
        if st["count"] <= 0:
            self.lbl_db_stats.setText("共 0 帧（收到数据后自动入库）")
            self.lbl_db_stats.setToolTip("")
            return
        first = (st["first"] or "")[:19].replace("T", " ")
        last = (st["last"] or "")[:19].replace("T", " ")
        self.lbl_db_stats.setText(
            f"共 {st['count']} 帧 · {st['vehicles']} 台车 · {first} → {last}")
        self.lbl_db_stats.setToolTip(f"最早：{st['first']}\n最晚：{st['last']}")

    def _export_session_csv(self) -> None:
        """把最近 N 天的会话数据导出成 CSV（一行 = 一帧）。"""
        import rx_store
        days = int(self.spin_db_days.value())
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        desktop = Path.home() / "Desktop"
        folder = desktop if desktop.is_dir() else Path.home()
        default = str(folder / f"serial-{stamp}-session-{days}d.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, f"导出近 {days} 天会话数据（CSV）", default, "CSV 表格 (*.csv)")
        if not path:
            return
        rows = rx_store.export_csv(path, days)
        if rows <= 0:
            QMessageBox.information(
                self, "导出会话数据",
                f"最近 {days} 天没有落库的数据。\n\n"
                "收到数据后会自动入库（这一节的「刷新」能看到总帧数）。")
            return
        app_log.info(f"会话数据已导出: {path}（{rows} 行）")
        self._flash_btn(self.btn_db_export, f"✓ {rows} 行")

    def _fit_db_days_width(self) -> None:
        """按当前文字（如「7 天」）收紧「近 N 天」数字框，避免大段空白。

        用户 2026-11-02 三次点名"空太多"：固定成能放「365 天」的宽度时，值=7
        右侧会空一大片。这里按 ``text()``（含 " 天" 后缀）的实测像素宽重设固定宽：
        ``+22`` 是右侧上下箭头的自绘宽度，``+16`` 是左右文字内边距；下限 64 保证
        单/双位数够看，上限 124 与「365 天」实测（85px + 22 + 16 ≈ 123）对齐，
        既不裁「365 天」、又不给「7 天」留大段空白。数值一变就重算（见 valueChanged）。
        """
        spin = getattr(self, "spin_db_days", None)
        if spin is None:
            return
        fm = spin.fontMetrics()
        need = fm.horizontalAdvance(spin.text()) + 22 + 16   # 22=上下箭头, 16=左右内边距
        spin.setFixedWidth(max(64, min(124, need)))           # 64=下限, 124=「365 天」实测上限

    # ------------------------------------------------------------ 协议

    def _build_protocol_section(self, form: QFormLayout) -> None:
        """「协议」：解析规则 JSON 编辑 + 校验/保存 + 试跑。

        为什么（用户："换一台 BMS 就得改 Python 重打包，太麻烦"）：标题写法
        （``Voltage590:`` / ``Pack3:``）、值的位数（4 位 mV / 2 位℃）、错误字样
        都可能变 —— 做成规则文件后，在面板里改、校验、试跑、保存即生效。
        """
        import rx_protocol
        form.addRow(self._caption("协议"))
        hint = QLabel("解析规则（JSON）。改完点「保存」立刻生效，不用重启；\n"
                      "「试跑」只做验证、不保存。")
        hint.setFont(ui_font(10.5))
        hint.setObjectName("groupCaption")
        hint.setWordWrap(True)
        # 同日志行的坑：不钉 1 的话 minimumSizeHint 会把面板撑爆
        hint.setMinimumWidth(1)
        hint.setToolTip(
            "可改的 7 项：module_header / temp_header（段标题）、"
            "cell_value / cell_width（电芯值的正则与取几位）、"
            "temp_value / temp_width（温度的）、error_pattern（错误字样）。\n"
            "⛔ 默认值与原来写死在代码里的口径一字不差，改坏了点「恢复默认」即可。")
        self.btn_proto_reset = InkButton("恢复默认", variant="ghost")
        self.btn_proto_reset.setToolTip("删掉自定义规则，回到内置默认（与旧版本行为一致）")
        self.btn_proto_reset.clicked.connect(self._restore_protocol)
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(8)
        head_row.addWidget(hint, 1)
        head_row.addWidget(self.btn_proto_reset)

        self.txt_proto = QPlainTextEdit()
        self.txt_proto.setFont(mono_font(10))
        self.txt_proto.setFixedHeight(150)          # 10 行上下（可滚动）
        self.txt_proto.setPlainText(
            json.dumps(rx_protocol.active(), ensure_ascii=False, indent=2))
        self.txt_proto.setToolTip("当前的协议规则（JSON）。校验不通过时不要保存。")
        self.lbl_proto_state = QLabel("")
        self.lbl_proto_state.setFont(ui_font(10.5))
        self.lbl_proto_state.setObjectName("groupCaption")
        self.lbl_proto_state.setMinimumWidth(1)
        self.lbl_proto_state.setWordWrap(True)
        self.btn_proto_check = InkButton("校验", variant="ghost")
        self.btn_proto_check.setToolTip("只检查规则是否合法（报出是哪一项有问题），不保存")
        self.btn_proto_check.clicked.connect(self._check_protocol)
        self.btn_proto_save = InkButton("保存", variant="ghost")
        self.btn_proto_save.setToolTip("校验通过后落盘并立刻生效（显示层马上按新规则解析）")
        self.btn_proto_save.clicked.connect(self._save_protocol)
        buttons_row = QHBoxLayout()
        buttons_row.setContentsMargins(0, 0, 0, 0)
        buttons_row.setSpacing(8)
        buttons_row.addWidget(self.lbl_proto_state, 1)
        buttons_row.addWidget(self.btn_proto_check)
        buttons_row.addWidget(self.btn_proto_save)

        # ---- 试跑 ----
        self.txt_proto_sample = QPlainTextEdit()
        self.txt_proto_sample.setFont(mono_font(10))
        self.txt_proto_sample.setFixedHeight(84)    # 4~5 行
        self.txt_proto_sample.setPlaceholderText(
            "把一段真实接收数据粘到这里（含标题行），点「试跑」看解析结果")
        self.btn_proto_try = InkButton("试跑", variant="ghost")
        self.btn_proto_try.setToolTip("用左上的规则解析下面这段文本（不保存、不影响接收区）")
        self.btn_proto_try.clicked.connect(self._try_protocol)
        self.lbl_proto_try = QLabel("试跑结果：—")
        self.lbl_proto_try.setFont(ui_font(10.5))
        self.lbl_proto_try.setObjectName("groupCaption")
        self.lbl_proto_try.setMinimumWidth(1)
        self.lbl_proto_try.setWordWrap(True)
        try_row = QHBoxLayout()
        try_row.setContentsMargins(0, 0, 0, 0)
        try_row.setSpacing(8)
        try_row.addWidget(self.btn_proto_try)
        try_row.addWidget(self.lbl_proto_try, 1)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        column.addLayout(head_row)
        column.addWidget(self.txt_proto)
        column.addLayout(buttons_row)
        column.addWidget(self.txt_proto_sample)
        column.addLayout(try_row)
        form.addRow(self._row(column))

    def _proto_say(self, text: str, ok: bool) -> None:
        """原地显示校验/保存结果（不弹框：这里是高频编辑的输入区）。"""
        self.lbl_proto_state.setText(("✓ " if ok else "✗ ") + text)
        self.lbl_proto_state.setToolTip(text)

    def _protocol_json(self) -> dict | None:
        """编辑框里的 JSON → 规则字典；解析或校验失败时原地报错并返回 None。"""
        import rx_protocol
        text = self.txt_proto.toPlainText().strip()
        if not text:
            self._proto_say("规则是空的 —— 点「恢复默认」可载回默认规则", False)
            return None
        try:
            profile = json.loads(text)
        except ValueError as exc:
            self._proto_say(f"JSON 解析失败：{exc}", False)
            return None
        ok, message = rx_protocol.validate(profile)
        if not ok:
            self._proto_say(message, False)
            return None
        return profile

    def _check_protocol(self) -> None:
        profile = self._protocol_json()
        if profile is not None:
            self._proto_say("规则有效（7 项全部通过）", True)

    def _save_protocol(self) -> None:
        import rx_protocol
        import ui_highlight
        profile = self._protocol_json()
        if profile is None:
            return
        if not rx_protocol.save_profile(profile):
            self._proto_say("保存失败（写入配置失败，规则未生效）", False)
            return
        ui_highlight.set_protocol(profile)      # 立刻生效（不用重启）
        self._proto_say("已保存并生效（显示层已按新规则解析）", True)

    def _restore_protocol(self) -> None:
        import rx_protocol
        import ui_highlight
        rx_protocol.save_profile(None)
        ui_highlight.set_protocol(None)
        self.txt_proto.setPlainText(
            json.dumps(rx_protocol.DEFAULT, ensure_ascii=False, indent=2))
        self._proto_say("已恢复默认规则（与旧版本行为一致）", True)

    def _try_protocol(self) -> None:
        import rx_protocol
        profile = self._protocol_json()
        if profile is None:
            return
        result = rx_protocol.sample_parse(
            self.txt_proto_sample.toPlainText(), profile)
        self.lbl_proto_try.setText("试跑结果：" + result["note"])
        self.lbl_proto_try.setToolTip(
            f"电压模组：{result['cells']}\n温度模组：{result['temps']}")

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

    def _export_data(self, kind: str) -> None:
        """导出已接收数据的 CSV（宽表）/ JSON（嵌套）。

        数据来源是 ``rx_export.session()``（模块级单例，主窗口每帧喂），所以
        **不依赖接收区里显示着什么** —— 暂停、清空显示、翻历史都不会丢数据。
        """
        import rx_export
        session = rx_export.session()
        rounds = session.rounds()
        if not rounds:
            QMessageBox.information(
                self, "导出数据",
                "还没有解析到可导出的数据。\n\n"
                "需要有 Voltage1: / Temperature1: 这两类帧进来（BMS 行标志一轮结束）。")
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        desktop = Path.home() / "Desktop"
        folder = desktop if desktop.is_dir() else Path.home()
        default = str(folder / f"serial-{stamp}-data.{kind}")
        if kind == "csv":
            caption, filt = "导出数据（CSV 宽表）", "CSV 表格 (*.csv)"
        else:
            caption, filt = "导出数据（JSON）", "JSON 文件 (*.json)"
        path, _ = QFileDialog.getSaveFileName(self, caption, default, filt)
        if not path:
            return
        try:
            if kind == "csv":
                rows = session.write_csv(path)
                # 反馈文字要**短**：这两颗按钮现在是田字布局里的一格（宽度锁死，
                # 见 _build_log_section），长文案会把按钮顶宽、两列当场对不齐。
                msg = f"✓ {rows} 行"
            else:
                from version import VERSION as _ver
                meta = {"app": "CQUFSAE串口助手", "version": _ver}
                n = session.write_json(path, meta=meta)
                msg = f"✓ {n} 轮"
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"写文件失败：{exc}")
            return
        button = self.btn_export_csv if kind == "csv" else self.btn_export_json
        self._flash_btn(button, msg)

    def _flash_btn(self, button, text: str, ms: int = 3200) -> None:
        """在按钮上原地显示结果，几秒后复原（和「检查更新」同一套反馈）。"""
        original = button.text()
        button.setText(text)
        QTimer.singleShot(ms, lambda b=button, o=original: b.setText(o))

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

    @staticmethod
    def _local_version() -> tuple:
        """本地版本号，同样补齐到三段，跟远端 tag 的口径一致。"""
        from version import VERSION
        parts = tuple(int(p) for p in VERSION.split("."))
        return parts + (0,) * (3 - len(parts))

    def _check_update(self) -> None:
        """多源检查更新：**全部源并行发**，先回者参与、取版本号最高者胜。

        2026-10-25 用户要求"不开代理也要快速更新"：这里原本是"逐源串行 + 每源
        4s + 整链 2 轮 + 代理 3 层"，最坏 10×4×2×3 ≈ 4 分钟（断网时按钮一直转）。
        改成复用自动检查那条并行实现（``updater.UpdateChecker``）：典型 0.3~1.5s，
        代理解析（系统 → 本机 Clash → 直连）与"取最新而非取最先"都在它里面，
        两条入口从此只有一份实现。
        """
        if self._checker is not None:
            return                       # 已经在查了：别叠第二条，也别重入
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText("检查中…")
        checker = self._make_checker()
        self._checker = checker
        checker.checked.connect(self._on_update_checked)
        checker.failed.connect(self._on_update_check_failed)
        checker.start()

    def _make_checker(self):
        """工厂：自测里换成"假网络"的 Checker（见 selftest 的检查更新用例）。"""
        import updater
        return updater.UpdateChecker(self)

    def _on_update_checked(self, info: dict) -> None:
        self._checker = None
        self._finish_update_check()
        self._handle_update_info(info)

    def _on_update_check_failed(self, reason: str) -> None:
        self._checker = None
        self._finish_update_check()
        self.lbl_version.setText(f"检查失败：{reason}")
        self._flash_button_result("网络失败")

    def _handle_update_info(self, info: dict) -> None:
        from updater import UpdateDialog, update_asset_and_hash, version_tuple
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
                              # 按"安装版/免安装版"挑对应资产的**远端**哈希
                              sha256=update_asset_and_hash(info)[1],
                              tag=f"v{latest_str}", auto=False, parent=self)
        dialog.exec()

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
        self.btn_check_update.setEnabled(True)
        self.btn_check_update.setText("检查更新")
        # 注：全局代理策略由 UpdateChecker 自己还原（QNetworkProxy 是全局状态，
        # 检查过程中可能切到 Clash / 直连，留着会影响随后的大文件下载）。

    # ------------------------------------------------------------ 杂项

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 深色面板配系统白标题栏是全界面最扎眼的撞色（用户截图点名）——
        # 对话框也要走一遍 DWM 深色标题栏 + 圆角，和主窗口同一套处理
        apply_titlebar_theme(self, self._scheme)
        # 面板是"用的时候才打开"的：这一开期间可能收过数据、切过车、别的车也
        # 存了历史 —— 「接收历史」的车号列表每次打开都重建一次（构造时已经建过
        # 一次，两次走同一个入口，没有第二份逻辑）。
        if hasattr(self, "cmb_history"):
            self._refresh_history_labels()

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

    def _inline_caption(self, text: str) -> QLabel:
        """行内小标签（"赛季：" / "车号："）。

        字号与「模组 i」里那些"电芯数量："一致（10.5pt 的 groupCaption），
        这样同一张面板里"字段名前缀"看着是同一种东西。⛔ 最小宽度必须钉住
        文字宽度：字段区不够宽时 QLayout 会把这个 min 小的标签压扁（踩过，
        "个"被压成 2px）。
        """
        label = QLabel(text)
        label.setFont(ui_font(10.5))
        label.setObjectName("groupCaption")
        label.setMinimumWidth(label.fontMetrics().horizontalAdvance(text) + 2)
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
