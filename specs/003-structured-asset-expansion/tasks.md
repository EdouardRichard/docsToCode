---
description: "Task list for 003 Structured Asset Expansion feature implementation"
---

# Tasks: 003 Structured Asset Expansion

**Input**: Design documents from `specs/003-structured-asset-expansion/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: TDD 模式——每个功能任务拆分为 Red（编写失败测试）+ Green（实现使测试通过）。

**Organization**: 任务按 User Story 分组，按优先级 P1 → P2 从上至下排序。每个任务标注 [P]（可并行）或串行依赖。

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: 可并行（不同文件，无未完成依赖）
- **[Story]**: 所属用户故事（US1~US6）
- 每个任务包含 [路径]（代码/测试文件）和 [AC]（验收标准）

## Path Conventions

- **后端**: `backend/src/rag_mcp/`、`backend/tests/`
- **评测**: `eval/`
- **前端**: `frontend/src/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 安装新增依赖与创建样例语料

- [X] T001 安装 003 新增 Python 依赖
  - [路径] `backend/pyproject.toml`（或 `requirements.txt`）
  - [AC] `pip install tree-sitter-go python-docx pdfplumber sqlparse` 成功；`python -c "import tree_sitter_go, docx, pdfplumber, sqlparse"` 无异常

- [X] T002 [P] 创建 6 种格式的样例语料目录与文件
  - [路径] `backend/tests/fixtures/samples/{openapi.json, openapi.yaml, swagger.json, malformed.openapi.json, schema.sql, unsupported_dialect.sql, service.go, malformed.go, mismatched.go, module.py, malformed.py, design.docx, empty.docx, corrupt.docx, paper.pdf, corrupt.pdf, scanned.pdf}`
  - [AC] 样例文件存在；正常用例非空（失败用例可为空/损坏）；OpenAPI（JSON+YAML）各含 ≥ 2 端点 + ≥ 1 Schema；Swagger 2.0 含 ≥ 2 端点（definitions Schema）；`malformed.openapi.json` 为 OpenAPI 形态但不合规（缺 `openapi`/`swagger` 版本字段或断 `$ref`）；DDL 含 ≥ 2 表 + ≥ 1 命名约束（PK/FK），`unsupported_dialect.sql` 含私有方言特性；Go 含包声明+结构体+方法，`malformed.go` 含语法错误，`mismatched.go` 为 `.go` 扩展名但内容为 Python 代码（扩展名/内容不匹配用例，区别于语法错误的 `malformed.go`）；Python 含类+方法+嵌套（含嵌套类与嵌套函数），`malformed.py` 含语法错误；Word 含多级标题+表格，`empty.docx` 为空，`corrupt.docx` 为损坏 OOXML；`paper.pdf` 含多栏布局，`corrupt.pdf` 为损坏非扫描 PDF，`scanned.pdf` 为纯图像无可提取文本层

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 扩展 001/002 框架以支持新格式接入——所有用户故事的前提

**⚠️ CRITICAL**: 用户故事阶段在此阶段完成前不得开始

- [X] T052 扩展 DB CHECK 约束：`knowledge_sources.format` + `chunks.chunk_type`（FR-001~006/FR-010 落库前置，阻塞所有新格式落库）
  - [路径] `backend/alembic/versions/*_expand_format_and_chunk_type_check.py`（新增迁移）+ `backend/src/rag_mcp/models/`（确认 ORM 模型与扩展后 CHECK 一致）
  - [AC] Alembic 迁移执行 `ALTER TABLE knowledge_sources DROP CONSTRAINT IF EXISTS knowledge_sources_format_check` 后 `ADD CONSTRAINT knowledge_sources_format_check CHECK (format IN ('markdown','java','openapi','ddl','go','python','word','pdf'))`；执行 `ALTER TABLE chunks DROP CONSTRAINT IF EXISTS chunks_chunk_type_check` 后 `ADD CONSTRAINT chunks_chunk_type_check CHECK (chunk_type IN ('section','symbol','endpoint','schema','table','column','constraint','index','view','procedure','function','method','type','interface','class','heading','paragraph','list'))`（data-model.md §6.2/§6.3）；`alembic upgrade head` 成功；001/002 既有 `markdown`/`java` 知识源与 `section`/`symbol` Chunk 在新 CHECK 下仍合法（向后兼容）；`alembic downgrade` 可回退至原约束。**阻塞依据**：001 data-model L165/L223、002 data-model L338 明确该 CHECK 扩展为 003 职责；未迁移则 T003 落库 `format` 字段与各格式解析器落库 `chunk_type` 均违反 CHECK 而失败

- [X] T053 [P] 扩展管理 API 响应契约 `format` enum 至 8 值（FR-010/SC-001 上传响应前置）
  - [路径] `specs/003-structured-asset-expansion/contracts/management-api.format-extension.schema.json`（003 新增）+ `backend/` 管理 API 响应校验（将 001 inline `enum:[markdown,java]` 改为引用 `common.schema.json#/definitions/SourceFormat`）
  - [AC] 上传 openapi/ddl/go/python/word/pdf 文件后管理 API `KnowledgeSourceResponse.format` 返回对应值并通过契约校验（不再被 001 的 `enum:[markdown,java]` 拒绝）；`format` 字段引用 `SourceFormat`（8 值，common.schema.json）；该契约为 REST 管理 API 契约（非 MCP 对外契约，不违反宪法原则 VII，data-model.md §3.1/plan.md §契约变更）

