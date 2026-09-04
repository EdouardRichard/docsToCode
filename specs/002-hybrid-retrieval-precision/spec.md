# Feature Specification: Hybrid Retrieval Precision

**Feature Branch**: `002-hybrid-retrieval-precision`

**Created**: 2026-08-27

**Status**: Specified

**Input**: User description: "Qdrant BM25/Sparse + RRF 融合 + bge-reranker，与 001 Dense 对照。范围依据：蓝图 §23.4.2 / §9 / §8.1 / §18.2。"

**Scope Basis**: 蓝图 §23.4.2（002 纵向交付 Feature 定义）、§9（混合检索链路）、§8.1（Qdrant 职责）、§18.2（本地默认模型）。

## 对照对象（基线）

本 Feature 的对照评测对象为 **001 Dense 检索基线**（Feature `001-minimum-rag-mcp-loop`），其基线数据记录于 `eval/baseline_report.json`：

| 指标 | 001 Dense 基线值 |
|------|----------------|
| Recall@K（mean, K=5） | 1.0 |
| MRR（mean） | 0.9091 |
| nDCG@K（mean） | 0.9329 |
| 延迟 P50 | 138.45 ms |
| 延迟 P95 | 185.15 ms |
| 评测集查询数 | 11 |
| 嵌入模型 | BAAI/bge-m3 |
| Qdrant 集合 | `chunks_dense_bge-m3_v1`（Dense-only） |

基线中查询 5（`Find the definition of com.example.service.UserService#validateToken.`）的期望证据排在第 2 位（MRR 贡献 0.5），原因是 Dense 相似度分数中类级 Chunk（0.6151）略高于方法级 Chunk（0.6133），语义相似度无法稳定区分精确符号。这正是 BM25/Sparse 词汇精确匹配与 Rerank 精排要解决的问题。002 在原 11 条基础上扩充词汇精确查询以增强排名质量提升的可度量性（见 FR-019），原 11 条保留用于与基线逐条对照。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 精确符号与关键词查询获得更高排名 (Priority: P1)

外部 Agent（ChatGPT App、DeepSeek Harness、Claude Code）以类名、方法名、错误码、表名、字段名、Endpoint 或配置项等精确标识发起检索时，系统通过 BM25/Sparse 词汇精确召回与 Dense 语义召回结合，使携带该精确标识的证据排在更靠前的位置，而不是仅靠语义相似度模糊排序。

**Why this priority**: 这是 002 相对 001 Dense 基线的核心增量价值。001 基线已暴露 Dense 在精确符号查询上的排序弱点（验证用例 validateToken 排在第 2 位）。词汇精确匹配是蓝图 §9 明确的混合检索信号之一，直接决定 Agent 是否能一次拿到正确证据。

**Independent Test**: 在固定评测集上对精确标识类查询运行混合检索，验证期望证据排名第一的比例高于 001 Dense 基线（基线 validateToken 用例排名第一失败），且不引入跨项目证据。

**Acceptance Scenarios**:

1. **Given** 一个已发布且声明 `dense_ready` 与 `lexical_ready` 能力的知识版本，**When** Agent 携带显式项目作用域查询包含精确全限定符号路径（如 `com.example.Service#methodName`）的查询，**Then** 系统同时执行 Dense 与 Sparse/BM25 召回，且包含该精确符号的证据在融合后排名不劣于纯 Dense 结果。
2. **Given** 查询包含固定类名或错误码等词汇信号，**When** Dense 召回因语义相近而返回多个分数接近的候选，**Then** Sparse/BM25 对包含精确词汇的 Chunk 给出更高词频权重，使融合后正确候选的排名提升。
3. **Given** 某个查询的精确标识只出现在作用域外的项目，**When** Agent 仅携带本项目作用域查询，**Then** 系统不返回作用域外的 Sparse 命中，跨项目泄漏事件数为零。

---

### User Story 2 - 混合检索质量优于 Dense 基线 (Priority: P1)

