"""
在本地机器上使用 Docker 或 Apple Container 管理沙箱容器。
负责容器生命周期、端口分配与跨进程容器发现。
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import subprocess

from src.sandbox_provisioner.contract import (
    deterministic_sandbox_id,
    legacy_deterministic_sandbox_id,
)
from src.utils.network import get_free_port, release_port

from .backend import SandboxBackend, wait_for_sandbox_ready
from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)

_SAFE_CONTAINER_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_CAPABILITY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class _ContainerPortConflict(RuntimeError):
    """Docker rejected the requested published port."""


class _ContainerNameConflict(RuntimeError):
    """Docker reported an existing container with the requested name."""


class LocalContainerBackend(SandboxBackend):
    """
    在 macOS 上，若可用优先使用 Apple Container，否则回退 Docker。
    其他平台默认使用 Docker。

    特性：
    - 基于确定性容器命名实现跨进程发现
    - 使用线程安全工具进行端口分配
    - 管理容器生命周期（使用 --rm 启停）
    - 支持挂载卷与环境变量注入
    """

    def __init__(
        self,
        *,
        image: str,
        base_port: int,
        container_prefix: str,
        config_mounts: list,
        environment: dict[str, str],
        pids_limit: int = 512,
        drop_all_capabilities: bool = True,
        capability_additions: tuple[str, ...] = (),
        labels: dict[str, str] | None = None,
        read_only_rootfs: bool = False,
        tmpfs_mounts: tuple[str, ...] = (),
    ):
        """
        参数：
            image: 使用的容器镜像。
            base_port: 查找可用端口的起始基准端口。
            container_prefix: 容器名前缀（例如 "lumen-sandbox"）。
            config_mounts: 来自配置的卷挂载（VolumeMountConfig 列表）。
            environment: 注入容器的环境变量。

        """
        self._image = image
        self._base_port = base_port
        self._container_prefix = container_prefix
        self._config_mounts = config_mounts
        self._environment = environment
        self._pids_limit = pids_limit
        self._drop_all_capabilities = drop_all_capabilities
        self._capability_additions = tuple(capability_additions)
        self._labels = dict(labels or {})
        self._read_only_rootfs = read_only_rootfs
        self._tmpfs_mounts = tuple(tmpfs_mounts)
        if _SAFE_CONTAINER_COMPONENT_RE.fullmatch(container_prefix) is None:
            raise ValueError("container_prefix contains unsafe characters")
        if not 32 <= pids_limit <= 4096:
            raise ValueError("pids_limit must be between 32 and 4096")
        if any(_SAFE_CAPABILITY_RE.fullmatch(cap) is None for cap in self._capability_additions):
            raise ValueError("capability_additions contains an invalid Linux capability")
        self._runtime = self._detect_runtime()

    @property
    def runtime(self) -> str:
        """检测出的容器运行时（"docker" 或 "container"）。"""
        return self._runtime

    def _detect_runtime(self) -> str:
        """
        在 macOS 上优先使用 Apple Container，否则回退 Docker。
        其他平台使用 Docker。

        返回：
            Apple Container 返回 "container"，Docker 返回 "docker"。
        """
        import platform

        if platform.system() == "Darwin":
            try:
                result = subprocess.run(
                    ["container", "--version"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                logger.info(f"Detected Apple Container: {result.stdout.strip()}")
                return "container"
            except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                logger.info("Apple Container not available, falling back to Docker")

        return "docker"

    # ── SandboxBackend 接口 ───────────────────────────────────────────────

    def create(self, thread_id: str, sandbox_id: str, extra_mounts: list[tuple[str, str, bool]] | None = None) -> SandboxInfo:
        """
        参数：
            thread_id: 创建沙箱对应的线程 ID，可用于按线程组织沙箱。
            sandbox_id: 确定性沙箱 ID（用于容器命名）。
            extra_mounts: 额外挂载列表，格式为 (host_path, container_path, read_only)。

        返回：
            含容器详情的 SandboxInfo。

        异常：
            RuntimeError: 容器启动失败时抛出。
        """
        legacy = self._discover_verified_legacy_alias(
            thread_id,
            sandbox_id,
            extra_mounts or [],
        )
        if legacy is not None:
            return legacy

        container_name = self._container_name(sandbox_id)

        # 重试逻辑：若 Docker 拒绝端口（例如进程重启后旧容器仍占用绑定），
        # 则跳过当前端口并尝试下一个。get_free_port 的 socket 绑定检测
        # 与 Docker 的 0.0.0.0 绑定一致，但 Docker 释放端口可能存在轻微延迟，
        # 因此这里做兜底重试以保证始终可推进。
        _next_start = self._base_port
        container_id: str | None = None
        port: int = 0
        for _attempt in range(10):
            port = get_free_port(start_port=_next_start)
            try:
                container_id = self._start_container(container_name, port, extra_mounts)
                break
            except _ContainerPortConflict:
                release_port(port)
                logger.warning(
                    "Sandbox port was already allocated; retrying with the next port"
                )
                _next_start = port + 1
                continue
            except _ContainerNameConflict:
                release_port(port)
                logger.warning(
                    "Sandbox container name is already in use; attempting discovery"
                )
                existing = self.discover(sandbox_id)
                if existing is not None:
                    return existing
                raise
            except RuntimeError:
                release_port(port)
                raise
        else:
            raise RuntimeError("Could not start sandbox container: all candidate ports are already allocated by Docker")

        # 在 Docker 内运行（DooD）时，沙箱容器应通过 host.docker.internal 访问，
        # 而不是 localhost（容器实际运行在宿主机 Docker daemon）。
        sandbox_host = os.environ.get("LUMEN_SANDBOX_HOST", "localhost")
        return SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_url=f"http://{sandbox_host}:{port}",
            container_name=container_name,
            container_id=container_id,
        )

    def _discover_verified_legacy_alias(
        self,
        thread_id: str,
        sandbox_id: str,
        expected_mounts: list[tuple[str, str, bool]],
    ) -> SandboxInfo | None:
        """Adopt an old 8-hex container only when its thread mounts match."""

        try:
            if sandbox_id != deterministic_sandbox_id(thread_id):
                return None
            legacy_id = legacy_deterministic_sandbox_id(thread_id)
        except ValueError:
            return None

        container_name = self._container_name(legacy_id)
        if not self._is_container_running(
            container_name
        ) or not self._has_required_labels(container_name):
            return None
        if not self._has_expected_mounts(container_name, expected_mounts):
            logger.warning(
                "Refusing to adopt a legacy sandbox whose mounts do not match"
            )
            return None

        port = self._get_container_port(container_name)
        if port is None:
            raise RuntimeError(
                "Verified legacy sandbox exists without a published control port"
            )
        sandbox_host = os.environ.get("LUMEN_SANDBOX_HOST", "localhost")
        sandbox_url = f"http://{sandbox_host}:{port}"
        if not wait_for_sandbox_ready(sandbox_url, timeout=5):
            raise RuntimeError("Verified legacy sandbox is not ready")

        logger.info("Adopting a verified legacy sandbox under the current ID")
        return SandboxInfo(
            sandbox_id=sandbox_id,
            provisioned_sandbox_id=legacy_id,
            sandbox_url=sandbox_url,
            container_name=container_name,
        )

    def destroy(self, info: SandboxInfo) -> None:
        """停止容器并释放其端口。"""
        container_ref = info.container_id or info.container_name
        if container_ref:
            self._stop_container(container_ref)
        # 从 sandbox_url 提取端口并释放
        try:
            from urllib.parse import urlparse

            port = urlparse(info.sandbox_url).port
            if port:
                release_port(port)
        except Exception:
            pass

    def is_alive(self, info: SandboxInfo) -> bool:
        """检查容器是否仍在运行（轻量检查，不走 HTTP）。"""
        if info.container_name:
            return self._is_container_running(info.container_name)
        return False

    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        """
        检查预期容器名是否在运行，获取其端口，并验证健康检查可达。

        参数：
            sandbox_id: 确定性沙箱 ID（用于推导容器名）。

        返回：
            找到且健康则返回 SandboxInfo，否则返回 None。
        """
        container_name = self._container_name(sandbox_id)

        if not self._is_container_running(container_name) or not self._has_required_labels(
            container_name
        ):
            return None

        port = self._get_container_port(container_name)
        if port is None:
            return None

        sandbox_host = os.environ.get("LUMEN_SANDBOX_HOST", "localhost")
        sandbox_url = f"http://{sandbox_host}:{port}"
        if not wait_for_sandbox_ready(sandbox_url, timeout=5):
            return None

        return SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_url=sandbox_url,
            container_name=container_name,
        )

    # ── 容器操作 ───────────────────────────────────────────────────────────

    def _container_name(self, sandbox_id: str) -> str:
        if _SAFE_CONTAINER_COMPONENT_RE.fullmatch(sandbox_id) is None:
            raise ValueError("sandbox_id contains unsafe container-name characters")
        name = f"{self._container_prefix}-{sandbox_id}"
        if len(name) > 255:
            raise ValueError("sandbox container name is too long")
        return name

    @staticmethod
    def _resolve_publish_host() -> str:
        """Resolve a non-public host address for the sandbox control port."""
        configured_host = os.environ.get("LUMEN_SANDBOX_BIND_HOST", "127.0.0.1").strip()
        if not configured_host:
            raise RuntimeError("LUMEN_SANDBOX_BIND_HOST must not be empty")

        try:
            addresses = {
                ipaddress.ip_address(sockaddr[0])
                for _family, _socktype, _proto, _canonname, sockaddr in socket.getaddrinfo(
                    configured_host,
                    None,
                    type=socket.SOCK_STREAM,
                )
            }
        except (OSError, ValueError) as exc:
            raise RuntimeError("Could not resolve the configured sandbox bind host") from exc

        allowed = sorted(
            (
                address
                for address in addresses
                if not address.is_unspecified
                and not address.is_multicast
                and not address.is_global
            ),
            key=lambda address: (address.version != 4, str(address)),
        )
        if not allowed:
            raise RuntimeError(
                "Sandbox control ports may only bind to a loopback, private, or link-local host address"
            )
        return str(allowed[0])

    @staticmethod
    def _format_publish_mapping(host: str, port: int) -> str:
        formatted_host = f"[{host}]" if ":" in host else host
        return f"{formatted_host}:{port}:8080"

    def _start_container(
        self,
        container_name: str,
        port: int,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> str:
        """
        参数：
            container_name: 容器名。
            port: 宿主机端口（映射到容器 8080）。
            extra_mounts: 额外挂载列表。

        返回：
            容器 ID。

        异常：
            RuntimeError: 容器启动失败时抛出。
        """
        cmd = [self._runtime, "run"]

        # 面向 Docker 运行时的专属安全参数
        if self._runtime == "docker":
            cmd.extend(
                [
                    "--security-opt",
                    "no-new-privileges:true",
                    "--privileged=false",
                    "--pids-limit",
                    str(self._pids_limit),
                ]
            )
            if self._drop_all_capabilities:
                cmd.extend(["--cap-drop", "ALL"])
            for capability in self._capability_additions:
                cmd.extend(["--cap-add", capability])
            if self._read_only_rootfs:
                cmd.append("--read-only")
            for tmpfs in self._tmpfs_mounts:
                cmd.extend(["--tmpfs", tmpfs])

        publish_host = self._resolve_publish_host()

        cmd.extend(
            [
                "--rm",
                "-d",
                "-p",
                self._format_publish_mapping(publish_host, port),
                "--name",
                container_name,
            ]
        )

        # 环境变量
        for key, value in self._environment.items():
            cmd.extend(["-e", f"{key}={value}"])

        for key, value in self._labels.items():
            cmd.extend(["--label", f"{key}={value}"])

        # 配置层挂载
        for mount in self._config_mounts:
            mount_spec = f"{mount.host_path}:{mount.container_path}"
            if mount.read_only:
                mount_spec += ":ro"
            cmd.extend(["-v", mount_spec])

        # 额外挂载（线程目录、技能目录等）
        if extra_mounts:
            for host_path, container_path, read_only in extra_mounts:
                mount_spec = f"{host_path}:{container_path}"
                if read_only:
                    mount_spec += ":ro"
                cmd.extend(["-v", mount_spec])

        cmd.append(self._image)

        logger.info(
            "Starting sandbox container %s using %s (image=%s, publish_host=%s, mounts=%s, env_keys=%s)",
            container_name,
            self._runtime,
            self._image,
            publish_host,
            len(self._config_mounts) + len(extra_mounts or []),
            sorted(self._environment),
        )

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
            container_id = result.stdout.strip()
            logger.info(
                "Started container %s (ID: %s) using %s",
                container_name,
                container_id[:12],
                self._runtime,
            )
            return container_id
        except subprocess.TimeoutExpired as exc:
            logger.error("Timed out while starting sandbox container %s", container_name)
            raise RuntimeError("Timed out while starting sandbox container") from exc
        except subprocess.CalledProcessError as exc:
            stderr = str(exc.stderr or "").lower()
            if (
                "port is already allocated" in stderr
                or "address already in use" in stderr
            ):
                raise _ContainerPortConflict(
                    "Sandbox port is already allocated"
                ) from exc
            if (
                "is already in use by container" in stderr
                or "conflict. the container name" in stderr
            ):
                raise _ContainerNameConflict(
                    "Sandbox container name is already in use"
                ) from exc
            logger.error("Failed to start sandbox container")
            raise RuntimeError("Failed to start sandbox container") from exc

    def _stop_container(self, container_id: str) -> None:
        """停止容器（--rm 会自动删除容器）。"""
        if _SAFE_CONTAINER_COMPONENT_RE.fullmatch(container_id) is None:
            raise ValueError("container reference contains unsafe characters")
        try:
            subprocess.run(
                [self._runtime, "stop", container_id],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info(f"Stopped container {container_id} using {self._runtime}")
        except subprocess.CalledProcessError:
            logger.warning("Failed to stop sandbox container")

    def _has_required_labels(self, container_name: str) -> bool:
        if not self._labels:
            return True
        try:
            result = subprocess.run(
                [self._runtime, "inspect", "-f", "{{json .Config.Labels}}", container_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return False
            labels = json.loads(result.stdout or "null")
            return isinstance(labels, dict) and all(
                labels.get(key) == value for key, value in self._labels.items()
            )
        except (json.JSONDecodeError, subprocess.TimeoutExpired):
            return False

    def _has_expected_mounts(
        self,
        container_name: str,
        expected_mounts: list[tuple[str, str, bool]],
    ) -> bool:
        """Verify legacy bind sources and read-only modes before reuse."""

        if self._runtime != "docker" or not expected_mounts:
            return False
        try:
            result = subprocess.run(
                [self._runtime, "inspect", "-f", "{{json .Mounts}}", container_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return False
            mounts = json.loads(result.stdout or "null")
            if not isinstance(mounts, list):
                return False
            by_destination = {
                item.get("Destination"): item
                for item in mounts
                if isinstance(item, dict)
                and isinstance(item.get("Destination"), str)
            }
            for host_path, container_path, read_only in expected_mounts:
                mount = by_destination.get(container_path)
                if (
                    not isinstance(mount, dict)
                    or mount.get("Type") != "bind"
                    or mount.get("Source") != host_path
                    or mount.get("RW") != (not read_only)
                ):
                    return False
            return True
        except (json.JSONDecodeError, subprocess.TimeoutExpired):
            return False

    def _is_container_running(self, container_name: str) -> bool:
        """
        用于跨进程容器发现：任意进程都可通过确定性容器名
        检测另一个进程启动的容器。

        """
        try:
            result = subprocess.run(
                [self._runtime, "inspect", "-f", "{{.State.Running}}", container_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0 and result.stdout.strip().lower() == "true"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False

    def _get_container_port(self, container_name: str) -> int | None:
        """
        参数：
            container_name: 要检查的容器名。

        返回：
            映射到容器 8080 的宿主机端口；未找到返回 None。
        """
        try:
            result = subprocess.run(
                [self._runtime, "port", container_name, "8080"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                # 输出格式如 "0.0.0.0:PORT" 或 ":::PORT"
                port_str = result.stdout.strip().split(":")[-1]
                return int(port_str)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
            pass
        return None
