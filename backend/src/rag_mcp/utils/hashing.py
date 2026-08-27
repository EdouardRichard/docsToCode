"""Content hashing utilities for knowledge source deduplication."""

import hashlib
from pathlib import Path


def hash_file(file_path: str | Path) -> str:
    """Compute SHA-256 hash of a file's contents.

    Args:
        file_path: Path to the file to hash.

    Returns:
        Hex-encoded SHA-256 digest string.
    """
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def hash_bytes(data: bytes) -> str:
    """Compute SHA-256 hash of raw bytes.

    Args:
        data: Raw bytes to hash.

    Returns:
        Hex-encoded SHA-256 digest string.
    """
    return hashlib.sha256(data).hexdigest()
