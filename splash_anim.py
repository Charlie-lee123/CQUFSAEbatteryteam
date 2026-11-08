"""开屏动画接口层。

资源约定
--------
- **预设**放在 ``assets/splash/``：视频（``.mp4/.mov/.mkv/.webm/.avi``）或
  图片 / 动图（``.gif/.webp/.png/.jpg/.jpeg/.bmp``）。文件名里带 ``light`` /
  ``dark`` 的会自动归到对应主题 —— 用户选「跟随主题」时按当前主题挑。
- 用户在设置面板「开屏动画」里选：**无 / 跟随主题 / 某个预设 / 自定义文件**。

对外 API（给 main.py 与设置面板用，别在别处直接读 QSettings）
--------------------------------------------------------------
- ``presets()``            -> list[dict]  ``{id,label,path,kind,theme}``
- ``choice()``             -> str         ``none | theme | preset:<文件名> | custom``
- ``custom_path()``        -> str
- ``save_choice(v)`` / ``save_custom_path(p)``
- ``configured()``         -> dict | None 按当前配置解析出要播的那一条
- ``play(preset, on_finished=None)`` -> SplashPlayer | None
- ``play_configured(on_finished=None)`` -> SplashPlayer | None

设计原则
--------
- **绝不阻塞启动**：缺 QtMultimedia、文件坏了、解码失败 → 返回 None，主界面照常出现。
- 播放器是独立置顶窗口（无边框 / 整屏 / 界面底色），主窗在它后面准备好；动画结束
  用 300ms **内容淡出**：窗口置 WA_TranslucentBackground，paintEvent 把背景与 logo
  一起淡到透明（高斯模糊交叉溶入），最后同步画出全透明帧再撤场，无缝露出主界面。
  ⛔ 不要恢复 `setWindowOpacity(1-k)` —— frameless 窗 opacity→0 撤场会触发 Windows
  DWM 闪一下（2026-11-06 用户「最后不要闪一下」）；⛔ 也不要把开屏 reparent 成主窗
  子控件（每帧重画整屏 3200×2000 ×24 次，实测闪+卡，od25 已整体回退）。
  点一下 / 按任意键可跳过；另有 8s 兜底。
- 播放器对象由模块级 ``_active`` 持有（顶层窗口没有 Python 引用会被 GC 掉）。
"""
from __future__ import annotations

import concurrent.futures
import gc
import os
import re
import time
from pathlib import Path

try:                                  # ⛔ cv2 已从打包里砍掉（exe -41.5MB，冷启动
    import cv2                        #    快 1s 的主因）：只有帧序列路径（planA
except Exception:                     #    回退）需要它，planB 走 splash_motion 的
    cv2 = None                        #    numpy 高斯。
import numpy as np

from PySide6.QtCore import (QElapsedTimer, QPoint, QPointF, QRect, QRectF,
                            QSettings, Qt, QTimer, QUrl)
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QMovie, QPalette,
                         QPainter, QTransform)
from PySide6.QtWidgets import QLabel, QWidget

import app_log

# ---- SPLASH_AUDIT 启动计时（仅 SPLASH_AUDIT=1 时生效）---------------------
_AUDIT_ON = os.environ.get("SPLASH_AUDIT") == "1"
_AUDIT_T0 = float(os.environ.get("SPLASH_AUDIT_T0", time.perf_counter()))


def _aud(tag: str) -> None:
    if _AUDIT_ON:
        import sys
        print(f"SPLASH_AUDIT {tag} "
              f"{(time.perf_counter() - _AUDIT_T0) * 1000:.1f}",
              file=sys.stderr, flush=True)


_ORG, _APP = "MimoWorks", "SerialAssistant"
_KEY_CHOICE = "custom/splash_anim"
_KEY_PATH = "custom/splash_custom_path"
_DEFAULT_CHOICE = "theme"

_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
_IMAGE_EXTS = {".gif", ".webp", ".png", ".jpg", ".jpeg", ".bmp"}
_MOVIE_EXTS = {".gif", ".webp"}

_MAX_MS = 8000          # 兜底：最长播 8 秒，绝不把启动卡死
_FADE_MS = 300          # 淡出 0.3s（用户 2026-11-04 从 0.36 压缩）
_STILL_MS = 1600        # 静态图停多久（没有"结束"事件）
_POSTER_MS = 500        # 海报截在这一帧；视频播放头追到这儿才露面（跳过开头黑场）
# 帧序列并行预解窗口深度（帧）：≈0.4s @60fps 的缓冲，吸收 worker 解码抖动与
# 主窗构造期的 CPU 争用；配合 _show_frame 的「非阻塞保持上一帧」策略，彻底消除
# 主线程同步解码造成的确定性卡顿（用户 2026-11-03：「中间有明显卡顿，100%复现
# 在文字刚好要显露」）。内存峰值 ≈ 窗口深度 × 单帧(屏物理尺寸) + 预热缓存，
# 开屏期 4s 可接受（用户明确「不要压画质和帧率」，优先顺滑）。
_PREDEC_WINDOW = 24
# 允许"按播放时钟连跳"的最大帧数；超过就**吸收**（把多出来的时长记进
# _pause_total，动画整体顺延）而不是跳帧 —— 匀速左移的段落里连跳 3 帧以上，
# 眼睛看到的就是"卡"。见 _frame_step 的停顿吸收段。
# ⚠️ 这个值实测调过：改成 1 反而更差（吸收触发太频繁，每次吸收都会让那一帧
#    晚 1~2ms 上屏，16.7ms 的节拍网格被打散 —— 实测擦入段中位 17.2/19.5ms、
#    最大 23.6/26.3ms）；2 能吸收真正的停顿（>33ms）又不动正常节拍
#    （中位 16.6~16.8ms）。别再往下调。
_MAX_SKIP = 2
_active: "_SplashWindow | None" = None


# ------------------------------------------------------------------ 预设 / 配置

def _splash_dir() -> Path:
    # 打包后 __file__ 在 _MEIPASS 里，assets 就在旁边
    return Path(__file__).resolve().parent / "assets" / "splash"


def presets() -> list[dict]:
    """扫描 ``assets/splash/``，返回可选的预设（按文件名排序）。"""
    folder = _splash_dir()
    if not folder.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        # 首帧海报（xxx.poster.png）是给加载期兜底用的，不是可选动画
        if ".poster." in path.name.lower():
            continue
        ext = path.suffix.lower()
        if ext not in _VIDEO_EXTS and ext not in _IMAGE_EXTS:
            continue
        name = path.stem
        low = name.lower()
        theme = "dark" if "dark" in low else ("light" if "light" in low else "")
        label = name.replace("_", " ").replace("-", " ").strip() or path.name
        # ⛔ 2026-11-03 用户：「不要压画质和帧率」—— 预设一律用**原片**，
        #    不做任何降分辨率/降帧率转码（曾经转过 720p30，已删）。
        out.append({
            "id": f"preset:{path.name}",
            "label": label,
            "path": str(path),
            "kind": "video" if ext in _VIDEO_EXTS else "image",
            "theme": theme,
        })
    return out


_DROP_PLAN_TOKENS = {"splash", "dark", "light", "4k", "4k60", "2160p",
                     "1080p", "720p", "480p", "60", "30", "120", "fps"}


def _plan_key(preset: dict) -> str:
    """方案的稳定 key：去掉主题 / 分辨率 / 帧率 token 之后剩下的词。

    ``splash_dark_4k60`` → ``""``（默认方案 = planA）
    ``splash_dark_planb_4k60`` → ``planb``（planB）
    """
    stem = Path(str(preset.get("path", ""))).stem.lower()
    tokens = [t for t in re.split(r"[_\-\s]+", stem) if t]
    kept = [t for t in tokens if t not in _DROP_PLAN_TOKENS]
    return "_".join(kept)


def _plan_label(key: str) -> str:
    low = key.lower()
    if not low or low in ("a", "plana"):
        return "planA"
    if low in ("b", "planb"):
        return "planB"
    return key.replace("planb", "planB").replace("plana", "planA")


def plans() -> list[dict]:
    """按**方案**分组（同一方案里的浅色/深色是变体，自动按主题挑）。

    用户 2026-11-03：「只保留 planA、planB 这些选项，系统自动选择当前主题相同
    的动画」—— 下拉里不再列单个文件，只列方案。
    """
    groups: dict[str, dict] = {}
    for preset in presets():
        key = _plan_key(preset)
        group = groups.setdefault(
            key, {"id": f"plan:{key or 'a'}",
                  "label": _plan_label(key), "variants": []})
        group["variants"].append(preset)
    return sorted(groups.values(),
                  key=lambda g: (g["label"] != "planA", g["label"]))


def _pick_variant(variants: list[dict], scheme: str | None = None) -> dict | None:
    if not variants:
        return None
    if scheme is None:
        try:
            from ui_theme import current_scheme
            scheme = current_scheme()
        except Exception:                     # noqa: BLE001
            scheme = "dark"
    for preset in variants:
        if preset["theme"] == scheme:
            return preset
    for preset in variants:
        if preset["theme"] == "dark":
            return preset
    return variants[0]


def _settings() -> QSettings:
    return QSettings(_ORG, _APP)


