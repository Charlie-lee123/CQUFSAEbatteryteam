"""打包产物校验（二进制层面，不靠源码自证）——od63 起通用，含 od64 S2。

背景：od61 曾出现「打包比修复早 4 小时」——用户测的 exe 里根本没有修复。
所以这里直接从 ONEFILE exe 抽 PYZ，检查真实进包的字节码指纹：

  A. od62 交叉溶解（末段淡出覆盖层 = 还原方向闪）必须已撤销；
  B. 保留 _CoverLabel 的「保源图比例 + 对称裁边」（od58 拉伸根治）；
  C. 保留 ui_settings 车号残影基础修复（透明底 + swap 当拍 clear）；
  D. 保留进全屏压 DWM 原生动画（_land）；
  E. od64 起：S2 单窗内交叉溶解（set_blend / painter.setOpacity / 陈旧退路）；
  F. od64 起：车号让位裁剪（visible_h / skip_old / QRectF / _veh_visible_h）。

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


def run_checks(mw_codes, mw_by, us_codes, us_by) -> bool:
    ok = True

    def chk(label, cond):
        nonlocal ok
        print(f"  {'OK ' if cond else 'FAIL'} {label}")
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
    print("\n[C] ui_settings 车号选择残影基础修复")
    chk("_FadeOverlay 透明背景（WA_TranslucentBackground）",
        any("WA_TranslucentBackground" in set(c.co_names)
            for c in us_codes if c.co_name == "__init__"))
    vt = us_by.get("_veh_tick")
    chk("_veh_tick 存在", vt is not None)
    if vt:
        chk("swap 当拍 clear 覆盖层", "clear" in set(vt.co_names))
        # rows_shown 是 state["rows_shown"] 的**字符串键**，落在 co_consts，
        # 不是 co_names（属性名）；_veh_paint 是方法名，才在 co_names。
        chk("显行后跳过 _veh_paint（rows_shown 守卫）",
            "_veh_paint" in set(vt.co_names)
            and "rows_shown" in {v for v in vt.co_consts
                                 if isinstance(v, str)})

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

    # ---- F：od64 车号让位裁剪 ----
    print("\n[F] 车号让位 / 1:1 裁剪（od64 起）")
    sf = us_by.get("show_frame")
    chk("show_frame 支持 visible_h", sf is not None
        and "visible_h" in sf.co_varnames)
    chk("_veh_visible_h 存在", us_by.get("_veh_visible_h") is not None)
    jobs = us_by.get("_veh_jobs")
    chk("_veh_jobs 支持 skip_old（无旧块跳过空转）", jobs is not None
        and "skip_old" in jobs.co_varnames)
    chk("_FadeOverlay.paintEvent 用 QRectF 三参数裁剪",
        any("QRectF" in set(c.co_names) and "_visible_h" in set(c.co_names)
            for c in us_codes if c.co_name == "paintEvent"))

    # ---- G：od65/od66 车号「切换方向」(has_old=True) 残影根治 ----
    # od64 只修了 无旧块 路径（_veh_visible_h 1:1 裁剪）；有旧块(换车号/改模组数)
    # 仍把 stale rect_old 全画，下行数时旧块底部叠到已上移设置项 = 残影。
    # od65 第一版改用「动态 QRect(当前高度)」做 P1 几何 —— 但 _FadeOverlay.paintEvent
    # 的 dpr = pix.height()/rect.height() 依赖覆盖层高度==整块 rect 高度，几何被缩成
    # cur_h 后 dpr 算错，旧块被压扁成错位双份残影（用户「还是没有变化」的真因）。
    # od66 修正：P1 几何**保持整块 rect_old**、只把可见高度裁剪到 cur_h（顶对齐 1:1、
    # 不压扁），与无旧块路径同一套路。故本段验证「整块 rect_old + visible_h=cur_h 裁剪」，
    # 而非「动态 QRect」—— 后者是已否决的 bug 做法。
    print("\n[G] od66 车号切换(有旧块)残影根治（整块几何+visible_h 裁剪）")
    vp = us_by.get("_veh_paint")
    chk("_veh_paint 存在", vp is not None)
    if vp:
        names = set(vp.co_names)
        consts = {v for v in vp.co_consts if isinstance(v, str)}
        chk("P1 旧块跟随占位行高度收敛（引用 _veh_shift_ease）",
            "_veh_shift_ease" in names)
        chk("P1 旧块几何保持整块 rect_old（不动态构造 QRect 压扁）",
            "rect_old" in consts and "QRect" not in names)
        chk("P1 旧块按当前高度裁剪（show_frame 带 visible_h=cur_h）",
            "cur_h" in vp.co_varnames)
        chk("P1 读取占位行起止高度（h0/h1 字符串键）",
            {"h0", "h1"}.issubset(consts))

    # ---- H：od68 车号动画残影终治 ----
    # od67 仅 activate() 不足：QFormLayout 改 setFixedHeight 后默认复用缓存行高，
    # 下方「主题/材质强度」行重排被推迟到下一轮事件循环 → car→car 切换仍有一帧
    # 覆盖层压到主题 上。od68 改为先 invalidate 再 activate，让布局当拍重排；并加
    # 「硬约束」：覆盖层显示底部绝不允许越过主题实时顶端（统一到对话框坐标，P1/P3
    # 两条路径都夹）。本段断言这两处字节码特征。
    print("\n[H] od68 车号动画残影终治（invalidate 当拍重排 + 硬约束夹紧）")
    vt = us_by.get("_veh_tick")
    chk("_veh_tick 存在", vt is not None)
    if vt:
        vt_names = set(vt.co_names)
        chk("占位行高度变更后 invalidate + activate 让下方行当拍重排",
            "invalidate" in vt_names and "activate" in vt_names)
    vp = us_by.get("_veh_paint")
    chk("_veh_paint 存在", vp is not None)
    if vp:
        vp_names = set(vp.co_names)
        chk("硬约束夹紧：引用实时主题顶端 cmb_theme", "cmb_theme" in vp_names)
        chk("硬约束夹紧：用 mapTo 统一到对话框坐标（非 form 局部坐标）",
            "mapTo" in vp_names)
        chk("硬约束夹紧：用 QPoint 做坐标换算（避免坐标混用夹成 0）",
            "QPoint" in vp_names)

    # ---- I：od69 覆盖层残留（旧块错位叠）根治 ----
    # 根因：_veh_paint 只在 items 非空且 rect 有效时 show_frame；否则**什么都不做**，
    # 覆盖层保留上一帧（P1 的旧块 rect_old / 旧内容）。高 DPI 低帧率时 P2 的 clear
    # 被整帧跳过 → 旧块以旧几何停在屏幕上压住新块 = 用户「模糊块和清晰块错位叠」。
    # 修法：无内容时主动 overlay.clear()；has_old P3 模糊档未就绪时用清晰新块顶上。
    print("\n[I] od69 覆盖层无内容时主动清除（消除旧块错位残留）")
    vp2 = us_by.get("_veh_paint")
    if vp2:
        vp2_names = set(vp2.co_names)
        vp2_consts = {v for v in vp2.co_consts if isinstance(v, str)}
        chk("无内容时主动 overlay.clear()（不留上一帧旧块）",
            "clear" in vp2_names)
        chk("has_old P3 模糊档未就绪时用清晰新块顶上（pix_new 字符串键）",
            "pix_new" in vp2_consts)

    # ---- J：od70/od77 抓快照前藏覆盖层 + 车号下拉改 activated（断自激塌陷）----
    print("\n[J] od70/od77 抓快照前藏覆盖层 + 车号下拉 activated")
    vstart = us_by.get("_veh_start")
    chk("_veh_start 抓 pix_old 前隐藏覆盖层（含 hide）",
        vstart is not None and "hide" in set(vstart.co_names))
    vgn = us_by.get("_veh_grab_new")
    chk("_veh_grab_new 抓 pix_new 前隐藏覆盖层（含 hide）",
        vgn is not None and "hide" in set(vgn.co_names))
    act_ok = any("activated" in set(c.co_names) for c in us_by.values())
    chk("某方法里连了车号下拉 activated（断 0↔5 自激）", act_ok)

    # ---- K：od71 动画中断必须落到终态 + 诊断日志 ----
    # 根因：_veh_tick 只捕 RuntimeError 且 settle=False —— settle=False **不显行**，
    # 任何中断都把逐模组行留在 reserve 隐藏 = 模组数=N 却没有行 + 残影。
    print("\n[K] od71 动画中断落终态（settle）+ 诊断日志")
    chk("_veh_dbg 诊断日志方法存在", us_by.get("_veh_dbg") is not None)
    vt2 = us_by.get("_veh_tick")
    chk("_veh_tick 异常时写 TICK_EXC 诊断（引用 _veh_dbg）",
        vt2 is not None and "_veh_dbg" in set(vt2.co_names))
    for name in ("_veh_start", "_veh_swap", "_veh_finish"):
        c = us_by.get(name)
        chk(f"{name} 写诊断日志（引用 _veh_dbg）",
            c is not None and "_veh_dbg" in set(c.co_names))

    # ---- M：od73 断「车号下拉自激 0↔5 反复切换」----
    # 用户真机日志实测：打开面板 5 秒内 4 段 218↔0 动画反复。根因是
    # _refresh_number_combo 硬置 blockSignals(False)、把外层屏蔽提前解除，
    # 重建后 setCurrentIndex 回吐 currentIndexChanged → 被当成取消选车 → 自激。
    print("\n[M] od73 车号下拉自激环防护")
    op = us_by.get("_on_vehicle_picked")
    chk("_on_vehicle_picked 同步期间屏蔽下拉信号（含 blockSignals）",
        op is not None and "blockSignals" in set(op.co_names))
    rc = us_by.get("_refresh_number_combo")
    chk("_refresh_number_combo 保存并恢复原屏蔽状态（_prev_block 局部变量）",
        rc is not None and "_prev_block" in rc.co_varnames)

    # ---- N：od75 落定自愈 + 整块同步重绘 ----
    # 用户截图：模组数=5 却一行 Module 都没有，下方内容整片上移 + 旧帧残留成淡重影
    # （QScrollArea viewport 在高度塌陷后没全量重画）。落定时自愈行数 + 强制重绘。
    print("\n[N] od75/od76 落定自愈行数 + body/viewport 同步重绘")
    vf = us_by.get("_veh_finish")
    chk("_veh_finish 存在", vf is not None)
    if vf:
        vf_names = set(vf.co_names)
        chk("_veh_finish 自愈：行数不符时 _rebuild_module_rows",
            "_rebuild_module_rows" in vf_names)
        chk("_veh_finish 调整块重绘（含 _veh_repaint_all）",
            "_veh_repaint_all" in vf_names)
    vra = us_by.get("_veh_repaint_all")
    chk("_veh_repaint_all 存在", vra is not None)
    if vra:
        rn = set(vra.co_names)
        chk("_veh_repaint_all 含 viewport", "viewport" in rn)
        chk("_veh_repaint_all 含同步 repaint", "repaint" in rn)

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
        for mod in ("main_window", "ui_settings"):
            if mod not in toc:
                print(f"FAIL PYZ.toc 缺模块 {mod}")
                return None
        print(f"PYZ.toc 含 main_window / ui_settings（共 {len(toc)} 条）")
        mw = make_index(zar.extract("main_window"))
        us = make_index(zar.extract("ui_settings"))
        return mw[0], mw[1], us[0], us[1]
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def from_source():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = []
    for mod in ("main_window", "ui_settings"):
        path = os.path.join(root, mod + ".py")
        with open(path, "rb") as f:
            code = compile(f.read(), path, "exec")
        codes, by = make_index(code)
        print(f"[source] {mod}.py -> {len(codes)} code objects")
        out.extend((codes, by))
    return out[0], out[1], out[2], out[3]


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
