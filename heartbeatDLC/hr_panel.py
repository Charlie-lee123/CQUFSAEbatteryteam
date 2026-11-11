# -*- coding: utf-8 -*-
"""heartbeatDLC —— 多设备心率面板。

三种看法凑在一起，满足「同时显示多台、又能分别查看」：
  · 左侧设备列表：勾选即连接，取消即断开，可任意组合；
  · 「全部设备」标签：每台一张卡片，大字 BPM 同屏对比；
  · 每台设备一个独立标签：大字 BPM、佩戴状态、电量/佩戴位置、统计、趋势。

独立顶层窗口：既可由主程序以 DLC 形式加载，也能 ``python -m heartbeatDLC`` 直接跑。
⛔ 只用 Qt 控件 + palette 颜色，不写全局 QSS（宿主 QSS 里有 @token@ 占位符）。
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QPainter, QPalette, QPen,
)
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget,
    QTabWidget, QVBoxLayout, QWidget,
)

from .hr_protocol import (
    BAND10PRO_HINT, BPM_MAX, BPM_MIN, HRReading, describe_contact, device_label,
    plausible_bpm,
)
from .hr_worker import HeartRateHub, HeartRateScanner
from .hr_visual import HeartVisualWidget

MAX_SAMPLES = 600          # 每台设备各自保留 600 点（约 10 分钟 @1Hz）
MAX_DIAG_LINES = 60
OVERVIEW_COLUMNS = 2


def default_data_dir() -> Path:
    """插件自己的数据目录，和主程序的用户数据同根，**不碰 QSettings**。"""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local) / "CQUFSAE-SerialAssistant" / "dlc" / "heartbeatDLC"
    else:
        base = Path.home() / ".CQUFSAE-SerialAssistant" / "dlc" / "heartbeatDLC"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _tab_title(info: dict) -> str:
    """标签页短标题：去掉小米的冗长前缀，保留型号。"""
    name = str(info.get("name") or info.get("address") or "设备")
    for prefix in ("Xiaomi Smart Band ", "Xiaomi ", "Mi Smart Band ", "Mi Band "):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name[:16] or str(info.get("address", "设备"))[-8:]


class TrendChart(QWidget):
    """最近 N 个 BPM 的折线图（纯 QPainter，不依赖 QtCharts）。

    ⛔ 宿主 ``SerialAssistant.spec`` 排除了 ``PySide6.QtCharts``，插件不能引入。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._values: list[int] = []
        self.setMinimumHeight(120)
        self.setToolTip("这台设备最近的心率趋势（最多 600 个采样点）")

    def set_values(self, values: list[int]) -> None:
        self._values = list(values[-MAX_SAMPLES:])
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            pal = self.palette()
            grid = pal.color(QPalette.ColorRole.Mid)
            ink = pal.color(QPalette.ColorRole.WindowText)
            r = self.rect().adjusted(8, 8, -8, -8)
            painter.setPen(QPen(grid, 1))
            for i in range(5):
                y = r.top() + r.height() * i // 4
                painter.drawLine(r.left(), y, r.right(), y)
            if not self._values:
                painter.setPen(ink)
                painter.drawText(r, Qt.AlignmentFlag.AlignCenter, "等待心率数据 …")
                return
            lo, hi = min(self._values), max(self._values)
            span = max(20, hi - lo)
            mid = (lo + hi) / 2
            bottom, top = mid - span / 2 - 2, mid + span / 2 + 2
            n = len(self._values)
            points = []
            for i, value in enumerate(self._values):
                x = r.left() + (r.width() * i // max(1, n - 1)) if n > 1 else r.left()
                y = r.bottom() - int((value - bottom) / (top - bottom) * r.height())
                points.append((x, max(r.top(), min(r.bottom(), y))))
            painter.setPen(QPen(ink, 2))
            for first, second in zip(points, points[1:]):
                painter.drawLine(first[0], first[1], second[0], second[1])
            warn = QColor(200, 60, 60)
            for (x, y), value in zip(points, self._values):
                if not plausible_bpm(value):
                    painter.setBrush(warn)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(x - 3, y - 3, 6, 6)
            painter.setPen(ink)
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                             f"{hi} bpm")
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                             f"{lo} bpm")
        finally:
            painter.end()


