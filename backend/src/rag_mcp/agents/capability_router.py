"""Capability router for Agent model selection (T009, FR-002, blueprint sec 18).

Routes Agent roles to appropriate models based on capability requirements:
  - query_planner       -> low-latency model (simple, high-frequency)
  - evidence_analyst    -> stronger model (complex evidence judgment)
  - context_orchestrator-> middle model (configurable)

No vendor lock-in: model names come from run-config / environment, never
hardcoded (Constitution architecture constraint, sec 18).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RouteResult:
    """Result of routing an Agent role to a model.

    Attributes:
        role: The agent role (query_planner / evidence_analyst / context_orchestrator).
        model: The selected model name (vendor-agnostic).
        model_and_version: Model name + version for traceability (FR-002).
        llm_base_url: Provider base URL (from config, not hardcoded).
        llm_api_key: Provider API key (from config, not hardcoded).
    """

    role: str
    model: str
    model_and_version: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""


class CapabilityRouter:
    """Routes Agent roles to models based on capability requirements (FR-002).

    No vendor lock-in: all model names are configurable via constructor or
    Settings. The router simply selects the configured model for each role,
    falling back to a default when a role-specific model is not set.
    """

    def __init__(
        self,
        query_planner_model: str = "",
        evidence_analyst_model: str = "",
        context_orchestrator_model: str = "",
        default_model: str = "",
        llm_base_url: str = "",
        llm_api_key: str = "",
    ) -> None:
        self._models = {
            "query_planner": query_planner_model,
            "evidence_analyst": evidence_analyst_model,
            "context_orchestrator": context_orchestrator_model,
        }
        self._default_model = default_model
        self._llm_base_url = llm_base_url
        self._llm_api_key = llm_api_key

    @classmethod
    def from_settings(cls, settings: Any) -> "CapabilityRouter":
        """Construct a CapabilityRouter from a Settings instance (config-driven).

        Uses the agentic model routing fields from Settings, which are
        environment-overridable and default to the general LLM model.
        """
        agentic = settings.agentic
        routing = agentic.model_routing
        return cls(
            query_planner_model=routing.query_planner_model,
            evidence_analyst_model=routing.evidence_analyst_model,
            context_orchestrator_model=routing.context_orchestrator_model,
            default_model=routing.default_model,
            llm_base_url=routing.llm_base_url,
            llm_api_key=routing.llm_api_key,
        )

    def route(self, role: str) -> RouteResult:
        """Route an Agent role to the appropriate model (FR-002).

        Falls back to the default model when the role-specific model is empty.
        """
        model = self._models.get(role, "")
        if not model:
            model = self._default_model
        return RouteResult(
            role=role,
            model=model,
            model_and_version=model,  # model name serves as model_and_version
            llm_base_url=self._llm_base_url,
            llm_api_key=self._llm_api_key,
        )
