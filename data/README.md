# 人工来源审核与独立参考样本

当前知识以 Neo4j 中的当前资料、真实来源、Evidence 和 Statement 为准。本目录不保存解析中间件、模型批次缓存、问答轨迹、阶段门禁或项目完成状态。

- `source_reviews/`：用户确认、原页纠正、OCR 与表格区域复核、跨页关联、隔离和来源重绑定。新版已生效规则在 `config/source_annotations.json`；这里的历史原页裁决不是新的全文验收。
- `ground_truth/`：有实际来源和审核依据的 Gold、独立原页转录、Gold 所需源文及最终裁决。其旧 ID、来源版本和隔离状态保留，不能直接当作当前图谱的引用，也不自动用于问答。
- `ontology_reviews/`：已核本体映射的人工决定及对应完整候选和原页绑定。只有当前 `config/ontology_registry.json` 与 `ontology/core.ttl`、`ontology/shapes.ttl` 驱动正式运行。
- `registry/`：来源身份与原件记录。OCR 派生件和原始 PDF 不移入本目录。

## 文件用途迁移

以下文件逐个从旧阶段目录移到用途目录，未移动目录、未覆盖目标。迁移时仅更新保留文件之间的实际文件路径；原件/OCR 哈希、历史对象 ID、引文、审核身份和裁决内容保持原值。历史产物哈希不重新填写，旧哈希仅证明原历史字节，不表示路径调整后的文件哈希。未替换 Neo4j 的历史 `data_json` 或引用指纹。

旧路径仅用于追溯原文件位置。文件内指向已停用的旧审计、生成器、模型候选或合同的历史 inputs/producer/hash 字段保留其历史含义，不是可执行依赖。历史 SourceSpan 字段只属于参考样本，不进入新版链路。

