"""Regression cases from the PDF-derived field questions; no model calls."""
from types import SimpleNamespace

import pytest

from turbine_kg.agent.claims import _quantities, _references, validate
from turbine_kg.agent.runner import Session, plan_tasks
from turbine_kg.knowledge.quantities import canonical_unit, quantity_in_text, _unit
from turbine_kg.ontology.registry import OntologyRegistry


@pytest.mark.parametrize('text,expected', [
    ('偏差1.5毫米', {(1.5, 'mm')}),
    ('零下25摄氏度', {(-25.0, '°C')}),
    ('−25℃', {(-25.0, '°C')}),
    ('稳压15分钟', {(15.0, 'min')}),
    ('0.20千帕/小时', {(0.2, 'kPa/h')}),
    ('百分之5', {(5.0, '%')}),
    ('1纳米', set()),
    ('4mm/unknown', set()),
])
def test_common_quantities(text, expected):
    assert _quantities(text) == expected


def test_conversion_and_unknown_units():
    assert quantity_in_text(10, 'mm', '测得1厘米')
    assert quantity_in_text(-25, '℃', '气温零下25摄氏度')
    assert not quantity_in_text(10, 'm', '测得10毫米')
    assert _unit('毫米') == _unit('mm')
    assert canonical_unit('未知单位') == '未知单位'
    assert _unit('未知单位') is None
    assert quantity_in_text(0, '℃', '0摄氏度')


def test_fact_registration_preserves_negative_value_and_original_quote():
    quote = '环境温度零下25摄氏度'
    plan = {'objects': ['凝汽器'], 'knowledge_items': ['requirement'], 'fact_updates': [
        {'object': '凝汽器', 'key': '环境温度', 'source_text': quote,
         'value': {'value': -25, 'unit': '摄氏度', 'target': '凝汽器'}}]}
    session = Session()
    _, _, facts = plan_tasks(plan, session, OntologyRegistry(), [], use_skills=False,
                             question=quote, strict_facts=True)
    assert facts['凝汽器']['环境温度']['value'] == -25
    assert facts['凝汽器']['环境温度']['unit'] == '°C'
    assert session.fact_provenance['凝汽器']['环境温度']['source_text'] == quote


@pytest.mark.parametrize('text', [
    '10米柱距允许偏差±1mm，实测1.5mm，超限。',
    '现场−25℃低于−20℃，不得锤击；1050℃也不符合低于1000℃的要求。',
    '按设计压力2.0MPa的1.25倍计算，试验压力为2.5MPa。',
    '24小时总压降4.8kPa，4.8÷24＝0.20kPa/h；0.30kPa每小时超过该平均值。',
])
def test_field_values_and_arithmetic_are_not_mechanically_discarded(text):
    view = {'evidence_id': 'e', 'title': '规范', 'document_id': 'd', 'revision_id': 'r',
            'kind': 'text', 'context': {}, 'members': [
                {'member_key': 'm', 'source_id': 's', 'role': 'content',
                 'quote': '试验压力应为1.25倍设计压力，稳压15min。',
                 'text_origin': 'native_text', 'locator': {}}]}
    tools = SimpleNamespace(reads={'e': view}, visuals={}, summary=[], diagnostics=[])
    claim = {'text': text, 'basis': 'inference', 'task_id': 't',
             'supports': [{'evidence_id': 'e', 'member_keys': ['m']}]}
    accepted, rejected = validate([claim], tools, facts={}, tasks=[{'id': 't'}])
    assert len(accepted) == 1 and not rejected
    assert tools.diagnostics and all(row['blocking'] is False for row in tools.diagnostics)
    # Structural references remain enforced even in the relaxed numeric policy.
    bad = {**claim, 'supports': [{'evidence_id': 'e', 'member_keys': ['missing']}]}
    assert validate([bad], tools, facts={}, tasks=[{'id': 't'}])[1]


def test_multiplier_is_not_a_clause_reference():
    assert not _references('按1.25倍计算的试验压力为2.5 MPa')
    assert ('clause', '5.9.4') in _references('按照5.9.4的规定执行')
