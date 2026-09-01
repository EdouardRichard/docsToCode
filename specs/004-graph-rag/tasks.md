# Tasks: Graph RAG (004)

**Input**: Design documents from `/specs/004-graph-rag/`（plan.md / spec.md / data-model.md / research.md / quickstart.md / contracts）

**Prerequisites**: plan.md（技术栈/结构）、spec.md（5 用户故事+优先级）、research.md（决策+评测目标闸门 §0）、data-model.md（图实体/表结构/状态机）、contracts（4 图契约，置 003/contracts）。

**Tests**: 本 Feature 采用 TDD——每个功能任务拆为 ① Red（编写并运行失败测试）② Green（实现使测试通过）。

**Organization**: 按用户故事分组，依优先级 P1→P2 自上而下排序。`[P]`=无耦合可并行（不同文件、不依赖未完成任务）；无 `[P]`=存在前后依赖需串行（依赖在 [AC]/说明中标注）。

**Format**: 每任务 `- [ ] T### [P?] [US?] 描述 in 路径`，附 `[路径]`/`[AC]`/`① Red`/`② Green`。

**Path Conventions**: Web 应用——backend/src/rag_mcp/、backend/alembic/versions/、backend/tests/{contract,integration,unit}/、eval/。

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 图关系包骨架与运行配置（蓝图 §8.2/§12/§19，research §3）。

- [X] T001 Create graph/ package skeleton per plan.md Project Structure
  - [路径] backend/src/rag_mcp/graph/__init__.py, backend/src/rag_mcp/graph/store/__init__.py, backend/src/rag_mcp/graph/extractors/__init__.py
  - [AC] graph 子包与 store/extractors 子包可被导入；与既有 rag_mcp 包结构一致
  - ① Red: backend/tests/unit/test_graph_package.py — 断言 import rag_mcp.graph 及子模块存在，当前失败
  - ② Green: 创建空 __init__.py 形成包结构，测试通过

- [X] T002 [P] Add graph guardrail config defaults in config.py
  - [路径] backend/src/rag_mcp/config.py | backend/tests/unit/test_graph_config.py
  - [AC] 配置含 hop_default=2/hop_max=3/candidate_budget=10/20/graph_sub_timeout_ms=3000/total_timeout_ms=30000/direction_default=bidirectional/structure_weight_hard=1.0/structure_weight_soft=0.3/structure_weight_hop_decay=0.5/soft_confidence_threshold=0.6
  - ① Red: 测试读取 graph 配置断言上述数值，当前失败
  - ② Green: 在 config.py 注册 graph 配置段，测试通过

- [X] T003 [P] Verify no new runtime deps beyond 001/002/003
  - [路径] backend/pyproject.toml | backend/tests/unit/test_deps_unchanged.py
  - [AC] 004 复用既有依赖（psycopg/SQLAlchemy/tree-sitter/qdrant），不引入新运行时依赖
  - ① Red: 测试断言依赖集与基线快照一致，当前失败
  - ② Green: 记录快照并断言无新增，测试通过

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 图引擎核心——所有 P1 用户故事共享的图存储/扩展/融合/证据标注/能力门控/契约。CRITICAL: US1–US5 不得在本阶段完成前开始。

- [X] T004 [P] Alembic migration: graph_edge table + indexes
  - [路径] backend/alembic/versions/0041_create_graph_edge.py | backend/tests/unit/test_migration_graph_edge.py
  - [AC] graph_edge 表含 edge_id(PK)/knowledge_scope_id/project_id/index_version/source_chunk_id/target_chunk_id/relation_type/direction/is_hard/version/parse_evidence/created_at；索引 idx_graph_edge_source/idx_graph_edge_target/uniq_graph_edge；relation_type CHECK 硬关系枚举（data-model §2）
  - ① Red: 测试断言表与索引存在，当前失败
  - ② Green: 编写并应用迁移，测试通过

- [X] T005 [P] Alembic migration: soft_relation table + indexes
  - [路径] backend/alembic/versions/0042_create_soft_relation.py | backend/tests/unit/test_migration_soft_relation.py
  - [AC] soft_relation 表含 edge_id(PK)/隔离三元组/source/target/relation_type=inferred/direction/is_hard=false/version + 五元数据/lifecycle_state/superseded_by/superseded_at；索引 idx_soft_relation_pair/idx_soft_relation_active；lifecycle_state CHECK 四态（data-model §3）
  - ① Red: 测试断言表/索引/枚举约束，当前失败
  - ② Green: 编写并应用迁移，测试通过

