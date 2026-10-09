# 阶段 12：开发样本 Evidence 语义抽取

本阶段把 Evidence 接入可替换的 Candidate Extraction Provider，并通过严格响应 Schema、Candidate Schema、确定性语义验证和 Stage 9 OWL/SHACL 投影门禁生成候选 Engineering Statement。候选始终停留在 `candidate_only`，不进入正式知识、Review、Release 或 Neo4j。

Evidence 与 Candidate Statement 按多对多处理：一条 Evidence 可因仅作上下文而产生零条 Candidate，也可包含多个独立工程命题；跨 Evidence 的完整命题可绑定多个已确认来源。零 Candidate 必须有明确理由并进入覆盖对账。Statement 保持可独立检索、引用和判断适用性的中等粒度，每条只有一个主要粗粒度谓词，可关联多个实体、Evidence、范围和数量；本阶段不新增独立 Relation 真源。

- `stage12_input_manifest.json` 是无标签开发输入视图，对应 Stage 11 Registry 的全部 36 个 Development 页；每页区分可抽取、待复核和仅作上下文的 Evidence。未经复核的图字、公式 OCR 进入待复核；跨页残句和无明确答案的试题只作上下文。抽取器不读取 Stage 11 Gold 字段。原 8 页 `stage12_representative_baseline.json` 只保留为回归子集。
- `data/stage6/stage6_semantic_coverage_audit.json` 逐页对照原始页与 Stage 6 Evidence。未解决的语义遗漏或表格区域会阻断 Stage 12 预冻结准备，不会被已有 Evidence 质量审计的通过状态覆盖。
- `data/stage6/stage6_source_structure_index.json` 记录已核对的操作步骤、分类清单及题干选项来源组。Stage 12 退出审计分别核对每个步骤或清单项的 Evidence/span、原文与 Candidate 绑定；有明确答案的试题还须绑定同题的题干和选项，不能借用相邻题目。单条 Evidence 已处理不等于整组完成。
- `stage12_development_candidates.json` 只有在当前 Manifest 的全部可抽取 Evidence 均完成后才是当前候选输出，Producer 为 `build_stage12_candidates.py`；`evidence_outcomes` 必须逐一说明每条可抽取 Evidence 产出了 Candidate，或给出 `no_statement` 理由。失效的旧 Candidate 已删除；完整批次尚未形成时该路径应不存在，进度只读取 `stage12_real_llm_failure_summary.json` 和通过现行指纹复核的逐 Evidence 缓存。
- 抽取响应应带有当次临时 source-unit coverage ledger，将可辨认的来源命题单元逐项映射到 Candidate 或明确的 `no_statement`、续接、隔离理由。它只用于覆盖校验、失败定位和必要重试，不写成正式 Candidate、Relation 或长期知识产物，也不能替代阶段 6 的原页覆盖审计。
- `stage12_development_evaluation.json` 仅在当前 Candidate 完整生成并完成分歧裁决后，按边界、类型、实体、关系、量值、否定、条件、适用范围和 Evidence grounding 分字段记录开发结果；旧 Candidate 对应的 Evaluation 和分歧汇总已删除，不得作为当前成绩。
- `stage12_development_raw_evaluation.json` 先记录当前 Candidate 与 Gold 的原始比较；`stage12_development_disagreement_review.json` 保存逐项人工裁决及其当前候选绑定；`stage12_development_disagreement_adjudication.json` 只汇总已复核的当前分歧，不修改 Gold 或 Candidate。退出审计核对三者及其来源哈希。
- Development Gold 是非穷尽 reference。原始 matched-candidate precision、原始 over-split 数量和未裁决的 matched-field 差异只作诊断，不能直接阻断退出；退出门以经裁决的信息覆盖、Evidence 支持率、待裁决为零和关键错误为零为准。Gold 未列出但由 Evidence 支持的 Candidate 不能自动判错。
- `config/stage12_provider.json`、`config/stage12_prompt.txt` 和 `config/stage12_extraction_response.schema.json` 定义 Provider、中文自然语言提示词版本和严格 JSON 响应边界；JSON 字段与枚举保留合同规定的原值。正式主路径为配置的 external LLM，deterministic fixture 只能通过显式 fixture 模式用于测试和离线管线验证。
- Provider 只接收 statement text、bounded statement type、coarse relation、带角色的 Evidence 原文 entity、condition/applicability wording；数量、单位、比较符、否定、modality 和 relation direction 由确定性代码从 statement text 推导，协议常量和 entity class 由适配器补齐。传输层对 timeout、429、5xx 使用有界指数退避并尊重 `Retry-After`。
- 校验分为 `hard_error` 与 `needs_review`：Schema、身份/来源血缘、明确数字和单位、比较方向、显式禁止或否定反转、来源组成员错误等可确定问题属于硬错误；规范强度变化、因果或关系方向、字符相似度、同义改写、复杂切句、隐含主语和粒度差异先形成待复核风险，不得单独耗尽模型重试。`needs_review` 未经 Development 裁决或阶段 13 审核清零时不能满足当前质量门，也不能进入正式知识。
- 完成的真实 Evidence 候选会写入 `var/model_runs/stage12/evidence` 的结构化缓存；缓存键绑定 Evidence、Profile、Provider、Prompt、Schema 和语义源码指纹，缓存命中仍重新执行确定性校验，且不保存 raw model response。
- `stage12_real_llm_failure_summary.json` 是当前真实 LLM 运行摘要；只保存分类、调用次数、耗时和运行进度，不保存 token 统计、Evidence 原文、模型结构化回复或逐次尝试内容。
- `diagnose_stage12_real_llm.py --limit 2` 只在失败排查时对少量 Development Evidence 绕过 success cache；只有完整通过 Schema、语义和 Evidence binding 的结果才替换 cache。诊断会覆盖运行摘要，随后必须重跑完整 Development 批次以恢复全量摘要。Prompt、Schema 或 semantic source 变化后的旧键缓存不会命中当前输入，也不会改 key 迁移；无用旧缓存应在重建后清理。
- `stage12_semantic_coverage_matrix.json` 由 `build_stage12_semantic_coverage_matrix.py` 从当前 Development Gold 确定性生成，记录 Gold hash、覆盖维度与缺口；它不声明 Gold exhaustive，也不保存 Development 质量成绩。
- `stage12_robustness_cases.json` 保存冻结的开发用例；`stage12_robustness_evaluation.json` 只有绑定当前 Prompt、Schema、语义代码和 Provider 配置时才是当前真实 LLM 鲁棒性结果。`stage12_fixture_robustness_evaluation.json` 单独保存当前 fixture 结果，二者不得混用；任何输入指纹变化都会使旧结果失效并等待重跑。
- 已暴露的 15 页 Stage 11 Holdout Gold 保留用于回归与来源审计，不参与阶段 12 最终验收；其旧详细评测不再作为当前阶段 12 输入。
- `stage12_exit_audit.json` 是本阶段 canonical live Exit Audit；阶段退出前由审计器覆盖更新，正式退出并冻结后按变更控制处理。项目当前状态唯一以 `data/project_state.json` 为准，本 README 不固化 Prompt 版本、批次编号或动态质量成绩。Stage 13 按用户要求保持冻结，不执行、不重放、不修改。

