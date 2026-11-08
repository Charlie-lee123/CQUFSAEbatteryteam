"""od48 产物运行时自检：--check-splash / --check-net / 完整启动存活。

exe 是 console=False，stdout 大多看不到，所以一律以**退出码 + 落盘结果文件**
判定；每个子进程都带硬超时，避免网络/窗口卡死拖住整轮验证。
"""
import os
import subprocess
import sys
import time

EXE = os.path.abspath(sys.argv[1] if len(sys.argv) > 1
                      else "dist_od48/SerialAssistant.exe")
HERE = os.path.dirname(EXE)
results = []


def run(args, timeout, label):
    try:
        t0 = time.perf_counter()
        p = subprocess.run([EXE] + args, timeout=timeout,
                           capture_output=True, text=True, errors="replace")
        dt = time.perf_counter() - t0
        out = (p.stdout or "").strip().replace("\n", " | ")[:200]
        err = (p.stderr or "").strip().replace("\n", " | ")[:200]
        print(f"  {label}: exit={p.returncode} {dt:.1f}s")
        if out:
            print(f"    stdout: {out}")
        if err:
            print(f"    stderr: {err}")
        return p.returncode, dt
    except subprocess.TimeoutExpired:
        print(f"  {label}: TIMEOUT >{timeout}s（视为失败）")
        return -99, timeout


def main() -> int:
    print(f"目标: {EXE} ({os.path.getsize(EXE)} bytes)\n")

    # 1) 开屏冒烟：会真播 1.2s 开屏，能起来就说明 Qt/资源/新动画模块都进包了
    rc1, _ = run(["--check-splash"], 60, "--check-splash")
    results.append(("开屏冒烟", rc1 == 0))

    # 2) 联网：spec 删了 Qt OpenSSL 后端，必须验 Schannel 仍能取 HTTPS
    net_file = os.path.join(HERE, "net_check.txt")
    if os.path.isfile(net_file):
        os.unlink(net_file)
    rc2, _ = run(["--check-net"], 150, "--check-net")
    net_txt = ""
    if os.path.isfile(net_file):
        with open(net_file, encoding="utf-8", errors="replace") as fh:
            net_txt = fh.read().strip()
        print("    net_check.txt:")
        for line in net_txt.splitlines():
            print(f"      {line}")
    results.append(("联网(Schannel)", rc2 == 0 and net_txt.startswith("OK")))

    # 3) 完整启动存活：不传 --check-*，跑满 8s 仍活着 = 主窗构造没崩
    print("  完整启动存活（8s）...")
    try:
        proc = subprocess.Popen([EXE], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        time.sleep(8)
        alive = proc.poll() is None
        print(f"    8s 后存活={alive} (poll={proc.poll()})")
        results.append(("完整启动存活", alive))
        if alive:
            # onefile 会带子进程，用 taskkill /T 整棵树收掉
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
        proc.wait(timeout=10)
    except Exception as exc:  # noqa: BLE001
        print(f"    启动存活检查异常: {exc!r}")
        results.append(("完整启动存活", False))

    print("\n=== 汇总 ===")
    ok = True
    for name, good in results:
        print(f"  {'✓' if good else '✗'} {name}")
        ok = ok and good
    print("✓ 产物运行时自检全部通过" if ok else "✗ 运行时自检有失败项，请勿部署")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
