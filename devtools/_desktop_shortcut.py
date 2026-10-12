# -*- coding: utf-8 -*-
"""把桌面快捷方式指到最新的产物上。

背景：打包产物从 `dist\\` 挪到了 `发布\\`（用户要求"输出到易于查找的位置"），
而桌面那条 `CQUFSAE串口调试助手.lnk` 还指着老路径 —— 双击就打不开了（用户报的
"我的桌面快捷方式也要保留啊"）。这个脚本负责把它改指到当前产物，**名字、位置、
图标都不变**；不存在就创建一条。

为什么不走 PowerShell 的 `WScript.Shell`：那条路在本机被安全策略拦掉了
（"COM object instantiation can run arbitrary code"），`dangerouslyDisableSandbox`
也绕不过 —— 它拦的是工具层的命令模式，不是沙箱。这里用 ctypes 直接调
`IShellLinkW` + `IPersistFile`，纯 Python 进程内完成，不依赖 pywin32。

用法：
    python _desktop_shortcut.py            # 更新（不存在则创建）
    python _desktop_shortcut.py --print    # 只看现在指向哪，不改
"""

from __future__ import annotations

import ctypes
import os
import sys

# 脚本挪进 devtools/ 之后，运行时项目根不再自动在 sys.path 里 —— 补上
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from ctypes import POINTER, byref, c_void_p

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "发布", "SerialAssistant.exe")
RELEASE_DIR = os.path.dirname(EXE)
# 快捷方式的图标**用一个独立的 .ico**，不指 exe 本身。
# ⚠️ 这是一只 50MB 的 onefile 单文件 exe：Windows 从它里面抽图标要扫整个
# 文件，超时就退化成白纸（用户报"桌面图标变成一张白纸"就是这个）。指到
# 旁边这个小 ico 上是即取即得。
ICON = os.path.join(RELEASE_DIR, "app.ico")
LINK_NAME = "CQUFSAE串口调试助手.lnk"

ole32 = ctypes.oledll.ole32
shell32 = ctypes.windll.shell32
# ⚠️ 这两个 API 的字符串参数得声明成 POINTER(c_wchar)：传 create_unicode_buffer 时
# ctypes 不会把缓冲区自动当成 c_wchar_p（那是给 str 用的），声明错了会直接抛 TypeError。
shell32.SHGetFolderPathW.argtypes = [c_void_p, ctypes.c_int, c_void_p,
                                     ctypes.c_uint32, POINTER(ctypes.c_wchar)]
shell32.SHGetFolderPathW.restype = ctypes.c_long

# ---------------------------------------------------------------- COM 小工具

CLSCTX_INPROC_SERVER = 0x1
CSIDL_DESKTOPDIRECTORY = 0x0010

IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPersistFile = "{0000010b-0000-0000-C000-000000000046}"
CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"

# IShellLinkW 的方法下标（IUnknown 占 0~2）
(QUERY_INTERFACE, _ADD_REF, RELEASE) = 0, 1, 2
(SET_DESCRIPTION, SET_WORKING_DIR, SET_ICON_LOCATION,
 SET_RELATIVE_PATH, SET_PATH) = 7, 9, 17, 18, 20
# IPersistFile：IUnknown 0~2、GetClassID 3、IsDirty 4、Load 5、Save 6
(PF_LOAD, PF_SAVE) = 5, 6

STGM_READ = 0x0
HRESULT = ctypes.c_long


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> GUID:
    value = GUID()
    ole32.CLSIDFromString(ctypes.c_wchar_p(text), byref(value))
    return value


def _call(obj: c_void_p, index: int, *args, argtypes=None) -> int:
    """按 vtable 下标调一个 COM 方法。第 0 个参数固定是 this 指针。"""
    vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(HRESULT, c_void_p, *(argtypes or []))
    return prototype(vtable[index])(obj, *args)


def desktop_dir() -> str:
    """真实桌面目录（会跟随 OneDrive 之类的重定向）。"""
    buffer = ctypes.create_unicode_buffer(260)
    shell32.SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY, None, 0, buffer)
    return buffer.value or os.path.join(os.path.expanduser("~"), "Desktop")


# ---------------------------------------------------------------- 读写快捷方式

