"""DPI / 多屏适配静态扫描（只读，不导入 Qt、不建窗口）。

用法：
    .venv/Scripts/python.exe devtools/_audit_dpi_scan.py

产出：``_check/dpi_audit.txt``（分类清单：dpr 基建 / 位图 / scaled / 固定几何 /
最小尺寸 / 1px 画笔 / 屏几何取用）。纯文本正则 + AST，不做任何运行时判断。
"""

from __future__ import annotations

import ast
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_check", "dpi_audit.txt")

# 只看产品代码，跳过自测/探针/构建产物
SKIP_DIRS = {"devtools", "_check", "build", "dist", "__pycache__", ".venv",
             "mimo", "installer", ".git"}
SKIP_FILES = {"selftest.py"}

CATEGORIES = [
    ("A. DPI 基建（main.py 只该有这些）",
     re.compile(r"setHighDpiScaleFactorRoundingPolicy|AA_EnableHighDpiScaling|"
                r"AA_UseHighDpiPixmaps|QT_SCALE_FACTOR|QT_ENABLE_HIGHDPI|"
                r"QT_AUTO_SCREEN_SCALE_FACTOR")),
    ("B. QPixmap( / QImage( 直接构造",
     re.compile(r"\bQPixmap\(|\bQImage\(")),
    ("C. .scaled( 缩放",
     re.compile(r"\.scaled\(")),
    ("D. setDevicePixelRatio",
     re.compile(r"setDevicePixelRatio")),
    ("E. 固定几何 setFixed{Size,Width,Height}",
     re.compile(r"setFixed(Size|Width|Height)\(")),
    ("F. 最小尺寸 setMinimum{Size,Width,Height}",
     re.compile(r"setMinimum(Size|Width|Height)\(")),
    ("G. 1px 画笔（小数 dpr 下会发虚）",
     re.compile(r"setWidthF\(\s*1\.0\s*\)|QPen\([^)]*\b1\.0\b")),
    ("H. 屏幕几何 / dpr 取用",
     re.compile(r"availableGeometry|screenGeometry|primaryScreen|\.screens\(\)|"
                r"devicePixelRatio|logicalDotsPerInch")),
]


def product_files() -> list[str]:
    out = []
    for name in sorted(os.listdir(ROOT)):
        if not name.endswith(".py") or name in SKIP_FILES:
            continue
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            out.append(name)
    return out


def scan_line_patterns() -> dict[str, list[tuple[str, int, str]]]:
    hits: dict[str, list[tuple[str, int, str]]] = {c: [] for c, _ in CATEGORIES}
    for name in product_files():
        with io.open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, 1):
                line = raw.rstrip("\n")
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                for cat, rx in CATEGORIES:
                    if rx.search(line):
                        hits[cat].append((name, lineno, line.strip()))
    return hits


def dpr_coverage() -> list[tuple[str, int, str, bool]]:
    """QPixmap.fromImage / QPixmap(...) 之后 ±8 行内是否 setDevicePixelRatio。

    只是"看得见"的近似判断：真正的正确性要看位图是不是按逻辑像素画。
    """
    rows = []
    for name in product_files():
        with io.open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        for i, line in enumerate(lines):
            if re.search(r"fromImage\(|\bQPixmap\(\s*[A-Za-z_]", line) and \
                    not line.lstrip().startswith("#"):
                window = "\n".join(lines[max(0, i - 8): i + 9])
                rows.append((name, i + 1, line.strip(),
                             "setDevicePixelRatio" in window))
    return rows


def magic_numbers() -> list[tuple[str, int, str]]:
    """setFixedWidth/setFixedHeight 里出现的大于 100 的字面量（硬编码几何）。"""
    rows = []
    for name in product_files():
        with io.open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if line.lstrip().startswith("#"):
                    continue
                m = re.search(r"setFixed(?:Width|Height)\(\s*(\d{3,})\s*\)", line)
                if m:
                    rows.append((name, lineno, line.strip()))
    return rows


def resize_literals() -> list[tuple[str, int, str]]:
    rows = []
    for name in product_files():
        try:
            tree = ast.parse(io.open(os.path.join(ROOT, name),
                                     encoding="utf-8").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            fname = getattr(fn, "attr", getattr(fn, "id", ""))
            if fname not in ("resize", "setGeometry", "move", "moveCenter"):
                continue
            if any(isinstance(a, (ast.Constant, ast.Tuple, ast.List))
                   for a in node.args):
                seg = ast.get_source_segment(
                    io.open(os.path.join(ROOT, name), encoding="utf-8").read(),
                    node) or ""
                rows.append((name, node.lineno, " ".join(seg.split())))
    return rows


def pen_widths() -> list[tuple[str, int, str]]:
    """**AST** 精确找 1.0 逻辑宽的画笔（小数 dpr 下必发虚）。

    ⚠️ 必须用 AST 而不是逐行正则：``QPen(...)`` 的参数经常跨行写，
    逐行正则会漏掉一半（实测漏 6/11）。
    """
    rows = []
    for name in product_files():
        try:
            src = io.open(os.path.join(ROOT, name), encoding="utf-8").read()
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = getattr(node.func, "attr", getattr(node.func, "id", ""))
            hit = False
            if fname == "QPen":
                hit = any(isinstance(a, ast.Constant)
                          and isinstance(a.value, (int, float))
                          and float(a.value) == 1.0 for a in node.args)
            elif fname == "setWidthF":
                hit = bool(node.args) and isinstance(node.args[0], ast.Constant) \
                    and float(node.args[0].value) == 1.0
            if hit:
                rows.append((name, node.lineno, f"{fname}(…)  1.0"))
    return rows


def main() -> int:
    hits = scan_line_patterns()
    cov = dpr_coverage()
    magic = magic_numbers()
    geo = resize_literals()
    pens = pen_widths()
    buf = []
    w = buf.append
    w("=" * 78)
    w("SerialAssistant DPI / 多屏 适配静态扫描（devtools/_audit_dpi_scan.py）")
    w("=" * 78)
    for cat, _rx in CATEGORIES:
        rows = hits[cat]
        w(f"\n## {cat} —— {len(rows)} 处")
        for name, lineno, text in rows:
            w(f"  {name}:{lineno}  {text[:150]}")
    w(f"\n## I. fromImage/QPixmap(...) ±8 行内**没有** setDevicePixelRatio —— "
      f"{sum(1 for r in cov if not r[3])} / {len(cov)} 处")
    for name, lineno, text, has in cov:
        if not has:
            w(f"  {name}:{lineno}  {text[:130]}")
    w(f"\n## J. setFixedWidth/Height(NNN) 字面量 —— {len(magic)} 处")
    for name, lineno, text in magic:
        w(f"  {name}:{lineno}  {text[:150]}")
    w(f"\n## K. resize/setGeometry/move 带字面量实参 —— {len(geo)} 处")
    for name, lineno, text in geo:
        w(f"  {name}:{lineno}  {text[:150]}")
    w(f"\n## L. 1.0 逻辑宽画笔（AST 精确，逐行正则只找到 {len(hits[CATEGORIES[6][0]])} 处）"
      f" —— {len(pens)} 处")
    for name, lineno, text in pens:
        w(f"  {name}:{lineno}  {text}")
    text = "\n".join(buf) + "\n"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    print(f"\n（已写入 {OUT}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
