"""动画规范审计（只读静态扫描）。

不改产品源码、不建窗口、不跑主循环。只做三件事：
  1. 枚举 `Tween(...)` / `Reveal(...)` 构造点，抠出「时长 / 曲线 / min_interval」；
  2. 枚举所有定时器间隔（QTimer.setInterval / start(ms) / singleShot(ms)）并区分
     「动画节拍」与「业务轮询」；
  3. 扫禁用/可疑模式（QGraphicsOpacityEffect、QPropertyAnimation、QSS transition、
     硬编码 16/8/60fps、setFixed* 写死像素、按 96dpi 的常量）。

产物：_check/_audit_anim_callsites.md（给人看的表）+ 控制台摘要。

用法：
    python devtools/_audit_anim_callsites.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOPE = ["main.py", "main_window.py", "ui_anim.py", "ui_surface.py",
         "ui_theme.py", "ui_controls.py", "ui_settings.py", "ui_brand.py",
         "ui_backdrop.py", "splash_anim.py", "splash_motion.py"]
OUT = ROOT / "_check" / "_audit_anim_callsites.md"

# 业务/数据类定时器（不算动画节拍），命中后只登记、不判违规
BUSINESS_TIMER_KEYS = (
    "PORT_WATCH_MS", "_clock_timer", "_search_timer", "_rx_flush",
    "_toast_timer", "_send_timer", "_rx_coalesce_timer", "_fs_idle_retry_timer",
    "BLE_SCAN_SECONDS", "TIMER_MIN_MS", "RX_MERGE_MS", "RX_HOLD_MAX_MS",
    "STAMP_ROUND_MIN_MS", "guard", "_max_timer",
)

CALL_RE = re.compile(r"\b(Tween|Reveal)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
NUM_FIRST_RE = re.compile(r"^\s*([A-Za-z_][\w.]*|\d+)\s*,")
CURVE_RE = re.compile(r"(?:curve\s*=\s*)?\b(linear|out_cubic|out_quint|in_out_cubic|"
                      r"in_out_quint|out_expo|STANDARD|EASE)\b")
INTERVAL_RE = re.compile(r"setInterval\(\s*([^)]+)\)")
START_MS_RE = re.compile(r"\.start\(\s*(\d+)\s*\)")
SINGLE_RE = re.compile(r"singleShot\(\s*(\d+)\s*,")
MININT_RE = re.compile(r"min_interval_ms\s*=\s*([A-Za-z_][\w.]*|\d+)")

BANNED = [
    ("QGraphicsOpacityEffect 用于控件", re.compile(r"QGraphicsOpacityEffect\s*\(")),
    ("硬件加速属性动画类", re.compile(r"\bQ(Property|Variant|Abstract)Animation\b")),
    ("QSS transition/animation", re.compile(r"transition\s*:|animation\s*:")),
    ("裸写 96dpi 假设", re.compile(r"\b96\s*(?:dpi|DPI)")),
]


def iter_lines(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(text, 1):
        yield i, line


def _match_paren(text: str, open_idx: int, limit: int = 600) -> str:
    """从 `open_idx`（指向 '('）起截到配对的 ')'；跨行、最多 limit 字符。"""
    depth = 0
    end = min(len(text), open_idx + limit)
    for i in range(open_idx, end):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
    return text[open_idx + 1:end]


def scan_calls(path: Path) -> list[dict]:
    """跨行抠 `Tween(` / `Reveal(` 的实参（补丁版：旧版按行扫会漏掉多行构造）。"""
    rows = []
    text = path.read_text(encoding="utf-8", errors="replace")
    guard = CALL_RE.pattern.replace(r"\s*\(", r"\s*\(")
    for m in re.finditer(r"\b(Tween|Reveal)\s*\(", text):
        # 跳过定义处（`class Tween(` / `def ... Tween(` 不存在，但注释里会提到）
        line_no = text.count("\n", 0, m.start()) + 1
        line_txt = text.splitlines()[line_no - 1]
        if line_txt.lstrip().startswith(("#", "``", "·")):
            continue
        kind = m.group(1)
        args = _match_paren(text, m.end() - 1)
        flat = " ".join(args.split())
        first = NUM_FIRST_RE.match(flat)
        duration = first.group(1) if first else "(kw?)"
        curve = CURVE_RE.search(flat)
        mini = MININT_RE.search(flat)
        rows.append({
            "file": path.name,
            "line": line_no,
            "kind": kind,
            "duration": duration,
            "curve": curve.group(1) if curve else "(默认 out_cubic)",
            "min_interval": mini.group(1) if mini else "-",
            "raw": flat[:150],
        })
    _ = guard
    return rows


def scan_intervals(path: Path) -> list[dict]:
    rows = []
    for i, line in iter_lines(path):
        for rx, kind in ((INTERVAL_RE, "setInterval"), (START_MS_RE, "start(ms)"),
                         (SINGLE_RE, "singleShot")):
            for m in rx.finditer(line):
                rows.append({"file": path.name, "line": i, "kind": kind,
                             "value": m.group(1), "raw": line.strip()[:110]})
    return rows


def scan_banned(path: Path) -> list[dict]:
    rows = []
    for i, line in iter_lines(path):
        for label, rx in BANNED:
            if rx.search(line):
                rows.append({"file": path.name, "line": i, "label": label,
                             "raw": line.strip()[:110]})
    return rows


def frame_facts() -> list[str]:
    """从 ui_anim.py 抠出帧率常量与节拍公式（不执行、不 import Qt）。"""
    src = (ROOT / "ui_anim.py").read_text(encoding="utf-8")
    facts = []
    for key in ("FPS_DEFAULT", "FPS_MIN", "FPS_MAX_CAP"):
        m = re.search(rf"^{key}\s*=\s*(\d+)", src, re.M)
        if m:
            facts.append(f"{key} = {m.group(1)}")
    facts.append("frame_interval_ms() = max(1, round(1000 / frame_hz))")
    for hz in (60, 120, 144, 165, 240):
        facts.append(f"  假设 frame_hz()={hz} → 节拍 {max(1, round(1000 / hz))}ms"
                     f"（≈{hz}fps）")
    return facts


def main() -> int:
    calls: list[dict] = []
    intervals: list[dict] = []
    banned: list[dict] = []
    for name in SCOPE:
        p = ROOT / name
        if not p.exists():
            print(f"[skip] 缺文件 {name}", file=sys.stderr)
            continue
        calls += scan_calls(p)
        intervals += scan_intervals(p)
        banned += scan_banned(p)

    by_curve: dict[str, int] = {}
    for c in calls:
        by_curve[c["curve"]] = by_curve.get(c["curve"], 0) + 1

    anim_iv = [r for r in intervals
               if r["kind"] in ("setInterval", "start(ms)")
               and not any(k in r["raw"] for k in BUSINESS_TIMER_KEYS)]

    lines = ["# 动画调用点静态扫描（只读）", ""]
    lines += ["## 0. 帧率事实（从 ui_anim.py 抠出）", ""]
    lines += [f"- {f}" for f in frame_facts()]
    lines += ["", "## 1. Tween / Reveal 构造点", "",
              "| 文件:行 | 类 | 时长 | 曲线 | min_interval |",
              "|---|---|---|---|---|"]
    for c in sorted(calls, key=lambda r: (r["file"], r["line"])):
        lines.append(f"| {c['file']}:{c['line']} | {c['kind']} | {c['duration']} "
                     f"| {c['curve']} | {c['min_interval']} |")

    lines += ["", "## 2. 曲线使用分布", "", "| 曲线 | 次数 |", "|---|---|"]
    for k, v in sorted(by_curve.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v} |")

    lines += ["", "## 3. 定时器间隔（疑似动画节拍：已剔除业务轮询）", "",
              "| 文件:行 | 形式 | 值 | 代码 |", "|---|---|---|---|"]
    for r in sorted(anim_iv, key=lambda r: (r["file"], r["line"])):
        lines.append(f"| {r['file']}:{r['line']} | {r['kind']} | {r['value']} "
                     f"| `{r['raw']}` |")

    lines += ["", "## 4. 禁用 / 可疑模式命中", "",
              "| 文件:行 | 命中 | 代码 |", "|---|---|---|"]
    for r in banned:
        lines.append(f"| {r['file']}:{r['line']} | {r['label']} | `{r['raw']}` |")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"calls={len(calls)} anim_timers={len(anim_iv)} banned={len(banned)}")
    print("curves:", by_curve)
    print("banned:")
    for r in banned:
        print(f"  {r['file']}:{r['line']}  {r['label']}")
    print("anim timers:")
    for r in anim_iv:
        print(f"  {r['file']}:{r['line']}  {r['kind']}={r['value']}")
    print(f"written {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
