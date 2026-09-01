"""Graph-enhanced evaluation comparison runner (T025).

Builds a graph_enhanced_comparison report conforming to
eval-graph-comparison-report.schema.json. Computes three_gate_pass
(SC-001 structural improvement >= 3%, SC-002 001 non-inferior,
SC-013 002 non-structural non-inferior) + hard constraints to determine
enters_default_path (FR-024, Constitution X).
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any


class GraphComparisonRunner:
    def __init__(self, config):
        self._config = dict(config)

    def build_report(self, baseline_metrics, graph_metrics, structural_metrics,
                     sc001_improvement_pct, sc002_noninferior, sc013_noninferior,
                     per_query, hard_constraints, reproducibility):
        # Compute structural subset relative improvements
        b_mrr = structural_metrics.get('baseline_mrr_mean', 0.0)
        g_mrr = structural_metrics.get('graph_mrr_mean', 0.0)
        b_ndcg = structural_metrics.get('baseline_ndcg_mean', 0.0)
        g_ndcg = structural_metrics.get('graph_ndcg_mean', 0.0)
        recall_non_dec = structural_metrics.get('recall_non_decreasing', True)
        mrr_imp = ((g_mrr - b_mrr) / b_mrr * 100) if b_mrr > 0 else 0.0
        ndcg_imp = ((g_ndcg - b_ndcg) / b_ndcg * 100) if b_ndcg > 0 else 0.0

        # Three-gate pass
        sc001_pass = sc001_improvement_pct >= 3.0 and recall_non_dec
        sc002_pass = bool(sc002_noninferior)
        sc013_pass = bool(sc013_noninferior)
        hc_leakage = hard_constraints.get('cross_project_leakage_events', 1)
        hc_schema = hard_constraints.get('schema_validity_rate', 0.0)
        hc_locate = hard_constraints.get('source_locatability_rate', 0.0)
        hc_pass = (hc_leakage == 0 and hc_schema >= 1.0 and hc_locate >= 1.0)
        all_pass = sc001_pass and sc002_pass and sc013_pass and hc_pass

        # Deltas
        deltas = {
            'mrr_mean_delta': graph_metrics['mrr']['mean'] - baseline_metrics['mrr']['mean'],
            'ndcg_mean_delta': graph_metrics['ndcg_at_k']['mean'] - baseline_metrics['ndcg_at_k']['mean'],
            'recall_mean_delta': graph_metrics['recall_at_k']['mean'] - baseline_metrics['recall_at_k']['mean'],
            'latency_p50_delta_ms': graph_metrics['latency_ms']['p50'] - baseline_metrics['latency_ms']['p50'],
            'latency_p95_delta_ms': graph_metrics['latency_ms']['p95'] - baseline_metrics['latency_ms']['p95'],
        }

        report = {
            'report_type': 'graph_enhanced_comparison',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'config': self._config,
            'baseline_metrics': baseline_metrics,
            'graph_metrics': graph_metrics,
            'structural_subset_metrics': {
                'baseline_mrr_mean': b_mrr,
                'graph_mrr_mean': g_mrr,
                'mrr_relative_improvement': mrr_imp,
                'baseline_ndcg_mean': b_ndcg,
                'graph_ndcg_mean': g_ndcg,
                'ndcg_relative_improvement': ndcg_imp,
                'recall_at_k_non_decreasing': recall_non_dec,
            },
            'deltas': deltas,
            'hard_constraints': {
                'cross_project_leakage_events': hc_leakage,
                'schema_validity_rate': hc_schema,
                'source_locatability_rate': hc_locate,
                'all_passed': hc_pass,
            },
            'three_gate_pass': {
                'sc001_structural_improvement': sc001_pass,
                'sc002_001_noninferior': sc002_pass,
                'sc013_002_nonstructural_noninferior': sc013_pass,
                'hard_constraints_passed': hc_pass,
                'all_passed': all_pass,
            },
            'per_query_comparison': per_query,
            'reproducibility': reproducibility,
            'enters_default_path': all_pass and hc_pass,
        }
        return report
