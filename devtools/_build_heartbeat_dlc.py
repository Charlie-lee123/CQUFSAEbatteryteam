# -*- coding: utf-8 -*-
"""把 heartbeatDLC/ 打成可分发的 ZIP + SHA256（**只写本地，不上传 GitHub**）。

    .venv/Scripts/python.exe devtools/_build_heartbeat_dlc.py

产物：
    发布/heartbeatDLC-<version>.zip
    发布/heartbeatDLC-<version>.zip.sha256

ZIP 里根目录固定是 ``heartbeatDLC/``，和 dlc_host.install_zip 的目录名/id 校验对上。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "heartbeatDLC"
OUT_DIR = ROOT / "发布"
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".tmp", ".log"}


def main() -> int:
    manifest_path = SOURCE / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    plugin_id = manifest["id"]
    version = manifest["version"]
    if plugin_id != SOURCE.name:
        raise SystemExit(f"目录名 {SOURCE.name} 与清单 id {plugin_id} 不一致")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{plugin_id}-{version}.zip"

    files = [p for p in sorted(SOURCE.rglob("*"))
             if p.is_file()
             and not any(part in SKIP_DIRS for part in p.parts)
             and p.suffix.lower() not in SKIP_SUFFIXES]
    if not any(p.name == "manifest.json" for p in files):
        raise SystemExit("缺少 manifest.json")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            arcname = f"{plugin_id}/{path.relative_to(SOURCE).as_posix()}"
            zf.write(path, arcname)

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (OUT_DIR / f"{out.name}.sha256").write_text(
        f"{digest}  {out.name}\n", "utf-8")
    print(f"DLC 打包完成：{out}")
    print(f"  {len(files)} 个文件，{out.stat().st_size} 字节")
    print(f"  SHA256 {digest}")
    print(f"  校验文件 {OUT_DIR / (out.name + '.sha256')}")
    print("  ⚠️ 只生成了本地文件，未上传到 GitHub（发布需维护者明确授权）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