- [X] T003 扩展格式检测逻辑，支持 6 种新格式
  - [路径] `backend/src/rag_mcp/services/ingestion_service.py`（`_parse_content` 方法及格式检测逻辑）
  - [AC] 上传 `.go`/`.py`/`.sql`/`.json`/`.yaml`/`.yml`/`.docx`/`.pdf` 文件时 `format` 字段正确设置；`.json`/`.yaml`/`.yml` 非 OpenAPI 规范时拒绝并说明原因（FR-010）；上传 `mismatched.go`（扩展名与内容不匹配）时检测格式不匹配并报告失败，不得静默按扩展名切片（spec 边缘案例）

- [X] T004 扩展 `backfill_parent_chunk_ids()` 支持 `structure_path` 键
  - [路径] `backend/src/rag_mcp/services/ingestion_service.py`（`backfill_parent_chunk_ids` 函数）
  - [AC] 函数三键兼容：`section_path`/`symbol_path`/`structure_path`；`parent_structure_path` 正确解析为 `parent_chunk_id`（data-model.md §3.3）

- [X] T005 [P] 创建 `text_extractor.py` 二进制格式文本提取调度框架
  - [路径] `backend/src/rag_mcp/parsers/text_extractor.py`
  - [AC] 提供 `extract_text(raw_bytes, fmt) -> str` 接口；Word/PDF 格式调用对应提取器；纯文本格式直接返回；提取失败时抛出带原因的异常（FR-011）

**Checkpoint**: 框架扩展完成，用户故事实现可以开始

---

## Phase 3: User Story 1 - OpenAPI/Swagger 接口定义按端点检索 (Priority: P1) 🎯 MVP

**Goal**: 上传 OpenAPI/Swagger 规范文件，按端点（Endpoint）和 Schema 定义结构切片，Agent 可通过端点路径精确检索

**Independent Test**: 上传含多端点+Schema 的 OpenAPI 文件，验证按端点切分 Chunk，Agent 通过 `GET /api/v1/users` 检索能定位到正确端点定义及来源版本与位置

### Red: 编写失败测试

- [X] T006 [P] [US1] Red: 编写 OpenAPI 解析器单元测试
  - [路径] `backend/tests/unit/test_parsers/test_openapi_parser.py`
  - [AC] `pytest` 运行失败（ImportError: No module named 'openapi_parser'）；测试覆盖端点切片（`chunk_type=endpoint`）、Schema 切片（`chunk_type=schema`）、`structure_path` 格式（`GET /api/v1/users`）、$ref 父子关系（端点=子、被引用 Schema=父；多引用取请求体主引用，research.md §4.2；FR-001）

- [X] T007 [P] [US1] Red: 编写 OpenAPI 来源位置标识契约测试
  - [路径] `backend/tests/contract/test_format_locators.py`（OpenAPI endpoint/schema 分支）
  - [AC] `pytest` 运行失败（契约文件或校验逻辑未实现）；验证 `source_position` 匹配 `format-locators.schema.json` 中 OpenAPI endpoint 和 schema 的 pattern

### Green: 实现功能使测试通过

- [X] T008 [US1] Green: 实现 `openapi_parser.py` 端点+Schema 切片
  - [路径] `backend/src/rag_mcp/parsers/openapi_parser.py`
  - [AC] T006 测试全部通过；解析 JSON/YAML OpenAPI 文件，按 `paths.{path}.{method}` 生成端点 Chunk，按 `components.schemas.{name}`（或 Swagger 2.0 `definitions.{name}`）生成 Schema Chunk；每条 Chunk 携带 `structure_path` 与 `parent_structure_path`（端点的 `parent_structure_path` 指向主引用 Schema：请求体 `$ref` 优先、无则取响应首个引用；Schema 的 `parent_structure_path` 为空，research.md §4.2）；Swagger 2.0 与 OpenAPI 3.x 均支持（research.md §1.1）；超长结构单元在自然边界处二次切分，Chunk 目标长度约 512–1024 Token，不使用统一 Token 切片覆盖全部材料（FR-007）

- [X] T009 [US1] Green: 将 OpenAPI 格式接入 `_parse_content` 调度
  - [路径] `backend/src/rag_mcp/services/ingestion_service.py`（`_parse_content` 方法）
  - [AC] `_parse_content("...", "openapi", "api.json")` 返回 Chunk 列表；T007 契约测试通过；入库流程完整执行（credential_scan → parsing → chunking → embedding → sparse_index）

- [X] T010 [US1] Green: OpenAPI 端到端集成测试
  - [路径] `backend/tests/integration/test_format_expansion.py`（OpenAPI 测试用例）
  - [AC] 上传 OpenAPI 样例文件 → 处理完成 → `search_knowledge` 查询端点路径 → 返回证据 `source_position` 匹配 `GET /api/v1/users`；`completion_status=complete`；跨项目隔离验证通过；Schema 定义与引用端点经 `$ref` 父子关系可关联（Schema 为父、端点为子，US1-AC2）；端点 Chunk 作为子 Chunk 命中时可经父级 Schema 上下文恢复端点完整定义并保留 Schema 引用关系（US1-AC3/FR-008）

**Checkpoint**: US1 OpenAPI 独立可验收

---

## Phase 4: User Story 2 - DDL 数据结构定义按表/字段检索 (Priority: P1)

