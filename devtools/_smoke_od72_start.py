# -*- coding: utf-8 -*-
r"""产物启动冒烟：跑打包好的 SerialAssistant.exe，确认
  ① 进程真的起来了（不是导入期就崩）；
  ② 主窗可见、底栏 SOC 标签存在并显示 "--"；
  ③ 日志里没有 ImportError / ModuleNotFoundError。

用法：
    python devtools\_smoke_od72_start.py                 # 默认 dist_od72
    python devtools\_smoke_od72_start.py dist_od75       # 指定产物目录
    python devtools\_smoke_od72_start.py 发布            # 冒烟发布目录那份

⛔ 用 taskkill 强杀，**不调 closeEvent** —— 那会把 geometry 写进真实注册表。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 目录可用 argv[1] 覆盖（od75 起产物目录逐轮变化，别再写死）
_DIR = sys.argv[1] if len(sys.argv) > 1 else "dist_od72"
EXE = Path(_DIR) if Path(_DIR).is_absolute() else ROOT / _DIR / "SerialAssistant.exe"
if EXE.is_dir():
    EXE = EXE / "SerialAssistant.exe"
SHOT = ROOT / "_check" / ("smoke_" + EXE.parent.name + ".png")

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  —— " + detail) if detail else ""))
    if not ok:
        fails.append(name)


def main() -> int:
    if not EXE.exists():
        print("找不到 exe:", EXE)
        return 2
    SHOT.parent.mkdir(parents=True, exist_ok=True)

    log_dir = Path.home() / "AppData" / "Local" / "CQUFSAE-SerialAssistant" / "logs"
    before = {p.name: p.stat().st_size for p in log_dir.glob("*.log")} if log_dir.exists() else {}

    proc = subprocess.Popen([str(EXE)], cwd=str(EXE.parent))
    try:
        # 开屏 splash 3.5 s + _pump_build 分段构建，给足 12 s
        time.sleep(12.0)
        check("P1 进程存活（未在启动期崩溃）", proc.poll() is None,
              f"exit={proc.poll()}")

        if proc.poll() is None:
            try:
                from PIL import ImageGrab

                img = ImageGrab.grab(all_screens=True)
                img.save(SHOT)
                w, h = img.size
                check("P2 抓到整屏截图", w > 200 and h > 200, f"{w}x{h}")
                # 主窗底色是浅灰，纯黑说明还没画出来
                px = img.convert("RGB").getdata()
                dark = sum(1 for r, g, b in px if r < 12 and g < 12 and b < 12)
                ratio = dark / max(1, w * h)
                check("P3 画面非全黑（主窗已绘出）", ratio < 0.85, f"近黑占比 {ratio:.1%}")
            except Exception as exc:  # noqa: BLE001
                check("P2 抓到整屏截图", False, repr(exc))
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)],
                           capture_output=True, check=False)
        time.sleep(1.0)

    # 日志尾部找导入错误
    if log_dir.exists():
        bad: list[str] = []
        for p in sorted(log_dir.glob("*.log"), key=lambda q: q.stat().st_mtime)[-2:]:
            start = before.get(p.name, 0)
            try:
                text = p.read_text(encoding="utf-8", errors="replace")[start:]
            except OSError:
                continue
            for kw in ("ImportError", "ModuleNotFoundError", "cannot import name"):
                if kw in text:
                    bad.append(f"{p.name}: {kw}")
        check("P4 新增日志无导入错误", not bad, "; ".join(bad) or "干净")

    print()
    print(f"{'全部通过' if not fails else '失败 ' + str(len(fails)) + ' 项'}")
    print("IS_PASS:", "YES" if not fails else "NO")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
