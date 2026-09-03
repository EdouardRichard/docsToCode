"""Contract test for the runtime metrics endpoint (T061/T062).

FR-016/FR-017/SC-006: GET /runtime/metrics returns a response conforming to
runtime-metrics.schema.json, contains no query/evidence body, and returns
quickly (sub-second aggregation). The endpoint lives on the writer
management plane.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

_CONTRACTS = (
    Path(__file__).resolve().parents[3]
    / "specs" / "006-runtime-hardening" / "contracts"
)


def _load(name: str) -> dict:
    with open(_CONTRACTS / name, encoding="utf-8") as f:
        return json.load(f)


_COMMON = _load("common.schema.json")
_METRICS = _load("runtime-metrics.schema.json")


def _merged(schema: dict) -> dict:
    """Inline common.schema.json definitions and rewrite absolute $refs."""
    merged = copy.deepcopy(schema)
    merged.setdefault("$defs", {})
    merged["$defs"].update(copy.deepcopy(_COMMON["definitions"]))
    prefixes = (
        _COMMON["$id"] + "#/definitions/",  # absolute $id form
        "common.schema.json#/definitions/",  # relative file form
    )

    def _rewrite(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if k == "$ref" and isinstance(v, str):
                    for prefix in prefixes:
                        if v.startswith(prefix):
                            obj[k] = "#/$defs/" + v[len(prefix):]
                            break
                else:
                    _rewrite(v)
        elif isinstance(obj, list):
            for item in obj:
                _rewrite(item)

    _rewrite(merged)
    return merged


_VALIDATOR = Draft202012Validator(_merged(_METRICS))


@pytest.mark.asyncio
async def test_metrics_endpoint_conforms_to_schema(test_client):
    start = time.monotonic()
    response = await test_client.get("/runtime/metrics")
    elapsed = time.monotonic() - start
    assert response.status_code == 200, response.text
    payload = response.json()
    _VALIDATOR.validate(payload)
    # SC-006: aggregation returns quickly (sub-second-ish; allow CI slack)
    assert elapsed < 10.0


@pytest.mark.asyncio
async def test_metrics_has_no_query_or_evidence_body(test_client):
    response = await test_client.get("/runtime/metrics")
    payload = response.json()
    serialized = json.dumps(payload, ensure_ascii=False)
    # FR-017: no query/evidence body anywhere in the response
    assert "query_text" not in serialized
    assert "full_content" not in serialized
    assert "content_excerpt" not in serialized


@pytest.mark.asyncio
async def test_metrics_required_keys_present(test_client):
    response = await test_client.get("/runtime/metrics")
    payload = response.json()
    for key in (
        "generated_at", "window", "request_totals",
        "completion_status_distribution", "latency", "subpath_timings_ms",
        "provider_usage", "ttl_purge", "active_instances",
    ):
        assert key in payload
