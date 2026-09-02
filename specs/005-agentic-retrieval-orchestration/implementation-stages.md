# Implementation Stages & Infrastructure — 005 Agentic Retrieval Orchestration

**Branch**: 005-agentic-retrieval-orchestration | **Date**: 2026-09-02 | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Tasks**: [tasks.md](./tasks.md)

> 交付拆分指南。Part A 给出基础设施配置方式；Part B 给出多阶段实施计划（按 infra 依赖排序、TDD、可配置开关 + 对照评测、不替换 001 默认路径）；Part C 给出每阶段可直接交付实施者的准确提示词。当前进度：T001/T004/T005 已完成并提交（commit f48aa9d，30 契约测试绿）。

---

## Part A — 基础设施配置方式

005 复用 001–004 既有 infra（`backend/src/rag_mcp/config.py` 的 env 驱动 Settings）。所有组件本机 loopback，`docker compose up` 开箱即用；远程部署用 `.env` 覆盖（仓库根，gitignored；参见 `.env.example`）。

### A.1 组件与环境变量

| 组件 | 默认地址 | 环境变量 | 用途 |
|------|----------|----------|------|
| PostgreSQL | `postgresql+asyncpg://postgres:postgres@localhost:5432/rag_mcp` | `DATABASE_URL`（async）/ `DATABASE_URL_SYNC`（psycopg2，alembic 用） | 控制面 + 005 运行期 4 表（evidence_ledger_entry/agent_judgment/context_selection_list/agentic_retrieval_run） |
| Qdrant | `http://localhost:6333` | `QDRANT_URL` | Dense/Sparse 检索（001/002）；005 复用为 Agent 可选信号 |
| 管理 API | `127.0.0.1:8000` | `MANAGEMENT_PORT` | REST 管理（项目/知识源/版本/重建） |
| MCP 服务 | `127.0.0.1:8080` | `MCP_PORT` | Streamable HTTP MCP（`search_knowledge`/`get_evidence`）；DeepSeek Harness E2E 目标 |
| 嵌入/Reranker | 本地 `BAAI/bge-m3` + `BAAI/bge-reranker-v2-m3` | `EMBEDDING_MODEL` | 001/002 本地默认；005 复用 |
| 图扩展（004） | 默认关闭 | `GRAPH_ENHANCED_RETRIEVAL_ENABLED` | 005 多跳图信号来源；评测前默认 false |

### A.2 启动与校验命令

```bash
# 1) 拉起 infra（PostgreSQL + Qdrant；项目提供 docker-compose 默认）
docker compose up -d            # 或本机已运行 postgres:5432 + qdrant:6333

# 2) 安装依赖（含 ml 可选组：sentence-transformers 用于 BGE）
cd backend && py -m pip install -e ".[ml]"

# 3) 应用迁移（001–004 baseline + 005 新增 4 表，见 Stage 2）
alembic upgrade head            # DATABASE_URL_SYNC 指向同一库

# 4) 启动服务（管理 API :8000 + MCP :8080，loopback）
py -m rag_mcp.server            # 等价 uvicorn rag_mcp.server:app --host 127.0.0.1 --port 8000

# 5) 健康与契约校验
curl -s http://127.0.0.1:8000/health
py -m pytest tests/contract/test_005_schemas.py -q     # 已绿（30 passed）
py -m pytest tests/contract/test_mcp_schemas.py -q     # 001 对外契约不回归
```

### A.3 005 配置扩展点（必须镜像 004 GraphConfig 模式）

`backend/src/rag_mcp/config.py` 已有 `GraphConfig` dataclass + `graph_enhanced_retrieval_enabled`（默认 false，文档明示“确定性 001/002 默认路径在对照评测通过前保持不变”）。005 **必须镜像此模式**新增：

- `@dataclass(frozen=True) class AgenticConfig`：`enabled=False`（默认关）、`max_rounds=2`/`max_rounds_cap=3`、`agent_node_timeout_ms=5000`/`cap=10000`、`top_k_max=20`、`max_evidence_per_source=3`/`cap=5`、`total_timeout_ms=30000`、图护栏沿用 004（`graph_*`）。
- `Settings` 增 env 字段 `AGENTIC_RETRIEVAL_ENABLED`（默认 false）、`AGENTIC_MAX_ROUNDS`、`AGENTIC_NODE_TIMEOUT_MS` 等，`@property agentic` 组装 `AgenticConfig`。
- **不变量**：`AGENTIC_RETRIEVAL_ENABLED=false` 时，`search_knowledge`/`get_evidence` 走 001/002/004 确定性路径，零行为变化（FR-024/宪法 VII）；仅当对照评测三段通过（SC-001 ≥3% / SC-002 001 非劣 / SC-015 非回归 + 硬性指标全过）后，运维显式置 true 才进默认路径。

