# 阶段 5：OCR、版面解析和页面 Golden Sample

阶段 5 只处理当前首批五份资料，不做全库一次性 OCR，不形成正式 Evidence、Release 或阶段 15 的正式首批准入。

当前冻结范围：5 份资料、775 页；人工 Golden Sample 目标为 36 页。`stage5_sample_manifest.json` 记录页面分层、原始资料和处理资产边界。`audit_stage5_inputs.py` 先核对原件、OCR 派生件、页数、页序边界和 Registry SHA-256，再允许进入 OCR/版面/表格基准测试。

审核边界：原始资料完整性由主智能体查看并核对；疑难页面先由主智能体抽查，只有数字、单位、否定词、表格关系或主备方案结论无法确定时，才在聊天窗口提交负责人；主引擎、备用引擎和最终质量边界需要负责人确认。

阶段 5 的全量运行记录应覆盖 775 页，但人工精度真值主要覆盖冻结 Golden Sample 和所有高风险异常页。原始 PDF 保持只读，OCR 派生结果写入 `OCR_DERIVED_ROOT`。

当前基线命令（在仓库根目录执行）：

- `PYTHONPATH=src python scripts/audit_stage5_inputs.py`
- `PYTHONPATH=src python scripts/benchmark_stage5_baseline.py`
- `PYTHONPATH=src;scripts python scripts/benchmark_stage5_rapidocr_sample.py`
- `PYTHONPATH=src python scripts/audit_stage5_tables.py`
- `PYTHONPATH=src python scripts/audit_stage5_exit.py`
- `PYTHONPATH=src python scripts/audit_stage5_page_identity.py`

当前已完成：5 份资料、775 页输入审计和页面基线；36 页 Golden Sample 的 RapidOCR 复跑；6 页表格/续表结构基线。基线中 3 页的页面记录触发低文本标记，其中 1 页是排除 `review_required` 和 `scan_only` 后的工程候选；两者必须分别报告，不能把候选数当成全量低文本数。当前尚未宣布阶段 5 关闭，因为表格单元格真值、OCR 指标真值以及主/备用引擎和最终质量阈值仍需完成决策。规则线检测只产生候选区域，不代表单元格文字已经准确。

页码约定：阶段 5 的 `pdf_page` 是从 1 开始的物理 PDF 页码；资料自身页脚、章节页号或图纸编号另记为 `logical_page_label`。当前已复核：D300N 物理第 94 页对应逻辑标识 `3-3-4`，是接近空白的边界页；辅机书物理第 300 页对应印刷/逻辑页 `291`，有完整题目、公式、图和图注，不能按空白页处理。对应证据见 `stage5_page_identity_audit_2026-09-09.json`。

负责人已确认质量原则：进入 Evidence 的内容必须与原始资料完全一致，尤其是中文字符、数字、小数点、单位、否定词、表格行列/续表关系、公式含义以及图文对应关系。相似度只用于发现疑点，不能作为通过标准；无法准确还原的公式、图示或复杂表格只能保留人工视觉证据，不得直接进入结构化 Evidence。
