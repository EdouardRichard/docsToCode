"""Unit tests for the Python symbol-aware parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_mcp.parsers.python_parser import PythonParser


FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "fixtures" / "samples"


@pytest.fixture
def parser():
    """Return a fresh PythonParser instance."""
    return PythonParser()


@pytest.fixture
def sample_python():
    """Source of backend/tests/fixtures/samples/module.py."""
    return (FIXTURES_DIR / "module.py").read_text(encoding="utf-8")


@pytest.fixture
def malformed_python():
    """Source of backend/tests/fixtures/samples/malformed.py (SyntaxError)."""
    return (FIXTURES_DIR / "malformed.py").read_text(encoding="utf-8")


@pytest.fixture
def decorated_python():
    """Inline snippet exercising decorators on a function and a method."""
    return (
        "@staticmethod\n"
        "def helper():\n"
        "    return 42\n"
        "\n"
        "class Foo:\n"
        "    @property\n"
        "    def bar(self):\n"
        "        return 1\n"
    )


# ------------------------------------------------------------------ #
# 1. Function extraction
# ------------------------------------------------------------------ #


class TestExtractsFunctionSymbols:
    def test_extracts_module_level_function(self, parser, sample_python):
        """Parser finds the module-level parse_config function."""
        chunks = parser.parse(sample_python, "module.py")
        func_paths = {c["symbol_path"] for c in chunks if c["chunk_type"] == "function"}
        assert "module.parse_config" in func_paths


# ------------------------------------------------------------------ #
# 2. Nested function extraction
# ------------------------------------------------------------------ #


class TestExtractsNestedFunction:
    def test_extracts_nested_function(self, parser, sample_python):
        """parse_line is nested inside parse_config."""
        chunks = parser.parse(sample_python, "module.py")
        by_path = {c["symbol_path"]: c for c in chunks}

        nested = by_path.get("module.parse_config.parse_line")
        assert nested is not None, "expected nested function module.parse_config.parse_line"
        assert nested["chunk_type"] == "function"
        assert nested["symbol_type"] == "function"
        # parent is the enclosing function
        assert nested["parent_symbol_path"] == "module.parse_config"

    def test_nested_function_is_not_a_method(self, parser, sample_python):
        """A function nested in a function stays 'function', not 'method'."""
        chunks = parser.parse(sample_python, "module.py")
        by_path = {c["symbol_path"]: c for c in chunks}
        assert by_path["module.parse_config.parse_line"]["chunk_type"] == "function"


# ------------------------------------------------------------------ #
# 3. Class extraction
# ------------------------------------------------------------------ #


class TestExtractsClassSymbols:
    def test_extracts_user_class(self, parser, sample_python):
        chunks = parser.parse(sample_python, "module.py")
        class_paths = {c["symbol_path"] for c in chunks if c["chunk_type"] == "class"}
        assert "module.User" in class_paths

    def test_extracts_outer_class(self, parser, sample_python):
        chunks = parser.parse(sample_python, "module.py")
        class_paths = {c["symbol_path"] for c in chunks if c["chunk_type"] == "class"}
        assert "module.Outer" in class_paths


# ------------------------------------------------------------------ #
# 4. Nested class extraction
# ------------------------------------------------------------------ #


class TestExtractsNestedClass:
    def test_extracts_nested_class(self, parser, sample_python):
        """Inner is nested inside Outer."""
        chunks = parser.parse(sample_python, "module.py")
        by_path = {c["symbol_path"]: c for c in chunks}
        inner = by_path.get("module.Outer.Inner")
        assert inner is not None
        assert inner["chunk_type"] == "class"
        assert inner["parent_symbol_path"] == "module.Outer"


# ------------------------------------------------------------------ #
# 5. Method extraction
# ------------------------------------------------------------------ #


class TestExtractsMethodSymbols:
    def test_extracts_user_methods(self, parser, sample_python):
        chunks = parser.parse(sample_python, "module.py")
        method_paths = {c["symbol_path"] for c in chunks if c["chunk_type"] == "method"}
        assert "module.User.validate" in method_paths
        assert "module.User.get_display_name" in method_paths

    def test_extracts_nested_class_method(self, parser, sample_python):
        chunks = parser.parse(sample_python, "module.py")
        method_paths = {c["symbol_path"] for c in chunks if c["chunk_type"] == "method"}
        assert "module.Outer.Inner.validate" in method_paths

    def test_method_parent_is_containing_class(self, parser, sample_python):
        chunks = parser.parse(sample_python, "module.py")
        for c in chunks:
            if c["chunk_type"] == "method":
                expected = c["symbol_path"].rsplit(".", 1)[0]
                assert c["parent_symbol_path"] == expected, (
                    f"method {c['symbol_path']} parent "
                    f"{c['parent_symbol_path']!r} != {expected!r}"
                )


# ------------------------------------------------------------------ #
# 6. Symbol path format
# ------------------------------------------------------------------ #


class TestSymbolPathFormat:
    def test_paths_are_dot_separated(self, parser, sample_python):
        chunks = parser.parse(sample_python, "module.py")
        assert chunks, "expected non-empty chunk list"
        for c in chunks:
            path = c["symbol_path"]
            assert path, "empty symbol_path"
            assert path.startswith("module."), path
            assert "#" not in path, f"unexpected '#' separator in {path}"
            parts = path.split(".")
            assert all(parts), f"empty segment in {path}"
            assert len(parts) >= 2, f"expected module.symbol, got {path}"


# ------------------------------------------------------------------ #
# 7. Parent symbol tracking
# ------------------------------------------------------------------ #


class TestParentSymbolTracking:
    def test_top_level_function_parent_is_module(self, parser, sample_python):
        chunks = parser.parse(sample_python, "module.py")
        by_path = {c["symbol_path"]: c for c in chunks}
        assert by_path["module.parse_config"]["parent_symbol_path"] == "module"

    def test_top_level_class_parent_is_module(self, parser, sample_python):
        chunks = parser.parse(sample_python, "module.py")
        by_path = {c["symbol_path"]: c for c in chunks}
        assert by_path["module.User"]["parent_symbol_path"] == "module"
        assert by_path["module.Outer"]["parent_symbol_path"] == "module"

    def test_nested_class_parent(self, parser, sample_python):
        chunks = parser.parse(sample_python, "module.py")
        by_path = {c["symbol_path"]: c for c in chunks}
        assert by_path["module.Outer.Inner"]["parent_symbol_path"] == "module.Outer"

    def test_nested_function_parent(self, parser, sample_python):
        chunks = parser.parse(sample_python, "module.py")
        by_path = {c["symbol_path"]: c for c in chunks}
        assert by_path["module.parse_config.parse_line"]["parent_symbol_path"] == "module.parse_config"


# ------------------------------------------------------------------ #
# 8. Chunk type values (function/class/method, NOT 'symbol')
# ------------------------------------------------------------------ #


class TestChunkTypeValues:
    def test_chunk_type_equals_symbol_type(self, parser, sample_python):
        chunks = parser.parse(sample_python, "module.py")
        for c in chunks:
            assert c["chunk_type"] == c["symbol_type"], (
                f"chunk_type {c['chunk_type']!r} != symbol_type {c['symbol_type']!r}"
            )

    def test_chunk_types_are_function_class_method(self, parser, sample_python):
        allowed = {"function", "class", "method"}
        chunks = parser.parse(sample_python, "module.py")
        for c in chunks:
            assert c["chunk_type"] in allowed, (
                f"expected chunk_type in {allowed}, got {c['chunk_type']!r}"
            )
            assert c["chunk_type"] != "symbol"

    def test_required_keys_present(self, parser, sample_python):
        required = {
            "content_text", "symbol_path", "symbol_type", "start_line",
            "end_line", "parent_symbol_path", "token_count", "chunk_type",
        }
        chunks = parser.parse(sample_python, "module.py")
        for c in chunks:
            assert required.issubset(c.keys()), required - set(c.keys())


# ------------------------------------------------------------------ #
# 9. Decorators preserved
# ------------------------------------------------------------------ #


class TestDecoratorsPreserved:
    def test_function_decorator_preserved(self, parser, decorated_python):
        chunks = parser.parse(decorated_python, "decorators.py")
        by_path = {c["symbol_path"]: c for c in chunks}
        helper = by_path["decorators.helper"]
        assert "@staticmethod" in helper["content_text"]
        assert helper["chunk_type"] == "function"
        # start_line covers the decorator line
        assert helper["start_line"] == 1

    def test_method_decorator_preserved(self, parser, decorated_python):
        chunks = parser.parse(decorated_python, "decorators.py")
        by_path = {c["symbol_path"]: c for c in chunks}
        bar = by_path["decorators.Foo.bar"]
        assert "@property" in bar["content_text"]
        assert bar["chunk_type"] == "method"


# ------------------------------------------------------------------ #
# 10. Malformed source (SyntaxError -> ValueError)
# ------------------------------------------------------------------ #


class TestHandlesSyntaxError:
    def test_malformed_raises_value_error(self, parser, malformed_python):
        with pytest.raises(ValueError):
            parser.parse(malformed_python, "malformed.py")

    def test_value_error_message_is_clear(self, parser, malformed_python):
        with pytest.raises(ValueError) as exc_info:
            parser.parse(malformed_python, "malformed.py")
        msg = str(exc_info.value).lower()
        assert "parse" in msg or "syntax" in msg, msg

    def test_inline_syntax_error_raises(self, parser):
        with pytest.raises(ValueError):
            parser.parse("def broken(\n    pass", "bad.py")


# ------------------------------------------------------------------ #
# 11. Empty input
# ------------------------------------------------------------------ #


class TestHandlesEmptyInput:
    def test_empty_string(self, parser):
        assert parser.parse("", "empty.py") == []

    def test_whitespace_only(self, parser):
        assert parser.parse("   ", "ws.py") == []

    def test_newlines_only(self, parser):
        assert parser.parse("\n\n\n", "nl.py") == []

    def test_no_filename(self, parser):
        assert parser.parse("", "") == []


# ------------------------------------------------------------------ #
# 12. Line numbers accurate
# ------------------------------------------------------------------ #


class TestLineNumbersAccurate:
    def test_line_ranges_valid(self, parser, sample_python):
        chunks = parser.parse(sample_python, "module.py")
        lines = sample_python.splitlines()
        total = len(lines)
        for c in chunks:
            assert c["start_line"] >= 1
            assert c["end_line"] >= c["start_line"]
            assert c["end_line"] <= total
            source_slice = "\n".join(lines[c["start_line"] - 1 : c["end_line"]])
            assert c["content_text"] in source_slice or source_slice in c["content_text"], (
                f"content mismatch for {c['symbol_path']} "
                f"(lines {c['start_line']}-{c['end_line']})"
            )

    def test_specific_symbol_lines(self, parser, sample_python):
        chunks = parser.parse(sample_python, "module.py")
        by_path = {c["symbol_path"]: c for c in chunks}
        # def parse_config is on line 7
        assert by_path["module.parse_config"]["start_line"] == 7
        # class User is on line 27
        assert by_path["module.User"]["start_line"] == 27
        # User.validate is on line 34
        assert by_path["module.User.validate"]["start_line"] == 34
        # nested class Inner is on line 46
        assert by_path["module.Outer.Inner"]["start_line"] == 46
