"""BMS 定制功能配置注册表 —— 定制化配置的统一管理入口。

背景：这是给重庆大学方程式赛车队电池组定制的 BMS 串口读取工具，后续会持续
加定制功能（协议特定解析、数据后处理、专用面板……）。如果每加一个功能就
改一遍设置面板，面板很快就会失控 —— 所以把"定制功能有哪些开关/参数"集中
**注册**在这里：

- 设置面板按 ``specs()`` 动态生成「定制功能」一节，新配置自动出现，面板代码零改动；
- 功能代码用 ``get(key)`` 取值（QSettings 持久化，按注册类型归一）；
- 修改用 ``set(key, value)``（设置面板确定时统一落盘）。

加一个定制配置的完整步骤：这里 ``register_*`` 一行 + 功能代码 ``get(key)``
—— 完事，设置界面自动多出对应控件。
"""

from __future__ import annotations

import json

from PySide6.QtCore import QSettings

_ORG, _APP = "MimoWorks", "SerialAssistant"
_SPECS: list[dict] = []
# key → spec 的**索引**：get/set/display_value 每次都要按 key 找 spec，
# 线性扫 _SPECS 是 O(n)。用一张表建索引后是 O(1)（接收路径每个时间戳都要
# 取 3 个开关，省下来的是每秒几十次的无谓扫描）。
_BY_KEY: dict[str, dict] = {}

# 改过默认值的配置：{key: 旧默认值}。迁移时只动"没被用户选过、且还停在旧默认值"
# 的那些（见 migrate_defaults）；新默认值直接取注册表里那一条。
_LEGACY_DEFAULTS: dict[str, object] = {}


def _add(spec: dict) -> None:
    """登记一条配置，同时建 key 索引（保持注册顺序，供面板生成）。"""
    _SPECS.append(spec)
    # setdefault：与旧实现 ``next(s for s in _SPECS if ...)`` 的"取第一条"语义
    # 完全一致 —— 万一哪天重复注册同一个 key，也不会偷偷切到后一条。
    _BY_KEY.setdefault(spec["key"], spec)


def register_bool(key: str, label: str, default: bool = False, tip: str = "") -> None:
    """注册一个布尔型定制配置（设置面板里是一枚拨动开关）。"""
    _add({"key": key, "kind": "bool", "label": label,
          "default": default, "tip": tip})


def register_int(key: str, label: str, default: int, minimum: int, maximum: int,
                 tip: str = "", suffix: str = "") -> None:
    """注册一个整数型定制配置（设置面板里是一个数字框）。"""
    _add({"key": key, "kind": "int", "label": label, "default": default,
          "minimum": minimum, "maximum": maximum, "tip": tip,
          "suffix": suffix})


def register_slider(key: str, label: str, default: int, minimum: int,
                    maximum: int, tip: str = "", suffix: str = "",
                    scale: float = 1.0, preview: str = "",
                    maximum_fn=None) -> None:
    """注册一个**滑动无级调节**的整数配置（设置面板里是一根滑杆）。

    取值类型与 ``register_int`` 完全一样（``kind`` 仍是 ``"int"``），只是面板
    上换成一枚 ``GlassSlider`` —— 所以 ``get``/``set``/``custom_values`` 都不用
    为它分情况。

    ``scale``：滑块是整数控件，要表达小数时用"整数存储 × scale = 显示值"。
    字号用它存**半磅**（21 → 10.5pt），既能保持原有观感又足够细腻；
    面板上的数值标签与预览都按 scale 换算。

    ``preview``：非空时在这一行**下方**再挂一行等宽字的预览（用当前字号渲染、
    用接收区正文的颜色），拖滑块当场能看到字号变化。
    """
    # ``maximum_fn``：右端要按**运行环境**现算时用（比如"动画帧率"的上限是
    # 当前显示器的刷新率）。面板每次打开都会重新调它，换显示器不用重启。
    _add({"key": key, "kind": "int", "widget": "slider", "label": label,
          "default": default, "minimum": minimum, "maximum": maximum,
          "tip": tip, "suffix": suffix, "scale": scale,
          "preview": preview, "maximum_fn": maximum_fn})


