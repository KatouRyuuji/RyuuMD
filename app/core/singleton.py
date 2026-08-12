"""单实例守护：第二个进程启动时把打开请求转发给已运行实例，由其开新窗口。

机制（VSCode / Obsidian 同款思路，跨平台零依赖实现）：
- 主实例监听 127.0.0.1 随机端口，端口与随机 token 写入
  %APPDATA%/RyuuMD/instance.json；
- 新进程启动先读该文件尝试连接转发（1 秒超时），成功即退出自身 ——
  双击 md 文件不再冷启动整套 WebView2，秒开新窗口；
- 连接失败（残留文件/主实例已死）则接管成为新的主实例。
- token 校验防止本机其他程序伪造请求。
"""

from __future__ import annotations

import json
import secrets
import socket
import threading
from typing import Any, Callable, Optional

from .config import Config


def _lock_path(config: Config):
    return config.data_dir / "instance.json"


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
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0) as s:
            payload = json.dumps(
                {"token": token, "action": "open", "path": path or ""},
                ensure_ascii=False,
            )
            s.sendall(payload.encode("utf-8") + b"\n")
            s.settimeout(2.0)
            return s.recv(16).startswith(b"ok")
    except OSError:
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
                json.dumps({"port": port, "token": self.token}), encoding="utf-8"
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
                        self.on_open(str(msg.get("path") or ""))
                        conn.sendall(b"ok")
            except Exception:  # noqa: BLE001
                # 单条坏请求不影响服务循环
                continue

    def stop(self) -> None:
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        try:
            _lock_path(self.config).unlink(missing_ok=True)
        except OSError:
            pass
