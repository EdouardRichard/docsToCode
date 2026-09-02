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
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from an LLM response body.

    Handles responses wrapped in markdown code fences or surrounded by
    prose. Returns None when no JSON object can be recovered.
    """
    if not text:
        return None
    stripped = text.strip()
    # Strip markdown code fences
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
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
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self._model = model or ""
        self._timeout_s = timeout_s if timeout_s > 0 else 5.0

    @property
    def model(self) -> str:
        return self._model

    def chat_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any] | str,
    ) -> dict[str, Any] | None:
        """Call the chat completions endpoint and parse JSON from the answer.

        Returns the parsed JSON dict, or None on ANY failure (SC-011).
        """
        if not self._base_url:
            logger.warning("LLMClient: no base_url configured; returning None")
            return None

        user_text = (
            user_payload
            if isinstance(user_payload, str)
            else json.dumps(user_payload, ensure_ascii=False)
        )
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
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self._base_url}/chat/completions"
        try:
            with httpx.Client(timeout=self._timeout_s) as http:
                resp = http.post(url, json=body, headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "LLMClient: HTTP %s from %s", resp.status_code, url,
                )
                return None
            data = resp.json()
        except Exception as exc:
            logger.warning("LLMClient: request to %s failed: %s", url, exc)
            return None

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            logger.warning("LLMClient: unexpected response shape from %s", url)
            return None

        return _extract_json(content)
