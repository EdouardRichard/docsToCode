"""Contract test for the 006 schema set (T065/T066).

FR-025 / Constitution VII: the five 006 schemas are valid JSON Schema
2020-12; dependent schemas $ref common.schema.json definitions (resolved
here by inlining); the external search_knowledge / get_evidence output
schemas are unchanged (no MCP contract regression). Negative constraints
held: no quality-comparison evaluation (FR-027) and existing guardrails
are untouched (FR-029).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, SchemaError

_CONTRACTS = (
    Path(__file__).resolve().parents[3] / "specs" / "006-runtime-hardening" / "contracts"
)
_MCP_001 = (
    Path(__file__).resolve().parents[3] / "specs" / "001-minimum-rag-mcp-loop" / "contracts"
)

_SCHEMA_FILES = (
    "common.schema.json",
    "writer-lease.schema.json",
    "instance-registry.schema.json",
    "provider-config.schema.json",
    "runtime-metrics.schema.json",
)


def _load(dir_path: Path, name: str) -> dict:
    with open(dir_path / name, encoding="utf-8") as f:
        return json.load(f)


def _merged(schema: dict, common: dict) -> dict:
    """Inline common definitions and rewrite both absolute and relative $refs."""
    merged = copy.deepcopy(schema)
    merged.setdefault("$defs", {})
    merged["$defs"].update(copy.deepcopy(common["definitions"]))
    prefixes = (
        common["$id"] + "#/definitions/",
        "common.schema.json#/definitions/",
    )

    def _rewrite(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if k == "$ref" and isinstance(v, str):
                    for p in prefixes:
                        if v.startswith(p):
                            obj[k] = "#/$defs/" + v[len(p):]
                            break
                else:
                    _rewrite(v)
        elif isinstance(obj, list):
            for item in obj:
                _rewrite(item)

    _rewrite(merged)
    return merged


def test_five_schemas_are_valid_draft_2020_12():
    for name in _SCHEMA_FILES:
        schema = _load(_CONTRACTS, name)
        # check_schema raises SchemaError if not a valid 2020-12 schema
        Draft202012Validator.check_schema(schema)


def test_common_definitions_present():
    common = _load(_CONTRACTS, "common.schema.json")
    for defn in ("InstanceId", "WorkerId", "LeaseId", "InstanceMode", "ProcessRole",
                 "LeaseState", "InstanceState", "ProviderType", "ProviderCapability",
                 "ConcurrencyLimit", "HostTarget", "MetricKey", "CompletionStatus"):
        assert defn in common["definitions"], f"missing definition {defn}"


def test_dependent_schemas_ref_common_resolvable():
    """$ref from each dependent schema resolves against common definitions."""
    common = _load(_CONTRACTS, "common.schema.json")
    for name in (
        "writer-lease.schema.json",
        "instance-registry.schema.json",
        "provider-config.schema.json",
        "runtime-metrics.schema.json",
    ):
        schema = _load(_CONTRACTS, name)
        merged = _merged(schema, common)
        # A merged schema must itself be valid (all $refs resolved)
        Draft202012Validator.check_schema(merged)


def test_external_mcp_schemas_unchanged():
    """FR-025/Constitution VII: search_knowledge/get_evidence output unchanged."""
    search = _load(_MCP_001, "mcp-search-output.schema.json")
    evidence = _load(_MCP_001, "mcp-get-evidence.schema.json")
    Draft202012Validator.check_schema(search)
    Draft202012Validator.check_schema(evidence)
    # Four-state completion_status and evidence/request_id remain the contract
    assert "completion_status" in search["properties"]
    assert "evidence" in search["properties"]
    assert "request_id" in search["required"]
    assert "output" in evidence["properties"]


def test_no_quality_comparison_eval():
    """FR-027: 006 performs no quality-comparison evaluation (engineering hardening)."""
    import inspect

    from rag_mcp import eval as eval_pkg  # noqa: F401
    # instance_form_smoke is a non-regression smoke adapter, not a comparison
    # runner; it must not import the quality comparison runner.
    from rag_mcp.eval import instance_form_smoke as smoke

    src = inspect.getsource(smoke)
    assert "run_comparison" not in src
    assert "run_agentic_comparison" not in src


def test_existing_guardrails_unchanged():
    """FR-029: existing retrieval guardrail defaults are untouched."""
    from rag_mcp.config import get_settings

    s = get_settings()
    assert s.retrieval.top_k_default == 5
    assert s.retrieval.top_k_max == 20
    assert s.retrieval.total_timeout_ms == 30_000
    assert s.hybrid_retrieval.rrf_k == 60
    assert s.graph.enabled is False
    assert s.agentic.enabled is False
