# 语义权威包

Stage 8 的 `config/ontology_contract.json` 是最小设计输入，`minimal_turbine.ttl` 由 `scripts/build_stage8_ontology.py` 重建，保留五个顶层类、八个运行类及其固定 IRI。候选映射和审核不自动晋级为正式词汇或 Release，审核事实以独立 Overlay 为准。

Stage 9 工程语义权威由 `minimal_turbine.ttl` 和 `stage9_core.ttl` 的本地组合，以及 `stage9_shapes.ttl` 构成。后者只扩展当前消费者需要的来源定位类/属性和定义，不重定义 Stage 8 类和关系端点。运行 Schema 的唯一结构真源是 `config/semantic_runtime.schema.json`；类、属性、数据类型与端点由 OWL 声明，基数、断言类型、单位及交叉字段约束由 SHACL 声明。

运行输入是包含 `nodes` 与 `relations` 的 JSON bundle。节点使用绝对 IRI、OWL 本地类名和 datatype properties；关系使用 source/predicate/target。运行时从 OWL 派生内存词汇清单与端点 shapes，在同一校验调用中消费，不维护第二份手写类/端点枚举。

`validate_runtime_payload` 执行 Schema → RDF Dataset → OWL 词汇检查 → pySHACL → Validation Report。报告字段以 `src/turbine_kg/ontology/semantic.py` 为准；非合规报告含失败阶段、节点与原因。可选上下文缺失只产生未知范围提示，不等于匹配成功；参数种类标签可缺失，标量及单边/双边区间必须有合法单位。

实际消费者是 `stage3/projection.py:build_traceability_projection`：`research_adapter` 只读转换既有研发对象，并在任何节点构造或 Neo4j 事务前消费 `conforms`，失败抛带报告的 `SemanticValidationError`，整批保留不变。旧研发 ID 用可逆 IRI 编码表达，原有记录、审核与投影 ID 不变。来源片段及页码逐项回连；真实已审核引文必须存在于单个绑定片段中，合成 Fixture 原有描述性 Evidence 不被改称为正式原文。

项目阶段状态只见 `data/project_state.json`；运行验收和正式输入指纹只见 `data/stage9/stage9_exit_audit.json`。

Stage 8 另有 `modeling_pattern_coverage` 覆盖审计：它只验证少量真实 Stage 7 candidate 能否代表八种后续建模模式，不扩大最小 OWL，不生成正式词汇。参数、过程、适用性和 alias 探针可以 defer；关键是每种模式都有真实候选、明确处置和来源证据。
