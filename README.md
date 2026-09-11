# 核电汽轮机安调智能体

本仓库用于建设面向核电汽轮机本机及辅机安装、调试、检查、验收和问题处置场景的 CLI 技术分析与建议工具。

## 当前状态

项目当前状态唯一以 [`data/project_state.json`](data/project_state.json) 为准；阶段退出审计是各阶段的证据记录，不再复制维护项目总状态。本文件不重复维护具体阶段状态。

阶段 7 是当前完成阶段：术语输入清单、候选术语和业务能力问题模板均为 `candidate_only`，以 [`data/stage7/stage7_exit_audit.json`](data/stage7/stage7_exit_audit.json) 为准。阶段 6 的样本 Evidence、原始材料权威边界、表格隔离边界和 775 页全文 Evidence 的后续门禁，仍以 [`data/stage6/stage6_exit_audit.json`](data/stage6/stage6_exit_audit.json) 及其关联质量审计为准。原始材料始终是证据来源，OCR 只用于文字和坐标辅助。

阶段 5 的 36 页 RapidOCR 结果是一次性质量验收记录，按原始 PDF、样本清单、引擎版本、运行参数和 OCR 代码记录输入指纹；日常退出审计只读取冻结结果，不自动复核。只有负责人明确要求时，才根据指纹判断是否需要重新执行 OCR。

## 项目边界

- 项目代码、配置和运行资产只放在本仓库。
- 原始资料位于本地项目根目录的 `Original materials`，后续通过 `SOURCE_ROOT` 只读访问。
- OCR 派生产物写入 `OCR_DERIVED_ROOT`（默认 `var/derived/ocr`），不写入只读的 `SOURCE_ROOT`；Registry 以稳定逻辑路径、`source_root_id` 和 `asset_kind` 追溯其原件或派生件身份。
- 可处理资料由 `config/source_allowlist.tsv` 显式列出。
- 资料 Profile 由 `config/source_profiles/registry.json` 声明并纳入 Registry 记录；代码不得根据目录名称推断资料语义角色。
- 逻辑文档身份由 `config/document_identity.tsv` 受控分配；新增路径、派生件或 Revision 前先补齐映射，不由指纹自动产生新身份。
- OCR 派生件与其原件的精确对应关系由 `config/derived_asset_links.tsv` 声明；同一逻辑文档下，Revision 与具体文件资产分开标识。
- 构建链路只读取白名单中的文件，不递归扫描整个父工作区。
- 原始资料、数据库目录、密钥、缓存、模型运行记录和大体积发布产物不进入 Git。
- 磁盘上的 Registry、Evidence、Engineering Statement、审核记录和 Release 产物是可重建的数据权威；Neo4j 仅作为运行投影。

总体建设计划见 `总计划.md`，该文件只记录各阶段目标、任务、交付物和验收要求。

## 本地配置

复制 `.env.example` 为 `.env` 后填写本机参数。程序启动时从项目根目录 `.env` 读取配置，已有环境变量优先；模块中不写死本机绝对路径。

`SOURCE_ROOT` 只放原始资料，`OCR_DERIVED_ROOT` 只放本地 OCR 派生产物；二者不可配置为同一目录。

项目唯一运行环境为：`D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env`。项目代码、OCR、PDF、图像处理、Neo4j 客户端和测试必须使用该环境；Codex 通用 Python 不属于项目依赖环境。

Neo4j 使用 `compose.yaml` 描述，但容器由项目负责人手动创建和启动。数据库只是磁盘权威产物的运行投影，正式知识不得直接在 Neo4j Browser 中修改。

- Neo4j Browser：`http://localhost:7475`
- Neo4j Bolt：`neo4j://localhost:7688`

本地阶段 3 试点命令（必须进入 `新版demo` 目录，并在当前终端设置一次源码路径）。终端是 `cmd`，使用下面第一种写法：

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

提问命令会读取新版项目自己的 `.env` 模型连接配置，直接把问题作为命令行参数即可，兼容旧版 Demo 的提问方式；也可使用 `ask "问题"` 的显式写法。省略问题时会进入 `请输入工程问题：` 交互输入。默认输出简洁的中文回答、适用性提示和依据；需要完整机器可读结果时加 `--json`。也可以用 `--question` 覆盖默认值，或用 `--no-evidence-send` 只做检索、不调用模型。回答依据同时显示物理页和已确认的逻辑页；没有逻辑页时只显示物理页。只发送 Neo4j 命中的最小证据片段；没有模型响应时仍返回检索证据和失败原因。Neo4j 未启动时会给出简短连接提示，不输出无关堆栈。新版代码不依赖旧版 Demo，旧版目录删除后不影响新版运行。

其中 `import` 和 `status` 只需在需要时运行；日常提问只需要：

```powershell
cd "D:\本体\汽轮机安调项目\项目初期demo\新版demo"
$env:PYTHONPATH="src"
$projectPython = "D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe"
& $projectPython -m turbine_kg.stage3.trial_cli "你的问题"
```

在 `cmd` 中对应为：

```cmd
cd /d "D:\本体\汽轮机安调项目\项目初期demo\新版demo"
set "PYTHONPATH=%CD%\src"
"D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe" -m turbine_kg.stage3.trial_cli "你的问题"
```

也可以像旧版 Demo 一样，启动后再输入问题：

```powershell
& $projectPython -m turbine_kg.stage3.trial_cli
```

如果当前终端显示的是 `D:\本体\汽轮机安调项目\项目初期demo>`，说明还在父目录，必须先执行上面的 `cd`；否则会出现 `No module named 'turbine_kg'`。

## 当前检查

资料处理前可运行以下只读检查，确认白名单路径、文件大小和 SHA-256 均与当前资料一致：

```cmd
"D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe" scripts/check_source_allowlist.py
```

工程测试：

```cmd
set PYTHONPATH=src
"D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe" -m pytest
```

构建或更新 Source Registry（只读取白名单 PDF，不写入 Neo4j）：

```cmd
"D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe" scripts/build_source_registry.py
```

Registry 产物位于 `data/registry`。其中 `source_manual_findings.jsonl` 只记录已经完成的封面、页眉、页数和派生关系人工核验；未确认事项仍保留在 `source_review_queue.jsonl`。`stage2_source_selection.json` 记录进入阶段 3 研发试点的候选来源，不替代阶段 15 的正式准入门。`source_duplicate_groups.jsonl` 保存已物化的分组，`source_duplicate_relations.jsonl` 保存组内或候选关系。

阶段 6 的构建顺序和权威产物见 `data/stage6/README.md`。最终退出检查执行 `scripts/audit_stage6_exit.py`，只有 `stage6_exit_audit.json` 的 17 项检查全部通过，`stage6_evidence_bundle.jsonl` 才可作为阶段 7 的样本 Evidence 输入。阶段 7 的输入清单和候选构建分别执行 `scripts/build_stage7_input_manifest.py`、`scripts/build_stage7_terminology.py`，最终退出检查执行 `scripts/audit_stage7_exit.py`。
