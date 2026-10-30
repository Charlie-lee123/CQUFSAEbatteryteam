# -*- coding: utf-8 -*-
r"""接收数据的**结构化落盘**：CSV（宽表）/ JSON（嵌套）。

为什么单独一个模块（2026-10-30 用户："本轮数据 → CSV/JSON，而不是只有日志文本"）：
日志是给人看的，机器要用就得再解析一遍；而"每次车检的数据变成资产"要求
一次采集、Excel/pandas 直接可用。

⚠️ 数据从**原始分帧文本**来（``MainWindow._emit_block`` 的入参），**不能**从对齐后
的显示文本反解析：显示文本里有序号补零、电压/温度网格重排、断线标记 X 插在两值
之间 —— 反解析出来的列号是错的（真机上一定会错位）。

解析口径（与显示层同一套语义，见 ``main_window`` 的段状态机）：
  · ``Voltage1:``   → 进入电压段，段内所有数字按**模组**切块（每模组几个电芯由
                      设置/车辆档案决定，来自 ``_rx_grid_hints``）
  · ``Temperature1:`` → 进入温度段，同理按模组切块
  · ``BMS ...``     → 本轮结束
  · 电压段里再次出现 ``Voltage1:`` 且本轮已有数据 → 新一轮开始
"""
from __future__ import annotations

import csv
import json
import re
import time
from datetime import datetime

# 段标题：允许 "Voltage1:" / "Voltage1" / 带前后空白的变体
VOLT_HEADER = re.compile(r"^\s*Voltage1\s*[:：]?\s*$", re.I)
TEMP_HEADER = re.compile(r"^\s*Temperature1\s*[:：]?\s*$", re.I)
BMS_HEADER = re.compile(r"^\s*BMS\b", re.I)
# 数值：整数或小数（含负号，低温可能为负）；不带单位的纯数字
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

VOLT, TEMP = "volt", "temp"
# 只留最近这么多轮：一轮 ≈ 模组数 × (电芯数 + 温度数) 个浮点，500 轮约 2MB
MAX_ROUNDS = 500


