# 数据模型：001 Minimum RAG MCP Loop

**Feature**: `001-minimum-rag-mcp-loop`  
**状态**: Draft  
**日期**: 2026-08-27  
**依据**: 系统设计蓝图 §4–§8、§12–§13；Feature Spec FR-001 ~ FR-027；Constitution I–V  

---

## 1. 概述

本文档定义 001 Feature 所需的全部持久化实体，涵盖 PostgreSQL 关系表与 Qdrant 向量集合。文档包含：

- 实体字段、类型、约束与验证规则
- 实体关系图（文本）
- KnowledgeSource 与 KnowledgeVersion 状态转换图
- Qdrant 集合索引策略
- PostgreSQL 表约束与索引
- 跨存储一致性规则

001 Feature 仅建立 Dense 检索基线，不包含 BM25/Sparse、Rerank、Graph RAG 或三 Agent 编排相关实体。这些能力在后续 Feature 中扩展。

---

## 2. 实体关系图

```
┌─────────────────┐       ┌──────────────────┐
│     Project      │ 1   1 │  KnowledgeScope   │
│                  │◄─────►│                   │
│ project_id (PK)  │       │ scope_id (PK)     │
│ name             │       │ scope_type        │
│ alias            │       │ name              │
│ repo_path        │       │ status            │
│ knowledge_scope_id│      │ created_at        │
│ created_at       │       │ updated_at        │
│ updated_at       │       └────────┬──────────┘
└─────────────────┘                │ 1
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
                    ▼ *            ▼ *            ▼ *
          ┌────────────────┐ ┌──────────────┐ ┌────────────────┐
          │KnowledgeSource │ │KnowledgeVer. │ │ RetrievalRun   │
          │                │ │              │ │                │
          │ source_id (PK) │ │ version_id   │ │ run_id (PK)    │
          │knowledge_scope_│ │ (PK)         │ │ query_text     │
          │   id (FK)      │ │knowledge_scop│ │ project_scopes │
          │ filename       │ │ e_id (FK)    │ │ completion_st. │
          │ content_hash   │ │ version_num  │ │ evidence_count │
          │ format         │ │ capabilities │ │ duration_ms    │
          │ size_bytes     │ │ status       │ │ created_at     │
          │ status         │ │ published_at │ └────────────────┘
          │processing_err. │ │ created_at   │
          │ created_at     │ └──────┬───────┘
          │ updated_at     │        │ 1
          └───────┬────────┘        │
                  │ 1               │
           ┌──────┴──────┐          │
           │             │          │
           ▼ *           ▼ *        │
   ┌──────────────┐ ┌──────────┐   │
   │ProcessingRun │ │  Chunk   │◄──┘
   │              │ │          │
   │ run_id (PK)  │ │chunk_id  │
   │ source_id(FK)│ │ (PK)     │
   │ run_type     │ │source_id │
   │ status       │ │ (FK)     │
   │ started_at   │ │version_id│
   │ completed_at │ │ (FK)     │
   │ error_message│ │knowledge_│
   │ stages (JSON)│ │scope_id  │
   └──────────────┘ │ (FK)     │
                    │parent_   │
                    │chunk_id  │
                    │ (FK null)│
                    │content_  │
                    │ text     │
                    │position_ │
                    │ path     │
                    │chunk_type│
                    │start_line│
                    │end_line  │
                    │token_cnt │
                    │embedding_│
                    │ model    │
                    │index_ver │
                    └──────────┘
```

### 关系说明

| 关系 | 基数 | 说明 |
|------|------|------|
| Project ↔ KnowledgeScope | 1:1 | 每个项目恰好关联一个项目知识域；公共知识域不关联 Project |
| KnowledgeScope → KnowledgeSource | 1:* | 一个知识域包含多个知识源 |
| KnowledgeScope → KnowledgeVersion | 1:* | 一个知识域拥有多个版本（单调递增） |
| KnowledgeSource → ProcessingRun | 1:* | 一个知识源可有多次处理运行（初始 + 重试） |
| KnowledgeSource → Chunk | 1:* | 一个知识源产生多个 Chunk |
| KnowledgeVersion → Chunk | 1:* | 一个版本包含多个 Chunk |
| Chunk → Chunk (自引用) | *:1 | 子 Chunk 引用父 Chunk；父 Chunk 的 parent_chunk_id 为 NULL |
| KnowledgeScope → RetrievalRun | 1:* | 检索运行通过 project_scopes JSON 关联一个或多个知识域 |

---

## 3. 实体定义

### 3.1 KnowledgeScope（知识域）

