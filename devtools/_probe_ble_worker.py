# -*- coding: utf-8 -*-
r"""探针：实机验证 BleBroadcastWorker（走 Qt 信号那条真实路径）。

用法：
    python devtools\_probe_ble_worker.py [秒数] [目标地址或名字片段] [source] [key]
不带目标时只列表（验证 found 信号）；给了目标就顺带验证 received。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication    # noqa: E402

from ble_io import BleBroadcastWorker         # noqa: E402

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
TARGET = sys.argv[2] if len(sys.argv) > 2 else ""
SOURCE = sys.argv[3] if len(sys.argv) > 3 else "manufacturer"
KEY = sys.argv[4] if len(sys.argv) > 4 else ""

app = QApplication(sys.argv)

devices: dict[str, dict] = {}
payloads: list[bytes] = []

worker = BleBroadcastWorker(TARGET, source=SOURCE, key=KEY)
worker.found.connect(lambda info: devices.update({info["address"]: info}))
worker.received.connect(payloads.append)
worker.opened.connect(lambda: print("opened ✓ 扫描已开始", flush=True))
worker.open_failed.connect(lambda msg: print("open_failed ✗", msg, flush=True))
worker.failed.connect(lambda msg: print("failed ✗", msg, flush=True))

worker.open()          # ⚠️ 别忘了这一句 —— 探针第一版就是漏了它，
#                        表现是"0 个设备"，看着像蓝牙坏了，其实是根本没开扫。

import time   # noqa: E402

end = time.time() + SECONDS
while time.time() < end:
    app.processEvents()
    time.sleep(0.02)

worker.stop()
print(f"扫描 {SECONDS:.0f}s：发现 {len(devices)} 个设备，收到 {len(payloads)} 个负载",
      flush=True)
for addr, info in list(devices.items())[:12]:
    payload = info.get("payload")
    tail = f"  payload={payload.hex()}" if payload else ""
    print(f"  {addr}  name={info['name']!r} rssi={info['rssi']} 包间隔≈{1.0:.0f}{tail}",
          flush=True)
if payloads:
    print("前几个负载:", [p.hex()[:60] for p in payloads[:5]], flush=True)
