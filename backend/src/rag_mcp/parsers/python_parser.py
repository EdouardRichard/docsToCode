"""Python symbol-aware parser using the standard library ast module.

Parses Python source code into symbol-level chunks (function, class, method)
with fully qualified, dot-separated symbol paths.  Unlike the Java parser
(which tags every chunk as chunk_type 'symbol'), the Python parser tags each
chunk with its concrete kind -- 'function', 'class' or 'method' -- and mirrors
that value into symbol_type.

Degradation (FR-017): on a SyntaxError the parser raises ValueError with a
clear message instead of fabricating fake symbols.
"""

from __future__ import annotations

import ast
import logging
import math
import os
from typing import Any

logger = logging.getLogger(__name__)

# Rough token estimate: ~4 source characters per token (mirrors JavaParser).
_CHARS_PER_TOKEN = 4
# Chunks above this many tokens are split at natural (line) boundaries.
TARGET_MAX_TOKENS = 1024


def _estimate_tokens(text: str) -> int:
    """Rough token count estimate based on character length."""
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


def _module_name(filename: str) -> str:
    """Derive the Python module name from a filename.

    "path/to/module.py" -> "module"; "module.py" -> "module".
    """
    if not filename:
        return ""
    base = os.path.basename(filename)
    if base.endswith(".py"):
        base = base[:-3]
    return base


def _compute_line_offsets(source: str) -> list[int]:
    """Return the byte offset where each 1-based line begins."""
    offsets = [0]
    for i, ch in enumerate(source):
        if ch == "\n":
            offsets.append(i + 1)
    return offsets


def _decorator_start(node: ast.AST) -> tuple[int, int] | None:
    """Return (lineno, col) of the first decorator's '@' if *node* is decorated."""
    decorators = getattr(node, "decorator_list", None)
    if not decorators:
        return None
    first = decorators[0]
    # The decorator expression starts one column after the '@'; step back so
    # the extracted source text includes the '@' itself.
    col = first.col_offset - 1
    if col < 0:
        col = 0
    return first.lineno, col


def _node_start_line(node: ast.AST) -> int:
    """Start line of *node*, including any decorators."""
    dec_start = _decorator_start(node)
    if dec_start is not None:
        return dec_start[0]
    return node.lineno


def _node_source(source: str, node: ast.AST, line_offsets: list[int]) -> str:
    """Exact source text of *node* including decorators and type annotations."""
    dec_start = _decorator_start(node)
    if dec_start is not None:
        start_lineno, start_col = dec_start
    else:
        start_lineno, start_col = node.lineno, node.col_offset
    end_lineno = node.end_lineno
    end_col = node.end_col_offset
    start_offset = line_offsets[start_lineno - 1] + start_col
    end_offset = line_offsets[end_lineno - 1] + end_col
    return source[start_offset:end_offset]


def _extract_sub_bodies(stmt: ast.stmt) -> list[list[ast.stmt]]:
    """Statement lists nested inside a compound statement (if/for/with/try/match).

    Functions/classes can be defined inside any of these; returning their
    bodies lets the recursive traversal keep finding nested defs while
    preserving the enclosing scope's path and 'in-class' context.
    """
    bodies: list[list[ast.stmt]] = []
    for attr in ("body", "orelse", "finalbody"):
        val = getattr(stmt, attr, None)
        if isinstance(val, list):
            bodies.append(val)
    handlers = getattr(stmt, "handlers", None)
    if handlers:
        for handler in handlers:
            hb = getattr(handler, "body", None)
            if isinstance(hb, list):
                bodies.append(hb)
    cases = getattr(stmt, "cases", None)
    if cases:
        for case in cases:
            cb = getattr(case, "body", None)
            if isinstance(cb, list):
                bodies.append(cb)
    return bodies


