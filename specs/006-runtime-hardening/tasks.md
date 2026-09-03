# Tasks: Runtime Hardening (006)

**Input**: Design documents from `/specs/006-runtime-hardening/`

**Prerequisites**: [plan.md](./plan.md)（技术选型/结构/数据模型扩展/契约变更）、[spec.md](./spec.md)（用户故事与优先级）、[research.md](./research.md)（11 项技术决策与评测目标闸门）、[data-model.md](./data-model.md)（3 新表 + retrieval_runs 扩展 + 指标口径）、[contracts/](./contracts/)（5 个 schema）、[quickstart.md](./quickstart.md)（8 个验证场景）。

**Tests**: **TDD 强制**——每个功能任务拆两步：① Red（编写并运行会失败的测试）② Green（实现使测试通过）。Red 任务先行、Green 任务依赖其 Red 任务。

**Organization**: 按用户故事分组，依优先级 P1 → P2 排序；每故事独立可测、可独立交付为增量。

## Format

`- [ ] Txxx [P?] [USx?] <Red|Green>: 描述 — 文件路径`，其下缩进 `- **AC**: 验收标准`。
`[P]` = 可并行（不同文件、无对未完成任务依赖）；`[USx]` = 所属用户故事（仅故事阶段任务必带）。

## Path Conventions

后端为 `backend/src/rag_mcp/`，测试为 `backend/tests/`（contract/integration/unit 子目录）。契约 schema 位于 `specs/006-runtime-hardening/contracts/`（文档工件，已生成，不重复实现）。

---

## Phase 1: Setup（共享基础设施）

**Purpose**: 运行配置骨架扩展（006 全部新增参数的数据结构与装载）。

- [X] T001 [P] Red: 扩展 Settings 的运行配置字段单测（instance_mode/worker_id/租约续约/过期/TTL/正文开关/三类 Provider 配置/并发上限/超时档位缺省与上限钳制）— `backend/tests/unit/test_runtime_settings.py`
  - **AC**: 测试导入 `Settings`/`ProviderConfig`/`TimeoutProfiles` 失败（ImportError），先红；断言缺省值（续约 30/过期 90、TTL 7、并发 LLM 4/8·Embedding 8/16·Reranker 2/4、Host 60000/60000/120000ms）与上限钳制逻辑。
- [X] T002 Green: 实现 `Settings` 扩展与 `config/provider_config.py`、`config/timeout_profiles.py`（frozen dataclass + env 装载 + 上限钳制 + 超时档位校验函数占位）— `backend/src/rag_mcp/config/__init__.py`
  - **AC**: T001 全绿；`get_settings()` 返回含新字段的 `Settings`；非法值（如超上限并发）被钳制或启动校验拒绝。

---

## Phase 2: Foundational（阻塞前置）

**Purpose**: 数据模型与 ID 生成基础——所有用户故事的前置。

**⚠️ CRITICAL**: 本阶段完成前，任何用户故事不可开始。

- [X] T003 [P] Red: 迁移 0060 测试（三张运行期表存在、约束齐全：worker_id active 部分唯一、lease active 部分唯一、CHECK 枚举）— `backend/tests/unit/test_migration_runtime_tables.py`
  - **AC**: 测试对 `instance_registry`/`writer_lease`/`runtime_maintenance_log` 反射断言列/索引/约束，当前迁移缺失 → 失败（先红）。
- [X] T004 Green: 编写 Alembic 迁移 `0060_create_runtime_tables`（三表 + §2.2/§3.2 索引约束 + 外键 holder→instance_registry）— `backend/alembic/versions/0060_create_runtime_tables.py`
  - **AC**: T003 全绿；`alembic upgrade head` 幂等；`idx_registry_worker_active`（WHERE state='active'）与 `idx_lease_single_active`（WHERE state='active'）存在。
- [X] T005 [P] Red: 迁移 0061 测试（retrieval_runs 新列存在、query_text 可空、默认值）— `backend/tests/unit/test_migration_retrieval_runs_ext.py`
  - **AC**: 断言 `tool`/`instance_id`/`instance_mode`/`error_summary`/`trace_body_recorded`/`provider_usage` 列与 `query_text` 可空、`trace_body_recorded` 默认 true；当前缺失 → 失败。
