# 阶段 8：最小 OWL 本体与映射审核入口

本目录只记录 Stage 8 的最小本体设计和候选映射审核入口。`config/ontology_contract.json` 是本阶段设计输入，`ontology/minimal_turbine.ttl` 是由脚本生成的 OWL 设计产物；本阶段不建立 SHACL、EngineeringCase、正式运行词汇或 Release。

当前链路：

```text
Stage 7 candidate_only 候选 + 业务能力问题 + Ontology Contract
→ scripts/build_stage8_ontology.py
→ ontology/minimal_turbine.ttl
→ ontology_mapping_shortlist.json
→ ontology_mapping_review_queue.jsonl
→ scripts/audit_stage8_exit.py
→ 人工映射审核
```

Stage 8 短名单是“概念映射审核入口”，不是最终本体。候选资格来自 Stage 7 的完整候选集合和业务能力问题；已接受的 Stage 6 原页 Evidence 优先作为审核证据，但不是进入短名单的硬门槛。只有 Stage 7 页面出现而没有 Stage 6 Evidence 的候选，也可以进入短名单，并必须回原始页面确认。孤立数值、句子/适用范围片段、要求/核验活动词以及动作、缩写、同义/旧称和 OCR 变体不进入本轮短名单。数值只有在后续结构化参数抽取中与参数名、对象、条件和证据绑定后，才可能形成 QuantityValue 数据。

ontology_mapping_review_overlay.jsonl 是独立的 Stage 8 审核记录，按候选指纹绑定用户的接受或暂缓决定，不修改 Stage 7 候选真源。当前已有 4 个候选接受、5 个候选暂缓；“螺栓”已接受为 Component，并保留其与“地脚螺栓”的上位关系，原始页面已确认；普通“振动”已暂缓，不作为 Situation。任何接受决定仍保持 promotion_status=candidate_only，不会自动进入正式本体或运行词汇。stage8_exit_audit.json 由独立审计脚本生成，当前审核队列为空，Stage 8 已满足进入 Stage 9 的条件。
