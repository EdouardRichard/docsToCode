# Quickstart 验证指南：002 Hybrid Retrieval Precision

**Feature**: 002-hybrid-retrieval-precision
**目的**: 提供可运行的端到端验证场景，证明混合检索相对 001 Dense 基线的质量提升与硬性合规。
**关联**: [plan.md](./plan.md) | [research.md](./research.md)（评测目标 §0）| [data-model.md](./data-model.md) | [contracts/](./contracts/)

> 本指南为验证/运行指南，不包含完整实现代码；实现细节见后续 `tasks.md` 与实现阶段。

---

## 1. 前置条件

1. **001 闭环已运行**：PostgreSQL + Qdrant 已启动（`docker compose up`），001 已入库 Markdown/Java 知识并产出 `eval/baseline_report.json`。
2. **模型就绪**：`BAAI/bge-m3`（Dense，001 已用）与 `BAAI/bge-reranker-v2-m3`（Reranker）可在本地 CPU 加载（首次自动下载）。
3. **新增依赖**：`pip install jieba`（CJK 分词）；sentence-transformers 已含 CrossEncoder（001 已装）。
4. **配置**：在 `.env` 或 config 中确认 `HybridRetrievalConfig`（rrf_k=60, rerank_budget=20, sparse_query_timeout_ms, total_timeout_ms=30000）。

---

## 2. 初始化混合检索索引（lexical_ready 发布门控）

**目的**：验证 Sparse/BM25 索引构建与能力清单门控（FR-011/FR-013/SC-008）。

**步骤**：
1. 对已有 Dense-only 知识源触发重建：
   ```bash
   curl -X POST http://127.0.0.1:8000/api/knowledge-sources/{id}/reprocess
   ```
2. 观察处理阶段含 `sparse_index`（data-model.md §3.2）。
3. 发布后查询版本能力清单。

**预期**：
- 新版本 `capabilities = {"dense_ready": true, "lexical_ready": true}`，符合 [knowledge-capabilities.schema.json](./contracts/knowledge-capabilities.schema.json)。
- 仅 `dense_ready` 的旧版本仍可 Dense-only 检索，不被纳入 Sparse 路径（FR-013）。
- `sparse_index` 失败时版本保持 `draft`，旧版本继续可用（FR-023）。

---

## 3. 验证场景

### VS-1 精确符号查询排名提升（US-1 / SC-001）

**命令**：在固定评测集上对 query 5（`validateToken`）运行混合检索。

**预期**：基线中期望证据排第 2（MRR=0.5）；混合检索后排第 1（MRR=1.0）。逐查询对照见对照报告 `per_query_comparison`。

### VS-2 混合检索质量优于 Dense 基线（US-2 / SC-001 / FR-021）

**命令**：
```bash
python eval/run_comparison.py --dataset eval/eval_dataset.json \
  --output eval/hybrid_comparison_report.json --limit 18
```

**预期**（research.md §0.2 进入默认路径阈值，相对口径 2026-09-04 修订）：
- 原 11 条子集 MRR/nDCG 相对**同会话 Dense 基线**严格正增量；Recall@K ≥ 基线（不下降）；绝对水位仅参考不作门禁。
- 报告 `deltas.mrr_mean_delta > 0` 且 `deltas.ndcg_mean_delta > 0`。
- 报告符合 [eval-comparison-report.schema.json](./contracts/eval-comparison-report.schema.json)。

### VS-3 对照评测可重复且可解释（US-3 / SC-006 / SC-007 / FR-020）

**命令**：连续运行两次 `run_comparison.py`。

**预期**：
- 非延迟指标（Recall/MRR/nDCG）两次相对偏差 ≤ 1%（reproducibility.non_latency_reproducible = true）。
- 延迟指标标注 `env_sensitive = true`，不作为否决项。
- `per_query_comparison` 逐查询列出 baseline_rank vs hybrid_rank + dense_score/sparse_score/fused_score/rerank_score。

### VS-4 跨项目泄漏为零（SC-002 / FR-008）

**命令**：对仅携带项目 A 作用域的查询运行混合检索，断言返回证据 `knowledge_scope_id` 全部属于 A。

**预期**：`hard_constraints.cross_project_leakage_events = 0`。

### VS-5 MCP Schema 合法率 100%（SC-003 / FR-009）

**命令**：对验收测试集每条查询调用 `search_knowledge`，用 001 `mcp-search-output.schema.json` 校验响应。

**预期**：`hard_constraints.schema_validity_rate = 1.0`；对外契约零变更。

### VS-6 来源可定位率 100%（SC-004 / FR-010）

**命令**：断言每条返回证据携带 source_version + source_position（Markdown 章节路径或 Java 全限定符号路径）。

**预期**：`hard_constraints.source_locatability_rate = 1.0`。

### VS-7 partial 降级（SC-009 / FR-016）

**命令**：模拟 Sparse 超时（设置 `sparse_query_timeout_ms=1`）后运行查询。

**预期**：Dense 可靠时返回 `completion_status = "partial"`，gaps 标注 `sparse_path_failed`；hybrid-trace 的 `failed_paths` 非空。Rerank 失败同理返回 RRF 排序结果 + `rerank_failed`。

### VS-8 CJK 中文词法召回（FR-025 / Edge Case）

**命令**：对中文查询（扩充评测集的中文用例）运行混合检索，对比 Dense-only 与混合排名。

**预期**：Sparse/BM25 对含中文精确词汇的 Chunk 给出更高词频权重，混合排名优于 Dense-only；朴素空格分词不作为 CJK 唯一分词方式。

### VS-9 能力门控与版本隔离（US-4 / SC-008 / FR-013）

**命令**：同时存在仅 `dense_ready` 版本与 `lexical_ready` 版本，分别查询。

**预期**：查询规划只对声明 `lexical_ready` 的版本启用 Sparse 路径；仅 Dense 版本走 Dense-only，两者 Chunk 不互相串入。

### VS-10 延迟对照与护栏（SC-005 / FR-015）

**命令**：对照报告 `latency_ms` 记录 P50/P95。

**预期**：P50/P95 允许因新增 Sparse+Rerank 上升，但 `subpath_timings.total_ms ≤ 30000`（总超时护栏），且 < 目标 Host Tool Call 超时。

---

## 4. 对照报告产物

运行完成后 `eval/hybrid_comparison_report.json` 包含：
- `baseline_metrics` + `hybrid_metrics` + `deltas`（逐项增量）
- `hard_constraints`（硬性指标，`all_passed` 须为 true 才进入默认路径）
- `per_query_comparison`（逐查询可解释排名变化）
- `reproducibility`（非延迟可重复性）
- `enters_default_path`（是否满足进入默认路径条件，FR-021 / 宪法原则 X）

**通过判定**：MRR/nDCG 可度量收益 + `hard_constraints.all_passed = true` → `enters_default_path = true`。

---

## 5. 清理与回退

- 混合检索不影响 Dense-only 路径：移除 `lexical_ready` 版本即可回退至 001 Dense 基线。
- 旧 `chunks_dense_*` 集合保留，Dense-only 版本始终可检索。
- 对照报告与基线报告保留供审计。
