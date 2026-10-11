@echo off
rem 推送 main 与标签到 GitHub。
rem 双击运行即可；如弹出 GitHub 登录/授权窗口，完成授权就会继续。
chcp 65001 >nul
cd /d "%~dp0"
set "GITEXE=D:\Git\cmd\git.exe"
if not exist "%GITEXE%" set "GITEXE=git"

echo 正在推送 main 与所有标签（如弹出 GitHub 登录/授权窗口，请完成授权）...
"%GITEXE%" push origin main --follow-tags
if errorlevel 1 (
    echo.
    echo 推送失败。常见原因：
    echo   - 授权窗口没完成（重跑一次本脚本）
    echo   - 代理没开：本仓库已配置走 127.0.0.1:7897，请确认 Clash 在运行
    pause
    exit /b 1
)
echo.
echo 完成 ✓ https://github.com/Charlie-lee123/CQUFSAEbatteryteam
pause
