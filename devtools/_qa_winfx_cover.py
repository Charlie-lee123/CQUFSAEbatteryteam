"""最大化/还原覆盖层状态机连续性自检（lead 2026-11-05）。

对应 od46 收尾时用户诉求之一：「软件最大化和还原还是不稳定，动画不连续，有跳变，
会闪，把这个问题也修了」。od46 用两条确定性修复把根因压住（见 _qa_dwm_eval.py
顶部说明）。本脚本**直接驱动那两条修复所在的内部方法**，用断言证明状态机不变量：

  R1 空闲快照自重试：进全屏后 _schedule_fs_idle_snap() 必须让 _fs_idle_pix 在
      重试预算（1.5s）内由 None 变成非 null —— 否则还原覆盖层会掉进"无旧画面"
      的纯原生回退分支（过冲回弹 = 闪）。
  R2 覆盖层底色同步缩放：_begin_window_cover(pix, start, with_bg=True) 之后，
      _cover_bg 与 _cover_label 的 geometry 都**严格等于 start**（不是整屏矩形）
      —— 否则进全屏第 1 帧窗外从壁纸一帧变纯色 = 跳变。
  连续性：_run_window_cover 期间逐帧采样，覆盖层矩形必须**单调长大**（无回退 =
      无抖），且 bg 与 label 每帧几何完全一致（同步缩放）。
  收尾还原：动画结束（_cover_label 变 None）后，opacity 复位 1.0、DWM 过渡旗复位
      False、无残留覆盖层控件。
  DWM 旗生命周期：_set_dwm_transitions(hwnd, True) 置位，_finish_window_cover()
      对称复位。

⚠️ 必须真机有显示器（QT_QPA_PLATFORM=windows）。无显示器时本脚本不会硬崩：bg 相关
断言自动 SKIP，其余状态断言尽力跑；任何异常都捕获成 FAIL 并打印原因。

跑法：
    .venv\\Scripts\\python.exe devtools/_qa_winfx_cover.py

退出码：0 = 全部 PASS；2 = 有 FAIL/SKIP（SKIP 不致命，但 FAIL 需查）；1 = 环境无法起 Qt。
（进程末尾用 os._exit 硬退出，绝不触发 closeEvent 的 QSettings 写回，避免污染用户配置。）
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")  # 必须赶在导入 Qt 之前

# devtools/ 下运行：把项目根（main_window.py / ui_theme.py 所在）上 path
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _make_app():
    app = QApplication(sys.argv)
    try:
        from ui_theme import ui_font
        app.setStyle("Fusion")
        app.setFont(ui_font(12.5))
    except Exception:  # noqa: BLE001 - 字体不是断言前提，缺失就跳过
        pass
    return app


def _make_window(app):
    from main_window import MainWindow
    w = MainWindow()
    pw = getattr(w, "_port_watch", None)
    if pw is not None:
        try:
            pw.stop()
        except Exception:  # noqa: BLE001
            pass
    return w


class _Report:
    def __init__(self):
        self.results = []

    def add(self, name, ok, detail=""):
        self.results.append((name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))

    def skip(self, name, detail=""):
        self.results.append((name, None, detail))
        print(f"  [SKIP] {name}" + (f"  — {detail}" if detail else ""))

    @property
    def failed(self):
        return [r for r in self.results if r[1] is False]


def _pump(app, seconds: float, step: float = 0.01):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(step)


def test_r1_snapshot_retry(app, w, rep: _Report):
    """R1：进全屏后空闲快照自重试必须在预算内就位。"""
    try:
        w._skip_startup_anim = False
        w._startup_phase = False
        w.showFullScreen()
        app.processEvents()
        time.sleep(0.05)
        w._fs_animating = False          # 排除过渡期 guard，专测自重试
        w._cover_label = None
        w._fs_idle_pix = None
        w._schedule_fs_idle_snap()
        # 重试预算 1.5s + 余量
        _pump(app, 1.9)
        ok = w._fs_idle_pix is not None and not w._fs_idle_pix.isNull()
        rep.add("R1 空闲快照自重试就位 (<=1.5s)", ok,
                f"_fs_idle_pix={'已建' if ok else '仍 None'}")
    except Exception as exc:  # noqa: BLE001
        rep.add("R1 空闲快照自重试就位 (<=1.5s)", False, f"异常: {exc!r}")


def test_r2_cover_bg_sync(app, w, rep: _Report):
    """R2：覆盖层底色与内容层同矩形起步（=start，不是整屏）。"""
    try:
        start = QRect(120, 80, 400, 300)
        pix = QPixmap(400, 300)
        pix.fill(Qt.GlobalColor.white)
        label = w._begin_window_cover(pix, start, with_bg=True)
        ok_label = label is not None and w._cover_label is not None \
            and w._cover_label.geometry() == start
        rep.add("R2 内容层起步矩形 == start", ok_label,
                f"label.geometry={w._cover_label.geometry() if w._cover_label else None}")
        if w.screen() is not None:
            bg = w._cover_bg
            ok_bg = bg is not None and bg.geometry() == start
            rep.add("R2 底色层起步矩形 == start (非整屏)", ok_bg,
                    f"bg.geometry={bg.geometry() if bg else None}")
        else:
            rep.skip("R2 底色层起步矩形 == start (非整屏)",
                     "offscreen 平台无 screen()，bg 分支不触发；需真机复跑")
        # 清理
        w._finish_window_cover()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        rep.add("R2 覆盖层底色同步缩放", False, f"异常: {exc!r}")


def test_b_layer_crossdissolve(app, w, rep: _Report):
    """od47g 双快照交叉溶解：A(当前)↔B(目标) 透明度互补、首尾归位、收尾清理。

    这是「最大化和还原最后还会闪/跳变」的根治点：旧实现覆盖层只显示**单张
    拉伸的旧版式快照**，末段交叉淡出时是从「错位旧图」硬切到「重排后的真窗口」
    = 闪。新方案加 B 层 = 目标版式快照，A→B 全程 smoothstep 交叉溶解，末段溶入
    真窗口（B 已同布局，无缝）。本断言证明：① 两帧都建起；② 起步 A 全显 B 全隐、
    两帧几何 == start（无双显首帧跳变）；③ 动画期间 A+B 透明度恒等 1.0（任何
    时刻都不会双透 → 绝不会中途露底闪）；④ 首尾归位（首 A 显 B 隐、末 A 隐 B 显）；
    ⑤ 收尾 B 层一并销毁（无残留）。
    """
    try:
        start = QRect(120, 80, 400, 300)
        end = QRect(0, 0, 1920, 1080)
        pa = QPixmap(400, 300)
        pa.fill(Qt.GlobalColor.white)
        pb = QPixmap(400, 300)
        pb.fill(Qt.GlobalColor.darkGray)
        label = w._begin_window_cover(pa, start, with_bg=False, pixmap_b=pb)
        ok_built = (label is not None and w._cover_label_b is not None
                    and w._cover_pix_b is not None)
        rep.add("B层 双快照已建立 (label_b/pix_b 非空)", ok_built,
                f"label_b={'有' if w._cover_label_b else '无'} "
                f"pix_b={'有' if w._cover_pix_b else '无'}")
        if not ok_built:
            w._finish_window_cover()
            app.processEvents()
            return
        # 起步：A 全显、B 全隐；两帧几何 == start（无双显首帧跳变）
        a0 = w._cover_label.windowOpacity()
        b0 = w._cover_label_b.windowOpacity()
        ok_head = abs(a0 - 1.0) < 1e-3 and abs(b0) < 1e-3
        rep.add("B层 起步 A≈1/B≈0 (无双显首帧跳变)", ok_head,
                f"A={a0:.3f} B={b0:.3f}")
        ok_geo = (w._cover_label.geometry() == start
                  and w._cover_label_b.geometry() == start)
        rep.add("B层 两帧起步矩形 == start (同步缩放)", ok_geo,
                f"A={w._cover_label.geometry()} B={w._cover_label_b.geometry()}")

        w._run_window_cover(label, start, end, 260)
        samples = []
        t0 = time.monotonic()
        while w._cover_label is not None and (time.monotonic() - t0) < 3.0:
            app.processEvents()
            time.sleep(0.004)
            if w._cover_label is not None and w._cover_label_b is not None:
                samples.append((w._cover_label.windowOpacity(),
                               w._cover_label_b.windowOpacity()))
        # 互补：A+B 恒等 1.0（任何时刻都不会双透 → 露出底层闪）
        complementary = all(abs(a + b - 1.0) < 1e-2 for a, b in samples) \
            if samples else False
        rep.add("B层 透明度全程互补 A+B≈1.0 (无双透闪)", complementary,
                f"{len(samples)} 帧采样")
        # 首尾归位：首帧≈A1B0、末帧≈A0B1
        if len(samples) >= 2:
            first, last = samples[0], samples[-1]
            head_ok = abs(first[0] - 1.0) < 0.15 and abs(first[1]) < 0.15
            tail_ok = abs(last[0]) < 0.15 and abs(last[1] - 1.0) < 0.15
            rep.add("B层 首帧 A显B隐 / 末帧 A隐B显", head_ok and tail_ok,
                    f"首={first} 末={last}")
        # 收尾清理：B 层一并销毁
        _pump(app, 0.3)
        ok_clean = (w._cover_label is None and w._cover_label_b is None
                    and w._cover_pix_b is None)
        rep.add("B层 收尾 B层一并清理 (无残留)", ok_clean,
                f"label={w._cover_label} label_b={w._cover_label_b} "
                f"pix_b={w._cover_pix_b}")
    except Exception as exc:  # noqa: BLE001
        rep.add("B层 双快照交叉溶解", False, f"异常: {exc!r}")


def test_e2e_maximize(app, w, rep: _Report):
    """E2E：走**真实** startup_fullscreen 入口（含 _land 100ms 延迟 + _run_window_cover）。

    不止测 _begin_window_cover 单方法，而是真把 maximize 全链路跑起来：验证
    `startup_fullscreen` 确实把 `self._fs_idle_pix` 当 B 层传进覆盖层、且动画期间
    A/B 透明度全程互补（无双透闪）。注入一张全屏版式快照模拟「此前已全屏过」，
    覆盖最常见的重复最大化/还原场景。
    """
    try:
        w._skip_startup_anim = False
        w._startup_phase = False
        w._fs_animating = False
        w._cover_label = None
        w._cover_label_b = None
        w._cover_pix_b = None
        pb = QPixmap(400, 300)
        pb.fill(Qt.GlobalColor.darkGray)      # B 层 = 全屏版式（目标）
        w._fs_idle_pix = pb
        w.showNormal()
        app.processEvents()
        time.sleep(0.05)
        w.startup_fullscreen()
        app.processEvents()
        built = w._cover_label_b is not None and w._cover_pix_b is not None
        rep.add("E2E 最大化 B层经真实入口建立", built,
                f"label_b={'有' if w._cover_label_b else '无'}")
        if not built:
            w._finish_window_cover()
            app.processEvents()
            return
        samples = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.7 and w._cover_label is not None:
            app.processEvents()
            time.sleep(0.004)
            if w._cover_label is not None and w._cover_label_b is not None:
                samples.append((w._cover_label.windowOpacity(),
                               w._cover_label_b.windowOpacity()))
        complementary = all(abs(a + b - 1.0) < 1e-2 for a, b in samples) \
            if samples else False
        rep.add("E2E 最大化 A+B≈1.0 (无双透闪)", complementary,
                f"{len(samples)} 帧采样")
        _pump(app, 0.3)
        clean = w._cover_label is None and w._cover_label_b is None
        rep.add("E2E 最大化 收尾清理", clean,
                f"label={w._cover_label} label_b={w._cover_label_b}")
    except Exception as exc:  # noqa: BLE001
        rep.add("E2E 最大化 真实路径", False, f"异常: {exc!r}")


def test_e2e_restore(app, w, rep: _Report):
    """E2E：走**真实** _on_escape 入口（全屏→普通）。

    验证 `startup_fullscreen` 抓的 `_normal_idle_pix` 真被当成 B 层（普通版式=目标）
    传进还原覆盖层，且 A/B 全程互补。注入 A=全屏快照、B=普通快照并把窗口摆成全屏态，
    直接调 `_on_escape()` 触发还原全链路（含 _do_restore 100ms 延迟）。
    """
    try:
        w._skip_startup_anim = False
        w._startup_phase = False
        w._fs_animating = False
        w._cover_label = None
        w._cover_label_b = None
        w._cover_pix_b = None
        pa = QPixmap(400, 300)
        pa.fill(Qt.GlobalColor.white)        # A 层 = 全屏版式（当前）
        pb = QPixmap(400, 300)
        pb.fill(Qt.GlobalColor.darkGray)     # B 层 = 普通版式（目标）
        w._fs_idle_pix = pa
        w._normal_idle_pix = pb
        w._want_fullscreen = True
        w._normal_geometry = QRect(200, 150, 800, 600)  # 让 end≠start 才有动画
        w.showFullScreen()
        app.processEvents()
        time.sleep(0.05)
        w._on_escape()
        app.processEvents()
        built = w._cover_label_b is not None and w._cover_pix_b is not None
        rep.add("E2E 还原 B层经真实入口建立", built,
                f"label_b={'有' if w._cover_label_b else '无'}")
        if not built:
            w._finish_window_cover()
            app.processEvents()
            return
        samples = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.7 and w._cover_label is not None:
            app.processEvents()
            time.sleep(0.004)
            if w._cover_label is not None and w._cover_label_b is not None:
                samples.append((w._cover_label.windowOpacity(),
                               w._cover_label_b.windowOpacity()))
        complementary = all(abs(a + b - 1.0) < 1e-2 for a, b in samples) \
            if samples else False
        rep.add("E2E 还原 A+B≈1.0 (无双透闪)", complementary,
                f"{len(samples)} 帧采样")
        _pump(app, 0.3)
        clean = w._cover_label is None and w._cover_label_b is None
        rep.add("E2E 还原 收尾清理", clean,
                f"label={w._cover_label} label_b={w._cover_label_b}")
    except Exception as exc:  # noqa: BLE001
        rep.add("E2E 还原 真实路径", False, f"异常: {exc!r}")


def test_continuity_and_finish(app, w, rep: _Report):
    """连续性（单调长大 + bg==label）与收尾状态还原。"""
    try:
        start = QRect(120, 80, 400, 300)
        end = QRect(0, 0, 1920, 1080)
        pix = QPixmap(400, 300)
        pix.fill(Qt.GlobalColor.white)
        label = w._begin_window_cover(pix, start, with_bg=True)
        if label is None:
            rep.add("连续性 覆盖层已建立", False, "_begin_window_cover 返回 None")
            return
        w._run_window_cover(label, start, end, 260)

        samples = []
        t0 = time.monotonic()
        while w._cover_label is not None and (time.monotonic() - t0) < 3.0:
            app.processEvents()
            time.sleep(0.005)
            lg = w._cover_label.geometry() if w._cover_label is not None else None
            bg = w._cover_bg.geometry() if w._cover_bg is not None else None
            if lg is not None:
                samples.append((lg, bg))

        # 单调性：宽高从不回退（允许相等），且整体从 start 走向 end
        monotonic = True
        if len(samples) >= 2:
            for i in range(1, len(samples)):
                prev, _ = samples[i - 1]
                cur, _ = samples[i]
                if cur.width() < prev.width() or cur.height() < prev.height():
                    monotonic = False
                    break
        else:
            monotonic = False
        rep.add("连续性 覆盖层矩形单调长大 (无回退/无抖)", monotonic,
                f"采样 {len(samples)} 帧")

        # 同步缩放：bg 与 label 每帧几何一致
        if w.screen() is not None:
            synced = all(bg == lg for lg, bg in samples if bg is not None) \
                and any(bg is not None for _, bg in samples)
            rep.add("连续性 bg 与 label 逐帧几何一致 (同步缩放)", synced,
                    f"{sum(1 for _, b in samples if b is not None)}/{len(samples)} 帧有 bg")
        else:
            rep.skip("连续性 bg 与 label 逐帧几何一致 (同步缩放)",
                     "offscreen 无 bg，需真机复跑")

        # 收尾还原：动画结束后无残留、opacity/DWM 旗复位
        _pump(app, 0.3)
        no_residue = w._cover_label is None and w._cover_bg is None
        rep.add("收尾 无残留覆盖层控件", no_residue,
                f"label={w._cover_label} bg={w._cover_bg}")
        rep.add("收尾 主窗 opacity 复位 1.0",
                abs(w.windowOpacity() - 1.0) < 1e-6,
                f"opacity={w.windowOpacity()}")
        rep.add("收尾 DWM 过渡旗复位 False",
                w._dwm_transition_off is False,
                f"_dwm_transition_off={w._dwm_transition_off}")
    except Exception as exc:  # noqa: BLE001
        rep.add("连续性/收尾", False, f"异常: {exc!r}")


def test_dwm_flag_lifecycle(app, w, rep: _Report):
    """DWM 过渡旗：开启置位、收尾对称复位。"""
    try:
        hwnd = int(w.winId())
        w._set_dwm_transitions(hwnd, True)
        on = w._dwm_transition_off is True
        rep.add("DWM 旗 _set_dwm_transitions(True) 置位", on,
                f"_dwm_transition_off={w._dwm_transition_off}")
        w._finish_window_cover()     # 收尾统一复位（此处无活跃覆盖层，仅复位旗）
        off = w._dwm_transition_off is False
        rep.add("DWM 旗 _finish_window_cover() 对称复位", off,
                f"_dwm_transition_off={w._dwm_transition_off}")
    except Exception as exc:  # noqa: BLE001
        rep.add("DWM 旗生命周期", False, f"异常: {exc!r}")


def main() -> int:
    print("=" * 78)
    print("最大化/还原覆盖层状态机连续性自检（od46 R1/R2 不变量）")
    print("=" * 78)
    try:
        app = _make_app()
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 无法启动 Qt（可能无显示器 / 平台不支持）：{exc!r}")
        return 1

    rep = _Report()
    w = _make_window(app)
    w.show()
    app.processEvents()
    time.sleep(0.1)

    print("\n[1] R1 空闲快照自重试")
    test_r1_snapshot_retry(app, w, rep)

    print("\n[2] R2 覆盖层底色同步缩放（进全屏首帧零跳变）")
    test_r2_cover_bg_sync(app, w, rep)

    print("\n[2b] od47g 双快照交叉溶解（根治最大化/还原闪）")
    test_b_layer_crossdissolve(app, w, rep)

    print("\n[2c] E2E 真实入口（startup_fullscreen / _on_escape）")
    test_e2e_maximize(app, w, rep)
    test_e2e_restore(app, w, rep)

    print("\n[3] 连续性 + 收尾状态还原")
    test_continuity_and_finish(app, w, rep)

    print("\n[4] DWM 过渡旗生命周期")
    test_dwm_flag_lifecycle(app, w, rep)

    print("\n" + "=" * 78)
    total = len([r for r in rep.results if r[1] is not None])
    passed = len([r for r in rep.results if r[1] is True])
    skipped = len([r for r in rep.results if r[1] is None])
    failed = len(rep.failed)
    print(f"汇总：{passed}/{total} PASS，{skipped} SKIP，{failed} FAIL")
    print("（SKIP 多因 offscreen 无 screen()；真机 windows 平台下应全部 PASS）")
    print("=" * 78)

    # 硬退出：绝不触发 closeEvent 的 QSettings 写回，保护用户配置。
    # ⚠️ os._exit 不刷缓冲，必须先 flush，否则全部 print 丢失。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(2 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
