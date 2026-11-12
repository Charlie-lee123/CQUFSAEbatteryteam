"""USB 转串口驱动体检的**界面级**真机验证（QApplication + 真 MainWindow）。

``_verify_drv.py`` 验的是只读判断链路（配置表 / 驱动包定位 / pnputil 解析 / 接线
是否写对）；这里验的是**真窗口 + 真事件循环**下的行为 —— 体检定时器会不会反复
触发、模态框会不会在该安静的时候冒出来、后台线程是否真的没把界面冻住、QSettings
有没有被多写。这些静态读代码看不出来，只能把它跑起来。

被测口径（2026-11-12 连改两版后的现行代码，见 main_window.py）：
  · 判据是 ``needed_keys()``（插着 + 我们有驱动 + 驱动库没装），**不是**
    ``missing_keys()``（没插 CP2102 的用户不该被问）；
  · 三个触发点：启动 ``_driver_gate_tick``（force=False）、端口轮询
    ``_poll_ports`` 每 4 拍（force=True）、打开失败 ``_on_open_failed``（单个框）；
  · 三道保护：``driver_setup`` 记「版本|拒过的键」（force=True 只越过"版本问过"，
    **不越过"这个芯片我拒过"**）、``_driver_asked``（一会话只问一次）、
    ``SA_SKIP_DRIVER_PROMPT``（自动化免打扰）。

判据（每条一个断言，失败要能定位）：

  S. 规格守卫：``_maybe_install_drivers`` 的开关叫 ``force``（不是旧的 ``auto``）、
     ``drv_assist`` 接口齐全且 ``hint()`` 已删、体检状态确实在构造路径里建立。
  0. 开屏期间不弹框：``_skip_startup_anim`` 为真时体检定时器只空转（不弹、也不被
     吃掉）；开屏退场后立刻补一次体检并弹框；窗口上真的建出三个状态属性。
  1. 没插设备就不打扰：``needed_keys()`` 为空时起真窗口，5 秒内（盖住 700ms 启动
     体检 + 4s 轮询体检两条路径）没有任何 ``QMessageBox``；另验
     ``SA_SKIP_DRIVER_PROMPT=1`` 时即使 ``needed_keys()`` 非空也不弹。
  2. 去重机制：缺驱动 → 弹框 →「以后再说」记「版本 + 拒过的键」；随后
     ``force=False`` 不弹、``force=True`` 但芯片正是拒过的那家也不追、换成没问过的
     芯片则照问、同窗口再 ``force=True`` 不弹（``_driver_asked``）、同一 QSettings
     起新窗口启动体检不问；启动体检定时器**只触发一次就自毁**。
  3. 点「现在安装」走后台线程：假 install 调用一次、参数正确、``_driver_busy``
     回来、toast 说"驱动已装好"、端口刷新 announce=False；安装期间主线程仍在处理
     事件（30ms 计时器在**安装中**那一段计数 > 5，证明界面没卡死）。
  4. 拒绝授权（code=5）：只 toast「已取消授权…」，**不**弹错误框。
  5. 打开串口失败联动：缺驱动时弹**一个**框（标题「打不开串口」），框里说清
     「打不开 + 缺哪家驱动」；点「知道了」不装、点「安装驱动」才进
     ``_start_driver_install(need)``；没有缺驱动设备时仍旧只 ``_warn`` 一句。
  6. 设置不被污染：开头做全量快照，``finally`` 里逐键还原 + 删除新增键，收尾
     逐键比对并打印差异。
  7. ``_poll_ports`` 的节拍：``_driver_ticks=3`` 时调一次就触发 force 体检；
     ``=2`` 时要调两次才触发一次。
  8. 拒绝记录语义：``driver_setup="<版本>|<拒过的键>"`` —— 同一版本且芯片正是拒过
     的不问；多插一个没拒过的要问；版本过期（换驱动包）要重新问；老格式（纯版本
     号）解析成空拒绝集。

⛔ **绝不真的安装驱动**：``drv_assist.install`` 全程换成假函数，另加一道 ``_run``
   绊索（一旦有人要跑 ``pnputil /add-driver`` 就抛异常），保证不弹 UAC、不改
   机器驱动库。

⚠️ 退出码不可全信：本项目已知现象 —— MainWindow 相关脚本收尾时 C++ 对象已析构，
   退出码偶尔是 **127**。所以收尾一律 ``win.hide()`` 而不是 ``win.close()``；
   判定**只看最后那行 ``IS_PASS n/m``**，退出码仅作参考。

⚠️ 起 MainWindow 会带出自动检查更新、BLE 等后台任务，这里把 ``updater.UpdateChecker``
   换成哑桩（不联网、不弹更新框），免得它们的模态框混进"没有弹框"的观测里。

跑法：``.venv\\Scripts\\python.exe devtools\\_verify_drv_ui.py``
"""
from __future__ import annotations

import contextlib
import inspect
import os
import re
import sys
import time

# ⚠️ 必须在 import PySide6 之前定平台：offscreen 下本项目的几何/字体断言会假红。
os.environ["QT_QPA_PLATFORM"] = "windows"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtCore import QObject, QSettings, QTimer, Signal      # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox            # noqa: E402

APP = QApplication(sys.argv)
APP.setQuitOnLastWindowClosed(False)

import drv_assist as D                                             # noqa: E402
import main_window as MW                                           # noqa: E402
import updater                                                     # noqa: E402

