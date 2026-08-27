# 001 Minimum RAG MCP Loop 技术研究文档

**Feature**: 001-minimum-rag-mcp-loop  
**状态**: Draft  
**日期**: 2026-08-27  
**目的**: 解决首个端到端闭环实现中的所有技术未知项，为 plan.md 提供决策依据。

---

## 一、技术栈决策

### 1.1 Python 版本与后端框架

**Decision**: Python 3.12 + FastAPI (async) + Uvicorn  
**Rationale**: 
- Python 3.12 是当前 LangChain/LangGraph 官方推荐的稳定版本，对 async/await 和类型提示支持完善。
- FastAPI 原生支持 async、SSE（通过 StreamingResponse）、自动 OpenAPI 文档生成，与 MCP SDK 的 ASGI 兼容性好。
- Uvicorn 作为 ASGI 服务器，性能满足单机多并发场景（SC-008 要求 5 并发）。
- 设计蓝图 §16.4 明确要求 Python REST 管理 API，FastAPI 是最成熟的异步 Python Web 框架。

**Alternatives Considered**:
- **Flask**: 同步为主，async 支持弱，SSE 需额外扩展，不适合高并发入库+检索场景。已排除。
- **Django REST Framework**: 功能过重，ORM 与 SQLAlchemy/Alembic 生态不兼容，启动慢。已排除。
- **Starlette**: FastAPI 底层框架，缺少自动文档和依赖注入，开发效率低。已排除。

### 1.2 编排框架

**Decision**: LangGraph (状态机) + LangChain (模型/Retriever 适配)  
**Rationale**: 
- 设计蓝图 §12 和 Constitution §IV 明确要求"确定性控制优先"，LangGraph 的状态图模型天然支持确定性节点流转和护栏限制。
- LangChain 提供统一的 Embedding/Reranker/LLM Provider 抽象，符合蓝图 §17 的能力路由层要求。
- 001 仅使用确定性检索路径（项目过滤 → Dense 召回 → 排序 → 父级补全 → 装箱），LangGraph 可简化为线性图，为后续三 Agent 预留扩展点。
- 社区活跃，与 Qdrant、BGE 集成成熟。

**Alternatives Considered**:
- **纯代码状态机**: 可控性最强，但缺乏可视化调试、checkpoint 和重试机制，后续引入三 Agent 时改造成本高。已排除。
- **CrewAI / AutoGen**: 面向多 Agent 对话，不适合确定性检索流水线，且与 MCP SDK 集成不成熟。已排除。
- **LlamaIndex**: 检索能力强但状态机支持弱，与 LangGraph 混用增加复杂度。001 不需要其高级索引能力。已排除。

### 1.3 前端技术栈

**Decision**: React 18 + TypeScript + Vite + Ant Design (antd)  
**Rationale**: 
- FR-001 要求"功能完整 SPA，基础组件库样式，无视觉打磨"。Ant Design 提供开箱即用的表格、表单、上传、进度条、消息通知等组件，覆盖管理端全部需求。
- Vite 构建速度快，HMR 体验好，适合单用户本地开发。
- TypeScript 保障类型安全，与后端 JSON Schema 契约对齐。
- React 18 的 Concurrent Mode 支持 SSE 流式更新渲染。

**Alternatives Considered**:
- **Vue 3 + Element Plus**: 功能等价，但团队熟悉度和 AI 工程生态中 React 更主流。已排除。
- **Next.js**: SSR/SSG 对本项目无价值，增加部署复杂度。已排除。
- **shadcn/ui + Tailwind**: 需要更多自定义组装，不符合"首轮不要求视觉打磨"的快速交付目标。已排除。

### 1.4 向量存储

**Decision**: Qdrant (Dense-only for 001)  
**Rationale**: 
- 设计蓝图 §8.1 和 Constitution Architecture Constraints 明确指定 Qdrant。
- 001 仅使用 Dense 检索，Qdrant 的 HNSW 索引在 CPU 模式下性能足够（蓝图 §18.5）。
- Qdrant 原生支持 payload filtering（knowledge_scope_id、project_id、index_version），满足项目隔离和版本过滤需求。
- 后续 002 Feature 可直接启用 Sparse/BM25，无需更换存储。
- 提供 Python SDK 和 REST API，与 FastAPI 集成简单。

