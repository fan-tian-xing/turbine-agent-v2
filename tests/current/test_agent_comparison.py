"""Acceptance coverage and source checks without any external model requests."""
from types import SimpleNamespace
import json
from pathlib import Path
import pytest
from turbine_kg.agent.runner import AnswerResult,Session,plan_tasks,task_statement_applicability
from turbine_kg.ontology.registry import OntologyRegistry
from turbine_kg.runtime import Snapshot
import check_answers as comparison


def test_acceptance_cases_cover_real_corpus_and_requested_behaviors():
    config=comparison.load_cases();cases=config['cases']
    assert len(cases)>=12 and sum(len(c['turns']) for c in cases)>len(cases)
    tags={tag for c in cases for tag in c['tags']}
    assert {'prose','complex_table','image','cross_page','anomaly','measures','multiple_objects','correction','manual','auxiliary_book','multiple_sources','scope_difference','unknown_applicability','no_false_conflict'}<=tags
    assert next(c for c in cases if c['id']=='auxiliary-horizontal-pump')['reference_locations'][0]['physical_pages']==[408]
    initial=[case for case in cases if case.get('phase','initial')=='initial']
    scope=json.loads((comparison.PROJECT_ROOT/'config/processing_scopes.json').read_text(encoding='utf-8'))[0]
    first,last=scope['physical_pages']
    assert initial and all(len(case['turns'])==1 for case in initial)
    assert all(first<=page<=last for case in initial for ref in case['reference_locations'] for page in ref['physical_pages'])
    assert {'quantity','anomaly','citation_selection','activity_scope'} <= {tag for case in initial for tag in case['tags']}


def test_generic_can_plan_requirements_and_checks():
    registry=OntologyRegistry()
    tasks,facts=plan_tasks({'objects':['卸扣'],'knowledge_items':['requirement','parameter','inspection']},Session(),registry)
    assert len(tasks)==1
    assert tasks[0]['knowledge_items']==['requirement','parameter','inspection']
    assert all(t['required'] for t in tasks)
    assert 'has_requirement' in tasks[0]['relation_ids']
    assert 'has_anomaly' not in tasks[0]['relation_ids']


@pytest.mark.parametrize('objects',[
    ['凝汽器','热交换器','除氧器'],['对象甲','对象乙','对象丙'],
])
def test_comparison_objects_get_independent_tasks_without_changing_intent(objects):
    registry=OntologyRegistry();question='比较这些对象的要求'
    spec={'object':'、'.join(objects),'subquestion':question,'knowledge_items':['requirement']}
    tasks,_=plan_tasks({'objects':objects,'object_tasks':[spec]},Session(),registry,question=question)
    assert [t['object'] for t in tasks]==objects
    assert all(t['subquestion']==question and t['relation_ids']==registry.navigation(['requirement']) for t in tasks)


def test_compound_object_is_not_split_and_diagnostic_relation_selection_is_preserved():
    registry=OntologyRegistry()
    tasks,_=plan_tasks({'objects':['水箱','排液管'],'object_tasks':[{
        'object':'水箱排液管','knowledge_items':['cause','anomaly']}]},Session(),registry)
    assert len(tasks)==1 and tasks[0]['object']=='水箱排液管'
    assert 'has_requirement' not in tasks[0]['relation_ids']


def test_mentioned_equipment_does_not_change_statement_primary_object():
    registry=OntologyRegistry()
    task={'object':'除氧器','activity':'安装','phase':None}
    safety_valve={'entities':[
        {'label':'承压容器安全阀','class':'Mention','role':'subject'},
        {'label':'高压除氧器','class':'Mention','role':'related'},
    ],'text':'高压除氧器的安全阀应为全启式。'}
    deaerator={'entities':[{'label':'除氧器','class':'Mention','role':'subject'}],
               'text':'除氧器安装应符合相关规定。'}
    # Mentioning the equipment does not establish applicability, but the
    # candidate remains available for the model to judge in its full context.
    assert task_statement_applicability(safety_valve,task,registry)=='unknown'
    assert task_statement_applicability(deaerator,task,registry)!='not_applicable'


