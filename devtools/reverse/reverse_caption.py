"""反向验证补丁（自绘标题栏）：故意破坏实现，看新用例是否如实挂掉。

用法：python reverse_caption.py {hide_expanded|no_glyph_refresh|no_frameless|no_resize_gate}
⚠️ 必须用文件执行而不是 `python -c`：命令行里的反斜杠会被 Windows 参数转换吃掉。
"""
import sys

which = sys.argv[1]


def load(path):
    return open(path, encoding="utf-8").read().replace("\r\n", "\n")


def save(path, text):
    open(path, "w", encoding="utf-8", newline="\n").write(text)


if which == "hide_expanded":
    # 展开接收区时把标题栏一起藏掉（用户点名不许发生的事）
    p = "main_window.py"
    s = load(p)
    old = "        self.brand_header.hide()\n        self._config_card.hide()\n"
    new = "        self._caption_bar.hide()\n        self.brand_header.hide()\n        self._config_card.hide()\n"
    assert old in s
    save(p, s.replace(old, new, 1))
    print("rev applied: 展开态藏掉标题栏")
elif which == "no_glyph_refresh":
    # 不刷新中间那颗键的面孔（全屏后仍显示"最大化"图标）
    p = "main_window.py"
    s = load(p)
    old = "            # 全屏 ⇄ 普通的每一次切换都会走到这儿，标题栏那颗键的面孔在这里统一刷\n            self._refresh_caption_ui()\n"
    assert old in s
    save(p, s.replace(old, "", 1))
    print("rev applied: 不刷新标题栏键面孔")
elif which == "no_frameless":
    # 窗口不再无边框（回到系统标题栏 + 自绘标题栏两套并存）
    p = "main_window.py"
    s = load(p)
    old = "        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)\n"
    assert old in s
    save(p, s.replace(old, "", 1))
    print("rev applied: 去掉无边框")
elif which == "no_resize_gate":
    # 边缘缩放不再区分窗口状态（全屏时也开口子）
    p = "main_window.py"
    s = load(p)
    old = ("        if not (self.isVisible() and not self.isFullScreen()\n"
           "                and not self.isMinimized() and not self.isMaximized()):\n"
           "            return Qt.Edge(0)\n")
    assert old in s
    save(p, s.replace(old, "", 1))
    print("rev applied: 边缘缩放不看窗口状态")
else:
    raise SystemExit("unknown")
