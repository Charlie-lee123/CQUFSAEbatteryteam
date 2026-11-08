"""分屏修复的打包产物校验。

目的：确认本次对 main_window.py 的三处改动**真进了 exe**，而不是只改了源码。

为什么用 co_names 而不是字符串常量：
  本次改动全部复用已有标识符（_snap_zoom / _rx_opts_row / _sync_fullscreen_padding
  本来就存在于模块里），没有新增任何字符串字面量（只是改注释和逻辑），
  所以无法用"新字符串"做指纹。改用**函数级 co_names 差异**作为确定性证据：

  1) 原函数 `_apply_snap_zoom` 里**从不调用** `_sync_fullscreen_padding`；
     改后它在末尾调用了 → 该函数的 co_names 会新增 `_sync_fullscreen_padding`。
  2) 原函数 `_sync_fullscreen_padding` 里**从不引用** `_snap_zoom`；
     改后 `full = ... or self._snap_zoom` → 该函数的 co_names 会新增 `_snap_zoom`。

只要这两点同时在 PYZ 里的 main_window 模块成立，就证明新代码进了打包产物。
"""
import sys
import types

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader


def collect_codes(co, out):
    out.append(co)
    for const in co.co_consts:
        if isinstance(const, types.CodeType):
            collect_codes(const, out)


def module_codes(mod_code):
    codes = []
    collect_codes(mod_code, codes)
    return codes


def main() -> int:
    exe_path = sys.argv[1] if len(sys.argv) > 1 else None
    if exe_path is None:
        for cand in ("dist/SerialAssistant/SerialAssistant.exe",
                     "dist/SerialAssistant.exe",
                     "发布/SerialAssistant.exe"):
            try:
                with open(cand, "rb"):
                    exe_path = cand
                    break
            except OSError:
                continue
    if exe_path is None:
        print("找不到待校验的 exe（dist/ 或 发布/ 下）")
        return 2

    print(f"校验目标: {exe_path}")
    car = CArchiveReader(exe_path)
    # 取出 PYZ 归档（car.toc 在不同 PyInstaller 版本里可能是 list 也可能是 dict）
    toc_keys = car.toc.keys() if isinstance(car.toc, dict) else [it[0] for it in car.toc]
    pyz_name = None
    if "PYZ.pyz" in toc_keys:
        pyz_name = "PYZ.pyz"
    else:
        for k in toc_keys:
            if k == "PYZ" or str(k).endswith("PYZ.pyz"):
                pyz_name = k
                break
    if pyz_name is None:
        print("CArchive 里没找到 PYZ.pyz")
        return 2
    print(f"PYZ 条目: {pyz_name}")
    # CArchiveReader.extract 返回的是**字节流**（不是路径）；ZlibArchiveReader 要吃
    # 文件路径，所以先落盘到临时文件再读。
    import os
    import tempfile
    pyz_bytes = car.extract(pyz_name)
    if isinstance(pyz_bytes, tuple):  # 个别版本 extract 返回 (data, ...) 元组
        pyz_bytes = pyz_bytes[0]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pyz")
    tmp.write(pyz_bytes)
    tmp.close()
    pyz_path = tmp.name

    zar = ZlibArchiveReader(pyz_path)
    ztoc = zar.toc
    toc = list(ztoc.keys()) if isinstance(ztoc, dict) else [t[0] for t in ztoc]
    if "main_window" not in toc:
        print("PYZ.toc 里没有 main_window —— 构建可能漏了")
        return 2
    print(f"PYZ.toc 含 main_window（共 {len(toc)} 个条目）")

    mod_code = zar.extract("main_window")
    if not isinstance(mod_code, types.CodeType):
        print(f"extract('main_window') 返回的不是 code 对象: {type(mod_code)}")
        return 2

    codes = module_codes(mod_code)
    by_name = {}
    for c in codes:
        by_name.setdefault(c.co_name, c)  # 取首个同名（方法只定义一次）

    ok = True
    # 证据 1：_apply_snap_zoom 现在调用 _sync_fullscreen_padding
    fn = by_name.get("_apply_snap_zoom")
    if fn is None:
        print("✗ 找不到函数 _apply_snap_zoom")
        ok = False
    else:
        has = "_sync_fullscreen_padding" in fn.co_names
        print(f"  _apply_snap_zoom.co_names 含 _sync_fullscreen_padding : "
              f"{'是' if has else '否'}")
        ok = ok and has

    # 证据 2：_sync_fullscreen_padding 现在引用 _snap_zoom
    fn2 = by_name.get("_sync_fullscreen_padding")
    if fn2 is None:
        print("✗ 找不到函数 _sync_fullscreen_padding")
        ok = False
    else:
        has2 = "_snap_zoom" in fn2.co_names
        print(f"  _sync_fullscreen_padding.co_names 含 _snap_zoom       : "
              f"{'是' if has2 else '否'}")
        ok = ok and has2

    # 附带确认 _rx_opts_row 仍被 _apply_snap_zoom 引用（折叠逻辑存在）
    if fn is not None:
        print(f"  _apply_snap_zoom.co_names 含 _rx_opts_row            : "
              f"{'是' if '_rx_opts_row' in fn.co_names else '否'}")

    if ok:
        print("\n✓ 三处分屏改动均已进入打包产物（PYZ/main_window）。")
        rc = 0
    else:
        print("\n✗ 未能在打包产物中确认新代码，可能重打包未生效。")
        rc = 1
    try:
        os.unlink(pyz_path)
    except OSError:
        pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
