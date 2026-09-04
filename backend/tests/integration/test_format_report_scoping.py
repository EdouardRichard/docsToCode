"""T055: 002 acceptance runs (--limit 18) must never overwrite the 003
per-format artifact (eval/format_expansion_report.json).

The 006 convergence regression re-ran run_comparison.py with --limit 18 for
the 002 fixed scope; the side-effect format report wiped the new-format
coverage. A limited (002-scoped) run now writes the format report to a
distinct 002-scoped path, and only unlimited runs (full dataset, the 003
scope) write the declared 003 artifact.
"""
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVAL_DIR = _REPO_ROOT / "eval"
sys.path.insert(0, str(_EVAL_DIR))

import run_comparison  # noqa: E402


class TestFormatReportPathScoping:
    def test_limited_run_does_not_target_003_artifact(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # A 002-scoped (limited) run must not write the 003 artifact path.
        path = run_comparison._format_report_target(
            format_report_output=None, limit=18,
        )
        assert path.name != "format_expansion_report.json"
        assert "002" in path.name or "limited" in path.name

    def test_unlimited_run_targets_003_artifact(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = run_comparison._format_report_target(
            format_report_output=None, limit=None,
        )
        assert path.name == "format_expansion_report.json"

    def test_explicit_output_always_wins(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        explicit = tmp_path / "custom_format_report.json"
        path = run_comparison._format_report_target(
            format_report_output=str(explicit), limit=18,
        )
        assert str(path) == str(explicit)

    def test_limited_run_skips_writing_003_artifact(self, tmp_path, monkeypatch):
        """generate_format_expansion_report with a limited dataset must
        leave an existing 003 artifact untouched (no overwrite, no
        truncation to the limited scope)."""
        artifact = tmp_path / "format_expansion_report.json"
        artifact.write_text(json.dumps({"per_format": {"openapi": {"num_queries": 2}}}), encoding="utf-8")
        monkeypatch.setattr(run_comparison, "_REPO_ROOT", tmp_path)

        dataset = [{"query": "q", "format": "java"}]
        dense_per_query = [{"expected_evidence_ids": [], "retrieved_evidence_ids": [], "latency_ms": 1.0}]
        hybrid_per_query = [{"expected_evidence_ids": [], "retrieved_evidence_ids": [], "latency_ms": 1.0}]
        metrics = {
            "num_queries": 1, "top_k": 5,
            "recall_at_k": {"mean": 0.0, "min": 0.0, "max": 0.0},
            "mrr": {"mean": 0.0, "min": 0.0, "max": 0.0},
            "ndcg_at_k": {"mean": 0.0, "min": 0.0, "max": 0.0},
            "latency_ms": {"p50": 0.0, "p95": 0.0},
        }
        report = run_comparison.generate_format_expansion_report(
            dataset=dataset,
            dense_per_query=dense_per_query,
            hybrid_per_query=hybrid_per_query,
            dense_metrics=dict(metrics),
            hybrid_metrics=dict(metrics),
            top_k=5,
            config={"num_queries": 1},
            output_path=str(artifact),
            skip_write_when_limited=True,
        )
        # Report is still returned for logging, but the 003 artifact is
        # untouched.
        assert report is not None
        stored = json.loads(artifact.read_text(encoding="utf-8"))
        assert stored == {"per_format": {"openapi": {"num_queries": 2}}}
