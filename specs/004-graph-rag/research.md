# Research: Graph RAG (004)

**Branch**: `004-graph-rag` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md)

**Scope Basis**: 蓝图 §23.4.4（004 纵向交付）、§10（双层图谱）、§8.2（PostgreSQL 图节点/边/硬软关系 + 一至三跳递归查询）、§8.3（不引入 Neo4j）、§9（图关系为混合检索第 3 信号）、§5（能力清单 `graph_ready`）、§24（评测驱动）、§12（护栏）、§19（超时）。

> 本文件为 `/speckit-plan` Phase 0 产出。所有 `[NEEDS CLARIFICATION]` 已在 spec.md 澄清阶段闭合；本文固化技术决策与相对基线评测目标。宪法原则 X 要求增强须证明收益且不违反硬性指标后才进入默认检索路径——故下列**评测目标闸门**置于全文之首，未达成则不得进入 plan。

---

## 0. 相对 001 基线的评测目标（进入 plan 的闸门）

依据 spec §"对照对象（基线）"、FR-021/FR-022/FR-024/FR-025 与 SC-001/SC-002/SC-013，以及宪法原则 X 与蓝图 §24.3，图增强相对两档基线的期望变化与通过判定如下。**research.md 必须先声明此节，否则不得进入 plan**——本节即该声明。

### 0.1 基线值（已记录于 eval 工件）

| 指标 | 001 Dense 基线（`eval/baseline_report.json`） | 002 混合基线（`eval/hybrid_comparison_report.json`） |
|------|----------------------------------------------|-----------------------------------------------------|
| 评测集查询数 | 11（Markdown+Java） | 18（Markdown+Java，含中文/词汇精确） |
| Recall@K (mean, K=5) | 1.0 | 1.0 |
| MRR (mean) | 0.9091 | 0.9722 |
| nDCG@K (mean) | 0.9329 | 0.9795 |
| 延迟 P50 | 138.45 ms | 152.40 ms |
| 延迟 P95 | 185.15 ms | 189.34 ms |
| 嵌入/Rerank | BAAI/bge-m3 | bge-m3 + RRF + BAAI/bge-reranker-v2-m3 |

### 0.2 期望变化（图增强 = 混合检索 + 图关系扩展）

| 指标 | 相对 001 Dense（11 条） | 相对 002 混合基线 |
|------|--------------------------|-------------------|
| **Recall@K** | 持平不下降：1.0 → 1.0（精确，Recall 不可下降） | 结构性受益子集：不下降（1.0→1.0）；002 原有非结构性查询：不下降 |
| **MRR (mean)** | 非劣（1% 相对容差）：≥ 0.9001；结构性 Java 查询（如 validateToken）期望由 #2 升至 #1，子集均值期望正向 | 结构性受益子集相对提升 **≥ 3%**（子集均值相对增量 ≥ 3%）；002 原有非结构性查询非劣（1% 相对容差） |
| **nDCG@K (mean)** | 非劣（1% 相对容差）：≥ 0.9236 | 结构性受益子集相对提升 **≥ 3%**；002 原有非结构性查询非劣（1% 相对容差） |
| **P50** | 记录对照（不作为非劣闸门，延迟环境敏感） | 允许因图扩展步骤上升；同会话重跑混合基线后对照，**增量公平记录**；P50 期望增量 < ~60 ms |
| **P95** | 记录对照 | 允许上升但 **P95 < 30s 总超时护栏且 < 目标 Host 最低 Tool Call 超时**（蓝图 §19/FR-017） |

### 0.3 进入默认检索路径的三段通过判定（FR-024 / SC-001 / SC-002 / SC-013）

1. **结构性受益子集提升闸口**（SC-001）：扩充评测集中"结构性受益"查询子集（新增 ≥ 6 条、含 ≥ 1 条中文，FR-021）的 MRR 与 nDCG 均值相对 002 混合基线**相对提升 ≥ 3%**，且该子集 Recall@K 不下降。
2. **001 非回归闸口**（SC-002）：001 Markdown/Java 11 条基线上 Recall@K 精确持平、MRR/nDCG 非劣（1% 相对容差，容差值在 research.md §6 声明 = 0.01）。
3. **002 非结构性非回归闸口**（SC-013）：002 评测集原有 18 条中非结构性受益部分 MRR/nDCG 非劣（1% 相对容差）、Recall@K 不下降。

**硬性指标（必须全过，蓝图 §24.2）**：跨项目泄漏事件 = 0；MCP `search_knowledge`/`get_evidence` 输出 Schema 合法率 = 100%；证据来源可定位率 = 100%。

