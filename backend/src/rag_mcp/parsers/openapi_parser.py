from __future__ import annotations
import json
import logging
import math
import re
from typing import Any

logger = logging.getLogger(__name__)
MAX_CHUNK_TOKENS = 1024
HTTP_METHODS = {'get', 'post', 'put', 'delete', 'patch', 'head', 'options'}

def _estimate_tokens(text):
    return max(1, math.ceil(len(text) / 4))

def _load_spec(text, filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    try:
        if ext == 'json':
            return json.loads(text)
        else:
            import yaml
            return yaml.safe_load(text)
    except Exception as exc:
        raise ValueError(f'Failed to parse {filename} as {ext.upper()}: {exc}') from exc

def _extract_ref_name(ref_value):
    if not isinstance(ref_value, str):
        return None
    if '#/components/schemas/' in ref_value:
        name = ref_value.split('#/components/schemas/')[-1]
        return f'schema:components.schemas.{name}'
    if '#/definitions/' in ref_value:
        name = ref_value.split('#/definitions/')[-1]
        return f'schema:definitions.{name}'
    return None

def _find_refs(obj, found=None):
    if found is None:
        found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == '$ref' and isinstance(v, str):
                ref_name = _extract_ref_name(v)
                if ref_name:
                    found.append(ref_name)
            else:
                _find_refs(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _find_refs(item, found)
    return found

def _find_main_ref(operation):
    refs = _find_refs(operation)
    if not refs:
        return ''
    body = operation.get('requestBody', {})
    body_refs = _find_refs(body)
    if body_refs:
        return body_refs[0]
    return refs[0]

def _obj_to_text(obj, indent=0):
    return json.dumps(obj, indent=2, ensure_ascii=False)

class OpenAPIParser:
    def parse(self, text, filename=''):
        if not text or not text.strip():
            return []
        spec = _load_spec(text, filename)
        if not isinstance(spec, dict):
            raise ValueError(f'{filename} is valid but not an OpenAPI spec (not an object)')
        is_openapi3 = 'openapi' in spec
        is_swagger2 = 'swagger' in spec
        if not is_openapi3 and not is_swagger2:
            raise ValueError(
                f'{filename} is valid JSON/YAML but not an OpenAPI/Swagger spec '
                f'(missing openapi or swagger version field)')
        chunks = []
        if is_openapi3:
            schemas_loc = 'components.schemas'
            schemas = spec.get('components', {}).get('schemas', {})
        else:
            schemas_loc = 'definitions'
            schemas = spec.get('definitions', {})
        for name, schema_def in schemas.items():
            sp = f'schema:{schemas_loc}.{name}'
            content = _obj_to_text(schema_def)
            tokens = _estimate_tokens(content)
            chunks.append({
                'content_text': content,
                'structure_path': sp,
                'start_line': 0,
                'end_line': 0,
                'parent_structure_path': '',
                'token_count': tokens,
                'chunk_type': 'schema',
            })
        paths = spec.get('paths', {})
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, operation in methods.items():
                if method.lower() not in HTTP_METHODS:
                    continue
                sp = f'{method.upper()} {path}'
                parent = _find_main_ref(operation)
                content = _obj_to_text(operation)
                tokens = _estimate_tokens(content)
                chunks.append({
                    'content_text': content,
                    'structure_path': sp,
                    'start_line': 0,
                    'end_line': 0,
                    'parent_structure_path': parent,
                    'token_count': tokens,
                    'chunk_type': 'endpoint',
                })
        return chunks