"""CQUFSAE 串口助手 —— 程序入口。"""

from __future__ import annotations

import os
import sys
import time

# ---- SPLASH_AUDIT 启动计时（仅 SPLASH_AUDIT=1 时生效，正常运行零开销）----
_AUDIT_ON = os.environ.get("SPLASH_AUDIT") == "1"
if _AUDIT_ON:
    # 进程启动时刻：main.py 是被解释器加载的第一个业务模块，越早打点越接近真实
    # 「双击 → 进入 main 模块」的时刻。后续各模块（splash_anim / main_window）
    # 用同一个 T0 算相对毫秒，保证跨模块时间轴连贯。
    os.environ.setdefault("SPLASH_AUDIT_T0", str(time.perf_counter()))
_AUDIT_T0 = float(os.environ.get("SPLASH_AUDIT_T0", time.perf_counter()))


def _aud(tag: str) -> None:
    """打印里程碑：SPLASH_AUDIT <tag> <相对进程启动的毫秒数> → stderr。"""
    if _AUDIT_ON:
        print(f"SPLASH_AUDIT {tag} "
              f"{(time.perf_counter() - _AUDIT_T0) * 1000:.1f}",
              file=sys.stderr, flush=True)


_aud("start")  # 进程启动（main 模块被加载的最早时刻）

# 软件版本号（发布 / 备份以它为准，git 上同时打同名 tag）。
# 唯一出处是 version.py：main 一被 import 就会拉起 MainWindow，
# 其它模块（如设置面板）要从 version.py 取，不能反向 import 这里。
from version import VERSION as __version__

from PySide6.QtCore import QSettings, QTimer, QUrl, Qt
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import QApplication

import customization
from ui_brand import app_icon
from ui_theme import apply_scheme, detect_system_scheme, ui_font
# ⛔ 2026-11-03 录屏实证：``main_window`` 全家桶 import 约 312ms（Qt 控件 + 各
#    ui_* 模块），而它**只有构造主窗时才需要**。放模块顶部意味着双击后要先付
#    这 312ms 才轮到建开屏窗口 —— 挪到 main() 里、play_configured() **之后**
#    再 import，开屏能早 0.3s 出现（onedir 实测 0.9s，优化后约 0.6s）。
#    ⚠️ 只延后 import，不改变任何构造时机/顺序。


def _prewarm_font_engine() -> None:
    """后台线程预热 Qt 字体引擎：把主窗构造的 ~600ms 冷启动从播放期搬走。

    ⛔⛔ od47c（用户「我选择不要那个阻塞，不想有任何观感上的影响」）：
    真机遥测 ``big_gaps=[(0.0,109),(2.0,635),(2.67,272),(2.94,103)]`` ——
    落点段 2.10~3.10 被合计 1010ms 阻塞**完整覆盖**，动画几乎一帧跳到终点。
    根因不是逻辑慢，而是主窗构造里**第一次 QFontMetrics 测量**触发 Windows
    字体引擎冷启动（实测首次 550~680ms，之后同进程第二次构造只要 137~217ms），
    而 ``still_delay_ms`` 把构造排在墙钟 2.0s，正好压在落点段。

    实测解法：后台线程用界面**实际**字体（mono_font/num_font/默认）各做一次
    ``height()`` + ``horizontalAdvance()``（595ms），主线程并行工作完全不被
    阻塞（Qt 字体库初始化会释放 GIL）；预热后 ``MainWindow()`` 741ms → 137ms。
    把这 595ms 与开屏的预热/播放重叠 = **双击响应零增加、落点段零阻塞**。

    ⚠️ 线程里只做 QFontMetrics 查询（reentrant、不建 QWidget、不改全局状态）；
       开屏播放期主线程只 fillRect/drawPixmap，不用字体，无竞态。daemon +
       全量 try/except 兜底：失败就静默降级为原行为，绝不让预热拖垮启动。
    """
    import threading

    def _run() -> None:
        try:
            from PySide6.QtGui import QFont, QFontMetrics

            import ui_theme
            for f in (ui_theme.mono_font(12.0), ui_theme.num_font(11.5),
                      QFont()):
                fm = QFontMetrics(f)
                fm.height()
                fm.horizontalAdvance("0000-00-00 00:00:00")
            # od53：**真正 drawText 一次**。上面只做 QFontMetrics 查询，而主窗构造
            #   里 `_combo_label_ink_offset` 是 `combo.grab()` → 自绘 paintEvent →
            #   `drawText` 字形栅格化，那是另一套冷启动（实测首次 272ms，之后
            #   每条下拉框还要 30~60ms）。在后台线程先把这条路径走通一次，
            #   真机复测首次 272ms → 个位数 ms。
            #   ⚠️ QImage + QPainter 在非 GUI 线程是允许的（QPixmap 不行）；
            #      这里不建任何 QWidget、不改全局状态，失败照样静默降级。
            try:
                from PySide6.QtGui import QImage, QPainter

                img = QImage(160, 32, QImage.Format.Format_ARGB32_Premultiplied)
                img.fill(0)
                p = QPainter(img)
                p.setFont(ui_theme.ui_font(12.5))
                p.drawText(img.rect(), 0x0084, "8")        # AlignCenter
                p.drawText(img.rect(), 0x0084, "▾ 0x1F80")
                p.end()
            except Exception:             # noqa: BLE001 - 预热失败不挡启动
                pass
        except Exception:                 # noqa: BLE001 - 预热失败不挡启动
            pass

    threading.Thread(target=_run, name="font-prewarm", daemon=True).start()


