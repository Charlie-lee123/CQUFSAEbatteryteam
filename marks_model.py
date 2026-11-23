# -*- coding: utf-8 -*-
r"""整轮标记的数据模型 —— 纯 Python、无 Qt、不持久化。

设计要点（对应需求 4）
----------------------
* **按整轮**：一条标记锚在一轮的首块（时间戳行）上；``key = f"{stamp}#{seq}"``
  是会话内稳定键，``seq`` 解决同一时间戳文案重复出现时的区分。
* **不持久化**：``MarkStore`` 只活在内存，绝不写 QSettings —— 重启 / 清屏 /
  进回放即空。
* **裁剪兜底**：``start_block`` 只作快速路径；实时接收 ``setMaximumBlockCount``
  会从头裁，块号会漂移，跳转时块文本对不上 ``stamp`` 就靠 UI 全文重定位。
  这里只负责存 ``stamp`` 与给出查询接口，重定位逻辑在 main_window。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoundMark:
    """一条整轮标记（不可变）。"""

    key: str            # = f"{stamp}#{seq}"，会话内稳定
    start_block: int    # 打标时轮首块号（仅快速路径，会被裁剪漂移）
    stamp: str          # 轮首时间戳行的原文（重定位锚点）
    summary: str        # 轮首后首个非空行的前 24 字（列表展示）
    seq: int            # 会话内自增序号


class MarkStore:
    """内存标记列表 + 当前游标。纯逻辑，可直接单测。"""

    def __init__(self) -> None:
        self.marks: list[RoundMark] = []
        self.current: int = -1          # 当前游标（next/prev 用），-1 = 未选
        self._seq: int = 0

    def add(self, start: int, stamp: str, summary: str) -> RoundMark:
        """加一条标记；``seq`` 会话内自增，返回落库的对象。"""
        self._seq += 1
        mark = RoundMark(
            key=f"{stamp}#{self._seq}",
            start_block=int(start),
            stamp=str(stamp),
            summary=str(summary)[:24],
            seq=self._seq,
        )
        self.marks.append(mark)
        self.current = len(self.marks) - 1
        return mark

    def clear(self) -> None:
        """清空（换车 / 清接收区 / 进回放）。``_seq`` 归零，但 key 只在会话内有意义。"""
        self.marks.clear()
        self.current = -1
        self._seq = 0

    def nearest_key(self, block: int) -> str | None:
        """向上找 ``start_block <= block`` 的最近一条（块号漂移的快速路径）。"""
        best: RoundMark | None = None
        for mark in self.marks:
            if mark.start_block <= block and (best is None
                                              or mark.start_block > best.start_block):
                best = mark
        return best.key if best is not None else None

    def by_key(self, key: str) -> RoundMark | None:
        """按稳定键取标记。"""
        for mark in self.marks:
            if mark.key == key:
                return mark
        return None

    def next(self) -> RoundMark | None:
        """下一条（循环）；空列表返回 None。"""
        if not self.marks:
            return None
        self.current = (self.current + 1) % len(self.marks)
        return self.marks[self.current]

    def prev(self) -> RoundMark | None:
        """上一条（循环）；空列表返回 None。"""
        if not self.marks:
            return None
        self.current = (self.current - 1) % len(self.marks)
        return self.marks[self.current]
