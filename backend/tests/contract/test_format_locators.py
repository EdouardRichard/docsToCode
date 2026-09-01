import json
import os
import re
import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
CONTRACTS_DIR = PROJECT_ROOT / 'specs' / '003-structured-asset-expansion' / 'contracts'
FIXTURES = PROJECT_ROOT / 'backend' / 'tests' / 'fixtures' / 'samples'

# Load format-locators schema patterns
def _load_schema():
    schema_path = CONTRACTS_DIR / 'format-locators.schema.json'
    with open(schema_path, encoding='utf-8') as f:
        return json.load(f)

SCHEMA = _load_schema()

# Extract patterns from the schema's allOf branches
def _get_patterns():
    patterns = {}
    for branch in SCHEMA.get('allOf', []):
        props = branch.get('then', {}).get('properties', {})
        if 'source_position' in props and 'pattern' in props['source_position']:
            fmt = branch.get('if', {}).get('properties', {}).get('format', {}).get('const', '')
            ct = branch.get('if', {}).get('properties', {}).get('chunk_type', {}).get('const', '')
            if fmt and ct:
                patterns[(fmt, ct)] = props['source_position']['pattern']
            elif fmt:
                # Format-level pattern (applies to all chunk_types of that format)
                patterns[(fmt, '*')] = props['source_position']['pattern']
    return patterns

PATTERNS = _get_patterns()

def _validate_position(fmt, chunk_type, source_position):
    # Find the specific (format, chunk_type) pattern, or the (format, *) pattern
    pattern = PATTERNS.get((fmt, chunk_type)) or PATTERNS.get((fmt, '*'))
    if pattern is None:
        # Some formats have format-level patterns without chunk_type
        return True  # Skip if no pattern defined
    # JSON schema patterns use ^...$ implicitly, Python re needs fullmatch
    py_pattern = pattern.replace('\\\\', '\\')  # unescape for Python
    if re.fullmatch(py_pattern, source_position):
        return True
    return False


class TestOpenAPILocators:
    def test_endpoint_positions_valid(self):
        from rag_mcp.parsers.openapi_parser import OpenAPIParser
        text = (FIXTURES / 'openapi.json').read_text()
        chunks = OpenAPIParser().parse(text, 'openapi.json')
        for c in chunks:
            sp = c['structure_path']
            assert _validate_position('openapi', c['chunk_type'], sp), f"Invalid endpoint/schema position: {sp}"


class TestDDLLocators:
    def test_ddl_positions_valid(self):
        from rag_mcp.parsers.ddl_parser import DDLParser
        text = (FIXTURES / 'schema.sql').read_text()
        chunks = DDLParser().parse(text, 'schema.sql')
        for c in chunks:
            sp = c['structure_path']
            assert _validate_position('ddl', c['chunk_type'], sp), f"Invalid DDL position: {sp}"


class TestGoLocators:
    def test_go_positions_valid(self):
        from rag_mcp.parsers.go_parser import GoParser
        text = (FIXTURES / 'service.go').read_text()
        chunks = GoParser().parse(text, 'service.go')
        for c in chunks:
            sp = c['symbol_path']
            assert _validate_position('go', c['chunk_type'], sp), f"Invalid Go position: {sp}"


class TestPythonLocators:
    def test_python_positions_valid(self):
        from rag_mcp.parsers.python_parser import PythonParser
        text = (FIXTURES / 'module.py').read_text()
        chunks = PythonParser().parse(text, 'module.py')
        for c in chunks:
            sp = c['symbol_path']
            assert _validate_position('python', c['chunk_type'], sp), f"Invalid Python position: {sp}"


class TestWordLocators:
    def test_word_positions_valid(self):
        from rag_mcp.parsers.text_extractor import extract_text
        from rag_mcp.parsers.word_parser import WordParser
        raw = (FIXTURES / 'design.docx').read_bytes()
        text = extract_text(raw, 'word')
        chunks = WordParser().parse(text, 'design.docx')
        for c in chunks:
            sp = c['section_path']
            assert _validate_position('word', c['chunk_type'], sp), f"Invalid Word position: {sp}"


class TestPDFLocators:
    def test_pdf_positions_valid(self):
        from rag_mcp.parsers.text_extractor import extract_text
        from rag_mcp.parsers.pdf_parser import PDFParser
        raw = (FIXTURES / 'paper.pdf').read_bytes()
        text = extract_text(raw, 'pdf')
        chunks = PDFParser().parse(text, 'paper.pdf')
        for c in chunks:
            sp = c['section_path']
            assert _validate_position('pdf', c['chunk_type'], sp), f"Invalid PDF position: {sp}"