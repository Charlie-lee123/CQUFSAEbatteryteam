# -*- coding: utf-8 -*-
"""把用户真实配置全量 dump 成 JSON，用来证明自测（子集/全量）不污染配置。

导出内容：
  · ``QSettings("MimoWorks", "SerialAssistant")`` 的 ``allKeys()`` + 全部值
  · ``vehicles/index.json`` 的 ``current``（车号不在 QSettings 里）
  · ``link_mode`` 单独拎出来突显（自测历史上把它 ble→serial 过）

用法：
    .venv\\Scripts\\python.exe devtools\\_dump_user_config.py _check/before.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings                            # noqa: E402


def _plain(value):
    """QSettings 的值可能带回环类型（QByteArray 等），统一转成可 JSON 化的形状。"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    try:
        return bytes(value).hex()
    except (TypeError, ValueError):
        return repr(value)


def main() -> int:
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)

    settings = QSettings("MimoWorks", "SerialAssistant")
    keys = sorted(settings.allKeys())
    data = {
        "qsettings": {key: _plain(settings.value(key)) for key in keys},
        "link_mode": _plain(settings.value("link_mode")),
        "custom/vehicle": _plain(settings.value("custom/vehicle")),
    }

    index_path = ROOT / "vehicles" / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        data["vehicles_current"] = index.get("current", "")
        data["vehicles_packs"] = [item.get("name") for item in index.get("packs", [])]
    else:
        data["vehicles_current"] = None

    out.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    print(f"已导出 {len(keys)} 个键 → {out}")
    print(f"  link_mode={data['link_mode']!r}  custom/vehicle={data['custom/vehicle']!r}  "
          f"vehicles.current={data['vehicles_current']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())