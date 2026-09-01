# 003 Structured Asset Expansion 技术研究文档

**Feature**: 003-structured-asset-expansion
**状态**: Draft
**日期**: 2026-08-28
**依据**: Feature Spec `specs/003-structured-asset-expansion/spec.md`；系统设计蓝图 §23.4 第 3 项 / §7 / §2.1 / §6.2 / §8.4 / §24；Constitution I–X
**对照基线**: `eval/baseline_report.json`（001 Dense-only）、`eval/hybrid_comparison_report.json`（002 混合检索）

---

## 〇、评测目标（相对 001/002 基线） — 进入 plan 的前置门禁

> **硬性前置要求**（用户指令 + 蓝图 §24.3 / 宪法原则 X）：本节在 research.md 中首先声明相对基线的可度量目标。若本节未声明或目标不可度量，则不得进入 plan.md。以下目标全部可由 `eval/run_eval.py` 与 `eval/run_comparison.py` 产出的指标在固定评测集上验证。

### 0.1 001/002 基线（已记录）

| 指标 | 001 Dense 基线 | 002 混合检索基线 | 备注 |
|------|---------------|-----------------|------|
| Recall@K（mean, K=5） | 1.0 | 1.0（不下降） | 002 在原 11 条上不下降 |
| MRR（mean） | 0.9091 | ≥ 0.95（002 期望） | 002 修复 query 5 validateToken |
| nDCG@K（mean） | 0.9329 | ≥ 0.96（002 期望） | 同上 |
| 延迟 P50 | 138.45 ms | 300–900 ms（002 期望） | 新增 Sparse+Rerank |
| 延迟 P95 | 185.15 ms | 1200–3000 ms（002 期望） | CPU Rerank |
| 评测集查询数 | 11（原始） | 18（扩充后） | 002 新增词汇精确+中文查询 |
| 支持格式 | Markdown、Java | Markdown、Java | 003 扩展至 8 种 |
| 检索路径 | Dense-only | Dense+Sparse+RRF+Rerank | 003 不修改检索路径 |

### 0.2 003 期望变化

003 是**格式扩展 Feature**，不是检索路径增强。核心区别：003 不修改检索链路（Dense/Sparse/Fusion/Rerank 全部复用 001/002），只在入库阶段新增格式解析器。因此评测目标分为两条独立轨道：

#### 轨道 A：回归验证（原 18 条 Markdown/Java 查询）

| 指标 | 002 基线 | 003 期望变化 | 期望值 / 约束 | 依据 |
|------|---------|-------------|--------------|------|
| **Recall@K (K=5)** | 1.0 | **不劣化** | ≥ 002 基线同集值 | SC-002 / FR-023 |
| **MRR (mean)** | ≥ 0.95 | **不劣化** | ≥ 002 基线同集值（容差 1%） | SC-002 / FR-023 |
| **nDCG@K (mean)** | ≥ 0.96 | **不劣化** | ≥ 002 基线同集值（容差 1%） | SC-002 / FR-023 |
| **延迟 P50** | 300–900 ms | **不变**（检索路径不变） | 容差范围内 | SC-010 |
| **延迟 P95** | 1200–3000 ms | **不变**（检索路径不变） | 容差范围内 | SC-010 |

**回归风险分析**：003 扩展 `_parse_content` 格式分派机制（FR-009），新增格式分支不影响已有的 `markdown`/`java` 分支逻辑。回归风险来自代码改动可能引入的 bug，通过原 18 条评测集回归验证覆盖。由于检索路径、嵌入模型、Qdrant 集合均不变，原 18 条查询的检索结果在逻辑上不应改变。

#### 轨道 B：新格式评测（每种格式 ≥ 2 条查询，共 ≥ 12 条新增）

