# -*- coding: utf-8 -*-
"""探针：本机能不能用 bleak 扫到 BLE 广播（以及广播里带不带数据）。

这一步只回答两件事：
  1. 蓝牙无线电 + WinRT 这条路通不通（扫得到设备就通）；
  2. 广播包里到底有什么（name / rssi / manufacturer_data / service_data），
     决定"把数据塞进广播包"这条路线在这台机器上是否可行。
用法：
    python devtools\_probe_ble_scan.py [扫描秒数]
"""
import asyncio
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bleak import BleakScanner   # noqa: E402

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0


async def main() -> int:
    seen: dict[str, dict] = {}

    def callback(device, adv) -> None:
        item = seen.setdefault(device.address, {"n": 0})
        item["n"] += 1
        item["name"] = device.name or (adv.local_name or "")
        item["rssi"] = adv.rssi
        item["mfg"] = {k: v.hex() for k, v in (adv.manufacturer_data or {}).items()}
        item["svc"] = {str(k): (v.hex() if isinstance(v, (bytes, bytearray)) else str(v))
                       for k, v in (adv.service_data or {}).items()}

    print(f"扫描 {SECONDS:.0f} 秒 ...", flush=True)
    scanner = BleakScanner(detection_callback=callback, scanning_mode="passive")
    await scanner.start()
    await asyncio.sleep(SECONDS)
    await scanner.stop()

    print(f"共发现 {len(seen)} 个 BLE 设备", flush=True)
    for addr, info in list(seen.items())[:15]:
        print(f"  {addr}  name={info.get('name')!r}  rssi={info.get('rssi')}  "
              f"包数={info['n']}", flush=True)
        if info.get("mfg"):
            print(f"        manufacturer_data = {info['mfg']}", flush=True)
        if info.get("svc"):
            print(f"        service_data = {info['svc']}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:                      # noqa: BLE001
        print("扫描失败:", type(exc).__name__, exc)
        print("（可能是：本机没有蓝牙无线电 / 蓝牙被关闭 / 权限受限）")
        sys.exit(1)
