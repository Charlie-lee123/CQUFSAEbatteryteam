# -*- coding: utf-8 -*-
"""验证「心率 DLC 单一开关」依赖的 dlc_host 契约（只读，不写 enabled.json、不卸载）。

只确认：discover 能找到 heartbeatDLC 且 enabled、load 返回的插件实例有
可调用的 show_panel（开关 ON 时 _on_dlc_switch 会调它）。不碰用户真实配置。
"""
import os
import sys

# 让脚本在 devtools/ 子目录下也能 import 到仓库根的 dlc_host.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dlc_host  # noqa: E402


class _StubCtx:
    def __init__(self):
        self.launchers = []

    def add_launcher(self, text, callback, tooltip=""):
        self.launchers.append((text, callback))

    def data_dir(self, plugin_id):
        return dlc_host.user_dir() / plugin_id

    def log(self, message):
        print("  [dlc log]", message)

    def reload_now(self):
        pass

    app_version = "2.7"

    def main_window(self):
        return None


def main() -> int:
    infos = dlc_host.discover()
    hb = next((i for i in infos if i.id == "heartbeatDLC"), None)
    if hb is None:
        print("FAIL: discover() 未找到 heartbeatDLC（本地是否装了心率 DLC？）")
        return 1
    print(f"  找到 heartbeatDLC v{hb.version} enabled={hb.enabled} error={hb.error!r}")
    if hb.error:
        print(f"FAIL: 清单/校验报错：{hb.error}")
        return 1

    # 加载（仅验证契约，不在真实主窗里跑）
    ctx = _StubCtx()
    try:
        plugin = dlc_host.load(hb, ctx)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: load 抛异常：{type(exc).__name__}: {exc}")
        return 1
    if plugin is None:
        print("FAIL: load 返回 None（可能缺依赖 bleak，冻结 exe 里已打包）")
        return 1
    print(f"  load OK，入口回调数={len(ctx.launchers)}")
    if not ctx.launchers:
        print("FAIL: 插件没通过 add_launcher 注册任何入口")
        return 1

    name, cb = ctx.launchers[0]
    print(f"  入口文案={name!r} 回调可调用={callable(cb)}")
    if not callable(cb):
        print("FAIL: 入口回调不可调用")
        return 1

    show = getattr(plugin, "show_panel", None)
    print(f"  plugin.show_panel 可调用={callable(show)}")
    if not callable(show):
        print("FAIL: 插件实例没有 show_panel")
        return 1

    # 关闭（避免留下运行实例；不卸载文件）
    shutdown = getattr(plugin, "shutdown", None)
    if callable(shutdown):
        try:
            shutdown()
        except Exception as exc:  # noqa: BLE001
            print(f"  (shutdown 抛错，忽略) {type(exc).__name__}: {exc}")

    print("PASS: 心率 DLC 单一开关所依赖的加载/入口契约成立")
    return 0


if __name__ == "__main__":
    sys.exit(main())
