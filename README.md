# 核电汽轮机安调智能体

本仓库用于建设面向核电汽轮机本机及辅机安装、调试、检查、验收和问题处置场景的 CLI 技术分析与建议工具。

## 当前状态

独立工程、配置边界和本地运行基础已经建立。阶段 0、1、2A、2B、3 已完成：全库 Source Registry 基线已建立，并已选出 5 个满足试点条件的来源。D300N、`DL/T 863—2016`、HAF103（当前提供的印刷页 3–34 完整供给单元）和《汽轮机辅机安装（第二版）》整本 480 页资料单元的本地 OCR 已完成逐页比对和独立抽检。该选择记录只证明资料可进入阶段 3 的研发试点；阶段 15 的 `First Batch Admission Gate` 仍须在正式处理开始前逐来源复核。阶段 3 已建立三类公开/合成 Fixture、确定性适用性匹配、JSON 精确过滤、最小可追溯投影、Equipment/Component/Process/Procedure/Step/QuantityValue 对象链和逐 Claim 校验，并完成 15 个真实试点页面的文本适配抽取和 3 组人工确认 Evidence。当前已将这 3 组确认 Evidence 对应的 4 条 Engineering Statement 导入新版 Neo4j，验证中文检索能够回查资料、页码和证据；并完成一次当前代码与 `gpt-5.6-luna` 的真实 LLM 重放，Claim 校验通过，最终回答由本地验证后的 Claim 组装。Registry 现以结构化范围约束确认来源和 Statement，LLM 面向用户的文本只由本地通过校验的 Claim 重新组装。阶段 3 严格审计已关闭，`formal_release=false` 不变。阶段 4 已进入实施：Document IR、身份目录、原生/扫描/混合页面统一入口、人工更正 Overlay、Registry→真实 PDF→Document IR→阶段 3结构投影的只读链路和 77 项全量测试已建立；阶段 4尚未完成，正式整本书抽取和正式 Release 尚未开始。Registry 构建器已完成第一轮模块化：CLI 入口、PDF 检查、指纹、资料 Profile、重复关系、分组物化、校验和 Registry 组装已分离；重复关系和重复分组分别写入 `source_duplicate_relations.jsonl` 与 `source_duplicate_groups.jsonl`。其他资料仍按后续批次保留在审核队列，尚未进入正式抽取。

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

Neo4j 使用 `compose.yaml` 描述，但容器由项目负责人手动创建和启动。数据库只是磁盘权威产物的运行投影，正式知识不得直接在 Neo4j Browser 中修改。

- Neo4j Browser：`http://localhost:7475`
- Neo4j Bolt：`neo4j://localhost:7688`

本地阶段 3 试点命令（必须进入 `新版demo` 目录，并在当前终端设置一次源码路径）。终端是 `cmd`，使用下面第一种写法：

```cmd
cd /d "D:\本体\汽轮机安调项目\项目初期demo\新版demo"
set "PYTHONPATH=%CD%\src"
python -m turbine_kg.stage3.trial_cli import
python -m turbine_kg.stage3.trial_cli status
python -m turbine_kg.stage3.trial_cli ask "密封瓦座水平结合面用塞尺检查要求是什么？"
```

如果终端是 PowerShell：

```powershell
cd "D:\本体\汽轮机安调项目\项目初期demo\新版demo"
$env:PYTHONPATH="src"
python -m turbine_kg.stage3.trial_cli import
python -m turbine_kg.stage3.trial_cli status
python -m turbine_kg.stage3.trial_cli ask "密封瓦座水平结合面用塞尺检查要求是什么？"
```

提问命令会读取新版项目自己的 `.env` 模型连接配置，只需输入问题即可。默认输出简洁的中文回答、适用性提示和依据；需要完整机器可读结果时加 `--json`。也可以用 `--question` 覆盖默认值，或用 `--no-evidence-send` 只做检索、不调用模型。只发送 Neo4j 命中的最小证据片段；没有模型响应时仍返回检索证据和失败原因。新版代码不依赖旧版 Demo，旧版目录删除后不影响新版运行。

其中 `import` 和 `status` 只需在需要时运行；日常提问只需要：

```powershell
cd "D:\本体\汽轮机安调项目\项目初期demo\新版demo"
$env:PYTHONPATH="src"
python -m turbine_kg.stage3.trial_cli ask "你的问题"
```

在 `cmd` 中对应为：

```cmd
cd /d "D:\本体\汽轮机安调项目\项目初期demo\新版demo"
set "PYTHONPATH=%CD%\src"
python -m turbine_kg.stage3.trial_cli ask "你的问题"
```

也可以像旧版 Demo 一样，启动后再输入问题：

```powershell
python -m turbine_kg.stage3.trial_cli ask
```

如果当前终端显示的是 `D:\本体\汽轮机安调项目\项目初期demo>`，说明还在父目录，必须先执行上面的 `cd`；否则会出现 `No module named 'turbine_kg'`。

## 当前检查

资料处理前可运行以下只读检查，确认白名单路径、文件大小和 SHA-256 均与当前资料一致：

```cmd
python scripts/check_source_allowlist.py
```

工程测试：

```cmd
set PYTHONPATH=src
python -m pytest
```

构建或更新 Source Registry（只读取白名单 PDF，不写入 Neo4j）：

```cmd
python scripts/build_source_registry.py
```

Registry 产物位于 `data/registry`。其中 `source_manual_findings.jsonl` 只记录已经完成的封面、页眉、页数和派生关系人工核验；未确认事项仍保留在 `source_review_queue.jsonl`。`stage2_source_selection.json` 记录进入阶段 3 研发试点的候选来源，不替代阶段 15 的正式准入门。`source_duplicate_groups.jsonl` 保存已物化的分组，`source_duplicate_relations.jsonl` 保存组内或候选关系。
