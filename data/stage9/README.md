# 阶段 9：OWL/SHACL 语义权威包

工程语义权威是 `ontology/minimal_turbine.ttl` 与 `ontology/stage9_core.ttl` 的本地组合和 `ontology/stage9_shapes.ttl`。运行 JSON 结构由 `config/semantic_runtime.schema.json` 校验；字段与调用说明见 `ontology/README.md` 和运行代码。

```text
现有准入研发输入 → research_adapter → JSON Schema → RDF Dataset
→ OWL vocabulary / generated endpoint shapes → pySHACL
→ Validation Report → stage3 projection → 既有研发 Neo4j 导入入口
```

失败报告在投影前原子阻断整批，不通过修改数据库修复知识。既有真实研发样本与公开合成样本分别对账，均不代表全文知识或正式 Release。独立代码/合同/链路复核记录为 `stage9_semantic_review.json`，不冒充业务知识人工批准。

本地执行 `scripts/audit_stage9_exit.py`（必须使用项目专用 Python）。该入口核对正式输入指纹、正反例、数量、真实消费者及独立复核，并运行针对性与全量测试；唯一退出证据为 `stage9_exit_audit.json`，项目状态以 `../project_state.json` 为准。

阶段 9 的合法下游输入已交给阶段 10：`data/stage9/stage9_exit_audit.json` 与当前 Registry 身份记录。阶段 10 的运行合同和退出记录见 `config/runtime_contract.json`、`config/runtime_run.schema.json` 及 `../stage10/stage10_audit.json`；阶段 10 不改写本阶段 OWL/SHACL 权威文件。
