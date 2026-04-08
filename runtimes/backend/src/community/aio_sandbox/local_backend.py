"""
在本地机器上使用 Docker 或 Apple Container 管理沙箱容器。
负责容器生命周期、端口分配与跨进程容器发现。
"""

from __future__ import annotations

import logging
import os
import subprocess

from src.utils.network import get_free_port, release_port

from .backend import SandboxBackend, wait_for_sandbox_ready
from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)


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
        container_name = f"{self._container_prefix}-{sandbox_id}"

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
            except RuntimeError as exc:
                release_port(port)
                err = str(exc)
                err_lower = err.lower()
                # 端口已被占用：跳过当前端口，重试下一个。
                if "port is already allocated" in err or "address already in use" in err_lower:
                    logger.warning(f"Port {port} rejected by Docker (already allocated), retrying with next port")
                    _next_start = port + 1
                    continue
                # 容器名冲突：可能有其他进程已为该 sandbox_id 启动容器。
                # 尝试发现并接管已有容器，而不是直接失败。
                if "is already in use by container" in err_lower or "conflict. the container name" in err_lower:
                    logger.warning(f"Container name {container_name} already in use, attempting to discover existing sandbox instance")
                    existing = self.discover(sandbox_id)
                    if existing is not None:
                        return existing
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

    def destroy(self, info: SandboxInfo) -> None:
        """停止容器并释放其端口。"""
        if info.container_id:
            self._stop_container(info.container_id)
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
        container_name = f"{self._container_prefix}-{sandbox_id}"

        if not self._is_container_running(container_name):
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
            cmd.extend(["--security-opt", "seccomp=unconfined"])

        cmd.extend(
            [
                "--rm",
                "-d",
                "-p",
                f"{port}:8080",
                "--name",
                container_name,
            ]
        )

        # 环境变量
        for key, value in self._environment.items():
            cmd.extend(["-e", f"{key}={value}"])

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

        logger.info(f"Starting container using {self._runtime}: {' '.join(cmd)}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            container_id = result.stdout.strip()
            logger.info(f"Started container {container_name} (ID: {container_id}) using {self._runtime}")
            return container_id
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start container using {self._runtime}: {e.stderr}")
            raise RuntimeError(f"Failed to start sandbox container: {e.stderr}")

    def _stop_container(self, container_id: str) -> None:
        """停止容器（--rm 会自动删除容器）。"""
        try:
            subprocess.run(
                [self._runtime, "stop", container_id],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info(f"Stopped container {container_id} using {self._runtime}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to stop container {container_id}: {e.stderr}")

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
