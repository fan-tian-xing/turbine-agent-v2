# 阶段 4：通用文档结构（进行中）

阶段 4 当前先建立与安调本体无关的 Document IR，不进行整本书抽取，也不做阶段 5 的 OCR 精度和 Golden Sample 基准。

当前已落地：

- `config/document_ir_contract.json`：身份、页码、坐标和字段级真源边界；
- `config/layout_profiles.json`：页面能力路由的最小声明式 Profile；
- `src/turbine_kg/documents/`：Document、Revision、Asset、Page、ParsingRun、BlockVersion、Table、Figure、SourceSpan 模型、校验和统一页面入口；
- 原生文本、扫描待 OCR、混合页面和需复核页面的统一 IR 路由；
- 不依赖文件名的页面能力判断；
- 多 Revision 旧配置兼容读取和最小中文显示映射；
- 阶段 3 兼容层的后续接入基础；
- 当前 Document IR 回归测试共 12 项，项目全量测试共 65 项。

当前明确留到阶段 5 及以后：

- OCR 引擎选择和精度基准；
- 30 页 Golden Sample；
- 正式 Evidence Contract；
- 完整业务本体、OWL/SHACL；
- 正式 Neo4j 图谱和 Release。
