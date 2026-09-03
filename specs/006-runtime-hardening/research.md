# Research: Runtime Hardening (006)

**Branch**: `006-runtime-hardening` | **Date**: 2026-09-03 | **Spec**: [spec.md](./spec.md)

**Scope Basis**: 蓝图 §23.4.6（006 纵向交付：单写多读实例、Provider 配置、追踪、运行指标和演进接口）、§19（延迟与降级）、§20（运行状态保留）、§21（实例与并发模型）；支撑 §17、§18、§16.3、§24.2/§24.3。

> 本文件为 `/speckit-plan` Phase 0 产出。所有 `[NEEDS CLARIFICATION]` 已在 spec.md 澄清阶段闭合（两轮共 6 问）；本文固化技术决策与相对 001 基线评测目标。006 是工程硬化 Feature，宪法原则 X 的适用方式为"非回归 + 硬性指标保持"，故下列**评测目标闸门**置于全文之首——本节即 `research.md 必须先声明相对 001 基线评测目标` 的声明。

---

## 0. 相对 001 基线的评测目标（进入 plan 的闸门）

依据 spec §"对照评测声明（001 确定性基线）"、FR-026~FR-028、FR-029 与 SC-008/SC-009：本 Feature 的对照评测要求为 **无（工程硬化，非检索质量）**——不新增检索信号、不改变融合/排序逻辑、不修改 MCP 对外契约，因此不存在质量增量对照；评测义务仅为"行为不变确认 + 硬性指标保持"。

### 0.1 基线值（已记录于 eval 工件）

| 指标 | 001 Dense 基线（`eval/baseline_report.json`） |
|------|--------------------------------------------------|
| 评测集查询数 | 11（Markdown + Java，`eval/eval_dataset.json` 原批次） |
| Recall@K (mean, K=5) | 1.0 |
| MRR (mean) | 0.9091 |
| nDCG@K (mean) | 0.9329 |
| 延迟 P50 | 138.45 ms |
| 延迟 P95 | 185.15 ms |
| 嵌入模型 | BAAI/bge-m3（local CPU） |
| LLM 成本 | 0（确定性 Dense 路径无 LLM 调用） |

### 0.2 期望变化（006 = 工程硬化：部署形态 + 运行配置 + 可观测性）

| 指标 | 期望变化（相对 001 基线，writer 与 reader 两实例形态各重跑一遍） | 判定口径 |
|------|--------------------------------------------------------------------|----------|
| **Recall@K** | **0 变化**：逐查询与基线一致 | 精确一致（Recall 为离散量） |
| **MRR** | **0 变化**（±1% 相对容差，沿用 001/002/004/005 可重复性约定） | 逐查询排名一致 |
| **nDCG@K** | **0 变化**（±1% 相对容差） | 逐查询一致 |
| **P50** | 环境敏感、不设阈值；**期望无系统性变化**（租约校验/worker_id 分配发生在启动期，指标聚合与 TTL 清理不在请求热路径，Provider 并发上限对 5 并发验收批次无影响） | 记录对照并标注 env_sensitive |
| **P95** | 环境敏感；护栏不变：**P95 < 30s 服务端总超时 < 各目标 Host Tool Call 超时**（蓝图 §19/FR-021） | 记录对照 + 护栏断言 |
| **模型成本** | **0 变化**：006 不新增任何 LLM 调用（确定性 Dense 冒烟路径无 Agent） | 与基线同为 0 |

### 0.3 通过判定（三项全过，FR-028/SC-009，澄清 Q3 固化）

1. **冒烟一致**：001 基线 11 条 Markdown/Java 评测集在 writer 与 reader 两实例形态各重跑一遍，非延迟指标与 `baseline_report.json` 逐条对照、1% 相对容差内一致（延迟与成本标注环境敏感）。
2. **既有套件全绿**：001–005 既有 pytest 验收测试集全部通过（含 002/004/005 评测类测试——其已覆盖混合/图/Agent 路径行为）。
3. **硬性指标保持**：跨项目泄漏 = 0、MCP Schema 合法率 = 100%、来源可定位率 = 100%（宪法硬约束、蓝图 §24.2）。

### 0.4 明确不作的事

