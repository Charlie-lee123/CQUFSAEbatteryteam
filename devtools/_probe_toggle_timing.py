# -*- coding: utf-8 -*-
"""探针：拨动开关的状态翻转时机（按下 vs 松开）。

用户报「设置里的这些拨动开关按下有延迟」——量一下三类路径各自在
**按下瞬间**和**松开瞬间**开关是否已经翻转：
  A. 「界面按钮」区带文字的 Switch（对照组）
  B. 「定制功能」区空文字 Switch（点轨道）
  C. 「定制功能」区行标签（点文字）
REVERSE 标记：本脚本只读不改，可在主工作区安全运行。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPointF, QEvent, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from ui_settings import SettingsDialog
import customization
from PySide6.QtCore import QSettings


def _snapshot():
    s = QSettings("MimoWorks", "SerialAssistant")
    return {k: s.value(k) for k in s.allKeys()}


def _restore(snap):
    s = QSettings("MimoWorks", "SerialAssistant")
    for k in s.allKeys():
        if k not in snap:
            s.remove(k)
    for k, v in snap.items():
        s.setValue(k, v)
    s.sync()


def press_only(widget):
    center = QPointF(widget.rect().center())
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, center,
                     QPointF(widget.mapToGlobal(widget.rect().center())),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    widget.event(ev)


def release_only(widget):
    center = QPointF(widget.rect().center())
    ev = QMouseEvent(QEvent.Type.MouseButtonRelease, center,
                     QPointF(widget.mapToGlobal(widget.rect().center())),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier)
    widget.event(ev)


def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    snap = _snapshot()
    dialog = SettingsDialog("dark", 0,
                            button_specs=[("encoding", "编码", "发送区")])
    dialog.show()
    QTest.qWait(50)
    try:
        # A: 界面按钮区开关（对照组）
        btn_sw = next(iter(dialog._button_switches.values()))
        was = btn_sw.isChecked()
        press_only(btn_sw)
        print(f"A 按下瞬间: checked {was} -> {btn_sw.isChecked()}")
        release_only(btn_sw)
        print(f"A 松开瞬间: checked -> {btn_sw.isChecked()}")
        btn_sw.setChecked(was)

        # B: 定制功能区开关本体
        key = next(s["key"] for s in customization.specs() if s["kind"] == "bool")
        sw = dialog._custom_widgets[key]
        was = sw.isChecked()
        press_only(sw)
        print(f"B[{key}] 按下瞬间: checked {was} -> {sw.isChecked()}")
        release_only(sw)
        print(f"B[{key}] 松开瞬间: checked -> {sw.isChecked()}")
        sw.setChecked(was)

        # C: 行标签
        spec = next(s for s in customization.specs() if s["kind"] == "bool")
        sw = dialog._custom_widgets[spec["key"]]
        label = next(c for c in dialog.findChildren(QLabel)
                     if c.text() == spec["label"])
        was = sw.isChecked()
        press_only(label)
        print(f"C[{spec['label']}] 按下瞬间: checked {was} -> {sw.isChecked()}")
        release_only(label)
        print(f"C[{spec['label']}] 松开瞬间: checked -> {sw.isChecked()}")
        sw.setChecked(was)
    finally:
        dialog.close()
        QTest.qWait(30)
        _restore(snap)


if __name__ == "__main__":
    main()
