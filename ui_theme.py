"""主题系统：亮/暗两套 token、跟随 Windows 主题、全局样式表。

设计方向 —— 黑白 + 小米风格 + 车队规范
- 界面严格黑白灰，不引入任何色相。所以在黑白系统里「强调」不是靠某个色相，
  而是靠反白：主按钮在浅色主题下是墨色实底、深色主题下是近白实底。
- 版式走小米那套：大圆角（卡片 20、控件 12、按钮药丸）、留白给足、
  靠柔和阴影而不是重描边分层、字重层级分明。
- 颜色 token 用名字取值（token("ink")），不用模块级常量。因为要支持两套主题，
  写死的常量没法跟着主题走；控件在绘制时取值，换主题只需重设 token 再重绘。
- 字体栈把 MiSans 放前面（装了就用），退到 Segoe UI Variable 与雅黑。
  数字用 Bahnschrift —— 这是车队 VIS 规范里写明的「编号和参数使用 Bahnschrift」。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont

# ------------------------------------------------------------------ token

LIGHT = {
    # 页面**纯白**。早先这里是一道对角渐变（bg_hi → bg_lo，#AEB8C4 那一端会把
    # 右下角压成一片脏灰，用户明确否掉了）。留着三个 token 是为了让取值方不用改。
    "bg": "#FFFFFF",              # 页面底色
    "bg_hi": "#FFFFFF",           # 背景亮端
    "bg_lo": "#FFFFFF",           # 背景暗端 —— 与亮端同值 = 没有渐变
    "surface": "#FFFFFF",         # 卡片
    "surface_2": "#F7F8FA",       # 次级面（输入框、下拉框底）
    "surface_hover": "#ECEFF3",
    "ink": "#111214",             # 主文字（近黑）
    "ink_2": "#5F6368",           # 次要文字
    "ink_3": "#8A9099",           # 弱化文字 / 占位
    "ink_disabled": "#BFC4CB",
    "line": "#E4E7EB",            # 描边与分隔
    "line_strong": "#D2D7DD",
    "fill_primary": "#111214",    # 主按钮底：黑白系统里强调靠反白
    "fill_primary_hover": "#2C2F33",
    "on_primary": "#FFFFFF",
    "sel": "#111214",
    "on_sel": "#FFFFFF",
    "shadow": "#0B0F14",
    "pulse": "#111214",
    "rim": "#FFFFFF",             # 玻璃上缘兜住的光（见 ui_surface 的描边）
    # 液态玻璃的"镜面高光"：左上亮边（hi）/ 右下暗边（lo）构成的双色调描边 ——
    # 苹果 §12 的 "bright top edge = light catching the material"。
    # 浅色主题下暗边不能纯黑（会像描了一圈墨线），用冷灰才像玻璃的折光。
    "glass_hi": "#FFFFFF",
    "glass_lo": "#8A94A2",
    "volt_high": "#C42B1C",       # 电压超上限（红）—— 浅底用深红才够对比
    "volt_low": "#0E7B3D",        # 电压低于下限（绿）
    "bms_error": "#C2410C",       # BMS ERROR（橙）—— 浅底用深橙才够对比（用户：黄改橙）
    "temp_warn": "#A16207",       # 温度越界（黄）—— 与 BMS 区分开：BMS 是橙、温度是黄
    "alert_red": "#C42B1C",       # 界面上的警示红（「定位异常」那枚三角感叹号）
}

DARK = {
    # 页面也是**纯色**（用户："背景不要渐变，要纯色"）。早先这里是 bg_hi #2E333B
    # 到 bg_lo #050506 的对角渐变，现在统一填 bg；两个旧 token 留着同值，
    # 免得取值方还要为"哪个主题有没有两端颜色"分情况。
    "bg": "#0D0D0F",
    "bg_hi": "#0D0D0F",
    "bg_lo": "#0D0D0F",
    "surface": "#1A1B1E",
    "surface_2": "#141518",
    "surface_hover": "#26282C",
    "ink": "#F2F3F5",
    "ink_2": "#D2D6DC",
    "ink_3": "#A6ACB5",
    # 禁用态图标/文字：原 #4A4E54 在"最大化后整屏只剩一列图标"时像坏了
    # （用户连续多轮反馈"灰色/暗淡"）。抬到 ink_3(#6E737A) 同级：看得清，
    # 但仍明显低于可用态，"不可用"的语义不变。
    "ink_disabled": "#8E949C",
    "line": "#272A2E",
    "line_strong": "#383C42",
    "fill_primary": "#F2F3F5",
    "fill_primary_hover": "#FFFFFF",
    "on_primary": "#101113",
    "sel": "#F2F3F5",
    "on_sel": "#101113",
    "shadow": "#000000",
    "pulse": "#F2F3F5",
    "rim": "#FFFFFF",             # 玻璃上缘兜住的光（见 ui_surface 的描边）
    # 液态玻璃的镜面高光：深色主题下"亮边近白、暗边纯黑"对比最强，最像玻璃的折光
    "glass_hi": "#FFFFFF",
    "glass_lo": "#000000",
    "volt_high": "#FF6B6B",       # 电压超上限（红）
    "volt_low": "#51CF66",        # 电压低于下限（绿）
    "bms_error": "#FB923C",       # BMS ERROR（橙）
    "temp_warn": "#FACC15",       # 温度越界（黄）
    "alert_red": "#FF6B6B",       # 界面上的警示红（「定位异常」那枚三角感叹号）
}

_PALETTES = {"light": LIGHT, "dark": DARK}
_SCHEME = "dark"
_TOKENS = dict(DARK)

# 阴影在深色主题下几乎看不见，也不该看见；浅色主题才是它的主场
SHADOW_ALPHA = {"light": 30, "dark": 0}

# ---------------------------------------------------------------- 毛玻璃强度
#
# 毛玻璃效果由两个量共同决定，**必须一起调**（这也是设置面板那根滑杆真正在动的东西）：
# - 面板面色不透明度：玻璃能透出多少底色；
# - 表面颗粒幅度：玻璃被打毛到什么程度。
# 只调不透明度 —— 透出来的是干净底色，那叫"透明"不叫"磨砂"；
# 只调颗粒 —— 玻璃仍是不透明白底，粉感像贴了张贴纸。
# 所以强度 t(0~1) 同时插值这两条曲线，滑杆向右 = 越透 + 越毛。
#
# （早先这里还有第三个量：背景的高斯模糊半径。背景改成纯色之后它已经没有意义 ——
#   纯色糊多少遍还是纯色 —— 所以连同背景虚化那一套一起删掉了。）
GLASS_ALPHA_RANGE = {"light": (210, 118), "dark": (206, 116)}

DEFAULT_GLASS = 0                 # 默认强度（0~100）。用户："暂时不要毛玻璃了，初始设成 0"

# 历史上出现过的**默认值**（68 是更早的，100 是上一版）。
# 之所以要留着它们：配置里的 glass 无论用户动没动过滑杆都会被写盘（关窗时保存当前值），
# 所以"值等于旧默认"只说明它可能是从没被选过的 —— 这种配置迁到当前默认。
# 但用户**明确选过**的值一律尊重，哪怕是 100 这个旧默认值本身；
# "选过没有"由一个单独的标记记录（glass_chosen），见 MainWindow._restore_settings。
LEGACY_GLASS = (68, 100)

_GLASS = DEFAULT_GLASS


def set_glass_strength(value: int) -> None:
    global _GLASS
    _GLASS = max(0, min(100, int(value)))


def glass_strength() -> int:
    return _GLASS


def _glass_t() -> float:
    return _GLASS / 100.0


def glass_alpha() -> int:
    """面板面色不透明度：强度越高越透，背景才透得上来。"""
    low, high = GLASS_ALPHA_RANGE.get(_SCHEME, GLASS_ALPHA_RANGE["dark"])
    return int(round(low + _glass_t() * (high - low)))


# ---------------------------------------------------------------- 界面材质
#
# **两种材质二选一**（设置 → 定制功能 → 界面材质），两者**共用同一根强度滑杆**：
#   · ``frosted``  毛玻璃 —— 现有表现（散射颗粒 + 玻璃底色 + 上缘兜到的光）；
#   · ``liquid``   液态玻璃 —— 苹果 Liquid Glass 的桌面近似（半透明主体 +
#                  镜面双色调描边 + 边缘内发光 + 主体中心提亮）。
#
# 强度 0 = 清玻璃（几乎只剩一层很淡的底色），100 = 材质拉满（各自特征最明确）。
# 同一个 0~100 由两种材质**各自映射**到自己的视觉参数上 —— 毛玻璃动的是颗粒幅度
# 与不透明度（见上面两条曲线），液态玻璃动的是镜面高光、内发光与中心提亮。
MATERIAL_FROSTED = "frosted"
MATERIAL_LIQUID = "liquid"
MATERIALS = (MATERIAL_FROSTED, MATERIAL_LIQUID)

_MATERIAL = MATERIAL_FROSTED


def set_material(name: str) -> None:
    """切换界面材质。非法值一律退回毛玻璃（默认档）。"""
    global _MATERIAL
    _MATERIAL = name if name in MATERIALS else MATERIAL_FROSTED


def material() -> str:
    return _MATERIAL


# 液态玻璃：不透明度范围（低 → 高）与三处高光的幅度上限。
# 与毛玻璃**方向相反**：强度越高，材质越"实"、镜面越亮 ——
# 苹果那套玻璃是**越厚越亮**，这里把"厚度"直接绑在强度上。
LIQUID_ALPHA_RANGE = {"light": (124, 206), "dark": (150, 210)}
LIQUID_SPEC_MAX = 214        # 左上镜面高光（亮边）的峰值 alpha
LIQUID_CORE_MAX = 46         # 主体中心提亮的峰值 alpha（模拟厚度 / 折射）
LIQUID_INNER_MAX = 62        # 边缘内发光的峰值 alpha


def liquid_alpha() -> int:
    """液态玻璃主体不透明度：强度越高越实（能兜住更多镜面高光）。"""
    low, high = LIQUID_ALPHA_RANGE.get(_SCHEME, LIQUID_ALPHA_RANGE["dark"])
    return int(round(low + _glass_t() * (high - low)))


def liquid_spec_alpha() -> int:
    """镜面双色调描边的峰值不透明度。强度 0 也留 ~30%，清玻璃仍有那道折光。"""
    return int(round(LIQUID_SPEC_MAX * (0.30 + 0.70 * _glass_t())))


def liquid_core_alpha() -> int:
    """主体中心提亮（"厚度"）的不透明度：清玻璃几乎没有，拉满才明显。"""
    return int(round(LIQUID_CORE_MAX * _glass_t()))


def liquid_inner_alpha() -> int:
    """边缘内发光的不透明度：让主体边缘"亮一圈"，像玻璃把光收在壁里。"""
    return int(round(LIQUID_INNER_MAX * (0.25 + 0.75 * _glass_t())))


# ---------------------------------------------------------------- 磨砂颗粒
#
# 浅色主题的页面是**纯白**的，背景里没有任何中频细节可以模糊 ——
# 把一张纯白图糊上十遍还是纯白，光靠"虚化背景"这条路在浅色主题下已经走不通了。
#
# 所以浅色主题的磨砂改由**玻璃自身**承担：面板上铺一层**糊过的**随机颗粒（雾面）。
# 这不是退而求其次 —— 磨砂玻璃真正的物理特征本来就是表面微结构把透过的光打散，
# 一块酸蚀毛玻璃贴在白墙上，照样看得出"毛"，靠的正是这层颗粒而不是身后有什么。
#
# **两个主题现在都铺颗粒**：深色主题的页面也改成纯色之后，它身后不再有明暗起伏可糊，
# 和浅色一样只能靠玻璃自身的颗粒承载磨砂感。颗粒方向按主题反着来 ——
# 浅色是暗颗粒压在亮面上，深色是亮颗粒压在暗面上，两边都是在"把面打毛"。
#
# 三个参数：
#   amp  —— 逐像素随机 alpha 的上限（按强度滑杆 0~1 插值）。
#   blur —— **生成后整体模糊的半径（逻辑像素）**。这一项不是可选项，是"玻璃还是纸"的分界。
#   tile —— 贴图边长（逻辑像素）。
#
# ⚠️ **blur 必须 > 0**。逐像素的硬噪点不管幅度多低，落在屏幕上都被读成**砂纸 / 粗纸**的
# 纹理（用户原话："是要毛玻璃效果而不是粗纸啊"）—— 因为真磨砂玻璃的散射结构远小于一个
# 像素，眼睛看到的是**雾**，不是砂砾。糊一道之后相邻像素被平均成 2~3px 的柔和起伏，
# 那个"雾"才出得来。
# 模糊**不会削掉面板的存在感**：它保均值、只削方差。原来靠"平均变暗"撑起来的
# 「卡片比页面深一档」原样保留，被削掉的只是刺眼的颗粒对比度。
#
# 定档依据（都是 1:1 物理像素目视定的）：blur 再小（1.0）仍然看得出砂砾；2.0 才干净。
# 幅度反过来要**提上去**才有"毛"可看 —— 硬噪点时代 amp 26 就够刺眼，糊过之后同样
# 幅度只剩一层几乎看不见的平色，所以提到 50（面板平均密度与改动前基本一致：
# 浅色卡面 231 → 229，深色 30 → 38）。两个主题同档：面色明度差都在 200 上下，
# 同样的额外不透明度看着是同一个粗细。
FROST_GRAIN = {
    "light": {"color": "#45505E", "amp": (0, 50), "blur": 2.0, "tile": 128},
    "dark": {"color": "#FFFFFF", "amp": (0, 50), "blur": 2.0, "tile": 128},
}


def frost_gain() -> float:
    """磨砂颗粒的铺陈强度 0~1。返回 0 表示该主题不铺颗粒。

    颗粒贴图只按**最大幅度**生成一张，幅度靠这里的返回值去 ``setOpacity()`` 缩放。
    """
    spec = FROST_GRAIN.get(_SCHEME, FROST_GRAIN["dark"])
    top = spec["amp"][1]
    if top <= 0:
        return 0.0
    low, _ = spec["amp"]
    return max(0.0, min(1.0, (low + _glass_t() * (top - low)) / top))

# 品牌页头。VIS 规范：横排中英文组合最小屏幕宽度 560px，所以页头高度由它按比例
# 算出来（约 132px），不是随便定的。
LOCKUP_MIN_W = 560.0
HEADER_PAD = 16

# 拨动开关：开启时蓝色轨道 + 白色圆点，浅色和深色主题**完全一致**（用户指定）。
# 这是界面里唯一的功能性彩色，不参与黑白主题那套明暗互换的规则 ——
# 开关是状态指示，用固定色比"深色底反白"更容易一眼读懂。
SWITCH_ON = "#3482FF"
SWITCH_KNOB = "#FFFFFF"
# 关闭态的轨道仍然跟着主题走（浅色浅灰 / 深色深灰），否则分不出开还是关
SWITCH_OFF = {"light": "#D5D9DE", "dark": "#3A3E44"}

# ------------------------------------------------------------------ 尺寸

CARD_RADIUS = 26
CONTROL_RADIUS = 16
CARD_PAD = 18
# 卡片间距留得比较足，给圆角和阴影喘息的空间，也更接近小米那种疏朗的密度
GAP = 18
TITLE_H = 30
SHADOW_PAD = 6             # 卡片四周为阴影预留的空隙
SHADOW_BLUR = 18

# 自绘标题栏（见 ui_brand.CaptionBar）：窗口改成**无边框**之后系统标题栏没了，
# 最小化/最大化还原/关闭这三颗由我们自己画。条高 34、键 46×30 —— 比 Windows
# 原生的 32 高一点点，给悬停色块上下留 2px 呼吸。
CAPTION_H = 34
CAPTION_BTN_W = 46
CAPTION_BTN_H = 30
# 关闭键右边缘离**窗口右端**留一点（标题栏整条出血到窗口边缘，见
# main_window._build_ui，所以这个数就是离窗口边的距离）。原来是 6，加上标题栏
# 自己没有出血、被根布局又往里推了 12px，实际离窗口边 18px —— 用户点名"为什么
# 距离界面最右上角仍有较大间隙"。现在 4px：贴着但不压窗口边（窗口化时 DWM 会在
# 角上切一刀，留 4px 不至于被切到）。
CAPTION_INSET = 4
CAPTION_GAP = 2            # 三颗键之间

# 字体族：MiSans 装了就优先（小米自家字体），否则退到 Windows 的系统 UI 字体
_UI_FAMILIES = ["MiSans", "MiSans VF", "Segoe UI Variable Display", "Segoe UI",
                "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "sans-serif"]
# 车队 VIS 规范：编号和参数使用 Bahnschrift
_NUM_FAMILIES = ["Bahnschrift", "Segoe UI Variable Display", "Segoe UI",
                 "Microsoft YaHei UI", "sans-serif"]
_MONO_FAMILIES = ["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono",
                  "monospace"]


def ui_font(size: float = 12.5, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies(_UI_FAMILIES)
    font.setPointSizeF(size)
    font.setWeight(weight)
    return font


def num_font(size: float = 12.5, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """数字与参数读值。规范指定 Bahnschrift。"""
    font = QFont()
    font.setFamilies(_NUM_FAMILIES)
    font.setPointSizeF(size)
    font.setWeight(weight)
    return font


def mono_font(size: float = 10.5) -> QFont:
    font = QFont()
    font.setFamilies(_MONO_FAMILIES)
    font.setPointSizeF(size)
    return font


# --------------------------------------------------------------- 图标字体

# Windows 自带的图标字体：Win11 用 Segoe Fluent Icons，Win10 退到 Segoe MDL2 Assets。
# 用系统自带图标字体，字形天然就是「Windows 那套」，既不用自己画也不用带素材进来。
_ICON_FAMILIES = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]


def icon_font(pixel_size: int = 15) -> QFont:
    font = QFont()
    font.setFamilies(_ICON_FAMILIES)
    font.setPixelSize(pixel_size)
    return font


# 图标码位。**这几个都是渲染出来逐个看过确认的**，别凭记忆改 ——
# 记错一个码位出来的就是个毫不相干的符号，而且在界面上看着"像个图标"，很容易漏掉。
GLYPH_REFRESH = "\uE72C"          # 环形箭头
GLYPH_CLEAR = "\uE74D"            # 垃圾桶
GLYPH_SAVE = "\uE74E"             # 软盘
GLYPH_EXPAND = "\uE740"           # 箭头朝外（展开）
GLYPH_RESTORE = "\uE73F"          # 箭头朝内（还原）
GLYPH_SEND = "\uE724"             # 纸飞机
GLYPH_SETTINGS = "\uE713"         # 齿轮
GLYPH_COLLAPSE = "\uE70D"         # 向下小箭头
GLYPH_EXPAND_CHEVRON = "\uE70E"   # 向上小箭头
# 上下 chevron 的通用名。搜索条拿它当"上一个 / 下一个"，比借 expand/collapse 那对名字好读
GLYPH_CHEVRON_DOWN = GLYPH_COLLAPSE
GLYPH_CHEVRON_UP = GLYPH_EXPAND_CHEVRON
GLYPH_SEARCH = "\uE721"           # 放大镜
GLYPH_CLOSE = "\uE711"            # 关闭（叉）
GLYPH_ADD = "\uE710"              # 加号（新增）
GLYPH_PAUSE = "\uE769"            # 暂停（两竖）
GLYPH_PLAY = "\uE768"             # 播放（三角）
GLYPH_COPY = "\uE8C8"             # 复制（两张纸）

# 窗口标题栏三键的字形。用系统那套 Chrome* 码位而不是上面几个通用图标：
# 这几个就是 Windows 自己画在标题栏上的形状（细横线 / 方框 / 叠方框 / 细叉），
# 和用户在系统窗口上看到的完全一致 —— 自绘标题栏最怕"看着不像系统键"。
GLYPH_WIN_MIN = "\uE921"          # 最小化（一条横线）
GLYPH_WIN_MAX = "\uE922"          # 最大化（方框）
GLYPH_WIN_RESTORE = "\uE923"      # 还原（两个叠着的方框）
GLYPH_WIN_CLOSE = "\uE8BB"        # 关闭（细叉）

# 搜索命中的底色。界面是严格黑白灰，所以不做"搜索黄"那种彩色标记 ——
# 两档灰就够分层：全部命中一档淡的，当前命中一档重的。
MATCH_FILL = {"light": "#D2D9E1", "dark": "#343A43"}
MATCH_FILL_CURRENT = {"light": "#A7B3C1", "dark": "#4D5764"}


def match_fill(current: bool = False) -> QColor:
    table = MATCH_FILL_CURRENT if current else MATCH_FILL
    return QColor(table.get(_SCHEME, table["dark"]))


# 接收区里"时间戳"和"序号"的着色（用户指定：序号蓝、时间戳紫，见 ui_highlight）。
#
# 界面本来是严格黑白灰、唯一的功能色是开关那支蓝（SWITCH_ON）。序号沿用**同一族**
# 蓝，让"彩色 = 结构与状态提示"这条规矩保持一致：浅色底用偏深的蓝才够对比度
# （#3482FF 落在近白底上只有 3.6:1，读小字偏吃力），深色底用偏亮的蓝才不糊。
# 时间戳用户点名要紫 —— 和序号蓝同档亮度，一个冷一个暖，扫一眼就能分开"什么时候"
# 和"第几个点"。
RX_ACCENT = {"light": "#1A6BE0", "dark": "#5C9DFF"}
STAMP_ACCENT = {"light": "#7C3AED", "dark": "#C084FC"}


def rx_accent() -> QColor:
    return QColor(RX_ACCENT.get(_SCHEME, RX_ACCENT["dark"]))


def stamp_accent() -> QColor:
    return QColor(STAMP_ACCENT.get(_SCHEME, STAMP_ACCENT["dark"]))


# ------------------------------------------------------------------ 取值

def current_scheme() -> str:
    return _SCHEME


def set_scheme(scheme: str) -> None:
    """切换主题。控件在绘制时读 token，所以只要重绘就会跟上。"""
    global _SCHEME
    _SCHEME = scheme if scheme in _PALETTES else "dark"
    _TOKENS.clear()
    _TOKENS.update(_PALETTES[_SCHEME])


def set_scheme_mix(first: str, second: str, t: float) -> None:
    """主题**过渡**：把 token 设成两套主题之间的插值色（t=0 → first，t=1 → second）。

    苹果 §14：避免明暗突变 —— 换主题不该是"啪一下全黑/全白"。Qt 里 QSS 驱动的
    控件层没法逐帧插值（那要每帧重刷整张样式表并 re-polish 所有控件，125Hz 下
    直接卡死），所以过渡只做在**自绘的大色块**上（页面背景、卡片、页头、回放条）——
    屏幕上占面积最大的就是这几块，渐变它们就能消掉硬切感；QSS 那层由调用方
    一次性切到目标主题。

    只动已经存在的 token（两套主题的键必须一致，缺键时以目标主题为准）。
    """
    a = _PALETTES.get(first, DARK)
    b = _PALETTES.get(second, DARK)
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    for key, target in b.items():
        ca = QColor(a.get(key, target))
        cb = QColor(target)
        _TOKENS[key] = QColor(
            round(ca.red() + (cb.red() - ca.red()) * t),
            round(ca.green() + (cb.green() - ca.green()) * t),
            round(ca.blue() + (cb.blue() - ca.blue()) * t),
        ).name()


def token(name: str) -> str:
    try:
        return _TOKENS[name]
    except KeyError:
        raise KeyError(f"主题里没有这个 token：{name!r}") from None


def qcolor(name: str, alpha: int | None = None) -> QColor:
    result = QColor(token(name))
    if alpha is not None:
        result.setAlpha(alpha)
    return result


def scheme_qcolor(name: str, scheme: str, alpha: int | None = None) -> QColor:
    """按**指定主题**取色，不动全局 token。

    给"绘制时主题由参数决定"的路径用（页面背景就是这样：换个主题要立刻按新主题重绘，
    而自动重绘的时序里可能还没轮到全局 token 切换）。
    """
    palette = _PALETTES.get(scheme, DARK)
    try:
        result = QColor(palette[name])
    except KeyError:
        raise KeyError(f"主题里没有这个 token：{name!r}") from None
    if alpha is not None:
        result.setAlpha(alpha)
    return result


def rgb(name: str) -> str:
    """给 QSS 里拼 rgba() 用，因为 QSS 不吃 QColor。"""
    c = QColor(token(name))
    return f"{c.red()},{c.green()},{c.blue()}"


def shadow_alpha() -> int:
    return SHADOW_ALPHA.get(_SCHEME, 0)


def switch_off_color() -> QColor:
    return QColor(SWITCH_OFF.get(_SCHEME, SWITCH_OFF["dark"]))


# 文本区底色。不透明是用户明确要求的：输入和输出的地方不要高斯模糊 ——
# 日志要的是对比度和稳定，让背景纹理透上来只会干扰读数字。
EDITOR_RGBA = {"light": "#F6F7F9", "dark": "#131417"}


def editor_rgba() -> str:
    return EDITOR_RGBA.get(_SCHEME, EDITOR_RGBA["dark"])


def mix(first: QColor, second: QColor, t: float) -> QColor:
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return QColor(
        round(first.red() + (second.red() - first.red()) * t),
        round(first.green() + (second.green() - first.green()) * t),
        round(first.blue() + (second.blue() - first.blue()) * t),
        round(first.alpha() + (second.alpha() - first.alpha()) * t),
    )


def detect_system_scheme() -> str:
    """系统当前是亮色还是暗色。

    优先用 Qt 的 colorScheme（6.5+ 支持，还能监听变化）；拿不到时退到注册表。
    """
    try:
        from PySide6.QtGui import QGuiApplication

        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return "light"
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
    except Exception:
        pass
    try:
        import winreg

        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
    except Exception:
        return "dark"


# ------------------------------------------------------------------ 样式表

_QSS = """
QWidget {
    color: @ink@;
}

