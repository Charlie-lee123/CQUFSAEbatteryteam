"""独立验收（证伪向）：SOC 底栏 UI 居中 + QSS 三态渲染色。

离线跑（offscreen），只读打补丁 current_vehicle（不写注册表），历史目录重定向临时目录。
产物写到 _check/ 供肉眼复核。

跑法：.venv/Scripts/python.exe devtools/_qa_soc_ui.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROWS: list[tuple[str, str, str]] = []


def rec(tag: str, ok: bool, detail: str) -> None:
    ROWS.append((tag, "PASS" if ok else "FAIL", detail))


# ---------------------------------------------------------------- 7. UI 居中
def q7_layout() -> None:
    from PySide6.QtWidgets import QApplication

    import customization
    import rx_history
    import rx_store

    app = QApplication.instance() or QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="qa_soc_ui_"))
    real_dir = rx_history._resolve_dir
    real_pin = customization.current_vehicle
    real_db = getattr(rx_store, "_DB_PATH", None)
    # ⛔ close() 会走 _save_settings 把 geometry 等写回真实注册表 —— 必须先快照
    from PySide6.QtCore import QSettings
    settings = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: settings.value(k) for k in settings.allKeys()}
    rx_history._resolve_dir = lambda: tmp
    customization.current_vehicle = lambda: "E74-2026"   # 只读打补丁
    win = None
    try:
        import main_window
        win = main_window.MainWindow()
        win.show()
        app.processEvents()
        lbl = win.lbl_soc
        bar = win._bottom_bar

        # 先喂一轮真机量级报文，让读数变成真实长文本（最长的情况）
        cells = [3180 + (i % 7) for i in range(125)]
        probe_texts = []
        line = []
        for m in range(5):
            probe_texts.append(f"Voltage{m + 1}:")
            line = []
            for s in range(25):
                line.append(f"{s + 1:02d}.{cells[m * 25 + s]}")
            probe_texts.append(" ".join(line))
        probe_texts += ["CVMax=5446, Min=0"]
        win._feed_soc("\n".join(probe_texts))
        app.processEvents()
        long_text = win.lbl_soc.text()
        rec("7.0 读数文本样例", bool(long_text), repr(long_text))

        def measure(width: int, height: int = 640) -> tuple[int, int, int, int, int]:
            win.resize(width, height)
            app.processEvents()
            app.processEvents()
            w = lbl.width()
            offset = (lbl.x() + w // 2) - bar.width() // 2
            overflow_right = (lbl.x() + w) - bar.width()
            return bar.width(), w, lbl.x(), offset, overflow_right

        for width, tag in ((480, "窄窗 480（四分屏）"), (640, "三分屏 640"),
                           (960, "两分屏 960"), (2560, "极宽 2560"),
                           (3200, "超宽 3200")):
            bw, lw, lx, off, over = measure(width)
            rec(f"7.1 {tag}: 居中偏差 ≤4px", abs(off) <= 4,
                f"栏宽={bw} 标签宽={lw} x={lx} 偏差={off}px")
            rec(f"7.2 {tag}: 不溢出底栏右侧", over <= 0,
                f"over={over}px")

        # 极窄（比 8px 还窄）：宽度封 0 不能崩
        try:
            bw, lw, lx, off, over = measure(6)
            rec("7.3 极窄 6px 不崩（宽度封 0）", True, f"栏宽={bw} 标签宽={lw} x={lx}")
        except Exception as exc:                      # noqa: BLE001
            rec("7.3 极窄 6px 不崩（宽度封 0）", False, f"崩了 {exc!r}")

        # toast 宽度上限：窄窗下可能为负，_toast 不能崩
        win.resize(480, 640)
        app.processEvents()
        limit = win._toast_width_limit()
        rec("7.4 窄窗 toast limit 可读", isinstance(limit, int), f"limit={limit}")
        try:
            win._toast("已保存到 C:/Users/Somebody/AppData/Local/一个特别特别长的路径/文件名称.csv")
            app.processEvents()
            rec("7.5 窄窗长 toast 不崩", True, f"显示={win.lbl_toast.text()!r}")
        except Exception as exc:                      # noqa: BLE001
            rec("7.5 窄窗长 toast 不崩", False, f"崩了 {exc!r}")

        # 截图存档（窄/宽）
        out_dir = Path(__file__).resolve().parent.parent / "_check"
        out_dir.mkdir(exist_ok=True)
        for width, name in ((480, "narrow"), (1600, "wide")):
            win.resize(width, 640)
            app.processEvents()
            app.processEvents()
            win.grab().save(str(out_dir / f"qa_soc_bar_{name}.png"))

        # 换到没配 SOC 的车：应显示 -- 且 toast 不再腾地方
        customization.current_vehicle = lambda: "A08-2026"
        win._init_soc()
        app.processEvents()
        rec("7.6 未配车显示 --", win.lbl_soc.text() == "--", repr(win.lbl_soc.text()))
        rec("7.7 未配车 toast limit=0", win._toast_width_limit() == 0, "")
    finally:
        if win is not None:
            win.close()
            win.deleteLater()
            app.processEvents()
        rx_history._resolve_dir = real_dir
        customization.current_vehicle = real_pin
        if real_db is not None:
            rx_store.set_db_path(real_db)
        # 还原真实 QSettings（多余的键删掉、记过的写回）
        for k in list(settings.allKeys()):
            if k not in snap:
                settings.remove(k)
        for k, v in snap.items():
            settings.setValue(k, v)
        settings.sync()


# ---------------------------------------------------------------- 8. QSS 渲染
def q8_qss() -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_theme

    out_dir = Path(__file__).resolve().parent.parent / "_check"
    out_dir.mkdir(exist_ok=True)

    def build_bar():
        bar = QWidget()
        bar.setObjectName("bottomBar")
        row = QHBoxLayout(bar)
        plain = QLabel("未打开  0 B")     # 兄弟普通标签：验证权重压制是否真的失效
        plain.setParent(bar)
        row.addWidget(plain)
        lbl = QLabel("SOC 87.3%   总电压 401.2 V")
        lbl.setObjectName("socLabel")
        lbl.setParent(bar)
        row.addWidget(lbl)
        bar.resize(700, 32)
        bar.show()
        app.processEvents()
        return bar, plain, lbl

    def darkest(img: QImage, rect) -> int:
        x0, y0, w, h = rect
        best = 255
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                c = img.pixelColor(x, y)
                best = min(best, (c.red() + c.green() + c.blue()) // 3)
        return best

    def render_state(lbl: QLabel, state: str) -> tuple[int, int]:
        lbl.setProperty("state", state)
        lbl.style().unpolish(lbl)
        lbl.style().polish(lbl)
        lbl.update()
        app.processEvents()
        img = QImage(lbl.size(), QImage.Format_RGB32)
        img.fill(QColor("white"))
        p = QPainter(img)
        lbl.render(p, QPoint(0, 0))
        p.end()
        avg = darkest(img, (0, 0, img.width(), img.height()))
        return avg, img

    for scheme in ("light", "dark"):
        ui_theme.apply_scheme(scheme)
        app.processEvents()
        bar, plain, lbl = build_bar()
        shades = {}
        imgs = {}
        for state in ("ok", "warn", "off"):
            avg, img = render_state(lbl, state)
            shades[state] = avg
            imgs[state] = img
            img.save(str(out_dir / f"qa_soc_qss_{scheme}_{state}.png"))
        plain_dark = render_state(plain, "")[0] if False else None
        # 普通兄弟标签的渲染色（应等于 ink_2，用来证明 SOC 三态没被它压回）
        imgp = QImage(plain.size(), QImage.Format_RGB32)
        imgp.fill(QColor("white"))
        pp = QPainter(imgp)
        plain.render(pp, QPoint(0, 0))
        pp.end()
        plain_avg = darkest(imgp, (0, 0, imgp.width(), imgp.height()))

        rec(f"8.{'1' if scheme == 'light' else '3'} {scheme}: 三态渲染色两两不同",
            len(set(shades.values())) == 3, f"{shades}")
        rec(f"8.{'2' if scheme == 'light' else '4'} {scheme}: ok 比普通标签更极端",
            (shades["ok"] < plain_avg) if scheme == "light" else (shades["ok"] > plain_avg),
            f"ok={shades['ok']} 普通={plain_avg} warn={shades['warn']} off={shades['off']}")
        bar.deleteLater()
        app.processEvents()

    ui_theme.apply_scheme("light")


def main() -> int:
    for fn in (q7_layout, q8_qss):
        try:
            fn()
        except Exception as exc:              # noqa: BLE001
            import traceback
            traceback.print_exc()
            rec(fn.__name__ + " 整体异常", False, repr(exc))
    n_fail = sum(1 for _, s, _ in ROWS if s == "FAIL")
    for tag, status, detail in ROWS:
        print(f"[{status}] {tag}  —— {detail}")
    print(f"\n{len(ROWS) - n_fail}/{len(ROWS)} 通过；FAIL={n_fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
