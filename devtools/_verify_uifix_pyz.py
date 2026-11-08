"""UI 微调（图#1/2/4/5）的打包产物校验。

确定性指纹：本次图#4/5 弹层修复删掉了 `_on_popup_anim` 里那行逐帧
`container.setWindowOpacity(min(1.0, 0.35 + 0.65 * t))`。删除后，
`_on_popup_anim` **函数自身**的 co_names 不应再包含 `setWindowOpacity`
（注意：函数外如 hidePopup/showPopup 仍有，所以必须定位到这个函数内部）。
只要 PYZ 里 ui_controls 的 `_on_popup_anim` 的 co_names 不含 setWindowOpacity，
就证明弹层 opacity 动画已移除、新代码进了产物。
"""
import os
import sys
import tempfile
import types

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader


def collect(co, out):
    out.append(co)
    for const in co.co_consts:
        if isinstance(const, types.CodeType):
            collect(const, out)


def main() -> int:
    exe = sys.argv[1] if len(sys.argv) > 1 else "dist/SerialAssistant.exe"
    print(f"校验目标: {exe}")
    car = CArchiveReader(exe)
    toc_keys = car.toc.keys() if isinstance(car.toc, dict) else [it[0] for it in car.toc]
    pyz = "PYZ.pyz" if "PYZ.pyz" in toc_keys else next(
        (k for k in toc_keys if "PYZ" in str(k)), None)
    if pyz is None:
        print("CArchive 里没找到 PYZ.pyz")
        return 2
    data = car.extract(pyz)
    if isinstance(data, tuple):
        data = data[0]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pyz")
    tmp.write(data)
    tmp.close()
    try:
        zar = ZlibArchiveReader(tmp.name)
        ztoc = zar.toc
        toc = list(ztoc.keys()) if isinstance(ztoc, dict) else [t[0] for t in ztoc]
        if "ui_controls" not in toc:
            print("PYZ.toc 没有 ui_controls —— 构建可能漏了")
            return 2
        mod = zar.extract("ui_controls")
        if not isinstance(mod, types.CodeType):
            print(f"extract('ui_controls') 不是 code: {type(mod)}")
            return 2
        codes = []
        collect(mod, codes)
        fns = {c.co_name: c for c in codes}
        fn = fns.get("_on_popup_anim")
        if fn is None:
            print("✗ 找不到函数 _on_popup_anim")
            return 1
        has = "setWindowOpacity" in fn.co_names
        # hidePopup/showPopup 里仍有 setWindowOpacity(1.0)，但 _on_popup_anim 内不应有
        print(f"  _on_popup_anim.co_names 含 setWindowOpacity : "
              f"{'是(❌未修复)' if has else '否(✓已修复)'}")
        if has:
            print("\n✗ 弹层 opacity 逐帧动画仍在，可能重打包未生效。")
            return 1
        print("\n✓ 弹层 opacity 逐帧动画已移除，UI 微调进入了打包产物（PYZ/ui_controls）。")
        return 0
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
