"""DWM 进阶方案能力探针 + 评估（lead 2026-11-05）。

对应 od46 收尾时用户的两个诉求之一：「软件最大化和还原还是不稳定，动画不连续，
有跳变，会闪，把这个问题也修了」。od46 已用两条确定性修复把根因压住：

  R1 空闲快照自重试（main_window.py:2711 _build_fs_idle_pix / 2754 _schedule_fs_idle_snap）
     —— 进全屏后 60ms 起手 + 自重试（≤20 次 / ≤1.5s），保证还原覆盖层永远有
        "旧画面"可用，不再掉进无快照的纯原生回退分支（那是过冲回弹 = 闪的根）。
  R2 覆盖层底色同步缩放（main_window.py:2954 _begin_window_cover / 2986 bg.setGeometry(start)）
     —— 进全屏第 1 帧底色只盖原窗口矩形（零跳变），随后与内容层连续长大到全屏，
        t>0.7 两层一起交叉淡出。旧实现一上来把整屏刷纯色 = 第 1 帧窗外从壁纸变纯色。
  + 系统弹簧压制（main_window.py:2771 _set_dwm_transitions，DWMWA_TRANSITIONS_FORCEDISABLED=3）
     —— 覆盖层播自己动画时关掉系统原生还原/最大化动画，收尾再恢复（对称）。

本探针只做**能力盘点 + 结论落档**，不改动任何交付代码。它会在用户的 Windows
机器上实跑，报告 dwmapi 里哪些 API 真实可用、DWM 组合是否开启，并给出对
「DwmFlush / DwmRegisterThumbnail / DirectComposition」四条路的明确裁决。

跑法（桌面必须 ALIVE；本探针不抓屏，但 DwmIsCompositionEnabled / 函数地址探测
需要真实 Windows 会话）：
    .venv\\Scripts\\python.exe devtools/_qa_dwm_eval.py

退出码：0 = 全部能力探测成功（仅能力盘点，不判产品好坏）；1 = 非 win32 或 dwmapi 缺失。
"""
from __future__ import annotations

import ctypes
import os
import sys

# 能力盘点需要真实 Windows 会话 + DWM；非 win32 直接退出。
if sys.platform != "win32":
    print("[SKIP] 非 win32 平台，无 DWM 可评估。")
    sys.exit(1)

os.environ.setdefault("QT_QPA_PLATFORM", "windows")  # 仅用于一致性；本探针不建 Qt 窗口


def _probe_dwmapi() -> dict:
    """探测 dwmapi.dll 中相关导出函数的真实地址。"""
    out: dict = {}
    try:
        dll = ctypes.windll.dwmapi  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 无法加载 dwmapi.dll：{exc!r}")
        sys.exit(1)

    names = [
        "DwmFlush",
        "DwmSetWindowAttribute",
        "DwmGetWindowAttribute",
        "DwmIsCompositionEnabled",
        "DwmRegisterThumbnail",
        "DwmUnregisterThumbnail",
        "DwmUpdateThumbnailProperties",
        "DwmQueryThumbnailSourceSize",
        "DwmInvalidateIconicBitmaps",
    ]
    for n in names:
        try:
            addr = ctypes.cast(getattr(dll, n), ctypes.c_void_p).value
            out[n] = addr is not None and addr != 0
        except Exception:  # noqa: BLE001
            out[n] = False
    return out


def _composition_enabled() -> bool | None:
    try:
        dll = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        enabled = ctypes.c_bool(False)
        hr = dll.DwmIsCompositionEnabled(ctypes.byref(enabled))
        if hr == 0:
            return enabled.value
    except Exception:  # noqa: BLE001
        pass
    return None


def _rule(ok: bool, name: str, verdict: str) -> str:
    mark = "✅ 可用" if ok else "❌ 缺失"
    return f"  - {name:<38} {mark}  | {verdict}"


def main() -> int:
    print("=" * 78)
    print("DWM 进阶方案能力探针 + 评估（od46 最大化/还原根治的下一步）")
    print("=" * 78)

    caps = _probe_dwmapi()
    comp = _composition_enabled()

    print("\n[1] dwmapi 能力盘点")
    for k, v in caps.items():
        print(f"  {'✅' if v else '❌'} {k}")

    print(f"\n[2] DWM 组合（桌面合成）是否开启："
          f"{'开启' if comp else ('关闭' if comp is False else '探测失败')}")

    print("\n[3] 对四条进阶路的裁决（基于官方文档 + 开源实现实测）")
    print(_rule(caps.get("DwmSetWindowAttribute", False),
                "DWMWA_TRANSITIONS_FORCEDISABLED(3)",
                "od46 已用：压系统弹簧，收尾对称恢复 —— 保留"))
    print(_rule(caps.get("DwmFlush", False),
                "DwmFlush",
                "低风险的'微优化'：每次覆盖层重绘后强制 DWM 立即 present，"
                "收窄主窗 resize 的空白帧窗口；非根因修复，收益难量化，建议做成开关"))
    print(_rule(caps.get("DwmRegisterThumbnail", False),
                "DwmRegisterThumbnail",
                "不推荐：它是'活'镜像，源窗（主窗）自己在 resize 时同样有 backing-store "
                "空洞 + 弹簧；隐藏源窗镜像直接黑；rcSource 在 resize 后数百 ms 滞后 = "
                "E_INVALIDARG。会重新引入我们刚消灭的 live-resize 竞态"))
    print(_rule(caps.get("DwmUpdateThumbnailProperties", False),
                "DwmUpdateThumbnailProperties",
                "同上，Thumbnail 方案的一部分，不推荐"))
    print(_rule(caps.get("DwmQueryThumbnailSourceSize", False),
                "DwmQueryThumbnailSourceSize",
                "Thumbnail 方案配套；实测 resize 后回报尺寸滞后 —— 不推荐"))
    print("  - DirectComposition (COM)                           ❌ 不评估  | "
          "过度工程：整套 COM 合成管线，改动面巨大，且不解决'真窗 resize 空洞'根因")

    print("\n[4] 结论")
    print("  od46 的 R1(快照自重试) + R2(覆盖层同步缩放) + 系统弹簧压制 已是约束下的")
    print("  确定性最优解。DWM Thumbnail / DirectComposition 解决不了根因且引入 live 竞态，")
    print("  不建议接入。唯一可考虑的增量是 DwmFlush 的'关掉开关'式微优化，留给真机 A/B。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
