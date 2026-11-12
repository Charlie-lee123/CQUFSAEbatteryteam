# -*- coding: utf-8 -*-
r"""协议规则：把"这段 BMS 文本怎么读"从硬编码里抽出来，做成可编辑的规则字典。

## 为什么要它

接一台新 BMS，往往只是标题写法/值的位数变了（``Voltage590:`` → ``Pack3:``、
4 位 mV → 3 位）。改 Python 重打包成本太高 —— 这里把规则变成一份 JSON，
设置面板里改、校验、试跑、保存即生效（不用重启）。

## 规则字典（键名固定，7 项）

```json
{
  "schema": 1,
  "module_header": "\\s*Voltage\\d*\\s*:",          // 电压段标题（段边界/模组起点）
  "temp_header":   "\\s*Temperature\\d*\\s*:",      // 温度段标题
  "cell_value":    "^(?P<idx>\\d+)(?P<val>\\.\\d{1,4})(?!\\d)",   // 电芯采样 token
  "cell_width":    4,                               // 电压取几位（mV）
  "temp_value":    "^(?P<idx>\\d+)(?P<val>\\.\\d+)$",           // 温度采样 token
  "temp_width":    2,                               // 温度取几位（℃）
  "error_pattern": "BMS\\s+ERROR"                   // 异常字样（着色用）
}
```

⛔ **默认值与改动前的硬编码逐字节等价**（35 条老自测就是护栏）：
``module_header`` / ``temp_header`` 与 ``ui_highlight`` 里原来那两条一字不差
（含 ``\s*`` 前缀与 ``\d*`` 后缀）；``cell_width``/``temp_width`` = 原来的
4 位 mV / 2 位℃ 判据。给用户的示例若与这里不一致，**以这里为准**。

## 与 rx_export 的关系

``rx_export``（CSV/JSON 导出）与 ``rx_store``（SQLite）目前仍走自己的固定口径；
本模块先管**显示层**（``ui_highlight`` 的段判定/着色）与设置面板的"试跑"。
两边口径要合一的话是下一步的事，别在这里偷偷改导出行为。
"""

from __future__ import annotations

import json
import re

from PySide6.QtCore import QSettings

import app_log

# 规则存在**独立的键**里（不进 customization 的注册表：它不是给用户随手拨的
# 开关，而是一整份结构化的规则文档；坏了要能一键恢复默认）。
PROFILE_KEY = "protocol/profile"
SCHEMA = 1

REQUIRED = ("module_header", "temp_header", "cell_value", "cell_width",
            "temp_value", "temp_width", "error_pattern")

# 默认规则：与 ui_highlight 里原先的硬编码**逐字节等价**（见模块注释）。
# ⚠️ 2026-11-12 唯一的一处偏离：``cell_value`` 的小数位数从 ``\d{4}`` 放宽成
#    ``\d{1,4}``。真机固件是 ``printf("%d.%d", 序号, mV)``（一个零都不补），一颗
#    425 mV 的坏电芯发出来是 ``2.425`` —— 卡死 4 位会把这一条**静默丢掉**，试跑
#    出来的电芯数比实际少一个，用户会以为"协议没问题 / 电芯少了"。
#    放宽后``_values`` 的**左**补零负责把它读成 425（不是 4250），见那个函数的注释。
DEFAULT = {
    "schema": SCHEMA,
    "module_header": r"\s*Voltage\d*\s*:",
    "temp_header": r"\s*Temperature\d*\s*:",
    "cell_value": r"^(?P<idx>\d+)(?P<val>\.\d{1,4})(?!\d)",
    "cell_width": 4,
    "temp_value": r"^(?P<idx>\d+)(?P<val>\.\d+)$",
    "temp_width": 2,
    "error_pattern": r"BMS\s+ERROR",
}


class Compiled:
    """编译好的规则（ui_highlight 的活代理和 sample_parse 共用）。"""

    __slots__ = ("volt_header", "temp_header", "cell_value", "temp_value",
                 "error", "cell_width", "temp_width")

    def __init__(self, profile: dict) -> None:
        self.volt_header = re.compile(profile["module_header"])
        self.temp_header = re.compile(profile["temp_header"])
        self.cell_value = re.compile(profile["cell_value"])
        self.temp_value = re.compile(profile["temp_value"])
        # ⚠️ 大小写不敏感：真机发 "BMS Error"（只有首字母大写），用户 2026-10-24
        #    点名过"出现 BMS Error 得标黄" —— 这条从旧实现（BMS_ERROR_RE）继承。
        self.error = re.compile(profile["error_pattern"], re.IGNORECASE)
        self.cell_width = int(profile["cell_width"])
        self.temp_width = int(profile["temp_width"])


def validate(profile) -> tuple[bool, str]:
    """校验一份规则：必填项齐全、5 条正则都能编译、宽度是 1~9、有命名组。"""
    if not isinstance(profile, dict):
        return False, "规则必须是一个 JSON 对象"
    for key in REQUIRED:
        if key not in profile:
            return False, f"缺少必填项 {key}"
    for key in ("module_header", "temp_header", "cell_value",
                "temp_value", "error_pattern"):
        value = profile.get(key)
        if not isinstance(value, str) or not value.strip():
            return False, f"{key} 必须是非空字符串"
        try:
            re.compile(value)
        except re.error as exc:
            return False, f"{key} 正则编译失败：{exc}"
    for key in ("cell_width", "temp_width"):
        try:
            width = int(profile.get(key))
        except (TypeError, ValueError):
            return False, f"{key} 必须是整数"
        if not 1 <= width <= 9:
            return False, f"{key} 必须在 1~9 之间（实得 {width}）"
    for key in ("cell_value", "temp_value"):
        groups = re.compile(profile[key]).groupindex
        missing = [name for name in ("idx", "val") if name not in groups]
        if missing:
            return False, (f"{key} 缺少命名捕获组 {'/'.join(missing)}"
                           "（需要 (?P<idx>…) 与 (?P<val>…)）")
    return True, "规则有效（7 项全部通过）"