| 指标 | 003 期望 | 说明 |
|------|---------|------|
| **Recall@K (K=5)** | **首轮记录，不设阈值** | 新格式查询的 Recall 取决于切片质量与嵌入相似度；首轮记录基线数据，后续迭代设定阈值（沿用 001 渐进策略） |
| **MRR (mean)** | **首轮记录，不设阈值** | 同上 |
| **nDCG@K (mean)** | **首轮记录，不设阈值** | 同上 |
| **延迟 P50/P95** | **不超过 30s 总超时护栏** | 新格式 Chunk 走同一检索路径，延迟主要来自 Dense+Sparse+Rerank，与 002 基线同量级 |

### 0.3 硬性验收指标（必须 100% 不被违反）

| 硬性指标 | 002 基线 | 003 目标 | 验证方式 |
|---------|---------|---------|---------|
| 跨项目泄漏事件数 | 0 | **= 0**（SC-004 / FR-014） | 混合格式评测集断言 |
| MCP Schema 合法率 | 100% | **= 100%**（SC-005 / FR-015） | search_knowledge + get_evidence 输出契约校验 |
| 来源可定位率 | 100% | **= 100%**（SC-006 / FR-016） | 混合格式评测集断言（各格式来源位置标识） |

### 0.4 可重复性目标（SC-010）

- 同一环境连续两次运行的 Recall@K / MRR / nDCG 在 **1% 相对容差**内一致（沿用 001/002 非延迟可重复性要求）。
- 延迟指标标注为环境敏感，不作为可重复性否决项。

### 0.5 可解释性目标（FR-026）

- 对照评测报告逐格式记录新格式查询的 Recall@K、MRR、nDCG、P50/P95 延迟。
- 回归验证逐查询列出 002 基线排名 vs 003 回归排名，确认无变化或解释变化原因。

### 0.6 独立验收批次目标（SC-009 / 蓝图 §23.4 第 3 项）

- 每种新增格式可独立验收：上传单一格式材料 → 检索该格式内容 → 验证来源定位与硬约束满足。
- 一个格式的失败不阻塞其他格式的验收。

### 0.7 进入 plan 的判定

本节已声明全部可度量目标（轨道 A 回归不劣化 + 轨道 B 首轮记录 + 硬约束 100%/0 + 可重复性 + 可解释性 + 独立批次），**门禁通过，进入 plan.md**。

---

## 一、格式解析器技术选型

### 1.1 OpenAPI/Swagger 解析

**决策**: 使用 Python 标准库 `json` + `pyyaml` 解析 JSON/YAML 序列化格式，按 OpenAPI 结构树遍历 `paths` 与 `components/schemas` 生成端点级与 Schema 级 Chunk。

**理由**:
- OpenAPI/Swagger 是结构化 JSON/YAML 文档，不需要 AST 解析器——其结构本身就是语法树。
- `json` 是 Python 标准库，`pyyaml` 是 002 已引入的依赖（eval 配置使用 YAML），不新增依赖。
- 端点 Chunk 从 `paths.{path}.{method}` 提取，Schema Chunk 从 `components.schemas.{name}` 提取，父子关系通过 `$ref` 引用建立。

**替代方案考虑**:
- `openapi-spec-validator`：可验证规范合法性，但引入额外依赖且过于重量级；003 只需结构遍历+切片，规范验证由 FR-018 降级处理覆盖。
- 自定义正则提取：不可靠，OpenAPI 嵌套结构复杂。

**来源位置标识格式**:
- 端点: `{METHOD} {path}`（如 `GET /api/v1/users`）
- Schema: `schema:components.schemas.{name}`（如 `schema:components.schemas.User`）

**Swagger 2.0 兼容**: 蓝图 §7 说"OpenAPI/Swagger"，支持 Swagger 2.0（`swagger: "2.0"`）与 OpenAPI 3.x（`openapi: "3.x.x"`）。两者结构差异主要在 Schema 位置（Swagger 2.0 `definitions` vs OpenAPI 3.x `components.schemas`），解析器检测版本字段后选择对应路径。

