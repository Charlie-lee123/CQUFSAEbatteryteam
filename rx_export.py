# -*- coding: utf-8 -*-
r"""接收数据的**结构化落盘**：CSV（宽表）/ JSON（嵌套）。

为什么单独一个模块（2026-10-30 用户："本轮数据 → CSV/JSON，而不是只有日志文本"）：
日志是给人看的，机器要用就得再解析一遍；而"每次车检的数据变成资产"要求
一次采集、Excel/pandas 直接可用。

## 协议口径（**与显示层同源，别自己发明**）

真机发的是"序号.值"的采样点，值和序号在同一串里：

    Voltage590:  01.3851  02.3849  03.3850        ← 序号 01，值 3851 mV
    Voltage591:  01.3848  02.3847                 ← 下一帧 = 同一个轮里的**下一个模组**
    Temperature1: 01.25  02.26                    ← 序号 01，值 25 ℃

取值规则照抄 ``ui_highlight``（那里是着色用的，已经过真机验证）：

* ``SAMPLE_RE = (?<!\S)(\d+)(\.\d+)`` —— 组 1 是序号、组 2 是 ".值"；
* 值 = **小数点后的数字串直接当整数**（``1.3820`` → 3820 mV、``1.25`` → 25 ℃）；
* 段标题 ``Voltage\d*:`` / ``Temperature\d*:`` 可以和数据**在同一行**。

⛔ 不能假设"纯伏特浮点"（``3.8712``）：那是**开发自测的写法**，真机不是这样。
   第一版就是按浮点写的，``01.3851`` 会被读成 1.3851 V 且丢掉序号 —— 导出的
   数据整体错位（2026-10-30 独立验收抓出来的，见 devtools/_verify25_*.py）。

## 轮 / 模组怎么切

* **一帧 = 一个模组**（``Voltage590:`` 一帧 25 个点）。连续多帧电压 = 同一轮里的
  多个模组；帧内序号回到 1 说明换了模组。
* **段绕一圈回到电压 = 新一轮**（电压 → 温度 → BMS → 电压）。判据与显示层
  ``main_window._emit_block`` 完全一致，所以"分包"（同一模组被拆成几帧、每帧
  都带标题）也不会被切碎。
* ``BMS`` 行是本轮结束标记。

## 单位

internal 一律 **电压 mV 整数 / 温度 ℃ 整数**（BMS 口径就是 mV；整数还避免
``3.5999999`` 这种脏值）。导出时 CSV 直接写整数，JSON 也写整数。
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime

# 段标题：允许 "Voltage1:" / "Voltage590:" / 带前导空白 / 标题后面直接跟数据
VOLT_HEADER = re.compile(r"\s*Voltage\d*\s*[:：]?", re.I)
TEMP_HEADER = re.compile(r"\s*Temperature\d*\s*[:：]?", re.I)
BMS_HEADER = re.compile(r"\s*BMS\b", re.I)
# 采样点：序号 + ".值"（组 1 = 序号，组 2 = 带点号的值串）
SAMPLE_RE = re.compile(r"(?<!\S)(\d+)(\.\d+)")
# 兜底：纯数字（没有序号）—— 有些固件直接吐 "3851 3849" 或 "3.8712"
PLAIN_RE = re.compile(r"(?<!\S)-?\d+(?:\.\d+)?")

VOLT, TEMP = "volt", "temp"
MAX_ROUNDS = 500


def _value_of(digits: str, kind: str) -> int:
    """把 ``".3851"`` 这样的值串换算成本模块的单位（mV / ℃）整数。

    位宽按**单位本身的分辨率**截取：电压 4 位（0..9999 mV）、温度 2 位（0..99 ℃）。
    多出来的位是设备/排版附加的冗余数字（真机日志里出现过 ``1.38111``——值是 3811，
    末位跟着序号变）；截取后两种写法都对得上，也不会把 38.1 V 当成电池电压。

    ⛔ 不要"猜伏特"：``1.1000`` 既可能是"芯 1 = 1000 mV"，也可能是 1.1 V ——
       字面无法区分。设备协议就是**序号.值**（显示层 ``ui_highlight.SAMPLE_RE``
       也是这么读的），跟协议走才不会有歧义。
    """
    body = digits[1:]
    if kind == TEMP:
        return int((body + "00")[:2])
    return int((body + "0000")[:4])


class Session:
    """一路接收数据的结构化缓存（模块级单例，见 ``session()``）。"""

    def __init__(self, max_rounds: int = MAX_ROUNDS) -> None:
        self._max_rounds = max(1, int(max_rounds))
        self._rounds: list[dict] = []
        self._cells: list[int] = []
        self._temps: list[int] = []
        self._kind: str | None = None
        self._frame: list[int] = []         # 当前帧（= 一个模组）已解析的值
        self._cur: dict | None = None
        self._last_index = 0                # 帧内上一个序号，用来发现"回到 1"

    # -- 配置 ------------------------------------------------------------

    def configure(self, cells: list[int] | None, temps: list[int] | None) -> None:
        """每模组的电芯/温度数（车辆档案或全局配置）。仅用于**补列宽**与容错，
        切模组靠帧边界（见模块注释），不依赖这里的数字。"""
        self._cells = [int(c) for c in (cells or []) if int(c) > 0]
        self._temps = [int(t) for t in (temps or []) if int(t) > 0]

    # -- 采集 ------------------------------------------------------------

    def feed(self, text: str, stamp: str | None = None) -> None:
        if not text:
            return
        for raw in text.split("\n"):
            line = raw.strip("\r")
            if not line.strip():
                continue
            volt_head = VOLT_HEADER.match(line)
            temp_head = TEMP_HEADER.match(line)
            bms = BMS_HEADER.match(line)
            if volt_head or temp_head:
                kind = VOLT if volt_head else TEMP
                if kind != self._kind:
                    # 段切换：先把上一段的帧收进本轮
                    self._close_frame()
                    if kind == VOLT and self._cur is not None and (
                            self._cur["volt"] or self._cur["temp"]):
                        # 温度/BMS 之后又回电压 → 下一轮
                        self._flush(stamp)
                    elif kind == TEMP and self._cur is not None and self._cur["temp"]:
                        # 温度段重开（少见）：同一轮里换了个温度帧，不算新轮
                        pass
                    self._begin(stamp)
                    self._kind = kind
                else:
                    # 同段再来一个标题 = **下一帧**（= 下一个模组）
                    self._close_frame()
                head = volt_head or temp_head
                rest = line[head.end():] if head else line
                self._parse_samples(rest)
                continue
            if bms:
                self._close_frame()
                self._flush(stamp)
                self._kind = None
                continue
            if self._kind is None:
                continue
            self._parse_samples(line)

    # -- 内部 ------------------------------------------------------------

    def _parse_samples(self, text: str) -> None:
        """从一段文字里取采样点：优先"序号.值"，没有就退回纯数字。"""
        if not text.strip():
            return
        self._begin(None)
        found = False
        for m in SAMPLE_RE.finditer(text):
            found = True
            index = int(m.group(1))
            if self._frame and index <= self._last_index and index == 1:
                # 序号回到 1：这一帧里其实有**两个模组**（少见，但别丢数据）
                self._close_frame()
            self._last_index = index
            self._frame.append(_value_of(m.group(2), self._kind))
        if found:
            return
        for token in PLAIN_RE.findall(text):
            value = float(token)
            if self._kind == TEMP:
                self._frame.append(int(round(value)))
            else:
                # 没有序号的裸数：>100 当 mV，否则当 V（少数固件直接吐整毫伏）
                self._frame.append(int(round(value)) if abs(value) > 100
                                   else int(round(value * 1000)))
        if self._frame:
            self._begin(None)
            self._last_index = 0

    def _begin(self, stamp: str | None) -> None:
        if self._cur is None:
            self._cur = {
                "t": stamp or datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "volt": [],
                "temp": [],
            }

    def _close_frame(self) -> None:
        """把当前帧收进本轮对应段。

        切模组的规则（两种设备都见过，都要对）：
          · 真机 ``Voltage590: …`` 每帧一个模组 —— 帧长 = 该模组的点数；
          · 也有固件一帧把**所有**模组发完 —— 这时按设置里的逐模组点数切。
        判据：帧长超过"第一个模组的点数"就按配置切，否则整帧当一个模组。
        """
        if self._cur is None or not self._frame:
            self._frame = []
            self._last_index = 0
            return
        key = "volt" if self._kind == VOLT else "temp"
        if self._kind in (VOLT, TEMP):
            sizes = self._cells if self._kind == VOLT else self._temps
            rest = list(self._frame)
            if sizes and len(rest) > sizes[0]:
                idx = 0
                while rest:
                    need = sizes[idx] if idx < len(sizes) else sizes[-1]
                    self._cur[key].append(rest[:need])
                    rest = rest[need:]
                    idx += 1
            else:
                self._cur[key].append(rest)
        self._frame = []
        self._last_index = 0

    def _flush(self, stamp: str | None = None) -> None:
        self._close_frame()
        cur = self._cur
        self._cur = None
        if cur is None:
            return
        if not cur["volt"] and not cur["temp"]:
            return
        if stamp:
            cur["t"] = stamp
        self._rounds.append(cur)
        if len(self._rounds) > self._max_rounds:
            del self._rounds[:len(self._rounds) - self._max_rounds]

    # -- 对外 ------------------------------------------------------------

    def clear(self) -> None:
        self._rounds.clear()
        self._cur = None
        self._frame = []
        self._kind = None
        self._last_index = 0

    @property
    def round_count(self) -> int:
        return len(self._rounds) + (1 if self._cur else 0)

    def rounds(self) -> list[dict]:
        out = list(self._rounds)
        if self._cur and (self._cur["volt"] or self._cur["temp"] or self._frame):
            # 正在拼的最后一轮也导出（用户点导出时不一定等到了 BMS 行）
            snapshot = {"t": self._cur["t"],
                        "volt": [list(m) for m in self._cur["volt"]],
                        "temp": [list(m) for m in self._cur["temp"]]}
            if self._frame and self._kind in (VOLT, TEMP):
                key = "volt" if self._kind == VOLT else "temp"
                snapshot[key].append(list(self._frame))
            out.append(snapshot)
        return out

    # -- 落盘 ------------------------------------------------------------

    def write_csv(self, path: str) -> int:
        """宽表：**一行 = 某一轮里的某一个模组**，电芯/温度各自展开成列。"""
        rounds = self.rounds()
        n_cells = max((len(m) for r in rounds for m in r["volt"]), default=0)
        n_temps = max((len(m) for r in rounds for m in r["temp"]), default=0)
        header = ["时间", "轮次", "模组"]
        header += [f"电芯{i + 1}(mV)" for i in range(n_cells)]
        header += [f"温度{i + 1}(℃)" for i in range(n_temps)]
        rows_written = 0
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            for no, rnd in enumerate(rounds, start=1):
                modules = max(len(rnd["volt"]), len(rnd["temp"]))
                for mod in range(modules):
                    cells = rnd["volt"][mod] if mod < len(rnd["volt"]) else []
                    temps = rnd["temp"][mod] if mod < len(rnd["temp"]) else []
                    row = [rnd["t"], no, mod + 1]
                    row += [str(v) for v in cells] + \
                           ["" for _ in range(n_cells - len(cells))]
                    row += [str(t) for t in temps] + \
                           ["" for _ in range(n_temps - len(temps))]
                    writer.writerow(row)
                    rows_written += 1
        return rows_written

    def write_json(self, path: str, meta: dict | None = None) -> int:
        """嵌套结构：rounds[] → modules[] → cells/temps（整数，单位在 ``unit``）。"""
        rounds = self.rounds()
        doc = {
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "unit": {"volt": "mV", "temp": "℃"},
            "meta": dict(meta or {}),
            "rounds": [
                {
                    "no": no,
                    "time": rnd["t"],
                    "modules": [
                        {
                            "no": mod + 1,
                            "cells": list(rnd["volt"][mod])
                            if mod < len(rnd["volt"]) else [],
                            "temps": list(rnd["temp"][mod])
                            if mod < len(rnd["temp"]) else [],
                        }
                        for mod in range(max(len(rnd["volt"]), len(rnd["temp"])))
                    ],
                }
                for no, rnd in enumerate(rounds, start=1)
            ],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)
        return len(doc["rounds"])


# ---------------------------------------------------------------- 单例

_SESSION = Session()


def session() -> Session:
    """模块级单例：主窗口往里喂数据，设置面板从它导出（同 ``rx_history`` 的做法）。"""
    return _SESSION


def reset_for_tests() -> None:
    _SESSION.clear()
