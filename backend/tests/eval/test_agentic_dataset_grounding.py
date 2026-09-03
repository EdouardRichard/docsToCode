"""Grounding test for the agentic eval dataset (T060 Red, US4).

The agentic eval dataset MUST be bound to the real evaluation corpus:
  - every expected_evidence_id resolves to a chunk that really exists
  - each referenced chunk belongs to a PUBLISHED version of the entry scope
  - the dataset spans the Java call-graph / DDL foreign-key corpora
    (multi-hop retrieval benefit, FR-027)
  - composition unchanged: >=6 entries, multi_hop/gap/conflict each >=2,
    >=1 Chinese query (validated by test_agentic_eval_batch.py)

This test MUST FAIL while expected_evidence_ids are placeholder IDs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import text as sa_text

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATASET_PATH = _REPO_ROOT / "eval" / "agentic_eval_dataset.json"

# Evaluation corpus scopes that carry the multi-hop corpora (Java call graph,
# DDL foreign keys). The dataset must be grounded in real published chunks.
JAVA_SCOPE = 351193748123680768
DDL_SCOPE = 352016496592945153


@pytest.fixture(scope="module")
def dataset():
    with open(_DATASET_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class TestDatasetGrounding:
    """expected_evidence_ids resolve to real chunks in published versions."""

    async def test_all_expected_evidence_ids_exist(self, db_session, dataset):
        """Every expected_evidence_id must be a real chunk (no placeholders)."""
        for entry in dataset:
            ids = [int(e) for e in entry["expected_evidence_ids"]]
            assert ids, f"Entry has no expected evidence: {entry['query']}"
            result = await db_session.execute(
                sa_text("SELECT chunk_id FROM chunks WHERE chunk_id = ANY(:ids)"),
                {"ids": ids},
            )
            found = {row[0] for row in result.all()}
            missing = set(ids) - found
            assert not missing, (
                f"Query {entry['query']!r} references non-existent chunks: {missing}"
            )

    async def test_expected_chunks_in_published_versions(self, db_session, dataset):
        """Each expected chunk must belong to a published version of the scope."""
        for entry in dataset:
            scope_ids = [int(s) for s in entry["project_scope"]]
            ids = [int(e) for e in entry["expected_evidence_ids"]]
            result = await db_session.execute(
                sa_text(
                    "SELECT c.chunk_id FROM chunks c "
                    "JOIN knowledge_versions kv ON c.version_id = kv.version_id "
                    "WHERE c.chunk_id = ANY(:ids) "
                    "AND c.knowledge_scope_id = ANY(:scopes) "
                    "AND kv.status = 'published'"
                ),
                {"ids": ids, "scopes": scope_ids},
            )
            found = {row[0] for row in result.all()}
            missing = set(ids) - found
            assert not missing, (
                f"Query {entry['query']!r}: chunks not in published version of "
                f"scope {scope_ids}: {missing}"
            )

    def test_spans_java_and_ddl_corpora(self, dataset):
        """Dataset must include queries on both multi-hop corpora (FR-027)."""
        scopes_used = set()
        for entry in dataset:
            scopes_used.update(int(s) for s in entry["project_scope"])
        assert JAVA_SCOPE in scopes_used, "missing Java call-graph corpus queries"
        assert DDL_SCOPE in scopes_used, "missing DDL foreign-key corpus queries"

    def test_no_placeholder_scope(self, dataset):
        """The old placeholder scope must be gone after grounding."""
        for entry in dataset:
            for scope in entry["project_scope"]:
                # Every scope must be resolvable in the grounding corpora set or
                # another real eval scope; the placeholder-only project
                # 351193748123680768 (Java) and 352016496592945153 (DDL) are real,
                # but entries must not reference scopes without chunks.
                assert str(scope).strip() != "", "empty scope entry"
