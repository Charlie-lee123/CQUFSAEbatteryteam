# -*- coding: utf-8 -*-
r"""把 ``docs/SOC估算技术说明.md`` 生成**完全离线自包含**的 HTML + PDF。

为什么不用外部库
----------------
项目 .venv 里没有 markdown / weasyprint / reportlab，为了一个文档装一堆依赖不值当。
这份 md 的语法很规整（标题 / 表格 / 围栏代码块 / 列表 / 引用 / 粗体 / 行内代码），
所以这里内置一个极简解析器；两份 Mermaid 图与 OCV 曲线改成**内联 SVG**，不引 CDN，
保证断网也能开、Edge 无头打印时不会有"图还没渲染就出 PDF"的时序问题。

PDF 走系统已装的 Edge： ``--headless=new --print-to-pdf``，中文字体直接用系统字体，
不下载、不安装任何东西。user-data-dir 落在项目 _check/ 下（不碰 C 盘 Temp）。

跑法： ``.venv/Scripts/python.exe devtools/_build_soc_doc.py``
产物： ``SOC/SOC估算技术说明.html`` / ``SOC/SOC估算技术说明.pdf``
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "SOC估算技术说明.md"
#: 交付物按用户要求单独放项目根目录下的 SOC/（Markdown 源仍留在 docs/）
OUT_DIR = ROOT / "SOC"
HTML_OUT = OUT_DIR / "SOC估算技术说明.html"
PDF_OUT = OUT_DIR / "SOC估算技术说明.pdf"
PROFILE = ROOT / "_check" / "edge_print_profile"
# ⚠️ 先让 Edge 打到一个临时文件，再 copyfile 覆盖目标。
#    直接 unlink 旧 PDF 会被本机 safe-delete 拦下（文件常被预览器占用），
#    直接 --print-to-pdf 覆盖也不可靠。copyfile 是纯写入，不触发删除。
TMP_PDF = ROOT / "_check" / f"soc_doc_print_{os.getpid()}.pdf"

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)

# ---------------------------------------------------------------- 内联 SVG

SVG_FLOW = """<svg viewBox="0 0 680 476" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="SOC 四层数据流">
<title>SOC 四层数据流</title>
<defs><marker id="ar1" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#888780" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<rect x="60" y="40" width="240" height="56" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
<text x="180" y="60" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#2C2C2A">串口原始报文</text>
<text x="180" y="79" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#5F5E5A">流式文本块，可能是半轮</text>
<path d="M180 96 L180 128" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar1)"/>
<rect x="60" y="130" width="240" height="56" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
<text x="180" y="150" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#042C53">① 拼成整轮</text>
<text x="180" y="169" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#185FA5">125 槽位，(模组, 序号) 定位</text>
<path d="M180 186 L180 218" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar1)"/>
<rect x="60" y="220" width="240" height="56" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
<text x="180" y="240" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#412402">② 清洗 + 回填</text>
<text x="180" y="259" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#854F0B">断线回填，模组偏移保真值</text>
<path d="M180 276 L180 308" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar1)"/>
<rect x="60" y="310" width="240" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
<text x="180" y="330" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#26215C">③ SOC 融合</text>
<text x="180" y="349" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#534AB7">积分 + κ 回拉 + 静置锚定</text>
<path d="M180 366 L180 398" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar1)"/>
<rect x="60" y="400" width="240" height="56" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
<text x="180" y="420" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#04342C">底栏正中央</text>
<text x="180" y="439" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">SOC 87.3% · 总电压 401.2 V</text>
<rect x="380" y="310" width="240" height="56" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
<text x="500" y="330" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#2C2C2A">按车号取参数</text>
<text x="500" y="349" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#5F5E5A">容量 / 串数 / 内阻 / OCV 表</text>
<path d="M380 338 L302 338" fill="none" stroke="#888780" stroke-width="1" stroke-dasharray="4 3" marker-end="url(#ar1)"/>
</svg>"""

SVG_STEPS = """<svg viewBox="0 0 680 268" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="update 每轮的六个阶段">
<title>SocEstimator.update 每轮的六个阶段</title>
<defs><marker id="ar2" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#888780" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<rect x="40" y="30" width="186" height="62" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
<text x="133" y="52" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#042C53">① 清洗与有效性</text>
<text x="133" y="72" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#185FA5">clean.ok / 电流 / 零漂</text>
<rect x="247" y="30" width="186" height="62" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
<text x="340" y="52" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#042C53">② 时间步长</text>
<text x="340" y="72" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#185FA5">dt &gt; 10 s 视为无此段</text>
<rect x="454" y="30" width="186" height="62" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
<text x="547" y="52" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#26215C">③ 冷启动 / 重锚</text>
<text x="547" y="72" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#534AB7">按 OCV 直接定初值</text>
<path d="M226 61 L245 61" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar2)"/>
<path d="M433 61 L452 61" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar2)"/>
<path d="M547 92 L547 110 L133 110 L133 128" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar2)"/>
<rect x="40" y="130" width="186" height="62" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
<text x="133" y="152" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#412402">④ 安时积分</text>
<text x="133" y="172" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#854F0B">骨架，短期准</text>
<rect x="247" y="130" width="186" height="62" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
<text x="340" y="152" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#412402">⑤ κ 加权回拉</text>
<text x="340" y="172" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#854F0B">residual 时跳过</text>
<rect x="454" y="130" width="186" height="62" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
<text x="547" y="152" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#412402">⑥ 静置软锚定</text>
<text x="547" y="172" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#854F0B">增益 0.3 / 封顶 ±15%</text>
<path d="M226 161 L245 161" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar2)"/>
<path d="M433 161 L452 161" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar2)"/>
<path d="M547 192 L547 205 L340 205 L340 216" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar2)"/>
<rect x="180" y="218" width="320" height="36" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
<text x="340" y="236" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#04342C">clamp(0, 100) → SocResult → 底栏正中</text>
</svg>"""

SVG_ARCH = """<svg viewBox="0 0 680 300" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="系统架构：电池箱经 BMS 与 CAN 把原始采样送到 ECU，ECU 算出 SOC 再经 CAN 送到 C125">
<title>系统架构：BMS 采集 → ECU 算 SOC → C125 显示</title>
<defs><marker id="ara" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#888780" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<rect x="24" y="108" width="104" height="84" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
<text x="76" y="132" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#2C2C2A">电池箱</text>
<text x="76" y="152" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#5F5E5A">125 串 NCM</text>
<text x="76" y="170" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#5F5E5A">12.041 Ah</text>
<path d="M128 150 L168 150" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ara)"/>
<rect x="172" y="84" width="156" height="132" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
<text x="250" y="110" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#042C53">BMS_Master</text>
<text x="250" y="132" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#185FA5">STM32F103 + LTC6820</text>
<text x="250" y="152" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#185FA5">采集 125 路电压</text>
<text x="250" y="170" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#185FA5">40 路温度 / 电流</text>
<text x="250" y="188" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#185FA5">接触器状态</text>
<text x="250" y="206" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#185FA5">SN65HVD230 (CAN)</text>
<path d="M328 150 L372 150" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ara)"/>
<rect x="376" y="128" width="86" height="44" rx="8" fill="#FAF6EC" stroke="#B59A3E" stroke-width="0.5"/>
<text x="419" y="150" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="500" fill="#6B5712">CAN 总线</text>
<text x="419" y="166" text-anchor="middle" dominant-baseline="central" font-size="10" fill="#8a7a3a">500k / 1M</text>
<path d="M462 150 L502 150" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ara)"/>
<rect x="506" y="84" width="156" height="132" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
<text x="584" y="110" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#26215C">ECU</text>
<text x="584" y="132" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#534AB7">跑 SOC 估算</text>
<text x="584" y="150" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#534AB7">安时积分 + 电压回拉</text>
<text x="584" y="168" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#534AB7">+ 静置软锚定</text>
<text x="584" y="186" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#534AB7">+ 零漂在线学习</text>
<text x="584" y="206" text-anchor="middle" dominant-baseline="central" font-size="11" fill="#534AB7">BMS 算力不足，移到此</text>
<path d="M506 150 L462 150" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ara)"/>
<rect x="376" y="208" width="86" height="44" rx="8" fill="#FAF6EC" stroke="#B59A3E" stroke-width="0.5"/>
<text x="419" y="230" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="500" fill="#6B5712">CAN 总线</text>
<text x="419" y="246" text-anchor="middle" dominant-baseline="central" font-size="10" fill="#8a7a3a">500k / 1M</text>
<path d="M328 230 L372 230" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ara)"/>
<rect x="24" y="208" width="104" height="44" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
<text x="76" y="230" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="500" fill="#04342C">C125 仪表</text>
<text x="76" y="246" text-anchor="middle" dominant-baseline="central" font-size="10" fill="#0F6E56">只显示 SOC</text>
</svg>"""

SVG_CLEAN = """<svg viewBox="0 0 680 382" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="清洗环节如何区分断线与整模组不均衡">
<title>清洗环节的两类故障分叉判据</title>
<defs><marker id="ar3" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M2 1L8 5L2 9" fill="none" stroke="#888780" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>
<rect x="210" y="40" width="260" height="44" rx="8" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
<text x="340" y="62" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#2C2C2A">125 节端电压</text>
<path d="M340 84 L340 102 M182 102 L497 102" fill="none" stroke="#888780" stroke-width="1.5"/>
<path d="M182 102 L182 118" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar3)"/>
<path d="M497 102 L497 118" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar3)"/>
<rect x="40" y="120" width="285" height="120" rx="8" fill="#FCEBEB" stroke="#A32D2D" stroke-width="0.5"/>
<text x="182" y="146" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#501313">孤立断线 → 回填</text>
<text x="182" y="176" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#A32D2D">1~2 节 / 缺失 / X / 超量程</text>
<text x="182" y="200" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#A32D2D">写健康均值，不二次稀释</text>
<rect x="355" y="120" width="285" height="120" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
<text x="497" y="146" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#412402">整模组不均衡 → 保真值</text>
<text x="497" y="176" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#854F0B">连续 ≥12 节同向偏离（量程内）</text>
<text x="497" y="200" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#854F0B">module_offset，界面加 ± 提示</text>
<path d="M182 240 L182 260 L270 260 L270 278" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar3)"/>
<path d="M497 240 L497 260 L410 260 L410 278" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ar3)"/>
<rect x="190" y="280" width="300" height="56" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
<text x="340" y="300" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#04342C">总压 = 健康均值 × 125</text>
<text x="340" y="319" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">|均值−中位数| &gt; 8 mV → 停电压回拉</text>
<text x="340" y="362" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#5F5E5A">关键：超量程（0 mV）一律回填，绝不会被当成模组偏移保留</text>
</svg>"""

#: 三个 mermaid 块按出现顺序对应（§2.1 数据流 / §4.2 清洗分叉 / §6.3 update 状态机）
MERMAID_SVGS = (SVG_FLOW, SVG_CLEAN, SVG_STEPS)
MERMAID_CAPTIONS = (
    "图 1：SOC 四层数据流",
    "图 2：清洗环节的两类故障分叉判据",
    "图 4：update() 每轮的六个阶段",
)

#: 具名 SVG：`` ```mermaid <key> ``` `` 可按 key 选用（ECU 操作文档会用到 arch）。
#: 默认（不带 key 的 `` ```mermaid ``` ``）仍按上面 MERMAID_SVGS 的顺序映射，保证
#: 既有 SOC 技术说明文档行为不变。
ALL_SVGS: dict[str, str] = {
    "arch": SVG_ARCH,
    "flow": SVG_FLOW,
    "clean": SVG_CLEAN,
    "steps": SVG_STEPS,
}
ALL_CAPTIONS: dict[str, str] = {
    "arch": "图 1：系统架构（电池箱 → BMS → CAN → ECU 算 SOC → CAN → C125）",
    "flow": "图 2：SOC 算法数据流（拼整轮 → 清洗回填 → 融合 → 输出）",
    "clean": "图 3：清洗环节区分断线与整模组不均衡",
    "steps": "图 4：update() 每轮的六个阶段",
}

#: 第 0 章的 ASCII 速览图 → 流程条（等宽字体里中文对不齐，换成 flex chip）
FLOW_STRIP = (
    '<div class="flowstrip">'
    "<span>串口文本</span><b>→</b><span>拼成整轮 (125 节)</span><b>→</b>"
    "<span>清洗 + 回填</span><b>→</b><span>均值 × 125 = 总压</span>"
    "</div>"
    '<p class="flowhint">安时积分（短期准）做骨架，OCV 查表 + κ 加权回拉（长期准）'
    "与静置软锚定共同收敛，输出 SOC 到底栏正中。</p>"
)

OCV_PTS = [
    (0, 3.0818), (2, 3.4516), (4, 3.5595), (6, 3.6099), (8, 3.6385),
    (10, 3.6581), (15, 3.6908), (20, 3.7128), (25, 3.7337), (30, 3.7582),
    (35, 3.7872), (40, 3.8208), (45, 3.8595), (50, 3.9038), (55, 3.9546),
    (60, 4.0118), (65, 4.0744), (70, 4.1400), (75, 4.2066), (80, 4.2695),
    (85, 4.3236), (90, 4.3672), (95, 4.4016), (97, 4.4155), (99, 4.4558),
    (100, 4.4981),
]


def _ocv_svg() -> str:
    x0, x1, y0, y1 = 68.0, 640.0, 30.0, 226.0
    vmin, vmax = 3.0, 4.55

    def px(soc: float) -> float:
        return x0 + soc / 100.0 * (x1 - x0)

    def py(v: float) -> float:
        return y1 - (v - vmin) / (vmax - vmin) * (y1 - y0)

    parts = [
        '<svg viewBox="0 0 680 286" xmlns="http://www.w3.org/2000/svg" role="img" '
        'aria-label="E74 实测 OCV-SOC 曲线，10 到 30% 为平台段">',
        "<title>E74 实测 OCV-SOC 曲线</title>",
        f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" fill="#FBFAF7" stroke="#D3D1C7" stroke-width="0.5"/>',
    ]
    for v in (3.0, 3.3, 3.6, 3.9, 4.2, 4.5):
        yy = py(v)
        parts.append(f'<line x1="{x0}" y1="{yy:.1f}" x2="{x1}" y2="{yy:.1f}" stroke="#E6E3DA" stroke-width="0.5"/>')
        parts.append(f'<text x="{x0-8}" y="{yy:.1f}" text-anchor="end" dominant-baseline="central" font-size="11" fill="#5F5E5A">{v:.1f}</text>')
    for soc in range(0, 101, 20):
        xx = px(soc)
        parts.append(f'<line x1="{xx:.1f}" y1="{y0}" x2="{xx:.1f}" y2="{y1}" stroke="#E6E3DA" stroke-width="0.5"/>')
        parts.append(f'<text x="{xx:.1f}" y="{y1+16}" text-anchor="middle" font-size="11" fill="#5F5E5A">{soc}</text>')
    poly = " ".join(f"{px(s):.1f},{py(v):.1f}" for s, v in OCV_PTS)
    parts.append(f'<polyline points="{poly}" fill="none" stroke="#185FA5" stroke-width="2" stroke-linejoin="round"/>')
    flat = " ".join(f"{px(s):.1f},{py(v):.1f}" for s, v in OCV_PTS if 10 <= s <= 30)
    parts.append(f'<polyline points="{flat}" fill="none" stroke="#BA7517" stroke-width="3.5" stroke-linejoin="round"/>')
    parts.append(f'<line x1="{px(10):.1f}" y1="{y0}" x2="{px(10):.1f}" y2="{y1}" stroke="#BA7517" stroke-width="0.5" stroke-dasharray="3 3"/>')
    parts.append(f'<line x1="{px(30):.1f}" y1="{y0}" x2="{px(30):.1f}" y2="{y1}" stroke="#BA7517" stroke-width="0.5" stroke-dasharray="3 3"/>')
    parts.append(f'<text x="{(px(10)+px(30))/2:.1f}" y="{y0+16}" text-anchor="middle" font-size="11" fill="#854F0B">平台段 κ≈0.1</text>')
    parts.append('<text x="354" y="266" text-anchor="middle" font-size="11" fill="#5F5E5A">SOC (%)</text>')
    parts.append('<text x="18" y="128" text-anchor="middle" font-size="11" fill="#5F5E5A">OCV (V)</text>')
    parts.append('<line x1="420" y1="256" x2="444" y2="256" stroke="#185FA5" stroke-width="2"/>')
    parts.append('<text x="450" y="256" dominant-baseline="central" font-size="11" fill="#5F5E5A">实测曲线</text>')
    parts.append('<line x1="530" y1="256" x2="554" y2="256" stroke="#BA7517" stroke-width="3.5"/>')
    parts.append('<text x="560" y="256" dominant-baseline="central" font-size="11" fill="#5F5E5A">平台</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- 行内格式

def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    held: list[str] = []

    def hold(m: re.Match) -> str:
        held.append("<code>" + m.group(1) + "</code>")
        return f"\x00{len(held) - 1}\x00"

    text = re.sub(r"`([^`]+)`", hold, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: held[int(m.group(1))], text)


def is_table_sep(line: str) -> bool:
    return bool(re.fullmatch(r"\|[\s:\-|]+\|", line.strip())) and "-" in line


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


# ---------------------------------------------------------------- 主体转换

def convert(md: str,
            svg_map: dict[str, tuple[str, str]] | None = None
            ) -> tuple[str, list[tuple[str, str]]]:
    lines = md.split("\n")
    out: list[str] = []
    toc: list[tuple[str, str]] = []
    i = 0
    mermaid_no = 0
    h2_no = 0
    para: list[str] = []

    def flush_para() -> None:
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_para()
            lang = stripped[3:].strip().lower()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            if lang == "mermaid" or lang.startswith("mermaid "):
                key = lang.split(" ", 1)[1].strip() if lang.startswith("mermaid ") else None
                if key and svg_map and key in svg_map:
                    svg, cap = svg_map[key]
                else:
                    idx = min(mermaid_no, len(MERMAID_SVGS) - 1)
                    svg, cap = MERMAID_SVGS[idx], MERMAID_CAPTIONS[idx]
                out.append(f'<figure class="fig">{svg}<figcaption>{cap}</figcaption></figure>')
                mermaid_no += 1
            elif body and body[0].lstrip().startswith("串口文本"):
                # 这段 ASCII 流程图在等宽字体里中英文混排会对不齐，换成流程条
                out.append(FLOW_STRIP)
            else:
                label = lang.upper() if lang else "TEXT"
                out.append(f'<div class="codewrap"><span class="codelabel">{label}</span>'
                           "<pre><code>" + html.escape("\n".join(body), quote=False)
                           + "</code></pre></div>")
            continue

        if not stripped:
            flush_para()
            i += 1
            continue

        if re.fullmatch(r"-{3,}", stripped):
            flush_para()
            out.append("<hr>")
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            flush_para()
            level = len(m.group(1))
            title = m.group(2).strip()
            if level == 2:
                h2_no += 1
                sid = f"sec{h2_no}"
                toc.append((sid, title))
                out.append(f'<h2 id="{sid}">{inline(title)}</h2>')
            elif level == 1:
                out.append(f"<h1>{inline(title)}</h1>")
            else:
                out.append(f"<h{level}>{inline(title)}</h{level}>")
            # 5.4 节后插 OCV 曲线图（正文讲了 κ，这里配实测曲线）
            if level == 3 and title.startswith("5.4"):
                out.append('<figure class="fig">' + _ocv_svg()
                           + "<figcaption>图 3：E74 实测 OCV–SOC 曲线与 10~30% 平台段</figcaption></figure>")
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and is_table_sep(lines[i + 1]):
            flush_para()
            head = cells(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>")
            for row in rows:
                row += [""] * (len(head) - len(row))
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row[:len(head)]) + "</tr>")
            out.append("</tbody></table>")
            continue

        if stripped.startswith(">"):
            flush_para()
            quote: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + "<br>".join(inline(q) for q in quote) + "</blockquote>")
            continue

        if re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            flush_para()
            ordered = bool(re.match(r"^\d+\.\s+", stripped))
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while i < len(lines):
                cur = lines[i].strip()
                if not (re.match(r"^[-*]\s+", cur) or re.match(r"^\d+\.\s+", cur)):
                    break
                items.append(re.sub(r"^(?:[-*]|\d+\.)\s+", "", cur))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue

        para.append(stripped)
        i += 1

    flush_para()
    return "\n".join(out), toc


CSS = """
:root{--ink:#24242a;--muted:#5f5e5a;--line:#d9d7cf;--soft:#f5f4ef;--blue:#185fa5;--amber:#854f0b}
*{box-sizing:border-box}
body{margin:0;padding:0;background:#eeece6;color:var(--ink);
 font-family:"Microsoft YaHei","PingFang SC","Hiragino Sans GB","Segoe UI",sans-serif;
 font-size:10.5pt;line-height:1.78;-webkit-font-smoothing:antialiased}
.page{max-width:900px;margin:0 auto;background:#fff;padding:52px 64px 72px;box-shadow:0 1px 12px rgba(0,0,0,.08)}
h1{font-size:22pt;font-weight:600;margin:0 0 6px;letter-spacing:.4px;line-height:1.35}
h2{font-size:15.5pt;font-weight:600;margin:34px 0 14px;padding:0 0 8px 12px;
 border-bottom:1px solid var(--line);border-left:4px solid var(--blue);
 break-after:avoid;page-break-after:avoid}
h3{font-size:12.5pt;font-weight:600;margin:22px 0 10px;color:#0c447c;
 break-after:avoid;page-break-after:avoid}
h4{font-size:11pt;font-weight:600;margin:16px 0 8px;break-after:avoid}
p{margin:0 0 11px;text-align:justify}
strong{font-weight:600}
a{color:var(--blue);text-decoration:none}
hr{border:0;border-top:1px solid var(--line);margin:26px 0}
code{font-family:Consolas,"Cascadia Mono","Courier New",monospace;font-size:.9em;
 background:#f0efe9;color:#8a3a1a;padding:1px 4px;border-radius:3px}
.codewrap{position:relative;margin:14px 0 18px;break-inside:avoid;page-break-inside:avoid}
.codelabel{position:absolute;top:6px;right:10px;font-size:8pt;letter-spacing:.6px;color:#9a988f}
pre{margin:0;background:#f7f6f2;border:1px solid var(--line);border-left:3px solid #b7b4a9;
 border-radius:5px;padding:16px 16px 14px;overflow-x:auto;white-space:pre-wrap;word-break:break-word}
pre code{background:none;color:#2c2c2a;padding:0;font-size:8.6pt;line-height:1.6}
table{width:100%;border-collapse:collapse;margin:14px 0 20px;font-size:9.1pt;
 break-inside:avoid;page-break-inside:avoid}
th,td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top;line-height:1.55}
th{background:#eef1f4;font-weight:600;color:#0c447c}
tbody tr:nth-child(even){background:#faf9f6}
blockquote{margin:14px 0 20px;padding:11px 16px;background:#f3f6fa;
 border-left:3px solid var(--blue);border-radius:0 5px 5px 0;color:#3c4a58;font-size:9.8pt}
ul,ol{margin:0 0 13px;padding-left:24px}
li{margin:3px 0}
.fig{margin:18px 0 22px;padding:14px 12px 10px;border:1px solid var(--line);border-radius:6px;
 background:#fdfdfb;text-align:center;break-inside:avoid;page-break-inside:avoid}
.fig svg{display:block;width:100%;max-width:680px;height:auto;margin:0 auto;
 font-family:"Microsoft YaHei","Segoe UI",sans-serif}
figcaption{margin-top:8px;font-size:8.8pt;color:var(--muted)}
.cover{border-bottom:2px solid var(--blue);padding-bottom:18px;margin-bottom:22px}
.sub{color:var(--muted);font-size:9.6pt;margin-top:8px}
.badge{display:inline-block;background:#eef1f4;color:#0c447c;border-radius:4px;
 padding:2px 9px;margin-right:7px;font-size:8.8pt}
.toc{background:var(--soft);border:1px solid var(--line);border-radius:6px;padding:16px 22px;margin:0 0 26px;
 break-inside:avoid;page-break-inside:avoid}
.toc h2{border:0;border-left:0;padding:0;margin:0 0 10px;font-size:12pt;color:#0c447c}
.toc ol{columns:2;column-gap:28px;margin:0;padding-left:20px;font-size:9.4pt}
.toc li{margin:3px 0;break-inside:avoid}
.flowstrip{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:14px 0 8px;
 padding:14px 16px;background:#f3f6fa;border:1px solid var(--line);border-radius:6px;
 font-size:9.6pt;break-inside:avoid;page-break-inside:avoid}
.flowstrip span{background:#fff;border:1px solid #c9d6e2;border-radius:4px;padding:4px 10px;white-space:nowrap}
.flowstrip b{color:var(--blue);font-weight:500}
.flowhint{margin:0 0 16px;font-size:9.4pt;color:var(--muted)}
.foot{margin-top:36px;padding-top:12px;border-top:1px solid var(--line);
 font-size:8.6pt;color:var(--muted);text-align:center}
@media print{
 body{background:#fff;font-size:9.6pt}
 .page{max-width:none;margin:0;padding:0;box-shadow:none}
 h2{margin-top:22px}
 pre,table,.fig,blockquote{break-inside:avoid;page-break-inside:avoid}
 a{color:var(--ink)}
}
@page{size:A4;margin:15mm 14mm 16mm}
"""


def build_html(src: Path, title: str, sub: str, badges: str,
               src_name: str = "",
               svg_map: dict[str, tuple[str, str]] | None = None) -> str:
    """把一份 markdown 渲染成完整 HTML。

    ``badges`` 是已经拼好的 ``<span class="badge">…</span>`` 片段（允许为空）。
    ``src_name`` 仅用于页脚标注来源文件名。
    ``svg_map`` 是 ``{key: (svg, caption)}``，供 `` ```mermaid <key> ``` `` 选用。
    """
    lines = src.read_text(encoding="utf-8").split("\n")
    # 封面已承载标题与元信息：去掉 md 开头的 H1、紧随的引用块、分隔线与空行
    while lines and (not lines[0].strip()
                     or lines[0].startswith("# ")
                     or lines[0].lstrip().startswith(">")
                     or re.fullmatch(r"-{3,}", lines[0].strip())):
        lines.pop(0)
    body, toc = convert("\n".join(lines), svg_map=svg_map)
    items = "".join(f'<li><a href="#{sid}">{html.escape(title)}</a></li>' for sid, title in toc)
    head = (
        f'<div class="cover"><h1>{inline(title)}</h1>'
        f'<div class="sub">{inline(sub)}</div>'
        + (f'<div class="sub">{badges}</div>' if badges else "")
        + "</div>"
    )
    toc_html = f'<nav class="toc"><h2>目录</h2><ol>{items}</ol></nav>'
    foot = (f'本文档由 {src_name or src.name} 生成 · 全部图表为内联矢量图，可离线阅读'
            if src_name else "本文档由源 markdown 生成 · 全部图表为内联矢量图，可离线阅读")
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{inline(title)}</title>"
        f"<style>{CSS}</style></head><body><main class=\"page\">"
        + head + toc_html + body
        + f'<div class="foot">{foot}</div>'
        + "</main></body></html>"
    )


def build_pdf(html_out: Path, pdf_out: Path) -> Path | None:
    """返回**实际写入**的 PDF 路径（目标被占用时是备用名），失败返回 None。"""
    edge = next((p for p in EDGE_CANDIDATES if Path(p).exists()), None)
    if not edge:
        print("未找到 Edge / Chrome，跳过 PDF")
        return None
    PROFILE.mkdir(parents=True, exist_ok=True)
    TMP_PDF.parent.mkdir(parents=True, exist_ok=True)
    uri = html_out.resolve().as_uri()
    alt_pdf = pdf_out.with_name(pdf_out.stem + "_新版" + pdf_out.suffix)
    cmd = [
        edge, "--headless=new", "--disable-gpu", "--disable-extensions",
        "--no-first-run", "--no-default-browser-check",
        f"--user-data-dir={PROFILE}", "--no-pdf-header-footer",
        "--print-to-pdf-no-header", "--virtual-time-budget=4000",
        f"--print-to-pdf={TMP_PDF}", uri,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    actual: Path | None = None
    if TMP_PDF.exists() and TMP_PDF.stat().st_size > 30_000:
        try:
            shutil.copyfile(TMP_PDF, pdf_out)   # 覆盖写入，不删旧文件
            actual = pdf_out
        except PermissionError:
            # 目标被 PDF 阅读器 / 预览面板独占时，覆盖、替换、移动都会失败，
            # 而本机 safe-delete 又禁止删除。⛔ 不杀用户进程，改落备用名并
            # 明确回报，等占用释放后重跑本脚本即可写回原名。
            shutil.copyfile(TMP_PDF, alt_pdf)
            actual = alt_pdf
            print(f"⚠️ 原 PDF 被其它进程占用，本次另存为 {alt_pdf.name}；"
                  "关闭阅读器后重跑本脚本即可写回原名")
    if actual is None:
        print("Edge 返回码:", proc.returncode)
        print((proc.stderr or "")[-1200:])
    return actual


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把 markdown 技术文档转成离线自包含 HTML + PDF（Edge 无头打印）")
    ap.add_argument("--src", default=str(SRC), help="源 markdown 路径")
    ap.add_argument("--html", default=str(HTML_OUT), help="输出 HTML 路径")
    ap.add_argument("--pdf", default=str(PDF_OUT), help="输出 PDF 路径")
    ap.add_argument("--title", default="E74 电池 SOC 估算 · 技术实现说明")
    ap.add_argument("--sub",
                    default="SerialAssistant 2.7 · 2026 中国大学生方程式 · 重庆大学方程式赛车队 E74")
    ap.add_argument("--badges", default="125 串 / 12.041 Ah|高电压 NCM|2026-11-10",
                    help="封面徽章，用 | 分隔；留空则不要徽章")
    ap.add_argument("--svg-map", default="",
                    help="具名 SVG 白名单，逗号分隔（arch/flow/clean/steps）；用于 ECU 操作文档")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.exists():
        print("找不到源文档:", src)
        return 2
    Path(args.html).parent.mkdir(parents=True, exist_ok=True)
    badges_html = ("".join(f'<span class="badge">{b}</span>' for b in args.badges.split("|"))
                   if args.badges else "")
    svg_map = None
    if args.svg_map:
        svg_map = {k: (ALL_SVGS[k], ALL_CAPTIONS[k])
                   for k in args.svg_map.split(",") if k in ALL_SVGS}
    html_text = build_html(src, args.title, args.sub, badges_html,
                           src_name=src.name, svg_map=svg_map)
    html_out = Path(args.html)
    html_out.write_text(html_text, encoding="utf-8")
    print(f"HTML: {html_out}  {html_out.stat().st_size:,} B")

    actual_pdf = build_pdf(html_out, Path(args.pdf))
    if actual_pdf is not None:
        data = actual_pdf.read_bytes()
        pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
        print(f"PDF : {actual_pdf}  {actual_pdf.stat().st_size:,} B  约 {pages} 页")
        ok = True
    else:
        print("PDF : 生成失败")
        ok = False
    # 简单自检：HTML 不该残留未替换的 mermaid / markdown 表格分隔行
    bad = [x for x in ("```mermaid", "|---", "flowchart TD") if x in html_text]
    print("残留检查:", "干净" if not bad else f"发现 {bad}")
    print("IS_PASS:", "YES" if ok and not bad else "NO")
    return 0 if ok and not bad else 1


if __name__ == "__main__":
    sys.exit(main())
