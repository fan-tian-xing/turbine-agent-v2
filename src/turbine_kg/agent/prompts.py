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
    '公式核验区分对象状态、输入变量测量状态和规定试算/检查/操作时点；仍须核对数量对象、参数、'
    '变量定义、材料、工况和公式条件。方法说明不代表现场已执行，未算出具体数值不等于公式未知。'
)
SKILL_EXECUTION_RULES = (
    '只执行已选流程指引中与当前task_ids和knowledge_items对应的步骤；流程指引规定方法，不提供工程事实，'
    '真实原文、精确支持和本体注册合同才是事实约束。'
)
ANSWER_SCOPE_RULES = (
    '以用户明确提出的对象及作业或异常为回答范围。设备关联、同章出现，或加上“若涉及某部件”的条件，'
    '均不足以把另一项独立作业纳入本题。同一适用父条款内的部件分项应保留；跨条款内容须直接约束这项作业、'
    '构成必要前提，或针对该异常提供检查和处置依据。明确引用仍需核对发起条款的适用条件，不能自动展开整节其他作业。'
    '判断分项时先读所属条款的引导句：分项提及所问设备，不等于该条款的主要部件或作业就是本题；'
    '若引导句限定了另一项独立作业，只有用户明确询问该作业或来源证明它是当前作业必要前提时才采用。'
    '必要的前置准备可以支持当前操作；不主动扩展到后续验收和试运行，问题明确涉及其他阶段时按问题处理。'
)


def understanding_prompt(*, use_skills: bool = False) -> str:
    """Build the problem-understanding prompt for the first model request."""
    skill_fields = ''
    skill_rule = ''
    if use_skills:
        skill_fields = '"skill_ids":["..."],'
        skill_rule = '若输入提供了可用流程指引，只选择实际需要的指引和知识类型，不展开无关能力。'
    return (
        '理解原始问题，识别回答所需的对象、活动、阶段、意图、知识类型、已知条件和现场数值，按用户实际提出的独立问题分解任务；knowledge_items只是查找知识的方向，同一现场问题不得按知识类型拆成多个必答子问题。当前只回答本次问题，不发起追问；缺少现场条件时保留未知并在建议中说明适用前提。'
        '只保留用户明确提出的对象、参数、动作和阶段；不得凭行业经验补充对象、类别或现场事实。subquestion忠实保留原问题，不把一般的处置建议请求扩写成必须提供具体返修工艺、操作步骤或工艺参数。'
        '开放枚举先保留覆盖原问题的任务，读取来源后再按原文细化。对象使用可复用的设备、部件或程序名称；'
        '设备、部件、材料、型式、活动和阶段的限定必须保留在subquestion、query_terms、activity和phase中。'
        'activity只能填写用户明确询问或限定的已注册活动，phase保留用户原句中的阶段限定；没有明确限定时填写null，不能由设备名称或条款内容反推。'
        '单位、测量部位和数值只能来自用户原句，不能从资料或常识补齐；用户现场数值不是知识库事实。'
        '用户明确给出数值时必须记录到本次会话的fact_updates（仅内存，不写入知识图谱），value使用对象{"value":"数值","unit":"单位","target":"对象"}；比例的unit须保留用户明确给出的比较基准，在“倍”后直接接原句的基准名称，不添加加号或其他连接符，没有基准时不补。target填写该任务的设备或部件对象，不拼接参数或动作。非数值条件的value可用字符串。'
        + skill_rule
        + f'只返回JSON {{"objects":["对象"],{skill_fields}'
          '"knowledge_items":["知识类型"],"object_tasks":[{"object":"对象",'
          '"subquestion":"该对象要解决的子问题","activity":"已明确的活动或null",'
          '"phase":"已明确的阶段或null","intent":"lookup|diagnosis|comparison|procedure|other",'
          '"query_terms":["实际参数或动作"],'
          + (('"skill_ids":["..."],') if use_skills else '')
          + '"knowledge_items":["知识类型"],"requires_site_inputs":false,'
          '"calculation_required":false}],"source_kinds":["text|table|image"],'
          '"visual_required":false,"fact_updates":[{"object":"对象","key":"条件名",'
          '"parameter_id":"已有参数身份或null","value":"明确值",'
          '"source_text":"当前用户原句的连续摘录"}],"clarifications":[]}}。'
    )


