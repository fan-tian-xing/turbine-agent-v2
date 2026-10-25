# 汽轮机安调知识问答 Demo

本项目把书籍、规范中的安装调试知识整理为 Evidence 和 Statement，写入 Neo4j；问答时结合本体关系与全文检索召回依据，由 LangGraph 编排回答。默认输出「建议」和「依据来源」。当前试点范围由 `config/processing_scopes.json` 指定，本体设计见 `ontology/ontology_design.md`。

项目架构见 [architecture.md](architecture.md)。

仓库保存代码、配置、本体定义与测试。原 PDF、OCR 文件、Neo4j 数据卷、模型缓存和 `.env` 是本机资料，不随 Git 仓库提供。生效的来源纠正规则保存在 `config/source_annotations.json`。仅克隆仓库不会得到已入库的知识图谱。

主要模块按职责命名：`documents/page_mapping.py` 负责原件与 OCR 页映射，`documents/text_parser.py` 解析文本与 Markdown，`knowledge/chapter_processing.py` 编排整章处理，`knowledge/statement_extraction.py` 抽取和规范化 Statement，`knowledge/statement_sync.py` 负责写入、图谱投影及来源支持，`knowledge/ontology_mapping.py` 计算及更新本体映射，`knowledge/store.py` 统一存储和事务。原文实体绑定和公式纠正分别放在 `entity_bindings.py`、`formula_sources.py`。

## 数据流程

1. `documents` 解析来源版本、条款、分项、表格和跨页续文；`evidence` 组装可独立理解、精确定位的原文 Evidence，保存完整正文 `text` 及其成员定位 `members`。
2. 模型按 `config/ontology_registry.json` 提取最简 Statement：完整断言、知识类型、原始术语及其角色、所依据的 Evidence 成员和必要的语义关系。条件、数值、单位和公式保留在断言文本中，不另存参数字段。实体名称取原文连续词组，不拼接上下文或补写并列词的共享后缀。程序根据原始术语和来源上下文完成本体映射，检查结构、来源成员与覆盖；失败组最多重试两次。
3. Neo4j 保存资料、Evidence、Statement、已审定本体类代理及有证据的连接。文档节点和资料来源使用稳定标识；Evidence、Statement 和资料结构节点使用 `logical_key` 做增量匹配，内容变化由 `content_hash` 判断。通用书籍名称映射类；没有明确身份时不创建现场设备实例。未映射的名称留在 Statement 和 Evidence 中。
4. 问题分析读取本体类定义与别名，用户问题和现场数值只保存在会话内存，问答过程只读 Neo4j。查询同时使用类及关系导航和 Neo4j Evidence 全文索引。未登记为类的复合对象名称，可在原文明确出现、组成术语绑定同一来源成员且类别无歧义时，使用现有类检索，并限制到对应 Evidence；不新增复合类或全局别名。生成优先使用已读来源对应的 Statement 和对象角色，程序解析短编号引用；核验读取完整 Evidence，Statement 不足时以原文兜底，不回读原 PDF。
5. LangGraph 编排检索、补查和回答。总括询问安装要求、注意事项时，生成与独立核验均须覆盖直接适用的分项及条件分支；具体故障、现场数值问题则围绕所问内容回答，不展开无关分项。程序硬校验限于有效任务、已读 Evidence、成员绑定、明确的表图条款引用和图片读取状态。已移除逐字数值比对及其提示；语义核验只检查实质事实、对象、阶段、关键条件和量值错误，不审查措辞风格或事实与合理推断的标签划分，也不要求数值比较结果逐字出现在原文。对象及阶段适用性、条件完整性、数值计算和正文中的资料编号由完整原文语义核验判断；不因名称未逐字出现、量值不在原文、单位未登记或文件名缩写而直接删回答。引用识别采用同一套规则，根据明确引用表达识别，不靠有限单位清单排除数值，也不把“条、节”等数量词当作编号。当前采用单轮问答，不追问。通用问答流程由统一提示词和本体注册表共同驱动。

`Evidence` 是完整可引用的来源单元；一条 Evidence 含多项要求时可对应多条 Statement。程序的抽取完成状态不代表语义一定正确，试点按页对照原文核查。条件或量值信息不明确时保留原文并说明判断边界，不能补造现场事实。

Statement 本身就是 Core，不另存副本。`entities` 保留原始 `label`、`class` 和 `role`，程序将可替换的映射放在实体的 `ontology_class_id`、`ontology_binding` 或 `ontology_mapping_review` 中。本体升级后用 `--remap` 重新检查资料内全部 Statement 的映射，保留 ID、文本与支持关系，不调用模型重抽；只有原有语义确有遗漏时才定向重抽 Evidence。

正常回答直接使用已读来源对应的 Statement，模型引用本次问答内的 `S1`、`S2`；程序沿原有 `supports` 还原 Evidence 原文与定位，模型无需填写真实节点或成员 ID。没有可用 Statement、抽取未完整覆盖，或补答需要核对原文时，提供带 `E1` 等短编号的 Evidence 正文作为兜底。独立核验仍对照完整原文；短编号不写入 Neo4j，不改变 Statement 身份或存储结构。

