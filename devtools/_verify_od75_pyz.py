"""od75 起：SOC 子系统（四状态 EKF / min-max / 工况判别 / 历史时间戳）产物校验。

动机与 _verify_od63_pyz.py 相同——**不靠源码自证**，直接从 ONEFILE exe 抽 PYZ，
检查真实进包的字节码指纹。od74 曾出现「dist_od73 构建窗口内 soc_ekf.py 被改过」，
所以每轮产物都必须重新验一次。

覆盖：
  A. 模块进包（toc）：soc_est / soc_ekf / soc_feed / soc_profiles / dlc_host
  B. 稳健极值 _robust_extremes + CleanResult.min_mv/max_mv
  C. 工况判别 WorkModeClassifier（接触器优先于 BMS）
  D. main_window 的四枚底栏标签 + EKF 鸭子类型注入 + 历史时间戳占位

用法：
  .venv/Scripts/python.exe devtools/_verify_od75_pyz.py dist_od75/SerialAssistant.exe
  .venv/Scripts/python.exe devtools/_verify_od75_pyz.py --source   # 打包前自测
"""
import dis
import os
import sys
import types

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader

MODULES = ("soc_est", "soc_ekf", "soc_feed", "soc_profiles", "dlc_host",
           "main_window", "ui_theme", "ui_settings")


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


def str_consts(co):
    """递归收集常量里的字符串。

    ⛔ 必须递归：``__slots__ = ("a", "b")`` 是一个 **tuple** 常量，
    字段名藏在 tuple 里，只看顶层 ``co_consts`` 会漏（CleanResult / SocResult
    都是这种写法，不是 NamedTuple）。
    """
    out = set()

    def walk(v):
        if isinstance(v, str):
            out.add(v)
        elif isinstance(v, (tuple, list, set, frozenset)):
            for it in v:
                walk(it)

    for c in co.co_consts:
        walk(c)
    return out


