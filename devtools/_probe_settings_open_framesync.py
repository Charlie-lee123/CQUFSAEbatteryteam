"""打开设置动画 —— **逐帧（每动画帧=125Hz）** 全帧真屏抓拍。

比 _probe_settings_open_framegrab 更严格：
  · 把抓拍挂在 ui_anim 共享节拍 _Ticker._tick 上，**每推进一帧补间就抓一次全帧**
    （与动画严格同拍，不会把两帧并成一帧漏掉 1 帧闪）。
  · 抓「最终落点整窗矩形」（含阴影留边）的**全帧** PNG，逐帧落盘。
  · 每帧算：全帧均值亮度 + 与上一帧的「最大单像素亮度差」+「变化像素占比」。
    若某一帧出现 1 帧闪（即便均值不变），max_pixel_delta 会单独尖起。
  · 同时记 phase（动画/换帧/落定）、shot 可见性、swap_target、dialog 激活态、
    shot 几何、dialog 边框几何。

输出：
  _frames_open_sync/*.png   每帧
  _frames_open_sync/timeline.csv
"""
import os
import sys
import time
import csv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location(
    "qa", os.path.join(_ROOT, "devtools", "_qa_anim_verdict.py"))
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)

OUT = os.path.join(_ROOT, "devtools", "_frames_open_sync")
os.makedirs(OUT, exist_ok=True)
CSV_PATH = os.path.join(OUT, "timeline.csv")

_PAD = 24  # 留边吃阴影


def _mean_lum(img: QImage, stride: int = 3) -> float:
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    if w < 1 or h < 1:
        return float("nan")
    tot, n = 0, 0
    for y in range(0, h, stride):
        for x in range(0, w, stride):
            c = img.pixelColor(x, y)
            tot += 0.3 * c.red() + 0.59 * c.green() + 0.11 * c.blue()
            n += 1
    return tot / n if n else float("nan")


def _diff_stats(prev: QImage, cur: QImage, stride: int = 3):
    """返回 (max_abs_diff, changed_fraction)。prev/cur 已同尺寸 RGB888。"""
    w, h = cur.width(), cur.height()
    if prev.isNull() or w != prev.width() or h != prev.height():
        return float("nan"), float("nan")
    maxd = 0
    changed = 0
    n = 0
    for y in range(0, h, stride):
        for x in range(0, w, stride):
            a = prev.pixelColor(x, y)
            b = cur.pixelColor(x, y)
            d = abs(a.red() - b.red()) + abs(a.green() - b.green()) \
                + abs(a.blue() - b.blue())
            if d > maxd:
                maxd = d
            if d > 24:        # 单像素三通道和差 > 24 算「变了」
                changed += 1
            n += 1
    return maxd, (changed / n if n else float("nan"))


