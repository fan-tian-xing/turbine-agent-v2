# 数据边界

可审核、可重建的 Registry、Review Overlay、Golden Sample 和 Release Manifest 按后续阶段的正式合同写入本目录并纳入版本控制。`registry/stage2_source_selection.json` 只记录通过 Stage 2B、可进入 Stage 3 研发试点的候选来源；`registry/stage2_exit_audit.json` 记录 Stage 2 的退出边界；正式首批处理仍须在 Stage 15 通过 `First Batch Admission Gate`。Registry 的重复资产关系和已物化分组分别保存在 `registry/source_duplicate_relations.jsonl` 与 `registry/source_duplicate_groups.jsonl`，二者都使用稳定资产 ID 追溯物理文件。

项目当前状态唯一以 `project_state.json` 为准；本目录中的阶段退出审计只证明对应阶段的边界和门禁，不重复维护项目总状态。阶段 6 的唯一权威样本 Evidence 数据源是 `stage6/stage6_evidence_bundle.jsonl`；其范围为 `golden_sample_only`，不能当作 775 页全文 Evidence。

阶段 7 的术语输入清单覆盖首批五个供给单元的 775 个物理页，只消费其中明确通过阶段 7 输入门的 `text_accepted` 页面；`stage7/terminology_candidates.json` 和业务能力问题均为 `candidate_only`，不得当作本体、正式词汇或 Release。OCR 可参与候选发现，但原始材料仍是权威来源；复杂表格和区域级 Evidence 页面继续隔离。阶段 8 只对实际拟映射进本体的候选短名单执行人工审核。

临时解析数据写入 `data/staging/`；私有评测材料写入 `data/private_evaluation/`。这两类内容不进入 Git。大体积运行产物、缓存、日志和 OCR 派生 PDF 统一写入 `var/`：当前 OCR PDF 位于 `var/derived/ocr`，由 `OCR_DERIVED_ROOT` 指向，不能落入只读 `SOURCE_ROOT`；`var/stage3` 保存已确认研发试点的运行输入。Neo4j 数据与日志统一写入 `docker-data/`。
