# NOTICE — heartbeatDLC

本目录中的代码由 CQUFSAE 串口助手项目编写，采用 **MIT License**。

```
MIT License

Copyright (c) 2026 CQUFSAE Battery Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 第三方资料

实现所依据的是**公开的协议事实与抓包结论**，未复制下列项目的源代码：

- Bluetooth SIG, *Heart Rate Service 1.0* / *Heart Rate Measurement* 格式定义。
- `Tnze/miband-heart-rate`（MIT）：确认小米手环 10 在开启心率广播后使用
  标准 `0x180D` 服务与 `0x2A37` 通知，且通知帧遵循 SIG flags 定义。
- `Lniosy/heart-rate-monitor`（MIT）：小米手环 9 Pro 实测帧 `00 59` → 89 bpm，
  以及"广播里声明 0x180D、GATT 连接后订阅"的流程描述。
- `Xianggeok/xioami_heart_rate`：小米手环 10 按 0x180D 过滤设备的交叉印证。
- Xiaomi 官方 *Smart Band 10 Pro* 规格页：确认「蓝牙心率广播」要求接收端支持 BLE 心率
  协议；陶瓷特别版与标准版内部硬件一致（仅外壳 / 表带 / 重量不同）。
- `TheArmagan/advosc`（GPL-3.0，**仅参考其 `docs/MODULES.md` 说明文字，未使用或复制其代码**）：
  10 Pro 广播设备名、需停留在广播页面（回到表盘会停止广播）、免配对、多心率源、断线自动重连
  等实机注意事项。GPL-3.0 的义务不适用于仅参考事实描述的独立实现。

## 视觉风格说明

- 心率可视化（`hr_visual.py`）是网页「**心宿**」（`https://jcjt.uno/`，公开的前端 SPA，
  内部署名「心宿 · 沐风工作室」）**同款视觉效果的功能性复刻（clean-room rewrite）**，
  采用 PySide6 的 `QPainter` 2D 绘制。**未复制其任何源代码、未引入 Three.js / WebGL /
  任何外部资源或字体**，仅依据其公开发布的客户端代码中描述的视觉参数与布局（纯黑底、
  左上红色大字 BPM + 红色光晕、中部红色网格 ECG 带、满屏红/白星点场每次心跳整体放大并闪红、
  Courier 等宽字体排版）以 `QPainter` 重新实现。
- 该复刻仅针对**纯前端视觉观感**，不涉及 BLE / 心率协议（插件心率协议依据 SIG 规范与开源
  抓包独立实现，见上文「第三方资料」）。两者无任何代码或素材关联。
- 「心宿 / 沐风工作室」为原站署名；「小米 / Mi Band / Xiaomi Smart Band」为小米公司商标；
  本项目与各商标方均无隶属或授权关系，仅复刻公开可见的视觉风格。

小米、Mi Band、Xiaomi Smart Band 为小米公司商标；本项目与其无隶属或授权关系。
