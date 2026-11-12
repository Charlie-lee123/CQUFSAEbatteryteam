# -*- coding: utf-8 -*-
"""可选 DLC 插件运行时（宿主侧）。

设计前提（用户 2026-11-09）：DLC **不随主程序一起发布**，用户自行选择下载。
所以宿主这边只留一套很薄的机制，不把任何插件代码编进 exe：

1. 从两个目录发现 ``manifest.json``：用户数据目录（安装版 / 自动安装用）
   与 exe 同级 ``dlc/``（便携版手动放）；
2. 校验清单字段、API 版本、最低主程序版本；
3. 把一个插件目录加进 ``sys.path``，``importlib`` 载入 ``create(ctx)``；
4. 给插件一个受限的 ``HostContext``：注册面板回调、拿数据目录、写日志。

⛔ 三条硬边界：
- 不碰主程序的 QSettings（插件配置走独立 JSON 文件，见 ``enabled.json``）；
- 不写全局 QSS（``@token@`` 占位符写错一处会让整套样式静默失效）；
- 插件加载/运行的任何异常都在本模块内吞掉并转成状态文本，
  **绝不**让第三方扩展把主窗口拖崩。

安装 ZIP 的安全约束：防 Zip Slip、限制条目数与解压体积、白名单后缀、
先解到临时目录再原子替换，替换前把旧版本备份成 ZIP。
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import app_log
from version import VERSION

DLC_API = 1
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,47}$")
_MAX_ENTRIES = 200
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 32 * 1024 * 1024
_ALLOWED_SUFFIXES = {".py", ".json", ".md", ".txt", ".png", ".ico", ".svg",
                     ".cfg", ".ini", ".csv"}
_SKIP_PARTS = {"__pycache__", "__macosx", ".git", ".idea", ".vscode"}

# 供自测注入，避免写到用户真实目录
_DIR_OVERRIDE_ENV = "SERIALASSISTANT_DLC_DIR"


def _data_root() -> Path:
    """和 app_log / rx_history 同根：%LOCALAPPDATA%\\CQUFSAE-SerialAssistant。"""
    override = os.environ.get(_DIR_OVERRIDE_ENV)
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local:
        return Path(local) / "CQUFSAE-SerialAssistant" / "dlc"
    return Path.home() / ".CQUFSAE-SerialAssistant" / "dlc"


def user_dir() -> Path:
    """用户安装的 DLC 目录（自动安装写这里）。"""
    path = _data_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def portable_dir() -> Path:
    """便携版目录：exe 同级 dlc/；源码运行时是项目根目录的 dlc/。"""
    base = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False) else Path(__file__).resolve().parent)
    return base / "dlc"


def _version_tuple(text: object) -> tuple[int, ...]:
    parts = []
    for chunk in str(text or "").split(".")[:4]:
        match = re.match(r"\d+", chunk.strip())
        parts.append(int(match.group()) if match else 0)
    return tuple(parts) or (0,)


def _read_manifest(path: Path) -> tuple[dict | None, str]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"清单读不出来：{exc}"
    if not isinstance(data, dict):
        return None, "清单不是 JSON 对象"
    plugin_id = str(data.get("id") or "")
    if not _ID_RE.match(plugin_id):
        return None, "清单缺少合法的 id（字母开头，3~48 位字母数字下划线）"
    entry = str(data.get("entry") or plugin_id)
    if not _ID_RE.match(entry):
        return None, "清单的 entry 不是合法的 Python 包名"
    api = data.get("api")
    if not isinstance(api, int) or api < 1 or api > DLC_API:
        return None, f"插件 API 版本 {api!r} 与本程序支持的 v{DLC_API} 不兼容"
    minimum = _version_tuple(data.get("min_app") or "0")
    if _version_tuple(VERSION) < minimum:
        return None, (f"需要主程序 v{data.get('min_app')} 及以上，"
                      f"当前是 v{VERSION}")
    return data, ""


@dataclass
class PluginInfo:
    """一个已发现（不一定已加载）的 DLC。"""

    id: str
    name: str
    version: str
    entry: str
    path: Path                 # manifest.json 所在目录（即 Python 包目录）
    root: Path                 # 该插件所属的 dlc 根目录
    enabled: bool = True
    error: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        state = "可用" if not self.error else f"不可用：{self.error}"
        return f"{self.name}  v{self.version}  ·  {state}"


def _enabled_file() -> Path:
    return _data_root() / "enabled.json"


def _enabled_map() -> dict[str, bool]:
    try:
        data = json.loads(_enabled_file().read_text("utf-8"))
        if isinstance(data, dict):
            return {str(k): bool(v) for k, v in data.items()}
    except (OSError, ValueError):
        pass
    return {}


def set_enabled(plugin_id: str, enabled: bool) -> None:
    """记录启用状态。默认（键不存在）= 启用，安装完即可用。"""
    if not _ID_RE.match(plugin_id):
        return
    _data_root().mkdir(parents=True, exist_ok=True)
    data = _enabled_map()
    data[plugin_id] = bool(enabled)
    try:
        _enabled_file().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except OSError as exc:
        app_log.warning(f"DLC 启用状态写入失败：{exc}")


def _manifest_in(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found = list(root.glob("*/manifest.json"))
    # 根目录直接放一份 manifest 也认（单文件式插件）
    if (root / "manifest.json").is_file():
        found.append(root / "manifest.json")
    return found


def discover() -> list[PluginInfo]:
    """扫描两个目录，返回插件清单（用户目录优先，同名不重复）。"""
    enabled = _enabled_map()
    seen: set[str] = set()
    result: list[PluginInfo] = []
    for root in (user_dir(), portable_dir()):
        for manifest in _manifest_in(root):
            data, error = _read_manifest(manifest)
            plugin_id = (data or {}).get("id") or manifest.parent.name
            if plugin_id in seen:
                continue
            seen.add(str(plugin_id))
            raw = data or {}
            result.append(PluginInfo(
                id=str(plugin_id),
                name=str(raw.get("name") or plugin_id),
                version=str(raw.get("version") or "?"),
                entry=str(raw.get("entry") or plugin_id),
                path=manifest.parent,
                root=root,
                enabled=enabled.get(str(plugin_id), True),
                error=error,
                raw=raw,
            ))
    return result


def _safe_member(name: str, prefix: str) -> PurePosixPath | None:
    """把 zip 条目转成相对目标路径；不安全返回 None（防 Zip Slip）。"""
    text = str(name).replace("\\", "/")
    pure = PurePosixPath(text)
    if pure.is_absolute() or text.startswith("/") or ":" in text.split("/")[0]:
        return None
    parts = pure.parts
    if any(part in ("..", "") for part in parts):
        return None
    if any(part.lower() in _SKIP_PARTS for part in parts):
        return None
    if prefix:
        if not text.startswith(prefix + "/"):
            return None
        pure = PurePosixPath(text[len(prefix) + 1:])
        if not pure.parts:
            return None
    suffix = pure.suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        return None
    return pure


def _backup(root: Path, plugin_id: str, target: Path) -> Path | None:
    """替换/卸载前把旧插件目录压成 ZIP，出问题时可手工恢复。"""
    if not target.is_dir():
        return None
    backup_dir = root / "_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = backup_dir / f"{plugin_id}-{stamp}.zip"
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(target.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    zf.write(path, str(path.relative_to(root)))
        return out
    except OSError as exc:
        app_log.warning(f"DLC 备份失败（不阻断安装）：{exc}")
        return None


def install_zip(zip_path: str | Path) -> PluginInfo:
    """安装一个 DLC ZIP，返回安装后的清单信息。失败抛 ``ValueError``。

    ZIP 里允许两种布局：``heartbeatDLC/manifest.json``（推荐）或直接
    ``manifest.json``。插件 id 取自清单，并会与目录名/包名一并校验。
    """
    source = Path(zip_path)
    if not source.is_file():
        raise ValueError(f"找不到文件：{source}")
    if source.stat().st_size > _MAX_TOTAL_BYTES:
        raise ValueError("安装包超过 32 MB，拒绝安装")
    # 发布包旁边若带 .sha256 就先验完整性（脚本 _build_heartbeat_dlc.py 会生成）。
    # 手动转发的裸 ZIP 没有校验文件也允许装，但会在日志里注明。
    sidecar = source.with_name(source.name + ".sha256")
    if sidecar.is_file():
        try:
            expected = sidecar.read_text("utf-8").split()[0].strip().lower()
        except (OSError, IndexError) as exc:
            raise ValueError(f"校验文件读不出来：{sidecar}（{exc}）") from exc
        actual = sha256_file(source)
        if expected != actual:
            raise ValueError(f"SHA256 校验失败，安装包可能被改动或损坏："
                             f"期望 {expected[:16]}… / 实际 {actual[:16]}…")
        app_log.info(f"DLC 安装包 SHA256 校验通过：{source.name}")
    else:
        app_log.info(f"DLC 安装包没有随附 .sha256，按用户自行选择的文件直接安装：{source.name}")

    root = user_dir()
    with zipfile.ZipFile(source) as zf:
        entries = zf.infolist()
        if len(entries) > _MAX_ENTRIES:
            raise ValueError(f"安装包条目过多（{len(entries)} 个），拒绝安装")
        manifests = [i for i in entries if PurePosixPath(i.filename).name
                     == "manifest.json" and not i.is_dir()]
        if not manifests:
            raise ValueError("安装包里没有 manifest.json，不是有效的 DLC")
        chosen = min(manifests, key=lambda i: len(PurePosixPath(i.filename).parts))
        prefix = str(PurePosixPath(chosen.filename).parent)
        if prefix == ".":
            prefix = ""
        try:
            raw = json.loads(zf.read(chosen))
        except (ValueError, KeyError, RuntimeError) as exc:
            raise ValueError(f"manifest.json 解析失败：{exc}") from exc
        data, error = _read_manifest_dict(raw)
        if error:
            raise ValueError(error)
        plugin_id = str(data["id"])
        if prefix and PurePosixPath(prefix).name != plugin_id:
            raise ValueError(f"目录名 {PurePosixPath(prefix).name!r} 与清单 id "
                             f"{plugin_id!r} 不一致")

        total = 0
        staged: list[tuple[object, PurePosixPath]] = []
        for item in entries:
            if item.is_dir():
                continue
            rel = _safe_member(item.filename, prefix)
            if rel is None:
                continue
            total += item.file_size
            if item.file_size > _MAX_FILE_BYTES or total > _MAX_TOTAL_BYTES:
                raise ValueError("安装包解压后体积超限，拒绝安装")
            staged.append((item, rel))
        if not any(rel.name == "manifest.json" for _, rel in staged):
            raise ValueError("过滤后没有可用的清单文件")

        tmp = root / f".{plugin_id}.staging-{os.getpid()}"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        try:
            for item, rel in staged:
                dest = tmp / Path(*rel.parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(item) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out, 64 * 1024)
            check, error = _read_manifest(tmp / "manifest.json")
            if error or check is None:
                raise ValueError(f"解包后的清单校验失败：{error}")
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    target = root / plugin_id
    # ⛔ 只允许删宿主自己管理、且确实带同 id 清单的插件目录
    if target.exists():
        old, old_error = _read_manifest(target / "manifest.json")
        if old_error or (old or {}).get("id") != plugin_id:
            shutil.rmtree(tmp, ignore_errors=True)
            raise ValueError("同名目录不是本程序管理的 DLC，拒绝覆盖")
        _backup(root, plugin_id, target)
        shutil.rmtree(target)
    tmp.replace(target)
    app_log.info(f"DLC 已安装：{plugin_id} v{raw.get('version')} → {target}")
    set_enabled(plugin_id, True)
    info = next((i for i in discover() if i.id == plugin_id), None)
    if info is None:
        raise ValueError("安装完成但没能重新发现插件，请检查安装目录")
    return info


def _read_manifest_dict(raw: object) -> tuple[dict | None, str]:
    """``_read_manifest`` 的内存版（ZIP 里的清单还没落盘，不写临时文件）。"""
    if not isinstance(raw, dict):
        return None, "清单不是 JSON 对象"
    plugin_id = str(raw.get("id") or "")
    if not _ID_RE.match(plugin_id):
        return None, "清单缺少合法的 id"
    entry = str(raw.get("entry") or plugin_id)
    if not _ID_RE.match(entry):
        return None, "清单的 entry 不是合法的 Python 包名"
    api = raw.get("api")
    if not isinstance(api, int) or api < 1 or api > DLC_API:
        return None, f"插件 API 版本 {api!r} 与本程序支持的 v{DLC_API} 不兼容"
    if _version_tuple(VERSION) < _version_tuple(raw.get("min_app") or "0"):
        return None, f"需要主程序 v{raw.get('min_app')} 及以上，当前是 v{VERSION}"
    return raw, ""


def uninstall(info: PluginInfo) -> Path | None:
    """卸载（仅用户目录下的插件），返回备份 ZIP 路径。"""
    if info.root.resolve() != user_dir().resolve():
        raise ValueError("便携版目录里的插件请手动删除，卸载只管用户安装目录")
    target = info.path
    if target == info.root:                 # 根目录直放 manifest 的形态不删根
        raise ValueError("这个插件直接放在 dlc 根目录，请手动删除对应文件")
    check, error = _read_manifest(target / "manifest.json")
    if error or (check or {}).get("id") != info.id:
        raise ValueError("目录里没有匹配 id 的清单，拒绝删除")
    backup = _backup(info.root, info.id, target)
    shutil.rmtree(target)
    set_enabled(info.id, False)
    app_log.info(f"DLC 已卸载：{info.id}，备份 {backup}")
    return backup


def sha256_file(path: str | Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_runtime(argv: list[str] | None = None) -> int:
    """``SerialAssistant.exe --check-dlc``：在**冻结产物**上验证 DLC 运行时。

    ONEFILE 产物里插件是外部目录的纯 .py，能不能被 ``importlib`` 动态载入，
    源码环境测不出来 —— 这个探针会在产物上真解一帧心率、真 import 一次 UI 层，
    结果写进 exe 同目录的 ``dlc_check.txt``（console=False 看不到 stdout）。
    """
    lines: list[str] = [
        f"VERSION={VERSION}  DLC_API={DLC_API}  frozen={getattr(sys, 'frozen', False)}",
        f"user_dir={user_dir()}",
        f"portable_dir={portable_dir()}",
    ]
    ok = True
    try:
        import bleak                                     # noqa: F401
        lines.append("bleak=OK（主程序已内置，插件无需另装）")
    except Exception as exc:                             # noqa: BLE001
        ok = False
        lines.append(f"bleak=FAIL {type(exc).__name__}: {exc}")

    class _StubCtx:
        app_version = VERSION
        launchers: list[str] = []

        def add_launcher(self, text, callback, tooltip="") -> None:
            _StubCtx.launchers.append(str(text))

        def data_dir(self, plugin_id):
            return _data_root() / "_data" / str(plugin_id)

        def log(self, message) -> None:
            lines.append(f"[plugin] {message}")

        def reload_now(self) -> None:
            pass

    infos = discover()
    lines.append(f"discovered={len(infos)}")
    if not infos:
        lines.append("WARN 没有安装任何 DLC，动态加载路径未覆盖；"
                     "请把 heartbeatDLC 放到 exe 同级 dlc/ 再测")
    for info in infos:
        lines.append(f"- {info.id} v{info.version} enabled={info.enabled} "
                     f"error={info.error or 'none'} path={info.path}")
        plugin = load(info, _StubCtx())
        if info.error or not info.enabled:
            continue
        if plugin is None:
            ok = False
            lines.append("  load=FAIL")
            continue
        lines.append(f"  load=OK launchers={_StubCtx.launchers}")
        try:
            protocol = importlib.import_module(f"{info.entry}.hr_protocol")
            frame = protocol.decode_hr_measurement(bytes([0x00, 0x59]))
            if frame is None or frame.bpm != 89:
                raise AssertionError("0x2A37 解码结果不是 89 bpm")
            # 多设备目标集合：移除一台、加入另一台，其余保持不变
            merged = protocol.merge_targets(
                {"A": "甲", "B": "乙"},
                [("remove", "A", ""), ("add", "C", "丙")])
            if merged != {"B": "乙", "C": "丙"}:
                raise AssertionError("多设备目标集合合并错误")
            importlib.import_module(f"{info.entry}.hr_worker")
            importlib.import_module(f"{info.entry}.hr_panel")
            lines.append("  protocol/worker/panel import=OK，00 59 → 89 bpm，多设备合并=OK")
        except Exception as exc:                         # noqa: BLE001
            ok = False
            lines.append(f"  submodule=FAIL {type(exc).__name__}: {exc}")
        shutdown = getattr(plugin, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:                           # noqa: BLE001
                pass
    lines.append(f"RESULT={'PASS' if ok else 'FAIL'}")

    text = "\n".join(lines)
    out = (Path(sys.executable).resolve().parent / "dlc_check.txt"
           if getattr(sys, "frozen", False) else Path.cwd() / "dlc_check.txt")
    try:
        out.write_text(text + "\n", "utf-8")
    except OSError as exc:
        print(f"写 {out} 失败：{exc}", file=sys.stderr)
    print(text)
    print(f"\n（结果也写到了：{out}）")
    return 0 if ok else 1


def load(info: PluginInfo, ctx: "HostContext") -> object | None:
    """载入插件并调 ``create(ctx)``。异常一律转成日志 + 返回 None。"""
    if info.error or not info.enabled:
        return None
    parent = info.path.parent
    text = str(parent)
    if text not in sys.path:
        sys.path.insert(0, text)
    try:
        importlib.invalidate_caches()
        module = importlib.import_module(info.entry)
        factory = getattr(module, "create", None)
        if not callable(factory):
            raise TypeError(f"{info.entry} 没有 create(ctx) 接口")
        plugin = factory(ctx)
        app_log.info(f"DLC 已加载：{info.id} v{info.version}")
        return plugin
    except Exception as exc:                  # noqa: BLE001 - 第三方扩展不能拖垮主窗
        app_log.error(f"DLC 加载失败 {info.id}: {type(exc).__name__}: {exc}")
        return None


class HostContext:
    """宿主暴露给插件的受限上下文（插件拿不到 QSettings / 全局 QSS）。"""

    def __init__(self, window) -> None:
        self._window = window

    @property
    def app_version(self) -> str:
        return VERSION

    @property
    def main_window(self):
        return self._window

    def add_launcher(self, text: str, callback, tooltip: str = "") -> None:
        """注册插件面板回调（宿主不再生成独立入口按钮，由「DLC」面板调用）。"""
        adder = getattr(self._window, "_dlc_add_launcher", None)
        if callable(adder):
            adder(str(text), callback, tooltip)

    def open_panel(self, plugin_id: str) -> bool:
        """打开已安装插件的面板；插件未启用 / 没有面板时返回 False。"""
        opener = getattr(self._window, "_dlc_open_panel", None)
        if callable(opener):
            try:
                return bool(opener(str(plugin_id)))
            except Exception:                   # noqa: BLE001 - 打开面板失败不该冒泡
                app_log.exception("打开插件面板失败")
        return False

    def request_uninstall(self, plugin_id: str) -> bool:
        """插件面板里的「卸载」入口：宿主确认后停实例、卸载（先备份）。

        ⛔ 必须由宿主执行：插件在运行中被删目录会连自己的模块文件一起消失。
        """
        remover = getattr(self._window, "_dlc_request_uninstall", None)
        if callable(remover):
            try:
                return bool(remover(str(plugin_id)))
            except Exception:                   # noqa: BLE001 - 卸载失败不该冒泡
                app_log.exception("卸载插件失败")
        return False

    def data_dir(self, plugin_id: str) -> Path:
        """插件数据目录：卸载插件不会连带删掉这里的历史数据。"""
        if not _ID_RE.match(str(plugin_id)):
            raise ValueError("非法的插件 id")
        path = _data_root() / "_data" / str(plugin_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def log(self, message: str) -> None:
        app_log.info(f"[DLC] {message}")

    def reload_now(self) -> None:
        """安装/启用后立刻加载尚未加载的插件（不卸载已有实例）。"""
        loader = getattr(self._window, "_init_dlc", None)
        if callable(loader):
            loader()