def invalid_skill_repair_prompt() -> str:
    return '上一轮流程指引选择无效，只修正流程指引ID和任务知识类型；不要改写用户问题或补造工程事实。'


def retrieval_prompt(tool_description: str, *, use_skills: bool = False) -> str:
    text = (
        '选择下一批必要的来源工具。只返回JSON '
        '{"calls":[{"tool":"...","parameters":{}}],"stop":false}。'
        f'可用工具：{tool_description}。只能使用注册的relation_id。'
        '同时使用本体关系导航和问题中的对象、活动、阶段、参数搜索Evidence；命中后必须read_evidence读取Neo4j中的完整来源，'
        '搜索得分不能作为依据。沿已读来源的明确引用补查，优先读取尚未读取的候选；没有进展或来源已经足够时stop。'
        '候选召回不等于适用；优先读取直接相关要求及必要准备、明确引用，再读取能补充当前问题的部件要求。不能仅因阶段不同排除必要的前置准备；没有必要时不扩展到后续验收、试运。'
        '不能读取本地PDF、OCR或图片，不能凭图题替代像素读取。'
    )
    if use_skills:
        text += '只在已选流程指引明确要求时使用其流程，不把流程指引当作工程事实。'
    return text


def answer_review_prompt(*, use_skills: bool = False) -> str:
    """Independent advice review; source gathering itself needs no model gate."""
    text = (
        '这是一次独立的回答核验请求，不是最终回答，也不是重复生成建议。'
        '逐条对照已读取的完整Evidence及必要父项、表头和引用，核对建议是否有依据、是否适用于当前问题、是否保留关键条件、否定和数量。'
        '允许同一工程含义的表达，不要求逐字匹配。grounding_context说明来源成员的主体及关系：subject/quantity_target是主要对象，related仅表示提及，不能自动视为要求适用于该对象；同条款分项仍须结合父项。它不能替代Evidence。'
        + ANSWER_SCOPE_RULES +
        '可以依据资料提出检查、判断和后续处理建议，推断须明确；具体修复工艺必须有资料支持。缺失现场条件不应补造，也不要求追问。'
        '核对同对象、同参数在重叠条件下是否有实际矛盾；不同条件或可同时满足的限值不算冲突。真实冲突应在建议中并列双方要求和条件，不自动按年份、权威或严格程度取舍；遗漏时以missing_items指出精确双方来源。节点ID、类字段、内部状态和成员存在性由程序检查，不要求模型审计。用户事实中的现场数值可以复述，不要求规范原文有相同数字，但不得把用户数值当作规范阈值。'
        '现场处置问题只检查会改变当前建议的关键遗漏，或用户明确提出却未回答的子问题。missing_items须说明省略此项会使哪条当前建议不成立或无法执行，或哪个明确子问题未回答，并绑定资料依据；仅“相关”“同条款还有”或设备具有该部件都不算遗漏。不穷举命中条款，不按knowledge_items逐类追求齐全。'
        '已有依据支持、可执行且界限明确的检查和后续处理建议，可以充分回应当前处置问题；一般的处理建议请求不等于要求具体返修工艺。是否明确要求工艺步骤或操作参数，以原始问题为准，不能依据任务改写增加要求。missing_items只能列已有资料明确提供、回答尚未采用且对本题必要的要求；资料根本没有的工艺或尚未知的现场条件只能说明资料边界，不能作为补答任务要求模型生成。用户明确要求具体工艺但来源没有时，应指出该请求缺少依据，仍不能编造。现场信息尚待确认时，可以给条件化建议，不要求先把现场所有信息补齐。'
        '同时检查当前建议与gaps是否一致；已有可靠建议解决的旧模型缺口可标resolved_gap_ids。允许以用户明确数值和已引用限值进行单位换算、直接比较及简单四则运算，核对计算和条件，不要求结果也出现在原文；程序比较unknown不是单独拒绝理由。来源或参数确有歧义时保留限制，不把局部检查扩大为整体合格。检索尚未完成不能宣称已查全。'
        '只返回JSON {"tasks":[{"task_id":"...",'
        '"claim_verdicts":[{"claim_id":"...","supported":true,"applicable":true,'
        '"conditions_preserved":true,"reason":"简短原因"}],'
        '"missing_items":[{"reason":"遗漏如何直接影响原问题的建议","supports":[{"evidence_id":"...","member_keys":["..."]}]}],'
        '"resolved_gap_ids":["当前建议已经解决的模型缺口id"],'
        '"redundant_claims":[{"claim_id":"重复建议id","covered_by_claim_ids":["保留建议id"],"fully_covered":true,"conditions_preserved":true}]}]}。不能因为引用同一Evidence就批准未引用成员或其他工程含义。'
    )
    if use_skills:text += SKILL_EXECUTION_RULES
    return text + SEMANTIC_CONFIRMATION_RULES + FORMULA_PHASE_RULES


