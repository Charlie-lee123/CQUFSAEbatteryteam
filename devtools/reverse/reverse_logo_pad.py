"""反向验证补丁：标题栏图标 / 序号补齐。用法：python reverse_logo_pad.py {icon|pad}"""
import sys

which = sys.argv[1]


def load(path):
    return open(path, encoding="utf-8").read().replace("\r\n", "\n")


def save(path, text):
    open(path, "w", encoding="utf-8", newline="\n").write(text)


if which == "icon":
    # 标题栏不画窗口图标
    p = "ui_brand.py"
    s = load(p)
    old = ("        icon = self._icon.pixmap(QSize(self.ICON_PX, self.ICON_PX),\n"
           "                                 self.devicePixelRatioF())\n"
           "        if not icon.isNull():\n")
    assert old in s
    save(p, s.replace(old, "        icon = None\n        if False:\n", 1))
    print("rev applied: 不画窗口图标")
elif which == "pad":
    # 序号不补齐
    p = "ui_highlight.py"
    s = load(p)
    old = "    if width <= 1:\n        return text\n"
    assert old in s
    save(p, s.replace(old, "    return text\n", 1))
    print("rev applied: 序号不补齐")
else:
    raise SystemExit("unknown")