历史 Holdout 已暴露，不得用于调参或替代 Independent Reserve；其页面身份保留在 Stage 11 Registry 中，供新 Reserve 选页时排除。

## Independent Reserve 的预冻结边界

`stage12_reserve_source_regions.json` 记录新 Reserve 的原页选区、已核选择题答案分组与旧 Reserve 页面排除身份。`stage12_reserve_evidence.jsonl` 只纳入已与原始 PDF 核对的选区，不声称五张整页已完整覆盖；同一条证据里的试题干扰选项不得作为正确答案的语义支持。`stage12_reserve_gold_reviews.json`、`stage12_reserve_gold.jsonl` 和 `stage12_reserve_gold_audit.json` 保存模型运行前的来源裁决、Gold 及一致性检查。旧 Reserve 已暴露，不能再作为独立验收。

`build_stage12_reserve_freeze_manifest.py` 只生成 `prepared_not_approved` 清单，固定 Gold、来源复核、Evidence、样本与来源 Registry、Profile 路由、Contract、Prompt、Schema、语义/候选构建代码、LLM 传输、Stage 9 本体与校验依赖、Provider 配置、Reserve runner/evaluator 和 Exit Gate 的 SHA-256。清单的 `human_approved` 状态需要后续人工批准，并填写与实际 Provider/模型配置一致的 `provider_runtime_fingerprint`；准备清单本身不授权 Reserve 模型调用。

`stage12_reserve_pipeline.py` 默认只输出只读执行计划。正式一次性执行入口是 `--execute`：只有已批准且哈希仍匹配的 freeze manifest、完整的独立 Gold 裁决和五条 Registry/Evidence 校验全部通过时才开放，并在第一个 Provider 调用之前独占创建执行标记。失败也保留标记，不能自动重试同一 Reserve。Candidate 只写入独立 Reserve 产物，不进入 Development、Release 或 Neo4j。离线测试仅接受合成 Evidence 和 fixture Provider，不读真实 Reserve Evidence。正式 Candidate 生成后，须另行保存针对该 Candidate/Gold/初评哈希的人工分歧裁决，再由 `--finalize` 重算 Evidence-first 指标；Exit Audit 会重验冻结资产、一次性标记、Candidate 和裁决血缘及 Gate 数值。

本轮仅准备并冻结新的独立 Reserve，不运行 `--execute` 或 `--finalize`。阶段 12 最终退出状态由新 Reserve 的一次性验收决定。

动态运行结果、当前 lineage、质量门和 blocker 分别读取 `data/project_state.json`、当前 `stage12_real_llm_failure_summary.json`、完整批次生成后的 canonical Candidate/Evaluation 和 `stage12_exit_audit.json`；历史过程由 Git 与受保护审计产物追溯，不在 README 中累积。运行方式：

```powershell
$projectPython = "D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe"
& $projectPython scripts/build_stage12_candidates.py --force  # 仅在 Stage 6 覆盖审计放行后
& $projectPython scripts/build_stage12_development_adjudication.py --raw-only
# 对照原始材料审查 raw 分歧，将当前裁决写入 stage12_development_disagreement_review.json 后：
& $projectPython scripts/build_stage12_development_adjudication.py
& $projectPython scripts/build_stage12_candidates.py --evaluate-existing  # 不重建 Candidate，保持人工裁决的来源哈希
& $projectPython scripts/build_stage12_semantic_coverage_matrix.py  # 仅从当前 Gold 重建 coverage summary
& $projectPython scripts/evaluate_stage12_robustness.py  # 正式真实 LLM 模式
& $projectPython scripts/evaluate_stage12_robustness.py --fixture  # 仅离线 fixture 模式
& $projectPython scripts/audit_stage12_exit.py
& $projectPython -m pytest -q tests/stage12 tests/stage9 tests/stage10 tests/stage11 tests/unit
```
