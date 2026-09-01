# Feature Specification: Structured Asset Expansion

**Feature Branch**: `003-structured-asset-expansion`

**Created**: 2026-08-28

**Status**: Draft

**Input**: User description: "Word/PDF/OpenAPI/DDL/Go/Python 按批扩展切片。范围依据：蓝图 §23.4 第 3 项 / §7 / §2.1。硬性约束：检索必须显式 project_scope；跨项目串库必须为零；MCP Schema 合法率与来源可定位率必须 100%。与 001 确定性基线对照评测。不重复 001 与 002 已实现能力。"

**Scope Basis**: 蓝图 §23.4 第 3 项（003 纵向交付 Feature 定义：Word、PDF、OpenAPI、DDL、Go、Python 等格式按独立验收批次扩展）、§7（解析与结构切片：格式感知结构切片，不使用统一 Token 切片）、§2.1（范围内：用户上传文档、接口定义、代码、数据结构；系统执行解析、结构切片）。

## 对照对象（基线）

本 Feature 的对照评测对象为 **001 确定性基线**（Feature `001-minimum-rag-mcp-loop`），其基线数据记录于 `eval/baseline_report.json`：

| 指标 | 001 Dense 基线值 |
|------|----------------|
| Recall@K（mean, K=5） | 1.0 |
| MRR（mean） | 0.9091 |
| nDCG@K（mean） | 0.9329 |
| 延迟 P50 | 138.45 ms |
| 延迟 P95 | 185.15 ms |
| 评测集查询数 | 11（原始） |
| 嵌入模型 | BAAI/bge-m3 |
| 支持格式 | Markdown、Java |
| 切片策略 | Markdown 章节感知 / Java 符号感知（tree-sitter） |

001 基线建立了 Markdown 章节路径（如 `## 安装 > ### 配置`）与 Java 全限定符号路径（如 `com.example.service.UserService#methodName`）两种来源定位格式，并验证了 Dense 检索在该评测集上的 Recall@K=1.0。002 在此基础上扩充至 18 条查询（新增词汇精确查询与中文查询），并新增 Sparse/BM25、融合与 Rerank 能力。

003 的对照评测要求：
1. **无回归验证**：在 001/002 既有评测集（Markdown/Java 查询）上重跑检索，验证 Recall@K、MRR、nDCG 不劣于基线，确认新格式解析器的引入未破坏已有格式的切片与检索质量。
2. **新格式评测**：为每种新增格式（OpenAPI、DDL、Go、Python、Word、PDF）在固定评测集中新增 ≥ 2 条查询（含 ≥ 1 条精确结构标识查询与 ≥ 1 条自然语言查询），验证新格式内容的检索质量与来源定位。
3. **硬约束验证**：在包含全部格式的混合评测集上验证跨项目串库为零、MCP Schema 合法率 100%、来源可定位率 100%。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 上传 OpenAPI/Swagger 接口定义并按端点检索 (Priority: P1)

用户上传 OpenAPI/Swagger 规范文件（JSON 或 YAML），系统按服务、端点（Endpoint）、请求体、响应体和 Schema 定义进行结构切片，外部 Agent 可以通过 HTTP 方法与路径（如 `GET /api/v1/users`）或 Schema 名称精确检索到对应接口定义。

**Why this priority**: 接口定义是 Agent 形成"根据现有接口形成详细设计"（蓝图 §24.4）的核心知识来源。OpenAPI 具有机器可读的结构化定义，端点级切片使 Agent 能一次定位到精确的接口契约，而非在全文中模糊搜索。

**Independent Test**: 上传一个包含多个端点与 Schema 定义的 OpenAPI 文件，验证系统按端点切分 Chunk，并验证 Agent 通过端点路径检索能定位到正确的端点定义及其来源版本与位置。

**Acceptance Scenarios**:

1. **Given** 一个已发布且声明检索能力的知识版本包含 OpenAPI 文件，**When** Agent 携带显式项目作用域查询 `GET /api/v1/users` 端点定义，**Then** 系统返回该端点的请求/响应结构 Chunk，携带来源版本与位置标识（HTTP 方法+路径），且不返回其他端点的内容。
2. **Given** OpenAPI 文件包含 `components/schemas` 中的 Schema 定义，**When** Agent 查询某个 Schema 名称（如 `User`），**Then** 系统返回该 Schema 定义 Chunk，携带来源位置标识（Schema 路径），父子关系将 Schema 定义与其引用端点关联。
3. **Given** 一个 OpenAPI 端点的请求体引用了公共 Schema，**When** 该端点 Chunk 作为子 Chunk 检索命中，**Then** 系统可通过父级上下文恢复该端点的完整定义，并保留 Schema 引用关系。

