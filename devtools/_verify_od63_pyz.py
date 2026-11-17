"""打包产物校验（二进制层面，不靠源码自证）——od63 起通用，含 od64 S2。

背景：od61 曾出现「打包比修复早 4 小时」——用户测的 exe 里根本没有修复。
所以这里直接从 ONEFILE exe 抽 PYZ，检查真实进包的字节码指纹：

  A. od62 交叉溶解（末段淡出覆盖层 = 还原方向闪）必须已撤销；
  B. 保留 _CoverLabel 的「保源图比例 + 对称裁边」（od58 拉伸根治）；
  C. 保留 ui_settings 车号残影基础修复（透明底 + swap 当拍 clear）；
  D. 保留进全屏压 DWM 原生动画（_land）；
  E. od64 起：S2 单窗内交叉溶解（set_blend / painter.setOpacity / 陈旧退路）；
  F. 车号过渡（现行设计）：先落数据 → 落位 → 抓图 → 盖 → 淡出前重对齐；
     od119 起覆盖层 = 不透明清晰图垫底 + 模糊图按 α 叠加（底层不透明是关键）。

用法：
  # 打包前自测（编译当前源码，验证断言本身不会假失败）
  .venv/Scripts/python.exe devtools/_verify_od63_pyz.py --source
  # 打包后验真实进包字节码
  .venv/Scripts/python.exe devtools/_verify_od63_pyz.py dist_od64/SerialAssistant.exe

⛔ 断言一律用「任一 code object 命中」而不是 by.get(name)：项目里同名
   paintEvent / __init__ 有多个类，setdefault 取到的第一个未必是目标类。
"""
import os
import sys
import types

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader


def collect_codes(co, out):
    out.append(co)
    for const in co.co_consts:
        if isinstance(const, types.CodeType):
            collect_codes(const, out)


def make_index(code):
    codes = []
    collect_codes(code, codes)
    by = {}
    for c in codes:
        by.setdefault(c.co_name, c)
    return codes, by


