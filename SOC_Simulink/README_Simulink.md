# E74 SOC 估算 + 总电压 — MATLAB / Simulink 模型

把 SerialAssistant 的 SOC 估算算法（纯 Python 版 `soc_est.py`，V2.7）移植成
**MATLAB 函数**，可直接放进 **Simulink 的 MATLAB Function 模块**里跑。

> 本目录是「估算 SOC 和总电压的函数 + Simulink 模型」，与 `交付电控/` 里的
> Python 源码、`SOC跑在ECU的操作步骤.pdf` 同源。两处算法必须保持一致。

---

## 1. 文件清单

| 文件 | 作用 |
|------|------|
| `soc_params.m` | **唯一参数源**：E74 串数/容量/内阻/**62 点 OCV 表**/全部算法常数。换车只改这里。 |
| `ocv_from_soc.m` / `soc_from_ocv.m` | OCV↔SOC 查表（线性插值，不外推）。 |
| `slope_at.m` / `kappa_at.m` | OCV 斜率（mV/%）与电压可信度权重 κ。 |
| `clean_cells.m` | 一轮端电压清洗（量程筛→MAD/IQR→连续段判据→回填→极值）。返回 struct。 |
| `compute_current.m` | 有效电流 + 零漂在线学习（**含 P1 接触器硬约束**）。 |
| `pack_voltage.m` | **总电压函数**：`mean_mv × n_cells / 1000`（V）。 |
| `rest_anchor.m` | 静置锚定（增益 0.3，封顶 ±15%）。含固定缓冲。 |
| `mode_classify.m` | 工况判别（放电/充电/静置/母线断开/未知）。含固定缓冲。 |
| `soc_step.m` | **主函数**：有状态，逐轮出 `soc / pack_v / soc_min / soc_max / i_eff / flag / mode`。 |
| `soc_reset.m` | 清空所有持久状态（仿真重置用）。 |
| `soc_selfcheck.m` | 对照 Python 参考值的自测（见第 3 节）。 |
| `build_soc_model.m` | 一键生成 `SOC_estimation.slx`。 |

---

## 2. 快速开始

```matlab
addpath('F:\MimoCodeworks\SerialAssistant\SOC_Simulink');  % 加到路径

% (a) 命令行单轮估算
soc_reset();
[soc, pack_v, smin, smax, ieff, flag, mode] = ...
    soc_step(3800*ones(125,1), 40*ones(40,1), NaN, 0, false(125,1), true, true);

% (b) 跑自测，确认与 Python 一致
soc_selfcheck

% (c) 生成并打开 Simulink 模型
build_soc_model
```

Simulink 里：打开 `SOC_estimation.slx` → Run。SOC_Scope 看 SOC(%)，
Detail_Scope 看 总电压 / soc_min / soc_max / 电流，两个 Display 看 flag / mode。

**喂真实数据**：把 Constant/Clock 源替换成你的 CAN/串口输入，保持端口顺序与
尺寸：
- `cells_mv` 125×1 (mV)，缺失用 `NaN`
- `temps_c` 40×1 (°C)
- `i_meas` 标量 (A)，无电流字段用 `NaN`
- `ts` 标量 (s，单调递增)
- `bad_mask` 125×1 logical（设备打 `X` 的位置）
- `contactor_open` 标量 logical（接触器全断）
- `bms_ok` 标量 logical

---

## 3. 自测（验证移植正确性）

`soc_selfcheck` 对照 `soc_est.py`（V2.7）实测参考值，全部应在 `1e-3` 容差内通过：

- OCV 查表：SOC 0/50/100 ↔ OCV 3.0818/3.9038/4.4981 V
- 斜率：SOC 2/20/50 → 184.9 / 4.4 / 8.86 mV/%；κ → 1.0 / 0.44 / 0.886
- 总电压：`pack_voltage(3700,125) = 462.5 V`
- 清洗：123 节 3700mV + 1 节 0（缺失）+ 1 节 5000（超量程）→ 均值 3700，
  回填索引 `[11;41]`，`pack_mv = 462500`（462.5 V），`ok=true`
- 冷启动：3800mV、无电流、接触器断开 → `soc≈36.905%`、`mode='open'`
- 安时积分：120A 放电 2s → SOC 方向正确下降

> 本沙箱无法启动 MATLAB（许可证/显示问题），自测请你在本机 MATLAB 中运行。

---

## 4. 与 `soc_est.py` 的对应关系（硬约束，改动必须同步）

- **P1 电流判序**：`compute_current` 里 `contactor_open && bms_ok` **必须排在**
  轨值/超限判断之前（母线全断 ⇒ 真实电流为 0，此时读数才用于学零漂）。
- **清洗非对称规则**：超量程/缺失 = `flag=NaN` ⇒ 必然回填；只有**量程内**的连续
  同向偏移（≥ 半个模组 = 12 节）才保留为真值（`module_offset`）。`run_min` 是
  故意留的悬崖，别调低。
- **极值只取 `keep=true` 的真实读值**，绝不用回填值（`min/max` 失去物理意义）。
- **总电压 = 清洗后均值 × 串数**（车上 `V=` 霍尔已坏恒 0，不影响 SOC）。
- **不上完整 EKF**：用「固定增益 + 可信度加权」融合，采样抖动 0.9~2.5s + 零漂
  会让 EKF 协方差发散。四状态 EKF 分支（`soc_ekf.py`）未移植。

---

## 5. 注意事项

- MATLAB Function 模块默认设为 **Interpreted execution（解释执行）**，以兼容完整
  MATLAB 特性（含持久状态、变长数组）。若要对该模型做 C 代码生成，把
  `IsInterpreted` 改回 `'off'`，并确认各 helper 能通过代码生成编译。
- 仿真重新开始建议先 `soc_reset()`（持久状态不会自动跨运行清零）。
- 换车型：编辑 `soc_params.m` 的 `q_ah / n_cells / cells_per_module / r0_mohm /
  i_sign / ocv_table`；其余算法不变。
