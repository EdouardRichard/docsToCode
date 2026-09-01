from __future__ import annotations
import logging
import math
from typing import Any
import tree_sitter_go as tsgo
from tree_sitter import Language, Parser, Node

logger = logging.getLogger(__name__)
GO_LANGUAGE = Language(tsgo.language())
MAX_CHUNK_TOKENS = 1024

def _estimate_tokens(text):
    return max(1, math.ceil(len(text) / 4))

def _node_text(node, source):
    return source[node.start_byte:node.end_byte].decode('utf-8', errors='replace')

def _find_child(node, type_name):
    for c in node.children:
        if c.type == type_name:
            return c
    return None

def _find_children(node, type_name):
    return [c for c in node.children if c.type == type_name]

class GoParser:
    def parse(self, source_code, filename='', source_id=0):
        if not source_code or not source_code.strip():
            return []
        source_bytes = source_code.encode('utf-8') if isinstance(source_code, str) else source_code
        parser = Parser(GO_LANGUAGE)
        tree = parser.parse(source_bytes)
        root = tree.root_node
        if root.has_error or root.child_count == 0:
            has_error_node = any(c.type == 'ERROR' for c in root.children)
            if has_error_node:
                raise ValueError(
                    f'Go parse error in {filename or "<unnamed>"}: syntax errors detected, '
                    f'cannot produce reliable symbol boundaries (FR-017)'
                )
        package = self._extract_package(root, source_bytes)
        chunks = []
        for child in root.children:
            if child.type == 'function_declaration':
                chunks.extend(self._process_function(child, source_bytes, package))
            elif child.type == 'method_declaration':
                chunks.extend(self._process_method(child, source_bytes, package))
            elif child.type == 'type_declaration':
                chunks.extend(self._process_type(child, source_bytes, package))
        return chunks

    def _extract_package(self, root, source):
        pkg_clause = _find_child(root, 'package_clause')
        if pkg_clause is None:
            return ''
        pkg_id = _find_child(pkg_clause, 'package_identifier')
        if pkg_id is not None:
            return _node_text(pkg_id, source)
        return ''

    def _process_function(self, node, source, package):
        name_node = _find_child(node, 'identifier')
        if name_node is None:
            return []
        name = _node_text(name_node, source)
        path = f'{package}.{name}' if package else name
        text = _node_text(node, source)
        tokens = _estimate_tokens(text)
        return [self._chunk(text, path, 'function', node, source, '', tokens)]

    def _process_method(self, node, source, package):
        name_node = _find_child(node, 'field_identifier')
        if name_node is None:
            name_node = _find_child(node, 'identifier')
        if name_node is None:
            return []
        name = _node_text(name_node, source)
        receiver_type = self._extract_receiver_type(node, source)
        path = f'{package}.{receiver_type}#{name}' if package else f'{receiver_type}#{name}'
        parent = f'{package}.{receiver_type}' if package else receiver_type
        text = _node_text(node, source)
        tokens = _estimate_tokens(text)
        return [self._chunk(text, path, 'method', node, source, parent, tokens)]

    def _extract_receiver_type(self, node, source):
        params = _find_children(node, 'parameter_list')
        if not params:
            return 'Unknown'
        first_param_list = params[0]
        for child in first_param_list.children:
            if child.type == 'parameter_declaration':
                for c in child.children:
                    if c.type == 'pointer_type':
                        text = _node_text(c, source)
                        return text.lstrip('*')
                    if c.type == 'type_identifier':
                        return _node_text(c, source)
                    if c.type == 'qualified_type_identifier':
                        return _node_text(c, source).split('.')[-1]
        return 'Unknown'

    def _process_type(self, node, source, package):
        specs = _find_children(node, 'type_spec')
        chunks = []
        for spec in specs:
            name_node = _find_child(spec, 'type_identifier')
            if name_node is None:
                continue
            name = _node_text(name_node, source)
            path = f'{package}.{name}' if package else name
            if _find_child(spec, 'interface_type') is not None:
                ct = 'interface'
            else:
                ct = 'type'
            text = _node_text(spec, source)
            tokens = _estimate_tokens(text)
            chunks.append(self._chunk(text, path, ct, spec, source, '', tokens))
        return chunks

    def _chunk(self, content, symbol_path, symbol_type, node, source, parent_path, tokens):
        if tokens > MAX_CHUNK_TOKENS:
            return {
                'content_text': content[:MAX_CHUNK_TOKENS*4],
                'symbol_path': symbol_path,
                'symbol_type': symbol_type,
                'start_line': node.start_point[0] + 1,
                'end_line': node.end_point[0] + 1,
                'parent_symbol_path': parent_path,
                'token_count': MAX_CHUNK_TOKENS,
                'chunk_type': symbol_type,
            }
        return {
            'content_text': content,
            'symbol_path': symbol_path,
            'symbol_type': symbol_type,
            'start_line': node.start_point[0] + 1,
            'end_line': node.end_point[0] + 1,
            'parent_symbol_path': parent_path,
            'token_count': tokens,
            'chunk_type': symbol_type,
        }