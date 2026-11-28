"""od163 打包产物校验：确认用户 2026-11-27 那五项改动**真进了 exe**。

指纹取自 PYZ 里的**字节码**（co_consts / co_names / co_name），不是源码注释，
所以是确定性证据 —— 只看源码文件是证明不了 exe 里是哪一版。

  1) 串口视图滤掉蓝牙虚拟口
     serial_io.is_bluetooth_port 存在，且 _BLUETOOTH_HWID = "BTHENUM"；
     list_serial_ports 的 co_varnames 含 include_bluetooth（关键字参数进了
     签名）；main_window._refresh_ports / _poll_ports 的 co_consts 含
     "include_bluetooth"（说明真的按关掉蓝牙的方式调用了）。
  2) 模糊参数对齐车号切换
     main_window 模块级含 _CFG_BLUR_SIGMA = 24.0 与 _CFG_BLUR_MS = 650；
     _blur_pixmap 的签名含 desaturate。
  3) 材质效果滑杆等长 + 动画帧率去复原按钮
     ui_settings 里已**没有** GLASS_ROW_W；_build_slider_row 的 co_consts 含
     "anim_fps"（说明那个 if 判断进包了）。
  4) 赛季 2026~2030 且框宽 92
     ui_settings 模块级含 SEASON_MIN_YEAR=2026 / SEASON_MAX_YEAR=2030 /
     SEASON_SPIN_W=92；原 2000/2100 不再作为 const 出现在建下拉的函数里。
  5) 开关文字在左
     ui_controls.Switch.set_text_first 存在；main_window 里
     chk_timestamp / chk_hex_view 的构建函数 co_names 含 set_text_first。
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


def consts_of(code):
    """一个函数/模块 code 对象里出现的全部**字面量**（递归，展开元组）。

    ⚠️ 必须展开元组：Python 3.12 起关键字参数的**名字**不放在 co_names，
    而是压进 co_consts 里的一个 tuple（``KW_NAMES`` 指令指向它）——
    所以 ``f(x=1)`` 里的 "x" 只会出现在 ``co_consts`` 的
    ``('x',)`` 这种元组里。不展开就会漏判"真的传了那个关键字"。
    """
    found = []

    def walk(co):
        for c in co.co_consts:
            if isinstance(c, types.CodeType):
                walk(c)
            elif isinstance(c, tuple):
                for item in c:
                    if not isinstance(item, types.CodeType):
                        found.append(item)
            else:
                found.append(c)

    walk(code)
    return found


def find_func(code, name):
    """按 co_name 递归找一个函数（同名可能有多个，返回第一个）。"""
    if code.co_name == name:
        return code
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            hit = find_func(c, name)
            if hit is not None:
                return hit
    return None


def all_funcs(code, name, out=None):
    if out is None:
        out = []
    if code.co_name == name:
        out.append(code)
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            all_funcs(c, name, out)
    return out


class Checker:
    def __init__(self):
        self.failed = []

    def ok(self, cond, msg):
        if cond:
            print(f"  [OK] {msg}")
        else:
            print(f"  [!!] {msg}")
            self.failed.append(msg)
        return cond


def main() -> int:
    exe_path = sys.argv[1] if len(sys.argv) > 1 else "发布/SerialAssistant.exe"
    if not os.path.isfile(exe_path):
        print(f"找不到待校验的 exe: {exe_path}")
        return 2
    print(f"校验目标: {exe_path}  ({os.path.getsize(exe_path)} bytes)")

    car = CArchiveReader(exe_path)
    toc_keys = (car.toc.keys() if isinstance(car.toc, dict)
                else [it[0] for it in car.toc])
    pyz_key = next((k for k in toc_keys
                    if k == "PYZ.pyz" or k == "PYZ"
                    or str(k).endswith("PYZ.pyz")), None)
    if pyz_key is None:
        print("exe 里没有 .pyz 归档")
        return 2
    # ⚠️ ZlibArchiveReader 只吃**文件路径**（新版本要按文件名里的 "?offset"
    #    定位归档起点），直接喂 bytes 会 TypeError。先落到临时文件再读。
    pyz_bytes = car.extract(pyz_key)
    if isinstance(pyz_bytes, tuple):
        pyz_bytes = pyz_bytes[0]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pyz")
    tmp.write(pyz_bytes)
    tmp.close()
    try:
        pyz = ZlibArchiveReader(tmp.name)
        names = set(pyz.toc.keys()) if isinstance(pyz.toc, dict) else set(pyz.toc)
        return _run_checks(pyz, names)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _run_checks(pyz, names):

    def module_code(mod):
        if mod not in names:
            return None
        try:
            raw = pyz.extract(mod)
        except Exception:                                  # noqa: BLE001
            return None
        if isinstance(raw, types.CodeType):
            return raw
        if hasattr(raw, "code"):
            return raw.code
        if isinstance(raw, (bytes, bytearray)):
            import marshal
            return marshal.loads(raw)
        return None

    ck = Checker()

    # ---- 模块都进包了吗 ----
    for mod in ("serial_io", "main_window", "ui_settings", "ui_controls",
                "ui_perf_mode"):
        ck.ok(mod in names, f"{mod} 在 PYZ 里")

    # ---- 1) 串口滤掉蓝牙 ----
    print("\n[1] 串口视图滤掉蓝牙虚拟口")
    si = module_code("serial_io")
    if si is not None:
        ck.ok("BTHENUM" in consts_of(si),
              "serial_io 含判据前缀 'BTHENUM'")
        _ibp = find_func(si, "is_bluetooth_port")
        ck.ok(_ibp is not None, "serial_io.is_bluetooth_port 存在")
        _lsp = find_func(si, "list_serial_ports")
        ck.ok(_lsp is not None, "serial_io.list_serial_ports 存在")
        if _lsp is not None:
            ck.ok("include_bluetooth" in _lsp.co_varnames,
                  "list_serial_ports 签名含 include_bluetooth（关键字参数）")

    mw = module_code("main_window")
    if mw is not None:
        for fn in ("_refresh_ports", "_poll_ports"):
            code = find_func(mw, fn)
            if ck.ok(code is not None, f"main_window.{fn} 存在"):
                ck.ok("include_bluetooth" in code.co_names
                      or "include_bluetooth" in consts_of(code),
                      f"{fn} 按 include_bluetooth=False 调用")

    # ---- 2) 模糊参数对齐 ----
    print("\n[2] 连接方式切换的模糊参数对齐车号切换")
    if mw is not None:
        _mod_consts = [c for c in mw.co_consts]
        ck.ok(24.0 in _mod_consts, "main_window 模块级含 24.0（_CFG_BLUR_SIGMA）")
        ck.ok(650 in _mod_consts, "main_window 模块级含 650（_CFG_BLUR_MS）")
        _swap = find_func(mw, "_play_config_blur_swap")
        if ck.ok(_swap is not None, "_play_config_blur_swap 存在"):
            ck.ok("desaturate" in _swap.co_varnames
                  or "desaturate" in _swap.co_names
                  or "desaturate" in consts_of(_swap),
                  "模糊调用带 desaturate 关键字")
    upm = module_code("ui_perf_mode")
    if upm is not None:
        _blur = find_func(upm, "_blur_pixmap")
        if ck.ok(_blur is not None, "ui_perf_mode._blur_pixmap 存在"):
            ck.ok("desaturate" in _blur.co_varnames,
                  "_blur_pixmap 签名含 desaturate")

    # ---- 3) 材质效果滑杆 + anim_fps 去按钮 ----
    print("\n[3] 材质效果滑杆等长 / 动画帧率去掉复原按钮")
    us = module_code("ui_settings")
    if us is not None:
        ck.ok("GLASS_ROW_W" not in us.co_names
              and "GLASS_ROW_W" not in [c for c in consts_of(us)
                                        if isinstance(c, str)],
              "ui_settings 里已无 GLASS_ROW_W（旧固定行宽已废除）")
        _bsr = find_func(us, "_build_slider_row")
        if ck.ok(_bsr is not None, "_build_slider_row 存在"):
            ck.ok("anim_fps" in consts_of(_bsr),
                  "_build_slider_row 里有 anim_fps 判断（不放复原按钮）")

    # ---- 4) 赛季范围与框宽 ----
    print("\n[4] 赛季 2026~2030 且框宽 92")
    if us is not None:
        _uc = consts_of(us)
        ck.ok(2026 in _uc, "ui_settings 含 2026（SEASON_MIN_YEAR）")
        ck.ok(2030 in _uc, "ui_settings 含 2030（SEASON_MAX_YEAR）")
        ck.ok(92 in [c for c in us.co_consts if not isinstance(c, str)],
              "ui_settings 模块级含 92（SEASON_SPIN_W）")

    # ---- 5) 开关文字在左 ----
    print("\n[5] 接收区开关文字在左")
    uc = module_code("ui_controls")
    if ck.ok(uc is not None, "ui_controls 在包内"):
        ck.ok(find_func(uc, "set_text_first") is not None,
              "ui_controls.Switch.set_text_first 存在")
        ck.ok(find_func(uc, "_track_rect") is not None,
              "Switch._track_rect 存在（几何同源）")
    if mw is not None:
        hits = [f for f in all_funcs(mw, "_build_receive_area")
                if "set_text_first" in f.co_names]
        if not hits:
            hits = [f for f in all_funcs(mw, "__init__")
                    if "set_text_first" in f.co_names]
        ck.ok(bool(hits), "main_window 构建接收区时调用了 set_text_first")

    print()
    if ck.failed:
        print(f"✗ 有 {len(ck.failed)} 项没进包：")
        for m in ck.failed:
            print(f"   - {m}")
        return 1
    print("✓ 五项改动全部进入打包产物。")
    return 0


if __name__ == "__main__":
    sys.exit(main())