---

### 1.2 DDL 解析

**决策**: 使用 `sqlparse` 库分割 SQL 语句流，按语句类型（CREATE TABLE / CREATE INDEX / CREATE VIEW / CREATE PROCEDURE / ALTER TABLE）分类，仅 DDL 语句产生 Chunk；DML（INSERT/UPDATE/DELETE）标注为未识别，不产生可检索 Chunk。

**理由**:
- `sqlparse` 是成熟的 SQL 解析库，支持语句分割与 token 级分类，不需要完整 SQL 语法树。
- 蓝图 §7 DDL 结构单元为表、字段、约束、索引、视图、存储过程——这些对应 DDL 语句类型。
- 澄清 Q1 确认：DML 不产生可检索 Chunk，知识库聚焦于 schema 定义。

**替代方案考虑**:
- `sqlglot`：支持完整 SQL AST 与方言转换，但更重；003 只需语句分割与表名/字段名提取，`sqlparse` 足够。
- 正则表达式：SQL 语法复杂（嵌套括号、多行语句），正则不可靠。

**来源位置标识格式**:
- 表: `table:{table_name}`（如 `table:users`）
- 字段: `table:{table_name}.column:{column_name}`（如 `table:users.column:email`）
- 索引: `index:{index_name}`
- 视图: `view:{view_name}`
- 存储过程: `procedure:{proc_name}`

**ANSI SQL 基本方言**: 支持 CREATE TABLE、CREATE INDEX、CREATE VIEW、CREATE PROCEDURE、ALTER TABLE、约束定义（PRIMARY KEY / FOREIGN KEY / UNIQUE / CHECK）。特定数据库私有语法（如 PostgreSQL 的 `CREATE EXTENSION`）标注为未识别。

**ALTER TABLE 处理**: `ALTER TABLE` 不产生独立 `chunk_type`（DDL 仅 `table`/`column`/`constraint`/`index`/`view`/`procedure`，见 format-locators.schema.json ddl 分支）。其结构效果拆解归属目标表：新增/修改字段 → `column` Chunk（`structure_path=table:{name}.column:{col}`，`parent_structure_path=table:{name}`）；新增命名约束 → `constraint` Chunk（`constraint:{name}`，`parent=table:{name}`）。若目标表在当前文件未先 CREATE，仍按目标表名生成 `column`/`constraint` Chunk，`parent_structure_path` 指向 `table:{name}`（表 Chunk 缺失时不阻塞，沿用 001 父级路径不可解析时不设 `parent_chunk_id` 行为，data-model §3.3）。

---

### 1.3 Go AST 解析

**决策**: 使用 `tree-sitter-go`（Go 语言绑定），与 001 Java 解析器的 `tree-sitter-java` 方案一致。

**理由**:
- 001 已建立 tree-sitter 解析框架（`java_parser.py` 使用 `tree_sitter.Language` + `Parser`），Go 复用同一框架，保持一致性。
- tree-sitter 提供增量解析、错误恢复（语法错误时仍可提取部分 AST），支持 001 的降级策略（FR-017）。
- tree-sitter-go 是官方维护的 Go 语法树绑定，覆盖 Go 1.x 语法。

**替代方案考虑**:
- Go 官方 `go/parser` + `go/ast`：需要 Go 工具链运行，引入 Go 运行时依赖，不适合纯 Python 后端。
- 正则提取：无法可靠处理嵌套结构体、接口嵌入等。

**来源位置标识格式**:
- 函数: `{package}.{function_name}`
- 方法: `{package}.{receiver_type}#{method_name}`（如 `pkg.Service#Method`）
- 类型: `{package}.{type_name}`
- 接口: `{package}.{interface_name}`（与类型同形，如 `pkg.Reader`；对齐 format-locators.schema.json Go 分支与 §4.1 规范总表）

包名从 AST `package_declaration` 提取（与 001 Java 的 `package_declaration` 提取方式一致）。