- [X] T006 Green: 编写迁移 `0061_extend_retrieval_runs`（7 列扩展 + query_text 可空化 + 聚合索引 `(instance_mode, tool, created_at)`/`(completion_status, created_at)`）— `backend/alembic/versions/0061_extend_retrieval_runs.py`
  - **AC**: T005 全绿；旧行回填默认（tool='search_knowledge'、trace_body_recorded=TRUE、instance_id/instance_mode NULL）。
- [X] T007 [P] Red: ORM 模型测试（instance_registry/writer_lease/runtime_maintenance_log 字段/约束/关系）— `backend/tests/unit/test_runtime_models.py`
  - **AC**: 对三模型反射字段类型/默认/关系（lease.holder_instance_id FK）；当前模型缺失 → ImportError 失败。
- [X] T008 [P] Green: 实现 ORM 模型 — `backend/src/rag_mcp/models/runtime.py`（含 `InstanceRegistry`/`WriterLease`/`RuntimeMaintenanceLog`）
  - **AC**: T007 全绿；模型与迁移列一致（雪花 BIGINT PK、UUID、CHECK 枚举、TIMESTAMPTZ）。
- [X] T009 [P] Red: retrieval_run 扩展模型测试 — `backend/tests/unit/test_retrieval_run_runtime.py`
  - **AC**: 断言新列映射与 `query_text nullable=True`；当前模型缺新列 → 失败。
- [X] T010 [P] Green: 扩展 `retrieval_run.py`（新增 7 列映射 + query_text 可空）— `backend/src/rag_mcp/models/retrieval_run.py`
  - **AC**: T009 全绿；不破坏 002/004/005 既有字段（retrieval_mode/subpath_timings/evidence_ref_ids/format）。
- [X] T011 [P] Red: SnowflakeGenerator worker_id 参数化测试（互异 worker_id 同毫秒生成互异 ID；同 worker 同毫秒走 sequence；越界报错）— `backend/tests/unit/test_snowflake_worker.py`
  - **AC**: 断言 worker_id 0 与 1 同毫秒生成结果不同；worker_id=1024 抛 ValueError；单实例默认 worker_id=0 兼容既有 `generate_id()`。
- [X] T012 [P] Green: `SnowflakeGenerator(worker_id=...)` 参数化（`generate_id(worker_id)` 或工厂函数）— `backend/src/rag_mcp/utils/snowflake.py`
  - **AC**: T011 全绿；保持既有默认调用不变（模块级 `generate_id()` 默认 worker_id=0）。

**Checkpoint**: 基础就绪——用户故事实现可并行开始。

---

## Phase 3: User Story 1 - 部署单写多读实例（Priority: P1）🎯 MVP

**Goal**: 共享 PostgreSQL/Qdrant 上启动 writer（管理进程持租约 + 只读 MCP）与多个 reader（仅只读 MCP）；第二个 writer 被拒；reader 的 `get_evidence` 不依赖 writer 本地文件（FR-001~FR-007）。

**Independent Test**: 启动 writer 管理进程 + writer MCP + 2 reader MCP，从 reader 端到端 `search_knowledge`/`get_evidence` 过 Schema 校验；强杀 writer 后 reader 仍可用；再启第二个 writer 被拒（quickstart 场景 1/2/3）。

### Tests for User Story 1（先红）

- [X] T013 [P] [US1] Red: WriteCoordinator 状态机单测（抢占/续约/释放/过期回收/第二 writer 拒绝）— `backend/tests/unit/runtime/test_write_coordinator.py`
  - **AC**: 断言：active 唯一（部分唯一索引）；抢占失败返回持有者 instance_id 与到期时间；续约更新 renewed_at/expires_at；过期行可回收；参数化缩短续约/过期窗口可测试（FR-002/FR-003）。
- [X] T014 [US1] Green: 实现 `WriteCoordinator` 抽象 + `PostgresLeaseWriteCoordinator` — `backend/src/rag_mcp/runtime/write_coordinator.py`
  - **AC**: T013 全绿；事务内"先标记过期→INSERT active→唯一冲突检测"语义；续约/释放方法齐全；SQL 与 [data-model.md §3](./data-model.md) 一致。