def qs() -> QSettings:
    """每次都用**全新**的 QSettings 实例。

    ⛔ 别把它做成模块级单例：产品代码写 ``driver_setup`` 用的是它自己新建的实例，
    各自有自己的内存缓存。同一个进程里两个实例交叉读写会互相覆盖 —— 我这边一次
    ``sync()`` 能把产品刚写进去的"问过了"记录按旧缓存清掉，表现为"莫名其妙又弹框"。
    实测踩过：2h/3d 间歇性假红。新实例 + 立即 sync 才是干净读。
    """
    return QSettings("MimoWorks", "SerialAssistant")
BOX_TITLE = "串口驱动"                       # 体检对话框的标题
BTN_NOW = "现在安装"
BTN_LATER = "以后再说"
SKIP_ENV = "SA_SKIP_DRIVER_PROMPT"

PASS = 0
FAIL: list[str] = []
NOTES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL.append(f"{name}" + (f" —— {detail}" if detail else ""))
        print(f"  FAIL {name}" + (f" —— {detail}" if detail else ""))


def note(text: str) -> None:
    NOTES.append(text)
    print(f"  NOTE {text}")


def clear_skip_env() -> None:
    """弹框类用例的前提：``SA_SKIP_DRIVER_PROMPT`` 必须没设，否则产品直接 return。"""
    os.environ.pop(SKIP_ENV, None)


@contextlib.contextmanager
def skip_env(on: bool = True):
    """临时开关 ``SA_SKIP_DRIVER_PROMPT``，出来一定复原。"""
    old = os.environ.pop(SKIP_ENV, None)
    if on:
        os.environ[SKIP_ENV] = "1"
    try:
        yield
    finally:
        os.environ.pop(SKIP_ENV, None)
        if old is not None:
            os.environ[SKIP_ENV] = old


# ---------------------------------------------------------------------------
# 基础设施：事件循环泵、模态框观察者、补丁栈、设置快照
# ---------------------------------------------------------------------------
def pump(seconds: float) -> None:
    """跑一段事件循环（不用 app.exec()，脚本要能自己控制节奏）。"""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        APP.processEvents()
        time.sleep(0.005)


def wait_until(pred, timeout: float = 8.0, step: float = 0.02) -> bool:
    """轮询等条件成立（期间照跑事件循环 —— 模态框的嵌套循环也从这里进）。"""
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if pred():
            return True
        APP.processEvents()
        time.sleep(step)
    return bool(pred())


def _find_button(box: QMessageBox, text: str):
    for button in box.buttons():
        if button.text().replace("&", "") == text:
            return button
    return None


class BoxWatcher(QObject):
    """在事件循环里盯着 QMessageBox：记下出现过哪些、点掉指定按钮、卡住就强关。

    模态框（``box.exec()``）会进**嵌套事件循环**，只有事件循环里的定时器才碰得到
    它 —— 所以只能 QTimer 轮询 + ``button.click()``，不能 sleep 也不能在外面点。

    ``click=""`` 表示只看不点（用来断言"根本没弹框"）；万一真弹了，超过 ``grace``
    就 ``done(0)`` 强关，绝不把脚本挂在模态框上。
    """

    def __init__(self, title: str = "", click: str = "",
                 grace_ms: int = 1200, total_ms: int = 8000) -> None:
        super().__init__()
        self._title = title
        self._click = click
        self._grace = grace_ms / 1000.0
        self._total = total_ms / 1000.0
        self._t0 = time.perf_counter()
        self._first_seen: float | None = None
        self._seen_ids: set[int] = set()
        self.titles: list[str] = []        # 出现过的框标题（去重，按出现顺序）
        self.texts: list[str] = []          # 每个框的正文（text + informativeText）
        self.clicked = ""                  # 实际点下去的按钮文字
        self.forced = 0                     # 被强关次数（> 0 = 卡住了）
        self.timed_out = False
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _hit(self, box: QMessageBox) -> bool:
        return self._title == "" or box.windowTitle() == self._title

    def _remember(self, box: QMessageBox) -> None:
        if id(box) not in self._seen_ids:
            self._seen_ids.add(id(box))
            self.titles.append(box.windowTitle())
            self.texts.append(f"{box.text()}\n{box.informativeText()}")

    def _tick(self) -> None:
        now = time.perf_counter()
        if now - self._t0 > self._total:
            self.timed_out = True
            self._force_close()
            self.stop()
            return
        boxes = [w for w in QApplication.topLevelWidgets()
                 if isinstance(w, QMessageBox) and w.isVisible() and self._hit(w)]
        if not boxes:
            self._first_seen = None
            return
        for box in boxes:
            self._remember(box)
        if self._click and not self.clicked:
            for box in boxes:
                button = _find_button(box, self._click)
                if button is not None:
                    self.clicked = button.text()
                    button.click()
                    return
        if self._first_seen is None:
            self._first_seen = now
        elif now - self._first_seen > self._grace:
            self._force_close()

    def _force_close(self) -> None:
        for box in list(QApplication.topLevelWidgets()):
            if isinstance(box, QMessageBox) and box.isVisible() and self._hit(box):
                self.forced += 1
                self._remember(box)
                box.done(0)            # 退出 exec()，别把脚本挂死


class Patches:
    """临时替换属性，用完逐个还原（不在进程里留痕迹）。"""

    def __init__(self) -> None:
        self._saved: list[tuple[object, str, object]] = []

    def set(self, obj: object, name: str, value: object) -> None:
        for saved_obj, saved_name, _old in self._saved:
            if saved_obj is obj and saved_name == name:
                setattr(obj, name, value)
                return
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def restore(self) -> None:
        for obj, name, old in reversed(self._saved):
            setattr(obj, name, old)
        self._saved.clear()


def snapshot() -> dict:
    """QSettings 全量快照（键 + 值，值保留原 QVariant 类型）。"""
    store = qs()
    store.sync()
    return {key: store.value(key) for key in store.allKeys()}


