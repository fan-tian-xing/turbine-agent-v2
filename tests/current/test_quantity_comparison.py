from types import SimpleNamespace
import pytest

from turbine_kg.knowledge.quantities import compare_quantity


REGISTRY=SimpleNamespace(canonical_label=lambda word:{'外壳':'壳体'}.get(word,word))


def check(value,*,operator='le',actual_unit='mm',unit='mm',limit=3,**kwargs):
    quantity={'target':'壳体','parameter':'偏差','value':limit,'unit':unit,
              'operator':operator,'location':None,**kwargs.pop('quantity',{})}
    actual={'value':value,'unit':actual_unit,**kwargs.pop('actual',{})}
    return compare_quantity(quantity,{'偏差':actual},object_label='外壳',registry=REGISTRY,
                            applicability=kwargs.pop('applicability','applicable'))


@pytest.mark.parametrize('value,operator,status',[(3,'le','satisfied'),(3,'lt','not_satisfied'),
    (3.000000000001,'le','not_satisfied'),(2,'ge','not_satisfied'),(3,'eq','satisfied')])
def test_exact_boundary_comparison_is_not_llm_or_float_tolerance(value,operator,status):
    assert check(value,operator=operator)['status']==status


def test_converts_known_units_without_modifying_measurement_or_limit():
    result=check(.003,actual_unit='m')
    assert result['status']=='satisfied' and float(result['normalized_actual'])==3
    assert result['actual']['value']==.003 and result['limit']==3
    assert check(1.2,actual_unit='倍',unit='1',limit=1.25,operator='ge')['status']=='not_satisfied'
    assert check(10,actual_unit='min',unit='s',limit=600,operator='ge')['status']=='satisfied'


def test_working_pressure_ratio_requires_the_same_explicit_baseline():
    assert check(1.20,actual_unit='倍工作压力',unit='倍工作压力',limit=1.25,operator='ge')['status']=='not_satisfied'
    assert check(1.30,actual_unit='倍工作压力',unit='倍工作压力',limit=1.25,operator='ge')['status']=='satisfied'
    assert check(1.30,actual_unit='倍额定压力',unit='倍工作压力',limit=1.25,operator='ge')['status']=='unknown'
    assert check(1.30,actual_unit='倍',unit='倍工作压力',limit=1.25,operator='ge')['status']=='unknown'


def test_explicit_user_quantity_string_is_split_without_model_target():
    from turbine_kg.agent.runner import plan_tasks, Session
    from turbine_kg.ontology.registry import OntologyRegistry
    question='压力容器试验采用1.20倍工作压力，是否合格？'
    plan={'objects':['压力容器'],'knowledge_items':['parameter'],
          'fact_updates':[{'object':'压力容器','key':'试验压力',
                           'value':'1.20倍工作压力','source_text':'试验采用1.20倍工作压力'}]}
    _,facts=plan_tasks(plan,Session(),OntologyRegistry(),
                         question=question,strict_facts=True)
    assert facts['压力容器']['试验压力']=={'value':'1.20','unit':'倍工作压力'}


def test_user_ratio_with_baseline_before_number_remains_a_session_fact():
    from turbine_kg.agent.runner import plan_tasks, Session
    from turbine_kg.ontology.registry import OntologyRegistry
    registry=OntologyRegistry()
    question='压力容器试验采用工作压力的1.20倍，是否合格？'
    session=Session()
    plan={'objects':['压力容器'],'knowledge_items':['parameter'],
          'fact_updates':[{'object':'压力容器','key':'试验压力',
                           'value':{'value':'1.20','unit':'倍工作压力'},
                           'source_text':'采用工作压力的1.20倍'}]}
    _,facts=plan_tasks(plan,session,registry,
                         question=question,strict_facts=True)
    assert facts['压力容器']['试验压力']=={'value':'1.20','unit':'倍工作压力'}
    plan['fact_updates'][0]['value']['unit']='倍额定压力'
    _,facts=plan_tasks(plan,Session(),registry,
                         question=question,strict_facts=True)
    assert not facts['压力容器']


