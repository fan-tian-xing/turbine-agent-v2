"""Centralized prompts for the question answering pipeline.

The prompts describe model tasks only. Engineering facts must come from the
ontology registry and the Evidence/Statement payload returned by Neo4j.
"""
from __future__ import annotations


SEMANTIC_CONFIRMATION_RULES = (
    'context.confirmed_semantic_reviews是程序从现有Evidence记录读取、逐成员校验的用户限定解释，'
    '只在field指定的语义字段及绑定原文成员内使用；不是整条陈述或整页核验通过的证明，'
    '该确认不代表其他判断均正确，也不能扩展到其他对象、字段或版本。模型不得自行编造人工确认标记。'
)
FORMULA_PHASE_RULES = (
    '公式和计算须保留变量含义、单位、适用条件及输入所对应的对象状态；方法描述不代表用户已执行，'
    '输入不足时说明缺少的条件，不能补造变量值。'
)
ANSWER_SCOPE_RULES = (
    '回答范围由原问题的对象、目标、阶段和已知条件确定；关联或同章出现不证明适用。'
    '结合条款引导和上下文判断分项、跨条款引用及必要前提，不自动展开无关内容。'
    '总括询问覆盖直接适用的并列要求和条件分支；具体问题只回答所问判断及其必要依据。'
    '例外只改变原文明示的义务，保留其余仍适用的要求；不同阶段的前提与目标阶段本身的要求应分别说明。'
)


def understanding_prompt() -> str:
    """Build the problem-understanding prompt for the first model request."""
    return (
        '理解原始问题，识别回答所需的对象、活动、阶段、意图、知识类型、已知条件和现场数值，按用户实际提出的独立问题分解任务；knowledge_items只是查找知识的方向，同一现场问题不得按知识类型拆成多个必答子问题。当前只回答本次问题，不发起追问；缺少现场条件时保留未知并在建议中说明适用前提。'
        '只保留用户明确提出的对象、参数、动作和阶段；不得凭行业经验补充对象或现场事实。可共同回答的内容合并为一个任务，避免总任务与分项重复。subquestion忠实保留原问题，不增加用户未提出的目标或交付要求。'
        '开放枚举先保留覆盖原问题的任务，读取来源后再按原文细化。对象可按输入本体定义规范化为同一设备、部件或程序的已有类名称；这不代表新增独立作业。原始称谓与型式保留在subquestion和query_terms，无法确认类别时保留原称。query_terms使用简短、完整的专业术语，不把整句问题或修饰性长句当作一个检索词组；'
        '设备、部件、材料、型式、活动和阶段的限定必须保留在subquestion、query_terms、activity和phase中。'
        'object_tasks每项object使用一个独立对象；多对象比较可共享subquestion，但各对象分别检索，不将并列对象名称拼成一个对象。'
        'activity只能填写用户明确询问或限定的已注册活动，phase保留用户原句中的阶段限定；没有明确限定时填写null，不能由设备名称或条款内容反推。'
        '单位、测量部位和数值只能来自用户原句，不能从资料或常识补齐；用户现场数值不是知识库事实。'
        '用户明确给出数值时必须记录到本次会话的fact_updates（仅内存，不写入知识图谱），value使用对象{"value":"数值","unit":"单位","target":"对象"}；比例的unit须保留用户明确给出的比较基准，在“倍”后直接接原句的基准名称，不添加加号或其他连接符，没有基准时不补。target填写该任务的设备或部件对象，不拼接参数或动作。非数值条件的value可用字符串。'
        '只返回JSON {"objects":["对象"],'
          '"knowledge_items":["知识类型"],"object_tasks":[{"object":"对象",'
          '"subquestion":"该对象要解决的子问题","activity":"已明确的活动或null",'
          '"phase":"已明确的阶段或null","intent":"lookup|diagnosis|comparison|procedure|other",'
          '"query_terms":["实际参数或动作"],'
          '"knowledge_items":["知识类型"],"requires_site_inputs":false,'
          '"calculation_required":false}],"source_kinds":["text|table|image"],'
          '"visual_required":false,"fact_updates":[{"object":"对象","key":"条件名",'
          '"parameter_id":"已有参数身份或null","value":"明确值",'
          '"source_text":"当前用户原句的连续摘录"}],"clarifications":[]}}。'
    )


