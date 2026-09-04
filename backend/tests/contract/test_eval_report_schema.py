"""Contract test for eval-comparison-report schema (T021)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPECS_DIR = _REPO_ROOT / "specs" / "002-hybrid-retrieval-precision" / "contracts"


def _load_schema(name: str) -> dict:
    with open(_SPECS_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


def _inline_refs(schema: dict, common: dict) -> dict:
    schema = json.loads(json.dumps(schema))
    schema["definitions"] = common.get("definitions", {})
    # Also handle $defs already in the schema
    for k, v in common.get("definitions", {}).items():
        schema.setdefault("definitions", {})[k] = v
    schema_str = json.dumps(schema)
    schema_str = schema_str.replace(
        "./common.schema.json#/definitions/", "#/definitions/"
    )
    return json.loads(schema_str)


@pytest.fixture
def report_schema() -> dict:
    raw = _load_schema("eval-comparison-report.schema.json")
    common = _load_schema("common.schema.json")
    return _inline_refs(raw, common)


def _validate(instance, schema):
    jsonschema.validate(instance, schema)


class TestValidReport:
    def test_complete_report(self, report_schema):
        report = {
            "report_type": "hybrid_retrieval_comparison",
            "generated_at": "2026-08-27T12:00:00Z",
            "config": {
                "embedding_model": "BAAI/bge-m3",
                "reranker_model": "BAAI/bge-reranker-v2-m3",
                "hybrid_collection": "chunks_hybrid_bge-m3_v1",
                "fusion_algorithm": "rrf",
                "rrf_k": 60,
                "rerank_budget": 20,
                "dataset_path": "eval/eval_dataset.json",
                "num_queries": 15,
            },
            "baseline_metrics": {
                "recall_at_k": {"mean": 1.0, "min": 1.0, "max": 1.0},
                "mrr": {"mean": 0.9091, "min": 0.5, "max": 1.0},
                "ndcg_at_k": {"mean": 0.9329, "min": 0.6309, "max": 1.0},
                "latency_ms": {"p50": 138.45, "p95": 185.15, "mean": 150.0},
            },
            "hybrid_metrics": {
                "recall_at_k": {"mean": 1.0, "min": 1.0, "max": 1.0},
                "mrr": {"mean": 0.9545, "min": 1.0, "max": 1.0},
                "ndcg_at_k": {"mean": 0.9665, "min": 1.0, "max": 1.0},
                "latency_ms": {"p50": 500.0, "p95": 2000.0, "mean": 800.0},
            },
            "deltas": {
                "mrr_mean_delta": 0.0454,
                "ndcg_mean_delta": 0.0336,
                "recall_mean_delta": 0.0,
                "latency_p50_delta_ms": 361.55,
                "latency_p95_delta_ms": 1814.85,
            },
            "hard_constraints": {
                "cross_project_leakage_events": 0,
                "schema_validity_rate": 1.0,
                "source_locatability_rate": 1.0,
                "all_passed": True,
            },
            "per_query_comparison": [
                {
                    "query_index": 0,
                    "query": "test query",
                    "project_scope": "100",
                    "expected_evidence_ids": ["300"],
                    "baseline_rank": 2,
                    "hybrid_rank": 1,
                    "baseline_dense_score": 0.6133,
                    "hybrid_dense_score": 0.6133,
                    "hybrid_sparse_score": 12.5,
                    "hybrid_fused_score": 0.0325,
                    "hybrid_rerank_score": 0.85,
                    "rank_improved": True,
                },
            ],
            "reproducibility": {
                "non_latency_reproducible": True,
                "tolerance": 0.01,
                "checks": [
                    {
                        "metric": "recall_at_k.mean",
                        "run_1": 1.0, "run_2": 1.0,
                        "relative_delta": 0.0, "tolerance": 0.01,
                        "passed": True,
                    },
                ],
            },
            "enters_default_path": True,
        }
        _validate(report, report_schema)


class TestRequiredFields:
    @pytest.mark.parametrize("missing", [
        "report_type", "generated_at", "config", "baseline_metrics",
        "hybrid_metrics", "deltas", "hard_constraints",
        "per_query_comparison", "enters_default_path",
    ])
    def test_missing_required_rejected(self, report_schema, missing):
        report = {
            "report_type": "hybrid_retrieval_comparison",
            "generated_at": "2026-08-27T12:00:00Z",
            "config": {"embedding_model": "m", "reranker_model": "r",
                "hybrid_collection": "c", "fusion_algorithm": "rrf",
                "dataset_path": "p", "num_queries": 1},
            "baseline_metrics": {
                "recall_at_k": {"mean": 1, "min": 1, "max": 1},
                "mrr": {"mean": 1, "min": 1, "max": 1},
                "ndcg_at_k": {"mean": 1, "min": 1, "max": 1},
                "latency_ms": {"p50": 1, "p95": 1, "mean": 1},
            },
            "hybrid_metrics": {
                "recall_at_k": {"mean": 1, "min": 1, "max": 1},
                "mrr": {"mean": 1, "min": 1, "max": 1},
                "ndcg_at_k": {"mean": 1, "min": 1, "max": 1},
                "latency_ms": {"p50": 1, "p95": 1, "mean": 1},
            },
            "deltas": {"mrr_mean_delta": 0, "ndcg_mean_delta": 0, "recall_mean_delta": 0},
            "hard_constraints": {"cross_project_leakage_events": 0,
                "schema_validity_rate": 1.0, "source_locatability_rate": 1.0,
                "all_passed": True},
            "per_query_comparison": [],
            "enters_default_path": True,
        }
        del report[missing]
        with pytest.raises(jsonschema.ValidationError):
            _validate(report, report_schema)


class TestHardConstraints:
    def test_all_passed_false(self, report_schema):
        """all_passed can be False (not all hard constraints met)."""
        report = {
            "report_type": "hybrid_retrieval_comparison",
            "generated_at": "2026-08-27T12:00:00Z",
            "config": {"embedding_model": "m", "reranker_model": "r",
                "hybrid_collection": "c", "fusion_algorithm": "rrf",
                "dataset_path": "p", "num_queries": 1},
            "baseline_metrics": {
                "recall_at_k": {"mean": 1, "min": 1, "max": 1},
                "mrr": {"mean": 1, "min": 1, "max": 1},
                "ndcg_at_k": {"mean": 1, "min": 1, "max": 1},
                "latency_ms": {"p50": 1, "p95": 1, "mean": 1},
            },
            "hybrid_metrics": {
                "recall_at_k": {"mean": 1, "min": 1, "max": 1},
                "mrr": {"mean": 1, "min": 1, "max": 1},
                "ndcg_at_k": {"mean": 1, "min": 1, "max": 1},
                "latency_ms": {"p50": 1, "p95": 1, "mean": 1},
            },
            "deltas": {"mrr_mean_delta": 0, "ndcg_mean_delta": 0, "recall_mean_delta": 0},
            "hard_constraints": {"cross_project_leakage_events": 1,
                "schema_validity_rate": 0.9, "source_locatability_rate": 1.0,
                "all_passed": False},
            "per_query_comparison": [],
            "enters_default_path": False,
        }
        _validate(report, report_schema)


class TestOriginalSubsetGate:
    """original_subset_gate (002 fix) is optional; when present it must be
    well-formed so the strict original-11 gate stays auditable in reports."""

    def _report_with_gate(self, gate: dict) -> dict:
        return {
            "report_type": "hybrid_retrieval_comparison",
            "generated_at": "2026-09-10T00:00:00Z",
            "config": {"embedding_model": "m", "reranker_model": "r",
                "hybrid_collection": "c", "fusion_algorithm": "rrf",
                "dataset_path": "p", "num_queries": 1},
            "baseline_metrics": {
                "recall_at_k": {"mean": 1, "min": 1, "max": 1},
                "mrr": {"mean": 1, "min": 1, "max": 1},
                "ndcg_at_k": {"mean": 1, "min": 1, "max": 1},
                "latency_ms": {"p50": 1, "p95": 1, "mean": 1},
            },
            "hybrid_metrics": {
                "recall_at_k": {"mean": 1, "min": 1, "max": 1},
                "mrr": {"mean": 1, "min": 1, "max": 1},
                "ndcg_at_k": {"mean": 1, "min": 1, "max": 1},
                "latency_ms": {"p50": 1, "p95": 1, "mean": 1},
            },
            "deltas": {"mrr_mean_delta": 0, "ndcg_mean_delta": 0, "recall_mean_delta": 0},
            "hard_constraints": {"cross_project_leakage_events": 0,
                "schema_validity_rate": 1.0, "source_locatability_rate": 1.0,
                "all_passed": True},
            "per_query_comparison": [],
            "enters_default_path": False,
            "original_subset_gate": gate,
        }

    def test_valid_gate_accepted(self, report_schema):
        gate = {
            "num_queries": 11,
            "baseline_mrr_mean": 0.954545,
            "hybrid_mrr_mean": 1.0,
            "baseline_ndcg_mean": 0.966386,
            "hybrid_ndcg_mean": 1.0,
            "baseline_recall_mean": 1.0,
            "hybrid_recall_mean": 1.0,
            "mrr_threshold": 0.95,
            "ndcg_threshold": 0.96,
            "mrr_positive_delta": True,
            "ndcg_positive_delta": True,
            "recall_non_decreasing": True,
        }
        _validate(self._report_with_gate(gate), report_schema)

    def test_malformed_gate_rejected(self, report_schema):
        gate = {
            "num_queries": "11",  # wrong type: must be integer
            "baseline_mrr_mean": 0.954545,
            "hybrid_mrr_mean": 1.0,
            "baseline_ndcg_mean": 0.966386,
            "hybrid_ndcg_mean": 1.0,
            "baseline_recall_mean": 1.0,
            "hybrid_recall_mean": 1.0,
            "mrr_positive_delta": True,
            "ndcg_positive_delta": True,
            "recall_non_decreasing": True,
        }
        with pytest.raises(jsonschema.ValidationError):
            _validate(self._report_with_gate(gate), report_schema)
