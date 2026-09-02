# Implementation Plan: Agentic Retrieval Orchestration (005)

**Branch**: `005-agentic-retrieval-orchestration` | **Date**: 2026-09-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/005-agentic-retrieval-orchestration/spec.md`；技术决策与评测目标闸门见 [research.md](./research.md)（§0 先声明相对 001 基线的评测目标）；数据模型见 [data-model.md](./data-model.md)；契约见 [contracts](./contracts/)；验证指南见 [quickstart.md](./quickstart.md)。

## Summary

005 在 001/002/003/004 确定性检索基线之上引入三 Agent 编排：查询规划 Agent（拆解子问题、选信号与关系方向）、证据分析 Agent（覆盖度/缺口/冲突结构化判断）、上下文编排 Agent（去重/多样/父级补充/装箱），由 LangGraph 确定性状态机驱动九步主流程与有界补充检索循环（6→3→4→5→6→7），追加式证据账本记录每条证据的检索查询/检索器/得分/版本/来源/轮次/子问题（蓝图 §11–13）。Agent 编排须在固定评测集上证明相对确定性基线 Agent 受益子集 MRR/nDCG ≥ 3% 相对提升 + 001 之 11 条非劣 + 002/004 非受益非回归三段通过 + 硬性指标全过后才进入默认检索路径（宪法原则 X）。005 不修改对外 MCP 契约（宪法原则 VII），Agent 判断与账本为内部追踪契约，以输出 `request_id`+`evidence_id` 为桥接键对外可追溯。

## Technical Context

**Language/Version**: Python 3.11+（宪法架构约束：Python/LangGraph/LangChain 后端编排基线；沿用 001/002/003/004）。

**Primary Dependencies**: LangGraph/LangChain（状态机与节点流转、模型/Prompt/Retriever 适配，复用 001）、Qdrant（Dense/Sparse 检索，复用 002）、PostgreSQL（Agent 编排运行期表：账本/判断/选择清单/运行记录，蓝图 §8.2/§13，复用 001/003/004）、`BAAI/bge-m3`+`BAAI/bge-reranker-v2-m3`（本地默认，复用 001/002）、004 图扩展（复用，作查询规划 Agent 可选信号）、Model Gateway/能力路由层（不绑供应商，蓝图 §18）、Snowflake ID 生成（沿用 `utils/hashing.py` 标识模式）。

**Storage**: PostgreSQL——新增 `evidence_ledger_entry`（追加式账本，`ledger_entry_id` 雪花 ID）、`agent_judgment`（覆盖度/冲突枚举判断）、`context_selection_list`（追加式选择清单 decision 枚举）、`agentic_retrieval_run`（运行记录+公共状态包络+护栏+子路径耗时+Agent 输出引用+账本引用）。运行期表使用 TTL，不进入向量库，不写回项目知识库（蓝图 §20）。Qdrant 复用（不改）。详见 [data-model.md](./data-model.md)。

**Testing**: pytest（contract/integration/unit）+ 契约 Schema 校验（json-schema 2020-12，4 个 005 schema）+ 评测 runner（复用 002，扩展 Agent 编排对照报告）+ LangGraph 状态机跳转断言 + 追加式不变量断言。对照评测须同会话先重跑确定性基线、再跑 Agent 编排（FR-030）。

**Target Platform**: 本机 loopback HTTP（单用户、本机部署，沿用 001–004）；Streamable HTTP MCP 为主、stdio 适配。

**Project Type**: web-service（MCP 检索 + REST 管理，扩展 001–004 既有 backend/frontend）。

**Performance Goals**: 单次 Agent 编排调用 P95 < 30s 总超时护栏且 < 目标 Host 最低 Tool Call 超时（蓝图 §19）；Agent 节点超时默认 5s/上限 10s；补充检索轮次默认 2/上限 3；装箱 top_k ≤ 20；单来源最大证据默认 3/上限 5；图护栏沿用 004（跳数 2/3、预算 10/20、子超时 3s）。

**Constraints**: 状态跳转权属确定性控制器非 Agent（宪法 VI）；`needs_supplementary` 为 Agent 判断输入非独占跳转；补充候选重新进入融合/Rerank/分析（非直接并入上下文）；账本追加式不可改写；选择清单独立追加不改账本；不改对外 MCP 契约（evidence 项 `additionalProperties: false`）；跨项目泄漏=0；不新增知识版本能力标志（运行时开关）。

**Scale/Scope**: 单用户本机、5 并发（请求级隔离，蓝图 §21.1）；Agent 受益评测批次 ≥ 6 条（多跳/缺口/冲突各 ≥ 2，含 ≥ 1 中文）；运行状态 TTL；不引入多实例或分布式协调（属 006）。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.* 依据 `/.specify/memory/constitution.md` v1.2.0。Phase 0 research.md §0 已声明相对 001 基线评测目标闸门（满足"research.md 必须先声明相对 001 基线评测目标，否则不得进入 plan"）。

### Non-Negotiable Hard Constraints（蓝图 §24.2）

| 硬约束 | 状态 | 依据 |
|--------|------|------|
| 跨项目泄漏 = 0 | ✅ PASS | FR-022/SC-003：账本/判断/选择清单/运行记录均带 `(knowledge_scope_id, project_id, index_version)` 隔离；`project_scope` 缺失拒绝；验收集泄漏事件=0 |
| 无 `project_scope` 检索须拒绝 | ✅ PASS | FR-021：继承 001–004 显式作用域要求，缺则拒绝、不回退全库 |
| 上传内容不得作控制指令 | ✅ PASS | FR-019/FR-020：原始内容只进证据字段、结构化边界隔离；Agent 输出经 Schema 校验；状态机跳转由确定性控制器决定非 Agent 独占（宪法 V/VI） |
| MCP Schema 合法率 100% | ✅ PASS | FR-024/SC-004：005 不改 001 对外 MCP 契约（evidence 项 `additionalProperties: false` 不新增字段）；Agent 判断/账本为内部契约，以 `request_id`+`evidence_id` 桥接；验收集 100% |
| 证据来源可定位率 100% | ✅ PASS | FR-023/SC-005：账本条目携带来源 ID/版本/位置，沿用 001/003 来源定位格式；验收集 100% |

### Core Principles（10 条）

| 原则 | 状态 | 依据 |
|------|------|------|
| I 显式知识作用域 | ✅ PASS | FR-021 |
| II 项目事实优先 | ✅ PASS | FR-016：项目与公共证据冲突并列返回、不臆造裁决；Agent 推理不写回知识库（FR-011） |
| III 暴露不确定性 | ✅ PASS | FR-013/FR-016/SC-010：覆盖度/冲突枚举显式返回；`partial` 携带缺口/冲突/失败路径；推断证据不覆盖硬证据 |
| IV 来源可定位 | ✅ PASS | FR-008/FR-009/FR-032：追加式账本 `ledger_entry_id`+`sub_problem_id`+`round_index`，每条证据可解析检索查询/检索器/得分/版本/来源/轮次 |
| V 数据与控制分离 | ✅ PASS | FR-019/FR-020：上传/检索内容为不可信数据；结构化边界隔离；Schema 校验；高风险片段可审计隔离 |
| VI 确定性控制优先 | ✅ PASS | FR-004/FR-013/FR-015：LangGraph 状态机跳转权属确定性控制器；`needs_supplementary` 为判断输入非独占；Agent 输出经 Schema 校验后生效，校验失败回退确定性等价行为 |
| VII 接口独立演进 | ✅ PASS | FR-024：不改对外 MCP 契约；005 内部契约独立 $id `/schemas/005/`，与 001–004 schema 分版本演进；$ref 引用 005 common.schema.json 共享定义 |
| VIII 知识版本不可混用 | ✅ PASS | 不新增知识版本能力标志；Agent 编排为运行时路径叠加于既有已发布版本之上，不触发不可混用（spec Assumptions） |
| IX 同步结果优先 | ✅ PASS | Agent 编排在单次 Tool Call 内返回，30s 护栏，不依赖 MCP Tasks/Resources |
| X 评测驱动优化 | ✅ PASS | research §0 评测目标闸门；三段通过判定（SC-001 ≥3%/SC-002 001 非劣/SC-015 非回归）+ 硬性指标全过才进默认路径；未达则可选路径保留 |

**Gate 结论**：无违规。无 Complexity Tracking 条目（无原则豁免）。无宪法原则 X 例外（research §11）。Phase 1 设计后复核：data-model/contracts/quickstart 均不引入新违规——`evidence_ledger_entry`/`agent_judgment`/`context_selection_list`/`agentic_retrieval_run` 为内部运行期数据契约（不改对外 MCP 响应），4 个 schema 以独立 $id 演进、$ref 复用 005 common.schema.json 共享定义，全部硬约束与原则保持 PASS。

## Project Structure

### Documentation (this feature)

```text
specs/005-agentic-retrieval-orchestration/
├── plan.md              # 本文件（/speckit-plan 产出）
├── research.md          # Phase 0 产出（§0 含 001 相对评测目标闸门）
├── data-model.md        # Phase 1 产出（运行期表/状态机/标识契约）
├── quickstart.md        # Phase 1 产出（端到端验证场景）
├── contracts/           # Phase 1 产出（内部追踪契约，复用 common.schema.json 共享定义）
│   ├── common.schema.json                    # 共享定义（复用 001 + 005 新增 RunId/LedgerEntryId/SubProblemId/RoundIndex/ContextResultId/AgentRole/CoverageState/ConflictType/SelectionDecision/RetrieverType）
│   ├── evidence-ledger-entry.schema.json     # 追加式证据账本条目
│   ├── agent-judgment.schema.json            # 证据分析 Agent 节点输出（覆盖度/冲突枚举）
│   └── agentic-retrieval-run.schema.json     # 运行记录+公共状态包络+三 Agent 输出引用+选择清单+账本引用
└── tasks.md             # /speckit-tasks 产出（本命令不创建）
```

### Source Code (repository root)

```text
backend/
├── src/rag_mcp/
│   ├── agents/                        # 005 新增：三 Agent 角色 + 能力路由
│   │   ├── base.py                     # Agent 抽象 + 节点 Schema 校验 + 降级回退（FR-003/SC-011）
│   │   ├── query_planner.py            # 查询规划 Agent（拆解 sub_problem_id、选信号/关系方向，FR-001/FR-033）
│   │   ├── evidence_analyst.py         # 证据分析 Agent（覆盖度/冲突/缺口判断，FR-013/FR-015）
│   │   ├── context_orchestrator.py     # 上下文编排 Agent（去重/多样/父级/装箱+选择清单，FR-017/FR-018）
│   │   └── capability_router.py        # 能力路由层（模型选择、不绑供应商，蓝图 §18，FR-002）
│   ├── orchestration/                 # 005 新增：状态机 + 账本 + 追踪
│   │   ├── state_machine.py            # LangGraph 九步状态机 + 补充检索有界循环 + 护栏（FR-004/FR-005/FR-006/FR-014）
│   │   ├── ledger.py                   # 追加式证据账本（evidence_ledger_entry，只 INSERT，FR-008/FR-009）
│   │   ├── judgment_store.py          # agent_judgment 持久化 + Schema 校验结果
│   │   ├── context_selection.py        # context_selection_list 追加式选择清单（不改账本，FR-017/FR-032）
│   │   ├── state_envelope.py          # 公共状态包络 + agentic_retrieval_run（FR-010/FR-031，TTL）
│   │   └── trace_recorder.py          # 子路径耗时/Agent 输出引用/账本引用追踪（可关正文，FR-011/FR-012）
│   ├── eval/                           # 005 扩展
│   │   └── agentic_comparison.py       # Agent 编排对照评测 + 三段通过判定 + 逐查询可解释性（FR-026/FR-028/FR-029）
│   ├── mcp/                            # 复用 001（不改对外契约，仅经 request_id 桥接内部账本）
│   ├── retrieval/                      # 复用 002/004（Dense/Sparse/图扩展/融合/Rerank，作 Agent 可选信号）
│   └── ... (复用 001/002/003/004 既有 modules)
├── tests/
│   ├── contract/                       # 4 个 005 schema 契约校验 + 对外 MCP schema 不回归
│   ├── integration/                    # 端到端 Agent 编排 + 跨项目隔离 + 补充检索循环 + 降级四态
│   └── unit/                           # 账本追加式不变量/状态机跳转/选择清单 decision 枚举/能力路由
└── ... (复用 001–004 既有 frontend/management)
```

**Structure Decision**: 单 web-service 后端，扩展 001–004 既有 `backend/src/rag_mcp/`，新增 `agents/`（三角色+能力路由）与 `orchestration/`（状态机+账本+追踪）两个模块，扩展 `eval/`；不改 `mcp/` 对外契约实现（仅经 `request_id` 桥接内部账本）。

### 数据模型扩展

详见 [data-model.md](./data-model.md)。005 **不新增知识版本能力标志**（Agent 编排为运行时路径，经运行配置开关启用/禁用，spec Assumptions、research §0.3）。新增 4 张运行期表（PostgreSQL，TTL）：`evidence_ledger_entry`（追加式账本，`ledger_entry_id` 雪花 ID，`round_index`/`sub_problem_id`）、`agent_judgment`（覆盖度/冲突枚举判断）、`context_selection_list`（追加式选择清单，`context_result_id`+`decision` 枚举）、`agentic_retrieval_run`（运行记录+公共状态包络+护栏+子路径耗时+Agent 输出引用+账本引用）。状态机含补充检索有界循环（6→3→4→5→6→7）与四态终态。运行期表不进入向量库、不写回知识库。

### 契约变更

契约置于 [contracts/](./contracts/)，复用 common.schema.json 共享定义（$ref）。**对外 MCP 契约不变**（FR-024，宪法 VII）：`search_knowledge`/`get_evidence` 输出 Schema 沿用 001，evidence 项 `additionalProperties: false` 不新增字段；Agent 判断与账本引用为**内部追踪契约**，以输出 `request_id`+`evidence_id` 为桥接键对外可追溯。新增 4 个内部 schema（独立 $id `/schemas/005/`，分版本演进）：`common.schema.json`（复用 001 共享定义 + 005 新增 RunId/LedgerEntryId/SubProblemId/RoundIndex/ContextResultId/AgentRole/CoverageState/ConflictType/SelectionDecision/RetrieverType）、`evidence-ledger-entry.schema.json`、`agent-judgment.schema.json`、`agentic-retrieval-run.schema.json`。

## Complexity Tracking

> 无 Constitution Check 违规，无 Complexity Tracking 条目。