def test_user_value_need_not_occur_in_cited_knowledge():
    from turbine_kg.agent.claims import binding_error
    view={'evidence_id':'e','title':'资料','document_id':'d','revision_id':'r',
          'kind':'text','context':{},'members':[{'member_key':'m','source_id':'src',
          'quote':'试验压力应不小于工作压力的1.25倍。','role':'content',
          'locator':{'physical_page':1},'text_origin':'native_text','attributes':{}}]}
    claim={'id':'c','text':'现场采用工作压力的1.20倍，要求为不小于工作压力的1.25倍。',
           'basis':'source','task_id':'t','supports':[{'evidence_id':'e','member_keys':['m']}]}
    assert binding_error(claim,{'e':view},{}) is None


@pytest.mark.parametrize('kwargs',[
    {'applicability':'unknown'}, {'actual_unit':None}, {'actual_unit':'MPa'},
    {'quantity':{'target':'另一部件'}}, {'quantity':{'parameter':None}},
    {'quantity':{'location':'端面'}}, {'value':float('nan')}, {'value':True},
    {'quantity':{'operator':'about'}}, {'quantity':{'value':'3～5'}},
])
def test_unknown_or_misaligned_measurements_do_not_yield_a_pass(kwargs):
    assert check(**{'value':3,**kwargs})['status']=='unknown'


def test_missing_location_only_blocks_when_source_specifies_location():
    assert check(3,quantity={'completeness':'incomplete','missing_fields':['location']})['status']=='satisfied'
    assert check(3,quantity={'location':'端面'},actual={'location':'端面'})['status']=='satisfied'
    assert check(3,quantity={'location':'端面'},actual={'location':'侧面'})['status']=='unknown'
    assert check(3,applicability='not_applicable')['status']=='not_applicable'


def test_parameter_and_target_must_be_aligned_not_merely_similar():
    quantity={'target':'壳体','parameter':'弯曲度','value':3,'unit':'mm/m','operator':'le'}
    result=compare_quantity(quantity,{'全长度弯曲度':{'value':2,'unit':'mm'}},
                            object_label='壳体',registry=REGISTRY,applicability='applicable')
    assert result['status']=='unknown'


def test_task_comparison_requires_read_evidence_and_known_source_conditions():
    from turbine_kg.agent.runner import task_quantity_checks
    tasks=[{'id':'t','object':'壳体'}]
    statement={'id':'s','_object':'壳体','supports':[{'evidence_id':'e','member_keys':['m']}],
               'quantities':[{'target':'壳体','parameter':'偏差','value':3,'unit':'mm','operator':'le'}]}
    facts={'壳体':{'偏差':{'value':4,'unit':'mm'}}}
    assert task_quantity_checks(tasks,{},facts,REGISTRY)[0]['status']=='unknown'
    result=task_quantity_checks(tasks,{'e':{}},facts,REGISTRY)[0]
    assert result['status']=='unknown' and 'statement_id' not in result
    assert tasks[0]['numeric_comparisons']==[result]
    statement['text']='制造厂无另行要求，壳体偏差不超过3mm。'
    assert task_quantity_checks(tasks,{'e':{}},facts,REGISTRY)[0]['status']=='unknown'


def test_evidence_only_limit_compares_one_explicit_measurement():
    from turbine_kg.agent.runner import task_quantity_checks
    task={'id':'t','object':'凝汽器壳体'}
    view={'kind':'text','context':{},'members':[{'member_key':'m','role':'content','quality':'good',
        'quote':'凝汽器壳体每米弯曲度不应大于3mm/m。'}]}
    facts={'凝汽器壳体':{'弯曲度':{'value':4,'unit':'mm/m','target':'凝汽器壳体'}}}
    result=task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]
    assert result['status']=='not_satisfied' and result['source']=='read_evidence'
    assert result['supports']==[{'evidence_id':'e','member_keys':['m']}]
    assert result['limit']=='3' and result['unit']=='mm/m'


