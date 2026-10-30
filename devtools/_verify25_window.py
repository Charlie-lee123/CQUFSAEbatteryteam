# -*- coding: utf-8 -*-
r"""v2.5 验收探针 B：主窗口链路 / 改名 / QSettings 未丢 / 产物核对。

  W  _emit_block 真的把数据喂进 rx_export（不是只有纯模块单测能过）
     + 真机帧经窗口链路导出的数值（与显示层口径对账）
     + _feed_export 抛异常时接收不受影响
  N  改名完整性（窗口标题 / 页头 / applicationName / 日志目录 / 设置面板按钮）
  S  QSettings 键没被改名带走（既有配置仍能读）
  P  产物 发布/SerialAssistant.exe：PYZ 里 main_window 的窗口标题常量

⚠️ 铁律：开主窗口前对 QSettings("MimoWorks","SerialAssistant") 做全量快照，
   finally 按快照还原（新键要删）；不 deleteLater + processEvents。

用法：QT_QPA_PLATFORM=windows .venv\Scripts\python.exe devtools\_verify25_window.py
产物：_check/verify25_window.txt
"""
from __future__ import annotations

import csv
import os
import sys
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtCore import QSettings                                 # noqa: E402
from PySide6.QtWidgets import QApplication                            # noqa: E402

OUT = os.path.join(_ROOT, "_check")
os.makedirs(OUT, exist_ok=True)
ORG, APP = "MimoWorks", "SerialAssistant"
OLD_NAME = "CQUFSAE串口调试助手"
NEW_NAME = "CQUFSAE串口助手"

_RESULTS: list[tuple[str, bool, str]] = []
_LINES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((name, bool(ok), detail))
    line = f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else "")
    print(line)
    _LINES.append(line)
    return ok


def info(name: str, detail: str) -> None:
    line = f"INFO  {name}   [{detail}]"
    print(line)
    _LINES.append(line)


# ---------------------------------------------------------------- 产物核对
def check_exe(rel: str) -> None:
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    exe = os.path.join(_ROOT, rel)
    if not os.path.isfile(exe):
        check(f"P1 产物存在 {rel}", False, "文件不存在")
        return
    check(f"P1 产物存在 {rel}", True, f"{os.path.getsize(exe) / 1e6:.1f}MB")

    import marshal
    import tempfile
    import types

    ca = CArchiveReader(exe)
    pyz_name = None
    for name in ca.toc:
        nm = name[0] if isinstance(name, tuple) else name
        if isinstance(nm, str) and nm.startswith("PYZ"):
            pyz_name = nm
            break
    if not pyz_name:
        check("P2 PYZ 存在", False, "")
        return
    fd, tmp = tempfile.mkstemp(suffix=".pyz")
    os.write(fd, ca.extract(pyz_name))
    os.close(fd)
    try:
        def consts_of(mod: str) -> list[str]:
            data = ZlibArchiveReader(tmp).extract(mod)
            code = data if isinstance(data, types.CodeType) else marshal.loads(bytes(data)[16:])

            def walk(co):
                for c in co.co_consts:
                    if isinstance(c, str):
                        yield c
                    elif isinstance(c, types.CodeType):
                        yield from walk(c)
            return list(walk(code))

        try:
            mw = consts_of("main_window")
        except Exception as exc:                     # noqa: BLE001
            check("P2 从 exe 抽出 main_window", False, str(exc))
            return
        check("P2 exe 里的 main_window 含新名常量", NEW_NAME in mw,
              f"命中 {mw.count(NEW_NAME)} 次")
        check("P3 exe 里的 main_window 无旧名常量", OLD_NAME not in mw,
              f"命中 {mw.count(OLD_NAME)} 次")
        try:
            main_c = consts_of("main")
        except Exception:                            # noqa: BLE001
            # 入口脚本不在 PYZ 里，而是 CArchive 的一个 script 条目（marshal 字节）
            try:
                raw_main = ca.extract("main")
                code = marshal.loads(bytes(raw_main))
                main_c = []

                def walk2(co):
                    for c in co.co_consts:
                        if isinstance(c, str):
                            main_c.append(c)
                        elif isinstance(c, types.CodeType):
                            walk2(c)
                walk2(code)
            except Exception:                        # noqa: BLE001
                main_c = []
        check("P4 exe 里的 main 含新名（setApplicationName）", NEW_NAME in main_c,
              f"命中 {main_c.count(NEW_NAME)} 次")
        try:
            rx = consts_of("rx_export")
            has_rx = True
        except Exception:                            # noqa: BLE001
            has_rx = False
            rx = []
        check("P5 exe 里打包了 rx_export 模块", has_rx, f"{len(rx)} 个常量")
    finally:
        os.remove(tmp)


