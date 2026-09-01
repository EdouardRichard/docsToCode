"""graph_ready capability gating (T012).

Controls whether a knowledge version participates in graph expansion, in
accordance with the knowledge-capabilities.graph-extension.schema.json
contract and the following feature requirements:

  * FR-015: graph_ready=true MUST imply dense_ready=true AND lexical_ready=true
            (graph expansion is layered on top of hybrid retrieval; without
            dense/sparse it is meaningless).
  * FR-013: declaring graph_ready before graph relations are ready prevents
            entering the graph expansion path.
  * FR-014: versions not declaring graph_ready MUST NOT participate in graph
            expansion but continue to support dense / hybrid retrieval.

The gating logic is pure (no I/O, no ORM session) so it can be unit-tested in
isolation and reused both at publish time and in the retrieval query planner.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "GraphCapabilities",
    "validate_capabilities",
    "is_graph_ready_version",
    "can_enter_graph_expansion",
]


def validate_capabilities(capabilities: Mapping[str, Any] | None) -> None:
    """Enforce the graph_ready -> (dense_ready + lexical_ready) implication.

    Validates a capabilities JSONB object (the same shape as the
    knowledge-capabilities.graph-extension.schema.json contract).

    * If graph_ready is not present or falsy, no graph implication applies
      (the dense-only / hybrid-only capability sets remain valid per FR-014).
    * If graph_ready is truthy, both dense_ready and lexical_ready MUST be
      truthy, otherwise ValueError is raised (FR-015).

    Args:
        capabilities: the version capabilities dict, e.g.
            {"dense_ready": True, "lexical_ready": True, "graph_ready": True}.

    Raises:
        ValueError: if graph_ready is true but dense_ready or lexical_ready
            is missing / false.
    """
    caps = capabilities or {}
    if not caps.get("graph_ready"):
        # graph_ready not declared or false: hybrid/dense-only capability set,
        # no graph implication to enforce (FR-014).
        return

    if not caps.get("dense_ready"):
        raise ValueError(
            "graph_ready=true requires dense_ready=true (FR-015): graph "
            "expansion is layered on top of dense retrieval, which is missing."
        )
    if not caps.get("lexical_ready"):
        raise ValueError(
            "graph_ready=true requires lexical_ready=true (FR-015): graph "
            "expansion is layered on top of lexical retrieval, which is missing."
        )


def is_graph_ready_version(version: Any) -> bool:
    """Return True only when a version is fully graph-ready.

    A version is graph-ready when **both** hold:

    1. The graph_ready boolean column on knowledge_versions is true
       (added by migration T007; the authoritative fast-path flag).
    2. The version capabilities JSONB declares dense_ready=true AND
       lexical_ready=true (the FR-015 implication, checked defensively
       so a corrupt/inconsistent flag can never gate graph expansion open).

    Versions for which graph_ready is false or absent (FR-014) and versions
    whose capabilities do not satisfy the implication are **not** graph-ready:
    they continue to participate in hybrid retrieval but MUST NOT enter the
    graph expansion path.

    Args:
        version: a KnowledgeVersion instance (or any object exposing a
            graph_ready attribute and a capabilities mapping).

    Returns:
        True if the version may participate in graph expansion.
    """
    if not bool(getattr(version, "graph_ready", False)):
        return False

    capabilities = getattr(version, "capabilities", None) or {}
    if not capabilities.get("dense_ready"):
        return False
    if not capabilities.get("lexical_ready"):
        return False
    return True


def can_enter_graph_expansion(version: Any, has_graph_edges: bool) -> bool:
    """Return True only when a version may enter the graph expansion path.

    Combines capability readiness with the runtime precondition that graph
    relations actually exist for the version:

      * FR-013: declaring graph_ready before graph relations are ready
        prevents entering graph expansion (has_graph_edges=False blocks it).
      * FR-014: a non-graph-ready version never enters graph expansion, even
        when graph edges happen to exist in the store.

    Args:
        version: a KnowledgeVersion instance (see is_graph_ready_version).
        has_graph_edges: whether hard (and, when declared, soft) graph
            relations are ready for this version.

    Returns:
        True iff the version is graph-ready AND has_graph_edges is truthy.
    """
    return is_graph_ready_version(version) and bool(has_graph_edges)


class GraphCapabilities:
    """Class facade over the module-level gating functions.

    Provided as a stable, importable namespace for callers that prefer a
    class-based API (e.g. dependency-injected capability gates). Every method
    delegates to the corresponding module-level function so behaviour stays
    identical and unit-tested in one place.
    """

    @staticmethod
    def validate_capabilities(capabilities: Mapping[str, Any] | None) -> None:
        """Delegate to validate_capabilities."""
        return validate_capabilities(capabilities)

    @staticmethod
    def is_graph_ready_version(version: Any) -> bool:
        """Delegate to is_graph_ready_version."""
        return is_graph_ready_version(version)

    @staticmethod
    def can_enter_graph_expansion(version: Any, has_graph_edges: bool) -> bool:
        """Delegate to can_enter_graph_expansion."""
        return can_enter_graph_expansion(version, has_graph_edges)