- [X] T006 [P] Alembic migration: graph_expansion_path table
  - [路径] backend/alembic/versions/0043_create_graph_expansion_path.py | backend/tests/unit/test_migration_graph_expansion_path.py
  - [AC] graph_expansion_path 表含 request_id/evidence_id/chunk_id/start_chunk_id/edge_path(JSONB)/hop_count∈[1,3]/structure_weight/graph_rank；FK→retrieval_run/evidence/chunk（data-model §4，DM-1 chunk_id↔evidence_id 桥接）
  - ① Red: 测试断言表与 hop_count CHECK，当前失败
  - ② Green: 编写并应用迁移，测试通过

- [X] T007 [P] Alembic migration: extend knowledge_capabilities with graph_ready
  - [路径] backend/alembic/versions/0044_add_graph_ready.py | backend/tests/unit/test_migration_graph_ready.py
  - [AC] knowledge_capabilities 新增 graph_ready 布尔列（默认 false）；遵循 knowledge-capabilities.graph-extension.schema.json 门控
  - ① Red: 测试断言 graph_ready 列存在，当前失败
  - ② Green: 编写并应用迁移，测试通过

- [X] T008 [P] graph/models.py — GraphEdge / SoftRelation / GraphExpansionPath
  - [路径] backend/src/rag_mcp/graph/models.py | backend/tests/unit/test_graph_models.py
  - [AC] 三模型字段与迁移列一一对应；GraphEdge.relation_type 限定硬枚举、SoftRelation 四态枚举与五元数据必填；GraphExpansionPath 含 chunk_id 与 evidence_id 双向关联字段（DM-1）；符合 graph-relations.schema.json allOf 约束
  - ① Red: 测试构造非法 relation_type/lifecycle_state 与缺失元数据应抛错，当前失败
  - ② Green: 实现模型与校验，测试通过

- [X] T009 [P] graph/store/base.py — GraphStore abstract interface
  - [路径] backend/src/rag_mcp/graph/store/base.py | backend/tests/unit/test_graph_store_abstract.py
  - [AC] 抽象定义 get_neighbors(chunk_id, relation_types, direction, hop, budget, scope)/expand(...)/isolation 契约；蓝图 §8.3 保留迁移能力
  - ① Red: 测试抽象方法签名与隔离参数，当前失败
  - ② Green: 定义抽象基类与协议，测试通过

- [X] T010 graph/store/postgres_graph_store.py — recursive CTE 1~3 hop + guardrails (depends T004, T008, T009)
  - [路径] backend/src/rag_mcp/graph/store/postgres_graph_store.py | backend/tests/unit/test_postgres_graph_store.py
  - [AC] WITH RECURSIVE 完成 1~3 跳；depth 谓词 + LIMIT 截断（总预算非逐跳，FR-017）；只在隔离三元组作用域内扩展；高扇出按 structure_weight 全局排序截断（蓝图 §12）
  - ① Red: 测试注入高扇出 fixture，断言返回候选 ≤ budget 且按权重排序、跨作用域边不返回，当前失败
  - ② Green: 实现递归 CTE + 护栏截断，测试通过

- [X] T011 graph/expansion.py — total-budget truncation, structure weight, edge_path, bidirectional default (depends T010)
  - [路径] backend/src/rag_mcp/graph/expansion.py | backend/tests/unit/test_graph_expansion.py
  - [AC] 单次图扩展总预算 10/20 全局截断；structure_weight 按关系类型+跳数衰减（硬 1.0→0.5→0.25，软 0.3，research §2）；默认双向遍历 calls+called_by/fk_references+fk_referenced_by；每条候选保留 edge_path 跳序列（FR-008）
  - ① Red: 测试断言总预算截断、权重衰减、双向默认、edge_path 结构，当前失败
  - ② Green: 实现扩展引擎，测试通过

- [X] T012 [P] graph/capabilities.py — graph_ready gating (depends T007, T008)
  - [路径] backend/src/rag_mcp/graph/capabilities.py | backend/tests/unit/test_graph_capabilities.py
  - [AC] graph_ready=true 隐含 dense_ready+lexical_ready；声明 graph_ready 的版本图关系就绪才可检索；未声明则不参与图扩展但继续混合检索（FR-013/FR-014）
  - ① Red: 测试声明 graph_ready 但图未就绪应拒绝可检索、不隐含 dense 应拒绝，当前失败
  - ② Green: 实现能力门控，测试通过

