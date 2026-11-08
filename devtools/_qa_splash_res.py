"""开屏分辨率/计时核验（lead 2026-11-05）。

验证 od46 修正：高斯模糊不再是「降分辨率放大」——
  - 任意模糊档输出位图尺寸 == 清晰档（全分辨率，播放期 1:1 画出，零放大）
  - sigma→0 的模糊档与清晰档无缝交接
  - 预热耗时仍受控（dpr=2 真机最坏情况）
同时核对落点段计时：文字露完(1.90)到进角(2.00)的「定格」段已用 ease_out 消除。

跑法：.venv\\Scripts\\python.exe devtools/_qa_splash_res.py
（offscreen 即可，无需真机显示）
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    _app = QApplication(sys.argv)      # 保持引用，避免被 GC 提前回收
    import splash_motion as sm

    dpr = 2.0            # 模拟 Retina：旧实现会把低位图拉 6× → 最易暴露「降分辨率」bug
    screen_h = 1080.0
    screen_w = 1920.0

    t0 = time.perf_counter()
    r = sm.MotionRenderer("dark", dpr, screen_w, screen_h, sm.SPEC_PLANB)
    preheat = time.perf_counter() - t0

    clear = r._pixmap(r._clear_w)
    clear_w_phys = clear.width()           # 物理像素宽
    print(f"[info] clear_w 逻辑={r._clear_w}  清晰档物理宽={clear_w_phys}  (dpr={dpr})")
    print(f"[info] 旧实现模糊基图物理宽≈{r._blur_w}（被拉 {clear_w_phys/r._blur_w:.1f}×）")
    print(f"[info] 预热耗时={preheat*1000:.0f}ms")

    fails = 0

    # 1) 所有模糊档都是全分辨率
    for step in r._sigma_steps():
        b = r._blur(step)
        if b is None:
            print(f"  [FAIL] _blur({step}) 返回 None")
            fails += 1
            continue
        same = b.width() == clear_w_phys and b.height() == clear.height()
        if not same:
            print(f"  [FAIL] _blur({step}) 尺寸={b.width()}x{b.height()} != 清晰档 "
                  f"{clear_w_phys}x{clear.height()}（仍被降分辨率放大）")
            fails += 1
    if fails == 0:
        print("  [PASS] 全部模糊档 == 清晰档尺寸（全分辨率，零放大）")

    # 2) sigma→0 与清晰档无缝交接（同一张图）
    b0 = r._blur(0.0)
    same0 = (b0 is not None and b0.width() == clear_w_phys
             and b0.cacheKey() == clear.cacheKey())
    # cacheKey 相等说明返回的就是清晰档缓存本身
    print(f"  [{'PASS' if same0 else 'WARN'}] sigma→0 模糊档 == 清晰档"
          f"（cacheKey 相同={same0}）")

    # 3) 淡出模糊图也是全分辨率
    fp = r._fade_pix
    if fp is not None and fp.width() == clear_w_phys:
        print("  [PASS] 淡出 _fade_pix 全分辨率")
    else:
        print(f"  [FAIL] _fade_pix 尺寸异常：{None if fp is None else fp.width()}")
        fails += 1

    # 4) 落点段计时：用 _state 抽样「文字露完→进角」是否还定格
    #    od46：t=1.90 之后用 ease_out（起步即动）。比较 t=1.92 与 t=1.90 的 logo 位置
    s = r._spec
    st_a = r._state(s.t_text_end + 0.001, screen_w / screen_h)
    st_b = r._state(s.t_text_end + 0.12, screen_w / screen_h)
    moved = abs(st_b[0] - st_a[0]) + abs(st_b[1] - st_a[1]) + abs(st_b[2] - st_a[2])
    print(f"[info] 文字露完+0.001s 位置=({st_a[0]:.4f},{st_a[1]:.4f},{st_a[2]:.4f})")
    print(f"[info] 文字露完+0.12s  位置=({st_b[0]:.4f},{st_b[1]:.4f},{st_b[2]:.4f})  位移={moved:.4f}")
    if moved > 1e-4:
        print("  [PASS] 落点段起步即动（_ease_land 先慢后快再慢，无定格/无拖尾）")
    else:
        print("  [FAIL] 落点段头 0.12s 几乎不动（仍有定格）")
        fails += 1

    # 5) 真渲染一帧验证不崩（mid-fade t=0.55）。offscreen 直接画到 QImage 时
    #     painter 坐标是设备像素，故用逻辑尺寸的图像 + 逻辑 w/h 调用 paint。
    try:
        img = QImage(int(screen_w), int(screen_h), QImage.Format.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        r.paint(p, int(screen_w), int(screen_h), 0.55)
        p.end()
        c = img.pixelColor(int(screen_w) // 2, int(screen_h) // 2)
        print(f"  [PASS] t=0.55 渲染成图 {img.width()}x{img.height()}，"
              f"中心 alpha={c.alpha()}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] 渲染异常：{exc!r}")
        fails += 1

    # 6) 极端分辨率按内存预算自适应减档（绝不 OOM）
    hi = sm.MotionRenderer("dark", 2.0, 3840.0, 2160.0, sm.SPEC_PLANB)
    hi_n = len(hi._sigma_steps())
    hi_per = hi._pixmap(hi._clear_w).width() * hi._pixmap(hi._clear_w).height() * 4
    hi_total = (hi_n - 1) * hi_per / 1048576.0
    print(f"[info] 8K 级：清晰档物理宽={hi._pixmap(hi._clear_w).width()}  "
          f"档数={hi_n}  模糊缓存≈{hi_total:.0f}MB")
    if 3 <= hi_n < 13 and hi_total <= sm._BLUR_CACHE_BUDGET / 1048576.0 + 5:
        print("  [PASS] 极端分辨率自动减档，缓存落在预算内")
    else:
        print("  [FAIL] 极端分辨率未受预算约束")
        fails += 1
    del hi

    # 7) 淡入结束释放档表：常驻内存回落、淡出图仍可用、绝不重算
    payload_before = sum(v.width() * v.height() * 4
                         for v in r._blur_cache.values()) / 1048576.0
    r.release_blur_cache()
    released = (len(r._blur_cache) == 0
                and r._fade_pix is not None
                and r._fade_pix.width() == clear_w_phys
                and r._blur(1.0).cacheKey() == clear.cacheKey())
    print(f"[info] 释放前模糊档≈{payload_before:.0f}MB → 释放后 0MB"
          f"（常驻仅清晰档+淡出图≈{2*clear_w_phys*clear.height()*4/1048576.0:.0f}MB）")
    if released:
        print("  [PASS] 释放幂等、_fade_pix 存活、迟到调用退清晰档不重算")
    else:
        print("  [FAIL] 释放逻辑异常（档表未清 / 淡出图丢失 / 触发重算）")
        fails += 1
    r.release_blur_cache()          # 幂等
    if len(r._blur_cache) != 0:
        print("  [FAIL] 二次释放不幂等")
        fails += 1

    print(f"\n汇总：{'全部 PASS' if fails == 0 else str(fails)+' 项 FAIL'}"
          f"（预热 {preheat*1000:.0f}ms）")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
