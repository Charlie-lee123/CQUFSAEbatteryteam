# -*- coding: utf-8 -*-
r"""整轮标记的数据模型 —— 纯 Python、无 Qt、不持久化。

设计要点（对应需求 4）
----------------------
* **按整轮**：一条标记锚在一轮的首块（时间戳行）上；``key = f"{stamp}#{seq}"``
  是会话内稳定键，``seq`` 解决同一时间戳文案重复出现时的区分。
* **不持久化、单套共享**：``MarkStore`` 只活在内存，绝不写 QSettings ——
  重启即空。实时 / 近期回放 / 历史回放**共用同一套**（用户纠正：不是各一套）：
  在哪个数据源打的标，切到别处也仍列在同一张表里；进/出回放都不清空，
  只有用户主动"清空接收区"（main_window._clear_receive）才清。
* **三层锚点（stamp → content_sig 主锚 → stamp_rev 兜底）**：
  - ``stamp``：轮首时间戳原文，先把候选缩到"同秒的几轮"。
  - **``content_sig``（主锚）**：整轮内容（``[span0,span1)`` 各块文本 join 后
    blake2b 8 字节）。内容不随裁剪/前插而变，**两个漂移方向都不影响** —— 这是
    最可靠的精确锚点。定位时对每个同 stamp 候选算签名、相同即命中。
  - ``stamp_rev``（兜底）：打标那一刻「这一轮之后还有几条同 stamp 块」（0=最后
    一处），**反向**计数。仅当文档被换成"只加载一页的回放"、该轮内容已不在（签名
    对不上）时，退回 ``len(hits)-1-min(rev,len-1)`` 从尾部倒数定位。
    用反向而非正序：两种漂移都只动头部（实时从头裁、回放顶部前插），尾部稳定；
    正序会随头裁前移（QA 实测退化）。尾部整段被删时 ``rev>=len(hits)`` clamp 到
    最早那条 —— "数据没加载"的合理退化。
  ⚠️ ``start_block`` 仅作展示/调试，**不再参与定位**（块号漂移若恰等于一整轮行数，
  同文本时间戳块仍会误命中相邻轮 —— 已删这条快速路径）。
* **同轮去重**：同一轮反复打标（连按 Alt+M）不该出重复条目。判重键是
  ``(stamp, content_sig)`` —— 同 stamp 但签名不同 = 不同轮，允许各自打标；
  签名相同视为同一轮。（极端情况 BMS 两轮数据完全一致 → 签名相同 → 按同一轮处理，
  这是有意的取舍：宁可少一条也不出重复。）UI 侧经 :meth:`MarkStore.add_unique`
  做「找到就返回已有、否则新增」，不破坏底层 :meth:`MarkStore.add` 的语义。
  这里只负责存数据与查询，重定位 / toast 逻辑在 main_window。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoundMark:
    """一条整轮标记（不可变）。"""

    key: str            # = f"{stamp}#{seq}"，会话内稳定
    start_block: int    # 打标时轮首块号（仅展示/调试；定位不靠它，会被裁剪漂移）
    stamp: str          # 轮首时间戳行的原文（主锚点）
    summary: str        # 轮首后首个非空行的前 24 字（列表展示）
    seq: int            # 会话内自增序号
    # **兜底**锚点：反向组内序号（0 = 该 stamp 的最后一条）= 打标那一刻"这一轮之后
    # 还有几条同 stamp 块"。仅当内容签名对不上（该轮内容已不在文档）时用于退化定位。
    # 带默认值 0 → 向后兼容。
    stamp_rev: int = 0
    # **主锚点**：整轮内容签名（blake2b 8 字节 hex）。内容不随裁剪/前插而变，是
    # 定位与判重最可靠的键。带默认值 ""（老对象/无签名时为空 → 定位退回 rev 兜底）。
    content_sig: str = ""


class MarkStore:
    """内存标记列表 + 当前游标。纯逻辑，可直接单测。"""

    def __init__(self) -> None:
        self.marks: list[RoundMark] = []
        self.current: int = -1          # 当前游标（next/prev 用），-1 = 未选
        self._seq: int = 0

    def add(self, start: int, stamp: str, summary: str,
            stamp_rev: int = 0, content_sig: str = "") -> RoundMark:
        """加一条标记；``seq`` 会话内自增，返回落库的对象。

        ``content_sig`` 是整轮内容签名（主锚），``stamp_rev`` 是反向组内序号（兜底）。
        ⚠️ 本方法**不判重**（保持既有语义：每次调用都追加）。要"同轮不重复"
        请用 :meth:`add_unique`。
        """
        self._seq += 1
        mark = RoundMark(
            key=f"{stamp}#{self._seq}",
            start_block=int(start),
            stamp=str(stamp),
            summary=str(summary)[:24],
            seq=self._seq,
            stamp_rev=int(stamp_rev),
            content_sig=str(content_sig),
        )
        self.marks.append(mark)
        self.current = len(self.marks) - 1
        return mark

    def find_round(self, stamp: str, content_sig: str = "") -> RoundMark | None:
        """按 ``(stamp, content_sig)`` 找"同一轮"的已有标记；没有返回 None。

        判重键是「时间戳 + 内容签名」：同 stamp 但签名不同 = 不同轮（同一秒两轮），
        签名相同 = 同一轮（连按两次 Alt+M）。极端相同内容的两轮按同一轮处理（有意取舍）。
        """
        for mark in self.marks:
            if mark.stamp == stamp and mark.content_sig == content_sig:
                return mark
        return None

    def add_unique(self, start: int, stamp: str, summary: str,
                   stamp_rev: int = 0, content_sig: str = "") -> tuple[RoundMark, bool]:
        """"同轮不重复"地加标：已存在 ``(stamp, content_sig)`` 就返回已有那条。

        返回 ``(mark, created)``：``created=False`` 表示命中已有（没新增）。
        命中时把游标 ``current`` 定位到已有那条的下标，便于 UI 高亮选中。
        """
        existing = self.find_round(stamp, content_sig)
        if existing is not None:
            self.current = self.marks.index(existing)
            return existing, False
        return self.add(start, stamp, summary, stamp_rev, content_sig), True

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
