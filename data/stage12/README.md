# 阶段 12：代表章节语义抽取

本阶段首次把 Evidence 接入语义抽取器，并通过 Stage 10 的 `statement_extraction` runtime 生成候选 Engineering Statement。候选先经过 Stage 12 Candidate Schema 和 Stage 9 OWL/SHACL 投影门禁，始终停留在 `candidate_only`，不进入正式知识、Release 或 Neo4j。

- `stage12_input_manifest.json` 是无标签开发输入视图：8 个代表页、5 份资料，覆盖数值/单位、范围、否定、条件、多对象/步骤和列举。抽取器只读取该清单指定的 Stage 6 accepted Evidence，不读取 Stage 11 Gold 字段。
- `stage12_development_candidates.json` 是唯一候选输出，Producer 为 `build_stage12_candidates.py`，消费者为开发评测、Stage 12 审计和后续审核阶段。
- `stage12_development_evaluation.json` 按边界、类型、实体、关系、量值、否定、条件、适用范围和 Evidence grounding 分字段记录开发结果，并分类错误。
- `stage12_holdout_evaluation.json` 只能由 `evaluate_stage12_holdout.py` 生成；它读取留出 Evidence/Gold 后只写评测指标，不写回开发候选、Profile、规则或 runtime cache。
- `stage12_exit_audit.json` 是本阶段退出证据记录；项目当前状态唯一以 `data/project_state.json` 为准。当前 runtime、来源绑定、隔离和 OWL/SHACL 投影检查已通过，但开发集关系/适用范围质量门仍需修复，且代表页尚未具备章节级身份；Stage 13 按用户要求保持冻结，不执行、不重放、不修改。

当前观测：开发集 Evidence grounding 为 1.0，但关系和适用范围字段仍按冻结阈值单独计量；Holdout 注册 50 条、其中 48 条按冻结的 accepted-Evidence 规则评测，2 条隔离表格行在比较前排除，详细行号、页码和原因以评测产物为准。留出结果历史上已暴露，只能作为不可用于调参的独立验收记录。

运行方式：

```powershell
$projectPython = "D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe"
& $projectPython scripts/build_stage12_input_manifest.py
& $projectPython scripts/build_stage12_candidates.py --evaluate-development
& $projectPython scripts/evaluate_stage12_holdout.py
& $projectPython scripts/audit_stage12_exit.py
& $projectPython -m pytest -q -p no:cacheprovider tests/stage12 tests/stage11 tests/unit/test_project_state.py
```