- [X] T013 fusion/rrf.py extension — graph as 3rd retriever input, rank-only (depends T011)
  - [路径] backend/src/rag_mcp/fusion/rrf.py | backend/tests/unit/test_rrf_graph_input.py
  - [AC] RRF 融合分数 Σ 1/(k_rrf+rank_r) 覆盖 Dense/Sparse/graph 三路；graph 候选以 graph_rank 并入；结构权重只影响图候选内部排序不作独立融合系数（research §2）；确定性无随机扰动（FR-019）
  - ① Red: 测试三路 RRF 融合分数与图候选 rank 注入，当前失败（仅 Dense/Sparse 两路）
  - ② Green: 扩展 RRF 接受 graph 第 3 路，测试通过

- [X] T014 services/evidence_service.py extension — hard/soft relation annotation, no contract change (depends T008)
  - [路径] backend/src/rag_mcp/services/evidence_service.py | backend/tests/unit/test_evidence_relation_annotation.py
  - [AC] 图扩展召回的硬关系证据标注为可验证证据、软关系标注为推断关系，二者可区分；不改 search_knowledge/get_evidence 对外 Schema（FR-011，宪法 VII）；证据携带来源 ID/版本/位置（FR-012）
  - ① Red: 测试断言证据含硬/软标注且对外 Schema 校验通过，当前失败
  - ② Green: 实现标注（不改对外契约），测试通过

- [X] T015 [P] Contract test: graph-relations.schema.json
  - [路径] backend/tests/contract/test_graph_relations_schema.py | specs/003-structured-asset-expansion/contracts/graph-relations.schema.json
  - [AC] 合法/非法图边样例通过/失败校验；硬/软 allOf 约束、四态 superseded_by 必填均生效
  - ① Red: 测试用例断言非法样例被拒，当前失败
  - ② Green: 接入 json-schema 校验器，测试通过

- [X] T016 [P] Contract test: graph-expansion-trace.schema.json
  - [路径] backend/tests/contract/test_graph_expansion_trace_schema.py | specs/003-structured-asset-expansion/contracts/graph-expansion-trace.schema.json
  - [AC] 追踪样例（含 graph_candidates/edge_path/三路 fused_candidates，候选 evidence_id 可空、存活时回填）通过校验；partial→failed_paths 必填生效（DM-1）
  - ① Red: 测试断言追踪样例校验，当前失败
  - ② Green: 接入校验，测试通过

- [X] T017 [P] Contract test: knowledge-capabilities.graph-extension.schema.json
  - [路径] backend/tests/contract/test_capabilities_graph_extension_schema.py | specs/003-structured-asset-expansion/contracts/knowledge-capabilities.graph-extension.schema.json
  - [AC] graph_ready=true 必隐含 dense+lexical；非法组合（graph_ready 但 dense=false）被拒
  - ① Red: 测试断言门控样例校验，当前失败
  - ② Green: 接入校验，测试通过

- [X] T018 [P] Contract test: eval-graph-comparison-report.schema.json
  - [路径] backend/tests/contract/test_eval_graph_comparison_schema.py | specs/003-structured-asset-expansion/contracts/eval-graph-comparison-report.schema.json
  - [AC] 报告样例（three_gate_pass/per_query graph 字段含 graph_edge_path_summary/reproducibility tolerance=0.01）通过校验（DM-2）
  - ① Red: 测试断言报告样例校验，当前失败
  - ② Green: 接入校验，测试通过

- [X] T041 [P] Runtime graph-expansion-trace ledger — wire per-request trace (depends T010, T011, T013, T014)
  - [路径] backend/src/rag_mcp/graph/trace_recorder.py | backend/src/rag_mcp/services/evidence_service.py | backend/tests/integration/test_graph_runtime_trace.py
  - [AC] 每次图增强检索记录 request_id/knowledge_scope_ids/completion_status/guardrails/subpath_timings(dense/sparse/graph_recall/fusion/rerank/total)/graph_candidates(含可空 evidence_id)/fused_candidates/failed_paths/evidence_ref_ids，符合 graph-expansion-trace.schema.json；partial 时 failed_paths 非空；候选存活为证据时回填 evidence_id 并写 graph_expansion_path（DM-1 桥接）；沿用 001/002/003 证据账本扩展（蓝图 §13，FR-026）
  - ① Red: 集成测试断言一次图增强检索产出完整 trace 且 evidence_id 回填与 graph_expansion_path 一致，当前失败
  - ② Green: 实现 trace_recorder 并接入检索路径，测试通过

