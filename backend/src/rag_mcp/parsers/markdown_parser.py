"""Markdown section-aware parser using markdown-it-py.

Parses Markdown documents into section-aware chunks with heading hierarchy
tracking, line number preservation, and chunk size control per blueprint §7.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from markdown_it import MarkdownIt


# Chunk size thresholds (blueprint §7)
MAX_CHUNK_TOKENS = 1024
MIN_CHUNK_TOKENS = 64


def _estimate_tokens(text: str) -> int:
    """Approximate token count using word splitting."""
    return len(text.split())


@dataclass
class _SectionNode:
    """Internal representation of a heading section in the document tree."""

    level: int  # heading level (1 for #, 2 for ##, etc.)
    title: str  # heading text content
    start_line: int  # 1-based line where heading starts
    end_line: int  # 1-based line where section ends (inclusive)
    content_text: str  # full text of this section (heading + body)
    children: list[_SectionNode] = field(default_factory=list)
    parent: _SectionNode | None = None

    @property
    def section_path(self) -> str:
        """Build the heading hierarchy path like '## 安装 > ### 配置'."""
        parts: list[str] = []
        node: _SectionNode | None = self
        while node is not None:
            prefix = "#" * node.level
            parts.append(f"{prefix} {node.title}")
            node = node.parent
        parts.reverse()
        return " > ".join(parts)

    @property
    def parent_section_path(self) -> str:
        """Return parent's section path, or empty string for top-level."""
        if self.parent is None:
            return ""
        return self.parent.section_path


