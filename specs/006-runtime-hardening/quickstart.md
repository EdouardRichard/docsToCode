# Quickstart: Runtime Hardening (006)

**Branch**: `006-runtime-hardening` | **Date**: 2026-09-03 | **Spec**: [spec.md](./spec.md)

> 端到端验证指南。契约引用见 [contracts](./contracts/)，数据模型见 [data-model.md](./data-model.md)，评测目标见 [research.md §0](./research.md)。本指南只约束可观测的期望结果与验收口径；具体命令、迁移与完整测试套件由 tasks.md 与实现阶段提供。

## 前置条件

- 001–005 已交付并可运行：Web 管理、上传/切片/入库、Dense+Sparse+RRF+Rerank、图扩展、三 Agent 编排、MCP `search_knowledge`/`get_evidence`。
- PostgreSQL 与 Qdrant 可用（docker-compose；共享存储，蓝图 §8/§21.2）。
- 评测基线工件存在：`eval/baseline_report.json`（001 Dense，11 条）；`eval/eval_dataset.json` 原批次可执行。
- 已发布一个声明所需检索能力的知识版本（冒烟用 Dense 能力即可）。
- 本机已安装 DeepSeek Harness（唯一必过参考客户端，SC-001；ChatGPT App 与 Claude Code 记录兼容性状态、不阻塞）。
- 运行配置就绪（默认值即合法）：实例模式、租约参数（续约 30s/过期 90s）、Provider 配置（默认 local CPU Embedding/Reranker + remote LLM）、并发上限（LLM 4/8、Embedding 8/16、Reranker 2/4）、TTL（默认 7 天）、超时档位（各 Host 默认 > 服务端总超时 30s）。

## 场景 1 — 启动单写多读部署

**验证**：User Story 1/FR-001/FR-004/FR-005/SC-001。

1. 启动 writer 部署：管理进程（抢租约成功）+ writer MCP 进程（独立端口）；随后启动 2 个 reader MCP 进程（各自独立端口，默认绑定 127.0.0.1）。
2. 期望：writer 管理进程持有 active 租约（[writer-lease.schema.json](./contracts/writer-lease.schema.json) 校验通过）；管理端/管理 API/入库/发布可用。
3. 期望：reader 实例只提供只读 MCP 检索——不启动管理面（`server.py --mode reader` 显式报错）、不运行入库/迁移/TTL 清理；`get_evidence` 从共享数据库读取 Chunk 正文/父级上下文/来源定位。
4. 期望：DeepSeek Harness 分别经 writer MCP 与 reader MCP 端点完成 `search_knowledge` + `get_evidence` 端到端调用并通过输出 Schema 校验（澄清 Q5 双形态必过）。

## 场景 2 — 双写拒绝与租约恢复

**验证**：FR-002/FR-003/SC-002、Edge Cases。

1. writer 已持租约时，再次以 writer 模式启动第二个管理进程。
2. 期望：第二个 writer 100% 被拒绝进入写模式，错误信息含持有者 instance_id 与到期时间；不静默降级为 reader；验收期间双写事件数 = 0。
3. 强杀 writer 管理进程（不释放租约），等待过期窗口（固化默认 90s）后重启。
4. 期望：新 writer 获得租约继续维护；回收窗口内任何第二个 writer 仍被拒；期间 reader 检索不受影响（读路径不依赖租约）。

## 场景 3 — reader 独立于 writer

**验证**：FR-005/SC-003。

1. 停止 writer 的全部进程（管理与 MCP）。
2. 期望：reader 完成 `search_knowledge` 与 `get_evidence` 的成功率 100%；因 writer 不可用导致的 reader 失败数 = 0；无任何对 writer 本地原始文件路径的访问。

## 场景 4 — Provider 配置与启动校验

**验证**：User Story 2/FR-008~FR-013/SC-004/SC-005。

