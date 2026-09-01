"""Section-aware Word text parser (T028).

Parses the text extracted from a .docx by text_extractor.extract_text,
which preserves heading structure with markdown-style markers:

  * "Heading N" paragraphs        -> "#" * N + " " + title
  * list items (List Bullet/...)  -> "- " + text
  * table rows                    -> "| cell | cell |"

The parser builds a heading hierarchy from the # markers -- exactly like
the Markdown parser -- and emits section-aware chunks of type
heading / paragraph / list / table.

This is a pure text parser: it never touches python-docx.  It slots into
the ingestion pipeline after text extraction and credential redaction
(ingestion_service._parse_content passes the redacted text string here),
mirroring how the PDF parser consumes extract_text output.
"""

from __future__ import annotations

import re
from typing import Any

# Chunk size threshold (blueprint section 7).
MAX_CHUNK_TOKENS = 1024

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TABLE_ROW_RE = re.compile(r"^\|.*\|\s*$")
_BULLET_RE = re.compile(r"^[-*]\s+(.+)$")
_NUMBERED_RE = re.compile(r"^\d+\.\s+(.+)$")


def _estimate_tokens(text: str) -> int:
    """Approximate token count using whitespace splitting."""
    return len(text.split())


def _is_list_line(line: str) -> bool:
    """True for markdown-style bullet ("- ") or numbered ("1. ") lines."""
    return bool(_BULLET_RE.match(line) or _NUMBERED_RE.match(line))


def _list_item_text(line: str) -> str:
    """Strip the list marker, returning just the item text."""
    m = _BULLET_RE.match(line)
    if m:
        return m.group(1).strip()
    m = _NUMBERED_RE.match(line)
    if m:
        return m.group(1).strip()
    return line.strip()


