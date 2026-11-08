"""QSettings 注册表整树备份/还原（探针安全网）。

⛔ 项目有过「探针重置用户设置」的事故：开窗探针必须在**进程外**做整树备份，
跑完原样写回。``reg.exe`` 在沙箱里被禁，故用 ``winreg`` 逐键递归。

用法::

    python devtools/_probe_settings_backup.py save  _check/_splashcap/settings.json
    python devtools/_probe_settings_backup.py restore _check/_splashcap/settings.json
    python devtools/_probe_settings_backup.py diff a.json b.json
"""
from __future__ import annotations

import json
import sys
import winreg

ROOT = r"Software\MimoWorks\SerialAssistant"


def _dump(hkey, path: str, out: dict) -> None:
    with winreg.OpenKey(hkey, path, 0, winreg.KEY_READ) as k:
        n_sub, n_val, _ = winreg.QueryInfoKey(k)
        vals = {}
        for i in range(n_val):
            name, value, typ = winreg.EnumValue(k, i)
            if isinstance(value, bytes):
                value = {"__hex__": value.hex()}
            vals[name] = [typ, value]
        out["values"] = vals
        out["subs"] = {}
        for i in range(n_sub):
            name = winreg.EnumKey(k, i)
            child: dict = {}
            _dump(hkey, path + "\\" + name, child)
            out["subs"][name] = child


def save(dest: str) -> None:
    tree: dict = {}
    _dump(winreg.HKEY_CURRENT_USER, ROOT, tree)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(tree, fh, ensure_ascii=False, indent=1)
    print(f"[settings] saved -> {dest}")


def _wipe(hkey, path: str) -> None:
    try:
        with winreg.OpenKey(hkey, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    name = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _wipe(hkey, path + "\\" + name)
                try:
                    winreg.DeleteKey(k, name)
                except OSError:
                    pass
    except FileNotFoundError:
        return


def _load(hkey, path: str, node: dict) -> None:
    with winreg.CreateKeyEx(hkey, path, 0, winreg.KEY_ALL_ACCESS) as k:
        for name, (typ, value) in node.get("values", {}).items():
            if isinstance(value, dict) and "__hex__" in value:
                value = bytes.fromhex(value["__hex__"])
            winreg.SetValueEx(k, name, 0, typ, value)
        for name, child in node.get("subs", {}).items():
            _load(hkey, path + "\\" + name, child)


def restore(src: str) -> None:
    with open(src, encoding="utf-8") as fh:
        tree = json.load(fh)
    _wipe(winreg.HKEY_CURRENT_USER, ROOT)
    _load(winreg.HKEY_CURRENT_USER, ROOT, tree)
    print(f"[settings] restored <- {src}")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "save":
        save(sys.argv[2])
    elif cmd == "restore":
        restore(sys.argv[2])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
