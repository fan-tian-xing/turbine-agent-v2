# 核电汽轮机安调智能体 v2

本仓库用于建设面向核电汽轮机本机及辅机安装、调试、检查、验收和问题处置场景的 CLI 技术分析与建议工具。

## 当前状态

项目当前状态唯一以 [`data/project_state.json`](data/project_state.json) 为准；阶段退出审计是各阶段的证据记录，不再复制维护项目总状态。本文件不重复维护具体阶段状态。

阶段产物、构建顺序和使用边界见 [`data/README.md`](data/README.md) 及各阶段目录的 README。原始材料始终是证据来源；OCR 派生件可用于 Document IR、文字适配、候选发现和坐标辅助，但不替代原始材料作为 Evidence 真值。

阶段 5 已完成五份资料的 775 个现存物理页文字适配；阶段 0–5 已复核并清理遗留引用。阶段 6 的 36 页样本退出审计记录 28 页形成 398 条 Evidence、8 页按负向门禁排除，但后续独立原页核查发现 DL5190.3 物理第 25、113 页的图中文字遗漏，按当前用户要求暂缓修复。阶段 7 已按现行输入重建 18,628 条候选并通过退出检查；阶段 8 已对 16 项当前候选完成三方独立原页审核与来源重绑，5 项候选映射接受、11 项暂缓，未发布正式词汇或本体新类。阶段 9 已将旧研发样本的 3 组 4 条引文核对并绑定现行来源，OWL/SHACL 与投影前门禁通过；阶段 10 当前运行缓存与版本影响范围审计通过。阶段 11 的 46 条开发 Gold 已绑定现行 Evidence，退出审计通过；阶段 12 的 Development 正按当前 Evidence 和中文提示词重建，独立 Reserve 仅准备预冻结。原件缺页只记录缺口，不补造内容。五份资料的全文 Evidence 属于阶段 15。

## 项目边界

- 项目代码、配置和运行资产只放在本仓库。
- 原始资料位于本地项目根目录的 `Original materials`，通过 `SOURCE_ROOT` 只读访问。
- OCR 派生产物写入 `OCR_DERIVED_ROOT`（默认 `var/derived/ocr`），不写入只读的 `SOURCE_ROOT`；Registry 以稳定逻辑路径、`source_root_id` 和 `asset_kind` 追溯其原件或派生件身份。
- 可处理资料由 `config/source_allowlist.tsv` 显式列出。
- 资料 Profile 由 `config/source_profiles/registry.json` 声明并纳入 Registry 记录；代码不得根据目录名称推断资料语义角色。
- 逻辑文档身份由 `config/document_identity.tsv` 受控分配；新增路径、派生件或 Revision 前先补齐映射，不由指纹自动产生新身份。
- OCR 派生件与其原件的精确对应关系由 `config/derived_asset_links.tsv` 声明；同一逻辑文档下，Revision 与具体文件资产分开标识。
- 构建链路只读取白名单中的文件，不递归扫描整个父工作区。
- 原始资料、数据库目录、密钥、缓存、模型运行记录和大体积发布产物不进入 Git。
- 磁盘上的 Registry、Evidence、Engineering Statement、审核记录和 Release 产物是可重建的数据权威；Neo4j 仅作为运行投影。

总体建设计划见 `总计划.md`，该文件只记录各阶段目标、任务、交付物和验收要求。

阶段 5 正式文件、哈希和验收范围见 [`data/stage5/stage5_sample_manifest.json`](data/stage5/stage5_sample_manifest.json)、[`data/registry/ocr_validation_report.json`](data/registry/ocr_validation_report.json) 和 [`data/stage5/stage5_exit_audit.json`](data/stage5/stage5_exit_audit.json)。阶段 6 当前样本结果见 [`data/stage6/stage6_exit_audit.json`](data/stage6/stage6_exit_audit.json) 和 [`data/stage6/stage6_semantic_coverage_audit.json`](data/stage6/stage6_semantic_coverage_audit.json)，须连同上述已知原页缺口理解；阶段 7–11 当前结果见 [`data/stage7/stage7_exit_audit.json`](data/stage7/stage7_exit_audit.json)、[`data/stage8/stage8_exit_audit.json`](data/stage8/stage8_exit_audit.json)、[`data/stage9/stage9_exit_audit.json`](data/stage9/stage9_exit_audit.json)、[`data/stage10/stage10_audit.json`](data/stage10/stage10_audit.json) 和 [`data/stage11/stage11_exit_audit.json`](data/stage11/stage11_exit_audit.json)。阶段 12 以当前中文提示词重建的输出及退出审计为准；旧结果不能充当当前验收。

## 本地配置

复制 `.env.example` 为 `.env` 后填写本机参数。程序启动时从项目根目录 `.env` 读取配置，已有环境变量优先；模块中不写死本机绝对路径。

`SOURCE_ROOT` 只放原始资料，`OCR_DERIVED_ROOT` 只放本地 OCR 派生产物；二者不可配置为同一目录。

项目唯一运行环境为：`D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env`。项目代码、OCR、PDF、图像处理、Neo4j 客户端和测试必须使用该环境；Codex 通用 Python 不属于项目依赖环境。