**Alternatives Considered**:
- **Chroma**: 轻量但过滤能力弱，不支持复杂 payload 查询，后续混合检索升级困难。已排除。
- **Weaviate**: 功能丰富但运维重，GraphQL API 与本项目 REST 风格不一致。已排除。
- **pgvector**: 与 PostgreSQL 同库，但向量检索性能低于专用引擎，且蓝图明确要求 Qdrant。已排除。

### 1.5 控制面数据库

**Decision**: PostgreSQL 16 + SQLAlchemy 2.0 (async) + Alembic  
**Rationale**: 
- 设计蓝图 §8.2 和 Constitution 明确指定 PostgreSQL 负责控制面数据。
- SQLAlchemy 2.0 async 与 FastAPI/Uvicorn 配合良好，支持连接池和事务隔离。
- Alembic 是 SQLAlchemy 官方迁移工具，支持版本化、回滚和多环境。
- PostgreSQL 16 的 JSONB、数组类型和递归 CTE 满足 Chunk 元数据、图关系和证据账本需求。
- 蓝图 §21.2 要求 Writer 租约，PostgreSQL advisory lock 可实现单 Writer 协调。

**Alternatives Considered**:
- **SQLite**: 不支持并发写入，无法满足 SC-008 的 5 并发要求。已排除。
- **MySQL**: 缺少原生 JSONB 和递归 CTE 优化，图关系查询性能差。已排除。
- **MongoDB**: 文档模型不适合强关系型控制面数据，事务支持弱于 PostgreSQL。已排除。

### 1.6 Embedding 模型

**Decision**: BAAI/bge-m3 (Dense only, local CPU default via sentence-transformers)  
**Rationale**: 
- 设计蓝图 §18.2 和 Constitution 明确指定 bge-m3 为本地默认。
- bge-m3 支持 Dense/Sparse/ColBERT 三种表示，001 仅用 Dense，但为 002 预留升级路径。
- sentence-transformers 库提供统一的 CPU/GPU/API 加载接口，符合蓝图 §18.1 的 Provider 抽象要求。
- CPU 模式下 512-token chunk 的 embedding 延迟约 50-100ms/chunk（8 核现代 CPU），满足入库批处理和在线查询需求。
- 模型大小约 2GB，16GB RAM 基线可容纳。

**Alternatives Considered**:
- **text-embedding-3-small (OpenAI)**: 需网络调用，增加延迟和成本，不符合"本地默认"原则。作为 Remote API Provider 备选保留。
- **nomic-embed-text**: 性能略优但社区生态不如 BGE，与 LangChain 集成文档少。已排除。
- **e5-mistral-7b-instruct**: 需 GPU，不符合 CPU 默认要求。作为 GPU Provider 备选保留。

### 1.7 Snowflake ID 生成

**Decision**: `snowflake-id` Python 库 (or `snowflakeid`)  
**Rationale**: 
- FR-003 和 Clarification 明确要求知识域 ID 使用雪花 ID。
- 雪花 ID 保证分布式唯一性、时间有序性和可解析性（包含时间戳、机器ID、序列号）。
- Python 生态中有多个成熟实现，选择纯 Python 无 C 依赖的版本以降低安装复杂度。
- 可在应用层生成，不依赖数据库自增，符合跨存储一致性要求（蓝图 §8.4）。

**Alternatives Considered**:
- **UUID v4**: 无序，索引性能差，不可解析时间信息。已排除。
- **UUID v7**: 时间有序但生态支持不如雪花 ID，Python 标准库未内置。已排除。
- **数据库自增**: 绑定单一数据库，无法跨 Qdrant/PostgreSQL 统一标识。已排除。

### 1.8 SSE 实现方案

**Decision**: FastAPI StreamingResponse + asyncio.Queue per client  
**Rationale**: 
- FR-027 要求 SSE 推送异步操作进度。
- FastAPI 原生支持 `StreamingResponse(media_type="text/event-stream")`，无需额外依赖。
- 每个 SPA 客户端连接分配独立 asyncio.Queue，服务端任务完成时向相关队列推送事件。
- 事件格式遵循标准 SSE：`event: <type>\ndata: <json>\n\n`。
- 支持断线重连（Last-Event-ID）和心跳保活。

