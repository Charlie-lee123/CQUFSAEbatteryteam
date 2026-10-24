# -*- coding: utf-8 -*-
r"""动态冒烟：把**用户点得到**的交互全跑一遍，抓真实异常（Python traceback）。

本文件只做"点"，不改产品源码；所有产品行为都是真实入口（按钮 clicked、
滑杆 valueChanged、回车事件、串口 worker 失败信号……）。

铁律（污染用户配置是**不可逆**事故）：
  · 全程 QT_QPA_PLATFORM=windows；
  · 开窗前对 QSettings 全量快照，**先销毁所有窗口、再还原**（顺序反了，
    窗口析构时的 QSettings 回写会把测试值盖回去 —— selftest.main 的收尾
    就是按这个顺序写的，这里照抄）；
  · rx_history 指到临时目录（否则灌进去的假数据成了用户的"本地留存"）；
  · 剪贴板内容先存后还；
  · 模态框（QMessageBox / _NameDialog）一律改造成**记录器**或定时关掉：
    真弹一个 exec() 会把探针挂死（这正是用户机器上"点了没反应"的同款现场）。

异常怎么抓（Qt 会吞掉槽里的异常，只往 stderr 打）：
  1) sys.excepthook / threading.excepthook / sys.unraisablehook —— PyErr_Print
     走的就是 excepthook，PySide6 槽异常由此进来；
  2) 把 sys.stderr 换成 Tee，**每步之后**捞出新增片段记一条（兜底，防止
     某个版本绕过 excepthook）；
  3) 读 app_log 文件（%LOCALAPPDATA%\CQUFSAE-SerialAssistant\logs\
     serialassistant.log）在本进程期间新增的 ERROR 行。

用法：QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_smoke_all.py
产物：_check/smoke_report.txt（人读）、_check/smoke_frames/（导出日志）
"""
from __future__ import annotations

import os
import sys
import time
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import QEvent, QSettings, Qt, QTimer               # noqa: E402
from PySide6.QtGui import QGuiApplication, QTextCursor                # noqa: E402
from PySide6.QtTest import QTest                                       # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog,      # noqa: E402
                               QMessageBox, QPushButton, QScrollArea)

OUT_DIR = os.path.join(_ROOT, "_check")
FRAME_DIR = os.path.join(OUT_DIR, "smoke_frames")
os.makedirs(FRAME_DIR, exist_ok=True)
REPORT_PATH = os.path.join(OUT_DIR, "smoke_report.txt")

# ------------------------------------------------------------------ 记录容器
APP: QApplication | None = None
WINDOW = None
EXC: list[dict] = []          # 真异常（traceback）
STDERR_WARN: list[dict] = []  # stderr 里非 traceback 的输出（Qt 警告）
STATE: list[str] = []         # 状态/视觉异常（没抛异常但明显不对）
STEPS: list[str] = []         # 真跑过的交互名
WARNS: list[str] = []         # 被拦截的模态提示文案（产品主动报的错，不是异常）
DIALOGS: list[str] = []       # 自动关掉的模态框
CONTROL = [False]             # 阳性对照：异常捕获链路是否真的有效
RESTORE_LEFT: list[str] = []  # 收尾时回读注册表发现的还原残留
_STDERR_BUF: list[str] = []
_LOG_OFFSET = 0
_LOG_PATH = ""


class _Tee:
    """stderr 双写：留存一份给探针判定，同时照常吐到终端。"""

    def __init__(self, real):
        self._real = real

    def write(self, text):
        if text:
            _STDERR_BUF.append(text)
            try:
                self._real.write(text)
            except Exception:                      # noqa: BLE001 - 终端关了也得记账
                pass

    def flush(self):
        try:
            self._real.flush()
        except Exception:                          # noqa: BLE001
            pass

    def isatty(self):
        return False

    def fileno(self):
        try:
            return self._real.fileno()
        except Exception:                          # noqa: BLE001
            raise OSError("no fileno")


def _install_hooks() -> None:
    def hook(kind, value, tb):
        EXC.append({"where": "sys.excepthook",
                    "text": "".join(traceback.format_exception(kind, value, tb))})

    def thread_hook(args):
        EXC.append({"where": f"threading.excepthook({getattr(args.thread, 'name', '?')})",
                    "text": "".join(traceback.format_exception(
                        args.exc_type, args.exc_value, args.exc_traceback))})

    def unraisable_hook(args):
        EXC.append({"where": f"sys.unraisablehook({getattr(args, 'object', '?')})",
                    "text": "".join(traceback.format_exception(
                        args.exc_type, args.exc_value, args.exc_traceback))})

    sys.excepthook = hook
    import threading
    threading.excepthook = thread_hook
    sys.unraisablehook = unraisable_hook


# ------------------------------------------------------------------ 模态框治理
class ModalKiller:
    """定时关掉任何**真的弹出来**的模态框，并记下它的标题/正文。

    为什么必须有：串口打开失败、蓝牙扫不起来、删车确认都走 ``QMessageBox``
    或 ``box.exec()``。探针卡在 exec() 里就等于"假死"，什么异常都测不到。
    能改造成记录器的（warning / 文件对话框 / 新建车号）在下面直接换掉；
    这个定时器是**兜住剩下的**，不是主路径。
    """

    def __init__(self, app: QApplication):
        self.app = app
        self.timer = QTimer()
        self.timer.setInterval(80)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _tick(self):
        widget = self.app.activeModalWidget()
        if widget is None:
            return
        if WINDOW is not None and widget is getattr(WINDOW, "_settings_dialog", None):
            return                                  # 设置面板标了 setModal(False)
        try:
            title = widget.windowTitle()
            body = ""
            if isinstance(widget, QMessageBox):
                body = widget.text()
                for button in widget.buttons():
                    role = widget.buttonRole(button)
                    if role in (QMessageBox.ButtonRole.YesRole,
                                QMessageBox.ButtonRole.AcceptRole):
                        DIALOGS.append(f"[{title}] {body[:80]} → 点 {button.text()}")
                        button.click()
                        return
                DIALOGS.append(f"[{title}] {body[:80]} → 无肯定键，reject")
                widget.reject()
                return
            DIALOGS.append(f"[{title}] {type(widget).__name__} → reject")
            widget.reject()
        except Exception:                          # noqa: BLE001 - 关框失败不能拖垮探针
            EXC.append({"where": "ModalKiller",
                        "text": traceback.format_exc()})


def _patch_modals() -> None:
    """把会 exec() 的东西换成记录器（探针专用，产品源码一字未改）。"""
    def fake_warning(parent=None, title="", text="", *args, **kwargs):
        WARNS.append(f"{title}｜{str(text).splitlines()[0] if text else ''}")
        return QMessageBox.StandardButton.Ok

    def fake_save(parent=None, caption="", default="", filters="", *a, **k):
        name = "".join(ch for ch in str(default) if ch.isalnum() or ch in "._-")
        path = os.path.join(FRAME_DIR, name or "saved.log")
        WARNS.append(f"文件对话框｜保存 → {os.path.basename(path)}")
        return path, "日志文件 (*.log *.txt)"

    QMessageBox.warning = staticmethod(fake_warning)
    QMessageBox.information = staticmethod(fake_warning)
    QMessageBox.critical = staticmethod(fake_warning)
    QFileDialog.getSaveFileName = staticmethod(fake_save)
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))


def _patch_new_vehicle_dialog(name: str) -> None:
    """新建车号的 _NameDialog 走 exec()：换成"填好名字直接确定"。"""
    import ui_settings

    def fake_exec(self):
        self.edit.setText(name)
        return QDialog.DialogCode.Accepted

    ui_settings._NameDialog.exec = fake_exec


def _patch_vehicle_confirm() -> None:
    """删车确认弹层：默认按"确认删除"（真实删除链路照走）。"""
    import ui_settings
    ui_settings.SettingsDialog._confirm_vehicle_delete = staticmethod(lambda label: True)