- [X] T015 [P] [US1] Red: SourceObjectStore 抽象 + 本地 FS 实现单测 — `backend/tests/unit/runtime/test_source_object_store.py`
  - **AC**: 断言读/写/存在性走 `DATA_ROOT`；路径越界（目录穿越）被拒；抽象方法签名与蓝图 §21.2 演进接口一致（FR-006）。
- [X] T016 [P] [US1] Green: 实现 `SourceObjectStore` 抽象 + `LocalFilesystemSourceObjectStore` — `backend/src/rag_mcp/runtime/source_object_store.py`
  - **AC**: T015 全绿；对既有 `data_root` 访问收口（包一层，不改行为）。
- [X] T017 [US1] Red: instance_registry 分配/心跳/误配检测单测（显式 WORKER_ID 冲突拒绝、自动补位最低空闲、心跳过期）— `backend/tests/unit/runtime/test_instance_registry.py`
  - **AC**: 断言：两实例同显式 worker_id → 唯一约束冲突显式拒绝（含冲突实例标识）；未配置自动获最低空闲（单实例=0）；过期行可被回收；注册/心跳事务正确（FR-030/澄清 Q6）。
- [X] T018 [US1] Green: 实现 `instance_registry.py`（注册/心跳/分配 + 唯一冲突检测）— `backend/src/rag_mcp/runtime/instance_registry.py`
  - **AC**: T017 全绿；依赖 T012 worker_id 参数化；writer 管理进程清理过期行。
- [X] T019 [P] [US1] Red: schema_compat 单测（alembic head 一致/不一致分支）— `backend/tests/unit/runtime/test_schema_compat.py`
  - **AC**: 断言 head 一致通过、不一致显式失败并含版本信息（FR-007）。
- [X] T020 [P] [US1] Green: 实现 `schema_compat.py`（比对代码迁移 head 与库内 `alembic_version`）— `backend/src/rag_mcp/runtime/schema_compat.py`
  - **AC**: T019 全绿。
- [X] T021 [US1] Red: 超时档位校验单测（服务端总超时 < 每个 Host Tool Call 超时；反向配置显式拒绝）— `backend/tests/unit/test_timeout_profiles.py`
  - **AC**: 断言 server total（默认 30000）< min(各 Host 60000/60000/120000)；任一 Host ≤ server total 时校验函数拒绝并给出可纠正错误；缺省值装载正确（FR-021/SC-010）。
- [X] T022 [US1] Green: 实现超时档位校验并在实例启动时强制调用（writer 管理进程 + writer/reader MCP 启动前）— `backend/src/rag_mcp/config/timeout_profiles.py`、`backend/src/rag_mcp/server.py`、`backend/_run_mcp.py`
  - **AC**: T021 全绿；启动时反向配置显式失败；超时数值变更不要求改 MCP 契约（FR-022）。
- [X] T023 [US1] Red: server.py 模式强制单测（reader 显式报错、writer 抢租约失败即拒启、TTL 清理归属 writer）— `backend/tests/unit/test_server_mode.py`
  - **AC**: 断言 `--mode reader` 启动管理进程抛明确错误；writer 抢租约失败不进入写模式；reader 不挂写路径后台任务（FR-001/FR-004）。
- [X] T024 [US1] Green: `server.py` 角色强制 + 租约抢占 + 指标路由占位 — `backend/src/rag_mcp/server.py`
  - **AC**: T023 全绿；writer 启动顺序=抢租约→建引擎→注册指标路由→起 TTL 循环。
- [X] T025 [US1] Red: _run_mcp.py 模式单测（--mode writer|reader、INSTANCE_MODE 等效、reader 无维护后台）— `backend/tests/unit/test_run_mcp_mode.py`
  - **AC**: 断言 reader MCP 不启动入库/TTL 清理/迁移；两形态均注册 `search_knowledge`/`get_evidence`（FR-001/FR-004）。
- [X] T026 [US1] Green: `_run_mcp.py --mode writer|reader`（实例注册 + worker_id 分配 + schema 校验 + 超时档位校验）— `backend/_run_mcp.py`
  - **AC**: T025 全绿；依赖 T018/T020/T022；writer MCP 与 reader MCP 均注册实例行（process_role=mcp）。

