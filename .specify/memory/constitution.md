<!--
Sync Impact Report
- Version change: 1.0.0 -> 1.1.0
- Added principles: explicit knowledge scope; evidence before inference; untrusted
  content isolation; deterministic control; versioned indexes; client-compatible
  MCP; evaluation-driven evolution
- Modified principle: Client-Compatible Read-Only MCP now includes ChatGPT App
  (Codex), DeepSeek Harness, and Claude Code
- Added sections: Architecture Constraints; Specification and Delivery Workflow
- Removed sections: none
- Deferred items: none
-->
# AI Engineering RAG MCP Constitution

## Core Principles

### I. Explicit Knowledge Scope
Every project-knowledge request MUST carry an explicit `project_scope`. Every
knowledge source, chunk, vector, and graph relationship MUST carry a stable
`knowledge_scope_id`. The system MUST NOT infer a global active project or search
all projects when scope is absent. Public knowledge MUST use a distinct public
scope and MUST NOT masquerade as a project.

### II. Evidence Before Inference
Every externally returned claim MUST be backed by source-locatable evidence with
source ID, version, and position. Project and public evidence MUST retain their
domain identity. Conflicts and evidence gaps MUST be returned explicitly; internal
Agents MUST NOT fabricate a resolution. Inferred graph relationships MUST remain
distinguishable from deterministic relationships.

### III. Untrusted Content Isolation
Uploaded documents and code MUST be treated as untrusted data and MUST NOT control
prompts, tools, permissions, or state transitions. Credential values MUST be
replaced with typed placeholders before entering retrieval indexes or MCP evidence,
while field names, structure, and source locations remain available for retrieval.

### IV. Deterministic Control and Stable Contracts
State transitions, project filtering, capability checks, iteration limits, and
response serialization MUST be controlled by deterministic code. LLM Agents MAY
provide schema-validated judgments but MUST NOT own workflow control. Database
models, internal Agent state, REST contracts, and MCP contracts MUST evolve behind
separate versioned schemas.

### V. Versioned and Rebuildable Knowledge
Each published knowledge version MUST declare its available index capabilities.
Only capabilities marked ready MAY be queried. Embedding model, vector dimension,
chunking strategy, and index version MUST be recorded, and incompatible embeddings
MUST NOT share an index version. All derived indexes MUST be rebuildable from the
source object and version metadata.

### VI. Client-Compatible Read-Only MCP
The baseline MCP surface MUST remain read-only and usable by ChatGPT App (Codex),
DeepSeek Harness, and Claude Code through `search_knowledge` and `get_evidence`.
A Tool response MUST contain directly usable core evidence and MUST NOT depend on
Resources or Tasks support. `structuredContent` is canonical; compatibility text
MUST be deterministically derived from it.

### VII. Evaluation-Driven Evolution
Cross-project leakage MUST be zero. MCP schema validity and evidence source
locatability MUST each be 100% on the acceptance suite. Dense retrieval establishes
the first measurable baseline. BM25, Rerank, Graph RAG, and Agent orchestration MUST
demonstrate benefit against a fixed baseline before becoming default behavior.

## Architecture Constraints

- Python, LangGraph, and LangChain form the backend orchestration baseline.
- React and TypeScript form the Web management baseline; Python exposes the REST
  management API.
- Qdrant owns Dense and Sparse/BM25 retrieval. PostgreSQL owns control-plane data,
  chunk metadata, version state, and the initial lightweight graph.
- Local defaults are `BAAI/bge-m3` and `BAAI/bge-reranker-v2-m3`. Provider
  interfaces MUST permit local CPU, local GPU, and remote API execution.
- Streamable HTTP is the primary shared MCP transport; stdio is an adapter.
- The initial deployment is single-writer/multi-reader, but storage and write
  coordination abstractions MUST permit future distributed operation.
- Unauthenticated HTTP MUST bind to loopback by default.

## Specification and Delivery Workflow

1. Every delivery Feature MUST have independently testable user scenarios and
   measurable success criteria in `spec.md`.
2. The first delivery Feature is `001-minimum-rag-mcp-loop`; it covers the Web
   management path, Markdown and Java ingestion, Dense retrieval, both MCP Tools,
   and baseline evaluation.
3. Feature clarification MUST resolve all `[NEEDS CLARIFICATION]` markers before
   planning.
4. `plan.md` MUST preserve the approved system blueprint and this constitution.
5. `tasks.md` MUST map every task to a requirement or user story and MUST include
   contract, isolation, and target-host tests.
6. Implementation MUST NOT begin before the applicable spec, plan, and tasks have
   passed consistency analysis.
7. Scope expansion MUST be created as a new Feature or an explicit constitution
   amendment; it MUST NOT be hidden inside an unrelated task.

## Governance

This constitution overrides conflicting Feature specs, plans, tasks, and local
implementation preferences. Amendments require an explicit rationale, affected
artifacts, migration impact, and semantic version change:

- MAJOR for incompatible principle removal or redefinition.
- MINOR for a new principle or materially expanded governance.
- PATCH for non-semantic clarification.

Every `/speckit-plan` and `/speckit-analyze` review MUST verify constitutional
compliance. Exceptions MUST be documented in the relevant Feature `research.md`
with an expiry or removal condition; silent exceptions are prohibited.

**Version**: 1.1.0 | **Ratified**: 2026-08-26 | **Last Amended**: 2026-08-26
