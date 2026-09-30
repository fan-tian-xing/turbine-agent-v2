# 阶段 4：通用文档结构

阶段 4 建立与安调本体无关的 Document IR，只做文档结构解析，不进行工程语义抽取，也不做阶段 5 的 OCR 精度和 Golden Sample 基准。项目当前状态唯一以 `../project_state.json` 为准，其中的 `exit_audit` 指向当前阶段 4 门禁依据。

主要产物：

- `config/document_ir_contract.json`：身份、页码、坐标和字段级真源边界；
- `config/layout_profiles.json`：页面能力路由的最小声明式 Profile；
- `src/turbine_kg/documents/`：Document、Revision、Asset、Page、ParsingRun、BlockVersion、Table、Figure、SourceSpan 模型、校验和统一页面入口；
- Registry 身份目录：资产、逻辑文档和 OCR 派生关系通过 `IdentityCatalog` 校验，路径只作为查找地址，不作为身份；
- 原生文本、扫描待 OCR、混合页面和需复核页面的统一 IR 路由；
- 不依赖文件名的页面能力判断；
- 多 Revision 旧配置兼容读取和最小中文显示映射；
- 阶段 3 兼容层的后续接入基础；
- `stage3.document_compat.project_document_ir`：仅投影 Page/SourceSpan，不生成阶段 3 的业务适用范围、Evidence 或 Statement；
- `parse_registered_pdf`：Registry IdentityCatalog → 真实 PyMuPDF → Document IR → 阶段 3 结构投影的最小只读端到端链路；
- 真实 PyMuPDF 原生文字页的图片会进入 `image` Block/Figure，并保留页面 bbox；扫描页仍只进入 `scan_only`，不伪造 OCR 文本，表格行列恢复留到阶段 5；
- [`stage4_full_parse_audit.json`](stage4_full_parse_audit.json)：当前阶段 5 清单中五份正式 OCR 的全量结构解析、实际文件哈希、页序映射和异常页清单；固定路径是当前结构审计入口，旧日期运行结果不供默认消费者读取。
- [`stage4_full_parse_exception_review.json`](stage4_full_parse_exception_review.json)：当前原件及派生件的空白页绑定，继承既有原件审核事实；不新增用户批准。`stage4_human_review_2026-09-08.json` 保留原始审核事实。
- 阶段 4 文档层回归覆盖 Document IR、Identity Catalog 和注册 PDF 链路；项目全量测试数量以最近一次完整 pytest 运行记录为准。
- 阶段 3 兼容层目前是只读的结构投影桥接，仅提供 Revision/Page/SourceSpan，不替代 Stage 3 的语义 Fixture、Evidence 或 Statement；人工更正已作为不覆盖原始解析结果的 Overlay 记录，表格 SourceSpan 定位已纳入校验。

默认入口对当前正式 OCR 的全部 775 个现存物理页执行结构解析，校验 Registry 身份、当前文件哈希、页序映射和空白页例外。它不以阶段 5 的逐字逐格验收为前提，也不替代该验收。阶段 3 冻结清单及其用户确认保持原样；只有显式指定 `--historical-frozen --output <独立路径>` 才进入历史结构诊断。OCR 精度、Golden Sample 和表格行列恢复按阶段 5 合同核查。

复跑全量审计（PowerShell）：在仓库根目录执行：

```powershell
$env:PYTHONPATH = "src"
$projectPython = "D:\本体\汽轮机安调项目\项目初期demo\runtime-python\turbine-kg-env\Scripts\python.exe"
& $projectPython scripts/audit_stage4_full_parse.py
```

当前明确留到阶段 5 及以后：

- OCR 引擎选择和精度基准；
- 阶段 5 冻结的 36 页 Golden Sample；
- 正式 Evidence Contract；
- 完整业务本体、OWL/SHACL；
- 正式 Neo4j 图谱和 Release。
