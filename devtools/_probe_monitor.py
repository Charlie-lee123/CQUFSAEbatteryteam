# -*- coding: utf-8 -*-
r"""数据监控面板真平台探针（QT_QPA_PLATFORM=windows）。

覆盖：面板创建 / 8 勾选框 / 勾选增删 chart / 伪造样本（含 None）折线非空 /
JSONL 落盘读回 / 时间窗历史回看 / 实时样本不打架 / 主窗按钮与懒建单例 /
_push_monitor_sample 缺 _soc_read_last 兜底。

⛔ 全程用 SERIALASSISTANT_MONITOR_DIR 指到 _check/monitor_probe，**不碰用户真实
   monitor 目录**；QSettings 前后快照还原；rx_history 目录也指向隔离目录。
"""
from __future__ import annotations

import json
import os
import sys
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

STAGE = Path(_ROOT) / "_check" / "monitor_probe"
DATA = STAGE / "data"
if not str(STAGE).replace("\\", "/").endswith("_check/monitor_probe"):
    raise SystemExit("拒绝清理非预期目录")
if STAGE.exists():
    shutil.rmtree(STAGE)
DATA.mkdir(parents=True)
os.environ["SERIALASSISTANT_MONITOR_DIR"] = str(DATA)

from PySide6.QtCore import QDateTime, QEvent, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QPixmap, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox  # noqa: E402

import customization  # noqa: E402
import main_window  # noqa: E402
import rx_history  # noqa: E402
import soc_est  # noqa: E402
import ui_controls  # noqa: E402
import ui_monitor  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
VEHICLE = "E74-2026"


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def pump(app, n: int = 8) -> None:
    for _ in range(n):
        app.processEvents()


def render_img(widget) -> QImage:
    pixmap = QPixmap(widget.size())
    pixmap.fill(Qt.GlobalColor.white)
    widget.render(pixmap)
    return pixmap.toImage()


