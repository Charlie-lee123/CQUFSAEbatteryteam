# -*- coding: utf-8 -*-
"""heartbeatDLC —— 蓝牙心率协议解析（纯函数层）。

本模块**不 import Qt、不 import bleak**，只做字节 → 数值的换算，因而可以在
没有蓝牙适配器、没有图形环境的机器上做无硬件自测。

适用范围（用户 2026-11-09 明确）：**小米手环 8 代及以后**。这些型号在手环端
开启「心率广播 / Share HR」后，会在 BLE 广播里声明标准 Heart Rate Service
``0x180D``；中心设备连接 GATT，订阅 Heart Rate Measurement ``0x2A37`` 的通知，
即可拿到遵循 Bluetooth SIG 定义的心率测量值。

小米手环 4~7 走的是另一套厂商广播（manufacturer data，公司 ID ``0x0157``），
**本插件不实现**，也不做猜测性兼容 —— 那样容易把误读的字节当成心率显示。

参考（仅借鉴协议事实，未复制其代码）：
- Bluetooth SIG Heart Rate Service 1.0 / Heart Rate Measurement 格式
- Tnze/miband-heart-rate（MIT）：小米手环 10 用 0x180D + 0x2A37 notify
- Lniosy/heart-rate-monitor（MIT）：小米手环 9 Pro 实测 ``00 59`` = 89 bpm
"""

from __future__ import annotations

from dataclasses import dataclass

# 蓝牙 SIG 16 位 UUID 的 128 位完整形式（bleak 在 Windows 返回完整形式，
# 有些固件/资料只写 16 位；两者都要认）。
_UUID_TAIL = "-0000-1000-8000-00805f9b34fb"
HRS_UUID_16 = "180d"
HRM_UUID_16 = "2a37"
HRS_UUID = f"0000{HRS_UUID_16}{_UUID_TAIL}"
HRM_UUID = f"0000{HRM_UUID_16}{_UUID_TAIL}"
# 10 Pro 的 GATT 里同时提供这两项标准特征（用于详情里显示电量 / 佩戴位置）
BATTERY_LEVEL_UUID = "00002a19" + _UUID_TAIL
BODY_SENSOR_LOCATION_UUID = "00002a38" + _UUID_TAIL
SENSOR_LOCATIONS = ("其他", "胸部", "手腕", "手指", "手", "耳垂", "脚")
# 小米手环 10 Pro（含陶瓷特别版）的操作提示。陶瓷版只是外壳/表带/重量不同，
# 内部硬件与固件一致，见小米官网规格页。
BAND10PRO_HINT = (
    "小米手环 10 Pro（含陶瓷特别版）：手环端「设置 → 心率 → 蓝牙心率广播」开启后，"
    "请停留在广播页面 —— 回到表盘时广播通常会停止。广播期间手环会主动断开手机连接，"
    "不要在 Windows 设置里配对手环。"
)

# 小米手环 8 代以后的正常心率区间。超出只作提示，不丢弃数据 —— 运动心率带的
# 极限值可能更宽，误杀比多显示一条更糟。
BPM_MIN = 30
BPM_MAX = 220

# 名称特征只用来在设备列表里做「小米」标注和排序，**不作为硬性过滤条件**：
# 过滤条件只认广播里的 0x180D，任何标准 BLE 心率带都能连。
_XIAOMI_TOKENS = (
    "xiaomi", "mi band", "mi smart band", "smart band", "redmi band",
    "redmi watch", "xiaomi watch", "miband",
)


@dataclass(frozen=True)
class HRReading:
    """一帧 Heart Rate Measurement 的解码结果。

    ``contact``：None = 设备不支持佩戴检测；False = 未检测到皮肤接触；
    True = 已佩戴。小米手环 8~10 的抓包里 flags 常为 ``0x00``（不报接触状态），
    此时界面应显示「未报告」而不是武断地说「未佩戴」。
    ``rr_ms``：RR 间期，单位毫秒（原始值以 1/1024 秒计）。
    """

    bpm: int
    contact: bool | None = None
    energy: int | None = None
    rr_ms: tuple[float, ...] = ()
    flags: int = 0


def normalize_uuid(value: object) -> str:
    """把 16 位 / 128 位 / 带花括号 / 大小写混写的 UUID 归一成小写完整形式。"""
    text = str(value or "").strip().lower().strip("{}")
    if len(text) == 4:
        try:
            int(text, 16)
        except ValueError:
            return text
        return f"0000{text}{_UUID_TAIL}"
    if len(text) == 36 and text.endswith(_UUID_TAIL):
        return text
    return text


def has_hrs(service_uuids) -> bool:
    """广播里是否声明了标准心率服务 ``0x180D``。"""
    try:
        return any(normalize_uuid(uuid) == HRS_UUID for uuid in (service_uuids or ()))
    except TypeError:
        return False


def is_xiaomi_band(name: str) -> bool:
    """按设备名粗判是不是小米手环 / 手表（只影响 UI 标注）。"""
    low = (name or "").lower()
    return any(token in low for token in _XIAOMI_TOKENS)


def plausible_bpm(bpm: int) -> bool:
    """心率值是否落在小米手环 8 代以后的可信区间。"""
    return BPM_MIN <= bpm <= BPM_MAX


