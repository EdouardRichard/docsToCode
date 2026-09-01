"""Runtime graph-expansion-trace recorder (T041).

Records per-request graph-enhanced retrieval trace conforming to
graph-expansion-trace.schema.json: subpath_timings, graph_candidates
(with nullable evidence_id), fused_candidates, failed_paths,
evidence_ref_ids. Backfills evidence_id when candidates survive as
evidence (DM-1 bridge to graph_expansion_path). FR-026, blueprint sec 13.
"""
from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)


class GraphTraceRecorder:
    def __init__(self, request_id, knowledge_scope_ids, guardrails):
        self._request_id = request_id
        self._knowledge_scope_ids = list(knowledge_scope_ids)
        self._guardrails = dict(guardrails)
        self._timings = None
        self._graph_candidates = []
        self._fused_candidates = []
        self._failed_paths = []
        self._completion_status = 'complete'
        self._evidence_ref_ids = []

    def record_timings(self, timings):
        self._timings = dict(timings)

    def record_graph_candidates(self, candidates):
        """Record graph candidates, normalising IDs to schema string form.

        graph-expansion-trace.schema.json requires ChunkId/KnowledgeScopeId/
        EdgeId as numeric strings; PG returns bigints, so normalise here at
        the single recording point.
        """
        normalised = []
        for cand in candidates:
            c = dict(cand)
            for key in ("chunk_id", "knowledge_scope_id", "start_chunk_id"):
                if c.get(key) is not None:
                    c[key] = str(c[key])
            path = []
            for step in c.get("edge_path") or []:
                s = dict(step)
                if s.get("edge_id") is not None:
                    s["edge_id"] = str(s["edge_id"])
                path.append(s)
            c["edge_path"] = path
            normalised.append(c)
        self._graph_candidates = normalised

    def record_fused_candidates(self, candidates):
        self._fused_candidates = [dict(c) for c in candidates]

    def record_failed_path(self, path):
        self._failed_paths.append(path)

    def set_completion_status(self, status):
        self._completion_status = status

    def set_evidence_ref_ids(self, ids):
        self._evidence_ref_ids = list(ids)

    def backfill_evidence_ids(self, evidence_map):
        for cand in self._graph_candidates:
            cid = str(cand.get('chunk_id', ''))
            if cid in evidence_map:
                cand['evidence_id'] = str(evidence_map[cid])

    def to_trace_dict(self):
        trace = {
            'request_id': self._request_id,
            'retrieval_mode': 'hybrid',
            'knowledge_scope_ids': self._knowledge_scope_ids,
            'completion_status': self._completion_status,
            'guardrails': self._guardrails,
            'fused_candidates': self._fused_candidates,
            'evidence_ref_ids': self._evidence_ref_ids,
        }
        if self._timings is not None:
            trace['subpath_timings'] = self._timings
        trace['graph_candidates'] = self._graph_candidates
        if self._completion_status == 'partial':
            trace['failed_paths'] = self._failed_paths if self._failed_paths else ['unknown']
        return trace

    async def persist_paths(self, session, request_id_int, scope):
        import json

        from sqlalchemy import text
        count = 0
        for cand in self._graph_candidates:
            eid = cand.get('evidence_id')
            if not eid:
                continue
            try:
                await session.execute(text(
                    'INSERT INTO graph_expansion_path (request_id, evidence_id, '
                    'chunk_id, start_chunk_id, edge_path, hop_count, '
                    'structure_weight, graph_rank) '
                    'VALUES (:rid, :eid, :cid, :scid, CAST(:ep AS jsonb), '
                    ':hc, :sw, :gr) '
                    'ON CONFLICT DO NOTHING'
                ), {
                    'rid': request_id_int,
                    'eid': int(eid),
                    'cid': int(cand['chunk_id']),
                    'scid': int(cand['start_chunk_id']),
                    'ep': json.dumps(cand.get('edge_path', [])),
                    'hc': cand.get('hop_count', 1),
                    'sw': float(cand.get('structure_weight', 1.0)),
                    'gr': cand.get('graph_rank', 0),
                })
                count += 1
            except Exception as exc:
                logger.warning('Failed to persist graph_expansion_path: %s', exc)
        return count