def result():
    cite={'id':'c','evidence_id':'e','member_key':'m','title':'DL5190.3','document_id':'g','revision':'r','quote':'原始条文','locator':{'physical_page':113}}
    return AnswerResult('合格建议','partial',[{'text':'合格建议'}],[cite],[],{'task_coverage':[{'object':'起重机','knowledge_item':'requirement','coverage':'evidence_found'}],'tools':[],'errors':[],'seconds':1,'calls':2,'rounds':1})


class Assembler:
    def read(self,eid,snapshot,budget=None):
        assert eid=='e' and snapshot.document_ids==('g',)
        return {'revision_id':'r','kind':'table','members':[{'member_key':'m','quote':'原始条文','locator':{'physical_page':113}}]}


def test_metrics_share_denominator_and_verify_actual_original_quote():
    case={'knowledge_items':['requirement','parameter'],'reference_locations':[{'title_contains':'DL5190.3','physical_pages':[112,113]}],'required_source_kinds':['table'],'require_pixels':True}
    answer=result();metrics=comparison.metrics(case,answer,Assembler(),Snapshot(('g',),'o'),Session())
    assert metrics['citation_integrity_rate']==1 and metrics['coverage_ratio']==.5
    assert metrics['reference_location_hit'] and not metrics['all_reference_pages_hit']
    assert metrics['expected_source_kind_hit'] and not metrics['required_pixels_met']
    answer.citations[0]['quote']='拼接或改写条文'
    assert comparison.metrics(case,answer,Assembler(),Snapshot(('g',),'o'),Session())['citation_integrity_rate']==0


def test_generic_combined_task_preserves_configured_item_coverage():
    answer=result()
    answer.search_summary['task_coverage']=[{'object':'起重机','knowledge_item':'requirement',
        'knowledge_items':['requirement','inspection'],'coverage':'evidence_found'}]
    measured=comparison.metrics({'knowledge_items':['requirement','inspection']},answer,Assembler(),Snapshot(('g',),'o'),Session())
    assert measured['coverage_satisfied']==measured['coverage_targets']==2
    assert measured['coverage_ratio']==1
    assert measured['question_complete'] is None


def test_list_mode_never_connects_database_or_model(monkeypatch,capsys):
    def forbidden(*a,**kw): raise AssertionError('unexpected external operation')
    monkeypatch.setattr(comparison,'KnowledgeStore',forbidden)
    monkeypatch.setattr(comparison,'ModelClient',forbidden)
    assert comparison.main(['--list','--case','hydrostatic-pressure-below-limit'])==0
    assert 'hydrostatic-pressure-below-limit' in capsys.readouterr().out


@pytest.mark.parametrize('phase',[None,'generality','all'])
def test_cli_phase_selects_only_authorized_acceptance_scope(monkeypatch,capsys,phase):
    def forbidden(*a,**kw):raise AssertionError('phase listing must stay offline')
    monkeypatch.setattr(comparison,'KnowledgeStore',forbidden)
    monkeypatch.setattr(comparison,'ModelClient',forbidden)
    args=['--list']+(['--phase',phase] if phase else [])
    assert comparison.main(args)==0
    selected=json.loads(capsys.readouterr().out)
    expected=[row for row in comparison.load_cases()['cases'] if phase=='all' or row.get('phase','initial')==(phase or 'initial')]
    assert selected['phase']==(phase or 'initial') and selected['cases']==expected
    if phase is None:
        assert all(row.get('phase','initial')=='initial' for row in selected['cases'])
        assert all(ref['title_contains']=='DL5190.3' for row in selected['cases'] for ref in row.get('reference_locations',[]))


def test_explicit_case_cannot_bypass_selected_phase(monkeypatch,capsys):
    def forbidden(*a,**kw):raise AssertionError('phase rejection must precede external operations')
    monkeypatch.setattr(comparison,'KnowledgeStore',forbidden)
    monkeypatch.setattr(comparison,'ModelClient',forbidden)
    with pytest.raises(SystemExit) as error:
        comparison.main(['--list','--case','body-installation-sequence'])
    assert error.value.code==2 and '--phase' in capsys.readouterr().err
    assert comparison.main(['--list','--phase','generality','--case','body-installation-sequence'])==0
    assert [row['id'] for row in json.loads(capsys.readouterr().out)['cases']]==['body-installation-sequence']


