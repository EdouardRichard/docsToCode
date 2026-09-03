# Feature Specification: Runtime Hardening

**Feature Branch**: `006-runtime-hardening`

**Created**: 2026-09-03

**Status**: Draft

**Input**: User description: "单写多读、Provider 配置、追踪与指标。范围依据：蓝图 §23.4.6 / §19–21。检索必须显式 project_scope，跨项目串库为零，MCP Schema 合法率与来源可定位率 100%。与 001 确定性基线的对照评测要求：无（工程硬化，非检索质量）。不重复 001 已实现能力，明确范围内/范围外。输入材料：001~005 代码、部署环境。"

**Scope Basis**: 蓝图 §23.4.6（006 纵向交付 Feature 定义：单写多读实例、Provider 配置、追踪、运行指标和演进接口）、§19（延迟与降级）、§20（运行状态保留）、§21（实例与并发模型，含 §21.1 并发请求与 §21.2 单写多读部署）。支撑章节：§17（Model Gateway 与能力路由，不绑定供应商）、§18（模型部署：§18.1 Provider 类型、§18.3 增强模型与索引版本、§18.5 本地性能原则）、§16.3（本机绑定默认）、§24.2（硬性验收指标）、§24.3（基线对照声明义务）。

## 对照评测声明（001 确定性基线）

本 Feature 相对 001 确定性基线（`eval/baseline_report.json`，Markdown/Java 11 条）的对照评测要求为 **无**。

理由：006 是工程硬化 Feature——交付部署形态（单写多读实例）、运行配置（Provider、超时、TTL、正文开关）与可观测性（追踪与运行指标），不新增检索信号、不改变融合/排序逻辑、不修改 MCP 对外契约，因此不存在可对照的检索质量增量。进入 `plan.md` 前，`research.md` 中的相对基线声明固化为"**无（工程硬化，非检索质量）**"（蓝图 §24.3 的 006 特例）。

替代的评测义务（仅合规与非回归，不构成质量对照）：

1. **硬性验收指标保持**：跨项目串库 = 0、MCP Schema 合法率 = 100%、来源可定位率 = 100%，在 writer + reader 部署形态的验收测试集上成立（宪法硬约束、蓝图 §24.2）。
2. **非回归冒烟**（澄清 Q3 固化）：001 基线 11 条 Markdown/Java 评测集（`eval/eval_dataset.json` 原批次）在 writer 与 reader 两种实例形态下各重跑一遍，非延迟指标（Recall@K、MRR、nDCG）与 `baseline_report.json` 逐条对照、在既有可重复性容差（1% 相对容差，沿用 001/002/004/005 约定）内一致；延迟与成本指标标注环境敏感。冒烟重跑用于确认工程硬化未改变检索行为，**不设质量提升阈值、不作质量声明**。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 部署单写多读实例 (Priority: P1)

运维者在共享的 PostgreSQL 与 Qdrant 之上启动一个 writer 实例（Web 管理端、REST 管理 API、入库任务、索引发布、数据库迁移与只读 MCP 检索）和一个或多个 reader 实例（仅只读 MCP 检索）。外部 Agent 通过 reader 端到端完成 `search_knowledge` 与 `get_evidence`，不依赖 writer 本地原始文件；误启动第二个 writer 时被拒绝进入写模式，不发生双写。

**Why this priority**: 单写多读是蓝图 §21.2 的首期部署形态，也是 001 以来各 Feature 显式延期到 006 的核心硬化项（001 假设"Reader 模式和多实例硬化属于后续 Runtime Hardening Feature"）。没有它，检索可用性与知识维护无法解耦——writer 维护、迁移或重启期间外部 Agent 无法继续检索。

**Independent Test**: 在共享 docker-compose PostgreSQL/Qdrant 上启动 writer + 2 个 reader（各自独立 MCP 端口，默认绑定 127.0.0.1），从 reader 调用 `search_knowledge` 与 `get_evidence` 并通过输出 Schema 校验；停止 writer 后 reader 检索与证据展开仍成功；再启动第二个 writer，验证其被拒绝进入写模式并返回明确错误。

**Acceptance Scenarios**:

1. **Given** 共享 PostgreSQL/Qdrant 与已发布知识版本，**When** 运维者以 writer 模式启动实例，**Then** 实例获得写入所有权（PostgreSQL 单 Writer 租约）并提供 Web 管理端、REST 管理 API、入库、索引发布、数据库迁移与只读 MCP 检索（蓝图 §21.2）。
2. **Given** writer 已运行，**When** 运维者以 reader 模式启动新实例，**Then** 该实例只提供只读 MCP 检索，不暴露管理端与管理 API 写路径，不执行入库、索引发布、数据库迁移与知识库写路径后台任务。
3. **Given** writer 已持有有效租约，**When** 另一进程试图以 writer 模式启动，**Then** 系统拒绝其进入写模式并返回明确的占用错误，不发生双写、不静默降级为 reader。
4. **Given** writer 进程停止，**When** 外部 Agent 通过 reader 检索并展开证据，**Then** `get_evidence` 从共享数据库读取 Chunk 正文、父级上下文与来源定位并成功返回，不访问 writer 本地原始文件（蓝图 §21.2）。
5. **Given** writer 意外终止且未释放租约，**When** 租约到期（固化默认 90s 过期窗口）后运维者重启 writer，**Then** 新 writer 获得租约继续维护，租约续约（默认 30s）与过期回收语义可验证。