def register_choice(key: str, label: str, default: str, options, tip: str = "") -> None:
    """注册一个**枚举**型定制配置（设置面板里是一个下拉框，二选一起步）。

    ``options``：``((显示名, 存储值), ...)``。存储值必须是字符串 —— 它进 QSettings
    之后要能原样读回来（见 ``get`` 的归一：不在 options 里的值一律退回默认）。
    取值类型与其它类型一样走 ``get``/``set``，所以面板/主窗口都无需为它分情况。
    """
    _add({"key": key, "kind": "choice", "label": label,
          "default": default, "options": tuple(options), "tip": tip})


def specs() -> tuple[dict, ...]:
    """全部注册项（设置面板按它生成界面，顺序即注册顺序）。"""
    return tuple(_SPECS)


def get(key: str):
    """取一个定制配置的当前值。未注册的 key 直接抛 KeyError（写错了要炸出来）。"""
    spec = _BY_KEY[key]
    value = QSettings(_ORG, _APP).value(f"custom/{key}", spec["default"])
    if value is None:
        # 键被显式写成过 null（快照恢复 setValue(key, None) 会这样写）——
        # 按默认值算，别让 None 掉进类型归一里变成 False
        return spec["default"]
    if spec["kind"] == "bool":
        # QSettings 在 Windows 注册表里可能把 bool 存成 str —— 按注册类型归一
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("1", "true", "yes")
    if spec["kind"] == "choice":
        # 不在选项里的值（老配置 / 手改注册表 / 版本回退）一律退回默认 ——
        # 下拉框拿到一个不存在的值会一片空白，宁可回默认。
        valid = {stored for _label, stored in spec["options"]}
        return str(value) if str(value) in valid else spec["default"]
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(spec["default"])


def display_value(key: str) -> float:
    """取配置的**显示值**（滑杆型按 ``scale`` 换算，其它类型原样返回）。

    字号这类小数用整数存储（半磅：21 → 10.5pt），面板上的标签与预览、以及
    主窗口应用字号时都走这里，免得 scale 的知识散落在各处。
    """
    spec = _BY_KEY[key]
    return get(key) * float(spec.get("scale", 1.0))


def set(key: str, value) -> None:
    """写入一个定制配置（由设置面板确定时统一调用）。

    写完立刻 ``sync()``：QSettings 的写入按实例缓冲，不同步的话紧接着
    另一个实例去读会拿到旧值（自测里 set(7) 后 snap 读到的还是 3，实测踩过）。
    """
    _BY_KEY[key]                                    # 未注册的 key 照样炸出来
    settings = QSettings(_ORG, _APP)
    settings.setValue(f"custom/{key}", value)
    settings.sync()


def mark_chosen(key: str) -> None:
    """记下"用户在设置面板里明确改过这个键"。

    迁移默认值时的判据必须是**有没有选过**，不能只看当前值 —— 否则用户主动
    拨到旧默认值那一侧的选择会被当成"没动过"覆盖掉（毛玻璃强度那边踩过同一个
    坑，见 MEMORY.md 的 ``glass_chosen``）。
    """
    _BY_KEY[key]                                    # 未注册的 key 照样炸出来
    settings = QSettings(_ORG, _APP)
    settings.setValue(f"custom/{key}_chosen", True)
    settings.sync()


def migrate_defaults() -> None:
    """把"用户没选过、且还停在旧默认值"的配置迁到新默认值。主窗口启动时调一次。

    ``clock_show_date`` 的默认值从 True 改成了 False（用户要求"默认不显示
    年、月、日"）。老版本没存过这个键的人本来就取新默认；存过 True 的多半是被
    旧默认值落盘的，但**也可能是用户自己拨开的** —— 所以只迁没有 ``*_chosen``
    标记的那些，用户明确选过的一律尊重。
    """
    settings = QSettings(_ORG, _APP)
    changed = False
    for key, legacy in _LEGACY_DEFAULTS.items():
        if settings.value(f"custom/{key}_chosen", False, type=bool):
            continue                                # 用户明确选过
        spec = _BY_KEY[key]
        raw = settings.value(f"custom/{key}", None)
        if raw is None or spec["default"] == legacy:
            continue                                # 没存过 / 默认值没变
        current = (raw if isinstance(raw, bool)
                   else str(raw).lower() in ("1", "true", "yes"))
        if current == legacy:
            settings.setValue(f"custom/{key}", spec["default"])
            changed = True
    if changed:
        settings.sync()