系统对 Dense 与 Sparse 召回结果执行 RRF/DBSF 融合，并对融合后的有限候选执行 Cross-Encoder Rerank，使最终返回的证据在 MRR 与 nDCG 上可度量地优于 001 Dense-only 基线，同时 Recall@K 不下降、硬性验收指标不被违反。

**Why this priority**: 蓝图 §24.3 与宪法原则 X（评测驱动优化）要求增强必须在固定评测集上证明收益且不违反硬性指标后才能进入默认检索路径。融合与 Rerank 是 §9 检索链路的第 4、5 信号，是 002 的精度主体。

**Independent Test**: 在 001 建立的同一固定评测集（`eval/eval_dataset.json`）上运行完整混合检索路径，生成混合检索报告并与 `baseline_report.json` 对照，验证 MRR 与 nDCG 的均值均有可度量提升，Recall@K 不低于基线，跨项目泄漏为零，Schema 合法率与来源可定位率均为 100%。

**Acceptance Scenarios**:

1. **Given** 001 固定评测集与 Dense 基线报告，**When** 系统在同一评测集上运行 Dense+Sparse 融合 + Rerank 路径，**Then** MRR 与 nDCG 均值相对基线有可度量提升，Recall@K 不低于基线。
2. **Given** 融合后得到候选证据集合，**When** Reranker 对有限候选（不超过配置的 Rerank 候选预算）执行精排，**Then** 最终返回顺序按精排分数确定，且被 Rerank 剔除的低相关候选仍保留可展开的证据 ID。
3. **Given** 单次 `search_knowledge` 调用，**When** 系统执行 Dense 查询、Sparse 查询、融合与 Rerank 全流程，**Then** 单次调用总耗时不超过服务端总超时护栏，P50/P95 延迟记录在评测报告中并与基线对照。
4. **Given** 混合检索路径中 Sparse 或 Rerank 子步骤失败或超时，**When** 已有 Dense 召回的可靠证据，**Then** 系统返回 `partial` 状态并携带已验证证据与失败路径信息，不返回空结果或伪造证据。

---

### User Story 3 - 对照评测可重复且可解释 (Priority: P2)

用户与检索工程师能够在同一固定评测集上重复运行混合检索评测，获得与 001 Dense 基线并排对照的报告，报告包含指标增量、延迟对照、逐查询命中与排名变化，且非延迟指标在重复运行间稳定。

**Why this priority**: 评测驱动是宪法原则 X 的硬约束；可重复、可解释的对照是增强进入默认路径的前提证据。001 基线报告已显示非延迟指标可重复而延迟指标因环境波动不可在 1% 容差内重复——002 必须延续这一可重复性边界。

**Independent Test**: 连续运行两次混合检索评测，验证 Recall@K/MRR/nDCG 在容差内一致（与基线相同的非延迟可重复性要求），并验证对照报告逐查询列出 Dense 基线排名与混合检索排名的差异。

**Acceptance Scenarios**:

1. **Given** 固定评测集与 001 基线报告，**When** 用户运行混合检索评测，**Then** 系统生成包含 Recall@K、MRR、nDCG、P50/P95 延迟与基线逐项增量（delta）的对照报告。
2. **Given** 同一环境连续两次运行混合检索评测，**When** 比较两次非延迟指标，**Then** Recall@K、MRR、nDCG 的相对偏差在容差内（沿用 001 的 1% 容差策略），延迟指标标注为环境敏感、不作为可重复性否决项。
3. **Given** 某查询在 Dense 基线中期望证据排名第 2、在混合检索中排名第 1，**When** 用户查看对照报告，**Then** 报告逐查询显示基线排名与混合排名、Dense 分数、Sparse 分数（或词频权重）、融合分数与 Rerank 分数，可解释排名变化原因。

---

### User Story 4 - 新索引版本声明混合能力且不混用 (Priority: P2)

系统为知识版本发布声明 `dense_ready` 与 `lexical_ready` 能力的混合检索版本；仅声明 `dense_ready` 的旧版本继续以 Dense-only 路径可用；查询规划只调用已发布版本明确声明的检索能力，不混合不同嵌入模型或不兼容切片策略的索引版本。