---

### User Story 2 - Provider 运行配置 (Priority: P1)

运维者通过统一运行配置为 Embedding、Reranker 与 LLM 三类能力分别选择 Provider（local CPU / local GPU / remote API），三类能力可独立选择不同 Provider。配置在启动时被校验：未知类型、不可达端点、与既有索引版本不兼容的维度等非法配置产生显式失败，而非静默回退到错误模型。切换 Embedding 模型/Provider 时创建新索引版本并重新向量化，不同模型的向量不混入同一索引版本。

**Why this priority**: Provider 配置是蓝图 §18.1/§17 与宪法架构约束（"Provider 接口 MUST 许可本地 CPU、本地 GPU 与远程 API 执行"）的直接落地，也是 001 计划显式延期到 006 的扩展点（`providers/local_gpu.py` / `remote_api.py`）与 002 延期的增强模型/量化项的配置前提。无它则部署形态无法适配 GPU 或云端环境。

**Independent Test**: 不改代码，仅通过运行配置将三类能力分别指向不同 Provider 类型（本地 CPU Embedding、本地 CPU Reranker、远程 API LLM），验证检索路径端到端可用且对外契约不变；再提交一组非法配置（未知类型、不可达端点、维度不匹配），验证启动全部显式报错、无静默回退。

**Acceptance Scenarios**:

1. **Given** 统一 Provider 运行配置，**When** 运维者为 Embedding/Reranker/LLM 分别配置 Provider 类型与模型，**Then** 三类能力使用各自 Provider 执行，互不绑定（蓝图 §18.1：三类能力可分别选择不同 Provider）。
2. **Given** 配置声明 remote API Provider，**When** 启动校验执行，**Then** 配置结构与端点可达性（尽力而为的健康探测）被校验；非法或不可达时启动显式失败并给出可纠正的错误信息。
3. **Given** 声明了不同维度/模型的 Embedding Provider，**When** 运维者尝试将其直接作用于既有已发布索引版本，**Then** 系统拒绝混装，唯一合法路径是创建新索引版本并重新向量化（宪法 VIII、蓝图 §18.3）。
4. **Given** Provider 配置合法且模型可用，**When** 检索请求在 writer 与 reader 实例上执行，**Then** 对外 MCP 契约不变：`completion_status` 四态、来源定位格式与两个核心 Tool 的输出 Schema 均不受 Provider 选择影响（宪法 VII）。
5. **Given** remote API LLM Provider 运行中故障或超时，**When** Agent 编排路径执行，**Then** 沿用 005 确定性降级契约（Agent 回退确定性等价行为并返回有效 `completion_status`），Provider 层不引入新的硬失败（蓝图 §19、005 SC-011）。

---

### User Story 3 - 追踪与运行指标 (Priority: P2)

运维者可以查询覆盖 writer 与 reader 实例的运行指标：检索请求量（按实例与 Tool 聚合）、`completion_status` 分布、P50/P95 延迟与子路径耗时、Provider 调用次数与用量（含 LLM 成本计量）、TTL 清理量。追踪记录统一接受正文开关与 TTL 运行配置：关闭正文记录后，全部检索模式（dense/hybrid/graph_enhanced/agentic）的运行记录只保留 ID、状态、耗时与错误。

**Why this priority**: 蓝图 §23.4.6 将"追踪、运行指标"列为 006 命名交付项，§23.3 能力域 6（检索评估、追踪与运行配置）要求延迟、成本与追踪可观测。001 已有逐次 RetrievalRun 记录、005 已有 Agent 编排追踪与 agentic 路径的正文开关，但缺少可查询的聚合指标，且 001 路径的运行记录恒存查询正文（无法关闭）；硬化后系统才可被运维。

**Independent Test**: 执行一组已知构成的验收请求批次后查询指标，验证请求量、状态分布、延迟分位数、子路径耗时与 Provider 用量与批次逐条对账；关闭正文记录后重放请求，验证四种检索模式的运行记录均不含查询与证据正文、但保留 ID/状态/耗时/错误。

**Acceptance Scenarios**:

1. **Given** 已知构成的验收请求批次（跨 writer 与 reader 实例），**When** 运维者查询运行指标，**Then** 指标按请求量、`completion_status` 分布、P50/P95 延迟、子路径耗时、Provider 调用/用量与 TTL 清理量聚合，数值与批次逐条对应（可对账）。
2. **Given** 追踪配置关闭正文记录，**When** 任一检索模式（dense/hybrid/graph_enhanced/agentic）的请求执行，**Then** 运行记录不含查询正文与证据正文，只保留 ID、状态、耗时与错误（蓝图 §20；005 已覆盖 agentic，006 扩展为全模式统一开关）。
3. **Given** 任意指标查询，**When** 指标被读取，**Then** 指标只含聚合数值与标识，不含查询/证据正文（无论正文记录开关状态）。
4. **Given** TTL 运行配置，**When** 运行记录到期，**Then** 过期记录被清理且清理量计入指标；知识源、Chunk、向量与图关系不随 TTL 删除（蓝图 §20）。
5. **Given** 一次需要回溯的问题请求，**When** 运维者检索其运行记录，**Then** 可由请求标识定位运行记录及其状态、耗时、错误与证据引用（沿用 001 FR-025 / 005 FR-031 追溯契约，006 不减弱）。

