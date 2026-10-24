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


@pytest.mark.parametrize('unit', ['m', 'mm', 'MPa', 'kPa/h', 'kg', 'kN', '°C', '%', '米', '千克', '倍', 'bar', 'V', 'μm', 'rad', 'L/min'])
@pytest.mark.parametrize('space', ['', ' ', '\u3000'])
def test_cued_engineering_values_are_not_structural_references(unit, space):
    from turbine_kg.documents.ir import parse_references
    text = f'高度应符合设计要求且不宜低于3.5{space}{unit}；按5.8.2条执行。'
    assert [(ref['kind'], ref['number']) for ref in parse_references(text)] == [('clause', '5.8.2')]
    assert _references(text) == {('clause', '5.8.2')}


def test_spaced_unit_does_not_discard_a_source_bound_claim():
    from turbine_kg.agent.claims import binding_error
    reads = {'e': {'evidence_id': 'e', 'document_id': 'd', 'revision_id': 'r', 'title': '规范',
                   'context': {'clause_number': '5.8.2'}, 'members': [
                       {'member_key': 'm', 'quote': 'U形管高度应符合设计要求且不宜低于3.5m。',
                        'role': 'content', 'text_origin': 'native_text', 'locator': {}}]}}
    claim = {'basis': 'source', 'text': 'U形管高度应符合设计要求且不宜低于3.5 m。',
             'supports': [{'evidence_id': 'e', 'member_keys': ['m']}]}
    assert binding_error(claim, reads, {}) is None
    assert binding_error({**claim, 'text': claim['text'] + '按5.8.9条执行。'}, reads, {}) == '回答的表图或条款编号没有来源支持'


@pytest.mark.parametrize('text', ['这1条冷却管不得强行穿入。', '这2节短管应清理干净。', '第1条冷却管应清洁。', '这1条规定应遵守。'])
def test_count_classifiers_are_not_clause_or_section_ids(text):
    from turbine_kg.documents.ir import parse_references
    assert not _references(text)
    assert not parse_references(text)


def test_explicit_single_number_references_remain_distinct_from_counts():
    assert _references('按第1条规定执行，参照第2节的要求。')=={('clause','1'),('section','2')}


def test_document_filename_is_not_an_authoritative_identifier_gate():
    from turbine_kg.agent.claims import binding_error
    reads={'e':{'evidence_id':'e','title':'DL5190.3—2019规范', 'document_id':'d','revision_id':'r',
                'context':{},'members':[{'member_key':'m','quote':'冷却管不得延伸长度。',
                'role':'content','text_origin':'native_text','locator':{}}]}}
    claim={'text':'依据DL/T5190.3—2019，冷却管不得延伸长度。','basis':'source',
           'supports':[{'evidence_id':'e','member_keys':['m']}]}
    assert binding_error(claim,reads,{}) is None


def test_successfully_read_pixels_are_not_unconditionally_rejected_again():
    tools=SimpleNamespace(reads={'e':{'members':[{'member_key':'m','source_kind':'picture'}]}},
        visuals={'e':[{'member_key':'m','status':'read','resource_read':True,'pixels_sent':True,'response_received':True}]},
        summary=[],diagnostics=[])
    claim={'text':'图中标注孔径。','basis':'image','task_id':'t','supports':[{'evidence_id':'e','member_keys':['m']}]}
    accepted,rejected=validate([claim],tools,facts={},tasks=[{'id':'t'}])
    assert accepted and not rejected
    tools.visuals={}
    assert validate([claim],tools,facts={},tasks=[{'id':'t'}])[1]
