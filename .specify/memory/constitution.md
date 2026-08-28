<!--
Sync Impact Report
- Version change: 1.1.0 -> 1.2.0
- Rationale: MINOR. The consolidated 7-principle set was restructured 1:1 to the
  blueprint Chapter 3 "核心设计原则" (10 条) so each principle is independently
  testable. No obligation was removed or weakened; the new set is a strict
  superset of v1.1.0. A new "Non-Negotiable Hard Constraints" section codifies
  the Chapter 24.2 hard acceptance criteria as release blockers.
- Principle mapping (old -> new):
    I.  Explicit Knowledge Scope            -> I.   Explicit Knowledge Scope (unchanged)
    II. Evidence Before Inference           -> II.  Project Facts Take Priority
                                              III. Expose Uncertainty
                                              IV.  Locatable Evidence
    III.Untrusted Content Isolation         -> V.   Data and Control Separation (renamed, same scope)
    IV. Deterministic Control and Stable Contracts
                                            -> VI.  Deterministic Control First (control half)
                                              VII. Independent Interface Evolution (contract half)
    V.  Versioned and Rebuildable Knowledge -> VIII. Knowledge Version Non-Mixing (renamed, same scope)
    VI. Client-Compatible Read-Only MCP     -> IX.  Synchronous Results First (reframed, superset)
    VII.Evaluation-Driven Evolution         -> X.   Evaluation-Driven Optimization (expanded)
- Added sections: Non-Negotiable Hard Constraints
- Modified sections: Core Principles (restructured to 10); Governance (adds
  hard-constraint release-blocker rule and reference to the constraint section)
- Removed sections: none (Architecture Constraints and Specification and
  Delivery Workflow preserved verbatim from v1.1.0)
- Deferred items: none
-->
# AI Engineering RAG MCP Constitution

## Core Principles

### I. Explicit Knowledge Scope
Every project-knowledge retrieval request MUST carry one or more explicit
project references (an explicit `project_scope`). The system MUST NOT infer an
implicit active project, MUST NOT default to whole-library search, and MUST
refuse retrieval when scope is absent. A project reference that cannot be
resolved to a unique project MUST stop retrieval and return candidate projects.
Public knowledge MUST use a distinct public scope and MUST NOT masquerade as a
project.

### II. Project Facts Take Priority
Public knowledge MUST NOT silently overwrite project knowledge. When project
and public evidence conflict, the system MUST return both concurrently, each
retaining its domain identity. Public knowledge participates only when a query
involves a relevant public capability; project knowledge answers how the current
project uses that capability, and public knowledge answers what that capability
is.

### III. Expose Uncertainty
Conflicts that cannot be adjudicated and evidence gaps MUST be returned to the
caller explicitly. Internal Agents MUST NOT fabricate a resolution or fill a gap
by inference. Inferred graph relationships MUST remain distinguishable from
deterministic relationships.

### IV. Locatable Evidence
Every externally returned claim MUST be backed by source-locatable evidence
carrying a source ID, version, and position. Evidence returned through MCP MUST
expose content, source location, version, status, gaps, and an evidence read
identifier, and MUST NOT expose internal database structure.

### V. Data and Control Separation
Uploaded documents and code MUST be treated as untrusted data. Untrusted
content MUST NOT directly control prompts, Agent state, permissions, tool
selection, capability gating, or state-machine transitions. Credential values
MUST be replaced with typed placeholders before entering retrieval indexes or
MCP evidence, while field names, structure, authentication method, and source
locations remain available for retrieval.

### VI. Deterministic Control First
Retrieval, filtering, fusion, ranking, budget, and state transitions MUST be
controlled by deterministic components. LLM Agents MAY provide schema-validated
judgments but MUST NOT own workflow control. An LLM judgment MUST NOT be the
sole authority over a state-machine transition.

### VII. Independent Interface Evolution
Database models, internal Agent state, and the externally-facing MCP contract
MUST evolve behind separate versioned schemas. A change to one schema MUST NOT
impose a breaking change on another without an explicit migration.

### VIII. Knowledge Version Non-Mixing
Data produced by different embedding models or incompatible chunking strategies
MUST NOT be mixed into the same index version. Each published knowledge version
MUST declare its available index capabilities, and only capabilities marked
ready MAY be queried. All derived indexes MUST be rebuildable from the source
object and version metadata.

### IX. Synchronous Results First
A single ordinary Tool Call from a target client MUST return a directly
consumable final result. Long-running task extensions MUST NOT be a baseline
dependency for the core retrieval path. A Tool response MUST contain directly
usable core evidence and MUST NOT depend on Resources or Tasks support.

### X. Evaluation-Driven Optimization
Retrieval quality, latency, and cost MUST be determined by evaluation on
real-project corpora and target MCP hosts, not by theoretical assumption.
Enhancements (lexical, rerank, graph, and Agent orchestration) MUST prove
measurable benefit against a fixed baseline AND MUST NOT violate any hard
acceptance metric before entering the default retrieval path.

## Non-Negotiable Hard Constraints

These invariants are absolute and non-violable. Derived from the blueprint
acceptance criteria (Chapter 24.2) and the scope/data principles, each is a
release blocker; violation by any feature, task, or implementation MUST halt
release until corrected.

- **Cross-project leakage MUST be zero.** No retrieval result, evidence item,
  graph relationship, or chunk from one `knowledge_scope` may surface in
  another project's retrieval unless an explicit multi-project
  `project_scope` requested it. *Verification: the count of cross-project
  leakage events in the acceptance suite MUST equal zero.*
- **Retrieval without explicit project_scope MUST be rejected.** A
  project-knowledge retrieval request carrying no explicit `project_scope`
  MUST be refused; it MUST NOT fall back to any default or whole-library
  search. *Verification: a request with no project_scope returns a rejection,
  never results.*
- **Uploaded content MUST NOT act as a control instruction.** Uploaded
  documents and code are untrusted data only; they MUST NOT control prompts,
  tool selection, permissions, capability gating, or state transitions.
  *Verification: a malicious upload cannot alter control flow, tool
  availability, or prompt scaffolding.*
- **MCP schema validity MUST be 100% on the acceptance suite.** Every Tool
  response in the acceptance test set MUST validate against its declared MCP
  schema. *Verification: the schema-validity rate over the suite MUST equal
  100%.*
- **Evidence source locatability MUST be 100% on the acceptance suite.**
  Every externally returned claim in the acceptance test set MUST carry a
  source ID, version, and position resolvable to the originating knowledge
  source. *Verification: the source-locatability rate over the suite MUST
  equal 100%.*

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
implementation preferences. The Non-Negotiable Hard Constraints are
release-blocker invariants: no Feature, plan, task, or local implementation may
weaken or suspend them; an apparent need to do so requires an explicit
constitution amendment with rationale and migration impact.

Amendments require an explicit rationale, affected artifacts, migration impact,
and semantic version change:

- MAJOR for incompatible principle removal or redefinition.
- MINOR for a new principle or materially expanded governance.
- PATCH for non-semantic clarification.

Every `/speckit-plan` and `/speckit-analyze` review MUST verify constitutional
compliance, including every Non-Negotiable Hard Constraint. Exceptions MUST be
documented in the relevant Feature `research.md` with an expiry or removal
condition; silent exceptions are prohibited.

**Version**: 1.2.0 | **Ratified**: 2026-08-26 | **Last Amended**: 2026-08-27