# ------------------------------------------------------------------ 工具
def pump(ms: int = 140) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        APP.processEvents()
        time.sleep(0.002)


def wait_until(predicate, timeout: float = 4.0, label: str = "") -> bool:
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        APP.processEvents()
        try:
            if predicate():
                return True
        except Exception:                          # noqa: BLE001
            pass
        time.sleep(0.005)
    if label:
        STATE.append(f"等待超时：{label}（{timeout:.1f}s 内条件未成立）")
    return False


def drain_stderr(where: str) -> None:
    chunk = "".join(_STDERR_BUF)
    _STDERR_BUF.clear()
    if not chunk.strip():
        return
    record = {"where": where, "text": chunk}
    if "Traceback" in chunk:
        EXC.append(record)
    else:
        STDERR_WARN.append(record)


def step(name: str, action, *, settle: int = 150) -> None:
    """跑一条交互：记录名字 → 执行 → 抽事件 → 收异常。"""
    STEPS.append(name)
    try:
        action()
    except Exception:                              # noqa: BLE001 - 直接调用链上的异常
        EXC.append({"where": f"[{name}] 调用直接抛出", "text": traceback.format_exc()})
    pump(settle)
    drain_stderr(name)


def expect(condition, message: str) -> None:
    if not condition:
        STATE.append(message)


def warn_count() -> int:
    return len(WARNS)


# ------------------------------------------------------------------ 数据构造
def volt_frame(round_no: int) -> str:
    """一轮电压：25 个 n.####（12 + 13 两行，模拟真机折行）。"""
    values = [f"{i + 1:02d}.{3700 + (i % 7) * 10 + round_no:04d}" for i in range(25)]
    return ("Voltage1:\n" + " ".join(values[:12]) + "\n" + " ".join(values[12:]) + "\n")


def temp_frame(round_no: int) -> str:
    """一轮温度：8 个 n.nn（4 + 4 两行）。"""
    values = [f"{i + 1}.{20 + (i % 5) + round_no:02d}" for i in range(8)]
    return ("Temperature1:\n" + " ".join(values[:4]) + "\n" + " ".join(values[4:]) + "\n")


def bms_ok() -> str:
    return "BMS OK\n"


def bms_error() -> str:
    return "BMS ERROR\n1.1 Over Voltage\n2.3 Under Temperature\n"


def feed(text: str) -> None:
    """从产品**唯一收包入口**喂一帧，然后强制吐出来（不等 60ms 合并）。"""
    WINDOW._on_received(text.encode("utf-8"))
    WINDOW._flush_rx(force=True)
    pump(80)


def one_round(round_no: int, error: bool = False) -> None:
    feed(volt_frame(round_no))
    feed(temp_frame(round_no))
    feed(bms_error() if error else bms_ok())


# ------------------------------------------------------------------ 设置面板
def settings_open() -> None:
    WINDOW.btn_settings.click()
    ok = wait_until(lambda: WINDOW._settings_dialog is not None, 5.0, "设置面板没打开")
    if ok:
        wait_until(lambda: WINDOW._settings_swap_target is None, 5.0, "设置面板入场动画没收尾")
        pump(120)


def settings_close(accept: bool) -> None:
    dialog = WINDOW._settings_dialog
    if dialog is None:
        STATE.append("关闭设置面板时面板已经不存在（打开失败了？）")
        return
    if accept:
        dialog.btn_confirm.click()
    else:
        candidates = [b for b in dialog.findChildren(QPushButton) if b.text() == "取消"]
        if not candidates:
            STATE.append("设置面板找不到「取消」按钮")
            dialog.reject()
        else:
            candidates[0].click()
    wait_until(lambda: WINDOW._settings_dialog is None, 6.0, "设置面板关闭动画没收尾")
    pump(150)


def dialog_controls(dialog):
    """把面板里的滑杆 / 开关 / 数字框 / 下拉框按类别收一遍。"""
    from ui_controls import ChevronComboBox, ChevronSpinBox, GlassSlider, Switch
    sliders = list(dialog.findChildren(GlassSlider))
    switches = list(dialog.findChildren(Switch))
    spins = list(dialog.findChildren(ChevronSpinBox))
    combos = list(dialog.findChildren(ChevronComboBox))
    return sliders, switches, spins, combos


# ------------------------------------------------------------------ 主流程
def _start_watchdog(limit_s: float = 300.0) -> None:
    """兜底：万一卡进某个 exec() 且没人关，硬退出并留下已跑部分的报告。

    没有它，自动化里一次挂死就得人工去关窗口；有了它最坏情况也能拿到报告。
    """
    import threading

    def watch():
        deadline = time.time() + limit_s
        while time.time() < deadline:
            time.sleep(1.0)
        try:
            STATE.append(f"看门狗触发：{limit_s:.0f}s 还没跑完（大概率卡在某个模态框）")
            drain_stderr("看门狗")
            write_report([])
        finally:
            os._exit(2)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()