def settings_delta(before: dict, after: dict) -> list[str]:
    """逐键比对，返回人能读的差异行（新增 / 消失 / 值变了）。"""
    lines: list[str] = []
    for key in sorted(set(before) | set(after)):
        if key not in before:
            lines.append(f"+ {key} = {after[key]!r}")
        elif key not in after:
            lines.append(f"- {key} = {before[key]!r}")
        elif before[key] != after[key]:
            lines.append(f"~ {key}: {before[key]!r} → {after[key]!r}")
    return lines


def restore_settings(snap: dict) -> None:
    """逐键还原快照，并删掉本次运行新增的键（项目铁律：不污染用户设置）。"""
    store = qs()
    for key in store.allKeys():
        if key not in snap:
            store.remove(key)
    for key, value in snap.items():
        store.setValue(key, value)
    store.sync()


def write_setup(record: str) -> None:
    """写 ``driver_setup``（空串 = 删掉），写完立刻落盘。"""
    store = qs()
    if record:
        store.setValue("driver_setup", record)
    else:
        store.remove("driver_setup")
    store.sync()


def read_setup() -> str:
    """读 ``driver_setup`` 的原始字符串（空串 = 没有记录）。"""
    store = qs()
    store.sync()
    return str(store.value("driver_setup", "") or "")


# ---------------------------------------------------------------------------
# 假件：假的 install / 假端口 worker / 哑桩更新检查
# ---------------------------------------------------------------------------
class FakeInstall:
    """``drv_assist.install`` 的替身：记下调用、可选地"装"一会儿。"""

    def __init__(self, result: tuple[int, str] = (0, "ok"), delay: float = 0.0) -> None:
        self.calls: list[list[str]] = []
        self._result = result
        self._delay = delay

    def __call__(self, keys, root: str = ""):
        self.calls.append(list(keys))
        if self._delay:
            time.sleep(self._delay)
        return self._result


class FakePortWorker(QObject):
    """冒充串口 worker：``_on_open_failed`` 靠 ``self.sender()`` 认人，所以必须
    用真信号触发，不能直接调方法（直接调时 sender() 是 None，方法会早退）。"""

    open_failed = Signal(str)

    def __init__(self, port: str) -> None:
        super().__init__()
        self.port = port


class NoopChecker(QObject):
    """``updater.UpdateChecker`` 的哑桩：不联网、不弹更新框。"""

    checked = Signal(dict)
    failed = Signal(str)

    def start(self) -> None:
        pass


# ---------------------------------------------------------------------------
# 真窗口的起停
# ---------------------------------------------------------------------------
def build_window(label: str):
    """构造真 MainWindow（构造期间不跑事件循环，定时器还不会触发）。"""
    began = time.perf_counter()
    win = MW.MainWindow()
    cost = time.perf_counter() - began
    print(f"  （{label}：MainWindow 构造 {cost:.2f}s）")
    if getattr(win, "_skip_startup_anim", False):
        note(f"{label} 的 _skip_startup_anim 意外为真，启动体检会一直等开屏退场")
    return win


def quiet(win) -> None:
    """让窗口闭嘴并藏起来：停掉它的所有子定时器（免得后台继续写设置），再 hide。

    ⚠️ 不 close()：项目已知 close 时会因 C++ 对象析构偶发退出码 127。
    """
    for timer in win.findChildren(QTimer):
        try:
            timer.stop()
        except RuntimeError:                   # 已被删掉的 C++ 对象
            pass
    win.hide()


def arm_gate_spy(patches: Patches) -> list[int]:
    """数一数 ``_driver_gate_tick`` 被调了几次（类级替换，构造前装）。

    为什么不连 ``timeout`` 信号：定时器在自己的槽里就 ``deleteLater`` 了，信号
    后续的槽能不能收到不稳定；类级替换是确定的。
    """
    ticks: list[int] = []
    real_tick = MW.MainWindow._driver_gate_tick

    def spy(window):
        ticks.append(1)
        real_tick(window)

    patches.set(MW.MainWindow, "_driver_gate_tick", spy)
    return ticks


def kick_gate(win, watcher: BoxWatcher) -> bool:
    """等启动体检的框出现并被点掉；构造/定时器意外时不至于假红。"""
    if wait_until(lambda: bool(watcher.clicked), timeout=6.0):
        return True
    note("体检定时器 6 秒内没触发，手动踢一次 _driver_gate_tick 兜底")
    try:
        win._driver_gate_tick()
    except RuntimeError:
        return False
    return wait_until(lambda: bool(watcher.clicked), timeout=4.0)


def install_tripwire(patch: Patches) -> dict:
    """给 ``drv_assist._run`` 装绊索：真要跑 add-driver 就抛异常并记一笔。"""
    trip = {"add_driver": []}
    real_run = D._run

    def guarded_run(args, timeout: float = 20.0):
        if any("add-driver" in str(arg) for arg in args):
            trip["add_driver"].append([str(a) for a in args])
            raise RuntimeError("绊索：检测到真实的 pnputil /add-driver")
        return real_run(args, timeout=timeout)

    patch.set(D, "_run", guarded_run)
    return trip


# ---------------------------------------------------------------------------
# S 规格守卫
# ---------------------------------------------------------------------------
def _argtypes_first(src: str, func: str) -> str | None:
    """从源码里抠出 ``xxx.<func>.argtypes = [...]`` 的第一项类型名。"""
    hit = re.search(rf"\.{func}\.argtypes\s*=\s*\[([^\]]*)\]", src, re.S)
    if hit is None:
        return None
    return hit.group(1).replace("\n", " ").split(",")[0].strip()