不设置任何质量提升阈值、不执行质量对照评测、不作质量提升声明（FR-027）；更强 Embedding/Reranker 的评测与默认路径切换属蓝图 §26 触发条件，006 只交付 Provider 配置能力。

---

## 1. 技术决策

### 1.1 Writer 租约：PostgreSQL 租约表行（非 advisory lock）

- **Decision**: `writer_lease` 表单行租约：INSERT 抢占 + 部分唯一索引（仅 state='active' 一行）+ 周期续约（默认 30s）+ 过期回收（默认 90s 窗口）。抢不到即拒绝进入写模式并返回持有者信息。
- **Rationale**: 蓝图 §21.2 明言"PostgreSQL 单 Writer 租约"；租约状态（持有者、续约时间、到期时间）需要可观测、可审计（运营排查"谁是 writer"），表行天然满足；崩溃后过期回收语义清晰（FR-003）。
- **Alternatives considered**: ① `pg_advisory_lock`——连接生命周期绑定、状态不可查询、调试困难，否决；② etcd/Redis 分布式锁——引入新基础设施，违反本机优先与首期范围，否决（留作 `WriteCoordinator` 抽象的未来实现）。

### 1.2 跨实例 worker_id：实例注册表唯一约束 + 显式配置优先、自动补位

- **Decision**: 新增 `instance_registry` 表：每个进程启动时注册（`instance_id` UUID PK、`worker_id` 带 active 部分唯一约束、心跳续期）；`WORKER_ID` 显式配置优先，未配置时自动选取最低空闲 worker_id；唯一约束冲突 = 相同 worker_id 误配置 → 启动显式拒绝（spec Edge Case、澄清 Q6）。writer 管理进程负责清理过期注册行。
- **Rationale**: 兼顾三目标：保持 64 位雪花格式与仓库惯例（001 知识域/005 账本）、误配置可检测（DB 约束天然仲裁）、单实例默认 worker_id=0 兼容 001 既有实现。
- **Alternatives considered**: ① 纯显式配置无注册表——无法检测两实例同 worker_id 的误配置，违反 Edge Case 要求，否决；② UUIDv7 替换雪花——改动所有 run/ledger 主键生成路径且背离仓库惯例，否决；③ 租约表集中分配——reader 不持租约，ID 生成不应依赖 writer 可用性，否决。

### 1.3 运行指标：共享库查询时聚合 + 管理面只读端点

- **Decision**: 不建指标存储表；运行指标 = 对共享 PostgreSQL 运行期表（`retrieval_runs` + 005 agentic 表 + 新增维护日志）的**查询时聚合**，经 writer 管理面只读端点（`GET /runtime/metrics`）暴露；`retrieval_runs` 扩展 `instance_id`/`instance_mode`/`tool`/`provider_usage` 列使聚合可按实例与 Tool 分组、可与验收批次逐条对账（SC-006）；`get_evidence` 调用纳入运行记录（`tool` 列区分）。
- **Rationale**: reader 的检索活动经共享库天然覆盖（reader 写运行记录属逐请求运行状态，FR-004 豁免）；重启不丢指标；对账=直接数行数，与 TTL 窗口一致；零新增写路径（聚合不在请求热路径，P50/P95 期望变化=0 的依据之一）。
- **Alternatives considered**: ① 实例内存计数器——重启即失、reader 需各自暴露端点（违反 §21.2 reader 只提供 MCP 检索），否决；② 定期 rollup 表——请求路径外新增写任务与一致性窗口，收益不抵复杂度，否决（指标窗口受 TTL 约束已声明为可接受语义）。

### 1.4 Provider 配置载体：环境变量 + 启动校验工厂（沿用 001 Settings 模式）

- **Decision**: 统一以环境变量（可选 `.env`）声明三类能力各自的 Provider：`{EMBEDDING|RERANKER|LLM}_PROVIDER_TYPE`（local_cpu / local_gpu / remote_api）+ `*_MODEL` + `*_ENDPOINT` + `*_API_KEY_ENV`（凭据按环境变量**名**引用，值不落配置）+ `*_CONCURRENCY_LIMIT`；`providers/factory.py` 启动时统一校验（类型合法、必填完备、remote 端点尽力探测、Embedding 维度与活跃 Dense 集合维度一致），非法即显式失败（SC-004）。
- **Rationale**: 001–005 全部配置均为 env 驱动的 frozen `Settings`（`config/__init__.py`），沿用零新概念；宪法 V（凭据类型化占位）要求凭据值不得进入配置结构。
- **Alternatives considered**: ① YAML/TOML 配置文件——新增解析层与惯例分裂，否决；② 经管理 API 运行时改配——运行时变更引入半生效状态与租约外写入，违反 frozen Settings 惯例，否决（Provider 切换=改配置重启实例，属部署操作）。

