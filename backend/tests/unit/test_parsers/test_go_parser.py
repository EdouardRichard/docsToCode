import pytest
import os
from rag_mcp.parsers.go_parser import GoParser

FIXTURES = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'tests', 'fixtures', 'samples')

@pytest.fixture
def parser():
    return GoParser()

@pytest.fixture
def sample_go():
    with open(os.path.join(FIXTURES, 'service.go')) as f:
        return f.read()

class TestExtractsTypeSymbols:
    def test_extracts_types(self, parser, sample_go):
        chunks = parser.parse(sample_go, 'service.go')
        types = [c for c in chunks if c['chunk_type'] == 'type']
        paths = [c['symbol_path'] for c in types]
        assert 'main.User' in paths
        assert 'main.UserService' in paths

    def test_extracts_interface(self, parser, sample_go):
        chunks = parser.parse(sample_go, 'service.go')
        ifaces = [c for c in chunks if c['chunk_type'] == 'interface']
        assert len(ifaces) >= 1
        assert any(c['symbol_path'] == 'main.Reader' for c in ifaces)

class TestExtractsMethodSymbols:
    def test_extracts_method(self, parser, sample_go):
        chunks = parser.parse(sample_go, 'service.go')
        methods = [c for c in chunks if c['chunk_type'] == 'method']
        assert len(methods) >= 1
        assert any(c['symbol_path'] == 'main.UserService#FindUser' for c in methods)

    def test_method_parent(self, parser, sample_go):
        chunks = parser.parse(sample_go, 'service.go')
        methods = [c for c in chunks if c['chunk_type'] == 'method']
        for m in methods:
            assert m['parent_symbol_path'] == 'main.UserService'

class TestExtractsFunctionSymbols:
    def test_extracts_function(self, parser, sample_go):
        chunks = parser.parse(sample_go, 'service.go')
        funcs = [c for c in chunks if c['chunk_type'] == 'function']
        assert any(c['symbol_path'] == 'main.ProcessData' for c in funcs)

class TestSymbolPathFormat:
    def test_function_path_format(self, parser, sample_go):
        chunks = parser.parse(sample_go, 'service.go')
        funcs = [c for c in chunks if c['chunk_type'] == 'function']
        for f in funcs:
            assert '.' in f['symbol_path']
            assert '#' not in f['symbol_path']

    def test_method_path_format(self, parser, sample_go):
        chunks = parser.parse(sample_go, 'service.go')
        methods = [c for c in chunks if c['chunk_type'] == 'method']
        for m in methods:
            assert '#' in m['symbol_path']
            parts = m['symbol_path'].split('#')
            assert len(parts) == 2

    def test_type_path_format(self, parser, sample_go):
        chunks = parser.parse(sample_go, 'service.go')
        types = [c for c in chunks if c['chunk_type'] in ('type', 'interface')]
        for t in types:
            assert '.' in t['symbol_path']
            assert '#' not in t['symbol_path']

class TestChunkTypes:
    def test_chunk_types_not_symbol(self, parser, sample_go):
        chunks = parser.parse(sample_go, 'service.go')
        valid_types = {'function', 'method', 'type', 'interface'}
        for c in chunks:
            assert c['chunk_type'] in valid_types, f"Invalid: {c['chunk_type']}"

class TestHandlesParseErrors:
    def test_malformed_go(self, parser):
        with open(os.path.join(FIXTURES, 'malformed.go')) as f:
            bad = f.read()
        with pytest.raises(ValueError):
            parser.parse(bad, 'malformed.go')

    def test_malformed_go_message(self, parser):
        with open(os.path.join(FIXTURES, 'malformed.go')) as f:
            bad = f.read()
        try:
            parser.parse(bad, 'malformed.go')
            assert False, 'Should have raised'
        except ValueError as e:
            assert 'syntax' in str(e).lower() or 'error' in str(e).lower()

class TestHandlesEmptyInput:
    def test_empty(self, parser):
        assert parser.parse('') == []
        assert parser.parse('   ') == []

class TestRequiredFields:
    def test_all_fields_present(self, parser, sample_go):
        chunks = parser.parse(sample_go, 'service.go')
        required = {'content_text', 'symbol_path', 'symbol_type', 'start_line', 'end_line', 'parent_symbol_path', 'token_count', 'chunk_type'}
        for c in chunks:
            assert required.issubset(c.keys()), f"Missing: {required - set(c.keys())}"

    def test_line_numbers_valid(self, parser, sample_go):
        chunks = parser.parse(sample_go, 'service.go')
        lines = sample_go.splitlines()
        for c in chunks:
            assert c['start_line'] >= 1
            assert c['end_line'] >= c['start_line']
            assert c['end_line'] <= len(lines)