**Checkpoint**: 图引擎就绪（存储/扩展/融合/证据标注/能力门控/4 契约校验/运行追踪）。US1–US5 可在各自阶段开始。


---

## Phase 3: User Story 1 — Java 调用图硬关系召回与可验证证据 (Priority: P1) MVP

**Goal**: 从已切片 Java Chunk 确定性提取 calls/called_by 硬边，1~3 跳扩展召回调用者/被调用者并标记为可验证证据（spec US1）。

**Independent Test**: 对声明 graph_ready 的 Java 版本，运行图增强检索，验证调用者/被调用者证据被召回且标记为硬关系证据、MRR/nDCG 相对混合基线有提升、跨项目泄漏=0。

- [X] T019 [P] [US1] graph/extractors/java_call_graph.py — extract calls/called_by (reuse parsers/java_parser.py)
  - [路径] backend/src/rag_mcp/graph/extractors/java_call_graph.py | backend/tests/unit/test_java_call_graph.py
  - [AC] 从 Java Chunk 确定性 AST 提取 calls/called_by 边（类/函数调用、方法调用、API 实现与调用，蓝图 §10.1）；写入 graph_edge(is_hard=true, parse_evidence 含 AST 定位)；AST 失败时报告降级不伪造（Edge Case）
  - ① Red: 测试用 fixture Java 源断言提取出预期 calls/called_by 边与 parse_evidence，当前失败
  - ② Green: 复用 java_parser AST 实现确定性提取，测试通过

- [X] T020 [US1] services/ingestion_service.py extension — invoke java_call_graph extractor at ingest (depends T019, T008, T010)
  - [路径] backend/src/rag_mcp/services/ingestion_service.py | backend/tests/integration/test_us1_ingestion_java.py
  - [AC] Java 知识源入库时触发调用图提取并写 graph_edge；硬边带隔离三元组；AST 降级时记录原因（宪法 III）
  - ① Red: 集成测试断言入库后 graph_edge 含预期硬边且隔离字段齐全，当前失败
  - ② Green: 接入提取器到入库流程，测试通过

- [ ] T021 [US1] Story integration test — Java call-chain recall (AS1.1–1.3)
  - [路径] backend/tests/integration/test_us1_java_callgraph_recall.py
  - [AC] AS1.1 携带显式作用域查询某方法→1~3 跳召回调用者/被调用者硬关系证据、每条带来源 ID/版本/位置；AS1.2 validateToken 类用例方法级证据排名不劣于混合基线、变化可由 edge_path 解释；AS1.3 跨项目作用域外图边不返回、泄漏=0
  - ① Red: 端到端测试断言三场景，当前失败（依赖 T019/T020 + 引擎）
  - ② Green: T019/T020 + 引擎 T010/T011/T013/T014 就绪后通过

**Checkpoint**: US1 独立可测——Java 调用图硬关系召回端到端通过。

---

## Phase 4: User Story 2 — DDL 外键硬关系召回与可验证证据 (Priority: P1)

**Goal**: 从已切片 DDL Chunk 确定性提取 fk_references/fk_referenced_by 硬边，1~3 跳扩展召回被引用表/引用方表/级联字段（spec US2）。

**Independent Test**: 对声明 graph_ready 的 DDL 版本，运行图增强检索，验证被引用/引用方表证据被召回且标记为硬关系证据、跨项目泄漏=0。

- [X] T022 [P] [US2] graph/extractors/ddl_fk.py — extract fk_references/fk_referenced_by (reuse parsers/ddl_parser.py)
  - [路径] backend/src/rag_mcp/graph/extractors/ddl_fk.py | backend/tests/unit/test_ddl_fk.py
  - [AC] 从 DDL Chunk 确定性提取 fk_references/fk_referenced_by 边（表、字段与外键，蓝图 §10.1）；无外键或引用对象不在本项目时只对可确定关系产生硬边（Edge Case）
  - ① Red: 测试用 fixture DDL 断言提取出预期外键边，当前失败
  - ② Green: 复用 ddl_parser 实现确定性提取，测试通过