**未达阈值处置**：结构性子集未达 ≥ 3% 相对提升 → 图扩展作为**可选检索路径**保留、**不进入默认路径**（宪法原则 X）；硬性指标任一未过 → 阻塞发布（宪法硬约束）。

### 0.4 可重复性边界（SC-007）

同一环境连续两次运行：Recall@K、MRR、nDCG 在 1% 相对容差内一致；P50/P95 标注为环境敏感、不作为否决项。对照评测须在同一环境会话内先重跑混合基线、再运行图增强路径（FR-025，延迟增量公平）。

---

## 1. 决策：PostgreSQL 图存储与递归 CTE 选型

- **Decision**: 图节点、图边、硬关系与软关系全部存于 PostgreSQL（蓝图 §8.2）。图节点不独立建表——节点即已切片 Chunk，节点身份 = `chunk_id`（沿用 001/003 Chunk 标识，common.schema.json `ChunkId`，Snowflake ID `^[0-9]+$`）。图边以 `graph_edge`（硬关系）与 `soft_relation`（软关系，含五项元数据与四态状态）两表承载，均带 `(knowledge_scope_id, project_id, index_version)` 隔离三元组。一至三跳扩展用 `WITH RECURSIVE` 递归 CTE 实现，护栏在 CTE 内以 `LIMIT`/`depth` 谓词与结构权重排序截断。
- **Rationale**: 蓝图 §8.2/§8.3 明确首期不引入 Neo4j，PostgreSQL 关系表 + 递归查询即可完成一至三跳；节点=Chunk 复用既有标识避免双写一致性成本（§8.4 跨存储一致 `chunk_id`）；递归 CTE 在 1~3 跳、候选预算 ≤ 20 的护栏内性能可控（§12 护栏 + §19 超时双保险）。
- **Alternatives**:
  - Neo4j：蓝图 §8.3/§26 仅在"PostgreSQL 一至三跳无法满足质量或性能"时才迁移，首期触发条件不满足，拒绝。
  - 独立 graph_node 表：与 chunk 一一映射且无独立属性，徒增双写一致性成本，拒绝；节点身份直接用 `chunk_id`。
  - 物化视图预计算全跳数：违背"图派生数据可从原始知识源重建"（§8.4）且扇出爆炸，拒绝；运行时递归查询 + 护栏截断。
- **性能依据**: 递归 CTE 在 `(source_chunk_id, relation_type, direction)` 与 `(knowledge_scope_id, project_id, index_version)` 上的 B-tree 复合索引 + `depth` 谓词 + `LIMIT` 截断，使 1~3 跳查询在候选预算 ≤ 20、子超时 3s 护栏内完成（FR-017）；高扇出节点按结构权重全局排序截断（候选预算为**总预算**非逐跳，spec FR-017 澄清）。

## 2. 决策：图信号作 RRF 第 3 路输入的融合机制

- **Decision**: 图扩展候选进入 **RRF/DBSF 融合池作为第 3 路输入**（与 Dense、Sparse 候选同池），融合后统一 Cross-Encoder Rerank——对齐蓝图 §9 信号顺序，消解 spec FR-006"叠加在…之上"与 §9 顺序的歧义（spec 澄清 Q5）。RRF 融合分数 = `Σ_retrievers 1/(k_rrf + rank_r)`，其中 graph 作为第 3 个 retriever 贡献其按结构权重排序后的 rank。结构权重（`structure_weight`）用于图扩展候选的**内部排序与截断**（决定 graph rank），不作为独立融合系数叠加——保持 RRF 的 rank-only 语义与确定性（宪法原则 VI）。
- **Rationale**: RRF rank-only 融合天然处理异构检索器且无需归一化分数尺度，图候选以 rank 形式并入最自然；统一 Rerank 保证最终排序可解释、可重复（SC-007/SC-008）；结构权重只影响图候选的内部次序与是否进入候选池，不引入随机或 LLM 主观加权，符合确定性控制优先。
- **Alternatives**:
  - Rerank 后按结构权重加权叠加（字面 FR-006 旧措辞）：绕过 Rerank 可解释性、与 §9 顺序冲突，已否决（spec 澄清）。
  - 双层接入（既进融合池又在 Rerank 后二次加权）：双重计权导致排序不可解释、确定性下降，拒绝。
  - 把结构权重作为独立融合系数：需调参且引入主观尺度，违背 RRF rank-only 简洁性，拒绝。
- **结构权重默认值**: `structure_weight` 默认按关系类型与跳数衰减：硬关系 `calls/called_by/fk_references/fk_referenced_by` 起始权重 1.0、每跳衰减 0.5（2 跳 = 0.5、3 跳 = 0.25）；软关系 `inferred` 起始权重 0.3（低权重补充，FR-005）、同样跳数衰减。该默认值可在运行配置覆盖（config 键 `structure_weight_hard`/`structure_weight_soft`/`structure_weight_hop_decay`），但软关系权重 MUST 低于硬关系（宪法原则 III/VI）。最终数值在评测校准后固化于运行配置（§6）。