**Alternatives Considered**:
- **WebSocket**: 双向通信过重，SSE 单向推送已满足需求，且浏览器原生支持更好。已排除。
- **Polling**: 延迟高，浪费请求，不符合实时反馈需求。已排除。
- **第三方 SSE 库 (sse-starlette)**: FastAPI 内置能力已足够，引入额外依赖无收益。已排除。

### 1.9 MCP SDK 选择

**Decision**: Python `mcp` SDK (official Model Context Protocol SDK)  
**Rationale**: 
- 设计蓝图 §16.2 要求 Streamable HTTP 传输，Python mcp-sdk 官方支持该传输方式。
- 与 FastAPI/ASGI 集成成熟，可作为独立服务或与 FastAPI 共存。
- 支持 Tool、Resource、Prompt 定义，001 仅需 Tool（search_knowledge, get_evidence）。
- structuredContent 支持 JSON Schema 2020-12 校验，符合蓝图 §16.7 要求。

**Alternatives Considered**:
- **TypeScript mcp-sdk**: 后端为 Python，跨语言调用增加复杂度。已排除。
- **自实现 MCP 协议**: 工作量大，易出错，不符合"不重复造轮子"原则。已排除。
- **LangChain MCP adapter**: 封装层过厚，调试困难，直接使用官方 SDK 更透明。已排除。

---

## 二、架构决策

### 2.1 项目结构

**Decision**: Monorepo with `backend/` and `frontend/` top-level directories  
**Rationale**: 
- 单用户本地部署，monorepo 简化依赖管理和版本同步。
- `backend/` 包含 FastAPI app、LangGraph graphs、models、services、MCP server。
- `frontend/` 包含 React+TS SPA，构建产物由 FastAPI 静态文件服务托管（蓝图 §16.4）。
- 共享契约目录 `contracts/` 位于根级别，供前后端引用（蓝图 §22）。
- 符合 specKit 工件结构要求（蓝图 §23.2）。

**Alternatives Considered**:
- **Multi-repo**: 单用户项目过度工程化，CI/CD 复杂度高。已排除。
- **Nx/Turborepo**: 适用于大型团队，本项目规模不需要。已排除。
- **Backend-only with embedded frontend**: 前后端耦合过紧，不利于独立开发和测试。已排除。

### 2.2 数据库迁移策略

**Decision**: Alembic with auto-generate and versioned migrations  
**Rationale**: 
- SQLAlchemy 2.0 + Alembic 是 Python 生态事实标准。
- 自动生成迁移脚本减少手写错误，人工审核后提交。
- 迁移文件纳入版本控制，支持回滚和多环境部署。
- 001 初始迁移包含：knowledge_scopes, projects, knowledge_sources, knowledge_versions, chunks, processing_runs, retrieval_runs 表。

**Alternatives Considered**:
- **手动 SQL**: 易出错，不可追溯，不支持回滚。已排除。
- **SQLModel built-in migration**: 功能有限，不支持复杂约束和索引。已排除。
- **Django migrations**: 与 SQLAlchemy 生态不兼容。已排除。

### 2.3 文件上传存储策略

**Decision**: Local filesystem under configurable data root (Writer mode)  
**Rationale**: 
- 设计蓝图 §21.2 明确首期 Writer 使用本地文件系统，通过 SourceObjectStore 抽象预留 S3 扩展。
- 单用户本地部署，对象存储过度工程化。
- 文件按 `{data_root}/{knowledge_scope_id}/{source_id}/{version}/original` 组织，便于清理和重建。
- Chunk 正文和元数据存入 PostgreSQL，Reader 检索不依赖原始文件（蓝图 §21.2）。

**Alternatives Considered**:
- **MinIO/S3**: 首期单用户无需分布式存储，增加运维成本。作为后续扩展保留。
- **数据库 BLOB**: 大文件性能差，备份恢复慢。已排除。
- **内存存储**: 重启丢失，不可接受。已排除。

