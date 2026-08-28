# 数据模型：002 Hybrid Retrieval Precision

**Feature**: 002-hybrid-retrieval-precision
**状态**: Draft
**日期**: 2026-08-27
**依据**: 系统设计蓝图 §8.1 / §8.4 / §9 / §12 / §13 / §18.2；Feature Spec FR-001 ~ FR-025；Constitution I–X
**基线**: 001 data-model.md（本文档在其上扩展，不重复已定义的完整实体，仅描述 002 变更与新增）

---

## 1. 概述

本文档定义 002 Feature 相对 001 的数据模型扩展。002 在 001 已建立的 PostgreSQL 控制面与 Qdrant Dense 集合之上：

- 扩展 Qdrant 集合为 Dense + Sparse 命名向量并存（新建 `chunks_hybrid_*` 集合）
- 扩展 `KnowledgeVersion.capabilities` 新增 `lexical_ready`
- 扩展 `ProcessingRun.stages` 新增 `sparse_index` 阶段
- 扩展 `RetrievalRun` 记录混合检索子路径耗时与证据引用追踪
- 新增检索路径内部数据结构（融合候选、Rerank 候选）的定义（瞬态，非持久化实体）

**不改 001 已确立的对外 MCP 契约**（FR-009 / 宪法原则 VII）。

---

## 2. 实体关系图（增量）

001 实体关系图不变。002 在以下实体上做字段级扩展与新增瞬态结构：

```
001 既有实体（不变）          002 扩展点
─────────────────────────    ─────────────────────────────────
KnowledgeVersion  ──────────► capabilities 新增 lexical_ready
ProcessingRun     ──────────► stages 新增 sparse_index 阶段
RetrievalRun      ──────────► 新增子路径耗时 JSONB + evidence_ref_ids

新增瞬态结构（请求内生命周期，不持久化为独立表）:
┌──────────────────────┐    ┌──────────────────────┐
│  FusedCandidate      │    │  RerankCandidate     │
│  (RRF 融合产物)       │    │  (Rerank 输入/输出)    │
└──────────────────────┘    └──────────────────────┘
```

---

## 3. 实体扩展定义

### 3.1 KnowledgeVersion（扩展）

001 已定义。002 扩展 `capabilities` JSONB 的能力清单。

**capabilities JSONB 结构（002 扩展）**：

```json
{
  "dense_ready": true,
  "lexical_ready": true
}
```

**新增字段语义**：

| 能力键 | 类型 | 002 默认 | 说明 |
|--------|------|---------|------|
| `dense_ready` | boolean | true | Dense 向量索引就绪（001 已有） |
| `lexical_ready` | boolean | false → true | Sparse/BM25 词法索引就绪；声明 true 的版本才启用 Sparse 路径（FR-011/FR-013） |

**扩展验证规则**：
- 发布原子性（蓝图 §8.4）：版本转 `published` 前，声明的所有能力对应派生索引均就绪。声明 `lexical_ready: true` 时，Dense 向量与 Sparse 稀疏向量必须均已写入 Qdrant 并校验通过。
- 查询规划能力门控（FR-013）：混合检索路径只查询 `capabilities.lexical_ready = true` 的已发布版本；仅 `dense_ready` 的版本继续支持 Dense-only 检索，不被纳入 Sparse 路径。
- 同一 `index_version` 内 Dense 与 Sparse 共存属同一嵌入模型（bge-m3）与同一切片策略上的能力扩展，不触发宪法原则 VIII 不可混用（FR-012）。

**状态转换（不变）**：沿用 001 §4.2，draft → published → superseded。发布门控增加 Sparse 就绪检查。

---

### 3.2 ProcessingRun（扩展）

001 已定义。002 扩展 `stages` JSONB 的处理阶段序列。

**stages JSONB 结构（002 扩展）**：

```json
[
  { "stage": "credential_scan", "status": "completed", ... },
  { "stage": "parsing",         "status": "completed", ... },
  { "stage": "chunking",        "status": "completed", "details": {"chunks_created": 15} },
  { "stage": "embedding",       "status": "completed", "details": {"model": "BAAI/bge-m3", "vectors": 15} },
  { "stage": "sparse_index",    "status": "completed", "details": {"encoder": "bm25_jieba", "vectors": 15, "tokenizer": "jieba_precise"} }
]
```

**新增阶段**：