class DeviceDetail(QWidget):
    """单台设备的独立详情页：沉浸心率可视化（默认）+ 可切换的趋势图。

    视觉画布 ``visual`` 用纯 QPainter 绘制（黑底 + 红色大字 BPM + 红色网格 ECG +
    满屏红/白星点场心跳闪红，心宿 jcjt.uno 同款观感），按本设备心率节拍自驱；
    ``lbl_bpm`` 是给挂载探针用的隐藏镜像，``trend`` 是备选视图。
    """

    def __init__(self, info: dict, parent=None) -> None:
        super().__init__(parent)
        self.info = dict(info)
        self.samples: list[dict] = []
        self.meta: dict = {}
        root = QVBoxLayout(self)
        root.setSpacing(8)
        self.lbl_name = QLabel(info.get("name") or info.get("address") or "设备")
        self.lbl_name.setToolTip(info.get("label") or "")
        self.lbl_addr = QLabel(info.get("address") or "")
        self.lbl_state = QLabel("未连接")
        self.lbl_state.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.lbl_stats = QLabel("最小 -- · 平均 -- · 最大 -- · 样本 0")
        self.lbl_meta = QLabel("")

        # 给挂载探针保留的隐藏 BPM 镜像（视觉已在画布上画大字 BPM）
        self.lbl_bpm = QLabel("--")
        self.lbl_bpm.setVisible(False)

        self.btn_view = QPushButton("切换：趋势图")
        self.btn_view.setToolTip("在「粒子/心跳特效」与「BPM 趋势图」之间切换")
        self.btn_view.clicked.connect(self._toggle_view)

        self.stack = QStackedWidget()
        self.visual = HeartVisualWidget()
        self.trend = TrendChart()
        self.stack.addWidget(self.visual)        # 0：沉浸特效（默认）
        self.stack.addWidget(self.trend)         # 1：趋势图
        root.addWidget(self.lbl_name)
        root.addWidget(self.lbl_addr)
        root.addWidget(self.lbl_state)
        root.addWidget(self.lbl_stats)
        root.addWidget(self.lbl_meta)
        root.addWidget(self.btn_view)
        root.addWidget(self.stack, 1)

    def _toggle_view(self) -> None:
        if self.stack.currentIndex() == 0:
            self.stack.setCurrentIndex(1)
            self.btn_view.setText("切换：特效")
        else:
            self.stack.setCurrentIndex(0)
            self.btn_view.setText("切换：趋势图")

    def on_sample(self, sample: dict) -> None:
        self.samples.append(sample)
        self.samples = self.samples[-MAX_SAMPLES:]
        bpm = int(sample.get("bpm", 0))
        self.lbl_bpm.setText(str(bpm))           # 镜像，供挂载探针读取
        self.visual.set_connected(True)
        self.visual.set_bpm(bpm)            # 节拍由可视化按 60000/bpm 自驱（同网站）
        reading = HRReading(
            bpm=bpm, contact=sample.get("contact"),
            energy=sample.get("energy"),
            rr_ms=tuple(sample.get("rr_ms") or ()),
            flags=int(sample.get("flags", 0)))
        note = "" if plausible_bpm(bpm) else f" · 超出常见区间 {BPM_MIN}~{BPM_MAX}"
        self.lbl_state.setText(f"接收中 · {describe_contact(reading)}{note}")
        values = [int(item.get("bpm", 0)) for item in self.samples]
        self.trend.set_values(values)
        self.lbl_stats.setText(
            f"最小 {min(values)} · 平均 {sum(values) / len(values):.0f} · "
            f"最大 {max(values)} · 样本 {len(values)}")

    def on_state(self, text: str) -> None:
        self.lbl_state.setText(str(text).splitlines()[0] if text else "")

    def on_meta(self, meta: dict) -> None:
        self.meta.update(meta)
        parts = []
        if "battery" in self.meta:
            parts.append(f"电量 {self.meta['battery']}%")
        if "location" in self.meta:
            parts.append(f"佩戴位置 {self.meta['location']}")
        self.lbl_meta.setText(" · ".join(parts))

    def set_stopped(self) -> None:
        self.lbl_state.setText("已停止接收（历史数据保留）")
        self.visual.set_connected(False)