### 2.4 凭据脱敏方案

**Decision**: Regex-based pattern matching with typed placeholders  
**Rationale**: 
- 设计蓝图 §7 和 Constitution §III 要求凭据值替换为类型化占位符，保留字段名和结构。
- 001 仅处理 Markdown 和 Java，正则模式可覆盖常见凭据格式：
  - API Key: `(?i)(api[_-]?key|token|secret)\s*[:=]\s*['"]?[A-Za-z0-9_\-]{16,}['"]?` → `<api-key>`
  - Password: `(?i)(password|passwd|pwd)\s*[:=]\s*['"]?[^\s'"]{4,}['"]?` → `<password>`
  - Token: `(?i)(bearer|authorization)\s*[:=]\s*['"]?[A-Za-z0-9_\-\.]{20,}['"]?` → `<token>`
- 在切片前执行，确保 Qdrant 和 MCP 响应不含原始值。
- 保留变量名、赋值语句结构和来源行号，满足检索需求。

**Alternatives Considered**:
- **NER-based detection**: 误报率高，依赖模型，延迟大。001 不需要广义敏感信息检测。已排除。
- **AST-only extraction**: 仅适用于代码，Markdown 中的凭据无法处理。已排除。
- **Presidio/AWS Macie**: 企业级工具过重，首期不需要。已排除。

### 2.5 Markdown 章节感知切片

**Decision**: Custom markdown-it-py parser with heading hierarchy tracking  
**Rationale**: 
- FR-007 要求 Markdown 使用章节感知切片，保留父子上下文。
- markdown-it-py 是 Python 生态最成熟的 Markdown 解析器，支持 AST 遍历和自定义规则。
- 切片逻辑：
  1. 解析标题层级（#, ##, ###...）构建章节树。
  2. 每个叶子章节（含内容）作为一个 Chunk。
  3. 父 Chunk 包含子 Chunk 列表和章节路径（如 `## 安装 > ### 配置`）。
  4. 超长章节按段落边界二次切分，保留父级引用。
- **Chunk 大小控制**（蓝图 §7）：目标 512–1024 Token。叶子章节超过 1024 Token 时按段落边界切分为多个子 Chunk，每个子 Chunk 保留相同的 section_path 和 parent_chunk_id。低于 64 Token 的叶子章节合并到父 Chunk 中，不单独建索引。
- 输出包含 section_path、start_line、end_line、parent_chunk_id、token_count。

**Alternatives Considered**:
- **LangChain MarkdownTextSplitter**: 仅按标题切分，不保留父子关系和章节路径。已排除。
- **Unstructured.io**: 功能丰富但依赖重，001 仅需 Markdown 解析。已排除。
- **正则表达式**: 无法正确处理嵌套列表、代码块和引用。已排除。

### 2.6 Java 符号感知切片

**Decision**: tree-sitter-java with Python bindings  
**Rationale**: 
- FR-007 要求 Java 使用符号感知切片，保留类、方法、字段等符号边界。
- tree-sitter 提供增量解析、容错性强（部分语法错误仍可解析），符合 Edge Case "Java 文件无法形成完整语法树时报告降级"。
- tree-sitter-java 绑定成熟，解析速度远快于 javalang（C 实现 vs 纯 Python）。
- 切片逻辑：
  1. 解析 AST，提取 class/interface/method/field 节点。
  2. 每个顶层符号（class/interface）作为一个父 Chunk。
  3. 方法/字段作为子 Chunk，携带全限定符号路径（如 `com.example.Service#methodName`）。
  4. 保留 import 和 package 声明作为上下文附加到父 Chunk。
- **Chunk 大小控制**（蓝图 §7）：目标 512–1024 Token。单个方法超过 1024 Token 时按逻辑块（try-catch、if-else、循环体）边界切分，每个子 Chunk 保留相同的 symbol_path 前缀和 parent_chunk_id。低于 64 Token 的字段/常量合并到所属类 Chunk 中。
- 输出包含 symbol_path、symbol_type、start_line、end_line、parent_chunk_id、token_count。

