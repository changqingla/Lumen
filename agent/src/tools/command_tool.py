"""RunCommandTool — 双模式命令执行工具（兼容模式 + 容器沙盒模式）"""
import asyncio
import json
import shlex
from typing import Optional

from langchain.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from ..utils.logger import get_logger

logger = get_logger(__name__)

COMMAND_WHITELIST = frozenset([
    "node", "npm", "python", "python3", "soffice", "pdftoppm", "markitdown",
])
COMMAND_TIMEOUT = 120  # seconds
OUTPUT_MAX_CHARS = 10000


class RunCommandTool(BaseTool):
    """在当前请求的工作目录中执行受限 Shell 命令（双模式）。"""

    name: str = "run_command"
    description: str = (
        "在当前请求的工作目录中执行受限 Shell 命令。\n\n"
        '输入格式（JSON）:\n  {"command": "node generate.js", "args": []}\n'
        '  或直接字符串: "node generate.js"\n\n'
        "输出: 命令的 stdout + stderr（各限 10000 字符）\n"
        "注意: 仅允许白名单命令: node, python, python3, soffice, pdftoppm, markitdown"
    )

    workspace_path: str = ""
    active_skill_runtime: Optional[dict] = None

    class Config:
        arbitrary_types_allowed = True

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_command(self, cmd: str) -> tuple[bool, str]:
        """验证命令是否在白名单中。

        提取命令首词，检查是否在 COMMAND_WHITELIST 中。

        Returns:
            (is_valid, error_message) — 合法时 error_message 为空字符串。
        """
        cmd = cmd.strip()
        if not cmd:
            return False, "命令不能为空"

        try:
            first_word = shlex.split(cmd)[0]
        except ValueError:
            # shlex.split can fail on unbalanced quotes, etc.
            first_word = cmd.split()[0] if cmd.split() else ""

        if first_word in COMMAND_WHITELIST:
            return True, ""
        return False, f"命令 '{first_word}' 不在白名单中。允许的命令: {', '.join(sorted(COMMAND_WHITELIST))}"

    def _check_path_traversal(self, cmd_str: str) -> bool:
        """检查命令字符串中是否包含 ``..`` 路径遍历。

        Returns:
            True 表示检测到路径遍历（不安全）。
        """
        return ".." in cmd_str

    # ------------------------------------------------------------------
    # Execution backends
    # ------------------------------------------------------------------

    async def _run_in_subprocess(self, command: str, timeout: int) -> str:
        """兼容模式：使用 asyncio 子进程执行命令。

        cwd 设为 workspace_path，stdout/stderr 各截断至 OUTPUT_MAX_CHARS。
        """
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace_path or None,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"[ERROR] run_command: 命令执行超时 ({timeout}s)\ncommand: {command}"

            stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")[:OUTPUT_MAX_CHARS]
            stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")[:OUTPUT_MAX_CHARS]
            exit_code = proc.returncode

            if exit_code != 0:
                return (
                    f"[ERROR] run_command: 命令执行失败 (exit code {exit_code})\n"
                    f"stdout: {stdout}\n"
                    f"stderr: {stderr}"
                )

            return f"[EXIT 0]\nstdout: {stdout}\nstderr: {stderr}"

        except Exception as exc:
            logger.error("Subprocess execution error", extra={"error": str(exc), "command": command})
            return f"[ERROR] run_command: 子进程执行异常: {exc}"

    async def _run_in_container(self, command: str, runtime: dict) -> str:
        """容器沙盒模式：通过 Docker SDK 在临时容器中执行命令。"""
        try:
            import docker
            from docker.errors import ContainerError, ImageNotFound, APIError
        except ImportError:
            return "[ERROR] run_command: Docker SDK 未安装，无法使用容器沙盒模式。请安装 docker 包。"

        image = runtime.get("image", "node:20-slim")
        network_mode = runtime.get("network", "none")
        mem_limit = runtime.get("memory", "512m")
        timeout = runtime.get("timeout", COMMAND_TIMEOUT)

        # Track container for timeout cleanup
        container_ref = {"container": None, "client": None}

        try:
            def _docker_run():
                client = docker.from_env()
                container_ref["client"] = client
                # Use detach mode so we can track the container for cleanup
                container = client.containers.run(
                    image=image,
                    command=f"sh -c {shlex.quote(command)}",
                    volumes={
                        self.workspace_path: {"bind": "/workspace", "mode": "rw"},
                    },
                    working_dir="/workspace",
                    network_mode=network_mode,
                    mem_limit=mem_limit,
                    remove=False,  # We handle removal ourselves for cleanup
                    detach=True,
                )
                container_ref["container"] = container
                result = container.wait()
                exit_code = result.get("StatusCode", -1)
                stdout = container.logs(stdout=True, stderr=False)
                stderr = container.logs(stdout=False, stderr=True)
                # Clean up container
                try:
                    container.remove(force=True)
                except Exception:
                    pass
                container_ref["container"] = None
                return exit_code, stdout, stderr

            exit_code, stdout_bytes, stderr_bytes = await asyncio.wait_for(
                asyncio.to_thread(_docker_run),
                timeout=timeout,
            )

            stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")[:OUTPUT_MAX_CHARS]
            stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")[:OUTPUT_MAX_CHARS]

            if exit_code != 0:
                return (
                    f"[ERROR] run_command: 命令执行失败 (exit code {exit_code})\n"
                    f"stdout: {stdout}\n"
                    f"stderr: {stderr}"
                )

            return f"[EXIT 0]\nstdout: {stdout}\nstderr: {stderr}"

        except asyncio.TimeoutError:
            # Kill the still-running container
            try:
                c = container_ref.get("container")
                if c:
                    c.kill()
                    c.remove(force=True)
                    logger.info("Killed timed-out container")
            except Exception as kill_exc:
                logger.warning(f"Failed to kill timed-out container: {kill_exc}")
            return f"[ERROR] run_command: 容器执行超时 ({timeout}s)\ncommand: {command}"
        except ImageNotFound:
            return f"[ERROR] run_command: Docker 镜像 '{image}' 不存在，请先拉取镜像。"
        except APIError as exc:
            error_msg = str(exc)
            if "connection" in error_msg.lower() or "refused" in error_msg.lower():
                return (
                    "[ERROR] run_command: Docker 守护进程不可用。"
                    "请确保已挂载 /var/run/docker.sock 并且 Docker 服务正在运行。"
                )
            return f"[ERROR] run_command: Docker API 错误: {error_msg}"
        except Exception as exc:
            error_msg = str(exc)
            # Catch-all for connection errors (e.g. docker not installed at OS level)
            if "connectionrefusederror" in error_msg.lower() or "filenotfounderror" in error_msg.lower():
                return (
                    "[ERROR] run_command: Docker 守护进程不可用。"
                    "请确保已挂载 /var/run/docker.sock 并且 Docker 服务正在运行。"
                )
            return f"[ERROR] run_command: 容器执行失败: {error_msg}"

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def _parse_input(self, query: str) -> str:
        """Parse input: accept JSON with 'command' key or plain string."""
        query = query.strip()
        if query.startswith("{"):
            try:
                data = json.loads(query)
                return data.get("command", "").strip()
            except json.JSONDecodeError:
                pass
        return query

    def _run(self, query: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        """Sync wrapper — delegates to _arun."""
        return asyncio.get_event_loop().run_until_complete(self._arun(query, run_manager))

    async def _arun(self, query: str, run_manager=None) -> str:
        """主入口：解析输入 → 选择执行模式 → 返回结果。"""
        try:
            command = self._parse_input(query)
            if not command:
                return "[ERROR] run_command: 未提供有效命令"

            logger.info("run_command invoked", extra={
                "command": command,
                "mode": "container" if self.active_skill_runtime else "compat",
            })

            # --- 容器沙盒模式 ---
            if self.active_skill_runtime:
                return await self._run_in_container(command, self.active_skill_runtime)

            # --- 兼容模式 ---
            # 白名单检查
            is_valid, err_msg = self._validate_command(command)
            if not is_valid:
                return f"[ERROR] run_command: {err_msg}"

            # 路径遍历检查
            if self._check_path_traversal(command):
                return "[ERROR] run_command: 命令中包含 '..' 路径遍历，已拒绝执行"

            return await self._run_in_subprocess(command, COMMAND_TIMEOUT)

        except Exception as exc:
            logger.error("run_command unexpected error", extra={"error": str(exc)})
            return f"[ERROR] run_command: {exc}"
