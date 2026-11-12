"""USB 转串口驱动自检与安装（STLink / CH340 / CP210x / FTDI / PL2303）。

## 为什么要有这个模块

用户（方程式车队）拿到的是一个 exe，插上 STLink 或九块九的 CH340 模块就想用。
但 Windows 10/11 **不是**插上就有串口：这五家芯片的驱动都不在系统内置驱动库里，
新机器插上去只会多一个「未知设备」，串口下拉框里空空如也 —— 用户的第一反应是
"软件坏了"。赛场更糟：没有网络，跑到官网下驱动的路子直接断掉。所以驱动必须
**随产品分发、离线可装**。

## 三件事，分得很清楚

1. **体检**（只读）
   - ``driver_store_infos()``：``pnputil /enum-drivers`` 解析出驱动库里已有的 inf
     名 —— 用来判断"这台机器装过了没有"，装过就不打扰。
   - ``problem_devices()``：CFGMGR32 枚举**当前在位**但带问题代码的设备，取
     ``USB\\VID_xxxx&PID_xxxx`` 形式的实例 ID。⚠️ 不能只查 Ports 设备类 ——
     没装驱动的设备会被 Windows 归到「其他设备」，那个类里根本没有它。
   - ``needed_keys()``：上面两者交叉，得到"插着、但我们有对应驱动、且还没装"的键。

2. **安装**（要管理员）
   ``install()`` 以 ``runas`` 拉起**我们自己**（``--install-drivers``），子进程里
   逐个 ``pnputil /add-driver <inf> /install``。为什么不用 .bat/.ps1：
   - 安装目录常在 ``%LOCALAPPDATA%``，中文用户名会让脚本编码翻车；
   - 一次 UAC 提权就能把所有驱动灌完，比每个 inf 弹一次强得多；
   - 子进程是打包后的 exe 自己，不需要额外分发任何可执行文件。
   父进程拿 ``ShellExecuteW`` 的进程句柄等它结束、取退出码（0 全成 / 1 有失败 /
   5 用户点了"否"）。

3. **说话**（给界面用）
   ``describe()`` / ``hint()`` 生成人话，界面层不碰任何 Windows API。

## 驱动包放哪

``payload_dir()`` 按序找：exe 同级 ``drivers/``（安装包释放的位置，也允许用户自己
换新）→ ``sys._MEIPASS/drivers``（onefile 解包，免安装 exe 走这条）→ 源码目录
``drivers/``。每家的子目录里是 ``.inf`` + 它引用的 ``.sys`` + ``.cat`` 平铺，
外加一份 ``SOURCE.txt`` 记录版本与签名主体（见 ``drivers/README.md``）。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import uuid
from ctypes import wintypes

# 驱动包版本标记。改了 drivers/ 里的文件、或改了下面的 VID 表，就把这个日期改掉：
# 用户下次启动会重新体检一次（老机器装过就不再问，见 main_window._driver_gate）。
PAYLOAD_VERSION = "2026-11-12"

# 每家芯片一条。``infs`` 可以有多条（FTDI 的端口驱动依赖总线驱动，两个都要进库）；
# ``vids`` 只按 VID 匹配 —— PID 不写死，同一家的新批次 PID 层出不穷，按 VID 收网
# 反而更全，而且装的是整个驱动包，多认几个 PID 不会有副作用。
PAYLOADS: tuple[dict, ...] = (
    {
        "key": "stlink",
        "label": "STLink 虚拟串口",
        "vendor": "STMicroelectronics",
        "vids": ("0483",),
        "infs": ("stlink_vcp.inf",),
    },
    {
        "key": "ch34x",
        "label": "CH340 / CH341",
        "vendor": "WCH",
        "vids": ("1A86",),
        "infs": ("ch341ser.inf",),
    },
    {
        "key": "cp210x",
        "label": "CP2102 / CP2104",
        "vendor": "Silicon Labs",
        "vids": ("10C4",),
        "infs": ("silabser.inf",),
    },
    {
        "key": "ftdi",
        "label": "FT232 / FT2232",
        "vendor": "FTDI",
        "vids": ("0403",),
        "infs": ("ftdibus.inf", "ftdiport.inf"),
    },
    {
        "key": "pl2303",
        "label": "PL2303",
        "vendor": "Prolific",
        "vids": ("067B",),
        "infs": ("ser2pl.inf",),
    },
)

# ---- CFGMGR32 / SetupAPI 常量（只用得到这几个，别去引 pywin32）----
_CR_SUCCESS = 0
_DN_HAS_PROBLEM = 0x00000400
_DIGCF_PRESENT = 0x2
_MAX_DEVICE_ID_LEN = 200
# 24 = CM_PROB_NO_DRIVER，28 = CM_PROB_FAILED_INSTALL。只有这两种是"缺驱动/装失败"，
# 别把 22（被禁用）、43（设备报错）也算进来 —— 那些装驱动也不解决。
_PROBLEM_NO_DRIVER = (24, 28)

_CREATE_NO_WINDOW = 0x08000000
_SE_ERR_ACCESSDENIED = 5
_WAIT_TIMEOUT = 0x00000102          # WaitForSingleObject 的超时常量


# ---------------------------------------------------------------------------
# 驱动包定位
# ---------------------------------------------------------------------------
def payload_dir() -> str:
    """返回驱动包根目录（含各家子目录）；找不到返回空串。

    顺序：exe 同级 → onefile 解包目录 → 源码目录。exe 同级放前面是故意的：
    安装包把它释放到 ``{app}\\drivers``，用户也可以自己往里丢更新的版本覆盖。
    """
    roots: list[str] = []
    if getattr(sys, "frozen", False):
        roots.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                                  "drivers"))
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            roots.append(os.path.join(meipass, "drivers"))
    else:
        roots.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "drivers"))
    for root in roots:
        if not os.path.isdir(root):
            continue
        for entry in PAYLOADS:
            if _payload_files(root, entry["key"]):
                return root
    return ""


def _payload_files(root: str, key: str) -> list[str]:
    """某家驱动包里**实际存在**的 inf 绝对路径（按 PAYLOADS 里声明的顺序）。

    名字大小写不敏感：WCH 的包在不同版本里叫 ``CH341SER.INF`` 或 ``ch341ser.inf``，
    ``pnputil`` 自己不挑，我们也不该挑。
    """
    folder = os.path.join(root, key)
    if not os.path.isdir(folder):
        return []
    have = {name.lower(): name for name in os.listdir(folder)}
    out: list[str] = []
    for wanted in _entry(key).get("infs", ()):
        real = have.get(wanted.lower())
        if real:
            out.append(os.path.join(folder, real))
    return out


def _entry(key: str) -> dict:
    for entry in PAYLOADS:
        if entry["key"] == key:
            return entry
    return {}


# ---------------------------------------------------------------------------
# 体检（全只读）
# ---------------------------------------------------------------------------
def _run(args: list[str], timeout: float = 20.0) -> tuple[int, str]:
    """跑一个控制台命令并收回输出（不弹黑框）。失败一律当"没结果"，不抛。"""
    try:
        done = subprocess.run(
            args, capture_output=True, text=True, errors="replace",
            timeout=timeout, creationflags=_CREATE_NO_WINDOW, check=False)
    except (OSError, subprocess.SubprocessError):
        return -1, ""
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def driver_store_infos() -> set[str]:
    """驱动库里**已有**的 inf 原始文件名（小写）。拿不到就返回空集。

    解析方式故意宽松：``pnputil /enum-drivers`` 的中文/英文输出的字段名不一样
    （"原始名称"/"Original Name"），但**每一段都必然有一行以 .inf 结尾**。
    与其赌字段名，不如把所有 ``xxx.inf`` 词元全收进来 —— 里面既有 ``oem12.inf``
    这种发布名，也有原始名，我们要查的原始名必在其中。
    """
    code, text = _run(["pnputil", "/enum-drivers"])
    if code != 0 or not text:
        return set()
    found: set[str] = set()
    for token in text.replace("\r", " ").replace("\n", " ").split():
        low = token.strip().lower()
        if low.endswith(".inf") and len(low) > 4:
            found.add(os.path.basename(low))
    return found


def shipped_keys() -> list[str]:
    """驱动包里**实际带了**的键（缺包的那些不列，顺序跟 PAYLOADS 一致）。

    对外是给能力接口用的（``capability_api.driver_status``）：Agent 想知道"这个
    产物到底能装哪几家"，比逐个目录去翻 inf 方便。
    """
    root = payload_dir()
    if not root:
        return []
    return [entry["key"] for entry in PAYLOADS
            if _payload_files(root, entry["key"])]


def missing_keys() -> list[str]:
    """驱动包里**这台机器还没装**的键（顺序跟 PAYLOADS 一致）。"""
    root = payload_dir()
    if not root:
        return []
    have = driver_store_infos()
    out: list[str] = []
    for entry in PAYLOADS:
        infs = _payload_files(root, entry["key"])
        if not infs:
            continue                                  # 没带这一家的包，别管
        names = [os.path.basename(p).lower() for p in infs]
        if not all(name in have for name in names):
            out.append(entry["key"])
    return out


def _guid(text: str):
    u = uuid.UUID(text)
    return _GUID(u.time_low, u.time_mid, u.time_hi_version,
                 (ctypes.c_ubyte * 8)(*u.bytes[8:]))


def problem_devices() -> list[dict]:
    """当前在位、且问题代码是"没驱动/装失败"的 USB 设备。

    返回 ``[{"instance": ..., "desc": ..., "vid": ...}]``。必须同时查「其他设备」
    （``{4D36E97E-...}``）类：没装驱动的设备被 Windows 归到那里，只查 Ports 类会
    一个都看不见 —— 这条踩过。

    ⚠️ **不要**给 ``SetupDiGetClassDevs`` 传 ``DIGCF_ALLCLASSES``：它和类 GUID 一起
    用会变成"枚举全系统所有设备"，实测这一下要 1.4 秒，而挂在端口轮询上的体检是
    每 4 秒一次的 —— 等于白烧 35% 的一个核。只查这三个类 + 只读状态位，实测 4ms。
    """
    if os.name != "nt":
        return []
    devs = _enum_present("{4D36E97E-E325-11CE-BFC1-08002BE10318}")  # Unknown
    devs += _enum_present("{36FC9E60-C465-11CF-8056-444553540000}")  # USB
    devs += _enum_present("{4D36E978-E325-11CE-BFC1-08002BE10318}")  # Ports
    seen: set[str] = set()
    out: list[dict] = []
    for dev in devs:
        instance = dev["instance"]
        if not instance.upper().startswith("USB\\VID_") or instance in seen:
            continue
        if not dev["has_problem"] or dev["problem"] not in _PROBLEM_NO_DRIVER:
            continue
        seen.add(instance)
        out.append({
            "instance": instance,
            "vid": instance[8:12].upper(),      # "USB\VID_" 正好 8 个字符
        })
    return out


def needed_keys() -> list[str]:
    """插着、我们有驱动、但驱动库还没装上的键（去重，按 PAYLOADS 顺序）。

    **只在真的插了设备时才说话**：没插东西的机器上 ``problem_devices()`` 是空的，
    函数在进 ``pnputil`` 之前就返回了（~4ms ctypes 枚举）。这条很重要 —— 启动时
    的体检就是靠它做到"没插设备就不打扰用户"。

    ⚠️ 一个设备带问题代码（24/28）但它的 inf 已经在驱动库里时，这里**不算缺驱动**：
    那种情况重装同一个包也不会好（典型的比如供电不足、设备本身坏了），让用户去点
    「安装驱动」只会白等一次 UAC。
    """
    vids = {dev["vid"] for dev in problem_devices()}
    if not vids:
        return []
    return [key for key in missing_keys()
            if any(vid in vids for vid in _entry(key).get("vids", ()))]


# ---- SetupAPI 枚举（只读，句柄必关）----
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", _GUID),
                ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


def _api():
    """惰性拿 setupapi/cfgmgr32 函数表（Windows-only，非 Windows 返回 None）。"""
    if os.name != "nt":
        return None
    if getattr(_api, "_cache", None) is None:
        setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
        cfg = ctypes.WinDLL("cfgmgr32", use_last_error=True)
        setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
        setupapi.SetupDiGetClassDevsW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, wintypes.DWORD]
        setupapi.SetupDiEnumDeviceInfo.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
        cfg.CM_Get_DevNode_Status.argtypes = [
            ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(wintypes.ULONG),
            wintypes.DWORD, wintypes.ULONG]
        cfg.CM_Get_Device_IDW.argtypes = [
            wintypes.DWORD, ctypes.c_wchar_p, wintypes.ULONG, wintypes.ULONG]
        _api._cache = (setupapi, cfg)      # type: ignore[attr-defined]
    return _api._cache                          # type: ignore[attr-defined]


def _enum_present(class_guid: str) -> list[dict]:
    api = _api()
    if api is None:
        return []
    setupapi, cfg = api
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(_guid(class_guid)), None, None, _DIGCF_PRESENT)
    if not hdev or hdev == ctypes.c_void_p(-1).value:
        return []
    out: list[dict] = []
    try:
        index = 0
        while True:
            did = _SP_DEVINFO_DATA()
            did.cbSize = ctypes.sizeof(_SP_DEVINFO_DATA)
            if not setupapi.SetupDiEnumDeviceInfo(
                    ctypes.c_void_p(hdev), index, ctypes.byref(did)):
                break
            index += 1
            status = wintypes.ULONG(0)
            problem = wintypes.ULONG(0)
            ret = cfg.CM_Get_DevNode_Status(
                ctypes.byref(status), ctypes.byref(problem), did.DevInst, 0)
            buf = ctypes.create_unicode_buffer(_MAX_DEVICE_ID_LEN)
            instance = ""
            if cfg.CM_Get_Device_IDW(did.DevInst, buf, _MAX_DEVICE_ID_LEN,
                                     0) == _CR_SUCCESS:
                instance = buf.value
            out.append({
                "instance": instance,
                "problem": problem.value if ret == _CR_SUCCESS else 0,
                "has_problem": bool(status.value & _DN_HAS_PROBLEM),
            })
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(hdev))
    return out


# ---------------------------------------------------------------------------
# 安装（提权到自身）
# ---------------------------------------------------------------------------
def _elevated_argv(root: str, keys: list[str]) -> list[str]:
    """构造"提权后要跑的命令行"。frozen 就是 exe 自己；源码态借 python 跑 main.py。"""
    tail = ["--install-drivers", root] + list(keys)
    if getattr(sys, "frozen", False):
        return [sys.executable] + tail
    script = os.path.abspath(sys.argv[0] or "main.py")
    return [sys.executable, script] + tail


def install(keys: list[str], root: str = "") -> tuple[int, str]:
    """把这些键的驱动装进驱动库（会弹一次 UAC）。

    返回 ``(退出码, 人话)``。退出码：0 全成，1 有失败，5 用户拒绝了授权，
    -1 环境问题（找不到驱动包/拉起失败）。
    """
    root = root or payload_dir()
    if not root:
        return -1, "找不到随程序附带的驱动包（drivers 目录缺失）"
    keys = [k for k in keys if _payload_files(root, k)]
    if not keys:
        return -1, "没有可安装的驱动"
    argv = _elevated_argv(root, keys)

    class _SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND), ("lpVerb", ctypes.c_wchar_p),
            ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
            ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
            ("lpClass", ctypes.c_wchar_p), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    # ⛔ 句柄是 64 位。不声明 argtypes 的话 ctypes 按 C int（32 位）传参，
    #    高 32 位会被截掉 —— WaitForSingleObject 等到的是个野句柄，表现为
    #    "立刻返回 WAIT_FAILED"、退出码读不出来（父进程误报"没装上"）。
    #    ⚠️ 这条在 64 位句柄恰好是小数值时才碰不到，测试机上很难偶发到。
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    # ⚠️ ShellExecuteW 拿不到退出码，必须用 SHELLEXECUTEINFOW + SEE_MASK_NOCLOSEPROCESS
    #    才能真正等到子进程结束 —— 否则"装完了"是假的，刷新端口时驱动还没进库。
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    info.fMask = 0x00000040                      # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = argv[0]
    info.lpParameters = subprocess.list2cmdline(argv[1:])
    info.nShow = 1                               # SW_SHOWNORMAL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == _SE_ERR_ACCESSDENIED:
            return 5, "已取消管理员授权，驱动没有安装"
        return -1, f"拉起安装进程失败（错误码 {err}）"
    code = -1
    if info.hProcess:
        # ⚠️ 不是"等很久"的问题：UAC 那一下必须用户亲手点，等不到就只能超时。
        #    2 分钟足够点一下授权；再久用户会以为软件死了（实测 2026-11-12 有一次
        #    点开安装后界面毫无反应，日志里连结果都没落）。
        wait = kernel32.WaitForSingleObject(info.hProcess, 120000)
        exit_code = wintypes.DWORD(0)
        if wait == _WAIT_TIMEOUT:
            kernel32.CloseHandle(info.hProcess)
            return 1, "等授权超时（2 分钟），驱动没有安装。可以再点一次"
        if kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
            code = int(exit_code.value)
        kernel32.CloseHandle(info.hProcess)
    if code == 0:
        return 0, "驱动已装好"
    return (code if code > 0 else 1), "部分驱动没装上，可以稍后重试"


def _pnputil_ok(code: int, output: str) -> bool:
    """``pnputil /add-driver`` 到底算不算成功。

    ⛔ 退出码**不等于**成败。实测（2026-11-12 15:15，本机日志）：
        pnputil /add-driver ch341ser.inf /install  →  **退出码 5**
        输出却是「已成功添加驱动程序包。(系统中已存在) | 已添加驱动程序包数: 0」
    只看退出码会把"本来就已经装好了"报成"安装失败"，用户以为驱动没进去。

    规则：输出里有明确的失败字样就以输出为准（判失败）；否则输出里有成功/已存在
    的字样就算成功；两者都没有才退回退出码。
    """
    text = (output or "").lower()
    for word in ("失败", "错误", "error", "failed", "invalid"):
        if word in text:
            return code == 0
    for word in ("已成功", "已添加", "已存在", "success", "already exists", "installed"):
        if word in text:
            return True
    return code == 0


def run_cli(argv: list[str]) -> int:
    """``--install-drivers <驱动包根目录> <键>...`` 的落地实现（**提权后**才走到）。

    只做一件事：把每个 inf 交给 ``pnputil /add-driver ... /install``。不加
    ``/force`` —— 那会装未签名的包，安全策略不接受。全部结果写进日志，退出码
    0 = 全成，1 = 有失败。

    ⛔ ``attempted == 0`` 必须报失败：早先版本按"几个 inf 失败"计数，一个 inf 都
    没找到时 ``failed`` 也是 0，于是**装了个空气却回报成功**（2026-11-12 15:15
    就出现过：父进程以为装好了，驱动库里根本没有 silabser）。
    """
    import app_log

    app_log.init()
    rest = [a for a in argv[1:] if not a.startswith("--")]
    if not rest:
        app_log.error("--install-drivers 缺少驱动包目录")
        return 1
    root = rest[0]
    wanted = rest[1:]
    keys = [e["key"] for e in PAYLOADS if e["key"] in wanted] if wanted else \
        [e["key"] for e in PAYLOADS]
    attempted = 0
    failed = 0
    for key in keys:
        for inf in _payload_files(root, key):
            attempted += 1
            code, output = _run(["pnputil", "/add-driver", inf, "/install"],
                                timeout=90.0)
            tail = " | ".join(line.strip() for line in output.splitlines()
                              if line.strip())[:300]
            ok = _pnputil_ok(code, output)
            app_log.info(f"装驱动 {key} {os.path.basename(inf)}："
                         f"退出码 {code} {'' if ok else '(判失败) '}{tail}")
            if not ok:
                failed += 1
    if attempted == 0:
        app_log.error(f"--install-drivers 没找到可装的 inf"
                      f"（root={root} keys={keys}）")
        return 1
    app_log.info(f"驱动安装结束：{len(keys)} 家 {attempted} 个 inf，失败 {failed} 个")
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# 人话
# ---------------------------------------------------------------------------
def describe(keys: list[str]) -> str:
    """``["ch34x", "cp210x"]`` → ``"CH340 / CH341、CP2102 / CP2104"``。"""
    labels = [e["label"] for e in PAYLOADS if e["key"] in keys]
    return "、".join(labels)
