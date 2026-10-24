# -*- coding: utf-8 -*-
"""蓝牙广播（BLE advertising）接收通道 —— **只扫描、不连接、不配对**。

和 ``serial_io.SerialWorker`` 用同一套"契约"（这是刻意的）：
``open()`` 只启动线程、建链结果走 ``opened()`` / ``open_failed(str)``、
数据走 ``received(bytes)``、运行期出错走 ``failed(str)`` —— 于是界面里
电源开关那一整套开/关/取消逻辑可以原样复用，不用为蓝牙再写一份。

**为什么走广播而不是连接**：用户要的是"随时读数据、不绑定"。BLE 外设把数据直接
放进广播包（manufacturer data 或 service data），中心设备**被动扫描**就能收到：
不配对、不连接、多台电脑可同时收，机器重启也不用重连。代价有两条，用之前要清楚：

1. **单向**：只能读，发不了指令（要发就得连接，那是 GATT 那条路）。
2. **带宽很低**：BLE 4.x 单个广播包有效载荷 31 字节，配合典型广播间隔
   （20~100ms），实用吞吐大约 0.3~1.5 KB/s；BLE 5 的扩展广播能到 255 字节/包，
   但要求两端都支持。所以设备侧要把数据拆成小包、降频发送。

设备侧要做的（固件）：把要传的字节放进 advertisement 的 manufacturer data
（公司 ID + 自定义负载）或某个 service data 里，按固定节奏重复广播。软件这边
用 ``source``（manufacturer/service）+ ``key``（公司 ID 或服务 UUID 片段）来认。
"""

from __future__ import annotations

import asyncio
import time

from PySide6.QtCore import QThread, Signal

# 设备列表推送节流：广播包密度可能是每秒上百个（几十个设备 × 每个每秒几包），
# 全都转成 Qt 信号会把主线程的事件队列淹掉。列表只是给人看的，1 秒一次足够。
FOUND_THROTTLE_S = 1.0
# 扫描循环里检查"要不要停"的间隔
POLL_S = 0.2


def parse_key(text: str) -> str:
    """把用户填的 key 规范成小写字符串（公司 ID 支持 0xFFFF / 65535 两种写法）。

    只做规范化不做校验 —— 匹配是"子串包含"，填错最多是收不到数据，
    界面上能一眼看出来（列表里会显示真实的公司 ID / UUID）。
    """
    text = (text or "").strip()
    if not text:
        return ""
    try:
        if text.lower().startswith("0x"):
            return str(int(text, 16)).lower()
        return str(int(text)).lower()
    except ValueError:
        return text.lower()


