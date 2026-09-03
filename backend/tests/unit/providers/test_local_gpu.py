"""Unit tests for the local GPU provider (T043, RED first).

FR-010/Assumptions: the same local model executes on a GPU device path;
when device=cuda but no CUDA hardware is present, startup validation must
fail explicitly — never silently falling back to CPU.
"""

from __future__ import annotations

import pytest

from rag_mcp.providers.local_gpu import LocalGPUEmbeddingProvider, is_gpu_available


def test_import_local_gpu() -> None:
    assert LocalGPUEmbeddingProvider is not None


def test_device_parameterized_cuda() -> None:
    provider = LocalGPUEmbeddingProvider(model_name="BAAI/bge-m3", device="cuda")
    assert provider.device == "cuda"


def test_device_parameterized_cpu() -> None:
    provider = LocalGPUEmbeddingProvider(model_name="BAAI/bge-m3", device="cpu")
    assert provider.device == "cpu"


def test_is_gpu_available_returns_bool() -> None:
    assert isinstance(is_gpu_available(), bool)


def test_no_gpu_cuda_validation_fails_explicitly() -> None:
    """device=cuda with no GPU -> explicit failure (no silent CPU fallback)."""
    if is_gpu_available():
        pytest.skip("CUDA present; the no-GPU branch is not exercisable here")
    provider = LocalGPUEmbeddingProvider(model_name="BAAI/bge-m3", device="cuda")
    with pytest.raises(ValueError) as excinfo:
        provider.validate_hardware()
    assert "cuda" in str(excinfo.value).lower() or "gpu" in str(excinfo.value).lower()


def test_cpu_device_validation_passes() -> None:
    provider = LocalGPUEmbeddingProvider(model_name="BAAI/bge-m3", device="cpu")
    provider.validate_hardware()  # must not raise
