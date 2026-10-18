"""桌面 ALIVE 监视器（QA 自用，触发像素项 seam-to-seam 开跑）。

只做一件事：按 ``_desktop_gate`` 的门禁三判据轮询，一旦 ALIVE 立即打印三个数
并 ``exit 0``；超过预算 ``exit 1``。**不跑任何像素判据、不碰业务代码**——
真正的判据由各探针自己在起手/收尾（以及每次抓屏前）重核，本脚本 flicker 触达
只当发令枪，数据可信度由探针的门禁负责。

用法（项目根目录）::

    .venv/Scripts/python.exe devtools/_qa_desktop_watch.py [间隔秒] [次数]

默认 15s × 120（30 分钟预算）。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _desktop_gate import (MIN_MAX_LUMA, MIN_NONZERO_PCT,  # noqa: E402
                            MIN_UNIQ_COLORS, desktop_metrics)


def main() -> int:
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    attempts = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    for i in range(1, attempts + 1):
        luma, pct, colors, w, h = desktop_metrics()
        alive = (luma > MIN_MAX_LUMA and pct > MIN_NONZERO_PCT
                 and colors > MIN_UNIQ_COLORS)
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] #{i}/{attempts} {w}x{h} max_luma={luma} "
              f"nonzero={pct:.2f}% uniq_colors={colors} "
              f"{'ALIVE → 开跑' if alive else 'BLACK'}", flush=True)
        if alive:
            return 0
        if i < attempts:
            time.sleep(interval)
    print(f"[watch] {attempts} 次轮询（{interval:.0f}s 间隔）未见 ALIVE，退出",
          flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
