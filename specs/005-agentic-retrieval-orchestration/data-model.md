# Data Model: Agentic Retrieval Orchestration (005)

**Branch**: `005-agentic-retrieval-orchestration` | **Date**: 2026-09-02 | **Spec**: [spec.md](./spec.md) | **Research**: [research.md](./research.md)

> 数据模型扩展依据蓝图 §13（追加式证据账本、公共状态包络、节点专属 Schema）、§12（确定性状态机与护栏）、§14（证据充分性与终态）、§11（三 Agent 角色）、§8.2（PostgreSQL 检索运行/证据账本索引/追踪记录）、§20（运行状态 TTL、不写回知识库）。005 复用 001/002/003/004 既有表（项目、知识源、版本、Chunk、图关系、入库任务、运行配置、检索运行/证据账本），**只新增 Agent 编排运行期表**，不改既有表结构与对外 MCP 契约（FR-024，宪法原则 VII）。下列 DDL 为模型级草图，完整迁移脚本属 tasks/实现阶段。运行期表使用 TTL，不进入向量库，不写回项目知识库（蓝图 §20）。

---

## 1. 实体总览

| 实体 | 物理表 | 说明 | 来源 FR |
|------|--------|------|---------|
| 证据账本条目（Evidence Ledger Entry） | `evidence_ledger_entry` | 追加式账本原始记录，`ledger_entry_id` 雪花 ID；携带检索查询/检索器/得分/版本/来源/轮次/子问题 | FR-008/FR-009/FR-032 |
| Agent 判断（Agent Judgment） | `agent_judgment` | 证据分析 Agent 每轮结构化判断：覆盖度/冲突/缺口/是否补充 | FR-013/FR-015/FR-032 |
| 上下文选择清单（Context Selection List） | `context_selection_list` | 上下文编排 Agent 追加式选择记录：选中/截断/去重，不改写账本 | FR-017/FR-032 |
| Agent 编排检索运行（Agentic Retrieval Run） | `agentic_retrieval_run` | 单次 Agent 编排检索运行记录 + 公共状态包络 + 护栏状态 + 子路径耗时 + Agent 输出引用 + 账本引用 | FR-010/FR-031 |
| 知识版本能力清单（Capabilities） | （**不新增标志**） | Agent 编排为运行时路径，通过运行配置开关启用/禁用，不新增 `agentic_ready` 能力标志（spec Assumptions、research §0.3） | FR-024 |
| 对照评测报告（Comparison Report） | `eval_comparison_report` 扩展 | Agent 编排指标 + 逐查询 Agent 判断 + 三段通过判定 | FR-026/FR-028/FR-029 |

---

## 2. evidence_ledger_entry（追加式证据账本）

### 2.1 字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `ledger_entry_id` | TEXT | PK, `^[0-9]+$` (Snowflake) | 账本条目唯一标识，系统生成（research §3） |
| `request_id` | TEXT | NOT NULL, FK→retrieval_run | 所属检索请求（沿用 001 RequestId） |
| `run_id` | TEXT | NOT NULL | Agent 编排运行标识（`request_id` + `run_id` 双键隔离，FR-025） |
| `round_index` | INTEGER | NOT NULL, ≥0 | 产生轮次（0=首轮，≥1=补充检索轮次，FR-032） |
| `sub_problem_id` | INTEGER | NOT NULL, ≥1 | 所属拆解子问题（`run_id` 内单调递增，FR-032/FR-009） |
| `evidence_id` | TEXT | NOT NULL, FK→evidence | 该账本条目指向的证据（沿用 001 EvidenceId） |
| `retrieval_query` | TEXT | NOT NULL | 召回该证据的检索查询（蓝图 §13） |
| `retriever` | TEXT | NOT NULL, CHECK in (dense, sparse, graph, fusion, rerank) | 召回该证据的检索器 |
| `score` | NUMERIC(6,4) | NOT NULL, ∈ [0,1] | 相关性/融合/Rerank 分数（蓝图 §13） |
| `source_version` | INTEGER | NOT NULL, ≥1 | 知识源版本号（SourceVersion） |
| `source_position` | TEXT | NOT NULL | 来源位置路径（Markdown 章节/Java 全限定符号，沿用 001） |
| `knowledge_scope_id` | TEXT | NOT NULL, FK→knowledge_scope | 隔离字段（宪法硬约束） |
| `project_id` | TEXT | NOT NULL | 隔离字段 |
| `index_version` | INTEGER | NOT NULL | 隔离字段 |
| `referenced_by_agent` | TEXT | NOT NULL, CHECK in (query_planner, evidence_analyst, context_orchestrator) | 引用/评价/筛选该证据的 Agent 角色（FR-032） |
| `created_at` | TIMESTAMPTZ | NOT NULL | 写入时间（TTL 清理依据，DB 审计列） |
| `ttl_expires_at` | TIMESTAMPTZ | NULL | TTL 到期时间（蓝图 §20） |