### Integration for User Story 1（先红，串起全链路）

- [X] T027 [US1] Red: 双写拒绝集成测试（两管理进程竞争）— `backend/tests/integration/test_runtime_double_writer.py`
  - **AC**: 断言第二个 writer 100% 拒绝进入写模式、错误含持有者信息、全程双写事件=0（SC-002）。
- [X] T028 [US1] Green: 打通租约抢占 → server 模式 → 双写拒绝链路，使 T027 转绿 — 涉及 `backend/src/rag_mcp/runtime/write_coordinator.py`、`backend/src/rag_mcp/server.py`
  - **AC**: T027 全绿；无静默降级为 reader。
- [X] T029 [US1] Red: reader 独立性集成测试（停 writer 后 reader 检索/展开成功、无 writer 本地文件访问）— `backend/tests/integration/test_runtime_reader_independence.py`
  - **AC**: 断言 writer 停止后 reader `get_evidence` 成功率 100%、失败数=0、无对 writer 本地路径访问（SC-003/FR-005）。
- [X] T030 [US1] Green: 打通 reader 路径（共享库证据展开），使 T029 转绿 — 涉及 `backend/src/rag_mcp/mcp/get_evidence.py`
  - **AC**: T029 全绿。
- [X] T031 [US1] Red: 租约恢复集成测试（强杀 writer、缩短过期窗口、重启抢回）— `backend/tests/integration/test_runtime_lease_recovery.py`
  - **AC**: 断言回收窗口内第二个 writer 被拒、过期后新 writer 抢回续约、期间 reader 不受影响（FR-003）。
- [X] T032 [US1] Green: 打通租约过期回收 + 续约循环，使 T031 转绿 — 涉及 `backend/src/rag_mcp/runtime/write_coordinator.py`
  - **AC**: T031 全绿。
- [X] T033 [US1] Red: DeepSeek Harness 双形态必过集成测试（经 writer 与 reader 两 MCP 端点各完成 search_knowledge + get_evidence 并过 Schema）— `backend/tests/integration/test_deepseek_harness_dual_form.py`
  - **AC**: 断言 DeepSeek Harness 参考客户端在两实例形态端到端调用成功且输出过 Schema；ChatGPT App/Claude Code 记录兼容性状态不阻塞（SC-001/澄清 Q5）。
- [X] T034 [US1] Green: 复用 001 `test_deepseek_harness_e2e.py` 的 Harness 客户端适配为双形态（参数化 writer/reader 端点），使 T033 转绿 — `backend/tests/integration/test_deepseek_harness_dual_form.py`
  - **AC**: T033 全绿。
- [X] T035 [US1] Red: 跨实例并发隔离集成测试（writer + reader 混合 5 并发，状态/证据/作用域串扰=0）— `backend/tests/integration/test_runtime_cross_instance_concurrency.py`
  - **AC**: 断言混合 5 并发请求无请求状态/证据/项目作用域串扰，运行状态按 request_id/run_id 隔离（SC-011/FR-008）。
- [X] T036 [US1] Green: 复用 001 `test_concurrency.py` harness 扩展为跨实例并发，使 T035 转绿 — `backend/tests/integration/test_runtime_cross_instance_concurrency.py`
  - **AC**: T035 全绿。

---

## Phase 4: User Story 2 - Provider 运行配置（Priority: P1）

**Goal**: 三类能力（embedding/reranker/llm）各自独立选择 Provider（local_cpu/local_gpu/remote_api）+ 启动统一校验 + 独立并发上限 + Embedding 切换防混装（FR-008~FR-015）。

**Independent Test**: 仅改运行配置将三类能力指向不同 Provider 并端到端检索成功；提交非法配置（未知类型/不可达端点/维度不匹配）全部启动显式失败；Embedding 维度切换拒绝混装（quickstart 场景 4）。

### Tests for User Story 2（先红）

- [X] T037 [US2] Red: Provider 工厂校验单测（类型合法/必填完备/remote 端点探测/维度一致性）— `backend/tests/unit/providers/test_provider_factory.py`
  - **AC**: 断言：未知 provider_type、remote 缺端点、维度不匹配均显式失败且 errors 含可纠正信息；合法配置通过（FR-010/FR-011/SC-004）。