# ---------------------------------------------------------------- 主窗口
def check_window(app: QApplication, settings: QSettings) -> None:
    import rx_export
    import app_log
    app_log.init()
    from main_window import MainWindow

    rx_export.reset_for_tests()
    window = MainWindow()
    window.setGeometry(0, 0, 1134, 820)
    window.show()
    app.processEvents()
    try:
        # ---------------- N 改名
        check("N1 窗口标题 = CQUFSAE串口助手", window.windowTitle() == NEW_NAME,
              repr(window.windowTitle()))
        brand = getattr(window, "brand_header", None)
        product = getattr(brand, "_product", None)
        check("N2 页头产品名 = 串口助手（旧「串口调试助手」已不在）",
              product == "串口助手", f"页头 _product = {product!r}")
        src_main = open(os.path.join(_ROOT, "main.py"), encoding="utf-8").read()
        check("N3 main.py setApplicationName 用新名",
              f'app.setApplicationName("{NEW_NAME}")' in src_main, "源码命中")
        import app_log
        log_dir = os.path.dirname(str(app_log.path() or ""))
        check("N4 日志目录仍是 CQUFSAE-SerialAssistant（内部标识未动）",
              "CQUFSAE-SerialAssistant" in log_dir, log_dir)
        check("N5 组织名仍是 MimoWorks",
              f'app.setOrganizationName("{ORG}")' in src_main, "源码命中")

        # ---------------- S 配置未丢
        keys = settings.allKeys()
        check("S1 QSettings(MimoWorks/SerialAssistant) 里仍有既有键",
              len(keys) > 0, f"{len(keys)} 个键，例 {keys[:3]}")
        check("S2 仍有 custom/ 子键（内部标识没被改名带走）",
              any(k.startswith("custom/") for k in keys),
              f"custom 键 {len([k for k in keys if k.startswith('custom/')])} 个")
        new_store = QSettings(ORG, NEW_NAME)
        old_store = QSettings(ORG, OLD_NAME)
        check("S3 新显示名不是存储位置（那里是空的）",
              new_store.allKeys() == [], f"{new_store.allKeys()[:3]}")
        check("S4 旧显示名也不是存储位置", old_store.allKeys() == [],
              f"{old_store.allKeys()[:3]}")

        # ---------------- W 主窗口链路
        # 钉死一套导出尺寸，不依赖用户当前车辆档案
        window._export_profile_now = lambda: ([3, 3, 3], [2, 2, 2])
        window._rx_export_profile = None
        frame = ("Voltage1:\n3.8712 3.8001 3.7999 4.1234 4.0000 0.0000 "
                 "3.9000 3.9001 3.9000\n"
                 "Temperature1:\n25.5 26.0\n-5.5 0.0\n24.0 25.05\n"
                 "BMS OK\n")
        window._on_received(frame.encode("utf-8"))
        window._flush_rx(force=True)
        app.processEvents()
        rounds = rx_export.session().rounds()
        check("W1 _emit_block 真的喂进了 rx_export.session()",
              len(rounds) == 1, f"{len(rounds)} 轮")
        if rounds:
            check("W2 经窗口链路也是 3 模组 × 3 芯",
                  [len(m) for m in rounds[0]["volt"]] == [3, 3, 3],
                  f"{[len(m) for m in rounds[0]['volt']]}")
            out = os.path.join(OUT, "v25_window.csv")
            rx_export.session().write_csv(out)
            table = list(csv.reader(open(out, encoding="utf-8-sig", newline="")))
            head = table[0]
            i = head.index("电芯1(mV)")
            check("W3 窗口链路导出的模组2首芯落在电芯1列",
                  table[2][i] == "4123", f"模组2 电芯1 = {table[2][i]}")
        check("W4 接收区有内容", bool(window.txt_rx.toPlainText().strip()),
              f"{len(window.txt_rx.toPlainText())} 字符")

        # 真机帧（序号.mV）经窗口链路：暴露与显示层口径的差距
        rx_export.reset_for_tests()
        window._emit_block("Voltage1:\n1.3851 2.3852 3.3853\nBMS OK")
        dev = rx_export.session().rounds()
        got = [v for m in (dev[0]["volt"] if dev else []) for v in m]
        check("W5 真机帧经窗口链路导出 3851 mV（显示层口径）",
              got == [3.851, 3.852, 3.853],
              f"实得 {got}（×1000 = {[round(v * 1000) for v in got]} mV）")

        # ---------------- W 异常注入：不影响接收
        before = window.txt_rx.toPlainText()
        def boom():
            raise RuntimeError("验收探针注入：_export_profile_now 爆炸")
        orig_feed = rx_export.Session.feed
        try:
            window._export_profile_now = boom
            window._rx_export_profile = None
            window._emit_block("Temperature1:\n21.5 22.5\nBMS OK")
            app.processEvents()
            after = window.txt_rx.toPlainText()
            check("W6 _export_profile_now 抛异常：接收区照常增长（不吞包）",
                  len(after) > len(before), f"{len(before)} → {len(after)} 字符")

            window._export_profile_now = lambda: ([3], [1])

            def feed_boom(*_a, **_k):
                raise ValueError("验收探针注入：feed 爆炸")
            rx_export.Session.feed = feed_boom
            window._rx_export_profile = None
            before2 = window.txt_rx.toPlainText()
            window._emit_block("Voltage1:\n3.8712 3.8712 3.8712\nBMS OK")
            app.processEvents()
            after2 = window.txt_rx.toPlainText()
            check("W7 rx_export.feed 抛异常：接收区照常增长",
                  len(after2) > len(before2), f"{len(before2)} → {len(after2)} 字符")
        finally:
            window.__dict__.pop("_export_profile_now", None)
            rx_export.Session.feed = orig_feed

        # ---------------- 设置面板两颗按钮
        import ui_settings
        dlg = ui_settings.SettingsDialog("dark", 55, None, [])
        try:
            check("N6 设置面板有「数据 CSV」按钮",
                  dlg.btn_export_csv.text() == "数据 CSV", dlg.btn_export_csv.text())
            check("N7 设置面板有「数据 JSON」按钮",
                  dlg.btn_export_json.text() == "数据 JSON", dlg.btn_export_json.text())
            # 真点一次（把文件对话框换成固定路径），验证槽 → rx_export → 落盘
            rx_export.reset_for_tests()
            rx_export.session().configure([3], [1])
            rx_export.session().feed("Voltage1:\n3.8712 3.8712 3.8712\nBMS OK")
            target = os.path.join(OUT, "v25_dialog_export.csv")
            if os.path.exists(target):
                os.remove(target)
            orig_dialog = ui_settings.QFileDialog.getSaveFileName
            ui_settings.QFileDialog.getSaveFileName = staticmethod(
                lambda *_a, **_k: (target, "CSV 表格 (*.csv)"))
            try:
                dlg.btn_export_csv.click()
                app.processEvents()
            finally:
                ui_settings.QFileDialog.getSaveFileName = orig_dialog
            ok = os.path.exists(target) and os.path.getsize(target) > 0
            head2 = ""
            if ok:
                head2 = open(target, encoding="utf-8-sig").readline().strip()
            check("N8 点「数据 CSV」真的落盘且是宽表表头",
                  ok and head2.startswith("时间,轮次,模组,电芯1(mV)"),
                  f"{head2[:60]!r}")
            check("N9 导出后按钮回显 ✓（用户能看见结果）",
                  dlg.btn_export_csv.text().startswith("✓"),
                  dlg.btn_export_csv.text())
        finally:
            dlg.close()
    finally:
        try:
            if window.isFullScreen():
                window.showNormal()
        except RuntimeError:
            pass
        window.setGeometry(0, 0, 1134, 820)
        window.close()
        app.processEvents()


