"""从已发布 exe 的 PYZ 里取出 splash_anim 模块，检查 _frames_for 是否还残留
`return None`（帧序列停用）的字节码。直接回答『部署包里动画到底动不动』。

用法：.venv/Scripts/python.exe devtools/_check_deployed_splash.py
"""
import dis
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "发布" / "SerialAssistant.exe"


def main() -> int:
    if not EXE.is_file():
        print(f"找不到已发布 exe: {EXE}")
        return 2
    # PyInstaller 的归档读取器
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    tmp = ROOT / "devtools" / "_check_pyz_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    pyz_path = tmp / "PYZ.pyz"
    pyz_data = None
    arc = CArchiveReader(str(EXE))
    # 本机 PyInstaller 的 arc.toc 直接产出条目名（字符串）
    for name in arc.toc:
        if name == "PYZ.pyz":
            pyz_data = arc.extract(name)
            break
    if pyz_data is None:
        print("PYZ.pyz 未在 exe 中找到")
        return 3
    pyz_path.write_bytes(pyz_data)

    z = ZlibArchiveReader(str(pyz_path))
    print("PYZ 内 splash_anim 相关条目：")
    for name in z.toc:
        if "splash" in name:
            print("   ", name)
    # 直接取 splash_anim 模块的 code 对象（ZlibArchiveReader.extract 已返回 code）
    code = z.extract("splash_anim")

    # 递归在 co_consts 里找名为 _frames_for 的函数 code 对象（不 exec 整个模块，
    # 避免顶层 import app_log 等依赖报 Missing）
    def find_code(co, name):
        if getattr(co, "co_name", None) == name:
            return co
        for c in getattr(co, "co_consts", ()):
            if hasattr(c, "co_consts"):
                r = find_code(c, name)
                if r is not None:
                    return r
        return None

    co = find_code(code, "_frames_for")
    if co is None:
        print("splash_anim 里没有 _frames_for 的 code 对象（结构异常）")
        return 4
    print(f"\n_frames_for 常量表 co_consts = {co.co_consts}")
    print("\n_frames_for 反汇编（看开头有没有 LOAD_CONST None / RETURN_VALUE）：")
    instrs = list(dis.get_instructions(co))
    for instr in instrs[:12]:
        print(f"  {instr.offset:4d}: {instr.opname} {instr.argval}")
    # 判定：函数体第一条有效指令就是 LOAD_CONST None + RETURN_VALUE（即 `return None` 停用）
    bad = False
    if len(instrs) >= 2:
        bad = (instrs[0].opname == "LOAD_CONST" and instrs[0].argval is None
               and instrs[1].opname == "RETURN_VALUE")
    print(f"\n判定：_frames_for 是否被 `return None` 停用 = {bad}")
    if bad:
        print("  ⚠️ 部署包里帧序列被停用 —— 必须重打包（这正是用户看到『动画不动』的根因）")
    else:
        print("  ✓ 部署包里 _frames_for 正常，帧序列路径可用 —— 问题在别处，勿盲目重打包")
    return 0


if __name__ == "__main__":
    sys.exit(main())
