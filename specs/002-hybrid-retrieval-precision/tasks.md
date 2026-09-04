---
description: "Task list for 002 Hybrid Retrieval Precision feature implementation"
---

# Tasks: 002 Hybrid Retrieval Precision

**Input**: Design documents from `/specs/002-hybrid-retrieval-precision/`
**Branch**: `002-hybrid-retrieval-precision`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅, constitution.md ✅

**Tests**: TDD 模式（用户明确要求）—— 每个实现模块先写失败测试，再写实现使测试通过。

**Organization**: 按用户故事分组（P1 优先），每个任务修改文件数 ≤ 2。标注 [P] 表示可并行（不同文件、无未完成依赖）。

**Path Conventions**: Web app — `backend/src/rag_mcp/`（源码）、`backend/tests/`（测试）、`eval/`（评测脚本）。

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1=精确符号排名, US2=混合质量优于Dense, US3=对照评测, US4=能力声明)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization — add the one new dependency 002 requires.

- [X] T001 Add `jieba` dependency to `backend/pyproject.toml` and verify import
  **验收标准**: `pip install -e backend` 成功；`python -c "import jieba; print(jieba.lcut('混合检索'))"` 正确切分中文。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core library infrastructure that MUST be complete before ANY user story retrieval/ingestion work can begin.

**CRITICAL**: No user story work can begin until this phase is complete.

### TDD: Tests First (all parallelizable)

- [X] T002 [P] Write unit tests for BM25SparseEncoder (CJK jieba tokenization, latin regex tokenization, BM25 term weights, determinism: same-input-same-output, frozen vocab) in `backend/tests/unit/test_sparse_encoder.py`
  **验收标准**: 测试文件存在；运行 `pytest tests/unit/test_sparse_encoder.py` 全部 FAIL（encoder 尚未实现）；测试覆盖中文切分、英文符号切分、确定性、词表冻结。
- [X] T004 [P] Write unit tests for RRF fusion (formula correctness `Σ 1/(k+rank)`, determinism, tie-breaker by chunk_id, k parameter) in `backend/tests/unit/test_rrf.py`
  **验收标准**: 测试全 FAIL；覆盖两路 rank 融合、打平 tie-breaker、不同 k 值、空一侧保留。
- [X] T006 [P] Write contract tests for LocalCPUReranker (returns rerank_score, respects top_k, deterministic tie-break, no random perturbation) in `backend/tests/unit/test_reranker.py`
  **验收标准**: 测试全 FAIL；覆盖 CrossEncoder 接口契约、确定性次序、候选预算截断。
- [X] T008 [P] Extend HybridRetrievalConfig in `backend/src/rag_mcp/config.py` (rrf_k=60, rerank_budget=20, sparse_query_timeout_ms, fusion_algorithm="rrf", reranker_model="BAAI/bge-reranker-v2-m3")
  **验收标准**: `get_settings().retrieval` 暴露 rrf_k/rerank_budget/sparse_query_timeout_ms/fusion_algorithm/reranker_model 字段；默认值符合 research.md §1.2/§1.3。
- [X] T009 [P] Write unit tests for RetrievalRun hybrid fields (retrieval_mode, subpath_timings, evidence_ref_ids; dense backward-compat; hybrid requires subpath_timings) in `backend/tests/unit/test_retrieval_run_model.py`
  **验收标准**: 测试全 FAIL；覆盖 dense 默认值、hybrid 必填 subpath_timings、evidence_ref_ids 默认空数组、向后兼容。
- [X] T011 [P] Write integration tests for QdrantStore hybrid methods (create_hybrid_collection Dense+Sparse named vectors, upsert_hybrid same Point, search_sparse, query_hybrid, scope+version filter) in `backend/tests/integration/test_qdrant_hybrid.py`
  **验收标准**: 测试全 FAIL（需 Qdrant 运行）；覆盖命名向量并存、同一 Point、scope 过滤、Dense 与 Sparse 各自查询。

### Implementation (depends on their tests)

- [X] T003 Implement BM25SparseEncoder in `backend/src/rag_mcp/indexing/sparse_encoder.py` (jieba precise CJK + latin regex + BM25 weights → sparse {indices,values}, deterministic, frozen vocab)
  **依赖**: T002 | **验收标准**: T002 测试全 PASS；同输入恒定同输出；中文正确切分；词表构建期冻结不在线学习（宪法原则 VI）。
