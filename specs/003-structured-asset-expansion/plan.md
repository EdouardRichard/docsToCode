# Implementation Plan: 003 Structured Asset Expansion

**Branch**: `003-structured-asset-expansion` | **Date**: 2026-08-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-structured-asset-expansion/spec.md`

## Summary

003 扩展 001/002 已建立的 RAG MCP 知识库系统，新增 Word、PDF、OpenAPI/Swagger、DDL、Go、Python 六种格式的格式感知结构切片能力。每种格式按独立验收批次扩展，复用 001 的解析器调度框架、凭据规范化与父子索引机制，以及 002 的 Sparse/BM25、融合与 Rerank 检索路径。003 不修改 MCP 对外契约、不新增检索能力标志、不改变检索链路。核心交付物为 6 个新格式解析器 + 扩展的评测集 + 与 001/002 基线的回归对照报告。

## Technical Context

**Language/Version**: Python 3.12（后端）、TypeScript（前端 SPA，003 仅扩展上传接受类型列表）

**Primary Dependencies**:
- 已有（001/002）: FastAPI, SQLAlchemy, Qdrant, markdown-it-py, tree-sitter-java, tree-sitter, jieba, pyyaml
- 新增: tree-sitter-go（Go AST）、python-docx（Word OOXML）、pdfplumber（PDF 文本+布局）、sqlparse（DDL 语句分割）
- 标准库: `ast`（Python 源代码解析，零外部依赖）

**Storage**: PostgreSQL（控制面元数据）、Qdrant（Dense+Sparse 向量）、本地文件系统（原始上传文件）

**Testing**: pytest（单元/集成/契约测试），沿用 001/002 测试目录结构

**Target Platform**: Windows 本机部署，loopback HTTP（未认证管理与 MCP 服务默认仅本机访问）

**Project Type**: web-service（Python 后端 REST + MCP）+ SPA（React/TypeScript 前端管理端）

**Performance Goals**: 服务端总超时 30s（沿用 001/002 护栏）；新格式解析在入库阶段执行，不阻塞检索延迟；5 并发请求隔离（沿用 001/002）

**Constraints**: 单用户、单 Writer 实例；不引入分布式协调；检索路径不变（Dense+Sparse+Rerank）；MCP 契约不变（宪法原则 VII）

**Scale/Scope**: 8 种格式（Markdown+Java 已有，新增 6 种）；评测集从 18 条扩展至 ≥ 30 条（原 18 条保留 + 新增 ≥ 12 条）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### 原则检查

| 原则 | 状态 | 说明 |
|------|------|------|
| I. 显式知识作用域 | ✅ 通过 | FR-013 继承 001 的显式 `project_scope` 要求；新格式检索 MUST 携带显式作用域 |
| II. 项目事实优先 | ✅ 通过 | 003 不修改公共知识与项目知识的分离逻辑 |
| III. 不确定即暴露 | ✅ 通过 | 003 不修改冲突暴露机制；新格式 Chunk 的来源定位可验证 |
| IV. 证据可定位 | ✅ 通过 | FR-016 要求每条证据携带来源 ID、版本与可定位位置；`format-locators.schema.json` 定义各格式位置标识校验规则 |
| V. 数据与控制分离 | ✅ 通过 | FR-012 复用 001 凭据规范化；上传的新格式材料视为不可信数据，不控制 Prompt 或状态机 |
| VI. 确定性控制优先 | ✅ 通过 | 新格式解析器为确定性组件（AST/结构遍历），不引入 LLM 判断 |
| VII. 接口独立演进 | ✅ 通过 | FR-015 不修改 MCP 对外契约；`common.schema.json` 扩展 `SourcePosition` description + 新增 `SourceFormat` 定义（均 additive，不破坏兼容，不改 Schema 结构） |
| VIII. 知识版本不可混用 | ✅ 通过 | FR-022 不新增能力标志；新格式 Chunk 在同一嵌入模型(bge-m3)与切片策略上获得索引 |
| IX. 同步结果优先 | ✅ 通过 | 003 不引入长任务扩展；新格式检索走既有同步检索路径 |
| X. 评测驱动优化 | ✅ 通过 | FR-023~FR-027 要求回归验证与逐格式评测；research.md §0 声明可度量评测目标 |

### 硬约束检查

| 硬约束 | 状态 | 说明 |
|--------|------|------|
| 跨项目泄漏必须为零 | ✅ 通过 | FR-014 保证新格式 Chunk 的 `knowledge_scope_id`/`project_id`/`index_version` 过滤与 001/002 一致 |
| 无显式 project_scope 必须拒绝 | ✅ 通过 | FR-013 继承 001 拒绝逻辑 |
| 上传内容不得作为控制指令 | ✅ 通过 | FR-012 凭据规范化复用；新格式内容仅进入证据字段 |
| MCP Schema 合法率 100% | ✅ 通过 | FR-015 新格式 Chunk 通过既有 Schema 返回 |
| 来源可定位率 100% | ✅ 通过 | FR-016 + `format-locators.schema.json` 校验 |

### 交付工作流检查

| 工作流要求 | 状态 | 说明 |
|-----------|------|------|
| 独立可测用户场景与可度量成功标准 | ✅ 通过 | 6 个用户故事（每种格式独立可测）+ 10 个成功标准 |
| 001 为首个 Feature | ✅ 通过 | 003 在 001/002 基线上扩展 |
| 澄清标记已解决 | ✅ 通过 | spec 无 [NEEDS CLARIFICATION] 标记 |
| plan.md 保留蓝图与宪法 | ✅ 通过 | 本文档引用蓝图 §23.4 第 3 项/§7/§2.1 与宪法 I–X |
| 不在 spec/plan/tasks 通过前开始实现 | ✅ 通过 | 待 tasks.md 生成后进入实现 |
| 范围扩展为新 Feature | ✅ 通过 | 003 为独立 Feature，不隐藏在 001/002 任务中 |

**Gate 结果**: 全部通过，无违规项。Complexity Tracking 表为空。

## Project Structure

### Documentation (this feature)

```text
specs/003-structured-asset-expansion/
├── spec.md              # Feature 规格（/speckit-specify 输出）
├── plan.md              # 本文件（/speckit-plan 输出）
├── research.md          # 技术研究 + 评测目标（/speckit-plan Phase 0 输出）
├── data-model.md       # 数据模型扩展（/speckit-plan Phase 1 输出）
├── quickstart.md       # 端到端验证指南（/speckit-plan Phase 1 输出）
├── contracts/          # 契约定义
│   ├── common.schema.json           # 共享类型定义（复用 001/002 + SourcePosition 扩展）
│   ├── format-locators.schema.json  # 各格式来源位置标识校验规则（003 新增）
│   └── management-api.format-extension.schema.json  # 管理 API format enum 扩展至 8 值（003 新增）
├── checklists/
│   └── requirements.md  # 规格质量检查清单
└── tasks.md             # 任务列表（/speckit-tasks 输出，已生成）
```

### Source Code (repository root)

```text
backend/
├── src/rag_mcp/
│   ├── parsers/                    # 解析器目录（003 扩展）
│   │   ├── __init__.py             # 已有
│   │   ├── markdown_parser.py      # 001 已有（不修改）
│   │   ├── java_parser.py          # 001 已有（不修改）
│   │   ├── credential_redactor.py # 001 已有（不修改）
│   │   ├── openapi_parser.py      # 003 新增：OpenAPI/Swagger 端点+Schema 切片
│   │   ├── ddl_parser.py          # 003 新增：DDL 表/字段/约束/索引/视图/存储过程切片
│   │   ├── go_parser.py           # 003 新增：Go AST 符号切片（tree-sitter-go）
│   │   ├── python_parser.py       # 003 新增：Python AST 符号切片（标准库 ast）
│   │   ├── word_parser.py         # 003 新增：Word OOXML 标题/段落/列表/表格切片
│   │   ├── pdf_parser.py          # 003 新增：PDF 标题/段落/页码切片（含多栏检测）
│   │   └── text_extractor.py      # 003 新增：二进制格式文本提取调度（Word/PDF）
│   ├── services/
│   │   └── ingestion_service.py   # 003 扩展：_parse_content 新增 6 个格式分支
│   ├── indexing/
│   │   ├── qdrant_client.py       # 不修改
│   │   └── sparse_encoder.py      # 不修改
│   └── ...                        # 其他模块不修改
├── tests/
│   ├── unit/test_parsers/          # 003 新增测试
│   │   ├── test_openapi_parser.py
│   │   ├── test_ddl_parser.py
│   │   ├── test_go_parser.py
│   │   ├── test_python_parser.py
│   │   ├── test_word_parser.py
│   │   └── test_pdf_parser.py
│   ├── integration/
│   │   └── test_format_expansion.py  # 003 新增：多格式端到端集成测试
│   └── contract/
│       └── test_format_locators.py    # 003 新增：来源位置标识契约校验
└── ...

