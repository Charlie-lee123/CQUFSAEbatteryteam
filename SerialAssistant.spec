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
}

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
    ],
    noarchive=False,
    optimize=0,
)

# 见上面 _DROP_BINARIES：Analysis 之后再滤二进制（hook 会把它们抓满）。
a.binaries = [entry for entry in a.binaries
              if os.path.basename(entry[0]).lower() not in _DROP_BINARIES]

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
