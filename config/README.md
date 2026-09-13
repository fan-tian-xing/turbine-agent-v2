# 配置边界

本目录保存进入版本控制的声明式配置。资料白名单、逻辑文档身份、Revision 和 OCR 派生关系共同组成 Registry 的受控输入：

- `source_allowlist.tsv`：允许处理的资料路径、大小和 SHA-256；
- `document_identity.tsv`：路径到逻辑文档身份；
- `asset_revision_identity.tsv`：路径到逻辑文档与 Revision 的显式分配，支持同一逻辑文档存在多个 Revision；
- `revision_identity.tsv`：受控 Revision 目录；
- `derived_asset_links.tsv`：OCR 派生资产与原件的对应关系。

阶段 10 的 `runtime_run.schema.json` 是可选 extraction batch 的字段真源，`runtime_contract.json` 只约束知识身份、Revision 生命周期、缓存和发布边界。批次不强制保存模型、Prompt、Git、运行时间或 producer SHA；结构化结果缓存位于 `var/model_runs/stage10`，不属于知识真源。

密钥和本机连接参数只写入仓库根目录的 `.env`，不得提交到本目录。正式 Release 前另行冻结长期稳定的本体 namespace；当前研究 namespace 不在阶段 10 修改。