def test_acceptance_pins_snapshot_question_and_individual_turn_budget(monkeypatch,capsys):
    calls=[];reviews=[]
    class Model:
        providers=[['ignored','same_model','ignored',1]]
        def call(self,system,user,budget):
            reviews.append((json.loads(user)['question'],budget))
            return {'verdicts':[{'index':0,**{key:True for key in ('numbers_units','conditions_negation','modality','object_parameter','question_relevance')}}]}
    class Agent:
        def __init__(self,store,registry,model):self.model=model
        def answer(self,question,session,**kwargs):
            calls.append((question,id(session),kwargs['validation_snapshot'],kwargs['budget']))
            answer=result();answer.claims[0]['supports']=[{'evidence_id':'e','member_keys':['m']}]
            return answer
    monkeypatch.setattr(comparison,'Agent',Agent)
    monkeypatch.setattr(comparison,'fixed_model',lambda *a:Model())
    monkeypatch.setattr(comparison,'ContextAssembler',lambda *a:Assembler())
    snapshot=Snapshot(('g',),'o');case={'id':'case','turns':['第一问','第二问'],'knowledge_items':['requirement']}
    records=comparison.run(SimpleNamespace(settings=None),OntologyRegistry(),[case],snapshot)
    assert len(records)==len(calls)==2
    assert {c[0] for c in calls}=={'第一问','第二问'} and all(c[2] is snapshot for c in calls)
    assert len({id(c[3]) for c in calls})==len(calls)
    assert len(reviews)==len(calls) and all(review==(call[0],call[3]) for review,call in zip(reviews,calls))
    assert all(row['metrics']['question_complete'] is None for row in records)
    assert 'same_model' in capsys.readouterr().out


def test_semantic_scoring_counts_errors_and_preserves_unknown_dimensions():
    claim={'basis':'source','text':'间隙必须小于0.10 m','supports':[{'evidence_id':'e','member_keys':['m']}]}
    answer=result();answer.claims=[claim]
    view={'members':[{'member_key':'m','quote':'冷态时，间隙宜不大于0.10 mm。'}]}
    class Model:
        def call(self,system,user,budget):
            payload=json.loads(user)
            assert payload['claims'][0]['original_members'][0]['quote']==view['members'][0]['quote']
            assert payload['question']=='查询冷态间隙'
            return {'verdicts':[{'index':0,'numbers_units':False,'conditions_negation':False,'modality':False,'object_parameter':True,'question_relevance':None}]}
    from turbine_kg.runtime import Budget
    measured=comparison.semantic_metrics(answer,{'e':view},Model(),Budget(),'查询冷态间隙',Session())
    assert measured['numbers_units_errors']==measured['conditions_negation_errors']==measured['modality_errors']==1
    assert measured['question_relevance_errors'] is None and measured['semantic_unchecked']==1
    assert measured['semantic_review_status']=='partial'


def test_comparison_deadline_does_not_erase_valid_partial_answer():
    from turbine_kg.runtime import Budget,BudgetExceeded
    class ExpiredAssembler:
        def read(self,*args):raise BudgetExceeded('shared deadline reached')
    measured=comparison.metrics({'knowledge_items':['requirement']},result(),ExpiredAssembler(),Snapshot(('g',),'o'),Session(),budget=Budget())
    assert measured['status']=='partial' and measured['claims_kept']==1
    assert measured['citations_valid']==0 and len(measured['citations_unchecked'])==1
    assert not measured['invalid_citations'] and measured['citation_integrity_rate'] is None
    assert measured['question_complete'] is None and measured['question_completeness_status']=='unknown'