Neo4j 使用 `compose.yaml` 描述，但容器由项目负责人手动创建和启动。数据库只是磁盘权威产物的运行投影，正式知识不得直接在 Neo4j Browser 中修改。

- Neo4j Browser：`http://localhost:7475`
- Neo4j Bolt：`neo4j://localhost:7688`

本地阶段 3 试点命令：先进入 `新版demo` 目录并设置源码路径，按当前终端选择一种写法。`import` 和 `status` 在需要时运行，日常直接执行提问命令即可。

```cmd
cd /d "D:\本体\汽轮机安调项目\项目初期demo\新版demo"
set "PYTHONPATH=%CD%\src"
"D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe" -m turbine_kg.stage3.trial_cli import
"D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe" -m turbine_kg.stage3.trial_cli status
"D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe" -m turbine_kg.stage3.trial_cli "密封瓦座水平结合面用塞尺检查要求是什么？"
```

如果终端是 PowerShell：

```powershell
cd "D:\本体\汽轮机安调项目\项目初期demo\新版demo"
$env:PYTHONPATH="src"
$projectPython = "D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe"
& $projectPython -m turbine_kg.stage3.trial_cli import
& $projectPython -m turbine_kg.stage3.trial_cli status
& $projectPython -m turbine_kg.stage3.trial_cli "密封瓦座水平结合面用塞尺检查要求是什么？"
```

提问命令读取项目 `.env` 的模型连接配置，支持直接传入问题、`ask "问题"` 或 `--question "问题"`；省略问题时进入交互输入。默认输出中文回答、适用性提示和依据，`--json` 输出机器可读结果，`--no-evidence-send` 只检索、不调用模型。依据显示物理页及已确认的逻辑页；仅向模型发送 Neo4j 命中的最小证据片段。模型调用失败时返回检索证据和失败原因，Neo4j 未启动时返回连接提示。

## 当前检查

资料处理前可运行以下只读检查，确认白名单路径、文件大小和 SHA-256 均与当前资料一致：

```cmd
"D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe" scripts/check_source_allowlist.py
```

工程测试：

```powershell
New-Item -ItemType Directory -Force -Path "var/tmp" | Out-Null
& $projectPython -m pytest -p no:cacheprovider --basetemp "var/tmp/tests-$([guid]::NewGuid().ToString('N'))"
```

构建或更新 Source Registry（只读取白名单 PDF，不写入 Neo4j）：

```cmd
"D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe" scripts/build_source_registry.py
```

Registry 产物位于 `data/registry`。其中 `source_manual_findings.jsonl` 只记录已经完成的封面、页眉、页数和派生关系人工核验；未确认事项仍保留在 `source_review_queue.jsonl`。`stage2_source_selection.json` 记录进入阶段 3 研发试点的候选来源，不替代阶段 15 的正式准入门。`source_duplicate_groups.jsonl` 保存已物化的分组，`source_duplicate_relations.jsonl` 保存组内或候选关系。

阶段 6 的构建顺序和权威产物见 [`data/stage6/README.md`](data/stage6/README.md)。最终退出检查执行 `scripts/audit_stage6_exit.py`；退出记录状态通过且无阻塞项后，`stage6_evidence_bundle.jsonl` 才可作为阶段 7 的样本 Evidence 输入。阶段 7 的输入清单和候选构建分别执行 `scripts/build_stage7_input_manifest.py`、`scripts/build_stage7_terminology.py`，最终退出检查执行 `scripts/audit_stage7_exit.py`。

阶段 8、9、10 的顺序检查分别执行 `scripts/audit_stage8_exit.py`、`scripts/audit_stage9_exit.py --current-source-bindings data/stage9/stage9_current_source_bindings.json`、`scripts/audit_stage10_exit.py`。阶段 10 的实际运行模式为：

```powershell
& $projectPython scripts/build_stage7_terminology.py --runtime
& $projectPython scripts/build_stage7_terminology.py --runtime --force
```

运行结果写入本地受控缓存，使用范围、输入门禁及生命周期字段见 [`data/stage10/README.md`](data/stage10/README.md)；运行合同见 [`config/runtime_contract.json`](config/runtime_contract.json)。

阶段 11 包含 Engineering Statement 合同、唯一 Evaluation Sample Registry、语义标注和裁决。36 页开发/回归 Golden 与 15 页 Statement 留出分开登记；留出只由评价入口读取，盲测不读取。原用户裁决和隔离决定保留。原有 17 条旧 Evidence 来源均已迁移；辅机第 360 页低真空答文拆成 6 条、高真空答文拆成 5 条，当前共有 46 条开发 Gold。现行来源与三方语义复核另记哈希，入口和退出均为 `complete`。阶段 12 仍须独立重验。阶段 11 产物说明见 [`data/stage11/README.md`](data/stage11/README.md)。

阶段 15 的目标是按 Registry 身份对五份资料 775 个唯一物理页完成全文 Evidence、Engineering Statement 和 candidate 图谱处理。开发页、试点页及阶段 14 candidate 都要迁移对账后只处理一次；OCR 仅为派生资产，视觉页、隔离页和非内容页均需有明确处置，正式 candidate 还需通过覆盖、审核、重复加载和隔离 Neo4j 对账门禁。
