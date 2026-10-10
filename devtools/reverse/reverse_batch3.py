"""反向验证补丁（本轮）：故意破坏新实现，看断言是否如实挂掉。

用法（在副本目录里跑）：
    python reverse_batch3.py temp      # 温度越界不标黄
    python reverse_batch3.py altersel  # 搜索高亮不走合并出口（会顶掉异常高亮）
    python reverse_batch3.py pad       # 序号补齐退回"补空格"
"""

import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "temp"


def patch(path, old, new):
    src = open(path, encoding="utf-8").read().replace("\r\n", "\n")
    assert src.count(old) == 1, f"{path}: 匹配 {src.count(old)} 处，应当只有 1 处"
    open(path, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))


if MODE == "temp":
    # 温度段不再判越界（用户要的"黄色标注"整个消失）
    patch("ui_highlight.py",
          '            elif self._section == SECTION_TEMP:\n'
          '                value = int(match.group(2)[1:])\n'
          '                if value > temp_high or value < temp_low:\n'
          '                    self.setFormat(start, length, self._temp_fmt)\n',
          '            elif False:        # REVERSE: 温度不判越界\n'
          '                pass\n')

elif MODE == "altersel":
    # 搜索高亮直接塞给控件，不走合并出口 —— 异常高亮会被顶掉
    patch("main_window.py",
          '            selections.append(selection)\n'
          '        self._set_search_selections(selections)\n',
          '            selections.append(selection)\n'
          '        self.txt_rx.setExtraSelections(selections)   # REVERSE\n')

elif MODE == "pad":
    # 序号补齐退回"补空格"（用户要的是补零）
    patch("ui_highlight.py",
          '        return "0" * max(0, width - len(index)) + index + match.group(2)\n',
          '        return " " * max(0, width - len(index)) + index + match.group(2)\n')

elif MODE == "boundary":
    # 段落边界退回"只认 Voltage1 标题"（时间戳不再把段重置成电压）
    patch("ui_highlight.py",
          '    if stamp_span(text):\n        return SECTION_VOLT\n',
          '    pass        # REVERSE: 时间戳不重置段\n')

elif MODE == "bms_bound":
    # BMS 不再关闭段（温度数据会一直往后判）
    patch("ui_highlight.py",
          '    if BMS_LINE_RE.match(text):\n        return SECTION_OTHER\n',
          '    pass        # REVERSE: BMS 不关闭段\n')

elif MODE == "triangle":
    # 把三角形加回来（用户已经要求去掉它）
    patch("ui_controls.py",
          '        pen = QPen(red, 2.4)\n',
          '        from PySide6.QtCore import QPointF as _P\n'
          '        from PySide6.QtGui import QPolygonF\n'
          '        painter.setPen(QPen(color, 1.7))        # REVERSE: 三角形回来了\n'
          '        painter.setBrush(Qt.BrushStyle.NoBrush)\n'
          '        painter.drawPolygon(QPolygonF([_P(cx, cy - 9.0),\n'
          '                                       _P(cx + 7.8, cy + 4.5),\n'
          '                                       _P(cx - 7.8, cy + 4.5)]))\n'
          '        pen = QPen(red, 2.4)\n')

else:
    raise SystemExit(f"未知模式 {MODE}")

print(f"reverse '{MODE}' applied")
