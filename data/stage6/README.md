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

Golden Sample 的处置范围如下，页面和 Evidence 数量以退出审计及其关联质量报告为准：

- 文字/区域正向页面通过原页视觉复核后形成局部 Evidence；
- 复杂表格页面通过原页区域、表头层级、叶子列、续表关系和同页分区复核后形成 `region_scoped` 表格 Evidence；
- 表格数据单元格没有被结构化放行，未逐格确认的数值仍不可用于结构化 Claim；
- 元数据、导航和空白/边界页作为负向门禁样本，不生成正向 Evidence；
- 辅机书原始 PDF 的 90° 页面旋转已使用显式 authority rotation 映射，Evidence bbox 仍采用显示页坐标，并已重新叠框核对原页。

复核决定保存在 `stage6_page_review_decisions.jsonl`，构建脚本只读取该文件，不覆盖人工或视觉复核决定。`stage6_evidence_quality_audit.json` 对原始资产权威、页码、bbox、文本哈希、处置等级、审核绑定和隔离保留进行数量及零容忍对账。

`stage6_evidence_bundle.jsonl` 是阶段 6 唯一权威样本 Evidence 数据源，由文字/区域 Evidence 和表格区域 Evidence 对账合并生成。两个 component annotations 只作为其可重建输入。早期 `vertical_slice` 与 `candidate` 启动诊断产物已清理，不得作为下游输入。

`stage6_evidence_review_decisions.jsonl` 保存旧轮次三条用户原页接受记录，其中两条旧 Evidence ID 已随现行重建失效；它不是当前构建或退出门禁的输入。该文件暂保留为不可由新审核记录替代的用户裁决来源，当前逐页决定以 `stage6_page_review_decisions.jsonl` 为准。

`stage6_exit_audit.json` 的 `complete` 记录了原 36 页样本质量、表格区域及逐页语义覆盖门禁的当时判断，并不表示五份资料的全文 Evidence 已完成。独立原页核查随后发现 DL5190.3 物理第 25、113 页图中文字未进入 Evidence；本轮按用户要求暂缓修复，因此原审计的“确认遗漏 0”不能再视为事实上的无遗漏结论。原审计将 28 页列为有意义内容页、8 页列为负向门禁页。HAF103 第 29 页的表格外定义及跨第 30 页的续句已有原页绑定；D300N 第 50 页的表项 6 续段与第 49 页绑定，表项 7–9 和右侧记录目录列保持独立。表格数据单元格仍未获准作为独立数值事实。

此审计只覆盖已持有的原始 PDF 和 Golden Sample 页，不表示五份资料的印刷全书齐全。DLT863 已登记缺失印刷页 12、14–23、25、27–29，相关跨页残项不能拼成完整 Statement。

来源结构索引只登记原页确有共同边界的组。`operation_groups` 是操作步骤，`context_groups` 可登记分类列表或单题题干与选项。确认操作组可由任意数量的步骤组成；每步可绑定多条 Evidence 及各自原文摘录。题库组另外绑定原页已标答案的选项字母、答案文本及选项原文摘录；无印刷答案不能补答案。相邻 Evidence 不自动归入同一组，未核跨页步骤保持 `unresolved`。

已核 OCR 更正、侧栏去除、受控表格文本区域及抽取许可写在 `stage6_source_review_overrides.json`，以源 PDF hash 和原 OCR 框位置/原文绑定。图形区域见 `stage6_visual_regions.json`，仅以 `visual_only` 保留位置和图注；`stage6_reference_token_exclusions.json` 逐页记录标准页眉或图内标记为何不进入文字 Evidence。canonical 行顶层 `stage12_extractability=context_only` 的 76 条仅提供来源上下文，不得当作独立工程事实；辅机第 360 页 La1F2010 两段完整答文已原页复核并标为 `approved_answer/extractable`，题目仍为 `context_only`。`pending_review` 为 0。当前共有 391 条文字／图形 Evidence 和 7 条表格区域 Evidence，质量、语义覆盖和退出审计均为 `complete`。

准备把表格数据晋级为单元格级 Evidence 时，若原页表头继承或单元格归属仍存在歧义，必须提交负责人复核；未解决歧义继续隔离。

阶段 6 未生成 Engineering Statement、OWL/SHACL、Neo4j 正式投影或 Release。阶段 3 试验 Evidence 也没有被直接晋级或作为本阶段构建输入。

阶段 7 的 `../stage7/terminology_input_manifest.json` 已重新绑定当前 canonical Evidence，候选和退出审计已按当前输入重建；阶段 7 仅形成候选，不把暂缓的图中文字缺口转成工程事实。五份资料的全文 Evidence 仍须到阶段 15 完成。