def run_checks(mw_codes, mw_by, us_codes, us_by, extra=None,
               drv_data_ok: bool | None = None) -> bool:
    ok = True

    def chk(label, cond, detail=""):
        nonlocal ok
        print(f"  {'OK ' if cond else 'FAIL'} {label}"
              + (f" —— {detail}" if detail and not cond else ""))
        ok = ok and cond

    def floats(c):
        return {v for v in c.co_consts if isinstance(v, float)}

    # ---- A：od62 交叉溶解必须已撤销（两代共同） ----
    print("\n[A] od62 交叉溶解（末段淡出）已撤销")
    tick = mw_by.get("_on_cover_tick")
    chk("_on_cover_tick 存在", tick is not None)
    if tick:
        chk("无 _cf 局部变量（淡出交叉溶解已删）", "_cf" not in tick.co_varnames)
        fl = floats(tick)
        chk("无 0.85/0.15 淡出常量", not {0.85, 0.15}.issubset(fl))
        # 撤销后仍要保留主窗 opacity 连续恢复（P1-3）
        chk("保留主窗 opacity 连续恢复（0.55/0.80）",
            {0.55, 0.8}.issubset(fl))
        # 收尾策略二选一：od63 的 t>=0.99 硬换源 / od64 起 S2 smoothstep 溶解
        s2_names = {"set_blend", "_cover_blend_start", "_cover_pix_b"}
        s2 = s2_names.issubset(set(tick.co_names))
        chk("收尾策略：S2 交叉溶解 或 od63 t>=0.99 换源",
            s2 or 0.99 in fl)

    # ---- B：保比例绘制必须还在 ----
    print("\n[B] _CoverLabel 保源图比例 + 对称裁边")
    keep = [c for c in mw_codes if c.co_name == "draw_keep_aspect"]
    chk("存在 draw_keep_aspect（A/B 各自算裁边矩形）", len(keep) >= 1)
    if keep:
        chk("裁边逻辑同时用 src_ar / rect_ar",
            {"src_ar", "rect_ar"}.issubset(set(keep[0].co_varnames)))
    stretch = [c for c in mw_codes if c.co_name == "_cover_prescale"]
    chk("_cover_prescale 存在", len(stretch) >= 1)
    if stretch:
        chk("_cover_prescale 用 KeepAspectRatio",
            "KeepAspectRatio" in set(stretch[0].co_names))

    # ---- C：车号残影基础修复 ----
    # ⛔⛔ 2026-11-15 改口径：`_FadeOverlay` **不能**再带 WA_TranslucentBackground。
    #    带半透明属性的子控件内容会被合成两遍（正确位置 + 再偏移一个自身坐标），
    #    屏幕上就是「一套模糊 + 一套清晰」两套模组行 —— 用户报的那个叠影。
    #    这条断言以前写反了（要求必须有 WA_TranslucentBackground），现在翻过来。
    print("\n[C] ui_settings 车号选择残影基础修复")
    ov_inits = [c for c in us_codes
                if c.co_name == "__init__"
                and "WA_TranslucentBackground" in set(c.co_names)]
    chk("_FadeOverlay.__init__ 可定位（唯一引用 WA_TranslucentBackground 的 __init__）",
        len(ov_inits) == 1)
    if ov_inits:
        n = set(ov_inits[0].co_names)
        chk("_FadeOverlay 是不透明子控件（WA_NoSystemBackground + WA_OpaquePaintEvent）",
            {"WA_NoSystemBackground", "WA_OpaquePaintEvent"}.issubset(n))

    # ---- D：进全屏压 DWM ----
    print("\n[D] startup_fullscreen._land 压系统原生动画")
    land = mw_by.get("_land")
    chk("_land 存在", land is not None)
    if land:
        chk("_land 调 _set_dwm_transitions",
            "_set_dwm_transitions" in set(land.co_names))

    # ---- E：od64 S2 单窗内交叉溶解 ----
    print("\n[E] S2 单窗内交叉溶解（od64 起）")
    blend = mw_by.get("set_blend")
    chk("_CoverLabel.set_blend 存在", blend is not None)
    if blend:
        chk("set_blend 存 _blend_w 像素权重", "_blend_w" in set(blend.co_names))
    chk("paintEvent 用 painter.setOpacity 做像素混合（非窗口级透明）",
        any("setOpacity" in set(c.co_names)
            for c in mw_codes if c.co_name == "paintEvent"))
    sfs = mw_by.get("startup_fullscreen")
    chk("startup_fullscreen 有 0.90 / 0.99 陈旧度退路常量",
        sfs is not None and {0.9, 0.99}.issubset(floats(sfs)))

    # ---- F：车号过渡（现行设计）----
    # ⛔ 2026-11-14 od116「推倒重来」之后，旧设计的 8 个函数
    #    （_veh_tick / _veh_paint / _veh_swap / _veh_finish / _veh_grab_new /
    #      _veh_visible_h / _veh_jobs / _veh_veil_rect）整体删除。
    #    旧断言还留在这里的话，本脚本会**永远 FAIL**，等于安全网自己喊狼来了
    #    （2026-11-14 就被它误导过一次）。所以整段换成「现行设计」的断言，
    #    并把"旧函数不许复活"显式写成检查项。
    print("\n[F] 车号过渡（现行设计：先落数据 → 落位 → 抓图 → 盖 → 淡出前重对齐）")
    for name in ("_veh_start", "_veh_settle_layout", "_veh_resync",
                 "_veh_pix_close", "_veh_ensure_updates", "_veh_fade_out",
                 "_veh_stop", "_veh_repaint_all"):
        chk(f"ui_settings.{name} 存在", us_by.get(name) is not None)

    start = us_by.get("_veh_start")
    if start:
        sn = set(start.co_names)
        chk("_veh_start 先落数据再抓图", "_veh_apply_rows" in sn and "grab" in sn)
        chk("_veh_start 抓图前先落位", "_veh_settle_layout" in sn)
        chk("_veh_start 贴糊层前确保更新开着", "_veh_ensure_updates" in sn)
        chk("_veh_start 收尾整块重绘面板", "repaint" in sn)
        chk("_veh_start 不再用 setUpdatesEnabled（已实测否决）",
            "setUpdatesEnabled" not in sn)

    fade = us_by.get("_veh_fade_out")
    if fade:
        chk("_veh_fade_out 淡出前重新对齐快照",
            "_veh_resync" in set(fade.co_names))

    rs = us_by.get("_veh_resync")
    if rs:
        rn = set(rs.co_names)
        chk("_veh_resync 抓图前 hide 覆盖层（别把糊层烤进新图）", "hide" in rn)
        chk("_veh_resync 用同一条模糊流水线", "_veh_blur_pixmap" in rn)
        chk("_veh_resync 用 _veh_pix_close 判等", "_veh_pix_close" in rn)

    for dead in ("_veh_tick", "_veh_paint", "_veh_swap", "_veh_finish",
                 "_veh_grab_new", "_veh_visible_h", "_veh_jobs", "_veh_veil_rect"):
        chk(f"旧设计 {dead} 已删除（⛔ 别复活）", us_by.get(dead) is None)

    # od119：覆盖层改成「不透明清晰图垫底 + 模糊图按 α 叠上去」。
    # 只画一层半透明模糊图时，淡出过程中覆盖层底下的窗口表面会露出来
    # （上面可能还留着更早几帧的像素）= 用户反复报的"两套内容叠着"。
    # ⛔ 同名 code object（paintEvent / __init__）有多个类，必须用"任一命中"。
    sfs = [c for c in us_codes if c.co_name == "show_frame"]
    chk("覆盖层 show_frame 收「清晰底图 + 模糊图」两张",
        sfs and all({"base", "blur"}.issubset(set(c.co_varnames))
                    for c in sfs))
    chk("覆盖层 show_frame 已无 items/weight 列表参数",
        sfs and all("items" not in set(c.co_varnames) for c in sfs))
    chk("覆盖层 paintEvent 用 CompositionMode_Source 整块替换",
        any("CompositionMode_Source" in set(c.co_names)
            for c in us_codes if c.co_name == "paintEvent"))
    rb = us_by.get("_rebuild_module_rows")
    if rb:
        chk("_rebuild_module_rows 已删死参数 reserve（占位行机制早已不存在）",
            "reserve" not in set(rb.co_varnames))

    # od73 自激环防护（仍然有效，别丢）：车号下拉必须连 `activated`，
    # ⛔ 连 `currentIndexChanged` 会被程序化 rebuild 的 setCurrentIndex 自激，
    #    把行数塌成 0（用户 2026-11-08 报过）。
    build = us_by.get("_build_vehicle_section")
    if build:
        bn = set(build.co_names)
        chk("车号下拉连 activated、不连 currentIndexChanged",
            "activated" in bn and "currentIndexChanged" not in bn)

    print("\n[P] od78 性能模式（整流 + 手势 + 浮层）")
    mods = extra or {}
    pm = mods.get("perf_mode", (None, None))[1]
    upm = mods.get("ui_perf_mode", (None, None))[1]
    brand = mods.get("ui_brand", (None, None))[1]
    chk("perf_mode 模块进包", pm is not None)
    chk("ui_perf_mode 模块进包", upm is not None)
    chk("ui_brand 模块进包", brand is not None)

    sanitizer = pm.get("Sanitizer") if pm else None
    chk("Sanitizer 类存在", sanitizer is not None)
    if pm:
        # 判据常量在**模块顶层**（不在 Sanitizer 类体里）：4600 mV 上限、
        # 700 mV 偏离上限。改判据必须同步改断言，否则这条会替我们报警。
        module = mods["perf_mode"][0][0]
        _ints = {v for v in module.co_consts if isinstance(v, int)}
        _flo = {v for v in module.co_consts if isinstance(v, float)}
        chk("电压上限 4600mV 在包里", 4600 in _ints)
        chk("离群判据 700mV 在包里", 700.0 in _flo)

    overlay = upm.get("PerfModeOverlay") if upm else None
    chk("PerfModeOverlay 类存在", overlay is not None)
    blur = upm.get("_gaussian") if upm else None
    chk("_gaussian 存在（真高斯）", blur is not None)
    if blur is not None:
        # ⛔ 可分离卷积的 einsum 下标：窗口轴在末尾 -> "hwck,k->hwc"。
        #    写成 "hwkc" 会 ValueError 且被 except 吞掉、退回"没模糊"（od47 踩过）。
        chk("高斯 einsum 维度正确（hwck）",
            any(isinstance(c, str) and "hwck,k->hwc" in c
                for c in blur.co_consts))

    if brand is not None:
        settle = brand.get("_settle_taps")
        chk("队标手势：_settle_taps 发 perf_mode_requested",
            settle is not None
            and "perf_mode_requested" in set(settle.co_names))
        chk("队标手势：_register_tap 做连击计数",
            brand.get("_register_tap") is not None
            and "_tap_count" in set(brand.get("_register_tap").co_names))

    setter = mw_by.get("_set_perf_mode")
    chk("主窗口 _set_perf_mode 存在", setter is not None)
    emit = mw_by.get("_emit_block")
    if emit is not None:
        chk("_emit_block 接了整流（_perf_sanitizer）",
            "_perf_sanitizer" in set(emit.co_names))

    # ---- Q：od79 v2.7（底栏定位时序 + 电流判序 P1）----
    # 本轮改的是"**什么时候**算"和"**先判哪一条**"，光看符号在不在区分不出来
    # （新旧两版的 co_names 完全相同）。所以额外做一件更硬的事：把 PYZ 里这几个
    # 函数的字节码与**当前源码编译出来的**逐字节比对 —— 这直接回答
    # "打进去的是不是眼前这份代码"，od73 就是栽在构建窗口内源码被改。
    print("\n[Q] od79 v2.7（版本号 + 底栏定位时序 + 电流判序）")
    ver = mods.get("version", (None, None))[1]
    se = mods.get("soc_est", (None, None))[1]
    sd = mods.get("soc_display", (None, None))[1]
    chk("version 模块进包", ver is not None)
    chk("soc_est 模块进包", se is not None)
    # 2026-11-12 空载 SOC 显示门控：漏包会让底栏 min/max 冻结静默失效
    chk("soc_display 模块进包", sd is not None)
    if sd is not None:
        chk("SocDisplayGate.decide 存在", sd.get("decide") is not None)
    if ver is not None:
        mod_code = mods["version"][0][0]
        # ⛔ 别把版本号写死：每次升版都要回来改一次，漏改就假红（2026-11-12 发 2.8
        #    时就中了）。以**源码** version.VERSION 为准，比"包里是哪个版本"。
        #    ⚠️ 这里不能读 src_idx —— 它在本函数后半段才建，会 UnboundLocalError。
        want_version = ""
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(
                "_vsrc",
                os.path.join(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))), "version.py"))
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            want_version = str(_mod.VERSION)
        except Exception:                       # noqa: BLE001
            want_version = ""
        chk(f"包内版本号是 {want_version}（与源码一致）",
            bool(want_version)
            and want_version in {c for c in mod_code.co_consts if isinstance(c, str)})

    chk("_schedule_place_soc 存在", mw_by.get("_schedule_place_soc") is not None)
    chk("_place_soc_settled 存在", mw_by.get("_place_soc_settled") is not None)
    # ⛔ 别用 mw_by.get("resizeEvent")：项目里同名方法有多个类，取到的第一个
    #    未必是 MainWindow（见文件头的 ⛔ 说明）。用"任一 code object 命中"。
    geo = [c for c in mw_codes if c.co_name in ("resizeEvent", "showEvent")]
    chk("resizeEvent/showEvent 都改走 _schedule_place_soc",
        bool(geo) and all("_schedule_place_soc" in set(c.co_names) for c in geo))
    chk("resizeEvent/showEvent 不再直接调 _place_soc",
        bool(geo) and not any("_place_soc" in set(c.co_names) for c in geo))
    settled = mw_by.get("_place_soc_settled")
    if settled is not None:
        chk("_place_soc_settled 会清 pending 标志",
            "_place_soc_pending" in set(settled.co_names))

    # od86：设置里新增「显示 SOC 读数」开关。漏包 = 开关点了没反应（静默失效），
    # 所以既查符号在不在，也在下面查字节码与源码一致。
    chk("_apply_soc_visible 存在（「显示 SOC 读数」开关）",
        mw_by.get("_apply_soc_visible") is not None)
    place = mw_by.get("_place_soc")
    if place is not None:
        chk("_place_soc 会跳过被隐藏的读数（isVisibleTo）",
            "isVisibleTo" in set(place.co_names))

    def _sig(co):
        """字节码指纹：指令流 + 名字表 + 非 code 常量。"""
        return (co.co_code, co.co_names, co.co_varnames,
                tuple(c for c in co.co_consts
                      if not isinstance(c, types.CodeType)))

    def _src_index(mod):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, mod + ".py")
        with open(path, "rb") as f:
            return make_index(compile(f.read(), path, "exec"))[1]

    src_idx = {}
    for mod in ("soc_est", "main_window", "soc_display"):
        try:
            src_idx[mod] = _src_index(mod)
        except OSError:
            src_idx[mod] = {}
    for mod, index, fn in (("soc_est", se, "_effective_current"),
                           ("soc_est", se, "clean_cells"),
                           ("main_window", mw_by, "_schedule_place_soc"),
                           ("main_window", mw_by, "_place_soc_settled"),
                           ("main_window", mw_by, "_apply_soc_visible"),
                           ("soc_display", sd, "decide")):
        got = index.get(fn) if index else None
        want = src_idx.get(mod, {}).get(fn)
        chk(f"{mod}.{fn} 与当前源码逐字节一致（不是旧构建）",
            got is not None and want is not None and _sig(got) == _sig(want))

    # ---- R. od90：USB 转串口驱动体检（drv_assist）----
    print("\n[R] od90（随包驱动自检 / 提权安装）")
    da = mods.get("drv_assist", (None, None))[1]
    chk("drv_assist 模块进包", da is not None)
    check_names = ("payload_dir", "install", "run_cli", "describe",
                   "missing_keys", "needed_keys", "problem_devices",
                   "_pnputil_ok")
    missing_fn = [n for n in check_names if n not in da] if da else []
    chk("drv_assist 关键函数都在包内（缺任一 = 驱动体检静默失效）",
        da is not None and not missing_fn, str(missing_fn))
    if da is not None:
        mod_code = mods["drv_assist"][0][0]
        # PAYLOADS 里的 dict 是**运行时**构造的（dict 不是常量），所以模块 co_consts
        # 里能找到的是 BUILD_CONST_KEY_MAP 用的键元组；查它就等于查"PAYLOADS 进包了"。
        chk("PAYLOADS 常量进包（驱动键表）",
            any(isinstance(c, tuple)
                and {"key", "label", "vendor", "vids", "infs"} <= set(c)
                for c in mod_code.co_consts))
        chk("PAYLOAD_VERSION 是字符串常量",
            any(isinstance(c, str) and len(c) == 10 and c[4] == "-"
                for c in mod_code.co_consts))
    chk("_driver_gate_tick 存在（启动体检轮询）",
        mw_by.get("_driver_gate_tick") is not None)
    chk("_maybe_install_drivers 存在", mw_by.get("_maybe_install_drivers") is not None)
    chk("_on_driver_install_done 存在",
        mw_by.get("_on_driver_install_done") is not None)
    # od97：打开成功却一直没数据时的点明（2026-11-12 用户「连上读不了数」）
    chk("_arm_rx_silence_watch / _check_rx_silence 存在（静默提示）",
        mw_by.get("_arm_rx_silence_watch") is not None
        and mw_by.get("_check_rx_silence") is not None)

    drv_src = {}
    try:
        drv_src["drv_assist"] = _src_index("drv_assist")
    except OSError:
        drv_src["drv_assist"] = {}
    for mod, index, fn in (("drv_assist", da, "payload_dir"),
                           ("drv_assist", da, "install"),
                           ("drv_assist", da, "run_cli"),
                           ("drv_assist", da, "_pnputil_ok"),
                           ("main_window", mw_by, "_maybe_install_drivers"),
                           ("main_window", mw_by, "_check_rx_silence")):
        got = index.get(fn) if index else None
        want = drv_src.get(mod, {}).get(fn) if mod == "drv_assist" else \
            src_idx.get(mod, {}).get(fn)
        chk(f"{mod}.{fn} 与当前源码逐字节一致（不是旧构建）",
            got is not None and want is not None and _sig(got) == _sig(want))

    # 驱动包本身是 data（不在 PYZ 里），漏打 = 用户机器上没有 inf 可装。
    if drv_data_ok is not None:
        chk("exe 里带着 drivers/ 驱动包（data 层）", drv_data_ok)

    return ok


