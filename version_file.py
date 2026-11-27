# -*- coding: utf-8 -*-
"""PyInstaller 的 Windows 版本资源模板（v3.1 起）。

为什么需要这个文件：``EXE()`` 不传 ``version=`` 时，PyInstaller 打出来的 exe
**没有版本资源块** —— Windows 属性页「详细信息」里 文件版本/产品版本 全是空，
任务管理器、卸载列表、第三方版本检测都读不到版本号。用户 2026-11-27 说
「现在还是 V3.0」，一部分原因就是根本没有可信的版本号可看。

⛔ 这里唯一的数字来源是 ``version.py``：本模板由 ``devtools/_gen_version_file.py``
   读 ``VERSION`` 后渲染，**不要在 .spec 里手写版本字符串**。
   实际构建时也可以直接调 ``render()``（见 SerialAssistant.spec 顶部）。
"""

# ⚠️ 本文件是**模板**，不是可执行脚本：`{version_tuple}` / `{version_str}` /
#    `{company}` 等占位符由 `render()` 替换。直接用双花括号转义需要的地方。

TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'080404b0',
        [StringStruct(u'CompanyName', u'{company}'),
         StringStruct(u'FileDescription', u'{description}'),
         StringStruct(u'FileVersion', u'{version_str}'),
         StringStruct(u'InternalName', u'SerialAssistant'),
         StringStruct(u'LegalCopyright', u'{copyright}'),
         StringStruct(u'OriginalFilename', u'SerialAssistant.exe'),
         StringStruct(u'ProductName', u'{product}'),
         StringStruct(u'ProductVersion', u'{version_str}')])
    ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""


def render(version: str) -> str:
    """把 ``"3.1"`` 渲染成版本资源源码字符串。

    ⚠️ ``filevers`` / ``prodvers`` 必须是**四元组整数**，少一位 PyInstaller 会
       报 "version tuple must have 4 elements"。``"3.1"`` → ``(3, 1, 0, 0)``。
    """
    parts = [int(p) for p in str(version).split(".") if p.isdigit()]
    while len(parts) < 4:
        parts.append(0)
    version_tuple = tuple(parts[:4])
    return TEMPLATE.format(
        version_tuple=version_tuple,
        version_str=str(version),
        company="重庆大学方程式赛车队 (CQUFSAE)",
        description="CQUFSAE 串口助手",
        product="CQUFSAE 串口助手",
        copyright="© 2026 CQUFSAE",
    )


if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from version import VERSION

    print(render(VERSION))