### 2.2 索引

- `idx_ledger_run` on (`run_id`, `round_index`, `sub_problem_id`) — 按运行/轮次/子问题分组追溯（SC-009）。
- `idx_ledger_request_evidence` on (`request_id`, `evidence_id`) — 对外 `(request_id, evidence_id)` 桥接键解析（research §3，不改对外契约）。
- `idx_ledger_scope` on (`knowledge_scope_id`, `project_id`, `index_version`, `created_at`) — 跨项目隔离校验 + TTL 清理。

### 2.3 校验规则

- **追加式不变量**（FR-008）：表只允许 INSERT，禁止 UPDATE/DELETE（除 TTL 到期清理）；Agent 选择/筛选不得改写本表，仅可在 `context_selection_list` 记录决策。
- `ledger_entry_id` MUST 为雪花 ID（`^[0-9]+$`）；`round_index`/`sub_problem_id` MUST 在同一 `run_id` 内单调。
- 账本条目隔离三元组 `(knowledge_scope_id, project_id, index_version)` MUST 与所属请求 `project_scope` 一致（跨项目泄漏=0，宪法硬约束 FR-022）。
- `evidence_id` MUST 属于同一隔离三元组（跨项目证据不得写入，FR-022）。
- 追踪配置关闭正文时，`retrieval_query` 可存空串占位但 ID/状态/耗时保留（FR-012）。

---

## 3. agent_judgment（证据分析 Agent 判断）

### 3.1 字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `judgment_id` | TEXT | PK, `^[0-9]+$` | 判断记录唯一标识 |
| `run_id` | TEXT | NOT NULL | 所属运行 |
| `round_index` | INTEGER | NOT NULL, ≥0 | 该判断所属轮次 |
| `coverage_state` | TEXT | NOT NULL, CHECK in (covered, partial, uncovered) | 覆盖度状态枚举（FR-032） |
| `conflict_type` | TEXT | NOT NULL, CHECK in (none, version_conflict, source_conflict, domain_conflict) | 冲突类型枚举（FR-032） |
| `uncovered_sub_problem_ids` | JSONB | NOT NULL | 未覆盖子问题 `sub_problem_id` 数组（FR-013） |
| `needs_supplementary` | BOOLEAN | NOT NULL | 是否需要补充检索（确定性控制器消费） |
| `gap_descriptions` | JSONB | NOT NULL | 缺口描述数组（含 description/suggested_action） |
| `model_and_version` | TEXT | NOT NULL | 证据分析 Agent 模型与版本（能力路由记录） |
| `schema_valid` | BOOLEAN | NOT NULL | 节点 Schema 校验结果（FR-003，false 触发降级） |
| `created_at` | TIMESTAMPTZ | NOT NULL | 写入时间 |

### 3.2 校验规则