---

### User Story 4 - 硬性约束与非回归验收 (Priority: P2)

006 交付后，宪法硬约束（检索必须显式 `project_scope`、跨项目串库为零、MCP Schema 合法率 100%、来源可定位率 100%）在 writer + reader 部署形态的验收测试集上保持成立；001–005 既有验收套件在硬化后的运行时上保持通过。除合规与非回归外，本 Feature 无对照评测要求（对照评测声明：无——工程硬化，非检索质量）。

**Why this priority**: 这是宪法与蓝图 §24.2 对任何交付的强制发布闸口（release blocker），也是"工程硬化不引入回归"的唯一质量性验收。作为门禁而非用户可见价值，定为 P2；其结果决定 US1–US3 是否可发布。

**Independent Test**: 在 writer + reader 部署上运行宪法硬约束验收集与 001–005 既有验收测试（含固定评测集冒烟重跑），验证泄漏 = 0、Schema 合法率 = 100%、定位率 = 100%、既有套件全绿、非延迟指标在既有容差内一致。

**Acceptance Scenarios**:

1. **Given** writer + reader 部署的验收测试集，**When** 全部验收请求执行，**Then** 跨项目泄漏事件数为零、所有 Tool 成功响应 100% 通过 `search_knowledge` 与 `get_evidence` 输出 Schema 校验、所有返回证据 100% 可定位（宪法硬约束）。
2. **Given** 缺少显式 `project_scope` 的项目检索请求（无论打到 writer 还是 reader 实例），**When** 请求执行，**Then** 系统拒绝并返回可纠正的作用域错误，不回退默认或全库搜索（宪法原则 I）。
3. **Given** 001–005 既有验收测试集，**When** 在硬化后的运行时上执行，**Then** 全部保持通过（非回归）。
4. **Given** 固定评测集冒烟重跑，**When** 结果与既有基线记录比较，**Then** 非延迟指标在既有可重复性容差内一致；不设质量提升阈值、不作质量声明（对照评测声明：无）。

### Edge Cases

- 两个 writer 同时启动竞争租约：仅一个成功；另一个 MUST 拒绝进入写模式并明确报错，不静默降级为 reader，不发生双写。
- writer 崩溃后租约未释放：租约 MUST 到期可回收；回收窗口内第二个 writer 不得进入写模式；续约周期与过期窗口作为运行配置管理；读路径不依赖租约，回收期间 reader 检索不受影响。
- reader 被误配置执行维护任务：reader 模式 MUST NOT 运行任何知识库写路径后台任务（入库调度、索引发布、数据库迁移、TTL 清理）；TTL 清理归属 writer。reader 的逐请求运行/追踪记录写入（追加式运行状态，蓝图 §20）不属知识库写路径，不受租约约束。
- reader 启动时共享数据库 schema 不兼容（writer 已升级而 reader 未升级）：reader MUST 显式失败并说明版本不兼容，不得以错误假设继续服务。
- Provider 配置非法（未知类型、必填缺失、端点不可达、Embedding 维度与既有索引版本不匹配）：启动显式失败；MUST NOT 静默回退到未声明模型或向既有索引版本混装向量。
- remote Provider 运行中故障（连接失败、超时、HTTP 错误、响应畸形）：沿用既有确定性降级（partial / no_evidence / failed 与 005 Agent 确定性回退），Provider 层不引入新硬失败或阻塞状态机。
- 正文记录关闭时的回溯请求：记录仍含 ID、状态、耗时、错误与证据引用；正文缺失不破坏可追溯结构。
- 并发超过 Provider 独立并发上限：请求受有界排队/拒绝护栏约束，不产生无界并发，不互相污染请求状态。
- 服务端总超时被配置为大于等于目标 Host Tool Call 超时：配置校验 MUST 拒绝或显式告警（蓝图 §19：服务端节点超时与总超时必须小于目标 Host 配置）。
- 多 reader 并发检索与 writer 入库/发布同时发生：reader 只读已发布版本；未完成版本不参与检索（沿用 001 FR-008/FR-009 不变量）。
- 多实例被误配置为相同 worker_id：系统 MUST 在启动或运行中检测并显式拒绝该配置，防止跨实例雪花 ID 冲突破坏运行记录写入（澄清 Q6）。
- 服务绑定非本机地址：仅显式配置允许；认证仍属蓝图 §26 触发条件（不在本期），非本机暴露由运维者自行承担风险。

## Requirements *(mandatory)*

### Functional Requirements

**实例模式与写入协调（蓝图 §21.2）**

