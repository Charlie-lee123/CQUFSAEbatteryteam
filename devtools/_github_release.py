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
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

OWNER_REPO = "Charlie-lee123/CQUFSAEbatteryteam"
API = f"https://api.github.com/repos/{OWNER_REPO}"
UPLOAD = f"https://uploads.github.com/repos/{OWNER_REPO}/releases"

# 代理候选：环境变量给的（WorkBuddy 内部代理 / 系统代理）→ 本机 Clash → 直连。
# ⛔ 以前这里把 127.0.0.1:7897 写死，Clash 没开时 urllib 直接 ConnectionRefused，
#    报错完全看不出是代理的问题（2026-10-18 发 2.2 时踩到）。现在逐个探活，
#    全不通就用空 ProxyHandler 直连。
_PROXY_CANDIDATES = (
    os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "",
    "http://127.0.0.1:7897",
    "http://127.0.0.1:60199",
)
_PROXY_CHOSEN: list[str] = []


def _proxy_url() -> str:
    """探活挑一个能连上的代理；都连不上返回空串（= 直连）。"""
    for url in _PROXY_CANDIDATES:
        if not url:
            continue
        try:
            host, port = url.split("//", 1)[-1].rstrip("/").split(":")
            with socket.create_connection((host, int(port)), timeout=1.5):
                return url
        except (OSError, ValueError):
            continue
    return ""


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def credential_helpers() -> list[str]:
    """凭据助手候选路径，按可用性挑第一个。

    ⚠️ 这里以前**硬编码**了 ``D:/Git/mingw64/bin/git-credential-manager.exe`` ——
    那台机器上确实在那儿，但换一份 Git（比如 WorkBuddy 自带的 PortableGit）就
    失效，而且失效时只报 ``git credential fill`` 退出码 128，完全看不出是路径
    问题（2026-10-15 发 v2.0 时踩到）。现在按顺序找：
      1. 当前 git 同目录下的 git-credential-manager.exe（最稳，跟着 git 走）；
      2. PATH 上的 git-credential-manager / -core；
      3. 裸名 "manager"（交给 git 自己解析，兜底）。
    """
    found: list[str] = []
    git_exe = shutil.which("git")
    if git_exe:
        beside = os.path.join(os.path.dirname(git_exe), "git-credential-manager.exe")
        if os.path.exists(beside):
            found.append(beside)
    for name in ("git-credential-manager", "git-credential-manager-core", "manager"):
        path = shutil.which(name)
        if path:
            found.append(path)
    found.append("manager")
    return found


def get_token() -> str:
    """从 git 凭据管理器现取 GitHub 令牌（等价于 push 时用的那把）。

    ⚠️ 必须像 push 一样**清空 helper 列表再显式给一个**：系统里配了两个
    credential.helper，裸 `git credential fill` 会弹 CredentialHelperSelector
    GUI 无限等待（本机踩过——发布脚本第一次就是挂在这儿，不是在上传）。
    助手路径由 :func:`credential_helpers` 现找，别再写死。
    """
    last = None
    for helper in credential_helpers():
        try:
            out = subprocess.run(
                ["git", "-c", "credential.helper=",
                 "-c", f"credential.helper={helper}",
                 "credential", "fill"],
                input="protocol=https\nhost=github.com\n\n",
                capture_output=True, text=True, check=True,
                timeout=60).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            last = exc
            print(f"  · 凭据助手 {helper} 不可用，换下一个")
            continue
        for line in out.splitlines():
            if line.startswith("password="):
                print(f"  · 凭据来自 {helper}")
                return line.split("=", 1)[1].strip()
    raise SystemExit(f"没取到 GitHub 凭据（试过的助手见上）: {last}")


def opener():
    if not _PROXY_CHOSEN:
        _PROXY_CHOSEN.append(_proxy_url())
        print(f"  · 网络出口：{_PROXY_CHOSEN[0] or '直连'}")
    url = _PROXY_CHOSEN[0]
    mapping = {"http": url, "https": url} if url else {}
    return urllib.request.build_opener(urllib.request.ProxyHandler(mapping))


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


def with_retry(op, token, url, *, method="GET", data=None, headers=None,
               timeout=120, what="请求", retries=5):
    """带重试的 API 调用。

    ⚠️ 本机 Clash 对 github.com / api.github.com 会**间歇性** TLS 掐断
    （``SSL: UNEXPECTED_EOF_WHILE_READING``，几分钟内时好时坏 —— 2026-10-15 发
    v2.0 时反复踩到）。以前只有上传那一步有重试，**列资产、删旧资产**这些
    小请求断在没保护的地方就整条流程挂掉，而且看不出到底传到哪一步了。
    所以统一走这里：所有请求都能重试，失败信息带上是哪一步。
    """
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return api(op, token, url, method=method, data=data,
                       headers=headers, timeout=timeout)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500:
                raise                      # 4xx 是业务错误（已存在/没权限），重试没意义
            last = exc
            print(f"  ! {what} 第 {attempt}/{retries} 次失败：{exc}")
            time.sleep(2 * attempt)
    raise SystemExit(f"{what} 失败（已重试 {retries} 次）：{last}")