- [X] T023 [US2] services/ingestion_service.py extension — invoke ddl_fk extractor (depends T022)
  - [路径] backend/src/rag_mcp/services/ingestion_service.py | backend/tests/integration/test_us2_ingestion_ddl.py
  - [AC] DDL 知识源入库时触发外键提取并写 graph_edge；外键边带隔离三元组
  - ① Red: 集成测试断言入库后 graph_edge 含预期外键边，当前失败
  - ② Green: 接入 ddl_fk 到入库流程，测试通过

- [ ] T024 [US2] Story integration test — DDL FK recall (AS2.1–2.3)
  - [路径] backend/tests/integration/test_us2_ddl_fk_recall.py
  - [AC] AS2.1 查询某表被哪些表外键引用→1~3 跳召回引用方表/级联字段硬关系证据、带来源；AS2.2 未召回目标表沿外键边扩展并入候选并带外键路径；AS2.3 跨项目外键边不返回、泄漏=0
  - ① Red: 端到端测试断言三场景，当前失败
  - ② Green: T022/T023 + 引擎就绪后通过

**Checkpoint**: US1+US2 均独立可测——Java 调用图与 DDL 外键硬关系召回均通过。

---

## Phase 5: User Story 3 — 图增强检索在结构性受益查询上优于混合基线 (Priority: P1)

**Goal**: 图候选作 RRF 第 3 路输入+统一 Rerank，在结构性受益子集相对 002 混合基线 MRR/nDCG ≥3% 相对提升、Recall@K 不下降，且 001/002 非劣三段通过+硬性指标全过后进默认路径（spec US3，research §0）。

**Independent Test**: 固定评测集运行图增强，产出 eval-graph-comparison-report，验证 SC-001/SC-002/SC-013 + 硬性指标 + SC-007 可重复性。

- [X] T025 [US3] eval runner extension — graph-enhanced comparison report + three_gate_pass (depends T013, T011)
  - [路径] eval/graph_comparison_runner.py | backend/tests/unit/test_graph_eval_runner.py
  - [AC] 复用 002 评测 runner，扩展为 graph_enhanced_comparison 报告：config 含图护栏、three_gate_pass 三段判定、per_query 含 graph_rank/structure_weight/hop_count、reproducibility tolerance=0.01；符合 eval-graph-comparison-report.schema.json
  - ① Red: 测试用样例结果断言报告字段与三段判定逻辑，当前失败
  - ② Green: 实现图增强评测 runner，测试通过

- [ ] T026 [US3] Extend eval dataset with ≥6 structural-benefit queries (≥1 Chinese)
  - [路径] eval/eval_dataset.json | eval/README.md
  - [AC] 新增 ≥6 条结构性受益查询（Java 调用链如 validateToken 调用者/被调用者、DDL 外键链路如引用 users 的表、≥1 中文等价）；遵循 001 AI 生成+人工审核+JSON 约定；原既有查询保留（FR-021）
  - ① Red: 测试断言新增条数≥6、含中文、字段合法、原有条数不变，当前失败
  - ② Green: 新增并审核条目，测试通过

- [ ] T027 [US3] Story integration test — graph-enhanced eval vs 002 hybrid + 001 Dense (AS3.1–3.3)
  - [路径] backend/tests/integration/test_us3_graph_eval_comparison.py
  - [AC] AS3.1 结构性子集 MRR/nDCG 相对混合基线 ≥3% 相对提升、Recall@K 不下降；AS3.2 图扩展子步骤超时→partial+失败路径、不返回空/伪造；AS3.3 001 11 条 Recall@K 精确持平、MRR/nDCG 非劣（1% 容差）；硬性指标泄漏=0/Schema 100%/定位 100%；同会话先重跑混合基线（FR-025）
  - ① Red: 端到端测试断言上述场景与 three_gate_pass，当前失败
  - ② Green: T025/T026 + 引擎就绪后通过

**Checkpoint**: US1+US2+US3 均通过——图增强在结构性受益子集证明收益且三段非劣。


---

## Phase 6: User Story 4 — 软关系推断且不伪装为项目事实 (Priority: P2)