| 阶段 | 顺序 | 说明 |
|------|------|------|
| `sparse_index` | embedding 之后 | 对每个 Chunk 正文执行 jieba+BM25 稀疏编码，写入 Qdrant `sparse` 命名向量 |

**验证规则**：
- `sparse_index` 阶段失败时版本保持 `draft`，不暴露半成品（沿用 001 失败保护，蓝图 §8.4）。
- 重建（reprocess）时重新执行全部阶段含 `sparse_index`；Sparse 索引可从 Chunk 正文确定性重建（FR-014）。
- 002 不修改 001 已有的 `credential_scan` / `parsing` / `chunking` / `embedding` 阶段语义。

---

### 3.3 RetrievalRun（扩展）

001 已定义。002 扩展检索运行记录以支持混合检索子路径追踪（FR-022 / 蓝图 §13 证据账本）。

**新增字段**：

| 字段 | 类型 | 约束 | 描述 |
|------|------|------|------|
| `retrieval_mode` | VARCHAR(16) | NOT NULL, DEFAULT 'dense', CHECK IN ('dense', 'hybrid') | 检索模式：dense=001 Dense-only；hybrid=002 混合检索 |
| `subpath_timings` | JSONB | NULLABLE | 混合检索各子路径耗时明细（仅 hybrid 模式非空） |
| `evidence_ref_ids` | JSONB | NOT NULL, DEFAULT '[]' | 返回的证据 ID 列表（支持问题回溯） |

**subpath_timings JSONB 结构**：

```json
{
  "dense_recall_ms": 12.3,
  "sparse_recall_ms": 8.1,
  "fusion_ms": 0.4,
  "rerank_ms": 245.7,
  "total_ms": 266.5
}
```

**evidence_ref_ids JSONB 结构**：

```json
["351194693498830853", "351194693498830848"]
```

**扩展验证规则**：
- `retrieval_mode = 'hybrid'` 时 `subpath_timings` 非 NULL。
- `retrieval_mode = 'dense'` 时 `subpath_timings` 为 NULL（兼容 001 既有记录）。
- `completion_status = 'partial'` 时 `subpath_timings` 中应标注失败/超时路径。
- 本表仍为追加式记录，不支持更新或删除（仅 TTL 清理，沿用 001 §3.7）。

---

## 4. 新增瞬态数据结构（请求生命周期，非持久化）

以下结构在单次 `search_knowledge` 调用内构造、消费、丢弃，不持久化为独立表。其契约定义见 `contracts/hybrid-retrieval-trace.schema.json`（供对照评测报告与内部追踪引用）。

### 4.1 FusedCandidate（融合候选）

Dense 与 Sparse 召回结果经 RRF 融合后的候选。

| 字段 | 类型 | 描述 |
|------|------|------|
| `chunk_id` | string | Chunk 唯一标识（Snowflake ID 字符串） |
| `knowledge_scope_id` | string | 知识域标识（沿用 common 定义） |
| `source_retrievers` | array[string] | 命中该候选的检索器列表，取值 `["dense"]` / `["sparse"]` / `["dense","sparse"]` |
| `dense_score` | number/null | Dense 相似度分数（cosine ∈ [0,1]）；仅 Dense 命中时有值 |
| `sparse_score` | number/null | Sparse BM25 分数（词频权重）；仅 Sparse 命中时有值 |
| `dense_rank` | integer/null | Dense ranked list 中的排名（1-based）；未命中为 null |
| `sparse_rank` | integer/null | Sparse ranked list 中的排名（1-based）；未命中为 null |
| `fused_score` | number | RRF 融合分数 `Σ 1/(k + rank)` |

**确定性 tie-breaker**（FR-017）：融合排序键为 `(fused_score_desc, dense_rank_asc, sparse_rank_asc, chunk_id_asc)`，无随机扰动。

### 4.2 RerankCandidate（Rerank 候选）

融合后截取的有限候选，送入 Cross-Encoder Rerank。

| 字段 | 类型 | 描述 |
|------|------|------|
| `chunk_id` | string | Chunk 唯一标识 |
| `content_text` | string | Chunk 正文（用于 Cross-Encoder 输入，凭据已替换） |
| `fused_score` | number | 融合分数（继承自 FusedCandidate） |
| `rerank_score` | number | Cross-Encoder 精排分数（Rerank 后填充） |

