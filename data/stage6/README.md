# 阶段 6：Evidence 层与 Golden Sample

本目录记录阶段 6 的 Evidence 产物和退出门禁；当前项目状态及下一阶段状态唯一以 `../project_state.json` 为准。Evidence 的唯一权威来源是 `Original materials` 原始资产；OCR、RapidOCR 文本和处理 PDF 只用于文字适配、坐标辅助和原页对照，不是证据来源。

当前闭环：

```text
Registry + Original materials
→ Document IR
→ 物理页/逻辑页和原始/处理资产对齐
→ 局部 SourceSpan 与 Evidence
→ 独立页面复核决定
→ Golden Sample 质量对账
```

36 页 Golden Sample 的处理结果：

- 22 个文字/区域正向页面全部完成原页视觉复核，形成 280 条已接受的局部 Evidence；
- 6 个复杂表格页面完成原页区域、表头层级、叶子列、续表关系和同页分区复核，形成 7 条 `region_scoped` 表格 Evidence；
- 表格数据单元格没有被结构化放行，未逐格确认的数值仍不可用于结构化 Claim；
- 5 个元数据页、1 个导航页和 2 个空白/边界页作为负向门禁样本，不生成正向 Evidence；
- 辅机书原始 PDF 的 90° 页面旋转已使用显式 authority rotation 映射，Evidence bbox 仍采用显示页坐标，并已重新叠框核对原页。

复核决定保存在 `stage6_page_review_decisions.jsonl`，构建脚本只读取该文件，不覆盖人工或视觉复核决定。`stage6_evidence_quality_audit.json` 对原始资产权威、页码、bbox、文本哈希、处置等级、审核绑定和隔离保留进行数量及零容忍对账。

`stage6_evidence_bundle.jsonl` 是阶段 6 唯一权威样本 Evidence 数据源，由 280 条文字/区域 Evidence 和 7 条表格区域 Evidence 对账合并生成。两个 component annotations 只作为其可重建输入。带 `vertical_slice` 或 `candidate` 名称的早期三页产物只保留为非权威启动诊断记录，不得被下游阶段消费。

当前不需要用户立即审核。只有未来准备把表格数据晋级为单元格级 Evidence，且原页表头继承或单元格归属仍存在歧义时，才触发用户复核。

阶段 6 未生成 Engineering Statement、OWL/SHACL、Neo4j 正式投影或 Release。阶段 3 试验 Evidence 也没有被直接晋级或作为本阶段构建输入。

阶段 7 已消费冻结的 `../stage7/terminology_input_manifest.json`，该清单覆盖 775 个物理页；阶段 7 仅消费其中 `text_accepted` 内容，全文 Evidence 仍须到阶段 15 构建。