/* ---- 悬停提示 ----
   字号必须在这里写死。tooltip 默认取应用字体（入口设的是 12.5pt ≈ 16.7px），
   对一行中文提示来说太大了 —— 悬停这种瞬时信息不该比它旁边的正文还抢眼。
   12px 与 Windows 系统提示同档。 */
QToolTip {
    background: @surface@;
    color: @ink@;
    border: 1px solid @line@;
    border-radius: 8px;
    padding: 5px 9px;
    font-size: 12px;
}

QLabel {
    background: transparent;
    /* 苹果 §12「vibrancy」：毛玻璃表面上的文字不要用平面灰，要**更高对比 +
       略重字重**。此前全应用统一 400，是"看着灰/没层级"的最大原因 —— 500 立见。 */
    font-weight: 500;
    color: @ink_2@;
}

QLabel#title {
    /* 卡片标题：苹果式"提一级"—— Semibold + 主文字色 */
    font-weight: 600;
    color: @ink@;
}

/* ---- 文本区 ---- */
QPlainTextEdit {
    background: @editor@;
    border: 1px solid @line@;
    border-radius: @CONTROL_RADIUS@px;
    padding: 10px 12px;
    color: @ink@;
    selection-background-color: rgba(@sel_rgb@,0.18);
    selection-color: @ink@;
}

