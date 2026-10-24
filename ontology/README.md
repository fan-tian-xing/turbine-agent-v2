# 本体与存储合同

`config/ontology_registry.json` 是机器定义入口。

- `classes`：物理对象、活动及设备／部件／系统／工具等基础业务类。
- `domain_classes`：可跨资料复用的具体种类，不含书籍章节限定。
- `record_types`：来源提及、类存储代理、知识记录、来源证据、资料记录；这些不是业务分类树的分支。
- `relations`：允许的方向、端点类型、知识类型和角色。
- `storage_policy`：提及只内嵌保存；条件、量值和公式保留在完整断言文字中，不另拆 Statement 字段。

`core.ttl` 与 `shapes.ttl` 由 `OntologyRegistry.authority()` 生成。类层次一直连接到 `owl:Thing`；存储代理的结构约束独立声明。`ontology_design.md` 介绍当前 v11 的类层次、Mention 处理边界、合同版本与初始验收问题。当前为6个基础业务类、54个领域类；基础等暂不建类的名称保留为内嵌 Mention。

模型获得可抽取类型、业务类定义和关系合同。程序校验抽取格式与来源绑定；回答依据来自 Evidence。本体调整后通过 `--remap` 更新现有 Statement 映射和图谱投影。