- [X] T005 Implement RRF fusion in `backend/src/rag_mcp/fusion/rrf.py` + `backend/src/rag_mcp/fusion/__init__.py` (deterministic, tie-breaker `(fused_score_desc, dense_rank_asc, sparse_rank_asc, chunk_id_asc)`, DBSF stub)
  **依赖**: T004 | **验收标准**: T004 测试全 PASS；打平时次序确定无随机扰动（FR-017）；DBSF 作为可配置备选保留接口。
- [X] T007 Implement LocalCPUReranker in `backend/src/rag_mcp/providers/local_cpu_reranker.py` (bge-reranker-v2-m3 via sentence-transformers CrossEncoder, CPU default, rerank_score added)
  **依赖**: T006 | **验收标准**: T006 测试全 PASS；只处理传入有限候选（蓝图 §18.5）；打平按 `(rerank_score_desc, fused_score_desc, chunk_id_asc)` 确定排序。
- [X] T010 Write Alembic migration + update RetrievalRun model in `backend/alembic/versions/*_add_hybrid_retrieval_fields.py` + `backend/src/rag_mcp/models/retrieval_run.py` (add retrieval_mode/subpath_timings/evidence_ref_ids + chk_hybrid_timings constraint + idx_rr_mode_created index)
  **依赖**: T009 | **验收标准**: `alembic upgrade head` 成功；T009 测试全 PASS；dense 既有记录向后兼容（retrieval_mode='dense', subpath_timings=NULL）。
- [X] T012 Implement hybrid collection methods in `backend/src/rag_mcp/indexing/qdrant_client.py` (create_hybrid_collection, upsert_hybrid Dense+Sparse same Point, search_sparse, query_hybrid with scope+version filter)
  **依赖**: T011 | **验收标准**: T011 测试全 PASS；Dense 与 Sparse 同一 Point 共享 Payload；scope+version 过滤强制（FR-008 泄漏为零基础）。

**Checkpoint**: Foundation ready — encoder, fusion, reranker, config, migration+model, Qdrant hybrid methods all functional. User story implementation can now begin.

---

## Phase 3: User Story 1 - 精确符号与关键词查询获得更高排名 (Priority: P1) 🎯 MVP

**Goal**: Dense 与 Sparse/BM25 召回结合，使携带精确标识的证据在融合后排名不劣于纯 Dense，且不引入跨项目证据。

**Independent Test**: 在固定评测集上对精确标识类查询运行混合检索，验证期望证据排名第一的比例高于 001 Dense 基线（基线 validateToken 用例排名第一失败），跨项目泄漏为零。

### TDD: Tests First

- [X] T013 [P] [US1] Write contract test for knowledge-capabilities schema (dense_ready+lexical_ready combos, gating rules, lexical_ready⇒dense_ready) in `backend/tests/contract/test_capabilities_schema.py`
  **依赖**: contracts/knowledge-capabilities.schema.json | **验收标准**: 校验合法/非法能力组合；lexical_ready=true 隐含 dense_ready=true；符合 [contracts/knowledge-capabilities.schema.json](../002-hybrid-retrieval-precision/contracts/knowledge-capabilities.schema.json)。
- [X] T014 [P] [US1] Write integration test for ingestion sparse_index stage (build sparse vectors, publish lexical_ready only after sparse ready, failure stays draft) in `backend/tests/integration/test_ingestion_sparse.py`
  **依赖**: T003, T012 | **验收标准**: 测试全 FAIL；覆盖 sparse_index 阶段执行、lexical_ready 发布门控、失败保护（版本保持 draft）。
- [X] T016 [P] [US1] Write integration test for hybrid recall path (Dense+Sparse parallel recall, exact symbol rank ≥ Dense-only, scope filter, zero cross-project leakage, pure-natural-language query rank preservation, missing-scope rejection) in `backend/tests/integration/test_hybrid_recall.py`
  **依赖**: T014 | **验收标准**: 测试全 FAIL；覆盖 validateToken 用例排名提升、scope 外不泄漏、能力门控（仅 lexical_ready 版本走 sparse）；纯自然语言查询（无精确词汇信号）混合排名不劣于 Dense-only（spec 边缘用例 L99，弱词法信号不压低语义强证据）；缺少显式 project_scope 的项目检索被拒绝（FR-007 回归）。

