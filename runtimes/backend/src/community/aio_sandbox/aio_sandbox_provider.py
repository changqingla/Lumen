"""
该 Provider 由以下组件组合而成：
- SandboxBackend：决定沙箱如何被供给（本地容器或远程 provisioner）

Provider 自身负责：
- 进程内缓存（提升重复访问性能）
- 空闲超时管理
- 带信号处理的优雅关闭
- 挂载计算（线程目录、技能目录）
"""

import atexit
import fcntl
import logging
import os
import signal
import threading
import time
import uuid

from src.config import get_app_config
from src.config.paths import VIRTUAL_PATH_PREFIX, Paths, get_paths
from src.sandbox.sandbox import Sandbox
from src.sandbox.sandbox_provider import SandboxProvider
from src.sandbox_provisioner.contract import deterministic_sandbox_id

from .aio_sandbox import AioSandbox
from .backend import SandboxBackend, wait_for_sandbox_ready
from .local_backend import LocalContainerBackend
from .remote_backend import RemoteSandboxBackend
from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_IMAGE = (
    "crpi-wh1i56a4x558rrhm.cn-hangzhou.personal.cr.aliyuncs.com/"
    "changqinga/sandbox@sha256:"
    "742062f99915e5495df8d4bfeaf40a93197c87c7c47b4e2407cd2b6356df8f48"
)
DEFAULT_PORT = 8080
DEFAULT_CONTAINER_PREFIX = "lumen-sandbox"
DEFAULT_IDLE_TIMEOUT = 600  # 10 分钟（秒）
DEFAULT_REPLICAS = 5  # 最大并发沙箱容器数
IDLE_CHECK_INTERVAL = 60  # 每 60 秒检查一次


