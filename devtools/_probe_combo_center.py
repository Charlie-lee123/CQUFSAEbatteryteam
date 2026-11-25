# -*- coding: utf-8 -*-
r"""真平台探针：配置卡 7 个下拉框「框内文字水平居中」的像素验收。

判据（写 _check/combo_center_probe.txt）：
  A 7 个框的**文字墨迹中心**落在「去掉右侧 24px 箭头」的内容区中线 ±4px
  B 竖直墨迹中心仍落在框高一半 ±3px（不破坏 _fit_text_vertical 的标定）
  C 可编辑框的 QLineEdit 对齐确为 AlignCenter（不可编辑框走计算 padding）
  D 设置面板的 SOC 来源下拉框仍是左对齐（本改动绝不污染全局 QSS）
  E QSettings 前后零变化

取样办法：同一个控件渲染两张图（原文 / 空文字），逐像素差分出**纯文字墨迹**，
避开 QSS 底色、边框和自绘 chevron；⛔ 不用 widget.grab() 的默认合成截图量色。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox

import main_window
import ui_settings
import ui_theme

ATTRS = ("cmb_link", "cmb_port", "cmb_baud", "cmb_stopbits",
         "cmb_bytesize", "cmb_parity", "cmb_rx_lines")
ARROW_W = 24

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def _render(w) -> QImage:
    """按逻辑像素渲染控件（含子控件）；两张图相减即可只留文字墨迹。"""
    pm = QPixmap(w.size())
    pm.fill(Qt.GlobalColor.transparent)
    w.render(pm)
    return pm.toImage().convertToFormat(QImage.Format.Format_RGB32)


def _ink_box(combo, text: str) -> tuple[int, int, int, int] | None:
    """渲染 原文 vs 空文字，差分出文字墨迹盒 (left, top, right, bottom)。"""
    edit = combo.lineEdit()
    combo.blockSignals(True)
    if edit is not None:
        edit.blockSignals(True)
        old_text = edit.text()
        edit.setText(text)
        full = _render(combo)
        edit.setText("")
        empty = _render(combo)
        edit.setText(old_text)
        edit.blockSignals(False)
    else:
        old_idx = combo.currentIndex()
        idx = combo.findText(text)
        combo.setCurrentIndex(idx if idx >= 0 else -1)
        full = _render(combo)
        combo.setCurrentIndex(-1)
        empty = _render(combo)
        combo.setCurrentIndex(old_idx)
    combo.blockSignals(False)

    w, h = combo.width(), combo.height()
    xs: list[int] = []
    ys: list[int] = []
    for x in range(2, max(3, w - 30)):   # 右 30px 是自绘 chevron 区
        for y in range(2, max(3, h - 2)):
            a, b = empty.pixelColor(x, y), full.pixelColor(x, y)
            if (abs(a.red() - b.red()) + abs(a.green() - b.green())
                    + abs(a.blue() - b.blue())) > 30:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    ui_theme.apply_scheme("dark")
    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}
    win = None
    dlg = None
    try:
        win = main_window.MainWindow()
        win.resize(1400, 900)
        win.show()
        app.processEvents()
        for _ in range(6):              # 等布局 + _fit_text_vertical 的 QTimer 补量
            app.processEvents()

        for attr in ATTRS:
            combo = getattr(win, attr)
            # 串口列表可能为空：给它一段临时文字量墨迹，量完还原。
            text = combo.currentText() or ("COM3" if attr == "cmb_port" else "")
            box = _ink_box(combo, text)
            if box is None:
                check(f"A {attr} 量到文字墨迹", False, "text={text!r}")
                continue
            left, top, right, bottom = box
            cx = (left + right) / 2.0
            cy = (top + bottom + 1) / 2.0
            target_x = (combo.width() - ARROW_W) / 2.0
            target_y = combo.height() / 2.0
            check(f"A {attr} 文字水平居中（±4px）",
                  abs(cx - target_x) <= 4,
                  f"text={text!r} ink={box} cx={cx:.1f} target={target_x:.1f} "
                  f"w={combo.width()} style={combo.styleSheet()!r}")
            check(f"B {attr} 竖直墨迹中心未动（±3px）",
                  abs(cy - target_y) <= 3,
                  f"cy={cy:.1f} target={target_y:.1f} h={combo.height()}")

        editable_align = {}
        for attr in ATTRS:
            combo = getattr(win, attr)
            edit = combo.lineEdit()
            if combo.isEditable() and edit is not None:
                editable_align[attr] = (int(edit.alignment().value),
                                        int(Qt.AlignmentFlag.AlignCenter.value))
        check("C 三个可编辑框的 QLineEdit 已 AlignCenter",
              len(editable_align) == 3
              and all(a == b for a, b in editable_align.values()),
              str(editable_align))

        # D 设置面板：SOC 来源必须仍贴左边（反证全局 QSS 没被污染）
        dlg = ui_settings.SettingsDialog("dark")
        dlg.show()
        app.processEvents()
        soc = None
        for c in dlg.findChildren(QComboBox):
            values = [c.itemData(i) for i in range(c.count())]
            if "calc" in values and "read" in values:
                soc = c
                break
        if soc is None:
            check("D 找到设置面板 SOC 来源下拉框", False)
        else:
            check("D1 SOC 来源下拉框未打居中标记",
                  not soc.property("centerText"),
                  f"property={soc.property('centerText')!r}")
            box = _ink_box(soc, soc.currentText())
            target_x = (soc.width() - ARROW_W) / 2.0
            check("D2 SOC 来源文字仍左对齐",
                  box is not None and box[0] <= 16
                  and (box[0] + box[2]) / 2.0 < target_x - 15,
                  f"ink={box} target_center={target_x:.1f} "
                  f"style={soc.styleSheet()!r}")
        dlg.close()
        dlg = None

        after = {k: qs.value(k) for k in qs.allKeys()}
        check("E QSettings 前后零变化", after == snap,
              f"keys {len(snap)} -> {len(after)}")
    finally:
        if dlg is not None:
            dlg.close()
        if win is not None:
            win.close()
        for k in qs.allKeys():
            qs.remove(k)
        for k, v in snap.items():
            qs.setValue(k, v)
        qs.sync()

    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    text = "\n".join(f"{'PASS' if ok else 'FAIL'}  {n}   {d}"
                     for n, ok, d in _RESULTS)
    out = Path(_ROOT) / "_check" / "combo_center_probe.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + f"\n\n合计 {passed}/{len(_RESULTS)} PASS\n",
                   encoding="utf-8")
    print(f"\n合计 {passed}/{len(_RESULTS)} PASS  ->  "
          f"{'IS_PASS: YES' if passed == len(_RESULTS) else 'IS_PASS: NO'}")
    return 0 if passed == len(_RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