- **FR-001**: 系统 MUST 支持两种实例模式：`writer`（唯一知识库维护实例：Web 管理端、REST 管理 API、入库任务、索引发布、数据库迁移与只读 MCP 检索）与 `reader`（仅只读 MCP 检索，连接共享的 Qdrant 与 PostgreSQL）。实例模式作为运行配置/启动参数管理。
- **FR-002**: 知识库维护写入所有权 MUST 通过可替换的 `WriteCoordinator` 抽象获得；首期实现 PostgreSQL 单 Writer 租约。已存在有效租约时，第二个 writer MUST 被拒绝进入写模式并返回明确错误，不发生双写。租约约束范围为知识库维护写路径（入库、索引发布、迁移、清理）。澄清 Q4 固化标识格式：租约记录标识 `lease_id` 为雪花 ID（沿用仓库稳定标识惯例），持有者标识 `instance_id` 为实例进程启动时生成的 UUID；租约状态机为 {active → released（正常退出主动释放）| expired（过期未续约，可被新 writer 回收）}。
- **FR-003**: Writer 租约 MUST 具备明确的续约与过期回收语义：writer 崩溃后租约到期可被新 writer 获取；回收窗口内不得有第二个 writer 进入写模式。续约周期与过期窗口作为运行配置管理（澄清 Q2 固化默认：续约间隔 30s、过期窗口 90s，即续约间隔的 3 倍；可由运行配置覆盖）。
- **FR-004**: reader 实例 MUST NOT 暴露 Web 管理端与管理 API 写路径，MUST NOT 执行入库任务、索引发布、数据库迁移与知识库写路径后台任务（含 TTL 清理；TTL 清理归属 writer）。reader 的逐请求运行/追踪记录（追加式运行状态）不受此限（蓝图 §20）。
- **FR-005**: reader 检索所需的 Chunk 正文、父级上下文与来源定位 MUST 位于共享数据库；`get_evidence` MUST NOT 依赖访问 writer 本地原始文件（蓝图 §21.2；001 数据布局已满足，006 验证并保障该不变量在 reader 形态下成立）。
- **FR-006**: 原始文件存储 MUST 通过可替换的 `SourceObjectStore` 抽象访问，首期实现本地文件系统 Provider；数据模型、索引版本与 MCP 契约 MUST NOT 依赖"永远只有一个 Writer"的假设（演进接口，蓝图 §21.2 末段）。
- **FR-007**: reader 启动时 MUST 校验共享数据库 schema 兼容性；不兼容时显式失败并说明原因，不得以错误假设继续服务。数据库迁移仅由 writer 执行。

**并发与隔离（蓝图 §21.1）**

- **FR-008**: 同一后端 MUST 支持多客户端、多会话并发访问：不保存全局活动项目或隐式会话状态；每个请求独立携带 `project_scope`；运行状态按 `request_id`/`run_id` 隔离（跨 writer/reader 实例同样成立）；PostgreSQL、Qdrant 与模型客户端使用连接池（沿用 001 FR-023/005 FR-025，006 扩展至跨实例形态）。
- **FR-009**: LLM、Embedding 与 Reranker MUST 使用相互独立的并发上限，作为运行配置管理（蓝图 §21.1）；超限请求受有界护栏约束，不产生无界并发。澄清 Q2 固化默认与上限：LLM 默认 4 / 上限 8、Embedding 默认 8 / 上限 16、Reranker 默认 2 / 上限 4（上限为默认 2 倍），可由运行配置覆盖但不得超上限。

**Provider 配置（蓝图 §17、§18）**

- **FR-010**: 系统 MUST 提供统一的 Provider 运行配置，为 Embedding、Reranker 与 LLM 三类能力分别声明 Provider；Provider 类型 MUST 覆盖 local CPU、local GPU 与 remote API 三类（宪法架构约束、蓝图 §18.1）；writer 与 reader 实例使用同一 Provider 配置机制。
- **FR-011**: Provider 配置 MUST 在启动时校验：类型合法、必填字段完备、声明端点可达（尽力而为的健康探测）、Embedding 维度与目标索引版本兼容。非法配置 MUST 显式失败并给出可纠正的错误信息，MUST NOT 静默回退到未声明的模型。
- **FR-012**: 后端 MUST 通过 Model Gateway / 能力路由层使用 Provider，MUST NOT 绑定具体 LLM 供应商；OpenAI-compatible / Anthropic-compatible 仅作为供应商适配协议（蓝图 §17）。
- **FR-013**: 切换 Embedding 模型或 Provider 时 MUST 创建新索引版本并重新向量化；不同 Embedding 模型或不兼容切片策略产生的向量 MUST NOT 混入同一索引版本（宪法 VIII、蓝图 §18.3；沿用 002 重建治理）。
- **FR-014**: 本地 CPU Provider MAY 使用 ONNX/OpenVINO 或量化实现（蓝图 §18.5）；是否在本 Feature 落地量化实现由 plan.md / research.md 决策，非验收必需项。
- **FR-015**: Provider 故障（远程端点失败、超时、模型不可用、响应畸形）MUST 沿用既有确定性降级路径：`completion_status` 四态与 005 SC-011 Agent 确定性回退契约保持不变；Provider 层 MUST NOT 引入新的硬失败或使状态机阻塞。

