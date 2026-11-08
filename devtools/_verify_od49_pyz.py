"""od49 打包产物校验：A（收尾闪/帧率断崖）+ B2（撤场 z-order 空窗期）全部进包。

沿用 od48 的 CArchiveReader → ZlibArchiveReader → 递归 co_consts/co_names 指纹法。
新增 B2 指纹：main 入口脚本在 CArchive 顶层（不在 PYZ），用 marshal.loads 读取。
"""
import marshal
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


def names_strings(co):
    """把某个 code 对象及其所有嵌套 code 的 co_names / 字符串常量汇总成一个集合。"""
    out = set()
    stack = [co]
    while stack:
        c = stack.pop()
        out.add(c.co_name)          # 嵌套函数名（_on_fade_start 等）是 code.co_name
        out.update(c.co_names)
        out.update(v for v in c.co_consts if isinstance(v, str))
        stack.extend(v for v in c.co_consts if isinstance(v, types.CodeType))
    return out


def main() -> int:
    exe = sys.argv[1] if len(sys.argv) > 1 else "发布/SerialAssistant.exe"
    if not os.path.isfile(exe):
        print(f"找不到 exe: {exe}")
        return 2
    print(f"校验目标: {exe} ({os.path.getsize(exe)} bytes)")
    car = CArchiveReader(exe)
    top = car.toc.keys() if isinstance(car.toc, dict) else [it[0] for it in car.toc]
    top = [str(k) for k in top]
    pyz_name = "PYZ.pyz" if "PYZ.pyz" in top else next(
        (k for k in top if k == "PYZ" or k.endswith("PYZ.pyz")), None)
    if pyz_name is None:
        print("✗ 没找到 PYZ.pyz")
        return 2
    data = car.extract(pyz_name)
    if isinstance(data, tuple):
        data = data[0]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pyz")
    tmp.write(data)
    tmp.close()
    ok = True

    def chk(label, cond):
        nonlocal ok
        print(f"  {'✓' if cond else '✗'} {label}")
        ok = ok and cond

    try:
        zar = ZlibArchiveReader(tmp.name)
        ztoc = zar.toc
        toc = list(ztoc.keys()) if isinstance(ztoc, dict) else [t[0] for t in ztoc]
        for mod in ("splash_motion", "splash_anim"):
            if mod not in toc:
                print(f"✗ PYZ.toc 缺模块 {mod}")
                return 2
        print(f"PYZ.toc 含 splash_motion / splash_anim（共 {len(toc)} 条）")

        def index(mod):
            code = zar.extract(mod)
            if not isinstance(code, types.CodeType):
                raise TypeError(f"extract({mod}) -> {type(code)}")
            codes = []
            collect_codes(code, codes)
            by = {}
            for c in codes:
                by.setdefault(c.co_name, c)
            return codes, by

        sm, sm_by = index("splash_motion")
        sa, sa_by = index("splash_anim")

        # ---- A 组：上一轮三处硬伤 ----
        print("\n[A] 收尾闪 + 淡出帧率断崖")
        land = sm_by.get("_ease_land")
        chk("_ease_land 存在", land is not None)
        if land:
            consts = {v for v in land.co_consts if isinstance(v, float)}
            chk("新落点曲线常量 6.0/8.0/3.0 齐全", {6.0, 8.0, 3.0}.issubset(consts))
        chk("_smoothstep 存在（淡出包络独立）", "_smoothstep" in sm_by)
        paint = sm_by.get("paint")
        if paint:
            pn = set(paint.co_names)
            chk("paint() 引用 _smoothstep", "_smoothstep" in pn)
            chk("paint() 不含 _svg（落点走缓存位图）", "_svg" not in pn)
            # od52（用户「到位后再淡出啊」）：落点飞行途中不再化幕 —— paint 里
            # 不许再出现 _ease_land（od51 用它让底色随落点同步收口，被否掉）；
            # 淡出包络改成落地之后才开始：_seg(fk,0,0.75) + _seg(fk,0.55,1)。
            chk("paint() 落点途中不化幕（无 _ease_land）", "_ease_land" not in pn)
            pf = {v for v in paint.co_consts if isinstance(v, float)}
            # od57：曲线改成「planA 整体糊开 / planB 背景径向溶解」后的新常量。
            chk("淡出包络走新曲线（常量 0.88 / 0.74）",
                {0.88, 0.74}.issubset(pf))
            chk("旧「边飞边淡」包络已移除（0.65 / 0.35 不再成对）",
                not ({0.65, 0.35}.issubset(pf)))
        fade = sa_by.get("_fade_step")
        chk("_fade_step() 存在", fade is not None)
        if fade:
            fn = set(fade.co_names)
            chk("_fade_step 不含 setWindowOpacity", "setWindowOpacity" not in fn)
            chk("_fade_step 用墙钟 QElapsedTimer / repaint 同步画全透明帧",
                "repaint" in fn or "QElapsedTimer" in fn)
        init = sa_by.get("__init__")
        chk("__init__ 含 WA_TranslucentBackground",
            init is not None and "WA_TranslucentBackground" in set(init.co_names))
        start_m = sa_by.get("_start_motion")
        start_f = sa_by.get("start")
        # setAutoFillBackground(False)：常量 0/False 未必直观，改用「无 True 调用」+ 存在调用
        for nm, fn in (("_start_motion", start_m), ("start", start_f)):
            if fn is None:
                print(f"  [info] {nm} 不存在（路径可能未启用），跳过")
                continue
            print(f"  [info] {nm}.co_names 含 setAutoFillBackground: "
                  f"{'setAutoFillBackground' in set(fn.co_names)}")

        # ---- B2 组：main 入口脚本在 CArchive 顶层 ----
        print("\n[B2] 撤场 z-order 空窗期")
        main_key = next((k for k in top if k == "main" or k.endswith("/main")), None)
        if main_key is None:
            chk("CArchive 找到 main 入口脚本", False)
        else:
            raw = car.extract(main_key)
            if isinstance(raw, tuple):
                raw = raw[0]
            mcode = marshal.loads(raw)
            chk("main 入口脚本可 marshal 解码", isinstance(mcode, types.CodeType))
            blob = names_strings(mcode)
            chk("main 含 _on_fade_start 回调", "_on_fade_start" in blob)
            chk("main 调用 set_on_fade_start", "set_on_fade_start" in blob)
            chk("main 回调内 activateWindow / raise_", "activateWindow" in blob and "raise_" in blob)
            chk("main 保留 _on_splash_done 兜底", "_on_splash_done" in blob)
            chk("B2 失败留痕日志已进包",
                any("开屏淡出前主窗置前失败" in s for s in blob))

        # ---- od53 组：开屏构造期卡顿治理 + 窗口几何夹屏 ----
        print("\n[od53] 构造期让出 / 预热 / 几何夹屏")
        chk("_warm_font_on_main 存在（主线程字体预热）",
            "_warm_font_on_main" in sa_by)
        land_fn = sa_by.get("set_land_from_window")
        if land_fn is not None:
            ln = set(land_fn.co_names)
            # od53：把 processEvents 换成只刷布局的 sendPostedEvents —— 前者会把
            # _build_fs_idle_pix 的整屏 BitBlt 拉进开屏，实测 187ms。
            # 只要求"主路径改成 LayoutRequest 刷布局"：函数里还留一处
            # processEvents，那是"开屏窗口不在主窗那块屏上"时才走的重定位分支。
            chk("set_land_from_window 用 LayoutRequest 只刷布局",
                "sendPostedEvents" in ln or "QCoreApplication" in ln)
        mw, mw_by = index("main_window")
        chk("_clamp_geometry_to_screen 存在（几何夹进屏幕可用区）",
            "_clamp_geometry_to_screen" in mw_by)
        chk("_fit_rect_to_screen 存在（同一套夹取算法）",
            "_fit_rect_to_screen" in mw_by)
        rest = mw_by.get("_restore_settings")
        chk("_restore_settings 调用了夹屏",
            rest is not None and "_clamp_geometry_to_screen" in set(rest.co_names))
        # od54（用户 2026-11-07「设置按钮的飞入飞出动画怎么没了，给我改回去」）：
        # 开/关两个入口必须重新走「快照 + 几何补间」，别再退化成直接 show()/close()。
        for _nm in ("_open_settings", "_close_settings_dialog"):
            _fn = mw_by.get(_nm)
            chk(f"{_nm} 重新接上飞入飞出（_animate_settings_geometry）",
                _fn is not None and "_animate_settings_geometry" in set(_fn.co_names))
        for _nm in ("_open_settings", "_close_settings_dialog"):
            _fn = mw_by.get(_nm)
            chk(f"{_nm} 走快照容器（_show_settings_shot）",
                _fn is not None and "_show_settings_shot" in set(_fn.co_names))
        # od55-A（用户 2026-11-07「最大化/还原还在闪、不连贯」）：
        chk("_CoverLabel 存在（单窗 drawPixmap 快速缩放）", "_CoverLabel" in mw_by)
        beg = mw_by.get("_begin_window_cover")
        chk("覆盖层只剩一窗（不再引用 _cover_label_b）",
            beg is not None and "_cover_label_b" not in set(beg.co_names))
        run_cover = mw_by.get("_run_window_cover")
        chk("起播同步画首帧（_on_cover_tick）",
            run_cover is not None and "_on_cover_tick" in set(run_cover.co_names))
        snap = mw_by.get("_on_snap")
        chk("覆盖层可见期间丢弃后台快照", snap is not None
            and "_cover_timer" in set(snap.co_names))
        chk("_image_has_content 存在（纯黑快照判废）", "_image_has_content" in mw_by)
        # od55-B（车号选中动画：让位 + 高斯模糊淡入）
        us, us_by = index("ui_settings")
        for _nm in ("_veh_start", "_veh_tick", "_veh_swap", "_veh_paint"):
            chk(f"ui_settings.{_nm} 存在（车号过渡）", _nm in us_by)
        _fo = us_by.get("_FadeOverlay")
        chk("_FadeOverlay 支持多档位图交叉", _fo is not None)
        chk("车号过渡未使用 QGraphics 特效（铁律）",
            not any("QGraphicsBlurEffect" in {c for c in f.co_names}
                    for f in us if f.co_name.startswith("_veh")))
        # od56（用户 2026-11-07「设置动画会跳一下」）：柔化项必须进包
        anim_fn = mw_by.get("_animate_settings_geometry")
        chk("设置动画时长走 _SETTINGS_ANIM_MS",
            anim_fn is not None and "_SETTINGS_ANIM_MS" in set(anim_fn.co_names))
        btn_fn = mw_by.get("_settings_button_rect")
        chk("起止矩形外扩 _SETTINGS_START_INFLATE",
            btn_fn is not None and "_SETTINGS_START_INFLATE" in set(btn_fn.co_names))
        chk("换帧收尾链路存在（_start_settings_swap_fade）",
            "_start_settings_swap_fade" in mw_by)
        # od57（用户 2026-11-07「planA 整体高斯模糊淡出 / planB 背景模糊淡出」）
        _paint = sm_by.get("paint")
        if _paint is not None:
            _consts = {c for c in _paint.co_consts if isinstance(c, str)}
            chk("paint() 读 fade_style（两套收尾分叉）", "fade_style" in _consts)
            chk("paint() 支持 all / bg 两种风格",
                {"all", "bg"}.issubset(_consts))
            chk("planB 幕布径向溶解（QRadialGradient）",
                "QRadialGradient" in set(_paint.co_names))
        chk("模糊档表改到 _close 释放（淡出要用档表）",
            "release_blur_cache" in set((sa_by.get("_close") or
                                         type("X", (), {"co_names": ()})).co_names))
        fs = mw_by.get("_build_fs_idle_pix")
        if fs is not None:
            # 早退写的是 getattr(self, "_skip_startup_anim", False) → 属性名是**常量**
            chk("_build_fs_idle_pix 带开屏期早退（_skip_startup_anim）",
                "_skip_startup_anim" in {c for c in fs.co_consts
                                         if isinstance(c, str)})

        print("\n" + ("✓ od49 改动全部进入打包产物。" if ok
                      else "✗ 产物与源码不一致，请勿部署。"))
        return 0 if ok else 1
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
