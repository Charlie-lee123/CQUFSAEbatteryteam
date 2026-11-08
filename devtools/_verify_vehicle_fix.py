# -*- coding: utf-8 -*-
r"""车号修复独立验收判据（evidence-recorder，只读取证 / 纯离屏）。

用途
====
对「未选择 → 选中车号」动画的修复做**客观、可重复**的 PASS/FAIL 判定：
输入「修复前基线」与「待验收目录」，输出 6 条判据的数值对比与结论。

⛔ 重要约束（第一段）
--------------------
- **纯离屏**：只 import numpy / PIL / cv2 / 标准库；**不 import PySide6、
  不构造 QApplication、不打开任何主窗口、不 monkey-patch 产品代码**。
- **不抢 `_check/.app_run.lock`**：本脚本完全不碰该锁（vehicle-fix 正在跑真机验证）。
- 不改产品源码、不 git、不打包、不动 发布 与 dist*、不跑 selftest。
- 不读改后的 `ui_settings.py` 来「预期结果」；所有阈值来自**修复前基线实测**。
- **基线完整性门禁**（team-lead 新增）：accept 模式出结论前强制校验修复前
  基线指纹（67 帧 / 20.31% / swap_idx=57 / t054 alpha 分布 / 相交像素≈43318）。
  vehicle-fix 曾把修复后数据覆写进默认基线目录；一旦指纹不匹配即 BLOCKED，
  ⛔ 不出任何 PASS/FAIL、不覆盖既有报告，只产出独立门禁诊断文件。

输入契约（一个验收目录 = 以下产物的并集；可放在一个目录，也可拆成
`--dir` + `--dir-geom` 两个目录，脚本会合并查找）
------------------------------------------------------------------
来自 `_evidence_vehicle.py` 风格的逐拍探针：
  - `frames.json`  : {"frames":[{"seq","w","h","meta":{ms,tag,swap_frame,
                     ov_vis,ov_items,ov_alpha,...}}], "swap_idx": int}
  - `vNNN_*.png`   : 合成帧（body 可视区 grab，含覆盖层与真控件）；
                     NNN = 0 基帧序号，可与 frames.json 的 index 对齐。抽稀即可。
  - `report.md`    : 逐拍表（含「对上帧diff% / 对落定帧diff%」两列）；
                     **稠密 67 帧的 diff 只存在于该表**，缺失时脚本退化为用
                     现存 PNG 重算并标注「精度降级」。
来自 `_evidence_vehicle_geom.py` 风格的几何探针：
  - `tNNN_composite.png`    : t∈{000,054,084,106,432} 的 body 合成帧
  - `tNNN_overlay_own.png`  : 同拍覆盖层**自身渲染**（透明底，带 alpha 通道）
  - `geom.json`             : {"geom":[{target_ms,overlay,rect_new,holder,
                     form_rows/legacy,ov,...}], ...}
可选（判据 5b；team-lead 平铺契约命名为 `haveold/`，脚本同时兼容历史
`old_block/` 命名，自动探测并在报告写明实际采用路径，⛔ 不静默跳过）：
  - `haveold/frames.json` + `haveold/*.png`（或 `old_block/...`）：
    「换车号 / 有旧块(has_old=True)」方向的逐拍录屏。

用法
====
  # 第一段自检：target 省略 → 用基线自身当目标，验证脚本可用 / 阈值合理
  .venv/Scripts/python.exe devtools/_verify_vehicle_fix.py

  # 第二段验收（等 team-lead 通知后再跑；基线完整性门禁自动先行校验）：
  .venv/Scripts/python.exe devtools/_verify_vehicle_fix.py \
      --target _check/vehicle_fix/after \
      --target-geom _check/vehicle_fix/after_geom
  #   haveold/frames.json 由脚本自动探测（--target 的兄弟目录），
  #   也可显式 --haveold _check/vehicle_fix/haveold

  # 纯离屏自检基线是否恢复干净（不产出验收结论）：
  ... --baseline _check/vehicle_fix/before/evidence_vehicle \
      --baseline-geom _check/vehicle_fix/before/evidence_vehicle_geom

  # 自定义输出
  ... --out _check/verify_vehicle_fix/report.md \
      --json _check/verify_vehicle_fix/result.json

判据总览（阈值依据见文末 THRESHOLDS 与各 check_* 的 docstring）
============================================================
 1 早期双份文字消失   : 三子项（覆盖层实际绘制高度 / 合成帧新增内容量 /
                       覆盖层 painted 区与旧项真控件的相交像素数）
 2 早期突变收敛       : ms 0~120 最大「对上帧diff%」，基线 20.31%
 3 ms≈432 落定态      : 该帧与 SETTLED 帧 diff，基线 0.17%
 4 swap 当拍           : swap±3 帧逐像素差，基线 0.00
 5 有旧块场景不回归   : 5a 不发灰（不透明占比） + 5b P1/P2/P3 三段结构
 6 覆盖层不透明背板回归: overlay_own 不透明像素占比，基线 0.00%
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from typing import Any

import cv2
import numpy as np
from PIL import Image

# ------------------------------------------------------------------ 常量
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_BASE = os.path.join(_ROOT, "_check", "evidence_vehicle")
_DEFAULT_BASE_GEOM = os.path.join(_ROOT, "_check", "evidence_vehicle_geom")
_DEFAULT_OUT = os.path.join(_ROOT, "_check", "verify_vehicle_fix", "report.md")
_DEFAULT_JSON = os.path.join(_ROOT, "_check", "verify_vehicle_fix", "result.json")

_EARLY_T = (54, 84, 106)          # 判据 1 的三拍目标 ms
_ALL_T = (0, 54, 84, 106, 432)
_PHASE = {"p1_end": 200.0, "p2_end": 320.0, "p3_end": 680.0}
# ↑ 取自定义前「修复前基线代码」注释中的 P1/P2/P3/P4 分段（#141 报告已记录）；
#   不读改后的源码。如修复调整了时间轴，用 --p1/--p2/--p3 覆盖。

_OLD_ITEMS = {
    "主题", "材质强度", "界面材质", "开屏动画", "动画文件",
    "启动时最大化", "默认折叠发送区", "暂停定位行数", "行数（最大化）",
    "时间戳显示年份",
}

# ---- 阈值（全部由修复前基线实测标定，见 _check/verify_vehicle_fix/report.md）----
THRESHOLDS = {
    # 判据 1A：实际绘制高度 / H_full（H_full = 修复前完整 pix_new 逻辑高 218px）
    "c1_vis_early_max": 0.50,     # 基线=1.00（整块全画）；修复后 ms54 应≈holder/218≈0.09
    "c1_vis_late_min": 0.70,      # 基线=1.00；ms432 holder≈196/218≈0.90，仍应接近满高
    "c1_vis_mono_tol": 0.05,     # 54→84→106→432 单调不减的容差（×H_full）
    "c1_add_ratio": 0.60,        # D_add(修复后) ≤ 0.6 × D_add(基线)；留给 holder 位移的不可约分量
    "c1_overlap_ratio": 0.10,    # 相交像素 ≤ 0.1 × 基线（基线 4.3万 / 3.9万 / 3.6万 px²）
    # 判据 2
    "c2_improve_abs": 10.0,      # 验收模式：早期最大突变 ≤ 10.0%（= 0.5 × 20.31%）
    "c2_guard_ratio": 1.20,      # 自检/不回归：≤ 1.2 × 基线 = 24.4%
    "c2_jump_cap": 12.0,         # 验收模式：ms0~120 内不得再有 >12% 的单帧突变
    # 判据 3
    "c3_settle_area": 1.00,      # ms432 与落定帧 area%(|diff|>25) ≤ 1.00%（基线 0.17/0.13%）
    "c3_settle_mean": 1.50,      # 平均绝对灰度差 ≤ 1.5（基线 0.33）
    # 判据 4
    "c4_swap_area": 0.10,        # swap±3 相邻帧 area% ≤ 0.10%（基线 0.00）
    "c4_swap_mean": 0.50,        # 逐像素平均绝对差 ≤ 0.50（基线 0.00）
    # 判据 5 / 6
    "c5_opaque_max": 2.00,       # 任一 overlay_own 不透明占比 ≤ 2.00%（基线 0.00%）
    "c6_opaque_max": 2.00,       # 同 c5，专用于「不透明背板」回归
    "c5_alpha_cap": 1.00,        # ov_alpha 不得超过 1.0
}

# ---- 修复前基线「真值指纹」（team-lead 新增门禁，检测基线被污染）----
# 全部取自第一段自检报告 _check/verify_vehicle_fix/report.md 与几何补证实测；
# ⚠️ 不读改后源码、不读修复后产物。容差吸收 PNG 重编码 / 浮点微差。
# 污染版特征（22:15 覆写）：63 帧 / swap53 / 峰值 9.58% / t054 全透明 94.89%
#                          / 半透明 4.61% / ov.n_items=2。
BASELINE_FINGERPRINT = {
    "report_rows": 67,             # report.md 稠密表行数（污染版 63 / 解析后 62）
    "report_rows_tol": 2,          # parse_report_diffs 会跳过表头类非数字行，干净版解析后 66
    "report_max_prev": 20.31,      # ms0~120「对上帧diff%」峰值（污染版 9.58）
    "report_max_prev_tol": 0.50,
    "frames_len": 67,              # frames.json 帧数（污染版 63）
    "swap_idx": 57,                # 污染版 53
    "t054_transparent": 73.9097,   # t054_overlay_own alpha 分布（污染版 94.89）
    "t054_partial": 26.0903,       # 污染版 4.61
    "alpha_dist_tol": 3.0,         # 透明/半透明占比容差（百分点）
    "opaque_max": 2.0,             # 不透明占比上限（基线 0.00%）
    "t054_overlap": 43318.2,       # 判据 1C 相交像素（污染版明显更小）
    "overlap_tol_ratio": 0.15,     # ±15%
    "t054_ov_n_items": 1,          # geom.json t054 的 ov.n_items（污染版 2）
}

# ---- 附录 C：t432 合成帧的目视画质描述（team-lead 追加项）----
# ⚠️ 这是 evidence-recorder 目视 `_check/vehicle_fix/after_geom/t432_composite.png`
#    后记录的**静态文字**，非自动量测；只做描述，⛔ 不下 PASS/FAIL、不参与 6 条判据。
_APPENDIX_VISUAL_NOTE = (
    "目视 `_check/vehicle_fix/after_geom/t432_composite.png`（ms≈432 合成帧）："
    "① 双份文字：未见两套重叠的设置项文字；"
    "② 穿插/叠压：Module 1~4 与旧设置项（主题等）未互相穿插、叠压或残影，"
    "旧项已被 holder 让位推到画面下沿之外（底部仅见「主题」字样刚露头）；"
    "③ Module 行完整性：Module 1~4 完整显示，Module 5 行仅露出上半、下沿被裁切，"
    "呈卷帘过渡态（与 P0 让位 480ms 未结束一致）；"
    "④ 实底发灰：白色背景，未见不透明实底或整体发灰"
    "（客观佐证：t432_overlay_own.png 不透明占比 0.0%）；"
    "⑤ 整体：文字清晰、排列整齐，无残影 / 双份 / 穿插；仅存在上述过渡态裁切。"
)


# ------------------------------------------------------------------ 图像工具
def _load_rgba(path: str) -> np.ndarray:
    """读 PNG 为 RGBA uint8（纯 PIL，不需要 QApplication）。"""
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8)


def _gray400(path: str) -> np.ndarray:
    """归一化 400x250 灰度 —— 与基线探针 _evidence_vehicle.py:342-347 完全一致。"""
    a = _load_rgba(path)[:, :, :3]
    g = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)
    return cv2.resize(g, (400, 250), interpolation=cv2.INTER_AREA)


def diff_stats(path_a: str, path_b: str) -> dict[str, float]:
    """两帧差分：area%(|diff|>25) 与 平均绝对灰度差、最大差。与基线口径一致。"""
    d = cv2.absdiff(_gray400(path_a), _gray400(path_b))
    return {
        "area_pct": round(float((d > 25).mean()) * 100.0, 4),
        "mean_abs": round(float(d.mean()), 4),
        "max_abs": int(d.max()),
    }


def alpha_stats(path: str) -> dict[str, Any]:
    """PNG alpha 分布（全透明 <8 / 半透明 / 不透明 ≥250）+ 非透明外接框。

    ⚠️ `vis_h_ratio` 是「相对本图高度」：若修复让覆盖层控件**本身变矮**，该比值
    会恒为 1.0（假阴性）。判据 1A 因此改用 `bbox_h`（设备像素绝对高度）除以 dpr
    得到逻辑高度，再与固定参考 `H_full`（修复前完整 pix_new 高度）比较，
    对「裁剪」与「改变控件几何」两种实现都成立。
    """
    al = _load_rgba(path)[:, :, 3]
    h, w = al.shape
    nz = np.where(al >= 8)
    if len(nz[0]) == 0:
        return {"w": w, "h": h, "pct_transparent": 100.0,
                "pct_partial": 0.0, "pct_opaque": 0.0,
                "mean_alpha": 0.0, "bbox": None, "bbox_h": 0,
                "vis_h_ratio": 0.0}
    bh = int(nz[0].max()) - int(nz[0].min()) + 1
    return {
        "w": w, "h": h,
        "pct_transparent": round(float((al < 8).mean()) * 100, 4),
        "pct_partial": round(float(((al >= 8) & (al < 250)).mean()) * 100, 4),
        "pct_opaque": round(float((al >= 250).mean()) * 100, 4),
        "mean_alpha": round(float(al.mean()), 4),
        "bbox": (int(nz[1].min()), int(nz[0].min()),
                 int(nz[1].max()), int(nz[0].max())),
        "bbox_h": bh,
        "vis_h_ratio": round(bh / h, 4),
    }


# ------------------------------------------------------------------ 产物装载
class Artifacts:
    """合并 1~2 个目录的产物索引；缺失项保持 None，判据会报 SKIP 而非静默 PASS。"""

    V_RE = re.compile(r"^v(\d{3})_.*\.png$")
    T_COMP_RE = re.compile(r"^t(\d{3})_composite\.png$")
    T_OWN_RE = re.compile(r"^t(\d{3})_overlay_own\.png$")

    def __init__(self, dirs: list[str], label: str) -> None:
        self.dirs = [d for d in dirs if d and os.path.isdir(d)]
        self.label = label
        self.warnings: list[str] = []
        self.frames_json: dict | None = None
        self.report_md: str | None = None
        self.geom: dict | None = None
        self.v: dict[int, str] = {}
        self.t_comp: dict[int, str] = {}
        self.t_own: dict[int, str] = {}
        self.old_block_dir: str | None = None
        self._scan()

    def _find(self, name: str) -> str | None:
        for d in self.dirs:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
        return None

    def _scan(self) -> None:
        p = self._find("frames.json")
        if p:
            try:
                self.frames_json = json.load(open(p, encoding="utf-8"))
            except Exception as exc:                        # noqa: BLE001
                self.warnings.append(f"frames.json 解析失败: {exc!r}")
        p = self._find("report.md")
        if p:
            self.report_md = open(p, encoding="utf-8").read()
        p = self._find("geom.json")
        if p:
            try:
                self.geom = json.load(open(p, encoding="utf-8"))
            except Exception as exc:                        # noqa: BLE001
                self.warnings.append(f"geom.json 解析失败: {exc!r}")
        for d in self.dirs:
            for f in sorted(os.listdir(d)):
                full = os.path.join(d, f)
                if not os.path.isfile(full):
                    continue
                m = self.V_RE.match(f)
                if m:
                    self.v.setdefault(int(m.group(1)), full)
                    continue
                m = self.T_COMP_RE.match(f)
                if m:
                    self.t_comp.setdefault(int(m.group(1)), full)
                    continue
                m = self.T_OWN_RE.match(f)
                if m:
                    self.t_own.setdefault(int(m.group(1)), full)
            for _name in ("haveold", "old_block"):
                ob = os.path.join(d, _name)
                if os.path.isdir(ob) and os.path.isfile(
                        os.path.join(ob, "frames.json")):
                    self.old_block_dir = ob
                    break
        if not self.v:
            self.warnings.append("未找到 vNNN_*.png 合成帧")
        if not self.t_own:
            self.warnings.append("未找到 tNNN_overlay_own.png（判据 1/5/6 会降级）")

    # ---- 便捷查询 ----
    def meta(self, i: int) -> dict:
        f = (self.frames_json or {}).get("frames") or []
        return f[i].get("meta", {}) if 0 <= i < len(f) else {}

    def swap_idx(self) -> int | None:
        if self.frames_json and "swap_idx" in self.frames_json:
            return self.frames_json["swap_idx"]
        f = (self.frames_json or {}).get("frames") or []
        for i, fr in enumerate(f):
            if fr.get("meta", {}).get("swap_frame"):
                return i
        return None

    def ms(self, i: int) -> float | None:
        m = self.meta(i)
        return float(m["ms"]) if m.get("ms") is not None else None

    def nearest_v(self, ms: float, lo: float = 0.0,
                  hi: float = 1e9) -> int | None:
        cands = [(abs((self.ms(i) or -1e9) - ms), i)
                 for i in self.v if lo <= (self.ms(i) or -1e9) <= hi]
        return min(cands)[1] if cands else None

    def settled_v(self) -> int | None:
        for i in sorted(self.v, reverse=True):
            if (self.meta(i).get("tag") == "SETTLED"
                    or (self.ms(i) or 0) >= 1000):
                return i
        return max(self.v) if self.v else None

    def geom_snap(self, t: int) -> dict | None:
        for g in (self.geom or {}).get("geom", []):
            if abs(float(g.get("target_ms", -1)) - t) < 1:
                return g
        return None

    def dpr(self) -> float:
        """从 frames.json 取 dpr，缺省 2.0（基线实测 dpr=2.0）。"""
        f = (self.frames_json or {}).get("frames") or []
        if f:
            v = f[0].get("meta", {}).get("dpr")
            if v:
                return float(v)
        return 2.0

    def full_pix_height(self) -> float | None:
        """完整 pix_new 的逻辑高度（判据 1A 的固定参考 H_full）。

        优先取 geom.json 的 rect_new 高；退回 grabnew_pix_new.png 高/dpr；
        再退回基线 t054_overlay_own.png 高/dpr。
        """
        for g in (self.geom or {}).get("geom", []):
            rn = g.get("rect_new")
            if rn and len(rn) >= 4 and rn[3] > 0:
                return float(rn[3])
        p = self._find("grabnew_pix_new.png")
        if p:
            return float(_load_rgba(p).shape[0]) / self.dpr()
        p = self.t_own.get(54)
        if p:
            return float(_load_rgba(p).shape[0]) / self.dpr()
        return None


# ------------------------------------------------------------------ report.md 稠密 diff
def parse_report_diffs(text: str | None) -> list[dict]:
    """解析基线逐拍表 → [{i, ms, area_prev, area_final}]（67 帧稠密）。"""
    out: list[dict] = []
    if not text:
        return out
    for line in text.splitlines():
        if not re.match(r"^\|\s*\d+\s*\|", line):
            continue
        c = [x.strip() for x in line.split("|")]
        try:
            out.append({"i": int(c[1]), "ms": float(c[3]),
                        "area_prev": float(c[10]), "area_final": float(c[11])})
        except (ValueError, IndexError):
            continue
    return out


def sparse_diffs(a: Artifacts) -> list[dict]:
    """无 report.md 时的降级路径：用现存 vNNN PNG 重算相邻帧 diff。"""
    idx = sorted(a.v)
    out = []
    for j, i in enumerate(idx[1:], start=1):
        d = diff_stats(a.v[i], a.v[idx[j - 1]])
        out.append({"i": i, "ms": a.ms(i) or -1.0,
                    "area_prev": d["area_pct"], "area_final": None})
    return out


def early_max(diffs: list[dict], hi: float = 120.0) -> dict:
    early = [d for d in diffs if d.get("area_prev") is not None
             and 0 <= d["ms"] <= hi]
    if not early:
        return {"value": None, "at": None, "n": 0, "gt12": []}
    top = max(early, key=lambda x: x["area_prev"])
    return {"value": round(top["area_prev"], 4),
            "at": (round(top["ms"], 1), top["i"]),
            "n": len(early),
            "gt12": [(round(d["ms"], 1), round(d["area_prev"], 2))
                     for d in early if d["area_prev"] > THRESHOLDS["c2_jump_cap"]]}


# ------------------------------------------------------------------ 判据 1 的相交像素
def painted_rect_in_body(snap: dict, own_path: str,
                         dpr: float = 2.0) -> tuple | None:
    """把 overlay_own 的非透明外接框从「覆盖层局部设备像素」映射到 body 逻辑坐标。

    返回 (x0,y0,x1,y1)；覆盖层全透明时返回 None。
    `geom.json` 的 overlay 是 (x,y,w,h,vis,hid)，相对 body 的逻辑坐标。
    """
    a = alpha_stats(own_path)
    if a["bbox"] is None or snap is None:
        return None
    ov = snap.get("overlay")
    if not ov:
        return None
    x0, y0, x1, y1 = a["bbox"]
    return (ov[0] + x0 / dpr, ov[1] + y0 / dpr,
            ov[0] + x1 / dpr, ov[1] + y1 / dpr)


def overlap_pixels(snap: dict, pr: tuple | None) -> dict:
    """覆盖层 painted 矩形与「仍可见的旧设置项真控件」的相交像素数。"""
    if pr is None or snap is None:
        return {"total": 0.0, "hits": [], "coverage": "no-painted-or-no-geom"}
    rows = snap.get("form_rows") or []
    src = "form_rows"
    if not rows:
        # 兼容旧 schema：legacy 只有 _custom_rows 的部分项，覆盖不全，如实标注
        rows = [{"text": lg.get("label_text", ""),
                 "label": lg.get("label"), "field": lg.get("field")}
                for lg in (snap.get("legacy") or [])]
        src = "legacy(覆盖不全)"
    total, hits = 0.0, []
    for fr in rows:
        if fr.get("text") not in _OLD_ITEMS:
            continue
        for key in ("label", "field"):
            r = fr.get(key)
            if not r or len(r) < 5 or not r[4]:      # r[4]=isVisible
                continue
            ix = max(0.0, min(pr[2], r[0] + r[2]) - max(pr[0], r[0]))
            iy = max(0.0, min(pr[3], r[1] + r[3]) - max(pr[1], r[1]))
            if ix * iy > 0:
                total += ix * iy
                hits.append((fr["text"], key, round(ix * iy, 1)))
    return {"total": round(total, 1), "hits": hits, "coverage": src}


# ------------------------------------------------------------------ 结果容器
def result(name: str, status: str, baseline: Any, target: Any,
           threshold: str, detail: str, evidence: list[str] | None = None) -> dict:
    return {"name": name, "status": status, "baseline": baseline,
            "target": target, "threshold": threshold, "detail": detail,
            "evidence": evidence or []}


def combine(statuses: list[str]) -> str:
    """混合 PASS+SKIP 时报 `PASS*`（部分子项无数据），不伪装成全 PASS。"""
    if "ERROR" in statuses:
        return "ERROR"
    if "FAIL" in statuses:
        return "FAIL"
    if all(s == "SKIP" for s in statuses):
        return "SKIP"
    if any(s == "SKIP" for s in statuses):
        return "PASS*"
    return "PASS"


# ------------------------------------------------------------------ 5b 数据目录探测
def discover_haveold(tdirs: list[str], explicit: str | None = None) -> str | None:
    """定位判据 5b「换车号 / has_old=True」方向的 frames.json 所在目录。

    team-lead 平铺契约为 `_check/vehicle_fix/haveold/frames.json`，而脚本历史
    约定是 `old_block/frames.json`。两者命名不一致，这里统一探测：
      1) 显式 --haveold（最高优先）；
      2) --target / --target-geom 各目录下的 haveold/、old_block/ 子目录；
      3) 上述目录的**兄弟目录**（即 _check/vehicle_fix/haveold/）；
      4) 目录自身若名为 haveold/old_block，也直接采纳。
    返回第一个「是目录且含 frames.json」的绝对路径；找不到返回 None。
    实际采用路径会写进报告，⛔ 绝不静默跳过，也不需要软链 / 拷贝 / 改目录名。
    """
    cands: list[str] = []
    if explicit:
        cands.append(explicit)
    for d in tdirs:
        if not d:
            continue
        d = os.path.abspath(d)
        parent = os.path.dirname(d)
        if os.path.basename(d) in ("haveold", "old_block"):
            cands.append(d)
        for name in ("haveold", "old_block"):
            cands.append(os.path.join(d, name))
            cands.append(os.path.join(parent, name))
    seen: set[str] = set()
    for c in cands:
        c = os.path.abspath(c)
        if c in seen:
            continue
        seen.add(c)
        if os.path.isdir(c) and os.path.isfile(os.path.join(c, "frames.json")):
            return c
    return None


# ------------------------------------------------------------------ 基线完整性门禁
def baseline_integrity(base: Artifacts) -> dict:
    """校验修复前基线是否仍是「干净基线」，未被修复后数据覆写污染。

    team-lead 新增门禁：vehicle-fix 曾把修复后数据写进默认基线目录
    （63 帧 / swap53 / 峰值 9.58% / t054 全透明 94.89% / 半透明 4.61% /
     ov.n_items=2），据此出的 PASS/FAIL 全部作废。
    校验项全部复用脚本已有的解析 / 量测函数，指纹来自第一段自检实测；
    不读改后源码、不读修复后产物、不修改任何输入文件。
    """
    items: list[dict] = []

    def add(name: str, ok: bool, expected: Any, actual: Any) -> None:
        items.append({"name": name, "ok": bool(ok),
                      "expected": expected, "actual": actual})

    fp = BASELINE_FINGERPRINT

    # 1) report.md 稠密逐拍表行数（±2 容差：parse_report_diffs 会跳过表头类
    #    非数字行，干净基线解析后为 66 而非 67；污染版仅 62，差 5 仍可排除）
    diffs = parse_report_diffs(base.report_md)
    add("report.md 稠密表行数 ≈ 67",
        abs(len(diffs) - fp["report_rows"]) <= fp["report_rows_tol"],
        f"{fp['report_rows']} ±{fp['report_rows_tol']}", len(diffs))

    # 2) ms0~120「对上帧diff%」峰值 ≈ 20.31（与判据 2 同一口径）
    mx = early_max(diffs)["value"]
    add("峰值「对上帧diff%」≈ 20.31", (mx is not None and
        abs(mx - fp["report_max_prev"]) <= fp["report_max_prev_tol"]),
        f"{fp['report_max_prev']} ±{fp['report_max_prev_tol']}", mx)

    # 3) frames.json 帧数 / swap_idx
    nf = len((base.frames_json or {}).get("frames") or [])
    add("frames.json 帧数 = 67", nf == fp["frames_len"], fp["frames_len"], nf)
    si = base.swap_idx()
    add("frames.json swap_idx = 57", si == fp["swap_idx"], fp["swap_idx"], si)

    # 4) t054_overlay_own.png 的 alpha 分布（污染区分度最高）
    p54 = base.t_own.get(54)
    if p54:
        a = alpha_stats(p54)
        tol = fp["alpha_dist_tol"]
        add("t054 不透明占比 ≤ 2%", a["pct_opaque"] <= fp["opaque_max"],
            f"≤{fp['opaque_max']}%", a["pct_opaque"])
        add("t054 全透明占比 ≈ 73.91%",
            abs(a["pct_transparent"] - fp["t054_transparent"]) <= tol,
            f"{fp['t054_transparent']} ±{tol}", a["pct_transparent"])
        add("t054 半透明占比 ≈ 26.09%",
            abs(a["pct_partial"] - fp["t054_partial"]) <= tol,
            f"{fp['t054_partial']} ±{tol}", a["pct_partial"])
    else:
        for nm in ("t054 不透明占比 ≤ 2%", "t054 全透明占比 ≈ 73.91%",
                   "t054 半透明占比 ≈ 26.09%"):
            add(nm, False, "存在 t054_overlay_own.png", "MISSING")

    # 5) 判据 1C 相交像素 ≈ 43318（复用几何量测，team-lead 点名项）
    snap54 = base.geom_snap(54)
    ov54: float | None = None
    if snap54 and p54:
        ov54 = overlap_pixels(
            snap54, painted_rect_in_body(snap54, p54, base.dpr()))["total"]
    lo = fp["t054_overlap"] * (1 - fp["overlap_tol_ratio"])
    hi = fp["t054_overlap"] * (1 + fp["overlap_tol_ratio"])
    add("t054 相交像素 ≈ 43318", (ov54 is not None and lo <= ov54 <= hi),
        f"[{lo:.0f}, {hi:.0f}]", ov54)

    # 6) geom.json t054 的 ov（污染版 n_items=2）
    n_items = bg = None
    if snap54:
        ov = snap54.get("ov") or {}
        n_items, bg = ov.get("n_items"), ov.get("bg_none")
    add("geom.json t054 ov.n_items = 1", n_items == fp["t054_ov_n_items"],
        fp["t054_ov_n_items"], n_items)
    add("geom.json t054 ov.bg_none = True", bg is True, True, bg)

    ok = all(i["ok"] for i in items)
    bad = [i["name"] for i in items if not i["ok"]]
    return {
        "ok": ok,
        "verdict": "PASS" if ok else "FAIL",
        "items": items,
        "reason": ("基线指纹全部匹配，基线完整。"
                   if ok else f"基线指纹不匹配（{len(bad)} 项异常）：{bad}；"
                              "疑似被修复后数据覆写污染。"),
    }


# ================================================================== 六条判据
def check_c1(base: Artifacts, tgt: Artifacts, mode: str) -> dict:
    """判据 1：早期双份文字是否消失（三子项，全过才 PASS）。

    1A 覆盖层实际绘制高度（逻辑 px）= overlay_own 非透明外接框高 / dpr ——
       修复前 5 拍恒 = H_full(218px)（整块位图全画）；修复后应随 holder 让位
       高度逐帧增长：ms54 很小、ms432 接近满高。
       ⚠️ 用绝对逻辑高度而非「相对本图高度」，兼容「clip 裁剪」与「控件变矮」
          两种实现；这是「裁剪生效」的直接证据，也是双份文字消失的必要条件。
    1B 合成帧新增内容量 D_add(t) = area%(|composite(t)-composite(0)|>25)
       —— 同一 run 内 t000 是「无覆盖层」基准，diff 即该拍覆盖层新增的内容。
       修复前 14.82/13.76/21.13%；修复后只画细条 → 应 ≤0.6× 基线。
    1C 相交像素数 —— 覆盖层 painted 矩形（由 1A 的 alpha 外接框映射到 body 坐标）
       与仍可见旧项 label/field 矩形的相交面积 px²。
       修复前 43318/38913/36481（54/84/106）；修复后细条在旧项上方 → 应 ≤0.1× 基线。
    """
    subs: list[dict] = []
    ev: list[str] = []

    # ---- 1A 覆盖层实际绘制高度（逻辑 px，绝对量）----
    # 用 bbox_h/dpr 而非「相对本图高度」，兼容两种修复实现：
    #   (a) 控件几何不变、paintEvent 用 sourceRect 裁剪 → 图仍是 946x436，bbox 变矮
    #   (b) 控件几何本身随 holder 变矮     → 图也变小，bbox 仍等于图高
    # 两种情况下 bbox_h/dpr 都等于「实际可见的逻辑高度」。
    dpr = tgt.dpr()
    h_full = base.full_pix_height() or 218.0
    b_vis, t_vis = {}, {}
    for t in _ALL_T:
        pb, pt = base.t_own.get(t), tgt.t_own.get(t)
        if pb:
            b_vis[t] = round(alpha_stats(pb)["bbox_h"] / dpr, 2)
        if pt:
            t_vis[t] = round(alpha_stats(pt)["bbox_h"] / dpr, 2)
    if not t_vis:
        subs.append(result("1A 覆盖层可见高度", "SKIP", b_vis, None,
                           f"ms54≤{THRESHOLDS['c1_vis_early_max']}×H_full / "
                           f"ms432≥{THRESHOLDS['c1_vis_late_min']}×H_full",
                           "目标目录缺 tNNN_overlay_own.png"))
        s1a = "SKIP"
    else:
        early, late = t_vis.get(54), t_vis.get(432)
        mono = all(t_vis.get(b, 0) >= t_vis.get(a, 0) - THRESHOLDS["c1_vis_mono_tol"] * h_full
                   for a, b in zip((54, 84, 106), (84, 106, 432))
                   if a in t_vis and b in t_vis)
        ok = (early is not None
              and early <= THRESHOLDS["c1_vis_early_max"] * h_full
              and late is not None
              and late >= THRESHOLDS["c1_vis_late_min"] * h_full
              and mono)
        s1a = "PASS" if ok else "FAIL"
        subs.append(result("1A 覆盖层可见高度(逻辑px)", s1a, b_vis, t_vis,
                           f"ms54≤{THRESHOLDS['c1_vis_early_max']*h_full:.1f}px、"
                           f"ms432≥{THRESHOLDS['c1_vis_late_min']*h_full:.1f}px、"
                           f"单调不减(±{THRESHOLDS['c1_vis_mono_tol']*h_full:.1f}px)",
                           f"H_full={h_full}px dpr={dpr}；目标 ms54={early} "
                           f"ms432={late} 单调={mono}；基线恒 {h_full}px（整块全画）"))
        ev.append("tNNN_overlay_own.png 的 alpha 非透明外接框（bbox_h/dpr）")

    # ---- 1B 合成帧新增内容量 ----
    b_add, t_add = {}, {}
    p0b, p0t = base.t_comp.get(0), tgt.t_comp.get(0)
    for t in _EARLY_T:
        if p0b and base.t_comp.get(t):
            b_add[t] = diff_stats(base.t_comp[t], p0b)["area_pct"]
        if p0t and tgt.t_comp.get(t):
            t_add[t] = diff_stats(tgt.t_comp[t], p0t)["area_pct"]
    if not t_add:
        subs.append(result("1B 合成帧新增内容量 D_add", "SKIP", b_add, None,
                           f"≤{THRESHOLDS['c1_add_ratio']}×基线",
                           "目标目录缺 t000/tNNN_composite.png"))
        s1b = "SKIP"
    else:
        ratios = {t: round(t_add[t] / b_add[t], 4) for t in t_add
                  if b_add.get(t)}
        ok = bool(ratios) and all(v <= THRESHOLDS["c1_add_ratio"] for v in ratios.values())
        s1b = "PASS" if ok else "FAIL"
        subs.append(result("1B 合成帧新增内容量 D_add", s1b, b_add, t_add,
                           f"每拍 ≤{THRESHOLDS['c1_add_ratio']}×基线",
                           f"比值={ratios}；基线 14.82/13.76/21.13%"))
        ev.append("tNNN_composite.png 与 t000_composite.png 的差分")

    # ---- 1C 相交像素数 ----
    b_ov, t_ov = {}, {}
    for t in _EARLY_T:
        sb, st = base.geom_snap(t), tgt.geom_snap(t)
        pb, pt = base.t_own.get(t), tgt.t_own.get(t)
        if sb and pb:
            b_ov[t] = overlap_pixels(sb, painted_rect_in_body(sb, pb))
        if st and pt:
            t_ov[t] = overlap_pixels(st, painted_rect_in_body(st, pt))
    if not t_ov:
        subs.append(result("1C painted 区×旧项相交像素", "SKIP",
                           {t: v["total"] for t, v in b_ov.items()}, None,
                           f"≤{THRESHOLDS['c1_overlap_ratio']}×基线",
                           "目标目录缺 geom.json / tNNN_overlay_own.png"))
        s1c = "SKIP"
    else:
        ratios = {t: round(t_ov[t]["total"] / b_ov[t]["total"], 4)
                  for t in t_ov if b_ov.get(t) and b_ov[t]["total"] > 0}
        # 基线为 0 时不做除法，直接要求目标也为 0
        zero_ok = all(t_ov[t]["total"] == 0 for t in t_ov
                      if b_ov.get(t) and b_ov[t]["total"] == 0)
        ok = bool(ratios) and all(v <= THRESHOLDS["c1_overlap_ratio"]
                                  for v in ratios.values()) and zero_ok
        s1c = "PASS" if ok else "FAIL"
        subs.append(result("1C painted 区×旧项相交像素", s1c,
                           {t: v["total"] for t, v in b_ov.items()},
                           {t: v["total"] for t, v in t_ov.items()},
                           f"每拍 ≤{THRESHOLDS['c1_overlap_ratio']}×基线",
                           f"比值={ratios}；基线 43318/38913/36481 px²；"
                           f"覆盖来源={[v['coverage'] for v in t_ov.values()]}"))
        ev.append("geom.json 的 form_rows/legacy 几何 + overlay_own alpha 外接框")

    status = combine([s1a, s1b, s1c])
    return {"id": "1", "title": "早期双份文字消失（ms54/84/106）",
            "status": status, "subs": subs, "evidence": ev,
            "threshold": "1A/1B/1C 三项全过",
            "detail": ("自检模式 target=基线 → 1B/1C 比值恒 1.0、"
                       "1A 可见高度恒 H_full → 预期 FAIL"
                       if mode == "selfcheck" else
                       "验收模式：1A/1B/1C 三项全过才 PASS；"
                       "任一子项 FAIL 即判据 1 FAIL（各子项基线/目标/阈值见上表）")}


def check_c2(base: Artifacts, tgt: Artifacts, mode: str) -> dict:
    """判据 2：早期突变收敛。

    度量 D_early_max = ms∈[0,120] 内最大「对上帧diff%」
    （优先用 report.md 的稠密 67 帧序列；缺失则用现存 PNG 重算并标注降级）。
    基线实测 20.31%（i=8, ms83.8，即 seq8→9 ms72.1→83.8）—— 正是「清晰+清晰」
    双份文字突然成形的那一拍；次大 14.38%。

    阈值依据：
    - 验收模式（有独立 target）：≤10.0% ≈ 0.5×20.31%，且 ms0~120 内不得再有
      >12% 的单帧突变。20.31% 是次大值的 1.41 倍、是「双份成形」的特征峰；
      砍半是既显著又留有余量的门槛。
    - 自检模式（target=基线，无独立对照）：只验「不回归」≤1.2×基线=24.4%，
      改进阈值作为「验收判据」单独列出，不参与自检 PASS/FAIL。
    """
    bd = parse_report_diffs(base.report_md)
    td = parse_report_diffs(tgt.report_md)
    b_src = "report.md(稠密)" if bd else "PNG重算(降级)"
    t_src = "report.md(稠密)" if td else "PNG重算(降级)"
    if not bd:
        bd = sparse_diffs(base)
    if not td:
        td = sparse_diffs(tgt)
    b, t = early_max(bd), early_max(td)
    ev = ["report.md 逐拍表「对上帧diff%」列（稠密 67 帧）"] if b_src.startswith("report") else []
    ev.append("基线 seq8→9 (ms72.1→83.8) = 20.31%")

    if b["value"] is None or t["value"] is None:
        return {"id": "2", "title": "早期突变收敛", "status": "SKIP",
                "subs": [], "evidence": ev,
                "threshold": f"验收≤{THRESHOLDS['c2_improve_abs']}%",
                "detail": "缺 diff 序列（frames/report/PNG 均无）"}
    guard = b["value"] * THRESHOLDS["c2_guard_ratio"]
    if mode == "accept":
        # D_early_max ≤10% 已蕴含「无 >12% 单帧突变」；gt12 只作诊断输出，
        # 不再叠加为独立硬性否决条件，避免阈值重复/过严。
        ok = (t["value"] <= THRESHOLDS["c2_improve_abs"]
              and t["value"] <= guard)
        status = "PASS" if ok else "FAIL"
        thr = (f"≤{THRESHOLDS['c2_improve_abs']}%（=0.5×基线）且 "
               f"≤{guard:.2f}%（不回归）")
    else:
        ok = t["value"] <= guard
        status = "PASS" if ok else "FAIL"
        thr = (f"自检只验不回归 ≤{guard:.2f}%（1.2×基线）；"
               f"验收另要求 ≤{THRESHOLDS['c2_improve_abs']}%")
    return {"id": "2", "title": "早期突变收敛（seq8→9 20.31% 基线）",
            "status": status, "subs": [], "evidence": ev, "threshold": thr,
            "detail": (f"基线 D_early_max={b['value']}% @ms{b['at'][0]}(i={b['at'][1]}) "
                       f"源={b_src}；目标={t['value']}% @ms{t['at'][0]} 源={t_src}；"
                       f"目标 >{THRESHOLDS['c2_jump_cap']}% 的帧(诊断)={t['gt12']}"
                       + ("；⚠️ 两侧 diff 源不一致，数值可比性下降" if b_src != t_src else ""))}


def check_c3(base: Artifacts, tgt: Artifacts, mode: str) -> dict:
    """判据 3：ms≈432 落定态仍整齐干净。

    取保存帧里 ms 最接近 432 的 vNNN 与 SETTLED 帧做差分。
    基线 v040(ms431.8) vs v066(SETTLED) = 0.17% / mean 0.33；
    report.md 稠密表 i=40 对落定 0.17%、i=41 0.13%。
    阈值 ≤1.00%（约 6× 基线，吸收动画时序抖动）且 mean ≤1.5。
    """
    def calc(a: Artifacts) -> dict:
        s = a.settled_v()
        i = a.nearest_v(432.0)
        if s is None or i is None:
            return {"ok": False, "why": "缺 SETTLED 帧或 ms≈432 的 vNNN 帧"}
        d = diff_stats(a.v[i], a.v[s])
        return {"ok": True, "frame": os.path.basename(a.v[i]),
                "ms": a.ms(i), "settled": os.path.basename(a.v[s]),
                "area_pct": d["area_pct"], "mean_abs": d["mean_abs"]}

    b, t = calc(base), calc(tgt)
    if not t["ok"]:
        return {"id": "3", "title": "ms≈432 落定态", "status": "SKIP",
                "subs": [], "evidence": [], "threshold": f"≤{THRESHOLDS['c3_settle_area']}%",
                "detail": t["why"]}
    ok = (t["area_pct"] <= THRESHOLDS["c3_settle_area"]
          and t["mean_abs"] <= THRESHOLDS["c3_settle_mean"])
    return {"id": "3", "title": "ms≈432 落定态整齐干净",
            "status": "PASS" if ok else "FAIL", "subs": [],
            "evidence": ["vNNN_veh_tick.png(ms≈432)", "vNNN_SETTLED.png"],
            "threshold": (f"area%≤{THRESHOLDS['c3_settle_area']} 且 "
                          f"mean_abs≤{THRESHOLDS['c3_settle_mean']}"),
            "detail": (f"基线 {b.get('area_pct')}% / mean {b.get('mean_abs')} "
                       f"({b.get('frame')}@{b.get('ms')}ms vs {b.get('settled')})；"
                       f"目标 {t['area_pct']}% / mean {t['mean_abs']} "
                       f"({t['frame']}@{t['ms']}ms vs {t['settled']})")}


def check_c4(base: Artifacts, tgt: Artifacts, mode: str) -> dict:
    """判据 4：swap 当拍前后 3 帧逐像素差仍为 0.00（od63 修复不能被推翻）。

    从 frames.json 的 swap_idx 取 [swap-3, swap+3] 内**已保存**的 vNNN 帧，
    逐对做差分。基线 6 对全部 0.00% / mean 0.00。
    阈值 area%≤0.10、mean_abs≤0.50：既容纳 PNG/AA 噪声，又能抓住覆盖层
    多画一帧（那会是数 % 的差分）。
    """
    def calc(a: Artifacts) -> dict:
        si = a.swap_idx()
        if si is None:
            return {"ok": False, "why": "frames.json 无 swap_idx / swap_frame"}
        win = sorted(i for i in a.v if si - 3 <= i <= si + 3)
        pairs = []
        for x, y in zip(win, win[1:]):
            d = diff_stats(a.v[y], a.v[x])
            pairs.append({"from": x, "to": y, **d})
        return {"ok": bool(pairs), "swap_idx": si, "window": win,
                "pairs": pairs,
                "max_area": max((p["area_pct"] for p in pairs), default=None),
                "max_mean": max((p["mean_abs"] for p in pairs), default=None),
                "why": "" if pairs else "swap 窗口内没有成对保存帧"}

    b, t = calc(base), calc(tgt)
    if not t["ok"]:
        return {"id": "4", "title": "swap 当拍前后 3 帧", "status": "SKIP",
                "subs": [], "evidence": [], "threshold": "逐像素 0.00",
                "detail": t["why"]}
    ok = (t["max_area"] is not None
          and t["max_area"] <= THRESHOLDS["c4_swap_area"]
          and t["max_mean"] <= THRESHOLDS["c4_swap_mean"])
    return {"id": "4", "title": "swap 当拍前后 3 帧逐像素差 = 0.00",
            "status": "PASS" if ok else "FAIL", "subs": [],
            "evidence": ["frames.json swap_idx", "v054..v060_veh_tick.png"],
            "threshold": (f"max area%≤{THRESHOLDS['c4_swap_area']} 且 "
                          f"max mean_abs≤{THRESHOLDS['c4_swap_mean']}"),
            "detail": (f"基线 swap_idx={b.get('swap_idx')} 窗口={b.get('window')} "
                       f"max={b.get('max_area')}%/{b.get('max_mean')}；"
                       f"目标 swap_idx={t.get('swap_idx')} 窗口={t.get('window')} "
                       f"max={t['max_area']}%/{t['max_mean']}；"
                       f"逐对={[(p['from'],p['to'],p['area_pct']) for p in t['pairs']]}")}


def check_c5(base: Artifacts, tgt: Artifacts, mode: str,
             haveold_dir: str | None = None) -> dict:
    """判据 5：有旧块（换车号）场景不回归、且不出现淡化发灰。

    haveold_dir：main() 用 discover_haveold() 探测到的 5b 数据目录，
    兼容平铺契约 `haveold/` 与历史命名 `old_block/`；报告会写明实际路径。

    5a 不发灰：目标目录**所有** *_overlay_own.png 的不透明像素占比 ≤2%
       （基线恒 0.00%）；另查 geom.json 任一 ov.bg_none=False → 直接 FAIL
       （铺了实底的源码级证据）。
    5b P1/P2/P3 结构：需要 `haveold/frames.json`（或 `old_block/frames.json`）
       —— 换车号方向录屏；由 discover_haveold() 自动探测，⛔ 不静默跳过。
       P1 (0,200] 至少一帧 ov_vis=True 且 ov_items>0；
       P2 (200,320) 全部 ov_vis=False（空白段，代码里是 overlay.clear()）；
       P3 [320,680) 至少一帧 ov_vis=True 且 ov_items>0，且 ov_alpha 达到 ≥0.95；
       swap 帧 ms≥680；swap 之后全部 ov_vis=False；
       全程 ov_alpha ≤1.0。
       ⚠️ 现有基线里**没有**换车号方向的录屏，自检时 5b 报 SKIP（如实标注）；
          第二段由 vehicle-fix 按平铺契约产出 `_check/vehicle_fix/haveold/`。
    """
    subs, ev = [], []

    # ---- 5a 发灰 ----
    def opaques(a: Artifacts) -> dict:
        out = {}
        for t, p in sorted(a.t_own.items()):
            out[f"t{t:03d}"] = alpha_stats(p)["pct_opaque"]
        return out
    b_op, t_op = opaques(base), opaques(tgt)
    bg_bad = []
    for g in (tgt.geom or {}).get("geom", []):
        ov = g.get("ov") or {}
        if ov and (ov.get("bg_none") is False or ov.get("translucent") is False):
            bg_bad.append((g.get("target_ms"), ov))
    if not t_op:
        s5a = "SKIP"
        subs.append(result("5a 不发灰（不透明占比）", "SKIP", b_op, None,
                           f"≤{THRESHOLDS['c5_opaque_max']}%",
                           "目标目录缺 tNNN_overlay_own.png"))
    else:
        ok = (max(t_op.values()) <= THRESHOLDS["c5_opaque_max"] and not bg_bad)
        s5a = "PASS" if ok else "FAIL"
        subs.append(result("5a 不发灰（不透明占比）", s5a, b_op, t_op,
                           f"≤{THRESHOLDS['c5_opaque_max']}% 且 geom.ov.bg_none 全为 True",
                           f"目标最大={max(t_op.values())}%；bg_none 异常={bg_bad}"))
        ev.append("所有 tNNN_overlay_own.png 的 alpha 分布 + geom.json 的 ov.bg_none")

    # ---- 5b P1/P2/P3 ----
    ob = haveold_dir or tgt.old_block_dir
    src_note = (f"5b 数据目录={ob}" if ob else
                "未探测到 haveold/ 或 old_block/ 数据目录")
    if not ob or not os.path.isfile(os.path.join(ob, "frames.json")):
        s5b = "SKIP"
        subs.append(result("5b 换车号 P1/P2/P3 结构", "SKIP", None, None,
                           "P1 有素材 / P2 空白 / P3 淡入 / swap≥680 / swap 后 ov 全隐",
                           f"⛔ 无「换车号 / 有旧块」方向录屏；{src_note}；"
                           "第二段按平铺契约补齐 `haveold/frames.json` 后自动启用"))
    else:
        fj = json.load(open(os.path.join(ob, "frames.json"), encoding="utf-8"))
        fs = [f.get("meta", {}) for f in fj.get("frames", [])]
        def ms(m):
            return float(m.get("ms") or -1)
        p1 = [m for m in fs if 0 < ms(m) <= _PHASE["p1_end"]]
        p2 = [m for m in fs if _PHASE["p1_end"] < ms(m) < _PHASE["p2_end"]]
        p3 = [m for m in fs if _PHASE["p2_end"] <= ms(m) < _PHASE["p3_end"]]
        swaps = [(i, ms(m)) for i, m in enumerate(fs) if m.get("swap_frame")]
        after = [m for m in fs if swaps and ms(m) > swaps[0][1]]
        a_ok = all((m.get("ov_alpha") or 0) <= THRESHOLDS["c5_alpha_cap"] + 1e-6
                   for m in fs)
        checks = {
            "P1 有旧块素材": any(m.get("ov_vis") and (m.get("ov_items") or 0) > 0
                                for m in p1),
            "P2 空白段": bool(p2) and all(not m.get("ov_vis") for m in p2),
            "P3 淡入到 α≥0.95": any(m.get("ov_vis") and (m.get("ov_items") or 0) > 0
                                    for m in p3)
                              and any((m.get("ov_alpha") or 0) >= 0.95 for m in p3),
            "swap 帧 ms≥680": bool(swaps) and swaps[0][1] >= _PHASE["p3_end"],
            "swap 后覆盖层全隐": bool(after) and all(not m.get("ov_vis") for m in after),
            "ov_alpha ≤1.0": a_ok,
        }
        s5b = "PASS" if all(checks.values()) else "FAIL"
        subs.append(result("5b 换车号 P1/P2/P3 结构", s5b, None, checks,
                           "六项结构断言全 True",
                           f"{src_note}；分段帧数 P1={len(p1)} P2={len(p2)} "
                           f"P3={len(p3)}；swap={swaps[:1]}；明细={checks}"))
        ev.append(f"{ob}/frames.json 的 meta.ov_vis/ov_items/ov_alpha/swap_frame")

    return {"id": "5", "title": "有旧块场景不回归且不发灰",
            "status": combine([s5a, s5b]), "subs": subs, "evidence": ev,
            "threshold": f"5a ≤{THRESHOLDS['c5_opaque_max']}% 不透明 + 5b 六项结构断言",
            "detail": (f"自检：5a 用现有 overlay_own 可验（预期 PASS）；{src_note}"
                       if mode == "selfcheck" else
                       f"验收模式：5a 不发灰 + 5b 六项结构断言；{src_note}")}


def check_c6(base: Artifacts, tgt: Artifacts, mode: str) -> dict:
    """判据 6：覆盖层不透明背板回归（od62「发灰」特征）。

    修复前 t054_overlay_own.png = 全透明 73.91% / 半透明 26.09% / **不透明 0%**。
    若修复误铺不透明实底（fillRect / bg≠None），不透明占比会跃升到接近 100%。
    阈值：早期 4 拍（54/84/106/432）每拍不透明占比 ≤2.00%（留 AA 边缘余量），
    且不得出现 bg_none=False。
    """
    def stats(a: Artifacts) -> dict:
        out = {}
        for t in (54, 84, 106, 432):
            p = a.t_own.get(t)
            if p:
                s = alpha_stats(p)
                out[f"t{t:03d}"] = {"全透明": s["pct_transparent"],
                                    "半透明": s["pct_partial"],
                                    "不透明": s["pct_opaque"]}
        return out
    b, t = stats(base), stats(tgt)
    if not t:
        return {"id": "6", "title": "覆盖层不透明背板回归", "status": "SKIP",
                "subs": [], "evidence": [], "threshold": f"不透明≤{THRESHOLDS['c6_opaque_max']}%",
                "detail": "目标目录缺 tNNN_overlay_own.png"}
    worst = max(v["不透明"] for v in t.values())
    bg_bad = [g.get("target_ms") for g in (tgt.geom or {}).get("geom", [])
              if (g.get("ov") or {}).get("bg_none") is False]
    ok = worst <= THRESHOLDS["c6_opaque_max"] and not bg_bad
    return {"id": "6", "title": "覆盖层不透明背板回归（od62 发灰）",
            "status": "PASS" if ok else "FAIL", "subs": [],
            "evidence": ["t054/t084/t106/t432_overlay_own.png 的 alpha 分布",
                         "geom.json 的 ov.bg_none"],
            "threshold": (f"每拍不透明≤{THRESHOLDS['c6_opaque_max']}%（基线 0.00%）、"
                          "且 bg_none 全 True"),
            "detail": (f"基线 t054=73.91/26.09/0.00%；目标各拍={t}；"
                       f"最大不透明={worst}%；bg_none 异常={bg_bad}")}


# ================================================================== 报告
def render(res: dict) -> str:
    L: list[str] = []

    def say(s: str = "") -> None:
        L.append(s)

    say("# 车号修复独立验收判据 —— 报告\n")
    say(f"- 生成时间：{res['generated']}")
    say(f"- 模式：`{res['mode']}`（{'自检：target=基线' if res['mode']=='selfcheck' else '正式验收：target≠基线'}）")
    say(f"- 基线目录：{res['baseline_dirs']}")
    say(f"- 目标目录：{res['target_dirs']}")
    say(f"- 脚本：`devtools/_verify_vehicle_fix.py`（纯离屏：PIL+numpy+cv2，"
        "不构造 QApplication / 不开主窗口 / 不碰 `.app_run.lock`）")
    say(f"- 阈值来源：**修复前基线实测**（见各判据 baseline 列），非源码预测\n")

    integ = res.get("baseline_integrity")
    if integ:
        say(f"## 基线完整性门禁 — **{integ['verdict']}**\n")
        say("| 校验项 | 结果 | 期望（基线真值） | 实测 |")
        say("|---|---|---|---|")
        for it in integ["items"]:
            say(f"| {it['name']} | {'PASS' if it['ok'] else 'FAIL'} | "
                f"`{it['expected']}` | `{it['actual']}` |")
        say("")
        say(f"- 结论：{integ['reason']}")
        if not integ["ok"]:
            say("- ⚠️ 基线未通过完整性校验：以下 6 条判据数值**不可作为正式结论**，"
                "仅用于诊断；请先恢复基线后再出 PASS/FAIL。")
        say("")
    if res.get("haveold_dir"):
        say(f"- 5b 数据目录（自动探测）：`{res['haveold_dir']}`\n")
    elif res.get("mode") == "accept":
        say("- 5b 数据目录：**未探测到**（haveold/ 与 old_block/ 均无 frames.json）"
            " → 判据 5b 将报 SKIP/FAIL，⛔ 不静默跳过。\n")

    say("## 汇总\n")
    say("| # | 判据 | 结果 | 阈值 |")
    say("|---|---|---|---|")
    for c in res["checks"]:
        say(f"| {c['id']} | {c['title']} | **{c['status']}** | {c['threshold']} |")
    say("")
    fails = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
    skips = [c["id"] for c in res["checks"] if c["status"] == "SKIP"]
    partial = [c["id"] for c in res["checks"] if c["status"] == "PASS*"]
    sub_skips = [(c["id"], s["name"]) for c in res["checks"]
                 for s in c.get("subs", []) if s["status"] == "SKIP"]
    say(f"- FAIL：{fails or '无'}；SKIP：{skips or '无'}；PASS*（含子项 SKIP）：{partial or '无'}")
    if sub_skips:
        say(f"- 子项 SKIP 明细：{sub_skips}")
    if res["mode"] == "selfcheck":
        say(f"- 自检预期：**判据 1 = FAIL，判据 2/3/4/6 = PASS，"
            f"判据 5 = PASS\\*（5a PASS，5b 因无换车号基线 SKIP）**。")
    else:
        say("- 验收模式：判据 2 启用改进阈值 ≤10.0%（自检时只验不回归）；"
            "目标目录与基线目录不同，判据 1/2 为相对比较。")
    say("- `PASS*` = 该判据已客观量化的子项全部通过，但至少一个子项**缺输入数据**，"
        "不算完整 PASS；"
        + ("第二段补齐 `haveold/frames.json`（或 `old_block/frames.json`）后会自动转为 PASS/FAIL。"
           if res["mode"] == "selfcheck" else
           "本次 `haveold/frames.json` 已启用判据 5b，无 SKIP 伪装。"))
    say("")

    for c in res["checks"]:
        say(f"## 判据 {c['id']}：{c['title']} — **{c['status']}**\n")
        if c.get("subs"):
            say("| 子项 | 结果 | 基线 | 目标 | 阈值 | 说明 |")
            say("|---|---|---|---|---|---|")
            for s in c["subs"]:
                say(f"| {s['name']} | {s['status']} | `{s['baseline']}` | "
                    f"`{s['target']}` | {s['threshold']} | {s['detail']} |")
            say("")
        say(f"- 阈值：{c['threshold']}")
        say(f"- 明细：{c['detail']}")
        if c.get("evidence"):
            say(f"- 证据：{'; '.join(c['evidence'])}")
        say("")

    # ---- team-lead 要求：每条判据的基线值 / 目标实测值 / 阈值 / 结果 ----
    say("## 判据数值明细（基线值 / 目标实测值 / 阈值 / 结果）\n")
    say("| # | 判据 / 子项 | 结果 | 基线值 | 目标值（实测） | 阈值 |")
    say("|---|---|---|---|---|---|")
    for c in res["checks"]:
        if c.get("subs"):
            for s in c["subs"]:
                say(f"| {c['id']} | {s['name']} | {s['status']} | "
                    f"`{s['baseline']}` | `{s['target']}` | {s['threshold']} |")
        else:
            parts = [p.strip() for p in re.split(r"[；;]", c["detail"]) if p.strip()]
            b_part = next((p for p in parts if p.startswith("基线")), "—")
            t_part = next((p for p in parts if p.startswith("目标")), "—")
            say(f"| {c['id']} | {c['title']} | {c['status']} | "
                f"`{b_part}` | `{t_part}` | {c['threshold']} |")
    say("")

    ev_idx = res.get("evidence_index") or {}
    if ev_idx:
        say("## 证据文件（相对项目根目录）\n")
        for c in res["checks"]:
            paths = ev_idx.get(c["id"]) or []
            say(f"- **判据 {c['id']} {c['title']}**："
                + (", ".join(f"`{p}`" for p in paths) if paths else "（无）"))
        say("")

    say("## 阈值总表（全部来自修复前基线实测）\n")
    say("| 键 | 值 | 依据 |")
    say("|---|---|---|")
    why = {
        "c1_vis_early_max": "基线 5 拍实际绘制高度恒 H_full=218px（整块全画）；修复后 ms54 应≈holder≈20px",
        "c1_vis_late_min": "ms432 holder≈196px / H_full=218px≈0.90，仍应接近满高",
        "c1_vis_mono_tol": "单调性容差（×H_full=10.9px），吸收 tick 抖动",
        "c1_add_ratio": "D_add 基线 14.82/13.76/21.13%；留 40% 给 holder 位移的不可约分量",
        "c1_overlap_ratio": "相交像素基线 43318/38913/36481 px²，修复后细条在旧项上方应≈0",
        "c2_improve_abs": "0.5 × 20.31%（seq8→9 特征峰，次大 14.38% 的 1.41 倍）",
        "c2_guard_ratio": "不回归上限 1.2×基线=24.4%",
        "c2_jump_cap": "次大突变 14.38%，验收期不应再出现 >12% 的单帧跳变",
        "c3_settle_area": "基线 0.17%(v040)/0.13%(i=41)，留 ~6× 余量吸收时序抖动",
        "c3_settle_mean": "基线 mean_abs 0.33",
        "c4_swap_area": "基线 6 对全 0.00%，0.10% 容纳 PNG/AA 噪声",
        "c4_swap_mean": "基线逐像素 mean_abs 0.00",
        "c5_opaque_max": "基线 overlay_own 不透明恒 0.00%；实底会接近 100%",
        "c6_opaque_max": "同 c5，专用于不透明背板回归",
        "c5_alpha_cap": "ov_alpha 物理上限 1.0",
    }
    for k, v in THRESHOLDS.items():
        say(f"| `{k}` | {v} | {why.get(k, '')} |")
    say("")

    if res["warnings"]:
        say("## 装载告警 / 降级说明\n")
        for w in res["warnings"]:
            say(f"- {w}")
        say("")

    say("## 输入契约（第二段验收目录需备齐）\n")
    say("- `frames.json`（frames[].meta: ms/tag/swap_frame/ov_vis/ov_items/ov_alpha；swap_idx）")
    say("- `vNNN_*.png`：合成帧，NNN=0 基帧序号；抽稀可，SETTLED 帧必须有")
    say("- `report.md`：逐拍表（稠密 67 帧 diff 只在表里；缺失会退化成 PNG 重算并标注）")
    say("- `t000/t054/t084/t106/t432_composite.png` + 同名 `_overlay_own.png`")
    say("- `geom.json`：geom[].overlay / rect_new / holder / form_rows / ov.bg_none")
    say("- `haveold/frames.json`（或历史命名 `old_block/frames.json`）："
        "换车号（has_old=True）方向录屏，判据 5b 需要；脚本自动探测并在报告写明实际路径")
    say("")
    say("## 约束复核\n")
    say("- 未改任何产品源码；未 git；未打包；未动 发布 与 dist*；未跑 selftest。")
    say("- 未读改后的 `ui_settings.py`（阈值全部来自修复前基线实测）。")
    say("- 纯离屏：脚本不 import PySide6、不构造 QApplication、不开真机主窗口、"
        "不获取/释放 `_check/.app_run.lock`；QSettings 未被触碰。")
    say("")

    ap = res.get("appendix")
    if ap:
        say("## 附录：补充量测（⛔ 不参与 6 条 PASS/FAIL、不改判据）\n")
        say("> team-lead 追加项：把「落定时刻推迟」与「画质回归」分开记录；"
            "本节只做量测与描述，⛔ 不下任何 PASS/FAIL。\n")
        f = ap.get("first_fully_settled")
        say("### A. 修复后最早完全落定帧\n")
        if f:
            say(f"- SETTLED 参照帧：`{ap['settled_path']}`"
                f"（idx={ap['settled_idx']}, ms={ap['settled_ms']}）")
            say(f"- **最早完全落定（已保存帧中）**：`v{f['idx']:03d}_veh_tick.png`"
                f" @ **ms={f['ms']}**")
            say(f"- 该帧 vs SETTLED 逐像素差：area% = **{f['area_pct']}** / "
                f"mean_abs = **{f['mean_abs']}** / max_abs = **{f['max_abs']}**")
            p = ap.get("prev_unsettled")
            if p:
                say(f"- ⚠️ 抽稀限制：前一已保存帧 v{p['idx']:03d}@{p['ms']}ms 仍为 "
                    f"{p['area_pct']}% / {p['mean_abs']}；真实落定时刻位于 "
                    f"**({p['ms']}, {f['ms']}] ms** 区间内，"
                    "已保存帧只能给出上界，无法更精确（不外推）。")
        else:
            say("- ⛔ 未找到与 SETTLED 逐像素差为 0 的已保存帧（如实标注）。")
        say("")
        say(f"逐帧量测（共 {ap['n_measured']} 张已保存帧，与 SETTLED 的逐像素差）：\n")
        say("| 帧 | ms | area% | mean_abs | max_abs | 完全落定 |")
        say("|---|---|---|---|---|---|")
        for r in ap.get("frames", []):
            say(f"| v{r['idx']:03d} | {r['ms']} | {r['area_pct']} | "
                f"{r['mean_abs']} | {r['max_abs']} | "
                f"{'是' if r['fully_settled'] else '否'} |")
        say("")
        a = ap.get("t432_alpha")
        if a:
            say("### B. t432 覆盖层自身渲染（客观代理量）\n")
            say(f"- `_check/vehicle_fix/after_geom/t432_overlay_own.png`："
                f"全透明 {a['pct_transparent']}% / 半透明 {a['pct_partial']}% / "
                f"不透明 **{a['pct_opaque']}%**（实底发灰判据）；"
                f"非透明外接框高 bbox_h={a['bbox_h']}px。")
            say("")
        say("### C. t432 合成帧画质描述（目视，纯描述、不下结论）\n")
        say(f"- {ap['visual_note']}")
        say("")

    return "\n".join(L) + "\n"


def render_gate(gate: dict) -> str:
    """基线未恢复时的独立门禁报告（⛔ 不含任何 PASS/FAIL 验收结论）。"""
    L: list[str] = [
        "# 车号修复独立验收 —— 基线完整性门禁 BLOCKED", "",
        f"- 生成时间：{gate['generated']}",
        f"- 模式：`{gate['mode']}`（正式验收，尚未开始）",
        f"- 基线目录：{gate['baseline_dirs']}",
        f"- 目标目录：{gate['target_dirs']}", "",
        "## ⛔ 结论：BLOCKED，未出具任何 PASS/FAIL", "",
        gate["reason"], "",
        "## 基线完整性逐项校验", "",
        "| 校验项 | 结果 | 期望（基线真值） | 实测 |",
        "|---|---|---|---|",
    ]
    for it in gate["baseline_integrity"]["items"]:
        L.append(f"| {it['name']} | {'PASS' if it['ok'] else 'FAIL'} | "
                 f"`{it['expected']}` | `{it['actual']}` |")
    L += [
        "",
        "## 处理建议", "",
        gate["action"], "",
        "- 本门禁为 team-lead 新增；纯离屏，不拿锁、不开真机、不改产品源码、"
        "不覆盖既有报告（只写 *.gate.md / *.gate.json）。",
    ]
    return "\n".join(L) + "\n"


def build_evidence_index(base: Artifacts, tgt: Artifacts,
                         haveold_dir: str | None) -> dict[str, list[str]]:
    """把各判据实际读取的产物整理成「相对项目根目录」的路径，供报告引用。

    只做路径汇总，不读文件内容、不改变任何判据结论。
    """
    def rel(p: str | None) -> str | None:
        if not p:
            return None
        return os.path.relpath(os.path.abspath(p), _ROOT).replace("\\", "/")

    def uniq(seq):
        out, seen = [], set()
        for p in seq:
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        return out

    own = [rel(tgt.t_own.get(t)) for t in _ALL_T]
    comp = [rel(tgt.t_comp.get(t)) for t in _ALL_T]
    geom_p = rel(tgt._find("geom.json"))
    rep_p = rel(tgt._find("report.md"))
    fj_p = rel(tgt._find("frames.json"))

    i432 = tgt.nearest_v(432.0)
    iset = tgt.settled_v()
    settle = [rel(tgt.v.get(i)) for i in (i432, iset) if i is not None]

    si = tgt.swap_idx()
    swap_win = []
    if si is not None:
        for i in range(si - 3, si + 4):
            p = rel(tgt.v.get(i))
            if p:
                swap_win.append(p)

    idx = {
        "1": uniq(own + comp + [geom_p]),
        "2": uniq([rep_p, fj_p]),
        "3": uniq(settle + [fj_p]),
        "4": uniq([fj_p] + swap_win),
        "5": uniq(own + [geom_p]),
        "6": uniq(own + [geom_p]),
    }
    if haveold_dir:
        hp = rel(os.path.join(haveold_dir, "frames.json"))
        if hp:
            idx["5"].append(hp)
    return idx


def appendix_measurement(base: Artifacts, tgt: Artifacts) -> dict:
    """附录补充量测（⛔ 不参与 6 条 PASS/FAIL、不改判据、只做量测与描述）。

    team-lead 追加项：把「落定时刻推迟」与「画质回归」两件事分开记录。
    A. 最早完全落定帧：目标「已保存」vNNN 帧中，第一个与 SETTLED 帧逐像素
       差为 0（area%=0 / mean_abs=0 / max_abs=0）的帧；给出 ms 与 diff 数值。
       ⚠️ 抽稀序列只能给「已保存帧中最早」，真实落定时刻位于
       (前一帧 ms, 该帧 ms] 区间内，如实标注，不外推。
    B. t432_overlay_own.png 的 alpha 客观代理量（实底发灰）。
    C. t432_composite.png 画质静态描述（见 _APPENDIX_VISUAL_NOTE）。
    """
    def rel(p):
        if not p:
            return None
        return os.path.relpath(os.path.abspath(p), _ROOT).replace("\\", "/")

    si = tgt.settled_v()
    sp = tgt.v.get(si) if si is not None else None
    frames: list[dict] = []
    first = None
    if sp:
        for i in sorted(tgt.v):
            if i == si:
                continue
            d = diff_stats(tgt.v[i], sp)
            rec = {"idx": i, "ms": tgt.ms(i), "area_pct": d["area_pct"],
                   "mean_abs": d["mean_abs"], "max_abs": d["max_abs"]}
            rec["fully_settled"] = (d["area_pct"] == 0.0
                                    and d["mean_abs"] == 0.0
                                    and d["max_abs"] == 0)
            frames.append(rec)
            if first is None and rec["fully_settled"]:
                first = rec
    prev = None
    if first:
        cands = [r for r in frames if r["idx"] < first["idx"]]
        prev = cands[-1] if cands else None

    own432 = tgt.t_own.get(432)
    a432 = alpha_stats(own432) if own432 else None

    return {
        "settled_idx": si,
        "settled_ms": tgt.ms(si) if si is not None else None,
        "settled_path": rel(sp),
        "first_fully_settled": first,
        "prev_unsettled": prev,
        "frames": frames,
        "n_measured": len(frames),
        "t432_alpha": a432,
        "visual_note": _APPENDIX_VISUAL_NOTE,
    }


# ================================================================== main
def main() -> int:
    ap = argparse.ArgumentParser(description="车号修复独立验收判据（纯离屏）")
    ap.add_argument("--baseline", default=_DEFAULT_BASE, help="修复前逐拍基线目录")
    ap.add_argument("--baseline-geom", default=_DEFAULT_BASE_GEOM,
                    help="修复前几何/overlay_own 基线目录")
    ap.add_argument("--target", default=None,
                    help="待验收目录；省略=自检（用基线自身当目标）")
    ap.add_argument("--target-geom", default=None,
                    help="待验收几何目录（可选，与 --target 合并查找）")
    ap.add_argument("--out", default=_DEFAULT_OUT)
    ap.add_argument("--json", dest="json_out", default=_DEFAULT_JSON)
    ap.add_argument("--p1", type=float, default=_PHASE["p1_end"])
    ap.add_argument("--p2", type=float, default=_PHASE["p2_end"])
    ap.add_argument("--p3", type=float, default=_PHASE["p3_end"])
    ap.add_argument("--haveold", default=None,
                    help="5b 换车号数据目录（含 frames.json）；省略则自动探测"
                         " target 同级的 haveold/ 或 old_block/")
    args = ap.parse_args()
    _PHASE.update({"p1_end": args.p1, "p2_end": args.p2, "p3_end": args.p3})

    mode = "accept" if args.target else "selfcheck"
    base = Artifacts([args.baseline, args.baseline_geom], "baseline")
    if mode == "selfcheck":
        tgt = base                       # 自检：目标 == 基线
        tdirs = list(base.dirs)
    else:
        tdirs = [args.target, args.target_geom] if args.target_geom else [args.target]
        tgt = Artifacts(tdirs, "target")

    # ---- team-lead 新增门禁：accept 模式出结论前先校验基线完整性 ----
    integ = baseline_integrity(base)
    haveold_dir = discover_haveold(tdirs, args.haveold)

    if mode == "accept" and not integ["ok"]:
        # ⛔ 基线未恢复：不出任何 PASS/FAIL，不覆盖既有报告
        gate = {
            "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "accept", "gate": "BLOCKED",
            "baseline_dirs": base.dirs, "target_dirs": tdirs,
            "baseline_integrity": integ, "haveold_dir": haveold_dir,
            "reason": "基线完整性校验未通过：默认基线目录疑似被修复后数据覆写污染，"
                      "未出具任何 PASS/FAIL 验收结论。",
            "action": "请从 _check/vehicle_fix/before/ 原样恢复 "
                      "_check/evidence_vehicle/ 与 _check/evidence_vehicle_geom/ 后重试；"
                      "或显式 --baseline / --baseline-geom 指向干净基线副本。",
        }
        stem = os.path.splitext(os.path.abspath(args.out))[0]
        gmd, gjs = stem + ".gate.md", stem + ".gate.json"
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(gmd, "w", encoding="utf-8") as fh:
            fh.write(render_gate(gate))
        with open(gjs, "w", encoding="utf-8") as fh:
            json.dump(gate, fh, ensure_ascii=False, indent=1, default=str)
        print("[BLOCKED] 基线完整性校验未通过，未出具 PASS/FAIL 结论。")
        print(f"[BLOCKED] 门禁报告 -> {gmd}")
        print(f"[BLOCKED] 门禁数据 -> {gjs}")
        return 2

    checks = [check_c1(base, tgt, mode), check_c2(base, tgt, mode),
              check_c3(base, tgt, mode), check_c4(base, tgt, mode),
              check_c5(base, tgt, mode, haveold_dir), check_c6(base, tgt, mode)]

    warnings = [f"[baseline] {w}" for w in base.warnings]
    if tgt is not base:
        warnings += [f"[target] {w}" for w in tgt.warnings]
    res = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "baseline_dirs": base.dirs,
        "target_dirs": tdirs,
        "thresholds": THRESHOLDS,
        "checks": checks,
        "warnings": warnings,
        "summary": {c["id"]: c["status"] for c in checks},
        "baseline_integrity": integ,
        "haveold_dir": haveold_dir,
        "evidence_index": build_evidence_index(base, tgt, haveold_dir),
        "appendix": appendix_measurement(base, tgt),
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(render(res))
    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1, default=str)

    print(render(res))
    print(f"[OK] report -> {args.out}")
    print(f"[OK] json   -> {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
