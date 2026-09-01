"""Unit test for eval dataset structural-benefit queries (T026).

Validates >=6 new structural-benefit queries (Java call chain, DDL FK chain,
>=1 Chinese), all fields valid, original queries preserved (FR-021).

This test MUST FAIL before the queries are added (TDD).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATASET_PATH = _REPO_ROOT / "eval" / "eval_dataset.json"

_ORIGINAL_COUNT = 30  # pre-004 dataset size


def _load_dataset() -> list[dict]:
    with open(_DATASET_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text))


class TestStructuralQueries:
    def test_dataset_exists(self):
        assert _DATASET_PATH.exists(), "eval_dataset.json must exist"

    def test_original_queries_preserved(self):
        """FR-021: original queries must be preserved."""
        data = _load_dataset()
        assert len(data) >= _ORIGINAL_COUNT, (
            f"Dataset must preserve {_ORIGINAL_COUNT} original queries, got {len(data)}"
        )

    def test_at_least_six_structural_queries(self):
        """FR-021: >=6 structural-benefit queries added."""
        data = _load_dataset()
        structural = [q for q in data if q.get("is_structural_benefit") is True]
        assert len(structural) >= 6, (
            f"Expected >=6 structural queries, got {len(structural)}"
        )

    def test_at_least_one_chinese_query(self):
        """FR-021: >=1 Chinese structural query."""
        data = _load_dataset()
        structural = [q for q in data if q.get("is_structural_benefit") is True]
        chinese = [q for q in structural if _has_chinese(q.get("query", ""))]
        assert len(chinese) >= 1, (
            f"Expected >=1 Chinese structural query, got {len(chinese)}"
        )

    def test_structural_queries_have_valid_fields(self):
        """All structural queries MUST have query, project_scope, expected_evidence_ids."""
        data = _load_dataset()
        structural = [q for q in data if q.get("is_structural_benefit") is True]
        for q in structural:
            assert isinstance(q.get("query"), str) and len(q["query"]) > 0
            assert isinstance(q.get("project_scope"), list) and len(q["project_scope"]) > 0
            assert isinstance(q.get("expected_evidence_ids"), list) and len(q["expected_evidence_ids"]) > 0

    def test_structural_queries_cover_java_and_ddl(self):
        """Structural queries MUST cover both Java call chain and DDL FK chain."""
        data = _load_dataset()
        structural = [q for q in data if q.get("is_structural_benefit") is True]
        queries = " ".join(q.get("query", "").lower() for q in structural)
        assert "call" in queries or "调用" in queries, "Should cover Java call chain"
        assert "foreign" in queries or "reference" in queries or "外键" in queries or "引用" in queries, (
            "Should cover DDL FK chain"
        )
