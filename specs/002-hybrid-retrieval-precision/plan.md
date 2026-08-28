# Implementation Plan: 002 Hybrid Retrieval Precision

**Branch**: `002-hybrid-retrieval-precision` | **Date**: 2026-08-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-hybrid-retrieval-precision/spec.md`
**Design Blueprint**: `docs/superpowers/specs/2026-08-26-ai-engineering-rag-mcp-design.md`（§23.4.2 / §9 / §8.1 / §18.2）
**Baseline**: `eval/baseline_report.json`（001 Dense-only）+ 001-minimum-rag-mcp-loop 全部已实现工件

## Summary

在 001 Dense-only 闭环之上，引入 Qdrant 命名稀疏向量（BM25/jieba CJK 分词）与 Dense 召回并行，经 RRF 融合后对有限候选执行 bge-reranker-v2-m3 Cross-Encoder 精排，使精确符号/关键词查询的排名可度量提升（MRR ≥ 0.95、nDCG ≥ 0.96，原 11 条）。知识版本能力清单扩展 `lexical_ready`，查询规划按声明能力门控。混合检索路径在 `RetrievalService` 内部接入，不改对外 MCP 契约（FR-009 / 宪法原则 VII）。对照评测在同会话先重跑 Dense 基线再跑混合，产出逐查询可解释对照报告，硬性指标（零串库/Schema 100%/来源 100% 全通过）后才进入默认路径。

## Technical Context

**Language/Version**: Python 3.12（沿用 001）

**Primary Dependencies**（在 001 基础上新增）:
- Qdrant 命名稀疏向量支持（qdrant-client ≥ 1.10，001 已用，无需换版）
- sentence-transformers `CrossEncoder('BAAI/bge-reranker-v2-m3')`（与 001 Embedding 共享依赖栈，无新框架）
- jieba（CJK 分词，纯 Python，新增依赖）
- 其余沿用 001：FastAPI / LangGraph / LangChain / SQLAlchemy 2.0 / Alembic / mcp-sdk

**Storage**: PostgreSQL 16（控制面，扩展 RetrievalRun 字段）+ Qdrant（新增 `chunks_hybrid_*` 集合，Dense+Sparse 命名向量并存）+ 本地文件系统（沿用 001）

**Testing**: pytest（backend unit/integration）+ jsonschema（契约校验）+ 扩展 `eval/run_eval.py` 双模式 + 新增 `eval/run_comparison.py` 对照报告

**Target Platform**: 本机（Windows/macOS/Linux），单 Writer，沿用 001 部署环境

**Project Type**: Web 应用（SPA + REST API + MCP server），沿用 001 架构

**Performance Goals**: 混合检索 MRR/nDCG 相对 001 基线可度量提升（见 research.md §0）；P50/P95 延迟允许上升但不超过 30s 总超时护栏且 < 目标 Host Tool Call 超时

**Constraints**:
- 不改对外 MCP 契约（FR-009 / 宪法原则 VII）
- 不引入新嵌入模型（FR-012，同一 bge-m3 + 同一切片策略）
- Sparse/BM25 由 Qdrant 负责（蓝图 §8.1/§18.2），CJK 分词由后端 jieba 负责（FR-025）
- Rerank 只处理融合后有限候选（≤ 候选预算，蓝图 §18.5）
- 并发沿用 001 请求级隔离（5 并发，FR-018）

**Scale/Scope**: 单用户、多项目；扩充评测集（原 11 + 新增词汇精确/中文查询）；首轮覆盖 US-1~US-4 全部验收

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*
*依据：.specify/memory/constitution.md v1.2.0（原则 I–X + 5 项硬性约束 + 架构约束）*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Explicit Knowledge Scope | ✅ PASS | FR-007 继承 001 显式 project_scope 要求；Dense 与 Sparse 召回都强制携带 knowledge_scope_id/version_id 过滤 |
| II. Evidence Before Inference (Project Facts Priority) | ✅ PASS | 002 检索链路确定性（Dense+Sparse+RRF+Rerank，无 LLM 推断）；证据携带 source_version 与 source_position（FR-010）；公共与项目知识分别标记（FR-006） |
| III. Expose Uncertainty | ✅ PASS | partial 状态暴露失败路径与证据缺口（FR-016）；Rerank 剔除的低相关候选保留可展开证据 ID |
| IV. Locatable Evidence | ✅ PASS | 每条证据携带来源 ID/版本/位置（FR-010/SC-004，100% 可定位）；混合检索中间分数记录在内部追踪，不进入对外响应（宪法原则 IV：MCP MUST NOT expose internal structure） |
| V. Data and Control Separation | ✅ PASS | 上传内容仍为不可信数据；Reranker 输入为 query+passage 对，不控制 Prompt/工具选择/状态跳转；BM25/Rerank 均为确定性组件 |
| VI. Deterministic Control First | ✅ PASS | 检索/过滤/融合/排序/预算由确定性代码控制（FR-017）；RRF 与 Rerank 打平用稳定 tie-breaker，无随机扰动；BM25 稀疏编码确定性 |
| VII. Independent Interface Evolution | ✅ PASS | 不改对外 MCP 契约与 Schema（FR-009）；002 契约变更为内部契约，复用 common.schema.json 共享定义；DB 模型扩展与对外契约解耦 |
| VIII. Knowledge Version Non-Mixing | ✅ PASS | 同一 bge-m3 + 同一切片策略上新增 Sparse，属新增能力非混用（FR-012）；capabilities 声明 lexical_ready，发布原子性（蓝图 §8.4）；派生索引可重建（FR-014） |
| IX. Synchronous Results First | ✅ PASS | 单次 search_knowledge 同步返回可消费结果；混合检索全流程在 30s 总超时内完成，不依赖 MCP Resources/Tasks（FR-015） |
| X. Evaluation-Driven Optimization | ✅ PASS | research.md §0 声明相对基线可度量目标；MRR/nDCG 可度量收益 + 硬性指标全通过后才进入默认路径（FR-021/SC-001） |

**硬性约束 Compliance**:

| 硬性约束 | Status | Notes |
|---------|--------|-------|
| 跨项目泄漏 MUST = 0 | ✅ PASS | Dense/Sparse 召回、融合候选、Rerank 结果都强制 scope 过滤（FR-008/SC-002） |
| 无显式 project_scope MUST 拒绝 | ✅ PASS | 继承 001（FR-007） |
| 上传内容 MUST NOT 作控制指令 | ✅ PASS | 沿用 001 凭据替换 + 不可信数据原则 |
| MCP Schema 合法率 MUST = 100% | ✅ PASS | 不改对外契约（FR-009/SC-003） |
| 来源可定位率 MUST = 100% | ✅ PASS | 沿用 001 来源定位（FR-010/SC-004） |

**Architecture Constraints Compliance**:
- Python + LangGraph + LangChain ✅（沿用 001，混合检索接入确定性状态机）
- React + TypeScript ✅（002 不涉及前端改动）
- Qdrant 负责 Dense 与 Sparse/BM25 ✅（蓝图 §8.1）
- PostgreSQL 控制面 ✅（扩展 RetrievalRun 字段）
- BAAI/bge-m3 + bge-reranker-v2-m3 本地默认 ✅（蓝图 §18.2）
- Streamable HTTP 主传输 ✅（沿用 001）
- 单 Writer/多读抽象 ✅（沿用 001）
- Loopback 默认绑定 ✅（沿用 001）

**GATE: PASS — 无违规，进入 Phase 0/1。**

## Project Structure

### Documentation (this feature)

```text
specs/002-hybrid-retrieval-precision/
├── spec.md              # Feature 规格（已通过 clarify）
├── plan.md              # 本文件（/speckit-plan 产出）
├── research.md          # Phase 0：评测目标 + 技术选型决策
├── data-model.md        # Phase 1：实体扩展 + Qdrant 集合扩展
├── quickstart.md        # Phase 1：验证指南
├── contracts/           # Phase 1：内部契约变更（复用 common.schema.json）
│   ├── common.schema.json                  # 共享类型定义（与 001 一致，蓝图 §23.1）
│   ├── hybrid-retrieval-trace.schema.json  # 混合检索内部链路追踪
│   ├── knowledge-capabilities.schema.json  # 版本能力清单（dense_ready + lexical_ready）
│   └── eval-comparison-report.schema.json  # 对照评测报告
└── tasks.md             # Phase 2（/speckit-tasks，本命令不创建）
```

### Source Code（仓库根，在 001 基础上扩展）

```text
backend/
├── src/rag_mcp/
│   ├── config.py             # 扩展：HybridRetrievalConfig（rrf_k, rerank_budget, sparse_timeout 等）
│   ├── models/
│   │   └── retrieval_run.py  # 扩展：retrieval_mode / subpath_timings / evidence_ref_ids 字段
│   ├── providers/
│   │   ├── base.py           # RerankerProvider ABC（001 已声明，002 不改）
│   │   └── local_cpu_reranker.py  # 新增：bge-reranker-v2-m3 CrossEncoder 实现
│   ├── indexing/
│   │   ├── qdrant_client.py  # 扩展：create_hybrid_collection / upsert_hybrid / search_sparse / query_hybrid
│   │   └── sparse_encoder.py # 新增：BM25SparseEncoder（jieba CJK + BM25 权重，确定性）
│   ├── services/
│   │   ├── ingestion_service.py    # 扩展：sparse_index 阶段 + hybrid 集合写入 + lexical_ready 发布门控
│   │   └── retrieval_service.py    # 扩展：混合检索路径（Dense+Sparse 召回→RRF→Rerank→装箱 + partial 降级）
│   └── fusion/                     # 新增目录
│       └── rrf.py                  # RRF 融合（确定性，DBSF 备选）
└── alembic/versions/
    └── *_add_hybrid_retrieval_fields.py  # 新增迁移：retrieval_runs 表扩展
