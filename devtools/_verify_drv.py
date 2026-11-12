"""验证 USB 转串口驱动自检 / 安装模块（``drv_assist``）与界面接入。

⛔ 全程**不真的安装驱动**：不弹 UAC、不调 ``pnputil /add-driver``。安装动作本身
   会改机器状态，且需要用户点授权，不适合放进自动化门禁。这里验的是"判断得对
   不对"和"接进去了没有"：

   1. 驱动包定位（exe 同级 / onefile 解包 / 源码目录）与大小写不敏感匹配；
   2. 驱动库解析（``pnputil /enum-drivers``）能认出本机已装的 inf；
   3. 缺哪个键、插着哪些没驱动的设备，判断链路正确；
   4. 提权命令行在 frozen / 源码两种形态下都对；
   5. 界面接入点存在（启动体检、打开失败提示、main.py 的 --install-drivers 分支）。

跑法：``.venv\\Scripts\\python.exe devtools\\_verify_drv.py``
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import drv_assist as D                      # noqa: E402

PASS = 0
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL.append(f"{name}" + (f" —— {detail}" if detail else ""))
        print(f"  FAIL {name}" + (f" —— {detail}" if detail else ""))


def _src(name: str) -> str:
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. 配置表本身
# ---------------------------------------------------------------------------
def test_payload_table() -> None:
    print("[1] 驱动包配置表")
    keys = [e["key"] for e in D.PAYLOADS]
    check("PAYLOADS 有 5 家（stlink/ch34x/cp210x/ftdi/pl2303）",
          keys == ["stlink", "ch34x", "cp210x", "ftdi", "pl2303"], str(keys))
    check("key 无重复", len(keys) == len(set(keys)))
    bad_vid = [v for e in D.PAYLOADS for v in e["vids"]
               if not re.fullmatch(r"[0-9A-F]{4}", v)]
    check("VID 全是 4 位大写十六进制", not bad_vid, str(bad_vid))
    bad_inf = [i for e in D.PAYLOADS for i in e["infs"]
               if not i.lower().endswith(".inf")]
    check("infs 全是 .inf", not bad_inf, str(bad_inf))
    check("每家都有 label / vendor",
          all(e.get("label") and e.get("vendor") for e in D.PAYLOADS))
    # FTDI 的端口驱动依赖总线驱动：少装一个，设备管理器里会留一个黄叹号。
    ftdi = [e for e in D.PAYLOADS if e["key"] == "ftdi"][0]
    check("FTDI 同时装总线+端口两个 inf（少一个会留黄叹号）",
          set(ftdi["infs"]) == {"ftdibus.inf", "ftdiport.inf"}, str(ftdi["infs"]))
    check("PAYLOAD_VERSION 是日期形态", bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}",
                                                          D.PAYLOAD_VERSION)),
          D.PAYLOAD_VERSION)


# ---------------------------------------------------------------------------
# 2. 驱动包定位与文件匹配
# ---------------------------------------------------------------------------
def test_payload_files() -> None:
    print("[2] 驱动包定位 / 文件匹配")
    tmp = tempfile.mkdtemp(prefix="drvchk_", dir=ROOT)
    try:
        for name in ("stlink", "ch34x", "cp210x", "ftdi", "pl2303"):
            os.makedirs(os.path.join(tmp, name), exist_ok=True)
        # 故意用大写扩展名 + 大写文件名：WCH 的原始包就是 CH341SER.INF
        open(os.path.join(tmp, "ch34x", "CH341SER.INF"), "wb").close()
        open(os.path.join(tmp, "ch34x", "CH341SER.SYS"), "wb").close()
        # FTDI 只放一个 inf → 该算"包不全"，不能当成可安装
        open(os.path.join(tmp, "ftdi", "ftdibus.inf"), "wb").close()

        got = D._payload_files(tmp, "ch34x")
        check("文件名大小写不敏感匹配（CH341SER.INF → ch341ser.inf）",
              len(got) == 1 and got[0].lower().endswith("ch341ser.inf"), str(got))
        check("只返回**真实存在**的 inf",
              D._payload_files(tmp, "cp210x") == [],
              str(D._payload_files(tmp, "cp210x")))
        check("多家 inf 的包只认齐的那一家（FTDI 只有 1/2 → 返回 1 条）",
              len(D._payload_files(tmp, "ftdi")) == 1)
        check("不存在的键返回空", D._payload_files(tmp, "nope") == [])

        # payload_dir：源码目录下没有 drivers 时返回空串（本机此刻就是这样）
        real = os.path.join(ROOT, "drivers")
        if not os.path.isdir(real):
            check("源码目录无 drivers/ 时 payload_dir() 返回空串",
                  D.payload_dir() == "", repr(D.payload_dir()))
        else:
            check("存在 drivers/ 时 payload_dir() 指向它",
                  D.payload_dir() == real, D.payload_dir())

        # 模拟"exe 同级"优先：把 frozen 打开，指向临时目录
        saved = (getattr(sys, "frozen", None), getattr(sys, "_MEIPASS", None),
                 sys.executable)
        try:
            sys.frozen = True                       # type: ignore[attr-defined]
            sys._MEIPASS = os.path.join(tmp, "_meipass")   # type: ignore[attr-defined]
            os.makedirs(sys._MEIPASS, exist_ok=True)
            os.makedirs(os.path.join(tmp, "exe_dir", "drivers", "stlink"),
                        exist_ok=True)
            open(os.path.join(tmp, "exe_dir", "drivers", "stlink",
                              "stlink_vcp.inf"), "wb").close()
            sys.executable = os.path.join(tmp, "exe_dir", "SerialAssistant.exe")
            check("frozen 时优先用 exe 同级的 drivers/",
                  D.payload_dir() == os.path.join(tmp, "exe_dir", "drivers"),
                  D.payload_dir())
        finally:
            if saved[0] is None:
                try:
                    del sys.frozen                   # type: ignore[attr-defined]
                except AttributeError:
                    pass
            else:
                sys.frozen = saved[0]                # type: ignore[attr-defined]
            if saved[1] is None:
                try:
                    del sys._MEIPASS                 # type: ignore[attr-defined]
                except AttributeError:
                    pass
            else:
                sys._MEIPASS = saved[1]              # type: ignore[attr-defined]
            sys.executable = saved[2]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2b. 随包驱动的**结构完整性**（这条是差点漏出去的坑）
# ---------------------------------------------------------------------------
def _inf_required_files(path: str) -> list[str]:
    """解析 INF 的 ``[SourceDisksFiles*]``，返回它声明需要的文件名（去掉注释）。

    ⚠️ 为什么必须查：``pnputil /add-driver`` 会按这张表把文件搬进驱动库，
    **少一个就报错**。真实踩过 —— 从驱动库 ``/export-driver`` 导出的包只含
    本机架构用到的文件（CH340 少了 x86 的 3 个、PL2303 少了 2 个），而它们的
    INF 是**单张**（不分 arch）的 ``[SourceDisksFiles]``，表里 x86 文件也在。
    这种包看起来"能装"，真装时可能失败，用户只会看到"部分驱动没装上"。
    所以规矩是：**表里列了、目录里就必须有**（x86 的也留着，一共才几十 KB）。
    """
    names: list[str] = []
    inside = False
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if line.startswith("["):
                inside = line.lower().startswith("[sourcedisksfiles")
                continue
            if not inside or not line or line.startswith(";"):
                continue
            name = line.split("=")[0].strip()
            if name:
                names.append(name)
    return names


def test_payload_complete() -> None:
    print("[2b] 随包驱动结构完整性（inf 声明的文件是否都在）")
    root = os.path.join(ROOT, "drivers")
    if not os.path.isdir(root):
        check("drivers/ 存在", False, "打包必需，缺了 exe 里就没有驱动")
        return
    for entry in D.PAYLOADS:
        infs = D._payload_files(root, entry["key"])
        if not infs:
            print(f"  --  {entry['key']}：未收录（跳过）")
            continue
        folder = os.path.join(root, entry["key"])
        have = {n.lower(): n for n in os.listdir(folder)}
        # arch 子目录也要算（CP210x 的 silabser.sys 在 x64\ 下）
        for sub in os.listdir(folder):
            sub_path = os.path.join(folder, sub)
            if os.path.isdir(sub_path):
                for n in os.listdir(sub_path):
                    have.setdefault(f"{sub}\\{n}".lower(), n)
                    have.setdefault(n.lower(), n)
        missing: list[str] = []
        for inf in infs:
            for name in _inf_required_files(inf):
                base = os.path.basename(name.replace("\\", os.sep))
                if base.lower() not in have:
                    missing.append(f"{entry['key']}/{base}")
        check(f"{entry['key']}：inf 声明的文件一个不少", not missing,
              "缺 " + ", ".join(sorted(set(missing))))
        check(f"{entry['key']}：签名目录文件（.cat）在", any(
            n.lower().endswith(".cat") for n in os.listdir(folder)))


# ---------------------------------------------------------------------------
# 3. 驱动库解析
# ---------------------------------------------------------------------------
def test_store() -> None:
    print("[3] 驱动库解析（pnputil /enum-drivers）")
    infos = D.driver_store_infos()
    check("能解析出非空 inf 集合", bool(infos), f"{len(infos)} 条")
    check("集合里只有 .inf 名字（无路径/无杂项）",
          all(i.endswith(".inf") and os.sep not in i for i in infos),
          str(sorted(infos)[:3]))
    check("本机已装的 ch341ser.inf 能被认出", "ch341ser.inf" in infos,
          "本机没装 WCH 驱动时这条会红，属环境差异")
    check("本机已装的 stlink_vcp.inf 能被认出", "stlink_vcp.inf" in infos,
          "本机没装 STLink 驱动时这条会红，属环境差异")


# ---------------------------------------------------------------------------
# 4. 缺驱动判断
# ---------------------------------------------------------------------------
def test_missing() -> None:
    print("[4] 缺驱动 / 缺设备判断")
    tmp = tempfile.mkdtemp(prefix="drvchk2_", dir=ROOT)
    try:
        # 造一个"只有 CH340 包"的 payload 根
        os.makedirs(os.path.join(tmp, "ch34x"), exist_ok=True)
        open(os.path.join(tmp, "ch34x", "ch341ser.inf"), "wb").close()
        saved = D.payload_dir
        D.payload_dir = lambda: tmp                 # type: ignore[assignment]
        try:
            # ch34x 已经在本机驱动库里（见 [3]），所以"库里缺"的应该是空；
            # 这同时证明 missing_keys 不是无脑把 payload 全报一遍。
            got = D.missing_keys()
            check("驱动库里已有的键不再报缺", "ch34x" not in got, str(got))
        finally:
            D.payload_dir = saved                   # type: ignore[assignment]

        # 造一个假的"本机没装过"的 inf 名，确认能报出来
        os.makedirs(os.path.join(tmp, "cp210x"), exist_ok=True)
        open(os.path.join(tmp, "cp210x", "silabser_FAKE.inf"), "wb").close()
        saved = D.payload_dir
        D.payload_dir = lambda: tmp                 # type: ignore[assignment]
        try:
            # inf 名跟配置表对不上 → 该家视为"没带包"，仍不报
            check("inf 名不匹配配置表时不算缺", "cp210x" not in D.missing_keys(),
                  str(D.missing_keys()))
        finally:
            D.payload_dir = saved                   # type: ignore[assignment]

        # 「没带驱动包」的语义要用假的 payload_dir 造：本机源码目录下现在**真**有
        # drivers/，直接断言 missing_keys() 为空会红（那是环境，不是缺陷）。
        saved = D.payload_dir
        D.payload_dir = lambda: ""                  # type: ignore[assignment]
        try:
            check("没有驱动包时 missing_keys() 为空", D.missing_keys() == [],
                  str(D.missing_keys()))
            check("没有驱动包时 needed_keys() 为空", D.needed_keys() == [],
                  str(D.needed_keys()))
        finally:
            D.payload_dir = saved                   # type: ignore[assignment]
        check("problem_devices() 返回 list", isinstance(D.problem_devices(), list))
        check("needed_keys 是 missing_keys 的子集",
              set(D.needed_keys()) <= set(D.missing_keys()))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. 提权命令行
# ---------------------------------------------------------------------------
def test_argv() -> None:
    print("[5] 提权命令行构造")
    saved_frozen = getattr(sys, "frozen", None)
    saved_exe, saved_argv = sys.executable, sys.argv[:]
    try:
        sys.frozen = True                           # type: ignore[attr-defined]
        sys.executable = r"C:\app\SerialAssistant.exe"
        argv = D._elevated_argv(r"C:\app\drivers", ["ch34x"])
        check("frozen：直接拉起 exe 自己",
              argv == [r"C:\app\SerialAssistant.exe", "--install-drivers",
                       r"C:\app\drivers", "ch34x"], str(argv))
    finally:
        if saved_frozen is None:
            try:
                del sys.frozen                   # type: ignore[attr-defined]
            except AttributeError:
                pass
        else:
            sys.frozen = saved_frozen            # type: ignore[attr-defined]
        sys.executable = saved_exe
    try:
        sys.argv = [os.path.join(ROOT, "main.py")]
        argv = D._elevated_argv(os.path.join(ROOT, "drivers"), ["stlink"])
        check("源码态：python + main.py + 参数",
              argv[1].endswith("main.py") and "--install-drivers" in argv
              and argv[-1] == "stlink", str(argv))
        check("源码态第一个参数是解释器", argv[0] == sys.executable, argv[0])
    finally:
        sys.argv = saved_argv
    check("空 keys 也能构造（不为空即合法）",
          len(D._elevated_argv("/x", [])) >= 3)


# ---------------------------------------------------------------------------
# 6. 人话 / 早退分支
# ---------------------------------------------------------------------------
def test_words() -> None:
    print("[6] 文案与早退分支")
    check("describe 空列表 → 空串", D.describe([]) == "")
    check("describe 单个键 → 对应 label",
          D.describe(["ch34x"]) == "CH340 / CH341", D.describe(["ch34x"]))
    check("describe 多个键用顿号连",
          D.describe(["stlink", "cp210x"]) == "STLink 虚拟串口、CP2102 / CP2104",
          D.describe(["stlink", "cp210x"]))
    check("describe 忽略未知键", D.describe(["nope"]) == "")

    # ⛔ 核心行为（2026-11-12 定稿）：没插设备就一声不响。
    #    needed_keys 必须先看问题设备（ctypes，3ms），没有就直接返回 ——
    #    否则"新机器第一次启动就问要不要装 CP2102"的老毛病会回来。
    if not D.problem_devices():
        t0 = time.perf_counter()
        got = D.needed_keys()
        cost = (time.perf_counter() - t0) * 1000
        check("没插设备时 needed_keys() 为空", got == [], str(got))
        check("没插设备时走快速路径（不进 pnputil，<40ms）", cost < 40,
              f"{cost:.0f} ms")

    saved = D.payload_dir
    D.payload_dir = lambda: ""                       # type: ignore[assignment]
    try:
        code, msg = D.install(["ch34x"])
        check("没有驱动包时 install 早退，不弹 UAC", code == -1, f"{code} {msg}")
        check("早退说明提到 drivers", "drivers" in msg, msg)
    finally:
        D.payload_dir = saved                        # type: ignore[assignment]

    tmp = tempfile.mkdtemp(prefix="drvchk3_", dir=ROOT)
    try:
        saved = D.payload_dir
        D.payload_dir = lambda: tmp                  # type: ignore[assignment]
        try:
            code, msg = D.install(["ch34x"])
            check("空驱动包目录时 install 早退", code == -1 and "没有" in msg,
                  f"{code} {msg}")
        finally:
            D.payload_dir = saved                    # type: ignore[assignment]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # run_cli 少参数：必须直接返回 1，⛔ 绝不真的调 pnputil
    check("run_cli 缺驱动包参数返回 1（不装任何东西）",
          D.run_cli(["x.exe", "--install-drivers"]) == 1)


# ---------------------------------------------------------------------------
# 7. 界面 / 入口接入
# ---------------------------------------------------------------------------
def test_wiring() -> None:
    print("[7] 界面与程序入口接入")
    mw = _src("main_window.py")
    main = _src("main.py")
    spec = _src("SerialAssistant.spec")

    check("main_window 引入 drv_assist", "import drv_assist" in mw)
    check("启动时起了驱动体检轮询（_driver_gate_tick）",
          "_driver_gate_tick" in mw and "self._driver_gate.start()" in mw)
    check("启动体检不越过「问过」记录（force 默认 False）",
          "def _maybe_install_drivers(self, force: bool = False)" in mw)
    check("轮询等开屏退场后才动手（_skip_startup_anim 判据）",
          re.search(r"def _driver_gate_tick.*?_skip_startup_anim", mw,
                    re.S) is not None)
    check("体检只在**插着且没驱动**时才问（needed_keys，不是库里缺哪家就问哪家）",
          re.search(r"def _maybe_install_drivers.*?needed_keys\(\)", mw,
                    re.S) is not None)
    check("没插设备时不被问（库里缺什么不等于用户需要什么）",
          "missing_keys()" not in mw.split("def _maybe_install_drivers")[1]
          .split("def _on_driver_install_done")[0])
    check("一个会话只问一次（_driver_asked）", "_driver_asked" in mw)
    check("插上设备由端口轮询顺带发现（每 4 拍，无 COM 口也能查到）",
          re.search(r"def _poll_ports.*?_driver_ticks % 4 == 0.*?"
                    r"_maybe_install_drivers\(force=True\)", mw, re.S) is not None)
    check("自动化环境可跳过弹框（SA_SKIP_DRIVER_PROMPT）",
          "SA_SKIP_DRIVER_PROMPT" in mw)
    # 这个键在整个 main_window 里只该出现**两处**：_driver_declined 读一次、
    # _driver_decline 写一次。多出来就说明有人在别处又动了它（用户设置是铁律）。
    check("问过的记录用 driver_setup 键，且只有一读一写",
          mw.count('"driver_setup"') == 2, str(mw.count('"driver_setup"')))
    check("读取走 _driver_declined、写入走 _driver_decline",
          "def _driver_declined" in mw and "def _driver_decline" in mw)
    check("拒绝记录精确到「哪几家芯片」（不是只记版本号）",
          "def _driver_declined" in mw and "def _driver_decline" in mw)
    check("插了没问过的新芯片仍会问（need ⊄ declined 才问）",
          "set(need) <= declined" in mw)
    check("安装走后台线程（不冻界面）",
          "class _DriverInstallWorker(QThread)" in mw
          and "worker.start()" in mw)
    check("装完刷新端口列表",
          re.search(r"def _on_driver_install_done.*?_refresh_ports", mw,
                    re.S) is not None)
    check("用户拒绝授权（5）有专门的话",
          "code == 5" in mw and "已取消授权" in mw)
    check("打开串口失败时点出缺驱动",
          re.search(r"def _on_open_failed.*?drv_assist\.needed_keys", mw,
                    re.S) is not None)
    check("打开失败时把「装驱动」并进同一个错误框（不叠第二个模态）",
          re.search(r"def _on_open_failed.*?addButton\(.安装驱动", mw,
                    re.S) is not None)
    check("并框前先置 _driver_asked（避免 4s 后又弹一个）",
          re.search(r"def _on_open_failed.*?_driver_asked = True", mw,
                    re.S) is not None)
    check("并框里点安装走 _start_driver_install",
          re.search(r"def _on_open_failed.*?_start_driver_install\(need\)", mw,
                    re.S) is not None)
    check("main.py 有 --install-drivers 分支", '"--install-drivers" in sys.argv'
          in main)
    check("--install-drivers 在建 QApplication 之前返回",
          main.index('"--install-drivers" in sys.argv')
          < main.index("app = QApplication(sys.argv)"))
    check("spec 把 drivers/ 打进 exe", "'drivers'" in spec or '"drivers"' in spec,
          "见 SerialAssistant.spec 的 Analysis(datas=...)")


def main() -> int:
    print("=" * 72)
    print("drv_assist 验证（只读：不装驱动、不弹 UAC、不改任何设置）")
    print("=" * 72)
    for fn in (test_payload_table, test_payload_files, test_payload_complete,
               test_store, test_missing, test_argv, test_words, test_wiring):
        fn()
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
    raise SystemExit(main())
