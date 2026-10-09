# 阶段 9：OWL/SHACL 语义权威包

本阶段已基于当前阶段 8 退出审计重验，`stage9_exit_audit.json` 为当前结果，状态 `complete`。原 3 组用户确认的研发样本绑定了现行 Registry：`stage9_current_source_bindings.json` 记录原件物理页视觉核对、当前完整页面文字、4 条不变的历史引文和语义核对；它不产生新的用户确认，也不把样本升级为正式知识。

工程语义权威是 `ontology/minimal_turbine.ttl` 与 `ontology/stage9_core.ttl` 的本地组合和 `ontology/stage9_shapes.ttl`。运行 JSON 结构由 `config/semantic_runtime.schema.json` 校验；字段与调用说明见 `ontology/README.md` 和运行代码。

```text
现有准入研发输入 → research_adapter → JSON Schema → RDF Dataset
→ OWL vocabulary / generated endpoint shapes → pySHACL
→ Validation Report → stage3 projection → 既有研发 Neo4j 导入入口
```

失败报告在投影前原子阻断整批，不通过修改数据库修复知识。既有真实研发样本与公开合成样本分别对账，均不代表全文知识或正式 Release。独立代码/合同/链路复核记录为 `stage9_semantic_review.json`，不冒充业务知识人工批准。

本地执行 `scripts/audit_stage9_exit.py --current-source-bindings data/stage9/stage9_current_source_bindings.json`（必须使用项目专用 Python）。该入口核对正式输入指纹、正反例、数量、真实消费者及独立复核，并运行阶段 9 消费者测试与阶段 11 之前的项目回归。阶段 11 已按现行输入重验通过；阶段 12 正在重建，失效的旧可重建运行结果已删除。唯一退出证据为 `stage9_exit_audit.json`，项目状态以 `../project_state.json` 为准。

阶段 10 消费本阶段退出审计 `data/stage9/stage9_exit_audit.json` 与当前 Registry 身份记录。阶段 10 的知识生命周期合同和退出记录见 `config/runtime_contract.json`、`config/runtime_run.schema.json` 及 `../stage10/stage10_audit.json`；阶段 10 不改写本阶段 OWL/SHACL 权威文件。
