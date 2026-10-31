# 车辆（电池箱）在线库

软件里 **设置 → 车辆 → 在线车辆库** 从本目录读取并下载车辆档案。
读取走多源镜像（ghfast / gh-proxy / ghproxy / gitmirror / gitdl → raw 直连），
**不需要代理**，与「检查更新」用的是同一套链路。

## 目录约定

| 文件 | 作用 |
|---|---|
| `index.json` | 清单：有哪些包、每个包对应哪个文件。客户端先读它，再按需下载 |
| `*.cqv.json` | 单个车辆包（可装多台车） |

## `index.json`

```json
{
 "schema": 1,
 "updated_at": "2026-10-31T11:35:00",
 "packs": [
  {"name": "CQUFSAE-2026", "file": "CQUFSAE-2026.cqv.json", "modules": 5,
   "note": "8 月版，25 芯/模组"}
 ]
}
```

- `name`：显示名，也是去重键（同名视为同一个包，上传会替换）
- `file`：`vehicles/` 下的文件名
- `modules`：模组数（仅用于列表展示，客户端会以包内实际结构为准）
- `note`：一句话备注，可省略

> 上传时客户端会**自动**读-改-写这个文件（按 `name` 去重合并），
> 手工新增包也可以只改这里。

## `*.cqv.json`

```json
{
 "schema": 1,
 "app": "CQUFSAE串口助手",
 "exported_at": "2026-10-31T11:30:00",
 "vehicles": [
  {"name": "E74-2026", "cells": [25, 25, 25, 25, 25], "temps": [8, 8, 8, 8, 8]}
 ]
}
```

- `cells` / `temps`：**逐模组**的电芯数与温度采集数（长度 = 模组数）
- 导入是同名覆盖、不同名新增；长度不一致会截断到较短的一边并在导入结果里提示
- `schema` 只做版本标记，缺失或更高版本都会尽力导入

## 怎么发布

1. 软件里 **车辆 → 导出车辆…** 得到 `.cqv.json`
2. **在线车辆库 → 上传**：填一次 GitHub 令牌（本机用 Windows DPAPI 加密保存），
   点上传即写入 `vehicles/<包名>.cqv.json` 并自动登记到 `index.json`
3. 没有令牌时：点「在浏览器里上传」，把文件拖进 GitHub 的 upload 页，由有权限的人合并

> 令牌需要 `repo`（classic）或 `Contents: Read and write`（fine-grained）权限，
> 只存在本机、不上传。