知识域是项目域和公共域的统一抽象。所有知识源、Chunk、向量和图关系强制携带 `knowledge_scope_id`。

| 字段 | 类型 | 约束 | 描述 |
|------|------|------|------|
| `scope_id` | BIGINT | PK, NOT NULL | Snowflake ID，全局唯一稳定标识 |
| `scope_type` | VARCHAR(16) | NOT NULL, CHECK IN ('project', 'public') | 知识域类型 |
| `name` | VARCHAR(255) | NOT NULL | 人类可读名称 |
| `status` | VARCHAR(16) | NOT NULL, DEFAULT 'active', CHECK IN ('active', 'archived', 'deleting') | 知识域生命周期状态 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 创建时间 |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 最后更新时间 |

**验证规则**：
- `scope_type = 'project'` 时，必须存在且仅存在一条对应的 Project 记录（由 Project 表的 UNIQUE FK 保证）。
- `scope_type = 'public'` 时，不得关联任何 Project 记录。
- `status = 'deleting'` 时，该知识域不参与新检索，但已有证据展开请求仍可完成（直到派生数据删除完毕）。
- `status = 'archived'` 时，该知识域不参与检索，但保留历史数据供审计。

**宪法合规**：满足 Constitution I（显式知识域），所有下游实体均通过 FK 强制携带此 ID。

---

### 3.2 Project（项目）

项目是用户工作的上下文身份，与项目知识域一对一绑定。

| 字段 | 类型 | 约束 | 描述 |
|------|------|------|------|
| `project_id` | BIGINT | PK, NOT NULL | Snowflake ID |
| `name` | VARCHAR(255) | NOT NULL | 项目名称 |
| `alias` | VARCHAR(128) | UNIQUE, NOT NULL | 项目别名，用于 MCP 请求中的项目引用解析 |
| `repo_path` | VARCHAR(1024) | UNIQUE, NULLABLE | 仓库路径或工作目录，可选的项目引用方式 |
| `knowledge_scope_id` | BIGINT | FK → knowledge_scopes.scope_id, UNIQUE, NOT NULL | 关联的项目知识域（1:1） |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 创建时间 |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 最后更新时间 |

**验证规则**：
- `knowledge_scope_id` 上的 UNIQUE 约束确保一个知识域只能被一个项目关联。
- `alias` 全局唯一，用于 MCP 请求中按别名解析项目作用域。
- `repo_path` 全局唯一（当非 NULL 时），用于按路径解析项目作用域。
- 删除项目时必须级联将关联知识域标记为 `archived` 或 `deleting`。

**设计决策**：Clarification 确认 Project ↔ KnowledgeScope 为 1:1 关系。多 Agent 并发隔离由请求级状态隔离保障，与知识域数量无关。

---

### 3.3 KnowledgeSource（知识源）

用户上传的原始材料及其生命周期状态。

| 字段 | 类型 | 约束 | 描述 |
|------|------|------|------|
| `source_id` | BIGINT | PK, NOT NULL | Snowflake ID |
| `knowledge_scope_id` | BIGINT | FK → knowledge_scopes.scope_id, NOT NULL | 所属知识域 |
| `filename` | VARCHAR(1024) | NOT NULL | 原始文件名 |
| `content_hash` | CHAR(64) | NOT NULL | SHA-256 内容哈希 |
| `format` | VARCHAR(16) | NOT NULL, CHECK IN ('markdown', 'java') | 文件格式（001 仅支持两种） |
| `size_bytes` | BIGINT | NOT NULL, CHECK >= 0 | 文件大小（字节） |
| `status` | VARCHAR(16) | NOT NULL, DEFAULT 'uploaded', CHECK IN ('uploaded', 'processing', 'published', 'failed', 'deleted') | 知识源生命周期状态 |
| `processing_error` | TEXT | NULLABLE | 最近一次处理失败的错误信息 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 上传/创建时间 |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 最后状态变更时间 |

**验证规则**：
- `content_hash` 在同一 `knowledge_scope_id` 内应检查重复：相同哈希的材料不应创建不可区分的重复有效版本（FR-005 + Edge Case）。
- `format` 在 001 Feature 中仅限 `markdown` 和 `java`；上传其他格式时拒绝并返回原因。
- `size_bytes = 0` 时拒绝处理（Edge Case：空文件）。
- `status = 'deleted'` 后该知识源的 Chunk 不再参与新检索，但保留元数据直到派生数据清理完成。
- `processing_error` 仅在 `status = 'failed'` 时有值；状态转为 `processing` 或 `published` 时应清空。

