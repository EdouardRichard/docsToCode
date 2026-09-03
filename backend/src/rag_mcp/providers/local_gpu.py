"""Local GPU provider (006, T044).

FR-010 / research §1.5: the same local model executed on a GPU device path.
The device is parameterized (default cuda); when the requested device is
CUDA but no GPU hardware is present, validation fails explicitly — the
provider never silently falls back to CPU (Assumptions).
"""

from __future__ import annotations

import logging
from typing import Any

from rag_mcp.providers.local_cpu import LocalCPUEmbeddingProvider

logger = logging.getLogger(__name__)


def is_gpu_available() -> bool:
    """Whether a CUDA GPU is actually present."""
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - torch missing counts as no GPU
        return False


class LocalGPUEmbeddingProvider(LocalCPUEmbeddingProvider):
    """bge-m3 (or configured model) executed on a GPU device path."""

    def __init__(self, model_name: str | None = None, device: str = "cuda") -> None:
        super().__init__(model_name)
        self._device = device

    @property
    def device(self) -> str:
        return self._device

    def validate_hardware(self) -> None:
        """FR-010: refuse CUDA when no GPU is present (no silent CPU fallback)."""
        if self._device.startswith("cuda") and not is_gpu_available():
            raise ValueError(
                f"device {self._device!r} requires CUDA but no GPU is available; "
                f"local_gpu provider refuses to silently fall back to CPU "
                f"(FR-010/Assumptions)"
            )

    def _ensure_model(self) -> Any:
        self.validate_hardware()
        if self._model is None:
            logger.info("Loading embedding model %s on %s ...", self._model_name, self._device)
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name, device=self._device)
            self._dimension = self._model.get_sentence_embedding_dimension()
            logger.info(
                "Embedding model %s loaded on %s (dim=%d)",
                self._model_name, self._device, self._dimension,
            )
        return self._model
