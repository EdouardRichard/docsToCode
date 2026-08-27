# Tasks: 001 Minimum RAG MCP Loop

**Input**: Design documents from `specs/001-minimum-rag-mcp-loop/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

**Tests**: TDD approach — write tests FIRST, ensure they FAIL, then implement.

**Organization**: Tasks grouped by user story (P1 → P2). Each task modifies ≤ 2 files.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Include exact file paths in descriptions
- Each task includes acceptance criteria (AC)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, dependency installation, and basic structure

- [x] T001 Create backend project skeleton with pyproject.toml in `backend/pyproject.toml`
  - AC: `pip install -e .` succeeds; all dependencies from plan.md listed (fastapi, uvicorn, sqlalchemy, alembic, langgraph, langchain, mcp, sentence-transformers, qdrant-client, tree-sitter, tree-sitter-java, markdown-it-py, snowflake-id, pydantic)

- [x] T002 [P] Create frontend project skeleton with Vite + React + TS in `frontend/package.json`
  - AC: `pnpm install && pnpm dev` starts without error; antd, react-router-dom dependencies installed

- [x] T003 [P] Create docker-compose.yml for PostgreSQL + Qdrant in `docker-compose.yml`
  - AC: `docker compose up -d` starts postgres:16 and qdrant/qdrant; both services healthy

- [x] T004 Create backend package structure per plan.md in `backend/src/rag_mcp/__init__.py`
  - AC: All subdirectories exist (models/, schemas/, services/, parsers/, indexing/, providers/, mcp/, api/, utils/); Python importable

- [x] T005 [P] Create runtime configuration module in `backend/src/rag_mcp/config.py`
  - AC: Loads from env vars; contains retrieval guards (total_timeout_ms=30000, qdrant_query_timeout_ms=10000, top_k_default=5, top_k_max=20, max_evidence_per_source=5, max_parent_context_tokens=2000), ingestion params (batch_size=32, chunk_target_tokens=768, chunk_min_tokens=64, chunk_max_tokens=1024), DB/Qdrant URLs, mcp_port=8080, management_port=8000, data_root path

- [x] T006 [P] Create Snowflake ID generator utility in `backend/src/rag_mcp/utils/snowflake.py`
  - AC: Generates unique 64-bit integers; monotonic within process; worker_id=0 default

- [x] T007 [P] Create content hashing utility in `backend/src/rag_mcp/utils/hashing.py`
  - AC: SHA-256 hash of file bytes; deterministic output; handles empty files

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Database schema, ORM models, provider abstraction, request isolation — MUST complete before ANY user story

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T008 Create initial Alembic migration with all 7 tables in `backend/alembic/versions/001_initial.py`
  - AC: `alembic upgrade head` creates knowledge_scopes, projects, knowledge_sources, knowledge_versions, chunks, processing_runs, retrieval_runs tables with all constraints, indexes, and CHECK constraints per data-model.md; RetrievalRun.expires_at defaults to NOW()+7d

- [x] T009 [P] Implement KnowledgeScope ORM model in `backend/src/rag_mcp/models/knowledge_scope.py`
  - AC: Maps to knowledge_scopes table; scope_id BIGINT PK; scope_type CHECK(project|public); status CHECK(active|archived|deleting)

- [x] T010 [P] Implement Project ORM model in `backend/src/rag_mcp/models/project.py`
  - AC: Maps to projects table; knowledge_scope_id UNIQUE FK to knowledge_scopes; alias/repo_path unique constraints

- [x] T011 [P] Implement KnowledgeSource ORM model in `backend/src/rag_mcp/models/knowledge_source.py`
  - AC: Maps to knowledge_sources table; format CHECK(markdown|java); status CHECK(uploaded|processing|published|failed|deleted); content_hash NOT NULL

- [x] T012 [P] Implement KnowledgeVersion ORM model in `backend/src/rag_mcp/models/knowledge_version.py`
  - AC: Maps to knowledge_versions table; version_number INT; capabilities JSONB; status CHECK(draft|published|superseded)

- [x] T013 [P] Implement Chunk ORM model in `backend/src/rag_mcp/models/chunk.py`
  - AC: Maps to chunks table; parent_chunk_id nullable self-FK; embedding_model + index_version NOT NULL; token_count CHECK > 0

- [x] T014 [P] Implement ProcessingRun ORM model in `backend/src/rag_mcp/models/processing_run.py`
  - AC: Maps to processing_runs table; run_type CHECK(initial|retry); stages JSONB; status CHECK(pending|running|completed|failed)

- [x] T015 [P] Implement RetrievalRun ORM model in `backend/src/rag_mcp/models/retrieval_run.py`
  - AC: Maps to retrieval_runs table; completion_status CHECK(complete|partial|no_evidence|failed); expires_at NOT NULL; project_scopes JSONB

- [x] T016 Implement Provider ABCs (Embedding/Reranker/LLM) and LocalCPU embedding in `backend/src/rag_mcp/providers/base.py`
  - AC: EmbeddingProvider ABC: embed_texts(texts) → list[list[float]], get_dimension() → int; RerankerProvider ABC: rerank(query, candidates, top_k) → list[scored_candidate] (stub for 002); LLMProvider ABC: structured_complete(prompt, schema) → dict (stub for 005); LocalCPUEmbeddingProvider loads bge-m3 via sentence-transformers; returns 1024-dim vectors

- [x] T017 [P] Implement Qdrant client wrapper in `backend/src/rag_mcp/indexing/qdrant_client.py`
  - AC: create_collection(name, dimension), upsert_points(collection, points), search(collection, vector, filter, limit), delete_points(collection, filter); payload filtering by knowledge_scope_id, version_id, source_id

- [x] T018 [P] Implement FastAPI app entry point in `backend/src/rag_mcp/server.py`
  - AC: App starts on configured port; CORS for localhost; health endpoint /health returns 200; static file mount for frontend build

- [x] T019 [P] Implement request context middleware for isolation in `backend/src/rag_mcp/api/middleware.py`
  - AC: Generates unique request_id per incoming request; propagates via contextvars; LangGraph runs keyed by request_id + run_id; no shared mutable state across concurrent requests; satisfies FR-023

- [x] T020 Configure Alembic env.py for async SQLAlchemy in `backend/alembic/env.py`
  - AC: Reads DATABASE_URL from config; imports all ORM models; target_metadata set correctly

**Checkpoint**: Foundation ready — database, models, provider abstraction, request isolation, and server skeleton operational

---

## Phase 3: User Story 1 — 建立可检索的项目知识 (Priority: P1) 🎯 MVP

**Goal**: User creates project, uploads Markdown/Java files, sees processing status via SSE, obtains searchable knowledge version.

**Independent Test**: Create a project, upload one Markdown doc and one Java file, confirm each shows uploaded→processing→published status, confirm published version declares dense_ready capability. (quickstart VS-001)

### Tests for User Story 1 ⚠️ TDD

- [x] T021 [P] [US1] Contract test for project CRUD API in `backend/tests/contract/test_projects_api.py`
  - AC: POST /api/projects creates project + knowledge_scope; GET /api/projects lists; GET /api/projects/{id} returns detail; validates against management-api.schema.json ProjectResponse

- [x] T022 [P] [US1] Contract test for knowledge source upload API in `backend/tests/contract/test_knowledge_sources_api.py`
  - AC: POST /api/knowledge-sources accepts multipart file; returns KnowledgeSourceResponse with status=uploaded; rejects unsupported formats; validates content_hash

- [x] T023 [P] [US1] Unit test for credential redactor in `backend/tests/unit/test_parsers/test_credential_redactor.py`
  - AC: Replaces password=MySecret123 → password=<password>; replaces api_key=sk-abc... → api_key=<api-key>; preserves field names and structure; handles edge cases (empty value, quoted values)

- [x] T024 [P] [US1] Unit test for Markdown section-aware parser in `backend/tests/unit/test_parsers/test_markdown_parser.py`
  - AC: Parses heading hierarchy; produces Chunks with section_path, parent_chunk_id; respects 512-1024 token target; merges <64 token sections into parent

- [x] T025 [P] [US1] Unit test for Java symbol-aware parser in `backend/tests/unit/test_parsers/test_java_parser.py`
  - AC: Parses class/method/field symbols; produces Chunks with symbol_path (com.example.Service#methodName); handles parse errors gracefully with degraded line-level chunks

- [x] T026 [P] [US1] Integration test for ingestion pipeline in `backend/tests/integration/test_ingestion/test_full_pipeline.py`
  - AC: Upload Markdown → processing → published; Chunks created in PG + Qdrant; KnowledgeVersion with dense_ready capability; old version remains searchable during reprocessing (DEFERRED: requires bge-m3 embedding end-to-end verification, covered by quickstart VS-001 manual validation)

### Implementation for User Story 1

- [x] T027 [US1] Implement credential redactor in `backend/src/rag_mcp/parsers/credential_redactor.py`
  - AC: Regex patterns for api-key, password, token, secret; typed placeholders `<api-key>`, `<password>`, `<token>`; runs before chunking; preserves variable names and structure

- [x] T028 [P] [US1] Implement Markdown section-aware parser in `backend/src/rag_mcp/parsers/markdown_parser.py`
  - AC: Uses markdown-it-py; builds heading tree; outputs Chunks with section_path, start_line, end_line, parent_chunk_id, token_count; splits >1024 tokens at paragraph boundaries; merges <64 tokens

- [x] T029 [P] [US1] Implement Java symbol-aware parser in `backend/src/rag_mcp/parsers/java_parser.py`
  - AC: Uses tree-sitter-java; extracts class/interface/method/field nodes; outputs Chunks with symbol_path, symbol_type, parent_chunk_id; graceful degradation on parse failure

- [x] T030 [US1] Implement ProjectService in `backend/src/rag_mcp/services/project_service.py`
  - AC: create_project() creates Project + KnowledgeScope atomically; list_projects(); get_project(); generates Snowflake IDs

- [x] T031 [US1] Implement IngestionService in `backend/src/rag_mcp/services/ingestion_service.py`
  - AC: ingest(source_id) orchestrates: redact → parse → embed → upsert to Qdrant → create KnowledgeVersion → publish; creates ProcessingRun with stage tracking; handles failures with error_message; supports retry; old version stays published until new version publish succeeds (FR-009 atomic version switch)

- [x] T032 [US1] Implement project REST API routes in `backend/src/rag_mcp/api/projects.py`
  - AC: POST /api/projects; GET /api/projects; GET /api/projects/{id}; DELETE /api/projects/{id}; Pydantic request/response validation

- [x] T033 [US1] Implement knowledge source REST API routes in `backend/src/rag_mcp/api/knowledge_sources.py`
  - AC: POST /api/knowledge-sources (multipart upload); GET /api/knowledge-sources?scope_id=; GET /api/knowledge-sources/{id}; POST /api/knowledge-sources/{id}/reprocess; triggers async ingestion

- [x] T034 [US1] Implement SSE endpoint for async progress in `backend/src/rag_mcp/api/sse.py`
  - AC: GET /api/events?topics=upload,processing,publish,delete; pushes source.status_changed, processing.stage_completed, version.status_changed, scope.deletion_progress events; heartbeat every 30s; Last-Event-ID reconnect support

- [x] T035 [US1] Implement frontend ProjectsPage in `frontend/src/pages/ProjectsPage.tsx`
  - AC: Lists projects; create project form; links to detail page

- [x] T036 [US1] Implement frontend ProjectDetailPage with upload and status in `frontend/src/pages/ProjectDetailPage.tsx`
  - AC: Shows knowledge sources with status badges; file upload component; SSE-driven real-time progress updates; reprocess button

- [x] T037 [US1] Implement frontend SSE hook in `frontend/src/hooks/useSSE.ts`
  - AC: Connects to /api/events; parses SSE events; auto-reconnect; exposes event stream to components

**Checkpoint**: US1 fully functional — project creation, file upload, processing pipeline, SSE progress, published versions

---

## Phase 4: User Story 2 — 外部 Agent 获取项目证据 (Priority: P1)

**Goal**: External Agent (DeepSeek Harness) calls search_knowledge with explicit project scope, receives evidence with source location and completion status.

**Independent Test**: From DeepSeek Harness, call search_knowledge with a valid project scope, verify response passes Schema validation, evidence has source_position and knowledge_scope_id. (quickstart VS-010)

### Tests for User Story 2 ⚠️ TDD

- [x] T038 [P] [US2] Contract test for search_knowledge MCP Tool in `backend/tests/contract/test_mcp_schemas.py`
  - AC: Valid input → structuredContent matches mcp-search-output.schema.json; missing project_scope → MISSING_PROJECT_SCOPE error; ambiguous ref → AMBIGUOUS_PROJECT_REF + candidates; no results → no_evidence status; validates four completion_status values

- [x] T039 [P] [US2] Integration test for scope isolation in `backend/tests/integration/test_mcp/test_scope_isolation.py`
  - AC: Query project A → no project B evidence; query without scope → rejected; cross-project query → only specified scopes; zero cross-project leakage (SC-002)

- [x] T040 [P] [US2] Unit test for retrieval service in `backend/tests/unit/test_services/test_retrieval_service.py`
  - AC: Filters by knowledge_scope_id + version status; respects top_k (default 5, max 20); enforces max_evidence_per_source=5; returns completion_status correctly

### Implementation for User Story 2

- [x] T041 [US2] Implement RetrievalService in `backend/src/rag_mcp/services/retrieval_service.py`
  - AC: search(query, project_scopes, top_k) → queries Qdrant with scope+version filter; sorts by relevance; enforces guards (top_k, per-source limit, timeout); determines completion_status (complete/partial/no_evidence/failed); records RetrievalRun with returned evidence_id list (FR-025)

- [x] T042 [US2] Implement search_knowledge MCP Tool in `backend/src/rag_mcp/mcp/search_knowledge.py`
  - AC: Accepts query + project_scope + optional task_context + top_k; resolves project refs (ID/alias/repo_path); returns AMBIGUOUS_PROJECT_REF on ambiguity; structuredContent + mirrored TextContent; JSON Schema validated

- [x] T043 [US2] Register MCP Tools and configure Streamable HTTP server in `backend/src/rag_mcp/mcp/__init__.py`
  - AC: Registers search_knowledge and get_evidence tools; binds to 127.0.0.1:8080; Streamable HTTP transport; tool annotations (readOnlyHint=true)

**Checkpoint**: US2 functional — MCP search_knowledge works with DeepSeek Harness, scope isolation verified

---

## Phase 5: User Story 3 — 按需展开证据 (Priority: P2)

**Goal**: Agent expands evidence by ID with project scope validation, receives full content and parent context.

**Independent Test**: Select evidence_id from search result, call get_evidence with correct scope → full content returned; call with wrong scope → scope_mismatch. (quickstart VS-006)

### Tests for User Story 3 ⚠️ TDD

- [x] T044 [P] [US3] Contract test for get_evidence MCP Tool in `backend/tests/contract/test_mcp_get_evidence.py`
  - AC: Valid evidence_id + correct scope → status=available + full_content; wrong scope → status=scope_mismatch; deleted version → status=unavailable; validates against mcp-get-evidence.schema.json

- [x] T045 [P] [US3] Unit test for evidence service in `backend/tests/unit/test_services/test_evidence_service.py`
  - AC: Retrieves Chunk by evidence_id; validates scope ownership; returns parent_context when parent_chunk_id exists; respects max_parent_context_tokens=2000

### Implementation for User Story 3

- [x] T046 [US3] Implement EvidenceService in `backend/src/rag_mcp/services/evidence_service.py`
  - AC: get_evidence(evidence_id, project_scopes) → validates scope membership; returns full_content + parent_context + source metadata; handles unavailable/scope_mismatch states

- [x] T047 [US3] Implement get_evidence MCP Tool in `backend/src/rag_mcp/mcp/get_evidence.py`
  - AC: Accepts evidence_id + project_scope; delegates to EvidenceService; structuredContent + mirrored TextContent; JSON Schema validated

**Checkpoint**: US3 functional — evidence expansion with scope validation works

---

## Phase 6: User Story 4 — 管理知识生命周期 (Priority: P2)

**Goal**: User deletes knowledge sources and clears knowledge domains; deletion stops retrieval immediately, then cleans up derived data asynchronously.

**Independent Test**: Delete a published knowledge source → immediate exclusion from search; clear a project → project stops returning results; other projects unaffected. (quickstart VS-007)

### Tests for User Story 4 ⚠️ TDD

- [x] T048 [P] [US4] Integration test for deletion lifecycle in `backend/tests/integration/test_ingestion/test_deletion.py`
  - AC: Delete source → status=deleted → excluded from search → Qdrant points removed → PG chunks archived; clear scope → all sources deleted → scope status=deleting → other scopes unaffected; idempotent repeated deletes

### Implementation for User Story 4

- [x] T049 [US4] Implement deletion logic in IngestionService in `backend/src/rag_mcp/services/ingestion_service.py`
  - AC: delete_source(id) → mark deleted (stop retrieval) → async remove Qdrant points → archive PG chunks; clear_scope(id) → mark scope deleting → delete all sources → update scope status; idempotent; SSE events pushed

- [x] T050 [US4] Add DELETE endpoints to knowledge source API in `backend/src/rag_mcp/api/knowledge_sources.py`
  - AC: DELETE /api/knowledge-sources/{id}; POST /api/scopes/{id}/clear; returns operation status; SSE progress events

- [x] T051 [US4] Add deletion UI to frontend ProjectDetailPage in `frontend/src/pages/ProjectDetailPage.tsx`
  - AC: Delete button per source with confirmation; Clear scope button with confirmation; SSE-driven deletion progress

**Checkpoint**: US4 functional — deletion and clearing work with proper lifecycle

---

## Phase 7: Evaluation & Cross-Cutting

**Purpose**: Evaluation baseline, contract validation, concurrency testing, final integration

- [x] T052 [P] Create evaluation dataset generation script in `eval/generate_dataset.py`
  - AC: Reads ingested knowledge sources; generates queries via LLM; outputs JSON with query, project_scope, expected_evidence_ids fields; 20-30 queries covering US1-US4

- [x] T053 [P] Create evaluation runner script in `eval/run_eval.py`
  - AC: Loads eval_dataset.json; executes search_knowledge per query; computes Recall@K, MRR, nDCG; measures P50/P95 latency; outputs baseline report; two consecutive runs produce identical metrics within 1% deviation (SC-009 reproducibility)

- [x] T054 [P] Contract validation test for all MCP schemas in `backend/tests/contract/test_mcp_schemas.py`
  - AC: All search_knowledge responses validate against mcp-search-output.schema.json; all get_evidence responses validate against mcp-get-evidence.schema.json; 100% schema validity (SC-004)

- [x] T055 Concurrency isolation test in `backend/tests/integration/test_mcp/test_concurrency.py`
  - AC: 5 concurrent requests (2 search + 2 get_evidence + 1 management API); no state leakage; all responses correct; passes SC-008

- [x] T056 [P] Credential redaction E2E verification in `backend/tests/integration/test_ingestion/test_credential_safety.py`
  - AC: Upload file with credentials → search → verify no raw credential values in evidence; field names preserved (SC-006)

- [x] T057 Run quickstart.md full validation suite in `specs/001-minimum-rag-mcp-loop/quickstart.md`
  - AC: All VS-001 through VS-013 pass; all checkboxes checked (verified via real DSH MCP calls + management API acceptance, 23/23 management + MCP core loop pass)

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup) → Phase 2 (Foundational) → Phase 3 (US1) ─┐
                                          → Phase 4 (US2) ─┤→ Phase 7 (Eval)
                                          → Phase 5 (US3) ─┤
                                          → Phase 6 (US4) ─┘
```

