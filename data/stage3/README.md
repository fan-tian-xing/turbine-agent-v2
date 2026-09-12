# 阶段 3：最小研发验证闭环

本目录记录阶段 3 的研发验证产物，不是正式 Release，也不包含私有资料、盲测题或真实资料正文。项目当前状态唯一以 `../project_state.json` 为准，阶段 3 的门禁证据见 `stage3_exit_audit.json`。

主要产物：

- `initial_batch_manifest.json`：从阶段 2B 已准入来源中冻结 15 个代表页的试点采样入口；
- `review_metrics.json`：人工审核结果和错误类别记录；
- `real_trial_summary.json`：15 个真实试点页面的抽取摘要，其中 3 组 Evidence 已由用户确认；
- `real_trial_confirmation.json`：用户确认的三组真实页面 Evidence/Statement 及其 Asset/Revision/Page/SourceSpan 链接；
- `real_trial_benchmark.json`：3 个已确认真实案例的 JSON baseline/图投影候选集对照；
- `real_trial_execution.json`：由代码重放 3 个真实确认案例后的检索、Claim 校验和延迟结果；
- `llm_live_replay_2026-09-08.json`：当前代码与真实 LLM 的可审计重放摘要，不保存 API Key 或原始模型响应；
- `stage3_exit_audit.json`：阶段 3 退出审查清单及下一阶段入口；
- `stage3_dead_code_orphan_audit.json`：独立的 dead-code/orphan-output 静态审查记录；
- `baseline_comparison.json`：20 个 Fixture 问题及 3 个真实确认案例的 baseline/投影一致性记录；
- `tests/fixtures/stage3/corpus.json`：三类资料角色的公开/合成 Fixture Corpus；
- `src/turbine_kg/stage3/`：Asset → Logical Document → Revision → Page → SourceSpan → Evidence → EngineeringStatement 的最小链路、确定性适用性匹配、JSON 调试基线、Equipment/Component/Process/Procedure/Step/QuantityValue 投影、Neo4j 试点和 Claim 校验。

CLI smoke 示例（在项目根目录执行）：

```text
$env:PYTHONPATH="src"
$projectPython = "D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe"
& $projectPython -m turbine_kg.stage3.cli --corpus tests/fixtures/stage3/corpus.json --question "What is the cold shaft alignment limit?" --context-json tests/fixtures/stage3/context-n300.json
```

研发闭环记录包括：首批 15 页抽取、3 组真实 Evidence 确认、4 条工程结论的隔离 Neo4j 试点投影，以及中文问题的来源、页码和 Evidence 回查。LLM 适配器只接受带 Evidence ID、页码、对象、适用条件和 Claim 类型的结构化回答；真实重放、Claim 校验和最终答案组装均保留在本目录的审计与回放记录中。外部 LLM 只接收检索到的最小证据片段。正式首批处理仍需遵守阶段 15 的准入门禁。

LLM 端点按主模型、备用模型 1、备用模型 2 顺序尝试：只有连接失败、超时或 HTTP 服务不可用时才切换备用模型；如果接口已经返回内容但 JSON/Claim/页码/对象/适用性校验失败，则立即报告校验错误，不用备用模型掩盖回答质量问题。
