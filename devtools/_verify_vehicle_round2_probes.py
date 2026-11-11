"""Round 2 独立边界探针（QA 自建；不修改 harness、不修改工程师探针）。

目的：回答 team-lead 点名的 4 个问题，全部离线、确定性：
  ① 「上传 → 第1次刷新 → 90s TTL 内第2次刷新且 API 不可达」会不会掉回空库；
  ② 「上传 → 只刷新一次但 API 全挂」能否看到刚登记的包；
  ③ 「上传 → 连续两次刷新 API 都挂」第二次是否掉回空库；
  ④ 我自设计的 download_remote_pack() 用例（不采信工程师探针 [8]）；
  ⑤ AC-08 运行时证据：读链路请求是否带 Authorization / URL 是否带凭据；
  ⑥ AC-11 静态证据：是否存在绕过 _open 的网络出口；
  ⑦ AC-05 实测耗时。

⛔ 只 import / 调用 vehicle_share + 复用我自己的 harness 的 FakeNet / install_fake；
   不改产品源码、不写 QSettings、不开主窗口、不跑 selftest.py。

产物：_check/vehicle_index_round2_probes.txt
"""

from __future__ import annotations

import copy
import re
import sys
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "devtools") not in sys.path:
    sys.path.insert(0, str(ROOT / "devtools"))

import _verify_vehicle_index as H

import vehicle_share as vs

CHECK = ROOT / "_check"
CHECK.mkdir(parents=True, exist_ok=True)
LINES: list[str] = []


def emit(text: str = "") -> None:
    line = H.redact(str(text))
    LINES.append(line)
    print(line)


def fresh_net(api_doc=None, mirror_doc=None) -> H.FakeNet:
    net = H.FakeNet()
    net.authoritative_doc = copy.deepcopy(
        api_doc if api_doc is not None else H.FRESH_DOC)
    net.mirror_doc = copy.deepcopy(
        mirror_doc if mirror_doc is not None else H.STALE_DOC)
    return net


def names(result: dict) -> list[str]:
    return [item["name"] for item in (result.get("items") or [])]


FRESH_PACK = {
    "schema": 1, "app": "CQUFSAE串口助手",
    "exported_at": "2026-11-09T23:00:00",
    "vehicles": [{"name": "E74-2026", "cells": [25, 25, 25, 25, 25],
                  "temps": [8, 8, 8, 8, 8]}],
}
STALE_PACK = {
    "schema": 1, "app": "CQUFSAE串口助手",
    "exported_at": "2026-11-09T19:00:00",
    "vehicles": [{"name": "OLD-PACK", "cells": [1], "temps": [1]}],
}