### 1.5 Remote Provider 协议与 local_gpu 语义

- **Decision**: remote Embedding 走 OpenAI-compatible `/embeddings`；remote Reranker 走 OpenAI-compatible `/rerank`（Jina/Cohere 兼容形态）；LLM 沿用既有 `/chat/completions`（`agents/llm_client.py`）。local_gpu = 同一本地模型按 GPU device 执行（`device` 参数化），无 GPU 硬件时启动校验显式失败（Assumptions：本机无 GPU 以校验语义验收，有硬件加执行冒烟）。失败契约沿用 005：任何 Provider 错误返回 None/降级，绝不阻塞状态机（SC-012）。
- **Rationale**: 蓝图 §17（OpenAI-compatible 仅作适配协议）、§18.1（三类 Provider）、§18.4（DeepSeek API 无原生 Embedding/Rerank 端点 → 不作默认依赖）。
- **Alternatives considered**: 绑定单一供应商 SDK——违反宪法 VII 与蓝图 §17 供应商中立，否决。

### 1.6 实例入口与角色绑定

- **Decision**: 管理进程（`server.py`）仅 writer 角色：启动时抢租约（失败即拒绝启动）、运行入库/TTL 清理/续约，`--mode reader` 启动即显式报错（reader 不运行管理面）。MCP 进程（`_run_mcp.py --mode writer|reader`，`INSTANCE_MODE` 等效）：writer MCP 与 reader MCP 均只读检索；writer 部署 = 管理进程 + writer MCP 进程，reader 部署 = 仅 reader MCP 进程。两进程各自注册实例与 worker_id（`instance_mode` 区分）。
- **Rationale**: 蓝图 §21.2 角色定义；`server.py --mode` 既有占位参数转正；`_run_mcp.py` 是 001 以来唯一 MCP 入口。
- **Alternatives considered**: 单进程同时服务管理与 MCP——端口/进程模型与 001–005 现状（8000 管理 + 8080 MCP）冲突，否决。

### 1.7 正文开关统一与运行记录扩展

- **Decision**: 单一 `TRACE_BODY_ENABLED`（默认 true）作用于全部检索模式；005 的 `AGENTIC_TRACE_BODY_ENABLED` 保留为兼容别名（005 测试不破坏）。`retrieval_runs.query_text` 改可空（关闭时 NULL）、新增 `error_summary` JSONB 与 `trace_body_recorded` 列（FR-020 回溯契约补齐错误字段）。TTL 默认 7 天改为 `RETRIEVAL_TTL_DAYS` 可配置。
- **Rationale**: 蓝图 §20（正文可关、保留 ID/状态/耗时/错误）；001 现状 query_text NOT NULL 恒存正文，是 006 要硬化的点。
- **Alternatives considered**: 关闭时存哨兵字符串（"<redacted>"）——与 NULL 语义混淆且破坏"不含正文"断言的直观性，否决。

### 1.8 reader schema 兼容校验

- **Decision**: reader MCP 启动时比对代码迁移头（alembic head）与共享库 `alembic_version`：不一致 → 显式失败并说明版本（FR-007）；迁移仅由 writer 管理进程执行。
- **Rationale**: 复用既有 alembic 基础设施，零新机制。
- **Alternatives considered**: 自建 schema 版本表——与 alembic 重复，否决。

### 1.9 超时档位

