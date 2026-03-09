"""Workspace manager for per-request directory isolation."""
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from .logger import get_logger

logger = get_logger(__name__)


class WorkspaceManager:
    """Manages isolated workspace directories for each agent request."""

    BASE_DIR = Path("/tmp/agent_workspace")
    MAX_AGE_SECONDS = 7200  # 2 hours
    MAX_TOTAL_SIZE_GB = 5.0

    def create(self, session_id: str, request_id: str) -> Path:
        """Create and return a workspace directory path.

        Args:
            session_id: The session identifier.
            request_id: The unique request identifier (UUID4).

        Returns:
            Path to the created workspace directory.

        Raises:
            OSError: If directory creation fails.
        """
        workspace = self.BASE_DIR / f"{session_id}_{request_id}"
        workspace.mkdir(parents=True, exist_ok=True)
        logger.info("Workspace created", extra={"workspace": str(workspace)})
        return workspace

    def cleanup(self, workspace_path: str) -> None:
        """Remove a workspace directory, logging warnings on failure.

        Args:
            workspace_path: Absolute path to the workspace directory.
        """
        try:
            shutil.rmtree(workspace_path)
            logger.info("Workspace cleaned up", extra={"workspace": workspace_path})
        except Exception as exc:
            logger.warning(
                "Failed to cleanup workspace",
                extra={"workspace": workspace_path, "error": str(exc)},
            )

    def cleanup_stale(self) -> int:
        """Remove workspace directories older than MAX_AGE_SECONDS.

        Uses directory modification time (mtime) as the age indicator.
        On Linux, ctime reflects inode changes (not creation), so mtime
        is more reliable for detecting truly idle workspaces.

        Returns:
            Number of stale directories removed.
        """
        if not self.BASE_DIR.exists():
            return 0

        now = time.time()
        removed = 0
        for entry in self.BASE_DIR.iterdir():
            if not entry.is_dir():
                continue
            try:
                mtime = os.path.getmtime(str(entry))
                if now - mtime > self.MAX_AGE_SECONDS:
                    shutil.rmtree(str(entry))
                    removed += 1
                    logger.info("Stale workspace removed", extra={"workspace": str(entry)})
            except Exception as exc:
                logger.warning(
                    "Failed to remove stale workspace",
                    extra={"workspace": str(entry), "error": str(exc)},
                )
        return removed

    def check_disk_usage(self) -> float:
        """Return total size of BASE_DIR in GB, logging a warning if over threshold.

        Returns:
            Total size in GB.
        """
        if not self.BASE_DIR.exists():
            return 0.0

        total_bytes = 0
        for dirpath, _dirnames, filenames in os.walk(str(self.BASE_DIR)):
            for fname in filenames:
                try:
                    total_bytes += os.path.getsize(os.path.join(dirpath, fname))
                except OSError:
                    pass

        total_gb = total_bytes / (1024 ** 3)
        if total_gb > self.MAX_TOTAL_SIZE_GB:
            logger.warning(
                "Workspace disk usage exceeds threshold",
                extra={"total_gb": round(total_gb, 2), "threshold_gb": self.MAX_TOTAL_SIZE_GB},
            )
        return total_gb


_workspace_manager: Optional[WorkspaceManager] = None


def get_workspace_manager() -> WorkspaceManager:
    """Return the global WorkspaceManager singleton."""
    global _workspace_manager
    if _workspace_manager is None:
        _workspace_manager = WorkspaceManager()
    return _workspace_manager
