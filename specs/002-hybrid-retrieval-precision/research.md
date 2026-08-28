# 002 Hybrid Retrieval Precision 技术研究文档

**Feature**: 002-hybrid-retrieval-precision
**状态**: Draft
**日期**: 2026-08-27
**依据**: Feature Spec `specs/002-hybrid-retrieval-precision/spec.md`；系统设计蓝图 §8.1 / §9 / §18.2 / §23.4.2；Constitution I–X
**对照基线**: `eval/baseline_report.json`（001 Dense-only）

---

## 〇、评测目标（相对 001 Dense 基线） — 进入 plan 的前置门禁

> **硬性前置要求**（蓝图 §24.3 / 宪法原则 X）：本节在 research.md 中首先声明相对基线的可度量目标。若本节未声明或目标不可度量，则不得进入 plan.md。以下目标全部可由 `eval/run_eval.py` 产出的指标在固定评测集上验证。

### 0.1 001 Dense 基线（已记录于 `eval/baseline_report.json`）

| 指标 | 001 Dense 基线值 | 备注 |
|------|----------------|------|
| Recall@K（mean, K=5） | 1.0 | 已饱和（min=max=1.0），无提升空间 |
| MRR（mean） | 0.9091 | min=0.5（query 5 `validateToken` 期望证据排第 2） |
| nDCG@K（mean, K=5） | 0.9329 | min=0.6309（同 query 5） |
| 延迟 P50 | 138.45 ms | 环境敏感，不可在 1% 容差内重复 |
| 延迟 P95 | 185.15 ms | 环境敏感 |
| 评测集查询数 | 11 | 原 11 条 |
| 嵌入模型 | BAAI/bge-m3 | Dense-only |
| Qdrant 集合 | `chunks_dense_bge-m3_v1` | Dense-only |

### 0.2 002 混合检索期望变化（进入默认路径的目标）

评测在 FR-019 扩充后的评测集上执行（原 11 条保留用于逐条对照 + 新增词汇精确/中文查询）。**进入默认检索路径的判定**（FR-021 / 宪法原则 X）：MRR 与 nDCG 在原 11 条上有可度量收益 **且** 硬性指标全部不被违反。

| 指标 | 001 基线 | 002 期望变化 | 期望值 / 约束 | 依据 |
|------|---------|-------------|--------------|------|
| **Recall@K (K=5)** | 1.0 | **不下降**（基线已饱和） | ≥ 1.0（原 11 条）；扩充集同样 ≥ 基线同集 Dense 重跑值 | SC-001 / FR-008 |
| **MRR (mean)** | 0.9091 | **可度量提升** | 原 11 条 ≥ 0.95（仅修复 query 5 `validateToken` 从 rank 2→1 即可使 mean 从 0.9091→0.9545） | SC-001 / FR-021 |
| **nDCG@K (mean)** | 0.9329 | **可度量提升** | 原 11 条 ≥ 0.96（仅修复 query 5 即可使 mean 从 0.9329→0.9665） | SC-001 / FR-021 |
| **延迟 P50** | 138.45 ms | **允许上升，须被记录并对照** | 期望 300–900 ms（新增 Sparse 召回 + RRF + Rerank），**不超过 30s 总超时护栏** | SC-005 / FR-015 |
| **延迟 P95** | 185.15 ms | **允许上升，须被记录并对照** | 期望 1200–3000 ms（CPU Rerank 在候选预算内），**不超过 30s 总超时护栏且 < 目标 Host Tool Call 超时** | SC-005 / FR-015 |

### 0.3 硬性验收指标（必须 100% 不被违反，否则不得进入默认路径）

| 硬性指标 | 001 基线 | 002 目标 | 验证方式 |
|---------|---------|---------|---------|
| 跨项目泄漏事件数 | 0 | **= 0**（SC-002 / FR-008） | 验收测试集断言 |
| MCP Schema 合法率 | 100% | **= 100%**（SC-003 / FR-009） | search_knowledge 输出契约校验 |
| 来源可定位率 | 100% | **= 100%**（SC-004 / FR-010） | 验收测试集断言 |

### 0.4 可重复性目标（SC-006）

