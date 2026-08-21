"""单实例守护：第二个进程启动时把打开请求转发给已运行实例，由其开新窗口。

机制（VSCode / Obsidian 同款思路，跨平台零依赖实现）：
- 主实例监听 127.0.0.1 随机端口，端口、随机 token、pid 写入
  %APPDATA%/RyuuMD/instance.json；
- 新进程启动先读该文件：若记录了 pid 且进程已死，直接删锁接管，不连端口；
  否则尝试连接转发（0.4 秒超时），成功即退出自身 ——
  双击 md 文件不再冷启动整套 WebView2，秒开新窗口；
- 主实例收到请求立即应答，再异步建窗（应答与建窗耗时解耦）；
- 连接失败（残留文件/主实例已死）则删锁并接管成为新的主实例。
- token 校验防止本机其他程序伪造请求。
- 老格式锁文件（无 pid）保持兼容：跳过存活检查，按连接超时处理。
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import threading
from typing import Any, Callable, Optional

from .config import Config


def _lock_path(config: Config):
    return config.data_dir / "instance.json"


def _unlink_lock(lock) -> None:
    try:
        lock.unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """进程是否仍在。权限异常当作存活，避免误删锁把仍在跑的实例挤掉。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if handle:
            kernel32.CloseHandle(handle)
            return True
        # 5 = ERROR_ACCESS_DENIED：无权限查询仍视为存活
        return int(kernel32.GetLastError()) == 5
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def try_forward(config: Config, path: str) -> bool:
    """尝试把「打开 path」请求转发给已运行实例。成功返回 True（调用方应退出）。

    path 为空表示「唤起一个新窗口（首页）」。
    """
    lock = _lock_path(config)
    if not lock.is_file():
        return False
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
        port, token = int(info["port"]), str(info["token"])
    except Exception:  # noqa: BLE001
        _unlink_lock(lock)
        return False

    raw_pid = info.get("pid")
    if raw_pid is not None:
        try:
            pid = int(raw_pid)
        except (TypeError, ValueError):
            pid = 0
        if not _pid_alive(pid):
            _unlink_lock(lock)
            return False

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4) as s:
            payload = json.dumps(
                {"token": token, "action": "open", "path": path or ""},
                ensure_ascii=False,
            )
            s.sendall(payload.encode("utf-8") + b"\n")
            s.settimeout(2.0)
            return s.recv(16).startswith(b"ok")
    except OSError:
        _unlink_lock(lock)
        return False


class InstanceServer:
    """主实例的请求接收端。收到合法 open 请求时回调 on_open(path)。"""

    def __init__(self, config: Config, on_open: Callable[[str], Any]) -> None:
        self.config = config
        self.on_open = on_open
        self.token = secrets.token_hex(16)
        self._sock: Optional[socket.socket] = None

    def start(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.bind(("127.0.0.1", 0))
            self._sock.listen(4)
            port = self._sock.getsockname()[1]
            _lock_path(self.config).write_text(
                json.dumps(
                    {"port": port, "token": self.token, "pid": os.getpid()},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            return False
        threading.Thread(target=self._serve, daemon=True, name="ryuumd-instance").start()
        return True

    def _serve(self) -> None:
        assert self._sock is not None
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return  # socket 已关闭，退出线程
            try:
                with conn:
                    conn.settimeout(2.0)
                    data = b""
                    while b"\n" not in data and len(data) < 64 * 1024:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    msg = json.loads(data.decode("utf-8"))
                    if msg.get("token") != self.token:
                        conn.sendall(b"denied")
                        continue
                    if msg.get("action") == "open":
                        conn.sendall(b"ok")
                        # 先应答再建窗：建窗要 Invoke 回 UI 线程，慢机器上耗时可能
                        # 超过转发方 recv 超时 —— 超时会让第二进程误删活锁、退化为
                        # 完整实例（双实例互踩配置/锁）。应答与建窗解耦后转发秒回。
                        threading.Thread(
                            target=self.on_open,
                            args=(str(msg.get("path") or ""),),
                            daemon=True,
                            name="ryuumd-open-window",
                        ).start()
            except Exception:  # noqa: BLE001
                # 单条坏请求不影响服务循环
                continue

    def stop(self) -> None:
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        _unlink_lock(_lock_path(self.config))
