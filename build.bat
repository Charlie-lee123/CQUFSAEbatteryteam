@echo off
rem 打包成免安装单文件 exe（构建配方在 SerialAssistant.spec）。
rem ⛔ 别再用 pyinstaller 命令行参数现拼：spec 里有 2026-10-17 的运行时裁剪
rem    （剔除 opengl32sw / Qt6Quick+Qml / Qt6Pdf 等，52.6MB→35.2MB），
rem    命令行打法会把它们全带回来，包体直接翻倍。
rem 产物落在 dist\，再复制进「发布」文件夹（发布脚本 / 安装包都从这里取）。
cd /d "%~dp0"
".venv\Scripts\python.exe" -m PyInstaller SerialAssistant.spec --noconfirm || exit /b 1
copy /y "dist\SerialAssistant.exe" "发布\SerialAssistant.exe" || exit /b 1
rem 桌面快捷方式跟着产物走：产物一挪目录，老快捷方式就静默失效（双击打不开），
rem 而 Windows 不会自愈它。这一步把它重新指到刚打出来的 exe 上（名字/位置不变，
rem 没有就创建一条）。
".venv\Scripts\python.exe" "devtools\_desktop_shortcut.py"
echo.
echo 打包完成: 发布\SerialAssistant.exe
pause