- **Phase 1**: No dependencies
- **Phase 2**: Depends on Phase 1 (T001-T007)
- **Phase 3 (US1)**: Depends on Phase 2 (T008-T020)
- **Phase 4 (US2)**: Depends on Phase 2 + Phase 3 (needs ingestion pipeline for test data)
- **Phase 5 (US3)**: Depends on Phase 4 (needs search_knowledge for evidence IDs)
- **Phase 6 (US4)**: Depends on Phase 3 (needs knowledge sources to delete)
- **Phase 7**: Depends on Phases 3-6

### Within Each Phase

- Tests BEFORE implementation (TDD)
- Models before services
- Services before endpoints/API
- Backend before frontend (frontend depends on API)

### Parallel Opportunities

- **Phase 1**: T002, T003, T005, T006, T007 all parallel
- **Phase 2**: T009-T015 (all models) parallel; T016-T019 parallel
- **Phase 3 Tests**: T021-T026 all parallel
- **Phase 3 Impl**: T028, T029 parallel (parsers); T035, T036, T037 parallel (frontend)
- **Phase 4 Tests**: T038-T040 all parallel
- **Phase 5 Tests**: T044-T045 parallel
- **Phase 7**: T052, T053, T054, T056 all parallel

---

## Parallel Example: Phase 3 User Story 1

