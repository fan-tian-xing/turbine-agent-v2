# 配置

本目录保存声明式运行合同，无密钥、批次缓存或阶段审计。

- `agent.json`：问答预算、模型重试、文档解析参数。
- `skills.json`：当前为空；形成经验证的案例流程后才登记 Skill。
- `ontology_registry.json`：唯一机器注册表，驱动抽取 schema、端点/角色验证、导航及 OWL/SHACL。
- `source_annotations.json`：绑定原始 SHA-256 的已核验文字、区域、表格、跨页及隔离规则。
- `processing_scopes.json`：默认处理的完整章节范围；页范围参数只用于测试或定向修复。范围放在配置，核心代码不按书名或页码分支。
- `source_allowlist.tsv`：允许处理的来源路径、大小、SHA-256。
- `document_identity.tsv`：稳定文档身份。
- `derived_asset_links.tsv`：现有 OCR 派生件与原件精确路径对应。
- `acceptance_questions.json`：当前资料的验收问题、来源位置及后续多资料通用性案例。

原始资料为事实来源；Neo4j 保存当前资料、Evidence、Statement、索引和处理状态。资料变化时按稳定标识更新受影响节点及关系，并删除失效记录，不使用候选代切换。本体注册表约束抽取类型、关系和查询路径；来源绑定保留原文位置。

`scripts/check_source_allowlist.py` 使用 `documents/source_inputs.py` 的通用维护校验，只读取明确声明的来源与 OCR 文件，核对路径、数量、大小和 SHA-256；不生成注册快照或审计文件。

本机密钥与服务参数只在根目录 `.env`；环境变量优先。不得输出密钥。
