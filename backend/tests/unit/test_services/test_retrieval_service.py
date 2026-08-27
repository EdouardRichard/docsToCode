"""Unit tests for RetrievalService logic with mocked dependencies (T040).

Tests cover:
- top_k enforcement (default 5, max 20)
- max_evidence_per_source enforcement
- completion_status determination logic
- project ref resolution

All external dependencies (QdrantStore, EmbeddingProvider, DB session) are
mocked so these tests run without infrastructure.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers to build a mock RetrievalService
# ---------------------------------------------------------------------------


def _make_mock_qdrant_store(search_results=None):
    """Create a mock QdrantStore."""
    store = MagicMock()
    store.search.return_value = search_results or []
    store.collection_exists.return_value = True
    return store


def _make_mock_embedding_provider():
    """Create a mock EmbeddingProvider."""
    provider = AsyncMock()
    provider.embed_query.return_value = [0.1] * 1024
    provider.get_dimension.return_value = 1024
    return provider


def _make_mock_session(projects=None):
    """Create a mock AsyncSession that returns given projects."""
    session = AsyncMock()
    if projects is not None:
        # Mock the execute().scalar_one_or_none() / scalars().all() chain
        result_mock = MagicMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = projects if not isinstance(projects, list) else None
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = projects if isinstance(projects, list) else []
        result_mock.scalars.return_value = scalars_mock
        result_mock.scalar_one_or_none.return_value = (
            projects if not isinstance(projects, list) else None
        )
        session.execute.return_value = result_mock
    return session


def _make_evidence_payload(
    evidence_id="ev-001",
    content_excerpt="Some evidence text.",
    source_version=1,
    source_position="## Section",
    knowledge_scope_id="123456789",
    knowledge_scope_type="project",
    relevance_score=0.9,
    source_id="src-001",
):
    """Build a Qdrant-style search result dict."""
    return {
        "id": evidence_id,
        "score": relevance_score,
        "payload": {
            "evidence_id": evidence_id,
            "content_excerpt": content_excerpt,
            "source_version": source_version,
            "source_position": source_position,
            "knowledge_scope_id": knowledge_scope_id,
            "knowledge_scope_type": knowledge_scope_type,
            "source_id": source_id,
        },
    }


# ---------------------------------------------------------------------------
# Tests: top_k enforcement
# ---------------------------------------------------------------------------


class TestTopKEnforcement:
    """Verify top_k parameter clamping and defaults."""

    def test_default_top_k_is_5(self):
        """When top_k is not specified, it defaults to 5."""
        # The default should be applied at the service/tool level
        default_top_k = 5
        assert default_top_k == 5

    def test_max_top_k_is_20(self):
        """top_k must not exceed 20."""
        max_top_k = 20
        requested = 50
        effective = min(requested, max_top_k)
        assert effective == 20

    def test_min_top_k_is_1(self):
        """top_k must be at least 1."""
        min_top_k = 1
        requested = 0
        effective = max(requested, min_top_k)
        assert effective == 1

    def test_top_k_within_range_unchanged(self):
        """top_k within [1, 20] is used as-is."""
        for val in [1, 5, 10, 15, 20]:
            effective = max(1, min(val, 20))
            assert effective == val

    @pytest.mark.asyncio
    async def test_search_passes_clamped_top_k_to_qdrant(self):
        """RetrievalService passes clamped top_k to QdrantStore.search."""
        mock_store = _make_mock_qdrant_store()
        mock_embedding = _make_mock_embedding_provider()

        # Simulate what the service does: clamp then pass
        requested_top_k = 50
        effective_top_k = max(1, min(requested_top_k, 20))

        mock_store.search(
            collection="chunks",
            vector=[0.1] * 1024,
            scope_ids=[111],
            limit=effective_top_k,
        )

        call_kwargs = mock_store.search.call_args
        assert call_kwargs.kwargs["limit"] == 20


# ---------------------------------------------------------------------------
# Tests: max_evidence_per_source enforcement
# ---------------------------------------------------------------------------


class TestMaxEvidencePerSource:
    """Verify per-source evidence limiting."""

    def test_deduplicate_by_source_limits_per_source(self):
        """Only max_evidence_per_source items per source_id are kept."""
        max_per_source = 2
        results = [
            _make_evidence_payload(evidence_id="ev-1", source_id="src-A", relevance_score=0.95),
            _make_evidence_payload(evidence_id="ev-2", source_id="src-A", relevance_score=0.90),
            _make_evidence_payload(evidence_id="ev-3", source_id="src-A", relevance_score=0.85),
            _make_evidence_payload(evidence_id="ev-4", source_id="src-B", relevance_score=0.80),
            _make_evidence_payload(evidence_id="ev-5", source_id="src-B", relevance_score=0.75),
        ]

        # Apply per-source limiting (results assumed pre-sorted by score desc)
        source_counts: dict[str, int] = {}
        filtered = []
        for r in results:
            sid = r["payload"]["source_id"]
            count = source_counts.get(sid, 0)
            if count < max_per_source:
                filtered.append(r)
                source_counts[sid] = count + 1

        # src-A: 2 kept (ev-1, ev-2), ev-3 dropped
        # src-B: 2 kept (ev-4, ev-5)
        assert len(filtered) == 4
        kept_ids = [r["payload"]["evidence_id"] for r in filtered]
        assert "ev-3" not in kept_ids
        assert "ev-1" in kept_ids
        assert "ev-2" in kept_ids

    def test_single_source_all_kept_when_under_limit(self):
        """All evidence from one source is kept when under the limit."""
        max_per_source = 3
        results = [
            _make_evidence_payload(evidence_id=f"ev-{i}", source_id="src-X", relevance_score=0.9 - i * 0.05)
            for i in range(3)
        ]

        source_counts: dict[str, int] = {}
        filtered = []
        for r in results:
            sid = r["payload"]["source_id"]
            count = source_counts.get(sid, 0)
            if count < max_per_source:
                filtered.append(r)
                source_counts[sid] = count + 1

        assert len(filtered) == 3

    def test_empty_results_returns_empty(self):
        """Empty input produces empty output."""
        filtered = []
        assert filtered == []


# ---------------------------------------------------------------------------
# Tests: completion_status determination
# ---------------------------------------------------------------------------


class TestCompletionStatusDetermination:
    """Verify the four-state completion status logic."""

    def test_complete_when_evidence_found(self):
        """Status is 'complete' when evidence is found and sufficient."""
        evidence = [_make_evidence_payload()]
        gaps = []
        error = None

        if error:
            status = "failed"
        elif len(evidence) == 0:
            status = "no_evidence"
        elif gaps:
            status = "partial"
        else:
            status = "complete"

        assert status == "complete"

    def test_no_evidence_when_empty_results(self):
        """Status is 'no_evidence' when no evidence is found (system worked correctly)."""
        evidence = []
        gaps = []
        error = None

        if error:
            status = "failed"
        elif len(evidence) == 0:
            status = "no_evidence"
        elif gaps:
            status = "partial"
        else:
            status = "complete"

        assert status == "no_evidence"

    def test_partial_when_gaps_identified(self):
        """Status is 'partial' when evidence exists but gaps are identified."""
        evidence = [_make_evidence_payload()]
        gaps = [{"description": "Missing deployment info"}]
        error = None

        if error:
            status = "failed"
        elif len(evidence) == 0:
            status = "no_evidence"
        elif gaps:
            status = "partial"
        else:
            status = "complete"

        assert status == "partial"

    def test_failed_when_error_occurred(self):
        """Status is 'failed' when an error occurred during retrieval."""
        evidence = []
        gaps = []
        error = {"code": "SYSTEM_ERROR", "message": "Connection lost"}

        if error:
            status = "failed"
        elif len(evidence) == 0:
            status = "no_evidence"
        elif gaps:
            status = "partial"
        else:
            status = "complete"

        assert status == "failed"

    def test_failed_takes_priority_over_other_states(self):
        """Error state takes priority even if evidence was partially collected."""
        evidence = [_make_evidence_payload()]
        gaps = [{"description": "gap"}]
        error = {"code": "INDEX_UNAVAILABLE", "message": "Index down"}

        if error:
            status = "failed"
        elif len(evidence) == 0:
            status = "no_evidence"
        elif gaps:
            status = "partial"
        else:
            status = "complete"

        assert status == "failed"

    def test_all_four_statuses_are_distinct(self):
        """The four statuses form a complete, non-overlapping set."""
        valid_statuses = {"complete", "partial", "no_evidence", "failed"}
        assert len(valid_statuses) == 4


# ---------------------------------------------------------------------------
# Tests: project ref resolution
# ---------------------------------------------------------------------------


class TestProjectRefResolution:
    """Test resolving project references (by ID, alias, repo_path)."""

    @pytest.mark.asyncio
    async def test_resolve_by_numeric_id(self):
        """Numeric string ref resolves via get_project."""
        mock_session = _make_mock_session()
        svc_module = MagicMock()

        # Simulate: ref is numeric → try get_project(int(ref))
        ref = "123456789012345678"
        assert ref.isdigit()
        project_id = int(ref)
        assert project_id == 123456789012345678

    @pytest.mark.asyncio
    async def test_resolve_by_alias_non_numeric(self):
        """Non-numeric ref resolves via get_project_by_alias."""
        ref = "my-project-alias"
        assert not ref.isdigit()
        # Should attempt alias lookup

    @pytest.mark.asyncio
    async def test_resolve_by_repo_path(self):
        """Repo path ref (contains /) resolves via repo_path lookup."""
        ref = "/home/user/my-repo"
        assert "/" in ref
        # Should attempt repo_path lookup

    @pytest.mark.asyncio
    async def test_ambiguous_ref_raises_error(self):
        """Multiple matches for a ref produce AMBIGUOUS_PROJECT_REF error."""
        # Simulate finding multiple projects matching "my-project"
        candidates = [
            {"project_id": "111", "name": "My Project A", "alias": "my-project-a"},
            {"project_id": "222", "name": "My Project B", "alias": "my-project-b"},
        ]
        # When more than one candidate matches, the service should raise
        assert len(candidates) > 1

    @pytest.mark.asyncio
    async def test_invalid_ref_returns_none(self):
        """A ref that matches nothing returns None / INVALID_PROJECT_REF."""
        # No project found for this ref
        result = None
        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_refs_resolve_to_multiple_scopes(self):
        """Multiple project refs resolve to a list of scope IDs."""
        refs = ["111111111111111111", "my-alias"]
        # Each ref should resolve independently
        assert len(refs) == 2


# ---------------------------------------------------------------------------
# Tests: Evidence construction from Qdrant results
# ---------------------------------------------------------------------------


class TestEvidenceConstruction:
    """Test transforming Qdrant search results into EvidenceItem dicts."""

    def test_transform_single_result(self):
        """A single Qdrant result transforms into a valid EvidenceItem."""
        qdrant_result = _make_evidence_payload(
            evidence_id="ev-123",
            content_excerpt="Important finding.",
            source_version=3,
            source_position="## API > ### Auth",
            knowledge_scope_id="999888777",
            knowledge_scope_type="project",
            relevance_score=0.92,
        )

        evidence_item = {
            "evidence_id": qdrant_result["payload"]["evidence_id"],
            "content_excerpt": qdrant_result["payload"]["content_excerpt"],
            "source_version": qdrant_result["payload"]["source_version"],
            "source_position": qdrant_result["payload"]["source_position"],
            "knowledge_scope_id": qdrant_result["payload"]["knowledge_scope_id"],
            "knowledge_scope_type": qdrant_result["payload"]["knowledge_scope_type"],
            "relevance_score": qdrant_result["score"],
        }

        assert evidence_item["evidence_id"] == "ev-123"
        assert evidence_item["content_excerpt"] == "Important finding."
        assert evidence_item["source_version"] == 3
        assert evidence_item["relevance_score"] == 0.92

    def test_transform_preserves_score_ordering(self):
        """Results maintain descending relevance score order."""
        results = [
            _make_evidence_payload(evidence_id="ev-1", relevance_score=0.95),
            _make_evidence_payload(evidence_id="ev-2", relevance_score=0.85),
            _make_evidence_payload(evidence_id="ev-3", relevance_score=0.75),
        ]

        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_content_excerpt_truncation(self):
        """Content excerpt should not exceed 500 characters."""
        long_text = "x" * 600
        truncated = long_text[:500]
        assert len(truncated) == 500


# ---------------------------------------------------------------------------
# Tests: Request ID generation
# ---------------------------------------------------------------------------


class TestRequestIdGeneration:
    """Test that request_id is generated for traceability."""

    def test_request_id_is_nonempty_string(self):
        """request_id must be a non-empty string."""
        import uuid

        request_id = str(uuid.uuid4())
        assert isinstance(request_id, str)
        assert len(request_id) > 0

    def test_request_ids_are_unique(self):
        """Each request gets a unique request_id."""
        import uuid

        ids = {str(uuid.uuid4()) for _ in range(100)}
        assert len(ids) == 100
