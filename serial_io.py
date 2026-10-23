"""串口通信层。

界面层只通过 SerialWorker 的公开方法和信号与串口打交道，不直接持有 pyserial 对象，
这样串口的开、关、收发都集中在这一个文件里，改协议或换实现不用动界面。

端口参数一律走 ``serial.serial_for_url``，而不是直接构造 ``serial.Serial``：
前者既能打开 "COM7"，也能打开 "loop://" 这类回环/伪端口地址，
所以没有硬件时也能跑通完整收发链路（见 selftest.py）。
"""

from __future__ import annotations

import codecs
import re
from collections import deque

import serial
from PySide6.QtCore import QThread, Signal
from serial.tools import list_ports

BAUD_PRESETS = ["1200", "2400", "4800", "9600", "19200", "38400", "57600",
                "115200", "230400", "460800", "921600"]
BYTESIZE_CHOICES = {"5": 5, "6": 6, "7": 7, "8": 8}
# 无校验写成 "none" 而不是「无」：串口圈里 N(none) 是通用写法，
# 中文的「无」放在一排参数里既容易和别的选项混淆，也不好和 8N1 那种记法对上。
# 校验位：**键是存储值**（写进 QSettings 的就是它），显示名在 PARITY_LABELS。
# ⚠️ 两者必须分开：老版本直接把中文当键存（"奇"/"偶"/"标记"/"空格"），
#    显示名一改就会让老配置对不上号（见 parity_key 的归一）。
PARITY_CHOICES = {"none": serial.PARITY_NONE, "odd": serial.PARITY_ODD,
                  "even": serial.PARITY_EVEN, "mark": serial.PARITY_MARK,
                  "space": serial.PARITY_SPACE}
# 下拉里显示的英文名（用户 2026-10-23：把「奇 / 偶 / 标记 / 空格」改成英文）
PARITY_LABELS = {"none": "None", "odd": "Odd", "even": "Even",
                 "mark": "Mark", "space": "Space"}
# 状态栏里拼串口参数用的字母写法，串口圈约定是 8N1 这种，不写中文
PARITY_LETTERS = {"none": "N", "odd": "O", "even": "E", "mark": "M", "space": "S"}
# 老配置里存过的中文键（还有更老的「无」）。只在读取时用。
PARITY_LEGACY = {"无": "none", "奇": "odd", "偶": "even", "标记": "mark",
                 "空格": "space"}


def parity_key(value) -> str:
    """把配置里的值归一成 ``PARITY_CHOICES`` 的键（认不出来的退回 "none"）。"""
    text = str(value or "").strip()
    if text in PARITY_CHOICES:
        return text
    return PARITY_LEGACY.get(text, "none")
STOPBIT_CHOICES = {"1": 1, "1.5": 1.5, "2": 2}
ENCODINGS = ["utf-8", "gbk", "ascii", "latin-1"]

# 单次阻塞读取的等待上限。读线程靠它周期性醒来，既不会空转烧 CPU，
# 也决定了发送队列的排空延迟上限（见 SerialWorker.run）。
READ_TIMEOUT_S = 0.03


def _port_sort_key(device: str) -> tuple[str, int]:
    """端口名的排序键：把结尾的编号当**数字**排。

    直接按字符串排会得到 COM10 < COM2 —— 插拔自动连接时"挑第一个"就会挑错人，
    用户看到的列表顺序也反直觉。``device`` 不含结尾数字（loop:// 这种）时编号取 -1，
    排在同名带编号的前面。
    """
    head = device.rstrip("0123456789")
    digits = device[len(head):]
    return (head.lower(), int(digits) if digits else -1)


def list_serial_ports() -> list[tuple[str, str]]:
    """返回 [(设备名, 描述)]，按端口号排序。没有串口时返回空列表。

    ⚠️ 元组的**第一个**元素才是能交给 pyserial 打开的端口名，第二个只是给人看的
    描述（界面上拼成 "COM3 — STMicroelectronics STLink ..."）。两者搞混的后果见
    ``main_window._selected_port`` 的说明。
    """
    ports = [(p.device, p.description or p.device) for p in list_ports.comports()]
    return sorted(ports, key=lambda item: _port_sort_key(item[0]))


def parse_hex(text: str) -> bytes:
    """把 'AA 01 ff'、'AA01FF'、'AA,01' 这类写法解析成字节串。

    含非 HEX 字符或字符个数为奇数时抛 ValueError，由调用方提示用户。
    """
    cleaned = re.sub(r"[\s,;:_-]+", "", text)
    if not cleaned:
        return b""
    if len(cleaned) % 2:
        raise ValueError("HEX 字符个数为奇数，无法两两凑成一个字节")
    illegal = sorted(set(re.findall(r"[^0-9a-fA-F]", cleaned)))
    if illegal:
        raise ValueError("含非 HEX 字符：" + " ".join(illegal))
    return bytes.fromhex(cleaned)