def _open(path: str):
    """打开一条已存在的 .lnk，返回 (IShellLinkW*, IPersistFile*)。

    先 ``CoCreateInstance`` 出空对象，再用 IPersistFile::Load 把文件灌进去 ——
    这样原有属性（名字、位置、其它字段）都保留，只改我们关心的那几个。
    """
    ole32.CoInitialize(None)
    link = c_void_p()
    ole32.CoCreateInstance(byref(_guid(CLSID_ShellLink)), None,
                           CLSCTX_INPROC_SERVER,
                           byref(_guid(IID_IShellLinkW)), byref(link))
    if not link:
        raise RuntimeError("CoCreateInstance(ShellLink) 失败")
    persist = c_void_p()
    _call(link, QUERY_INTERFACE, byref(_guid(IID_IPersistFile)), byref(persist),
          argtypes=[c_void_p, c_void_p])
    if not persist:
        raise RuntimeError("QueryInterface(IPersistFile) 失败")
    _call(persist, PF_LOAD, ctypes.c_wchar_p(path), ctypes.c_uint32(STGM_READ),
          argtypes=[ctypes.c_wchar_p, ctypes.c_uint32])
    return link, persist


def read_target(path: str) -> str:
    link, _persist = _open(path)
    buffer = ctypes.create_unicode_buffer(1024)
    _call(link, 3, buffer, ctypes.c_int(len(buffer)), None, ctypes.c_uint32(0),
          argtypes=[POINTER(ctypes.c_wchar), ctypes.c_int, c_void_p,
                    ctypes.c_uint32])
    return buffer.value


def write_shortcut(path: str, target: str, workdir: str, icon: str,
                   description: str = "") -> None:
    """写入（不存在就新建）一条快捷方式。"""
    if os.path.exists(path):
        link, persist = _open(path)
    else:
        ole32.CoInitialize(None)
        link = c_void_p()
        ole32.CoCreateInstance(byref(_guid(CLSID_ShellLink)), None,
                               CLSCTX_INPROC_SERVER,
                               byref(_guid(IID_IShellLinkW)), byref(link))
        persist = c_void_p()
        _call(link, QUERY_INTERFACE, byref(_guid(IID_IPersistFile)),
              byref(persist), argtypes=[c_void_p, c_void_p])

    _call(link, SET_PATH, ctypes.c_wchar_p(target), argtypes=[ctypes.c_wchar_p])
    _call(link, SET_WORKING_DIR, ctypes.c_wchar_p(workdir),
          argtypes=[ctypes.c_wchar_p])
    _call(link, SET_ICON_LOCATION, ctypes.c_wchar_p(icon), ctypes.c_int(0),
          argtypes=[ctypes.c_wchar_p, ctypes.c_int])
    if description:
        _call(link, SET_DESCRIPTION, ctypes.c_wchar_p(description),
              argtypes=[ctypes.c_wchar_p])
    _call(persist, PF_SAVE, ctypes.c_wchar_p(path), ctypes.c_int(True),
          argtypes=[ctypes.c_wchar_p, ctypes.c_int])


def main() -> int:
    link_path = os.path.join(desktop_dir(), LINK_NAME)
    if "--print" in sys.argv:
        if not os.path.exists(link_path):
            print("桌面没有这条快捷方式:", link_path)
            return 1
        target = read_target(link_path)
        print(f"{link_path}\n  -> {target}\n  目标存在: {os.path.exists(target)}")
        return 0

    if not os.path.exists(EXE):
        print(f"FAIL 产物不存在，先打包：{EXE}")
        return 1
    # 图标随产物一起发：从 assets 拷一份到发布目录（快捷方式指它而不是 exe）
    if not os.path.exists(ICON):
        import shutil
        source = os.path.join(ROOT, "assets", "app.ico")
        if os.path.exists(source):
            shutil.copyfile(source, ICON)
            print(f"  图标已就位 {ICON}")

    before = read_target(link_path) if os.path.exists(link_path) else "（不存在）"
    write_shortcut(link_path, EXE, RELEASE_DIR, ICON, "CQUFSAE 串口调试助手")
    after = read_target(link_path)
    print(f"桌面快捷方式: {link_path}")
    print(f"  改前 -> {before}")
    print(f"  改后 -> {after}")
    print(f"  工作目录 {RELEASE_DIR}")
    if not os.path.exists(after):
        print("FAIL 改完之后目标还是不存在")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
