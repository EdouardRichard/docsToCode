import pytest
from pathlib import Path
from rag_mcp.utils.snowflake import generate_id

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
FIXTURES = PROJECT_ROOT / 'backend' / 'tests' / 'fixtures' / 'samples'

def _fixture_path(name):
    return str(FIXTURES / name)

def _uniq(prefix):
    return f'{prefix}-{generate_id()}'

class TestFormatExpansionIntegration:
    @pytest.mark.asyncio
    async def test_openapi_upload_and_format(self, test_client):
        alias = _uniq('openapi')
        resp = await test_client.post('/api/projects', json={'name': 'OpenAPI Test', 'alias': alias})
        assert resp.status_code == 201
        scope_id = resp.json()['knowledge_scope_id']
        with open(_fixture_path('openapi.json'), 'rb') as f:
            resp = await test_client.post(f'/api/knowledge-sources?scope_id={scope_id}', files={'file': ('openapi.json', f, 'application/json')})
        assert resp.status_code == 201
        assert resp.json()['format'] == 'openapi'

    @pytest.mark.asyncio
    async def test_ddl_upload_and_format(self, test_client):
        alias = _uniq('ddl')
        resp = await test_client.post('/api/projects', json={'name': 'DDL Test', 'alias': alias})
        assert resp.status_code == 201
        scope_id = resp.json()['knowledge_scope_id']
        with open(_fixture_path('schema.sql'), 'rb') as f:
            resp = await test_client.post(f'/api/knowledge-sources?scope_id={scope_id}', files={'file': ('schema.sql', f, 'text/plain')})
        assert resp.status_code == 201
        assert resp.json()['format'] == 'ddl'

    @pytest.mark.asyncio
    async def test_go_upload_and_format(self, test_client):
        alias = _uniq('go')
        resp = await test_client.post('/api/projects', json={'name': 'Go Test', 'alias': alias})
        assert resp.status_code == 201
        scope_id = resp.json()['knowledge_scope_id']
        with open(_fixture_path('service.go'), 'rb') as f:
            resp = await test_client.post(f'/api/knowledge-sources?scope_id={scope_id}', files={'file': ('service.go', f, 'text/plain')})
        assert resp.status_code == 201
        assert resp.json()['format'] == 'go'

    @pytest.mark.asyncio
    async def test_python_upload_and_format(self, test_client):
        alias = _uniq('py')
        resp = await test_client.post('/api/projects', json={'name': 'Python Test', 'alias': alias})
        assert resp.status_code == 201
        scope_id = resp.json()['knowledge_scope_id']
        with open(_fixture_path('module.py'), 'rb') as f:
            resp = await test_client.post(f'/api/knowledge-sources?scope_id={scope_id}', files={'file': ('module.py', f, 'text/plain')})
        assert resp.status_code == 201
        assert resp.json()['format'] == 'python'

    @pytest.mark.asyncio
    async def test_word_upload_and_format(self, test_client):
        alias = _uniq('word')
        resp = await test_client.post('/api/projects', json={'name': 'Word Test', 'alias': alias})
        assert resp.status_code == 201
        scope_id = resp.json()['knowledge_scope_id']
        with open(_fixture_path('design.docx'), 'rb') as f:
            resp = await test_client.post(f'/api/knowledge-sources?scope_id={scope_id}', files={'file': ('design.docx', f, 'application/octet-stream')})
        assert resp.status_code == 201
        assert resp.json()['format'] == 'word'

    @pytest.mark.asyncio
    async def test_pdf_upload_and_format(self, test_client):
        alias = _uniq('pdf')
        resp = await test_client.post('/api/projects', json={'name': 'PDF Test', 'alias': alias})
        assert resp.status_code == 201
        scope_id = resp.json()['knowledge_scope_id']
        with open(_fixture_path('paper.pdf'), 'rb') as f:
            resp = await test_client.post(f'/api/knowledge-sources?scope_id={scope_id}', files={'file': ('paper.pdf', f, 'application/pdf')})
        assert resp.status_code == 201
        assert resp.json()['format'] == 'pdf'

    @pytest.mark.asyncio
    async def test_mismatched_go_rejected(self, test_client):
        alias = _uniq('mismatch')
        resp = await test_client.post('/api/projects', json={'name': 'Mismatch Test', 'alias': alias})
        assert resp.status_code == 201
        scope_id = resp.json()['knowledge_scope_id']
        with open(_fixture_path('mismatched.go'), 'rb') as f:
            resp = await test_client.post(f'/api/knowledge-sources?scope_id={scope_id}', files={'file': ('mismatched.go', f, 'text/plain')})
        assert resp.status_code == 400