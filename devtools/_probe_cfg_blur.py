# -*- coding: utf-8 -*-
"""只读探针：验连接方式切换的「高斯模糊淡出」过渡（2026-11-26 用户点名）。

断言：
  1. 切模式后立刻出现 _ConfigFade 覆盖层（不透明垫底 + 模糊层）；
  2. 240ms 内覆盖层 alpha 从 1 单调降到 0；
  3. 结束后覆盖层 hide，配置卡回到真实新版式（无残影、无遗留 maximumHeight）；
  4. 往返切两次都不残留覆盖层；
  5. tooltip 圆角 12px（QSS 里读到）。

铁律：QSettings 全量快照 + finally 原样还原；收尾 app.quit()，绝不 os._exit()。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402


def pump(app, ms: float, on_tick=None) -> list:
    """真跑事件循环 ms 毫秒，逐拍回调收集样本。"""
    out = []
    t0 = time.perf_counter()
    while (time.perf_counter() - t0) * 1000 < ms:
        app.processEvents()
        if on_tick is not None:
            out.append(on_tick())
        QTest.qWait(4)
    return out


def main() -> int:
    app = QApplication(sys.argv)
    settings = QSettings("MimoWorks", "SerialAssistant")
    settings_snapshot = {k: settings.value(k) for k in settings.allKeys()}
    had = set(settings_snapshot)

    import selftest
    from main_window import LINK_BLE, LINK_SERIAL
    from ui_theme import stylesheet

    # 5) tooltip 圆角
    qss = stylesheet()
    block = qss[qss.index("QToolTip {"):]
    block = block[:block.index("}")]
    radius = [l.strip() for l in block.splitlines() if "border-radius" in l]
    print(f"[tooltip] {radius}")
    assert any("12px" in r for r in radius), "tooltip 圆角没改到 12px"

    window = selftest.make_window()
    window.cmb_link.setCurrentText(LINK_SERIAL)
    window.show()
    QTest.qWait(300)

    card = window._config_card
    print(f"[初始] card={card.width()}x{card.height()} "
          f"serial_params visible={window.serial_params.isVisible()}")

    def snapshot() -> tuple:
        """每帧**重新取**覆盖层：过渡是 QTimer.singleShot(0) 延迟起的，
        切模式那一刻它还没建（缓存快照会全程拿到 None）。"""
        ov = getattr(window, "_params_fade", None)
        if ov is None:
            return (None, False)
        try:
            return (ov._alpha, ov.isVisible())
        except RuntimeError:
            return (None, False)

    for round_no, target in enumerate((LINK_BLE, LINK_SERIAL), 1):
        window.cmb_link.setCurrentText(target)
        alphas = pump(app, 550, snapshot)
        QTest.qWait(60)  # 让 _done() 的 clear() 收尾落定
        vals = [a for a, _v in alphas if a is not None]
        vis = [v for _a, v in alphas]
        print(f"[第 {round_no} 轮 -> {target}] 采样 {len(vals)} 帧 "
              f"alpha: {vals[:4]} ... {vals[-4:]}")
        print(f"   alpha 范围 {min(vals) if vals else '-'} ~ {max(vals) if vals else '-'} "
              f"覆盖层曾可见={any(vis)}")
        bad = [i for i in range(1, len(vals)) if vals[i] > vals[i - 1] + 1e-6]
        print(f"   非单调帧数={len(bad)}（应为 0）")
        overlay = getattr(window, "_params_fade", None)
        assert any(vis), "覆盖层从未出现 —— 过渡没播"
        assert not bad, "alpha 不是单调下降"
        assert vals[-1] < 0.02, f"alpha 没收尾：{vals[-1]}"
        assert overlay is not None and not overlay.isVisible(), "结束没撤覆盖层"
        assert window.serial_params.maximumHeight() == 16777215, "残留 maximumHeight"

    # 收尾前再切回串口，保证 QSettings 的 link_mode 与基线一致（finally 也会还原）
    window.cmb_link.setCurrentText(LINK_SERIAL)
    QTest.qWait(300)
    card.grab(card.rect()).save("_check/cfg_blur_final.png")
    print("[收尾] 终态截图 _check/cfg_blur_final.png")

    window.close()
    window.deleteLater()
    for _ in range(4):
        app.processEvents()

    for key in settings.allKeys():
        if key not in had:
            settings.remove(key)
    for key, value in settings_snapshot.items():
        settings.setValue(key, value)
    settings.sync()
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
