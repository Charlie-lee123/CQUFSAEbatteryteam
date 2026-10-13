# -*- coding: utf-8 -*-
r"""创建 GitHub Release 并上传产物（无 gh CLI，走 REST API + git 凭据）。

用法：
    python devtools\_github_release.py <tag> <exe路径> [说明.md] [setup路径]

- exe 是免安装单文件版（老用户静默替换更新走它）。
- setup 是 Inno 安装包（第 5 项，安装版用户走它；可给第 4 个参数，
  没有就只发布 exe）。
- 上传完打印两件产物的 SHA256 —— **把它们写回 version.py 的
  LATEST_EXE_SHA256 / LATEST_SETUP_SHA256**，客户端据此校验下载内容。
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


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    setup = sys.argv[4] if len(sys.argv) > 4 else None
    token = get_token()
    op = opener()

    assets_to_upload = [(exe, os.path.basename(exe))]
    if setup:
        assets_to_upload.append((setup, os.path.basename(setup)))

    exe_size = os.path.getsize(exe)
    exe_sha = sha256_of(exe)
    print(f"exe: {exe}  {exe_size:,} B  sha256={exe_sha}")
    setup_info = None
    if setup:
        setup_size = os.path.getsize(setup)
        setup_sha = sha256_of(setup)
        setup_info = (setup_size, setup_sha)
        print(f"setup: {setup}  {setup_size:,} B  sha256={setup_sha}")

    body_text = ""
    if notes_path and os.path.exists(notes_path):
        body_text = open(notes_path, encoding="utf-8").read()
        body_text = body_text.replace(
            "{SHA256}", f"`{exe_sha}`").replace("{SIZE}", f"{exe_size:,}")
        if setup_info:
            body_text = body_text.replace(
                "{SETUP_SHA256}", f"`{setup_info[1]}`").replace(
                "{SETUP_SIZE}", f"{setup_info[0]:,}")

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

    # 2) 逐个上传产物（同名先删，保证是最新构建）
    status, existing = api(op, token, API + f"/releases/{rel_id}/assets")
    for path, name in assets_to_upload:
        for asset in existing:
            if asset["name"] == name:
                api(op, token, API + f"/releases/assets/{asset['id']}",
                    method="DELETE")
                print(f"已删除旧资产 {name}")
        data = open(path, "rb").read()
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
    for path, name in assets_to_upload:
        check = urllib.request.Request(
            f"https://github.com/{OWNER_REPO}/releases/latest/download/{name}",
            method="HEAD",
            headers={"User-Agent": "serialassistant-release"})
        with opener().open(check, timeout=120) as resp:
            print(f"latest 链接 [{name}]:", resp.status, resp.url)

    # 4) 回填提示：把这两行写进 version.py（客户端校验下载内容靠它们）
    print("\n记得把下面两行写回 version.py：")
    print(f'LATEST_EXE_SHA256 = "{exe_sha}"')
    if setup_info:
        print(f'LATEST_SETUP_SHA256 = "{setup_info[1]}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