frontend/
└── src/
    └── ...                         # 003 仅扩展上传接受的文件类型列表

eval/
├── eval_dataset.json               # 003 扩展：新增 ≥ 12 条新格式查询
├── run_eval.py                     # 003 扩展：回归/可重复性报告（T040/T051）
├── run_comparison.py               # 003 扩展：新增格式对照报告生成
├── format_expansion_report.json   # 003 新增：逐格式对照报告输出（T039）
├── regression_report.json          # 003 新增：001/002 回归验证输出（T040）
├── reproducibility_report.json     # 003 新增：评测可重复性验证输出（T051）
├── baseline_report.json           # 001 基线（不修改）
└── hybrid_comparison_report.json   # 002 基线（不修改）
```

**Structure Decision**: 沿用 001/002 已建立的 backend/frontend 双项目结构。新增文件全部在 `backend/src/rag_mcp/parsers/` 目录内（与 001 的 markdown_parser.py/java_parser.py 并列），遵循已有的解析器组织模式。前端仅扩展上传组件的接受类型列表。评测扩展在 `eval/` 目录。

## Complexity Tracking

> Constitution Check 全部通过，无违规项需证明。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |

## 设计决策汇总

### 技术选型（详见 [research.md](research.md)）

| 格式 | 解析方案 | 依赖 | 来源位置标识格式 |
|------|---------|------|----------------|
| OpenAPI | 标准库 json + pyyaml 结构遍历 | pyyaml（002 已有） | `GET /api/v1/users` |
| DDL | sqlparse 语句分割+分类 | sqlparse（新增） | `table:users` |
| Go | tree-sitter-go AST | tree-sitter-go（新增） | `pkg.Service#Method` |
| Python | 标准库 ast 模块 | 无（标准库） | `module.Class.method` |
| Word | python-docx OOXML 解析 | python-docx（新增） | `## 架构 > ### 数据流` |
| PDF | pdfplumber 文本+布局提取 | pdfplumber（新增） | `page:12 §3.2 数据流` |

