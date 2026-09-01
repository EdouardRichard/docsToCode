"""Documentation cross-reference test (T040).

Validates that 004 spec docs cross-reference each other and the contract
paths consistently, and that tasks map to FR/user stories (traceability).

This test MUST FAIL before cross-refs are consistent (TDD).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC_DIR = _REPO_ROOT / "specs" / "004-graph-rag"
_CONTRACTS = _REPO_ROOT / "specs" / "003-structured-asset-expansion" / "contracts"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class TestDocFilesExist:
    @pytest.mark.parametrize("name", ["spec.md", "plan.md", "research.md",
                                      "data-model.md", "quickstart.md", "tasks.md"])
    def test_doc_exists(self, name):
        assert (_SPEC_DIR / name).exists(), f"{name} must exist"


class TestContractPathsConsistent:
    @pytest.mark.parametrize("contract", [
        "graph-relations.schema.json",
        "graph-expansion-trace.schema.json",
        "knowledge-capabilities.graph-extension.schema.json",
        "eval-graph-comparison-report.schema.json",
    ])
    def test_contract_exists(self, contract):
        assert (_CONTRACTS / contract).exists(), f"{contract} must exist"

    def test_plan_references_contracts_dir(self):
        plan = _read(_SPEC_DIR / "plan.md")
        assert "003-structured-asset-expansion/contracts" in plan or "contracts" in plan


class TestCrossReferences:
    def test_tasks_reference_user_stories(self):
        """tasks.md must map tasks to user stories (traceability)."""
        tasks = _read(_SPEC_DIR / "tasks.md")
        for us in ["US1", "US2", "US3", "US4", "US5"]:
            assert us in tasks, f"tasks.md must reference {us}"

    def test_tasks_reference_functional_requirements(self):
        """tasks.md must reference FR numbers (traceability)."""
        tasks = _read(_SPEC_DIR / "tasks.md")
        assert "FR-" in tasks, "tasks.md must reference FR-### requirements"

    def test_quickstart_references_contracts(self):
        """quickstart.md must reference the contract schemas."""
        qs = _read(_SPEC_DIR / "quickstart.md")
        assert "graph-relations.schema.json" in qs or "graph-expansion-trace" in qs

    def test_data_model_references_contracts(self):
        """data-model.md must reference the graph contracts."""
        dm = _read(_SPEC_DIR / "data-model.md")
        assert "graph-relations" in dm

    def test_research_declares_eval_gate(self):
        """research.md must declare the relative-eval gate first (research sec 0)."""
        research = _read(_SPEC_DIR / "research.md")
        assert "评测目标" in research or "基线" in research
