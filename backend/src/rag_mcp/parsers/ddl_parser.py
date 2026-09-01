"""DDL (SQL) statement-aware parser using sqlparse.

Parses SQL DDL into structure-aware chunks: tables, columns, named
table-level constraints, indexes, views, and procedures.  DML statements
(INSERT/UPDATE/DELETE) and unsupported dialect features (CREATE EXTENSION,
CREATE MATERIALIZED VIEW, ...) are detected and skipped without aborting the
whole file.  Each chunk carries a structure_path, parent_structure_path, line
range, an approximate token count, and a chunk_type.

The parser uses sqlparse for statement splitting and top-level classification
(Statement.get_type).  Table internals (columns and named constraints) are
extracted with lightweight, parenthesis-depth-aware string parsing because
sqlparse identifier grouping is unreliable inside a CREATE TABLE body.
"""

from __future__ import annotations

import bisect
import logging
import re
from typing import Any

import sqlparse

logger = logging.getLogger(__name__)

# Chunk size ceiling (mirrors the markdown parser blueprint section 7).
MAX_CHUNK_TOKENS = 1024

# Statement types that are DML and must never produce chunks.
_DML_TYPES = {"INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE"}

# Classify a CREATE statement object kind from its leading keywords.  Order
# matters: MATERIALIZED VIEW and UNIQUE INDEX must be tested before the plain
# VIEW / INDEX alternatives.
_CREATE_KIND_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?"
    r"(MATERIALIZED\s+VIEW|UNIQUE\s+INDEX|INDEX|TABLE|VIEW|PROCEDURE|"
    r"EXTENSION|FUNCTION|TRIGGER|SEQUENCE|DATABASE|SCHEMA)\b",
    re.IGNORECASE | re.DOTALL,
)

# Supported CREATE kinds (normalized).  Everything else is skipped as an
# unrecognized dialect feature.
_SUPPORTED_KINDS = {"TABLE", "INDEX", "VIEW", "PROCEDURE"}

# Named table-level constraint: CONSTRAINT <name> <kind> ...
_NAMED_CONSTRAINT_RE = re.compile(
    r"^\s*CONSTRAINT\s+([A-Za-z_][\w]*)\s+"
    r"(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK)\b(.*)$",
    re.IGNORECASE | re.DOTALL,
)

# Bare (unnamed) table-level constraint keyword (no chunk produced for these).
_UNNAMED_CONSTRAINT_RE = re.compile(
    r"^\s*(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK)\b",
    re.IGNORECASE,
)

# Object name extraction regexes (applied to the comment-stripped statement).
_TABLE_NAME_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)",
    re.IGNORECASE,
)
_INDEX_NAME_RE = re.compile(
    r"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([A-Za-z_][\w]*)\s+ON\s+([A-Za-z_][\w]*)",
    re.IGNORECASE,
)
_VIEW_NAME_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+([A-Za-z_][\w]*)\s+AS\b",
    re.IGNORECASE | re.DOTALL,
)
_PROCEDURE_NAME_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\s+([A-Za-z_][\w]*)\b",
    re.IGNORECASE,
)
# Column name = leading (possibly schema-qualified, last segment taken).
_COL_NAME_RE = re.compile(r"^\s*([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)")
# ALTER TABLE target + trailing actions.
_ALTER_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?"
    r"([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)\s+(.*)$",
    re.IGNORECASE | re.DOTALL,
)
# ALTER ADD named constraint: ADD CONSTRAINT <name> <kind> ...
_ALTER_NAMED_RE = re.compile(
    r"^ADD\s+CONSTRAINT\s+([A-Za-z_][\w]*)\s+"
    r"(?:PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK)\b",
    re.IGNORECASE,
)
# ALTER ADD column: ADD [COLUMN] <name> <type ...>
_ALTER_ADD_COL_RE = re.compile(
    r"^ADD\s+(?:COLUMN\s+)?([A-Za-z_][\w]*)\b",
    re.IGNORECASE,
)


def _estimate_tokens(text: str) -> int:
    """Approximate token count using whitespace splitting."""
    return max(1, len(text.split()))


