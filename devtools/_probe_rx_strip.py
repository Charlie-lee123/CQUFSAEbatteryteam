# -*- coding: utf-8 -*-
r"""接收区「最大化 + 流式收数 → 顶部小条」调查探针。

用户报的现象：接收区**最大化**、串口正在流式收数时，接收卡片顶部（正文第一行
上方）出现一条很窄（约 4~6px、横跨卡片宽度）的残影条；**按下暂停后消失**。

本脚本只做三件事，不碰产品代码：

  A. 量：把 `_rx_opts_row`（「显示时间戳 / HEX / 编码」工具栏行）在最大化态
     结束后的**残留几何约束**打出来（height / min / max / geometry / isVisible）；
  B. 看：用 ``PrintWindow(PW_RENDERFULLCONTENT)`` 抓真窗口、**拼 Magnified
     裁切图**存进 `_check/`，把卡片顶部那几十逻辑像素放大到能肉眼判读；
  C. 归因：做四组对照（空闲 / 流式 / 暂停 / 恢复）+ 一个**阳性对照**
     （最大化态下强行把工具栏 show() 成自然高，看条子是不是它的顶行）。

场景覆盖：
  S1 最大化 + 空闲       —— 残影是否**本来就在**
  S2 最大化 + 流式收数   —— 用户报的场景
  S3 最大化 + 暂停       —— 用户说"暂停就消失"
  S4 暂停后再恢复        —— 是否复发
  S5 流式**中途**最大化   —— 动画期间灌数据是不是必要条件
  S6 普通态 + 流式        —— 对照组
  S7 阳性对照：最大化态强行显示工具栏

用法：
    QT_QPA_PLATFORM=windows .venv\\Scripts\\python.exe devtools/_probe_rx_strip.py

产物：_check/rx_strip_*.png（放大 4 倍的裁切图）、_check/rx_strip.txt（文本报告）

⚠️ 跑前/跑后都要复查 QSettings 里的 custom/startup_maximized 与 geometry；
   ⛔ 绝不能和 selftest 并行（两边抢同一份配置）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

_CHECK = os.path.join(_ROOT, "_check")


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def capture(hwnd: int) -> "tuple[object, int, int, bool]":
    """PrintWindow(PW_RENDERFULLCONTENT) 抓整窗 → (QImage, w, h, ok)。物理像素、自上而下。"""
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None, 0, 0, False
    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(memdc, bmp)
    ok = user32.PrintWindow(hwnd, memdc, 2)          # 2 = PW_RENDERFULLCONTENT
    info = BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.biWidth, info.biHeight = width, -height
    info.biPlanes, info.biBitCount, info.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memdc, bmp, 0, height, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd, hdc)
    from PySide6.QtGui import QImage
    img = QImage(bytes(buf.raw), width, height, QImage.Format.Format_ARGB32).copy()
    return img, width, height, bool(ok)


def capture_screen(hwnd: int) -> "tuple[object, int, int]":
    """从**屏幕 DC** BitBlt 窗口矩形 → (QImage, w, h)。

    与 PrintWindow 的本质区别：这是 DWM 合成后**屏幕上真实显示**的像素，
    不会反过来要求窗口重绘 —— 如果"条子"是陈旧像素/未被重绘的区域，
    PrintWindow（会触发重绘）看不见它，屏幕抓图看得见。
    """
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None, 0, 0
    dc = user32.GetDC(None)                          # 屏幕 DC
    memdc = gdi32.CreateCompatibleDC(dc)
    bmp = gdi32.CreateCompatibleBitmap(dc, width, height)
    gdi32.SelectObject(memdc, bmp)
    SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
    gdi32.BitBlt(memdc, 0, 0, width, height, dc, rect.left, rect.top,
                 SRCCOPY | CAPTUREBLT)
    info = BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.biWidth, info.biHeight = width, -height
    info.biPlanes, info.biBitCount, info.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memdc, bmp, 0, height, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(None, dc)
    from PySide6.QtGui import QImage
    img = QImage(bytes(buf.raw), width, height, QImage.Format.Format_ARGB32).copy()
    return img, width, height


def pump(app, ms: float) -> None:
    """让真事件循环跑 ms 毫秒（Qt 的 UpdateRequest / 绘制在这一步发生）。"""
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.002)


def _frame(n: int) -> bytes:
    return (f"Voltage{n}:\n"
            + " ".join(f"{j}.3820" for j in range(1, 26)) + "\n\n"
            + f"Temperature{n}:\n1.22 2.22 3.22 4.25\n\n").encode("utf-8")


class RowSpy:
    """监听 `_rx_opts_row` 的 Show/Hide/Resize，看流式期间有没有人把它亮出来。"""

    def __init__(self, row) -> None:
        from PySide6.QtCore import QEvent, QObject
        self.row = row
        self.log: list[str] = []
        self.t0 = time.perf_counter()
        types = {int(QEvent.Type.Show): "Show", int(QEvent.Type.Hide): "Hide",
                 int(QEvent.Type.ShowToParent): "ShowToParent",
                 int(QEvent.Type.HideToParent): "HideToParent",
                 int(QEvent.Type.Resize): "Resize"}
        self._names = types
        outer = self

        class _Filter(QObject):
            def eventFilter(self, _obj, ev):        # noqa: N802
                name = outer._names.get(int(ev.type()))
                if name:
                    outer.log.append(
                        f"{name} h={outer.row.height()} "
                        f"vis={int(outer.row.isVisible())} "
                        f"t={(time.perf_counter() - outer.t0) * 1000:.0f}ms")
                return False

        self._filter = _Filter()
        row.installEventFilter(self._filter)

    def reset(self) -> None:
        self.log.clear()

    def counts(self) -> str:
        def want(prefix: str) -> int:
            return sum(1 for s in self.log if s.startswith(prefix))
        return (f"Show={want('Show')} Hide={want('Hide')} Resize={want('Resize')}"
                f" ShowToParent={want('ShowToParent')}")


def row_state(window) -> dict:
    """把工具栏行当前的**全部**状态量化（残影嫌疑对象就是它）。"""
    from PySide6.QtCore import Qt
    row = window._rx_opts_row
    try:
        g = row.geometry()
        return {
            "isVisible": row.isVisible(),
            "WA_WState_Hidden": bool(row.testAttribute(Qt.WidgetAttribute.WA_WState_Hidden)),
            "height": row.height(),
            "minH": row.minimumHeight(),
            "maxH": row.maximumHeight(),
            "geom": (g.x(), g.y(), g.width(), g.height()),
            "content_h": window._opts_content.height(),
            "txt_rx_top": _geom_tuple(window.txt_rx)[1],
        }
    except RuntimeError as exc:                      # 已析构
        return {"error": str(exc)}


def _geom_tuple(w):
    g = w.geometry()
    return (g.x(), g.y(), g.width(), g.height())


def _global_top(w) -> int:
    from PySide6.QtCore import QPoint
    return w.mapToGlobal(QPoint(0, 0)).y()


def crop_and_magnify(img, x0: int, y0: int, w: int, h: int, zoom: int = 4):
    """从抓图里裁一块、**纵向放大** zoom 倍（条子只有几个物理像素，肉眼看不见）。"""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPixmap
    piece = QPixmap.fromImage(img.copy(QRect(x0, y0, w, h)))
    return piece.scaled(w, h * zoom)


def row_ink(img, y: int, x0: int, x1: int) -> float:
    """第 y 行（物理像素）在 [x0,x1) 范围内的"墨量"：偏离该行众数色的像素占比。

    背景（毛玻璃）自带磨砂颗粒，所以看的是**占比**而不是是否绝对均匀。
    """
    from collections import Counter
    colors = Counter()
    step = 2
    for x in range(x0, x1, step):
        colors[img.pixelColor(x, y).name()] += 1
    if not colors:
        return 0.0
    mode, _ = colors.most_common(1)[0]
    mr, mg, mb = int(mode[1:3], 16), int(mode[3:5], 16), int(mode[5:7], 16)
    off = 0
    total = 0
    for x in range(x0, x1, step):
        c = img.pixelColor(x, y)
        total += 1
        if abs(c.red() - mr) + abs(c.green() - mg) + abs(c.blue() - mb) > 24:
            off += 1
    return off / max(1, total)


def row_sig(img, y: int, x0: int, x1: int) -> list[tuple[int, int, int]]:
    """第 y 行在 [x0,x1) 上的**逐列 RGB 指纹**（每隔 4 个物理像素取一点）。"""
    sig = []
    for x in range(x0, x1, 4):
        c = img.pixelColor(x, y)
        sig.append((c.red(), c.green(), c.blue()))
    return sig


def sig_diff(a, b) -> float:
    """两条行指纹的平均通道差（0=完全相同）。"""
    if not a or not b or len(a) != len(b):
        return -1.0
    total = sum(abs(p[0] - q[0]) + abs(p[1] - q[1]) + abs(p[2] - q[2])
                for p, q in zip(a, b))
    return total / (3.0 * len(a))


def ink_profile(img, x0: int, y_top: int, rows: int) -> list[float]:
    return [row_ink(img, y_top + r, x0, x0 + 500) for r in range(rows)]


def fmt_profile(label: str, profile: list[float], y_top: int) -> list[str]:
    out = [f"    [{label}] y_phys {y_top}.. 墨量："]
    cells = [f"{y_top + i}:{v:.2f}" for i, v in enumerate(profile)]
    for i in range(0, len(cells), 8):
        out.append("      " + "  ".join(cells[i:i + 8]))
    return out


def scenario_shots(app, window, hwnd, name: str, count: int = 6,
                   gap_ms: float = 130.0, feed=None) -> list:
    """边 shoot 边（可选）喂数据。返回抓到的 QImage 列表。"""
    shots = []
    for i in range(count):
        img, _w, _h, _ok = capture(hwnd)
        if img is not None:
            shots.append(img)
        if feed is not None:
            for _ in range(4):
                window._on_received(feed())
                pump(app, 9)
        else:
            pump(app, gap_ms)
    return shots


def main() -> int:
    from PySide6.QtCore import QPoint, QSettings
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    os.makedirs(_CHECK, exist_ok=True)

    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    scheme = os.environ.get("RX_STRIP_SCHEME") or detect_system_scheme()
    apply_scheme(scheme)
    report_scheme = scheme
    if os.environ.get("RX_STRIP_MATERIAL"):          # 覆盖材质（liquid/frosted）
        try:
            from ui_theme import MATERIAL_FROSTED, MATERIAL_LIQUID, set_material
            set_material(MATERIAL_LIQUID
                         if os.environ["RX_STRIP_MATERIAL"] == "liquid"
                         else MATERIAL_FROSTED)
            report_scheme += f" + material={os.environ['RX_STRIP_MATERIAL']}"
        except Exception as exc:                     # noqa: BLE001
            report_scheme += f" + material 覆盖失败: {exc}"
    app.setFont(ui_font(12.5))
    from ui_anim import display_max_hz, set_frame_hz
    set_frame_hz(display_max_hz())

    from main_window import MainWindow
    window = MainWindow()
    lines: list[str] = []
    try:
        window._port_watch.stop()
        window.show()
        window.apply_startup_state()
        pump(app, 700)
        hwnd = int(window.winId())

        spy = RowSpy(window._rx_opts_row)

        report = lines.append
        report(f"\n== 0. 基线（普通态）scheme={report_scheme} ==")
        report(f"    opts_row = {row_state(window)}")
        report(f"    expanded = {window._expanded}  window={_geom_tuple(window)}")

        # ---------- 准备数据：普通态先灌几轮，滚动条有行程 ----------
        window.chk_timestamp.setChecked(True)
        window._clear_receive()
        n = [0]

        def feed() -> bytes:
            n[0] += 1
            return _frame(n[0])

        for _ in range(6):
            window._on_received(feed())
            pump(app, 40)
        pump(app, 400)

        row = window._rx_opts_row
        caps: dict[str, list] = {}
        scaps: dict[str, list] = {}
        meta: dict[str, dict] = {}

        def shoot(scen: str, count: int = 6, feed_mode: str = "idle") -> None:
            """抓 count 张图（PrintWindow + 屏幕 BitBlt 各一份）。

            feed_mode: idle 不喂 / stream 边喂边抓 / burst 高速灌到块数上限。
            """
            imgs, simgs = [], []
            for i in range(count):
                img, _w, _h, _ok = capture(hwnd)
                simg, _sw, _sh = capture_screen(hwnd)
                if img is not None:
                    imgs.append(img)
                if simg is not None:
                    simgs.append(simg)
                if feed_mode == "stream":
                    for _k in range(4):
                        window._on_received(feed())
                        pump(app, 9)
                elif feed_mode == "burst":
                    for _k in range(6):
                        window._on_received(feed())
                        window._flush_rx()
                        pump(app, 7)
                else:
                    pump(app, 120)
            caps[scen] = imgs
            scaps[scen] = simgs
            meta[scen] = {"state": row_state(window), "spy": spy.counts(),
                          "row_y_phys": rows_phys(row)[1]}
            # 全分辨率留档（最后一张），供离线像素取证
            if imgs:
                imgs[-1].save(os.path.join(_CHECK, f"rx_strip_{scen}_full.png"))
            if simgs:
                simgs[-1].save(os.path.join(_CHECK, f"rx_strip_{scen}_screen.png"))

        def rows_phys(w) -> "tuple[int, int]":
            """控件左上角在**抓图位图**里的物理坐标。"""
            g = w.mapToGlobal(QPoint(0, 0))
            f = window.frameGeometry().topLeft()
            return (int((g.x() - f.x()) * dpr_img), int((g.y() - f.y()) * dpr_img))

        def dump(scen: str, zoom: int = 4) -> None:
            """把该场景的每一行墨量 + 放大裁切图写出来（PrintWindow 与屏幕各一份）。"""
            for kind, store in (("pw", caps), ("scr", scaps)):
                imgs = store.get(scen) or []
                if not imgs:
                    report(f"    [{scen}/{kind}] 没抓到图")
                    continue
                rx, ry = rows_phys(row)
                report(f"    [{scen}/{kind}] opts_row 在抓图中的物理左上 = ({rx}, {ry})")
                for i, img in enumerate(imgs[-2:]):
                    piece = crop_and_magnify(img, max(0, rx), max(0, ry - 24),
                                             700, 60, zoom)
                    piece.save(os.path.join(
                        _CHECK, f"rx_strip_{scen}_{kind}_{i}.png"))
                    for line in fmt_profile(
                            f"{scen}/{kind}#{i} ry-24..",
                            ink_profile(img, max(0, rx), max(0, ry - 24), 60),
                            max(0, ry - 24)):
                        report(line)

        # ---------- S1: 最大化 + 空闲 ----------
        spy.reset()
        report("\n== S1 最大化 + 空闲（不喂数据）==")
        window._toggle_expand()
        pump(app, 900)
        report(f"    dpr={window.devicePixelRatioF()}  expanded={window._expanded}")
        report(f"    window={_geom_tuple(window)}")
        img0, w0, h0, ok0 = capture(hwnd)
        dpr_img = h0 / max(1, window.frameGeometry().height())
        report(f"    PrintWindow ok={ok0} {w0}x{h0}  dpr_img={dpr_img:.3f}")
        report(f"    opts_row = {row_state(window)}")
        report(f"    spy: {spy.counts()} | {spy.log[-4:] if spy.log else '(无事件)'}")
        report(f"    opts_row 物理左上={rows_phys(row)}  txt_rx 物理左上={rows_phys(window.txt_rx)}")
        shoot("S1", 4)
        dump("S1")

        # ---------- S2: 最大化 + 流式 ----------
        spy.reset()
        report("\n== S2 最大化 + 流式收数（用户报的场景）==")
        shoot("S2", 10, "stream")
        report(f"    opts_row = {meta['S2']['state']}")
        report(f"    spy: {meta['S2']['spy']} | {spy.log[-6:] if spy.log else '(无事件)'}")
        dump("S2")

        # ---------- S3: 暂停 ----------
        report("\n== S3 最大化 + 暂停（用户说条子消失）==")
        window._toggle_pause()
        pump(app, 900)
        shoot("S3", 4, "stream")
        report(f"    opts_row = {meta['S3']['state']}")
        report(f"    spy: {meta['S3']['spy']}")
        dump("S3")

        # ---------- S4: 恢复 ----------
        report("\n== S4 恢复滚动 ==")
        window._toggle_pause()
        pump(app, 900)
        shoot("S4", 6, "stream")
        report(f"    opts_row = {meta['S4']['state']}")
        dump("S4")

        # ---------- S9: 高速灌到 maximumBlockCount 上限（真实长时间收数的常态） ----------
        report("\n== S9 高速灌数到块数上限（流式长跑后追加会从顶部裁块）==")
        report(f"    maximumBlockCount={window.txt_rx.maximumBlockCount()} "
               f"blocks before={window.txt_rx.document().blockCount()}")
        shoot("S9", 120, "burst")
        report(f"    blocks after={window.txt_rx.document().blockCount()} "
               f"scrollbar={window.txt_rx.verticalScrollBar().value()}/"
               f"{window.txt_rx.verticalScrollBar().maximum()}")
        report(f"    opts_row = {meta['S9']['state']}")
        dump("S9")

        # ---------- S8: 流式期间强行整窗重绘（验证"陈旧像素"假说） ----------
        report("\n== S8 流式 + repaint/update 全量重绘（陈旧像素假说：重绘后条子应消失）==")
        for _ in range(6):
            window._on_received(feed())
            pump(app, 12)
        window.txt_rx.update()
        window._rx_card.update()
        window.centralWidget().repaint()
        pump(app, 300)
        shoot("S8", 3, "stream")
        dump("S8")

        # ---------- S10: 流式**中途**最大化（动画 260ms + 合并窗口期间一直在收数） ----------
        report("\n== S10 流式中途点最大化（转场动画期间持续收数）==")
        if window._expanded:                     # 先回普通态，保证下面是"展开"方向
            window._toggle_expand()
            pump(app, 1100)
        window._clear_receive()
        n[0] = 0
        pump(app, 300)
        from PySide6.QtCore import QTimer
        feeder = QTimer(window)
        feeder.setInterval(8)
        feeder.timeout.connect(lambda: window._on_received(feed()))
        feeder.start()
        window._toggle_expand()          # 转场期间 feeder 一直在喂数据
        pump(app, 1600)
        feeder.stop()
        report(f"    expanded={window._expanded}")
        report(f"    opts_row = {row_state(window)}")
        report(f"    spy: {spy.counts()} | {spy.log[-6:] if spy.log else '(无事件)'}")
        shoot("S10", 8, "stream")
        report(f"    spy: {meta['S10']['spy']}")
        dump("S10")

        # ---------- S11: 流式 + 用户向上翻页（视图被追底来回拽，滚动方向高频反转） ----------
        report("\n== S11 最大化 + 流式 + 反复向上翻（追底与用户滚动互相拉扯）==")
        bar = window.txt_rx.verticalScrollBar()

        class _Yanker:
            stop = False

        yanker = _Yanker()

        def _yank() -> None:
            if yanker.stop or window._paused:
                return
            bar.setValue(max(0, bar.maximum() - 600))
            QTimer.singleShot(90, _yank)

        QTimer.singleShot(60, _yank)
        shoot("S11", 30, "stream")
        yanker.stop = True
        report(f"    opts_row = {meta['S11']['state']}")
        report(f"    spy: {meta['S11']['spy']}")
        dump("S11")

        # ---------- S12: **真串口路径**（SerialWorker loop:// 线程 → 队列信号） ----------
        report("\n== S12 最大化 + 真实串口路径收数（loop:// 读线程 → queued 信号）==")
        if window._expanded:
            window._toggle_expand()
            pump(app, 1100)
        window._clear_receive()
        n[0] = 0
        from serial_io import SerialWorker
        worker = SerialWorker("loop://", baudrate=115200)
        worker.received.connect(window._on_received)   # 与主窗口同一条接线
        opened = []
        worker.opened.connect(lambda: opened.append(True))
        worker.open()
        pump(app, 1200)
        report(f"    loop:// opened={bool(opened)} running={worker.isRunning()}")
        n_feed = [0]
        feeder = QTimer(window)
        feeder.setInterval(8)

        def _real_feed() -> None:
            n_feed[0] += 1
            worker.write(_frame(n_feed[0]))

        feeder.timeout.connect(_real_feed)
        feeder.start()
        window._toggle_expand()          # 真数据流里点最大化
        pump(app, 2200)
        # 暂停再恢复（用户操作序列）
        window._toggle_pause()
        pump(app, 800)
        shoot("S12", 3, "idle")
        window._toggle_pause()
        pump(app, 1200)
        feeder.stop()
        worker.stop()
        pump(app, 400)
        report(f"    opts_row = {meta['S12']['state']}")
        report(f"    spy: {meta['S12']['spy']}")
        dump("S12")

        # ---------- S13: 真实路径长跑浸泡（分钟级收数 + 周期性抓图对比） ----------
        soak_s = float(os.environ.get("RX_STRIP_SOAK", "0") or 0)
        if soak_s > 0:
            report(f"\n== S13 真实路径浸泡 {soak_s}s（最大化 + loop:// 持续收数）==")
            if window._expanded:
                window._toggle_expand()
                pump(app, 1100)
            window._clear_receive()
            n[0] = 0
            worker2 = SerialWorker("loop://", baudrate=115200)
            worker2.received.connect(window._on_received)
            opened2 = []
            worker2.opened.connect(lambda: opened2.append(True))
            worker2.open()
            pump(app, 1000)
            nf = [0]
            soak_timer = QTimer(window)
            soak_timer.setInterval(6)

            def _soak_feed() -> None:
                nf[0] += 1
                worker2.write(_frame(nf[0]))

            soak_timer.timeout.connect(_soak_feed)
            soak_timer.start()
            window._toggle_expand()
            deadline = time.perf_counter() + soak_s
            k = 0
            while time.perf_counter() < deadline:
                pump(app, 500)
                k += 1
                img, _w, _h, _ok = capture(hwnd)
                if img is not None:
                    img.save(os.path.join(_CHECK, f"rx_strip_SOAK_{k:03d}.png"))
            soak_timer.stop()
            worker2.stop()
            report(f"    blocks={window.txt_rx.document().blockCount()} "
                   f"scrollbar={window.txt_rx.verticalScrollBar().value()}/"
                   f"{window.txt_rx.verticalScrollBar().maximum()}")
            report(f"    opts_row = {row_state(window)}")
            report(f"    存图 rx_strip_SOAK_*.png × {k}")
            pump(app, 300)

        # ---------- S6: 退出最大化 + 流式（对照组） ----------
        report("\n== S6 回到普通态 + 流式（对照组）==")
        window._toggle_expand()
        pump(app, 1200)
        report(f"    opts_row = {row_state(window)}")
        shoot("S6", 4, "stream")
        report(f"    spy: {meta['S6']['spy']}")
        dump("S6")

        # ---------- S7: 阳性对照：最大化态强行显示工具栏 ----------
        report("\n== S7 阳性对照：最大化态强行 show() 工具栏（看它的顶行长什么样）==")
        window._toggle_expand()
        pump(app, 1000)
        report(f"    （先复位成最大化态）opts_row = {row_state(window)}")
        row = window._rx_opts_row
        row.setMinimumHeight(0)
        row.setMaximumHeight(16777215)
        row.setFixedHeight(34)
        row.show()
        window._rx_apply_layout_now()
        pump(app, 500)
        report(f"    opts_row = {row_state(window)}")
        shoot("S7", 3)
        dump("S7")
        row.hide()
        row.setMinimumHeight(0)
        row.setMaximumHeight(16777215)
        pump(app, 200)

        # ---------- 跨场景对比：条子那几行到底是谁的像素 ----------
        report("\n== 跨场景行指纹对比（0=逐像素完全相同；每场景用自己当时的行位）==")
        x0 = max(0, rows_phys(row)[0])
        sigs: dict[str, dict[int, list]] = {}
        for scen, imgs in caps.items():
            if not imgs:
                continue
            img = imgs[-1]
            ry = int(meta.get(scen, {}).get("row_y_phys", rows_phys(row)[1]))
            band = list(range(max(0, ry), max(0, ry) + 12))
            sigs[scen] = {y: row_sig(img, y, x0, x0 + 700) for y in band}
        pairs = [("S1", "S3"), ("S2", "S3"), ("S2", "S1"), ("S4", "S3"),
                 ("S9", "S2"), ("S10", "S2"), ("S2", "S7"), ("S1", "S7"),
                 ("S3", "S7"), ("S6", "S7")]
        for a, b in pairs:
            if a not in sigs or b not in sigs:
                continue
            ya = sorted(sigs[a])
            yb = sorted(sigs[b])
            diffs = [sig_diff(sigs[a][y], sigs[b][yb[i]])
                     for i, y in enumerate(ya) if i < len(yb)]
            report(f"    {a} vs {b}: " + " ".join(f"{ya[i] if i < len(ya) else '?'}:{d:6.1f}"
                                                  for i, d in enumerate(diffs)))

        text = "\n".join(lines)
        print(text)
        with open(os.path.join(_CHECK, "rx_strip.txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
        return 0
    finally:
        try:
            window.setGeometry(0, 0, 1134, 820)
            window.close()
            app.processEvents()
        except Exception:                            # noqa: BLE001
            pass
        for key in settings.allKeys():
            if key not in snapshot:
                settings.remove(key)
        for key, value in snapshot.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
        settings.sync()


if __name__ == "__main__":
    raise SystemExit(main())
