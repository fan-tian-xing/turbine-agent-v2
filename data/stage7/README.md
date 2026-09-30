# 阶段 7：术语候选与业务能力问题

本目录已按当前五份 OCR PDF、阶段 5 基线和阶段 6 样本 Evidence 重建。项目总状态以 [`../project_state.json`](../project_state.json) 为准，当前退出依据是 [`stage7_exit_audit.json`](stage7_exit_audit.json)，状态为 `complete`。

[`terminology_input_manifest.json`](terminology_input_manifest.json) 冻结五份资料的 775 个现存物理页及准入身份；其中 731 页可用于文字候选分析，34 页仅供视觉核查，1 页隔离，9 页为非内容页。[`terminology_candidates.json`](terminology_candidates.json) 保存 18,628 条 `candidate_only` 候选；[`stage7_critical_term_golden_set.json`](stage7_critical_term_golden_set.json) 用于检查关键术语召回，`business_capability_questions.json` 记录业务能力问题。候选不等于经原页批准的 Evidence、正式词汇、本体类或 Release，复杂表格数值仍受隔离约束。

重建顺序为 `scripts/build_stage7_input_manifest.py`、`scripts/build_stage7_terminology.py`、`scripts/audit_stage7_exit.py`，均须使用项目专用 Python。阶段 8 已按当前候选指纹重绑审核决定；重建本阶段输入后，必须重新核对阶段 8 及以后阶段的消费者，不能仅替换审计中的旧哈希。
