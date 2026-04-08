from abc import ABC, abstractmethod


class Sandbox(ABC):
    """沙箱环境抽象基类。"""

    _id: str

    def __init__(self, id: str):
        self._id = id

    @property
    def id(self) -> str:
        return self._id

    @abstractmethod
    def execute_command(self, command: str) -> str:
        """
        参数：
            command: 要执行的命令。

        返回：
            命令执行的标准输出或错误输出。
        """
        pass

    @abstractmethod
    def read_file(self, path: str) -> str:
        """
        参数：
            path: 要读取文件的绝对路径。

        返回：
            文件内容。
        """
        pass

    @abstractmethod
    def list_dir(self, path: str, max_depth=2) -> list[str]:
        """
        参数：
            path: 要列出的目录绝对路径。
            max_depth: 最大遍历深度，默认 2。

        返回：
            目录内容。
        """
        pass

    @abstractmethod
    def write_file(self, path: str, content: str, append: bool = False) -> None:
        """
        参数：
            path: 要写入文件的绝对路径。
            content: 要写入的文本内容。
            append: 是否追加写入。False 时会创建或覆盖文件。

        """
        pass

    @abstractmethod
    def update_file(self, path: str, content: bytes) -> None:
        """
        参数：
            path: 要更新文件的绝对路径。
            content: 要写入文件的二进制内容。

        """
        pass

    @abstractmethod
    def delete_file(self, path: str) -> None:
        """
        参数：
            path: 要删除文件的绝对路径。

        """
        pass