def test_common_item_hit_does_not_count_unanswered_subquestion_as_complete():
    from turbine_kg.runtime import Budget
    question='静态刚性要求是什么，厂家要求缺失时如何选表？'
    answer=result();answer.claims=[{'text':'使用表内允许值','supports':[{'evidence_id':'e','member_keys':['m']}]}]
    class SourceAssembler(Assembler):
        def read(self,*args):
            return {**super().read(*args),'title':'DL5190.3','context':{'table_title':'表12.1.15 起重机静态刚性要求','rows':[0,1,2]},
                    'members':[{'member_key':'m','quote':'原始条文','locator':{'physical_page':113}},
                               {'member_key':'h','quote':'轻、中、重级','role':'header','locator':{'physical_page':113}}]}
    calls=[];budget=Budget()
    class Model:
        def call(self,system,user,used_budget):
            calls.append(used_budget);payload=json.loads(user)
            source=payload['claims'][0]['original_evidence'][0]
            assert source['context']['table_title']=='表12.1.15 起重机静态刚性要求'
            assert source['members'][1]['quote']=='轻、中、重级'
            assert payload['answer']==answer.answer and 'gaps' in payload
            return {'verdicts':[{'index':0,**{key:True for key in ('numbers_units','conditions_negation','modality','object_parameter','question_relevance')}}],
                    'question_completeness':{'complete':False,'subquestions':[
                        {'question_quote':'静态刚性要求是什么','answered':True,'claim_indices':[0]},
                        {'question_quote':'厂家要求缺失时如何选表','answered':False,'claim_indices':[],'missing_information':'未解释制造厂要求优先及默认条件'}]}}
    measured=comparison.metrics({'knowledge_items':['requirement']},answer,SourceAssembler(),Snapshot(('g',),'o'),Session(),model=Model(),budget=budget,question=question)
    assert calls==[budget] and measured['semantic_review_status']=='complete'
    assert measured['coverage_ratio']==measured['common_knowledge_item_coverage_ratio']==1
    assert measured['question_complete'] is False and measured['question_completeness_status']=='incomplete'
    assert measured['question_missing_subquestions'][0]['question_quote']=='厂家要求缺失时如何选表'


def test_missing_or_unbound_completeness_verdict_remains_unknown():
    question='查询间隙和检查方法'
    reviews=[{},
             {'complete':True,'subquestions':[]},
             {'complete':True,'subquestions':[{'question_quote':'查询间隙','answered':True,'claim_indices':[]}]},
             {'complete':True,'subquestions':[{'question_quote':'查询间隙','answered':True,'claim_indices':[4]}]},
             {'complete':True,'subquestions':[{'question_quote':'模型新增的问题','answered':True,'claim_indices':[0]}]},
             {'complete':True,'subquestions':[{'question_quote':'查询间隙','answered':None,'claim_indices':[0]}]}]
    for review in reviews:
        measured=comparison.question_completeness({'question_completeness':review},question,1)
        assert measured['question_complete'] is None and measured['question_completeness_status']=='unknown'


def test_complete_question_requires_independently_supported_answer_claims():
    categories=('numbers_units','conditions_negation','modality','object_parameter','question_relevance')
    review={'complete':True,'subquestions':[{'question_quote':'查询间隙','answered':True,'claim_indices':[0]}]}
    for numeric_verdict in (True,False,None):
        verdict={'question_completeness':review,'verdicts':[{'index':0,**{key:True for key in categories},'numbers_units':numeric_verdict}]}
        measured=comparison.question_completeness(verdict,'查询间隙',1)
        assert measured['question_complete'] is (True if numeric_verdict is True else None)
    assert comparison.question_completeness({'question_completeness':review},'查询间隙',1)['question_complete'] is None


def image_answer():
    answer=result();answer.claims=[{'basis':'image','text':'间隙标注位于轮缘之间','supports':[{'evidence_id':'e','member_keys':['picture']}]}]
    answer.search_summary['tools']=[{'tool':'read_visual','results':[{'status':'read','description':{'observations':['不得当作独立来源']},'resource_read':True,'pixels_sent':True,'response_received':True}]}]
    member={'member_key':'picture','source_id':'original-picture','source_kind':'picture','quote':'','image':{'path':'original.png','content_hash':'verified-sha'}}
    return answer,{'e':{'kind':'image','context':{'figure_title':'实际图题'},'members':[member]}}


