# -*- coding: utf-8 -*-
"""验证打包产物里的图标资产确实是新的。

两件事都要查，缺一不可：
  1) **归档内**的 assets/app.ico —— 运行时 QIcon 用它（窗口/任务栏图标）
  2) **exe 资源段**里的图标 —— 资源管理器显示的那个（PyInstaller --icon）

用法：python devtools/_verify_icon_in_exe.py dist_odXX\SerialAssistant.exe
"""
from __future__ import annotations

import hashlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


def sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def check_archive(exe: str, expect: str) -> bool:
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(exe)
    names = [n for n in archive.toc if n.replace("\\", "/").endswith("assets/app.ico")]
    if not names:
        print("  FAIL 归档里没有 assets/app.ico")
        return False
    ok = True
    for name in names:
        data = archive.extract(name)
        digest = hashlib.sha256(data).hexdigest()
        same = digest == expect
        ok &= same
        print(f"  归档 {name}: {len(data)} bytes  {'OK 与新 ico 一致' if same else 'FAIL 是旧图'}")
    return ok


def check_resources(exe: str) -> bool:
    """用 .NET 的 ExtractAssociatedIcon 取 exe 自带的图标，存 PNG 供核对。"""
    import subprocess
    out = os.path.join(_ROOT, "_check", "icon", "exe_icon.png")
    ps = (
        "Add-Type -AssemblyName System.Drawing;"
        f"$i=[System.Drawing.Icon]::ExtractAssociatedIcon('{exe}');"
        f"$i.ToBitmap().Save('{out}',[System.Drawing.Imaging.ImageFormat]::Png);"
        f"Write-Output $i.Width"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  FAIL 取 exe 图标失败: {r.stderr.strip()[:200]}")
        return False
    from PIL import Image
    img = Image.open(out).convert("RGBA")
    red = sum(1 for p in img.getdata() if p[0] > 110 and p[1] < 95 and p[2] < 95)
    white_art = sum(1 for p in img.getdata() if min(p[:3]) > 200 and p[3] > 200)
    print(f"  exe 资源图标 {img.size} -> {out}")
    print(f"  偏红像素 {red}，纯白像素 {white_art}")
    # 白闪电版本时偏红像素必然是 0
    return red > 0


def main(exe: str) -> int:
    expect = sha256(os.path.join(_ROOT, "assets", "app.ico"))
    print(f"源 assets/app.ico sha256={expect[:16]}…")
    ok = check_archive(exe, expect)
    ok &= check_resources(exe)
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