### Implementation

- [X] T015 [US1] Implement sparse_index stage + lexical_ready publish gating in `backend/src/rag_mcp/services/ingestion_service.py` (new sparse_index stage after embedding, write sparse vectors to hybrid collection, set capabilities.lexical_ready=true atomically on publish)
  **依赖**: T013, T014, T003, T012 | **验收标准**: T014 测试全 PASS；声明 lexical_ready 的版本 Dense+Sparse 均就绪后才 published（蓝图 §8.4）；sparse 构建失败版本保持 draft 旧版本可用（FR-023）。
- [X] T017 [US1] Implement Dense+Sparse parallel recall + capability gating in `backend/src/rag_mcp/services/retrieval_service.py` (query lexical_ready versions for sparse, Dense+Sparse recall with scope+version filter, RRF combine, exact symbol rank ≥ Dense-only)
  **依赖**: T016, T015, T005, T012 | **验收标准**: T016 测试全 PASS；仅 lexical_ready 版本参与 sparse 路径（FR-013）；Dense+Sparse 都强制 scope 过滤（FR-008）；精确符号排名不劣于 Dense-only。

**Checkpoint**: User Story 1 functional — exact-symbol queries rank higher via Dense+Sparse recall, independently testable. This is the MVP.

---

## Phase 4: User Story 2 - 混合检索质量优于 Dense 基线 (Priority: P1)

**Goal**: RRF 融合 + Cross-Encoder Rerank 使 MRR/nDCG 可度量优于 001 Dense 基线，Recall@K 不下降，partial 降级可区分，单次调用在超时护栏内。

**Independent Test**: 在固定评测集上运行完整混合路径，原 11 条子集 MRR/nDCG 相对同会话 Dense 基线严格正增量（相对口径，2026-09-04 修订，见 research.md §0.6 末注），Recall@K ≥ 1.0，P95 ≤ 30s，partial 在 sparse/rerank 失败时正确返回。

### TDD: Tests First

- [X] T018 [P] [US2] Write contract test for hybrid-retrieval-trace schema (subpath_timings, fused_candidates with per-retriever scores, failed_paths, evidence_ref_ids) in `backend/tests/contract/test_hybrid_trace_schema.py`
  **依赖**: contracts/hybrid-retrieval-trace.schema.json | **验收标准**: 校验 trace 结构合法；hybrid 模式必填 subpath_timings；partial 必填 failed_paths；符合 [contracts/hybrid-retrieval-trace.schema.json](../002-hybrid-retrieval-precision/contracts/hybrid-retrieval-trace.schema.json)。
- [X] T019 [P] [US2] Write integration test for full hybrid path (RRF fusion + Rerank + boxing + partial degradation on sparse/rerank failure + subpath timing + concurrency isolation) in `backend/tests/integration/test_hybrid_full.py`
  **依赖**: T017 | **验收标准**: 测试全 FAIL；覆盖完整链路、Rerank 剔除候选保留可展开 evidence ID、partial 状态、子路径耗时记录、确定性 tie-breaker；5 并发请求验证融合中间状态与 Rerank 候选不跨请求串扰（FR-018，research.md §2.4 请求局部变量隔离）。

### Implementation

- [X] T020 [US2] Implement fusion+Rerank+partial degradation+subpath timing in `backend/src/rag_mcp/services/retrieval_service.py` (RRF fusion → rerank budget trim → CrossEncoder rerank → final boxing; partial on sparse/rerank timeout with failed_paths; record subpath_timings + evidence_ref_ids in RetrievalRun)
  **依赖**: T019, T018, T017, T005, T007, T010 | **验收标准**: T019 测试全 PASS；partial 正确标注失败路径（FR-016）；Rerank 只处理 ≤ 候选预算（FR-005）；subpath_timings 记录 dense/sparse/fusion/rerank/total（FR-022）；对外输出契约不变（FR-009）。

**Checkpoint**: User Stories 1 AND 2 both functional — full hybrid retrieval path with measurable quality improvement and partial degradation.

---

## Phase 5: User Story 3 - 对照评测可重复且可解释 (Priority: P2)

