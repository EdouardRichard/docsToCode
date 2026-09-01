import pytest
import os
from rag_mcp.parsers.text_extractor import extract_text, TextExtractionError
from rag_mcp.parsers.pdf_parser import PDFParser

FIXTURES = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'tests', 'fixtures', 'samples')

@pytest.fixture
def parser():
    return PDFParser()

@pytest.fixture
def paper_text():
    with open(os.path.join(FIXTURES, 'paper.pdf'), 'rb') as f:
        return extract_text(f.read(), 'pdf')

class TestPDFParserExtractsHeadings:
    def test_extracts_headings(self, parser, paper_text):
        chunks = parser.parse(paper_text, 'paper.pdf')
        headings = [c for c in chunks if c['chunk_type'] == 'heading']
        assert len(headings) >= 3
        titles = [c['section_path'] for c in headings]
        assert any('Introduction' in t for t in titles)
        assert any('Background' in t for t in titles)
        assert any('Methodology' in t for t in titles)

    def test_heading_section_path_format(self, parser, paper_text):
        chunks = parser.parse(paper_text, 'paper.pdf')
        headings = [c for c in chunks if c['chunk_type'] == 'heading']
        for h in headings:
            assert h['section_path'].startswith('page:'), f"Expected page: prefix, got {h['section_path']}"

class TestPDFParserExtractsParagraphs:
    def test_extracts_paragraphs(self, parser, paper_text):
        chunks = parser.parse(paper_text, 'paper.pdf')
        paragraphs = [c for c in chunks if c['chunk_type'] == 'paragraph']
        assert len(paragraphs) >= 3
        for p in paragraphs:
            assert p['content_text'], 'Paragraph should have content'

    def test_paragraph_section_path(self, parser, paper_text):
        chunks = parser.parse(paper_text, 'paper.pdf')
        paragraphs = [c for c in chunks if c['chunk_type'] == 'paragraph']
        for p in paragraphs:
            assert p['section_path'].startswith('page:'), f"Expected page: prefix"

class TestPDFParserPageNumbers:
    def test_every_chunk_has_page_number(self, parser, paper_text):
        chunks = parser.parse(paper_text, 'paper.pdf')
        assert len(chunks) > 0
        for c in chunks:
            assert c['section_path'].startswith('page:'), f"Missing page number in {c['section_path']}"

    def test_start_line_includes_page(self, parser, paper_text):
        chunks = parser.parse(paper_text, 'paper.pdf')
        for c in chunks:
            assert c['start_line'] > 0
            assert c['end_line'] >= c['start_line']

class TestPDFParserChunkTypes:
    def test_chunk_types_valid(self, parser, paper_text):
        chunks = parser.parse(paper_text, 'paper.pdf')
        valid_types = {'heading', 'paragraph'}
        for c in chunks:
            assert c['chunk_type'] in valid_types, f"Invalid chunk_type: {c['chunk_type']}"

class TestPDFParserParentTracking:
    def test_heading_parent(self, parser, paper_text):
        chunks = parser.parse(paper_text, 'paper.pdf')
        headings = [c for c in chunks if c['chunk_type'] == 'heading']
        for h in headings:
            if '2.1' in h.get('section_path', ''):
                assert '2 Background' in h.get('parent_section_path', ''), f"Expected parent to contain 2 Background"

class TestPDFParserEdgeCases:
    def test_empty_input(self, parser):
        assert parser.parse('') == []
        assert parser.parse('   ') == []

    def test_no_page_markers(self, parser):
        text = 'Just some plain text without page markers.'
        chunks = parser.parse(text, 'test.pdf')
        assert len(chunks) >= 1
        assert chunks[0]['section_path'] == 'page:1'

    def test_scanned_pdf_rejected(self):
        with open(os.path.join(FIXTURES, 'scanned.pdf'), 'rb') as f:
            with pytest.raises(TextExtractionError):
                extract_text(f.read(), 'pdf')

    def test_corrupt_pdf_rejected(self):
        with open(os.path.join(FIXTURES, 'corrupt.pdf'), 'rb') as f:
            with pytest.raises(TextExtractionError):
                extract_text(f.read(), 'pdf')

class TestPDFParserRequiredFields:
    def test_all_chunks_have_required_fields(self, parser, paper_text):
        chunks = parser.parse(paper_text, 'paper.pdf')
        required = {'content_text', 'section_path', 'start_line', 'end_line', 'parent_section_path', 'token_count', 'chunk_type'}
        for c in chunks:
            assert required.issubset(c.keys()), f"Missing keys: {required - set(c.keys())}"