1. 仅通过运行配置将三类能力分别指向不同 Provider（local CPU Embedding、local CPU Reranker、remote API LLM，默认组合），执行检索端到端验证对外契约不变。
2. 期望：三类能力各自使用所配 Provider；MCP 输出 Schema、`completion_status` 四态与来源定位格式不受 Provider 选择影响。
3. 依次提交 ≥3 类非法配置：未知 provider_type、不可达 remote 端点、Embedding 维度与活跃集合不匹配。
4. 期望：启动 100% 显式失败并给出可纠正错误（[provider-config.schema.json](./contracts/provider-config.schema.json) 的 validation.errors），静默回退数 = 0。
5. 声明不同维度/模型的 Embedding 并尝试直接作用于既有已发布索引版本。
6. 期望：拒绝混装；唯一合法路径为创建新索引版本 + 重新向量化（SC-005，混装事件 = 0）。

## 场景 5 — 追踪与运行指标

**验证**：User Story 3/FR-016~FR-020/SC-006/SC-007。

1. 执行一组已知构成的验收请求批次（跨 writer 与 reader 实例、覆盖四种检索模式），随后查询指标端点。
2. 期望：请求量（按实例模式/Tool）、`completion_status` 分布、P50/P95、子路径耗时、Provider 用量与批次逐条对账（对账偏差 = 0；响应经 [runtime-metrics.schema.json](./contracts/runtime-metrics.schema.json) 校验、秒级返回、全文无正文）。
3. 关闭 `TRACE_BODY_ENABLED` 后重放请求。
4. 期望：四种检索模式（dense/hybrid/graph_enhanced/agentic）新增运行记录无查询/证据正文（query_text 为 NULL），ID/状态/耗时/错误保留完整率 100%；指标仍可查询且无正文。
5. 将 TTL 配置缩短并触发清理（或等待清理周期）。
6. 期望：过期运行记录被清理，清理量计入指标（TTL 清理量）；知识源/Chunk/向量/图关系不受影响。

## 场景 6 — 超时档位校验

**验证**：FR-021/SC-010、Edge Cases。

1. 查看默认超时档位：服务端总超时 30s < 各 Host Tool Call 超时（默认 60000/60000/120000ms）。
2. 期望：配置校验通过；检索超时行为产生 `partial`/`no_evidence`/`failed` 而非无响应。
3. 配置一个反向档位（某 Host 超时 ≤ 服务端总超时）并重启。
4. 期望：启动校验显式拒绝该配置（蓝图 §19：服务端超时必须小于 Host 配置）。

## 场景 7 — 非回归三项判定（对照评测要求：无）

**验证**：User Story 4/FR-027/FR-028/SC-008/SC-009。

1. 运行 `eval/instance_form_smoke.py`：001 基线 11 条 Markdown/Java 评测集经 MCP HTTP 分别对 writer 与 reader 端点执行。
2. 期望：非延迟指标（Recall@K/MRR/nDCG）与 `baseline_report.json` 逐条对照、1% 相对容差内一致；P50/P95 记录对照并标注环境敏感；不设质量阈值、不作质量声明（FR-027：对照要求为"无"）。
3. 运行 001–005 既有 pytest 验收测试集。
4. 期望：全部通过（非回归）。
5. 在验收集上断言硬性指标：跨项目泄漏 = 0、MCP Schema 合法率 = 100%、来源可定位率 = 100%（含经 reader 实例的请求与缺 `project_scope` 拒绝场景）。

## 场景 8 — 跨实例 ID 唯一性

**验证**：FR-030/SC-013、Edge Cases（澄清 Q6）。

1. writer + 2 个 reader 并发执行验收批次（含运行记录写入共享库）。
2. 期望：`instance_registry` 活跃 worker_id 互异；运行记录主键冲突/ID 重复事件数 = 0；全部运行记录 ID 保持 64 位雪花格式。
3. 将两个实例显式配置为相同 `WORKER_ID` 并启动。
4. 期望：第二个实例启动被显式拒绝（唯一约束检测，错误信息含冲突实例标识）。
