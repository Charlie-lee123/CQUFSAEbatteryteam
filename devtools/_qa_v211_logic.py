# -*- coding: utf-8 -*-
"""独立验收者（QA qa-v211）证伪探针 —— v2.11 纯逻辑层。

不复用工程师的 selftest / _verify_* 断言；逐条独立的、可证伪的探针。
本探针只碰纯逻辑（无 Qt GUI），并把 QSettings 重定向到隔离 INI，
绝不读写用户的真实注册表设置。收尾不 os._exit()。

用法：
    QT_QPA_PLATFORM=windows .venv\\Scripts\\python.exe devtools/_qa_v211_logic.py
产物：_check/_qa_v211_logic.log（由调用方重定向）
"""
from __future__ import annotations

import ast
import io
import os
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_log = []


def emit(line: str = "") -> None:
    print(line)
    _log.append(line)


def check(name: str, ok: bool, detail: str = "") -> bool:
    emit(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  | {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


_failures: list[str] = []


def iso_qsettings():
    """把所有 QSettings 重定向到隔离 INI（在 import 产品模块之前调用）。"""
    from PySide6.QtCore import QSettings
    iso = Path(ROOT) / "_check" / "_qa_v211_qsettings.ini"
    iso.parent.mkdir(exist_ok=True)
    if iso.exists():
        iso.unlink()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(iso))
    return iso


# =============================================================== 1. soc_source 文案 / 新键
def probe_customization():
    emit("\n=== [1] customization: 文案 / 新键（隔离 INI）===")
    import customization
    specs = customization.specs()
    by_key = {s["key"]: s for s in specs}

    soc = by_key.get("soc_source")
    volt = by_key.get("volt_source")
    keep = by_key.get("keep_awake_on_rx")

    check("soc_source 存在", soc is not None)
    if soc:
        labels = [lab for lab, _v in soc["options"]]
        values = [v for _lab, v in soc["options"]]
        emit(f"    soc_source labels={labels} values={values} default={soc['default']!r}")
        check("soc_source 文案=本地估算/报文读取", labels == ["本地估算", "报文读取"], str(labels))
        check("soc_source 存储值不变=calc/read", values == ["calc", "read"], str(values))
        check("soc_source 默认值不变=calc", soc["default"] == "calc", str(soc["default"]))
        check("soc_source 文案无「车端」", all("车端" not in lab for lab in labels))

    check("volt_source 是独立新键", volt is not None)
    if volt:
        labels = [lab for lab, _v in volt["options"]]
        values = [v for _lab, v in volt["options"]]
        emit(f"    volt_source labels={labels} values={values} default={volt['default']!r}")
        check("volt_source 文案=本地估算/BMS 上报", labels == ["本地估算", "BMS 上报"], str(labels))
        check("volt_source 值=calc/read", values == ["calc", "read"], str(values))
        check("volt_source 默认=calc", volt["default"] == "calc", str(volt["default"]))
        tip = str(volt.get("tip", ""))
        emit(f"    volt_source tip={tip!r}")
        check("volt_source tip 含 V= / 0.00 / 不回退 提示",
              ("V=" in tip and "0.00" in tip and ("不" in tip or "照实" in tip)), tip)

    check("keep_awake_on_rx 是独立新键", keep is not None)
    if keep:
        emit(f"    keep_awake_on_rx default={keep['default']!r}")
        check("keep_awake_on_rx 默认=False", keep["default"] is False, str(keep["default"]))

    # 读默认值：应落在 calc / False，且这些读取只发生在这两个新键上（隔离 INI）
    emit(f"    get(volt_source)={customization.get('volt_source')!r}"
         f"  get(keep_awake_on_rx)={customization.get('keep_awake_on_rx')!r}"
         f"  get(soc_source)={customization.get('soc_source')!r}")


# =============================================================== 2. soc_feed v_read 原文
def probe_soc_feed():
    emit("\n=== [2] soc_feed: V= 原文字符串（含 0.00）===")
    import soc_feed

    rc = soc_feed.RoundCollector(n_modules=1, cells_per_module=2)
    # 一整轮：电压2点 + V/I 行 + CVMax 收尾
    rounds = rc.feed("Voltage1:\n 1.3700 2.3800\nV=0.00,I=-1.50\nCVMax=3800, Min=0\n")
    emit(f"    rounds={len(rounds)}  last_v_read={rc.last_v_read!r}"
         f"  last_soc_read={rc.last_soc_read!r}")
    check("last_v_read 拿到原文 '0.00'（字符串）",
          rc.last_v_read == "0.00" and isinstance(rc.last_v_read, str), repr(rc.last_v_read))
    check("last_v_read 不是 float 0.0 的字符串化歧义",
          rc.last_v_read == "0.00", repr(rc.last_v_read))
    if rounds:
        d = rounds[0]
        emit(f"    RoundData.v_read={d.v_read!r}  i_meas={d.i_meas!r}")
        check("RoundData.v_read 随轮带走 '0.00'",
              d.v_read == "0.00" and isinstance(d.v_read, str), repr(d.v_read))
        check("i_meas 仍正确解析=-1.50（float 校验没破坏电流）",
              d.i_meas is not None and abs(d.i_meas - (-1.5)) < 1e-9, repr(d.i_meas))
    else:
        check("整轮被 CVMax 收尾吐出", False, "rounds 为空")

    # 保真：不同写法的原文必须逐字保留（证伪"转 float 再格式化"）
    for raw, want in [("3.50", "3.50"), ("0.0", "0.0"), ("00.00", "00.00"), ("12", "12")]:
        rc2 = soc_feed.RoundCollector(n_modules=1, cells_per_module=1)
        rc2.feed(f"V={raw},I=2.0\n")
        check(f"V={raw} 原文逐字保留为 {want!r}",
              rc2.last_v_read == want, repr(rc2.last_v_read))

    # 坏行不污染：V= 非数字 → 不更新 v_read
    rc2 = soc_feed.RoundCollector(n_modules=1, cells_per_module=1)
    rc2.feed("V=abc,I=2.0\n")
    check("坏 V= 行不更新 last_v_read", rc2.last_v_read is None, repr(rc2.last_v_read))

    # reset 清零
    rc.reset()
    check("reset() 清空 last_v_read", rc.last_v_read is None, repr(rc.last_v_read))


# =============================================================== 3. 工况 classify 五类
def probe_classify():
    emit("\n=== [3] soc_est.WorkModeClassifier.classify（独立直接调用）===")
    import soc_est

    dead = soc_est.MODE_I_DEADBAND_A
    emit(f"    MODE_I_DEADBAND_A={dead}  MODE_I_MIN_A={soc_est.MODE_I_MIN_A}"
         f"  MODE_HOLD_S={soc_est.MODE_HOLD_S}")
    check("死区常量从 MODE_I_MIN_A 派生且相等",
          dead == soc_est.MODE_I_MIN_A == 1.2, f"dead={dead}")

    # 每项用全新实例，ts=0 避开滞回干扰
    cases = [
        # (name, i_eff, i_valid, contactor_open, bms_ok, want)
        ("I=+5 → 放电", 5.0, True, False, True, "discharge"),
        ("I=-5 → 充电", -5.0, True, False, True, "charge"),
        ("I=0 → 静置", 0.0, True, False, True, "idle"),
        ("I=死区边界(1.2) → 静置", dead, True, False, True, "idle"),
        ("I=略高于死区(1.3) → 放电", dead + 0.1, True, False, True, "discharge"),
        ("I=略低于死区(-1.3) → 充电", -(dead + 0.1), True, False, True, "charge"),
        ("i_valid=False(I=-5) → 静置", -5.0, False, False, True, "idle"),
        ("bms_ok=False 但电流显著(I=-5) → 充电", -5.0, True, False, False, "charge"),
        ("contactor_open=True + I=99 → 母线断开(open)", 99.0, True, True, True, "open"),
        ("contactor_open=True + i_valid=False → open", 0.0, False, True, False, "open"),
    ]
    for name, i_eff, i_valid, copen, bms_ok, want in cases:
        c = soc_est.WorkModeClassifier()
        got = c.classify(ts=0.0, mean_mv=3800.0, i_eff=i_eff, i_valid=i_valid,
                         contactor_open=copen, bms_ok=bms_ok)
        check(name, got == want, f"got={got!r} want={want!r}")

    # mean<=0 → unknown（保留）
    c = soc_est.WorkModeClassifier()
    got = c.classify(0.0, 0.0, 5.0, True, False, True)
    check("mean_mv<=0 → unknown", got == "unknown", repr(got))

    # 删除项确认
    check("MODE_V_WINDOW_S 已删", not hasattr(soc_est, "MODE_V_WINDOW_S"))
    check("MODE_V_DELTA_MV 已删", not hasattr(soc_est, "MODE_V_DELTA_MV"))
    check("_trend 已删", not hasattr(soc_est.WorkModeClassifier, "_trend"))
    c = soc_est.WorkModeClassifier()
    check("_hist 已删", not hasattr(c, "_hist"))

    # open force 不受滞回：先放电(写 hold_until)，再在保持期内 open 必须立刻切
    c = soc_est.WorkModeClassifier()
    c.classify(0.0, 3800, 5.0, True, False, True)        # discharge, hold_until=30
    got = c.classify(1.0, 3800, 5.0, True, True, True)   # 保持期内 open
    check("open force 不受滞回（保持期内也立刻 open）", got == "open", repr(got))


# =============================================================== 4. wake_lock fake setter
def probe_wake_lock():
    emit("\n=== [4] wake_lock（注入 fake setter，绝不真改电源策略）===")
    import wake_lock

    calls: list[int] = []
    wl = wake_lock.WakeLock(os_setter=calls.append)

    wl.configure(True)
    check("仅 configure(True) 未 receiving → 不 acquire", calls == [], str(calls))
    wl.set_receiving(True)
    check("enabled AND receiving → acquire 一次",
          calls == [wake_lock.ACQUIRE_FLAGS], str(calls))
    wl.set_receiving(True)
    check("重复 set_receiving(True) 幂等（不重复 acquire）",
          calls == [wake_lock.ACQUIRE_FLAGS], str(calls))
    wl.set_receiving(False)
    check("停收 → release 一次",
          calls == [wake_lock.ACQUIRE_FLAGS, wake_lock.RELEASE_FLAGS], str(calls))
    wl.set_receiving(False)
    check("重复停收幂等",
          calls == [wake_lock.ACQUIRE_FLAGS, wake_lock.RELEASE_FLAGS], str(calls))

    # 关开关场景：enabled=False 时 receiving=True 不 acquire
    calls2: list[int] = []
    wl2 = wake_lock.WakeLock(os_setter=calls2.append)
    wl2.set_receiving(True)          # 先 receiving 但 enabled 默认 False
    check("enabled=False 时 receiving=True 不 acquire", calls2 == [], str(calls2))
    wl2.configure(True)             # 现在开 → acquire
    check("后开开关 → acquire 一次", calls2 == [wake_lock.ACQUIRE_FLAGS], str(calls2))
    wl2.configure(False)            # 再关 → release
    check("关开关 → release", calls2[-1] == wake_lock.RELEASE_FLAGS, str(calls2))

    # release() 强制：即使 receiving 仍 True
    calls3: list[int] = []
    wl3 = wake_lock.WakeLock(os_setter=calls3.append)
    wl3.configure(True)
    wl3.set_receiving(True)
    before = len(calls3)
    wl3.release()
    check("release() 强制恢复且 held=False",
          wl3.held is False and calls3[-1] == wake_lock.RELEASE_FLAGS
          and len(calls3) == before + 1, str(calls3))
    wl3.release()
    check("release() 幂等（held=False 再 release 不重复调 OS）",
          len(calls3) == before + 1, str(calls3))

    check("ACQUIRE_FLAGS=0x80000000|1|2",
          wake_lock.ACQUIRE_FLAGS == 0x80000000 | 0x1 | 0x2,
          hex(wake_lock.ACQUIRE_FLAGS))
    check("RELEASE_FLAGS=0x80000000", wake_lock.RELEASE_FLAGS == 0x80000000,
          hex(wake_lock.RELEASE_FLAGS))


# =============================================================== 5. marks_model
def probe_marks_model():
    emit("\n=== [5] marks_model（纯内存）===")
    from marks_model import MarkStore

    ms = MarkStore()
    check("初始空", ms.marks == [] and ms.current == -1)
    a = ms.add(10, "[12:00:00]", "Voltage1: 01.3700mV ...")
    b = ms.add(20, "[12:00:05]", "Voltage1: 02.3800mV ...")
    emit(f"    a={a}")
    emit(f"    b={b}")
    check("seq 自增 1,2", a.seq == 1 and b.seq == 2, f"{a.seq},{b.seq}")
    check("key 稳定且互异", a.key != b.key and a.key.startswith("[12:00:00]#"), f"{a.key} / {b.key}")
    check("summary 截断 24 字", len(a.summary) <= 24, str(len(a.summary)))
    check("nearest_key(15)==a.key", ms.nearest_key(15) == a.key)
    check("nearest_key(25)==b.key", ms.nearest_key(25) == b.key)
    check("nearest_key(5)==None（上方无标记）", ms.nearest_key(5) is None)
    check("by_key(b)==b", ms.by_key(b.key) == b)
    check("by_key(不存在)==None", ms.by_key("nope") is None)
    # 循环：add 后 current 落在最后一条(b)，next→a, prev→b
    check("next 循环 b→a", ms.next().key == a.key)
    check("prev 循环 a→b", ms.prev().key == b.key)
    ms.clear()
    check("clear 清空 marks/current/seq",
          ms.marks == [] and ms.current == -1 and ms._seq == 0)
    check("空列表 next()==None", ms.next() is None)


# =============================================================== 6. 车端 UI 文案扫描
def probe_cheduan_scan():
    emit("\n=== [6] 全项目「车端」扫描（只判 UI 可见字符串，注释/docstring 允许）===")
    product_files = [
        "customization.py", "soc_feed.py", "soc_est.py", "soc_display.py",
        "soc_profiles.py", "soc_ekf.py", "main_window.py", "ui_settings.py",
        "ui_highlight.py", "ui_theme.py", "ui_anim.py", "ui_controls.py",
        "serial_io.py", "rx_store.py", "rx_history.py", "wake_lock.py",
        "marks_model.py", "drv_assist.py", "perf_mode.py", "ui_surface.py",
        "ui_perf_mode.py", "main.py",
    ]
    violations = []
    comment_hits = []
    docstring_hits = []
    for rel in product_files:
        p = Path(ROOT) / rel
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        # 注释行（tokenize）
        comment_lines = set()
        try:
            for tok in tokenize.generate_tokens(io.StringIO(src).readline):
                if tok.type == tokenize.COMMENT and "车端" in tok.string:
                    comment_lines.add(tok.start[0])
        except (tokenize.TokenError, IndentationError):
            pass
        # docstring 行范围（AST）
        doc_ranges: list[tuple[int, int]] = []
        try:
            tree = ast.parse(src)
        except SyntaxError:
            violations.append(f"{rel}:AST解析失败")
            continue
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body:
                first = body[0]
                if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    doc_ranges.append((first.lineno, first.end_lineno))
        # 所有含「车端」的字符串常量
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and "车端" in node.value):
                ln = node.lineno
                in_doc = any(a <= ln <= b for a, b in doc_ranges)
                if in_doc:
                    docstring_hits.append(f"{rel}:{ln}")
                else:
                    violations.append(f"{rel}:{ln}: {node.value[:60]!r}")
        for ln in sorted(comment_lines):
            comment_hits.append(f"{rel}:{ln}")

    emit(f"    注释命中 {len(comment_hits)} 处，docstring 命中 {len(docstring_hits)} 处（设计允许保留）")
    check("UI 可见字符串里「车端」= 0", not violations, "; ".join(violations) if violations else "")
    emit("    注释命中样例: " + ", ".join(comment_hits[:6]))