def image_verdict():
    return {'verdicts':[{'index':0,**{key:True for key in ('numbers_units','conditions_negation','modality','object_parameter','question_relevance')}}],
            'question_completeness':{'complete':True,'subquestions':[{'question_quote':'图内间隙在哪里','answered':True,'claim_indices':[0]}]}}


def test_independent_image_review_never_reopens_local_source_files(monkeypatch):
    from turbine_kg.runtime import Budget
    answer,views=image_answer();budget=Budget(images=1,calls=2)
    def forbidden(*args, **kwargs): raise AssertionError('query evaluation must not read files')
    monkeypatch.setattr(Path,'is_file',forbidden)
    monkeypatch.setattr(Path,'read_bytes',forbidden)
    calls=[]
    class Model:
        last_summary={}
        def call(self,system,user,used_budget,*,images=None):
            calls.append((used_budget,images));payload=json.loads(user)
            assert images is None and payload['review_images'][0]['status']=='unread'
            assert 'observations' not in user and '不得当作独立来源' not in user
            self.last_summary={'pixels_sent':True,'response_received':True}
            return image_verdict()
    measured=comparison.semantic_metrics(answer,views,Model(),budget,'图内间隙在哪里',Session())
    assert calls==[(budget,None)] and budget.images==1 and budget.calls==2
    assert measured['semantic_images_read']==0 and measured['semantic_image_review'][0]['status']=='unread'
    assert measured['semantic_review_status']=='partial' and measured['question_complete'] is None


@pytest.mark.parametrize('failure',['missing','hash','quota','spoofed_kind','transport','one_missing_member'])
def test_unverified_or_unread_review_picture_cannot_count_as_semantically_complete(monkeypatch,failure):
    from turbine_kg.runtime import Budget
    answer,views=image_answer();budget=Budget(images=4 if failure=='quota' else 1)
    monkeypatch.setattr(Path,'is_file',lambda path:failure!='missing' and str(path)!='missing.png')
    if failure=='spoofed_kind':views['e']['members'][0]['source_kind']='paragraph'
    if failure=='one_missing_member':
        views['e']['members'].append({'member_key':'missing-picture','source_id':'second-original','source_kind':'picture','quote':'','image':{'path':'missing.png','content_hash':'verified-sha'}})
        answer.claims[0]['supports'][0]['member_keys'].append('missing-picture')
    calls=[]
    class Model:
        last_summary={'pixels_sent':False,'response_received':False} if failure=='transport' else {'pixels_sent':True,'response_received':True}
        def call(self,system,user,used_budget,*,images=None):
            calls.append((used_budget,images));return image_verdict()
    measured=comparison.semantic_metrics(answer,views,Model(),budget,'图内间隙在哪里',Session())
    assert len(calls)==1 and calls[0][0] is budget and budget.images<=4
    assert measured['question_complete'] is None and measured['semantic_unchecked']==1
    assert measured['object_parameter_errors'] is None and measured['semantic_review_status']=='partial'
    if failure not in ('transport','one_missing_member'):assert calls[0][1] is None


def test_image_review_deduplicates_missing_graph_pixels_without_using_budget(monkeypatch):
    from turbine_kg.runtime import Budget
    def forbidden(*args, **kwargs): raise AssertionError('query evaluation must not read files')
    monkeypatch.setattr(Path,'is_file',forbidden)
    monkeypatch.setattr(Path,'read_bytes',forbidden)
    members=[{'member_key':str(i),'source_id':str(i),'source_kind':'picture','image':{'path':str(i)+'.png','content_hash':'verified-sha'}} for i in range(5)]
    payload=[{'index':0,'claim':{'basis':'image'},'original_members':members},
             {'index':1,'claim':{'basis':'image'},'original_members':[members[0]]}]
    budget=Budget(images=1)
    images,states,visual_claims=comparison.review_images(payload)
    assert images==[] and len(states)==5 and budget.images==1 and budget.calls==0
    assert states[0]['claim_indices']==[0,1] and visual_claims=={0,1}
    assert all('graph_pixels_unavailable' in state['reason'] for state in states)