抽取时，模型给出的未注册类别退回 Mention，再由程序映射；可选语义关系不合法时只舍弃该关系，保留正文与来源绑定均合法的 Statement。模型漏报完成标志不单独触发重抽，仍须通过原生来源成员覆盖检查；模型明确报告缺漏时继续重试。直接入库仍严格检查字段、来源归属、关系端点和事务一致性。

Neo4j 供 Cypher 查询的 `Statement.type` 和 `OntologyClass.aliases` 与节点内容同步写入，保证按知识类型筛选及类别名查询可用。类的提及关系只提供候选上下文，不能直接证明该条要求适用于所问对象；回答仍需核对原文主体与条件。

Evidence 的完整原文统一存为 `text`，抽取和问答都能读取；`search_tokens` 从该正文生成，供全文索引使用。`members` 保留原文成员及定位，引用分项时由程序补齐必要的引导句和续行，不新增 `items`，不再另外保存 `search_text`。数值问题先使用程序解析的限值；无法明确对应时，模型只从已读图谱原文中定位对象、条件和限值，程序核对来源后执行比较，临时限值不写回知识图谱。

多对象比较按独立对象分别检索，共享原问题但不拼接类名。查询关系仍由所选知识类型决定，不自动增加其他关系。`navigate` 默认只召回对象本类，依据不足时以 `expand=true` 扩展上下位或组成关系。各对象先获得关键原文，再读取引用与更多候选；已召回的关键依据优先于候选翻页，关键词结果与本体结果继续合并去重。引用按原始页码及来源阅读顺序排列，只展示实际选用的成员，不改变答案陈述和支持关系。

问答模型的提示词集中在 `src/turbine_kg/agent/prompts.py`。 问答直接使用这套通用流程，不加载外部流程包，也不保留流程选择开关。 提示词只定义任务理解、本体与关键词检索、来源读取、回答、核验和补查的通用流程，不内置测试题、设备专用步骤或条款答案。领域概念由本体提供，知识结论由当前召回的原文提供。全文检索按完整对象名称和审定别名召回，活动、现象等词组用于排序；不使用词向量。检索返回相关候选，模型根据实际问题筛选。建议可包含必要准备和检查，具体修复工艺必须有资料支持。独立核验未完成时保留有依据的部分建议并说明限制。

用户明确阶段时，匹配阶段的依据优先于普通图谱关联；高度匹配的阶段条款，或与已读材料同等或更直接相关的对象条款未读时，不因模型已选过其他资料就提前认定资料足够。复杂现场问题先由模型结合候选摘要选择要读取的 Evidence，摘要不能作为回答依据。核验发现阶段错误或对应依据不足时，LangGraph 返回检索规划，按缺口选择已有候选或补查；已有原文足够的表达或遗漏问题仍直接补答。最多返回检索两次，共用原有工具和时间预算，保留已通过建议。补查不要求增加 Evidence；确认引用超出入库范围时说明缺口，不继续读取无关材料。阶段不明确的资料仍可作为候选，不按字面阶段硬删除。

LangGraph 分别使用理解、检索、生成、核验、补答和输出节点。核验通过后输出；已有依据可解决的遗漏进入补答，再返回核验；需要核实依据时返回检索；确认超出当前资料范围时保留可靠建议和缺口后结束。每次生成后的补答最多一次。来源无效的草稿不进入答案，但不要求恢复每条草稿；核验依据用户问题判断真实遗漏，已完成任务的旧拒绝项立即从内存清理。空补答不重复核验；问题、事实、建议正文及已读原文未变时复用已通过结论，只核验新增或修改内容。普通 CLI 在“资料边界与未完成项”中显示尚未解决的来源缺口和漏答，`--json` 保留完整结构化结果；过程状态只在本次问答内存中流转。

补答以及已有建议的补查更新使用显式 `operations`：`add` 对应本次缺口 ID，`replace` 指定已有回答陈述 ID。程序校验任务、目标 ID 和 Evidence 支持后再执行；替换沿用原陈述 ID，失败时保留原有正确内容，不把整份答案追加。没有实质遗漏时允许空操作。单位识别使用完整边界，不把 `m²`、`m³` 或未知复合单位截取成 `m`；未支持的单位保留完整符号和原文交给 LLM 理解，不因程序不能换算而拒绝有依据的回答；程序只计算能确定的换算。

全文检索的规范类名没有命中时，可按原问题中的对象称谓补查，不增加全局别名；模型同时接收来源章节，以区分同名对象在不同型式或作业下的要求。例外只改变原文明示的义务，其他仍适用要求继续保留。模型 JSON 格式错误、返回缺少正文、临时超时或断连时，沿用原有重试次数和截止时间重试。

## 本机准备与问答（CMD）

需要 Python 3.12、Neo4j 5 和已获授权的资料。首次在新电脑使用时，在仓库目录打开 `cmd`：

```cmd
py -3.12 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -e .
copy .env.example .env
```

