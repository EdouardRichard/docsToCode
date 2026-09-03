"""Per-Host timeout profiles (006, T002/T021/T022).

Blueprint §19 / FR-021: every target MCP Host gets its own Tool Call timeout
profile, and the server-side total timeout must be STRICTLY smaller than every
Host timeout so a slow retrieval degrades to `partial`/`no_evidence`/`failed`
(006 four-state contract) instead of the Host giving up first.

Defaults (research §1.9):
- HOST_TIMEOUT_MS_DEEPSEEK_HARNESS = 60000
- HOST_TIMEOUT_MS_CLAUDE_CODE      = 60000
- HOST_TIMEOUT_MS_CHATGPT_APP      = 120000
- server total (RETRIEVAL_TOTAL_TIMEOUT_MS) = 30000

The validation function is invoked at instance startup (T022): a reversed
configuration (any Host timeout <= server total) is rejected with an
actionable message. Timeout values are runtime configuration only — they are
never written into the MCP contract (FR-022).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

HOST_TARGETS = ("deepseek_harness", "claude_code", "chatgpt_app")

_ENV_BY_HOST = {
    "deepseek_harness": "HOST_TIMEOUT_MS_DEEPSEEK_HARNESS",
    "claude_code": "HOST_TIMEOUT_MS_CHATGPT_APP",
    "chatgpt_app": "HOST_TIMEOUT_MS_CHATGPT_APP",
}

_DEFAULTS_BY_HOST = {
    "deepseek_harness": 60_000,
    "claude_code": 60_000,
    "chatgpt_app": 120_000,
}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class TimeoutProfiles:
    """Per-Host Tool Call timeout profiles plus the server total timeout."""

    deepseek_harness_ms: int = 60_000
    claude_code_ms: int = 60_000
    chatgpt_app_ms: int = 120_000
    server_total_ms: int = 30_000

    @classmethod
    def from_env(cls) -> "TimeoutProfiles":
        return cls(
            deepseek_harness_ms=_env_int(
                "HOST_TIMEOUT_MS_DEEPSEEK_HARNESS", cls.deepseek_harness_ms
            ),
            claude_code_ms=_env_int("HOST_TIMEOUT_MS_CLAUDE_CODE", cls.claude_code_ms),
            chatgpt_app_ms=_env_int("HOST_TIMEOUT_MS_CHATGPT_APP", cls.chatgpt_app_ms),
            server_total_ms=_env_int("RETRIEVAL_TOTAL_TIMEOUT_MS", cls.server_total_ms),
        )

    def host_timeouts_ms(self) -> dict[str, int]:
        """Host target -> configured Tool Call timeout in ms."""
        return {
            "deepseek_harness": self.deepseek_harness_ms,
            "claude_code": self.claude_code_ms,
            "chatgpt_app": self.chatgpt_app_ms,
        }

    def min_host_timeout_ms(self) -> int:
        return min(self.host_timeouts_ms().values())


def validate_timeout_profiles(profiles: TimeoutProfiles) -> list[str]:
    """Validate that the server total timeout is strictly below every Host.

    Returns a list of correctable error messages; an empty list means the
    configuration is valid. Startup enforcement (T022) fails the instance
    loudly when any error is present — no silent fallback (Edge Case, SC-010).
    """
    errors: list[str] = []
    server_total = profiles.server_total_ms
    if server_total <= 0:
        errors.append(
            f"RETRIEVAL_TOTAL_TIMEOUT_MS must be positive, got {server_total}"
        )
        return errors
    for host, timeout in profiles.host_timeouts_ms().items():
        if timeout <= 0:
            errors.append(f"Host timeout for {host} must be positive, got {timeout}")
        elif timeout <= server_total:
            errors.append(
                f"server total timeout ({server_total}ms) must be strictly smaller "
                f"than the {host} Host Tool Call timeout ({timeout}ms); raise the "
                f"Host timeout or lower RETRIEVAL_TOTAL_TIMEOUT_MS (blueprint §19)"
            )
    return errors
