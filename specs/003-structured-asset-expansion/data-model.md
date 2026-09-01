# 数据模型：003 Structured Asset Expansion

**Feature**: 003-structured-asset-expansion
**状态**: Draft
**日期**: 2026-08-28
**依据**: 系统设计蓝图 §6.2 / §7 / §8.4 / §13；Feature Spec FR-001 ~ FR-027；Constitution I–X
**基线**: 001/002 data-model.md（本文档在其上扩展，不重复已定义的完整实体，仅描述 003 变更与新增）

---

## 1. 概述

本文档定义 003 Feature 相对 001/002 的数据模型扩展。003 在 001/002 已建立的 PostgreSQL 控制面与 Qdrant 混合集合之上：

- 扩展 `KnowledgeSource.format` 字段新增 6 种格式值（`openapi`/`ddl`/`go`/`python`/`word`/`pdf`）
- 扩展 `Chunk` 来源位置标识字段（新增 `structure_path`/`parent_structure_path` 键）
- 扩展 `backfill_parent_chunk_ids()` 函数支持 `structure_path` 键
- 扩展 `ProcessingRun.stages` 新增 `text_extraction` 阶段（仅二进制格式）
- 扩展 `RetrievalRun` 记录格式类型

**不改 001/002 已确立的对外 MCP 契约**（FR-015 / 宪法原则 VII）。
**不新增数据库表**；新增 1 列 `retrieval_runs.format`（沿用 002 对 `RetrievalRun` 的列扩展模式，内部控制面审计字段，不出现在 MCP 对外契约，宪法原则 VII）。其余扩展均复用既有列：`KnowledgeSource.format` 已是 string 类型、`chunk_type` 已是 string 类型、Chunk 来源位置标识通过既有 JSONB/string 字段承载。

---

## 2. 实体关系图（增量）

```
001/002 既有实体（不变）       003 扩展点
──────────────────────────    ─────────────────────────────────────────
KnowledgeSource    ──────────► format 新增 openapi/ddl/go/python/word/pdf
Chunk              ──────────► 新增 structure_path / parent_structure_path 键
                                chunk_type 新增 endpoint/schema/table/column/
                                constraint/index/view/procedure/function/method/
                                type/interface/class/heading/paragraph/list
                                （跨格式同名 chunk_type 如 DDL 与 Word 的 `table`，
                                由 KnowledgeSource.format 消歧，见 §6.3）
ProcessingRun      ──────────► stages 新增 text_extraction（二进制格式）
RetrievalRun       ──────────► 新增 format 列 VARCHAR(8) NULLABLE（记录命中证据格式，§3.5/§6.1）

001/002 既有函数（扩展）       003 扩展点
──────────────────────────    ─────────────────────────────────────────
backfill_parent_chunk_ids()  ─► 新增 structure_path 键支持
_parse_content()              ─► 新增 6 个格式分支
```

---

## 3. 实体扩展定义

### 3.1 KnowledgeSource（扩展 format 字段）

001 已定义 `KnowledgeSource` 模型。003 扩展 `format` 字段的合法值集合。

**字段**: `format: str`（已有，VARCHAR(16)；**001/002 带 DB CHECK 约束 `format IN ('markdown','java')`**——001 data-model L165/L569、002 data-model L338 明确该 CHECK 扩展为 003 职责；003 MUST 扩展至 8 值，见 §6.2）

**001/002 合法值**: `markdown`, `java`

**003 新增合法值**:

| format 值 | 文件扩展名 | 检测方式 | 说明 |
|-----------|-----------|---------|------|
| `openapi` | `.json`, `.yaml`, `.yml` | 内容检测：含 `openapi` 或 `swagger` 版本字段 | OpenAPI 3.x 或 Swagger 2.0 |
| `ddl` | `.sql` | 扩展名 | ANSI SQL DDL |
| `go` | `.go` | 扩展名 | Go 源代码 |
| `python` | `.py` | 扩展名 | Python 源代码 |
| `word` | `.docx` | 扩展名 | OOXML Word 文档 |
| `pdf` | `.pdf` | 扩展名 | 文本版 PDF |

**验证规则**: 上传时先按扩展名预判，再按内容验证（FR-010）。`.json`/`.yaml`/`.yml` 扩展名的文件需检查内容是否为 OpenAPI/Swagger 规范；非 OpenAPI 的 JSON/YAML 文件拒绝处理并说明原因。

**不变项**: `knowledge_scope_id`, `source_id`, `content_hash`, `filename`, `size_bytes`, `status`, `processing_error`, `created_at`, `updated_at` — 全部不变。

