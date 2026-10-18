"""确认设置面板渐隐修复已打进 exe。

机理：旧构建的 ``_SettingsShot.set_alpha`` 走 ``painter.setOpacity``（顶层非分层窗口
上=压暗成黑）；新源码走 ``setWindowOpacity``（窗口级合成，真透出）。两路在字节码里
体现为名引用差异：旧有 ``painter`` / ``setOpacity``，新有 ``setWindowOpacity``。

做法：从构建产物 ``dist/SerialAssistant.exe`` 里抽 PYZ、再抽 ``main_window`` 模块，
递归 walk 其 ``co_names``，看 ``_SettingsShot.set_alpha`` 这个函数对象引用了哪个名。

用法：构建完成后
    .venv\\Scripts\\python.exe devtools/_verify_exe_fix.py [exe路径]
"""
from __future__ import annotations

import sys
import types

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader


def _walk_codes(co, path=""):
    """yield (qualname, code) for co and all nested code objects."""
    yield path, co
    for const in co.co_consts:
        if isinstance(const, types.CodeType):
            name = const.co_name
            yield from _walk_codes(const, f"{path}.{name}" if path else name)


def _module_codes(pyz_path: str, modname: str):
    z = ZlibArchiveReader(pyz_path)
    try:
        data = z.extract(modname)  # PyInstaller 直接吐 code 对象
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"PYZ 里没有 {modname!r}: {exc}")
    if isinstance(data, types.CodeType):
        return data
    # 老版本可能吐原始 pyc 字节：跳过 16 字节 pyc 头再 unmarshal
    import marshal
    return marshal.loads(bytes(data)[16:])


def main() -> int:
    exe = sys.argv[1] if len(sys.argv) > 1 else "dist/SerialAssistant.exe"
    ca = CArchiveReader(exe)
    # 找 PYZ：通常名为 'PYZ-00.pyz' 之类
    pyz_name = None
    pyz_data = None
    for name in ca.toc:
        nm = name[0] if isinstance(name, tuple) else name
        if isinstance(nm, str) and nm.startswith("PYZ"):
            pyz_name = nm
            pyz_data = ca.extract(nm)
            break
    if pyz_name is None:
        raise SystemExit("CArchive 里没找到 PYZ")
    import tempfile, os
    fd, tmp = tempfile.mkstemp(suffix=".pyz")
    os.write(fd, pyz_data)
    os.close(fd)
    try:
        code = _module_codes(tmp, "main_window")
    finally:
        os.remove(tmp)

    # 找 _SettingsShot.set_alpha 这个函数的 co_names
    found = {}
    for path, co in _walk_codes(code):
        if path.endswith("_SettingsShot.set_alpha") or path.endswith("set_alpha"):
            found[path] = set(co.co_names)

    if not found:
        print("✗ 没在 main_window 里定位到 set_alpha")
        return 1

    ok = True
    for path, names in found.items():
        has_window_op = "setWindowOpacity" in names
        has_painter = "painter" in names
        has_setopacity_attr = "setOpacity" in names
        # 新修复：引用 setWindowOpacity，且不再用 painter.setOpacity
        fixed = has_window_op and not (has_painter and has_setopacity_attr)
        status = "✓ 已修复(setWindowOpacity)" if fixed else "✗ 仍是旧实现(painter.setOpacity)"
        if not fixed:
            ok = False
        print(f"{status}  [{path}]")
        print(f"    co_names 关键项: setWindowOpacity={has_window_op} "
              f"painter={has_painter} setOpacity={has_setopacity_attr}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
