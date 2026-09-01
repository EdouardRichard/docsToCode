# Data Model: Graph RAG (004)

**Branch**: `004-graph-rag` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md) | **Research**: [research.md](./research.md)

> 数据模型扩展依据蓝图 §8.2（PostgreSQL 拥有图节点/图边/硬关系/软关系）、§8.4（跨存储一致 `chunk_id`/`knowledge_scope_id`/`project_id`/`index_version`）、§10（双层图谱）、§5（能力清单 `graph_ready`）。004 复用 001/002/003 既有表（项目、知识源、版本、Chunk、入库任务、运行配置、检索运行/证据账本），**只新增图关系相关表与能力清单扩展**，不改既有表结构与对外 MCP 契约（FR-011，宪法原则 VII）。下列 DDL 为模型级草图，完整迁移脚本属 tasks/实现阶段。

---

## 1. 实体总览

| 实体 | 物理表 | 说明 | 来源 FR |
|------|--------|------|---------|
| 图节点（Graph Node） | （虚拟，= Chunk） | 节点身份 = `chunk_id`，沿用 001/003 Chunk 标识；不独立建表 | FR-001/Key Entities |
| 图边（Graph Edge，硬关系） | `graph_edge` | 确定性解析产生的关系边，`is_hard=true` | FR-001/FR-002 |
| 软关系（Soft Relation） | `soft_relation` | LLM 离线推断，`is_hard=false`/`relation_type=inferred`，五项元数据 + 四态 | FR-003/FR-004/FR-005 |
| 图关系扩展路径（Graph Expansion Path） | `graph_expansion_path`（检索运行子表） | 从起点 Chunk 经若干边到达证据的跳序列 | FR-008/FR-023 |
| 知识版本能力清单（Capabilities） | 扩展 `knowledge_capabilities`（002 表） | 新增 `graph_ready` 标志 | FR-013/FR-014/FR-015 |
| 对照评测报告（Comparison Report） | `eval_comparison_report` 扩展 | 图增强指标 + 逐查询图扩展路径 + 三段通过判定 | FR-022/FR-023/FR-024 |

---

## 2. graph_edge（硬关系）

### 2.1 字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `edge_id` | TEXT | PK, `^[0-9]+$` (Snowflake) | 图边唯一标识，系统生成（research §5） |
| `knowledge_scope_id` | TEXT | NOT NULL, FK→knowledge_scope | 隔离字段（宪法硬约束） |
| `project_id` | TEXT | NOT NULL | 隔离字段 |
| `index_version` | INTEGER | NOT NULL | 隔离字段（同嵌入模型+切片策略的索引版本） |
| `source_chunk_id` | TEXT | NOT NULL, FK→chunk | 调用方/外键引用方节点 |
| `target_chunk_id` | TEXT | NOT NULL, FK→chunk | 被调用方/被引用方节点 |
| `relation_type` | TEXT | NOT NULL, CHECK in 硬关系枚举 | `{calls, called_by, fk_references, fk_referenced_by, other_hard}`（软关系用 `inferred`，见 soft_relation） |
| `direction` | TEXT | NOT NULL, CHECK in (out,in) | 边方向 |
| `is_hard` | BOOLEAN | NOT NULL, = true（本表恒真） | 硬关系标记 |
| `version` | INTEGER | NOT NULL, ≥1 | 知识源版本号（SourceVersion） |
| `parse_evidence` | JSONB | NOT NULL | 确定性解析依据（AST 节点位置/DDL 约束名等），使关系可审计（宪法 IV） |
| `created_at` | TIMESTAMPTZ | NOT NULL | 写入时间（DB 审计列，不纳入 graph-relations 契约对象，实现序列化图边时剔除该列） |

### 2.2 索引

- `idx_graph_edge_source` on (`knowledge_scope_id`, `project_id`, `index_version`, `source_chunk_id`, `relation_type`, `direction`) — 递归 CTE 正向扩展主索引（FR-006/FR-007）。
- `idx_graph_edge_target` on (`knowledge_scope_id`, `project_id`, `index_version`, `target_chunk_id`, `relation_type`, `direction`) — 反向扩展（called_by/fk_referenced_by）。
- `uniq_graph_edge` UNIQUE on (`knowledge_scope_id`, `index_version`, `source_chunk_id`, `target_chunk_id`, `relation_type`, `direction`, `version`) — 同版本同对同类型不重复写。

### 2.3 校验规则

- `relation_type` MUST ∈ 硬关系枚举（软关系 `inferred` 由 soft_relation 表承载，本表禁止）。
- `source_chunk_id`/`target_chunk_id` MUST 属于同一 `(knowledge_scope_id, project_id, index_version)`（跨项目图边不得写入，宪法硬约束 FR-010）。
- `parse_evidence` MUST 非空且含确定性解析定位（不得为 LLM 推断依据，宪法 III/VI）。
- 写入由确定性 AST/DDL 解析触发；提取失败/降级时报告原因、不写入伪造边（Edge Case）。

---

## 3. soft_relation（软关系）

