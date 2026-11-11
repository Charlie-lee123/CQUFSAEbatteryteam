# -*- coding: utf-8 -*-
"""heartbeatDLC 无硬件自测：协议解析 + 清单完整性。

    python -m heartbeatDLC --selftest
    .venv/Scripts/python.exe heartbeatDLC/selftest.py   （需把项目根放进 sys.path）

不需要蓝牙适配器，也不需要图形环境。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def run() -> int:
    try:
        from .hr_protocol import (
            BAND10PRO_HINT, BPM_MAX, HRS_UUID, decode_battery,
            decode_hr_measurement, decode_sensor_location, device_label,
            has_hrs, is_hr_candidate, is_xiaomi_10_pro, is_xiaomi_band,
            merge_targets, normalize_uuid, plausible_bpm,
        )
    except ImportError:                       # 直接以脚本方式运行
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from heartbeatDLC.hr_protocol import (
            BAND10PRO_HINT, BPM_MAX, HRS_UUID, decode_battery,
            decode_hr_measurement, decode_sensor_location, device_label,
            has_hrs, is_hr_candidate, is_xiaomi_10_pro, is_xiaomi_band,
            merge_targets, normalize_uuid, plausible_bpm,
        )

    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        checks.append((name, bool(ok)))

    # 1. 小米手环 9 Pro / 10 实测帧：00 59 = 89 bpm
    r = decode_hr_measurement(bytes([0x00, 0x59]))
    check("标准 UINT8 帧 00 59 → 89 bpm", r is not None and r.bpm == 89
          and r.contact is None and r.rr_ms == ())
    check("实测 00 5A / 00 57 / 00 58", [
        decode_hr_measurement(bytes([0x00, v])).bpm for v in (0x5A, 0x57, 0x58)
    ] == [90, 87, 88])

    # 2. UINT16 小端：flags=0x01, 0x003C = 60
    r = decode_hr_measurement(bytes([0x01, 0x3C, 0x00]))
    check("UINT16 小端帧 → 60 bpm", r is not None and r.bpm == 60)
    r = decode_hr_measurement(bytes([0x01, 0x10, 0x01]))
    check("UINT16 >255 帧 → 272 bpm", r is not None and r.bpm == 272)

    # 3. 佩戴检测三态：bit2=supported / bit1=detected
    r = decode_hr_measurement(bytes([0x06, 0x4B]))
    check("接触位 supported+detected → True", r is not None and r.contact is True)
    r = decode_hr_measurement(bytes([0x04, 0x4B]))
    check("接触位 supported+not detected → False", r is not None and r.contact is False)
    r = decode_hr_measurement(bytes([0x02, 0x4B]))
    check("无 supported 位 → contact=None", r is not None and r.contact is None)

    # 4. 能量消耗与 RR 间期（1024 tick = 1000 ms）
    r = decode_hr_measurement(bytes([0x08, 0x4B, 0x05, 0x00]))
    check("Energy Expended → 5 kJ", r is not None and r.energy == 5)
    r = decode_hr_measurement(bytes([0x10, 0x4B, 0x00, 0x04, 0x00, 0x02]))
    check("RR 间期 → 1000.0 / 500.0 ms", r is not None
          and r.rr_ms == (1000.0, 500.0))

    # 5. 非法帧必须返回 None，不抛异常
    check("空帧 → None", decode_hr_measurement(b"") is None)
    check("单字节帧 → None", decode_hr_measurement(b"\x00") is None)
    check("UINT16 缺字节 → None", decode_hr_measurement(b"\x01\x3c") is None)
    check("Energy 缺字节 → None", decode_hr_measurement(b"\x08\x4b\x05") is None)
    check("bytearray 可解", decode_hr_measurement(bytearray(b"\x00\x4b")) is not None)

    # 6. UUID 归一化与设备过滤
    check("16 位 180d 归一", normalize_uuid("180D") == HRS_UUID)
    check("完整 UUID 归一", normalize_uuid(HRS_UUID.upper()) == HRS_UUID)
    check("花括号 UUID 归一", normalize_uuid("{" + HRS_UUID + "}") == HRS_UUID)
    check("含 180D 的服务列表", has_hrs(["0000fe95-0000-1000-8000-00805f9b34fb",
                                        HRS_UUID]))
    check("不含 180D 的服务列表", not has_hrs(["0000fe95-0000-1000-8000-00805f9b34fb"]))
    check("空服务列表", not has_hrs([]) and not has_hrs(None))

    # 7. 小米设备名标注（只影响 UI，不影响能否连接）
    check("Xiaomi Smart Band 9 Pro 被识别", is_xiaomi_band("Xiaomi Smart Band 9 Pro"))
    check("Mi Band 10 被识别", is_xiaomi_band("Mi Band 10"))
    check("普通心率带不被误标", not is_xiaomi_band("Polar H10"))
    check("设备行文案", "小米" in device_label(
        {"name": "Xiaomi Smart Band 10", "address": "AA:BB", "rssi": -60,
         "xiaomi": True}))

    # 8. 可信区间
    check("30 / 220 为边界内", plausible_bpm(30) and plausible_bpm(220))
    check("0 / 255 为边界外", not plausible_bpm(0) and not plausible_bpm(255))
    check("BPM_MAX 常量", BPM_MAX == 220)

    # 9. 小米手环 10 Pro（含陶瓷特别版）专项兼容
    check("10 Pro 设备名被识别", is_xiaomi_10_pro("Xiaomi Smart Band 10 Pro"))
    check("10 Pro 陶瓷版设备名被识别",
          is_xiaomi_10_pro("Xiaomi Smart Band 10 Pro Ceramic Edition"))
    check("9 Pro 不被误认成 10 Pro", not is_xiaomi_10_pro("Xiaomi Smart Band 9 Pro"))
    check("广播声明 0x180D → 候选且已声明",
          is_hr_candidate([HRS_UUID], "Polar H10") == (True, True))
    check("10 Pro 未声明 0x180D → 名称兜底为候选",
          is_hr_candidate([], "Xiaomi Smart Band 10 Pro") == (True, False))
    check("普通设备未声明 0x180D → 不列入",
          is_hr_candidate([], "Polar H10") == (False, False))
    check("10 Pro 在列表里被标注", "小米 10 Pro" in device_label(
        {"name": "Xiaomi Smart Band 10 Pro", "address": "AA:BB", "rssi": -60,
         "xiaomi": True}))
    check("10 Pro 操作提示含关键约束",
          all(word in BAND10PRO_HINT for word in ("表盘", "配对", "手机", "广播")))

    # 10. 标准电池 / 佩戴位置特征（10 Pro 的 GATT 里带这两项）
    check("Battery Level 0x54 → 84%", decode_battery(b"\x54") == 84)
    check("Battery Level 空/越界 → None",
          decode_battery(b"") is None and decode_battery(b"\xff") is None)
    check("Sensor Location 0x02 → 手腕", decode_sensor_location(b"\x02") == "手腕")
    check("Sensor Location 越界 → None", decode_sensor_location(b"\x63") is None)

    # 11. 多设备目标集合：add / remove / clear / 同批替换
    check("两台设备同时加入", merge_targets(
        {}, [("add", "A", "手环甲"), ("add", "B", "手环乙")])
        == {"A": "手环甲", "B": "手环乙"})
    check("移除其中一台不影响另一台", merge_targets(
        {"A": "手环甲", "B": "手环乙"}, [("remove", "A", "")]) == {"B": "手环乙"})
    check("clear 清空全部", merge_targets(
        {"A": "甲", "B": "乙"}, [("clear", "", "")]) == {})
    check("重复 add 覆盖名称", merge_targets(
        {"A": "甲"}, [("add", "A", "甲-新")]) == {"A": "甲-新"})
    check("clear+add 同批按最终集合比较", merge_targets(
        {"A": "甲", "B": "乙"},
        [("clear", "", ""), ("add", "A", "甲"), ("add", "B", "乙")])
        == {"A": "甲", "B": "乙"})
    check("非法地址被忽略", merge_targets({}, [("add", "", "空")]) == {})

    # 12. 清单字段
    manifest = json.loads((Path(__file__).resolve().parent / "manifest.json").read_text("utf-8"))
    check("manifest.id", manifest.get("id") == "heartbeatDLC")
    check("manifest.api", manifest.get("api") == 1)
    check("manifest.entry", manifest.get("entry") == "heartbeatDLC")
    check("manifest.min_app 非空", bool(manifest.get("min_app")))

    # 13. 可视化模块（需要 Qt；无图形环境则跳过，不计入失败）
    skipped: list[str] = []
    try:
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication(["heartbeatDLC-selftest"])
        from .hr_visual import (
            HeartVisualWidget, PARTICLE_COUNT, QRS_TEMPLATE,
        )

        widget = HeartVisualWidget()
        widget.resize(400, 300)
        widget.set_bpm(72)
        widget.pulse(72)                        # 触发脉冲（pulse=1.0）
        widget.set_connected(True)
        widget._tick()                          # 推进一帧动画 + ECG（pulse 会衰减）
        check("可视化构造 + 心跳注入 bpm/pulse",
              widget._bpm == 72 and widget._pulse > 0.0)
        check("QRS 模板 28 点", len(QRS_TEMPLATE) == 28)
        check("粒子数 > 0", PARTICLE_COUNT > 0)
        widget.set_connected(False)             # 覆盖断开路径
        img = QImage(400, 300, QImage.Format.Format_RGB32)
        img.fill(0)
        widget.render(img)                      # 离屏渲染一帧（QPaintDevice 重载）
        check("离屏渲染一帧不抛异常", True)
    except Exception as exc:                    # noqa: BLE001 - 无 Qt/无显示时跳过
        skipped.append(f"可视化模块（无 Qt/无显示）：{exc}")

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if skipped:
        for item in skipped:
            print(f"  SKIP  {item}")
    print(f"\nheartbeatDLC 自测：{len(checks) - len(failed)}/{len(checks)} 通过"
          + (f"（跳过 {len(skipped)} 项）" if skipped else ""))
    if failed:
        for name in failed:
            print(f"  ✗ {name}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
