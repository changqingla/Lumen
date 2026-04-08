"""
provisioner 会在 k3s 中按 sandbox_id 动态创建 Pod 与 NodePort Service。
后端通过 ``k3s:{NodePort}`` 直接访问对应沙箱 Pod。

架构示意：
    ┌────────────┐  HTTP   ┌─────────────┐  K8s API  ┌──────────┐
    │ this file  │ ──────▸ │ provisioner │ ────────▸ │   k3s    │
    │ (backend)  │         │ :8002       │           │ :6443    │
    └────────────┘         └─────────────┘           └─────┬────┘
                                                           │ creates
                           ┌─────────────┐           ┌─────▼──────┐
                           │   backend   │ ────────▸ │  sandbox   │
                           │             │  direct   │  Pod(s)    │
                           └─────────────┘ k3s:NPort └────────────┘
"""

from __future__ import annotations

import logging

import requests

from .backend import SandboxBackend
from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)


class RemoteSandboxBackend(SandboxBackend):
    """
    Pod 的创建、销毁与发现均由 provisioner 负责，
    本后端仅作为轻量 HTTP 客户端。

    典型 config.yaml 示例::

        sandbox:
          use: src.community.aio_sandbox:AioSandboxProvider
          provisioner_url: http://provisioner:8002
    """

    def __init__(self, provisioner_url: str):
        """
        参数：
            provisioner_url: provisioner 服务地址
                             （例如 ``http://provisioner:8002``）。

        """
        self._provisioner_url = provisioner_url.rstrip("/")

    @property
    def provisioner_url(self) -> str:
        return self._provisioner_url

    # ── SandboxBackend 接口 ───────────────────────────────────────────────

    def create(
        self,
        thread_id: str,
        sandbox_id: str,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> SandboxInfo:
        """
        调用 ``POST /api/sandboxes``，在 k3s 创建专属 Pod + NodePort Service。
        """
        return self._provisioner_create(thread_id, sandbox_id, extra_mounts)

    def destroy(self, info: SandboxInfo) -> None:
        """通过 provisioner 销毁沙箱 Pod + Service。"""
        self._provisioner_destroy(info.sandbox_id)

    def is_alive(self, info: SandboxInfo) -> bool:
        """检查沙箱 Pod 是否处于运行状态。"""
        return self._provisioner_is_alive(info.sandbox_id)

    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        """
        调用 ``GET /api/sandboxes/{sandbox_id}``，
        若 Pod 存在则返回其信息。

        """
        return self._provisioner_discover(sandbox_id)

    # ── Provisioner API 调用 ──────────────────────────────────────────────

    def _provisioner_create(self, thread_id: str, sandbox_id: str, extra_mounts: list[tuple[str, str, bool]] | None = None) -> SandboxInfo:
        """调用 `POST /api/sandboxes`：创建 Pod + Service。"""
        try:
            resp = requests.post(
                f"{self._provisioner_url}/api/sandboxes",
                json={
                    "sandbox_id": sandbox_id,
                    "thread_id": thread_id,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"Provisioner created sandbox {sandbox_id}: sandbox_url={data['sandbox_url']}")
            return SandboxInfo(
                sandbox_id=sandbox_id,
                sandbox_url=data["sandbox_url"],
            )
        except requests.RequestException as exc:
            logger.error(f"Provisioner create failed for {sandbox_id}: {exc}")
            raise RuntimeError(f"Provisioner create failed: {exc}") from exc

    def _provisioner_destroy(self, sandbox_id: str) -> None:
        """调用 `DELETE /api/sandboxes/{sandbox_id}`：销毁 Pod + Service。"""
        try:
            resp = requests.delete(
                f"{self._provisioner_url}/api/sandboxes/{sandbox_id}",
                timeout=15,
            )
            if resp.ok:
                logger.info(f"Provisioner destroyed sandbox {sandbox_id}")
            else:
                logger.warning(f"Provisioner destroy returned {resp.status_code}: {resp.text}")
        except requests.RequestException as exc:
            logger.warning(f"Provisioner destroy failed for {sandbox_id}: {exc}")

    def _provisioner_is_alive(self, sandbox_id: str) -> bool:
        """调用 `GET /api/sandboxes/{sandbox_id}`：检查 Pod 阶段。"""
        try:
            resp = requests.get(
                f"{self._provisioner_url}/api/sandboxes/{sandbox_id}",
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                return data.get("status") == "Running"
            return False
        except requests.RequestException:
            return False

    def _provisioner_discover(self, sandbox_id: str) -> SandboxInfo | None:
        """调用 `GET /api/sandboxes/{sandbox_id}`：发现已有沙箱。"""
        try:
            resp = requests.get(
                f"{self._provisioner_url}/api/sandboxes/{sandbox_id}",
                timeout=10,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return SandboxInfo(
                sandbox_id=sandbox_id,
                sandbox_url=data["sandbox_url"],
            )
        except requests.RequestException as exc:
            logger.debug(f"Provisioner discover failed for {sandbox_id}: {exc}")
            return None
