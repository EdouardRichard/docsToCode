"""Unit tests for the SourceObjectStore abstraction (T015, RED first).

Covers the 006 storage evolution interface (FR-006, blueprint §21.2):
reads/writes/existence must resolve under DATA_ROOT using the established
`{data_root}/{scope_id}/{source_id}/{filename}` convention, and directory
traversal (e.g. `../../` in a component) must be rejected.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def store(tmp_path: Path):
    from rag_mcp.runtime.source_object_store import LocalFilesystemSourceObjectStore

    return LocalFilesystemSourceObjectStore(data_root=tmp_path)


def test_import_source_object_store() -> None:
    from rag_mcp.runtime.source_object_store import (  # noqa: F401
        LocalFilesystemSourceObjectStore,
        SourceObjectStore,
    )


def test_save_and_read_roundtrip(store, tmp_path: Path) -> None:
    data = b"# hello\nsource body\n"
    path = store.save(scope_id=123, source_id=456, filename="doc.md", data=data)

    assert path.exists()
    # Path stays under DATA_ROOT with the established convention
    assert tmp_path in path.parents
    assert path.name == "doc.md"
    assert str(123) in str(path)
    assert str(456) in str(path)

    assert store.read_bytes(scope_id=123, source_id=456, filename="doc.md") == data


def test_exists(store) -> None:
    assert store.exists(scope_id=1, source_id=2, filename="a.md") is False
    store.save(scope_id=1, source_id=2, filename="a.md", data=b"x")
    assert store.exists(scope_id=1, source_id=2, filename="a.md") is True


def test_read_missing_raises_file_not_found(store) -> None:
    with pytest.raises(FileNotFoundError):
        store.read_bytes(scope_id=1, source_id=2, filename="missing.md")


def test_directory_traversal_rejected(store) -> None:
    """FR-006 safety: components with separators or '..' are rejected."""
    with pytest.raises(ValueError):
        store.save(scope_id=1, source_id=2, filename="../escape.md", data=b"x")
    with pytest.raises(ValueError):
        store.save(scope_id=1, source_id=2, filename="sub/dir/file.md", data=b"x")
    with pytest.raises(ValueError):
        store.save(scope_id="../..", source_id=2, filename="file.md", data=b"x")
    with pytest.raises(ValueError):
        store.save(scope_id=1, source_id="../../etc", filename="file.md", data=b"x")
    with pytest.raises(ValueError):
        store.read_bytes(scope_id=1, source_id=2, filename="../../etc/passwd")
    with pytest.raises(ValueError):
        store.exists(scope_id=1, source_id="..%2f..", filename="f.md")


def test_absolute_filename_rejected(store) -> None:
    with pytest.raises(ValueError):
        store.save(scope_id=1, source_id=2, filename="C:\\tmp\\evil.md", data=b"x")


def test_open_stream(store) -> None:
    store.save(scope_id=7, source_id=8, filename="s.md", data=b"stream")
    with store.open(scope_id=7, source_id=8, filename="s.md") as fh:
        assert fh.read() == b"stream"


def test_abstraction_is_abstract(tmp_path: Path) -> None:
    """SourceObjectStore stays an evolution interface (blueprint §21.2)."""
    from rag_mcp.runtime.source_object_store import SourceObjectStore

    with pytest.raises(TypeError):
        SourceObjectStore()  # type: ignore[abstract]
