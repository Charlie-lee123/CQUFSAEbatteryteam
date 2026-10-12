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
    hitbtn    开关命中区失效           -> check_customization 挂
    hit_large 开关命中区过大（整矩形） -> check_customization 挂
    pressflip 开关退回"松开才翻转"     -> check_customization 挂
    sentoggle 发送区不按配置初始化     -> check_send_collapsed_startup 挂
    titlebar  标题栏主题沿父链上溯     -> check_caption_bar 挂
    stamp_round 时间戳不判轮首（每帧打）-> check_expand_and_copy_all 挂
    higeat    越界只染值、不染序号     -> check_send_enter_and_stamp 挂
    higoff    「高亮异常数据」开关失效 -> check_send_enter_and_stamp 挂
    nofont    字号滑杆不实时回调       -> check_customization 挂
    nocursor  边缘不给缩放光标         -> check_caption_bar 挂
    cursoreat 边缘事件不吃掉（被父撤）-> check_caption_bar 挂
    fontcolor 预览退回整条单色       -> check_customization 挂
    noreset   复原按钮不回默认值     -> check_customization 挂
    spinpad   输入框不做垂直补偿     -> check_customization 挂
    combofit  非编辑下拉不做实测矫正 -> check_customization 挂
    popwidth  弹层宽度不自适应       -> check_config_parity_clock 挂
    hitblank  开关按下不过命中区     -> check_customization 挂

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

elif MODE == "stamp_round":
    # 时间戳退回"帧里含 Voltage1 就打"（不判轮首）—— 电压分包成多帧、每帧都带
    # 标题时一轮打出多个时间戳，复制本轮会从轮内第二个起算、前半段丢失
    patch("main_window.py",
          "            if self._align_section != SECTION_VOLT and VOLT_HEADER_RE.match(line):\n"
          "                new_round = True\n",
          "            if self._contains_anchor(line):   # REVERSE: 帧含标题就算新轮\n"
          "                new_round = True\n")

elif MODE == "higeat":
    # 越界条目退回"只染值、不染序号" —— 用户点名"高亮要包括序号"的回归
    patch("ui_highlight.py",
          "            start = match.start(1)\n"
          "            length = match.end(2) - match.start(1)\n",
          "            start = match.start(2) + 1      # REVERSE: 只染值，不染序号\n"
          "            length = match.end(2) - match.start(2) - 1\n")

elif MODE == "higoff":
    # 「高亮异常数据」开关失效（永远当开着）—— 关掉后异常色还在
    patch("ui_highlight.py",
          '        self._errors_on = bool(customization.get("highlight_errors"))\n',
          '        self._errors_on = True   # REVERSE: 开关失效\n')

elif MODE == "nofont":
    # 字号滑杆不实时回调主窗口 —— "拖动即时预览"失效
    patch("ui_settings.py",
          '        if self._on_custom is not None:\n'
          '            self._on_custom(spec["key"], value)\n',
          '        pass   # REVERSE: 不实时回调，预览只在确定后生效\n')

elif MODE == "nocursor":
    # 边缘不给缩放光标 —— 用户点名的"边框拖拽提示"整个没了
    patch("main_window.py",
          "        for want, shape in cls._EDGE_CURSORS:\n"
          "            if edges == want:\n"
          "                return shape\n"
          "        return None\n",
          "        return None   # REVERSE: 永远不给缩放光标\n")

elif MODE == "cursoreat":
    # 命中边缘时不吃掉 mouseMove —— 事件沿父链上传，第二次把光标撤掉
    patch("main_window.py",
          "                return True\n"
          "            self._release_resize_cursor()\n",
          "                return False   # REVERSE: 不拦住事件\n"
          "            self._release_resize_cursor()\n")

elif MODE == "fontcolor":
    # 预览退回"整条单色" —— 逐字符彩虹配色没了（用户点名的红橙黄绿蓝紫+反色高亮）
    patch("ui_settings.py",
          '        high = qcolor("volt_high").name()      # 红\n',
          '        return f\'<span style="color:{qcolor("ink").name()}">'
          '{spec["preview"]}</span>\'   # REVERSE: 退回单色\n'
          '        high = qcolor("volt_high").name()      # 红\n')

elif MODE == "noreset":
    # 「复原」按钮不回默认值（差 2 个半磅）—— 用户点名的复原失效
    patch("ui_settings.py",
          '                    lambda _=False, s=spec, sl=slider: '
          'sl.setValue(int(s["default"])))\n',
          '                    lambda _=False, s=spec, sl=slider: '
          'sl.setValue(int(s["default"]) + 2))   # REVERSE: 复原不回默认\n')

elif MODE == "spinpad":
    # 输入框不做垂直处理 —— 「保留行数」等文字与上下边框间距不等。
    # 现在的实现是"实测墨迹摆编辑框"，所以要双管齐下才骗得过去：
    #   ① 两个调用点都改回 None（跳过实测）→ 走兜底分支；
    #   ② 兜底里的字号补偿归零 → 朴素居中（y=7，明显偏上）。
    patch("ui_controls.py",
          "            offset = _edit_ink_offset(self._edit)\n",
          "            offset = None   # REVERSE: 不实测\n")
    patch("ui_controls.py",
          "        offset = _edit_ink_offset(edit)\n",
          "        offset = None   # REVERSE: 不实测\n")
    patch("ui_controls.py",
          "        pt = self.font().pointSizeF()\n"
          "        return max(0, int(round((pt - self.VERT_PIVOT_PT) * self.VERT_GAIN)))\n",
          "        return 0   # REVERSE: 不做垂直补偿\n")

elif MODE == "combofit":
    # 不可编辑下拉框不做实测矫正 —— 文字回到"行盒居中"的默认偏差
    # （"1" 偏下 1px、"none" 偏下 2.75px，正好越过 1.5px 的断言线）
    patch("ui_controls.py",
          "        shift = int(round(-base_delta))        # 目标位移（正 = 下移）\n",
          "        shift = 0   # REVERSE: 不做实测矫正\n")

elif MODE == "popwidth":
    # 弹层宽度不按内容自适应 —— 回到"跟控件同宽"，
    # 编码框的 latin-1 会被截成 l...1（用户点名像波特率那样展开）
    patch("ui_controls.py",
          "        need = widest + 60\n"
          "        view.setMinimumWidth(max(self.width(), need))\n",
          "        view.setMinimumWidth(self.width())   # REVERSE: 不自适应\n")

elif MODE == "hitblank":
    # mousePressEvent 退回"不检查命中区"——控件内任意位置按下都翻转
    # （v1.4 的 hitButton 修复就是栽在这上面：覆写绕开了它）
    patch("ui_controls.py",
          "        if (event.button() == Qt.MouseButton.LeftButton and self.isEnabled()\n"
          "                and self.hitButton(event.position().toPoint())):\n",
          "        if event.button() == Qt.MouseButton.LeftButton "
          "and self.isEnabled():   # REVERSE: 不过命中区\n")

else:
    raise SystemExit(f"未知模式 {MODE}")

print(f"reverse '{MODE}' applied")