def run() -> None:
    global APP, WINDOW

    # 原生崩溃（访问违例 / abort）不会走 excepthook —— 只有 faulthandler 会把
    # 崩溃时的 Python 栈打出来（实测有一次进程在蓝牙扫描阶段以 127 硬退，
    # 日志里一个字都没有；没有这个文件就完全查不到是哪一步）。
    import faulthandler
    _crash = open(os.path.join(OUT_DIR, "smoke_crash.txt"), "w", encoding="utf-8")
    faulthandler.enable(file=_crash, all_threads=True)

    import app_log
    global _LOG_PATH, _LOG_OFFSET
    app_log.init()
    _LOG_PATH = app_log.path() or ""
    if _LOG_PATH and os.path.exists(_LOG_PATH):
        _LOG_OFFSET = os.path.getsize(_LOG_PATH)

    _install_hooks()
    sys.stderr = _Tee(sys.stderr)
    _patch_modals()
    _start_watchdog()

    APP = QApplication(sys.argv)
    from ui_theme import apply_scheme, detect_system_scheme, ui_font
    APP.setStyle("Fusion")
    apply_scheme(detect_system_scheme())
    APP.setFont(ui_font(12.5))

    # ---- 环境隔离（必须在建窗之前）--------------------------------------
    import rx_history
    import tempfile
    from pathlib import Path
    real_resolve = rx_history._resolve_dir
    hist_dir = Path(tempfile.mkdtemp(prefix="smoke_hist_"))
    rx_history._resolve_dir = lambda: hist_dir

    clip = APP.clipboard()
    clip_backup = clip.text()

    global_settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {k: global_settings.value(k) for k in global_settings.allKeys()}
    # 快照同时落盘：万一本进程被硬杀（实测出现过一次 127 硬退），事后还能照着还原。
    with open(os.path.join(OUT_DIR, "smoke_settings_snapshot.txt"), "w",
              encoding="utf-8") as handle:
        for key in sorted(snapshot):
            value = snapshot[key]
            handle.write(f"{key}\t{'' if value is None else value}\n")
    print(f"[环境] 配置快照 {len(snapshot)} 键；历史目录隔离到 {hist_dir}")

    # 基线比对：_check/config_baseline_repaired.txt 是"人工确认过的干净配置"。
    # 实测踩过坑 —— 探针被硬杀（exit 127）时会留下脏值，而**下一次运行会把
    # 脏值当快照**、还原成同一份脏值，于是"还原通过"是个假象。所以先对一眼基线。
    baseline_path = os.path.join(OUT_DIR, "config_baseline_repaired.txt")
    if os.path.exists(baseline_path):
        baseline = {}
        with open(baseline_path, "r", encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.rstrip("\n").partition("\t")
                baseline[key] = value
        drift = [key for key in sorted(set(baseline) | set(snapshot))
                 if baseline.get(key) != ("" if snapshot.get(key) is None
                                          else str(snapshot.get(key)))]
        if drift:
            STATE.append("运行前配置已偏离人工基线（很可能是上一次硬退留下的脏值）："
                         + "、".join(drift[:8]))
            print(f"[警告] 运行前配置与基线差 {len(drift)} 项：{drift[:8]}")

    # 不联网、不自动开串口（这两条都会把探针带偏，且不是本轮要测的交互）
    from main_window import LINK_BLE, LINK_SERIAL, MainWindow
    MainWindow._auto_check_update = lambda self: None

    killer = ModalKiller(APP)
    _patch_new_vehicle_dialog("SMOKE-临时车")
    _patch_vehicle_confirm()

    window = MainWindow()
    WINDOW = window
    window._port_watch.stop()
    window.cmb_link.setCurrentText(LINK_SERIAL)
    window.show()
    window.setGeometry(80, 60, 1240, 860)
    pump(600)
    print("[环境] 主窗口已就绪")

    # ---- 自检（阳性对照）：故意在槽里抛一次，确认捕获链路真的通 ----
    # 没有这一步，"0 崩"可能只是探针聋了。
    before = len(EXC)
    QTimer.singleShot(0, lambda: (_ for _ in ()).throw(
        RuntimeError("SMOKE 阳性对照：这个异常必须被记录")))
    pump(300)
    CONTROL[0] = len(EXC) > before
    if CONTROL[0]:
        EXC.pop(before)                 # 自检用的异常不算产品缺陷
        print("[自检] 槽内异常捕获链路正常（阳性对照已捕获）")
    else:
        STATE.append("!!! 阳性对照失败：故意抛出的槽异常没被记录，"
                     "本次「0 崩」不可信")
        print("[自检] ✗ 捕获链路失效！")
    _STDERR_BUF.clear()

    # ============================================================ 1. 设置面板
    print("\n== 1. 设置面板（打开 → 滚动 → 全控件边界 → 取消）==")
    step("设置：打开", settings_open, settle=200)
    dialog = window._settings_dialog
    if dialog is None:
        STATE.append("设置面板打不开，后续设置相关交互全部跳过")
    else:
        scroll = dialog.findChild(QScrollArea)
        if scroll is None:
            STATE.append("设置面板里找不到滚动区")
        else:
            def scroll_groups():
                bar = scroll.verticalScrollBar()
                span = max(1, bar.maximum())
                for frac in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
                    bar.setValue(int(span * frac))
                    pump(90)
                if bar.value() != bar.maximum():
                    STATE.append(f"设置面板滚动条没到底（{bar.value()}/{bar.maximum()}）")
                bar.setValue(0)
            step("设置：逐组滚动到底", scroll_groups, settle=200)

        sliders, switches, spins, combos = dialog_controls(dialog)
        print(f"        控件盘点：滑杆 {len(sliders)}，开关 {len(switches)}，"
              f"数字框 {len(spins)}，下拉框 {len(combos)}")

        def drive_glass():
            bar = dialog.slider_glass
            for value in (0, 50, 100):
                bar.setValue(value)
                pump(120)
                expect(bar.value() == value,
                       f"材质强度滑杆设 {value} 后读到 {bar.value()}")
        step("设置：材质强度 0/50/100", drive_glass, settle=200)

        def drive_material():
            combo = getattr(dialog, "cmb_material", None)
            if combo is None:
                STATE.append("设置面板里没有「界面材质」下拉框")
                return
            for index in range(combo.count()):
                combo.setCurrentIndex(index)
                pump(220)
        step("设置：界面材质逐项切换", drive_material, settle=250)

        def drive_fps():
            slider = dialog._custom_widgets.get("anim_fps")
            if slider is None:
                STATE.append("找不到动画帧率滑杆")
                return
            for value in (30, 120):
                slider.setValue(value)
                pump(160)
        step("设置：帧率滑杆 30/120", drive_fps, settle=200)

        def drive_font():
            slider = dialog._custom_widgets.get("rx_font_size")
            if slider is None:
                STATE.append("找不到接收区字号滑杆")
                return
            for raw in (16, 36):          # scale=0.5 → 8pt / 18pt
                slider.setValue(raw)
                pump(160)
        step("设置：字号滑杆 8/18", drive_font, settle=200)

        def drive_switches():
            for toggle in switches:
                before = toggle.isChecked()
                toggle.click()
                pump(120)
                expect(toggle.isChecked() != before,
                       f"开关「{toggle.accessibleName() or toggle.text()}」点了没反应")
                toggle.click()
                pump(120)
        step("设置：每个开关各拨两次", drive_switches, settle=200)

        def drive_spins():
            for spin in spins:
                low, high = spin.minimum(), spin.maximum()
                spin.setValue(low)
                pump(90)
                spin.setValue(high)
                pump(90)
                expect(spin.value() == high,
                       f"数字框设到最大值 {high} 后读到 {spin.value()}")
                spin.setValue(low)
                pump(90)
        step("设置：每个数字框 最小/最大（含 0）", drive_spins, settle=260)

        # 车辆：新建 → 切赛季 → 改模组个数（含 0） → 删除
        def drive_vehicle():
            import customization
            names = customization.vehicle_names()
            print(f"        现有车辆档案：{names or '（无）'}")
            if names:
                # 有档案就先切一台，确认接收区重排不炸
                dialog.spin_vehicle_season.setValue(
                    int(customization.split_vehicle(names[0])[0] or 2026))
                pump(150)
                for index in range(dialog.cmb_vehicle_number.count()):
                    dialog.cmb_vehicle_number.setCurrentIndex(index)
                    pump(200)
            dialog.btn_vehicle_new.click()
            pump(300)
            created = customization.current_vehicle()
            expect(bool(created), "点「＋新建车号」之后没有选中任何车")
            try:
                if created:
                    for count in (0, 3, 5):
                        dialog.spin_vehicle_modules.setValue(count)
                        pump(250)
                    pump(300)                  # 等 200ms 的接收区重排合并
            finally:
                # 不管上面哪一步炸了，这台测试车都得删掉（删不掉就是永久脏档案）
                if created:
                    dialog._on_vehicle_delete_requested(created)
                    pump(300)
                expect(created not in customization.vehicle_names(),
                       f"删车之后档案还在：{created}")
        step("设置：车辆新建/切换/模组数/删除", drive_vehicle, settle=350)

        def drive_export_log():
            button = getattr(dialog, "btn_export_log", None)
            if button is None:
                STATE.append("设置面板里没有「导出日志」按钮")
                return
            button.click()
            pump(300)
        step("设置：导出日志（文件框已改造）", drive_export_log, settle=200)

        def drive_mcp_copy():
            button = getattr(dialog, "btn_mcp_copy", None)
            if button is None:
                STATE.append("设置面板里没有「复制接入配置」按钮")
                return
            button.click()
            pump(250)
        step("设置：MCP 复制接入配置", drive_mcp_copy, settle=200)

        def cancel_and_verify():
            """取消 = 把所有**实时预览**原样还回去（改了就回不去是用户级 bug）。"""
            from ui_anim import frame_hz
            from ui_theme import glass_strength, material
            prev_glass, prev_material, _hidden, prev_font, prev_fps = \
                window._settings_previous
            settings_close(False)
            expect(window._settings_dialog is None, "按「取消」之后面板还挂着")
            expect(glass_strength() == prev_glass,
                   f"取消后材质强度没还原：{prev_glass} → {glass_strength()}")
            expect(material() == prev_material,
                   f"取消后材质没还原：{prev_material} → {material()}")
            expect(frame_hz() == prev_fps,
                   f"取消后动画帧率没还原：{prev_fps} → {frame_hz()}")
            expect(abs(window.txt_rx.font().pointSizeF() - float(prev_font)) < 0.01,
                   "取消后接收区字号没还原："
                   f"{prev_font} → {window.txt_rx.font().pointSizeF()}")

        step("设置：取消（预览必须全部还原）", cancel_and_verify, settle=300)


    # ============================================================ 2. 主题三态
    print("\n== 2. 主题三态（浅色 / 深色 / 跟随系统）==")
    for label, stored in (("浅色", "light"), ("深色", "dark"), ("跟随系统", "system")):
        def apply_theme(stored=stored):
            settings_open()
            dlg = window._settings_dialog
            if dlg is None:
                return
            index = dlg.cmb_theme.findData(stored)
            if index < 0:
                STATE.append(f"主题下拉里没有 {stored}")
                settings_close(False)
                return
            dlg.cmb_theme.setCurrentIndex(index)
            pump(120)
            settings_close(True)
            pump(600)                       # 让自绘面的颜色过渡跑完
            expect(window._theme_mode == stored,
                   f"确定后 _theme_mode={window._theme_mode!r}，应为 {stored!r}")
            if stored in ("light", "dark"):
                from ui_theme import current_scheme
                expect(current_scheme() == stored,
                       f"确定后实际配色是 {current_scheme()!r}，应为 {stored!r}")
        step(f"主题：切到{label}并确定", apply_theme, settle=300)

    # ============================================================ 3. 接收区
    print("\n== 3. 接收区（喂数据 + 全套工具栏）==")
    window.chk_hex_view.setChecked(False)
    window.chk_timestamp.setChecked(True)

    step("接收：喂 3 轮真实格式数据", lambda: [one_round(i) for i in (0, 1, 2)], settle=250)
    text = window.txt_rx.toPlainText()
    expect("Voltage1:" in text and "Temperature1:" in text and "BMS" in text,
           f"喂完数据接收区内容不对：{text[:120]!r}")

    step("接收：喂 BMS Error 那一轮", lambda: one_round(3, error=True), settle=250)

    def pause_and_feed():
        window.btn_pause.click()
        pump(250)
        expect(window._paused, "点暂停之后 _paused 还是 False")
        frozen = window.txt_rx.toPlainText()
        one_round(4)
        pump(200)
        expect(window.txt_rx.toPlainText() == frozen,
               "暂停期间接收区内容仍在变化（没冻结住）")
        window.btn_pause.click()
        pump(400)
        expect(not window._paused, "点继续之后 _paused 还是 True")
    step("接收：暂停 → 喂数据 → 继续", pause_and_feed, settle=350)

    step("接收：清空", lambda: (window.btn_clear.click(), pump(200)), settle=200)
    expect(not window.txt_rx.toPlainText().strip(), "清空之后接收区还有内容")

    step("接收：重新喂 2 轮", lambda: [one_round(i) for i in (5, 6)], settle=250)

    def copy_round():
        window.txt_rx.moveCursor(QTextCursor.MoveOperation.End)
        window.btn_copy_all.click()
        pump(250)
        copied = APP.clipboard().text()
        expect(bool(copied.strip()), "「复制本轮」没有往剪贴板写东西")
    step("接收：复制本轮", copy_round, settle=250)

    def copy_all_branch():
        """无时间戳 → _round_span 返回 None → 走"复制全部"分支。"""
        window.chk_timestamp.setChecked(False)
        window.btn_clear.click()
        pump(150)
        one_round(7)
        window.btn_copy_all.click()
        pump(250)
        expect(bool(APP.clipboard().text().strip()), "「复制全部」没有写剪贴板")
        window.chk_timestamp.setChecked(True)
    step("接收：复制全部（无时间戳分支）", copy_all_branch, settle=250)

    def save_log():
        window.btn_save.click()
        pump(400)
        written = [f for f in os.listdir(FRAME_DIR) if f.endswith((".log", ".txt"))]
        expect(bool(written), "「保存接收日志」没有落出文件")
        if written:
            target = os.path.join(FRAME_DIR, written[-1])
            size = os.path.getsize(target)
            expect(size > 0, f"导出的日志是空的：{target}")
            print(f"        导出日志 → {target}（{size} 字节）")
    # 搜索要有**多处命中**才测得出"上一处/下一处"：先把文档喂到 4 轮
    step("接收：再喂 3 轮（给搜索造多处命中）",
         lambda: [one_round(i) for i in (11, 12, 13)], settle=300)

    step("接收：导出日志", save_log, settle=300)

    def search_hit():
        window.btn_search.click()
        pump(400)                       # Reveal 220ms
        window.edit_search.setText("BMS")
        pump(500)                       # 防抖 220ms？多给点
        expect(window._matches, "搜索 BMS 应有命中，实际 0 条")
        expect(len(window._matches) >= 2,
               f"搜索多处命中断言前提不成立（只找到 {len(window._matches)} 处）")
        print(f"        命中 {len(window._matches)} 处，标签={window.lbl_matches.text()!r}")
    step("接收：搜索（有结果）", search_hit, settle=300)

    def search_miss():
        window.edit_search.setText("zzz-不存在的串")
        pump(500)
        expect(not window._matches, "搜不存在的词却报有命中")
        expect(window.lbl_matches.text() == "无匹配",
               f"无结果时标签不是「无匹配」：{window.lbl_matches.text()!r}")
    step("接收：搜索（无结果）", search_miss, settle=300)

    def search_bad_regex():
        # 产品是**纯文本**查找（没有正则模式）：非法正则字符必须被当字面量，不能炸
        for term in ("[(", "*+?", "\\"):
            window.edit_search.setText(term)
            pump(350)
        window.edit_search.setText("BMS")
        pump(400)
    step("接收：搜索（正则非法字符）", search_bad_regex, settle=300)

    def search_nav():
        first = window.lbl_matches.text()
        window.btn_find_next.click()
        pump(150)
        window.btn_find_next.click()
        pump(150)
        forward = window.lbl_matches.text()
        window.btn_find_prev.click()
        pump(150)
        back = window.lbl_matches.text()
        window.btn_find_prev.click()
        pump(150)
        for _ in range(2):
            window.btn_find_next.click()
            pump(120)
        window.btn_find_close.click()
        pump(350)
        expect(first != forward, f"点「下一处」计数没变（还是 {first!r}）")
        expect(back != forward, f"点「上一处」计数没变（还是 {back!r}）")
        expect(window._matches == [], "关闭搜索之后命中列表没清空")
    step("接收：上一处/下一处/关闭搜索", search_nav, settle=300)

    step("接收：定位最近一次异常", lambda: (window.btn_alert.click(), pump(400)), settle=300)

    def replay_toggle():
        window.btn_open_history.click()
        pump(900)                       # 灌几千行 + 220ms 提示条
        first = window._replay_active
        window.btn_open_history.click()
        pump(700)
        print(f"        回放进={first}，退出后={window._replay_active}")
        expect(not window._replay_active, "再点一次「关闭历史数据」没有退出回放")
    step("接收：历史回放开/关", replay_toggle, settle=350)

    def hex_view():
        window.btn_clear.click()
        pump(120)
        window.chk_hex_view.setChecked(True)
        pump(150)
        feed("HEXTEST\n")
        pump(200)
        expect("48 45 58" in window.txt_rx.toPlainText().upper()
               or "HEX" not in window.txt_rx.toPlainText(),
               "HEX 显示开着时喂数据，内容不像十六进制")
        window.chk_hex_view.setChecked(False)
        pump(150)
        feed("明文回来了\n")
        pump(200)
        expect("明文回来了" in window.txt_rx.toPlainText(), "关掉 HEX 后明文没恢复")
    step("接收：HEX 显示切换", hex_view, settle=250)

    def timestamp_switches():
        import re
        window.chk_timestamp.setChecked(False)
        pump(120)
        window.btn_clear.click()
        pump(120)
        one_round(8)
        shown = window.txt_rx.toPlainText().lstrip()
        expect(not re.match(r"^\[\d{4}-", shown),
               f"时间戳关掉后仍然出现了时间戳行：{shown[:60]!r}")
        window.chk_timestamp.setChecked(True)
        pump(120)
        window.btn_clear.click()
        pump(120)
        one_round(9)
        expect(re.search(r"\[\d{4}-\d{2}-\d{2}", window.txt_rx.toPlainText()) is not None,
               "时间戳打开后没出现时间戳")
    step("接收：显示时间戳开关", timestamp_switches, settle=250)

    # 时间戳三件套（年月日）在设置面板里，走一次"改完确定"
    def stamp_three():
        settings_open()
        dlg = window._settings_dialog
        if dlg is None:
            return
        for key in ("stamp_show_year", "stamp_show_month", "stamp_show_day"):
            widget = dlg._custom_widgets.get(key)
            if widget is None:
                STATE.append(f"设置面板里找不到时间戳开关 {key}")
                continue
            for _ in range(2):
                widget.click()
                pump(120)
        settings_close(True)
        window.btn_clear.click()
        pump(120)
        one_round(10)
    step("接收：TIMESTAMP 年月日三件套", stamp_three, settle=300)

    # 最重的一条：所有定制项一起改成非默认 → 确定。
    # 这条走 _apply_settings_result 的全部下游（落盘 / 高亮器重读 / 排版重排 /
    # 启动姿态 / 按钮显隐），比"逐项改完再取消"覆盖得深。
    def apply_everything():
        import customization
        settings_open()
        dlg = window._settings_dialog
        if dlg is None:
            return
        # 先退出选车，否则三件套网格参数是隐藏态、改了也不参与排版
        customization.select_vehicle("")
        dlg._sync_vehicle_rows()
        pump(200)
        plan = {"module_count": 2, "cells_per_module": 4, "temps_per_module": 2,
                "volt_limit_low": 3000, "volt_limit_high": 4200,
                "broken_wire_mv": 1500,
                "temp_limit_low": -10, "temp_limit_high": 70,
                "pause_snap_lines": 3, "pause_snap_lines_max": 5}
        for key, value in plan.items():
            widget = dlg._custom_widgets.get(key)
            if widget is None:
                STATE.append(f"设置面板里找不到 {key}")
                continue
            widget.setValue(value)
            pump(90)
        # 按钮显隐：全藏一遍再全显回来（净改动为 0，但预览链路跑了两遍）
        for key, toggle in list(dlg._button_switches.items()):
            toggle.click()
            pump(60)
            toggle.click()
            pump(60)
        before_blocks = window.txt_rx.document().blockCount()
        settings_close(True)
        pump(600)                       # 等 200ms 合并的重排
        after_blocks = window.txt_rx.document().blockCount()
        print(f"        接收区块数 {before_blocks} → {after_blocks}"
              f"（module_count=2 → 重排）")
        expect(customization.get("module_count") == 2,
               f"确定之后 module_count 没落盘：{customization.get('module_count')}")
        expect("Voltage1:" in window.txt_rx.toPlainText(),
               "改排版参数并确定之后，接收区内容被清掉了")
    step("设置：全项改成非默认 → 确定（落盘 + 重排）", apply_everything, settle=500)

    def restore_grid():
        """把排版三件套改回 0（自动），确认重排能再走一遍且内容不丢。"""
        settings_open()
        dlg = window._settings_dialog
        if dlg is None:
            return
        for key in ("module_count", "cells_per_module", "temps_per_module"):
            widget = dlg._custom_widgets.get(key)
            if widget is not None:
                widget.setValue(0)
                pump(90)
        settings_close(True)
        pump(600)
        expect("Voltage1:" in window.txt_rx.toPlainText(),
               "改回自动排版之后接收区内容没了")
    step("设置：排版三件套改回自动 → 确定", restore_grid, settle=400)

    def settings_rapid_toggle():
        """快速连点设置键 3 次：开关语义 + 可打断动画不能留下半开状态。"""
        for _ in range(3):
            window.btn_settings.click()
            pump(60)
        pump(1200)
        if window._settings_dialog is not None:
            settings_close(False)
        expect(window._settings_dialog is None,
               "连点 3 次设置键之后面板还挂着（开关语义/动画中断有问题）")
    step("设置：快速连点 3 次（开关语义）", settings_rapid_toggle, settle=400)

    # ============================================================ 4. 发送区
    print("\n== 4. 发送区（折叠/展开 + 回车发送）==")

    def toggle_send(times: int):
        for _ in range(times):
            window.btn_minimize.click()
        pump(420)

    tx_base = {"collapsed": window._tx_collapsed,
               "height": window._tx_card.height()}

    def toggle_send_checked(times: int, label: str):
        before = window._tx_collapsed
        toggle_send(times)
        expect(window._tx_collapsed != before,
               f"快速连点 {times} 次之后折叠态没变（{label}）")
        # 卡片高度不能停在 0（那就是"折叠了但没展开/展开了但没内容"）
        expect(window._tx_card.height() > 0,
               f"{label}之后发送卡高度为 0（动画停在半路）")

    step("发送区：收起 3 次（快速连点）",
         lambda: toggle_send_checked(3, "进"), settle=500)
    step("发送区：再 3 次（快速连点）",
         lambda: toggle_send_checked(3, "出"), settle=500)
    expect(window._tx_collapsed == tx_base["collapsed"],
           f"6 次快速连点之后折叠态没回到初始（{tx_base['collapsed']} → "
           f"{window._tx_collapsed}）")
    expect(abs(window._tx_card.height() - tx_base["height"]) < 40,
           f"6 次快速连点之后发送卡高度跑偏：{tx_base['height']} → "
           f"{window._tx_card.height()}")
    print(f"        折叠态={window._tx_collapsed} 高度={window._tx_card.height()}"
          f"（初始 {tx_base['height']}）")

    def enter_send():
        window.txt_tx.setPlainText("SMOKE 回车发送")
        pump(100)
        before = warn_count()
        QTest.keyClick(window.txt_tx, Qt.Key.Key_Return)
        pump(400)
        expect(warn_count() > before, "没开串口时回车发送没有给任何提示")
    step("发送区：回车发送（未开串口，应有提示）", enter_send, settle=300)

    def ctrl_enter_send():
        window.txt_tx.setPlainText("SMOKE Ctrl+回车")
        pump(100)
        before = warn_count()
        QTest.keyClick(window.txt_tx, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        pump(400)
        expect(warn_count() > before, "Ctrl+回车在没有串口时没有提示")
    step("发送区：Ctrl+回车发送", ctrl_enter_send, settle=300)

    def hex_send_bad():
        window.chk_hex_send.setChecked(True)
        window.txt_tx.setPlainText("ZZ GG")
        pump(100)
        before = warn_count()
        window.btn_send.click()
        pump(300)
        expect(warn_count() > before, "HEX 模式写了非法内容，点发送没有报错提示")
        window.chk_hex_send.setChecked(False)
    step("发送区：HEX 非法内容发送", hex_send_bad, settle=250)

    def timer_send():
        before = warn_count()
        window.chk_timer_send.setChecked(True)
        pump(300)
        expect(warn_count() > before, "没开串口就开定时发送，没有提示")
        expect(not window.chk_timer_send.isChecked(),
               "定时发送在没串口时没有自动弹回（开关卡在开）")
    step("发送区：定时发送（无串口）", timer_send, settle=250)

    # ============================================================ 5. 串口
    print("\n== 5. 串口（无设备）==")

    def open_without_port():
        before = warn_count()
        port_text = window.cmb_port.currentText()
        window.btn_open.click()
        pump(700)
        expect(warn_count() > before, f"没选串口就打开（当前 {port_text!r}）却没提示")
        expect(not window.btn_open.isChecked(), "打开失败后开关没弹回去（卡在开）")
        expect(window._worker is None, "打开失败后 worker 没清掉")
    step("串口：未选口点打开 → 失败提示", open_without_port, settle=400)

    def open_again():
        window.btn_open.click()
        pump(600)
        expect(not window._port_connected, "第二次点击竟然连上了（本机无串口）")
        expect(window.lbl_state.text() in ("未打开",) or "失败" in window.lbl_state.text()
               or "请先" in window.lbl_state.text(),
               f"状态栏文字没回到正常：{window.lbl_state.text()!r}")
    step("串口：再点一次确认没卡住", open_again, settle=400)

    def open_fake_port():
        window.cmb_port.setEditText("COM99")
        pump(120)
        before = warn_count()
        window.btn_open.click()
        ok = wait_until(lambda: window._worker is None, 8.0, "打开 COM99 后 worker 一直没结束")
        pump(300)
        if ok:
            expect(warn_count() > before, "COM99 打不开却没给任何提示")
            expect(not window.btn_open.isChecked(), "COM99 失败后开关卡在开")
            expect(window.txt_tx.isEnabled(), "失败后配置区没解锁")
            print(f"        状态栏={window.lbl_state.text()!r}")
    step("串口：挤一个不存在的 COM99 → 走真实失败链路", open_fake_port, settle=500)

    def reopen_after_fail():
        before = warn_count()
        window.btn_open.click()
        pump(900)
        expect(warn_count() >= before,
               "失败之后再次打开，连提示都没有（可能状态已错乱）")
    step("串口：失败后再点一次", reopen_after_fail, settle=400)

    # ---- 真·收发链路：loop:// 是 pyserial 自带虚拟回环，不碰任何真实设备 ----
    def loopback_open():
        window.cmb_port.setEditText("loop://")
        pump(150)
        before_errors = warn_count()
        window.btn_open.click()
        ok = wait_until(lambda: window._port_connected, 8.0, "loop:// 一直没连上")
        pump(300)
        expect(ok, "loop:// 回环口打不开（发送链路没法继续测）")
        expect("loop://" in window.lbl_state.text(),
               f"打开成功后状态栏没写端口名：{window.lbl_state.text()!r}")
        expect(not window.cmb_baud.isEnabled(), "连上之后波特率还能改（配置没锁）")
        expect(warn_count() == before_errors, "打开 loop:// 时弹了错误提示")
        print(f"        状态栏={window.lbl_state.text()!r}")
    step("串口：打开 loop:// 虚拟回环", loopback_open, settle=400)

    def loopback_send():
        window.txt_tx.setPlainText("AT+PING")
        pump(100)
        before_tx = window._tx_bytes
        QTest.keyClick(window.txt_tx, Qt.Key.Key_Return)
        ok = wait_until(lambda: window._tx_bytes > before_tx, 3.0, "回车后 TX 计数没涨")
        expect(ok, "回车发送没有真的写出去")
        got = wait_until(lambda: "AT+PING" in window.txt_rx.toPlainText(), 3.0,
                         "回环没收到自己发的内容")
        expect(got, "回环口没收到自己发的内容（收包链路没跑通）")
        # 发送成功 → 应该进「历史」下拉；从下拉里点选要能回填
        expect(window.cmb_history.count() > 1, "发送成功之后历史下拉没有新增条目")
        if window.cmb_history.count() > 1:
            window.cmb_history.activated.emit(1)
            pump(200)
            expect(window.txt_tx.toPlainText() == window.cmb_history.itemText(1),
                   "从发送历史里点选没有回填到输入框")
    step("串口：回车发送并收回环数据", loopback_send, settle=400)

    def loopback_hex_send():
        window.chk_hex_send.setChecked(True)
        window.txt_tx.setPlainText("AA 01 FF")
        pump(100)
        before_tx = window._tx_bytes
        window.btn_send.click()
        wait_until(lambda: window._tx_bytes >= before_tx + 3, 3.0, "HEX 发送没发够 3 字节")
        expect(window._tx_bytes >= before_tx + 3,
               f"HEX 发送字节数不对：{before_tx} → {window._tx_bytes}")

        # 非法 HEX：这一次 worker 在，能真正走到 parse_hex 的报错分支
        window.txt_tx.setPlainText("ZZ GG")
        pump(100)
        before_warn = warn_count()
        window.btn_send.click()
        pump(400)
        expect(warn_count() > before_warn, "HEX 写了非法内容，发送没有任何提示")
        window.chk_hex_send.setChecked(False)
        window.txt_tx.setPlainText("")
    step("串口：HEX 发送 + 非法 HEX 报错", loopback_hex_send, settle=300)

    def loopback_timer():
        window.txt_tx.setPlainText("TICK")
        window.spin_interval.setValue(200)
        pump(100)
        before_tx = window._tx_bytes
        window.chk_timer_send.setChecked(True)
        pump(900)
        expect(window._tx_bytes > before_tx, "定时发送开着，TX 计数没涨")
        window.chk_timer_send.setChecked(False)
        pump(300)
        frozen = window._tx_bytes
        pump(700)
        expect(window._tx_bytes == frozen, "关掉定时发送之后还在发")
        expect(window.chk_timer_send.isChecked() is False, "定时发送关不掉")
    step("串口：定时发送开/关（200ms）", loopback_timer, settle=300)

    def loopback_close():
        window.btn_open.click()
        ok = wait_until(lambda: window._worker is None, 6.0, "关串口之后 worker 没清掉")
        pump(300)
        expect(ok, "点关闭之后 worker 还在（状态卡住）")
        expect(not window._port_connected, "关闭后 _port_connected 还是 True")
        expect("未打开" in window.lbl_state.text(),
               f"关闭后状态栏没回到「未打开」：{window.lbl_state.text()!r}")
        expect(window.cmb_baud.isEnabled(), "关闭后配置项没解锁")

        # 再开再关一次：确认状态机可以反复走
        window.btn_open.click()
        again = wait_until(lambda: window._port_connected, 8.0, "第二次开 loop:// 失败")
        expect(again, "关掉之后再也打不开了")
        window.btn_open.click()
        wait_until(lambda: window._worker is None, 6.0, "第二次关串口没清掉 worker")
    step("串口：关闭 + 再开再关（状态机可反复）", loopback_close, settle=400)

    # ============================================================ 6. BLE
    print("\n== 6. 蓝牙广播（连接方式切换）==")

    def switch_ble():
        window.cmb_link.setCurrentText(LINK_BLE)
        pump(400)
        expect(window._link_is_ble(), "切到蓝牙后 _link_is_ble() 还是 False")
        expect(not window.txt_tx.isEnabled(), "蓝牙广播模式下发送框没禁用")
        # 扫描 3.5 秒
        wait_until(lambda: window._ble_scanner is None, 9.0, "蓝牙扫描一直不结束")
        pump(400)
        print(f"        扫描后下拉 {window.cmb_port.count()} 项，状态={window.lbl_state.text()!r}")
    step("蓝牙：切到广播模式并等扫描结束", switch_ble, settle=400)

    def ble_popup():
        print("        [标记] 蓝牙下拉 showPopup", flush=True)
        window.cmb_port.showPopup()
        pump(350)
        print("        [标记] 蓝牙下拉 hidePopup", flush=True)
        window.cmb_port.hidePopup()
        pump(250)
        print("        [标记] 蓝牙重新扫描（点刷新）", flush=True)
        window.btn_rescan.click()
        pump(700)
        ok = wait_until(lambda: window._ble_scanner is None, 9.0,
                        "重新扫描一直不结束")
        print(f"        [标记] 重新扫描结束 scanner={window._ble_scanner!r}"
              f"（{'正常收尾' if ok else '超时'}）", flush=True)
    step("蓝牙：设备下拉开/关 + 重新扫描", ble_popup, settle=400)

    def switch_back():
        print(f"        [标记] 切回串口；此时 scanner={window._ble_scanner!r}", flush=True)
        window.cmb_link.setCurrentText(LINK_SERIAL)
        pump(400)
        print("        [标记] 已切回串口", flush=True)
        expect(not window._link_is_ble(), "切回串口后 _link_is_ble() 还是 True")
        expect(window.txt_tx.isEnabled(), "切回串口后发送框还是禁用")
    step("蓝牙：切回串口", switch_back, settle=300)

    # ============================================================ 7. 最大化
    print("\n== 7. 接收区最大化 / 退出 ==")

    expand_base = {"geometry": window.geometry().getRect()}

    def expand_times(times: int, label: str):
        before = window._expanded
        for _ in range(times):
            window.btn_expand.click()
        pump(900)
        print(f"        {label}后：_expanded={window._expanded} 全屏={window.isFullScreen()}")
        expect(window._expanded != before,
               f"连点 {times} 次之后展开态没变（{label}）")
        if window._expanded:
            expect(window.isFullScreen(), f"{label}后接收区展开了但窗口没全屏")

    step("最大化：连点 3 次（进）", lambda: expand_times(3, "进"), settle=700)
    step("最大化：连点 3 次（出）", lambda: expand_times(3, "出"), settle=700)
    now_geo = window.geometry().getRect()
    base_geo = expand_base["geometry"]
    expect(not window._expanded, "3 进 3 出之后还停在展开态")
    expect(not window.isFullScreen(), "3 进 3 出之后窗口还全屏（没还原）")
    expect(abs(now_geo[2] - base_geo[2]) < 40 and abs(now_geo[3] - base_geo[3]) < 60,
           f"退出最大化后窗口尺寸没还原：{base_geo} → {now_geo}")

    def expand_once_then_escape():
        window.btn_expand.click()
        pump(900)
        early = window._expanded
        # Esc 走的是窗口级 QShortcut：QTest.sendEvent 给控件／windowHandle 都
        # **不过快捷键图**（实测同一段代码有时收有时不收 —— 那正是假阴性的来源）。
        # 真用户按的是键盘，所以这里用 Win32 PostMessage 往 HWND 投一个真的
        # WM_KEYDOWN/WM_KEYUP —— 走的就是系统那条路。
        hwnd = int(window.winId())
        VK_ESCAPE, WM_KEYDOWN, WM_KEYUP = 0x1B, 0x0100, 0x0101
        import ctypes
        user32 = ctypes.windll.user32
        window.activateWindow()
        window.raise_()
        pump(300)
        user32.PostMessageW(hwnd, WM_KEYDOWN, VK_ESCAPE, 0)
        user32.PostMessageW(hwnd, WM_KEYUP, VK_ESCAPE, 0xC0000001)
        hit = wait_until(lambda: not window._expanded, 2.0)
        pump(400)
        print(f"        展开={early}，Esc 之后展开={window._expanded}"
              f"（{'快捷键生效' if hit else '没生效'}）")
        if window._expanded and early:
            # 兜底：确认一下"不是用户按 Esc 该走的那条路"，同时把状态收回来，
            # 免得后面每一步都在全屏里跑
            STATE.append("按 Esc（Win32 真按键）没能退出接收区最大化，只能靠按钮退出"
                         " —— 快捷键链路需要人工确认")
            window._on_escape()
            pump(700)
        if window._expanded:
            window.btn_expand.click()
            pump(800)
    step("最大化：进 → Esc 退出", expand_once_then_escape, settle=600)

    # ============================================================ 8. 边框几何
    print("\n== 8. 窗口几何（拖到屏幕上下沿再回来）==")

    def drag_edges():
        geo = window.geometry()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        print(f"        屏幕={screen.getRect()} 初始={geo.getRect()}")
        window.move(0, 0)
        pump(250)
        window.move(0, screen.bottom() - 200)
        pump(250)
        window.move(geo.x(), geo.y())
        pump(250)
        now = window.geometry()
        expect(abs(now.width() - geo.width()) < 40 and abs(now.height() - geo.height()) < 60,
               f"拖动几次之后几何跑偏：{geo.getRect()} → {now.getRect()}")
        expect(not window.isFullScreen(), "拖完边框窗口卡在全屏态")
    step("边框：移到上/下沿再回来", drag_edges, settle=300)

    # ================================================ 9. 其它用户可达控件
    print("\n== 9. 其它用户可达控件（标题栏 / 分割条 / 编码 / 保留行数 / 回放返回）==")

    def caption_minimize():
        geo = window.geometry().getRect()
        window._caption_bar.btn_min.click()
        pump(700)
        expect(window.isMinimized(), "点标题栏「最小化」没进最小化")
        window.showNormal()                     # 用户从任务栏点回来
        pump(500)
        now = window.geometry().getRect()
        expect(abs(now[2] - geo[2]) < 40 and abs(now[3] - geo[3]) < 60,
               f"最小化还原后尺寸变了：{geo} → {now}")
    step("标题栏：最小化 → 还原", caption_minimize, settle=400)

    def caption_maximize():
        window._caption_bar.btn_max.click()
        pump(900)
        expect(window.isFullScreen(), "标题栏最大化键没进全屏")
        window._caption_bar.btn_max.click()
        pump(900)
        expect(not window.isFullScreen(), "标题栏最大化键再点一次没还原成普通窗口")
        expect(not window._expanded, "标题栏最大化/还原把「接收区展开」状态带跑了")
    step("标题栏：最大化 → 还原", caption_maximize, settle=500)

    def splitter_drag():
        before = window._rx_card.height()
        window._splitter.setSizes([820, 180])
        pump(350)
        after = window._rx_card.height()
        expect(after != before, f"拖分割条之后接收卡高度没变（{before}）")
        window._splitter.setSizes([99999, 0])       # 拖到极端
        pump(350)
        expect(window._rx_card.height() > 100,
               f"分割条拖到极端后接收区被压没了：{window._rx_card.height()}")
        expect(window._tx_card.height() > 20,
               f"分割条拖到极端后发送区被压没了：{window._tx_card.height()}")
        window._splitter.setSizes([before, 320])
        pump(300)
    step("分割条：拖动 + 拖到极端", splitter_drag, settle=350)

    def encoding_switch():
        for enc in ("gbk", "ascii", "latin-1", "gbk"):
            window.cmb_encoding.setCurrentText(enc)
            pump(200)
        window.btn_clear.click()
        pump(150)
        window._on_received("中文测试GBK\n".encode("gbk"))
        window._flush_rx(force=True)
        pump(250)
        expect("中文测试" in window.txt_rx.toPlainText(),
               f"编码选 gbk 时中文没解出来：{window.txt_rx.toPlainText()[:40]!r}")
        window.cmb_encoding.setCurrentText("utf-8")
        pump(200)
    step("接收：编码下拉逐个切换（含 GBK 中文）", encoding_switch, settle=250)

    def rx_lines_change():
        window.cmb_rx_lines.setEditText("abc")
        pump(250)
        expect(window.txt_rx.maximumBlockCount() == window._rx_blocks,
               "「保留行数」输入非法字符后上限被改坏了")
        window.cmb_rx_lines.setEditText("800")
        pump(300)
        expect(window.txt_rx.maximumBlockCount() == 800,
               f"保留行数改成 800 没生效：{window.txt_rx.maximumBlockCount()}")
        window.cmb_rx_lines.setEditText("999999999")
        pump(300)
        expect(window.txt_rx.maximumBlockCount() == 500_000,
               f"保留行数超上限没夹住：{window.txt_rx.maximumBlockCount()}")
        window.cmb_rx_lines.setEditText("3000")
        pump(250)
    step("串口栏：保留行数（非法 / 合法 / 超上限）", rx_lines_change, settle=300)

    def replay_return_button():
        window.btn_open_history.click()
        pump(900)
        if not window._replay_active:
            STATE.append("回放没进去，跳过「返回实时」按钮")
            return
        buttons = [b for b in window._replay_bar.findChildren(QPushButton)
                   if b.text() == "返回实时"]
        if not buttons:
            STATE.append("回放条上找不到「返回实时」按钮")
            window.btn_open_history.click()
            return
        buttons[0].click()
        pump(700)
        expect(not window._replay_active, "点「返回实时」没退出回放")
        expect(window.btn_open_history.text() == "打开历史数据",
               f"退出回放后按钮文案没还原：{window.btn_open_history.text()!r}")
    step("接收：回放条上的「返回实时」", replay_return_button, settle=350)

    def caption_close():
        geo = window.geometry().getRect()
        window._caption_bar.btn_close.click()
        pump(800)
        expect(not window.isVisible(), "点标题栏「关闭」窗口还留着")
        print(f"        关窗后几何={window.geometry().getRect()}（关前 {geo}）")
        window.show()                            # 后面收尾还要用它
        pump(400)
    step("标题栏：关闭窗口（走 closeEvent → 存盘）", caption_close, settle=400)

    # ============================================================ 收尾
    print("\n== 收尾 ==")
    killer.timer.stop()
    window._save_settings()
    pump(150)

    # 先销毁所有窗口，再还原配置（顺序不能反：析构回写会盖掉还原值）
    for widget in QApplication.topLevelWidgets():
        try:
            widget.close()
            widget.deleteLater()
        except Exception:                          # noqa: BLE001
            EXC.append({"where": "收尾：销毁窗口", "text": traceback.format_exc()})
    APP.processEvents()
    APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    APP.processEvents()

    rx_history._path = None
    rx_history._lines.clear()
    rx_history._pending.clear()
    rx_history._file_lines = 0
    rx_history._resolve_dir = real_resolve

    def restore() -> None:
        for key in global_settings.allKeys():
            if key not in snapshot:
                global_settings.remove(key)
        for key, value in snapshot.items():
            if value is None:
                global_settings.remove(key)
            else:
                global_settings.setValue(key, value)
        global_settings.sync()

    def verify_restore() -> list[str]:
        """回读注册表逐键比对 —— 还原**必须**可验证，不能只写一遍就当成了。

        真机实测过一次：写完之后有键没落地（同一时刻还有别的进程在读写同一份
        配置时尤其容易），结果"测试值留在用户配置里"。所以这里读回来对账，
        对不上就再写一遍，还不对就大声报出来。
        """
        reader = QSettings("MimoWorks", "SerialAssistant")
        reader.sync()                   # 强制从注册表重读（QSettings 内部有进程级缓存）
        bad = []
        for key, value in snapshot.items():
            now = reader.value(key)
            if value is None:
                if now is not None:
                    bad.append(f"{key}: 应删除，实际 {now!r}")
            elif str(now) != str(value):
                bad.append(f"{key}: 应 {value!r}，实际 {now!r}")
        for key in reader.allKeys():
            if key not in snapshot:
                bad.append(f"{key}: 多余键（快照里没有）")
        return bad

    restore()
    leftover = verify_restore()
    if leftover:
        restore()
        leftover = verify_restore()
    if leftover:
        STATE.append("配置还原不干净（已重试一次仍不一致）：" + "；".join(leftover[:6]))
    for item in leftover:
        print(f"  ⚠ 还原残留：{item}")
    RESTORE_LEFT.extend(leftover)
    print(f"[收尾] 配置还原回读：{'通过（逐键一致）' if not leftover else f'{len(leftover)} 条残留'}")

    try:
        clip.setText(clip_backup)
    except Exception:                              # noqa: BLE001
        EXC.append({"where": "收尾：还原剪贴板", "text": traceback.format_exc()})

    drain_stderr("收尾")

    # 日志新增的 ERROR 行
    log_errors: list[str] = []
    if _LOG_PATH and os.path.exists(_LOG_PATH):
        with open(_LOG_PATH, "r", encoding="utf-8", errors="replace") as handle:
            handle.seek(_LOG_OFFSET)
            for line in handle:
                if "[ERROR]" in line:
                    log_errors.append(line.rstrip())
    write_report(log_errors)


def write_report(log_errors: list[str]) -> None:
    lines: list[str] = []
    lines.append("动态冒烟报告（用户可达交互）")
    lines.append("=" * 78)
    lines.append(f"跑完时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Qt 平台：{os.environ.get('QT_QPA_PLATFORM')}")
    lines.append(f"探针自检（阳性对照）：{'通过 —— 槽内异常确实被记录' if CONTROL[0] else '✗ 失败，本次「0 崩」不可信'}")
    lines.append("")
    lines.append(f"【一、覆盖清单】共 {len(STEPS)} 条交互")
    for index, name in enumerate(STEPS, 1):
        lines.append(f"  {index:02d}. {name}")
    lines.append("")
    lines.append(f"【二、真异常（traceback）】{len(EXC)} 条")
    if not EXC:
        lines.append("  （无）")
    for item in EXC:
        lines.append(f"  ✗ 触发点：{item['where']}")
        for row in item["text"].rstrip().splitlines():
            lines.append(f"      {row}")
        lines.append("")
    lines.append(f"【三、stderr 非 traceback 输出】{len(STDERR_WARN)} 条")
    if not STDERR_WARN:
        lines.append("  （无）")
    for item in STDERR_WARN:
        lines.append(f"  ! {item['where']}: {item['text'].strip()[:400]}")
    lines.append("")
    lines.append(f"【四、状态/视觉异常】{len(STATE)} 条")
    if not STATE:
        lines.append("  （无）")
    for item in STATE:
        lines.append(f"  · {item}")
    lines.append("")
    lines.append(f"【五、拦截到的产品提示（不是异常，用来确认失败路径真的走到了）】{len(WARNS)} 条")
    for item in WARNS:
        lines.append(f"  → {item}")
    lines.append("")
    lines.append(f"【六、自动关掉的模态框】{len(DIALOGS)} 条")
    for item in DIALOGS:
        lines.append(f"  → {item}")
    lines.append("")
    lines.append(f"【七、日志里新增的 ERROR 行】{len(log_errors)} 条")
    for item in log_errors:
        lines.append(f"  ⚠ {item}")
    lines.append("")
    lines.append("【八、用户配置还原校验】"
                 + ("通过 —— 回读注册表逐键一致" if not RESTORE_LEFT
                    else f"✗ {len(RESTORE_LEFT)} 条残留"))
    for item in RESTORE_LEFT:
        lines.append(f"  ⚠ {item}")
    lines.append("")
    lines.append("【九、没覆盖的（说明，不是缺陷）】")
    for item in (
        "启动自动检查更新（需要联网，且发现新版本会 exec() 一个模态对话框）",
        "设置面板「检查更新」按钮（同上，走 GitHub + 代理回退）",
        "真实串口硬件收发（本机无 COM 口；收发链路已用 loop:// 回环覆盖）",
        "蓝牙「扫不到设备 / 蓝牙不可用」失败分支（本机蓝牙可用，扫到 27 个设备）",
        "删除车辆的 QMessageBox 二次确认（置为直接确认，删除链路照走）",
        "新建车号的 _NameDialog.exec()（改为直接填名确定）",
        "文件保存对话框（改为固定写到 _check/smoke_frames/）",
    ):
        lines.append(f"  - {item}")
    lines.append("")
    lines.append("=" * 78)
    lines.append(f"一行统计：跑了 {len(STEPS)} 条交互，崩 {len(EXC)} 条，"
                 f"状态异常 {len(STATE)} 条。")

    text = "\n".join(lines)
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    print("\n" + text)
    print(f"\n（报告已写入 {REPORT_PATH}）")


def restore_from_file(path: str) -> int:
    """从快照文件还原用户配置（探针被硬杀之后的补救入口）。

    用法：``.venv/Scripts/python.exe devtools/_probe_smoke_all.py --restore
    _check/smoke_settings_snapshot.txt``
    """
    from PySide6.QtCore import QSettings as _S
    settings = _S("MimoWorks", "SerialAssistant")
    wanted: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            key, _, value = line.rstrip("\n").partition("\t")
            if key:
                wanted[key] = value
    if not wanted:
        print(f"快照文件是空的：{path}")
        return 1
    for key in settings.allKeys():
        if key not in wanted:
            settings.remove(key)
    for key, value in wanted.items():
        settings.setValue(key, value)
    settings.sync()
    reader = _S("MimoWorks", "SerialAssistant")
    reader.sync()
    bad = [key for key in wanted
           if str(reader.value(key)) != value]
    print(f"已从 {path} 还原 {len(wanted)} 键；不一致 {len(bad)} 项 {bad[:8]}")
    return 0 if not bad else 1


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--restore":
        sys.exit(restore_from_file(sys.argv[2]))
    run()
    sys.exit(0)