---

### User Story 2 - 上传 DDL 数据结构定义并按表/字段检索 (Priority: P1)

用户上传 SQL DDL 文件（建表语句、约束、索引、视图、存储过程），系统按表（Table）、字段（Column）、约束（Constraint）、索引（Index）、视图（View）和存储过程进行结构切片，外部 Agent 可以通过表名或字段名精确检索到对应的数据结构定义。

**Why this priority**: 数据结构定义是 Agent 理解项目数据模型的核心知识。表名和字段名是蓝图 §9 明确的"固定类名、错误码、表名、字段名"词汇精确匹配场景，DDL 结构切片使这些标识可被精确召回。

**Independent Test**: 上传一个包含多张表、约束和索引的 DDL 文件，验证系统按表和字段切分 Chunk，并验证 Agent 通过表名检索能定位到正确的表定义及其来源版本与位置。

**Acceptance Scenarios**:

1. **Given** 一个已发布的知识版本包含 DDL 文件，**When** Agent 携带显式项目作用域查询表 `users` 的定义，**Then** 系统返回该表的建表语句 Chunk（含字段列表与约束），携带来源位置标识（表名），且不返回其他表的内容。
2. **Given** DDL 中某张表包含外键约束引用另一张表，**When** Agent 查询被引用表的字段定义，**Then** 系统返回该字段 Chunk，并可通过父级上下文恢复所在表的完整定义。
3. **Given** DDL 包含视图或存储过程定义，**When** Agent 查询视图名或存储过程名，**Then** 系统返回对应定义 Chunk，携带来源位置标识。

---

### User Story 3 - 上传 Go 源代码并按符号检索 (Priority: P1)

用户上传 Go 源代码文件，系统使用 AST 按包（Package）、类型（Type）、函数（Function）、方法（Method）和接口（Interface）进行符号级结构切片，外部 Agent 可以通过全限定符号路径（如 `pkg.Service#Method`）精确检索到对应的代码定义。

**Why this priority**: Go 是蓝图 §7 明确要求按 AST 符号切分的语言之一。符号级切片使 Agent 能精确定位函数或方法实现，直接支持"修改与既有模块一致的代码"（蓝图 §24.4）的任务。

**Independent Test**: 上传一个包含包声明、结构体、接口和方法的 Go 文件，验证系统按符号切分 Chunk，并验证 Agent 通过符号路径检索能定位到正确的方法实现及其来源版本与位置。

**Acceptance Scenarios**:

1. **Given** 一个已发布的知识版本包含 Go 源代码，**When** Agent 携带显式项目作用域查询 `pkg.Service#Method` 的实现，**Then** 系统返回该方法 Chunk，携带来源版本与全限定符号路径，且不返回作用域外项目的代码。
2. **Given** Go 文件无法形成完整 AST（语法错误），**When** 系统解析该文件，**Then** 系统报告降级或失败并说明原因，不得伪造符号边界（沿用 001 Java 解析的降级策略）。
3. **Given** 一个 Go 接口定义及其实现方法分属不同文件，**When** Agent 检索接口方法，**Then** 系统返回接口声明 Chunk 与实现方法 Chunk，二者通过符号路径可关联。

---

### User Story 4 - 上传 Python 源代码并按符号检索 (Priority: P1)

用户上传 Python 源代码文件，系统使用 AST 按模块（Module）、类（Class）、函数（Function）和方法（Method）进行符号级结构切片，外部 Agent 可以通过全限定符号路径（如 `module.Class.method`）精确检索到对应的代码定义。

**Why this priority**: Python 是蓝图 §7 明确要求按 AST 符号切分的语言之一。Python 是后端编排基线语言（蓝图架构约束），符号级切片使 Agent 能精确定位类或函数实现。

**Independent Test**: 上传一个包含模块级函数、类定义和方法的 Python 文件，验证系统按符号切分 Chunk，并验证 Agent 通过符号路径检索能定位到正确的函数或方法实现及其来源版本与位置。

**Acceptance Scenarios**:

1. **Given** 一个已发布的知识版本包含 Python 源代码，**When** Agent 携带显式项目作用域查询 `module.Class.method` 的实现，**Then** 系统返回该方法 Chunk，携带来源版本与全限定符号路径。
2. **Given** Python 文件包含嵌套类或嵌套函数，**When** 系统解析该文件，**Then** 系统按嵌套符号层次切分 Chunk，父子关系反映嵌套结构。
3. **Given** Python 文件包含装饰器或类型注解，**When** Agent 检索被装饰的函数，**Then** 系统 Chunk 保留装饰器与注解信息，来源位置覆盖完整符号定义。

