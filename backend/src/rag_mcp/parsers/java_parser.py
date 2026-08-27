"""Java symbol-aware parser using tree-sitter.

Parses Java source code into symbol-level chunks (class, interface, method,
field) with fully qualified symbol paths.  Falls back to line-level chunking
when tree-sitter cannot produce a valid AST (e.g. syntax errors).
"""

from __future__ import annotations

import logging
import math
from typing import Any

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser, Node

logger = logging.getLogger(__name__)

JAVA_LANGUAGE = Language(tsjava.language())

# Approximate token estimation: ~4 chars per token for Java source
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """Rough token count estimate based on character length."""
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


def _node_text(node: Node, source: bytes) -> str:
    """Extract the source text covered by *node*."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_child_by_type(node: Node, type_name: str) -> Node | None:
    """Return the first direct child of *node* matching *type_name*."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _extract_package(source: bytes, root: Node) -> str:
    """Extract the package name from the AST root, or '' if absent."""
    pkg_decl = _find_child_by_type(root, "package_declaration")
    if pkg_decl is None:
        return ""
    scoped = _find_child_by_type(pkg_decl, "scoped_identifier")
    if scoped is None:
        return ""
    return _node_text(scoped, source)


def _extract_class_name(node: Node, source: bytes) -> str:
    """Return the simple name of a class or interface declaration."""
    ident = _find_child_by_type(node, "identifier")
    if ident is not None:
        return _node_text(ident, source)
    return "<anonymous>"


def _extract_method_signature(node: Node, source: bytes) -> str:
    """Build a human-readable method signature including parameters."""
    name_node = _find_child_by_type(node, "identifier")
    name = _node_text(name_node, source) if name_node else "<unknown>"

    params_node = _find_child_by_type(node, "formal_parameters")
    if params_node is not None:
        params_text = _node_text(params_node, source)
    else:
        params_text = "()"

    # Return type: look for the type node before the identifier
    return_type = ""
    for child in node.children:
        if child.type == "identifier":
            break
        if child.type in (
            "type_identifier",
            "generic_type",
            "integral_type",
            "floating_point_type",
            "boolean_type",
            "void_type",
            "array_type",
            "scoped_type_identifier",
        ):
            return_type = _node_text(child, source)

    if return_type:
        return f"{return_type} {name}{params_text}"
    return f"{name}{params_text}"


def _extract_field_name(node: Node, source: bytes) -> str:
    """Extract the field name from a field_declaration node."""
    declarator = _find_child_by_type(node, "variable_declarator")
    if declarator is not None:
        ident = _find_child_by_type(declarator, "identifier")
        if ident is not None:
            return _node_text(ident, source)
    # Fallback: look for any identifier that isn't part of the type
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
    return "<unknown-field>"