**Goal**: 上传 SQL DDL 文件，按表/字段/约束/索引/视图/存储过程切片，DML 不索引，Agent 可通过表名精确检索

**Independent Test**: 上传含多表+约束的 DDL 文件，验证按表切分 Chunk，Agent 通过表名检索能定位到正确表定义

### Red: 编写失败测试

- [X] T011 [P] [US2] Red: 编写 DDL 解析器单元测试
  - [路径] `backend/tests/unit/test_parsers/test_ddl_parser.py`
  - [AC] `pytest` 运行失败（ImportError）；测试覆盖表切片（`chunk_type=table`）、字段切片（`chunk_type=column`）、命名表级约束切片（`chunk_type=constraint`）、索引/视图/存储过程切片（`chunk_type=index`/`view`/`procedure`）、`structure_path` 格式（`table:users`）、列级约束（NOT NULL/DEFAULT）作为字段 Chunk 属性不独立成 Chunk、DML 不产生 Chunk（澄清 Q1）、父子关系（FR-002）

- [X] T012 [P] [US2] Red: 编写 DDL 来源位置标识契约测试
  - [路径] `backend/tests/contract/test_format_locators.py`（DDL table/column/index/view/procedure 分支）
  - [AC] `pytest` 运行失败；验证 `source_position` 匹配 `table:users`、`table:users.column:email`、`constraint:pk_users`、`index:idx`、`view:name`、`procedure:name` 的 pattern

### Green: 实现功能使测试通过

- [X] T013 [US2] Green: 实现 `ddl_parser.py` 表/字段切片
  - [路径] `backend/src/rag_mcp/parsers/ddl_parser.py`
  - [AC] T011 测试全部通过；使用 `sqlparse` 分割 SQL 语句，仅 DDL 语句（CREATE TABLE/INDEX/VIEW/PROCEDURE、ALTER TABLE）产生 Chunk；ALTER TABLE 不产生独立 chunk_type，其字段/约束效果拆解为 `column`/`constraint` Chunk 并归属目标表 `table:{name}`（research.md §1.2 ALTER TABLE 处理）；DML（INSERT/UPDATE/DELETE）标注为未识别但不产生 Chunk；每条 Chunk 携带 `structure_path` 与 `parent_structure_path`（research.md §1.2）；超长结构单元在自然边界处二次切分，Chunk 目标长度约 512–1024 Token，不使用统一 Token 切片覆盖全部材料（FR-007）

- [X] T014 [US2] Green: 将 DDL 格式接入 `_parse_content` 调度
  - [路径] `backend/src/rag_mcp/services/ingestion_service.py`（`_parse_content` 方法）
  - [AC] `_parse_content("...", "ddl", "schema.sql")` 返回 Chunk 列表；T012 契约测试通过；含 DML 的 .sql 文件中 DML 不产生 Chunk

- [X] T015 [US2] Green: DDL 端到端集成测试（验证 DML 不索引）
  - [路径] `backend/tests/integration/test_format_expansion.py`（DDL 测试用例）
  - [AC] 上传含 DDL+DML 的 .sql 文件 → 处理完成 → DDL 语句产生 Chunk，DML 不产生 → `search_knowledge` 查询表名 → 返回证据 `source_position` 匹配 `table:users`；字段 Chunk 命中时可经父级上下文恢复所在表的完整定义（US2-AC2/FR-008）；`search_knowledge` 查询视图名/存储过程名 → 返回对应定义 Chunk（US2-AC3）；命名表级约束产生独立 `constraint` Chunk 并携带 `constraint:{name}` 来源位置标识（FR-002）

**Checkpoint**: US2 DDL 独立可验收

---

## Phase 5: User Story 3 - Go 源代码按符号检索 (Priority: P1)

**Goal**: 上传 Go 源代码，使用 tree-sitter-go AST 按包/类型/函数/方法/接口切片，Agent 可通过全限定符号路径检索

**Independent Test**: 上传含包声明+结构体+方法的 Go 文件，验证按符号切分 Chunk，Agent 通过 `pkg.Service#Method` 检索能定位到方法实现

### Red: 编写失败测试

- [X] T016 [P] [US3] Red: 编写 Go 解析器单元测试
  - [路径] `backend/tests/unit/test_parsers/test_go_parser.py`
  - [AC] `pytest` 运行失败（ImportError）；测试覆盖函数切片（`chunk_type=function`）、方法切片（`chunk_type=method`）、类型切片（`chunk_type=type`）、接口切片（`chunk_type=interface`）、`symbol_path` 格式（`pkg.Service#Method`）、AST 降级（语法错误时报错不伪造符号边界）、父子关系（FR-003/FR-017）

- [X] T017 [P] [US3] Red: 编写 Go 来源位置标识契约测试
  - [路径] `backend/tests/contract/test_format_locators.py`（Go function/method/type/interface 分支）
  - [AC] `pytest` 运行失败；验证 `source_position` 匹配 `pkg.ProcessData`（function）、`pkg.Service`（type/interface）和 `pkg.Service#Method`（method）的 pattern

### Green: 实现功能使测试通过