class WordParser:
    """Section-aware Word text parser producing structured chunks.

    Each chunk dict carries: content_text, section_path, start_line,
    end_line, parent_section_path, token_count, chunk_type.

    Chunk types:
      * heading   -- one per # heading line
      * paragraph -- one per plain (non-heading/list/table) line
      * list      -- one per contiguous block of list items
      * table     -- one per contiguous block of "| ... |" rows
    """

    def parse(self, text: str, filename: str = "") -> list[dict[str, Any]]:
        """Parse Word-extracted text into section-aware chunks.

        Args:
            text: Text extracted by text_extractor.extract_text (with
                # / | / - structure markers).  May already be
                credential-redacted; the parser is marker-driven and ignores
                any redaction placeholders.
            filename: Source filename, used for the no-heading fallback.

        Returns:
            List of chunk dicts.  Empty list for empty/whitespace input.
        """
        if not text or not text.strip():
            return []

        lines = text.splitlines()
        has_heading = any(
            _HEADING_RE.match(line.strip()) for line in lines if line.strip()
        )
        # Synthesized root used when the document has no headings at all.
        root_path = f"# {filename.strip() or 'document'}"

        heading_stack: list[tuple[int, str]] = []

        def _path_of(nodes: list[tuple[int, str]]) -> str:
            return " > ".join(f"{'#' * lv} {t}" for (lv, t) in nodes)

        def current_path() -> str:
            if not has_heading:
                return root_path
            return _path_of(heading_stack) if heading_stack else ""

        def current_parent() -> str:
            if not has_heading or len(heading_stack) < 2:
                return ""
            return _path_of(heading_stack[:-1])

        def push_heading(level: int, title: str) -> tuple[str, str]:
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            parent = _path_of(heading_stack)
            heading_stack.append((level, title))
            return _path_of(heading_stack), parent

        chunks: list[dict[str, Any]] = []

        # Block buffers for tables and lists (consecutive rows / items are
        # merged into a single chunk).
        table_rows: list[str] = []
        table_start: int | None = None
        table_end = 0
        list_items: list[str] = []
        list_start: int | None = None
        list_end = 0

        def flush_table() -> None:
            nonlocal table_rows, table_start, table_end
            if table_rows:
                content = "\n".join(table_rows)
                chunks.extend(
                    self._split_if_needed(
                        self._make_chunk(
                            content, current_path(), current_parent(),
                            table_start, table_end, "table",
                        )
                    )
                )
            table_rows = []
            table_start = None
            table_end = 0

        def flush_list() -> None:
            nonlocal list_items, list_start, list_end
            if list_items:
                content = "\n".join(list_items)
                chunks.extend(
                    self._split_if_needed(
                        self._make_chunk(
                            content, current_path(), current_parent(),
                            list_start, list_end, "list",
                        )
                    )
                )
            list_items = []
            list_start = None
            list_end = 0

        for i, raw in enumerate(lines):
            line = raw.strip()
            if not line:
                flush_table()
                flush_list()
                continue

            mh = _HEADING_RE.match(line)
            if mh:
                flush_table()
                flush_list()
                hashes, title = mh.group(1), mh.group(2).strip()
                path, parent = push_heading(len(hashes), title)
                chunks.append(
                    self._make_chunk(line, path, parent, i, i, "heading")
                )
                continue

            if _TABLE_ROW_RE.match(line):
                # A table block cannot share lines with a list block.
                flush_list()
                if table_start is None:
                    table_start = i
                table_end = i
                table_rows.append(line)
                continue

            if _is_list_line(line):
                flush_table()
                if list_start is None:
                    list_start = i
                list_end = i
                list_items.append(_list_item_text(line))
                continue

            # Plain paragraph line -> its own paragraph chunk.
            flush_table()
            flush_list()
            chunks.extend(
                self._split_if_needed(
                    self._make_chunk(
                        line, current_path(), current_parent(), i, i,
                        "paragraph",
                    )
                )
            )

        flush_table()
        flush_list()
        return chunks

    # ------------------------------------------------------------------ #
    # Chunk assembly & size control
    # ------------------------------------------------------------------ #

    @staticmethod
    def _make_chunk(
        content: str,
        section_path: str,
        parent_path: str,
        start_idx: int,
        end_idx: int,
        chunk_type: str,
    ) -> dict[str, Any]:
        """Build a chunk dict from 0-based line indices (converted to 1-based)."""
        return {
            "content_text": content,
            "section_path": section_path,
            "start_line": start_idx + 1,
            "end_line": end_idx + 1,
            "parent_section_path": parent_path,
            "token_count": _estimate_tokens(content),
            "chunk_type": chunk_type,
        }

    def _split_if_needed(self, chunk: dict[str, Any]) -> list[dict[str, Any]]:
        """Split an oversized chunk at natural boundaries.

        Tables/lists split at row/item boundaries (newlines); paragraphs
        split at sentence boundaries.  Line numbers are inherited from the
        original chunk (best-effort) because splitting only reorganises
        content.
        """
        if chunk["token_count"] <= MAX_CHUNK_TOKENS:
            return [chunk]

        text = chunk["content_text"]
        if "\n" in text:
            units = text.split("\n")
        else:
            units = re.split(r"(?<=[.!?])\s+", text)
        units = [u for u in units if u.strip()]
        if len(units) <= 1:
            return [chunk]

        out: list[dict[str, Any]] = []
        buf: list[str] = []
        buf_tokens = 0
        for unit in units:
            t = _estimate_tokens(unit)
            if buf and buf_tokens + t > MAX_CHUNK_TOKENS:
                out.append(self._rebuild(chunk, "\n".join(buf)))
                buf = []
                buf_tokens = 0
            buf.append(unit)
            buf_tokens += t
        if buf:
            out.append(self._rebuild(chunk, "\n".join(buf)))
        return out or [chunk]

    @staticmethod
    def _rebuild(template: dict[str, Any], content: str) -> dict[str, Any]:
        """Clone a chunk template with new content and a recomputed token count."""
        return {
            "content_text": content,
            "section_path": template["section_path"],
            "start_line": template["start_line"],
            "end_line": template["end_line"],
            "parent_section_path": template["parent_section_path"],
            "token_count": _estimate_tokens(content),
            "chunk_type": template["chunk_type"],
        }