---

### 1.4 Python AST 解析

**决策**: 使用 Python 标准库 `ast` 模块。

**理由**:
- Python 标准库 `ast` 内置于 CPython，零外部依赖，无需安装任何包。
- `ast.parse()` 直接生成完整 AST，支持 Python 3.x 语法（包括类型注解、装饰器、async 函数）。
- 与 001 的 tree-sitter 方案不同但合理：Python 解析 Python 源码使用原生 ast 模块是最自然的选择，避免为 Python 引入 tree-sitter-python 的不必要依赖。

**替代方案考虑**:
- `tree-sitter-python`：与 Go/Java 一致，但 Python 标准库 ast 更成熟且零依赖，没有必要引入 tree-sitter。
- `libcst`：面向代码转换（Codemod），切片场景不需要保留格式精确性。

**来源位置标识格式**:
- 模块级函数: `{module_name}.{function_name}`
- 嵌套函数: `{module_name}.{outer_func}.{inner_func}`（各段均小写，与模块级函数同构，允许多层嵌套，如 `utils.outer.inner`）
- 类: `{module_name}.{class_name}`
- 嵌套类: `{module_name}.{outer_class}.{inner_class}`（大写段，允许多层嵌套，如 `models.Outer.Inner`）
- 方法: `{module_name}.{class_name}.{method_name}`（如 `module.Class.method`；嵌套类方法在其上追加类层，如 `module.Outer.Inner.method`）

模块名从文件名提取（`path/to/module.py` → `module`），与 001 Java 从 AST `package_declaration` 提取包名的策略一致但来源不同（Python 的模块身份是文件名而非显式声明）。

---

### 1.5 Word OOXML 解析

**决策**: 使用 `python-docx` 库解析 `.docx`（OOXML）文档，按标题层次、段落、列表和表格结构切片。

**理由**:
- `python-docx` 是成熟的 Python OOXML 解析库，支持标题层次（Heading styles）、段落、列表和表格提取。
- `.docx` 是 ZIP 压缩的 XML 文档，`python-docx` 封装了 XML 解析，提供 Python 友好的 API。
- 蓝图 §7 要求 Word 按标题、段落、列表、表格结构切分——`python-docx` 的 API 直接映射这些结构单元。

**替代方案考虑**:
- 手动解析 OOXML XML：复杂且易错，`python-docx` 已封装。
- `docx2txt`：仅提取纯文本，丢失标题层次和表格结构，不满足来源定位要求。

**来源位置标识格式**:
- 标题路径: `## {heading1} > ### {heading2}`（与 Markdown 章节路径格式一致，蓝图 §7 将 Markdown/Word/PDF 归为同一切片策略）

**嵌入对象处理**: `python-docx` 可检测嵌入对象（图片、OLE 对象），spec 边缘案例（Word 嵌入对象）要求跳过无法提取文本的嵌入对象并继续处理文本内容，不因嵌入对象中断整个文档处理。

**无标题文档兜底**: 当 `.docx` 无任何标题（Heading）时，解析器合成文档级根标题 `# {filename}`（如 `# design.docx`）作为 `paragraph`/`list`/`table` Chunk 的 `section_path` 兜底，使来源位置标识匹配 word 分支正则 `^#{1,6} .+(?: > #{1,6} .+)*$`（与 Markdown 无标题兜底策略一致）；不产生空 `section_path`（data-model §3.2 非空约束）。

---

### 1.6 PDF 文本提取（含多栏布局检测）

**决策**: 使用 `pdfplumber` 库提取 PDF 文本与页面布局信息，支持多栏阅读顺序保留（澄清 Q2 确认首轮即需栏感知提取）。