class OverviewCard(QFrame):
    """「全部设备」总览里的一张卡片，点一下跳到该设备的详情标签。"""

    def __init__(self, info: dict, on_click, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setToolTip("点击查看这台设备的详情")
        self._on_click = on_click
        root = QVBoxLayout(self)
        root.setSpacing(2)
        self.lbl_name = QLabel(_tab_title(info))
        self.lbl_name.setToolTip(info.get("label") or "")
        self.lbl_bpm = QLabel("--")
        font = QFont()
        font.setPointSize(28)
        font.setBold(True)
        self.lbl_bpm.setFont(font)
        self.lbl_bpm.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.lbl_state = QLabel("未连接")
        self.lbl_state.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self.lbl_name)
        root.addWidget(self.lbl_bpm)
        root.addWidget(self.lbl_state)

    def mousePressEvent(self, _event) -> None:
        self._on_click()
        super().mousePressEvent(_event)

    def on_sample(self, sample: dict) -> None:
        self.lbl_bpm.setText(str(int(sample.get("bpm", 0))))
        self.lbl_state.setText("接收中")

    def on_state(self, text: str) -> None:
        self.lbl_state.setText(str(text).splitlines()[0] if text else "")

    def on_meta(self, meta: dict) -> None:
        if "battery" in meta:
            self.setToolTip(f"电量 {meta['battery']}% · 点击查看这台设备的详情")

    def set_stopped(self) -> None:
        self.lbl_bpm.setText("--")
        self.lbl_state.setText("已停止")


