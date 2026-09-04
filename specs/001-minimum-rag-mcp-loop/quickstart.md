# Quickstart 验收验证指南: 001 Minimum RAG MCP Loop

**Branch**: `001-minimum-rag-mcp-loop` | **Date**: 2026-08-27

本文档定义了001版本首轮demo验收的可执行验证场景。每个场景对应spec中的成功准则（SC），包含前置条件、操作步骤和预期结果。

## 前置条件

### 环境要求

- Python 3.12+
- Node.js 20+ / pnpm
- PostgreSQL 16+（本地或Docker）
- Qdrant（本地或Docker）
- DeepSeek Harness（本设备已安装）

### 启动服务

```bash
# 1. 启动基础设施（本地）——远程环境则改为在 .env 中配置 DATABASE_URL / QDRANT_URL
docker compose up -d postgres qdrant

# 2. 初始化数据库
cd backend && alembic upgrade head

# 3. 启动管理面 REST API（Writer 模式，默认 127.0.0.1:8000）
python -m rag_mcp.server --mode writer --port 8000

# 3b. 启动 MCP 服务（Streamable HTTP，默认 127.0.0.1:8080）
python backend/_run_mcp.py

# 4. 启动前端开发服务器
cd frontend && pnpm dev

# 5. 配置DeepSeek Harness MCP连接
# 在DSH的MCP配置中添加：
# {
#   "mcpServers": {
#     "rag-knowledge": {
#       "url": "http://127.0.0.1:8080/mcp"
#     }
#   }
# }
```

## 验证场景

### VS-001: 项目创建与文件上传 (SC-001)

**前置条件**: 系统中无项目

**步骤**:
1. 打开浏览器访问 `http://localhost:5173`
2. 点击"创建项目"，输入项目名称和别名
3. 上传一份Markdown文档（含章节结构）
4. 上传一个Java源代码文件
5. 观察SSE推送的处理进度

**预期结果**:
- [x] 项目创建成功，显示项目ID和知识域ID
- [x] 两个文件均显示`uploaded` → `processing` → `published`状态流转
- [x] SSE实时推送处理进度事件
- [x] 发布成功后，知识版本声明`dense_ready`能力

---

### VS-002: 凭据值规范化 (SC-006)

**前置条件**: 准备包含密码/API Key的测试Markdown文件

**步骤**:
1. 上传包含以下内容的Markdown文件：
   ```
   数据库连接: password=MySecret123
   API配置: api_key=sk-abc123def456
   Token: bearer_token=eyJhbGciOiJIUzI1NiJ9...
   ```
2. 等待处理完成
3. 通过`search_knowledge`查询"数据库连接配置"
4. 检查返回的证据内容

**预期结果**:
- [x] 证据正文中不包含`MySecret123`、`sk-abc123def456`等原始值
- [x] 占位符保留了字段名（password, api_key, bearer_token）
- [x] 代码结构和来源位置完整保留

---

### VS-003: 项目作用域隔离 (SC-002)

**前置条件**: 创建两个项目A和B，分别上传不同内容

**步骤**:
1. 创建项目A，上传关于"用户认证模块"的文档
2. 创建项目B，上传关于"支付网关"的文档
3. 使用`search_knowledge`，project_scope仅指定项目A，查询"支付流程"
4. 使用`search_knowledge`，project_scope仅指定项目B，查询"用户登录"

**预期结果**:
- [x] 步骤3不返回项目B的任何证据
- [x] 步骤4不返回项目A的任何证据
- [x] 每条证据的`knowledge_scope_id`与请求的作用域一致

---

### VS-004: 缺少项目作用域拒绝 (FR-014)

**步骤**:
1. 调用`search_knowledge`，不提供`project_scope`参数

**预期结果**:
- [x] 系统返回错误响应，说明必须提供项目作用域
- [x] 不执行任何检索操作
- [x] 错误信息包含可纠正的提示

---

### VS-005: 跨项目检索 (FR-015)

**前置条件**: 项目A和项目B均已发布知识版本

**步骤**:
1. 调用`search_knowledge`，project_scope同时包含项目A和项目B
2. 查询一个与两个项目都相关的通用问题

**预期结果**:
- [x] 返回的证据仅来自项目A和项目B
- [x] 每条证据保留其`knowledge_scope_id`标识
- [x] 不返回其他项目的证据

---

### VS-006: 证据展开与作用域校验 (US-3)