---

### User Story 5 - 上传 Word 文档并按标题/段落检索 (Priority: P2)

用户上传 Word 文档（`.docx`），系统按标题（Heading）、段落（Paragraph）、列表（List）和表格（Table）进行结构切片，外部 Agent 可以通过标题路径（如 `## 架构设计 > ### 数据流`）检索到对应的文档内容。

**Why this priority**: Word 是蓝图 §7 明确要求按标题/段落/列表/表格切分的文档格式。Word 文档是项目文档的常见载体，标题级切片使 Agent 能按章节定位文档内容。P2 优先级因为 Word 解析需要从 OOXML 格式提取文本结构，复杂度高于纯文本格式。

**Independent Test**: 上传一个包含多级标题、段落、列表和表格的 Word 文档，验证系统按标题层次切分 Chunk，并验证 Agent 通过标题路径检索能定位到正确的章节内容及其来源版本与位置。

**Acceptance Scenarios**:

1. **Given** 一个已发布的知识版本包含 Word 文档，**When** Agent 携带显式项目作用域查询某标题下的内容，**Then** 系统返回该标题下的段落/列表/表格 Chunk，携带来源版本与标题路径位置标识。
2. **Given** Word 文档包含表格，**When** 系统切片，**Then** 表格作为独立结构单元切分，保留行列结构信息。
3. **Given** Word 文档为空或无法解析，**When** 系统处理该文件，**Then** 系统报告失败并说明原因，不产生空 Chunk 或伪造内容。

---

### User Story 6 - 上传 PDF 文档并按标题/页码检索 (Priority: P2)

用户上传 PDF 文档，系统按标题（Heading）、段落（Paragraph）和页码进行结构切片，外部 Agent 可以通过标题路径或页码检索到对应的文档内容。

**Why this priority**: PDF 是蓝图 §7 明确要求按标题/段落切分的文档格式。PDF 文档广泛用于项目规格、设计文档和外部参考资料。P2 优先级因为 PDF 文本提取与结构识别复杂度高，且扫描版 PDF（图像）不在本 Feature 范围内。

**Independent Test**: 上传一个包含标题层次与多页内容的文本版 PDF 文档，验证系统按标题和页码切分 Chunk，并验证 Agent 通过标题路径或页码检索能定位到正确的内容及其来源版本与位置。

**Acceptance Scenarios**:

1. **Given** 一个已发布的知识版本包含文本版 PDF 文档，**When** Agent 携带显式项目作用域查询某标题下的内容，**Then** 系统返回该标题下的段落 Chunk，携带来源版本与标题路径及页码位置标识。
2. **Given** PDF 文档跨页内容，**When** 系统切片，**Then** 每条 Chunk 携带其所在页码，跨页段落不因分页断裂而丢失上下文。
3. **Given** PDF 文档为扫描版（纯图像，无可提取文本），**When** 系统处理该文件，**Then** 系统报告不支持的格式原因并拒绝处理，不伪造文本内容。

---

### Edge Cases

- 上传文件扩展名与实际内容不匹配时（如 `.go` 文件内含 Python 代码），系统必须检测格式不匹配并报告失败，不得静默按扩展名错误切片。
- OpenAPI/Swagger 文件不符合规范（如缺少 `openapi`/`swagger` 版本字段或 JSON/YAML 语法错误）时，系统必须报告解析失败并说明原因。
- DDL 文件包含 DML 语句（INSERT/UPDATE/DELETE）或不被支持的 SQL 方言特性时，系统必须按可识别的 DDL 语句切分并标注非 DDL 语句为未识别（不产生可检索 Chunk），不得丢弃整个文件。
- Go 或 Python 文件包含语法错误无法形成完整 AST 时，系统必须报告降级或失败，不得伪造符号边界（沿用 001 Java 解析降级策略）。
- Word 文档包含嵌入对象（图片、图表、OLE 对象）时，系统必须跳过无法提取文本的嵌入对象并继续处理文本内容，不因嵌入对象而中断整个文档处理。
- PDF 文档包含混合文本与扫描页面时，系统必须处理可提取文本的页面并跳过纯图像页面，标注跳过原因。
- 当 PDF 页面的栏布局无法被检测器识别时（如不规则布局、嵌套栏、单栏与多栏混合页面），系统必须降级为线性提取并标注降级原因，不得因栏检测失败而丢弃整个页面或文件。
- 新格式 Chunk 与已有 Markdown/Java Chunk 共存于同一项目知识版本时，各格式 Chunk 必须保留各自的来源位置标识格式，不互相混淆。
- 上传包含凭据值的新格式材料（如 OpenAPI 中的 API Key、DDL 中的数据库密码、Python 中的环境变量赋值）时，凭据值必须被类型化占位符替换，字段名和结构保留（沿用 001 凭据规范化）。
- 多种格式材料并发入库时，请求级项目作用域、证据与解析状态不得互相污染（沿用 001 并发隔离）。
- 当新格式解析器对某种边缘结构产生空 Chunk 时，系统不得发布包含空 Chunk 的版本，沿用 001 "无 Chunk 则失败"策略。