# ---------------------------------------------------------------- 现有定制功能
# 界面材质：**二选一**（毛玻璃 / 液态玻璃）。两者**共用同一根强度滑杆**
# （设置面板顶部那根「材质强度」），强度 0~100 无级、拖动实时预览。
# 存储值是字符串（"frosted" / "liquid"），见 register_choice 的说明。
register_choice("material", "界面材质", "frosted",
                (("毛玻璃", "frosted"), ("液态玻璃", "liquid")),
                tip="毛玻璃：磨砂颗粒 + 玻璃底色（现有表现）；"
                    "液态玻璃：半透明主体 + 镜面双色调描边 + 边缘内发光 + 中心提亮"
                    "（苹果 Liquid Glass 的桌面近似）。两者共用下面的强度滑杆")

# 打开软件时的起手姿态。
# ⚠️ 本软件里的"最大化"是**整窗全屏**：铺满整台显示器并把任务栏一起盖掉
# （见 main_window.startup_fullscreen）。默认**开** = 保持老行为，用户升级
# 上来不会突然变成窗口起手；关掉之后软件先以普通窗口打开，想全屏随时点
# 标题栏中间那颗键（或把窗口拖到屏幕顶端）。
register_bool("startup_maximized", "启动时最大化", True,
              tip="打开软件时直接整窗全屏（铺满屏幕并盖住任务栏）；"
                  "关掉则先以普通窗口打开，之后可随时点标题栏中间那颗键全屏")

# 打开软件时发送区是否默认折叠。
# 默认**开** = 保持老行为（这台软件是"只看不发"的 BMS 读取工具，输入框常年
# 闲着，把高度让给接收区）。关掉则开局就展开发送区。用户随时可点发送区标题栏
# 那颗键手动折叠/展开，本项只决定**开局姿态**。
register_bool("send_collapsed_startup", "默认折叠发送区", True,
              tip="打开软件时把发送区收起到只剩标题栏，把高度让给接收区；"
                  "关掉则开局展开。随时可点发送区标题栏的按钮手动折叠/展开")

# 「暂停定位行数」：暂停时视口顶部与当前时间戳行之间保留的行数（此前固定 7 行）
register_int("pause_snap_lines", "暂停定位行数", 7, 0, 20,
             tip="暂停时数据停留位置顶部与当前时间戳行之间保留的行数")

# 时间戳里年 / 月 / 日的独立显示开关（用户点名要"分别提供独立的开关"）
register_bool("stamp_show_year", "时间戳显示年份", True)
register_bool("stamp_show_month", "时间戳显示月份", True)
register_bool("stamp_show_day", "时间戳显示日期", True)

# 接收区右上角实时时钟是否带年月日。
# 时刻（时:分:秒）**永远显示**，年月日是可选的那一段 —— 默认关（用户点名：
# "默认必须包含时、分、秒……该选项默认关闭"）。默认值从 True 改成 False 的
# 迁移见 _LEGACY_DEFAULTS。
register_bool("clock_show_date", "时钟显示年月日", False,
              tip="接收区右上角实时时钟是否包含年月日（时刻始终显示）")
_LEGACY_DEFAULTS["clock_show_date"] = True

# 电压正常值范围（mV）：采样点超出上限标红、低于下限标绿，范围内用默认字符色
register_int("volt_limit_low", "电压下限", 2500, 0, 10000, suffix=" mV",
             tip="低于该值（毫伏）的电压采样点显示为绿色")
register_int("volt_limit_high", "电压上限", 4500, 0, 10000, suffix=" mV",
             tip="高于该值（毫伏）的电压采样点显示为红色")