class PythonParser:
    """Symbol-aware Python source parser.

    Produces a list of chunk dicts suitable for downstream embedding and
    indexing.  Each chunk carries symbol metadata (path, type, lines) and an
    approximate token count.
    """

    def parse(
        self,
        source_code: str,
        filename: str = "",
        source_id: int = 0,
    ) -> list[dict[str, Any]]:
        """Parse Python source and return symbol-level chunks.

        Parameters
        ----------
        source_code:
            Raw Python source text.
        filename:
            Optional filename used to derive the module name and for
            diagnostics.
        source_id:
            Knowledge-source identifier (accepted for API parity with
            JavaParser; not used by the parser itself).

        Returns
        -------
        list[dict]
            Each dict contains: content_text, symbol_path, symbol_type,
            start_line, end_line, parent_symbol_path, token_count, chunk_type.

        Raises
        ------
        ValueError
            If *source_code* is not syntactically valid Python (FR-017).
        """
        if not source_code or not source_code.strip():
            return []

        try:
            tree = ast.parse(source_code, filename=filename or "<unknown>")
        except SyntaxError as exc:
            raise ValueError(
                f"Failed to parse Python source {filename or '<unknown>'}: "
                f"{exc.msg} (line {exc.lineno})"
            ) from exc

        module_name = _module_name(filename)
        line_offsets = _compute_line_offsets(source_code)
        chunks: list[dict[str, Any]] = []

        self._process_body(
            tree.body, module_name, False, source_code, line_offsets, chunks
        )

        # Split overlong symbols at natural (line) boundaries.
        finalized: list[dict[str, Any]] = []
        for chunk in chunks:
            finalized.extend(self._maybe_split(chunk))
        return finalized

    # ------------------------------------------------------------------
    # Recursive symbol extraction
    # ------------------------------------------------------------------

    def _process_body(
        self,
        body: list[ast.stmt],
        path_prefix: str,
        in_class: bool,
        source: str,
        line_offsets: list[int],
        chunks: list[dict[str, Any]],
    ) -> None:
        """Walk *body*, emitting chunks for defs/classes and recursing into them.

        *path_prefix* is the symbol path of the enclosing scope (the module
        name at the top level, e.g. 'utils').  *in_class* is True only when the
        direct parent is a class, which makes FunctionDefs 'method's rather
        than 'function's.
        """
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chunk_type = "method" if in_class else "function"
                symbol_path = self._join_path(path_prefix, node.name)
                self._add_chunk(
                    node, symbol_path, path_prefix, chunk_type,
                    source, line_offsets, chunks,
                )
                # A function body is a new function scope (not a class scope),
                # so nested defs are 'function's, not 'method's.
                self._process_body(
                    node.body, symbol_path, False, source, line_offsets, chunks
                )
            elif isinstance(node, ast.ClassDef):
                chunk_type = "class"
                symbol_path = self._join_path(path_prefix, node.name)
                self._add_chunk(
                    node, symbol_path, path_prefix, chunk_type,
                    source, line_offsets, chunks,
                )
                # A class body keeps the 'in-class' context so direct defs
                # become 'method's; nested classes propagate it too.
                self._process_body(
                    node.body, symbol_path, True, source, line_offsets, chunks
                )
            else:
                # Compound statement (if/for/while/with/try/match): descend
                # into its sub-bodies to keep finding nested defs while
                # preserving the current scope's path and in-class context.
                for sub_body in _extract_sub_bodies(node):
                    self._process_body(
                        sub_body, path_prefix, in_class, source,
                        line_offsets, chunks,
                    )

    @staticmethod
    def _join_path(prefix: str, name: str) -> str:
        """Join a scope path with a symbol name using dot separators."""
        return f"{prefix}.{name}" if prefix else name

    @staticmethod
    def _add_chunk(
        node: ast.AST,
        symbol_path: str,
        parent: str,
        chunk_type: str,
        source: str,
        line_offsets: list[int],
        chunks: list[dict[str, Any]],
    ) -> None:
        """Append a fully-formed chunk dict for *node*."""
        content = _node_source(source, node, line_offsets)
        chunks.append({
            "content_text": content,
            "symbol_path": symbol_path,
            "symbol_type": chunk_type,
            "start_line": _node_start_line(node),
            "end_line": node.end_lineno,
            "parent_symbol_path": parent,
            "token_count": _estimate_tokens(content),
            "chunk_type": chunk_type,
        })

    # ------------------------------------------------------------------
    # Overlong-chunk splitting (target 512-1024 tokens)
    # ------------------------------------------------------------------

    @staticmethod
    def _maybe_split(chunk: dict[str, Any]) -> list[dict[str, Any]]:
        """Split an oversized chunk at line boundaries into <=1024-token parts.

        Small symbols (the common case) pass through untouched.  Each split
        part inherits the parent symbol's path/type so retrieval still resolves
        to the same symbol; only content_text and the line range differ.
        """
        if chunk["token_count"] <= TARGET_MAX_TOKENS:
            return [chunk]

        lines = chunk["content_text"].split("\n")
        max_chars = TARGET_MAX_TOKENS * _CHARS_PER_TOKEN
        groups: list[list[str]] = []
        current: list[str] = []
        current_len = 0
        for line in lines:
            addition = len(line) + 1  # +1 for the joining newline
            if current and current_len + addition > max_chars:
                groups.append(current)
                current = []
                current_len = 0
            current.append(line)
            current_len += addition
        if current:
            groups.append(current)

        if len(groups) <= 1:
            return [chunk]

        result: list[dict[str, Any]] = []
        cursor = chunk["start_line"]
        for group in groups:
            text = "\n".join(group)
            n_lines = len(group)
            result.append({
                "content_text": text,
                "symbol_path": chunk["symbol_path"],
                "symbol_type": chunk["symbol_type"],
                "start_line": cursor,
                "end_line": cursor + n_lines - 1,
                "parent_symbol_path": chunk["parent_symbol_path"],
                "token_count": _estimate_tokens(text),
                "chunk_type": chunk["chunk_type"],
            })
            cursor += n_lines
        return result
