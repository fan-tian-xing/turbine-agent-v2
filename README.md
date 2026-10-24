# 汽轮机安调知识问答 Demo

本项目把书籍、规范中的安装调试知识整理为 Evidence 和 Statement，写入 Neo4j；问答时结合本体关系与全文检索召回依据，由 LangGraph 编排回答。默认输出「建议」和「依据来源」。当前试点范围由 `config/processing_scopes.json` 指定，本体设计见 `ontology/ontology_design.md`。

项目架构见 [architecture.md](architecture.md)。

仓库保存代码、配置、本体定义与测试。原 PDF、OCR 文件、Neo4j 数据卷、模型缓存和 `.env` 是本机资料，不随 Git 仓库提供。生效的来源纠正规则保存在 `config/source_annotations.json`。仅克隆仓库不会得到已入库的知识图谱。

主要模块按职责命名：`documents/page_mapping.py` 负责原件与 OCR 页映射，`documents/text_parser.py` 解析文本与 Markdown，`knowledge/chapter_processing.py` 编排整章处理，`knowledge/statement_extraction.py` 抽取与持久化 Statement，`knowledge/statement_sync.py` 负责事务替换与来源支持，`knowledge/ontology_mapping.py` 计算及更新本体映射。原文实体绑定和公式纠正分别放在 `entity_bindings.py`、`formula_sources.py`。

## 数据流程

1. `documents` 解析来源版本、条款、分项、表格和跨页续文；`evidence` 组装可独立理解、精确定位的原文 Evidence，保存完整正文 `text` 及其成员定位 `members`。
2. 模型按 `config/ontology_registry.json` 提取最简 Statement：完整断言、知识类型、原始术语及其角色、所依据的 Evidence 成员和必要的语义关系。条件、数值、单位和公式保留在断言文本中，不另存参数字段。程序根据原始术语和来源上下文完成本体映射，检查结构、来源成员与覆盖；失败组最多重试两次。
3. Neo4j 保存资料、Evidence、Statement、已审定本体类代理及有证据的连接。文档节点和资料来源使用稳定标识；Evidence、Statement 和资料结构节点使用 `logical_key` 做增量匹配，内容变化由 `content_hash` 判断。通用书籍名称映射类；没有明确身份时不创建现场设备实例。未映射的名称留在 Statement 和 Evidence 中。
4. 问题分析读取本体类定义与别名，用户问题和现场数值只保存在会话内存，问答过程只读 Neo4j。查询同时使用类及关系导航和 Neo4j Evidence 全文索引。回答模型接收完整 Evidence、出处及 Statement 提供的精简对象角色信息，不重复发送 Statement 全文，不回读原 PDF。
5. LangGraph 编排检索、补查和回答。生成后由独立模型请求检查建议的依据、适用性和影响本题建议的关键遗漏，不要求覆盖所有召回内容。程序检查任务绑定、Evidence 成员、引用、数值单位和图片读取状态，允许已确认的等值单位换算。当前采用单轮问答，不追问。Skill 配置为空，后续只有形成稳定的案例流程后才决定是否启用。

`Evidence` 是完整可引用的来源单元；一条 Evidence 含多项要求时可对应多条 Statement。程序的抽取完成状态不代表语义一定正确，试点按页对照原文核查。条件或量值信息不明确时保留原文并说明判断边界，不能补造现场事实。

Statement 本身就是 Core，不另存副本。`entities` 保留原始 `label`、`class` 和 `role`，程序将可替换的映射放在实体的 `ontology_class_id`、`ontology_binding` 或 `ontology_mapping_review` 中。本体升级后用 `--remap` 重新检查资料内全部 Statement 的映射，保留 ID、文本与支持关系，不调用模型重抽；只有原有语义确有遗漏时才定向重抽 Evidence。

Evidence 的完整原文统一存为 `text`，抽取和问答都能读取；`search_tokens` 从该正文生成，供全文索引使用。`members` 保留原文成员及定位，引用分项时由程序补齐必要的引导句和续行，不新增 `items`，不再另外保存 `search_text`。数值问题先使用程序解析的限值；无法明确对应时，模型只从已读图谱原文中定位对象、条件和限值，程序核对来源后执行比较，临时限值不写回知识图谱。