class DDLParser:
    """Statement-aware SQL DDL parser.

    Produces a list of chunk dicts suitable for downstream embedding and
    indexing.  Each chunk carries structure metadata (path, parent path,
    line range) and an approximate token count.
    """

    def parse(
        self,
        text: str,
        filename: str = "",
    ) -> list[dict[str, Any]]:
        """Parse SQL text and return structure-aware DDL chunks.

        Parameters
        ----------
        text:
            Raw SQL source text.
        filename:
            Optional filename for diagnostics / provenance.

        Returns
        -------
        list[dict]
            Each dict contains: content_text, structure_path, start_line,
            end_line, parent_structure_path, token_count, chunk_type.
        """
        if not text or not text.strip():
            return []

        self._text = text
        self._line_starts = self._compute_line_starts(text)

        raw: list[dict[str, Any]] = []
        cursor = 0
        for stmt_text in sqlparse.split(text):
            if not stmt_text or not stmt_text.strip():
                cursor += len(stmt_text)
                continue

            offset = text.find(stmt_text, cursor)
            if offset < 0:
                logger.debug(
                    "Could not locate statement verbatim in %s; skipping: %r",
                    filename or "<unnamed>",
                    stmt_text[:60],
                )
                cursor += len(stmt_text)
                continue
            cursor = offset + len(stmt_text)

            raw.extend(self._process_statement(stmt_text, offset, filename))

        return self._finalize_all(raw)

    # ------------------------------------------------------------------
    # Line / offset helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_line_starts(text: str) -> list[int]:
        """Return char offsets where each 1-based line begins."""
        starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                starts.append(i + 1)
        return starts

    def _line_of(self, offset: int) -> int:
        """1-based line number for a (clamped) char offset."""
        if not self._line_starts:
            return 1
        o = max(0, min(offset, len(self._text) - 1))
        return bisect.bisect_right(self._line_starts, o)

    # ------------------------------------------------------------------
    # Statement dispatch
    # ------------------------------------------------------------------

    def _process_statement(
        self,
        stmt_text: str,
        offset: int,
        filename: str,
    ) -> list[dict[str, Any]]:
        """Classify a single statement and route it to a handler."""
        parsed = sqlparse.parse(stmt_text)
        stmt_type = parsed[0].get_type() if parsed else None

        eff_text, eff_offset = self._strip_leading_comments(stmt_text, offset)
        if not eff_text.strip():
            return []

        if stmt_type in _DML_TYPES:
            logger.debug(
                "Skipping DML statement (%s) in %s",
                stmt_type,
                filename or "<unnamed>",
            )
            return []

        if stmt_type == "CREATE":
            return self._process_create(eff_text, eff_offset, filename)

        if stmt_type == "ALTER":
            return self._process_alter(eff_text, eff_offset, filename)

        logger.debug(
            "Skipping unrecognized statement type %r in %s",
            stmt_type,
            filename or "<unnamed>",
        )
        return []

    def _process_create(
        self,
        eff_text: str,
        eff_offset: int,
        filename: str,
    ) -> list[dict[str, Any]]:
        """Dispatch a CREATE statement to its kind-specific handler."""
        m = _CREATE_KIND_RE.match(eff_text)
        if not m:
            logger.debug(
                "Skipping unrecognized CREATE statement in %s: %r",
                filename or "<unnamed>",
                eff_text[:60],
            )
            return []
        kind = self._normalize_kind(m.group(1))
        if kind == "TABLE":
            return self._process_create_table(eff_text, eff_offset)
        if kind == "INDEX":
            return self._process_create_index(eff_text, eff_offset)
        if kind == "VIEW":
            return self._process_create_view(eff_text, eff_offset)
        if kind == "PROCEDURE":
            return self._process_create_procedure(eff_text, eff_offset)
        logger.debug(
            "Skipping unsupported CREATE %s in %s",
            kind,
            filename or "<unnamed>",
        )
        return []

    @staticmethod
    def _normalize_kind(raw_kind: str) -> str:
        """Normalize a matched CREATE kind to a canonical token."""
        upper = re.sub(r"\s+", " ", raw_kind.upper())
        if upper == "UNIQUE INDEX":
            return "INDEX"
        if upper == "MATERIALIZED VIEW":
            return "MATERIALIZED VIEW"
        return upper

    # ------------------------------------------------------------------
    # CREATE TABLE
    # ------------------------------------------------------------------

    def _process_create_table(
        self,
        eff_text: str,
        eff_offset: int,
    ) -> list[dict[str, Any]]:
        """Extract table, column, and named-constraint chunks."""
        m = _TABLE_NAME_RE.match(eff_text)
        if not m:
            return []
        table_name = m.group(1).split(".")[-1]
        table_path = f"table:{table_name}"

        open_idx = eff_text.find("(", m.end())
        if open_idx < 0:
            # Table without a body (e.g. CREATE TABLE t AS SELECT ...).
            return [
                self._raw_chunk(
                    eff_text.strip(),
                    table_path,
                    "",
                    "table",
                    eff_offset,
                    eff_offset + len(eff_text) - 1,
                )
            ]
        close_idx = self._matching_paren(eff_text, open_idx)
        body = eff_text[open_idx + 1 : close_idx]
        body_base = eff_offset + open_idx + 1

        raw: list[dict[str, Any]] = []

        # Table-level chunk (whole statement).
        raw.append(
            self._raw_chunk(
                eff_text.strip(),
                table_path,
                "",
                "table",
                eff_offset,
                eff_offset + len(eff_text) - 1,
            )
        )

        for entry_text, rel_start in self._split_top_level_commas(body):
            abs_start = body_base + rel_start
            abs_end = abs_start + len(entry_text) - 1

            named = _NAMED_CONSTRAINT_RE.match(entry_text)
            if named:
                cname = named.group(1)
                raw.append(
                    self._raw_chunk(
                        entry_text,
                        f"constraint:{cname}",
                        table_path,
                        "constraint",
                        abs_start,
                        abs_end,
                    )
                )
                continue

            if _UNNAMED_CONSTRAINT_RE.match(entry_text):
                # Unnamed table-level constraint -> not an independent chunk.
                continue

            col_match = _COL_NAME_RE.match(entry_text)
            if col_match:
                col_name = col_match.group(1).split(".")[-1]
                raw.append(
                    self._raw_chunk(
                        entry_text,
                        f"{table_path}.column:{col_name}",
                        table_path,
                        "column",
                        abs_start,
                        abs_end,
                    )
                )
                continue

        return raw

    # ------------------------------------------------------------------
    # CREATE INDEX / VIEW / PROCEDURE
    # ------------------------------------------------------------------

    def _process_create_index(
        self,
        eff_text: str,
        eff_offset: int,
    ) -> list[dict[str, Any]]:
        m = _INDEX_NAME_RE.match(eff_text)
        if not m:
            return []
        name = m.group(1)
        return [
            self._raw_chunk(
                eff_text.strip(),
                f"index:{name}",
                "",
                "index",
                eff_offset,
                eff_offset + len(eff_text) - 1,
            )
        ]

    def _process_create_view(
        self,
        eff_text: str,
        eff_offset: int,
    ) -> list[dict[str, Any]]:
        m = _VIEW_NAME_RE.match(eff_text)
        if not m:
            return []
        name = m.group(1)
        return [
            self._raw_chunk(
                eff_text.strip(),
                f"view:{name}",
                "",
                "view",
                eff_offset,
                eff_offset + len(eff_text) - 1,
            )
        ]

    def _process_create_procedure(
        self,
        eff_text: str,
        eff_offset: int,
    ) -> list[dict[str, Any]]:
        m = _PROCEDURE_NAME_RE.match(eff_text)
        if not m:
            return []
        name = m.group(1)
        return [
            self._raw_chunk(
                eff_text.strip(),
                f"procedure:{name}",
                "",
                "procedure",
                eff_offset,
                eff_offset + len(eff_text) - 1,
            )
        ]

    # ------------------------------------------------------------------
    # ALTER TABLE (decomposed into column/constraint chunks)
    # ------------------------------------------------------------------

    def _process_alter(
        self,
        eff_text: str,
        eff_offset: int,
    ) -> list[dict[str, Any]]:
        m = _ALTER_RE.match(eff_text)
        if not m:
            return []
        table_name = m.group(1).split(".")[-1]
        table_path = f"table:{table_name}"
        rest = m.group(2)
        rest_base = eff_offset + m.start(2)

        raw: list[dict[str, Any]] = []
        for action_text, rel_start in self._split_top_level_commas(rest):
            abs_start = rest_base + rel_start
            abs_end = abs_start + len(action_text) - 1

            named = _ALTER_NAMED_RE.match(action_text)
            if named:
                cname = named.group(1)
                raw.append(
                    self._raw_chunk(
                        action_text,
                        f"constraint:{cname}",
                        table_path,
                        "constraint",
                        abs_start,
                        abs_end,
                    )
                )
                continue

            add_col = _ALTER_ADD_COL_RE.match(action_text)
            if add_col:
                col_name = add_col.group(1).split(".")[-1]
                raw.append(
                    self._raw_chunk(
                        action_text,
                        f"{table_path}.column:{col_name}",
                        table_path,
                        "column",
                        abs_start,
                        abs_end,
                    )
                )
                continue
            # DROP / RENAME / other actions are not chunked.
        return raw

    # ------------------------------------------------------------------
    # String-level parsing helpers
    # ------------------------------------------------------------------

    def _strip_leading_comments(
        self,
        stmt_text: str,
        offset: int,
    ) -> tuple[str, int]:
        """Drop leading dash comment lines and blank lines.

        Returns the effective statement text and its absolute char offset.
        """
        idx = 0
        n = len(stmt_text)
        while idx < n:
            nl = stmt_text.find("\n", idx)
            line_end = nl if nl >= 0 else n
            line = stmt_text[idx:line_end].strip()
            if line == "" or line.startswith("--"):
                idx = line_end + 1 if nl >= 0 else n
            else:
                break
        return stmt_text[idx:], offset + idx

    @staticmethod
    def _split_top_level_commas(text: str) -> list[tuple[str, int]]:
        """Split text on top-level commas (depth- and string-aware).

        Returns a list of (stripped_entry, rel_start) where rel_start is the
        offset of the entry first non-whitespace character within text.
        """
        entries: list[tuple[str, int]] = []
        depth = 0
        in_squote = False
        in_dquote = False
        seg_start = 0
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if in_squote:
                if ch == "'":
                    if i + 1 < n and text[i + 1] == "'":
                        i += 2
                        continue
                    in_squote = False
            elif in_dquote:
                if ch == '"':
                    in_dquote = False
            else:
                if ch == "'":
                    in_squote = True
                elif ch == '"':
                    in_dquote = True
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth = max(0, depth - 1)
                elif ch == "," and depth == 0:
                    entries.append((text[seg_start:i], seg_start))
                    seg_start = i + 1
            i += 1
        if seg_start < n:
            entries.append((text[seg_start:n], seg_start))

        result: list[tuple[str, int]] = []
        for raw_entry, start in entries:
            lead = len(raw_entry) - len(raw_entry.lstrip())
            stripped = raw_entry.strip()
            if stripped:
                result.append((stripped, start + lead))
        return result

    @staticmethod
    def _matching_paren(text: str, open_idx: int) -> int:
        """Return the index of the closing paren matching the one at open_idx."""
        depth = 0
        in_squote = False
        in_dquote = False
        i = open_idx
        n = len(text)
        while i < n:
            ch = text[i]
            if in_squote:
                if ch == "'":
                    if i + 1 < n and text[i + 1] == "'":
                        i += 2
                        continue
                    in_squote = False
            elif in_dquote:
                if ch == '"':
                    in_dquote = False
            else:
                if ch == "'":
                    in_squote = True
                elif ch == '"':
                    in_dquote = True
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        return i
            i += 1
        return n - 1

    # ------------------------------------------------------------------
    # Chunk assembly
    # ------------------------------------------------------------------

    def _raw_chunk(
        self,
        content_text: str,
        structure_path: str,
        parent: str,
        chunk_type: str,
        abs_start: int,
        abs_end: int,
    ) -> dict[str, Any]:
        """Build a provisional chunk carrying absolute offsets (pre-split)."""
        return {
            "content_text": content_text,
            "structure_path": structure_path,
            "parent_structure_path": parent,
            "chunk_type": chunk_type,
            "_abs_start": abs_start,
            "_abs_end": abs_end,
        }

    def _finalize_all(
        self,
        raw: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert raw entries to final chunks, splitting overlong ones."""
        chunks: list[dict[str, Any]] = []
        for entry in raw:
            chunks.extend(self._finalize_one(entry))
        return chunks

    def _finalize_one(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """Emit a chunk (or split sub-chunks) with computed line numbers."""
        content = entry["content_text"]
        if not content.strip():
            return []
        tokens = _estimate_tokens(content)
        abs_start = entry["_abs_start"]
        abs_end = entry["_abs_end"]

        if tokens <= MAX_CHUNK_TOKENS:
            return [self._make_chunk(entry, content, abs_start, abs_end, tokens)]

        # Split an overlong statement at blank-line / newline boundaries.
        sub: list[dict[str, Any]] = []
        search = 0
        for piece in re.split(r"\n\s*\n", content):
            piece = piece.strip()
            if not piece:
                continue
            p_start = content.find(piece, search)
            if p_start < 0:
                p_start = search
            p_end = p_start + len(piece) - 1
            search = p_end + 1
            sub.append(
                self._make_chunk(
                    entry,
                    piece,
                    abs_start + p_start,
                    abs_start + p_end,
                    _estimate_tokens(piece),
                )
            )
        return sub or [
            self._make_chunk(entry, content, abs_start, abs_end, tokens)
        ]

    def _make_chunk(
        self,
        entry: dict[str, Any],
        content: str,
        abs_start: int,
        abs_end: int,
        tokens: int,
    ) -> dict[str, Any]:
        """Assemble the final public chunk dict."""
        start_line = self._line_of(abs_start)
        end_line = self._line_of(abs_end)
        if end_line < start_line:
            end_line = start_line
        return {
            "content_text": content,
            "structure_path": entry["structure_path"],
            "start_line": start_line,
            "end_line": end_line,
            "parent_structure_path": entry["parent_structure_path"],
            "token_count": tokens,
            "chunk_type": entry["chunk_type"],
        }
