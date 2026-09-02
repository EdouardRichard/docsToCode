# Tasks: Agentic Retrieval Orchestration (005)

**Branch**: 005-agentic-retrieval-orchestration | **Date**: 2026-09-02 | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

> 可执行任务清单。每个功能任务遵循 TDD：① Red（编写并运行失败测试）② Green（实现使测试通过）。任务严格按 User Story 分组，依优先级 P1→P2 自上而下。每个子任务含 [路径]（文件路径）与 [AC]（验收标准）。[P] 表示无前后依赖、可并行；无 [P] 且标 [deps] 者为串行依赖。设计依据 plan.md、data-model.md、contracts/、research.md、quickstart.md。

## Format: - [ ] [ID] [P?] [Story?] Description — [路径] path

- **[P]**: 可并行（不同文件、无对未完成任务的依赖）
- **[路径]**: 明确的代码/测试文件路径
- **[AC]**: 验收标准（客观可测）
- **[deps]**: 串行依赖任务 ID（[P] 时省略）

## Path Conventions

- 后端源码：backend/src/rag_mcp/{agents,orchestration,eval}/ （复用 001–004 既有 backend/）
- 测试：backend/tests/{contract,integration,unit,e2e}/
- 契约：specs/005-agentic-retrieval-orchestration/contracts/*.json
- 评测：eval/agentic_eval_dataset.json、eval/agentic_comparison_report.json
- 迁移：backend/migrations/005_agentic_tables.py

## Implementation Strategy

- **MVP 先行**：Phase 1（Setup）+ Phase 2（Foundational）+ Phase 3（US1 查询规划）即可演示“多跳查询被拆解后获更完整证据”的独立价值。
- **增量交付**：US1→US2→US3（均 P1）逐故事可独立验收；US4（P2）为评测闸口。
- **复用优先**：不重新实现 001–004 能力（解析/切片/嵌入/融合/Rerank/图谱/对外 MCP 契约）。
- **宪法护栏贯穿**：每个 Green 任务须满足“不改对外契约、跨项目泄漏=0、确定性控制器掌跳转权”。

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Create feature source directories per plan
  - [路径] backend/src/rag_mcp/agents/、backend/src/rag_mcp/orchestration/、backend/src/rag_mcp/eval/
  - [AC] 目录与 __init__.py 创建；imports 可解析

- [X] T002 [P] Configure agentic run-config schema (guardrails defaults, model routing, agent toggle, 004 graph guardrails reuse)
  - [路径] backend/src/rag_mcp/config/agentic.py
  - [AC] config 加载默认值（轮次 2/上限3、节点 5s/上限10s、top_k≤20、单来源 3/上限5、总超时 30s、图跳数 2/3·预算 10/20·子超时 3s）；toggle off→确定性回退

- [X] T003 DB migration: create 005 runtime tables (TTL + indexes + isolation triples)
  - [路径] backend/migrations/005_agentic_tables.py
  - [AC] evidence_ledger_entry / agent_judgment / context_selection_list / agentic_retrieval_run 建表；TTL 列；(knowledge_scope_id,project_id,index_version) 隔离；append-only（ORM 无 UPDATE 路径）

## Phase 2: Foundational (Blocking Prerequisites — BLOCKS all user stories)

- [X] T004 [P] Red: contract test asserting 4 schemas parse + $ref resolve + 固化 enums present
  - [路径] backend/tests/contract/test_005_schemas.py + specs/005-.../contracts/*.json
  - [AC] common/evidence-ledger-entry/agent-judgment/agentic-retrieval-run 均合法 JSON Schema；枚举 {covered,partial,uncovered}/{none,version_conflict,source_conflict,domain_conflict}/{selected,truncated,deduped}/{query_planner,evidence_analyst,context_orchestrator} 存在；测试当前失败

- [X] T005 Green: confirm/fix 4 contract schemas
  - [路径] specs/005-.../contracts/*.json
  - [AC] T004 测试通过
  - [deps] T004

- [X] T006 [P] Red: failing test for AgentBase node-Schema validation + degradation fallback
  - [路径] backend/tests/unit/agents/test_base_agent.py
  - [AC] 非法结构化输出→schema_valid=false→回退确定性等价→返回有效四态；不阻塞状态机（FR-003/SC-011）

- [X] T007 Green: implement base.py (Agent 抽象 + Schema 校验 + degrade)
  - [路径] backend/src/rag_mcp/agents/base.py
  - [AC] T006 测试通过
  - [deps] T006

- [X] T008 [P] Red: failing test for capability router (no vendor lock, role→capability)
  - [路径] backend/tests/unit/agents/test_capability_router.py
  - [AC] query_planner→低延迟、evidence_analyst→更强、context_orchestrator→居中；model_and_version 记录；无硬编码供应商（FR-002/蓝图 §18）

- [X] T009 Green: implement capability_router.py
  - [路径] backend/src/rag_mcp/agents/capability_router.py
  - [AC] T008 测试通过
  - [deps] T008

- [X] T010 [P] Red: failing test for append-only ledger store (identity + isolation + no-overwrite)
  - [路径] backend/tests/unit/orchestration/test_ledger.py
  - [AC] 只 INSERT；ledger_entry_id 雪花 ID（数字串）；round_index/sub_problem_id 单调；(request_id,evidence_id) 桥接键解析；跨作用域写入拒绝（FR-008/FR-009/FR-022）

- [X] T011 Green: implement ledger.py
  - [路径] backend/src/rag_mcp/orchestration/ledger.py
  - [AC] T010 测试通过
  - [deps] T010

- [X] T012 Red: failing test for LangGraph state machine skeleton (9-step + guardrails + isolation)
  - [路径] backend/tests/unit/orchestration/test_state_machine.py
  - [AC] 九步主状态流顺序；护栏强制（轮次/超时/装箱）；状态按 request_id/run_id 隔离；无全局活动项目（FR-004/FR-006/FR-025）

- [X] T013 Green: implement state_machine.py skeleton (确定性控制器 + 补充循环 stub)
  - [路径] backend/src/rag_mcp/orchestration/state_machine.py
  - [AC] T012 测试通过
  - [deps] T012

- [X] T014 [P] Red: failing test for trace recorder (sub-path timings + agent/judgment/ledger refs + TTL + redact)
  - [路径] backend/tests/unit/orchestration/test_trace_recorder.py
  - [AC] 记录 sub_path_timings/agent_outputs_ref/ledger_ref；TTL 设置；配置关闭正文时只保留 ID/状态/耗时/错误（FR-011/FR-012/蓝图 §20）

- [X] T015 Green: implement trace_recorder.py
  - [路径] backend/src/rag_mcp/orchestration/trace_recorder.py
  - [AC] T014 测试通过
  - [deps] T014

- [X] T016 [P] Red: failing test for state envelope / run record (fields conform schema)
  - [路径] backend/tests/unit/orchestration/test_state_envelope.py
  - [AC] agentic_retrieval_run 含 project_scope/completion_status/guardrail_state/rounds/agent_outputs_ref/ledger_ref/schema_valid_all，符合 agentic-retrieval-run.schema.json（FR-010/FR-031）

- [X] T017 Green: implement state_envelope.py
  - [路径] backend/src/rag_mcp/orchestration/state_envelope.py
  - [AC] T016 测试通过
  - [deps] T016

## Phase 3: User Story 1 — 复杂与多跳查询被拆解后获更完整证据 (Priority: P1) 🎯 MVP

**Story goal**: 多跳查询被拆解为可追溯子问题、选信号/关系方向，并行检索覆盖完整意图。
**Independent test**: 多跳 Java 调用图/DDL 查询上 Agent 路径 Recall@K > 确定性单轮；sub_problem_id 单调；信号/方向记录可追溯。

- [X] T018 [P] [US1] Red: failing test for query planner decomposition (sub_problems monotonic, signals, directions, schema-valid)
  - [路径] backend/tests/unit/agents/test_query_planner.py
  - [AC] 多跳查询→≥1 子问题；sub_problem_id 从 1 单调；signals⊆{dense,sparse,graph}；relation_directions⊆004 成对；schema_valid=true（FR-001/FR-032/FR-033）

- [X] T019 [US1] Green: implement query_planner.py
  - [路径] backend/src/rag_mcp/agents/query_planner.py
  - [AC] T018 测试通过；单意图查询→1 子问题、无额外开销
  - [deps] T018

- [X] T020 [P] [US1] Red: failing test for relation-direction selection respecting 004 bidirectional default + fallback
  - [路径] backend/tests/unit/agents/test_query_planner_directions.py
  - [AC] 默认方向=calls+called_by / fk_references+fk_referenced_by；非法选择→回退 004 确定性双向默认（FR-033）

- [X] T021 [US1] Green: implement direction selection + fallback
  - [路径] backend/src/rag_mcp/agents/query_planner.py
  - [AC] T020 测试通过
  - [deps] T020

- [X] T022 [US1] Red: failing integration test wiring planner into state machine step 3 (run record populates query_planner output)
  - [路径] backend/tests/integration/test_us1_planner_integration.py
  - [AC] 步骤 3 调用 planner；agent_outputs_ref.query_planner.sub_problems 写入运行记录；步骤 4 并行检索使用子问题查询（蓝图 §12）

- [X] T023 [US1] Green: wire planner into state_machine.py step 3
  - [路径] backend/src/rag_mcp/orchestration/state_machine.py
  - [AC] T022 测试通过
  - [deps] T022

## Phase 4: User Story 2 — 证据缺口触发有界补充检索 (Priority: P1)

**Story goal**: 可验证缺口触发有界补充检索；补充候选重新进入融合/Rerank/分析；缺口/冲突显式不臆造。
**Independent test**: 缺口查询→补充轮召回遗漏证据；最终 Recall@K > 单轮基线；缺口判断+轮次在账本可追溯；达上限→partial。

- [X] T024 [P] [US2] Red: failing test for evidence analyst judgment (固化 enums, conflict surfaced, schema-valid)
  - [路径] backend/tests/unit/agents/test_evidence_analyst.py
  - [AC] 输出 coverage_state/conflict_type 固化枚举；uncovered_sub_problem_ids；needs_supplementary；项目/公共冲突并列返回不臆造；schema_valid=true（FR-013/FR-015/FR-032）

- [X] T025 [US2] Green: implement evidence_analyst.py
  - [路径] backend/src/rag_mcp/agents/evidence_analyst.py
  - [AC] T024 测试通过
  - [deps] T024

- [X] T026 [P] [US2] Red: failing test for agent_judgment store (persist + round_index + schema conform)
  - [路径] backend/tests/unit/orchestration/test_judgment_store.py
  - [AC] 判断持久化；round_index 单调；model_and_version 记录；符合 agent-judgment.schema.json

- [X] T027 [US2] Green: implement judgment_store.py
  - [路径] backend/src/rag_mcp/orchestration/judgment_store.py
  - [AC] T026 测试通过
  - [deps] T026

- [X] T028 [US2] Red: failing integration test for supplementary loop (6→3→4→5→6→7; controller owns continue)
  - [路径] backend/tests/integration/test_us2_supplementary_loop.py
  - [AC] rounds_completed≤max_rounds(2)；补充候选重新进入融合/Rerank/分析并携带分数；达上限→partial 含缺口；确定性控制器（非 Agent）决定继续（FR-005/FR-014/宪法 VI）

- [X] T029 [US2] Green: implement loop in state_machine.py
  - [路径] backend/src/rag_mcp/orchestration/state_machine.py
  - [AC] T028 测试通过
  - [deps] T028, T019

- [X] T030 [US2] Red: failing test for mixed-mechanism terminal decision (hard metrics + analyst judgment → controller four-state)
  - [路径] backend/tests/unit/orchestration/test_terminal_decision.py
  - [AC] partial 携带已验证证据+未覆盖+冲突+失败路径；无生成内容填补缺口；四态可区分（FR-015/FR-016/SC-011）

- [X] T031 [US2] Green: implement terminal decision in state_machine.py
  - [路径] backend/src/rag_mcp/orchestration/state_machine.py
  - [AC] T030 测试通过
  - [deps] T030

## Phase 5: User Story 3 — 最终上下文去重、保多样、可解释 (Priority: P1)

**Story goal**: 最终上下文去重/保多样/父级补充/装箱；每条证据可追溯；选择清单追加不改账本。
**Independent test**: 重叠证据→无重复、≥1/来源、截断有可展开 ID；账本原始未改写；(request_id,evidence_id) 解析。

- [X] T032 [P] [US3] Red: failing test for context orchestrator (dedup, diversity, parent scope, binning, selection list, schema-valid)
  - [路径] backend/tests/unit/agents/test_context_orchestrator.py
  - [AC] 无重复；保留≥1/来源；装箱 top_k≤20；selection_list decision∈{selected,truncated,deduped}；truncated→可展开 evidence_id；schema_valid=true（FR-017/FR-018/FR-032）

- [X] T033 [US3] Green: implement context_orchestrator.py
  - [路径] backend/src/rag_mcp/agents/context_orchestrator.py
  - [AC] T032 测试通过
  - [deps] T032

- [X] T034 [P] [US3] Red: failing test for context_selection_list store (append-only, no ledger overwrite)
  - [路径] backend/tests/unit/orchestration/test_context_selection.py
  - [AC] 选择清单只 INSERT；context_result_id+decision 枚举；账本原始条目未改写；符合 schema（FR-008/FR-017）

- [X] T035 [US3] Green: implement context_selection.py
  - [路径] backend/src/rag_mcp/orchestration/context_selection.py
  - [AC] T034 测试通过
  - [deps] T034

- [X] T036 [US3] Red: failing integration test wiring orchestration into step 8 + MCP serialization bridge (output unchanged)
  - [路径] backend/tests/integration/test_us3_orchestration_integration.py
  - [AC] 步骤 8 产出上下文；search_knowledge 输出 Schema 合法（additionalProperties:false 不违反）；账本可凭 (request_id,evidence_id) 解析（FR-024/SC-004）

- [X] T037 [US3] Green: wire step 8 + serialization bridge
  - [路径] backend/src/rag_mcp/orchestration/state_machine.py + backend/src/rag_mcp/mcp/（仅桥接，不改对外契约）
  - [AC] T036 测试通过
  - [deps] T036

## Phase 6: User Story 4 — Agent 编排经对照评测证明收益后进入默认路径 (Priority: P2)

**Story goal**: Agent 编排对照评测；≥3% 受益子集 + 001 非劣 + 002/004 非回归 + 硬性指标全过→进默认路径，否则可选路径。
**Independent test**: 对照报告产出；逐查询可解释；三段通过判定；默认路径决策；可重复。

- [X] T038 [P] [US4] Red: failing test for agentic comparison eval report (metrics + per-query + three_gate_pass)
  - [路径] backend/tests/unit/eval/test_agentic_comparison.py
  - [AC] 报告含 Recall@K/MRR/nDCG/P50/P95/cost + per_query_comparison（确定性 vs Agent 排名+判断+账本引用）+ three_gate_pass（sc001/sc002/sc015/hard_metrics）+ enters_default_path（FR-026/FR-028/FR-029）

- [X] T039 [US4] Green: implement agentic_comparison.py eval runner
  - [路径] backend/src/rag_mcp/eval/agentic_comparison.py
  - [AC] T038 测试通过
  - [deps] T038

- [X] T040 [P] [US4] Red: failing test for eval batch composition (≥6, categories, ≥1 zh, JSON format)
  - [路径] backend/tests/eval/test_agentic_eval_batch.py + eval/agentic_eval_dataset.json
  - [AC] ≥6 条；多跳/缺口/冲突各≥2；≥1 中文；JSON 符合 001 eval 格式（FR-027）

- [X] T041 [US4] Green: create eval batch dataset
  - [路径] eval/agentic_eval_dataset.json
  - [AC] T040 测试通过
  - [deps] T040

- [X] T042 [US4] Red: failing integration test for same-session rerun-baseline-then-agentic fairness + repeatability
  - [路径] backend/tests/integration/test_us4_eval_fairness.py
  - [AC] 同会话先重跑确定性基线再跑 Agent；非延迟指标 1% 容差内一致；延迟环境敏感（FR-030/SC-008）

- [X] T043 [US4] Green: implement fairness orchestration
  - [路径] backend/src/rag_mcp/eval/agentic_comparison.py
  - [AC] T042 测试通过
  - [deps] T042

## Phase N: Polish & Cross-Cutting Concerns

- [X] T044 [P] Red: failing test for cross-project isolation hardening (single-scope→no leakage; no-scope→reject)
  - [路径] backend/tests/integration/test_cross_project_isolation.py
  - [AC] 单作用域查询不返回他项目证据/账本/判断；无 project_scope→拒绝；泄漏事件=0（FR-021/FR-022/SC-003/宪法硬约束）

- [X] T045 Green: harden isolation across ledger/judgment/selection/run
  - [路径] backend/src/rag_mcp/orchestration/*.py
  - [AC] T044 测试通过
  - [deps] T044

- [X] T046 [P] Red: failing test for prompt-injection defense (untrusted→evidence field only; schema-valid; high-risk isolated auditable)
  - [路径] backend/tests/integration/test_prompt_injection_defense.py
  - [AC] 恶意上传不能改控制流/工具选择/Prompt；结构化边界隔离；Agent 输出 Schema 校验（FR-019/FR-020/宪法 V）

- [X] T047 Green: harden injection defense
  - [路径] backend/src/rag_mcp/agents/base.py + backend/src/rag_mcp/orchestration/
  - [AC] T046 测试通过
  - [deps] T046

- [X] T048 [P] Red: failing test for concurrency isolation (5 concurrent reqs, different project_scope, no crosstalk)
  - [路径] backend/tests/integration/test_concurrency_isolation.py
  - [AC] 5 并发→无状态/账本/作用域串扰（FR-025/SC-013）

- [X] T049 Green: verify/fix concurrency isolation
  - [路径] backend/src/rag_mcp/orchestration/state_machine.py
  - [AC] T048 测试通过
  - [deps] T048

- [X] T050 [P] Red: failing test for hard-metric gates (schema 100% + locatability 100% + leakage 0 on acceptance suite)
  - [路径] backend/tests/contract/test_hard_metrics.py
  - [AC] 100% Schema 合法；100% 可定位；0 泄漏（宪法硬约束/蓝图 §24.2）

- [X] T051 Green: ensure hard-metric gates via MCP bridge
  - [路径] backend/src/rag_mcp/mcp/（桥接，不改对外契约）
  - [AC] T050 测试通过
  - [deps] T050

- [X] T052 Red: failing E2E test for DeepSeek Harness (agentic search_knowledge + get_evidence end-to-end, schema-valid, 30s<host timeout)
  - [路径] backend/tests/e2e/test_deepseek_harness_e2e.py
  - [AC] DH 端到端调用成功；输出 Schema 合法；30s 护栏<目标 Host 最低 Tool Call 超时（SC-012）

- [X] T053 Green: fix E2E issues
  - [路径] backend/src/rag_mcp/mcp/
  - [AC] T052 测试通过
  - [deps] T052

- [X] T054 [P] Red: failing test for run-state TTL + no-writeback-to-KB + tracing redaction
  - [路径] backend/tests/unit/orchestration/test_run_state_lifecycle.py
  - [AC] TTL 设置；Agent 推理结果不写回知识库；配置关闭正文时只留 ID/状态/耗时/错误（FR-011/FR-012/蓝图 §20）

- [X] T055 Green: implement TTL + redaction
  - [路径] backend/src/rag_mcp/orchestration/trace_recorder.py + state_envelope.py
  - [AC] T054 测试通过
  - [deps] T054

- [X] T056 Run quickstart.md validation scenarios end-to-end
  - [路径] specs/005-.../quickstart.md
  - [AC] 场景 1–7 全部可观测通过
  - [deps] T053, T055

## Dependencies & Execution Order

### Phase Dependencies
- **Setup (Phase 1)**: 无依赖，可立即开始。
- **Foundational (Phase 2)**: 依赖 Setup；阻塞全部 User Story。
- **US1 (Phase 3)**: 依赖 Phase 2；无其他故事依赖（MVP）。
- **US2 (Phase 4)**: 依赖 Phase 2 + US1（查询规划携带缺口上下文重调）。
- **US3 (Phase 5)**: 依赖 Phase 2 + US2（账本已有证据）。
- **US4 (Phase 6)**: 依赖 US1–US3（完整 Agent 路径）。
- **Polish (Phase N)**: 依赖 US1–US4。

### Within Each User Story
- 每个功能单元：Red→Green 串行（Green [deps] Red）。
- 同故事内不同文件/无依赖的 Red 可 [P] 并行（如 US1 的 T018 与 T020）。

### Serial Critical Path
T001→T003→(Phase 2 骨架 T012→T013)→T018→T019→T022→T023→T028→T029→T036→T037→T038→T039→T056

## Parallel Opportunities
- Phase 1：T002 [P]（独立配置文件）。
- Phase 2：T004/T006/T008/T010/T014/T016 互不依赖文件，可并行（[P]）；各自 Green 串行其 Red。
- US1：T018、T020 并行（不同测试文件，[P]）。
- US4：T038、T040 并行（[P]）。
- Polish：T044/T046/T048/T050/T054 多为不同关注点、不同文件，可并行（[P]）。

## Parallel Example: User Story 1

```text
# 并行启动 US1 的 Red 测试（不同文件、无依赖）
# T018 (test_query_planner.py)  ||  T020 (test_query_planner_directions.py)
# 待各自 Red 失败后，串行实现 Green：T019 → T021，再 T022 → T023（集成）
```

## MVP Scope

Phase 1 + Phase 2 + Phase 3（US1 查询规划）即可演示“多跳查询被拆解后获更完整证据”的独立价值（sub_problem 可追溯、信号/方向记录、并行检索覆盖）。US2/US3/US4 为后续增量。

## Phase 7: Convergence

> 收敛评估（/speckit-converge，2026-09-03）：对照 spec.md/plan.md/constitution.md 评估当前代码后识别的剩余工作。
> 现状：结构层（Agent 抽象/Schema 校验/降级/账本与运行记录模型/对照判定逻辑/真实 LLM 调用）已全绿（1064 passed），
> 但证据流水线（真实检索接入）、持久化落库、MCP 桥接与真实对照评测尚未贯通。以下任务按 CRITICAL→HIGH→MEDIUM→LOW 排序。

- [ ] T057 CRITICAL 桥接 MCP：search_knowledge 在 AGENTIC_RETRIEVAL_ENABLED=true 时路由至 Agent 编排路径（missing）
  - [路径] backend/src/rag_mcp/mcp/search_knowledge.py + backend/src/rag_mcp/orchestration/（新增编排入口）
  - [AC] 开关 OFF 时行为与 001 逐字节一致（确定性默认路径不变）；开关 ON 时 search_knowledge 经状态机返回；对外响应 Schema 不变（additionalProperties:false 不违反，FR-024）；partial 终态经既有 gaps/error 字段携带未覆盖/冲突/失败路径（FR-016）；request_id 可桥接内部账本（FR-024/SC-004/SC-012）
  - [证据] mcp/ 与 server.py 中 0 处 agentic 引用；T037 AC 声明的"仅桥接"未落地
  - [deps] 无（可先行）

- [ ] T058 CRITICAL 状态机步骤 4/5 接入真实检索：并行检索（Dense/Sparse/图）+ 融合/Rerank（missing）
  - [路径] backend/src/rag_mcp/orchestration/state_machine.py（复用 services/retrieval_service.py、fusion/rrf.py、graph/expansion.py）
  - [AC] 每个子问题查询经 002 混合检索（QdrantStore dense+sparse + RRF + Rerank）召回；图信号按 planner 选择经 004 图扩展（沿用 004 护栏）；单来源最大证据 3/上限 5 强制（FR-006）；召回候选携带检索器/得分/来源/版本进入账本输入；补充轮候选重新进入融合/Rerank/分析（FR-005/FR-014）；US1/US2 独立测试可度量（Recall@K 可计算）
  - [证据] state_machine.py 第 261–267 行：_step_parallel_retrieval/_step_fusion_rerank 仅记录步骤名
  - [deps] 无（可与 T057 并行）

- [ ] T059 HIGH 持久化贯通：账本/判断/选择清单/运行记录在运行中落库（partial）
  - [路径] backend/src/rag_mcp/orchestration/state_machine.py（接线 ledger.py、judgment_store.py、context_selection.py、models.py）
  - [AC] 每条召回证据写入 evidence_ledger_entry（检索查询/检索器/得分/版本/来源/round_index/sub_problem_id，隔离三元组，追加式，FR-008/FR-009）；每轮 evidence_analyst 判断写入 agent_judgment；步骤 8 选择决策写入 context_selection_list；运行结束写入 agentic_retrieval_run（FR-031）；(request_id, evidence_id) 桥接键可解析（SC-006）；并发 5 请求落库无串扰（SC-013）
  - [证据] state_machine.py 中 0 处 store/model 写入调用
  - [deps] T058

- [ ] T060 HIGH 评测集真值化：agentic_eval_dataset.json 对齐真实语料（partial）
  - [路径] eval/agentic_eval_dataset.json（+ 必要时补充评测语料/入库）
  - [AC] ≥6 条（多跳/缺口/冲突各≥2、含≥1 中文，FR-027）；每条 expected_evidence_ids 解析为评测项目已发布版本中真实存在的 chunk（Java 调用图/DDL 外键/混合检索代码语料）；查询可被真实索引召回且排名差异可度量；遵循 001 AI 生成+人工审核+JSON 约定；原 001/002/004 查询保留逐条可比
  - [证据] 当前 expected_evidence_ids 为占位 ID，未绑定真实语料
  - [deps] 无（可与 T057/T058 并行）

- [ ] T061 HIGH 真实对照评测运行器 + 报告产出（missing）
  - [路径] eval/run_agentic_comparison.py（复用 eval/run_graph_comparison.py 模式 + rag_mcp/eval/agentic_comparison.py）
  - [AC] 同一环境会话先重跑确定性基线再跑 Agent 编排（FR-030）；在 001 11 条 + 002/004 扩充集 + 005 新增批次上计算 Recall@K/MRR/nDCG/P50/P95/成本；逐查询记录双路径排名、Agent 判断（子问题/信号/方向/缺口/补充轮次/编排决策）、子路径耗时与账本引用（FR-028/SC-009）；三段通过判定（SC-001 ≥3%/SC-002/SC-015）+ 硬性指标；产出 eval/agentic_comparison_report.json（含 enters_default_path，FR-029/宪法 X）；可重复性 1% 容差校验（SC-008）
  - [证据] eval/ 下无 agentic_comparison_report.json；AgenticComparisonRunner 仅接收给定指标，无真实执行
  - [deps] T057, T058, T059, T060

- [ ] T062 HIGH 状态机迁移至 LangGraph（partial）
  - [路径] backend/src/rag_mcp/orchestration/state_machine.py（langgraph 已在依赖）
  - [AC] 九步主流程与补充循环由 LangGraph StateGraph 管理节点流转（FR-004、plan 决策）；跳转权仍属确定性控制器（宪法 VI）；护栏/隔离/四态行为不变；既有全部状态机与集成测试通过
  - [证据] 全源码 0 处 langgraph 引用；FR-004 MUST"LangGraph 管理状态机"
  - [deps] T058（在真实流水线上迁移）

- [ ] T063 MEDIUM 子路径耗时与成本记录接入运行流（partial）
  - [路径] backend/src/rag_mcp/orchestration/state_machine.py + trace_recorder.py
  - [AC] 每次运行记录 dense/sparse/graph/fusion/rerank/三 Agent 节点耗时与总成本至运行记录（FR-031/SC-007）；TTL 与脱敏配置生效（FR-011/FR-012）
  - [证据] TraceRecorder 未在执行流中使用；sub_path_timings 恒为空对象
  - [deps] T058

- [ ] T064 MEDIUM 提示注入检测与标记（partial）
  - [路径] backend/src/rag_mcp/agents/（新增检测模块）+ orchestration/
  - [AC] 可疑注入内容被检测并标记（可审计）；高风险片段从内部控制 Prompt 隔离但保留来源（FR-020）；检索文本不能改变状态跳转/工具选择/模型权限（FR-019/宪法 V）；检测失败不阻塞检索（检测为辅助，结构隔离为边界）
  - [证据] 全源码 0 处注入检测实现；当前仅有 Schema 校验边界
  - [deps] 无

- [ ] T065 MEDIUM 上下文编排父级上下文补充（partial）
  - [路径] backend/src/rag_mcp/agents/context_orchestrator.py
  - [AC] 需父级上下文的证据按需纳入父级范围（复用 001 父级回填能力）；父级补充不超装箱上限（FR-017/US3-AC2）；决策进入选择清单可追溯
  - [证据] context_orchestrator._orchestrate 仅去重/多样/装箱，无父级补充
  - [deps] T058

- [ ] T066 MEDIUM 005 运行期表 TTL 清理（partial）
  - [路径] backend/src/rag_mcp/services/maintenance_service.py
  - [AC] 周期清理 agentic_retrieval_run/evidence_ledger_entry 过期行（含级联的 agent_judgment/context_selection_list）；运行状态不进入向量库/不写回知识库（FR-011/SC-014/蓝图 §20）；清理不影响未过期运行与他项目数据
  - [证据] services/ 中 0 处 005 表清理逻辑
  - [deps] T059

- [ ] T067 LOW 查询规划跳数上限选择（partial）
  - [路径] backend/src/rag_mcp/agents/query_planner.py + contracts/agentic-retrieval-run.schema.json
  - [AC] planner 可选图扩展跳数上限（004 的 1~3 护栏内）并经 Schema 校验；非法/缺失回退 004 默认 2（FR-033）；契约新增字段向后兼容（宪法 VII）
  - [证据] planner 输出与契约均无跳数字段
  - [deps] T058

- [ ] T068 HIGH 端到端验收：quickstart 场景 1–7 + 硬性指标套件（missing）
  - [路径] backend/tests/integration/（真实服务端套件）+ specs/005-.../quickstart.md
  - [AC] 启动真实服务（AGENTIC_RETRIEVAL_ENABLED=true）后 quickstart 场景 1–7 全部可观测通过；验收套件：跨项目泄漏=0、MCP Schema 合法率 100%、来源可定位率 100%（宪法硬约束/蓝图 §24.2）；DeepSeek Harness 经 MCP 端到端 search_knowledge/get_evidence 通过且 30s 护栏 < Host 超时（SC-012）；对照报告结论（enters_default_path）记录于报告
  - [证据] 现有 T052/T056 测试为单元级模拟，未在真实服务端验证
  - [deps] T057, T058, T059, T061

