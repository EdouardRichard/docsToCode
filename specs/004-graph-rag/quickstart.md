# Quickstart: Graph RAG (004)

**Branch**: `004-graph-rag` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

> 端到端验证场景，证明 004 图增强检索可工作并通过验收。本文为验证/运行指南，不含完整实现代码、迁移脚本或测试套件（详见 tasks.md 与实现阶段）。契约引用见 [contracts](../003-structured-asset-expansion/contracts/)，数据模型见 [data-model.md](./data-model.md)，评测目标见 [research.md §0](./research.md)。

## 前置条件

- 001/002/003 已交付并可运行：Web 管理、Markdown/Java 上传与切片、Dense+Sparse+RRF+Rerank 混合检索、MCP `search_knowledge`/`get_evidence`、DDL 切片。
- PostgreSQL 可用（蓝图 §8.2，图节点/边/硬软关系存储）。
- 评测基线工件存在：`eval/baseline_report.json`（001 Dense）、`eval/hybrid_comparison_report.json`（002 混合）。
- 本机已安装 DeepSeek Harness（必过参考客户端，SC-012）。
- 已发布一个声明 `graph_ready` 的知识版本（Java 调用图 + DDL 外键语料已触发重建）。

## 场景 1 — 图关系构建与 graph_ready 发布

**验证**：FR-001/FR-013/FR-016/SC-010。

1. 对已有混合能力知识源（Java + DDL）触发重建，发布声明 `graph_ready` 的新版本（FR-027）。
2. 期望：系统从已切片 Java Chunk 确定性提取 `calls`/`called_by` 硬边，从 DDL Chunk 提取 `fk_references`/`fk_referenced_by` 硬边，写入 `graph_edge`（[data-model §2](./data-model.md)）。
3. 期望：新版本能力清单 `graph_ready=true`，且图关系就绪后才变为可检索状态；未就绪版本不变可检索（FR-013）。
4. 校验：能力清单符合 [knowledge-capabilities.graph-extension.schema.json](../003-structured-asset-expansion/contracts/knowledge-capabilities.graph-extension.schema.json)（graph_ready 隐含 dense+lexical）。
5. 重建校验：删除图派生数据后可从原始知识源 + 版本信息重建全部 `graph_edge`（FR-016）。

## 场景 2 — 图增强检索端到端（调用者/被调用者召回）

**验证**：User Story 1/FR-006/FR-007/FR-008/FR-011/SC-004/SC-005/SC-009。

1. 经 MCP `search_knowledge` 携带显式 `project_scope` 查询某 Java 方法的调用上下文（如 `validateToken` 调用者/被调用者）。
2. 期望：图扩展默认双向遍历 `calls`+`called_by`（research §3），1~3 跳护栏内召回调用者与被调用者证据，每条硬关系证据标记为可验证证据并携带来源 ID/版本/位置。
3. 期望：图候选进入 RRF 融合池作第 3 路输入、统一 Rerank（research §2）；图扩展路径（跳序列/关系类型/方向/结构权重）记录于 [graph-expansion-trace.schema.json](../003-structured-asset-expansion/contracts/graph-expansion-trace.schema.json)。
4. 期望：返回响应 100% 通过 `search_knowledge`/`get_evidence` 输出 Schema 校验（004 不改对外契约，FR-011）；证据 100% 可定位（SC-005）。
5. 期望：硬关系证据与软关系证据在结果中可区分标注（SC-009）；软关系携带五项元数据且不静默覆盖硬关系。

## 场景 3 — DDL 外键硬关系召回

**验证**：User Story 2/FR-001/FR-010。

1. 携带显式 `project_scope` 查询"引用 users 表的表"。
2. 期望：沿 `fk_references`/`fk_referenced_by` 边 1~3 跳扩展，召回引用方表与级联字段证据，携带外键关系路径。
3. 跨项目校验：仅携带单项目作用域时，跨项目图边不产生返回证据（场景 4）。

