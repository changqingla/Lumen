import os
import re
from pathlib import Path

# 代理（Agent）在沙箱内看到的虚拟路径前缀
VIRTUAL_PATH_PREFIX = "/mnt/user-data"

_SAFE_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class Paths:
    """
    lumen 应用数据的集中式路径配置。

    目录结构（宿主机侧）：
        {base_dir}/
        ├── memory.json      <-- legacy quarantine; never auto-injected
        ├── memories/
        │   └── {memory_scope}/
        │       ├── memory.json
        │       └── agents/{agent_name}/memory.json
        ├── USER.md          <-- legacy/operator state; not prompt-injected
        ├── agents/
        │   └── {agent_name}/
        │       ├── config.yaml
        │       ├── SOUL.md  <-- Agent 个性/身份设定（与主提示一同注入）
        │       └── memory.json
        └── threads/
            └── {thread_id}/
                └── user-data/         <-- 在沙箱中挂载为 /mnt/user-data/
                    ├── workspace/     <-- /mnt/user-data/workspace/
                    ├── uploads/       <-- /mnt/user-data/uploads/
                    ├── knowledge/     <-- /mnt/user-data/knowledge/ (read-only)
                    └── outputs/       <-- /mnt/user-data/outputs/

    BaseDir 解析顺序（按优先级）：
        1. 构造参数 `base_dir`
        2. `LUMEN_HOME` 环境变量
        3. 本地开发回退：cwd/.lumen（当 cwd 位于 backend/ 目录时）
        4. 默认：$HOME/.lumen
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base_dir = Path(base_dir).resolve() if base_dir is not None else None

    @property
    def host_base_dir(self) -> Path:
        """用于 Docker 卷挂载源的宿主机可见基础目录。

        当在 Docker 中运行且挂载了 Docker socket（DooD）时，Docker 守护进程运行在宿主机上，
        会基于宿主机文件系统解析挂载路径。请将 `LUMEN_HOST_BASE_DIR` 设置为与当前容器
        `base_dir` 对应的宿主机路径，确保沙箱容器卷挂载生效。

        若未设置该环境变量，则回退为 `base_dir`（本地/原生执行模式）。
        """
        if env := os.getenv("LUMEN_HOST_BASE_DIR"):
            return Path(env)
        return self.base_dir

    @property
    def base_dir(self) -> Path:
        """所有应用数据的根目录。"""
        if self._base_dir is not None:
            return self._base_dir

        if env_home := os.getenv("LUMEN_HOME"):
            return Path(env_home).resolve()

        cwd = Path.cwd()
        if cwd.name == "backend" or (cwd / "pyproject.toml").exists():
            return cwd / ".lumen"

        return Path.home() / ".lumen"

    @property
    def memory_file(self) -> Path:
        """Return the quarantined legacy global path (not used for injection)."""
        return self.base_dir / "memory.json"

    @property
    def user_md_file(self) -> Path:
        """Return the legacy/operator profile path (not prompt-injected)."""
        return self.base_dir / "USER.md"

    @property
    def agents_dir(self) -> Path:
        """所有自定义 Agent 的根目录：`{base_dir}/agents/`。"""
        return self.base_dir / "agents"

    def agent_dir(self, name: str) -> Path:
        """指定 Agent 的目录：`{base_dir}/agents/{name}/`。"""
        return self.agents_dir / name.lower()

    def agent_memory_file(self, name: str) -> Path:
        """Return a quarantined legacy agent path (not used for injection)."""
        return self.agent_dir(name) / "memory.json"

    def thread_dir(self, thread_id: str) -> Path:
        """
        单个线程数据的宿主机路径：`{base_dir}/threads/{thread_id}/`

        该目录包含 `user-data/` 子目录，并会在沙箱内挂载为 `/mnt/user-data/`。

        异常：
            ValueError: 当 `thread_id` 包含不安全字符（路径分隔符或 `..`）时抛出，
                        以防目录遍历。
        """
        if _SAFE_THREAD_ID_RE.fullmatch(thread_id) is None:
            raise ValueError(f"Invalid thread_id {thread_id!r}: only alphanumeric characters, hyphens, and underscores are allowed.")
        return self.base_dir / "threads" / thread_id

    def sandbox_work_dir(self, thread_id: str) -> Path:
        """
        Agent 工作目录的宿主机路径。
        宿主机：`{base_dir}/threads/{thread_id}/user-data/workspace/`
        沙箱：`/mnt/user-data/workspace/`
        """
        return self.thread_dir(thread_id) / "user-data" / "workspace"

    def sandbox_uploads_dir(self, thread_id: str) -> Path:
        """
        用户上传文件的宿主机路径。
        宿主机：`{base_dir}/threads/{thread_id}/user-data/uploads/`
        沙箱：`/mnt/user-data/uploads/`
        """
        return self.thread_dir(thread_id) / "user-data" / "uploads"

    def sandbox_knowledge_dir(self, thread_id: str) -> Path:
        """Return the Backend-managed, sandbox-read-only knowledge directory."""
        return self.thread_dir(thread_id) / "user-data" / "knowledge"

    def sandbox_outputs_dir(self, thread_id: str) -> Path:
        """
        Agent 生成产物的宿主机路径。
        宿主机：`{base_dir}/threads/{thread_id}/user-data/outputs/`
        沙箱：`/mnt/user-data/outputs/`
        """
        return self.thread_dir(thread_id) / "user-data" / "outputs"

    def sandbox_user_data_dir(self, thread_id: str) -> Path:
        """
        user-data 根目录的宿主机路径。
        宿主机：`{base_dir}/threads/{thread_id}/user-data/`
        沙箱：`/mnt/user-data/`
        """
        return self.thread_dir(thread_id) / "user-data"

    def ensure_thread_dirs(self, thread_id: str) -> None:
        """为线程创建标准沙箱目录。

        目录会使用 `0o777` 权限创建，确保沙箱容器（其 UID 可能与宿主机后端进程不同）
        可写入卷挂载路径，避免出现 “Permission denied”。
        之所以显式调用 `chmod()`，是因为 `Path.mkdir(mode=...)` 受进程 umask 影响，
        可能无法得到预期权限。
        """
        for d in [
            self.sandbox_work_dir(thread_id),
            self.sandbox_uploads_dir(thread_id),
            self.sandbox_knowledge_dir(thread_id),
            self.sandbox_outputs_dir(thread_id),
        ]:
            d.mkdir(parents=True, exist_ok=True)
            d.chmod(0o777)

    def resolve_virtual_path(self, thread_id: str, virtual_path: str) -> Path:
        """将沙箱虚拟路径解析为真实宿主机文件系统路径。

        参数：
            thread_id: 线程 ID。
            virtual_path: 沙箱内看到的虚拟路径，例如
                          ``/mnt/user-data/outputs/report.pdf``。
                          匹配前会先去除前导斜杠。

        返回：
            解析后的宿主机绝对路径。

        异常：
            ValueError: 当路径不以期望虚拟前缀开头，或检测到路径遍历时抛出。
        """
        stripped = virtual_path.lstrip("/")
        prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

        # 要求精确的路径段边界匹配，避免前缀混淆
        # （例如拒绝 "mnt/user-dataX/..." 这类路径）。
        if stripped != prefix and not stripped.startswith(prefix + "/"):
            raise ValueError(f"Path must start with /{prefix}")

        relative = stripped[len(prefix) :].lstrip("/")
        base = self.sandbox_user_data_dir(thread_id).resolve()
        actual = (base / relative).resolve()

        try:
            actual.relative_to(base)
        except ValueError:
            raise ValueError("Access denied: path traversal detected")

        return actual


# ── 单例 ─────────────────────────────────────────────────────────────────

_paths: Paths | None = None


def get_paths() -> Paths:
    """返回全局 Paths 单例（延迟初始化）。"""
    global _paths
    if _paths is None:
        _paths = Paths()
    return _paths