def test_s_surface() -> None:
    """S 被测实现的接口长相 —— 规格再漂移就在这里先红，免得后面用例假绿。"""
    print("[S] 规格守卫（force 口径 / 接口 / 构造期状态 / 64 位句柄 argtypes）")
    sig = str(inspect.signature(MW.MainWindow._maybe_install_drivers))
    check("S1. _maybe_install_drivers 的开关叫 force（旧的 auto 已消失）",
          "force" in sig and "auto" not in sig, sig)
    wanted = ("needed_keys", "describe", "install", "payload_dir", "PAYLOAD_VERSION")
    missing = [name for name in wanted if not hasattr(D, name)]
    check("S2. drv_assist 接口齐全，且 hint() 已删除",
          not missing and not hasattr(D, "hint"),
          f"缺={missing} hint={hasattr(D, 'hint')}")
    # ⚠️ 不能拿 inspect.getsource(MainWindow.__init__) 判：本文件里 __init__ 长达
    #    400 多行，inspect 的 block 探测会在中途截断（实测只到 _init_dlc 那行），
    #    断言会假红。改成全文搜索 + 运行时 hasattr（见 0c）。
    with open(os.path.join(ROOT, "main_window.py"), encoding="utf-8") as fh:
        whole = fh.read()
    gone = [name for name in ("self._driver_asked = ", "self._driver_ticks = ",
                              "self._driver_gate: QTimer | None = QTimer(")
            if name not in whole]
    check("S3. 驱动体检状态与定时器在构造路径里建立", not gone, str(gone))
    # ⛔ 防重入：_build_ui() 若被重复调用，这段必须不再重建 —— 否则 _driver_asked
    #    被清回 False、_driver_gate 重新 start，同一会话会重复弹框。哨兵用
    #    _driver_asked 是否存在（_driver_gate 会被 _driver_gate_tick 置 None，不能用）。
    check("S3b. 这段初始化有防重入守卫（以 hasattr(self, '_driver_asked') 为哨兵）",
          re.search(r"if not hasattr\(self, \"_driver_asked\"\):", whole) is not None,
          "没找到 if not hasattr(self, \"_driver_asked\") 守卫")
    # 哨兵只有在"_driver_asked 仅在此处初始化、永不被打回 False"时才成立：
    # 别处再来一句 self._driver_asked = False，守卫就形同虚设（重复弹框）。
    check("S3c. _driver_asked 只在守卫块里初始化一次（没有别处清回 False）",
          whole.count("self._driver_asked = False") == 1,
          f"出现 {whole.count('self._driver_asked = False')} 次")
    check("S3d. _driver_gate.start() 只有一处（守卫块内），不会重复起表",
          whole.count("self._driver_gate.start()") == 1,
          f"出现 {whole.count('self._driver_gate.start()')} 次")
    check("S4. 建立它的 _build_ui 确实由 __init__ 调用（否则永远不跑）",
          "self._build_ui()" in whole, "没找到 self._build_ui() 调用")
    # 提权子进程句柄是 64 位：不声明 argtypes，ctypes 会按 C int 传，高 32 位被截
    # → WaitForSingleObject 等到野句柄（表现为"装完立刻报失败"）。这条只查源码
    # 结构，绝不真的跑 install（会弹 UAC）。
    with open(os.path.join(ROOT, "drv_assist.py"), encoding="utf-8") as fh:
        drv_src = fh.read()
    bad: list[str] = []
    for func in ("WaitForSingleObject", "GetExitCodeProcess", "CloseHandle"):
        first = _argtypes_first(drv_src, func)
        if first != "wintypes.HANDLE":
            bad.append(f"{func}: {first!r}")
    check("S5. kernel32 三个句柄函数都声明了 argtypes 且首参是 wintypes.HANDLE",
          not bad, "; ".join(bad) or "没找到 argtypes 声明")


# ---------------------------------------------------------------------------
# 0 开屏期间不许弹框
# ---------------------------------------------------------------------------
def test_0_splash_guard() -> None:
    """0 开屏没退场（``_skip_startup_anim`` 为真）时体检定时器只空转，不弹框。"""
    print("[0] 开屏期间不弹框（等开屏退场后才问）")
    patches = Patches()
    try:
        clear_skip_env()
        patches.set(D, "needed_keys", lambda: ["cp210x"])
        write_setup("")                          # 清掉旧记录，保证会问
        ticks = arm_gate_spy(patches)

        watcher = BoxWatcher(title=BOX_TITLE, click=BTN_LATER)
        win = build_window("test0")
        win._skip_startup_anim = True              # 模拟 main.py 的开屏期
        win.show()
        pump(2.5)
        check("0a. 开屏没退场时：一框不弹，体检定时器还在空转（没被吃掉）",
              not watcher.titles and watcher.forced == 0
              and len(ticks) >= 2 and win._driver_gate is not None,
              f"titles={watcher.titles} forced={watcher.forced} "
              f"ticks={len(ticks)} gate={win._driver_gate!r}")

        win._skip_startup_anim = False              # 开屏退场
        woken = kick_gate(win, watcher)
        check("0b. 开屏退场后马上补一次体检并弹框",
              woken and BOX_TITLE in watcher.titles, f"titles={watcher.titles}")
        attrs = [name for name in ("_driver_busy", "_driver_asked", "_driver_ticks")
                 if not hasattr(win, name)]
        check("0c. 窗口上真的建出了体检状态（_driver_busy/_driver_asked/_driver_ticks）",
              not attrs, str(attrs))
        watcher.stop()
        quiet(win)
    finally:
        patches.restore()