def compile_profile(profile: dict | None = None) -> Compiled:
    """编译规则；无效直接抛 ``ValueError``（调用方负责兜底成 DEFAULT）。"""
    profile = dict(DEFAULT) if profile is None else profile
    ok, message = validate(profile)
    if not ok:
        raise ValueError(message)
    return Compiled(profile)


def active() -> dict:
    """当前生效规则：有且有效的自定义 → 用它；没有/坏了 → DEFAULT（并记日志）。"""
    settings = QSettings("MimoWorks", "SerialAssistant")
    raw = settings.value(PROFILE_KEY, "")
    if not raw:
        return dict(DEFAULT)
    try:
        profile = json.loads(str(raw))
    except (TypeError, ValueError):
        app_log.warning("协议规则不是合法 JSON，已退回默认")
        return dict(DEFAULT)
    ok, message = validate(profile)
    if not ok:
        app_log.warning(f"协议规则无效（{message}），已退回默认")
        return dict(DEFAULT)
    return dict(profile)


def save_profile(profile: dict | None) -> bool:
    """落盘（QSettings）。``None`` = 恢复默认（删掉自定义键）。返回是否成功。"""
    settings = QSettings("MimoWorks", "SerialAssistant")
    if profile is None:
        settings.remove(PROFILE_KEY)
        settings.sync()
        app_log.info("协议规则已恢复默认")
        return True
    ok, message = validate(profile)
    if not ok:
        app_log.warning(f"协议规则保存失败：{message}")
        return False
    settings.setValue(PROFILE_KEY, json.dumps(profile, ensure_ascii=False))
    settings.sync()
    app_log.info("协议规则已保存")
    return True


def sample_parse(text: str, profile: dict | None = None) -> dict:
    """拿规则**试跑**一段文本（设置面板「试跑」用的就是它）。

    返回::

        {"modules": n,          # 解析出几个模组（= 标题行个数）
         "cells": [[...]],      # 每个电压模组的电芯值（整数，按 cell_width 截取）
         "temps": [[...]],      # 每个温度模组的温度值（按 temp_width 截取）
         "errors": n,           # error_pattern 命中的处数
         "note": "..."}         # 给人看的一句话摘要

    语义（与设备实际布局一致）：一条电压标题 = 一个模组；标题同一行可以带数据；
    后续**不带标题**的数据行是当前模组的续传（分包劈开时会这样）。
    """
    prof = active() if profile is None else profile
    ok, message = validate(prof)
    if not ok:
        return {"modules": 0, "cells": [], "temps": [], "errors": 0,
                "note": f"规则无效：{message}"}
    compiled = compile_profile(prof)
    modules: list[dict] = []
    errors = 0
    kind = ""                       # "" / "volt" / "temp"
    current: dict | None = None
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        errors += sum(1 for _ in compiled.error.finditer(line))
        head = compiled.temp_header.match(line)
        if head:
            kind = "temp"
            current = {"cells": [], "temps": _values(
                line[head.end():], compiled.temp_value, compiled.temp_width)}
            modules.append(current)
            continue
        head = compiled.volt_header.match(line)
        if head:
            kind = "volt"
            current = {"cells": _values(
                line[head.end():], compiled.cell_value, compiled.cell_width),
                "temps": []}
            modules.append(current)
            continue
        if current is None:
            continue                # 第一条标题之前：没有段可归，忽略
        if kind == "volt":
            current["cells"].extend(
                _values(line, compiled.cell_value, compiled.cell_width))
        elif kind == "temp":
            current["temps"].extend(
                _values(line, compiled.temp_value, compiled.temp_width))
    cells = [m["cells"] for m in modules if m["cells"]]
    temps = [m["temps"] for m in modules if m["temps"]]
    return {
        "modules": len(modules),
        "cells": cells,
        "temps": temps,
        "errors": errors,
        "note": (f"{len(modules)} 个模组（{len(cells)} 段电压 / {len(temps)} 段温度），"
                 f"{sum(len(c) for c in cells)} 个电芯，"
                 f"{sum(len(t) for t in temps)} 个温度，{errors} 处错误字样"),
    }


def _values(text: str, pattern: re.Pattern, width: int) -> list[int]:
    """从一段文字里按 token 取采样值：``val`` 去掉点号后取前 ``width`` 位。

    位数不足往**左**补零（``.425`` -> 0425 = 425 mV），与 ``rx_export._value_of``、
    ``perf_mode._digits``、``ui_highlight`` 的补零方向保持一致 —— 往右补会把
    425 mV 的坏电芯读成 4250 mV 的健康电芯（2026-11-12 修，见 _value_of 的注释）。
    """
    out: list[int] = []
    for token in text.split():
        match = pattern.fullmatch(token)
        if not match:
            continue
        body = match.group("val")
        if body.startswith("."):
            body = body[1:]
        if not body:
            continue
        try:
            out.append(int(body[:width].zfill(width)))
        except ValueError:
            continue
    return out
