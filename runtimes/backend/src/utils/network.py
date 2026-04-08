"""线程安全的网络工具。"""

import socket
import threading


class PortAllocator:
    """端口分配器。

    维护一个“已保留端口集合”，并通过互斥锁保证分配过程原子化。
    端口一旦被分配，会持续保留，直到显式释放。

    用法示例：
        allocator = PortAllocator()

        # 方式一：手动分配与释放
        port = allocator.allocate(start_port=8080)
        try:
            # 使用该端口...
            pass
        finally:
            allocator.release(port)

    """

    def __init__(self):
        self._lock = threading.Lock()
        self._reserved_ports: set[int] = set()

    def _is_port_available(self, port: int) -> bool:
        """检查端口是否可用。

        参数：
            port: 待检查端口号。

        返回：
            可用返回 True，否则返回 False。
        """
        if port in self._reserved_ports:
            return False

        # 使用 0.0.0.0（通配地址）而不是 localhost 进行绑定检测，
        # 以与 Docker 行为保持一致。Docker 会绑定 0.0.0.0:PORT；
        # 若只检测 127.0.0.1，可能在 Docker 已占用该端口时误判为可用。
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return True
            except OSError:
                return False

    def allocate(self, start_port: int = 8080, max_range: int = 100) -> int:
        """分配可用端口（线程安全）。

        会在指定范围内查找可用端口，找到后加入保留集合并返回。
        端口会一直保留，直到调用 `release()`。

        参数：
            start_port: 起始搜索端口。
            max_range: 最大搜索端口数量。

        返回：
            找到的可用端口号。

        异常：
            RuntimeError: 指定范围内无可用端口时抛出。
        """
        with self._lock:
            for port in range(start_port, start_port + max_range):
                if self._is_port_available(port):
                    self._reserved_ports.add(port)
                    return port

            raise RuntimeError(f"No available port found in range {start_port}-{start_port + max_range}")

    def release(self, port: int) -> None:
        """释放已保留端口。

        参数：
            port: 需要释放的端口号。
        """
        with self._lock:
            self._reserved_ports.discard(port)

# 全局端口分配器实例，供应用内共享
_global_port_allocator = PortAllocator()


def get_free_port(start_port: int = 8080, max_range: int = 100) -> int:
    """获取可用端口（基于全局分配器）。

    该函数通过全局分配器避免并发调用返回同一端口。
    端口会被标记为已保留，直到调用 `release_port()`。

    参数：
        start_port: 起始搜索端口。
        max_range: 最大搜索端口数量。

    返回：
        可用端口号。

    异常：
        RuntimeError: 指定范围内无可用端口时抛出。
    """
    return _global_port_allocator.allocate(start_port, max_range)


def release_port(port: int) -> None:
    """释放由 `get_free_port()` 保留的端口。

    参数：
        port: 需要释放的端口号。
    """
    _global_port_allocator.release(port)
