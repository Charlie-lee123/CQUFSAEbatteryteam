# -*- coding: utf-8 -*-
r"""「未选择 → 选中车号」方向的模糊档取证：模糊到底有没有画出来。

不抓屏（QT 屏幕抓帧拿不到 DWM 合成），改为在 `_veh_paint` 上打点，逐帧记录：
  ms / has_old / 目标 sigma / alpha / 已生成模糊档数 / 实际能取到的层数 /
  覆盖层最终有没有 show_frame。

对应 `ui_settings._veh_paint` 的 has_old=False 分支：
    span = _VEH_SHIFT_MS(480); p = ms/480
    sigma = _VEH_SIGMA_MAX(24) * (1 - EASE(p))
    alpha = 1 - (1-p)**3
    items = _veh_frame_items(state, "new", sigma)

⛔ 只读打点，不改产品代码；QSettings 快照 + finally 还原。
"""
from __future__ import annotations

import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = os.path.join(_ROOT, "_check", "veh_blur_trace")
os.makedirs(OUT, exist_ok=True)

RECORDS: list[dict] = []
PAINT: list[dict] = []
_T0 = time.perf_counter()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}

    import customization
    import ui_settings

    # ---- 打点：直接拦最终绘制调用，拿**真实**的 σ / α（不自己重算公式）----
    orig_items = ui_settings.SettingsDialog._veh_frame_items
    orig_show = ui_settings._FadeOverlay.show_frame
    cur = {"sigma": None, "items": None}

    def traced_items(self, state, which, sigma):
        out = orig_items(self, state, which, sigma)
        if which == "new":
            cur["sigma"] = float(sigma)
            cur["items"] = len(out)
        return out

    def traced_show(self, items, rect, alpha, bg=None, visible_h=None):
        RECORDS.append({
            "t": round((time.perf_counter() - _T0) * 1000.0, 1),
            "sigma": round(cur["sigma"], 2) if cur["sigma"] is not None else None,
            "alpha": round(float(alpha), 3),
            "n_items": cur["items"],
            "n_pix": len(items) if items else 0,
            "visible_h": visible_h,
            "rect_h": rect.height() if rect is not None else None,
        })
        return orig_show(self, items, rect, alpha, bg, visible_h)

    ui_settings.SettingsDialog._veh_frame_items = traced_items
    ui_settings._FadeOverlay.show_frame = traced_show

    # 保留一次 paint 打点（只为统计"画不出内容"的帧，不参与曲线判定）
    orig_paint = ui_settings.SettingsDialog._veh_paint

    def traced_paint(self, state, ms):
        has_old = (state.get("pix_old") is not None
                   and not state["rect_old"].isNull())
        PAINT.append({"ms": round(ms, 1), "has_old": has_old,
                      "levels_new": len(state.get("levels_new") or [])})
        return orig_paint(self, state, ms)

    ui_settings.SettingsDialog._veh_paint = traced_paint

    dlg = None
    try:
        # 需要一个有模组参数的车号
        names = customization.vehicle_names()
        print("车号列表:", names)
        target = next((n for n in names if n.startswith("E74")), names[0] if names else None)
        if target is None:
            print("没有车号，无法取证")
            return 2

        import main_window
        win = main_window.MainWindow()
        win.show()
        for _ in range(30):
            app.processEvents()

        dlg = ui_settings.SettingsDialog(win)
        dlg.show()
        for _ in range(40):
            app.processEvents()

        cmb = getattr(dlg, "cmb_vehicle", None) or getattr(dlg, "cmb_number", None)
        if cmb is None:
            # 兜底：找带车号的 combo
            from PySide6.QtWidgets import QComboBox
            for c in dlg.findChildren(QComboBox):
                if c.count() and any(str(c.itemText(i)).startswith("E74")
                                     for i in range(c.count())):
                    cmb = c
                    break
        print("下拉:", cmb, "cur=", cmb.currentIndex() if cmb else None,
              "count=", cmb.count() if cmb else None)
        if cmb is not None:
            for i in range(cmb.count()):
                print(f"  item[{i}] = {cmb.itemText(i)!r}  data={cmb.itemData(i)!r}")

        # ① 先置为「未选择」(index 0)
        # ⛔ 不能用 cmb.setCurrentIndex()：车号下拉连的是 `activated`，
        #    程序化改 index **不发**这个信号，动画根本不会启动（实测 0 帧）。
        #    直接调回调才走真实路径。
        RECORDS.clear()
        PAINT.clear()
        _T0 = time.perf_counter()
        dlg._on_vehicle_picked(0)
        for _ in range(90):
            app.processEvents()
            time.sleep(0.01)
        n_deselect = len(RECORDS)
        print(f"① 切到未选择：{n_deselect} 帧，current={customization.current_vehicle()!r}")

        # ② 再选目标车号 —— 这就是用户说的「从无车号到有车号」
        RECORDS.clear()
        PAINT.clear()
        _T0 = time.perf_counter()

        def _hit(i: int) -> bool:
            txt = str(cmb.itemText(i))
            data = str(cmb.itemData(i) or "")
            return target in txt or target in data

        idx = next((i for i in range(cmb.count()) if _hit(i)), None)
        if idx is None:
            print(f"找不到含 {target} 的下拉项，直接选 index 1")
            idx = 1 if cmb.count() > 1 else 0
        dlg._on_vehicle_picked(idx)
        # 动画中定时抓**控件**（含子控件覆盖层），拿真实视觉给用户看。
        # ⛔ 不能用 QScreen.grabWindow：拿不到 DWM 合成的自绘内容。
        shots = []
        deadline = time.perf_counter() + 0.95
        next_shot = 70.0                 # ⛔ 相对毫秒（now 也是相对毫秒）
        while time.perf_counter() < deadline:
            app.processEvents()
            now = (time.perf_counter() - _T0) * 1000.0
            if now >= next_shot and len(shots) < 6:
                try:
                    pix = dlg.grab()
                    path = os.path.join(OUT, f"frame_{int(now):04d}ms.png")
                    pix.save(path)
                    shots.append((int(now), path))
                except RuntimeError:
                    pass
                next_shot = now + 75
            time.sleep(0.004)
        print("\n抓帧:", [f"{t}ms" for t, _ in shots])

        print(f"\n=== 未选择→选中（deslect 绘制 {n_deselect} 次，select 绘制 {len(RECORDS)} 次）===")
        if RECORDS:
            print(f"{'ms':>7} {'σ':>7} {'α':>6} {'层':>3} {'可见h':>6}")
            for r in RECORDS:
                s = "—" if r["sigma"] is None else f"{r['sigma']:.2f}"
                print(f"{r['t']:>7.1f} {s:>7} {r['alpha']:>6.3f} "
                      f"{r['n_pix']:>3} {str(r['visible_h']):>6}")
            # 关键指标：用户**看得见**（α≥0.5）且**确实糊**（σ≥4）的重叠窗口
            vis = [r for r in RECORDS
                   if r["sigma"] is not None and r["sigma"] >= 4.0
                   and r["alpha"] >= 0.5 and r["n_pix"] > 0]
            print(f"\n可见模糊帧（σ≥4 且 α≥0.5）: {len(vis)}")
            if vis:
                print(f"  时间窗口 {vis[0]['t']:.0f} ~ {vis[-1]['t']:.0f} ms"
                      f"（跨 {vis[-1]['t'] - vis[0]['t']:.0f} ms）")
                print(f"  σ 范围 {min(r['sigma'] for r in vis):.1f} ~ "
                      f"{max(r['sigma'] for r in vis):.1f}")
            else:
                print("  ⚠ 没有一帧同时满足「看得见 + 明显模糊」= 用户感知不到聚焦")
        with open(os.path.join(OUT, "trace.json"), "w", encoding="utf-8") as f:
            json.dump({"show_frames": RECORDS, "paints": PAINT}, f,
                      ensure_ascii=False, indent=1)
        print("轨迹:", os.path.join(OUT, "trace.json"))
        return 0
    finally:
        ui_settings.SettingsDialog._veh_paint = orig_paint
        ui_settings.SettingsDialog._veh_frame_items = orig_items
        ui_settings._FadeOverlay.show_frame = orig_show
        if dlg is not None:
            try:
                dlg.close()
                dlg.deleteLater()
            except RuntimeError:
                pass
        for _ in range(10):
            app.processEvents()
        for k in qs.allKeys():
            if k not in snap:
                qs.remove(k)
        for k, v in snap.items():
            qs.setValue(k, v)
        qs.sync()


if __name__ == "__main__":
    sys.exit(main())
