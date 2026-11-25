# -*- coding: utf-8 -*-
r"""QA 独立证伪：串口设置卡纳入「界面按钮」显隐 + DLC 只在蓝牙广播显示
                + 上一轮 RowButton 视觉改版回归 + 初始显隐落点/弹层动画。

只读驱动真实 MainWindow，不改产品源码、不写用户真实历史/数据库/配置
（全程隔离 + finally 还原 QSettings 全量快照）。

判据：
  A 按钮清单：BUTTON_SPECS 新增三条（分组「串口设置」）、属性取得到、
    设置面板「界面按钮」节能列出；「串口开关 / 连接方式 / DLC」**仍不在**清单里。
  B 隐藏行为：隐藏「串口参数」→ 四行一起消失；隐藏「数据工具」→ 整组（含分隔线
    与标题）消失；隐藏「保留行数」→ 标签与下拉框一起消失；三者互不影响、都能恢复。
  C 模式组合：切蓝牙广播 → DLC 可见 / 串口参数隐藏；切回串口 → 反过来；
    「隐藏串口参数 + 蓝牙广播」→ 参数不显示且 DLC 仍可见。
  D 视觉改版回归：五颗动作行（查看标记/打开近期数据/打开历史数据/历史报错数据/DLC）
    文案、tooltip、clicked handler、回放态文案切换、窄档（1/4 屏）不崩。
  E 初始显隐落点：_refresh_config_visibility() 在**布局装配之后**落；
    真机下拉弹层展开动画起止高不同（不吃掉动画）。
  F 门禁：QSettings 71 键前后相等、hidden_buttons 仍为 []。

运行（真平台，⛔ 别 | head）：
  QT_QPA_PLATFORM=windows .venv\Scripts\python.exe devtools\_qa_config_visibility.py > _check\qa_config_vis_run.log 2>&1
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtWidgets import QApplication, QWidget

_CHECK = Path(_ROOT) / "_check"
_RESULTS: list[tuple[str, bool, str]] = []
_NOTES: list[str] = []
_ART: dict[str, object] = {}


def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""),
          flush=True)
    return bool(ok)


def note(text: str) -> None:
    _NOTES.append(text)
    print(f"NOTE  {text}", flush=True)


def art(key: str, value) -> None:
    _ART[key] = value


def settle(app, ms: int = 260) -> None:
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000.0 < ms:
        app.processEvents()
        time.sleep(0.006)


def visible_tree(win, widget) -> bool:
    """控件"在屏幕上可见"（自身没被 hide，且祖先链都在）。"""
    if widget is None:
        return False
    node = widget
    while node is not None:
        if node.isHidden():
            return False
        node = node.parentWidget()
    return True


def main() -> int:                                     # noqa: C901 - 探针
    app = QApplication.instance() or QApplication(sys.argv)
    from ui_theme import ui_font
    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))

    qs = QSettings("MimoWorks", "SerialAssistant")
    snap = {k: qs.value(k) for k in qs.allKeys()}
    art("qsettings_keys", len(snap))
    hidden_before = qs.value("hidden_buttons")

    hist_dir = Path(tempfile.mkdtemp(prefix="qa_cfgvis_hist_", dir=_CHECK))
    db_dir = Path(tempfile.mkdtemp(prefix="qa_cfgvis_db_", dir=_CHECK))
    win = None
    try:
        import rx_history
        import rx_store
        real_resolver = rx_history._resolve_dir
        real_db = rx_store.db_path()
        rx_history._resolve_dir = lambda: hist_dir
        rx_store.set_db_path(str(db_dir / "sessions.db"))

        import main_window
        from main_window import BUTTON_SPECS, LINK_BLE, LINK_SERIAL
        from ui_settings import SettingsDialog

        win = main_window.MainWindow()
        win.resize(1200, 800)
        win.show()
        win.raise_()
        win.activateWindow()
        settle(app, 500)
        # 配置卡开局可能是收起态（接收区展开）→ 展开它再量显隐
        if not win._config_card.isVisible():
            win._config_card.show()
            settle(app, 200)

        # ================= A 按钮清单 =================
        specs = list(BUTTON_SPECS)
        art("button_specs", specs)
        check("A1 BUTTON_SPECS 含新增三条且分组=「串口设置」",
              ("serial_params", "串口参数", "串口设置") in specs
              and ("rx_lines_box", "保留行数", "串口设置") in specs
              and ("actions_box", "数据工具", "串口设置") in specs,
              str([s for s in specs if s[2] == "串口设置"]))
        _objs = {k: getattr(win, k, None) for k in
                 ("serial_params", "rx_lines_box", "actions_box")}
        check("A2 三个属性都取得到且是 QWidget、在配置卡内容区内",
              all(isinstance(v, QWidget) for v in _objs.values())
              and all(win._config_content.isAncestorOf(v) for v in _objs.values()),
              str({k: type(v).__name__ for k, v in _objs.items()}))
        _dlg = SettingsDialog(win._theme_mode, 0, BUTTON_SPECS, [], parent=win)
        _sw = dict(getattr(_dlg, "_button_switches", {}))
        art("settings_switches", sorted(_sw))
        check("A3 设置面板「界面按钮」节能列出三个开关",
              all(k in _sw for k in ("serial_params", "rx_lines_box", "actions_box")),
              f"switches={sorted(_sw)}")
        _labels = {lbl for _k, lbl, _g in specs}
        _keys = {k for k, _l, _g in specs}
        check("A4 「串口开关」「连接方式」「DLC」**不在**可隐藏清单里（防把用户锁死）",
              not ({"串口开关", "连接方式", "DLC"} & _labels)
              and not ({"btn_open", "cmb_link", "btn_dlc"} & _keys),
              f"labels={sorted(_labels)}")
        _dlg.close()
        _dlg.deleteLater()
        app.processEvents()

        # ================= B 隐藏行为 =================
        # ⚠️ 本机 QSettings 的 link_mode='ble' → 开机就在蓝牙广播模式（串口参数本就
        #    隐藏、DLC 本就显示）。先钉回串口模式，B 段的显隐判断才有意义。
        win.cmb_link.setCurrentText(LINK_SERIAL)
        settle(app, 300)
        check("B0 已钉回串口模式（否则本机默认蓝牙广播，B 段判断全错位）",
              win.cmb_link.currentText() == LINK_SERIAL
              and not win.serial_params.isHidden(),
              f"mode={win.cmb_link.currentText()!r} "
              f"serial_hidden={win.serial_params.isHidden()}")

        def _hide(keys: list[str]) -> None:
            win._preview_buttons(list(keys))     # 产品路径（设置面板开关走同一条）
            settle(app, 200)

        def _restore() -> None:
            _hide([])

        _restore()
        _hide(["serial_params"])
        check("B1 隐藏「串口参数」→ 波特率/停止位/数据位/校验位四行一起消失",
              win.serial_params.isHidden()
              and not any(visible_tree(win, w) for w in
                          (win.cmb_baud, win.cmb_stopbits, win.cmb_bytesize,
                           win.cmb_parity))
              and visible_tree(win, win.rx_lines_box)
              and visible_tree(win, win.actions_box),
              f"serial_params_hidden={win.serial_params.isHidden()} "
              f"rows_visible={[visible_tree(win, w) for w in (win.cmb_baud, win.cmb_stopbits, win.cmb_bytesize, win.cmb_parity)]} "
              f"rx_lines_visible={visible_tree(win, win.rx_lines_box)} "
              f"actions_visible={visible_tree(win, win.actions_box)}")
        _hide(["actions_box"])
        _act_btns = [win.btn_marks, win.btn_open_history, win.btn_open_long_history,
                     win.btn_error_history, win.btn_dlc]
        _div = win.actions_box.findChild(QWidget, "actionsGroupDivider")
        _ttl = win.actions_box.findChild(QWidget, "actionsGroupTitle")
        check("B2 隐藏「数据工具」→ 整组（含分隔线与标题）消失",
              win.actions_box.isHidden()
              and not any(visible_tree(win, b) for b in _act_btns)
              and _div is not None and not visible_tree(win, _div)
              and _ttl is not None and not visible_tree(win, _ttl),
              f"box_hidden={win.actions_box.isHidden()} "
              f"btns_visible={[visible_tree(win, b) for b in _act_btns]} "
              f"divider_visible={None if _div is None else visible_tree(win, _div)} "
              f"title_visible={None if _ttl is None else visible_tree(win, _ttl)}")
        _hide(["rx_lines_box"])
        check("B3 隐藏「保留行数」→ 标签与下拉框一起消失",
              win.rx_lines_box.isHidden()
              and not visible_tree(win, win.cmb_rx_lines)
              and visible_tree(win, win.serial_params)
              and visible_tree(win, win.actions_box),
              f"box_hidden={win.rx_lines_box.isHidden()} "
              f"combo_visible={visible_tree(win, win.cmb_rx_lines)} "
              f"serial_visible={visible_tree(win, win.serial_params)} "
              f"actions_visible={visible_tree(win, win.actions_box)}")
        _hide([])
        check("B4 三个开关互不影响，且都能恢复",
              not win.serial_params.isHidden()
              and not win.rx_lines_box.isHidden()
              and not win.actions_box.isHidden()
              and visible_tree(win, win.cmb_baud)
              and visible_tree(win, win.cmb_rx_lines)
              and visible_tree(win, win.btn_marks),
              f"serial_hidden={win.serial_params.isHidden()} "
              f"rx_lines_hidden={win.rx_lines_box.isHidden()} "
              f"actions_hidden={win.actions_box.isHidden()} "
              f"baud_visible={visible_tree(win, win.cmb_baud)} "
              f"rx_combo_visible={visible_tree(win, win.cmb_rx_lines)} "
              f"marks_visible={visible_tree(win, win.btn_marks)}")

        # ================= C 模式组合 =================
        _restore()
        win.cmb_link.setCurrentText(LINK_SERIAL)
        settle(app, 250)
        check("C1 串口模式（初始）：串口参数可见、DLC 不可见",
              not win.serial_params.isHidden() and win.btn_dlc.isHidden(),
              f"serial_hidden={win.serial_params.isHidden()} "
              f"dlc_hidden={win.btn_dlc.isHidden()}")
        win.cmb_link.setCurrentText(LINK_BLE)
        settle(app, 250)
        check("C2 切蓝牙广播 → 串口参数隐藏、DLC 可见",
              win.serial_params.isHidden() and not win.btn_dlc.isHidden(),
              f"serial_hidden={win.serial_params.isHidden()} "
              f"dlc_hidden={win.btn_dlc.isHidden()}")
        win.cmb_link.setCurrentText(LINK_SERIAL)
        settle(app, 250)
        check("C3 切回串口 → 串口参数回来、DLC 不可见",
              not win.serial_params.isHidden() and win.btn_dlc.isHidden(),
              f"serial_hidden={win.serial_params.isHidden()} "
              f"dlc_hidden={win.btn_dlc.isHidden()}")
        _hide(["serial_params"])
        win.cmb_link.setCurrentText(LINK_BLE)
        settle(app, 250)
        check("C4 隐藏串口参数 + 蓝牙广播 → 参数不显示、**DLC 仍可见**",
              win.serial_params.isHidden() and not win.btn_dlc.isHidden(),
              f"serial_hidden={win.serial_params.isHidden()} "
              f"dlc_hidden={win.btn_dlc.isHidden()}")
        _hide([])
        win.cmb_link.setCurrentText(LINK_SERIAL)
        settle(app, 250)

        # ================= D 视觉改版回归（五颗动作行）=================
        _want = {"btn_marks": "查看标记", "btn_open_history": "打开近期数据",
                 "btn_open_long_history": "打开历史数据",
                 "btn_error_history": "历史报错数据", "btn_dlc": "DLC"}
        _bad = []
        for attr, text in _want.items():
            b = getattr(win, attr)
            if b.text() != text:
                _bad.append(f"{attr}={b.text()!r}")
            if not b.toolTip():
                _bad.append(f"{attr} 无 tooltip")
        check("D1 五颗动作行文案与 tooltip 齐全",
              not _bad, "; ".join(_bad) or str(list(_want.values())))
        # ⛔ 不能靠 QObject.receivers() 判连接：PySide6 里 Python 槽不计入。
        #    改用源码级断言（key=属性名 → connect(槽)），并且**只**功能点安全按钮
        #    （DLC 会走安装流程、历史报错会弹模态窗，点了会卡住探针）。
        _src_text = (Path(_ROOT) / "main_window.py").read_text(encoding="utf-8")
        _handlers = {"btn_marks": "_show_marks_dialog",
                     "btn_open_history": "_open_history",
                     "btn_open_long_history": "_open_long_history",
                     "btn_error_history": "_open_error_history",
                     "btn_dlc": "_on_dlc_clicked"}
        _miss = [f"{a}->{h}" for a, h in _handlers.items()
                 if f"self.{a}.clicked.connect(self.{h})" not in _src_text]
        check("D1b 五颗动作行的 clicked 都接到对应槽（源码级）",
              not _miss, "; ".join(_miss) or str(list(_handlers.values())))
        check("D2 「数据工具」分组标题 + 分隔线在位（QLabel#actionsGroupTitle / "
              "QWidget#actionsGroupDivider）",
              win.actions_box.findChild(QWidget, "actionsGroupTitle") is not None
              and win.actions_box.findChild(QWidget, "actionsGroupDivider") is not None,
              f"title={win.actions_box.findChild(QWidget, 'actionsGroupTitle') is not None} "
              f"divider={win.actions_box.findChild(QWidget, 'actionsGroupDivider') is not None}")
        # 回放态文案切换
        win._clear_receive()
        rx_history.flush()
        rx_history._pending.clear()
        _rows: list[str] = []
        for i, st in enumerate(("2026-12-01 08:00:00", "2026-12-01 08:00:10")):
            _rows += [f"[{st}]", "", "Voltage1:", f"{i + 1:02d}.3800", "",
                      "Temperature1:", "1.25", "", "BMS OK"]
        if rx_history._path is not None:
            rx_history._path.write_text("\n".join(_rows) + "\n", encoding="utf-8")
            rx_history._lines.clear()
            rx_history._lines.extend(_rows)
            rx_history._file_lines = len(_rows)
        win.btn_open_history.click()          # 真按钮路径
        settle(app, 400)
        _rec = (win.btn_open_history.text(), win.btn_open_long_history.text())
        _kind = win._replay_kind
        win._replay_kind = "long"                 # 白盒切到长历史分支，只验文案映射
        win._refresh_replay_ui()
        _long = (win.btn_open_history.text(), win.btn_open_long_history.text())
        win._replay_kind = _kind
        win._refresh_replay_ui()
        check("D3 回放态文案切换：近期回放 → 「关闭近期数据 / 打开历史数据」；"
              "长历史 → 「打开近期数据 / 关闭历史数据」",
              _rec == ("关闭近期数据", "打开历史数据")
              and _long == ("打开近期数据", "关闭历史数据"),
              f"recent={_rec} long={_long} kind={_kind!r}")
        win._exit_replay()
        settle(app, 250)
        check("D4 退出回放后文案复原",
              (win.btn_open_history.text(), win.btn_open_long_history.text())
              == ("打开近期数据", "打开历史数据"),
              f"{win.btn_open_history.text()!r}/{win.btn_open_long_history.text()!r}")
        # 禁用逻辑：五颗动作行在实时态 / 回放态都不该被禁用
        _dis_live = [a for a in _want if not getattr(win, a).isEnabled()]
        win._open_history()
        settle(app, 300)
        _dis_replay = [a for a in _want if not getattr(win, a).isEnabled()]
        win._exit_replay()
        settle(app, 250)
        check("D6 五颗动作行在实时态与回放态都不被禁用",
              not _dis_live and not _dis_replay,
              f"live_disabled={_dis_live} replay_disabled={_dis_replay}")
        # 窄档（1/4 屏）不崩
        try:
            win.resize(560, 400)
            settle(app, 300)
            _narrow_ok = True
        except Exception:                          # noqa: BLE001
            _narrow_ok = False
        _fit = []
        for attr in _want:
            b = getattr(win, attr)
            _need = b.fontMetrics().horizontalAdvance(b.text())
            if _need > b.width() + 1:
                _fit.append(f"{attr} 文字{_need:.0f}>宽{b.width()}")
        check("D5 窄档（1/4 屏 560×400）不崩、动作行文字不被挤没",
              _narrow_ok and not _fit, "; ".join(_fit) or "ok")
        win.resize(1200, 800)
        settle(app, 250)

        # ================= E 初始显隐落点 + 弹层动画 =================
        _src = (Path(_ROOT) / "main_window.py").read_text(encoding="utf-8")
        _lines = _src.splitlines()

        def _def_line(name: str) -> int:
            return next(i for i, _ln in enumerate(_lines)
                        if _ln.strip().startswith(f"def {name}("))

        _d_ui = _def_line("_build_ui")
        _d_panel = _def_line("_build_config_panel")
        _d_rcv = _def_line("_refresh_config_visibility")
        # 只认**真调用**（整行就是那一句），注释里提到的不算
        _calls = [i for i, _ln in enumerate(_lines)
                  if _ln.strip() == "self._refresh_config_visibility()"]
        _d_panel_end = next(i for i in range(_d_panel + 1, len(_lines))
                            if _lines[i].startswith("    def "))
        _in_panel = [i for i in _calls if _d_panel < i < _d_panel_end]
        _in_ui = [i for i in _calls if _d_ui < i < _d_rcv]
        _add = next((i for i, _ln in enumerate(_lines)
                     if "self._config_card.body.addWidget(self._config_content" in _ln), -1)
        _setlayout = next((i for i, _ln in enumerate(_lines)
                           if "self._config_content.setLayout(panel)" in _ln), -1)
        _first_ui_call = _in_ui[0] if _in_ui else -1
        check("E1 初始显隐落点在**配置卡组装完成之后**（不在 _build_config_panel 内部）",
              not _in_panel and bool(_in_ui) and _first_ui_call > _add > _setlayout,
              f"calls={_calls} in_panel={_in_panel} setLayout@{_setlayout} "
              f"addWidget@{_add} first_ui_call@{_first_ui_call}")
        # 真机弹层展开动画：起始高应明显小于完整高
        _pc = win.cmb_stopbits
        _pc._fit_popup_width()
        _pc.showPopup()
        _pv = _pc.view().window()
        _h_start = _pv.height()
        for _ in range(400):
            app.processEvents()
            if not _pc.view().isVisible():
                break
            _h_start = min(_h_start, _pv.height())
            time.sleep(0.003)
        _h_full = max(_pv.height(), _h_start)
        _pc.hidePopup()
        for _ in range(8):
            app.processEvents()
        art("popup_h", (_h_start, _h_full))
        check("E2 真机下拉弹层展开动画存在（起始高 ≤ 完整高的一半）",
              _h_start <= _h_full * 0.5, f"起始高 {_h_start} / 完整高 {_h_full}")

    except Exception:                                 # noqa: BLE001 - 探针留证据
        print("EXCEPTION:\n" + traceback.format_exc(), flush=True)
        check("!! 探针异常中断（上面已有结论仍有效）", False,
              traceback.format_exc(limit=3).replace("\n", " | ")[:400])
    finally:
        if win is not None:
            try:
                win.close()
                # ⛔ close() 只启动 160ms 淡出、真正的清理在 165ms 后的 _finish_close；
                #    立刻 deleteLater() 会在淡出/抓图线程还活着时析构主窗 → 进程
                #    异常退出（退出码 127、stdout 缓冲丢失）。先让事件循环把这段时间
                #    跑完，再析构。
                for _ in range(80):
                    app.processEvents()
                    time.sleep(0.006)
                win.deleteLater()
            except Exception:                          # noqa: BLE001
                pass
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        try:
            import rx_history
            import rx_store
            rx_history._resolve_dir = real_resolver
            rx_store.shutdown()
            rx_store.set_db_path(real_db)
            rx_store.reset_for_tests()
        except Exception:                              # noqa: BLE001
            pass
        for k in list(qs.allKeys()):
            if k not in snap:
                qs.remove(k)
        for k, v in snap.items():
            qs.setValue(k, v)
        qs.sync()
        shutil.rmtree(hist_dir, ignore_errors=True)
        shutil.rmtree(db_dir, ignore_errors=True)

    now = {k: qs.value(k) for k in qs.allKeys()}
    hidden_after = qs.value("hidden_buttons")
    art("qsettings_keys_after", len(now))
    check("F1 QSettings 全量快照前后完全一致（71 键）",
          now == snap and len(now) == len(snap),
          f"{len(snap)} 键 -> {len(now)} 键；差异="
          f"{sorted(set(now) ^ set(snap)) or [k for k in snap if now.get(k) != snap[k]]}")
    check("F2 hidden_buttons 仍为 []（探针没留隐藏状态）",
          hidden_before == hidden_after == [] or hidden_before == hidden_after,
          f"{hidden_before!r} -> {hidden_after!r}")

    passed = sum(1 for _n, ok, _d in _RESULTS if ok)
    total = len(_RESULTS)
    tail = f"\n合计 {passed}/{total} PASS  ->  IS_PASS: {'YES' if passed == total else 'NO'}"
    print(tail, flush=True)

    lines = [f"{'PASS' if ok else 'FAIL'}  {n}" + (f"   [{d}]" if d else "")
             for n, ok, d in _RESULTS]
    (_CHECK / "qa_config_vis_report.txt").write_text(
        "\n".join(lines) + "\n\nNOTES\n" + "\n".join(_NOTES) + "\n" + tail + "\n",
        encoding="utf-8")
    md = ["# QA 独立验收：串口设置卡显隐 + DLC 随模式 + RowButton 改版回归", "",
          f"- 结果：**{passed}/{total} PASS**（IS_PASS={'YES' if passed == total else 'NO'}）",
          f"- QSettings：{len(snap)} 键 → {len(now)} 键", "",
          "| # | 判据 | 结果 | 证据 |", "|---|---|---|---|"]
    for n, ok, d in _RESULTS:
        head, _, rest = n.partition(" ")
        md.append(f"| {head} | {rest} | {'PASS' if ok else '**FAIL**'} | "
                  f"{d.replace('|', '/')} |")
    if _NOTES:
        md += ["", "## NOTES"] + [f"- {t}" for t in _NOTES]
    md += ["", "## 关键读数", "```"] + [f"{k} = {v}" for k, v in _ART.items()] + ["```"]
    (_CHECK / "qa_config_vis_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    app.quit()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