def _run_apply_update(target: str) -> int:
    """`--apply-update <目标exe>`：自动更新的最后一步。

    旧实例已把新 exe 下载到临时目录并以本参数拉起**我们**（新 exe）；旧实例
    随即退出。这里等目标 exe 的文件锁释放（onefile 的引导进程退出要几秒），
    然后用自己覆盖目标、重启新版本。配置全在注册表，替换 exe 不丢任何设置。
    """
    import os
    import shutil
    import subprocess
    import time

    # ⚠️ 必须在**函数内**再 import 一次：``import app_log`` 写在 main() 里只绑到
    #    main 的局部名，这个模块级函数看不到它 —— 以前这里用的是全局名，真走到
    #    "目标文件被占用超时" 这条错误分支时会抛 NameError（本该报错的地方自己崩了）。
    import app_log

    deadline = time.time() + 60
    while True:
        try:
            shutil.copyfile(sys.executable, target)
            break
        except PermissionError:
            if time.time() > deadline:
                app_log.error(f"自动更新失败：目标文件始终被占用 {target}")
                return 1
            time.sleep(0.5)
    subprocess.Popen([target], cwd=os.path.dirname(target) or ".",
                     close_fds=True)
    return 0


def _run_check_net(argv: list[str]) -> int:
    """`--check-net [url]`：不进界面，用**产品同一条网络栈**逐个探版本源。

    为什么不是"取一个 URL 试一下"：这台机器上 cdn.jsdelivr.net 的 **HTTPS 被墙**
    （同址 HTTP 200），而镜像 ghfast.top / GitHub API 都正常 —— 单测一条会得出
    "联网坏了"的错误结论。所以默认把 `updater.version_sources()` 里的源**逐个**
    探一遍，如实列出谁通谁不通；产品里的检查更新就是从这些源里并行取胜者。

    结果写进 exe 同目录的 `net_check.txt`（exe 是 console=False，控制台看不到）。
    退出码：有一条通 = 0，全不通 = 1。
    """
    import app_log

    rest = [a for a in argv[1:] if not a.startswith("--")]
    explicit = rest[0] if rest else None
    out = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "net_check.txt")

    app = QApplication([])                     # QNetworkAccessManager 要 QCoreApplication
    from PySide6.QtNetwork import QNetworkProxy
    QNetworkProxy.setApplicationProxy(
        QNetworkProxy(QNetworkProxy.ProxyType.DefaultProxy))

    def _probe(url: str, timeout_ms: int = 8000) -> dict:
        box: dict[str, object] = {}
        done = {"flag": False}

        def _finish(reply) -> None:
            box["status"] = reply.attribute(
                QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            err = reply.error()
            box["err"] = getattr(err, "value", err)
            body = bytes(reply.readAll())
            box["bytes"] = len(body)
            box["head"] = body[:32].decode("utf-8", "replace").replace("\n", " ")
            reply.deleteLater()
            done["flag"] = True
            app.quit()

        nam = QNetworkAccessManager()
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(timeout_ms)
        nam.finished.connect(lambda r: _finish(r))
        nam.get(request)
        # ⛔ 兜底计时器必须能停：单次 singleShot 会泄漏到下一次 app.exec()，
        #    把下一次探测提前掐断（实测会把"通"误报成"超时"）。
        guard = QTimer()
        guard.setSingleShot(True)
        guard.timeout.connect(app.quit)
        guard.start(timeout_ms + 2000)
        app.exec()
        guard.stop()
        if not done["flag"]:
            box.update({"status": None, "err": "timeout", "bytes": 0, "head": ""})
        return box

    if explicit:
        targets = [("自定义", explicit)]
    else:
        try:
            import updater
            seen: set[str] = set()
            targets = []
            for src in updater.version_sources():
                name, url = src[0], src[1]
                if url in seen:
                    continue
                seen.add(url)
                targets.append((name, url))
        except Exception:                      # noqa: BLE001 - 退化成单条探测
            targets = [("jsDelivr", "https://cdn.jsdelivr.net/gh/Charlie-lee123/"
                                    "CQUFSAEbatteryteam@main/version.py")]

    lines, ok_count = [], 0
    for name, url in targets:
        box = _probe(url)
        good = box.get("err") == 0 and (box.get("status") or 0) == 200
        ok_count += 1 if good else 0
        lines.append(f"{'OK  ' if good else 'FAIL'} {name:16s} "
                     f"status={box.get('status')} err={box.get('err')} "
                     f"bytes={box.get('bytes')}")
    head = (f"{'OK' if ok_count else 'FAIL'} 版本源连通性 "
            f"{ok_count}/{len(targets)} 可用\n")
    text = head + "\n".join(lines) + "\n"
    try:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        app_log.error(f"--check-net 写结果失败: {exc}")
    return 0 if ok_count else 1


def _run_check_vehicles(argv: list[str]) -> int:
    """`--check-vehicles`：不进界面，用车辆库**同一条 urllib 通道**拉一次在线索引。

    与 ``--check-net``（走 Qt QNetworkAccessManager）是**不同**的网络栈。在线车辆库
    的所有请求都走 Python ``urllib``（vehicle_share._open → _race），2026-11-08 曾因
    spec 排除 unicodedata 导致 encodings.idna 加载失败、urllib 全挂
    （"LookupError: unknown encoding: idna"）。本探针直接复现该路径，用来确认
    重建后的产物里 urllib 真的可用。结果写 exe 同目录 ``vehicle_check.txt``。
    """
    import app_log
    import json as _json
    import vehicle_share

    out = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                       "vehicle_check.txt")
    urls = vehicle_share.remote_index_urls()
    payload, source, error = vehicle_share._race(urls, 8.0)
    if payload is None:
        text = f"FAIL urllib 通道不可用\nerror={error}\nsources={list(urls)}\n"
        code = 1
    else:
        try:
            doc = _json.loads(payload)
            packs = doc.get("packs", [])
            text = f"OK source={source} packs={len(packs)}\n"
            code = 0
        except Exception as exc:                     # noqa: BLE001
            text = f"OK-fetch-but-parse-fail source={source} err={exc!r}\n"
            code = 0
    try:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        app_log.error(f"--check-vehicles 写结果失败: {exc}")
    return code


