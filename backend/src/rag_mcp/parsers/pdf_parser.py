from __future__ import annotations
import re
import math
from typing import Any

MAX_CHUNK_TOKENS = 1024

def _estimate_tokens(text):
    return max(1, math.ceil(len(text) / 4))

PAGE_MARKER_RE = re.compile(r'^=== PAGE (\d+) ===$')
HEADING_RE = re.compile(r'^(\d+(?:\.\d+)*)\s+(.+)$')

class PDFParser:
    def parse(self, text, filename=''):
        if not text or not text.strip():
            return []
        pages = self._split_pages(text)
        if not pages:
            return []
        chunks = []
        for page_num, page_text in pages:
            chunks.extend(self._parse_page(page_text, page_num, filename))
        return chunks

    def _split_pages(self, text):
        pages = []
        current_page = None
        current_lines = []
        for line in text.splitlines():
            m = PAGE_MARKER_RE.match(line.strip())
            if m:
                if current_page is not None:
                    pages.append((current_page, chr(10).join(current_lines)))
                current_page = int(m.group(1))
                current_lines = []
            else:
                current_lines.append(line)
        if current_page is not None:
            pages.append((current_page, chr(10).join(current_lines)))
        if not pages and text.strip():
            pages.append((1, text))
        return pages

    def _parse_page(self, page_text, page_num, filename):
        lines = page_text.splitlines()
        if not lines:
            return []
        chunks = []
        heading_path = []
        para_lines = []
        para_start = 0

        def flush(end_idx):
            nonlocal para_lines, para_start
            if not para_lines:
                return
            pt = chr(10).join(para_lines).strip()
            if not pt:
                para_lines = []
                return
            sp = self._section_path(page_num, heading_path)
            pp = self._parent_path(page_num, heading_path)
            tokens = _estimate_tokens(pt)
            if tokens > MAX_CHUNK_TOKENS:
                for sub in self._split_text(pt):
                    chunks.append(self._chunk(sub, sp, page_num, para_start, end_idx, pp, 'paragraph'))
            else:
                chunks.append(self._chunk(pt, sp, page_num, para_start, end_idx, pp, 'paragraph'))
            para_lines = []

        for i, line in enumerate(lines):
            s = line.strip()
            if not s:
                flush(i)
                para_start = i + 1
                continue
            m = HEADING_RE.match(s)
            if m and len(s) < 100:
                flush(i)
                sec_num = m.group(1)
                title = m.group(2).strip()
                level = sec_num.count('.') + 1
                while len(heading_path) >= level:
                    heading_path.pop()
                heading_path.append((level, sec_num, title))
                hp = self._heading_str(heading_path)
                pp = self._heading_str(heading_path[:-1]) if len(heading_path) > 1 else ''
                sp = 'page:' + str(page_num) + ' §' + hp
                parent_sp = ('page:' + str(page_num) + ' §' + pp) if pp else 'page:' + str(page_num)
                chunks.append(self._chunk(s, sp, page_num, i, i, parent_sp, 'heading'))
                para_start = i + 1
            else:
                para_lines.append(s)
        flush(len(lines))

        if not chunks and page_text.strip():
            sp = 'page:' + str(page_num)
            chunks.append(self._chunk(page_text.strip(), sp, page_num, 0, len(lines), '', 'paragraph'))
        return chunks

    def _chunk(self, content, section_path, page_num, start, end, parent_path, chunk_type):
        return {
            'content_text': content,
            'section_path': section_path,
            'start_line': page_num * 1000 + start,
            'end_line': page_num * 1000 + end,
            'parent_section_path': parent_path,
            'token_count': _estimate_tokens(content),
            'chunk_type': chunk_type,
        }

    def _section_path(self, page_num, heading_path):
        if heading_path:
            return 'page:' + str(page_num) + ' §' + self._heading_str(heading_path)
        return 'page:' + str(page_num)

    def _parent_path(self, page_num, heading_path):
        if len(heading_path) > 1:
            return 'page:' + str(page_num) + ' §' + self._heading_str(heading_path[:-1])
        return 'page:' + str(page_num)

    def _heading_str(self, heading_path):
        return ' '.join(num + ' ' + title for _, num, title in heading_path)

    def _split_text(self, text):
        sentences = re.split(r'(?<=[.!?])\s+', text)
        result = []
        current = []
        current_tokens = 0
        for s in sentences:
            t = _estimate_tokens(s)
            if current_tokens + t > MAX_CHUNK_TOKENS and current:
                result.append(' '.join(current))
                current = []
                current_tokens = 0
            current.append(s)
            current_tokens += t
        if current:
            result.append(' '.join(current))
        return result