按 `.env.example` 填写本机 Neo4j、模型与资料路径。仓库根目录的 `compose.yaml` 可启动本地 Neo4j：`docker compose up -d`。原始资料和 OCR 文件须放在 `.env` 指向的位置，并与 `config/source_allowlist.tsv`、`config/document_identity.tsv`、`config/derived_asset_links.tsv` 的登记相符。已有项目虚拟环境时可直接激活，不必重新安装。

本机已有专用环境时，在 `新版demo` 目录打开 CMD，使用：

```cmd
call ..\runtime-python\turbine-kg-env\Scripts\activate.bat
turbine "除氧器试运前需要满足什么要求"
```

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

当前资料的问答验收题可用 `python -B scripts\check_answers.py --list` 查看，去掉 `--list` 后执行。验收直接调用当前通用问答流程，分别检查来源绑定与语义完整性；其他资料题放在 `--phase generality`，需要先导入对应资料。

先检查一页 Evidence 和 Statement，再扩到两页，确认跨页、条件、量值和来源均正确后才扩大处理范围。资料更新时按稳定逻辑标识增量写入当前图谱：变化记录用 `MERGE/SET` 更新，失效记录和关系按资料标识删除，不复制候选代。

运行配置见 `config/README.md`。OCR 衍生文件按登记的完整相对路径解析，保留子目录，避免不同资料的同名文件相互混淆。资料更新采用 Neo4j 官方增量写入思路：稳定键匹配后使用 `MERGE`/`SET` 更新，按本次新资料结果删除已失效的资料节点和受管理关系。解析和本体校验应在写入前完成，不能把未完成结果直接写入正式图谱。适用范围统一在 Evidence 层传递，保留范围原文和来源定位，再关联到 Statement；解析器不再提供另一套运行时范围注入接口。模型外发由 `.env` 的 `LLM_ALLOW_EVIDENCE_SEND` 控制；项目不会把密钥写入图谱或文档。保留原件和 OCR 以便核对来源；本体合同与图谱只维护当前版本。

代码中的 `KnowledgeStore.sync_document()` 提供单版本增量同步接口：输入一本文档的完整节点与受管理关系集合，节点按 `logical_key` 匹配，内容按 `content_hash` 判断，关系按稳定关系键合并。只允许同步文档自有节点；`OntologyClass` 等共享本体节点不能作为文档删除范围。

添加章节前，程序自动检查所有已入库文档及其 Statement 的本体版本；版本变化时先重新映射，再处理新章节。即使 Document 已标记为新版本、Statement 仍为旧版本，也会补齐映射。重映射不调用模型、不重新 OCR，保留旧 Statement 的 ID、正文、原始 Mention、角色及 Evidence 支持，只重建本体映射和派生关系；来源未变且已完成抽取的旧 Evidence 不重复抽取。只有原文发生变化、原抽取未完成或显式要求复验时才重新抽取。

`--remap` 可主动刷新所选文档的映射；同一文档配置多个章节时只执行一次，并同时更新其他版本过时的已入库文档。整组重映射与本体同步在同一个事务内完成，失败会回滚，避免混用新旧版本。本体删除或改名后，只清理没有关系、且未被 Statement 引用的废弃类节点。`MENTIONS` 同时保存有来源锚点的非主体提及和上下文检索线索，`object` 角色不会因不能建立主体要求边而丢失，边的 `entity_roles` 保留原始角色：真实章节标题、已支持的条款引导语、已验证复合名中的对象可用于召回；上下文单独保存在边的 `context_members` 中，不增加 Statement 实体，也不建立主体要求或继承参数。 检索合并多条命中路径时，逐条保留主体或上下文身份；上下文命中不会因为同条 Statement 还命中其他主体关系而升级为直接主体匹配。入库复用同一映射函数核对来源，并清理当前 Statement 已失效的派生导航边及受管理引用。重映射更新版本时不会覆盖刚从当前 Core 重建的支持和引用信息。已有语义关系在当前本体不再支持时仍保留在 Statement 中，暂不生成相应图谱边；关系改名时可在新关系的 `aliases` 中显式登记旧关系 ID，重映射即可恢复投影，不根据相似名称猜测。数值比较和范围条件共用 `quantities.py` 的单位换算，检索停止条件使用同一个判断入口。

## 本机运行目录与清理

`../runtime-python` 是本机运行资源目录，不是临时输出目录：

- `turbine-kg-env`：项目 Python 环境及已安装依赖，保留完整环境。
- `cache/paddlex/official_models`：OCR、版面及表格模型；名称包含 `cache` 不代表可直接清空。
- `model-cache/hub`：Docling 版面及表格模型，路径由 `config/agent.json` 指定。

`__pycache__` 中的 `.pyc` 和 `.pytest_cache` 属于可再生成缓存，清理后后续运行可能重新出现。验证时使用 `python -B -m pytest tests/current -q -p no:cacheprovider` 可避免本次生成这些缓存。不按空文件、压缩包或锁文件后缀批量删除依赖目录中的文件；其中可能包含包数据、安装记录及下载管理文件。删除模型会导致再次下载，也可能使离线 OCR 无法运行。
