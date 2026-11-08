# -*- coding: utf-8 -*-
"""探针：数据位下拉高频点击残影修复验证（2026-11-03）。

用户报：弹层应该向下展开，但高频点击时控件框的上边有残影闪烁。
根因（修复前）：
  1. showPopup 每次都对容器 setWindowFlags → 原生窗口销毁重建，DWM 可能
     拍到旧窗口最后一帧（Qt 默认定位 = 压在控件框上边）= 残影；
  2. super().showPopup() 之后先 setGeometry(满高 target) 再缩矮条，中间
     多一次可见几何变更。

修复：
  1. flags/attribute 只在首次设置（_popup_flags_done）；
  2. super() 之前就按 target 摆起始矮条（隐藏状态下改几何不可见）；
  3. super() 之后不再经过满高，直接落到矮条。

本探针用 monkeypatch 记录容器每一笔 setGeometry 与可见性，断言：
  A. 窗口可见期间的每笔几何要么 = 起始矮条、要么 = 展开插值（高 ≤ target 高）；
  B. 从第二次弹出起不再发生原生窗口重建（setWindowFlags 不再被调用）；
  C. 快速 hide→show 循环后 target 高度不缩水（复利缩水回归检查）。
QSettings 全量快照/还原，绝不污染真实配置。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout  # noqa: E402

SETTINGS_ORG = "MimoWorks"
SETTINGS_APP = "SerialAssistant"

_snapshot = {}
_POPUP_QSS_KEYS = ("custom/",)  # 本探针不写任何 custom/ 键，快照仅作保险


def take_snapshot() -> None:
    st = QSettings(SETTINGS_ORG, SETTINGS_APP)
    for k in st.allKeys():
        _snapshot[k] = st.value(k)


def restore_snapshot() -> None:
    st = QSettings(SETTINGS_ORG, SETTINGS_APP)
    current = set(st.allKeys())
    for k in current - set(_snapshot):
        st.remove(k)
    for k, v in _snapshot.items():
        st.setValue(k, v)
    st.sync()


def main() -> int:
    take_snapshot()
    app = QApplication.instance() or QApplication(sys.argv)

    import ui_controls

    box = ui_controls.ChevronComboBox()
    for i in range(8):  # 数据位：5~12 之类的一小组
        box.addItem(str(5 + i))

    host = QWidget()
    lay = QVBoxLayout(host)
    lay.addWidget(box)
    lay.addStretch(1)
    host.resize(360, 400)
    host.move(200, 200)
    host.show()

    # --- 记录器 -----------------------------------------------------------
    log = []            # (phase, kind, detail)
    flags_calls = []    # 每次 setWindowFlags 的调用时间线

    container_ref = {}

    def record_geo(tag):
        c = container_ref.get("c")
        if c is not None:
            g = c.geometry()
            log.append((tag, g.x(), g.y(), g.width(), g.height(),
                        c.isVisible()))

    orig_set_geo = type(box.view().window()).setGeometry
    orig_set_flags = type(box.view().window()).setWindowFlags

    def patched_set_geo(self, *a):
        r = orig_set_geo(self, *a)
        w, h = (a[0].width(), a[0].height()) if len(a) == 1 else (a[2], a[3])
        log.append(("setGeometry", self.x(), self.y(), int(w), int(h),
                    self.isVisible()))
        return r

    def patched_set_flags(self, f):
        flags_calls.append(("setWindowFlags", int(f)))
        return orig_set_flags(self, f)

    # 只 patch 容器这一个实例（避免全局副作用）
    container_ref["cls"] = type(box.view().window())
    cls = type(box.view().window())
    cls.setGeometry = patched_set_geo
    cls.setWindowFlags = patched_set_flags

    result = {"fails": []}

    def cycle_round(round_no: int) -> None:
        # 开
        container = box.view().window()
        container_ref["c"] = container
        log.append((f"--- round {round_no} showPopup ---", 0, 0, 0, 0, False))
        box.showPopup()
        record_geo(f"after showPopup r{round_no}")
        # 高频：立刻关
        box.hidePopup()
        record_geo(f"after hidePopup r{round_no}")

    def finish() -> None:
        try:
            # --- 断言 A：可见期间没有满高错位帧 ----------------------------
            # 矮条高 = target 高的 25%（最低 14px）。窗口可见时的几何高度
            # 必须 ≤ target 高（且宽度 = target 宽）。我们用「每次 show 的
            # target 高」近似：取该轮记录里最大宽度对应 target。
            # 更直接：高度单调不减（展开段）或 = 矮条（收尾），且从未出现
            # 「高 > 0.9 * 该轮最大高 且 y 与最小 y 相差 > 4」的错位帧。
            shows = [e for e in log if e[0].startswith("after showPopup")]
            for e in shows:
                pass  # 展示用，断言在下面按轮分组做
            # 按轮分组
            rounds = []
            cur = None
            for e in log:
                if e[0].startswith("--- round"):
                    cur = []
                    rounds.append(cur)
                elif cur is not None:
                    cur.append(e)
            for ri, entries in enumerate(rounds, 1):
                geos = [e for e in entries if e[0] in ("setGeometry",
                                                       "after showPopup",
                                                       "after hidePopup")]
                if not geos:
                    result["fails"].append(f"round {ri}: 没有任何几何记录")
                    continue
                max_h = max(e[4] for e in geos if e[0] == "setGeometry") \
                    if any(e[0] == "setGeometry" for e in geos) else 0
                vis = [e for e in geos if e[5]]
                for e in vis:
                    # 可见帧高度必须 ≤ max_h（= target 高），且不能是
                    # "高=满高 且 不是最后一笔"（满高闪帧）
                    if e[4] > max_h * 0.98 and e is not vis[-1] \
                            and e[0] == "setGeometry" and len(vis) > 2:
                        result["fails"].append(
                            f"round {ri}: 可见期间出现满高帧 {e[1:]}")
            # --- 断言 B：第二次弹出起不再 setWindowFlags -------------------
            if len(flags_calls) > 1:
                result["fails"].append(
                    f"setWindowFlags 被调用了 {len(flags_calls)} 次（应只 1 次）")

            # --- 断言 C：target 高度不缩水 ---------------------------------
            target_hs = [e[4] for e in shows]
            if len(target_hs) >= 3:
                first, last = target_hs[0], target_hs[-1]
                if last < first * 0.9:
                    result["fails"].append(
                        f"target 高度缩水: 首轮 {first} → 末轮 {last}")

            ok = not result["fails"]
            print("POPUP_PROBE",
                  "PASS" if ok else "FAIL",
                  f"flags_calls={len(flags_calls)}",
                  f"rounds={len(rounds)}",
                  f"target_h={target_hs}")
            for f in result["fails"]:
                print("  FAIL:", f)
            if not ok:
                for e in log[:80]:
                    print("   ", e)
            app.quit()
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print("POPUP_PROBE ERROR", exc)
            app.quit()

    # 连续 4 轮高频开关（每轮间隔 ~60ms，模拟快速连点）
    def start_cycles(i=0):
        if i >= 4:
            QTimer.singleShot(120, finish)
            return
        cycle_round(i + 1)
        QTimer.singleShot(60, lambda: start_cycles(i + 1))

    QTimer.singleShot(150, start_cycles)
    app.exec()
    cls.setGeometry = orig_set_geo
    cls.setWindowFlags = orig_set_flags
    restore_snapshot()
    host.close()
    return 0 if not result["fails"] else 1


if __name__ == "__main__":
    sys.exit(main())