def main() -> int:
    emit("=" * 100)
    emit("Round 2 独立边界探针 — QA 自建（离线固定响应，不采信工程师自测）")
    emit(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    emit(f"vehicle_share.py: {ROOT / 'vehicle_share.py'}")
    emit(f"harness: {ROOT / 'devtools' / '_verify_vehicle_index.py'}")
    emit("=" * 100)
    emit()
    emit("【判据 oracle】用户刚上传成功 → 90s「刚写过」窗口内的任何一次刷新，")
    emit("  即使 contents API 不可达、镜像仍是 packs=0 旧缓存，也不得把在线库显示成空的；")
    emit("  因为进程自己刚刚写进去的包是已知事实。这是本次 PRD「上传成功但刷新为空」的")
    emit("  直接延伸，不是额外要求。")
    emit()

    # ---------------------------------------------------------------- ①
    emit("[P1] 上传 → 第1次刷新(API OK) → 90s 内第2次刷新(API 不可达)")
    vs.reset_written_marker()
    net = fresh_net(H.STALE_DOC, H.STALE_DOC)
    with H.install_fake(net):
        up = vs.update_index(H.PACK_NEW, H.SENTINEL_TOKEN)
        pending_after_put = vs._WRITTEN_PENDING
        r1 = vs.fetch_remote_index(timeout_ms=1500)
        pending_after_r1 = vs._WRITTEN_PENDING
        time.sleep(0.15)
        net.contents_fail = True
        r2 = vs.fetch_remote_index(timeout_ms=1500)
        time.sleep(0.15)
    emit(f"  update_index: ok={up['ok']} count={up['count']}  "
         f"PUT后 _WRITTEN_PENDING={pending_after_put}")
    emit(f"  第1次刷新(API OK): items={len(r1['items'])} names={names(r1)} "
         f"source={r1['source']}  刷新后 _WRITTEN_PENDING={pending_after_r1}")
    emit(f"  第2次刷新(API 挂/CDN 仍 packs=0, TTL 内): "
         f"items={len(r2['items'])} names={names(r2)} source={r2['source']}")
    p1_bad = (len(r1["items"]) > 0 and len(r2["items"]) == 0)
    emit(f"  → {'❌ 掉回空库（用户可见缺陷）' if p1_bad else '✔ 未掉回空库'}")
    emit(f"  → 期望: 第2次刷新仍能看到 QA-2026；实际: {names(r2)}")
    emit()

    # ---------------------------------------------------------------- ②
    emit("[P2] 上传 → 只刷新一次，但 API 全挂（TTL 窗口内的首次刷新）")
    vs.reset_written_marker()
    net2 = fresh_net(H.STALE_DOC, H.STALE_DOC)
    with H.install_fake(net2):
        up2 = vs.update_index(H.PACK_NEW, H.SENTINEL_TOKEN)
        net2.contents_fail = True
        r = vs.fetch_remote_index(timeout_ms=1500)
        time.sleep(0.15)
    emit(f"  update_index: ok={up2['ok']} count={up2['count']}")
    emit(f"  唯一一次刷新(API 挂/CDN packs=0): "
         f"items={len(r['items'])} names={names(r)} source={r['source']}")
    p2_ok = "QA-2026" in names(r)
    emit(f"  → {'✔ 能看到刚登记的包（回填生效）' if p2_ok else '❌ 连唯一一次刷新都空'}")
    emit()

    # ---------------------------------------------------------------- ③
    emit("[P3] 上传 → 连续两次刷新 API 都挂（模拟一次持续 API 抖动）")
    vs.reset_written_marker()
    net3 = fresh_net(H.STALE_DOC, H.STALE_DOC)
    with H.install_fake(net3):
        up3 = vs.update_index(H.PACK_NEW, H.SENTINEL_TOKEN)
        net3.contents_fail = True
        a1 = vs.fetch_remote_index(timeout_ms=1500)
        time.sleep(0.15)
        a2 = vs.fetch_remote_index(timeout_ms=1500)
        time.sleep(0.15)
    emit(f"  update_index: ok={up3['ok']} count={up3['count']}")
    emit(f"  第1次刷新: items={len(a1['items'])} names={names(a1)} "
         f"source={a1['source']}")
    emit(f"  第2次刷新: items={len(a2['items'])} names={names(a2)} "
         f"source={a2['source']}")
    p3_bad = (len(a1["items"]) > 0 and len(a2["items"]) == 0)
    emit(f"  → {'❌ 第2次即掉回空库' if p3_bad else '✔ 两次都非空'}")
    emit()

    # ---------------------------------------------------------------- ④
    emit("[P4] 我自设计的 download_remote_pack() 用例（不采信工程师探针 [8]）")
    emit("     假服务器：权威源返回 FRESH_PACK(5 模组)，镜像恒返回 OLD-PACK(1 模组) 旧包")
    vs.reset_written_marker()
    seen: list[dict] = []

    def fake_import(doc):
        seen.append(copy.deepcopy(doc))
        return {"added": 1, "updated": 0, "skipped": 0, "errors": []}

    net4 = fresh_net(FRESH_PACK, STALE_PACK)
    with H.install_fake(net4), mock.patch.object(
            vs, "import_payload", side_effect=fake_import):
        d1 = vs.download_remote_pack({"file": "E74-2026.cqv.json"})
        time.sleep(0.15)
    got = seen[0]["vehicles"][0]["name"] if seen else "<无导入>"
    emit(f"  4a 权威源可用: ok={d1['ok']} source={d1['source']} "
         f"导入的车={got} 权威源请求数={net4.by_kind['authoritative']}")
    p4a = (d1["ok"] and d1["source"] == "api.github.com" and got == "E74-2026")

    seen.clear()
    net5 = fresh_net(FRESH_PACK, STALE_PACK)
    net5.contents_fail = True
    with H.install_fake(net5), mock.patch.object(
            vs, "import_payload", side_effect=fake_import):
        d2 = vs.download_remote_pack({"file": "E74-2026.cqv.json"})
        time.sleep(0.15)
    got2 = seen[0]["vehicles"][0]["name"] if seen else "<无导入>"
    emit(f"  4b 权威源全挂: ok={d2['ok']} source={d2['source']} "
         f"导入的车={got2} 镜像请求数={net5.by_kind['mirror']}")
    p4b = (d2["ok"] and got2 == "OLD-PACK"
           and d2["source"] != "api.github.com")
    emit(f"  → 4a {'PASS' if p4a else 'FAIL'}（必须优先权威源的新包）; "
         f"4b {'PASS' if p4b else 'FAIL'}（全挂须优雅退回镜像链，不抛异常）")
    emit("  → 结论: download_remote_pack() 残留面"
         + ("已修好 ✔" if (p4a and p4b) else "仍有问题 ❌"))
    emit()

    # ---------------------------------------------------------------- ⑤
    emit("[P5] AC-08 运行时证据：读链路请求头 / URL 是否带凭据")
    vs.reset_written_marker()
    net6 = fresh_net(H.FRESH_DOC, H.STALE_DOC)
    with H.install_fake(net6):
        vs.fetch_remote_index(timeout_ms=1200)
        time.sleep(0.15)
    net7 = fresh_net(FRESH_PACK, STALE_PACK)
    with H.install_fake(net7), mock.patch.object(
            vs, "import_payload", side_effect=fake_import):
        vs.download_remote_pack({"file": "E74-2026.cqv.json"})
        time.sleep(0.15)
    reads = [r for n in (net6, net7) for r in n.requests
             if r["method"] != "PUT"]
    auth = [r["url"] for r in reads if "authorization" in r["headers"]]
    secret_urls = [r["url"] for r in reads
                   if H._URL_SECRET_RE.search(r["url"])]
    ua_missing = [r["url"] for r in reads
                  if "user-agent" not in r["headers"]]
    emit(f"  读链路请求数={len(reads)}  带 Authorization 的={len(auth)}  "
         f"URL 含凭据的={len(secret_urls)}  缺 UA 的={len(ua_missing)}")
    emit(f"  URL 样例: {reads[0]['url'][:110] if reads else '-'}")
    emit(f"  → AC-08 {'PASS' if not auth and not secret_urls else 'FAIL'} "
         f"/ AC-12 {'PASS' if not ua_missing else 'FAIL'}")
    emit("  （写链路 update_index 的 Authorization 头是设计内行为，不在此统计）")
    emit()

    # ---------------------------------------------------------------- ⑥
    emit("[P6] AC-11 静态证据：是否存在绕过 vehicle_share._open 的网络出口")
    src = (ROOT / "vehicle_share.py").read_text(encoding="utf-8")
    bypass = re.findall(
        r"urlopen\s*\(|requests\s*\.|QNetworkAccessManager|http\.client|"
        r"httpx\s*\.|urllib3|socket\.create_connection", src)
    open_calls = len(re.findall(r"\b_open\s*\(", src))
    emit(f"  _open( 调用点={open_calls}  绕过写法命中={len(bypass)} {bypass[:5]}")
    emit(f"  → AC-11 {'PASS' if not bypass and open_calls >= 5 else 'FAIL'}")
    emit("  （本 harness 在 OpenerDirector.open 层 fail-closed 拦截，绕过即打真实外网）")
    emit()

    # ---------------------------------------------------------------- ⑦
    emit("[P7] AC-05 实测耗时（全源失败，预算不被线性叠加）")
    vs.reset_written_marker()
    net8 = fresh_net(H.FRESH_DOC, H.STALE_DOC)
    net8.contents_fail = True
    net8.mirror_fail = True
    with H.install_fake(net8):
        t0 = time.perf_counter()
        vs.fetch_remote_index(timeout_ms=1000)
        e1 = time.perf_counter() - t0
        t0 = time.perf_counter()
        vs.fetch_remote_index(timeout_ms=8000)
        e2 = time.perf_counter() - t0
    emit(f"  timeout_ms=1000 → {e1:.3f}s (门槛 <4s)  "
         f"{'PASS' if e1 < 4 else 'FAIL'}")
    emit(f"  timeout_ms=8000 → {e2:.3f}s (门槛 <12s) "
         f"{'PASS' if e2 < 12 else 'FAIL'}")
    emit()

    # ---------------------------------------------------------------- 汇总
    defect = p1_bad or p3_bad or not p2_ok
    emit("=" * 100)
    emit("【汇总】")
    emit(f"  P1 第2次刷新(API 挂) 掉回空库: {'是 ❌' if p1_bad else '否 ✔'}")
    emit(f"  P2 单次刷新 API 全挂能看到新包: {'是 ✔' if p2_ok else '否 ❌'}")
    emit(f"  P3 连续两次刷新 API 都挂，第2次掉回空库: "
         f"{'是 ❌' if p3_bad else '否 ✔'}")
    emit(f"  P4 下载链路: 4a={'PASS' if p4a else 'FAIL'} / "
         f"4b={'PASS' if p4b else 'FAIL'}")
    emit(f"  P5 读链路零 Authorization / URL 零凭据: "
         f"{'PASS' if not auth and not secret_urls else 'FAIL'}")
    emit(f"  P6 无绕过 _open: {'PASS' if not bypass else 'FAIL'}")
    emit()
    emit("  【独立判断】「一次性消费」是否为测试凑绿：")
    emit("  Round 2 历史判定：**是**。当时 `_write_state()` docstring 明写理由是")
    emit("  「QA harness 的 setUp 不重置进程内标记」，即为迁就测试隔离而裁剪生产语义；")
    emit("  而测试隔离本可（且现在已）由 harness 调 vs.reset_written_marker() 解决，")
    emit("  两者并不冲突，故「一次性是必要取舍」不成立。")
    if defect:
        emit("  → 本轮探针**仍证实**用户可见缺陷：TTL 内第 2 次刷新（API 不可达 +")
        emit("    CDN 旧缓存）又掉回空库。K1 未修好，Routing 应为 Engineer。")
        emit("  → 最小修法（不改任何验收断言）：`_write_state()` 去掉一次性消费，")
        emit("     TTL 内每次都允许回填；`_pick_index()` 保留 written/not has_api/")
        emit("     best[packs]==0/有候选 四门；QA 侧 setUp 调 reset_written_marker() 隔离。")
    else:
        emit("  → 本轮复跑：P1 / P3 由 ❌ 转 ✔，**K1 缺陷已消除**；历史判定维持不变")
        emit("    （一次性消费是缺陷，不是必要取舍），但当前实现已按 PRD 修正。")
        emit("  → P1 里 `PUT后=True / 刷新后=True` 是 K1 后的预期值（TTL 窗口标志不再")
        emit("    被消费归零），不是缺陷；team-lead 已批准工程师保留该符号。")
    emit(f"  P7 时间预算: 1000ms→{e1:.3f}s / 8000ms→{e2:.3f}s "
         f"{'PASS' if e1 < 4 and e2 < 12 else 'FAIL'}")
    emit("=" * 100)

    text = "\n".join(LINES) + "\n"
    (CHECK / "vehicle_index_round2_probes.txt").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
