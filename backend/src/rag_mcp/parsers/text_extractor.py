"""
Binary format text extraction dispatcher (003, FR-011).

Provides extract_text(raw_bytes, fmt) -> str for Word and PDF formats.
Text formats bypass this module -- their raw content is already text.

Extraction failures raise an exception with a clear cause (FR-019).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

BINARY_FORMATS = frozenset({"word", "pdf"})


class TextExtractionError(Exception):
    """Raised when text extraction fails (FR-011/FR-019)."""


def extract_text(raw_bytes, fmt):
    """Extract text from binary format raw bytes."""
    if fmt == "word":
        return _extract_word_text(raw_bytes)
    elif fmt == "pdf":
        return _extract_pdf_text(raw_bytes)
    raise ValueError(f"Format {fmt!r} is not a binary format")


def _extract_word_text(raw_bytes):
    """Extract text from Word .docx with heading structure preserved (FR-011)."""
    import io
    try:
        from docx import Document
    except ImportError as exc:
        raise TextExtractionError("python-docx not installed") from exc
    try:
        doc = Document(io.BytesIO(raw_bytes))
    except Exception as exc:
        raise TextExtractionError(f"Failed to open Word doc: {exc}") from exc
    parts = []
    for para in doc.paragraphs:
        text = para.text
        if not text:
            continue
        style_name = ""
        try:
            style_name = para.style.name or ""
        except Exception:
            pass
        # Preserve heading structure with markdown-style markers
        if style_name.startswith("Heading"):
            try:
                level = int(style_name.replace("Heading", "").strip() or "1")
            except ValueError:
                level = 1
            parts.append("#" * level + " " + text)
        elif style_name == "Title":
            parts.append("# " + text)
        elif style_name.startswith("List Bullet") or style_name.startswith("List Number"):
            # Preserve list structure with markdown-style "- " markers so the
            # downstream parser can emit chunk_type="list" blocks.
            parts.append("- " + text)
        else:
            parts.append(text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells: parts.append("| " + " | ".join(cells) + " |")
    result = chr(10).join(parts)
    if not result.strip():
        raise TextExtractionError("Word document is empty (FR-019)")
    return result


def _extract_pdf_text(raw_bytes):
    """Extract text from PDF with page markers preserved (FR-011)."""
    import io
    try:
        import pdfplumber
    except ImportError as exc:
        raise TextExtractionError("pdfplumber not installed") from exc
    try:
        pages_text = []
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            if len(pdf.pages) == 0:
                raise TextExtractionError("PDF has no pages (FR-019)")
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text() or ""
                # Preserve page boundaries with markers for the parser
                pages_text.append(f"=== PAGE {i+1} ===" + chr(10) + page_text)
    except TextExtractionError:
        raise
    except Exception as exc:
        raise TextExtractionError(f"Failed to open PDF: {exc}") from exc
    result = (chr(10) + chr(10)).join(pages_text)
    # Check for actual text content (not just page markers)
    actual_text = chr(10).join(
        line for line in result.splitlines()
        if line.strip() and not line.strip().startswith("=== PAGE")
    )
    if not actual_text.strip():
        raise TextExtractionError("No text layer in PDF -- scanned? (FR-019)")
    return result
