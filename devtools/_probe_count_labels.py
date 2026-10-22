# -*- coding: utf-8 -*-
"""数量行文案探针：渲染「电芯数量：〔N〕个 / 温度采集数量：〔N〕个」并量宽度。

⛔ 不写任何 QSettings / customization：只调 `_rebuild_module_rows`（纯建控件），
   不走 `_on_module_value` 的持久化路径。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

import ui_settings  # noqa: E402
import ui_theme  # noqa: E402

OUT = os.path.join(ROOT, "_check", "count_labels.png")

app = QApplication(sys.argv)
# 主程序是 Fusion + 全站 QSS（main.py 里 app.setStyle("Fusion")）—— 探针要跟着，
# 否则截出来的面板跟产品长得不一样。
app.setStyle("Fusion")
ui_theme.apply_scheme("dark")
dialog = ui_settings.SettingsDialog("dark", 55)

# 五个模组：25/24/25/24/25 电芯、8/5/6/5/8 温度（模拟用户截图里的那种配置）
dialog._rebuild_module_rows([25, 24, 25, 24, 25], [8, 5, 6, 5, 8])
app.processEvents()

# 1) 文案：每个 holder 必须是 前缀 + 输入框 + "个"
bad = 0
for label, field in dialog._module_rows:
    holders = field.findChildren(QWidget)
    for holder in holders:
        layout = holder.layout()
        if layout is None:
            continue
        texts = []
        for i in range(layout.count()):
            item = layout.itemAt(i).widget()
            if isinstance(item, QLabel):
                texts.append(item.text())
        if texts:
            print(f"  {label.text()}: {texts}")
            # 只有前缀（量词"个"已按用户要求删掉）
            if texts not in (["电芯数量："], ["温度采集数量："]):
                bad += 1
print("文案不符的 holder 数:", bad)

# 2) 宽度：行内字段的 sizeHint
for label, field in dialog._module_rows[:1]:
    print(f"  行 sizeHint 宽 {field.sizeHint().width()}px")
    break

# 3) 整行渲染一张图（拿第一、第二行 + 模组个数行）——按面板最小宽起手
dialog.resize(dialog.minimumWidth(), 700)
app.processEvents()
grab = dialog.grab()
ok = grab.save(OUT)
print("saved:", ok, OUT, grab.width(), "x", grab.height())

# 4) 裁切：以**滚动区视口**右缘为界（按对话框算会漏报——内容是被视口裁的）
from PySide6.QtWidgets import QScrollArea  # noqa: E402

sa = dialog.findChild(QScrollArea)
limit = sa.viewport().width() if sa else dialog.width()
overflow = 0
for w in dialog.findChildren(QWidget):
    if not w.isVisible() or not sa or not sa.widget().isAncestorOf(w):
        continue
    right = w.mapTo(sa.viewport(), w.rect().topRight()).x()
    if right > limit - 2:
        overflow += 1
        print(f"  裁切: {w.__class__.__name__} 右缘 {right} > 视口 {limit}")
print("被视口裁掉的控件数:", overflow)