- [X] T038 [US2] Green: 实现 `providers/factory.py`（三类能力 Provider 装配 + 统一校验 + 维度比对入口）— `backend/src/rag_mcp/providers/factory.py`
  - **AC**: T037 全绿；供应商中立（FR-012）：OpenAI/Anthropic-compatible 仅作适配协议、不绑供应商；校验结果符合 [provider-config.schema.json](./contracts/provider-config.schema.json)。
- [X] T039 [P] [US2] Red: remote embedding 单测（OpenAI-compatible /embeddings、失败返回 None/降级）— `backend/tests/unit/providers/test_remote_api_embedding.py`
  - **AC**: 断言 /embeddings 调用形态、维度返回、HTTP 错误/超时/畸形 → 显式降级不抛入状态机（FR-015）。
- [X] T040 [P] [US2] Green: 实现 `providers/remote_api_embedding.py` — `backend/src/rag_mcp/providers/remote_api_embedding.py`
  - **AC**: T039 全绿。
- [X] T041 [P] [US2] Red: remote reranker 单测（OpenAI-compatible /rerank）— `backend/tests/unit/providers/test_remote_api_reranker.py`
  - **AC**: 断言 /rerank 调用形态、分数归一、失败降级（FR-015）。
- [X] T042 [P] [US2] Green: 实现 `providers/remote_api_reranker.py` — `backend/src/rag_mcp/providers/remote_api_reranker.py`
  - **AC**: T041 全绿。
- [X] T043 [P] [US2] Red: local_gpu 单测（device 参数化、无硬件显式失败语义）— `backend/tests/unit/providers/test_local_gpu.py`
  - **AC**: 断言 device='cuda' 路径选择、无 GPU 时启动校验显式失败（不静默回退 CPU）（FR-010/Assumptions）。
- [X] T044 [P] [US2] Green: 实现 `providers/local_gpu.py`（同模型 GPU device 执行路径）— `backend/src/rag_mcp/providers/local_gpu.py`
  - **AC**: T043 全绿。
- [X] T045 [US2] Red: Provider 独立并发上限单测（信号量按能力隔离、超限有界）— `backend/tests/unit/providers/test_provider_concurrency.py`
  - **AC**: 断言 LLM/Embedding/Reranker 上限互不影响、超限请求被排队/拒绝护栏约束（FR-009）。
- [X] T046 [US2] Green: 并发上限接入工厂/Provider 包装层 — `backend/src/rag_mcp/providers/factory.py`
  - **AC**: T045 全绿。

### Integration for User Story 2（先红）

- [X] T047 [US2] Red: Provider 配置端到端 + 非法配置启动集成测试 — `backend/tests/integration/test_provider_config_integration.py`
  - **AC**: 断言仅经运行配置完成三类能力到不同 Provider 并检索成功、对外契约不变；≥3 类非法配置启动显式失败、静默回退=0（SC-004）。
- [X] T048 [US2] Green: 打通配置装载→工厂校验→运行时装配，使 T047 转绿 — 涉及 `backend/src/rag_mcp/config/provider_config.py`、`backend/src/rag_mcp/providers/factory.py`
  - **AC**: T047 全绿。
- [X] T049 [US2] Red: Embedding 维度切换防混装集成测试 — `backend/tests/integration/test_embedding_switch_no_mix.py`
  - **AC**: 断言不同维度/模型 Embedding 直接作用既有索引版本被拒、唯一合法路径=新索引版本+重向量化、混装事件=0（SC-005/FR-013）。
- [X] T050 [US2] Green: 打通维度一致性校验 + 新索引版本路径，使 T049 转绿 — 涉及 `backend/src/rag_mcp/providers/factory.py`、`backend/src/rag_mcp/services/ingestion_service.py`
  - **AC**: T049 全绿。
- [X] T051 [US2] Red: remote Provider 故障注入集成测试（连接失败/超时/HTTP 错误 → 有效四态、硬失败=0）— `backend/tests/integration/test_provider_fault_injection.py`
  - **AC**: 断言 remote embedding/reranker/llm 故障注入时检索路径返回有效 completion_status 四态、Provider 层新增硬失败数=0、状态机不阻塞（SC-012/FR-015）。
