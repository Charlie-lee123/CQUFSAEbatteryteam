#!/usr/bin/env bash
# 必做① 交换测量（lead 2026-10-19 定的纪律，照工程师给的流程）：
#   跑前核 md5 → 备份 → 换旧渐隐实现 → 跑探针（数据带 _oldfade）→ 还原 → 再核 md5
#
# ⚠️ 只在桌面 ALIVE 时跑（两个探针都要真屏抓像素）。
# ⚠️ 跑完 main_window.py 必须回到 74b005571298f275e289db27652ce31a，
#    否则立即停止一切并报 lead + 工程师（指纹不核导致数据归属不清，吃过亏）。
#
# 旧实现在探针上的预期（阳性对照三证据，全部来自修复前的实测记录）：
#   1. layered_risk：快照全程**非** WS_EX_LAYERED（"始终 layered"断言应 FAIL）；
#   2. PIX/P2：渐隐段均亮**线性趋黑** 45.1 → 20.8 → 3.0 → 0.2 → 0.0；
#   3. PIX/P2：撤快照那一帧均亮 0.0 → 45.1 整块硬跳（变化点 86400 量级）。
set -u
cd "$(dirname "$0")/.."
EXPECT=74b005571298f275e289db27652ce31a
PY=.venv/Scripts/python.exe

stamp() { md5sum main_window.py | cut -d' ' -f1; }

before=$(stamp)
if [ "$before" != "$EXPECT" ]; then
  echo "ABORT: pre-run md5 is not the authorized build ($before != $EXPECT)"
  exit 1
fi

cp main_window.py main_window.py.bak
cp devtools/_qa_old_impl/main_window_old_fade.py main_window.py
echo "swapped in old fade impl (file md5 $(md5sum devtools/_qa_old_impl/main_window_old_fade.py | cut -d' ' -f1))"

restore() {
  mv -f main_window.py.bak main_window.py
  after=$(stamp)
  echo "restored md5 = $after"
  if [ "$after" != "$EXPECT" ]; then
    echo "ABORT: post-restore md5 drifted! stop everything, report lead+engineer"
    exit 2
  fi
}
trap restore EXIT

"$PY" devtools/_qa_layered_risk.py > _check/layered_risk_oldfade.txt 2>&1
echo "layered_risk(old fade) exit=$?"
"$PY" devtools/_qa_anim_verdict.py PIX > _check/pix_run_oldfade.txt 2>&1
echo "PIX(old fade) exit=$?"
echo "done. old-fade data: _check/layered_risk_oldfade.txt / _check/pix_run_oldfade.txt"
