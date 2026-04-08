import os
import shutil
import subprocess
from pathlib import Path

from src.sandbox.local.list_dir import list_dir
from src.sandbox.sandbox import Sandbox


class LocalSandbox(Sandbox):
    def __init__(self, id: str, path_mappings: dict[str, str] | None = None):
        """
        使用可选路径映射初始化本地沙箱。

        参数：
            id: 沙箱标识
            path_mappings: 容器路径到本地路径的映射字典
                          示例：{"/mnt/skills": "/absolute/path/to/skills"}
        """
        super().__init__(id)
        self.path_mappings = path_mappings or {}

    def _resolve_path(self, path: str) -> str:
        """
        通过映射将容器路径解析为本地真实路径。

        参数：
            path: 可能为容器路径的输入路径

        返回：
            解析后的本地路径
        """
        path_str = str(path)

        # 逐个尝试映射（按最长前缀优先，保证更具体匹配先命中）
        for container_path, local_path in sorted(self.path_mappings.items(), key=lambda x: len(x[0]), reverse=True):
            if path_str.startswith(container_path):
                # 用本地路径替换容器路径前缀
                relative = path_str[len(container_path) :].lstrip("/")
                resolved = str(Path(local_path) / relative) if relative else local_path
                return resolved

        # 未命中映射时返回原路径
        return path_str

    def _reverse_resolve_path(self, path: str) -> str:
        """
        通过映射将本地路径反向解析为容器路径。

        参数：
            path: 可能需要映射回容器路径的本地路径

        返回：
            命中映射则返回容器路径，否则返回原路径
        """
        path_str = str(Path(path).resolve())

        # 逐个尝试映射（按本地路径最长前缀优先，保证更具体匹配先命中）
        for container_path, local_path in sorted(self.path_mappings.items(), key=lambda x: len(x[1]), reverse=True):
            local_path_resolved = str(Path(local_path).resolve())
            if path_str.startswith(local_path_resolved):
                # 用容器路径替换本地路径前缀
                relative = path_str[len(local_path_resolved) :].lstrip("/")
                resolved = f"{container_path}/{relative}" if relative else container_path
                return resolved

        # 未命中映射时返回原路径
        return path_str

    def _reverse_resolve_paths_in_output(self, output: str) -> str:
        """
        在输出文本中将本地路径反向解析为容器路径。

        参数：
            output: 可能包含本地路径的输出文本

        返回：
            将本地路径替换为容器路径后的输出文本
        """
        import re

        # 按本地路径长度降序排序，保证前缀匹配正确
        sorted_mappings = sorted(self.path_mappings.items(), key=lambda x: len(x[1]), reverse=True)

        if not sorted_mappings:
            return output

        # 构造匹配绝对路径的模式
        # 例如匹配 /Users/... 等绝对路径
        result = output
        for container_path, local_path in sorted_mappings:
            local_path_resolved = str(Path(local_path).resolve())
            # 将本地路径转义后用于正则
            escaped_local = re.escape(local_path_resolved)
            # 匹配本地路径及其可选的后续子路径
            pattern = re.compile(escaped_local + r"(?:/[^\s\"';&|<>()]*)?")

            def replace_match(match: re.Match) -> str:
                matched_path = match.group(0)
                return self._reverse_resolve_path(matched_path)

            result = pattern.sub(replace_match, result)

        return result

    def _resolve_paths_in_command(self, command: str) -> str:
        """
        在命令字符串中将容器路径解析为本地路径。

        参数：
            command: 可能包含容器路径的命令字符串

        返回：
            已将容器路径解析为本地路径的命令
        """
        import re

        # 按路径长度降序排序，保证前缀匹配正确
        sorted_mappings = sorted(self.path_mappings.items(), key=lambda x: len(x[0]), reverse=True)

        # 构造匹配全部容器路径的正则
        # 匹配容器路径及其可选后续子路径
        if not sorted_mappings:
            return command

        # 构造可匹配任一容器路径的模式
        patterns = [re.escape(container_path) + r"(?:/[^\s\"';&|<>()]*)??" for container_path, _ in sorted_mappings]
        pattern = re.compile("|".join(f"({p})" for p in patterns))

        def replace_match(match: re.Match) -> str:
            matched_path = match.group(0)
            return self._resolve_path(matched_path)

        return pattern.sub(replace_match, command)

    @staticmethod
    def _get_shell() -> str:
        """
        按优先级返回首个可用 shell：
        /bin/zsh → /bin/bash → /bin/sh → PATH 中的首个 `sh`。
        若都不可用则抛出 RuntimeError。

        """
        for shell in ("/bin/zsh", "/bin/bash", "/bin/sh"):
            if os.path.isfile(shell) and os.access(shell, os.X_OK):
                return shell
        shell_from_path = shutil.which("sh")
        if shell_from_path is not None:
            return shell_from_path
        raise RuntimeError("No suitable shell executable found. Tried /bin/zsh, /bin/bash, /bin/sh, and `sh` on PATH.")

    def execute_command(self, command: str) -> str:
        # 执行前先将命令中的容器路径解析为本地路径
        resolved_command = self._resolve_paths_in_command(command)

        result = subprocess.run(
            resolved_command,
            executable=self._get_shell(),
            shell=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = result.stdout
        if result.stderr:
            output += f"\nStd Error:\n{result.stderr}" if output else result.stderr
        if result.returncode != 0:
            output += f"\nExit Code: {result.returncode}"

        final_output = output if output else "(no output)"
        # 输出中将本地路径反向映射回容器路径
        return self._reverse_resolve_paths_in_output(final_output)

    def list_dir(self, path: str, max_depth=2) -> list[str]:
        resolved_path = self._resolve_path(path)
        entries = list_dir(resolved_path, max_depth)
        # 输出中将本地路径反向映射回容器路径
        return [self._reverse_resolve_paths_in_output(entry) for entry in entries]

    def read_file(self, path: str) -> str:
        resolved_path = self._resolve_path(path)
        try:
            with open(resolved_path) as f:
                return f.read()
        except OSError as e:
            # 使用原始路径重抛异常，避免暴露内部解析路径并保持报错清晰
            raise type(e)(e.errno, e.strerror, path) from None

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        resolved_path = self._resolve_path(path)
        try:
            dir_path = os.path.dirname(resolved_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            mode = "a" if append else "w"
            with open(resolved_path, mode) as f:
                f.write(content)
        except OSError as e:
            # 使用原始路径重抛异常，避免暴露内部解析路径并保持报错清晰
            raise type(e)(e.errno, e.strerror, path) from None

    def update_file(self, path: str, content: bytes) -> None:
        resolved_path = self._resolve_path(path)
        try:
            dir_path = os.path.dirname(resolved_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(resolved_path, "wb") as f:
                f.write(content)
        except OSError as e:
            # 使用原始路径重抛异常，避免暴露内部解析路径并保持报错清晰
            raise type(e)(e.errno, e.strerror, path) from None

    def delete_file(self, path: str) -> None:
        resolved_path = self._resolve_path(path)
        try:
            os.remove(resolved_path)
        except FileNotFoundError:
            # 从调用方视角看，删除不存在文件应视为无操作。
            return
        except OSError as e:
            # 使用原始路径重抛异常，避免暴露内部解析路径并保持报错清晰
            raise type(e)(e.errno, e.strerror, path) from None
