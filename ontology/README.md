# 语义权威包

Stage 8 已建立最小 Ontology Contract 和可重建的 minimal_turbine.ttl。合同是本阶段的设计输入，OWL 文件由 scripts/build_stage8_ontology.py 生成；当前只覆盖五个稳定顶层类、八个首版运行类及最小关系，不包含 SHACL、案例、正式运行词汇或 Release。能力路径明确保留“量值—参数种类标签”和“情境—涉及对象”两条最小语义连接。

完整 OWL/SHACL 语义权威包将在 Stage 9 建立。Stage 8 已完成本体设计和候选映射审核；“螺栓”已接受为 Component 并保留与“地脚螺栓”的上下位关系，原始页面已确认；普通“振动”已暂缓，不作为 Situation。接受记录仍保持 candidate_only，任何候选都不会自动晋级为正式运行词汇或 Release。

Stage 8 另有 `modeling_pattern_coverage` 覆盖审计：它只验证少量真实 Stage 7 candidate 能否代表八种后续建模模式，不扩大最小 OWL，不生成正式词汇。参数、过程、适用性和 alias 探针可以 defer；关键是每种模式都有真实候选、明确处置和来源证据。
