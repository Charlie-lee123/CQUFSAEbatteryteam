"""反向验证补丁：故意破坏本轮实现，看自测是否如实挂掉。

用法：python rev3_patch.py {controls|wheel|settings}
⚠️ 必须用文件而不是 `python -c`：命令行里的反斜杠会被 Windows 参数转换
   （`\n` 变成 `/n`），带换行的正则根本传不进去（踩过）。
文件是 CRLF，先统一成 LF 再打补丁。
"""
import re
import sys

which = sys.argv[1]

WHEEL_RE = re.compile(
    r"\n    def wheelEvent\(self, event\) -> None:\n"
    r"(?:        .*\n|\n)*?        event\.ignore\(\)\n")


def load(path):
    return open(path, encoding="utf-8").read().replace("\r\n", "\n")


def save(path, text):
    open(path, "w", encoding="utf-8", newline="\n").write(text)


if which == "controls":
    # 数字框的竖直墨迹补偿退回 top=4（用户报的"间距不一致"）
    p = "ui_controls.py"
    s = load(p)
    s2 = s.replace("TEXT_MARGIN = (11, 0, 6, 0)", "TEXT_MARGIN = (11, 4, 6, 0)", 1)
    assert s2 != s, "TEXT_MARGIN 没改到"
    save(p, s2)
    print("rev-controls applied")
elif which == "wheel":
    # 两个 wheelEvent 覆盖删掉（滚轮又能改数字/换选项）
    p = "ui_controls.py"
    s = load(p)
    s2 = WHEEL_RE.sub("\n", s)
    assert s2.count("def wheelEvent") == 0, f"还剩 {s2.count('def wheelEvent')} 个"
    save(p, s2)
    print("rev-wheel applied")
elif which == "align":
    # 字段包装不归零内边距（样式默认 9px 把字段顶偏）
    p = "ui_settings.py"
    s = load(p)
    s2 = s.replace(
        "        holder.setLayout(layout)\n        layout.setContentsMargins(0, 0, 0, 0)\n",
        "        holder.setLayout(layout)\n", 1)
    assert s2 != s, "_row 没改到"
    save(p, s2)
    print("rev-align applied")
elif which == "settings":
    # 字段包装不归零内边距（样式默认 9px 把字段顶偏）+ 时钟年月日默认改回 True
    p = "ui_settings.py"
    s = load(p)
    s2 = s.replace(
        "        holder.setLayout(layout)\n        layout.setContentsMargins(0, 0, 0, 0)\n",
        "        holder.setLayout(layout)\n", 1)
    assert s2 != s, "_row 没改到"
    save(p, s2)
    p2 = "customization.py"
    s3 = load(p2)
    s4 = s3.replace('register_bool("clock_show_date", "时钟显示年月日", False,',
                    'register_bool("clock_show_date", "时钟显示年月日", True,', 1)
    assert s4 != s3, "clock_show_date 默认值没改到"
    save(p2, s4)
    print("rev-settings applied")
else:
    raise SystemExit("unknown")
