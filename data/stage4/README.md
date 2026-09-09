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
- `stage4_smoke_2026-09-08.json`：15 页、5 个已登记资产的真实链路冒烟记录；`stage4_full_parse_audit_2026-09-09.json`：五份冻结处理单元的 775 页全量结构解析、哈希、页序映射和异常页清单；`stage4_final_exit_audit_2026-09-09.json`：待项目负责人签字的最终退出审计；`stage4_exit_audit.json`：当前仍为 `in_progress`，明确阶段 4尚未关闭的边界和下一道门；
- 当前文档层回归测试共 25 项，项目全量测试共 80 项。
- 阶段 3 兼容层目前是只读的结构投影桥接，仅提供 Revision/Page/SourceSpan，不替代 Stage 3 的语义 Fixture、Evidence 或 Statement；人工更正已作为不覆盖原始解析结果的 Overlay 记录，表格 SourceSpan 定位已纳入校验。

阶段 4 当前仍不能关闭。项目负责人已在本聊天确认 HAF103 资料单元、辅机书页面完整性和“表格行列恢复留到阶段 5”的边界；确认记录为 `stage4_human_review_2026-09-08.json`。两个全量解析空输出页的视觉处置记录为 `stage4_full_parse_exception_review_2026-09-09.json`。最终回归、全量审计复跑和退出审计刷新均已完成；仅待项目负责人接受 `stage4_final_exit_audit_2026-09-09.json`，完成前不得把 `stage4_exit_audit.json` 改为 `complete`。

复跑全量审计：在仓库根目录设置 `PYTHONPATH=src` 后执行 `C:\\Users\\尹仕程\\Documents\\Codex\\.venvs\\default\\Scripts\\python.exe scripts/audit_stage4_full_parse.py`。

当前明确留到阶段 5 及以后：

- OCR 引擎选择和精度基准；
- 30 页 Golden Sample；
- 正式 Evidence Contract；
- 完整业务本体、OWL/SHACL；
- 正式 Neo4j 图谱和 Release。
