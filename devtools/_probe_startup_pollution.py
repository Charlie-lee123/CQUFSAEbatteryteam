# -*- coding: utf-8 -*-
"""排查：哪个 check 把 custom/startup_maximized 写脏了。
在临时副本思路下直接跑主 selftest 的 CHECKS，逐项后打印注册表值。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
import selftest


def main():
    app = QApplication(sys.argv)
    s = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: s.value(k) for k in s.allKeys()}
    try:
        for name, func in selftest.CHECKS:
            before = s.value("custom/startup_maximized")
            try:
                func(app) if func.__code__.co_argcount else func()
                status = "ok"
            except Exception as exc:
                status = f"RAISED {type(exc).__name__}: {str(exc)[:60]}"
            after = s.value("custom/startup_maximized")
            mark = "  <-- 写脏" if before != after else ""
            print(f"{name}: before={before} after={after} [{status}]{mark}")
            s.sync()
    finally:
        for k in s.allKeys():
            if k not in snap:
                s.remove(k)
        for k, v in snap.items():
            s.setValue(k, v)
        s.sync()


if __name__ == "__main__":
    main()