## 场景 4 — 跨项目隔离（泄漏=0）

**验证**：FR-009/FR-010/SC-003，宪法硬约束。

1. 两个独立项目各自声明 `graph_ready`；仅携带项目 A 作用域查询。
2. 期望：图扩展只在 A 的 `(knowledge_scope_id, project_id, index_version)` 内沿边扩展；项目 B 的图边不产生返回证据。
3. 校验：验收测试集中跨项目泄漏事件数 = 0（[eval-graph-comparison-report](../003-structured-asset-expansion/contracts/eval-graph-comparison-report.schema.json) `hard_constraints.cross_project_leakage_events=0`）。
4. 无 `project_scope` 请求 MUST 被拒绝（宪法硬约束）。

## 场景 5 — 降级与四态（partial / failed）

**验证**：FR-018/SC-011，Edge Cases。

1. 模拟图扩展子步骤超时（图扩展子超时 3s 触发），而混合检索已有可靠证据。
2. 期望：返回 `partial` 状态，携带已验证证据与失败路径信息（`failed_paths` 含 `graph_recall_timeout`），不返回空结果或伪造证据。
3. 模拟系统异常无法形成可靠证据：返回 `failed`。
4. 模拟完全无可靠证据但非异常：返回 `no_evidence`。
5. 校验四态可区分、可操作（沿用 001/002）。

## 场景 6 — 对照评测与三段通过判定

**验证**：FR-021/FR-022/FR-023/FR-024/FR-025/SC-001/SC-002/SC-007/SC-013，宪法原则 X。

1. 在 001/002/003 既有评测集上新增 ≥ 6 条结构性受益查询（含 ≥ 1 条中文，FR-021），原既有查询保留。
2. 同一环境会话内先重跑混合基线、再运行图增强路径（FR-025，延迟公平）。
3. 产出图增强对照报告（[eval-graph-comparison-report.schema.json](../003-structured-asset-expansion/contracts/eval-graph-comparison-report.schema.json)）。
4. 三段通过判定：
   - SC-001：结构性子集 MRR/nDCG 相对 002 混合基线 ≥ 3% 相对提升、Recall@K 不下降。
   - SC-002：001 11 条 Recall@K 精确持平、MRR/nDCG 非劣（1% 容差）。
   - SC-013：002 原有非结构性查询 MRR/nDCG 非劣、Recall@K 不下降。
   - 硬性指标：泄漏=0、Schema 合法率=100%、来源可定位率=100%。
5. 期望：`three_gate_pass.all_passed=true` → `enters_default_path=true`；未达则图扩展作为可选路径保留、不进默认路径。
6. 可重复性：连续两次运行非延迟指标在 1% 相对容差内一致（SC-007）。

## 场景 7 — DeepSeek Harness 端到端

**验证**：FR-028/SC-012。

1. 在 DeepSeek Harness 中调用图增强 `search_knowledge` 与 `get_evidence`。
2. 期望：端到端调用成功、输出通过 Schema 校验。
3. 期望：单次调用总超时 30s 护栏低于目标 Host 最低 Tool Call 超时预算（蓝图 §19）。
4. ChatGPT App / Claude Code 仅记录兼容性状态、不作 004 验收阻塞项。

## 运行命令（示意）

```bash
# 重建并发布 graph_ready 版本（复用 001 管理 API）
# 运行图增强对照评测（复用 002 评测 runner，扩展图增强路径）
# 校验契约
#   - 能力清单：knowledge-capabilities.graph-extension.schema.json
#   - 图边数据：graph-relations.schema.json
#   - 检索追踪：graph-expansion-trace.schema.json
#   - 评测报告：eval-graph-comparison-report.schema.json
```

> 具体命令、迁移与完整测试套件由 tasks.md 与实现阶段提供；本指南只约束可观测的期望结果与验收口径。
