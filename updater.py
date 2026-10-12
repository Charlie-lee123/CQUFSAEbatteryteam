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
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from ui_anim import Tween, out_cubic
from ui_controls import InkButton
from ui_theme import GAP, qcolor, ui_font

TRANSFER_TIMEOUT_MS = 20000      # 单请求超时（含镜像响应慢的余量）
SOURCE_TIMEOUT_MS = 8000         # 版本检测单源超时
CHECK_PASSES = 2                 # 整条源链的重试轮数
DOWNLOAD_PASSES = 2              # 下载源链的重试轮数


# ------------------------------------------------------------------ 源定义

def _OWNER_REPO() -> str:
    from version import OWNER_REPO
    return OWNER_REPO


def version_sources() -> tuple:
    """版本检测源链（名字, url, 解析器）—— 依次尝试，失败回退。

    ① jsDelivr CDN（国内可直连，仓库根目录的 version.py）
    ②③ 镜像加速的 GitHub raw（两个常用镜像互为备份）
    ④ GitHub raw 直连（开着代理的用户）
    ⑤ GitHub API 标签列表（兜底；只有版本号、没有哈希）
    """
    repo = _OWNER_REPO()
    raw = f"https://raw.githubusercontent.com/{repo}/main/version.py"
    return (
        ("jsDelivr",
         f"https://cdn.jsdelivr.net/gh/{repo}@main/version.py",
         parse_version_file),
        ("ghfast",
         f"https://ghfast.top/{raw}", parse_version_file),
        ("gh-proxy",
         f"https://gh-proxy.com/{raw}", parse_version_file),
        ("GitHub raw", raw, parse_version_file),
        ("GitHub API",
         f"https://api.github.com/repos/{repo}/tags", parse_tags_json),
    )


def release_download_urls(tag: str, asset: str | None = None) -> tuple:
    """某版本的 exe 下载源链（镜像加速在前，GitHub 直连兜底）。"""
    from version import ASSET_NAME
    asset = asset or ASSET_NAME
    repo = _OWNER_REPO()
    direct = f"https://github.com/{repo}/releases/download/{tag}/{asset}"
    return (
        f"https://ghfast.top/{direct}",
        f"https://gh-proxy.com/{direct}",
        direct,
    )


# ------------------------------------------------------------------ 解析器

def version_tuple(s: str) -> tuple:
    """版本号字符串 → 可比较的整数元组（"1.10" 排在 "1.9" 后面）。"""
    return tuple(int(p) for p in str(s).split(".") if p.isdigit())