- **Decision**: `Settings.timeout_profiles`：`HOST_TIMEOUT_MS_DEEPSEEK_HARNESS`（默认 60000）、`HOST_TIMEOUT_MS_CLAUDE_CODE`（默认 60000）、`HOST_TIMEOUT_MS_CHATGPT_APP`（默认 120000），启动校验 `server total（默认 30000）< min(各 Host)`，反向配置显式拒绝（Edge Case）。初值保守设定，最终按蓝图 §19 由部署环境 P50/P95 评测调整（运行配置可覆盖，不改契约）。
- **Rationale**: FR-021；三 Host 的实际超时策略各不相同且可被宿主覆盖，保守初值保证 30s 服务端总超时严格小于全部 Host。
- **Alternatives considered**: 写死单一 Host 超时——违反 §19 每 Host 独立档位，否决。

### 1.10 非回归冒烟适配器

- **Decision**: `eval/instance_form_smoke.py`：将 001 基线 11 条评测集经 **MCP Streamable HTTP**（非进程内直调）分别对 writer MCP 与 reader MCP 端点执行，逐查询与 `baseline_report.json` 对照（1% 容差），产出含双形态结果与硬性指标的冒烟报告（JSON）。
- **Rationale**: FR-028 要求"两实例形态各重跑一遍"——经真实 MCP 传输才能覆盖 reader 部署形态；001 既有 `test_deepseek_harness_e2e.py` 已验证该路径可行。
- **Alternatives considered**: 进程内 eval runner 直调——不经过实例形态，无法验证 reader，否决。

### 1.11 维护日志（TTL 清理量审计）

- **Decision**: 新增 append-only `runtime_maintenance_log`（TTL 清理事件与清理行数），由 writer TTL 循环写入；指标端点聚合其计数满足 FR-016 的"TTL 清理量"。
- **Rationale**: 清理量需可对账（SC-006）；内存计数重启即失。
- **Alternatives considered**: 日志文件解析——不可查询、不可对账，否决。

## 2. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 002–005 既有测试依赖 query_text NOT NULL / 恒有正文 | 迁移默认 trace_body_recorded=true 且默认开关 true——旧行为不变；正文关闭仅在新开关下生效；005 兼容别名 |
| 雪花时钟回拨在多实例下放大 | 沿用 001 生成器语义（拒绝回拨生成）；心跳/租约时间用 DB 时钟仲裁，ID 时间戳仅作有序性 |
| reader 自动补位 worker_id 与写入并发竞争 | 唯一约束 + 事务内 SELECT 最低空闲（FOR UPDATE SKIP LOCKED），冲突重试一次后显式失败 |
| 指标聚合查询在运行表增长后变慢 | 聚合限定 TTL 窗口 + 复合索引（instance_mode/tool/created_at、status/created_at）；验收口径为秒级返回（SC-006） |
| remote Provider 校验拖慢启动 | 仅尽力而为探测（超时 2s、失败仅告警，除维度不匹配外不阻塞），运行期失败走既有降级 |
| 误配置 reader 运行管理面 | 管理进程启动即校验角色：`--mode reader` 显式报错退出 |

## 3. 复用与不改动清单（不重复 001–005）

- 检索路径全部复用：Dense/Sparse/RRF/DBSF/Rerank/图扩展/三 Agent 状态机（001/002/004/005）——不改默认值与上限（FR-029 沿用清单）。
- 对外 MCP 契约不变：`search_knowledge`/`get_evidence` 输出 Schema、`completion_status` 四态、来源定位格式（宪法 VII）。
- 管理面功能复用：项目/知识源/SSE/上传入库/版本发布（001/003）——006 仅按实例模式约束其可用性。
- `WriteCoordinator`/`SourceObjectStore` 首期实现分别为 PostgreSQL 租约与本地文件系统（包既有 `data_root` 访问）；S3/分布式协调器仅留抽象演进接口（§21.2 末段）。
- **ONNX/OpenVINO 量化（FR-014 决策闭合）**：不在 006 落地。006 只提供 Provider 配置能力（local_cpu/local_gpu/remote_api 与模型切换），量化推理作为本地 CPU 性能优化项延后至后续 Feature（蓝图 §18.5 允许但非必需；spec 范围外已排除）。

## 4. 延后项（tasks.md / 实现阶段决策）

- 租约/心跳的精确事务语句、迁移 DDL 细节、指标 SQL 与端点字段命名——tasks.md 落实。
- Provider 并发上限的信号量接入点（LLM 同步客户端的执行器封装）——实现阶段定。
- `.env.example` 补全部新变量与注释——tasks.md。
