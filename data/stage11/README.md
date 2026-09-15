# 阶段 11：语义抽取入口门

阶段 11 冻结 Engineering Statement 合同和唯一 Evaluation Sample Registry，样本仍属于研究/评价边界，不产生正式 Release 或运行词汇。

- `stage11_statement_development_samples.jsonl`：36 页开发/回归 Golden 的最小 Statement 样本，包含 4 条阶段 3 用户确认记录及五份资料的补充记录。阶段 3 的 15 个试点页是这 36 页的子集。
- `stage11_statement_holdout.jsonl` 与 `stage11_holdout_evidence.jsonl`：五份资料各 3 页、共 15 页的 Statement 任务留出集；另有每份资料 1 页固定替补登记在 Registry 中。留出页与开发页不重叠，术语/本体暴露按任务分别记录。
- `evaluation_sample_registry.json`：开发、留出、替补和盲测隔离的唯一登记入口。盲测只登记边界，不读取内容。
- `stage11_entry_audit.json`：Stage 11 正式退出记录；只有该记录通过，Stage 12 才可读取开发样本并由独立评价入口读取留出集。

构建与检查：

```powershell
$projectPython = "D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe"
& $projectPython scripts/build_stage11_development_samples.py
& $projectPython scripts/build_stage11_holdout.py
& $projectPython scripts/audit_stage11_exit.py
& $projectPython -m pytest -q -p no:cacheprovider tests/stage11
```

AI 交叉审核只标记为 `ai_cross_review`；源页、页码、Evidence 和哈希必须可回查。阶段 15 将按五份资料 775 个唯一物理页统一迁移并全文处理，样本页只处理一次，OCR 派生件不重复计为资料。
