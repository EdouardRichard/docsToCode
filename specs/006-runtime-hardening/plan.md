# Implementation Plan: Runtime Hardening (006)

**Branch**: `006-runtime-hardening` | **Date**: 2026-09-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-runtime-hardening/spec.md`；技术决策与评测目标闸门见 [research.md](./research.md)（§0 先声明相对 001 基线的评测目标：无对照、非回归冒烟 + 硬性指标保持）；数据模型见 [data-model.md](./data-model.md)；契约见 [contracts](./contracts/)；验证指南见 [quickstart.md](./quickstart.md)。

## Summary

006 在 001–005 已交付的检索能力之上做工程硬化：**单写多读部署**（writer = 管理进程持 PostgreSQL 单 Writer 租约 + 只读 MCP；reader = 仅只读 MCP 连共享 Qdrant/PostgreSQL，`get_evidence` 不依赖 writer 本地文件；`WriteCoordinator`/`SourceObjectStore` 可替换抽象留分布式演进接口）、**统一 Provider 运行配置**（embedding/reranker/llm × local_cpu/local_gpu/remote_api 独立选择 + 启动校验 + 独立并发上限 + Embedding 切换防混装）、**追踪与运行指标**（正文开关扩展至全部检索模式、TTL 配置化、共享库查询时聚合的管理面只读指标端点）、**超时档位运行配置化**（按目标 Host 独立、服务端总超时严格小于 Host Tool Call 超时）与**跨实例 ID 唯一性**（实例注册表互异 worker_id）。检索路径与对外 MCP 契约零改动（宪法 VII）；对照评测要求为"无（工程硬化，非检索质量）"，替代义务 = 非回归三项判定 + 硬性指标保持（research §0）。

## Technical Context

**Language/Version**: Python 3.11+（宪法架构约束：Python/LangGraph/LangChain 后端基线；沿用 001–005）。

**Primary Dependencies**: FastAPI（管理面，复用 001）、FastMCP（MCP 服务，复用 001）、SQLAlchemy async + asyncpg（复用 001）、Alembic（迁移，仅 writer 执行）、Qdrant（检索，复用 001/002，不改）、`BAAI/bge-m3` + `BAAI/bge-reranker-v2-m3`（本地默认，复用）、OpenAI-compatible HTTP（remote Provider 适配协议，蓝图 §17）、httpx（remote Provider 客户端，复用 005 `agents/llm_client.py` 模式）。

**Storage**: PostgreSQL——新增 3 张运行期表：`instance_registry`（实例注册/worker_id 互异约束/心跳）、`writer_lease`（单写租约，active 部分唯一索引）、`runtime_maintenance_log`（TTL 清理量审计）；`retrieval_runs` 扩展列（tool/instance_id/instance_mode/error_summary/trace_body_recorded/provider_usage，query_text 可空化）；运行指标为查询时聚合派生口径（无新表）。Qdrant 不变。详见 [data-model.md](./data-model.md)。

**Testing**: pytest（contract/integration/unit）+ 契约 Schema 校验（json-schema 2020-12，5 个 006 schema）+ 多进程集成测试（writer+2 reader 双写拒绝/租约恢复/reader 独立性/跨实例 ID）+ `eval/instance_form_smoke.py`（001 基线 11 条经 MCP HTTP 双形态冒烟，research §1.10）+ 001–005 既有套件全量回归。

**Target Platform**: 本机 loopback HTTP（单用户，默认 127.0.0.1 绑定，宪法架构约束）；部署形态 = 管理进程（writer-only，默认 8000）+ MCP 进程（writer/reader，各自独立端口，默认 8080 起）；Streamable HTTP MCP 为主。

**Project Type**: web-service（扩展 001–005 既有 backend；frontend 不改动）。

**Performance Goals**: 检索行为零变化（P50/P95 期望无系统性变化——租约/注册/校验均在启动期，指标聚合与 TTL 清理不在请求热路径，research §0.2）；指标查询秒级返回（SC-006）；服务端总超时 30s < 各 Host Tool Call 超时（默认 60000/60000/120000ms，可配置）。

**Constraints**: 硬约束继承（泄漏=0/Schema 100%/定位 100%/显式 project_scope）；不改 `search_knowledge`/`get_evidence` 对外契约（宪法 VII）；既有检索护栏默认值与上限零改动（FR-029 沿用清单）；凭据只以环境变量名引用（宪法 V）；单写多读只是首期部署策略——数据模型/索引版本/MCP 契约不得依赖"永远只有一个 Writer"（蓝图 §21.2）；认证不在本期（蓝图 §26 触发条件）。

**Scale/Scope**: 单用户本机；验收默认 1 writer + 2 reader；跨实例 5 并发混合请求（SC-011）；运行指标窗口受 TTL（默认 7 天）约束；worker_id 空间 0–1023。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.* 依据 `/.specify/memory/constitution.md` v1.2.0。Phase 0 research.md §0 已置顶声明相对 001 基线评测目标（对照要求 = 无（工程硬化，非检索质量）；期望变化：Recall@K/MRR/nDCG = 0 变化（1% 容差）、P50/P95 环境敏感不设阈值且护栏不变、LLM 成本 0；通过判定 = 非回归三项 + 硬性指标），满足"research.md 必须先声明相对 001 基线评测目标，否则不得进入 plan"。

### Non-Negotiable Hard Constraints（蓝图 §24.2）

| 硬约束 | 状态 | 依据 |
|--------|------|------|
| 跨项目泄漏 = 0 | ✅ PASS | FR-024/SC-008：检索路径零改动，作用域过滤沿用 001–005；writer 与 reader 实例同一过滤代码路径；验收集泄漏事件=0（含经 reader 的请求） |
| 无 project_scope 检索须拒绝 | ✅ PASS | FR-023：作用域校验位于共享检索入口，对两实例形态同等生效；缺则拒绝、不回退全库 |
| 上传内容不得作控制指令 | ✅ PASS | 006 不新增内容处理路径；Provider 配置/租约/指标均为控制面数据，与不可信内容无接触（宪法 V 沿用） |
| MCP Schema 合法率 100% | ✅ PASS | FR-025：006 不改两个核心 Tool 的输出 Schema；新增 5 个契约均为内部/管理面 schema（独立 $id `/schemas/006/`）；验收集 100% |
| 证据来源可定位率 100% | ✅ PASS | 检索与证据展开路径零改动，来源定位格式沿用 001/003；reader 形态经共享数据库读取同样定位信息（SC-003） |

### Core Principles（10 条）

| 原则 | 状态 | 依据 |
|------|------|------|
| I 显式知识作用域 | ✅ PASS | FR-023：显式 `project_scope` 要求对 writer/reader 实例同等生效 |
| II 项目事实优先 | ✅ PASS | 006 不触碰检索融合/排序与公共/项目证据并列语义 |
| III 暴露不确定性 | ✅ PASS | 四态 `completion_status` 与错误回溯不变；新增 error_summary 增强错误暴露（FR-020） |
| IV 来源可定位 | ✅ PASS | 来源 ID/版本/位置沿用；追踪记录保留 ID/状态/耗时/错误/证据引用（FR-020） |
| V 数据与控制分离 | ✅ PASS | Provider 配置中凭据仅以环境变量名引用（SecretEnvName，宪法 V）；指标/追踪只含聚合数值与标识、无正文（FR-017） |
| VI 确定性控制优先 | ✅ PASS | 租约抢占/续约/回收、worker_id 分配、超时校验均为确定性 DB 约束与配置校验；不新增 LLM 判断 |
| VII 接口独立演进 | ✅ PASS | 对外 MCP 契约不变（FR-025）；006 契约独立 $id `/schemas/006/` 分版本演进，$ref 复用 006 common.schema.json 共享定义 |
| VIII 知识版本不可混用 | ✅ PASS | FR-013：Embedding 切换必须新索引版本 + 重向量化，启动校验维度一致性拒绝混装（SC-005） |
| IX 同步结果优先 | ✅ PASS | 单写多读不改变同步 Tool Call 返回语义；不依赖 Resources/Tasks |
| X 评测驱动优化 | ✅ PASS | research §0 闸门：对照要求 = 无（工程硬化）；替代义务 = 非回归三项判定 + 硬性指标保持（FR-027/FR-028）；无质量声明 |

**Gate 结论**：无违规；无 Complexity Tracking 条目（无原则豁免）。**Phase 1 设计后复核**：data-model/contracts/quickstart 均不引入新违规——3 张新表与 `retrieval_runs` 扩展均为运行期/审计数据（不进知识库、不进向量库、不改对外 MCP 响应）；5 个 schema 以独立 $id 演进、$ref 复用 006 common.schema.json；Provider 配置不含凭据值；指标契约显式排除正文——全部硬约束与原则保持 PASS。

## Project Structure

### Documentation (this feature)

```text
specs/006-runtime-hardening/
├── plan.md              # 本文件（/speckit-plan 产出）
├── research.md          # Phase 0 产出（§0 评测目标闸门：对照要求"无"+ 非回归判定）
├── data-model.md        # Phase 1 产出（instance_registry/writer_lease/maintenance_log/retrieval_runs 扩展/指标口径）
├── quickstart.md        # Phase 1 产出（8 个端到端验证场景）
├── contracts/           # Phase 1 产出（内部与管理面契约，复用 common.schema.json 共享定义）
│   ├── common.schema.json                # 共享定义（InstanceId/WorkerId/LeaseId/LeaseState/ProviderType/HostTarget 等）
│   ├── writer-lease.schema.json          # 单写租约记录（状态机 + 持有者 + 续约/过期语义）
│   ├── instance-registry.schema.json     # 实例注册记录（worker_id 互异 + 心跳）
│   ├── provider-config.schema.json       # 校验后的统一 Provider 运行配置（凭据仅环境变量名）
│   └── runtime-metrics.schema.json       # 运行指标只读端点响应（聚合数值、无正文）
└── tasks.md             # /speckit-tasks 产出（本命令不创建）
```

### Source Code (repository root)

```text
backend/
├── src/rag_mcp/
│   ├── runtime/                      # 006 新增：实例/租约/存储抽象
│   │   ├── __init__.py
│   │   ├── write_coordinator.py       # WriteCoordinator 抽象 + PostgresLeaseWriteCoordinator（抢占/续约/释放/回收，FR-002/FR-003）
│   │   ├── source_object_store.py     # SourceObjectStore 抽象 + LocalFilesystemSourceObjectStore（演进接口，FR-006）
│   │   ├── instance_registry.py       # 实例注册/心跳/worker_id 分配与误配检测（FR-030/SC-013）
│   │   ├── schema_compat.py           # reader 启动 alembic head 比对（FR-007）
│   │   └── metrics.py                 # 运行指标查询时聚合 + 维护日志读取（FR-016/FR-017）
│   ├── providers/                     # 006 扩展
│   │   ├── base.py                    # 既有 ABC（不变）
│   │   ├── local_cpu.py               # 既有（不变）
│   │   ├── local_cpu_reranker.py      # 既有（不变）
│   │   ├── local_gpu.py               # 006 新增：同模型 GPU device 执行路径（无硬件显式失败）
│   │   ├── remote_api_embedding.py    # 006 新增：OpenAI-compatible /embeddings
│   │   ├── remote_api_reranker.py     # 006 新增：OpenAI-compatible /rerank
│   │   └── factory.py                 # 006 新增：Provider 工厂 + 启动统一校验（类型/端点/维度，FR-010/FR-011）
│   ├── config/
│   │   ├── __init__.py                # Settings 扩展：instance_mode/worker_id/租约/TTL/正文开关/并发上限（沿 env 模式）
│   │   ├── provider_config.py         # 006 新增：三类能力 Provider 配置装载
│   │   └── timeout_profiles.py        # 006 新增：按 Host 超时档位 + server < host 校验（FR-021）
│   ├── api/
│   │   └── runtime_metrics.py         # 006 新增：GET /runtime/metrics（writer 管理面只读端点）
│   ├── eval/
│   │   └── instance_form_smoke.py     # 006 新增：001 基线 11 条经 MCP HTTP 双形态冒烟（FR-028）
│   ├── services/
│   │   └── maintenance_service.py     # 扩展：清理计数写入 runtime_maintenance_log；TTL 配置驱动
│   ├── models/
│   │   └── retrieval_run.py           # 扩展：tool/instance_id/instance_mode/error_summary/trace_body_recorded/provider_usage；query_text 可空
│   ├── mcp/                           # 复用 001（对外契约不变）
│   ├── server.py                      # 扩展：writer 角色校验（reader 显式报错）+ 租约抢占 + 指标路由
│   └── ... (复用 001–005 既有 modules，检索路径零改动)
├── _run_mcp.py                        # 扩展：--mode writer|reader（INSTANCE_MODE 等效）；reader 无维护后台任务
├── alembic/versions/
│   ├── 0060_create_runtime_tables.py # instance_registry + writer_lease + runtime_maintenance_log
│   └── 0061_extend_retrieval_runs.py # retrieval_runs 列扩展 + query_text 可空化 + 聚合索引
└── tests/
    ├── contract/                       # 5 个 006 schema 契约校验 + 对外 MCP schema 不回归
    ├── integration/                    # 多进程：双写拒绝/租约恢复/reader 独立/跨实例 ID/正文开关/指标对账/超时校验
    └── unit/                           # 租约状态机/worker_id 分配/Provider 校验/降级契约不回归
