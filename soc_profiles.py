"""SOC 参数的存放与取用 —— 按**车号**索引，为换车/多车预留入口。

为什么单独一个模块
------------------
SOC 参数（容量、串数、内阻、OCV 表、电流方向）和「车辆档案」里的模组结构
（每模组几节电芯、几个温度点）是**两件事**：前者决定怎么算，后者决定怎么排版，
生命周期也完全不同 —— 用户可能临时新建一台车只想看电压排版，还没标定过电池。
所以参数单独存一个键（``custom/soc_packs``），**不塞进车辆档案的 JSON**：

* 不动 ``customization`` 的读写与迁移路径，已有配置零风险；
* 不动车辆档案的导出/导入与在线库格式，不会让老版本读到不认识的结构；
* 没配过的车自然落到 ``None`` → 界面显示 ``--``，功能对它是惰性的。

内置表 :data:`BUILTIN_PACKS` 就是"预留别的车"的入口：来一台新车，先在这儿加
一条（或用 :func:`save_pack` 写一条进注册表），估算链路一行不用改。
"""

from __future__ import annotations

import json

from PySide6.QtCore import QSettings

import customization
from soc_est import OCV_TABLE, SocCfg

#: QSettings 键（与 customization 同一个组织/应用名，跟着它的快照恢复一起被
#: 备份/还原 —— selftest 里不会污染用户真实配置）。
_KEY = "custom/soc_packs"

#: 内置参数表：键是**车号**（``E74-2026`` 拆出来的 ``E74``），与赛季无关 ——
#: 同一台车换了赛季，电池参数不该跟着丢。
BUILTIN_PACKS: dict[str, dict] = {
    "E74": {
        "q_ah": 12.041,
        "n_cells": 125,
        "cells_per_module": 25,
        "r0_mohm": 6.8,
        "i_sign": 1.0,
        "chemistry": "NCM",
        "ocv_table": [list(p) for p in OCV_TABLE],
    },
}


def vehicle_number(name: str) -> str:
    """车名 → 车号。``E74-2026`` → ``E74``；拆不出来就用原名。"""
    _season, number = customization.split_vehicle(name)
    return (number or "").strip().upper()


def _load_raw() -> dict:
    raw = QSettings("MimoWorks", "SerialAssistant").value(_KEY, "")
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _builtin(number: str) -> dict | None:
    entry = BUILTIN_PACKS.get((number or "").upper())
    return dict(entry) if entry else None


def cfg_for(vehicle: str) -> SocCfg | None:
    """取一台车的 SOC 参数。

    优先级：注册表里的自定义配置 > 内置表 > ``None``（该车不做 SOC）。
    返回 ``None`` 是**正常状态**，不是错误。
    """
    number = vehicle_number(vehicle)
    if not number:
        return None
    custom = _load_raw().get(number)
    if isinstance(custom, dict):
        merged = dict(_builtin(number) or {})
        merged.update({k: v for k, v in custom.items() if v is not None})
        cfg = SocCfg.from_dict({**merged, "number": number})
        if cfg is not None:
            return cfg
    builtin = _builtin(number)
    if builtin is not None:
        cfg = SocCfg.from_dict({**builtin, "number": number})
        if cfg is not None:
            return cfg
    return None


def supported() -> list[str]:
    """所有能算 SOC 的车号（内置 + 自定义），供设置界面列出来。"""
    numbers = set(BUILTIN_PACKS)
    numbers.update(str(k) for k in _load_raw())
    return sorted(numbers)


def save_pack(cfg: SocCfg) -> bool:
    """写一台车的参数（``cfg.number`` 是车号）。写失败返回 False，不抛。"""
    number = (cfg.number or "").strip().upper()
    if not number:
        return False
    data = _load_raw()
    data[number] = cfg.to_dict()
    settings = QSettings("MimoWorks", "SerialAssistant")
    settings.setValue(_KEY, json.dumps(data, ensure_ascii=False))
    settings.sync()
    return True


def delete_pack(number: str) -> bool:
    """删掉自定义配置（内置表不受影响）。"""
    key = (number or "").strip().upper()
    data = _load_raw()
    if key not in data:
        return False
    data.pop(key, None)
    settings = QSettings("MimoWorks", "SerialAssistant")
    settings.setValue(_KEY, json.dumps(data, ensure_ascii=False))
    settings.sync()
    return True
