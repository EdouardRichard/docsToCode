"""Prompt-injection detection and marking (T064, FR-019/FR-020).

Heuristic detector for suspicious injected content in retrieved evidence:
  - suspicious fragments are detected and MARKED (auditable event)
  - high-risk fragments are isolated from internal control prompts but the
    source attribution survives (FR-020)
  - detection failure NEVER blocks retrieval — the detector never raises
    (detection is assistive; the structural Schema/controller boundary is
    the actual defense, Constitution V)

Retrieved text can never change state jumps / tool selection / model
permissions: the deterministic controller only consumes schema-validated
structured judgments (FR-019).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class InjectionReport:
    """Result of scanning one text fragment."""

    suspicious: bool
    risk_level: str  # "none" | "low" | "high"
    matched_patterns: list[str] = field(default_factory=list)


# High-risk: direct attempts to hijack control flow / disclosure / tooling
_HIGH_RISK_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "chat_delimiter_escape",
        re.compile(r"<\|im_(?:start|end)\|>|<\|(?:system|user|assistant)\|>", re.I),
    ),
    (
        "delimiter_escape",
        re.compile(r"\]\]>|</prompt>|</system>|###\s*(?:system|instruction)", re.I),
    ),
    (
        "role_hijack",
        re.compile(
            r"ignore (?:all |any )?(?:previous|prior|above|earlier) "
            r"(?:instructions?|prompts?|rules?)",
            re.I,
        ),
    ),
    (
        "role_hijack_zh",
        re.compile(r"忽略(?:之前|以上|先前|所有)的?(?:指令|提示|规则|要求)"),
    ),
    (
        "identity_override",
        re.compile(
            r"you are now\b|act as (?:a |an )?(?:shell|admin|root)\b"
            r"|你现在是|扮演(?:管理员|root)",
            re.I,
        ),
    ),
    (
        "prompt_disclosure",
        re.compile(
            r"(?:reveal|show|print|output).{0,40}(?:system|internal) prompt"
            r"|泄露|输出.{0,12}(?:系统|内部)(?:提示|指令)",
            re.I,
        ),
    ),
    (
        "tool_call_manipulation",
        re.compile(
            r"(?:call|invoke|run) the (?:tool|function) [a-z_]+"
            r"|调用工具\s*[a-z_]+",
            re.I,
        ),
    ),
    (
        "user_concealment",
        re.compile(r"do not (?:tell|inform|show) the user|不要告诉用户|瞒着用户", re.I),
    ),
]

# Low-risk: suspicious but not a direct control-flow attempt
_LOW_RISK_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("suspicious_base64", re.compile(r"\bbase64[:\s]", re.I)),
    ("jailbreak_marker", re.compile(r"\bDAN mode\b|\bdeveloper mode\b|越狱模式", re.I)),
]


class InjectionDetector:
    """Heuristic prompt-injection detector (never raises, FR-020)."""

    def detect(self, text: Any) -> InjectionReport:
        """Scan a text fragment; detection failure returns a safe report."""
        try:
            if not isinstance(text, str) or not text:
                return InjectionReport(suspicious=False, risk_level="none")
            matched_high = [
                name for name, rx in _HIGH_RISK_PATTERNS if rx.search(text)
            ]
            if matched_high:
                return InjectionReport(
                    suspicious=True, risk_level="high", matched_patterns=matched_high,
                )
            matched_low = [
                name for name, rx in _LOW_RISK_PATTERNS if rx.search(text)
            ]
            if matched_low:
                return InjectionReport(
                    suspicious=True, risk_level="low", matched_patterns=matched_low,
                )
            return InjectionReport(suspicious=False, risk_level="none")
        except Exception as exc:  # noqa: BLE001 - detection never blocks
            logger.warning("Injection detection failed: %s", exc)
            return InjectionReport(suspicious=False, risk_level="none")

    def sanitize_for_prompt(self, text: str, source_ref: str = "") -> str:
        """Isolate high-risk content from internal prompts (FR-020).

        The raw fragment is withheld; the source attribution survives so the
        evidence stays locatable and auditable.
        """
        report = self.detect(text)
        if report.risk_level == "high":
            return (
                "[quarantined untrusted content withheld from analysis; "
                f"source: {source_ref or 'unknown'}]"
            )
        return text