**追踪与运行指标（蓝图 §20、§23.3 能力域 6）**

- **FR-016**: 系统 MUST 提供可查询的运行指标，覆盖 writer 与 reader 实例的检索活动：请求量（按实例模式与 Tool 聚合）、`completion_status` 分布、P50/P95 延迟、子路径耗时、Provider 调用次数与用量（LLM 成本计量沿用 005 真实调用口径）、TTL 清理量。指标键使用稳定字符串标识（聚合维度：能力 × provider_type × 实例模式，澄清 Q4）。指标属运维指标，不是检索质量指标（质量指标属离线评测，001–005 已实现）。
- **FR-017**: 运行指标 MUST 只包含聚合数值与标识，MUST NOT 包含查询正文或证据正文（无论正文记录开关状态）。
- **FR-018**: 追踪正文开关 MUST 覆盖全部检索模式（dense / hybrid / graph_enhanced / agentic）：关闭时运行记录只保留 ID、状态、耗时与错误（蓝图 §20；005 已覆盖 agentic 路径，006 扩展为全模式统一开关）。
- **FR-019**: Agent 运行状态与追踪记录 MUST 使用 TTL，TTL 时长作为运行配置管理（默认沿用 7 天）；知识源、Chunk、向量与图关系 MUST NOT 随 TTL 删除；Agent 推理结果 MUST NOT 自动写回项目知识库（蓝图 §20，沿用 005 FR-011）。
- **FR-020**: 每次检索的请求标识、知识作用域、完成状态、耗时、错误与证据引用 MUST 保持可回溯（沿用 001 FR-025 / 005 FR-031 契约，006 不减弱）。

**延迟与超时运行配置（蓝图 §19）**

- **FR-021**: 服务端节点超时与总超时 MUST 作为运行配置管理（MUST NOT 写死在协议契约中），且 MUST 小于目标 Host 的 MCP Tool Call 超时（ChatGPT App / Claude Code / DeepSeek Harness `toolCallTimeoutMs`）；每个目标 Host 使用独立的超时档位配置（澄清 Q2 固化：服务端总超时默认沿用 30s，各 Host 档位具体数值在 plan.md / research.md 依据蓝图 §19 P50/P95 评测确定）。
- **FR-022**: 超时数值变更 MUST NOT 要求修改对外 MCP 契约；部分检索路径超时但已有可靠证据时返回 `partial`，无法形成可靠证据时返回 `no_evidence`/`failed`（蓝图 §19，沿用 001/002 降级语义）。

**硬性约束（继承宪法与蓝图 §24.2）**

- **FR-023**: 项目知识检索 MUST 携带显式 `project_scope`；缺少显式作用域时 MUST 拒绝并不得回退默认或全库搜索；无法唯一解析项目引用时 MUST 停止检索并返回候选项目列表（无论请求落在 writer 还是 reader 实例）（宪法原则 I、蓝图 §4.1、001 FR-014）。
- **FR-024**: 跨项目泄漏 MUST 为零：任一检索结果、证据、图关系或 Chunk 不得从一个 `project_scope` 出现在另一项目的检索中，除非显式多项目 `project_scope` 请求（宪法硬约束、蓝图 §24.2）。
- **FR-025**: 验收测试集中所有 Tool 成功响应 MUST 100% 通过 `search_knowledge` 与 `get_evidence` 输出 Schema 校验，证据来源可定位率 MUST 为 100%（每条证据携带来源 ID、版本与位置）；006 MUST NOT 修改两个核心 Tool 的对外契约（宪法原则 IV、VII、蓝图 §24.2）。
- **FR-026**: 未认证的管理与 MCP 服务 MUST 默认仅绑定本机地址；非本机绑定仅经显式配置允许（宪法架构约束、蓝图 §16.3；认证属蓝图 §26 触发条件，不在本期）。

**对照评测声明（无）与非回归（蓝图 §24.3）**

- **FR-027**: 本 Feature 相对 001 确定性基线的对照评测要求为 **无（工程硬化，非检索质量）**：MUST NOT 设置质量提升阈值、MUST NOT 执行质量对照评测、MUST NOT 作质量提升声明；`research.md` 基线声明固化为此口径。
- **FR-028**: 作为替代义务，006 的通过判定为三项全过（澄清 Q3 固化）：（1）001 基线 11 条 Markdown/Java 评测集在 writer 与 reader 两实例形态各重跑一遍，非延迟指标与 `baseline_report.json` 逐条对照、在 1% 相对容差内一致（延迟与成本标注环境敏感）；（2）001–005 既有 pytest 验收测试集全部通过；（3）宪法硬约束在 writer + reader 部署形态上成立（FR-023~FR-025）。回归冒烟不构成质量对照。

**既有检索护栏沿用（不重复 001–005，澄清 Q1）**

