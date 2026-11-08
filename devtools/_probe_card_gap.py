"""探针：量普通窗口的四周边距（用户报「四周有空隙」）。

流程照 main.py：show → apply_startup_state（全屏）→ 等动画落定 → 截图；
再 _on_escape（还原到普通态）→ 等几何补间落定 → 截图 → 量四周边距。
⛔ QSettings 全量快照 + finally 还原（项目铁律）。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings                       # noqa: E402
from PySide6.QtWidgets import QApplication                 # noqa: E402

app = QApplication([])
st = QSettings("MimoWorks", "SerialAssistant")
snapshot = {k: st.value(k) for k in st.allKeys()}
CHECK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_check")


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def measure(tag: str, win) -> None:
    path = os.path.join(CHECK, f"card_{tag}.png")
    pix = win.grab()
    pix.save(path)
    im = pix.toImage()
    dpr = im.width() / max(1, win.width())
    m = win._inner_lay.contentsMargins()
    print(f"[{tag}] 窗口 {win.width()}x{win.height()} level={win._compact_level} "
          f"inner(L{m.left()} T{m.top()} R{m.right()} B{m.bottom()}) "
          f"shadow_pad={getattr(win, '_cards', [None])[0] and win._cards[0]._shadow_pad}")

    def rgb(x, y):
        c = im.pixelColor(int(x * dpr), int(y * dpr))
        return (c.red(), c.green(), c.blue())

    base = rgb(2, 2)
    left = top = None
    for d in range(1, 60):
        if left is None and rgb(d, win.height() * 0.5) != base:
            left = d
        if top is None and rgb(win.width() * 0.4, d) != base:
            top = d
    print(f"[{tag}] 底色{base}  左侧内容起点 x={left}  顶部内容起点 y={top}")


try:
    from main_window import MainWindow                      # noqa: E402

    win = MainWindow()
    win.show()
    win.apply_startup_state()
    pump(1.2)
    measure("full", win)

    win._on_escape()          # 全屏 → 普通（走几何补间）
    pump(1.0)
    measure("normal", win)

    # -- 悬停浮起：检查顶部圆角有没有被控件矩形裁掉 --------------------
    card = win._cards[0]
    card._lift = 1.0          # 直接置满浮起量（不走补间，纯看绘制）
    card.update()
    pump(0.25)
    cim = card.grab().toImage()
    cdpr = cim.width() / max(1, card.width())
    print(f"hover 态 card {card.width()}x{card.height()} shadow_pad={card._shadow_pad}")
    for y in range(0, 6):
        c = cim.pixelColor(int(card.width() * 0.5 * cdpr), int(y * cdpr))
        print(f"  y={y}: RGB({c.red()},{c.green()},{c.blue()})")
    print("（y=0 应是窗口底色、y≥1 起是卡面色 → 顶部圆角有余量、没被裁）")
finally:
    # ⛔ 先把窗口的异步定时器（_place_edit 的 0/80/260/520ms、动画 Tween）
    #    全部跑完再还原快照 —— 否则它们在 finally 之后才触发
    #    _remember_normal_geometry，把探针窗口几何写回真实配置
    #    （2026-11-02 实测：geometry 被写成 1600x1000）。
    pump(0.8)
    try:
        win.hide()
    except Exception:                                      # noqa: BLE001
        pass
    for key in st.allKeys():
        if key not in snapshot:
            st.remove(key)
    for key, value in snapshot.items():
        st.setValue(key, value)
    st.sync()
    print("QSettings 已还原")