### 3.1 字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `edge_id` | TEXT | PK, `^[0-9]+$` | 软关系唯一标识 |
| `knowledge_scope_id` | TEXT | NOT NULL | 隔离字段 |
| `project_id` | TEXT | NOT NULL | 隔离字段 |
| `index_version` | INTEGER | NOT NULL | 隔离字段 |
| `source_chunk_id` | TEXT | NOT NULL, FK→chunk | 推断关系起点 |
| `target_chunk_id` | TEXT | NOT NULL, FK→chunk | 推断关系终点 |
| `relation_type` | TEXT | NOT NULL, = `inferred` | 软关系固定类型 |
| `direction` | TEXT | NOT NULL, in (out,in) | 推断方向 |
| `is_hard` | BOOLEAN | NOT NULL, = false | 软关系标记 |
| `version` | INTEGER | NOT NULL, ≥1 | 知识源版本号 |
| `inference_source` | TEXT | NOT NULL | 推断来源（五项元数据 1） |
| `confidence` | NUMERIC(4,3) | NOT NULL, ∈ [0,1] | 置信度（五项元数据 2） |
| `model_and_version` | TEXT | NOT NULL | 模型与版本（五项元数据 3） |
| `generated_at` | TIMESTAMPTZ | NOT NULL | 生成时间（五项元数据 4） |
| `supporting_evidence_ids` | JSONB | NOT NULL | 支撑证据 ID 数组（五项元数据 5） |
| `lifecycle_state` | TEXT | NOT NULL, CHECK in (inferred,active,superseded,retired) | 四态生命周期（FR-003） |
| `superseded_by` | TEXT | NULL | 取代本软关系的 edge_id（active→superseded 时填，可追溯） |
| `superseded_at` | TIMESTAMPTZ | NULL | 进入 superseded 的时间 |

### 3.2 索引

- `idx_soft_relation_pair` on (`knowledge_scope_id`, `index_version`, `source_chunk_id`, `target_chunk_id`, `relation_type`, `lifecycle_state`) — supersede 判定主索引。
- `idx_soft_relation_active` on (`knowledge_scope_id`, `project_id`, `index_version`, `lifecycle_state`) WHERE `lifecycle_state='active'` — 默认检索路径低权重补充查询。

### 3.3 状态机（四态，FR-003 / research §4）

```
inferred ──(置信度≥阈值0.6 且 支撑证据ID校验通过)──▶ active
   │                                                  │
   │ (不达阈值/无支撑证据)                              │ (同对出现硬关系) or
   │                                                   │ (同三元组新推断置信度更高)
   ▼                                                   ▼
retired ◀──(版本撤销/清空删除)                      superseded
```

**转换规则（确定性，不由 LLM 独占，宪法 VI）：**

| 转换 | 触发条件 | 后续 |
|------|----------|------|
| `inferred→active` | 置信度 ≥ 0.6（默认阈值）且 `supporting_evidence_ids` 非空且经校验 | 进入默认检索路径作低权重补充（结构权重 0.3） |
| `inferred→retired` | 版本撤销/清空删除 | 不参与检索 |
| `active→superseded` | 同 `(source_chunk_id,target_chunk_id,relation_type)` 三元组的新推断置信度更高（旧者转 superseded、新者转 active），或同对出现硬关系（软关系直接 superseded，硬关系为准） | 保留可追溯（`superseded_by` 指向新者/硬关系 edge_id），不参与检索 |
| `superseded→retired` | 版本撤销/清空删除 | 不参与检索 |
| `active→retired` | 版本撤销/清空删除 | 不参与检索 |

**禁止转换**：软关系 → 硬关系（宪法 III，硬关系只能由确定性解析产生）。

### 3.4 校验规则

- 五项元数据（`inference_source`/`confidence`/`model_and_version`/`generated_at`/`supporting_evidence_ids`）MUST 全部非空（FR-003）。
- `lifecycle_state='active'` MUST 满足置信度 ≥ 阈值且支撑证据校验通过；不满足则不得进入默认检索路径（FR-005）。
- 软关系不得静默覆盖同对硬关系：MCP 返回时两者并列、可区分标注（FR-004，宪法 III）。
- 低置信度（< 阈值）或缺支撑证据的软关系 MUST NOT 进入默认检索路径，或仅作低权重补充。

---

## 4. graph_expansion_path（图关系扩展路径，检索运行子表）

记录单次图增强检索中每条图扩展召回证据的跳序列，使"为什么召回这条证据"可解释（FR-008/FR-023/SC-008）。沿用 001/002/003 检索运行/证据账本表，新增图扩展路径子结构。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `request_id` | TEXT | NOT NULL, FK→retrieval_run | 所属检索请求（沿用 001 RequestId） |
| `evidence_id` | TEXT | NOT NULL, FK→evidence | 图扩展召回并最终返回的证据 |
| `chunk_id` | TEXT | NOT NULL, FK→chunk | 该证据对应的候选 Chunk（与 evidence 双向关联，桥接运行追踪 `graph_candidates`，FR-026） |
| `start_chunk_id` | TEXT | NOT NULL | 扩展起点 Chunk |
| `edge_path` | JSONB | NOT NULL | 跳序列数组：`[{hop, edge_id, relation_type, direction, is_hard}, …]`（结构与 graph-expansion-trace HopStep 一致） |
| `hop_count` | INTEGER | NOT NULL, ∈ [1,3] | 实际跳数（护栏内） |
| `structure_weight` | NUMERIC(6,4) | NOT NULL | 累计结构权重（按关系类型+跳数衰减，research §2） |
| `graph_rank` | INTEGER | NOT NULL, ≥1 | 图候选内部排名（并入 RRF 第 3 路用） |