**Why this priority**: 宪法原则 VIII（知识版本不可混用）与蓝图 §5 索引能力清单是硬约束。002 在同一嵌入模型（BAAI/bge-m3，蓝图 §18.2）与同一切片策略上新增 Sparse/BM25 词法能力，属新增能力而非混用不兼容数据，但必须通过能力清单显式声明与版本隔离来保证不违反原则 VIII。

**Independent Test**: 发布一个混合能力版本，验证其能力清单为 `{dense_ready: true, lexical_ready: true}`，混合检索路径只查询声明 `lexical_ready` 的版本；仅 Dense 的旧版本仍可被 Dense-only 查询命中，且两者的 Chunk 不互相串入。

**Acceptance Scenarios**:

1. **Given** 一个已发布 Dense-only 版本（仅 `dense_ready`），**When** 用户为同一知识源构建并发布包含 Sparse/BM25 词法索引的新版本，**Then** 新版本能力清单声明 `lexical_ready`，发布前 Dense 与 Sparse 索引均已就绪。
2. **Given** 仅声明 `dense_ready` 的版本与声明 `lexical_ready` 的版本同时存在，**When** 查询规划选择检索信号，**Then** 系统只调用当前已发布版本明确声明的能力，未声明 `lexical_ready` 的版本不参与 Sparse 路径。
3. **Given** Sparse/BM25 索引构建失败，**When** 系统尝试发布新版本，**Then** 新版本不变为可检索状态，旧 Dense-only 版本继续可用（沿用 001 的失败保护）。
4. **Given** 所有派生索引（Dense 向量与 Sparse/BM25 词法索引），**When** 用户重建知识源，**Then** 系统能够从原始知识源与版本信息重建全部派生索引（沿用蓝图 §8.4）。

### Edge Cases

- 当 Dense 与 Sparse 召回的候选完全不重叠时，融合必须保留两侧最高相关的可定位证据，不应因一侧为空而丢弃另一侧结果。
- 当查询为纯自然语言、不含任何精确词汇信号时，Sparse/BM25 召回可能弱于 Dense，融合不应让弱词法信号压低语义强证据的排名。
- 当查询为纯精确标识（如裸类名或错误码）且 Dense 语义召回噪声大时，融合必须让词法精确命中获得足够权重。
- 当 Reranker 对某些候选评分打平时，系统必须保留稳定、确定的次序（确定性控制，宪法原则 VI），不得引入随机扰动。
- 当 Sparse 或 Rerank 子步骤超时而 Dense 召回已有可靠证据时，返回 `partial` 并标注超时路径，不得阻塞返回。
- 当同一查询跨多个并发请求调用混合检索时，请求级作用域、证据账本与融合中间状态不得互相污染（沿用 001 并发隔离）。
- 当知识版本声明 `lexical_ready` 但底层 Sparse 索引因数据损坏不可用时，系统必须将该版本视为不可检索或降级为 Dense-only，不得静默返回残缺词法结果。
- 当对照评测重复运行时，非延迟指标必须在容差内一致；延迟指标因环境波动允许超出容差但必须记录并标注。
- 当查询或 Chunk 为中文（CJK）内容时，Sparse/BM25 必须使用 CJK 分词而非朴素空格分词，否则中文词法召回失效；CJK 与英文混合内容必须正确切分两类词法信号。

## Requirements *(mandatory)*

### Functional Requirements

**检索能力与信号**