**Goal**: 同一固定评测集可重复运行混合检索评测，产出与 001 Dense 基线并排对照的报告，含指标增量、逐查询排名变化与可解释分数，非延迟指标在容差内可重复。

**Independent Test**: 连续两次运行对照评测，Recall/MRR/nDCG 在 1% 容差内一致；报告逐查询列出基线排名 vs 混合排名 + 各路分数。

### TDD: Tests First

- [X] T021 [P] [US3] Write contract test for eval-comparison-report schema (baseline_metrics, hybrid_metrics, deltas, hard_constraints, per_query_comparison, reproducibility, enters_default_path) in `backend/tests/contract/test_eval_report_schema.py`
  **依赖**: contracts/eval-comparison-report.schema.json | **验收标准**: 校验报告结构合法；符合 [contracts/eval-comparison-report.schema.json](../002-hybrid-retrieval-precision/contracts/eval-comparison-report.schema.json)。
- [X] T022 [P] [US3] Write test for eval dataset expansion (original 11 preserved, new lexical/Chinese queries, JSON format) in `backend/tests/unit/test_eval_dataset.py`
  **验收标准**: 测试全 FAIL；覆盖原 11 条保留、新增查询含中文用例、JSON 格式合法。
- [X] T024 [P] [US3] Write integration test for run_eval --mode hybrid + run_comparison (same-session Dense rerun then hybrid, per-query explainable, reproducibility 1% tolerance, enters_default_path logic) in `backend/tests/integration/test_eval_comparison.py`
  **依赖**: T020 | **验收标准**: 测试全 FAIL；覆盖同会话先 Dense 后 Hybrid、逐查询可解释分数、非延迟可重复、enters_default_path 判定。

### Implementation

- [X] T023 [US3] Expand eval dataset with lexical-precision + Chinese queries in `eval/generate_dataset.py` + `eval/eval_dataset.json` (preserve original 11, add exact-symbol/error-code/config/Chinese queries, AI-generated human-reviewed JSON)
  **依赖**: T022 | **验收标准**: T022 测试全 PASS；原 11 条保留逐条可比（FR-019）；含中文查询用例（FR-025）；遵循 001 AI生成+人工审核约定。
- [X] T025 [US3] Implement run_eval --mode hybrid + run_comparison.py in `eval/run_eval.py` + `eval/run_comparison.py` (dual-mode dense|hybrid, same-session baseline rerun then hybrid, per-query scores, reproducibility check, comparison report per eval-comparison-report schema)
  **依赖**: T024, T021, T020 | **验收标准**: T024 测试全 PASS；对照报告符合 schema；非延迟 1% 容差可重复（SC-006）；延迟标注 env_sensitive；enters_default_path 判定正确（FR-021）。

**Checkpoint**: User Story 3 functional — repeatable, explainable comparison evaluation with baseline vs hybrid metrics.

---

## Phase 6: User Story 4 - 新索引版本声明混合能力且不混用 (Priority: P2)

**Goal**: 知识版本能力清单正确声明 lexical_ready；仅 dense_ready 的版本不被纳入 Sparse 路径；声明混合能力的版本 Dense+Sparse 均就绪后才可检索；可从原始知识源重建全部派生索引。

**Independent Test**: 发布混合能力版本验证 capabilities={dense_ready,lexical_ready}；dense-only 与 hybrid 版本共存时查询规划只调用声明能力；重建可重建全部派生索引（含 sparse）。

### TDD: Tests First

- [X] T026 [P] [US4] Write integration test for version isolation + capability gating (dense-only + lexical_ready coexist, sparse not used for dense-only versions, no cross-contamination, lexical_ready declared but sparse index corrupted → degrade to dense-only or unsearchable) in `backend/tests/integration/test_capability_isolation.py`
  **依赖**: T017 | **验收标准**: 测试全 FAIL；覆盖 FR-013 能力门控、版本共存不串入、同一 index_version 不混用不兼容数据（FR-012）；声明 lexical_ready 但底层 Sparse 索引损坏时版本降级为 Dense-only 或不可检索，不静默返回残缺词法结果（spec 边缘用例 L104）。
- [X] T027 [P] [US4] Write integration test for rebuild (reprocess triggers full rebuild incl sparse_index, old version stays searchable during rebuild, derived indexes rebuildable from source) in `backend/tests/integration/test_rebuild_sparse.py`
  **依赖**: T015 | **验收标准**: 测试全 FAIL；覆盖 FR-014 可重建、FR-023 重建期间旧版本可用、sparse_index 重新执行。

