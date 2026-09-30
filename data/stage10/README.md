# 阶段 10：稳定身份、文档版本与知识可维护性

`stage10_audit.json` 已按当前阶段 9 退出审计、阶段 7 候选及 Registry 重建，退出检查与阶段 11 之前的项目回归通过。`var/model_runs/stage10` 仅保留绑定现行阶段 6/7 输入的一组可重验结构化缓存；缓存是隔离运行产物，不是正式知识真源。

唯一退出记录是 `stage10_audit.json`，项目当前状态以 `../project_state.json` 为准。

本阶段将现有 Stage 7 术语分析器接入隔离运行模式：

```text
Stage 7 冻结输入 → Registry/白名单/Revision 指纹准入
→ 结构化候选结果缓存 → 缓存命中重验或 --force 新 extraction batch
→ Revision 影响范围检查 → 退出审计
```

可选 extraction batch 字段由 `config/runtime_run.schema.json` 定义，跨模块约束由 `config/runtime_contract.json` 定义。缓存位于 `var/model_runs/stage10`，只保存可重新校验的结构化结果；原始模型响应不落盘，失败或损坏结果不会替换成功结果。缓存 key 和实现指纹不进入 Evidence、Statement 或 Release 的正式语义。

缓存输入包含已接受页实际使用的处理与原始资产 Registry 指纹；无关资料变化不会使缓存失效。每次访问都核对当前准入、文本适配、清单路径及白名单，缓存批次和输出包必须绑定本次输入。

`stage10_audit.json` 中 `inputs["data/registry/source_assets.jsonl"]` 的 SHA-256 是该文件的字节哈希；`registry_dependency.sha256` 是已接受页所依赖资产的范围指纹，用于缓存失效判断，两者含义不同。

正式知识沿 `Document → Revision → Page/SourceSpan → Evidence → Engineering Statement → Graph` 追溯。生命周期 helper 明确要求 `knowledge_status`，不从 `status` 或 `publication_stage` 推断。Revision 影响辅助函数只定位当前 `active` 的 Evidence、Statement，`superseded`/`invalid` 保留历史，共享 Entity 不删除；完整差异、关系重建、Release 和图投影流程留到后续阶段。

阶段 8 的映射接受、Evidence 审核和知识批准仍分别处理；阶段 10 不生成正式 Statement 台账、Release 包或发布资格。当前本体 namespace 仍是研究版本，正式 Release 前再冻结长期标识。