- **FR-001**: 系统 MUST 在入库流程中为每个 Chunk 构建 Qdrant Sparse/BM25 词法索引，与 001 已建立的 Dense 向量索引并存（蓝图 §8.1：Qdrant 负责 Sparse/BM25 关键词检索）。Sparse/BM25 由 Qdrant 负责构建与检索，不引入新的嵌入模型（蓝图 §18.2：BGE-M3 默认只提供 Dense Embedding）。
- **FR-002**: 系统 MUST 对同一查询并行或顺序执行 Dense 召回与 Sparse/BM25 召回，两条召回路径都强制携带 `knowledge_scope_id`、`project_id` 与 `index_version` 过滤，确保跨项目泄漏为零（宪法硬约束）。
- **FR-003**: 系统 MUST 对 Dense 与 Sparse 召回结果执行 RRF 或 DBSF 融合，融合后保留每条候选的来源检索器、Dense 分数、Sparse 分数（或词频权重）与融合分数（蓝图 §8.1、§9 第 4 项）。
- **FR-004**: 系统 MUST 对融合后的有限候选集执行 Cross-Encoder Rerank，使用本地默认 Reranker `BAAI/bge-reranker-v2-m3`（蓝图 §18.2），Rerank 候选数不超过配置的候选预算护栏（蓝图 §12）。
- **FR-005**: 系统 MUST 使 Reranker 只处理融合后的有限候选，不在线对全库重排，以控制延迟与资源（蓝图 §18.5）。
- **FR-006**: 系统 MUST 在融合与 Rerank 阶段保留公共知识与项目知识的知识域身份，不在融合时丢失来源标记（蓝图 §9：公共知识与项目知识分别召回和标记）。

**作用域与硬约束继承**

- **FR-007**: 混合检索 MUST 继承 001 的显式项目作用域要求：缺少显式 `project_scope` 的项目检索 MUST 被拒绝，不得回退默认全库搜索（宪法硬约束）。
- **FR-008**: 混合检索 MUST 保证跨项目泄漏事件数在验收测试集中为零：Sparse/BM25 命中、Dense 命中、融合候选与 Rerank 结果都不包含作用域外项目的 Chunk（宪法硬约束）。
- **FR-009**: 混合检索的 Tool 响应 MUST 100% 通过 `search_knowledge` 输出 Schema 校验（宪法硬约束）；002 不修改 001 已确立的 MCP 对外契约，只在内部检索路径增强（宪法原则 VII：接口独立演进）。
- **FR-010**: 混合检索返回的每条证据 MUST 携带来源 ID、版本与可定位位置（Markdown 章节路径或 Java 全限定符号路径），来源可定位率在验收测试集中为 100%（宪法硬约束，沿用 001 FR-017）。

**版本与能力声明**

- **FR-011**: 系统 MUST 允许发布声明 `dense_ready` 与 `lexical_ready` 能力的知识版本；声明混合检索能力的版本 MUST 在 Dense 与 Sparse/BM25 索引均就绪后才可变为可检索状态（蓝图 §5 索引能力清单）。
- **FR-012**: 系统 MUST NOT 将不同嵌入模型或不兼容切片策略产生的数据混入同一索引版本（宪法原则 VIII）；002 在同一 bge-m3 嵌入模型与同一切片策略上新增 Sparse/BM25 词法能力，属新增能力而非混用。
- **FR-013**: 查询规划 MUST 只调用当前已发布版本明确声明的能力；仅声明 `dense_ready` 的版本 MUST NOT 参与 Sparse/BM25 路径，但 MUST 继续支持 Dense-only 检索（蓝图 §5：查询规划只能调用已发布版本明确声明的能力）。
- **FR-014**: 系统 MUST 能够从原始知识源与版本信息重建全部派生索引，包括 Dense 向量与 Sparse/BM25 词法索引（蓝图 §8.4、宪法原则 VIII）。

**降级与护栏**

- **FR-015**: 系统 MUST 为混合检索配置护栏：Rerank 候选预算、融合参数、Sparse 查询超时与单次调用总超时；总超时 MUST 小于目标 MCP Host 的 Tool Call 超时（蓝图 §12、§19，沿用 001 护栏体系）。
- **FR-016**: 系统沿用 001 蓝图 §14 四态完成状态（`success` / `partial` / `no_evidence` / `failed`）：当 Sparse 召回或 Rerank 子步骤失败或超时、但 Dense 召回已有可靠证据时，系统 MUST 返回 `partial` 状态并携带已验证证据与失败路径信息；当 Dense 与 Sparse 均无可靠证据但非系统异常时返回 `no_evidence`；当系统异常无法形成可靠证据时返回 `failed`（蓝图 §14 四态，沿用 001 FR-017）。
- **FR-017**: 系统 MUST 在 Rerank 评分打平时保留稳定、确定的次序，不在融合或排序中引入随机扰动（宪法原则 VI：确定性控制优先）。
- **FR-018**: 系统 MUST 在同一后端隔离不同 Agent、不同会话与并发请求的运行状态、证据账本与融合中间状态，并发场景下不发生串扰（沿用 001 FR-023，蓝图 §21.1）。