class MarkdownParser:
    """Section-aware Markdown parser that produces structured chunks.

    Uses markdown-it-py to parse the AST and builds a heading tree.
    Each leaf section becomes a Chunk dict with metadata.
    Sections exceeding MAX_CHUNK_TOKENS are split at paragraph boundaries.
    Sections below MIN_CHUNK_TOKENS are merged into their parent.
    """

    def __init__(self) -> None:
        self._md = MarkdownIt()

    def parse(self, text: str, source_id: int = 0) -> list[dict[str, Any]]:
        """Parse Markdown text into section-aware chunks.

        Args:
            text: Raw Markdown content.
            source_id: Optional source identifier for provenance tracking.

        Returns:
            List of chunk dicts with keys: content_text, section_path,
            start_line, end_line, parent_section_path, token_count, chunk_type.
        """
        if not text or not text.strip():
            return []

        lines = text.split("\n")
        tokens = self._md.parse(text)

        # Extract heading positions from tokens
        headings = self._extract_headings(tokens, lines)

        if not headings:
            # No headings found — treat entire document as one chunk
            stripped = text.strip()
            if not stripped:
                return []
            return [
                {
                    "content_text": stripped,
                    "section_path": "",
                    "start_line": 1,
                    "end_line": len(lines),
                    "parent_section_path": "",
                    "token_count": _estimate_tokens(stripped),
                    "chunk_type": "section",
                }
            ]

        # Build section tree from headings
        root_sections = self._build_section_tree(headings, lines)

        # Collect leaf sections (sections with content)
        leaf_sections = self._collect_leaves(root_sections)

        # Merge small sections into parents
        merged_sections = self._merge_small_sections(leaf_sections)

        # Split large sections and produce final chunks
        chunks: list[dict[str, Any]] = []
        for section in merged_sections:
            sub_chunks = self._split_if_needed(section)
            chunks.extend(sub_chunks)

        return chunks

    def _extract_headings(
        self, tokens: list[Any], lines: list[str]
    ) -> list[tuple[int, int, str]]:
        """Extract heading info from markdown-it tokens.

        Returns list of (level, line_number_1based, title_text).
        """
        headings: list[tuple[int, int, str]] = []
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token.type == "heading_open":
                level = int(token.tag[1])  # h1 -> 1, h2 -> 2, etc.
                # The map attribute gives [start_line, end_line] (0-based)
                start_line_0 = token.map[0] if token.map else 0
                line_num = start_line_0 + 1  # convert to 1-based

                # Next token should be inline with the heading text
                title = ""
                if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                    title = tokens[i + 1].content.strip()

                headings.append((level, line_num, title))
            i += 1

        return headings

    def _build_section_tree(
        self,
        headings: list[tuple[int, int, str]],
        lines: list[str],
    ) -> list[_SectionNode]:
        """Build a tree of section nodes from heading positions."""
        total_lines = len(lines)
        root_sections: list[_SectionNode] = []
        stack: list[_SectionNode] = []  # stack of open sections by level

        for idx, (level, line_num, title) in enumerate(headings):
            # Determine end line: up to the next heading's start line - 1,
            # or end of document
            if idx + 1 < len(headings):
                next_heading_line = headings[idx + 1][1]  # 1-based
                end_line = next_heading_line - 1
            else:
                end_line = total_lines

            # Strip trailing blank lines from section
            while end_line > line_num and not lines[end_line - 1].strip():
                end_line -= 1

            # Extract content text for this section
            section_lines = lines[line_num - 1 : end_line]
            content_text = "\n".join(section_lines).strip()

            node = _SectionNode(
                level=level,
                title=title,
                start_line=line_num,
                end_line=end_line,
                content_text=content_text,
            )

            # Pop stack until we find a parent with lower level
            while stack and stack[-1].level >= level:
                stack.pop()

            if stack:
                node.parent = stack[-1]
                stack[-1].children.append(node)
            else:
                root_sections.append(node)

            stack.append(node)

        return root_sections

    def _collect_leaves(
        self, sections: list[_SectionNode]
    ) -> list[_SectionNode]:
        """Collect all sections that have meaningful content.

        A section is included if it has body text beyond just its heading,
        or if it has no children (leaf node).
        """
        result: list[_SectionNode] = []

        for section in sections:
            if section.children:
                # Check if this section has its own content beyond heading
                heading_line = f"{'#' * section.level} {section.title}"
                body = section.content_text
                # Remove the heading line from content to get body-only text
                body_without_heading = body[len(heading_line) :].strip()

                if body_without_heading:
                    # This section has its own content — include it
                    result.append(section)

                # Recurse into children
                result.extend(self._collect_leaves(section.children))
            else:
                # Leaf node — always include
                result.append(section)

        return result

    def _merge_small_sections(
        self, sections: list[_SectionNode]
    ) -> list[_SectionNode]:
        """Merge sections with < MIN_CHUNK_TOKENS into their parent chunk.

        If a section is too small and has a parent, merge its content into
        the parent's content. Otherwise keep it as-is.
        """
        result: list[_SectionNode] = []
        merged_into_parent: set[int] = set()

        for section in sections:
            token_count = _estimate_tokens(section.content_text)
            if token_count < MIN_CHUNK_TOKENS and section.parent is not None:
                # Mark for merging — we'll append content to parent
                merged_into_parent.add(id(section))
                # Find the parent in our result list and extend its content
                parent_found = False
                for existing in result:
                    if existing is section.parent:
                        existing.content_text += "\n\n" + section.content_text
                        existing.end_line = max(existing.end_line, section.end_line)
                        parent_found = True
                        break
                if not parent_found:
                    # Parent isn't in result yet; add this section anyway
                    # (it will be part of parent when parent is processed)
                    result.append(section)
            else:
                result.append(section)

        return result

    def _split_if_needed(self, section: _SectionNode) -> list[dict[str, Any]]:
        """Split a section into chunks respecting MAX_CHUNK_TOKENS.

        If the section fits within the limit, return a single chunk.
        Otherwise split at paragraph boundaries.
        """
        token_count = _estimate_tokens(section.content_text)

        if token_count <= MAX_CHUNK_TOKENS:
            return [
                {
                    "content_text": section.content_text,
                    "section_path": section.section_path,
                    "start_line": section.start_line,
                    "end_line": section.end_line,
                    "parent_section_path": section.parent_section_path,
                    "token_count": token_count,
                    "chunk_type": "section",
                }
            ]

        # Split at paragraph boundaries
        paragraphs = re.split(r"\n\s*\n", section.content_text)
        chunks: list[dict[str, Any]] = []
        current_text_parts: list[str] = []
        current_tokens = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            para_tokens = _estimate_tokens(para)

            if current_tokens + para_tokens > MAX_CHUNK_TOKENS and current_text_parts:
                # Flush current buffer as a chunk
                chunk_text = "\n\n".join(current_text_parts)
                chunks.append(
                    {
                        "content_text": chunk_text,
                        "section_path": section.section_path,
                        "start_line": section.start_line,
                        "end_line": section.end_line,
                        "parent_section_path": section.parent_section_path,
                        "token_count": _estimate_tokens(chunk_text),
                        "chunk_type": "section",
                    }
                )
                current_text_parts = []
                current_tokens = 0

            current_text_parts.append(para)
            current_tokens += para_tokens

        # Flush remaining
        if current_text_parts:
            chunk_text = "\n\n".join(current_text_parts)
            chunks.append(
                {
                    "content_text": chunk_text,
                    "section_path": section.section_path,
                    "start_line": section.start_line,
                    "end_line": section.end_line,
                    "parent_section_path": section.parent_section_path,
                    "token_count": _estimate_tokens(chunk_text),
                    "chunk_type": "section",
                }
            )

        return chunks
