# -*- coding: utf-8 -*-
"""探针：车辆（车型）档案 + 逐模组网格排版的落地效果。

做三件事（都是"看得见的证据"，不是断言）：
  1. 建一台 3 模组的车（电芯 25/24/25、温度 8/5/6）并选中，然后喂 **3 组不同
     长度的数据**，逐组打印**接收区实际渲染出来**的每排点数 —— 用来证明"一排 =
     一个模组"、装不下时在**模组内部均衡拆行**（不丢点、不甩孤单元格）、数据不够
     时按实际截断。
  2. 截图 `_check/vehicle_grid.png`（接收区，能看到三个模组各自被拆成两排、每排
     都贴着视口容量）。
  3. 截图 `_check/vehicle_settings.png`（设置面板滚到「车辆」一节）。

⛔ **截图必须用真实窗口宽度（1180×940）拍，不许把窗口撑宽再截**：
   25 个电芯一排是 247 个字符（7 + 3 间隔），真实 1180 窗口的视口只放得下 ~120
   字符，所以每个模组必然被拆成两排 —— 这正是用户会看到的画面，也是要验的东西
   （`_cap_rows` 的断点落在单元格边界、没有"行尾孤零零一个单元格"）。
   撑宽到 2400 固然好看，但那是不存在的窗口，图上什么毛病都看不出来（v1 就这么
   拍过一次，等于没验）。

⛔ 全程 QSettings 快照还原（含 geometry / startup_maximized —— 开过主窗口的脚本
   跑完必须复查这两项，见 MEMORY.md）。
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QPoint, QRect, QSettings, Qt              # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea     # noqa: E402

CHECK_DIR = os.path.join(_ROOT, "_check")
SHOT_GRID = os.path.join(CHECK_DIR, "vehicle_grid.png")
SHOT_SETTINGS = os.path.join(CHECK_DIR, "vehicle_settings.png")
# ⛔ 截图窗口 = **用户真实窗口尺寸**（见文件头说明）：不许为了"一排装下"撑宽。
SHOT_WINDOW_W = 1180
SHOT_WINDOW_H = 940

VOLT_CELLS = [25, 24, 25]
VOLT_TEMPS = [8, 5, 6]


def pump(app, ms: int) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


def volt_points(count: int, start: int = 1) -> str:
    """造 ``count`` 个 4 位小数的电压采样点（序号连续），空格分隔。"""
    return " ".join(f"{i}.3700" for i in range(start, start + count))


def temp_points(count: int, start: int = 1) -> str:
    return " ".join(f"{i}.25" for i in range(start, start + count))


def dump_rows(window, tag: str) -> list[int]:
    """打印接收区**实际渲染**的每一排点数，返回该列表。"""
    from ui_highlight import SAMPLE_RE
    document = window.txt_rx.document()
    rows = [document.findBlockByNumber(i).text()
            for i in range(document.blockCount())]
    counts = [len(SAMPLE_RE.findall(line)) for line in rows if SAMPLE_RE.findall(line)]
    print(f"\n[{tag}] 渲染 {len(rows)} 行，其中数据排每排点数：{counts}")
    for index, line in enumerate(rows):
        shown = line if len(line) <= 108 else line[:108] + " …"
        print(f"  {index:2d}| {shown}")
    return counts


def feed(window, app, payload: str, tag: str) -> list[int]:
    window._clear_receive()
    window._on_received(payload.encode("utf-8"))
    window._flush_rx()
    # ⚠️ 必须再跑足够长的事件循环：显示走 30ms 合并 + **半行缓发 300ms**，只调
    #    一次 _flush_rx() 拿到的还是空文档（第一版探针就是这样打出四组"渲染 1
    #    行、0 个点"）。450ms 覆盖最慢那条路径。
    pump(app, 450)
    return dump_rows(window, tag)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from ui_theme import apply_scheme, ui_font
    app.setFont(ui_font(12.5))
    apply_scheme("dark")

    import customization
    from selftest import make_window, restore_settings

    settings = QSettings("MimoWorks", "SerialAssistant")
    snapshot = {key: settings.value(key) for key in settings.allKeys()}
    window = None
    dialog = None
    try:
        os.makedirs(CHECK_DIR, exist_ok=True)
        print("=== 屏幕 ===")
        screen = app.primaryScreen()
        print("  geometry", screen.geometry(), "dpr", screen.devicePixelRatio())

        # ---- 造一台车并选中（用户的真实档案先备份在 snapshot 里）----
        for name in customization.vehicle_names():
            customization.delete_vehicle(name)
        customization.save_vehicle("探针车-3模组", VOLT_CELLS, VOLT_TEMPS)
        customization.select_vehicle("探针车-3模组")
        print("\n=== 车辆档案 ===")
        print("  current =", customization.current_vehicle())
        print("  profile =", customization.active_profile())
        print("  合计电芯 =", sum(VOLT_CELLS), " 合计温度 =", sum(VOLT_TEMPS))

        # ---- 建窗（**用户真实窗口尺寸**，不撑宽：见文件头）----
        window = make_window()
        window.setGeometry(0, 0, SHOT_WINDOW_W, SHOT_WINDOW_H)
        window.show()
        pump(app, 500)
        print("\n=== 窗口 ===")
        print("  size =", window.size())
        chars = window._rx_max_row_chars()
        print("  视口一行能放下的字符数 =", chars)
        print("  25 个电芯一排需要的字符数 =", 25 * 7 + 24 * 3,
              "→ 装不下，按 _cap_rows 在模组内部拆")
        from ui_highlight import CELL_GAP, _cap_rows, grid_row_sizes
        cell_w = len("01.3700")                        # 90 个点、值 4 位小数
        pitch = cell_w + len(CELL_GAP)
        fit = max(1, (chars + len(CELL_GAP)) // pitch) if chars else 0
        print(f"  cell_w = {cell_w}  pitch = {pitch}  → fit = {fit} 格/排")
        print("  逐模组切分 grid_row_sizes(74, [25,24,25]) =",
              grid_row_sizes(74, row_sizes=VOLT_CELLS))
        print("  封顶后 _cap_rows(..., fit) =",
              _cap_rows(grid_row_sizes(74, row_sizes=VOLT_CELLS), fit))

        # ---- 三组不同长度的电压数据 + 一组温度数据 ----
        print("\n=== 电压段：25/24/25 = 74 点（正好三个模组）===")
        feed(window, app, "Voltage1:\n" + volt_points(74), "74 点")

        print("\n=== 电压段：90 点（比合计多 16 → 按最后一排 25 续排）===")
        feed(window, app, "Voltage1:\n" + volt_points(90), "90 点")

        print("\n=== 电压段：40 点（比合计少 → 按实际截断，不补假数据）===")
        feed(window, app, "Voltage1:\n" + volt_points(40), "40 点")

        print("\n=== 温度段：19 点（8/5/6）===")
        feed(window, app, "Temperature1:\n" + temp_points(19), "19 点")

        # ---- 截图一：接收区（回到"正好三个模组"那组，最干净）----
        window._clear_receive()
        window._on_received(("Voltage1:\n" + volt_points(74) + "\n"
                             "Temperature1:\n" + temp_points(19)).encode("utf-8"))
        window._flush_rx()
        pump(app, 400)
        shot_rows = dump_rows(window, "截图内容")
        print("  截图里的数据排点数 =", shot_rows)
        expected = _cap_rows(grid_row_sizes(74, row_sizes=VOLT_CELLS), fit) + VOLT_TEMPS
        assert shot_rows == expected, \
            f"截图排布与 _cap_rows 的预期不一致：{shot_rows} != {expected}"
        assert sum(shot_rows[:len(expected) - len(VOLT_TEMPS)]) == 74, \
            "封顶拆行后电压点丢了"
        pixmap = window.grab()
        # ⚠️ 坑：grab() 返回的位图带 devicePixelRatio（本机 2.0），size() 是**物理**
        #    像素；而 mapTo/size() 给的是**逻辑**坐标。直接拿逻辑矩形去 copy，裁到的
        #    是左上角那块（第一版就裁出了品牌栏 + 半行数据）。这里先把矩形换算成物理。
        dpr = pixmap.devicePixelRatio() or 1.0
        origin = window.txt_rx.mapTo(window, QPoint(0, 0))
        # 只取**文本区**：上面留 6px，下面按**实际内容高度**收（整块文本区有 500 多
        # 高，全画进来下半张是空白，反而看不清那些排）。
        # ⚠️ 别用 document().size()：QPlainTextEdit 用的是 QPlainTextDocumentLayout，
        #    它的 size() 常常是过期的（实测只给出 66px → 裁出 80 高的碎片图）。
        #    按"块数 × 行距"算最稳。
        from PySide6.QtGui import QFontMetricsF
        line_h = QFontMetricsF(window.txt_rx.font()).lineSpacing()
        rows_total = window.txt_rx.document().blockCount()
        content_h = int(rows_total * line_h) + 14
        box = QRect(origin.x() - 14, origin.y() - 6,
                    window.txt_rx.width() + 28,
                    max(80, min(window.txt_rx.height(), content_h)))
        print(f"  内容 {rows_total} 行 × 行距 {line_h:.1f} = {content_h}px")
        print("  裁切（逻辑）=", box, " 窗口 =", window.size(), " dpr =", dpr)
        physical = QRect(int(box.x() * dpr), int(box.y() * dpr),
                         int(box.width() * dpr), int(box.height() * dpr))
        crop = pixmap.copy(physical.intersected(pixmap.rect()))
        # 缩回逻辑尺寸再存：dpr=2 的原始位图是两倍宽，看图和贴文档都不方便
        crop = crop.scaledToWidth(box.width(), Qt.SmoothTransformation)
        crop.save(SHOT_GRID)
        print("  已保存", SHOT_GRID, crop.size())
        window.close()
        pump(app, 200)

        # ---- 截图二：设置面板滚到「车辆」一节 ----
        from ui_settings import SettingsDialog
        dialog = SettingsDialog("dark", 55)
        dialog.setGeometry(60, 30, 560, 880)
        dialog.show()
        pump(app, 400)
        scroll = dialog.findChild(QScrollArea)
        caption = next(label for label in dialog.findChildren(QLabel)
                       if label.text().startswith("车辆（电池箱）"))
        if scroll is not None:
            scroll.ensureWidgetVisible(caption, 0, 12)
            pump(app, 300)
        print("\n=== 设置面板 ===")
        print("  车辆下拉条目 =", [dialog.cmb_vehicle.itemText(i)
                                for i in range(dialog.cmb_vehicle.count())])
        print("  当前车辆 =", dialog.cmb_vehicle.currentText(),
              " 模组个数框 =", dialog.spin_vehicle_modules.value(),
              " 逐模组行 =", len(dialog._module_rows))
        print("  旧的三个全局参数可见 =",
              [dialog._custom_rows[key][0].isVisibleTo(dialog)
               for key in dialog._LEGACY_GRID_KEYS])
        dialog.grab().save(SHOT_SETTINGS)
        print("  已保存", SHOT_SETTINGS)
        dialog.close()
        pump(app, 200)
        return 0
    finally:
        for widget in (dialog, window):
            if widget is not None:
                try:
                    widget.close()
                except RuntimeError:
                    pass
        pump(app, 200)
        restore_settings(settings, snapshot)
        settings.sync()
        print("\n=== 收尾核对（必须与跑之前一致）===")
        for key in ("geometry", "custom/startup_maximized",
                    "custom/module_count", "custom/cells_per_module",
                    "custom/temps_per_module", "custom/vehicle",
                    "custom/vehicles"):
            print(f"  {key} =", settings.value(key))


if __name__ == "__main__":
    sys.exit(main())