def main() -> int:
    _LINES.extend(["=" * 92, "v2.5 验收 B：主窗口链路 / 改名 / 配置 / 产物", "=" * 92])

    check_exe(os.path.join("发布", "SerialAssistant.exe"))

    app = QApplication.instance() or QApplication(sys.argv[:1])
    settings = QSettings(ORG, APP)
    snapshot = {k: settings.value(k) for k in settings.allKeys()}
    info("快照 QSettings", f"{len(snapshot)} 个键")
    try:
        check_window(app, settings)
    except Exception:                                # noqa: BLE001
        check("X 主窗口段执行完", False, traceback.format_exc(limit=3).replace("\n", " | "))
    finally:
        restore(settings, snapshot)
        # 再验一次：还原后既有键一个不少、值不变
        keys = set(settings.allKeys())
        missing = [k for k in snapshot if k not in keys]
        changed = [k for k in snapshot if k in keys
                   and str(settings.value(k)) != str(snapshot[k])]
        check("S5 finally 还原后配置一个不差（不污染用户设置）",
              not missing and not changed, f"缺 {missing[:3]} 变 {changed[:3]}")

    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    tail = f"\n合计 {passed}/{total} PASS"
    print(tail)
    _LINES.append(tail)
    with open(os.path.join(OUT, "verify25_window.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(_LINES) + "\n")
    return 0 if passed == total else 1


def restore(settings: QSettings, snapshot: dict) -> None:
    for key in set(settings.allKeys()) - set(snapshot):
        settings.remove(key)
    for key, value in snapshot.items():
        if value is not None:
            settings.setValue(key, value)
    settings.sync()


if __name__ == "__main__":
    sys.exit(main())