QPlainTextEdit:focus {
    border: 1px solid @line_strong@;
}

/* 发送输入框：上下内边距**故意不相等** —— 为的是让第一行文字在框里垂直居中。
   一行的高度是照字体度量排出来的，而中文字形在这个行盒里天生偏上：本机实测
   （Cascadia Mono + 中文回退字体）行盒内墨迹上沿 1.5px、下沿 14.8px，于是
   "上边框到字"比"字到下边框"少 8px（实测 15.5 : 23.5），看着就是字贴着上面。
   把原来的 10/10 拆成 14/6 正好补掉这 8px。
   ⚠️ **上下之和仍然是 20**，所以 main_window.TX_EDITOR_CHROME 不用跟着改；
   改这两个数就得同时改它，否则输入框的最小高度会算错。 */
QPlainTextEdit#txEditor {
    padding: 14px 12px 6px 12px;
}

/* ---- 接收区搜索框 ----
   按 objectName 限定，不用裸 QLineEdit：串口名和波特率的下拉框是可编辑的，
   里面各藏着一个 QLineEdit，裸规则会连它们一起改掉。 */
QLineEdit#searchEdit {
    background: @surface_2@;
    border: 1px solid @line@;
    border-radius: @CONTROL_RADIUS@px;
    padding: 5px 10px;
    color: @ink@;
    selection-background-color: rgba(@sel_rgb@,0.18);
    selection-color: @ink@;
}