**理由**:
- `pdfplumber` 基于 `pdfminer.six`，提供页面级文本提取、坐标信息和布局分析。
- 支持多栏布局检测：通过分析文本块的 x 坐标分布识别栏边界，按阅读顺序（左栏从上到下 → 右栏从上到下）输出文本。
- 澄清 Q2 确认首轮即需栏感知提取——`pdfplumber` 的布局分析能力满足此要求。
- 当栏布局无法检测时（不规则布局、嵌套栏），降级为线性提取并标注降级原因（边缘案例）。

**替代方案考虑**:
- `PyMuPDF (fitz)`：性能更好但布局分析 API 不如 `pdfplumber` 直观；多栏检测需要更多自定义代码。
- `pdfminer.six`：`pdfplumber` 的底层引擎，直接使用 API 更低层。
- `pypdf`：仅提取文本，无布局分析，不支持多栏检测。

**来源位置标识格式**:
- 标题路径 + 页码: `page:{N} §{section_path}`（如 `page:12 §3.2 数据流`）
- 无标题时: `page:{N}`（仅页码）

**栏检测实现策略**:
1. 提取页面所有文本块的坐标信息（x0, x1, y0, y1, text）。
2. 按 y 坐标分组成行，分析每行的 x 坐标分布。
3. 如果存在明显的 x 坐标间隙（栏间距），识别为多栏布局。
4. 按栏排序文本块：左栏从上到下，然后右栏从上到下。
5. 如果无法识别栏边界（间隙不明显或布局不规则），降级为线性提取（从上到下、从左到右）并记录降级原因。

---

## 二、数据模型扩展决策

### 2.1 KnowledgeSource.format 字段扩展

**决策**: 扩展 001 已有的 `format` 字段（当前值为 `markdown`/`java`），新增 6 个值：`openapi`/`ddl`/`go`/`python`/`word`/`pdf`。

**理由**: format 是知识源属性（FR-022），不是版本能力属性。新增格式不需要新的能力标志——新格式 Chunk 自动获得 Dense 与 Sparse 索引（复用 001/002 的嵌入与 Sparse 编码流程）。

**检测策略**: 上传时先按扩展名预判，再按内容验证（FR-010）：
- `.json`/`.yaml`/`.yml` → 检查是否含 `openapi`/`swagger` 字段 → `openapi` 或拒绝
- `.sql` → `ddl`
- `.go` → `go`
- `.py` → `python`
- `.docx` → `word`
- `.pdf` → `pdf`
- `.md` → `markdown`（001 已有）
- `.java` → `java`（001 已有）
- 其他 → 拒绝并说明原因

### 2.2 Chunk 来源位置标识字段

**决策**: 复用 001 已有的 `section_path`/`symbol_path` 字段模式，新增格式按结构类型映射：

| 格式 | 位置路径字段 | 父级路径字段 | chunk_type 值 |
|------|------------|------------|--------------|
| OpenAPI 端点 | `structure_path` | `parent_structure_path` | `endpoint` |
| OpenAPI Schema | `structure_path` | `parent_structure_path` | `schema` |
| DDL 表 | `structure_path` | `parent_structure_path` | `table` |
| DDL 字段 | `structure_path` | `parent_structure_path` | `column` |
| Go 符号 | `symbol_path` | `parent_symbol_path` | `function`/`method`/`type`/`interface` |
| Python 符号 | `symbol_path` | `parent_symbol_path` | `function`/`class`/`method` |
| Word 标题 | `section_path` | `parent_section_path` | `heading`/`paragraph`/`list`/`table` |
| PDF 标题 | `section_path` | `parent_section_path` | `heading`/`paragraph` |

**理由**: 001 的 `backfill_parent_chunk_ids()` 函数已支持 `section_path` 和 `symbol_path` 两种键。新增 `structure_path` 作为第三种键，扩展 `backfill_parent_chunk_ids()` 支持该键。Go/Python 沿用 `symbol_path`（与 Java 一致），Word/PDF 沿用 `section_path`（与 Markdown 一致），OpenAPI/DDL 使用新的 `structure_path`。