### A.4 LLM Provider 集成点

`backend/src/rag_mcp/providers/base.py` 已声明 `LLMProvider(ABC).structured_complete(prompt, schema) -> dict`（标注 "stub for 005"）。三 Agent 经能力路由（`agents/capability_router.py`）选择 LLMProvider 实现：

- 查询规划（简单）→ 低延迟模型；证据分析（复杂）→ 更强模型（如 DeepSeek V4-Flash，蓝图 §18.4）；上下文编排 → 居中可配。
- 本地优先：本地 LLM 优先（与 BGE 同源本地策略）；远程 API 经 langchain-community/httpx 适配，不绑定供应商（宪法 §18）。
- **测试态**：实现一个 `StubLLMProvider`（按 `schema` 返回固定/参数化结构化输出），使 Agent 单测无需真实 LLM。

### A.5 测试基础设施注意

- `backend/tests/conftest.py` 的 `engine`/`db_session`/`test_client` fixture 使用真实 `settings.database_url`（PostgreSQL）。**需要 DB 的集成测试须 PostgreSQL 在线**；纯逻辑单测（契约/枚举/Agent 降级/账本追加式逻辑）用 stub，不依赖 DB。
- 单测文件放 `backend/tests/unit/...`，集成测试放 `backend/tests/integration/...`，契约测试放 `backend/tests/contract/...`（沿用既有约定）。

---

## Part B — 多阶段实施计划

按 infra 依赖与依赖顺序分 8 阶段。每阶段：TDD（Red→Green）、可配置开关 + 对照评测不变量、按逻辑组提交、`tasks.md` 标 `[X]`。Stage 0 为环境前置（非代码）；Stage 1 为 infra-independent 纯逻辑（单测 + stub）；Stage 2+ 引入 DB/检索/LLM 集成。

| 阶段 | 主题 | 任务 | infra 前置 | 可并行 |
|------|------|------|-----------|--------|
| 0 | 环境搭建 | （非代码） | PostgreSQL + Qdrant + deps + alembic baseline + graph_ready 语料 | — |
| 1 | infra-independent 基础 | T002, T006–T017（除 T003） | 无（stub） | T006/T008/T010/T014/T016 组内 [P] |
| 2 | DB 模型与迁移 | T003 + 4 表 ORM + 账本/判断/选择清单/运行存储 DB 集成 | PostgreSQL | 模型 [P] |
| 3 | US1 查询规划 | T018–T023 | LLM stub（单测）/ Qdrant（集成） | T018‖T020 |
| 4 | US2 证据分析 + 补充检索 | T024–T031 | LLM + 检索集成 | T024‖T026 |
| 5 | US3 上下文编排 | T032–T037 | 账本已有证据 | T032‖T034 |
| 6 | US4 对照评测闸口 | T038–T043 | 完整 Agent 路径 + 001 基线 + graph_ready 语料 | T038‖T040 |
| 7 | 硬化与 E2E | T044–T056 | 全路径 + MCP 服务端 + DeepSeek Harness | T044/T046/T048/T050/T054 [P] |

**通用不变量（每阶段提示词内置）**：① TDD Red→Green；② `AGENTIC_RETRIEVAL_ENABLED` 默认 false，不触碰 001 默认路径与对外 MCP 契约（FR-024/宪法 VII），增强仅在对照评测三段通过后经运维开关进入默认路径（宪法 X）；③ 每逻辑组一次 `git commit`，`tasks.md` 标 `[X]`；④ 固化值见 spec FR-006（护栏）/FR-032（标识与枚举）/SC-001（≥3%）；⑤ 跨项目泄漏=0、Schema 100%、定位 100%（宪法硬约束）。

---

## Part C — 每阶段准确提示词

以下提示词自包含，可直接交付实施者（人或 agent）。前提：实施者已检出 `005-agentic-retrieval-orchestration` 分支，可访问 `specs/005-.../`（spec/plan/research/data-model/contracts/tasks）与 `backend/`。

