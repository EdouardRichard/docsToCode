"""Source object storage abstraction (006, T016).

`SourceObjectStore` is the storage evolution interface from blueprint §21.2:
the first deployment keeps source objects on the local filesystem under
DATA_ROOT using the 001 path convention
`{data_root}/{scope_id}/{source_id}/{filename}`; object stores (S3 etc.) can
replace it later without touching callers. FR-006: reader instances resolve
evidence through the shared database, never through writer-local file paths
— this class merely wraps the existing data_root access (no behavior
change for 001–005 ingestion).

Every path component is validated: directory traversal, separators
(including percent-encoded ones) and absolute forms are rejected loudly.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Union

logger = logging.getLogger(__name__)

StrPath = Union[str, int, Path]

# Substrings that never appear in a safe single path component (covers
# literal and percent-encoded separators, Windows drives and UNC forms).
_UNSAFE_SUBSTRINGS = ("/", "\\", "%2f", "%5c", "://")


def _validate_component(value: StrPath, name: str) -> str:
    text = str(value)
    if text == "" or text in (".", ".."):
        raise ValueError(f"unsafe {name} component: {value!r}")
    lowered = text.lower()
    if any(bad in lowered for bad in _UNSAFE_SUBSTRINGS):
        raise ValueError(
            f"unsafe {name} component {value!r}: separators and traversal "
            f"sequences are not allowed"
        )
    if ":" in text:
        raise ValueError(f"unsafe {name} component {value!r}: ':' not allowed")
    if text.startswith("~"):
        raise ValueError(f"unsafe {name} component {value!r}: '~' not allowed")
    if text.startswith("\\"):
        raise ValueError(f"unsafe {name} component {value!r}: UNC path not allowed")
    return text


class SourceObjectStore(ABC):
    """Storage evolution interface for raw source objects (blueprint §21.2)."""

    def resolve_path(self, scope_id: StrPath, source_id: StrPath, filename: StrPath) -> Path:
        """Validated local path for one object (template method)."""
        raise NotImplementedError

    @abstractmethod
    def save(self, scope_id: StrPath, source_id: StrPath, filename: StrPath, data: bytes) -> Path:
        """Persist one object; returns the resolved local path."""

    @abstractmethod
    def read_bytes(self, scope_id: StrPath, source_id: StrPath, filename: StrPath) -> bytes:
        """Read one object's bytes."""

    @abstractmethod
    def exists(self, scope_id: StrPath, source_id: StrPath, filename: StrPath) -> bool:
        """Whether the object exists."""

    @abstractmethod
    def open(self, scope_id: StrPath, source_id: StrPath, filename: StrPath) -> BinaryIO:
        """Open the object for streamed reading."""


class LocalFilesystemSourceObjectStore(SourceObjectStore):
    """Local filesystem implementation over the 001 data_root convention."""

    def __init__(self, data_root: StrPath) -> None:
        self._root = Path(data_root)

    def resolve_path(self, scope_id: StrPath, source_id: StrPath, filename: StrPath) -> Path:
        scope = _validate_component(scope_id, "scope_id")
        source = _validate_component(source_id, "source_id")
        name = _validate_component(filename, "filename")
        path = self._root / scope / source / name
        # Belt-and-braces: the resolved absolute path must stay under root.
        root_abs = self._root.resolve()
        resolved = path.resolve()
        if root_abs != resolved and root_abs not in resolved.parents:
            raise ValueError(
                f"resolved path {resolved} escapes data root {root_abs}"
            )
        return path

    def save(
        self, scope_id: StrPath, source_id: StrPath, filename: StrPath, data: bytes
    ) -> Path:
        path = self.resolve_path(scope_id, source_id, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        logger.debug("saved source object %s (%d bytes)", path, len(data))
        return path

    def read_bytes(self, scope_id: StrPath, source_id: StrPath, filename: StrPath) -> bytes:
        path = self.resolve_path(scope_id, source_id, filename)
        if not path.exists():
            raise FileNotFoundError(f"source object not found: {path}")
        return path.read_bytes()

    def exists(self, scope_id: StrPath, source_id: StrPath, filename: StrPath) -> bool:
        path = self.resolve_path(scope_id, source_id, filename)
        return path.exists()

    def open(self, scope_id: StrPath, source_id: StrPath, filename: StrPath) -> BinaryIO:
        path = self.resolve_path(scope_id, source_id, filename)
        if not path.exists():
            raise FileNotFoundError(f"source object not found: {path}")
        return path.open("rb")
