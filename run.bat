@echo off
rem 启动串口调试助手。pythonw 不带控制台窗口。
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo 找不到虚拟环境，请先执行:
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" main.py
