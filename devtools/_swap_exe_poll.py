"""后台轮询：用户关闭正在运行的 SerialAssistant 后，把新打包的 exe 换进 发布/。

不杀用户进程；每 3s 试一次，最多 190 次（~9.5min）。锁释放即替换成功。
"""
import shutil, time, sys

SRC = "dist/SerialAssistant.exe"
DST = "发布/SerialAssistant.exe"

for i in range(190):
    try:
        shutil.copyfile(SRC, DST)
        print(f"REPLACED after {i} tries", flush=True)
        sys.exit(0)
    except (PermissionError, OSError):
        time.sleep(3)
print("GAVE_UP: 发布目录仍被占用，请手动关闭软件后重跑本脚本", flush=True)
sys.exit(1)
