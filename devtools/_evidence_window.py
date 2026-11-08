# -*- coding: utf-8 -*-
r"""evidence-recorder 逐拍取证①：最大化/还原覆盖层（只读取证，不改产品源码）。

回答用户「最大化/还原还是有拉伸 / 闪」到底发生在哪一帧、是什么现象。
与现成 gdigrab 屏幕录屏（实测 ~14fps、300ms 动画只 4 帧）互补：本探针在
**每个 _on_cover_tick 拍**抓一次「目标窗口自身」的渲染内容（QWidget.grab，
空图时 PrintWindow 兜底），不受桌面遮挡、不漏帧，精确定位：
  - t>=0.99 的 A→B 换源帧（_on_cover_tick main_window.py:3400-3403）
  - t=1.0 收尾 hide 覆盖层 + 露真窗（_finish_window_cover main_window.py:3436）
  - 逐拍 label 几何宽高比 / 源图比例 / 内容矩形比例 / 对称裁切量序列

⛔ 干扰声明：逐拍 grab 给每拍增加数 ms，动画被轻微拉长；但换源、收尾、
   裁切量都是离散事件与几何读数，不受拉长影响。未包装的真实时序由现成
   gdigrab/ddagrab 屏幕录屏旁证（见 _check/evidence_rerun_*.log）。
⛔ 只读取证：运行时 monkey-patch（内存），不写任何产品文件、不 git、不打包。
⛔ QSettings 全量快照 + finally 还原（删新增键、实例关闭后新开核对）。
⛔ _check/.app_run.lock 互斥（≤240s）。产物 _check/evidence_window/。

用法：.venv/Scripts/python.exe devtools/_evidence_window.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time

_T0 = time.perf_counter()


def _ms() -> float:
    return (time.perf_counter() - _T0) * 1000.0


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

OUT = os.path.join(_ROOT, "_check", "evidence_window")
os.makedirs(OUT, exist_ok=True)
LOCK = os.path.join(_ROOT, "_check", ".app_run.lock")

_SNAPSHOT: dict = {}
FRAMES: list[dict] = []          # 每拍：{seq,w,h,data(RGBA8888 bytes),meta}
EVENTS: list[tuple[float, str]] = []
_SEQ = {"n": 0}
_MAX_W = 1400                    # 存帧统一缩放上限（物理像素测量走 meta，不靠存帧）


def _log(tag: str) -> None:
    EVENTS.append((_ms(), tag))
    print(f"EVENT {_ms():7.1f} {tag}", flush=True)


# ---------------------------------------------------------------- 锁 / 设置
def _acquire_lock(wait_s: float = 240.0) -> bool:
    t0 = time.time()
    while True:
        try:
            os.mkdir(LOCK)
            with open(os.path.join(LOCK, "owner.txt"), "w", encoding="utf-8") as fh:
                fh.write("evidence-window pid=%d\n" % os.getpid())
            return True
        except FileExistsError:
            if time.time() - t0 > wait_s:
                return False
            time.sleep(1.0)


def _release_lock() -> None:
    try:
        shutil.rmtree(LOCK, ignore_errors=True)
    except Exception:                                    # noqa: BLE001
        pass


def _restore_settings() -> None:
    from PySide6.QtCore import QSettings
    st = QSettings("MimoWorks", "SerialAssistant")
    for k in set(st.allKeys()) - set(_SNAPSHOT):
        st.remove(k)
    for k, v in _SNAPSHOT.items():
        st.setValue(k, v)
    st.sync()
    del st
    # 实例关闭后再开新实例核对（team-lead 要求）
    chk = QSettings("MimoWorks", "SerialAssistant")
    chk.sync()
    print(f"[核对] geometry={chk.value('geometry')!r} "
          f"startup_maximized={chk.value('custom/startup_maximized')!r}", flush=True)
    del chk


# ---------------------------------------------------------------- 子进程 app
def run_app() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(45, exit=True)
    from PySide6.QtCore import QSettings, Qt, QTimer
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication, QWidget

    st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: st.value(k) for k in st.allKeys()})
    del st

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_anim
    from main_window import MainWindow, _capture_native_window

    ui_anim.set_frame_hz(ui_anim.display_max_hz())
    scr = app.primaryScreen()
    sg = scr.geometry()
    dpr = float(scr.devicePixelRatio())
    print(f"ENV screen={sg.width()}x{sg.height()} dpr={dpr} "
          f"hz={ui_anim.frame_hz()}", flush=True)

    # 黑底垫底（防 WorkBuddy 抢镜）+ 主窗置顶
    backdrop = QWidget(None, Qt.Window | Qt.FramelessWindowHint
                       | Qt.WindowStaysOnTopHint)
    backdrop.setAttribute(Qt.WA_ShowWithoutActivating)
    backdrop.setAutoFillBackground(True)
    pal = backdrop.palette()
    pal.setColor(backdrop.backgroundRole(), QColor(0, 0, 0))
    backdrop.setPalette(pal)
    backdrop.setWindowFlag(Qt.WindowTransparentForInput, True)
    backdrop.setGeometry(sg)
    backdrop.show()
    backdrop.raise_()

    window = MainWindow()
    try:
        window._port_watch.stop()
    except Exception:                                    # noqa: BLE001
        pass
    w, h = 1134, 820
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    window.setGeometry(sg.x() + (sg.width() - w) // 2,
                       sg.y() + max(60, (sg.height() - h) // 3), w, h)
    window.show()
    window.raise_()
    window.activateWindow()
    _log("window_shown")

    # ---- 抓「目标窗口自身」渲染：grab 优先，空图时 PrintWindow 兜底 ----
    def _to_rgba(pix, limit_w=_MAX_W):
        """QPixmap -> (w,h,RGBA8888 bytes)。统一缩到 <=limit_w 宽，避免内存爆。"""
        if pix is None or pix.isNull():
            return None
        img = pix.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        if img.width() > limit_w:
            img = img.scaledToWidth(limit_w, Qt.TransformationMode.SmoothTransformation)
            img = img.convertToFormat(QImage.Format.Format_RGBA8888)
        ww, hh = img.width(), img.height()
        if ww <= 0 or hh <= 0:
            return None
        return ww, hh, bytes(img.constBits())

    def _is_blank(data) -> bool:
        import numpy as np
        a = np.frombuffer(data, dtype=np.uint8)
        if a.size < 4:
            return True
        return float(a[::4].mean()) < 3.0 and float(a[::4].std()) < 2.0

    def _grab_widget(widget, tag: str, meta: dict):
        """抓一个控件自身渲染；记录用了 grab 还是 PrintWindow 兜底。"""
        method = "grab"
        got = None
        try:
            got = _to_rgba(widget.grab())
        except Exception:                                # noqa: BLE001
            got = None
        if got is not None and not _is_blank(got[2]):
            ww, hh, data = got
        else:
            # 顶层分层窗可能 grab 到空 → PrintWindow(PW_RENDERFULLCONTENT) 兜底
            try:
                pm = _capture_native_window(int(widget.winId()))
                got2 = _to_rgba(pm)
                if got2 is not None and not _is_blank(got2[2]):
                    ww, hh, data = got2
                    method = "printwindow"
                elif got is not None:
                    ww, hh, data = got
                    method = "grab(blank)"
                else:
                    return
            except Exception:                            # noqa: BLE001
                if got is None:
                    return
                ww, hh, data = got
        meta = dict(meta)
        meta.update(tag=tag, method=method, dpr=dpr)
        _SEQ["n"] += 1
        FRAMES.append({"seq": _SEQ["n"], "w": ww, "h": hh,
                       "data": data, "meta": meta})
        return _SEQ["n"]

    def _read_meta(self, phase: str, note: str = "") -> dict:
        m = {"seq": _SEQ["n"] + 1, "wall": _ms(), "phase": phase,
             "note": note}
        try:
            elapsed = self._cover_clock.elapsed()
            dur = float(self._cover_duration)
            m["t"] = round(min(1.0, elapsed / dur), 5) if dur > 0 else -1.0
            m["elapsed"] = elapsed
            m["dur"] = dur
        except Exception:                                # noqa: BLE001
            m["t"] = -1.0
        lab = self._cover_label
        src = getattr(lab, "_src", None) if lab is not None else None
        g = None
        if lab is not None:
            try:
                g = lab.geometry()
                m["label"] = (g.x(), g.y(), g.width(), g.height())
                m["cov_vis"] = lab.isVisible()
                m["cov_op"] = round(float(lab.windowOpacity()), 3)
            except RuntimeError:
                m["label"] = None
        else:
            m["label"] = None
        if src is not None and not src.isNull():
            sw, sh = src.width(), src.height()
            m["src"] = (sw, sh)
            m["src_ar"] = round(sw / sh, 6) if sh else -1.0
            try:
                m["src_key"] = int(src.cacheKey())
            except Exception:                            # noqa: BLE001
                m["src_key"] = -1
        else:
            m["src"] = None
            m["src_ar"] = -1.0
            m["src_key"] = -1
        # A/B 判定：当前 _src 的 cacheKey 与 _cover_pix / _cover_pix_b 比对
        ka = kb = -1
        try:
            if self._cover_pix is not None and not self._cover_pix.isNull():
                ka = int(self._cover_pix.cacheKey())
            if self._cover_pix_b is not None and not self._cover_pix_b.isNull():
                kb = int(self._cover_pix_b.cacheKey())
        except Exception:                                # noqa: BLE001
            pass
        m["a_key"], m["b_key"] = ka, kb
        m["which"] = ("B" if kb > 0 and m["src_key"] == kb
                      else ("A" if ka > 0 and m["src_key"] == ka else "?"))
        # 内容矩形 / 对称裁切量（复刻 _CoverLabel.paintEvent main_window.py:660-673）
        if g is not None and src is not None and not src.isNull() and sh:
            lw, lh = g.width(), g.height()
            m["label_ar"] = round(lw / lh, 6) if lh else -1.0
            rect_ar = lw / max(1, lh)
            if m["src_ar"] > rect_ar:
                dw = int(round(lh * m["src_ar"])); dh = lh
                cx, cy = (lw - dw) // 2, 0
            else:
                dw = lw; dh = int(round(lw / m["src_ar"]))
                cx, cy = 0, (lh - dh) // 2
            m["content"] = (dw, dh)
            m["content_ar"] = round(dw / max(1, dh), 6)
            m["crop_x"], m["crop_y"] = cx, cy
            # 裁切中心相对 label 中心的漂移（对称裁边应恒为 0）
            m["center_dx"] = cx + dw / 2 - lw / 2
            m["center_dy"] = cy + dh / 2 - lh / 2
        else:
            m["label_ar"] = m["content_ar"] = -1.0
            m["content"] = None
            m["crop_x"] = m["crop_y"] = None
            m["center_dx"] = m["center_dy"] = None
        try:
            m["win_op"] = round(float(self.windowOpacity()), 3)
            m["fs"] = bool(self.isFullScreen())
        except RuntimeError:
            m["win_op"], m["fs"] = -1.0, False
        return m

    # ---- 包装三件套 ----
    _orig_tick = MainWindow._on_cover_tick
    _orig_begin = MainWindow._begin_window_cover
    _orig_finish = MainWindow._finish_window_cover
    _phase = {"cur": "enter"}

    def _wrap_begin(self, *a, **kw):
        _log("begin_window_cover")
        return _orig_begin(self, *a, **kw)

    def _wrap_tick(self):
        t0 = time.perf_counter()
        _orig_tick(self)                                 # 先落定 set_source/geometry
        if self._cover_label is None:
            return
        m = _read_meta(self, _phase["cur"])
        m["cost_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        _grab_widget(self._cover_label, "cover_tick", m)

    def _wrap_finish(self, from_tick: bool = False):
        # 收尾「前」：覆盖层最后一帧 + 真窗当前帧
        m_pre = _read_meta(self, "finish_pre",
                           "覆盖层尚在，下一刻 hide")
        try:
            _grab_widget(self._cover_label, "finish_pre_cover", m_pre)
        except Exception:                                # noqa: BLE001
            pass
        try:
            _grab_widget(self, "finish_pre_main",
                         {**m_pre, "tag": "finish_pre_main"})
        except Exception:                                # noqa: BLE001
            pass
        _log(f"finish_before from_tick={from_tick}")
        out = _orig_finish(self, from_tick=from_tick)     # hide 覆盖层、露真窗
        _log("finish_after")
        # 收尾「后」5 帧：真窗已重排、覆盖层已撤；用 singleShot 让 DWM 合成一拍
        for i, delay in enumerate((0, 8, 16, 32, 48)):
            def _post(i=i):
                if window is None:
                    return
                mm = {"wall": _ms(), "phase": "finish_post",
                      "post_i": i, "t": 1.0, "which": "REAL",
                      "cov_vis": False, "note": "真窗露出/重排后"}
                try:
                    mm["win_op"] = round(float(self.windowOpacity()), 3)
                    mm["fs"] = bool(self.isFullScreen())
                except RuntimeError:
                    pass
                try:
                    _grab_widget(self, f"finish_post_{i}", mm)
                except Exception:                        # noqa: BLE001
                    pass
            QTimer.singleShot(delay, _post)
        return out

    MainWindow._on_cover_tick = _wrap_tick
    MainWindow._begin_window_cover = _wrap_begin
    MainWindow._finish_window_cover = _wrap_finish

    # ---- 触发：真按钮路径，进全屏 + 还原 ----
    def _click_max():
        _phase["cur"] = "enter"
        _log("click_max -> _toggle_fullscreen()")
        try:
            window._toggle_fullscreen()
        except Exception as exc:                         # noqa: BLE001
            _log(f"click_max EXC {exc!r}")

    def _click_restore():
        _phase["cur"] = "restore"
        _log("click_restore -> _toggle_fullscreen()")
        try:
            window._toggle_fullscreen()
        except Exception as exc:                         # noqa: BLE001
            _log(f"click_restore EXC {exc!r}")

    def _quit():
        _log("quit")
        try:
            _dump_and_analyze()
        except Exception as exc:                         # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"[warn] analyze: {exc!r}", flush=True)
        try:
            _restore_settings()
        except Exception as exc:                         # noqa: BLE001
            print(f"[warn] restore settings: {exc!r}", flush=True)
        # os._exit 绕过 __main__ 的 finally，这里显式释放锁
        _release_lock()
        sys.stdout.flush()
        os._exit(0)

    _wd = threading.Timer(28.0, lambda: os._exit(3))
    _wd.daemon = True
    _wd.start()
    QTimer.singleShot(1300, _click_max)
    QTimer.singleShot(3400, _click_restore)
    QTimer.singleShot(5600, _quit)
    print("STAGE: timers scheduled", flush=True)
    app.exec()
    _wd.cancel()
    return 0


# ---------------------------------------------------------------- 写盘 + 分析
def _dump_and_analyze() -> None:
    import cv2
    import numpy as np
    from PySide6.QtGui import QImage

    print(f"STAGE: dumping {len(FRAMES)} frames", flush=True)
    # 1) 全帧 PNG（事后写盘，不在 tick 热路径编码）
    for fr in FRAMES:
        qimg = QImage(fr["data"], fr["w"], fr["h"],
                      QImage.Format.Format_RGBA8888).copy()
        qimg.save(os.path.join(OUT, f"w{fr['seq']:03d}_{fr['meta'].get('tag','x')}.png"))
    # 2) 元数据
    metas = [fr["meta"] for fr in FRAMES]
    with open(os.path.join(OUT, "frames.json"), "w", encoding="utf-8") as fh:
        json.dump({"frames": [{k: v for k, v in fr.items() if k != "data"}
                              for fr in FRAMES],
                   "events": EVENTS}, fh, ensure_ascii=False, indent=1)

    # 3) 逐帧归一化差分（帧尺寸随动画变化 → 统一 resize 到 400x250 灰度再比）
    grays = []
    for fr in FRAMES:
        a = np.frombuffer(fr["data"], dtype=np.uint8).reshape(fr["h"], fr["w"], 4)
        g = cv2.cvtColor(a[:, :, :3], cv2.COLOR_RGB2GRAY)
        grays.append(cv2.resize(g, (400, 250), interpolation=cv2.INTER_AREA))
    diffs = []
    for i in range(1, len(grays)):
        d = cv2.absdiff(grays[i], grays[i - 1])
        diffs.append({"i": i, "seq": FRAMES[i]["seq"],
                      "area": float((d > 25).mean()),
                      "mean": float(d.mean()),
                      "dluma": float(grays[i].mean()) - float(grays[i - 1].mean())})

    lines = []
    def say(s=""):
        print(s, flush=True)
        lines.append(s)

    say("# evidence-window：最大化/还原覆盖层逐拍取证（od63 代码，只读取证）\n")
    say(f"- 每拍抓「目标窗口自身」渲染（QWidget.grab，空图 PrintWindow 兜底）；"
        f"存帧缩放上限 {_MAX_W}px 宽，物理比例量测走 meta 不受影响。")
    say(f"- 共 {len(FRAMES)} 帧 / {len(EVENTS)} 事件；"
        f"dpr 与各帧尺寸见 frames.json。")
    say("- ⚠️ 逐拍 grab 给每拍增加数 ms，动画被轻微拉长；换源/收尾/裁切量是"
        "离散事件与几何读数，不受拉长影响。未包装真实时序见现成屏幕录屏。\n")

    # 方法统计
    methods = {}
    for m in metas:
        methods[m.get("method", "?")] = methods.get(m.get("method", "?"), 0) + 1
    say(f"## 抓帧方法：{methods}\n")

    # 4) 逐拍表
    say("## 逐拍序列（label 几何 / 源图 / 内容比例 / 对称裁切 / 换源 / 收尾）\n")
    say("| seq | tag | t | 阶段 | label(WxH) | label_ar | src(WxH) | src_ar | "
        "content_ar | crop_x | crop_y | center_dx | 源 | win_op | cov_vis | "
        "grab耗时 | 帧间diff面积 | 帧间Δluma |")
    say("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, fr in enumerate(FRAMES):
        m = fr["meta"]
        d = diffs[i - 1] if i >= 1 else {}
        lab = m.get("label")
        src = m.get("src")
        say(f"| {fr['seq']} | {m.get('tag','')} | {m.get('t','')} | "
            f"{m.get('phase','')} | "
            f"{f'{lab[2]}x{lab[3]}' if lab else '-'} | {m.get('label_ar','')} | "
            f"{f'{src[0]}x{src[1]}' if src else '-'} | {m.get('src_ar','')} | "
            f"{m.get('content_ar','')} | {m.get('crop_x','')} | "
            f"{m.get('crop_y','')} | {m.get('center_dx','')} | "
            f"{m.get('which','')} | {m.get('win_op','')} | {m.get('cov_vis','')} | "
            f"{m.get('cost_ms','')} | "
            f"{round(d.get('area',0)*100,2) if d else ''} | "
            f"{round(d.get('dluma',0),2) if d else ''} |")

    # 5) 关键事件定位
    say("\n## 关键帧定位\n")
    cover_ticks = [(i, m) for i, m in enumerate(metas)
                   if m.get("tag") == "cover_tick"]
    swaps = []                                          # A→B 换源帧
    prev_which = None
    for i, m in cover_ticks:
        wk = m.get("which")
        if prev_which in ("A",) and wk in ("B",):
            swaps.append((i, m))
        if wk in ("A", "B"):
            prev_which = wk
    say(f"- A→B 换源帧（src cacheKey 从 A 变 B）："
        f"{[(FRAMES[i]['seq'], m.get('t'), m.get('phase')) for i, m in swaps] or '未捕获/全程A'}")
    late = [(FRAMES[i]["seq"], m.get("t"), m.get("which"), m.get("phase"))
            for i, m in cover_ticks if isinstance(m.get("t"), float) and m["t"] >= 0.95]
    say(f"- t>=0.95 的 tick 帧：{late}")
    fin_pre = [FRAMES[i]["seq"] for i, m in enumerate(metas)
               if m.get("phase") == "finish_pre"]
    fin_post = [FRAMES[i]["seq"] for i, m in enumerate(metas)
                if m.get("phase") == "finish_post"]
    say(f"- 收尾前帧 seq={fin_pre}；收尾后帧 seq={fin_post}")

    # 6) 跳变最大的帧
    say("\n## 帧间内容突变（归一化 400x250 后相邻帧 |diff|>25 面积%）\n")
    top = sorted(diffs, key=lambda x: x["area"], reverse=True)[:10]
    for d in top:
        m0 = metas[d["i"] - 1]; m1 = metas[d["i"]]
        say(f"- seq {FRAMES[d['i']-1]['seq']}→{d['seq']}: "
            f"面积={d['area']*100:.1f}% 均差={d['mean']:.1f} "
            f"Δluma={d['dluma']:+.1f}  "
            f"t {m0.get('t')}→{m1.get('t')}  "
            f"源 {m0.get('which')}→{m1.get('which')}  "
            f"tag {m0.get('tag')}→{m1.get('tag')}")

    # 7) 几何/裁切量化
    say("\n## 逐拍量化：比例与裁切序列\n")
    cas = [m.get("content_ar") for _, m in cover_ticks
           if isinstance(m.get("content_ar"), float) and m["content_ar"] > 0]
    lars = [m.get("label_ar") for _, m in cover_ticks
            if isinstance(m.get("label_ar"), float) and m["label_ar"] > 0]
    crops = [abs(m.get("crop_x") or 0) + abs(m.get("crop_y") or 0)
             for _, m in cover_ticks]
    cdx = [m.get("center_dx") for _, m in cover_ticks
           if m.get("center_dx") is not None]
    say(f"- content_ar（内容矩形宽高比，恒=源图比例即无畸变）："
        f"min={min(cas) if cas else '-'} max={max(cas) if cas else '-'} "
        f"跨帧变化={round(max(cas)-min(cas),6) if cas else '-'}")
    say(f"- label_ar（label 自身宽高比，随窗口长大/缩小连续变）："
        f"min={min(lars) if lars else '-'} max={max(lars) if lars else '-'}")
    say(f"- 对称裁切量 |crop_x|+|crop_y| 序列：{crops}")
    say(f"- 裁切中心漂移 center_dx 序列：{cdx}（全 0 = 对称裁边，中心不漂移）")
    if crops:
        dc = [abs(crops[i] - crops[i - 1]) for i in range(1, len(crops))]
        say(f"- 裁切量单帧最大变化 = {max(dc) if dc else 0}px "
            f"（第 {dc.index(max(dc))+2 if dc else '-'} 个 tick）")

    with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"STAGE: report -> {os.path.join(OUT,'report.md')}", flush=True)


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    from PySide6.QtCore import QSettings
    _st = QSettings("MimoWorks", "SerialAssistant")
    _SNAPSHOT.update({k: _st.value(k) for k in _st.allKeys()})
    del _st
    if "--app" in sys.argv:
        run_app()
        os._exit(0)
    if not _acquire_lock():
        print("LOCK busy (240s)", flush=True)
        sys.exit(2)
    try:
        run_app()
    finally:
        try:
            _restore_settings()
            print("[兜底] QSettings 已按父进程快照恢复", flush=True)
        except Exception as exc:                         # noqa: BLE001
            print(f"[warn] {exc!r}", flush=True)
        _release_lock()
    sys.exit(0)
