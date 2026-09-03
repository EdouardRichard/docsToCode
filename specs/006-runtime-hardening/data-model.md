# Data Model: Runtime Hardening (006)

**Branch**: `006-runtime-hardening` | **Date**: 2026-09-03 | **Spec**: [spec.md](./spec.md)

> Phase 1 产出。数据模型扩展范围：**新增 3 张运行期表**（`instance_registry`、`writer_lease`、`runtime_maintenance_log`）与 **`retrieval_runs` 扩展列**；运行指标为查询时聚合的**派生口径**（无新表）。Qdrant 不变；知识模型（知识域/知识源/版本/Chunk/图）不变；005 agentic 表不变（仅 TTL 时长改为配置驱动）。运行期表不进入向量库、不写回知识库（蓝图 §20）。

## 1. 实体总览

| 实体 | 类型 | 职责 | 生命周期 |
|------|------|------|----------|
| instance_registry | 新表（运行期） | 实例注册、worker_id 分配与误配检测（澄清 Q6） | 进程启动注册 → 心跳续期 → 过期/释放 |
| writer_lease | 新表（运行期） | 单写租约：写入所有权仲裁（FR-002/FR-003） | 抢占 active → 续约 → released / expired |
| retrieval_runs（扩展） | 既有表加列 | 追踪记录全模式正文开关、错误、实例归属、Tool 归属、Provider 用量（FR-016/FR-018/FR-020） | 沿用 TTL（改为配置驱动） |
| runtime_maintenance_log | 新表（运行期） | TTL 清理量审计（FR-016） | append-only，自身按 TTL 清理 |
| 运行指标（Runtime Metrics） | 派生聚合 | 可查询运维指标（无表，查询时聚合） | 受运行记录 TTL 窗口约束 |
| Writer 租约 / 源对象存储 / Provider 配置 / 超时档位 | 抽象与配置 | 演进接口与运行配置（蓝图 §21.2/§17/§18/§19） | 非持久化实体，见 contracts |

**关系**：`writer_lease.holder_instance_id` → `instance_registry.instance_id`；`retrieval_runs.instance_id` → `instance_registry.instance_id`（历史行可 NULL，注册行过期清理后保留 `instance_mode` 冗余列）。

## 2. instance_registry（实例注册与 worker_id 分配）

### 2.1 字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| instance_id | UUID | PK | 进程启动时生成的 UUID（澄清 Q4），标识一次进程生命周期 |
| worker_id | SMALLINT | NOT NULL，0–1023 | 该进程雪花生成器的 worker_id（澄清 Q6） |
| instance_mode | VARCHAR(8) | NOT NULL，CHECK IN (writer, reader) | 实例模式（蓝图 §21.2） |
| process_role | VARCHAR(12) | NOT NULL，CHECK IN (management, mcp) | 进程角色（管理进程 / MCP 进程） |
| state | VARCHAR(16) | NOT NULL，CHECK IN (active, released, expired)，DEFAULT active | 注册活性（澄清 Q4：与 writer_lease 租约状态机同构的三态） |
| started_at | TIMESTAMPTZ | NOT NULL，DEFAULT NOW() | 进程启动时间 |
| last_heartbeat_at | TIMESTAMPTZ | NOT NULL | 最近心跳（与租约续约同周期，默认 30s） |
| expires_at | TIMESTAMPTZ | NOT NULL | 心跳过期时间（last_heartbeat_at + 过期窗口，默认 90s） |
| released_at | TIMESTAMPTZ | NULL | 正常退出时主动注销时间 |

### 2.2 索引与约束

- `PRIMARY KEY (instance_id)`。
- `UNIQUE INDEX idx_registry_worker_active ON (worker_id) WHERE state = 'active'` —— **相同 worker_id 误配置的检测点**：并发活跃实例 worker_id 必须互异（FR-030/Edge Case），冲突即启动显式拒绝；released/expired 行不占用 worker_id。
- `INDEX idx_registry_expires ON (expires_at) WHERE state = 'active'` —— 过期清理扫描。

### 2.3 状态机

```
registered(active) --心跳--> active（滚动更新 last_heartbeat_at / expires_at）
active --正常退出--> released（state=released 且 released_at 置位，worker_id 立即可复用）
active --超过 expires_at 未心跳--> expired（writer 维护循环标记/清理，worker_id 可复用）
released/expired --同进程重启或新进程--> 新注册行（新 instance_id）
```

### 2.4 分配与心跳语义（FR-030）

- **显式优先**：配置 `WORKER_ID` 时直接注册该值，唯一约束冲突 → 显式拒绝（错误信息含冲突实例标识）。
- **自动补位**：未配置时事务内选取未被活跃注册占用的最低 worker_id（`FOR UPDATE SKIP LOCKED` 最低空闲扫描），冲突重试一次后显式失败。
- **单实例兼容**：仅一个进程且未配置时自动获得 worker_id=0，与 001 既有实现（固定 worker_id=0）行为一致。
- **心跳**：所有实例进程周期心跳（默认 30s）；过期注册由 writer 管理进程清理（维护写路径，FR-004）。