class Session:
    """一路接收数据的结构化缓存（模块级单例，见 ``session()``）。"""

    def __init__(self, max_rounds: int = MAX_ROUNDS) -> None:
        self._max_rounds = max(1, int(max_rounds))
        self._rounds: list[dict] = []
        self._cells: list[int] = []
        self._temps: list[int] = []
        self._kind: str | None = None
        self._buffer: list[float] = []      # 当前段里还没分完的数字（可能跨帧）
        self._cur: dict | None = None       # 正在拼的这一轮
        self._started: float = 0.0

    # -- 配置 ------------------------------------------------------------

    def configure(self, cells: list[int] | None, temps: list[int] | None) -> None:
        """设置**每模组的电芯数/温度数**（来自车辆档案或全局配置）。

        为空表示"跟随设备发来的点数"：整段数字均分到模组 —— 这种情况下模组号
        只能用"每模组值一批"的近似切法（见 ``_assign``）。
        """
        self._cells = [int(c) for c in (cells or []) if int(c) > 0]
        self._temps = [int(t) for t in (temps or []) if int(t) > 0]

    # -- 采集 ------------------------------------------------------------

    def feed(self, text: str, stamp: str | None = None) -> None:
        """喂一块**原始**分帧文本（``_emit_block`` 的入参，未对齐）。"""
        if not text:
            return
        lines = [ln for ln in text.split("\n") if ln.strip()]
        if not lines:
            return
        for line in lines:
            if VOLT_HEADER.match(line):
                # ⛔ 不能"见到 Voltage1: 就开新一轮"：真机上电压是**分包**发的，
                #    每帧都带一次标题（见 main_window._emit_block 的注释）。只有
                #    "段状态从非电压段绕回电压段"才是新一轮 —— 和显示层同一判据。
                if (self._kind != VOLT and self._cur is not None
                        and (self._cur["volt"] or self._cur["temp"])):
                    self._flush(stamp)
                self._begin(stamp)
                self._kind = VOLT
                continue
            if TEMP_HEADER.match(line):
                if self._kind != TEMP:
                    self._buffer = []       # 电压段没分完的半截丢掉（模组不完整）
                self._begin(stamp)
                self._kind = TEMP
                continue
            if BMS_HEADER.match(line):
                self._flush(stamp)
                self._kind = None
                self._buffer = []
                continue
            if self._kind is None:
                continue
            for token in NUM_RE.findall(line):
                try:
                    self._buffer.append(float(token))
                except ValueError:      # pragma: no cover - 正则已保证可转
                    continue
            self._assign()

    # -- 内部 ------------------------------------------------------------

    def _begin(self, stamp: str | None) -> None:
        if self._cur is None:
            self._cur = {
                "t": stamp or datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "epoch": time.time(),
                "volt": [],          # list[list[float]] 每模组一组（mV）
                "temp": [],          # list[list[float]] 每模组一组（°C）
            }
            self._started = time.time()

    def _sizes(self, kind: str) -> list[int]:
        sizes = self._cells if kind == VOLT else self._temps
        return sizes

    def _assign(self) -> None:
        """把缓冲里的数字按"每模组几个"切块装进当前轮。"""
        if self._cur is None:
            return
        kind = self._kind
        if kind is None:
            return
        sizes = self._sizes(kind)
        key = "volt" if kind == VOLT else "temp"
        bucket = self._cur[key]
        while self._buffer:
            idx = len(bucket)
            if sizes:
                need = sizes[idx] if idx < len(sizes) else sizes[-1]
            else:
                # 没有逐模组配置：把整段均分做不到"流式"，就按设备自然分帧
                # —— 一个块（一次 feed 的数字）算一个模组，够用且不会错位。
                need = len(self._buffer)
            if len(self._buffer) < need:
                return                  # 这一模组还没收齐，等下一帧
            chunk = self._buffer[:need]
            del self._buffer[:need]
            bucket.append(chunk)

    def _flush(self, stamp: str | None = None) -> None:
        cur = self._cur
        self._cur = None
        if cur is None:
            return
        # 段里没分满一个模组的"零头"也留下：设备和设置里的模组/电芯数**对不上**
        # 时（换板子、忘改设置），整轮丢掉会让用户导出到空文件却不知道原因；
        # 留成"最后一个不完整模组"至少数据还在（CSV 里该行后面是空格）。
        kind = self._kind
        if self._buffer and kind in (VOLT, TEMP):
            cur["volt" if kind == VOLT else "temp"].append(list(self._buffer))
            self._buffer = []
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
        self._buffer = []
        self._kind = None

    @property
    def round_count(self) -> int:
        return len(self._rounds) + (1 if self._cur else 0)

    def rounds(self) -> list[dict]:
        """已完成轮次（正在拼的那一轮不算，除非只有它）。"""
        out = list(self._rounds)
        if self._cur and (self._cur["volt"] or self._cur["temp"]):
            out.append(self._cur)
        return out

    # -- 落盘 ------------------------------------------------------------

    @staticmethod
    def _num(value: float, digits: int) -> str:
        text = f"{value:.{digits}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    def write_csv(self, path: str, digits: int = 4) -> int:
        """宽表：**一行 = 某一轮里的某一个模组**，电芯/温度各自展开成列。

        为什么是宽表：算放电曲线要"每个时间点一行"，找最弱电芯要"同一列里取最小"
        —— 宽表在 Excel 里直接筛、在 pandas 里直接 ``.min(axis=1)``，都不用透视。
        各模组点数不一致时按最大值补空列（Excel 里就是空白格）。
        """
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
                    # ⚠️ 电芯一律按 **mV 整数** 落盘（表头写的就是 mV）：BMS 口径
                    #    就是 mV，浮点伏特在 Excel 里既不好比大小、也会出现
                    #    3.5999999 这种脏值。温度保留一位小数。
                    row += [str(int(round(v * 1000))) for v in cells] + \
                           ["" for _ in range(n_cells - len(cells))]
                    row += [self._num(v, 2) for v in temps] + \
                           ["" for _ in range(n_temps - len(temps))]
                    writer.writerow(row)
                    rows_written += 1
        return rows_written

    def write_json(self, path: str, meta: dict | None = None) -> int:
        """嵌套结构：rounds[] → modules[] → cells/temps（同时给出单位与列名）。"""
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
                            "cells": [int(round(v * 1000)) for v in
                                      (rnd["volt"][mod] if mod < len(rnd["volt"]) else [])],
                            "temps": [round(v, 2) for v in
                                      (rnd["temp"][mod] if mod < len(rnd["temp"]) else [])],
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
    """自测用：清空并保留单例身份（别的模块可能已经持有引用）。"""
    _SESSION.clear()
