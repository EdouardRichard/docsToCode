"""Java call-graph hard-relation extractor (T019).

Deterministically extracts calls/called_by edges from Java source using
tree-sitter AST analysis (blueprint sec 10.1, research sec 7, FR-001/FR-002).
Reuses the tree-sitter Java language from parsers/java_parser.py.
AST failure reports degradation, does not fabricate edges (Constitution III).
"""
from __future__ import annotations
import logging
from typing import Any
import tree_sitter_java as tsjava
from tree_sitter import Language, Parser, Node
from rag_mcp.graph.store.base import GraphScope

logger = logging.getLogger(__name__)
JAVA_LANGUAGE = Language(tsjava.language())


def _node_text(node, source):
    return source[node.start_byte:node.end_byte].decode('utf-8', errors='replace')


def _find_child(node, type_name):
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_all(node, type_name, results=None):
    if results is None:
        results = []
    if node.type == type_name:
        results.append(node)
    for child in node.children:
        _find_all(child, type_name, results)
    return results


def _extract_method_name(invocation, source):
    ident = _find_child(invocation, 'identifier')
    if ident is not None:
        return _node_text(ident, source)
    return None


def _find_enclosing_method(node):
    parent = node.parent
    while parent is not None:
        if parent.type == 'method_declaration':
            return parent
        parent = parent.parent
    return None


def _extract_method_name_from_decl(decl, source):
    ident = _find_child(decl, 'identifier')
    if ident is not None:
        return _node_text(ident, source)
    return None


class JavaCallGraphExtractor:
    def extract(self, source_code, chunks, scope):
        if not source_code or not source_code.strip():
            return []
        source_bytes = source_code.encode('utf-8')
        parser = Parser(JAVA_LANGUAGE)
        tree = parser.parse(source_bytes)
        root = tree.root_node
        has_errors = any(child.type == 'ERROR' for child in root.children)
        if has_errors or root.child_count == 0:
            logger.warning('Java AST parse failed; no edges fabricated')
            return []
        method_decls = _find_all(root, 'method_declaration')
        method_name_to_chunk = {}
        for decl in method_decls:
            name = _extract_method_name_from_decl(decl, source_bytes)
            if name:
                chunk = self._find_chunk_for_decl(decl, chunks, source_bytes)
                if chunk:
                    method_name_to_chunk.setdefault(name, chunk)
        invocations = _find_all(root, 'method_invocation')
        edges = []
        seen = set()
        for inv in invocations:
            inv_name = _extract_method_name(inv, source_bytes)
            if not inv_name:
                continue
            target_chunk = method_name_to_chunk.get(inv_name)
            if not target_chunk:
                continue
            enclosing = _find_enclosing_method(inv)
            if not enclosing:
                continue
            source_chunk = self._find_chunk_for_decl(enclosing, chunks, source_bytes)
            if not source_chunk or source_chunk['chunk_id'] == target_chunk['chunk_id']:
                continue
            calls_key = (source_chunk['chunk_id'], target_chunk['chunk_id'], 'calls')
            if calls_key not in seen:
                seen.add(calls_key)
                edges.append(self._make_edge(source_chunk, target_chunk, 'calls', scope, enclosing, inv, source_bytes))
            cb_key = (target_chunk['chunk_id'], source_chunk['chunk_id'], 'called_by')
            if cb_key not in seen:
                seen.add(cb_key)
                edges.append(self._make_edge(target_chunk, source_chunk, 'called_by', scope, enclosing, inv, source_bytes))
        return edges

    def _find_chunk_for_decl(self, decl, chunks, source):
        decl_name = _extract_method_name_from_decl(decl, source)
        for chunk in chunks:
            if chunk.get('symbol_type') != 'method':
                continue
            sym_path = chunk.get('symbol_path', '')
            if '#' in sym_path:
                chunk_method_name = sym_path.rsplit('#', 1)[-1]
            else:
                chunk_method_name = sym_path.rsplit('.', 1)[-1] if '.' in sym_path else sym_path
            if chunk_method_name == decl_name:
                return chunk
        return None

    def _make_edge(self, source_chunk, target_chunk, relation_type, scope, caller_decl, invocation, source):
        inv_line = invocation.start_point[0] + 1
        caller_name = _extract_method_name_from_decl(caller_decl, source) or 'unknown'
        return {
            'source_chunk_id': source_chunk['chunk_id'],
            'target_chunk_id': target_chunk['chunk_id'],
            'relation_type': relation_type,
            'direction': 'out',
            'is_hard': True,
            'version': 1,
            'knowledge_scope_id': scope.knowledge_scope_id,
            'project_id': scope.project_id,
            'index_version': scope.index_version,
            'parse_evidence': {
                'source_format': 'java',
                'extractor': 'java_call_graph',
                'locator': 'method:{}:line:{}'.format(caller_name, inv_line),
            },
        }