问答模型的提示词集中在 `src/turbine_kg/agent/prompts.py`。普通问答不启用 Skill。全文检索按完整对象名称和审定别名召回，活动、现象等词组用于排序；不使用词向量。检索返回相关候选，模型根据实际问题筛选。建议可包含必要准备和检查，具体修复工艺必须有资料支持。独立核验未完成时保留有依据的部分建议并说明限制。

## 本机准备与问答（CMD）

需要 Python 3.12、Neo4j 5 和已获授权的资料。首次在新电脑使用时，在仓库目录打开 `cmd`：

```cmd
py -3.12 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -e .
copy .env.example .env
```

按 `.env.example` 填写本机 Neo4j、模型与资料路径。仓库根目录的 `compose.yaml` 可启动本地 Neo4j：`docker compose up -d`。原始资料和 OCR 文件须放在 `.env` 指向的位置，并与 `config/source_allowlist.tsv`、`config/document_identity.tsv`、`config/derived_asset_links.tsv` 的登记相符。已有项目虚拟环境时可直接激活，不必重新安装。

在已激活环境的同一 `cmd` 窗口运行：

```cmd
turbine "你要问的安调问题"
```

不传问题时进入连续提问模式；输入 `/退出` 结束。每个新窗口只需重新激活环境一次。`turbine` 是 `pyproject.toml` 注册的命令，不需要每次设置 `PYTHONPATH`。

`--trace` 显示内部检索与程序校验诊断，`--json` 返回完整结构化结果。查询直接读取 Neo4j 当前资料，不使用候选代或激活切换。

需要审查对象是否仍为 Mention 时，可只读运行：

```cmd
python -B scripts\inspect_graph.py --mapping-report
```

报告按标签统计未绑定本体类的实体，不会修改 Neo4j。只有同一类含义稳定、且能由来源确认的重复标签，才应补入注册表别名或类；不能把一次性复合描述直接登记成类。

## 导入与验证

先配置并核对来源，再在已激活环境中运行：

```cmd
python -B scripts\check_source_allowlist.py
turbine-ingest --planned --prepare-only
turbine-ingest --planned
turbine-ingest --planned --work-pages 37 --workers 2
turbine-ingest --planned --work-pages 37,38 --retry-semantics --workers 2
turbine-ingest --planned --remap
python -B -m pytest tests/current -q -p no:cacheprovider
```

`--planned` 默认处理配置章节的全部页面；`--work-pages` 仅用于测试和定向修复。这里的 37、38 是当前试点的物理页示例；导入其他资料时须先修改 `config/processing_scopes.json` 并核对来源登记，不能直接沿用这些页码。Document 的 `requested_pages`、`processed_pages`、`semantic_pages`、`unprocessed_pages` 分别记录请求、实际解析、语义完成和尚未完成的页面。导入会更新 Neo4j，首次使用需先启动数据库。

当前资料的问答验收题可用 `python -B scripts\compare_answer_modes.py --list` 查看，去掉 `--list` 后执行。未配置 Skill 时只运行通用问答；其他资料题放在 `--phase generality`，需要先导入对应资料。

先检查一页 Evidence 和 Statement，再扩到两页，确认跨页、条件、量值和来源均正确后才扩大处理范围。资料更新时按稳定逻辑标识增量写入当前图谱：变化记录用 `MERGE/SET` 更新，失效记录和关系按资料标识删除，不复制候选代。

运行配置见 `config/README.md`。资料更新采用 Neo4j 官方增量写入思路：稳定键匹配后使用 `MERGE`/`SET` 更新，按本次新资料结果删除已失效的资料节点和受管理关系。解析和本体校验应在写入前完成，不能把未完成结果直接写入正式图谱。模型外发由 `.env` 的 `LLM_ALLOW_EVIDENCE_SEND` 控制；项目不会把密钥写入图谱或文档。保留原件和 OCR 以便核对来源；本体合同与图谱只维护当前版本。

代码中的 `KnowledgeStore.sync_document()` 提供单版本增量同步接口：输入一本文档的完整节点与受管理关系集合，节点按 `logical_key` 匹配，内容按 `content_hash` 判断，关系按稳定关系键合并。只允许同步文档自有节点；`OntologyClass` 等共享本体节点不能作为文档删除范围。