## 3. writer_lease（单写租约）

### 3.1 字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| lease_id | BIGINT | PK，雪花 ID | 租约稳定标识（澄清 Q4） |
| holder_instance_id | UUID | NOT NULL，FK→instance_registry | 持有者（writer 管理进程） |
| state | VARCHAR(16) | NOT NULL，CHECK IN (active, released, expired)，DEFAULT active | 租约状态 |
| acquired_at | TIMESTAMPTZ | NOT NULL | 获取时间 |
| renewed_at | TIMESTAMPTZ | NOT NULL | 最近续约时间 |
| expires_at | TIMESTAMPTZ | NOT NULL | 过期时间（renewed_at + 过期窗口，默认 90s） |
| released_at | TIMESTAMPTZ | NULL | 正常释放时间 |

### 3.2 约束

- `UNIQUE INDEX idx_lease_single_active ON (state) WHERE state = 'active'` —— **任意时刻至多一个 active 租约**（FR-002 双写为零的数据库级保证）。
- `INDEX idx_lease_expires ON (expires_at) WHERE state = 'active'`。

### 3.3 状态机与语义（FR-002/FR-003，澄清 Q2）

```
(空) --抢占 INSERT--> active --续约(默认 30s 周期)--> active
active --正常退出--> released（明确释放，立即可再抢占）
active --超过 expires_at 未续约--> expired（可被新 writer 回收）
expired/released --新 writer 抢占--> 新 active 行
```

- **抢占**：新 writer 启动时（a）先将 `expires_at < NOW() AND state='active'` 的行标记 expired（过期回收），（b）INSERT active 行；步骤 (b) 撞唯一索引且该行仍有效 → **拒绝进入写模式**，错误信息含持有者 instance_id 与到期时间（不静默降级为 reader）。
- **续约**：writer 管理进程周期（默认 30s）UPDATE 自身行的 renewed_at/expires_at；续约失败（网络抖动）容忍至过期窗口（90s）。
- **读路径不依赖租约**：reader MCP 与 writer MCP 的检索/证据展开不读取租约（SC-003）。

## 4. retrieval_runs 扩展

### 4.1 新增列（ALTER，全部向后兼容）

| 列 | 类型 | 默认 | 说明 |
|----|------|------|------|
| tool | VARCHAR(16) | 'search_knowledge'，CHECK IN (search_knowledge, get_evidence) | Tool 归属；`get_evidence` 调用纳入运行记录（FR-016 按 Tool 聚合） |
| instance_id | UUID | NULL | 服务该请求的实例进程（006 起新请求必填；历史行为 NULL） |
| instance_mode | VARCHAR(8) | NULL，CHECK IN (writer, reader) | 实例模式冗余列（注册行过期清理后聚合仍可分组） |
| query_text | Text | 原 NOT NULL → **NULLABLE** | 正文开关关闭时为 NULL（FR-018；001 现状为恒存正文） |
| error_summary | JSONB | NULL | `{code, message, failed_paths[]}`（FR-020 回溯契约补齐错误字段） |
| trace_body_recorded | BOOLEAN | TRUE | 该行写入时正文开关状态（审计可分辨） |
| provider_usage | JSONB | NULL | `{embedding_calls, rerank_calls, llm_calls, llm_prompt_chars, llm_completion_chars}`（请求完成时内存累计、随行写入；SC-006 对账口径） |

### 4.2 兼容与回填

- 历史行：`tool='search_knowledge'`、`instance_id/instance_mode=NULL`、`trace_body_recorded=TRUE`、`query_text` 保持原值——聚合按"legacy"分组或并入 writer 语义（指标契约以 NULL 分组标注）。
- 002/004/005 对 query_text 的既有断言不受影响：默认开关 true 时行为不变；005 的 `AGENTIC_TRACE_BODY_ENABLED` 保留为兼容别名，`TRACE_BODY_ENABLED` 为统一开关（research §1.7）。

### 4.3 TTL 语义（FR-019）

- `expires_at = 写入时间 + RETRIEVAL_TTL_DAYS`（默认 7 天，配置驱动；替换原 server_default 常量为应用侧计算值，迁移保留 server_default 兜底）。
- 清理由 writer 管理进程 TTL 循环执行（`RETRIEVAL_TTL_CLEANUP_INTERVAL_S` 沿用）；清理行数写入 `runtime_maintenance_log`。