- [X] T018 [US3] Green: 实现 `go_parser.py` AST 符号切片
  - [路径] `backend/src/rag_mcp/parsers/go_parser.py`
  - [AC] T016 测试全部通过；使用 `tree_sitter_go` + `tree_sitter` 解析 Go AST，按包声明提取 package 名，按 function/method/type/interface 节点切分 Chunk；AST 失败时降级或报错（沿用 001 Java 降级策略）；`symbol_path` 格式为 `pkg.Service#Method`（research.md §1.3）；超长结构单元在自然边界处二次切分，Chunk 目标长度约 512–1024 Token，不使用统一 Token 切片覆盖全部材料（FR-007）

- [X] T019 [US3] Green: 将 Go 格式接入 `_parse_content` 调度
  - [路径] `backend/src/rag_mcp/services/ingestion_service.py`（`_parse_content` 方法）
  - [AC] `_parse_content("...", "go", "service.go")` 返回 Chunk 列表；T017 契约测试通过

- [X] T020 [US3] Green: Go 端到端集成测试（验证 AST 降级）
  - [路径] `backend/tests/integration/test_format_expansion.py`（Go 测试用例）
  - [AC] 上传正常 Go 文件 → 符号切片成功 → `search_knowledge` 查询 `pkg.Service#Method` → 返回证据；上传语法错误 Go 文件 → 系统报告降级或失败，不伪造符号边界；接口声明 Chunk 与分属不同文件的实现方法 Chunk 经符号路径可关联（US3-AC3/FR-008）

**Checkpoint**: US3 Go 独立可验收

---

## Phase 6: User Story 4 - Python 源代码按符号检索 (Priority: P1)

**Goal**: 上传 Python 源代码，使用标准库 ast 按模块/类/函数/方法切片，Agent 可通过全限定符号路径检索

**Independent Test**: 上传含模块级函数+类+方法的 Python 文件，验证按符号切分 Chunk，Agent 通过 `module.Class.method` 检索能定位到方法实现

### Red: 编写失败测试

- [X] T021 [P] [US4] Red: 编写 Python 解析器单元测试
  - [路径] `backend/tests/unit/test_parsers/test_python_parser.py`
  - [AC] `pytest` 运行失败（ImportError）；测试覆盖函数切片（`chunk_type=function`）、类切片（`chunk_type=class`）、方法切片（`chunk_type=method`）、`symbol_path` 格式（`module.Class.method`，含嵌套类 `module.Outer.Inner` 与嵌套函数 `module.outer.inner`）、嵌套符号父子关系、装饰器与类型注解保留（FR-004）；AST 语法错误降级——`malformed.py` 报错且不伪造符号边界（FR-017）

- [X] T022 [P] [US4] Red: 编写 Python 来源位置标识契约测试
  - [路径] `backend/tests/contract/test_format_locators.py`（Python function/class/method 分支）
  - [AC] `pytest` 运行失败；验证 `source_position` 匹配 `utils.parse_config`（function）、`models.User`（class）、`models.User.validate`（method）、`models.Outer.Inner`（嵌套类）、`models.Outer.Inner.validate`（嵌套类方法）、`utils.outer.inner`（嵌套函数）的 pattern（嵌套形式定义见 data-model.md §5.1）

### Green: 实现功能使测试通过

- [X] T023 [US4] Green: 实现 `python_parser.py` AST 符号切片
  - [路径] `backend/src/rag_mcp/parsers/python_parser.py`
  - [AC] T021 测试全部通过；使用 Python 标准库 `ast` 模块解析 AST，按 module/class/function/method 节点切分 Chunk；模块名从文件名提取；嵌套类/函数按层次切分；装饰器与类型注解保留在 Chunk 内容中；`ast.parse` 抛出 SyntaxError 时报错（research.md §1.4）；超长结构单元在自然边界处二次切分，Chunk 目标长度约 512–1024 Token，不使用统一 Token 切片覆盖全部材料（FR-007）

- [X] T024 [US4] Green: 将 Python 格式接入 `_parse_content` 调度
  - [路径] `backend/src/rag_mcp/services/ingestion_service.py`（`_parse_content` 方法）
  - [AC] `_parse_content("...", "python", "module.py")` 返回 Chunk 列表；T022 契约测试通过

- [X] T025 [US4] Green: Python 端到端集成测试（验证嵌套符号与 AST 降级）
  - [路径] `backend/tests/integration/test_format_expansion.py`（Python 测试用例）
  - [AC] 上传含嵌套类+方法的 Python 文件 → 符号切片成功 → `search_knowledge` 查询 `module.Class.method` → 返回证据；嵌套父子关系正确；上传 `malformed.py`（语法错误）→ 系统报告降级或失败并说明原因，不伪造符号边界（FR-017/SC-008，对齐 Go 的 T020 malformed.go 降级用例）

**Checkpoint**: US4 Python 独立可验收

---

## Phase 7: User Story 5 - Word 文档按标题/段落检索 (Priority: P2)

**Goal**: 上传 Word .docx 文档，使用 python-docx 按标题/段落/列表/表格切片，Agent 可通过标题路径检索

**Independent Test**: 上传含多级标题+段落+表格的 Word 文档，验证按标题层次切分 Chunk，Agent 通过标题路径检索能定位到正确章节内容

### Red: 编写失败测试

- [X] T026 [P] [US5] Red: 编写 Word 解析器单元测试
  - [路径] `backend/tests/unit/test_parsers/test_word_parser.py`
  - [AC] `pytest` 运行失败（ImportError）；测试覆盖标题切片（`chunk_type=heading`）、段落切片（`chunk_type=paragraph`）、列表切片（`chunk_type=list`）、表格切片（`chunk_type=table`）、`section_path` 格式（`## 架构 > ### 数据流`）、标题层次父子关系、嵌入对象跳过（FR-005/边缘案例）

