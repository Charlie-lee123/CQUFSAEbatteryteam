"""设置面板「选车号」动画改造的只读勘察探针（不改产品源码）。

    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_vehicle_geom.py

量四类数字：
  A) 几何：未选车 / 3 行 / 6 行 / 12 行时，逐模组行区域的高度、下面的行各下移多少；
     面板尺寸、滚动区视口、body 高度与滚动范围。
  B) 代价：整表单重排一次要多久；抓「模组行区域」要多久。
  C) 每帧重排压测：用 QTimer 等价节奏逐帧改 verticalSpacing（= 整表单重排 + 整视口
     重绘，比"只动一个容器高度"更重），统计帧间隔 p50/p95/max。
  D) 高斯：splash_motion._gaussian 在模组行区域尺寸上的单次耗时、8 档预生成总耗时，
     以及"先降采样再模糊"的对照。
"""
from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "_check" / "vehicle_anim"
QSET_ORG, QSET_APP = "MimoWorks", "SerialAssistant"


def _qsnap() -> dict:
    from PySide6.QtCore import QSettings
    s = QSettings(QSET_ORG, QSET_APP)
    data = {k: s.value(k) for k in s.allKeys()}
    s.sync()
    return data


def _qrestore(snap: dict) -> bool:
    from PySide6.QtCore import QSettings
    s = QSettings(QSET_ORG, QSET_APP)
    for key in list(s.allKeys()):
        if key not in snap:
            s.remove(key)
    for key, value in snap.items():
        s.setValue(key, value)
    s.sync()
    del s
    gc.collect()
    s2 = QSettings(QSET_ORG, QSET_APP)
    return {k: s2.value(k) for k in s2.allKeys()} == snap