- 同一环境连续两次运行的 Recall@K / MRR / nDCG 在 **1% 相对容差**内一致（沿用 001 非延迟可重复性要求）。
- 延迟指标标注为环境敏感，**不作为可重复性否决项**（沿用 001 基线报告 reproducibility 结论）。

### 0.5 可解释性目标（SC-007 / FR-020）

- 对照评测报告逐查询列出 Dense 基线排名 vs 混合检索排名，并对每个排名变化提供 Dense 分数、Sparse 分数（或词频权重）、融合分数与 Rerank 分数。

### 0.6 MRR/nDCG 提升的可度量性论证

基线中 query 5（`Find the definition of com.example.service.UserService#validateToken.`）的期望证据 `351194693498830853` 排在第 2 位（MRR=0.5），原因是 Dense 相似度中类级 Chunk（0.6151）略高于方法级 Chunk（0.6133）。BM25/Sparse 词汇精确匹配对包含 `validateToken` 的方法级 Chunk 给出更高词频权重，使融合后该 Chunk 升至 rank 1。

- **MRR**: query 5 从 0.5→1.0，11 条均值 0.9091→0.9545（+0.0454，+5.0% 相对）。扩充集新增的词汇精确查询中，Dense 因语义相近返回多个分数接近候选而排名错误的情况将进一步被 Sparse+Rerank 修正，提供额外可度量收益。
- **nDCG**: query 5 从 0.6309→1.0，11 条均值 0.9329→0.9665（+0.0336，+3.6% 相对）。

以上为**最小可度量收益下界**（仅修复 1 条查询），实际收益预期更高。进入默认路径的阈值即"原 11 条 MRR ≥ 0.95 且 nDCG ≥ 0.96 且硬性指标全通过"。

---

## 一、技术栈与选型决策

### 1.1 Sparse/BM25 词法检索实现路径

**Decision**: 采用 Qdrant 原生 Sparse Vector（命名稀疏向量）+ 后端确定性 BM25 稀疏编码器（jieba CJK 分词），在同一 Qdrant 集合中以命名向量 `dense` 与 `sparse` 并存。

**Rationale**:
- 蓝图 §8.1 明确"Qdrant 负责 Sparse/BM25 关键词检索"，§18.2 明确"BGE-M3 默认只提供 Dense Embedding，Sparse/BM25 由 Qdrant 负责"。采用 Qdrant 稀疏向量使 Qdrant 负责稀疏索引的存储与相似度计算，符合蓝图职责划分。
- Qdrant 1.7+ 原生支持命名稀疏向量（`sparse_vectors` 配置），可在同一 Collection 中与 Dense 向量并存，复用同一 Payload（`knowledge_scope_id` / `version_id` / `source_id`）与过滤条件，保证跨项目泄漏为零（FR-008）且不引入第二个集合的同步复杂度。
- Qdrant 内置 Full-Text Search（BM25 scoring）依赖其内置分词器，对 CJK（中文连续无空格）分词能力不足；而 FR-025 强制要求 CJK 分词。采用自定义稀疏编码器可在后端用 jieba 对中文精确切分后再生成稀疏向量，满足 FR-025。
- 宪法原则 VI（确定性控制优先）要求检索、过滤、融合、排序由确定性组件控制。BM25 稀疏编码是确定性算法（TF-IDF/BM25 词频权重，无随机性），由后端确定性代码计算后写入 Qdrant，Qdrant 只执行稀疏点积相似度——控制权在后端确定性组件，不依赖外部 LLM。
- 同一 Collection 命名向量方案使 Dense 与 Sparse 指向同一 Point（同一 `chunk_id`），融合时无需跨集合对齐，RRF 直接按 rank 融合即可。

