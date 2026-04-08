"""沙箱供给后端的抽象基类。"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

import requests

from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)


def wait_for_sandbox_ready(sandbox_url: str, timeout: int = 30) -> bool:
    """
    参数：
        sandbox_url: 沙箱 URL（例如 http://k3s:30001）。
        timeout: 最大等待秒数。

    返回：
        沙箱就绪返回 True，否则返回 False。
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{sandbox_url}/v1/sandbox", timeout=5)
            if response.status_code == 200:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
    return False


class SandboxBackend(ABC):
    """
    两种实现：
    - LocalContainerBackend：本地启动 Docker/Apple Container，并管理端口
    - RemoteSandboxBackend：连接预先存在的远端地址（K8s 服务或外部服务）

    """

    @abstractmethod
    def create(self, thread_id: str, sandbox_id: str, extra_mounts: list[tuple[str, str, bool]] | None = None) -> SandboxInfo:
        """
        参数：
            thread_id: 创建沙箱对应的线程 ID。对需要按线程组织实例的后端有用。
            sandbox_id: 确定性沙箱标识符。
            extra_mounts: 额外挂载列表，格式为 (host_path, container_path, read_only)。
                对不管理容器的后端（如 remote）可忽略。

        返回：
            包含连接信息的 SandboxInfo。
        """
        ...

    @abstractmethod
    def destroy(self, info: SandboxInfo) -> None:
        """
        参数：
            info: 要销毁的沙箱元数据。

        """
        ...

    @abstractmethod
    def is_alive(self, info: SandboxInfo) -> bool:
        """
        这里应进行轻量级存活检查（例如 container inspect），
        而非完整健康检查。

        参数：
            info: 待检查的沙箱元数据。

        返回：
            沙箱看起来仍存活时返回 True。
        """
        ...

    @abstractmethod
    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        """
        用于跨进程恢复：当另一个进程启动了沙箱时，
        当前进程可通过确定性容器名或 URL 发现该实例。

        参数：
            sandbox_id: 要查找的确定性沙箱 ID。

        返回：
            找到且健康时返回 SandboxInfo，否则返回 None。
        """
        ...
