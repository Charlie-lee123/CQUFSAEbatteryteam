# -*- coding: utf-8 -*-
"""量本轮用户报的四处界面细节，给出可验证的数字。

用户报的现象：
  ① 设置面板「接收历史」行：路径文字与「打开记录」按钮之间空白过大；
  ② 自绘标题栏三颗窗口键离窗口右上角还有较大间隙；
  ③ 回放提示条（点「打开历史数据」后出现）圆角偏小，和其他卡片不一致；
  ④ 回放提示条左侧那条竖向元素是什么。

本探针只**测量 + 截图**，不改任何设置。
"""
import ctypes
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import time

os.environ["QT_QPA_PLATFORM"] = "windows"
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

from PySide6.QtCore import QPoint, QRect, QSettings
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
from ui_theme import CAPTION_H, apply_scheme, ui_font

app.setStyle("Fusion")
app.setFont(ui_font(12.5))
apply_scheme("dark")
import ui_theme as _t
from main_window import MainWindow


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


out = os.path.join(_ROOT, "_check")
os.makedirs(out, exist_ok=True)

settings = QSettings("MimoWorks", "SerialAssistant")
snapshot = {key: settings.value(key) for key in settings.allKeys()}
# ⚠️ 不要 remove("geometry")：窗口关掉时会把它自己的几何写回配置，而那次写入
# 可能落在本探针的还原**之后**（QSettings 析构时才 flush），把用户记住的窗口
# 尺寸搞丢（实测丢过一次）。留着不动、最后按快照整体还原最稳。

window = MainWindow()
window._port_watch.stop()
window.resize(1200, 820)
window.show()
pump(0.9)

print("=" * 60)
print("② 标题栏三颗键")
bar = window._caption_bar
close = bar.btn_close
bar_at = bar.mapTo(window, bar.rect().topLeft())
close_at = close.mapTo(window, close.rect().topLeft())
print(f"窗口 {window.width()}x{window.height()}")
print(f"标题栏 左上=({bar_at.x()},{bar_at.y()}) 尺寸={bar.width()}x{bar.height()}"
      f"  其右端离窗口右边 {window.width() - (bar_at.x() + bar.width())}px")
print(f"关闭键 左上=({close_at.x()},{close_at.y()}) 尺寸={close.width()}x{close.height()}")
print(f"   -> 右边缘离窗口右边 = {window.width() - (close_at.x() + close.width())}px")
print(f"   -> 上边缘离窗口上边 = {close_at.y()}px")
print(f"   -> 下边缘离窗口上边 = {close_at.y() + close.height()}px"
      f"  (标题栏高 {CAPTION_H})")

shot = window.grab()
sc = shot.width() / window.width()
dpr = window.devicePixelRatioF()
crop = shot.copy(QRect(int(shot.width() - 260 * sc), 0,
                       int(260 * sc), int(64 * sc)))
crop = crop.scaled(int(crop.width() * 2), int(crop.height() * 2))
crop.save(os.path.join(out, "probe_caption_corner.png"))
print(f"截图 -> _check/probe_caption_corner.png (窗口dpr={dpr} 抓图缩放={sc:.3f})")

print("=" * 60)
print("③④ 回放提示条")
window._replay_active = True
window._refresh_replay_ui()
pump(0.5)
rbar = window._replay_bar
r_at = rbar.mapTo(window, rbar.rect().topLeft())
print(f"回放条 左上=({r_at.x()},{r_at.y()}) 尺寸={rbar.width()}x{rbar.height()}"
      f"  类={rbar.__class__.__name__}")
css = _t.stylesheet()
m = re.search(r"QWidget#replayBar\s*\{[^}]*\}", css)
print("QSS 里是否还画回放条的面（应为 None，面已改自绘）:",
      " ".join(m.group(0).split()) if m else "None ✓")
print(f"自绘用的圆角 = {getattr(rbar, 'RADIUS', None) or _t.CARD_RADIUS}"
      f"（CARD_RADIUS={_t.CARD_RADIUS}）")
for child in rbar.children():
    nm = getattr(child, "objectName", lambda: "")()
    txt = getattr(child, "text", lambda: "")()
    print(f"   子控件 {child.__class__.__name__:12s} name={nm!r} text={txt!r}")
    if child.__class__.__name__ == "QHBoxLayout":
        for i in range(child.count()):
            w = child.itemAt(i).widget()
            if w is not None:
                g = w.geometry()
                print(f"      [{i}] {w.__class__.__name__:12s}"
                      f" name={getattr(w, 'objectName', lambda: '')()!r}"
                      f" geom=({g.x()},{g.y()},{g.width()}x{g.height()})")

shot2 = window.grab()
sc2 = shot2.width() / window.width()
rc = shot2.copy(QRect(int(r_at.x() * sc2), int(r_at.y() * sc2),
                      int(150 * sc2), int(rbar.height() * sc2)))
rc = rc.scaled(int(rc.width() * 3), int(rc.height() * 3))
rc.save(os.path.join(out, "probe_replay_left.png"))
print("截图 -> _check/probe_replay_left.png")
# 整条（看圆角在两端的效果）
full = shot2.copy(QRect(int(r_at.x() * sc2), int(r_at.y() * sc2),
                        int(rbar.width() * sc2), int(rbar.height() * sc2)))
full = full.scaled(int(full.width() * 1.1), int(full.height() * 1.1))
full.save(os.path.join(out, "probe_replay_full.png"))
print("截图 -> _check/probe_replay_full.png")
window._replay_active = False
window._refresh_replay_ui()
pump(0.2)

