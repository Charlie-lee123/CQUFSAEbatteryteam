# -*- coding: utf-8 -*-
r"""evidence-recorder 补证：车号动画早期「重叠」的控件级几何（只读取证）。

沿用 _evidence_vehicle.py 的 monkey-patch 手法，回答 5 问：
  ① state["pix_new"]（_veh_grab_new 里 body.grab(rect)，ui_settings.py:2144）
     位图本身画的是什么 —— 是否混入「主题/材质强度/界面材质/开屏动画」旧设置项？
  ② 同拍 overlay.geometry()/rect_new/Module field/旧设置项 label·field 几何差多少 px。
  ③ 早期真行 form.setRowVisible 状态 + 每个 Module field 的 isVisible()
     （ms 0/54/84/106/432 各采一次），核实 _veh_start 的 reserve=True 是否生效。
  ④ 判决 A(透明底叠在仍可见真控件上) / B(pix_new 位图内部串进旧设置项) / C(两者)。
  ⑤ dump _veh_grab_new 执行拍的 rect、新行 geometry、占位行 holder geometry/高度、
     以及 body.grab(rect) 结果图 —— 判断临时显形+占位在场时布局是否把新行推错位置。
  ⑥ _veh_frame_items 在 pix_new 就绪前 / 刚就绪时返回什么（sharp=None 是否空 items）。

⛔ 只读取证：运行时 monkey-patch（内存），不写任何产品文件、不 git、不打包、
   不动 发布 与 dist*、不跑 selftest、不让用户参与。
⛔ QSettings 全量快照 + finally 还原 + 外层 pickle 兜底；_check/.app_run.lock 互斥。
⛔ 所有 QImage 只存内存 bytes，quit 后统一写盘（避开动画热路径 PNG 编码）。
产物：_check/evidence_vehicle_geom/

用法：.venv/Scripts/python.exe devtools/_evidence_vehicle_geom.py
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

OUT = os.path.join(_ROOT, "_check", "evidence_vehicle_geom")
os.makedirs(OUT, exist_ok=True)
LOCK = os.path.join(_ROOT, "_check", ".app_run.lock")

_SNAPSHOT: dict = {}
IMGS: dict[str, tuple[int, int, bytes]] = {}   # name -> (w,h,RGBA8888 bytes)
RECT_CALLS: list[dict] = []
FRAME_ITEMS: list[dict] = []
GEOM: list[dict] = []                          # 目标 ms 的控件几何快照
GRABNEW: list[dict] = []
EVENTS: list[tuple[float, str]] = []
_TARGETS = [0.0, 54.0, 84.0, 106.0, 432.0]
_done_targets: set[float] = set()
_IN_GRAB = {"v": False}
_ARMED = {"v": False}          # ⛔ 只在「无数据→有数据」目标方向采样
_DUMPED = {"layout": False}    # 抓图时刻整幅布局图只 dump 一次
_MAX_W = 1200


def _hit(ms: float, t: float) -> bool:
    """0/54/84/106 用 ±8 精确采样；432 附近实测有 70~79ms 的 tick 空档
    （如 408.3→478.3），改用「首个 ≥t-14 的 tick」兜底，绝不提前触发。"""
    if t >= 400:
        return (t - 14.0) <= ms <= (t + 90.0)
    return abs(ms - t) <= 8.0


def _log(tag: str) -> None:
    EVENTS.append((_ms(), tag))
    print(f"EVENT {_ms():7.1f} {tag}", flush=True)


def _acquire_lock(wait_s: float = 240.0) -> bool:
    t0 = time.time()
    while True:
        try:
            os.mkdir(LOCK)
            with open(os.path.join(LOCK, "owner.txt"), "w", encoding="utf-8") as fh:
                fh.write("evidence-vehicle-geom pid=%d\n" % os.getpid())
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
    from PySide6.QtWidgets import (QApplication, QFormLayout, QLabel,
                                  QScrollArea)

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
    print(f"ENV dpr={dpr} hz={ui_anim.frame_hz()} "
          f"TOTAL={ui_settings._VEH_TOTAL_MS} SHIFT={ui_settings._VEH_SHIFT_MS}",
          flush=True)

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

    S = {"dialog": None, "name": None, "body": None, "region": None}

    # ---- 通用几何工具（相对 body 的坐标）----
    def _body():
        d = S["dialog"]
        if d is None:
            return None
        try:
            return d.form.parentWidget()
        except Exception:                                # noqa: BLE001
            return None

    def _geo(w):
        """(x,y,w,h,visible,hidden) 相对 body；w=None 返回 None。"""
        b = _body()
        if w is None or b is None:
            return None
        try:
            p = w.mapTo(b, QPoint(0, 0))
            return (p.x(), p.y(), w.width(), w.height(),
                    bool(w.isVisible()), bool(w.isHidden()))
        except RuntimeError:
            return None

    def _rows_geo():
        d = S["dialog"]
        out = []
        try:
            for i, (lab, fld) in enumerate(d._module_rows):
                out.append({"i": i, "label": _geo(lab), "field": _geo(fld)})
        except Exception:                                # noqa: BLE001
            pass
        return out

    def _legacy_geo():
        """_custom_rows 里的旧设置项 (label, field) 几何。"""
        d = S["dialog"]
        out = []
        try:
            for key, pair in d._custom_rows.items():
                lab, fld = pair
                out.append({"key": key,
                            "label_text": lab.text() if lab is not None else "",
                            "label": _geo(lab), "field": _geo(fld)})
        except Exception:                                # noqa: BLE001
            pass
        return out

    def _form_rows_geo():
        """按 QFormLayout 行号枚举**整张表单**，补齐 _custom_rows 之外的
        「主题 / 材质强度 / 开屏动画 / 动画文件」等旧设置项与占位行。"""
        d = S["dialog"]
        out = []
        try:
            form = d.form
            try:
                LBL = QFormLayout.ItemRole.LabelRole
                FLD = QFormLayout.ItemRole.FieldRole
            except AttributeError:                       # noqa: BLE001
                LBL, FLD = QFormLayout.LabelRole, QFormLayout.FieldRole
            for row in range(form.rowCount()):
                li = form.itemAt(row, LBL)
                fi = form.itemAt(row, FLD)
                lw = li.widget() if li is not None else None
                fw = fi.widget() if fi is not None else None
                lt = lw.text() if isinstance(lw, QLabel) else ""
                out.append({"row": row, "text": lt,
                            "label": _geo(lw), "field": _geo(fw)})
        except Exception:                                # noqa: BLE001
            pass
        return out

    def _holder_geo(state):
        h = state.get("holder") if state else None
        return _geo(h)

    def _overlay_geo():
        d = S["dialog"]
        try:
            ov = d._veh_overlay()
            return _geo(ov)
        except Exception:                                # noqa: BLE001
            return None

    def _ov_state():
        """覆盖层自身状态：α / items 数 / bg 是否 None / 是否透明底。"""
        d = S["dialog"]
        try:
            ov = d._veh_overlay()
            return {"alpha": round(float(getattr(ov, "_alpha", 0.0)), 4),
                    "n_items": len(getattr(ov, "_items", []) or []),
                    "bg_none": bool(getattr(ov, "_bg", None) is None),
                    "translucent": bool(ov.testAttribute(
                        Qt.WidgetAttribute.WA_TranslucentBackground))}
        except Exception:                                # noqa: BLE001
            return {}

    def _store_png(name, pix, limit_w=_MAX_W):
        if pix is None or pix.isNull():
            return False
        try:
            img = pix.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            if img.width() > limit_w:
                img = img.scaledToWidth(
                    limit_w, Qt.TransformationMode.SmoothTransformation
                ).convertToFormat(QImage.Format.Format_RGBA8888)
            ww, hh = img.width(), img.height()
            if ww <= 0 or hh <= 0:
                return False
            IMGS[name] = (ww, hh, bytes(img.constBits()))
            return True
        except Exception:                                # noqa: BLE001
            return False

    def _region():
        d = S["dialog"]
        if d is None:
            return None
        try:
            body = d.form.parentWidget()
            area = d.findChild(QScrollArea)
            bar = area.verticalScrollBar()
            r = QRect(0, bar.value(), body.width(), area.viewport().height()) \
                & body.rect()
            S["body"] = body
            S["region"] = r
            return r
        except Exception:                                # noqa: BLE001
            return None

    def _scroll_to_vehicle():
        d = S["dialog"]
        if d is None:
            return
        try:
            area = d.findChild(QScrollArea)
            cap = next((l for l in d.findChildren(QLabel)
                        if l.text().startswith("车辆（电池箱）")), None)
            if cap is not None:
                area.ensureWidgetVisible(cap, 0, 30)
        except Exception:                                # noqa: BLE001
            pass
        _region()

    # ---------------- 包装 ----------------
    _orig_rows_rect = ui_settings.SettingsDialog._module_rows_rect
    _orig_grab_new = ui_settings.SettingsDialog._veh_grab_new
    _orig_frame_items = ui_settings.SettingsDialog._veh_frame_items
    _orig_paint = ui_settings.SettingsDialog._veh_paint
    _orig_tick = ui_settings.SettingsDialog._veh_tick
    _orig_swap = ui_settings.SettingsDialog._veh_swap

    def _wrap_rows_rect(self):
        r = _orig_rows_rect(self)
        if not _ARMED["v"]:
            return r
        rec = {"wall": _ms(), "in_grab": _IN_GRAB["v"],
               "rect": (r.x(), r.y(), r.width(), r.height()),
               "null": bool(r.isNull()),
               "rows": _rows_geo(), "legacy": _legacy_geo()}
        st = self._veh_state()
        rec["holder"] = _holder_geo(st)
        RECT_CALLS.append(rec)
        # ⑤ 抓图时刻：新行临时显形 + 占位行在场时，整幅 body 可视区合成图。
        #    _veh_grab_new 在这之后才 body.grab(rect)，此刻行仍可见、布局已 activate。
        if _IN_GRAB["v"] and not _DUMPED["layout"]:
            _DUMPED["layout"] = True
            try:
                rr = S["region"]
                if rr is not None and not rr.isEmpty():
                    _store_png("grabnew_layout.png", S["body"].grab(rr))
            except Exception:                            # noqa: BLE001
                pass
        return r

    def _wrap_grab_new(self, state):
        _IN_GRAB["v"] = True
        _log("grab_new START")
        try:
            out = _orig_grab_new(self, state)
        finally:
            _IN_GRAB["v"] = False
        p = state.get("pix_new")
        rn = state.get("rect_new")
        ok = bool(p is not None and not p.isNull())
        if not _ARMED["v"]:
            _log(f"grab_new DONE ok={ok} armed=False（非目标方向，跳过采样）")
            return out
        # ① dump：state["pix_new"] 本身就是 body.grab(rect) 的结果
        if ok:
            _store_png("grabnew_pix_new.png", p, limit_w=_MAX_W)
        gr = {"wall": _ms(), "ok": ok, "armed": _ARMED["v"],
              "pix": (f"{p.width()}x{p.height()}" if ok else "null"),
              "rect_new": ((rn.x(), rn.y(), rn.width(), rn.height())
                           if rn is not None and not rn.isNull() else None),
              "rect_calls_in_grab": [c for c in RECT_CALLS if c["in_grab"]],
              "rows_after": _rows_geo(), "holder_after": _holder_geo(state),
              "legacy_after": _legacy_geo()}
        GRABNEW.append(gr)
        _log(f"grab_new DONE ok={ok} pix={gr['pix']} rect_new={gr['rect_new']}")
        return out

    def _wrap_frame_items(self, state, which, sigma):
        items = _orig_frame_items(self, state, which, sigma)
        if _ARMED["v"]:
            sharp = state.get("pix_old" if which == "old" else "pix_new")
            FRAME_ITEMS.append({
                "ms": round(getattr(_wrap_frame_items, "cur_ms", -1.0), 1),
                "which": which, "sigma": round(float(sigma), 2),
                "sharp_none": bool(sharp is None),
                "n_levels": len(state.get(f"levels_{which}") or []),
                "n_items": len(items),
                "first_is_sharp": bool(items and items[0][0] is sharp),
                "weights": [round(float(w), 3) for _, w in items],
            })
        return items

    def _wrap_paint(self, state, ms):
        _wrap_frame_items.cur_ms = ms
        out = _orig_paint(self, state, ms)
        if not _ARMED["v"]:
            return out
        # 目标 ms 拍：dump 覆盖层自身渲染 + body 可视区合成图
        for t in _TARGETS:
            if _hit(ms, t) and ("paint", t) not in _done_targets:
                _done_targets.add(("paint", t))
                tag = f"t{int(t):03d}"
                try:
                    ov = self._veh_overlay()
                    _store_png(f"{tag}_overlay_own.png", ov.grab())
                except Exception:                        # noqa: BLE001
                    pass
                r = S["region"]
                if r is not None and not r.isEmpty():
                    _store_png(f"{tag}_composite.png", S["body"].grab(r))
                break
        return out

    def _wrap_tick(self, state, k):
        ms = k * float(ui_settings._VEH_TOTAL_MS)
        _wrap_frame_items.cur_ms = ms
        before = bool(state.get("rows_shown"))
        out = _orig_tick(self, state, k)
        after = bool(state.get("rows_shown"))
        if not _ARMED["v"]:
            return out
        # ③ 目标 ms 拍：真行可见性 + 全几何
        for t in _TARGETS:
            if _hit(ms, t) and ("geom", t) not in _done_targets:
                _done_targets.add(("geom", t))
                snap = {
                    "target_ms": t, "actual_ms": round(ms, 1),
                    "rows_shown": after, "before": before,
                    "all_rows_visible": [
                        {"i": i, "label_vis": g["label"][4] if g["label"] else None,
                         "field_vis": g["field"][4] if g["field"] else None,
                         "label_geo": g["label"], "field_geo": g["field"]}
                        for i, g in enumerate(_rows_geo())],
                    "overlay": _overlay_geo(),
                    "rect_new": ((state["rect_new"].x(), state["rect_new"].y(),
                                  state["rect_new"].width(), state["rect_new"].height())
                                 if state.get("rect_new") is not None
                                 and not state["rect_new"].isNull() else None),
                    "holder": _holder_geo(state),
                    "legacy": _legacy_geo(),
                    "pix_new_ready": bool(state.get("pix_new") is not None
                                          and not state["pix_new"].isNull()),
                    "pix_new_size": (
                        (state["pix_new"].width(), state["pix_new"].height())
                        if state.get("pix_new") is not None
                        and not state["pix_new"].isNull() else None),
                    "pix_old_ready": bool(state.get("pix_old") is not None
                                          and not state["pix_old"].isNull()),
                    "has_old": bool(state.get("pix_old") is not None
                                    and not state["rect_old"].isNull()),
                    "levels_new": len(state.get("levels_new") or []),
                    "levels_old": len(state.get("levels_old") or []),
                    "ov": _ov_state(),
                    "form_rows": _form_rows_geo(),
                }
                GEOM.append(snap)
                _log(f"GEOM@{t:.0f} actual_ms={ms:.1f} "
                     f"rows_vis={[r['field_vis'] for r in snap['all_rows_visible']]}")
                break
        return out

    def _wrap_swap(self, state):
        _log("veh_swap")
        return _orig_swap(self, state)

    ui_settings.SettingsDialog._module_rows_rect = _wrap_rows_rect
    ui_settings.SettingsDialog._veh_grab_new = _wrap_grab_new
    ui_settings.SettingsDialog._veh_frame_items = _wrap_frame_items
    ui_settings.SettingsDialog._veh_paint = _wrap_paint
    ui_settings.SettingsDialog._veh_tick = _wrap_tick
    ui_settings.SettingsDialog._veh_swap = _wrap_swap

    # ---------------- 触发 ----------------
    def _open():
        _log("_open_settings()")
        window._open_settings()
        S["dialog"] = window._settings_dialog
        _log(f"dialog={S['dialog'] is not None}")
        QTimer.singleShot(500, _scroll_to_vehicle)

    def _prime_empty():
        d = S["dialog"]
        if d is None:
            return
        names = list(customization.vehicle_names() or [])
        S["name"] = customization.current_vehicle() or (names[0] if names else None)
        _ARMED["v"] = False          # 置空方向（有数据→无数据）不采样
        _log(f"置空 基线车={S['name']!r}（不采样）")
        customization.select_vehicle("")
        d._sync_vehicle_rows()

    def _target():
        d = S["dialog"]
        if d is None or not S["name"]:
            return
        _region()
        _ARMED["v"] = True           # ⛔ 只采「无数据→有数据」目标方向
        _log(f"ARMED 真实路径 select_vehicle({S['name']!r}) + _sync_vehicle_rows()")
        customization.select_vehicle(S["name"])
        d._sync_vehicle_rows()
        _log("_sync_vehicle_rows 返回")

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
            print(f"[warn] restore: {exc!r}", flush=True)
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


# ---------------------------------------------------------------- 写盘 + 报告
def _dump_and_analyze() -> None:
    from PySide6.QtGui import QImage
    print(f"STAGE: dumping {len(IMGS)} PNGs", flush=True)
    for name, (w, h, data) in IMGS.items():
        QImage(data, w, h, QImage.Format.Format_RGBA8888).copy().save(
            os.path.join(OUT, name))
    with open(os.path.join(OUT, "geom.json"), "w", encoding="utf-8") as fh:
        json.dump({"grabnew": GRABNEW, "rect_calls": RECT_CALLS,
                   "frame_items": FRAME_ITEMS, "geom": GEOM,
                   "events": EVENTS, "imgs": sorted(IMGS)}, fh,
                  ensure_ascii=False, indent=1)

    L = []
    def say(s=""):
        print(s, flush=True)
        L.append(s)

    def fmt(g):
        return "-" if g is None else f"({g[0]},{g[1]},{g[2]}x{g[3]}) vis={g[4]} hid={g[5]}"

    def alpha_stats(name):
        """统计已写盘 PNG 的 alpha 分布 —— 判 A 型（透明底透出真控件）的关键数值。"""
        path = os.path.join(OUT, name)
        if not os.path.exists(path):
            return None
        img = QImage(path)
        if img.isNull():
            return None
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
        n = img.width() * img.height()
        a0 = apartial = a255 = total = 0
        for y in range(img.height()):
            for x in range(img.width()):
                a = (img.pixel(x, y) >> 24) & 0xFF
                total += a
                if a < 8:
                    a0 += 1
                elif a >= 250:
                    a255 += 1
                else:
                    apartial += 1
        return {"w": img.width(), "h": img.height(),
                "pct_transparent": round(100.0 * a0 / n, 2),
                "pct_partial": round(100.0 * apartial / n, 2),
                "pct_opaque": round(100.0 * a255 / n, 2),
                "mean_alpha": round(total / n, 2)}

    say("# 车号动画早期重叠：控件级几何补证（od63 代码，只读取证）\n")
    say(f"- 产物目录：`_check/evidence_vehicle_geom/`；PNG {len(IMGS)} 张、"
        f"rect 调用 {len(RECT_CALLS)} 次、frame_items {len(FRAME_ITEMS)} 次、"
        f"几何快照 {len(GEOM)} 拍。")
    say("- 手法：沿用 `_evidence_vehicle.py` 的运行时 monkey-patch；"
        "所有 QImage 存内存、quit 后统一写盘，不在动画热路径编码 PNG。\n")

    # ---- ① pix_new 位图 ----
    say("## ① `state[\"pix_new\"]` 位图内容（`_veh_grab_new` ui_settings.py:2144）\n")
    for g in GRABNEW:
        say(f"- grab_new 调用（armed=True 目标方向）：wall={g['wall']:.0f}ms "
            f"ok={g['ok']} pix={g['pix']} rect_new={g['rect_new']}")
        say(f"  → dump：`grabnew_pix_new.png` = `body.grab(rect)` 的**原始结果**"
            f"（未经任何叠加）；`grabnew_layout.png` = 抓图那一拍整幅 body 可视区"
            f"（新行临时显形 + 占位行在场）")
        say(f"  → 尺寸说明：dpr=2 时 `body.grab(rect)` 返回**设备像素**位图，"
            f"宽高各为 rect 的 2 倍；`drawPixmap(self.rect(), pix)` 会缩回逻辑尺寸。")
        say(f"  → 抓图返回后新行重新隐藏："
            f"{[fmt(r['field']) for r in g['rows_after']]}")
    a = alpha_stats("grabnew_pix_new.png")
    if a:
        say(f"- **`grabnew_pix_new.png` alpha 统计**：{a['w']}x{a['h']}，"
            f"全透明 {a['pct_transparent']}%、半透明 {a['pct_partial']}%、"
            f"不透明 {a['pct_opaque']}%、平均 alpha {a['mean_alpha']}")
        say("  → 全透明占比高 = `body.grab()` 抓到的是**带透明底的位图**；覆盖层又是"
            "`WA_TranslucentBackground` + `bg=None`，这些空洞会让底下真控件透出来（A 型）。")
    say("- `grabnew_layout.png` alpha 统计："
        + str(alpha_stats("grabnew_layout.png")))
    if not GRABNEW:
        say("- ⛔ 未捕获目标方向的 grab_new（动画没跑或 jobs 未执行）")

    say("\n### 覆盖层自身渲染（透明底证据）\n")
    for t in _TARGETS:
        name = f"t{int(t):03d}_overlay_own.png"
        a = alpha_stats(name)
        if a:
            say(f"- `{name}` {a['w']}x{a['h']}：全透明 {a['pct_transparent']}%、"
                f"半透明 {a['pct_partial']}%、不透明 {a['pct_opaque']}%、"
                f"平均 alpha {a['mean_alpha']}")

    # ---- ⑤ 抓图时刻布局 ----
    say("\n## ⑤ 抓图时刻布局（rect / 新行 / 占位行）\n")
    ingrab = [c for c in RECT_CALLS if c["in_grab"]]
    say(f"- `_module_rows_rect()` 在 grab_new 内被调用 {len(ingrab)} 次：")
    for c in ingrab:
        say(f"  - rect={c['rect']} null={c['null']} wall={c['wall']:.0f}ms")
        for r in c["rows"]:
            say(f"      Module{r['i']+1} label={fmt(r['label'])} "
                f"field={fmt(r['field'])}")
        say(f"      holder(占位行)={fmt(c['holder'])}")
        for lg in c["legacy"][:6]:
            say(f"      旧设置项[{lg['key']}] {lg['label_text']!r} "
                f"label={fmt(lg['label'])} field={fmt(lg['field'])}")
    for g in GRABNEW:
        say(f"- grab_new 返回后：rows_after="
            f"{[fmt(r['field']) for r in g['rows_after']]} "
            f"holder_after={fmt(g['holder_after'])}")
        say(f"  rect_new={g['rect_new']}；body.grab(rect_new) 即 pix_new。")

    # ---- ③ 真行可见性 ----
    say("\n## ③ 早期真行可见性（`reserve=True` 是否生效）\n")
    say("| 目标ms | 实际ms | rows_shown | 各 Module field isVisible | "
        "pix_new就绪 | levels_new | has_old | ov α/items/bgNone | overlay rect | holder |")
    say("|---|---|---|---|---|---|---|---|---|---|")
    for g in sorted(GEOM, key=lambda x: x["target_ms"]):
        vis = [r["field_vis"] for r in g["all_rows_visible"]]
        ov = g.get("ov", {})
        say(f"| {g['target_ms']:.0f} | {g['actual_ms']} | {g['rows_shown']} | "
            f"{vis} | {g['pix_new_ready']} | {g['levels_new']} | "
            f"{g.get('has_old')} | {ov.get('alpha')}/{ov.get('n_items')}/"
            f"{ov.get('bg_none')} | {fmt(g['overlay'])} | {fmt(g['holder'])} |")
    for g in sorted(GEOM, key=lambda x: x["target_ms"]):
        say(f"\n- t={g['target_ms']:.0f}ms 逐行几何：")
        for r in g["all_rows_visible"]:
            say(f"    Module{r['i']+1} label={fmt(r['label_geo'])} "
                f"field={fmt(r['field_geo'])}")
        say(f"    overlay={fmt(g['overlay'])}  rect_new={g['rect_new']}  "
            f"holder={fmt(g['holder'])}")
        for lg in g["legacy"][:6]:
            say(f"    旧项[{lg['key']}] {lg['label_text']!r} "
                f"label={fmt(lg['label'])} field={fmt(lg['field'])}")

    # ---- ② 几何差 ----
    say("\n## ② 覆盖层矩形 vs 真控件矩形（px 差 / 竖向相交）\n")
    say("- overlay 与 `rect_new` 同矩形；`_FadeOverlay` 是 body 的子控件且 `raise_()`，"
        "盖在真控件之上。`bg=None` + 透明底时，只有位图像素不透明处才遮住真控件。\n")
    for g in sorted(GEOM, key=lambda x: x["target_ms"]):
        ov = g["overlay"]
        if ov is None:
            continue
        ox, oy, ow, oh = ov[0], ov[1], ov[2], ov[3]
        say(f"- t={g['target_ms']:.0f}ms overlay=({ox},{oy},{ow}x{oh}) "
            f"vis={ov[4]} hid={ov[5]} rect_new={g['rect_new']} "
            f"ov={g.get('ov')}")
        for r in g["all_rows_visible"]:
            f = r["field_geo"]
            if f is None:
                continue
            say(f"    Module{r['i']+1} field={fmt(f)} → Δy={f[1]-oy} "
                f"isVisible={r['field_vis']}")
        for lg in g["legacy"][:6]:
            lb = lg["label"]
            if lb is None:
                continue
            top = max(oy, lb[1])
            bot = min(oy + oh, lb[1] + lb[3])
            inter = max(0, bot - top)
            say(f"    旧项[{lg['key']}]{lg['label_text']!r} label={fmt(lb)} → "
                f"Δy={lb[1]-oy} 与 overlay 竖向相交={inter}px"
                + ("  ⚠️落在覆盖层矩形内" if inter > 0 else ""))

    # ---- ②b 整张表单逐行：谁落在覆盖层矩形内 ----
    say("\n## ②b 整张表单逐行 vs overlay 矩形（补齐 _custom_rows 之外的旧项）\n")
    say("- 覆盖层矩形固定为 `rect_new=(0,139,473x218)`，即 y∈[139,357)。"
        "下列逐行来自 `QFormLayout.itemAt(row, LabelRole/FieldRole)`，"
        "覆盖「主题/材质强度/界面材质/开屏动画/动画文件」等未登记进 `_custom_rows` 的行。\n")
    for g in sorted(GEOM, key=lambda x: x["target_ms"]):
        ov = g["overlay"]
        if ov is None:
            continue
        oy, oh = ov[1], ov[3]
        say(f"- t={g['target_ms']:.0f}ms (overlay vis={ov[4]}, α={g.get('ov',{}).get('alpha')})：")
        for fr in g.get("form_rows", []):
            lb = fr["label"]
            if lb is None:
                continue
            top = max(oy, lb[1])
            bot = min(oy + oh, lb[1] + lb[3])
            inter = max(0, bot - top)
            if lb[1] > oy + oh + 80:      # 太远的截断，聚焦重叠区
                continue
            say(f"    row{fr['row']:>2} {fr['text']!r:<16} "
                f"label={fmt(lb)} field={fmt(fr['field'])} "
                f"与overlay相交={inter}px"
                + ("  ⚠️重叠" if inter > 0 else ""))

    # ---- ⑥ frame_items ----
    say("\n## ⑥ `_veh_frame_items` 在 pix_new 就绪前 / 刚就绪时返回什么\n")
    new_items = [f for f in FRAME_ITEMS if f["which"] == "new"]
    say(f"- 目标方向共 {len(FRAME_ITEMS)} 次调用，"
        f"其中 `which='new'` {len(new_items)} 次（旧方向已排除）。下表为最早 20 次：")
    say("| ms | σ | sharp=None | n_levels(new) | n_items | 首项=sharp | 权重 |")
    say("|---|---|---|---|---|---|---|")
    for f in new_items[:20]:
        say(f"| {f['ms']} | {f['sigma']} | {f['sharp_none']} | "
            f"{f['n_levels']} | {f['n_items']} | {f['first_is_sharp']} | "
            f"{f['weights']} |")
    pre = [f for f in new_items if f["sharp_none"]]
    post = [f for f in new_items if not f["sharp_none"] and f["n_levels"] == 0]
    say(f"\n- **pix_new 就绪前**：`sharp=None` 的 new 调用 {len(pre)} 次，"
        f"n_items={sorted(set(f['n_items'] for f in pre))} → "
        f"返回 `[]`（2193-2194），`show_frame` 不被调用，overlay 保持隐藏。")
    say(f"- **pix_new 刚就绪、`levels_new` 未生成**：{len(post)} 帧，"
        f"n_items={sorted(set(f['n_items'] for f in post))}、"
        f"first_is_sharp={sorted(set(str(f['first_is_sharp']) for f in post))}、"
        f"σ 范围={min((f['sigma'] for f in post), default=None)}~"
        f"{max((f['sigma'] for f in post), default=None)} → "
        f"返回 `[(sharp,1.0)]`（2195-2196） = **直接 α 混合清晰原图，没有高斯模糊**。")
    say("- 判据（ui_settings.py:2178-2203）：`sharp=None` 且 `levels=[]` → "
        "`lo=None,hi=None` → 返回 `[]`（2193-2194）；`sharp` 刚就绪但 "
        "`levels_new` 未生成 → `hi=None` → 返回 `[(sharp,1.0)]`（2195-2196）；"
        "`σ≤0.15` 亦返回 `[(sharp,1.0)]`（2183-2184）。")
    say("- 补充：`_veh_jobs()` 的 job[0] 是 `grab_new`，由 `_veh_pump` 在 "
        "**第一个 ms>0.5 的 tick** 执行；`blur_old` 共 5 个 job 因 `pix_old=None` "
        "全部空转，`blur_new` 要排到其后 → `levels_new` 生成明显滞后于 pix_new。")

    # ---- ④ 判决指引 ----
    say("\n## ④ 判决（A/B/C）—— 需结合肉眼看图\n")
    say("- **A 型**（透明底把半透明 pix_new 叠在仍可见真控件上）："
        "`grabnew_pix_new.png` 干净（只有 Module 行），但 "
        "`t054_composite.png`（body.grab region，含底下真控件）出现双份文字；"
        "且 ③ 表中早期 Module field `isVisible=True`（reserve 失效）或旧项 label "
        "落在 overlay 矩形内。")
    say("- **B 型**（pix_new 位图内部串进旧设置项）："
        "`grabnew_pix_new.png` 本身已含「主题/材质强度/…」文字 → "
        "⑤ 的 rect 把旧设置项区域框进去了。")
    say("- **C 型**：两者同时成立。")
    say("- 证据图：`grabnew_pix_new.png`、`t054_overlay_own.png`"
        "（覆盖层自身渲染，透明底）、`t054_composite.png`（合成）。")
    say("- 关键背景：od62 把 `_FadeOverlay` 改 `WA_TranslucentBackground`+`bg=None`"
        "（ui_settings.py:1096、2303），`_veh_paint` 无旧块分支 α=1-(1-p)^3 "
        "（2299）从 0 起；若 A 成立，透明底让底下真控件在 α<1 时透出来 = 双份。")

    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
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
