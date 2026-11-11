# -*- coding: utf-8 -*-
"""性能模式验证：整流逻辑 / 主窗口接入 / 隐藏手势 / 提示浮层。

⚠️ 全部在离屏平台跑（``QT_QPA_PLATFORM=offscreen``），不弹真窗口、不写用户配置：
``_set_perf_mode`` 只改内存状态，性能模式本身也**不持久化**（每次启动是关的）。

用法::

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe devtools/_verify_perf_mode.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QEventLoop, QPointF, Qt, QTimer   # noqa: E402
from PySide6.QtGui import QMouseEvent                                  # noqa: E402
from PySide6.QtWidgets import QApplication                             # noqa: E402

import perf_mode                                                       # noqa: E402

_fail: list[str] = []
_count = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _count
    _count += 1
    if ok:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _fail.append(name)


def spin(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


# ---------------------------------------------------------------- 纯逻辑

SAMPLE = """Voltage1:
01.4211   02.4211   03.3051 X   04.5209   05.0000
Temperature1:
1.25    2.25    3.99
BMS Error
errorTime=0
1.19 Under Voltage
5.8 Over Temp
Communication Error
COM0=0
AIR+=0,AIR-=0,PRE=0
V=0.00,I=-250.00
CVMax=5433, Min=0"""


def check_logic() -> None:
    print("\n[1] 整流逻辑（纯文本）")
    s = perf_mode.Sanitizer(n_cells=125)
    out = s.feed(SAMPLE)

    check("坏点 <1500mV 被替换", "03.3051" not in out and "03.4211" in out, out)
    check("超量程 >4600mV 被替换", "04.5209" not in out and "04.4211" in out, out)
    check("0V 坏点被替换", "05.0000" not in out, out)
    check("坏点标记 X 被去掉", " X " not in out, out)
    check("BMS Error → BMS OK", "BMS OK" in out and "BMS Error" not in out, out)
    check("errorTime 明细删除", "errorTime" not in out, out)
    check("Under/Over 明细删除", "Under Voltage" not in out and "Over Temp" not in out, out)
    check("Communication Error 删除", "Communication Error" not in out, out)
    check("接触器/COM 状态保留", "COM0=0" in out and "AIR+=0" in out, out)
    check("V= 用串数推算", "V=526.38" in out, out)
    check("CVMax / Min 用本轮极值", "CVMax=4211" in out or "CVMax=4212" in out, out)
    check("Min=0 不复存在", "Min=0" not in out, out)

    s2 = perf_mode.Sanitizer(n_cells=125)
    once = s2.feed(SAMPLE)
    check("幂等", once == s2.feed(once), "二次整流改变了内容")

    # 时间戳里的 02:34 长得像采样点，绝不能被当成电压吃掉
    s3 = perf_mode.Sanitizer(n_cells=125)
    stamped = "[2026-11-11 02:34:35]\nVoltage1:\n01.4211 02.4211"
    out3 = s3.feed(stamped)
    check("时间戳原样保留", out3.startswith("[2026-11-11 02:34:35]"), out3)

    # 整段没有合理值时**不许编造**
    s4 = perf_mode.Sanitizer(n_cells=125)
    out4 = s4.feed("Voltage1:\n01.0000   02.0000")
    check("无参考值时不编造", "01.0000" in out4 and "02.0000" in out4, out4)

    # 分包：前半段给参考值，后半段全坏 → 用上一段的参考值
    s5 = perf_mode.Sanitizer(n_cells=125)
    s5.feed("Voltage1:\n01.4200   02.4200")
    out5 = s5.feed("Voltage2:\n01.0000   02.0000")
    check("跨块参考值兜底", "01.4200" in out5 and "02.4200" in out5, out5)

    # 温度段：25 / 25 正常，99 越界 → 25
    s6 = perf_mode.Sanitizer(n_cells=125)
    out6 = s6.feed("Temperature1:\n1.25    2.25    3.99")
    check("温度越界替换", "3.25" in out6 and "3.99" not in out6, out6)

    # 正常的轻微不均衡一个字符都不许动
    s7 = perf_mode.Sanitizer(n_cells=125)
    normal = "Voltage1:\n01.4211   02.4180   03.4230"
    check("正常数据零改动", s7.feed(normal) == normal, s7.feed(normal))

    # 真机日志回放：整流后不应再有超量程电压 / BMS Error / X
    real = ("C:/Users/Lenovo/AppData/Local/CQUFSAE-SerialAssistant/logs/"
            "接收历史-E74-2026.log")
    if os.path.exists(real):
        import io
        import re
        text = io.open(real, encoding="utf-8", errors="replace").read()
        s8 = perf_mode.Sanitizer(n_cells=125)
        cleaned = s8.feed(text)
        over = [int(m.group("val")) for m in perf_mode.CELL_TOKEN.finditer(cleaned)
                if int(m.group("val")) > perf_mode.CELL_MAX_MV]
        check("真机日志：无超量程电压", not over, f"{len(over)} 个残留")
        check("真机日志：无 BMS Error", "BMS Error" not in cleaned)
        check("真机日志：无 X 标记",
              not re.search(r"(?<!\S)X(?!\S)", cleaned))
        check("真机日志：所有 BMS 行都是 OK",
              set(re.findall(r"(?m)^BMS\s+(\w+)", cleaned)) <= {"OK"})
    else:
        print("  SKIP  真机日志不存在（换台机器跑就跳过这一段）")


# ---------------------------------------------------------------- 主窗口

def check_window(app: QApplication) -> object:
    print("\n[2] 主窗口接入")
    import main_window

    window = main_window.MainWindow()
    window.resize(1200, 820)
    window.show()
    spin(120)

    check("默认关闭（不持久化）", window._perf_on is False)

    raw = ("Voltage1:\n01.4211   02.4211   03.3051 X   04.5209\n"
           "BMS Error\nerrorTime=0\n1.19 Under Voltage\n"
           "COM0=0\nV=0.00,I=-250.00\nCVMax=5433, Min=0")

    window._emit_block(raw)
    spin(30)
    plain = window.txt_rx.toPlainText()
    check("关闭时原样显示", "03.3051" in plain and "BMS Error" in plain, plain[:200])

    # 测试不依赖本机到底选没选车：串数直接注入，这样 V= 的推算链路一定能验到
    window._perf_sanitizer.set_n_cells(125)
    window._set_perf_mode(True)
    check("进入后状态置位", window._perf_on is True)
    check("进入时清屏", window.txt_rx.toPlainText() == "")

    window._emit_block(raw)
    spin(30)
    plain = window.txt_rx.toPlainText()
    check("开启后坏点消失", "03.3051" not in plain and "04.5209" not in plain, plain)
    check("开启后 BMS OK", "BMS OK" in plain and "BMS Error" not in plain, plain)
    check("开启后明细不显示", "errorTime" not in plain, plain)
    check("开启后 V= 合理", "V=0.00" not in plain, plain)
    # 注入 0 串数（= 没选车）时必须**不推算**——猜串数比露着 0V 更糟
    window._perf_sanitizer.set_n_cells(0)
    window._clear_receive()
    window._emit_block(raw)
    spin(30)
    check("无串数时不推算总压", "V=0.00" in window.txt_rx.toPlainText())
    window._perf_sanitizer.set_n_cells(125)

    # 关掉之后立刻回到真实值
    window._set_perf_mode(False)
    window._emit_block(raw)
    spin(30)
    plain = window.txt_rx.toPlainText()
    check("退出后恢复原始数据", "03.3051" in plain and "BMS Error" in plain, plain)

    # 浮层：几何铺满客户区、模糊图非空、动画结束后自动隐藏
    window._set_perf_mode(True)
    overlay = window._perf_overlay
    check("浮层已创建", overlay is not None)
    check("浮层铺满客户区", overlay.geometry() == window.rect(),
          f"{overlay.geometry()} vs {window.rect()}")
    check("模糊图已生成", overlay._bg is not None and not overlay._bg.isNull())
    if overlay._bg is not None and not overlay._bg.isNull():
        same = (overlay._bg.size() == window.grab().size())
        check("模糊图与原窗口同尺寸", same, str(overlay._bg.size()))
    check("浮层不吃鼠标", overlay.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents))
    spin(420)
    check("淡入完成：卡片全不透明", overlay._card_alpha > 0.99)
    check("淡入完成：缩放到位", abs(overlay._card_scale - 1.0) < 0.01)
    spin(1500)
    check("自动消失（不需要用户确认）", not overlay.isVisible(), "浮层还在")

    # 绘制不崩：浅/深两套主题各抓一帧（grab 会走 paintEvent）
    from ui_theme import apply_scheme
    for scheme in ("dark", "light"):
        apply_scheme(scheme)
        window._set_perf_mode(True)
        spin(60)
        shot = window.grab()
        check(f"{scheme} 主题下浮层可绘制",
              not shot.isNull() and shot.width() > 0)
        window._set_perf_mode(False)
        spin(320)
    apply_scheme("dark")

    window.close()
    window.deleteLater()
    spin(60)
    return window


# ---------------------------------------------------------------- 手势

def check_gesture(app: QApplication) -> None:
    print("\n[3] 队标手势")
    import main_window

    window = main_window.MainWindow()
    window.resize(1200, 820)
    window.show()
    spin(120)
    brand = window.brand_header
    rect = brand._drawn_lockup_rect()
    check("队标热区非空", not rect.isEmpty(), str(rect))
    center = QPointF(rect.center())

    def tap(pos: QPointF | None = None) -> None:
        point = center if pos is None else pos
        event = QMouseEvent(QEvent.Type.MouseButtonPress, point,
                            Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        brand.mousePressEvent(event)

    tap()
    spin(380)
    check("单击不切换", window._perf_on is False)

    tap()
    tap()
    spin(380)
    check("双击进入", window._perf_on is True)

    # 双击后手里多按的那一下不能立刻算成三击
    tap()
    spin(120)
    tap()
    spin(380)
    check("切换后短暂锁生效", window._perf_on is True)

    spin(500)
    tap()
    tap()
    tap()
    spin(380)
    check("三击退出", window._perf_on is False)

    spin(500)
    far = QPointF(brand.width() - 12.0, brand.height() / 2.0)
    for _ in range(3):
        tap(far)
    spin(380)
    check("热区外点击无效", window._perf_on is False)

    window.close()
    window.deleteLater()
    spin(60)


def main() -> int:
    app = QApplication(sys.argv)
    check_logic()
    check_window(app)
    check_gesture(app)
    print(f"\n{'=' * 46}")
    if _fail:
        print(f"FAILED：{len(_fail)} / {_count}")
        for name in _fail:
            print("  -", name)
        return 1
    print(f"OK：{_count} 项全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