| 原文件位置 | 当前用途文件 |
|---|---|
| `data/registry/stage2_source_selection.json` | [source_usage_confirmations.json](source_reviews/source_usage_confirmations.json) |
| `data/stage3/real_trial_confirmation.json` | [user_confirmed_source_examples.json](source_reviews/user_confirmed_source_examples.json) |
| `data/stage4/stage4_full_parse_exception_review.json` | [intentional_blank_page_decisions.json](source_reviews/intentional_blank_page_decisions.json) |
| `data/stage4/stage4_human_review_2026-09-08.json` | [user_source_boundary_decisions.json](source_reviews/user_source_boundary_decisions.json) |
| `data/stage5/stage5_golden_sample_review_2026-09-28.json` | [ocr_source_acceptance.json](source_reviews/ocr_source_acceptance.json) |
| `data/stage5/stage5_source_gaps.json` | [missing_original_pages.json](source_reviews/missing_original_pages.json) |
| `data/stage5/stage5_table_truth_review.json` | [table_source_truth.json](source_reviews/table_source_truth.json) |
| `data/stage5/stage5_truth_annotations_2026-09-28.json` | [ocr_manual_transcriptions.json](ground_truth/ocr_manual_transcriptions.json) |
| `data/stage6/ocr_review_auxiliary.json` | [auxiliary_ocr_review.json](source_reviews/auxiliary_ocr_review.json) |
| `data/stage6/ocr_review_d300n_haf103.json` | [manual_and_safety_ocr_review.json](source_reviews/manual_and_safety_ocr_review.json) |
| `data/stage6/ocr_review_standards.json` | [standard_ocr_review.json](source_reviews/standard_ocr_review.json) |
| `data/stage6/stage6_auxiliary_boundary_review.json` | [auxiliary_source_boundaries.json](source_reviews/auxiliary_source_boundaries.json) |
| `data/stage6/stage6_auxiliary_early_page_supplements.json` | [auxiliary_early_source_corrections.json](source_reviews/auxiliary_early_source_corrections.json) |
| `data/stage6/stage6_auxiliary_source_structure.json` | [auxiliary_reviewed_source_structure.json](source_reviews/auxiliary_reviewed_source_structure.json) |
| `data/stage6/stage6_auxiliary_source_supplements.json` | [auxiliary_reviewed_source_supplements.json](source_reviews/auxiliary_reviewed_source_supplements.json) |
| `data/stage6/stage6_cross_page_source_units.json` | [reviewed_cross_page_source_units.json](source_reviews/reviewed_cross_page_source_units.json) |
| `data/stage6/stage6_d300n_p50_row8_regions.json` | [manual_procedure_continuation_regions.json](source_reviews/manual_procedure_continuation_regions.json) |
| `data/stage6/stage6_evidence_annotations.jsonl` | [original_text_annotations.jsonl](source_reviews/original_text_annotations.jsonl) |
| `data/stage6/stage6_evidence_review_decisions.jsonl` | [user_evidence_acceptance.jsonl](source_reviews/user_evidence_acceptance.jsonl) |
| `data/stage6/stage6_incomplete_source_exclusions.json` | [incomplete_source_regions.json](source_reviews/incomplete_source_regions.json) |
| `data/stage6/stage6_page_review_decisions.jsonl` | [original_page_decisions.jsonl](source_reviews/original_page_decisions.jsonl) |
| `data/stage6/stage6_reference_token_exclusions.json` | [non_body_token_regions.json](source_reviews/non_body_token_regions.json) |
| `data/stage6/stage6_semantic_coverage_decisions.jsonl` | [original_page_coverage_decisions.jsonl](source_reviews/original_page_coverage_decisions.jsonl) |
| `data/stage6/stage6_source_review_overrides.json` | [reviewed_source_corrections.json](source_reviews/reviewed_source_corrections.json) |
| `data/stage6/stage6_source_structure_index.json` | [reviewed_source_groups.json](source_reviews/reviewed_source_groups.json) |
| `data/stage6/stage6_standard_source_supplements.json` | [standard_reviewed_source_supplements.json](source_reviews/standard_reviewed_source_supplements.json) |
| `data/stage6/stage6_table_evidence_annotations.jsonl` | [reviewed_table_annotations.jsonl](source_reviews/reviewed_table_annotations.jsonl) |
| `data/stage6/stage6_table_review_decisions.jsonl` | [reviewed_table_decisions.jsonl](source_reviews/reviewed_table_decisions.jsonl) |
| `data/stage6/stage6_visual_regions.json` | [reviewed_picture_regions.json](source_reviews/reviewed_picture_regions.json) |
| `data/stage6/stage6_evidence_bundle.jsonl` | [development_source_evidence.jsonl](ground_truth/development_source_evidence.jsonl) |
| `data/stage6/stage6_evidence_golden_sample.json` | [source_sample_definitions.json](ground_truth/source_sample_definitions.json) |
| `data/stage7/stage7_critical_term_golden_set.json` | [terminology_examples.json](ground_truth/terminology_examples.json) |
| `data/stage8/ontology_mapping_review_overlay.jsonl` | [mapping_decisions.jsonl](ontology_reviews/mapping_decisions.jsonl) |
| `data/stage8/ontology_mapping_shortlist.json` | [reviewed_mapping_candidates.json](ontology_reviews/reviewed_mapping_candidates.json) |
| `data/stage8/stage8_mapping_rebinding_review.json` | [mapping_source_review.json](ontology_reviews/mapping_source_review.json) |
| `data/stage9/stage9_current_source_bindings.json` | [confirmed_source_rebindings.json](source_reviews/confirmed_source_rebindings.json) |
| `data/stage11/stage11_statement_development_samples.jsonl` | [engineering_development_gold.jsonl](ground_truth/engineering_development_gold.jsonl) |
| `data/stage11/stage11_statement_holdout.jsonl` | [engineering_holdout_gold.jsonl](ground_truth/engineering_holdout_gold.jsonl) |
| `data/stage11/stage11_holdout_evidence.jsonl` | [engineering_holdout_sources.jsonl](ground_truth/engineering_holdout_sources.jsonl) |
| `data/stage11/stage11_adjudication_queue.jsonl` | [engineering_adjudication_decisions.jsonl](ground_truth/engineering_adjudication_decisions.jsonl) |
| `data/stage11/stage11_current_gold_review.json` | [engineering_gold_source_review.json](ground_truth/engineering_gold_source_review.json) |
| `data/stage11/stage11_source_rebinding_review.json` | [engineering_gold_source_rebindings.json](ground_truth/engineering_gold_source_rebindings.json) |
| `data/stage12/stage12_development_disagreement_review.json` | [engineering_disagreement_decisions.json](ground_truth/engineering_disagreement_decisions.json) |
| `data/stage12/stage12_development_gold_adjudication.json` | [engineering_gold_user_adjudication.json](ground_truth/engineering_gold_user_adjudication.json) |
| `data/stage12/stage12_reserve_gold.jsonl` | [engineering_reserve_gold.jsonl](ground_truth/engineering_reserve_gold.jsonl) |
| `data/stage12/stage12_reserve_evidence.jsonl` | [engineering_reserve_sources.jsonl](ground_truth/engineering_reserve_sources.jsonl) |
| `data/stage12/stage12_reserve_gold_reviews.json` | [engineering_reserve_source_review.json](ground_truth/engineering_reserve_source_review.json) |
| `data/stage12/stage12_reserve_source_regions.json` | [engineering_reserve_source_regions.json](ground_truth/engineering_reserve_source_regions.json) |
