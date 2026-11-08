"""一次性核验：已部署 exe 的 _veh_tick 是否含 od67 的 activate() 调用。

用法：.venv/Scripts/python.exe devtools/_check_exe_od67.py [exe路径]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _verify_od63_pyz import from_pyz  # noqa: E402


def main() -> int:
    exe = sys.argv[1] if len(sys.argv) > 1 else "发布/SerialAssistant.exe"
    res = from_pyz(exe)
    if res is None:
        return 2
    mw_codes, mw_by, us_codes, us_by = res
    vt = us_by.get("_veh_tick")
    if vt is None:
        print("FAIL _veh_tick 不在 PYZ")
        return 2
    names = set(vt.co_names)
    consts = {v for v in vt.co_consts if isinstance(v, str)}
    has_activate = "activate" in names
    has_layout = "layout" in names
    has_form = "form" in names
    print(f"[{exe}]")
    print(f"  _veh_tick 含 'activate': {has_activate}")
    print(f"  _veh_tick 含 'layout':  {has_layout}")
    print(f"  _veh_tick 含 'form':    {has_form}")
    print(f"  rows_shown 守卫:        {'rows_shown' in consts}")
    # 同时核验 od66 的整块 rect_old 修复是否还在
    vp = us_by.get("_veh_paint")
    if vp is not None:
        vnames = set(vp.co_names)
        vconsts = {v for v in vp.co_consts if isinstance(v, str)}
        print(f"  [od66] _veh_paint 含 rect_old(整块): {'rect_old' in vconsts}")
        print(f"  [od66] _veh_paint 含 _veh_shift_ease: {'_veh_shift_ease' in vnames}")
    print("RESULT:", "OD67_PRESENT" if has_activate else "OD67_MISSING")
    return 0 if has_activate else 1


if __name__ == "__main__":
    sys.exit(main())
