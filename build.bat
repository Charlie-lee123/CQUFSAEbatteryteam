@echo off
rem 打包成免安装单文件 exe。
rem 产物落在「发布」文件夹里：发布\SerialAssistant.exe —— 名字一眼能认，
rem 要发给别人时整个文件夹拖过去，或只把那个 exe 发出去都行（单文件免安装）。
rem 注意 --add-data：车队队标等素材必须打进去，否则换台机器上就只剩文字没有品牌元素
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pip install pyinstaller || exit /b 1
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean ^
    --onefile --windowed ^
    --distpath "发布" ^
    --name "SerialAssistant" ^
    --icon "assets\app.ico" ^
    --add-data "assets;assets" ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.QtWebEngineQuick ^
    --exclude-module PySide6.Qt3DCore ^
    --exclude-module PySide6.QtMultimedia ^
    --exclude-module PySide6.QtCharts ^
    --exclude-module PySide6.QtDataVisualization ^
    --exclude-module tkinter ^
    main.py || exit /b 1
rem 旧版默认输出目录（dist）已经不用了，留着会让人分不清哪份是新的
if exist "dist" rmdir /s /q "dist"
rem 桌面快捷方式跟着产物走：产物一挪目录，老快捷方式就静默失效（双击打不开），
rem 而 Windows 不会自愈它。这一步把它重新指到刚打出来的 exe 上（名字/位置不变，
rem 没有就创建一条）。
".venv\Scripts\python.exe" "devtools\_desktop_shortcut.py"
echo.
echo 打包完成: 发布\SerialAssistant.exe
pause