def _run_ble_scan(argv: list[str]) -> int:
    """`--ble-scan [秒数] [manufacturer|service] [key]`：不进界面，扫 BLE 广播。

    两个用处，都很实在：
      1. **验证产物**：打包后的 exe 里 bleak/WinRT 有没有真的打进去（BLE 这套
         依赖是二进制轮子，PyInstaller 漏掉就只能在真机上试才知道）；
      2. **给用户查设备**：把扫到的设备名 / 地址 / 广播负载十六进制打出来 ——
         "我的设备到底发没发、发的是什么" 一眼就能看出来，不用猜。

    只扫描、不连接、不配对（和软件里的蓝牙广播模式是同一条路）。
    """
    import time

    from PySide6.QtCore import QCoreApplication

    seconds, source, key = 6.0, "manufacturer", ""
    rest = [a for a in argv[1:] if not a.startswith("--")]
    if rest:
        try:
            seconds = max(1.0, float(rest[0]))
        except ValueError:
            pass
    if len(rest) > 1:
        source = rest[1]
    if len(rest) > 2:
        key = rest[2]

    # 只给 Qt 程序名：别让它去解析我们的命令行参数
    app = QCoreApplication(argv[:1])
    from ble_io import BleBroadcastWorker

    seen: dict[str, dict] = {}
    worker = BleBroadcastWorker("", source=source, key=key)
    worker.found.connect(lambda info: seen.update({info["address"]: info}))
    worker.open_failed.connect(lambda msg: print("扫描起不来:", msg))
    worker.failed.connect(lambda msg: print("扫描出错:", msg))
    print(f"扫描 BLE 广播 {seconds:.0f} 秒（source={source} key={key or '任意'}）...")
    worker.open()
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    worker.stop()

    rows = [info for info in seen.values() if info.get("payload")]
    lines = [f"扫描 BLE 广播 {seconds:.0f} 秒（source={source} key={key or '任意'}）",
             f"共发现 {len(seen)} 个 BLE 设备，其中 {len(rows)} 个带着广播负载："]
    for info in sorted(rows, key=lambda i: -i["rssi"])[:20]:
        lines.append(f"  {info['address']}  {info['name'] or '(无名)'}"
                     f"  RSSI {info['rssi']}")
        lines.append(f"      负载 = {info['payload'].hex()}")
    if not rows:
        lines.append("  （没有设备在广播里带数据。设备侧要主动把数据放进广播包，"
                     "光配对/连接是收不到的）")

    # ⚠️ 产物是 console=False 的窗口程序，stdout 接不到任何控制台 —— 只打印的话
    #    用户双击运行就什么都看不到。所以同时写一份到产物目录，并把路径说出来。
    #    （app_log 要先初始化才知道日志目录在哪；这一步在上面打印之前其实是空的。）
    import app_log

    app_log.init()
    text = "\n".join(lines)
    path = ""
    try:
        log_dir = os.path.dirname(app_log.path() or "")
        if log_dir:
            path = os.path.join(log_dir, "蓝牙扫描结果.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
    except OSError:
        path = ""
    print(text)
    if path:
        print(f"\n（结果也写到了：{path}）")
    return 0


def main() -> int:
    # 开屏动画只带 Windows Media Foundation 后端（ffmpeg 后端已从包里剔除，省约
    # 10MB）。必须在**第一次 import QtMultimedia 之前**设好 —— splash_anim 是
    # 惰性导入的，所以放在 main() 最前面足够。
    os.environ.setdefault("QT_MEDIA_BACKEND", "windows")
    # 自动更新的自举分支：不做任何 GUI，覆盖完目标就退出
    target = None
    if "--apply-update" in sys.argv:
        from updater import parse_apply_update_args
        target = parse_apply_update_args(sys.argv)
    if target:
        import app_log
        app_log.init()
        app_log.info(f"自动更新：接管替换流程，目标 {target}")
        return _run_apply_update(target)

    # 命令行探针：用产品同一条网络栈取一次 HTTPS（验证"去掉 Qt OpenSSL 后端
    # 之后更新检查还能不能连"——这是 2.4 瘦身唯一有联网风险的改动）
    if "--check-net" in sys.argv:
        return _run_check_net(sys.argv)

    # 命令行探针：用车辆库**同一条 urllib 通道**拉一次在线索引（验证 2026-11-08
    # 的 unicodedata 排除导致 encodings.idna 失效、urllib 全挂的修复是否真的生效）。
    if "--check-vehicles" in sys.argv:
        return _run_check_vehicles(sys.argv)

    # 命令行探针：只扫 BLE 广播、不进界面（验证产物 / 查自己的设备）
    if "--ble-scan" in sys.argv:
        return _run_ble_scan(sys.argv)

    # MCP 接入包导出：不进界面，把"交给 Agent 自动部署"的那段文本写成文件。
    # console=False 打不出字，所以走文件（也是验证"源码真的进了包"的落地证据）。
    if "--mcp-payload" in sys.argv:
        import mcp_bridge
        rest = [a for a in sys.argv[1:] if not a.startswith("--")]
        target = rest[0] if rest else None
        written = mcp_bridge.write_payload(target)
        print(written)
        return 0

    # 日志最先初始化：后面任何一步（含窗口构造）的异常都能留痕
    import app_log
    app_log.init()
    app_log.info("=" * 8 + f" 软件启动 v{__version__} " + "=" * 8)

    # 高 DPI / 多屏适配：固定缩放取整策略为 PassThrough，使 125%/150%/175% 等分数
    # 缩放下也按物理像素精确缩放（不留整数取整导致的模糊）；整数缩放（如本机 dpr=2）
    # 行为不变。必须在 QApplication 创建前设置。
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:  # noqa: BLE001 - 极老 Qt 无该 API 时静默跳过
        pass

    app = QApplication(sys.argv)
    _aud("qapp")  # QApplication 创建完成
    app.setApplicationName("CQUFSAE串口助手")
    app.setOrganizationName("MimoWorks")
    # 窗口与任务栏图标。这一步不能省：--icon 只设了 exe 文件自身的图标，
    # 运行时的窗口图标要在这里显式给。
    app.setWindowIcon(app_icon())
    # 用 Fusion 打底：Windows 原生样式对自定义样式表的响应有不少怪脾气，
    # Fusion 更可预期，配色也由我们自己完全接管。
    app.setStyle("Fusion")
    # 先定主题再建窗口，窗口构造时拿到的就是正确配色，不会闪一下再换。
    # ⛔ 主题由用户存的 theme_mode 决定（system=跟随系统，否则钉死浅/深），
    #    **不是**永远跟随系统。开屏动画在 MainWindow 把 theme_mode 读出来、_apply_theme
    #    跑之前就播，所以这里先用同一个有效主题把全局 _SCHEME 定下来 —— 否则固定浅/
    #    深色的用户开屏仍是系统主题那一版（用户："换主题后开屏颜色不跟着变"）。
    _theme_mode = str(QSettings("MimoWorks", "SerialAssistant").value(
        "theme_mode", "system") or "system")
    _scheme = detect_system_scheme() if _theme_mode == "system" else _theme_mode
    apply_scheme(_scheme)
    app.setFont(ui_font(12.5))
    # 动画帧率（设置里可无级调，上限跟显示器刷新率走）：必须在建窗口**之前**生效，
    # 否则首屏入场动画会先按默认 120fps 跑一段再切过去。
    import ui_anim
    # 2026-11-02 用户：「帧率要和我的屏幕最高刷新率一样」。旧默认 120 低于高刷屏
    # （本机 165Hz）—— 没主动改过（仍等于旧默认 120）就升到显示器刷新率；
    # 用户把滑杆调到别的值仍照他用（set_frame_hz 里再夹到 [24, 屏幕上限]）。
    _fps = int(customization.get("anim_fps"))
    if _fps == 120:
        _fps = ui_anim.display_max_hz()
    ui_anim.set_frame_hz(_fps)

    # 字体引擎冷启动要 ~600ms，放在后台线程与开屏预热/播放并行 —— 否则它会
    # 在主窗构造时爆发，把「向左上角落点」整段动画吃掉（见函数注释）。
    # ⚠️ --check-* 验证入口不建主窗、不播完整动画，且进程退出时 Qt 清理字体库
    #    会额外等这根线程（实测 --check-splash 虚增 ~1.1s），故只在正常启动开。
    if not any(a.startswith("--check-") for a in sys.argv[1:]):
        _prewarm_font_engine()

    # 打包验证入口：播一遍开屏、1.2s 后退出。用来确认 QtMultimedia 后端、ffmpeg
    # 剔除后的 windowsmediaplugin、以及 assets/splash 预设都真的进了 exe。
    if "--check-splash" in sys.argv:
        import splash_anim
        preset = splash_anim.configured()
        if preset is None:
            print("splash: none")
            return 0
        player = splash_anim.play(preset)
        if player is None:
            print("splash: failed")
            return 1
        # ⛔ 不要在这里局部 ``import QTimer``：那会让 Python 把 QTimer 当成
        #    main() 的**局部变量**，而 main() 后面（_build_window 的定时构造）
        #    也用 QTimer——赋值前访问 = UnboundLocalError，真实启动直接崩
        #    （2026-11-03 用户截图实证；--check-splash 分支自身提前 return
        #    反而测不出来）。QTimer 文件顶部已 import。
        QTimer.singleShot(1200, app.quit)
        app.exec()
        print(f"splash: ok ({preset['id']})")
        return 0

    window = None
    _launched = False

    def _reveal_main(sync: bool = False) -> None:
        """把主窗放出来。**开屏还在播的时候就调** —— 主界面在开屏后面打开，
        开屏淡出时直接露出来，不会等动画结束才开始建/显示（用户："播放开屏动画
        的时候你要顺便就把串口界面给打开了…响应慢了一点点"）。

        ⛔ 2026-11-04（用户：「开屏动画的最后卡了」）：``sync=True`` 时
        ``show`` + ``apply_startup_state``（全屏落地，系统调用 ~200ms）+ 全屏
        首帧 ``repaint()`` **同步做完**，赶在开屏淡出之前把主窗首帧画好 ——
        否则那笔重排+重绘会落在播放尾部（实测尾部连跳 skip=3~4 帧）。
        od46：几何动画改用 dt-clamp 推进（构造期时钟不再冻结），这 ~300ms
        主线程任务只会让动画进度按 DT_MAX 缓慢爬，不会定格、不会跳帧。
        ``sync=False``（无开屏时的常规启动）仍拆两拍，避免启动卡顿。
        """
        nonlocal _launched
        if _launched:
            return
        _launched = True
        window.show()
        if sync:
            window.apply_startup_state()
            # 全屏版式首帧：否则主窗表面要到开屏撤走时才第一次绘制，
            # 那笔重排+重绘又会落在播放尾部（同 skip 来源）。
            window.repaint()
            # 任务8：配合 main_window 组，在几何动画 dt-clamp 期间补建一次全屏空闲
            # 快照（另一组保证该方法可重入/自重试）。⚠️ 用 QTimer 延后到下一轮事件
            # 循环再做：抓屏 51~130ms，若同步做会卡在「用户看到首帧」的启动关键路径
            # 上（实测 od46 启动回退 ~0.4s 的元凶之一）。开屏还在播（空白段 0.3s），
            # 下一轮再抓完全看不见、且 snapshot 仍远早于用户操作。失败绝不能挡启动。
            try:
                QTimer.singleShot(0, window._build_fs_idle_pix)
            except Exception:            # noqa: BLE001 - 快照失败不挡启动
                pass
            app_log.info("软件就绪（起手姿态：%s）"
                         % ("整窗全屏" if customization.get("startup_maximized")
                            else "普通窗口"))
            return
        QTimer.singleShot(0, lambda: (
            window.apply_startup_state(),
            app_log.info("软件就绪（起手姿态：%s）"
                         % ("整窗全屏" if customization.get("startup_maximized")
                            else "普通窗口"))))

    # 开屏动画（接口层 splash_anim）：**先起开屏，主窗在后面初始化** ——
    # 点开就有画面，不用干等 MainWindow 构造（玻璃字体布局约 1s）。
    # 没配置/文件坏了/缺 QtMultimedia 都返回 None → 直接建主窗显示，绝不挡启动。
    _splash = None
    try:
        import splash_anim
        # 双击后的初始化间隙先预热（仅源码调试环境有意义）：cv2 已从打包产物
        #    砍掉后 prewarm_async() 直接返回；planB 走原生几何动画，所有位图与
        #    模糊档在 MotionRenderer._preheat() 里一次性算好，播放期零重渲染。
        try:
            splash_anim.prewarm_async(16)
        except Exception:                 # noqa: BLE001 - 预热失败不影响启动
            pass
        _splash = splash_anim.play_configured()
    except Exception as exc:              # noqa: BLE001 - 开屏失败不能挡住启动
        app_log.warning(f"开屏动画启动失败：{exc}")

    def _preload_main_module() -> None:
        """在开屏的 0.3s 纯底/淡入段把 main_window 全家的 import 预热掉。

        ⛔ 2026-11-04（用户：「开屏动画会在两个 logo 变换的时候卡一两秒」）：
        ``from main_window import MainWindow`` 是一次 ≈312ms 的同步阻塞，
        原先在 _build_window（定版静止段的定时器回调）里才付——和构造叠在
        一起 ~1.2s，静止段吞不下就溢出到运动段 = 肉眼卡顿。import 提前到
        事件循环第一拍（此时画面还是纯底 lead），阻塞不可见；构造主窗时
        只剩 MainWindow() 本体的开销。
        """
        try:
            import main_window  # noqa: F401 - 只预热模块 import，构造仍走 _build_window
        except Exception as exc:          # noqa: BLE001 - 预热失败不影响启动
            app_log.warning(f"main_window 预加载失败：{exc}")

    if _splash is not None:
        QTimer.singleShot(0, _preload_main_module)

    def _build_window() -> None:
        """构造主界面并显示（幂等）。

        ⛔ 2026-11-03 用户：「有点模糊和卡顿」。此前是在 main() 里用
        ``processEvents + sleep`` **忙等**到开屏视频的静止段再同步构造 ——
        那段忙等会把开屏的帧定时器压成不均匀的节奏（画面一顿一顿）。
        现在改成 **QTimer 定时构造**：先返回 app.exec() 让事件循环正常跑，
        开屏按源帧率满速播；到静止段时定时器再触发构造（构造的 ~937ms
        正好被视频自带的 0.8s 静止吃掉）。定时器不准也只会偏早/偏晚一点，
        不会再影响开屏帧率。
        """
        nonlocal window
        if window is not None:
            return
        # ⛔⛔ main_window 全家桶（≈312ms）在这里才 import：开屏窗口已经在屏幕上，
        #    这段加载不挡"点开就有画面"。
        from main_window import MainWindow
        import main_window as _mw
        # 开屏期间让构造切段让出事件循环：开屏现在每帧只要 ~2.5ms（纯曲线 +
        # 全预热），让出一次的代价从 8ms 降到 2.5ms，并行终于划算了。
        _mw._BUILD_PUMP = _splash is not None
        try:
            window = MainWindow()
        finally:
            _mw._BUILD_PUMP = False
        _aud("mainwindow_constructed")  # 主窗构造完成
        # SPLASH_AUDIT 计量模式：主窗构造完即让若干开屏动画帧跑完再退出，
        # 以便捕获 first_tick 等里程碑；正常运行（无该 env）不受影响。
        if _AUDIT_ON:
            QTimer.singleShot(120, app.quit)
        # 把真实主窗口交给开屏播放器：收尾段按真实 UI logo 位置做纠正贴合
        # （Plan B 跨机型精确落位，见 splash_anim.set_logo_window）。
        if _splash is not None:
            try:
                _splash.set_logo_window(window)
            except RuntimeError:          # 开屏已被点掉
                pass
        # 开屏期间把**启动动画整个跳过**（不跑 260ms 真窗口几何补间、不淡入），
        # 主窗直接就位。⛔ 不要用 setUpdatesEnabled(False)：主窗没画出首帧 →
        # DWM 里没有有效表面，开屏结束 raise/activate 会失败。
        if _splash is not None:
            window._skip_startup_anim = True
            # ⛔⛔ 顺序（2026-11-04 用户：「开屏动画的最后卡了」）：show + 全屏
            #    落地 + 首帧画完这三步**同步做完**，赶在开屏淡出之前，否则那
            #    ~300ms 重排/重绘会落在播放尾部（旧版实测尾部 skip=3~4 帧连跳）。
            _reveal_main(sync=True)
            # 把主窗页头 lockup 的**真实几何**交给开屏当落点，并放行淡出：
            # 动画播完后画面停在落定帧等构造完成，落定帧与页头 logo 完全重合，
            # 这段「静止」才看不出来（否则就是用户说的「中间怎么卡了一下」）。
            # od46：构造期不再冻结播放时钟 —— 几何动画靠 _motion_tick 的
            # dt-clamp 缓慢推进，构造完成后 set_land_from_window 注入真实落点，
            # 播完才放行淡出。这里没有 pause/resume，也没有 200ms 解冻定时器。
            try:
                _splash.set_land_from_window(window)
            except RuntimeError:          # 开屏已被点掉
                pass
        else:
            _reveal_main()

    def _on_splash_done() -> None:
        """开屏播完/被点掉：确保主窗已建出并聚焦（兜底，幂等）。"""
        _build_window()
        if window is not None:
            try:
                # ⛔ 解除「跳过启动动画」：开屏已经没了，用户**第一次**手动点
                #    最大化没有理由再走无动画的早退分支。
                window._skip_startup_anim = False
                # 补建全屏空闲快照（还原方向的覆盖层"旧画面"）：开屏期间一直
                # 没排（见 main_window._schedule_fs_idle_snap —— 整屏 BitBlt
                # 会砸在开屏动画尾部 = 用户报的"最后卡了"）。1500ms 而不是
                # 600ms：开屏刚撤时主窗正在首次上屏/合成，别让这 130ms 抓屏
                # 紧跟着砸上去。
                window._fs_idle_pix = None
                # ⛔ 200ms 而不是 1500ms：1500 是帧序列时代的妥协（怕整屏抓屏
                #    砸在开屏尾部的解码上）。开屏改原生几何动画后尾部没有解码，
                #    但**这个延迟留着就会出事** —— 用户开屏一结束就点最大化时
                #    快照还没建，`_on_escape` 只能走「无快照回退」（纯原生 +
                #    `_nccalc_preserve`），观感就是「会闪、不连续、不流畅」。
                QTimer.singleShot(200, window._build_fs_idle_pix)
                # ⚠️ 不要把这步延后（2026-11-04 实测：延后 120ms 反而把 skip
                #    从 13 抬到 17~18，尾部还多出一处）。这一步在开屏淡出结束、
                #    帧定时器已停之后执行，不产生 skip。
                window.raise_()
                window.activateWindow()
            except RuntimeError:
                pass

    def _on_fade_start() -> None:
        """B2：淡出**开始前**把主窗置前/激活，消除撤场↔激活的 z-order 空窗期。

        开屏是 topmost 全屏窗，_close() 一销毁其表面 DWM 立即重合成；而
        raise_()/activateWindow() 原本只在 _on_splash_done（由 _close() 末尾
        触发）里做 ⇒ 存在「开屏已撤、主窗尚未置前」的空窗期，真机录到 DWM
        用 8 帧/~0.09s 才把主窗逐帧合成完整（B2 独立闪源）。

        ⛔⛔ 别再重犯这三个误区（2026-11-06 真机实验
        devtools/_probe_b2_early_activate.py，GetForegroundWindow A/B 定性）：
        1) activateWindow() 设的是**前台窗口/foreground**，
           WindowStaysOnTopHint 管的是 **z-order 层级**，两者互不相干。
           提前激活后开屏仍 topmost 可见 = 期望行为，不是「被压住而无效」，
           用户在这 300ms 里什么都看不见，但主窗已拿到前台权限。
        2) 不存在 z-fight：没有窗口被反复抬升、不改任何 flags。close() 撤场
           那一刻主窗已是前台，不必再跨进程抢 SetForegroundWindow，也就不会
           和 DWM 重合成撞同一帧。
           机制证据 = devtools/_probe_b2_early_activate.py 的
           GetForegroundWindow A/B：提前激活组 fg==main、开屏仍可见 True，
           两次复跑一致，证明「提前激活有效且可控」。
           ⚠️ 该探针的对照组 close 后**没有**调 activateWindow()，而产品
           _on_splash_done 是会调的（只是排在 close 之后、受 Windows 前台
           锁定影响）。所以产品病灶是「激活太晚、与 DWM 撤场抢同一帧 +
           跨进程前台锁定」，由诊断专员真机抓帧（8 帧/~0.09s 逐帧合成）
           支撑。⛔ 别把对照组表述成「产品完全没激活」。
        3) 不会牵动 _reveal_main(sync=True) 时序：本回调由 finish() 在淡出
           开始前触发，那时 _reveal_main 早已同步做完（show + 全屏落地 +
           repaint 首帧）；_build_window() 幂等直接返回。

        ⚠️ 提前跳过（点击/按键）路径下，若 _build_at_still 尚未触发，
        _build_window() 会在 finish() 内同步构造主窗（~1.2s 额外耗时）；
        此时开屏仍不透明且 topmost，用户看不见，随后才跑 300ms 淡出。
        ⛔ _on_splash_done 里的 raise_()/activateWindow() 保留作幂等兜底。
        """
        try:
            _build_window()                # 幂等；正常时序下主窗早已就位
            if window is not None:
                try:
                    window.raise_()
                    window.activateWindow()
                except RuntimeError:       # 与 _on_splash_done 一致的兜底写法
                    pass
        except Exception as exc:           # noqa: BLE001 - 失败必须留痕
            # ⛔ finish() 把 fade_cb 包在 except Exception: pass 里；
            #    不在这里记日志，B2 会无声退化且日志无痕。
            # app_log 是 main() 内 :321 的**局部** import（非模块级，见 :97
            #    的历史 NameError 教训）；本函数嵌在 main() 里，按闭包读取即可，
            #    ⛔ 别把它提成模块级函数。
            app_log.warning(f"开屏淡出前主窗置前失败：{exc!r}")

    if _splash is not None:
        # 兜底先挂上：开屏被用户点掉/提前结束时也必须有主窗
        _splash.set_on_finished(_on_splash_done)
        # ⛔ B2：淡出开始前的置前挂钩，见 _on_fade_start（真机实验依据：
        #    devtools/_probe_b2_early_activate.py）。只挂一次，⛔ 不再挂第二个
        #    set_on_finished；_on_splash_done 的 raise/activate 保留作兜底。
        _splash.set_on_fade_start(_on_fade_start)
        # 到「天然静止段」再构造（delay=None 表示没帧序列/没静止段 → 立即构造）：
        # od46：几何分支不再 pause_clock / 200ms 后 resume。构造期靠
        # _motion_tick 的 dt-clamp 消化（每次最多推进 DT_MAX=2.5/hz），构造的
        # ~1.2s 里 _mt 缓慢连续爬升：既不出现 Δt=0 长游程、也不跳帧缺帧，
        # 构造完注入真实落点、播完才放行淡出。帧序列路径的 pause_clock /
        # resume_clock 方法本身保留在 splash_anim（custom 仍可能用），只是几何
        # 分支不再调用 —— 只改几何分支、风险隔离。
        _delay = splash_anim.still_delay_ms(_splash)
        if _delay is None:
            _build_window()
        else:
            def _build_at_still() -> None:
                _build_window()
            QTimer.singleShot(max(0, int(_delay)), _build_at_still)
    else:
        _build_window()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