# ---------------------------------------------------------------------------
# 1 + 4 + 5：同一个窗口
# ---------------------------------------------------------------------------
def test_1_quiet(patch: Patches):
    """1 没插设备就不打扰 + SA_SKIP_DRIVER_PROMPT 开关生效。"""
    print("[1] 没插设备就不打扰（5 秒内不许有任何 QMessageBox）+ 免打扰开关")
    clear_skip_env()
    real_needed = D.needed_keys()
    real_missing = D.missing_keys()
    note(f"本机 needed_keys() = {real_needed!r}；missing_keys() = {real_missing!r}")
    if real_needed:
        note("本机此刻真插着缺驱动的设备（needed_keys 非空），改把 needed_keys"
             " 置空做确定性验证：没插设备时一律不许弹框")
        patch.set(D, "needed_keys", lambda: [])
        label = "needed_keys→[]"
    else:
        label = "真机现状（没有任何设备缺驱动）"
        if real_missing:
            note(f"missing_keys() 非空（{real_missing!r}）但**没有设备插着**；"
                 "按现在的设计不该因此弹框 —— 本条就是在验这一点")

    watcher = BoxWatcher(title="", click="")          # 任何 QMessageBox 都算违规
    win = build_window("test1")
    win.show()
    pump(5.0)          # 盖住 700ms 启动体检 + 4s 端口轮询体检两条路径
    check(f"1a. {label}：5 秒内没有任何弹框",
          not watcher.titles and watcher.forced == 0 and not watcher.timed_out,
          f"titles={watcher.titles} forced={watcher.forced} "
          f"timed_out={watcher.timed_out}")
    watcher.stop()

    # 反向证明：缺驱动本该弹，但 SA_SKIP_DRIVER_PROMPT=1 时一框不弹
    inner = Patches()
    try:
        inner.set(D, "needed_keys", lambda: ["cp210x"])
        with skip_env():
            watcher2 = BoxWatcher(title=BOX_TITLE, click="", grace_ms=600)
            win._maybe_install_drivers(force=True)     # 免打扰开关命中，立刻 return
            pump(0.6)
            check("1b. SA_SKIP_DRIVER_PROMPT=1 时缺驱动也不弹框（自动化免打扰）",
                  not watcher2.titles and watcher2.forced == 0,
                  f"titles={watcher2.titles} forced={watcher2.forced}")
            watcher2.stop()
    finally:
        inner.restore()
    return win


def test_4_cancel(win) -> None:
    """4 拒绝授权（code=5）的文案。"""
    print("[4] 拒绝管理员授权（code=5）：只 toast，不弹错误框")
    warns: list[str] = []
    win._warn = lambda message: warns.append(message)
    win.lbl_toast.setText("")
    win._driver_busy = True                        # 模拟"正在装"的状态
    win._on_driver_install_done(5, "已取消管理员授权，驱动没有安装")
    text = win.lbl_toast.text()
    check("4a. 拒绝授权不弹错误框（_warn 未被调用）", warns == [], str(warns))
    check("4b. 拒绝授权有专门文案", "已取消授权" in text and "没有安装" in text,
          repr(text))
    check("4c. 拒绝授权后 _driver_busy 复位（不会卡住后续体检）",
          win._driver_busy is False, str(win._driver_busy))


def test_5_open_failed(win) -> None:
    """5 打开串口失败：**一个**框里说清缺驱动，两条按钮路径都得对。"""
    print("[5] 打开串口失败：单个框「打不开串口」，知道了不装 / 安装驱动才装")
    patches = Patches()
    try:
        clear_skip_env()
        patches.set(D, "needed_keys", lambda: ["ch34x"])
        patches.set(D, "describe", lambda keys: "CH340 / CH341")
        fake_install = FakeInstall((0, "ok"))
        patches.set(D, "install", fake_install)      # 双保险：绝不可能真装
        warns: list[str] = []
        installs: list[list[str]] = []
        win._warn = lambda message: warns.append(message)
        win._start_driver_install = lambda need: installs.append(list(need))

        # --- 路径 A：点「知道了」→ 不装、不弹第二个框 ---
        win._driver_asked = False
        watcher = BoxWatcher(title="打不开串口", click="知道了", grace_ms=1500)
        worker = FakePortWorker("COM99")            # 真信号触发，sender() 才认得
        win._worker = worker
        worker.open_failed.connect(win._on_open_failed)
        worker.open_failed.emit("拒绝访问")          # 内部 box.exec() 由 watcher 点掉
        pump(0.3)

        body = watcher.texts[0] if watcher.texts else ""
        check("5a. 只弹了一个框，标题是「打不开串口」",
              len(watcher.titles) == 1 and watcher.titles[0] == "打不开串口"
              and watcher.forced == 0,
              f"titles={watcher.titles} forced={watcher.forced}")
        check("5b. 框里同时有「打不开」和「还没有驱动」",
              "打不开" in body and "还没有驱动" in body
              and "CH340 / CH341" in body, repr(body[:168]))
        check("5c. 点「知道了」→ 不装、也不走 _warn（不叠第二个模态框）",
              watcher.clicked == "知道了" and installs == [] and warns == [],
              f"clicked={watcher.clicked!r} installs={installs} warns={warns!r}")
        check("5d. 并框时先把 _driver_asked 置真（本会话不再追问）",
              win._driver_asked is True, str(win._driver_asked))
        watcher.stop()

        # --- 路径 B：点「安装驱动」→ 才走后台安装，参数是缺的那个键 ---
        win._driver_asked = False
        watcher2 = BoxWatcher(title="打不开串口", click="安装驱动", grace_ms=1500)
        worker2 = FakePortWorker("COM98")
        win._worker = worker2
        worker2.open_failed.connect(win._on_open_failed)
        worker2.open_failed.emit("拒绝访问")
        pump(0.3)
        check("5e. 点「安装驱动」→ _start_driver_install(need) 被调用一次",
              watcher2.clicked == "安装驱动" and installs == [["ch34x"]],
              f"clicked={watcher2.clicked!r} installs={installs}")
        check("5f. 全程没有真的跑 drv_assist.install（假函数零调用）",
              fake_install.calls == [], str(fake_install.calls))
        watcher2.stop()

        # --- 路径 C：没有缺驱动设备时，仍旧只 _warn 一句，不弹框 ---
        patches.set(D, "needed_keys", list)
        win._driver_asked = False
        warns.clear()
        plain = BoxWatcher(title="", click="", grace_ms=600)
        worker3 = FakePortWorker("COM97")
        win._worker = worker3
        worker3.open_failed.connect(win._on_open_failed)
        worker3.open_failed.emit("拒绝访问")
        pump(0.4)
        check("5g. 没有缺驱动设备时只 _warn 一句，不弹框",
              len(warns) == 1 and "打不开" in warns[0] and not plain.titles,
              f"warns={warns!r} titles={plain.titles}")
        plain.stop()
    finally:
        patches.restore()


