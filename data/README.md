# 数据边界

可审核、可重建的 Registry、Review Overlay、Golden Sample 和 Release Manifest 按后续阶段的正式合同写入本目录并纳入版本控制。Registry 的重复资产关系和已物化分组分别保存在 `registry/source_duplicate_relations.jsonl` 与 `registry/source_duplicate_groups.jsonl`，二者都使用稳定资产 ID 追溯物理文件。

临时解析数据写入 `data/staging/`；私有评测材料写入 `data/private_evaluation/`。这两类内容不进入 Git。大体积运行产物、缓存、日志和 Release 内容统一写入 `var/`，Neo4j 数据与日志统一写入 `docker-data/`。