def retrieval_prompt(tool_description: str) -> str:
    text = (
        '选择下一批必要的来源工具。只返回JSON '
        '{"calls":[{"tool":"...","parameters":{}}],"stop":false}。'
        f'可用工具：{tool_description}。只能使用注册的relation_id。'
        '同时使用本体关系导航和问题中的对象、活动、阶段、参数搜索Evidence；命中后必须read_evidence读取Neo4j中的完整来源，'
        '搜索得分不能作为依据。沿已读来源的明确引用补查，优先读取尚未读取的候选；没有进展或来源已经足够时stop。'
        '候选召回不等于适用；优先读取直接相关依据、必要前提和明确引用，再补充能解决问题缺口的材料，不因关联而扩展其他独立任务。'
        '本体导航默认只找对象本类；直接依据不足时用expand=true扩展相关类。多对象先保证各对象读取关键依据，不以翻完全部候选页作为回答前提。'
        '用户明确阶段时优先读取匹配该阶段的依据；review_feedback是上一轮核验指出的依据缺口，须保持原问题的对象和阶段补查，不能将检索词改写成另一作业。仍有高度匹配的阶段条款未读时不能stop。'
        'source_candidates中的statement_previews仅为候选语义摘要，用于选择来源工具；不能据此直接回答。结合核验缺口选择真正相关的Evidence读取，不机械按图谱路径或原始排序取下一条。已有原文已能解决问题时可stop返回生成，不要求补查一定找到新Evidence。'
        '核对section与条款引导句中的型式和范围；同名对象在不同型式或作业下可能有不同要求，用户未明确时保留相关候选供回答说明条件，不混用数值或自行补定型式。'
        '工程依据只取自Neo4j返回的Statement和Evidence；不能读取本地PDF、OCR或图片，不能凭图题替代像素读取。'
    )
    return text


def answer_review_prompt() -> str:
    """Check substantive engineering errors and required coverage once."""
    text = (
        '这是一次独立的回答核验请求，只检查实质错误和影响原问题的漏答。'
        '对照完整Evidence核对对象、阶段、关键前提和例外、数值单位、否定与规范强度；来源不支持的新事实或工艺不能通过。'
        '仅有明确事实错误、对象或阶段不适用、改变结论的条件遗漏时设为false，reason必须指出具体错误。'
        '不审查措辞风格，不要求逐字复述，不因source/inference标签或一句中含有合理推断而拒绝整条建议。'
        '对象名称可以用规范名称、简称或上下文指代，不因规范名称未逐字出现在问题中否定适用性。'
        '有依据的合规判断、用户数值与规范限值的直接比较、单位换算和简单计算均允许；结果无需逐字出现于原文，程序unknown不是错误。'
        '结论可以由用户事实和来源合理推导，无须原文逐字给出同样结论；证据已足以回答原问题时结束，不扩展用户未要求的目标。'
        '来源缺少的信息应作为回答边界说明，保留有依据的结论，不把资料边界当作内容错误，也不要求补造。'
        'verified_claim_ids中的建议在相同问题和原文下已经通过，无需再次判定；只核对新增或修改的建议及整体漏答、重复内容。'
        'binding_rejections只是未采用的草稿，不是必须恢复的答案；已有内容覆盖或本题不需要时忽略，不因此要求补答。'
        'missing_items只列已有原文明确提供、当前答案未表达、且直接回答用户问题所必需的内容；总括询问须覆盖相关分项和条件分支，具体问题不扩展无关作业。'
        '例外只改变原文明示的义务，保留其余仍适用的要求；不同阶段的前提不能代替所问阶段本身的要求。'
        '必要依据尚未读取时填写retrieval_request；仅措辞不同、来源没有用户未要求的信息或未穷尽其他相关条款，不构成补答或补查理由。'
        '目录或未读摘要不能作为事实依据；真实来源ID及成员存在性由程序检查。遗漏引用使用可用S/E短编号。'
        'context.references为deferred_outside_scope或external_document时，说明正文未入库，保留边界而不反复补查。补查可以复核已读材料，不要求找到新Evidence。'
        'resolved_gap_ids只消除当前答案已经解决的旧模型缺口；redundant_claims只标含义和条件确实完全覆盖的重复建议。'
        '只返回JSON {"tasks":[{"task_id":"...",'
        '"claim_verdicts":[{"claim_id":"...","supported":true,"applicable":true,"conditions_preserved":true,"reason":"具体实质错误或简短依据"}],'
        '"missing_items":[{"reason":"必要遗漏","references":["S1或可用的E1"]}],'
        '"retrieval_request":null,"resolved_gap_ids":[],"redundant_claims":[]}]}。'
        '需要补查时retrieval_request为{"reason":"具体依据缺口","query_terms":["检索词"]}；'
        '确有重复时redundant_claims为[{"claim_id":"重复项ID","covered_by_claim_ids":["保留项ID"],"fully_covered":true,"conditions_preserved":true}]。'
    )
    return text + SEMANTIC_CONFIRMATION_RULES + FORMULA_PHASE_RULES


