from turbine_kg.agent.prompts import (
    answer_prompt,
    answer_review_prompt,
    retrieval_prompt,
    understanding_prompt,
)


def test_prompts_use_the_single_generic_question_answering_contract():
    import inspect
    for function in (understanding_prompt,answer_prompt,answer_review_prompt):
        assert not inspect.signature(function).parameters
    assert '工程依据只取自Neo4j返回的Statement和Evidence' in retrieval_prompt('read_evidence')
    assert '现场数值不是知识库事实' in understanding_prompt()
    assert '具体步骤和参数必须有来源支持' in answer_prompt()


def test_answer_review_prompt_is_a_separate_answer_review_request():
    prompt = answer_review_prompt()
    assert "独立的回答核验请求" in prompt
    assert "只检查实质错误" in prompt
    assert "完整Evidence" in prompt
    assert "claim_verdicts" in prompt
    assert "关键前提和例外" in prompt


def test_advice_uses_engineering_limits_without_internal_status_or_scope_expansion():
    prompt=answer_prompt()
    assert "不报告内部比较过程、程序判断、工具或字段状态" in prompt
    assert "关联或同章出现不证明适用" in prompt
    assert "不自动展开无关内容" in prompt
    review=answer_review_prompt()
    assert '"complete":' not in review
    assert "不扩展用户未要求的目标" in review
    assert "missing_items只列已有原文明确提供" in review
    assert "不审查措辞风格" in review and "verified_claim_ids" in review


def test_overview_requires_relevant_branches_without_expanding_specific_problems():
    for prompt in (answer_prompt(),answer_review_prompt()):
        assert '总括询问' in prompt and '条件分支' in prompt
    assert '具体问题只回答所问判断及其必要依据' in answer_prompt()


def test_exceptions_and_stages_are_described_without_a_named_operation():
    for prompt in (answer_prompt(),answer_review_prompt()):
        assert '例外只改变原文明示的义务' in prompt
        assert '不同阶段的前提' in prompt


def test_prompts_do_not_embed_previous_test_questions_or_answers():
    from turbine_kg.agent.prompts import repair_prompt,numeric_alignment_prompt
    from turbine_kg.knowledge.statement_extraction import MINIMAL_EXTRACTION_SYSTEM
    prompts=[understanding_prompt(),retrieval_prompt('registered tools'),answer_prompt(),
             answer_review_prompt(),repair_prompt(),numeric_alignment_prompt(),MINIMAL_EXTRACTION_SYSTEM]
    for prompt in prompts:
        assert all(term not in prompt for term in ('除氧器','空冷','箱罐','风机','管束','通水',
            '试胀工作','试运前','免做一项试验','完工判据','60%','5.4.16','5.3.7'))



def test_repair_uses_explicit_operations_and_accepts_a_false_missing_item():
    from turbine_kg.agent.prompts import repair_prompt,ANSWER_OPERATIONS_RULES
    prompt=repair_prompt()
    assert 'operations' in prompt and 'claim_id' in prompt and 'missing_item_id' in prompt
    assert '无需修改时operations为空' in prompt
    assert '遗漏提示也可能误判' in prompt
    assert ANSWER_OPERATIONS_RULES in prompt
