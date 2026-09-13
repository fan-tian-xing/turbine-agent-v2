# 阶段 10：稳定身份、文档版本与知识可维护性

阶段 10 已完成。唯一退出记录是 `stage10_audit.json`，项目当前状态以 `../project_state.json` 为准。

本阶段将现有 Stage 7 术语分析器接入隔离运行模式：

```text
Stage 7 冻结输入 → Registry/白名单/Revision 指纹准入
→ 结构化候选结果缓存 → 缓存命中重验或 --force 新 extraction batch
→ Revision 影响范围检查 → 退出审计
```

可选 extraction batch 字段由 `config/runtime_run.schema.json` 定义，跨模块约束由 `config/runtime_contract.json` 定义。缓存位于 `var/model_runs/stage10`，只保存可重新校验的结构化结果；原始模型响应不落盘，失败或损坏结果不会替换成功结果。缓存 key 和实现指纹不进入 Evidence、Statement 或 Release 的正式语义。

正式知识沿 `Document → Revision → Page/SourceSpan → Evidence → Engineering Statement → Graph` 追溯。Revision 替换时只定位受影响的 Evidence、Statement 和派生关系，保留共享 Entity；完整差异、Release 和图投影流程留到后续阶段。

阶段 8 的映射接受、Evidence 审核和知识批准仍分别处理；阶段 10 不生成正式 Statement 台账、Release 包或发布资格。当前本体 namespace 仍是研究版本，正式 Release 前再冻结长期标识。
