"""覆盖层微基准 v2：寻找「不逐帧 SetWindowPos」的可行实现。

A~H 见 _winfx_cover_bench.py。本轮只测**几何固定、只改内容**的候选：
  I  固定尺寸、每帧 move()（位置变、尺寸不变，layered，2 层）
  J  满屏固定 + setMask(当前矩形) + setPixmap(FastTransformation)（不 translucent）
  K  满屏固定 + WA_TranslucentBackground + paintEvent 里 drawPixmap(target_rect)  dpr2
  L  同上 dpr1
  M  满屏固定 + 不透明 + paintEvent 画 scaled + 窗外填底色（只测绘制成本）
  N  满屏固定 + setMask + 只 move 不改 mask/图（mask 成本隔离）
  O  现状路径但窗口类型用 Qt.Window|Frameless（非 ToolTip）

用法：.venv\\Scripts\\python.exe devtools/_winfx_cover_bench2.py
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

from PySide6.QtCore import QRect, QTimer, Qt                # noqa: E402
from PySide6.QtGui import (QColor, QFont, QImage, QPainter,  # noqa: E402
                           QPixmap, QRegion)
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

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


class Stage(QWidget):
    """满屏固定、半透明、自己画笔的舞台窗（候选 K/L/M）。"""

    def __init__(self, translucent: bool, opaque_fill: bool) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        if translucent:
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.pix: QPixmap | None = None
        self.target = QRect()
        self.opaque_fill = opaque_fill

    def paintEvent(self, ev) -> None:            # noqa: N802
        p = QPainter(self)
        if self.opaque_fill:
            p.fillRect(self.rect(), QColor(32, 34, 40))
        if self.pix is not None and not self.target.isEmpty():
            p.drawPixmap(self.target, self.pix)
        p.end()


def bench(app, name: str, *, dpr: float, kind: str, frames: int = 60) -> dict:
    scr = app.primaryScreen()
    geo = scr.geometry()
    W, H = int(geo.width() * dpr), int(geo.height() * dpr)
    big = make_pix(W, H)
    start = QRect(int(geo.x() + geo.width() * 0.15), int(geo.y() + geo.height() * 0.12),
                  int(geo.width() * 0.70), int(geo.height() * 0.76))
    end = QRect(geo)

    widgets = []
    stage = None
    labels = []
    if kind in ("J", "N"):
        for _ in range(2):
            lab = QLabel(None)
            lab.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint
                               | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
            lab.setAttribute(Qt.WA_ShowWithoutActivating, True)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            lab.setGeometry(geo)
            pm = big.scaled(int(start.width() * dpr), int(start.height() * dpr),
                            Qt.IgnoreAspectRatio, Qt.FastTransformation)
            pm.setDevicePixelRatio(dpr)
            lab.setPixmap(pm)
            lab.setMask(QRegion(start))
            lab.show()
            labels.append(lab)
            widgets.append(lab)
    elif kind in ("I",):
        for _ in range(2):
            lab = QLabel(None)
            lab.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint
                               | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
            lab.setAttribute(Qt.WA_ShowWithoutActivating, True)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            lab.setGeometry(start)
            pm = big.scaled(int(start.width() * dpr), int(start.height() * dpr),
                            Qt.IgnoreAspectRatio, Qt.FastTransformation)
            pm.setDevicePixelRatio(dpr)
            lab.setPixmap(pm)
            lab.setWindowOpacity(0.99)
            lab.show()
            labels.append(lab)
            widgets.append(lab)
    else:
        stage = Stage(translucent=(kind in ("K", "L")),
                      opaque_fill=(kind == "M"))
        stage.setGeometry(geo)
        stage.pix = big
        stage.target = QRect(start)
        stage.show()
        widgets.append(stage)
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
        cur = QRect(x, y, w, h)
        t1 = time.perf_counter()
        if kind == "I":
            for lab in labels:
                lab.move(x, y)
        elif kind == "N":
            for lab in labels:
                lab.move(x, y)          # mask 不变，只挪位置
        elif kind == "J":
            for lab in labels:
                lab.setMask(QRegion(cur))
                sc = big.scaled(max(1, int(w * dpr)), max(1, int(h * dpr)),
                                Qt.IgnoreAspectRatio, Qt.FastTransformation)
                sc.setDevicePixelRatio(dpr)
                lab.setPixmap(sc)
                lab.move(x, y)
        else:
            stage.target = cur
            stage.update()
        body = (time.perf_counter() - t1) * 1e3
        now = time.perf_counter()
        rec.append({"i": i, "pc": (now - state["t0"]) * 1e3,
                    "gap": (now - state["last"]) * 1e3 if i else 0.0,
                    "body": body})
        state["last"] = now
        state["i"] += 1
        if state["i"] >= frames:
            timer.stop()
            for wd in widgets:
                wd.hide()
                wd.deleteLater()
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
        "variant": name, "dpr": dpr, "kind": kind,
        "fps_eff": round((len(rec) - 1) / (span / 1e3), 1),
        "gap_p50": round(statistics.median(gaps), 1),
        "gap_p95": round(sorted(gaps)[int(len(gaps) * 0.95) - 1], 1),
        "gap_max": round(max(gaps), 1),
        "body_p50": round(statistics.median(body), 2),
        "body_max": round(max(body), 1),
    }
    print(json.dumps(out, ensure_ascii=False))
    return out


def main() -> None:
    CHECK.mkdir(exist_ok=True)
    acquire_lock()
    try:
        app = QApplication(sys.argv[:1])
        cases = [
            ("I 固定尺寸+只 move(2层,layered)", 2.0, "I"),
            ("J 满屏固定+setMask+setPixmap dpr2", 2.0, "J"),
            ("J' 满屏固定+setMask+setPixmap dpr1", 1.0, "J"),
            ("K 满屏 translucent+drawPixmap dpr2", 2.0, "K"),
            ("L 满屏 translucent+drawPixmap dpr1", 1.0, "L"),
            ("M 满屏不透明+drawPixmap dpr2", 2.0, "M"),
            ("N 满屏+mask 只 move(隔离mask成本)", 2.0, "N"),
        ]
        res = []
        for name, dpr, kind in cases:
            res.append(bench(app, name, dpr=dpr, kind=kind))
            time.sleep(0.4)
        (CHECK / "_winfx_cover_bench2.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