def _settle(app, dialog, rounds: int = 8) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent
    for _ in range(rounds):
        QCoreApplication.sendPostedEvents(None, int(QEvent.Type.LayoutRequest))
        app.processEvents()
        if dialog is not None:
            dialog.layout().activate()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", default="3,6,12")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--frames", type=int, default=40)
    args = ap.parse_args()
    row_counts = [int(v) for v in args.rows.split(",") if v.strip()]

    OUT.mkdir(parents=True, exist_ok=True)
    snap = _qsnap()
    from PySide6.QtWidgets import QApplication, QFormLayout, QScrollArea
    from PySide6.QtGui import QImage, QPixmap

    app = QApplication(sys.argv[:1])
    import customization
    import ui_theme
    ui_theme.apply_scheme("dark")

    report: dict = {}
    try:
        names = {}
        for n in row_counts:
            name = f"T{n}-2026"
            customization.save_vehicle(name, [25] * n, [8] * n)
            names[n] = name

        from main_window import BUTTON_SPECS
        from ui_settings import SettingsDialog

        dialog = SettingsDialog("dark", 60, BUTTON_SPECS, (), parent=None)
        dialog.setModal(False)
        dialog.move(-4000, -4000)
        dialog.show()
        _settle(app, dialog)

        dpr = dialog.devicePixelRatioF()
        scroll = dialog.findChild(QScrollArea)
        body = scroll.widget()
        report["dialog"] = {
            "dpr": dpr,
            "size": [dialog.width(), dialog.height()],
            "size_hint": [dialog.sizeHint().width(), dialog.sizeHint().height()],
            "min_size": [dialog.minimumSize().width(), dialog.minimumSize().height()],
            "viewport": [scroll.viewport().width(), scroll.viewport().height()],
            "body_min_hint": [body.minimumSizeHint().width(),
                              body.minimumSizeHint().height()],
            "form_hspacing": dialog.form.horizontalSpacing(),
            "form_vspacing": dialog.form.verticalSpacing(),
            "module_row_base": dialog._module_row_base,
            "form_rows_empty": None,
        }

        def snapshot(tag: str) -> dict:
            rect = dialog._module_rows_rect()
            rows = []
            for i in range(dialog.form.rowCount()):
                item = dialog.form.itemAt(i, QFormLayout.ItemRole.LabelRole)
                fitem = dialog.form.itemAt(i, QFormLayout.ItemRole.FieldRole)
                fw = fitem.widget() if fitem else None
                label = ""
                if item is not None and item.widget() is not None:
                    w = item.widget()
                    label = w.text() if hasattr(w, "text") else ""
                rows.append({"row": i, "label": label,
                             "y": fw.y() if fw is not None else None,
                             "h": fw.height() if fw is not None else None})
            sb = scroll.verticalScrollBar()
            return {"tag": tag,
                    "module_rect": [rect.x(), rect.y(), rect.width(), rect.height()],
                    "module_rows": len(dialog._module_rows),
                    "form_size_hint_h": dialog.form.sizeHint().height(),
                    "body_size": [body.width(), body.height()],
                    "body_min_hint_h": body.minimumSizeHint().height(),
                    "scroll_max": int(sb.maximum()),
                    "scroll_value": int(sb.value()),
                    "rows": rows}

        states = {}
        # ① 未选车
        customization.select_vehicle("")
        dialog._sync_vehicle_rows()
        _settle(app, dialog)
        states["(未选择)"] = snapshot("(未选择)")
        # ② 逐档选车（只看模组数不同的车）
        for n in row_counts:
            customization.select_vehicle(names[n])
            t0 = time.perf_counter()
            dialog._sync_vehicle_rows()
            _settle(app, dialog)
            cost = (time.perf_counter() - t0) * 1000.0
            st = snapshot(names[n])
            st["sync_cost_ms_incl_settle"] = round(cost, 2)
            states[names[n]] = st
        report["states"] = states

        # 按**标签**算下移量（行号会因为插行而整体错位，不能按行号比）
        def by_label(st) -> dict:
            out = {}
            for r in st["rows"]:
                key = r["label"]
                if key in out:
                    key = f"{key}#{r['row']}"
                out[key] = r
            return out

        keys = list(states)
        deltas = {}
        for a, b in zip(keys, keys[1:]):
            la, lb = by_label(states[a]), by_label(states[b])
            moved = []
            for k in la:
                if k in lb and la[k]["y"] is not None and lb[k]["y"] is not None:
                    dy = lb[k]["y"] - la[k]["y"]
                    if dy:
                        moved.append({"label": k, "dy_logical": dy})
            deltas[f"{a} -> {b}"] = moved
        report["row_deltas"] = deltas

        # B) 代价
        costs = {"relayout": [], "grab_module": [], "grab_body": [],
                 "viewport_grab": []}
        for _ in range(args.reps):
            t0 = time.perf_counter()
            dialog.form.invalidate()
            dialog.layout().activate()
            body.adjustSize()
            app.processEvents()
            costs["relayout"].append((time.perf_counter() - t0) * 1000.0)
            rect = dialog._module_rows_rect()
            t0 = time.perf_counter()
            body.grab(rect)
            costs["grab_module"].append((time.perf_counter() - t0) * 1000.0)
            t0 = time.perf_counter()
            body.grab()
            costs["grab_body"].append((time.perf_counter() - t0) * 1000.0)
            t0 = time.perf_counter()
            scroll.viewport().grab()
            costs["viewport_grab"].append((time.perf_counter() - t0) * 1000.0)
        report["costs_ms"] = {k: {"p50": round(statistics.median(v), 2),
                                  "min": round(min(v), 2),
                                  "max": round(max(v), 2)}
                              for k, v in costs.items()}

        # C) 每帧重排压测：逐帧改 verticalSpacing（整表单重排 + 视口重绘）
        def tween_probe(mutate, frames: int) -> dict:
            iv = []
            for i in range(frames):
                k = i / max(1, frames - 1)
                t0 = time.perf_counter()
                mutate(k)
                dialog.update()
                app.processEvents()
                iv.append((time.perf_counter() - t0) * 1000.0)
            s = sorted(iv)
            n = len(s)
            return {"n": n, "p50": round(s[n // 2], 2),
                    "p95": round(s[min(n - 1, int(n * 0.95))], 2),
                    "max": round(s[-1], 2),
                    "sum": round(sum(s), 2)}

        base = tween_probe(lambda k: None, args.frames)
        vs = tween_probe(
            lambda k: dialog.form.setVerticalSpacing(12 + int(round(24 * k))),
            args.frames)

        # 带**同步重绘**的同款压测：repaint() 会当场把整窗画进 backing store，
        # 比 update() 更接近真机上每帧要付的代价（offscreen 窗口也会真画）。
        def tween_paint(mutate, frames: int) -> dict:
            iv = []
            for i in range(frames):
                k = i / max(1, frames - 1)
                t0 = time.perf_counter()
                mutate(k)
                dialog.repaint()
                iv.append((time.perf_counter() - t0) * 1000.0)
            s = sorted(iv)
            n = len(s)
            return {"n": n, "p50": round(s[n // 2], 2),
                    "p95": round(s[min(n - 1, int(n * 0.95))], 2),
                    "max": round(s[-1], 2), "sum": round(sum(s), 2)}

        vs_paint = tween_paint(
            lambda k: dialog.form.setVerticalSpacing(12 + int(round(24 * k))),
            args.frames)
        dialog.form.setVerticalSpacing(12)
        _settle(app, dialog)
        report["frame_probe_ms"] = {"noop": base, "vspacing_relayout": vs,
                                    "vspacing_relayout_repaint": vs_paint}

        # D) 高斯
        import numpy as np
        import splash_motion
        import ui_backdrop
        rect = dialog._module_rows_rect()
        sizes = [(6, int(rect.width() * dpr), int(rect.height() * dpr))]
        n_cur = len(dialog._module_rows)
        sizes = [(n_cur, int(rect.width() * dpr), int(rect.height() * dpr))]
        gauss = {}
        for rows, w, h in sizes:
            img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(0xFF202020)
            pix = QPixmap.fromImage(img)
            arr = np.zeros((h, w, 4), dtype=np.uint8)
            arr[:, :, 3] = 255
            per_sigma = {}
            for sigma in (18.0, 14.0, 10.0, 6.0, 3.0):
                s1, s2 = [], []
                splash_motion._GAUSSIAN_ERROR = None
                for _ in range(args.reps):
                    t0 = time.perf_counter()
                    splash_motion._gaussian(pix, sigma)
                    s1.append((time.perf_counter() - t0) * 1000.0)
                    t0 = time.perf_counter()
                    splash_motion._gaussian(arr, sigma)
                    s2.append((time.perf_counter() - t0) * 1000.0)
                per_sigma[str(sigma)] = {
                    "pixmap_p50": round(statistics.median(s1), 2),
                    "ndarray_p50": round(statistics.median(s2), 2),
                    "err": splash_motion._GAUSSIAN_ERROR,
                }
            gauss[f"rows={rows}"] = {"size_phys": [w, h], "sigma": per_sigma}
            # 8 档预生成总耗时（用实测 p50 线性外推）
            vals = [v["pixmap_p50"] for v in per_sigma.values()]
            gauss[f"rows={rows}"]["pregen8_est_ms"] = round(
                sum(vals) * 8 / len(vals), 1)
        report["gaussian"] = gauss

        # 对照：先降到 1/k 再模糊再放大的代价（降采样收益）
        img = QImage(int(rect.width() * dpr), int(rect.height() * dpr),
                     QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0xFF202020)
        pix = QPixmap.fromImage(img)
        from PySide6.QtCore import Qt
        small = pix.scaled(pix.width() // 4, pix.height() // 4,
                           Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        t0 = time.perf_counter()
        splash_motion._gaussian(small, 18.0 / 4.0)
        down = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        splash_motion._gaussian(pix, 18.0)
        full = (time.perf_counter() - t0) * 1000.0
        report["downsample"] = {"down4_sigma4.5_ms": round(down, 2),
                               "full_sigma18_ms": round(full, 2)}

        # ui_backdrop 对照
        bd = []
        for _ in range(args.reps):
            t0 = time.perf_counter()
            ui_backdrop.gaussian_blur(img, 18)
            bd.append((time.perf_counter() - t0) * 1000.0)
        report["ui_backdrop_gaussian18_ms"] = {
            "p50": round(statistics.median(bd), 2), "min": round(min(bd), 2)}

        dialog.close()
        dialog.deleteLater()
        app.processEvents()
        (OUT / "geom.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    finally:
        ok = _qrestore(snap)
        print(f"[probe] QSettings 还原={'OK' if ok else 'FAILED'}")


if __name__ == "__main__":
    raise SystemExit(main())