**Alternatives Considered**:
- **Qdrant Full-Text Search（BM25 payload text index）**: 实现最简，但内置分词对 CJK 支持不足（按 Unicode 词边界/空格切分，中文会整段粘连），无法满足 FR-025。已排除。
- **FastEmbed `Qdrant/bm25` 稀疏模型**: FastEmbed 提供 BM25 sparse encoder，但其分词对 CJK 同样依赖空格/Unicode 边界，需额外 jieba 预处理，且引入 FastEmbed 依赖。不如直接在后端实现确定性 BM25 稀疏编码器（jieba + TF-BM25 权重）可控。已排除。
- **SPLADE 神经稀疏模型**: 语义稀疏表示质量高，但需额外嵌入模型（违反"002 不引入新嵌入模型"，FR-001/FR-012），且 CPU 推理延迟高。已排除。
- **独立 Sparse Collection**: Dense 与 Sparse 分属两个 Collection，融合时需跨集合对齐 `chunk_id`，增加同步复杂度与泄漏风险面。已排除，采用同 Collection 命名向量。

### 1.2 融合算法：RRF vs DBSF

**Decision**: 默认采用 **RRF（Reciprocal Rank Fusion）**，`k` 参数默认 60（经典值），保留 DBSF 作为可配置备选。

**Rationale**:
- RRF 是 rank-based 融合，不依赖两路分数的尺度对齐（Dense cosine ∈ [0,1] 与 Sparse BM25 分数量纲不同），鲁棒且实现简单。
- RRF 公式 `score(d) = Σ_i 1/(k + rank_i(d))` 完全确定性（宪法原则 VI），无随机扰动，打平时可保留稳定次序（FR-017）。
- Qdrant 1.10+ 的 `query_points` API 支持同集合多向量查询（Dense + Sparse）并返回各路分数，后端在拿到两路 ranked list 后执行 RRF，职责清晰。
- DBSF（Dense-Sparse Best Score Fusion，加权分数融合）需要分数归一化与权重调参，调试成本高，作为可配置备选保留但不在 002 首轮启用。

**Alternatives Considered**:
- **DBSF（加权分数融合）**: 需归一化 Dense 与 Sparse 分数到同尺度并调权重，参数敏感、首轮不易定标。作为 config 可选项保留，默认不用。已排除为默认。
- **纯 Sparse 取优（max of two scores）**: 不利用 rank 信息，易受单路噪声影响。已排除。

### 1.3 Cross-Encoder Rerank 集成

**Decision**: 采用 `BAAI/bge-reranker-v2-m3`，通过 sentence-transformers `CrossEncoder` 加载，本地 CPU 默认运行；RerankerProvider ABC 已在 001 `providers/base.py` 声明，002 实现具体 provider。

**Rationale**:
- 蓝图 §18.2 指定 `BAAI/bge-reranker-v2-m3` 为本地默认 Reranker。
- sentence-transformers `CrossEncoder('BAAI/bge-reranker-v2-m3')` 是成熟的加载路径，与 001 已用的 sentence-transformers Embedding 共享依赖栈，无需引入 FlagEmbedding 额外依赖。
- bge-reranker-v2-m3 是多语言 Cross-Encoder，原生支持中英混合查询（FR-025 中文评测用例的精排需求）。
- 蓝图 §18.5"Rerank 只处理融合后的有限候选"——Reranker 只接收 RRF 融合后截取的 ≤候选预算（默认 20）的候选，不在线全库重排，延迟可控。
- CPU 模式下 bge-reranker-v2-m3 对 query+passage 对的推理延迟约 10–30ms/对（8 核现代 CPU），20 候选约 200–600ms，落在 30s 总超时预算内。

**Alternatives Considered**:
- **FlagEmbedding 库 `FlagReranker`**: 同一模型另一加载路径，API 等价但引入额外依赖。sentence-transformers 已足够。已排除。
- **ONNX/OpenVINO 量化加速**: 蓝图 §18.5 允许 CPU 量化实现，作为 006 Runtime Hardening 的性能优化项保留，002 首轮用 FP16/FP32 CPU 推理即可。
- **远程 API Reranker**: Provider 接口保留（蓝图 §18.1），002 不启用远程路径。

### 1.4 CJK（中文）分词

**Decision**: 采用 **jieba**（精确模式）作为 CJK 分词器，集成于后端 BM25 稀疏编码器；英文/拉丁文走正则 token 化（小写化 + 标点剥离）；中英混合内容按字符类别分别切分后合并词表。

