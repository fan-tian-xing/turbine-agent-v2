# 阶段 10：运行追溯、版本与可复现缓存

阶段 10 已完成。唯一退出记录是 `stage10_audit.json`，项目当前状态以 `../project_state.json` 为准。

本阶段将现有 Stage 7 术语分析器接入隔离运行模式：

```text
Stage 7 冻结输入 → Registry/白名单/指纹准入
→ 运行合同与生产者版本引用 → 结构化候选结果缓存
→ 缓存命中重验或 --force 新 Run → 最小 PROV 映射 → 退出审计
```

运行记录字段由 `config/runtime_run.schema.json` 定义，跨模块约束由 `config/runtime_contract.json` 定义。缓存位于 `var/model_runs/stage10`，只保存可重新校验的结构化结果；原始模型响应不落盘，失败或损坏结果不会替换成功结果。运行记录引用输入、配置、生产者文件和输出的路径与 SHA-256，多个 Run 可以共用同一版本引用。

阶段 8 的映射接受、Evidence 审核和知识批准仍分别处理；阶段 10 不生成正式 Statement 台账、Release 包或发布资格。当前本体 namespace 仍是研究版本，正式 Release 前再冻结长期标识。