**凭据值规范化**：在切片前对内容执行凭据值替换（FR-006），生成检索安全副本。原始文件由 Source Object Store 管理，检索索引和 MCP 响应不包含原始凭据值。

---

### 3.4 KnowledgeVersion（知识版本）

一个可发布或已发布的知识快照。版本号在同一知识域内单调递增。

| 字段 | 类型 | 约束 | 描述 |
|------|------|------|------|
| `version_id` | BIGINT | PK, NOT NULL | Snowflake ID |
| `knowledge_scope_id` | BIGINT | FK → knowledge_scopes.scope_id, NOT NULL | 所属知识域 |
| `version_number` | INTEGER | NOT NULL, CHECK > 0 | 单调递增版本号（同一知识域内唯一） |
| `capabilities` | JSONB | NOT NULL, DEFAULT '{}' | 索引能力清单，如 `{"dense_ready": true}` |
| `status` | VARCHAR(16) | NOT NULL, DEFAULT 'draft', CHECK IN ('draft', 'published', 'superseded') | 版本生命周期状态 |
| `published_at` | TIMESTAMPTZ | NULLABLE | 发布时间；仅 `status = 'published'` 时有值 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 版本创建时间 |

**验证规则**：
- `(knowledge_scope_id, version_number)` 联合唯一，确保版本号在同一知识域内不重复。
- `status = 'draft'` 的版本不参与检索（FR-008）。
- `status = 'published'` 表示该版本的所有声明能力均已就绪，可参与检索。
- `status = 'superseded'` 表示已有更新的已发布版本；旧版本仍可通过证据 ID 展开（FR-020），但不作为新检索的首选版本。
- 新版本发布时，前一 `published` 版本自动转为 `superseded`。
- 发布过程中部分派生数据写入失败时，版本保持 `draft`，不得暴露半成品（Edge Case + 蓝图 §8.4）。
- 001 Feature 的 capabilities 仅包含 `dense_ready`；后续 Feature 扩展 `lexical_ready`、`graph_ready` 等。

**宪法合规**：满足 Constitution V（版本化与可重建知识）。

---

### 3.5 Chunk（检索单元）

从知识源产生的结构化检索单元，是检索的最小粒度。

| 字段 | 类型 | 约束 | 描述 |
|------|------|------|------|
| `chunk_id` | BIGINT | PK, NOT NULL | Snowflake ID |
| `source_id` | BIGINT | FK → knowledge_sources.source_id, NOT NULL | 来源知识源 |
| `version_id` | BIGINT | FK → knowledge_versions.version_id, NOT NULL | 所属知识版本 |
| `knowledge_scope_id` | BIGINT | FK → knowledge_scopes.scope_id, NOT NULL | 所属知识域（冗余，加速过滤） |
| `parent_chunk_id` | BIGINT | FK → chunks.chunk_id, NULLABLE | 父 Chunk ID；顶层 Chunk 为 NULL |
| `content_text` | TEXT | NOT NULL | Chunk 正文（凭据值已替换） |
| `position_path` | VARCHAR(1024) | NOT NULL | 来源定位路径 |
| `chunk_type` | VARCHAR(16) | NOT NULL, CHECK IN ('section', 'symbol') | Chunk 类型 |
| `start_line` | INTEGER | NOT NULL, CHECK > 0 | 起始行号（1-based） |
| `end_line` | INTEGER | NOT NULL, CHECK >= start_line | 结束行号（1-based，含） |
| `token_count` | INTEGER | NOT NULL, CHECK > 0 | Token 数量估算 |
| `embedding_model` | VARCHAR(128) | NOT NULL | 生成 Embedding 的模型标识 |
| `index_version` | VARCHAR(64) | NOT NULL | 索引版本标识（Embedding 模型 + 切片策略的组合标识） |

**验证规则**：
- `knowledge_scope_id` 必须与 `source_id` 所属知识域一致（应用层校验 + 触发器可选）。
- `parent_chunk_id` 若非 NULL，必须指向同一 `source_id` 和 `version_id` 下的 Chunk。
- `position_path` 格式取决于 `chunk_type`：
  - `section`（Markdown）：章节路径，如 `## 安装 > ### 配置`
  - `symbol`（Java）：全限定符号路径，如 `com.example.Service#methodName`
- `content_text` 不得包含原始凭据值（FR-006）。
- `embedding_model` 和 `index_version` 共同标识向量兼容性；不兼容的 Embedding 不得共享同一 `index_version`（Constitution V）。
- `token_count` 目标范围 512–1024 Token（蓝图 §7），但不作为硬约束。