def test_evidence_limit_split_across_adjacent_source_members_keeps_both_supports():
    from turbine_kg.agent.runner import task_quantity_checks
    task={'id':'t','object':'压力容器','subquestion':'压力容器试验压力为1.20倍工作压力是否符合要求'}
    view={'kind':'text','context':{},'members':[
        {'member_key':'first','role':'content','quality':'readable',
         'quote':'压力容器试验压力应不小于1.25 倍工作'},
        {'member_key':'second','role':'content','quality':'readable',
         'quote':'压力。'}]}
    facts={'压力容器':{'试验压力':{'value':1.20,'unit':'倍工作压力','target':'压力容器'}}}
    result=task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]
    assert result['status']=='not_satisfied'
    assert result['supports']==[{'evidence_id':'e','member_keys':['first','second']}]


def test_evidence_only_limit_does_not_borrow_other_dimension_or_branch():
    from turbine_kg.agent.runner import task_quantity_checks
    task={'id':'t','object':'凝汽器壳体'}
    facts={'凝汽器壳体':{'全长弯曲度':{'value':4,'unit':'mm','target':'凝汽器壳体'}}}
    view={'kind':'text','context':{},'members':[{'member_key':'m','role':'content','quality':'good',
        'quote':'凝汽器壳体每米弯曲度不应大于3mm/m。'}]}
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'
    facts={'凝汽器壳体':{'弯曲度':{'value':4,'unit':'mm/m','target':'凝汽器壳体'}}}
    view['members'].append({'member_key':'m2','role':'content','quality':'good',
                           'quote':'凝汽器壳体弯曲度不得大于5mm/m。'})
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'
    view['members']=view['members'][:1]
    view['members'][0]['quote']='当壳体为现场拼装时，凝汽器壳体弯曲度不应大于3mm/m。'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'
    view['members'][0]['quote']='凝汽器壳体每米弯曲度不应大于3mm/m。'
    view['members'][0]['quality']='uncertain'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'


@pytest.mark.parametrize('parent_role',['section','content'])
def test_numbered_item_inherits_explicit_parent_and_keeps_parallel_limits_separate(parent_role):
    from turbine_kg.agent.runner import task_quantity_checks
    view={'kind':'text','context':{},'members':[
        {'member_key':'parent','role':parent_role,'quality':'readable',
         'quote':'5.2.2 凝汽器壳体现场组装应符合下列规定：'},
        {'member_key':'item','role':'content','quality':'readable',
         'quote':'6 壳体表面的弯曲度偏差应不大于3mm/m，且全长度的弯曲应小于20mm；'}]}
    facts={'凝汽器壳体':{'弯曲度':{'value':4,'unit':'mm/m','target':'凝汽器壳体'}}}
    task={'id':'t','object':'凝汽器壳体','subquestion':'凝汽器壳体现场组装表面弯曲度4mm/m怎么办'}
    result=task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]
    assert result['status']=='not_satisfied' and result['unit']=='mm/m'
    assert result['supports']==[{'evidence_id':'e','member_keys':['parent','item']}]
    task['subquestion']='凝汽器壳体现场组装弯曲度4mm/m怎么办'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'
    facts['凝汽器壳体']['弯曲度']['location']='表面'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='not_satisfied'
    facts['凝汽器壳体']['弯曲度']['location']='端面'
    task['subquestion']='凝汽器壳体现场组装表面弯曲度4mm/m怎么办'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'
    del facts['凝汽器壳体']['弯曲度']['location']
    task['subquestion']='凝汽器壳体弯曲度4mm/m怎么办'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'


