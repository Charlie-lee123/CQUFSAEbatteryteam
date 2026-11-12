# drivers/ —— 随产品分发的 USB 转串口驱动包

这个目录里的东西**会一起打进 exe 和安装包**，由 `drv_assist.py` 在用户机器上装进
Windows 驱动库。之所以要随身带：Windows 10/11 的驱动库里**没有**这几家的驱动，
新电脑插上 STLink / CH340 只会多出一个「未知设备」，串口下拉框里空空如也；而
赛场常常没有网络，让用户去官网下驱动的路子直接断掉。用户的原话是「第一次下载好
打开串口就能用」。

## 目录约定

```
drivers/
  <芯片键名>/          ← 键名必须与 drv_assist.PAYLOADS 里的 key 完全一致
    *.inf              ← 驱动描述文件（必须存在，缺了这家就当"没带"）
    *.sys / *.dll      ← inf 引用的二进制
    *.cat              ← 数字签名目录文件
    SOURCE.txt         ← 来源、版本、签名主体、每个文件的 SHA256（自己维护）
  README.md            ← 本文件
```

- 键名：`stlink` / `ch34x` / `cp210x` / `ftdi` / `pl2303`。
- 子目录结构**原样保留**：多架构 INF 用 `[SourceDisksFiles.amd64]` 指到
  `x64\xxx.sys`，把子目录拆平会让 `pnputil` 报找不到文件（CP210x 就是这种）。
- 每家目录里必须有一份 `SOURCE.txt`，写明从哪来、什么版本、谁签的、每个文件的
  SHA256。换包时一起更新 —— 出问题时要能一眼看出装的是哪版。

## 收录规则（改这个目录之前先读）

1. **只收官方签名包**。`.sys` / `.cat` 的 Authenticode 必须 `Valid`；未签名的一律
   不收，`pnputil` 也不接受（安装命令里**不加** `/force`）。
2. **只收 amd64 可用的包**。本产品是 x64 构建，32 位 Windows 不在支持范围。
   多架构包可以把其它架构子目录一起带上（体积很小，比如 CP210x 全套才 650KB），
   但前提是 INF 的 amd64 段能正常解析。
3. **不夹带脚本和无关工具**。INF 没引用的 `.bat` / `.reg` 一律删掉（CP210x 原包
   里的 `UpdateParam.bat`、`UpdateParameters.reg` 就是这么处理掉的）。
4. **许可协议文件要留着**。比如 `SLAB_License_Agreement_VCP_Windows.txt`，
   SiLabs 的分发条款要求随包附带。
5. ⚠️ **这里装的是厂商的驱动二进制**。它们可以自由下载，但"随产品再分发"属于
   各家的再分发条款范围。本项目是车队内部工具、非商业分发，按现状处理；若将来
   要商用或大规模公开分发，需要逐家确认条款（这是产品决策，不是代码问题）。

## 怎么更新某一家

优先从**已经装好的机器**导出 —— 导出的是 Windows 实际在用、且已验证签名与
完整性的那一份：

```powershell
pnputil /enum-drivers                    # 找到该驱动的 oemNN.inf 与原始 inf 名
pnputil /export-driver oemNN.inf <本目录>\<芯片键名>
```

⛔ **但导出结果不一定完整**：`/export-driver` 只导**本机架构**用到的那几个文件，
而单张 `[SourceDisksFiles]` 的 INF（CH340、PL2303 都是）把 x86 文件也列着。
这类包必须在导出后**补齐**（从厂商官方 zip 或 Catalog 的完整 cab 里取），否则
`pnputil /add-driver` 可能报缺文件。`devtools/_verify_drv.py` 的 `[2b]` 段会替
你把关：它逐个解析 `[SourceDisksFiles]` 并核对目录里的文件，缺了直接 FAIL。

导出后核对：`Get-AuthenticodeSignature <目录>\*.sys,*.cat` 全部 `Valid`；
INF 里 `[SourceDisksFiles*]` 引用的文件都在（含 arch 子目录）；
最后更新该目录的 `SOURCE.txt`（`_check/_gen_source.py` 有现成模板）。

厂商官方包也可以，但要注意 Cloudflare 拦截（FTDI 官网实测被拦）与"下载到的是
HTML 错误页而不是 zip"（先看前 4 字节是不是 `PK\x03\x04`）。

## 改了之后必须做的事

1. **`drv_assist.PAYLOAD_VERSION` 改成当天日期**。老用户机器上记着
   `driver_setup=<旧版本>`，不升版本就**不会**再弹"装驱动"的提示，新加的这家
   等于白带（这是 2026-11-12 定的去重机制，别绕过它）。
2. 重打 exe（`datas` 里已经包含本目录）与安装包。
3. `.venv\Scripts\python.exe devtools\_verify_drv.py` 应仍全绿。

## 当前收录（2026-11-12）

| 键 | 芯片 | 版本 | 签名 | 体积 |
|---|---|---|---|---|
| `stlink` | STLink VCP（VID_0483） | 2.01 / 2017-06-08 | STMicroelectronics | 20 KB |
| `ch34x` | CH340 / CH341（VID_1A86） | 4.0.2026.02 | **WHQL** | 466 KB |
| `cp210x` | CP2102/04/05/08（VID_10C4） | 11.6.0.420 | SiLabs + **WHQL** | 660 KB |
| `ftdi` | FT232/FT2232/FT4232（VID_0403） | 2.14.1.2 | **WHQL** | 304 KB |
| `pl2303` | PL2303（VID_067B） | 3.8.43.0 | **WHQL** | 938 KB |

合计约 2.4 MB，全部 x64 可用、全部签名 `Valid`。

两处来之不易，记下来免得下次重踩：

- **FTDI**：官网 `ftdichip.com` 的整条下载链路被 Cloudflare 拦（下回来的是
  "Just a moment..." 的 HTML）。改成从 **Microsoft Update Catalog** 取 —— 那是
  厂商自己提交、微软再签一遍的同一份包，比第三方镜像可信。
- **PL2303**：Catalog 里下的包**只给了 64 位文件**，而它的 INF 是单张
  `[SourceDisksFiles]`（x86 的 `ser2pl.sys` / `ser2pl.dll` 也列着）。这种包
  `pnputil` 可能报缺文件。补法：Catalog 里同一驱动的另一个 cab（`prol_3843`）
  是完整集，把 6 个文件一起展开即可。
  ⚠️ 3.5 之后的 PL2303 驱动不再支持老 **PL2303HXA**（插上 Code 10）。因为本软件
  只在"设备完全没有驱动"时才安装，所以不会弄坏现在能用的老适配器。

所有键名都在 `drv_assist.PAYLOADS` 里；**某个键没有对应目录时会被静默跳过**，
不会出现"提示装一个装不了的驱动"。反过来说，补包只要建目录 + 升
`PAYLOAD_VERSION`，代码一行不用改。

