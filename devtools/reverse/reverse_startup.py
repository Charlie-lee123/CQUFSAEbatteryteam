"""反向验证补丁：故意破坏「启动时最大化」的实现，看自测是否如实挂掉。

用法（在副本目录里跑）：
    python reverse_startup.py default    # 注册表的默认值改成 False
    python reverse_startup.py ignore     # apply_startup_state 忽略配置，恒全屏
    python reverse_startup.py keepwant   # 关掉分支不降「想要全屏」
    python reverse_startup.py nolive     # 面板拨动之后不立刻应用

⚠️ 补丁一律写成**文件**再执行 —— `python -c "...\n..."` 里的反斜杠会被
Windows 的参数转换吃掉，带换行的替换永远不匹配（踩过一次）。
"""

import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "default"


def patch(path: str, old: str, new: str) -> None:
    src = open(path, encoding="utf-8").read().replace("\r\n", "\n")
    assert src.count(old) == 1, f"{path}: 匹配到 {src.count(old)} 处，应当只有 1 处"
    open(path, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))


if MODE == "default":
    patch("customization.py",
          'register_bool("startup_maximized", "启动时最大化", True,',
          'register_bool("startup_maximized", "启动时最大化", False,')

elif MODE == "ignore":
    patch("main_window.py",
          '        if customization.get("startup_maximized"):\n'
          '            self.startup_fullscreen()\n'
          '        else:\n'
          '            self._want_fullscreen = False\n'
          '            self._refresh_caption_ui()\n',
          '        self.startup_fullscreen()       # REVERSE: 忽略配置，恒全屏\n')

elif MODE == "keepwant":
    patch("main_window.py",
          '        if customization.get("startup_maximized"):\n'
          '            self.startup_fullscreen()\n'
          '        else:\n'
          '            self._want_fullscreen = False\n'
          '            self._refresh_caption_ui()\n',
          '        if customization.get("startup_maximized"):\n'
          '            self.startup_fullscreen()\n'
          '        else:\n'
          '            self._refresh_caption_ui()   # REVERSE: 不降「想要全屏」\n')

elif MODE == "nolive":
    patch("main_window.py",
          '        if customization.get("startup_maximized"):\n'
          '            if not self.isFullScreen() and not self._expanded:\n'
          '                self.startup_fullscreen()\n'
          '        else:\n'
          '            self._want_fullscreen = False\n'
          '            if self.isFullScreen() or self._expanded:\n'
          '                self._on_escape()\n'
          '        self._refresh_caption_ui()\n',
          '        self._refresh_caption_ui()       # REVERSE: 拨了也不当场应用\n')

else:
    raise SystemExit(f"未知模式 {MODE}")

print(f"reverse '{MODE}' applied")