# 温度正常值范围（℃）：**超出上下限的一律标黄**（用户点名"以黄色标注" —— 温度
# 不分"偏高/偏低"两种颜色，越界就是越界）。温度段里的值是"序号.值"，
# 值取整数部分（"1.22" = 第 1 点 22 ℃），与电压那边的取值口径一致。
register_int("temp_limit_low", "温度下限", 0, -50, 200, suffix=" ℃",
             tip="低于该值（摄氏度）的温度采样点显示为黄色")
register_int("temp_limit_high", "温度上限", 60, -50, 200, suffix=" ℃",
             tip="高于该值（摄氏度）的温度采样点显示为黄色")

# 是否高亮异常数据（用户点名要一个开关）。
# 默认**开** = 保持现行为：电压越界标红/绿、温度越界标黄、BMS 错误标橙。
# 关掉后异常一律不上色，只留时间戳紫、序号蓝的常规着色 —— 看榜单/截图给
# 别人看时不想让满屏红绿时用。
register_bool("highlight_errors", "高亮异常数据", True,
              tip="电压/温度越界与 BMS 错误是否用红/绿/黄/橙色标出；"
                  "关掉后所有数据都用普通字符色显示（时间戳与序号照常着色）")

# 接收区字体大小（用户点名：滑动**无级**调节 + 实时预览）。
# 存的是**半磅**（21 = 10.5pt）—— 滑杆是整数控件，用 0.5pt 步进既能保住现在
# 10.5pt 的观感，又足够细腻。面板上的数值标签与预览文字都按 scale 换算。
register_slider("rx_font_size", "接收区字体大小", 21, 16, 48, scale=0.5,
                suffix=" pt", preview="CQUFSAE.2026",
                tip="接收区数据显示的字号；拖动滑块即时预览，确定后生效")


def _display_max_hz() -> int:
    """显示器最高刷新率（延迟 import：customization 别把 ui_anim/QtWidgets 拉进来）。

    每次面板打开都**先丢缓存再取** —— 换显示器/改刷新率之后滑杆右端要跟着变，
    不能吃上一次的缓存值。
    """
    from ui_anim import display_max_hz, forget_screen_hz
    forget_screen_hz()
    return display_max_hz()


# 动画帧率（用户 2026-10-17 点名）：无级调节，**上限 = 显示器最高刷新率**
# （他那台笔记本是 165Hz，默认 120 是为了省电；调满更顺滑）。
# ⛔ 改帧率**不改动画时长** —— 补间按真实时间算进度（见 ui_anim 的说明）。
register_slider("anim_fps", "动画帧率", 120, 24, 240, suffix=" Hz",
                maximum_fn=_display_max_hz,
                tip="动画每秒推进多少次。右端自动取当前显示器的最高刷新率；"
                    "调高更顺滑但更吃显卡，调低更省电 —— 动画的**时长不变**，"
                    "只是每秒画的张数不同")

# ---- 电池数据网格排版 ----
# 三者都用来决定"采样点网格该排成什么样"（展示层，解析逻辑不动）：
#   · 模组个数       → 网格**行数**（一排一个模组）
#   · 每模组电芯数   → 电压段每排的目标个数
#   · 每模组温度采集数 → 温度段每排的目标个数
# ⚠️ 如实说明：**默认 0 时"每排个数"跟随设备发来的每行点数**（这是 0 = 自动的含义），
#    但"**末排多一、其余各排一致**"的分配规则**始终生效**，不是缺陷、也不是只有
#    配了正数才生效 —— 用户点名要的就是这个（原话见 ui_highlight.grid_row_sizes）。
#    所以默认档下只要设备一行发不完（N 不是每行点数的整数倍），末排就会比别排多
#    一个；N 正好整除时各排全等，与旧行为一致。排不下时以**实际数据个数**为准
#    （绝不丢点）。
register_int("module_count", "模组个数", 0, 0, 64,
             tip="电池模组个数 → 数据网格的行数（一排一个模组）；"
                 "0 = 每排个数跟随设备发来的每行点数")
register_int("cells_per_module", "每模组电芯数", 0, 0, 32,
             tip="每个模组的电芯数 → 电压段每排放几个点；"
                 "0 = 跟随设备发来的每行点数")