**前置条件**: 已通过`search_knowledge`获得证据ID

**步骤**:
1. 使用正确的project_scope调用`get_evidence`展开证据
2. 使用错误的project_scope调用`get_evidence`尝试展开同一证据

**预期结果**:
- [x] 步骤1返回完整内容和父级上下文
- [x] 步骤2返回`scope_mismatch`状态，不返回正文

---

### VS-007: 知识源删除与清空 (SC-007)

**前置条件**: 项目包含多个已发布知识源

**步骤**:
1. 记录当前检索结果
2. 删除其中一个知识源
3. 立即执行相同查询
4. 等待删除完成后再次查询
5. 清空整个项目知识域
6. 查询该项目

**预期结果**:
- [x] 步骤3：被删除的知识源不再参与新检索
- [x] 步骤4：确认删除完成，派生数据已清理
- [x] 步骤6：项目返回无证据状态
- [x] 其他项目不受影响

---

### VS-008: 并发隔离 (SC-008)

**步骤**:
1. 同时发起5个并发请求：
   - 2个不同项目的`search_knowledge`
   - 2个不同证据的`get_evidence`
   - 1个Web管理API请求
2. 收集所有响应

**预期结果**:
- [x] 所有5个请求均正确完成
- [x] 每个响应的project_scope与请求一致
- [x] 无证据串扰或状态污染

---

### VS-009: Schema校验 (SC-004)

**步骤**:
1. 对`search_knowledge`和`get_evidence`的所有成功响应
2. 使用`contracts/mcp-search-output.schema.json`和`contracts/mcp-get-evidence.schema.json`进行JSON Schema校验

**预期结果**:
- [x] 所有响应通过Schema校验
- [x] `structuredContent`与TextContent内容一致

---

### VS-010: DeepSeek Harness端到端 (SC-005)

**前置条件**: DSH已配置MCP连接

**步骤**:
1. 在DeepSeek Harness中发起对话
2. 让Agent调用`search_knowledge`查询已入库的项目知识
3. 让Agent调用`get_evidence`展开某条证据

**预期结果**:
- [x] DSH成功发现并调用两个Tool
- [x] 返回的证据内容可被Agent正常使用
- [x] Schema校验通过

---

### VS-011: 评测基线产出 (SC-009)

**前置条件**: 评测集已准备（AI生成+人工审核）

**步骤**:
1. 运行评测脚本：`python -m rag_mcp.eval --dataset eval_dataset.json`
2. 查看输出的基线报告

**预期结果**:
- [x] 产出Recall@K、MRR、nDCG数值
- [x] 产出P50/P95延迟数值
- [x] 结果可重复执行且数值稳定

---

### VS-012: 知识源重处理 (蓝图 §5)

**前置条件**: 一个已发布的知识源

**步骤**:
1. 记录当前知识源的version_id和Chunk数量
2. 调用 `POST /api/knowledge-sources/{id}/reprocess`
3. 观察SSE推送的处理进度
4. 等待处理完成

**预期结果**:
- [x] 创建新的ProcessingRun（run_type=retry）
- [x] 生成新的KnowledgeVersion（version_number递增）
- [x] 旧版本保持可检索直到新版本发布成功
- [x] 新版本发布后，旧版本状态变为superseded
- [x] Chunk内容与原始文件一致（派生数据可从知识源重建）

---

### VS-013: 四类终态区分 (SC-010)

**步骤**:
1. 查询一个有明确答案的问题 → 验证`complete`
2. 查询一个部分覆盖的问题 → 验证`partial` + gaps
3. 查询一个系统中不存在相关知识的问题 → 验证`no_evidence`
4. 模拟系统异常（如Qdrant不可用） → 验证`failed` + error
5. 使用模糊项目引用 → 验证`AMBIGUOUS_PROJECT_REF` + candidates

**预期结果**:
- [x] 四种状态的`completion_status`字段值正确
- [x] `partial`状态携带非空gaps数组
- [x] `no_evidence`状态evidence数组为空，无error
- [x] `failed`状态携带error对象
- [x] 模糊引用返回候选项目列表
- [x] 不以生成内容填补缺口

## 验收判定

所有VS-001至VS-013的检查项全部通过即视为001版本demo验收通过。

ChatGPT App和Claude Code的兼容性状态记录在单独的兼容性报告中，不作为验收阻塞项。