### Implementation

- [X] T028 [US4] Refine capability gating + rebuild atomicity in `backend/src/rag_mcp/services/retrieval_service.py` + `backend/src/rag_mcp/services/ingestion_service.py` (query-planning only calls declared capabilities; rebuild re-runs all stages incl sparse_index; publish atomicity guarantees)
  **依赖**: T026, T027, T017, T015 | **验收标准**: T026 + T027 测试全 PASS；查询规划只调用已发布版本声明能力（FR-013）；重建全部派生索引可重建（FR-014）；不自动批量迁移（FR-023）。

**Checkpoint**: User Story 4 functional — capability declaration, version isolation, and rebuild governance verified.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation and cross-cutting verification across all user stories.

- [X] T029 [P] Run quickstart.md validation scenarios VS-1 ~ VS-10 against the running system
  **验收标准**: 全部验证场景通过；对照报告 enters_default_path=true 时硬性指标全通过（零串库/Schema 100%/来源 100%）。
- [X] T030 [P] (跨切面·交付治理) Run `/speckit-analyze` cross-artifact consistency check across spec.md, plan.md, tasks.md
  **验收标准**: analyze 报告无 ERROR 级别不一致；任务覆盖全部 FR-001 ~ FR-025。
- [X] T031 (跨切面·交付治理) Update iteration roadmap / changelog noting 002 delivery in `docs/1.0-iteration-roadmap.md`
  **验收标准**: 路线图标注 002 交付完成；残留缺口（如 ONNX 量化、增强模型）归到 006。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on T001; BLOCKS all user stories
- **US1 (Phase 3, P1 MVP)**: Depends on Foundational; no other-story deps
- **US2 (Phase 4, P1)**: Depends on US1 (T017 recall provides candidates to rerank)
- **US3 (Phase 5, P2)**: Depends on US2 (T020 full hybrid path needed for comparison)
- **US4 (Phase 6, P2)**: Depends on US1 (T015/T017 ingestion+retrieval); can partially overlap US2/US3
- **Polish (Phase 7)**: Depends on all user stories complete

### User Story Dependencies

- **US1 (P1)**: Foundational only — independently testable MVP
- **US2 (P1)**: US1 (needs recall path to add rerank on top)
- **US3 (P2)**: US2 (needs full hybrid path to evaluate)
- **US4 (P2)**: US1 (needs ingestion+retrieval to verify isolation/rebuild); tests can overlap US2/US3

### Within Each User Story (TDD)

1. Tests written FIRST and must FAIL
2. Implementation makes tests PASS
3. Each implementation task depends on its preceding test task

### Parallel Opportunities

- **Foundational tests**: T002, T004, T006, T008, T009, T011 all [P] — launch together
- **Foundational impl**: After their tests, T003/T005/T007/T010/T012 run for different components (different files)
- **US1 tests**: T013, T014 [P] (T016 depends T014)
- **US2 tests**: T018, T019 [P] (T019 depends T017 from US1)
- **US3 tests**: T021, T022, T024 [P]
- **US4 tests**: T026, T027 [P]
- **Polish**: T029, T030, T031 [P]

---

## Parallel Example: Foundational Phase

