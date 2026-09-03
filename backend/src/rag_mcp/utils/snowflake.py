"""Snowflake ID generator for distributed-unique, time-ordered IDs.

Uses 64-bit integers compatible with PostgreSQL BIGINT and Qdrant u64 Point IDs.

001: single-writer mode with worker_id fixed at 0.
006 (FR-030/SC-013): every instance process runs its own generator with a
distinct worker_id allocated via instance_registry (explicit WORKER_ID or
auto-assigned lowest free). The module-level API exposes per-worker-id
generation while the legacy default generate_id() stays worker_id=0
compatible with 001.
"""

import threading
import time


class SnowflakeGenerator:
    """Thread-safe Snowflake ID generator.

    Bit layout (64 bits):
        - 41 bits: milliseconds since epoch (custom epoch)
        - 10 bits: worker ID (0-1023)
        - 12 bits: sequence number (0-4095)
    """

    # Custom epoch: 2024-01-01 00:00:00 UTC in milliseconds
    EPOCH = 1704067200000

    WORKER_BITS = 10
    SEQUENCE_BITS = 12
    MAX_SEQUENCE = (1 << SEQUENCE_BITS) - 1  # 4095
    MAX_WORKER = (1 << WORKER_BITS) - 1  # 1023

    def __init__(self, worker_id: int = 0) -> None:
        if not 0 <= worker_id <= self.MAX_WORKER:
            raise ValueError(f"worker_id must be 0-{self.MAX_WORKER}, got {worker_id}")
        self._worker_id = worker_id
        self._sequence = 0
        self._last_timestamp = 0
        self._lock = threading.Lock()

    def _current_millis(self) -> int:
        return int(time.time() * 1000)

    def generate(self) -> int:
        """Generate a unique 64-bit Snowflake ID."""
        with self._lock:
            timestamp = self._current_millis()

            if timestamp < self._last_timestamp:
                raise RuntimeError(
                    f"Clock moved backwards. Refusing to generate ID for "
                    f"{self._last_timestamp - timestamp}ms"
                )

            if timestamp == self._last_timestamp:
                self._sequence = (self._sequence + 1) & self.MAX_SEQUENCE
                if self._sequence == 0:
                    # Sequence exhausted, wait for next millisecond
                    while timestamp <= self._last_timestamp:
                        timestamp = self._current_millis()
            else:
                self._sequence = 0

            self._last_timestamp = timestamp

            return (
                ((timestamp - self.EPOCH) << (self.WORKER_BITS + self.SEQUENCE_BITS))
                | (self._worker_id << self.SEQUENCE_BITS)
                | self._sequence
            )


# Module-level singleton for convenience
_default_generator = SnowflakeGenerator(worker_id=0)

# Per-worker-id generator cache (006): each instance process generates IDs
# with its own worker_id so concurrent instances never collide.
_generator_lock = threading.Lock()
_generators: dict[int, SnowflakeGenerator] = {}


def get_generator(worker_id: int = 0) -> SnowflakeGenerator:
    """Return the cached generator for a worker_id (006, T012).

    Raises ValueError when worker_id is outside 0-1023 (same contract as
    SnowflakeGenerator itself).
    """
    if not 0 <= worker_id <= SnowflakeGenerator.MAX_WORKER:
        raise ValueError(
            f"worker_id must be 0-{SnowflakeGenerator.MAX_WORKER}, got {worker_id}"
        )
    with _generator_lock:
        generator = _generators.get(worker_id)
        if generator is None:
            generator = SnowflakeGenerator(worker_id=worker_id)
            _generators[worker_id] = generator
        return generator


def generate_id(worker_id: int = 0) -> int:
    """Generate a Snowflake ID (default worker_id=0 keeps 001 behavior)."""
    if worker_id == 0:
        return _default_generator.generate()
    return get_generator(worker_id).generate()