**Qdrant 同步**：每个 Chunk 在 Qdrant 中对应一个 Point，`chunk_id` 作为 Point ID，Payload 包含 `knowledge_scope_id`、`source_id`、`version_id`、`index_version`、`chunk_type`、`position_path`、`start_line`、`end_line`。

---

### 3.6 ProcessingRun（处理运行）

一次知识源的处理执行记录，包括初始处理和重试。

| 字段 | 类型 | 约束 | 描述 |
|------|------|------|------|
| `run_id` | BIGINT | PK, NOT NULL | Snowflake ID |
| `source_id` | BIGINT | FK → knowledge_sources.source_id, NOT NULL | 关联的知识源 |
| `run_type` | VARCHAR(16) | NOT NULL, CHECK IN ('initial', 'retry') | 运行类型 |
| `status` | VARCHAR(16) | NOT NULL, DEFAULT 'pending', CHECK IN ('pending', 'running', 'completed', 'failed') | 运行状态 |
| `started_at` | TIMESTAMPTZ | NULLABLE | 实际开始时间 |
| `completed_at` | TIMESTAMPTZ | NULLABLE | 完成或失败时间 |
| `error_message` | TEXT | NULLABLE | 失败时的错误详情 |
| `stages` | JSONB | NOT NULL, DEFAULT '[]' | 处理阶段记录 |

**stages JSON 结构**：

```json
[
  {
    "stage": "credential_scan",
    "status": "completed",
    "started_at": "2026-08-27T10:00:00Z",
    "completed_at": "2026-08-27T10:00:01Z",
    "details": {}
  },
  {
    "stage": "parsing",
    "status": "completed",
    "started_at": "2026-08-27T10:00:01Z",
    "completed_at": "2026-08-27T10:00:03Z",
    "details": {"ast_nodes": 42}
  },
  {
    "stage": "chunking",
    "status": "completed",
    "started_at": "2026-08-27T10:00:03Z",
    "completed_at": "2026-08-27T10:00:04Z",
    "details": {"chunks_created": 15}
  },
  {
    "stage": "embedding",
    "status": "completed",
    "started_at": "2026-08-27T10:00:04Z",
    "completed_at": "2026-08-27T10:00:12Z",
    "details": {"model": "BAAI/bge-m3", "vectors": 15}
  }
]
```

**验证规则**：
- `status = 'pending'` 时 `started_at` 为 NULL。
- `status = 'running'` 时 `started_at` 非 NULL，`completed_at` 为 NULL。
- `status = 'completed'` 或 `'failed'` 时 `completed_at` 非 NULL。
- `error_message` 仅在 `status = 'failed'` 时有值。
- 重试运行时 `run_type = 'retry'`，且同一 `source_id` 的前一次运行必须为终态（`completed` 或 `failed`）。
- 001 Feature 的处理阶段固定为：`credential_scan` → `parsing` → `chunking` → `embedding`。

---

### 3.7 RetrievalRun（检索运行）

一次独立检索调用的记录，用于问题回溯和评测（FR-025）。

| 字段 | 类型 | 约束 | 描述 |
|------|------|------|------|
| `run_id` | BIGINT | PK, NOT NULL | Snowflake ID |
| `query_text` | TEXT | NOT NULL | 查询原文 |
| `project_scopes` | JSONB | NOT NULL | 请求的项目作用域列表 |
| `completion_status` | VARCHAR(16) | NOT NULL, CHECK IN ('complete', 'partial', 'no_evidence', 'failed') | 检索完成状态（蓝图 §14 四态） |
| `evidence_count` | INTEGER | NOT NULL, CHECK >= 0 | 返回的证据数量 |
| `duration_ms` | INTEGER | NOT NULL, CHECK >= 0 | 检索耗时（毫秒） |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 检索发起时间 |
| `expires_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() + INTERVAL '7 days' | TTL过期时间（蓝图 §20），到期后由清理任务删除 |

**project_scopes JSON 结构**：

```json
[
  {
    "scope_id": "1234567890123456789",
    "scope_type": "project",
    "resolved_from": "alias",
    "resolved_value": "my-project"
  }
]
```

**验证规则**：
- `project_scopes` 至少包含一个元素（FR-014：缺少项目作用域时拒绝检索，此时不创建 RetrievalRun 或在创建时标记为 `failed`）。
- `completion_status` 语义（蓝图 §14）：
  - `complete`：检索目标得到充分覆盖。
  - `partial`：已有可靠证据，但存在明确缺口或部分路径失败。
  - `no_evidence`：系统正常执行，但没有找到可靠证据。
  - `failed`：系统异常导致无法形成有效响应。
- `query_text` 可根据追踪配置脱敏或截断（蓝图 §20）。
- `expires_at` 默认 7 天 TTL，到期后由定期清理任务删除过期记录（蓝图 §20）。
- 本表为追加式记录，不支持更新或删除（仅清理任务可删除过期记录）。

---

## 4. 状态转换图

### 4.1 KnowledgeSource 状态转换

```
                    ┌──────────────────────────────────────┐
                    │                                      │
                    ▼                                      │
              ┌──────────┐                                 │
   上传成功   │          │  开始处理                        │
  ──────────►│ uploaded │──────────────┐                   │
              │          │              │                   │
              └──────────┘              ▼                   │
                                  ┌────────────┐            │
                                  │            │ 处理成功   │
                                  │ processing │────────────┤
                                  │            │            │
                                  └────────────┘            │
                                    │         ▲             │
                           处理失败 │         │ 重试        │
                                    ▼         │             │
                               ┌──────────┐   │             │
                               │          │───┘             │
                               │  failed  │                 │
                               │          │                 │
                               └──────────┘                 │
                                    │                       │
                          用户删除  │                       │
                                    ▼                       │
                               ┌──────────┐                 │
                               │          │  清理完成       │
                               │ deleted  │─────────────────┘
                               │          │  (归档元数据)
                               └──────────┘
                                    
              ┌──────────┐
              │          │ ← 处理成功后自动转入
              │published │
              │          │
              └──────────┘
                   │
         用户删除  │
                   ▼
              ┌──────────┐
              │          │
              │ deleted  │
              │          │
              └──────────┘