QLineEdit#searchEdit:hover {
    background: @surface_hover@;
}

QLineEdit#searchEdit:focus {
    border: 1px solid @line_strong@;
}

/* ---- 设置面板「模组 i」的数量输入框（电芯数量：〔N〕个）----
   与 searchEdit 同一套族谱：surface_2 底 + line 描边 + CONTROL_RADIUS 圆角，
   hover/focus 也一致。高度对齐 QComboBox（min/max 20 + 上下 padding 6 + 边框 2
   = 34，见下面 QComboBox 那条的换算），同一行里两种控件才不会一高一矮。 */
QLineEdit#countEdit {
    background: @surface_2@;
    border: 1px solid @line@;
    border-radius: @CONTROL_RADIUS@px;
    padding: 6px 8px;
    min-height: 20px;
    max-height: 20px;
    color: @ink@;
    selection-background-color: rgba(@sel_rgb@,0.18);
    selection-color: @ink@;
}

QLineEdit#countEdit:hover {
    background: @surface_hover@;
}

QLineEdit#countEdit:focus {
    border: 1px solid @line_strong@;
}

QLabel#searchCount {
    color: @ink_2@;
}

/* ---- 历史回放提示条 ----
   ⚠️ 这里**故意没有** `QWidget#replayBar { background/border/border-radius }`：
   QSS 对普通 QWidget 的 border-radius 在真实主窗口里会被整条丢掉（渲染成直角，
   2026-10-14 用户报"边框没有圆角"就是这个），所以那条面改由
   ``main_window.ReplayBar.paintEvent`` 自绘（和卡片同一条路，圆角取 CARD_RADIUS，
   描边取 line）。留着 QSS 那条只会和自绘打架。
   原来还挂着一条 `border-left: 3px solid @fill_primary@`（深色主题下就是一根近白
   竖条），用户点名"如非必要请一并移除" —— 文案本身已说明是回放数据，纯装饰，去掉。 */