class BleBroadcastWorker(QThread):
    """被动扫描 BLE 广播，把指定设备的广播负载当数据流送出来。

    ``target``：设备地址（如 ``AA:BB:CC:DD:EE:FF``）或名字片段；空串 = 只列表、
    不喂数据（用于"先扫一扫看看有哪些设备"）。
    ``source``：``"manufacturer"`` / ``"service"``，取哪张表里的负载。
    ``key``：公司 ID（十进制或 0x 十六进制）或服务 UUID 片段；空 = 取该表第一项。
    """

    received = Signal(bytes)
    failed = Signal(str)
    opened = Signal()
    open_failed = Signal(str)
    # 扫到的设备（给界面刷新列表）：{"address","name","rssi","payload"(bytes|None)}
    found = Signal(object)

    def __init__(self, target: str = "", *, source: str = "auto",
                 key: str = "", parent=None) -> None:
        super().__init__(parent)
        self._target = (target or "").strip()
        # auto = 先看 manufacturer data，没有再退到 service data。设备侧把数据
        # 放哪张表里都行，用户不用去配 —— 等真机固件定了再收窄也来得及。
        self._source = source if source in ("manufacturer", "service", "auto") else "auto"
        self._key = parse_key(key)
        self._running = False
        self._last_found: dict[str, float] = {}

    @property
    def port(self) -> str:
        """界面层（底栏状态、断开提示）按"端口"称呼当前连接对象 —— 这里复用同一
        个名字，蓝牙下显示成设备名/地址，别让上层为它分情况。"""
        return self._target or "蓝牙广播"

    def open(self) -> None:
        """请求开始扫描：只启动线程（和串口一样，别在界面线程里等 I/O）。"""
        self._running = True
        self.start()

    def stop(self) -> None:
        """请求停止：置位 + **只等 200ms**（一个轮询周期）。

        ⚠️ 以前 wait(2000)，而它是在**界面线程**调的 —— 扫描正卡在一次 WinRT
        回调里时界面能冻满 2 秒（切回串口、关窗口都会撞上）。串口那边同样的
        方法只等 200ms（见 SerialWorker.stop 的注释），这里对齐：
        没等到也没关系，``run()`` 看到 ``_running`` 已置假会自己收尾，
        调用方用 ``isRunning()`` 判断并挂 ``finished`` 兜底销毁
        （见 main_window._close_port 的 _retiring 分支）。
        """
        self._running = False
        self.wait(200)

    # -- 内部 ------------------------------------------------------------

    def _matches(self, device, adv) -> bool:
        if not self._target:
            return False
        target = self._target.upper()
        if device.address.upper() == target:
            return True
        name = (device.name or adv.local_name or "")
        return bool(name) and self._target.lower() in name.lower()

    def _payload_of(self, adv) -> bytes | None:
        """按 source/key 从广播包里取出负载字节。

        匹配规则：key 为空取该表**排序后的第一项**（确定性，避免每次拿到不同的
        公司）；填了就取 key 是它子串的那一项 —— 公司 ID 在 bleak 里是整数，
        服务 UUID 是字符串，两种都按小写字符串比。
        ``source="auto"``：先 manufacturer 再 service，谁有数据用谁。
        """
        sources = ("manufacturer", "service") if self._source == "auto" \
            else (self._source,)
        for source in sources:
            if source == "service":
                table = {str(k).lower(): v
                         for k, v in (adv.service_data or {}).items()}
            else:
                table = {str(k).lower(): v
                         for k, v in (adv.manufacturer_data or {}).items()}
            if not table:
                continue
            if self._key:
                for name in table:
                    if self._key in name:
                        return bytes(table[name])
                if self._source != "auto":
                    return None          # 指定了来源就只认这一张表
                continue                 # auto：这张表里没有就换下一张
            return bytes(table[sorted(table)[0]])
        return None

    def _on_adv(self, device, adv) -> None:
        payload = self._payload_of(adv)
        now = time.monotonic()
        last = self._last_found.get(device.address, 0.0)
        if now - last >= FOUND_THROTTLE_S:
            self._last_found[device.address] = now
            self.found.emit({
                "address": device.address,
                "name": device.name or adv.local_name or "",
                "rssi": adv.rssi,
                "payload": payload,
            })
        if payload and self._matches(device, adv):
            self.received.emit(payload)

    async def _scan_loop(self) -> None:
        from bleak import BleakScanner      # 延迟导入：没装 bleak 时别影响串口功能

        scanner = BleakScanner(detection_callback=self._on_adv,
                              scanning_mode="passive")
        await scanner.start()
        try:
            self.opened.emit()
            while self._running:
                await asyncio.sleep(POLL_S)
        finally:
            await scanner.stop()

    def run(self) -> None:
        try:
            asyncio.run(self._scan_loop())
        except Exception as exc:                    # noqa: BLE001
            # 起不来多半是：本机没有蓝牙无线电 / 蓝牙关着 / 依赖没打进去。
            # 文本要够具体，用户拿这句就能判断该去开蓝牙还是找我要新版本。
            self._running = False
            self.open_failed.emit(
                f"{type(exc).__name__}: {exc}\n"
                "（请确认电脑有蓝牙、蓝牙已打开，且本程序完整安装）")