### 2.3 ProcessingRun.stages 扩展

**决策**: 在 002 已有的 `credential_scan` → `parsing` → `chunking` → `embedding` → `sparse_index` 流程中，新增二进制格式文本提取阶段：

- 纯文本格式（OpenAPI/DDL/Go/Python）：流程不变（`credential_scan` → `parsing` → ...）
- 二进制格式（Word/PDF）：在 `credential_scan` 前新增 `text_extraction` 阶段（`text_extraction` → `credential_scan` → `parsing` → ...）

**理由**: FR-011 要求二进制格式在凭据规范化之前提取文本内容。提取后的文本进入与纯文本格式相同的后续流程。

---

## 三、契约变更决策

### 3.1 MCP 对外契约（不变）

**决策**: 003 不修改 `search_knowledge` 与 `get_evidence` 的对外契约与 Schema（宪法原则 VII：接口独立演进）。

**理由**: 新格式 Chunk 通过既有 MCP 契约返回。`SourcePosition` 字段（002 `common.schema.json` 中定义为 `type: string`）已支持任意位置路径字符串，各格式的来源位置标识作为该字段的值返回，不需要修改 Schema 定义。

### 3.2 common.schema.json（复用 + 描述扩展）

**决策**: 复用 002 的 `common.schema.json`（与 001 内容一致），仅在 `SourcePosition` 的 description 中补充各格式来源位置标识示例。不修改 Schema 结构定义。

**理由**: `SourcePosition` 已是 `type: string`（无 pattern 约束），天然支持新增格式的位置路径。补充 description 是文档级变更，不破坏兼容性。

### 3.3 新增契约：format-locators.schema.json

**决策**: 在 `contracts/` 目录新增 `format-locators.schema.json`，定义各格式来源位置标识的规范格式与验证规则，供验收测试集校验来源可定位率（硬约束 100%）。

**理由**: 硬约束要求来源可定位率 100%，需要一个可校验的规范来定义"什么算有效的来源位置"。该契约不是 MCP 对外契约（不改变 Tool 响应 Schema），而是验收测试的内部校验规范。

---

## 四、来源位置标识格式规范

### 4.1 规范总表

| 格式 | 结构单元 | 来源位置标识格式 | 示例 |
|------|---------|----------------|------|
| Markdown | 章节 | `{heading_path}` | `## 安装 > ### 配置` |
| Java | 类 | `{fqcn}` | `com.example.Service` |
| Java | 方法 | `{fqcn}#{member}` | `com.example.Service#methodName` |
| OpenAPI | 端点 | `{METHOD} {path}` | `GET /api/v1/users` |
| OpenAPI | Schema | `schema:components.schemas.{name}` | `schema:components.schemas.User` |
| DDL | 表 | `table:{name}` | `table:users` |
| DDL | 字段 | `table:{table}.column:{col}` | `table:users.column:email` |
| DDL | 命名约束 | `constraint:{name}` | `constraint:pk_users` |
| DDL | 索引 | `index:{name}` | `index:idx_users_email` |
| DDL | 视图 | `view:{name}` | `view:active_users` |
| DDL | 存储过程 | `procedure:{name}` | `procedure:calculate_stats` |
| Go | 函数 | `{pkg}.{func}` | `pkg.ProcessData` |
| Go | 方法 | `{pkg}.{type}#{method}` | `pkg.Service#Method` |
| Go | 类型 | `{pkg}.{type}` | `pkg.Service` |
| Python | 函数 | `{module}.{func}` | `utils.parse_config` |
| Python | 嵌套函数 | `{module}.{outer_func}.{inner_func}` | `utils.outer.inner` |
| Python | 类 | `{module}.{class}` | `models.User` |
| Python | 嵌套类 | `{module}.{outer_class}.{inner_class}` | `models.Outer.Inner` |
| Python | 方法 | `{module}.{class}.{method}` | `models.User.validate`（含嵌套类方法 `models.Outer.Inner.validate`） |
| Word | 标题 | `{heading_path}` | `## 架构设计 > ### 数据流` |
| PDF | 标题+页码 | `page:{N} §{heading_path}` | `page:12 §3.2 数据流` |
| PDF | 仅页码 | `page:{N}` | `page:5` |

