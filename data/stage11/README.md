# 阶段 11：语义抽取入口门

阶段 11 的 Engineering Statement 合同和唯一 Evaluation Sample Registry 保持研究/评价边界，不产生正式 Release 或运行词汇。当前退出审计按阶段 6–10 现行输入重验为 `complete`：17 条旧 Evidence 来源均已迁移，开发 Gold 现为 46 条。D300N 物理第 73 页 4 条和 HAF103 物理第 1 页 1 条按本轮用户裁决改为 `descriptive`，保留原有类型、条件和适用范围。辅机物理第 360 页 La1F2010 低真空答文拆为 6 条、高真空答文拆为 5 条，均绑定现行可提取 Evidence；题目仍只作上下文。此处是教材答文的描述性知识，不把约 60 kPa 写成普遍强制标准。旧两轮审核和用户裁决哈希保留为历史记录，现行拆分另有三方审核和当前 Gold 哈希。辅机第 78 页的 6 条用户裁决与 D300N 第 32 页、辅机第 429 页的隔离决定继续保留。

- `stage11_statement_development_samples.jsonl`：36 页开发/回归 Golden 的 Statement 样本，包含阶段 3 用户确认及五份资料的补充记录。阶段 3 的 15 个试点页是这 36 页的子集；当前文件按语义单元保存 46 条 Statement。
- `stage11_statement_holdout.jsonl` 与 `stage11_holdout_evidence.jsonl`：五份资料各 3 页、共 15 页的 Statement 任务留出；页是抽样单位，Statement 数量按语义拆分为多条。13 页已确认并标为 Gold，D300N 第 32 页和辅机第 429 页隔离；辅机第 78 页保留 6 道完整题目，按原页标示答案回填并去除选择题格式，跨页的 Lc5A1332 不纳入。另有每份资料 1 页固定替补登记在 Registry 中。
- `evaluation_sample_registry.json`：开发、留出、替补和盲测隔离的唯一登记入口。盲测只登记边界，不读取内容。
- `review_round_a.jsonl`、`review_round_b.jsonl`：两个互相不可见的独立语义审核建议，均保留输入/输出哈希和原候选未修改标记；它们不是最终 Gold。
- `stage11_adjudication_queue.jsonl`：逐样本记录两轮审核的拆分/结论冲突及裁决哈希；当前 21 个目标样本均已完成裁决，辅机第 78 页的用户裁决记录了实际选项和跨页排除原因。
- `stage11_source_rebinding_review.json`：按现行 Evidence、原件页及旧 Gold 审核原有 17 条失效来源，记录已写回的来源、角色、文本哈希和 Statement 哈希。
- `stage11_current_gold_review.json`：保存 6 个受迁移影响样本的历史裁决哈希与当前 Gold 哈希，并记录辅机第 360 页两段答文的原页范围及三方语义复核；旧用户裁决不被改写成新裁决。
- `stage11_entry_audit.json` 与 `stage11_exit_audit.json`：当前入口与正式退出记录；两者均须通过，Stage 12 才可读取开发样本并由独立评价入口读取留出集。

构建与检查：

```powershell
$projectPython = "D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe"
& $projectPython scripts/audit_stage11_exit.py
& $projectPython -m pytest -q -p no:cacheprovider tests/stage11
```

当前 46 条 Gold 含人工裁决及新的三方语义复核，不能直接重跑候选构建脚本覆盖。阶段 12 正按现行输入独立重建；失效的旧 Candidate、Evaluation、分歧汇总和真实 Robustness 结果已删除，阶段 11 的通过不替代阶段 12 重验。

AI 交叉审核记录只能表示待执行的审核轮次，不能由 builder 直接写成 accepted；最终需要独立复核记录和真实语义标注。阶段 15 将按五份资料 775 个唯一物理页统一迁移并全文处理，样本页只处理一次，OCR 派生件不重复计为资料。