def to_hex(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


class StreamDecoder:
    """增量文本解码器：跨数据块被切断的多字节字符会缓冲到下一块，不会显示成乱码。

    codecs 的 incrementaldecoder 就是干这个的，不要自己拼半个字符。
    """

    def __init__(self, encoding: str = "utf-8") -> None:
        self._encoding = encoding
        self._decoder = codecs.getincrementaldecoder(encoding)(errors="replace")

    @property
    def encoding(self) -> str:
        return self._encoding

    def set_encoding(self, encoding: str) -> None:
        if encoding != self._encoding:
            self._encoding = encoding
            # 换编码必须换解码器实例，只改名字的话还会拿旧编码去解新数据
            self._decoder = codecs.getincrementaldecoder(encoding)(errors="replace")

    def reset(self) -> None:
        self._decoder.reset()

    def feed(self, data: bytes) -> str:
        return self._decoder.decode(data)


class SerialWorker(QThread):
    """一个串口 = 一个线程。收到数据发 received(bytes)，出错发 failed(str) 并终止。

    **建链也是异步的**：``open()`` 只启动线程，真正的 ``serial.open()`` 在 ``run()``
    里做，结果发 ``opened()`` / ``open_failed(str)``。为什么非得这样：蓝牙串口
    （"标准串行蓝牙链接上的标准串行"）在 Windows 上要先建 RFCOMM 链路，同步 open
    会把界面卡住好几秒（2026-10-14 用户点名优化这一条）。USB 口本来就是毫秒级，
    走同一条路也不会变慢。
    """

    received = Signal(bytes)
    failed = Signal(str)
    # 建链结果（异步）。opened 之后才真正能收发；open_failed 带异常文本。
    opened = Signal()
    open_failed = Signal(str)

    def __init__(self, port: str, *, baudrate: int = 115200, bytesize: int = 8,
                 parity: str = serial.PARITY_NONE, stopbits: float = 1, parent=None) -> None:
        super().__init__(parent)
        self._port = port
        self._config = dict(baudrate=baudrate, bytesize=bytesize,
                            parity=parity, stopbits=stopbits, timeout=READ_TIMEOUT_S)
        self._ser: serial.SerialBase | None = None
        self._tx: deque[bytes] = deque()
        self._running = False

    @property
    def port(self) -> str:
        return self._port

    def open(self) -> None:
        """请求打开：**只启动线程，不在调用线程里建链**（原因见类说明）。

        失败不再抛给调用方 —— 走 ``open_failed`` 信号（异步建链，异常回不到这里）。
        """
        self._running = True
        self.start()

    @staticmethod
    def _close_ser(ser) -> None:
        """关句柄：设备已消失时 close() 自己也会抛，这里不影响退出流程。"""
        try:
            ser.close()
        except Exception:
            pass

    def write(self, data: bytes) -> None:
        """只入队，实际写入由读线程完成（见 run 里的说明）。

        建链还没完成时也照样入队 —— 队列在 opened 之后的循环里才会被排空，
        所以"连着之前就按了发送"不会丢，最多等建链那几秒。
        """
        if data:
            self._tx.append(data)

    def stop(self) -> None:
        """请求收尾：置停止位，**只等 200ms**。

        ⚠️ 不是"等到线程退出"：建链中时线程可能正卡在 ``open()`` 里（蓝牙口要等
        十几秒），一直等会把界面一起冻住 —— 而 stop() 是在界面线程调的。没等到
        也没关系：``run()`` 发现 ``_running`` 已置假会自己关句柄退出，调用方用
        ``isRunning()`` 判断、挂 ``finished`` 兜底销毁（见 _close_port）。
        正常关闭时线程在一个读超时（30ms）内就退出了，200ms 足够。
        """
        self._running = False
        self.wait(200)
        # 线程已经退出的话，句柄它自己在 run() 的 finally 里关过并置了 None；
        # 这里兜一次底（线程从未启动成的极端情况）。
        if not self.isRunning() and self._ser is not None:
            self._close_ser(self._ser)
            self._ser = None

    def run(self) -> None:
        # 1) 建链。串口对端可能远在天边（蓝牙），这一步慢 —— 所以它在工作线程里。
        try:
            ser = serial.serial_for_url(self._port, do_not_open=True, **self._config)
            ser.open()
        except (serial.SerialException, ValueError) as exc:
            # ValueError 也得接住：serial_for_url 会把带 ":" 的名字当 URL 解析，
            # 手输一个 "COM3 abc" 或者未知协议（"nosuch://x"）时抛的是 ValueError，
            # 只捕获 SerialException 的话它会一路冒到 Qt 的事件循环里。
            self._running = False
            self.open_failed.emit(str(exc))
            return
        if not self._running:
            # 建链期间用户又把开关拨回去了（取消）：收尾退出，别把口留着
            self._close_ser(ser)
            return
        self._ser = ser
        self.opened.emit()

        # 2) 收发循环。句柄在这个线程里关（只有这个线程在用它；从界面线程关会和
        #    正在阻塞的 read() 抢，所以 finally 收在这里）。
        try:
            while self._running:
                # 发送队列统一在这里排空，而不是让界面线程直接调用 write()：
                # pyserial 的句柄不保证读写线程并发安全，这样界面线程只管入队，
                # 不用加锁。代价是发送延迟最多一个 READ_TIMEOUT_S。
                while self._tx:
                    try:
                        ser.write(self._tx.popleft())
                    except Exception as exc:
                        self.failed.emit(f"发送失败：{exc}")
                        self._running = False
                        return
                try:
                    pending = ser.in_waiting
                    data = ser.read(pending or 1)
                except Exception as exc:
                    self.failed.emit(f"读取失败：{exc}")
                    self._running = False
                    return
                if data:
                    self.received.emit(data)
        finally:
            self._close_ser(ser)
            if self._ser is ser:
                self._ser = None