- [X] T027 [P] [US5] Red: 编写 Word 来源位置标识契约测试
  - [路径] `backend/tests/contract/test_format_locators.py`（Word heading/paragraph/list/table 分支）
  - [AC] `pytest` 运行失败；验证 `source_position` 匹配 `## 架构 > ### 数据流` 的 pattern（与 Markdown 格式一致）

### Green: 实现功能使测试通过

- [X] T028 [US5] Green: 实现 `word_parser.py` 标题/段落/列表/表格切片
  - [路径] `backend/src/rag_mcp/parsers/word_parser.py`
  - [AC] T026 测试全部通过；使用 `python-docx` 解析 .docx，按 Heading style 提取标题层次，按段落/列表/表格切分 Chunk；`section_path` 格式为标题路径；嵌入对象（图片/OLE）跳过并继续处理文本（research.md §1.5）；超长结构单元在自然边界处二次切分，Chunk 目标长度约 512–1024 Token，不使用统一 Token 切片覆盖全部材料（FR-007）

- [X] T029 [US5] Green: 扩展 `text_extractor.py` 支持 Word 文本提取
  - [路径] `backend/src/rag_mcp/parsers/text_extractor.py`
  - [AC] `extract_text(raw_bytes, "word")` 返回提取的文本内容；在 `credential_scan` 阶段之前执行（FR-011）；`ProcessingRun.stages` 含 `text_extraction` 阶段

- [X] T030 [US5] Green: 将 Word 格式接入 `_parse_content` 调度
  - [路径] `backend/src/rag_mcp/services/ingestion_service.py`（`_parse_content` 方法 + pipeline 阶段）
  - [AC] `_parse_content("...", "word", "design.docx")` 返回 Chunk 列表；T027 契约测试通过；入库流程含 `text_extraction → credential_scan → parsing → ...` 阶段

- [X] T031 [US5] Green: Word 端到端集成测试
  - [路径] `backend/tests/integration/test_format_expansion.py`（Word 测试用例）
  - [AC] 上传 .docx 文件 → 处理完成（含 text_extraction 阶段）→ `search_knowledge` 查询标题 → 返回证据 `source_position` 匹配标题路径

**Checkpoint**: US5 Word 独立可验收

---

## Phase 8: User Story 6 - PDF 文档按标题/页码检索 (Priority: P2)

**Goal**: 上传文本版 PDF 文档，使用 pdfplumber 按标题/段落/页码切片（含多栏检测），Agent 可通过标题路径或页码检索

**Independent Test**: 上传含标题层次+多页+多栏布局的 PDF 文档，验证按标题和页码切分 Chunk，多栏文本按正确阅读顺序进入 Chunk

### Red: 编写失败测试

- [X] T032 [P] [US6] Red: 编写 PDF 解析器单元测试
  - [路径] `backend/tests/unit/test_parsers/test_pdf_parser.py`
  - [AC] `pytest` 运行失败（ImportError）；测试覆盖标题切片（`chunk_type=heading`）、段落切片（`chunk_type=paragraph`）、`section_path` 格式（`page:12 §3.2 数据流`）、多栏阅读顺序保留（澄清 Q2）、扫描版 PDF 拒绝、栏检测降级（FR-006/边缘案例）

- [X] T033 [P] [US6] Red: 编写 PDF 来源位置标识契约测试
  - [路径] `backend/tests/contract/test_format_locators.py`（PDF heading/paragraph 分支）
  - [AC] `pytest` 运行失败；验证 `source_position` 匹配 `page:12 §3.2 数据流` 和 `page:5` 的 pattern

### Green: 实现功能使测试通过

- [X] T034 [US6] Green: 实现 `pdf_parser.py` 标题/段落/页码切片（含多栏检测）
  - [路径] `backend/src/rag_mcp/parsers/pdf_parser.py`
  - [AC] T032 测试全部通过；使用 `pdfplumber` 提取文本与布局信息，按标题/段落切分 Chunk，每条 Chunk 携带页码；多栏布局通过 x 坐标分析检测，按阅读顺序输出文本；栏检测失败时降级为线性提取并标注降级原因（research.md §1.6）；超长结构单元在自然边界处二次切分，Chunk 目标长度约 512–1024 Token，不使用统一 Token 切片覆盖全部材料（FR-007）

- [X] T035 [US6] Green: 扩展 `text_extractor.py` 支持 PDF 文本提取
  - [路径] `backend/src/rag_mcp/parsers/text_extractor.py`
  - [AC] `extract_text(raw_bytes, "pdf")` 返回提取的文本内容；扫描版 PDF（无可提取文本层）抛出异常并说明"不支持的格式"（FR-019/边缘案例）

- [X] T036 [US6] Green: 将 PDF 格式接入 `_parse_content` 调度
  - [路径] `backend/src/rag_mcp/services/ingestion_service.py`（`_parse_content` 方法 + pipeline 阶段）
  - [AC] `_parse_content("...", "pdf", "paper.pdf")` 返回 Chunk 列表；T033 契约测试通过；入库流程含 `text_extraction → credential_scan → parsing → ...` 阶段

