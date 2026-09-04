import os
import pytest

from rag_mcp.parsers.text_extractor import (
    extract_text,
    TextExtractionError,
    _detect_columns,
    _reconstruct_columnar,
    _suspicious_multicolumn_reason,
)
from rag_mcp.parsers.pdf_parser import PDFParser

FIXTURES = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'tests', 'fixtures', 'samples')

PAGE_WIDTH = 595.0


def _w(x0, x1, top, text):
    """Build a pdfplumber-style word dict."""
    return {'x0': float(x0), 'x1': float(x1), 'top': float(top),
            'bottom': float(top) + 10, 'text': text}


class TestColumnDetection:
    def test_two_column_layout_detected(self):
        words = [
            _w(60, 150, 50, 'Alpha'), _w(60, 150, 70, 'Beta'),
            _w(330, 420, 50, 'Gamma'), _w(330, 420, 70, 'Delta'),
        ]
        n, boundaries = _detect_columns(words, PAGE_WIDTH)
        assert n == 2
        assert len(boundaries) == 1
        assert 150 < boundaries[0] < 330

    def test_single_column_layout_detected(self):
        words = [_w(60, 60 + 50 + (i % 6) * 65, 50 + i * 15, f'w{i}') for i in range(10)]
        n, boundaries = _detect_columns(words, PAGE_WIDTH)
        assert n == 1
        assert boundaries == []

    def test_stray_word_does_not_create_column(self):
        words = [_w(60, 60 + 50 + (i % 6) * 65, 50 + i * 15, f'w{i}') for i in range(10)]
        words.append(_w(400, 450, 90, 'stray'))
        n, boundaries = _detect_columns(words, PAGE_WIDTH)
        assert n == 1

    def test_three_column_layout_detected(self):
        words = []
        for col_x in (60, 250, 440):
            for i in range(4):
                words.append(_w(col_x, col_x + 80, 50 + i * 15, f'c{col_x}w{i}'))
        n, boundaries = _detect_columns(words, PAGE_WIDTH)
        assert n == 3
        assert len(boundaries) == 2


class TestColumnarReconstruction:
    def test_reading_order_left_column_first(self):
        words = [
            _w(60, 150, 50, 'Alpha'), _w(60, 150, 70, 'Beta'),
            _w(330, 420, 50, 'Gamma'), _w(330, 420, 70, 'Delta'),
        ]
        text = _reconstruct_columnar(words, [290.0])
        assert text == 'Alpha\nBeta\nGamma\nDelta'

    def test_words_joined_in_x_order_within_line(self):
        words = [
            _w(200, 260, 50, 'second'), _w(60, 120, 50, 'first'),
            _w(330, 390, 50, 'righty'),
        ]
        text = _reconstruct_columnar(words, [290.0])
        assert text == 'first second\nrighty'


class TestSuspiciousMultiColumn:
    def test_fullwidth_lines_blocking_gutter_flagged(self):
        """8 bimodal lines + 2 full-width lines: no clean gutter, but the
        per-line gap pattern reveals an (undetectable) multi-column layout."""
        words = []
        for i in range(8):
            top = 50 + i * 15
            words.append(_w(60, 200, top, f'L{i}'))
            words.append(_w(330, 470, top, f'R{i}'))
        for j in range(2):
            words.append(_w(60, 470, 500 + j * 15, f'FULL{j}'))
        reason = _suspicious_multicolumn_reason(words, PAGE_WIDTH)
        assert reason is not None
        assert reason != ''

    def test_single_column_not_flagged(self):
        words = [_w(60, 60 + 50 + (i % 6) * 65, 50 + i * 15, f'w{i}') for i in range(12)]
        assert _suspicious_multicolumn_reason(words, PAGE_WIDTH) is None

    def test_clean_two_column_not_flagged(self):
        """A clean two-column layout is handled by _detect_columns, not the
        degradation path."""
        words = []
        for i in range(8):
            top = 50 + i * 15
            words.append(_w(60, 200, top, f'L{i}'))
            words.append(_w(330, 470, top, f'R{i}'))
        assert _suspicious_multicolumn_reason(words, PAGE_WIDTH) is None


class TestPaperPdfReadingOrder:
    """Integration: the real multi-column fixture must be extracted in
    reading order (FR-006 澄清 Q2)."""

    def test_left_column_precedes_right_column(self):
        with open(os.path.join(FIXTURES, 'paper.pdf'), 'rb') as f:
            text = extract_text(f.read(), 'pdf')
        # 左栏末行（结论段）必须先于右栏文本；线性提取会把右栏顶部文本提前
        left_last = text.index('low latency under both normal and failure')
        right_start = text.index('conditions. Future work will focus')
        assert left_last < right_start, (
            'column-aware reading order not preserved: right-column text '
            'appears before the end of the left column'
        )

    def test_all_content_preserved(self):
        with open(os.path.join(FIXTURES, 'paper.pdf'), 'rb') as f:
            text = extract_text(f.read(), 'pdf')
        for phrase in [
            'Research Paper on Distributed',
            'Systems',
            '1 Introduction',
            '2 Background',
            '2.1 Consensus Algorithms',
            '2.2 Data Flow',
            '3 Methodology',
            '4 Results',
            '5 Conclusion',
            'conditions. Future work will focus on geo-replicated',
            'deployments and Byzantine fault tolerance.',
        ]:
            assert phrase in text, f'missing phrase after column-aware extraction: {phrase}'

    def test_no_degradation_annotation_for_clean_columns(self):
        with open(os.path.join(FIXTURES, 'paper.pdf'), 'rb') as f:
            text = extract_text(f.read(), 'pdf')
        assert 'linear-fallback' not in text
        assert text.startswith('=== PAGE 1 ===')


class TestColumnDetectionDegradation:
    """FR-006 edge case: irregular layout -> linear fallback + annotated
    degradation reason (page never discarded)."""

    def _make_irregular_pdf(self, path):
        from reportlab.pdfgen import canvas
        c = canvas.Canvas(str(path), pagesize=(595, 842))
        y = 800
        for i in range(8):
            c.drawString(60, y, f'Left column line {i} with some content')
            c.drawString(330, y, f'Right column line {i} with other content')
            y -= 20
        for j in range(2):
            c.drawString(60, y, f'Full width spanning line {j} blocks the gutter detection')
            y -= 20
        c.save()

    def test_irregular_pdf_degrades_to_linear_with_annotation(self, tmp_path):
        path = tmp_path / 'irregular.pdf'
        self._make_irregular_pdf(path)
        with open(path, 'rb') as f:
            text = extract_text(f.read(), 'pdf')
        # 降级为线性提取：内容不丢失
        assert 'Left column line 0' in text
        assert 'Right column line 7' in text
        assert 'Full width spanning line 1' in text
        # 且标注降级原因
        assert '=== PAGE 1 (linear-fallback' in text

    def test_parser_handles_annotated_page_marker(self):
        parser = PDFParser()
        text = (
            '=== PAGE 1 (linear-fallback: column-layout-inconclusive) ===' + chr(10)
            + 'Para one.' + chr(10) + chr(10)
            + '=== PAGE 2 ===' + chr(10)
            + '2 Heading' + chr(10)
            + 'Body.'
        )
        chunks = parser.parse(text, 'x.pdf')
        contents = ' '.join(c['content_text'] for c in chunks)
        assert 'Para one.' in contents
        assert 'Body.' in contents
        headings = [c for c in chunks if c['chunk_type'] == 'heading']
        assert any(h['section_path'].startswith('page:2') for h in headings)
