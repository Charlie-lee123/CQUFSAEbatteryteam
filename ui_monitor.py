# -*- coding: utf-8 -*-
"""数据监控面板：独立浮窗 + 勾选通道 + JSONL 历史回看。

设计约束（务必保持）：
  · ⛔ 纯 QPainter 折线图，不依赖 QtCharts / matplotlib / 任何新依赖
    （宿主 ``SerialAssistant.spec`` 明确排除了 PySide6.QtCharts）。
  · ⛔ 不写全局 QSS、不碰宿主 QSettings：自己的配置/样本落在自己的 json/jsonl 文件里。
  · ⛔ 挂到接收链路上的 ``push_sample`` 只做内存追加 + 节流写盘，任何失败只记日志。

数据目录：``%LOCALAPPDATA%/CQUFSAE-SerialAssistant/monitor``；
没有 LOCALAPPDATA 时退到 ``~/.CQUFSAE-SerialAssistant/monitor``。
``SERIALASSISTANT_MONITOR_DIR`` 仅供探针隔离用户目录用（仿 DLC 的环境变量隔离）。
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDateTime, QElapsedTimer, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QCheckBox, QDateTimeEdit, QDialog, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import app_log

#: (key, 标题, 单位) —— 顺序即界面顺序、也是默认勾选顺序
CHANNELS: tuple[tuple[str, str, str], ...] = (
    ("soc", "SOC", "%"),
    ("soc_read", "上报 SOC", "%"),
    ("pack_v", "总压", "V"),
    ("i_eff", "电流", "A"),
    ("temp", "温度", "°C"),
    ("cell_min", "单体最低", "mV"),
    ("cell_max", "单体最高", "mV"),
    ("cell_mean", "单体均值", "mV"),
)
CHANNEL_KEYS: tuple[str, ...] = tuple(key for key, _, _ in CHANNELS)
CHANNEL_META: dict[str, tuple[str, str]] = {
    key: (title, unit) for key, title, unit in CHANNELS
}
#: 默认前五颗勾上，三颗单体电压默认不勾
DEFAULT_CHANNELS: frozenset[str] = frozenset(CHANNEL_KEYS[:5])

MAX_RT_SAMPLES = 6000          # 实时环形缓冲上限（约 10 分钟 @10Hz）
MAX_HISTORY_LINES = 20000      # 回看最多读最近这么多行
FLUSH_INTERVAL_MS = 1000       # 落盘节流：每秒最多一次
UNSELECTED_VEHICLE = "未选择车号"


def default_data_dir() -> Path:
    """面板自己的数据目录（⛔ 不碰 QSettings）。"""
    override = os.environ.get("SERIALASSISTANT_MONITOR_DIR")
    if override:
        base = Path(override)
    else:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            base = Path(local) / "CQUFSAE-SerialAssistant" / "monitor"
        else:
            base = Path.home() / ".CQUFSAE-SerialAssistant" / "monitor"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        app_log.error(f"监控面板数据目录创建失败：{exc}")
    return base


def safe_vehicle_name(name: str | None) -> str:
    """车号 -> 安全文件名：只留字母数字下划线短横线与汉字，其余换下划线。"""
    cleaned = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "_", str(name or ""))
    return cleaned or UNSELECTED_VEHICLE


def _tail_lines(path: Path, limit: int) -> list[bytes]:
    """从文件尾部倒着捞最多 ``limit`` 行，避免大文件整本读进内存。"""
    size = path.stat().st_size
    if size <= 0:
        return []
    block = 65536
    with path.open("rb") as handle:
        pos = size
        newlines = 0
        while pos > 0 and newlines <= limit:
            step = min(block, pos)
            pos -= step
            handle.seek(pos)
            newlines += handle.read(step).count(b"\n")
        handle.seek(pos)
        data = handle.read()
    return data.splitlines()[-limit:]


class MonitorChart(QWidget):
    """单通道折线图（纯 QPainter）。

    样本是 ``(ts: float, value: float)``，``ts`` 用 ``time.time()`` 墙钟秒，
    横轴按真实时间间距绘制，所以串口偶发停顿不会被画成等间距假象。
    """

    def __init__(self, key: str = "", title: str = "", unit: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.title = title
        self.unit = unit
        self._samples: list[tuple[float, float]] = []
        self.setMinimumHeight(92)
        self.setToolTip(f"{title or '通道'} 曲线（横轴为真实时间）")

    def set_meta(self, title: str, unit: str) -> None:
        self.title, self.unit = title, unit
        self.update()

    def samples(self) -> list[tuple[float, float]]:
        return list(self._samples)

    def set_values(self, values) -> None:
        """灌入样本（只 ``update()``，不 ``repaint()``）。

        兼容 ``[(ts, value), ...]`` 与裸数值序列（后者按 0..n-1 当横轴）。
        """
        result: list[tuple[float, float]] = []
        for item in values or ():
            try:
                if isinstance(item, (tuple, list)) and len(item) >= 2:
                    ts, value = float(item[0]), float(item[1])
                else:
                    ts, value = float(item), float(item)
                if ts == ts and value == value:      # 丢掉 NaN
                    result.append((ts, value))
            except (TypeError, ValueError):
                continue
        self._samples = result
        self.update()

    def clear(self) -> None:
        self._samples = []
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            pal = self.palette()
            grid = pal.color(QPalette.ColorRole.Mid)
            ink = pal.color(QPalette.ColorRole.WindowText)
            muted = pal.color(QPalette.ColorRole.PlaceholderText)
            r = self.rect().adjusted(8, 8, -8, -8)
            painter.setPen(QPen(grid, 1))
            for i in range(5):
                y = r.top() + r.height() * i // 4
                painter.drawLine(r.left(), y, r.right(), y)
            if not self._samples:
                painter.setPen(muted)
                painter.drawText(r, Qt.AlignmentFlag.AlignCenter, "等待数据 …")
                return

            values = [value for _, value in self._samples]
            lo, hi = min(values), max(values)
            span = hi - lo
            if span <= 1e-9:
                pad = max(abs(hi) * 0.01, 1.0)
                lo, hi = lo - pad, hi + pad
            else:
                pad = span * 0.08
                lo, hi = lo - pad, hi + pad

            # 点太多时限密到约每像素一个，避免每帧画几千条线拖慢 UI
            n = len(self._samples)
            stride = max(1, n // max(32, r.width()))
            shown = list(self._samples[::stride])
            if shown[-1] != self._samples[-1]:
                shown.append(self._samples[-1])
            ts_first, ts_last = self._samples[0][0], self._samples[-1][0]
            ts_span = ts_last - ts_first

            def point_at(index: int, ts: float, value: float) -> tuple[int, int]:
                if ts_span > 1e-9:
                    ratio = (ts - ts_first) / ts_span
                else:
                    ratio = index / max(1, len(shown) - 1)
                ratio = max(0.0, min(1.0, ratio))
                x = r.left() + int(r.width() * ratio)
                y = r.bottom() - int((value - lo) / (hi - lo) * r.height())
                return x, max(r.top(), min(r.bottom(), y))

            points = [point_at(i, ts, value)
                      for i, (ts, value) in enumerate(shown)]
            painter.setPen(QPen(ink, 2))
            for first, second in zip(points, points[1:]):
                painter.drawLine(first[0], first[1], second[0], second[1])
            if len(points) == 1:
                x, y = points[0]
                painter.setBrush(QColor(ink))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(x - 3, y - 3, 6, 6)

            label = f"{self.title} {values[-1]:.2f}{self.unit}"
            painter.setPen(ink)
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                             label)
            right = r.adjusted(int(r.width() * 0.6), 0, 0, 0)
            painter.drawText(right, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                             f"hi {hi:.2f}")
            painter.drawText(right, Qt.AlignmentFlag.AlignRight
                             | Qt.AlignmentFlag.AlignBottom, f"lo {lo:.2f}")
        finally:
            painter.end()


class MonitorPanel(QDialog):
    """独立非模态浮窗：勾选通道画图、JSONL 落盘、按时间窗回看。

    主窗持有强引用（懒建单例）；关窗只 hide，不 ``deleteLater()``；
    退出/清空时 ``flush()`` 强制把节流窗口里的样本写下去。

    ⛔⛔ **必须继承 QDialog（或至少带 Qt.Window 标志）**：它曾被写成裸
    ``QWidget`` + ``parent=主窗`` —— 那样 ``show()`` 出来的是主窗里的**子控件**
    而不是独立窗口（实测 ``isWindow()=False``、几何 (0,0,900,640)），控件直接
    糊在页头和接收区上、没有标题栏也关不掉，用户报「监控面板不可用」。
    与「查看标记」弹窗同一套做法（``QDialog(self)`` + ``show()``）。
    """

    def __init__(self, parent: QWidget | None = None,
                 data_dir: Path | str | None = None) -> None:
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModality.NonModal)
        # Qt.Tool：跟着主窗走、**始终压在主窗之上**（主窗默认整窗全屏，普通窗口
        # 一切换焦点就被全屏的父窗盖住，看起来像"点没了"），且不占任务栏；
        # 主窗最小化时它也自动收起。用户要的是"边收数据边看曲线"，必须一直可见。
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint)
        self._data_dir = (Path(data_dir) if data_dir is not None
                          else default_data_dir())
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self.setWindowTitle("数据监控面板")
        self.resize(900, 640)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._cfg = self._load_cfg()
        self._vehicle = UNSELECTED_VEHICLE
        self._samples_path = self._path_for(UNSELECTED_VEHICLE)
        self._rt: dict[str, deque[tuple[float, float]]] = {
            key: deque(maxlen=MAX_RT_SAMPLES) for key in CHANNEL_KEYS}
        self._rt_count = 0
        self._rt_first: float | None = None
        self._rt_last: float | None = None
        self._pending: list[str] = []
        self._flush_timer = QElapsedTimer()
        self._flush_timer.start()
        self._history_mode = False
        self._history_data: dict[str, list[tuple[float, float]]] = {}
        self._history_count = 0
        self._history_first: float | None = None
        self._history_last: float | None = None
        self._charts: dict[str, MonitorChart] = {}
        self._boxes: dict[str, QCheckBox] = {}

        self._build_ui()
        self._apply_cfg()
        self._update_status()

    # ------------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        top = QHBoxLayout()
        self.lbl_vehicle = QLabel(f"车号: {self._vehicle}")
        top.addWidget(self.lbl_vehicle)
        top.addStretch(1)
        self.btn_clear = QPushButton("清空曲线")
        self.btn_clear.setToolTip("清掉实时缓冲里的曲线；已落盘的历史样本仍可回看")
        self.btn_clear.clicked.connect(self.clear)
        self.btn_open_dir = QPushButton("打开数据目录")
        self.btn_open_dir.clicked.connect(self._open_data_dir)
        top.addWidget(self.btn_clear)
        top.addWidget(self.btn_open_dir)
        root.addLayout(top)

        channels_row = QHBoxLayout()
        channels_row.addWidget(QLabel("显示通道"))
        boxes = QGridLayout()
        boxes.setContentsMargins(0, 0, 0, 0)
        boxes.setHorizontalSpacing(10)
        boxes.setVerticalSpacing(2)
        for index, (key, title, _unit) in enumerate(CHANNELS):
            box = QCheckBox(title)
            box.setToolTip(f"勾选显示「{title}」曲线")
            box.toggled.connect(
                lambda checked, channel=key: self._on_channel_toggled(channel, checked))
            self._boxes[key] = box
            boxes.addWidget(box, index // 4, index % 4)
        channels_row.addLayout(boxes)
        channels_row.addStretch(1)
        root.addLayout(channels_row)

        history_row = QHBoxLayout()
        history_row.addWidget(QLabel("起始"))
        now = QDateTime.currentDateTime()
        self.dt_start = QDateTimeEdit(now.addSecs(-3600))
        self.dt_end = QDateTimeEdit(now)
        for edit in (self.dt_start, self.dt_end):
            edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            edit.setCalendarPopup(True)
        history_row.addWidget(self.dt_start)
        history_row.addWidget(QLabel("结束"))
        history_row.addWidget(self.dt_end)
        self.btn_load_history = QPushButton("加载历史")
        self.btn_load_history.clicked.connect(self.load_history)
        self.btn_realtime = QPushButton("返回实时")
        self.btn_realtime.clicked.connect(self.back_to_realtime)
        history_row.addWidget(self.btn_load_history)
        history_row.addWidget(self.btn_realtime)
        history_row.addStretch(1)
        root.addLayout(history_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self._scroll_host = QWidget()
        self._charts_layout = QVBoxLayout(self._scroll_host)
        self._charts_layout.setContentsMargins(0, 0, 0, 0)
        self._charts_layout.setSpacing(8)
        self._charts_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self._scroll_host)
        root.addWidget(self.scroll, 1)

        self.lbl_status = QLabel("采样点 0")
        self.lbl_mode = QLabel("历史模式：可回看指定时间窗；实时样本仍继续采集写盘，"
                               "但不刷图表。点「返回实时」切回。")
        self.lbl_mode.setWordWrap(True)
        root.addWidget(self.lbl_status)
        root.addWidget(self.lbl_mode)

    # ------------------------------------------------------------------ 配置

    def _load_cfg(self) -> dict:
        try:
            data = json.loads((self._data_dir / "settings.json").read_text("utf-8"))
            if isinstance(data, dict) and isinstance(data.get("channels"), list):
                picked = [str(x) for x in data["channels"] if x in CHANNEL_KEYS]
                return {"channels": picked}
        except (OSError, ValueError):
            pass
        return {"channels": list(DEFAULT_CHANNELS)}

    def _save_cfg(self) -> None:
        try:
            (self._data_dir / "settings.json").write_text(
                json.dumps(self._cfg, ensure_ascii=False, indent=2), "utf-8")
        except OSError as exc:
            app_log.error(f"监控面板设置保存失败：{exc}")

    def _apply_cfg(self) -> None:
        picked = set(self._cfg.get("channels") or [])
        for key, box in self._boxes.items():
            box.blockSignals(True)
            box.setChecked(key in picked)
            box.blockSignals(False)
        self._rebuild_charts()

    def _on_channel_toggled(self, key: str, checked: bool) -> None:
        try:
            picked = [k for k in CHANNEL_KEYS if self._boxes[k].isChecked()]
            self._cfg["channels"] = picked
            self._save_cfg()
            self._rebuild_charts()
        except Exception as exc:                  # noqa: BLE001 - 勾选绝不能崩主窗
            app_log.error(f"监控面板切换通道失败：{exc}")

    def _rebuild_charts(self) -> None:
        """按当前勾选重建竖排布局（复用 chart 对象，样本不丢）。"""
        while self._charts_layout.count():
            item = self._charts_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
        for key in CHANNEL_KEYS:
            if not self._boxes[key].isChecked():
                continue
            chart = self._charts.get(key)
            if chart is None:
                title, unit = CHANNEL_META[key]
                chart = MonitorChart(key, title, unit)
                self._charts[key] = chart
            source = self._history_data if self._history_mode else self._rt
            chart.set_values(source.get(key, ()))
            self._charts_layout.addWidget(chart)
            chart.show()
        self._update_status()

    def _open_data_dir(self) -> None:
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._data_dir)))
        except Exception as exc:                  # noqa: BLE001
            app_log.error(f"监控面板打开数据目录失败：{exc}")

    # ------------------------------------------------------------------ 数据

    def _path_for(self, vehicle: str) -> Path:
        return self._data_dir / f"samples-{safe_vehicle_name(vehicle)}.jsonl"

    def set_vehicle(self, vehicle: str | None) -> None:
        """换车：清实时缓冲、重新指向新车号的样本文件并切回实时视图。"""
        name = str(vehicle or "") or UNSELECTED_VEHICLE
        if name == self._vehicle:
            return
        self.flush()
        self._vehicle = name
        self._samples_path = self._path_for(name)
        self.lbl_vehicle.setText(f"车号: {name}")
        self._rt = {key: deque(maxlen=MAX_RT_SAMPLES) for key in CHANNEL_KEYS}
        self._rt_count = 0
        self._rt_first = self._rt_last = None
        self._history_mode = False
        self._history_data = {}
        self._history_count = 0
        self._history_first = self._history_last = None
        self._rebuild_charts()

    def push_sample(self, values: dict, vehicle: str | None = None) -> None:
        """追加一条样本（⛔ 只在主窗拿到有效 res 时调用，失败只记日志）。"""
        try:
            if vehicle is not None and str(vehicle or "") != self._vehicle:
                self.set_vehicle(vehicle)
            ts = float(values.get("ts", time.time()))
            row: dict[str, float] = {"ts": ts}
            for key in CHANNEL_KEYS:
                value = values.get(key)
                if value is None:
                    continue
                number = float(value)
                if number != number:              # NaN 不落盘
                    continue
                row[key] = number
                self._rt[key].append((ts, number))
            if len(row) <= 1:
                return
            self._rt_count += 1
            if self._rt_first is None:
                self._rt_first = ts
            self._rt_last = ts
            self._pending.append(json.dumps(row, ensure_ascii=False))
            if not self._history_mode:
                for key, chart in self._charts.items():
                    chart.set_values(self._rt[key])
                self._update_status()
            if self._flush_timer.elapsed() >= FLUSH_INTERVAL_MS:
                self.flush()
        except Exception as exc:                  # noqa: BLE001 - 绝不拖垮接收链路
            app_log.error(f"监控面板写入样本失败：{exc}")

    def flush(self) -> None:
        """把待写样本一次性 append 到 JSONL 并关句柄（幂等）。"""
        self._flush_timer.restart()
        if not self._pending:
            return
        lines, self._pending = self._pending, []
        try:
            with self._samples_path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError as exc:
            app_log.error(f"监控面板样本落盘失败：{exc}")
            self._pending = (lines + self._pending)[-MAX_HISTORY_LINES:]

    def clear(self) -> None:
        """清空实时曲线（先把节流窗口里的样本强制落盘）。"""
        try:
            self.flush()
            self._rt = {key: deque(maxlen=MAX_RT_SAMPLES) for key in CHANNEL_KEYS}
            self._rt_count = 0
            self._rt_first = self._rt_last = None
            self._history_mode = False
            self._history_data = {}
            self._history_count = 0
            self._history_first = self._history_last = None
            self._rebuild_charts()
        except Exception as exc:                  # noqa: BLE001
            app_log.error(f"监控面板清空失败：{exc}")

    def load_history(self) -> None:
        """读指定时间窗内的最近若干行 JSONL 灌进图表（坏行跳过）。"""
        try:
            start = self.dt_start.dateTime().toSecsSinceEpoch()
            end = self.dt_end.dateTime().toSecsSinceEpoch()
            if end < start:
                start, end = end, start
            data: dict[str, list[tuple[float, float]]] = {
                key: [] for key in CHANNEL_KEYS}
            count = 0
            first = last = None
            if self._samples_path.is_file():
                for raw in _tail_lines(self._samples_path, MAX_HISTORY_LINES):
                    try:
                        row = json.loads(raw.decode("utf-8", "replace"))
                        ts = float(row["ts"])
                    except (ValueError, TypeError, KeyError, AttributeError):
                        continue
                    if not (start <= ts <= end):
                        continue
                    count += 1
                    first = ts if first is None else min(first, ts)
                    last = ts if last is None else max(last, ts)
                    for key in CHANNEL_KEYS:
                        value = row.get(key)
                        if value is None:
                            continue
                        try:
                            number = float(value)
                        except (TypeError, ValueError):
                            continue
                        if number == number:
                            data[key].append((ts, number))
            self._history_data = data
            self._history_count = count
            self._history_first, self._history_last = first, last
            self._history_mode = True
            self._rebuild_charts()
        except Exception as exc:                  # noqa: BLE001
            app_log.error(f"监控面板历史加载失败：{exc}")

    def back_to_realtime(self) -> None:
        """退出回看：图表切回实时环形缓冲（实时样本期间照常写盘没断）。"""
        self._history_mode = False
        self._history_data = {}
        self._history_count = 0
        self._history_first = self._history_last = None
        self._rebuild_charts()

    # ------------------------------------------------------------------ 状态

    def _update_status(self) -> None:
        if self._history_mode:
            count, first, last = (self._history_count, self._history_first,
                                  self._history_last)
        else:
            count, first, last = self._rt_count, self._rt_first, self._rt_last

        def fmt(ts: float | None) -> str:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") \
                if ts is not None else "--"

        picked = sum(1 for k in CHANNEL_KEYS if self._boxes[k].isChecked())
        mode = "历史回看" if self._history_mode else "实时"
        self.lbl_status.setText(
            f"{mode} · 采样点 {count} · 起 {fmt(first)} · 止 {fmt(last)}"
            f" · 显示 {picked}/{len(CHANNEL_KEYS)} 通道 · {self._vehicle}")
        self.lbl_mode.setVisible(True)

    def closeEvent(self, event) -> None:
        """关窗只 hide（不销毁），但必须把节流窗口里的样本落盘。"""
        try:
            self.flush()
        except Exception as exc:                  # noqa: BLE001
            app_log.error(f"监控面板关窗落盘失败：{exc}")
        super().closeEvent(event)
