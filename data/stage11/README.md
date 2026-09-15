# 阶段 11：语义抽取入口门

阶段 11 冻结 Engineering Statement 合同和唯一 Evaluation Sample Registry，样本仍属于研究/评价边界，不产生正式 Release 或运行词汇。当前页面与 Evidence 已冻结为候选，语义 Golden 尚未完成，退出审计保持 `in_progress`，因此阶段 12 暂不开放。

- `stage11_statement_development_samples.jsonl`：36 页开发/回归 Golden 的最小 Statement 样本，包含 4 条阶段 3 用户确认记录及五份资料的补充记录。阶段 3 的 15 个试点页是这 36 页的子集。
- `stage11_statement_holdout.jsonl` 与 `stage11_holdout_evidence.jsonl`：五份资料各 3 页、共 15 页的 Statement 任务留出候选；另有每份资料 1 页固定替补登记在 Registry 中。留出页与开发页不重叠，术语/本体暴露按任务分别记录。候选默认 `pending_manual_review`，复杂阅读顺序页进入 `isolated`，不得直接作为 Gold。
- `evaluation_sample_registry.json`：开发、留出、替补和盲测隔离的唯一登记入口。盲测只登记边界，不读取内容。
- `review_round_a.jsonl`、`review_round_b.jsonl`：两个互相不可见的独立语义审核建议，均保留输入/输出哈希和原候选未修改标记；它们不是最终 Gold。
- `stage11_adjudication_queue.jsonl`：逐样本记录两轮审核的拆分/结论冲突。队列全部裁决并回写最终标注前，Stage 11 不得退出。
- `stage11_entry_audit.json`：Stage 11 正式退出记录；只有该记录通过，Stage 12 才可读取开发样本并由独立评价入口读取留出集。

构建与检查：

```powershell
$projectPython = "D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe"
& $projectPython scripts/build_stage11_development_samples.py
& $projectPython scripts/build_stage11_holdout.py
& $projectPython scripts/build_stage11_adjudication_queue.py
& $projectPython scripts/audit_stage11_exit.py
& $projectPython -m pytest -q -p no:cacheprovider tests/stage11
```

AI 交叉审核记录只能表示待执行的审核轮次，不能由 builder 直接写成 accepted；最终需要独立复核记录和真实语义标注。阶段 15 将按五份资料 775 个唯一物理页统一迁移并全文处理，样本页只处理一次，OCR 派生件不重复计为资料。