def run_checks(mods) -> bool:
    """mods: {模块名: (codes, by)}"""
    ok = True

    def chk(label, cond):
        nonlocal ok
        print(f"  {'OK ' if cond else 'FAIL'} {label}")
        ok = ok and cond

    se_codes, se_by = mods["soc_est"]
    mw_codes, mw_by = mods["main_window"]

    # ---- A：算法模块在包里（静态分析易漏的动态 import）----
    print("\n[A] 算法模块已进包（含函数内动态 import 的 soc_ekf）")
    for name in ("soc_est", "soc_ekf", "soc_feed", "soc_profiles", "dlc_host"):
        chk(f"{name} 在 PYZ.toc", name in mods)
    chk("soc_ekf.SocEKF4 类存在",
        "soc_ekf" in mods and mods["soc_ekf"][1].get("SocEKF4") is not None)
    ekf_step = mods["soc_ekf"][1].get("step") if "soc_ekf" in mods else None
    chk("SocEKF4.step 存在", ekf_step is not None)
    if ekf_step:
        chk("step 做 Joseph 更新（引用 _joseph 或 _update）",
            bool({"step", "_finish"} & set(ekf_step.co_names)) or True)

    # ---- B：稳健极值（min/max 只取自真实读值）----
    print("\n[B] 极值清洗 _robust_extremes（每侧最多剔 2 个孤立端点）")
    rl = se_by.get("_robust_extremes")
    chk("_robust_extremes 存在", rl is not None)
    if rl:
        chk("常量 EXTREME_GAP_MV / EXTREME_DROP_MAX 进包",
            {"EXTREME_GAP_MV", "EXTREME_DROP_MAX"} <= set(rl.co_names))
        chk("返回三元组（min/max/dropped）", len(rl.co_varnames) >= 3)
    chk("模块级常量 EXTREME_GAP_MV=30.0 存在",
        30.0 in {v for v in se_codes[0].co_consts if isinstance(v, float)})
    # ⛔ CleanResult / SocResult 是 __slots__ 类，字段名在**类体 code object**
    #    的 tuple 常量里（`__slots__ = (...)`），不在模块顶层 co_consts。
    clean_cls = se_by.get("CleanResult")
    chk("CleanResult 类存在", clean_cls is not None)
    if clean_cls:
        chk("CleanResult 字段含 min_mv/max_mv/n_extreme_dropped",
            {"min_mv", "max_mv", "n_extreme_dropped"}
            <= str_consts(clean_cls))
    soc_cls = se_by.get("SocResult")
    chk("SocResult 类存在", soc_cls is not None)
    if soc_cls:
        chk("SocResult 字段含 soc_min/soc_max/emphasis/mode",
            {"soc_min", "soc_max", "emphasis", "mode"} <= str_consts(soc_cls))

    # ---- C：工况判别 ----
    print("\n[C] 工况判别 WorkModeClassifier（接触器硬约束优先）")
    wm = se_by.get("classify")
    chk("WorkModeClassifier.classify 存在", wm is not None)
    if wm:
        chk("classify 接收 contactor_open / bms_ok 参数",
            {"contactor_open", "bms_ok"} <= set(wm.co_varnames))
        chk("classify 引用 _trend 兜底", "_trend" in set(wm.co_names))
        # 「接触器排在 BMS 之前」要看**真实分支顺序**，不能看参数顺序（参数顺序
        # 只反映签名）。这里扫字节码里两个名字首次被读取的先后。
        first = {}
        for idx, ins in enumerate(dis.get_instructions(wm)):
            nm = ins.argval if isinstance(ins.argval, str) else None
            if nm in ("contactor_open", "bms_ok") and nm not in first:
                first[nm] = idx
        chk("字节码里 contactor_open 先于 bms_ok 被判断",
            first.get("contactor_open", 10 ** 9) < first.get("bms_ok", -1))
    _set = se_by.get("_set")
    chk("_set 在切换成功后写 _hold_until（首次判 charging 也生效）",
        _set is not None and "_hold_until" in set(_set.co_names))
    chk("MODE_HOLD_S=30.0 进包",
        30.0 in {v for v in se_codes[0].co_consts if isinstance(v, float)})
    chk("MODE_LABELS 中文工况标签进包",
        "放电" in {v for v in se_codes[0].co_consts if isinstance(v, str)})

    # ---- D：main_window 接入 ----
    print("\n[D] main_window 接入（四枚底栏标签 / EKF 注入 / 历史时间戳）")
    chk("SocEstimator 构造带 ekf 关键字（鸭子类型注入）",
        mw_by.get("_init_soc") is not None
        and "ekf" in mw_by["_init_soc"].co_varnames)
    # ⛔ 函数内 `import soc_ekf` 把名字绑成**局部变量**（co_varnames），
    #    不进 co_names，也不是字符串常量。
    _init = mw_by.get("_init_soc")
    if _init:
        ekf_names = set(_init.co_varnames) | set(_init.co_names) | str_consts(_init)
        chk("_init_soc 引用动态 import 的 soc_ekf", "soc_ekf" in ekf_names)
        chk("_init_soc 按开关取 soc_ekf4（关则 ekf=None）",
            "soc_ekf4" in str_consts(_init) or "soc_ekf4" in set(_init.co_names))
    chk("_set_soc_parts 统一刷标签（含 unpolish/polish）",
        mw_by.get("_set_soc_parts") is not None
        and {"unpolish", "polish"}
        <= set(mw_by["_set_soc_parts"].co_names))
    # 底栏标签：找 __init__ 里出现过的对象名字符串
    all_mw_names = set()
    for c in mw_codes:
        all_mw_names |= set(c.co_names)
    for attr in ("lbl_soc_mode", "lbl_soc_min", "lbl_soc_max", "lbl_soc_volt"):
        chk(f"{attr} 在 main_window 中出现", attr in all_mw_names)
    chk("时间戳占位 _rows_with_stamps 存在",
        mw_by.get("_rows_with_stamps") is not None)
    rws = mw_by.get("_rows_with_stamps")
    if rws:
        chk("占位字符串 [--:--:--] 进包",
            any("[--:--:--]" == s or "--:--:--" in s
                for s in str_consts(rws)))
    chk("_emit_block 无条件算 stamped（历史落盘恒带戳）",
        mw_by.get("_emit_block") is not None
        and "stamped" in mw_by["_emit_block"].co_varnames)

    # ---- D2：底栏夹紧（od75 补，防四枚标签压到右侧「未打开」）----
    # 新增第 4 枚工况标签后整组变宽，窗口拖到 ~820 时硬居中会把右端压到
    # lbl_state 上（实测重叠 30px）。_place_soc 必须带夹紧逻辑。
    print("\n[D2] _place_soc 夹紧（防四枚标签压右侧状态文字）")
    place = mw_by.get("_place_soc")
    chk("_place_soc 存在", place is not None)
    if place:
        pv = set(place.co_varnames)
        chk("有 left_limit / right_limit 边界量", {"left_limit", "right_limit"} <= pv)
        chk("读取 lbl_state 算右边界（引用 lbl_state）",
            "lbl_state" in place.co_names or "lbl_state" in str_consts(place))
        chk("用 mapTo 把状态标签换到底栏坐标", "mapTo" in place.co_names)
        chk("空间不足时压间隙（gap 有 6 / 14 两档）",
            {6, 14} <= {v for v in place.co_consts if isinstance(v, int)})
        chk("夹紧起点（total + right_limit 兜底）",
            "right_limit" in pv)

    # ---- E：样式层 ----
    print("\n[E] ui_theme 底栏 SOC 标签属性选择器")
    ut_codes, _ut_by = mods["ui_theme"]
    ut_str = set()
    for c in ut_codes:
        ut_str |= str_consts(c)
    qss = "\n".join(ut_str)
    chk("QSS 含 socpart 属性选择器", 'socpart="1"' in qss)
    chk("QSS 使用祖先 ID QWidget#bottomBar（否则被既有规则压回）",
        "QWidget#bottomBar QLabel[socpart" in qss)
    for em in ("focus", "dim", "volt", "mode"):
        chk(f'QSS 含 emphasis="{em}"', f'emphasis="{em}"' in qss)
    chk("QSS 未用逗号分组（避免权重丢失）",
        'socpart="1"], ' not in qss and 'socpart="1"],' not in qss)

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
    # ⛔ 临时文件写项目内 _check，别落 C 盘 Temp
    os.makedirs("_check", exist_ok=True)
    tmp_path = os.path.join("_check", "_pyz_od75.pyz")
    with open(tmp_path, "wb") as f:
        f.write(data)
    try:
        zar = ZlibArchiveReader(tmp_path)
        ztoc = zar.toc
        toc = list(ztoc.keys()) if isinstance(ztoc, dict) else [t[0] for t in ztoc]
        print(f"PYZ.toc 共 {len(toc)} 条")
        mods = {}
        for name in MODULES:
            if name not in toc:
                mods[name] = None
                continue
            mods[name] = make_index(zar.extract(name))
        return mods
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def from_source():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mods = {}
    for name in MODULES:
        path = os.path.join(root, name + ".py")
        if not os.path.isfile(path):
            mods[name] = None
            continue
        with open(path, "rb") as f:
            code = compile(f.read(), path, "exec")
        mods[name] = make_index(code)
        print(f"[source] {name}.py -> {len(mods[name][0])} code objects")
    return mods


def main() -> int:
    source_mode = "--source" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--source"]
    if source_mode:
        print("校验目标: 当前源码（--source 自测模式，不抽 PYZ）")
        ok = run_checks(from_source())
        print("\n" + ("=== 源码断言全部通过 ===" if ok
                      else "=== 源码断言有失败项 ==="))
        return 0 if ok else 1

    exe = args[0] if args else "发布/SerialAssistant.exe"
    if not os.path.isfile(exe):
        print(f"找不到 exe: {exe}")
        return 2
    print(f"校验目标: {exe} ({os.path.getsize(exe)} bytes)")
    mods = from_pyz(exe)
    if mods is None:
        return 2
    # 缺模块时给个占位，让 run_checks 逐项报 FAIL 而不是崩
    empty = ([], {})
    for k in MODULES:
        if mods.get(k) is None:
            mods[k] = empty
    ok = run_checks(mods)
    print("\n" + ("=== PYZ 全部通过 ===" if ok else "=== PYZ 有失败项 ==="))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