def _warm_font_on_main() -> None:
    """在**主线程**把 Qt 字体引擎的冷启动付掉（动画时钟启动前调用）。

    ⛔⛔ 字体引擎是**按线程**缓存的：main.py 里 `_prewarm_font_engine` 那根后台
    线程只替自己付了钱，主线程首次 `QFontMetrics(...).height()` 实测仍要
    **273ms**（裸进程首次 706ms、同线程第二次 0.0ms）。这笔钱原本落在主窗
    构造里第一次量下拉框墨迹时 → 正好砸在开屏动画第 0.2~0.5s
    （`splash_perf.log` 的 big_gaps `(0.2, 273)` 实锤），用户感受就是
    「帧率不高/顿一下」。
    这里在 `show()+repaint()`（静止首帧已上屏）之后、`_elapsed.start()` 之前
    一次性付掉：不增加双击响应时间，也不占任何一帧动画。
    """
    try:
        from PySide6.QtGui import QFontMetrics, QImage, QPainter

        import ui_theme
        _f = ui_theme.ui_font(12.5)
        QFontMetrics(_f).height()          # ← 字体库/度量冷启动在这里付掉
        # 顺带把字形栅格化路径也走通（下拉框自绘 drawText 用的是这条）
        img = QImage(96, 28, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        _p = QPainter(img)
        _p.setFont(_f)
        _p.drawText(img.rect(), 0x0084, "8")
        _p.drawText(img.rect(), 0x0084, "▾")
        _p.end()
        # ⛔⛔ 另一个一次性的 295ms：**图标字体**（Segoe Fluent Icons /
        #    Segoe MDL2 Assets）首次加载 + 码位映射。主窗里第一次用到它的是
        #    下拉框自绘 chevron（`_draw_chevron` → `icon_font(px)` →
        #    `drawText("\uE70D")`），实测正好砸在开屏第 0.2s。这里先把这套
        #    字体和这个码位走通一次。
        _if = ui_theme.icon_font(11)
        QFontMetrics(_if).height()
        _p = QPainter(img)
        _p.setFont(_if)
        _p.drawText(img.rect(), 0x0084, "\uE70D")
        _p.drawText(img.rect(), 0x0084, "\uE70E")
        _p.end()
        # ⛔⛔ 关键：**真控件的第一帧绘制**才是那 272ms 的大头（裸进程实测：
        #    首次 `QComboBox.grab()` 623ms、第二次 0.2ms；度量与 drawText 都
        #    替不了它）。主窗构造里第一次触发它的是 `cmb_baud.setFont(num_font)`
        #    → 正好砸在动画第 0.2s。这里用一个一次性控件把这条路径走通：
        #    不设 parent、用完立刻 deleteLater，不进入任何对象树。
        from PySide6.QtWidgets import QComboBox, QLineEdit

        for _w in (QComboBox(), QLineEdit()):
            _w.setFont(ui_theme.num_font(11.5))
            _w.resize(140, 30)
            _w.grab()
            _w.deleteLater()
    except Exception:                      # noqa: BLE001 - 预热失败不挡开屏
        pass


def choice() -> str:
    value = str(_settings().value(_KEY_CHOICE, _DEFAULT_CHOICE) or _DEFAULT_CHOICE)
    # 旧配置存的是单个文件（preset:xxx）→ 归一成它所属的方案，老用户不用重选
    if value.startswith("preset:"):
        for preset in presets():
            if preset["id"] == value:
                key = _plan_key(preset)
                return f"plan:{key or 'a'}"
    known = {"none", "theme", "custom"} | {g["id"] for g in plans()}
    return value if value in known else _DEFAULT_CHOICE


def custom_path() -> str:
    return str(_settings().value(_KEY_PATH, "") or "")


def save_choice(value: str) -> None:
    _settings().setValue(_KEY_CHOICE, str(value))


def save_custom_path(path: str) -> None:
    _settings().setValue(_KEY_PATH, str(path))


def _make_preset(path: str, ident: str) -> dict | None:
    p = Path(path)
    if not p.is_file():
        return None
    ext = p.suffix.lower()
    if ext not in _VIDEO_EXTS and ext not in _IMAGE_EXTS:
        return None
    return {"id": ident, "label": p.stem, "path": str(p),
            "kind": "video" if ext in _VIDEO_EXTS else "image", "theme": ""}


def configured(scheme: str | None = None) -> dict | None:
    """按当前配置解析出要播的那一条；``None`` = 不播开屏。

    选的是**方案**（planA/planB）：在同方案的浅色/深色变体里按主题挑，
    没有同主题的就退深色、再退第一条。``scheme`` 可显式指定（预览时用下拉里
    选的主题，而不是全局当前主题）。
    """
    value = choice()
    if value == "none":
        return None
    if value == "custom":
        return _make_preset(custom_path(), "custom")
    groups = plans()
    if not groups:
        return None
    if value == "theme":                 # 默认 = 第一个方案（planA）
        return _pick_variant(groups[0]["variants"], scheme)
    for group in groups:
        if group["id"] == value:
            return _pick_variant(group["variants"], scheme)
    return None


def _motion_wanted_preset(preset: dict | str | None) -> bool:
    """这条配置是否走**原生几何动画**（内置 planA / planB 方案）。

    od46 起 planA 也是原生几何（与 planB 同源，见 splash_motion.SPEC_PLANA /
    SPEC_PLANB）。判定核心是「**内置方案**」，显式排除 custom / 用户自定义文件：

      * ``plan:``（planA）/ ``plan:planb`` / ``plan:a`` 等内置方案键 → True；
      * ``theme``（跟随主题，解析后仍是内置方案）→ True；
      * ``configured()`` 返回的真实文件 preset / 旧 QSettings 残留的
        ``preset:xxx`` → path 必须落在 ``assets/splash/`` 内置目录，且
        ``_plan_key()`` 为 ``""``(planA) / ``"planb"``(planB) → True；
      * ``custom`` / ``none`` / 内置目录之外的任意路径 → False（继续走
        QMediaPlayer / 静态图，绝不误伤）。

    接收 ``dict``（preset / configured 结果）或 ``str``（choice 值），后者方便
    离屏探针直接判 ``plan:a`` / ``plan:planb`` / ``theme`` / ``custom`` 四类。
    """
    try:
        p = {"id": preset} if isinstance(preset, str) else (preset or {})
        cid = str(p.get("id", "")).lower()
        # ⛔ 显式排除自定义文件 / 关闭
        if cid in ("custom", "none"):
            return False
        # 内置方案键 / 跟随主题
        if cid == "theme" or cid.startswith("plan:"):
            return True
        # 真实文件 preset：path 必须来自 assets/splash/ 磁盘扫描
        raw = str(p.get("path", ""))
        if not raw:
            return False
        path = os.path.normcase(os.path.abspath(raw))
        root = os.path.normcase(os.path.abspath(str(_splash_dir())))
        if not path.startswith(root + os.sep):
            return False                     # 用户在别处选的文件
        return _plan_key({"path": raw}) in ("", "planb")
    except Exception:                         # noqa: BLE001
        return False


# ------------------------------------------------------------------ 播放器

class _SplashWindow(QWidget):
    """置顶整屏黑底播放器：视频 / 动图 / 静态图。"""

    def __init__(self, preset: dict, on_finished=None) -> None:
        super().__init__(None)
        self._preset = preset
        self._on_finished = on_finished
        self._on_fade_start = None
        self._player = None
        self._audio = None
        self._movie = None
        self._video = None
        self._pix: QImage | None = None
        self._pix_buf = None               # 保活 _pix 背后的 numpy buffer
        self._land_box = None              # Plan B 机型无关落点盒（帧空间 [3200,2000] 归一化）
        self._logo_win = None              # 真实主窗口（取真实 UI logo 盒用）
        self._logo_rect_cache = None       # 真实 logo 盒（开屏窗口本地逻辑坐标），算一次缓存
        self._clock_paused = False         # 播放时钟冻结（主窗构造期间）
        self._pause_mark = 0
        self._pause_total = 0
        # 帧序列并行预解码（ThreadPoolExecutor，见 _fr_begin）
        self._dec_pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._dec_futs: dict = {}          # idx -> Future[QImage]
        self._dec_want = 0                 # 主线程已消费到的下一帧
        self._pix_size: tuple | None = None  # worker 缩放的目标物理尺寸
        self._done = False
        self._video_live = False      # 视频层是否已接管显示（接管后不再画海报）
        # 帧序列播放状态（见 _start_frames）
        self._fr: dict | None = None
        self._fr_idx = -1
        self._fr_skip = 0
        self._fr_paths: list = []
        self._elapsed = None
        self._frame_timer: QTimer | None = None
        try:
            from ui_theme import current_scheme
            light = current_scheme() == "light"
        except Exception:                 # noqa: BLE001
            light = False
        # 加载期兜底色：**用界面底色，不用纯黑**（用户 2026-11-03「不要黑屏」）。
        # ⛔ 2026-11-04「要完美重合」：浅色必须是 ui_theme 的 #FFFFFF，不能是
        #    #f4f4f6 —— 交棒淡出时开屏层与主窗页面底若有 11 色阶差，整屏会
        #    泛一层灰，logo 再准也看着"没贴上"。
        self._bg = QColor("#ffffff" if light else "#0d0d0f")
        # autoFillBackground 的底色也钉成同一色（paintEvent 之前/之间的填充不留杂色）
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, self._bg)
        self.setPalette(pal)
        self._closed = False
        self._fade_k = 0.0                    # 淡出进度（paintEvent 非 motion 路径用）
        self._fade_timer: QTimer | None = None
        # ⛔ 淡出按 **QElapsedTimer 墙钟**计时（不靠定时器拍数假累积）；
        #    _fade_close_pending 保证 k=1 的全透明帧只排一次撤场。
        self._fade_elapsed: QElapsedTimer | None = None
        self._fade_close_pending = False
        self._perf = None                  # 真机帧间隔遥测（仅帧序列路径）
        self._gc_held = False              # _fr_begin 里 gc.disable()，finish 恢复
        # 原生几何动画（见 splash_motion）：不走帧序列时的绘制器 / 进度（秒）
        self._motion = None
        self._motion_timer: QTimer | None = None
        self._motion_total = 3.0
        self._fade_ms = _FADE_MS     # 淡出时长（ms）；_start_motion 按所选 MotionSpec 覆盖（planA=400）
        self._mt = 0.0
        # 主窗构造触发点 = 文字擦满那一刻（spec.t_text_end×1000）；
        # _start_motion() 会用所选 MotionSpec 覆盖，这里只是安全初值。
        self._motion_hold_ms = 1900.0
        self._land_ready = False          # 主窗真实落点已注入（播完才放行淡出）

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        # ⛔ 2026-11-06 根治「开屏最后闪一下」：window 置 WA_TranslucentBackground，
        # 淡出只淡**内容**（bg 透明度 + logo 交叉溶到透明），窗口本身不再
        # setWindowOpacity → 0（frameless 窗在 opacity 撤场时会触发 Windows DWM
        # 闪一下）。内容全透明后 close() 即可无缝露出主窗。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        screen = QGuiApplication.primaryScreen()
        self.setGeometry(screen.geometry() if screen is not None else self.rect())

        self._max_timer = QTimer(self)
        self._max_timer.setSingleShot(True)
        self._max_timer.timeout.connect(self.finish)

    def set_on_finished(self, callback) -> None:
        """主窗还没建好时先挂起回调；已经播完关了就地补调一次。

        启动顺序是"先起开屏、主窗在后面初始化"，所以回调只能在主窗建好后挂上；
        短动画有可能先播完 —— 这时必须立刻补调，否则主窗永远不显示。
        """
        if self._closed:
            if callback is not None:
                callback()
            return
        self._on_finished = callback

    def set_on_fade_start(self, callback) -> None:
        """淡出**开始前**调：把主窗置前/激活，消除「开屏 topmost 表面已撤、
        主窗尚未置前」的 z-order/前台空窗期（B2 独立闪源，2026-11-06）。

        由 finish() 在 _fade_timer 启动**之前**同步调用：此刻开屏仍完全不透明
        且仍 topmost、用户看不见；300ms 淡出给 activateWindow() 留出跨过
        Windows 前台锁定的时间，到 _close() 撤场时主窗已是前台。
        ⚠️ activateWindow() 管前台、topmost 管层级，互不相干（真机 A/B 见
        devtools/_probe_b2_early_activate.py）；本回调**不负责**恢复主窗重绘
        —— _reveal_main(sync=True) 已同步画好首帧，且 main.py 明确禁用
        setUpdatesEnabled(False)，旧 docstring 的「恢复重绘」是空语义。
        """
        if self._done:
            if callback is not None:
                callback()
            return
        self._on_fade_start = callback

    def set_logo_window(self, win) -> None:
        """把真实主窗口交给播放器：收尾段按真实 UI logo 位置做纠正贴合用。

        必须在主窗构造出来之后调用（main._build_window 里 MainWindow() 之后）。
        真实 logo 盒延迟到第一次需要时才量，避免主窗还没就位就取几何。
        """
        self._logo_win = win
        self._logo_rect_cache = None

    # ---- 生命周期
    def _load_poster(self) -> None:
        """首帧海报（xxx.poster.png）：视频解码前先显示它，不露黑底。"""
        path = Path(str(self._preset.get("path", "")))
        poster = path.with_name(path.stem + ".poster.png")
        if not poster.is_file():
            return
        pix = QImage(str(poster))
        if not pix.isNull():
            self._pix = pix
            self.update()

    # ---- 原生几何动画（od46：planA / planB 都走）
    def _motion_wanted(self) -> bool:
        """这条预设是否走原生几何动画（而不是播 QMediaPlayer / 帧序列）。

        ⛔ 2026-11-04 用户定调：「不要把开屏动画看成视频，应该当作动画，完全由
        原生代码驱动，视频只是要实现的效果」。所以设计稿留在 assets 里，但播放
        路径完全不用它 —— 零解码、零预解窗口、零帧对齐，「尾部跳帧」这回事从
        根上不存在。

        ⛔ od46 起 **planA 也改成原生几何动画**（不再走帧序列），与 planB 同源
        （两套 MotionSpec 取值相同）。判定收敛到「内置方案 + plan key 为
        ""(planA)/"planb"(planB)」，**显式排除 custom 与用户自定义文件**，
        见模块级 ``_motion_wanted_preset()``。设计稿 mp4 / poster 文件名不动
        （presets()/plans()/choice() 依赖磁盘扫描）。
        """
        return _motion_wanted_preset(self._preset)

    def _start_motion(self) -> None:
        import splash_motion

        try:
            from ui_theme import current_scheme
            scheme = current_scheme()
        except Exception:                 # noqa: BLE001
            scheme = "dark"
        # od46：planA / planB 都走原生几何，按 plan key 选 spec（当前同源）。
        spec = (splash_motion.SPEC_PLANB
                if _plan_key(self._preset) == "planb"
                else splash_motion.SPEC_PLANA)
        # ⚠️ __init__ 里 setGeometry(screen.geometry()) 已执行，此刻宽高有效。
        # od46：档位按**所有屏幕里最大的逻辑尺寸**预留 —— set_land_from_window()
        # 遇到主窗在副屏时会把开屏 setGeometry 到副屏；若副屏更大，只按起播屏
        # 算的 _clear_w 就不够、会放大糊。播放期绝不重渲染，只能起播前留够。
        try:
            from PySide6.QtGui import QGuiApplication
            _screens = QGuiApplication.screens() or []
            _sh = max([float(self.height())]
                      + [float(sc.geometry().height()) for sc in _screens])
            _sw = max([float(self.width())]
                      + [float(sc.geometry().width()) for sc in _screens])
            # ⛔ od47d「画质与屏幕最高分辨率一致」：dpr 也要取所有屏最大值，
            #    副屏是高 DPI 时按起播屏 dpr 算会少一半物理像素。
            _dpr = max([float(self.devicePixelRatioF())]
                       + [float(sc.devicePixelRatio()) for sc in _screens])
        except Exception:                 # noqa: BLE001 - 取不到就用当前屏
            _sh, _sw = float(self.height()), float(self.width())
            _dpr = float(self.devicePixelRatioF())
        # ⛔⛔ od47d「双击后响应时间过长」：**先把窗口显示出来再预热**。
        #    paintEvent 在 _motion 为 None 时只画纯背景（与几何渲染器同色），
        #    所以 show()+repaint() 之后那 ~600ms 预热是在「用户已经看到开屏
        #    窗口」的情况下进行的，感知上的双击响应从 ~600ms 降到 ~50ms；
        #    动画时钟仍在预热完成后才 start，播放期不背这笔开销。
        self.setWindowOpacity(1.0)
        # ⛔ 2026-11-06 实验 A（真实窗口 grab 取像素）实证：
        #    WA_TranslucentBackground(True) + setAutoFillBackground(True) 会让
        #    Qt 在 paintEvent 之前用调色板 Window 色**不透明**地铺满 backing
        #    store，paint 里那层带 alpha 的底色根本不生效（alpha 恒 255）→
        #    窗口带着画面被 close = 用户「最后不要闪一下」。
        #    ⚠️ setPalette(:341-343) 保留（视频层/子控件仍要用），但 autoFill
        #    必须关掉：paintEvent 两条分支都自己 fillRect 覆盖全窗口（motion
        #    分支委托 _motion.paint → fillRect(0,0,w,h,带 alpha 底色)；非 motion
        #    分支 :1182 fillRect(self.rect(), ...))，不依赖 autoFill 兜底；
        #    show() 后还有同步 repaint() 画首帧。
        self.setAutoFillBackground(False)
        self.show()
        self.repaint()                    # 强制同步画出纯背景首帧
        _aud("splash_repaint")  # 开屏纯背景首帧已同步画出（用户首次可见）
        # ⛔⛔ od53「帧率不高」的真凶之一：主线程**自己的**字体引擎冷启动。
        #    实测同进程里后台 `_prewarm_font_engine` 线程跑完只共享了一部分，
        #    主线程首次 `QFontMetrics(...).height()` 仍要 **273ms**
        #    （裸进程实测首测 706ms、主线程二测 0.0ms —— 字体引擎是**按线程**
        #    缓存的，后台线程替不了主线程）。这笔开销原本在主窗构造时爆发，
        #    正好压在动画第 0.2~0.5s（splash_perf.log big_gaps 实锤 273ms）。
        #    这里在**动画时钟启动之前**、画面已经是静止首帧（用户看到的就是
        #    开屏底色）时一次性付掉：不增加双击响应（窗口已上屏），也不占
        #    任何一帧动画的时间。
        _warm_font_on_main()
        self._motion = splash_motion.MotionRenderer(
            scheme, _dpr, _sw, _sh, spec)
        _aud("motion_ready")  # 渲染器构造完（清晰 logo 就绪；模糊档后台预热）
        self._motion_spec = spec
        self._fade_ms = float(self._motion_spec.fade_ms)
        self._motion_total = float(spec.t_total)
        self._motion_hold_ms = float(spec.t_text_end) * 1000.0
        self._mt = 0.0
        # od46：clamp 累计 dt，构造期不冻结时钟。`wall_intervals` 是**原始墙钟
        # 间隔**（stutter/max/p95/eq_fps 基于它，否则 clamp 后 stutter 恒 0）；
        # `intervals` 是 clamp 后的进度增量 Δt（只作节拍均匀性证据，max ≤ DT_MAX）。
        self._perf = {"frames": 0, "intervals": [], "wall_intervals": [],
                      "held": 0, "skip": 0}
        self._last_wall = time.perf_counter()
        # 窗口已在预热前 show 过（见上），这里只需置前/取焦点，别再 show 一次
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._max_timer.start(_MAX_MS)
        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        self._pause_total = 0
        self._motion_timer = QTimer(self)
        # ⛔ 精确定时器（od46 真 bug 修复）：Windows 默认 CoarseTimer ≈15.6ms，
        #    5ms 间隔会被压到 ~60fps。对齐帧序列路径与 ui_anim 全局补间。
        self._motion_timer.setTimerType(Qt.TimerType.PreciseTimer)
        # 刷新率**跟设置走**：ui_anim.frame_hz() 是「设置 → 动画帧率」生效后的
        # 值。⚠️ 设置面板里的 120 是「跟随屏幕」的**哨兵值**（main.py 在
        # anim_fps==120 时替换成 display_max_hz()），本机 frame_hz()=165；
        # 真实帧率仍需靠 _write_perf_log 的 eq_fps 真机实测。
        # 唤醒间隔比目标周期再密 1ms，保证每个 vsync 都有新内容。
        try:
            from ui_anim import frame_hz
            hz = max(60, int(frame_hz()))
        except Exception:                 # noqa: BLE001 - 拿不到就跟 60
            hz = 60
        self._hz = hz
        # ⛔ od47d「帧率与设置一致」：165Hz 的目标周期 6.06ms。Windows 事件
        #    循环会把 QTimer 间隔整体拖 ~2~3ms：5ms→实测 6.9~7.4ms(136~146fps)、
        #    4ms→6.7~7.0ms(143~149fps)，都达不到设置值。收紧到 2ms 让每个
        #    vsync 都能拿到新内容；Qt 会合并连续 update()，不会真的画 500 帧。
        #    绘制本身是纯 drawPixmap/矢量，单帧 <1ms。
        self._motion_timer.setInterval(max(1, int(round(1000.0 / hz)) - 4))
        self._motion_timer.timeout.connect(self._motion_tick)
        self._motion_timer.start()

    def _motion_tick(self) -> None:
        """几何分支：**直接读墙钟**推进进度（不累加、不钳制）。

        ⛔⛔ od47 真 bug 修复（用户「请检查真实停留时间」）：od46 用
        ``dt = min(wall, 2.5/hz)`` 累加 ``_mt``，本意是构造期不冻结时钟，
        但主线程被占时 QTimer 根本不触发 —— 那 556ms 阻塞后第一个 tick
        只推进 15.15ms，**其余 541ms 被永久吞掉**。真机遥测实测
        ``frames=320 eq_fps=74.7`` → 开屏墙钟 4.28s，而设计只有 3.0+0.3s，
        多出的 ~1.3s 全表现为「画面定格不动」＝用户说的真实停留。
        长阻塞期间画面本来就画不出来（update 也不执行），clamp 只是把
        这段静止人为延长。改为读 QElapsedTimer 绝对值：阻塞结束即从
        「该到的时间点」继续，总时长严格 3.3s，且与 main.py 按墙钟排的
        ``still_delay_ms``（1.9s 构造主窗）重新对齐。
        ⚠️ 帧序列路径的 pause_clock/resume_clock 不受影响，只改几何分支。
        """
        if self._done or self._motion is None:
            return
        if _AUDIT_ON and not getattr(self, "_aud_ticked", False):
            self._aud_ticked = True
            _aud("first_tick")  # 开屏动画第一帧（时钟启动后）
        # 把后台预热的模糊档 QImage 在主线程转成 QPixmap 填入缓存（见
        # MotionRenderer._preheat_async）：不阻塞动画，模糊档逐帧补满。
        m = self._motion
        if getattr(m, "_preheat_pending", None):
            _done = m._absorb_pending()
            if _done and not getattr(self, "_aud_preheat_done", False):
                self._aud_preheat_done = True
                _aud("preheat_done")  # 后台模糊档全部就绪
        now = time.perf_counter()
        wall = now - self._last_wall
        self._last_wall = now
        prev_mt = self._mt
        elapsed = getattr(self, "_elapsed", None)
        if elapsed is not None:
            self._mt = (elapsed.elapsed()
                        - getattr(self, "_pause_total", 0)) / 1000.0
        else:                              # 兜底：拿不到时钟就按墙钟累加
            self._mt += wall
        dt = max(0.0, self._mt - prev_mt)
        # ⛔ od57：模糊档表**不能在淡入结束就释放**（原逻辑），因为 planA 的
        #    「整体高斯模糊淡出」要用同一套档表逐档糊开（0→最大档）。改为
        #    **收尾淡出走完**（finish() 的 _fade_ms 结束后，planA=400/planB=300）再释放，见 _close。
        #    档表约 244MB @1080p@200%，多留 ~1s 可以接受；_close 一定会放。
        perf = self._perf
        if perf is not None:
            perf["wall_intervals"].append(wall)
            perf["intervals"].append(dt)
            perf["frames"] = perf.get("frames", 0) + 1
            # 诊断遥测：>100ms 的长阻塞记下它发生在动画的哪一秒，
            # 才能判断压到的是空白段还是「向左上角落点」段。
            if wall > 0.100:
                perf.setdefault("big_gaps", []).append(
                    (round(prev_mt, 2), round(wall * 1000)))
        self.update()
        if self._mt >= self._motion_total:
            # ⛔ 播完**不立刻**淡出：画面停在落定帧等主窗构造完成。
            #    落定帧与主窗页头 logo 是同一个几何（set_land_from_window），
            #    静止看不出；中间任何位置切断动画都是更严重的问题。
            self._mt = self._motion_total
            self.update()
            if self._land_ready:
                self._motion_timer.stop()
                self.finish()

    def set_land_from_window(self, win) -> None:
        """把主窗页头 lockup 的真实几何注入动画当终点，然后放行淡出。

        od46：改用「屏幕全局逻辑坐标 → 开屏窗口本地逻辑坐标」的换算，除以
        **开屏窗口**宽高（od45 用主窗本地坐标 / 主窗宽高，普通窗启动、主窗在
        副屏、窄窗都会偏位）。全程 QRectF/QPointF 浮点，不 ``.toPoint()`` 截断
        （dpr=1.25/1.5 会丢 1px）。注入失败明确告警并**回退设计稿落点**。

        必须在主窗 show + 布局生效之后调用（``main._build_window`` 里
        ``_reveal_main(sync=True)`` 之后）。
        """
        if self._motion is None:
            return
        try:
            bh = win.brand_header
            # ⛔ 用 BrandHeader.ink_rect()（单一真值源 ui_brand.INK_FRAC），
            #    不要自己拿 lockup_rect() 再乘比例（实测会差 (16,10) 逻辑像素）。
            r = bh.ink_rect()                       # QRectF 浮点，真实墨迹盒
            if r.width() <= 1.0 or r.height() <= 1.0:
                raise ValueError("墨迹盒尚未就绪")
            # 病灶 A：apply_startup_state()（最大化/普通窗落地）后 Qt 布局/窗口几何
            #   有时尚未完全 settle，bh.mapToGlobal 可能拿到陈旧/未最终化的几何 →
            #   开屏落点与软件页头 logo 差几个逻辑像素 = 用户看到的「界限」。
            #   ⛔ 原注释写「让出一次事件循环」，现已改为**只推布局请求**（见下），
            #      不再让出整个事件循环 —— 统一进程的定时器会被一起跑掉。
            win.ensurePolished()
            # ⛔⛔ 只把**布局请求**推完，**不要** QApplication.processEvents()：
            #    后者会把挂起的定时器一起跑掉 —— 实测它把 `_build_fs_idle_pix`
            #    的整屏 BitBlt（187ms）拖进来，正好砸在开屏第 0.4s
            #    （splash_perf.log 的 big_gaps 实锤）。布局只需 LayoutRequest。
            lay = win.layout()
            if lay is not None:
                lay.activate()
            bh.updateGeometry()
            from PySide6.QtCore import QCoreApplication, QEvent
            QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
            # 副屏适配：主窗与开屏不在同一屏时，先把开屏挪到主窗所在屏再算。
            ws = win.screen()
            ss = self.screen()
            if ws is not None and (ss is None or ws is not ss):
                from PySide6.QtWidgets import QApplication
                self.setGeometry(ws.geometry())
                QApplication.processEvents()     # 让 setGeometry 生效再 mapFromGlobal
            # 全局逻辑坐标 → 开屏本地逻辑坐标（全程 QPointF 浮点，不 .toPoint()）
            g = bh.mapToGlobal(QPointF(r.x(), r.y()))   # QPointF
            local = self.mapFromGlobal(g)                # QPointF
            sw = float(self.width()) or 1.0
            sh = float(self.height()) or 1.0
            self._motion.set_land(local.x() / sw, local.y() / sh,
                                  r.height() / sh)
            app_log.info(
                f"[splash] 落点注入 x={local.x() / sw:.4f} "
                f"y={local.y() / sh:.4f} h={r.height() / sh:.4f}")
        except Exception as exc:          # noqa: BLE001
            app_log.warning(f"开屏落点注入失败，回退设计稿落点：{exc}")
        self._land_ready = True
        if self._mt >= self._motion_total:
            if self._motion_timer is not None:
                self._motion_timer.stop()
            self.finish()

    def start(self) -> None:
        if self._motion_wanted():
            self._start_motion()
            return
        is_video = self._preset.get("kind") == "video"
        # ⛔ 先算帧序列路径：帧序列路径下 frame_0 本身就是空白（开场渐入），
        #    **绝不用模糊 logo 海报当首帧**（用户 2026-11-03：「淡入之前应该是
        #    空白的」）。海报只在视频/静态图回退路径有意义（视频解码前先铺一张
        #    避免黑场），帧序列路径跳过 —— 首帧直接交 _start_frames 的 frame_0。
        fr = self._frames_for(str(self._preset.get("path", "")))
        if fr is None:
            self._load_poster()
        # ⛔ 立即以不透明显示：帧序列路径下此刻 _pix 仍是 None（paintEvent 用
        #    _bg 填空白），视频/静态图回退路径下海报已画好（非黑）。
        self.setWindowOpacity(1.0)
        # ⛔ 同 _start_motion：帧序列/视频回退路径也必须关掉 autoFill，否则
        #    WA_TranslucentBackground 的透明合成失效（实验 A：alpha 恒 255）。
        #    paintEvent 的非 motion 分支自己 fillRect(self.rect(), ...) 铺满全窗，
        #    show() 之后下方还有同步 repaint() 兜首帧，无需 autoFill。
        self.setAutoFillBackground(False)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._max_timer.start(_MAX_MS)
        # 优先帧序列：把 mp4 在打包时抽成图片帧，启动零解码预热 —— 第一帧即刻显示、
        # 之后按源帧率逐帧播放，绕开 QMediaPlayer 解 H264 约 1s 的预热（用户实测
        # 启动静止海报 1022ms）。缺帧时回退 QMediaPlayer，便于调试新丢进来的 mp4。
        if fr is not None:
            self._start_frames(fr)
        elif is_video:
            self._start_video()
        else:
            self._start_image()
        # ⛔⛔ 同步补一次绘制（2026-11-03 用户截图：开头有残影「没有被覆盖全」）。
        #    show() 只是把窗口挂上屏，绘制要等事件循环 —— 而 main() 的启动顺序是
        #    play_configured() 返回后**同步构造 MainWindow**（几百 ms，期间事件循环
        #    不跑），这段时间窗口在屏幕上没有内容，底下的旧画面直接透出来，就是
        #    用户截到的"残影"。repaint() 是同步绘制，返回时窗口已有第一帧内容，
        #    构造 MainWindow 的几百 ms 里屏幕上是海报/首帧，不再露底。
        self.repaint()

    # ---- 帧序列播放（默认路径，零解码预热、按刷新率丝滑） ----
    def _frames_for(self, path: str) -> dict | None:
        """帧序列路径（**已启用**：4K 帧 + ThreadPoolExecutor(4) 并行预解码）。

        2026-11-03 用户定指标「帧率≥60、画质≥2K 最好 4K」：
        - 单线程解码的物理上限：1440p 14.6ms/帧（54.7fps 顶格）、4K 33.7ms
          （30fps 都不到）——靠 splash_anim 的 **ThreadPoolExecutor(4) 并行
          预解码**消化（Qt 解码释放 GIL，真并行，产能 ~81fps > 60）；
        - worker 顺带缩放到窗口物理尺寸，主线程 paintEvent 只做 1:1 drawImage；
        - 帧由 devtools/extract_splash_frames.py 生成（2160p + still 字段版），
          重渲 mp4 后必须重跑它。
        """
        # ⛔ cv2 已从打包产物排除（spec excludes），且 *_frames/ 在 a.datas
        #    里被整体过滤 —— 帧序列路径在打包版不可达。缺 cv2 时直接返回
        #    None → start() 自动回退 QMediaPlayer / 静态图，**绝不**落到
        #    「帧帧解码失败、保持上一帧直到 8s 兜底」的死循环。
        if cv2 is None:
            return None
        p = Path(path)
        d = p.parent / f"{p.stem}_frames"
        m = d / "manifest.json"
        if not m.is_file():
            return None
        try:
            import json
            man = json.loads(m.read_text(encoding="utf-8"))
            count = int(man.get("count", 0))
            if count <= 0:
                return None
            lb = man.get("land_box")
            if isinstance(lb, (list, tuple)) and len(lb) == 4:
                lb = [float(x) for x in lb]
            else:
                lb = None
            return {"dir": d, "fps": int(man.get("fps", 60)),
                    "count": count, "ext": man.get("ext", "jpg"),
                    # Plan B 机型无关落点盒（帧空间 [3200,2000] 归一化）：
                    # 播放端运行时按真实窗口 + 真实 UI 墨迹盒算纠正相似变换贴合。
                    "land_box": lb,
                    # 视频里天然静止的段落（打包时逐帧分析写进 manifest）：
                    # 主窗构造的 ~1s 会藏在这段里，画面本来就不动，看不出顿。
                    "still_ms": int(man.get("still_at_ms", 0) or 0),
                    "still_len_ms": int(man.get("still_len_ms", 0) or 0)}
        except Exception:                     # noqa: BLE001
            return None

    def _start_frames(self, fr: dict) -> None:
        self._fr = fr
        self._fr_idx = -1
        # ⛔ 从 ``_POSTER_MS`` 处起播（2026-11-03 录屏实证）：视频开头 0~0.23s 是
        #    黑场、紧接着还是暗场，帧序列若从第 0 帧播，**开屏窗口一出现就是纯黑**
        #    （录屏逐帧：mean 18.2 纯黑静止 1.1s，用户："开屏之前停住了这么久"）。
        #    海报机制本来就是为跳过这段设计的（QMediaPlayer 路径等播放头 ≥
        #    _POSTER_MS 才露视频），帧序列路径必须同样跳过。
        # ⛔ 2026-11-03 用户：「开屏动画开始动前 0.3s 应该是空白的」——开头那段
        #    空白渐入是**原片设计的一部分**，必须忠实播放（曾从 500ms 起播跳过它，
        #    用户看到的是"直接开始还糊"）。fr_skip 归零 = 从第 0 帧忠实播放。
        self._fr_skip = 0
        # 视频天然静止段的起点（manifest 里预分析好的，见 _frames_for）
        self._fr_still_ms = int(fr.get("still_ms", 0) or 0)
        # Plan B 机型无关落点盒：播放收尾段按真实 UI logo 算纠正变换贴合
        self._land_box = fr.get("land_box")
        # ffmpeg 抽帧是 1 基（frame_00001.jpg … frame_{count:05d}.jpg），故用 i+1。
        # 帧序列全为同一扩展名（Plan B 不再烘焙无损真值帧），无需按帧号换扩展名。
        ext = fr["ext"]
        self._fr_paths = [
            fr["dir"] / f"frame_{i + 1:05d}.{ext}"
            for i in range(fr["count"])
        ]
        # 首帧立刻出（跳过黑场后的那一帧 = 海报内容），绝不空窗。
        # ⛔ 线程池必须在首帧 _show_frame **之前**建好（窗口已 show、尺寸有效）：
        #    _show_frame 末尾会往 _dec_pool 提交预解窗口，pool 为 None 直接
        #    AttributeError 崩（2026-11-03 用户截图实证：启动即崩
        #    "'NoneType' object has no attribute 'submit'"）。
        dpr = self.devicePixelRatioF() or 1.0
        self._pix_size = (max(1, int(round(self.width() * dpr))),
                          max(1, int(round(self.height() * dpr))))
        if self._dec_pool is None:
            self._dec_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=8, thread_name_prefix="splashdec")
        self._show_frame(self._fr_skip)
        self._elapsed = QElapsedTimer()
        self._frame_timer = QTimer(self)
        self._frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        # ⛔ **单发**定时器（2026-11-03 终修）：每次只排"下一帧的到期时刻"，
        #    见 ``_schedule_next``。旧实现是 ``start(8)`` 每 8ms 轮询一次，由
        #    ``int(t*fps)`` 决定要不要换帧 —— 16.67/8 = 2.08 不是整数比，同一段
        #    匀速左移的显示间隔会在 12ms / 24ms 之间来回摆（实测擦入段
        #    10.2~25.7ms，中位 16.2），眼睛看到的就是"一顿一顿"。
        self._frame_timer.setSingleShot(True)
        self._frame_timer.timeout.connect(self._frame_step)
        # ⛔ 计时**延后到事件循环真正跑起来**再起跑：main() 在 play_configured()
        #    返回后由 QTimer 定时构造主窗（构造的 ~937ms 冻结事件循环）——
        #    若此处就 _elapsed.start()，这段冻结会被算进播放进度，恢复时 idx
        #    直接跳过前 ~60 帧（"停住、然后突然开始"）。
        QTimer.singleShot(0, self._fr_begin)

    def _fr_begin(self) -> None:
        """事件循环真正启动后才开始计时、开始逐帧推进（见 _start_frames）。"""
        if self._done or self._elapsed is None or self._frame_timer is None:
            return
        # ⛔ 性能铁律（2026-11-03）：播放期 gc.disable()，避免每帧 ~25-33MB 的
        #    numpy/QImage 分配触发 100ms+ 自动 GC 停顿（真机「卡」的主因之一）。
        #    finish() 里 gc.enable() 恢复；_close 再兜底一次。
        gc.disable()
        self._gc_held = True
        # 真机帧间隔遥测：记录每帧显示墙钟间隔 + 主线程同步解码回退次数，
        # 写到 logs/splash_perf.log，供用户重测后回传、定位真机卡顿点。
        self._perf = {"frames": 0, "intervals": [], "held": 0, "skip": 0,
                      "last": None, "start": time.perf_counter()}
        self._elapsed.start()
        # ⛔ 并行预解码（2026-11-03 用户定指标：帧率≥60、画质 4K）：4K 单帧
        #    JPEG 解码 ~33.7ms，单线程连 30fps 都到不了。ThreadPoolExecutor(8)
        #    并行（cv2 的 C++ 解码释放 GIL = 真并行），8 线程下 4K 解码+cover
        #    缩放等效 ~12ms/帧（产能 83fps > 60）。worker 顺带把帧缩放到窗口
        #    物理尺寸（cover，Smooth），主线程 paintEvent 只做 1:1 drawImage（~1ms）。
        #    ⛔⛔ 预解窗口已由 _show_frame(0) 建好（_start_frames 里）：这里只补到
        #    窗口深度，**绝不 clear()** —— 旧代码 clear() 会取消已经飞在半空的
        #    1..24 帧解码，造成首段大片空白/卡顿。
        dpr = self.devicePixelRatioF() or 1.0
        self._pix_size = (max(1, int(round(self.width() * dpr))),
                          max(1, int(round(self.height() * dpr))))
        if self._dec_pool is None:
            self._dec_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=8, thread_name_prefix="splashdec")
        for j in range(self._fr_idx + 1,
                       min(self._fr_idx + 1 + _PREDEC_WINDOW, self._fr["count"])):
            if j not in self._dec_futs:
                self._dec_futs[j] = self._dec_pool.submit(self._decode_frame, j)
        # 排第一拍：从"下一帧的绝对到期时刻"起跑（见 _schedule_next）
        self._schedule_next()

    def _schedule_next(self) -> None:
        """把下一次唤醒直接排到**下一帧的到期时刻**（单发定时器，不轮询）。

        ⛔⛔ 2026-11-03 用户：「还是有卡顿，就是左移之后文字一点一点显露出来的时候
        发生的卡顿，就这一个位置非常明显。」探针
        （``devtools/_probe_splash_wipe.py``）逐帧实测：擦入段（idx 180~215）的
        显示间隔在 10.2~25.7ms 之间游走（中位 16.2ms、held=0、解码 36~53ms 完全
        跟得上），**不是掉帧也不是解码跟不上，而是帧节拍不均匀**。

        根因：旧实现 ``start(8)`` 每 8ms 轮询一次，由 ``int(t*fps)`` 决定换不换
        帧。16.67ms / 8ms = 2.08 —— 不是整数比，于是"这一拍该换了没"要隔 2 拍
        （16ms）还是 3 拍（24ms）完全取决于轮询相位；再叠加 paintEvent 每帧
        ~4ms 的阻塞（实测 avg 4.1ms），相位还会漂 → 间隔抖动 ±40%。匀速左移的
        画面上，这种抖动眼睛一抓就准（"一顿一顿"），而在渐变/虚化段会被掩盖，
        所以用户觉得"就这一个位置明显"。

        改成：算出下一帧的**绝对**到期毫秒数，用 ``start()`` 排一个单发定时器。
        每次唤醒都重新按当前墙钟算，误差不累积，只剩定时器分辨率（~1ms）。
        """
        if self._done or self._frame_timer is None or self._elapsed is None:
            return
        if self._clock_paused or self._fr is None:
            return
        fps = float(self._fr.get("fps", 60) or 60)
        cur_ms = float(self._elapsed.elapsed() - self._pause_total)
        # 下一帧 = 已显示帧 +1（hold 时 _fr_idx 没动 → 到期时刻已过 → 立刻重试）
        due_ms = (self._fr_idx + 1) / fps * 1000.0
        wait = due_ms - cur_ms
        if wait < 0.0:
            # 已经晚了（停顿/掉帧/hold 重试）：2ms 后回来，别空转也别睡过下一帧
            wait = 2.0
        self._frame_timer.start(max(1, int(wait)))

    def _decode_frame(self, idx: int) -> QImage | None:
        """worker：解码第 idx 帧并缩放到窗口物理尺寸（cover）。纯 CPU，无 GUI。

        ⛔ 用 **cv2** 而不是 QImage 解码（2026-11-03 实测 4K 帧率上不去的根因）：
        PySide6 的方法调用**不释放 GIL**——QImage 解码 4K 要 33.7ms 且 4 个线程
        被GIL 串行化成 22.5fps；cv2 的 C++ 解码**释放 GIL**，8 线程真并行，
        4K 解码+cover 缩放等效 12.1ms/帧（产能 83fps > 60）。INTER_AREA 用于
        缩小场景，质量与双线性相当且抗摩尔纹。
        """
        # ⛔ Unicode 路径安全：cv2.imread 在 Windows 上无法读含非 ASCII 字符的路径
        #    （部署目录 `发布/` 含中文 → 帧全部解码失败 → 开屏「动画不动」）。改用
        #    读 bytes + cv2.imdecode，能正确处理 Unicode 路径（ASCII 路径也兼容）。
        # ⛔⛔ 2026-11-04：读盘必须走 **file.read()**（CPython 读盘时释放 GIL），
        #    不能用 np.fromfile —— 它**持有 GIL**。真机遥测实锤（skip=16≈267ms、
        #    held=0）：冷缓存/Defender 实时扫描时一次 ~6MB PNG 的 fromfile 拖住
        #    GIL，主线程整个冻结（开屏 logo 段"卡一下"，录屏 f0114~f0129 逐帧
        #    实证）；解码本身跟得上（held=0），冻的只是等 GIL 的主线程。
        try:
            with open(self._fr_paths[idx], "rb") as fh:
                data = fh.read()
        except OSError:
            return None
        buf = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)   # BGR
        if img is None:
            return None
        # ⛔ 双击后预热缓存命中：prewarm_async() 提前解好的帧直接用（零 IO/零解码）
        pre = _PREWARM.pop(idx, None)
        if pre is not None:
            h, w, ch = pre.shape
            qimg = QImage(pre.data, w, h, ch * w,
                          QImage.Format.Format_ARGB32_Premultiplied)
            return qimg, pre
        size = self._pix_size
        if size is not None:
            k = max(size[0] / img.shape[1], size[1] / img.shape[0])
            img = cv2.resize(img, (round(img.shape[1] * k),
                                   round(img.shape[0] * k)),
                             interpolation=cv2.INTER_AREA)
        # ⛔ 统一产出**屏幕同格式** BGRA wrap（2026-11-03 实测）：
        #    paintEvent 的 drawImage(RGB888→BGRA 屏) 每帧要做格式转换+缩放
        #    15~25ms——主线程的大头。worker 转成 BGRA 后 drawImage 1:1 =
        #    纯 memcpy（~3ms）。不 copy：wrap numpy buffer，调用方持有
        #    numpy 引用（_pix_buf）保活即可。
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        h, w, ch = img.shape
        qimg = QImage(img.data, w, h, ch * w, QImage.Format.Format_ARGB32_Premultiplied)
        return qimg, img

    def _show_frame(self, idx: int) -> bool:
        """显示第 idx 帧：优先取并行预解码的现成结果；没 ready 就**保持上一帧**
        （不阻塞主线程同步解码），下一拍定时器再试 —— 彻底消除确定性卡顿。

        ⛔⛔ 关键（2026-11-03 用户：「中间明显卡顿，100%复现在文字刚好要显露」）：
        旧实现在本帧未解码好时**主线程同步解码兜底**，单帧 4K 解码 20~36ms 直接
        卡住主线程 → 在解码偏慢的帧（文字/高细节帧）上 100% 复现卡顿。改为：
        未 ready 就保留上一帧（视觉只是多停 ~8~16ms，完全无感），把解码交给后台
        worker，下一拍（8ms 后）再取。配合 _PREDEC_WINDOW 深缓冲，worker 永远比
        显示跑得快，绝大多数帧 ready 直接上，几乎不触发 hold。
        """
        perf = self._perf
        if idx < 0 or idx >= len(self._fr_paths):
            return False
        # 清理确定过时的 future（k<idx：已经过了，取消其后台解码，释放池 slot）
        stale = [k for k in self._dec_futs if k < idx]
        for k in stale:
            f = self._dec_futs.pop(k)
            f.cancel()
        # 维持预解窗口：idx+1..idx+_PREDEC_WINDOW 始终在途（深缓冲吸收抖动）
        for j in range(idx + 1, min(idx + 1 + _PREDEC_WINDOW, len(self._fr_paths))):
            if j not in self._dec_futs:
                self._dec_futs[j] = self._dec_pool.submit(self._decode_frame, j)
        # 取本帧：只在 future 已完成时才取结果；未完成就保持上一帧（不阻塞）
        fut = self._dec_futs.get(idx)
        pair = None
        if fut is not None and fut.done():
            self._dec_futs.pop(idx, None)
            try:
                pair = fut.result(timeout=0)
            except Exception:             # noqa: BLE001 - 解码失败走下一拍重试
                pair = None
        if pair is None:
            # 本帧未就绪：保持上一帧，记一次 hold（绝不同步解码 → 无卡顿）
            if perf is not None:
                perf["held"] = perf.get("held", 0) + 1
            return False
        self._pix, self._pix_buf = pair
        self._fr_idx = idx
        self._dec_want = idx + 1
        self.update()
        if perf is not None:
            now = time.perf_counter()
            if perf["last"] is not None:
                perf["intervals"].append(now - perf["last"])
            perf["last"] = now
            perf["frames"] += 1
        return True

    def pause_clock(self) -> None:
        """冻结播放进度（主窗构造期间调用，见 main._build_window）。

        ⛔ 冻结期 worker 正好空闲：**立刻把后续 12 帧全部提交预解**——否则
        恢复后预解窗口是空的，主线程每个 future 都在干等（解码 50-70ms/帧），
        全程 10-15fps（2026-11-03 实测）。
        """
        if self._elapsed is None or self._clock_paused:
            return
        self._clock_paused = True
        self._pause_mark = self._elapsed.elapsed()
        if self._dec_pool is not None and self._fr is not None:
            # ⛔ 冻结期 worker 正好空闲：把后续 _PREDEC_WINDOW 帧全部提交预解
            #    （旧代码只补 13 帧，解冻后遇到高细节帧易触发 hold）。
            # ⚠️ 实测（devtools/_probe_splash_wipe.py）：冻结 1408ms 里只有 1 帧
            #    是这期间**新**提交的 —— 因为进冻结那一刻滚动窗口已经是满的
            #    （idx+1..idx+24 早在 _dec_futs 里）。所以这里"补 24 帧"事实上
            #    几乎不新增工作，worker 在冻结期 97% 时间空转。曾计划把冻结期
            #    加深预解（48~64 帧）来"吃掉"这段空转，但**探针证明它不构成卡顿**：
            #    解冻后前方在途帧数全程 q=24 满格、held=0、解码 36~53ms 对 60fps
            #    绰绰有余，真正的卡顿来自主线程事件循环停顿 + 帧节拍量化（见
            #    _schedule_next / _frame_step）。单帧 25.6MB，加深到 64 帧要
            #    1.6GB 内存换零收益，故**保持 24 帧**。
            for j in range(self._fr_idx, min(self._fr_idx + _PREDEC_WINDOW,
                                             self._fr["count"])):
                if j not in self._dec_futs:
                    self._dec_futs[j] = self._dec_pool.submit(
                        self._decode_frame, j)

    def resume_clock(self) -> None:
        """解除冻结：从冻结时的那一帧继续播，冻结时长不计入播放进度。"""
        if self._elapsed is None or not self._clock_paused:
            return
        self._pause_total += self._elapsed.elapsed() - self._pause_mark
        self._clock_paused = False
        # ⛔ 定时器是**单发**的（见 _schedule_next）：冻结期那一拍返回时不再续排，
        #    所以解冻必须自己把节拍重新挂上，否则动画停在冻结帧再也不走。
        self._schedule_next()

    def _frame_step(self) -> None:
        if self._done or self._fr is None or self._elapsed is None:
            return
        if self._clock_paused:
            return
        fps = float(self._fr["fps"] or 60)
        # ⛔ 减去冻结累计：主窗构造的 ~1.2s 不算播放时间——否则恢复后
        #    播放头猛跳几十帧，解码线程永远追不上 = 全程 15fps（实测）。
        t = (self._elapsed.elapsed() - self._pause_total) / 1000.0
        idx = self._fr_skip + int(t * fps)
        # ⛔⛔ 停顿吸收（2026-11-03 终修）：主线程被**别人的**长任务卡住时
        #    （实测解冻后连续三跳：156ms@127 / 112ms@136 / 89ms@142 —— 主窗
        #    show + apply_startup_state + 全屏钉位那一串系统调用），按播放时钟
        #    算出来的 idx 会一口气跳 9 帧；匀速左移的画面上突然跳一截，就是用户
        #    说的"卡顿"。这里**不跳帧**：把多出来的时长记进 ``_pause_total``
        #    （等价于"又冻结了一次"），动画整体顺延，画面每一帧都还在。
        #    代价：整段时长被拉长（实测 +0.2s 量级），有 _MAX_MS=8s 兜底。
        skip = idx - (self._fr_idx + 1)
        if skip > _MAX_SKIP:
            self._pause_total += skip * (1000.0 / fps)
            idx -= skip
            if self._perf is not None:
                self._perf["skip"] = self._perf.get("skip", 0) + skip
            # ⛔ 2026-11-04 诊断：把每次"主线程被卡"落到日志（帧号+墙钟），
            #    定位真机冻结到底发生在播放的哪一段/哪个时刻。
            try:
                import app_log as _al
                _al.info(f"[splash] skip={skip} at frame {idx} "
                         f"(video {idx / fps:.2f}s, wall "
                         f"{self._elapsed.elapsed() / 1000.0:.2f}s)")
            except Exception:             # noqa: BLE001 - 日志失败不影响播放
                pass
        if idx >= self._fr["count"]:
            self.finish()
            return
        if idx != self._fr_idx:
            self._show_frame(idx)
        self._schedule_next()

    def _reveal_video(self, _frame=None) -> None:
        """播放头追上海报那一帧：显示视频层、整窗保持不透明。

        视频从 0 开始播，但海报截在 ``_POSTER_MS`` 处。在播放头到达 _POSTER_MS
        之前不露视频层 —— 否则会先闪一下黑场（视频开头 0~0.23s 是黑）、再跳回海报
        内容。追上了才显示视频，海报→视频无缝衔接（见 start()）。
        """
        if self._done or self._video_live:
            return
        try:
            pos = self._player.position() if self._player is not None else 0
        except RuntimeError:
            pos = 0
        if pos < _POSTER_MS:
            return
        try:
            if self._video is not None:
                self._video.show()
                self._video.raise_()
            self._video_live = True
            self.setWindowOpacity(1.0)
        except RuntimeError:
            return
        try:
            sink = self._player.videoSink() if self._player is not None else None
            if sink is not None:
                sink.videoFrameChanged.disconnect(self._reveal_video)
        except (RuntimeError, TypeError):
            pass

    def _start_video(self) -> None:
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
        except Exception as exc:          # noqa: BLE001 - 没装多媒体后端
            app_log.warning(f"开屏动画：QtMultimedia 不可用（{exc}），跳过")
            self.finish()
            return
        video = QVideoWidget(self)
        video.setGeometry(self.rect())
        video.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatioByExpanding)
        # 先藏着：它一 show 就是黑块，会盖住海报/后面的主窗；首帧到了再露出来
        video.hide()
        self._video = video
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setMuted(True)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(video)
        sink = self._player.videoSink()
        if sink is not None:
            sink.videoFrameChanged.connect(self._reveal_video)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(lambda *_args: self.finish())
        self._player.setSource(QUrl.fromLocalFile(str(self._preset["path"])))
        self._player.play()

    def _on_media_status(self, status) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        if status in (QMediaPlayer.MediaStatus.EndOfMedia,
                      QMediaPlayer.MediaStatus.InvalidMedia):
            self.finish()

    def _start_image(self) -> None:
        path = str(self._preset["path"])
        if Path(path).suffix.lower() in _MOVIE_EXTS:
            self._movie = QMovie(path)
            self._movie.setCacheMode(QMovie.CacheMode.CacheAll)
            self._movie.setScaledSize(self.size())
            label = QLabel(self)
            label.setGeometry(self.rect())
            label.setScaledContents(True)
            label.setMovie(self._movie)
            label.show()
            self._movie.finished.connect(self.finish)
            self._movie.start()
            return
        self._pix = QImage(path)
        if self._pix.isNull():
            self.finish()
            return
        self.update()
        QTimer.singleShot(_STILL_MS, self.finish)

    # ---- 收尾纠正（Plan B 跨机型贴合） ----
    def _get_real_logo_rect(self) -> QRectF | None:
        """真实 UI **墨迹盒**，转换到开屏窗口本地逻辑坐标。

        取 BrandHeader 的 ink 盒（与视频里的墨迹盒同源、宽高比一致），运行时映射到
        真实屏幕，再扣掉开屏窗口左上角。播放器据此把视频墨迹盒拉到这个真实墨迹盒上。
        """
        if self._logo_win is None:
            return None
        if self._logo_rect_cache is not None:
            return self._logo_rect_cache
        try:
            from ui_brand import BrandHeader
            bh = self._logo_win.findChild(BrandHeader)
        except Exception:                 # noqa: BLE001
            return None
        if bh is None:
            return None
        try:
            g = bh.ink_global_rect()
        except Exception:                 # noqa: BLE001
            return None
        if g is None or g.isEmpty():
            return None
        local = self.mapFromGlobal(QPoint(int(g.x()), int(g.y())))
        rect = QRectF(float(local.x()), float(local.y()), g.width(), g.height())
        self._logo_rect_cache = rect
        return rect

    def _correction_transform(self, w: int, h: int, pw: int, ph: int
                              ) -> QTransform | None:
        """收尾段把视频**墨迹盒**纠正贴合到真实 UI **墨迹盒**的相似变换。

        返回应用到 painter 的 ``QTransform``（映射 pix 像素 → 开屏窗口逻辑坐标），
        ``None`` 表示本帧不纠正（正常 cover 绘制）。仅在 Plan B 且已进入静止落定段、
        且真实墨迹盒可取时生效；静止段之前不纠正，避免飞行途中错位。

        机型无关的关键：视频落点 ``land_box`` 是帧空间 [3200,2000] 归一化的墨迹盒，
        先按解码 cover（``K2 = max(w/3200, h/2000)``）映射到窗口逻辑坐标得到视频墨迹
        盒 V，再算相似变换把 V 拉到真实墨迹盒 R。视频墨迹盒与 UI 墨迹盒宽高比一致
        （均 = 1060/168）→ 单缩放+平移即可四角对齐；参考机型上 V≈R → 恒等变换，
        行为不变；其它机型自动贴合。
        """
        if self._land_box is None or self._fr is None or self._fr_still_ms <= 0:
            return None
        fps = float(self._fr.get("fps", 60) or 60)
        t_ms = (self._fr_idx / fps) * 1000.0
        if t_ms < self._fr_still_ms - 1:
            return None
        R = self._get_real_logo_rect()
        if R is None or R.isEmpty():
            return None
        lb = self._land_box
        # 解码 cover：帧(3200x2000) → 窗口逻辑坐标（与 paintEvent 的 drawImage
        # 同源：pix 已按窗口物理尺寸 cover 缩放，再 1:1 落到逻辑坐标）
        K2 = max(w / 3200.0, h / 2000.0)
        vx = lb[0] * 3200.0 * K2 - (w - 3200.0 * K2) / 2.0
        vy = lb[1] * 2000.0 * K2 - (h - 2000.0 * K2) / 2.0
        vw = lb[2] * 3200.0 * K2
        vh = lb[3] * 2000.0 * K2
        if vw <= 0.5 or vh <= 0.5:
            return None
        # 相似变换：统一缩放 s（以宽度对齐）+ 平移，把 V 拉到 R
        s = R.width() / vw
        dpr = self.devicePixelRatioF() or 1.0
        # pix 像素 (u,v) → 窗口逻辑 (d + s*(u/dpr))：先 cover 到逻辑再施加相似变换
        dx = R.x() - s * vx
        dy = R.y() - s * vy
        m = QTransform()
        m.translate(dx, dy)
        m.scale(s / dpr, s / dpr)
        return m

    # ---- 绘制 / 交互
    def paintEvent(self, event) -> None:
        if self._motion is not None:
            painter = QPainter(self)
            self._motion.paint(painter, self.width(), self.height(), self._mt)
            painter.end()
            return
        painter = QPainter(self)
        # 淡出：背景随 _fade_k 淡到透明（窗口已置 WA_TranslucentBackground，
        # 不再用 setWindowOpacity，避免 frameless 撤场时的 DWM 闪一下）。
        k = self._fade_k
        env = 1.0 - k
        bgc = self._bg
        _ba = int(round(255.0 * max(0.0, min(1.0, env))))
        painter.fillRect(self.rect(),
                         QColor(bgc.red(), bgc.green(), bgc.blue(), _ba))
        # 视频层接管后它由 QVideoWidget 自己画，海报（静态图）就不用再叠了，
        # 免得两层在透明边缘/缩放上差一帧
        if not self._video_live and self._pix is not None and not self._pix.isNull():
            # ⛔ 2026-11-03 用户：「有点模糊和卡顿」——以前每帧都
            #    ``pix.scaled(size, SmoothTransformation)``：那是 CPU 高质量双三次
            #    缩放，还要**新建一张 3200×2000 的 QPixmap（≈25MB）**，每帧一次
            #    分配 + 拷贝，是开屏掉帧的大头。改成一次 drawPixmap(target, pix)
            #    让光栅引擎直接按目标矩形采样（双线性，视觉无差），不再产生
            #    中间 pixmap。KeepAspectRatioByExpanding 语义手工算：铺满窗口、
            #    超出部分居中裁掉。
            pw, ph = self._pix.width(), self._pix.height()
            w, h = self.width(), self.height()
            # Plan B 收尾段：按真实 UI 墨迹盒做纠正相似变换（跨机型精确贴合），
            # 视频墨迹盒被拉到真实墨迹盒上；本机≈恒等，行为不变。
            corr = None
            if self._land_box is not None and pw > 0 and ph > 0:
                corr = self._correction_transform(w, h, pw, ph)
            painter.setOpacity(max(0.0, min(1.0, env)))
            if corr is not None:
                painter.setTransform(corr)
                painter.setRenderHint(
                    QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawImage(QRect(0, 0, pw, ph), self._pix)
                painter.resetTransform()
            elif pw > 0 and ph > 0 and w > 0 and h > 0:
                kk = max(w / pw, h / ph)
                tw, th = max(1, int(round(pw * kk))), max(1, int(round(ph * kk)))
                painter.setRenderHint(
                    QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawImage(QRect((w - tw) // 2, (h - th) // 2, tw, th),
                                  self._pix)
            painter.setOpacity(1.0)
        painter.end()

    def mousePressEvent(self, event) -> None:
        self.finish()

    def keyPressEvent(self, event) -> None:
        self.finish()

    # ---- 收尾
    def finish(self) -> None:
        if self._done:
            return
        self._done = True
        # 恢复 GC（与 _fr_begin 的 gc.disable() 配对；_close 会再兜底一次）
        if self._gc_held:
            gc.enable()
            self._gc_held = False
        if self._dec_pool is not None:
            self._dec_pool.shutdown(wait=False)
            self._dec_pool = None
        self._dec_futs.clear()
        self._max_timer.stop()
        # ⛔ 帧定时器是单发的（见 _schedule_next）：finish 后可能还有一拍挂在
        #    事件循环上，显式停掉，别让它在淡出期间再唤醒一次。
        if self._frame_timer is not None:
            self._frame_timer.stop()
        if self._motion_timer is not None:
            self._motion_timer.stop()
        if self._movie is not None:
            self._movie.stop()
        if self._player is not None:
            self._player.stop()
        fade_cb, self._on_fade_start = self._on_fade_start, None
        if fade_cb is not None:
            try:
                fade_cb()
            except Exception:             # noqa: BLE001 - 回调失败不能拖住淡出
                pass
        # ⛔⛔ 2026-11-06「末尾帧率断崖」：旧实现把淡出定时器钉在固定
        #    15ms 周期（≈66fps），并按「拍数 × 15ms」**假定**每拍精确 15ms
        #    累积进度；而落点段 _start_motion 用 ui_anim.frame_hz()（本机
        #    165）+ PreciseTimer + QElapsedTimer 墙钟。一进淡出帧率从 165
        #    掉到 66 → 用户「动画也感觉卡」。
        #    这里与 _start_motion **同源**：帧率取 frame_hz()、PreciseTimer、
        #    间隔 max(1, round(1000/hz)-4)；进度改由 QElapsedTimer 墙钟算，
        #    定时器拍得不准也只会改变采样密度，不会改变 300ms 的淡出总时长。
        try:
            from ui_anim import frame_hz
            hz = max(60, int(frame_hz()))
        except Exception:                 # noqa: BLE001 - 拿不到就跟 60
            hz = 60
        self._fade_elapsed = QElapsedTimer()
        self._fade_elapsed.start()
        self._fade_close_pending = False
        self._fade_timer = QTimer(self)
        self._fade_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._fade_timer.timeout.connect(self._fade_step)
        self._fade_timer.start(max(1, int(round(1000.0 / hz)) - 4))
        # 真机性能遥测落盘（仅帧序列路径会写；任何异常都静默降级）
        if self._perf is not None and self._perf["intervals"]:
            self._write_perf_log(self._perf)

    def _write_perf_log(self, perf: dict) -> None:
        """把真机帧间隔遥测写到 logs/splash_perf.log（失败静默降级）。

        od46：几何分支 clamp 后 ``intervals``（进度增量 Δt）恒 ≤ DT_MAX，若继续
        用它算 stutter 会恒 0、指标失真。所以 stutter / max / p95 / eq_fps 一律
        基于**原始墙钟间隔** ``wall_intervals``；``intervals`` 只留作节拍均匀性
        证据（日志里的 ``dt_max`` 应 ≤ DT_MAX）。帧序列路径没有 wall_intervals，
        自动回退到 intervals（行为不变）。

        记录每帧显示墙钟间隔的统计：中位 / P95 / 最大 / 等效 fps / 卡顿占比
        （>33ms 视为卡顿）/ held（未解好而保持上一帧的次数）/ skip（被"停顿
        吸收"掉、改由后续帧顺延播放的帧数）。
        """
        try:
            import statistics
            from datetime import datetime
            wall = perf.get("wall_intervals") or perf["intervals"]
            iv = perf["intervals"]
            n = len(wall)
            if n == 0:
                return
            wall_sorted = sorted(wall)
            def _pct(p):
                k = max(0, min(n - 1, int(round((p / 100.0) * (n - 1)))))
                return wall_sorted[k] * 1000.0
            median = statistics.median(wall) * 1000.0
            avg = (sum(wall) / n) * 1000.0
            p95 = _pct(95)
            pmax = max(wall) * 1000.0
            total_wall = sum(wall)
            eq_fps = n / total_wall if total_wall > 0 else 0.0
            stutter = sum(1 for x in wall if x > 0.0333)   # >33ms 视为卡顿
            stutter_pct = stutter / n * 100.0
            held = perf.get("held", 0)
            skipped = perf.get("skip", 0)
            hz = max(1, int(getattr(self, "_hz", 60)))
            dt_cap_ms = 2.5 / hz * 1000.0
            dt_max_ms = (max(iv) * 1000.0) if iv else 0.0
            # ⛔ od47d：整体 eq_fps 会被启动期长阻塞拉低，另报一个「稳定段
            #    帧率」（剔除 >33ms 的卡顿帧）才能验证是否真的跑到设置里的
            #    frame_hz()（本机 165）。用户 20:58 明确要求帧率与设置一致。
            stable = [x for x in wall if 0 < x <= 0.0333]
            stable_fps = (1.0 / statistics.median(stable)) if stable else 0.0
            # 日志目录优先与 serialassistant.log 同目录（用户数据根）
            log_path = None
            try:
                import app_log as _al
                p = _al.path()
                if p:
                    log_path = Path(p).parent / "splash_perf.log"
            except Exception:                         # noqa: BLE001
                log_path = None
            if log_path is None:
                local = os.environ.get("LOCALAPPDATA")
                base = (Path(local) / "CQUFSAE-SerialAssistant" / "logs"
                        if local else Path.home() / ".serialassistant" / "logs")
                try:
                    base.mkdir(parents=True, exist_ok=True)
                except OSError:
                    return
                log_path = base / "splash_perf.log"
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"frames={perf['frames']} shown={n} avg={avg:.1f}ms "
                    f"median={median:.1f}ms p95={p95:.1f}ms max={pmax:.1f}ms "
                    f"eq_fps={eq_fps:.1f} stable_fps={stable_fps:.1f} "
                    f"hz(设置目标)={hz} "
                    f"stutter(>33ms)={stutter}({stutter_pct:.1f}%) "
                    f"dt_max={dt_max_ms:.2f}ms/DT_MAX={dt_cap_ms:.2f}ms "
                    f"held(prev-frame kept, not blocking)={held} "
                    f"skip(absorbed, played in order)={skipped} "
                    f"big_gaps(动画秒,ms)={perf.get('big_gaps', [])}\n")
        except Exception:                             # noqa: BLE001
            pass

    def _fade_step(self) -> None:
        """淡出一拍：进度由 ``QElapsedTimer`` **墙钟**决定，不假定每拍 15ms。

        ⛔⛔ 硬伤 B（2026-11-06 定位）：旧实现在 k 到 1.0 那一拍只调了异步
        ``update()`` 就立刻 ``_close()`` —— 全透明帧**从未真正上屏**，窗口带着
        最后一帧有画面的 backing store 被 close，DWM 重合成 = 用户反复投诉的
        「最后闪一下」。修：k=1 用 ``repaint()`` **同步**画出全透明帧，再延后
        一整个目标帧周期才撤场，保证 DWM 至少合成过一帧全透明画面。
        ``_fade_close_pending`` + ``_close()`` 的 ``_closed`` 守卫保证幂等。
        """
        if self._fade_close_pending:
            return
        elapsed = self._fade_elapsed
        k = (min(1.0, elapsed.elapsed() / float(self._fade_ms))
             if elapsed is not None else 1.0)
        self._fade_k = k
        try:
            # 内容先交叉溶到**高斯模糊版**（用户：「logo 到位了就背景高斯模糊
            # 淡出」），背景与 logo 随淡出淡到透明。**不再 setWindowOpacity**
            # （frameless 窗 opacity→0 撤场会触发 Windows DWM 闪一下）；窗口已置
            # WA_TranslucentBackground，只淡内容即可无缝露出主窗。
            if self._motion is not None:
                self._motion.set_fade(k)
            if k >= 1.0:
                # ⛔ 同步绘制（update 是异步的，close 会抢在绘制之前）
                self.repaint()
                self._fade_close_pending = True
                hz = max(1, int(getattr(self, "_hz", 60)))
                QTimer.singleShot(max(1, int(round(1000.0 / hz))), self._close)
            else:
                self.update()
        except RuntimeError:
            self._close()

    def _close(self) -> None:
        # ⛔ 幂等：k=1 延后一拍撤场、异常兜底、8s 兜底都可能再调一次；
        #    _on_finished 只能触发一次（主窗靠它显示）。
        if self._closed:
            return
        # 兜底：无论如何都把 GC 交还自动回收（正常由 finish() 已恢复）
        if self._gc_held:
            gc.enable()
            self._gc_held = False
        if self._fade_timer is not None:
            self._fade_timer.stop()
            self._fade_timer = None
        if self._motion_timer is not None:
            self._motion_timer.stop()
            self._motion_timer = None
        # od57：模糊档表在这里释放（原本在淡入结束就放，但 planA 的整体高斯模糊
        #       淡出要用同一套档表，见 _motion_tick 的说明）。幂等。
        try:
            if self._motion is not None:
                self._motion.release_blur_cache()
        except Exception:                 # noqa: BLE001 - 释放失败不影响撤场
            pass
        try:
            self.close()
        except RuntimeError:
            pass
        callback, self._on_finished = self._on_finished, None
        self._closed = True
        global _active
        _active = None
        if callback is not None:
            try:
                callback()
            except Exception as exc:      # noqa: BLE001
                app_log.error(f"开屏动画结束回调失败：{exc}")


def play(preset: dict | None, on_finished=None) -> "_SplashWindow | None":
    """播放一条开屏动画；返回播放器（没得播/失败返回 None）。"""
    global _active
    if not preset:
        return None
    path = Path(str(preset.get("path", "")))
    if not path.is_file():
        app_log.warning(f"开屏动画文件不存在：{path}")
        return None
    try:
        window = _SplashWindow(preset, on_finished)
    except Exception as exc:              # noqa: BLE001
        app_log.warning(f"开屏动画创建失败：{exc}")
        return None
    _active = window
    try:
        window.start()
    except Exception as exc:              # noqa: BLE001 - 起播失败别留下空窗口
        app_log.warning(f"开屏动画起播失败：{exc}")
        _active = None
        try:
            window.close()
        except Exception:                 # noqa: BLE001
            pass
        return None
    return window


def play_configured(on_finished=None) -> "_SplashWindow | None":
    """按设置播放；没有配置/失败返回 None（调用方据此直接显示主窗）。"""
    try:
        preset = configured()
    except Exception as exc:              # noqa: BLE001
        app_log.warning(f"开屏动画配置读取失败：{exc}")
        return None
    return play(preset, on_finished=on_finished)


# ---- 双击后预热（2026-11-03 用户：「在双击后和空白的 0.3s 就可以提前开始调用渲染」）
# main.py 在 QApplication/主题就绪、play_configured() **之前**调 prewarm_async()：
# 用后台线程把前 N 帧先解成 numpy（cv2 释放 GIL），_decode_frame 命中缓存就直接
# wrap QImage——开屏起播和空白段期间帧帧现成，深缓冲直接建立。
_PREWARM: dict = {}            # idx -> numpy HxWx3 RGB（跨线程 dict 读写，GIL 下安全）


def prewarm_async(count: int = 16) -> None:
    """后台预解码前 ``count`` 帧（按当前主题的 preset）。

    必须在 QApplication 建立之后、play_configured() 之前调用。线程是 daemon，
    开屏起播后剩余的缓存项由 _decode_frame 逐个消费/清理，无需显式停止。
    """
    import threading

    # ⛔ cv2 已砍：打包版连 manifest 都没有，直接返回，不再起 daemon 线程
    #    （线程内 cv2.imdecode 会抛 AttributeError 且 windowed 模式下静默）。
    if cv2 is None:
        return

    try:
        preset = configured()
    except Exception:                     # noqa: BLE001 - 预热失败不影响启动
        return
    if not preset:
        return
    path = Path(str(preset.get("path", "")))
    d = path.parent / f"{path.stem}_frames"
    m = d / "manifest.json"
    if not m.is_file():
        return
    try:
        import json
        man = json.loads(m.read_text(encoding="utf-8"))
        total = int(man.get("count", 0))
    except Exception:                     # noqa: BLE001
        return
    if total <= 0:
        return

    def _run() -> None:
        for i in range(min(count, total)):
            if i in _PREWARM:
                continue
            ext = man.get("ext", "jpg")
            p = d / f"frame_{i + 1:05d}.{ext}"
            # ⛔ Unicode 路径安全（同 _decode_frame）：部署目录含中文时 cv2.imread 失败
            # ⛔ 读盘走 file.read()（释放 GIL；np.fromfile 持 GIL，见 _decode_frame）
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8),
                               cv2.IMREAD_COLOR)      # BGR，释放 GIL
            if img is None:
                continue
            # ⛔ 统一转 **BGRA**（与 _decode_frame 的产出同格式）：命中路径直接
            #    按 ARGB32_Premultiplied wrap，两个路径行为完全一致。
            img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
            _PREWARM[i] = img

    threading.Thread(target=_run, name="splash-prewarm", daemon=True).start()