print("=" * 60)
print("① 设置面板「接收历史」行")
import app_log

app_log.init()
from main_window import BUTTON_SPECS
from ui_controls import InkButton
from ui_settings import SettingsDialog
from ui_theme import glass_strength
from PySide6.QtWidgets import QLabel

dlg = SettingsDialog(window._theme_mode, glass_strength(),
                     BUTTON_SPECS, sorted(window._hidden_buttons), parent=window)
dlg.show()
pump(1.0)
print(f"对话框 {dlg.width()}x{dlg.height()}")
target = None
for btn in dlg.findChildren(InkButton):
    if btn.text() == "打开记录":
        target = btn
        break
print("「打开记录」按钮 =", target)
if target is not None:
    pos = target.mapTo(dlg, target.rect().topLeft())
    lay = target.parentWidget().layout() if target.parentWidget() else None
    print(f"   按钮 左上=({pos.x()},{pos.y()}) 尺寸={target.width()}x{target.height()}"
          f"  右边缘离对话框右边 {dlg.width() - (pos.x() + target.width())}px")
    print(f"   同行布局 spacing = {lay.spacing() if lay else '?'}")
    label = None
    if lay is not None:
        for i in range(lay.count()):
            item = lay.itemAt(i)
            w = item.widget()
            if w is None:
                print(f"   [{i}] <spacer {item.spacerItem()}>")
                continue
            g = w.geometry()
            print(f"   [{i}] {w.__class__.__name__:11s}"
                  f" text={getattr(w, 'text', lambda: '')()[:44]!r}"
                  f" geom=({g.x()},{g.y()},{g.width()}x{g.height()})"
                  f" sizeHint.w={w.sizeHint().width()}")
            if isinstance(w, QLabel):
                label = w
    if label is not None:
        # ⚠️ 两个控件的 geometry 都在各自父控件坐标系里，必须都映射到对话框再比
        lab_pt = label.mapTo(dlg, QPoint(0, 0))
        gap = pos.x() - (lab_pt.x() + label.width())
        print(f"   -> 标签右边缘 到 按钮左边缘 空白 = {gap}px"
              f"  (布局 spacing={lay.spacing() if lay else '?'}，"
              f"差值 {gap - (lay.spacing() if lay else 0)} 应为 0)")
        # 这一行在滚动区下方，先滚到它再截图（否则抓到的是一屏别的东西）
        from PySide6.QtWidgets import QScrollArea
        sa = dlg.findChild(QScrollArea)
        if sa is not None:
            sa.ensureWidgetVisible(label)
            pump(0.4)
            lab_pt = label.mapTo(dlg, QPoint(0, 0))
            pos = target.mapTo(dlg, QPoint(0, 0))
            print(f"   滚动后：标签 y={lab_pt.y()} 按钮 y={pos.y()}"
                  f" 标签右缘到按钮左缘 ="
                  f" {pos.x() - (lab_pt.x() + label.width())}px")
            print(f"   滚动后：按钮右边缘离对话框右边 ="
                  f" {dlg.width() - (pos.x() + target.width())}px")
    dlg_shot = dlg.grab()
    dsc = dlg_shot.width() / max(1, dlg.width())
    top = int(max(0, pos.y() - 46) * dsc)
    c2 = dlg_shot.copy(0, top, dlg_shot.width(), int(96 * dsc))
    c2 = c2.scaled(int(c2.width() * 1.5), int(c2.height() * 1.5))
    c2.save(os.path.join(out, "probe_hist_row.png"))
    print("截图 -> _check/probe_hist_row.png")
    # 「日志」行是同样的改法（按钮串在文字后面），把它一起拍进来看两行的观感
    log_btn = None
    for btn in dlg.findChildren(InkButton):
        if btn.text() == "打开文件夹":
            log_btn = btn
            break
    if log_btn is not None:
        sa2 = dlg.findChild(QScrollArea)
        if sa2 is not None:
            sa2.ensureWidgetVisible(log_btn)
            pump(0.3)
            lp = log_btn.mapTo(dlg, QPoint(0, 0))
            shot3 = dlg.grab()
            d3 = shot3.width() / max(1, dlg.width())
            top3 = int(max(0, lp.y() - 92) * d3)
            c3 = shot3.copy(0, top3, shot3.width(), int(190 * d3))
            c3 = c3.scaled(int(c3.width() * 1.4), int(c3.height() * 1.4))
            c3.save(os.path.join(out, "probe_log_rows.png"))
            print(f"「打开文件夹」右边缘离对话框右边 ="
                  f" {dlg.width() - (lp.x() + log_btn.width())}px")
            print("截图 -> _check/probe_log_rows.png")
dlg.reject()
pump(0.4)

window.close()
pump(0.3)
# ⚠️ 窗口**析构**时还会把几何写回配置（QSettings 到析构才 flush），那次写可能落在
# 下面这次还原**之后**，把用户记住的窗口尺寸覆盖掉 —— 先显式销毁窗口、跑一轮
# 事件，让它的写入落地，再做最后那次无条件还原。
window.deleteLater()
app.processEvents()
import gc as _gc

_gc.collect()
app.processEvents()
for key in settings.allKeys():
    if key not in snapshot:
        settings.remove(key)
for key, value in snapshot.items():
    settings.setValue(key, value)
settings.sync()
print("=" * 60)
print("完成（设置已还原）")
