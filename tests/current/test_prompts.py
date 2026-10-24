from turbine_kg.agent.prompts import (
    answer_prompt,
    answer_review_prompt,
    retrieval_prompt,
    understanding_prompt,
)


def test_disabled_skill_is_absent_from_question_answering_prompts():
    prompts = [
        understanding_prompt(),
        retrieval_prompt("read_evidence"),
        answer_review_prompt(),
        answer_prompt(),
    ]
    assert all("Skill" not in prompt for prompt in prompts)


def test_answer_review_prompt_is_a_separate_answer_review_request():
    prompt = answer_review_prompt()
    assert "独立的回答核验请求" in prompt
    assert "不是最终回答" in prompt
    assert "grounding_context说明来源成员的主体及关系" in prompt
    assert "claim_verdicts" in prompt
    assert "必要的前置准备" in prompt


def test_enabled_skill_is_added_only_when_explicitly_enabled():
    assert "流程指引" not in understanding_prompt()
    assert "流程指引" in understanding_prompt(use_skills=True)


def test_advice_uses_engineering_limits_without_internal_status_or_scope_expansion():
    prompt=answer_prompt()
    assert "不报告内部比较过程、程序判断、工具或字段状态" in prompt
    assert "设备关联、同章出现" in prompt
    assert "不能自动展开整节其他作业" in prompt
    review=answer_review_prompt()
    assert '"complete":' not in review
    assert "一般的处理建议请求不等于要求具体返修工艺" in review
    assert "missing_items只能列已有资料明确提供" in review
    assert "哪条当前建议不成立或无法执行" in review


def test_overview_requires_relevant_branches_without_expanding_specific_problems():
    for prompt in (answer_prompt(),answer_review_prompt()):
        assert '总括询问' in prompt and '条件分支' in prompt
        assert '具体故障、现场数值或单一指标' in prompt
    assert '缺少其中一项即属于漏答' in answer_review_prompt()
    assert '不要求它使已有正确建议失效' in answer_review_prompt()
