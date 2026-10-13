# -*- coding: utf-8 -*-
r"""把打包好的 exe 做成 Inno Setup 安装包（第 5 项）。

流程：
  1. 检查 `发布\SerialAssistant.exe` 存在（没有就先跑 build.bat）；
  2. 从 `version.py` 现读版本号（唯一出处，不许硬编码）；
  3. 找到 iscc.exe（Inno Setup 6 编译器）—— PATH → 常见安装目录；
  4. 调 `iscc /DAppVersion=x.y installer\SerialAssistant.iss`；
  5. 报告产物路径与大小。

用法：
    python devtools\_build_installer.py            # 正常编译
    python devtools\_build_installer.py --check    # 只体检（找 iscc / 查 exe），不编译

注意：本机若没装 Inno Setup，脚本会给出中文指引而不是甩一堆报错 ——
安装器源码在仓库里，谁装了编译器谁就能出包。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXE = ROOT / "发布" / "SerialAssistant.exe"
ISS = ROOT / "installer" / "SerialAssistant.iss"
OUT_DIR = ROOT / "发布"

# iscc.exe 常见落点（含 per-user 安装）
ISCC_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"),
    os.path.expandvars(r"%ProgramFiles%\Inno Setup 6\ISCC.exe"),
)


def find_iscc() -> str | None:
    """定位 Inno Setup 6 编译器：先 PATH，再常见安装目录，最后扫各盘根目录。

    最后一档是必要的：本机就是手动装到 `F:\\Inno Setup 6\\`（非标准路径），
    只查 %ProgramFiles% 那几处会漏掉。
    """
    import shutil
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found
    for path in ISCC_CANDIDATES:
        if path and os.path.exists(path):
            return path
    # 扫各盘根下的 *Inno Setup*/ISCC.exe（含大小写变体与版本号后缀）
    for drive in _drives():
        try:
            for entry in Path(drive + os.sep).iterdir():
                name = entry.name.lower()
                if not entry.is_dir() or "inno" not in name:
                    continue
                candidate = entry / "ISCC.exe"
                if candidate.exists():
                    return str(candidate)
        except (OSError, PermissionError):
            continue
    return None


def _drives() -> list[str]:
    """列出本机可用的盘符（C: / D: / F: ...）。"""
    import string
    return [f"{letter}:" for letter in string.ascii_uppercase if os.path.exists(f"{letter}:\\")]


def read_version() -> str:
    from version import VERSION
    return VERSION


def main() -> int:
    check_only = "--check" in sys.argv
    version = read_version()
    print(f"版本号（来自 version.py）：{version}")

    missing = []
    if not EXE.exists():
        missing.append(f"  ✗ 待打包的 exe 不存在：{EXE}")
    else:
        print(f"  ✓ exe 就绪：{EXE}  ({EXE.stat().st_size:,} B)")
    if not ISS.exists():
        missing.append(f"  ✗ 安装脚本不存在：{ISS}")
    else:
        print(f"  ✓ 安装脚本就绪：{ISS}")

    iscc = find_iscc()
    if iscc:
        print(f"  ✓ Inno Setup 编译器：{iscc}")
    else:
        print("  ✗ 没找到 Inno Setup 6 编译器 (ISCC.exe)")
        print("     安装：https://jrsoftware.org/isdl.php （选 Inno Setup 6）")
        print("     装好后重跑本脚本即可；安装器源码已在仓库里，不需要改代码。")

    if missing:
        print("\n先解决以下问题：")
        print("\n".join(missing))
        print("提示：先在项目根跑 build.bat（或 PyInstaller 打包）生成 exe。")
        return 1

    if check_only:
        print("\n--check 模式：体检结束，未编译。")
        return 0 if iscc else 1

    if not iscc:
        print("\n缺编译器，无法出包。装完 Inno Setup 6 再跑一次。")
        return 1

    cmd = [iscc, f"/DAppVersion={version}", str(ISS)]
    print("\n编译：" + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"\n编译失败（iscc 退出码 {result.returncode}）")
        return result.returncode

    produced = sorted(OUT_DIR.glob(f"SerialAssistant-setup-{version}*.exe"))
    print("\n编译完成：")
    for path in produced:
        print(f"  → {path}  ({path.stat().st_size:,} B)")
    if not produced:
        print(f"  ⚠ 没在 {OUT_DIR} 找到 setup 产物，检查 iss 的 OutputDir/OutputBaseFilename")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
