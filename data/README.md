# 数据边界

可审核、可重建的 Registry、Review Overlay、Golden Sample 和 Release Manifest 按后续阶段的正式合同写入本目录并纳入版本控制。`registry/stage2_source_selection.json` 只记录通过 Stage 2B、可进入 Stage 3 研发试点的候选来源；正式首批处理仍须在 Stage 15 通过 `First Batch Admission Gate`。Registry 的重复资产关系和已物化分组分别保存在 `registry/source_duplicate_relations.jsonl` 与 `registry/source_duplicate_groups.jsonl`，二者都使用稳定资产 ID 追溯物理文件。

临时解析数据写入 `data/staging/`；私有评测材料写入 `data/private_evaluation/`。这两类内容不进入 Git。大体积运行产物、缓存、日志和 OCR 派生 PDF 统一写入 `var/`，其中 OCR 由 `OCR_DERIVED_ROOT` 指向，不能落入只读 `SOURCE_ROOT`；Neo4j 数据与日志统一写入 `docker-data/`。
