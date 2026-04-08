from src.sandbox.local.local_sandbox import LocalSandbox
from src.sandbox.sandbox import Sandbox
from src.sandbox.sandbox_provider import SandboxProvider

_singleton: LocalSandbox | None = None


class LocalSandboxProvider(SandboxProvider):
    def __init__(self):
        """初始化本地沙箱提供者，并设置路径映射。"""
        self._path_mappings = self._setup_path_mappings()

    def _setup_path_mappings(self) -> dict[str, str]:
        """
        为本地沙箱设置路径映射。

        将容器路径映射到本地实际路径（包含技能目录）。

        返回：
            路径映射字典
        """
        mappings = {}

        # 将技能容器路径映射到本地技能目录
        try:
            from src.config import get_app_config

            config = get_app_config()
            skills_path = config.skills.get_skills_path()
            container_path = config.skills.container_path

            # 仅当技能目录存在时才添加映射
            if skills_path.exists():
                mappings[container_path] = str(skills_path)
        except Exception as e:
            # 配置加载失败时仅记录告警，不阻塞启动
            print(f"Warning: Could not setup skills path mapping: {e}")

        return mappings

    def acquire(self, thread_id: str | None = None) -> str:
        global _singleton
        if _singleton is None:
            _singleton = LocalSandbox("local", path_mappings=self._path_mappings)
        return _singleton.id

    def get(self, sandbox_id: str) -> Sandbox | None:
        if sandbox_id == "local":
            if _singleton is None:
                self.acquire()
            return _singleton
        return None

    def release(self, sandbox_id: str) -> None:
        # 本地沙箱（LocalSandbox）使用单例模式，无需清理。
        # 注意：该方法有意不由 SandboxMiddleware 调用，
        # 以支持同一线程多轮对话复用沙箱。
        # 对 Docker 类提供者（如 AioSandboxProvider），
        # 清理通过 shutdown() 在应用关闭时执行。
        pass