**候选预算护栏**（FR-015 / FR-005）：RerankCandidate 数量 ≤ 配置的候选预算（默认 20，蓝图 §18.5），不在线全库重排。

**Rerank 排序键**（FR-017）：`(rerank_score_desc, fused_score_desc, chunk_id_asc)`，打平时确定。

---

## 5. Qdrant 集合索引策略（扩展）

### 5.1 集合命名

002 新增混合检索集合（Dense + Sparse 命名向量并存）：

```
chunks_hybrid_{index_version}
```

- `index_version` 由 `embedding_model` + 切片策略版本组合，与 001 相同（如 `bge-m3_v1`），因 002 不改嵌入模型与切片策略。
- 旧 `chunks_dense_{index_version}` 集合保留，供仅声明 `dense_ready` 的旧版本 Dense-only 检索。

### 5.2 Point 结构（命名向量）

```json
{
  "id": "<chunk_id as u64>",
  "vector": {
    "dense":  [0.012, -0.034, ...],
    "sparse": {
      "indices": [102, 4096, 8812, ...],
      "values":  [0.18, 0.42, 0.07, ...]
    }
  },
  "payload": {
    "knowledge_scope_id": "<snowflake_id>",
    "source_id": "<snowflake_id>",
    "version_id": "<snowflake_id>",
    "chunk_id": "<snowflake_id>",
    "chunk_type": "section | symbol",
    "position_path": "## 安装 > ### 配置",
    "start_line": 42,
    "end_line": 87,
    "index_version": "bge-m3_v1",
    "embedding_model": "BAAI/bge-m3"
  }
}
```

### 5.3 命名向量配置

| 命名向量 | 配置 | 说明 |
|---------|------|------|
| `dense` | VectorParams(size=1024, distance=Cosine, hnsw={m:16, ef_construct:200}) | bge-m3 Dense，沿用 001 §5.4 |
| `sparse` | SparseVectorParams (默认配置) | BM25 稀疏向量，由后端确定性编码器生成 |

### 5.4 Payload 索引（沿用 001 §5.3，新增无需额外字段）

| Payload 字段 | 索引类型 | 用途 |
|-------------|---------|------|
| `knowledge_scope_id` | Keyword Index | 知识域过滤（跨项目泄漏为零的硬保障） |
| `version_id` | Keyword Index | 版本过滤 |
| `chunk_type` | Keyword Index | 按类型过滤 |
| `source_id` | Keyword Index | 按知识源过滤/删除 |
| `index_version` | Keyword Index | 索引版本隔离 |

### 5.5 Sparse 向量生成（后端确定性编码器）

Sparse 向量由后端 `BM25SparseEncoder` 在 `sparse_index` 阶段对每个 Chunk 正文生成：

1. **分词**：jieba 精确模式（CJK）+ 正则 token 化（拉丁文小写化 + 标点剥离）；中英混合按字符类别分别切分后合并。
2. **权重计算**：BM25 词频权重（TF-IDF 风格，确定性，无随机性）。
3. **输出**：稀疏向量 `{indices: [term_id...], values: [weight...]}`，term_id 为词表的稳定整数映射。
4. **查询侧**：同一编码器对查询文本分词 + 查词表权重，生成查询稀疏向量。

**确定性保证**（宪法原则 VI）：同输入恒定同输出，词表为构建期冻结的稳定映射，不在线学习。

### 5.6 检索过滤（混合模式）

Dense 与 Sparse 查询均强制携带相同 Payload 过滤：

```json
{
  "filter": {
    "must": [
      { "key": "knowledge_scope_id", "match": { "value": "<scope_id>" } },
      { "key": "version_id", "match": { "value": "<published_version_id>" } }
    ]
  }
}
```

跨项目检索时对每个项目作用域分别过滤查询，结果合并保留知识域身份（FR-006）。

---

## 6. PostgreSQL DDL 变更

### 6.1 RetrievalRun 表扩展（ALTER）

```sql
-- 002 扩展：检索模式与子路径追踪
ALTER TABLE retrieval_runs
    ADD COLUMN retrieval_mode    VARCHAR(16) NOT NULL DEFAULT 'dense'
        CHECK (retrieval_mode IN ('dense', 'hybrid')),
    ADD COLUMN subpath_timings   JSONB,
    ADD COLUMN evidence_ref_ids  JSONB NOT NULL DEFAULT '[]';

-- 兼容约束：hybrid 模式必须有子路径耗时
ALTER TABLE retrieval_runs
    ADD CONSTRAINT chk_hybrid_timings
    CHECK (retrieval_mode <> 'hybrid' OR subpath_timings IS NOT NULL);
```

