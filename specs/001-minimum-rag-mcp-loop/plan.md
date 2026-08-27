# Implementation Plan: 001 Minimum RAG MCP Loop

**Branch**: `001-minimum-rag-mcp-loop` | **Date**: 2026-08-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-minimum-rag-mcp-loop/spec.md`
**Design Blueprint**: `docs/superpowers/specs/2026-08-26-ai-engineering-rag-mcp-design.md`

## Summary

建立首个端到端RAG MCP闭环：用户通过React+TypeScript SPA管理项目知识域，上传Markdown和Java材料；系统执行凭据规范化、结构切片和Dense向量化；通过Streamable HTTP MCP服务向DeepSeek Harness（参考客户端）提供`search_knowledge`和`get_evidence`两个只读Tool；建立可重复的评测基线。全部四个用户故事（US-1~US-4）纳入首轮demo验收。

## Technical Context

**Language/Version**: Python 3.12 (backend), TypeScript 5.x + React 18 (frontend)

**Primary Dependencies**:
- Backend: FastAPI, LangGraph, LangChain, mcp-sdk (Python), SQLAlchemy 2.0, Alembic
- Frontend: React 18, TypeScript, Vite, Ant Design (基础组件库)
- Embedding: sentence-transformers + BAAI/bge-m3 (local CPU default)
- Vector Store: Qdrant (Dense retrieval only for 001)
- Database: PostgreSQL 16+
- ID Generation: snowflake-id (Python)
- Java Parsing: tree-sitter + tree-sitter-java
- Markdown Parsing: markdown-it-py + custom section splitter

**Storage**: PostgreSQL (control plane, chunk metadata, version state) + Qdrant (dense vectors) + Local filesystem (raw uploaded files)

**Testing**: pytest (backend unit/integration), Playwright (E2E), jsonschema (contract validation)

**Target Platform**: Local machine (Windows/macOS/Linux), single-writer mode

**Project Type**: Web application (SPA frontend + REST API backend + MCP server)

**Performance Goals**: Dense检索基线产出（Recall@K, MRR, nDCG, P50/P95延迟），首轮不设阈值

**Constraints**: 
- 默认仅监听127.0.0.1
- 单Writer实例
- 不依赖Neo4j、MCP Resources或MCP Tasks
- 并发上限5个请求

**Scale/Scope**: 单用户，多项目，首轮demo验收覆盖全部4个用户故事

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Explicit Knowledge Scope | ✅ PASS | FR-014拒绝无scope检索；所有实体携带knowledge_scope_id |
| II. Evidence Before Inference | ✅ PASS | 001使用确定性检索，无LLM推断；证据携带source_version和source_position |
| III. Untrusted Content Isolation | ✅ PASS | FR-006凭据值替换为类型化占位符；上传内容不作为控制指令 |
| IV. Deterministic Control | ✅ PASS | 001不使用三Agent编排；状态流转由确定性代码控制 |
| V. Versioned Knowledge | ✅ PASS | KnowledgeVersion声明dense_ready能力；未发布版本不参与检索 |
| VI. Client-Compatible MCP | ✅ PASS | structuredContent为规范源；TextContent确定性生成；不依赖Resources/Tasks |
| VII. Evaluation-Driven | ✅ PASS | FR-024建立固定评测集；跨项目串库目标为零 |

**Architecture Constraints Compliance**:
- Python + LangGraph + LangChain ✅
- React + TypeScript ✅
- Qdrant for Dense ✅
- PostgreSQL for control plane ✅
- BAAI/bge-m3 default ✅
- Streamable HTTP primary transport ✅
- Single-writer/multi-reader abstractions ✅
- Loopback binding default ✅

## Project Structure

### Documentation (this feature)

```text
specs/001-minimum-rag-mcp-loop/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── common.schema.json            # 共享类型定义（蓝图 §22）
│   ├── mcp-search-input.schema.json
│   ├── mcp-search-output.schema.json
│   ├── mcp-get-evidence.schema.json
│   └── management-api.schema.json
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
backend/
├── pyproject.toml
├── alembic/
│   ├── alembic.ini
│   └── versions/
├── src/
│   └── rag_mcp/
│       ├── __init__.py
│       ├── server.py              # FastAPI app + MCP server entry point
│       ├── config.py              # Runtime configuration
│       ├── models/                # SQLAlchemy ORM models
│       │   ├── __init__.py
│       │   ├── knowledge_scope.py
│       │   ├── project.py
│       │   ├── knowledge_source.py
│       │   ├── knowledge_version.py
│       │   ├── chunk.py
│       │   ├── processing_run.py
│       │   └── retrieval_run.py
│       ├── schemas/               # Pydantic request/response schemas
│       │   ├── __init__.py
│       │   ├── project.py
│       │   ├── knowledge_source.py
│       │   └── mcp.py
│       ├── services/              # Business logic layer
│       │   ├── __init__.py
│       │   ├── project_service.py
│       │   ├── ingestion_service.py
│       │   ├── retrieval_service.py
│       │   ├── evidence_service.py
│       │   └── evaluation_service.py
│       ├── parsers/               # Format-aware parsers
│       │   ├── __init__.py
│       │   ├── markdown_parser.py
│       │   ├── java_parser.py
│       │   └── credential_redactor.py
│       ├── providers/             # Model provider abstraction (蓝图 §18.1)
│       │   ├── __init__.py
│       │   ├── base.py            # EmbeddingProvider / RerankerProvider / LLMProvider ABC
│       │   ├── local_cpu.py       # sentence-transformers CPU backend (001 default)
│       │   ├── local_gpu.py       # GPU backend stub (001 interface only)
│       │   └── remote_api.py      # Remote API backend stub (001 interface only)
│       ├── indexing/              # Vector indexing
│       │   ├── __init__.py
│       │   ├── embedder.py
│       │   └── qdrant_client.py
│       ├── mcp/                   # MCP tool implementations
│       │   ├── __init__.py
│       │   ├── search_knowledge.py
│       │   └── get_evidence.py
│       ├── api/                   # REST management API routes
│       │   ├── __init__.py
│       │   ├── projects.py
│       │   ├── knowledge_sources.py  # 含 POST /{id}/reprocess (蓝图 §5)
│       │   └── sse.py
│       └── utils/
│           ├── __init__.py
│           ├── snowflake.py
│           └── hashing.py
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── test_parsers/
    │   ├── test_services/
    │   └── test_utils/
    ├── integration/
    │   ├── test_api/
    │   ├── test_mcp/
    │   └── test_ingestion/
    └── contract/
        ├── test_mcp_schemas.py
        └── test_management_schemas.py