## Requirements *(mandatory)*

### Functional Requirements

**格式感知结构切片（蓝图 §7）**

- **FR-001**: 系统 MUST 为 OpenAPI/Swagger 文件（JSON 或 YAML 格式）实施端点感知切片，按服务、端点（HTTP 方法+路径）、请求体、响应体和 Schema 定义切分 Chunk，每条 Chunk 携带来源位置标识（端点路径如 `GET /api/v1/users` 或 Schema 路径如 `schema:components.schemas.User`），并保留端点与引用 Schema 的父子关系（蓝图 §7：OpenAPI/Swagger 按服务、Endpoint、请求、响应和 Schema 切分）。**chunk 粒度说明**：`service` 为文档级父级上下文，`请求体`/`响应体` 作为 `endpoint` Chunk 的内容承载（不独立成 chunk_type）；003 仅产出 `endpoint` 与 `schema` 两种 chunk_type（见 data-model.md §3.2），端点 Chunk 内含其请求/响应结构。**蓝图 §7 映射**：蓝图 §7 字面列 service/Endpoint/请求/响应/Schema 五单元；003 将 `service` 作文档级父上下文、`请求`/`响应` 作 `endpoint` Chunk 内容承载，刻意收窄为 `endpoint`+`schema` 两 chunk_type，属有记录的范围决策（对齐蓝图 §23.4 第 3 项独立验收批次）。
- **FR-002**: 系统 MUST 为 DDL 文件实施表感知切片，按表（Table）、字段（Column）、约束（Constraint）、索引（Index）、视图（View）和存储过程切分 Chunk，每条 Chunk 携带来源位置标识（如 `table:users`、`table:users.column:email` 或 `constraint:pk_users`），并保留表与其字段/约束的父子关系。命名表级约束（PRIMARY KEY/FOREIGN KEY/UNIQUE/CHECK）作为独立 `constraint` Chunk 切分；列级约束（NOT NULL/DEFAULT）作为字段 Chunk 的属性，不独立成 Chunk（蓝图 §7：DDL 按表、字段、约束、索引、视图和存储过程切分）。DDL 文件中的非 DDL 语句（如 INSERT/UPDATE/DELETE 等 DML）MUST NOT 产生可检索 Chunk，系统 MUST 在处理阶段标注非 DDL 语句为未识别但不将其作为独立 Chunk 索引（知识库聚焦于 schema 定义而非瞬态数据）。
- **FR-003**: 系统 MUST 为 Go 源代码实施 AST 符号感知切片，按包（Package）、类型（Type）、函数（Function）、方法（Method）和接口（Interface）切分 Chunk，每条 Chunk 携带全限定符号路径（如 `pkg.Service#Method`）作为来源位置标识，并保留符号间的父子关系（蓝图 §7：Java、Go、Python 及其他支持语言按 AST 符号、类、函数、方法和模块切分）。**chunk 粒度说明**：蓝图 §7 字面列模块；003 将 `Package` 作为全限定符号路径的前缀组成（如 `pkg.Service#Method` 中的 `pkg.`，包名自包声明提取），不独立成 chunk_type；Go 仅产出 `function`/`method`/`type`/`interface` 四种 chunk_type（见 data-model.md §3.2），属有记录的范围决策。
- **FR-004**: 系统 MUST 为 Python 源代码实施 AST 符号感知切片，按模块（Module）、类（Class）、函数（Function）和方法（Method）切分 Chunk，每条 Chunk 携带全限定符号路径（如 `module.Class.method`）作为来源位置标识，并保留嵌套符号的父子关系（蓝图 §7：Java、Go、Python 及其他支持语言按 AST 符号切分）。**chunk 粒度说明**：蓝图 §7 字面列模块；003 将 `Module` 作为全限定符号路径的前缀组成（如 `module.Class.method` 中的 `module.`，模块名自文件名提取），不独立成 chunk_type；Python 仅产出 `function`/`class`/`method` 三种 chunk_type（见 data-model.md §3.2），属有记录的范围决策。嵌套类与嵌套函数按嵌套符号层次切分，其定位器格式见 data-model.md §5.1。
- **FR-005**: 系统 MUST 为 Word 文档（`.docx`）实施标题/段落感知切片，按标题（Heading）、段落（Paragraph）、列表（List）和表格（Table）切分 Chunk，每条 Chunk 携带标题路径（如 `## 架构设计 > ### 数据流`）作为来源位置标识，并保留标题层次父子关系（蓝图 §7：Markdown、Word、PDF 和 Wiki 导出按标题、段落、列表和表格结构切分）。
- **FR-006**: 系统 MUST 为 PDF 文档实施标题/段落/页码感知切片，按标题和段落切分 Chunk，每条 Chunk 携带标题路径与所在页码（如 `page:12 §3.2 数据流`）作为来源位置标识（蓝图 §7：PDF 按标题、段落结构切分；蓝图 §6.2：文档章节、页码或段落）。首轮验收 MUST 支持多栏布局的阅读顺序保留（栏感知提取），使多栏 PDF（如学术论文、多栏报告）的文本按正确阅读顺序进入 Chunk，而非线性错乱提取。**范围说明**：首轮验收 PDF 仅产出 `heading` 与 `paragraph` 两种 chunk_type（见 data-model.md §3.2）；蓝图 §7 将 Markdown/Word/PDF/Wiki 同列按标题/段落/列表/表格切分，003 对 PDF 的 `list`/`table` chunk_type 延期至后续批次（pdfplumber 对 PDF 表格/列表的可靠结构识别复杂度高），属刻意收窄的范围决策。
- **FR-007**: 所有新增格式的 Chunk MUST 控制目标长度在约 512–1024 Token，超长结构单元在自然边界处二次切分，不使用统一 Token 切片覆盖全部材料（蓝图 §7：Chunk 目标长度控制在约 512–1024 Token）。
- **FR-008**: 所有新增格式的 Chunk MUST 建立父子索引，子 Chunk 用于精确召回，父 Chunk 用于恢复结构上下文（蓝图 §7：系统建立父子索引）。