def test_object_plus_explicit_location_target_is_exactly_normalized():
    from turbine_kg.agent.runner import task_quantity_checks
    view={'kind':'text','context':{},'members':[{'member_key':'m','role':'content','quality':'readable',
        'quote':'壳体表面弯曲度不应大于3mm/m。'}]}
    task={'id':'t','object':'壳体','subquestion':'壳体表面弯曲度4mm/m怎么办'}
    actual={'value':4,'unit':'mm/m','target':'壳体表面','location':'表面'}
    facts={'壳体':{'弯曲度':actual}}
    result=task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]
    assert result['status']=='not_satisfied' and result['target_alignment']=='object_plus_location'
    assert result['user_target']=='壳体表面' and actual['target']=='壳体表面'
    actual['target']='外壳表面'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='not_satisfied'
    actual['target']='另一壳体表面'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'
    actual['target']='壳体端面'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'


def test_abbreviated_task_object_still_requires_full_parent_object_and_stage():
    from turbine_kg.agent.runner import task_quantity_checks
    view={'kind':'text','context':{},'members':[
        {'member_key':'parent','role':'section','quality':'readable',
         'quote':'5.2.2 凝汽器壳体现场组装应符合下列规定：'},
        {'member_key':'item','role':'content','quality':'readable',
         'quote':'6 壳体表面的弯曲度偏差应不大于3mm/m，且全长度的弯曲应小于20mm；'}]}
    task={'id':'t','object':'壳体','subquestion':'凝汽器壳体现场组装时壳体表面弯曲度偏差4mm/m怎么办'}
    facts={'壳体':{'弯曲度偏差':{'value':4,'unit':'mm/m','target':'壳体表面','location':'表面'}}}
    result=task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]
    assert result['status']=='not_satisfied'
    assert result['supports']==[{'evidence_id':'e','member_keys':['parent','item']}]
    task['subquestion']='壳体现场组装时壳体表面弯曲度偏差4mm/m怎么办'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'
    task['subquestion']='凝汽器壳体表面弯曲度偏差4mm/m怎么办'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'


@pytest.mark.parametrize('qualifier',['检修前','复位后','校准时','运输期间','maintenance前'])
def test_numeric_scope_recognition_does_not_enumerate_domain_activities(qualifier):
    from turbine_kg.knowledge.evidence_limits import compare_read_evidence
    view={'kind':'text','context':{},'members':[{'member_key':'m','role':'content',
          'quote':f'壳体{qualifier}弯曲度应不大于3mm/m。'}]}
    task={'id':'t','object':'壳体'}
    facts={'壳体':{'弯曲度':{'value':4,'unit':'mm/m','target':'壳体'}}}
    assert compare_read_evidence(task,{'e':view},facts,REGISTRY)[0]['status']=='unknown'


def test_parent_object_and_composite_location_are_exactly_bound():
    from turbine_kg.agent.runner import task_quantity_checks
    view={'kind':'text','context':{},'members':[
        {'member_key':'parent','role':'section','quality':'readable',
         'quote':'5.2.2 凝汽器壳体现场组装应符合下列规定：'},
        {'member_key':'item','role':'content','quality':'readable',
         'quote':'壳体表面的弯曲度偏差应不大于3mm/m。'}]}
    task={'id':'t','object':'壳体','subquestion':'凝汽器壳体现场组装时壳体表面弯曲度偏差4mm/m是否合格'}
    fact={'value':4,'unit':'mm/m','target':'凝汽器壳体','location':'壳体表面'}
    facts={'壳体':{'弯曲度偏差':fact}}
    result=task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]
    assert result['status']=='not_satisfied'
    assert result['target_alignment']=='parent_object_and_location'
    claim={'task_id':'t','supports':result['supports']}
    fact['target']='另一设备壳体'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'
    fact['target']='凝汽器壳体';fact['location']='壳体端面'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'
    fact['target']='壳体表面弯曲度偏差';fact['location']='壳体表面'
    result=task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]
    assert result['status']=='not_satisfied'
    assert result['target_alignment']=='object_location_parameter'
    fact['target']='另一壳体表面弯曲度偏差'
    assert task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]['status']=='unknown'
    fact.pop('target')
    fact['location']='壳体表面'
    result=task_quantity_checks([task],{'e':view},facts,REGISTRY)[0]
    assert result['status']=='not_satisfied'


