# -*- coding: utf-8 -*-
"""真实平台探针（只读）：验证「个性化」分组 + 短下拉收窄，抓面板截图。

⛔ QSettings 全量快照 + finally 还原（不 clear、不重置用户设置）；
   app.quit()，绝不 os._exit()。不修改任何产品代码。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
# ⚠️ 不设 QT_SCALE_FACTOR：本机系统缩放已是 200%，再叠一层会变成 dpr=4.0。
# 让 Qt 走系统原生 high-DPI，实测 dpr=2.0（截图按设备像素保存）。
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFormLayout,
    QPushButton,
    QStyle,
    QStyleOptionComboBox,
)


def main() -> int:
    app = QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    del settings

    try:
        import selftest
        from ui_settings import COMBO_W, SHORT_CHOICE_W, SettingsDialog

        window = selftest.make_window()
        window.show()
        app.processEvents()

        from PySide6.QtTest import QTest
        QTest.qWait(300)
        dialog = SettingsDialog(window._theme_mode, 0, [], window)
        dialog.resize(1180, 900)
        dialog.show()
        QTest.qWait(500)

        # ---- 1) 分组顺序实测（跨越列标题 + 字段行）----
        form = dialog.form
        rows = []
        for r in range(form.rowCount()):
            sp = form.itemAt(r, QFormLayout.ItemRole.SpanningRole)
            if sp is not None and sp.widget() is not None:
                widget = sp.widget()
                if widget.objectName() == "groupCaption":
                    rows.append(("caption", widget.text()))
                continue
            lab = form.itemAt(r, QFormLayout.ItemRole.LabelRole)
            if lab is not None and lab.widget() is not None:
                rows.append(("field", lab.widget().text()))
        print("[groups] " + " > ".join(t for k, t in rows if k == "caption"))
        want = ["主题", "材质强度", "界面材质", "开屏动画", "动画文件", "动画帧率"]
        idx = next((j for j, (k, t) in enumerate(rows)
                    if k == "caption" and t == "个性化"), None)
        assert idx is not None, f"没有「个性化」分组：{rows[:24]}"
        got = [t for k, t in rows[idx + 1:idx + 1 + len(want)]]
        assert got == want, f"「个性化」组内不是六项连续：{got}"
        assert ("caption", "动画") not in rows, "原「动画」组标题仍在"
        print(f"[personality] 组内顺序 = {' / '.join(got)}")

        # ---- 2) 动画帧率行没丢复原按钮 ----
        fps = dialog._custom_widgets["anim_fps"]
        resets = [b for b in fps.parentWidget().findChildren(QPushButton)
                  if b.accessibleName() == "恢复默认值"]
        assert len(resets) == 1, f"动画帧率行复原按钮数量：{len(resets)}"
        print(f"[anim_fps] 复原按钮=1 滑杆宽={fps.width()} "
              f"行容器宽={fps.parentWidget().width()}")

        # ---- 3) 短下拉：明显变窄 + 两字选项不截断 ----
        for key in ("soc_source", "volt_source"):
            w = dialog._custom_widgets[key]
            opt = QStyleOptionComboBox()
            opt.initFrom(w)
            opt.currentText = w.currentText()
            cr = w.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox, opt,
                QStyle.SubControl.SC_ComboBoxEditField, w)
            need = max(w.fontMetrics().horizontalAdvance(w.itemText(i))
                       for i in range(w.count()))
            assert w.width() == SHORT_CHOICE_W, \
                f"{key} 宽 {w.width()} != SHORT_CHOICE_W({SHORT_CHOICE_W})"
            assert w.width() < COMBO_W, \
                f"{key} 没有比 COMBO_W({COMBO_W}) 窄"
            assert need <= cr.width(), \
                f"{key} 选项文字被截：需 {need}px，内容区仅 {cr.width()}px"
            print(f"[combo] {key} 宽={w.width()}(COMBO_W={COMBO_W}) "
                  f"最长={need}px 内容区={cr.width()}px "
                  f"选项={[w.itemText(i) for i in range(w.count())]}")

        # ---- 4) 截图 ----
        out = ROOT / "_check" / "personality_panel.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        assert dialog.grab().save(str(out)), "截图保存失败"
        print(f"[shot] {out} size={dialog.size().toTuple()} "
              f"dpr={app.devicePixelRatio():.1f}")

        # ---- 5) 补充截图：滚到 SOC 组，目视核对「本地 / 主控」窄框 ----
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QScrollArea
        scrolls = dialog.findChildren(QScrollArea)
        assert len(scrolls) == 1, f"滚动区数量不对：{len(scrolls)}"
        sa = scrolls[0]
        soc = dialog._custom_widgets["soc_source"]
        y = soc.mapTo(sa.widget(), QPoint(0, 0)).y()
        sa.verticalScrollBar().setValue(max(0, y - 140))
        QTest.qWait(200)
        out2 = ROOT / "_check" / "personality_panel_soc.png"
        assert dialog.grab().save(str(out2)), "SOC 组截图保存失败"
        print(f"[shot] {out2} scroll={sa.verticalScrollBar().value()} "
              f"soc_y={y}")

        dialog.reject()
        QTest.qWait(200)
        window.close()
        window.deleteLater()
        app.processEvents()
    finally:
        # 全量还原，但不 clear —— 绝不重置用户设置。
        s = QSettings("MimoWorks", "SerialAssistant")
        for k in list(s.allKeys()):
            if k not in snapshot:
                s.remove(k)
        for k, v in snapshot.items():
            s.setValue(k, v)
        s.sync()
        del s
        app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