### 3.2 Chunk（扩展来源位置标识字段）

001 已定义 `Chunk` 模型。003 扩展 Chunk 的来源位置标识字段键集合。

**001 已有字段键**:
- `section_path` / `parent_section_path`（Markdown 使用）
- `symbol_path` / `parent_symbol_path`（Java 使用）
- `content_text`, `start_line`, `end_line`, `token_count`, `chunk_type`, `chunk_id`, `parent_chunk_id`

**003 新增字段键**:
- `structure_path` / `parent_structure_path`（OpenAPI/DDL 使用）

**格式到字段键映射**:

| 格式 | 位置路径字段 | 父级路径字段 | 来源位置标识格式 |
|------|------------|------------|----------------|
| Markdown | `section_path` | `parent_section_path` | `## 安装 > ### 配置` |
| Java | `symbol_path` | `parent_symbol_path` | `com.example.Service#methodName` |
| OpenAPI | `structure_path` | `parent_structure_path` | `GET /api/v1/users` |
| DDL | `structure_path` | `parent_structure_path` | `table:users` |
| Go | `symbol_path` | `parent_symbol_path` | `pkg.Service#Method` |
| Python | `symbol_path` | `parent_symbol_path` | `module.Class.method` |
| Word | `section_path` | `parent_section_path` | `## 架构 > ### 数据流` |
| PDF | `section_path` | `parent_section_path` | `page:12 §3.2 数据流` |

**chunk_type 新增值**:

| 格式 | chunk_type 值 |
|------|--------------|
| OpenAPI | `endpoint`, `schema` |
| DDL | `table`, `column`, `constraint`, `index`, `view`, `procedure` |
| Go | `function`, `method`, `type`, `interface` |
| Python | `function`, `class`, `method` |
| Word | `heading`, `paragraph`, `list`, `table` |
| PDF | `heading`, `paragraph` |

**验证规则**:
- 每个 Chunk MUST 有且仅有一个位置路径字段（`section_path` 或 `symbol_path` 或 `structure_path`），不得为空。
- 父级路径字段可为空（顶级结构单元无父级）。
- `chunk_type` MUST 为合法值之一。

### 3.3 backfill_parent_chunk_ids() 函数扩展

001 已定义 `backfill_parent_chunk_ids(chunk_dicts)` 函数，通过两遍遍历构建 `parent_chunk_id`。

**001 实现**: 支持 `section_path` 和 `symbol_path` 两种键。

**003 扩展**: 新增 `structure_path` 作为第三种键。

**扩展后逻辑**:
```python
position_path = (
    chunk.get("section_path")
    or chunk.get("symbol_path")
    or chunk.get("structure_path")  # 003 新增
    or ""
)
parent_path = (
    chunk.get("parent_section_path")
    or chunk.get("parent_symbol_path")
    or chunk.get("parent_structure_path")  # 003 新增
    or ""
)
```

**验证规则**: 父级路径为空、不可解析或等于自身路径时，不设置 `parent_chunk_id`（沿用 001 行为）。

### 3.4 ProcessingRun.stages 扩展

001/002 已定义 `ProcessingRun.stages` JSONB 数组。003 新增 `text_extraction` 阶段。

**002 已有阶段**:
1. `credential_scan` — 凭据规范化
2. `parsing` — 格式感知切片
3. `chunking` — Chunk ID 分配与父子回填
4. `embedding` — Dense 嵌入
5. `sparse_index` — Sparse/BM25 词法索引

**003 新增阶段**:

| 阶段 | 适用格式 | 位置 | 说明 |
|------|---------|------|------|
| `text_extraction` | Word, PDF | `credential_scan` 之前 | 从二进制格式提取文本内容；纯文本格式跳过此阶段 |

**纯文本格式流程**: `credential_scan` → `parsing` → `chunking` → `embedding` → `sparse_index`

**二进制格式流程**: `text_extraction` → `credential_scan` → `parsing` → `chunking` → `embedding` → `sparse_index`

**阶段记录格式**（沿用 001/002）:
```json
{
  "stage": "text_extraction",
  "status": "completed",
  "started_at": "2026-08-28T...",
  "completed_at": "2026-08-28T...",
  "details": {"pages": 12, "columns_detected": true}
}
```

### 3.5 RetrievalRun 扩展

001/002 已定义 `RetrievalRun` 记录检索运行状态。003 新增 `format` 列记录检索命中的格式类型（FR-027）。

**新增列**:

