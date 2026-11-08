"""PYZ 内省：确认「卡片浮起修复」已进打包产物。

递归找 main_window 里的 _sync_fullscreen_padding 函数对象，查它的 co_names
是否含新旗子 _pending_restore_fs（说明 full 判定已改用瞬时旗，而非把持久的
_want_fullscreen 直接并进 full）。
"""
import marshal
import sys
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

EXE = "dist/SerialAssistant.exe"
TARGET = "main_window"


def _walk(code):
    yield code
    for const in code.co_consts:
        if hasattr(const, "co_code"):
            yield from _walk(const)


def _find(code, name):
    for c in _walk(code):
        if c.co_name == name:
            return c
    return None


def main() -> int:
    car = CArchiveReader(EXE)
    data = car.extract("PYZ.pyz")
    tmp = "build/_pyz_probe.pyz"
    with open(tmp, "wb") as f:
        f.write(data)
    zar = ZlibArchiveReader(tmp)
    blob = zar.extract(TARGET)
    if isinstance(blob, bytes):
        blob = marshal.loads(blob)
    fn = _find(blob, "_sync_fullscreen_padding")
    if fn is None:
        print("FAIL: _sync_fullscreen_padding not found in", TARGET)
        return 1
    names = fn.co_names
    has_flag = "_pending_restore_fs" in names
    has_want = "_want_fullscreen" in names
    print(f"[pyz] _sync_fullscreen_padding co_names = {sorted(names)}")
    print(f"[pyz] contains _pending_restore_fs = {has_flag}")
    print(f"[pyz] contains _want_fullscreen  = {has_want}")
    # full 行现在应是 isFullScreen/isMaximized/_snap_zoom/_pending_restore_fs，
    # 不应再把 _want_fullscreen 直接并进 full。
    ok = has_flag and not has_want
    print("PASS" if ok else "FAIL", "- 卡片浮起修复已进入产物"
          if ok else " - full 行仍直接并 _want_fullscreen")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