**Alternatives Considered**:
- **javalang**: 纯 Python，解析速度慢，容错性差（语法错误直接抛异常）。已排除。
- **Eclipse JDT/Core**: JVM 依赖，与 Python 后端集成复杂。已排除。
- **正则表达式**: 无法处理嵌套类、泛型、注解等复杂语法。已排除。

### 2.7 评测数据集生成方案

**Decision**: AI-generated queries with human review, stored as JSON  
**Rationale**: 
- FR-024 要求"AI 生成、人工审核、JSON 格式"。
- 生成流程：
  1. 从验收材料中提取关键实体（类名、方法名、章节标题、配置项）。
  2. 使用 LLM 生成自然语言查询，覆盖精确匹配、语义相似、跨章节、跨文件场景。
  3. 人工审核查询质量和 expected_evidence_ids 准确性。
  4. 存储为 JSON：`[{"query": "...", "project_scope": "...", "expected_evidence_ids": [...]}]`。
- 首轮 demo 目标 20-30 条查询，覆盖 US-1~US-4 核心场景。
- 基线指标（Recall@K, MRR, nDCG, P50/P95）记录但不设阈值（SC-009）。

**Alternatives Considered**:
- **纯人工编写**: 耗时过长，001 不需要大规模测试集。已排除。
- **合成数据生成器**: 缺乏项目特定语义，生成的查询不真实。已排除。
- **历史日志挖掘**: 001 为新系统，无历史数据。已排除。

---

## 三、集成决策

### 3.1 Streamable HTTP MCP 传输配置

**Decision**: Standalone MCP server on fixed port (default 8080), separate from FastAPI management API (port 8000)  
**Rationale**: 
- 设计蓝图 §16.2 要求 Streamable HTTP 为主传输，固定端口供客户端手动配置。
- MCP server 与管理 API 分离，避免路由冲突和安全面扩大。
- MCP server 使用 `mcp.server.streamable_http` 模块，绑定 `127.0.0.1:8080`（FR-026 要求默认仅本机访问）。
- 支持 POST `/mcp` (tool call) 和 GET `/mcp` (SSE stream) 端点。
- 客户端配置示例（DeepSeek Harness）：
  ```json
  {
    "mcpServers": {
      "rag-mcp": {
        "url": "http://127.0.0.1:8080/mcp"
      }
    }
  }
  ```

**Alternatives Considered**:
- **与 FastAPI 同端口**: 路由前缀冲突风险，MCP 协议与管理 API 生命周期不同。已排除。
- **stdio transport**: 仅适用于本地进程间通信，不支持多客户端并发。作为适配器保留，非 001 主路径。
- **HTTP+SSE (deprecated)**: 蓝图 §16.2 明确不采用已废弃协议。已排除。

### 3.2 DeepSeek Harness MCP 客户端配置格式

**Decision**: Standard MCP server configuration in dsh.json or session config  
**Rationale**: 
- DeepSeek Harness 使用标准 MCP 客户端协议，配置格式与其他 MCP Host 一致。
- 001 验收以 DeepSeek Harness 为参考客户端（SC-005），需确保配置可用。
- 配置位置：`~/.dsh/dsh.json` 或会话级 `.dsh/mcp.json`。
- 最小配置：
  ```json
  {
    "mcpServers": {
      "ai-eng-rag": {
        "url": "http://127.0.0.1:8080/mcp",
        "transport": "streamable-http"
      }
    }
  }
  ```
- 文档化配置步骤作为 quickstart.md 的一部分。

**Alternatives Considered**:
- **环境变量配置**: 不支持多服务器，格式不标准。已排除。
- **命令行参数**: 每次启动需重复输入，不适合持久化配置。已排除。

### 3.3 SSE 端点设计

**Decision**: `/api/events` endpoint with topic-based filtering  
**Rationale**: 
- FR-027 要求 SSE 推送异步操作进度。
- 端点：`GET /api/events?topics=upload,processing,publish,delete`
- 事件类型：
  - `upload.started`: `{source_id, filename}`
  - `processing.progress`: `{source_id, stage, percent, message}`
  - `processing.completed`: `{source_id, version_id, status}`
  - `processing.failed`: `{source_id, error, retryable}`
  - `publish.started`: `{version_id}`
  - `publish.completed`: `{version_id, capabilities}`
  - `delete.started`: `{scope_id, source_id?}`
  - `delete.completed`: `{scope_id, source_id?}`
