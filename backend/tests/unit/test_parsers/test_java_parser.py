"""Unit tests for the Java symbol-aware parser."""

from __future__ import annotations

import pytest

from rag_mcp.parsers.java_parser import JavaParser


@pytest.fixture
def parser():
    """Return a fresh JavaParser instance."""
    return JavaParser()


# ------------------------------------------------------------------ #
# 1. Class extraction
# ------------------------------------------------------------------ #


class TestExtractsClassSymbols:
    def test_extracts_class_symbols(self, parser, sample_java):
        """Parser finds the UserService class declaration."""
        chunks = parser.parse(sample_java, "UserService.java")
        class_chunks = [c for c in chunks if c["symbol_type"] == "class"]
        assert len(class_chunks) >= 1
        assert any(
            c["symbol_path"].endswith("UserService") for c in class_chunks
        )


# ------------------------------------------------------------------ #
# 2. Method extraction
# ------------------------------------------------------------------ #


class TestExtractsMethodSymbols:
    def test_extracts_method_symbols(self, parser, sample_java):
        """Parser finds findById, getActiveUsers, and validateToken methods."""
        chunks = parser.parse(sample_java, "UserService.java")
        method_names = {
            c["symbol_path"].split("#")[-1]
            for c in chunks
            if c["symbol_type"] == "method"
        }
        assert "findById" in method_names
        assert "getActiveUsers" in method_names
        assert "validateToken" in method_names


# ------------------------------------------------------------------ #
# 3. Symbol path format
# ------------------------------------------------------------------ #


class TestSymbolPathFormat:
    def test_symbol_path_format(self, parser, sample_java):
        """Method symbol paths use 'ClassName#methodName' format."""
        chunks = parser.parse(sample_java, "UserService.java")
        method_chunks = [c for c in chunks if c["symbol_type"] == "method"]
        for chunk in method_chunks:
            path = chunk["symbol_path"]
            assert "#" in path, f"Expected '#' separator in {path}"
            parts = path.split("#")
            assert len(parts) == 2, f"Expected exactly one '#' in {path}"
            assert parts[0].endswith("UserService")
            assert parts[1]  # method name is non-empty


# ------------------------------------------------------------------ #
# 4. Symbol types
# ------------------------------------------------------------------ #


class TestSymbolTypesCorrect:
    def test_symbol_types_correct(self, parser, sample_java):
        """Class nodes have type 'class', method nodes have type 'method'."""
        chunks = parser.parse(sample_java, "UserService.java")
        types_by_path = {c["symbol_path"]: c["symbol_type"] for c in chunks}

        # The class itself
        assert types_by_path.get("com.example.service.UserService") == "class"

        # Methods
        assert (
            types_by_path.get("com.example.service.UserService#findById")
            == "method"
        )
        assert (
            types_by_path.get("com.example.service.UserService#getActiveUsers")
            == "method"
        )
        assert (
            types_by_path.get("com.example.service.UserService#validateToken")
            == "method"
        )


# ------------------------------------------------------------------ #
# 5. Parent symbol tracking
# ------------------------------------------------------------------ #


class TestParentSymbolTracking:
    def test_parent_symbol_tracking(self, parser, sample_java):
        """Methods reference their containing class as parent_symbol_path."""
        chunks = parser.parse(sample_java, "UserService.java")
        expected_parent = "com.example.service.UserService"

        method_chunks = [c for c in chunks if c["symbol_type"] == "method"]
        assert len(method_chunks) > 0, "Expected at least one method chunk"

        for chunk in method_chunks:
            assert chunk["parent_symbol_path"] == expected_parent, (
                f"Method {chunk['symbol_path']} should have parent "
                f"{expected_parent}, got {chunk['parent_symbol_path']}"
            )

        # Fields should also reference the class
        field_chunks = [c for c in chunks if c["symbol_type"] == "field"]
        for chunk in field_chunks:
            assert chunk["parent_symbol_path"] == expected_parent