## 3. 决策：图扩展护栏参数（候选预算语义、跳数、超时）

- **Decision**: 采纳 spec 澄清结果——跳数默认 2/上限 3；候选预算为**单次图扩展总预算**（默认 10/上限 20，按结构权重全局排序截断后并入融合，非逐跳）；图扩展查询子超时默认 3s；单次调用总超时沿用 001/002/003 的 30s 且 MUST < 目标 Host 最低 Tool Call 超时（蓝图 §19）。默认关系方向为双向遍历成对关系类型（`calls`+`called_by`、`fk_references`+`fk_referenced_by`），由确定性配置决定、不由 LLM 独占（宪法原则 VI）。
- **Rationale**: 总预算语义贴合蓝图 §12"按结构权重截断、保留最高相关可定位证据"的单次护栏；双向默认服务 User Story 1"召回调用者与被调用者"；确定性方向避免 005 Agent 未交付时的控制权悬空。
- **Alternatives**: 逐跳预算（累计可达 30~60）易突破延迟/证据预算，已否决（spec 澄清 Q1）；LLM 决定方向（属 005）违反宪法 VI，已否决（spec 澄清 Q2）。

## 4. 决策：软关系 LLM 推断（离线入库期）

- **Decision**: 软关系在**离线入库期**由 LLM 推断（蓝图 §10.2），与 Feature 005 运行时三 Agent 语义判断不同。推断产出**四态生命周期** `inferred`→`active`→`superseded`→`retired`（spec FR-003）；`active`→`superseded` 转换由**确定性规则**触发（同 `(source_chunk_id, target_chunk_id, relation_type)` 三元组且新推断置信度更高，或同对出现硬关系即取代），**不由 LLM 独占判定**（宪法原则 VI，spec 澄清 Q3）。软关系 MUST 携带五项必填元数据（推断来源、置信度、模型与版本、生成时间、支撑证据 ID）且与硬关系可区分、不得冒充项目事实。
- **模型/Prompt 选型**: 沿用宪法架构约束"Python/LangGraph/LangChain 编排基线 + Provider 接口允许本地 CPU/GPU/远程 API"。推断 LLM 通过 Provider 路由层选择，**默认走本地 LLM**（与 bge-m3/bge-reranker 同源本地优先策略），具体模型名与 Prompt 模板在运行配置中声明并记录于 `model_and_version` 元数据字段；004 规格不绑定具体模型版本（避免实现细节泄漏），只约束五项元数据与可区分/不冒充硬事实。
- **置信度阈值**: `active` 要求置信度 ≥ 阈值且经支撑证据 ID 校验；**默认阈值 0.6**（0~1 标度），低于阈值的软关系不进入默认检索路径或仅作低权重补充（FR-005）。阈值可由运行配置覆盖；最终值在评测校准后固化（§6）。软关系起始结构权重 0.3 < 硬关系 1.0（§2）。
- **Alternatives**:
  - 运行时三 Agent 推断软关系：属 Feature 005，004 范围外（spec 范围外声明），拒绝。
  - LLM 决定 supersede 转换：违反宪法原则 VI（LLM 不得独占状态机转换），已否决（spec 澄清 Q3）。
  - 软关系升级为硬关系：违反宪法原则 III（硬关系只能由确定性解析产生），拒绝。

## 5. 决策：edge_id 标识格式与图边契约

- **Decision**: `edge_id` 采用 Snowflake ID 字符串形式（与 `chunk_id`/`knowledge_scope_id` 一致的 `^[0-9]+$` 模式，common.schema.json `ChunkId` 同型），由系统在写入图边时生成。图边规范标识字段固化为：`edge_id`、`source_chunk_id`、`target_chunk_id`、`relation_type`(枚举 `{calls, called_by, fk_references, fk_referenced_by, other_hard, inferred}`)、`direction`(out|in)、`is_hard`(bool)、`version`(SourceVersion)、`knowledge_scope_id`/`project_id`/`index_version` 隔离字段；硬关系额外带确定性解析依据；软关系额外带五项元数据 + 四态状态。完整契约见 [graph-relations.schema.json](../003-structured-asset-expansion/contracts/graph-relations.schema.json)。
- **Rationale**: Snowflake ID 与既有标识同型，保证全链路可定位与一致（宪法原则 IV）；`is_hard` + `relation_type=inferred` 双重区分硬/软（宪法原则 III）；`other_hard` 保留框架扩展占位（首期 Java 调用图 + DDL 外键，蓝图 §10.1 其余硬关系为后续批次）。
- **Alternatives**: UUID 字符串：与 `chunk_id` 的 Snowflake 数值串模式不一致，跨存储关联更繁，拒绝；自然键（source+target+type 复合主键）：无法承载同对多版本/多推断实例且违反"edge_id 独立可定位"，拒绝。

