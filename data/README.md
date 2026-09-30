# 数据边界

可审核、可重建的 Registry、Review Overlay、Golden Sample 和 Release Manifest 按后续阶段的正式合同写入本目录并纳入版本控制。`registry/stage2_source_selection.json` 只记录通过 Stage 2B、可进入 Stage 3 研发试点的候选来源；`registry/stage2_exit_audit.json` 记录 Stage 2 的退出边界；正式首批处理仍须在 Stage 15 通过 `First Batch Admission Gate`。Registry 的重复资产关系和已物化分组分别保存在 `registry/source_duplicate_relations.jsonl` 与 `registry/source_duplicate_groups.jsonl`，二者都使用稳定资产 ID 追溯物理文件。

项目当前状态唯一以 `project_state.json` 为准；本目录中的阶段退出审计只证明对应阶段的边界和门禁，不重复维护项目总状态。阶段 6 的唯一权威样本 Evidence 数据源是 `stage6/stage6_evidence_bundle.jsonl`；其范围为 `golden_sample_only`，不能当作全文 Evidence。

当前结构审计使用 [`stage4/stage4_full_parse_audit.json`](stage4/stage4_full_parse_audit.json)，当前全文 OCR 门禁使用 [`stage5/stage5_exit_audit.json`](stage5/stage5_exit_audit.json)；阶段 5 的稳定基线、表格事实和独立转录入口见 [`stage5/README.md`](stage5/README.md)。阶段 0–5 已复核；阶段 6 样本退出审计通过，但独立原页核查发现图中文字遗漏，本轮依用户要求暂缓；阶段 7–11 已按当前输入重建并通过，阶段 12 Development 正按中文提示词重建，独立 Reserve 尚未运行最终验收。不得通过替换旧产物的输入哈希宣称重验通过。

阶段 7 的 `stage7/terminology_input_manifest.json`、`stage7/terminology_candidates.json` 和业务能力问题已按当前输入重新生成；产物说明见 [`stage7/README.md`](stage7/README.md)。候选仍为 `candidate_only`，不得当作本体、正式词汇或 Release。辅机 OCR 的候选分析准入依据阶段 5 对当前 PDF 的用户验收，不宣称助手逐行逐格复核。原始材料仍是权威来源；复杂表格的数据单元格继续隔离。阶段 8 的三方独立审核已绑定当前候选指纹与所查原件页；旧人工裁决保留为 Overlay 内嵌历史记录，不冒充本次用户确认。

阶段 8 最终映射中的唯一审核事实是 `mapping_review_decision`；`ontology_mapping_review_queue.jsonl` 的 `review_status` 只表示待办任务状态。阶段 9 的 OWL/SHACL 语义权威和阶段 10 的知识生命周期合同、结构化缓存及退出审计分别位于 `ontology/`、`config/runtime_*` 和 `stage10/stage10_audit.json`。Stage 10 缓存属于本地运行产物，不是知识真源，也不覆盖冻结的 Stage 7 候选。

阶段 11 的 46 条开发 Gold 和来源迁移见 [`stage11/README.md`](stage11/README.md)；旧双轮审核与用户裁决哈希保留为历史，当前 Gold 的复核与哈希另行记录。阶段 12 旧产物不因阶段 11 通过而自动有效。

临时解析数据写入 `data/staging/`；私有评测材料写入 `data/private_evaluation/`。这两类内容不进入 Git。大体积运行产物、缓存、日志和 OCR 派生 PDF 统一写入 `var/`：当前 OCR PDF 位于 `var/derived/ocr`，由 `OCR_DERIVED_ROOT` 指向，不能落入只读 `SOURCE_ROOT`；`var/stage3` 保存已确认研发试点的运行输入。Neo4j 数据与日志统一写入 `docker-data/`。
