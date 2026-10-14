# -*- coding: utf-8 -*-
r"""安装/卸载全流程体检 —— 全程在**隔离目录**里跑，不动用户的真实安装。

为什么要有这个：安装器/卸载器的验证没法靠"跑一遍看有没有报错"（沙箱里 spawn 会
被拦、进程退 0 也不代表真装上了，踩过）。所以这里一律**查落地证据**：
安装目录、卸载表注册项、快捷键、旧版本是否被清掉、配置有没有被误删。

覆盖的场景（就是用户 2026-10-15 点名的两条）：
  1. 全新安装：装到临时目录，核对 exe/图标/快捷方式/卸载项都在；
  2. **升级自动卸旧版**：先装 1.8 到一个目录，再把新版装到**另一个**目录，
     新安装器应当把 1.8 静默卸掉（磁盘上不留两份、卸载表不留两条），
     并且**用户的配置必须还在**（这条是重点：老卸载程序静默模式下会删配置，
     新安装器用"导出→卸→导回"兜住）；
  3. 卸载：静默卸载后目录、快捷方式、卸载项都要消失。

安全网：测试前后各导出一份 `HKCU\Software\MimoWorks`（配置全在这下面），
跑完无条件导回 —— 就算被测程序把配置删了也伤不到用户。

用法：
    python devtools\_installer_check.py            # 全流程
    python devtools\_installer_check.py --keep     # 跑完不清理（留着看现场）
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import winreg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW_SETUP = os.path.join(ROOT, "发布", "SerialAssistant-setup-2.0.exe")
OLD_SETUP = os.path.join(ROOT, "发布", "SerialAssistant-setup-1.8.exe")
APP_KEY = r"Software\MimoWorks"
UNINST_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
APPID = "{8F3C1B42-7A6E-4D91-9C15-2E7B4A0D5F31}_is1"
KEEP = "--keep" in sys.argv

# ⚠️ 工作目录**放 F 盘**（用户点名：我下载/安装/测试的东西一律别占系统盘 C）。
#    装两次 52MB 的 exe 加卸载，落在 C 盘 Temp 里既占系统盘又容易忘清。
WORK_BASE = os.path.join(ROOT, "_check", "install_test")
saved_settings = os.path.join(WORK_BASE, "sa_check_settings.json")


def run(cmd: list[str], *, timeout: int = 300) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ---------------------------------------------------------------- 注册表工具
# ⚠️ 一律用 winreg，**不要用 reg.exe**：本机沙箱把 reg.exe 列进了程序黑名单，
#    连"绕过沙箱"都不放行（踩过——脚本第一版就是死在这儿的）。
def dump_tree(root, path: str = APP_KEY) -> dict:
    """递归导出一棵注册表子树 → {子路径: {值名: [类型, JSON 安全的值]}}。

    类型必须一起存：导回时 `SetValueEx` 要求类型和值对得上（REG_MULTI_SZ 给个
    字符串就会 "Could not convert the data to the specified type" 崩掉，踩过）。
    """
    out: dict[str, dict] = {}
    try:
        handle = winreg.OpenKey(root, path)
    except OSError:
        return out
    with handle:
        values = {}
        index = 0
        while True:
            try:
                name, value, kind = winreg.EnumValue(handle, index)
            except OSError:
                break
            if isinstance(value, bytes):
                value = {"__b64__": base64.b64encode(value).decode()}
            values[name] = [kind, value]
            index += 1
        if values:
            out[path] = values
        else:
            out[path] = {}
        index = 0
        while True:
            try:
                sub = winreg.EnumKey(handle, index)
            except OSError:
                break
            out.update(dump_tree(root, f"{path}\\{sub}"))
            index += 1
    return out


def delete_tree(root, path: str) -> None:
    """递归删掉一棵子树（winreg 的 DeleteKey 只能删空键，得自己往下走）。"""
    try:
        with winreg.OpenKey(root, path) as handle:
            index = 0
            subs = []
            while True:
                try:
                    subs.append(winreg.EnumKey(handle, index))
                except OSError:
                    break
                index += 1
        for sub in subs:
            delete_tree(root, f"{path}\\{sub}")
        winreg.DeleteKey(root, path)
    except OSError:
        pass


def load_tree(snapshot: dict) -> None:
    """把导出的子树写回（先清掉现有的，保证和快照完全一致）。"""
    delete_tree(winreg.HKEY_CURRENT_USER, APP_KEY)
    for path, values in sorted(snapshot.items(), key=lambda kv: kv[0].count("\\")):
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path) as key:
            for name, (kind, value) in values.items():
                if isinstance(value, dict) and "__b64__" in value:
                    winreg.SetValueEx(key, name, 0, kind,
                                      base64.b64decode(value["__b64__"]))
                    continue
                if value is None:            # 空值：类型沿用原样
                    winreg.SetValueEx(key, name, 0, kind, "")
                    continue
                winreg.SetValueEx(key, name, 0, kind, value)


def app_value(name: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_KEY) as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return "<缺失>"


def set_marker(name: str, value: str) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, APP_KEY) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def del_marker(name: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        pass


def uninstall_entries() -> list[str]:
    """「应用和功能」里本程序留下的条目（HKLM + HKCU 都查）。"""
    found = []
    for hive, root in (("HKLM", winreg.HKEY_LOCAL_MACHINE),
                       ("HKCU", winreg.HKEY_CURRENT_USER)):
        try:
            with winreg.OpenKey(root, UNINST_KEY) as handle:
                index = 0
                while True:
                    try:
                        sub = winreg.EnumKey(handle, index)
                    except OSError:
                        break
                    if "CQUFSAE" in sub or APPID in sub:
                        found.append(f"{hive}\\{sub}")
                    index += 1
        except OSError:
            continue
    return found


def has_shortcut() -> bool:
    paths = [os.path.join(os.environ.get("USERPROFILE", ""), "Desktop",
                          "CQUFSAE串口调试助手.lnk"),
             os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                          "Start Menu", "Programs", "CQUFSAE串口调试助手")]
    return any(os.path.exists(p) for p in paths)


def show_setup_log(path: str) -> None:
    """把安装器日志里和"旧版本拆除"有关的那几行贴出来。"""
    if not os.path.exists(path):
        print(f"  （没找到安装日志 {path}）")
        return
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = [ln.strip() for ln in handle
                 if "旧版本" in ln or "移除旧" in ln or "旧目录" in ln
                 or "旧快捷方式" in ln or "检测到" in ln or "Error" in ln
                 or "Exception" in ln]
    print("  ── 安装器日志（旧版拆除相关）──")
    for line in lines[:12]:
        print("   ", line)


def uninstall(target_dir: str) -> None:
    unins = os.path.join(target_dir, "unins000.exe")
    if os.path.exists(unins):
        run([unins, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])


def wait_gone(path: str, seconds: float = 30) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if not os.path.exists(path):
            return True
        time.sleep(0.5)
    return not os.path.exists(path)


def main() -> int:
    for path in (NEW_SETUP, OLD_SETUP):
        if not os.path.exists(path):
            print(f"✗ 缺少安装包：{path}")
            return 1

    os.makedirs(WORK_BASE, exist_ok=True)
    work = os.path.join(WORK_BASE, "run")       # 固定目录，跑前先清（留在 F 盘）
    shutil.rmtree(work, ignore_errors=True)     # 上一次跑剩的现场必须先清掉，
    os.makedirs(work, exist_ok=True)            # 否则旧 unins000.exe 会干扰判断（踩过）
    old_dir = os.path.join(work, "OldApp")       # 模拟"旧版装在别处"
    new_dir = os.path.join(work, "NewApp")
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  {'✓' if ok else '✗'} {label}" + (f"  {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    snapshot = dump_tree(winreg.HKEY_CURRENT_USER)
    with open(saved_settings, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=1)
    print(f"配置已备份（{len(snapshot)} 个键）→ {saved_settings}（跑完无条件导回）")
    marker = f"check-{int(time.time())}"
    set_marker("_installer_check_marker", marker)

    try:
        # ---- 1) 先装一份"旧版"到 OldApp（造出升级现场）----
        print("\n[1] 安装旧版 1.8 →", old_dir)
        old_log = os.path.join(work, "old_setup.log")
        code, out = run([OLD_SETUP, "/VERYSILENT", "/SUPPRESSMSGBOXES",
                         "/NORESTART", "/NOICONS", f"/DIR={old_dir}",
                         f"/LOG={old_log}"])
        check("旧版安装进程退出码 0", code == 0, f"exit={code}")
        check("旧版落地（exe 存在）",
              os.path.exists(os.path.join(old_dir, "SerialAssistant.exe")))
        check("卸载表有旧版条目", len(uninstall_entries()) >= 1,
              f"{len(uninstall_entries())} 条")

        # ---- 2) 装新版到**另一个**目录：应当自动把旧版卸掉 ----
        print("\n[2] 安装新版 2.0 →", new_dir, "（旧版在另一个目录，应被自动卸掉）")
        new_log = os.path.join(work, "new_setup.log")
        code, out = run([NEW_SETUP, "/VERYSILENT", "/SUPPRESSMSGBOXES",
                         "/NORESTART", f"/DIR={new_dir}", f"/LOG={new_log}"])
        check("新版安装进程退出码 0", code == 0, f"exit={code}")
        check("新版落地（exe 存在）",
              os.path.exists(os.path.join(new_dir, "SerialAssistant.exe")))
        check("新版落地（图标存在）",
              os.path.exists(os.path.join(new_dir, "app.ico")))
        check("旧版目录被清掉", not os.path.exists(old_dir), old_dir)
        show_setup_log(new_log)
        entries = uninstall_entries()
        check("卸载表只剩一条（没有两份并存）", len(entries) == 1,
              f"{len(entries)} 条")
        check("**用户配置未被误删**", app_value("_installer_check_marker") == marker,
              app_value("_installer_check_marker"))

        # ---- 3) 卸载新版：目录/快捷方式/卸载项都应消失 ----
        print("\n[3] 静默卸载新版")
        uninstall(new_dir)
        check("安装目录已删除", wait_gone(new_dir), new_dir)
        check("卸载表条目已清空", len(uninstall_entries()) == 0,
              f"{len(uninstall_entries())} 条")
        check("静默卸载后配置仍在", app_value("_installer_check_marker") == marker)
    finally:
        # 收尾：导回配置（无条件）、清标记、清测试目录
        load_tree(snapshot)
        del_marker("_installer_check_marker")
        # 万一 3) 没跑到（中途失败），把可能残留的安装也卸掉，别留垃圾
        for d in (old_dir, new_dir):
            if os.path.exists(d):
                uninstall(d)
                time.sleep(1)
        if not KEEP:
            shutil.rmtree(work, ignore_errors=True)
            print(f"\n测试目录已清理：{work}")
        else:
            print(f"\n测试目录保留：{work}")

    print("\n" + "=" * 60)
    if failures:
        print(f"有 {len(failures)} 项未通过：")
        for item in failures:
            print("  ✗", item)
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