**评测与对照**

- **FR-019**: 系统 MUST 在 001 固定评测集（`eval/eval_dataset.json`，11 条）基础上扩充新增词汇精确查询（精确符号、错误码、配置项等场景，新增 ≥ 5 条查询且含 ≥ 1 条中文查询以验证 CJK 词法召回，仍遵循 AI 生成、人工审核、JSON 格式），保留原 11 条保证与基线逐条可比；在扩充后的评测集上运行混合检索评测，产出 Recall@K、MRR、nDCG、P50/P95 延迟指标，并与 `eval/baseline_report.json` 的 001 Dense 基线逐项对照（蓝图 §24.1、§24.3）。
- **FR-020**: 对照评测 MUST 逐查询记录 Dense 基线排名与混合检索排名、Dense 分数、Sparse 分数（或词频权重）、融合分数与 Rerank 分数，使排名变化可解释（宪法原则 IV：证据可定位与可解释）。
- **FR-021**: 混合检索增强 MUST 在固定评测集上证明 MRR 与 nDCG 相对 001 Dense 基线有可度量收益，且未违反任何硬性验收指标（跨项目泄漏为零、Schema 合法率 100%、来源可定位率 100%）后，才进入默认检索路径（宪法原则 X、蓝图 §24.3）。
- **FR-022**: 系统 MUST 记录每次混合检索的请求标识、知识作用域、完成状态、各子路径（Dense/Sparse/Fusion/Rerank）耗时与证据引用，以支持问题回溯（蓝图 §13 证据账本，沿用 001 FR-025）。
- **FR-023**: 已有 Dense-only 知识版本获得 Sparse/BM25 词法能力 MUST 通过用户对已有知识源触发重建、发布声明 `lexical_ready` 的新版本实现；系统 MUST NOT 自动批量迁移已发布版本，也 MUST NOT 使已发布 Dense-only 版本在重建期间不可检索（沿用 001 失败保护与重建能力，蓝图 §5、§8.4）。
- **FR-024**: 对照评测 MUST 在同一环境会话内先重跑 Dense-only 基线、再运行混合检索，以保证延迟增量对照公平（`baseline_report.json` 的延迟本身不可在 1% 容差内重复）；非延迟指标同时与 `eval/baseline_report.json` 记录基线交叉验证。
- **FR-025**: Sparse/BM25 词法索引 MUST 支持中文（CJK）分词，使中文 Markdown 内容与中文查询可被词法精确召回；朴素空格分词 MUST NOT 作为 CJK 内容的唯一分词方式。对照评测集 MUST 包含中文查询用例以验证 CJK 词法召回质量。

### Key Entities *(include if feature involves data)*