- `coverage_state` 与 `conflict_type` MUST ∈ 固化枚举（FR-032）；`schema_valid=false` 时确定性控制器 MUST 回退到该角色确定性等价行为（SC-011）。
- **控制权不变量**（FR-013/宪法 VI）：`needs_supplementary` 为 Agent 的判断**输入**，确定性控制器据此决定是否继续检索；Agent 不得独占状态机跳转。
- 项目与公共证据冲突时 `conflict_type` ≠ none，系统并列返回两类证据并标明知识域身份（FR-016，宪法 III）。

---

## 4. context_selection_list（上下文编排追加式选择清单）

### 4.1 字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `context_result_id` | TEXT | NOT NULL | 所属上下文编排结果（`run_id` 内稳定标识，FR-032） |
| `run_id` | TEXT | NOT NULL | 所属运行 |
| `ledger_entry_id` | TEXT | NOT NULL, FK→evidence_ledger_entry | 被选择/截断/去重的账本条目 |
| `decision` | TEXT | NOT NULL, CHECK in (selected, truncated, deduped) | 选择决策枚举（FR-032/FR-017） |
| `created_at` | TIMESTAMPTZ | NOT NULL | 写入时间 |

**主键**：(`context_result_id`, `ledger_entry_id`) — 同一编排结果对同一账本条目唯一决策。

### 4.2 校验规则

- **追加式不变量**（FR-008/FR-017）：本表只 INSERT，不改写 `evidence_ledger_entry`；`decision` 记录选中/截断/去重决策，使上下文编排可审计（SC-006）。
- `decision=deduped` 时该账本条目对应证据未进入最终上下文（去重）；`truncated` 时因装箱上限未进入（FR-018）；`selected` 时进入最终上下文。

---

## 5. agentic_retrieval_run（Agent 编排检索运行 + 公共状态包络）

记录单次 Agent 编排检索运行的状态、护栏、子路径耗时、Agent 输出引用与账本引用，支持问题回溯（FR-010/FR-031）。

### 5.1 字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `run_id` | TEXT | PK, `^[0-9]+$` | 运行唯一标识 |
| `request_id` | TEXT | NOT NULL | 所属检索请求（沿用 001，对外桥接键） |
| `project_scope` | JSONB | NOT NULL | 显式项目作用域数组（宪法 I） |
| `knowledge_scope_ids` | JSONB | NOT NULL | 解析后的知识作用域列表 |
| `task_context` | JSONB | NULL | 调用方任务上下文（仅作提示，FR-007） |
| `run_config` | JSONB | NOT NULL | 运行配置与模型能力要求（护栏、模型路由、Agent 开关） |
| `completion_status` | TEXT | NOT NULL, CHECK in (complete, partial, no_evidence, failed) | 终态（蓝图 §14 四态） |
| `max_rounds` | INTEGER | NOT NULL, default 2 | 最大检索轮次护栏（上限 3，FR-006） |
| `rounds_completed` | INTEGER | NOT NULL, ≥0 | 实际完成轮次 |
| `guardrail_state` | JSONB | NOT NULL | 当前护栏状态：节点超时 5s/10s、装箱上限 top_k≤20、单来源上限 3/5、总超时 30s、图护栏沿用 004（跳数 2/3、预算 10/20、子超时 3s） |
| `sub_path_timings` | JSONB | NOT NULL | 各子路径（Dense/Sparse/图扩展/融合/Rerank/各 Agent 节点）耗时（FR-031） |
| `agent_outputs_ref` | JSONB | NOT NULL | 各 Agent 判断引用：`{query_planner: {sub_problems[]}, evidence_analyst: judgment_id[], context_orchestrator: context_result_id[]}` |
| `ledger_ref` | JSONB | NOT NULL | 账本引用：`{ledger_entry_ids[], rounds: [{round_index, sub_problem_ids[], judgment_id}]}` |
| `total_cost` | NUMERIC(10,4) | NULL | 单次调用 LLM 成本（SC-007） |
| `schema_valid_all` | BOOLEAN | NOT NULL | 所有 Agent 节点 Schema 校验是否通过（任一 false 触发降级记录） |
| `created_at` | TIMESTAMPTZ | NOT NULL | 写入时间 |
| `ttl_expires_at` | TIMESTAMPTZ | NOT NULL | TTL 到期时间（蓝图 §20） |

