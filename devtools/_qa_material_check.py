# -*- coding: utf-8 -*-
r"""QA 独立验收探针：材质系统（功能正确性 + 对比度）。

与工程师的 ``_material_shots.py`` **无关**：这里自己抓图、自己算对比度、
自己驱动设置对话框走"取消/确定"两条路径，不复用他的脚本，也不抄它的数字。

覆盖：
  1. 两种材质都能切换，且切换后**同一次事件循环内**卡片/页头/回放条/标题栏重绘；
  2. 强度 0/1/50/99/100 五档下材质参数单调（不回头）；
  3. 设置面板改强度后【取消】回退、【确定】落盘且"重启"后保持；
  4. 切材质**不丢**其它 customization 配置（全量键值对比）；
  5. 强度 100 时卡面主/次文字对比度（WCAG，自己算）。

用法：
    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools\_qa_material_check.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSettings      # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage                           # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QWidget        # noqa: E402

from ui_theme import (                                                     # noqa: E402
    MATERIAL_FROSTED, MATERIAL_LIQUID, apply_scheme,
    frost_gain, glass_alpha, glass_strength, liquid_alpha, liquid_core_alpha,
    liquid_inner_alpha, liquid_spec_alpha, material, qcolor, set_glass_strength,
    set_material, ui_font,
)

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    return ok


# ----------------------------------------------------------------- 对比度

def _lin(value: float) -> float:
    value /= 255.0
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(color: QColor) -> float:
    return 0.2126 * _lin(color.red()) + 0.7152 * _lin(color.green()) + 0.0722 * _lin(color.blue())


def contrast(a: QColor, b: QColor) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = (la, lb) if la >= lb else (lb, la)
    return (hi + 0.05) / (lo + 0.05)


# ----------------------------------------------------------------- 渲染工具

def render_widget(widget: QWidget, dpr: float = 2.0) -> QImage:
    """把控件渲染进一张 QImage（软件渲染，稳定可复现）。"""
    w = max(1, widget.width())
    h = max(1, widget.height())
    image = QImage(int(w * dpr), int(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    image.fill(qcolor("bg"))
    widget.render(image, QPoint(0, 0))
    return image


def image_signature(image: QImage) -> bytes:
    """取一张图的字节签名（用来判断"重绘后是否真的变了"）。"""
    bits = image.constBits()
    return bytes(bits) if bits else b""


def pump(app, seconds: float) -> None:
    import time
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


def median_surface(image: QImage, pad_frac: float = 0.30) -> QColor:
    """卡面底色：取下半部像素的中位亮度值（避开标题/正文墨迹与描边）。"""
    w, h = image.width(), image.height()
    x0, x1 = int(w * 0.10), int(w * 0.90)
    y0, y1 = int(h * (1.0 - pad_frac)), int(h * 0.94)
    samples = [image.pixelColor(x, y)
               for y in range(max(1, y0), max(2, y1), 2)
               for x in range(max(1, x0), max(2, x1), 2)]
    if not samples:
        return QColor(0, 0, 0)
    samples.sort(key=luminance)
    return samples[len(samples) // 2]


def text_color(image: QImage, rect: QRect, surface: QColor) -> QColor:
    """标签矩形内"与卡面对比度最大"的像素 —— 对深浅两种主题都取得到字形核心色。"""
    best, best_ratio = surface, 0.0
    for y in range(rect.top(), min(rect.bottom(), image.height())):
        for x in range(rect.left(), min(rect.right(), image.width())):
            color = image.pixelColor(x, y)
            ratio = contrast(surface, color)
            if ratio > best_ratio:
                best, best_ratio = color, ratio
    return best


# ----------------------------------------------------------------- 绘制计数

class _PaintCounter(QObject):
    """给控件挂上，统计它收到多少次 Paint 事件。"""

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def eventFilter(self, obj, event) -> bool:      # noqa: N802
        if event.type() == QEvent.Type.Paint:
            self.count += 1
        return False


# ----------------------------------------------------------------- 用例

def test_immediate_switch(app, window) -> None:
    """两种材质切换后，同一次事件循环内所有自绘面重绘。

    ⚠️ 品牌页头 / 标题栏**没有玻璃面**（它们直接画在页面背景上，见
    ``ui_brand.BrandHeader`` 的说明）—— 它们会重绘，但绘制内容不随材质变。
    真正"随材质换渲染路径"的是**卡片**与**回放条**（走 ``paint_glass``）。
    """
    all_faces = {
        "接收卡片": window._rx_card,
        "品牌页头": window.brand_header,
        "回放条": window._replay_bar,
        "标题栏": window._caption_bar,
    }
    glass_faces = ("接收卡片", "回放条")     # 有玻璃面、绘制随材质变的

    # A. 状态必须**同步**切换（不等事件循环）
    window._apply_material(MATERIAL_FROSTED)
    set_glass_strength(70)
    pump(app, 0.15)
    frosted_sigs = {name: image_signature(render_widget(w)) for name, w in all_faces.items()}

    window._apply_material(MATERIAL_LIQUID)
    check("材质切换：状态同步生效", material() == MATERIAL_LIQUID,
          f"material()={material()!r} 立刻为 liquid")

    liquid_sigs = {name: image_signature(render_widget(w)) for name, w in all_faces.items()}
    for name in glass_faces:
        check(f"材质切换：{name}绘制随材质变化",
              frosted_sigs[name] != liquid_sigs[name],
              "毛玻璃/液态玻璃渲染像素不同")

    # B. 重绘必须发生在**同一次事件循环内**（挂 Paint 事件计数器，切完只跑一轮）
    counters = {}
    for name, widget in all_faces.items():
        counter = _PaintCounter()
        widget.installEventFilter(counter)
        counters[name] = counter
    pump(app, 0.1)                          # 先把队列里积压的重绘清空
    for counter in counters.values():
        counter.count = 0
    window._apply_material(MATERIAL_FROSTED)     # 切回毛玻璃
    app.processEvents()                     # 只跑一轮事件循环
    for name, counter in counters.items():
        check(f"材质切换：{name}同一次事件循环内重绘", counter.count > 0,
              f"Paint 事件 {counter.count} 次")
        all_faces[name].removeEventFilter(counter)
    window._apply_material(MATERIAL_LIQUID)
    pump(app, 0.1)


def test_strength_monotonic(app, window) -> None:
    """强度 0/1/50/99/100 五档：材质参数单调（不回头）。"""
    strengths = [0, 1, 50, 99, 100]

    # 参数层：直接读 ui_theme 的材质参数（材质效果的单一真源）
    set_material(MATERIAL_FROSTED)
    frosted = []
    for s in strengths:
        set_glass_strength(s)
        frosted.append((s, glass_alpha(), frost_gain()))
    alpha_vals = [a for _s, a, _g in frosted]
    gain_vals = [g for _s, _g, g in frosted]
    check("毛玻璃：强度↑ → 面色不透明度单调不增（越透）",
          all(b <= a + 1e-9 for a, b in zip(alpha_vals, alpha_vals[1:])),
          f"alpha={alpha_vals}")
    check("毛玻璃：强度↑ → 颗粒幅度单调不减（越毛）",
          all(b >= a - 1e-9 for a, b in zip(gain_vals, gain_vals[1:])),
          f"gain={[round(g,3) for g in gain_vals]}")

    set_material(MATERIAL_LIQUID)
    liquid = []
    for s in strengths:
        set_glass_strength(s)
        liquid.append((s, liquid_alpha(), liquid_spec_alpha(),
                       liquid_core_alpha(), liquid_inner_alpha()))
    for idx, label in ((1, "主体不透明度"), (2, "镜面高光"), (3, "中心提亮"), (4, "边缘内发光")):
        vals = [row[idx] for row in liquid]
        check(f"液态玻璃：强度↑ → {label}单调不减",
              all(b >= a for a, b in zip(vals, vals[1:])), f"{label}={vals}")

    # 渲染层佐证：毛玻璃颗粒增强 → 空卡面内部像素标准差应单调不减
    # （用**空卡片**量，避免日志正文/控件几何污染方差）
    from ui_surface import Card
    set_material(MATERIAL_FROSTED)
    spreads = []
    for s in strengths:
        set_glass_strength(s)
        holder = QWidget()
        card = Card("", holder)
        card.resize(360, 200)
        card.layout().activate()
        for _ in range(2):
            app.processEvents()
        img = render_widget(card)
        w, h = img.width(), img.height()
        vals = [luminance(img.pixelColor(x, y))
                for y in range(int(h * 0.55), int(h * 0.9), 3)
                for x in range(int(w * 0.1), int(w * 0.9), 3)]
        mean = sum(vals) / max(1, len(vals))
        std = (sum((v - mean) ** 2 for v in vals) / max(1, len(vals))) ** 0.5
        spreads.append(std)
        holder.deleteLater()
    check("毛玻璃：强度↑ → 渲染颗粒方差单调不减",
          all(b >= a - 0.0005 for a, b in zip(spreads, spreads[1:])),
          f"颗粒方差={[round(v,5) for v in spreads]}")


def test_settings_cancel_confirm(app, window) -> None:
    """设置面板：改强度后取消 → 回退；确定 → 落盘且重启后保持。"""
    import main_window as mw

    real_dialog = mw.SettingsDialog

    class _FakeDialog(real_dialog):     # type: ignore[misc, valid-type]
        sim_accept = True
        sim_strength = None
        sim_material = None

        def exec(self):                 # noqa: A003
            if self.sim_strength is not None:
                self.slider_glass.setValue(int(self.sim_strength))   # 触发实时预览
            if self.sim_material is not None:
                combo = self._custom_widgets.get("material")
                idx = combo.findData(self.sim_material) if combo is not None else -1
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            QApplication.processEvents()
            return (QDialog.DialogCode.Accepted if self.sim_accept
                    else QDialog.DialogCode.Rejected)

    mw.SettingsDialog = _FakeDialog
    try:
        # --- 取消路径 ---
        window._apply_glass(10)
        window._apply_material(MATERIAL_FROSTED)
        app.processEvents()
        before = glass_strength()
        _FakeDialog.sim_accept = False
        _FakeDialog.sim_strength = 90
        _FakeDialog.sim_material = MATERIAL_LIQUID
        window._open_settings()
        app.processEvents()
        check("设置取消：强度回到改之前",
              glass_strength() == before, f"{before} -> {glass_strength()}")
        check("设置取消：材质回到改之前",
              material() == MATERIAL_FROSTED, f"material()={material()!r}")

        # --- 确定路径 ---
        settings = QSettings("MimoWorks", "SerialAssistant")
        import customization
        _FakeDialog.sim_accept = True
        _FakeDialog.sim_strength = 88
        _FakeDialog.sim_material = MATERIAL_LIQUID
        window._open_settings()
        app.processEvents()
        check("设置确定：强度当场生效", glass_strength() == 88, f"glass={glass_strength()}")
        check("设置确定：材质当场生效", material() == MATERIAL_LIQUID,
              f"material()={material()!r}")
        settings.sync()
        stored_glass = settings.value("glass")
        check("设置确定：强度已落盘", int(stored_glass) == 88, f"registry glass={stored_glass!r}")
        check("设置确定：glass_chosen 已置位",
              settings.value("glass_chosen", False, type=bool) is True)
        check("设置确定：材质已落盘",
              str(customization.get("material")) == MATERIAL_LIQUID,
              f"customization.material={customization.get('material')!r}")

        # --- "重启"后保持（新开一个主窗口，读注册表恢复）---
        set_glass_strength(0)
        set_material(MATERIAL_FROSTED)
        window2 = mw.MainWindow()
        window2._port_watch.stop()
        window2.setGeometry(0, 0, 1134, 820)
        app.processEvents()
        check("重启后：强度恢复为 88", glass_strength() == 88, f"glass={glass_strength()}")
        check("重启后：材质恢复为液态玻璃", material() == MATERIAL_LIQUID,
              f"material()={material()!r}")
        window2.setGeometry(0, 0, 1134, 820)
        window2.close()
        window2.deleteLater()
        app.processEvents()
    finally:
        mw.SettingsDialog = real_dialog


def test_material_switch_keeps_other_custom(app, window) -> None:
    """切材质不得丢用户其它 customization 配置（全量键值对比）。"""
    import customization
    # 钉几个哨兵值（模拟用户改过的配置）
    customization.set("volt_limit_high", 4111)
    customization.set("pause_snap_lines", 13)
    customization.set("rx_font_size", 30)
    customization.set("startup_maximized", False)

    keys = [spec["key"] for spec in customization.specs()]
    before = {k: customization.get(k) for k in keys}

    # 只切材质，确定
    import main_window as mw
    real_dialog = mw.SettingsDialog

    class _FakeDialog(real_dialog):     # type: ignore[misc, valid-type]
        def exec(self):                 # noqa: A003
            combo = self._custom_widgets.get("material")
            idx = combo.findData(MATERIAL_LIQUID) if combo is not None else -1
            if idx >= 0:
                combo.setCurrentIndex(idx)
            QApplication.processEvents()
            return QDialog.DialogCode.Accepted

    mw.SettingsDialog = _FakeDialog
    try:
        window._open_settings()
        app.processEvents()
    finally:
        mw.SettingsDialog = real_dialog

    after = {k: customization.get(k) for k in keys}
    lost = {k: (before[k], after[k]) for k in keys if before[k] != after[k]}
    only_material = set(lost.keys()) <= {"material"}
    detail = ("仅 material 变化" if only_material
              else "被改动的键: " + repr(lost))
    check("切材质：其余定制配置全部保持不变", only_material, detail)
    check("切材质：哨兵值未被覆盖",
          after["volt_limit_high"] == 4111 and after["pause_snap_lines"] == 13
          and after["rx_font_size"] == 30,
          f"volt_high={after['volt_limit_high']} pause={after['pause_snap_lines']} "
          f"font={after['rx_font_size']}")
    # 收尾：把哨兵还原为默认
    for spec in customization.specs():
        customization.set(spec["key"], spec["default"])


def test_contrast(app, window) -> None:
    """强度 100 时卡面主/次文字对比度（自己渲染自己算）。"""
    for scheme in ("dark", "light"):
        # ⚠️ 必须走 apply_scheme（连调色板一起切）——只 set_scheme 的话
        #    render() 的 DrawWindowBackground 会用**旧主题的调色板底色**铺底，
        #    浅色下卡片会被垫深（实测卡面 #757678，纯属测量假象）。
        apply_scheme(scheme)
        for mat in (MATERIAL_FROSTED, MATERIAL_LIQUID):
            set_material(mat)
            set_glass_strength(100)
            holder = QWidget()
            card = __import__("ui_surface", fromlist=["Card"]).Card("接收区", holder)
            card.resize(400, 220)
            body = card.body
            primary = QLabel("CQUFSAE 电压 3700 mV")
            primary.setObjectName("title")
            primary.setFont(ui_font(13.0, QFont.Weight.DemiBold))
            secondary = QLabel("第 12 轮 · Temperature1 · 22 ℃")
            secondary.setFont(ui_font(11.0))
            body.addWidget(primary)
            body.addWidget(secondary)
            card.layout().activate()
            for _ in range(3):
                app.processEvents()

            image = render_widget(card)
            surface = median_surface(image)
            dpr = image.devicePixelRatio() or 2.0

            def label_rect(lbl: QLabel) -> QRect:
                tl = lbl.mapTo(card, QPoint(0, 0))
                return QRect(int(tl.x() * dpr), int(tl.y() * dpr),
                             int(lbl.width() * dpr), int(lbl.height() * dpr))

            p_ratio = contrast(surface, text_color(image, label_rect(primary), surface))
            s_ratio = contrast(surface, text_color(image, label_rect(secondary), surface))
            check(f"对比度[{scheme}/{mat}] 主文字 ≥4.5:1", p_ratio >= 4.5,
                  f"{p_ratio:.2f}:1  卡面={surface.name()}")
            check(f"对比度[{scheme}/{mat}] 次文字 ≥3.0:1", s_ratio >= 3.0,
                  f"{s_ratio:.2f}:1")
            holder.deleteLater()
    set_glass_strength(0)
    set_material(MATERIAL_FROSTED)


# ----------------------------------------------------------------- main

def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from ui_anim import set_animations_enabled_override
    set_animations_enabled_override(True)

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    import customization
    for spec in customization.specs():
        customization.set(spec["key"], spec["default"])

    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    from main_window import MainWindow
    window = MainWindow()
    window._port_watch.stop()
    window.setGeometry(0, 0, 1180, 860)
    window.show()
    pump(app, 1.0)                      # 等布局/入场动画落定
    window._replay_bar.show()           # 让回放条参与"是否重绘"的检查
    pump(app, 0.4)                      # 等回放条被布局分配尺寸

    try:
        test_immediate_switch(app, window)
        test_strength_monotonic(app, window)
        test_settings_cancel_confirm(app, window)
        test_material_switch_keeps_other_custom(app, window)
        test_contrast(app, window)
    finally:
        window.setGeometry(0, 0, 1134, 820)
        window.close()
        # 把本次探针**新造出来的键**也清掉（否则 custom/material 会永久留在用户配置里）
        for key in set(settings.allKeys()) - set(snapshot):
            settings.remove(key)
        for key, value in snapshot.items():
            if value is not None:
                settings.setValue(key, value)
        settings.sync()

    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    print("\n" + "=" * 72)
    print(f"材质系统验收：{passed}/{total} 通过")
    for name, ok, detail in _RESULTS:
        if not ok:
            print(f"  FAIL: {name}  [{detail}]")
    print("=" * 72)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
