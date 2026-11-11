# -*- coding: utf-8 -*-
"""heartbeatDLC —— BLE 扫描 / **多设备并发连接** / 订阅（Qt 线程层）。

和宿主 ``ble_io.BleBroadcastWorker`` 用同一套约定：``open()`` 只启动线程，
结果走信号回界面线程，``stop()`` 只等 200ms（绝不在界面线程里等 WinRT 回调）。

两段各管一件事：
  ``HeartRateScanner`` 短扫描若干秒 → 列出心率设备（含 10 Pro 名称兜底）；
  ``HeartRateHub``    一个 QThread / 一个 asyncio 事件循环里，为**每台设备**
                      起一个独立 Task：连接 → 订阅 0x2A37 → 断线各自重连。

为什么是一个 Hub 而不是「每台设备一个 QThread」：WinRT 后端在多个线程里各跑
一个 asyncio.run 会各自初始化一套 COM/WinRT 上下文，容易互相抢；同一事件循环里
多个 BleakClient 并发连接是官方支持的路径，也更好统一收尾。
"""

from __future__ import annotations

import asyncio
import threading
import time

from PySide6.QtCore import QThread, Signal

from .hr_protocol import (
    BAND10PRO_HINT, BATTERY_LEVEL_UUID, BODY_SENSOR_LOCATION_UUID, HRM_UUID,
    decode_battery, decode_hr_measurement, decode_sensor_location, device_label,
    is_hr_candidate, is_xiaomi_band, merge_targets, normalize_uuid,
)

POLL_S = 0.2
SCAN_PUSH_S = 0.8
FIND_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 15.0
RECONNECT_DELAY_S = 2.0
# 同一台设备的相同错误 10 秒内只报一次：没开广播时会反复重连，别刷屏
ERROR_DEDUP_S = 10.0


def _friendly_error(exc: BaseException) -> str:
    """把 bleak / WinRT 的异常翻成用户能照着做的一段话。"""
    return (
        f"{type(exc).__name__}: {exc}\n"
        "请依次确认：\n"
        "  1. 电脑蓝牙已打开，且支持 BLE（低功耗蓝牙）；\n"
        "  2. 手环端已开启「心率广播 / Share HR」，并停留在广播页面；\n"
        "  3. 手机没有同时连着这只手环（BLE 单连接，广播时手环会主动断开手机）；\n"
        "  4. 不要在 Windows 设置里配对手环；\n"
        "  5. 手环贴近电脑，心率测量已开始。\n"
        + BAND10PRO_HINT
    )


class HeartRateScanner(QThread):
    """扫描心率设备，结束时把列表一次性送出来。"""

    devices = Signal(object)      # list[dict]，字段见 _on_adv
    failed = Signal(str)

    def __init__(self, seconds: float = 6.0, parent=None) -> None:
        super().__init__(parent)
        self._seconds = max(1.0, float(seconds))
        self._running = False
        self._found: dict[str, dict] = {}
        self._last_push = 0.0

    @property
    def found(self) -> list[dict]:
        """扫描进行中也可以取当前快照（面板做「边扫边列」用）。"""
        rows = sorted(self._found.values(),
                      key=lambda i: (not i.get("xiaomi"),
                                     not i.get("hr_declared", True),
                                     -(i.get("rssi") or -999)))
        return list(rows)

    def open(self) -> None:
        self._running = True
        self.start()

    def stop(self) -> None:
        """⛔ 只等 200ms：WinRT 回调卡住时不能把界面线程钉死（对齐 ble_io）。"""
        self._running = False
        self.wait(200)

    def _on_adv(self, device, adv) -> None:
        address = str(getattr(device, "address", "") or "")
        if not address:
            return
        name = str(getattr(device, "name", "") or getattr(adv, "local_name", "") or "")
        candidate, declared = is_hr_candidate(getattr(adv, "service_uuids", None), name)
        if not candidate:
            return
        rssi = getattr(adv, "rssi", None)
        info = {
            "address": address,
            "name": name,
            "rssi": int(rssi) if isinstance(rssi, (int, float)) else None,
            "xiaomi": is_xiaomi_band(name),
            "hr_declared": declared,
        }
        info["label"] = device_label(info) + ("" if declared else " · 待校验")
        self._found[address] = info
        now = time.monotonic()
        if now - self._last_push >= SCAN_PUSH_S:
            self._last_push = now
            self.devices.emit(self.found)

    async def _scan(self) -> None:
        from bleak import BleakScanner     # 惰性导入：没装 bleak 只影响本插件

        scanner = BleakScanner(detection_callback=self._on_adv,
                              scanning_mode="active")
        await scanner.start()
        try:
            started = time.monotonic()
            while self._running and time.monotonic() - started < self._seconds:
                await asyncio.sleep(POLL_S)
        finally:
            await scanner.stop()
        self.devices.emit(self.found)

    def run(self) -> None:
        try:
            asyncio.run(self._scan())
        except Exception as exc:                      # noqa: BLE001
            self.failed.emit(_friendly_error(exc))


