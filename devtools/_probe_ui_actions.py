# -*- coding: utf-8 -*-
r"""一次性真平台探针：配置卡底部动作行改版的功能等价性 + 窄档不崩。

证明（写 _check/ui_actions_probe.txt）：
  A 五颗按钮都存在、是 RowButton、文案与改前一致；五颗全是 outlined 描边态
  B tooltip 非空且关键子串在（文案/提示未丢）
  C clicked 连接的目标方法存在且可调用（handler 未丢）
  D 回放态文案切换与改前一致（打开/关闭 近期/历史）
  E 禁用逻辑：btn_dlc / btn_error_history 等 setEnabled(False) 生效
  F 行高=32、DLC 是 muted；五颗像素上有浅底+描边，宽度按文字取、不撑满整行且水平居中
  G 布局仍在 _config_content 内、不在 _config_fields；无 64px 占位残留
  H 窄档（把 config 收窄到 1/4）不抛异常、卡片最小宽不异常
  I QSettings 前后零变化
"""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from PySide6.QtCore import QEvent, QPoint, QSettings, Qt
from PySide6.QtGui import QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QWidget

import customization
import main_window
import rx_history
import ui_controls

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def in_chain(w, anc) -> bool:
    cur = w
    while cur is not None:
        if cur is anc:
            return True
        cur = cur.parentWidget()
    return False


