"""线程虚拟路径（如 mnt/user-data/outputs/...）的通用解析工具。"""

from pathlib import Path

from fastapi import HTTPException

from src.config.paths import get_paths


def resolve_thread_virtual_path(thread_id: str, virtual_path: str) -> Path:
    """将线程内虚拟路径解析为宿主机文件系统路径。

    参数：
        thread_id: 线程 ID。
        virtual_path: 沙箱内可见的虚拟路径
            （如 `/mnt/user-data/outputs/file.txt`）。

    返回：
        解析后的文件系统路径。

    异常：
        HTTPException: 路径非法或越界访问时抛出。
    """
    try:
        return get_paths().resolve_virtual_path(thread_id, virtual_path)
    except ValueError as e:
        status = 403 if "traversal" in str(e) else 400
        raise HTTPException(status_code=status, detail=str(e))
