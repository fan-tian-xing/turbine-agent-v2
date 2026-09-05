# 配置边界

本目录保存进入版本控制的声明式配置。`source_allowlist.tsv` 是资料候选清单；`document_identity.tsv` 是人工维护的路径到逻辑文档身份映射，新增资产或 Revision 必须先更新它，不能让指纹自动改变文档身份；`source_profiles/registry.json` 将物理资产映射到经过审查的资料 Profile，资料角色和默认文本策略由 Profile 声明，不由 Python 根据目录名称推断。未配置 Profile 的白名单资料不得进入 Registry 构建。后续阶段按实际合同新增 parsing、extraction、vocabulary 和 validation 配置，不创建空模板。

密钥和本机连接参数只写入仓库根目录的 `.env`，不得写入本目录。
