r"""离屏几何探针：「时钟显示年月日并入时间戳行」+「启动时最大化/保留任务栏 并排」。

    QT_QPA_PLATFORM=offscreen .venv\Scripts\python.exe devtools/_probe_merge_rows.py

做法：**同进程 A/B 对照** —— 先把 `SettingsDialog._MERGE_GROUPS` /
`_MERGE_PREFIXES` 临时换回改动前的版本建一个面板（A，基线），再换回当前版本
建一个面板（B），两张表单在**同一份运行状态**下对比：
    - 行数必须正好少 2（两条原来各占一行的开关并进别人的行）
    - 「时间戳显示」行的字段容器 width 增量 = 第 4 枚开关 + 小字 + 2 个 spacing
    - 「启动时最大化」行的 width 增量 = 第 2 枚开关 + 小字 + 2 个 spacing
这样绕开"绝对行数随 QSettings / 车辆档案漂移"的坑（别处 selftest 在并跑）。

只读产品代码：
- **只构造** SettingsDialog，不 show / 不 exec；
- 绝不调 accept() / save，不写任何 QSettings 键（构造期只读配置）；
- app.quit() 收尾，绝不 os._exit()。

⚠️ offscreen 平台的字体度量与真机不同：**宽度数字只作相对比较**，不能当验收
   绝对值（真机最小宽度由 ui_settings.py 末尾 setMinimumWidth 按实测算）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QFormLayout, QWidget,
)

from ui_controls import Switch  # noqa: E402
from ui_settings import SettingsDialog  # noqa: E402

# ---- 改动前的合并表（= 2026-11-27 三轮改动之前的 git HEAD 版本）----
# A 组（基线）用它们建面板，与当前版本做同状态对照。
OLD_MERGE_GROUPS = {
    "stamp_show_year": ("时间戳显示",
                        ("stamp_show_year", "stamp_show_month", "stamp_show_day")),
    "volt_limit_low": ("电压范围", ("volt_limit_low", "volt_limit_high")),
    "temp_limit_low": ("温度范围", ("temp_limit_low", "temp_limit_high")),
    "pause_snap_lines": ("暂停定位行数",
                         ("pause_snap_lines", "pause_snap_lines_max")),
    "soc_source": ("SOC 数据来源", ("soc_source", "volt_source")),
    "volt_rows": ("电压数据行数", ("volt_rows", "temp_rows")),
    "material": ("主题", ("material",)),
}
OLD_MERGE_PREFIXES = {
    "stamp_show_year": "年",
    "stamp_show_month": "月",
    "stamp_show_day": "日",
    "volt_limit_high": "~",
    "temp_limit_high": "~",
    "pause_snap_lines_max": "行数（最大化）",
    "volt_source": "总电压来源",
}


def row_widgets(form: QFormLayout) -> list[tuple[int, str, QWidget | None]]:
    """逐行取 (行号, 行标签文案, 字段控件)。跨列行标签为空串。"""
    out = []
    for row in range(form.rowCount()):
        lab = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        fld = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
        text = ""
        if lab is not None and lab.widget() is not None:
            text = getattr(lab.widget(), "text", lambda: "")()
        out.append((row, text, fld.widget() if fld else None))
    return out


def field_width(form: QFormLayout, label_text: str) -> int:
    """某行字段容器的 sizeHint 宽（行标签必须唯一）。"""
    hits = [w.sizeHint().width() for _r, t, w in row_widgets(form)
            if t == label_text and w is not None]
    assert len(hits) == 1, f"行标签「{label_text}」出现 {len(hits)} 次（应为 1）"
    return hits[0]


def build(dialog_cls: type) -> SettingsDialog:
    """按当前类级合并表建一个面板（只用默认参数，不 show）。"""
    return dialog_cls("dark", 0, [], None)


def measure(dialog: SettingsDialog) -> dict:
    form = dialog.form
    return {
        "rowcount": form.rowCount(),
        "min_w": dialog.minimumWidth(),
        "w_stamp": field_width(form, "时间戳显示"),
        "w_start": field_width(form, "启动时最大化"),
        "labels": [t for _r, t, _w in row_widgets(form)],
    }


def main() -> int:
    app = QApplication(sys.argv)
    from ui_theme import set_scheme, stylesheet as qss, ui_font

    app.setStyle("Fusion")
    app.setFont(ui_font(12.5))
    set_scheme("dark")
    app.setStyleSheet(qss())

    cls = SettingsDialog
    cur_groups = cls._MERGE_GROUPS
    cur_prefixes = cls._MERGE_PREFIXES

    # ---- A：改前基线 ----
    cls._MERGE_GROUPS = OLD_MERGE_GROUPS
    cls._MERGE_PREFIXES = OLD_MERGE_PREFIXES
    try:
        a = build(cls)
        try:
            base = measure(a)
        finally:
            a.close()
            a.deleteLater()
            app.processEvents()
    finally:
        cls._MERGE_GROUPS = cur_groups
        cls._MERGE_PREFIXES = cur_prefixes

    # ---- B：当前版本 ----
    b = build(cls)
    try:
        now = measure(b)
        form = b.form

        print(f"[probe] rowCount   A(改前)={base['rowcount']}  B(改后)={now['rowcount']}"
              f"  Δ={now['rowcount'] - base['rowcount']:+d}")
        print(f"[probe] minimumWidth A={base['min_w']}  B={now['min_w']}"
              f"  Δ={now['min_w'] - base['min_w']:+d}")
        print(f"[probe] 「时间戳显示」字段宽 A={base['w_stamp']}  B={now['w_stamp']}"
              f"  Δ={now['w_stamp'] - base['w_stamp']:+d}")
        print(f"[probe] 「启动时最大化」字段宽 A={base['w_start']}  B={now['w_start']}"
              f"  Δ={now['w_start'] - base['w_start']:+d}")

        # ---- 断言 1：行数正好少 2（两条并整行消失）----
        assert now["rowcount"] == base["rowcount"] - 2, (
            f"行数不是少 2：A={base['rowcount']} B={now['rowcount']}")
        print("[probe] ✔ rowCount 正好 -2")

        # ---- 断言 2/3：两个合并组的成员共用同一个父容器 ----
        for owner in ("stamp_show_year", "startup_maximized"):
            _text, members = b._MERGE_GROUPS[owner]
            holder = b._custom_widgets[members[0]].parentWidget()
            assert holder is not None, f"「{_text}」首成员没有容器"
            for key in members:
                assert b._custom_widgets[key].parentWidget() is holder, \
                    f"{key} 不在「{_text}」同一容器里"
            assert isinstance(b._custom_widgets[members[-1]], Switch), \
                f"「{_text}」末成员不是 Switch"
            print(f"[probe] ✔ 「{_text}」: {len(members)} 个成员同一容器，"
                  f"sizeHint={holder.sizeHint().width()}")

        # ---- 断言 4：两条原来的独立行标签不再作为行标签出现 ----
        for gone in ("时钟显示年月日", "最大化时保留任务栏"):
            assert gone not in now["labels"], f"「{gone}」还单独占着一行（A 里应有）"
        assert "时钟显示年月日" in base["labels"], "基线里「时钟显示年月日」应当独立成行"
        assert "最大化时保留任务栏" in base["labels"], "基线里它应当独立成行"
        print("[probe] ✔ 两条独立行标签都已并走（且基线里确实存在）")

        # ---- 断言 5：小字与登记表 ----
        assert b._MERGE_PREFIXES["clock_show_date"] == "时钟"
        assert b._custom_rows["clock_show_date"][0].text() == "时钟"
        assert b._custom_rows["maximize_keep_taskbar"][0].text() == \
            b._MERGE_PREFIXES["maximize_keep_taskbar"]
        for key in ("clock_show_date", "maximize_keep_taskbar"):
            assert b._custom_rows[key][1] is b._custom_widgets[key], \
                f"{key} 的 _custom_rows 字段不是真控件"
        print("[probe] ✔ 小字 / _custom_rows / _custom_widgets 登记一致")

        # ---- 小字措辞取舍：按实测宽度二选一 ----
        _sw = b._custom_widgets["startup_maximized"].sizeHint().width()
        _fm = QFontMetrics(ui_font(10.5))
        _cands = {}
        for cand in ("时保留任务栏", "保留任务栏"):
            lw = _fm.horizontalAdvance(cand) + 2        # +2 = _inline_caption 的余量
            _cands[cand] = _sw * 2 + 20 + lw            # 开关+10+小字+10+开关
            print(f"[probe] 预估合并行宽（小字「{cand}」）= {_cands[cand]}")
        assert now["w_start"] == _cands[b._MERGE_PREFIXES["maximize_keep_taskbar"]], (
            f"实际行宽 {now['w_start']} 与按小字算出的预估不一致：{_cands}")
        print(f"[probe] ✔ 实际行宽 {now['w_start']} 与公式吻合；"
              f"选中的小字「{b._MERGE_PREFIXES['maximize_keep_taskbar']}」"
              f"（另一候选 {_cands['保留任务栏']}）")

        # ---- 字段列宽的事前估计（只打印，真值以真机 selftest 为准）----
        _label_col = max((b._label(t).sizeHint().width()
                          for _r, t, _w in row_widgets(form) if t), default=0)
        _col = now["min_w"] - 58 - _label_col - 18      # 58 = 外层边距+滚动条
        print(f"[probe] 最坏情况字段列宽 ≈ {_col}（label 列 {_label_col}）")
        for name, w in (("时间戳显示", now["w_stamp"]),
                        ("启动时最大化", now["w_start"])):
            print(f"[probe] 「{name}」估计 slack = {_col - w}"
                  f"（selftest 阈值 {_col // 5}；自填满={w >= _col - 40}）")

        print("[probe] PASS")
        return 0
    finally:
        b.close()
        b.deleteLater()
        app.processEvents()
        app.quit()


if __name__ == "__main__":
    raise SystemExit(main())