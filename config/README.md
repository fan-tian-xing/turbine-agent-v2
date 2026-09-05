# 配置边界

本目录保存进入版本控制的声明式配置。`source_allowlist.tsv` 是资料候选清单；`document_identity.tsv` 是人工维护的路径到逻辑文档身份映射，新增资产或 Revision 必须先更新它，不能让指纹自动改变文档身份；`derived_asset_links.tsv` 声明每一个 OCR 派生资产精确对应的原始资产；`source_profiles/registry.json` 将资产映射到经过审查的资料 Profile，并分别声明语义资料角色与资产类型，不由 Python 根据目录名称推断。未配置 Profile 或派生映射的白名单资料不得进入 Registry 构建。OCR 派生目录、解析、抽取、词汇和校验配置只在对应处理链实际启用时建立，不创建无消费者的空配置。

密钥和本机连接参数只写入仓库根目录的 `.env`，不得写入本目录。
