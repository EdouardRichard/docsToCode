# Contract test: chunk length contract (FR-007, T045).
#
# Every format parser must produce structure-aware chunks whose approximate
# token counts respect the 512-1024 target window and are NOT the product of
# uniform token-window slicing. Each parser is run on its canonical fixture
# and the resulting chunks are inspected for three invariants:
#
# 1. every chunk reports a positive token_count;
# 2. no chunk exceeds the 1024-token ceiling (fixtures are small; an oversized
#    chunk would indicate a missing split-at-natural-boundary step); and
# 3. chunk token counts vary because they reflect structural units of
#    differing size, not a fixed token window (anti-uniform-slicing).

from __future__ import annotations

from pathlib import Path

import pytest

from rag_mcp.parsers.text_extractor import extract_text
from rag_mcp.parsers.openapi_parser import OpenAPIParser
from rag_mcp.parsers.ddl_parser import DDLParser
from rag_mcp.parsers.go_parser import GoParser
from rag_mcp.parsers.python_parser import PythonParser
from rag_mcp.parsers.word_parser import WordParser
from rag_mcp.parsers.pdf_parser import PDFParser

# Project root: backend/tests/contract/test_chunk_length.py -> 4 parents up.
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
FIXTURES = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "samples"

# FR-007 chunk-size ceiling shared by every parser.
MAX_CHUNK_TOKENS = 1024


def _read_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def _read_bytes(name):
    return (FIXTURES / name).read_bytes()


def _assert_chunk_length_contract(chunks, label):
    assert chunks, f"{label}: expected at least one chunk from the fixture"

    counts = [c["token_count"] for c in chunks]

    # 1. Every chunk reports a positive, integer token count.
    for i, tc in enumerate(counts):
        assert isinstance(tc, int) and tc > 0, (
            f"{label}: chunk {i} has non-positive token_count {tc!r}"
        )

    # 2. No chunk exceeds the 1024-token ceiling. These fixtures are small, so
    #    any violation means a parser failed to split an oversized unit at
    #    natural boundaries (FR-007).
    for i, tc in enumerate(counts):
        assert tc <= MAX_CHUNK_TOKENS, (
            f"{label}: chunk {i} exceeds {MAX_CHUNK_TOKENS} tokens ({tc}); "
            f"oversized chunks must be split at natural boundaries"
        )

    # 3. Structure-aware (not uniform token-window) slicing. Uniform slicing
    #    would produce chunks of identical size; structure-aware parsing
    #    produces chunks whose sizes track the underlying units (symbols,
    #    sections, statements) and therefore vary. With >=2 chunks we require
    #    more than one distinct token count; a single chunk trivially
    #    satisfies the contract.
    if len(counts) >= 2:
        distinct = set(counts)
        assert len(distinct) > 1, (
            f"{label}: all {len(counts)} chunks share token_count={counts[0]}, "
            f"which looks like uniform token-window slicing, not "
            f"structure-aware chunking"
        )


class TestOpenAPIChunkLength:
    def test_chunks_respect_length_contract(self):
        text = _read_text("openapi.json")
        chunks = OpenAPIParser().parse(text, "openapi.json")
        _assert_chunk_length_contract(chunks, "openapi")


class TestDDLChunkLength:
    def test_chunks_respect_length_contract(self):
        text = _read_text("schema.sql")
        chunks = DDLParser().parse(text, "schema.sql")
        _assert_chunk_length_contract(chunks, "ddl")


class TestGoChunkLength:
    def test_chunks_respect_length_contract(self):
        text = _read_text("service.go")
        chunks = GoParser().parse(text, "service.go")
        _assert_chunk_length_contract(chunks, "go")


class TestPythonChunkLength:
    def test_chunks_respect_length_contract(self):
        text = _read_text("module.py")
        chunks = PythonParser().parse(text, "module.py")
        _assert_chunk_length_contract(chunks, "python")


class TestWordChunkLength:
    def test_chunks_respect_length_contract(self):
        text = extract_text(_read_bytes("design.docx"), "word")
        chunks = WordParser().parse(text, "design.docx")
        _assert_chunk_length_contract(chunks, "word")


class TestPDFChunkLength:
    def test_chunks_respect_length_contract(self):
        text = extract_text(_read_bytes("paper.pdf"), "pdf")
        chunks = PDFParser().parse(text, "paper.pdf")
        _assert_chunk_length_contract(chunks, "pdf")
