# -*- coding: utf-8 -*-
"""SerialAssistant 内部能力的**只读、异常隔离**统一门面（给 MCP / 自动化调用）。

* 本层只暴露「读 + 纯计算」，⛔ **不暴露任何写函数**（set/save/delete/record/
  install/uninstall/flush/select_vehicle/set_db_path/set_directory 都不在这里）。
* 顶层 ⛔ 禁止 ``PySide6.QtWidgets`` / ``main_window`` / ``ui_*`` / ``updater``
  import；只直接 import 5 个零 Qt 纯逻辑模块，保证「门面 import 不失败」。
  需要 PySide6 的能力一律**函数内延迟 import** 并优雅降级（``Unavailable``）。
* 每个 adapter 独立 ``try/except Exception``（照抄 ``dlc_host.load``:492-494）：
  单能力失败只返回结构化 error 并附 ``type(exc).__name__``，不拖垮门面。
* 所有返回经 ``_jsonable`` 转成 dict/list/标量，并用
  ``json.dumps(..., allow_nan=False)`` 硬保证可序列化；⛔ 不返回 Python 对象/生成器。
* 入参校验：字符串长度上限、cells_mv 长度上限、soc 严格限定 [0,100]、
  NaN/Inf 防护、路径防目录穿越（参考 ``dlc_host._safe_member``:197）。

CLI（源码态验证用，⛔ 不接 main.py）::

    python capability_api.py --list
    python capability_api.py --call ocv_from_soc --params-json '{"soc": 50}'
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import sys
import types
from dataclasses import fields, is_dataclass
from pathlib import Path, PurePosixPath

import rx_export
import soc_ekf
import soc_est
import soc_feed
import version

_MAX_TEXT = 200_000          # 单个字符串上限（防超大 text 打爆解析/清洗）
_MAX_LIST = 5_000            # 单个 list/dict 上限（含 cells_mv）
_MAX_DEPTH = 12
_MAX_INT = 10 ** 18


class InvalidParams(ValueError):
    """入参不合法；门面转成 ok=False，不抛到调用方。"""


class Unavailable(RuntimeError):
    """P1 能力所需的 PySide6 主程序环境缺失 / 模块导入失败。"""


# 每项对外字段：name / description / params{字段名:类型名} / mutating / requires
# ``params`` 用字符串类型名，保证 list_tools() 本身 JSON 可序列化。
_TOOLS: dict[str, dict] = {}


def _register(name, description, params, fn, requires=None):
    """登记一个只读能力；``mutating`` 恒为 False（本层不暴露写能力）。"""
    _TOOLS[name] = {
        "name": name, "description": description, "params": dict(params),
        "mutating": False, "requires": requires, "handler": fn,
    }


# ---------------------------------------------------------------- 序列化
def _jsonable(obj, depth=0):
    """任意 Python 对象 → 严格 JSON 可序列化结构（dict/list/标量）。

    ``CleanResult``/``SocResult``/``RoundData``（``__slots__``）、``EkfStep``/
    ``PluginInfo``（dataclass）、``SocCfg``（``to_dict``）一律转 dict；
    NaN/Inf → None；未知对象兜底成字符串。
    """
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, Path):
        return str(obj)
    if depth > _MAX_DEPTH:
        return None
    if isinstance(obj, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_jsonable(v, depth + 1) for v in obj]
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _jsonable(getattr(obj, f.name), depth + 1)
                for f in fields(obj)}
    out: dict = {}
    for cls in type(obj).__mro__:
        slots = cls.__dict__.get("__slots__")
        if not slots:
            continue
        if isinstance(slots, str):
            slots = (slots,)
        for key in slots:
            if key.startswith("__") or key in out or not hasattr(obj, key):
                continue
            out[key] = _jsonable(getattr(obj, key), depth + 1)
    if out:
        return out
    if hasattr(obj, "__dict__"):
        return {k: _jsonable(v, depth + 1)
                for k, v in vars(obj).items() if not k.startswith("_")}
    try:
        return str(obj)
    except Exception:                                   # noqa: BLE001
        return f"<{type(obj).__name__}>"


# ---------------------------------------------------------------- 入参校验
def _validate(obj, depth=0):
    """递归通用守卫：超长字符串 / 超大列表 / NaN·Inf / 超大整数 / 过深嵌套。"""
    if obj is None or isinstance(obj, bool):
        return
    if depth > _MAX_DEPTH:
        raise InvalidParams(f"参数嵌套过深（>{_MAX_DEPTH}）")
    if isinstance(obj, str):
        if len(obj) > _MAX_TEXT:
            raise InvalidParams(f"字符串过长（{len(obj)} > {_MAX_TEXT}）")
    elif isinstance(obj, float):
        if not math.isfinite(obj):
            raise InvalidParams("参数含 NaN/Inf")
    elif isinstance(obj, int):
        if abs(obj) > _MAX_INT:
            raise InvalidParams("整数过大")
    elif isinstance(obj, (list, tuple, set, frozenset)):
        if len(obj) > _MAX_LIST:
            raise InvalidParams(f"列表过长（{len(obj)} > {_MAX_LIST}）")
        for item in obj:
            _validate(item, depth + 1)
    elif isinstance(obj, dict):
        if len(obj) > _MAX_LIST:
            raise InvalidParams(f"字典过大（{len(obj)} > {_MAX_LIST}）")
        for key, value in obj.items():
            _validate(key, depth + 1)
            _validate(value, depth + 1)


def _num(p, key, default=None, required=False):
    v = p.get(key)
    if v is None:
        if required:
            raise InvalidParams(f"缺少参数 {key}")
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise InvalidParams(f"{key} 必须是数值（收到 {type(v).__name__}）")
    v = float(v)
    if not math.isfinite(v):
        raise InvalidParams(f"{key} 必须是有限数")
    return v


def _soc(p, key="soc"):
    """soc/斜率/κ 入参严格限定 [0,100]：越界（含负数）拒绝而非静默夹紧，
    满足「垃圾参数必须 ok=False」的异常隔离要求。"""
    v = _num(p, key, required=True)
    if not 0.0 <= v <= 100.0:
        raise InvalidParams(f"{key} 必须在 [0,100]（收到 {v}）")
    return v


def _text(p, key="text", default=""):
    v = p.get(key, default)
    if v is None:
        return default
    if not isinstance(v, str):
        raise InvalidParams(f"{key} 必须是字符串")
    return v                       # 长度由 _validate 统一卡


def _int(p, key, default=0):
    v = p.get(key)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int):
        raise InvalidParams(f"{key} 必须是整数")
    return v


def _bool(p, key, default):
    v = p.get(key)
    if v is None:
        return default
    if not isinstance(v, bool):
        raise InvalidParams(f"{key} 必须是布尔")
    return v


def _table(p, key="table", required=False):
    v = p.get(key)
    if v is None:
        if required:
            raise InvalidParams(f"缺少参数 {key}")
        return soc_est.OCV_TABLE
    if not isinstance(v, (list, tuple)) or len(v) < 2:
        raise InvalidParams(f"{key} 必须是至少 2 点的表")
    out = []
    for pair in v:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            raise InvalidParams(f"{key} 每点必须是 [soc, ocv]")
        a, b = pair[0], pair[1]
        if (isinstance(a, bool) or isinstance(b, bool)
                or not isinstance(a, (int, float))
                or not isinstance(b, (int, float))):
            raise InvalidParams(f"{key} 每点必须是数值")
        a, b = float(a), float(b)
        if not math.isfinite(a) or not math.isfinite(b):
            raise InvalidParams(f"{key} 每点必须是有限数")
        out.append((a, b))
    return out


def _cells(p, key="cells_mv", required=True):
    v = p.get(key)
    if v is None:
        if required:
            raise InvalidParams(f"缺少参数 {key}")
        return []
    if not isinstance(v, (list, tuple)):
        raise InvalidParams(f"{key} 必须是列表")
    out = []
    for item in v:
        if item is None:
            out.append(None)
        elif isinstance(item, bool) or not isinstance(item, (int, float)):
            raise InvalidParams(
                f"{key} 元素必须是数值或 null（收到 {type(item).__name__}）")
        else:
            f = float(item)
            if not math.isfinite(f):
                raise InvalidParams(f"{key} 元素必须是有限数")
            out.append(f)
    return out


def _soc_cfg(p, key="cfg"):
    d = p.get(key)
    if d is None:
        return soc_est.SocCfg()
    if isinstance(d, soc_est.SocCfg):
        return d
    if not isinstance(d, dict):
        raise InvalidParams(f"{key} 必须是 dict")
    cfg = soc_est.SocCfg.from_dict(d)
    if cfg is None:
        raise InvalidParams(f"{key} 不是合法 SocCfg（from_dict 返回 None）")
    return cfg


def _module(name):
    """P1 能力的函数内延迟 import；失败统一降级为 Unavailable。"""
    try:
        return importlib.import_module(name)
    except Exception as exc:                             # noqa: BLE001
        raise Unavailable(
            f"需要主程序环境（PySide6）：导入 {name} 失败 "
            f"{type(exc).__name__}: {exc}") from exc


def _safe_folder(folder):
    """防目录穿越（参考 dlc_host._safe_member:197）：拒绝对路径/盘符/.. /null。"""
    if folder is None:
        return None
    if not isinstance(folder, str):
        raise InvalidParams("folder 必须是字符串或 null")
    text = folder.replace("\\", "/")
    if "\x00" in text:
        raise InvalidParams("folder 含 null 字节")
    pure = PurePosixPath(text)
    if pure.is_absolute() or text.startswith("/") or ":" in text.split("/")[0]:
        raise InvalidParams("folder 不允许绝对路径/盘符")
    if any(part in ("..", "") for part in pure.parts):
        raise InvalidParams("folder 不允许 .. 目录穿越")
    return folder


def _namespace(obj):
    """dict → SimpleNamespace（递归），供 format_soc_parts/format_soc 鸭子类型读。"""
    if isinstance(obj, dict):
        return types.SimpleNamespace(**{k: _namespace(v) for k, v in obj.items()})
    if isinstance(obj, (list, tuple)):
        return [_namespace(v) for v in obj]
    return obj


# ================================================================ handlers
def _t_version(p):
    return {"VERSION": version.VERSION, "REPO_URL": version.REPO_URL}


def _t_ocv_from_soc(p):
    return soc_est.ocv_from_soc(_soc(p), _table(p))


def _t_soc_from_ocv(p):
    return soc_est.soc_from_ocv(_num(p, "ocv_v", required=True), _table(p))


def _t_slope_at(p):
    return soc_est.slope_at(_soc(p), _table(p))


def _t_kappa_at(p):
    return soc_est.kappa_at(_soc(p), _table(p))


def _t_table_ok(p):
    return bool(soc_est.table_ok(_table(p, required=True)))


def _t_clean_cells(p):
    return soc_est.clean_cells(
        _cells(p), bad_mask=p.get("bad_mask"),
        n_cells=_int(p, "n_cells", 0),
        cells_per_module=_int(p, "cells_per_module", 0))


def _t_format_soc_parts(p):
    res = p.get("res")
    return list(soc_est.format_soc_parts(
        _namespace(res) if res is not None else None))


def _t_format_soc(p):
    res = p.get("res")
    return soc_est.format_soc(_namespace(res) if res is not None else None)


def _t_soc_cfg_to_dict(p):
    return _soc_cfg(p).to_dict()


def _t_soc_cfg_from_dict(p):
    data = p.get("data", p)
    if not isinstance(data, dict):
        raise InvalidParams("data 必须是 dict")
    cfg = soc_est.SocCfg.from_dict(data)
    return cfg.to_dict() if cfg is not None else None


def _t_soc_estimator_update(p):
    cfg = _soc_cfg(p)
    ekf = soc_ekf.SocEKF4(cfg) if _bool(p, "use_ekf", False) else None
    est = soc_est.SocEstimator(cfg=cfg, ekf=ekf)
    if p.get("reset_soc") is not None:
        est.reset(_num(p, "reset_soc"))
    return est.update(
        _cells(p), temps_c=p.get("temps_c"), i_meas=p.get("i_meas"),
        ts=p.get("ts"), bad_mask=p.get("bad_mask"),
        contactor_open=p.get("contactor_open"),
        bms_ok=_bool(p, "bms_ok", True))


def _t_soc_estimator_reset(p):
    est = soc_est.SocEstimator(cfg=_soc_cfg(p))
    est.reset(None if p.get("soc") is None else _num(p, "soc"))
    return {"soc": est.soc, "known": est.known}


def _t_parse_text(p):
    kwargs = {}
    for key in ("n_modules", "cells_per_module", "temps_per_module"):
        if p.get(key) is not None:
            kwargs[key] = _int(p, key)
    return soc_feed.parse_text(_text(p), **kwargs)


def _t_soc_ekf4_step(p):
    cfg = _soc_cfg(p)
    raw = p.get("params")
    ekf_params = None
    if isinstance(raw, dict) and raw:
        try:
            ekf_params = soc_ekf.EkfParams(**raw)
        except TypeError as exc:
            raise InvalidParams(f"params 字段不合法: {exc}") from exc
    ekf = soc_ekf.SocEKF4(cfg, params=ekf_params)
    return ekf.step(_num(p, "v_cell_v", required=True),
                    _num(p, "i_a", required=True),
                    _num(p, "dt_s", required=True))


def _t_rx_export_session(p):
    session = rx_export.Session()          # ⛔ 绝不碰 _SESSION / session()
    session.feed(_text(p), stamp=p.get("stamp"))
    return session.rounds()                # ⛔ 不调 write_csv / write_json


# ---- P1 handlers：函数内延迟 import + Unavailable 优雅降级 -----------------
def _t_builtin_packs(p):
    return _module("soc_profiles").BUILTIN_PACKS


def _t_cfg_for(p):
    cfg = _module("soc_profiles").cfg_for(_text(p, "vehicle"))
    return cfg.to_dict() if cfg is not None else None


def _t_customization_vehicles(p):
    return _module("customization").vehicles()


def _t_customization_vehicle_names(p):
    return _module("customization").vehicle_names()


def _t_customization_current_vehicle(p):
    return _module("customization").current_vehicle()


def _t_customization_active_profile(p):
    return _module("customization").active_profile()


def _t_customization_get(p):
    return _module("customization").get(_text(p, "key"))


def _t_customization_specs(p):
    return _module("customization").specs()


def _t_dlc_discover(p):
    return _module("dlc_host").discover()


def _t_rx_store_info(p):
    mod = _module("rx_store")
    return {"count": mod.count(), "stats": mod.stats()}


def _t_driver_status(p):
    """USB 转串口驱动体检（只读）：随包带了谁、本机缺谁、插着谁没驱动。

    界面里"插上就能用"靠的就是这一套（见 drv_assist），这里只是把它暴露给
    Agent —— 排查"为什么没串口"时第一件事就该看它。**不安装任何东西**。
    """
    mod = _module("drv_assist")
    return {
        "payload_dir": mod.payload_dir(),
        "payload_version": mod.PAYLOAD_VERSION,
        "shipped": mod.shipped_keys(),
        "missing_on_this_pc": mod.missing_keys(),
        "plugged_without_driver": mod.problem_devices(),
        "needed": mod.needed_keys(),
    }


def _t_rx_history_labels(p):
    return _module("rx_history").labels_of(_safe_folder(p.get("folder")))


# ================================================================ 注册表
# 显式列出每个 handler（而非按字符串名索引），静态检查（vulture）可见其被引用。
# P0：requires=None（零 Qt，直接 import）；P1：requires="PySide6"（延迟 import）。
_register("version_info", "读取软件版本号与仓库地址（纯常量）",
          {}, _t_version)
_register("ocv_from_soc", "SOC(%) → OCV(V)；OCV 表内线性插值、表外取端点",
          {"soc": "float", "table": "list"}, _t_ocv_from_soc)
_register("soc_from_ocv", "OCV(V) → SOC(%)；反查，不外推",
          {"ocv_v": "float", "table": "list"}, _t_soc_from_ocv)
_register("slope_at", "该 SOC 处 OCV 曲线斜率（mV/%）",
          {"soc": "float", "table": "list"}, _t_slope_at)
_register("kappa_at", "该 SOC 处电压可信度权重 κ",
          {"soc": "float", "table": "list"}, _t_kappa_at)
_register("table_ok", "校验 OCV 表是否可用（>=2 点且 SOC/OCV 严格单调递增）",
          {"table": "list"}, _t_table_ok)
_register("clean_cells", "清洗一轮端电压（量程筛选/稳健离群/断线回填），返回 CleanResult",
          {"cells_mv": "list", "bad_mask": "list", "n_cells": "int",
           "cells_per_module": "int"}, _t_clean_cells)
_register("format_soc_parts",
          "把 SocResult 渲染成底栏四枚标签文本；res 传 soc_estimator_update 的返回 dict",
          {"res": "dict", "cfg": "dict"}, _t_format_soc_parts)
_register("format_soc", "把 SocResult 渲染成完整一行（回测/日志用）；res 同上",
          {"res": "dict", "cfg": "dict"}, _t_format_soc)
_register("soc_cfg_to_dict", "把 SocCfg 配置字典规范化后 to_dict 序列化",
          {"cfg": "dict"}, _t_soc_cfg_to_dict)
_register("soc_cfg_from_dict", "解析 SocCfg 配置字典；坏配置返回 null（不抛异常）",
          {"data": "dict"}, _t_soc_cfg_from_dict)
_register("soc_estimator_update",
          "新建 SocEstimator 喂一轮数据，返回 SocResult（可注入 EKF / 先 reset）",
          {"cfg": "dict", "cells_mv": "list", "temps_c": "list",
           "i_meas": "float", "ts": "float", "bad_mask": "list",
           "contactor_open": "bool", "bms_ok": "bool",
           "reset_soc": "float", "use_ekf": "bool"}, _t_soc_estimator_update)
_register("soc_estimator_reset", "新建 SocEstimator 并 reset(soc)，返回重置后状态",
          {"cfg": "dict", "soc": "float"}, _t_soc_estimator_reset)
_register("parse_text", "把整段 BMS 报文解析成整轮 RoundData 列表",
          {"text": "str", "n_modules": "int", "cells_per_module": "int",
           "temps_per_module": "int"}, _t_parse_text)
_register("soc_ekf4_step", "新建 SocEKF4 跑一轮 (V_cell,I,dt)，返回 EkfStep",
          {"cfg": "dict", "params": "dict", "v_cell_v": "float",
           "i_a": "float", "dt_s": "float"}, _t_soc_ekf4_step)
_register("rx_export_session",
          "用全新 Session 解析接收文本并返回 rounds()（⛔ 不碰模块级单例、不落盘）",
          {"text": "str", "stamp": "str"}, _t_rx_export_session)
_register("builtin_packs",
          "读取内置 SOC 参数表 BUILTIN_PACKS（纯常量，只读）；"
          "注：soc_profiles 顶层依赖 PySide6.QtCore，故按 PySide6 能力降级",
          {}, _t_builtin_packs, "PySide6")
_register("cfg_for", "按车号取 SOC 参数配置（soc_profiles.cfg_for，只读）",
          {"vehicle": "str"}, _t_cfg_for, "PySide6")
_register("customization_vehicles", "全部车辆档案（name/cells/temps 副本，只读）",
          {}, _t_customization_vehicles, "PySide6")
_register("customization_vehicle_names", "车辆名列表（只读）",
          {}, _t_customization_vehicle_names, "PySide6")
_register("customization_current_vehicle", "当前车辆名；没选车返回空串（只读）",
          {}, _t_customization_current_vehicle, "PySide6")
_register("customization_active_profile",
          "当前车辆逐模组结构 (cells,temps) 或 null（只读）",
          {}, _t_customization_active_profile, "PySide6")
_register("customization_get", "读一个已注册定制配置的值（customization.get，只读）",
          {"key": "str"}, _t_customization_get, "PySide6")
_register("customization_specs", "全部注册项（设置面板据此生成界面，只读）",
          {}, _t_customization_specs, "PySide6")
_register("dlc_discover", "扫描已安装 DLC 清单（dlc_host.discover，只读）",
          {}, _t_dlc_discover, "PySide6")
_register("rx_store_info",
          "会话库总帧数与统计（rx_store.count/stats，只读查询，不改默认库路径）",
          {}, _t_rx_store_info, "PySide6")
_register("driver_status",
          "USB 转串口驱动体检：随包带了哪些芯片驱动、本机驱动库缺哪些、"
          "当前插着哪些设备没驱动（只读，不安装）", {}, _t_driver_status)
_register("rx_history_labels", "某目录下历史文件 → [(标签,绝对路径)]（只读）",
          {"folder": "str"}, _t_rx_history_labels, "PySide6")


# ---------------------------------------------------------------- 对外 API
def list_tools() -> list[dict]:
    """返回全部只读能力的规格；每项字段齐全、``mutating`` 恒为 False。"""
    return [
        {"name": s["name"], "description": s["description"],
         "params": dict(s["params"]), "mutating": s["mutating"],
         "requires": s["requires"]}
        for s in _TOOLS.values()
    ]


def call(name: str, params: dict) -> dict:
    """调用一个能力；统一返回 ``{ok, data, error}``，任何异常都被隔离。"""
    spec = _TOOLS.get(name)
    if spec is None:
        return {"ok": False, "data": None,
                "error": {"type": "UnknownTool",
                          "message": f"未知能力：{name}（用 --list 查看）"}}
    if not isinstance(params, dict):
        return {"ok": False, "data": None,
                "error": {"type": "InvalidParams", "message": "params 必须是 dict"}}
    try:
        _validate(params)
        data = _jsonable(spec["handler"](params))
        json.dumps(data, ensure_ascii=False, allow_nan=False)   # 硬保证可序列化
        return {"ok": True, "data": data, "error": None}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "data": None,
                "error": {"type": type(exc).__name__, "message": str(exc)}}


# ---------------------------------------------------------------- CLI
def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="SerialAssistant 只读能力门面（CLI 验证用，不接 main.py）")
    parser.add_argument("--list", action="store_true", help="列出全部能力")
    parser.add_argument("--call", metavar="NAME", help="调用一个能力")
    parser.add_argument("--params-json", default="{}", help="调用参数（JSON 对象）")
    args = parser.parse_args(argv)
    if args.list:
        print(json.dumps(list_tools(), ensure_ascii=False, indent=2))
        return 0
    if args.call:
        try:
            params = json.loads(args.params_json)
        except (ValueError, TypeError) as exc:
            print(json.dumps({"ok": False, "data": None,
                              "error": {"type": "InvalidJSON", "message": str(exc)}},
                             ensure_ascii=False))
            return 1
        result = call(args.call, params)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result["ok"] else 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main())