- **FR-029**: 006 MUST NOT 改变 001–005 已固化的检索护栏默认值与上限，MUST NOT 新建统一配置面板收编它们；既有护栏沿用各 Feature 既有环境变量入口与固化值：top_k 默认 5 / 上限 20、检索总超时 30s、Qdrant 查询超时 10s（001/002）；RRF k=60、Rerank 候选预算 20、Sparse 子路径超时 5s（002）；图扩展跳数默认 2 / 上限 3、候选预算默认 10 / 上限 20、图查询子超时 3s（004）；Agent 最大轮次默认 2 / 上限 3、节点超时默认 5s / 上限 10s、单来源最大证据数默认 3 / 上限 5（005）。006 新增/扩展的运行参数仅限：Writer 租约参数（续约/过期）、Provider 独立并发上限、按 Host 超时档位、TTL 可配置化、追踪正文开关的全模式扩展（沿用 005 既有开关语义）。

**跨实例 ID 唯一性（澄清 Q6）**

- **FR-030**: 并发运行的实例（writer 与各 reader）MUST 持有互不相同的雪花 worker_id，使多实例同时生成的运行/账本等雪花 ID 保持全局唯一；多实例并发下共享数据库的 ID 重复/主键冲突事件数 MUST 为 0。单实例部署默认 worker_id=0（兼容 001 既有实现）；worker_id 分配机制（显式运行配置或租约协调分配）与相同 worker_id 误配置的检测手段在 plan.md 决策。

### Key Entities *(include if feature involves data)*

- **实例模式（Instance Mode）**: 部署实例的角色，枚举 {writer, reader}；每个实例进程启动时生成稳定标识 `instance_id`（UUID），用于租约持有者归属与指标按实例聚合；并发实例持有互不相同的雪花 worker_id（跨实例 ID 唯一性，澄清 Q6）；writer 为唯一知识库维护实例并提供只读 MCP 检索，reader 仅提供只读 MCP 检索（蓝图 §21.2，澄清 Q4）。
- **写入协调器（WriteCoordinator）**: 授予知识库维护写入所有权的可替换抽象；首期实现为 PostgreSQL 单 Writer 租约，后续可替换为分布式协调器（蓝图 §21.2）。
- **Writer 租约（Writer Lease）**: 写入所有权的运行时凭据：稳定标识 `lease_id`（雪花 ID），字段含持有者 `instance_id`（UUID）、获取时间、续约时间、到期时间；状态机 {active → released（正常退出主动释放）| expired（过期未续约，可被新 writer 回收）}（澄清 Q4 固化）。
- **源对象存储（SourceObjectStore）**: 原始上传文件访问的可替换抽象；首期为本地文件系统 Provider，后续可替换为 S3 兼容对象存储（蓝图 §21.2）。
- **Provider 运行配置（Provider Configuration）**: 按能力（Embedding/Reranker/LLM）独立声明的运行配置：Provider 类型 {local_cpu, local_gpu, remote_api}、模型标识、端点、并发上限；经启动校验生效。
- **运行指标（Runtime Metrics）**: 运维聚合数据：请求量（按实例模式/Tool）、`completion_status` 分布、P50/P95 延迟、子路径耗时、Provider 调用与用量、TTL 清理量；只含聚合数值与标识。
- **追踪记录（Trace Record）**: 全部检索模式的逐请求运行记录（沿用 001 RetrievalRun 与 005 Agent 编排运行），携带 TTL 与正文开关语义：关闭正文时只保留 ID、状态、耗时与错误。
- **超时档位（Timeout Profile）**: 按目标 Host 独立配置的运行超时：节点超时与总超时，且总小于该 Host 的 Tool Call 超时（蓝图 §19）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 单写多读部署可用：writer + ≥2 个 reader 共享 PostgreSQL/Qdrant 部署下，reader 端到端完成 `search_knowledge` 与 `get_evidence`；DeepSeek Harness 为唯一必过参考客户端，其只读检索调用（澄清 Q5：与知识入库无关，入库仅经后端管理面由 writer 执行）须在 writer 实例与 reader 实例的 MCP 端点各自端到端完成并通过输出 Schema 校验；ChatGPT App 与 Claude Code 记录兼容性状态、不作验收阻塞项（沿用 001 SC-005 惯例并扩展至双实例形态）。
- **SC-002**: 双写为零：验收场景中第二个 writer 的全部启动尝试均被拒绝进入写模式（拒绝率 100%，错误信息明确）；整个验收期间跨实例双写事件数 = 0。
- **SC-003**: reader 独立于 writer：writer 停止后，reader 完成检索与证据展开的成功率在该场景中为 100%（因 writer 不可用导致的 reader 失败数 = 0）。
- **SC-004**: Provider 配置可运维：仅通过运行配置完成三类能力到不同 Provider 的分配并端到端检索成功；≥3 类非法配置（未知类型 / 不可达端点 / 维度不匹配）全部在启动时显式失败（显式失败率 100%，静默回退数 = 0）。
- **SC-005**: 向量不混装：切换 Embedding 模型/Provider 时向既有索引版本混入异构向量的事件数 = 0；新索引版本 + 重新向量化为唯一合法路径。
- **SC-006**: 指标可对账：已知验收批次执行后，指标中的请求量、`completion_status` 分布、延迟分位数与 Provider 用量与批次逐条对账（对账偏差 = 0）；指标查询秒级返回；指标中出现查询/证据正文的次数 = 0。
- **SC-007**: 正文开关全模式生效：关闭正文记录后，四种检索模式（dense/hybrid/graph_enhanced/agentic）运行记录中含正文的记录数 = 0，ID/状态/耗时/错误保留完整率 = 100%。
- **SC-008**: 硬性约束保持：验收测试集跨项目泄漏事件数 = 0；Tool 成功响应 Schema 校验通过率 = 100%；证据来源可定位率 = 100%（宪法硬约束、蓝图 §24.2）。
- **SC-009**: 非回归三项判定全过（澄清 Q3 固化）：（1）001 基线 11 条评测集在 writer 与 reader 两实例形态重跑的非延迟指标与 `baseline_report.json` 在 1% 相对容差内逐条一致；（2）001–005 既有验收测试集全部通过；（3）硬性指标保持（与 SC-008 一致）。不设质量阈值、不作质量声明（对照评测要求：无）。
- **SC-010**: 超时护栏有效：服务端总超时小于每个目标 Host 的 Tool Call 超时（配置校验拒绝反向配置）；超时行为产生 `partial`/`no_evidence`/`failed` 而非无响应。
- **SC-011**: 跨实例并发隔离：writer 与 reader 混合 5 并发请求场景中，请求状态、证据与项目作用域串扰事件数 = 0（沿用并扩展 001 SC-008 至跨实例形态）。
- **SC-012**: 降级不回归：remote Provider 故障注入场景中，系统按既有降级契约返回有效 `completion_status`（Provider 层新增硬失败数 = 0）。
- **SC-013**: 跨实例 ID 唯一性：writer + 2 个 reader 并发执行验收批次（含运行/追踪记录写入共享数据库）期间，ID 重复/主键冲突事件数 = 0，所有运行记录 ID 保持 64 位雪花格式（澄清 Q6）。