```

**状态转换矩阵**：

| 当前状态 | 允许的目标状态 | 触发条件 |
|----------|---------------|----------|
| `uploaded` | `processing` | 系统开始处理该知识源 |
| `processing` | `published` | 所有处理阶段成功完成，Chunk 和向量写入成功 |
| `processing` | `failed` | 任一处理阶段失败 |
| `failed` | `processing` | 用户发起重试，创建新的 ProcessingRun |
| `failed` | `deleted` | 用户删除该知识源 |
| `published` | `deleted` | 用户删除该知识源 |
| `deleted` | （终态） | 派生数据清理完成后保留元数据 |

**关键约束**：
- `published` 状态的 KnowledgeSource 其关联的最新 KnowledgeVersion 必须为 `published`。
- 删除操作先将知识源标记为 `deleted`（立即停止参与新检索），再异步清理派生数据（FR-012）。
- 重试时不清除 `processing_error`，直到新的 ProcessingRun 成功完成。

---

### 4.2 KnowledgeVersion 状态转换

```
              ┌──────────┐
   创建版本   │          │  所有声明能力就绪
  ──────────►│  draft   │──────────────┐
              │          │              │
              └──────────┘              ▼
                   ▲              ┌─────────────┐
                   │              │             │
                   │  发布失败    │  published  │
                   │  (保持draft) │             │
                   │              └─────────────┘
                   │                     │
                   │          新版本发布成功
                   │                     ▼
                   │              ┌──────────────┐
                   │              │              │
                   │              │ superseded   │
                   │              │              │
                   │              └──────────────┘
                   │
                   │  知识域清空
                   ▼
              (版本随知识域一起归档/删除)