**格式检测与解析框架扩展**

- **FR-009**: 系统 MUST 扩展 001 已建立的解析器调度框架（格式分派机制），支持新增六种格式的格式检测与解析器分派，不重新实现 001 已有的 Markdown 和 Java 解析器（沿用 001 框架，宪法原则 VII：接口独立演进）。
- **FR-010**: 系统 MUST 在上传时根据文件内容或扩展名检测格式，并记录于知识源的 `format` 字段；检测到不受支持的格式时 MUST 拒绝处理并说明原因（沿用 001 FR-004 等价约束）。
- **FR-011**: 系统 MUST 对二进制格式（Word `.docx`、PDF）在凭据规范化之前提取文本内容，提取后的文本进入与纯文本格式相同的凭据规范化→切片→嵌入流程；文本提取失败时 MUST 报告失败并说明原因。
- **FR-012**: 系统 MUST 对所有新增格式复用 001 已建立的凭据规范化能力，在切片前将明确的凭据值替换为类型化占位符（`<api-key>`、`<password>`、`<token>`、`<secret>`），保留字段名、结构和来源位置（沿用 001 FR-006，蓝图 §7：入库流程在切片前生成检索安全副本）。

**作用域与硬约束继承**

- **FR-013**: 新增格式材料的检索 MUST 继承 001 的显式项目作用域要求：缺少显式 `project_scope` 的项目检索 MUST 被拒绝，不得回退默认全库搜索（宪法硬约束：检索无显式 project_scope 必须拒绝）。
- **FR-014**: 新增格式材料的检索 MUST 保证跨项目泄漏事件数在验收测试集中为零：新格式 Chunk、Dense/Sparse 召回、融合与 Rerank 结果都不包含作用域外项目的 Chunk（宪法硬约束：跨项目串库必须为零）。
- **FR-015**: 新增格式材料的 Tool 响应 MUST 100% 通过 `search_knowledge` 与 `get_evidence` 输出 Schema 校验（宪法硬约束：MCP Schema 合法率 100%）；003 不修改 001 已确立的 MCP 对外契约，新格式 Chunk 通过既有契约返回（宪法原则 VII）。
- **FR-016**: 新增格式材料返回的每条证据 MUST 携带来源 ID、版本与可定位位置（各格式的来源位置标识格式，见 FR-001 至 FR-006），来源可定位率在验收测试集中为 100%（宪法硬约束：证据来源可定位率 100%）。