class HeartRateHub(QThread):
    """多设备心率接收中枢：一个事件循环，每台设备一个独立 Task。

    主线程调 ``add`` / ``remove`` / ``set_targets`` / ``clear`` 即可动态改设备集合；
    命令通过 ``loop.call_soon_threadsafe`` 注入，不在 Qt 线程里碰 asyncio 对象。
    """

    sample = Signal(object)                 # dict，含 address / name / bpm / ...
    device_state = Signal(str, str)         # address, 状态文本
    device_error = Signal(str, str)         # address, 完整错误文本
    device_meta = Signal(str, object)       # address, {"battery": int, "location": str}
    failed = Signal(str)                    # 线程级致命错误

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._running = False
        self._targets: dict[str, str] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._pending: list[tuple[str, str, str]] = []
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._find_lock: asyncio.Lock | None = None
        self._last_error: dict[str, tuple[str, float]] = {}

    # -- 主线程可调用 -------------------------------------------------------
    def open(self) -> None:
        self._running = True
        self.start()

    def stop(self) -> None:
        self._running = False
        self.wait(200)

    def add(self, address: str, name: str = "") -> None:
        self._post(("add", (address or "").strip(), name or ""))

    def remove(self, address: str) -> None:
        self._post(("remove", (address or "").strip(), ""))

    def clear(self) -> None:
        self._post(("clear", "", ""))

    def set_targets(self, targets: dict[str, str]) -> None:
        """一次性替换整套目标（同一批命令里比较最终集合，不会误重建未变设备）。"""
        with self._lock:
            self._pending.append(("clear", "", ""))
            for address, name in targets.items():
                self._pending.append(("add", address, name))
        self._notify_drain()

    def _post(self, command: tuple[str, str, str]) -> None:
        with self._lock:
            self._pending.append(command)
        self._notify_drain()

    def _notify_drain(self) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._drain_pending)
            except RuntimeError:                     # 循环已停，命令留到下次启动
                pass

    # -- 事件循环内 ---------------------------------------------------------
    def _drain_pending(self) -> None:
        with self._lock:
            commands, self._pending = self._pending, []
        if not commands:
            return
        new_targets = merge_targets(self._targets, commands)
        removed = set(self._targets) - set(new_targets)
        added = {key: val for key, val in new_targets.items()
                 if key not in self._targets}
        self._targets = new_targets
        for address in removed:
            task = self._tasks.pop(address, None)
            if task is not None:
                task.cancel()
            self.device_state.emit(address, "已停止")
        for address, name in added.items():
            task = asyncio.create_task(self._device_loop(address, name))
            self._tasks[address] = task
            task.add_done_callback(
                lambda _t, addr=address: self._tasks.pop(addr, None))

    async def _find(self, BleakScanner, address: str):
        """按地址找设备；多设备同时重连时用锁串行，避免并发扫描互相抢。"""
        assert self._find_lock is not None
        async with self._find_lock:
            finder = getattr(BleakScanner, "find_device_by_address", None)
            if finder is not None:
                return await finder(address, timeout=FIND_TIMEOUT_S)
            result: dict[str, object] = {}
            scanner = BleakScanner(
                detection_callback=lambda d, _a: result.setdefault(
                    str(getattr(d, "address", "")), d),
                scanning_mode="active")
            await scanner.start()
            try:
                deadline = time.monotonic() + FIND_TIMEOUT_S
                while time.monotonic() < deadline and address not in result:
                    await asyncio.sleep(POLL_S)
            finally:
                await scanner.stop()
            return result.get(address)

    @staticmethod
    def _verify_hrm(client) -> None:
        """连接后确认 0x2A37 真的存在 —— 10 Pro 名称兜底进来的设备靠这步把关。"""
        services = getattr(client, "services", None)
        if services is None:                 # 后端不暴露服务表就交给 start_notify
            return
        for service in services:
            for char in getattr(service, "characteristics", ()):
                if normalize_uuid(getattr(char, "uuid", "")) == HRM_UUID:
                    return
        raise RuntimeError("设备没有提供标准心率特征 0x2A37。" + BAND10PRO_HINT)

    async def _read_meta(self, client, address: str) -> None:
        """10 Pro 等标准设备会带电量 / 佩戴位置，读到就显示，读不到静默跳过。"""
        meta: dict[str, object] = {}
        for uuid, parser, key in (
                (BATTERY_LEVEL_UUID, decode_battery, "battery"),
                (BODY_SENSOR_LOCATION_UUID, decode_sensor_location, "location")):
            try:
                value = parser(await client.read_gatt_char(uuid))
                if value is not None:
                    meta[key] = value
            except Exception:                        # noqa: BLE001 - 可选特征，缺了正常
                pass
        if meta:
            self.device_meta.emit(address, meta)

    def _on_hr(self, address: str, name: str, _sender, data) -> None:
        reading = decode_hr_measurement(data)
        if reading is None:
            return
        self.sample.emit({
            "bpm": reading.bpm,
            "contact": reading.contact,
            "energy": reading.energy,
            "rr_ms": reading.rr_ms,
            "flags": reading.flags,
            "ts": time.time(),
            "address": address,
            "name": name,
        })

    def _emit_error(self, address: str, exc: BaseException) -> None:
        now = time.monotonic()
        key = f"{type(exc).__name__}:{exc}"
        previous = self._last_error.get(address)
        if previous and previous[0] == key and now - previous[1] < ERROR_DEDUP_S:
            return
        self._last_error[address] = (key, now)
        self.device_error.emit(address, _friendly_error(exc))

    async def _wait(self, seconds: float) -> None:
        """可被打断的等待：stop() 置位后最多再走一个 POLL_S 就退出。"""
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            await asyncio.sleep(POLL_S)

    async def _device_loop(self, address: str, name: str) -> None:
        from bleak import BleakClient, BleakScanner

        while self._running:
            try:
                self.device_state.emit(address, "正在查找设备 …")
                device = await self._find(BleakScanner, address)
                if device is None:
                    raise RuntimeError("没找到设备（确认手环停留在心率广播页面）")
                if not self._running:
                    break
                self.device_state.emit(address, "正在连接 …")
                async with BleakClient(device, timeout=CONNECT_TIMEOUT_S) as client:
                    if not getattr(client, "is_connected", False):
                        raise RuntimeError("连接未建立（手机可能正占用手环的 BLE 链路）")
                    self._verify_hrm(client)
                    await self._read_meta(client, address)
                    self.device_state.emit(address, "已连接，等待心率 …")
                    def handler(_sender, data, addr=address, label=name):
                        self._on_hr(addr, label, _sender, data)

                    await client.start_notify(HRM_UUID, handler)
                    try:
                        while self._running and getattr(client, "is_connected", False):
                            await asyncio.sleep(POLL_S)
                    finally:
                        try:
                            await client.stop_notify(HRM_UUID)
                        except Exception:            # noqa: BLE001 - 断线时失败很正常
                            pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:                 # noqa: BLE001
                self._emit_error(address, exc)
            if not self._running:
                break
            self.device_state.emit(address, "连接断开，2 秒后自动重连 …")
            await self._wait(RECONNECT_DELAY_S)
        self.device_state.emit(address, "已停止")

    async def _main(self) -> None:
        # 预先 import 一次，确保缺依赖时在 run() 的 try 里报出可执行的提示
        import bleak                                  # noqa: F401
        self._loop = asyncio.get_running_loop()
        self._find_lock = asyncio.Lock()
        self._drain_pending()
        try:
            while self._running:
                await asyncio.sleep(POLL_S)
        finally:
            tasks = list(self._tasks.values())
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._tasks.clear()
            self._targets.clear()

    def run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:                     # noqa: BLE001
            self.failed.emit(_friendly_error(exc))