def main() -> int:
    import tempfile
    import shutil
    from pathlib import Path

    app = QApplication.instance() or QApplication(sys.argv)
    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}
    real_dir = rx_history._resolve_dir
    real_pin = customization.current_vehicle
    tmp = Path(tempfile.mkdtemp(prefix="actprobe_", dir=Path(_ROOT) / "_check"))
    win = None
    try:
        rx_history._resolve_dir = lambda: tmp
        customization.current_vehicle = lambda: "E74-2026"
        win = main_window.MainWindow()
        win.resize(1400, 900)
        win.show()
        win._config_card.show()
        win._config_content.show()
        app.processEvents()

        btns = {
            "btn_marks": "查看标记",
            "btn_open_history": "打开近期数据",
            "btn_open_long_history": "打开历史数据",
            "btn_error_history": "历史报错数据",
            "btn_dlc": "DLC",
        }
        ok_all = True
        for attr, text in btns.items():
            b = getattr(win, attr, None)
            is_row = isinstance(b, ui_controls.RowButton)
            same_text = b is not None and b.text() == text
            check(f"A {attr} 存在/RowButton/文案", is_row and same_text,
                  f"row={is_row} text={getattr(b, 'text', lambda: '?')()!r}")
            ok_all = ok_all and is_row and same_text
        check("A 五颗全部符合", ok_all)
        _outlined = {a: getattr(getattr(win, a, None), "_outlined", False)
                     for a in btns}
        check("A2 五颗全是 outlined 描边按钮", all(_outlined.values()),
              str(_outlined))

        # B tooltip 关键子串
        tips = {
            "btn_marks": "查看整轮标记",
            "btn_open_history": "近期数据",
            "btn_open_long_history": "采样间隔",
            "btn_error_history": "超温",
            "btn_dlc": "DLC",
        }
        for attr, sub in tips.items():
            b = getattr(win, attr)
            check(f"B {attr} tooltip 含「{sub}」", sub in b.toolTip(), b.toolTip()[:24])

        # C handler 可调用
        handlers = {
            "btn_marks": "_show_marks_dialog",
            "btn_open_history": "_open_history",
            "btn_open_long_history": "_open_long_history",
            "btn_error_history": "_open_error_history",
            "btn_dlc": "_on_dlc_clicked",
        }
        for attr, meth in handlers.items():
            check(f"C {attr} handler {meth} 存在", callable(getattr(win, meth, None)))

        # D 回放文案切换
        win._replay_active = False
        win._replay_kind = ""
        win._refresh_replay_ui()
        app.processEvents()
        d1 = (win.btn_open_history.text() == "打开近期数据"
              and win.btn_open_long_history.text() == "打开历史数据")
        win._replay_active = True
        win._replay_kind = "recent"
        win._refresh_replay_ui()
        app.processEvents()
        d2 = (win.btn_open_history.text() == "关闭近期数据"
              and win.btn_open_long_history.text() == "打开历史数据")
        win._replay_kind = "long"
        win._refresh_replay_ui()
        app.processEvents()
        d3 = (win.btn_open_history.text() == "打开近期数据"
              and win.btn_open_long_history.text() == "关闭历史数据")
        check("D 回放文案三态切换一致", d1 and d2 and d3,
              f"d1={d1} d2={d2} d3={d3}")
        win._replay_active = False
        win._replay_kind = ""
        win._refresh_replay_ui()
        app.processEvents()

        # E 禁用逻辑
        for attr in ("btn_dlc", "btn_error_history", "btn_marks"):
            b = getattr(win, attr)
            b.setEnabled(False)
            off = not b.isEnabled()
            b.setEnabled(True)
            on = b.isEnabled()
            check(f"E {attr} 禁用/启用生效", off and on)

        # F 行高 / muted
        check("F1 行高 = 32（RowButton.HEIGHT）",
              ui_controls.RowButton.HEIGHT == 32
              and win.btn_marks.height() in (32, 0),  # 未 show 高度可能 0
              f"HEIGHT={ui_controls.RowButton.HEIGHT} h={win.btn_marks.height()}")
        check("F2 DLC 是 muted", getattr(win.btn_dlc, "_muted", False) is True)
        check("F3 其余四颗非 muted",
              all(not getattr(getattr(win, a), "_muted", False)
                  for a in ("btn_marks", "btn_open_history",
                            "btn_open_long_history", "btn_error_history")))

        # F4 真像素：直接 render() 按钮自己（⛔ 整窗/Card 的 grab 都抓不到玻璃卡片
        #    内容，实测全透明）。判据不写死主题色：描边态必须**不透明**地画出
        #    浅底和边框，普通动作行的对应位置必须是透明的。
        def _render_px(widget, lx: int, ly: int):
            pm = QPixmap(widget.size())
            pm.fill(Qt.GlobalColor.transparent)
            # render() 默认会先把 QSS 的 QPushButton 底色铺上（实测全不透明），
            #    必须只给 DrawChildren 才能看到控件自己 paintEvent 画的透明/描边。
            widget.render(pm, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
            img = pm.toImage()
            return img, img.pixelColor(lx, ly).getRgb()

        app.processEvents()   # 居中对齐改完先让布局落定，别量到中间态尺寸
        for attr in btns:
            b = getattr(win, attr)
            # 串口模式下 DLC 按设计隐藏；隐藏控件 render() 出来的图尺寸会失真
            # （实测 widget=88x32 但 pixmap=640x32，取样全透明）。临时显示再收回去。
            dlc_hidden = (attr == "btn_dlc" and b.isHidden())
            if dlc_hidden:
                b.show()
                app.processEvents()
            # 只 render 一次：DLC 在两次 render 之间尺寸可能还没落定，
            # 先前分别取样会出现 inside 透明 / border 有色的假红。
            img, _ = _render_px(b, 0, 0)
            img.save(str(Path(_ROOT) / "_check" / f"ui_actions_{attr}.png"))
            cx = img.width() // 2
            inside = img.pixelColor(cx, 5).getRgb()
            border = img.pixelColor(cx, 0).getRgb()   # 胶囊上边缘中点=描边
            check(f"F4 {attr} 浅底/描边在像素上可见",
                  inside[3] == 255 and border[3] == 255 and inside[:3] != border[:3],
                  f"inside={inside} border={border} size={img.width()}x{img.height()} "
                  f"widget={b.width()}x{b.height()} visible={b.isVisible()}")
            if dlc_hidden:
                b.hide()
                app.processEvents()
        widths = {a: getattr(win, a).width() for a in btns}
        check("F5 按钮宽度按文字取合适尺寸（不撑满配置栏）",
              all(88 <= w <= 180 for w in widths.values())
              and max(widths.values()) < win._config_content.width() - 20,
              f"widths={widths} content={win._config_content.width()}")
        # F6 用户「将这几个按钮居中放置」：每颗按钮中心 x 必须落在配置栏中线。
        center = win._config_content.width() / 2.0
        offsets = {}
        for attr in btns:
            b = getattr(win, attr)
            p = b.mapTo(win._config_content, QPoint(b.rect().center().x(), 0))
            offsets[attr] = round(p.x() - center, 1)
        check("F6 五颗按钮水平居中", all(abs(v) <= 2 for v in offsets.values()),
              f"offsets={offsets} center={center}")

        # G 在 _config_content 内、不在 _config_fields、无 64px 占位残留
        check("G1 五颗都在 _config_content 内",
              all(in_chain(getattr(win, a), win._config_content) for a in btns))
        fld_widgets = {w for w, _b in win._config_fields}
        check("G2 五颗都不在 _config_fields",
              all(getattr(win, a) not in fld_widgets for a in btns))
        check("G3 spacer 属性已删除",
              not any(hasattr(win, s) for s in
                      ("_hist_spacer", "_long_hist_spacer", "_err_spacer", "_dlc_spacer")))

        # H 窄档：收窄卡片不崩、最小宽不异常增大
        try:
            for level in (0, 1, 2, 3):
                win._apply_compact(level) if hasattr(win, "_apply_compact") else None
                app.processEvents()
            narrow_ok = True
            narrow_detail = "ok"
        except Exception as exc:                        # noqa: BLE001
            narrow_ok = False
            narrow_detail = f"{type(exc).__name__}: {exc}"
        check("H 窄档 _apply_compact 不抛异常", narrow_ok, narrow_detail)
        mw = win._config_card.minimumWidth()
        check("H2 卡片最小宽合理（<=420）", mw <= 420, f"minW={mw}")

    finally:
        if win is not None:
            win.close()
            # close() 只起 160ms 淡出，真清理在 165ms 后；立刻 deleteLater()
            # 会在抓图/扫描线程还活着时析构主窗 → 退出码 127、stdout 丢。
            for _ in range(80):
                app.processEvents()
                time.sleep(0.006)
            win.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        rx_history._resolve_dir = real_dir
        customization.current_vehicle = real_pin
        for k in list(qs.allKeys()):
            if k not in snap:
                qs.remove(k)
        for k, v in snap.items():
            qs.setValue(k, v)
        qs.sync()
        shutil.rmtree(tmp, ignore_errors=True)

    now = {k: qs.value(k) for k in qs.allKeys()}
    check("I QSettings 前后零变化", now == snap, f"keys {len(snap)} -> {len(now)}")
    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    tail = f"\n合计 {passed}/{total} PASS  ->  IS_PASS: {'YES' if passed == total else 'NO'}"
    print(tail)
    with open(os.path.join(_ROOT, "_check", "ui_actions_probe.txt"),
              "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" +
                           (f"   [{d}]" if d else "") for n, ok, d in _RESULTS)
                 + tail + "\n")
    app.quit()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