# ---------------------------------------------------------------------------
# 2 去重机制：driver_setup + _driver_asked
# ---------------------------------------------------------------------------
def test_2_dedup() -> None:
    """2 去重机制：``driver_setup``（版本 + 拒过的键）与 ``_driver_asked``。"""
    print("[2] 去重机制（版本+拒过名单 & 一会话一问）")
    patches = Patches()
    try:
        clear_skip_env()
        patches.set(D, "needed_keys", lambda: ["cp210x"])
        write_setup("")                          # 清掉旧记录，保证会问
        ticks = arm_gate_spy(patches)

        # --- 2a~2c：启动体检弹框，「以后再说」记下拒过的键 ---
        watcher = BoxWatcher(title=BOX_TITLE, click=BTN_LATER)
        win = build_window("test2")
        win.show()
        clicked = kick_gate(win, watcher)
        pump(0.4)
        check("2a. 缺驱动时启动体检弹了「串口驱动」框，并点到了「以后再说」",
              clicked and BOX_TITLE in watcher.titles
              and watcher.clicked == BTN_LATER,
              f"clicked={watcher.clicked!r} titles={watcher.titles} "
              f"forced={watcher.forced}")
        version, declined = win._driver_declined()
        check("2b. 「以后再说」记下「版本 + 拒过的键」（driver_setup=版本|键）",
              version == D.PAYLOAD_VERSION and declined == {"cp210x"},
              f"version={version!r} declined={declined!r}")
        check("2c. 启动体检定时器只触发一次就自毁",
              len(ticks) == 1 and win._driver_gate is None,
              f"ticks={len(ticks)} gate={win._driver_gate!r}")
        watcher.stop()
        quiet(win)

        # --- 2d~2f：同一个新窗口里对比 force=False / force=True（已拒）/ 新芯片 ---
        watcher2 = BoxWatcher(title=BOX_TITLE, click=BTN_LATER, grace_ms=900)
        win2 = build_window("test2b")
        win2.show()
        pump(0.2)
        win2._maybe_install_drivers(force=False)    # 版本已问过 → 直接 return
        pump(0.3)
        check("2d. force=False 且这个版本已问过 → 不弹框",
              not watcher2.titles, f"titles={watcher2.titles}")

        win2._maybe_install_drivers(force=True)     # 但芯片正是拒过的那家 → 也不弹
        pump(0.5)
        check("2e. force=True 但芯片正是用户拒过的 → 不追着问",
              not watcher2.titles and not watcher2.clicked,
              f"titles={watcher2.titles} clicked={watcher2.clicked!r}")

        patches.set(D, "needed_keys", lambda: ["ftdi"])   # 换一家从没问过的
        win2._maybe_install_drivers(force=True)
        popped = wait_until(lambda: bool(watcher2.clicked), timeout=3.0)
        check("2f. force=True + 没问过的芯片 → 越过「版本问过」照问",
              popped and BOX_TITLE in watcher2.titles,
              f"titles={watcher2.titles} clicked={watcher2.clicked!r}")

        seen_before = len(watcher2.titles)
        watcher3 = BoxWatcher(title=BOX_TITLE, click="", grace_ms=600)
        win2._maybe_install_drivers(force=True)     # _driver_asked 命中 → 不再弹
        pump(0.8)
        check("2g. 同一窗口再 force=True 不再弹（_driver_asked 一会话一问）",
              len(watcher2.titles) == seen_before and not watcher3.titles
              and win2._driver_asked is True,
              f"titles={watcher2.titles} new={watcher3.titles} "
              f"asked={win2._driver_asked}")
        watcher2.stop()
        watcher3.stop()
        quiet(win2)

        # --- 2h：同一 QSettings 起新窗口，启动体检（force=False）一次都不问 ---
        patches.set(D, "needed_keys", lambda: ["pl2303"])   # 连拒过名单里都没有
        watcher4 = BoxWatcher(title=BOX_TITLE, click="", grace_ms=600)
        win3 = build_window("test2c")
        win3.show()
        pump(2.5)
        check("2h. 同一个 QSettings 再起一次：启动体检不问（哪怕芯片没拒过）",
              not watcher4.titles and watcher4.forced == 0,
              f"titles={watcher4.titles} forced={watcher4.forced}")
        watcher4.stop()
        quiet(win3)
    finally:
        patches.restore()