**Rationale**:
- jieba 是 Python 中文分词事实标准，纯 Python 实现无外部服务依赖，确定性输出（宪法原则 VI）。
- 稀疏编码器对 Chunk 正文与查询分别分词后计算 BM25 词频权重，写入 Qdrant 稀疏向量；查询时同一分词器处理查询文本，保证词表对齐。
- FR-025 明确"朴素空格分词 MUST NOT 作为 CJK 内容的唯一分词方式"，jieba 满足硬约束。
- jieba 精确模式对代码符号（`com.example.Service#method`）会保留点号与井号分隔的 token，结合英文正则可正确切分全限定符号路径。

**Alternatives Considered**:
- **pkuseg / THULAC**: 学术分词器，准确率略优但依赖重、加载慢，对代码语料收益不显著。已排除。
- **Qdrant 内置分词**: 对 CJK 不足，见 1.1。已排除。
- **HanLP**: 功能强但依赖与模型体积大，超出 002 需求。已排除。

### 1.5 知识版本能力清单扩展

**Decision**: 在 `KnowledgeVersion.capabilities` JSONB 中新增 `lexical_ready` 布尔字段；发布原子性要求 Dense 与 Sparse 索引均就绪后才置 `lexical_ready: true` 并转 `published`。

**Rationale**:
- 001 已预留扩展点（001 data-model.md §3.4 / §9）："001 的 capabilities 仅含 `dense_ready`，后续扩展 `lexical_ready`"。
- 蓝图 §8.4"入库任务只有在 PostgreSQL 元数据和该版本能力清单声明的必要派生索引都达到要求后，才能发布为可检索版本"。
- 同一 bge-m3 嵌入模型 + 同一切片策略上新增 Sparse，属同一 `index_version` 的新增能力，不触发宪法原则 VIII 不可混用（FR-012 / spec Assumptions）。
- 查询规划只调用已发布版本明确声明的能力（FR-013）：仅 `dense_ready` 的版本走 Dense-only；声明 `lexical_ready` 的版本才启用 Sparse 路径。

### 1.6 Sparse 索引构建时机与重建

**Decision**: Sparse 索引在入库流程 `embedding` 阶段之后新增 `sparse_index` 阶段构建；已有 Dense-only 版本通过用户触发重建（`POST /{id}/reprocess`）发布声明 `lexical_ready` 的新版本获得 Sparse 能力，系统不自动批量迁移（FR-023）。

**Rationale**:
- 001 `ProcessingRun.stages` JSONB 已支持阶段记录（credential_scan → parsing → chunking → embedding），002 新增 `sparse_index` 阶段，沿用同一处理运行框架。
- 重建沿用 001 的失败保护：Sparse 构建失败时版本保持 `draft`，旧 Dense-only 版本继续可用（FR-023 / SC-009）。
- 蓝图 §8.4"所有派生数据必须能够从原始知识源和版本信息重建"——Sparse 索引由 Chunk 正文确定性重建，满足可重建性（FR-014）。

### 1.7 对照评测框架扩展

**Decision**: 扩展 `eval/run_eval.py` 支持双模式（`--mode dense` / `--mode hybrid`），新增 `eval/run_comparison.py` 生成对照报告；评测集扩充保留原 11 条 + 新增词汇精确/中文查询。

**Rationale**:
- 001 `run_eval.py` 已实现 Recall@K/MRR/nDCG/P50/P95 计算与可重复性检查，002 复用指标计算逻辑，新增 Dense+Sparse+RRF+Rerank 检索路径与对照报告。
- FR-024 要求"同环境同会话先重跑 Dense 基线、再跑混合检索"以保证延迟增量公平——对照脚本在一次运行中先 Dense 后 Hybrid。
- FR-019 要求原 11 条保留保证逐条可比，扩充部分遵循 AI 生成 + 人工审核 + JSON 格式（沿用 001 `generate_dataset.py` 约定）。

### 1.8 混合检索路径接入方式（不改对外契约）

**Decision**: 在 `RetrievalService` 内部扩展为混合检索路径（Dense+Sparse 召回 → RRF 融合 → Rerank → 装箱），`search_knowledge` 与 `get_evidence` 对外契约与 Schema 完全不变（FR-009 / 宪法原则 VII）。