- [X] T052 [US2] Green: 打通 Provider 故障→既有降级契约（005 确定性回退），使 T051 转绿 — `backend/tests/integration/test_provider_fault_injection.py`
  - **AC**: T051 全绿。

---

## Phase 5: User Story 3 - 追踪与运行指标（Priority: P2）

**Goal**: 正文开关覆盖全部检索模式、TTL 配置化、可查询运行指标（请求量/状态分布/P50/P95/子路径耗时/Provider 用量/TTL 清理量），指标无正文（FR-016~FR-020）。

**Independent Test**: 已知批次执行后查询指标逐条对账（偏差=0、秒级、无正文）；关闭正文后四种模式记录均无正文且 ID/状态/耗时/错误保留（quickstart 场景 5）。

### Tests for User Story 3（先红）

- [X] T053 [US3] Red: 正文开关全模式单测（query_text 置空、trace_body_recorded 标记、ID/状态/耗时/错误保留）— `backend/tests/unit/test_trace_body_switch.py`
  - **AC**: 断言 TRACE_BODY_ENABLED=false 时四种模式运行记录 query_text IS NULL 且 trace_body_recorded=FALSE、ID/状态/耗时/错误保留完整率 100%；AGENTIC_TRACE_BODY_ENABLED 兼容别名生效（FR-018/FR-019/SC-007）。
- [X] T054 [US3] Green: 统一正文开关接入运行记录写入（dense/hybrid/graph_enhanced/agentic）— `backend/src/rag_mcp/services/retrieval_service.py`、`backend/src/rag_mcp/orchestration/persistence.py`
  - **AC**: T053 全绿。
- [X] T055 [P] [US3] Red: provider_usage 记录单测（embedding/rerank/llm 调用计数与字符量、缓存命中不计）— `backend/tests/unit/test_provider_usage.py`
  - **AC**: 断言 provider_usage JSONB 口径与 005 真实调用口径一致（llm 缓存命中不计数）（FR-016）。
- [X] T056 [P] [US3] Green: 请求完成时内存累计 provider_usage 并随运行记录写入 — `backend/src/rag_mcp/services/retrieval_service.py`
  - **AC**: T055 全绿。
- [X] T057 [US3] Red: 指标聚合单测（计数/状态分布/percentile/子路径/provider 用量/TTL 清理量）— `backend/tests/unit/test_runtime_metrics.py`
  - **AC**: 断言聚合 SQL 口径与 [data-model.md §6](./data-model.md) 一致、按 instance_mode/tool 分组、窗口受 TTL 约束、结果可序列化为 [runtime-metrics.schema.json](./contracts/runtime-metrics.schema.json)。
- [X] T058 [US3] Green: 实现 `runtime/metrics.py`（查询时聚合 + 指标组装）— `backend/src/rag_mcp/runtime/metrics.py`
  - **AC**: T057 全绿；聚合限定 TTL 窗口；不含任何正文。
- [X] T059 [US3] Red: 维护日志单测（TTL 清理计数写入、append-only）— `backend/tests/unit/test_maintenance_log.py`
  - **AC**: 断言 purge 行数写入 `runtime_maintenance_log`、只 INSERT、可聚合（FR-016）。
- [X] T060 [US3] Green: 扩展 `maintenance_service.py`（清理计数写日志 + TTL 配置驱动）— `backend/src/rag_mcp/services/maintenance_service.py`
  - **AC**: T059 全绿；TTL 清理归属 writer（reader 不运行）。
- [X] T061 [US3] Red: 指标端点契约测试（GET /runtime/metrics 响应 schema 校验 + 无正文 + 秒级）— `backend/tests/contract/test_runtime_metrics_schema.py`
  - **AC**: 断言响应通过 [runtime-metrics.schema.json](./contracts/runtime-metrics.schema.json) 校验、全文不含 query_text/evidence 正文、查询秒级（FR-016/FR-017/SC-006）。
- [X] T062 [US3] Green: 实现 `api/runtime_metrics.py` 端点 + 挂到 writer 管理面 — `backend/src/rag_mcp/api/runtime_metrics.py`
  - **AC**: T061 全绿；仅 writer 管理面暴露（reader 不启动管理面）。

