# 阶段 5：OCR、版面解析和页面 Golden Sample

阶段 5 只处理当前首批五份资料，不做全库一次性 OCR，不形成正式 Evidence、Release 或阶段 15 的正式首批准入。

当前冻结范围：5 份资料、775 页；人工 Golden Sample 目标为 36 页。`stage5_sample_manifest.json` 记录页面分层、原始资料和处理资产边界。`audit_stage5_inputs.py` 先核对原件、OCR 派生件、页数、页序边界和 Registry SHA-256，再允许进入 OCR/版面/表格基准测试。

审核边界：原始资料完整性和 Golden Sample 页面由主智能体对照原件核对；疑难页面先做区域级隔离，只有数字、单位、否定词或表格关系确实无法判定时，才在聊天窗口提交负责人。项目只使用 RapidOCR；低置信度或复杂页面回到原始页人工复核，不以相似度替代原始页真值。

阶段 5 的全量运行记录应覆盖 775 页，但人工精度真值主要覆盖冻结 Golden Sample 和所有高风险异常页。原始 PDF 保持只读，OCR 派生结果写入 `OCR_DERIVED_ROOT`。

所有命令必须使用项目专用解释器：`D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe`。不要使用 Codex 通用 Python 代替。

当前基线命令（在仓库根目录执行）：

```powershell
$projectPython = "D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe"
$env:PYTHONPATH = "src;scripts"
& $projectPython scripts/audit_stage5_inputs.py
& $projectPython scripts/benchmark_stage5_baseline.py
& $projectPython scripts/benchmark_stage5_rapidocr_sample.py
& $projectPython scripts/audit_stage5_tables.py
& $projectPython scripts/decide_stage5_engines.py
& $projectPython scripts/audit_stage5_exit.py
& $projectPython scripts/audit_stage5_page_identity.py
& $projectPython scripts/audit_project_runtime.py
```

阶段 5 的 36 页 OCR 和质量复核是一次性验收记录，不纳入日常自动复核。`audit_stage5_exit.py` 只读取已经冻结的结果，不会因为日期变化或指纹变化自动触发 OCR。只有负责人明确要求复核时，才执行 `benchmark_stage5_rapidocr_sample.py --force`，再重建真值、质量报告和退出审计。已有结果仍记录输入指纹，供人工决定是否需要复核。

如需重建原始 PDF 真值和定量质量报告，按以下顺序执行：

```powershell
& $projectPython scripts/build_stage5_truth_annotations.py
& $projectPython scripts/benchmark_stage5_quality.py
& $projectPython scripts/audit_stage5_exit.py
```

当前已完成：5 份资料、775 页输入审计和页面基线；RapidOCR 完成冻结 Golden Sample 36 页复跑、0 失败；并完成 7 个版面候选的分类（6 个真实表格页、1 个复杂非表格页）及原始页结构复核。当前只使用 RapidOCR；低置信度、关键数字或复杂版面直接回到 Original materials 原始页人工复核，不用复核结果覆盖原件。相似度只用于发现疑点，不代表字符准确率；无法可靠恢复的表格、公式、图示和阅读顺序只能保留原始页视觉依据，不能直接进入结构化 Evidence。规则线检测只产生候选区域，不代表单元格文字已经准确。

新增的 `stage5_truth_annotations_*.json` 和 `stage5_quality_benchmark_*.json` 使用 `Original materials` 原始 PDF 作为真值来源，记录可评分文本区域的字符、数字、单位、否定词、原生页面几何、阅读顺序和本地耗时指标；扫描页只有存在独立人工转录时才允许文字定量评分，不能把处理链生成的 OCR 文字再当作真值。复杂表格和没有独立文字真值的扫描页，其 bbox/cell/字符结果明确保留为隔离状态。质量报告状态 `complete_with_quarantine` 表示定量复核已完成且所有未确认结构均已隔离，不表示复杂表格 cell-level accuracy 已通过。

页码约定：阶段 5 的 `physical_page` 是从 1 开始的物理 PDF 页码，`pdf_page` 仅作为兼容字段且必须与之相等；资料自身页脚、章节页号或图纸编号另记为 `logical_page_label`。当前已复核：D300N 物理第 94 页对应逻辑标识 `3-3-4`，是接近空白的边界页；辅机书物理第 300 页对应印刷/逻辑页 `291`，有完整题目、公式、图和图注，不能按空白页处理。对应证据见 `stage5_page_identity_audit_2026-09-09.json`。

负责人已确认质量原则：进入 Evidence 的内容必须与原始资料完全一致，尤其是中文字符、数字、小数点、单位、否定词、表格行列/续表关系、公式含义以及图文对应关系。相似度只用于发现疑点，不能作为通过标准；无法准确还原的公式、图示或复杂表格只能保留人工视觉证据，不得直接进入结构化 Evidence。
