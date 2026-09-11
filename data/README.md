# 数据边界

可审核、可重建的 Registry、Review Overlay、Golden Sample 和 Release Manifest 按后续阶段的正式合同写入本目录并纳入版本控制。`registry/stage2_source_selection.json` 只记录通过 Stage 2B、可进入 Stage 3 研发试点的候选来源；`registry/stage2_exit_audit.json` 记录 Stage 2 的退出边界；正式首批处理仍须在 Stage 15 通过 `First Batch Admission Gate`。Registry 的重复资产关系和已物化分组分别保存在 `registry/source_duplicate_relations.jsonl` 与 `registry/source_duplicate_groups.jsonl`，二者都使用稳定资产 ID 追溯物理文件。

阶段 6 的唯一正式样本 Evidence 数据源是 `stage6/stage6_evidence_bundle.jsonl`；其范围为 `golden_sample_only`，不能当作 775 页全文 Evidence。阶段 0～6 的当前完成状态和保留边界记录在 `stage6/stage0_to_stage6_completion_review_2026-09-11.json`。

临时解析数据写入 `data/staging/`；私有评测材料写入 `data/private_evaluation/`。这两类内容不进入 Git。大体积运行产物、缓存、日志和 OCR 派生 PDF 统一写入 `var/`，其中 OCR 由 `OCR_DERIVED_ROOT` 指向，不能落入只读 `SOURCE_ROOT`；Neo4j 数据与日志统一写入 `docker-data/`。