### 数据模型扩展（详见 [data-model.md](data-model.md)）

- `KnowledgeSource.format`: 新增 6 个合法值（**含 DB CHECK 约束扩展 `IN ('markdown','java')` → 8 值，见 data-model.md §6.2**；001 L165/L569、002 L338 明确为 003 职责）
- `Chunk`: 新增 `structure_path`/`parent_structure_path` 键（OpenAPI/DDL 使用）
- `backfill_parent_chunk_ids()`: 扩展支持 `structure_path` 键
- `ProcessingRun.stages`: 新增 `text_extraction` 阶段（二进制格式）
- `RetrievalRun`: 新增 `format` 列 VARCHAR(8) NULLABLE（问题回溯，FR-027；见 data-model.md §3.5/§6.1，沿用 002 对 `RetrievalRun` 的列扩展模式）
- **不新增数据库表**；新增 1 列 `retrieval_runs.format`（内部控制面审计字段，不影响 MCP 对外契约，宪法原则 VII）；扩展 2 个既有 DB CHECK 约束：`knowledge_sources.format`（§6.2）、`chunks.chunk_type`（§6.3，001/002 既有 `markdown`/`java` 与 `section`/`symbol` 向后兼容）

### 契约变更（详见 [contracts/](contracts/)）

- `common.schema.json`: 复用 001/002 定义，扩展 `SourcePosition` description + 新增 `SourceFormat` 定义
- `format-locators.schema.json`: 新增，各格式来源位置标识校验规则
- `management-api.format-extension.schema.json`: 新增，将 001 管理 API 响应 `KnowledgeSourceResponse.format` 的 inline `enum:[markdown,java]` 改为引用 `SourceFormat`（8 值）—— REST 管理 API 契约扩展（非 MCP 对外契约）
- **MCP 对外契约不变**（宪法原则 VII）；REST 管理 API 契约按上项扩展（不影响 MCP 契约）

### Constitution 合规检查（post-design 重新评估）

Phase 1 设计完成后重新评估 Constitution Check：**全部通过**。设计决策未引入任何宪法原则或硬约束的违规：

- 新格式解析器为确定性组件（原则 VI）
- 新格式 Chunk 通过既有 MCP 契约返回（原则 VII）
- 新格式在同一嵌入模型上获得索引（原则 VIII）
- 评测目标已在 research.md §0 声明（原则 X）
- 跨项目泄漏、Schema 合法率、来源可定位率三条硬约束均有对应 FR 与 SC

## 实现批次建议

003 的 6 种格式按独立验收批次交付（蓝图 §23.4 第 3 项 / SC-009）。建议批次顺序（不强制）：

| 批次 | 格式 | 理由 |
|------|------|------|
| 1 | Go + Python | 沿用 001 Java 的 AST 方案，技术风险最低；Go 用 tree-sitter（与 Java 一致），Python 用标准库 ast |
| 2 | OpenAPI + DDL | 结构化定义格式，解析器逻辑清晰；OpenAPI 用 JSON/YAML 遍历，DDL 用 sqlparse |
| 3 | Word + PDF | 二进制格式，需要文本提取阶段；Word 用 python-docx，PDF 用 pdfplumber（含多栏检测） |

每批次完成后可独立验收：上传该格式材料 → 检索 → 验证来源定位与硬约束 → 记录评测指标。

## 依赖安装

```bash
pip install tree-sitter-go python-docx pdfplumber sqlparse
```

`pyyaml` 已由 002 引入，`tree-sitter` 已由 001 引入。Python `ast` 为标准库。
