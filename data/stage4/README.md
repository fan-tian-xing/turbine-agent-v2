# 阶段 4：通用文档结构（进行中）

阶段 4 当前先建立与安调本体无关的 Document IR，不进行整本书抽取，也不做阶段 5 的 OCR 精度和 Golden Sample 基准。

当前已落地：

- `config/document_ir_contract.json`：身份、页码、坐标和字段级真源边界；
- `config/layout_profiles.json`：页面能力路由的最小声明式 Profile；
- `src/turbine_kg/documents/`：Document、Revision、Asset、Page、ParsingRun、BlockVersion、Table、Figure、SourceSpan 模型、校验和统一页面入口；
- Registry 身份目录：48 个资产、44 个逻辑文档和 OCR 派生关系均通过 `IdentityCatalog` 校验，路径只作为查找地址，不作为身份；
- 原生文本、扫描待 OCR、混合页面和需复核页面的统一 IR 路由；
- 不依赖文件名的页面能力判断；
- 多 Revision 旧配置兼容读取和最小中文显示映射；
- 阶段 3 兼容层的后续接入基础；
- `stage3.document_compat.project_document_ir`：仅投影 Page/SourceSpan，不生成阶段 3 的业务适用范围、Evidence 或 Statement；
- `parse_registered_pdf`：Registry IdentityCatalog → 真实 PyMuPDF → Document IR → 阶段 3 结构投影的最小只读端到端链路；
- 真实 PyMuPDF 原生文字页的图片会进入 `image` Block/Figure，并保留页面 bbox；扫描页仍只进入 `scan_only`，不伪造 OCR 文本，表格行列恢复留到阶段 5；
- `stage4_smoke_2026-09-08.json`：15 页、5 个已登记资产的真实链路冒烟记录；`stage4_exit_audit.json`：当前仍为 `in_progress`，明确阶段 4尚未关闭的边界和下一道门；
- 当前文档层回归测试共 23 项，项目全量测试共 77 项。
- 阶段 3 兼容层目前是只读的结构投影桥接，仅提供 Revision/Page/SourceSpan，不替代 Stage 3 的语义 Fixture、Evidence 或 Statement；人工更正已作为不覆盖原始解析结果的 Overlay 记录，表格 SourceSpan 定位已纳入校验。

阶段 4 当前仍不能关闭。除自动化测试外，还需要项目负责人审核资料身份与原件—OCR 对账、代表性页面定位、真实表格边界、人工更正 Overlay 和输入—输出—异常数量对账，并签署最终退出审计。审核要求及阶段 5 启动前置条件见根目录 `总计划.md` 的“阶段 4 人工审核门槛”和“阶段 5 启动前置条件”。审核结果应记录为 `data/stage4/stage4_human_review_YYYY-MM-DD.json`；在该记录形成前，不得把 `stage4_exit_audit.json` 改为 `complete`。

当前明确留到阶段 5 及以后：

- OCR 引擎选择和精度基准；
- 30 页 Golden Sample；
- 正式 Evidence Contract；
- 完整业务本体、OWL/SHACL；
- 正式 Neo4j 图谱和 Release。
