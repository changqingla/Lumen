import fnmatch
from pathlib import Path

IGNORE_PATTERNS = [
    # 版本控制目录
    ".git",
    ".svn",
    ".hg",
    ".bzr",
    # 依赖目录
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    "env",
    ".tox",
    ".nox",
    ".eggs",
    "*.egg-info",
    "site-packages",
    # 构建产物
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".output",
    ".turbo",
    "target",
    "out",
    # 集成开发环境（IDE）与编辑器文件
    ".idea",
    ".vscode",
    "*.swp",
    "*.swo",
    "*~",
    ".project",
    ".classpath",
    ".settings",
    # 操作系统生成文件
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "*.lnk",
    # 日志与临时文件
    "*.log",
    "*.tmp",
    "*.temp",
    "*.bak",
    "*.cache",
    ".cache",
    "logs",
    # 覆盖率与测试产物
    ".coverage",
    "coverage",
    ".nyc_output",
    "htmlcov",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
]


def _should_ignore(name: str) -> bool:
    """检查文件/目录名是否匹配任一忽略规则。"""
    for pattern in IGNORE_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def list_dir(path: str, max_depth: int = 2) -> list[str]:
    """
    列出目录内容，最大递归深度为 max_depth。

    参数：
        path: 要列出的根目录路径。
        max_depth: 最大遍历深度（默认：2）。
                   1 = 仅直接子项，2 = 子项 + 孙项，以此类推。

    返回：
        文件与目录的绝对路径列表，
        其中会排除匹配 IGNORE_PATTERNS 的项。
    """
    result: list[str] = []
    root_path = Path(path).resolve()

    if not root_path.is_dir():
        return result

    def _traverse(current_path: Path, current_depth: int) -> None:
        """递归遍历目录，直到 max_depth。"""
        if current_depth > max_depth:
            return

        try:
            for item in current_path.iterdir():
                if _should_ignore(item.name):
                    continue

                post_fix = "/" if item.is_dir() else ""
                result.append(str(item.resolve()) + post_fix)

                # 未达到最大深度时继续递归子目录
                if item.is_dir() and current_depth < max_depth:
                    _traverse(item, current_depth + 1)
        except PermissionError:
            pass

    _traverse(root_path, 1)

    return sorted(result)
