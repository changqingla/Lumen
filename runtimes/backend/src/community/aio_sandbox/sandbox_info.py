"""用于跨进程发现与状态持久化的沙箱元数据。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class SandboxInfo:
    """
    该数据类保存跨进程重连既有沙箱所需的全部信息，
    适用于不同进程（如 gateway 与 langgraph）、多 worker，
    或跨 K8s Pod 且共享存储的场景。

    """

    sandbox_id: str
    sandbox_url: str  # 例如 http://localhost:8080 或 http://k3s:30001
    container_name: str | None = None  # 仅本地容器后端使用
    container_id: str | None = None  # 仅本地容器后端使用
    created_at: float = field(default_factory=time.time)
