"""独立复验（证伪向 · 第二轮）：单模组偏移修复（连续段判据）。

只读真机数据（sessions.db 复制到 _check/qa_recheck 后读副本；日志文件只读打开）。
不改产品源码、不调 win.close()、不碰注册表。

跑法：.venv/Scripts/python.exe devtools/_qa_soc_recheck.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import soc_est as S

ROWS: list[tuple[str, str, str]] = []
REAL_LOG = r"C:\Users\Lenovo\AppData\Local\CQUFSAE-SerialAssistant\logs\接收历史-E74-2026.log"
DB_COPY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "_check", "qa_recheck", "sessions.db")


def rec(tag: str, ok: bool, detail: str) -> None:
    ROWS.append((tag, "PASS" if ok else "FAIL", detail))


def clean(cells, per=25, mask=None):
    return S.clean_cells(cells, mask, len(cells), per)


# ===================================================== A 最小复现（team-lead 原样）
def A_minimal() -> None:
    cells = [3300] * 25 + [3200] * 100
    # A1 完全照 team-lead 的调用（不传 cells_per_module）
    r = S.clean_cells(cells, n_cells=125)
    rec("A1 最小复现(默认 per)：bad_idx==[]", r.bad_idx == [], f"bad_idx={r.bad_idx}")
    rec("A2 最小复现(默认 per)：pack==402.5V", abs(r.pack_mv / 1000 - 402.5) < 1e-9,
        f"{r.pack_mv/1000:.3f}V")
    rec("A3 最小复现(默认 per)：module_offset is True", r.module_offset is True, str(r.module_offset))
    # A4 生产路径（per=25）
    r = S.clean_cells(cells, n_cells=125, cells_per_module=25)
    rec("A4 生产路径(per=25)：bad==[] 且 pack==402.5 且 offset",
        r.bad_idx == [] and abs(r.pack_mv / 1000 - 402.5) < 1e-9 and r.module_offset,
        f"bad={r.bad_idx} pack={r.pack_mv/1000:.3f} off={r.module_offset}")


# ===================================================== B run_min 悬崖（11/12/13）
def _block(n, start, run, hi=3300, lo=3200):
    c = [lo] * n
    for k in range(start, start + run):
        c[k] = hi
    return c


def B_run_cliff() -> None:
    for run in (11, 12, 13):
        # 中段连续 run 节 +100mV（两侧都是 3200，run 长度精确可控）
        cells = _block(125, 40, run)
        r = clean(cells, per=25)
        kept_all = len(r.bad_idx) == 0
        if run >= 12:
            rec(f"B1 run={run}(>=12)：整段保留 bad==[] 且 offset",
                kept_all and r.module_offset and abs(r.pack_mv / 1000 - (400 + run * 0.1)) < 1e-9,
                f"bad={len(r.bad_idx)} off={r.module_offset} pack={r.pack_mv/1000:.3f}V"
                f"（真值 {400+run*0.1:.3f}）")
        else:
            rec(f"B1 run={run}(<12)：整段当孤立坏点回填（设计边界）",
                len(r.bad_idx) == run and not r.module_offset,
                f"bad={len(r.bad_idx)} off={r.module_offset} pack={r.pack_mv/1000:.3f}V"
                f"（真值 {400+run*0.1:.3f}，回填后少报 {(400+run*0.1)-r.pack_mv/1000:.3f}V）")

    # B2 单模组整段（25 节，位于模组边界 0..24）—— 应保留
    cells = _block(125, 0, 25)
    r = clean(cells, per=25)
    rec("B2 模组0整段(0..24)保留", r.bad_idx == [] and r.module_offset,
        f"bad={r.bad_idx} off={r.module_offset} pack={r.pack_mv/1000:.3f}V")


# ===================================================== C 振幅 & 统计塌陷区
def C_amplitude() -> None:
    for amp in (20, 21, 22, 500, -20, -21, -500):
        cells = [3200] * 125
        for k in range(25):
            cells[k] = 3200 + amp
        r = clean(cells, per=25)
        true_pack = (25 * (3200 + amp) + 100 * 3200) / 1000.0
        if abs(amp) >= 21:
            rec(f"C amp={amp:+d}：单模组保留 bad==[] offset pack 正确",
                r.bad_idx == [] and r.module_offset and abs(r.pack_mv / 1000 - true_pack) < 1e-9,
                f"bad={len(r.bad_idx)} off={r.module_offset} pack={r.pack_mv/1000:.3f}V真值{true_pack:.3f}")
        else:
            # ±20 恰在限内（>limit 为 False）→ 视为正常，不回填、不报 offset，总压仍对
            rec(f"C amp={amp:+d}（=限内边界）：不回填不报 offset pack 正确",
                r.bad_idx == [] and not r.module_offset and abs(r.pack_mv / 1000 - true_pack) < 1e-9,
                f"bad={len(r.bad_idx)} off={r.module_offset} pack={r.pack_mv/1000:.3f}V真值{true_pack:.3f}")


# ===================================================== D 两段不相邻 vs 一段连续
def D_two_runs_vs_one() -> None:
    # D1 两段各 6 节同向离群（总 12，但不相邻）→ 各自 <12，都应该回填
    cells = [3200] * 125
    for k in range(10, 16):
        cells[k] = 3300
    for k in range(60, 66):
        cells[k] = 3300
    r = clean(cells, per=25)
    rec("D1 两段各6节(总12)不相邻 → 都回填 bad=12 不报 offset",
        len(r.bad_idx) == 12 and not r.module_offset,
        f"bad={len(r.bad_idx)} off={r.module_offset}")
    # D2 一段连续 12 节 → 保留
    cells = _block(125, 10, 12)
    r = clean(cells, per=25)
    rec("D2 一段连续12节 → 保留 bad==[] offset",
        r.bad_idx == [] and r.module_offset, f"bad={len(r.bad_idx)} off={r.module_offset}")


# ===================================================== E 模组边界跨界
def E_boundary_cross() -> None:
    # E1 连续 12 节横跨模组1/2 边界（模组1=0..24, 模组2=25..49），从 19 起 12 节
    cells = _block(125, 19, 12)
    r = clean(cells, per=25)
    rec("E1 12节跨界(19..30) → 跨边界仍算一段、保留 offset",
        r.bad_idx == [] and r.module_offset,
        f"bad={len(r.bad_idx)} off={r.module_offset}（跨模组1/2 边界仍按连续长度判）")
    # E2 11 节跨界 → 回填（<12）
    cells = _block(125, 19, 11)
    r = clean(cells, per=25)
    rec("E2 11节跨界(19..29) → 回填 bad=11 不报 offset",
        len(r.bad_idx) == 11 and not r.module_offset,
        f"bad={len(r.bad_idx)} off={r.module_offset}")


# ===================================================== F 断线与模组偏移混合
def F_mixed() -> None:
    # 模组0 整段 +100（3300），另有两节真断线（0 与 5000）在别处
    cells = [3200] * 125
    for k in range(25):
        cells[k] = 3300
    cells[80] = 0          # 真断线（超量程下界）
    cells[100] = 5000      # 真断线（超量程上界）
    r = clean(cells, per=25)
    rec("F1 模组偏移+两节断线：断线仍回填(bad=[81,101])、模组保留",
        r.bad_idx == [81, 101] and r.module_offset,
        f"bad={r.bad_idx} off={r.module_offset} pack={r.pack_mv/1000:.3f}V")
    # F2 模组偏移 但模组中间一节断线（None）→ 断线回填、其余偏移保留
    cells = [3200] * 125
    for k in range(25):
        cells[k] = 3300
    cells[12] = None
    r = clean(cells, per=25)
    rec("F2 模组偏移+模组内1节None：None回填、其余偏移保留 offset",
        r.bad_idx == [13] and r.module_offset,
        f"bad={r.bad_idx} off={r.module_offset}（断线把连续段劈成 12/12）")


# ===================================================== G cells_per_module=0 兜底
def G_fallback_per() -> None:
    # per=0 → per_module = max(1, 125//8)=15, run_min=max(2,int(15*0.5))=7
    cells = _block(125, 30, 7)
    r = S.clean_cells(cells, n_cells=125)      # 不传 per
    rec("G1 per=0 兜底：连续7节即判定模组偏移(run_min=7)",
        r.bad_idx == [] and r.module_offset,
        f"bad={len(r.bad_idx)} off={r.module_offset}（得 <=7 才触发，偏宽松）")
    cells = _block(125, 30, 6)
    r = S.clean_cells(cells, n_cells=125)
    rec("G2 per=0 兜底：连续6节 < run_min(7) → 回填",
        len(r.bad_idx) == 6 and not r.module_offset,
        f"bad={len(r.bad_idx)} off={r.module_offset}")


# ===================================================== H 回归（原有行为不受损）
def H_regression() -> None:
    # H1 两节断线 bad=[12,23] 总压 400.0
    cells = [3200] * 125
    cells[11] = 0
    cells[22] = 5000
    r = clean(cells, per=25)
    rec("H1 两节断线 bad==[12,23] pack==400.0",
        r.bad_idx == [12, 23] and abs(r.pack_mv / 1000 - 400.0) < 1e-9,
        f"bad={r.bad_idx} pack={r.pack_mv/1000:.3f}V")
    # H2 全坏 ok=False
    r = clean([0] * 125, per=25)
    rec("H2 全坏 → ok=False", not r.ok, f"ok={r.ok}")
    # H3 缺失 None 回填
    cells = [3200] * 125
    for k in (5, 6, 7, 8, 9):
        cells[k] = None
    r = clean(cells, per=25)
    rec("H3 缺失5节→回填 bad=5", len(r.bad_idx) == 5, f"bad={len(r.bad_idx)}")
    # H4 全健康：无回填、无 offset、无 residual
    r = clean([3200] * 125, per=25)
    rec("H4 全健康 → 无回填/无 offset/无 residual",
        r.bad_idx == [] and not r.module_offset and not r.residual,
        f"bad={r.bad_idx} off={r.module_offset} res={r.residual}")


# ===================================================== I 真机数据零误报
def _count_flags(rounds_cells, per=25) -> dict:
    total = mo = res = okc = 0
    for cells in rounds_cells:
        if not cells or sum(1 for c in cells if c is not None) == 0:
            continue
        total += 1
        r = S.clean_cells(cells, None, 125, per)
        if not r.ok:
            continue
        okc += 1
        mo += 1 if r.module_offset else 0
        res += 1 if r.residual else 0
    return {"total": total, "ok": okc, "module_offset": mo, "residual": res}


def I_realdata() -> None:
    # I1 日志文件（119 轮）
    try:
        import soc_feed
        with open(REAL_LOG, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        rounds = soc_feed.parse_text(text, n_modules=5, cells_per_module=25)
        full = [r.cells for r in rounds if r.n_samples >= 120]
        st = _count_flags(full, per=25)
        rec("I1 日志文件(补全轮) 真机零误报 module_offset=0 且 residual=0",
            st["module_offset"] == 0 and st["residual"] == 0,
            f"轮={st['total']} ok={st['ok']} offset={st['module_offset']} residual={st['residual']}"
            f"（parse 出 {len(rounds)} 轮，其中 ≥120 节 {len(full)} 轮）")
    except Exception as exc:                       # noqa: BLE001
        rec("I1 日志文件 真机零误报", False, f"异常 {exc!r}")

    # I2 sessions.db（复制副本，按 ts 拼 125 节）
    try:
        con = sqlite3.connect(f"file:{DB_COPY}?mode=ro&immutable=1", uri=True)
        cur = con.cursor()
        rows = cur.execute(
            "SELECT ts, module, cells FROM frames WHERE vehicle='E74-2026' ORDER BY ts"
        ).fetchall()
        con.close()
        byts: dict = {}
        for ts, mod, cells in rows:
            try:
                lst = json.loads(cells) if cells else []
            except Exception:                      # noqa: BLE001
                lst = []
            slot = byts.setdefault(ts, {})
            m = int(mod)
            # 同一个 ts 会有 cells=[] 的重复模组行（温度/空帧），不能让它覆盖
            # 真正的 25 节行 —— 取较长的那份。
            if m not in slot or len(lst) > len(slot[m]):
                slot[m] = lst
        rounds_cells = []
        for ts, mods in byts.items():
            arr = [None] * 125
            for mod, lst in mods.items():
                for s, val in enumerate(lst):
                    idx = (mod - 1) * 25 + s
                    if 0 <= idx < 125:
                        arr[idx] = val
            rounds_cells.append(arr)
        st = _count_flags(rounds_cells, per=25)
        rec("I2 sessions.db 真机零误报 module_offset=0 且 residual=0",
            st["module_offset"] == 0 and st["residual"] == 0,
            f"轮={st['total']} ok={st['ok']} offset={st['module_offset']} residual={st['residual']}"
            f"（distinct ts={len(byts)}）")
    except Exception as exc:                       # noqa: BLE001
        rec("I2 sessions.db 真机零误报", False, f"异常 {exc!r}")


def main() -> int:
    for fn in (A_minimal, B_run_cliff, C_amplitude, D_two_runs_vs_one,
               E_boundary_cross, F_mixed, G_fallback_per, H_regression, I_realdata):
        try:
            fn()
        except Exception as exc:                   # noqa: BLE001
            import traceback
            traceback.print_exc()
            rec(fn.__name__ + " 整体异常", False, repr(exc))
    n_fail = sum(1 for _, s, _ in ROWS if s == "FAIL")
    for tag, status, detail in ROWS:
        print(f"[{status}] {tag}  —— {detail}")
    print(f"\n{len(ROWS) - n_fail}/{len(ROWS)} 通过；FAIL={n_fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
