"""Unit tests for WorkspaceManager."""
import os
import shutil
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils.workspace_manager import WorkspaceManager, get_workspace_manager


@pytest.fixture
def manager(tmp_path):
    """Create a WorkspaceManager with a temporary BASE_DIR."""
    mgr = WorkspaceManager()
    mgr.BASE_DIR = tmp_path / "agent_workspace"
    mgr.BASE_DIR.mkdir()
    return mgr


class TestCreate:
    def test_creates_directory(self, manager):
        path = manager.create("sess1", "req1")
        assert path.exists()
        assert path.is_dir()

    def test_path_format(self, manager):
        path = manager.create("sess1", "req1")
        assert path.name == "sess1_req1"
        assert path.parent == manager.BASE_DIR

    def test_unique_paths_different_request_ids(self, manager):
        p1 = manager.create("sess1", "req1")
        p2 = manager.create("sess1", "req2")
        assert p1 != p2

    def test_unique_paths_different_session_ids(self, manager):
        p1 = manager.create("sessA", "req1")
        p2 = manager.create("sessB", "req1")
        assert p1 != p2

    def test_idempotent_create(self, manager):
        p1 = manager.create("sess1", "req1")
        # Write a file inside
        (p1 / "test.txt").write_text("hello")
        p2 = manager.create("sess1", "req1")
        assert p1 == p2
        # Existing content should still be there
        assert (p2 / "test.txt").read_text() == "hello"


class TestCleanup:
    def test_removes_directory(self, manager):
        path = manager.create("sess1", "req1")
        (path / "file.txt").write_text("data")
        manager.cleanup(str(path))
        assert not path.exists()

    def test_cleanup_nonexistent_logs_warning(self, manager):
        """cleanup on a missing path should not raise."""
        manager.cleanup("/tmp/nonexistent_workspace_xyz_test")

    def test_cleanup_nested_files(self, manager):
        path = manager.create("sess1", "req1")
        nested = path / "sub" / "deep"
        nested.mkdir(parents=True)
        (nested / "file.txt").write_text("nested data")
        manager.cleanup(str(path))
        assert not path.exists()


class TestCleanupStale:
    def test_removes_old_directories(self, manager):
        old_path = manager.create("old_sess", "old_req")
        # Patch getmtime to return a time older than MAX_AGE_SECONDS
        old_time = time.time() - manager.MAX_AGE_SECONDS - 100
        with patch("os.path.getmtime", return_value=old_time):
            removed = manager.cleanup_stale()
        assert removed == 1
        assert not old_path.exists()

    def test_keeps_recent_directories(self, manager):
        recent_path = manager.create("recent", "req")
        removed = manager.cleanup_stale()
        assert removed == 0
        assert recent_path.exists()

    def test_returns_zero_when_base_dir_missing(self, manager):
        shutil.rmtree(str(manager.BASE_DIR))
        assert manager.cleanup_stale() == 0

    def test_mixed_old_and_new(self, manager):
        old_path = manager.create("old", "req1")
        new_path = manager.create("new", "req2")
        old_time = time.time() - manager.MAX_AGE_SECONDS - 100

        def mock_getmtime(p):
            if "old_req1" in p:
                return old_time
            return time.time()

        with patch("os.path.getmtime", side_effect=mock_getmtime):
            removed = manager.cleanup_stale()
        assert removed == 1
        assert not old_path.exists()
        assert new_path.exists()


class TestCheckDiskUsage:
    def test_returns_zero_when_empty(self, manager):
        usage = manager.check_disk_usage()
        assert usage == 0.0

    def test_returns_zero_when_base_dir_missing(self, manager):
        shutil.rmtree(str(manager.BASE_DIR))
        assert manager.check_disk_usage() == 0.0

    def test_calculates_size(self, manager):
        path = manager.create("sess", "req")
        # Write 1KB of data
        (path / "data.bin").write_bytes(b"x" * 1024)
        usage = manager.check_disk_usage()
        assert usage > 0.0
        assert usage < 0.001  # 1KB is way less than 1GB


class TestSingleton:
    def test_returns_same_instance(self):
        m1 = get_workspace_manager()
        m2 = get_workspace_manager()
        assert m1 is m2