def from_pyz(exe):
    car = CArchiveReader(exe)
    top = car.toc.keys() if isinstance(car.toc, dict) else [it[0] for it in car.toc]
    top = [str(k) for k in top]
    pyz_name = "PYZ.pyz" if "PYZ.pyz" in top else next(
        (k for k in top if k == "PYZ" or k.endswith("PYZ.pyz")), None)
    if pyz_name is None:
        print("FAIL 没找到 PYZ.pyz")
        return None
    data = car.extract(pyz_name)
    if isinstance(data, tuple):
        data = data[0]
    # ⛔ 临时文件写 F 盘项目内，别落 C 盘 Temp
    os.makedirs("_check", exist_ok=True)
    tmp_path = os.path.join("_check", "_pyz_extracted.pyz")
    with open(tmp_path, "wb") as f:
        f.write(data)
    try:
        zar = ZlibArchiveReader(tmp_path)
        ztoc = zar.toc
        toc = list(ztoc.keys()) if isinstance(ztoc, dict) else [t[0] for t in ztoc]
        wanted = ("main_window", "ui_settings", "perf_mode",
                  "ui_perf_mode", "ui_brand", "soc_est", "version",
                  "soc_display", "drv_assist")
        for mod in wanted:
            if mod not in toc:
                print(f"FAIL PYZ.toc 缺模块 {mod}")
                return None
        print(f"PYZ.toc 含 {' / '.join(wanted)}（共 {len(toc)} 条）")
        built = {mod: make_index(zar.extract(mod)) for mod in wanted}
        mw = built["main_window"]
        us = built["ui_settings"]
        extra = {mod: built[mod] for mod in ("perf_mode", "ui_perf_mode",
                                             "ui_brand", "soc_est", "version",
                                             "soc_display", "drv_assist")}
        # drivers/ 是 data 层（不在 PYZ）：清单里必须真有 .inf，否则用户机器
        # 上没有可安装的驱动包，体检会一直报"找不到驱动包"。
        drv_data = any(
            str(k).replace("\\", "/").lower().startswith("drivers/")
            and str(k).lower().endswith(".inf") for k in top)
        return mw[0], mw[1], us[0], us[1], extra, drv_data
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def from_source():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    built = {}
    for mod in ("main_window", "ui_settings", "perf_mode",
                "ui_perf_mode", "ui_brand", "soc_est", "version",
                "soc_display", "drv_assist"):
        path = os.path.join(root, mod + ".py")
        with open(path, "rb") as f:
            code = compile(f.read(), path, "exec")
        codes, by = make_index(code)
        built[mod] = (codes, by)
        print(f"[source] {mod}.py -> {len(codes)} code objects")
    extra = {mod: built[mod] for mod in ("perf_mode", "ui_perf_mode",
                                         "ui_brand", "soc_est", "version",
                                         "soc_display", "drv_assist")}
    mw = built["main_window"]
    us = built["ui_settings"]
    drv_root = os.path.join(root, "drivers")
    drv_data = any(name.lower().endswith(".inf")
                   for _r, _d, files in os.walk(drv_root) for name in files)
    return mw[0], mw[1], us[0], us[1], extra, drv_data


def main() -> int:
    source_mode = "--source" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--source"]
    if source_mode:
        print("校验目标: 当前源码（--source 自测模式，不抽 PYZ）")
        res = from_source()
        ok = run_checks(*res)
        print("\n" + ("=== 源码断言全部通过 ===" if ok
                      else "=== 源码断言有失败项 ==="))
        return 0 if ok else 1

    exe = args[0] if args else "发布/SerialAssistant.exe"
    if not os.path.isfile(exe):
        print(f"找不到 exe: {exe}")
        return 2
    print(f"校验目标: {exe} ({os.path.getsize(exe)} bytes)")
    res = from_pyz(exe)
    if res is None:
        return 2
    ok = run_checks(*res)
    print("\n" + ("=== PYZ 全部通过 ===" if ok else "=== PYZ 有失败项 ==="))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