### 提示词 — Stage 1：infra-independent 基础（T002, T006–T017 除 T003）

> 实现 005 阶段 1 基础设施（纯逻辑，单测 + stub，无需 DB/Qdrant/LLM）。任务：T002（AgenticConfig）、T006/T007（AgentBase 节点 Schema 校验 + 降级）、T008/T009（capability_router）、T010/T011（追加式账本 ledger，stub session 单测）、T012/T013（LangGraph 状态机骨架，9 步 + 护栏 + request_id/run_id 隔离，stub 检索）、T014/T015（trace_recorder，TTL + 可关正文）、T016/T017（state_envelope/agentic_retrieval_run，符合 contracts/agentic-retrieval-run.schema.json）。
> 文件：`backend/src/rag_mcp/config.py`（镜像 `GraphConfig` 新增 `AgenticConfig` + `AGENTIC_RETRIEVAL_ENABLED=false` + agentic_* env 字段 + `@property agentic`）；`backend/src/rag_mcp/agents/{base,capability_router}.py`；`backend/src/rag_mcp/orchestration/{ledger,state_machine,trace_recorder,state_envelope}.py`；`backend/tests/unit/{agents,orchestration}/test_*.py`。
> 约束：TDD（先写失败测试再实现）；`AGENTIC_RETRIEVAL_ENABLED` 默认 false，关闭时零行为变化、不改对外 MCP 契约；Agent 输出经节点 Schema 校验，校验失败回退确定性等价行为并返回有效四态（FR-003/SC-011）；账本只 INSERT 不改写（FR-008）；状态跳转权属确定性控制器非 Agent（宪法 VI）。固化护栏：轮次 2/上限 3、节点超时 5s/上限 10s、top_k≤20、单来源 3/上限 5、总超时 30s（FR-006）。
> LLM：实现 `StubLLMProvider`（按 schema 返回结构化输出）供 Agent 单测，无需真实 LLM；复用 `providers/base.py` 的 `LLMProvider.structured_complete(prompt, schema)`。
> 退出标准：所有单测绿（`py -m pytest tests/unit -q`）；`tasks.md` 标 T002/T006–T017 中已完成项 `[X]`；按逻辑组 git commit（建议 3 组：配置/Agent 基座、账本+追踪+运行包络、状态机骨架）。

### 提示词 — Stage 2：DB 模型与迁移（T003 + 4 表 ORM + 存储集成）

> 前置：PostgreSQL 在线（`DATABASE_URL`/`DATABASE_URL_SYNC` 指向 `localhost:5432/rag_mcp`），`alembic upgrade head` 已应用 001–004 baseline。
> 任务：T003（alembic 迁移建 005 运行期 4 表）+ 为 4 表实现 SQLAlchemy ORM 模型（`backend/src/rag_mcp/models/{evidence_ledger_entry,agent_judgment,context_selection_list,agentic_retrieval_run}.py`，沿用既有 models 风格 + Snowflake ID + `(knowledge_scope_id,project_id,index_version)` 隔离三元组 + TTL 列）+ 将 Stage 1 的 ledger/judgment_store/context_selection/state_envelope 从 stub 切换为真实 AsyncSession 持久化（集成测试用 conftest 的 `db_session` fixture）。
> 文件：`backend/alembic/versions/NNNN_agentic_tables.py`（手写 revision，不 autogenerate 以精确控制）；`backend/src/rag_mcp/models/*.py`；`backend/tests/integration/orchestration/test_*_db.py`。
> 约束：账本表只允许 INSERT（ORM 无 UPDATE 路径，除 TTL 清理）；隔离三元组 CHECK + 索引；跨作用域写入拒绝（FR-022）；运行期表 TTL、不写回知识库（蓝图 §20）；不改 001–004 既有表结构与对外契约。
> 退出标准：`alembic upgrade head` 成功建 4 表；集成测试绿（含追加式不变量、跨作用域拒绝）；T003 标 `[X]`；git commit（"feat(005): runtime tables + ORM models + stores (T003)"）。

### 提示词 — Stage 3：US1 查询规划（T018–T023）

