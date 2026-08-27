"""Credential redactor module for RAG MCP parsers.

Replaces credential VALUES with typed angle-bracket placeholders while
preserving field names, assignment operators, code structure, and source
positions.  Runs *before* chunking so that neither Qdrant nor MCP
responses ever contain raw secrets.

Placeholder format (FR-006 / research §4.1):
    <api-key>, <password>, <token>, <secret>
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Compiled patterns – each captures the *prefix* (field name + operator) in
# group 1 and the *value* (including optional surrounding quotes) in group 2.
# The replacement keeps group 1 intact and substitutes only the value.
# ---------------------------------------------------------------------------

# API keys: api_key, api-key, apikey (case-insensitive)
_API_KEY_RE = re.compile(
    r"(?i)"
    r"((?:api[_\-]?key|apikey)\s*[:=]\s*)"   # group 1: field name + operator
    r"(['\"]?)"                                # group 2: opening quote (optional)
    r"([A-Za-z0-9_\-]{16,})"                  # group 3: value (≥16 chars)
    r"(['\"]?)"                                # group 4: closing quote (optional)
)

# Passwords: password, passwd, pwd
_PASSWORD_RE = re.compile(
    r"(?i)"
    r"((?:password|passwd|pwd)\s*[:=]\s*)"
    r"(['\"]?)"
    r"([^\s'\"]{4,})"                          # value: ≥4 non-whitespace/non-quote chars
    r"(['\"]?)"
)

# Tokens: bearer, authorization, token (and compounds like bearer_token)
_TOKEN_RE = re.compile(
    r"(?i)"
    r"((?:bearer|authorization|token)[_\w]*\s*[:=]\s*)"
    r"(['\"]?)"
    r"([A-Za-z0-9_\-\.]{20,})"                # value: ≥20 chars (JWT-like)
    r"(['\"]?)"
)

# Secrets: secret, client_secret
_SECRET_RE = re.compile(
    r"(?i)"
    r"((?:client_)?secret\s*[:=]\s*)"
    r"(['\"]?)"
    r"([A-Za-z0-9_\-]{8,})"                   # value: ≥8 chars
    r"(['\"]?)"
)


def _replace(match: re.Match[str], placeholder: str) -> str:
    """Return the prefix + quoted placeholder, preserving original quoting."""
    prefix = match.group(1)
    open_quote = match.group(2)
    close_quote = match.group(4)
    return f"{prefix}{open_quote}{placeholder}{close_quote}"


def redact_credentials(text: str) -> str:
    """Replace credential values in *text* with typed placeholders.

    Parameters
    ----------
    text:
        Arbitrary text (Markdown, Java source, config file, …).

    Returns
    -------
    str
        A copy of *text* with credential values replaced by typed
        angle-bracket placeholders.  Field names, operators, quotes,
        and all non-matching content are preserved verbatim.
    """
    if not text:
        return text

    # Order matters: more-specific patterns first to avoid partial matches.
    # Token pattern is longest-value (≥20), then API key (≥16), secret (≥8),
    # password (≥4).  We apply them sequentially; each pass works on the
    # result of the previous one so already-redacted placeholders won't
    # re-match (they contain `<` / `>` which are outside the value charsets).
    result = _TOKEN_RE.sub(lambda m: _replace(m, "<token>"), text)
    result = _API_KEY_RE.sub(lambda m: _replace(m, "<api-key>"), result)
    result = _SECRET_RE.sub(lambda m: _replace(m, "<secret>"), result)
    result = _PASSWORD_RE.sub(lambda m: _replace(m, "<password>"), result)
    return result
