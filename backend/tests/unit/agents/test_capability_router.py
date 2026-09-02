"""Unit test for capability router (T008 Red).

Tests the capability router that selects models for each Agent role based
on capability requirements, without hardcoding any vendor (FR-002, blueprint sec 18).

This test MUST FAIL before capability_router.py is implemented (TDD Red).
"""

from __future__ import annotations

import pytest


class TestCapabilityRouterImport:
    def test_import_capability_router(self):
        """CapabilityRouter must be importable."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        assert CapabilityRouter is not None


class TestRoleToModelRouting:
    """FR-002: query_planner->low-latency, evidence_analyst->stronger,
    context_orchestrator->middle (blueprint sec 18.4)."""

    def test_query_planner_gets_low_latency_model(self):
        """query_planner should get the low-latency model."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        router = CapabilityRouter(
            query_planner_model="fast-model",
            evidence_analyst_model="strong-model",
            context_orchestrator_model="mid-model",
            default_model="fallback-model",
        )
        result = router.route("query_planner")
        assert result.model == "fast-model"
        assert result.role == "query_planner"

    def test_evidence_analyst_gets_stronger_model(self):
        """evidence_analyst should get the stronger model."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        router = CapabilityRouter(
            query_planner_model="fast-model",
            evidence_analyst_model="strong-model",
            context_orchestrator_model="mid-model",
            default_model="fallback-model",
        )
        result = router.route("evidence_analyst")
        assert result.model == "strong-model"

    def test_context_orchestrator_gets_middle_model(self):
        """context_orchestrator should get the middle model."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        router = CapabilityRouter(
            query_planner_model="fast-model",
            evidence_analyst_model="strong-model",
            context_orchestrator_model="mid-model",
            default_model="fallback-model",
        )
        result = router.route("context_orchestrator")
        assert result.model == "mid-model"

    def test_unknown_role_falls_back_to_default(self):
        """Unknown role should fall back to the default model."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        router = CapabilityRouter(
            query_planner_model="fast-model",
            evidence_analyst_model="strong-model",
            context_orchestrator_model="mid-model",
            default_model="fallback-model",
        )
        result = router.route("unknown_role")
        assert result.model == "fallback-model"

    def test_empty_role_model_falls_back_to_default(self):
        """When a role-specific model is empty, fall back to default."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        router = CapabilityRouter(
            query_planner_model="",  # empty
            evidence_analyst_model="strong-model",
            context_orchestrator_model="",
            default_model="fallback-model",
        )
        result = router.route("query_planner")
        assert result.model == "fallback-model"


class TestModelAndVersionRecording:
    """FR-002: model_and_version must be recorded for traceability."""

    def test_route_result_records_model_and_version(self):
        """RouteResult must include model_and_version string."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        router = CapabilityRouter(
            query_planner_model="deepseek-v4-flash",
            default_model="deepseek-v4-flash",
        )
        result = router.route("query_planner")
        assert hasattr(result, "model_and_version")
        assert result.model_and_version == "deepseek-v4-flash"

    def test_route_result_has_role(self):
        """RouteResult must include the role."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        router = CapabilityRouter(default_model="test-model")
        result = router.route("evidence_analyst")
        assert result.role == "evidence_analyst"


class TestNoVendorLockIn:
    """No hardcoded vendor (Constitution architecture constraint, sec 18)."""

    def test_models_are_configurable(self):
        """Models should be fully configurable, not hardcoded."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        # Any model name should work - no vendor assumption
        router = CapabilityRouter(
            query_planner_model="custom-vendor-fast",
            evidence_analyst_model="another-vendor-strong",
            context_orchestrator_model="third-vendor-mid",
            default_model="any-model",
        )
        assert router.route("query_planner").model == "custom-vendor-fast"
        assert router.route("evidence_analyst").model == "another-vendor-strong"
        assert router.route("context_orchestrator").model == "third-vendor-mid"

    def test_from_settings_factory(self):
        """CapabilityRouter can be constructed from Settings (config-driven)."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        from rag_mcp.config import Settings
        settings = Settings()
        router = CapabilityRouter.from_settings(settings)
        assert router is not None
        # Should route without error
        result = router.route("query_planner")
        assert result is not None
        assert hasattr(result, "model")

    def test_llm_base_url_and_api_key_passed_through(self):
        """Router should pass through llm_base_url and llm_api_key (FR-002)."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        router = CapabilityRouter(
            default_model="test",
            llm_base_url="https://api.example.com",
            llm_api_key="sk-test",
        )
        result = router.route("query_planner")
        assert hasattr(result, "llm_base_url")
        assert result.llm_base_url == "https://api.example.com"
        assert result.llm_api_key == "sk-test"