QLabel#replayCaption {
    color: @ink_2@;
}

/* ---- 接收区右上角的实时时钟 ----
   放在卡片标题栏右侧，属于"参数"性质，用 Bahnschrift。 */
QLabel#rxClock {
    color: @ink_2@;
}

/* ---- 下拉框 / 数字框：药丸感，靠圆角而不是描边 ----
   min/max 一起写死：QSpinBox 自带的 sizeHint 比下拉框高 4px（它把上下步进按钮
   也算进去了），两者并排放在"串口配置"表单里会一高一矮。把高度夹死成同一档，
   行高就齐了。 */
QComboBox, QSpinBox {
    background: @surface_2@;
    border: 1px solid @line@;
    border-radius: @CONTROL_RADIUS@px;
    /* 上下**对称**，这是"基准"——谁都不要在这里做视觉偏心：
       不可编辑下拉框的字形在行盒里天生偏下，且偏多少**逐条文字不同**
       （"1" 偏 1px、"none" 偏 2.75px、CJK 偏 1.4px），全局给不出一刀切的值。
       所以由 ChevronComboBox 在运行时按**实测墨迹**给每条算自己的实例
       padding（_fit_text_vertical）；数字框/可编辑下拉框的行内编辑框则由
       _place_edit 直接摆位。这一条只管外观与尺寸。
       ⚠️ 上下之和必须仍是 12 —— 框高 = min-height 20 + 12 + 边框 2 = 34，
       改了这个和就得连 min/max-height 一起动。 */
    padding: 6px 10px 6px 12px;
    min-height: 20px;
    max-height: 20px;
    color: @ink@;
}

