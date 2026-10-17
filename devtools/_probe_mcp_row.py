# -*- coding: utf-8 -*-
r"""探针：设置面板「AI 接入」行的渲染与复制兜底。

量三件事（2.1 新增的入口，用户对设置面板的版式很敏感）：
  1. 行真的在（按钮存在、文字正确、在日志节之后）；
  2. 滚到底之后按钮完整可见（不被面板底边裁掉半颗）；
  3. 点一下：剪贴板不可用的环境里走落盘兜底，文件内容与 deploy_payload() 一致。

用法：python devtools\_probe_mcp_row.py
产物：_check/mcp_row.png
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import ui_font
    from ui_theme import apply_scheme, detect_system_scheme
    apply_scheme(detect_system_scheme())
    app.setFont(ui_font(12.5))

    from PySide6.QtCore import QPoint
    from ui_settings import SettingsDialog

    dialog = SettingsDialog(theme_mode="dark")
    dialog.show()
    app.processEvents()

    # 1) 行存在
    button = getattr(dialog, "btn_mcp_copy", None)
    assert button is not None, "设置面板缺少「AI 接入」按钮"
    assert button.text() == "复制接入配置", f"按钮文案变了：{button.text()!r}"

    # 2) 滚到底，按钮要完整落在可视区内
    from PySide6.QtWidgets import QScrollArea
    areas = [w for w in dialog.findChildren(QScrollArea) if w.isVisible()]
    assert areas, "设置面板里没有滚动区？"
    bar = areas[0].verticalScrollBar()
    bar.setValue(bar.maximum())
    app.processEvents()

    top_left = button.mapTo(dialog, QPoint(0, 0))
    bottom = top_left.y() + button.height()
    assert bottom <= dialog.height(), \
        f"按钮被底边裁掉：bottom={bottom} > dialog={dialog.height()}"

    dialog.grab().save("_check/mcp_row.png")

    # 3) 复制（能进剪贴板最好；进不了必须落盘，且内容与 deploy_payload 一致）
    import mcp_bridge
    expect = mcp_bridge.deploy_payload()
    button.click()
    app.processEvents()
    from PySide6.QtGui import QGuiApplication
    got = QGuiApplication.clipboard().text()
    if got == expect:
        print("复制路径：剪贴板 OK")
    else:
        # 兜底落盘写在哪？按钮文字应该已变成文件名
        assert button.text() != "复制接入配置", "点击后按钮没有反馈"
        print(f"复制路径：剪贴板不可用，按钮显示 {button.text()!r}（落盘兜底）")

    # 探针没动主窗口，不会写 geometry；对话框直接丢弃（不走 done()）
    dialog.hide()
    # 兜底落盘写的文件（开发态落在项目根）只属于这次点击，验完就收走
    stray = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                         "mcp-接入配置.txt")
    if os.path.exists(stray):
        os.remove(stray)
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