class HeartbeatPanel(QWidget):
    """多设备心率广播 DLC 主面板（非模态独立窗口）。"""

    def __init__(self, ctx=None, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._data_dir = (Path(ctx.data_dir("heartbeatDLC")) if ctx is not None
                          else default_data_dir())
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._cfg = self._load_cfg()
        self._devices: list[dict] = []
        self._by_addr: dict[str, dict] = {}
        self._details: dict[str, DeviceDetail] = {}
        self._cards: dict[str, OverviewCard] = {}
        self._scanner: HeartRateScanner | None = None
        self._hub: HeartRateHub | None = None
        self._retiring: list = []

        self.setWindowTitle("心率广播 DLC · heartbeatDLC")
        self.resize(760, 560)
        self._build_ui()
        self._apply_cfg()

    # ------------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(9)

        top = QHBoxLayout()
        self.btn_scan = QPushButton("扫描设备")
        self.btn_scan.clicked.connect(self._start_scan)
        self.btn_stop = QPushButton("停止全部")
        self.btn_stop.clicked.connect(self._stop_all)
        self.lbl_count = QLabel("未扫描")
        top.addWidget(self.btn_scan)
        top.addWidget(self.btn_stop)
        top.addStretch(1)
        top.addWidget(self.lbl_count)
        root.addLayout(top)

        body = QHBoxLayout()
        self.list_devices = QListWidget()
        self.list_devices.setFixedWidth(250)
        self.list_devices.setToolTip("勾选要接收的设备，可同时选多台")
        self.list_devices.itemChanged.connect(self._on_item_changed)
        body.addWidget(self.list_devices)

        self.tabs = QTabWidget()
        self.overview = QWidget()
        self._overview_grid = QGridLayout(self.overview)
        self._overview_grid.setSpacing(10)
        self._overview_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.overview)
        self.tabs.addTab(scroll, "全部设备")
        body.addWidget(self.tabs, 1)
        root.addLayout(body, 1)

        opts = QHBoxLayout()
        self.chk_csv = QCheckBox("记录到 CSV")
        self.chk_csv.setToolTip("每条心率追加到 hr_log.csv，带设备地址与名称")
        self.chk_auto = QCheckBox("自动恢复上次设备")
        self.chk_auto.setToolTip("扫描完成后自动勾选并连接上次选中的设备")
        self.chk_csv.toggled.connect(self._on_cfg_changed)
        self.chk_auto.toggled.connect(self._on_cfg_changed)
        self.btn_open_dir = QPushButton("打开数据目录")
        self.btn_open_dir.clicked.connect(self._open_data_dir)
        opts.addWidget(self.chk_csv)
        opts.addWidget(self.chk_auto)
        opts.addStretch(1)
        opts.addWidget(self.btn_open_dir)
        root.addLayout(opts)

        self.txt_diag = QPlainTextEdit()
        self.txt_diag.setReadOnly(True)
        self.txt_diag.setMaximumBlockCount(MAX_DIAG_LINES)
        self.txt_diag.setFixedHeight(76)
        root.addWidget(self.txt_diag)

        tip = QLabel(BAND10PRO_HINT + "  " +
                     "小米手环 8 代及以后走标准 0x180D 心率服务；多设备时每台独立重连，"
                     "同时连接数受电脑蓝牙适配器限制。数据仅供训练参考，非医疗设备。")
        tip.setWordWrap(True)
        tip.setToolTip(tip.text())
        root.addWidget(tip)

    def _diag(self, text: str) -> None:
        self.txt_diag.appendPlainText(
            f"[{datetime.now().strftime('%H:%M:%S')}] {text}")

    def _update_count(self) -> None:
        selected = sum(1 for address in self._details
                       if self._is_checked(address))
        self.lbl_count.setText(
            f"扫到 {len(self._devices)} 台 · 已选 {selected} 台 · "
            f"详情 {len(self._details)} 台")

    # ------------------------------------------------------------------ 配置

    def _load_cfg(self) -> dict:
        try:
            data = json.loads((self._data_dir / "settings.json").read_text("utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {"csv": False, "auto": False, "selected": []}

    def _save_cfg(self) -> None:
        try:
            (self._data_dir / "settings.json").write_text(
                json.dumps(self._cfg, ensure_ascii=False, indent=2), "utf-8")
        except OSError as exc:
            self._diag(f"设置保存失败：{exc}")

    def _apply_cfg(self) -> None:
        for box, key in ((self.chk_csv, "csv"), (self.chk_auto, "auto")):
            box.blockSignals(True)
            box.setChecked(bool(self._cfg.get(key)))
            box.blockSignals(False)
        self._update_count()

    def _on_cfg_changed(self) -> None:
        self._cfg["csv"] = self.chk_csv.isChecked()
        self._cfg["auto"] = self.chk_auto.isChecked()
        self._save_cfg()

    def _open_data_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._data_dir)))

    # ------------------------------------------------------------------ 扫描

    def _start_scan(self) -> None:
        if self._scanner is not None:
            self._diag("正在扫描，请稍候")
            return
        self._devices = []
        self.list_devices.blockSignals(True)
        self.list_devices.clear()
        self.list_devices.addItem("扫描中 …")
        self.list_devices.blockSignals(False)
        self.btn_scan.setEnabled(False)
        self._diag("开始扫描带 0x180D 的设备（含小米 10 Pro 名称兜底）")
        worker = HeartRateScanner(seconds=6.0)
        worker.devices.connect(self._on_devices)
        worker.failed.connect(lambda text: self._diag(text.splitlines()[0]))
        worker.finished.connect(self._on_scan_finished)
        self._scanner = worker
        worker.open()

    def _on_devices(self, rows) -> None:
        self._devices = list(rows or [])
        self._by_addr = {info["address"]: info for info in self._devices}
        restore = self.chk_auto.isChecked()
        remembered = set(str(x) for x in (self._cfg.get("selected") or []))
        self.list_devices.blockSignals(True)
        self.list_devices.clear()
        if not self._devices:
            self.list_devices.addItem("没扫到设备")
        for info in self._devices:
            item = QListWidgetItem(info.get("label") or device_label(info))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled
                          | Qt.ItemFlag.ItemIsSelectable
                          | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked
                               if restore and info["address"] in remembered
                               else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, info["address"])
            self.list_devices.addItem(item)
        self.list_devices.blockSignals(False)
        self._update_count()

    def _on_scan_finished(self) -> None:
        self.btn_scan.setEnabled(True)
        worker, self._scanner = self._scanner, None
        if worker is not None:
            self._retire(worker)
        if not self._devices:
            self._diag("没扫到设备：确认手环已开启心率广播、停留在广播页、手机已断开")
        else:
            self._diag(f"扫描结束，共 {len(self._devices)} 台心率设备")
        if self.chk_auto.isChecked() and self._devices:
            self._apply_selection()
        self._update_count()

    def _is_checked(self, address: str) -> bool:
        for index in range(self.list_devices.count()):
            item = self.list_devices.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == address:
                return item.checkState() == Qt.CheckState.Checked
        return False

    def _apply_selection(self) -> None:
        """把当前勾选的设备一次性同步给 Hub（同一批命令，不重建未变设备）。"""
        targets: dict[str, str] = {}
        for index in range(self.list_devices.count()):
            item = self.list_devices.item(index)
            address = item.data(Qt.ItemDataRole.UserRole)
            if not address or item.checkState() != Qt.CheckState.Checked:
                continue
            info = self._by_addr.get(address, {})
            targets[address] = info.get("name") or ""
            self._ensure_detail(info or {"address": address})
            self._ensure_card(info or {"address": address})
        self._cfg["selected"] = list(targets)
        self._save_cfg()
        if targets:
            self._ensure_hub().set_targets(targets)
            self._diag(f"已启动 {len(targets)} 台设备的独立接收")
        elif self._hub is not None:
            self._hub.clear()
        self._update_count()

    # ------------------------------------------------------------------ 视图

    def _ensure_detail(self, info: dict) -> DeviceDetail:
        address = info["address"]
        detail = self._details.get(address)
        if detail is None:
            detail = DeviceDetail(info)
            self._details[address] = detail
            self.tabs.addTab(detail, _tab_title(info))
        return detail

    def _ensure_card(self, info: dict) -> OverviewCard:
        address = info["address"]
        card = self._cards.get(address)
        if card is None:
            card = OverviewCard(info, lambda a=address: self._show_detail(a))
            self._cards[address] = card
            index = len(self._cards) - 1
            self._overview_grid.addWidget(
                card, index // OVERVIEW_COLUMNS, index % OVERVIEW_COLUMNS)
        return card

    def _show_detail(self, address: str) -> None:
        detail = self._details.get(address)
        if detail is not None:
            self.tabs.setCurrentWidget(detail)

    # ------------------------------------------------------------------ Hub

    def _ensure_hub(self) -> HeartRateHub:
        if self._hub is None:
            hub = HeartRateHub()
            hub.sample.connect(self._on_sample)
            hub.device_state.connect(self._on_device_state)
            hub.device_error.connect(self._on_device_error)
            hub.device_meta.connect(self._on_device_meta)
            hub.failed.connect(lambda text: self._diag(text.splitlines()[0]))
            self._hub = hub
            hub.open()
        return self._hub

    def _on_item_changed(self, item) -> None:
        address = item.data(Qt.ItemDataRole.UserRole)
        if not address:
            return
        info = self._by_addr.get(address, {"address": address})
        if item.checkState() == Qt.CheckState.Checked:
            self._ensure_detail(info)
            self._ensure_card(info)
            self._ensure_hub().add(address, info.get("name") or "")
            self._diag(f"开始接收：{_tab_title(info)}")
        else:
            if self._hub is not None:
                self._hub.remove(address)
            detail = self._details.get(address)
            if detail is not None:
                detail.set_stopped()
            card = self._cards.get(address)
            if card is not None:
                card.set_stopped()
            self._diag(f"停止接收：{_tab_title(info)}")
        remembered = [a for a in (self._cfg.get("selected") or []) if a != address]
        if item.checkState() == Qt.CheckState.Checked:
            remembered.append(address)
        self._cfg["selected"] = remembered
        self._save_cfg()
        self._update_count()

    def _stop_all(self) -> None:
        self.list_devices.blockSignals(True)
        for index in range(self.list_devices.count()):
            item = self.list_devices.item(index)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.list_devices.blockSignals(False)
        worker, self._hub = self._hub, None
        if worker is not None:
            self._retire(worker)
        for detail in self._details.values():
            detail.set_stopped()
        for card in self._cards.values():
            card.set_stopped()
        self._cfg["selected"] = []
        self._save_cfg()
        self._diag("已停止全部接收（各设备历史数据保留，可继续分别查看）")
        self._update_count()

    # ------------------------------------------------------------------ 数据

    def _on_sample(self, sample: dict) -> None:
        address = str(sample.get("address") or "")
        if not address:
            return
        info = self._by_addr.get(address, {
            "address": address, "name": sample.get("name") or ""})
        detail = self._ensure_detail(info)
        card = self._ensure_card(info)
        detail.on_sample(sample)
        card.on_sample(sample)
        if self.chk_csv.isChecked():
            self._write_csv(sample, detail)

    def _on_device_state(self, address: str, text: str) -> None:
        detail = self._details.get(address)
        if detail is not None:
            detail.on_state(text)
        card = self._cards.get(address)
        if card is not None:
            card.on_state(text)
        first = str(text).splitlines()[0] if text else ""
        self._diag(f"[{_tab_title(self._by_addr.get(address, {'address': address}))}] {first}")

    def _on_device_error(self, address: str, text: str) -> None:
        detail = self._details.get(address)
        if detail is not None:
            detail.on_state(text.splitlines()[0] if text else "连接出错")
        for line in str(text).splitlines():
            self._diag(f"[{_tab_title(self._by_addr.get(address, {'address': address}))}] {line}")

    def _on_device_meta(self, address: str, meta: dict) -> None:
        detail = self._details.get(address)
        if detail is not None:
            detail.on_meta(meta)
        card = self._cards.get(address)
        if card is not None:
            card.on_meta(meta)

    def _write_csv(self, sample: dict, detail: DeviceDetail) -> None:
        path = self._data_dir / "hr_log.csv"
        try:
            new = not path.exists()
            with path.open("a", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                if new:
                    writer.writerow(("timestamp", "address", "name", "bpm",
                                     "contact", "energy_kj", "flags",
                                     "battery", "location"))
                writer.writerow((
                    datetime.fromtimestamp(float(sample.get("ts", 0))).isoformat(
                        timespec="seconds"),
                    sample.get("address"), sample.get("name"),
                    sample.get("bpm"),
                    "" if sample.get("contact") is None
                    else int(bool(sample.get("contact"))),
                    sample.get("energy") if sample.get("energy") is not None else "",
                    sample.get("flags"),
                    detail.meta.get("battery", ""), detail.meta.get("location", "")))
        except OSError as exc:
            self._diag(f"CSV 写入失败：{exc}")

    # ------------------------------------------------------------------ 收尾

    def _retire(self, worker) -> None:
        """⛔ QThread 还活着时不能让 Python 回收包装对象（会 qFatal）。"""
        for name in ("devices", "failed", "sample", "device_state",
                     "device_error", "device_meta"):
            signal = getattr(worker, name, None)
            if signal is not None:
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
        worker.stop()
        if worker.isRunning():
            self._retiring.append(worker)
            worker.finished.connect(lambda w=worker: self._reap(w))
        else:
            worker.deleteLater()

    def _reap(self, worker) -> None:
        if worker.isRunning():
            return
        if worker in self._retiring:
            self._retiring.remove(worker)
        worker.deleteLater()

    def shutdown(self) -> None:
        """宿主退出时调用（幂等）。"""
        if self._scanner is not None:
            worker, self._scanner = self._scanner, None
            self._retire(worker)
        if self._hub is not None:
            worker, self._hub = self._hub, None
            self._retire(worker)
        self._save_cfg()

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
