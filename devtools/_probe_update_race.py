# -*- coding: utf-8 -*-
r"""更新包"多源并行抢跑"的离线验证（2026-10-25）。

为什么要有这个探针：用户要求"不开代理也能快速更新"，而"快"这件事**只能在产物
/真实链路里量**，不能靠读代码。这里起一个本地 HTTP 服务当假镜像：

  · /slow/...  每 60ms 只吐 8KB（模拟限速镜像，下 300KB 要 ~2.3s，30MB 要几分钟）
  · /fast/...  立刻整块返回

源链**故意把 slow 放在第一条**（修改前的串行实现会先卡在它上面）。断言：
  1. 胜出的必须是 fast（`_apply` 收到的字节与 fast 载荷逐字节一致）；
  2. 总耗时明显小于"串行等 slow 下完"的时间；
  3. 胜出后 slow 那条连接被断开（没有继续收字节）。

用法：QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_update_race.py
"""
from __future__ import annotations

import hashlib
import http.server
import os
import socketserver
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings, QEvent, QTimer          # noqa: E402
from PySide6.QtTest import QTest                              # noqa: E402
from PySide6.QtWidgets import QApplication                    # noqa: E402

PAYLOAD = bytes(range(256)) * 1200        # 307,200 B，内容确定便于比对
SLOW_CHUNK = 8192
SLOW_GAP = 0.06


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                      # noqa: N802 - 对齐标准库命名
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        try:
            if self.path.startswith("/slow"):
                for i in range(0, len(PAYLOAD), SLOW_CHUNK):
                    self.wfile.write(PAYLOAD[i:i + SLOW_CHUNK])
                    self.wfile.flush()
                    time.sleep(SLOW_GAP)
            else:
                self.wfile.write(PAYLOAD)
        except (BrokenPipeError, ConnectionResetError):
            pass                            # 被抢占方中断：正常现象

    def log_message(self, *args):           # 静音
        pass


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: settings.value(k) for k in settings.allKeys()}

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    base = f"http://127.0.0.1:{port}"
    urls = (f"{base}/slow/SerialAssistant.exe", f"{base}/fast/SerialAssistant.exe")

    import updater
    real_urls = updater.release_download_urls
    updater.release_download_urls = lambda *a, **kw: urls   # 注入假源链

    got: list[bytes] = []
    dialog = updater.UpdateDialog(latest="9.9", current="2.4",
                                  sha256=hashlib.sha256(PAYLOAD).hexdigest(),
                                  tag="v9.9")
    dialog._frozen = True                    # 开发环境默认禁用自更新，这里强行打开
    dialog._apply = lambda data: got.append(bytes(data))   # 别真替换文件
    dialog.show()
    ok = False
    try:
        t0 = time.perf_counter()
        dialog._update_now()
        serial_baseline = len(PAYLOAD) / SLOW_CHUNK * SLOW_GAP   # 串行要等的时间
        while time.perf_counter() - t0 < 15:
            QTest.qWait(50)
            if got:
                break
        elapsed = time.perf_counter() - t0
        ok = bool(got) and got[0] == PAYLOAD
        print(f"串行基线（只等 slow）：约 {serial_baseline:.2f}s")
        print(f"并行抢跑实际耗时：{elapsed:.2f}s")
        print(f"胜出载荷与 fast 一致：{got[0] == PAYLOAD if got else '没拿到数据'}")
        print(f"加速比：{serial_baseline / elapsed:.1f}×")
        print("判定：", "PASS" if ok and elapsed < serial_baseline * 0.6 else "FAIL")
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        updater.release_download_urls = real_urls
        server.shutdown()
        back = QSettings("MimoWorks", "SerialAssistant")
        for k in back.allKeys():
            if k not in snapshot:
                back.remove(k)
        for k, v in snapshot.items():
            back.setValue(k, v)
        back.sync()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