def image_diff(a: QImage, b: QImage) -> int:
    if a.size() != b.size():
        return -1
    return sum(1 for y in range(0, a.height(), 2) for x in range(0, a.width(), 2)
               if a.pixelColor(x, y) != b.pixelColor(x, y))


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}
    real_dir = rx_history._resolve_dir
    real_vehicle = customization.current_vehicle
    rx_history._resolve_dir = lambda: STAGE / "rx"
    customization.current_vehicle = lambda: VEHICLE
    panel = None
    win = None
    try:
        # ---------------- ① 面板创建 / 默认勾选 ----------------
        panel = ui_monitor.MonitorPanel(data_dir=DATA)
        panel.set_vehicle(VEHICLE)
        panel.resize(900, 640)
        panel.show()
        pump(app)
        boxes = panel.findChildren(QCheckBox)
        texts = [b.text() for b in boxes]
        expected_texts = [title for _key, title, _unit in ui_monitor.CHANNELS]
        check("① 面板窗口标题/尺寸/非模态",
              panel.windowTitle() == "数据监控面板"
              and panel.width() > 800 and panel.height() > 500
              and not panel.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose),
              f"{panel.windowTitle()} {panel.width()}x{panel.height()}")
        check("① 8 个通道勾选框齐全", len(boxes) == 8 and texts == expected_texts,
              str(texts))
        checked = [k for k in ui_monitor.CHANNEL_KEYS if panel._boxes[k].isChecked()]
        check("① 默认勾选前 5 个", checked == list(ui_monitor.CHANNEL_KEYS[:5]),
              str(checked))

        # ---------------- ② 勾选变化 -> chart 增删 ----------------
        def chart_keys():
            return [c.key for c in panel._charts.values() if c.isVisible()]

        check("② 初始 5 张图", chart_keys() == list(ui_monitor.CHANNEL_KEYS[:5]),
              str(chart_keys()))
        panel._boxes["soc"].setChecked(False)
        pump(app)
        after_off = chart_keys()
        check("② 取消 soc -> 4 张图", after_off == list(ui_monitor.CHANNEL_KEYS[1:5]),
              str(after_off))
        panel._boxes["cell_min"].setChecked(True)
        pump(app)
        after_on = chart_keys()
        check("② 勾选 cell_min -> 5 张图（复用样本）",
              after_on == ["soc_read", "pack_v", "i_eff", "temp", "cell_min"],
              str(after_on))
        saved_cfg = json.loads((DATA / "settings.json").read_text("utf-8"))
        check("② 勾选配置写入自己的 settings.json",
              saved_cfg.get("channels") == after_on, str(saved_cfg))
        # 恢复默认勾选（后面按前 5 个验证折线）
        for key in ui_monitor.CHANNEL_KEYS:
            panel._boxes[key].setChecked(key in ui_monitor.DEFAULT_CHANNELS)
        pump(app)

        # ---------------- ③ 伪造样本含 None -> 折线非空 ----------------
        base = time.time() - 60
        pushed = 30
        for i in range(pushed):
            panel.push_sample({
                "ts": base + i,
                "soc": 40.0 + i * 0.6,
                "soc_read": None,                    # 明确喂 None
                "pack_v": 400.0 + i * 0.2,
                "i_eff": -3.0 + i * 0.1,
                "temp": 25.0 + i * 0.05,
                "cell_min": 3300.0 + i,
                "cell_max": None,                    # 明确喂 None
                "cell_mean": 3350.0 + i,
            }, vehicle=VEHICLE)
        panel.flush()
        pump(app)
        chart = panel._charts["soc"]
        with_data = render_img(chart)
        saved_samples = chart.samples()
        chart.clear()
        no_data = render_img(chart)
        chart.set_values(saved_samples)
        pump(app)
        diff = image_diff(with_data, no_data)
        with_data.save(str(STAGE / "chart_with_data.png"))
        no_data.save(str(STAGE / "chart_empty.png"))
        check("③ push 后 chart 有折线（render 与非空态不同）", diff > 20,
              f"diff={diff} samples={len(saved_samples)}")
        check("③ None 通道不写样本",
              len(panel._rt["soc_read"]) == 0 and len(panel._rt["cell_max"]) == 0,
              f"soc_read={len(panel._rt['soc_read'])} cell_max={len(panel._rt['cell_max'])}")
        check("③ 非勾选通道仍照常采集（cell_min）",
              len(panel._rt["cell_min"]) == pushed, str(len(panel._rt["cell_min"])))
        panel.grab().save(str(STAGE / "monitor_panel.png"))

        # ---------------- ④ JSONL 落盘读回 ----------------
        path = DATA / f"samples-{VEHICLE}.jsonl"
        check("④ JSONL 文件名按车号安全化且已落盘", path.is_file(), str(path))
        lines = [ln for ln in path.read_text("utf-8").splitlines() if ln.strip()]
        bad = [ln for ln in lines if not ln.startswith("{")]
        check("④ 落盘行数 = push 次数且无坏行",
              len(lines) == pushed and not bad, f"lines={len(lines)} bad={len(bad)}")
        rows = [json.loads(ln) for ln in lines]
        check("④ 字段正确：ts 为 float、None 字段不出现",
              all(isinstance(r.get("ts"), float) and "soc_read" not in r
                  and "cell_max" not in r for r in rows),
              f"first={rows[0] if rows else None}")

        # 坏行容错：手工插一条垃圾行再回看，不能崩
        with path.open("a", encoding="utf-8") as fh:
            fh.write("{this is not json}\n")

        # ---------------- ⑤ 历史时间窗加载 ----------------
        panel.dt_start.setDateTime(QDateTime.fromSecsSinceEpoch(int(base) - 5))
        panel.dt_end.setDateTime(QDateTime.fromSecsSinceEpoch(int(base) + pushed + 5))
        pump(app)
        panel.load_history()
        pump(app)
        check("⑤ 历史模式生效且坏行跳过",
              panel._history_mode and panel._history_count == pushed,
              f"mode={panel._history_mode} count={panel._history_count}")
        hist_chart = panel._charts["soc"]
        check("⑤ 历史样本灌进同一套 chart",
              len(hist_chart.samples()) == pushed, str(len(hist_chart.samples())))
        # 历史模式下继续 push：写盘继续、图表不打架
        panel.push_sample({"soc": 999.0, "pack_v": 1.0, "i_eff": 1.0, "temp": 1.0,
                           "cell_min": 1.0, "cell_mean": 1.0}, vehicle=VEHICLE)
        panel.flush()
        pump(app)
        check("⑤ 历史模式下实时样本不刷图表",
              len(hist_chart.samples()) == pushed
              and len(panel._rt["soc"]) == pushed + 1,
              f"chart={len(hist_chart.samples())} rt={len(panel._rt['soc'])}")
        lines2 = path.read_text("utf-8").splitlines()
        check("⑤ 历史模式下实时样本继续落盘", len(lines2) == pushed + 2,
              f"lines={len(lines2)}（含 1 条坏行）")
        panel.back_to_realtime()
        pump(app)
        check("⑤ 返回实时用环形缓冲重绘",
              not panel._history_mode and len(panel._charts["soc"].samples()) == pushed + 1,
              str(len(panel._charts["soc"].samples())))
        # 清空曲线：强制落盘且不改历史文件已有内容
        before_clear = len(path.read_text("utf-8").splitlines())
        panel.clear()
        pump(app)
        check("⑤ 清空曲线后图表等待数据",
              len(panel._charts["soc"].samples()) == 0
              and len(path.read_text("utf-8").splitlines()) == before_clear,
              f"lines={len(path.read_text('utf-8').splitlines())}")

        # ---------------- ⑥ 主窗按钮 / 懒建单例 ----------------
        win = main_window.MainWindow()
        win.resize(1400, 900)
        win.show()
        pump(app, 12)
        btn = getattr(win, "btn_monitor", None)
        check("⑥ 按钮存在且是 RowButton",
              isinstance(btn, ui_controls.RowButton) and btn.text() == "监控面板",
              f"type={type(btn).__name__} text={getattr(btn, 'text', lambda: '?')()}")
        check("⑥ tooltip 文案",
              "勾选" in btn.toolTip() and "回看历史" in btn.toolTip(),
              btn.toolTip())
        # 用户 2026-11-25：监控面板排「数据工具」第一颗（查看标记之上）
        box_layout = win.actions_box.layout()
        actions_layout = next(
            (box_layout.itemAt(i).layout() for i in range(box_layout.count())
             if box_layout.itemAt(i).layout() is not None
             and box_layout.itemAt(i).layout().count() >= 5), None)
        order = ([actions_layout.itemAt(i).widget().text()
                  for i in range(actions_layout.count())
                  if actions_layout.itemAt(i).widget() is not None]
                 if actions_layout is not None else [])
        check("⑥ 按钮顺序：监控面板在查看标记之上",
              order == ["监控面板", "查看标记", "打开近期数据",
                        "打开历史数据", "历史报错数据"],
              str(order))
        widths = [getattr(win, attr).width() for attr in (
            "btn_monitor", "btn_marks", "btn_open_history",
            "btn_open_long_history", "btn_error_history")]
        check("⑥ 前两颗与后三颗等宽", max(widths) - min(widths) <= 1,
              str(widths))
        btn.click()
        pump(app)
        p1 = win._monitor_panel
        check("⑥ 点击按钮打开面板",
              p1 is not None and isinstance(p1, ui_monitor.MonitorPanel)
              and p1.isVisible(), f"visible={p1.isVisible() if p1 else None}")
        win._show_monitor()
        pump(app)
        check("⑥ 懒建单例复用同一实例", win._monitor_panel is p1)
        check("⑥ 面板车辆跟随当前车号",
              p1.lbl_vehicle.text() == f"车号: {VEHICLE}", p1.lbl_vehicle.text())

        # ---------------- ⑦ _soc_read_last 缺失兜底 ----------------
        if hasattr(win, "_soc_read_last"):
            delattr(win, "_soc_read_last")
        clean = SimpleNamespace(min_mv=3300.0, max_mv=3400.0, mean_mv=3350.0)
        res = soc_est.SocResult(55.0, 400.0, clean, 1.5, 0.0, 26.0, "ok",
                                False, True, mode="discharge")
        count_before = len(p1._rt["soc"])
        try:
            win._push_monitor_sample(res)
            ok = True
            detail = ""
        except Exception as exc:                    # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        check("⑦ 缺 _soc_read_last 时兜底不崩", ok, detail)
        check("⑦ 兜底后 soc 样本 +1、soc_read 不写",
              len(p1._rt["soc"]) == count_before + 1
              and len(p1._rt["soc_read"]) == 0,
              f"soc={len(p1._rt['soc'])} read={len(p1._rt['soc_read'])}")
        # 门控前的原始 res：soc 应就是 55.0，没有被 copy.copy 替身改写
        check("⑦ 曲线取门控前原始 res",
              p1._rt["soc"][-1][1] == 55.0,
              str(p1._rt["soc"][-1]))

    finally:
        if win is not None:
            win.close()
            for _ in range(90):
                app.processEvents()
                time.sleep(0.006)
            win.deleteLater()
        if panel is not None:
            panel.flush()
            panel.hide()
            panel.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        rx_history._resolve_dir = real_dir
        customization.current_vehicle = real_vehicle
        for key in list(qs.allKeys()):
            if key not in snap:
                qs.remove(key)
        for key, value in snap.items():
            qs.setValue(key, value)
        qs.sync()

    now = {k: qs.value(k) for k in qs.allKeys()}
    check("⑧ QSettings 前后零变化", now == snap, f"keys {len(snap)} -> {len(now)}")
    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    total = len(RESULTS)
    tail = f"\n合计 {passed}/{total} PASS  ->  IS_PASS: {'YES' if passed == total else 'NO'}"
    print(tail)
    (STAGE / "probe_result.txt").write_text(
        "\n".join(f"{'PASS' if ok else 'FAIL'}  {n}" + (f"   [{d}]" if d else "")
                  for n, ok, d in RESULTS) + tail + "\n", "utf-8")
    app.quit()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