**解析降级与失败处理**

- **FR-017**: 当 Go 或 Python 文件无法形成完整 AST 时，系统 MUST 报告降级或失败并说明原因，不得伪造符号边界（沿用 001 Java 解析器降级策略：AST 失败时回退或报错）。
- **FR-018**: 当 OpenAPI/Swagger 文件不符合规范或 DDL 文件包含不支持的 SQL 方言时，系统 MUST 报告解析失败或降级并说明原因，不得静默丢弃或伪造结构。
- **FR-019**: 当 Word 或 PDF 文档为空、损坏或无可提取文本（如扫描版 PDF）时，系统 MUST 报告失败并说明原因，不产生空 Chunk 或伪造内容（沿用 001 "无 Chunk 则失败"策略）。
- **FR-020**: 当新格式解析产生空 Chunk 列表时，系统 MUST NOT 发布包含空 Chunk 的版本，并沿用 001 失败保护：未完成版本不参与检索，旧版本继续可用。

**版本与重建**

- **FR-021**: 系统 MUST 能够从原始知识源与版本信息重建新增格式的全部派生索引（Dense 向量与 Sparse/BM25 词法索引），沿用蓝图 §8.4 与 001/002 重建能力。
- **FR-022**: 新增格式材料发布的知识版本 MUST 声明与已有格式相同的检索能力清单（`dense_ready` 与 `lexical_ready`），不引入新的能力标志；格式支持是知识源属性，不是版本能力属性（蓝图 §5 索引能力清单）。

**对照评测**

- **FR-023**: 系统 MUST 在 001/002 既有固定评测集（`eval/eval_dataset.json`，18 条 Markdown/Java 查询）上重跑检索，验证 Recall@K、MRR、nDCG 不劣于 001 基线（`eval/baseline_report.json`）与 002 混合检索基线（`eval/hybrid_comparison_report.json`）（非劣判定容差按 research.md §0.2 轨道 A 声明：Recall@K 精确、MRR/nDCG 1% 相对容差），确认新格式解析器的引入未造成已有格式检索回归（蓝图 §24.3：后续 Feature 必须声明相对基线目标）。
- **FR-024**: 系统 MUST 为每种新增格式（OpenAPI、DDL、Go、Python、Word、PDF）在固定评测集中新增 ≥ 2 条查询（含 ≥ 1 条精确结构标识查询如端点路径或表名，与 ≥ 1 条自然语言查询），新增查询遵循 001 的 AI 生成、人工审核、JSON 格式约定，保留原 18 条保证与基线逐条可比。
- **FR-025**: 系统 MUST 在包含全部格式（Markdown、Java、OpenAPI、DDL、Go、Python、Word、PDF）的混合评测集上验证：跨项目泄漏事件数为零、所有 Tool 成功响应 100% 通过 Schema 校验、所有返回证据 100% 可定位到确定的知识源版本与内容位置（宪法硬约束 ×3）。
- **FR-026**: 对照评测 MUST 逐格式记录新格式查询的 Recall@K、MRR、nDCG、P50/P95 延迟，以及与 001 基线的回归验证结果，使格式扩展的质量影响可解释（宪法原则 IV：证据可定位与可解释）。
- **FR-027**: 系统 MUST 记录每次新格式材料检索的请求标识、知识作用域、完成状态、格式类型与证据引用，以支持问题回溯（蓝图 §13 证据账本，沿用 001 FR-025）。

### Key Entities *(include if feature involves data)*

