# -*- coding: utf-8 -*-
r"""evidence-recorder 逐拍取证②：车号选择动画（只读取证，不改产品源码）。

回答用户「车号选择是有残影」「从未选择到选中车号的时候车辆数据选择动画还是不对劲」。
聚焦**无数据 → 有数据**方向（用户点名），逐 _veh_tick 拍抓「设置面板 body 可视区」
自身渲染（body.grab(region)，覆盖层 _veh_mask 是 body 子控件且 raise_，会被一起
抓到 → 能拍到「覆盖层位图 + 真行」的重叠/错位）。

重点帧：_veh_swap + overlay.clear() 当拍（ui_settings.py:2222-2233，ms>=680）
        前后各 2~3 帧，以及全高斯淡入过程（P3 320~680ms）。

⛔ 干扰声明：逐拍 grab 给每拍增加数 ms，动画被轻微拉长；swap/clear/rows_shown
   是离散状态翻转，不受拉长影响。未包装真实时序由现成 _record_vehicle.py 屏幕录屏旁证。
⛔ 只读取证：运行时 monkey-patch（内存），不写任何产品文件、不 git、不打包。
⛔ QSettings 全量快照 + finally 还原（删新增键、实例关闭后新开核对）。
⛔ _check/.app_run.lock 互斥（≤240s）。产物 _check/evidence_vehicle/。

用法：.venv/Scripts/python.exe devtools/_evidence_vehicle.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time

_T0 = time.perf_counter()


def _ms() -> float:
    return (time.perf_counter() - _T0) * 1000.0


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

OUT = os.path.join(_ROOT, "_check", "evidence_vehicle")
os.makedirs(OUT, exist_ok=True)
LOCK = os.path.join(_ROOT, "_check", ".app_run.lock")

_SNAPSHOT: dict = {}
FRAMES: list[dict] = []          # {seq,w,h,data(RGBA8888),meta}
EVENTS: list[tuple[float, str]] = []
_SEQ = {"n": 0}
_MAX_W = 1000


def _log(tag: str) -> None:
    EVENTS.append((_ms(), tag))
    print(f"EVENT {_ms():7.1f} {tag}", flush=True)


def _acquire_lock(wait_s: float = 240.0) -> bool:
    t0 = time.time()
    while True:
        try:
            os.mkdir(LOCK)
            with open(os.path.join(LOCK, "owner.txt"), "w", encoding="utf-8") as fh:
                fh.write("evidence-vehicle pid=%d\n" % os.getpid())
            return True
        except FileExistsError:
            if time.time() - t0 > wait_s:
                return False
            time.sleep(1.0)


def _release_lock() -> None:
    try:
        shutil.rmtree(LOCK, ignore_errors=True)
    except Exception:                                    # noqa: BLE001
        pass


def _restore_settings() -> None:
    from PySide6.QtCore import QSettings
    st = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st.allKeys()) - set(_SNAPSHOT):
        st.remove(k)
    for k, v in _SNAPSHOT.items():
        st.setValue(k, v)
    st.sync()
    del st
    chk = QSettings("MimoWorks", "SerialAssistant")
    chk.sync()
    print(f"[核对] geometry={chk.value('geometry')!r} "
          f"vehicle={chk.value('custom/vehicle')!r} "
          f"startup_maximized={chk.value('custom/startup_maximized')!r}", flush=True)
    del chk


# ---------------------------------------------------------------- 子进程 app
def run_app() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(50, exit=True)
    from PySide6.QtCore import QPoint, QRect, QSettings, Qt, QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})
    del st

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    import ui_anim
    import ui_settings
    import customization
    from main_window import MainWindow

    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    scr = app.primaryScreen()
    sg = scr.geometry()
    dpr = float(scr.devicePixelRatio())
    _VEH_TOTAL_MS = float(ui_settings._VEH_TOTAL_MS)
    print(f"ENV dpr={dpr} hz={ui_anim.frame_hz()} "
          f"_VEH_TOTAL_MS={_VEH_TOTAL_MS}", flush=True)

    window = MainWindow()
    try:
        window._port_watch.stop()
    except Exception:                                    # noqa: BLE001
        pass
    window.setGeometry(sg.x() + 120, sg.y() + 60, 1134, 820)
    window.show()
    window.raise_()
    window.activateWindow()
    _log("window_shown")

    _state = {"dialog": None, "name": None, "body": None, "scroll": None,
              "region": None}

    # ---- 抓 body 可视区（含覆盖层 + 真行）----
    def _refresh_region():
        d = _state["dialog"]
        if d is None:
            return
        try:
            body = d.form.parentWidget()
            scroll = d.findChild(QScrollArea)
            bar = scroll.verticalScrollBar()
            view_h = scroll.viewport().height()
            region = QRect(0, bar.value(), body.width(), view_h) & body.rect()
            _state["body"] = body
            _state["scroll"] = scroll
            _state["region"] = region
        except Exception:                                # noqa: BLE001
            _state["body"] = None
            _state["region"] = None

    def _scroll_to_vehicle():
        d = _state["dialog"]
        if d is None:
            return
        try:
            scroll = d.findChild(QScrollArea)
            cap = next((l for l in d.findChildren(QLabel)
                        if l.text().startswith("车辆（电池箱）")), None)
            if cap is not None:
                scroll.ensureWidgetVisible(cap, 0, 30)
        except Exception:                                # noqa: BLE001
            pass
        _refresh_region()

    def _grab(meta: dict):
        body = _state["body"]
        region = _state["region"]
        pix = None
        if body is not None and region is not None and not region.isEmpty():
            try:
                pix = body.grab(region)
            except Exception:                            # noqa: BLE001
                pix = None
        if pix is None or pix.isNull():
            try:
                pix = _state["dialog"].grab()
            except Exception:                            # noqa: BLE001
                return
        if pix is None or pix.isNull():
            return
        img = pix.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        if img.width() > _MAX_W:
            img = img.scaledToWidth(
                _MAX_W, Qt.TransformationMode.SmoothTransformation
            ).convertToFormat(QImage.Format.Format_RGBA8888)
        ww, hh = img.width(), img.height()
        if ww <= 0 or hh <= 0:
            return
        m = dict(meta)
        m.update(dpr=dpr, img_w=ww, img_h=hh)
        _SEQ["n"] += 1
        FRAMES.append({"seq": _SEQ["n"], "w": ww, "h": hh,
                       "data": bytes(img.constBits()), "meta": m})

    def _overlay_meta() -> dict:
        d = _state["dialog"]
        out = {"ov_items": -1, "ov_alpha": -1.0, "ov_vis": False}
        try:
            ov = d._veh_overlay()
            if ov is not None:
                out["ov_items"] = len(getattr(ov, "_items", []) or [])
                out["ov_alpha"] = round(float(getattr(ov, "_alpha", 0.0)), 3)
                out["ov_vis"] = bool(ov.isVisible())
        except Exception:                                # noqa: BLE001
            pass
        return out

    # ---- 包装 _veh_tick / _veh_swap / _veh_paint ----
    _orig_tick = ui_settings.SettingsDialog._veh_tick
    _orig_swap = ui_settings.SettingsDialog._veh_swap
    _orig_paint = ui_settings.SettingsDialog._veh_paint
    _armed = {"on": False, "swap_seq": None}

    def _wrap_swap(self, state):
        _log("veh_swap 当拍 start")
        _orig_swap(self, state)
        _armed["swap_seq"] = _SEQ["n"] + 1
        _log(f"veh_swap 当拍 done rows_shown={state.get('rows_shown')}")

    def _wrap_paint(self, state, ms):
        _orig_paint(self, state, ms)

    def _wrap_tick(self, state, k):
        t0 = time.perf_counter()
        before = bool(state.get("rows_shown"))
        ms = k * _VEH_TOTAL_MS
        _orig_tick(self, state, k)                       # 先让状态/显隐落定
        after = bool(state.get("rows_shown"))
        ovm = _overlay_meta()
        m = {"wall": _ms(), "ms": round(ms, 1), "k": round(k, 4),
             "rows_shown_before": before, "rows_shown_after": after,
             "swap_frame": (not before and after),
             "tag": "veh_tick"}
        m.update(ovm)
        m["cost_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        if _armed["on"]:
            _grab(m)
        if not before and after:
            _log(f"SWAP 帧 ms={ms:.0f} 覆盖层 items={ovm['ov_items']} "
                 f"alpha={ovm['ov_alpha']} vis={ovm['ov_vis']}")

    ui_settings.SettingsDialog._veh_tick = _wrap_tick
    ui_settings.SettingsDialog._veh_swap = _wrap_swap
    ui_settings.SettingsDialog._veh_paint = _wrap_paint

    # ---- 触发（真入口 + 真实路径）----
    def _open():
        _log("_open_settings() 调用")
        window._open_settings()
        _state["dialog"] = window._settings_dialog
        _log(f"_open_settings 返回 dialog={_state['dialog'] is not None}")
        QTimer.singleShot(500, _scroll_to_vehicle)

    def _prime_empty():
        d = _state["dialog"]
        if d is None:
            _log("⛔ 无 dialog，跳过置空")
            return
        names = list(customization.vehicle_names() or [])
        _state["name"] = (customization.current_vehicle()
                          or (names[0] if names else None))
        _log(f"置空（基线车={_state['name']!r} names={names}）")
        customization.select_vehicle("")
        d._sync_vehicle_rows()
        _log("置空完成（等其动画跑完再触发目标方向）")

    def _target():
        d = _state["dialog"]
        if d is None:
            _log("⛔ 无 dialog，跳过目标")
            return
        name = _state["name"]
        if not name:
            _log("⛔ 没有可用车号，无法复现无数据→有数据")
            return
        _refresh_region()
        # 动作前基线帧（同步重建在第一拍 tick 之前就改完，必须留一帧基线）
        base = _overlay_meta()
        base.update(wall=_ms(), ms=-1.0, k=-1.0,
                    rows_shown_before=False, rows_shown_after=False,
                    swap_frame=False, tag="BASE_before_action")
        _grab(base)
        _armed["on"] = True
        _log(f"真实路径 select_vehicle({name!r}) + _sync_vehicle_rows()"
             f"（无数据→有数据）")
        customization.select_vehicle(name)
        d._sync_vehicle_rows()
        _log("_sync_vehicle_rows 返回（动画开始）")
        # 落定后再补一帧静止基线
        QTimer.singleShot(int(_VEH_TOTAL_MS) + 500, _settle)

    def _settle():
        m = _overlay_meta()
        m.update(wall=_ms(), ms=_VEH_TOTAL_MS + 500, k=2.0,
                 rows_shown_before=True, rows_shown_after=True,
                 swap_frame=False, tag="SETTLED")
        _grab(m)
        _armed["on"] = False
        _log("落定帧已抓，停止逐拍")

    def _quit():
        _log("quit")
        try:
            _dump_and_analyze()
        except Exception as exc:                         # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"[warn] analyze: {exc!r}", flush=True)
        try:
            _restore_settings()
        except Exception as exc:                         # noqa: BLE001
            print(f"[warn] restore settings: {exc!r}", flush=True)
        _release_lock()
        sys.stdout.flush()
        os._exit(0)

    _wd = threading.Timer(34.0, lambda: os._exit(3))
    _wd.daemon = True
    _wd.start()
    QTimer.singleShot(900, _open)
    QTimer.singleShot(2100, _prime_empty)
    QTimer.singleShot(3400, _target)
    QTimer.singleShot(6400, _quit)
    print("STAGE: timers scheduled", flush=True)
    app.exec()
    _wd.cancel()
    return 0


# ---------------------------------------------------------------- 写盘 + 分析
def _dump_and_analyze() -> None:
    import cv2
    import numpy as np
    from PySide6.QtGui import QImage

    print(f"STAGE: dumping {len(FRAMES)} frames", flush=True)
    metas = [fr["meta"] for fr in FRAMES]

    # 归一化灰度（帧尺寸基本一致；仍统一 resize 到 400x250 保险）
    grays = []
    for fr in FRAMES:
        a = np.frombuffer(fr["data"], dtype=np.uint8).reshape(fr["h"], fr["w"], 4)
        g = cv2.cvtColor(a[:, :, :3], cv2.COLOR_RGB2GRAY)
        grays.append(cv2.resize(g, (400, 250), interpolation=cv2.INTER_AREA))
    final = grays[-1] if grays else None
    diffs = []
    for i in range(1, len(grays)):
        dp = cv2.absdiff(grays[i], grays[i - 1])
        df = cv2.absdiff(grays[i], final) if final is not None else dp
        diffs.append({"i": i, "seq": FRAMES[i]["seq"],
                      "area_prev": float((dp > 25).mean()),
                      "area_final": float((df > 25).mean()),
                      "mean_prev": float(dp.mean()),
                      "dluma": float(grays[i].mean()) - float(grays[i-1].mean())})

    # 导出关键帧 PNG：基线 / 全帧抽稀 / swap 当拍前后 3 帧 / 落定
    swap_idx = next((i for i, m in enumerate(metas) if m.get("swap_frame")), None)
    want = {0, len(FRAMES) - 1}
    if swap_idx is not None:
        for j in range(max(0, swap_idx - 3), min(len(FRAMES), swap_idx + 4)):
            want.add(j)
    for j in range(0, len(FRAMES), max(1, len(FRAMES) // 12)):
        want.add(j)
    for i in sorted(want):
        fr = FRAMES[i]
        qimg = QImage(fr["data"], fr["w"], fr["h"],
                      QImage.Format.Format_RGBA8888).copy()
        qimg.save(os.path.join(
            OUT, f"v{i:03d}_{fr['meta'].get('tag','x')}.png"))

    with open(os.path.join(OUT, "frames.json"), "w", encoding="utf-8") as fh:
        json.dump({"frames": [{k: v for k, v in fr.items() if k != "data"}
                              for fr in FRAMES],
                   "events": EVENTS, "swap_idx": swap_idx}, fh,
                  ensure_ascii=False, indent=1)

    lines = []
    def say(s=""):
        print(s, flush=True)
        lines.append(s)

    say("# evidence-vehicle：车号选择动画逐拍取证（od63 代码，只读取证）\n")
    say("- 方向：**未选择 → 选中车号**（无数据→有数据，用户点名）。")
    say("- 每拍抓设置面板 body 可视区（body.grab(region)，覆盖层 _veh_mask 是"
        " body 子控件且 raise_，会与真行一起入画 → 能拍到重叠/错位）。")
    say(f"- 共 {len(FRAMES)} 帧 / {len(EVENTS)} 事件；"
        f"swap 当拍 index={swap_idx}。")
    say("- ⚠️ 逐拍 grab 给每拍增加数 ms，动画被轻微拉长；swap/clear/rows_shown"
        " 是离散状态翻转，不受拉长影响。未包装时序见 _record_vehicle.py 屏幕录屏。\n")

    say("## 逐拍序列（ms / rows_shown / 覆盖层 items·alpha·可见 / 帧间差分）\n")
    say("| i | tag | ms | rows_shown 前→后 | swap当拍 | ov_items | ov_alpha | "
        "ov_vis | grab耗时 | 对上帧diff% | 对落定帧diff% | Δluma |")
    say("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, fr in enumerate(FRAMES):
        m = fr["meta"]
        d = diffs[i - 1] if i >= 1 else {}
        say(f"| {i} | {m.get('tag','')} | {m.get('ms','')} | "
            f"{m.get('rows_shown_before','')}→{m.get('rows_shown_after','')} | "
            f"{m.get('swap_frame','')} | {m.get('ov_items','')} | "
            f"{m.get('ov_alpha','')} | {m.get('ov_vis','')} | "
            f"{m.get('cost_ms','')} | "
            f"{round(d.get('area_prev',0)*100,2) if d else ''} | "
            f"{round(d.get('area_final',0)*100,2) if d else ''} | "
            f"{round(d.get('dluma',0),2) if d else ''} |")

    say("\n## swap 当拍（_veh_swap + overlay.clear）前后 3 帧\n")
    if swap_idx is not None:
        for j in range(max(0, swap_idx - 3), min(len(FRAMES), swap_idx + 4)):
            m = metas[j]
            d = diffs[j - 1] if j >= 1 else {}
            flag = " <== SWAP 当拍" if j == swap_idx else ""
            say(f"- i={j} seq={FRAMES[j]['seq']} ms={m.get('ms')} "
                f"rows_shown {m.get('rows_shown_before')}→{m.get('rows_shown_after')} "
                f"ov(items={m.get('ov_items')}, alpha={m.get('ov_alpha')}, "
                f"vis={m.get('ov_vis')}) "
                f"对上帧diff={round(d.get('area_prev',0)*100,2) if d else 0}% "
                f"对落定diff={round(d.get('area_final',0)*100,2) if d else 0}%{flag}")
    else:
        say("- ⛔ 未捕获 swap 当拍（rows_shown 未翻转 / 动画没跑）")

    say("\n## 重影/双影判据\n")
    # 判据 A：swap 后是否仍有「覆盖层可见且 alpha>0」的帧（真行已显形 → 重叠）
    if swap_idx is not None:
        post = [(j, metas[j]) for j in range(swap_idx, len(FRAMES))
                if metas[j].get("tag") == "veh_tick"]
        overlap = [(j, m.get("ms"), m.get("ov_alpha"), m.get("ov_vis"))
                   for j, m in post
                   if m.get("ov_vis") and (m.get("ov_alpha") or 0) > 0.02]
        say(f"- swap 当拍及之后，覆盖层仍可见且 alpha>0.02 的帧："
            f"{overlap or '无（overlay.clear 当拍已撤，符合修复预期）'}")
        say(f"  → 若为空，说明「显真行 + 清覆盖层」同拍完成，无重叠窗口残影。")
    # 判据 B：帧间内容突变最大帧
    top = sorted(diffs, key=lambda x: x["area_prev"], reverse=True)[:8]
    say("\n- 帧间内容突变最大的 8 帧（归一化 400x250，|diff|>25 面积%）：")
    for d in top:
        m0, m1 = metas[d["i"] - 1], metas[d["i"]]
        say(f"  - seq {FRAMES[d['i']-1]['seq']}→{d['seq']}: "
            f"面积={d['area_prev']*100:.1f}% Δluma={d['dluma']:+.1f} "
            f"ms {m0.get('ms')}→{m1.get('ms')} "
            f"swap {m0.get('swap_frame')}→{m1.get('swap_frame')}")
    # 判据 C：对落定帧差异是否单调下降（非单调 = 中途出现「先像落定又退回去」= 闪/重影）
    af = [d["area_final"] for d in diffs]
    if af:
        rises = [(i, af[i] - af[i - 1]) for i in range(1, len(af))
                 if af[i] > af[i - 1] + 0.03]
        say(f"\n- 对落定帧差异「不降反升」的帧（>3% 反弹）："
            f"{[(FRAMES[i+1]['seq'], round(r*100,1)) for i, r in rises[:12]] or '无'}")
        say("  → 反弹帧 = 画面突然「离落定态更远」，可能对应残影/闪/错位一帧。")

    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"STAGE: report -> {os.path.join(OUT,'report.md')}", flush=True)


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    from PySide6.QtCore import QSettings
    _st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: _st.value(k) for k in _st.allKeys()})
    del _st
    if "--app" in sys.argv:
        run_app()
        os._exit(0)
    if not _acquire_lock():
        print("LOCK busy (240s)", flush=True)
        sys.exit(2)
    try:
        run_app()
    finally:
        try:
            _restore_settings()
            print("[兜底] QSettings 已按父进程快照恢复", flush=True)
        except Exception as exc:                         # noqa: BLE001
            print(f"[warn] {exc!r}", flush=True)
        _release_lock()
    sys.exit(0)