# ---------------------------------------------------------------------------
# 3 点「现在安装」走后台线程
# ---------------------------------------------------------------------------
def test_3_install_now() -> None:
    """3 点「现在安装」走后台线程，界面这期间不卡。"""
    print("[3] 点「现在安装」→ 后台线程安装，界面不卡")
    patches = Patches()
    try:
        clear_skip_env()
        fake = FakeInstall((0, "ok"), delay=1.0)   # 装 1 秒，够量出"界面没冻"
        patches.set(D, "install", fake)
        patches.set(D, "needed_keys", lambda: ["cp210x"])
        write_setup("")                          # 清掉旧记录，保证会问

        refreshes: list[bool] = []
        watcher = BoxWatcher(title=BOX_TITLE, click=BTN_NOW)
        win = build_window("test3")
        win._refresh_ports = (
            lambda announce=True, keep_device="": refreshes.append(bool(announce)))
        win.show()
        clicked = kick_gate(win, watcher)
        busy_after_click = win._driver_busy
        calls_after_click = list(fake.calls)

        ticks = [0]                                # 30ms 的"界面还活着"计数器
        meter = QTimer()
        meter.setInterval(30)
        meter.timeout.connect(lambda: ticks.__setitem__(0, ticks[0] + 1))
        meter.start()
        pump(0.6)                                  # 假安装还剩 0.4 秒左右
        meter.stop()
        finished = wait_until(lambda: win._driver_busy is False, timeout=4.0)
        pump(0.2)

        check("3a. 假 install 被调用一次，参数就是缺的那个键",
              clicked and calls_after_click == [["cp210x"]],
              f"clicked={watcher.clicked!r} calls={calls_after_click}")
        check("3a2. install 已被假函数顶替（真函数不可能被调到）",
              D.install is fake and MW.drv_assist.install is fake,
              f"D.install={D.install!r}")
        check("3b. 安装期间主线程仍在处理事件（30ms 计数 > 5）",
              ticks[0] > 5, f"ticks={ticks[0]}（安装中约 0.6 秒）")
        note(f"3b 实测：假安装在后台跑了 1.0s 里的 0.6s 内，主线程 30ms 计时器"
             f"计数 {ticks[0]}（≈20 是「完全没卡」的理论值）")
        check("3c. 后台装完回主线程：_driver_busy 复位 + 刷新端口 + toast 报结果",
              busy_after_click is True and finished
              and refreshes[:1] == [False] and "驱动已装好" in win.lbl_toast.text(),
              f"busy_after_click={busy_after_click} finished={finished} "
              f"refresh={refreshes} toast={win.lbl_toast.text()!r}")
        check("3d. 点「现在安装」不写 driver_setup（只有「以后再说」才记）",
              not read_setup(), repr(read_setup()))
        watcher.stop()
        quiet(win)
    finally:
        patches.restore()


# ---------------------------------------------------------------------------
# 9 _build_ui 重入：体检初始化不许被重建
# ---------------------------------------------------------------------------
def test_9_build_ui_reentry() -> None:
    """9 万一将来 `_build_ui()` 被再调一次（主题重建之类），体检不许重来一遍。

    窗口**不 show**：`_driver_gate_tick` 在不可见时早退，定时器就一直没被消费，
    这样"同一个定时器对象"才有意义（否则两边都是 None，断言是空的）。
    """
    print("[9] _build_ui 重入：体检定时器不重建、哨兵不被清")
    patches = Patches()
    try:
        clear_skip_env()
        patches.set(D, "needed_keys", list)
        win = build_window("test9")
        gate_before = win._driver_gate
        win._driver_asked = True                  # 模拟"本会话已经问过"
        win._build_ui()                           # 重复调用（实测安全，会重建控件）
        pump(0.2)
        check("9a. 重复 _build_ui 不重建体检定时器（还是同一个、仍 active）",
              gate_before is not None and win._driver_gate is gate_before
              and gate_before.isActive(),
              f"before={gate_before!r} after={win._driver_gate!r}")
        check("9b. 也不会把 _driver_asked 清回 False（否则同一会话会重复弹框）",
              win._driver_asked is True, str(win._driver_asked))
        quiet(win)
    finally:
        patches.restore()


# ---------------------------------------------------------------------------
# 8 拒绝记录语义（"以后再说"之后到底还问不问）
# ---------------------------------------------------------------------------
def test_8_declined_semantics() -> None:
    """8 拒绝记录语义：版本 + 拒过的键都参与判定（2f 那个缺陷的回归用例）。

    窗口**不 show**：启动体检定时器只在可见时才动手（``_driver_gate_tick`` 的
    早退分支），这样每次判定都是我们直接调方法触发的，不被 700ms 定时器插队。
    """
    print("[8] 拒绝记录语义（版本 + 拒过的键）")
    patches = Patches()
    try:
        clear_skip_env()
        win = build_window("test8")
        cases = (
            ("同一版本 + 正是拒过的芯片 → 不问",
             f"{D.PAYLOAD_VERSION}|cp210x", ["cp210x"], True, False),
            ("同一版本 + 多插一个没拒过的芯片 → 问",
             f"{D.PAYLOAD_VERSION}|cp210x", ["cp210x", "ch34x"], True, True),
            ("版本过期（换了驱动包）+ force=False → 重新问",
             "2025-01-01|cp210x", ["cp210x"], False, True),
        )
        results: list[str] = []
        for label, record, need, force, expect in cases:
            write_setup(record)
            patches.set(D, "needed_keys", lambda keys=need: list(keys))
            win._driver_asked = False              # 模拟"本会话还没问过"
            watcher = BoxWatcher(title=BOX_TITLE, click=BTN_LATER, grace_ms=900)
            win._maybe_install_drivers(force=force)     # 要弹框时内部 exec 会阻塞
            pump(0.4)
            got = BOX_TITLE in watcher.titles
            results.append(f"{label}: expect={expect} got={got}")
            check(f"8. {label}", got == expect,
                  f"expect={expect} got={got} titles={watcher.titles}")
            watcher.stop()

        # 老格式（只有版本号、没有竖线）也得能解析，拒过的键算空集
        write_setup(D.PAYLOAD_VERSION)
        version, declined = win._driver_declined()
        check("8d. 老格式「纯版本号」解析成 (版本, 空集)",
              version == D.PAYLOAD_VERSION and declined == set(),
              f"version={version!r} declined={declined!r}")
        note("拒绝记录判定三连：" + " | ".join(results))
        quiet(win)
    finally:
        patches.restore()


