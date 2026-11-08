"""设置面板「回退到直接弹出」回归探针（2026-11-06）。

验证：
  1. ``MainWindow._open_settings()`` 直接居中弹出设置对话框，**不再**走
     ``_SettingsShot`` / PrintWindow 抓图 / 几何放大补间；
  2. ``_close_settings_dialog(accepted=False)`` 直接关闭，**不再**缩回按钮动画；
  3. 全程无 Python traceback、``_settings_shot`` 未被 show；
  4. QSettings 未被污染（开窗/关窗前对 ``MimoWorks/SerialAssistant`` 全量快照，
     finally 里原样还原，含删除探针期间可能新增的键）。

⛔ 绝不跑 selftest.py、绝不写发布/、绝不动 customization 落盘（cancel 只还原）。

运行：``.venv/Scripts/python.exe devtools/_probe_settings_revert.py``
（须在 Windows 桌面环境，QT_QPA_PLATFORM=windows；offscreen/headless 亦可用）。
"""
from __future__ import annotations

import os
import sys
import time

# 把项目根目录加进 sys.path（脚本在 devtools/ 下，否则 import 不到主模块）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 真窗口平台；若没有显示器，调用方自行 export QT_QPA_PLATFORM=offscreen。
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import app_log
app_log.init()

import customization
import ui_anim
from ui_theme import apply_scheme, detect_system_scheme, ui_font

from main_window import MainWindow


def _snapshot_settings() -> dict:
    """对 MimoWorks/SerialAssistant 做全量快照（键 → 值）。"""
    s = QSettings("MimoWorks", "SerialAssistant")
    snap: dict[str, object] = {}
    for key in s.allKeys():
        snap[key] = s.value(key)
    return snap


def _restore_settings(snap: dict) -> None:
    """原样还原快照；多出来的键删掉。在 QSettings 实例关闭（sync）后再还原一次，
    避免 MainWindow 持有的实例析构时把缓冲写回覆盖还原。
    """
    s = QSettings("MimoWorks", "SerialAssistant")
    current = set(s.allKeys())
    original = set(snap.keys())
    for key in original:
        s.setValue(key, snap[key])
    for key in (current - original):
        s.remove(key)
    s.sync()
    # 二次确认：析构前再清理一次可能残余的新增键（MainWindow 实例此时应已销毁）。
    s2 = QSettings("MimoWorks", "SerialAssistant")
    for key in (set(s2.allKeys()) - original):
        s2.remove(key)
    s2.sync()


def _pump(app: QApplication, ms: float) -> None:
    deadline = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.005)


def main() -> int:
    snap = _snapshot_settings()
    errors: list[str] = []
    out_png = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "_check", "_probe_settings_revert.png")
    out_png = os.path.abspath(out_png)

    app = QApplication(sys.argv)
    app.setApplicationName("CQUFSAE串口助手")
    app.setOrganizationName("MimoWorks")
    app.setStyle("Fusion")
    scheme = detect_system_scheme()
    apply_scheme(scheme)
    app.setFont(ui_font(12.5))
    ui_anim.set_frame_hz(int(customization.get("anim_fps")) or 120)

    window = None
    try:
        window = MainWindow()
        window.show()
        # 整个探针放到屏幕外，避免真实窗口闪到用户屏幕上。
        window.move(-4000, -4000)
        window.raise_()
        _pump(app, 150)

        # —— 打开设置 ——
        window._open_settings()
        _pump(app, 400)
        dlg = window._settings_dialog
        if dlg is None:
            errors.append("打开后 _settings_dialog 为 None")
        elif not dlg.isVisible():
            errors.append("打开后设置对话框 isVisible() 为假")
        # 快照容器必须保持隐藏（未走放大动画）
        if not window._settings_shot.isHidden():
            errors.append("_settings_shot 被 show（放大动画 machinery 仍在跑）")
        # 抓一张面板内容图供人工查看（offscreen，不闪给用户）
        if dlg is not None and dlg.isVisible():
            try:
                pix = dlg.grab()
                pix.save(out_png)
            except Exception as exc:  # noqa: BLE001 - 抓图失败不致命
                errors.append(f"dialog.grab() 失败: {exc!r}")

        # —— 关闭设置（cancel，只还原不落盘）——
        window._close_settings_dialog(accepted=False)
        _pump(app, 200)
        if window._settings_dialog is not None:
            errors.append("关闭后 _settings_dialog 仍非 None")

        print("OPEN: dialog=%r visible=%r shot_hidden=%r"
              % (dlg is not None,
                 dlg.isVisible() if dlg is not None else False,
                 window._settings_shot.isHidden()))
        print("CLOSE: settings_dialog=%r" % (window._settings_dialog,))
        print("PNG: %s" % out_png)
    except Exception as exc:  # noqa: BLE001 - 任何异常都记下来
        import traceback
        errors.append("EXCEPTION: %s\n%s" % (exc, traceback.format_exc()))
    finally:
        try:
            if window is not None:
                window.deleteLater()
            app.processEvents()
        except Exception:  # noqa: BLE001
            pass
        # 析构 MainWindow 持有的 QSettings 实例前先 sync 一次，再还原。
        try:
            app.closeAllWindows()
        except Exception:  # noqa: BLE001
            pass
        _restore_settings(snap)
        # 丢弃 app（释放该进程持有的注册表/ini 句柄）
        try:
            app.quit()
        except Exception:  # noqa: BLE001
            pass
        del app

    if errors:
        print("RESULT: FAIL")
        for e in errors:
            print("  -", e)
        return 1
    print("RESULT: PASS  (打开=居中直接弹出, 关闭=直接关闭, 无抓图/无放大/无闪, QSettings 未被污染)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
