# -*- coding: utf-8 -*-
r"""检查更新与自动更新：多源版本检测、更新提示弹窗、下载校验与静默替换。

设计要点（用户 2026-10-12 点名）：
- 无代理/无法直连 GitHub 的网络也要能检测与下载 —— 版本与下载都走"源链"：
  jsDelivr CDN → ghfast/gh-proxy 镜像 → GitHub 直连（开着代理的用户走最后一
  级照样通），前一级失败自动回退到下一级，整条链再重试一轮。
- 完整性校验：仓库根目录的 version.py 里带 `LATEST_EXE_SHA256`（发布脚本在
  发布时写入），下载完成后先校验哈希再安装；拿不到哈希时退回大小比对。
- 静默更新：下载好的新 exe 以 `--apply-update <目标路径>` 自举——它等旧实例
  退出后用自己覆盖目标 exe 再重启（onefile exe 不能覆盖正在运行的自己，
  借"新进程复制自己"绕开；详见 main._run_apply_update）。
- 配置保留：所有配置都在注册表 QSettings（HKCU\Software\MimoWorks\...），
  日志在 ~/.serialassistant —— 更新只替换 exe 文件，配置天然不丢；语义变更
  走 customization.migrate_defaults() 的兼容迁移。
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from PySide6.QtCore import (
    QObject, QTimer, QUrl, Signal,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import (
    QNetworkAccessManager, QNetworkReply, QNetworkRequest,
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget,
)

import app_log
from ui_controls import InkButton
from ui_theme import GAP, ui_font

SOURCE_TIMEOUT_MS = 4000         # 版本检测**单源**超时（并行测速，不用等满）
CHECK_PASSES = 2                 # 整条源链的重试轮数
# 第一条有效结果回来后再等这么久，把同期到达的答案收集齐，取**版本号最高**的那条。
# 为什么要窗口（2026-10-25）：jsDelivr 的边沿缓存会滞后几分钟到几小时（发布后仍返回
# 上一版的 version.py），而它的响应往往最快 —— "先回者胜"就会拿到陈旧版本号，
# 用户点检查更新看到"已是最新"却拉不到包。窗口只有 300ms，代价远小于收益。
PICK_GRACE_MS = 300
# 代理策略层：系统代理 → 本机 Clash → 直连。全并行仍拿不到版本时逐层切，
# 每层只跑 CHECK_PASSES 轮；最坏 ≈ 3 × 4.8s，典型 1s 内。
CLASH_PROXY = ("127.0.0.1", 7897)
DOWNLOAD_PASSES = 1              # 下载源链重试轮数（源多了，一轮足够）
STALL_MS = 8000                  # 下载"多久没有任何新字节"就换源
# 同时抢跑的下载源数：2 条（内存峰值 ≈ 2×包体）。再大收益递减、峰值内存翻倍。
RACERS_PER_BATCH = 2
SLOW_BYTES_PER_S = 40_000        # 慢于这个速度且已下 >8s，也换源（镜像限速时用）



# ------------------------------------------------------------------ 镜像表
#
# GitHub 的 release 资产在国内直连基本拉不动，实际都走"前缀式"加速镜像：
#
#     https://<镜像>/https://github.com/<owner>/<repo>/releases/download/...
#
# 这些镜像**都有随时挂掉/限速的可能**，所以这里不是"选一条最靠谱"，而是：
#   ① 版本检测：**所有源并行发**，谁先回有效内容就用谁（顺手完成测速）；
#   ② 下载：用刚才测出来的最快镜像起跑，**卡住 8 秒没新字节就换下一条**。
# 多列几条的意义在于"总有一条活着"，而不是指望某一条永远在。
MIRROR_PREFIXES = (
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "https://hub.gitmirror.com/",
    "https://gitdl.cn/",
)

# 本次会话里测出来的最快镜像前缀（UpdateChecker 命中镜像源时更新）。
# 下载从它起跑；没有就按 MIRROR_PREFIXES 的默认顺序。
_BEST_PREFIX: str | None = None


def best_prefix() -> str | None:
    return _BEST_PREFIX


def note_best_prefix(prefix: str | None) -> None:
    """记下本次测速最快的镜像（供下载起跑用；空值忽略）。"""
    global _BEST_PREFIX
    if prefix:
        _BEST_PREFIX = prefix


def _OWNER_REPO() -> str:
    from version import OWNER_REPO
    return OWNER_REPO


def version_sources() -> tuple:
    """版本检测源（名字, url, 解析器, 镜像前缀）—— **并行**发，先回者胜。

    ① jsDelivr 三个边缘节点（国内可直连，仓库根目录的 version.py）
    ② 各镜像加速的 GitHub raw（前缀见 MIRROR_PREFIXES）
    ③ GitHub raw 直连（开着代理的用户）
    ④ GitHub API 标签列表（兜底；只有版本号、没有哈希）
    """
    repo = _OWNER_REPO()
    raw = f"https://raw.githubusercontent.com/{repo}/main/version.py"
    out = [
        ("jsDelivr", f"https://cdn.jsdelivr.net/gh/{repo}@main/version.py",
         parse_version_file, None),
        ("jsDelivr-fastly", f"https://fastly.jsdelivr.net/gh/{repo}@main/version.py",
         parse_version_file, None),
        ("jsDelivr-gcore", f"https://gcore.jsdelivr.net/gh/{repo}@main/version.py",
         parse_version_file, None),
    ]
    for prefix in MIRROR_PREFIXES:
        host = prefix.rstrip("/").split("//", 1)[-1]
        out.append((host, f"{prefix}{raw}", parse_version_file, prefix))
    out.append(("GitHub raw", raw, parse_version_file, None))
    out.append(("GitHub API", f"https://api.github.com/repos/{repo}/tags",
                parse_tags_json, None))
    return tuple(out)


def release_download_urls(tag: str, asset: str | None = None) -> tuple:
    """某版本资产的下载源链：**测速最快的镜像在前**，其余镜像随后，直连兜底。"""
    from version import ASSET_NAME
    asset = asset or ASSET_NAME
    repo = _OWNER_REPO()
    direct = f"https://github.com/{repo}/releases/download/{tag}/{asset}"
    prefixes = list(MIRROR_PREFIXES)
    best = best_prefix()
    if best in prefixes:
        prefixes.remove(best)
        prefixes.insert(0, best)
    return tuple([f"{p}{direct}" for p in prefixes] + [direct])


def setup_asset_name(version: str) -> str:
    """某版本安装包在 Release 里的资产名，如 ``SerialAssistant-setup-1.8.exe``。"""
    from version import SETUP_ASSET_PREFIX, SETUP_ASSET_SUFFIX
    return f"{SETUP_ASSET_PREFIX}{version}{SETUP_ASSET_SUFFIX}"


# 「应用和功能」里可能出现的显示名片段：改名前后 + 英文名。
# ⛔ 改名后新装的 DisplayName 是「CQUFSAE串口助手」，只认旧名会让安装版
#    把自己判成免安装版（更新时去静默替换 Program Files 里的 exe，必失败）。
INSTALL_DISPLAY_NAMES = ("串口助手", "串口调试助手", "SerialAssistant")   # 旧名兼容


def is_installed() -> bool:
    """当前是不是"安装版"（第 5 项：安装器接管更新的判据）。

    Inno Setup 会在安装目录写 `unins000.exe`，并在
    ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\<AppId>_is1``
    下登记 InstallLocation。装了安装版就走"下载 setup → 运行它 → 由安装向导
    接管覆盖"，否则（免安装单文件版）走原来的静默替换 exe。
    """
    import os
    import sys
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for name in ("unins000.exe", "unins001.exe"):
        if os.path.exists(os.path.join(exe_dir, name)):
            return True
    return False


def installed_location() -> str | None:
    """从注册表读安装版的安装目录（读不到返回 None）。"""
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key)
    except OSError:
        return None
    try:
        index = 0
        while True:
            try:
                sub = winreg.EnumKey(root, index)
            except OSError:
                return None
            index += 1
            try:
                with winreg.OpenKey(root, sub) as item:
                    name = _reg_value(item, "DisplayName") or ""
                    # ⛔ 三个名字都要认：改名后**新装**的 DisplayName 是
                    #    「CQUFSAE串口助手」（不含"调试助手"），只认旧名会让
                    #    安装版把自己判成免安装版 → 更新时去静默替换 Program Files
                    #    里的 exe，必然失败（2026-10-30 独立验收揪出来的）。
                    if any(key in name for key in INSTALL_DISPLAY_NAMES):
                        location = _reg_value(item, "InstallLocation")
                        if location:
                            return location
            except OSError:
                continue
    finally:
        root.Close()


def _reg_value(key, name: str) -> str | None:
    import winreg
    try:
        value, _kind = winreg.QueryValueEx(key, name)
        return str(value) if value else None
    except OSError:
        return None


def update_asset_and_hash(info: dict) -> tuple[str | None, str | None]:
    """按"是否安装版"挑这次要下的资产与对应哈希。

    安装版下安装包（``SerialAssistant-setup-x.y.exe``）、校验 ``LATEST_SETUP_SHA256``；
    免安装版下 ``SerialAssistant.exe``、校验 ``LATEST_EXE_SHA256``。
    返回 ``(asset, sha256)``，任何一项拿不到就回 None（由调用方退回默认）。
    """
    import version
    if is_installed():
        return (setup_asset_name(info.get("version", "")),
                info.get("setup_sha256") or None)
    return (version.ASSET_NAME, info.get("sha256") or None)


# ------------------------------------------------------------------ 解析器

def version_tuple(s: str) -> tuple:
    """版本号字符串 → 可比较的整数元组（"1.10" 排在 "1.9" 后面）。"""
    return tuple(int(p) for p in str(s).split(".") if p.isdigit())


def parse_version_file(text: str) -> dict | None:
    """仓库里 version.py 的内容 → {"version", "sha256", "setup_sha256"}。"""
    import re
    m = re.search(r'^VERSION\s*=\s*"(\d+(?:\.\d+)*)"', text, re.M)
    if not m:
        return None
    out = {"version": m.group(1)}
    m = re.search(r'^LATEST_EXE_SHA256\s*=\s*"([0-9a-fA-F]{64})"', text, re.M)
    if m:
        out["sha256"] = m.group(1).lower()
    m = re.search(r'^LATEST_SETUP_SHA256\s*=\s*"([0-9a-fA-F]{64})"', text, re.M)
    if m:
        out["setup_sha256"] = m.group(1).lower()
    return out


def parse_tags_json(text: str) -> dict | None:
    """GitHub tags API 的 JSON → {"version": "1.6"}（按数值取最大）。"""
    import json
    tags = json.loads(text)
    best = None
    for tag in tags:
        name = str(tag.get("name", "")).lstrip("v")
        parts = tuple(int(p) for p in name.split(".") if p.isdigit())
        if parts and (best is None or parts > best[0]):
            best = (parts, name)
    return {"version": best[1]} if best else None


# ------------------------------------------------------------------ 完整性

def verify_integrity(data: bytes, sha256: str | None = None,
                     size: int | None = None) -> tuple[bool, str]:
    """下载内容的完整性校验。返回 (通过, 说明)。"""
    if sha256:
        got = hashlib.sha256(data).hexdigest()
        if got != sha256.lower():
            return (False, f"哈希不符（期望 {sha256[:12]}…，实得 {got[:12]}…）")
        return (True, "SHA256 校验通过")
    if size:
        if len(data) != size:
            return (False, f"大小不符（期望 {size:,} B，实得 {len(data):,} B）")
        return (True, "大小校验通过")
    return (False, "服务端没有提供本版的校验值（可能正在发布），请稍后再试")


def parse_apply_update_args(argv) -> str | None:
    """main.py 的 --apply-update <目标exe路径>；没有则 None。"""
    if "--apply-update" not in argv:
        return None
    i = argv.index("--apply-update")
    if i + 1 >= len(argv):
        return None
    return argv[i + 1]


def spawn_apply_update(new_exe: str, target_exe: str) -> None:
    """让**新 exe**自举替换：它等旧实例退出后用自己覆盖目标并重启（见 main）。"""
    import subprocess
    subprocess.Popen([new_exe, "--apply-update", target_exe],
                     cwd=str(Path(new_exe).parent), close_fds=True)


# ------------------------------------------------------------------ 检测器

class UpdateChecker(QObject):
    """多源版本检测：**所有源并行发**，第一条回有效内容的胜出。

    为什么改成并行（2026-10-17，用户："自动更新太慢了"）：原来是"逐源串行 +
    每源 8 秒超时"，国内常见的坏情况是**某条镜像连得上但极慢**——用户要干等
    8 秒才轮到下一条，运气差时 20 秒才拿到版本号。并行之后：
      · 总耗时 ≈ **最快那条源**的耗时（通常 0.3~1.5s），慢的那几条不影响结果；
      · 顺手完成了"测速"——赢的那条就是当前网络下最快的镜像，
        ``note_best_prefix()`` 记下来，下载直接从它起跑；
      · 首轮全挂才会进第二轮（CHECK_PASSES），不会再出现"卡在一条源上"。

    结果信号 ``checked(info)``：info 含 ``version`` / ``sha256`` / ``source``；
    全部失败发 ``failed(reason)``。
    """

    checked = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._replies: list = []
        self._pass = 0
        self._layer = 0
        self._done = False
        self._candidates: list = []      # [(version_tuple, info, 到达序号)]
        self._seq = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        # 胜出后给镜像留的"报到窗口"（见 _on_one）：整体冠军可能来自 jsDelivr
        # 或直连，这时候还不知道哪条镜像最快 —— 但下载恰恰要用镜像。
        self._grace = QTimer(self)
        self._grace.setSingleShot(True)
        self._grace.timeout.connect(self._abort_all)
        # 结果选择窗口：收齐同期答案，取版本号最高的那条（见 PICK_GRACE_MS）
        self._pick = QTimer(self)
        self._pick.setSingleShot(True)
        self._pick.timeout.connect(self._emit_best)

    def start(self) -> None:
        self._pass = 0
        self._layer = 0
        self._candidates = []
        self._layer = self._first_usable_layer()
        self._apply_proxy_layer()
        self._launch_pass()

    @staticmethod
    def _proxy_alive(host: str, port: int, timeout_ms: int = 350) -> bool:
        """TCP 探一下本机代理端口还活着没（最坏阻塞 timeout_ms）。"""
        from PySide6.QtNetwork import QTcpSocket
        if not host or port <= 0:
            return False
        sock = QTcpSocket()
        sock.connectToHost(host, int(port))
        ok = sock.waitForConnected(timeout_ms)
        sock.abort()
        return ok

    def _first_usable_layer(self) -> int:
        """跳过"系统代理指向一个死掉的本机端口"这一层。

        这台机器上极常见：Clash 关了，但系统代理设置还指着 127.0.0.1:7897 ——
        Qt 对每条源都要等满 4s 才超时，整整白等一轮（4.8s）才轮到下一层。
        开跑前花 ≤350ms 探一下端口，死了就直接从下一层开始。
        """
        try:
            from PySide6.QtNetwork import (QNetworkProxyFactory,
                                           QNetworkProxyQuery)
            proxy = QNetworkProxyFactory.systemProxyForQuery(
                QNetworkProxyQuery(QUrl("https://cdn.jsdelivr.net")))[0]
        except Exception:                 # noqa: BLE001 - 取不到就按"系统代理"跑
            return 0
        if proxy.type() != 0 and proxy.hostName():   # 0 = NoProxy
            if not self._proxy_alive(proxy.hostName(), proxy.port()):
                # 死代理：本机 Clash 若是同一个端口也一并跳过，直接直连
                clash = CLASH_PROXY
                if (proxy.hostName(), int(proxy.port())) == (clash[0], clash[1]):
                    return 2
                return 1
        return 0

    # -- 代理分层 --------------------------------------------------------

    @staticmethod
    def proxy_layers() -> tuple:
        """(层名, 是否指定代理)。0=跟随系统，1=本机 Clash，2=直连。"""
        return (("system", None), ("clash", CLASH_PROXY), ("direct", "no"))

    def _apply_proxy_layer(self) -> None:
        from PySide6.QtNetwork import QNetworkProxy
        _, proxy = self.proxy_layers()[min(self._layer,
                                           len(self.proxy_layers()) - 1)]
        if proxy == "no":
            QNetworkProxy.setApplicationProxy(
                QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
        elif proxy is None:
            QNetworkProxy.setApplicationProxy(
                QNetworkProxy(QNetworkProxy.ProxyType.DefaultProxy))
        else:
            QNetworkProxy.setApplicationProxy(QNetworkProxy(
                QNetworkProxy.ProxyType.HttpProxy, *proxy))

    def _restore_proxy(self) -> None:
        """检查结束把全局代理还原成"跟随系统"（QNetworkProxy 是全局状态）。"""
        from PySide6.QtNetwork import QNetworkProxy
        QNetworkProxy.setApplicationProxy(
            QNetworkProxy(QNetworkProxy.ProxyType.DefaultProxy))

    # -- 内部 ------------------------------------------------------------

    def _launch_pass(self) -> None:
        self._done = False
        self._abort_all()
        for name, url, parser, prefix in version_sources():
            request = QNetworkRequest(QUrl(url))
            request.setTransferTimeout(SOURCE_TIMEOUT_MS)
            request.setAttribute(
                QNetworkRequest.RedirectPolicyAttribute,
                QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
            reply = self._nam.get(request)
            reply.finished.connect(
                lambda r=reply, n=name, p=parser, pre=prefix:
                self._on_one(r, n, p, pre))
            self._replies.append(reply)
        # 整轮兜底：并行也要防止"全部挂起不回调"
        self._timer.start(SOURCE_TIMEOUT_MS + 800)

    def _abort_all(self) -> None:
        self._grace.stop()
        for reply in self._replies:
            try:
                reply.finished.disconnect()
                reply.abort()
                reply.deleteLater()
            except (RuntimeError, TypeError):
                pass
        self._replies = []

    def _on_one(self, reply, name: str, parser, prefix: str | None) -> None:
        if self._done:
            # 已经有源赢了：剩下的回应只用来"认一下最快的镜像"，不参与结果
            if prefix and reply.error() == QNetworkReply.NetworkError.NoError:
                note_best_prefix(prefix)
            return
        data = bytes(reply.readAll())
        error = reply.error()
        if error != QNetworkReply.NetworkError.NoError or not data:
            return                      # 这条不行：等别的源，全部失败由 _on_timeout 收尾
        try:
            info = parser(data.decode("utf-8", errors="replace"))
        except Exception:
            info = None
        if not (info and info.get("version")):
            if prefix:
                note_best_prefix(prefix)   # 活着就算数（即使这一版解析不出）
            return
        info.setdefault("sha256", None)
        info.setdefault("setup_sha256", None)
        info["source"] = name
        if prefix:
            note_best_prefix(prefix)
        # 不立刻发射：开一个 PICK_GRACE_MS 的窗口把同期答案收齐，再取版本号最高的
        # 那条（jsDelivr 边沿缓存滞后时，靠这个窗口让镜像的正确答案盖过它）。
        self._candidates.append((version_tuple(info["version"]), info,
                                 self._seq, prefix))
        self._seq += 1
        self._timer.stop()               # 已有结果，别让"整轮兜底"再来打断
        if not self._pick.isActive():
            self._pick.start(PICK_GRACE_MS)

    def _emit_best(self) -> None:
        """窗口到点：取版本号最高者（同版本取先到的），发结果。"""
        if self._done or not self._candidates:
            return
        self._done = True
        self._pick.stop()
        self._timer.stop()
        _vt, info, _seq, prefix = max(self._candidates,
                                      key=lambda c: (c[0], -c[2]))
        self._candidates = []
        self._restore_proxy()
        self.checked.emit(info)
        # 镜像再留 400ms 的报到窗口：赢家若不是镜像，还得靠它认一条最快的下载源
        if prefix is None:
            self._grace.start(400)
        else:
            self._abort_all()

    def _on_timeout(self) -> None:
        if self._done:
            return
        self._pass += 1
        self._abort_all()
        if self._pass >= CHECK_PASSES:
            # 这一层（系统代理）跑完还没结果 → 换下一层：本机 Clash → 直连
            if self._layer + 1 < len(self.proxy_layers()):
                self._layer += 1
                self._pass = 0
                self._apply_proxy_layer()
                self._launch_pass()
                return
            self._done = True
            self._restore_proxy()
            self.failed.emit("所有版本源都不可达（镜像、直连、本机代理都试过）")
            return
        self._launch_pass()


# ------------------------------------------------------------------ 弹窗

class UpdateDialog(QDialog):
    """更新提示弹窗：三个选项 + 下载进度，样式全部复用全局 QSS 与现有控件。

    - 「立即更新」：多源下载（镜像加速在前）→ 哈希/大小校验 → 自举替换重启。
      开着代理的用户走源链里的 GitHub 直连照样通；下载失败自动重试并回退。
    - 「暂不更新」：关闭弹窗；``dismissed_session`` 置 True，本次会话内
      自动检查不再弹（手动"检查更新"不受影响）。
    - 「前往 GitHub 仓库」：系统默认浏览器打开仓库主页。
    """

    dismissed_session = Signal()

    def __init__(self, latest: str, current: str, sha256: str | None = None,
                 size: int | None = None, tag: str | None = None,
                 auto: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("发现新版本")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._latest = latest
        self._size = size
        self._tag = tag or f"v{latest}"
        self._auto = auto
        self._frozen = bool(getattr(sys_frozen(), "frozen", False))
        # 安装版 → 下载安装包、交安装向导接管；免安装版 → 下载 exe 静默替换。
        self._is_setup = self._frozen and is_installed()
        # ⛔ 校验口径 = **远端 version.py 里这一版的哈希**（调用方经
        #    `update_asset_and_hash(info)` 按"安装版/免安装版"挑好传进来）。
        #    这里曾经在安装版分支覆盖成 `version.LATEST_SETUP_SHA256` —— 那是
        #    **当前运行版本**的常量，拿它校验"下一版"的安装包必然不符：整条下载
        #    源链试完一遍后报"所有下载源都失败了"（2026-10-24 静态审查揪出来的
        #    必然触发缺陷）。本地常量只在本版本自己发布时等于正确值，永远不能
        #    用来校验新版本。
        self._sha256 = sha256 or None
        self._nam = QNetworkAccessManager(self)
        self._download_pass = 0
        # 多源抢跑：urls 是本次源链，racers/recv 每条在跑源各一份缓冲与计数
        self._urls: tuple = ()
        self._next_idx = 0
        self._racers: dict = {}
        self._recv: dict = {}
        # 卡源看门狗：镜像"连上了但不给数据"是最常见的坑（TCP 通、字节不来），
        # 没有它的话下载会一直挂在那条源上，用户只能看着进度条不动。
        self._stall = QTimer(self)
        self._stall.setSingleShot(True)
        self._stall.timeout.connect(self._on_stall)

        root = QVBoxLayout(self)
        root.setContentsMargins(GAP + 6, GAP + 6, GAP + 6, GAP + 6)
        root.setSpacing(GAP)

        title = QLabel(f"发现新版本 v{latest}")
        title.setFont(ui_font(15.0))
        title.setObjectName("dialogTitle")
        root.addWidget(title)

        if self._is_setup:
            note = ("将下载安装包并启动安装向导（可自选安装位置，"
                    "自动创建桌面快捷方式）。你的设置与日志都会保留。")
        else:
            note = "更新只替换程序文件，你的所有设置都会保留。"
        info = QLabel(f"当前版本 v{current}　→　新版本 v{latest}\n" + note)
        info.setFont(ui_font(11.0))
        info.setWordWrap(True)
        root.addWidget(info)

        self._status = QLabel("")
        self._status.setFont(ui_font(10.5))
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.hide()
        root.addWidget(self._progress)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self._btn_repo = InkButton("前往 GitHub 仓库", variant="ghost")
        self._btn_repo.clicked.connect(self._open_repo)
        self._btn_later = InkButton("暂不更新", variant="ghost")
        self._btn_later.clicked.connect(self._later)
        self._btn_now = InkButton("立即更新", variant="primary")
        self._btn_now.clicked.connect(self._update_now)
        if not self._frozen:
            # 开发环境（python main.py）没有可替换的 exe —— 不提供自更新
            self._btn_now.setEnabled(False)
            self._btn_now.setToolTip("开发环境不可用（未打包）")
        buttons.addWidget(self._btn_repo)
        buttons.addStretch(1)
        buttons.addWidget(self._btn_later)
        buttons.addWidget(self._btn_now)
        root.addLayout(buttons)

    # -- 三个选项 --------------------------------------------------------

    def _open_repo(self) -> None:
        from version import REPO_URL
        QDesktopServices.openUrl(QUrl(REPO_URL))

    def _later(self) -> None:
        self.dismissed_session.emit()
        self.reject()

    def _update_now(self) -> None:
        if not self._frozen:
            return
        self._download_pass = 0
        self._urls = release_download_urls(
            self._tag,
            setup_asset_name(self._latest) if self._is_setup else None)
        self._next_idx = 0
        self._racers = {}                # QNetworkReply -> bytearray（各源独立缓冲）
        self._recv = {}                  # QNetworkReply -> 已收字节数（给进度条挑"领先者"）
        self._btn_now.setEnabled(False)
        self._btn_later.setEnabled(False)
        self._btn_repo.setEnabled(False)
        self._progress.show()
        self._progress.setValue(0)
        self._progress.setRange(0, 100)
        self._status.setText("正在下载更新包（多源并行抢跑）…")
        self._start_batch()

    # -- 下载：多源并行抢跑 ------------------------------------------------

    def _start_batch(self) -> None:
        """从源链里取下一批（最多 RACERS_PER_BATCH 条）同时开跑。

        ⚠️ 为什么并行（2026-10-25，用户："不开代理也要快"）：镜像之间速度能差
        一个数量级，而"谁快"事先不知道（测速只覆盖版本文件、不代表 30MB 资产的
        吞吐）。串行时一旦第一条慢，用户就得盯着进度条干等（旧逻辑靠 8 秒看门狗
        兜，仍然要浪费 8 秒）。现在同时起两条、谁先下完谁算数，另一条立即中断。
        内存代价：2 × 包体（约 60MB）峰值，下完立刻释放。
        """
        if self._next_idx >= len(self._urls):
            self._download_pass += 1
            self._next_idx = 0
            if self._download_pass >= DOWNLOAD_PASSES:
                self._download_failed("所有下载源都失败了（镜像与直连都试过）")
                return
            self._status.setText("下载失败，正在自动重试…")
        batch = self._urls[self._next_idx:self._next_idx + RACERS_PER_BATCH]
        self._next_idx += len(batch)
        for url in batch:
            self._launch_racer(url)

    def _launch_racer(self, url: str) -> None:
        host = url.split("/")[2]
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(0)            # 大文件不设整体超时，靠看门狗换源
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self._nam.get(request)
        self._racers[reply] = bytearray()
        self._recv[reply] = 0
        reply.downloadProgress.connect(
            lambda got, total, r=reply: self._on_racer_progress(r, got, total))
        reply.finished.connect(lambda r=reply: self._on_racer_finished(r))
        self._stall.start(STALL_MS)
        self._status.setText(
            f"正在下载更新（{len(self._racers)} 路并行 · {host}）…")

    def _on_racer_progress(self, reply, received: int, total: int) -> None:
        if reply not in self._racers:
            return
        self._recv[reply] = max(received, self._recv.get(reply, 0))
        self._stall.start(STALL_MS)              # 有字节进来 = 至少一条活着
        lead = max(self._recv.values())
        lead_total = total if received >= lead else 0
        if lead_total > 0:
            self._progress.setValue(int(lead * 100 / lead_total))
            self._status.setText(f"正在下载更新… {lead / 1e6:.1f} / "
                                 f"{lead_total / 1e6:.1f} MB"
                                 f"（{len(self._racers)} 路并行）")
        else:
            self._progress.setRange(0, 0)
            self._status.setText(f"正在下载更新… {lead / 1e6:.1f} MB")

    def _on_stall(self) -> None:
        """看门狗：整批连续 STALL_MS 没有新字节 → 全部放弃，换下一批。"""
        if not self._racers:
            return
        app_log.info("下载停滞，换下一批源")
        self._abort_racers()

    def _abort_racers(self) -> None:
        self._stall.stop()
        for reply in list(self._racers):
            try:
                reply.downloadProgress.disconnect()
                reply.finished.disconnect()
                reply.abort()
                reply.deleteLater()
            except (RuntimeError, TypeError):
                pass
        self._racers.clear()
        self._recv.clear()

    def _on_racer_finished(self, reply) -> None:
        if reply not in self._racers:
            return                        # 已被别人胜出后清掉
        buf = self._racers.pop(reply)
        self._recv.pop(reply, None)
        error = reply.error()
        tail = bytes(reply.readAll()) if error == QNetworkReply.NetworkError.NoError else b""
        reply.deleteLater()
        if error == QNetworkReply.NetworkError.NoError:
            buf += tail
        host = reply.url().host() if hasattr(reply, "url") else "?"
        ok, why = ((False, "空响应") if not buf
                   else verify_integrity(bytes(buf), self._sha256, self._size))
        if ok:
            self._stall.stop()
            self._abort_racers()
            self._progress.setRange(0, 100)
            self._progress.setValue(100)
            self._status.setText("校验通过，正在准备安装…")
            self._apply(bytes(buf))
            return
        app_log.info(f"更新包这条源不可用（{host}：{why}），继续等/换下一批")
        if not self._racers:             # 本批全部失败 → 下一批
            self._progress.setValue(0)
            self._progress.setRange(0, 100)
            self._start_batch()


    def _download_failed(self, reason: str) -> None:
        self._progress.hide()
        self._status.setText(f"下载失败：{reason}")
        self._btn_now.setEnabled(True)
        self._btn_later.setEnabled(True)
        self._btn_repo.setEnabled(True)

    def _apply(self, data: bytes) -> None:
        """装更新：安装版交给安装向导接管，免安装版静默替换 exe。

        第 5 项（用户选择"安装器接管更新"）：装了安装版的用户下载的是
        **安装包**，运行它由 Inno 向导完成覆盖 —— 能自选目录、自动建桌面
        快捷方式、有卸载程序，且升级时沿用上次的安装位置。免安装单文件版
        没有安装目录可接管，仍旧走原来的"自举替换 exe"。
        """
        import os
        import sys
        if self._is_setup:
            setup = str(Path(tempfile.gettempdir()) /
                        setup_asset_name(self._latest))
            with open(setup, "wb") as f:
                f.write(data)
            self._status.setText("校验通过，正在启动安装向导…")
            _launch_installer(setup, silent=not self._auto)
            self.accept()
            QApplication.instance().quit()
            return
        tmp = str(Path(tempfile.gettempdir()) / "SerialAssistant_update.exe")
        with open(tmp, "wb") as f:
            f.write(data)
        target = os.path.abspath(sys.executable)
        spawn_apply_update(tmp, target)
        self.accept()
        QApplication.instance().quit()


def _launch_installer(setup_path: str, silent: bool = False) -> None:
    """运行下载好的安装包。

    ``silent`` ＝ 后台自动更新场景：用 ``/SILENT`` 走完安装（进度条可见），
    不让用户一步步点 —— 但**保留向导 UI 的静默**（``/VERYSILENT`` 连进度条
    都没有，用户会以为卡住了）。手动点「立即更新」时打开完整向导。
    升级时 AppId 不变，Inno 自动沿用上次的安装目录与快捷方式选择。
    """
    import subprocess
    args = [setup_path]
    if silent:
        args.append("/SILENT")
    # /NORESTART：装完不自动重启机器（我们自己会重启软件）
    args.append("/NORESTART")
    subprocess.Popen(args, cwd=str(Path(setup_path).parent), close_fds=True)


def sys_frozen():
    """sys 模块的间接引用（selftest 里替换方便）。"""
    import sys
    return sys
