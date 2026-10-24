# -*- mode: python ; coding: utf-8 -*-

import os

# ---------------------------------------------------------------------------
# 运行时裁剪（2026-10-17）
#
# PySide6 的官方 hook 是"按依赖全量收"的：只要 import 了 QtGui / QtWidgets，
# 它会把 Qt 的**整套**运行时（QML 引擎、Quick、Pdf、软件 OpenGL 兜底…）一起塞进包。
# 本项目只用 Core / Gui / Widgets / Network / Svg 五个模块，其余是纯负担 ——
# 裁前 56 个二进制、~90MB（exe 52.5MB），其中 41MB 来自下面这几块：
#
#   opengl32sw.dll     20.6MB  软件 OpenGL 兜底（无 OpenGL 绘制 → 永不加载）
#   Qt6Quick + Qml*    13.7MB  QML 引擎（一行 QML 都没有）
#   Qt6Pdf.dll          4.6MB  PDF 渲染
#   Qt6OpenGL.dll       2.0MB  同上
#   qtiff / qwebp       1.6MB  只用到 png / ico / svg / jpeg
#
# ⛔ 删的是**二进制**而不是 Python 模块：这些 DLL 是被插件依赖带进来的，
#    光在 excludes 里点名拦不住 → Analysis 之后对 a.binaries 过一遍过滤。
# ⛔ 平台插件（qwindows / qdirect2d）、TLS 后端（qopenssl / qschannel）、
#    libcrypto / libssl **一个都不能动** —— 窗口和联网更新全靠它们。
#    已核实：引用上述 DLL 的只有 qml/quick 系列插件（本项目根本不收它们）。
_DROP_BINARIES = {
    'opengl32sw.dll',
    'qt6quick.dll', 'qt6quickwidgets.dll', 'qt6qml.dll', 'qt6qmlmodels.dll',
    'qt6qmlmeta.dll', 'qt6qmlworkerscript.dll', 'qt6virtualkeyboard.dll',
    'qt6pdf.dll', 'qt6opengl.dll',
    'qtiff.dll', 'qwebp.dll',
    # ---- 2026-10-22（2.3 瘦身）----
    # 平台插件只留 qwindows：direct2d 是**可选**平台（Qt 默认走 windows），
    # minimal/offscreen 只有显式设置 QT_QPA_PLATFORM 才会用到，产品从不设。
    'qdirect2d.dll', 'qminimal.dll', 'qoffscreen.dll',
    # 图片格式只留 png（内置）/ ico / svg / jpeg：gif/icns/tga/wbmp/pdf 全没用过
    'qgif.dll', 'qicns.dll', 'qtga.dll', 'qwbmp.dll', 'qpdf.dll',
    # 输入法/触摸/原生样式：程序启动时 app.setStyle("Fusion")，这三类都不加载
    'qtuiotouchplugin.dll', 'qtvirtualkeyboardplugin.dll',
    'qmodernwindowsstyle.dll',
    # ---- 2026-10-24（2.4 瘦身）----
    # ① Qt 自带的中文/多语翻译库 qt_*.qm：产品界面全是自己画的，**从来没装过
    #    QTranslator**（grep 过），这些 .qm 一个都不会被加载 → 全删。
    # ② Qt 的 **OpenSSL 后端**（qopensslbackend + libcrypto-3-x64 + libssl-3-x64）：
    #    Windows 上 Qt 默认走系统自带的 **Schannel**，qopensslbackend 只有显式
    #    指定 QT_TLS_BACKEND=openssl 才会加载。删掉这三块省 6.8MB。
    #    ⛔ 删完**必须在产物上验联网**：`SerialAssistant.exe --check-net`
    #    （用 QNetworkAccessManager 取一次 HTTPS，结果写 net_check.txt）——
    #    这是唯一有联网风险的裁剪，源码里跑 selftest 验证不了它。
    #    ⚠️ Python 自己的 libcrypto-3.dll / libssl-3.dll（不带 -x64）**不能删**：
    #    那是 CPython 的 ssl 模块用的，两者名字不同、来源不同。
    'qopensslbackend.dll', 'libcrypto-3-x64.dll', 'libssl-3-x64.dll',
    # ③ JPEG 解码插件：assets 里只有 ico / png / svg，程序不接受用户图片，
    #    按文件名解码的路径根本不存在（png/ico 由 Qt 内置处理）。
    'qjpeg.dll',
    # ④ 网络状态查询插件（QNetworkInformation 专用）：全项目没调用过这个 API，
    #    Qt 也不会自动加载它（只在显式 backend 查询时才载入）。
    'qnetworklistmanager.dll',
}

# 数据文件层同样要减：Qt 的翻译目录整块不要（见上"从来没装过 QTranslator"）。
_DROP_DATA_DIRS = {'pyside6/translations'}

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    # mcp/ 目录要跟着走：设置面板「AI 接入」复制的那段接入包靠它自包含
    #（源码在包内，Agent 不需要联网 clone 仓库）。见 mcp_bridge.py。
    datas=[('assets', 'assets'), ('mcp/serial_assistant_mcp.py', 'mcp')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # ---- 没用到的 Qt 模块（Python 侧；顺带阻止 hook 再拉它们的依赖）----
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineQuick', 'PySide6.Qt3DCore', 'PySide6.QtMultimedia',
        'PySide6.QtCharts', 'PySide6.QtDataVisualization',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets',
        'PySide6.QtQuick3D', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtSql',
        'PySide6.QtXml', 'PySide6.QtTest', 'PySide6.QtDBus',
        'PySide6.QtPrintSupport', 'PySide6.QtUiTools', 'PySide6.QtHelp',
        'PySide6.QtDesigner', 'PySide6.QtBluetooth', 'PySide6.QtPositioning',
        # ---- 标准库里明显用不到的大件 ----
        'tkinter', 'unittest', 'pydoc', 'doctest', 'sqlite3', 'xmlrpc',
        'distutils', 'setuptools', 'pip',
        # ---- 2026-10-24（2.4 二次瘦身；每条都查过 import 链）----
        # tarfile 只被 `shutil.unpack_archive` 引用（本项目只用 shutil.copyfile
        # 干"覆盖 exe"那件事，从不解压），连带 _lzma/_bz2 一起省 0.36MB。
        'tarfile', 'lzma', '_lzma', 'bz2', '_bz2',
        # unicodedata（1.1MB）：唯一引用链是 pathlib→urllib.parse→unicodedata，
        # 而 urllib.parse 里那是**函数内**的 import（只在解析非 ASCII 主机名时才走）。
        # 本程序所有 URL 都是 ASCII 常量、且从不解析用户输入的主机名 → 不会触发。
        'unicodedata',
        # 注：decimal/_decimal（0.28MB）**不能删** —— random→statistics→fractions→
        #     decimal 是一条真实链，random 是刚需。
    ],
    noarchive=False,
    # 1 = 去掉 assert 与 __debug__ 分支（产品代码没有依赖 assert 的逻辑）
    optimize=0,
)

# 见上面 _DROP_BINARIES：Analysis 之后再滤二进制（hook 会把它们抓满）。
a.binaries = [entry for entry in a.binaries
              if os.path.basename(entry[0]).lower() not in _DROP_BINARIES]
# datas 里也要滤（translations 是目录形式进来的，按规范化路径前缀匹配）
a.datas = [entry for entry in a.datas
           if not any(str(entry[0]).replace("\\", "/").lower().startswith(d)
                      for d in _DROP_DATA_DIRS)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SerialAssistant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/app.ico'],
)