```

**状态转换矩阵**：

| 当前状态 | 允许的目标状态 | 触发条件 |
|----------|---------------|----------|
| `draft` | `published` | 该版本声明的所有能力（001 仅 `dense_ready`）均已就绪 |
| `draft` | `draft`（不变） | 发布过程中部分派生数据写入失败；版本保持 draft |
| `published` | `superseded` | 同一知识域内有更新的版本成功发布 |
| `superseded` | （终态） | 历史版本，可通过证据 ID 展开但不作为新检索首选 |

**关键约束**：
- 发布是原子操作：只有当 Dense 向量全部写入 Qdrant 且 Chunk 元数据全部写入 PostgreSQL 后，版本才转为 `published`。
- 同一知识域同时最多只有一个 `published` 版本。
- `superseded` 版本的 Chunk 和向量保留在存储中，支持证据展开（FR-020）。
- 知识域清空时，所有版本随知识域一起进入归档/删除流程。

---

## 5. Qdrant 集合索引策略

### 5.1 集合命名

001 Feature 使用单一 Dense 向量集合：

```
chunks_dense_{index_version}
```

- `index_version` 由 `embedding_model` + 切片策略版本组合生成，例如 `bge-m3_v1`。
- 切换 Embedding 模型或切片策略时创建新集合，旧集合保留直到手动清理。

### 5.2 Point 结构

```json
{
  "id": "<chunk_id as u64>",
  "vector": [0.012, -0.034, ...],
  "payload": {
    "knowledge_scope_id": "<snowflake_id>",
    "source_id": "<snowflake_id>",
    "version_id": "<snowflake_id>",
    "chunk_id": "<snowflake_id>",
    "chunk_type": "section | symbol",
    "position_path": "## 安装 > ### 配置",
    "start_line": 42,
    "end_line": 87,
    "index_version": "bge-m3_v1",
    "embedding_model": "BAAI/bge-m3"
  }
}
```

### 5.3 Payload 索引

| Payload 字段 | 索引类型 | 用途 |
|-------------|---------|------|
| `knowledge_scope_id` | Keyword Index | 知识域过滤（每次检索必用） |
| `version_id` | Keyword Index | 版本过滤（仅检索已发布版本） |
| `chunk_type` | Keyword Index | 按类型过滤 |
| `source_id` | Keyword Index | 按知识源过滤 |
| `index_version` | Keyword Index | 索引版本隔离 |

### 5.4 向量配置

| 参数 | 值 | 说明 |
|------|-----|------|
| `size` | 1024 | BGE-M3 Dense 输出维度 |
| `distance` | Cosine | 语义相似度度量 |
| `on_disk` | false | 001 默认内存驻留；后续可按规模调整 |
| `hnsw_config.m` | 16 | 默认 HNSW 参数 |
| `hnsw_config.ef_construct` | 200 | 默认 HNSW 构建参数 |

### 5.5 检索过滤

每次 `search_knowledge` 调用必须携带 `knowledge_scope_id` 过滤条件：

```json
{
  "filter": {
    "must": [
      {
        "key": "knowledge_scope_id",
        "match": { "value": "<requested_scope_id>" }
      },
      {
        "key": "version_id",
        "match": { "value": "<current_published_version_id>" }
      }
    ]
  }
}
```

跨项目检索时对每个项目作用域分别执行过滤查询，结果合并时保留知识域身份。

---

## 6. PostgreSQL 表约束与索引

### 6.1 DDL 概要

```sql
-- 知识域
CREATE TABLE knowledge_scopes (
    scope_id        BIGINT PRIMARY KEY,
    scope_type      VARCHAR(16) NOT NULL CHECK (scope_type IN ('project', 'public')),
    name            VARCHAR(255) NOT NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived', 'deleting')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 项目
CREATE TABLE projects (
    project_id          BIGINT PRIMARY KEY,
    name                VARCHAR(255) NOT NULL,
    alias               VARCHAR(128) NOT NULL UNIQUE,
    repo_path           VARCHAR(1024) UNIQUE,
    knowledge_scope_id  BIGINT NOT NULL UNIQUE REFERENCES knowledge_scopes(scope_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 知识源
CREATE TABLE knowledge_sources (
    source_id           BIGINT PRIMARY KEY,
    knowledge_scope_id  BIGINT NOT NULL REFERENCES knowledge_scopes(scope_id),
    filename            VARCHAR(1024) NOT NULL,
    content_hash        CHAR(64) NOT NULL,
    format              VARCHAR(16) NOT NULL CHECK (format IN ('markdown', 'java')),
    size_bytes          BIGINT NOT NULL CHECK (size_bytes >= 0),
    status              VARCHAR(16) NOT NULL DEFAULT 'uploaded' CHECK (status IN ('uploaded', 'processing', 'published', 'failed', 'deleted')),
    processing_error    TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 知识版本
CREATE TABLE knowledge_versions (
    version_id          BIGINT PRIMARY KEY,
    knowledge_scope_id  BIGINT NOT NULL REFERENCES knowledge_scopes(scope_id),
    version_number      INTEGER NOT NULL CHECK (version_number > 0),
    capabilities        JSONB NOT NULL DEFAULT '{}',
    status              VARCHAR(16) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'superseded')),
    published_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (knowledge_scope_id, version_number)
);

-- Chunk
CREATE TABLE chunks (
    chunk_id            BIGINT PRIMARY KEY,
    source_id           BIGINT NOT NULL REFERENCES knowledge_sources(source_id),
    version_id          BIGINT NOT NULL REFERENCES knowledge_versions(version_id),
    knowledge_scope_id  BIGINT NOT NULL REFERENCES knowledge_scopes(scope_id),
    parent_chunk_id     BIGINT REFERENCES chunks(chunk_id),
    content_text        TEXT NOT NULL,
    position_path       VARCHAR(1024) NOT NULL,
    chunk_type          VARCHAR(16) NOT NULL CHECK (chunk_type IN ('section', 'symbol')),
    start_line          INTEGER NOT NULL CHECK (start_line > 0),
    end_line            INTEGER NOT NULL CHECK (end_line >= start_line),
    token_count         INTEGER NOT NULL CHECK (token_count > 0),
    embedding_model     VARCHAR(128) NOT NULL,
    index_version       VARCHAR(64) NOT NULL
);

-- 处理运行
CREATE TABLE processing_runs (
    run_id              BIGINT PRIMARY KEY,
    source_id           BIGINT NOT NULL REFERENCES knowledge_sources(source_id),
    run_type            VARCHAR(16) NOT NULL CHECK (run_type IN ('initial', 'retry')),
    status              VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    error_message       TEXT,
    stages              JSONB NOT NULL DEFAULT '[]'
);

-- 检索运行
CREATE TABLE retrieval_runs (
    run_id              BIGINT PRIMARY KEY,
    query_text          TEXT NOT NULL,
    project_scopes      JSONB NOT NULL,
    completion_status   VARCHAR(16) NOT NULL CHECK (completion_status IN ('complete', 'partial', 'failed')),
    evidence_count      INTEGER NOT NULL CHECK (evidence_count >= 0),
    duration_ms         INTEGER NOT NULL CHECK (duration_ms >= 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 6.2 索引策略

| 表 | 索引名 | 列 | 类型 | 用途 |
|----|--------|-----|------|------|
| `knowledge_sources` | `idx_ks_scope_status` | `(knowledge_scope_id, status)` | B-Tree | 按知识域和状态查询知识源 |
| `knowledge_sources` | `idx_ks_scope_hash` | `(knowledge_scope_id, content_hash)` | B-Tree | 同域内重复内容检测 |
| `knowledge_versions` | `idx_kv_scope_status` | `(knowledge_scope_id, status)` | B-Tree | 查找已发布版本 |
| `knowledge_versions` | `idx_kv_scope_number` | `(knowledge_scope_id, version_number)` | B-Tree (UNIQUE) | 版本号唯一性与排序 |
| `chunks` | `idx_chunk_source` | `(source_id)` | B-Tree | 按知识源查找 Chunk |
| `chunks` | `idx_chunk_version` | `(version_id)` | B-Tree | 按版本查找 Chunk |
| `chunks` | `idx_chunk_scope` | `(knowledge_scope_id)` | B-Tree | 按知识域查找 Chunk（删除时使用） |
| `chunks` | `idx_chunk_parent` | `(parent_chunk_id)` | B-Tree | 父子关系查询 |
| `chunks` | `idx_chunk_scope_version` | `(knowledge_scope_id, version_id)` | B-Tree | 检索时复合过滤 |
| `processing_runs` | `idx_pr_source` | `(source_id)` | B-Tree | 按知识源查找处理历史 |
| `processing_runs` | `idx_pr_source_status` | `(source_id, status)` | B-Tree | 查找活跃运行 |
| `retrieval_runs` | `idx_rr_created` | `(created_at)` | B-Tree | 按时间范围查询检索记录 |

### 6.3 触发器

```sql
-- 自动更新 updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER set_updated_at BEFORE UPDATE ON knowledge_scopes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER set_updated_at BEFORE UPDATE ON projects
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER set_updated_at BEFORE UPDATE ON knowledge_sources
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

### 6.4 约束补充

- **Chunk 跨表一致性**：应用层在插入 Chunk 时校验 `source_id.knowledge_scope_id = knowledge_scope_id` 且 `version_id.knowledge_scope_id = knowledge_scope_id`。可选使用 CHECK 触发器强制执行。
- **KnowledgeVersion 单发布约束**：应用层在发布新版本时，先将同域下已有的 `published` 版本转为 `superseded`，再发布新版本。此操作应在事务中完成。
- **ProcessingRun 串行约束**：同一 `source_id` 不允许同时存在多个 `pending` 或 `running` 状态的 ProcessingRun。应用层在创建新运行前检查。

---

## 7. 跨存储一致性规则

PostgreSQL 与 Qdrant 之间的数据一致性是系统正确性的基础。以下规则必须在 001 Feature 中严格执行。

### 7.1 共享标识符

| 标识符 | PostgreSQL | Qdrant | 说明 |
|--------|-----------|--------|------|
| `knowledge_scope_id` | `knowledge_scopes.scope_id` | Payload `knowledge_scope_id` | 所有实体的知识域归属 |
| `source_id` | `knowledge_sources.source_id` | Payload `source_id` | 知识源追溯 |
| `chunk_id` | `chunks.chunk_id` | Point ID | Chunk 唯一标识 |
| `version_id` | `knowledge_versions.version_id` | Payload `version_id` | 版本过滤 |
| `index_version` | `chunks.index_version` | Collection 名称后缀 + Payload | 索引兼容性隔离 |

### 7.2 写入顺序

1. **先写 PostgreSQL**：Chunk 元数据写入 PostgreSQL 表。
2. **再写 Qdrant**：向量和 Payload 写入 Qdrant 集合。
3. **最后更新版本状态**：当且仅当步骤 1 和 2 全部成功后，将 KnowledgeVersion 从 `draft` 转为 `published`。

如果步骤 2 失败：
- PostgreSQL 中的 Chunk 元数据保留（标记为未完成版本的组成部分）。
- KnowledgeVersion 保持 `draft`。
- 允许重试步骤 2，或清除步骤 1 的数据后重新处理。

### 7.3 读取一致性

- 检索时以 PostgreSQL 中 `status = 'published'` 的 KnowledgeVersion 为准。
- Qdrant 查询必须携带 `version_id` 过滤，确保不会检索到未发布或已撤销版本的向量。
- 证据展开时以 PostgreSQL Chunk 正文为准，Qdrant 仅提供向量相似度搜索结果。

### 7.4 删除一致性

1. 先将 KnowledgeSource 标记为 `deleted`（PostgreSQL），立即停止参与新检索。
2. 从 Qdrant 中删除该知识源的所有 Point（按 `source_id` 过滤）。
3. 从 PostgreSQL 中删除或归档该知识源的 Chunk 元数据。
4. 更新受影响的知识版本状态（如有必要）。

步骤 2 和 3 的顺序可以互换，但步骤 1 必须先于两者执行。

### 7.5 index_version 一致性

- `index_version` 由 `embedding_model` 标识 + 切片策略版本号组合生成（如 `bge-m3_v1`）。
- 同一 `index_version` 下的所有 Chunk 必须使用相同的 Embedding 模型和切片策略。
- 切换 Embedding 模型时，必须创建新的 Qdrant 集合和新的 KnowledgeVersion，不得向旧集合写入新模型的向量。
- `chunks.index_version` 字段的值必须与该 Chunk 所在 Qdrant 集合的名称后缀一致。

---

## 8. SSE 异步反馈集成

SSE（Server-Sent Events）用于向 SPA 推送异步操作的进度和状态更新（FR-027）。SSE 本身不引入新的持久化实体，但需要与以下实体的状态变更联动：

| 事件类型 | 关联实体 | 推送内容 |
|----------|---------|---------|
| `source.status_changed` | KnowledgeSource | `source_id`, `old_status`, `new_status`, `processing_error` |
| `processing.stage_completed` | ProcessingRun | `run_id`, `source_id`, `stage`, `status`, `progress` |
| `version.status_changed` | KnowledgeVersion | `version_id`, `knowledge_scope_id`, `old_status`, `new_status` |
| `scope.deletion_progress` | KnowledgeScope | `scope_id`, `deleted_sources`, `total_sources`, `status` |

SSE 事件为瞬态通知，不持久化。客户端断连重连后可通过 REST API 查询当前状态。

---

## 9. 001 Feature 范围限定

以下实体和能力**不在** 001 Feature 范围内，但在数据模型中预留扩展点：

| 排除项 | 预留方式 | 后续 Feature |
|--------|---------|-------------|
| BM25/Sparse 向量 | Qdrant 集合命名约定支持多集合；Chunk 表无 sparse 字段 | 002 Hybrid Retrieval Precision |
| Graph 节点/边表 | 数据库 Schema 不包含 graph_nodes/graph_edges 表 | 004 Graph RAG |
| 证据账本表 | RetrievalRun 仅记录摘要；追加式证据明细表后续添加 | 005 Agentic Retrieval Orchestration |
| Reranker 配置 | 不在本数据模型中体现 | 002 |
| 多格式支持 | `format` CHECK 约束仅含 markdown/java | 003 Structured Asset Expansion |
| 读写分离协调 | 无 WriteCoordinator 租约表 | 006 Runtime Hardening |
| 追踪详细日志 | RetrievalRun 仅含摘要字段 | 006 |

---

## 10. 附录：Snowflake ID 生成规范

- 使用 64 位整数，兼容 PostgreSQL BIGINT 和 Qdrant u64 Point ID。
- 时间戳精度为毫秒级。
- 同一进程内保证单调递增。
- 不同实例间通过 worker_id 区分（001 单 Writer 场景下 worker_id 固定为 0）。
- ID 一旦分配永不复用，即使对应实体被删除。