| 列 | 类型 | 约束 | 描述 |
|------|------|------|------|
| `format` | VARCHAR(8) | NULLABLE, CHECK IN ('markdown','java','openapi','ddl','go','python','word','pdf') 或 NULL | 命中证据所属知识源的格式类型；无命中时为 NULL |

**存储方式**: `format` 为 `retrieval_runs` 表上的新增独立列，沿用 002 对 `RetrievalRun` 的列扩展模式（如 `retrieval_mode`）。**不复用** `evidence_ref_ids` / `subpath_timings` 等 JSONB 列（语义不符）。该列为内部控制面审计字段，**不出现在 MCP 对外契约**（`search_knowledge` / `get_evidence` 响应不变，宪法原则 VII）。

**迁移**: 通过 Alembic 迁移新增该列（见 §6.1）。`format` 为 NULLABLE，001/002 既有记录默认 NULL，向后兼容。

**验证规则**: `format` 取排名最高（top-1）返回证据所属知识源的 format 值；无命中时为 NULL。检索同时命中多种格式时仅记录 top-1 证据的格式，多格式命中明细可经既有 `evidence_ref_ids` 追溯。

---

## 4. 不变项汇总

以下 001/002 实体与字段在 003 中**不修改**：

| 实体/字段 | 不变原因 |
|-----------|---------|
| `KnowledgeScope` 模型 | 003 不新增知识域类型 |
| `Project` 模型 | 003 不修改项目结构 |
| `KnowledgeVersion` 模型 + `capabilities` | 003 不新增能力标志（FR-022） |
| `KnowledgeSource` 除 format 外所有字段 | 003 只扩展 format 合法值（含 DB CHECK 扩展，见 §6.2） |
| `Chunk` 数据库表结构 | 003 不新增列（来源位置标识通过现有 JSONB/string 字段承载）；`chunk_type` 的 DB CHECK 约束扩展见 §6.3 |
| Qdrant 集合结构 | 003 不修改 Dense/Sparse 向量结构 |
| MCP `search_knowledge` / `get_evidence` 契约 | 宪法原则 VII |
| `completion_status` 四态 | 蓝图 §14 |
| 凭据规范化逻辑 | 001 已实现，003 复用 |
| Dense/Sparse/Rerank 检索路径 | 001/002 已实现，003 复用 |

---

## 5. 来源位置标识验证规则

### 5.1 校验规则（用于验收测试集，硬约束 SC-006）

每个返回的证据 MUST 携带一个 `source_position` 字符串，该字符串 MUST 匹配以下格式之一（按知识源 format 分类）：

| format | source_position 正则 | 示例 |
|--------|---------------------|------|
| markdown | `^#{1,6} .+(?: > #{1,6} .+)*$` | `## 安装 > ### 配置` |
| java | `^[a-z][\w.]*(?:#[A-Za-z_]\w*)?$` | `com.example.Service` 或 `com.example.Service#methodName` |
| openapi (endpoint) | `^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) /.+$` | `GET /api/v1/users` |
| openapi (schema) | `^schema:(components\.schemas|definitions)\..+$` | `schema:components.schemas.User`（3.x）/ `schema:definitions.User`（Swagger 2.0） |
| ddl (table) | `^table:[a-zA-Z_][\w]*$` | `table:users` |
| ddl (column) | `^table:[a-zA-Z_][\w]*\.column:[a-zA-Z_][\w]*$` | `table:users.column:email` |
| ddl (constraint) | `^constraint:[a-zA-Z_][\w]*$` | `constraint:pk_users` |
| ddl (index) | `^index:[a-zA-Z_][\w]*$` | `index:idx_users_email` |
| ddl (view) | `^view:[a-zA-Z_][\w]*$` | `view:active_users` |
| ddl (procedure) | `^procedure:[a-zA-Z_][\w]*$` | `procedure:calculate_stats` |
| go (function) | `^[a-z_][\w]*\.[A-Za-z_]\w*$` | `pkg.ProcessData` |
| go (method) | `^[a-z_][\w]*\.[A-Za-z_]\w*#[A-Za-z_]\w*$` | `pkg.Service#Method` |
| go (type) | `^[a-z_][\w]*\.[A-Za-z_]\w*$` | `pkg.Service`（与 function 同 pattern，由 chunk_type 区分） |
| go (interface) | `^[a-z_][\w]*\.[A-Za-z_]\w*$` | `pkg.Reader`（与 function/type 同 pattern，由 chunk_type 区分） |
| python (function) | `^[a-z_][\w]*(\.[a-z_]\w*)+$` | `utils.parse_config`（嵌套函数 `utils.outer.inner`） |
| python (class) | `^[a-z_][\w]*(\.[A-Z][\w]*)+$` | `models.User`（嵌套类 `models.Outer.Inner`） |
| python (method) | `^[a-z_][\w]*(\.[A-Z][\w]*)+\.[a-z_]\w*$` | `models.User.validate`（嵌套类方法 `models.Outer.Inner.validate`） |
| word | `^#{1,6} .+(?: > #{1,6} .+)*$` | `## 架构 > ### 数据流` |
| pdf | `^page:\d+(?: §.+)?$` | `page:12 §3.2 数据流` |