QComboBox:hover, QSpinBox:hover {
    background: @surface_hover@;
}

/* ---- 进度条（自动更新的下载进度）----
   跟输入框同一族外观：surface_2 底 + line 描边 + 圆角；填充用主按钮那支墨色
   （强调=反白体系里的同一支），百分比文字不放条里 —— 状态行走标签。 */
QProgressBar {
    background: @surface_2@;
    border: 1px solid @line@;
    border-radius: 7px;
    min-height: 14px;
    max-height: 14px;
    color: transparent;
}

QProgressBar::chunk {
    background: @fill_primary@;
    border-radius: 6px;
}

/* 文字一律**左端对齐**（用户 2026-10-12 点名"这些地方需要左端对齐"）。
   ⚠️ 只在有 QSS 规则时才会暴露这个坑：QStyleSheetStyle 接管 CE_ComboBoxLabel
   之后，**不可编辑**的下拉框文字会改成**居中** —— 于是同一栏里"1""8""none"
   居中、"115200"（可编辑，文字在 QLineEdit 里）和"5000 行"（QSpinBox，行内
   编辑框）左对齐，五行的文字左边缘参差不齐。text-align 是 QSS 对下拉框生效
   的属性，加这一条它们就都从 padding-left 的 12px 起排了。 */
QComboBox {
    text-align: left;
}

/* 可编辑下拉框内部那层 QLineEdit：Qt 的 QSS **不支持负 margin**（试过
   margin-left:-6px，实测完全无效、编辑框还停在原处），所以不在样式表里挪，
   改由 ChevronComboBox 在事件过滤器里摆（见 _place_edit）—— 和 ChevronSpinBox
   同一套办法。这里只负责把它自身的底/描边去掉，免得覆盖下拉框的外观。 */
QComboBox QLineEdit {
    background: transparent;
    border: none;
}

/* 数字框的内层编辑框**同样要去掉自带底**：QSpinBox 的 QSS padding 对行内编辑框
   不生效（见 ChevronSpinBox 的注释），编辑框由样式直接摆 —— 它自带的那层不透明
   底比 surface_2 深一档、高度又铺不满整框，框底会漏出一条浅带（用户 2026-10-22
   报的"模组个数的数据距离上下边框距离不一样"，实测就是这条缝在视觉上把框"截短"了）。
   透明之后外层统一上色，竖直居中仍由 _place_edit 按实测墨迹摆。 */
QSpinBox QLineEdit {
    background: transparent;
    border: none;
}

QComboBox:focus, QSpinBox:focus {
    border: 1px solid @line_strong@;
}

QComboBox:disabled, QSpinBox:disabled {
    color: @ink_disabled@;
    background: transparent;
    border-color: @line@;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

/* 箭头不由 QSS 画：QSS 的三角靠"零尺寸 + 彩色边框"拼，Fusion 下常被挤成一个小方块。
   现在改为自绘 chevron（ChevronComboBox / ChevronSpinBox），字形取自系统图标字体，
   与界面其它图标同源，主题跟随、高 DPI 下也锐利。这里只把原生箭头彻底关掉。 */
QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
    border: none;
}

QComboBox::down-arrow:disabled {
    image: none;
}