frontend/
├── package.json
├── tsconfig.json
├── vite.config.ts
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/                    # REST API client
│   │   ├── client.ts
│   │   ├── projects.ts
│   │   └── knowledgeSources.ts
│   ├── components/             # UI components
│   │   ├── ProjectList.tsx
│   │   ├── ProjectDetail.tsx
│   │   ├── FileUpload.tsx
│   │   ├── ProcessingStatus.tsx
│   │   └── KnowledgeSourceList.tsx
│   ├── hooks/
│   │   ├── useSSE.ts
│   │   └── useProjects.ts
│   ├── pages/
│   │   ├── ProjectsPage.tsx
│   │   └── ProjectDetailPage.tsx
│   └── types/
│       └── index.ts
└── tests/
    └── e2e/
        └── project-management.spec.ts

docker-compose.yml              # PostgreSQL + Qdrant for local dev
eval/
├── eval_dataset.json            # AI-generated, human-reviewed evaluation set
└── run_eval.py                  # Evaluation runner script
```

### 实际实现与计划的差异（迭代 0 结构裁定）

实现采用「合并/内联」优于「占位文件」的原则，以下计划中的文件未按原样创建，改由既有文件承担其职责：

| 计划文件 | 实际落点 | 裁定 |
|----------|----------|------|
| `indexing/embedder.py` | Embedding 由 `providers/local_cpu.py` + `services/ingestion_service.py` 内联调用完成 | 不建，provider 抽象已覆盖 |
| `providers/local_gpu.py` / `remote_api.py` | `providers/base.py` 已声明 Reranker/LLM ABC（002/005 扩展点）；GPU/远程在 006 落地 | 不建空桩，006 再实现 |
| `schemas/mcp.py` | MCP 输入输出由 `specs/001-*/contracts/*.schema.json` 校验（JSON Schema 2020-12） | 不建，契约已外置 |
| `services/evaluation_service.py` | 评测由 `eval/run_eval.py` + `eval/generate_dataset.py` 脚本承担 | 不建，脚本已覆盖 |
| `frontend/src/components/*`（5 个组件） | 逻辑内联在 `pages/ProjectsPage.tsx` 与 `pages/ProjectDetailPage.tsx` | 不拆分，页面自洽 |
| `frontend/src/hooks/useProjects.ts` | 数据获取内联在页面 + `api/` 客户端 | 不建 |
| `frontend/tests/e2e/*`（Playwright） | 未实现；端到端由 quickstart VS 场景 + MCP 实测覆盖 | 推迟到 006 或后续 |

另见 `docs/1.0-iteration-roadmap.md` 迭代 0 缺口清单（G1~G10）。

**Structure Decision**: Option 2 (Web application) — backend/ + frontend/ 分离。后端包含REST管理API和MCP服务（独立端口：管理面 8000，MCP 8080）。前端为独立SPA，开发时Vite代理到后端，生产时由后端托管构建产物。

## Complexity Tracking

> No constitution violations to justify. All gates pass.

## Phase 0: Research Summary

详见 [research.md](./research.md)。关键决策摘要：

| 决策项 | 选择 | 理由 |
|--------|------|------|
| Python框架 | FastAPI | 原生async、自动OpenAPI、SSE支持 |
| MCP SDK | mcp-sdk (Python) | 官方SDK，Streamable HTTP支持 |
| Java解析 | tree-sitter + tree-sitter-java | AST级符号感知，容错性好 |
| Markdown解析 | markdown-it-py + 自定义章节分割 | 保留标题层级和父子关系 |
| 凭据检测 | 正则模式匹配 + 类型化占位符 | 确定性、可审计、不误删字段名 |
| ID生成 | snowflake-id | 有序、高性能、分布式就绪 |
| 数据库迁移 | Alembic | SQLAlchemy生态标准 |
| 前端组件库 | Ant Design | 功能完整、TypeScript友好、适合管理后台 |
| SSE实现 | FastAPI StreamingResponse | 原生支持，无需额外依赖 |
| 评测集生成 | LLM生成 + 人工审核 | 与项目知识对齐，质量可控 |

## Phase 1: Design Artifacts

- **Data Model**: [data-model.md](./data-model.md) — 7个核心实体、状态机、索引策略
- **Common Schema**: [contracts/common.schema.json](./contracts/common.schema.json) — 共享类型定义（蓝图 §22）
- **MCP Contracts**: [contracts/](./contracts/) — search_knowledge和get_evidence的JSON Schema
- **Management API Contract**: [contracts/management-api.schema.json](./contracts/management-api.schema.json)
- **Quickstart Validation**: [quickstart.md](./quickstart.md) — 12个验收验证场景

## Post-Design Constitution Re-Check

| Principle | Status | Verification |
|-----------|--------|-------------|
| I. Explicit Knowledge Scope | ✅ PASS | data-model.md中所有实体携带knowledge_scope_id；mcp-search-input.schema.json要求project_scope必填 |
| II. Evidence Before Inference | ✅ PASS | 001纯确定性检索；mcp-search-output.schema.json每条证据含source_version和source_position；completion_status四态（含no_evidence）对齐蓝图§14 |
| III. Untrusted Content Isolation | ✅ PASS | credential_redactor.py在切片前执行；Qdrant和MCP响应不含原始凭据值 |
| IV. Deterministic Control | ✅ PASS | 无LLM参与检索流程；状态机由Python代码控制 |
| V. Versioned Knowledge | ✅ PASS | KnowledgeVersion含capabilities和status字段；检索过滤已发布版本 |
| VI. Client-Compatible MCP | ✅ PASS | structuredContent + TextContent双通道；Schema 2020-12描述 |
| VII. Evaluation-Driven | ✅ PASS | eval/目录含评测集和运行脚本；SC-002硬性指标（零串库） |

**All gates pass. No violations.**