def answer_prompt(*, use_skills: bool = False) -> str:
    """Prompt for source-grounded answer generation."""
    text = (
        '按已读取的Neo4j来源回答原始问题。以payload中Evidence.text的完整原文和程序计算结果为依据；members用于精确定位，成员边界不等于独立句子，引用分项时须带齐必要引导句、续文和条件。grounding_context仅说明来源成员的主体及查询关系，不是另一份工程事实；'
        '不能读取本地原始材料，不能把检索得分或未读候选当作依据。'
        '按独立子问题回答，保留对象、动作、活动、阶段、条件、否定、数量、单位和适用边界。'
        + ANSWER_SCOPE_RULES +
        '检索返回的来源只是候选；先判断其对当前task的实际适用性，再选择精确的原文成员支持建议。'
        '现场问题围绕当下检查、判断和处理组织建议，不用“顺带提醒”展开后续独立作业；同一Evidence中的其他事项可以不回答。'
        '允许用用户明确提供的数值及已引用原文的限值做直接比较、单位换算和简单四则运算，写明关键比较或算式；计算结果不必逐字出现在原文。numeric_comparisons为unknown只表示程序未完成自动比较，不代表清楚的原文与用户数值不可比较。参数、对象、单位和条件有实质歧义时保留未知，不把单项判断扩大为设备整体合格。'
        '面向业务用户只表述实际测量对象、部位、适用条件和下一步检查，不报告内部比较过程、程序判断、工具或字段状态。尚不能确定指标对应时说明需要核实的工程条件，不能把这种不确定改写为已确认合格或超限。'
        '可以依据资料提出检查、判断和后续处理建议，并明确哪些属于推断；具体修复工艺必须有来源支持。没有工艺资料时，给出能够支持的检查和后续处理建议并说明界限，不把未请求的具体工艺当作必须补答项。原因只能按来源的确定程度表述。缺少现场条件时说明判断边界，不发起追问。'
        '同对象同参数在明确重叠条件下确有矛盾时，并列双方要求及条件，不自动选择一方。先形成建议，再附精确来源；来源不完整时说明缺口。knowledge_items是检索方向，不要求每种类型均生成建议；没有某类资料不构成必须补答的缺口。建议中的必要条件和推断前提直接写入text，不额外附加通用“验证”检查清单。多个相互独立的建议必须分别生成claims，不能合并成一段；单个建议可以保持完整段落。只返回JSON：'
        '{"claims":[{"text":"单条事实或建议","basis":"source|inference|image",'
        '"task_id":"...","knowledge_item":"...","supports":[{"evidence_id":"...",'
        '"member_keys":["..."]}]}],'
        '"gaps":[{"kind":"source_missing|user_info_needed|answer_incomplete|system_incomplete",'
        '"task_id":"...","supports":[],"reason":"...","question":""}]}。'
    )
    if use_skills:
        text += '逐项遵循已选流程指引，但流程指引不构成工程事实。'
    return text + SEMANTIC_CONFIRMATION_RULES + FORMULA_PHASE_RULES


def repair_prompt(*, use_skills: bool = False) -> str:
    text = (
        '只修正上一轮被程序拒绝或明确缺失的来源绑定陈述。逐条使用已读Evidence成员，'
        '根据当前task和独立核验指出的问题修正适用性，必要前置准备可保留；保持用户明确作业或异常的范围，不补其他独立作业，不复述内部处理状态。不得新增无来源事实、不得重复已经通过的内容；修正不了就删除。只返回与既有task_id对应的JSON '
        '{"claims":[...]}，保留精确的evidence_id和member_keys。'
    )
    if use_skills:
        text += '遵循已选流程指引，但不把指引当作工程事实。'
    return text + SEMANTIC_CONFIRMATION_RULES + FORMULA_PHASE_RULES


def numeric_alignment_prompt() -> str:
    """Align an explicit user measurement with a limit in already-read sources."""
    return (
        '你只为已有的未知数值比较寻找来源限值，不回答问题，不补充工程常识。'
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
