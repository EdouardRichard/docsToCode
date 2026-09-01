# Implementation Plan: Graph RAG (004)

**Branch**: `004-graph-rag` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-graph-rag/spec.md`；技术决策来自 [research.md](./research.md)；数据模型见 [data-model.md](./data-model.md)；契约见 [contracts](../003-structured-asset-expansion/contracts/)；验证指南见 [quickstart.md](./quickstart.md)。

## Summary

004 在 PostgreSQL 中构建图节点/图边/硬关系/软关系，使用关系表 + 递归 CTE 完成一至三跳关系扩展，作为混合检索链路第 3 路输入并入 RRF 融合池（与 Dense/Sparse 同池）、统一 Rerank（蓝图 §8.2/§9）。硬关系从 001 Java 符号切片与 003 DDL 切片确定性提取（调用图 / 外键）；软关系由 LLM 离线推断、携带五项元数据与四态生命周期、确定性 supersede 规则、与硬关系可区分不冒充。知识版本能力清单扩展 `graph_ready`。图增强须在固定评测集上证明相对 002 混合基线结构性子集 ≥ 3% 相对提升 + 001/002 非劣三段通过 + 硬性指标全过后才进入默认检索路径（宪法原则 X）。004 不修改对外 MCP 契约（宪法原则 VII），图扩展证据通过既有契约增补硬/软关系标注返回。

## Technical Context

**Language/Version**: Python 3.11+（宪法架构约束：Python/LangGraph/LangChain 后端编排基线；沿用 001/002/003）。

**Primary Dependencies**: LangGraph/LangChain（编排，复用 001/003）、Qdrant（Dense/Sparse 检索，复用 002）、PostgreSQL（图节点/边/硬软关系 + 控制面，复用 001/003）、`BAAI/bge-m3` + `BAAI/bge-reranker-v2-m3`（本地默认，复用 001/002）、tree-sitter/Java AST（调用图提取，复用 001 `parsers/java_parser.py`）、DDL 解析（复用 003 `parsers/ddl_parser.py`）、Snowflake ID 生成（沿用 `utils/hashing.py` 标识模式）。

**Storage**: PostgreSQL——新增 `graph_edge`（硬关系）、`soft_relation`（软关系，五元数据 + 四态）、`graph_expansion_path`（检索运行子表）、扩展 `knowledge_capabilities`（`graph_ready`）。Qdrant 复用（不改）。跨存储一致 `knowledge_scope_id`/`project_id`/`chunk_id`/`index_version`（蓝图 §8.4）。详见 [data-model.md](./data-model.md)。

**Testing**: pytest（contract/integration/unit）+ 契约 Schema 校验（json-schema 2020-12）+ 评测 runner（复用 002，扩展图增强对照报告 [eval-graph-comparison-report.schema.json](../003-structured-asset-expansion/contracts/eval-graph-comparison-report.schema.json)）。对照评测须同会话先重跑混合基线、再跑图增强（FR-025）。

**Target Platform**: 本机 loopback HTTP（单用户、本机部署，沿用 001/002/003）；Streamable HTTP MCP 为主、stdio 适配。

**Project Type**: web-service（MCP 检索 + REST 管理，扩展 001/002/003 既有 backend/frontend）。

**Performance Goals**: 单次图增强调用 P95 < 30s 总超时护栏且 < 目标 Host 最低 Tool Call 超时（蓝图 §19）；图扩展子超时 3s；候选预算 ≤ 20；1~3 跳递归 CTE 在护栏内完成（research §1）。

**Constraints**: 跳数默认 2/上限 3；候选预算为单次总预算（非逐跳）；默认双向遍历；关系方向/supersede 由确定性规则决定、不由 LLM 独占（宪法 VI）；跨项目泄漏=0；不改对外 MCP 契约；软关系权重 < 硬关系。

**Scale/Scope**: 单用户本机、5 并发（请求级隔离，蓝图 §21.1）；首期语料 Java 调用图 + DDL 外键（蓝图 §10.1 其余硬关系为后续批次）；不引入 Neo4j（蓝图 §8.3，§26 触发条件未满足）。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.* 依据 `/.specify/memory/constitution.md` v1.2.0。

### Non-Negotiable Hard Constraints（蓝图 §24.2）

| 硬约束 | 状态 | 依据 |
|--------|------|------|
| 跨项目泄漏 = 0 | ✅ PASS | FR-010/SC-003：图边带 `(knowledge_scope_id, project_id, index_version)` 隔离；图扩展只在请求作用域内沿边扩展；bidirectional 默认仍在作用域内；验收集泄漏事件=0 |
| 无 `project_scope` 检索须拒绝 | ✅ PASS | FR-009：继承 001/002/003 显式作用域要求，缺则拒绝、不回退全库 |
| 上传内容不得作控制指令 | ✅ PASS | 硬关系由确定性 AST/DDL 解析产生；软关系 LLM 推断为**离线数据生成**（不入运行时控制流）；supersede 转换由确定性规则触发、不由 LLM 独占（宪法 V/VI） |
| MCP Schema 合法率 100% | ✅ PASS | FR-011/SC-004：004 不修改 001 对外 MCP 契约，图扩展证据通过既有契约增补标注返回；验收集 100% |
| 证据来源可定位率 100% | ✅ PASS | FR-012/SC-005：`edge_id` + `parse_evidence` + 沿用 001/003 来源定位格式；验收集 100% |

### Core Principles（10 条）

| 原则 | 状态 | 依据 |
|------|------|------|
| I 显式知识作用域 | ✅ PASS | FR-009 |
| II 项目事实优先 | ✅ PASS | FR-004：硬关系为准，软关系不得静默覆盖硬关系 |
| III 暴露不确定性 | ✅ PASS | FR-003/FR-004：软关系四态、五元数据、与硬关系可区分；推断不伪造解析 |
| IV 来源可定位 | ✅ PASS | FR-002/FR-012：图边规范标识字段 + parse_evidence |
| V 数据与控制分离 | ✅ PASS | 上传代码为非信数据；确定性解析；离线 LLM 推断不控工作流；凭据规范化复用 001 |
| VI 确定性控制优先 | ✅ PASS | RRF rank-only 融合；确定性方向（spec 澄清 Q2）；确定性 supersede（spec 澄清 Q3）；LLM 仅作离线数据生成不作状态机/工作流控制 |
| VII 接口独立演进 | ✅ PASS | 不改对外 MCP 契约；图契约独立 $id `/schemas/004/`，与 001/002/003 schema 分版本演进 |
| VIII 知识版本不可混用 | ✅ PASS | FR-015：graph_ready 同 bge-m3 嵌入+切片策略上的派生能力，不触发不可混用 |
| IX 同步结果优先 | ✅ PASS | 图增强在单次 Tool Call 内返回，30s 护栏，不依赖 MCP Tasks/Resources |
| X 评测驱动优化 | ✅ PASS | research §0 评测目标闸门；三段通过判定（SC-001/SC-002/SC-013）+ 硬性指标全过才进默认路径；未达则可选路径保留 |

**Gate 结论**：无违规。无 Complexity Tracking 条目（无原则豁免）。无宪法原则 X 例外（research §9）。Phase 1 设计后复核：data-model/contracts/quickstart 均不引入新违规——图边/软关系契约为内部数据契约（不改对外 MCP 响应），`graph_ready` 为能力清单扩展（以扩展契约声明，不改 002 对外 schema），全部硬约束与原则保持 PASS。

## Project Structure

### Documentation (this feature)

```text
specs/004-graph-rag/
├── plan.md              # 本文件（/speckit-plan 产出）
├── research.md          # Phase 0 产出（含 001 相对评测目标闸门 §0）
├── data-model.md        # Phase 1 产出（图实体/表结构/状态机/校验）
├── quickstart.md        # Phase 1 产出（端到端验证场景）
└── tasks.md             # /speckit-tasks 产出（本命令不创建）
```

**契约位置（用户指示）**：004 图契约物理置于 `specs/003-structured-asset-expansion/contracts/`（与既有 common.schema.json 同目录，集中 schema 族），逻辑 $id 为 `/schemas/004/` 标识 004 归属，$ref 绝对引用 003 common.schema.json 共享定义：

```text
specs/003-structured-asset-expansion/contracts/
├── common.schema.json                              # 003 既有（共享定义，004 不改）
├── format-locators.schema.json                     # 003 既有（图节点 source_position 复用）
├── management-api.format-extension.schema.json     # 003 既有
├── graph-relations.schema.json                     # 004 新增：图边/硬软关系数据契约
├── graph-expansion-trace.schema.json              # 004 新增：图扩展内部追踪契约
├── knowledge-capabilities.graph-extension.schema.json  # 004 新增：graph_ready 能力扩展
└── eval-graph-comparison-report.schema.json        # 004 新增：图增强对照评测报告
```

### Source Code (repository root)

```text
backend/
├── src/rag_mcp/
│   ├── graph/                        # 004 新增：图关系核心
│   │   ├── models.py                 # graph_edge / soft_relation ORM/dataclass
│   │   ├── store/
│   │   │   ├── base.py               # GraphStore 抽象（蓝图 §8.3 迁移能力）
│   │   │   └── postgres_graph_store.py  # 递归 CTE 1~3 跳 + 护栏截断
│   │   ├── extractors/
│   │   │   ├── java_call_graph.py    # 复用 parsers/java_parser.py，确定性 calls/called_by
│   │   │   └── ddl_fk.py             # 复用 parsers/ddl_parser.py，确定性 fk_references
│   │   ├── soft_relation_inference.py # 离线 LLM 推断 + 五元数据 + 四态机
│   │   ├── expansion.py              # 图扩展（总预算截断、结构权重排序、edge_path 记录）
│   │   ├── trace_recorder.py         # 运行追踪：graph-expansion-trace 账本（subpath_timings/failed_paths/evidence_id 回填，FR-026）
│   │   └── capabilities.py           # graph_ready 能力门控（就绪才可检索）
│   ├── fusion/
│   │   └── rrf.py                    # 004 扩展：graph 作第 3 路输入（不改 rank-only 语义）
│   ├── services/
│   │   ├── ingestion_service.py      # 004 扩展：入库期提取硬关系 + 离线软关系推断
│   │   └── evidence_service.py       # 004 扩展：证据增补硬/软关系标注（不改对外契约）
│   ├── models/
│   │   └── chunk.py                  # 复用（图节点=Chunk）
│   ├── mcp/
│   │   ├── search_knowledge.py       # 004 不改（图扩展证据经既有契约返回）
│   │   └── get_evidence.py           # 004 不改
│   ├── parsers/
│   │   ├── java_parser.py           # 复用 001
│   │   └── ddl_parser.py            # 复用 003
│   └── indexing/qdrant_client.py     # 复用 002（不改）
├── alembic/versions/                 # 004 新增迁移：graph_edge / soft_relation / graph_expansion_path / capabilities
└── tests/
    ├── contract/                     # 4 个图契约 Schema 校验
    ├── integration/                  # 图扩展端到端、跨项目隔离、四态降级
    └── unit/                        # 递归 CTE 跳数/预算截断、supersede 规则、结构权重

eval/                                  # 004 扩展：图增强对照评测 runner
└── (reuse 002 runner + graph path scores, three_gate_pass)

frontend/src/                          # 004 不改前端（沿用 001/003 管理 UI；graph_ready 状态展示属现有版本管理）
```

**Structure Decision**: 采用既有 `backend/ + frontend/` Web 应用结构（模板 Option 2），004 在 `backend/src/rag_mcp/` 新增 `graph/` 子包承载图关系核心（store/extractors/expansion/capabilities），扩展 `fusion/rrf.py`、`services/ingestion_service.py`、`services/evidence_service.py` 与 `alembic` 迁移，**不改 `mcp/` 对外契约模块**。契约按用户指示物理置于 003/contracts（复用 common.schema.json），逻辑 $id `/schemas/004/` 独立演进（宪法 VII）。图节点不独立建表（=Chunk），避免双写一致性。

## Complexity Tracking

> Constitution Check 无违规，无需填表。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |