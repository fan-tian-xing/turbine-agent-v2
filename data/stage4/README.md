# 阶段 4：通用文档结构

阶段 4 当前先建立与安调本体无关的 Document IR，不进行整本书抽取，也不做阶段 5 的 OCR 精度和 Golden Sample 基准。项目当前状态唯一以 `../project_state.json` 为准，阶段 4 的门禁证据见 `stage4_exit_audit.json`。

主要产物：

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
- `stage4_smoke_2026-09-08.json`：15 页、5 个已登记资产的真实链路冒烟记录；`stage4_full_parse_audit_2026-09-12.json`：当前五份冻结处理单元的 775 页全量结构解析、哈希、页序映射和异常页清单；2026-09-09 版本作为历史审计记录保留；`stage4_final_exit_audit_2026-09-09.json`：最终退出审计；`stage4_exit_audit.json`：阶段 4 退出汇总；`stage4_dead_code_orphan_audit.json`：阶段 4 范围内的死代码和孤立产物审查。
- 阶段 4 文档层回归覆盖 Document IR、Identity Catalog 和注册 PDF 链路；项目全量测试数量以最近一次完整 pytest 运行记录为准。
- 阶段 3 兼容层目前是只读的结构投影桥接，仅提供 Revision/Page/SourceSpan，不替代 Stage 3 的语义 Fixture、Evidence 或 Statement；人工更正已作为不覆盖原始解析结果的 Overlay 记录，表格 SourceSpan 定位已纳入校验。

五份冻结处理单元的全量解析、异常页处置、最终回归和退出审计刷新记录在上述审计文件中；OCR 精度、Golden Sample 和表格行列恢复仍按总计划进入阶段 5。

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
