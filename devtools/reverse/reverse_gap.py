"""反向验证补丁：把「全屏必须方角」的修复回退，看探针能否测出来。

用法（在副本目录里跑）：python reverse_gap.py round
"""

import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "round"


def patch(path, old, new):
    src = open(path, encoding="utf-8").read().replace("\r\n", "\n")
    assert src.count(old) == 1, f"{path}: 匹配 {src.count(old)} 处，应当只有 1 处"
    open(path, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))


if MODE == "round":
    # 1) 调用点退回"无条件圆角"
    patch("main_window.py",
          '            apply_titlebar_theme(self, scheme, rounded=not self.isFullScreen())',
          '            apply_titlebar_theme(self, scheme)     # REVERSE: 无条件圆角')
    # 2) 去掉"换主题后重新钉一遍"的兜底
    patch("main_window.py",
          '        if self.isFullScreen() and not self._expect_normal:\n'
          '            self._enforce_native_fullscreen()\n',
          '        pass        # REVERSE: 去掉换主题后的兜底\n')
else:
    raise SystemExit(f"未知模式 {MODE}")

print(f"reverse '{MODE}' applied")
