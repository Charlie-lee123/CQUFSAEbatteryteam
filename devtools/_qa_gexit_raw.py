"""G′ 退出#1 原始 tick dump（QA 诊断专用，2026-10-19；不判定任何东西）。

背景：``final_run9`` 的退出#1 出现 4 个「绝对单步」FAIL（接收卡 133/132、
功能列 103/98、配置卡 125/121、工具栏行 90/79），失效帧签名一律是
「Δq≈0.31 / 步长 8ms」。按 ``ui_anim.Tween`` 的真实时间计时
（``perf_counter``，曲线峰斜率 4.04/260ms），8ms 内 ``_rx_t`` 最多推进
0.126 —— 0.31 物理不可能。本探针把退出#1 的**每个 tick 原始行**
（t、_rx_t、各跟踪控件几何）原样打出，用以分辨三种可能：

  (a) ``_rx_t`` 真在两拍之间跳了 0.31（补间/时钟行为，源码层）；
  (b) 几何变化事件对（``_widget_motion_q`` 的合并规则）把多拍并成了
      一个「8ms 事件」，Δq 是跨拍的累计（测量伪影）；
  (c) ``_rx_t`` 正常推进而几何跳了（布局量化/迟到落地 → 真跳变）。

判读法：原始行里找「Δt<12ms 且 |Δq|>0.15」的相邻拍 —— 若存在且几何
同拍跳变，是 (a)/(c)；若原始行里 Δq 是跨多拍累计、而 8ms 只是合并后
的事件间隔，是 (b)。

用法（项目根目录，桌面 ALIVE 与否不影响几何读数，但仍照惯例核门禁）::

    .venv/Scripts/python.exe devtools/_qa_gexit_raw.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qa_anim_verdict import (GeometryTap, TRACK, _boot_single,  # noqa: E402
                              _motion_min_dt, _teardown_single,
                              _widget_motion_q, close_settings,
                              land_settings, pump)


def _raw_dump(rows, watch: str = "接收卡") -> None:
    """原始 tick 行 + 相邻拍差分（零合并、零过滤）。"""
    t0 = rows[0][0]
    print(f"\n---- 原始 tick 行（{len(rows)} 拍，零合并）----")
    print("   t_ms    _rx_t | " + " | ".join(f"{s}:x,y,w,h" for s, _ in TRACK))
    prev_q = None
    prev_t = None
    for t, snap in rows:
        q = snap.get("_q")
        cells = []
        for short, _attr in TRACK:
            g = snap.get(short)
            cells.append("None" if g is None else
                         f"{g[0]},{g[1]},{g[2]},{g[3]}{'*' if not g[4] else ''}")
        flag = ""
        if prev_q is not None and q is not None:
            dt = (t - prev_t) * 1000.0
            dq = abs(q - prev_q)
            if dq > 0.15 and dt < 15.0:
                flag = f"  ←← 悖反签名：Δt={dt:.1f}ms |Δq|={dq:.3f}"
        print(f"{((t - t0) * 1000.0):7.1f} "
              f"{'  None' if q is None else f'{q:6.3f}'} | "
              + " | ".join(cells) + flag)
        prev_q, prev_t = q, t

    # 拍间隔直方图（看 8ms 拍是孤例还是常态）
    dts = [(rows[i + 1][0] - rows[i][0]) * 1000.0 for i in range(len(rows) - 1)]
    if dts:
        buckets: dict[str, int] = {}
        for d in dts:
            key = f"{int(d // 4) * 4:d}-{int(d // 4) * 4 + 3:d}ms"
            buckets[key] = buckets.get(key, 0) + 1
        print("拍间隔分布：" + "，".join(f"{k}×{v}" for k, v in sorted(buckets.items())))


def _event_pairs(rows, watch: str = "接收卡") -> None:
    """合并后的事件对（_widget_motion_q 同口径），与原始行对照。"""
    min_dt = _motion_min_dt()
    pairs, dq_zero, span, events, qs = _widget_motion_q(rows, watch, min_dt)
    t0 = rows[0][0]
    print(f"\n---- 合并后几何变化事件对（{watch}，min_dt={min_dt:.1f}ms，"
          f"span={span:.0f}px）----")
    print("  #   t1_ms   t2_ms   Δt_ms   Δgeo    Δq    Δpos   Δsize")
    for i, (d, dq, t1, t2, dpos, dsize) in enumerate(pairs):
        mark = ""
        if d > 100:
            mark = "  ← 大位移对"
        print(f"{i:3d} {(t1 - t0) * 1000.0:7.1f} {(t2 - t0) * 1000.0:7.1f} "
              f"{(t2 - t1) * 1000.0:7.1f} {d:7.1f} {dq:6.3f} {dpos:7.1f} "
              f"{dsize:7.1f}{mark}")
    for (t1, t2, d, dpos, dsize, q1, q2) in dq_zero:
        print(f"  Δq==0 对：{(t1 - t0) * 1000.0:.1f}→{(t2 - t0) * 1000.0:.1f}ms "
              f"Δgeo={d:.1f}px（Δpos={dpos:.1f}/Δsize={dsize:.1f}） "
              f"q={q1:.3f}→{q2:.3f}")
    # 最大步长对的归因分类：忠实（Δq 大、斜率 ≤1.4×行程=判据已放行）
    # vs 盲区跳变（Δq<0.01，斜率判据看不见、只有绝对单步能抓）。
    if pairs and span >= 8:
        worst = max(pairs, key=lambda p: p[0])
        d, dq, t1, t2, _dpos, _dsize = worst
        slope = (d / dq / span) if dq > 1e-9 else float("inf")
        kind = ("忠实跟踪（斜率判据已放行）" if dq >= 0.01 and slope <= 1.4
                else "盲区跳变（Δq<0.01，真结构跳变候选）" if dq < 0.01
                else f"超斜率（{slope:.2f}×行程，斜率判据本应抓）")
        print(f"  ▶ 最大步长对：Δgeo={d:.1f}px / Δq={dq:.3f} / "
              f"Δt={(t2 - t1) * 1000.0:.1f}ms / 斜率={slope:.2f}×行程 → {kind}")


def main() -> int:
    app, window, ctx = _boot_single()
    try:
        # 复刻 FINAL 退出#1 的前奏：设置开关两轮（P/P2）+ 展开
        land_settings(app, window)
        close_settings(app, window)
        land_settings(app, window)
        close_settings(app, window)
        if not window._expanded:
            window._expand_receive()
            pump(app, 1.2)

        with GeometryTap(app, window) as tap:
            for run in range(1, 6):
                if not window._expanded:
                    window._expand_receive()
                    pump(app, 1.2)
                print(f"\n[dump] ===== 第 {run} 次退出（前奏同 FINAL）=====")
                rows = tap.run(window._collapse_receive)
                print(f"[dump] 录制 {len(rows)} 拍；_rx_t 首末："
                      f"{rows[0][1].get('_q'):.3f} → "
                      f"{rows[-1][1].get('_q'):.3f}")
                _raw_dump(rows)
                for short, _attr in TRACK:
                    _event_pairs(rows, short)
                pump(app, 0.8)
    finally:
        _teardown_single(app, window, ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
