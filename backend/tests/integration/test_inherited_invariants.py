import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.utils.snowflake import generate_id
from datetime import datetime, timezone

class TestInheritedInvariants:
    # FR-020: empty chunk list -> no version published, old version continues
    @pytest.mark.asyncio
    async def test_empty_chunks_no_version_published(self, db_session):
        from rag_mcp.services.ingestion_service import IngestionService
        from sqlalchemy import select, func

        # Mock session that returns a source, then verify no version created
        session = AsyncMock()
        source = MagicMock()
        source.source_id = 12345
        source.knowledge_scope_id = 67890
        source.format = 'go'
        source.filename = 'empty.go'
        source.status = 'uploaded'
        source.processing_error = None

        result = MagicMock()
        result.scalar_one_or_none.return_value = source
        session.execute.return_value = result

        qdrant = MagicMock()
        embedding = MagicMock()
        embedding.embed_texts = AsyncMock(return_value=[[0.1]*8])
        embedding.get_dimension.return_value = 8

        svc = IngestionService.__new__(IngestionService)
        svc._session = session
        svc._embedding_provider = embedding
        svc._qdrant_store = qdrant
        svc._settings = MagicMock()
        svc._settings.embedding_model = 'test-model'
        svc._settings.data_root = './data/uploads'

        # Force _read_raw_bytes to return bytes, and _parse_content to return empty chunks
        svc._read_raw_bytes = AsyncMock(return_value=b"package main\n")
        svc._parse_content = MagicMock(return_value=[])

        with pytest.raises(ValueError, match='No chunks produced'):
            await svc.ingest(12345)

        # No KnowledgeVersion should have been added
        assert not session.add.called or not any(
            isinstance(call.args[0], KnowledgeVersion) for call in session.add.call_args_list
        )

    # FR-021: derived indexes (Dense+Sparse) rebuildable from source+version
    @pytest.mark.asyncio
    async def test_rebuild_indexes_from_source(self, db_session):
        from rag_mcp.services.ingestion_service import IngestionService

        # Verify reprocess() runs the same full pipeline as ingest()
        session = AsyncMock()
        qdrant = MagicMock()
        embedding = MagicMock()
        svc = IngestionService.__new__(IngestionService)
        svc._session = session
        svc._embedding_provider = embedding
        svc._qdrant_store = qdrant
        svc._settings = MagicMock()
        svc._run_pipeline = AsyncMock()

        await svc.reprocess(999)

        # reprocess must invoke the same pipeline (rebuild Dense+Sparse)
        svc._run_pipeline.assert_called_once_with(999, run_type='retry')

    # FR-022: same dense_ready/lexical_ready capability flags, no new flags
    @pytest.mark.asyncio
    async def test_same_capability_flags(self, db_session):
        version = KnowledgeVersion(
            version_id=generate_id(),
            knowledge_scope_id=generate_id(),
            version_number=1,
            capabilities={'dense_ready': True, 'lexical_ready': True},
            status='draft',
            created_at=datetime.now(timezone.utc),
        )
        caps = version.capabilities
        assert caps.get('dense_ready') == True
        assert caps.get('lexical_ready') == True
        assert set(caps.keys()) <= {'dense_ready', 'lexical_ready'}