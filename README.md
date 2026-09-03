# 核电汽轮机安调智能体

本仓库用于建设面向核电汽轮机本机及辅机安装、调试、检查、验收和问题处置场景的 CLI 技术分析与建议工具。

## 当前状态

独立工程、配置边界和本地运行基础已经建立，尚未开始 Source Registry、文档解析、OCR、本体、图谱或问答实现。

## 项目边界

- 项目代码、配置和运行资产只放在本仓库。
- 原始资料位于本地项目根目录的 `Original materials`，后续通过 `SOURCE_ROOT` 只读访问。
- 可处理资料由 `config/source_allowlist.tsv` 显式列出。
- 构建链路只读取白名单中的文件，不递归扫描整个父工作区。
- 原始资料、数据库目录、密钥、缓存、模型运行记录和大体积发布产物不进入 Git。
- 磁盘上的 Registry、Evidence、Engineering Statement、审核记录和 Release 产物是可重建的数据权威；Neo4j 仅作为运行投影。

总体建设计划见 `总计划.md`，该文件只记录各阶段目标、任务、交付物和验收要求。

## 本地配置

复制 `.env.example` 为 `.env` 后填写本机参数。程序启动时从项目根目录 `.env` 读取配置，已有环境变量优先；模块中不写死本机绝对路径。

Neo4j 使用 `compose.yaml` 描述，但容器由项目负责人手动创建和启动。数据库只是磁盘权威产物的运行投影，正式知识不得直接在 Neo4j Browser 中修改。

- Neo4j Browser：`http://localhost:7475`
- Neo4j Bolt：`neo4j://localhost:7688`

## 当前检查

资料处理前可运行以下只读检查，确认白名单路径、文件大小和 SHA-256 均与当前资料一致：

```cmd
python scripts/check_source_allowlist.py
```

工程测试：

```cmd
python -m pytest
```