- [X] T037 [US6] Green: PDF 端到端集成测试（验证多栏检测+扫描版拒绝）
  - [路径] `backend/tests/integration/test_format_expansion.py`（PDF 测试用例）
  - [AC] 上传文本版多栏 PDF → 多栏文本按阅读顺序进入 Chunk → `search_knowledge` 查询标题 → 返回证据 `source_position` 含页码；跨页段落不因分页断裂而丢失上下文、每条 Chunk 携带所在页码（US6-AC2）；上传扫描版 PDF → 系统拒绝处理并报告原因

**Checkpoint**: US6 PDF 独立可验收

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: 评测集扩展、回归验证、硬约束验证与前端适配

- [X] T038 [P] 扩展评测集，新增 ≥ 12 条新格式查询
  - [路径] `eval/eval_dataset.json`
  - [AC] 原 18 条保留；新增每种格式 ≥ 2 条查询（含 ≥ 1 条精确结构标识 + ≥ 1 条自然语言），共 ≥ 12 条新增；新增查询含 `query`/`project_scope`/`expected_evidence_ids` 字段（FR-024）

- [X] T039 [P] 扩展 `run_comparison.py` 支持格式对照报告
  - [路径] `eval/run_comparison.py`
  - [AC] 生成逐格式 Recall@K/MRR/nDCG/P50/P95 报告；逐查询对照 001 Dense 基线与 002 混合检索基线排名 vs 003 回归排名；输出 `eval/format_expansion_report.json`（FR-026）

- [X] T040 回归验证：原 18 条 Markdown/Java 查询不劣化
  - [路径] `eval/run_eval.py` + `eval/regression_report.json`
  - [AC] 原 18 条查询 Recall@K/MRR/nDCG 不劣于 001 Dense 基线与 002 混合检索基线（以 002 为主门禁：Recall@K 精确、MRR/nDCG 1% 相对容差，research.md §0.2 轨道 A）；逐查询排名无变化或变化可解释（SC-002/FR-023）—— 依赖 T038

- [X] T041 硬约束验证：混合格式评测集
  - [路径] `backend/tests/contract/test_format_locators.py`（全格式 `source_position` 断言）+ `eval/run_comparison.py`（混合格式评测集运行，复用 T039）+ 复用 001/002 既有 MCP 输出 Schema 校验（对 `search_knowledge`/`get_evidence` 响应按 `contracts/common.schema.json` 与 001/002 MCP 响应 Schema 做 jsonschema 校验）
  - [AC] 跨项目泄漏事件数 = 0；MCP Schema 合法率 = 100%（对混合评测集每条 Tool 响应跑 001/002 既有 jsonschema 校验，失败率 = 0）；来源可定位率 = 100%（全格式 `source_position` 经 `format-locators.schema.json` 校验通过）（SC-004/SC-005/SC-006/FR-025）—— 依赖 T038~T040

- [X] T042 [P] 前端上传接受类型列表扩展
  - [路径] `frontend/src/`（上传组件文件类型 accept 属性）
  - [AC] 上传组件接受 `.go`/`.py`/`.sql`/`.json`/`.yaml`/`.yml`/`.docx`/`.pdf` 文件类型；格式标签正确显示

- [X] T043 [P] 新格式凭据脱敏验证（FR-012 / SC-007）
  - [路径] `backend/tests/integration/test_format_expansion.py`（凭据脱敏用例）+ `backend/tests/unit/test_parsers/test_credential_redaction.py`
  - [AC] 上传含凭据的新格式材料（OpenAPI API Key、DDL 密码、Python 环境变量赋值）→ 处理完成 → 检索索引与 MCP 证据正文均不含原始凭据值；字段名/结构仍可检索；凭据值被 `<api-key>`/`<password>`/`<token>`/`<secret>` 类型化占位符替换（沿用 001 凭据规范化，宪法原则 V）

- [X] T044 [P] RetrievalRun 格式与证据引用追踪（FR-027）
  - [路径] `backend/alembic/versions/*_add_retrieval_run_format.py`（新增迁移）+ `backend/src/rag_mcp/models/retrieval_run.py`（模型新增 `format` 列）+ `backend/src/rag_mcp/services/`（RetrievalRun 写入 `format`）+ `backend/tests/unit/test_retrieval_run.py`
  - [AC] Alembic 迁移新增 `retrieval_runs.format VARCHAR(8) NULLABLE` 列 + CHECK（`format` 为 NULL 或 8 种合法值之一）；`retrieval_run.py` 模型新增 `format: Mapped[str | None]` 列；`alembic upgrade head` 成功且 001/002 既有记录 `format` 默认 NULL（向后兼容，data-model.md §6.1）；`RetrievalRun` 记录每次新格式检索的请求标识、知识作用域、完成状态、`format`（命中证据所属格式；同时命中多格式时取排名最高（top-1）证据的格式，data-model.md §3.5）与证据引用；无命中时 `format=NULL`；单元测试断言字段写入与回溯可查（data-model.md §3.5）；`format` 列不进入 MCP 对外契约（宪法原则 VII）

- [X] T045 [P] Chunk 目标长度与不统一切片验证（FR-007）
  - [路径] `backend/tests/contract/test_chunk_length.py`
  - [AC] 各新格式解析器产出的 Chunk 目标长度控制在约 512–1024 Token；超长结构单元在自然边界处二次切分；断言无“统一 Token 切片”覆盖全部材料（蓝图 §7 L144/L158）