- **Sparse/BM25 词法索引**: Qdrant 中与 Dense 向量并存的词法检索索引，按 `knowledge_scope_id`、`project_id` 与 `index_version` 过滤；不依赖新嵌入模型，由 Qdrant 负责构建与检索。
- **融合候选（Fused Candidates）**: Dense 与 Sparse 召回结果经 RRF/DBSF 融合后的候选集合，每条候选保留来源检索器、各路分数与融合分数。
- **Rerank 候选集**: 融合后截取的有限候选，由 Cross-Encoder Reranker 精排，数量受候选预算护栏约束。
- **知识版本能力清单（Capabilities）**: 001 已有 `dense_ready`；002 扩展为 `{dense_ready, lexical_ready}`，声明 `lexical_ready` 的版本才启用 Sparse/BM25 路径。
- **对照评测报告**: 在固定评测集上记录混合检索指标、与 001 Dense 基线的逐项增量、逐查询排名变化与可解释分数的评测产物。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 在固定评测集上，混合检索（Dense+Sparse 融合 + Rerank）的 MRR 与 nDCG 均值相对 001 Dense 基线有可度量提升（原 11 条子集上相对**同会话 Dense 基线**严格正增量，相对口径详见 research.md §0.2/§0.6，2026-09-04 修订）；Recall@K 不低于基线（不要求提升，因基线已达 1.0，要求不下降）。
- **SC-002**: 验收测试集中混合检索的跨项目泄漏事件数为零。
- **SC-003**: 验收测试集中所有混合检索 Tool 成功响应 100% 通过 `search_knowledge` 输出 Schema 校验。
- **SC-004**: 验收测试集中所有混合检索返回的证据 100% 可定位到确定的知识源版本与内容位置（Markdown 章节路径或 Java 全限定符号路径）。
- **SC-005**: 混合检索单次调用的 P50/P95 延迟被记录，并与同一环境会话内重跑的 Dense 基线对照（保证延迟增量公平，FR-024）；延迟允许因新增 Sparse 与 Rerank 步骤而上升，但 MUST 不超过服务端总超时护栏且小于目标 MCP Host 的 Tool Call 超时。
- **SC-006**: 对照评测可重复：同一环境连续两次运行的 Recall@K、MRR、nDCG 在容差内一致（沿用 001 非延迟可重复性要求）；延迟指标标注为环境敏感。
- **SC-007**: 对照评测报告逐查询展示 001 Dense 基线排名与混合检索排名差异，且对每个排名变化提供 Dense 分数、Sparse 分数（或词频权重）、融合分数与 Rerank 分数以解释变化。
- **SC-008**: 知识版本能力清单正确声明 `lexical_ready`；仅声明 `dense_ready` 的版本不被纳入 Sparse 路径；声明混合能力的版本在 Dense 与 Sparse 索引均就绪后才可检索。
- **SC-009**: Sparse 或 Rerank 子步骤失败或超时时，已有 Dense 可靠证据的场景返回 `partial` 并携带失败路径信息；完全无法形成可靠证据时返回 `failed`（四态可区分、可操作，沿用 001 SC-010）。

## 范围内 / 范围外

### 范围内（002）

- 在 Qdrant 中构建 Sparse/BM25 词法索引（含 CJK 中文分词），与 Dense 向量索引并存。
- Dense 与 Sparse/BM25 召回的 RRF/DBSF 融合。
- 融合后有限候选的 Cross-Encoder Rerank（`BAAI/bge-reranker-v2-m3`）。
- 知识版本能力清单扩展 `lexical_ready` 与版本隔离。
- 混合检索路径在 `search_knowledge` 内部的接入（不修改对外 MCP 契约）。
- 混合检索护栏（Rerank 候选预算、融合参数、子步骤超时、总超时）。
- 在 001 固定评测集上的混合检索评测与对照报告（含逐查询可解释性）。
- 检索运行的子路径耗时与证据引用追踪扩展。

### 范围外（不重复 001，且不属于 002）

- Web 管理、项目与知识域管理、上传、凭据规范化与结构切片（001 已实现，002 复用）。
- Dense 嵌入与向量索引构建的基线能力（001 已实现；002 只在其上新增 Sparse，不重建嵌入模型）。
- MCP `search_knowledge` 与 `get_evidence` 的对外契约与 Schema（001 已确立；002 不修改契约，宪法原则 VII）。
- PostgreSQL 图关系扩展、硬关系与软关系（蓝图 §9 第 3 项与 §10，属 Feature 004 Graph RAG）。
- 三 Agent 编排、追加式证据账本的 Agent 判断、补充检索与上下文编排（蓝图 §9 第 6 项与 §11，属 Feature 005 Agentic Retrieval Orchestration）。
- LLM 在检索链路中的语义判断（蓝图 §9 第 6 项，属 Feature 005）。
- 新文件格式解析（Word、PDF、OpenAPI、DDL、Go、Python 等，属 Feature 003）。
- 单写多读实例、Provider 运行配置、追踪与运行指标硬化（属 Feature 006 Runtime Hardening）。

