# Quickstart 验证指南：003 Structured Asset Expansion

**Feature**: 003-structured-asset-expansion
**目的**: 提供可运行的端到端验证场景，证明 6 种新格式材料的结构切片、来源定位与硬性合规，以及与 001/002 基线的无回归验证。
**关联**: [plan.md](./plan.md) | [research.md](./research.md)（评测目标 §0）| [data-model.md](./data-model.md) | [contracts/](./contracts/)

> 本指南为验证/运行指南，不包含完整实现代码；实现细节见后续 `tasks.md` 与实现阶段。

---

## 1. 前置条件

1. **001/002 闭环已运行**：PostgreSQL + Qdrant 已启动（`docker compose up`），001/002 已入库 Markdown/Java 知识并产出 `eval/baseline_report.json` 与 `eval/hybrid_comparison_report.json`。
2. **模型就绪**：`BAAI/bge-m3`（Dense，001 已用）与 `BAAI/bge-reranker-v2-m3`（Rerank，002 已用）可在本地 CPU 加载。
3. **新增依赖**：
   ```bash
   pip install tree-sitter-go python-docx pdfplumber sqlparse
   ```
   `pyyaml`（002 已引入）、`tree-sitter`（001 已引入）不重复安装。Python `ast` 为标准库。
4. **配置**：沿用 001/002 配置，无新增配置项（003 不引入新检索能力标志或新超时参数）。

---

## 2. 批次 1 验证：Go + Python AST 符号切片

**目的**: 验证 Go 与 Python 源代码的 AST 符号感知切片、来源定位与检索（FR-003/FR-004/SC-001/SC-006）。

### 2.1 上传 Go 源代码

**步骤**:
1. 创建测试项目：
   ```bash
   curl -X POST http://127.0.0.1:8000/api/projects -H "Content-Type: application/json" -d '{"alias": "go-test"}'
   ```
2. 上传 Go 文件（含包声明、结构体、方法）：
   ```bash
   curl -X POST http://127.0.0.1:8000/api/knowledge-sources \
     -F "project_ref=go-test" -F "file=@service.go"
   ```
3. 等待处理完成，查看处理阶段：
   ```bash
   curl http://127.0.0.1:8000/api/knowledge-sources/{id}/runs
   ```

**预期结果**:
- `format` 字段为 `go`
- `parsing` 阶段产生 ≥ 1 个 Chunk（`chunk_type` 为 `function`/`method`/`type`/`interface`）
- `credential_scan` 阶段完成（复用 001 凭据规范化）
- 版本声明 `dense_ready` + `lexical_ready`（复用 002 Sparse 能力）
- 无 `text_extraction` 阶段（纯文本格式跳过）

### 2.2 检索 Go 符号

**步骤**:
```bash
curl -X POST http://127.0.0.1:8000/mcp/search_knowledge \
  -H "Content-Type: application/json" \
  -d '{"query": "pkg.Service#Method", "project_scope": ["go-test"]}'
```

**预期结果**:
- `completion_status` 为 `complete`
- 返回证据的 `source_position` 匹配 `pkg.Service#Method` 格式（[format-locators.schema.json](./contracts/format-locators.schema.json) Go method 模式）
- 证据携带 `source_version` 和可定位的来源位置

### 2.3 上传 Python 源代码并检索

重复 2.1-2.2 步骤，上传 `.py` 文件，检索 `module.Class.method`，验证 `source_position` 匹配 Python method 模式。

---

## 3. 批次 2 验证：OpenAPI + DDL 结构切片

### 3.1 上传 OpenAPI/Swagger 规范

**步骤**:
1. 上传 OpenAPI JSON 文件：
   ```bash
   curl -X POST http://127.0.0.1:8000/api/knowledge-sources \
     -F "project_ref=api-test" -F "file=@openapi.json"
   ```
2. 等待处理完成。

**预期结果**:
- `format` 字段为 `openapi`
- 产生端点 Chunk（`chunk_type=endpoint`，`structure_path` 为 `GET /api/v1/users` 格式）
- 产生 Schema Chunk（`chunk_type=schema`，`structure_path` 为 `schema:components.schemas.User` 格式）
- 端点与引用 Schema 之间有父子关系

### 3.2 检索 OpenAPI 端点

```bash
curl -X POST http://127.0.0.1:8000/mcp/search_knowledge \
  -d '{"query": "GET /api/v1/users", "project_scope": ["api-test"]}'
```

**预期结果**: 证据 `source_position` 匹配 OpenAPI endpoint 模式。

### 3.3 上传 DDL 并验证 DML 不索引

**步骤**:
1. 上传含 DDL + DML 的 `.sql` 文件（如 migration script）：
   ```bash
   curl -X POST http://127.0.0.1:8000/api/knowledge-sources \
     -F "project_ref=db-test" -F "file=@schema.sql"
   ```

