"""Unit tests for SnowflakeGenerator worker_id parameterization (T011, RED first).

006 cross-instance ID uniqueness (FR-030/SC-013): every instance process runs
its own generator with a distinct worker_id allocated via instance_registry.
The module API must expose per-worker-id generation (factory + parameterized
generate_id) while keeping the legacy default generate_id() (worker_id=0)
compatible with 001.
"""

from __future__ import annotations

import pytest

from rag_mcp.utils.snowflake import SnowflakeGenerator, generate_id


def _fixed_millis(value: int):
    """Return a millis() replacement pinned to one millisecond."""
    return lambda: value


FIXED_MILLIS = 1_700_000_000_123


def test_distinct_worker_ids_same_millisecond_differ() -> None:
    """互异 worker_id 同毫秒生成互异 ID (T011 AC)."""
    gen0 = SnowflakeGenerator(worker_id=0)
    gen1 = SnowflakeGenerator(worker_id=1)
    gen0._current_millis = _fixed_millis(FIXED_MILLIS)
    gen1._current_millis = _fixed_millis(FIXED_MILLIS)

    id0 = gen0.generate()
    id1 = gen1.generate()
    assert id0 != id1
    # worker bits (bits 12..21) must carry the worker_id
    assert (id0 >> 12) & 0x3FF == 0
    assert (id1 >> 12) & 0x3FF == 1


def test_same_worker_same_millisecond_sequence_advances() -> None:
    """同 worker 同毫秒走 sequence (T011 AC)."""
    gen = SnowflakeGenerator(worker_id=0)
    gen._current_millis = _fixed_millis(FIXED_MILLIS)
    first = gen.generate()
    second = gen.generate()
    assert first != second
    assert (second - first) == 1  # adjacent sequence in the same ms
    assert (first & 0xFFF) == 0
    assert (second & 0xFFF) == 1


def test_worker_id_out_of_range_rejected() -> None:
    """worker_id=1024 raises ValueError (T011 AC)."""
    with pytest.raises(ValueError):
        SnowflakeGenerator(worker_id=1024)
    with pytest.raises(ValueError):
        SnowflakeGenerator(worker_id=-1)


def test_default_generate_id_worker_zero() -> None:
    """单实例默认 worker_id=0 兼容既有 generate_id() (T011 AC)."""
    value = generate_id()
    assert (value >> 22) > 0  # timestamp part present
    assert (value >> 12) & 0x3FF == 0  # default worker_id = 0


def test_generate_id_accepts_worker_id() -> None:
    """generate_id(worker_id) parameterized form (T012 API)."""
    a = generate_id()
    b = generate_id(worker_id=1)
    assert a != b
    assert (b >> 12) & 0x3FF == 1
    assert (a >> 12) & 0x3FF == 0


def test_get_generator_factory() -> None:
    """get_generator(worker_id) returns a cached per-worker generator (T012 API)."""
    from rag_mcp.utils.snowflake import get_generator

    g0 = get_generator(0)
    g1 = get_generator(1)
    assert g0 is not g1
    assert g0._worker_id == 0
    assert g1._worker_id == 1
    # cached per worker_id
    assert get_generator(0) is g0
    assert get_generator(1) is g1


def test_get_generator_validates_worker_id() -> None:
    from rag_mcp.utils.snowflake import get_generator

    with pytest.raises(ValueError):
        get_generator(1024)
