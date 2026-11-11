# -*- coding: utf-8 -*-
"""DLC 宿主侧无硬件自测：安装 / 发现 / 加载 / 启停 / 卸载 / 恶意包拦截。

    .venv/Scripts/python.exe devtools/_dlc_selftest.py

⛔ 全程用 ``SERIALASSISTANT_DLC_DIR`` 指到项目内 ``_check/dlc_stage``，
**不碰用户真实的 %LOCALAPPDATA% 目录，不碰 QSettings**。
"""

from __future__ import annotations

import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "_check" / "dlc_stage"
sys.path.insert(0, str(ROOT))
os.environ["SERIALASSISTANT_DLC_DIR"] = str(STAGE)

import dlc_host  # noqa: E402
from heartbeatDLC.selftest import run as protocol_selftest  # noqa: E402

CHECKS: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append((name, bool(ok)))


def _reset_stage() -> None:
    if not str(STAGE).replace("\\", "/").endswith("_check/dlc_stage"):
        raise SystemExit("拒绝清理非预期目录")
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)


def _make_zip(out: Path, extra: dict[str, bytes] | None = None) -> Path:
    source = ROOT / "heartbeatDLC"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts \
                    and path.suffix not in (".pyc", ".pyo"):
                zf.write(path, f"heartbeatDLC/{path.relative_to(source).as_posix()}")
        for name, payload in (extra or {}).items():
            zf.writestr(name, payload)
    return out


def main() -> int:
    _reset_stage()
    print("== 协议层自测 ==")
    check("协议自测全绿", protocol_selftest() == 0)

    print("\n== 宿主侧自测 ==")
    package = STAGE.parent / "dlc_test.zip"
    _make_zip(package)
    try:
        info = dlc_host.install_zip(package)
    except Exception as exc:                       # noqa: BLE001
        check("安装 ZIP 不抛异常", False)
        print(f"  安装失败：{type(exc).__name__}: {exc}")
        info = None
    else:
        check("安装 ZIP 不抛异常", True)
        check("安装后 id 正确", info.id == "heartbeatDLC")
        check("安装后版本正确", info.version == "1.0.0")
        check("安装目录在用户 DLC 根下", info.path == STAGE / "heartbeatDLC")
        check("清单文件已落盘", (info.path / "manifest.json").is_file())
        check("恶意/无关文件不会进来：__pycache__",
              not (info.path / "__pycache__").exists())

    found = dlc_host.discover()
    check("discover 找到 1 个插件", len(found) == 1)
    check("默认启用", bool(found) and found[0].enabled)
    check("默认无错误", bool(found) and not found[0].error)
    check("portable 目录不重复计数", len(found) == 1)

    # 外部目录动态加载：先把项目根里的同名包从模块缓存摘掉，
    # 确保这次 import 真的来自 stage（模拟用户下载后安装的目录）。
    for name in [m for m in sys.modules if m == "heartbeatDLC"
                 or m.startswith("heartbeatDLC.")]:
        sys.modules.pop(name, None)

    class StubCtx:
        app_version = dlc_host.VERSION
        launchers: list[str] = []

        def add_launcher(self, text, callback, tooltip="") -> None:
            StubCtx.launchers.append(str(text))

        def data_dir(self, plugin_id):
            return STAGE / "_data" / str(plugin_id)

        def log(self, message) -> None:
            pass

        def reload_now(self) -> None:
            pass

    plugin = dlc_host.load(found[0], StubCtx()) if found else None
    check("外部目录动态加载成功", plugin is not None)
    check("插件注册了入口按钮", "心率 DLC" in StubCtx.launchers)
    check("插件模块来自 stage 目录",
          str(getattr(sys.modules.get("heartbeatDLC"), "__file__", ""))
          .replace("\\", "/").startswith(str(STAGE).replace("\\", "/")))
    if plugin is not None:
        check("插件有 shutdown 接口", callable(getattr(plugin, "shutdown", None)))
        plugin.shutdown()

    dlc_host.set_enabled("heartbeatDLC", False)
    check("禁用状态可被 discover 读到",
          any(not i.enabled for i in dlc_host.discover()))
    dlc_host.set_enabled("heartbeatDLC", True)
    check("重新启用生效", all(i.enabled for i in dlc_host.discover()))

    # 恶意包：路径穿越、绝对路径、可执行文件后缀都必须被过滤或拒绝
    hostile = STAGE.parent / "dlc_hostile.zip"
    _make_zip(hostile, {
        "heartbeatDLC/../evil.py": b"print('zip slip')",
        "C:/absolute.py": b"print('abs')",
        "heartbeatDLC/payload.exe": b"MZ",
    })
    try:
        hostile_info = dlc_host.install_zip(hostile)
    except Exception as exc:                        # noqa: BLE001
        check("恶意包被拒绝", True)
        print(f"  拒绝原因：{type(exc).__name__}: {exc}")
    else:
        target = hostile_info.path
        check("路径穿越文件未写出", not (STAGE / "evil.py").exists())
        check("可执行文件未安装", not (target / "payload.exe").exists())
        check("绝对路径文件未写出", not (STAGE / "absolute.py").exists())

    # 目录名与 id 不一致的包必须拒绝
    mismatch = STAGE.parent / "dlc_mismatch.zip"
    with zipfile.ZipFile(mismatch, "w") as zf:
        zf.writestr("wrongname/manifest.json",
                    (ROOT / "heartbeatDLC" / "manifest.json").read_text("utf-8"))
    try:
        dlc_host.install_zip(mismatch)
        check("目录名与 id 不一致被拒绝", False)
    except ValueError:
        check("目录名与 id 不一致被拒绝", True)

    # 随包 .sha256：正确则放行，被改动则拒绝（dlc_host.install_zip 的新校验）
    official = ROOT / "发布" / "heartbeatDLC-1.0.0.zip"
    sidecar = official.with_name(official.name + ".sha256")
    if official.is_file() and sidecar.is_file():
        signed = STAGE.parent / "dlc_signed.zip"
        signed_sidecar = signed.with_name(signed.name + ".sha256")
        shutil.copyfile(official, signed)
        shutil.copyfile(sidecar, signed_sidecar)
        try:
            dlc_host.install_zip(signed)
            check("带正确 .sha256 的安装包通过校验", True)
        except Exception as exc:                    # noqa: BLE001
            check("带正确 .sha256 的安装包通过校验", False)
            print(f"  校验失败：{type(exc).__name__}: {exc}")
        signed_sidecar.write_text("0" * 64 + f"  {signed.name}\n", "utf-8")
        try:
            dlc_host.install_zip(signed)
            check("SHA256 不匹配被拒绝", False)
        except ValueError:
            check("SHA256 不匹配被拒绝", True)
        signed.unlink(missing_ok=True)
        signed_sidecar.unlink(missing_ok=True)
    else:
        print("  SKIP  .sha256 用例（先跑 _build_heartbeat_dlc.py 生成发布包）")

    current = next((i for i in dlc_host.discover() if i.id == "heartbeatDLC"), None)
    if current is not None:
        backup = dlc_host.uninstall(current)
        check("卸载后目录消失", not current.path.exists())
        check("卸载前生成备份 ZIP", backup is not None and backup.is_file())
        check("卸载后 discover 为空", not dlc_host.discover())

    package.unlink(missing_ok=True)
    hostile.unlink(missing_ok=True)
    mismatch.unlink(missing_ok=True)

    failed = [name for name, ok in CHECKS if not ok]
    print("\n== 结果 ==")
    for name, ok in CHECKS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\nDLC 宿主自测：{len(CHECKS) - len(failed)}/{len(CHECKS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