def is_xiaomi_10_pro(name: str) -> bool:
    """是不是小米手环 10 Pro（陶瓷特别版广播名通常仍为 Smart Band 10 Pro）。"""
    low = (name or "").lower()
    if "10 pro" not in low and "10pro" not in low:
        return False
    return any(token in low for token in
               ("xiaomi", "mi band", "smart band", "band 10", "ceramic"))


def is_hr_candidate(service_uuids, name: str) -> tuple[bool, bool]:
    """扫描阶段能否把它当心率设备列出。

    返回 ``(是否候选, 广播里是否声明了 0x180D)``。正常情况下只认 0x180D；
    **10 Pro 额外做名称兜底**：部分固件把服务 UUID 放在 scan response，
    或手环刚开广播的那一小段广播里还没带全，这时也列出来让用户能点，
    连接后再用 GATT 校验 0x2A37 是否存在（见 hr_worker.HeartRateHub）。
    """
    if has_hrs(service_uuids):
        return True, True
    if is_xiaomi_10_pro(name):
        return True, False
    return False, False


def decode_battery(data) -> int | None:
    """``0x2A19`` Battery Level：一个字节的百分比。"""
    try:
        raw = bytes(data)
        return raw[0] if raw and 0 <= raw[0] <= 100 else None
    except (TypeError, ValueError):
        return None


def decode_sensor_location(data) -> str | None:
    """``0x2A38`` Body Sensor Location：枚举值 → 中文位置。"""
    try:
        raw = bytes(data)
        if not raw:
            return None
        return SENSOR_LOCATIONS[raw[0]] if raw[0] < len(SENSOR_LOCATIONS) else None
    except (TypeError, ValueError):
        return None


def decode_hr_measurement(data) -> HRReading | None:
    """解一帧 ``0x2A37`` 通知。格式不合法返回 ``None``，不抛异常。

    Flags 位定义（Bluetooth SIG HRS 1.0）：
      bit0  Heart Rate Value Format   0 = UINT8，1 = UINT16（小端）
      bit1  Sensor Contact Detected   1 = 检测到接触
      bit2  Sensor Contact Supported 1 = 支持佩戴检测（bit1 此时才有意义）
      bit3  Energy Expended Present  后续 2 字节 UINT16，单位 kJ
      bit4  RR-Interval Present      后续每 2 字节一条 UINT16，单位 1/1024 秒

    小米手环 9 Pro / 10 的实测帧是 ``00 59``（flags=0，UINT8=89）。实现仍把
    UINT16、接触位、能量、RR 全部解出来，避免换固件后读错。
    """
    try:
        raw = bytes(data)
    except (TypeError, ValueError):
        return None
    if len(raw) < 2:
        return None

    flags = raw[0]
    pos = 1
    if flags & 0x01:                       # UINT16，小端
        if len(raw) < 3:
            return None
        bpm = raw[1] | (raw[2] << 8)
        pos = 3
    else:                                  # UINT8
        bpm = raw[1]
        pos = 2

    contact: bool | None = None
    if flags & 0x04:                       # 支持佩戴检测
        contact = bool(flags & 0x02)

    energy: int | None = None
    if flags & 0x08:
        if len(raw) < pos + 2:
            return None
        energy = raw[pos] | (raw[pos + 1] << 8)
        pos += 2

    rr: list[float] = []
    if flags & 0x10:
        while pos + 1 < len(raw):
            tick = raw[pos] | (raw[pos + 1] << 8)
            rr.append(round(tick * 1000.0 / 1024.0, 1))
            pos += 2

    return HRReading(bpm=bpm, contact=contact, energy=energy,
                     rr_ms=tuple(rr), flags=flags)


def describe_contact(reading: HRReading) -> str:
    """把三态接触位翻译成界面文案。"""
    if reading.contact is None:
        return "佩戴状态：未报告"
    return "佩戴状态：已佩戴" if reading.contact else "佩戴状态：未检测到佩戴"


def merge_targets(current: dict[str, str],
                  commands: list[tuple[str, str, str]]) -> dict[str, str]:
    """把 ``add`` / ``remove`` / ``clear`` 命令合并成目标表 ``{address: name}``。

    多设备集合的增删逻辑抽成纯函数，无 bleak / Qt 事件循环也能自测。
    注意 ``clear`` + ``add`` 出现在同一批命令时按**最终集合**比较，
    未变的设备不会被误重建（见 ``hr_worker.HeartRateHub._drain_pending``）。
    """
    result = dict(current)
    for action, address, name in commands:
        if action == "clear":
            result.clear()
        elif action == "add" and address:
            result[address] = name
        elif action == "remove":
            result.pop(address, None)
    return result


def device_label(info: dict) -> str:
    """设备列表里的一行文字。"""
    name = info.get("name") or "未命名设备"
    if is_xiaomi_10_pro(name):
        tag = "小米 10 Pro"
    elif info.get("xiaomi"):
        tag = "小米"
    else:
        tag = "标准心率"
    rssi = info.get("rssi")
    signal = f"{rssi} dBm" if isinstance(rssi, int) else "信号未知"
    return f"{name} · {tag} · {signal} · {info.get('address', '')}"
