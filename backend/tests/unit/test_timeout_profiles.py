"""Unit tests for timeout profile validation (T021, RED first).

FR-021 / SC-010 / blueprint §19: the server-side total timeout must be
STRICTLY smaller than every Host Tool Call timeout, so slow retrieval
degrades to the four-state contract instead of the Host giving up first.
A reversed configuration must be rejected with an actionable message.
"""

from __future__ import annotations

import pytest

from rag_mcp.config.timeout_profiles import TimeoutProfiles, validate_timeout_profiles


def test_defaults_valid() -> None:
    """server total 30000 < min(60000, 60000, 120000)."""
    profiles = TimeoutProfiles()
    errors = validate_timeout_profiles(profiles)
    assert errors == []


def test_default_values_match_spec() -> None:
    profiles = TimeoutProfiles()
    assert profiles.server_total_ms == 30_000
    assert profiles.deepseek_harness_ms == 60_000
    assert profiles.claude_code_ms == 60_000
    assert profiles.chatgpt_app_ms == 120_000


def test_reversed_host_timeout_rejected() -> None:
    """Any Host timeout <= server total is rejected (Edge Case)."""
    profiles = TimeoutProfiles(
        deepseek_harness_ms=25_000,  # < server total 30000
        claude_code_ms=60_000,
        chatgpt_app_ms=120_000,
        server_total_ms=30_000,
    )
    errors = validate_timeout_profiles(profiles)
    assert errors, "reversed profile must be rejected"
    assert any("deepseek_harness" in e for e in errors)


def test_equal_host_timeout_rejected() -> None:
    """Equality is also rejected: strictly smaller is required."""
    profiles = TimeoutProfiles(
        claude_code_ms=30_000,  # == server total
        server_total_ms=30_000,
    )
    errors = validate_timeout_profiles(profiles)
    assert any("claude_code" in e for e in errors)


def test_valid_custom_profile_passes() -> None:
    profiles = TimeoutProfiles(
        deepseek_harness_ms=45_000,
        claude_code_ms=45_000,
        chatgpt_app_ms=90_000,
        server_total_ms=30_000,
    )
    assert validate_timeout_profiles(profiles) == []


def test_error_messages_are_actionable() -> None:
    profiles = TimeoutProfiles(chatgpt_app_ms=10_000)
    errors = validate_timeout_profiles(profiles)
    assert errors
    joined = " ".join(errors)
    # Correctable guidance mentions the env var or the ordering rule
    assert "RETRIEVAL_TOTAL_TIMEOUT_MS" in joined or "strictly smaller" in joined


def test_min_host_timeout_helper() -> None:
    profiles = TimeoutProfiles()
    assert profiles.min_host_timeout_ms() == 60_000