- **格式感知 Chunk（Format-Aware Chunk）**: 从新增格式材料产生的结构化检索单元，携带格式特定的来源位置标识（端点路径、表名/字段名、全限定符号路径、标题路径/页码），并保留父子上下文关系。
- **来源位置标识（Source Locator）**: 各格式的可定位位置标识，沿用 001 Markdown 章节路径与 Java 符号路径模式，扩展为：OpenAPI 端点路径/Schema 路径、DDL 表名/字段名/约束名、Go/Python 全限定符号路径、Word 标题路径、PDF 标题路径+页码。
- **格式检测（Format Detection）**: 上传时根据文件内容或扩展名确定 `format` 字段值，扩展 001 的 `markdown`/`java` 至 `openapi`/`ddl`/`go`/`python`/`word`/`pdf`。
- **对照评测报告（Comparison Report）**: 在扩充后的固定评测集上记录各格式检索指标、与 001 Dense 基线的回归验证结果、逐格式可解释的评测产物。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 用户能够通过浏览器上传 OpenAPI、DDL、Go、Python、Word 和 PDF 六种格式的材料，并看到每种格式的完整处理状态（沿用 001 SC-001 浏览器可用性）。
- **SC-002**: 在 001/002 既有评测集（18 条 Markdown/Java 查询）上重跑检索，Recall@K、MRR、nDCG 不劣于 001 基线与 002 混合检索基线（非劣判定容差按 research.md §0.2 轨道 A：Recall@K 精确、MRR/nDCG 1% 相对容差），确认新格式引入未造成已有格式检索回归。
- **SC-003**: 新增格式查询（每种格式 ≥ 2 条，共 ≥ 12 条）在固定评测集上产生 Recall@K、MRR、nDCG 指标，并与 001 基线对照记录；首轮验收记录指标数值，不预设通过阈值（沿用 001"首轮记录基线、不预设阈值"的渐进策略）。
- **SC-004**: 在包含全部八种格式（Markdown、Java、OpenAPI、DDL、Go、Python、Word、PDF）的混合评测集上，跨项目泄漏事件数为零。
- **SC-005**: 在混合评测集上，所有 Tool 成功响应 100% 通过 `search_knowledge` 与 `get_evidence` 输出 Schema 校验。
- **SC-006**: 在混合评测集上，所有返回证据 100% 可定位到确定的知识源版本与内容位置（各格式的来源位置标识格式）。
- **SC-007**: 新增格式材料中包含的测试凭据值不会出现在检索索引或 MCP 证据正文，配置字段名和结构仍可检索（沿用 001 SC-006 凭据安全）。
- **SC-008**: Go 或 Python 文件无法形成完整 AST 时，系统报告降级或失败并说明原因，不伪造符号边界（沿用 001 Java 降级策略）。
- **SC-009**: 每种新增格式可独立验收：上传单一格式材料、检索该格式内容、验证来源定位与硬约束满足，即可确认该格式批次通过验收（蓝图 §23.4 第 3 项：按独立验收批次扩展）。
- **SC-010**: 对照评测可重复：同一环境连续两次运行混合评测集的 Recall@K、MRR、nDCG 在容差内一致（沿用 001/002 非延迟可重复性要求）；延迟指标标注为环境敏感。

## 范围内 / 范围外

### 范围内（003）

- 为 OpenAPI/Swagger、DDL、Go、Python、Word、PDF 六种格式实施格式感知结构切片（蓝图 §7 各格式切片规则）。
- 扩展 001 解析器调度框架支持新增格式的格式检测与解析器分派。
- 为每种新增格式定义来源位置标识格式（端点路径、表名/字段名、全限定符号路径、标题路径/页码）。
- 二进制格式（Word `.docx`、PDF）的文本提取（提取后进入既有凭据规范化→切片→嵌入流程）。
- 在固定评测集中新增各格式查询并与 001/002 基线对照（无回归验证 + 新格式质量记录）。
- 新格式 Chunk 的父子上下文关系构建（沿用 001 父子索引机制）。
- 新格式解析的降级与失败处理（沿用 001 失败保护策略）。
- 新格式材料的检索运行追踪扩展（格式类型与证据引用）。

### 范围外（不重复 001 与 002，且不属于 003）

- Web 管理端框架、项目与知识域管理、上传组件基础架构（001 已实现；003 仅扩展上传接受的文件类型列表）。
- Markdown 与 Java 解析器（001 已实现；003 复用，不重新实现）。
- 凭据规范化逻辑（001 已实现；003 复用，不修改）。
- Dense 嵌入与向量索引构建（001 已实现；003 复用）。
- Sparse/BM25 词法索引、RRF/DBSF 融合与 Cross-Encoder Rerank（002 已实现；003 复用）。
- MCP `search_knowledge` 与 `get_evidence` 对外契约与 Schema（001 已确立；003 不修改契约，宪法原则 VII）。
- `completion_status` 四态（`complete`/`partial`/`no_evidence`/`failed`）（001 已确立；003 沿用，蓝图 §14）。
- PostgreSQL 图关系扩展、硬关系与软关系（蓝图 §9 第 3 项与 §10，属 Feature 004 Graph RAG）。
- 三 Agent 编排、追加式证据账本的 Agent 判断、补充检索与上下文编排（蓝图 §9 第 6 项与 §11，属 Feature 005）。
- 单写多读实例、Provider 运行配置、追踪与运行指标硬化（属 Feature 006 Runtime Hardening）。
- 扫描版 PDF 的 OCR 文本识别（本 Feature 仅支持文本版 PDF；OCR 属后续扩展）。
- PDF 文档的 `list`/`table` chunk_type（蓝图 §7 将 PDF 与 Markdown/Word 同列按标题/段落/列表/表格切分；003 首轮仅产出 `heading`/`paragraph`，PDF 表格/列表的可靠结构识别延期至后续批次，见 FR-006）。
- Word 旧版二进制格式 `.doc`（本 Feature 仅支持 OOXML `.docx`；`.doc` 属后续扩展）。