def still_delay_ms(player) -> int | None:
    """距开屏视频「天然静止段」起点还有多少毫秒（None = 无帧序列/无静止段）。

    调用方拿它排一个 ``QTimer.singleShot``：到静止段再构造主窗，构造的 ~937ms
    正好被视频自带的 ~0.8s 静止"消化"（用户 2026-11-03：开屏前停顿要短）。

    ⛔ **不要在 main() 里忙等**（曾经的 pump_to_still 就是 `processEvents +
    sleep` 循环）：那样会把开屏的帧定时器压成不均匀节奏，画面一顿一顿
    （用户："有点模糊和卡顿"）。定时器方案让事件循环全程正常跑。
    """
    if player is None:
        return None
    try:
        # ⛔⛔ od47c：原生几何动画的主窗构造**提前到 t=0（空白段）**。
        #    原设计排在 spec.t_text_end（墙钟 2.0s），那里正好是「向左上角
        #    落点」段起点；即使后台字体预热把构造从 741ms 压到 137ms，
        #    连上 show/apply/repaint 仍有 ~250ms 阻塞落在 t=2.03，
        #    真机遥测 big_gaps 实锤。空白段 0~0.30s 画面纯背景、无任何
        #    动画内容，把这 250ms 放进去**完全不可见**；且主窗更早就绪，
        #    淡出时直接露出，反而更贴合「渲染动画的同时加载软件本体」。
        #    ⚠️ 只改几何分支；帧序列/视频（custom）路径仍按天然静止段走。
        motion = getattr(player, "_motion", None)
        if motion is not None:
            return 0
        still_ms = int(getattr(player, "_fr_still_ms", 0) or 0)
        fr = getattr(player, "_fr", None)
        if not still_ms or fr is None:
            return None
        fps = float(fr.get("fps", 60) or 60)
        cur_ms = max(0, int(player._fr_idx)) / fps * 1000.0
        return max(0, int(round(still_ms - cur_ms)))
    except RuntimeError:
        return None