## 范围内 / 范围外

### 范围内（006）

- writer/reader 实例模式与 `WriteCoordinator` 抽象：PostgreSQL 单 Writer 租约、续约与过期回收、第二个 writer 拒绝、reader 只读约束与 schema 兼容校验、TTL 清理归属 writer（蓝图 §21.2）。
- `SourceObjectStore` 抽象与本地文件系统 Provider（演进接口；不实现 S3，蓝图 §21.2）。
- 统一 Provider 运行配置：local CPU / local GPU / remote API × Embedding / Reranker / LLM 独立选择、启动校验（类型/端点/维度兼容）、独立并发上限、Provider 故障沿用既有降级（蓝图 §17、§18、§21.1）。
- Embedding 模型/Provider 切换的新索引版本与重向量化治理（防混装，宪法 VIII、蓝图 §18.3）。
- 追踪正文开关扩展至全部检索模式、TTL 运行配置化、可查询运行指标（请求量/状态分布/P50/P95/子路径耗时/Provider 用量与成本/TTL 清理量）（蓝图 §20、§23.3 能力域 6）。
- 超时档位运行配置化：按目标 Host 独立配置、服务端总超时小于 Host Tool Call 超时（蓝图 §19）。
- 宪法硬约束合规与 001–005 非回归验收（含固定评测集回归冒烟）；对照评测要求声明为"无"。

### 范围外（不重复 001–005，且不属于 006）

- Web 管理、项目与知识域管理、上传、凭据规范化、解析与结构切片（001/003 已实现；006 复用其管理路径，仅按实例模式约束其可用性）。
- Dense/Sparse 检索、RRF/DBSF 融合、Rerank、图扩展与三 Agent 编排等检索路径本身（001/002/004/005 已实现；006 不改变检索行为与排序逻辑）。
- MCP `search_knowledge` 与 `get_evidence` 对外契约与 Schema 变更、`completion_status` 四态与来源定位格式（001 已确立；006 不修改契约，宪法 VII）。
- 检索质量对照评测与质量阈值（对照要求：无——工程硬化，非检索质量；非回归冒烟除外）。
- 更强 Embedding/Reranker（Qwen3 增强档）的评测与默认路径切换（蓝图 §26 触发条件；006 只提供 Provider 配置能力，不切换默认模型、不作质量声明）。
- 分布式多写协调、S3 兼容对象存储与分布式任务协调器的实际实现（仅 `WriteCoordinator`/`SourceObjectStore` 抽象与本地实现；实际分布式运行属后续演进，蓝图 §21.2）。
- 认证与多用户权限、广义敏感内容脱敏、自动内容同步、Neo4j、MCP Tasks（蓝图 §26 触发条件未满足，不在本期）。
- ONNX/OpenVINO 量化实现（可选性能项 MAY，非验收必需；由 plan.md 决策，蓝图 §18.5）。

## Clarifications

### Session 2026-09-03