```bash
# Launch all foundational tests together (different files, no deps):
Task T002: "test_sparse_encoder.py"
Task T004: "test_rrf.py"
Task T006: "test_reranker.py"
Task T009: "test_retrieval_run_model.py"
Task T011: "test_qdrant_hybrid.py"
# Plus config task:
Task T008: "config.py"

# After tests land, launch independent implementations:
Task T003: "sparse_encoder.py"      (after T002)
Task T005: "fusion/rrf.py"          (after T004)
Task T007: "local_cpu_reranker.py"  (after T006)
Task T010: "migration + model"      (after T009)
Task T012: "qdrant_client.py"       (after T011)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002–T012) — CRITICAL, blocks all
3. Complete Phase 3: User Story 1 (T013–T017)
4. **STOP and VALIDATE**: Test US1 independently — exact-symbol queries rank higher, zero leakage
5. Deploy/demo MVP if ready

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add US1 → Test independently → MVP (Dense+Sparse recall, exact symbols rank higher)
3. Add US2 → Test independently → Full hybrid path (fusion + rerank + partial)
4. Add US3 → Test independently → Repeatable comparison evaluation report
5. Add US4 → Test independently → Capability governance (isolation + rebuild)
6. Polish → quickstart validation + analyze

### Parallel Team Strategy

With multiple developers after Foundational:
- Developer A: US1 (then US2)
- Developer B: US3 tests (after US2 path ready) + US4 tests (after US1)
- Stories complete and integrate independently

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to user story for traceability (FR coverage)
- TDD: every implementation task has a preceding test task that must FAIL first
- Each task ≤ 2 files modified
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same-file conflicts, cross-story dependencies that break independence
- 对外 MCP 契约零变更贯穿全部任务（FR-009 / 宪法原则 VII）

## Requirement Coverage

| FR | Tasks | Story |
|----|-------|-------|
| FR-001 (Sparse 索引构建) | T003, T012, T015 | US1 |
| FR-002 (Dense+Sparse 并行召回 scope 过滤) | T012, T017 | US1 |
| FR-003 (RRF/DBSF 融合保留各路分数) | T005, T017, T020 | US1/US2 |
| FR-004 (Cross-Encoder Rerank bge-reranker) | T007, T020, T032, T037 | US2 |
| FR-005 (Rerank 只处理有限候选) | T007, T020, T032, T037 | US2 |
| FR-006 (融合保留知识域身份) | T017, T020 | US1/US2 |
| FR-007 (显式 project_scope 继承) | T016, T017 | US1 |
| FR-008 (跨项目泄漏为零) | T012, T017, T020 | US1/US2 |
| FR-009 (不改对外契约) | T017, T020 | US1/US2 |
| FR-010 (来源可定位 100%) | T017, T020 | US1/US2 |
| FR-011 (lexical_ready 发布门控) | T015, T028 | US1/US4 |
| FR-012 (不可混用) | T026 | US4 |
| FR-013 (能力门控) | T017, T028 | US1/US4 |
| FR-014 (可重建派生索引) | T015, T028 | US1/US4 |
| FR-015 (护栏配置) | T008, T020, T036 | US2 |
| FR-016 (partial 降级) | T020 | US2 |
| FR-017 (确定性 tie-breaker) | T005, T007, T020 | US2 |
| FR-018 (并发隔离) | T017, T019, T020 | US1/US2 |
| FR-019 (扩充评测集) | T023 | US3 |
| FR-020 (逐查询可解释分数) | T025, T034, T037 | US3 |
| FR-021 (可度量收益才进默认路径) | T025, T035, T037, T039 | US3 |
| FR-022 (子路径耗时追踪) | T010, T020 | US2 |
| FR-023 (重建不自动迁移) | T015, T028 | US1/US4 |
| FR-024 (同会话先 Dense 后 Hybrid) | T025, T033, T038 | US3 |
| FR-025 (CJK 分词) | T003, T023 | US1/US3 |
## Phase 8: Convergence

**Purpose**: Close gaps identified by $speckit-converge — wire the reranker into the
production path, fix the broken comparison evaluation, and produce the missing
comparison report artifact. These tasks surfaced because all T001–T031 are marked
complete but the code does not yet satisfy FR-004/FR-005 (reranker never instantiated
in the MCP path), FR-019/FR-020/FR-024/SC-001/SC-007 (comparison eval is broken and
report absent), and FR-015/SC-005 (per-query DB scan performance risk).

- [X] T032 Wire LocalCPUReranker into the MCP production search path per FR-004/FR-005 (missing)
  **Evidence**: `create_mcp_server()` in `backend/src/rag_mcp/mcp/__init__.py` does not accept a `reranker` parameter; `backend/_run_mcp.py` does not instantiate `LocalCPUReranker`; `mcp/search_knowledge.py` creates `RetrievalService` without a reranker; `RetrievalService._try_hybrid_recall` line 666 gates rerank behind `if self._reranker is not None` which is always `False` in production.
  **验收标准**: `create_mcp_server()` accepts an optional `reranker: RerankerProvider | None` param; `_run_mcp.py` instantiates `LocalCPUReranker()` and passes it to `create_mcp_server()`; `register_search_knowledge_tool` passes the reranker to `RetrievalService`; a hybrid search with lexical_ready versions exercises the rerank sub-path (subpath_timings includes rerank_ms > 0). FR-009: no change to the external MCP response schema.

- [X] T033 Fix run_comparison.py to build and pass BM25SparseEncoder to run_single_eval per FR-019/FR-024/SC-001 (partial)
  **Evidence**: `eval/run_comparison.py` lines 86–89 call `run_single_eval(..., mode="hybrid")` without the `sparse_encoder` parameter; `eval/run_eval.py` line 132 `if mode == "hybrid" and sparse_encoder is not None:` falls through to Dense-only search, so the "hybrid" comparison never exercises Sparse/RRF.
  **验收标准**: `run_comparison.py` builds a `BM25SparseEncoder` fitted on published chunk texts (same pattern as `run_eval.py` lines 488–514) and passes it to every `run_single_eval` call for the hybrid run; the Dense baseline run still uses `sparse_encoder=None`; the hybrid run's per-query results reflect Sparse+RRF fusion (fused scores differ from Dense scores).

- [X] T034 Populate per-query sparse/fused/rerank scores in comparison report and wire rerank into eval path per FR-020/SC-007 (partial)
  **Evidence**: `run_comparison.py` lines 151–153 hardcode `hybrid_sparse_score: None`, `hybrid_fused_score: None`, `hybrid_rerank_score: None`; `run_eval.py` `search_knowledge()` does Dense+Sparse+RRF but never rerank; FR-020/SC-007 require per-query Dense score, Sparse score, fused score, and rerank score.
  **验收标准**: `run_eval.py` `search_knowledge()` hybrid path optionally applies rerank (using `LocalCPUReranker` or a configurable reranker) after RRF fusion; `run_comparison.py` per_query_comparison entries carry actual `hybrid_sparse_score`, `hybrid_fused_score`, and `hybrid_rerank_score` values (not None) for each query; the comparison report schema validates against `eval-comparison-report.schema.json`.

- [X] T035 Run hybrid comparison evaluation and generate eval/hybrid_comparison_report.json verifying SC-001 thresholds per FR-021/SC-001 (missing)
  **依赖**: T033, T034 | **Evidence**: `eval/hybrid_comparison_report.json` does not exist; the plan lists it as "新增：002 对照报告产物"; FR-021 requires proof of measurable benefit (原 11 条严格正增量，相对口径 2026-09-04 修订，见 Phase 9 T039；旧绝对阈值 MRR ≥ 0.95 / nDCG ≥ 0.96 已废止) with hard constraints all passing before entering the default retrieval path.
  **验收标准**: Run `python eval/run_comparison.py --dataset eval/eval_dataset.json --output eval/hybrid_comparison_report.json --limit 18` against a running system with ingested lexical_ready knowledge; report exists with `enters_default_path=true`; original-11 subset shows strict positive MRR/nDCG deltas vs the same-session Dense baseline with non-decreasing recall (relative criterion, research.md §0.6 末注); `hard_constraints.all_passed=true` (zero cross-project leakage, schema 100%, source-locatability 100%, measured); reproducibility check passes within 1% tolerance for non-latency metrics.

- [X] T036 Cache or persist sparse encoder vocabulary to avoid per-query full DB scan per FR-015/SC-005 (partial)
  **Evidence**: `RetrievalService._build_sparse_encoder` (lines 750–771) executes a full PostgreSQL scan of all published chunk `content_text` and rebuilds `BM25SparseEncoder.fit()` on every hybrid search call; this adds significant latency per query and risks exceeding the 30s total timeout guardrail (SC-005).
  **验收标准**: The sparse encoder vocabulary (or a shared encoder instance) is cached/persisted so that it is built once (at ingestion publish time or on first query, then cached) rather than per query; hybrid search latency P50/P95 is recorded in `subpath_timings` and stays within the 30s total timeout; the cached encoder produces identical term IDs (hash-based) to the ingestion-time encoder so stored and query sparse vectors remain compatible.

## Phase 9: Convergence — 评测链路真实性修复（2026-09-04）

> 收敛评估（$speckit-analyze 全量一致性分析发现）：既有 T032–T036 已勾选，但验收证据存在三处失真——
> (1) eval 混合臂从未运行 Reranker（run_eval.py:165 以 RRF 分数冒充 rerank 分数）；(2) 对照报告 hard_constraints
> 为硬编码字面量（run_comparison.py:337-343）而非实测；(3) enters_default_path 门禁以 delta>=0 在全 18 条上
> 判定（应按原 11 条子集要求严格正增量，宪法原则 X）。本轮修复使验收证据真实化。

- [X] T037 评测混合臂接入 Reranker（对齐生产路径 retrieval_service.py:1340-1396）：run_eval.py hybrid 模式实例化 LocalCPUReranker、按候选预算截取、记录真实 Cross-Encoder 分数并按精排次序重排 per FR-004/FR-005/FR-020/SC-007 (contradicts)
  - **验收标准**: run_eval.py 混合臂 rerank_score 为真实 Cross-Encoder 分数（不再等于 fused_score）；run_comparison.py 混合臂与可重复性第二跑均传入 Reranker；报告 per_query_comparison 各路分数（dense/sparse/fused/rerank）互不相同
- [X] T038 对照报告硬指标改为实测：run_eval.py 产出 evidence_items（对齐 mcp-search-output.schema.json 证据结构），run_comparison.py 逐条测量泄漏/Schema 合法率/来源可定位率 per SC-002/SC-003/SC-004 (partial)
  - **验收标准**: 报告 hard_constraints 三值由 18 条×top_k 条证据逐条实测得出（70 条证据：泄漏 0/Schema 1.0/定位 1.0），非硬编码
- [X] T039 enters_default_path 门禁改为原 11 条严格判定 + --limit 固定 002 验收集：按原 001 基线 11 条子集计算 MRR/nDCG 严格正增量与 Recall 非降，报告携带 original_subset_gate 明细块 per FR-021/SC-001/宪法 X (contradicts)
  - **验收标准**: 门禁仅在 dataset 前 11 条上判定且要求 delta>0；报告含 original_subset_gate（契约 schema 已增补可选属性）；--limit 18 使存储记录可复现
  - **验收记录（2026-09-04 重跑）**: 原 11 条 MRR 0.7576→0.7727（+正增量）、nDCG 0.8203→0.8322（+正增量）、Recall 1.0 持平、validateToken rank 3→2（真实 rerank 分数 0.0379）；18 条全量 delta MRR +0.0926 / nDCG +0.0688；硬指标实测全过。**enters_default_path=false**：原 11 条绝对值未达 research.md §0.2 阈值（MRR≥0.95/nDCG≥0.96）——该阈值为旧环境水位口径，当前环境基线臂绝对值整体下移（0.7576）；阈值口径修订为待用户决策事项（保留绝对阈值则混合路径按宪法 X 不进默认路径，或修订 §0.2 为相对提升口径）
  - **口径决议（2026-09-04，用户选 B）**: research.md §0.2/§0.6 已修订为相对增量口径（宪法 X 判定语义为"可度量收益"，绝对阈值系旧环境水位操作化、跨环境不可迁移）；spec.md SC-001、plan.md 评测目标、quickstart 预期、本文件 US2/T035 同步；门禁改造（移除绝对阈值子句，报告改记 mrr/ndcg_relative_improvement_pct，契约 schema 与测试同步）。**报告终态重产（enters_default_path=true）**：原 11 条 MRR +2.0% / nDCG +1.45% 相对提升（0.7576→0.7727、0.8203→0.8322）、Recall 1.0 持平、硬指标实测全过（泄漏=0 / Schema=1.0 / 定位=1.0，70 条证据逐条测量）、非延迟可重复通过、真实报告通过 eval-comparison-report.schema.json 校验——混合检索进入默认检索路径，SC-001/FR-021 达成

## Phase 10: Convergence

- [X] T040 将 HybridRetrievalConfig 的 rrf_k/fusion_algorithm/sparse_query_timeout_ms 与总超时护栏接入确定性检索路径（当前仅 rerank_budget 被读取、rrf_k 硬编码 60、sparse 超时未强制） per FR-015 (partial)
- [X] T041 将 Sparse 子路径失败/超时降级为 partial 并保留 Dense 可靠证据、写入 failed_paths（当前 query_hybrid 原子调用、sparse 异常未捕获），并新增注入真实 sparse 错误的测试 per FR-016/SC-009 (missing)
