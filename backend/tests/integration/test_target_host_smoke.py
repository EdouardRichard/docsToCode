import json
import pytest
import pytest_asyncio
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
FIXTURES = PROJECT_ROOT / 'backend' / 'tests' / 'fixtures' / 'samples'
CONTRACTS_DIR = PROJECT_ROOT / 'specs' / '003-structured-asset-expansion' / 'contracts'
MCP_URL = 'http://127.0.0.1:8080/mcp'


def _load_format_locators():
    with open(CONTRACTS_DIR / 'format-locators.schema.json', encoding='utf-8') as f:
        return json.load(f)


def _validate_source_position(fmt, source_position):
    import re
    schema = _load_format_locators()
    for branch in schema.get('allOf', []):
        if_node = branch.get('if', {}).get('properties', {})
        if if_node.get('format', {}).get('const', '') == fmt:
            pattern = branch.get('then', {}).get('properties', {}).get('source_position', {}).get('pattern', '')
            if pattern and re.fullmatch(pattern, source_position):
                return True
    return False


@pytest_asyncio.fixture
async def openapi_alias():
    # Function-scoped self-contained setup: ingest OpenAPI data.
    from rag_mcp.db import get_session_factory
    from rag_mcp.services.ingestion_service import IngestionService
    from rag_mcp.services.project_service import ProjectService
    from rag_mcp.schemas.project import ProjectCreate
    from rag_mcp.providers.local_cpu import LocalCPUEmbeddingProvider
    from rag_mcp.indexing.qdrant_client import QdrantStore
    from rag_mcp.utils.snowflake import generate_id
    from rag_mcp.api.knowledge_sources import _detect_format
    from rag_mcp.models.knowledge_source import KnowledgeSource
    from rag_mcp.utils.hashing import hash_bytes

    factory = get_session_factory()
    embedding = LocalCPUEmbeddingProvider()
    qdrant = QdrantStore()

    alias = 't048-smoke-' + str(generate_id())
    async with factory() as session:
        svc = ProjectService(session)
        project = await svc.create_project(ProjectCreate(name='T048 Smoke', alias=alias))
        await session.commit()
        scope_id = project.knowledge_scope_id

    content = (FIXTURES / 'openapi.json').read_bytes()
    fmt = _detect_format('openapi.json', content)
    source_id = generate_id()
    save_dir = PROJECT_ROOT / 'backend' / 'data' / 'uploads' / str(scope_id) / str(source_id)
    save_dir.mkdir(parents=True, exist_ok=True)
    (save_dir / 'openapi.json').write_bytes(content)

    async with factory() as session:
        now = datetime.now(timezone.utc)
        source = KnowledgeSource(source_id=source_id, knowledge_scope_id=scope_id, filename='openapi.json', content_hash=hash_bytes(content), format=fmt, size_bytes=len(content), status='uploaded', created_at=now, updated_at=now)
        session.add(source)
        await session.commit()

    async with factory() as session:
        service = IngestionService(session, embedding, qdrant)
        await service.ingest(source_id)

    return alias


class TestTargetHostSmoke:
    @pytest.mark.asyncio
    async def test_search_and_get_evidence_via_mcp_host(self, openapi_alias):
        # T048: verify new-format chunks consumable via real MCP Host (Streamable HTTP).
        #
        # Requires the standalone MCP server on MCP_URL. Start it with:
        #     cd backend && python _run_mcp.py
        # When the server is not running (or the connection drops mid-test),
        # the test SKIPS instead of failing (T050 hardening): connectivity is
        # environment state, not a product defect.
        import asyncio

        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        # Connection failures surface as httpx errors OR as anyio/asyncio
        # cancellations (CancelledError is a BaseException), so both entry
        # and the session phase below catch the union explicitly (T050).
        _connect_errors = (httpx.ConnectError, ConnectionError, OSError,
                           asyncio.CancelledError)

        try:
            client = streamablehttp_client(MCP_URL)
            read, write, _ = await client.__aenter__()
        except _connect_errors as e:
            pytest.skip(f'MCP server not reachable: {type(e).__name__}: {e}')
        except RuntimeError as e:
            # streamable-http cleanup race when the server vanished mid-handshake
            pytest.skip(f'MCP server handshake failed: {e}')

        try:
            async with ClientSession(read, write) as session:
                await session.initialize()
                # search_knowledge
                result = await session.call_tool('search_knowledge', {
                    'query': 'GET /api/v1/users endpoint definition',
                    'project_scope': [openapi_alias],
                })
                payload = json.loads(result.content[0].text)
                assert payload['completion_status'] in ('complete', 'partial', 'no_evidence', 'failed')
                assert payload['completion_status'] == 'complete'
                assert len(payload['evidence']) > 0
                for ev in payload['evidence']:
                    sp = ev['source_position']
                    assert 'source_version' in ev and 'evidence_id' in ev
                    assert _validate_source_position('openapi', sp), f'Invalid source_position: {sp}'

                # get_evidence
                ev_id = payload['evidence'][0]['evidence_id']
                ev_result = await session.call_tool('get_evidence', {
                    'evidence_id': ev_id,
                    'project_scope': [openapi_alias],
                })
                ev_payload = json.loads(ev_result.content[0].text)
                assert ev_payload['status'] in ('available', 'unavailable', 'scope_mismatch')
                assert ev_payload['status'] == 'available'
                assert 'full_content' in ev_payload
                assert 'source_position' in ev_payload
        except _connect_errors as e:
            pytest.skip(f'MCP server connection lost during test: {type(e).__name__}: {e}')
        finally:
            try:
                await client.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 - cleanup must not mask results
                pass