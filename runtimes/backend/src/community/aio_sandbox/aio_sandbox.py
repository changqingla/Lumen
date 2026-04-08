import base64
import logging
import shlex

from agent_sandbox import Sandbox as AioSandboxClient

from src.sandbox.sandbox import Sandbox

logger = logging.getLogger(__name__)


class AioSandbox(Sandbox):
    """
    该沙箱通过 HTTP API 连接到已运行的 AIO 沙箱容器。
    """

    def __init__(self, id: str, base_url: str, home_dir: str | None = None):
        """
        参数：
            id: 当前沙箱实例唯一标识。
            base_url: 沙箱 API 地址（例如 http://localhost:8080）。
            home_dir: 沙箱内 HOME 目录。若为 None，则运行时从沙箱动态获取。

        """
        super().__init__(id)
        self._base_url = base_url
        self._client = AioSandboxClient(base_url=base_url, timeout=600)
        self._home_dir = home_dir

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def home_dir(self) -> str:
        """获取沙箱内部 HOME 目录。"""
        if self._home_dir is None:
            context = self._client.sandbox.get_context()
            self._home_dir = context.home_dir
        return self._home_dir

    def execute_command(self, command: str) -> str:
        """
        参数：
            command: 要执行的命令。

        返回：
            命令输出内容。
        """
        try:
            result = self._client.shell.exec_command(command=command)
            output = result.data.output if result.data else ""
            return output if output else "(no output)"
        except Exception as e:
            logger.error(f"Failed to execute command in sandbox: {e}")
            return f"Error: {e}"

    def read_file(self, path: str) -> str:
        """
        参数：
            path: 要读取文件的绝对路径。

        返回：
            文件内容。
        """
        try:
            result = self._client.file.read_file(file=path)
            return result.data.content if result.data else ""
        except Exception as e:
            logger.error(f"Failed to read file in sandbox: {e}")
            return f"Error: {e}"

    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        """
        参数：
            path: 要列出的目录绝对路径。
            max_depth: 最大遍历深度，默认 2。

        返回：
            目录内容列表。
        """
        try:
            # 通过 shell 命令按深度限制列目录
            # `find` 的 `-maxdepth` 参数用于限制遍历层级
            result = self._client.shell.exec_command(command=f"find {path} -maxdepth {max_depth} -type f -o -type d 2>/dev/null | head -500")
            output = result.data.output if result.data else ""
            if output:
                return [line.strip() for line in output.strip().split("\n") if line.strip()]
            return []
        except Exception as e:
            logger.error(f"Failed to list directory in sandbox: {e}")
            return []

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        """
        参数：
            path: 要写入文件的绝对路径。
            content: 要写入的文本内容。
            append: 是否追加写入。

        """
        try:
            if append:
                # 先读取现有内容再追加
                existing = self.read_file(path)
                if not existing.startswith("Error:"):
                    content = existing + content
            self._client.file.write_file(file=path, content=content)
        except Exception as e:
            logger.error(f"Failed to write file in sandbox: {e}")
            raise

    def update_file(self, path: str, content: bytes) -> None:
        """
        参数：
            path: 要更新文件的绝对路径。
            content: 要写入文件的二进制内容。

        """
        try:
            base64_content = base64.b64encode(content).decode("utf-8")
            self._client.file.write_file(file=path, content=base64_content, encoding="base64")
        except Exception as e:
            logger.error(f"Failed to update file in sandbox: {e}")
            raise

    def delete_file(self, path: str) -> None:
        """
        参数：
            path: 要删除文件的绝对路径。

        """
        try:
            # 使用 rm -f 语义：删除不存在文件也视为成功（无操作）
            self._client.shell.exec_command(command=f"rm -f -- {shlex.quote(path)}")
        except Exception as e:
            logger.error(f"Failed to delete file in sandbox: {e}")
            raise
