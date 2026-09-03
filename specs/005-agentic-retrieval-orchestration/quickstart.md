# Quickstart: Agentic Retrieval Orchestration (005)

**Branch**: `005-agentic-retrieval-orchestration` | **Date**: 2026-09-02 | **Spec**: [spec.md](./spec.md)

> 端到端验证指南。契约引用见 [contracts](./contracts/)，数据模型见 [data-model.md](./data-model.md)，评测目标见 [research.md §0](./research.md)。本指南只约束可观测的期望结果与验收口径；具体命令、迁移与完整测试套件由 tasks.md 与实现阶段提供。

## 前置条件

- 001/002/003/004 已交付并可运行：Web 管理、Markdown/Java 上传与切片、Dense+Sparse+RRF+Rerank 混合检索、MCP `search_knowledge`/`get_evidence`、DDL 切片、图扩展（004 `graph_ready`）。
- PostgreSQL 可用（蓝图 §8.2，Agent 编排运行期表存储）。
- 评测基线工件存在：`eval/baseline_report.json`（001 Dense）、`eval/hybrid_comparison_report.json`（002 混合）。
- 已发布一个声明 `graph_ready` 的知识版本（Java 调用图 + DDL 外键语料已触发重建，使多跳图信号可用）。
- 本机已安装 DeepSeek Harness（必过参考客户端，SC-012）。
- 运行配置已声明三 Agent 模型路由（查询规划低延迟、证据分析更强、上下文编排居中）与护栏默认值（轮次 2/上限 3、节点超时 5s/上限 10s、装箱 top_k ≤ 20、单来源上限 3/上限 5、总超时 30s、图护栏沿用 004）。

## 场景 1 — 多跳查询被拆解后获更完整证据

**验证**：User Story 1/FR-001/FR-005/FR-033/SC-009。

1. 经 MCP `search_knowledge` 携带显式 `project_scope` 查询 Java 调用图多跳问题（如"哪些服务调用 `UserService#validateToken` 且 validateToken 自身依赖什么"）。
2. 期望：查询规划 Agent 拆解为可追溯子问题（`sub_problem_id` 单调递增），选择 Dense/Sparse/图扩展信号与关系方向（默认双向 `calls`+`called_by`），并行检索后返回覆盖各子问题的证据。
3. 期望：每条返回证据在追加式账本（[evidence-ledger-entry.schema.json](./contracts/evidence-ledger-entry.schema.json)）中携带 `round_index`/`sub_problem_id`/`retriever`/`score`，可按子问题分组追溯（SC-009）。
4. 跨项目校验：仅携带单项目作用域时，跨项目证据不产生返回（场景 4）。

## 场景 2 — 证据缺口触发有界补充检索

**验证**：User Story 2/FR-013/FR-014/FR-015/SC-011。

1. 构造首轮可验证缺口查询（期望证据在首轮 Dense/Sparse 召回中排名靠后）。
2. 期望：证据分析 Agent 输出 `coverage_state=partial`/`uncovered` 与 `needs_supplementary=true`（[agent-judgment.schema.json](./contracts/agent-judgment.schema.json)）；确定性控制器据此在 `max_rounds` 内触发补充检索。
3. 期望：查询规划 Agent 携带缺口上下文重新生成补充查询（循环步骤 6→3→4→5→6→7），补充召回证据**重新进入融合/Rerank/证据分析**、携带融合/Rerank 分数；最终 Recall@K 高于确定性单轮基线。
4. 期望：`agentic_retrieval_run.rounds_completed` 记录实际轮次（≤ `max_rounds`=2）；无缺口或达上限后进入上下文编排（[agentic-retrieval-run.schema.json](./contracts/agentic-retrieval-run.schema.json)）。
5. 降级校验：模拟证据分析 Agent 输出未通过 Schema 校验（`schema_valid=false`），期望系统回退该角色确定性等价行为并仍返回有效四态（SC-011）。

## 场景 3 — 上下文编排去重、保多样、可解释

**验证**：User Story 3/FR-008/FR-017/FR-018/SC-006。

1. 对返回多条重叠证据的查询运行 Agent 路径。
2. 期望：上下文编排 Agent 输出 `context_result_id` 与追加式选择清单（`decision ∈ {selected, truncated, deduped}`，[agentic-retrieval-run.schema.json](./contracts/agentic-retrieval-run.schema.json)），不改写原始账本条目。
3. 期望：最终上下文无重复证据、保留至少一条来自不同来源的证据、超量时为被截断证据（`decision=truncated`）提供可展开 `evidence_id`。
4. 期望：每条返回证据可凭 `(request_id, evidence_id)` 解析内部账本到检索查询/检索器/得分/版本/来源/轮次，且原始记录未被改写（SC-006，FR-008 不变量）。

## 场景 4 — 跨项目隔离（泄漏=0）

**验证**：FR-021/FR-022/FR-025/SC-003，宪法硬约束。

