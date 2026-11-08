"""车辆一节：选车后截图 + 关键控件几何（模组数框 / 逐模组字段）。

用法：python devtools/_shot_vehicle.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPoint, QRect  # noqa: E402
from PySide6.QtGui import QPixmap, QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget  # noqa: E402

import customization  # noqa: E402
import rx_store  # noqa: E402
# 探针环境里 rx_store.db_path() 解析不到用户目录会返回 None → 会话数据库一节被跳过。
# 这里塞一个长路径，验证 lbl_db_path 现在**完整显示**（不再 _elide_middle 掐成 …）。
rx_store.db_path = lambda: (  # noqa: E731
    r"C:\Users\Lenovo\AppData\Roaming\SerialAssistant\logs\sessions.db")
import ui_theme  # noqa: E402
from ui_settings import SettingsDialog  # noqa: E402
from ui_theme import qcolor, ui_font  # noqa: E402

CHECK = ROOT / "_check"


def _rect(dialog, widget):
    top_left = widget.mapTo(dialog, QPoint(0, 0))
    return top_left.x(), top_left.y(), widget.width(), widget.height()


def main() -> int:
    CHECK.mkdir(exist_ok=True)
    # 准备一台车：模组数较多，便于看到逐模组行
    customization._reset_for_tests() if hasattr(customization, "_reset_for_tests") else None
    names = customization.vehicle_names()
    if not names:
        customization.save_vehicle("E74-2026", [25, 25, 25], [8, 8, 8])
        names = customization.vehicle_names()
    target = names[0]
    customization.select_vehicle(target)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))
    ui_theme.apply_scheme("dark")
    app.setStyleSheet(ui_theme.stylesheet())

    dialog = SettingsDialog("dark", 0, parent=None)
    dialog.show()
    app.processEvents()
    QTest.qWait(500)
    app.processEvents()

    scroll = dialog.findChild(QScrollArea)
    body = scroll.widget()
    print(f"── 面板 {dialog.width()}x{dialog.height()}  body 最小宽={body.minimumSizeHint().width()}")

    # 关键控件几何
    for attr, name in (
        ("spin_vehicle_season", "赛季"),
        ("cmb_vehicle_number", "车号"),
        ("spin_vehicle_modules", "模组数"),
    ):
        w = getattr(dialog, attr, None)
        if w is not None:
            x, y, ww, hh = _rect(dialog, w)
            print(f"   {name:6s} x={x:4d} y={y:4d} w={ww:4d} h={hh:3d}  text={w.text() if hasattr(w,'text') else ''}")

    # 逐模组字段的总宽（最后一个 MODULE 行的两个输入框）
    edits = body.findChildren(QWidget)
    # 通过 objectName 找 countEdit 框
    counts = [w for w in body.findChildren(QWidget) if getattr(w, "objectName", lambda: "")() == "countEdit"]
    print(f"   逐模组 countEdit 个数={len(counts)}")
    for c in counts:
        x, y, ww, hh = _rect(dialog, c)
        print(f"      countEdit x={x:4d} y={y:4d} w={ww:4d} h={hh:3d}")

    # 整块截图
    full = QPixmap(body.size())
    full.fill(qcolor("surface"))
    body.render(full, QPoint(0, 0))
    target_p = CHECK / "vehicle_full.png"
    full.save(str(target_p))
    print(f"   整块截图 → {target_p} ({full.width()}x{full.height()})")

    # 只裁车辆一节（从「车辆（电池箱）」标题到第一个 MODULE 行结束）
    captions = [w for w in body.findChildren(QWidget) if w.property("text") == "车辆（电池箱）" or getattr(w, "text", lambda: "")() == "车辆（电池箱）"]
    # 用 _caption 的 objectName
    cap = None
    for w in body.findChildren(QWidget):
        if getattr(w, "text", lambda: "")() == "车辆（电池箱）":
            cap = w
            break
    if cap is not None:
        top = max(0, cap.mapTo(body, QPoint(0, 0)).y() - 8)
        # 模组数框位置
        mod = getattr(dialog, "spin_vehicle_modules", None)
        if mod is not None:
            bottom = mod.mapTo(body, QPoint(0, 0)).y() + mod.height() + 12
            rect = QRect(0, top, body.width(), bottom - top)
            pix = QPixmap(rect.size())
            pix.fill(qcolor("surface"))
            body.render(pix, QPoint(0, 0), rect)
            tp = CHECK / "vehicle_section.png"
            pix.save(str(tp))
            print(f"   车辆节裁图 → {tp} ({rect.width()}x{rect.height()})")

    # 定制功能一节：从「定制功能」标题到「日志」标题之前
    custom_cap = None
    log_cap = None
    for w in body.findChildren(QWidget):
        t = getattr(w, "text", lambda: "")()
        if t == "定制功能":
            custom_cap = w
        elif t == "日志":
            log_cap = w
    if custom_cap is not None:
        ctop = max(0, custom_cap.mapTo(body, QPoint(0, 0)).y() - 8)
        cbot = (log_cap.mapTo(body, QPoint(0, 0)).y()
                if log_cap is not None else body.height())
        crect = QRect(0, ctop, body.width(), cbot - ctop)
        cpix = QPixmap(crect.size())
        cpix.fill(qcolor("surface"))
        body.render(cpix, QPoint(0, 0), crect)
        ctp = CHECK / "custom_section.png"
        cpix.save(str(ctp))
        print(f"   定制功能节裁图 → {ctp} ({crect.width()}x{crect.height()})")
        # 量一下这一节里所有 ChevronSpinBox 的宽度，确认统一且不过宽
        spins = [w for w in body.findChildren(QWidget)
                 if w.__class__.__name__ == "ChevronSpinBox"]
        widths = sorted({w.width() for w in spins})
        print(f"   全面板 ChevronSpinBox 宽度集合={widths}")

    # 会话数据库一节：从「会话数据库」标题到「协议」标题之前；重点查路径是否完整（无 …）
    db_cap = None
    proto_cap = None
    for w in body.findChildren(QWidget):
        t = getattr(w, "text", lambda: "")()
        if t == "会话数据库":
            db_cap = w
        elif t == "协议":
            proto_cap = w
    if db_cap is not None:
        dtop = max(0, db_cap.mapTo(body, QPoint(0, 0)).y() - 8)
        dbot = (proto_cap.mapTo(body, QPoint(0, 0)).y()
                if proto_cap is not None else body.height())
        drect = QRect(0, dtop, body.width(), dbot - dtop)
        dpix = QPixmap(drect.size())
        dpix.fill(qcolor("surface"))
        body.render(dpix, QPoint(0, 0), drect)
        dtp = CHECK / "session_db_section.png"
        dpix.save(str(dtp))
        print(f"   会话数据库节裁图 → {dtp} ({drect.width()}x{drect.height()})")
        lbl = getattr(dialog, "lbl_db_path", None)
        if lbl is not None:
            txt = lbl.text().replace("\n", "")
            print(f"   lbl_db_path 是否含省略号='…' in txt={('…' in txt) or ('...' in txt)}")
            print(f"   lbl_db_path 文本预览={txt[:80]}")

    dialog.close()
    dialog.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
