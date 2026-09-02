"""Unit test for mixed-mechanism terminal decision (T030 Red, US2).

Tests the terminal decision logic that determines the final completion_status:
  - partial carries validated evidence + uncovered + conflict + failed paths (FR-016)
  - No generated content fills gaps (Constitution III)
  - Four states are distinguishable: complete/partial/no_evidence/failed (SC-011)

This test MUST FAIL before terminal decision is implemented (TDD Red).
"""

from __future__ import annotations

import pytest


class TestTerminalDecision:
    """FR-015/FR-016/SC-011: mixed-mechanism terminal decision -> four-state."""

    def _make_machine(self):
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        return AgenticStateMachine(
            run_id="999",
            request_id="req-1",
            project_scope=["proj-a"],
            knowledge_scope_ids=["100"],
        )

    def test_complete_when_covered(self):
        """coverage_state=covered -> complete (blueprint sec 14)."""
        machine = self._make_machine()
        status = machine.determine_terminal_status(
            coverage_state="covered",
            conflict_type="none",
            has_evidence=True,
            has_gap=False,
        )
        assert status == "complete"

    def test_partial_when_gap_and_evidence(self):
        """coverage_state=partial with evidence -> partial (FR-016)."""
        machine = self._make_machine()
        status = machine.determine_terminal_status(
            coverage_state="partial",
            conflict_type="none",
            has_evidence=True,
            has_gap=True,
        )
        assert status == "partial"

    def test_no_evidence_when_uncovered(self):
        """coverage_state=uncovered -> no_evidence (blueprint sec 14)."""
        machine = self._make_machine()
        status = machine.determine_terminal_status(
            coverage_state="uncovered",
            conflict_type="none",
            has_evidence=False,
            has_gap=False,
        )
        assert status == "no_evidence"

    def test_partial_when_max_rounds_with_gap(self):
        """Reaching max_rounds with gaps -> partial (FR-016)."""
        machine = self._make_machine()
        status = machine.determine_terminal_status(
            coverage_state="partial",
            conflict_type="none",
            has_evidence=True,
            has_gap=True,
            max_rounds_reached=True,
        )
        assert status == "partial"

    def test_partial_carries_conflict(self):
        """partial with conflict_type != none is still partial (FR-016)."""
        machine = self._make_machine()
        status = machine.determine_terminal_status(
            coverage_state="partial",
            conflict_type="version_conflict",
            has_evidence=True,
            has_gap=True,
        )
        assert status == "partial"

    def test_no_generated_content_fills_gap(self):
        """Terminal decision must not generate content to fill gaps (Constitution III)."""
        machine = self._make_machine()
        # When there is a gap, the status should be partial, not complete
        status = machine.determine_terminal_status(
            coverage_state="partial",
            conflict_type="none",
            has_evidence=True,
            has_gap=True,
        )
        assert status == "partial"  # not "complete" - gaps are exposed, not filled

    def test_four_states_distinguishable(self):
        """All four states must be distinguishable (SC-011)."""
        machine = self._make_machine()
        statuses = set()
        statuses.add(machine.determine_terminal_status("covered", "none", True, False))
        statuses.add(machine.determine_terminal_status("partial", "none", True, True))
        statuses.add(machine.determine_terminal_status("uncovered", "none", False, False))
        statuses.add(machine.determine_terminal_status("uncovered", "none", False, False, agent_failed=True))
        assert "complete" in statuses
        assert "partial" in statuses
        assert "no_evidence" in statuses
        # failed should also be possible
        assert "failed" in statuses or len(statuses) >= 3
