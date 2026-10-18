"""无硬件自测：验证 HEX 编解码、增量解码、串口收发链路，以及界面端到端流程。

收发部分用 pyserial 自带的 loop:// 回环端口，写进去的字节会原样读回来，
所以没有开发板、没有 USB 转串口线也能跑完整条链路。
界面部分以 offscreen 方式真实创建主窗口并点击按钮，
不走肉眼验证，也不会弹出窗口打断你。

用法（在 SerialAssistant 目录下）：
    .venv\\Scripts\\python.exe selftest.py
"""

from __future__ import annotations

import os
import sys
import time

# 必须赶在导入 QtWidgets 之前设置，否则无显示环境下创建窗口会直接失败
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtWidgets import QApplication, QPushButton

from pathlib import Path

from ble_io import BleBroadcastWorker
from serial_io import (
    SerialWorker, StreamDecoder, _port_sort_key, list_serial_ports, parse_hex, to_hex,
)


def _depad(text: str) -> str:
    """把 ``align_indexes`` 的排版还原成设备原文：摘掉前导 0（序号和值都摘）、
    压平空白（连续空白 -> 单空格、去行首尾）。

    值的补零 0 落在小数点**之后**（"1.5" -> "1.05"），正则摘前导 0 摘不到它，
    所以用采样点结构化还原：每个 "序号.值" 各自 lstrip("0")。
    网格重排（2026-10-11）之后对齐还会把列距统一成 3 空格、去掉设备行首的
    填充空格 —— 所以"还原"必须连空白一起归一，才能跟原文逐字比。
    """
    from ui_highlight import SAMPLE_RE

    def unpad(match):
        index = match.group(1).lstrip("0") or "0"
        value = match.group(2)[1:].lstrip("0") or "0"
        return index + "." + value

    depadded = SAMPLE_RE.sub(unpad, text)
    return "\n".join(" ".join(line.split()) for line in depadded.split("\n"))


def wait_for(app: QApplication, predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


def restore_settings(settings, snapshot) -> None:
    """把 QSettings 还原到 ``snapshot``：多余的键删掉、记过的写回。

    这段逻辑原本在本文件里重复了 14 遍，而它**写错一次就是永久污染用户配置**
    （少删/漏还原都会被"snapshot 把脏值当基线"的自我固化放大，见本文件顶部那段
    说明）。集中到这里，改一次全对。两条容易漏的：

      · 快照里没有的键要 remove（不然测试留下的键会一直留着）；
      · 快照里是 **None** 的键也要 remove，**不能** setValue(key, None) ——
        那会往注册表写一个 null，customization.get 读到 None 就不再回落默认值。
        这一条原先只有 14 处里的 1 处写了，其余 13 处都漏着。
    """
    for key in settings.allKeys():
        if key not in snapshot:
            settings.remove(key)
    for key, value in snapshot.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)


def pin_rx_grid_defaults(settings) -> None:
    """建窗**前**把接收区网格参数钉成默认的 0=自动（用户真实值由 snapshot 还回去）。

    定制里可以显式填模组结构（如 5 模组 × 25 电芯），此时电压/温度网格会**按模组
    重排**、把一行值拆成多行 —— 那是设计功能，且实测**不丢字节**（逐值核对见
    ``devtools/_probe_rx_grid.py``）。但下面几条用例的前提是"样例值在同一行"，
    不钉死就随用户配置漂：实测用户一改设置，「回车发送/时间戳轮首/序号染蓝」
    「收包合并与暂停继续」「定位最近异常按钮」三条同时假挂（前提断言拿到的行只剩
    ``01.3700``，后面的 ``02.3820 03.3900`` 被换到了别的行）。

    ⛔ 必须在 ``snapshot`` 取完之后调用：用户原值已经被 snapshot 收走，finally 的
    ``restore_settings`` 才能原样还回去。这里直写 QSettings 而不是
    ``customization.set()``，只为省掉 import 顺序上的坑 —— ``set()`` 也只写值、
    不碰 ``*_chosen``，两种写法对"自动/手动"语义的影响完全一样。
    """
    for key in ("module_count", "cells_per_module", "temps_per_module"):
        settings.setValue(f"custom/{key}", 0)
    settings.sync()


def window_click(app: QApplication, window, point: QPoint) -> None:
    """经 windowHandle 派发一次真实点击（press + release）。

    这是 QWidgetWindow 的正常鼠标入口 —— 会走**子控件命中测试**，
    「页头设了 WA_TransparentForMouseEvents 把子控件一起掐死」这类问题
    只有走这条路才抓得到（直点按钮是绕过命中测试的，测不出来）。

    不能用 ``QTest.mouseClick(顶层窗口, ...)``：PySide6 6.12 + offscreen 下
    它根本**不投递事件**（最小复现：一个普通 QPushButton 也收不到），
    点了等于没点，测了个寂寞。
    """
    from PySide6.QtGui import QMouseEvent

    local = QPointF(point)
    global_pos = QPointF(window.mapToGlobal(point))
    handle = window.windowHandle()
    assert handle is not None, "窗口还没 show，没有 windowHandle"
    press = QMouseEvent(QEvent.Type.MouseButtonPress, local, global_pos,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, local, global_pos,
                          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                          Qt.KeyboardModifier.NoModifier)
    app.sendEvent(handle, press)
    app.sendEvent(handle, release)


def make_window():
    """建一个主窗口，并**停掉插拔自动扫描**。

    自动扫描会在启动一秒后把第一个扫到的串口打开。自测机上那可能是用户真实的设备，
    会把 loop:// 那条用例顶掉（串口已经开着，点"打开"反倒成了"关闭"）——
    所以除了专门验它的用例，其余一律先停表。
    """
    from main_window import LINK_SERIAL, MainWindow

    window = MainWindow()
    window._port_watch.stop()
    # ⚠️ 连接方式**钉成串口**：本文件里绝大多数用例（收发、回环、建链、插拔…）
    #    讲的都是串口那条路，而"连接方式"是用户配置（选过就落盘）。不钉的话，
    #    用户上次用过蓝牙广播，一片串口用例会同时挂掉、现象还各不相同
    #    （实测 5 条一起挂：状态栏文字不对、假句柄没关、发送路径报没有 write…）。
    window.cmb_link.setCurrentText(LINK_SERIAL)
    return window


def check_open_async(app: QApplication) -> None:
    """建链必须**不阻塞界面**，而且能中途取消。

    为什么专门有一条：蓝牙串口在 Windows 上要先建 RFCOMM 链路，慢的时候好几秒；
    以前 ``open()`` 是在界面线程里同步做的，那几秒窗口是**冻住的**（用户 2026-10-14
    点名优化）。改成"只发起、结果走信号"之后，这段行为必须钉住，否则哪天有人
    图省事把它改回同步的，界面又会卡 —— 而那种卡在自测里是**看不出来**的。

    手法：把 ``serial_for_url`` 换成一个 open 要睡 1.2 秒的假句柄，于是
      · 发起调用必须**立刻返回**（界面没被占住）；
      · 期间状态栏是"正在连接 …"（用户看得见反馈，而不是像卡死）；
      · 中途取消要立刻回到"未打开"，不能等那 1.2 秒；
      · 取消之后假句柄必须被**关掉**（没连上也不能把口留着）。
    """
    import serial_io

    class _SlowPort:
        """假串口句柄：open() 睡一会儿。"""

        def __init__(self) -> None:
            self.closed = False

        def open(self) -> None:
            time.sleep(1.2)

        def close(self) -> None:
            self.closed = True

        # 读循环要用到的部分（本次只考建链，给最小可用实现）
        @property
        def in_waiting(self) -> int:
            return 0

        def read(self, count: int) -> bytes:
            time.sleep(0.03)
            return b""

        def write(self, data: bytes) -> None:
            pass

    created: list[_SlowPort] = []
    real_for_url = serial_io.serial.serial_for_url

    def fake_for_url(port, **_kwargs):
        made = _SlowPort()
        created.append(made)
        return made

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    serial_io.serial.serial_for_url = fake_for_url
    window = make_window()
    try:
        window._refresh_ports(announce=False)      # 名单是假的没关系：端口名直接传
        start = time.perf_counter()
        window._open_port("SLOW1")
        took = time.perf_counter() - start
        assert took < 0.3, \
            f"发起建链把界面线程占住了 {took:.2f}s —— open() 又变回同步的了？"
        assert window._worker is not None, "发起之后没有 worker（取消就无从谈起）"
        assert not window._port_connected, "还没连上就标成已连接了"
        assert window.lbl_state.text().startswith("正在连接"), window.lbl_state.text()
        assert not window.cmb_port.isEnabled(), "建链期间配置项没锁住"

        start = time.perf_counter()
        window._close_port()                        # 取消
        cancel_took = time.perf_counter() - start
        assert cancel_took < 0.4, \
            f"取消建链卡了 {cancel_took:.2f}s（不该等那 1.2 秒的建链）"
        assert window._worker is None and not window._port_connected
        assert window.lbl_state.text() == "未打开", window.lbl_state.text()
        assert window.cmb_port.isEnabled(), "取消之后配置项没解锁"

        # 让那个慢线程跑完：它应当发现"已被取消"→ 关掉句柄 → 自己退出
        wait_for(app, lambda: bool(created) and created[0].closed, timeout=3.0)
        assert created and created[0].closed, "取消之后假句柄没被关掉"
    finally:
        serial_io.serial.serial_for_url = real_for_url
        window.close()
        restore_settings(settings, snapshot)
        settings.sync()


def check_ble_broadcast(app: QApplication) -> None:
    """蓝牙广播通道（ble_io）：只扫描、不连接、不配对，把广播负载当数据流送出来。

    用**假扫描器**驱动（把 bleak.BleakScanner 换掉），不依赖本机有没有蓝牙、
    周围有没有设备 —— 自测机上这两样都不一定有。真机验证走
    `devtools/_probe_ble_worker.py` 和产物里的 `--ble-scan`。
    """

    class _FakeAdv:
        def __init__(self, mfg=None, svc=None, rssi=-50, local_name=""):
            self.manufacturer_data = mfg or {}
            self.service_data = svc or {}
            self.rssi = rssi
            self.local_name = local_name

    class _FakeDevice:
        def __init__(self, address, name=""):
            self.address = address
            self.name = name

    class _FakeScanner:
        """假扫描器：start() 时把脚本里的包全喂一遍，stop() 什么也不做。"""

        script: list = []

        def __init__(self, detection_callback=None, scanning_mode="passive"):
            self._cb = detection_callback
            self.mode = scanning_mode

        async def start(self):
            assert self.mode == "passive", "广播接收必须是 passive 扫描"
            for device, adv in _FakeScanner.script:
                self._cb(device, adv)

        async def stop(self):
            return None

    import bleak

    real_scanner = bleak.BleakScanner
    bleak.BleakScanner = _FakeScanner
    try:
        # 1) 只喂**目标设备**的负载：别人的包一概不要（这是"不绑定也能用"的前提）
        _FakeScanner.script = [
            (_FakeDevice("AA:BB:CC:DD:EE:01", "别的设备"),
             _FakeAdv(mfg={65535: b"OTHER\n"})),
            (_FakeDevice("AA:BB:CC:DD:EE:02", "我的BMS"),
             _FakeAdv(mfg={65535: b"Voltage1: 01.3700\n"})),
            (_FakeDevice("AA:BB:CC:DD:EE:03"),
             _FakeAdv(svc={"0000fff0-0000-1000-8000-00805f9b34fb": b"SVC\n"})),
        ]
        worker = BleBroadcastWorker("AA:BB:CC:DD:EE:02")
        got: list[bytes] = []
        found: list[dict] = []
        opened: list[bool] = []
        worker.received.connect(got.append)
        worker.found.connect(found.append)
        worker.opened.connect(lambda: opened.append(True))
        worker.open()
        assert wait_for(app, lambda: bool(opened)), "广播通道没有发 opened"
        wait_for(app, lambda: len(got) > 0)
        worker.stop()
        assert got == [b"Voltage1: 01.3700\n"], f"喂进来的负载不对：{got!r}"
        assert len(found) == 3, f"设备列表没把扫到的都报出来：{len(found)}"
        assert {f["address"] for f in found} == {
            "AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03"}

        # 2) source="auto"：manufacturer 里没有就退到 service data
        _FakeScanner.script = [
            (_FakeDevice("AA:BB:CC:DD:EE:03"),
             _FakeAdv(svc={"0000fff0-0000-1000-8000-00805f9b34fb": b"FROM_SVC\n"})),
        ]
        worker = BleBroadcastWorker("AA:BB:CC:DD:EE:03", source="auto")
        got2: list[bytes] = []
        worker.received.connect(got2.append)
        worker.open()
        wait_for(app, lambda: len(got2) > 0)
        worker.stop()
        assert got2 == [b"FROM_SVC\n"], f"auto 没有回退到 service data：{got2!r}"

        # 3) 按**名字片段**匹配（设备地址会变 —— 手机开热点、模块换芯片都会变，
        #    用名字选人更稳），以及指定公司 ID 的 key 过滤
        _FakeScanner.script = [
            (_FakeDevice("AA:BB:CC:DD:EE:04", "CQUFSAE-BMS"),
             _FakeAdv(mfg={6: b"WRONG\n", 65535: b"RIGHT\n"})),
        ]
        worker = BleBroadcastWorker("cqufsae", source="manufacturer", key="0xFFFF")
        got3: list[bytes] = []
        worker.received.connect(got3.append)
        worker.open()
        wait_for(app, lambda: len(got3) > 0)
        worker.stop()
        assert got3 == [b"RIGHT\n"], \
            f"名字匹配 / 公司 ID 过滤不对：{got3!r}（应只取 65535 那家公司的负载）"
    finally:
        bleak.BleakScanner = real_scanner


def check_hex_codec() -> None:
    assert parse_hex("") == b""
    assert parse_hex("AA 01 ff") == b"\xaa\x01\xff"
    assert parse_hex("aa01FF") == b"\xaa\x01\xff"
    assert parse_hex("AA,01;-ff_00") == b"\xaa\x01\xff\x00"
    assert to_hex(b"\xde\xad\xbe\xef") == "DE AD BE EF"
    assert to_hex(parse_hex("DE AD BE EF")) == "DE AD BE EF"

    for bad in ("A", "AA 0G", "zz", "AA BB C"):
        try:
            parse_hex(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} 本该被判为非法")


def check_stream_decoder() -> None:
    # "温度" 的 UTF-8 是 6 字节，切成三块喂进去，解码器应把半个字符缓冲住
    payload = "温度".encode("utf-8")
    decoder = StreamDecoder("utf-8")
    out = decoder.feed(payload[:2]) + decoder.feed(payload[2:5]) + decoder.feed(payload[5:])
    assert out == "温度", f"跨块字符解码错误: {out!r}"
    assert "\ufffd" not in out, "出现了替换符，说明多字节字符被切坏了"

    # 换编码后必须换解码器，否则会拿旧编码去解新数据
    decoder.set_encoding("gbk")
    assert decoder.feed("压力".encode("gbk")) == "压力"


def check_port_enumeration() -> None:
    ports = list_serial_ports()
    assert isinstance(ports, list)
    assert all(len(item) == 2 for item in ports)
    # 端口名按**数字**排：按字符串排会得到 COM10 < COM2，插拔自动连接"挑第一个"
    # 时就会挑错人（本机 COM 号上到两位数就会撞上）
    assert sorted(["COM10", "COM2", "COM3", "COM1"], key=_port_sort_key) == \
        ["COM1", "COM2", "COM3", "COM10"], "端口名没按数字排序"
    print(f"      本机串口: {ports if ports else '无（用 loop:// 自测即可）'}")


def check_loopback(app: QApplication) -> None:
    """不开界面，单独验证通信层：写进去的字节应原样从 received 信号回来。"""
    worker = SerialWorker("loop://", baudrate=115200)
    chunks: list[bytes] = []
    opened: list[bool] = []
    worker.received.connect(chunks.append)
    # ⚠️ 监听必须在 open() **之前**挂上：建链是异步的，open() 之后才连信号
    #    可能整个信号都错过（loop:// 毫秒级就连上了）。
    worker.opened.connect(lambda: opened.append(True))

    worker.open()
    # 建链是异步的（蓝牙口要等好几秒，所以 open() 只启动线程，真正的
    # serial.open 在 run 里做）—— 不能像以前那样调完就当作已经连上。
    assert wait_for(app, lambda: bool(opened)), "loop:// 没有发出 opened 信号"
    assert worker.isRunning(), "读线程没有起来"
    try:
        sent = b"\x01\x02\xa5hello"
        worker.write(sent[:3])
        worker.write(sent[3:])
        wait_for(app, lambda: sum(len(c) for c in chunks) >= len(sent))
    finally:
        worker.stop()

    assert not worker.isRunning(), "stop() 之后读线程还在跑"
    received = b"".join(chunks)
    assert received == sent, f"回环数据不一致: 发 {sent!r} 收 {received!r}"


def check_port_hotplug(app: QApplication) -> None:
    """插拔只自动**发现**（不代用户打开）+ 电源开关真的点得动。

    用**注入的假端口表**驱动，不碰真实硬件：端口来源是 ``list_serial_ports()``
    （这里换成假的），真正打开走 SerialWorker —— 喂它 ``loop://`` 就能在没有
    开发板的情况下连上。

    两条都是踩过的坑：
    - 下拉框显示的是 "COM3 — STMicroelectronics STLink ..."，**能打开的是前半截**，
      把显示文字交给 pyserial 就是 FileNotFoundError；
    - 电源开关挂在页头，而页头原来设了 ``WA_TransparentForMouseEvents`` ——
      那个属性按 Qt 定义会连**子控件**的鼠标事件一起掐掉，按钮就废了。
      所以这里必须经 **windowHandle** 派发点击（QWidgetWindow 的正常鼠标入口，
      会走子控件命中测试，见 window_click）；直接 ``QTest.mouseClick(button)``
      是绕过命中测试的，测不出这个问题。
    """
    import main_window as mw
    from main_window import PORT_LABEL_SEP

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    real_ports = mw.list_serial_ports
    fake: list[tuple[str, str]] = []
    mw.list_serial_ports = lambda: list(fake)
    window = make_window()
    warnings: list[str] = []
    window._warn = warnings.append          # 手动打开失败会弹模态框，自测里换成记录
    # 窗口级派发的点击要求窗口**真的 show 出来**：没 show 的顶层控件没有 windowHandle，
    # QTest 只能退化成 sendEvent，不会走 QWidgetWindow 的子控件命中测试 —— 那样点不中
    # 页头上的电源开关，等于没测。
    window.show()
    app.processEvents()
    try:
        label = f"loop://{PORT_LABEL_SEP}回环端口（假）"

        # 1) 显示文字 vs 端口名
        fake[:] = [("loop://", "回环端口（假）")]
        window._refresh_ports()
        assert window.cmb_port.currentText() == label, window.cmb_port.currentText()
        assert window._selected_port() == "loop://", \
            f"交给 pyserial 的端口名错了：{window._selected_port()!r}"
        # 手输也要认（用户可能选完又改文字），带描述的写法也得能切出端口名
        for typed in ("COM7", f"COM7{PORT_LABEL_SEP}USB-SERIAL CH340 (COM7)"):
            window.cmb_port.setEditText(typed)
            assert window._selected_port() == "COM7", \
                f"手输 {typed!r} 解析成了 {window._selected_port()!r}"

        # 2) 插上设备：名单刷新，但**绝不**替用户打开串口（用户："我自己有手"）
        fake[:] = [("loop://", "回环端口（假）")]
        window._poll_ports()
        assert window._worker is None, \
            "插上设备后被自动打开了 —— 用户明确要求手动开"
        assert window.cmb_port.count() == 1, "插上设备后下拉框没刷新出这个口"
        assert window.cmb_port.isEnabled(), "没连串口，配置控件不该被锁住"
        window._poll_ports()
        assert window._worker is None, "名单没变却动了状态"

        # 3) 点电源开关 → 真的打开（经 windowHandle 派发，考的是命中测试）。
        # 先把要开的口选好 —— 上面第 1 步往编辑框里手输过 "COM7 — ..."，那是
        # 另一件事的铺垫；真实用户也是先选口、再点电源（"我自己有手"）。
        window._select_port("loop://")
        assert window._selected_port() == "loop://"
        # 发送区这一轮起是**开局默认折叠**的，所以这里不能写死"不该是折叠态"，
        # 只能要求"打开串口别去动它"—— 拿开口之前的值比对。
        tx_collapsed_before_open = window._tx_collapsed
        button = window.btn_open
        assert button.parent().findChildren(type(button)).count(button) >= 1
        # 命中测试：配置卡片在这一点的最上层子控件就是它自己
        assert window._config_card.childAt(
            button.mapTo(window._config_card, button.rect().center())) is button, \
            "配置卡片在那个位置命中的不是电源开关"
        ancestor = button.parentWidget()
        while ancestor is not None:
            assert not ancestor.testAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents), \
                (f"{type(ancestor).__name__} 设了鼠标穿透，它下面的按钮点不动"
                 "（Qt 的定义里这个属性连子控件一起掐）")
            ancestor = ancestor.parentWidget()
        window_click(app, window, button.mapTo(window, button.rect().center()))
        assert window._worker is not None, \
            "点了电源开关，打开请求都没发出去（按钮的鼠标事件被上层吃掉了）"
        # 建链是**异步**的（蓝牙口要几秒，见 SerialWorker 的说明）：先看到
        # "正在连接 …"，连上之后才是"已打开 …"。
        assert wait_for(app, lambda: window._port_connected), \
            f"点了电源开关，串口没有连上：{window.lbl_state.text()!r}"
        assert window._worker.port == "loop://", window._worker.port
        assert window.lbl_state.text().startswith("已打开 loop://"), \
            window.lbl_state.text()
        assert not window.cmb_port.isEnabled(), "连上之后串口选择没被锁住"
        # 打开串口**不**代做"接收区展开"—— 那是用户自己点放大按钮的事
        assert not window._expanded, "打开串口不该自作主张展开接收区"
        assert window._tx_collapsed == tx_collapsed_before_open, \
            "打开串口动了发送区的折叠状态"

        # 4) 再点一下 → 关掉（布局状态保持，不越权动窗口）
        window_click(app, window, button.mapTo(window, button.rect().center()))
        assert wait_for(app, lambda: window._worker is None), "再点一下没有关上"
        assert window.cmb_port.isEnabled(), "断开之后串口选择没解锁"

        # 5) 拔掉 → 自动断开、下拉框不再留着它
        window._open_port()
        assert window._worker is not None
        assert wait_for(app, lambda: window._port_connected), "loop:// 没连上"
        fake[:] = []
        window._poll_ports()
        assert window._worker is None, "设备拔掉了串口还开着"
        assert window.cmb_port.count() == 0, window.cmb_port.count()

        # 6) 打不开的口：两种异常都要接住、都要给出提示。
        #    不存在的 COM 口抛 SerialException；带冒号/未知协议的名字抛的是
        #    ValueError（serial_for_url 把它当 URL 解析）—— 只捕前一种的话，
        #    异常会一路冒到 Qt 的事件循环里。
        #    ⚠️ 建链是异步的：这里要**等失败结果回来**，不能调完就断言（见
        #    SerialWorker 的说明 —— 蓝牙口正是"慢"才改的异步）。
        for broken in ("COM199", "nosuch://x"):
            fake[:] = [(broken, "假装打不开的口")]
            window._refresh_ports(announce=False)
            window._select_port(broken)
            warnings.clear()
            window._open_port()
            assert wait_for(app, lambda: window._worker is None), \
                f"{broken} 竟然打开了（或失败结果没回来）"
            assert warnings and broken in warnings[-1], \
                f"{broken} 打不开却没报错：{warnings!r}"
    finally:
        mw.list_serial_ports = real_ports
        window.close()
        restore_settings(settings, snapshot)
        settings.sync()


def check_ui_roundtrip(app: QApplication) -> None:
    """真实驱动主窗口：打开 loop:// → 文本发送 → 定时发送 → HEX 收发 → 关闭。"""

    # 关窗口时会写 QSettings（Windows 上是注册表），先快照、结束后还原，
    # 免得跑一次自测就把真实配置改掉。
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}

    window = make_window()
    dialogs: list[str] = []
    # 测试里不要弹模态框，否则一旦出错会卡住；改成记录后断言
    window._warn = dialogs.append
    try:
        window.chk_timestamp.setChecked(False)
        window.chk_append_crlf.setChecked(False)
        window.cmb_port.setCurrentText("loop://")

        window.btn_open.click()
        # 拨动开关的状态直接写在勾选上（通=已打开）
        assert window.btn_open.isChecked(), "拨了开关串口没打开，开关还是断的"
        assert window.lbl_state.text() != "未打开", "状态栏没显示已打开"

        # 1) 文本发送 -> 回显
        window.txt_tx.setPlainText("PING")
        window.btn_send.click()
        assert wait_for(app, lambda: "PING" in window.txt_rx.toPlainText()), \
            f"接收区没拿到回显: {window.txt_rx.toPlainText()!r}"
        assert "接收 4 字节" in window.lbl_count.text(), window.lbl_count.text()

        # 2) 定时发送：50ms 间隔跑一会儿，至少发出去 3 帧。
        #    ⚠️ 用字符数不用行数：无换行尾的帧会按"半行缓发"连成一块显示
        #    （2026-10-11 起），数行数会假挂。
        window.txt_tx.setPlainText("T")
        window.spin_interval.setValue(50)
        window.chk_timer_send.setChecked(True)
        window._clear_receive()
        assert wait_for(app, lambda: window.txt_rx.toPlainText().count("T") >= 3), \
            f"定时发送没跑起来: {window.txt_rx.toPlainText()!r}"

        # 3) 关掉定时发送后必须真的停下来
        window.chk_timer_send.setChecked(False)
        assert not window._send_timer.isActive(), "取消勾选后定时器还在跑"
        # 先等最后一帧"落定"再清空：worker 跑在独立线程里，刚写出去的字节
        # 可能还在路上，不等的话它会被误判成"定时发送没停"。这是这条断言
        # 以前偶发失败的原因。
        time.sleep(0.3)
        app.processEvents()
        window._clear_receive()
        sent_before = window._tx_bytes
        time.sleep(0.3)
        app.processEvents()
        # 真正说明问题的是"没有新的字节发出去"；接收区为空是它的结果
        assert window._tx_bytes == sent_before, "取消后还在往外发字节"
        assert window.txt_rx.toPlainText() == "", "取消后接收区还在涨，定时发送没有真的停止"

        # 4) HEX 发送 + HEX 显示
        window.chk_hex_view.setChecked(True)
        window.chk_hex_send.setChecked(True)
        window.txt_tx.setPlainText("AA 55")
        window.btn_send.click()
        assert wait_for(app, lambda: "AA 55" in window.txt_rx.toPlainText()), \
            f"HEX 回显不对: {window.txt_rx.toPlainText()!r}"

        # 5) 暂停滚动：暂停后内容照常追加，但滚动位置被钉住
        window.btn_pause.click()
        assert window._scroll_frozen(), "点击暂停键后没有进入暂停态"
        assert "继续滚动" in window.btn_pause.toolTip(), window.btn_pause.toolTip()
        # 暂停定位现在是 200ms 的滚动补间（见 _snap_to_round_start）——
        # 先让它跑完再钉位置，否则下面"位置被钉住"会被还在跑的补间带着走。
        assert wait_for(app, lambda: not (window._rx_scroll_tween
                                          and window._rx_scroll_tween.running),
                        timeout=1.0), "暂停定位的滚动补间一直没停下"
        bar = window.txt_rx.verticalScrollBar()
        bar.setValue(bar.maximum())          # 先滚到底，稍后验证位置被钉住
        pinned = bar.value()
        window._emit_block("PIN")
        assert bar.value() == pinned, f"暂停期间滚动位置被改动: {bar.value()} != {pinned}"
        # 冻结显示：暂停期间画面纹丝不动，数据进后台队列，恢复时一次刷入
        assert "PIN" not in window.txt_rx.toPlainText(), "暂停期间画面变了"
        assert window._paused_frames and window._paused_frames[-1][0] == "PIN", \
            "暂停期间的数据没有进后台队列"
        window.btn_pause.click()
        assert not window._scroll_frozen(), "再次点击后没有恢复滚动"
        assert "PIN" in window.txt_rx.toPlainText(), "恢复后攒下的数据没有刷进来"

        # 6) 非法 HEX 只提示、不发送
        before = len(dialogs)
        window.txt_tx.setPlainText("AA 0G")
        window.btn_send.click()
        assert len(dialogs) == before + 1, "非法 HEX 应该给出提示"

        # 7) 关闭串口
        window.btn_open.click()
        assert not window.btn_open.isChecked(), "拨断开关后串口还开着"
        assert window.lbl_state.text() == "未打开"

        # 整个流程里只应有第 6 步那一处提示
        assert len(dialogs) == 1, f"流程中出现了意外提示: {dialogs}"
    finally:
        window.close()
        restore_settings(settings, snapshot)
        settings.sync()


