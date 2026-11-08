"""od48 打包产物校验：确认本轮 4 项改动**真进了 exe**（不只是源码）。

指纹全部取自 PYZ 里的**字节码**（co_consts / co_names / co_name），不靠字符串
注释，因此是确定性证据：

  1) splash_motion._ease_land 换成新曲线 ``6x²−8x³+3x⁴``
     → co_consts 同时含 6.0 / 8.0 / 3.0（旧 smoothstep 只有 3.0 / 2.0）。
  2) 新增 _smoothstep（淡出包络），且 paint() 的 co_names 引用它
     → 证明淡出没有误用新的落点曲线。
  3) 上一轮「落点卡顿」修复：paint() 的 co_names **不再**含 _svg
     （每帧 QSvgRenderer 重栅格化已移除，改走缓存位图 drawPixmap）。
  4) 上一轮「末尾闪一下」修复：_fade_step() 的 co_names **不再**含
     setWindowOpacity；__init__ 的 co_names 含 WA_TranslucentBackground。
"""
import os
import sys
import tempfile
import types

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader


def collect_codes(co, out):
    out.append(co)
    for const in co.co_consts:
        if isinstance(const, types.CodeType):
            collect_codes(const, out)


def main() -> int:
    exe_path = sys.argv[1] if len(sys.argv) > 1 else "发布/SerialAssistant.exe"
    if not os.path.isfile(exe_path):
        print(f"找不到待校验的 exe: {exe_path}")
        return 2
    print(f"校验目标: {exe_path}  ({os.path.getsize(exe_path)} bytes)")

    car = CArchiveReader(exe_path)
    toc_keys = (car.toc.keys() if isinstance(car.toc, dict)
                else [it[0] for it in car.toc])
    pyz_name = "PYZ.pyz" if "PYZ.pyz" in toc_keys else next(
        (k for k in toc_keys if k == "PYZ" or str(k).endswith("PYZ.pyz")), None)
    if pyz_name is None:
        print("✗ CArchive 里没找到 PYZ.pyz")
        return 2

    pyz_bytes = car.extract(pyz_name)
    if isinstance(pyz_bytes, tuple):
        pyz_bytes = pyz_bytes[0]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pyz")
    tmp.write(pyz_bytes)
    tmp.close()
    pyz_path = tmp.name
    try:
        zar = ZlibArchiveReader(pyz_path)
        ztoc = zar.toc
        toc = list(ztoc.keys()) if isinstance(ztoc, dict) else [t[0] for t in ztoc]
        for mod in ("splash_motion", "splash_anim"):
            if mod not in toc:
                print(f"✗ PYZ.toc 缺模块 {mod} —— 打包漏了 hiddenimports")
                return 2
        print(f"PYZ.toc 含 splash_motion / splash_anim（共 {len(toc)} 条）")

        def codes_of(mod):
            code = zar.extract(mod)
            if not isinstance(code, types.CodeType):
                raise TypeError(f"extract({mod}) -> {type(code)}")
            out = []
            collect_codes(code, out)
            by_name = {}
            for c in out:
                by_name.setdefault(c.co_name, c)
            return out, by_name

        sm_codes, sm = codes_of("splash_motion")
        sa_codes, sa = codes_of("splash_anim")
        ok = True

        def chk(label, cond):
            nonlocal ok
            print(f"  {'✓' if cond else '✗'} {label}")
            ok = ok and cond

        # 1) 新落点曲线常量
        land = sm.get("_ease_land")
        chk("_ease_land 存在", land is not None)
        if land is not None:
            consts = {v for v in land.co_consts if isinstance(v, float)}
            chk("新曲线常量 6.0/8.0/3.0 齐全（旧曲线只有 3.0/2.0）",
                {6.0, 8.0, 3.0}.issubset(consts))

        # 2) 淡出包络独立成 _smoothstep，且 paint 引用它
        chk("新增 _smoothstep 函数", "_smoothstep" in sm)
        paint = sm.get("paint")
        chk("paint() 存在", paint is not None)
        if paint is not None:
            chk("paint() 引用 _smoothstep（淡出未误用落点曲线）",
                "_smoothstep" in paint.co_names)
            # 3) 落点卡顿修复：不再每帧重栅格化 SVG
            chk("paint() 已不含 _svg（落点段走缓存位图）",
                "_svg" not in paint.co_names)

        # 4) 末尾不闪：窗口不调 setWindowOpacity，改用 WA_TranslucentBackground
        fade = sa.get("_fade_step")
        chk("_fade_step() 存在", fade is not None)
        if fade is not None:
            chk("_fade_step() 不含 setWindowOpacity（消除 DWM 闪）",
                "setWindowOpacity" not in fade.co_names)
        init = sa.get("__init__")
        chk("__init__() 存在", init is not None)
        if init is not None:
            chk("__init__() 含 WA_TranslucentBackground（内容淡出）",
                "WA_TranslucentBackground" in init.co_names)

        # 附带：确认 _ease_land 仍只被 _state 使用，淡出没串味
        state = sm.get("_state")
        if state is not None:
            print(f"  [info] _state() 引用 _ease_land : "
                  f"{'_ease_land' in state.co_names}")

        print("\n" + ("✓ 本轮 4 项改动全部进入打包产物。" if ok
                      else "✗ 产物与源码不一致，请勿部署。"))
        return 0 if ok else 1
    finally:
        try:
            os.unlink(pyz_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
