"""SOC 链路单测：查表 / 清洗 / 估算 / 报文拼装 / 界面居中。

三条纪律：
1. **绝不写用户真实配置**。需要"当前车辆是 E74"时用 ``_pin_vehicle`` 把
   ``customization.current_vehicle`` 打成只读返回值，不调 ``select_vehicle``
   （它会落盘）。历史目录与会话库也重定向到临时目录。
2. 每条断言独立编号，失败不影响后面的用例。
3. 只报"通过/失败 + 明细"，退出码 0/1（可直接进 CI）。
4. 默认跑 **windows** 平台（``setdefault`` 到 ``windows``），⛔ 别改回 offscreen：
   E4/E5/E24/E26/E27 比的是**真实字体渲染出来的宽度**。offscreen 平台没有
   字体数据库，所有字符都退化成"字号见方"的方块占位 —— 实测同一枚
   ``SOC --`` 量出 96px（真机 50px），四枚标签在 820 宽窗口里根本放不下，
   于是 _place_soc 只能贴左、E24 必假红。要强制别的平台就显式给
   ``QT_QPA_PLATFORM``（此时几何用例不保证通过，见上面的原因）。

跑法：``.venv/Scripts/python.exe devtools/_verify_soc.py``
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), detail))


def close(name: str, got, want, tol) -> None:
    try:
        diff = abs(float(got) - float(want))
    except (TypeError, ValueError):
        check(name, False, f"无法比较：got={got!r} want={want!r}")
        return
    check(name, diff <= tol, f"got={got} want={want} 差={diff:.4f} 容差={tol}")


# ---------------------------------------------------------------- A 查表

def test_table() -> None:
    import soc_est as S

    close("A1 SOC=0 → OCV 3.0818", S.ocv_from_soc(0), 3.0818, 1e-9)
    close("A2 SOC=100 → OCV 4.4981", S.ocv_from_soc(100), 4.4981, 1e-9)

    table = S.OCV_TABLE
    mono = all(table[i][0] > table[i - 1][0] and table[i][1] > table[i - 1][1]
               for i in range(1, len(table)))
    check("A3 OCV 表严格单调递增", mono, f"{len(table)} 点")

    worst = 0.0
    for i in range(0, 1001):
        s = i / 10.0
        worst = max(worst, abs(S.soc_from_ocv(S.ocv_from_soc(s)) - s))
    check("A4 查表往返误差 < 0.5%", worst < 0.5, f"最大往返误差 {worst:.4f}%")

    close("A5 表外不外推（低端）", S.ocv_from_soc(-30), 3.0818, 1e-9)
    close("A6 表外不外推（高端）", S.soc_from_ocv(9.9), 100.0, 1e-9)
    close("A7 斜率：平台段约 4.9 mV/%", S.slope_at(30), 4.90, 0.15)
    close("A8 κ 平台段 < 0.6", S.kappa_at(25), 0.42, 0.06)
    close("A9 κ 驼峰段 = 1.0", S.kappa_at(70), 1.0, 1e-9)

    bad = ((0, 3.0), (5, 2.9))          # OCV 递减 → 表不可用
    check("A10 table_ok 拒绝非单调表", not S.table_ok(bad))
    check("A11 table_ok 接受正式表", S.table_ok(S.OCV_TABLE))


# ---------------------------------------------------------------- B 清洗

def test_clean() -> None:
    import soc_est as S

    clean = [3200] * 125
    r = S.clean_cells(clean)
    close("B1 全健康：均值 3200", r.mean_mv, 3200.0, 1e-9)
    close("B2 全健康：gap 0", r.gap_mv, 0.0, 1e-9)
    check("B3 全健康：无回填", not r.bad_idx and r.ok, f"bad={len(r.bad_idx)}")

    cells = list(clean)
    cells[7] = 5000                     # 超上限
    cells[60] = 120                     # 断线典型（读成很低的值）
    r = S.clean_cells(cells)
    close("B4 超量程被剔除（均值不变）", r.mean_mv, 3200.0, 1e-9)
    check("B5 两节被回填", r.bad_idx == [8, 61], f"bad_idx={r.bad_idx}")
    check("B6 回填后整串长度恒为 125", len(r.cells) == 125, f"{len(r.cells)}")
    close("B7 回填值 == 健康均值", r.cells[7], 3200.0, 1e-9)

    cells = list(clean)
    cells[23] = 2744                    # 值在量程内，但设备打了 X
    mask = [False] * 125
    mask[23] = True
    r = S.clean_cells(cells, mask)
    check("B8 X 标记即使值合法也判坏", r.bad_idx == [24], f"bad_idx={r.bad_idx}")

    cells = list(clean)
    for i in range(125):
        if i % 25 == 0:
            cells[i] = None             # 每个模组缺第 1 节
    r = S.clean_cells(cells)
    check("B9 缺失（None）被回填", len(r.bad_idx) == 5, f"bad={len(r.bad_idx)}")
    check("B10 缺失计数正确", r.n_unusable == 5, f"n_unusable={r.n_unusable}")

    cells = list(clean)
    cells[3] = 4000                     # 量程内、但离群 800 mV
    r = S.clean_cells(cells)
    check("B11 量程内离群被 MAD 抓到", r.bad_idx == [4], f"bad_idx={r.bad_idx}")

    # 整模组偏高 100 mV：真实不均衡，**不该**被当坏点全回填
    cells = [3300] * 40 + [3200] * 85
    r = S.clean_cells(cells)
    check("B12 整模组偏移不被误判为坏点", len(r.bad_idx) == 0,
          f"bad={len(r.bad_idx)} limit 被 IQR 撑开")
    check("B13 整模组偏移触发 RESIDUAL 告知", r.residual,
          f"gap={r.gap_mv:.2f} mV > {S.MEAN_MED_MAX_MV}")

    # B12/B13 的 40 节是 32%，**不能**代表最字面的"一个整模组"（25 节 = 20%）。
    # 20% 少数派会让 quantiles(n=4) 的 q3 仍落在多数派里、IQR 塌成 0，MAD 也塌
    # 成 0 —— 门槛退化成 20 mV 死线，整模组被当断线回填、总压少报、且不报警。
    # 这条是独立验收（QA）抓出来的，别再删。
    for ncell, amp in ((25, 100), (25, 21), (20, 50), (24, -100), (30, 30)):
        cells = [3200 + amp] * ncell + [3200] * (125 - ncell)
        r = S.clean_cells(cells, n_cells=125, cells_per_module=25)
        # 总压 = 125 节均值 × 125，不是 100 节基数（早期算错过，别改回来）
        want = (125 * 3200.0 + amp * ncell) / 1000.0
        # 整模组偏移时中位数仍落在多数派 3200 mV，均值偏移量 = amp × ncell / 125
        want_gap = abs(amp) * ncell / 125.0
        check(f"B18 整模组偏移 {ncell} 节×{amp:+}mV 不回填",
              len(r.bad_idx) == 0, f"bad={len(r.bad_idx)}")
        close(f"B19 整模组偏移 {ncell} 节×{amp:+}mV 总压正确",
              r.pack_mv / 1000.0, want, 0.001)
        # 静默判据要分两层：① 必须标 module_offset（不能假装没事）；
        # ② residual 只取决于均值-中位数差有没有过 8 mV —— 25 节×21 mV 只偏 4.2 mV，
        #    对电压回拉几乎没影响，此时 residual=False 才是对的，别强求两者同真。
        check(f"B20 整模组偏移 {ncell} 节×{amp:+}mV 静默判据正确",
              r.module_offset and abs(r.gap_mv - want_gap) < 0.01
              and r.residual == (want_gap > S.MEAN_MED_MAX_MV),
              f"module_offset={r.module_offset} gap={r.gap_mv:.2f}/{want_gap:.2f} "
              f"residual={r.residual}")

    # 反向：真正的连续多节断线（短于半个模组）仍必须回填
    cells = [3200] * 125
    for k in range(5):
        cells[40 + k] = 0
    r = S.clean_cells(cells, n_cells=125, cells_per_module=25)
    check("B21 连续 5 节坏点仍回填（短于半个模组）",
          r.bad_idx == [41, 42, 43, 44, 45] and not r.module_offset,
          f"bad={r.bad_idx} module_offset={r.module_offset}")

    # ⛔ 非对称防护：**整个模组采样线断了**（25 节全 0 mV）必须回填，绝不能
    # 因为"连续 25 节同向"被当成模组偏移保留 —— 否则总压会塌成 320 V、
    # SOC 直接显示 0%，把接线故障伪装成"电池没电"。
    # 机制：超量程在打标阶段就是 flag=None，None 一律 keep=False → 必然回填。
    cells = [0] * 25 + [3200] * 100
    r = S.clean_cells(cells, n_cells=125, cells_per_module=25)
    check("B22 整模组断线（25 节全 0mV）仍回填",
          len(r.bad_idx) == 25 and not r.module_offset,
          f"bad={len(r.bad_idx)} module_offset={r.module_offset}")
    close("B23 整模组断线 → 总压用健康均值", r.pack_mv / 1000.0, 400.0, 0.001)

    # 对照：同样连续 25 节、但**在量程内**的偏移是真值，必须保留
    cells = [2800] * 25 + [3200] * 100
    r = S.clean_cells(cells, n_cells=125, cells_per_module=25)
    check("B24 整模组量程内偏移（-400mV）保留真值",
          not r.bad_idx and r.module_offset,
          f"bad={len(r.bad_idx)} module_offset={r.module_offset}")
    close("B25 整模组量程内偏移 → 总压是真值", r.pack_mv / 1000.0, 390.0, 0.001)

    r = S.clean_cells([0] * 125)
    check("B14 全坏 → ok=False", not r.ok)
    close("B15 全坏 → 总压 0", r.pack_mv, 0.0, 1e-9)

    r = S.clean_cells([3200] * 125)
    close("B16 总压 == 均值 × 125", r.pack_mv, 3200.0 * 125, 1e-9)

    # 长度对不上（设备少发一帧）不该整轮作废
    r = S.clean_cells([3200] * 100, n_cells=125)
    check("B17 短数组按 n_cells 补齐", len(r.cells) == 125 and r.ok,
          f"len={len(r.cells)} ok={r.ok}")


# ---------------------------------------------------------------- C 估算

def test_estimator() -> None:
    import soc_est as S

    cfg = S.SocCfg("E74")

    est = S.SocEstimator(cfg)
    r = est.update([3200.0] * 125, [45] * 40, i_meas=0.0, ts=0.0,
                   contactor_open=True)
    close("C1 冷启动按 OCV 定初值", r.soc, S.soc_from_ocv(3.2), 0.05)
    check("C2 冷启动被标记", r.initialised, "")

    # 安时积分：1C 放电 60 s 应掉 12×60/3600/12.041×100 = 1.66%
    # ⚠️ 端电压必须**跟着真实 SOC 一起走**（OCV 减 IR 压降）。若把电压钉死在
    #    起始值，电压回拉会一路对抗积分并留一个 0.0554/0.02≈2.8% 的稳态滞后
    #    —— 那不是积分错，是测试构造了一个物理上不存在的场景。
    def cell_mv(soc: float, amps: float) -> float:
        return (S.ocv_from_soc(soc) - amps * 0.0068) * 1000.0

    def run_discharge(start: float, amps: float, steps: int, dt: float,
                      bias: float = 0.0) -> float:
        """按真实 SOC 轨迹喂 steps 个 dt 步，返回 SOC 变化量。

        ``bias`` 是传感器零漂：真实电流 ``amps`` 时**读数**是 ``amps + bias``，
        估算器扣掉零漂后应还原出 ``amps``。电压按真实 ``amps`` 算。
        """
        est = S.SocEstimator(cfg)
        est.reset(soc=start)
        est.i_bias = bias
        reading = amps + bias
        t = -dt                                  # 预热一步，让第一段也有 dt
        true = start
        est.update([cell_mv(true, amps)] * 125, [40] * 40, i_meas=reading,
                   ts=t, contactor_open=False)
        for _ in range(steps):
            t += dt
            est.update([cell_mv(true, amps)] * 125, [40] * 40, i_meas=reading,
                       ts=t, contactor_open=False)
            true -= amps * dt / 3600.0 / cfg.q_ah * 100.0
        return est.soc - start

    close("C3 1C 放电 60s ≈ -1.66%", run_discharge(80.0, 12.0, 30, 2.0),
          -1.66, 0.10)

    # 充电方向
    check("C4 负电流 = 充电（SOC 上升）",
          run_discharge(50.0, -12.0, 30, 2.0) > 0.0,
          f"{run_discharge(50.0, -12.0, 30, 2.0):.2f}")

    # 接触器全断 → 真实电流 0 → 零漂被学走、SOC 不动
    est = S.SocEstimator(cfg)
    est.reset(soc=50.0)
    t = 0.0
    for _ in range(40):
        est.update([cell_mv(50.0, 0.0)] * 125, [40] * 40, i_meas=1.89, ts=t,
                   contactor_open=True)
        t += 2.0
    close("C5 接触器全断时 SOC 不动", est.soc, 50.0, 0.35)
    close("C6 零漂收敛到读数 1.89", est.i_bias, 1.89, 0.05)

    # 接触器闭合后扣掉零漂：读数 13.89 = 真实 12 A
    close("C7 扣零漂后与 12A 放电一致",
          run_discharge(80.0, 12.0, 30, 2.0, bias=1.89), -1.66, 0.10)

    # dt 超上限不积分（暂停/断线恢复）
    est = S.SocEstimator(cfg)
    est.reset(soc=60.0)
    est.update([cell_mv(60.0, 12.0)] * 125, [40] * 40, i_meas=12.0, ts=0.0,
               contactor_open=False)
    est.update([cell_mv(60.0, 12.0)] * 125, [40] * 40, i_meas=12.0, ts=600.0,
               contactor_open=False)
    close("C8 dt=600s 不积分", est.soc, 60.0, 0.35)

    # 静置锚定：从 20% 的错误初值收敛到 50%
    est = S.SocEstimator(cfg)
    est.reset(soc=20.0)
    fired = False
    for i in range(150):
        r = est.update([3903.8] * 125, [45] * 40, i_meas=0.0, ts=i * 2.0,
                       contactor_open=False)
        fired = fired or r.anchored
    check("C9 静置锚定能触发", fired, "")
    close("C10 锚定后收敛到 50%", est.soc, 50.0, 0.5)

    # 电压还在爬时不锚定（满放后回弹）
    import math
    est = S.SocEstimator(cfg)
    est.reset(soc=0.0)
    anchored = False
    for i in range(180):
        v = 3.0811 + (3.4303 - 3.0811) * (1 - math.exp(-i * 2.0 / 60.0))
        r = est.update([v * 1000.0] * 125, [45] * 40, i_meas=0.0, ts=i * 2.0,
                       contactor_open=False)
        anchored = anchored or r.anchored
    check("C11 回弹未稳时不锚定", not anchored, f"末 SOC={est.soc:.2f}%")

    # 数据不可用时不更新 SOC
    est = S.SocEstimator(cfg)
    est.reset(soc=42.0)
    r = est.update([0] * 125, [40] * 40, i_meas=0.0, ts=0.0, contactor_open=True)
    check("C12 全坏 → flag=NO_DATA", r.flag == "NO_DATA", r.flag)
    close("C13 全坏 → SOC 保持", r.soc, 42.0, 1e-9)

    # RESIDUAL 时不采信电压回拉
    est = S.SocEstimator(cfg)
    est.reset(soc=30.0)
    cells = [3300.0] * 40 + [3200.0] * 85
    r = est.update(cells, [40] * 40, i_meas=0.0, ts=0.0, contactor_open=False)
    check("C14 残留异常 → flag=RESIDUAL", r.flag == "RESIDUAL", r.flag)
    close("C15 RESIDUAL 时不做电压回拉", r.soc, 30.0, 1e-9)

    # 闭环：2 s 采样、1C 全程放电，跟踪自洽（**不是**绝对精度，见表自证）
    est = S.SocEstimator(cfg)
    est.reset(soc=100.0)
    t = 0.0
    worst = 0.0
    for k in range(51):
        true_soc = 100 - 2.0 * k
        v = (S.ocv_from_soc(true_soc) - 12.0 * 0.0068) * 1000.0
        n = int(round(0.02 * 12.041 / 12.0 * 3600 / 2.0))
        for _ in range(n):
            r = est.update([v] * 125, [45] * 40, i_meas=12.0, ts=t,
                           contactor_open=False)
            t += 2.0
        worst = max(worst, abs(r.soc - true_soc))
    check("C16 闭环跟踪偏差 < 2%", worst < 2.0, f"最大 {worst:.2f}%")
    close("C17 闭环末端回到 0%", est.soc, 0.0, 0.5)

    # 换车 = 换 cfg
    other = S.SocCfg("X01", q_ah=8.0, n_cells=80, cells_per_module=16)
    check("C18 可为别的车建参数", other.n_cells == 80 and other.q_ah == 8.0)
    check("C19 坏配置返回 None", S.SocCfg.from_dict({"q_ah": 0}) is None)
    check("C20 非单调配置返回 None",
          S.SocCfg.from_dict({"ocv_table": [[0, 3.0], [10, 2.0]]}) is None)


# ---------------------------------------------------------------- D 报文

def test_feed() -> None:
    import soc_feed

    text = "\n".join([
        "Voltage1:", "01.3180   02.3176   03.3206",
        "Voltage2:", "01.3212   02.3207   03.3221",
        "Temperature1:", "1.41   2.41",
        "BMS OK", "COM0=0", "AIR+=0,AIR-=0,PRE=0", "V=0.00,I=1.89",
        "CVMax=5446, Min=0",
    ])
    rounds = soc_feed.parse_text(text, n_modules=2, cells_per_module=3,
                                 temps_per_module=2)
    check("D1 一轮被完整切出", len(rounds) == 1, f"{len(rounds)} 轮")
    r = rounds[0]
    check("D2 按 (模组, 序号) 归位", r.cells == [3180, 3176, 3206, 3212, 3207, 3221],
          f"{r.cells}")
    check("D3 温度归位", r.temps == [41, 41, None, None], f"{r.temps}")
    check("D4 complete 标记", r.complete, "")
    check("D5 接触器全断被识别", r.contactor_open is True, f"{r.contactor_open}")
    close("D6 电流解析", r.i_meas, 1.89, 1e-9)
    check("D7 采样计数", r.n_samples == 6, f"{r.n_samples}")

    # 同一轮里重复出现 Voltage1:（真机分包），不能劈成两轮
    text = "\n".join([
        "Voltage1:", "01.3180   02.3176",
        "Voltage2:", "01.3212   02.3207",
        "Voltage1:", "03.3206",
        "Voltage2:", "03.3221",
        "CVMax=1, Min=0",
    ])
    rounds = soc_feed.parse_text(text, n_modules=2, cells_per_module=3,
                                 temps_per_module=2)
    check("D8 分包重复 Voltage1 不劈轮", len(rounds) == 1, f"{len(rounds)} 轮")
    check("D9 分包后电芯齐全",
          rounds[0].cells == [3180, 3176, 3206, 3212, 3207, 3221],
          f"{rounds[0].cells}")

    # X 标记
    rounds = soc_feed.parse_text("Voltage1:\n01.3180  02.2744 X\nCVMax=1, Min=0",
                               n_modules=1, cells_per_module=2, temps_per_module=0)
    check("D10 X 标记被记录", rounds[0].bad == [False, True], f"{rounds[0].bad}")

    # 坏点（掉字符）由量程门槛兜住：744 低于下限、5439 高于上限，两个都该剔
    rounds = soc_feed.parse_text(
        "Voltage1:\n01.3180  02.744  03.5439\nCVMax=1, Min=0",
        n_modules=1, cells_per_module=3, temps_per_module=0)
    import soc_est as S
    c = S.clean_cells(rounds[0].cells, rounds[0].bad)
    check("D11 掉字符坏点被量程剔除", c.bad_idx == [2, 3], f"bad={c.bad_idx}")

    # 序号单调守卫：劈行造出的错位样本不许冒充
    rounds = soc_feed.parse_text("Voltage1:\n01.3180 02.3176 03.3206 04.3188",
                                n_modules=1, cells_per_module=5, temps_per_module=0)
    check("D12 正常升序全部收下", rounds[0].n_samples == 4, f"{rounds[0].n_samples}")

    # 缺 CVMax 时靠"回到电压段"收尾
    text = ("Voltage1:\n01.3180\nTemperature1:\n1.41\n"
            "Voltage1:\n01.3179\nCVMax=1, Min=0")
    rounds = soc_feed.parse_text(text, n_modules=1, cells_per_module=1,
                                 temps_per_module=1)
    check("D13 段状态判新一轮", len(rounds) == 2, f"{len(rounds)} 轮")


# ---------------------------------------------------------------- E 界面

def test_ui() -> None:
    import shutil

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    import customization
    import rx_history
    import rx_store

    app = QApplication.instance() or QApplication(sys.argv)

    # ⛔ 临时目录别用 tempfile.mkdtemp（会污染 C 盘 Temp），统一落 _check/
    tmp_path = __import__("pathlib").Path(__file__).resolve().parent.parent / "_check" / "soc_verify"
    if tmp_path.exists():
        shutil.rmtree(tmp_path, ignore_errors=True)
    tmp_path.mkdir(parents=True, exist_ok=True)
    tmp = str(tmp_path)

    real_dir = rx_history._resolve_dir
    real_pin = customization.current_vehicle
    real_db = getattr(rx_store, "_DB_PATH", None)

    # ⛔⛔ QSettings 快照 —— 本用例会 win.show()/win.resize()/win.close()，
    #   而 closeEvent 会走 _save_settings() 把 geometry 等键写进**用户真实注册表**。
    #   2026-11-10 就是漏了这一步，把用户的 geometry 覆盖成了探针窗口尺寸
    #   （1600x789），原值已不可考。⛔ 别删这段。
    qsettings = QSettings("MimoWorks", "SerialAssistant")
    snap = {key: qsettings.value(key) for key in qsettings.allKeys()}

    rx_history._resolve_dir = lambda: __import__("pathlib").Path(tmp)
    # ⛔ 只读打补丁：不调 select_vehicle（那会写用户配置）
    customization.current_vehicle = lambda: "E74-2026"
    try:
        import main_window
        win = main_window.MainWindow()
        win.resize(1100, 720)
        win.show()
        app.processEvents()

        check("E1 车辆参数已装载", win._soc_cfg is not None,
              f"{win._soc_cfg}")
        check("E2 未收到数据时显示 --", win.lbl_soc.text() == "SOC --",
              repr(win.lbl_soc.text()))
        check("E3 初始状态 off", win.lbl_soc.property("state") == "off",
              str(win.lbl_soc.property("state")))

        def group_offset() -> int:
            """整组（可见的三枚标签）中心 与 底栏中心 的偏差。"""
            shown = [lbl for lbl in win._soc_labels if lbl.text()]
            left = min(lbl.x() for lbl in shown)
            right = max(lbl.x() + lbl.width() for lbl in shown)
            return (left + right) // 2 - bar.width() // 2

        bar = win._bottom_bar
        off = group_offset()
        check("E4 读数在底栏正中（偏差 ≤ 4px）", abs(off) <= 4,
              f"偏差 {off}px（栏宽 {bar.width()}）")

        # 换窗口宽度后仍居中（用**不会触发夹紧**的宽度；夹紧场景见 E24）
        win.resize(900, 700)
        app.processEvents()
        off = group_offset()
        check("E5 改窗口宽度后仍居中", abs(off) <= 4, f"偏差 {off}px（栏宽 {bar.width()}）")

        # ⛔ 用**固定合成报文**，不依赖用户实时日志。
        #    2026-11-11 用户真机运行把「接收历史-E74-2026.log」重写成了满电
        #    平台（4.2 V/节），原先「取 n_samples 最大且最早的一轮」选中的轮
        #    从 400 V 变成 525 V，E9 的 350~450 V 断言就假红了。真机数据的
        #    验证交给 devtools/_soc_backtest.py，单测必须可复现。
        def block(i_meas: float, closed: bool) -> str:
            cells = [3200] * 125
            cells[0] = 3190          # 最低真实读值
            cells[124] = 3210        # 最高真实读值
            cells[11] = 0            # 断线
            cells[22] = 5000         # 超量程，都应被排除
            out = []
            for m in range(5):
                out.append(f"Voltage{m + 1}:")
                out.append("   ".join(
                    f"{s + 1:02d}.{cells[m * 25 + s]}" for s in range(25)))
            for m in range(5):
                out.append(f"Temperature{m + 1}:")
                out.append("   ".join(f"{s + 1}.40" for s in range(8)))
            air = "AIR+=1,AIR-=1,PRE=1" if closed else "AIR+=0,AIR-=0,PRE=0"
            out += ["BMS OK", "COM0=0", air,
                    f"V=0.00,I={i_meas:.2f}", "CVMax=3210, Min=3190"]
            return "\n".join(out)

        win._feed_soc(block(1.89, False))
        app.processEvents()
        text = "   ".join(lbl.text() for lbl in win._soc_labels if lbl.text())
        check("E6 报文驱动出 min/max 读数",
              "SOC min" in text and "max" in text and "%" in text, repr(text))
        check("E7 读数含总电压", "总电压" in text and "V" in text, repr(text))
        check("E8 有数据后状态转 ok/ warn",
              win.lbl_soc.property("state") in ("ok", "warn"),
              str(win.lbl_soc.property("state")))
        check("E9 总电压量级正确（350~450V）",
              350.0 <= win._soc_est.last.pack_v <= 450.0,
              f"{win._soc_est.last.pack_v:.1f} V")

        last = win._soc_est.last
        check("E13 min/max 来自真实读值且有序",
              last.soc_min is not None and last.soc_min <= last.soc_max,
              f"min={last.soc_min} max={last.soc_max}")
        check("E14 坏点没混进极值",
              abs(last.clean.min_mv - 3190.0) < 0.01
              and abs(last.clean.max_mv - 3210.0) < 0.01,
              f"min={last.clean.min_mv} max={last.clean.max_mv}")
        check("E15 只显示 min/max，不显示中心 SOC",
              len([lbl for lbl in win._soc_labels if lbl.text()]) == 4,
              repr([lbl.text() for lbl in win._soc_labels]))

        # 放电 → 突出 min；充电 → 突出 max
        win._feed_soc(block(12.0, True))
        app.processEvents()
        check("E16 放电突出 min",
              win.lbl_soc_min.property("emphasis") == "focus"
              and win.lbl_soc_max.property("emphasis") == "dim",
              f"min={win.lbl_soc_min.property('emphasis')} "
              f"max={win.lbl_soc_max.property('emphasis')}")
        win._feed_soc(block(-12.0, True))
        app.processEvents()
        check("E17 充电突出 max",
              win.lbl_soc_max.property("emphasis") == "focus"
              and win.lbl_soc_min.property("emphasis") == "dim",
              f"min={win.lbl_soc_min.property('emphasis')} "
              f"max={win.lbl_soc_max.property('emphasis')}")

        # ---- 没接负载：标工况 + 默认仍突出 min（用户 2026-11-11 定） ----
        win._feed_soc(block(1.89, False))      # AIR+/AIR-/PRE 全 0 = 母线断开
        app.processEvents()
        check("E22 母线断开时底栏标出工况",
              "母线断开" in win.lbl_soc_mode.text(),
              repr(win.lbl_soc_mode.text()))
        check("E23 没接负载默认突出 min",
              win._soc_est.last.mode == "open"
              and win.lbl_soc_min.property("emphasis") == "focus",
              f"mode={win._soc_est.last.mode} "
              f"em={win.lbl_soc_min.property('emphasis')}")

        # ---- 四枚标签的横向空间：别压右侧状态/计数（od75 新增第 4 枚） ----
        # 本轮改动把「工况」插到最左，整组宽度变大；820 是拖窄后的真实场景，
        # 空间不够时 _place_soc 必须夹紧到右侧文字左边，而不是硬居中压上去。
        from PySide6.QtCore import QPoint as _QPoint

        win.resize(820, 700)
        app.processEvents()

        shown = [lbl for lbl in win._soc_labels if lbl.text()]
        grp_l = min(lbl.x() for lbl in shown)
        grp_r = max(lbl.x() + lbl.width() for lbl in shown)
        state_x = win.lbl_state.mapTo(bar, _QPoint(0, 0)).x()
        check("E24 四枚标签不压右侧状态文字（留 ≥8px）",
              grp_r + 8 <= state_x,
              f"组右缘 {grp_r} / 状态左缘 {state_x}（栏宽 {bar.width()}）")
        # 最左那枚必须是工况标记（否则提示宽度会按 min 算，压住工况）
        leftmost = min(shown, key=lambda lbl: lbl.x())
        check("E25 整组最左是工况标签", leftmost is win.lbl_soc_mode,
              f"最左={leftmost.objectName() or '?'} "
              f"text={leftmost.text()!r}")
        # _toast_width_limit 必须按最左标签算（⛔ 不能再用 min 的 x）
        limit = win._toast_width_limit()
        want = grp_l - 12 - win.lbl_toast.x()
        check("E26 提示宽度上限按最左标签算", limit == want,
              f"limit={limit} 期望={want}")
        # 夹紧真的生效了（窄窗时不再精确居中，而是被推到右边不让叠）
        # 放宽到 1100 后必须恢复居中，说明夹紧是条件触发、不是永久偏移。
        win.resize(1100, 720)
        app.processEvents()
        off = group_offset()
        check("E27 放宽窗口后恢复居中（夹紧是条件触发）", abs(off) <= 4,
              f"偏差 {off}px（栏宽 {bar.width()}）")

        # ---- 四状态 EKF 开关：开 = EKF，关 = 原来的固定增益融合 ----
        spec = next((s for s in customization.specs()
                     if s["key"] == "soc_ekf4"), None)
        check("E18 设置里注册了 soc_ekf4 且默认关",
              spec is not None and spec["kind"] == "bool"
              and not bool(customization.get("soc_ekf4")),
              f"spec={spec}")
        win._init_soc()
        check("E19 开关关闭时估算器无 EKF（原方法）",
              win._soc_est is not None and win._soc_est._ekf is None,
              f"ekf={getattr(win._soc_est, '_ekf', '?')}")
        try:
            customization.set("soc_ekf4", True)
            win._init_soc()
            check("E20 开关打开时挂上四状态 EKF",
                  win._soc_est is not None and win._soc_est._ekf is not None,
                  f"ekf={getattr(win._soc_est, '_ekf', '?')}")
            win._feed_soc(block(1.89, False))
            app.processEvents()
            res = win._soc_est.last
            check("E21 EKF 路径仍出 min/max 且有序",
                  res is not None and res.soc_min is not None
                  and res.soc_min <= res.soc_max,
                  f"min={getattr(res, 'soc_min', None)} "
                  f"max={getattr(res, 'soc_max', None)}")
        finally:
            customization.set("soc_ekf4", False)
            win._init_soc()

        # 没配 SOC 的车 → --
        customization.current_vehicle = lambda: "A08-2026"
        win._init_soc()
        app.processEvents()
        check("E10 未配参数的车显示 --", win.lbl_soc.text() == "SOC --",
              repr(win.lbl_soc.text()))
        check("E11 未配参数的车状态 off", win.lbl_soc.property("state") == "off",
              str(win.lbl_soc.property("state")))
        check("E12 未配参数的车不腾 toast 空间",
              win._toast_width_limit() == 0, "")

        win.close()
        win.deleteLater()
        app.processEvents()
    finally:
        rx_history._resolve_dir = real_dir
        customization.current_vehicle = real_pin
        if real_db is not None:
            rx_store.set_db_path(real_db)
        # 还原 QSettings：删掉探针新增的键，其余按快照写回（geometry 尤其关键）
        for key in list(qsettings.allKeys()):
            if key not in snap:
                qsettings.remove(key)
        for key, value in snap.items():
            qsettings.setValue(key, value)
        qsettings.sync()


# ---------------------------------------------------------------- G 防护

def test_guards() -> None:
    """真机数据里踩出来的三个坑，锁成用例。"""
    import soc_est as S
    import soc_feed

    cfg = S.SocCfg("E74")

    # 1) 传感器轨值 -250 A：必须整帧作废，不许钳位、不许拿去学零漂
    est = S.SocEstimator(cfg)
    est.reset(soc=50.0)
    for i in range(20):
        est.update([3903.8] * 125, [40] * 40, i_meas=-250.0, ts=i * 2.0,
                   contactor_open=False)
    close("G1 超限电流不积分", est.soc, 50.0, 0.02)
    close("G2 超限电流不污染零漂", est.i_bias, 0.0, 1e-9)

    est = S.SocEstimator(cfg)
    est.reset(soc=50.0)
    for i in range(20):
        est.update([3903.8] * 125, [40] * 40, i_meas=-250.0, ts=i * 2.0,
                   contactor_open=True)
    close("G3 轨值也不许用来学零漂", est.i_bias, 0.0, 1e-9)

    check("G4 上限常量存在且合理", 100.0 <= S.I_LIMIT_A <= 400.0,
          f"I_LIMIT_A={S.I_LIMIT_A}")

    # 2) 接触器状态粘滞：报文偶尔不带 AIR 行，不能当成"状态未知"
    def round_text(open_line: bool, value: int = 3180) -> str:
        lines = [f"{s + 1:02d}.{value}" for s in range(25)]
        out = ["Voltage1:"] + lines + ["Temperature1:"] + ["1.40"] * 8
        out += ["BMS OK"]
        if open_line:
            out += ["AIR+=0,AIR-=0,PRE=0"]
        out += ["V=0.00,I=1.04", "CVMax=1, Min=0"]
        return "\n".join(out)

    collector = soc_feed.RoundCollector(n_modules=1, cells_per_module=25,
                                       temps_per_module=8)
    got = collector.feed(round_text(True))
    got += collector.feed(round_text(False))
    check("G5 带 AIR 行时解析为全断",
          len(got) == 2 and got[0].contactor_open is True,
          f"{[r.contactor_open for r in got]}")
    check("G6 缺 AIR 行时继承上一轮", len(got) == 2 and got[1].contactor_open is True,
          f"{[r.contactor_open for r in got]}")

    # 继承有上限：超过 STICKY_ROUNDS 之后必须回到"未知"
    collector = soc_feed.RoundCollector(n_modules=1, cells_per_module=25,
                                       temps_per_module=8)
    collector.feed(round_text(True))
    tail = []
    for _ in range(soc_feed.STICKY_ROUNDS + 3):
        tail += collector.feed(round_text(False))
    check("G7 继承超过上限后放弃", tail[-1].contactor_open is None,
          f"末轮={tail[-1].contactor_open}, 上限={soc_feed.STICKY_ROUNDS}")

    # 3) 长时间中断后按 OCV 重锚（跨会话时旧积分值已经作废）
    est = S.SocEstimator(cfg)
    est.reset(soc=90.0)
    est.update([3903.8] * 125, [40] * 40, i_meas=0.0, ts=0.0, contactor_open=True)
    res = est.update([3903.8] * 125, [40] * 40, i_meas=0.0, ts=3600.0,
                     contactor_open=True)
    expect = S.soc_from_ocv(3.9038)
    close("G8 隔一小时重新锚定到 OCV", res.soc, expect, 0.3)
    check("G9 重锚定被标记", res.initialised, "")

    est = S.SocEstimator(cfg)
    est.reset(soc=90.0)
    est.update([3903.8] * 125, [40] * 40, i_meas=0.0, ts=0.0, contactor_open=True)
    res = est.update([3903.8] * 125, [40] * 40, i_meas=0.0, ts=2.0,
                     contactor_open=True)
    check("G10 短间隔不重锚（保持慢回拉）", abs(res.soc - expect) > 5.0,
          f"{res.soc:.2f}% vs OCV {expect:.2f}%")

    # ---- P1（2026-11-11）：轨值判序 —— 「母线全断」必须压过「轨值」----
    # 真机 3099/4929 帧电流停在 ADC 码 0~4（-250.00 / -249.88 / -249.76 /
    # -249.63 / -249.51，量化步进 0.12 A = 1 LSB）。HALL_I 上有 R71(20k) 下拉，
    # 传感器没接 / 没供电 / 线断时 ADC 必读 0 V → 固件换算成 -250 A。
    # 旧判序先判轨值就 return (0, False)，于是 _rest_anchor 首行
    # `if not i_valid: return False` 直接退出 —— **静置锚定在整段轨值会话里
    # 一次都不跑**（真机累计约 3 小时），SOC 只剩 κ 加权电压回拉。
    # 下面 G11~G14 锁新语义，G15/G16 锁"别把正常读数改坏"，G17/G18 是端到端。
    def eff(i, open_, ok=True):
        e = S.SocEstimator(S.SocCfg("E74"))
        return e._effective_current(i, open_, ok)

    i_eff, i_valid = eff(-250.0, True)
    check("G11 轨值 + 母线全断 → 电流当 0 且**有效**（P1 新判序）",
          i_valid and i_eff == 0.0, f"i_eff={i_eff} valid={i_valid}")
    i_eff, i_valid = eff(-250.0, True, ok=False)
    check("G12 轨值 + 母线全断但 BMS 报错 → 仍无效（bms_ok 是必要条件）",
          not i_valid, f"valid={i_valid}")
    i_eff, i_valid = eff(-250.0, False)
    check("G13 轨值 + 母线闭合 → 仍无效（绝不放松）",
          not i_valid, f"valid={i_valid}")
    i_eff, i_valid = eff(-250.0, None)
    check("G14 轨值 + 接触器状态未知 → 仍无效",
          not i_valid, f"valid={i_valid}")
    i_eff, i_valid = eff(1.04, False)
    check("G15 正常小电流不受判序影响",
          i_valid and abs(i_eff - 1.04) < 1e-9, f"i_eff={i_eff}")
    i_eff, i_valid = eff(1.04, True)
    check("G16 母线全断时读数归零（学零漂路径不变）",
          i_valid and i_eff == 0.0, f"i_eff={i_eff}")

    # 端到端：整段都是轨值 + 母线全断 + 电压稳 → 静置锚定必须能触发并收敛。
    # ⛔ 这条就是 P1 的目的；旧判序下 `anchored` 永远为 False。
    est = S.SocEstimator(cfg)
    est.reset(soc=20.0)
    fired = False
    for i in range(90):                      # 180 s > REST_WINDOW_S=120
        r = est.update([3903.8] * 125, [40] * 40, i_meas=-250.0, ts=i * 2.0,
                       contactor_open=True)
        fired = fired or r.anchored
    check("G17 轨值整段下静置锚定仍能触发（P1 的目的）",
          fired, f"anchored={fired} 末 SOC={est.soc:.2f}%")
    close("G18 轨值整段下 SOC 收敛到 OCV 值 50%", est.soc, 50.0, 0.5)
    check("G19 轨值帧不会把 -250 泄漏到界面",
          abs(est.last.i_eff) < 1e-12, f"i_eff={est.last.i_eff}")


# ---------------------------------------------------------------- F 参数

def test_profiles() -> None:
    import soc_profiles

    cfg = soc_profiles.cfg_for("E74-2026")
    check("F1 E74-2026 取到内置参数", cfg is not None, f"{cfg}")
    if cfg:
        close("F2 E74 容量 12.041Ah", cfg.q_ah, 12.041, 1e-9)
        check("F3 E74 串数 125", cfg.n_cells == 125, str(cfg.n_cells))
        check("F4 E74 每模组 25 节", cfg.cells_per_module == 25,
              str(cfg.cells_per_module))
    check("F5 换赛季仍取到（E74-2027）",
          soc_profiles.cfg_for("E74-2027") is not None, "")
    check("F6 未配置的车返回 None",
          soc_profiles.cfg_for("A08-2026") is None, "")
    check("F7 空车名返回 None", soc_profiles.cfg_for("") is None, "")
    check("F8 车号归一化（小写 e74）",
          soc_profiles.vehicle_number("e74-2026") == "E74",
          soc_profiles.vehicle_number("e74-2026"))
    check("F9 内置表里有 E74", "E74" in soc_profiles.BUILTIN_PACKS, "")


# ---------------------------------------------------------------- H 配色

def test_color() -> None:
    """三种状态的**渲染颜色**必须真的不同。

    为什么要量像素而不是查属性：`QWidget#bottomBar QLabel` 这条规则的选择器
    权重比单写 `QLabel#socLabel` 高，少写一个祖先 ID 就会被按回 ink_2，而且
    没有任何报错 —— 属性查不出来，只有画出来才知道。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_theme

    ui_theme.apply_scheme("light")
    bar = QWidget()
    bar.setObjectName("bottomBar")
    row = QHBoxLayout(bar)
    plain = QLabel("未打开")
    plain.setParent(bar)
    row.addWidget(plain)
    label = QLabel("SOC min 84.2%")
    label.setObjectName("socMin")
    label.setParent(bar)
    # QSS 现在用 QWidget#bottomBar QLabel[socpart="1"] 统一选中底栏三枚 SOC
    # 标签（min / max / 总压），动态属性必须一起设上，否则量到的是普通标签色
    label.setProperty("socpart", "1")
    label.setProperty("emphasis", "normal")
    bar.resize(600, 30)
    bar.show()
    app.processEvents()

    def darkest(widget) -> int:
        img = QImage(widget.size(), QImage.Format_RGB32)
        img.fill(QColor("white"))
        painter = QPainter(img)
        widget.render(painter, QPoint(0, 0))
        painter.end()
        best = 255
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                best = min(best, (c.red() + c.green() + c.blue()) // 3)
        return best

    shades = {}
    for state in ("ok", "warn", "off"):
        label.setProperty("state", state)
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()
        app.processEvents()
        shades[state] = darkest(label)

    check("H1 三种状态颜色两两不同", len(set(shades.values())) == 3, f"{shades}")
    check("H2 ok 用主文字色（比普通标签深）", shades["ok"] < darkest(plain),
          f"ok={shades['ok']} vs 普通={darkest(plain)}")
    check("H3 off 比 ok 浅", shades["off"] > shades["ok"],
          f"off={shades['off']} ok={shades['ok']}")
    check("H4 warn 与 ok/off 都不同", shades["warn"] not in (shades["ok"], shades["off"]),
          f"{shades}")

    # 深色主题下同样要能区分
    ui_theme.apply_scheme("dark")
    dark = {}
    for state in ("ok", "warn", "off"):
        label.setProperty("state", state)
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()
        app.processEvents()
        dark[state] = darkest(label)
    check("H5 深色主题下也可区分", len(set(dark.values())) == 3, f"{dark}")
    ui_theme.apply_scheme("light")


def test_mode() -> None:
    """工况判别：多信号分层 + 电压趋势兜底 + 滞回（2026-11-11 新增）。

    真机 98 轮报文里接触器**全部全断**、`I=` 全是 -250 A 轨值，所以
    「没接负载」和「电流传感器不可信」才是常态，这里必须逐条锁住。
    """
    import soc_est as S

    def new():
        return S.WorkModeClassifier()

    c = new()
    m = c.classify(ts=0.0, mean_mv=3200.0, i_eff=100.0, i_valid=True,
                   contactor_open=True, bms_ok=True)
    check("M1 接触器全断压过一切电流读数", m == "open", m)

    c = new()
    m = c.classify(ts=0.0, mean_mv=3200.0, i_eff=12.0, i_valid=True,
                   contactor_open=False, bms_ok=True)
    check("M2 显著正电流判放电", m == "discharge", m)
    c = new()
    m = c.classify(ts=0.0, mean_mv=3200.0, i_eff=-12.0, i_valid=True,
                   contactor_open=False, bms_ok=True)
    check("M3 显著负电流判充电", m == "charge", m)

    c = new()
    m = c.classify(ts=0.0, mean_mv=3200.0, i_eff=-12.0, i_valid=True,
                   contactor_open=False, bms_ok=False)
    check("M4 BMS ERROR 且母线状态未知 → 未知", m == "unknown", m)
    c = new()
    m = c.classify(ts=0.0, mean_mv=3200.0, i_eff=-12.0, i_valid=True,
                   contactor_open=True, bms_ok=False)
    check("M4b BMS ERROR 但母线全断仍判未接负载", m == "open", m)

    # 电流无效（轨值 / 缺字段）时靠电压长窗口趋势
    c = new()
    for k in range(8):
        m = c.classify(ts=k * 10.0, mean_mv=3300.0 - k * 2.0,
                       i_eff=0.0, i_valid=False,
                       contactor_open=False, bms_ok=True)
    check("M5 电流无效 + 电压持续下降判放电", m == "discharge", m)
    c = new()
    for k in range(8):
        m = c.classify(ts=k * 10.0, mean_mv=3300.0 + k * 2.0,
                       i_eff=0.0, i_valid=False,
                       contactor_open=False, bms_ok=True)
    check("M6 电流无效 + 电压持续上升判充电", m == "charge", m)

    # 满放后静置回弹 348 mV 也不能被误判成充电（母线断开了）
    c = new()
    for k in range(8):
        m = c.classify(ts=k * 10.0, mean_mv=3100.0 + k * 40.0,
                       i_eff=0.0, i_valid=False,
                       contactor_open=True, bms_ok=True)
    check("M7 母线断开时静置回弹不误判充电", m == "open", m)

    # 滞回：判定在跑之后 30 s 内不因电流抖动退回静置
    c = new()
    c.classify(ts=0.0, mean_mv=3200.0, i_eff=12.0, i_valid=True,
               contactor_open=False, bms_ok=True)
    m = c.classify(ts=5.0, mean_mv=3200.0, i_eff=0.0, i_valid=True,
                   contactor_open=False, bms_ok=True)
    check("M8 30s 滞回内保持在跑", m == "discharge", m)
    m = c.classify(ts=40.0, mean_mv=3200.0, i_eff=0.0, i_valid=True,
                   contactor_open=False, bms_ok=True)
    check("M9 超过滞回切静置", m == "idle", m)

    # 接触器全断是硬约束，不该被滞回挡住
    m = c.classify(ts=45.0, mean_mv=3200.0, i_eff=0.0, i_valid=True,
                   contactor_open=True, bms_ok=True)
    check("M10 母线断开不受滞回影响", m == "open", m)

    c = new()
    c.classify(ts=0.0, mean_mv=3200.0, i_eff=12.0, i_valid=True,
               contactor_open=False, bms_ok=True)
    c.reset()
    check("M11 reset 清空趋势与滞回", c.mode == "unknown" and not c._hist,
          f"mode={c.mode} hist={len(c._hist)}")

    check("M12 工况文案齐全",
          set(S.MODE_LABELS) == {"discharge", "charge", "idle", "open", "unknown"},
          str(S.MODE_LABELS))


def main() -> int:
    for runner in (test_table, test_clean, test_estimator, test_feed,
                   test_guards, test_profiles, test_color, test_ui, test_mode):
        try:
            runner()
        except Exception as exc:                      # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{runner.__name__} 整体异常", False, repr(exc))

    failed = [r for r in _RESULTS if not r[1]]
    for name, ok, detail in _RESULTS:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}"
              + (f"  —— {detail}" if detail else ""))
    print(f"\n{len(_RESULTS) - len(failed)}/{len(_RESULTS)} 项通过")
    print("IS_PASS:", "YES" if not failed else "NO")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
