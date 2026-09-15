# 阶段 11：语义抽取入口门

阶段 11 冻结 Engineering Statement 合同和唯一 Evaluation Sample Registry，样本仍属于研究/评价边界，不产生正式 Release 或运行词汇。开发样本和留出集已完成逐条语义标注、两轮独立复核与最终裁决；其中辅机第 78 页经过用户确认，按原页实际标示的 A、A、C、B、D、A 回填为 6 条 Gold。D300N 第 32 页和辅机第 429 页因版面/表格无法可靠回查而保持隔离。退出审计已通过，阶段 12 可以读取明确的 Gold 开发输入。

- `stage11_statement_development_samples.jsonl`：36 页开发/回归 Golden 的最小 Statement 样本，包含 4 条阶段 3 用户确认记录及五份资料的补充记录。阶段 3 的 15 个试点页是这 36 页的子集；当前文件按语义单元保存 19 条 Statement。
- `stage11_statement_holdout.jsonl` 与 `stage11_holdout_evidence.jsonl`：五份资料各 3 页、共 15 页的 Statement 任务留出；页是抽样单位，Statement 数量按语义拆分为多条。13 页已确认并标为 Gold，D300N 第 32 页和辅机第 429 页隔离；辅机第 78 页保留 6 道完整题目，按原页标示答案回填并去除选择题格式，跨页的 Lc5A1332 不纳入。另有每份资料 1 页固定替补登记在 Registry 中。
- `evaluation_sample_registry.json`：开发、留出、替补和盲测隔离的唯一登记入口。盲测只登记边界，不读取内容。
- `review_round_a.jsonl`、`review_round_b.jsonl`：两个互相不可见的独立语义审核建议，均保留输入/输出哈希和原候选未修改标记；它们不是最终 Gold。
- `stage11_adjudication_queue.jsonl`：逐样本记录两轮审核的拆分/结论冲突及裁决哈希；当前 21 个目标样本均已完成裁决，辅机第 78 页的用户裁决记录了实际选项和跨页排除原因。
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