def main() -> int:
    app, window, ctx = qa._boot_single()
    screen = QApplication.primaryScreen()
    if screen is None:
        print("FATAL: 无主屏幕（headless）")
        qa._teardown_single(app, window, ctx)
        return 2

    dpr = screen.devicePixelRatio()
    print(f"screen={screen.name()} dpr={dpr}")

    # 先装钩子，再触发：保证动画每一帧都落到钩子里（否则 click 同步跑完、
    # 等不到 processEvents，补间会按墙钟一次性跳完，只抓到几帧）。
    import ui_anim
    ticker = ui_anim._ticker()

    rows = []
    frames = []
    prev_img = QImage()
    grab_rect = None
    idx = 0
    t0 = time.perf_counter()

    def grab_now():
        nonlocal prev_img, idx, grab_rect
        try:
            # 懒算抓取矩形：等 _open_settings 跑完、_settings_geom_to 是真实
            # 面板尺寸（宽>50）再定。_open_settings 同步执行期间（_pump_until_exposed
            # 触发的 tick）geom_to 还是默认 (0,0,0,0)，此时别锁定，跳过等下一拍。
            if grab_rect is None:
                target = getattr(window, "_settings_geom_to", None)
                if target is not None and target.width() > 50:
                    grab_rect = QRect(target.x() - _PAD, target.y() - _PAD,
                                      target.width() + 2 * _PAD,
                                      target.height() + 2 * _PAD)
                    print(f"grab_rect(logical)=({grab_rect.x()},"
                          f"{grab_rect.y()}){grab_rect.width()}x"
                          f"{grab_rect.height()}")
                else:
                    return
            # 注意：tick 本身就在 processEvents 内被调，这里**不要再** processEvents
            # （重入会拖慢节拍）。grabWindow 是同步 Win32 调用，直接抓当前屏幕。
            pix = screen.grabWindow(0, grab_rect.x(), grab_rect.y(),
                                    grab_rect.width(), grab_rect.height())
            img = pix.toImage()   # 不 convertToFormat：省一次全帧拷贝
            # 统计走极小缩略图（~120x150），Python 像素循环才快，才能 125Hz 跟住；
            # 全分辨率 PNG 编码 + 像素循环会把节拍拖成 ~5Hz 漏掉 1 帧闪。
            thumb = img.scaled(120, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception as exc:                       # 抓拍失败别拖垮动画
            print(f"  [warn] grab #{idx} 失败: {exc!r}")
            return
        mean = _mean_lum(thumb)
        maxd, chg = _diff_stats(prev_img, thumb)
        # 阶段
        shot_visible = False
        try:
            shot_visible = window._settings_shot.isVisible()
        except RuntimeError:
            pass
        swap_on = window._settings_swap_target is not None
        if swap_on:
            phase = "swap"
        elif shot_visible:
            phase = "anim"
        else:
            phase = "settled"
        dlg_active = False
        try:
            dlg_active = bool(window._settings_dialog.isActiveWindow())
        except (RuntimeError, AttributeError):
            pass
        p = ""
        try:
            sg = window._settings_shot.geometry()
            p = f"shot=({sg.x()},{sg.y()}){sg.width()}x{sg.height()}"
        except RuntimeError:
            pass
        path = os.path.join(OUT, f"f{idx:03d}_{phase}.png")
        # 换帧那几帧存**全分辨率**（错位就差那一两像素），其余降采样省时间
        if phase == "swap":
            img.save(path)
        else:
            img.scaledToWidth(480, Qt.SmoothTransformation).save(path)
        frames.append(path)
        rows.append({
            "idx": idx,
            "t_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "phase": phase,
            "shot_visible": int(shot_visible),
            "swap_on": int(swap_on),
            "dlg_active": int(dlg_active),
            "mean_lum": round(mean, 2),
            "max_px_delta": (round(maxd, 1) if maxd == maxd else "nan"),
            "changed_frac": (round(chg, 4) if chg == chg else "nan"),
            "geom": p,
        })
        prev_img = thumb
        idx += 1

    def patched_tick():
        orig_tick()
        grab_now()

    orig_tick = ticker._tick
    ticker._tick = patched_tick

    # 现在才触发打开：钩子已就位，动画每一帧都会进 grab_now
    window.btn_settings.click()

    # 泵事件让补间跑完 + 收尾余量（在循环里、每次 processEvents 之后抓，
    # 不在 tick 内抓——tick 内抓会拿到空白桌面）。
    deadline = time.perf_counter() + 1.6
    settled_seen = 0
    while time.perf_counter() < deadline:
        app.processEvents()
        grab_now()
        time.sleep(0.001)
        # 落定后再收 ~250ms 就停
        if rows and rows[-1]["phase"] == "settled":
            settled_seen += 1
            if settled_seen > 18:   # ~ 18 帧足够确认稳定
                break
        if (time.perf_counter() - t0) > 1.4:
            break

    ticker._tick = orig_tick

    with open(CSV_PATH, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["idx", "t_ms", "phase",
                               "shot_visible", "swap_on", "dlg_active",
                               "mean_lum", "max_px_delta", "changed_frac",
                               "geom"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # 分析：找 max_px_delta 的尖峰帧
    deltas = [(r["idx"], r["t_ms"], r["phase"], r["max_px_delta"])
              for r in rows if isinstance(r["max_px_delta"], (int, float))]
    deltas.sort(key=lambda x: -x[3])
    print(f"\n捕获 {len(rows)} 帧，存于 {OUT}")
    print("=== max_px_delta 最大的 6 帧（>120 视为明显跳变）===")
    for idx_, t, ph, md in deltas[:6]:
        print(f"  f{idx_:03d} t={t}ms phase={ph} max_px_delta={md}")
    # 稳态均值
    settled = [r["mean_lum"] for r in rows if r["phase"] == "settled"]
    if settled:
        print(f"落定稳态均值亮度 = {sum(settled)/len(settled):.2f}")
    print(f"CSV: {CSV_PATH}")

    qa._teardown_single(app, window, ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
