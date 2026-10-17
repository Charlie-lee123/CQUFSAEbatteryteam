# -*- coding: utf-8 -*-
"""任务1 探针：最大化态串口开关是否贴顶（时钟正下方）+ 图标间距是否均匀。

运行：QT_QPA_PLATFORM=windows .venv/Scripts/python.exe devtools/_probe_rail_top.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings, QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication   # noqa: E402


def _snap(s):
    return {k: s.value(k) for k in s.allKeys()}


def _restore(s, snap):
    for k in s.allKeys():
        if k not in snap:
            s.remove(k)
    for k, v in snap.items():
        if v is None:
            s.remove(k)
        else:
            s.setValue(k, v)
    s.sync()


def _pump(app, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


def _layout_chain(widget):
    """返回从 widget 向上每一层的 (类名, 拥有它的布局类名, 该布局是否直接持有本控件)。"""
    chain = []
    w = widget
    while w is not None:
        parent = w.parentWidget()
        direct = "?"
        owner = "—"
        if parent is not None:
            lay = parent.layout()
            if lay is not None:
                owner = type(lay).__name__
                for i in range(lay.count()):
                    if lay.itemAt(i).widget() is w:
                        direct = "YES"
                        break
                else:
                    direct = "no (grandchild)"
        chain.append((type(w).__name__, owner, direct))
        w = parent
    return chain


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, ui_font  # noqa: E402
    from main_window import MainWindow          # noqa: E402
    apply_scheme("dark")
    app.setFont(ui_font(12.5))

    s = QSettings("MimoWorks", "SerialAssistant")
    snap = _snap(s)
    # 别让自启动全屏把状态搅乱：本探针手动 _toggle_expand
    s.setValue("custom/startup_maximized", False)
    s.sync()

    try:
        w = MainWindow()
        w._port_watch.stop()
        w.show()
        w.raise_()
        _pump(app, 0.3)

        # ---------- ① 普通态：开关在配置卡 ----------
        normal = {
            "row_parent": type(w._open_row.parentWidget()).__name__,
            "row_pos": (w._open_row.x(), w._open_row.y()),
            "row_visible": w._open_row.isVisible(),
        }
        print("=" * 72)
        print("任务1：最大化态串口开关位置验收")
        print("=" * 72)
        print(f"\n[普通态] 开关父={normal['row_parent']} 可见={normal['row_visible']} "
              f"pos={normal['row_pos']}")

        # ---------- ② 展开态 ----------
        w._toggle_expand()
        _pump(app, 0.7)

        rail = w._rx_rail
        rail_g = rail.geometry()
        row_g = w._open_row.geometry()
        clock_g = w.lbl_clock.geometry()
        card_g = w._rx_card.geometry()
        btn_search_g = w.btn_search.geometry()
        btn_expand_g = w.btn_expand.geometry()
        rail_items = []
        rlay = rail.layout()
        for i in range(rlay.count()):
            it = rlay.itemAt(i)
            wgt = it.widget()
            rail_items.append(f"[{i}] {type(wgt).__name__}" if wgt else f"[{i}] spacer")

        print("\n[展开态]")
        # 留一张**目视证据**：用户报的是"开关悬在屏幕中部、没到时间下面"，
        # 光看数字不够，要能直接看见贴顶后的版式。
        shot_dir = os.path.join(_ROOT, "_check")
        os.makedirs(shot_dir, exist_ok=True)
        shot = w.grab()
        shot_path = os.path.join(shot_dir, "rail_top_maximized.png")
        print(f"  截图: {shot.save(shot_path)} {shot_path}")
        print(f"  卡片头时钟 lbl_clock: x={clock_g.x()} y={clock_g.y()} "
              f"w={clock_g.width()} h={clock_g.height()}  底缘={clock_g.bottom()}")
        print(f"  接收卡 _rx_card:     x={card_g.x()} y={card_g.y()} "
              f"w={card_g.width()} h={card_g.height()}")
        print(f"  功能列 _rx_rail:    x={rail_g.x()} y={rail_g.y()} "
              f"w={rail_g.width()} h={rail_g.height()}")
        print(f"  串口开关 _open_row:  x={row_g.x()} y={row_g.y()} "
              f"w={row_g.width()} h={row_g.height()}")
        print(f"  首图标 btn_search:  x={btn_search_g.x()} y={btn_search_g.y()}")
        print(f"  末图标 btn_expand:  x={btn_expand_g.x()} y={btn_expand_g.y()}")
        print(f"  rail verticalPolicy={rail.sizePolicy().verticalPolicy()}")
        print(f"  rail 内项: {rail_items}")
        print("  布局层级 (_rx_rail 向上):")
        for name, owner, direct in _layout_chain(rail):
            print(f"    {name:12s} 拥有布局={owner:14s} 直接持有={direct}")

        # 间距：相邻图标之间的竖直间隙
        ys = [getattr(b, "geometry")().y() for b in
              (w.btn_search, w.btn_alert, w.btn_pause, w.btn_copy_all,
               w.btn_clear, w.btn_save, w.btn_expand)]
        gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]

        # 断言（统一到主窗口坐标系，避免 rail 局部坐标与卡片坐标混比）
        row_top_win = w._open_row.mapTo(w, QPoint(0, 0)).y()
        clock_bottom_win = w.lbl_clock.mapTo(w, QPoint(0, clock_g.height())).y()
        card_top_win = w._rx_card.mapTo(w, QPoint(0, 0)).y()
        print(f"\n  开关顶(窗口系)={row_top_win}  时钟底缘(窗口系)={clock_bottom_win}  "
              f"卡片顶(窗口系)={card_top_win}  图标间距={gaps[0]}px")

        ok = True
        # 开关紧贴卡片头：开关顶 与 时钟底缘 的距离 ≤ 图标间距量级
        gap_to_clock = row_top_win - clock_bottom_win
        print(f"  开关顶 ↔ 时钟底缘 距离 = {gap_to_clock} px  (≤40 即贴顶)")
        top_ok = 0 <= gap_to_clock <= 40
        ok = ok and top_ok
        print(f"  -> 贴顶(≤40px) = {top_ok}")

        # 图标"间距"= 相邻按钮底→顶净距（= rail.spacing，不含按钮高度）。
        # gaps 是按钮顶→顶（含 30px 按钮高），换算成净距要减去按钮高。
        btn_h = btn_search_g.height()
        gaps_b2t = [g - btn_h for g in gaps]
        even_ok = (max(gaps_b2t) - min(gaps_b2t)) <= 2
        ok = ok and even_ok
        print(f"  图标净间距(底→顶) = {gaps_b2t}  均匀(极差≤2px) = {even_ok}")

        # ⚠️ 2026-10-17 用户改口径：只把**开关**挪到时钟下面，列里 7 颗功能键
        #    的位置一点不许动。所以开关是浮层（不在 rail 布局里），图标组照旧
        #    由首尾两根 1/1 stretch 垂直居中 —— 开关与首图标之间是**大段留白**，
        #    这正是要的样子，旧断言（"开关-图标净距 == 图标净距"）已作废。
        #    现在断言两件事：图标组仍居中、开关没被列宽裁掉。
        # ⚠️ 基准是**功能列自己**的中线（列填满卡片正文区，图标由首尾 1/1 stretch
        #    居中），不是整张卡的中线 —— 卡头（时钟那行）不属于正文区。
        #    ⚠️ 图标坐标也要换算到窗口系：btn_search_g 是**列局部**坐标，直接和
        #    窗口系的中线比会凭空差出一个列偏移（第一版就这么误报成"没居中"）。
        rail_top = w._rx_rail.mapTo(w, QPoint(0, 0)).y()
        rail_center = rail_top + rail_g.height() / 2
        icons_top = w.btn_search.mapTo(w, QPoint(0, 0)).y()
        icons_bottom = w.btn_expand.mapTo(w, QPoint(0, 0)).y() + btn_expand_g.height()
        icons_center = (icons_top + icons_bottom) / 2
        center_off = abs(icons_center - rail_center)
        print(f"  图标组中心={icons_center:.0f}  功能列中线={rail_center:.0f}  "
              f"偏移={center_off:.0f}px  (列高 {rail_g.height()})")
        center_ok = center_off <= 30
        ok = ok and center_ok
        print(f"  -> 图标组仍垂直居中(≤30px) = {center_ok}")

        # ⚠️ 口径（2026-10-17 终版）：右侧整列 = 开关（列顶）+ 图标（下方居中），
        # 数据栏在列左侧。图标 **贴列的左边**（左边距 = 布局 margins.left，不变），
        # 离卡片右缘变远；开关在列顶（右缘≈卡片内缘，时钟正下方）。
        in_rx = w._rx_card.isAncestorOf(w._open_row)
        col_left = w._rx_rail.mapTo(w._rx_card, QPoint(0, 0)).x()
        icons_left = btn_search_g.x()                     # 列局部坐标
        margins = w._rx_rail.layout().contentsMargins()
        left_ok = abs(icons_left - margins.left()) <= 2
        ok = ok and left_ok
        print(f"  列左缘(卡内)={col_left}  首图标左缘(列内)={icons_left}  "
              f"margins.left={margins.left()}  图标贴列左 = {left_ok}")
        row_x_in_card = w._open_row.mapTo(w._rx_card, QPoint(0, 0)).x()
        row_y_in_card = w._open_row.mapTo(w._rx_card, QPoint(0, 0)).y()
        col_right_in_card = col_left + rail_g.width()
        want_right = col_right_in_card - margins.right()
        row_right_in_card = row_x_in_card + row_g.width()
        top_ok = (in_rx and row_y_in_card <= 80
                  and abs(row_right_in_card - want_right) <= 2)
        ok = ok and top_ok
        print(f"  开关在数据区内的 (x,y)=({row_x_in_card},{row_y_in_card}) "
              f"右缘={row_right_in_card} 列内容右缘={want_right}  "
              f"在接收卡={in_rx}  列顶满宽 = {top_ok}")

        # ---------- ③ 收起后还原 ----------
        w._toggle_expand()
        _pump(app, 0.7)
        restored_parent = type(w._open_row.parentWidget()).__name__
        print(f"\n[收起后] 开关父={restored_parent} 可见={w._open_row.isVisible()}")
        restored_ok = restored_parent == normal["row_parent"]
        ok = ok and restored_ok
        print(f"  -> 回到配置卡原位 = {restored_ok}")

        # ---------- ④ 快速三连：展开→100ms→收起→展开 不许坏 ----------
        cycle_ok = True
        for i in range(3):
            w._toggle_expand()
            _pump(app, 0.1)
            w._toggle_expand()
            _pump(app, 0.1)
        w._toggle_expand()
        _pump(app, 0.6)
        # 压测只验"不崩、开关不丢"；快速切换下 240ms 搬家定时器会交错，
        # 终态落在配置卡或数据区都算瞬态合法。真正要紧的是**稳定展开**后的落位
        # —— 再做一次干净的收起→展开，给足时间，断言开关落在数据区左上角。
        w._toggle_expand()                      # 收起
        _pump(app, 0.8)
        home_parent = type(w._open_row.parentWidget()).__name__
        w._toggle_expand()                      # 再展开
        _pump(app, 0.8)
        final_parent_name = type(w._open_row.parentWidget()).__name__
        final_visible = w._open_row.isVisible()
        rail_top_final = w._rx_rail.geometry().y()
        cycle_ok = (w._rx_card.isAncestorOf(w._open_row) and final_visible
                    and rail_top_final < 120)
        ok = ok and cycle_ok
        print(f"\n[三连压测] 压测后开关父={home_parent}（瞬态合法）")
        print(f"[稳定展开] 终态: 开关父={final_parent_name} 在接收卡={w._rx_card.isAncestorOf(w._open_row)} "
              f"可见={final_visible} rail.y={rail_top_final}  -> "
              f"开关在数据区且列在右侧 = {cycle_ok}")

        print("\n" + "=" * 72)
        print(f"总判定: {'PASS' if ok else 'FAIL'}")
        print("=" * 72)
    finally:
        w.setGeometry(0, 0, 1134, 820)
        w.close()
        _restore(s, snap)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
