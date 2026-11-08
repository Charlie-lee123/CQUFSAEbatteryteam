# -*- coding: utf-8 -*-
"""离线自测探针：vehicle_share（车辆档案分享）+ rx_history（历史目录/标签）。

跑法（项目根目录）::

    .venv\\Scripts\\python.exe devtools\\_probe_vehicle_share.py

⛔ **绝不污染用户真实档案**：开跑前给 QSettings 的
``custom/vehicles`` / ``custom/vehicle`` / ``custom/rx_history_dir`` 拍快照，
结束时（finally）逐键还原（原来没有这个键就 remove）。历史目录全程指向临时目录。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QSettings   # noqa: E402

APP = QCoreApplication.instance() or QCoreApplication([])

import customization        # noqa: E402
import rx_history           # noqa: E402
import vehicle_share        # noqa: E402

ORG, APP_NAME = "MimoWorks", "SerialAssistant"
SNAPSHOT_KEYS = ("custom/vehicles", "custom/vehicle", "custom/rx_history_dir",
                 "custom/gh_token_enc")
OUT = ROOT / "_check" / "vehicle_share_probe.txt"

_log: list[str] = []
_failures: list[str] = []


def log(text: str = "") -> None:
    print(text)
    _log.append(text)


def check(name: str, ok: bool, detail: str = "") -> bool:
    if ok:
        log(f"[PASS] {name}" + (f" —— {detail}" if detail else ""))
    else:
        log(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))
        _failures.append(name)
    return ok


# ---------------------------------------------------------------- 快照 / 还原

def settings_handle() -> QSettings:
    return QSettings(ORG, APP_NAME)


def snapshot() -> dict:
    handle = settings_handle()
    handle.sync()
    return {key: (handle.contains(key), handle.value(key, None))
            for key in SNAPSHOT_KEYS}


def restore(snap: dict) -> None:
    handle = settings_handle()
    for key, (had, value) in snap.items():
        if had:
            handle.setValue(key, value)
        else:
            handle.remove(key)
    handle.sync()


def write_store(items: list[dict], current: str = "") -> None:
    """直接写档案存储（模拟"用户原本有哪些车"）。"""
    handle = settings_handle()
    handle.setValue("custom/vehicles",
                    json.dumps({"current": current, "items": items},
                               ensure_ascii=False))
    handle.setValue("custom/vehicle", current)
    handle.sync()


# ---------------------------------------------------------------- 假 HTTP 层
# 上传/索引路径**没有真 token 无法联网验证**，所以在进程内把
# ``vehicle_share._open`` 换成按 URL 片段分派的假实现，把分支走一遍。

class _FakeReply:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class _FakeHTTP:
    """``routes``：``[(url 片段, method, status, body)]``，按顺序匹配第一条。"""

    def __init__(self, routes) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, bytes | None]] = []

    def __call__(self, url, timeout, data=None, headers=None, method=None):
        method = method or "GET"
        self.calls.append((method, url, data))
        for fragment, want, status, body in self.routes:
            if fragment in url and want == method:
                if status >= 400:
                    raise urllib.error.HTTPError(url, status, "fake", None, None)
                raw = (body if isinstance(body, (bytes, bytearray))
                       else json.dumps(body, ensure_ascii=False).encode("utf-8"))
                return _FakeReply(bytes(raw))
        raise urllib.error.HTTPError(url, 404, "fake-not-found", None, None)

    def put_bodies(self) -> list[dict]:
        out = []
        for method, _url, data in self.calls:
            if method == "PUT" and data:
                out.append(json.loads(data.decode("utf-8")))
        return out


# ---------------------------------------------------------------- 主流程

def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="cqv_share_"))
    snap = snapshot()
    log("=" * 70)
    log("vehicle_share / rx_history 探针")
    log(f"临时目录：{temp}")
    log("=" * 70)
    try:
        _run(temp)
    finally:
        try:
            rx_history.set_directory("")          # 先把自定义目录清掉，别留脏配置
            rx_history._timer_stop()
        except Exception as exc:                  # noqa: BLE001
            log(f"[WARN] 还原历史目录失败：{exc}")
        restore(snap)
        after = snapshot()
        log(f"档案快照还原核对：{'一致' if after == snap else '不一致！'}")
        if after != snap:
            _failures.append("settings-restored")
        rx_history._timer_stop()

    log("")
    log("=" * 70)
    if _failures:
        log(f"结果：{len(_failures)} 项失败 → {', '.join(_failures)}")
    else:
        log("结果：全部通过")
    log("=" * 70)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(_log) + "\n", encoding="utf-8")
    log(f"输出已写入：{OUT}")
    return 1 if _failures else 0


def _run(temp: Path) -> None:
    # ---- 1. 造 2 台车 → 导出 → 清库 → 导入 ---------------------------------
    write_store([])
    customization.save_vehicle("E74-2026", [25, 25, 25], [8, 8, 8])
    customization.save_vehicle("E74-2025", [24, 24], [6, 6])
    check("造 2 台车", sorted(customization.vehicle_names()) == ["E74-2025", "E74-2026"],
          str(customization.vehicle_names()))

    pack = temp / "pack.cqv.json"
    count = vehicle_share.export_to_file(str(pack))
    check("export_to_file 返回车辆数", count == 2, f"count={count}")
    raw = pack.read_bytes()
    check("导出无 BOM", not raw.startswith(b"\xef\xbb\xbf"), raw[:6].hex())
    doc = json.loads(raw.decode("utf-8"))
    check("导出格式", doc.get("schema") == 1 and doc.get("app") == "CQUFSAE串口助手"
          and doc.get("exported_at"), json.dumps(doc, ensure_ascii=False)[:160])
    check("导出不含多余字段",
          set(doc["vehicles"][0]) == {"name", "cells", "temps"},
          str(sorted(doc["vehicles"][0])))

    write_store([])                                # 清库，模拟"换台电脑导入"
    result = vehicle_share.import_from_file(str(pack))
    log(f"import_from_file → {result}")
    check("导入 2 台新车", result["added"] == 2 and result["updated"] == 0,
          str(result))
    check("导入后档案正确",
          sorted(customization.vehicle_names()) == ["E74-2025", "E74-2026"],
          str(customization.vehicle_names()))

    # ---- 2. 同名 = 更新 ---------------------------------------------------
    single = temp / "single.cqv.json"
    single.write_text(json.dumps({
        "schema": 1, "app": "CQUFSAE串口助手",
        "exported_at": "2026-10-31T11:30:00",
        "vehicles": [{"name": "E74-2026", "cells": [30, 30], "temps": [9, 9]}],
    }, ensure_ascii=False), encoding="utf-8")
    result = vehicle_share.import_from_file(str(single))
    log(f"同名覆盖 → {result}")
    check("同名 = updated 1", result["updated"] == 1 and result["added"] == 0,
          str(result))
    saved = {item["name"]: item for item in customization.vehicles()}
    check("覆盖后结构变了", saved["E74-2026"]["cells"] == [30, 30],
          str(saved["E74-2026"]))
    check("别的车没被动", sorted(saved) == ["E74-2025", "E74-2026"], str(sorted(saved)))

    # ---- 3. 坏数据：不抛异常、errors 非空 --------------------------------
    bad_cases = {
        "缺 schema": {"vehicles": [{"name": "T1", "cells": [25, 25], "temps": [8, 8]}]},
        "cells/temps 不等长": {"schema": 1,
                            "vehicles": [{"name": "T2", "cells": [25, 25, 25],
                                          "temps": [8]}]},
        "schema 更新": {"schema": 99,
                       "vehicles": [{"name": "T3", "cells": [10], "temps": [1]}]},
        "顶层不是对象": [1, 2, 3],
        "没有车辆列表": {"schema": 1, "vehicles": "nope"},
        "坏条目": {"schema": 1, "vehicles": ["x", {"name": ""}, {"name": "T4"}]},
    }
    for label, payload in bad_cases.items():
        try:
            got = vehicle_share.import_payload(payload)
            ok = isinstance(got, dict) and bool(got["errors"])
        except Exception as exc:                  # noqa: BLE001
            got, ok = f"抛异常 {exc!r}", False
        log(f"坏数据「{label}」→ {got}")
        check(f"坏数据「{label}」不抛异常且 errors 非空", ok)
    check("截断生效（T2 只剩 1 个模组）",
          {i["name"]: i for i in customization.vehicles()}.get("T2", {}).get("cells")
          == [25],
          str({i["name"]: i["cells"] for i in customization.vehicles()}))

    empty = temp / "empty.cqv.json"
    empty.write_text("", encoding="utf-8")
    got = vehicle_share.import_from_file(str(empty))
    log(f"空文件 → {got}")
    check("空文件 errors 非空", bool(got["errors"]), str(got))

    garbage = temp / "garbage.cqv.json"
    garbage.write_text("{ not json", encoding="utf-8")
    got = vehicle_share.import_from_file(str(garbage))
    log(f"坏 JSON → {got}")
    check("坏 JSON errors 非空", bool(got["errors"]), str(got))

    got = vehicle_share.import_from_file(str(temp / "missing.cqv.json"))
    log(f"文件不存在 → {got}")
    check("文件不存在 errors 非空", bool(got["errors"]), str(got))

    # ---- 4. token 安全存储（DPAPI） ---------------------------------------
    plain = "ghp_PROBE_测试_7f3a9c"
    saved = vehicle_share.save_token(plain)
    log(f"save_token → {saved}，LAST_TOKEN_ERROR={vehicle_share.LAST_TOKEN_ERROR!r}")
    handle = settings_handle()
    handle.sync()
    cipher = str(handle.value(vehicle_share.TOKEN_KEY, "") or "")
    log(f"配置里的密文：{cipher[:36]}…（{len(cipher)} 字符）")
    check("save_token 返回 True（DPAPI 生效）", saved is True)
    check("密文非空", bool(cipher), f"{len(cipher)} 字符")
    check("密文 != 明文", cipher != plain and plain not in cipher, cipher[:24])
    check("密文形态像 DPAPI blob（base64 解出的开头是 01 00 00 00）",
          base64.b64decode(cipher or "").startswith(b"\x01\x00\x00\x00"),
          cipher[:12])
    check("load_token 解回原文", vehicle_share.load_token() == plain,
          repr(vehicle_share.load_token()))
    check("解密无错误", vehicle_share.LAST_TOKEN_ERROR == "",
          vehicle_share.LAST_TOKEN_ERROR)
    vehicle_share.clear_token()
    handle.sync()
    check("clear_token 后读不到",
          vehicle_share.load_token() == "" and not handle.contains(vehicle_share.TOKEN_KEY),
          repr(vehicle_share.load_token()))
    check("空串 save_token = 删除",
          vehicle_share.save_token("") is True and vehicle_share.load_token() == "")

    # DPAPI 不可用时的降级：把 _dpapi 换成必抛异常，断言"只存内存 + 返回 False"
    original_dpapi = vehicle_share._dpapi

    def _boom(*_args, **_kwargs):
        raise OSError(1234, "假装的 CryptProtectData 失败")

    try:
        vehicle_share._dpapi = _boom
        check("DPAPI 不可用 → save_token False", vehicle_share.save_token("tok-mem") is False)
        check("降级时给出可读说明", "本次运行" in vehicle_share.LAST_TOKEN_ERROR,
              vehicle_share.LAST_TOKEN_ERROR)
        check("降级后仍在内存里可读", vehicle_share.load_token() == "tok-mem",
              repr(vehicle_share.load_token()))
    finally:
        vehicle_share._dpapi = original_dpapi
        vehicle_share.clear_token()

    # ---- 5. _merge_index 纯函数（同名替换 / 去重 / 缺 schema） ------------
    pack = {"name": "B", "file": "B.cqv.json", "modules": 5, "note": "新"}
    merged = vehicle_share._merge_index(
        {"schema": 1, "updated_at": "2026-01-01T00:00:00",
         "packs": [{"name": "A", "file": "A.cqv.json", "modules": 3, "note": ""},
                   {"name": "B", "file": "B-old.cqv.json", "modules": 9, "note": "旧"}]},
        pack)
    log(f"_merge_index（同名替换）→ {json.dumps(merged, ensure_ascii=False)}")
    check("同名就地替换、位置不变",
          [p["name"] for p in merged["packs"]] == ["A", "B"]
          and merged["packs"][1]["modules"] == 5
          and merged["packs"][1]["file"] == "B.cqv.json", str(merged["packs"]))
    check("updated_at 刷新", merged["updated_at"] != "2026-01-01T00:00:00",
          merged["updated_at"])
    check("schema 保留", merged["schema"] == 1, str(merged["schema"]))

    merged = vehicle_share._merge_index(
        {"schema": 1, "packs": [{"name": "B", "file": "b1"}, {"name": "B", "file": "b2"},
                                {"name": "C", "file": "C.cqv.json"}]},
        pack)
    log(f"_merge_index（老索引里有重名）→ {[p['name'] for p in merged['packs']]}")
    check("老索引重名去重 + 新包仍只一条",
          [p["name"] for p in merged["packs"]] == ["B", "C"],
          str(merged["packs"]))

    merged = vehicle_share._merge_index(None, pack)
    log(f"_merge_index（索引不存在）→ {json.dumps(merged, ensure_ascii=False)}")
    check("缺 schema 用空壳",
          merged["schema"] == 1 and len(merged["packs"]) == 1
          and bool(merged["updated_at"]), str(merged))
    merged = vehicle_share._merge_index({"packs": "坏掉"}, pack)
    check("坏 packs 当空索引", [p["name"] for p in merged["packs"]] == ["B"],
          str(merged["packs"]))

    # ---- 6. 上传分支（假 HTTP 层，**未联网**） ---------------------------
    original_open = vehicle_share._open
    doc = vehicle_share.export_payload(["E74-2026"])
    pack_url_part = "contents/vehicles/E74-2026.cqv.json"
    index_url_part = "contents/vehicles/index.json"
    try:
        # 6a. 同名已存在 + overwrite=False → need_confirm，且**一个 PUT 都不发**
        fake = _FakeHTTP([(pack_url_part, "GET", 200, {"sha": "abc123", "content": ""})])
        vehicle_share._open = fake
        got = vehicle_share.upload_pack(doc, "fake-token")
        log(f"upload_pack(overwrite=False, 文件已存在) → {got}")
        check("同名且未确认 → need_confirm",
              got["need_confirm"] is True and got["ok"] is False, str(got))
        check("need_confirm 带文件名与网页地址",
              got["file"].endswith(".cqv.json") and got["url"].startswith("https://github.com/"),
              f"{got['file']} {got['url']}")
        check("need_confirm 时没有发 PUT",
              all(method == "GET" for method, _u, _d in fake.calls),
              str([(m, u.split('/')[-1]) for m, u, _d in fake.calls]))

        # 6b. overwrite=True → 带 sha 覆盖 + 自动登记索引（索引 404 → 空壳）
        fake = _FakeHTTP([
            (pack_url_part, "GET", 200, {"sha": "abc123", "content": ""}),
            (pack_url_part, "PUT", 200, {"content": {"html_url": "https://github.com/x/blob/main/v/E74-2026.cqv.json"}}),
            (index_url_part, "PUT", 200, {"content": {"html_url": "https://github.com/x/blob/main/v/index.json"}}),
        ])
        vehicle_share._open = fake
        got = vehicle_share.upload_pack(doc, "fake-token", overwrite=True)
        log(f"upload_pack(overwrite=True) → ok={got['ok']} file={got['file']} "
            f"index={got['index']}")
        check("覆盖上传成功", got["ok"] is True and got["error"] == "", str(got))
        bodies = fake.put_bodies()
        check("覆盖时带了 sha", len(bodies) >= 1 and bodies[0].get("sha") == "abc123",
              str(bodies[0].get("sha") if bodies else None))
        check("索引自动登记（index.ok）",
              isinstance(got["index"], dict) and got["index"]["ok"] is True,
              str(got["index"]))
        idx_doc = None
        for body in bodies:
            if body.get("message") == "更新车辆索引":
                idx_doc = json.loads(base64.b64decode(body["content"]).decode("utf-8"))
                check("索引文件 branch=main", body.get("branch") == "main",
                      str(body.get("branch")))
        check("索引 PUT 内容是合法索引", isinstance(idx_doc, dict)
              and idx_doc["packs"][0]["file"] == "E74-2026.cqv.json",
              str(idx_doc))

        # 6c. 索引写入失败不影响"包已上传"的结论
        fake = _FakeHTTP([
            (pack_url_part, "PUT", 200, {"content": {"html_url": "https://github.com/x/p"}}),
            (index_url_part, "PUT", 403, {"message": "no permission"}),
        ])
        vehicle_share._open = fake
        got = vehicle_share.upload_pack(doc, "fake-token")
        log(f"upload_pack(索引 403) → ok={got['ok']} index={got['index']}")
        check("索引失败但包上传仍算成功",
              got["ok"] is True and got["index"]["ok"] is False
              and "403" in got["index"]["error"], str(got["index"]))
    finally:
        vehicle_share._open = original_open

    # ---- 7. 历史目录 / 标签 ------------------------------------------------
    original_dir = rx_history.custom_directory()
    log(f"原自定义历史目录：{original_dir!r}")
    log(f"默认历史目录：{rx_history.directory()!r}")
    check("默认目录非空", bool(rx_history.directory()))

    hist = temp / "hist"
    check("set_directory 正常目录", rx_history.set_directory(str(hist)) is True,
          str(hist))
    check("directory() 跟着切", Path(rx_history.directory() or "") == hist,
          str(rx_history.directory()))
    check("path() 仍是「当前车号那个文件」",
          (rx_history.path() or "").endswith(rx_history.FILE_NAME),
          str(rx_history.path()))

    (hist / "接收历史.log").write_text("默认档\n", encoding="utf-8")
    old = hist / "接收历史-E74-2026.log"
    old.write_text("老车档\n", encoding="utf-8")
    new = hist / "接收历史-E74-2025.log"
    new.write_text("新车档\n", encoding="utf-8")
    stamp = time.time()
    os.utime(old, (stamp - 100, stamp - 100))
    os.utime(new, (stamp, stamp))
    labels = rx_history.available_labels()
    log(f"available_labels → {labels}")
    names = [name for name, _p in labels]
    check("标签解析（含「默认」）", sorted(names) == ["E74-2025", "E74-2026", "默认"],
          str(names))
    check("新的在前（mtime 倒序）", names[0] == "E74-2025", str(names))
    check("path_for_label 命中", rx_history.path_for_label("E74-2026")
          == str(old.resolve()), str(rx_history.path_for_label("E74-2026")))
    check("path_for_label 未命中 → None",
          rx_history.path_for_label("不存在") is None)

    blocker = temp / "blocker"
    blocker.write_text("我是文件不是目录", encoding="utf-8")
    check("set_directory 不可写目录 → False",
          rx_history.set_directory(str(blocker / "sub")) is False)
    check("失败后目录没变", Path(rx_history.directory() or "") == hist,
          str(rx_history.directory()))

    # 自测会替换 _resolve_dir 做隔离：那时必须无视自定义目录（隔离优先）
    original_resolver = rx_history._resolve_dir
    patched = temp / "patched"
    try:
        rx_history._resolve_dir = lambda: patched
        check("自测替换 _resolve_dir 时隔离优先",
              Path(rx_history.directory() or "") == patched,
              str(rx_history.directory()))
    finally:
        rx_history._resolve_dir = original_resolver

    # ---- 8. 在线索引（真联网） --------------------------------------------
    urls = vehicle_share.remote_index_urls()
    log(f"remote_index_urls（{len(urls)} 条）：")
    for index, url in enumerate(urls, start=1):
        log(f"  {index:2d}. {url}")
    check("源链数量 ≥ 2（镜像 + 直连）", len(urls) >= 2, str(len(urls)))
    check("末位是 GitHub raw 直连",
          urls[-1].startswith("https://raw.githubusercontent.com/"),
          urls[-1])

    log("正在实际联网拉索引（≤8s）…")
    started = time.monotonic()
    index_doc = vehicle_share.fetch_remote_index()
    spent = time.monotonic() - started
    log(f"fetch_remote_index → ok={index_doc.get('ok')} "
        f"source={index_doc.get('source')!r} error={index_doc.get('error')!r} "
        f"items={len(index_doc.get('items') or [])}")
    log(f"  耗时 {spent:.2f}s")
    check("fetch_remote_index 耗时 ≤ 9s", spent <= 9.0, f"{spent:.2f}s")
    if index_doc.get("ok"):
        # 仓库里已放好空索引（lead 建的）：可达 + packs 为空
        check("索引可达（ok=True）", True, f"source={index_doc.get('source')}")
        check("索引 source 非空", bool(index_doc.get("source")),
              repr(index_doc.get("source")))
        check("空索引 items == []（若已登记包请更新本断言）",
              index_doc.get("items") == [], str(index_doc.get("items")))
    else:
        check("索引失败但链路通（有明确 error）",
              bool(index_doc.get("error")), str(index_doc.get("error")))

    # 上传缺口：无 token 必须给出可读的 error（UI 要拿它引导用户）
    upload = vehicle_share.upload_pack(vehicle_share.export_payload(), "")
    log(f"upload_pack(无 token) → {upload}")
    check("无 token 上传返回明确 error",
          upload["ok"] is False and "token" in upload["error"].lower(), str(upload))
    log(f"upload_url_for_web() → {vehicle_share.upload_url_for_web()}")
    check("浏览器上传页",
          vehicle_share.upload_url_for_web().endswith("/upload/main/vehicles"),
          vehicle_share.upload_url_for_web())


if __name__ == "__main__":
    sys.exit(main())
