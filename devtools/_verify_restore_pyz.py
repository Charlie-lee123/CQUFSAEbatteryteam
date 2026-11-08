"""「最大化→最小化→任务栏还原四周露壁纸」修复的打包产物校验。

目的：确认 main_window.py 的 showEvent 兜底改动**真进了 exe**，而不是只改了源码。

指纹依据（函数级 co_names 差异，确定性证据）：

  改动前 showEvent 的还原兜底**不调用** _enforce_native_fullscreen / _sync_fullscreen_padding；
  改动后 showEvent 在「刚最小化又回来」分支里通过 QTimer.singleShot 调用了这两个方法：
      QTimer.singleShot(30, self._enforce_native_fullscreen)
      QTimer.singleShot(40, self._sync_fullscreen_padding)
  → showEvent 的 co_names 会**新增** `_enforce_native_fullscreen` 与 `_sync_fullscreen_padding`。

只要这两点在 PYZ 里的 main_window 模块成立，就证明新代码进了打包产物。
"""
import sys
import os
import tempfile
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
    pyz_bytes = car.extract(pyz_name)
    if isinstance(pyz_bytes, tuple):
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
        by_name.setdefault(c.co_name, c)

    ok = True
    fn = by_name.get("showEvent")
    if fn is None:
        print("✗ 找不到函数 showEvent")
        ok = False
    else:
        has_enforce = "_enforce_native_fullscreen" in fn.co_names
        has_sync = "_sync_fullscreen_padding" in fn.co_names
        print(f"  showEvent.co_names 含 _enforce_native_fullscreen : "
              f"{'是' if has_enforce else '否'}")
        print(f"  showEvent.co_names 含 _sync_fullscreen_padding  : "
              f"{'是' if has_sync else '否'}")
        ok = ok and has_enforce and has_sync

    # 2026-11-02 新修复指纹：_enforce_native_fullscreen 末尾补调 _enable_native_snap
    # （showFullScreen 会把 WS_POPUP 加回 → DWM 不播最小化/打开动画 + 缝隙）。
    fn2 = by_name.get("_enforce_native_fullscreen")
    if fn2 is None:
        print("✗ 找不到函数 _enforce_native_fullscreen")
        ok = False
    else:
        has_snap = "_enable_native_snap" in fn2.co_names
        print(f"  _enforce_native_fullscreen.co_names 含 _enable_native_snap : "
              f"{'是' if has_snap else '否'}")
        ok = ok and has_snap

    # 2026-11-02 DWM 描边修复指纹：_set_corner_preference 里 DWMWA_BORDER_COLOR=34
    fn3 = by_name.get("_set_corner_preference")
    if fn3 is None:
        print("✗ 找不到函数 _set_corner_preference")
        ok = False
    else:
        has_border34 = 34 in [c for c in fn3.co_consts if isinstance(c, int)]
        print(f"  _set_corner_preference 常量含 DWMWA_BORDER_COLOR(34) : "
              f"{'是' if has_border34 else '否'}")
        ok = ok and has_border34

    # 2026-11-02 覆盖层动画指纹：startup_fullscreen / _apply_snap_zoom 调 _run_window_cover
    for fname in ("startup_fullscreen", "_apply_snap_zoom"):
        f = by_name.get(fname)
        has_cover = f is not None and "_run_window_cover" in f.co_names
        print(f"  {fname}.co_names 含 _run_window_cover : "
              f"{'是' if has_cover else '否'}")
        ok = ok and has_cover
    # 2026-11-02 去闪指纹：覆盖层快照必须走屏幕抓图（grab/PrintWindow 只拿到底色）
    f = by_name.get("startup_fullscreen")
    has_screengrab = f is not None and "_grab_window_screen" in f.co_names
    print(f"  startup_fullscreen.co_names 含 _grab_window_screen : "
          f"{'是' if has_screengrab else '否'}")
    ok = ok and has_screengrab
    # 2026-11-02 第七轮：进全屏改真窗口几何补间（不再用拉伸快照）
    has_geomorph = f is not None and "_win_geo_morph" in f.co_names
    print(f"  startup_fullscreen.co_names 含 _win_geo_morph : "
          f"{'是' if has_geomorph else '否'}")
    ok = ok and has_geomorph

    # 2026-11-02 车辆节交叉淡入指纹（ui_settings）
    if "ui_settings" in toc:
        ui_code = zar.extract("ui_settings")
        ui_names = {c.co_name for c in module_codes(ui_code)} \
            if isinstance(ui_code, types.CodeType) else set()
        has_fade = "_FadeOverlay" in ui_names and "_play_module_rows_fade" in ui_names
        print(f"  ui_settings 含 _FadeOverlay/_play_module_rows_fade : "
              f"{'是' if has_fade else '否'}")
        ok = ok and has_fade

    if ok:
        print("\n✓ 还原四周缝隙的 showEvent 兜底改动已进入打包产物（PYZ/main_window）。")
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