```

**Structure Decision**: 单 web-service 后端，扩展 001–005 既有 `backend/src/rag_mcp/`，新增 `runtime/`（实例/租约/存储抽象/指标）与 `providers/` 扩展（remote/GPU/工厂校验）、`config/` 扩展（provider/timeout 配置）、`api/runtime_metrics.py`、`eval/instance_form_smoke.py`；入口为既有 `server.py`（管理，writer-only）与 `_run_mcp.py`（MCP，--mode writer|reader）。frontend 不改动。

### 数据模型扩展

详见 [data-model.md](./data-model.md)。知识模型（知识域/知识源/版本/Chunk/图）与 Qdrant **零改动**；005 agentic 表零改动（仅 TTL 时长配置驱动）。新增 3 张 PostgreSQL 运行期表：`instance_registry`（worker_id active 部分唯一约束 = 误配检测点）、`writer_lease`（active 部分唯一索引 = 双写为零的 DB 级保证；续约 30s/过期 90s）、`runtime_maintenance_log`（TTL 清理量审计，append-only）；`retrieval_runs` 扩展 7 列（tool/instance_id/instance_mode/error_summary/trace_body_recorded/provider_usage + query_text 可空化，向后兼容：默认开关 true 时旧行为不变）。运行指标 = 查询时聚合派生口径（无新表，窗口受 TTL 约束）。迁移仅由 writer 管理进程执行；reader 启动校验 alembic head。

### 契约变更

契约置于 [contracts/](./contracts/)，$ref 复用 006 common.schema.json 共享定义。**对外 MCP 契约零变更**（FR-025，宪法 VII）：`search_knowledge`/`get_evidence` 输出 Schema、`completion_status` 四态、来源定位格式沿用 001。新增 5 个内部/管理面 schema（独立 $id `/schemas/006/`，分版本演进）：`common.schema.json`（InstanceId/WorkerId/LeaseId/LeaseState/InstanceMode/ProcessRole/ProviderType/ProviderCapability/ConcurrencyLimit/SecretEnvName/HostTarget/MetricKey 等共享定义）、`writer-lease.schema.json`（租约记录与状态机）、`instance-registry.schema.json`（实例注册与 worker_id 互异）、`provider-config.schema.json`（校验后的统一 Provider 配置；凭据仅环境变量名引用）、`runtime-metrics.schema.json`（指标只读端点响应；聚合数值、显式排除正文）。

## Complexity Tracking

> 无 Constitution 违规需豁免——本表为空。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| （无） | — | — |