/* 弹出列表：容器窗口已被 ChevronComboBox 改成无边框 + 半透明（见 showPopup），
   这里的圆角才真正可见。margin 给圆角留出呼吸空间——半透明的四角透出底色，
   不会出现"圆角贴着直角窗边"的裁切感。item 自带圆角 + 悬停底色，选中态跟随。 */
QComboBox QAbstractItemView {
    background: @surface@;
    border: 1px solid @line@;
    border-radius: 12px;
    padding: 6px;
    /* ⚠️ margin 必须是 0：弹层容器窗口是**半透明**的（见 ChevronComboBox.showPopup），
       留了外边距的话，那一圈会把**背后的界面**透出来 —— 弹层压在卡片上时看着就是
       "里面还套了一层框"（用户 2026-10-15 截图点名的编码弹层）。
       圆角不靠外边距让位：容器是 WA_TranslucentBackground，圆角外的像素本来就是
       透明的，贴到窗口边也不会被裁。 */
    margin: 0;
    color: @ink@;
    outline: none;
    /* ⚠️ 这里**不要**写 selection-background-color / selection-color：
       它们会让选中态走样式引擎的"原始矩形"（方角、顶到边），::item 的
       border-radius / margin 全部被绕过 —— 用户点名"难看的矩形高亮"就是它。
       选中底色交给下面的 ::item:selected 画（实测：去掉之后圆角块生效）。*/
}

/* ⚠️ 这里**曾经**有 QComboBox QAbstractItemView::item / :hover / :selected
   三条规则（圆角 + 内边距 + 选中底色）。2026-10-15 逐项验证后确认：在 Fusion 上
   它们**完全不生效** —— 选中块始终由样式引擎画成"方角、顶到两边"的矩形（用户
   截图点名的丑东西），试过 9 个开关（resizeMode / sizeAdjustPolicy /
   uniformItemSizes / 视图级样式表…）都不动。条目绘制已改由
   ui_controls._PopupItemDelegate 接管，这里不再留无效规则。 */

QSpinBox::up-button, QSpinBox::down-button {
    background: transparent;
    border: none;
    width: 22px;
}

/* 同 QComboBox：原生箭头关掉，改由 ChevronSpinBox 自绘 chevron */
QSpinBox::up-arrow, QSpinBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
    border: none;
}

/* 滑杆（设置面板的毛玻璃强度）不再走 QSS：QSS 的子控件定位是隐式的，
   真机（2x 缩放）实测 groove 被默认定位缩进了 ~17px 而滑块的滑动范围没有 ——
   强度在两端时圆点整个悬在轨道外面（用户报"滑轨显示异常"）。
   现在由 ui_controls.GlassSlider 完全自绘，几何自己算，这里不再写规则。 */

/* ---- 次级按钮：主按钮是自绘的 InkButton，不走这套 ----
   这里**不能**写 min-height：QSS 的 min-height 会直接覆盖控件的
   setMinimumSize，而 IconButton 是用 setFixedSize 定尺寸的 ——
   一旦被覆盖，图标按钮就只剩 sizeHint 那么高（实测 20px），悬停浮出的圆角块
   跟着被压扁。高度交给 padding 撑（7px 上下 + 一行文字 ≈ 36px），
   自绘的那两个类则各自把 padding 清零、由自己的尺寸说了算。 */
QPushButton {
    background: @surface_2@;
    border: 1px solid @line@;
    border-radius: @CONTROL_RADIUS@px;
    padding: 7px 16px;
    font-weight: 500;
    color: @ink@;
}

QPushButton:hover {
    background: @surface_hover@;
    border-color: @line_strong@;
}

QPushButton:pressed {
    background: rgba(@sel_rgb@,0.10);
}

QPushButton:disabled {
    color: @ink_disabled@;
    border-color: @line@;
    background: transparent;
}

/* 自绘的主按钮整块绕开上面这套，否则会在自绘圆角外再描一圈轮廓 */
QPushButton#inkButton {
    background: transparent;
    border: none;
    padding: 0;
}

/* 图标按钮同理，而且是**必须**绕开 —— 不只是描边的问题：
   上面那条 QPushButton 的 padding(7px 上下) 会把内容最小高度顶到 36px，
   而 IconButton 是用 setFixedSize 定尺寸的（30、28、24 各不相同）。
   漏掉这一条时每个图标按钮都长成"宽 30、高 36"的竖长条：悬停浮出的圆角块
   被拉长，摆进底栏时还会跟相邻文字错开 4px。清零 padding 与描边之后，
   setFixedSize 才真正说了算 —— 所以这里也**不能**写 min-height/min-width，
   写了反而会把 setFixedSize 的最小值覆盖成 0。 */
QPushButton#iconButton {
    background: transparent;
    border: none;
    padding: 0;
}

/* ---- 滚动条：极细，悬停才显出来 ---- */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 3px 2px;
}

QScrollBar::handle:vertical {
    background: rgba(@ink3_rgb@,0.38);
    border-radius: 3px;
    min-height: 36px;
}

QScrollBar::handle:vertical:hover {
    background: rgba(@ink3_rgb@,0.70);
}

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px 3px;
}

QScrollBar::handle:horizontal {
    background: rgba(@ink3_rgb@,0.38);
    border-radius: 3px;
    min-width: 36px;
}

QScrollBar::handle:horizontal:hover {
    background: rgba(@ink3_rgb@,0.70);
}

QScrollBar::add-line, QScrollBar::sub-line {
    width: 0; height: 0; background: transparent; border: none;
}

QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}

/* ---- 分割条：不画线，只留一条悬停才亮的提示 ---- */
QSplitter::handle {
    background: transparent;
}

QSplitter::handle:vertical {
    height: 10px;
}

QSplitter::handle:vertical:hover {
    background: rgba(@ink3_rgb@,0.28);
    border-radius: 5px;
}

/* ---- 底部信息条：取代 QStatusBar。
   不用 QStatusBar 是因为 showMessage 的临时提示会把普通控件隐藏掉，
   左下角的设置按钮会跟着一起消失。自己排一行就没这个问题。
   **不铺底色**：页面已经是通篇一个颜色，再给这条铺一层半透明面色，窗口底下就会
   横着一条比页面亮一档的色带（用户："这个位置不要有背景颜色，太突兀了"）。
   只留一条上边框当分隔线。 ---- */