**Rationale**:
- 001 `retrieval_service.py` 的 `search()` 方法已封装检索编排，002 在其内部插入 Sparse 召回、融合、Rerank 步骤，对外输出结构（completion_status 四态 + evidence + gaps + request_id）不变。
- Rerank 分数作为内部排序依据，不暴露给外部 Agent（外部契约 `relevance_score` 仍为最终分数，已存在于 EvidenceItem schema）。
- 混合检索中间状态（Dense/Sparse/Fusion/Rerank 各路分数）记录在内部证据账本/对照报告中，不进入对外 MCP 响应（宪法原则 IV：MCP MUST NOT expose internal database structure）。

---

## 二、关键集成点与模式

### 2.1 Qdrant 集合与命名向量配置

现有 Dense 集合 `chunks_dense_bge-m3_v1` 需迁移为支持命名稀疏向量的集合，或新建支持稀疏向量的集合。由于 Qdrant 不支持向已有 Collection 动态添加 sparse vector config，**002 新建集合 `chunks_hybrid_bge-m3_v1`**（Dense + Sparse 命名向量并存），重建时写入新集合；旧 `chunks_dense_*` 集合保留供 Dense-only 版本检索。

命名向量配置：
- `dense`: VectorParams(size=1024, distance=Cosine) — bge-m3 Dense
- `sparse`: SparseVectorParams — BM25 稀疏向量

### 2.2 融合与 Rerank 的确定性次序（FR-017）

- RRF 融合按 `(rank, chunk_id)` 字典序作为稳定 tie-breaker，确保打平时次序确定。
- Rerank 打平时按 `(rerank_score_desc, fused_score_desc, chunk_id_asc)` 排序，无随机扰动。

### 2.3 降级与 partial 状态（FR-016）

- Sparse 超时/失败但 Dense 可靠 → `partial`，gaps 标注 `sparse_path_failed`。
- Rerank 超时/失败但融合候选可用 → `partial`，返回 RRF 排序结果，gaps 标注 `rerank_failed`。
- Dense 与 Sparse 均失败 → `no_evidence` 或 `failed`（按是否系统异常区分）。

### 2.4 并发隔离（FR-018）

沿用 001 请求级隔离（5 并发），混合检索中间状态（融合中间 list、Rerank 候选）均为请求局部变量，不跨请求共享，无串扰风险。

---

## 三、Constitution 合规例外

无例外。所有宪法原则（I–X）与硬性约束在 002 设计中均满足：
- 原则 VIII（版本不可混用）：002 在同一 bge-m3 + 同一切片策略上新增 Sparse，属新增能力非混用（FR-012）。
- 原则 X（评测驱动）：MRR/nDCG 可度量收益 + 硬性指标全通过后才进入默认路径（FR-021 / §0.2）。
- 原则 VII（接口独立演进）：不改对外 MCP 契约（FR-009）。

详细 Constitution Check 见 plan.md。

---

## 四、决策摘要表

| 决策项 | 选择 | 理由（摘要） |
|--------|------|-------------|
| Sparse/BM25 | Qdrant 命名稀疏向量 + 后端确定性 BM25 编码器（jieba CJK） | 蓝图职责划分 + CJK 支持 + 确定性控制 |
| 融合算法 | RRF（k=60 默认），DBSF 备选 | rank-based 鲁棒、不依赖分数尺度对齐、确定性 |
| Reranker | bge-reranker-v2-m3 via sentence-transformers CrossEncoder | 蓝图默认模型 + 共享依赖栈 + 多语言 |
| CJK 分词 | jieba 精确模式 | 中文分词事实标准、确定性、纯 Python |
| 能力清单 | capabilities 新增 lexical_ready | 001 已预留、发布原子性、查询规划能力门控 |
| 索引构建 | 入库新增 sparse_index 阶段 | 沿用 ProcessingRun 框架、可重建 |
| 重建迁移 | 用户触发重建发新版本，不自动批量迁移 | FR-023 失败保护 |
| 评测框架 | 扩展 run_eval 双模式 + run_comparison 对照报告 | 复用 001 指标计算 + 逐查询可解释 |
| 对外契约 | 不变，内部路径增强 | FR-009 / 宪法原则 VII |