**Goal**: 离线 LLM 推断软关系，五元数据+四态机+确定性 supersede 规则，与硬关系可区分、不冒充项目事实（spec US4，research §4）。

**Independent Test**: 运行软关系推断，验证五元数据齐全、MCP 结果硬/软可区分、软关系不静默覆盖硬关系、低置信度不入默认路径。

- [X] T028 [P] [US4] graph/soft_relation_inference.py — offline LLM inference + 5 metadata + 4-state + deterministic supersede (depends T005, T008, T012)
  - [路径] backend/src/rag_mcp/graph/soft_relation_inference.py | backend/tests/unit/test_soft_relation_inference.py
  - [AC] 离线 LLM 推断软关系写入 soft_relation（relation_type=inferred, is_hard=false, 五元数据必填）；四态机 inferred→active(置信度≥0.6 且支撑证据校验)→superseded→retired；active→superseded 由确定性三元组+置信度/硬关系取代规则触发、不由 LLM 独占（宪法 VI，spec 澄清 Q3）；软关系不得升级为硬关系（宪法 III）
  - ① Red: 测试断言五元数据缺失被拒、四态转换规则、supersede 触发、低置信度不 active，当前失败
  - ② Green: 实现推断+状态机+确定性 supersede，测试通过

- [ ] T029 [US4] services/ingestion_service.py extension — invoke soft relation inference at ingest (depends T028)
  - [路径] backend/src/rag_mcp/services/ingestion_service.py | backend/tests/integration/test_us4_ingestion_soft.py
  - [AC] 入库期触发软关系推断并写 soft_relation；model_and_version 记录所用 LLM；active 软关系进入默认检索路径作低权重补充（structure_weight 0.3）
  - ① Red: 集成测试断言入库后 soft_relation 含五元数据与状态，当前失败
  - ② Green: 接入推断到入库流程，测试通过

- [ ] T030 [US4] Story integration test — soft relation distinguishable, no masquerade (AS4.1–4.3)
  - [路径] backend/tests/integration/test_us4_soft_relation.py
  - [AC] AS4.1 软关系带五元数据且可独立检索/定位；AS4.2 软/硬冲突时并列返回可区分标注、软关系不静默覆盖硬关系；AS4.3 低置信度或缺支撑证据软关系不入默认路径或仅低权重补充（FR-005）
  - ① Red: 端到端测试断言三场景，当前失败
  - ② Green: T028/T029 + T014 标注就绪后通过

**Checkpoint**: US4 独立可测——软关系可溯源/可区分/可降权、不冒充硬事实。

---

## Phase 7: User Story 5 — 知识版本声明 graph_ready 且图关系可重建与隔离 (Priority: P2)

**Goal**: graph_ready 能力门控+版本隔离+图派生数据重建+清空删除（spec US5，蓝图 §5/§8.4）。

**Independent Test**: 发布 graph_ready 版本，验证就绪才可检索、按作用域隔离、清空后停止检索、可从原始源重建。

- [X] T031 [US5] graph/capabilities.py enforcement in retrieval path — only graph_ready versions enter graph expansion (depends T012, T013)
  - [路径] backend/src/rag_mcp/graph/capabilities.py | backend/tests/integration/test_us5_capability_enforcement.py
  - [AC] 查询规划只调用已发布版本明确声明的能力；未声明 graph_ready 的版本不参与图扩展但继续混合检索（FR-014）；graph_ready 隐含 dense+lexical
  - ① Red: 测试断言未声明 graph_ready 版本不走图扩展路径、声明版才走，当前失败
  - ② Green: 在检索路径接入能力门控，测试通过

- [X] T032 [US5] graph/store/postgres_graph_store.py — isolation enforcement + cleanup (depends T010)
  - [路径] backend/src/rag_mcp/graph/store/postgres_graph_store.py | backend/tests/integration/test_us5_isolation_cleanup.py
  - [AC] 图扩展只在请求作用域内已发布 graph_ready 版本上执行、跨项目图边不返回（SC-003）；删除/清空先标记不可检索再异步删除图关系（蓝图 §5）；其他项目图关系不受影响
  - ① Red: 测试断言跨项目隔离与清空顺序，当前失败
  - ② Green: 实现隔离与清空策略，测试通过

