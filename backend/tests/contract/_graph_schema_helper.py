"""Shared helper for 004 graph contract schema tests (T015-T018).

Loads 004 graph schemas and inlines cross-schema $ref references so that
jsonschema can validate instances without a remote registry.

The 004 schemas use absolute $id URLs and $ref to common.schema.json,
graph-relations.schema.json, and graph-expansion-trace.schema.json.
This helper merges all definitions and rewrites $ref prefixes.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTRACTS_DIR = (
    _REPO_ROOT / "specs" / "003-structured-asset-expansion" / "contracts"
)

# Absolute $id prefixes used in the 004 schemas
_REF_PREFIXES = [
    "https://ai-engineering-rag-mcp.local/schemas/003/common.schema.json#/definitions/",
    "https://ai-engineering-rag-mcp.local/schemas/004/graph-relations.schema.json#/definitions/",
    "https://ai-engineering-rag-mcp.local/schemas/004/graph-expansion-trace.schema.json#/definitions/",
]


def load_schema(name: str) -> dict:
    """Load a raw schema JSON from the contracts directory."""
    with open(_CONTRACTS_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


def inline_refs(target: dict, *ref_schemas: dict) -> dict:
    """Inline cross-schema $ref definitions into a single self-contained schema.

    Merges definitions from all ref_schemas into the target's definitions,
    then rewrites absolute $id prefixes to local #/definitions/ references.
    """
    result = json.loads(json.dumps(target))
    result.setdefault("definitions", {})
    for ref_schema in ref_schemas:
        for key, val in ref_schema.get("definitions", {}).items():
            result["definitions"].setdefault(key, val)
        # Also merge $defs (used by eval-graph-comparison-report)
        for key, val in ref_schema.get("$defs", {}).items():
            result.setdefault("$defs", {})
            result["$defs"].setdefault(key, val)

    schema_str = json.dumps(result)
    for prefix in _REF_PREFIXES:
        schema_str = schema_str.replace(prefix, "#/definitions/")
    return json.loads(schema_str)


def common_schema() -> dict:
    return load_schema("common.schema.json")


def graph_relations_schema() -> dict:
    return load_schema("graph-relations.schema.json")


def graph_trace_schema() -> dict:
    return load_schema("graph-expansion-trace.schema.json")


def capabilities_ext_schema() -> dict:
    return load_schema("knowledge-capabilities.graph-extension.schema.json")


def eval_report_schema() -> dict:
    return load_schema("eval-graph-comparison-report.schema.json")
