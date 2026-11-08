"""设置面板「日志 / 接收历史」两行按钮的几何自检 + 截图（+ 在线车辆库外观）。

用户对观感极挑剔，而改这类"两排等宽对齐"的活光看代码不算数 —— 这个探针同时
交出三样东西：

1. **几何量**：四颗按钮各自的 ``geometry()``（相对面板）、两列左边缘是否一致、
   四颗是否同宽、以及面板的 ``minimumSizeHint().width()`` / ``minimumWidth()``
   （本次改动的核心指标：日志一行挤四颗按钮把面板撑宽了，改成 2×2 应该降下来）；
2. **截图**：``_check/settings_log_rows.png``（日志 + 接收历史两节）、
   ``_check/settings_panel.png``（整块滚动内容）与 ``_check/vehicle_library.png``；
3. **不碰用户数据**：日志目录/历史目录都指到临时目录，QSettings 快照/还原；
   在线车辆库用 ``auto_refresh=False`` 打开，不联网。

用法：python devtools/_shot_settings_log.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPoint, QRect, QSettings  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QScrollArea, QWidget,
)

import app_log  # noqa: E402
import rx_history  # noqa: E402
import ui_theme  # noqa: E402
from ui_settings import SettingsDialog  # noqa: E402
from ui_theme import qcolor, ui_font  # noqa: E402

CHECK = ROOT / "_check"


def _isolate_data() -> Path:
    """把日志 + 接收历史都指到临时目录（探针绝不写用户的日志/历史）。"""
    tmp = Path(tempfile.mkdtemp(prefix="sa-probe-shot-"))
    app_log._PATH = tmp / "serialassistant.log"
    app_log._FILE = None
    rx_history._resolve_dir = lambda: tmp          # 同 selftest 的隔离手法
    # 造两份"别的车"的历史文件，好让「接收历史」的车号下拉有内容可列
    for key in ("E74-2026", "E73-2026"):
        (tmp / rx_history.file_for(key)).write_text(
            "2026-10-31 10:00:00\nVoltage1: 01.3800 02.3800\n", encoding="utf-8")
    rx_history.load("E74-2026")
    rx_history._timer_stop()                       # 别让 1s 刷盘定时器跑起来
    return tmp


def _rect(dialog: QWidget, widget: QWidget) -> tuple[int, int, int, int]:
    """控件在面板坐标系里的 (x, y, w, h)。"""
    top_left = widget.mapTo(dialog, QPoint(0, 0))
    return top_left.x(), top_left.y(), widget.width(), widget.height()


def _find_caption(body: QWidget, text: str) -> QLabel | None:
    for label in body.findChildren(QLabel):
        if label.text() == text:
            return label
    return None


def _report_log_row(dialog: SettingsDialog) -> None:
    """四颗按钮的几何：两列左边缘 / 同宽 / 整块宽度。"""
    buttons = (
        ("导出日志", getattr(dialog, "btn_export_log", None)),
        ("打开文件夹", getattr(dialog, "btn_open_log_dir", None)),
        ("数据 CSV", getattr(dialog, "btn_export_csv", None)),
        ("数据 JSON", getattr(dialog, "btn_export_json", None)),
    )
    print("── 日志区按钮几何（面板坐标系）")
    boxes: dict[str, tuple[int, int, int, int]] = {}
    for name, button in buttons:
        if button is None:
            print(f"   {name:8s} 缺失")
            continue
        box = _rect(dialog, button)
        boxes[name] = box
        print(f"   {name:8s} x={box[0]:4d} y={box[1]:4d} w={box[2]:4d} h={box[3]}")
    left = [boxes[n] for n in ("导出日志", "数据 CSV") if n in boxes]
    right = [boxes[n] for n in ("打开文件夹", "数据 JSON") if n in boxes]
    if len(boxes) == 4:
        left_x = sorted({b[0] for b in left})
        right_x = sorted({b[0] for b in right})
        widths = sorted({b[2] for b in boxes.values()})
        rows_y = sorted({boxes["导出日志"][1], boxes["数据 CSV"][1]})
        print(f"   左列 x={left_x}  右列 x={right_x}  → 对齐="
              f"{'是' if len(left_x) == 1 and len(right_x) == 1 else '否'}")
        print(f"   四颗宽度={widths}  → 同宽={'是' if len(widths) == 1 else '否'}")
        print(f"   两排 y={rows_y}")
        print(f"   整块宽={max(b[0] + b[2] for b in boxes.values())
                         - min(b[0] for b in boxes.values())}px")

    print("── 接收历史行")
    for attr, label in (("cmb_history", "车号下拉"), ("btn_open_history", "打开记录"),
                        ("btn_history_dir", "选择目录")):
        widget = getattr(dialog, attr, None)
        if widget is None:
            print(f"   {label} 缺失")
            continue
        x, y, w, h = _rect(dialog, widget)
        print(f"   {label} x={x:4d} y={y:4d} w={w:4d} h={h}"
              f" enabled={widget.isEnabled()}")
    combo = getattr(dialog, "cmb_history", None)
    if combo is not None:
        print(f"   车号下拉条目={[combo.itemText(i) for i in range(combo.count())]}")
        need = combo.fontMetrics().horizontalAdvance(combo.currentText())
        print(f"   当前文字宽={need}px  下拉宽={combo.width()}px"
              f"  → 放得下={'是' if need + 40 <= combo.width() else '否'}")
    # 路径文字挪到了按钮上方：量一下"标签底边 → 按钮顶边"的间距，确认没被拉开
    for label_attr, button_attr, name in (
            ("lbl_log_path", "btn_export_log", "日志"),
            ("lbl_history_path", "btn_open_history", "接收历史")):
        label = getattr(dialog, label_attr, None)
        button = getattr(dialog, button_attr, None)
        if label is None or button is None:
            continue
        _x, label_y, _w, label_h = _rect(dialog, label)
        _bx, button_y, _bw, _bh = _rect(dialog, button)
        print(f"   {name}：标签底边→按钮顶边 = {button_y - (label_y + label_h)}px"
              f"（左边缘差 {_bx - _x}px）")


def _report_min_width_sources(dialog: SettingsDialog, body: QWidget) -> None:
    """面板最小宽度到底由谁决定（一行一列地量，不靠猜）。

    实测模型（与代码里 ``__init__`` 末尾那行一致）::

        body.minimumSizeHint = 最宽标签列 + 18 + 最宽字段 + 12(右侧滚动条余量)
        panel.minimumWidth   = max(560, body + 52)

    ⛔ 调这一节的时候别只看"哪一行最宽"：QFormLayout 的标签列是**全表共用**
    的（取最宽那个标签），跨两列的行（日志 / 接收历史 / 车辆那几行）只要不超过
    合计值就不参与定宽 —— 2026-10-31 就是在这里把"选车行 478 是瓶颈"判错了。
    """
    form = getattr(dialog, "form", None)
    if form is None:
        return
    rows = []
    label_col, label_text = 0, ""
    for index in range(form.rowCount()):
        field = form.itemAt(index, form.ItemRole.FieldRole)
        label = form.itemAt(index, form.ItemRole.LabelRole)
        field_w = field.widget() if field is not None else None
        label_w = label.widget() if label is not None else None
        if label_w is not None:
            width = label_w.minimumSizeHint().width()
            if width > label_col:
                label_col = width
                label_text = getattr(label_w, "text", lambda: "")()
        if field_w is None:
            continue
        rows.append((field_w.minimumSizeHint().width(), index,
                     type(field_w).__name__))
    rows.sort(reverse=True)
    field_w, field_row, _kind = rows[0] if rows else (0, -1, "")
    spacing = form.horizontalSpacing()
    body_min = body.minimumSizeHint().width()
    print(f"   body 最小宽 = {body_min}px"
          f"  ＝ 标签列 {label_col}({label_text[:16]!r})"
          f" + {spacing} + 最宽字段 {field_w}(row{field_row}) + 12")
    print(f"   最宽的三行字段：{[(w, f'row{i}') for w, i, _ in rows[:3]]}")
    print(f"   面板最小宽 = max(560, body + 52) = {max(560, body_min + 52)}")


def _grab_log_section(dialog: SettingsDialog) -> None:
    """从滚动内容里裁出「日志 + 接收历史」两节（不受视口高度限制）。"""
    scroll = dialog.findChild(QScrollArea)
    if scroll is None or scroll.widget() is None:
        print("   没找到滚动区，跳过裁图")
        return
    body = scroll.widget()
    # body 自己是透明的（真实窗口里透出的是面板玻璃底）→ 先铺主题底色再渲染，
    # 不然浅色药丸按钮在白底上"看着像实心黑块"，评图会失真。
    full = QPixmap(body.size())
    full.fill(qcolor("surface"))
    body.render(full, QPoint(0, 0))
    full.save(str(CHECK / "settings_panel.png"))
    log_caption = _find_caption(body, "日志")
    if log_caption is None:
        print("   没找到「日志」小标题，跳过裁图")
        return
    anchors = [w for w in (getattr(dialog, "btn_history_dir", None),
                           getattr(dialog, "btn_open_history", None),
                           getattr(dialog, "btn_export_json", None),
                           getattr(dialog, "lbl_history_path", None)) if w is not None]
    if not anchors:
        anchors = [log_caption]
    top = max(0, log_caption.mapTo(body, QPoint(0, 0)).y() - 8)
    bottom = max(w.mapTo(body, QPoint(0, 0)).y() + w.height() for w in anchors) + 12
    rect = QRect(0, top, body.width(), bottom - top)
    # body 自己是透明的（真实窗口里透出的是面板玻璃底），直接 grab 会得到白底，
    # 深色主题的浅底药丸按钮在白底上"看着像实心黑块"。这里先铺主题底色再渲染，
    # 出来的图才是用户在面板里看到的观感。
    pix = QPixmap(rect.size())
    pix.fill(qcolor("surface"))
    body.render(pix, QPoint(0, 0), rect)
    target = CHECK / "settings_log_rows.png"
    pix.save(str(target))
    print(f"   日志区裁图 {rect.width()}x{rect.height()} → {target}")


def _vehicle_library() -> None:
    """在线车辆库对话框：只截图与几何，不联网（auto_refresh=False）。"""
    try:
        from ui_settings import VehicleLibraryDialog
    except ImportError:
        print("── 在线车辆库：ui_settings 里还没有 VehicleLibraryDialog")
        return
    dialog = VehicleLibraryDialog("dark", parent=None, auto_refresh=False)
    dialog.resize(640, 620)
    dialog.show()
    QTest.qWait(280)
    app = QApplication.instance()
    app.processEvents()
    x, y, w, h = dialog.geometry().getRect()
    print(f"── 在线车辆库 {w}x{h} at ({x},{y})"
          f"  最小宽={dialog.minimumSizeHint().width()}")
    target = CHECK / "vehicle_library.png"
    dialog.grab().save(str(target))
    print(f"   截图 → {target}")
    dialog.close()
    dialog.deleteLater()


def main() -> int:
    CHECK.mkdir(exist_ok=True)
    _isolate_data()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))
    ui_theme.apply_scheme("dark")
    app.setStyleSheet(ui_theme.stylesheet())

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    try:
        dialog = SettingsDialog("dark", 0, parent=None)
        dialog.show()
        app.processEvents()
        QTest.qWait(500)
        app.processEvents()

        print(f"── 设置面板 {dialog.width()}x{dialog.height()}"
              f"  minimumSizeHint().width()={dialog.minimumSizeHint().width()}"
              f"  实际最小宽={dialog.minimumWidth()}")
        scroll = dialog.findChild(QScrollArea)
        if scroll is not None and scroll.widget() is not None:
            body = scroll.widget()
            print(f"   body 最小宽={body.minimumSizeHint().width()}"
                  f"  宽={body.width()}  视口={scroll.viewport().width()}")
            _report_min_width_sources(dialog, body)
        _report_log_row(dialog)
        _grab_log_section(dialog)

        dialog.close()
        dialog.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    finally:
        settings = QSettings("MimoWorks", "SerialAssistant")
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            settings.setValue(key, value)
        settings.sync()

    _vehicle_library()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