> 前置：Stage 1/2 完成；LLM stub 可用（单测）/ Qdrant 在线（集成）。
> 任务：T018/T019（`agents/query_planner.py`：拆解子问题 `sub_problem_id` 单调、选 signals∈{dense,sparse,graph}、关系方向⊆004 成对、节点 Schema 校验）；T020/T021（关系方向选择默认双向 + 校验失败回退 004 确定性默认，FR-033）；T022/T023（接入状态机步骤 3，`agent_outputs_ref.query_planner.sub_problems` 写入运行记录，步骤 4 并行检索用子问题查询）。
> 文件：`backend/src/rag_mcp/agents/query_planner.py`；`backend/src/rag_mcp/orchestration/state_machine.py`（步骤 3）；`backend/tests/{unit/agents/test_query_planner*.py, integration/test_us1_planner_integration.py}`。
> 约束：TDD；单意图查询→1 子问题无额外开销；图护栏沿用 004（跳数 2/3、预算 10/20、子超时 3s，FR-033）；不改对外契约；`AGENTIC_RETRIEVAL_ENABLED=false` 时查询规划不进入默认路径。
> 退出标准：单测 + 集成测试绿；多跳查询子问题可追溯（SC-009）；T018–T023 标 `[X]`；git commit（"feat(005): query planner agent + step-3 wiring (T018-T023)"）。

### 提示词 — Stage 4：US2 证据分析 + 补充检索有界循环（T024–T031）

> 前置：Stage 3 完成；LLM + 检索集成可用。
> 任务：T024/T025（`agents/evidence_analyst.py`：覆盖度/冲突/缺口结构化判断，固化枚举 {covered,partial,uncovered}/{none,version_conflict,source_conflict,domain_conflict}，项目/公共冲突并列不臆造）；T026/T027（`judgment_store.py` 持久化，round_index 单调，符合 agent-judgment.schema.json）；T028/T029（补充检索有界循环 6→3→4→5→6→7，`rounds_completed≤max_rounds(2)`，补充候选重新进入融合/Rerank/分析并携带分数，**确定性控制器**决定继续/返回，非 Agent 独占）；T030/T031（混合机制终态决策，partial 携带已验证证据+未覆盖+冲突+失败路径，无生成填补）。
> 文件：`backend/src/rag_mcp/agents/evidence_analyst.py`；`backend/src/rag_mcp/orchestration/{judgment_store,state_machine}.py`；`backend/tests/{unit/agents/test_evidence_analyst.py, unit/orchestration/test_terminal_decision.py, integration/test_us2_supplementary_loop.py}`。
> 约束：TDD；`needs_supplementary` 为 Agent 判断输入、确定性控制器消费（宪法 VI）；达上限→partial；四态可区分（SC-011）。
> 退出标准：缺口查询触发补充轮、最终 Recall@K > 单轮基线（集成）；T024–T031 标 `[X]`；git commit（"feat(005): evidence analyst + supplementary loop (T024-T031)"）。

### 提示词 — Stage 5：US3 上下文编排（T032–T037）

> 前置：Stage 4 完成（账本已有证据）。
> 任务：T032/T033（`agents/context_orchestrator.py`：去重、保多样、父级补充、装箱 top_k≤20、`context_result_id` + 追加式选择清单 decision∈{selected,truncated,deduped}、truncated→可展开 evidence_id）；T034/T035（`context_selection.py` 只 INSERT 不改账本）；T036/T037（接入步骤 8 + MCP 序列化桥接，**不改对外契约**，账本经 `(request_id,evidence_id)` 桥接）。
> 文件：`backend/src/rag_mcp/agents/context_orchestrator.py`；`backend/src/rag_mcp/orchestration/{context_selection,state_machine}.py`；`backend/src/rag_mcp/mcp/`（仅桥接，不改 search_knowledge/get_evidence 契约）；`backend/tests/{unit/agents/test_context_orchestrator.py, integration/test_us3_orchestration_integration.py}`。
> 约束：TDD；追加式选择清单不改写账本（FR-008/FR-017）；输出 evidence 项 `additionalProperties:false` 不新增字段（FR-024）。
> 退出标准：重叠证据无重复、截断有可展开 ID、账本原始未改写、输出 Schema 合法（SC-006）；T032–T037 标 `[X]`；git commit（"feat(005): context orchestrator + step-8 bridge (T032-T037)"）。

### 提示词 — Stage 6：US4 对照评测闸口（T038–T043）

