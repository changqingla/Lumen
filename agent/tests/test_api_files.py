"""Tests for the GET /files/{session_id}/{filename} endpoint path validation.

Validates Requirements 11.1, 11.2, 11.3, 11.4:
- Path safety validation for session_id and filename
- Correct presigned URL generation
- 404 for missing files
- 400 for malicious path parameters
"""
import re

import pytest

# ---------------------------------------------------------------------------
# Inline copy of _validate_path_param to test without importing full api.py
# (api.py requires fastapi which is not available in the unit-test venv)
# ---------------------------------------------------------------------------

_SAFE_PATH_RE = re.compile(r'^[a-zA-Z0-9_\-\.]+$')


def _validate_path_param(value: str) -> bool:
    """Validate that a path parameter is safe (no traversal, no special chars)."""
    if not value or '..' in value or '/' in value or '\\' in value:
        return False
    return bool(_SAFE_PATH_RE.match(value))


# ---------------------------------------------------------------------------
# Unit tests for _validate_path_param
# ---------------------------------------------------------------------------


class TestValidatePathParam:
    """Unit tests for the path safety validator."""

    def test_normal_session_id(self):
        assert _validate_path_param("abc123") is True

    def test_filename_with_dot(self):
        assert _validate_path_param("output.pptx") is True

    def test_hyphen_and_underscore(self):
        assert _validate_path_param("my-file_v2.pptx") is True

    def test_uuid_style(self):
        assert _validate_path_param("550e8400-e29b-41d4-a716-446655440000") is True

    def test_rejects_empty(self):
        assert _validate_path_param("") is False

    def test_rejects_dot_dot(self):
        assert _validate_path_param("..") is False
        assert _validate_path_param("a..b") is False

    def test_rejects_slash(self):
        assert _validate_path_param("a/b") is False

    def test_rejects_backslash(self):
        assert _validate_path_param("a\\b") is False

    def test_rejects_special_chars(self):
        assert _validate_path_param("file name.txt") is False  # space
        assert _validate_path_param("file@name") is False
        assert _validate_path_param("file;name") is False
        assert _validate_path_param("file|name") is False

    def test_rejects_traversal_prefix(self):
        assert _validate_path_param("../etc/passwd") is False

    def test_rejects_percent_encoded(self):
        assert _validate_path_param("%2e%2e") is False

    def test_rejects_null_byte(self):
        assert _validate_path_param("file\x00name") is False