### 5.2 Swagger 2.0 兼容位置标识

Swagger 2.0 的 Schema 定义位于 `definitions` 而非 `components.schemas`。来源位置标识使用 `schema:definitions.{name}` 格式以区分。解析器检测规范版本后选择对应路径。

---

## 6. PostgreSQL DDL 变更

### 6.1 RetrievalRun 表扩展（ALTER）

```sql
-- 003 扩展：检索命中格式类型（问题回溯，FR-027）
ALTER TABLE retrieval_runs
    ADD COLUMN format VARCHAR(8)
        CHECK (format IS NULL OR format IN ('markdown','java','openapi','ddl','go','python','word','pdf'));
```

**兼容性**: `format` 为 NULLABLE，001/002 既有记录默认 NULL，向后兼容。本列仅用于内部控制面审计，不出现在 MCP 对外契约（宪法原则 VII）。

### 6.2 KnowledgeSource.format CHECK 约束扩展（ALTER）

001/002 的 `knowledge_sources.format` 列带 DB CHECK 约束 `CHECK (format IN ('markdown','java'))`（001 data-model L165/L569；002 data-model L338 标注"format CHECK 不变，属 003"）。003 新增 6 格式后 MUST 扩展该 CHECK，否则任何新格式知识源落库即违反约束、入库失败。

```sql
-- 003 扩展：knowledge_sources.format CHECK 由 ('markdown','java') 扩展至 8 值
-- 001/002 原约束为未命名 CHECK，PostgreSQL 自动命名为 knowledge_sources_format_check
ALTER TABLE knowledge_sources
    DROP CONSTRAINT IF EXISTS knowledge_sources_format_check,
    ADD CONSTRAINT knowledge_sources_format_check
        CHECK (format IN ('markdown','java','openapi','ddl','go','python','word','pdf'));
```

**兼容性**: 001/002 既有 `markdown`/`java` 记录在新 CHECK 下仍合法。约束名取 PostgreSQL 自动命名约定（`<表>_<列>_check`），`DROP CONSTRAINT IF EXISTS` 保证幂等与回退安全。

### 6.3 chunks.chunk_type CHECK 约束扩展（ALTER）

001 的 `chunks.chunk_type` 列带 DB CHECK 约束 `CHECK (chunk_type IN ('section','symbol'))`（001 data-model L223/L598）。003 引入新 `chunk_type` 值（见 §3.2）后 MUST 扩展该 CHECK，否则任何新格式 Chunk 落库即违反约束，FR-001~006 全部不可实现。

```sql
-- 003 扩展：chunks.chunk_type CHECK 由 ('section','symbol') 扩展至全部合法值（去重 18 个）
-- 001 原约束为未命名 CHECK，PostgreSQL 自动命名为 chunks_chunk_type_check
-- 跨格式同名 chunk_type（table 用于 DDL/Word，function/method 用于 Go/Python，
-- heading/paragraph 用于 Word/PDF）由 KnowledgeSource.format 消歧（见 §3.2）
ALTER TABLE chunks
    DROP CONSTRAINT IF EXISTS chunks_chunk_type_check,
    ADD CONSTRAINT chunks_chunk_type_check
        CHECK (chunk_type IN (
            'section','symbol',
            'endpoint','schema',
            'table','column','constraint','index','view','procedure',
            'function','method','type','interface','class',
            'heading','paragraph','list'
        ));
```

**兼容性**: 001/002 既有 `section`/`symbol` Chunk 在新 CHECK 下仍合法。`chunk_type` 列宽 VARCHAR(16) 已能容纳最长值（`constraint`/`interface` 各 10/9 字符），无需扩列。约束名取自动命名约定，`DROP CONSTRAINT IF EXISTS` 保证幂等与回退安全。

**迁移归属**: §6.2 与 §6.3 由同一 Alembic 迁移文件执行（见 tasks.md T052）；`alembic upgrade head` 成功且 001/002 既有记录向后兼容，`alembic downgrade` 可回退至原约束。
