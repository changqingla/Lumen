from __future__ import annotations

from pathlib import Path

from core.cache_paths import (
    atomic_save_trie_cache,
    get_project_cache_fallback,
    get_rag_cache_directory,
)


def test_rag_cache_directory_uses_explicit_writable_root(monkeypatch, tmp_path):
    cache_root = tmp_path / "cache"
    monkeypatch.setenv("RAG_CACHE_DIR", str(cache_root))

    assert get_rag_cache_directory() == cache_root
    assert cache_root.is_dir()


def test_rag_cache_fallback_does_not_write_into_source_root(monkeypatch):
    monkeypatch.delenv("RAG_CACHE_DIR", raising=False)

    cache_root = get_rag_cache_directory()

    assert cache_root == get_project_cache_fallback()
    assert cache_root.name == ".cache"
    assert cache_root.parent.name == "rag"


def test_trie_cache_is_published_atomically(tmp_path):
    destination = tmp_path / "huqie.txt.trie"

    class _Trie:
        @staticmethod
        def save(path: str) -> None:
            Path(path).write_bytes(b"complete-trie")

    atomic_save_trie_cache(_Trie(), destination)

    assert destination.read_bytes() == b"complete-trie"
    assert list(tmp_path.glob(".*.tmp")) == []