# ------------------------------------------------------------------ #
# 6. Line numbers
# ------------------------------------------------------------------ #


class TestLineNumbersAccurate:
    def test_line_numbers_accurate(self, parser, sample_java):
        """start_line and end_line match actual source positions."""
        chunks = parser.parse(sample_java, "UserService.java")
        lines = sample_java.splitlines()

        for chunk in chunks:
            start = chunk["start_line"]
            end = chunk["end_line"]

            # Lines are 1-based and within range
            assert start >= 1, f"start_line {start} < 1"
            assert end >= start, f"end_line {end} < start_line {start}"
            assert end <= len(lines), (
                f"end_line {end} exceeds total lines {len(lines)}"
            )

            # The content_text should appear within those source lines
            source_slice = "\n".join(lines[start - 1 : end])
            assert chunk["content_text"] in source_slice or source_slice in chunk["content_text"], (
                f"Content mismatch for {chunk['symbol_path']} "
                f"(lines {start}-{end})"
            )

    def test_specific_method_lines(self, parser, sample_java):
        """Verify exact line numbers for known methods in sample_java."""
        chunks = parser.parse(sample_java, "UserService.java")
        by_name = {
            c["symbol_path"].split("#")[-1]: c
            for c in chunks
            if c["symbol_type"] == "method"
        }

        # findById starts at line 23 (public Optional<User> findById...)
        assert by_name["findById"]["start_line"] == 23
        assert by_name["findById"]["end_line"] == 25

        # getActiveUsers starts at line 30
        assert by_name["getActiveUsers"]["start_line"] == 30
        assert by_name["getActiveUsers"]["end_line"] == 32

        # validateToken starts at line 34
        assert by_name["validateToken"]["start_line"] == 34
        assert by_name["validateToken"]["end_line"] == 39


# ------------------------------------------------------------------ #
# 7. Chunk type
# ------------------------------------------------------------------ #


class TestChunkTypeIsSymbol:
    def test_chunk_type_is_symbol(self, parser, sample_java):
        """All chunks from valid Java have chunk_type == 'symbol'."""
        chunks = parser.parse(sample_java, "UserService.java")
        assert len(chunks) > 0
        for chunk in chunks:
            assert chunk["chunk_type"] == "symbol", (
                f"Expected chunk_type 'symbol', got {chunk['chunk_type']!r} "
                f"for {chunk['symbol_path']}"
            )


# ------------------------------------------------------------------ #
# 8. Graceful degradation on parse errors
# ------------------------------------------------------------------ #


class TestHandlesParseErrorsGracefully:
    def test_handles_parse_errors_gracefully(self, parser):
        """Invalid Java syntax doesn't crash; returns degraded chunks."""
        bad_java = "public class { broken syntax !!! @@@ void }{"
        chunks = parser.parse(bad_java, "broken.java")

        # Should return something (fallback chunks), not raise
        assert isinstance(chunks, list)
        assert len(chunks) > 0

        # Fallback chunks still have the required keys
        required_keys = {
            "content_text",
            "symbol_path",
            "symbol_type",
            "start_line",
            "end_line",
            "parent_symbol_path",
            "token_count",
            "chunk_type",
        }
        for chunk in chunks:
            assert required_keys.issubset(chunk.keys()), (
                f"Fallback chunk missing keys: "
                f"{required_keys - set(chunk.keys())}"
            )

    def test_degraded_chunks_have_symbol_type_unknown(self, parser):
        """Fallback chunks use symbol_type 'unknown'."""
        bad_java = "this is not java at all {{{{"
        chunks = parser.parse(bad_java, "notjava.java")
        for chunk in chunks:
            assert chunk["symbol_type"] == "unknown"


# ------------------------------------------------------------------ #
# 9. Empty input
# ------------------------------------------------------------------ #


class TestHandlesEmptyInput:
    def test_handles_empty_input(self, parser):
        """Empty string returns empty list."""
        assert parser.parse("") == []
        assert parser.parse("   ") == []
        assert parser.parse("\n\n\n") == []