## 6. 决策：graph_ready 能力门控与容差值固化

- **Decision**: 知识版本能力清单扩展 `graph_ready`（蓝图 §5）。声明 `graph_ready` 的版本 MUST 在图关系（硬关系，声明软关系时含软关系）就绪后才可变为可检索状态（FR-013）；`graph_ready` 隐含 `dense_ready` + `lexical_ready`（同一 bge-m3 嵌入与切片策略上的派生能力，FR-015 不触发宪法原则 VIII 不可混用）。查询规划只调用已发布版本明确声明的能力；未声明 `graph_ready` 的版本 MUST NOT 参与图扩展路径但继续支持 Dense/混合检索（FR-014）。已有混合能力版本通过用户触发重建、发布声明 `graph_ready` 的新版本实现，不自动批量迁移（FR-027）。
- **容差值固化**: 非延迟指标可重复性容差 = **0.01（1% 相对容差）**（SC-007）；001/002 非劣判定容差同此值（SC-002/SC-013）。该值在本 research.md 声明以满足 spec"容差按 research.md 声明"的约束。
- **重建**: 全部图派生数据可从原始知识源与版本信息重建（蓝图 §8.4，FR-016）；清空操作先标记不可检索、再异步删除图关系（蓝图 §5）。
- **Alternatives**: 自动批量迁移已发布混合版本获 graph_ready：违反"失败保护与显式重建"（FR-027），拒绝；graph_ready 不隐含 dense/lexical：图扩展叠加于混合检索之上、无 Dense/Sparse 无意义，拒绝。

## 7. 决策：Java 调用图 / DDL 外键确定性提取（复用 001/003）

- **Decision**: 硬关系从**已切片 Chunk** 出发确定性提取，不重新解析/切片。Java 调用图复用 001 的 Java 符号感知切片（全限定符号路径），由确定性 AST 分析提取 `calls`/`called_by` 边（类/函数调用、方法调用、API 实现与调用）。DDL 外键复用 003 的 DDL 表/字段/约束感知切片，由确定性 DDL 解析提取 `fk_references`/`fk_referenced_by` 边（表、字段与外键，蓝图 §10.1）。提取失败/降级时报告并说明原因，不伪造关系（Edge Case，宪法原则 III）。
- **Rationale**: 001/003 已交付切片能力，004 只在其 Chunk 上提取图关系，避免重复实现（spec Assumptions）；AST/DDL 解析为确定性，硬关系标记为可验证证据（宪法原则 IV）。
- **Alternatives**: 重新实现解析器：违背复用原则且属范围外，拒绝；LLM 推断调用关系：属软关系范畴且非确定性，硬关系只能由确定性解析产生，拒绝。

## 8. 决策：目标 Host 兼容性

- **Decision**: 沿用 001 参考客户端策略——DeepSeek Harness 为本机已安装的必过参考客户端（端到端 `search_knowledge`/`get_evidence` 调用与输出 Schema 校验 MUST 通过，SC-012）；ChatGPT App 与 Claude Code 记录兼容性状态、MUST NOT 作为 004 验收阻塞项。图增强 30s 总超时护栏 MUST < 三者中最低 Tool Call 超时预算（蓝图 §19，FR-028）。
- **Rationale**: 004 不修改 001 已确立的 MCP 对外契约（FR-011，宪法原则 VII）；图扩展证据通过既有契约增补可区分的硬/软关系标注返回。
- **Alternatives**: 将 ChatGPT App/Claude Code 列为必过：违背 001 参考客户端策略且本机不一定安装，拒绝。

---

## 9. 宪法原则 X 例外登记

无例外。所有增强（图扩展）均在固定评测集上证明收益（≥ 3% 相对提升 + 三段非回归）且不违反硬性指标后才进入默认检索路径；未达阈值则作为可选路径保留、不进入默认路径。无静默例外（宪法 Governance 要求）。

## 10. NEEDS CLARIFICATION 残留

无。spec.md 澄清阶段（Session 2026-08-28 + 2026-09-01）已闭合全部未决项；本文 §1–§8 的技术决策均由蓝图章节、spec FR/SC 与宪法原则直接支撑，无悬空假设。延迟到运行配置/评测校准的数值（结构权重衰减、置信度阈值 0.6、容差 0.01）已在 §2/§4/§6 给出默认值并声明可覆盖边界。
