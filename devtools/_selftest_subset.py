# -*- coding: utf-8 -*-
"""只跑 selftest 里**与设置面板/开关相关**的几条用例。

为什么需要它：完整 `selftest.py` 在本机跑到串口那几条时会原生崩溃（退出码 127，
无 Python traceback，跑两次崩的位置还不一样：11 条 / 4 条）。那是串口/原生层的
环境问题，与本轮改动无关；但「定制功能注册表」等用例排在它后面，永远轮不到。
本脚本照抄 `selftest.main()` 的**全套隔离**（QSettings 快照、网格清零、取消选车、
接收历史与会话库指到临时目录），只把 CHECKS 换成指定的几条 —— 证据等价。

用法：QT_QPA_PLATFORM=windows .venv\\Scripts\\python.exe -u devtools\\_selftest_subset.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QSettings                    # noqa: E402
from PySide6.QtWidgets import QApplication                      # noqa: E402

WANT = ("定制功能注册表", "默认折叠发送区开关", "启动时最大化开关",
        "提示字号与按钮显隐", "自绘标题栏三键", "车辆与逐模组配置")


def main() -> int:
    import rx_history as _rxh
    import rx_store as _rxs
    import selftest as T
    from ui_theme import ui_font as _ui_font

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(_ui_font(12.5))

    gs = QSettings("MimoWorks", "SerialAssistant")
    T._heal_polluted_keys(gs)
    T._heal_hidden_buttons(gs)
    snap = {k: gs.value(k) for k in gs.allKeys()}
    for key in ("module_count", "cells_per_module", "temps_per_module"):
        gs.setValue(f"custom/{key}", 0)
    import customization as cu
    cu.select_vehicle("")
    gs.sync()

    real_resolve = _rxh._resolve_dir
    hist_dir = Path(tempfile.mkdtemp(prefix="subset_hist_"))
    _rxh._resolve_dir = lambda: hist_dir
    db_dir = Path(tempfile.mkdtemp(prefix="subset_db_"))
    _rxs.set_db_path(str(db_dir / "sessions.db"))

    picked = [(n, f) for n, f in T.CHECKS if n in WANT]
    missing = [n for n in WANT if n not in {x[0] for x in picked}]
    if missing:
        print("  WARN  这些名字不在 CHECKS 里:", missing)

    failed = 0
    for name, func in picked:
        try:
            func(app) if func.__code__.co_argcount else func()
        except Exception as exc:                               # noqa: BLE001
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"  PASS  {name}")

    for k in gs.allKeys():
        if k not in snap:
            gs.remove(k)
    for k, v in snap.items():
        gs.setValue(k, v)
    gs.sync()

    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()

    _rxh._path = None
    _rxh._lines.clear()
    _rxh._pending.clear()
    _rxh._file_lines = 0
    _rxh._resolve_dir = real_resolve
    _rxs.shutdown()
    _rxs.set_db_path(None)
    _rxs.reset_for_tests()
    import shutil
    shutil.rmtree(db_dir, ignore_errors=True)
    for k in gs.allKeys():
        if k not in snap:
            gs.remove(k)
    for k, v in snap.items():
        gs.setValue(k, v)
    gs.sync()

    print("\n%d/%d 项通过" % (len(picked) - failed, len(picked)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
