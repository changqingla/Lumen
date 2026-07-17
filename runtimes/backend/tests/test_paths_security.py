"""Identifier validation regressions for Runtime filesystem paths."""

import pytest

from src.config.paths import Paths


@pytest.mark.parametrize(
    "thread_id",
    ["thread-1\n", "thread-1\r", "thread/1", "../thread-1", ""],
)
def test_thread_dir_rejects_non_component_identifiers(tmp_path, thread_id):
    with pytest.raises(ValueError, match="Invalid thread_id"):
        Paths(tmp_path).thread_dir(thread_id)


def test_thread_dir_accepts_documented_identifier_characters(tmp_path):
    assert Paths(tmp_path).thread_dir("Thread_1-test") == (
        tmp_path / "threads" / "Thread_1-test"
    )