def parse_version_file(text: str) -> dict | None:
    """仓库里 version.py 的内容 → {"version": "1.6", "sha256": "..."}。"""
    import re
    m = re.search(r'^VERSION\s*=\s*"(\d+(?:\.\d+)*)"', text, re.M)
    if not m:
        return None
    out = {"version": m.group(1)}
    m = re.search(r'^LATEST_EXE_SHA256\s*=\s*"([0-9a-fA-F]{64})"', text, re.M)
    if m:
        out["sha256"] = m.group(1).lower()
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
    return (False, "没有可用的校验信息（哈希与大小都拿不到）")


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
    """多源版本检测：逐源请求（每源独立超时），失败自动回退，链完再重试。

    结果信号 ``checked(info)``：info 为归一化字典
    ``{"version": "1.6", "sha256": "..."/None, "tag": "v1.6"/None}``；
    全部失败发 ``failed(reason)``。
    """

    checked = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._reply = None
        self._pass = 0
        self._idx = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

    def start(self) -> None:
        self._pass = 0
        self._idx = 0
        self._try_next()

    def _try_next(self) -> None:
        sources = version_sources()
        if self._idx >= len(sources):
            self._pass += 1
            self._idx = 0
            if self._pass >= CHECK_PASSES:
                self.failed.emit("所有版本源都不可达")
                return
        name, url, parser = sources[self._idx]
        self._parser = parser
        self._source_name = name
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(SOURCE_TIMEOUT_MS)
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        self._reply = self._nam.get(request)
        self._reply.finished.connect(self._on_finished)
        self._timer.start(SOURCE_TIMEOUT_MS)

    def _advance(self) -> None:
        """回退到下一源；链尾则进入下一轮。"""
        self._idx += 1
        self._try_next()

    def _on_timeout(self) -> None:
        if self._reply is None:
            return
        self._reply.disconnect(self)
        self._reply.abort()
        self._reply.deleteLater()
        self._reply = None
        self._advance()

    def _on_finished(self) -> None:
        reply = self.sender()
        if reply is not getattr(self, "_reply", None):
            return                      # 迟到的回应，忽略
        self._timer.stop()
        error = reply.error()
        data = bytes(reply.readAll())
        reply.deleteLater()
        self._reply = None
        if error != QNetworkReply.NetworkError.NoError:
            self._advance()
            return
        try:
            info = self._parser(data.decode("utf-8", errors="replace"))
        except Exception:
            info = None
        if not info or not info.get("version"):
            self._advance()             # 解析不出也当源失败，回退下一源
            return
        info.setdefault("sha256", None)
        info["source"] = self._source_name
        self.checked.emit(info)


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
        self._sha256 = sha256
        self._size = size
        self._tag = tag or f"v{latest}"
        self._auto = auto
        self._frozen = bool(getattr(sys_frozen(), "frozen", False))
        self._nam = QNetworkAccessManager(self)
        self._reply = None
        self._download_pass = 0
        self._url_idx = 0
        self._data = bytearray()

        root = QVBoxLayout(self)
        root.setContentsMargins(GAP + 6, GAP + 6, GAP + 6, GAP + 6)
        root.setSpacing(GAP)

        title = QLabel(f"发现新版本 v{latest}")
        title.setFont(ui_font(15.0))
        title.setObjectName("dialogTitle")
        root.addWidget(title)

        info = QLabel(f"当前版本 v{current}　→　新版本 v{latest}\n"
                      "更新只替换程序文件，你的所有设置都会保留。")
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
        self._url_idx = 0
        self._data.clear()
        self._btn_now.setEnabled(False)
        self._btn_later.setEnabled(False)
        self._btn_repo.setEnabled(False)
        self._progress.show()
        self._progress.setValue(0)
        self._status.setText("正在下载更新（镜像加速）…")
        self._try_download()

    # -- 下载 ------------------------------------------------------------

    def _try_download(self) -> None:
        urls = release_download_urls(self._tag)
        if self._url_idx >= len(urls):
            self._download_pass += 1
            self._url_idx = 0
            if self._download_pass >= DOWNLOAD_PASSES:
                self._download_failed("所有下载源都失败了")
                return
            self._status.setText("下载失败，正在自动重试…")
        self._url = urls[self._url_idx]
        request = QNetworkRequest(QUrl(self._url))
        request.setTransferTimeout(0)            # 大文件不设整体超时，靠失败回退
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        self._reply = self._nam.get(request)
        self._reply.downloadProgress.connect(self._on_progress)
        self._reply.finished.connect(self._on_download_finished)

    def _on_progress(self, received: int, total: int) -> None:
        if total > 0:
            self._progress.setValue(int(received * 100 / total))
            self._status.setText(f"正在下载更新… {received / 1e6:.1f} / "
                                 f"{total / 1e6:.1f} MB")
        else:
            self._progress.setRange(0, 0)
            self._status.setText(f"正在下载更新… {received / 1e6:.1f} MB")

    def _on_download_finished(self) -> None:
        reply = self.sender()
        if reply is not getattr(self, "_reply", None):
            return
        error = reply.error()
        self._data += bytes(reply.readAll())
        reply.deleteLater()
        self._reply = None
        if error != QNetworkReply.NetworkError.NoError:
            # 该源失败：回退下一源 / 下一轮重试
            self._url_idx += 1
            self._try_download()
            return
        ok, why = verify_integrity(bytes(self._data), self._sha256, self._size)
        if not ok:
            # 完整性不过：换一个源重新下（镜像可能回的是错误页而不是 exe）
            self._url_idx += 1
            self._data.clear()
            self._try_download()
            return
        self._progress.setValue(100)
        self._status.setText("校验通过，正在准备安装…")
        self._apply(bytes(self._data))

    def _download_failed(self, reason: str) -> None:
        self._progress.hide()
        self._status.setText(f"下载失败：{reason}")
        self._btn_now.setEnabled(True)
        self._btn_later.setEnabled(True)
        self._btn_repo.setEnabled(True)

    def _apply(self, data: bytes) -> None:
        """把校验通过的新 exe 落到临时目录，交给它自举替换，然后退出应用。"""
        import os
        import sys
        tmp = str(Path(tempfile.gettempdir()) / "SerialAssistant_update.exe")
        with open(tmp, "wb") as f:
            f.write(data)
        target = os.path.abspath(sys.executable)
        spawn_apply_update(tmp, target)
        self.accept()
        QApplication.instance().quit()


def sys_frozen():
    """sys 模块的间接引用（selftest 里替换方便）。"""
    import sys
    return sys