# =============================================================== 7. _effective_current 逐字未动
def probe_p1_verbatim():
    emit("\n=== [7] _effective_current P1 判序源码逐字未动（对比 git HEAD）===")
    def extract(text: str) -> str:
        lines = text.splitlines()
        out, capturing = [], False
        for ln in lines:
            if "def _effective_current(" in ln:
                capturing = True
            if capturing:
                out.append(ln)
                if out and ln.startswith("    def ") and "def _effective_current(" not in ln:
                    break
                if "        return (raw - self.i_bias) * self.cfg.i_sign, True" in ln:
                    break
        return "\n".join(out)

    head = subprocess.run(["git", "show", "HEAD:soc_est.py"], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    wt = (Path(ROOT) / "soc_est.py").read_text(encoding="utf-8", errors="replace")
    h = extract(head.stdout)
    w = extract(wt)
    check("工作区 _effective_current 与 HEAD 逐字一致", h == w and len(h) > 200,
          f"head={len(h)}字 wt={len(w)}字 equal={h == w}")
    # 关键判序：contactor_open is True 必须出现在 'abs(raw) > I_LIMIT_A' 之前
    idx1 = w.find("if contactor_open is True:")
    idx2 = w.find("abs(raw) > I_LIMIT_A")
    check("P1 判序：contactor_open 在轨值判断之前", 0 <= idx1 < idx2,
          f"open@{idx1} rail@{idx2}")


def main() -> int:
    emit("QA qa-v211 独立证伪探针 —— v2.11 纯逻辑层")
    iso = iso_qsettings()
    emit(f"隔离 INI = {iso}")
    try:
        probe_customization()
        probe_soc_feed()
        probe_classify()
        probe_wake_lock()
        probe_marks_model()
        probe_cheduan_scan()
        probe_p1_verbatim()
    finally:
        # 隔离 INI 收尾：不碰真实设置；不 os._exit()
        pass

    emit("\n=== 汇总 ===")
    if _failures:
        emit(f"FAIL {len(_failures)} 项:")
        for f in _failures:
            emit(f"  - {f}")
        return 1
    emit("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