**兼容性**：001 既有记录 `retrieval_mode` 默认 `'dense'`，`subpath_timings` 为 NULL，`evidence_ref_ids` 为 `'[]'`，向后兼容。

### 6.2 新增索引

```sql
CREATE INDEX idx_rr_mode_created ON retrieval_runs (retrieval_mode, created_at);
```

| 索引 | 用途 |
|------|------|
| `idx_rr_mode_created` | 按检索模式与时间范围查询（区分 dense/hybrid 记录） |

### 6.3 无新增独立表

- **不新增** graph_nodes / graph_edges（属 004，001 data-model.md §9 已预留排除）。
- **不新增** 追加式证据账本明细表（属 005，001 §9 已预留排除）。002 的子路径追踪以 `retrieval_runs` 的 JSONB 字段承载，不引入新表。
- 融合候选与 Rerank 候选为瞬态结构，不持久化。

---

## 7. 跨存储一致性规则（扩展）

### 7.1 共享标识符（不变，沿用 001 §7.1）

Qdrant Payload 与 PostgreSQL 共享 `knowledge_scope_id` / `source_id` / `chunk_id` / `version_id` / `index_version`。Sparse 向量与 Dense 向量属同一 Point，共享全部 Payload 字段。

### 7.2 写入顺序（扩展）

1. **先写 PostgreSQL**：Chunk 元数据写入 `chunks` 表（沿用 001）。
2. **写 Dense 向量**：bge-m3 Dense 向量写入 Qdrant `chunks_hybrid_*` 的 `dense` 命名向量。
3. **写 Sparse 向量**：BM25 稀疏向量写入同一 Point 的 `sparse` 命名向量（可合并到同一次 upsert）。
4. **最后更新版本状态**：Dense 与 Sparse 均就绪后，置 `capabilities = {"dense_ready": true, "lexical_ready": true}` 并转 `published`。

步骤 3 失败时版本保持 `draft`，可重试或清除重建（沿用 001 §7.2 失败保护）。可选降级：若用户只需 Dense，可发布仅 `dense_ready` 的版本（不发 `lexical_ready`）。

### 7.3 重建一致性（FR-014 / FR-023）

- 重建时从原始知识源重新解析、切片、Dense 化、Sparse 化，全部派生索引可从源 + 版本元数据重建（蓝图 §8.4）。
- 重建期间旧 Dense-only 版本继续可检索，不自动批量迁移（FR-023）。
- 新版本发布成功后旧版本转 `superseded`，其 Chunk/向量保留供证据展开（沿用 001）。

---

## 8. 002 Feature 范围限定

以下**不在** 002 范围内（沿用 001 data-model.md §9 排除清单）：

| 排除项 | 预留方式 | 后续 Feature |
|--------|---------|-------------|
| Graph 节点/边表 | 不含 graph_nodes/graph_edges 表 | 004 Graph RAG |
| 追加式证据账本明细表 | RetrievalRun 以 JSONB 承载摘要 | 005 Agentic Retrieval |
| LLM 语义判断节点 | 检索链路不含 LLM 判断 | 005 |
| 新文件格式 | `format` CHECK 不变（markdown/java） | 003 |
| 增强模型（Qwen3 等） | 不切嵌入模型，同一 bge-m3 | 006 |
| ONNX/OpenVINO 量化 Reranker | Reranker FP CPU 推理 | 006 |

---

## 9. 附录：BM25 稀疏编码器算法摘要

**输入**：Chunk 正文文本 `content_text`（凭据已替换）。
**分词**：
- CJK 字符（Unicode 范围）：jieba 精确模式切分。
- 拉丁字符：小写化 + 正则 `[a-z0-9_]+` 提取（保留点号/井号分隔的代码符号 token，如 `validateToken`、`com.example`）。
**权重**：BM25 词频饱和权重 `tf * (idf_norm)`，idf 基于当前知识域文档频率（构建期计算，冻结）。
**输出**：稀疏向量 `{indices, values}`，term_id 来自冻结词表的稳定整数映射。
**确定性**：同输入 → 同输出；词表不在线更新（宪法原则 VI）。
