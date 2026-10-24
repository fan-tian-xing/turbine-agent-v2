"""Source-bound extraction must fail closed without adding unused semantic flags."""
import json

from turbine_kg.knowledge.extract import extract
from turbine_kg.ontology.registry import OntologyRegistry


def source():
    return [{'evidence_id':'source','kind':'text','context':{},'members':[
        {'member_key':'body','source_id':'paragraph','source_kind':'text','role':'content',
         'quote':'部件间隙应不大于0.10 mm。'}]}]


def statement(value=0.10):
    return {'text':'部件间隙应不大于0.10 mm。','type':'requirement',
        'entities':[{'label':'部件','class':'Mention','role':'subject'}],
        'quantities':[{'target':'部件','parameter':'间隙','value':value,'unit':'mm','operator':'le'}],
        'supports':[{'evidence_id':'e0','member_keys':['e0m0']}]}


def generated(row):
    return {'statements':[row],
            'evidence_status':[{'evidence_id':'e0','status':'complete','reason':'已抽取'}]}


def test_minimal_statement_needs_no_modal_flags():
    class Model:
        def call(self,system,payload,budget):
            assert not system.startswith('独立核验')
            row=statement();row.pop('quantities')
            return generated(row)
    items,coverage=extract(source(),Model(),OntologyRegistry(),repair=True)
    assert len(items)==1 and coverage['evidence']['source']['status']=='complete'
    assert not {'modality','negation','possibility'} & items[0].keys()
    assert coverage['attempt_count']==1


def test_model_without_profile_uses_the_same_minimal_protocol():
    class Model:
        def call(self,system,payload,budget):
            assert system.startswith('你从技术资料的Evidence原文提取最简知识记录。')
            assert 'conditions、quantities、formulas全部省略' in system
            assert 'quantity.target' not in system
            row=statement();row.pop('quantities')
            return generated(row)
    items,coverage=extract(source(),Model(),OntologyRegistry(),repair=True)
    assert len(items)==1 and coverage['evidence']['source']['status']=='complete'


def test_scope_spelling_is_kept_as_a_source_backed_fact():
    class Model:
        def call(self,system,payload,budget):
            row=statement();row.pop('quantities');row['type']='scope'
            return generated(row)
    items,coverage=extract(source(),Model(),OntologyRegistry(),repair=True,max_retries=0)
    assert len(items)==1 and items[0]['type']=='fact'
    assert coverage['evidence']['source']['status']=='complete'


def test_second_retry_is_source_bound_and_only_success_is_accepted():
    class Model:
        attempts=0
        def call(self,system,payload,budget):
            assert not system.startswith('独立核验')
            self.attempts+=1
            assert json.loads(payload)[0]['members'][0]['quote']=='部件间隙应不大于0.10 mm。'
            if self.attempts>1:assert '上次核验诊断' in system
            row=statement();row.pop('quantities')
            if self.attempts<3:row['supports'][0]['member_keys']=['unknown']
            return generated(row)
    model=Model();items,coverage=extract(source(),model,OntologyRegistry(),repair=True)
    assert model.attempts==3 and len(items)==1
    assert coverage['attempt_count']==3 and coverage['rejected']==[]
    assert items[0]['text']=='部件间隙应不大于0.10 mm。'
    assert len(coverage['attempt_history'])==3


def test_model_cannot_declare_empty_answerable_evidence_complete():
    class Model:
        attempts=0
        def call(self,system,payload,budget):
            self.attempts+=1
            return {'statements':[],'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    model=Model();items,coverage=extract(source(),model,OntologyRegistry(),repair=True)
    assert not items and model.attempts==3
    assert coverage['evidence']['source']['status']=='incomplete'
    assert any('原生来源成员未被有效Statement支持' in reason for reason in coverage['rejected'])


def test_model_without_profile_cannot_add_structured_numeric_projection():
    class Model:
        def call(self,system,payload,budget):
            assert not system.startswith('独立核验')
            return generated(statement())
    items,coverage=extract(source(),Model(),OntologyRegistry(),repair=True,max_retries=0)
    assert not items and coverage['evidence']['source']['status']=='incomplete'
    assert 'Statement 不再保存' in coverage['rejected'][0]
