"""确认「打开设置最后一帧闪」(DWM 首帧白底)修复已打进 exe。

机理：旧源码在 opening 分支用 96ms 渐隐 + ``_start_settings_swap_fade`` /
``_fade_settings_step`` / ``_SETTINGS_FADE_MS`` 去盖白帧；新源码改为
``QTimer.singleShot(50, lambda: self._finish_settings_swap(dialog))`` 延迟一拍
硬切，并新增 ``_finish_settings_swap`` / 复用 ``_dwm_flush``。

验证：从 ``dist/SerialAssistant.exe`` 抽 PYZ → 抽 ``main_window`` 模块 →
递归收集所有 code 对象的 ``co_name`` 与所有 ``co_names`` 引用名。

- 旧死符号（函数名 / 常量名）应当**消失**：``_start_settings_swap_fade``、
  ``_fade_settings_step``、``_SETTINGS_FADE_MS``
- 新符号应当**存在**：``_finish_settings_swap``（code 对象名）、
  ``_dwm_flush``（code 对象名）、``singleShot``（co_names 引用）、
  ``_dwm_flush`` 还会出现在 opening 分支的 co_names 里

用法：构建完成后
    .venv\\Scripts\\python.exe devtools/_verify_exe_openfix.py [exe路径]
"""
from __future__ import annotations

import marshal
import sys
import tempfile
import types

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader


def _collect_code_names(co, path="", out=None):
    """递归收集所有嵌套 code 对象的 co_name，以及所有 co_names 引用名。

    返回 (set_of_defined_names, set_of_referenced_names)。
    defined_names 来自每个 code 对象的 co_name；referenced_names 来自 co_names。
    """
    if out is None:
        out = (set(), set())
    defined, referenced = out
    defined.add(co.co_name)
    referenced.update(co.co_names)
    for const in co.co_consts:
        if isinstance(const, types.CodeType):
            _collect_code_names(const, f"{path}.{const.co_name}", out)
    return out


def _find_code(co, name):
    """递归找 co_name == name 的 code 对象。"""
    if co.co_name == name:
        return co
    for const in co.co_consts:
        if isinstance(const, types.CodeType):
            got = _find_code(const, name)
            if got is not None:
                return got
    return None


def _module_code(pyz_path: str, modname: str):
    z = ZlibArchiveReader(pyz_path)
    try:
        data = z.extract(modname)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"PYZ 里没有 {modname!r}: {exc}")
    if isinstance(data, types.CodeType):
        return data
    return marshal.loads(bytes(data)[16:])


def main() -> int:
    exe = sys.argv[1] if len(sys.argv) > 1 else "dist/SerialAssistant.exe"
    ca = CArchiveReader(exe)
    pyz_name = None
    pyz_data = None
    for entry in ca.toc:
        nm = entry[0] if isinstance(entry, tuple) else entry
        if isinstance(nm, str) and nm.startswith("PYZ"):
            pyz_name = nm
            pyz_data = ca.extract(nm)
            break
    if pyz_name is None:
        raise SystemExit("CArchive 里没找到 PYZ")
    fd, tmp = tempfile.mkstemp(suffix=".pyz")
    import os
    os.write(fd, pyz_data)
    os.close(fd)
    try:
        code = _module_code(tmp, "main_window")
    finally:
        os.remove(tmp)

    defined, referenced = _collect_code_names(code)

    # 旧死符号：应消失
    dead_funcs = ["_start_settings_swap_fade", "_fade_settings_step"]
    dead_consts = ["_SETTINGS_FADE_MS"]
    # 新符号：应存在
    new_funcs = ["_finish_settings_swap", "_dwm_flush"]
    new_refs = ["singleShot"]

    print(f"== 抽取模块 main_window 自检 (exe={exe}) ==")
    ok = True

    print("-- 应消失的死符号 --")
    for name in dead_funcs:
        present = name in defined
        print(f"  [{'✗ 还在' if present else '✓ 已删'}] code 对象 {name}")
        if present:
            ok = False
    for name in dead_consts:
        # 常量可能出现在 co_names 引用里（旧 _SETTINGS_FADE_MS 用到的地方）
        present = name in referenced
        print(f"  [{'✗ 还在' if present else '✓ 已删'}] 引用 {name}")
        if present:
            ok = False

    print("-- 应存在的新符号 --")
    for name in new_funcs:
        present = name in defined
        print(f"  [{'✓ 有' if present else '✗ 缺'}] code 对象 {name}")
        if not present:
            ok = False
    for name in new_refs:
        present = name in referenced
        print(f"  [{'✓ 有' if present else '✗ 缺'}] 引用 {name}")
        if not present:
            ok = False

    print("-- 裁剪符号修复（_grab_settings_frame）--")
    grab = _find_code(code, "_grab_settings_frame")
    if grab is None:
        print("  ✗ 没找到 _grab_settings_frame")
        ok = False
    else:
        varnames = set(grab.co_varnames)
        # 旧实现局部变量 left/top；新实现改成 crop_x/crop_y 且不再用 left/top
        old_markers = {"left", "top"} & varnames
        new_marker = {"crop_x", "crop_y"} <= varnames
        if old_markers:
            print(f"  ✗ 仍是旧实现（局部变量含 {sorted(old_markers)}）")
            ok = False
        elif new_marker:
            print("  ✓ 已是新实现（局部变量 crop_x/crop_y，无 left/top）")
        else:
            print(f"  ✗ 无法判定，co_varnames={sorted(varnames)}")
            ok = False

    if ok:
        print("== 结论：打开设置最后一帧闪修复已正确打进 exe ✓ ==")
    else:
        print("== 结论：exe 内仍是旧实现，需要重建 ⚠ ==")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