class AioSandboxProvider(SandboxProvider):
    """
    架构：
        Provider 组合 SandboxBackend（供给方式），支持：
        - 本地 Docker/Apple Container 模式（自动启停容器）
        - 远程 provisioner 模式（通过内部 API 管理沙箱容器）

    在 config.yaml 的 sandbox 下可配置：
        use: src.community.aio_sandbox:AioSandboxProvider
        image: <container image>
        port: 8080                      # 本地容器基础端口
        container_prefix: lumen-sandbox
        idle_timeout: 600               # 空闲超时（秒，0 表示禁用）
        replicas: 5                     # 最大并发容器数（超限后按 LRU 驱逐）
        mounts:                         # 本地容器挂载
          - host_path: /path/on/host
            container_path: /path/in/container
            read_only: false
        environment:                    # 容器环境变量
          NODE_ENV: production
          API_KEY: $MY_API_KEY
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sandboxes: dict[str, AioSandbox] = {}  # sandbox_id -> AioSandbox 实例
        self._sandbox_infos: dict[str, SandboxInfo] = {}  # sandbox_id -> SandboxInfo（供 destroy 使用）
        self._thread_sandboxes: dict[str, str] = {}  # thread_id -> sandbox_id
        self._thread_locks: dict[str, threading.Lock] = {}  # thread_id -> 进程内锁
        self._last_activity: dict[str, float] = {}  # sandbox_id -> 最近活跃时间戳
        # 预热池（warm pool）：已释放但容器仍在运行的沙箱。
        # 结构：sandbox_id -> (SandboxInfo, release_timestamp)。
        # 容器可被快速回收（避免冷启动），或在 replicas 满载时销毁。
        self._warm_pool: dict[str, tuple[SandboxInfo, float]] = {}
        self._shutdown_called = False
        self._idle_checker_stop = threading.Event()
        self._idle_checker_thread: threading.Thread | None = None

        self._config = self._load_config()
        self._backend: SandboxBackend = self._create_backend()

        # 注册关闭处理
        atexit.register(self.shutdown)
        self._register_signal_handlers()

        # 若启用空闲检查则启动后台线程
        if self._config.get("idle_timeout", DEFAULT_IDLE_TIMEOUT) > 0:
            self._start_idle_checker()

    # ── 工厂方法 ───────────────────────────────────────────────────────────

    def _create_backend(self) -> SandboxBackend:
        """
        选择逻辑（按顺序）：
        1. 设置了 ``provisioner_url`` → RemoteSandboxBackend（provisioner 模式）
              provisioner 通过受约束的 Docker 控制面动态创建容器。
        2. 否则默认 → LocalContainerBackend（本地模式）
              由本地 provider 直接管理容器启停。

        """
        provisioner_url = self._config.get("provisioner_url")
        if provisioner_url:
            logger.info("Using authenticated remote sandbox provisioner")
            return RemoteSandboxBackend(provisioner_url=provisioner_url)

        logger.info("Using local container sandbox backend")
        return LocalContainerBackend(
            image=self._config["image"],
            base_port=self._config["port"],
            container_prefix=self._config["container_prefix"],
            config_mounts=self._config["mounts"],
            environment=self._config["environment"],
            pids_limit=self._config["pids_limit"],
            drop_all_capabilities=self._config["drop_all_capabilities"],
        )

    # ── 配置 ───────────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        """从应用配置加载沙箱配置。"""
        config = get_app_config()
        sandbox_config = config.sandbox

        idle_timeout = getattr(sandbox_config, "idle_timeout", None)
        replicas = getattr(sandbox_config, "replicas", None)

        return {
            "image": sandbox_config.image or DEFAULT_IMAGE,
            "port": sandbox_config.port or DEFAULT_PORT,
            "container_prefix": sandbox_config.container_prefix or DEFAULT_CONTAINER_PREFIX,
            "idle_timeout": idle_timeout if idle_timeout is not None else DEFAULT_IDLE_TIMEOUT,
            "replicas": replicas if replicas is not None else DEFAULT_REPLICAS,
            "mounts": sandbox_config.mounts or [],
            "environment": self._resolve_env_vars(sandbox_config.environment or {}),
            "pids_limit": sandbox_config.pids_limit,
            "drop_all_capabilities": sandbox_config.drop_all_capabilities,
            # Compose can force the constrained provisioner without mutating the
            # shared config file used by non-Docker development environments.
            "provisioner_url": os.environ.get("SANDBOX_PROVISIONER_URL")
            or getattr(sandbox_config, "provisioner_url", None)
            or "",
        }

    @staticmethod
    def _resolve_env_vars(env_config: dict[str, str]) -> dict[str, str]:
        """解析环境变量引用（以 `$` 开头的值）。"""
        resolved = {}
        for key, value in env_config.items():
            if isinstance(value, str) and value.startswith("$"):
                env_name = value[1:]
                resolved[key] = os.environ.get(env_name, "")
            else:
                resolved[key] = str(value)
        return resolved

    # ── 确定性 ID ──────────────────────────────────────────────────────────

    @staticmethod
    def _deterministic_sandbox_id(thread_id: str) -> str:
        """
        保证同一 thread_id 在所有进程得到相同 sandbox_id，
        从而无需共享内存也可做跨进程发现。

        """
        return deterministic_sandbox_id(thread_id)

    # ── 挂载辅助 ───────────────────────────────────────────────────────────

    def _get_extra_mounts(self, thread_id: str | None) -> list[tuple[str, str, bool]]:
        """收集沙箱所需额外挂载（线程目录 + 技能目录）。"""
        mounts: list[tuple[str, str, bool]] = []

        if thread_id:
            mounts.extend(self._get_thread_mounts(thread_id))
            logger.info("Adding %d thread sandbox mounts", len(mounts))

        skills_mount = self._get_skills_mount()
        if skills_mount:
            mounts.append(skills_mount)
            logger.info("Adding read-only skills mount")

        return mounts

    @staticmethod
    def _get_thread_mounts(thread_id: str) -> list[tuple[str, str, bool]]:
        """
        若目录不存在会懒创建。
        挂载源使用 host_base_dir，确保在 Docker 内运行且挂载 Docker socket（DooD）时，
        宿主机 Docker daemon 能正确解析路径。

        """
        paths = get_paths()
        paths.ensure_thread_dirs(thread_id)

        # 设置了 LUMEN_HOST_BASE_DIR 时，host_paths 解析到宿主机基目录；
        # 若未设置，则回退到容器自身基目录（原生模式）。
        host_paths = Paths(base_dir=paths.host_base_dir)

        return [
            (str(host_paths.sandbox_work_dir(thread_id)), f"{VIRTUAL_PATH_PREFIX}/workspace", False),
            (str(host_paths.sandbox_uploads_dir(thread_id)), f"{VIRTUAL_PATH_PREFIX}/uploads", False),
            (str(host_paths.sandbox_knowledge_dir(thread_id)), f"{VIRTUAL_PATH_PREFIX}/knowledge", True),
            (str(host_paths.sandbox_outputs_dir(thread_id)), f"{VIRTUAL_PATH_PREFIX}/outputs", False),
        ]

    @staticmethod
    def _get_skills_mount() -> tuple[str, str, bool] | None:
        """
        在 Docker 内运行（DooD）时，挂载源使用 LUMEN_HOST_SKILLS_PATH，
        以保证宿主机 Docker daemon 可解析该路径。

        """
        try:
            config = get_app_config()
            skills_path = config.skills.get_skills_path()
            container_path = config.skills.container_path

            if skills_path.exists():
                # 在 Docker + DooD 场景下优先使用宿主机技能目录路径
                host_skills = os.environ.get("LUMEN_HOST_SKILLS_PATH") or str(skills_path)
                return (host_skills, container_path, True)  # 出于安全考虑只读挂载
        except Exception as exc:
            logger.warning(
                "Could not setup skills mount (%s)",
                type(exc).__name__,
            )
        return None

    # ── 空闲超时管理 ───────────────────────────────────────────────────────

    def _start_idle_checker(self) -> None:
        """启动后台线程，定期检查空闲沙箱。"""
        self._idle_checker_thread = threading.Thread(
            target=self._idle_checker_loop,
            name="sandbox-idle-checker",
            daemon=True,
        )
        self._idle_checker_thread.start()
        logger.info(f"Started idle checker thread (timeout: {self._config.get('idle_timeout', DEFAULT_IDLE_TIMEOUT)}s)")

    def _idle_checker_loop(self) -> None:
        idle_timeout = self._config.get("idle_timeout", DEFAULT_IDLE_TIMEOUT)
        while not self._idle_checker_stop.wait(timeout=IDLE_CHECK_INTERVAL):
            try:
                self._cleanup_idle_sandboxes(idle_timeout)
            except Exception as exc:
                logger.error(
                    "Error in idle checker loop (%s)",
                    type(exc).__name__,
                )

    def _cleanup_idle_sandboxes(self, idle_timeout: float) -> None:
        current_time = time.time()
        active_to_destroy = []
        warm_to_destroy: list[tuple[str, SandboxInfo]] = []

        with self._lock:
            # 活跃沙箱：由 _last_activity 跟踪
            for sandbox_id, last_activity in self._last_activity.items():
                idle_duration = current_time - last_activity
                if idle_duration > idle_timeout:
                    active_to_destroy.append(sandbox_id)
                    logger.info(
                        "Sandbox idle for %.1fs; marking for destroy",
                        idle_duration,
                    )

            # 预热池（warm pool）：由 _warm_pool 中的 release_timestamp 跟踪
            for sandbox_id, (info, release_ts) in list(self._warm_pool.items()):
                warm_duration = current_time - release_ts
                if warm_duration > idle_timeout:
                    warm_to_destroy.append((sandbox_id, info))
                    del self._warm_pool[sandbox_id]
                    logger.info(
                        "Warm-pool sandbox idle for %.1fs; marking for destroy",
                        warm_duration,
                    )

        # 销毁活跃沙箱（执行前再次确认仍为空闲）
        for sandbox_id in active_to_destroy:
            try:
                # 在加锁状态下二次确认沙箱仍为空闲再销毁。
                # 从上方快照到当前，沙箱可能已被重新获取（last_activity 更新）
                # 或已被其他流程释放/销毁。
                with self._lock:
                    last_activity = self._last_activity.get(sandbox_id)
                    if last_activity is None:
                        # 已被其他路径释放或销毁，跳过。
                        logger.info("Sandbox already gone before idle destroy; skipping")
                        continue
                    if (time.time() - last_activity) < idle_timeout:
                        # 快照之后已被重新获取（活跃时间已更新），跳过。
                        logger.info("Sandbox was re-acquired before idle destroy; skipping")
                        continue
                logger.info("Destroying idle sandbox")
                self.destroy(sandbox_id)
            except Exception as exc:
                logger.error(
                    "Failed to destroy idle sandbox (%s)",
                    type(exc).__name__,
                )

        # 销毁 warm-pool 沙箱（前面在锁内已从 _warm_pool 移除）
        for sandbox_id, info in warm_to_destroy:
            try:
                self._backend.destroy(info)
                logger.info("Destroyed idle warm-pool sandbox")
            except Exception as exc:
                logger.error(
                    "Failed to destroy idle warm-pool sandbox (%s)",
                    type(exc).__name__,
                )

    # ── 信号处理 ───────────────────────────────────────────────────────────

    def _register_signal_handlers(self) -> None:
        """注册信号处理器以实现优雅关闭。"""
        self._original_sigterm = signal.getsignal(signal.SIGTERM)
        self._original_sigint = signal.getsignal(signal.SIGINT)

        def signal_handler(signum, frame):
            self.shutdown()
            original = self._original_sigterm if signum == signal.SIGTERM else self._original_sigint
            if callable(original):
                original(signum, frame)
            elif original == signal.SIG_DFL:
                signal.signal(signum, signal.SIG_DFL)
                signal.raise_signal(signum)

        try:
            signal.signal(signal.SIGTERM, signal_handler)
            signal.signal(signal.SIGINT, signal_handler)
        except ValueError:
            logger.debug("Could not register signal handlers (not main thread)")

    # ── 线程锁（进程内） ───────────────────────────────────────────────────

    def _get_thread_lock(self, thread_id: str) -> threading.Lock:
        """获取或创建指定 thread_id 对应的进程内锁。"""
        with self._lock:
            if thread_id not in self._thread_locks:
                self._thread_locks[thread_id] = threading.Lock()
            return self._thread_locks[thread_id]

    # ── 核心流程：acquire / get / release / shutdown ───────────────────────

    def acquire(self, thread_id: str | None = None) -> str:
        """
        对同一 thread_id，该方法在多轮对话、多进程、
        以及共享同一状态目录的多服务实例中都返回同一 sandbox_id。

        同时具备进程内与跨进程锁保护，线程安全。

        参数：
            thread_id: 可选线程 ID，用于线程级配置与复用。

        返回：
            获取到的沙箱环境 ID。
        """
        if thread_id:
            thread_lock = self._get_thread_lock(thread_id)
            with thread_lock:
                return self._acquire_internal(thread_id)
        else:
            return self._acquire_internal(thread_id)

    def _acquire_internal(self, thread_id: str | None) -> str:
        """
        第 1 层：进程内缓存（最快，覆盖同进程重复访问）
        第 2 层：后端发现（覆盖由其他进程启动的容器；
                 sandbox_id 从 thread_id 确定性生成，无需共享状态文件，
                 任意进程都能推导同一容器名）

        """
        # ── 第 1 层：进程内缓存（快速路径） ──
        if thread_id:
            with self._lock:
                if thread_id in self._thread_sandboxes:
                    existing_id = self._thread_sandboxes[thread_id]
                    if existing_id in self._sandboxes:
                        logger.info("Reusing in-process sandbox")
                        self._last_activity[existing_id] = time.time()
                        return existing_id
                    else:
                        del self._thread_sandboxes[thread_id]

        # 线程绑定场景使用确定性 ID，匿名场景使用随机 ID
        sandbox_id = (
            self._deterministic_sandbox_id(thread_id)
            if thread_id
            else uuid.uuid4().hex
        )

        # ── 第 1.5 层：warm pool（容器仍运行，无冷启动） ──
        if thread_id:
            with self._lock:
                if sandbox_id in self._warm_pool:
                    info, _ = self._warm_pool.pop(sandbox_id)
                    sandbox = AioSandbox(id=sandbox_id, base_url=info.sandbox_url)
                    self._sandboxes[sandbox_id] = sandbox
                    self._sandbox_infos[sandbox_id] = info
                    self._last_activity[sandbox_id] = time.time()
                    self._thread_sandboxes[thread_id] = sandbox_id
                    logger.info("Reclaimed warm-pool sandbox")
                    return sandbox_id

        # ── 第 2 层：后端发现 + 创建（受跨进程锁保护） ──
        # 使用文件锁串行化同一 thread_id 的并发创建请求：
        # 后到的进程会发现先到进程创建的容器，而不是直接命中容器名冲突。
        if thread_id:
            return self._discover_or_create_with_lock(thread_id, sandbox_id)

        return self._create_sandbox(thread_id, sandbox_id)

    def _discover_or_create_with_lock(self, thread_id: str, sandbox_id: str) -> str:
        """
        文件锁可在多进程间串行化同一 thread_id 的沙箱创建，
        从而避免容器名冲突。

        """
        paths = get_paths()
        paths.ensure_thread_dirs(thread_id)
        lock_path = paths.thread_dir(thread_id) / f"{sandbox_id}.lock"

        with open(lock_path, "a") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                # 在文件锁下再次检查进程内缓存，避免等待期间同进程其他线程已抢先创建。
                with self._lock:
                    if thread_id in self._thread_sandboxes:
                        existing_id = self._thread_sandboxes[thread_id]
                        if existing_id in self._sandboxes:
                            logger.info(
                                "Reusing in-process sandbox after lock acquisition"
                            )
                            self._last_activity[existing_id] = time.time()
                            return existing_id
                    if sandbox_id in self._warm_pool:
                        info, _ = self._warm_pool.pop(sandbox_id)
                        sandbox = AioSandbox(id=sandbox_id, base_url=info.sandbox_url)
                        self._sandboxes[sandbox_id] = sandbox
                        self._sandbox_infos[sandbox_id] = info
                        self._last_activity[sandbox_id] = time.time()
                        self._thread_sandboxes[thread_id] = sandbox_id
                        logger.info(
                            "Reclaimed warm-pool sandbox after lock acquisition"
                        )
                        return sandbox_id

                # 后端发现：容器可能已由其他进程创建。
                discovered = self._backend.discover(sandbox_id)
                if discovered is not None:
                    sandbox = AioSandbox(id=discovered.sandbox_id, base_url=discovered.sandbox_url)
                    with self._lock:
                        self._sandboxes[discovered.sandbox_id] = sandbox
                        self._sandbox_infos[discovered.sandbox_id] = discovered
                        self._last_activity[discovered.sandbox_id] = time.time()
                        self._thread_sandboxes[thread_id] = discovered.sandbox_id
                    logger.info("Discovered existing sandbox")
                    return discovered.sandbox_id

                return self._create_sandbox(thread_id, sandbox_id)
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _evict_oldest_warm(self) -> str | None:
        """
        返回：
            被驱逐的 sandbox_id；若 warm pool 为空则返回 None。
        """
        with self._lock:
            if not self._warm_pool:
                return None
            oldest_id = min(self._warm_pool, key=lambda sid: self._warm_pool[sid][1])
            info, _ = self._warm_pool.pop(oldest_id)

        try:
            self._backend.destroy(info)
            logger.info("Destroyed warm-pool sandbox")
        except Exception as exc:
            logger.error(
                "Failed to destroy warm-pool sandbox (%s)",
                type(exc).__name__,
            )
            return None
        return oldest_id

    def _create_sandbox(self, thread_id: str | None, sandbox_id: str) -> str:
        """
        参数：
            thread_id: 可选线程 ID。
            sandbox_id: 要使用的沙箱 ID。

        返回：
            sandbox_id。

        异常：
            RuntimeError: 创建失败或就绪检查失败时抛出。
        """
        extra_mounts = self._get_extra_mounts(thread_id)

        # 执行 replicas 约束：仅 warm-pool 容器计入可驱逐预算。
        # 活跃沙箱正在服务线程，不能强制停止。
        replicas = self._config.get("replicas", DEFAULT_REPLICAS)
        with self._lock:
            total = len(self._sandboxes) + len(self._warm_pool)
        if total >= replicas:
            evicted = self._evict_oldest_warm()
            if evicted:
                logger.info(
                    "Evicted warm-pool sandbox to stay within replicas=%s",
                    replicas,
                )
            else:
                # 所有槽位均被活跃沙箱占用：继续创建并记录告警。
                # 副本数上限（replicas）是软限制，不会强停正在服务线程的容器。
                logger.warning(
                    "All %s replica slots are active; exceeding the soft limit",
                    replicas,
                )

        info = self._backend.create(thread_id, sandbox_id, extra_mounts=extra_mounts or None)

        # 等待沙箱就绪
        if not wait_for_sandbox_ready(info.sandbox_url, timeout=60):
            # A verified legacy alias predates this create request. Do not
            # destroy it merely because readiness failed during migration.
            if not info.provisioned_sandbox_id:
                self._backend.destroy(info)
            raise RuntimeError("Sandbox failed to become ready within timeout")

        sandbox = AioSandbox(id=sandbox_id, base_url=info.sandbox_url)
        with self._lock:
            self._sandboxes[sandbox_id] = sandbox
            self._sandbox_infos[sandbox_id] = info
            self._last_activity[sandbox_id] = time.time()
            if thread_id:
                self._thread_sandboxes[thread_id] = sandbox_id

        logger.info("Created sandbox")
        return sandbox_id

    def get(self, sandbox_id: str) -> Sandbox | None:
        """
        参数：
            sandbox_id: 沙箱 ID。

        返回：
            找到则返回沙箱实例，否则返回 None。
        """
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            if sandbox is not None:
                self._last_activity[sandbox_id] = time.time()
            return sandbox

    def release(self, sandbox_id: str) -> None:
        """
        与 destroy 不同，release 不会停止容器。
        容器会继续运行，可被同一线程在下一轮快速回收，避免冷启动。
        容器仅在 replicas 约束触发驱逐或 shutdown 时停止。

        参数：
            sandbox_id: 要释放的沙箱 ID。
        """
        info = None
        thread_ids_to_remove: list[str] = []

        with self._lock:
            self._sandboxes.pop(sandbox_id, None)
            info = self._sandbox_infos.pop(sandbox_id, None)
            thread_ids_to_remove = [tid for tid, sid in self._thread_sandboxes.items() if sid == sandbox_id]
            for tid in thread_ids_to_remove:
                del self._thread_sandboxes[tid]
            self._last_activity.pop(sandbox_id, None)
            # 放入 warm pool，容器继续保持运行
            if info and sandbox_id not in self._warm_pool:
                self._warm_pool[sandbox_id] = (info, time.time())

        logger.info("Released sandbox to warm pool (container still running)")

    def destroy(self, sandbox_id: str) -> None:
        """
        与 release 不同，destroy 会真正停止容器。
        适用于显式清理、容量驱逐或 shutdown 场景。

        参数：
            sandbox_id: 要销毁的沙箱 ID。
        """
        info = None
        thread_ids_to_remove: list[str] = []

        with self._lock:
            self._sandboxes.pop(sandbox_id, None)
            info = self._sandbox_infos.pop(sandbox_id, None)
            thread_ids_to_remove = [tid for tid, sid in self._thread_sandboxes.items() if sid == sandbox_id]
            for tid in thread_ids_to_remove:
                del self._thread_sandboxes[tid]
            self._last_activity.pop(sandbox_id, None)
            # 若在 warm pool 中也有该实例，一并取出
            if info is None and sandbox_id in self._warm_pool:
                info, _ = self._warm_pool.pop(sandbox_id)
            else:
                self._warm_pool.pop(sandbox_id, None)

        if info:
            self._backend.destroy(info)
            logger.info("Destroyed sandbox")

    def shutdown(self) -> None:
        """关闭全部沙箱。线程安全且幂等。"""
        with self._lock:
            if self._shutdown_called:
                return
            self._shutdown_called = True
            sandbox_ids = list(self._sandboxes.keys())
            warm_items = list(self._warm_pool.items())
            self._warm_pool.clear()

        # 停止空闲检查线程
        self._idle_checker_stop.set()
        if self._idle_checker_thread is not None and self._idle_checker_thread.is_alive():
            self._idle_checker_thread.join(timeout=5)
            logger.info("Stopped idle checker thread")

        logger.info(f"Shutting down {len(sandbox_ids)} active + {len(warm_items)} warm-pool sandbox(es)")

        for sandbox_id in sandbox_ids:
            try:
                self.destroy(sandbox_id)
            except Exception as exc:
                logger.error(
                    "Failed to destroy sandbox during shutdown (%s)",
                    type(exc).__name__,
                )

        for sandbox_id, (info, _) in warm_items:
            try:
                self._backend.destroy(info)
                logger.info("Destroyed warm-pool sandbox during shutdown")
            except Exception as exc:
                logger.error(
                    "Failed to destroy warm-pool sandbox during shutdown (%s)",
                    type(exc).__name__,
                )

        close_backend = getattr(self._backend, "close", None)
        if callable(close_backend):
            close_backend()
