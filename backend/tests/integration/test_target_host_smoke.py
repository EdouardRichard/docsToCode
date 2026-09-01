import json
import shutil
import socket
import pytest
import pytest_asyncio
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
FIXTURES = PROJECT_ROOT / 'backend' / 'tests' / 'fixtures' / 'samples'
CONTRACTS_DIR = PROJECT_ROOT / 'specs' / '003-structured-asset-expansion' / 'contracts'
MCP_URL = 'http://127.0.0.1:8080/mcp'


def _mcp_server_reachable(url: str = MCP_URL, timeout: float = 2.0) -> bool:
    """TCP reachability probe for the standalone MCP server (T051).

    Connectivity is environment state, not a product defect: probing BEFORE
    any fixture side effects lets setup skip cleanly when the server is down.
    T050 hardened only the test-body request phase; the fixture setup phase
    ran first and could ERROR instead of skip.
    """
    parsed = urlparse(url)
    host = parsed.hostname or '127.0.0.1'
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ensure_mcp_server(url: str = MCP_URL, timeout: float = 2.0) -> None:
    """Skip the calling test when the MCP server is unreachable (T051)."""
    if not _mcp_server_reachable(url, timeout=timeout):
        pytest.skip(
            f'MCP server not reachable at {url} — start it with: '
            f'cd backend && python _run_mcp.py'
        )


def _smoke_raw_dir(scope_id: int, source_id: int) -> Path:
    """Raw-file directory resolved exactly like IngestionService (T051).

    settings.data_root is CWD-relative ('./data/uploads') and
    IngestionService._read_raw_bytes resolves it the same way, so writing
    through this helper keeps fixture and ingestion consistent regardless of
    the pytest CWD (repo root or backend/).
    """
    from rag_mcp.config import get_settings

    return Path(get_settings().data_root) / str(scope_id) / str(source_id)


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
    # T051 ②: reachability probe BEFORE any setup side effects, so a down
    # server skips cleanly instead of erroring in fixture setup.
    _ensure_mcp_server(MCP_URL)

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
    project_id = None
    scope_id = None
    source_id = generate_id()

    async with factory() as session:
        svc = ProjectService(session)
        project = await svc.create_project(ProjectCreate(name='T048 Smoke', alias=alias))
        await session.commit()
        project_id = project.project_id
        scope_id = project.knowledge_scope_id

    content = (FIXTURES / 'openapi.json').read_bytes()
    fmt = _detect_format('openapi.json', content)
    # T051 ①: write through settings.data_root — exactly the path
    # IngestionService._read_raw_bytes resolves, whatever the pytest CWD.
    save_dir = _smoke_raw_dir(scope_id, source_id)
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

    try:
        yield alias
    finally:
        # T051 ③: best-effort cleanup of the fixture's footprints.
        # RetrievalRuns are append-only by repo convention and stay for TTL.
        try:
            async with factory() as session:
                from sqlalchemy import text as sa_text

                rows = await session.execute(sa_text(
                    "SELECT DISTINCT index_version FROM chunks "
                    "WHERE knowledge_scope_id = :sid"
                ), {"sid": scope_id})
                for iv in [r[0] for r in rows.all()]:
                    for coll in (f"chunks_hybrid_{iv}", f"chunks_dense_{iv}"):
                        try:
                            qdrant.delete_points_by_scope(coll, scope_id)
                        except Exception:  # noqa: BLE001
                            pass
                # Defensive graph purge (openapi ingestion writes no graph
                # rows, but keep parity with the API deletion flows).
                await session.execute(sa_text(
                    "DELETE FROM graph_expansion_path WHERE chunk_id IN ("
                    "SELECT chunk_id FROM chunks WHERE knowledge_scope_id = :sid)"
                ), {"sid": scope_id})
                await session.execute(sa_text(
                    "DELETE FROM soft_relation WHERE knowledge_scope_id = :sid"
                ), {"sid": scope_id})
                await session.execute(sa_text(
                    "DELETE FROM graph_edge WHERE knowledge_scope_id = :sid"
                ), {"sid": scope_id})
                await ProjectService(session).delete_project(project_id)
                await session.commit()
        except Exception:  # noqa: BLE001 - cleanup must not mask results
            pass
        if scope_id is not None:
            shutil.rmtree(_smoke_raw_dir(scope_id, source_id).parent, ignore_errors=True)


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


class TestSmokeGuardT051:
    """Unit-level guards for the T051 skip/robustness helpers.

    These tests are deterministic — they use ephemeral local sockets and need
    neither the real MCP server nor DB/Qdrant, so they run in every suite.
    """

    @staticmethod
    def _free_port() -> int:
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def test_probe_reports_unreachable_port(self):
        port = self._free_port()
        assert _mcp_server_reachable(f'http://127.0.0.1:{port}/mcp', timeout=0.5) is False

    def test_probe_reports_reachable_port(self):
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        try:
            port = srv.getsockname()[1]
            assert _mcp_server_reachable(f'http://127.0.0.1:{port}/mcp', timeout=2.0) is True
        finally:
            srv.close()

    def test_ensure_mcp_server_skips_when_unreachable(self):
        port = self._free_port()
        with pytest.raises(pytest.skip.Exception):
            _ensure_mcp_server(f'http://127.0.0.1:{port}/mcp', timeout=0.5)

    def test_raw_dir_resolves_under_settings_data_root(self):
        # T051 ①: the fixture's raw-file dir MUST resolve exactly like
        # IngestionService._read_raw_bytes (settings.data_root, CWD-relative),
        # so a repo-root pytest run cannot diverge from the ingest path.
        from rag_mcp.config import get_settings

        d = _smoke_raw_dir(123, 456)
        root = Path(get_settings().data_root)
        assert d == root / '123' / '456'