class JavaParser:
    """Symbol-aware Java source parser.

    Produces a list of chunk dicts suitable for downstream embedding and
    indexing.  Each chunk carries symbol metadata (path, type, lines) and
    an approximate token count.
    """

    def parse(
        self,
        source_code: str,
        filename: str = "",
        source_id: int = 0,
    ) -> list[dict[str, Any]]:
        """Parse Java source and return symbol-level chunks.

        Parameters
        ----------
        source_code:
            Raw Java source text.
        filename:
            Optional filename for diagnostics.
        source_id:
            Knowledge-source identifier (stored but not used by the parser).

        Returns
        -------
        list[dict]
            Each dict contains: content_text, symbol_path, symbol_type,
            start_line, end_line, parent_symbol_path, token_count, chunk_type.
        """
        if not source_code or not source_code.strip():
            return []

        source_bytes = source_code.encode("utf-8")
        parser = Parser(JAVA_LANGUAGE)
        tree = parser.parse(source_bytes)
        root = tree.root_node

        # Check for ERROR nodes at top level indicating parse failure
        has_errors = any(child.type == "ERROR" for child in root.children)
        if has_errors or root.child_count == 0:
            logger.warning(
                "tree-sitter parse produced errors for %s; falling back to line-level chunking",
                filename or "<unnamed>",
            )
            return self._fallback_line_chunks(source_code, source_id)

        package = _extract_package(source_bytes, root)
        chunks: list[dict[str, Any]] = []

        for child in root.children:
            if child.type == "class_declaration":
                chunks.extend(
                    self._process_class(child, source_bytes, package, "")
                )
            elif child.type == "interface_declaration":
                chunks.extend(
                    self._process_interface(child, source_bytes, package, "")
                )

        return chunks

    # ------------------------------------------------------------------
    # Symbol extraction helpers
    # ------------------------------------------------------------------

    def _process_class(
        self,
        node: Node,
        source: bytes,
        package: str,
        parent_path: str,
    ) -> list[dict[str, Any]]:
        """Extract chunks from a class_declaration node."""
        class_name = _extract_class_name(node, source)
        qualified = f"{package}.{class_name}" if package else class_name
        chunks: list[dict[str, Any]] = []

        # Class-level chunk
        class_text = _node_text(node, source)
        chunks.append({
            "content_text": class_text,
            "symbol_path": qualified,
            "symbol_type": "class",
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "parent_symbol_path": parent_path or "",
            "token_count": _estimate_tokens(class_text),
            "chunk_type": "symbol",
        })

        # Process body members
        body = _find_child_by_type(node, "class_body")
        if body is not None:
            chunks.extend(
                self._process_body(body, source, qualified)
            )

        return chunks

    def _process_interface(
        self,
        node: Node,
        source: bytes,
        package: str,
        parent_path: str,
    ) -> list[dict[str, Any]]:
        """Extract chunks from an interface_declaration node."""
        iface_name = _extract_class_name(node, source)
        qualified = f"{package}.{iface_name}" if package else iface_name
        chunks: list[dict[str, Any]] = []

        iface_text = _node_text(node, source)
        chunks.append({
            "content_text": iface_text,
            "symbol_path": qualified,
            "symbol_type": "interface",
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "parent_symbol_path": parent_path or "",
            "token_count": _estimate_tokens(iface_text),
            "chunk_type": "symbol",
        })

        body = _find_child_by_type(node, "interface_body")
        if body is not None:
            chunks.extend(
                self._process_body(body, source, qualified)
            )

        return chunks

    def _process_body(
        self,
        body_node: Node,
        source: bytes,
        parent_path: str,
    ) -> list[dict[str, Any]]:
        """Extract method and field chunks from a class/interface body."""
        chunks: list[dict[str, Any]] = []

        for member in body_node.children:
            if member.type == "method_declaration":
                sig = _extract_method_signature(member, source)
                method_name = _find_child_by_type(member, "identifier")
                name = _node_text(method_name, source) if method_name else "<unknown>"
                symbol_path = f"{parent_path}#{name}"
                method_text = _node_text(member, source)

                chunks.append({
                    "content_text": method_text,
                    "symbol_path": symbol_path,
                    "symbol_type": "method",
                    "start_line": member.start_point[0] + 1,
                    "end_line": member.end_point[0] + 1,
                    "parent_symbol_path": parent_path,
                    "token_count": _estimate_tokens(method_text),
                    "chunk_type": "symbol",
                })

            elif member.type == "field_declaration":
                field_name = _extract_field_name(member, source)
                symbol_path = f"{parent_path}#{field_name}"
                field_text = _node_text(member, source)

                chunks.append({
                    "content_text": field_text,
                    "symbol_path": symbol_path,
                    "symbol_type": "field",
                    "start_line": member.start_point[0] + 1,
                    "end_line": member.end_point[0] + 1,
                    "parent_symbol_path": parent_path,
                    "token_count": _estimate_tokens(field_text),
                    "chunk_type": "symbol",
                })

            elif member.type == "class_declaration":
                # Nested class
                chunks.extend(
                    self._process_class(member, source, "", parent_path)
                )
            elif member.type == "interface_declaration":
                # Nested interface
                chunks.extend(
                    self._process_interface(member, source, "", parent_path)
                )

        return chunks

    # ------------------------------------------------------------------
    # Graceful degradation
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_line_chunks(
        source_code: str,
        source_id: int,
        chunk_lines: int = 50,
    ) -> list[dict[str, Any]]:
        """Produce simple line-based chunks when AST parsing fails."""
        lines = source_code.splitlines()
        if not lines:
            return []

        chunks: list[dict[str, Any]] = []
        for i in range(0, len(lines), chunk_lines):
            block = "\n".join(lines[i : i + chunk_lines])
            start = i + 1
            end = min(i + chunk_lines, len(lines))
            chunks.append({
                "content_text": block,
                "symbol_path": "",
                "symbol_type": "unknown",
                "start_line": start,
                "end_line": end,
                "parent_symbol_path": "",
                "token_count": _estimate_tokens(block),
                "chunk_type": "symbol",
            })

        return chunks
