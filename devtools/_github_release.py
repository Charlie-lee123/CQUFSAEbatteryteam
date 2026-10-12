# -*- coding: utf-8 -*-
"""创建 GitHub Release 并上传 exe（无 gh CLI，走 REST API + git 凭据）。

用法：python devtools/_github_release.py <tag> <exe路径> [说明.md]

- 令牌从 `git credential fill`（credential manager 已存的 GitHub 凭据）现取，
  **不打印、不落盘**。
- 网络统一走本机 Clash（127.0.0.1:7897）—— 与仓库 git 的 http.proxy 一致。
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.request

OWNER_REPO = "Charlie-lee123/CQUFSAEbatteryteam"
API = f"https://api.github.com/repos/{OWNER_REPO}"
UPLOAD = f"https://uploads.github.com/repos/{OWNER_REPO}/releases"
PROXIES = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}


def get_token() -> str:
    """从 git 凭据管理器现取 GitHub 令牌（等价于 push 时用的那把）。

    ⚠️ 必须像 push 一样**清空 helper 列表再显式给一个**：系统里配了两个
    credential.helper，裸 `git credential fill` 会弹 CredentialHelperSelector
    GUI 无限等待（本机踩过——发布脚本第一次就是挂在这儿的，不是在上传）。
    """
    out = subprocess.run(
        ["git", "-c", "credential.helper=",
         "-c", "credential.helper=D:/Git/mingw64/bin/git-credential-manager.exe",
         "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, check=True,
        timeout=60).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("没取到 GitHub 凭据（credential fill 无 password）")


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler(PROXIES))


def api(op, token, url, method="GET", data=None, headers=None, timeout=120):
    body = None
    head = {"Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "serialassistant-release"}
    if headers:
        head.update(headers)
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        head.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=head, method=method)
    try:
        with op.open(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        print(f"  ! {method} {url} -> HTTP {e.code}: {detail}")
        raise


def main() -> int:
    tag = sys.argv[1]
    exe = sys.argv[2]
    notes_path = sys.argv[3] if len(sys.argv) > 3 else None
    token = get_token()
    op = opener()

    size = os.path.getsize(exe)
    sha = hashlib.sha256()
    with open(exe, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    print(f"exe: {exe}  {size:,} B  sha256={sha.hexdigest()}")

    body_text = ""
    if notes_path and os.path.exists(notes_path):
        body_text = open(notes_path, encoding="utf-8").read()
        body_text = body_text.replace(
            "{SHA256}", f"`{sha.hexdigest()}`").replace("{SIZE}", f"{size:,}")

    # 1) 建 Release（已存在就复用 —— 422 already_exists 时转 GET by tag）
    try:
        status, rel = api(op, token, API + "/releases", method="POST",
                          data={"tag_name": tag, "name": tag, "body": body_text,
                                "draft": False, "prerelease": False})
    except urllib.error.HTTPError as e:
        if e.code != 422:
            raise
        print("Release 已存在，改用现有的")
        status, rel = api(op, token, API + f"/releases/tags/{tag}")
    if status not in (200, 201):
        print("创建失败:", status, rel)
        return 1
    rel_id = rel["id"]
    print(f"Release id={rel_id}  tag={rel.get('tag_name')}")

    # 2) 上传 exe（同名先删，保证是最新构建）
    status, assets = api(op, token, API + f"/releases/{rel_id}/assets")
    for asset in assets:
        if asset["name"] == "SerialAssistant.exe":
            api(op, token, API + f"/releases/assets/{asset['id']}", method="DELETE")
            print("已删除旧资产 SerialAssistant.exe")
    name = os.path.basename(exe)
    data = open(exe, "rb").read()
    status, asset = api(
        op, token,
        UPLOAD + f"/{rel_id}/assets?name={name}",
        method="POST", data=data,
        headers={"Content-Type": "application/octet-stream"},
        timeout=1800)
    if status not in (200, 201):
        print("上传失败:", status, asset)
        return 1
    print(f"上传完成: {asset['name']}  {asset['size']:,} B")
    print("下载地址:", asset["browser_download_url"])

    # 3) 验证「一键下载」徽章链路（releases/latest → 302 → 资产）
    check = urllib.request.Request(
        f"https://github.com/{OWNER_REPO}/releases/latest/download/{name}",
        method="HEAD",
        headers={"User-Agent": "serialassistant-release"})
    with opener().open(check, timeout=120) as resp:
        print("latest 链接:", resp.status, resp.url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
