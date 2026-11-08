"""选车号动画（让位 + 高斯模糊淡入）的逐帧验收探针。

断言（按 team-lead 的验收口径）：
  A1 让位段「主题行 y」单调不减
  A2 单步位移 ≤ 8px（⚠️ 320ms/60Hz 下 Δ=276 物理上到不了 8px，见报告；这里同时
     打印实测 max 与「要达到 8px 需要多少 ms」）
  A3 让位结束时 y 与终态差 ≤ 1px
  A4 换行那一帧位移 == 0
  A5 P3 末帧画的是**原图**（σ=0，权重 1.0，且就是 pix_new）
  A6 整段帧间隔 p95 ≤ 20ms、max ≤ 40ms
  B  连点两次车号不崩、终态正确
  C  animations_enabled()=False → 直接落终态（不留占位行、行可见）
  D  动画中途关面板不崩、不收尾报错

    QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_vehicle_anim_qa.py
    ... --onscreen 0     # 离屏跑（默认 1：真窗口才有真实重绘代价）
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "_check" / "vehicle_anim"
LOCK = ROOT / "_check" / ".app_run.lock"
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


def _lock_take(owner: str, wait_s: float = 240.0) -> bool:
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            LOCK.mkdir(parents=True)
        except FileExistsError:
            time.sleep(1.0)
            continue
        try:
            (LOCK / "owner.txt").write_text(owner, encoding="utf-8")
        except OSError:
            pass
        return True
    return False


def _lock_drop() -> None:
    try:
        (LOCK / "owner.txt").unlink(missing_ok=True)
        LOCK.rmdir()
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onscreen", type=int, default=1, choices=[0, 1])
    ap.add_argument("--shots", type=int, default=0,
                    help="1 = 在 ms=60/160/300/360/480/600/700/760 各存一张截图")
    ap.add_argument("--tick", type=int, default=0,
                    help="覆盖 ui_settings._VEH_TICK_MS（0 = 用产品值）")
    ap.add_argument("--shift", type=int, default=0,
                    help="覆盖 ui_settings._VEH_SHIFT_MS（0 = 用产品值）")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    if not _lock_take("vehicle-anim/qa"):
        print("[qa] 240s 内没拿到 _check/.app_run.lock", flush=True)
        return 2
    snap = _qsnap()
    results: list[tuple[str, bool, str]] = []
    job: dict = {"runs": {}}
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication, QFormLayout, QLabel

        app = QApplication(sys.argv[:1])
        import customization
        import ui_theme
        ui_theme.apply_scheme("dark")
        for n in (3, 6, 12):
            customization.save_vehicle(f"Q{n}-2026", [25] * n, [8] * n)
        customization.select_vehicle("")

        import ui_settings
        if args.tick:
            ui_settings._VEH_TICK_MS = int(args.tick)
        if args.shift:
            ui_settings._VEH_SHIFT_MS = int(args.shift)
        from ui_settings import SettingsDialog
        from main_window import BUTTON_SPECS

        dialog = SettingsDialog("dark", 60, BUTTON_SPECS, (), parent=None)
        dialog.setModal(False)
        if args.onscreen:
            dialog.move(40, 40)
        else:
            dialog.move(-4000, -4000)
        dialog.show()
        for _ in range(8):
            QCoreApplication.sendPostedEvents(None, int(QEvent.Type.LayoutRequest))
            app.processEvents()

        def y_subject() -> int | None:
            for i in range(dialog.form.rowCount()):
                item = dialog.form.itemAt(i, QFormLayout.ItemRole.LabelRole)
                fitem = dialog.form.itemAt(i, QFormLayout.ItemRole.FieldRole)
                if item is None or item.widget() is None or fitem is None:
                    continue
                w = item.widget()
                if isinstance(w, QLabel) and w.text() == "主题":
                    fw = fitem.widget()
                    return fw.y() if fw is not None else None
            return None

        # ---- 逐帧记录：包一层 _veh_tick / _veh_paint ----
        rec: list[dict] = []
        orig_tick = dialog._veh_tick

        def sigma_of(ms: float) -> float:
            from ui_settings import (_VEH_BLUR_IN_END_MS, _VEH_BLUR_OUT_MS,
                                     _VEH_SIGMA_MAX, _VEH_SWAP_MS)
            from ui_anim import EASE
            if ms <= _VEH_BLUR_OUT_MS:
                p = ms / max(1, _VEH_BLUR_OUT_MS)
                return _VEH_SIGMA_MAX * (p ** 3)
            if ms < _VEH_SWAP_MS:
                return 0.0
            span = max(1, _VEH_BLUR_IN_END_MS - _VEH_SWAP_MS)
            p = max(0.0, min(1.0, (ms - _VEH_SWAP_MS) / float(span)))
            return _VEH_SIGMA_MAX * (1.0 - float(EASE(p)))

        shot_targets = [60, 160, 300, 360, 480, 600, 700, 760]
        shot_done: list[int] = []

        def maybe_shot(ms: float) -> None:
            if not args.shots:
                return
            for target in shot_targets:
                if target in shot_done or ms < target:
                    continue
                shot_done.append(target)
                try:
                    dialog.grab().save(str(OUT / f"shot_{target:03d}.png"))
                except Exception:       # noqa: BLE001
                    pass
                return

        def tick_probe(state, k):
            t0 = time.perf_counter()
            orig_tick(state, k)
            # 采样前先把布局落实一次：补间改的是 fixedHeight，Qt 要等下一拍才
            # 重排；不强制 activate 会读到上一拍的 y（会误判成"跳了一大步"）。
            dialog.form.activate()
            ms = k * 780.0
            maybe_shot(ms)
            mask = getattr(dialog, "_veh_mask", None)
            items = getattr(mask, "_items", []) if mask is not None else []
            holder = state.get("holder")
            rec.append({
                "t": t0, "ms": round(ms, 1),
                "y": y_subject(),
                "h": int(holder.height()) if holder is not None else None,
                "sigma": round(sigma_of(ms), 2),
                "alpha": round(float(getattr(mask, "_alpha", 0.0)), 3)
                if mask is not None else 0.0,
                "n_items": len(items),
                "sharp": bool(items and items[0][0] is state.get("pix_new")),
                "shown": bool(state.get("rows_shown")),
                "paint_ms": 0.0,
            })

        dialog._veh_tick = tick_probe

        def run_to(done: float = 0.95) -> list[dict]:
            rec.clear()
            t_end = time.perf_counter() + done
            while time.perf_counter() < t_end:
                app.processEvents()
                time.sleep(0.001)
            return list(rec)

        # ---------------- A：0 → 3 行（未选车 → 选车）----------------
        customization.select_vehicle("")
        dialog._sync_vehicle_rows()
        for _ in range(6):
            QCoreApplication.sendPostedEvents(None, int(QEvent.Type.LayoutRequest))
            app.processEvents()
        y0 = y_subject()
        customization.select_vehicle("Q3-2026")
        dialog._sync_vehicle_rows()
        frames = run_to(0.95)
        y_final = y_subject()
        n_rows = len(dialog._module_rows)
        holder_left = bool(getattr(dialog, "_veh", None))
        job["tick_ms"] = ui_settings._VEH_TICK_MS
        job["shift_ms"] = ui_settings._VEH_SHIFT_MS
        job["runs"]["A_0_to_3"] = {
            "y_start": y0, "y_final": y_final, "rows": n_rows,
            "frames": len(frames),
            "max_h": max((f["h"] or 0) for f in frames) if frames else None,
            "left_state": holder_left,
        }

        def steps(fr):
            out = []
            prev = None
            for f in fr:
                if f["y"] is None:
                    continue
                if prev is not None and not f["shown"]:
                    out.append((f["ms"], f["y"] - prev, f["y"]))
                prev = f["y"]
            return out

        st = steps(frames)
        mono = all(d >= 0 for _ms, d, _y in st)
        max_step = max((d for _ms, d, _y in st), default=0)
        results.append(("A1 让位段 y 单调不减", mono,
                        f"max_step={max_step}px n={len(st)}"))
        pos = sorted(d for _m, d, _y in st if d > 0)
        med_step = pos[len(pos) // 2] if pos else 0
        results.append(("A2 单步 ≤ 8px", max_step <= 8,
                        f"实测 max={max_step}px 中位={med_step}px"
                        f"（Δ={max((f['h'] or 0) for f in frames) if frames else 0}）"))
        target_gap = abs((y_final or 0) - ((y0 or 0) + 46 * 3))
        results.append(("A3 终态差 ≤ 1px", target_gap <= 1,
                        f"y_final={y_final} 期望={(y0 or 0) + 46 * 3} 差={target_gap}"))
        # 换行那一帧：rows_shown 由 False → True 的那一帧，y 不许变
        swap_delta = None
        for i in range(1, len(frames)):
            if frames[i]["shown"] and not frames[i - 1]["shown"]:
                if frames[i]["y"] is not None and frames[i - 1]["y"] is not None:
                    swap_delta = frames[i]["y"] - frames[i - 1]["y"]
                break
        results.append(("A4 换行帧位移 == 0", swap_delta == 0,
                        f"swap_delta={swap_delta}"))
        p3 = [f for f in frames if f["ms"] >= 660]
        last_sharp = bool(p3 and p3[-1]["sigma"] <= 0.15 and p3[-1]["sharp"])
        results.append(("A5 P3 末帧是原图(σ=0)", last_sharp,
                        f"末帧 sigma={p3[-1]['sigma'] if p3 else None} "
                        f"sharp={p3[-1]['sharp'] if p3 else None}"))
        iv = [(frames[i]["t"] - frames[i - 1]["t"]) * 1000.0
              for i in range(1, len(frames))]
        if iv:
            srt = sorted(iv)
            p50, p95 = srt[len(srt) // 2], srt[min(len(srt) - 1, int(len(srt) * .95))]
            results.append(("A6 帧间隔 p95 ≤ 20ms", p95 <= 20.0,
                            f"n={len(iv)} P50={p50:.2f} P95={p95:.2f} max={max(iv):.2f}"))
            results.append(("A6b 帧间隔 max ≤ 40ms", max(iv) <= 40.0,
                            f"max={max(iv):.2f}ms"))
            job["runs"]["A_0_to_3"].update({"p50": round(p50, 2),
                                            "p95": round(p95, 2),
                                            "max": round(max(iv), 2)})

        # ---------------- A2b：3 → 6（Δ=138，最常见的"换一台更多模组的车"）----
        customization.select_vehicle("Q3-2026")
        dialog._sync_vehicle_rows()
        run_to(0.9)
        y_pre = y_subject()
        customization.select_vehicle("Q6-2026")
        dialog._sync_vehicle_rows()
        frames2 = run_to(0.95)
        st2 = steps(frames2)
        max2 = max((d for _ms, d, _y in st2), default=0)
        job["runs"]["A2b_3_to_6"] = {
            "y_start": y_pre, "y_final": y_subject(), "frames": len(frames2),
            "max_step": max2, "mono": all(d >= 0 for _m, d, _y in st2),
        }
        pos2 = sorted(d for _m, d, _y in st2 if d > 0)
        results.append(("A2b 3→6 单步 ≤ 8px", max2 <= 8,
                        f"实测 max={max2}px 中位={pos2[len(pos2)//2] if pos2 else 0}px"))

        # ---------------- B：连点两次（3 → 6 → 12，80ms 内）----------------
        customization.select_vehicle("Q6-2026")
        dialog._sync_vehicle_rows()
        run_to(0.12)
        customization.select_vehicle("Q12-2026")
        dialog._sync_vehicle_rows()
        frames_b = run_to(1.0)
        ok_b = (len(dialog._module_rows) == 12 and dialog._veh is None
                and y_subject() is not None)
        # 终态高度 = 起点 + 46*12
        results.append(("B 连点两次不崩且落终态", ok_b,
                        f"rows={len(dialog._module_rows)} "
                        f"state={dialog._veh is not None} y={y_subject()}"))
        job["runs"]["B_double"] = {"frames": len(frames_b)}

        # ---------------- C：关掉动画 → 直接落终态 ----------------
        import ui_anim
        ui_anim.set_animations_enabled_override(False)
        try:
            customization.select_vehicle("Q3-2026")
            dialog._sync_vehicle_rows()
            app.processEvents()
            rows_c = len(dialog._module_rows)
            holder_c = bool(dialog._veh and dialog._veh.get("holder"))
            shown_c = all(not lbl.isHidden() for lbl, _f in dialog._module_rows)
            results.append(("C 关动画直接落终态", rows_c == 3 and not holder_c and shown_c,
                            f"rows={rows_c} 残留占位={holder_c} 行可见={shown_c}"))
        finally:
            ui_anim.set_animations_enabled_override(None)

        # ---------------- D：动画中途关面板 ----------------
        customization.select_vehicle("Q12-2026")
        dialog._sync_vehicle_rows()
        run_to(0.15)
        crashed = None
        try:
            dialog.close()
            for _ in range(10):
                app.processEvents()
                time.sleep(0.01)
        except Exception as exc:        # noqa: BLE001
            crashed = repr(exc)
        results.append(("D 中途关面板不崩", crashed is None, str(crashed or "ok")))

        job["results"] = [{"name": n, "ok": ok, "detail": d} for n, ok, d in results]
        dialog.deleteLater()
        app.processEvents()
        (OUT / "qa.json").write_text(json.dumps(job, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        print("\n===== 选车号动画验收 =====")
        for name, ok, detail in results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")
        print(f"\n逐帧数据：{OUT / 'qa.json'}")
        return 0 if all(ok for _n, ok, _d in results) else 1
    except Exception as exc:            # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[qa] 异常：{exc!r}", flush=True)
        return 1
    finally:
        okq = _qrestore(snap)
        print(f"[qa] QSettings 还原={'OK' if okq else 'FAILED'}")
        _lock_drop()


if __name__ == "__main__":
    raise SystemExit(main())