- 客户端通过 EventSource API 订阅，支持自动重连。
- 服务端维护活跃订阅者列表，任务完成时广播相关事件。

**Alternatives Considered**:
- **每资源独立 SSE 端点**: 连接数过多，管理复杂。已排除。
- **WebSocket**: 双向通信过重，SSE 已满足单向推送需求。已排除。
- **Long polling**: 延迟高，服务端负载大。已排除。

---

## 四、澄清遗留项决策

### 4.1 凭据占位符格式

**Decision**: Angle-bracket typed placeholders: `<api-key>`, `<password>`, `<token>`, `<secret>`  
**Rationale**: 
- 设计蓝图 §7 示例使用 `<api-key>`、`<password>`、`<token>` 格式。
- 尖括号在 Markdown 和 Java 中均为合法文本，不会破坏语法结构。
- 类型化占位符保留语义信息（区分 API Key 和密码），便于后续审计和分析。
- 与常见文档脱敏惯例一致（如 AWS 文档、GitHub secret scanning）。

**Alternatives Considered**:
- **`[REDACTED]`**: 丢失类型信息，无法区分不同凭据种类。已排除。
- **`***`**: 过于模糊，可能被误认为格式化文本。已排除。
- **UUID placeholder**: 可追踪但丧失可读性，001 不需要占位符溯源。已排除。

### 4.2 Knowledge Source 状态机定义

**Decision**: Five-state lifecycle with explicit transitions  
**Rationale**: 
- 基于 spec.md User Stories 和 Edge Cases 推导的最小完备状态机。
- 状态：
  1. `uploaded`: 文件接收成功，哈希计算完成，等待处理。
  2. `processing`: 正在执行解析、切片、向量化。
  3. `published`: 所有必需索引就绪，版本可检索。
  4. `failed`: 处理失败，保留错误信息，允许重试。
  5. `deleted`: 用户触发删除，停止参与检索，异步清理派生数据。
- 转换规则：
  - `uploaded` → `processing` (自动触发)
  - `processing` → `published` (全部索引成功)
  - `processing` → `failed` (任一步骤失败)
  - `failed` → `processing` (用户重试)
  - `published` → `deleted` (用户删除)
  - `uploaded` → `deleted` (用户删除未处理材料)
  - `processing` → `deleted` (用户中断处理)
- `deleted` 为终态，不可逆转。
- 状态变更通过 SSE 实时推送（FR-027）。

**Alternatives Considered**:
- **三状态 (pending/active/deleted)**: 无法区分处理中和已发布，用户无法判断何时可检索。已排除。
- **七状态 (含 archiving/restoring)**: 001 不需要归档和恢复功能，过度设计。已排除。
- **隐式状态 (基于字段组合)**: 查询复杂，易出错，不符合显式状态机原则。已排除。

---

## 五、检索管道护栏配置（蓝图 §12）

001 虽简化为线性管道，仍需以下护栏防止资源耗尽和响应失控：

| 护栏参数 | 默认值 | 说明 |
|----------|--------|------|
| `retrieval.total_timeout_ms` | 30000 | 单次 search_knowledge 总超时（含 Qdrant 查询 + 排序 + 父级补全） |
| `retrieval.qdrant_query_timeout_ms` | 10000 | Qdrant 单次向量查询超时 |
| `retrieval.max_evidence_per_source` | 5 | 单个知识源在一次检索中最多贡献的证据条数，防止单源垄断结果 |
| `retrieval.top_k_default` | 5 | 默认返回证据条数 |
| `retrieval.top_k_max` | 20 | 客户端可请求的最大证据条数 |
| `retrieval.max_parent_context_tokens` | 2000 | get_evidence 返回父级上下文的最大 Token 数 |
| `ingestion.batch_size` | 32 | Embedding 批处理大小 |
| `ingestion.chunk_target_tokens` | 768 | Chunk 目标 Token 数（512–1024 范围中位数） |
| `ingestion.chunk_min_tokens` | 64 | 低于此值的 Chunk 合并到父级 |
| `ingestion.chunk_max_tokens` | 1024 | 超过此值的 Chunk 按边界切分 |

