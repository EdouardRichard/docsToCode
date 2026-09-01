"""Test for eval dataset expansion (T022).

Tests: original 11 queries preserved, new lexical/Chinese queries added,
JSON format valid.

These tests MUST FAIL before T023 expands the dataset (TDD).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATASET_PATH = _REPO_ROOT / "eval" / "eval_dataset.json"


@pytest.fixture
def dataset() -> list[dict]:
    with open(_DATASET_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class TestOriginalQueriesPreserved:
    """FR-019: original 11 queries must be preserved for per-query comparison."""

    def test_at_least_11_queries(self, dataset):
        assert len(dataset) >= 11, f"Expected at least 11 queries, got {len(dataset)}"

    def test_original_validateToken_query_present(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("validateToken" in q for q in queries), "validateToken query must be preserved"

    def test_original_user_service_query_present(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("UserService" in q for q in queries), "UserService query must be preserved"


class TestNewQueriesAdded:
    """FR-019/FR-025: new lexical-precision and Chinese queries added."""

    def test_more_than_11_queries(self, dataset):
        assert len(dataset) > 11, f"Expected more than 11, got {len(dataset)}"

    def test_contains_chinese_query(self, dataset):
        queries = [e["query"] for e in dataset]
        for q in queries:
            for ch in q:
                cp = ord(ch)
                if 0x4e00 <= cp <= 0x9fff:
                    return
        pytest.fail("Dataset must contain at least one Chinese query (FR-025)")

    def test_contains_exact_symbol_query(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("#" in q and "." in q for q in queries), "Must contain exact symbol queries"


class TestJSONFormat:
    def test_is_list(self, dataset):
        assert isinstance(dataset, list)

    def test_each_entry_has_required_fields(self, dataset):
        for i, entry in enumerate(dataset):
            assert "query" in entry, f"Entry {i} missing query"
            assert "project_scope" in entry, f"Entry {i} missing project_scope"
            assert "expected_evidence_ids" in entry, f"Entry {i} missing expected_evidence_ids"

    def test_project_scope_nonempty(self, dataset):
        for i, entry in enumerate(dataset):
            assert len(entry["project_scope"]) > 0, f"Entry {i} has empty project_scope"

# ---------------------------------------------------------------------------
# T038: format-specific query expansion (003)
# ---------------------------------------------------------------------------

_NEW_FORMATS = ("openapi", "ddl", "go", "python", "word", "pdf")

# Format-locator patterns (mirrors format-locators.schema.json)
_LOCATOR_PATTERNS = {
    "openapi": [
        r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) /.+$",
        r"^schema:(components\.schemas|definitions)\..+$",
    ],
    "ddl": [
        r"^(table|column|constraint|index|view|procedure):[a-zA-Z_]\w*",
    ],
    "go": [
        r"^[a-z_]\w*\.[A-Za-z_]\w*#[A-Za-z_]\w*$",
    ],
    "python": [
        r"^[a-z_]\w*(\.[A-Z][\w]*)+\.[a-z_]\w*$",
    ],
    "word": [
        r"^#{1,6} .+(?: > #{1,6} .+)*$",
    ],
    "pdf": [
        r"^page:\d+(?: §.+)?$",
    ],
}


def _entries_with_format(dataset, fmt):
    return [e for e in dataset if e.get("format") == fmt]


def _is_exact_locator(query, fmt):
    for pat in _LOCATOR_PATTERNS.get(fmt, []):
        if re.match(pat, query):
            return True
    return False


class TestFormatExpansionQueries:
    """T038 (003): >= 12 new format-specific queries, 2 per format."""

    def test_at_least_30_queries(self, dataset):
        assert len(dataset) >= 30, f"Expected >= 30 (18+12), got {len(dataset)}"

    def test_original_18_count_preserved(self, dataset):
        """First 18 entries are the original 001/002 queries (no format field)."""
        for i in range(18):
            assert "format" not in dataset[i], (
                f"Original entry {i} should not have format field"
            )

    def test_new_entries_have_format_field(self, dataset):
        new = dataset[18:]
        assert len(new) >= 12, f"Expected >= 12 new entries, got {len(new)}"
        for i, e in enumerate(new):
            assert "format" in e, f"New entry {i + 18} missing format field"
            assert e["format"] in _NEW_FORMATS, (
                f"New entry {i + 18} has unexpected format: {e['format']}"
            )

    def test_each_new_format_has_at_least_2_queries(self, dataset):
        for fmt in _NEW_FORMATS:
            entries = _entries_with_format(dataset, fmt)
            assert len(entries) >= 2, (
                f"Format '{fmt}' has {len(entries)} queries, expected >= 2"
            )

    def test_each_format_has_exact_locator(self, dataset):
        for fmt in _NEW_FORMATS:
            entries = _entries_with_format(dataset, fmt)
            locators = [e for e in entries if _is_exact_locator(e["query"], fmt)]
            assert len(locators) >= 1, (
                f"Format '{fmt}' has no exact-locator query"
            )

    def test_each_format_has_natural_language_query(self, dataset):
        for fmt in _NEW_FORMATS:
            entries = _entries_with_format(dataset, fmt)
            nat = [e for e in entries if not _is_exact_locator(e["query"], fmt)]
            assert len(nat) >= 1, (
                f"Format '{fmt}' has no natural-language query"
            )

    def test_new_queries_have_required_fields(self, dataset):
        for i, e in enumerate(dataset[18:]):
            assert "query" in e, f"New entry {i + 18} missing query"
            assert "project_scope" in e, f"New entry {i + 18} missing project_scope"
            assert "expected_evidence_ids" in e, (
                f"New entry {i + 18} missing expected_evidence_ids"
            )
            assert len(e["project_scope"]) > 0, (
                f"New entry {i + 18} has empty project_scope"
            )
            assert len(e["expected_evidence_ids"]) > 0, (
                f"New entry {i + 18} has empty expected_evidence_ids"
            )


class TestFormatSpecificQueries:
    """T038: verify the specific format-specific queries are present."""

    def test_openapi_endpoint_query(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("GET /api/v1/users" in q for q in queries), (
            "OpenAPI endpoint query 'GET /api/v1/users' must be present"
        )

    def test_openapi_schema_natural_language(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("User schema definition" in q for q in queries), (
            "OpenAPI natural-language 'User schema definition' must be present"
        )

    def test_ddl_table_locator(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any(q == "table:users" for q in queries), (
            "DDL exact locator 'table:users' must be present"
        )

    def test_ddl_natural_language(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("orders table definition" in q for q in queries), (
            "DDL natural-language 'orders table definition' must be present"
        )

    def test_go_symbol_query(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("main.UserService#FindUser" in q for q in queries), (
            "Go symbol query 'main.UserService#FindUser' must be present"
        )

    def test_go_natural_language(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("FindUser method" in q for q in queries), (
            "Go natural-language 'FindUser method' must be present"
        )

    def test_python_symbol_query(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("module.User.validate" in q for q in queries), (
            "Python symbol query 'module.User.validate' must be present"
        )

    def test_python_natural_language(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("parse_config function" in q for q in queries), (
            "Python natural-language 'parse_config function' must be present"
        )

    def test_word_heading_query(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("Architecture Design" in q for q in queries), (
            "Word heading 'Architecture Design' query must be present"
        )

    def test_word_section_query(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("Data Flow section" in q for q in queries), (
            "Word 'Data Flow section' query must be present"
        )

    def test_pdf_page_locator(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any(q.startswith("page:1") for q in queries), (
            "PDF page locator 'page:1 ...' must be present"
        )

    def test_pdf_natural_language(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("consensus algorithms" in q for q in queries), (
            "PDF natural-language 'consensus algorithms' must be present"
        )