register_int("temps_per_module", "每模组温度采集数", 0, 0, 64,
             tip="每个模组的温度采集点数 → 温度段每排放几个点；"
                 "0 = 跟随设备发来的每行点数")


# ---------------------------------------------------------------- 车辆（车型）管理
# 为什么要"车辆"这一层（用户 2026-10-22 原话："我需要一个功能能够自行适配更多
# 车型的电池箱……这个车辆选择要和模组个数绑定"）：
# 上面那三个 register_int 是**全机唯一**的一套数字 —— 换一台车（模组个数、每个
# 模组的电芯/温度采集数都不一样）就得手改三遍、还会改错。把"一套电池箱结构"
# 整个存成一条**车辆档案**，选车就换结构，才是这件事该有的形状。
#
# ⚠️ 为什么不给 vehicles 走 register_* 注册表：注册表的取值类型只有
# bool / int / choice 三种标量，而这里是一条**结构体列表**。硬塞进去要么把它
# 压成字符串再让设置面板去解析（面板不该认识业务结构），要么给注册表加第五种
# 类型（面板得写一套它专属的生成代码）。两者都比"本模块内自己读写两个键"更绕
# —— 所以这两个键**不进 specs()**：它们不参与"确定时统一落盘"那套流程（改一行
# 模组数字就该立刻看到接收区重排，等按确定太迟钝，见 ui_settings 的说明）。
#
# 存储形状（两个键，`custom/` 前缀与其它定制配置一致）：
#   custom/vehicles = 一段 JSON：{"current": "<当前车辆名>",
#                                "items": [{"name": …, "cells": [...], "temps": [...]}]}
#   custom/vehicle  = 当前车辆名（纯字符串镜像）
# 为什么还要一个单独的 vehicle 键：JSON 是给"人改注册表/排查"看的整体，而
# "当前选了哪台车"是个**标量状态**，单独一个键改起来最不容易写坏整体结构；
# 两者由本模块唯一的写入口 ``_save_store`` 一起写，永远不会各说各话。
#
# 铁律（与旧三件套的兼容）：**没有选车 / 车辆没有模组结构时，本模块一个字都
# 不多说** —— ``active_profile()`` 返回 None，主窗口照旧读 module_count /
# cells_per_module / temps_per_module（0 = 自动）。所以这套新东西对老用户是
# 完全惰性的，selftest 全程把三个旧键钉成 0 也不会因为"存在一台车"而变行为。
_VEHICLES_KEY = "vehicles"
_VEHICLE_KEY = "vehicle"

# 新车辆的预填值（用户点名："否则默认 3 模组 × 25 电芯 × 8 温度"）
DEFAULT_MODULES = 3
DEFAULT_CELLS = 25
DEFAULT_TEMPS = 8
# 单模组取值范围：与 register_int 里那两个上限一致（超过设备协议就不合理）
_CELL_RANGE = (0, 32)
_TEMP_RANGE = (0, 64)
_MAX_MODULES = 64


def _read_custom(key: str) -> str:
    """读一个 `custom/` 下的字符串配置（没存过 / 存过 null → 空串）。"""
    value = QSettings(_ORG, _APP).value(f"custom/{key}", "")
    if value is None:
        return ""                    # 显式写成 null（快照恢复会这样）→ 当没存过
    return str(value)


def _clamp_list(raw, limits: tuple[int, int]) -> list[int] | None:
    """把 JSON 里的一段数字列表归一成 int 列表；结构不对返回 None。

    单个值坏掉（"abc"、null）不当整条档案废掉 —— 归到下限（0 = 该排不参与
    分组），因为一台车丢几个模组数字，比整台车从列表里消失好排查得多。
    """
    if not isinstance(raw, (list, tuple)):
        return None
    low, high = limits
    out: list[int] = []
    for value in raw:
        try:
            out.append(max(low, min(high, int(value))))
        except (TypeError, ValueError):
            out.append(low)
    return out


