"""验证「占位行高度补间」这条落点是否成立（只读，不改产品源码）。

要证的四条：
  1) 在 `_module_row_base` 插入一个跨两列的空 QWidget 行，改它的 fixedHeight 能把
     下面的行整体推下去（= 有真实位移，且不动标签列宽）。
  2) `form.setRowVisible(row, False)` 能把已存在的模组行压成 0 高（= 可预建后隐藏）。
  3) 「撤占位 + 显新行」在同一帧完成时，总高度相等 → 下面的行不动（无跳变）。
  4) 逐帧改占位高度时，单次 QFormLayout 重排的耗时（p50/p95/max）。

    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_vehicle_mech.py
"""
from __future__ import annotations

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


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    snap = _qsnap()
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QFormLayout, QWidget

    app = QApplication(sys.argv[:1])
    import customization
    import ui_theme
    ui_theme.apply_scheme("dark")
    customization.save_vehicle("T3-2026", [25] * 3, [8] * 3)
    customization.save_vehicle("T6-2026", [25] * 6, [8] * 6)

    out: dict = {}
    try:
        from main_window import BUTTON_SPECS
        from ui_settings import SettingsDialog

        dialog = SettingsDialog("dark", 60, BUTTON_SPECS, (), parent=None)
        dialog.setModal(False)
        dialog.move(-4000, -4000)
        dialog.show()

        def settle(rounds: int = 6) -> None:
            for _ in range(rounds):
                QCoreApplication.sendPostedEvents(None, int(QEvent.Type.LayoutRequest))
                app.processEvents()
                dialog.layout().activate()

        def y_of(label: str) -> int | None:
            for i in range(dialog.form.rowCount()):
                item = dialog.form.itemAt(i, QFormLayout.ItemRole.LabelRole)
                fitem = dialog.form.itemAt(i, QFormLayout.ItemRole.FieldRole)
                if item is None or item.widget() is None or fitem is None:
                    continue
                w = item.widget()
                if hasattr(w, "text") and w.text() == label:
                    fw = fitem.widget()
                    return fw.y() if fw is not None else None
            return None

        # ① 基线：3 模块
        customization.select_vehicle("T3-2026")
        dialog._sync_vehicle_rows()
        settle()
        base_y = y_of("主题")
        base_rect = dialog._module_rows_rect()
        out["baseline"] = {"subject_y": base_y,
                           "module_rect": [base_rect.x(), base_rect.y(),
                                           base_rect.width(), base_rect.height()],
                           "rows": len(dialog._module_rows)}

        # ①b 关键：占位行插在**旧行下方**（不是最前面）时，h=0 那一刻会不会
        #     自带一条 12px 的 vspacing（= 起始就跳一下）。
        holder0 = QWidget()
        holder0.setFixedHeight(0)
        dialog.form.insertRow(dialog._module_row_base + len(dialog._module_rows),
                              holder0)
        settle()
        y_after_ins = y_of("主题")
        out["step1b_placeholder_below_old"] = {
            "subject_y_before": base_y,
            "subject_y_after_insert_h0": y_after_ins,
            "delta_at_h0": None if (base_y is None or y_after_ins is None)
            else y_after_ins - base_y,
        }
        holder0.setFixedHeight(138)
        settle()
        y_after_138 = y_of("主题")
        out["step1b_placeholder_below_old"]["subject_y_at_h138"] = y_after_138
        out["step1b_placeholder_below_old"]["delta_at_h138"] = (
            None if (base_y is None or y_after_138 is None)
            else y_after_138 - base_y)
        dialog.form.removeRow(dialog._module_row_base + len(dialog._module_rows))
        settle()

        # ② 换成 6 模块（模拟"已经重建完"），再把 6 行**全部隐藏**
        customization.select_vehicle("T6-2026")
        dialog._rebuild_module_rows([25] * 6, [8] * 6)
        settle()
        rect6_full = dialog._module_rows_rect()
        y6_visible = y_of("主题")
        for i in range(len(dialog._module_rows)):
            dialog.form.setRowVisible(dialog._module_row_base + i, False)
        settle()
        y6_hidden = y_of("主题")
        out["step2_hide_rows"] = {
            "module_rect_visible": [rect6_full.x(), rect6_full.y(),
                                    rect6_full.width(), rect6_full.height()],
            "subject_y_visible": y6_visible,
            "subject_y_hidden": y6_hidden,
            "collapsed": None if (y6_visible is None or y6_hidden is None)
            else y6_visible - y6_hidden,
        }

        # ③ 在 base 插空占位行（跨两列），高度 0；再逐帧 0 → 区域全高
        holder = QWidget()
        holder.setFixedHeight(0)
        dialog.form.insertRow(dialog._module_row_base, holder)
        settle()
        y_start = y_of("主题")
        target = (rect6_full.height() if rect6_full.height() > 0 else 264)
        per_frame = []
        steps = 30
        for i in range(1, steps + 1):
            t0 = time.perf_counter()
            holder.setFixedHeight(int(round(target * i / steps)))
            dialog.form.invalidate()
            dialog.layout().activate()
            per_frame.append((time.perf_counter() - t0) * 1000.0)
        settle()
        y_end = y_of("主题")
        pf = sorted(per_frame)
        out["step3_tween"] = {
            "target": target,
            "subject_y_start": y_start, "subject_y_end": y_end,
            "delta": None if (y_start is None or y_end is None) else y_end - y_start,
            "per_frame_relayout_ms": {
                "n": len(pf), "p50": round(pf[len(pf) // 2], 3),
                "p95": round(pf[min(len(pf) - 1, int(len(pf) * .95))], 3),
                "max": round(pf[-1], 3)},
        }
        assert_ok_1 = (y_start is not None and y_end is not None
                       and y_end - y_start == target)

        # ④ 同帧"撤占位 + 显新行"：总高相等 → 主题 y 不动
        for i in range(len(dialog._module_rows)):
            dialog.form.setRowVisible(dialog._module_row_base + 1 + i, True)
        dialog.form.removeRow(dialog._module_row_base)
        settle()
        y_swap = y_of("主题")
        out["step4_swap_same_frame"] = {
            "subject_y": y_swap,
            "delta_vs_tween_end": None if (y_swap is None or y_end is None)
            else y_swap - y_end,
            "module_rect": list(dialog._module_rows_rect().getRect()),
        }
        out["asserts"] = {"placeholder_pushes_exactly_target": bool(assert_ok_1)}

        dialog.close()
        dialog.deleteLater()
        app.processEvents()
        (OUT / "mech.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    finally:
        ok = _qrestore(snap)
        print(f"[probe] QSettings 还原={'OK' if ok else 'FAILED'}")


if __name__ == "__main__":
    raise SystemExit(main())