### 4.2 父子关系映射

| 格式 | 子 Chunk | 父 Chunk | 关系 |
|------|---------|---------|------|
| OpenAPI | 引用端点（子） | 被引用 Schema 定义（父） | $ref 引用（主引用，见下表后规则） |
| DDL | 字段/约束 | 所属表 | 结构包含 |
| DDL | 索引 | 所属表 | 结构包含 |
| Go | 方法 | 所属类型 | 结构包含 |
| Python | 方法 | 所属类 | 结构包含 |
| Python | 嵌套类 | 外层类 | 结构包含 |
| Word | 段落/列表/表格 | 所属标题 | 章节包含 |
| PDF | 段落 | 所属标题/页码 | 章节包含 |

**OpenAPI 父子（主引用）规则**：端点引用多个 Schema（请求体/响应/参数）时，端点 Chunk 的 `parent_structure_path` 取请求体（requestBody `$ref`）引用的 Schema；无请求体引用时取响应引用的首个 Schema（按引用出现顺序）；无 Schema 引用时为空。Schema Chunk 自身 `parent_structure_path` 为空（顶级定义）。全部被引用 Schema 名保留在端点 Chunk 内容中（`$ref` 标记保留），父子索引仅记录主引用。该方向与 spec US1-AC2（父子关系关联 Schema 定义与其引用端点）和 US1-AC3（端点作为子 Chunk，经父级 Schema 上下文恢复完整定义）一致。

---

## 五、依赖清单

### 5.1 新增 Python 依赖

| 依赖 | 用途 | 格式 | 是否标准库 |
|------|------|------|-----------|
| `pyyaml` | YAML 解析 | OpenAPI | 否（002 已引入） |
| `sqlparse` | SQL 语句分割 | DDL | 否（新增） |
| `tree-sitter-go` | Go AST 解析 | Go | 否（新增） |
| `python-docx` | OOXML 解析 | Word | 否（新增） |
| `pdfplumber` | PDF 文本提取+布局 | PDF | 否（新增） |

Python `ast` 模块用于 Python 源代码解析，是标准库，无需安装。

### 5.2 复用依赖（不新增）

| 依赖 | 用途 | 来源 |
|------|------|------|
| `markdown-it-py` | Markdown 解析 | 001 |
| `tree-sitter-java` | Java AST 解析 | 001 |
| `BAAI/bge-m3` | Dense 嵌入 | 001 |
| `BAAI/bge-reranker-v2-m3` | Rerank | 002 |
| `jieba` | CJK 分词 | 002 |

---

## 六、降级与失败策略汇总

| 场景 | 策略 | 依据 |
|------|------|------|
| Go 语法错误无法形成 AST | 降级为行级切片或报错，不伪造符号边界 | FR-017 / 沿用 001 Java 降级 |
| Python 语法错误 | `ast.parse` 抛出 SyntaxError，报错并说明原因 | FR-017 |
| OpenAPI 规范不合法 | 报错并说明原因（缺版本字段/语法错误） | FR-018 |
| DDL 含不支持方言 | 按可识别语句切分，标注未识别部分 | FR-018 / 边缘案例 |
| Word 空文档/损坏 | 报错并说明原因，不产生空 Chunk | FR-019 |
| PDF 扫描版（纯图像） | 报错"不支持的格式"并拒绝处理 | FR-019 / 边缘案例 |
| PDF 多栏检测失败 | 降级为线性提取并标注降级原因 | 边缘案例 |
| 解析产生空 Chunk | 不发布版本，旧版本继续可用 | FR-020 / 沿用 001 |
