"""DDL foreign-key hard-relation extractor (T022).

Deterministically extracts fk_references/fk_referenced_by edges from DDL SQL
using regex-based FOREIGN KEY ... REFERENCES detection (blueprint §10.1,
research sec 7, FR-001/FR-002). Table names are matched to chunk_ids via the
provided chunk list (symbol_path = 'table:<name>'); the DDL parser reuses the
same 'table:<name>' structure_path convention.

Only determinable relations become hard edges: when a referenced or
referencing table is absent from the project chunks, no edge is fabricated
(Constitution III). Self-referencing FKs produce no edge (no self-edges).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from rag_mcp.graph.store.base import GraphScope

logger = logging.getLogger(__name__)

# CREATE TABLE <name> (  — captures the table name and the opening paren so the
# statement body can be isolated for FK scanning. Schema-qualified names are
# accepted; the last segment is taken as the table name.
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)\s*\(",
    re.IGNORECASE,
)

# FOREIGN KEY (cols) REFERENCES <ref_table> [(ref_cols)]
# Matches both named ('CONSTRAINT fk FOREIGN KEY ...') and unnamed
# ('FOREIGN KEY ...') table-level constraints, since the keyword 'FOREIGN KEY'
# anchors the match regardless of a preceding 'CONSTRAINT <name>'.
_FK_RE = re.compile(
    r"FOREIGN\s+KEY\s*\(([^)]*)\)\s+REFERENCES\s+"
    r"([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)"
    r"(?:\s*\(([^)]*)\))?",
    re.IGNORECASE,
)


def _matching_paren(text: str, open_idx: int) -> int:
    """Return the index of the closing paren matching the one at open_idx.

    Depth- and string-literal aware so CHECK constraints or defaults containing
    parens/quotes do not desync the scan.
    """
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


class DdlFkExtractor:
    """Deterministic DDL fk_references/fk_referenced_by extractor.

    Mirrors the JavaCallGraphExtractor contract: extract(source_code, chunks,
    scope) -> list[edge dict]. Each FK relationship yields a pair of reciprocal
    hard edges: fk_references (referencing -> referenced) and fk_referenced_by
    (referenced -> referencing).
    """

    def extract(
        self,
        source_code: str,
        chunks: list[dict[str, Any]],
        scope: GraphScope,
    ) -> list[dict[str, Any]]:
        """Extract fk_references/fk_referenced_by edges from DDL source.

        Parameters
        ----------
        source_code:
            Raw DDL SQL source text containing CREATE TABLE statements.
        chunks:
            Chunk dicts produced by the DDL parser (or normalized). Table
            chunks carry symbol_path/structure_path = 'table:<name>' and a
            'chunk_id'.
        scope:
            Isolation triple stamped onto every edge.

        Returns
        -------
        list[dict]
            Hard-relation edge dicts with relation_type fk_references /
            fk_referenced_by, is_hard=True, the isolation triple, and
            parse_evidence (source_format=ddl, extractor=ddl_fk, locator).
        """
        if not source_code or not source_code.strip():
            return []

        table_chunks = self._build_table_map(chunks)
        fk_rels = self._find_fk_relations(source_code)

        edges: list[dict[str, Any]] = []
        seen: set[tuple[int, int, str]] = set()
        for referencing, referenced in fk_rels:
            src_chunk = table_chunks.get(referencing)
            tgt_chunk = table_chunks.get(referenced)
            if not src_chunk or not tgt_chunk:
                # Referenced/referencing object not in this project -> only
                # produce hard edges for determinable relations (Edge Case).
                logger.debug(
                    "Skipping FK %s -> %s: missing table chunk "
                    "(src=%s, tgt=%s)",
                    referencing,
                    referenced,
                    src_chunk is not None,
                    tgt_chunk is not None,
                )
                continue
            if src_chunk["chunk_id"] == tgt_chunk["chunk_id"]:
                # No self-edges (table referencing itself).
                continue

            locator = "table:{r}.fk:{d}".format(r=referencing, d=referenced)

            ref_key = (
                src_chunk["chunk_id"],
                tgt_chunk["chunk_id"],
                "fk_references",
            )
            if ref_key not in seen:
                seen.add(ref_key)
                edges.append(
                    self._make_edge(src_chunk, tgt_chunk, "fk_references", scope, locator)
                )

            rby_key = (
                tgt_chunk["chunk_id"],
                src_chunk["chunk_id"],
                "fk_referenced_by",
            )
            if rby_key not in seen:
                seen.add(rby_key)
                edges.append(
                    self._make_edge(tgt_chunk, src_chunk, "fk_referenced_by", scope, locator)
                )
        return edges

    # ------------------------------------------------------------------
    # Source scanning
    # ------------------------------------------------------------------

    @staticmethod
    def _find_fk_relations(source: str) -> list[tuple[str, str]]:
        """Find (referencing_table, referenced_table) pairs in DDL source.

        Scans each CREATE TABLE body for table-level FOREIGN KEY ... REFERENCES
        clauses. The referencing table is the CREATE TABLE target; the
        referenced table is the identifier after REFERENCES.
        """
        relations: list[tuple[str, str]] = []
        for m in _CREATE_TABLE_RE.finditer(source):
            table_name = m.group(1).split(".")[-1]
            open_idx = m.end() - 1  # index of '('
            close_idx = _matching_paren(source, open_idx)
            body = source[open_idx + 1 : close_idx]
            for fk in _FK_RE.finditer(body):
                ref_table = fk.group(2).split(".")[-1]
                relations.append((table_name, ref_table))
        return relations

    # ------------------------------------------------------------------
    # Chunk resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _build_table_map(chunks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Map table name -> chunk for table-typed chunks.

        Accepts both the symbol_path/symbol_type convention (Java/Python/Go
        parsers) and the structure_path/chunk_type convention (DDL parser), so
        the extractor works with normalized chunks and raw parser output.
        """
        table_map: dict[str, dict[str, Any]] = {}
        for chunk in chunks:
            sym_type = chunk.get("symbol_type") or chunk.get("chunk_type") or ""
            if sym_type != "table":
                continue
            sym_path = (
                chunk.get("symbol_path") or chunk.get("structure_path") or ""
            )
            name = ""
            if sym_path.startswith("table:"):
                # 'table:users' -> 'users'; defensively strip any sub-path.
                name = sym_path[len("table:") :].split(".", 1)[0]
            if name:
                table_map.setdefault(name, chunk)
        return table_map

    # ------------------------------------------------------------------
    # Edge assembly
    # ------------------------------------------------------------------

    @staticmethod
    def _make_edge(
        source_chunk: dict[str, Any],
        target_chunk: dict[str, Any],
        relation_type: str,
        scope: GraphScope,
        locator: str,
    ) -> dict[str, Any]:
        """Assemble a hard-relation edge dict with isolation + evidence."""
        return {
            "source_chunk_id": source_chunk["chunk_id"],
            "target_chunk_id": target_chunk["chunk_id"],
            "relation_type": relation_type,
            "direction": "out",
            "is_hard": True,
            "version": 1,
            "knowledge_scope_id": scope.knowledge_scope_id,
            "project_id": scope.project_id,
            "index_version": scope.index_version,
            "parse_evidence": {
                "source_format": "ddl",
                "extractor": "ddl_fk",
                "locator": locator,
            },
        }