## Clarifications

### Session 2026-08-27

- Q: 002 发布后，已有的 001 Dense-only 知识版本如何获得 Sparse/BM25 词法能力? → A: 重建可触发、发新版本——用户对已有知识源触发重建，系统发布声明 `lexical_ready` 的新版本；旧 Dense-only 版本继续可用直到新版本发布，系统不自动批量迁移。
- Q: 001 的固定评测集仅 11 条且 Recall@K 已达 1.0，是否应扩充以有意义验证 MRR/nDCG 提升? → A: 扩充并保留原 11 条——在原 11 条基础上新增词汇精确查询（精确符号、错误码、配置项等），原 11 条保留保证与基线逐条可比。
- Q: 对照评测的延迟对照应基于记录的 baseline_report.json 还是同会话重跑 Dense 基线? → A: 同环境同会话重跑 Dense 基线——先重跑 Dense-only、再跑混合检索，延迟增量更公平；非延迟指标同时与 baseline_report.json 交叉验证。
- Q: 002 的 Sparse/BM25 是否需支持中文（CJK）分词? → A: 本 Feature 即需 CJK 分词——002 必须支持中文分词的 Sparse/BM25，并增加中文评测用例以验证中文词法召回质量。

## Assumptions

- 002 复用 001 已建立的固定评测集（`eval/eval_dataset.json`，11 条查询）与基线报告（`eval/baseline_report.json`），并在原 11 条基础上扩充新增词汇精确查询（精确符号、错误码、配置项等场景）；扩充部分仍遵循 001 的 AI 生成、人工审核、JSON 格式约定（蓝图 FR-024 等价约束），原 11 条保留以保证与基线逐条可比。
- 已有 Dense-only 知识通过用户触发重建获得 `lexical_ready`（不自动批量迁移，FR-023）；对照评测前，评测项目的知识源需先触发重建并发布声明 `lexical_ready` 的新版本，使混合检索路径可执行。
- 002 不引入新嵌入模型；Sparse/BM25 由 Qdrant 负责构建与检索，Dense 仍使用 `BAAI/bge-m3`（蓝图 §18.2）。因此新增 Sparse 能力属同一嵌入模型与切片策略上的能力扩展，不触发宪法原则 VIII 的"不可混用"。
- Reranker 默认 `BAAI/bge-reranker-v2-m3` 本地 CPU 运行；Provider 接口允许后续切换本地 GPU 或远程 API（蓝图 §18.1、§18.3）。
- 002 的 Sparse/BM25 必须支持中文（CJK）分词（FR-025）；本 Feature 验收录中文 Markdown 内容与中文查询用例，CJK 分词器选择与具体实现方式留给 plan.md / research.md 决策。
- 002 面向 001 已验证的单用户、本机部署环境；并发隔离沿用 001 的请求级隔离（5 并发），不引入多实例或分布式协调。
- 混合检索进入默认路径的判定以固定评测集上 MRR/nDCG 可度量提升且硬性指标不被违反为准；具体提升阈值在分析混合检索基线数据后于 `research.md` 声明（沿用 001"首轮记录基线、不预设阈值"的渐进策略）。
- 002 不修改 `search_knowledge` 与 `get_evidence` 对外契约；对外返回的证据结构、`completion_status` 四态与来源定位格式沿用 001。
- 服务端总超时沿用 001 的 30s 护栏并小于目标 Host Tool Call 超时；混合检索新增的 Sparse 与 Rerank 步骤在该总预算内调度。目标 MCP Host（如 Claude Code、ChatGPT App 等）的 Tool Call 超时预期 ≥ 60s，为 30s 服务端总超时提供 ≥ 2x 安全余量；部署时须确认目标 Host 实际超时配置满足此下界（蓝图 §19）。
