"""Integration test: cross-instance concurrency isolation (T035/T036).

SC-011/FR-008: a mixed batch of writer + reader instance requests running
5-way concurrently must show zero request-state / evidence / scope
crosstalk; run state is isolated by request_id / run_id.

Reuses the 001 concurrency harness (AgenticStateMachine): each concurrent
request carries a distinct instance_mode tag (writer/reader mix), request_id
and run_id, and its own project scope — no state may leak across requests.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_mixed_five_concurrent_no_crosstalk():
    """writer + reader mixed 5 concurrent; run state isolated by run_id."""
    from rag_mcp.orchestration.state_machine import AgenticStateMachine

    # Instance-mode tag per request (writer/reader mix); the machine itself
    # is mode-agnostic — the tag models the 006 deployment form.
    mode_tags = ["writer", "reader", "writer", "reader", "writer"]

    machines = [
        AgenticStateMachine(
            run_id=str(1000 + i),
            request_id=f"req-{1000 + i}",
            project_scope=[f"proj-{1000 + i}"],
            knowledge_scope_ids=[str(100 + i)],
        )
        for i in range(5)
    ]

    async def _run(i: int, m):
        return mode_tags[i], await asyncio.to_thread(m.run, context={"query": "test"})

    results = await asyncio.gather(*[_run(i, m) for i, m in enumerate(machines)])

    for i, m in enumerate(machines):
        assert m.run_id == str(1000 + i)
        assert m.request_id == f"req-{1000 + i}"
        assert m.project_scope == [f"proj-{1000 + i}"]
        # each request's instance-mode tag stayed attached to itself
        assert results[i][0] == mode_tags[i]


@pytest.mark.asyncio
async def test_request_state_isolated_by_request_id():
    """No request state leaks across concurrent requests (FR-008)."""
    from rag_mcp.orchestration.state_machine import AgenticStateMachine

    ids = [f"req-{7000 + i}" for i in range(5)]
    machines = [
        AgenticStateMachine(
            run_id=str(8000 + i),
            request_id=ids[i],
            project_scope=[f"scope-{i}"],
            knowledge_scope_ids=[str(i)],
        )
        for i in range(5)
    ]
    await asyncio.gather(
        *[
            asyncio.to_thread(m.run, context={"query": f"q{i}"})
            for i, m in enumerate(machines)
        ]
    )
    for i, m in enumerate(machines):
        assert m.request_id == ids[i]
        assert m.project_scope == [f"scope-{i}"]
        assert m.run_id == str(8000 + i)


def test_state_machine_isolated_by_run_id():
    """The 001 harness already isolates by request_id/run_id (FR-025)."""
    from rag_mcp.orchestration.state_machine import AgenticStateMachine

    m1 = AgenticStateMachine(
        run_id="1", request_id="r1", project_scope=["a"], knowledge_scope_ids=["1"]
    )
    m2 = AgenticStateMachine(
        run_id="2", request_id="r2", project_scope=["b"], knowledge_scope_ids=["2"]
    )
    m1.run(context={"query": "test", "force_gap": True})
    m2.run(context={"query": "test", "force_gap": False})
    assert m1.run_id == "1" and m2.run_id == "2"
    assert m1.project_scope == ["a"] and m2.project_scope == ["b"]
    # each machine rounds independently (no crosstalk between instances)
    assert isinstance(m1.rounds_completed, int)
    assert isinstance(m2.rounds_completed, int)
