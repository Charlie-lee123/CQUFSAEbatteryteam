"""覆盖层渲染路径微基准：定位「最大化/还原覆盖层每帧 25~50ms」的元凶。

在**真窗口、真 DWM** 下逐条枚举候选实现，测每帧周期与每次调用的耗时：
  A 现状：dpr2 大图 + 每帧 setGeometry + setPixmap(尺寸变时) + setWindowOpacity(<1 → layered)
  B dpr1（1600x1000）同上
  C 现状但 opacity 恒 1（不 layered）
  D 几何固定满屏、每帧只 setWindowOpacity（layered，25MB）
  E 几何固定满屏、每帧只 setWindowOpacity + dpr1
  F 单个 label（去掉 B 层/底色层）
  G 每帧 setGeometry + setPixmap，但 pixmap 恒为满屏尺寸（不做 FastTransformation）
  H 每帧 setGeometry，pixmap 一次性预缩放到满屏 + 只 setPixmap 一次（不换图）

用法：.venv\\Scripts\\python.exe devtools/_winfx_cover_bench.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, QRect, Qt, QTimer          # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel            # noqa: E402

CHECK = ROOT / "_check"
LOCK = CHECK / ".app_run.lock"


def acquire_lock(max_wait: float = 240.0) -> None:
    t0 = time.time()
    while True:
        try:
            LOCK.mkdir()
            return
        except FileExistsError:
            if time.time() - t0 > max_wait:
                raise SystemExit("锁被占用")
            time.sleep(0.5)


def release_lock() -> None:
    try:
        for p in LOCK.glob("*"):
            p.unlink()
        LOCK.rmdir()
    except OSError:
        pass


def make_pix(w: int, h: int) -> QPixmap:
    """造一张有文字/边框的内容，尺寸=物理像素。"""
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(246, 247, 249))
    p = QPainter(img)
    p.setPen(QColor(40, 44, 52))
    p.setFont(QFont("Consolas", max(8, h // 90)))
    for i in range(0, h, max(14, h // 60)):
        p.drawText(8, i + 10, "12:34:56.789  RX  0A 1B 2C 3D 4E 5F 60 71 82 93 A4 B5 C6 " * 3)
    p.setPen(QColor(30, 90, 200))
    p.drawRect(1, 1, w - 3, h - 3)
    p.end()
    return QPixmap.fromImage(img)


def run_variant(app, name: str, *, dpr: float, layered: bool, resize: bool,
                rescale: bool, layers: int, frames: int = 60) -> dict:
    scr = app.primaryScreen()
    geo = scr.geometry()
    W, H = int(geo.width() * dpr), int(geo.height() * dpr)
    big = make_pix(W, H)                       # 满屏物理像素大图（_cover_prescale 的产物）

    start = QRect(int(geo.x() + geo.width() * 0.15), int(geo.y() + geo.height() * 0.12),
                  int(geo.width() * 0.70), int(geo.height() * 0.76))
    end = QRect(geo)

    labels = []
    for _ in range(layers):
        lab = QLabel(None)
        lab.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint
                           | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        lab.setAttribute(Qt.WA_ShowWithoutActivating, True)
        lab.setScaledContents(False)
        lab.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lab.setGeometry(start)
        small = big.scaled(int(start.width() * dpr), int(start.height() * dpr),
                           Qt.IgnoreAspectRatio, Qt.FastTransformation)
        small.setDevicePixelRatio(dpr)
        lab.setPixmap(small)
        lab.setWindowOpacity(1.0 if not layered else 0.99)
        lab.show()
        labels.append(lab)
    app.processEvents()

    rec: list[dict] = []
    state = {"i": 0, "t0": 0.0, "last": 0.0}

    def tick() -> None:
        i = state["i"]
        if i == 0:
            state["t0"] = time.perf_counter()
        t = i / (frames - 1)
        e = 1.0 - (1.0 - t) ** 3
        w = int(round(start.width() + (end.width() - start.width()) * e))
        h = int(round(start.height() + (end.height() - start.height()) * e))
        x = int(round(start.x() + (end.x() - start.x()) * e))
        y = int(round(start.y() + (end.y() - start.y()) * e))
        costs = {}
        for lab in labels:
            if resize:
                t1 = time.perf_counter()
                lab.setGeometry(x, y, w, h)
                costs["setGeometry"] = costs.get("setGeometry", 0) + (
                    time.perf_counter() - t1) * 1e3
            if rescale:
                t1 = time.perf_counter()
                sc = big.scaled(max(1, int(w * dpr)), max(1, int(h * dpr)),
                                Qt.IgnoreAspectRatio, Qt.FastTransformation)
                sc.setDevicePixelRatio(dpr)
                lab.setPixmap(sc)
                costs["rescale"] = costs.get("rescale", 0) + (
                    time.perf_counter() - t1) * 1e3
            if layered:
                t1 = time.perf_counter()
                lab.setWindowOpacity(1.0 - 0.7 * e)
                costs["opacity"] = costs.get("opacity", 0) + (
                    time.perf_counter() - t1) * 1e3
        now = time.perf_counter()
        rec.append({"i": i, "pc": (now - state["t0"]) * 1e3,
                    "gap": (now - state["last"]) * 1e3 if i else 0.0,
                    "body": sum(costs.values()), "costs": costs})
        state["last"] = now
        state["i"] += 1
        if state["i"] >= frames:
            timer.stop()
            for lab in labels:
                lab.hide()
                lab.deleteLater()
            app.processEvents()
            app.quit()

    timer = QTimer()
    timer.setTimerType(Qt.PreciseTimer)
    timer.setInterval(16)
    timer.timeout.connect(tick)
    timer.start()
    app.exec()
    gaps = [r["gap"] for r in rec[1:]]
    body = [r["body"] for r in rec]
    span = rec[-1]["pc"]
    out = {
        "variant": name, "dpr": dpr, "layered": layered, "layers": layers,
        "resize": resize, "rescale": rescale,
        "frames": len(rec), "span_ms": round(span, 1),
        "fps_eff": round((len(rec) - 1) / (span / 1e3), 1),
        "gap_p50": round(statistics.median(gaps), 1),
        "gap_p95": round(sorted(gaps)[int(len(gaps) * 0.95) - 1], 1),
        "gap_max": round(max(gaps), 1),
        "body_p50": round(statistics.median(body), 2),
        "body_max": round(max(body), 1),
        "gap_hist": {str(int(g // 10 * 10)): gaps.count(g)
                     for g in gaps},
    }
    print(json.dumps(out, ensure_ascii=False))
    return out


def main() -> None:
    CHECK.mkdir(exist_ok=True)
    acquire_lock()
    try:
        app = QApplication(sys.argv[:1])
        variants = [
            ("A dpr2 + resize + rescale + layered(现状)", dict(dpr=2.0, layered=True, resize=True, rescale=True, layers=2)),
            ("B dpr1 + resize + rescale + layered", dict(dpr=1.0, layered=True, resize=True, rescale=True, layers=2)),
            ("C dpr2 + resize + rescale + 不透明", dict(dpr=2.0, layered=False, resize=True, rescale=True, layers=2)),
            ("D dpr2 满屏固定 + 只 opacity(layered)", dict(dpr=2.0, layered=True, resize=False, rescale=False, layers=2)),
            ("E dpr1 满屏固定 + 只 opacity(layered)", dict(dpr=1.0, layered=True, resize=False, rescale=False, layers=2)),
            ("F dpr2 + resize + rescale + layered(单层)", dict(dpr=2.0, layered=True, resize=True, rescale=True, layers=1)),
            ("G dpr2 + resize + 不换图 + layered", dict(dpr=2.0, layered=True, resize=True, rescale=False, layers=2)),
            ("H dpr1 + resize + 不换图 + layered", dict(dpr=1.0, layered=True, resize=True, rescale=False, layers=2)),
        ]
        results = []
        for name, kw in variants:
            results.append(run_variant(app, name, **kw))
            time.sleep(0.4)
        (CHECK / "_winfx_cover_bench.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
