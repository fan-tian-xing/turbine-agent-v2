---
name: requirements
description: 从已入库的技术资料查询安装调试依据，并根据问题给出有出处的建议。
metadata:
  id: requirements
  version: "6"
  required_items: [requirement, parameter, procedure, step, anomaly, observation, inspection, cause, measure, verification, fact, limitation]
  relation_ids: [has_requirement, has_procedure, has_anomaly, has_inspection, has_cause, has_measure, has_verification]
---

# 基础资料问答

1. 从问题中识别实际涉及的对象、活动、知识项和已知条件，只建立本题需要的任务。
2. 按本体类和已审定关系检索 Statement，同时用问题原词检索 Evidence；命中后读取完整 Evidence 及出处。
3. 只使用 Neo4j 返回的 Statement 和 Evidence 作为工程依据，不回读原始 PDF 或本地图片；用户现场数值只用于本次判断，不写入知识图谱。
4. 先给出有来源支持的建议，再列依据来源。资料只规定限值或结果时，不补造来源没有给出的处置步骤；资料不足时说明缺口。