def _equalize(cells: list[int], temps: list[int]) -> tuple[list[int], list[int]]:
    """让 cells / temps 长度相等（不变式：两者长度都等于模组个数）。

    短的一边用**自己最后一个值**补齐（新增模组沿用上一个模组的配置 —— 与设置
    面板"改模组个数时新增模组沿用最后一个模组的值"完全同一口径）；整边为空就
    用默认的 25 / 8。为什么不直接截断成 min：截断会**静默丢模组**，而这台车的
    模组个数是用户明确配过的，宁可补也不要少。
    """
    length = max(len(cells), len(temps))
    if length == 0:
        return [], []
    if len(cells) < length:
        cells = cells + [cells[-1] if cells else DEFAULT_CELLS] * (length - len(cells))
    if len(temps) < length:
        temps = temps + [temps[-1] if temps else DEFAULT_TEMPS] * (length - len(temps))
    return cells, temps


def _normalize_items(raw) -> list[dict]:
    """把 JSON 里的 items 归一成 ``[{"name", "cells", "temps"}, ...]``（只读，不改磁盘）。

    丢弃的只有"彻底没法用"的条目：不是字典、名字为空、名字重复（同名就认第一条
    —— 后面那条没有身份，留着会让"选中哪一台"变得不可预测）。
    """
    items: list[dict] = []
    # ⚠️ 本模块里 ``set`` 是被 register_int/set 那个**写配置函数**占用的名字，
    #    这里不能用内建 ``set()``（会 TypeError），用 list 判重就够（车辆数很少）。
    seen: list[str] = []
    for entry in raw if isinstance(raw, (list, tuple)) else ():
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name or name in seen:
            continue
        cells = _clamp_list(entry.get("cells"), _CELL_RANGE) or []
        temps = _clamp_list(entry.get("temps"), _TEMP_RANGE) or []
        cells, temps = _equalize(cells[: _MAX_MODULES], temps[: _MAX_MODULES])
        seen.append(name)
        items.append({"name": name, "cells": cells, "temps": temps})
    return items


def _load_store() -> dict:
    """读出 ``{"current": str, "items": [...]}``（**唯一读入口**）。

    只读一次 QSettings：主窗口每帧排版都会问一遍 ``active_profile()``，把
    "取 JSON + 取当前名"合并成一次读，热路径上少一次注册表往返。
    JSON 坏了（手改注册表、旧版本残留）就当成"没有车辆档案"——**不抛异常**：
    接收区排版不该因为一条坏配置就整段停摆。
    """
    data = None
    raw = _read_custom(_VEHICLES_KEY)
    if raw:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = None
    if not isinstance(data, dict):
        data = {}
    items = _normalize_items(data.get("items"))
    current = str(data.get("current") or "").strip()
    if current not in {item["name"] for item in items}:
        # JSON 里的 current 与 items 对不上（手改过）→ 退回单独的 vehicle 键
        current = _read_custom(_VEHICLE_KEY).strip()
    if current not in {item["name"] for item in items}:
        current = ""
    return {"current": current, "items": items}


def _save_store(items: list[dict], current: str) -> None:
    """**唯一写入口**：JSON 与 vehicle 镜像一起写、一起 sync（不会各说各话）。"""
    payload = json.dumps({"current": current, "items": items}, ensure_ascii=False)
    settings = QSettings(_ORG, _APP)
    settings.setValue(f"custom/{_VEHICLES_KEY}", payload)
    settings.setValue(f"custom/{_VEHICLE_KEY}", current)
    settings.sync()


def vehicles() -> list[dict]:
    """全部车辆档案（每条都带 name / cells / temps，结构已归一）。

    返回的是**副本**：调用方改了也不会悄悄改到磁盘上的档案（只在
    ``save_vehicle`` 里才落盘）。坏了的数据在这里就被丢掉了，上层不用再做防御。
    """
    return [{"name": item["name"],
             "cells": list(item["cells"]),
             "temps": list(item["temps"])}
            for item in _load_store()["items"]]


def vehicle_names() -> list[str]:
    """车辆名列表（下拉框按它生成，顺序 = 档案顺序 = 创建顺序）。"""
    return [item["name"] for item in _load_store()["items"]]