- [ ] T033 [US5] graph rebuild — rebuild all graph derived data from source + version (depends T019, T022, T028)
  - [路径] backend/src/rag_mcp/graph/store/postgres_graph_store.py（rebuild 方法）| backend/tests/integration/test_us5_rebuild.py
  - [AC] 从原始 Java/DDL 知识源+版本信息经确定性解析重建全部 graph_edge；软关系从原始源+同 model_and_version 重推断重建（FR-016，蓝图 §8.4）；已有混合版本经用户触发重建获 graph_ready、不自动批量迁移（FR-027）
  - ① Red: 测试删除图数据后重建结果与原一致，当前失败
  - ② Green: 实现重建，测试通过

- [ ] T034 [US5] Story integration test — graph_ready gating, isolation, cleanup, rebuild (AS5.1–5.4)
  - [路径] backend/tests/integration/test_us5_graph_ready_lifecycle.py
  - [AC] AS5.1 混合能力版本重建获 graph_ready、未就绪不可检索；AS5.2 两项目 graph_ready 版本只在作用域内扩展、跨项目不返回；AS5.3 删除/清空先停检索再异步删图、他项目不受影响；AS5.4 从原始源重建全部图派生数据
  - ① Red: 端到端测试断言四场景，当前失败
  - ② Green: T031/T032/T033 就绪后通过

**Checkpoint**: 全部 5 用户故事独立可测。

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: 跨故事的边缘用例、并发、可重复性、目标 Host、文档。

- [ ] T035 [P] Edge cases — AST degradation, no-FK, fan-out truncation, partial/failed 四态, hard>soft conflict, version revoked
  - [路径] backend/tests/integration/test_graph_edge_cases.py
  - [AC] 覆盖 spec Edge Cases 全部条目：AST 失败报告降级不伪造、DDL 无外键只产可定边、高扇出护栏截断、图扩展超时→partial、硬>软冲突以硬为准、低置信度软关系排除、graph_ready 数据损坏降级、版本撤销不静默替代（宪法 III）
  - ① Red: 测试断言各边缘场景行为，当前失败
  - ② Green: 修复实现使各场景通过

- [ ] T036 [P] Concurrency isolation test — 5 concurrent requests (蓝图 §21.1)
  - [路径] backend/tests/integration/test_graph_concurrency.py
  - [AC] 5 并发请求级隔离：作用域/证据账本/图扩展中间状态不串扰（FR-020）
  - ① Red: 测试断言并发无串扰，当前失败
  - ② Green: 确认/加固请求级隔离，测试通过

- [ ] T037 [P] Reproducibility test — SC-007 1% tolerance
  - [路径] backend/tests/integration/test_graph_reproducibility.py
  - [AC] 连续两次运行 Recall@K/MRR/nDCG 在 1% 相对容差内一致；延迟标注环境敏感不否决（SC-007）
  - ① Red: 测试断言可重复性，当前失败
  - ② Green: 确定性保证后通过

- [ ] T038 [P] DeepSeek Harness end-to-end + schema validation (SC-012)
  - [路径] backend/tests/integration/test_deepseek_harness_e2e.py
  - [AC] DeepSeek Harness 端到端调用图增强 search_knowledge/get_evidence 并通过输出 Schema 校验；30s 总超时 < Host 最低 Tool Call 超时；ChatGPT App/Claude Code 仅记录兼容性不阻塞（FR-028）
  - ① Red: 测试断言端到端调用与 Schema 校验通过，当前失败
  - ② Green: 接入并通过

- [ ] T039 [P] Run quickstart.md validation scenarios
  - [路径] specs/004-graph-rag/quickstart.md | backend/tests/integration/test_quickstart_scenarios.py
  - [AC] 依次执行 quickstart.md 场景 1–7，全部期望结果达成
  - ① Red: 测试以 quickstart 场景为断言，当前失败
  - ② Green: 全场景通过

