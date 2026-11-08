"""把 dist/SerialAssistant.exe 轮询覆盖到 发布/SerialAssistant.exe。

⛔ 不杀任何用户进程：若 exe 正被运行锁定，shutil.copy2 会抛异常，
捕获后每 3s 重试，直到空闲释放。成功且字节数校验一致后退出。
"""
import os
import shutil
import sys
import time

SRC = r"F:\MimoCodeworks\SerialAssistant\dist\SerialAssistant.exe"
DST = r"F:\MimoCodeworks\SerialAssistant\发布\SerialAssistant.exe"


def main() -> int:
    if not os.path.exists(SRC):
        print("SRC missing:", SRC)
        return 1
    want = os.path.getsize(SRC)
    for i in range(300):
        try:
            shutil.copy2(SRC, DST)
        except Exception as e:  # noqa: BLE001 - 锁定时反复重试
            if i % 25 == 0:
                print(f"  [{i}] 复制被拒（可能被占用），重试: {e}")
            time.sleep(3)
            continue
        try:
            got = os.path.getsize(DST)
        except OSError:
            got = -1
        if got == want:
            print(f"REPLACED after {i} tries  size={got}")
            return 0
        print(f"  [{i}] 大小不符 {got}!={want}，重试")
        time.sleep(3)
    print("FAILED：在重试次数内未能替换")
    return 1


if __name__ == "__main__":
    sys.exit(main())