### Integration for User Story 3（先红）

- [X] T063 [US3] Red: 指标对账集成测试（已知批次逐条对账）— `backend/tests/integration/test_runtime_metrics_reconcile.py`
  - **AC**: 断言请求量/状态分布/延迟分位数/provider 用量与批次逐条对应、对账偏差=0（SC-006）。
- [X] T064 [US3] Green: 打通运行记录扩展→聚合→端点链路，使 T063 转绿 — 涉及 `backend/src/rag_mcp/runtime/metrics.py`、`backend/src/rag_mcp/api/runtime_metrics.py`
  - **AC**: T063 全绿。

---

## Phase 6: User Story 4 - 硬性约束与非回归验收（Priority: P2）

**Goal**: 宪法硬约束（显式 project_scope/泄漏=0/Schema 100%/定位 100%）在 writer+reader 部署成立；001–005 既有套件全绿；非回归三项判定（FR-022~FR-028）。

**Independent Test**: 在 writer+reader 部署跑硬约束验收集与 001–005 既有测试 + 001 基线 11 条双形态冒烟（quickstart 场景 7/8）。

### Tests for User Story 4（先红）

- [X] T065 [US4] Red: 006 全量契约测试（5 个 schema 合法 + $ref 解析 + 对外 MCP schema 不回归）— `backend/tests/contract/test_006_schemas.py`
  - **AC**: 断言 5 个 006 schema 经 json-schema 2020-12 校验、`common.schema.json` $ref 全解析、`search_knowledge`/`get_evidence` 输出 schema 不变（FR-025/宪法 VII）；负向约束保持：不执行质量对照评测（FR-027）、既有护栏不改（FR-029）。
