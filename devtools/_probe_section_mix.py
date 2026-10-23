"""拿真实历史数据回放段状态机，找"电压行被当成温度判"的行。

判据：一行是**纯 4 位小数采样行**（电压格式）却落在 TEMP 段 → 会被温度阈值判色
（3858 > 60 → 整排标黄）；反过来，纯 2 位小数行落在 VOLT 段同理。
用法：python devtools/_probe_section_mix.py [日志路径]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ui_highlight import (  # noqa: E402
    SECTION_TEMP, SECTION_VOLT, _is_data_line, section_of,
)

DEC2 = re.compile(r"\d+\.\d{2}")
DEC4 = re.compile(r"\d+\.\d{4}")


def kind(line: str) -> str:
    tokens = line.split()
    if not tokens or not all(re.fullmatch(r"\d+\.\d+", t) for t in tokens):
        return ""
    if all(DEC4.fullmatch(t) for t in tokens):
        return "volt4"
    if all(DEC2.fullmatch(t) for t in tokens):
        return "temp2"
    return "mixed"


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "发布/logs/接收历史.log"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    section = "other"
    bad_volt = []      # 4 位小数行落在 TEMP
    bad_temp = []      # 2 位小数行落在 VOLT
    for no, line in enumerate(lines, 1):
        section = section_of(line, section)
        k = kind(line)
        if not _is_data_line(line):
            continue
        if k == "volt4" and section == SECTION_TEMP:
            bad_volt.append((no, line[:56]))
        elif k == "temp2" and section == SECTION_VOLT:
            bad_temp.append((no, line[:56]))
    print(f"文件 {path}  共 {len(lines)} 行")
    print(f"① 电压格式(4 位小数)却处在温度段：{len(bad_volt)} 行")
    for no, text in bad_volt[:6]:
        print(f"   L{no}: {text}")
    print(f"② 温度格式(2 位小数)却处在电压段：{len(bad_temp)} 行")
    for no, text in bad_temp[:6]:
        print(f"   L{no}: {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
