"""Snowflake ID generator for distributed-unique, time-ordered IDs.

Uses 64-bit integers compatible with PostgreSQL BIGINT and Qdrant u64 Point IDs.
Worker ID is fixed at 0 for single-writer mode (001).
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


def generate_id() -> int:
    """Generate a Snowflake ID using the default generator (worker_id=0)."""
    return _default_generator.generate()