def _semantic_fixture(applicability='applicable'):
    from turbine_kg.knowledge.evidence_limits import compare_read_evidence_semantically
    from turbine_kg.runtime import Budget

    question='制造厂未规定试验压力时，压力容器采用1.20倍工作压力是否符合要求？'
    task={'id':'t','object':'压力容器','subquestion':question}
    facts={'压力容器':{'试验压力':{'value':'1.20','unit':'倍工作压力','target':'压力容器'}}}
    view={'kind':'text','text':'压力容器严密性水压试验应符合下列规定：\n制造厂未规定试验压力时，严密性水压试验压力不小于1.25倍工作压力。',
        'members':[
            {'member_key':'lead','role':'section','quality':'readable','quote':'压力容器严密性水压试验应符合下列规定：'},
            {'member_key':'limit','role':'content','quality':'readable','quote':'制造厂未规定试验压力时，严密性水压试验压力不小于1.25倍工作压力。'},
        ]}
    class Model:
        calls=0
        def call(self,system,user,budget):
            self.calls+=1
            assert '只为已有的未知数值比较寻找来源限值' in system
            return {'comparisons':[{'parameter':'试验压力','target':'压力容器','location':None,
                'value':'1.25','unit':'倍工作压力','operator':'ge','applicability':applicability,
                'evidence_id':'e','member_keys':['lead','limit'],
                'source_quote':'制造厂未规定试验压力时，严密性水压试验压力不小于1.25倍工作压力。',
                'limit_quote':'试验压力不小于1.25倍工作压力',
                'source_condition_quote':'制造厂未规定试验压力时',
                'user_condition_quote':'制造厂未规定试验压力时'}]}
    model=Model()
    checks=[{'status':'unknown','parameter':'试验压力','source':'read_evidence','reason':'条件暂未确定'}]
    result=compare_read_evidence_semantically(task,{'e':view},facts,REGISTRY,model,Budget(),
                                              existing_checks=checks,question=question)
    return result,model,checks


def test_semantic_source_alignment_computes_only_after_exact_source_and_condition_binding():
    result,model,original=_semantic_fixture()
    assert result[0]['status']=='not_satisfied'
    assert result[0]['source']=='semantic_read_evidence'
    assert result[0]['limit']=='1.25' and result[0]['unit']=='倍工作压力'
    assert result[0]['supports']==[{'evidence_id':'e','member_keys':['lead','limit']}]
    assert original[0]['status']=='unknown'
    assert model.calls==1


def test_semantic_alignment_preserves_unknown_applicability():
    result,_,_=_semantic_fixture('unknown')
    assert result[0]['status']=='unknown'
    assert result[0]['applicability']=='unknown'


@pytest.mark.parametrize('change', ['wrong_member','wrong_quote','wrong_parameter','wrong_condition',
                                    'unhashable_member','invalid_evidence'])
