# Specification Quality Checklist: Graph RAG

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-28
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs) — names PostgreSQL (constitution-mandated storage owner), capability flags and eval artifact paths follow established 001/002/003 house style; no internal table schema, SQL DDL, or recursive-CTE syntax.
- [x] Focused on user value and business needs — user stories center on Agent evidence quality, hard/soft relation distinguishability, and measurable retrieval improvement.
- [x] Written for non-technical stakeholders — acceptance scenarios are behavior-oriented Given/When/Then.
- [x] All mandatory sections completed — User Scenarios & Testing, Requirements, Success Criteria, Assumptions present.

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain — none introduced; reasonable defaults documented in Assumptions.
- [x] Requirements are testable and unambiguous — each FR is verifiable; baseline values and tolerance pinned to eval artifacts.
- [x] Success criteria are measurable — SC-001..SC-012 carry metrics, percentages, and artifact references.
- [x] Success criteria are technology-agnostic (no implementation details) — SC focus on outcomes (leakage=0, schema 100%, locatability 100%, MRR/nDCG deltas); storage owner naming is constitutional, not an implementation leak.
- [x] All acceptance scenarios are defined — 5 user stories × 2-4 scenarios each + 10 edge cases.
- [x] Edge cases are identified — 10 edge cases covering AST failure, missing FKs, fan-out blowup, soft/hard conflict, etc.
- [x] Scope is clearly bounded — explicit 范围内/范围外 section excludes 001/002/003/005/006 and Neo4j.
- [x] Dependencies and assumptions identified — Assumptions section documents reuse of 001/002/003, offline soft-relation inference, and eval prerequisites.

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria — FR-001..FR-028 map to user story scenarios and success criteria.
- [x] User scenarios cover primary flows — Java call-graph hard relations, DDL FK hard relations, graph-vs-hybrid comparison, soft-relation inference, graph_ready versioning.
- [x] Feature meets measurable outcomes defined in Success Criteria — SC-001..SC-012 verifiable on fixed eval set against two declared baselines.
- [x] No implementation details leak into specification — recursive-query syntax, table DDL, and LLM prompt design deferred to plan.md/research.md.

## Notes

- Two declared baselines (001 Dense Markdown/Java + 002 hybrid) per user hard constraint and blueprint §23.4.4.
- Hard constraints inherited from constitution: project_scope explicit (FR-009), cross-project leakage=0 (FR-010/SC-003), MCP schema validity 100% (FR-011/SC-004), source locatability 100% (FR-012/SC-005).
- No extension hooks registered (.specify/extensions.yml absent); post-execution hooks skipped.
- Session 2026-08-28 clarify: pinned graph guardrails (FR-017: hop default 2/max 3, candidate budget 10/20, sub-timeout 3s, total 30s), default-path entry gate ≥3% MRR/nDCG relative vs hybrid baseline (SC-001/FR-024), soft-relation 4-state lifecycle inferred→active→superseded→retired with no soft→hard upgrade (FR-003), DeepSeek Harness must-pass host matrix + non-blocking ChatGPT App/Claude Code (FR-028/SC-012), relation-type enum {calls,called_by,fk_references,fk_referenced_by,other_hard,inferred} + canonical edge identity fields (FR-002).
- Items all pass; spec is ready for `$speckit-plan` (clarifications resolved).