def current_vehicle() -> str:
    """当前车辆名；没选车（或名已不存在）返回空串。

    ⚠️ 空串是**正常状态**，不是错误：那时整套旧的三件套参数继续生效，行为与
    加这个功能之前一字不差（见本段开头的铁律）。
    """
    return _load_store()["current"]


def select_vehicle(name: str) -> None:
    """选中一台车。传空串 = 取消选择；传不存在的名字 = 不动（回调里少一层判断）。"""
    store = _load_store()
    target = str(name or "").strip()
    if target and target not in {item["name"] for item in store["items"]}:
        return
    _save_store(store["items"], target)


def save_vehicle(name: str, cells, temps) -> None:
    """存一台车的模组结构（**同名覆盖**，否则追加到末尾）。

    ``cells[i]`` / ``temps[i]`` 是第 i 个模组的电芯数 / 温度采集数，两者长度都
    等于模组个数（不等的话按 ``_equalize`` 补齐）。名字为空直接忽略 —— 没有名字
    的档案没法被选中，存了也只是条垃圾。同一条档案的**位置不变**（覆盖而不是
    删了再追加）：下拉框的排列顺序是用户认位置的一部分，改名/改结构不该让它跳。
    """
    target = str(name or "").strip()
    if not target:
        return
    cells_list = _clamp_list(list(cells or []), _CELL_RANGE) or []
    temps_list = _clamp_list(list(temps or []), _TEMP_RANGE) or []
    cells_list, temps_list = _equalize(cells_list[: _MAX_MODULES],
                                       temps_list[: _MAX_MODULES])
    store = _load_store()
    items = store["items"]
    for item in items:
        if item["name"] == target:
            item["cells"], item["temps"] = cells_list, temps_list
            break
    else:
        items.append({"name": target, "cells": cells_list, "temps": temps_list})
    _save_store(items, store["current"])


def delete_vehicle(name: str) -> None:
    """删掉一台车；删的正好是当前选中的那台 → 顺便把选择清空。

    为什么删当前车要清空而不是"自动选下一台"：选谁开哪台车的电池箱是用户的
    判断，替用户挑一台等于把数据按错误的模组结构排版（这是会看错电池的）。
    """
    target = str(name or "").strip()
    store = _load_store()
    items = [item for item in store["items"] if item["name"] != target]
    if len(items) == len(store["items"]):
        return                                  # 没有这台车，别白写一次注册表
    current = "" if store["current"] == target else store["current"]
    _save_store(items, current)


def rename_vehicle(old: str, new: str) -> bool:
    """改名：成功 True；**重名 / 老名字不存在 / 新名字为空 → False**（一个字不改）。

    重名必须拒绝而不是覆盖：两台同名车在下拉框里长得一样，"我改的是哪台"就
    永远说不清了（覆盖还会顺手毁掉另一台车的结构）。
    """
    source = str(old or "").strip()
    target = str(new or "").strip()
    if not target:
        return False
    store = _load_store()
    names = [item["name"] for item in store["items"]]
    if source not in names:
        return False
    if target != source and target in names:
        return False
    if target == source:
        return True                             # 没改，也算成功（别回滚输入框）
    for item in store["items"]:
        if item["name"] == source:
            item["name"] = target
            break
    current = target if store["current"] == source else store["current"]
    _save_store(store["items"], current)
    return True


def active_profile() -> tuple[list[int], list[int]] | None:
    """当前车辆的逐模组结构 ``(cells, temps)``；**没选车 / 结构为空 → None**。

    这个 None 就是兼容开关：主窗口拿到 None 就走旧的三件套（0 = 自动），
    拿到列表就按列表逐排排（一排 = 一个模组，见 ui_highlight 的
    ``volt_row_sizes`` / ``temp_row_sizes``）。返回副本，调用方随便改。
    """
    store = _load_store()
    current = store["current"]
    if not current:
        return None
    for item in store["items"]:
        if item["name"] == current:
            if not item["cells"]:
                return None                     # 选了车但还没配模组 → 等同没选
            return list(item["cells"]), list(item["temps"])
    return None
