import json
from copy import deepcopy

import pytest

from turbine_kg.knowledge.extract import extract
from turbine_kg.ontology.registry import OntologyRegistry


def views():
    return [{'evidence_id':eid,'kind':'text','context':{},'members':[
        {'member_key':eid+'-member','source_id':eid+'-source','role':'content','quote':eid+' requirement.',
         'locator':{'physical_page':page}}]} for eid,page in [('a',1),('b',2)]]


def fact(eid,member,text):
    return {'text':text,'type':'requirement','entities':[{'label':'Generic object','class':'Mention','role':'subject'}],
            'supports':[{'evidence_id':eid,'member_keys':[member]}]}


def test_minimal_statement_protocol_preserves_full_text_without_numeric_projection():
    class Model:
        def call(self,system,payload,budget):
            if system.startswith('独立核验'):
                raise AssertionError('最简抽取不应调用第二个模型核验')
            assert 'conditions、quantities、formulas全部省略' in system
            return {'statements':[fact('e0','e0m0','不合格管超过1%时，应逐根试验。')],
                    'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    result,coverage=extract(views()[:1],Model(),OntologyRegistry(),repair=True)
    assert len(result)==1 and coverage['evidence']['a']['status']=='complete'
    assert 'review_status' not in coverage['evidence']['a']
    assert not any(field in result[0] for field in ('conditions','quantities','formulas'))


def test_invalid_member_binding_marks_only_its_source_incomplete():
    class Model:
        def call(self,system,payload,budget):
            assert not system.startswith('独立核验')
            return {'statements':[fact('e0','e0m0','a requirement.'),fact('e1','missing','b requirement.')],
                    'evidence_status':[{'evidence_id':f'e{i}','status':'complete'} for i in range(2)]}
    items,coverage=extract(views(),Model(),OntologyRegistry())
    assert len(items)==1 and coverage['evidence']['a']['status']=='complete'
    assert coverage['evidence']['b']['status']=='incomplete'
    assert not coverage['rejected_by_evidence']['a']
    assert any('支持成员未知' in reason for reason in coverage['rejected_by_evidence']['b'])
    assert coverage['rejected_details'][0]['evidence_ids']==['b']
    assert coverage['rejected_details'][0]['stage']=='normalization'


def test_one_retry_uses_identical_prompt_and_fresh_budget_for_only_failed_source():
    class Model:
        def __init__(self):self.calls=[];self.budgets=[]
        def call(self,system,payload,budget):
            data=json.loads(payload);self.calls.append((system,data));self.budgets.append(budget)
            if len(self.calls)==1:
                return {'statements':[fact('e0','e0m0','a requirement.'),fact('e1','missing','Bad b.')],
                        'evidence_status':[{'evidence_id':f'e{i}','status':'complete'} for i in range(2)]}
            if len(self.calls)==2:
                assert system.startswith('你从技术资料的Evidence原文提取最简知识记录。')
                assert '上次核验诊断' in system
                assert 'Bad b.' not in system and 'Bad b.' not in payload
                assert 'b-member' not in system
                assert len(data)==1 and data[0]['members'][0]['quote']=='b requirement.'
                return {'statements':[fact('e0','e0m0','b requirement.')],'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    model=Model();items,coverage=extract(views(),model,OntologyRegistry(),repair=True,max_retries=1)
    assert len(model.calls)==2
    assert model.budgets[0] is not model.budgets[1]
    assert len(items)==2 and coverage['rejected']==[] and coverage['repair_attempted']
    assert all(status['status']=='complete' for status in coverage['evidence'].values())


def test_invalid_repair_stays_incomplete_and_stops_at_retry_limit():
    class Model:
        def __init__(self):self.calls=0
        def call(self,system,payload,budget):
            self.calls+=1
            assert not system.startswith('独立核验')
            return {'statements':[fact('e0','missing','Bad.')],'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    model=Model();items,coverage=extract(views()[:1],model,OntologyRegistry(),repair=True,max_retries=1)
    assert items==[] and model.calls==2
    assert coverage['evidence']['a']['status']=='incomplete' and coverage['rejected_by_evidence']['a']


def test_generation_timeout_retries_identical_request_once_with_fresh_deadline():
    class Model:
        def __init__(self):self.requests=[]
        def call(self,system,payload,budget):
            assert not system.startswith('独立核验')
            self.requests.append((system,payload,budget))
            if len(self.requests)==1:
                budget.started-=budget.seconds+1
                raise TimeoutError('request expired')
            assert budget.remaining()>0
            return {'statements':[fact('e0','e0m0','a requirement.')],
                'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    model=Model();items,coverage=extract(views()[:1],model,OntologyRegistry(),repair=True,max_retries=1,seconds=20)
    assert len(items)==1 and coverage['evidence']['a']['status']=='complete'
    assert coverage['repair_attempted'] and len(model.requests)==2
    assert model.requests[0][:2]==model.requests[1][:2]
    assert model.requests[0][2] is not model.requests[1][2]


def test_generation_failure_is_sent_at_most_twice():
    class Model:
        calls=0
        def call(self,system,payload,budget):
            self.calls+=1
            raise TimeoutError('still unavailable')
    model=Model()
    with pytest.raises(TimeoutError):extract(views()[:1],model,OntologyRegistry(),repair=True,max_retries=1)
    assert model.calls==2


def test_table_completeness_covers_native_row_without_reextracting_attached_body_reference():
    view={'evidence_id':'table','kind':'table','context':{},'members':[
        {'member_key':'header','source_id':'h','role':'header','quote':'允许偏差（mm）'},
        {'member_key':'label','source_id':'l','role':'row_label','quote':'部件同轴度'},
        {'member_key':'value','source_id':'v','role':'value','quote':'0.8'},
        {'member_key':'body','source_id':'b','role':'explicit_table_reference','quote':'施工应符合表中规定。'}]}
    class Model:
        def call(self,system,payload,budget):
            assert not system.startswith('独立核验')
            assert len(json.loads(payload)[0]['members'])==4
            item=fact('e0','e0m1','部件同轴度允许偏差为0.8 mm。')
            item['supports'][0]['member_keys']=['e0m0','e0m1','e0m2']
            return {'statements':[item],
                    'evidence_status':[{'evidence_id':'e0','status':'complete','reason':'已覆盖'}]}
    items,coverage=extract([view],Model(),OntologyRegistry(),repair=True,max_retries=1)
    assert len(items)==1 and not coverage['rejected']
    assert coverage['evidence']['table']['status']=='complete'


def test_missing_generation_coverage_status_cannot_pass_completeness_gate():
    class Model:
        calls=0
        def call(self,system,payload,budget):
            self.calls+=1
            assert not system.startswith('独立核验')
            return {'statements':[fact('e0','e0m0','a requirement.')]}
    model=Model();items,coverage=extract(views()[:1],model,OntologyRegistry(),repair=True,max_retries=1,seconds=20)
    assert len(items)==1 and coverage['evidence']['a']['status']=='incomplete'
    assert any('原生来源成员未被有效Statement支持' in reason
               for reason in coverage['rejected_by_evidence']['a'])
    assert coverage['repair_attempted'] and model.calls==2


def test_missing_native_child_requires_source_bound_repair():
    view=views()[0];view['members'].append({'member_key':'child-two','source_id':'second-source',
        'role':'content','quote':'2. Second independent requirement.','locator':{'physical_page':1}})
    class Model:
        def __init__(self):self.calls=0
        def call(self,system,payload,budget):
            self.calls+=1
            if self.calls==1:return {'statements':[fact('e0','e0m0','a requirement.')],
                'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
            if self.calls==2:
                assert '原生来源成员未被有效Statement支持' in system
                assert 'child-two' not in system
                return {'statements':[fact('e0','e0m0','a requirement.'),fact('e0','e0m1','Second independent requirement.')],
                        'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    model=Model();items,coverage=extract([view],model,OntologyRegistry(),repair=True,max_retries=1)
    assert len(items)==2 and model.calls==2
    assert coverage['evidence']['a']['status']=='complete' and coverage['rejected']==[]


def test_failed_repair_does_not_add_a_second_statement():
    class Model:
        def __init__(self):self.calls=0
        def call(self,system,payload,budget):
            self.calls+=1
            assert not system.startswith('独立核验')
            text='a requirement.' if self.calls==1 else 'A paraphrase of the same obligation.'
            item=fact('e0','e0m0' if self.calls==1 else 'missing',text)
            return {'statements':[item],'evidence_status':[{'evidence_id':'e0',
                'status':'incomplete' if self.calls==1 else 'complete'}]}
    model=Model();items,coverage=extract(views()[:1],model,OntologyRegistry(),repair=True,max_retries=1)
    assert model.calls==2 and len(items)==1 and items[0]['text']=='a requirement.'
    assert coverage['evidence']['a']['status']=='incomplete'
    assert any(detail['stage']=='repair_component' for detail in coverage['rejected_details'])


@pytest.mark.parametrize('whole_component_complete',[False,True])
def test_cross_evidence_component_repair_replaces_atomically(whole_component_complete):
    class Model:
        def __init__(self):self.calls=0
        def call(self,system,payload,budget):
            self.calls+=1
            assert not system.startswith('独立核验')
            item=fact('e0','e0m0','Original joint obligation.' if self.calls==1 else 'Corrected joint obligation.')
            item['supports'].append({'evidence_id':'e1','member_keys':['e1m0']})
            return {'statements':[item],'evidence_status':[{'evidence_id':f'e{i}',
                'status':'complete' if self.calls==2 and (i==0 or whole_component_complete) else 'incomplete'}
                for i in range(2)]}
    model=Model();items,coverage=extract(views(),model,OntologyRegistry(),repair=True,max_retries=1)
    assert model.calls==2 and len(items)==1
    assert items[0]['text']==('Corrected joint obligation.' if whole_component_complete else 'Original joint obligation.')
    assert all(row['status']==('complete' if whole_component_complete else 'incomplete') for row in coverage['evidence'].values())


@pytest.mark.parametrize('missing_own_obligation',[False,True])
def test_reference_status_is_preserved_while_generation_coverage_is_checked(missing_own_obligation):
    view=views()[0];view['context']['reference_resolution']=[{'kind':'clause','number':'2.3.4','status':'deferred','target_evidence_ids':[]}]
    class Model:
        def call(self,system,payload,budget):
            assert not system.startswith('独立核验')
            return {'statements':[fact('e0','e0m0','a requirement.')],
                'evidence_status':[{'evidence_id':'e0','status':'incomplete' if missing_own_obligation else 'complete',
                    'reason':'Referenced clause is outside the current range'}]}
    items,coverage=extract([view],Model(),OntologyRegistry(),repair=True,max_retries=1,seconds=20)
    assert len(items)==1 and coverage['evidence']['a']['status']==('incomplete' if missing_own_obligation else 'complete')
    assert view['context']['reference_resolution'][0]['status']=='deferred'
    assert bool(coverage['rejected_by_evidence']['a'])==missing_own_obligation