**与运行追踪的桥接（DM-1 修复）**：本表为"图扩展召回并存活为返回证据"的持久行，按 `(request_id, evidence_id)` 定位；运行追踪 `graph-expansion-trace.graph_candidates[]` 为"图扩展召回的全部候选"（融合/Rerank 前），按 `chunk_id` 定位并携带可空 `evidence_id`。两者经 `chunk_id`↔`evidence_id` 双向关联：候选存活为证据时其 `evidence_id` 被回填至追踪项，同时本表写入对应 `chunk_id`，使 FR-023/FR-026/SC-008 的"图扩展路径↔回证据"可追溯。

校验：`hop_count` MUST ≤ 3（护栏上限）；`edge_path` 每跳的 edge MUST 属于同一请求作用域 `(knowledge_scope_id, project_id, index_version)`（跨项目泄漏=0，宪法硬约束）；`chunk_id` MUST 等于 `evidence_id` 所指证据的来源 Chunk。

---

## 5. knowledge_capabilities 扩展（graph_ready）

复用 002 `knowledge-capabilities.schema.json` 定义的对象，新增 `graph_ready` 布尔字段（蓝图 §5）。契约见 [knowledge-capabilities.graph-extension.schema.json](../003-structured-asset-expansion/contracts/knowledge-capabilities.graph-extension.schema.json)。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `dense_ready` | BOOLEAN | required, true 才可发布 | 001 已立，002 沿用 |
| `lexical_ready` | BOOLEAN | 隐含 dense_ready=true | 002 已立 |
| `graph_ready` | BOOLEAN | 隐含 dense_ready=true 且 lexical_ready=true；声明 true 时图关系须就绪 | 004 新增 |

**门控规则**：
- `graph_ready=true` MUST 隐含 `dense_ready=true` ∧ `lexical_ready=true`（图扩展叠加于混合检索之上，FR-015 不触发宪法 VIII 不可混用）。
- 声明 `graph_ready` 的版本在图关系（硬关系，声明软关系时含软关系）就绪后才可变为可检索状态（FR-013）；未就绪版本不变为可检索（蓝图 §8.4 不暴露半成品版本）。
- 未声明 `graph_ready` 的版本 MUST NOT 参与图扩展路径，但继续支持 Dense/混合检索（FR-014）。
- 已有混合能力版本通过用户触发重建、发布声明 `graph_ready` 的新版本，不自动批量迁移（FR-027）。

---

## 6. 对照评测报告扩展

复用 002 `eval_comparison_report` 结构，扩展为图增强对照报告（`report_type=graph_enhanced_comparison`），新增图扩展配置、三段通过判定、逐查询图扩展路径分数。契约见 [eval-graph-comparison-report.schema.json](../003-structured-asset-expansion/contracts/eval-graph-comparison-report.schema.json)。关键字段：

- `config.graph_*`：跳数/候选预算/子超时/方向/结构权重默认值（research §2/§3）。
- `three_gate_pass`：SC-001 提升闸口 / SC-002 001 非劣闸口 / SC-013 002 非结构性非劣闸口 三段判定结果。
- `per_query_comparison[].graph_*`：图增强排名、graph_rank、structure_weight、graph_recall_hop_count、`graph_edge_path_summary`（HopStep 数组，含 relation_type，DM-2 修复，FR-023/SC-008 可解释性）。

---

## 7. 关系图（ER 摘要）

```
knowledge_scope ─┬─< chunk (节点身份, 001/003)
                 ├─< graph_edge (硬关系, is_hard=true)
                 ├─< soft_relation (软关系, is_hard=false, 4-state)
                 └─< knowledge_capabilities (dense/lexical/graph_ready)

retrieval_run ──< graph_expansion_path (per evidence: hop序列, structure_weight, graph_rank)
            └── evidence (沿用 001, 增补 hard/soft relation 标注, 不改对外契约)
```

**跨存储一致**（蓝图 §8.4）：`knowledge_scope_id`/`project_id`/`chunk_id`/`index_version` 在 PostgreSQL 与 Qdrant 间一致；图边只引用既有 `chunk_id`，不引入新标识空间。

**重建**（蓝图 §8.4/FR-016）：`graph_edge` 可从原始 Java/DDL 知识源 + 版本信息经确定性解析重建；`soft_relation` 可从原始知识源 + 同模型/Prompt 重推断重建（`model_and_version` 记录使其可复现）。
