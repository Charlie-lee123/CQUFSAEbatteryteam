# -*- coding: utf-8 -*-
r"""跑 selftest 前把 highlight_errors 临时置 True（跑完原样还原）。

为什么需要：用户真实配置里 `custom/highlight_errors='false'`，而 selftest 的
前三条异常色断言在 :4608 之前就执行、要到 :4779 才把它设回 True —— 于是继承
false 全红（QA 已确认这是环境依赖，不是产品 bug）。这里在外面包一层：
全量快照 → 置 true → 跑 selftest（子进程）→ 还原快照。
"""
import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings  # noqa: E402

ORG, APP = "MimoWorks", "SerialAssistant"


def main() -> int:
    st = QSettings(ORG, APP)
    snap = {k: st.value(k) for k in st.allKeys()}
    print(f"[wrapper] snapshot {len(snap)} keys, "
          f"highlight_errors={st.value('custom/highlight_errors')!r}", flush=True)
    try:
        st.setValue("custom/highlight_errors", "true")
        st.sync()
        del st
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "windows"
        proc = subprocess.run(
            [sys.executable, "selftest.py"],
            cwd=_ROOT, env=env, capture_output=True, text=True, timeout=900)
        out = (proc.stdout or "") + (proc.stderr or "")
        print(out[-6000:], flush=True)
        print(f"[wrapper] selftest exit={proc.returncode}", flush=True)
        return proc.returncode
    finally:
        st2 = QSettings(ORG, APP)
        for k, v in snap.items():
            st2.setValue(k, v)
        st2.sync()
        del st2
        st3 = QSettings(ORG, APP)
        for k in st3.allKeys():
            if k not in snap:
                st3.remove(k)
        st3.sync()
        after = {k: st3.value(k) for k in st3.allKeys()}
        del st3
        print(f"[wrapper] restored {len(after)} keys, "
              f"highlight_errors={after.get('custom/highlight_errors')!r}, "
              f"vehicle={after.get('custom/vehicle')!r}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