# ---------------------------------------------------------------------------
# 7 _poll_ports 的节拍
# ---------------------------------------------------------------------------
def test_7_poll_cadence() -> None:
    """7 每 4 拍才做一次 force 体检（不是每次都做）。"""
    print("[7] _poll_ports 节拍：每 4 拍体检一次")
    patches = Patches()
    try:
        patches.set(D, "needed_keys", lambda: ["cp210x"])
        calls: list[bool] = []
        win = build_window("test7")
        win._maybe_install_drivers = lambda force=False: calls.append(force)
        win.show()
        pump(1.2)                       # 让启动体检那一拍过去，再清零计数
        calls.clear()

        win._driver_ticks = 3           # 4 % 4 == 0 → 下一拍就该体检
        win._poll_ports()
        first = len(calls)
        win._driver_ticks = 2           # 3 % 4 != 0 → 还要再走一拍
        win._poll_ports()
        second = len(calls)
        win._poll_ports()               # 这一拍到 4 了
        third = len(calls)
        check("7a. _driver_ticks=3 时调一次 _poll_ports 就触发一次 force 体检",
              first == 1, f"first={first}")
        check("7b. _driver_ticks=2 时要调两次才触发（3%4≠0，4%4==0）",
              second == 1 and third == 2, f"second={second} third={third}")
        quiet(win)
    finally:
        patches.restore()


# ---------------------------------------------------------------------------
def describe_impl() -> None:
    """把被测实现的"长相"打出来，报告里好对照是哪一版。"""
    path = os.path.join(ROOT, "main_window.py")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S",
                          time.localtime(os.path.getmtime(path)))
    print(f"被测 main_window.py：mtime {stamp}")
    for name in ("_maybe_install_drivers", "_driver_gate_tick",
                 "_on_driver_install_done"):
        try:
            sig = inspect.signature(getattr(MW.MainWindow, name))
        except AttributeError:
            sig = "<缺失>"
        print(f"  {name}{sig}")


def main() -> int:
    print("=" * 72)
    print("驱动体检界面级验证（只读：假 install + 绊索，绝不真的装驱动）")
    print("=" * 72)
    describe_impl()

    snap = snapshot()
    old_skip_env = os.environ.pop(SKIP_ENV, None)      # 弹框用例的前提
    if old_skip_env is not None:
        note(f"外部设了 {SKIP_ENV}={old_skip_env!r}，本次运行期间先摘掉（收尾复原）")
    patch = Patches()
    trip = install_tripwire(patch)
    patch.set(updater, "UpdateChecker", NoopChecker)   # 更新检查混不进观测
    windows: list[object] = []
    try:
        test_s_surface()
        test_0_splash_guard()
        before = snapshot()          # 从这里起只看窗口 1 自己的写设置行为
        win = test_1_quiet(patch)
        windows.append(win)
        test_4_cancel(win)
        test_5_open_failed(win)
        delta = settings_delta(before, snapshot())
        if delta:
            note(f"窗口 1 期间 QSettings 变化 {len(delta)} 项（构造期迁移/存档，"
                 f"与本功能无关）：" + "; ".join(delta[:6]))
        quiet(win)          # 先让它闭嘴：端口轮询会按 needed_keys 再去体检，会串台
        test_2_dedup()
        test_3_install_now()
        test_8_declined_semantics()
        test_9_build_ui_reentry()
        test_7_poll_cadence()
        check("6b. 全程没有任何真实 pnputil /add-driver 发生",
              trip["add_driver"] == [], str(trip["add_driver"]))
    finally:
        for win in windows:
            try:
                quiet(win)
            except RuntimeError:
                pass
        patch.restore()
        if old_skip_env is not None:
            os.environ[SKIP_ENV] = old_skip_env
        restore_settings(snap)
        # 还原之后再空跑半秒：还活着的窗口可能把延迟动作（singleShot/定时器尾巴）
        # 落成一次设置写入。发现就再还原一次，并留一条 NOTE（不是产品缺陷，
        # 但"脚本跑完设置必须没变"这条铁律得守住）。
        pump(0.5)
        late = settings_delta(snap, snapshot())
        if late:
            note(f"收尾空转期间窗口又写了 {len(late)} 项，已二次还原："
                 + "; ".join(late[:4]))
            restore_settings(snap)
        after = snapshot()
        diff = settings_delta(snap, after)
        if diff:
            print("  设置差异（还原后仍不一致）：")
            for line in diff:
                print("    " + line)
        check("6a. QSettings 逐键还原：收尾快照与开头完全一致",
              not diff, f"{len(diff)} 项差异")

    print("-" * 72)
    total = PASS + len(FAIL)
    if FAIL:
        print(f"FAILED {len(FAIL)}/{total}")
        for item in FAIL:
            print("  × " + item)
        return 1
    print(f"IS_PASS {PASS}/{total}")
    return 0


if __name__ == "__main__":
    rc = main()
    # ⚠️ 判定只看上面那行 IS_PASS；退出码偶尔会是 127（MainWindow 收尾时 C++
    #    对象已析构，项目已知现象），所以这里不做任何额外收尾善后。
    raise SystemExit(rc)
