# 核电汽轮机安调智能体

本仓库用于建设面向核电汽轮机本机及辅机安装、调试、检查、验收和问题处置场景的 CLI 技术分析与建议工具。

## 当前状态

当前仅建立独立工程和资料接入边界，尚未开始 Source Registry、文档解析、OCR、本体、图谱或问答实现。

## 项目边界

- 项目代码、配置和运行资产只放在本仓库。
- 原始资料位于本地项目根目录的 `Original materials`，后续通过 `SOURCE_ROOT` 只读访问。
- 可处理资料由 `config/source_allowlist.tsv` 显式列出。
- 构建链路只读取白名单中的文件，不递归扫描整个父工作区。
- 原始资料、数据库目录、密钥、缓存、模型运行记录和大体积发布产物不进入 Git。
- 磁盘上的 Registry、Evidence、Engineering Statement、审核记录和 Release 产物是可重建的数据权威；Neo4j 仅作为运行投影。

总体建设计划保留在本地项目根目录的 `总计划.md`，不纳入本仓库版本控制。

## 下一步

设计并实现全库 Source Registry，对白名单中的 45 份 PDF 建立身份、完整性、适用性和准入状态记录。

资料处理前可运行以下只读检查，确认白名单路径、文件大小和 SHA-256 均与当前资料一致：

```powershell
python scripts/check_source_allowlist.py
```