1. 两个独立项目各自已发布；仅携带项目 A 作用域查询。
2. 期望：LangGraph 状态按 `request_id`/`run_id` 隔离；账本/判断/选择清单均带 `(knowledge_scope_id, project_id, index_version)` 隔离；项目 B 证据不产生返回。
3. 校验：验收测试集中跨项目泄漏事件数 = 0；无 `project_scope` 请求 MUST 被拒绝（宪法硬约束）。

## 场景 5 — 降级与四态（partial / no_evidence / failed）

**验证**：FR-016/SC-011，Edge Cases。

1. 模拟某 Agent 节点超时（节点超时 5s 触发）而混合检索已有可靠证据：返回 `partial`，携带已验证证据与失败路径信息。
2. 模拟 Agent 编排被运行配置关闭：系统回退确定性检索路径并返回有效四态。
3. 模拟系统异常无法形成可靠证据：返回 `failed`；正常执行但无可靠证据：返回 `no_evidence`。
4. 校验四态可区分、可操作（沿用 001）。

## 场景 6 — 对照评测与三段通过判定

**验证**：FR-026/FR-027/FR-028/FR-029/FR-030/SC-001/SC-002/SC-008/SC-015，宪法原则 X。

1. 在 001/002/004 既有评测集上新增 ≥ 6 条 Agent 受益查询（多跳/缺口/冲突各 ≥ 2，含 ≥ 1 条中文，FR-027），原既有查询保留。
2. 同一环境会话内先重跑确定性基线、再运行 Agent 编排路径（FR-030，延迟公平）。
3. 产出 Agent 编排对照报告（`report_type=agentic_comparison`，复用 002 `eval_comparison_report` 结构扩展）。
4. 三段通过判定：
   - SC-001：Agent 受益子集 MRR/nDCG 相对确定性增强基线 **≥ 3%** 相对提升、Recall@K 不下降。
   - SC-002：001 11 条 Recall@K 精确持平、MRR/nDCG 非劣（1% 容差 = 0.01，research §10）。
   - SC-015：002/004 非受益查询 MRR/nDCG 非劣、Recall@K 不下降。
   - 硬性指标：泄漏=0、Schema 合法率=100%、来源可定位率=100%。
5. 期望：`three_gate_pass.all_passed=true` → `enters_default_path=true`；未达则 Agent 编排作为可选路径保留、不进默认路径。
6. 可重复性：连续两次运行非延迟指标在 1% 相对容差内一致（SC-008）；逐查询 Agent 判断（拆解子问题/缺口/补充轮次/上下文决策）与账本引用可解释排名变化（SC-009）。

## 场景 7 — DeepSeek Harness 端到端

**验证**：FR-024/SC-012。

1. 在 DeepSeek Harness 中调用 Agent 编排 `search_knowledge` 与 `get_evidence`。
2. 期望：端到端调用成功、输出 100% 通过 `search_knowledge`/`get_evidence` 输出 Schema 校验（005 不改对外契约，FR-024）。
3. 期望：单次调用总超时 30s 护栏低于目标 Host 最低 Tool Call 超时预算（蓝图 §19）；超时降级 `partial`。
4. ChatGPT App / Claude Code 仅记录兼容性状态、不作 005 验收阻塞项。

## 运行命令（实现阶段提供）

```bash
# 迁移：005 运行期表（alembic/versions/0050_create_agentic_tables.py，
# 独立表定义见 backend/migrations/005_agentic_tables.py）
cd backend && alembic upgrade head

# 启动 MCP 服务并启用 Agent 编排路径（运行配置开关，未达阈值为可选路径）
AGENTIC_RETRIEVAL_ENABLED=true python _run_mcp.py

# 运行 Agent 编排对照评测（同会话先重跑确定性基线再跑 Agent，FR-030）：
# 001 11 条 + 002/004 扩充集 + 005 新增批次，三段通过判定 + 硬性指标，
# 产出 eval/agentic_comparison_report.json（含 enters_default_path）
python eval/run_agentic_comparison.py \
    --dataset eval/eval_dataset.json \
    --agentic-dataset eval/agentic_eval_dataset.json \
    --output eval/agentic_comparison_report.json

# 契约校验（测试套件内执行）：
#   - 账本条目：contracts/evidence-ledger-entry.schema.json
#   - Agent 判断：contracts/agent-judgment.schema.json
#   - 运行记录：contracts/agentic-retrieval-run.schema.json
#   - 共享定义：contracts/common.schema.json
# 凭 (request_id, evidence_id) 解析内部账本（不改对外 MCP 契约，SC-006）：
#   SELECT * FROM evidence_ledger_entry WHERE request_id = :rid AND evidence_id = :eid;

# 真实服务端验收套件（场景 1–7 + 硬性指标，AGENTIC_RETRIEVAL_ENABLED=true）
cd backend && python -m pytest tests/integration/test_real_server_acceptance.py -q
```