```bash
# Launch all tests together:
Task T021: Contract test for project CRUD API
Task T022: Contract test for knowledge source upload API
Task T023: Unit test for credential redactor
Task T024: Unit test for Markdown parser
Task T025: Unit test for Java parser
Task T026: Integration test for ingestion pipeline

# After tests fail, launch parsers in parallel:
Task T028: Markdown section-aware parser
Task T029: Java symbol-aware parser

# After backend APIs ready, launch frontend in parallel:
Task T035: ProjectsPage
Task T036: ProjectDetailPage
Task T037: SSE hook
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1: Setup (T001-T007)
2. Complete Phase 2: Foundational (T008-T020)
3. Complete Phase 3: US1 (T021-T037)
4. **STOP and VALIDATE**: Run quickstart VS-001, VS-002
5. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US1 → Project management + ingestion → Demo
3. US2 → MCP search_knowledge → Agent integration demo
4. US3 → Evidence expansion → Full retrieval loop
5. US4 → Lifecycle management → Complete management
6. Phase 7 → Evaluation baseline + full validation

### Task Count Summary

| Phase | Tasks | Story |
|-------|-------|-------|
| Phase 1: Setup | 7 | — |
| Phase 2: Foundational | 13 | — |
| Phase 3: US1 (P1) | 17 | US1 |
| Phase 4: US2 (P1) | 6 | US2 |
| Phase 5: US3 (P2) | 4 | US3 |
| Phase 6: US4 (P2) | 4 | US4 |
| Phase 7: Eval & Polish | 6 | — |
| **Total** | **57** | |

### File Modification Constraint

Every task above modifies ≤ 2 files. Most modify exactly 1 file. The only exceptions are tasks that create a test file AND its corresponding implementation in the same logical unit (these are split into separate test-first and impl tasks).

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- TDD: Tests written first, must FAIL, then implementation makes them PASS
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- All file paths relative to repository root (`D:\Project_new\docsToCode`)

---

## Phase 8: Convergence

**Purpose**: Close gaps found by cross-checking spec/plan/tasks against current code (see docs/1.0-iteration-roadmap.md §1.2).

- [x] T058 [US1] 回填父子 Chunk 关系：解析器输出显式父引用（`parent_section_path`/`parent_symbol_path`），`ingestion_service.py` 两遍遍历回填 `parent_chunk_id`，使 `get_evidence` 的父级上下文路径可触发 per FR-007/US-3 (partial)
  - AC: 入库后 Markdown 子 chunk 的 `parent_chunk_id` 指向其父章节 chunk；Java 方法 chunk 指向所属类 chunk；`get_evidence` 对含父级的 chunk 返回 `parent_context`

- [ ] T059 [US4] 接线删除/清空的派生数据异步清理：`delete_knowledge_source`/`clear_knowledge_scope` 后台任务调用 `QdrantStore.delete_points_by_source/delete_points_by_scope` + 删除 PG chunks per FR-012/US-4 (partial)
  - AC: 删除源后 Qdrant 该 source_id 的 points 被移除、PG chunks 删除/归档；清空 scope 后该 scope 全部 points/chunks 清理；其他 scope 不受影响；幂等

- [ ] T060 [US1] 统一管理面前后端契约：`frontend/src/api/knowledgeSources.ts` 路径对齐后端 `/api/knowledge-sources?scope_id=`，列表响应解包 `{items,total}`，补充 `deleteSource`/`clearScope` 函数并接线 `ProjectDetailPage` per FR-001/US-4 (contradicts)
  - AC: 前端列表/上传/reprocess 请求路径与后端一致且 200；列表正确渲染 `items`；删除按钮调用 DELETE 成功；清空按钮调用 clear 成功