def test_semantic_alignment_rejects_unbound_or_mismatched_proposals(change):
    from turbine_kg.knowledge.evidence_limits import compare_read_evidence_semantically
    from turbine_kg.runtime import Budget

    question='制造厂未规定试验压力时，压力容器采用1.20倍工作压力是否符合要求？'
    task={'id':'t','object':'压力容器','subquestion':question}
    facts={'压力容器':{'试验压力':{'value':'1.20','unit':'倍工作压力','target':'压力容器'}}}
    view={'kind':'text','members':[
        {'member_key':'lead','role':'section','quality':'readable','quote':'压力容器严密性水压试验应符合下列规定：'},
        {'member_key':'limit','role':'content','quality':'readable','quote':'制造厂未规定试验压力时，严密性水压试验压力不小于1.25倍工作压力。'},
    ]}
    class Model:
        def call(self,system,user,budget):
            row={'parameter':'试验压力','target':'压力容器','location':None,'value':'1.25',
                 'unit':'倍工作压力','operator':'ge','applicability':'applicable','evidence_id':'e',
                 'member_keys':['lead','limit'],
                 'source_quote':'制造厂未规定试验压力时，严密性水压试验压力不小于1.25倍工作压力。',
                 'limit_quote':'试验压力不小于1.25倍工作压力',
                 'source_condition_quote':'制造厂未规定试验压力时',
                 'user_condition_quote':'制造厂未规定试验压力时'}
            if change=='wrong_member':row['member_keys']=['missing']
            if change=='unhashable_member':row['member_keys']=[{'key':'limit'}]
            if change=='invalid_evidence':row['evidence_id']=['e']
            if change=='wrong_quote':row['source_quote']='制造厂未规定试验压力时，压力容器试验压力不小于1.30倍工作压力。'
            if change=='wrong_parameter':row['parameter']='全长度弯曲度'
            if change=='wrong_condition':row['user_condition_quote']='制造厂有试验压力规定时'
            return {'comparisons':[row]}
    checks=[{'status':'unknown','parameter':'试验压力','source':'read_evidence'}]
    result=compare_read_evidence_semantically(task,{'e':view},facts,REGISTRY,Model(),Budget(),
                                              existing_checks=checks,question=question)
    assert result==checks


def test_semantic_alignment_does_not_call_model_for_known_check():
    from turbine_kg.knowledge.evidence_limits import compare_read_evidence_semantically
    from turbine_kg.runtime import Budget

    class Model:
        def call(self,*args):raise AssertionError('known comparison must not request model alignment')
    result=compare_read_evidence_semantically({'object':'设备'}, {}, {}, REGISTRY, Model(), Budget(),
                                              existing_checks=[{'status':'satisfied','parameter':'偏差'}])
    assert result==[{'status':'satisfied','parameter':'偏差'}]


def test_unknown_unit_reaches_the_model_with_original_source_and_does_not_block_answer():
    import json
    from types import SimpleNamespace
    from turbine_kg.agent.claims import validate
    from turbine_kg.knowledge.evidence_limits import compare_read_evidence_semantically
    from turbine_kg.runtime import Budget
    quote='装置电阻不大于5mΩ。'
    view={'kind':'text','members':[{'member_key':'m','role':'content','quality':'readable','quote':quote}]}
    task={'id':'t','object':'装置','subquestion':'电阻实测4mΩ是否合适'}
    facts={'装置':{'电阻':{'value':4,'unit':'mΩ'}}}
    class Model:
        def call(self,system,user,budget):
            payload=json.loads(user)
            assert payload['read_evidence'][0]['members'][0]['quote']==quote
            assert payload['user_facts']['电阻']['unit']=='mΩ'
            return {'comparisons':[{'parameter':'电阻','target':'装置','value':5,'unit':'mΩ',
                'operator':'le','applicability':'applicable','evidence_id':'e','member_keys':['m'],
                'source_quote':quote,'limit_quote':'不大于5mΩ'}]}
    result=compare_read_evidence_semantically(task,{'e':view},facts,REGISTRY,Model(),Budget(),
        existing_checks=[{'status':'unknown','parameter':'电阻'}])
    assert result[0]['source']=='semantic_read_evidence' and result[0]['unit']=='mΩ'
    assert result[0]['status']=='unknown'  # No invented deterministic conversion.
    view.update(evidence_id='e',title='规范',revision_id='r')
    view['members'][0].update(locator={},text_origin='native_text')
    tools=SimpleNamespace(reads={'e':view},visuals={},summary=[],diagnostics=[])
    claim={'task_id':'t','basis':'source','text':'要求装置电阻不大于5mΩ，实测4mΩ低于此限值。',
           'supports':[{'evidence_id':'e','member_keys':['m']}]}
    accepted,rejected=validate([claim],tools,tasks=[task])
    assert accepted and not rejected
