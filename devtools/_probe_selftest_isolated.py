# -*- coding: utf-8 -*-
"""证明 ``selftest.py --list`` / 参数错误路径**不构造 QApplication、不碰 QSettings**。

做法：导入 selftest 之后把 ``QApplication`` 与 ``QSettings`` 都换成"一构造就抛"
的炸弹，再调 ``main()``。若选集解析真的在 GUI 之前、且不读配置，两条路径都必须
正常返回；反过来任何一处偷懒构造都会立刻炸出断言。

用法：
    .venv\\Scripts\\python.exe devtools\\_probe_selftest_isolated.py
"""
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import selftest as T                                            # noqa: E402


class _Boom:
    def __init__(self, *args, **kwargs):
        raise AssertionError("不该构造：" + type(self).__name__)


def _run(argv, patch):
    """按给定 argv 调 main()，返回 (退出码, stdout)。"""
    saved_argv = sys.argv
    saved = (T.QApplication, T.QSettings)
    if patch:
        T.QApplication = _Boom
        T.QSettings = _Boom
    buf = io.StringIO()
    try:
        sys.argv = ["selftest.py", *argv]
        with redirect_stdout(buf):
            code = T.main()
    finally:
        sys.argv = saved_argv
        T.QApplication, T.QSettings = saved
    return code, buf.getvalue()


def main() -> int:
    code, out = _run(["--list"], patch=True)
    assert code == 0, code
    assert len([ln for ln in out.splitlines() if ln[:2].strip().isdigit()]) >= 41, out
    print(f"  PASS  --list 未构造 QApplication/QSettings，退出码 {code}")

    code, out = _run(["--only", "不存在的关键词"], patch=True)
    assert code == 2, code
    assert "没有匹配的 check" in out, out
    print(f"  PASS  --only 无匹配 未构造 QApplication，退出码 {code}")

    code, out = _run(["--only"], patch=True)
    assert code == 2, code
    assert "参数错误" in out, out
    print(f"  PASS  --only 缺参数 未构造 QApplication，退出码 {code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())