- [X] T046 继承不变式验证（FR-020 / FR-021 / FR-022）
  - [路径] `backend/tests/integration/test_inherited_invariants.py`
  - [AC] 新格式解析产生空 Chunk 列表时不发布版本、旧版本继续可用（FR-020）；对一新格式知识源可从原始源+版本信息重建全部派生索引（Dense+Sparse，蓝图 §8.4 / FR-021）；新格式版本声明与已有格式相同的 `dense_ready`/`lexical_ready` 能力清单、不引入新能力标志（FR-022）

- [X] T047 无显式 project_scope 拒绝验证（FR-013）
  - [路径] `backend/tests/integration/test_retrieval_isolation.py`
  - [AC] 对新格式材料的 `search_knowledge` 请求不携带显式 `project_scope` 时被拒绝并返回候选项目，不回退默认全库搜索（宪法硬约束：检索无显式 project_scope 必须拒绝）；与 T041 跨项目泄漏断言共同覆盖作用域硬约束

- [X] T048 [P] 目标 Host 冒烟测试（宪法交付工作流 §5：target-host tests）
  - [路径] `backend/tests/integration/test_target_host_smoke.py` + 001/002 既有目标 Host 评测脚手架
  - [AC] 对一新格式知识源（如 OpenAPI）通过真实 MCP Host（Claude Code / DeepSeek Harness / ChatGPT App 任一可用项）发起 `search_knowledge`，断言响应 100% 通过 `search_knowledge`/`get_evidence` 输出 Schema 校验、证据可定位、`completion_status` 合法；003 不修改 MCP 对外契约（FR-015），本任务验证新格式 Chunk 经既有契约在真实 Host 上可消费。**继承说明**：001 已建立目标 Host 基线，003 契约不变，本冒烟测试满足宪法交付工作流 §5 的 target-host 要求

- [X] T049 [P] FR-018 解析降级与失败路径测试（OpenAPI 不合规 + DDL 不支持方言）
  - [路径] `backend/tests/unit/test_parsers/test_openapi_parser.py`（降级分支）+ `backend/tests/unit/test_parsers/test_ddl_parser.py`（降级分支）
  - [AC] 上传 `malformed.openapi.json`（OpenAPI 形态但不合规：缺版本字段或断 `$ref`）→ 解析器报告解析失败/降级并说明原因，不伪造端点/Schema 边界（FR-018）；上传 `unsupported_dialect.sql`（含私有方言特性）→ 可识别 DDL 语句正常切分，不可识别语句标注为未识别但不丢弃整个文件、不伪造结构（FR-018/FR-002）

- [X] T050 [P] FR-019 空/损坏二进制格式失败路径测试（Word 空/损坏 + PDF 损坏）
  - [路径] `backend/tests/unit/test_parsers/test_word_parser.py`（空/损坏分支）+ `backend/tests/unit/test_parsers/test_pdf_parser.py`（损坏分支）
  - [AC] 上传 `empty.docx`（空）→ 系统报告失败并说明原因，不产生空 Chunk（FR-019/FR-020）；上传 `corrupt.docx`（损坏 OOXML）→ 文本提取失败抛出带原因异常（FR-011/FR-019）；上传 `corrupt.pdf`（损坏非扫描 PDF）→ 报告失败并说明原因，不伪造内容；未完成版本不参与检索、旧版本继续可用（FR-020）

- [X] T051 [P] SC-010 评测可重复性验证
  - [路径] `eval/run_eval.py` + `eval/reproducibility_report.json`
  - [AC] 同一环境连续两次运行混合评测集，断言非延迟指标 Recall@K/MRR/nDCG 在容差内一致（固定 Embedding 检索确定性下应完全相等）；延迟指标 P50/P95 标注为环境敏感不纳入一致性断言；输出 `reproducibility_report.json`（SC-010）—— 依赖 T038/T039

**Checkpoint**: 全部 8 种格式可检索；回归验证通过；硬约束验证通过；凭据安全与作用域拒绝验证通过；目标 Host 冒烟通过；解析降级/失败路径覆盖；评测可重复性验证通过

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，可立即开始
- **Foundational (Phase 2)**: 依赖 Setup 完成 —— **阻塞所有用户故事**。T052（DB CHECK 迁移）为 Phase 2 首要前置，T003 落库 `format` 与所有 chunk_type 落库均依赖 T052；T053（管理 API 契约）可与 T003 并行（不同文件）
- **User Stories (Phase 3~8)**: 全部依赖 Foundational 完成
  - P1 故事（US1~US4）可并行（如有多人），也可按 P1 顺序串行
  - P2 故事（US5~US6）可在 P1 完成后开始
- **Polish (Phase 9)**: 依赖所有用户故事完成（T040/T041 依赖 T038~T039；T043~T051 为覆盖缺口任务，T046 依赖各格式解析器就绪，T048 目标 Host 冒烟依赖至少一个用户故事完成，T049/T050 依赖对应解析器+T002 失败夹具就绪，T051 依赖 T038/T039）

### User Story Dependencies

