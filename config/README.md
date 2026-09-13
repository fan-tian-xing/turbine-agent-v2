# 配置边界

本目录保存进入版本控制的声明式配置。`source_allowlist.tsv` 是资料候选清单；`document_identity.tsv` 是人工维护的路径到逻辑文档身份映射，新增资产或 Revision 必须先更新它，不能让指纹自动改变文档身份；`derived_asset_links.tsv` 声明每一个 OCR 派生资产精确对应的原始资产；`source_profiles/registry.json` 将资产映射到经过审查的资料 Profile，并分别声明语义资料角色与资产类型，不由 Python 根据目录名称推断。未配置 Profile 或派生映射的白名单资料不得进入 Registry 构建。OCR 派生目录、解析、抽取、词汇和校验配置只在对应处理链实际启用时建立，不创建无消费者的空配置。

密钥和本机连接参数只写入仓库根目录的 `.env`，不得写入本目录。
# 配置边界

本目录保存进入版本控制的声明式配置。资料白名单、逻辑文档身份、Revision 和 OCR 派生关系共同组成 Registry 的受控输入：

- `source_allowlist.tsv`：允许处理的资料路径、大小和 SHA-256；
- `document_identity.tsv`：路径到逻辑文档身份；
- `asset_revision_identity.tsv`：路径到逻辑文档与 Revision 的显式分配，支持同一逻辑文档存在多个 Revision；
- `revision_identity.tsv`：受控 Revision 目录；
- `derived_asset_links.tsv`：OCR 派生资产与原件的对应关系。

阶段 10 的 `runtime_run.schema.json` 是运行记录的字段真源，`runtime_contract.json` 只约束跨模块关系、版本引用、缓存和发布边界。运行记录引用生产者文件、配置和输出的路径与 SHA-256，不为每次运行复制源码或原始资料；结构化结果缓存位于 `var/model_runs/stage10`，不属于知识真源。

密钥和本机连接参数只写入仓库根目录的 `.env`，不得提交到本目录。正式 Release 前另行冻结长期稳定的本体 namespace；当前研究 namespace 不在阶段 10 修改。