## Clarifications

### Session 2026-08-28

- Q: 对于同时包含 DDL（CREATE TABLE 等）和 DML（INSERT/UPDATE/DELETE）的 SQL 文件，DML 语句应作为可检索 Chunk 索引，还是仅切分 DDL 结构单元并将非 DDL 内容标注为元数据？ → A: 仅 DDL 结构单元产生可检索 Chunk；非 DDL 语句（如 INSERT/UPDATE/DELETE）在处理阶段标注为未识别，不作为独立 Chunk 索引。知识库聚焦于 schema 定义而非瞬态数据，对齐蓝图 §7 DDL 切片规则。
- Q: PDF 文本提取是否需要保留多栏布局的阅读顺序（如学术论文），还是线性提取即可满足首轮验收？ → A: 首轮验收即要求栏感知提取——PDF 解析器 MUST 检测并保留多栏布局的阅读顺序，使多栏文档（如学术论文、多栏报告）的文本按正确阅读顺序进入 Chunk，而非线性错乱提取。单栏文档按线性提取即可。

## Assumptions

- 003 复用 001 已建立的解析器调度框架（格式分派机制）、凭据规范化、父子索引机制与固定评测集（`eval/eval_dataset.json`，18 条），在其上扩展新增格式解析器，不重新实现已有能力。
- 003 复用 002 已建立的 Sparse/BM25 词法索引、融合与 Rerank 检索路径；新格式 Chunk 自动获得 Dense 与 Sparse 索引，不引入新的检索能力标志。
- 六种新增格式按独立验收批次扩展（蓝图 §23.4 第 3 项）：每种格式可独立上传、解析、检索与验收，一个格式的失败不阻塞其他格式的验收。
- Word 文档仅支持 OOXML 格式（`.docx`）；旧版二进制格式 `.doc` 不在本 Feature 范围内。
- PDF 文档仅支持文本版 PDF（含可提取文本层的 PDF）；扫描版 PDF（纯图像）不在本 Feature 范围内，系统将报告不支持的格式原因并拒绝处理。
- PDF 解析器 MUST 在首轮验收即支持多栏布局的阅读顺序保留（栏感知提取），使学术论文和多栏报告的文本按正确阅读顺序进入 Chunk；单栏文档按线性提取即可。栏检测的具体实现方式留给 plan.md / research.md 决策。
- OpenAPI/Swagger 支持 JSON 与 YAML 两种序列化格式；DDL 支持 ANSI SQL 基本方言（CREATE TABLE、CREATE INDEX、CREATE VIEW、CREATE PROCEDURE、ALTER TABLE、约束定义等），特定数据库私有语法特性不要求全覆盖。
- Go 与 Python AST 解析沿用 001 Java 解析的 AST 符号感知方案；Python 亦可使用标准库 AST 能力，具体解析器实现方式留给 plan.md / research.md 决策。
- 新增格式查询遵循 001 的 AI 生成、人工审核、JSON 格式约定（蓝图 FR-024 等价约束），原 18 条保留以保证与基线逐条可比。
- 003 面向 001/002 已验证的单用户、本机部署环境；并发隔离沿用 001/002 的请求级隔离（5 并发），不引入多实例或分布式协调。
- 003 不修改 `search_knowledge` 与 `get_evidence` 对外契约；对外返回的证据结构、`completion_status` 四态与来源定位格式沿用 001。
- 服务端总超时沿用 001/002 的 30s 护栏并小于目标 Host Tool Call 超时（蓝图 §19）。
- 各格式来源位置标识的具体字符串格式（如端点路径分隔符、符号路径分隔符）在 plan.md / research.md 中精确定义，本规格仅约束其必须可定位到知识源版本与内容位置。