**预期结果**:
- `format` 字段为 `ddl`
- DDL 语句（CREATE TABLE 等）产生 Chunk（`chunk_type=table`/`column`）
- DML 语句（INSERT/UPDATE/DELETE）**不产生可检索 Chunk**（澄清 Q1 确认）
- 处理阶段记录标注了未识别的 DML 语句
- 检索表名返回正确 Chunk，`source_position` 匹配 `table:users` 格式

---

## 4. 批次 3 验证：Word + PDF 文档切片（含多栏）

### 4.1 上传 Word 文档

**步骤**:
1. 上传 `.docx` 文件（含多级标题、段落、列表、表格）：
   ```bash
   curl -X POST http://127.0.0.1:8000/api/knowledge-sources \
     -F "project_ref=doc-test" -F "file=@design.docx"
   ```

**预期结果**:
- `format` 字段为 `word`
- 处理阶段含 `text_extraction`（二进制格式，在 `credential_scan` 之前）
- 产生标题/段落/列表/表格 Chunk
- `source_position` 匹配 Word 标题路径格式（`## 架构 > ### 数据流`）

### 4.2 上传 PDF 文档（含多栏布局）

**步骤**:
1. 上传文本版 PDF（含多栏布局，如学术论文）：
   ```bash
   curl -X POST http://127.0.0.1:8000/api/knowledge-sources \
     -F "project_ref=pdf-test" -F "file=@paper.pdf"
   ```

**预期结果**:
- `format` 字段为 `pdf`
- `text_extraction` 阶段的 `details` 记录 `columns_detected: true`（澄清 Q2 确认首轮需栏感知提取）
- 多栏文本按正确阅读顺序进入 Chunk（非线性错乱）
- `source_position` 匹配 PDF 格式（`page:12 §3.2 数据流`）

### 4.3 验证扫描版 PDF 被拒绝

上传纯图像 PDF：
```bash
curl -X POST http://127.0.0.1:8000/api/knowledge-sources \
  -F "project_ref=pdf-test" -F "file=@scanned.pdf"
```
**预期结果**: 系统拒绝处理并报告"不支持的格式"原因（FR-019 / 边缘案例）。

---

## 5. 硬约束验证（混合格式评测集）

**目的**: 验证三条硬约束在包含全部 8 种格式的混合评测集上不被违反（SC-004/SC-005/SC-006）。

### 5.1 跨项目泄漏为零

**步骤**:
1. 创建两个项目，分别上传不同格式的材料。
2. 使用一个项目的 `project_scope` 查询另一个项目的格式内容。
3. 验证返回结果不包含作用域外项目的 Chunk。

**预期结果**: 跨项目泄漏事件数 = 0。

### 5.2 MCP Schema 合法率 100%

**步骤**:
1. 在混合格式评测集上运行所有查询。
2. 对每个 `search_knowledge` 和 `get_evidence` 响应进行 Schema 校验。

**预期结果**: Schema 合法率 = 100%。

### 5.3 来源可定位率 100%

**步骤**:
1. 对混合评测集的每个返回证据，使用 [format-locators.schema.json](./contracts/format-locators.schema.json) 校验 `source_position`。

**预期结果**: 来源可定位率 = 100%（每个 `source_position` 匹配对应格式的 pattern）。

---

## 6. 回归验证（无劣化）

**目的**: 验证新格式解析器的引入未造成 001/002 既有 Markdown/Java 检索回归（SC-002/FR-023）。

**步骤**:
```bash
python eval/run_eval.py --dataset eval/eval_dataset.json --output eval/regression_report.json
python eval/run_comparison.py --baseline eval/hybrid_comparison_report.json --current eval/regression_report.json
python eval/run_comparison.py --baseline eval/baseline_report.json --current eval/regression_report.json
```

**预期结果**:
- 原 18 条 Markdown/Java 查询的 Recall@K/MRR/nDCG 不劣于 001 Dense 基线与 002 混合检索基线（以 002 为主门禁：Recall@K 精确、MRR/nDCG 1% 相对容差，research.md §0.2 轨道 A）
- 逐查询排名无变化或变化可解释

---

## 7. 新格式评测（首轮记录）

**目的**: 在扩充后的评测集（原 18 + 新增 ≥ 12 条）上运行评测，记录新格式指标（SC-003/FR-024）。

**步骤**:
```bash
python eval/run_eval.py --dataset eval/eval_dataset.json --output eval/format_expansion_report.json
```

**预期结果**:
- 每种新格式查询产生 Recall@K/MRR/nDCG 指标
- 首轮记录指标数值，不设通过阈值（沿用 001 渐进策略）
- 对照报告逐格式记录指标与 001 基线对照

---

## 8. 独立验收批次（SC-009）

**目的**: 验证每种新增格式可独立验收。

**步骤**: 对每种格式单独执行：
1. 上传单一格式材料
2. 检索该格式内容
3. 验证来源定位（[format-locators.schema.json](./contracts/format-locators.schema.json)）
4. 验证硬约束（泄漏=0, Schema=100%, 可定位率=100%）

**预期结果**: 每种格式独立通过验收，一个格式的失败不阻塞其他格式。
