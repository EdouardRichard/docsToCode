import pytest
import os
from rag_mcp.parsers.openapi_parser import OpenAPIParser

FIXTURES = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'tests', 'fixtures', 'samples')

@pytest.fixture
def parser():
    return OpenAPIParser()

@pytest.fixture
def openapi_json():
    with open(os.path.join(FIXTURES, 'openapi.json')) as f:
        return f.read()

@pytest.fixture
def swagger_json():
    with open(os.path.join(FIXTURES, 'swagger.json')) as f:
        return f.read()

class TestExtractsEndpoints:
    def test_extracts_endpoints(self, parser, openapi_json):
        chunks = parser.parse(openapi_json, 'openapi.json')
        endpoints = [c for c in chunks if c['chunk_type'] == 'endpoint']
        assert len(endpoints) >= 2
        paths = [c['structure_path'] for c in endpoints]
        assert any('GET' in p and '/api/v1/users' in p for p in paths)
        assert any('POST' in p and '/api/v1/users' in p for p in paths)

    def test_endpoint_structure_path_format(self, parser, openapi_json):
        chunks = parser.parse(openapi_json, 'openapi.json')
        endpoints = [c for c in chunks if c['chunk_type'] == 'endpoint']
        for e in endpoints:
            parts = e['structure_path'].split(' ', 1)
            assert len(parts) == 2
            assert parts[0] in ('GET','POST','PUT','DELETE','PATCH','HEAD','OPTIONS')
            assert parts[1].startswith('/')

class TestExtractsSchemas:
    def test_extracts_schemas(self, parser, openapi_json):
        chunks = parser.parse(openapi_json, 'openapi.json')
        schemas = [c for c in chunks if c['chunk_type'] == 'schema']
        assert len(schemas) >= 1
        paths = [c['structure_path'] for c in schemas]
        assert any('User' in p for p in paths)

    def test_schema_structure_path_format(self, parser, openapi_json):
        chunks = parser.parse(openapi_json, 'openapi.json')
        schemas = [c for c in chunks if c['chunk_type'] == 'schema']
        for s in schemas:
            assert s['structure_path'].startswith('schema:components.schemas.')

class TestParentChildRelationship:
    def test_endpoint_parent_is_schema(self, parser, openapi_json):
        chunks = parser.parse(openapi_json, 'openapi.json')
        endpoints = [c for c in chunks if c['chunk_type'] == 'endpoint']
        for e in endpoints:
            parent = e['parent_structure_path']
            if parent:
                assert parent.startswith('schema:')

    def test_schema_parent_empty(self, parser, openapi_json):
        chunks = parser.parse(openapi_json, 'openapi.json')
        schemas = [c for c in chunks if c['chunk_type'] == 'schema']
        for s in schemas:
            assert s['parent_structure_path'] == ''

class TestSwagger2Support:
    def test_swagger_definitions(self, parser, swagger_json):
        chunks = parser.parse(swagger_json, 'swagger.json')
        schemas = [c for c in chunks if c['chunk_type'] == 'schema']
        for s in schemas:
            assert s['structure_path'].startswith('schema:definitions.')

    def test_swagger_endpoints(self, parser, swagger_json):
        chunks = parser.parse(swagger_json, 'swagger.json')
        endpoints = [c for c in chunks if c['chunk_type'] == 'endpoint']
        assert len(endpoints) >= 2

class TestHandlesMalformed:
    def test_malformed_rejected(self, parser):
        with open(os.path.join(FIXTURES, 'malformed.openapi.json')) as f:
            text = f.read()
        with pytest.raises(ValueError):
            parser.parse(text, 'malformed.openapi.json')

    def test_malformed_missing_version_field_raises(self, parser):
        # The malformed fixture lacks the openapi/swagger version field but
        # defines a path (/api/v1/items GET) referencing a non-existent
        # schema. The parser must reject it -- naming the missing version
        # field -- rather than fabricating endpoint chunks from its paths
        # (FR-017 graceful degradation, no fake endpoints).
        with open(os.path.join(FIXTURES, 'malformed.openapi.json')) as f:
            text = f.read()
        with pytest.raises(ValueError) as exc_info:
            parser.parse(text, 'malformed.openapi.json')
        msg = str(exc_info.value).lower()
        assert 'version' in msg or 'openapi' in msg or 'swagger' in msg, msg

class TestHandlesEmpty:
    def test_empty(self, parser):
        assert parser.parse('') == []
        assert parser.parse('   ') == []

class TestRequiredFields:
    def test_all_fields_present(self, parser, openapi_json):
        chunks = parser.parse(openapi_json, 'openapi.json')
        required = {'content_text', 'structure_path', 'start_line', 'end_line', 'parent_structure_path', 'token_count', 'chunk_type'}
        for c in chunks:
            assert required.issubset(c.keys())

class TestYAMLSupport:
    def test_yaml_openapi(self, parser):
        with open(os.path.join(FIXTURES, 'openapi.yaml')) as f:
            text = f.read()
        chunks = parser.parse(text, 'openapi.yaml')
        endpoints = [c for c in chunks if c['chunk_type'] == 'endpoint']
        assert len(endpoints) >= 2
        schemas = [c for c in chunks if c['chunk_type'] == 'schema']
        assert len(schemas) >= 1