- [X] T066 [US4] Green: 契约校验 harness 接入（读 specs/006-runtime-hardening/contracts/*.schema.json）— `backend/tests/contract/test_006_schemas.py`
  - **AC**: T065 全绿。
- [X] T067 [US4] Red: 跨实例 ID 唯一性集成测试（writer+2 reader 并发、worker_id 互异、零冲突、同 worker_id 误配拒绝）— `backend/tests/integration/test_cross_instance_id.py`
  - **AC**: 断言并发批次运行记录主键零冲突、活跃 worker_id 互异、同 WORKER_ID 第二实例启动显式拒绝（SC-013/FR-030）。
- [X] T068 [US4] Green: 打通 worker_id 分配→雪花生成→注册约束链路，使 T067 转绿 — 涉及 `backend/src/rag_mcp/runtime/instance_registry.py`、`backend/src/rag_mcp/utils/snowflake.py`
  - **AC**: T067 全绿。
- [X] T069 [US4] Red: 实例形态冒烟适配器（001 基线 11 条经 MCP HTTP 双形态执行 + 逐条对照 baseline_report.json）— `backend/tests/eval/test_instance_form_smoke.py`
  - **AC**: 断言 writer 与 reader 两形态各重跑 11 条、非延迟指标 1% 容差内逐条一致、P50/P95 记录对照标注环境敏感（FR-028/SC-009）。
- [X] T070 [US4] Green: 实现 `eval/instance_form_smoke.py`（MCP Streamable HTTP 适配 + 对照报告）— `backend/src/rag_mcp/eval/instance_form_smoke.py`
  - **AC**: T069 全绿。
- [X] T071 [US4] Red: 双形态硬约束集成测试（泄漏=0、Schema 100%、定位 100%、缺 project_scope 拒绝，含 reader 请求）— `backend/tests/integration/test_runtime_hard_constraints.py`
  - **AC**: 断言 writer+reader 部署验收集泄漏事件=0、Tool 成功响应 100% 过 Schema、来源可定位率 100%、缺作用域请求被拒且不回退全库（FR-023/FR-024/FR-025/SC-008）；未认证服务默认绑定本机（FR-026）。
- [X] T072 [US4] Green: 复用 001–005 硬指标 harness 于双形态，使 T071 转绿 — 涉及 `backend/tests/contract/test_hard_metrics.py`、`backend/tests/integration/test_runtime_hard_constraints.py`
  - **AC**: T071 全绿。

---

## Phase 7: Polish & Cross-Cutting（收尾）

**Purpose**: 文档、配置样例与全量回归闸门。

- [X] T073 [P] 更新 `.env.example`（006 全部新变量 + 注释与缺省值）— `.env.example`
  - **AC**: 列出 instance_mode/worker_id/租约/TTL/正文开关/三类 Provider/并发上限/超时档位变量；注释说明默认值与上限。
- [X] T074 [P] 运行 [quickstart.md](./quickstart.md) 8 个场景逐条对照，记录偏差 — `specs/006-runtime-hardening/quickstart.md`
  - **AC**: 8 个场景全部可复现；任何偏差回写为任务修正。
- [X] T075 全量回归闸门：001–005 既有 pytest 套件 + 006 新增套件全绿；`research.md §0` 非回归三项判定通过 — `backend/tests/`
  - **AC**: 001–005 既有测试零回归；006 契约/集成/单测全绿；非回归三项判定通过（对照评测要求：无）。

---

## Dependencies（拓扑编排）

**串行链（关键路径）**：T001→T002 →（Phase 2 前置：T003→T004、T005→T006 迁移先行）→ T007→T008、T009→T010、T011→T012 → **US1**：T013→T014、T017→T018（依赖 T012）、T021→T022（超时校验）、T023→T024（依赖 T014）、T025→T026（依赖 T018/T020/T022）→ T027→T028→T029→T030→T031→T032→T033→T034→T035→T036 → **US2**：T037→T038→T039~T046→T047→T048、T049→T050、T051→T052 → **US3**：T053→T054、T057→T058→T061→T062→T063→T064 → **US4**：T065→T066、T067→T068、T069→T070、T071→T072 → **Polish**：T073、T074、T075。

**用户故事完成顺序**：US1 → US2 → US3 → US4（US1/US2 同为 P1，US1 为 MVP；US2 不依赖 US1 可理论并行，但 Provider 工厂是 reader 实例装配的一环，建议 US1 先行）。

**跨故事依赖**：US3 依赖 US1 的 `instance_registry`/`instance_mode` 归属（运行记录写 instance_id/instance_mode）；US4 依赖 US1（双形态部署）+ US3（指标/运行记录扩展）+ US2（Provider 降级契约）。

## Parallel Execution Examples

**Phase 2 并行**：T003/T004 与 T005/T006（迁移组）可与 T007/T008、T009/T010、T011/T012（模型/工具组）并行——模型组依赖迁移组完成（列定义对齐）。

**US1 内并行**：T013/T014（WriteCoordinator）、T015/T016（SourceObjectStore）、T019/T020（schema_compat）、T021/T022（超时校验）四线并行；T017/T018（registry）依赖 T012 完成后并入。

**US2 内并行**：T039/T040、T041/T042、T043/T044（三个 Provider 实现）三线并行，随后 T045/T046（并发上限）与 T037/T038（工厂）汇合。

**US3 内并行**：T053/T054（正文开关）与 T055/T056（provider_usage）与 T059/T060（维护日志）三线并行，T057/T058（聚合）在其后汇合。

**US4 内并行**：T065/T066（契约）、T067/T068（跨实例 ID）、T069/T070（冒烟）、T071/T072（硬约束）四线并行（均依赖 US1/US3 完成）。

## Implementation Strategy（MVP 优先）

1. **MVP = US1（单写多读）**：Phase 1+2 基础 + US1 交付，即可独立验收"部署形态硬化"核心价值（availability 解耦 + 双写拒绝 + reader 独立 + 超时护栏 + Harness 双形态必过 + 跨实例并发隔离）。
2. **US2（Provider 配置）**：P1 第二交付——GPU/云端适配与防混装；与 US1 串行推进。
3. **US3（追踪与指标）**：P2——运维可观测；依赖 US1 的实例归属。
4. **US4（硬约束与非回归）**：P2——发布闸门；前三者完成后跑全量回归与双形态硬指标。
5. 每个用户故事完成即为一可演示、可验收、可发布的增量（对应 quickstart 场景）；对照评测要求为"无"——仅非回归三项判定 + 硬性指标保持。
