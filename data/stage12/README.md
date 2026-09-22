# 阶段 12：代表章节语义抽取

本阶段把 Evidence 接入可替换的 Candidate Extraction Provider，并通过严格响应 Schema、Candidate Schema、确定性语义验证和 Stage 9 OWL/SHACL 投影门禁生成候选 Engineering Statement。候选始终停留在 `candidate_only`，不进入正式知识、Review、Release 或 Neo4j。

- `stage12_input_manifest.json` 是无标签开发输入视图，声明代表页、资料范围和语义覆盖标签。抽取器只读取该清单指定的 Stage 6 accepted Evidence，不读取 Stage 11 Gold 字段；当前数量以该 canonical manifest 为准。
- `stage12_development_candidates.json` 是唯一候选输出，Producer 为 `build_stage12_candidates.py`，消费者为开发评测、Stage 12 审计和后续审核阶段。
- `stage12_development_evaluation.json` 按边界、类型、实体、关系、量值、否定、条件、适用范围和 Evidence grounding 分字段记录开发结果，并分类错误。
- `stage12_development_disagreement_adjudication.json` 是当前 Development disagreement 的最小 canonical 裁决记录；它只解释 raw Gold/Candidate 差异，不修改 Gold/Candidate，Evaluator 由此计算 adjudicated information coverage 和已确认错误。
- `config/stage12_provider.json`、`config/stage12_prompt.txt` 和 `config/stage12_extraction_response.schema.json` 定义 Provider、Prompt 版本和严格 JSON 响应边界；正式主路径为配置的 external LLM，deterministic fixture 只能通过显式 fixture 模式用于测试和离线管线验证。
- Provider 只接收 statement text、bounded statement type、coarse relation、带角色的 Evidence 原文 entity、condition/applicability wording；数量、单位、比较符、否定、modality 和 relation direction 由确定性代码从 statement text 推导，协议常量和 entity class 由适配器补齐。传输层对 timeout、429、5xx 使用有界指数退避并尊重 `Retry-After`。
- 完成的真实 Evidence 候选会写入 `var/model_runs/stage12/evidence` 的结构化缓存；缓存键绑定 Evidence、Profile、Provider、Prompt、Schema 和语义源码指纹，缓存命中仍重新执行确定性校验，且不保存 raw model response。
- `stage12_real_llm_failure_summary.json` 是唯一当前真实 LLM 失败摘要；每次受控诊断直接覆盖，按 Evidence/attempt 区分 transport、schema 和 semantic validation，并保留 retry 后成功事件，不保存请求头、API Key 或 raw response。
- `diagnose_stage12_real_llm.py --limit 2` 只对少量 Development Evidence 绕过 success cache；只有完整通过 Schema、语义和 Evidence binding 的结果才替换 cache。Prompt、Schema 或 semantic source 变化后的旧 cache 只会被删除，不会改 key 迁移；普通运行只保留最新 batch 运行记录。
- `stage12_semantic_coverage_matrix.json` 由 `build_stage12_semantic_coverage_matrix.py` 从当前 Development Gold 确定性生成，记录 Gold hash、覆盖维度与缺口；它不声明 Gold exhaustive，也不保存 Development 质量成绩。
- `stage12_robustness_cases.json` 与 `stage12_robustness_evaluation.json` 保存真实 LLM 鲁棒性结果；`stage12_fixture_robustness_evaluation.json` 单独保存 fixture 结果，二者不得混用。
- `stage12_holdout_evaluation.json` 只能由 `evaluate_stage12_holdout.py` 生成；它读取留出 Evidence/Gold 后只写评测指标，不写回开发候选、Profile、规则或 runtime cache。
- `stage12_exit_audit.json` 是本阶段 canonical live Exit Audit；阶段退出前由审计器覆盖更新，正式退出并冻结后按变更控制处理。项目当前状态唯一以 `data/project_state.json` 为准，本 README 不固化 Prompt 版本、批次编号或动态质量成绩。Stage 13 按用户要求保持冻结，不执行、不重放、不修改。

历史 Holdout 已暴露，只能作为 observation-only 的受保护审计记录，不得用于调参或替代 Independent Reserve；其样本数、排除项和指标以 canonical Holdout Evaluation 为准。

动态运行结果、当前 lineage、质量门和 blocker 分别读取 `data/project_state.json`、canonical Candidate/Evaluation 和 `stage12_exit_audit.json`；历史过程由 Git 与受保护审计产物追溯，不在 README 中累积。运行方式：

```powershell
$projectPython = "D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe"
& $projectPython scripts/build_stage12_candidates.py --force --evaluate-development
& $projectPython scripts/diagnose_stage12_real_llm.py --limit 2  # 少量 Development 诊断，覆盖当前 failure summary
& $projectPython scripts/build_stage12_semantic_coverage_matrix.py  # 仅从当前 Gold 重建 coverage summary
& $projectPython scripts/evaluate_stage12_robustness.py  # 正式真实 LLM 模式
& $projectPython scripts/evaluate_stage12_robustness.py --fixture  # 仅离线 fixture 模式
& $projectPython scripts/audit_stage12_exit.py
& $projectPython -m pytest -q tests/stage12 tests/stage9 tests/stage10 tests/stage11 tests/unit
```