- Q: 006 是否把 001–005 已固化的检索护栏（RRF 常数、Rerank 候选预算、图跳数/候选预算、Agent 轮次/节点超时等）收编进统一运行配置体系？ → A: 保持不变：006 只新增自身运行参数（租约、Provider 并发上限、超时档位、TTL 可配置化、正文开关全模式扩展），既有护栏沿用 001–005 固化值与环境变量入口，规格以 FR-029 显式声明沿用清单，不改默认值与上限、不建统一配置面板。
- Q: 006 新增运行参数（Writer 租约续约/过期窗口、LLM/Embedding/Reranker 独立并发上限、运行记录 TTL）固化哪组默认值与上限？ → A: 标准档：租约续约 30s / 过期 90s（续约间隔 3 倍）；并发上限默认 LLM=4、Embedding=8、Reranker=2，上限为默认 2 倍（LLM=8、Embedding=16、Reranker=4）；TTL 默认 7 天沿用；服务端总超时默认沿用 30s 且小于各 Host Tool Call 超时（FR-003/FR-009/FR-019/FR-021）。
- Q: 非回归冒烟应固化哪组评测集与通过判定标准？ → A: 001 基线 11 条 Markdown/Java 集在 writer 与 reader 两实例形态各重跑一遍（与 baseline_report.json 逐条可比、1% 相对容差）+ 001–005 既有 pytest 验收套件全绿 + 硬性指标（泄漏 0/Schema 100%/定位 100%），三项全过为通过（FR-028/SC-009）。
- Q: 新增实体（Writer 租约与实例身份）的标识格式与租约状态机固化为什么？ → A: lease_id=雪花 ID、instance_id=进程启动时生成的 UUID（租约持有者引用）；租约状态机 {active → released（主动释放）| expired（过期可回收）}；Provider 配置与指标键用稳定字符串标识（capability × provider_type）（FR-002/FR-016、Key Entities）。
- Q: 必过的端到端只读检索调用应覆盖哪些 MCP 实例形态作为验收阻塞项？（附澄清：知识入库仅经后端管理面由 writer 执行，外部 Host 从不执行入库，仅调用只读 MCP 检索；writer 实例同时提供只读 MCP 检索端点，蓝图 §21.2） → A: DeepSeek Harness 为唯一必过参考客户端，其调用须在 writer 与 reader 两实例形态的 MCP 端点各自端到端完成并通过 Schema 校验；ChatGPT App 与 Claude Code 记录兼容性状态、不阻塞（SC-001）。
- Q: 多实例部署（writer + 多 reader 同时生成雪花 ID，如检索运行记录）下，跨实例 ID 唯一性如何保障（当前实现固定 worker_id=0，多进程同毫秒会冲突）？ → A: 保持 64 位雪花格式与仓库惯例，并发实例持有互不相同的 worker_id（分配机制：显式运行配置或租约协调，plan.md 决策）；单实例默认 worker_id=0 兼容 001；验收断言多实例并发 ID 冲突 = 0、相同 worker_id 误配置被显式拒绝（FR-030/SC-013）。

## Assumptions

- 006 复用 001–005 已建立的全部检索、管理与编排能力，仅在部署形态、运行配置与可观测性上硬化；不修改检索排序/融合逻辑、Agent 编排行为与对外 MCP 契约。
- 验收部署环境为本机 docker-compose（PostgreSQL 16 + Qdrant）+ 宿主 Python 进程：writer 与 reader 以独立端口启动（管理 API 默认 8000，各 MCP 实例独立端口），默认绑定 127.0.0.1；验收默认部署 2 个 reader。
- 默认 Provider 组合沿用既有实现（BGE-M3 Dense + BGE-Reranker-v2-m3 + 远程 OpenAI-compatible LLM，蓝图 §18.2/§18.4）；本机无 GPU 硬件时，local GPU Provider 以配置校验与显式失败语义验收（有 GPU 硬件时增加执行冒烟）。
- remote Embedding/Reranker Provider 仅在配置了可用 OpenAI-compatible 端点时验收执行路径；DeepSeek API 无原生 Embedding/Rerank 端点（蓝图 §18.4），不作默认依赖。
- 001 数据布局已满足 reader 需求（Chunk 正文、父级上下文与来源定位位于共享数据库，原始文件由 writer 本地保存）；006 的义务是 reader 实例模式与该不变量的验证保障，而非重建数据布局。
- 租约参数（续约 30s / 过期 90s）与 Provider 独立并发上限（LLM 4/8、Embedding 8/16、Reranker 2/4）已由澄清 Q2 固化；指标查询入口形态与各 Host 超时档位具体数值在 plan.md / research.md 精确化（依据蓝图 §19 的 P50/P95 评测）；本规格约束语义、上限关系与可配置性。
- TTL 默认 7 天沿用（001/005 既有值），可由运行配置覆盖。
- 非回归冒烟沿用 001/002/004/005 的可重复性约定：非延迟指标 1% 相对容差，延迟与成本标注环境敏感；重跑在同一环境会话内执行。
- 评测与验收所用的知识库为 001–005 已发布版本（声明所需检索能力），006 不新增评测语料或评测集查询。
- 跨实例 worker_id 的分配机制（显式运行配置 vs 租约协调分配）与误配置检测手段在 plan.md 决策；本规格约束互异性、单实例默认 worker_id=0 与验收断言（澄清 Q6）。