## 5. runtime_maintenance_log（TTL 清理量审计）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| log_id | BIGINT | PK，雪花 ID | 事件标识 |
| event_type | VARCHAR(32) | NOT NULL，CHECK IN (ttl_purge) | 维护事件类型 |
| purged_retrieval_runs | INTEGER | NOT NULL，DEFAULT 0 | 本次清理的 retrieval_runs 行数 |
| purged_agentic_runs | INTEGER | NOT NULL，DEFAULT 0 | 本次清理的 005 agentic 运行期表行数 |
| purged_maintenance_logs | INTEGER | NOT NULL，DEFAULT 0 | 自身清理行数 |
| created_at | TIMESTAMPTZ | NOT NULL，DEFAULT NOW() | 事件时间 |

- append-only（只 INSERT）；自身行按同一 TTL 清理（自清理计数入 purge 事件）。
- 指标端点聚合本表满足 FR-016"TTL 清理量"与 SC-006 对账。
- 无独立契约 schema：本表经 [runtime-metrics.schema.json](./contracts/runtime-metrics.schema.json) 的 `ttl_purge` 字段对外暴露（与 instance_registry/writer_lease/provider-config/metrics 不同，无需独立 schema）。

## 6. 运行指标聚合（派生口径，无新表）

聚合源：`retrieval_runs`（006 起新列）+ 005 agentic 表（Provider/LLM 用量）+ `runtime_maintenance_log`。窗口 = TTL 窗口（受运行记录 TTL 约束，声明为可接受语义）。全部为聚合数值与标识，**不含查询/证据正文**（FR-017）。

| 指标 | 聚合口径 |
|------|----------|
| 请求量 | `COUNT(*) GROUP BY instance_mode, tool`（NULL instance_mode = legacy/pre-006 分组） |
| completion_status 分布 | `COUNT(*) GROUP BY completion_status, instance_mode` |
| P50/P95 延迟 | `percentile_cont(0.5/0.95) WITHIN GROUP (ORDER BY duration_ms)`（按 tool 分组） |
| 子路径耗时 | `subpath_timings` JSONB 键的 P50（dense/sparse/fusion/rerank/graph/total） |
| Provider 用量 | `SUM(provider_usage.*)` + agentic 表 LLM 用量（沿用 005 真实调用口径，缓存命中不计） |
| TTL 清理量 | `SUM(runtime_maintenance_log.purged_*)` |

索引支撑：`idx_rr_mode_created`（既有）+ 新增 `(instance_mode, tool, created_at)`、`(completion_status, created_at)` 复合索引；聚合限定 TTL 窗口；验收口径为秒级返回（SC-006）。

## 7. 标识与枚举契约

| 标识/枚举 | 契约 |
|-----------|------|
| instance_id | UUID v4，进程启动生成，一次进程生命周期稳定（澄清 Q4） |
| worker_id | SMALLINT 0–1023，并发活跃实例互异（唯一约束保证；澄清 Q6） |
| lease_id | 雪花 ID（BIGINT，`^[0-9]+$`，沿用仓库惯例） |
| InstanceMode | {writer, reader} |
| ProcessRole | {management, mcp} |
| LeaseState / InstanceState | {active, released, expired} |
| Tool | {search_knowledge, get_evidence} |
| ProviderCapability | {embedding, reranker, llm} |
| ProviderType | {local_cpu, local_gpu, remote_api} |
| 指标键 | 稳定字符串：`能力 × provider_type × 实例模式` 聚合维度（澄清 Q4） |

## 8. 迁移（Alembic，仅 writer 管理进程执行）

- `0060_create_runtime_tables`：`instance_registry`、`writer_lease`、`runtime_maintenance_log` 三表 + §2.2/§3.2 索引约束。
- `0061_extend_retrieval_runs`：§4.1 列扩展 + `query_text` 可空化 + 聚合索引。
- 迁移幂等可重放；reader 启动校验 alembic head 一致（FR-007，research §1.8）。

## 9. 校验规则（验收断言口径）

- 双写为零：任一时刻 active 租约数 ≤ 1（唯一索引保证）；验收期间第二个 writer 启动尝试 100% 被拒（SC-002）。
- 跨实例 ID 唯一：多实例并发验收批次期间 `instance_registry` 活跃 worker_id 互异、运行记录主键零冲突（SC-013/FR-030）。
- reader 独立性：writer 停止后 reader 检索/证据展开成功率 100%，无任何对 writer 本地文件路径的访问（SC-003/FR-005）。
- 正文开关：TRACE_BODY_ENABLED=false 时四种检索模式新增运行记录 `query_text IS NULL AND trace_body_recorded = FALSE`，ID/状态/耗时/错误保留完整率 100%（SC-007）。
- 指标无正文：指标端点响应经 [runtime-metrics.schema.json](./contracts/runtime-metrics.schema.json) 校验且全文不含任何 query_text/evidence 正文（SC-006）。
- 租约语义：续约 30s/过期 90s（澄清 Q2 固化默认，可运行配置覆盖）；过期回收前后均无第二个 writer 进入写模式（FR-003）。