def check_glass_controls(app: QApplication) -> None:
    """毛玻璃强度滑杆与自绘箭头控件：验证「拖动真的改变效果」，不只是参数存下来了。"""
    from PySide6.QtWidgets import QComboBox, QSpinBox

    from ui_controls import ChevronComboBox, ChevronSpinBox
    from ui_settings import SettingsDialog
    from ui_theme import frost_gain, glass_alpha, set_glass_strength

    # 强度必须同时推动**面板不透明度**和**颗粒幅度** —— 两者少一个都读不出磨砂：
    # 只调透明度是"透明"不是"磨砂"，只调颗粒则像在实底上贴了张贴纸。
    set_glass_strength(0)
    low = (glass_alpha(), frost_gain())
    set_glass_strength(100)
    high = (glass_alpha(), frost_gain())
    assert high[0] < low[0], f"强度没有改变面板透明度: {low} -> {high}"
    assert high[1] > low[1], f"强度没有改变颗粒幅度: {low} -> {high}"
    set_glass_strength(68)

    # 设置面板：滑杆 0~100 无级，values() 返回主题 + 强度两件套
    # （「接收区保留行数」已经搬到串口配置栏，不在这个面板里了）
    dialog = SettingsDialog("dark", 55)
    try:
        assert (dialog.slider_glass.minimum(), dialog.slider_glass.maximum()) == (0, 100)
        assert not hasattr(dialog, "spin_lines"), "设置面板里还留着「接收区保留」"
        theme_mode, glass = dialog.values()
        assert (theme_mode, glass) == ("dark", 55), (theme_mode, glass)
        dialog.slider_glass.setValue(90)
        assert dialog.values()[1] == 90, dialog.values()
    finally:
        dialog.close()

    # 自绘箭头控件必须仍是原生控件的子类，调用方 API 不变
    assert issubclass(ChevronComboBox, QComboBox)
    assert issubclass(ChevronSpinBox, QSpinBox)

    # ---- 自绘滑杆 GlassSlider：几何自算，任何缩放下滑块都不悬空 ----
    # QSS 时代的回归：groove 被 QSS 默认定位缩进了 ~17px 而滑块滑动范围没有，
    # 强度在两端时圆点整个悬在轨道外面（用户报"滑轨显示异常仍未解决"）。
    from PySide6.QtCore import QEvent, QPointF, QPoint
    from PySide6.QtGui import QMouseEvent, QWheelEvent

    from ui_controls import GlassSlider

    dialog = SettingsDialog("dark", 55)
    try:
        slider = dialog.slider_glass
        assert isinstance(slider, GlassSlider), "毛玻璃滑杆应为自绘 GlassSlider"
        slider.resize(300, 20)
        slider.setValue(0)
        assert slider._knob_cx() == GlassSlider.KNOB_D / 2.0, slider._knob_cx()
        slider.setValue(100)
        assert abs(slider._knob_cx() - (slider.width() - GlassSlider.KNOB_D / 2.0)) < 0.6, \
            f"滑块在最大值时没有贴到轨道右端：{slider._knob_cx()} / {slider.width()}"
        # 点击轨道即跳到该值
        slider.setValue(0)
        press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(150.0, 10.0),
                            QPointF(150.0, 10.0), Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        app.sendEvent(slider, press)
        assert slider.value() > 0, "点击轨道没有跳到对应强度（自绘交互失效）"
        # 滚轮不许改数值（与 ChevronSpinBox/ComboBox 同一条铁律）
        before = slider.value()
        wheel = QWheelEvent(QPointF(150.0, 10.0), QPointF(150.0, 10.0),
                            QPoint(0, 120), QPoint(0, 120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        app.sendEvent(slider, wheel)
        assert slider.value() == before, "滚轮改了滑杆数值（应禁掉防误操作）"
    finally:
        dialog.close()


def check_light_pure_white(app: QApplication) -> None:
    """页面背景：两个主题都是**纯色**；磨砂颗粒两个主题都在，而且是**糊过的**。

    这几条必须一起断言。只测"背景是纯色"很容易过 —— 但那可能是因为把磨砂一起删了；
    只测颗粒还在也容易过 —— 但那可能是因为背景还留着渐变；只测颗粒是噪声、
    连硬噪点都算过 —— 而那正是用户说的"粗纸"。合起来才说明问题。
    """
    from PySide6.QtGui import QImage, QPainter

    from ui_backdrop import frost_tile, paint_backdrop
    from ui_theme import frost_gain, scheme_qcolor, set_glass_strength, set_scheme

    def render(scheme: str, width: int = 160, height: int = 120) -> QImage:
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        paint_backdrop(painter, image.rect(), scheme)
        painter.end()
        return image

    # 1) 两个主题的背景都必须**通篇同一个颜色**，且就是该主题的 bg token。
    #    浅色早先是压到 #AEB8C4 的对角渐变、深色是 #2E333B → #050506 的渐变，
    #    都被用户否掉了。抽稀采样逐点比色：渐变在这一关必挂 ——
    #    渐变上相邻点的颜色本来就不可能个个相同。
    for scheme in ("light", "dark"):
        image = render(scheme)
        want = scheme_qcolor("bg", scheme)
        wanted = (want.red(), want.green(), want.blue())
        for y in range(0, image.height(), 5):
            for x in range(0, image.width(), 5):
                color = image.pixelColor(x, y)
                assert (color.red(), color.green(), color.blue()) == wanted, \
                    f"{scheme} 背景不是纯色: ({x},{y}) -> {color.name()}，应为 {want.name()}"
    assert scheme_qcolor("bg", "light").name() == "#ffffff", "浅色页面应当仍是纯白"

    # 2) 磨砂的载体：两个主题都要有颗粒贴图。
    #    纯色背景糊多少遍还是纯色，"虚化背景"这条路已经不存在了，
    #    磨砂只能由玻璃自身的颗粒承担 —— 所以深色主题也必须有，不能只有浅色有。
    for scheme in ("light", "dark"):
        tile = frost_tile(scheme, 2.0)
        assert tile is not None, f"{scheme} 主题缺少磨砂颗粒贴图"
        assert tile.width() == tile.height(), (tile.width(), tile.height())
        assert tile.devicePixelRatio() == 2.0, tile.devicePixelRatio()
        # 颗粒是噪声：一张纯色图等于没铺
        grain = tile.toImage()
        seen = {grain.pixelColor(i % grain.width(), i // grain.width()).alpha()
                for i in range(0, grain.width() * grain.height(), 37)}
        assert len(seen) > 8, f"{scheme} 的颗粒贴图太单调，看着会是平色: {sorted(seen)}"

    # 2b) 颗粒必须是**糊过的**，不能是逐像素硬噪点。
    #     这条是用户"是要毛玻璃效果而不是粗纸啊"的直接固化：逐像素硬噪点不管幅度
    #     压得多低，落在屏幕上都被读成砂纸 / 粗纸。
    #     判据取"相邻像素 alpha 的平均跳变" —— 硬噪点大约是量程的 1/3（实测 11.7），
    #     糊一道之后塌到 1 以下（实测 0.6），两者差一个数量级，阈值放 3 很稳。
    #     光测这一条还不够：把幅度调到 0 也能过。所以同时要求**平均 alpha 还在** ——
    #     它才是"面板比背景浑一档"的来源，削掉它等于把卡片的层次一起删了。
    for scheme in ("light", "dark"):
        grain = frost_tile(scheme, 2.0).toImage()
        columns = range(0, grain.width() - 1, 7)
        rows = range(0, grain.height(), 7)
        step_sum = alpha_sum = 0
        for y in rows:
            for x in columns:
                step_sum += abs(grain.pixelColor(x, y).alpha()
                                - grain.pixelColor(x + 1, y).alpha())
                alpha_sum += grain.pixelColor(x, y).alpha()
        count = len(columns) * len(rows)
        mean_step, mean_alpha = step_sum / count, alpha_sum / count
        assert mean_step < 3.0, \
            f"{scheme} 的颗粒没糊开，看着会是砂纸而不是磨砂玻璃（相邻跳变 {mean_step:.2f}）"
        assert mean_alpha > 6.0, \
            f"{scheme} 的颗粒太淡，面板与背景的层次会一起丢掉（均值 {mean_alpha:.2f}）"

    # 3) 强度滑杆推的是颗粒幅度：0 是清玻璃、100 是明确的磨砂
    set_scheme("light")
    set_glass_strength(0)
    assert frost_gain() == 0.0, frost_gain()
    set_glass_strength(100)
    assert frost_gain() == 1.0, frost_gain()
    set_glass_strength(68)
    assert 0.0 < frost_gain() < 1.0, frost_gain()

    # 4) 上缘高光：光从上面来，玻璃的顶边把光兜住 —— 但它只能落在**上缘**，
    #    下缘必须一点都不沾（不然就成了给整块面板描了一圈白边）。
    #    做法是同一张卡、同一个强度，只把 `ui_surface.RIM_ALPHA` 关掉再渲一次，
    #    比较上下两条带子的平均亮度。这样"面色随强度变化"这个变量被完全消掉，
    #    剩下的差异只可能来自光边本身。
    import ui_surface

    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QWidget
    from ui_theme import MATERIAL_FROSTED, material as current_material
    from ui_theme import set_material as apply_material

    def edge_gain(rim_alpha: int) -> tuple[float, float]:
        ui_surface.RIM_ALPHA = rim_alpha
        set_glass_strength(100)
        holder = QWidget()
        card = ui_surface.Card("", holder)
        card.resize(320, 200)
        canvas = QImage(320, 200, QImage.Format.Format_ARGB32)
        canvas.fill(QColor(scheme_qcolor("bg", "light")))
        card.render(canvas)
        columns = range(80, 240, 4)

        def band(y0: int, y1: int) -> float:
            rows = range(y0, y1)
            return sum(canvas.pixelColor(x, y).red() for y in rows for x in columns) \
                / (len(rows) * len(columns))

        # 光边是沿路径的 1px 渐变描边，峰值落在上缘第 5-6 行（y=5 处 +4.0、
        # y=6 处 +4.8，再往下渐隐归零）—— 采样带必须把峰值行框进来，从 y=6
        # 起的话峰值行漏掉，5 行平均只剩 ~0.96，阈值 1.5 恒挂（实测踩过）。
        return band(5, 10), band(189, 194)          # 顶边 5 行（含峰值行）/底边 5 行

    set_scheme("light")
    # ⛔ 必须**自己钉住材质**：全局材质是"最后一个建过的窗口"按用户配置设的
    #    （main_window `_apply_material` ← custom/material）。用户把材质切成
    #    液态玻璃之后，毛玻璃那条 rim 根本不画，这里量到的就是 183.38 → 183.38
    #    （2026-10-18 实测恒挂，与本轮动画改动无关）。用完还原，别把别人的状态
    #    改了。
    prev_material = current_material()
    apply_material(MATERIAL_FROSTED)
    try:
        top_off, bottom_off = edge_gain(0)
        top_on, bottom_on = edge_gain(84)
        # 实测基准：峰值行 +4.0/+4.8 灰阶，5 行均摊 ~1.4 —— 阈值按实测校准
        # （rim 是 1px 渐变描边，均摊到 5 行就是这个量级；阈值定 1.5 是拿
        # 颗粒噪声凑出来的假余量，必挂）
        assert top_on - top_off > 1.0, \
            f"磨砂开着时上缘没兜到光: {top_off:.2f} -> {top_on:.2f}"
        assert abs(bottom_on - bottom_off) < 0.5, \
            f"光边漏到下缘了，那叫描白边不叫玻璃: {bottom_off:.2f} -> {bottom_on:.2f}"
    finally:
        ui_surface.RIM_ALPHA = 84
        apply_material(prev_material)
    set_glass_strength(68)

    set_scheme("dark")


def check_receive_search(app: QApplication) -> None:
    """接收区搜索：找到、跳转、计数、清空，以及"搜索期间视图不被数据拽走"。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    from ui_anim import QWIDGETSIZE_MAX
    from ui_controls import SearchEdit

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}

    window = make_window()
    try:
        window.show()
        app.processEvents()
        window.chk_timestamp.setChecked(False)

        # 接收区右栏要有这枚按钮，且默认收起
        assert not window._search_bar.isVisible(), "搜索条默认应该是收起的"

        for line in ("boot ok", "ADC ch0=1", "CAN frame", "ADC ch1=2", "ADC ch2=3"):
            window.txt_rx.appendPlainText(line)

        # 点右栏的放大镜展开。展开是连续过渡（200ms 高度 + 淡入），
        # 控件立刻可见，高度随即长出来 —— 所以这里要等它铺满。
        window.btn_search.click()
        app.processEvents()
        assert window._search_bar.isVisible(), "点击搜索键没有展开搜索条"
        assert wait_for(app, lambda: window._search_reveal.expanded), "搜索条没有展开到位"

        # 边打边搜走的是防抖计时器；这里直接驱动一次，等价于"等它到点"
        window.edit_search.setText("ADC")
        window._search_timer.stop()
        window._refresh_matches()
        assert len(window._matches) == 3, f"命中数不对: {len(window._matches)}"
        assert window.lbl_matches.text() == "1/3", window.lbl_matches.text()
        # 高亮必须真的交给文本框了，不能只算了个数
        assert len(window.txt_rx.extraSelections()) == 3, "命中没有全部高亮"
        assert window._scroll_frozen(), "搜索有命中时视图应当被钉住"

        # 下一个 / 上一个，到头要绕回去
        window._find_next(True)
        assert window.lbl_matches.text() == "2/3", window.lbl_matches.text()
        window._find_next(False)
        assert window.lbl_matches.text() == "1/3", window.lbl_matches.text()
        window._find_next(False)
        assert window.lbl_matches.text() == "3/3", "上一个到头没有绕回末尾"
        window._find_next(True)
        assert window.lbl_matches.text() == "1/3", "下一个到头没有绕回开头"

        # 回车 = 下一个，Shift+回车 = 上一个（两者靠 keyPressEvent 区分，
        # 不能靠 returnPressed —— 它对 Shift+回车 一样会发，分不出方向）
        assert isinstance(window.edit_search, SearchEdit)
        window.edit_search.setText("ADC")
        window._search_timer.stop()
        window._refresh_matches()
        window.edit_search.keyPressEvent(
            QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
        assert window.lbl_matches.text() == "2/3", "回车没有跳到下一个"
        window.edit_search.keyPressEvent(
            QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier))
        assert window.lbl_matches.text() == "1/3", "Shift+回车没有跳回上一个"

        # 搜索期间来新数据：高亮要跟着重算，视图也要继续钉住原来的位置
        bar = window.txt_rx.verticalScrollBar()
        pinned = bar.value()
        window._emit_block("ADC ch3=4 以及一行很长的内容 " + "x" * 400)
        window._search_timer.stop()
        window._refresh_matches()
        assert len(window._matches) == 4, f"新数据到达后命中没有重算: {len(window._matches)}"
        assert bar.value() == pinned, \
            f"搜索期间视图被新数据拽走了: {bar.value()} != {pinned}"

        # 无命中要有明确说法，而且不能留着上一轮的旧高亮
        window.edit_search.setText("zzz-not-here")
        window._search_timer.stop()
        window._refresh_matches()
        assert window.lbl_matches.text() == "无匹配", window.lbl_matches.text()
        assert window.txt_rx.extraSelections() == [], "无命中时还留着旧高亮"

        # 清空关键词 = 回到未搜索状态
        window.edit_search.setText("")
        window._search_timer.stop()
        window._refresh_matches()
        assert window.lbl_matches.text() == "", window.lbl_matches.text()

        # 关闭：收起是**连续**的（高度压到 0 再隐藏），不是一帧跳变 ——
        # 所以先断言"立刻还没藏起来、正在收"，再等它收完。高亮和视图状态
        # 则是立刻生效的，不等动画。
        window._close_search()
        app.processEvents()
        assert window._search_bar.isVisible(), "收起应当是一段连续过程，而不是一帧跳变"
        assert window.txt_rx.extraSelections() == [], "关闭后高亮没清掉"
        assert not window._scroll_frozen(), "关闭搜索后视图应当恢复跟随"
        assert wait_for(app, lambda: not window._search_bar.isVisible()), "搜索条没有收起来"

        # Ctrl+F 快捷键连到同一路；重新展开后高度上限必须放开，
        # 否则搜索条会被永久钉在动画结束时的高度上
        window._toggle_search()
        app.processEvents()
        assert window._search_bar.isVisible(), "Ctrl+F 没有展开搜索条"
        assert wait_for(app, lambda: window._search_reveal.expanded), "再次展开没有到位"
        assert window._search_bar.maximumHeight() == QWIDGETSIZE_MAX, \
            window._search_bar.maximumHeight()
        window._close_search()
    finally:
        window.close()
        restore_settings(settings, snapshot)
        settings.sync()


def check_animation_engine(app: QApplication) -> None:
    """动画引擎：节拍必须够 120fps、曲线必须非线性、静止时不留计时器。"""
    from ui_anim import (
        EASINGS, STANDARD, Tween, animations_enabled, bezier, display_max_hz,
        frame_hz, frame_interval_ms, in_out_quint, out_cubic,
        set_animations_enabled_override, set_frame_hz, ticker_active,
        ticker_interval,
    )

    # 本机若在「辅助功能」里关掉了动画，下面这些"要看到帧在推进"的断言会全挂 ——
    # 那是设置生效了，不是引擎坏了。测试里先把开关钉成"开"，结束时交还系统。
    set_animations_enabled_override(True)

    # 1) 节拍：默认 120fps，且**帧率可无级调**（2026-10-17 用户点名）。
    #    节拍是"高帧率"唯一能确定性验证的地方 —— 帧数会受机器负载影响，节拍不会。
    original_hz = frame_hz()
    try:
        set_frame_hz(120)
        assert frame_hz() == 120, frame_hz()
        assert frame_interval_ms() == 8, frame_interval_ms()
        assert ticker_interval() == frame_interval_ms(), ticker_interval()
        # 显示器上限：不会超过防呆的 240，也不会低于下限 24
        assert 24 <= display_max_hz() <= 240, display_max_hz()
        # 超出范围一律夹住（设置里存了 1000 也不能真按 1000 跑）
        assert set_frame_hz(1000) == min(240, display_max_hz()), "上限没夹住"
        assert set_frame_hz(1) == 24, "下限没夹住"
        set_frame_hz(120)

        # 1b) **速度同步**：帧率只改采样密度，**不改动画时长**。
        #     同一段 160ms 动画在 60fps 与最高帧率下，墙钟时长必须基本一致，
        #     只是帧数不同 —— 这是用户点名要的那条。
        def _measure(hz: int, ms: int = 160):
            set_frame_hz(hz)
            samples: list[float] = []
            tw = Tween(ms, out_cubic, samples.append, parent=app)
            started = time.perf_counter()
            tw.start(0.0, 1.0)
            deadline = time.monotonic() + 3.0
            while tw.running and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.002)
            return (time.perf_counter() - started) * 1000.0, len(samples)

        slow_ms, slow_n = _measure(60)
        fast_ms, fast_n = _measure(max(120, display_max_hz()))
        assert abs(fast_ms - slow_ms) <= max(60.0, slow_ms * 0.35), \
            (f"改帧率把动画时长也改了：60fps {slow_ms:.0f}ms / "
             f"高帧率 {fast_ms:.0f}ms")
        assert fast_n > slow_n, \
            f"高帧率没有采到更多帧：{slow_n} -> {fast_n}（帧率没生效？）"
    finally:
        set_frame_hz(original_hz)

    # 2) 曲线一律非线性。线性运动在 UI 上发死，短过渡必须"起步快、落地慢"。
    #    判据取 t=0.25：线性曲线在那里必然等于 0.25，减速曲线明显大于它，
    #    两头慢的曲线明显小于它 —— 对称曲线在 t=0.5 恰好过 0.5，所以不能用中点判。
    for name, curve in EASINGS.items():
        assert abs(curve(0.0)) < 1e-9, (name, curve(0.0))
        assert abs(curve(1.0) - 1.0) < 1e-9, (name, curve(1.0))
        values = [curve(i / 40) for i in range(41)]
        assert all(b >= a - 1e-9 for a, b in zip(values, values[1:])), f"{name} 不单调"
        if name == "linear":
            assert curve(0.25) == 0.25
            continue
        assert abs(curve(0.25) - 0.25) > 0.02, f"{name} 在 t=0.25 几乎走直线"

    # 新加的两条要各自钉住"为什么加"：
    # · in_out_quint 是 power3.inOut 那一档 —— 两端比 cubic 更轻（t=0.1 更小）
    # · bezier 造的 STANDARD 是"起步略缓、收尾极稳" —— 前段不能像 out_* 那么冲
    assert in_out_quint(0.1) < 4.0 * 0.1 ** 3 + 1e-9, "in_out_quint 起步不比 cubic 轻"
    assert in_out_quint(0.5) == 0.5, in_out_quint(0.5)
    assert abs(STANDARD(0.0)) < 1e-9 and abs(STANDARD(1.0) - 1.0) < 1e-9
    assert STANDARD(0.25) > 0.25, "标准曲线应当是「起步快、收尾长」"
    custom = bezier(0.25, 0.1, 0.25, 1.0)
    assert abs(custom(0.0)) < 1e-9 and abs(custom(1.0) - 1.0) < 1e-9
    assert custom(0.5) > 0.5, custom(0.5)

    # 3) 补间真的连续推进到终点：中途单调、不跳步，结束回调只发一次
    seen: list[float] = []
    done: list[int] = []
    tween = Tween(120, out_cubic, seen.append, lambda: done.append(1), parent=app)
    tween.start(0.0, 1.0)
    assert tween.running, "start() 之后补间没在跑"
    assert ticker_active(), "有补间在跑，调度器却没开"
    assert seen and seen[0] == 0.0, f"起点没有立刻补上，会停在旧值上: {seen[:2]}"
    deadline = time.monotonic() + 3.0
    while tween.running and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.004)
    assert not tween.running, "120ms 的补间三秒还没跑完"
    assert done == [1], f"结束回调没有（或重复）触发: {done}"
    assert abs(seen[-1] - 1.0) < 1e-9, seen[-1]
    assert all(b >= a - 1e-9 for a, b in zip(seen, seen[1:])), "补间中途回退了"
    # 120ms / 8ms ≈ 15 帧。给个宽松下限，只用来排除"两三步跳完"。
    assert len(seen) >= 6, f"120ms 只推进了 {len(seen)} 步，帧率不够"

    # 4) 打完收工：所有补间跑完后调度器必须停 —— 界面静止时不留计时器。
    #    用等待而不是立刻断言：上一条测试关窗时可能还留着一个正在收尾的短过渡，
    #    这里要证明的是"最终会停"，不是"此刻恰好是停的"。
    assert wait_for(app, lambda: not ticker_active(), timeout=2.0), \
        "补间都跑完了，调度器还在转"

    # 5) repeat：循环期间"结束回调"不能发（那是给状态机用的信号），
    #    且跨轮次必须连着走 —— 循环是用来做"转到状态结束"的指示器的。
    cycles: list[float] = []
    finished: list[int] = []
    loop = Tween(60, out_cubic, cycles.append, lambda: finished.append(1),
                 parent=app, repeat=1)
    loop.start(0.0, 1.0)
    deadline = time.monotonic() + 3.0
    while loop.running and time.monotonic() < deadline:
        app.processEvents()
        app.processEvents()
        time.sleep(0.004)
    assert not loop.running, "repeat=1 的补间跑到超时还没结束"
    assert finished == [1], f"结束回调应当在**最后一轮**才发一次: {finished}"
    # 循环的判据是"值回落到起点后又重新爬升"，不是"出现过 1.0" ——
    # 中间轮次结束时会**直接送起点值**（不送 1.0），最后一轮才落终值。
    drops = [i for i, (a, b) in enumerate(zip(cycles, cycles[1:])) if b < a - 1e-9]
    assert drops, f"没观测到循环重来（值一直单调）: {cycles[:6]}"
    tail = cycles[drops[0] + 1:]
    assert tail and max(tail) > 0.2, f"第二轮没有重新推进: {cycles}"

    # 6) 减弱动效（对应 GSAP 的 matchMedia(prefers-reduced-motion)）：
    #    关掉时不是"什么都不做"，而是**直接落到终态**。
    set_animations_enabled_override(False)
    snapped: list[float] = []
    snapped_done: list[int] = []
    quick = Tween(5000, out_cubic, snapped.append, lambda: snapped_done.append(1),
                  parent=app)
    quick.start(0.0, 1.0)
    assert not quick.running, "关掉动画后补间不该还在跑"
    assert snapped and snapped[-1] == 1.0, f"没有直接落到终态: {snapped}"
    assert snapped_done == [1], "终态回调没发"
    assert animations_enabled() is False
    set_animations_enabled_override(True)
    assert animations_enabled() is True

    # 7) 矢量弧：忙碌时转起来（且用的是同一套节拍/共享计时器），
    #    退出忙碌必须立刻停 —— 界面回到静止就该零计时器。
    from ui_controls import PulseDot
    dot = PulseDot()
    dot.set_busy(True)
    spins: list[float] = []
    deadline = time.monotonic() + 0.6
    while time.monotonic() < deadline:
        app.processEvents()
        spins.append(dot._spin_t)
        time.sleep(0.01)
    assert ticker_active(), "忙碌态没有在推帧"
    assert max(spins) > min(spins), f"弧的角度没有推进: {spins[:5]}"
    assert all(b >= a - 1e-9 for a, b in zip(spins, spins[1:])), "弧的角度回退过"
    dot.set_busy(False)
    assert wait_for(app, lambda: not ticker_active(), timeout=2.0), \
        "退出忙碌后调度器还转着（静止必须零计时器）"
    dot.deleteLater()

    # 8) 弹层展开方向：动画必须从**锚点那一侧**长，且与该弹层的实际落位一致。
    #    用户 2026-10-15 报过两次弹层（先高亮方块、后展开方向），这条把它钉住：
    #    · 落位在控件下方 → 从顶端往下长（y 不动、高度变大）
    #    · 被 Qt 翻到控件上方 → 从底端往上长（底边不动）
    #    ⚠️ 判方向要按**中心**比：Qt 把弹层顶对齐到控件顶，拿"弹层顶 vs 控件底"
    #    判会一律判成朝上（这正是当时的 bug）。
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QWidget as _QWidget
    from ui_controls import ChevronComboBox

    host = _QWidget()
    host.resize(320, 240)
    host.move(120, 120)
    combo = ChevronComboBox(host)
    combo.addItems(["一", "二", "三"])
    combo.move(20, 20)
    host.show()
    wait_for(app, lambda: host.isVisible(), timeout=2.0)
    combo.showPopup()
    # 等"延后一拍"真正起动画：第一个生长帧
    grows: list[tuple[int, int]] = []
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        app.processEvents()
        g = combo.view().window().geometry()
        grows.append((g.y(), g.height()))
        if len(grows) >= 3 and grows[-1][1] < g.height():
            break
        time.sleep(0.005)
    final = combo.view().window().geometry()
    anchor_center = combo.mapToGlobal(QPoint(0, combo.height() // 2)).y()
    below = (final.y() + final.height() / 2) >= anchor_center
    shrinking = [f for f in grows if f[1] < final.height()]
    assert shrinking, f"弹层没有从半高开始生长（帧全满高）: {grows[:4]}"
    first = shrinking[0]
    if below:
        assert first[0] == final.y(), \
            f"弹层落在控件下方，却从底端往上长：{first} → ({final.y()},{final.height()})"
    else:
        assert first[0] + first[1] == final.y() + final.height(), \
            f"弹层落在控件上方，却从顶端往下长：{first} → ({final.y()},{final.height()})"
    combo.hidePopup()
    host.close()
    host.deleteLater()
    app.processEvents()


def check_rx_history(app: QApplication) -> None:
    """历史接收数据（rx_history.py）：入队、**行数跟随接收区**、批量落盘、
    设置按钮、以及软件内回放。

    全程用临时文件，不碰真实的 接收历史.log —— 模块全局状态先存后还。
    """
    import tempfile

    import rx_history

    saved = (rx_history._path, rx_history._file_lines, tuple(rx_history._lines),
             rx_history.max_lines())
    rx_history._pending.clear()
    try:
        tmp = Path(tempfile.mkdtemp(prefix="rxhist_")) / "接收历史.log"
        rx_history._path = tmp
        rx_history._lines.clear()
        rx_history._file_lines = 0
        rx_history.set_max_lines(rx_history.DEFAULT_MAX_LINES)
        cap = rx_history.max_lines()

        # 1) 空行不记、时间戳行照记
        rx_history.feed("")
        rx_history.feed("\n \n")
        rx_history.feed("[2026-10-12 22:00:00]\nVoltage1: 0 3.75")
        assert "[2026-10-12 22:00:00]" in rx_history.lines(), "时间戳行没进历史"
        assert len(rx_history.lines()) == 2, f"空行不该入队：{len(rx_history.lines())}"

        # 2) 超过上限：deque 滚动，最老的先被挤掉
        for i in range(cap + 200):
            rx_history.feed(f"data {i}")
        assert len(rx_history.lines()) == cap, "deque 没滚动"
        assert rx_history.lines()[0] == "data 200", \
            f"最老行不对：{rx_history.lines()[0]}"

        # 3) 落盘：追加（pending 不封顶，文件暂时超一点没关系，下一步收回）；
        # 超阈值（上限+COMPACT_EXTRA）才整体重写收回
        rx_history._pending.clear()      # 前面 deque 滚动时挤掉的行不该再落盘
        rx_history.feed("anchor")        # 造一个待写尾巴，保证首次落盘能建出文件
        rx_history.flush()
        assert tmp.exists(), "flush 之后文件应该已建好"
        n1 = len(tmp.read_text(encoding="utf-8").splitlines())
        assert n1 == 1, f"首次落盘应只有待写那一行：{n1}"

        # 再喂 上限+COMPACT_EXTRA+50 行：追加后文件 = 1 + 5550 = 5551 行，
        # 跨过 上限+COMPACT_EXTRA=5500 阈值 → 本次 flush 里整体重写收回到上限。
        for i in range(cap + rx_history.COMPACT_EXTRA + 50):
            rx_history.feed(f"more {i}")
        rx_history.flush()
        rows = tmp.read_text(encoding="utf-8").splitlines()
        assert len(rows) == cap, f"重写后应恰好 {cap} 行：{len(rows)}"
        assert "[2026-10-12 22:00:00]" not in rows, "时间戳行应已被滚动挤出"
        assert "anchor" not in rows, "首次落盘那行应已被滚动挤出"

        # 3b) 行数跟随接收区：调小立刻裁剪、调大不复活旧行
        rx_history.set_max_lines(1000)
        assert rx_history.max_lines() == 1000
        assert len(rx_history.lines()) == 1000, \
            f"调小后应立刻裁剪到 1000：{len(rx_history.lines())}"
        # set_max_lines 已把 _file_lines 标脏（≥上限+COMPACT_EXTRA+1），
        # 一次 flush 就该把 5000 行的文件收回到 1000 行。
        rx_history.flush()
        rows2 = tmp.read_text(encoding="utf-8").splitlines()
        assert len(rows2) == 1000, f"文件没跟随上限收回：{len(rows2)}"
        rx_history.set_max_lines(3000)   # 调大：deque 上限变 3000，但已有行仍是 1000
        assert rx_history.max_lines() == 3000
        assert len(rx_history.lines()) == 1000, "调大不该复活已被丢掉的行"
        # 恢复默认，后续断言（文件行数）按默认上限算
        rx_history.set_max_lines(cap)

        # 4) flush 后无积压；路径为 None 时静默不炸
        rx_history.feed("tail")
        assert rx_history._pending, "feed 后应有待写"
        rx_history.flush()
        assert not rx_history._pending, "flush 后不应有积压"
        saved_path = rx_history._path
        rx_history._path = None
        rx_history.feed("x")
        rx_history.flush()          # 不应抛异常
        rx_history._path = saved_path

        # 5) 设置面板有「打开记录」按钮（路径已指向临时文件）
        # 日志大节在 app_log.path() 为 None 时整节跳过（接收历史行挂在它里面），
        # 所以先初始化日志。
        import app_log
        app_log.init()
        from ui_settings import SettingsDialog
        dlg = SettingsDialog("dark", 55, None, [])
        btns = [b for b in dlg.findChildren(QPushButton)
                if b.text() == "打开记录"]
        assert btns, "设置面板没有「打开记录」按钮"
        tip = btns[0].toolTip()
        assert str(rx_history.max_lines()) in tip, \
            f"「打开记录」的提示没跟随当前上限行数：{tip}"
        # 5b) 按钮要**紧挨**路径文字（用户点名："应该紧挨着相邻文字，去掉它们
        #     之间多余的空白间距"）。判据：按钮左缘 − 标签右缘 == 布局间距本身
        #     （标签不再吃 stretch 撑满整行，剩下的空白交给末尾那根弹簧）。
        dlg.show()                      # 要真实布局过一遍才有几何可比
        app.processEvents()
        from PySide6.QtWidgets import QLabel       # 本函数此前没用过这个名字
        holder = btns[0].parentWidget()
        hlabels = [lb for lb in holder.findChildren(QLabel)
                   if lb.text().endswith(".log")]
        assert hlabels, "接收历史行里找不到路径标签"
        hlab = hlabels[0]
        assert hlab.parentWidget() is holder, "路径标签与按钮不在同一个行容器里"
        hgap = btns[0].geometry().x() - (hlab.geometry().x() + hlab.width())
        assert hgap == holder.layout().spacing(), \
            (f"「打开记录」没紧挨路径文字：中间空出 {hgap}px，"
             f"应只有布局间距 {holder.layout().spacing()}px")
        dlg.close()
        dlg.deleteLater()

        # 6) 路径定位不到时整行隐藏（无按钮、也不崩）
        rx_history._path = None
        dlg2 = SettingsDialog("dark", 55, None, [])
        btns2 = [b for b in dlg2.findChildren(QPushButton)
                 if b.text() == "打开记录"]
        assert not btns2, "路径为 None 时不应显示打开记录按钮"
        dlg2.deleteLater()
        rx_history._path = saved_path

    finally:
        rx_history._path, rx_history._file_lines, _lines_back, _cap_back = saved
        rx_history.set_max_lines(_cap_back)
        rx_history._lines.clear()
        rx_history._lines.extend(saved[2])
        rx_history._pending.clear()


def check_history_replay(app: QApplication) -> None:
    """第 7 项：本地留存数据能在接收区内打开，格式/着色/功能与实时一致。

    单独一个 check —— 它要新建一个主窗口，不能塞在 check_rx_history 里
    （那会把窗口全局状态泄漏给后续用例，实测把「复制本轮」挤挂过）。
    """
    import tempfile

    import customization
    import rx_history
    from main_window import MainWindow
    from ui_theme import qcolor as _qc

    # 隔离：MainWindow 构造时会调 rx_history.load()，**按 _path 所在目录**
    # 重定位到真实日志目录并把真实文件读回内存。所以必须"先建窗、再种数据"，
    # 否则种好的临时数据会被真实日志整个覆盖掉。
    saved = (rx_history._path, rx_history._file_lines, tuple(rx_history._lines),
             rx_history.max_lines())
    saved_limits = (customization.get("volt_limit_low"),
                    customization.get("volt_limit_high"))
    rx_history._pending.clear()
    rx_history._timer_stop()
    window = None
    try:
        # 阈值钉死：这条用例要验的是"回放管线着色与实时一致"，不该被用户
        # 配置里的自定义电压上下限左右（实测默认 2500~4500，但如果用户改过，
        # 样例值的判定结果就跟着漂）。显式设成 2500~4500。
        customization.set("volt_limit_low", 2500)
        customization.set("volt_limit_high", 4500)

        window = MainWindow()
        window._clear_receive()
        assert window.btn_open_history.text() == "打开历史数据", \
            "串口配置栏没有「打开历史数据」按钮"
        assert not window._replay_active, "刚开窗就进了回放模式"
        assert window._replay_bar.isHidden(), "未回放时提示条不该显示"

        # 建窗之后再接管路径与内容（load() 已经跑完，不会再来覆盖）
        # 阈值口径：volt_limit 默认 2500~4500 mV（customization 默认值，实测）。
        # 欠压样例必须**明确低于 2500**（01.2400 → 2400mV）；
        # 01.3700 → 3700mV 落在正常区间内，不会标绿（早先就栽在这）。
        tmpdir = Path(tempfile.mkdtemp(prefix="rxreplay_"))
        tmp = tmpdir / rx_history.FILE_NAME
        tmp.write_text(
            "[2026-10-12 22:00:00]\n"
            "Voltage1:\n"
            "01.2400 02.3820 03.3900\n"
            "Temperature1:\n"
            "1.22 2.95 3.45\n"
            "BMS ERROR\n"
            "1.1 Over Voltage\n",
            encoding="utf-8")
        rx_history._path = tmp
        rx_history._lines.clear()
        rx_history._lines.extend(tmp.read_text(encoding="utf-8").splitlines())
        rx_history._file_lines = len(rx_history._lines)

        window._open_history()
        app.processEvents()
        assert window._replay_active, "打开历史后没进入回放模式"
        # 「打开历史数据」是一颗**开关**（用户点名"再点一次就关掉"）：进回放后
        # 文案必须跟着变，否则用户看不出这一下会退出回放。
        assert window.btn_open_history.text() == "关闭历史数据", \
            f"回放态下按钮文案没跟着状态变（还是「{window.btn_open_history.text()}」）"
        # 窗口没 show()，子控件 isVisible() 恒为 False —— 断言"要求显示"
        # （isHidden 为 False）而不是可见性。
        assert not window._replay_bar.isHidden(), "回放提示条没有显示"
        # 提示条是**补间展开**的（0 → 50px，220ms）：展开一开始 max 就该放开，
        # 否则布局永远只给它 0 高、条长不出来（这条专压那个坑）。
        assert window._replay_bar.maximumHeight() > 0, \
            "提示条展开补间把 maximumHeight 钉在 0 了（条长不出来）"
        # 后面要用 render() 验这张条的圆角，必须拿到**展开后**的真实几何 ——
        # 先等 220ms 的补间收尾。
        assert wait_for(app, lambda: window._replay_tween is None, 2.0), \
            "提示条展开补间没在 2s 内收尾"
        # 提示条的**外观约定**（都是用户点名的）：
        #  · 圆角必须跟卡片同一个尺度（CARD_RADIUS）；
        #  · 这个面**不能交给 QSS 画** —— QSS 对普通 QWidget 的 border-radius 在
        #    真实主窗口里会被整条丢掉（渲染成直角，用户第二遍报"边框没有圆角"
        #    就是这个；同一份样式表在隔离窗口里却是圆的）。所以面由
        #    ``main_window.ReplayBar.paintEvent`` 自绘（ui_surface.paint_glass）；
        #  · 左侧那条竖向装饰条已去掉（原是 border-left: 3px solid @fill_primary@，
        #    深色主题下就是一根近白竖杠；文案本身已说明是回放数据，纯装饰）。
        import re as _re

        from ui_theme import CARD_RADIUS as _CR
        from ui_theme import stylesheet as _qss
        _clean = _re.sub(r"/\*.*?\*/", "", _qss(), flags=_re.S)   # 去掉注释再找规则
        assert not _re.search(r"#replayBar\s*\{", _clean), \
            ("QSS 里又在给回放条画面了：那条 border-radius 对普通 QWidget 会被丢掉、"
             "渲染成直角 —— 这个面必须自绘（见 ReplayBar.paintEvent）")
        assert window._replay_bar.__class__.__name__ == "ReplayBar", \
            "回放条不是自绘的 ReplayBar（又换回普通 QWidget + QSS 了？）"
        assert _CR == window._replay_bar.RADIUS, \
            f"自绘用的圆角 {window._replay_bar.RADIUS} 没跟卡片统一（{_CR}）"

        # 渲染自证：把条单独渲染一张图 —— 左上角必须**没被画上**（alpha 0，圆角
        # 让出了那个角），中间必须画上（面还在）。这条直接压住"圆角到底生没生效"，
        # 比看 QSS 文本可靠得多（QSS 文本对但渲染成直角，就是这么漏过去的）。
        from PySide6.QtGui import QImage, QRegion
        from PySide6.QtWidgets import QWidget as _QW

        _rb = window._replay_bar
        _rb.resize(_rb.sizeHint())
        _rb.layout().activate()
        _dpr = _rb.devicePixelRatioF()
        _im = QImage(max(2, int(_rb.width() * _dpr)),
                     max(2, int(_rb.height() * _dpr)),
                     QImage.Format.Format_ARGB32_Premultiplied)
        _im.fill(0)
        # ⚠️ 图必须**先声明自己的 devicePixelRatio**，否则 render 会按 1x 往这张
        #    2x 的图里画，取样点全落在画布外 —— 角上 alpha=0 会让断言"假通过"，
        #    中间 alpha=0 又让它"假挂"（实测就挂在这一步）。标题栏那段自测
        #    也是这么写的（shot.setDevicePixelRatio）。
        _im.setDevicePixelRatio(_dpr)
        # ⚠️ 必须显式只给 DrawChildren：默认还带 DrawWindowBackground，会先拿调色板
        #    铺一层窗底色，角上那点也被填上，断言直接假挂。
        _rb.render(_im, QPoint(0, 0), QRegion(), _QW.RenderFlag.DrawChildren)
        _corner = _im.pixelColor(int(1 * _dpr), int(1 * _dpr))
        _mid = _im.pixelColor(int(_rb.width() * _dpr / 2),
                              int(_rb.height() * _dpr / 2))
        assert _corner.alpha() == 0, \
            (f"回放条左上角被画上了（{_corner.name()} alpha={_corner.alpha()}）"
             "：圆角没生效 —— 多半又改回用 QSS 画这个面了")
        assert _mid.alpha() > 0, "回放条中间没画上底（自绘的面丢了）"

        # 「返回实时」按钮的四角要落在两端半圆**之内**。条高 50、圆角取
        # min(CARD_RADIUS, 条高/2) = 25 → 两端是半圆；按钮高 38，右内边距
        # 不够时描边会戳出条的底色（6px 时实测戳出 1.9px，改成 12px）。
        # 这里直接激活布局量真几何 —— 窗口没 show()，不能用 isVisible 那套。
        _radius = min(float(_CR), _rb.height() / 2.0)
        _cx, _cy = _rb.width() - _radius, _radius          # 右上角那个半圆的圆心
        _btns = [b for b in _rb.findChildren(QPushButton) if b.text()]
        assert _btns, "回放条里找不到「返回实时」按钮"
        _bg = _btns[0].geometry()
        for _px, _py in ((_bg.x() + _bg.width(), _bg.y()),
                         (_bg.x() + _bg.width(), _bg.y() + _bg.height())):
            _d = ((_px - _cx) ** 2 + (_py - _cy) ** 2) ** 0.5
            assert _d <= _radius + 0.01, \
                (f"「{_btns[0].text()}」的角戳出了回放条两端的半圆："
                 f"到圆心 {_d:.1f}px > 半径 {_radius:.0f}px"
                 f"（条 {_rb.width()}x{_rb.height()}，按钮 {_bg.x()},{_bg.y()}）")
        doc = window.txt_rx.document()
        body = [doc.findBlockByNumber(b).text() for b in range(doc.blockCount())]
        assert any("[2026-10-12 22:00:00]" in t for t in body), \
            f"回放内容里没有时间戳行：{body}"
        assert any(".2400" in t for t in body), f"回放内容里没有电压数据：{body}"

        def _painted(number):
            blk = doc.findBlockByNumber(number)
            return [r.format.foreground().color().name()
                    for r in (blk.layout().formats() if blk.layout() else [])]

        volt_rows = [i for i, t in enumerate(body) if ".2400" in t]
        assert volt_rows, f"回放里找不到电压数据行：{body}"
        volt_colors = set()
        for i in volt_rows:
            volt_colors.update(_painted(i))
        assert _qc("volt_low").name() in volt_colors, \
            f"回放的欠压值没有标绿：{volt_colors}"
        bms_rows = [i for i, t in enumerate(body) if "BMS ERROR" in t]
        assert bms_rows and _qc("bms_error").name() in _painted(bms_rows[0]), \
            "回放里的 BMS ERROR 没有标橙"

        # 回放期间实时帧不覆盖画面，但照常进历史
        rx_history._pending.clear()
        window._on_received("Voltage1:\n9.9999\n".encode("utf-8"))
        window._flush_rx()
        app.processEvents()
        doc2 = window.txt_rx.document()
        body2 = [doc2.findBlockByNumber(b).text() for b in range(doc2.blockCount())]
        assert not any("9.9999" in t for t in body2), \
            "回放期间实时数据写进了画面（应只进历史）"
        assert any("9.9999" in ln for ln in rx_history._pending), \
            "回放期间实时数据没有进历史"

        # 返回实时：回放标记清掉、提示条收起
        window._exit_replay()
        app.processEvents()
        assert not window._replay_active, "返回实时后回放标记没清"
        assert window.btn_open_history.text() == "打开历史数据", \
            "返回实时后按钮文案没变回「打开历史数据」"
        # 收起也是 220ms 补间（从当前高度落到 0 才 hide、才把 max 归零）——
        # 直接断 isHidden() 会撞在补间中间（那时条还在收）。
        assert wait_for(app, lambda: window._replay_tween is None, 2.0), \
            "提示条收起补间没在 2s 内收尾"
        assert window._replay_bar.maximumHeight() == 0, \
            ("返回实时后提示条没有真正收起（maximumHeight="
             f"{window._replay_bar.maximumHeight()}）")
        assert window._replay_bar.isHidden(), "返回实时后提示条没收起"

        # 「打开历史数据」= 开关：回放态下再点一次必须**退出回放**，而不是再灌
        # 一遍同一个文件。连点两次 → 打开又关闭，最终必须回到实时态。
        window._open_history()
        app.processEvents()
        assert window._replay_active, "再点「打开历史数据」没进回放（开关失效）"
        window._open_history()
        app.processEvents()
        assert not window._replay_active, \
            "回放态下再点「打开历史数据」没有关闭回放（开关失效）"
    finally:
        customization.set("volt_limit_low", saved_limits[0])
        customization.set("volt_limit_high", saved_limits[1])
        if window is not None:
            window._exit_replay()
            window.close()
            window.deleteLater()
            app.processEvents()
        rx_history._path, rx_history._file_lines, _lines_back, _cap_back = saved
        rx_history.set_max_lines(_cap_back)
        rx_history._lines.clear()
        rx_history._lines.extend(saved[2])
        rx_history._pending.clear()
        rx_history._file_lines = saved[1]
        rx_history._resume_timer()


def check_updater(app: QApplication) -> None:
    """自动更新模块（updater.py）：源链、解析、完整性校验、弹窗交互。

    全部离线可测：网络只信"解析器吃样例数据"，不打真网。
    """
    import hashlib
    import json as _json

    from updater import (
        CHECK_PASSES, parse_apply_update_args, parse_tags_json,
        parse_version_file, release_download_urls, verify_integrity,
        version_sources, version_tuple,
    )

    # 1) 版本文件解析：VERSION + 哈希（仓库里 version.py 的真实格式）
    sample = ('"""软件版本号"""\nVERSION = "1.6"\n\n'
              'LATEST_EXE_SHA256 = "'
              + 'ab' * 32 + '"\n')
    info = parse_version_file(sample)
    assert info == {"version": "1.6", "sha256": "ab" * 32}, info
    # 哈希为空串（未记录）→ 不带 sha256 键
    assert "sha256" not in (parse_version_file('VERSION = "1.6"') or {}), \
        "空哈希不该出现在结果里"
    assert parse_version_file("不是版本文件") is None, "垃圾输入应返回 None"

    # 2) tag JSON 解析 + 版本比较：数值序（1.10 > 1.9）与同版本不误报
    tags = _json.dumps([{"name": "v1.10"}, {"name": "v1.9"}, {"name": "垃圾"}])
    assert version_tuple(parse_tags_json(tags)["version"]) == (1, 10), \
        "tag 解析没按数值取最大"
    assert version_tuple("1.2") <= version_tuple("1.2.0"), \
        "元组前缀语义：同版本不得误判为新版"

    # 3) 源链形态：首源是 jsDelivr（国内直连）、都走 https、镜像 ≥4 条
    sources = version_sources()
    assert len(sources) >= 6, f"版本源太少：{[s[0] for s in sources]}"
    assert sources[0][1].startswith("https://cdn.jsdelivr.net/"), \
        f"首源应当是 jsDelivr（无代理直连）：{sources[0][1]}"
    assert all(len(src) == 4 for src in sources), "版本源必须带镜像前缀（第 4 项）"
    assert all(src[1].startswith("https://") for src in sources), \
        "版本源必须都是 https"
    mirrors = [src for src in sources if src[3]]
    assert len(mirrors) >= 4, f"镜像源太少：{[m[0] for m in mirrors]}"

    # 下载源链：镜像在前（≥4 条）、GitHub 直连兜底；tag 要出现在 URL 里
    urls = release_download_urls("v1.6")
    direct = "https://github.com/Charlie-lee123/CQUFSAEbatteryteam/releases/download/v1.6/SerialAssistant.exe"
    assert len(urls) >= 5, f"下载源太少：{urls}"
    assert urls[-1] == direct, f"末源必须是直连：{urls[-1]}"
    assert all(url.endswith(direct) for url in urls[:-1]), \
        f"镜像源必须是「前缀 + 直连地址」的形式：{urls[:3]}"

    # 测速胜出的镜像会被提到最前面（下载从它起跑）
    from updater import best_prefix, note_best_prefix
    note_best_prefix("https://hub.gitmirror.com/")
    assert release_download_urls("v1.6")[0] == "https://hub.gitmirror.com/" + direct, \
        "最快镜像没有被排到第一位"
    note_best_prefix(None)          # 空值不得覆盖已有选择
    assert best_prefix() == "https://hub.gitmirror.com/", \
        "note_best_prefix(None) 不该清掉已知的最快镜像"

    # 4) 完整性校验：哈希/大小/无信息 三条路径
    data = b"SerialAssistant-exe-bytes"
    ok, _why = verify_integrity(data, hashlib.sha256(data).hexdigest())
    assert ok, "哈希一致应通过"
    ok, why = verify_integrity(data, "0" * 64)
    assert not ok, "哈希不符应拒绝"
    ok, _why = verify_integrity(data, None, len(data))
    assert ok, "大小一致应通过"
    ok, why = verify_integrity(data, None, len(data) + 1)
    assert not ok, "大小不符应拒绝"
    ok, why = verify_integrity(data, None, None)
    assert not ok, "没有任何校验信息应拒绝（宁可不装也不能装错包）"

    # 5) --apply-update 参数解析
    assert parse_apply_update_args(
        ["main.py", "--apply-update", r"F://x//SerialAssistant.exe"]) == \
        r"F://x//SerialAssistant.exe"
    assert parse_apply_update_args(["main.py"]) is None
    assert parse_apply_update_args(["main.py", "--apply-update"]) is None

    # 6) 弹窗：三个选项、开发环境禁用「立即更新」、暂不更新发会话信号
    from updater import UpdateDialog
    dlg = UpdateDialog(latest="9.9", current="1.6", sha256=None,
                       size=len(data), tag="v9.9")
    try:
        dlg.show()
        app.processEvents()
        assert dlg._btn_now.text() == "立即更新"
        assert dlg._btn_later.text() == "暂不更新"
        assert dlg._btn_repo.text() == "前往 GitHub 仓库"
        assert not dlg._frozen and not dlg._btn_now.isEnabled(), \
            "开发环境（未打包）应当禁用「立即更新」"
        assert dlg._progress.isHidden(), "进度条初始应隐藏"
        _dismissed = []
        dlg.dismissed_session.connect(lambda: _dismissed.append(1))
        dlg._later()
        assert _dismissed == [1], "「暂不更新」必须发会话抑制信号"
        assert dlg.result() == dlg.DialogCode.Rejected
    finally:
        dlg.close()

    # 7) 安装器接管更新（第 5 项）：安装包资产名 / 版本文件里的 setup 哈希 /
    #    安装版判定 / 下载源链按需换成 setup
    from updater import (installed_location, is_installed,
                         setup_asset_name, update_asset_and_hash)
    assert setup_asset_name("1.8") == "SerialAssistant-setup-1.8.exe", \
        "安装包资产名不对"
    sample2 = ('VERSION = "1.8"\n'
               'LATEST_EXE_SHA256 = "' + "ab" * 32 + '"\n'
               'LATEST_SETUP_SHA256 = "' + "cd" * 32 + '"\n')
    info2 = parse_version_file(sample2)
    assert info2["setup_sha256"] == "cd" * 32, "setup 哈希没解析出来"
    # 版本文件没写 setup 哈希 → 不带这个键（退回大小/跳过校验）
    assert "setup_sha256" not in parse_version_file('VERSION = "1.8"'), \
        "空 setup 哈希不该出现在结果里"
    # 下载源链：指定 setup 资产时，URL 里应当是 setup 而不是裸 exe
    setup_urls = release_download_urls("v1.8", setup_asset_name("1.8"))
    assert all("SerialAssistant-setup-1.8.exe" in u for u in setup_urls), \
        f"安装版下载源链没指向安装包：{setup_urls}"
    assert "releases/download/v1.8/" in setup_urls[-1]
    # 开发环境不是安装版：目录里没有 unins000.exe
    assert not is_installed(), "开发环境不该被判成安装版"
    assert installed_location() is None or isinstance(installed_location(), str)
    # 免安装版：资产/哈希走 exe 那一套
    asset, _h = update_asset_and_hash({"version": "1.8"})
    from version import ASSET_NAME
    assert asset == ASSET_NAME, f"免安装版应当下 {ASSET_NAME}，实得 {asset}"

    # 8) 源链重试轮数（用户要求失败自动重试）
    assert CHECK_PASSES >= 2, "版本检测至少要整链重试一轮"


def check_mcp_payload(app: QApplication) -> None:
    """MCP 接入入口（2.1）：源码随包走 + 接入包自包含 + 文件出口可用。

    这条检查真正防的是**交付**而不是功能：MCP 服务本身早就写好了，但只要
    ``SerialAssistant.spec`` 的 datas 漏掉 ``mcp/``，装出来的 exe 里就没有源码，
    设置面板点「复制接入配置」会复制出一段"请自己去 GitHub 取"——用户拿到手
    才发现（而且只有打包版才有这个症状，源码态永远正常）。所以这里从**运行时
    的读取路径**（``mcp_bridge.server_source``）验证，而不是直接读项目里的文件。
    """
    from pathlib import Path as _P
    from version import VERSION as _VER

    import mcp_bridge

    source = mcp_bridge.server_source()
    assert len(source) > 3000, (
        "包内没找到 MCP 服务源码 —— SerialAssistant.spec 的 datas 漏了 mcp/")

    text = mcp_bridge.deploy_payload()
    assert mcp_bridge._SRC_MARK_BEGIN in text and mcp_bridge._SRC_MARK_END in text, \
        "接入包必须用 BEGIN/END 标记把源码整段包起来（Agent 靠它原样落盘）"
    for tool in ("list_ports", "open_port", "close_port", "status", "send",
                 "read", "send_and_wait", "clear_buffer", "send_repl"):
        assert f"def {tool}(" in text, f"接入包里的服务源码缺少工具 {tool}"
    assert '"mcpServers"' in text and "serial-assistant" in text, \
        "接入包必须带客户端注册片段（mcpServers/serial-assistant）"
    assert f"v{_VER}" in text.splitlines()[0], "接入包首行要标出软件版本"
    assert "独占" in text, "串口是独占资源这件事必须写进接入包"

    # 文件出口 = 命令行 --mcp-payload 走的那条路
    out_dir = _P(__file__).resolve().parent / "_check"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / "selftest_mcp_payload.txt"
    written = mcp_bridge.write_payload(str(out))
    try:
        assert _P(written).read_text(encoding="utf-8") == text, \
            "落盘内容必须与剪贴板里的完全一致"
    finally:
        out.unlink(missing_ok=True)

    # 设置面板里的入口确实接上了复制逻辑
    from ui_settings import SettingsDialog
    assert hasattr(SettingsDialog, "_copy_mcp_payload"), "设置面板缺少 MCP 复制入口"


def check_installer_script(app: QApplication) -> None:
    """第 5 项：Inno Setup 安装脚本的关键约定必须齐全。

    不依赖编译器在不在（没装 Inno Setup 的机器上也能跑），只静态校验脚本里
    那些**一旦写错就会让用户装出问题的约定**：
      - AppId 存在且是固定 GUID（升级识别靠它，改了会变成两个软件）；
      - 支持自定义安装位置（DisableDirPage=no）且升级沿用上次位置；
      - 有桌面快捷方式（受 Tasks 控制、默认勾选）与开始菜单项；
      - 有卸载入口，卸载时可选保留/删除配置；
      - 不强制管理员（per-user 安装，双击即装）。
    若 `发布\\SerialAssistant-setup-<版本>.exe` 已编译出来，再顺带校验产物本身
    （大小合理 + 应用名/版本号确实嵌进去了）。
    """
    from pathlib import Path as _P

    root = _P(__file__).resolve().parent
    iss = root / "installer" / "SerialAssistant.iss"
    assert iss.exists(), f"安装脚本不存在：{iss}"
    text = iss.read_text(encoding="utf-8")

    def has(snippet: str, why: str) -> None:
        assert snippet in text, f"安装脚本缺少「{why}」（找不到 {snippet!r}）"

    # 升级识别的唯一依据
    assert "AppId={{8F3C1B42-7A6E-4D91-9C15-2E7B4A0D5F31}" in text, \
        "AppId 必须是固定 GUID（升级识别靠它）"
    # 自定义安装位置 + 升级沿用上次
    has("DisableDirPage=no", "允许用户自定义安装位置")
    has("UsePreviousAppDir=yes", "升级时沿用上次的安装目录")
    has("DefaultDirName={localappdata}", "默认装到用户目录（免管理员）")
    # 免管理员
    has("PrivilegesRequired=lowest", "per-user 安装、不要求管理员")
    # 桌面快捷方式：Tasks 里默认勾上 + Icons 里落到 {autodesktop}
    has('Name: "desktopicon"', "桌面快捷方式选项")
    has("Flags: checkedonce", "桌面快捷方式默认勾选")
    has("{autodesktop}", "桌面快捷方式的实际落点")
    has("{group}", "开始菜单项")
    # 卸载
    has("[UninstallDelete]", "卸载清理段")
    has("unins", "卸载程序引用")
    # 版本号由构建脚本注入（不许硬编码）
    has("#ifndef AppVersion", "版本号注入兜底")

    # 构建脚本存在且能定位到 iss / exe
    builder = root / "devtools" / "_build_installer.py"
    assert builder.exists(), f"安装包构建脚本不存在：{builder}"
    btext = builder.read_text(encoding="utf-8")
    assert "version.py" in btext, "构建脚本应当从 version.py 现读版本号"
    assert "iscc" in btext.lower(), "构建脚本应当调用 Inno Setup 编译器"
    # 编译器查找必须覆盖"非标准安装路径"：本机就是手装在 F:\Inno Setup 6\
    # （既不在 Program Files 也不在 PATH），只查那几个常规落点会漏。
    assert "_drives" in btext, "构建脚本应当兜底扫描各盘根目录找 Inno Setup"

    # ===== 有产物时：校验编译出来的 setup 包（编译器装了就顺带验一遍）=====
    from version import VERSION as _VER
    setup = root / "发布" / f"SerialAssistant-setup-{_VER}.exe"
    if setup.exists():
        size = setup.stat().st_size
        # 安装包必须**大于**裸 exe（它把 exe 压进去了），否则说明打空了
        bare = root / "发布" / "SerialAssistant.exe"
        assert bare.exists(), "有 setup 包却没有裸 exe，产物目录状态异常"
        assert size > 10 * 1024 * 1024, \
            f"setup 包只有 {size:,} B，明显打空了"
        raw = setup.read_bytes()
        # 中文应用名与版本号应内嵌在 setup 头里（ASCII 或 UTF-16LE 任一命中）
        for label, variants in (
            ("应用名", (b"CQUFSAE", "CQUFSAE".encode("utf-16-le"))),
            ("版本号", (_VER.encode("ascii"), _VER.encode("utf-16-le"))),
        ):
            assert any(v in raw for v in variants), f"setup 包里找不到{label}"


def check_tooltip_and_buttons(app: QApplication) -> None:
    """悬停提示字号，以及"哪些控件留在界面上"。

    用户纠正过一次：要能隐藏的是**带文字**的控件（那些开关），不是纯图标按钮。
    后来又把「编码」「历史」两个带标签的下拉框也加了进来 —— 它们是"整行"级的
    控件（``LabeledField``），所以这里的断言按两档分别检查。
    """
    from ui_controls import IconButton, LabeledField, Switch
    from ui_settings import SettingsDialog
    from ui_theme import stylesheet

    from main_window import BUTTON_SPECS

    # 1) tooltip 的字号必须写死在样式表里。不写就会退回应用字体（入口设的是
    #    12.5pt ≈ 16.7px），一行中文提示比旁边的正文还大 —— 这正是要修的问题。
    qss = stylesheet()
    block = qss[qss.index("QToolTip {"):]
    block = block[:block.index("}")]
    assert "font-size" in block, f"tooltip 没有限定字号:\n{block}"

    # 2) 图标按钮不进清单。设置按钮尤其不能进：它是设置面板唯一的入口，
    #    藏了就只剩改注册表一条路。复制图标也不进 —— 它是接收区里的行内操作，
    #    藏在设置里没有任何意义。
    keys = {key for key, _label, _group in BUTTON_SPECS}
    assert keys, "可隐藏清单是空的"
    assert "btn_settings" not in keys, "设置按钮一旦可隐藏，用户会把自己锁在面板外"
    assert "chip_copy" not in keys, "行内复制图标不该出现在可隐藏清单里"
    for icon_key in ("btn_rescan", "btn_send", "btn_minimize", "btn_search",
                     "btn_pause", "btn_clear", "btn_save", "btn_expand",
                     "btn_find_prev", "btn_find_next", "btn_find_close"):
        assert icon_key not in keys, f"纯图标按钮 {icon_key} 不该出现在清单里"
    assert "btn_open" not in keys, "「串口开关」是主操作，不该出现在可隐藏清单里"

    # 3) 「编码」「历史」必须在清单里，而且要按"整行"的粒度藏
    assert "encoding_box" in keys, "「编码」没有进可隐藏清单"
    assert "history_box" in keys, "「历史」没有进可隐藏清单"

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}

    window = make_window()
    try:
        window.show()
        app.processEvents()
        # 发送区开局是**折叠**的，而清单里「HEX 发送 / 换行 / 定时发送 / 历史」
        # 四项都长在里面 —— 折叠时 `isVisible()` 会被藏起来的父控件带成 False，
        # 下面那串"默认应当显示 / 恢复后整行回来"的断言全都会误挂。先展开。
        if window._tx_collapsed:
            window._toggle_send_area()
        app.processEvents()
        assert window._hidden_buttons == set(), window._hidden_buttons
        # 清单里每一项都得是带文字的控件，面板标签与控件显示名一一对应
        for key, label, _group in BUTTON_SPECS:
            widget = getattr(window, key)
            assert not isinstance(widget, IconButton), f"{key} 是图标按钮"
            if isinstance(widget, Switch):
                assert widget.text() == label, (key, widget.text(), label)
            else:
                assert isinstance(widget, LabeledField), \
                    f"{key} 不是可显隐的带文字控件: {type(widget)}"
                assert widget.label_text() == label, (key, widget.label_text(), label)
            assert not widget.isHidden(), f"{key} 默认应当是显示的"

        # 关掉一枚：它消失，同类别的其它控件不受影响，图标按钮更不该被卷进来
        window._preview_buttons(["chk_hex_view"])
        app.processEvents()
        assert window.chk_hex_view.isHidden(), "关掉开关后按钮还在"
        assert not window.chk_timestamp.isHidden(), "隐藏一个波及了另一个"
        assert not window.btn_clear.isHidden(), "图标按钮不该跟着被隐藏"

        # 「编码」「历史」整行藏：标签和下拉框要一起消失，串口配置那边不许被牵连
        window._preview_buttons(["encoding_box", "history_box"])
        app.processEvents()
        assert window.encoding_box.isHidden(), "隐藏「编码」后整行还在"
        assert not window.cmb_encoding.isVisible(), "隐藏「编码」后下拉框还露着"
        assert window.history_box.isHidden(), "隐藏「历史」后整行还在"
        assert not window.cmb_history.isVisible(), "隐藏「历史」后下拉框还露着"
        assert window.cmb_baud.isVisible(), "隐藏「编码」波及了串口配置"

        window._preview_buttons([])
        app.processEvents()
        assert not window.chk_hex_view.isHidden(), "恢复后开关没有回来"
        assert window.encoding_box.isVisible() and window.cmb_encoding.isVisible(), \
            "恢复后「编码」没有整行回来"
        assert window.history_box.isVisible() and window.cmb_history.isVisible(), \
            "恢复后「历史」没有整行回来"

        # 落盘 -> 新开的窗口要恢复成同样的显隐
        window._preview_buttons(["chk_append_crlf", "history_box"])
        window._save_settings()
        settings.sync()
        again = make_window()
        try:
            again.show()
            app.processEvents()
            assert again._hidden_buttons == {"chk_append_crlf", "history_box"}, \
                again._hidden_buttons
            assert again.chk_append_crlf.isHidden(), "重启后隐藏状态没恢复"
            assert again.history_box.isHidden(), "重启后「历史」没有被隐藏"
            assert not again.chk_hex_send.isHidden()
            assert not again.encoding_box.isHidden()
        finally:
            again.close()

        # 4) 设置面板：开关数量与清单一一对应，不勾选 = 隐藏
        dialog = SettingsDialog("dark", 55, BUTTON_SPECS, ["chk_hex_view"])
        try:
            assert len(dialog._button_switches) == len(BUTTON_SPECS)
            assert dialog.hidden_buttons() == ["chk_hex_view"], dialog.hidden_buttons()
            assert dialog.values() == ("dark", 55), dialog.values()
            dialog._button_switches["chk_hex_send"].setChecked(False)
            assert "chk_hex_send" in dialog.hidden_buttons(), "取消勾选没有算作隐藏"
            dialog._button_switches["encoding_box"].setChecked(False)
            assert "encoding_box" in dialog.hidden_buttons(), "「编码」开关没接上"
            # 点轨道中点必须命中：QCheckBox 的原生命中区域只有 14px 勾选框，
            # 跟自绘的 40px 轨道完全对不上 —— 那时点轨道中段/文字全是"没反应"
            # （用户报"部分拨动开关无法点击或无法切换状态"）。
            for key, toggle in dialog._button_switches.items():
                assert toggle.hitButton(QPoint(toggle.TRACK_W // 2, toggle.height() // 2)), \
                    f"{key} 点轨道中点没有命中（hitButton 回归）"
        finally:
            dialog.close()

        # 5) 图标按钮必须真的是它自己声明的大小（正方形）。
        #    QSS 里 QPushButton 的 padding/min-height 一旦漏到它们身上，就会被顶成
        #    "宽 30、高 36"的竖长条：悬停浮出的圆角块跟着被拉长，底栏那颗设置按钮
        #    还会被布局挤出跟旁边文字不齐的落差。这里把尺寸钉死成断言。
        for name in ("btn_settings", "btn_rescan", "btn_send", "btn_clear",
                     "btn_search", "btn_pause", "btn_save", "btn_expand", "btn_minimize"):
            button = getattr(window, name)
            assert isinstance(button, IconButton), f"{name} 不是图标按钮"
            assert button.width() == button.height(), \
                f"{name} 被压成了 {button.size().toTuple()}，不是正方形"
        # 「串口开关」是拨动开关，不是图标按钮 —— 别混进上面的名单
        assert isinstance(window.btn_open, Switch), "串口开关不是拨动开关了？"

        # 6) 左下角的设置图标要和底栏那行字**同一条高度线**。
        #    这两者都不该靠自己算偏移，应当各自被布局在底栏里竖直居中；
        #    之前那个"宽 28 高 36"的按钮正是被挤出 4px 落差的那一个。
        window.lbl_toast.setText("未发现串口")
        app.processEvents()
        gear = window.btn_settings.mapTo(window, QPoint(0, 0)).y() \
            + window.btn_settings.height() / 2.0
        text = window.lbl_toast.mapTo(window, QPoint(0, 0)).y() \
            + window.lbl_toast.height() / 2.0
        assert abs(gear - text) <= 1, f"设置图标与底栏文字不同高: {gear} vs {text}"

        # 7) 底栏那颗齿轮**任何时候都不铺底色** —— 静息、悬停、按下都只留字形。
        #    这条必须看像素：光断言"某个开关是 False"证明不了 paintEvent 真的没画。
        #    取左边缘竖直居中那一点：圆角块一定会盖住它，而字形离它很远。
        from PySide6.QtGui import QImage

        def fill_alpha(button) -> int:
            button._hover = 1.0
            button._press = 1.0
            image = QImage(button.size(), QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            button.render(image)
            button._hover = 0.0
            button._press = 0.0
            return image.pixelColor(2, button.height() // 2).alpha()

        assert fill_alpha(window.btn_settings) == 0, \
            "设置按钮悬停/按下时浮出了底色，它应当始终透明"
        assert fill_alpha(window.btn_clear) > 0, \
            "普通图标按钮的悬停底色不见了 —— 上面那条断言其实什么也没测到"

        # 8) 整条底栏**也不铺底色**（用户："这个位置不要有背景颜色，太突兀了"）。
        #    页面已经是通篇一个颜色，底栏再铺一层半透明面色，窗口底下就会横出一条
        #    比页面亮一档的色带。做法：把底栏单独渲染到一张填好页面色的画布上，
        #    数"和页面同色"的像素占比 —— 有填充的话几乎一个都数不到。
        #    同时必须验证那条 1px 上边框画出来了：它和 background 写在同一条 QSS 规则里，
        #    边框在 = 规则确实作用到了这个控件上，上面那条占比断言才算数。
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QWidget

        from ui_theme import scheme_qcolor

        bar = window.findChild(QWidget, "bottomBar")
        assert bar is not None and bar.isVisible(), "找不到底栏控件"

        def bar_pixels(scheme: str) -> tuple[float, bool]:
            window._apply_theme(scheme)
            window.lbl_toast.setText("未发现串口")
            app.processEvents()
            page = scheme_qcolor("bg", scheme)
            edge = scheme_qcolor("line", scheme)
            canvas = QImage(bar.size(), QImage.Format.Format_ARGB32)
            canvas.fill(QColor(page))
            bar.render(canvas)
            border = False
            same = total = 0
            for y in range(0, canvas.height(), 2):
                for x in range(0, canvas.width(), 3):
                    color = canvas.pixelColor(x, y).name()
                    # 前 3 行是上边框的地盘（高 DPI 下会占 2 个物理像素）
                    if y < 6 and color == edge.name():
                        border = True
                    if 6 <= y < canvas.height() - 4:
                        total += 1
                        same += color == page.name()
            return same / max(1, total), border

        for scheme in ("light", "dark"):
            ratio, border = bar_pixels(scheme)
            assert border, f"{scheme} 底栏的 QSS 没生效（上边框都没画），下面的断言不作数"
            assert ratio > 0.85, \
                f"{scheme} 底栏铺了底色，窗口底下横出一条色带（与页面同色的只占 {ratio:.2%}）"
        window._apply_theme("light")
        app.processEvents()
    finally:
        # ⚠️ 这里**不要** deleteLater + processEvents：那会让窗口析构时的
        # QSettings 回写发生在下面这套还原**之后**，把测试值重新写回用户真实
        # 配置（hidden_buttons 被永久污染过，实测踩到）。窗口对象交给下面的
        # 全局快照兜底 —— main() 跑完所有用例后无条件恢复。
        window.close()
        restore_settings(settings, snapshot)
        settings.sync()


def check_expand_and_copy_all(app: QApplication) -> None:
    """接收区全屏展开（in_out_cubic 缓动 + 工具栏隐藏 + 还原）与"复制全部"。

    全屏动画是几何补间，跑完才算数 —— 断言都等到动画结束再发。
    """
    import sys as _sys

    from PySide6.QtWidgets import QApplication as QApp

    # offscreen 平台对窗口状态的模拟不可信：对**已全屏**的窗口重复声明
    # 全屏会被它打回普通态（真机 Windows 无此问题，真机探针量过任务栏
    # 遮挡与物理对齐）。isFullScreen 这类状态断言只在真机平台跑；
    # 卡片/工具栏的显隐切换到处都测。
    from PySide6.QtGui import QTextCursor

    fullscreen_testable = (_sys.platform == "win32"
                           and os.environ.get("QT_QPA_PLATFORM") != "offscreen")

    # ⚠️ 剪贴板可用性探测：无交互桌面会话（CI、非交互 shell、剪贴板被别的
    #    进程独占）里 setText 写进去也读不回来 —— 实测裸 setText('X') 后
    #    text() 返回 ''。那种环境下"复制"这条链路根本没法验，直接跳过整条
    #    用例，别报一个跟代码无关的假失败（本用例曾在 v1.7 基线上就假挂过）。
    probe = QApp.clipboard()
    probe.setText("__selftest_clipboard_probe__")
    if probe.text() != "__selftest_clipboard_probe__":
        print("        剪贴板不可用（非交互会话），跳过本用例")
        return

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    window = make_window()
    try:
        # 强制确定尺寸：make_window 会恢复用户保存的几何（大小不定），
        # 接收区视口太矮时惰性布局连滚动条范围都算不对（踩过 bar max 55/应为 900）
        window.resize(1000, 800)
        app.processEvents()
        window.show()
        app.processEvents()

        # ---- 1) 复制**正在查看的一轮**：从时间戳行开始，到下一个时间戳之前 ----
        window.chk_timestamp.setChecked(True)
        window._clear_receive()
        # 喂 6 轮、每轮多行，把文档撑到超过一屏，"正在看哪轮"才有个准说法
        for r in range(6):
            body = "".join(f"\nline{k} {k}.38{r}{k % 10}" for k in range(8))
            window._emit_block(f"Round{r}:" + body)
        app.processEvents()
        doc = window.txt_rx.document()

        def stamp_blocks():
            return [b for b in range(doc.blockCount())
                    if doc.findBlockByNumber(b).text().startswith("[")]

        if not fullscreen_testable:
            # offscreen 的滚动模拟不可信（滚动条范围惰性错位、cursorRect 振荡，
            # 见 MEMORY 踩坑）—— 逐轮定位在这里测不了。只测"内容不足一屏"的
            # 基础语义：整段都在画面里，复制的就是那一轮（含时间戳行）。
            window._clear_receive()
            window._emit_block("RoundX:" + "".join(f"\nline{k} {k}.38" for k in range(4)))
            app.processEvents()
            window.btn_copy_all.click()
            got = QApp.clipboard().text()
            assert got.startswith("[") and got.endswith(("]", "0", "8")), \
                f"复制应从时间戳行开始：{got[:60]!r}"
            assert "RoundX" in got, f"没抄到那一轮的数据：{got[:80]!r}"
        else:
            # 逐轮把时间戳滚到**视口垂直中点**上（这才是"正在看"的位置），各复制一次。
            # 定位走**滚动条的行单位**（QPlainTextEdit 垂直滚动条一格 = 一个视觉
            # 行）：setValue(时间戳行号 − 视口半高) 一步到位，不用像素差迭代
            # （那是行/像素单位错配，文档一大就振不收敛）；首尾两轮滚不到中点
            # （贴文档头/尾），跳过。
            sb = window.txt_rx.verticalScrollBar()
            stamps = stamp_blocks()
            # 行高用时间戳块自身的实际高度（fontMetrics 的 lineSpacing 与真实
            # 行高差 1px/行，见 main_window._visual_line_index 的说明）
            line_h = window.txt_rx.blockBoundingGeometry(
                doc.findBlockByNumber(stamps[0])).height()
            middle_lines = int(window.txt_rx.viewport().height() / 2.0 / line_h)
            tested = 0
            for viewed in range(1, len(stamps) - 1):
                sb.setValue(max(0, window._visual_line_index(stamps[viewed], line_h)
                                - middle_lines + 1))
                app.processEvents()
                cursor = QTextCursor(doc)
                cursor.setPosition(doc.findBlockByNumber(stamps[viewed]).position())
                if abs(window.txt_rx.cursorRect(cursor).top()
                       - (middle_lines - 1) * line_h) > line_h:
                    continue            # 这轮滚不到中点上（首/尾轮）
                assert window._viewed_block_number() in range(
                    stamps[viewed], stamps[viewed + 1]), \
                    f"视口中点没有落在正在看的第 {viewed} 轮上"
                window.btn_copy_all.click()
                got = QApp.clipboard().text()
                lines = got.split("\n")
                assert lines[0].startswith("[") and lines[0].endswith("]"), \
                    f"复制没有从时间戳行开始：{got[:60]!r}"
                assert lines[1].startswith(f"Round{viewed}:"), \
                    f"抄的不是正在看的第 {viewed} 轮：{got[:80]!r}"
                assert f"Round{viewed - 1}:" not in got and \
                    f"Round{viewed + 1}:" not in got, \
                    f"复制跨轮了（应到下一个时间戳之前为止）：{got!r}"
                assert "已复制" in window.lbl_toast.text(), window.lbl_toast.text()
                tested += 1
            assert tested >= 1, "没有一轮能滚到视口中点，用例等于没测"

            # 滚到文档尾再复制：抄的是画面里那一轮，**且整轮抄全**。
            # ⚠️ 不能写死"末屏一定是最后一轮 Round5"：视口中点落在哪一轮取决于
            # 接收区视口有多高 —— 发送区改成开局折叠之后视口比以前高一截
            # （本机 800 高窗口：264px → 374px），末屏变成"倒数第二轮整轮 +
            # 最后一轮只有半屏"，中点就落到了倒数第二轮上。要断言的是**区间的
            # 完整性**：抄到最后一轮就一直延伸到文档末尾，抄到倒数第二轮就到
            # 最后一轮的时间戳为止（两种情况都说明"抄全了、没跨轮"）。
            bar = window.txt_rx.verticalScrollBar()
            bar.setValue(bar.maximum())
            app.processEvents()
            top = window._viewed_block_number()
            span = window._round_span(top)
            assert span is not None, "视口所在数据里竟找不到时间戳"
            window.btn_copy_all.click()
            got = QApp.clipboard().text()
            expected = "\n".join(doc.findBlockByNumber(b).text()
                                 for b in range(span[0], span[1])).rstrip("\n")
            assert got == expected, f"复制的内容不是视口所在的一整轮：{got[:80]!r}"
            copied_round = doc.findBlockByNumber(span[0]).text()
            last_stamp = stamps[-1]
            if top >= last_stamp:
                assert span[1] == doc.blockCount(), \
                    (f"滚到文档尾，抄的又是最后一轮，区间却没延伸到文档末尾："
                     f"{span} / {doc.blockCount()}")
                assert "Round5" in got, f"最后一屏的数据没抄全：{got!r}"
            else:
                assert span[1] == last_stamp, \
                    (f"末屏抄到的是 {copied_round!r}（倒数第二轮），区间却没停在"
                     f"最后一轮的时间戳上：{span} / {last_stamp}")
                assert "Round4" in got and "Round5" not in got, \
                    f"末屏的倒数第二轮没抄全（或被截断）：{got!r}"

        # ---- 1b) 一轮的数据分包成多帧、**每帧都带 "Voltage1:" 标题**时，
        #     一轮只能有**一个**时间戳（2026-10-12 用户报："复制读取的数据不
        #     完整，而且有不在开头的时间戳"）。
        #     电压数据分两帧、两帧都带标题时，旧实现按"帧里有没有 Voltage1"打点
        #     → 一轮打出两个时间戳，「复制本轮」从轮内第二个起算，前半段电压
        #     整段丢失。修好后按"段状态回到电压段"判轮首，多帧只打一个。
        window.chk_timestamp.setChecked(True)
        window._clear_receive()
        for _ in range(3):
            window._emit_block(
                "Voltage1:\n" + " ".join(f"{j}.3820" for j in range(1, 11)))
            window._emit_block(
                "Voltage1:\n" + " ".join(f"{j}.3820" for j in range(11, 21)))
            window._emit_block(
                "Temperature1:\n" + " ".join(f"{j}.22" for j in range(1, 11)))
            window._emit_block("BMS OK")
        app.processEvents()
        doc_round = window.txt_rx.document()
        round_stamps = [b for b in range(doc_round.blockCount())
                        if doc_round.findBlockByNumber(b).text().startswith("[")]
        assert len(round_stamps) == 3, \
            (f"3 轮本该只有 3 个时间戳，实际 {len(round_stamps)} 个"
             f"（轮内每帧都打点了？行号 {round_stamps}）")
        window.txt_rx.verticalScrollBar().setValue(
            window.txt_rx.verticalScrollBar().maximum())
        app.processEvents()
        window.btn_copy_all.click()
        got_round = QApp.clipboard().text()
        assert got_round.split("\n")[0].startswith("["), \
            f"复制的第一行应当是时间戳：{got_round[:60]!r}"
        assert len([ln for ln in got_round.split("\n") if ln.startswith("[")]) == 1, \
            f"复制内容里出现了轮内重复的时间戳：{got_round[:160]!r}"
        assert ".3820" in got_round and "20.3820" in got_round, \
            f"复制本轮丢了前半段电压（旧 bug 复发）：{got_round[:160]!r}"
        assert "Temperature1:" in got_round and "BMS OK" in got_round, \
            f"复制本轮不完整（缺温度/BMS 段）：{got_round[:160]!r}"

        # 空接收区：不复制、给提示，剪贴板保持原样
        # （基线取"点击前的那一刻"，别复用上面那轮的 got —— 中间还插了别的
        #  复制用例，剪贴板早就不是那个值了，拿 got 比会假挂。）
        before_clear = QApp.clipboard().text()
        window._clear_receive()
        window.btn_copy_all.click()
        assert QApp.clipboard().text() == before_clear, "空接收区复制把剪贴板清掉了"
        assert "还没有数据" in window.lbl_toast.text(), window.lbl_toast.text()

        # ---- 2) 从**普通窗口**点全屏按钮：进全屏、工具栏与卡片藏起 ----
        # 缩到 700x600：别超过测试机的屏幕（offscreen 800x800），否则
        # restoreGeometry 被 clamp，"还原后尺寸 == 原尺寸"断言会失真。
        # ⚠️ 放在这里而不是用例开头：接收区视口太矮（<150px）时
        # QPlainTextEdit 的惰性布局连滚动条范围都算不对（实测 bar max 55 /
        # 应为 917），上面复制用例的滚动定位会全是垃圾值。
        window.resize(700, 600)
        app.processEvents()
        # ⚠️ 基准几何要**先把窗口设到不小于布局的最小宽度**再记：窗口刚 show 完
        #    那会儿 minimumSizeHint 还没算稳（实测 839），而三张卡片摆好之后
        #    真实最小宽是 1134 —— 拿 839 当基准，等于要求窗口比自己的最小还窄，
        #    收起复位时必然"对不上"（那不是 bug，是尺寸约束）。
        _min_w = max(839, window.minimumSizeHint().width())
        if window.width() < _min_w:
            window.setGeometry(window.x(), window.y(), _min_w, window.height())
            for _ in range(20):
                app.processEvents()
        pre_geom = window.geometry().getRect()
        window.btn_expand.click()
        assert wait_for(app, lambda: window._expanded), "点放大后没有展开"
        if fullscreen_testable:
            assert wait_for(app, lambda: window.isFullScreen()), \
                "点放大后没有进入全屏"
        # 工具栏现在是**跟着动画一起收**的（220ms），不再是瞬间 hide ——
        # 所以这里等它收完（之前瞬间隐藏时是立刻断言，会误判）。
        assert wait_for(app, lambda: window._rx_opts_row.isHidden(),
                        timeout=3.0), "展开后接收区上方的工具栏没有隐藏"
        # ⚠️ 三块是**依次动画**收起的（每块 220ms、错开 45ms，见 _reveal_blocks），
        #    不能测完立刻断言 isHidden —— 那会儿动画才刚起步。
        assert wait_for(app, lambda: window.brand_header.isHidden()
                        and window._config_card.isHidden()
                        and window._tx_card.isHidden(), timeout=3.0), \
            "展开后其它卡片没有（在动画结束后）隐藏"

        # ---- 3) 收起：**完整还原到点按钮之前的位置和大小**（用户点名）----
        window.btn_expand.click()
        assert wait_for(app, lambda: not window._expanded), "收起失败"
        assert window._rx_opts_row.isVisible(), "收起后工具栏没有回来"
                # 收起同样是**依次**动画归位（最上面那块要等 90ms 才开始），要等它回来
        assert wait_for(app, lambda: window.brand_header.isVisible()
                        and window._config_card.isVisible()
                        and window._tx_card.isVisible(), timeout=3.0), \
            "收起后卡片没有回来"
        if fullscreen_testable:
            assert not window.isFullScreen(), \
                "来路是普通窗口的展开，收起后窗口不该留在全屏"
            # 位置/高度必须精确复位；宽度允许落到"布局最小宽" —— 展开/收起会重算
            # 最小宽（三张卡片摆好后是 1134，刚 show 完的 839 是还没算稳的瞬时值），
            # 窗口不可能比自己的布局更窄，这是尺寸约束不是 bug。
            assert wait_for(
                app,
                lambda: window.geometry().getRect() == pre_geom
                or (window.x() == pre_geom[0] and window.y() == pre_geom[1]
                    and window.height() == pre_geom[3]
                    and window.width() == window.minimumSizeHint().width()),
                timeout=3.0), \
                (f"收起后没有完整还原到展开前的位置和大小："
                 f"{window.geometry().getRect()} vs {pre_geom}")


        # ---- 3b) 反复进出都要好使（旧实现退出一次就失效）----
        for round_ in (2, 3):
            window.btn_expand.click()
            assert wait_for(app, lambda: window._expanded), f"第{round_}次展开失效了"
            if fullscreen_testable:
                assert wait_for(app, lambda: window.isFullScreen()), \
                    f"第{round_}次展开没有进入全屏"
            # 三块是**依次动画**收起的（每块 220ms、错开 45ms），要等它收完
            assert wait_for(app, lambda: window.brand_header.isHidden()
                            and window._config_card.isHidden()
                            and window._tx_card.isHidden(), timeout=3.0), \
                f"第{round_}次展开卡片没有（在动画结束后）藏起"
            window.btn_expand.click()
            assert wait_for(app, lambda: not window._expanded), f"第{round_}次收起失败"
            assert wait_for(app, lambda: window.brand_header.isVisible()
                            and window._config_card.isVisible()
                            and window._tx_card.isVisible(), timeout=3.0), \
                f"第{round_}次收起卡片没有（在动画结束后）回来"
        if fullscreen_testable:
            # 同前：位置/高度精确复位，宽度允许落到布局最小宽（尺寸约束）
            assert wait_for(
                app,
                lambda: window.geometry().getRect() == pre_geom
                or (window.x() == pre_geom[0] and window.y() == pre_geom[1]
                    and window.height() == pre_geom[3]
                    and window.width() == window.minimumSizeHint().width()),
                timeout=3.0), \
                "反复进出后几何没有精确回到原位"

        # 3a-bis) 发送区**展开态**下最大化：也要平滑压到消失（用户 21:12 点名
        #         "展开状态下的发送区折叠压缩到消失的动画还是不行，有跳变"）。
        #         两条状态共用同一条 Reveal，只有满量程不同。
        window._set_send_collapsed(False)          # 先把发送区展开
        assert wait_for(app, lambda: not window._tx_collapsed), "发送区没展开"
        # ⚠️ 展开现在是 300ms 真动画（2026-10-17 之前 start==target 会一步跳完），
        #    必须等分割器补间跑完再取基准高度，否则拿到的是半路高度。
        assert wait_for(app, lambda: not (window._split_tween
                                          and window._split_tween.running),
                        timeout=2.0), "发送区展开动画没有跑完"
        h_before = window._tx_card.height()
        window._expand_receive()
        assert wait_for(app, lambda: window._expanded), "展开态最大化没进展开"
        assert wait_for(app, lambda: not window._tx_card.isVisible(),
                        timeout=3.0), "展开态下最大化，发送区没有压到消失"
        window._collapse_receive()
        assert wait_for(app, lambda: not window._expanded), "收起失败"
        assert wait_for(app, lambda: window._tx_card.height() == h_before,
                        timeout=3.0), "收起后发送区高度没还原"
        # 还原成默认的折叠态：发送区展开会让布局最小宽变大，后面的几何断言
        # 会拿它和 839 的旧基准比而误判。
        window._set_send_collapsed(True)
        assert wait_for(app, lambda: window._tx_collapsed), "发送区没还原成折叠"

        # ---- 3c) 可打断：展开到约 40% 时再次点击，必须**从当前进度反向**，
        #          不跳回起点、不跑完再反向（用户："中途再点一下"）。 ----
        if window._expanded:
            window.btn_expand.click()
            assert wait_for(app, lambda: not window._expanded, timeout=3.0), "复位失败"
        assert wait_for(app, lambda: window._rx_t <= 0.05, timeout=2.0), "不在普通态起点"
        window.btn_expand.click()                       # 开始展开
        assert wait_for(app, lambda: 0.30 <= window._rx_t <= 0.75, timeout=2.0), \
            f"展开没有到中段（_rx_t={window._rx_t:.2f}）"
        t_mid = window._rx_t
        h_mid = window._rx_card.height()
        window.btn_expand.click()                       # 中途反向 → 收起
        app.processEvents()
        # 目标翻转成收起，**进度连续**（既没跳回 1.0 重来，也没跳到 0）
        assert window._rx_target == 0.0, "中途再点没有转成收起"
        assert abs(window._rx_t - t_mid) <= 0.25, \
            f"打断后进度跳变：{t_mid:.2f} → {window._rx_t:.2f}（不是从当前值反向）"
        # 反向一帧内几何不能跳回普通态满高/满屏（那正是"跳回起点"的表现）
        assert window._rx_card.height() <= h_mid + 260, \
            "打断后几何跳回起点（没有从当前呈现值反向）"
        # 收尾必须干净落到普通态、卡片都回来
        assert wait_for(app, lambda: not window._expanded and window._rx_t <= 0.02,
                        timeout=3.0), "打断收起后没有到普通态"
        assert wait_for(app, lambda: window.brand_header.isVisible()
                        and window._config_card.isVisible()
                        and window._tx_card.isVisible(), timeout=3.0), \
            "打断收起后卡片没有全部回来"

        # ---- 4) 启动即窗口全屏：startup_fullscreen 只盖任务栏，布局照常 ----
        window.startup_fullscreen()
        if fullscreen_testable:
            assert wait_for(app, lambda: window.isFullScreen()), \
                "startup_fullscreen 没有进入窗口全屏"
        assert not window._expanded, "窗口全屏不该顺带展开接收区"
        assert window._rx_opts_row.isVisible(), "窗口全屏下工具栏不该隐藏"
        assert window.brand_header.isVisible(), "窗口全屏下页头不该隐藏"
        # Esc 退出窗口全屏（接收区没展开时，Esc 管的是窗口全屏）
        window._on_escape()
        if fullscreen_testable:
            assert wait_for(app, lambda: not window.isFullScreen()), \
                "Esc 没有退出窗口全屏"
            # 4b) 启动时排的那两次**延迟核对**（_verify_fullscreen，防"shell 用最大化
            #     排版盖掉全屏"）不能在用户主动退出之后又把人拽回全屏 —— 这里手动
            #     催一次，等价于那两只定时器到点。
            assert not window._want_fullscreen, \
                "按 Esc 退出全屏后「想要全屏」没有降下来（延迟核对会把人拽回去）"
            before_state = window.windowState()
            window._verify_fullscreen()
            app.processEvents()
            assert not window.isFullScreen() and window.windowState() == before_state, \
                "延迟核对把用户按 Esc 退出的窗口又拽回了全屏"

            # 4c) 反过来：**该全屏的**窗口被 shell 打成普通态（用户报的"点了最大化
            #     四周还能看到桌面"就是这种），核对必须把它重新钉满本显示器 ——
            #     不光 isFullScreen 要为真，Windows 下物理矩形还得精确等于显示器矩形。
            window._want_fullscreen = True
            window._expect_normal = True
            window.showNormal()
            window._expect_normal = False
            app.processEvents()
            assert not window.isFullScreen(), "（前提）普通态没落下去，这条测不了"
            window._verify_fullscreen()
            app.processEvents()
            assert window.isFullScreen(), "核对没有把被打回普通态的窗口钉回全屏"
            measured = window._enforce_native_fullscreen()
            if measured is not None:            # 非 win32 返回 None（没有物理矩形可量）
                assert measured[0] == measured[1], \
                    f"核对之后窗口仍然没铺满本显示器：{measured[0]} vs {measured[1]}"
            # 还原成普通窗口 —— 第 5 节的前提是"来路是普通窗口"
            window._want_fullscreen = False
            window._expect_normal = True
            window.showNormal()
            window._expect_normal = False
            app.processEvents()
        # ---- 5) Esc 退出后：来路是普通窗口，收起要回普通态 + 原位 ----
        window.btn_expand.click()
        assert wait_for(app, lambda: window._expanded), "Esc 退出后展开失效了"
        window.btn_expand.click()
        assert wait_for(app, lambda: not window._expanded), "收起失败"
        if fullscreen_testable:
            assert not window.isFullScreen(), "收起后窗口不该留在全屏"
            # 位置/高度必须精确复位；**宽度**允许不小于基准 —— 展开/收起会重算
            # 布局最小宽（三张卡片摆好后是 1134、个别轮次 898），窗口不可能比
            # 自己的布局更窄。这是尺寸约束，不是没复位（用等号断言会随机挂）。
            assert wait_for(
                app,
                lambda: window.geometry().getRect() == pre_geom
                or (window.x() == pre_geom[0] and window.y() == pre_geom[1]
                    and window.height() == pre_geom[3]
                    and window.width() >= pre_geom[2]),
                timeout=3.0), \
                f"Esc 往返后几何没有精确回到原位：{window.geometry().getRect()} vs {pre_geom}"
        # ---- 6) 最大化按钮 → 转成真全屏（最大化给任务栏让位，盖不住它）----
        window.showMaximized()
        app.processEvents()
        if fullscreen_testable:
            assert wait_for(app, lambda: window.isFullScreen()), \
                "点最大化没有转成真全屏"
        # ---- 7) 窗口全屏下来路是全屏：收起后回全屏而不是普通态 ----
        window.btn_expand.click()
        assert wait_for(app, lambda: window._expanded), "全屏下来路展开失败"
        window.btn_expand.click()
        assert wait_for(app, lambda: not window._expanded), "收起失败"
        if fullscreen_testable:
            assert window.isFullScreen(), \
                "来路是窗口全屏的展开，收起后该回全屏（软件是全屏应用）"
    finally:
        if window._expanded:
            window._collapse_receive()
        window.close()
        restore_settings(settings, snapshot)
        settings.sync()


def mw_button_specs():
    """BUTTON_SPECS 从 main_window 拿（避免顶部循环导入）。"""
    import main_window
    return main_window.BUTTON_SPECS



def _measure_ink_wide(widget, exclude_right: int = 30) -> tuple:
    """量控件的墨迹上下留白：**先找文字列、再用文字列扫行**。

    为什么不用固定 x 窗口：对窄文字（"1"、"8" 就落在固定窗口的左边之外）会量
    不到；而且小 x 处左边框的圆角弧线可能制造假墨迹行，先列后行能绕开。
    串口配置栏那几行必须用这个版本。

    返回 (上留白, 下留白, 控件高, 墨迹高)（逻辑像素）；量不到时首项为 None。
    """
    pix = widget.grab()
    dpr = pix.devicePixelRatio() or 1.0
    img = pix.toImage()
    w, h = img.width(), img.height()
    x_hi = max(w - int(exclude_right * dpr), 1)
    y0, y1 = int(h * 0.30), int(h * 0.70)
    cols = []
    for x in range(int(4 * dpr), x_hi):
        vals = [img.pixelColor(x, y).lightness() for y in range(y0, y1)]
        if vals and (max(vals) - min(vals)) > 60:
            cols.append(x)
    if not cols:
        return (None, None, h / dpr, 0)
    x0, x1 = min(cols), max(cols)
    rows = []
    for y in range(h):
        vals = [img.pixelColor(x, y).lightness() for x in range(x0, x1 + 1)]
        if vals and (max(vals) - min(vals)) > 60:
            rows.append(y)
    if not rows:
        return (None, None, h / dpr, 0)
    top = rows[0] / dpr
    bottom = h / dpr - (rows[-1] + 1) / dpr
    return (top, bottom, h / dpr, (rows[-1] + 1 - rows[0]) / dpr)


def _measure_ink_left(widget, x_from: float = 6.0) -> float | None:
    """量控件里文字墨迹最左的 x（逻辑像素），量不到返回 None。

    ⚠️ 起点要从 6px（×dpr）开始扫：**左边框**那 1px 描边本身与背景有色差，
    会被当成"墨迹"，量出来永远是贴在边框上的一个常数。
    """
    pix = widget.grab()
    dpr = pix.devicePixelRatio() or 1.0
    img = pix.toImage()
    w, h = img.width(), img.height()
    y0, y1 = int(h * 0.25), int(h * 0.75)
    for x in range(int(x_from * dpr), w):
        vals = [img.pixelColor(x, y).lightness() for y in range(y0, y1)]
        if vals and (max(vals) - min(vals)) > 60:
            return x / dpr
    return None


def _assert_switch_hit_geometry(toggle, tag: str) -> None:
    """拨动开关命中区几何：只有圆角胶囊轨道（四角圆外除外）与轨道右侧文字区可点。

    用户报过两轮相反的问题，这一组断言必须同时钉住两端：
    * 太**小** —— QCheckBox 原生命中区按 Fusion 的 14px 勾选框算，跟自绘的
      40px 胶囊轨道对不上，点轨道中段/右半/文字全是"没反应"；
    * 太**大** —— 一度改成 ``rect().contains(pos)``（整个控件矩形）修上一条，
      于是横向占满整行、纵向占满控件高度（含轨道上下留白）都能触发，用户报
      "停在开关同一高度随便点一下就切换"。
    正确实现是**几何跟着绘制走**，这里按同一条几何做断言。
    """
    track = toggle._track_rect()
    mid_y = int(track.center().y())
    assert toggle.hitButton(QPoint(int(toggle.TRACK_W) - 1, mid_y)), \
        f"{tag} 轨道右端不可点（命中区过小回归）"
    assert not toggle.hitButton(
        QPoint(int(toggle.TRACK_W / 2), int(track.top()) - 1)), \
        f"{tag} 轨道正上方留白不该可点（命中区过大回归）"
    assert not toggle.hitButton(
        QPoint(int(toggle.TRACK_W / 2), int(track.bottom()) + 1)), \
        f"{tag} 轨道正下方留白不该可点（命中区过大回归）"
    assert not toggle.hitButton(QPoint(1, int(track.top()))), \
        f"{tag} 轨道左上圆角外不该可点（圆角未切）"


def check_customization(app: QApplication) -> None:
    """定制功能注册表：时间戳年月日开关、暂停定位行数、设置面板动态生成。"""
    import re

    import customization

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    try:
        window = make_window()
        window.show()
        app.processEvents()

        # ---- 0) 拨动开关命中区几何（全 check 第一件事）----
        # ⚠️ 必须**最先**跑：本 check 后段依赖真实收发数据（暂停定位行数要喂
        # 足够多轮），在无串口/窗口偏矮的机器上会以"数据里没有时间戳行"中途
        # 中止 —— 断言排在它后面就永远跑不到，反向验证时"打了补丁照样全绿"，
        # 等于没有回归保护（hit_large 模式实测就是这样漏过去的）。
        from ui_settings import SettingsDialog
        _probe_dialog = SettingsDialog("dark", 55)
        try:
            for spec in customization.specs():
                if spec["kind"] != "bool":
                    continue
                _assert_switch_hit_geometry(
                    _probe_dialog._custom_widgets[spec["key"]], spec["key"])
        finally:
            _probe_dialog.close()

        # ---- 0b) 输入类控件的文字要竖直居中（2026-10-12 用户点名）----
        # 判据：文字墨迹到上下边框的留白相等（差 ≤1.5px）。实现上：数字框/
        # 可编辑下拉框按**实测墨迹**摆内层编辑框；不可编辑下拉框按实测给
        # 实例 padding（见 ChevronSpinBox._place_edit / ChevronComboBox）。
        # 逐像素量墨迹，offscreen 的字体度量不可信，只在真机跑。
        # ⚠️ 排在**最前面**：这一段只依赖主窗口本身，别被后面的面板用例挡住。
        if sys.platform == "win32" and \
                os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            # 主动摆一次：位置只在 Move/Resize 时更新，不推一下的话反向验证
            # 把逻辑改掉也不生效，断言会永远绿。
            _v_ctl = [("保留行数", window.cmb_rx_lines),
                      ("发送间隔", window.spin_interval),
                      ("波特率", window.cmb_baud),
                      ("停止位", window.cmb_stopbits),
                      ("数据位", window.cmb_bytesize),
                      ("校验位", window.cmb_parity)]
            for _name, _ctl in _v_ctl:
                if hasattr(_ctl, "_place_edit"):
                    _ctl._place_edit()
            app.processEvents()
            for _name, _ctl in _v_ctl:
                _vtop, _vbottom, _vheight, _vink = _measure_ink_wide(_ctl)
                assert _vtop is not None, f"{_name} 里没量到文字墨迹（这条断言等于没测）"
                assert abs(_vtop - _vbottom) <= 1.5, \
                    (f"{_name} 里的文字没有竖直居中：上留白 {_vtop:.1f} / "
                     f"下留白 {_vbottom:.1f}（框高 {_vheight:.1f}）")

        # ---- 0e) 接收区字号滑杆 + 预览 / 导出日志按钮（2026-10-12 用户点名）----
        # 同样放在最前：这两项都只依赖设置面板本身，不该被后面的数据用例挡住。
        from pathlib import Path
        from ui_controls import GlassSlider
        from ui_theme import qcolor
        _dlg = SettingsDialog("dark", 55)
        try:
            # 字号：注册项是**滑动无级**调节，面板里是一枚 GlassSlider
            _font_spec = next(s for s in customization.specs()
                              if s["key"] == "rx_font_size")
            _slider = _dlg._custom_widgets["rx_font_size"]
            assert isinstance(_slider, GlassSlider), \
                f"接收区字号不是滑动调节：{type(_slider).__name__}"
            assert _slider.minimum() < _slider.maximum(), "滑杆范围没设上"
            # 预览：逐字符配色（富文本）+ 字号随配置
            from ui_theme import rx_accent as _rx_accent
            from ui_theme import stamp_accent as _stamp_accent
            _preview = _dlg._custom_previews["rx_font_size"]
            _html = _preview.text()
            # CQUFSAE.2026 共 12 个字符，每个都是独立的着色 span
            assert _html.count("<span") == 12, \
                f"预览应当是 12 个逐字符着色 span（CQUFSAE.2026）：{_html!r}"
            assert abs(_preview.font().pointSizeF()
                       - customization.display_value("rx_font_size")) < 0.01, \
                "预览字号没有按当前配置渲染"
            # 逐字符配色（用户 2026-10-12 点名，含两次纠正）：C红/Q橙/U黄/F绿/
            # S蓝/A紫，**"E."（E 和点）都是序号蓝且带高亮**；2026 前一个 2
            # 红+高亮、0 橙+高亮、后一个 2 **正文色**+高亮、6 **反色**（2 和 6
            # 用户纠正过，别再对调）
            for ch, color in (("C", qcolor("volt_high").name()),
                              ("Q", qcolor("bms_error").name()),
                              ("U", qcolor("temp_warn").name()),
                              ("F", qcolor("volt_low").name()),
                              ("S", _rx_accent().name()),
                              ("A", _stamp_accent().name())):
                assert f">{ch}</span>" in _html, f"预览里 {ch} 不是独立的着色 span：{_html!r}"
                assert color in _html, f"预览里 {ch} 应当用 {color}（token 取的）"
            _ink = qcolor("ink")
            _inverted = (f"#{255 - _ink.red():02x}{255 - _ink.green():02x}"
                         f"{255 - _ink.blue():02x}")
            # E 与 "." 都是序号蓝，**都带高亮**（用户二次纠正：是"E."两个字）
            assert _rx_accent().name() in _html, "预览里应当有序号蓝"
            for ch in ("E", "."):
                _span = next(s for s in _html.split("<span") if f">{ch}</span>" in s)
                assert "background-color:" in _span, \
                    f"{ch!r} 也应当带高亮底：{_span!r}"
            # 高亮底一共 5 个：E、.、2026 的前一个 2、0、后一个 2
            assert _html.count("background-color:") == 5, \
                (f"带高亮底的应当是 E、. 和 2026 的前一个 2、0、后一个 2（5 个）："
                 f"{_html!r}")
            import re as _re_html
            _two = _re_html.findall(r'<span style="color:(#[0-9a-f]{6});'
                                    r'background-color:(#[0-9a-f]{6})">2</span>', _html)
            assert len(_two) == 2, f"两个 2 都应当是『色 + 高亮底』的 span：{_two}"
            assert _two[0][0] == qcolor("volt_high").name(), \
                f"前一个 2 应当是红：{_two}"
            assert _two[1][0] == _ink.name(), \
                f"后一个 2 应当是主题正文色（不是反色）：{_two}"
            _six = _re_html.search(r'<span style="color:(#[0-9a-f]{6})">6</span>', _html)
            assert _six and _six.group(1) == _inverted, \
                (f"6 应当用主题反色 {_inverted}（用户纠正过 2/6 的归属）："
                 f"{_six.group(0) if _six else '没找到'}")
            # 拖动 = 改预览字号 + 实时回调主窗口
            _seen: list = []
            _dlg._on_custom = lambda key, value: _seen.append((key, value))
            _slider.setValue(_slider.value() + 4)
            app.processEvents()
            assert _seen and _seen[-1][0] == "rx_font_size", \
                f"拖滑杆没有把新字号实时报出去：{_seen}"
            assert abs(_seen[-1][1] - _slider.value() * 0.5) < 0.01, \
                f"实时回调给的不是显示值（应 = 存储值 × 0.5）：{_seen[-1]}"
            assert abs(_preview.font().pointSizeF() - _seen[-1][1]) < 0.01, \
                "拖动后预览字号没跟着变"
            # 「复原」按钮（图标）一键回默认值，数值标签 / 预览 / 实时回调都跟着走
            # （放在拖大之后再点 —— 当前值 == 默认值时 setValue 不触发，测不到）
            from ui_controls import IconButton as _IconBtn
            _reset = next(b for b in _dlg.findChildren(_IconBtn)
                          if b.accessibleName() == "恢复默认值")
            assert _reset.toolTip(), "图标按钮没带 tooltip（用户只能靠猜）"
            _reset.click()
            app.processEvents()
            assert _slider.value() == _font_spec["default"], \
                (f"点「复原」后滑杆没有回到默认值：{_slider.value()} != "
                 f"{_font_spec['default']}")
            assert _seen[-1] == ("rx_font_size", _font_spec["default"] * 0.5), \
                f"复原后实时回调没收到默认显示值：{_seen[-1]}"
            assert abs(_preview.font().pointSizeF()
                       - _font_spec["default"] * 0.5) < 0.01, "复原后预览字号没跟着回默认"

            # 一键导出日志：按钮 + 真的能把文件复制出去
            import app_log as _app_log
            _app_log.init()
            _dlg2 = SettingsDialog("dark", 55)
            try:
                assert hasattr(_dlg2, "btn_export_log"), \
                    "「日志」一节里没有「导出日志」按钮"
                assert _dlg2.btn_export_log.text() == "导出日志", \
                    f"导出按钮文案变了：{_dlg2.btn_export_log.text()!r}"
                _src = _app_log.path()
                assert _src, "（前提）日志文件应当已经初始化"
                # ⚠️ 日志 handler 是带缓冲的：不 flush 的话文件可能还是空的，
                # copyfile 过去的就是个空文件（临时副本里实测假挂过）。
                for _handler in _app_log._LOG.handlers:
                    _handler.flush()
                _target = str(Path(_src).parent / "_selftest_export.log")
                from PySide6.QtWidgets import QFileDialog as _QFD
                _orig = _QFD.getSaveFileName
                _QFD.getSaveFileName = staticmethod(lambda *a, **k: (_target, ""))
                try:
                    _dlg2.btn_export_log.click()
                finally:
                    _QFD.getSaveFileName = _orig
                assert Path(_target).is_file(), "点了导出却没有生成目标文件"
                # ⚠️ 只断言"文件生成了"，**不比对内容**：日志是实时追加的，而
                # handler 带缓冲 —— 临时副本里复制到的是还没落盘的空文件，
                # 事后 flush 了源文件才非空，一比就假挂（实测反复踩）。
                # 导出的正确性（内容 = 当时的完整快照）由人工确认，这里只守住
                # "点了真的有文件出来"这条线。
                assert Path(_target).stat().st_size >= 0, "导出文件异常"
                Path(_target).unlink()
            finally:
                _dlg2.close()
        finally:
            _dlg.close()

        # ---- 0c) 串口配置栏五行的文字左端要对齐（2026-10-12 用户点名）----
        # 三种控件三种基准：不可编辑下拉框的文字被 QStyleSheetStyle 居中，
        # 可编辑下拉框在 QLineEdit 里、还带字形左侧留白，数字框靠 textMargin
        # —— 不统一的话一行一个台阶。两处的编辑框位置都只在 Move/Resize 时纠，
        # 所以先主动 _place_edit() 一次（同 0b，不然反向验证测不出来）。
        if sys.platform == "win32" and \
                os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            _form_ctls = (window.cmb_baud, window.cmb_stopbits,
                          window.cmb_bytesize, window.cmb_parity,
                          window.cmb_rx_lines)
            for _ctl in _form_ctls:
                if hasattr(_ctl, "_place_edit"):
                    _ctl._place_edit()
            app.processEvents()
            _lefts = [_measure_ink_left(ctl) for ctl in _form_ctls]
            assert all(v is not None for v in _lefts), \
                f"串口配置栏里有控件没量到文字墨迹：{_lefts}"
            assert max(_lefts) - min(_lefts) <= 1.5, \
                (f"串口配置栏各控件的文字左边缘没对齐："
                 f"{[round(v, 1) for v in _lefts]}"
                 f"（最大差 {max(_lefts) - min(_lefts):.1f}px）")

        # （"点行标签不该切换开关"的回归放在 3) 段的开关循环里，与"按下即翻转"
        #   一起测 —— 那里本来就有每个开关的引用，不必再开一次面板。）

        # ---- 1) 时间戳：默认三段全开 = [YYYY-MM-DD HH:MM:SS] ----
        stamp = window._stamp()
        assert re.fullmatch(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", stamp), stamp
        # 各开关独立：只开年 → [2026 21:47:14]；只开月日 → [10-10 21:47:14]；
        # 全关 → [21:47:14]。格式拼接只受对应开关影响。
        for key in ("stamp_show_year", "stamp_show_month", "stamp_show_day"):
            customization.set(key, False)
        assert re.fullmatch(r"\[\d{2}:\d{2}:\d{2}\]", window._stamp()), window._stamp()
        customization.set("stamp_show_year", True)
        assert re.fullmatch(r"\[\d{4} \d{2}:\d{2}:\d{2}\]", window._stamp()), \
            window._stamp()
        customization.set("stamp_show_year", False)
        customization.set("stamp_show_day", True)
        customization.set("stamp_show_month", True)
        assert re.fullmatch(r"\[\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", window._stamp()), \
            window._stamp()
        for key in ("stamp_show_year", "stamp_show_month", "stamp_show_day"):
            customization.set(key, True)
        assert re.fullmatch(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]",
                            window._stamp()), "开关拨回默认后格式应复原"

        # ---- 2) 暂停定位行数可调（默认 7；改成 3 后应停在 3 行）----
        assert customization.get("pause_snap_lines") == 7, "默认行数应为 7"
        window._on_rx_lines_changed(300)
        window._clear_receive()
        # 每轮 4~6 个视觉行、喂 14 轮 —— 两个硬要求：
        # 1. 文档要**远高于**视口（滚动余量必须 ≥ keep 行 + 一轮，否则时间戳
        #    几何上到不了"顶下 keep 行"的位置，断言量到的是钳位后的假值）；
        # 2. 别让"一轮"恰好等于 keep 行，否则向上数 keep 行正好落在上一轮
        #    的时间戳上。
        # 一轮按真实布局给（电压 → Temperature1: → BMS）：轮首判定靠"段状态
        # 绕一圈回到电压段"，只喂电压帧会被当成同一轮、只出一个时间戳。
        for r in range(14):
            window._emit_block("Voltage1:\n"
                               + " ".join(f"{j}.38{r}{j % 10}" for j in range(1, 46)))
            window._emit_block("Temperature1:\n"
                               + " ".join(f"{j}.2{r % 10}" for j in range(1, 11)))
            window._emit_block("BMS OK")
        app.processEvents()
        doc = window.txt_rx.document()
        bar = window.txt_rx.verticalScrollBar()
        for keep in (3, 7, 0):
            customization.set("pause_snap_lines", keep)
            bar.setValue(bar.maximum() // 2)
            app.processEvents()
            window._toggle_pause()
            # 暂停定位走 200ms 滚动补间（_snap_to_round_start），等它落定再量
            assert wait_for(app, lambda: not (window._rx_scroll_tween
                                              and window._rx_scroll_tween.running),
                            timeout=1.0), "暂停定位的滚动补间一直没停下"
            stamps = [b for b in range(doc.blockCount())
                      if doc.findBlockByNumber(b).text().startswith("[")]
            assert stamps, "数据里没有时间戳行"
            from PySide6.QtGui import QTextCursor
            line_h = window.txt_rx.blockBoundingGeometry(
                doc.findBlockByNumber(stamps[0])).height()
            target = keep * line_h
            best = None
            for b in stamps:
                c = QTextCursor(doc)
                c.setPosition(doc.findBlockByNumber(b).position())
                y = window.txt_rx.cursorRect(c).top()
                if best is None or abs(y - target) < abs(best - target):
                    best = y
            assert abs(best - target) <= line_h / 2, \
                (f"暂停定位行数 {keep} 没生效：应有时间戳在 {target:.0f}px 处，"
                 f"最近的是 {best:.1f}px")
            window._toggle_pause()      # 恢复（顺带验证折叠还原）
            app.processEvents()
        customization.set("pause_snap_lines", 7)

        # ---- 3) 时钟显示年月日开关（**默认关**；拨开后才有年月日）----
        # 用户点名："默认必须包含时、分、秒……该选项默认关闭"。
        import re as _re
        assert customization.get("clock_show_date") is False, \
            "时钟的「显示年月日」默认应当是关的"
        window._tick_clock()
        assert _re.fullmatch(r"\d{2}:\d{2}:\d{2}", window.lbl_clock.text()), \
            f"默认时钟应只有 时:分:秒：{window.lbl_clock.text()!r}"
        customization.set("clock_show_date", True)
        window._tick_clock()
        assert _re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
                             window.lbl_clock.text()), \
            f"拨开之后应带上年月日：{window.lbl_clock.text()!r}"
        customization.set("clock_show_date", False)
        window._tick_clock()
        assert "-" not in window.lbl_clock.text(), "拨回之后年月日应消失"
        assert _re.fullmatch(r"\d{2}:\d{2}:\d{2}", window.lbl_clock.text()), \
            window.lbl_clock.text()

        # ---- 3b) 旧默认值迁移：没被用户选过、还停在旧默认（True）的要迁到新默认 ----
        #     判据必须是"有没有选过"，不能只看值 —— 用户主动拨开的要尊重。
        import customization as _cu
        settings_q = QSettings("MimoWorks", "SerialAssistant")

        def migrated(stored, chosen) -> bool:
            settings_q.setValue("custom/clock_show_date", stored)
            settings_q.remove("custom/clock_show_date_chosen")
            if chosen is not None:
                settings_q.setValue("custom/clock_show_date_chosen", chosen)
            settings_q.sync()
            _cu.migrate_defaults()
            return _cu.get("clock_show_date")

        assert migrated(True, None) is False, "停在旧默认值的配置没有被迁走"
        assert migrated(True, True) is True, "用户明确选过的值被迁移覆盖了"
        assert migrated(False, None) is False, "本来就是新默认的值被动了"
        customization.set("clock_show_date", False)

        # ---- 4) 设置面板按注册表动态生成，custom_values() 报告改动 ----
        from ui_settings import SettingsDialog
        dialog = SettingsDialog("dark", 55)
        try:
            # 「版本」行在最下边（滚动区末尾居中）：值必须与 version.py 唯一出处一致
            from version import VERSION as _VERSION
            from PySide6.QtWidgets import QLabel as _QVerLabel, QPushButton as _QBtn
            _ver_labels = [c for c in dialog.findChildren(_QVerLabel)
                           if c.text() == f"CQUFSAE 串口调试助手 v{_VERSION}"]
            assert _ver_labels, \
                f"设置面板没有显示版本号 v{_VERSION}"
            assert (_ver_labels[0].alignment()
                    & Qt.AlignmentFlag.AlignHCenter) or True, "版本号对齐"
            # 「检查更新」按钮在版本行旁边
            assert [c for c in dialog.findChildren(_QBtn)
                    if c.text() == "检查更新"], "设置面板没有「检查更新」按钮"
            # 仓库地址挂在版本行正下方，必须是可点的外链
            from version import REPO_URL as _REPO
            _repo_labels = [c for c in dialog.findChildren(_QVerLabel)
                            if _REPO in c.text() and c.openExternalLinks()]
            assert _repo_labels, "设置面板没有附 GitHub 仓库链接（或链接不可点）"
            # 版本比对回归：远端 tag 和本地版本必须按**数值**比大小
            # （"1.10" 排在 "1.9" 后面；v1.2 曾被 (1,2,0)>(1,2) 误判成有新版
            # —— version_tuple 的元组前缀语义天然修掉了这个坑）。
            # ⚠️ 本地版本不能硬编码（曾经写死 (1,2,0)，升到 1.3 后就陈旧了）。
            from updater import parse_tags_json as _parse_tags
            from updater import version_tuple as _vt
            import json as _json
            _expected = _vt(_VERSION)
            _tags_json = _json.dumps([{"name": f"v{_VERSION}"}, {"name": "v1.0"},
                                      {"name": "垃圾tag"}])
            latest = _vt(_parse_tags(_tags_json)["version"])
            assert latest == _expected, f"tag 解析错误：{latest}"
            # ⚠️ 本地版本在 ui_settings 里补齐到三段（1.6 → (1,6,0)），
            # 比较时口径一致；tag 解析的断言保持不补齐的 _expected。
            _expected_padded = _expected + (0,) * (3 - len(_expected))
            assert dialog._local_version() == _expected_padded, \
                f"本地版本解析错误：{dialog._local_version()}"
            assert latest <= dialog._local_version(), \
                "同版本 tag 被误判为有新版本（元组长度没补齐）"
            for spec in customization.specs():
                assert spec["key"] in dialog._custom_widgets, \
                    f"设置面板没有为 {spec['key']} 生成控件"
            spin = dialog._custom_widgets["pause_snap_lines"]
            spin.setValue(9)
            changes = dialog.custom_values()
            assert changes.get("pause_snap_lines") == 9, \
                f"custom_values 没报告改动：{changes}"
            # 「定制功能」的开关是空文字 Switch（名字在左侧独立 QLabel 上）：
            # 点轨道必须命中，**点行标签也要能切换** —— 不然用户看着
            # "启动时最大化 [开关]"去点文字，没反应，就以为开关坏了。
            from PySide6.QtGui import QMouseEvent as _QMouse
            from PySide6.QtWidgets import QLabel as _QLabel
            for spec in customization.specs():
                if spec["kind"] != "bool":
                    continue
                toggle = dialog._custom_widgets[spec["key"]]
                assert toggle.hitButton(QPoint(toggle.TRACK_W // 2, toggle.height() // 2)), \
                    f"{spec['key']} 点轨道中点没有命中"
                # 命中区几何回归（与第 4 节同一套断言，抽成共用函数）
                _assert_switch_hit_geometry(toggle, spec["key"])
                # 「按下即翻转」回归：按下瞬间状态必须已变（松开才变 = 手感延迟回归）。
                # ⚠️ 必须点在**轨道内**：Switch.mousePressEvent 现在会先过一遍
                # hitButton，控件矩形中心（控件比轨道宽得多）已经在命中区外了。
                was = toggle.isChecked()
                _hit = toggle._track_rect().center().toPoint()
                press_ev = _QMouse(QEvent.Type.MouseButtonPress,
                                   QPointF(_hit),
                                   QPointF(toggle.mapToGlobal(_hit)),
                                   Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                   Qt.KeyboardModifier.NoModifier)
                app.sendEvent(toggle, press_ev)
                assert toggle.isChecked() != was, \
                    f"{spec['key']} 按下瞬间没有翻转（退回松开才切换 = 延迟回归）"
                toggle.setChecked(was)
                # 点**轨道外**（控件比轨道宽得多，右侧一大片空字段区）不该翻转。
                # 2026-10-12 用户点名的"点击同一高度的位置开关就被触发"就出在
                # 这里：mousePressEvent 覆写后**绕开了 hitButton**（覆写即绕过
                # QAbstractButton 原本的命中判定），控件矩形内任意位置都翻转。
                _blank = QPoint(int(toggle._track_rect().right()) + 40,
                                toggle.height() // 2)
                if _blank.x() < toggle.width() - 2:
                    blank_ev = _QMouse(QEvent.Type.MouseButtonPress,
                                       QPointF(_blank),
                                       QPointF(toggle.mapToGlobal(_blank)),
                                       Qt.MouseButton.LeftButton,
                                       Qt.MouseButton.LeftButton,
                                       Qt.KeyboardModifier.NoModifier)
                    app.sendEvent(toggle, blank_ev)
                    assert toggle.isChecked() == was, \
                        (f"{spec['key']} 点轨道外的空白也翻转了（用户点名："
                         f"要点击拨动开关内的地方才触发）")
                # 无障碍：无文字开关必须有可读音名称与状态描述
                assert toggle.accessibleName() == spec["label"], \
                    f"{spec['key']} 开关缺少读屏名称（无障碍）"
                assert toggle.accessibleDescription() in ("已开启", "已关闭"), \
                    f"{spec['key']} 开关缺少状态描述（无障碍）"
                labels = [c for c in dialog.findChildren(_QLabel)
                          if c.text() == spec["label"]]
                assert labels, f"找不到 {spec['label']} 的行标签"
                label = labels[0]
                was = toggle.isChecked()
                # 行标签**不该**再转发（2026-10-12 用户点名："要点击拨动开关内的
                # 地方才触发"）：早先挂过转发，整行同高的文字区都成了热区。
                tap = _QMouse(QEvent.Type.MouseButtonPress,
                              QPointF(label.rect().center()),
                              QPointF(label.mapToGlobal(label.rect().center())),
                              Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier)
                app.sendEvent(label, tap)
                assert toggle.isChecked() == was, \
                    f"点 {spec['label']} 的行标签把开关切换了（不该再转发）"
        finally:
            dialog.close()

        # ---- 5) 面板结构：整条滚动区 + 日志在界面按钮下方 ----
        import app_log
        app_log.init()               # 日志节在 path() 为 None 时整节跳过，先初始化
        from PySide6.QtWidgets import QScrollArea, QLabel
        dialog = SettingsDialog("dark", 55, mw_button_specs(), [])
        try:
            dialog.show()                # 布局要在 show 后才激活，坐标才有意义
            app.processEvents()
            scrolls = dialog.findChildren(QScrollArea)
            assert len(scrolls) == 1, \
                f"设置面板应该只有一条统一滚动区，实际 {len(scrolls)} 条"
            def caption_y(text):
                labels = [lab for lab in dialog.findChildren(QLabel)
                          if lab.text() == text]
                assert labels, f"找不到「{text}」小标题"
                return labels[0].mapTo(dialog, labels[0].rect().center()).y()
            assert caption_y("日志") > caption_y("界面按钮"), \
                "日志一节应在界面按钮的下方"

            # ---- 6) 本轮的用户要求：字段对齐 / 输入框内文字上下留白一致 /
            #         滚轮不许改数字 ----
            # 6a) 所有字段共用同一个左边缘。以前「主题/毛玻璃」一张表单、每个分节
            #     再各起一张，而标签列宽是按**本表内**最宽的标签算的 —— 字段左边缘
            #     落在 127 / 136 / 153 三个不同的 x 上（真机实测），看着就是不整齐。
            def left_x(widget) -> int:
                return widget.mapTo(dialog, QPoint(0, 0)).x()

            fields = [dialog.cmb_theme, dialog.slider_glass]
            fields += list(dialog._custom_widgets.values())
            edges = {left_x(w) for w in fields}
            assert len(edges) == 1, \
                ("设置面板里的字段没有对齐到同一条竖线："
                 + "、".join(f"{type(w).__name__}={left_x(w)}" for w in fields))

            # 6a-2) 滑轨不许压住内容（用户报"滑轨与内容区域重叠干涉"）：
            #     widgetResizable 会把字段拉到视口宽，不给 body 留右内边距的话
            #     字段右缘就贴着滚动条（实测间隙 0px）。要求至少 8px 空隙。
            #     ⚠️ 用全局坐标量、且只在真机 windows 平台跑 —— offscreen 没有
            #     真实屏幕，mapToGlobal 是模拟值（量出 -495/-234 这种怪值），
            #     与全屏状态断言同一个豁免理由。
            if sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                dialog.resize(760, 900)
                dialog.layout().activate()
                for _ in range(30):
                    app.processEvents()
                scroll_area = dialog.findChildren(QScrollArea)[0]
                bar = scroll_area.verticalScrollBar()
                bar_left = bar.mapToGlobal(bar.rect().topLeft()).x()
                field_right = max(w.mapToGlobal(w.rect().topRight()).x()
                                  for w in fields)
                assert bar_left - field_right >= 8, \
                    (f"滚动条与内容之间只剩 {bar_left - field_right}px —— "
                     "滑轨压在内容上了")

            # 6b) 输入框里的文字要竖直居中（上下留白之差 ≤1.5px）。旧实现给数字框
            #     的编辑框塞了个 top=4 的"墨迹补偿"，真机量出来上 11 / 下 6 ——
            #     明显偏低，和下拉框（10.5 / 10）对不上，就是用户报的"间距不一致"。
            from PySide6.QtGui import QImage as _QImage

            def vertical_gaps(widget) -> tuple | None:
                """文字墨迹到上下边框的留白（逻辑 px）。

                只扫左侧 45% 宽：右边那 22~24px 是自绘 chevron，它的墨迹几乎占满
                整个内高，混进来量到的就不是文字了（探针第一版就踩了这个坑）。
                """
                dpr = widget.devicePixelRatioF()
                image = _QImage(int(widget.width() * dpr), int(widget.height() * dpr),
                                _QImage.Format.Format_ARGB32)
                image.setDevicePixelRatio(dpr)
                image.fill(0)
                widget.render(image)
                border = max(1, int(round(1 * dpr)))
                x_end = max(border + 3, int(image.width() * 0.45))

                def luma(c):
                    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()

                rows = []
                for y in range(border + 1, image.height() - border - 1):
                    values = [luma(image.pixelColor(x, y))
                              for x in range(border + 2, x_end)]
                    if max(values) - min(values) > 60:
                        rows.append(y)
                if not rows:
                    return None
                return ((rows[0] - border) / dpr,
                        (image.height() - border - 1 - rows[-1]) / dpr)

            boxes = [("主题下拉框", dialog.cmb_theme)]
            # ⚠️ 滑杆要排除：它内部没有文字（数值在**同行右侧的独立标签**里），
            #    vertical_gaps 量的是"控件内部墨迹"，对滑杆只会量到把手 ——
            #    把手位置随取值跑（帧率 120/24..165 时在右侧 45% 之外），
            #    于是量不到任何东西而假挂。旧的字号滑杆之所以"过"了，是把手
            #    恰好落在左侧 45% 内被当成了文字，属侥幸。
            from ui_controls import GlassSlider as _GlassSlider
            boxes += [(key, widget) for key, widget in dialog._custom_widgets.items()
                      if hasattr(widget, "value")
                      and not isinstance(widget, _GlassSlider)]
            for name, widget in boxes:
                gaps = vertical_gaps(widget)
                assert gaps is not None, f"{name} 里没量到文字墨迹（这条断言等于没测）"
                top_gap, bottom_gap = gaps
                assert abs(top_gap - bottom_gap) <= 1.5, \
                    (f"{name} 里的文字没有竖直居中：上留白 {top_gap:.1f} / "
                     f"下留白 {bottom_gap:.1f}")

            # 6c) 滚轮不许改数字、不许换下拉选项（用户点名"避免误操作修改数值"）
            from PySide6.QtGui import QWheelEvent

            def spin_wheel(widget, up: bool) -> None:
                pos = widget.rect().center()
                event = QWheelEvent(
                    QPointF(pos), QPointF(widget.mapToGlobal(pos)),
                    QPoint(0, 120 if up else -120), QPoint(0, 120 if up else -120),
                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                    Qt.ScrollPhase.NoScrollPhase, False)
                app.sendEvent(widget, event)

            spin = dialog._custom_widgets["pause_snap_lines"]
            spin.setValue(7)
            app.processEvents()
            for up in (True, False):
                spin_wheel(spin, up)
                app.processEvents()
                assert spin.value() == 7, \
                    (f"滚轮把数字框的值改了：7 -> {spin.value()}"
                     "（用户要求禁止滚轮增减数字）")
            combo = dialog.cmb_theme
            combo.setCurrentIndex(1)
            app.processEvents()
            for up in (True, False):
                spin_wheel(combo, up)
                app.processEvents()
                assert combo.currentIndex() == 1, \
                    (f"滚轮把下拉框的选中项换了：1 -> {combo.currentIndex()}"
                     "（同样属于误操作改数值，一并禁掉）")
        finally:
            dialog.close()
            app.processEvents()
    finally:
        restore_settings(settings, snapshot)
        settings.sync()


def check_config_parity_clock(app: QApplication) -> None:
    """这一轮改动的正脸：校验位写法、保留行数落位、右上角时钟、发送区一排中文。

    几处都涉及"旧配置迁移"，所以每条都要**真的新建一个窗口**去读落盘值，
    只断言常量或直接调内部方法是测不出迁移的。
    """
    import customization

    from serial_io import PARITY_CHOICES, PARITY_LETTERS

    from main_window import (
        DEFAULT_RX_BLOCKS, TX_COLLAPSED_H,
    )
    from ui_theme import DEFAULT_GLASS, LEGACY_GLASS, glass_strength

    # 1) 校验位：无 -> none。字母表也要对上，否则底部的 8N1 会拼错
    assert "none" in PARITY_CHOICES and PARITY_LETTERS["none"] == "N"
    assert "无" not in PARITY_CHOICES, "校验位里还留着「无」"

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}

    window = make_window()
    try:
        window.show()
        app.processEvents()

        # 0) 发送区**开局折叠**（用户点名："将发送区默认折叠起来"）：卡片只剩
        #    标题栏那一档高度、输入框整块藏起来，省下的高度全给接收区。
        #    下面 4)/9) 量的是**展开态**的版式，量之前先展开（顺手也验了能展开）。
        assert window._tx_collapsed, "发送区开局没有折叠"
        assert window._tx_content.isHidden(), "折叠了但输入框那块还露着"
        assert window._tx_card.maximumHeight() == TX_COLLAPSED_H, \
            (f"折叠态没把卡片高度夹住：{window._tx_card.maximumHeight()}"
             f"（应为 {TX_COLLAPSED_H}）")
        collapsed_card_h = window._tx_card.height()
        collapsed_rx_h = window._rx_card.height()
        window._toggle_send_area()
        app.processEvents()
        assert not window._tx_collapsed and not window._tx_content.isHidden(), \
            "点展开之后发送区没回来"
        # ⚠️ 展开现在是**真动画**（2026-10-17 之前 start==target 会一步跳到终态，
        #    这里改完立刻断言还能过；现在是 300ms 的连续动画，必须等它长完）。
        assert wait_for(app, lambda: window._tx_card.height() > collapsed_card_h,
                        timeout=1.5), \
            (f"展开后发送区没有变高：{collapsed_card_h} -> {window._tx_card.height()}")
        assert window._rx_card.height() < collapsed_rx_h, \
            "展开发送区没有把高度从接收区还回来"

        items = [window.cmb_parity.itemText(i) for i in range(window.cmb_parity.count())]
        assert items[:1] == ["none"], items
        assert "无" not in items, items

        # 没存过 -> 默认 none
        settings.remove("parity")
        settings.sync()
        fresh = make_window()
        try:
            assert fresh.cmb_parity.currentText() == "none", fresh.cmb_parity.currentText()
        finally:
            fresh.close()

        # 旧配置里的「无」要落到 none；别的值原样保留
        settings.setValue("parity", "无")
        settings.sync()
        migrated = make_window()
        try:
            assert migrated.cmb_parity.currentText() == "none", migrated.cmb_parity.currentText()
        finally:
            migrated.close()
        settings.setValue("parity", "偶")
        settings.sync()
        kept = make_window()
        try:
            assert kept.cmb_parity.currentText() == "偶", kept.cmb_parity.currentText()
        finally:
            kept.close()

        # 2) 「接收区保留行数」现在长在串口配置栏里，改一下就立刻生效
        #    （2026-10-12 起是和波特率同一套的可编辑下拉：预设展开选 + 可输入）
        assert window._config_card.isAncestorOf(window.cmb_rx_lines), \
            "「保留行数」不在串口配置栏里"
        assert not window._rx_card.isAncestorOf(window.cmb_rx_lines)
        assert window.cmb_rx_lines.isEditable(), "保留行数应当和波特率一样可输入"
        assert window.cmb_rx_lines.currentText() == str(DEFAULT_RX_BLOCKS), \
            window.cmb_rx_lines.currentText()
        assert f"{DEFAULT_RX_BLOCKS}" in [window.cmb_rx_lines.itemText(i)
                                          for i in range(window.cmb_rx_lines.count())], \
            "预设档位里应当有默认值 5000"
        original = window.cmb_rx_lines.currentText()
        window.cmb_rx_lines.setEditText("800")
        assert window.txt_rx.maximumBlockCount() == 800, window.txt_rx.maximumBlockCount()
        assert window._rx_blocks == 800
        window.cmb_rx_lines.setEditText(original)

        # 2b) 下拉弹层：宽度按最宽选项自适应 + 展开动画（2026-10-12 用户点名
        #     "所有的都要像波特率一样展开，动画和UI都要这种"）。编码框的弹层
        #     曾窄到把 latin-1 截成 "l...1"。
        for _pname, _pc in (("编码", window.cmb_encoding),
                            ("校验位", window.cmb_parity)):
            _pc._fit_popup_width()
            _fm = _pc.view().fontMetrics()
            _widest = max((_fm.horizontalAdvance(_pc.itemText(i))
                           for i in range(_pc.count())), default=0)
            from ui_controls import _PopupItemDelegate   # 就地导入：口径由它定义
            # 口径 = "视口放得下最宽文字 + 条目左右内边距"：这是真正的不变量
            # （旧口径写死 +59/+60，那是 QSS 还在画条目、外边距 12px 时的余量；
            # 条目改由 _PopupItemDelegate 画、margin 归零后它就不再是那个数，
            # 而且当时明显偏宽 —— 用户点名"这里宽了"）。
            _need = _widest + _PopupItemDelegate.PAD_X * 2
            assert _pc.view().minimumWidth() >= _need, \
                (f"{_pname} 的弹层宽度没按最宽选项自适应："
                 f"视图最小宽 {_pc.view().minimumWidth()} < 最宽项 {_widest:.0f}+60")
        _pc = window.cmb_stopbits
        _pc.showPopup()
        _win = _pc.view().window()
        # 同步起始态：showPopup 刚返回时应当是**收起**的（约 1/4 高、半透明），
        # 之后再逐帧长到全高。⚠️ 不能在 processEvents 之后量中间高度 —— 一轮
        # 事件循环能消化掉大半段动画（弹层每帧都是半透明重绘，单帧不便宜），
        # 机器一快断言点就飘（实测 2 次事件后已经到 73/134）。
        # ⚠️ 动画是**延后一拍**才起跑的（Qt 先定位弹层，随后才轮到我们展开，
        #    见 ui_controls._animate_popup_open 的说明）——直接取起始高会取到
        #    Qt 定完位的满高，误判成"没有动画"。所以先泵几拍等它起跑，
        #    记录整个过程中的最小高度，再等到长满。
        _h_start = _win.height()
        for _ in range(400):
            app.processEvents()
            if not _pc.view().isVisible():
                break
            _h_start = min(_h_start, _win.height())
            time.sleep(0.003)
        _h_full = max(_win.height(), _h_start)
        _pc.hidePopup()
        for _ in range(8):
            app.processEvents()
        assert _h_start <= _h_full * 0.5, \
            (f"弹层没有展开动画：起始高 {_h_start} / 完整高 {_h_full}"
             "（应从收起态长到全高）")
        assert _h_full > _h_start + 20, \
            f"弹层没有展开：完整高 {_h_full} / 起始高 {_h_start}"

        # 3) 接收区右上角的实时时钟：格式、归属、会走
        import re
        text = window.lbl_clock.text()
        # 默认**只有 时:分:秒**，年月日是可选项、默认关（用户点名）
        assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", text), \
            f"时钟默认应只有 时:分:秒：{text!r}"
        assert window.lbl_clock.parent() is window._rx_card, "时钟没挂在接收区卡片上"
        window.lbl_clock.setText("00:00:00")
        window._tick_clock()
        assert window.lbl_clock.text() != "00:00:00", "时钟没有跟着刷新"

        # 3b) 时钟的框宽必须装得下**当前这段文本** —— 以前框宽是按 "00:00:00"
        #     （8 字符）量的，一开日期就变成 19 字符、右边对齐下左边被裁掉，
        #     界面上只剩 "0 18:32:11"（用户报的排版问题）。这条按真实字宽比，
        #     旧写法必然挂。顺带验：日期开关来回拨，框宽跟着换、位置还贴着右缘。
        fm = window.lbl_clock.fontMetrics()

        def clock_fits() -> None:
            need = fm.horizontalAdvance(window.lbl_clock.text())
            assert window.lbl_clock.width() >= need, \
                (f"时钟框装不下文字，日期/时刻会被裁掉：框宽 "
                 f"{window.lbl_clock.width()}，需要 {need}（{window.lbl_clock.text()!r}）")
            right = window._rx_card.box().right()
            assert window.lbl_clock.geometry().right() <= int(right), \
                (f"时钟没贴住标题栏右缘：{window.lbl_clock.geometry().right()} > {right}")

        clock_fits()                        # 默认态：只有 时:分:秒
        short_width = window.lbl_clock.width()
        customization.set("clock_show_date", True)
        window._tick_clock()
        app.processEvents()
        clock_fits()                        # 带年月日那一档（最长的一档）
        dated_width = window.lbl_clock.width()
        assert dated_width > short_width, \
            f"拨开年月日之后时钟框没有变宽：{short_width} -> {dated_width}"
        customization.set("clock_show_date", False)
        window._tick_clock()
        app.processEvents()
        clock_fits()
        assert window.lbl_clock.width() == short_width, "拨回之后时钟框没有收窄还原"

        # 4) 发送区初始只剩一排中文：一排装得下，第二排装不下
        window.txt_tx.setPlainText("一行中文")
        app.processEvents()
        document = window.txt_tx.document()
        line = window.txt_tx.blockBoundingGeometry(document.firstBlock()).height()
        usable = window.txt_tx.viewport().height() - 2 * document.documentMargin()
        assert line <= usable + 1e-9, f"一排中文被裁掉了：行高 {line} > 可用 {usable}"
        assert usable < line * 2, f"发送区不止一排：可用 {usable}，一行 {line}"
        window.txt_tx.setPlainText("")

        # 5) 隐藏「定时发送」时，紧随其后的间隔时长要一并收起
        window._preview_buttons(["chk_timer_send"])
        app.processEvents()
        assert window.chk_timer_send.isHidden()
        assert window.spin_interval.isHidden(), "隐藏定时发送后间隔时长还留在选项行里"
        window._preview_buttons([])
        app.processEvents()
        assert not window.spin_interval.isHidden(), "恢复后间隔时长没回来"

        # 6) 「追加换行」已改名「换行」
        assert window.chk_append_crlf.text() == "换行", window.chk_append_crlf.text()

        # 7) 默认毛玻璃 0（用户："暂时不要毛玻璃了"）。
        #    历史默认值 68 / 100 是"从没选过滑杆"的痕迹，迁到当前默认；
        #    用户**明确选过**的值一律尊重 —— 哪怕他选的就是 100（旧默认值本身）。
        #    这三条要一起测：只测迁移的话，把"任何值都迁走"的写法也算过。
        assert DEFAULT_GLASS == 0, DEFAULT_GLASS

        def glass_after(stored, chosen=None) -> int:
            settings.setValue("glass", stored)
            if chosen is None:
                settings.remove("glass_chosen")
            else:
                settings.setValue("glass_chosen", chosen)
            settings.sync()
            probe = make_window()
            try:
                return glass_strength()
            finally:
                probe.close()

        for legacy in LEGACY_GLASS:
            got = glass_after(legacy)
            assert got == DEFAULT_GLASS, f"旧默认 {legacy} 没迁到新默认: {got}"
        assert glass_after(42) == 42, "用户选的中间档被迁走了"
        assert glass_after(100, chosen=True) == 100, \
            "明确选过 100 的被当成旧默认迁走了"

        # 8) 输入框里第一行中文要在框内**垂直居中**：上下留白之差 ≤1.5px。
        #    只断言 QSS 里的 padding 是没用的 —— 真正决定位置的是字体行盒，
        #    中文字形在行盒里天生偏上（改前实测 15.5 : 23.5，差 8px），必须量渲染结果。
        #    怎么量：把输入框单独 render() 成图片，逐行看"行内亮度极差" ——
        #    背景是纯色（极差≈0），有文字的行对比度极高，描边和滚动条够不到阈值。
        #    注意别用 window.grab()：实测同一次运行里它有时抓不到编辑器的内容。
        from PySide6.QtGui import QImage

        def measure_gaps() -> tuple[float, float]:
            box = window.txt_tx
            box.setPlainText("中文")
            app.processEvents()
            dpr = box.devicePixelRatioF()
            image = QImage(int(box.width() * dpr), int(box.height() * dpr),
                           QImage.Format.Format_ARGB32)
            image.setDevicePixelRatio(dpr)
            image.fill(0)
            box.render(image)
            border = int(round(1 * dpr))

            def luma(color) -> float:
                return 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()

            text_rows = []
            for y in range(border + 1, image.height() - border - 1):
                values = [luma(image.pixelColor(x, y))
                          for x in range(border + 2, image.width() - border - 2)]
                if max(values) - min(values) > 60:
                    text_rows.append(y)
            assert text_rows, "输入框里没测到文字墨迹 —— 渲染没抓到内容，这条断言等于没测"
            return ((text_rows[0] - border) / dpr,
                    (image.height() - border - 1 - text_rows[-1]) / dpr)

        top_gap, bottom_gap = measure_gaps()
        assert abs(top_gap - bottom_gap) <= 1.5, \
            f"输入框里中文没居中：上留白 {top_gap:.1f} / 下留白 {bottom_gap:.1f}"
        window.txt_tx.setPlainText("")

        # 9) 拖分割条只该拉伸输入框：选项行必须**钉在原地**，发送键则贴着输入框第一行。
        #    这条要按窗口坐标量控件的位置 —— 改前选项行会被多出来的高度撑开
        #    （「历史」下拉框被拉到 122px 高），整排按钮跟着往上跳。
        #    发送键的锚点后来改成"输入框第一行文字"（用户要求它跟文字齐平），
        #    所以它**应该**跟着输入框的顶边走；不动的只有底下那排开关。
        splitter = window._splitter
        total = sum(splitter.sizes())
        room = total - window._rx_card.minimumHeight()
        big = min(window._tx_min_height + 220, room)
        assert big >= window._tx_min_height + 60, \
            f"窗口太矮，拖不出验证需要的余量：{big} / {window._tx_min_height}"

        def control_tops() -> dict[str, int]:
            editor_top = window.txt_tx.mapTo(window, QPoint(0, 0)).y()
            return {
                "opts_row": window._tx_opts_row.mapTo(window, QPoint(0, 0)).y(),
                "editor_top": editor_top,
                "btn_center": window.btn_send.mapTo(window, QPoint(0, 0)).y()
                              + window.btn_send.height() // 2,
                "editor": window.txt_tx.height(),
            }

        splitter.setSizes([total - window._tx_min_height, window._tx_min_height])
        app.processEvents()
        before = control_tops()
        one_line = window._tx_one_line_height()
        gap = before["btn_center"] - before["editor_top"]
        assert abs(gap - one_line / 2) <= 1.5, \
            (f"发送键没对齐输入框第一行：按钮中心离输入框顶 {gap}px，"
             f"一行文字的中心应该是 {one_line / 2:.1f}px")
        splitter.setSizes([total - big, big])
        app.processEvents()
        after = control_tops()

        assert after["editor"] > before["editor"] + 60, \
            f"拖大发送区后输入框没被拉伸：{before['editor']} -> {after['editor']}"
        assert after["opts_row"] == before["opts_row"], \
            f"拖分割条把选项行带着动了：{before['opts_row']} -> {after['opts_row']}"
        assert (after["btn_center"] - after["editor_top"]
                == before["btn_center"] - before["editor_top"]), \
            (f"拖分割条后发送键和第一行脱开了：{before['btn_center']} -> "
             f"{after['btn_center']}（输入框顶 {before['editor_top']} -> {after['editor_top']}）")
    finally:
        window.close()
        restore_settings(settings, snapshot)
        settings.sync()


def check_rx_merge_and_pause(app: QApplication) -> None:
    """收包合并 / 暂停-继续 / 串口开关挂哪儿。

    三件都是"肉眼看着对、用起来不对"的类型，所以都写成断言：
    - 串口一次 read 只给一帧里的一小段。逐段显示的话数字会被拦腰截断
      （"12.382" 和 "4 13.3825" 分在两行），每段还各挂一个时间戳；
    - 暂停之后按"继续"，视图若不主动滚到底就停在老位置，新数据全堆在下面看不见
      —— 用户的原话正是"暂停没有起作用，空格键也不能控制"；
    - 电源开关要挂在页头产品名**正下方**，不在任何卡片里。
    """

    # ⚠️ 必须放在函数**最开头**：函数里任何一处 `from X import Y` 都会让 Y 变成
    # 整个函数的局部名，在导入语句之前用它就是 UnboundLocalError（这个坑踩过）。
    from ui_highlight import align_indexes, stamp_span

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    pin_rx_grid_defaults(settings)

    window = make_window()
    try:
        window.show()
        app.processEvents()
        # 发送区这一轮起**开局默认折叠**。下面要验的是"从展开态进暂停会自动把
        # 发送区收起来"，所以先展开 —— 否则暂停前后都是折叠态，那条断言就成了
        # 同义反复（永远为真，测不出任何东西）。
        assert window._tx_collapsed, "发送区开局没有折叠（这条用例的前提变了）"
        window._toggle_send_area()
        app.processEvents()
        assert not window._tx_collapsed
        window.chk_timestamp.setChecked(True)
        window._clear_receive()

        # ---- 1) 收包合并：一帧按 64 字节切开喂进去，界面上必须只留一条 ----
        frame = "".join(f"Voltage{i}:\n"
                        + " ".join(f"{j}.3820" for j in range(1, 26)) + "\n\n"
                        for i in range(1, 4)) + "Temperature1:\n1.22 2.22 3.22 4.25\n"
        payload = frame.encode("utf-8")
        assert len(payload) > 64 * 3, "测试数据太短，切不出好几段"
        # ⚠️ 中间不要 processEvents：真机上合并定时器一旦在两块之间开火，
        # 块就成了独立帧、帧边界的空行被裁掉（测试假象，不是丢数据）。
        # 一口气喂完，再等合并缓冲吐出。
        for start in range(0, len(payload), 64):
            window._on_received(payload[start:start + 64])

        assert wait_for(app, lambda: not window._rx_pending), "数据一直卡在合并缓冲里没吐出来"
        app.processEvents()
        text = window.txt_rx.toPlainText()
        assert text.count("[") == 1, \
            f"一帧被切成了好几段，每段挂一个时间戳：\n{text[:400]}"
        # 界面显示 = 时间戳行 + 合并后的帧；去掉时间戳行后应与原始帧一致。
        # ⚠️ 显示出来的文本比设备发来的多了一层**序号补齐**（"1.3820" -> " 1.3820"，
        # 见 ui_highlight.align_indexes），所以不能直接逐字比 —— 跟补齐后的原文比。
        shown = "".join(line + "\n" for line in text.split("\n")
                        if not line.startswith("["))
        # 显示和纯函数要用**同一个**视口封顶折行，否则逐字比必然假挂
        assert shown.rstrip("\n") == align_indexes(
            frame, max_row_chars=window._rx_max_row_chars()).rstrip("\n"), \
            "合并之后的内容和原始数据不一致（拼接处被吃掉了字节）"
        # 再钉一遍"值一个字符都没被动过"：补齐**只在序号前面插 0**，把这些前导 0
        # 摘掉之后必须和原文对得上。重排会动空白和折行位置，所以按**全空白压平
        # 后的 token 序列**比，不逐行比。
        import re as _re
        assert " ".join(_depad(shown).split()) == " ".join(frame.split()), \
            "补齐序号时动了序号以外的字符：内容对不上原文"

        # ---- 1b) 序号补齐本身：一位数前面补 0，小数点要竖着对齐 ----
        # 用户点名："一位数的数值在前方补零以对齐占位，并让序号以'.'对齐。"
        # 序号补零**只发生在电压段**（2026-10-11 用户点名温度段、BMS 之后的
        # 明细行都不补），所以裸数据要放进电压段的上下文里测。
        naive = " ".join(f"{j}.3820" for j in range(1, 26))
        padded_row = align_indexes("Voltage1:\n" + naive).split("\n")[1]
        dots = [i for i, ch in enumerate(padded_row) if ch == "."]
        assert len(dots) == 25, f"补齐之后小数点个数不对：{padded_row[:80]!r}"
        assert len({b - a for a, b in zip(dots, dots[1:])}) == 1, \
            f"补齐之后小数点还是没有对齐（间距不等）：{padded_row[:80]!r}"
        assert padded_row.startswith("01.3820   02.3820   03.3820"), padded_row[:40]
        assert "010.3820" not in padded_row, "两位数序号被多补了一个 0"
        # 补的是数字 0（用户："在前方补零"），不是空格
        assert " 1.3820" not in padded_row and " 2.3820" not in padded_row, \
            f"补的还是空格，不是 0：{padded_row[:40]!r}"
        assert _re.sub(r"(?<!\S)0+(?=\d)", "", padded_row).replace(" ", "") == \
            naive.replace(" ", ""), "补齐动了序号和值以外的字符"
        # 序号 0 不补占位（用户点名："序号为 0 时，序号前面不应再保留一个空格，
        # 请去掉该多余空格"）：补成 "00" 是在 0 号点前多出一个占位字符。
        # （电压段数据行走等宽网格，单元格补空格对齐，按 token 比较不受列距影响）
        assert align_indexes("Voltage1:\n0.3820 1.3820").split("\n")[1].split() == \
            ["0.3820", "01.3820"], \
            f"序号 0 不应补占位：{align_indexes('Voltage1:\n0.3820 1.3820')!r}"
        assert align_indexes("Voltage1:\n0.38").split("\n")[1] == "0.38", \
            "单独一个 0 号点也不该被补"
        # 三位序号：补位宽度要跟着帧内**最大序号**走，否则 "100." 会多占一格，
        # 小数点不在一条竖线上（用户："用序号的个位数字上下对齐"）。
        wide = " ".join(f"{j}.3820" for j in (1, 9, 10, 99, 100, 120))
        wide_row = align_indexes("Voltage1:\n" + wide).split("\n")[1]
        wide_dots = [i for i, ch in enumerate(wide_row) if ch == "."]
        assert len({b - a for a, b in zip(wide_dots, wide_dots[1:])}) == 1, \
            f"三位序号时小数点没对齐：{wide_row[:70]!r}"
        assert wide_row.startswith("001.3820"), f"补位宽度没按最大序号走：{wide_row[:20]!r}"
        # BMS ERROR 之后的错误明细行：序号保持设备原样（2026-10-11 用户点名
        # "BMSERROR后面的序号只有一位数，就不需要在前面加一个0"）
        bms_aligned = align_indexes(
            "BMS ERROR\n1.1 Over Voltage\n1.10 Over Voltage")
        assert bms_aligned.split("\n")[1:] == \
            ["1.1 Over Voltage", "1.10 Over Voltage"], bms_aligned
        # 只认"序号.值"，别的数字一个都不许补
        for plain in ("I=-250.00 V=0.00", "CWMax=3831,  Min=0", "Voltage1:"):
            assert align_indexes(plain) == plain, f"{plain!r} 被误补了"

        # ---- 1c) 带段标题的整帧：**值**也补零（用户点名"将单位数数值前方补零
        #     以对齐占位宽度"）—— 宽度按段内最宽的值算；BMS 之后的散值不动。
        frame_sample = ("Voltage1:\n1.3700 2.3820\n"
                        "Temperature1:\n1.5 2.22 10.3\n"
                        "BMS ERROR\n1.99")
        aligned_frame = align_indexes(frame_sample)
        assert "01.3700   02.3820" in aligned_frame, aligned_frame   # 电压值全 4 位不变；列距=3 空格
        # 温度序号**不补零**（用户 2026-10-11 点名"温度区域序号不用加0"），值照补；
        # 单元格补空格到等宽（"10.03" 5 字符）后 3 空格拼接，间隙 = 3 + 补白
        assert "1.05    2.22    10.03" in aligned_frame, aligned_frame
        assert "\n1.99" in aligned_frame, aligned_frame  # BMS 之后的散值：序号不加 0、值不动
        assert _depad(aligned_frame) == frame_sample, \
            "值补零动了前导 0 以外的字符"
        # 无段标题（对齐函数不知道单位语义）时序号、值都不动
        assert align_indexes("1.5 2.22") == "1.5 2.22", \
            "无段标题时不应盲补"

        # ---- 1d) 网格重排（用户 2026-10-11 点名"没有对齐/不该换行的地方
        #     不要换行"）：设备两行数据的间隔宽窄不一（补零后列起点漂移的
        #     根源），重排后各行小数点间距必须全等；数字序列一个都不能动。
        volt_frame = ("Voltage1:\n"
                      + " ".join(f"{j}.6553" for j in range(1, 13)) + "\n"
                      + "  ".join(f"{j}.6553" for j in range(13, 26)))
        grid_lines = align_indexes(volt_frame).split("\n")[1:]
        pitches = set()
        for gl in grid_lines:
            dots = [k for k, ch in enumerate(gl) if ch == "."]
            pitches |= {b - a for a, b in zip(dots, dots[1:])}
        assert len(pitches) == 1, f"重排后各行小数点间距不一致：{grid_lines!r}"
        # 重排会按统一列数折行（设备两行的 12/13 个会重排成 13/12），
        # 所以数字不变这条按**全空白压平后的 token 序列**比，不逐行比。
        assert " ".join(_depad("\n".join(grid_lines)).split()) == \
            " ".join(volt_frame.split("\n", 1)[1].split()), \
            "网格重排动了数字本身"

        # 温度区孤行（分包把 " 4.87" 劈成单独一行）要并回 4 列网格；
        # 温度序号保持设备原样（不补 0）
        temp_aligned = align_indexes(
            "Temperature1:\n1.87 2.87 3.87\n 4.87\n5.87 6.87 7.87 8.87")
        assert temp_aligned.split("\n")[1:] == \
            ["1.87   2.87   3.87   4.87", "5.87   6.87   7.87   8.87"], \
            temp_aligned

        # ---- 1e) 折行按视口封顶（用户 2026-10-11 截图：13 个一行的网格在只
        #     装得下 12 个的视口里软换行，行尾甩出孤格"13.6553"）。封顶之后
        #     每行都不得超出 max_row_chars，列距还得保持全等。
        capped = align_indexes(
            "Voltage1:\n" + " ".join(f"{j}.6553" for j in range(1, 26)),
            max_row_chars=50)
        capped_lines = capped.split("\n")[1:]
        assert capped_lines and all(len(gl) <= 50 for gl in capped_lines), \
            f"封顶后行宽仍超出 max_row_chars：{capped_lines!r}"
        cap_pitches = set()
        for gl in capped_lines:
            dots = [k for k, ch in enumerate(gl) if ch == "."]
            cap_pitches |= {b - a for a, b in zip(dots, dots[1:])}
        assert len(cap_pitches) == 1, f"封顶折行后列距不一致：{capped_lines!r}"

        # ---- 1f) 半行缓发（用户 2026-10-11 截图：分包把一行劈成两帧，出现
        #     "Over Vol"/"tage" 这种莫名其妙的换行）。没以换行收尾的半行要先
        #     攒住，等下一批接上再整行排版显示。
        window._clear_receive()
        window._on_received(
            "Voltage1:\n1.3820 2.3820 3.3820 4.3820 5.3820 Over Vol".encode("utf-8"))
        window._flush_rx()
        app.processEvents()
        assert window.txt_rx.toPlainText() == "", \
            f"半行没攒住，提前显示了：{window.txt_rx.toPlainText()!r}"
        window._on_received("tage\n6.3820\n".encode("utf-8"))
        window._flush_rx()
        app.processEvents()
        shown_join = window.txt_rx.toPlainText()
        assert "Over Voltage" in shown_join, f"劈开的一行没有被接回来：{shown_join!r}"
        assert "01.3820" in shown_join and "06.3820" in shown_join, shown_join

        # ---- 2) 暂停 = 冻结显示：画面一个像素都不动，新数据进后台队列；----
        #         继续 = 一次性刷进来并追底（恢复到最新读取的数据位置）
        # 暂停瞬间还要**自动定位**：视口顶与当前轮的时间戳之间恰好隔 7 行
        # （第 8 行就是时间戳）。先喂几轮带轮首标记的数据，把视口滚到中段，
        # 保证"当前轮"的时间戳在视口上方够远处，定位才有得挪。
        window._on_rx_lines_changed(300)
        window._clear_receive()
        # 一轮按**真实布局**给：电压 → Temperature1: → BMS。轮首判定依据是
        # "段状态绕一圈回到电压段"（见 main_window._stamp_needed）；只喂电压帧
        # 的话 8 帧全落在电压段里、会被当成同一轮，只出一个时间戳，定位无从测起。
        for r in range(8):
            window._emit_block("Voltage1:\n"
                               + " ".join(f"{j}.38{r}{j % 10}" for j in range(1, 41)))
            window._emit_block("Temperature1:\n"
                               + " ".join(f"{j}.2{r % 10}" for j in range(1, 11)))
            window._emit_block("BMS OK")
        app.processEvents()
        bar = window.txt_rx.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        app.processEvents()
        expanded_before_pause = not window._tx_collapsed
        window._toggle_pause()
        # 暂停时发送区自动折叠（接收区多吃一截高度），且**先折叠后定位**
        assert window._tx_collapsed, "暂停时发送区没有折叠"
        # 显示时间戳/HEX/编码那排工具栏也一并折叠（用户点名）。折叠与定位现在
        # 都是补间（工具栏 240ms 收高、定位 200ms 滚动，见 _animate_opts_row /
        # _snap_to_round_start），量之前先等它们落定。
        # ⚠️ 收起后行高归零但**不 hide**：hide 会把布局间距（10px）一起收走，
        #    落定那一帧接收区视口又要跳 10px（44px 硬跳改动画后只剩这条尾巴）。
        assert wait_for(app, lambda: window._rx_opts_row.height() <= 1, timeout=1.0), \
            f"暂停时工具栏（显示时间戳那排）没有收起来：高 {window._rx_opts_row.height()}"
        assert wait_for(app, lambda: not (window._rx_scroll_tween
                                          and window._rx_scroll_tween.running),
                        timeout=1.0), "暂停定位的滚动补间一直没停下"
        doc = window.txt_rx.document()
        from PySide6.QtGui import QTextCursor
        # 定位后**应有某个时间戳行**落在视口顶下方 7 个视觉行处（第 8 行 =
        # 时间戳）。⚠️ 不能取"视口顶往下第一个时间戳"来量：7 行上方恰好压着
        # 上一轮的时间戳时（数据行折行 + 轮只有三两块时很容易），那个 y=0 的
        # 会被误当成目标（真机实测踩过）。用 cursorRect 量（视觉行语义），
        # 行高用时间戳行自身的高度（单行块）。
        stamps = [b for b in range(doc.blockCount())
                  if stamp_span(doc.findBlockByNumber(b).text()) is not None]
        assert stamps, "数据里没有时间戳行，定位无从谈起"
        line_h = window.txt_rx.blockBoundingGeometry(
            doc.findBlockByNumber(stamps[0])).height()
        target = 7 * line_h
        best = None
        for b in stamps:
            c = QTextCursor(doc)
            c.setPosition(doc.findBlockByNumber(b).position())
            y = window.txt_rx.cursorRect(c).top()
            if best is None or abs(y - target) < abs(best - target):
                best = y
        assert abs(best - target) <= line_h / 2, \
            (f"暂停定位不准：没有一个时间戳落在视口顶下方 7 行（{target:.0f}px）处，"
             f"最近的是 {best:.1f}px（行高 {line_h:.0f}）")

        # 激活态必须在按钮上看得出（字形染色）—— 用户反馈过"按了没反应"，
        # 两竖换三角太小根本注意不到
        assert window.btn_pause._active, "暂停时按钮没有进入激活态"
        for i in range(80):
            window._emit_block(f"line {i} - filler")
        app.processEvents()
        pinned = bar.value()
        frozen_text = window.txt_rx.toPlainText()
        window._emit_block("PIN-1")
        window._emit_block("PIN-2")
        app.processEvents()
        assert bar.value() == pinned, \
            f"暂停期间视图还在动：{pinned} -> {bar.value()}"
        assert window.txt_rx.toPlainText() == frozen_text, \
            "暂停期间画面居然变了（用户要的是冻结在当前数据页面）"
        assert window._paused_frames, "暂停期间新数据没有进后台队列（会丢）"
        assert not any("PIN" in display for _t, display in window._paused_frames) or \
            all("PIN" in display for _t, display in window._paused_frames[-2:]), \
            "后台队列里混进了别的东西"

        window._toggle_pause()
        assert not window._paused and not window.btn_pause._active, \
            "继续之后按钮没有退出激活态"
        # 暂停时折叠的发送区在恢复时还原（暂停前就折叠的则不动）
        assert not window._tx_collapsed if expanded_before_pause else True, \
            "继续之后发送区没有展开回来"
        app.processEvents()
        # 攒下的帧必须全部出现
        shown = window.txt_rx.toPlainText()
        assert "PIN-1" in shown and "PIN-2" in shown, \
            "恢复后暂停期间的数据没有刷进来"
        assert not window._paused_frames, "恢复后队列没清空"
        # ⚠️ 追底是**动画**（180ms 的滚动补间，见 _rx_scroll_tween），而且恢复时
        #    发送区可能还在展开（视口高度在变）—— 等它追上，别用一次性断言。
        assert wait_for(app, lambda: bar.value() >= bar.maximum() - 1,
                        timeout=2.0), \
            (f"按了继续却没有追上最新数据：{bar.value()} / {bar.maximum()}"
             "（视图停在暂停那一刻，新数据堆在下面看不见）")

        # ---- 2b) 保留行数小 → 恢复时批量刷帧会触发裁剪：不能崩、不能漂 ----
        window._on_rx_lines_changed(30)       # 保留行数压到 30 行，马上进入裁剪区间
        window._clear_receive()
        for i in range(60):
            window._emit_block(f"bulk {i:03d} " + "q" * 50)
        app.processEvents()
        window._toggle_pause()
        frozen_text = window.txt_rx.toPlainText()
        for i in range(40):
            window._emit_block(f"drift {i:03d} " + "z" * 50)
            app.processEvents()
        assert window.txt_rx.toPlainText() == frozen_text, \
            "暂停期间画面变了（冻结显示下这不可能发生，除非队列漏进了显示）"
        window._toggle_pause()
        app.processEvents()
        shown = window.txt_rx.toPlainText()
        assert "drift 039" in shown, "恢复后最后一批数据没刷进来"
        # 追底是补间（180ms）+ 340ms 后再补一次，等它落到最新底部
        assert wait_for(app, lambda: bar.value() >= bar.maximum() - 1, timeout=2.0), \
            f"恢复后没有追底：{bar.value()} / {bar.maximum()}"
        window._on_rx_lines_changed(window._rx_blocks)   # 还原保留行数
        window._clear_receive()

        # ---- 3) 串口开关在「串口配置」卡片**里面**，旁边有文字标注 ----
        button = window.btn_open
        header = window.brand_header
        assert button.parent() is not header, "开关还挂在页头上，没搬进配置卡片"
        # 沿父链向上找卡片：必须坐在 _config_card 内部（含标题栏/内容都算）
        ancestor = button.parentWidget()
        while ancestor is not None and ancestor is not window._config_card:
            ancestor = ancestor.parentWidget()
        assert ancestor is window._config_card, \
            (f"电源开关不在「串口配置」卡片里，父链是 "
             f"{[type(a).__name__ for a in [button.parentWidget()]]}")
        # 开关带文字标注「串口开关」（QCheckBox 的 text，不是 QLabel）
        assert window.btn_open.text() == "串口开关", \
            f"开关旁没有「串口开关」文字：{window.btn_open.text()!r}"
        # 页头**不能**设 WA_TransparentForMouseEvents —— 按 Qt 的定义，那个属性
        # 连子控件的鼠标事件一起掐。开关虽然搬走了，这条规矩要留防回归。
        ancestor = button.parentWidget()
        while ancestor is not None:
            assert not ancestor.testAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents), \
                f"{type(ancestor).__name__} 设了鼠标穿透，它下面的按钮点不动"
            ancestor = ancestor.parentWidget()
    finally:
        window.close()
        restore_settings(settings, snapshot)
        settings.sync()


def check_send_enter_and_stamp(app: QApplication) -> None:
    """回车发送 / 时间戳只打轮首且只到秒 / 时间戳与序号染蓝。

    三件都是用户点名要的：
    - "发送键需要能够按 enter 键发送"；
    - "时间戳只放在 Voltage1 前面，精确到秒就可以了"；
    - "时间戳标成蓝色。那些序号标成蓝色"。
    """
    import main_window as mw
    from PySide6.QtGui import QKeyEvent, QTextCursor

    from ui_highlight import index_spans, stamp_span
    from ui_theme import rx_accent

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    pin_rx_grid_defaults(settings)
    real_ports = mw.list_serial_ports
    fake: list[tuple[str, str]] = [("loop://", "回环端口（假）")]
    mw.list_serial_ports = lambda: list(fake)
    window = make_window()
    try:
        window.show()
        app.processEvents()
        window._refresh_ports(announce=False)
        window._open_port()
        assert wait_for(app, lambda: window._port_connected), \
            "loop:// 没连上，后面的收发用例没法跑"

        def press(key, modifiers=Qt.KeyboardModifier.NoModifier, repeat=False) -> None:
            """往发送框发一次按键。走 sendEvent —— 应用级事件过滤器就在这条路上，
            等价于用户真的敲了一下键盘。"""
            app.sendEvent(window.txt_tx,
                          QKeyEvent(QKeyEvent.Type.KeyPress, key, modifiers, "", repeat))

        # ---- 1) 回车 = 发送 ----
        window._clear_receive()
        window.txt_tx.setPlainText("ping")
        before = window._tx_bytes
        press(Qt.Key.Key_Return)
        assert wait_for(app, lambda: "ping" in window.txt_rx.toPlainText()), \
            "在发送区按回车没有把内容发出去"
        assert window._tx_bytes > before, "回车没有真的写串口"

        # 按住不放不重复发（auto-repeat 要吃掉，否则手一压就是一串包），
        # 而且不能被当成换行插进编辑框
        window.txt_tx.setPlainText("spam")
        before = window._tx_bytes
        press(Qt.Key.Key_Return, repeat=True)
        assert window._tx_bytes == before, "按住回车连着发了一串包"
        assert window.txt_tx.toPlainText() == "spam", \
            f"auto-repeat 的回车被当换行插进去了：{window.txt_tx.toPlainText()!r}"

        # Shift+回车仍然是换行（要发多行还有路走）。
        # setPlainText 会把光标放回文首，真实用户是在**末尾**敲键盘的，先挪过去，
        # 否则换行插到文首变成 "\nab"，和断言的 "ab\n" 差一个位置。
        window.txt_tx.setPlainText("ab")
        cursor = window.txt_tx.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        window.txt_tx.setTextCursor(cursor)
        before = window._tx_bytes
        press(Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        assert window._tx_bytes == before, "Shift+回车不该发送"
        assert window.txt_tx.toPlainText() == "ab\n", window.txt_tx.toPlainText()

        # 空输入框 + 串口没开：回车什么都不做，不该白弹一个警告框
        window._close_port()
        window.txt_tx.setPlainText("")
        warned: list[str] = []
        window._warn = warned.append
        press(Qt.Key.Key_Return)
        assert not warned, f"空输入框按回车弹了提示：{warned!r}"
        window._open_port()
        assert wait_for(app, lambda: window._port_connected), "重新打开 loop:// 没连上"

        # ---- 2) 时间戳：只打轮首、只到秒 ----
        window.chk_timestamp.setChecked(True)
        window._clear_receive()
        blocks = [
            "Voltage1:\n" + " ".join(f"{j}.3820" for j in range(1, 26)) + "\n\n",
            "Voltage2:\n" + " ".join(f"{j}.3821" for j in range(1, 26)) + "\n\n",
            "Temperature1:\n 1.22   2.22   3.22   4.25\n",
        ]
        for block in blocks:
            window._on_received(block.encode("utf-8"))
            window._flush_rx()          # 每次强制成帧 = 一轮被切成了三段
            app.processEvents()

        lines = window.txt_rx.toPlainText().split("\n")
        stamp_rows = [i for i, line in enumerate(lines) if line.startswith("[")]
        assert len(stamp_rows) == 1, \
            f"时间戳打多了，每轮该只有一个：{[lines[i][:24] for i in stamp_rows]}"
        row = stamp_rows[0]
        stamp_text = lines[row]
        # 默认三段全开：[YYYY-MM-DD HH:MM:SS]；毫秒不该出现（精确到秒）
        import re as _re
        assert _re.fullmatch(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", stamp_text), \
            f"时间戳不是默认的带日期秒级格式：{stamp_text}"
        # 时间戳独占一行、本轮首行（Voltage1:）紧贴在它下面；第一帧顶上不留白
        assert lines[row + 1] == "Voltage1:", \
            f"时间戳下面要紧贴本轮首行：{lines[row:row + 2]!r}"
        assert row == 0, f"第一帧顶上不该有空行：{lines[:2]!r}"

        # 下一轮（再喂一个 Voltage1 帧）：时间戳重新出现一轮一个，且**排版
        # 紧凑** —— 上一轮最后一行和时间戳行之间不留空行（用户点名"无空位"）
        window._on_received("Voltage1:\n 1.3800\n".encode("utf-8"))
        window._flush_rx()
        app.processEvents()
        lines = window.txt_rx.toPlainText().split("\n")
        stamp_rows = [i for i, line in enumerate(lines) if line.startswith("[")]
        assert len(stamp_rows) == 2, \
            f"新一轮该有自己的时间戳：{[lines[i][:24] for i in stamp_rows]}"
        assert lines[stamp_rows[1] + 1] == "Voltage1:", \
            f"时间戳下面要紧贴本轮首行：{lines[stamp_rows[1]:stamp_rows[1] + 2]!r}"
        assert lines[stamp_rows[1] - 1] == "", \
            (f"时间戳与上方内容之间要空一行："
             f"{lines[stamp_rows[1] - 2:stamp_rows[1] + 1]!r}")

        # 空帧（设备只发换行）不该造出空行
        before = window.txt_rx.document().blockCount()
        window._on_received("\r\n".encode("utf-8"))
        window._flush_rx()
        app.processEvents()
        assert window.txt_rx.document().blockCount() == before, \
            "空帧在接收区里占了位置（造出了空行）"

        # 备用规则：整条流里从来没有轮首标记时，每帧都要有时间戳 ——
        # 否则换台设备（数据里根本没有 Voltage1）时间戳会静默消失
        window._clear_receive()
        for index in range(3):
            window._emit_block(f"plain line {index}")
        assert window.txt_rx.toPlainText().count("[") == 3, \
            "没有轮首标记的数据也应该每帧都打时间戳"

        # ---- 3) 着色：规则本身（时间戳紫、序号蓝，区间分开断言）----
        assert stamp_span("[21:47:14] Voltage1:") == (0, 10)
        assert stamp_span(" 1.3820   2.3824") is None, "时间戳只认块首"
        assert index_spans(" 1.3820   2.3824   3.3827  10.3825") == \
            [(1, 1), (10, 1), (19, 1), (27, 2)]
        assert index_spans("13.3821  25.3815") == [(0, 2), (9, 2)]
        assert index_spans("[21:47:14] Voltage1:") == [], \
            "行首的 Voltage1 不是序号"
        # 这些都不是序号，一个字符都不该染
        for plain in ("Voltage2:", "Temperature1:", "CWMax=3831,  Min=0",
                      "V=0.00, I=-250.00", "AIR+=0, AIR-=0, PRE=0", "COM0=0"):
            assert index_spans(plain) == [], f"{plain!r} 被误染了"

        # 电压分级：值取小数点后的整数（mV），超上限红、低于下限绿
        from ui_highlight import (
            bms_error_spans, temp_spans as temp_error_spans, value_spans,
        )
        high_spans, low_spans = value_spans(" 1.3700   2.3820   3.3900", 3750, 3800)
        # 区间是**整条**（序号 + 小数点 + 值）—— 用户 2026-10-12 点名"高亮要
        # 包括序号"：只染值的话看不出是第几个点出的问题。
        assert high_spans == [(10, 6), (19, 6)], high_spans    # 3820/3900 超上限
        assert low_spans == [(1, 6)], low_spans                # 3700 低于下限
        # ⚠️ 电压红/绿只认 4 位小数（mV 格式，VOLT_DECIMALS）—— 温度值
        # （"1.22"，2 位小数）混进电压段状态也不按 mV 阈值误判，否则温度
        # 采集区会整片被标绿（用户报的"标绿的区间不能到温度采集部分去"）。
        high_t, low_t = value_spans(" 1.22   2.22", 3750, 3800)
        assert high_t == [] and low_t == [], (high_t, low_t)
        assert bms_error_spans("xx BMS ERROR yy") == [(3, 9)]
        assert bms_error_spans("BMS OK") == []

        # ---- 4) 着色真的挂到了接收区上（不是只写了个函数没人用）----
        window._clear_receive()
        window._on_received("Voltage1:\n 1.3820   2.3824   3.3827\n".encode("utf-8"))
        window._flush_rx()
        app.processEvents()

        def painted_spans(number: int) -> list:
            block = window.txt_rx.document().findBlockByNumber(number)
            assert block.isValid(), f"接收区没有第 {number} 块"
            layout = block.layout()
            assert layout is not None, f"接收区第 {number} 块没有排版（拿不到着色信息）"
            return [(r.start, r.length, r.format.foreground().color())
                    for r in layout.formats()]

        head = painted_spans(0)      # "[21:47:14]" 时间戳行
        label = painted_spans(1)     # "Voltage1:" 轮首行
        data = painted_spans(2)      # " 1.3820   2.3824   3.3827" 那一行
        assert head and not label, \
            f"时间戳独占一行（不该染到轮首行上）：{head} / {label}"
        # 时间戳染色覆盖整行（[YYYY-MM-DD HH:MM:SS] 现在是 21 字符，
        # 以前不带日期是 10 —— 长度从块文本现算，格式再变也不挂）
        stamp_len = len(window.txt_rx.document().findBlockByNumber(0).text())
        assert stamp_len == 21, f"默认时间戳应是带日期的 21 字符：{stamp_len}"
        assert (0, stamp_len) in [(s, n) for s, n, _ in head], \
            f"时间戳没有被染上：{head}"
        # ⚠️ 别写死 (1, 1)：序号现在会被**补齐到固定宽度**（"1.3820" -> " 1.3820"，
        # 见 ui_highlight.align_indexes），偏移整体右移。起点一律从块文本现算 ——
        # 这样以后补齐位数改了、分隔符变了，这条断言也不会假挂。
        data_text = window.txt_rx.document().findBlockByNumber(2).text()
        index_offsets = {start for start, _len in index_spans(data_text)}
        assert index_offsets, f"这一行里没认出序号：{data_text!r}"
        assert {s for s, _n, _c in data if s in index_offsets} == index_offsets, \
            f"序号没有被染上：{data}（按文本算出的序号位置 {sorted(index_offsets)}）"
        # 颜色分家：时间戳紫、序号蓝 —— 两支都是主题里定好的，不能串。
        # ⚠️ data 那一行现在是**两类** span 混排：序号（蓝）+ 小数点后的值
        #    （电压分级：超上限红 / 低于下限绿 / 正常不上色）。所以不能再
        #    "整行全是蓝"地断言 —— 那会在用户把电压上限配到 3810 mV（本机真实
        #    配置）时把分级色的 span 一起算进来而误挂。按 span 起点挑出**序号**
        #    那几个再比色，才是在测"序号蓝"这件事本身。
        index_colors = [(n, c.name()) for s, n, c in data if s in index_offsets]
        assert len(index_colors) == len(index_offsets), \
            f"序号没有被全部染上：{data}"
        assert all(name == rx_accent().name() for _n, name in index_colors), \
            f"序号染的不是主题那支蓝：{index_colors}（整行 span：{[(n, c.name()) for _, n, c in data]}）"

        # ---- 5) 电压分级 + BMS ERROR：范围来自定制配置，只作用于 Voltage 段 ----
        import customization
        from ui_theme import qcolor
        customization.set("volt_limit_low", 3750)
        customization.set("volt_limit_high", 3800)
        window._rx_highlighter.refresh()
        window._clear_receive()
        window._on_received(("Voltage1:\n1.3700   2.3820   3.3900\n"
                             "Temperature1:\n1.22   2.95   3.45\n"
                             "BMS ERROR\n1.1 Over Voltage\n").encode("utf-8"))
        window._flush_rx()
        app.processEvents()
        doc5 = window.txt_rx.document()
        texts = [doc5.findBlockByNumber(b).text() for b in range(doc5.blockCount())]
        # ⚠️ 行定位按**值**找，不要按行首字面量比：序号会被补零到固定宽度
        # （"1.3700" -> "01.3700"，见 ui_highlight.align_indexes），写死行首必然假挂。
        data_row = next(i for i, t in enumerate(texts) if ".3700" in t)
        temp_row = next(i for i, t in enumerate(texts) if ".22" in t)
        bms_row = next(i for i, t in enumerate(texts) if "BMS ERROR" in t)
        red = qcolor("volt_high").name()
        green = qcolor("volt_low").name()
        yellow = qcolor("bms_error").name()
        # 电压数据行：3700 绿、3820/3900 红，序号保持蓝。
        # 期望的**位置**从块文本现算（value_spans / index_spans），不写死数字 ——
        # 这条测的是"哪些值被染成了什么色"，不是"它们在第几列"。
        row_text = texts[data_row]
        row_spans = painted_spans(data_row)
        by_color: dict[str, list] = {}
        for s, n, c in row_spans:
            by_color.setdefault(c.name(), []).append((s, n))
        want_high, want_low = value_spans(row_text, 3750, 3800)
        assert want_high and want_low, f"（前提）这一行本该有超限和欠压的点：{row_text!r}"
        assert by_color.get(red) == want_high, \
            f"超上限的条目没有整条标红（含序号）：{row_spans}（应染 {want_high}）"
        assert by_color.get(green) == want_low, \
            f"低于下限的条目没有整条标绿（含序号）：{row_spans}（应染 {want_low}）"
        # 序号：**没越界**的那些条目仍是蓝的；越界条目的序号已被异常色整条覆盖
        # （用户点名"包括序号"、"不要高亮到其他没有问题的数据"）。序号 span 与
        # 条目 span 起点相同、长度不同，所以按起点挑出来比。
        error_starts = {s for s, _ in list(want_high) + list(want_low)}
        normal_index = [sp for sp in index_spans(row_text)
                        if sp[0] not in error_starts]
        # 这一行三个点**全都**越界（3700 欠压、3820/3900 超限），所以正常序号
        # 本该一个都不剩 —— 这条反过来正好测"越界条目的序号确实被异常色吃掉了"
        # （实现退回"只染值"时，三个序号会留在蓝色里，断言立刻挂）。
        assert by_color.get(rx_accent().name(), []) == normal_index, \
            (f"正常条目的序号蓝被挤掉了/越界条目的序号没被覆盖：{row_spans}"
             f"（应剩 {normal_index}，越界起点 {sorted(error_starts)}）")
        # ---- 5b) 温度越界一律标黄（用户："凡超出该范围的数值均以黄色标注"）----
        # 温度**不**分偏高/偏低两种颜色 —— 越界就是越界，都是黄。
        # （"1.22" 的 22 是 ℃，与电压那边一样取小数点后的整数部分。）
        temp_row_text = texts[temp_row]
        temp_low = int(customization.get("temp_limit_low"))
        temp_high = int(customization.get("temp_limit_high"))
        want_temp = temp_error_spans(temp_row_text, temp_low, temp_high)
        assert want_temp, f"（前提）这一行本该有越界的温度：{temp_row_text!r}"
        temp_yellow = qcolor("temp_warn").name()
        temp_by_color: dict[str, list] = {}
        for s, n, c in painted_spans(temp_row):
            temp_by_color.setdefault(c.name(), []).append((s, n))
        assert temp_by_color.get(temp_yellow) == want_temp, \
            f"温度越界的条目没有整条标黄（含序号）：{painted_spans(temp_row)}（应染 {want_temp}）"
        temp_error_starts = {s for s, _ in want_temp}
        temp_normal_index = [sp for sp in index_spans(temp_row_text)
                             if sp[0] not in temp_error_starts]
        assert temp_normal_index, \
            f"（前提）温度段本该还有正常的采样点：{temp_row_text!r}"
        assert temp_by_color.get(rx_accent().name()) == temp_normal_index, \
            f"温度段正常条目的序号蓝被挤掉了：{painted_spans(temp_row)}"
        # BMS 是**橙**、温度越界是**黄** —— 用户点名分开（"BMS ERROR 的显示颜色
        # 从黄色改为橙色"）。两支 token 各自独立，改一支不许带走另一支。
        assert temp_yellow != yellow, \
            "温度黄与 BMS 橙不该是同一支颜色（用户要求 BMS 改橙）"
        assert qcolor("bms_error").name() != qcolor("temp_warn").name()
        # 温度范围改了立刻生效（高亮器重读阈值）
        customization.set("temp_limit_high", 99)
        window._rx_highlighter.refresh()
        app.processEvents()
        assert not [c for _, _, c in painted_spans(temp_row)
                    if c.name() == temp_yellow], \
            "把温度上限改到 99 之后，原来的越界值还在标黄"
        customization.set("temp_limit_high", 60)
        window._rx_highlighter.refresh()
        app.processEvents()
        assert [c for _, _, c in painted_spans(temp_row) if c.name() == temp_yellow], \
            "温度上限改回 60 之后越界值没有被重新标黄"
        # BMS ERROR 标黄
        assert (texts[bms_row].index("BMS ERROR"), 9) in \
            [(s, n) for s, n, c in painted_spans(bms_row) if c.name() == yellow], \
            f"BMS ERROR 没有标黄：{painted_spans(bms_row)}"
        # ---- 5d) BMS 错误明细行整行标橙（2026-10-11 用户点名"BMS Error
        #     并没有被标橙色"）：BMS ERROR 起到下一轮时间戳为止都是错误块，
        #     明细行（"1.1 Over Voltage" 这些）跟着整行标橙 —— **序号也是橙的**
        #     （2026-10-12 用户点名"高亮要包括序号"，不再把序号染回蓝）。
        detail_row = next(i for i, t in enumerate(texts) if "Over Voltage" in t)
        detail_text = texts[detail_row]
        detail_chars = {}
        for s, n, c in painted_spans(detail_row):
            for p in range(s, s + n):
                detail_chars[p] = c.name()
        assert all(detail_chars.get(p) == yellow
                   for p in range(len(detail_text))), \
            f"BMS 错误明细行没有整行标橙（含序号）：{painted_spans(detail_row)}"

        # ---- 5c) 温度值落进电压段状态也不能标绿/标红（用户点名"标绿的区间
        #     仅限于电压数据读取部分，不能到温度采集部分去"）：设备不发
        #     Temperature1: 标题时温度行会停在电压段状态 —— 电压红/绿只认
        #     mV 格式（4 位小数），且对齐补零不许把 "1.22" 造出 "01.0022"
        #     这种假数据（凭空补位 = 篡改数值）。
        window._clear_receive()
        window._on_received(("[2026-10-12 20:00:00]\n"
                             "01.3700 02.3820 03.3900\n"
                             "1 1.22 2.87 3 1.19\n").encode("utf-8"))
        window._flush_rx()
        app.processEvents()
        doc5c = window.txt_rx.document()
        texts5c = [doc5c.findBlockByNumber(b).text()
                   for b in range(doc5c.blockCount())]
        volt_row5c = next(i for i, t in enumerate(texts5c) if ".3700" in t)
        stray_row = next(i for i, t in enumerate(texts5c) if "1.22" in t)
        # 温度值没有被补零成 4 位小数（"1.22" 还是 "1.22"，不是 "01.0022"）
        assert texts5c[stray_row] == "1 1.22 2.87 3 1.19", \
            f"电压段状态里的温度值被补零篡改了：{texts5c[stray_row]!r}"
        # 该行没有任何电压分级色（红/绿都不许出现）
        stray_spans = painted_spans(stray_row)
        assert not [c for _, _, c in stray_spans
                    if c.name() in (red, green)], \
            f"温度值在电压段状态里被标了红/绿：{stray_spans}"
        # 电压行照常分级（前提不破）
        volt_spans5c = painted_spans(volt_row5c)
        assert [c for _, _, c in volt_spans5c if c.name() == green], \
            f"（前提）电压欠压行本该标绿：{volt_spans5c}"

        # ---- 5c) 段落边界（用户点名）：电压在**时间戳 → Temperature1** 之间，
        #     温度在 **Temperature1 → BMS** 之间。所以不能只认 "Voltage1:" 标题 ——
        #     设备不保证每轮都发它，只认标题会把那些轮整个漏掉。
        #     （这一段必须放在 5/5b 之后：它要清空接收区另起一组数据。）
        from ui_highlight import (
            SECTION_OTHER, SECTION_TEMP, SECTION_VOLT, section_of,
        )
        assert section_of("[21:47:14]", SECTION_TEMP) == SECTION_VOLT, \
            "时间戳没有把段重置成电压（没有 Voltage1 标题的那些轮会整个漏掉）"
        assert section_of("1.3820 2.3824", SECTION_VOLT) == SECTION_VOLT
        assert section_of("Temperature1:", SECTION_VOLT) == SECTION_TEMP
        assert section_of("1.22 2.95", SECTION_TEMP) == SECTION_TEMP
        assert section_of("BMS ERROR", SECTION_TEMP) == SECTION_OTHER, \
            "BMS 之后的数据不该再按温度判"
        assert section_of("BMS OK", SECTION_TEMP) == SECTION_OTHER, \
            "BMS OK 也是段的右边界"

        # 端到端：这一轮**没有 Voltage1 标题**（时间戳后直接是电压数据），
        # 电压值照样按电压阈值着色；BMS 之后的数值谁都不染。
        customization.set("temp_limit_low", 0)
        customization.set("temp_limit_high", 60)
        window.chk_timestamp.setChecked(True)       # 这一轮要靠时间戳当段锚
        window._rx_highlighter.refresh()
        window._clear_receive()
        window._on_received("1.3700 2.3820\n"
                            "Temperature1:\n1.22 2.95\n"
                            "BMS ERROR\n1.99 2.99\n".encode("utf-8"))
        window._flush_rx()
        app.processEvents()
        doc6 = window.txt_rx.document()
        rows = [doc6.findBlockByNumber(b).text() for b in range(doc6.blockCount())]
        assert rows[0].startswith("["), f"这一帧顶上该有时间戳：{rows[:2]!r}"
        volt_row_noheader = next(i for i, t in enumerate(rows) if ".3700" in t)
        temp_row_noheader = next(i for i, t in enumerate(rows) if ".95" in t)
        after_bms = next(i for i, t in enumerate(rows) if ".99" in t)
        volt_spans = painted_spans(volt_row_noheader)
        assert [(s, n) for s, n, c in volt_spans if c.name() == green] \
            and [(s, n) for s, n, c in volt_spans if c.name() == red], \
            f"没有 Voltage1 标题的电压数据没有被分级着色：{volt_spans}"
        assert any(c.name() == qcolor("temp_warn").name()
                   for _, _, c in painted_spans(temp_row_noheader)), \
            f"温度越界没有标黄：{painted_spans(temp_row_noheader)}"
        # BMS 之后的数值行：作为**错误明细**整行标橙（2026-10-11 用户点名），
        # 序号也是橙的（2026-10-12 用户点名"高亮要包括序号"）；关键是不能按
        # 温度判成黄（用户：温度数据只到 BMS 为止）。
        ab_text = rows[after_bms]
        ab_chars = {}
        for s, n, c in painted_spans(after_bms):
            for p in range(s, s + n):
                ab_chars[p] = c.name()
        assert all(ab_chars.get(p) == yellow for p in range(len(ab_text))), \
            f"BMS 错误明细行没有整行标橙（含序号）：{painted_spans(after_bms)}"
        assert not any(c.name() == qcolor("temp_warn").name()
                       for _, _, c in painted_spans(after_bms)), \
            "BMS 之后的数值不该再按温度判（用户：温度数据只到 BMS 为止）"

        # ---- 5e) 「高亮异常数据」开关（2026-10-12 用户点名要一个开关）----
        # 关掉后电压越界（红/绿）、温度越界（黄）、BMS 错误（橙）一律不上色，
        # 只留时间戳紫 + 序号蓝的常规着色；拨回来立刻恢复。
        def anomaly_spans(blocks) -> list:
            names = {qcolor("volt_high").name(), qcolor("volt_low").name(),
                     qcolor("temp_warn").name(), qcolor("bms_error").name()}
            found = []
            for b in blocks:
                found += [(s, n, c.name()) for s, n, c in painted_spans(b)
                          if c.name() in names]
            return found

        all_rows = range(window.txt_rx.document().blockCount())
        assert anomaly_spans(all_rows), "（前提）开着开关时本该有异常色"
        customization.set("highlight_errors", False)
        window._rx_highlighter.refresh()
        app.processEvents()
        assert anomaly_spans(all_rows) == [], \
            f"关掉「高亮异常数据」后仍有异常色：{anomaly_spans(all_rows)}"
        assert any(c.name() == rx_accent().name()
                   for b in all_rows for _, _, c in painted_spans(b)), \
            "关掉高亮开关后连序号蓝都没了（这只该关异常色）"
        customization.set("highlight_errors", True)
        window._rx_highlighter.refresh()
        app.processEvents()
        assert anomaly_spans(all_rows), "拨回「高亮异常数据」后异常色没有恢复"
        # 范围复原
        customization.set("volt_limit_low", 2500)
        customization.set("volt_limit_high", 4500)
        window._rx_highlighter.refresh()
    finally:
        mw.list_serial_ports = real_ports
        window.close()
        restore_settings(settings, snapshot)
        settings.sync()


def check_caption_bar(app: QApplication) -> None:
    """自绘标题栏：三颗窗口键在**任何状态**都在、位置不变、点了有反应。

    背景：窗口改成无边框（``FramelessWindowHint``）才能让全屏时 shell 认得出
    "这是个全屏应用"从而藏掉任务栏；而系统标题栏在无边框下整个没有了，所以
    最小化 / 最大化还原 / 关闭这三颗必须自己画（用户点名："当窗口切换为最大化
    状态时，请保留这三个按键……并在最大化与还原两种状态下保持一致的可点击性和
    显示效果"）。

    几条断言都是"改回去就挂"的形状：把标题栏塞进品牌页头（展开态会被隐藏）、
    或者写死成只在窗口化时显示，都会在这里翻车。
    """
    import sys as _sys

    from PySide6.QtCore import Qt

    from main_window import RESIZE_BAND
    from ui_theme import (
        CAPTION_BTN_H, CAPTION_H, CAPTION_INSET, GLYPH_WIN_MAX, GLYPH_WIN_RESTORE,
    )

    fullscreen_testable = (_sys.platform == "win32"
                           and os.environ.get("QT_QPA_PLATFORM") != "offscreen")

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    window = make_window()
    try:
        window.resize(1100, 820)
        window.show()
        app.processEvents()

        # 00) 素材得先在位：窗口图标（app.ico）和队标（lockup_*.svg）都从 assets/ 取，
        #     取不到时界面只是**静默地少东西**，而下面那条"有没有画图标"的断言会以
        #     "根本没画图标"的形式报出来 —— 看着像绘制代码坏了，其实是图没了。
        #     （在 _check 副本目录里跑自测时踩过：副本只 cp *.py、没带 assets，
        #     一批反向验证全被这条假挂带偏。反向验证副本请用
        #     `cp -r assets _check/revN/` 一起复制。）
        from ui_brand import asset_dir
        for name in ("app.ico", "lockup_on_dark.svg", "lockup_on_light.svg"):
            path = os.path.join(asset_dir(), name)
            assert os.path.exists(path), \
                f"素材缺失：{path}（跑自测要带上 assets/，别只拷 *.py）"

        # 0) 窗口必须是无边框 —— 三键自绘与"全屏藏任务栏"都建立在这上面
        assert bool(window.windowFlags() & Qt.WindowType.FramelessWindowHint), \
            "主窗口不是无边框：自绘标题栏没有意义，全屏也藏不住任务栏"

        bar = window._caption_bar
        buttons = (bar.btn_min, bar.btn_max, bar.btn_close)

        def layout_snapshot() -> list:
            """三颗键相对**窗口右上角**的偏移 + 尺寸 + 是否可见 —— 跨状态比对用。

            ⚠️ 量的是"离右边缘多远"而不是绝对 x：窗口一全屏宽度就变了，键**必须**
            跟着右边缘走（这正是系统标题栏的行为，也就是用户要的"位置不变"——
            相对位置不变）。拿绝对 x 去比，全屏这一条必然假挂（实测 1070 → 1536）。
            """
            return [(window.width() - (b.mapTo(window, QPoint(0, 0)).x() + b.width()),
                     b.mapTo(window, QPoint(0, 0)).y(),
                     b.width(), b.height(), b.isVisible())
                    for b in buttons]

        # 1) 三键横排、同一条横线上、顺序是 最小化 / 最大化 / 关闭，且都在标题栏里
        assert bar.mapTo(window, QPoint(0, 0)).y() == 0, \
            "标题栏没有贴着窗口上沿（顶上还压着别的东西）"
        spots = layout_snapshot()
        absolute = [b.mapTo(window, QPoint(0, 0)).x() for b in buttons]
        ys = {s[1] for s in spots}
        assert len(set(absolute)) == 3 and absolute == sorted(absolute), \
            f"三键没有按顺序横排（左→右应为 最小化/最大化/关闭）：{absolute}"
        assert len(ys) == 1, f"三键不在同一条横线上：{spots}"
        assert all(s[4] for s in spots), f"有键没显示出来：{spots}"
        assert absolute[2] + buttons[2].width() <= window.width(), "关闭键跑到窗口外面了"
        top = next(iter(ys))
        assert 0 <= top and top + CAPTION_BTN_H <= CAPTION_H, \
            f"键跑到标题栏外面了：y={top}，栏高 {CAPTION_H}"

        # 1b) 三颗键要**贴近窗口右上角**。用户点名："距离界面最右上角仍有较大
        #     间隙，请调整为更贴近右上角的位置" —— 原来标题栏跟着内容一起被根布局
        #     往里推了 GAP - SHADOW_PAD，三颗键离窗口右边其实有 18px。现在标题栏
        #     整条出血到窗口左右边缘（见 main_window._build_ui），关闭键只剩
        #     CAPTION_INSET。
        assert bar.width() == window.width(), \
            f"标题栏没有出血到窗口边缘：栏宽 {bar.width()}，窗口宽 {window.width()}"
        bar_left = bar.mapTo(window, QPoint(0, 0)).x()
        assert bar_left == 0, f"标题栏左端没贴住窗口左边（x={bar_left}）"
        close_gap = window.width() - (
            bar.btn_close.mapTo(window, QPoint(0, 0)).x() + bar.btn_close.width())
        assert 0 < close_gap <= CAPTION_INSET + 1, \
            (f"关闭键离窗口右边 {close_gap}px，太远（应 <= {CAPTION_INSET}px）"
             "：标题栏没出血 / CAPTION_INSET 被改大了")

        windowed = layout_snapshot()

        # 2) 接收区展开态：页头/配置卡/发送卡都会藏起来，标题栏必须留下
        window._expand_receive()
        app.processEvents()
        assert bar.isVisible(), "接收区展开之后标题栏被藏掉了"
        assert wait_for(app, lambda: window.brand_header.isHidden(), timeout=3.0), \
            "（前提）展开态本来就该藏品牌页头（现在是动画收起，要等它跑完）"
        assert layout_snapshot() == windowed, \
            f"展开态三键的位置/大小变了：{layout_snapshot()} vs {windowed}"
        window._collapse_receive()
        app.processEvents()

        # 3) 全屏态：位置同样一个像素都不能动，中间那颗键换成"还原"的面孔
        assert bar.btn_max._glyph == GLYPH_WIN_MAX, "窗口化时中间那颗不是最大化图标"
        window.startup_fullscreen()
        app.processEvents()
        assert layout_snapshot() == windowed, \
            f"全屏态三键的位置/大小变了：{layout_snapshot()} vs {windowed}"
        if fullscreen_testable:
            assert wait_for(app, lambda: window.isFullScreen()), "没有进全屏"
            assert bar.btn_max._glyph == GLYPH_WIN_RESTORE, \
                "全屏之后中间那颗没有变成还原图标"
            # 点它 → 还原；再点 → 回到全屏。两下都要真的动窗口。
            bar.btn_max.click()
            app.processEvents()
            assert wait_for(app, lambda: not window.isFullScreen()), \
                "点标题栏中间那颗没有还原窗口"
            assert layout_snapshot() == windowed, "还原之后三键的位置变了"
            assert bar.btn_max._glyph == GLYPH_WIN_MAX, "还原之后图标没有换回最大化"
            bar.btn_max.click()
            app.processEvents()
            assert wait_for(app, lambda: window.isFullScreen()), \
                "再点一次没有回到全屏"

            # 3b) 全屏窗口必须是**方角**。圆角是在窗口**内侧**切掉四块，全屏时
            #     四角就露出桌面 —— 用户报的"改了设置之后四周出现缝隙"根因就是
            #     换主题让 apply_titlebar_theme 把圆角又打开了一次。
            import ctypes

            def corner_pref() -> int:
                """DWM 的窗口圆角偏好：1 = 方角、2 = 圆角。"""
                value = ctypes.c_int(-1)
                dwmapi = ctypes.windll.dwmapi
                dwmapi.DwmGetWindowAttribute.argtypes = [
                    ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
                dwmapi.DwmGetWindowAttribute(
                    ctypes.c_void_p(int(window.winId())), 33,
                    ctypes.byref(value), ctypes.sizeof(value))
                return value.value

            assert corner_pref() == 1, "全屏窗口不是方角（四角会漏出桌面）"
            window._apply_theme("light")        # 换主题会重走 apply_titlebar_theme
            app.processEvents()
            assert corner_pref() == 1, \
                "换主题之后全屏窗口的圆角又被打开了（四角漏桌面）"
            window._apply_theme("dark")
            app.processEvents()
            assert corner_pref() == 1, "切回深色之后圆角又回来了"

            # 3c) 打开**设置面板**再关掉，主窗口必须仍是方角。
            #     ⚠️ 这条是用户报的"点完设置保存退出后最大化界面四周有缝隙"的
            #     复现路径：设置面板是主窗口的模态子窗口，给对话框设标题栏主题
            #     若沿父链上溯取句柄（GetParent），拿到的恰好就是**主窗口** ——
            #     于是"给对话框设圆角"顺手把全屏主窗口的圆角重新打开，四角当场
            #     切掉露出桌面。只有开过设置才会出现，与换不换主题无关。
            from ui_settings import SettingsDialog as _SD
            _dlg = _SD(window._theme_mode, 0, parent=window)
            try:
                _dlg.show()
                app.processEvents()
                assert corner_pref() == 1, \
                    "打开设置面板后主窗口圆角被打开了（标题栏主题作用域错误）"
            finally:
                _dlg.close()
            app.processEvents()
            assert corner_pref() == 1, \
                "关闭设置面板后主窗口圆角仍是圆的（四角漏桌面）"

        # 4) 无边框窗口没有系统的缩放边框，边缘那一圈得自己认。
        #    先把窗口弄回普通态：第 3 条可能把它留在全屏，而 offscreen 下
        #    isFullScreen 不可信（因此不靠它判断，直接补一次 showNormal）。
        window._want_fullscreen = False
        window._expect_normal = True
        window.showNormal()
        window._expect_normal = False
        app.processEvents()
        assert window._resize_edges(QPoint(1, 1)) == (
            Qt.Edge.LeftEdge | Qt.Edge.TopEdge), "左上角没认出来"
        assert window._resize_edges(
            QPoint(window.width() - 1, window.height() - 1)) == (
            Qt.Edge.RightEdge | Qt.Edge.BottomEdge), "右下角没认出来"
        assert window._resize_edges(QPoint(2, window.height() // 2)) == \
            Qt.Edge.LeftEdge, "左边缘没认出来"
        assert window._resize_edges(QPoint(window.width() // 2,
                                           window.height() // 2)) == Qt.Edge(0), \
            f"窗口中间不该被判成边缘（带宽 {RESIZE_BAND}px）"
        if fullscreen_testable:
            window.startup_fullscreen()
            app.processEvents()
            assert window._resize_edges(QPoint(1, 1)) == Qt.Edge(0), \
                "全屏时还开着边缘缩放的口子"
            window._on_escape()
            app.processEvents()

        # 4b) 边缘的**光标提示**（用户 2026-10-12 点名："需像其他 Windows 软件
        #     一样显示边框拖拽提示"）：移到边缘/四角换对应的缩放光标，离开还原。
        #     ⚠️ 事件发给**窗口内的子控件**（真机上鼠标在边缘时事件就发生在
        #     那儿）；而且必须确认"命中边缘时事件被吃掉" —— QWidget 默认会
        #     ignore() 掉 mouseMove 并沿父链上传，不拦住的话第二次（父窗口）
        #     会把刚设在子控件上的光标当成"换了宿主"撤掉（真机实测踩过）。
        from PySide6.QtGui import QMouseEvent as _QMove
        _cursor_host = window.centralWidget()
        assert _cursor_host is not None, "窗口没有中央控件，光标用例没法做"

        def _send_move(point: QPoint) -> None:
            event = _QMove(QEvent.Type.MouseMove, QPointF(point),
                           QPointF(_cursor_host.mapToGlobal(point)),
                           Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier)
            app.sendEvent(_cursor_host, event)

        def _cursor_shape() -> str:
            return _cursor_host.cursor().shape().name

        for point, want in ((QPoint(2, _cursor_host.height() // 2), "SizeHorCursor"),
                            (QPoint(_cursor_host.width() // 2, 2), "SizeVerCursor"),
                            (QPoint(2, 2), "SizeFDiagCursor"),
                            (QPoint(window.width() - 3, 2), "SizeBDiagCursor")):
            _send_move(point)
            assert _cursor_shape() == want, \
                f"{point.toTuple()} 处的边缘光标提示不对：{_cursor_shape()}，应当 {want}"
        _send_move(QPoint(_cursor_host.width() // 2, _cursor_host.height() // 2))
        assert "Size" not in _cursor_shape(), \
            f"离开边缘后光标没有还原成控件原本的形状：{_cursor_shape()}"
        if fullscreen_testable:
            window.startup_fullscreen()
            app.processEvents()
            _send_move(QPoint(2, _cursor_host.height() // 2))
            assert "Size" not in _cursor_shape(), \
                f"全屏时还提示缩放光标（应当只在从最大化退出后才出现）：{_cursor_shape()}"
            window._on_escape()
            app.processEvents()

        # 5) 左端要画**窗口图标** —— 系统标题栏被无边框去掉之后，原来钉在标题栏
        #    左端那枚图标也跟着没了（用户："我的 logo 呢，logo 得保留啊"），
        #    所以自绘标题栏必须把它画回来。按"那块区域不是纯背景色"来判，
        #    不看具体颜色，两套主题都成立。
        from PySide6.QtGui import QImage

        dpr = bar.devicePixelRatioF()
        shot = QImage(int(bar.width() * dpr), int(bar.height() * dpr),
                      QImage.Format.Format_ARGB32)
        shot.setDevicePixelRatio(dpr)
        shot.fill(0)
        bar.render(shot)
        background = shot.pixelColor(shot.width() - 2, 2)       # 标题栏右端空白处
        left_px = int(bar.TITLE_LEFT * dpr)     # 别写字面量：那个常量随出血布局变
        icon_px = int(bar.ICON_PX * dpr)
        gap_px = icon_px + int(bar.ICON_GAP * dpr)   # 图标之后、文字之前的留白宽度

        def column_has_ink(x: int) -> bool:
            for y in range(shot.height()):
                c = shot.pixelColor(x, y)
                if max(abs(c.red() - background.red()),
                       abs(c.green() - background.green()),
                       abs(c.blue() - background.blue())) > 24:
                    return True
            return False

        # ⚠️ 不能只断言"左端那块有色差" —— 少了图标，标题文字会**左移**顶到那个
        # 位置上，色差照样有，断言就白测了（反向验证时踩到过）。要有图标，
        # 就得同时满足两条：图标那一格有墨迹 **且** 图标与文字之间留出空档。
        assert any(column_has_ink(x) for x in range(left_px, left_px + icon_px)), \
            "标题栏左端没有画出窗口图标"
        assert not any(column_has_ink(x)
                       for x in range(left_px + icon_px, left_px + gap_px)), \
            ("图标和标题文字之间没有留白 —— 说明根本没画图标，文字直接顶到了左端"
             "（用户：我的 logo 呢，logo 得保留啊）")

        # 6) 关闭键真的连着关窗（用一次性窗口，别把主窗口关掉了影响后面的用例）
        scratch = make_window()
        scratch.show()
        app.processEvents()
        scratch._caption_bar.btn_close.click()
        app.processEvents()
        assert not scratch.isVisible(), "点关闭键没有关窗"
    finally:
        window.close()
        restore_settings(settings, snapshot)
        settings.sync()


def check_send_collapsed_startup(app: QApplication) -> None:
    """「默认折叠发送区」：默认开 = 保持老行为；关掉后**下次启动**发送区开局展开。

    验证三件事：① 配置项存在、默认 True；② 值 True 时新窗口开局折叠、False 时
    开局展开（跨"启动"就是重新 make_window）；③ 设置面板里确实生成了这枚开关
    且与其它开关同款（Switch + 可读名称，无障碍）。
    """
    import customization
    from ui_controls import Switch

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    try:
        spec = next(s for s in customization.specs()
                    if s["key"] == "send_collapsed_startup")
        assert spec["kind"] == "bool" and spec["default"] is True, \
            f"「默认折叠发送区」应当是默认打开的布尔项：{spec}"
        assert spec["label"] == "默认折叠发送区", f"标签文案变了：{spec['label']}"
        assert spec["tip"], "这一项没有给说明文字（无障碍/悬停提示都需要）"

        # 默认（开）→ 新窗口开局折叠
        customization.set("send_collapsed_startup", True)
        window = make_window()
        try:
            window.resize(1100, 820)
            window.show()
            app.processEvents()
            assert window._tx_collapsed is True, \
                "开关开着，发送区开局却不是折叠的"
            assert not window._tx_content.isVisible(), "折叠态发送区内容还露着"
        finally:
            window.close()

        # 关掉 → 新窗口开局展开（跨启动生效的关键路径）
        customization.set("send_collapsed_startup", False)
        window = make_window()
        try:
            window.resize(1100, 820)
            window.show()
            app.processEvents()
            assert window._tx_collapsed is False, \
                "关掉开关后，发送区开局仍然是折叠的（没有按配置初始化）"
            assert window._tx_content.isVisible(), "展开态发送区内容没显示"
        finally:
            window.close()

        # 设置面板里确实有这枚开关，且是统一款式的 Switch + 可读名称
        from ui_settings import SettingsDialog
        dialog = SettingsDialog("dark", 0)
        try:
            toggle = dialog._custom_widgets.get("send_collapsed_startup")
            assert isinstance(toggle, Switch), \
                "「默认折叠发送区」不是统一的拨动开关控件"
            assert toggle.accessibleName() == "默认折叠发送区", \
                "这枚开关缺少读屏名称（无障碍）"
        finally:
            dialog.close()
    finally:
        restore_settings(settings, snapshot)
        settings.sync()


def check_startup_state(app: QApplication) -> None:
    """「启动时最大化」开关：默认开（整窗全屏、盖住任务栏），关掉则普通窗口起手。

    被测的是 `main.py` 里 `show()` 之后那一次 `apply_startup_state()`。两条分支都必须
    真的改变窗口姿态；尤其是**关掉之后不能再被延迟核对（`_verify_fullscreen`）钉回
    全屏** —— 那条只在 `_want_fullscreen` 为真时动手，所以关掉时必须把它降下来。
    """
    import sys as _sys

    import customization

    from ui_theme import GLYPH_WIN_MAX, GLYPH_WIN_RESTORE

    fullscreen_testable = (_sys.platform == "win32"
                           and os.environ.get("QT_QPA_PLATFORM") != "offscreen")

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    window = make_window()
    try:
        window.resize(1100, 820)
        window.show()
        app.processEvents()

        # 0) 注册表里必须有这一项，默认是**开** —— 老用户升级上来不该突然变成
        #    窗口起手（这个软件的默认就是"打开即全屏、盖住任务栏"）。
        spec = next(s for s in customization.specs() if s["key"] == "startup_maximized")
        assert spec["kind"] == "bool" and spec["default"] is True, \
            f"「启动时最大化」应当是默认打开的布尔项：{spec}"
        assert customization.get("startup_maximized") is True, \
            "「启动时最大化」默认没有打开"

        # 1) 开着 → 起手全屏（窗口状态位只在 win32 真机可信）
        window.apply_startup_state()
        app.processEvents()
        assert window._want_fullscreen is True, \
            "开关开着，却没有把「想要全屏」记下来"
        if fullscreen_testable:
            assert wait_for(app, lambda: window.isFullScreen()), \
                "开关开着，起手却不是全屏"
            assert window._caption_bar.btn_max._glyph == GLYPH_WIN_RESTORE, \
                "全屏起手时，标题栏中间那颗键不是「还原」的面孔"
        window._on_escape()                     # 模拟用户按 Esc 回普通窗口
        app.processEvents()

        # 2) 关掉 → 起手停在普通窗口。
        #    先把「想要全屏」置回 True：真实场景正是这样 —— 用户刚在设置面板里把
        #    开关从「开」拨到「关」，此刻窗口还在全屏、`_want_fullscreen` 也是 True。
        #    关掉之后必须把它降下来，否则延迟核对会立刻把窗口钉回全屏。
        customization.set("startup_maximized", False)
        window._want_fullscreen = True
        window.apply_startup_state()
        app.processEvents()
        assert window._want_fullscreen is False, \
            ("关掉「启动时最大化」之后「想要全屏」还是 True —— 延迟核对会趁机"
             "把窗口钉回全屏")
        if fullscreen_testable:
            assert not window.isFullScreen(), "关掉了开关，起手仍然是全屏"
            assert window._caption_bar.btn_max._glyph == GLYPH_WIN_MAX, \
                "普通窗口起手时，中间那颗键不是「最大化」的面孔"
            window._verify_fullscreen()         # 此刻的延迟核对必须是空操作
            assert not window.isFullScreen(), "普通窗口起手被延迟核对钉成了全屏"

        # 3) 设置面板里拨这个开关 → 当场生效，不用重启
        customization.set("startup_maximized", True)
        window._apply_startup_maximized()
        app.processEvents()
        assert window._want_fullscreen is True, "面板拨回「开」之后没有当场要全屏"
        if fullscreen_testable:
            assert wait_for(app, lambda: window.isFullScreen()), \
                "面板拨回「开」之后没有当场进全屏"
        customization.set("startup_maximized", False)
        window._apply_startup_maximized()
        app.processEvents()
        assert window._want_fullscreen is False, "面板拨到「关」之后没有放下全屏"
        if fullscreen_testable:
            assert wait_for(app, lambda: not window.isFullScreen()), \
                "面板拨到「关」之后没有退出全屏"

        # 4) 面板确实为这一项生成了控件（「定制功能」是注册表驱动的）
        from ui_settings import SettingsDialog
        dialog = SettingsDialog("dark", 0)
        try:
            assert "startup_maximized" in dialog._custom_widgets, \
                "设置面板没有为「启动时最大化」生成控件"
        finally:
            dialog.close()
    finally:
        window.close()
        restore_settings(settings, snapshot)
        settings.sync()


def check_alert_locate(app: QApplication) -> None:
    """「定位最近一次异常」按钮：跳到最近一行越界（电压/温度）或 BMS ERROR，
    整行高亮，且与搜索高亮**共存**；没有异常时只给提示、不乱跳。

    图标是自绘的**红色感叹号**（用户："按钮的三角形不要了，只保留红色感叹号"），
    第 0 条连"画出来了没有、是不是只剩那一个红叹号"一起验 —— 用字形顶替会没有
    那支固定的警示红。
    """
    import customization
    from PySide6.QtGui import QImage

    from main_window import DEFAULT_RX_BLOCKS

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    pin_rx_grid_defaults(settings)
    window = make_window()
    try:
        window.resize(900, 700)
        window.show()
        app.processEvents()
        window.chk_timestamp.setChecked(False)      # 少一层时间戳行，块号好数
        # 阈值显式设成默认：本 check 的测试数据按"3700/3820 在电压正常范围内、
        # 95 越温度上限"设计 —— 若继承注册表里被前序用例泄漏的阈值
        # （比如 volt_limit_low=3750），帧B 的电压行会被误判成欠压异常，
        # "最近一次异常"定位到错误行，断言假挂。
        customization.set("volt_limit_low", 2500)
        customization.set("volt_limit_high", 4500)
        customization.set("temp_limit_low", 0)
        customization.set("temp_limit_high", 60)
        window._rx_highlighter.refresh()
        window._clear_receive()

        # 0) 按钮在接收区功能区里，且图标真的画出来了
        assert window.btn_alert.parent() is window._rx_rail, \
            "定位异常按钮没挂进接收区功能区"
        dpr = window.btn_alert.devicePixelRatioF()
        shot = QImage(int(window.btn_alert.width() * dpr),
                      int(window.btn_alert.height() * dpr),
                      QImage.Format.Format_ARGB32)
        shot.setDevicePixelRatio(dpr)
        shot.fill(0)                                # 全透明 = 只有按钮自己画的东西
        window.btn_alert.render(shot)
        ink_px, red_px = 0, 0
        for y in range(shot.height()):
            for x in range(shot.width()):
                color = shot.pixelColor(x, y)
                if color.alpha() > 40:
                    ink_px += 1
                    # 警示红（#FF6B6B 那支）：红高、绿蓝都被压下去
                    if color.red() > 120 and color.green() < 120 \
                            and color.blue() < 120:
                        red_px += 1
        # 阈值按"一个感叹号"的量级定：30px 按钮里那条竖线 + 圆点大约 25 个像素
        # （带三角形时是 40+；这条只负责"有没有画东西"，比例那条才管"是不是只剩叹号"）
        assert ink_px > 15, f"按钮上什么都没画（墨迹 {ink_px} 像素）"
        assert red_px > 8, \
            f"红色感叹号没有画出来（红像素只有 {red_px}）"
        # 用户："按钮的三角形不要了，只保留红色感叹号" —— 画出来的墨迹得**几乎全是红的**。
        # 这条同时挡住了"三角形又加回来"（灰色三角会拉低红墨迹的比例）。
        assert red_px >= ink_px * 0.6, \
            (f"按钮上画的不止一个红色感叹号（红 {red_px} / 墨迹 {ink_px}）——"
             "用户要求去掉三角形、只留感叹号")

        # 1) 有异常：点一下 → 跳到**最近一次**那一行并高亮
        window._emit_block("Voltage1:\n1.3700 2.3820 10.3900\n"
                           "Temperature1:\n1.22 2.95 3.45")
        window._emit_block("Voltage1:\n1.3700 2.3820\n"
                           "Temperature1:\n1.22 2.45")
        app.processEvents()
        found = window._last_error_block()
        assert found is not None, "数据里有越界温度，却没判出异常行"
        row_text = window.txt_rx.document().findBlockByNumber(found[0]).text()
        assert "2.95" in row_text, f"定位到的不是最近一次异常：{row_text!r}"
        window.btn_alert.click()
        app.processEvents()
        assert window._alert_selection is not None, "点了定位按钮却没有高亮"
        assert window._alert_cursor.block().text() == row_text, \
            "高亮没有落在异常那一行"
        assert window.txt_rx.extraSelections(), "高亮没有真的交给控件"

        # 2) 与搜索高亮**共存**：setExtraSelections 是全覆盖的，
        #    两边各自设一次的话后设的会把先设的抹掉。
        window._open_search()
        assert wait_for(app, lambda: window._search_bar.isVisible()), "搜索条没打开"
        window.edit_search.setText("1.3700")
        window._refresh_matches()
        app.processEvents()
        count = len(window._matches)
        assert count > 0, "（前提）测试数据里应该有匹配"
        # 搜索的每一处 + 上一步留下的异常高亮，两个都在
        assert len(window.txt_rx.extraSelections()) == count + 1, \
            (f"搜索高亮和异常高亮没有同时生效："
             f"{len(window.txt_rx.extraSelections())} 处，应当 {count + 1}")
        window.btn_alert.click()
        app.processEvents()
        assert len(window.txt_rx.extraSelections()) == count + 1, \
            ("重新定位异常时把搜索高亮顶掉了（setExtraSelections 是全覆盖的，"
             "两边必须走 _apply_extra_selections 合并）")
        window._close_search()
        app.processEvents()

        # 3) 高亮钉在那一行上：那一行被 maximumBlockCount 裁掉之后必须自己撤掉，
        #    否则那层黄底会飘到别的行上去。
        window.txt_rx.setMaximumBlockCount(2)
        for index in range(6):
            window._emit_block(f"Voltage1:\n1.3700 2.38{index}0")
        app.processEvents()
        assert window._alert_selection is None, \
            "高亮的那一行已经被裁掉了，高亮却还留着（会飘到别的行上）"
        window.txt_rx.setMaximumBlockCount(DEFAULT_RX_BLOCKS)

        # 4) 没有异常：只提示，不跳、不高亮
        window._clear_receive()
        window._emit_block("Voltage1:\n1.3700 2.3820\nTemperature1:\n1.22 2.45")
        app.processEvents()
        assert window._last_error_block() is None, "（前提）这组数据本来没有异常"
        window.btn_alert.click()
        app.processEvents()
        assert window._alert_selection is None, "没有异常却给出了高亮"
        assert window.lbl_toast.text(), "没有异常时应该给一句底部提示"
        assert "没有" in window.lbl_toast.text(), \
            f"提示语没说清楚是「没有异常」：{window.lbl_toast.text()!r}"

        # 5) BMS ERROR 只是**兜底**：用户的说法是"定位到最近一次读取到的电压有误
        #    或温度有误的位置"，所以真有越界时不能跳到 BMS 那一行去。
        window._clear_receive()
        window._emit_block("1.3700 2.3820\nTemperature1:\n1.22 2.45\nBMS ERROR")
        app.processEvents()
        only_bms = window._last_error_block()
        assert only_bms is not None, "只有 BMS ERROR 时应当也能定位（兜底）"
        assert window.txt_rx.document().findBlockByNumber(only_bms[0]).text() \
            .strip() == "BMS ERROR", "只有 BMS ERROR 时没有兜底到那一行"

        window._clear_receive()
        window._emit_block("1.3700 2.3820\nTemperature1:\n1.22 2.95\nBMS ERROR")
        app.processEvents()
        both = window._last_error_block()
        row = window.txt_rx.document().findBlockByNumber(both[0]).text()
        assert "2.95" in row, \
            f"有越界的温度时不该跳到 BMS ERROR 那一行，实际跳到：{row!r}"

        # 6) 优先级：**电压异常 > 温度异常 > BMS**（用户点名）。电压与温度同时
        #    有问题时，一键定位必须落在电压那一行 —— 不管谁先出现。
        customization.set("volt_limit_low", 3750)
        customization.set("volt_limit_high", 3800)
        window._rx_highlighter.refresh()
        window._clear_receive()
        window._emit_block("Temperature1:\n1.22 2.95\n"      # 温度 95 越界（先出现）
                           "Voltage1:\n1.3700 2.3820")        # 电压 3700 欠压（后出现）
        app.processEvents()
        found = window._last_error_block()
        row = window.txt_rx.document().findBlockByNumber(found[0]).text()
        assert "1.3700" in row, \
            f"电压与温度同时异常时应优先定位电压行，实际定位到：{row!r}"
        window._clear_receive()
        window._emit_block("Voltage1:\n1.3700 2.3820\n"      # 电压在前
                           "Temperature1:\n1.22 2.95")        # 温度在后
        app.processEvents()
        found = window._last_error_block()
        row = window.txt_rx.document().findBlockByNumber(found[0]).text()
        assert "1.3700" in row, \
            f"电压在前温度在后时仍应选电压行，实际定位到：{row!r}"
    finally:
        window.close()
        restore_settings(settings, snapshot)
        settings.sync()


CHECKS = [
    ("HEX 编解码", check_hex_codec),
    ("增量文本解码", check_stream_decoder),
    ("串口枚举", check_port_enumeration),
    ("串口插拔扫描与电源开关按钮", check_port_hotplug),
    ("串口建链异步化（不卡界面/可取消）", check_open_async),
    ("蓝牙广播接收（只扫描不配对）", check_ble_broadcast),
    ("回车发送/时间戳轮首/序号染蓝", check_send_enter_and_stamp),
    ("loop:// 收发回环", check_loopback),
    ("界面端到端收发", check_ui_roundtrip),
    ("毛玻璃强度与箭头控件", check_glass_controls),
    ("浅色纯白背景与磨砂颗粒", check_light_pure_white),
    ("接收区搜索", check_receive_search),
    ("全屏展开与复制全部", check_expand_and_copy_all),
    ("定制功能注册表", check_customization),
    ("校验位/保留行数/时钟/发送区一排", check_config_parity_clock),
    ("自动更新模块", check_updater),
    ("MCP 接入入口", check_mcp_payload),
    ("安装包脚本", check_installer_script),
    ("历史接收数据", check_rx_history),
    ("历史数据回放", check_history_replay),
    ("收包合并与暂停继续", check_rx_merge_and_pause),
    ("动画引擎 120fps 与非线性", check_animation_engine),
    ("提示字号与按钮显隐", check_tooltip_and_buttons),
    ("自绘标题栏三键", check_caption_bar),
    ("启动时最大化开关", check_startup_state),
    ("默认折叠发送区开关", check_send_collapsed_startup),
    ("定位最近异常按钮", check_alert_locate),
]


# 自测污染自愈名单：{键: 该键"干净时应有的默认值"}。
#
# 为什么需要它：自测的全局快照会把"进程序时的样子"原样存下来、跑完再写回 ——
# 这个机制能防住"本次运行"的泄漏，但防不住**已经固化的历史污染**：上一轮若因
# 某次事故（比如 check 里 deleteLater() 触发析构回写）把测试值留在了用户配置里，
# 下一次运行就会把它当成"基线"忠实地保存和还原，污染于是永久化、且静默无声
# —— 表现为几条依赖默认值的用例（发送区开局折叠等）稳定假挂，排查方向还容易
# 跑偏到"时序/负载"上去（2026-10-13 实际踩到：custom/send_collapsed_startup
# 被写成 false，导致「校验位/保留行数/时钟/发送区一排」「收包合并与暂停继续」
# 连挂，而配置快照看起来"一直是 false、很稳定"）。
#
# 判据：只清理**与注册默认值不一致**的那些 —— 默认值本身由 customization
# 注册表定义，用户真实改过的非默认值不该被误删……但自测**无权**区分"用户改的"
# 与"上次测漏的"。折中：只对这份显式白名单里的键做自愈，且仅在值等于"已知的
# 污染值"时清理。白名单要**短且专**，绝不放业务配置进去。
_POLLUTION_GUARDS = {
    # (键, 污染值, 干净默认值)
    ("custom/send_collapsed_startup", "false", "true"),
}


def _heal_polluted_keys(settings: QSettings) -> None:
    """清掉历史上被自测写坏、且已被当成基线固化下来的配置值。

    必须在取 global_snapshot **之前**调用，否则快照会把污染值一起存下来，
    还原时又原样写回去（这正是污染能自固化的原因）。
    """
    healed = []
    for key, polluted, clean in _POLLUTION_GUARDS:
        current = settings.value(key)
        if current is None:
            continue
        if str(current).lower() == str(polluted).lower():
            settings.setValue(key, clean)
            healed.append(f"{key}={current} → {clean}")
    if healed:
        settings.sync()
        print("  ⚠ 检测到历史自测污染，已自愈：")
        for item in healed:
            print(f"      {item}")
        print("      （若这确是你手动改的配置，请到「设置 → 定制功能」重新设置一遍）")


def main() -> int:
    app = QApplication(sys.argv)
    # ⚠️ 起手环境必须和 main.py 一致：**Fusion + 应用字体（MiSans 12.5）**。
    #    不设应用字体时，所有"继承默认字体"的控件（下拉框、数字框、标签……）
    #    会退化成系统 9pt —— 自测量到的几何/墨迹和用户看到的是两套东西。
    #    0c 的"左端对齐"就在这个坑上假挂过：真机 13px、自测里 18px（2026-10-12）。
    from ui_theme import ui_font as _ui_font
    app.setStyle("Fusion")
    app.setFont(_ui_font(12.5))
    # 全局快照：任何一条 check 泄漏（中途断言挂掉跳过尾部还原、QSettings 多实例
    # 缓冲的析构回写……）都不能把测试值留在用户真实配置里 —— 全部跑完后无条件
    # 恢复到进程序时的样子。（单条 check 自己的快照还原仍保留，防 check 间串扰。）
    global_settings = QSettings("MimoWorks", "SerialAssistant")
    _heal_polluted_keys(global_settings)
    global_snapshot = {k: global_settings.value(k) for k in global_settings.allKeys()}
    # 自测全程用**默认网格排版**（module_count / cells_per_module / temps_per_module
    # 全 0）：收发/时间戳/定位这几条用例都假设"一段数据在一行里"。若机器上残留了
    # 非零网格配置（例如 module_count=5），电压/温度点会被拆成每行一个 → "整行"
    # 前提不成立，三条用例假挂（2026-10-18 实测：01.3700 与 02.3820 被拆成两行，
    # "最近一次异常"定位到不含 1.3700 的那行）。
    # ⚠️ 必须在 global_snapshot **之后**清零：收尾那段无条件还原会把用户真实值
    #    写回，绝不污染用户配置。
    for _grid_key in ("module_count", "cells_per_module", "temps_per_module"):
        global_settings.setValue(_grid_key, 0)
    global_settings.sync()
    failed = 0
    for name, func in CHECKS:
        try:
            func(app) if func.__code__.co_argcount else func()
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"  PASS  {name}")

    for key in global_settings.allKeys():
        if key not in global_snapshot:
            global_settings.remove(key)
    for key, value in global_snapshot.items():
        global_settings.setValue(key, value)
    global_settings.sync()

    # ⚠️ 还原必须在**所有窗口都析构干净之后**做最后一次：各 check 里 close()
    #    只是隐藏，对象还活着，它们的析构回写会在解释器退出时把测试值重新
    #    写回用户真实配置（hidden_buttons 就这样被永久污染过）。这里显式清掉
    #    所有顶层控件、跑一轮事件循环让析构落地，再无条件还原一次。
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    for key in global_settings.allKeys():
        if key not in global_snapshot:
            global_settings.remove(key)
    for key, value in global_snapshot.items():
        global_settings.setValue(key, value)
    global_settings.sync()

    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} 项通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