| 故事 | 优先级 | 依赖 | 可并行? |
|------|--------|------|---------|
| US1 OpenAPI | P1 | Foundational | ✅ 与 US2~US4 并行 |
| US2 DDL | P1 | Foundational | ✅ 与 US1/US3~US4 并行 |
| US3 Go | P1 | Foundational | ✅ 与 US1/US2/US4 并行 |
| US4 Python | P1 | Foundational | ✅ 与 US1~US3 并行 |
| US5 Word | P2 | Foundational + T005 | ✅ 与 US6 并行 |
| US6 PDF | P2 | Foundational + T005 | ✅ 与 US5 并行 |

### Within Each User Story (TDD)

1. **Red**: 先编写测试，确认测试失败（模块不存在/断言失败）
2. **Green**: 实现功能，确认测试通过
3. 契约测试 → 单元测试 → 集成测试（由外到内验证）

### 串行依赖标注（无 [P] 的任务）

- T004 依赖 T003（同文件 `ingestion_service.py`）
- T052 为 Phase 2 首要前置（DB CHECK 迁移）；T003 落库 `format`、各格式解析器落库 `chunk_type` 均依赖 T052 完成，否则违反 DB CHECK 约束而落库失败
- T053 与 T003 可并行（管理 API 契约 vs 格式检测逻辑，不同文件）
- T009 依赖 T008（OpenAPI parser 先实现再接入）
- T014 依赖 T013（DDL parser 先实现再接入）
- T019 依赖 T018（Go parser 先实现再接入）
- T024 依赖 T023（Python parser 先实现再接入）
- T030 依赖 T028+T029（Word parser+text_extractor 先实现再接入）
- T036 依赖 T034+T035（PDF parser+text_extractor 先实现再接入）
- T040 依赖 T038（评测集先扩展再回归）
- T041 依赖 T040（回归先通过再验证硬约束）
- T046 依赖所有用户故事完成（需各格式解析器就绪验证继承不变式）
- T047 依赖 Foundational（检索路径就绪）；与 T041 共同验证作用域硬约束
- T048 依赖至少一个用户故事完成（需新格式知识源可检索）；目标 Host 冒烟
- T049 依赖 T008+T013（OpenAPI/DDL 解析器就绪）+ T002 失败夹具
- T050 依赖 T028+T034（Word/PDF 解析器就绪）+ T002 失败夹具
- T051 依赖 T038+T039（评测集与对照报告就绪）
- **[L6 说明]** T009/T014/T019/T024/T030/T036 均写入 `ingestion_service.py::_parse_content`，跨用户故事并行时须串行合并该方法分支（解析器文件本身可并行，`_parse_content` 接入不可真正并行）

### Parallel Opportunities

- Phase 1: T002 可与 T001 并行
- Phase 2: T005、T053 可与 T003/T004 并行（不同文件；T052 为 DB CHECK 迁移首要前置，须先完成后再落库）
- Phase 3~6: US1~US4 全部可并行（不同 parser 文件，Foundational 完成后）
- Phase 7~8: US5~US6 全部可并行（不同 parser 文件）
- Phase 9: T038/T039/T042/T048/T049/T050/T051 可并行（不同文件/不同失败路径）
- 每个 US 内的 Red 测试（T006+T007、T011+T012 等）可并行

---

## Implementation Strategy

### MVP First (US1 OpenAPI Only)

1. 完成 Phase 1: Setup
2. 完成 Phase 2: Foundational（**阻塞所有故事**）
3. 完成 Phase 3: US1 OpenAPI
4. **STOP and VALIDATE**: 上传 OpenAPI 文件 → 检索端点 → 验证来源定位与硬约束
5. 可演示 MVP

### Incremental Delivery

1. Setup + Foundational → 框架就绪
2. US1 OpenAPI → 独立验收 → 演示
3. US2 DDL → 独立验收 → 演示
4. US3 Go → 独立验收 → 演示
5. US4 Python → 独立验收 → 演示
6. US5 Word → 独立验收 → 演示
7. US6 PDF → 独立验收 → 演示
8. Polish → 回归+硬约束验证 → 全格式发布

### Parallel Team Strategy

- 开发者 A: US1 OpenAPI + US5 Word
- 开发者 B: US2 DDL + US6 PDF
- 开发者 C: US3 Go + US4 Python
- Polish 阶段全员协作

---

## Notes

- [P] = 可并行（不同文件，无未完成依赖）
- [US*] = 所属用户故事
- TDD: Red（失败测试）→ Green（实现通过）严格顺序
- 每个用户故事独立可验收（SC-009）
- commit 在每个任务或逻辑组之后
- 在任何 Checkpoint 可暂停验证当前故事

## Phase 10: Convergence

- [ ] T054 实现 PDF 多栏（栏感知）阅读顺序保留：用 pdfplumber 的 words/x 坐标检测栏布局并按正确阅读顺序重组文本，栏检测失败时降级线性提取并标注原因 per FR-006 (missing)
- [ ] T055 用完整 37 条评测集（不带 --limit）重产 eval/format_expansion_report.json，使 6 种新格式各含 ≥2 条非零指标；并发布 openapi 评测语料（当前 scope 352014591405850625 无 published 版本、openapi 查询 MRR=0） per SC-003/T039 (partial)
- [ ] T056 产出 eval/reproducibility_report.json（或将 T051 输出路径与报告内嵌 reproducibility 块对齐） per SC-010/T051 (missing)
- [ ] T057 为 eval/regression_report.json 增加原 18 条 Markdown/Java 相对 001/002 基线的非劣判定 verdict（当前无 gate/no_regression 字段、仅 30 条 hybrid 均值） per SC-002/T040 (partial)