QWidget#bottomBar {
    background: transparent;
    border-top: 1px solid @line@;
}

QWidget#bottomBar QLabel {
    /* 状态栏是**常读**信息（连接态、收发字节数），原来用最弱的 ink_3(173)
       显得发灰 —— 用户点名"底栏统计变暗淡"就是这里。提到 ink_2(214) + 500 字重。 */
    font-weight: 500;
    color: @ink_2@;
}

QDialog {
    background: @surface@;
}

/* 设置面板里的分组小标题（"接收区""发送区"这类） */
QLabel#groupCaption {
    font-weight: 600;      /* 分组小标题：苹果式的"提一级"靠字重 */
    color: @ink_2@;
}

/* 设置面板里按钮开关那段是放在滚动区里的。滚动区默认会按调色板的 Base
   铺一层底，在对话框自己的 surface 上会显出一块色差 —— 全部设成透明。 */
QScrollArea {
    background: transparent;
    border: none;
}

QScrollArea > QWidget > QWidget {
    background: transparent;
}

QMessageBox {
    background: @surface@;
}

QMessageBox QLabel {
    color: @ink@;
}
"""


def stylesheet() -> str:
    tokens = {
        "@bg@": token("bg"), "@surface@": token("surface"),
        "@surface_2@": token("surface_2"), "@surface_hover@": token("surface_hover"),
        "@ink@": token("ink"), "@ink_2@": token("ink_2"), "@ink_3@": token("ink_3"),
        "@ink_disabled@": token("ink_disabled"),
        "@line@": token("line"), "@line_strong@": token("line_strong"),
        "@fill_primary@": token("fill_primary"),
        "@CONTROL_RADIUS@": str(CONTROL_RADIUS),
        "@CARD_RADIUS@": str(CARD_RADIUS),
        "@sel_rgb@": rgb("sel"), "@ink3_rgb@": rgb("ink_3"),
        "@editor@": editor_rgba(),
    }
    qss = _QSS
    for name, value in tokens.items():
        qss = qss.replace(name, value)
    return qss


def apply_scheme(scheme: str) -> None:
    """设主题：换 token、重刷样式表、同步原生配色（菜单/提示框/标题栏）。"""
    from PySide6.QtGui import QGuiApplication, QPalette

    set_scheme(scheme)
    app = QGuiApplication.instance()
    if app is None:
        return
    app.setStyleSheet(stylesheet())
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, qcolor("bg"))
    palette.setColor(QPalette.ColorRole.Base, qcolor("surface"))
    palette.setColor(QPalette.ColorRole.AlternateBase, qcolor("surface_2"))
    palette.setColor(QPalette.ColorRole.Text, qcolor("ink"))
    palette.setColor(QPalette.ColorRole.WindowText, qcolor("ink"))
    palette.setColor(QPalette.ColorRole.ButtonText, qcolor("ink"))
    palette.setColor(QPalette.ColorRole.Button, qcolor("surface_2"))
    # 占位提示（"收到的数据会显示在这里"）在**接收区最大化**之后就是屏幕上唯一
    # 的文字，用 Qt 默认的灰色显得整屏发灰（用户连续多轮反馈）。
    # 提亮到 ink/ink_2 的中间调 #C8CCD1：仍然是"未输入"的层级，但不再是灰蒙蒙一片。
    palette.setColor(QPalette.ColorRole.PlaceholderText,
                     mix(qcolor("ink"), qcolor("ink_2"), 0.5))
    palette.setColor(QPalette.ColorRole.Highlight, qcolor("sel"))
    palette.setColor(QPalette.ColorRole.HighlightedText, qcolor("on_sel"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, qcolor("surface"))
    palette.setColor(QPalette.ColorRole.ToolTipText, qcolor("ink"))
    app.setPalette(palette)

# ---------------------------------------------------------------- 系统标题栏主题

def apply_titlebar_theme(window, scheme: str, rounded: bool = True) -> None:
    """让系统标题栏跟着主题走、窗口用圆角。非 Windows 或旧系统失败就静默跳过。

    主窗口和设置对话框都要调（对话框原来没调，深色主题下标题栏还是白的，
    和整个面板撞色）。非 QMainWindow 也一样适用 —— 只用 winId。

    ``rounded=False`` 时把窗口角设成 **方角**（``DWMWCP_DONOTROUND``）。主窗口
    **全屏时必须传 False**：圆角是在窗口内侧切掉四块，全屏窗口四角一圆就露出
    桌面 —— 这正是用户报的"改了设置之后四周出现缝隙"（换主题 → 重走本函数 →
    圆角被重新打开 → 四角漏桌面）。
    """
    import sys as _sys
    if _sys.platform != "win32":
        return
    try:
        import ctypes

        # ⚠️ 必须用窗口**自己的**句柄，绝不能沿父链上溯（曾经用
        # ``GetParent(winId()) or winId()``）—— 设置面板是主窗口的模态子窗口，
        # 它的 GetParent 恰好就是**主窗口**。于是"给对话框设标题栏主题"这一步
        # 顺手把主窗口的圆角重新打开成圆角：主窗口此时全屏，四角当场被切掉、
        # 露出桌面 —— 用户报的"点完设置保存退出后最大化界面四周有缝隙"根因
        # 就是它（只在开过设置后出现，且与换不换主题无关）。
        # Qt 的 winId() 本身就是顶层 HWND（无边框主窗口也一样），直接用。
        hwnd = int(window.winId())
        dark = ctypes.c_int(1 if scheme == "dark" else 0)
        # 20 是 Win11 的属性号，19 是 Win10 1809+ 的，两个都试，失败无副作用
        for attribute in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(dark), ctypes.sizeof(dark))
        # DWMWCP_ROUND=2 / DWMWCP_DONOTROUND=1
        corner = ctypes.c_int(2 if rounded else 1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
    except Exception:
        pass