### 5.2 索引

- `idx_run_request` on (`request_id`) — 对外桥接（`request_id` → `run_id`）。
- `idx_run_scope` on (`knowledge_scope_ids`, `created_at`) — 作用域隔离与 TTL 清理。

---

## 6. 状态机

### 6.1 补充检索有界循环（FR-005/FR-014，确定性控制器拥有跳转权）

```
round 0: 步骤1 接收校验 → 2 解析作用域 → 3 查询规划(拆解 sub_problem_id, 选信号/方向)
  → 4 并行检索(Dense/Sparse/图) → 5 融合+Rerank → 6 证据分析(覆盖度/冲突/缺口)
        │
        │ (缺口 AND rounds_completed < max_rounds AND 确定性控制器决定继续)
        ▼
round N+1: 3' 查询规划(携带缺口上下文, 生成补充查询) → 4 → 5 → 6
        │
        │ (无缺口 OR rounds_completed == max_rounds)
        ▼
步骤 8: 上下文编排(去重/多样/父级补充/装箱, 记录 context_selection_list)
  → 步骤 9: MCP 响应序列化(不改对外契约)
```

**跳转规则（确定性，宪法 VI）**：

| 跳转 | 触发条件 | 后续 |
|------|----------|------|
| 继续→补充轮次 | 证据分析输出 `needs_supplementary=true` 且 `rounds_completed < max_rounds` 且确定性控制器判定继续 | 查询规划 Agent 携带缺口上下文重新被调用（步骤 3'），召回候选重新进入融合/Rerank/分析 |
| 进入上下文编排 | 无缺口（`coverage_state=covered`）或 `rounds_completed == max_rounds` | 步骤 8 |
| 降级回退 | 某 Agent 节点输出未通过 Schema 校验 / 节点超时 / Agent 编排关闭 | 该角色回退确定性等价行为，仍返回有效四态（SC-011） |

**禁止**：LLM 独占"继续/返回"跳转（宪法 VI）；无界循环（违背 §12 护栏）。

### 6.2 终态（蓝图 §14，沿用 001）

```
complete ──(充分覆盖, 无未解决冲突)
partial  ──(有可靠证据但存在明确缺口/部分路径失败)  必须携带 已验证证据+未覆盖问题+冲突+失败路径
no_evidence ──(正常执行, 无可靠证据)
failed ──(系统异常, 无法形成有效响应)
```

---

## 7. 关系图（ER 摘要）

```
retrieval_run (001) ──1:1── agentic_retrieval_run (005, run_id, 公共状态包络)
                                    ├──< evidence_ledger_entry (追加式, ledger_entry_id, round_index, sub_problem_id)
                                    ├──< agent_judgment (每轮, coverage/conflict 枚举)
                                    └──< context_selection_list (追加式, context_result_id, decision)

knowledge_scope (001) ─┬─< chunk (001/003, 节点身份)
                       ├─< graph_edge/soft_relation (004, 图扩展信号)
                       └─ agentic 路径经运行配置开关启用, 不新增能力标志
```

**对外不变**（FR-024，宪法 VII）：`search_knowledge`/`get_evidence` 输出 Schema 不改；`evidence` 项 `additionalProperties: false` 不新增字段；账本/判断为内部追踪契约，以输出 `request_id`+`evidence_id` 为桥接键。

**重建与生命周期**（蓝图 §20）：Agent 运行状态（`agentic_retrieval_run`/`agent_judgment`/`context_selection_list`/`evidence_ledger_entry`）使用 TTL，不进入向量库，不写回项目知识库；Agent 推理结果不自动写回。账本与运行记录不要求从原始知识源重建（运行期状态非知识源）。