def answer_prompt() -> str:
    """Prompt for source-grounded answer generation."""
    text = (
        '按已读取的Neo4j知识回答原始问题。优先使用statements中的完整Statement，引用其S1、S2等短编号；程序负责沿Statement已有支持关系定位Evidence原文。不要填写或复写真实statement_id、evidence_id或member_keys。'
        '只有Statement未覆盖或语义不足时，才使用evidence_fallbacks中已提供正文的E1等备用原文短编号；evidence_catalog只有目录信息，不能据此引用未提供正文的Evidence。仍不足时说明缺口，不能编造。'
        'Statement中的对象、条件、例外及查询路径都须结合原意判断；上下文关联只是候选线索，不代表要求适用。'
        '不能读取本地原始材料，不能把检索得分或未读候选当作依据。'
        '按独立子问题回答，保留对象、动作、活动、阶段、条件、否定、数量、单位和适用边界。'
        'accepted_claims是上一轮保留的建议，不必重复生成；结合新增已读原文与review_feedback补充或修正所问内容，不能改变原问题的阶段。'
        + ANSWER_SCOPE_RULES +
        '检索返回的知识只是候选；先判断其对当前task的实际适用性，再选择实际支持建议的Statement短编号，可同时引用多个。'
        '允许用用户明确提供的数值及已引用原文的限值做直接比较、单位换算和简单四则运算，写明关键比较或算式；计算结果不必逐字出现在原文。numeric_comparisons为unknown只表示程序未完成自动比较，不代表清楚的原文与用户数值不可比较。参数、对象、单位和条件有实质歧义时保留未知，不把单项判断扩大为设备整体合格。'
        '面向业务用户只表述实际测量对象、部位、适用条件和下一步检查，不报告内部比较过程、程序判断、工具或字段状态。尚不能确定指标对应时说明需要核实的工程条件，不能把这种不确定改写为已确认合格或超限。'
        '可以依据资料作出判断和建议，区分来源事实与推断，结论的确定程度须符合依据。具体步骤和参数必须有来源支持；信息不足时说明边界，不增加用户未要求的内容，不发起追问。'
        '同对象同参数在明确重叠条件下确有矛盾时，并列双方要求及条件，不自动选择一方。先形成建议，再附精确来源；来源不完整时说明缺口。knowledge_items是检索方向，不要求每种类型均生成建议；没有某类资料不构成必须补答的缺口。建议中的必要条件和推断前提直接写入text，不额外附加通用“验证”检查清单。多个相互独立的建议必须分别生成claims，不能合并成一段；单个建议可以保持完整段落。只返回JSON：'
        '{"claims":[{"text":"单条事实或建议","basis":"source|inference|image",'
        '"task_id":"...","knowledge_item":"...","references":["S1","S2"]}],'
        '"gaps":[{"kind":"source_missing|user_info_needed|answer_incomplete|system_incomplete",'
        '"task_id":"...","references":[],"reason":"...","question":""}]}。'
    )
    return text + SEMANTIC_CONFIRMATION_RULES + FORMULA_PHASE_RULES