- [ ] T040 [P] Documentation cross-refs update
  - [路径] specs/004-graph-rag/plan.md, specs/004-graph-rag/research.md, specs/004-graph-rag/data-model.md, specs/004-graph-rag/quickstart.md
  - [AC] 文档间交叉引用与契约路径一致；tasks.md 任务映射到 FR/用户故事可追溯
  - ① Red: 脚本检查交叉链接，当前不一致
  - ② Green: 修正引用

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 Setup: 无依赖，立即开始（T001 先行；T002/T003 [P] 并行）
- Phase 2 Foundational: 依赖 Setup 完成；阻塞 US1–US5 全部。串行链：T004–T007 迁移 [P] 并行 → T008/T009 模型与抽象 [P] 并行 → T010 store（依赖 T004+T008+T009）→ T011 expansion（依赖 T010）→ T013 fusion（依赖 T011）；T012 capabilities（依赖 T007+T008，可与 T010/T011 并行）；T014 evidence（依赖 T008，可与 T010–T013 并行）；T015–T018 契约测试 [P] 并行；T041 运行追踪（依赖 T010+T011+T013+T014，可与 T015–T018 并行）
- Phase 3–7 用户故事: 均依赖 Foundational 完成
  - US1（P1）: T019→T020→T021（T019 可与 US2 的 T022 并行——不同提取器文件）
  - US2（P1）: T022→T023→T024（T022 与 T019 [P] 并行）
  - US3（P1）: T025→T026→T027（依赖 T013 fusion + T011 expansion）
  - US4（P2）: T028→T029→T030（T028 与 P1 故事可并行——软关系独立文件）
  - US5（P2）: T031→T032→T033→T034（T033 依赖 T019+T022+T028 重建）
- Phase 8 Polish: 依赖相关用户故事完成

### User Story Dependencies

- US1/US2/US3（P1）共享图引擎（Foundational）；US1 与 US2 提取器互不耦合可并行；US3 依赖 US1/US2 语料已入库以产出评测
- US4（P2）软关系独立于硬关系提取，可与 P1 并行；端到端依赖 T014 evidence 标注
- US5（P2）能力门控/隔离/重建依赖 T019/T022/T028 提取器与推断就绪

### Within Each User Story

- 功能任务 ① Red 先写并失败 → ② Green 实现通过
- 提取器→入库集成→故事端到端测试 顺序
- 故事级集成测试为该故事验收闸口

### Parallel Opportunities

- Phase 1: T002/T003 并行（不同文件）
- Phase 2: T004–T007 迁移并行；T008/T009 并行；T015–T018 契约测试并行；T041 运行追踪与契约测试并行；T012/T014 与 T010–T013 链并行
- Phase 3+4: T019(US1 Java) 与 T022(US2 DDL) 并行（不同提取器）
- Phase 6: T028(US4 软关系) 与 P1 故事并行
- Phase 8: T035–T040 多为 [P] 并行

---

## Parallel Example: Phase 2 Foundational

```text
# 并行：4 个迁移
Task: T004 graph_edge migration
Task: T005 soft_relation migration
Task: T006 graph_expansion_path migration
Task: T007 graph_ready capability migration
# 并行：模型 + 抽象 + 契约测试
Task: T008 graph/models.py
Task: T009 graph/store/base.py
Task: T015–T018 contract tests
Task: T041 runtime trace ledger
# 串行链（依赖上一批）
Task: T010 postgres_graph_store → T011 expansion → T013 fusion
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. 完成 Phase 1 Setup + Phase 2 Foundational（CRITICAL 阻塞）
2. 完成 Phase 3 US1（Java 调用图硬关系召回）
3. STOP 验证: 独立运行 US1 集成测试（T021）
4. 达 MVP——可演示 Java 调用链硬关系证据召回

### Incremental Delivery

1. Setup+Foundational → 引擎就绪
2. +US1 → 测试→MVP
3. +US2 → DDL 外键召回独立可测
4. +US3 → 图增强评测三段通过、决定是否进默认路径
5. +US4 → 软关系可溯源/可区分/可降权
6. +US5 → graph_ready 隔离/重建/清空
7. Phase 8 Polish → 边缘/并发/可重复/目标 Host/quickstart

### Parallel Team Strategy

1. 团队共同完成 Setup+Foundational
2. Foundational 完成后并行：A→US1、B→US2、C→US3（US3 待 US1/US2 语料）
3. P2 阶段：D→US4、E→US5（待提取器就绪）

---

## Notes

- [P] = 不同文件、无未完成依赖，可并行
- [USx] 映射 spec.md 用户故事，可追溯
- 每个功能任务 ① Red 须先失败再 ② Green 通过（TDD）
- 每故事独立可测，故事级集成测试为验收闸口
- 迁移任务以单元测试校验表/索引/约束为 Green
- 宪法硬约束（泄漏=0/Schema 100%/定位 100%/显式作用域/上传非控制）贯穿所有任务