"""反向验证补丁：故意破坏实现，看自测是否如实挂掉。

用法（在副本目录里跑）：python reverse_batch3.py <模式>
    temp      温度越界不标黄           -> check_send_enter_and_stamp 挂
    altersel  搜索高亮不走合并出口     -> check_alert_locate 挂
    pad       序号退回补空格           -> check_rx_merge_and_pause 挂
    boundary  时间戳不重置段           -> check_send_enter_and_stamp 挂
    bms_bound BMS 不关闭段             -> check_send_enter_and_stamp 挂
    triangle  三角形加回定位按钮       -> check_alert_locate 挂
    prio      定位优先级倒置（温度>电压）-> check_alert_locate 挂
    valuepad  值补零禁用               -> check_send_enter_and_stamp 挂
    orange    BMS ERROR 退回黄色       -> check_send_enter_and_stamp 挂
    nopad     设置面板滚动条空隙归零   -> check_customization 挂

⚠️ 补丁一律写成**文件**再执行 —— `python -c "...\n..."` 里的反斜杠会被
Windows 的参数转换吃掉（踩过一次）。
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
          "            elif self._section == SECTION_TEMP:\n"
          "                value = int(match.group(2)[1:])\n"
          "                if value > temp_high or value < temp_low:\n"
          "                    self.setFormat(start, length, self._temp_fmt)\n",
          "            elif False:        # REVERSE: 温度不判越界\n"
          "                pass\n")

elif MODE == "altersel":
    # 搜索高亮直接塞给控件，不走合并出口 —— 异常高亮会被顶掉
    patch("main_window.py",
          "            selections.append(selection)\n"
          "        self._set_search_selections(selections)\n",
          "            selections.append(selection)\n"
          "        self.txt_rx.setExtraSelections(selections)   # REVERSE\n")

elif MODE == "pad":
    # 序号补齐退回"不认 0 号点"（用户："序号为 0 时前面不应再保留占位"）
    patch("ui_highlight.py",
          '            pad_w = 0 if index == "0" else max(0, index_width - len(index))\n',
          '            pad_w = max(0, index_width - len(index))   # REVERSE: 0 号点规则被删\n')

elif MODE == "boundary":
    # 段落边界退回"只认 Voltage1 标题"（时间戳不再把段重置成电压）
    patch("ui_highlight.py",
          "    if stamp_span(text):\n        return SECTION_VOLT\n",
          "    pass        # REVERSE: 时间戳不重置段\n")

elif MODE == "bms_bound":
    # BMS 不再关闭段（温度数据会一直往后判）
    patch("ui_highlight.py",
          "    if BMS_LINE_RE.match(text):\n        return SECTION_OTHER\n",
          "    pass        # REVERSE: BMS 不关闭段\n")

elif MODE == "triangle":
    # 把三角形加回定位按钮（用户已经要求去掉它）
    patch("ui_controls.py",
          "        pen = QPen(red, 2.4)\n",
          "        from PySide6.QtCore import QPointF as _P\n"
          "        from PySide6.QtGui import QPolygonF\n"
          "        painter.setPen(QPen(color, 1.7))        # REVERSE: 三角形回来了\n"
          "        painter.setBrush(Qt.BrushStyle.NoBrush)\n"
          "        painter.drawPolygon(QPolygonF([_P(cx, cy - 9.0),\n"
          "                                       _P(cx + 7.8, cy + 4.5),\n"
          "                                       _P(cx - 7.8, cy + 4.5)]))\n"
          "        pen = QPen(red, 2.4)\n")

elif MODE == "prio":
    # 定位优先级倒置（温度抢在电压前面）
    patch("main_window.py",
          "        return last_volt or last_temp or last_bms\n",
          "        return last_temp or last_volt or last_bms   # REVERSE\n")

elif MODE == "valuepad":
    # 值补零禁用（用户要求的"数值前方补零"消失）
    patch("ui_highlight.py",
          "            value = \"0\" * max(0, widths.get(current, 0) - len(value)) + value\n",
          "            value = value          # REVERSE: 值不补零\n")

elif MODE == "orange":
    # BMS ERROR 退回黄色（用户要求改橙）
    patch("ui_theme.py",
          '    "bms_error": "#FB923C",',
          '    "bms_error": "#FACC15",')

elif MODE == "nopad":
    # 滚动条空隙消失（body 右内边距归零）
    patch("ui_settings.py",
          "        body_layout.setContentsMargins(0, 0, 12, 0)\n",
          "        body_layout.setContentsMargins(0, 0, 0, 0)\n")

elif MODE == "hitbtn":
    # Switch 命中区域失效（只有原生 14px 勾选框可点）—— "部分拨动开关无法点击"回归
    patch("ui_controls.py",
          "        point = QPointF(pos)\n"
          "        track = self._track_rect()\n",
          "        return False        # REVERSE: 命中区域失效\n"
          "        point = QPointF(pos)\n"
          "        track = self._track_rect()\n")

elif MODE == "hit_large":
    # 命中区退回「整个控件矩形」（横向占满行、纵向占满控件高度含留白）——
    # 用户报的"停在开关同一高度随便点一下就切换"回归
    patch("ui_controls.py",
          "        point = QPointF(pos)\n"
          "        track = self._track_rect()\n",
          "        return self.rect().contains(pos)   # REVERSE: 命中区过大\n"
          "        point = QPointF(pos)\n"
          "        track = self._track_rect()\n")

elif MODE == "knobgeom":
    # GlassSlider 几何退回错位版（滑块中心不再贴轨道端点）
    patch("ui_controls.py",
          "        return self.KNOB_D / 2.0 + self._span() * frac\n",
          "        return self.KNOB_D / 2.0 + self._span() * frac + 17.5   # REVERSE: 错位回归\n")

elif MODE == "pressflip":
    # 开关退回「松开才翻转」（按下不响应）—— "按下有延迟"回归
    patch("ui_controls.py",
          "        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():\n"
          "            self.setChecked(not self.isChecked())\n"
          "            event.accept()\n"
          "            return\n",
          "        if False:        # REVERSE: 按下不翻转，退回松开才切换\n"
          "            pass\n")

elif MODE == "sentoggle":
    # 「默认折叠发送区」不按配置初始化（硬编码回折叠）—— 配置不生效回归
    patch("main_window.py",
          "        self._set_send_collapsed(\n"
          "            bool(customization.get(\"send_collapsed_startup\")), remember=False)\n",
          "        self._set_send_collapsed(True, remember=False)   # REVERSE\n")

elif MODE == "titlebar":
    # 标题栏主题作用域退回"沿父链上溯" —— 设置对话框会把主窗口圆角重新打开
    patch("ui_theme.py",
          "        hwnd = int(window.winId())\n",
          "        hwnd = ctypes.windll.user32.GetParent(int(window.winId())) "
          "or int(window.winId())   # REVERSE\n")

else:
    raise SystemExit(f"未知模式 {MODE}")

print(f"reverse '{MODE}' applied")
