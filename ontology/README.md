# 语义权威包

Stage 8 的 `config/ontology_contract.json` 是最小设计输入，`minimal_turbine.ttl` 由 `scripts/build_stage8_ontology.py` 重建，保留五个顶层类、八个运行类及其固定 IRI。候选映射和审核不自动晋级为正式词汇或 Release，审核事实以独立 Overlay 为准。

Stage 9 工程语义权威由 `minimal_turbine.ttl` 和 `stage9_core.ttl` 的本地组合，以及 `stage9_shapes.ttl` 构成。运行 Schema 的唯一结构真源是 `config/semantic_runtime.schema.json`；类、属性、数据类型与端点由 OWL 声明，基数、断言类型、单位及交叉字段约束由 SHACL 声明。

运行输入是包含 `nodes` 与 `relations` 的 JSON bundle。`validate_runtime_payload` 执行 Schema → RDF Dataset → OWL 词汇检查 → pySHACL → Validation Report。

阶段 10 只在 OWL/SHACL 语义链之外增加稳定身份、Revision 生命周期和可重验结构化缓存。其可选批次字段由 `config/runtime_run.schema.json` 定义，跨模块约束由 `config/runtime_contract.json` 定义；缓存和任何临时投影不会成为本体或知识真源。

Stage 8 另有 `modeling_pattern_coverage` 覆盖审计：它只验证少量真实 Stage 7 candidate 能否代表八种后续建模模式，不扩大最小 OWL，不生成正式词汇。
