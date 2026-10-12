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

from PySide6.QtCore import QSettings

_ORG, _APP = "MimoWorks", "SerialAssistant"
_SPECS: list[dict] = []

# 改过默认值的配置：{key: 旧默认值}。迁移时只动"没被用户选过、且还停在旧默认值"
# 的那些（见 migrate_defaults）；新默认值直接取注册表里那一条。
_LEGACY_DEFAULTS: dict[str, object] = {}


def register_bool(key: str, label: str, default: bool = False, tip: str = "") -> None:
    """注册一个布尔型定制配置（设置面板里是一枚拨动开关）。"""
    _SPECS.append({"key": key, "kind": "bool", "label": label,
                   "default": default, "tip": tip})


def register_int(key: str, label: str, default: int, minimum: int, maximum: int,
                 tip: str = "", suffix: str = "") -> None:
    """注册一个整数型定制配置（设置面板里是一个数字框）。"""
    _SPECS.append({"key": key, "kind": "int", "label": label, "default": default,
                   "minimum": minimum, "maximum": maximum, "tip": tip,
                   "suffix": suffix})


def register_slider(key: str, label: str, default: int, minimum: int,
                    maximum: int, tip: str = "", suffix: str = "",
                    scale: float = 1.0, preview: str = "") -> None:
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
    _SPECS.append({"key": key, "kind": "int", "widget": "slider", "label": label,
                   "default": default, "minimum": minimum, "maximum": maximum,
                   "tip": tip, "suffix": suffix, "scale": scale,
                   "preview": preview})


def specs() -> tuple[dict, ...]:
    """全部注册项（设置面板按它生成界面，顺序即注册顺序）。"""
    return tuple(_SPECS)


def get(key: str):
    """取一个定制配置的当前值。未注册的 key 直接抛 KeyError（写错了要炸出来）。"""
    spec = next(s for s in _SPECS if s["key"] == key)
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
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(spec["default"])


def display_value(key: str) -> float:
    """取配置的**显示值**（滑杆型按 ``scale`` 换算，其它类型原样返回）。

    字号这类小数用整数存储（半磅：21 → 10.5pt），面板上的标签与预览、以及
    主窗口应用字号时都走这里，免得 scale 的知识散落在各处。
    """
    spec = next(s for s in _SPECS if s["key"] == key)
    return get(key) * float(spec.get("scale", 1.0))


def set(key: str, value) -> None:
    """写入一个定制配置（由设置面板确定时统一调用）。

    写完立刻 ``sync()``：QSettings 的写入按实例缓冲，不同步的话紧接着
    另一个实例去读会拿到旧值（自测里 set(7) 后 snap 读到的还是 3，实测踩过）。
    """
    next(s for s in _SPECS if s["key"] == key)      # 未注册的 key 照样炸出来
    settings = QSettings(_ORG, _APP)
    settings.setValue(f"custom/{key}", value)
    settings.sync()


def mark_chosen(key: str) -> None:
    """记下"用户在设置面板里明确改过这个键"。

    迁移默认值时的判据必须是**有没有选过**，不能只看当前值 —— 否则用户主动
    拨到旧默认值那一侧的选择会被当成"没动过"覆盖掉（毛玻璃强度那边踩过同一个
    坑，见 MEMORY.md 的 ``glass_chosen``）。
    """
    next(s for s in _SPECS if s["key"] == key)      # 未注册的 key 照样炸出来
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
        spec = next(s for s in _SPECS if s["key"] == key)
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
