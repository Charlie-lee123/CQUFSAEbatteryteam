# -*- coding: utf-8 -*-
"""只读验证 od120 exe 里 `_FadeOverlay.__init__` 的 setAttribute 实参值。

不靠源码、不靠属性名存在与否 —— 直接解 PYZ → marshal code object → dis，
看每次 `setAttribute(Qt.WidgetAttribute.WA_*, X)` 前面那条 LOAD_CONST 的 X。

    .venv\\Scripts\\python.exe devtools/_verify_exe_overlay_attr.py

⛔ 只读；不改产品源码、不打包、不动 发布/ 与 dist*。
"""
import dis
import hashlib
import io
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

EXE = os.path.join(ROOT, "发布", "SerialAssistant.exe")
EXPECT_SHA = "120aa36000b90c8a391492bb990075bf43d526188527473b1bee72a246f664ec"
WANT = ("WA_TranslucentBackground", "WA_NoSystemBackground", "WA_OpaquePaintEvent")


def find_class_init(module_code, class_name: str):
    """只认「类名 code object 的直接子 __init__」，不按名字全局找（同名太多）。"""
    for const in module_code.co_consts:
        if isinstance(const, types.CodeType) and const.co_name == class_name:
            for sub in const.co_consts:
                if isinstance(sub, types.CodeType) and sub.co_name == "__init__":
                    return sub
    return None


def analyze_init(code):
    """返回 {attr: (const_value, 指令序号)}；只看 LOAD_ATTR 命中属性名之后、
    下一条 CALL 之前的 LOAD_CONST。"""
    ins = list(dis.get_instructions(code))
    found = {}
    for i, inst in enumerate(ins):
        name = inst.argval
        if inst.opname not in ("LOAD_ATTR", "LOAD_GLOBAL", "LOAD_NAME"):
            continue
        if name not in WANT:
            continue
        # 往后找到第一条 CALL 前的 LOAD_CONST
        val = None
        for j in range(i + 1, min(i + 12, len(ins))):
            op = ins[j]
            if op.opname == "CALL":
                break
            if op.opname == "LOAD_CONST":
                val = op.argval
                break
        found[name] = (val, i, j if val is not None else None)
    return found, ins


def dis_text(code) -> str:
    buf = io.StringIO()
    dis.dis(code, file=buf)
    return buf.getvalue()


def extract_module(exe: str):
    from PyInstaller.archive.readers import CArchiveReader
    from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader

    car = CArchiveReader(exe)
    top = list(car.toc.keys()) if isinstance(car.toc, dict) else [x[0] for x in car.toc]
    top = [str(k) for k in top]
    pyz = "PYZ.pyz" if "PYZ.pyz" in top else next(
        (k for k in top if k == "PYZ" or str(k).endswith("PYZ.pyz")), None)
    if pyz is None:
        raise RuntimeError("CArchive 里没有 PYZ.pyz")
    data = car.extract(pyz)
    if isinstance(data, tuple):
        data = data[0]
    os.makedirs(os.path.join(ROOT, "_check"), exist_ok=True)
    tmp = os.path.join(ROOT, "_check", "_verify_overlay_pyz.pyz")
    with open(tmp, "wb") as fh:
        fh.write(data)
    try:
        zar = ZlibArchiveReader(tmp)
        toc = zar.toc
        names = list(toc.keys()) if isinstance(toc, dict) else [x[0] for x in toc]
        if "ui_settings" not in names:
            raise RuntimeError("PYZ 里没有 ui_settings")
        return zar.extract("ui_settings")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def main() -> int:
    print(f"[verify] exe = {EXE}")
    h = hashlib.sha256()
    with open(EXE, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    sha = h.hexdigest()
    print(f"[verify] SHA256 = {sha}")
    print(f"[verify] 期望   = {EXPECT_SHA}")
    print(f"[verify] 哈希一致 = {sha == EXPECT_SHA}")

    mod = extract_module(EXE)
    print(f"[verify] PYZ ui_settings code = {mod.co_name} "
          f"consts={len(mod.co_consts)}")

    init = find_class_init(mod, "_FadeOverlay")
    if init is None:
        print("FAIL 找不到 _FadeOverlay 类体里的 __init__")
        return 1
    print(f"[verify] _FadeOverlay.__init__ code = {init!r}")
    print(f"[verify] co_consts = {init.co_consts!r}")

    found, ins = analyze_init(init)
    print("\n[verify] 属性 → setAttribute 第二个实参（字节码层面）：")
    for attr in WANT:
        val, i, j = found.get(attr, (None, None, None))
        print(f"  {attr:<28} = {val!r}  (LOAD_ATTR@{i}, LOAD_CONST@{j})")

    print("\n[verify] _FadeOverlay.__init__ 反汇编（原始证据）：")
    txt = dis_text(init)
    print(txt)

    ok = (
        found.get("WA_TranslucentBackground", (None,))[0] is False
        and found.get("WA_NoSystemBackground", (None,))[0] is True
        and found.get("WA_OpaquePaintEvent", (None,))[0] is True
    )
    print("\n[结论] " + ("od120 exe 里 WA_TranslucentBackground=False，"
                        "另两个属性=True → 修复确实进包" if ok else
                        "od120 exe 的属性实参与预期不符 → 修复没有进包"))
    # 落一份证据，方便 lead 直接引用
    with open(os.path.join(ROOT, "_check", "verify_exe_overlay_attr.txt"), "w",
              encoding="utf-8") as fh:
        fh.write(f"exe={EXE}\nsha256={sha}\nexpected={EXPECT_SHA}\n\n")
        for attr in WANT:
            fh.write(f"{attr} = {found.get(attr, (None,))[0]!r}\n")
        fh.write("\n" + txt)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