def upload_asset(op, token, rel_id: int, path: str, name: str, size: int,
                 retries: int = 4) -> dict:
    """上传一个资产，带重试。

    ⚠️ 文件是**读进内存一次性 POST** 的，断了就得从头来；而大文件走本机 Clash
    代理时会偶发 ``SSL: UNEXPECTED_EOF_WHILE_READING``（2026-10-15 发 v2.0 时
    连挂两次，都是上传到一半被掐）。所以：失败退避重试，重试前先把可能残留的
    同名半截资产删掉。
    """
    data = open(path, "rb").read()
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        status, assets = with_retry(op, token, API + f"/releases/{rel_id}/assets",
                                    what=f"列资产（上传 {name} 前）")
        for asset in assets:
            if asset["name"] == name:
                with_retry(op, token, API + f"/releases/assets/{asset['id']}",
                           method="DELETE", what=f"删旧资产 {name}")
        try:
            status, asset = api(
                op, token,
                UPLOAD + f"/{rel_id}/assets?name={name}",
                method="POST", data=data,
                headers={"Content-Type": "application/octet-stream"},
                timeout=1800)
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            print(f"  ! 第 {attempt}/{retries} 次上传 {name} 中断：{exc}")
            time.sleep(3 * attempt)
            continue
        if status in (200, 201):
            return asset
        print(f"  ! 上传返回 {status}: {asset}")
        last = RuntimeError(str(asset))
        time.sleep(3 * attempt)
    raise SystemExit(f"上传 {name} 失败（已重试 {retries} 次）：{last}")


def main() -> int:
    tag = sys.argv[1]
    exe = sys.argv[2]
    notes_path = sys.argv[3] if len(sys.argv) > 3 else None
    setup = sys.argv[4] if len(sys.argv) > 4 else None
    # --force：即使线上已有同名同尺寸资产也重传（默认跳过，省得每次发版都重传 100MB）
    force = "--force" in sys.argv
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
        status, rel = with_retry(
            op, token, API + "/releases", method="POST",
            data={"tag_name": tag, "name": tag, "body": body_text,
                  "draft": False, "prerelease": False},
            what="创建 Release", retries=3)
    except urllib.error.HTTPError as e:
        if e.code != 422:
            raise
        print("Release 已存在，改用现有的")
        status, rel = with_retry(op, token, API + f"/releases/tags/{tag}",
                                 what="读取已有 Release")
        # 复用时把说明也刷新成这次的（不然改了 notes 永远不会出现在页面上）
        if rel.get("body") != body_text:
            with_retry(op, token, API + f"/releases/{rel['id']}", method="PATCH",
                       data={"body": body_text}, what="更新 Release 说明")
    if status not in (200, 201):
        print("创建失败:", status, rel)
        return 1
    rel_id = rel["id"]
    print(f"Release id={rel_id}  tag={rel.get('tag_name')}")

    # 2) 逐个上传产物（同名同尺寸的默认跳过；否则先删同名再传，保证是最新构建）
    status, existing = with_retry(op, token, API + f"/releases/{rel_id}/assets",
                                  what="列资产")
    for path, name in assets_to_upload:
        size = os.path.getsize(path)
        if not force and any(a["name"] == name and a["size"] == size
                             and a.get("state") == "uploaded" for a in existing):
            print(f"已是最新构建，跳过上传: {name}（{size:,} B）")
            continue
        asset = upload_asset(op, token, rel_id, path, name, size)
        print(f"上传完成: {asset['name']}  {asset['size']:,} B")
        print("下载地址:", asset["browser_download_url"])

    # 3) 验证：先按**接口**核对（这才是判据），再尽力试一下「一键下载」链路。
    #    ⚠️ 那条链路会 302 到 objects.githubusercontent.com —— 本机 Clash 对它走
    #    直连、经常 TLS 被掐（SSL: UNEXPECTED_EOF...），所以**失败只告警不中断**：
    #    以前它一断整个脚本就崩，明明资产已经传完了（2026-10-15 发 v2.0 踩到）。
    status, latest = with_retry(op, token, API + "/releases/latest", what="核对 latest")
    print(f"latest 指向: {latest.get('tag_name')}（期望 {tag}）")
    if latest.get("tag_name") != tag:
        print(f"  ! 警告：latest 不是 {tag}，检查是否还有更新的非草稿发布")
    status, final = with_retry(op, token, API + f"/releases/{rel_id}/assets",
                               what="核对资产")
    ok = True
    for path, name in assets_to_upload:
        size = os.path.getsize(path)
        hit = [a for a in final if a["name"] == name and a["size"] == size]
        print(f"  资产核对 {name}: {'✓ ' + format(size, ',') + ' B' if hit else '✗ 缺失或尺寸不符'}")
        ok = ok and bool(hit)
    for path, name in assets_to_upload:
        check = urllib.request.Request(
            f"https://github.com/{OWNER_REPO}/releases/latest/download/{name}",
            method="HEAD",
            headers={"User-Agent": "serialassistant-release"})
        try:
            with opener().open(check, timeout=60) as resp:
                print(f"latest 下载链路 [{name}]:", resp.status)
        except Exception as exc:                      # noqa: BLE001
            print(f"  ! latest 下载链路 [{name}] 暂时失败（{type(exc).__name__}）——"
                  f"本机代理对 objects.githubusercontent.com 不稳定，浏览器里能下就没事")

    # 4) 回填提示：把这两行写进 version.py（客户端校验下载内容靠它们）
    print("\n记得把下面两行写回 version.py：")
    print(f'LATEST_EXE_SHA256 = "{exe_sha}"')
    if setup_info:
        print(f'LATEST_SETUP_SHA256 = "{setup_info[1]}"')
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