> 前置：Stage 1–5 完成（完整 Agent 路径，开关可控）；`eval/baseline_report.json`（001）+ `eval/hybrid_comparison_report.json`（002）存在；已发布 `graph_ready` 知识版本（Java 调用图 + DDL 外键语料，供多跳/缺口/冲突查询）。
> 任务：T038/T039（`eval/agentic_comparison.py`：产出 Recall@K/MRR/nDCG/P50/P95/cost + per_query_comparison（确定性 vs Agent 排名 + 判断 + 账本引用）+ three_gate_pass{sc001,sc002,sc015,hard_metrics} + `enters_default_path`）；T040/T041（评测批次 `eval/agentic_eval_dataset.json` ≥6 条，多跳/缺口/冲突各≥2、含≥1 中文，AI 生成人工审核 JSON）；T042/T043（同会话先重跑确定性基线再跑 Agent，非延迟 1% 容差内一致，延迟环境敏感）。
> 文件：`backend/src/rag_mcp/eval/agentic_comparison.py`；`eval/agentic_eval_dataset.json`；`backend/tests/{unit/eval/test_agentic_comparison.py, integration/test_us4_eval_fairness.py}`。
> 约束：TDD；**不得直接替换 001 默认路径**——`enters_default_path` 仅为报告判定，实际进入默认路径须运维置 `AGENTIC_RETRIEVAL_ENABLED=true` 且三段通过（SC-001 ≥3% / SC-002 001 非劣 1% 容差 / SC-015 002·004 非回归 + 硬性指标泄漏0/Schema100%/定位100%）；未达则 Agent 路径保留为可选、不进默认（宪法 X）。
> 退出标准：对照报告产出、逐查询可解释、三段判定可重复（SC-008）；T038–T043 标 `[X]`；git commit（"feat(005): comparison eval + three-gate (T038-T043)"）。

### 提示词 — Stage 7：硬化与 E2E（T044–T056）

> 前置：Stage 1–6 完成；MCP 服务端可启动（`:8080`）；DeepSeek Harness 已配置连接本系统 MCP URL。
> 任务：T044/T045（跨项目隔离：单作用域不返回他项目证据/账本/判断，无 project_scope 拒绝，泄漏=0）；T046/T047（提示注入防护：不可信数据→证据字段、结构化边界隔离、Agent 输出 Schema 校验、高风险可审计隔离）；T048/T049（5 并发不同 project_scope 无串扰）；T050/T051（硬性指标门：Schema 100% + 定位 100% + 泄漏 0）；T052/T053（DeepSeek Harness E2E：agentic search_knowledge + get_evidence 端到端 + Schema 合法 + 30s<host 超时）；T054/T055（运行态 TTL + 不写回知识库 + 追踪可关正文）；T056（跑 quickstart.md 场景 1–7）。
> 文件：`backend/tests/{integration/test_cross_project_isolation.py, integration/test_prompt_injection_defense.py, integration/test_concurrency_isolation.py, contract/test_hard_metrics.py, e2e/test_deepseek_harness_e2e.py, unit/orchestration/test_run_state_lifecycle.py}`。
> 约束：TDD；多关注点不同文件可并行 [P]；E2E 须 MCP 服务端在线；不改对外契约。
> 退出标准：全部硬化测试绿、E2E 通过、quickstart 场景 1–7 可观测通过；T044–T056 标 `[X]`；git commit（"feat(005): hardening + E2E (T044-T056)"）。

---

## 质量保证检查表（每阶段退出前）

- [ ] 该阶段所有任务 TDD（Red→Green）已完成、测试绿
- [ ] `AGENTIC_RETRIEVAL_ENABLED` 默认 false；关闭时 001 默认路径与对外 MCP 契约零变化
- [ ] 未直接替换 001 确定性默认路径；进入默认路径仅经对照评测三段通过 + 运维开关
- [ ] 跨项目泄漏=0、Schema 100%、定位 100%（宪法硬约束）
- [ ] `tasks.md` 对应任务标 `[X]`；按逻辑组 git commit
- [ ] 未引入新对外契约字段（evidence 项 `additionalProperties:false` 不变）

## 当前进度

- ✅ T001（包目录）、T004/T005（契约校验，30 测试绿，commit f48aa9d）
- ⬜ Stage 1（T002, T006–T017 除 T003）→ Stage 2 → ... → Stage 7

> 实施者按 Stage 1→7 顺序执行，每阶段用对应提示词；阶段内按 tasks.md 的 [P]/[deps] 拓扑与 TDD 顺序。infra 依赖任务（Stage 2 DB、Stage 3+ 检索/LLM、Stage 6 评测语料、Stage 7 E2E）须先满足 Part A 对应组件在线。