ANSWER_OPERATIONS_RULES = (
    '本次修改已有答案，只返回JSON {"operations":[{"op":"add|replace",'
    '"claim_id":"被替换的已有陈述ID，仅replace填写",'
    '"missing_item_id":"本次缺口ID，仅add填写",'
    '"claim":{"text":"单条完整建议","task_id":"当前任务ID",'
    '"basis":"source|inference|image","knowledge_item":"知识类型",'
    '"references":["S1或已提供正文的E1"]}}],"gaps":[]}。'
    '现有正确内容保持不动；新增只写尚未表达的必要含义并对应missing_items中的id。'
    '修正、扩充已有建议或用新依据替代旧的不确定表述时，使用replace并明确claim_id，'
    '完整保留该条仍然正确的条件和要求，不把改写后的同义建议作为add。'
    '被拒绝条目也可按其原ID替换。不得返回整份claims，不得重新列出无需修改的建议；无需修改时operations为空。'
    '独立核验的遗漏提示也可能误判：已有答案已表达相同含义时无需操作，不因文字不同重复补充。'
)


def repair_prompt() -> str:
    text = (
        '只修正上一轮被程序拒绝或明确缺失的陈述。优先选择Statement的S短编号，Statement不足时可选择evidence_fallbacks中已提供正文的E短编号，'
        '根据当前task和独立核验指出的问题修正适用性，必要前置准备可保留；保持用户明确作业或异常的范围，不补其他独立作业，不复述内部处理状态。不得新增无来源事实、不得重复已经通过的内容；修正不了则不提交该操作。'
        '不照抄被拒绝条目的真实来源ID，引用只能填写当前可用的S/E短编号，由程序展开支持关系。'
        + ANSWER_OPERATIONS_RULES
    )
    return text + SEMANTIC_CONFIRMATION_RULES + FORMULA_PHASE_RULES


def numeric_alignment_prompt() -> str:
    """Align an explicit user measurement with a limit in already-read sources."""
    return (
        '你只为已有的未知数值比较寻找来源限值，不回答问题，不补充工程常识。'
        '程序未识别某个单位不代表原文不可理解。完整保留原文单位符号和量纲，不把未知单位截成已知单位；'
        '程序不能换算时，仍提供原文对象、条件及限值给后续回答结合原文判断，不因程序unknown拒绝有依据的回答。'
        '只能使用输入中已读取的Evidence原文和成员；每项必须绑定实际evidence_id及其真实member_key。'
        '仅返回与用户已有数值字段同一对象、同一参数的比较。参数字段名必须逐字取自user_facts；'
        'target沿用user_facts中的target，未提供时使用task.object，不把参数或动作拼接进对象名称。'
        '对象、限值、比较词、单位和测量位置必须由所绑定的source_quote支持。'
        'source_quote和limit_quote必须是Evidence成员原文的连续摘录；不得拼造或改写引文。'
        '适用性须对照用户问题中的原句条件与来源条件判断，不得自行补全或推测；'
        '原问题条件或来源条件无法确定是否一致时，applicability填写unknown。'
        'user_condition_quote须为空或为用户问题中的连续原句；source_condition_quote须为空或为来源中的连续原句。'
        '不要求拆解与本次数值比较无关的其他条件。不得以同设备、同章节或相似参数替代对象/参数对应。'
        '只返回JSON：{"comparisons":[{"parameter":"已有用户参数字段名",'
        '"target":"来源明确的测量对象", "location":"明确测量位置或null",'
        '"value":"来源限值数字", "unit":"来源单位", "operator":"lt|le|gt|ge|eq|ne",'
        '"applicability":"applicable|not_applicable|unknown", "evidence_id":"...",'
        '"member_keys":["..."], "source_quote":"完整的对象、参数、限值及条件依据原文",'
        '"limit_quote":"包含比较词、限值和单位的原文连续片段",'
        '"source_condition_quote":"来源适用条件原文或空字符串",'
        '"user_condition_quote":"用户问题条件原句或空字符串"}]}。'
    )