eval/
├── run_eval.py               # 扩展：--mode dense|hybrid
├── run_comparison.py         # 新增：同会话 Dense 重跑 + Hybrid，产出对照报告
├── generate_dataset.py       # 扩展：扩充词汇精确/中文查询（保留原 11 条）
├── eval_dataset.json         # 扩充后的评测集
├── baseline_report.json      # 001 基线（只读对照）
└── hybrid_comparison_report.json  # 新增：002 对照报告产物
```

**Structure Decision**: 沿用 001 Option 2（Web 应用，backend/ + frontend/ 分离）。002 不涉及前端改动（混合检索为后端内部路径增强），不新增独立包/服务。新增 `fusion/` 目录承载融合算法，新增 `local_cpu_reranker.py` 与 `sparse_encoder.py` 两个模块，最小侵入 001 结构。

## Complexity Tracking

> No constitution violations to justify. All gates pass.

## Phase 0: Research Summary

详见 [research.md](./research.md)。关键决策摘要：

| 决策项 | 选择 | 理由 |
|--------|------|------|
| Sparse/BM25 | Qdrant 命名稀疏向量 + 后端 BM25 编码器（jieba CJK） | 蓝图 §8.1/§18.2 职责划分 + CJK 支持 + 确定性 |
| 融合算法 | RRF（k=60 默认），DBSF 备选 | rank-based 鲁棒、不依赖分数尺度对齐、确定性 |
| Reranker | bge-reranker-v2-m3 via sentence-transformers CrossEncoder | 蓝图 §18.2 默认 + 共享依赖栈 + 多语言 |
| CJK 分词 | jieba 精确模式 | 中文分词事实标准、确定性、纯 Python |
| 能力清单 | capabilities 新增 lexical_ready | 001 已预留、发布原子性、查询规划门控 |
| 索引构建 | 入库新增 sparse_index 阶段 | 沿用 ProcessingRun 框架、可重建 |
| 重建迁移 | 用户触发重建发新版本，不自动批量迁移 | FR-023 失败保护 |
| 评测框架 | 扩展 run_eval 双模式 + run_comparison 对照 | 复用 001 指标计算 + 逐查询可解释 |
| 对外契约 | 不变，内部路径增强 | FR-009 / 宪法原则 VII |

**评测目标（相对 001 基线，进入默认路径阈值）**：Recall@K ≥ 1.0（不下降）；MRR ≥ 0.95；nDCG ≥ 0.96；P50/P95 记录对照且 ≤ 30s 总超时；硬性指标全通过。

## Phase 1: Design Artifacts

- **Data Model**: [data-model.md](./data-model.md) — KnowledgeVersion/ProcessingRun/RetrievalRun 字段扩展、FusedCandidate/RerankCandidate 瞬态结构、Qdrant hybrid 集合与命名向量、BM25 稀疏编码器、跨存储一致性扩展、DDL 变更
- **Common Schema**: [contracts/common.schema.json](./contracts/common.schema.json) — 共享类型定义（与 001 一致，蓝图 §23.1；新增 ChunkId/RetrievalMode 定义）
- **Internal Contract — Hybrid Trace**: [contracts/hybrid-retrieval-trace.schema.json](./contracts/hybrid-retrieval-trace.schema.json) — 混合检索内部链路追踪（子路径耗时 + 逐候选各路分数，FR-020/FR-022/SC-007）
- **Internal Contract — Capabilities**: [contracts/knowledge-capabilities.schema.json](./contracts/knowledge-capabilities.schema.json) — 版本能力清单 dense_ready + lexical_ready（FR-011/FR-013/SC-008，发布门控）
- **Internal Contract — Eval Report**: [contracts/eval-comparison-report.schema.json](./contracts/eval-comparison-report.schema.json) — 对照评测报告（基线 vs 混合逐项增量 + 逐查询可解释 + 硬性指标 + 可重复性 + enters_default_path 判定，FR-019/FR-024/SC-001/SC-006/SC-007）
- **对外 MCP 契约**: 不变，沿用 001 contracts/mcp-search-{input,output}.schema.json 与 mcp-get-evidence.schema.json（FR-009/宪法原则 VII）
- **Quickstart Validation**: [quickstart.md](./quickstart.md) — 验证场景指南

## Post-Design Constitution Re-Check

| Principle | Status | Verification |
|-----------|--------|-------------|
| I. Explicit Knowledge Scope | ✅ PASS | data-model.md §5.6 Dense/Sparse 查询均强制 scope+version 过滤；capabilities schema 不涉及 scope 绕过 |
| II. Evidence Before Inference | ✅ PASS | 无 LLM 推断；evidence 保留 source_version/source_position；partial 暴露失败路径 |
| III. Expose Uncertainty | ✅ PASS | hybrid-trace schema 的 failed_paths + eval-report 的 hard_constraints 暴露所有约束状态 |
| IV. Locatable Evidence | ✅ PASS | 对外契约不变；内部 trace 的 fused_candidates 不进入 MCP 响应 |
| V. Data and Control Separation | ✅ PASS | Reranker 输入为 query+passage；sparse_encoder 确定性，不受上传内容控制 |
| VI. Deterministic Control First | ✅ PASS | RRF/排序 tie-breaker 确定性（FR-017）；BM25 编码确定性；词表构建期冻结 |
| VII. Independent Interface Evolution | ✅ PASS | 对外契约零变更；002 contracts 为内部契约，$ref 复用 common.schema.json 共享定义 |
| VIII. Knowledge Version Non-Mixing | ✅ PASS | 同一 bge-m3+切片策略新增能力；capabilities 门控 lexical_ready；派生索引可重建 |
| IX. Synchronous Results First | ✅ PASS | 混合检索全流程同步，30s 总超时内；不依赖 Resources/Tasks |
| X. Evaluation-Driven Optimization | ✅ PASS | research.md §0 声明可度量目标；eval-report schema 含 enters_default_path 判定 + hard_constraints |

**All gates pass. No violations. No constitution exceptions documented (research.md §三确认无例外).**