所有护栏参数通过 `config.py` 管理，支持环境变量覆盖。服务端总超时（30s）必须小于目标 Host 的 Tool Call 超时。

---

## 六、Host 超时映射（蓝图 §19）

每个目标 MCP Host 有独立的超时配置，服务端护栏必须小于对应 Host 限制：

| Host | Tool Call 超时 | 服务端 total_timeout | 备注 |
|------|---------------|---------------------|------|
| DeepSeek Harness (参考客户端) | `toolCallTimeoutMs`（用户配置，默认60000ms） | 30000ms | 001验收参考客户端 |
| ChatGPT App (Codex) | 宿主MCP Tool超时策略（通常60-120s） | 30000ms | 兼容性记录，非阻塞 |
| Claude Code | MCP Tool超时配置（通常60s） | 30000ms | 兼容性记录，非阻塞 |

**原则**：服务端 `total_timeout` 固定为 30s，为所有已知 Host 留足余量。当部分检索路径超时但已有可靠证据时返回 `partial`；完全超时时返回 `failed`。超时数值后续可根据 P50/P95 评测调整。

---

## 八、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| bge-m3 CPU 模式入库延迟过高 | 大文件处理超时 | 异步批处理 + 进度 SSE + 可配置 batch size；优先在线查询 |
| tree-sitter-java 解析失败 | Java 文件无法切片 | 容错解析 + 降级为行级切片 + 明确失败原因（Edge Case） |
| SSE 连接断开丢失进度 | 用户体验差 | 客户端自动重连 + Last-Event-ID + 服务端事件缓冲（30s） |
| Qdrant/PostgreSQL 不一致 | 检索返回孤儿证据 | 事务性发布 + 版本能力清单 + 定期一致性检查任务 |
| MCP 客户端超时 | Tool call 失败 | 服务端总超时 < 客户端超时；partial 返回已有证据；日志记录 |

---

## 九、决策摘要表

| 领域 | 决策 | 关键理由 |
|------|------|----------|
| Python 版本 | 3.12 | LangChain/LangGraph 推荐，async 支持完善 |
| 后端框架 | FastAPI + Uvicorn | Async、SSE、OpenAPI、ASGI 兼容 |
| 编排框架 | LangGraph + LangChain | 确定性状态机 + Provider 抽象 |
| 前端 | React 18 + TS + Vite + Ant Design | 功能完整 SPA，快速交付 |
| 向量存储 | Qdrant (Dense-only) | 蓝图指定，过滤能力强，可扩展 |
| 控制面 DB | PostgreSQL 16 + SQLAlchemy + Alembic | 蓝图指定，关系型，迁移成熟 |
| Embedding | BAAI/bge-m3 (CPU) | 蓝图指定，本地默认，Provider 抽象 |
| ID 生成 | snowflake-id | 分布式唯一，时间有序 |
| SSE | FastAPI StreamingResponse | 原生支持，无额外依赖 |
| MCP SDK | Python mcp (official) | Streamable HTTP 支持，structuredContent |
| 项目结构 | Monorepo (backend/frontend) | 单用户简化，契约共享 |
| 文件存储 | Local filesystem (Writer) | 首期单用户，SourceObjectStore 抽象 |
| 凭据脱敏 | Regex + typed placeholders | 蓝图要求，保留结构 |
| Markdown 切片 | markdown-it-py + heading tree | 章节感知，父子关系 |
| Java 切片 | tree-sitter-java | 符号感知，容错性强 |
| 评测集 | AI-generated + human review | FR-024 要求，JSON 格式 |
| MCP 传输 | Streamable HTTP :8080 | 蓝图指定，固定端口 |
| 凭据占位符 | `<api-key>` etc. | 蓝图示例，类型化 |
| 知识源状态机 | 5-state lifecycle | 最小完备，SSE 推送 |

---

**下一步**: 本文档为 plan.md 提供全部技术决策依据。plan.md 应引用本文件中的决策，并将每个决策映射到具体任务和验收标准。
