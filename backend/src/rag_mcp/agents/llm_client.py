"""OpenAI-compatible LLM client (Model Gateway, blueprint sec 18).

Real HTTP calls via httpx — vendor-agnostic: works with any
OpenAI-compatible endpoint (DeepSeek, OpenAI, Anthropic-compatible
proxies, local models). No vendor is hardcoded (Constitution
architecture constraint); base_url / api_key / model all come from
run-config / environment.

Failure contract (SC-011): every error — connection, timeout, HTTP
status, malformed JSON — returns None so the calling agent falls back
to its deterministic equivalent. An LLM failure NEVER blocks or raises
into the state machine.

Response cache (T070, SC-008): an opt-in disk cache keyed by
(model, system prompt, user payload) makes the Agent orchestration
path byte-reproducible across same-session evaluation passes. The
first pass records real responses (successes AND failures — the eval
session replays both so pass 2 equals pass 1 exactly); later passes
replay them without any HTTP call. Cache hits do NOT count toward the
usage chars so recorded cost always reflects real LLM usage (SC-007).
The cache is enabled only when AGENTIC_LLM_CACHE_PATH (or an explicit
cache_dir) is configured — production callers never set it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Markdown code-fence marker (three backticks), built without a literal
# backtick character in this source line.
_FENCE = chr(96) * 3
_FENCE_RE = re.compile(_FENCE + r"(?:json)?s*(.*?)s*" + _FENCE, re.DOTALL)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from an LLM response body.

    Handles responses wrapped in markdown code fences or surrounded by
    prose. Returns None when no JSON object can be recovered.
    """
    if not text:
        return None
    stripped = text.strip()
    # Strip markdown code fences
    fence = _FENCE_RE.search(stripped)
    if fence:
        stripped = fence.group(1).strip()
    # Direct parse first
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    # Fall back: locate the first { ... } block with brace balancing
    start = stripped.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(stripped)):
            if stripped[i] == "{":
                depth += 1
            elif stripped[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = stripped[start:i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            return obj
                    except (json.JSONDecodeError, ValueError):
                        pass
                    break
        start = stripped.find("{", start + 1)
    return None


class LLMClient:
    """OpenAI-compatible chat client with JSON structured-output support.

    Makes REAL HTTP POST calls to {base_url}/chat/completions.
    All failures return None (the agent then degrades deterministically).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 5.0,
        cache_dir: str | None = None,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self._model = model or ""
        self._timeout_s = timeout_s if timeout_s > 0 else 5.0
        # Response cache (T070, SC-008): empty/None disables caching.
        self._cache_dir = cache_dir or None
        # Usage accounting for run-cost recording (T063, SC-007).
        # "calls" counts REAL HTTP calls only; cache hits are tracked
        # separately so recorded cost always reflects real usage.
        self.calls = 0
        self.prompt_chars = 0
        self.completion_chars = 0
        self.cache_hits = 0
        self.cache_misses = 0

    @property
    def model(self) -> str:
        return self._model

    # ------------------------------------------------------------------
    # Response cache (T070, SC-008)
    # ------------------------------------------------------------------

    def _cache_key(self, system_prompt: str, user_text: str) -> str:
        """Stable cache key: sha256 over (model, system prompt, user text)."""
        canonical = json.dumps(
            {"model": self._model, "system": system_prompt, "user": user_text},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _cache_lookup(
        self, system_prompt: str, user_text: str,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Return (found, payload) for a cached call.

        found=True with payload=None means a recorded FAILURE being
        replayed (record-replay semantics: pass 2 replays pass 1
        failures too). found=False means a cache miss. Corrupt or
        unreadable entries are treated as misses (best-effort cache).
        """
        if not self._cache_dir:
            return False, None
        path = Path(self._cache_dir) / (self._cache_key(system_prompt, user_text) + ".json")
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False, None
        if not isinstance(entry, dict) or "ok" not in entry:
            return False, None
        return True, entry.get("response") if entry.get("ok") else None

    def _cache_store(
        self,
        system_prompt: str,
        user_text: str,
        payload: dict[str, Any] | None,
    ) -> None:
        """Record a call outcome (success or failure) in the cache."""
        if not self._cache_dir:
            return
        entry = {"ok": payload is not None, "response": payload}
        path = Path(self._cache_dir) / (self._cache_key(system_prompt, user_text) + ".json")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(entry, ensure_ascii=False), encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001 - cache is best-effort
            logger.warning("LLMClient: cache write failed: %s", exc)

    def chat_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any] | str,
    ) -> dict[str, Any] | None:
        """Call the chat completions endpoint and parse JSON from the answer.

        Returns the parsed JSON dict, or None on ANY failure (SC-011).
        With the response cache enabled (T070), identical
        (system, payload) calls replay the recorded outcome without
        any HTTP call, making evaluation passes byte-reproducible.
        """
        user_text = (
            user_payload
            if isinstance(user_payload, str)
            else json.dumps(user_payload, ensure_ascii=False)
        )

        # Cache lookup first: a hit never touches the network and never
        # counts usage chars (cost stays real, SC-007).
        found, cached_payload = self._cache_lookup(system_prompt, user_text)
        if found:
            self.cache_hits += 1
            return cached_payload
        self.cache_misses += 1

        self.calls += 1
        self.prompt_chars += len(system_prompt) + len(user_text)

        if not self._base_url:
            logger.warning("LLMClient: no base_url configured; returning None")
            return None
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.0,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key

        url = self._base_url + "/chat/completions"
        try:
            with httpx.Client(timeout=self._timeout_s) as http:
                resp = http.post(url, json=body, headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "LLMClient: HTTP %s from %s", resp.status_code, url,
                )
                self._cache_store(system_prompt, user_text, None)
                return None
            data = resp.json()
        except Exception as exc:
            logger.warning("LLMClient: request to %s failed: %s", url, exc)
            self._cache_store(system_prompt, user_text, None)
            return None

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            logger.warning("LLMClient: unexpected response shape from %s", url)
            self._cache_store(system_prompt, user_text, None)
            return None

        payload = _extract_json(content)
        self._cache_store(system_prompt, user_text, payload)
        if content:
            self.completion_chars += len(content)
        return payload
