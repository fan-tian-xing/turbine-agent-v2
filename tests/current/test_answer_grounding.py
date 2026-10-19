"""Exercise persisted entity shape and the independent advice review boundary."""
from types import SimpleNamespace

from turbine_kg.agent.claims import (_quantities, model_source_views, validate,
                                     bind_source_context,proposed_source_bindings,citations_for,group_citations)
from turbine_kg.agent.runner import (candidate_priority, competing_unread_sources,
                                     review_answer_completeness, task_activity_match,
                                     task_statement_applicability, task_subject_match,
                                     filter_independent_source_claims,
                                     model_task_contracts,model_grounding_context,plan_tasks,Session,AnswerResult,Agent)
from turbine_kg.ontology.registry import OntologyRegistry
from turbine_kg.runtime import Budget,Snapshot


def sources():
    members=[{'member_key':key,'source_id':key,'quote':quote,'role':'content',
              'locator':{'physical_page':1},'text_origin':'native_text','attributes':{}}
             for key,quote in [('a','设备安装前应清洁。'),('b','部件应牢固。')]]
    return {'e':{'evidence_id':'e','title':'资料','document_id':'d','revision_id':'r',
                 'kind':'text','context':{},'members':members}}


def tools():
    return SimpleNamespace(reads=sources(),visuals={},summary=[],diagnostics=[])


def claim():
    return {'id':'c','text':'设备安装前应清洁。','basis':'source','task_id':'t',
            'supports':[{'evidence_id':'e','member_keys':['a']}],
            'conditions':[],'verification':[],'assumptions':[]}


def test_answer_cannot_cite_only_the_trailing_half_of_a_layout_split_sentence():
    view=sources()['e']
    view['members'][0]['quote']='试验压力应不小于1.25倍工作'
    view['members'][1]['quote']='压力，保压10min应无泄漏、无残余变形。'
    reads={'e':view}
    selected={'supports':[{'evidence_id':'e','member_keys':['b']}]}
    bound=bind_source_context(selected,reads)
    assert bound['supports'][0]['member_keys']==['a','b']
    groups=group_citations(citations_for(bound,reads),reads)
    assert groups[0]['quotes']==['试验压力应不小于1.25倍工作压力，保压10min应无泄漏、无残余变形。']


def test_citation_completion_does_not_restore_a_neighboring_numbered_item():
    view=sources()['e']
    view['members']=[
        {'member_key':'lead','source_id':'lead','order':0,'role':'section',
         'quote':'检查应符合下列规定：','attributes':{}},
        {'member_key':'first','source_id':'first','order':1,'role':'content',
         'quote':'1 铜管应检查；','attributes':{}},
        {'member_key':'second','source_id':'second','order':2,'role':'content',
         'quote':'2 钢管应清洁。','attributes':{'structural_parent_source_ids':['first']}},
    ]
    bound=bind_source_context({'supports':[{'evidence_id':'e','member_keys':['second']}]},{'e':view})
    assert bound['supports']==[{'evidence_id':'e','member_keys':['lead','second']}]


def test_persisted_class_id_is_used_for_activity_and_hierarchy():
    registry=OntologyRegistry()
    activity={'class':'OntologyClass','class_id':'Installation','label':'安装','role':'activity'}
    assert task_activity_match({'entities':[activity]},{'activity':'安装'},registry)=='applicable'
    subject={'class':'OntologyClass','class_id':'SteamJetEjector','label':'蒸汽抽气器','role':'subject'}
    parent=registry.domain_class_by_id('Ejector')['label']
    assert task_subject_match({'entities':[subject]},{'object':parent},registry)=='applicable'


def test_component_and_other_activity_remain_candidates_for_semantic_review():
    registry=OntologyRegistry()
    component={'class':'OntologyClass','class_id':'Shell','label':'壳体','role':'subject'}
    activity={'class':'OntologyClass','class_id':'TrialOperation','label':'试运行','role':'activity'}
    task={'object':registry.domain_class_by_id('Deaerator')['label'],'activity':'安装'}
    assert task_subject_match({'entities':[component]},task,registry)=='unknown'
    assert task_activity_match({'entities':[activity]},task,registry)=='not_applicable'
    assert task_statement_applicability({'entities':[component,activity]},task,registry)=='unknown'


def test_answer_scope_rejects_an_unasked_independent_component_operation():
    registry=OntologyRegistry()
    related={'class_id':'Deaerator','label':'除氧器','role':'related'}
    subject={'class':'Mention','label':'安全阀','role':'subject'}
    statement={'supports':[{'evidence_id':'e','member_keys':['lead','item']}],
               'entities':[related,subject]}
    source={'evidence_id':'e','context':{'clause_number':'5.6.5'},'members':[
        {'member_key':'lead','role':'content','quote':'承压容器安全阀安装应符合下列规定：'},
        {'member_key':'item','role':'content','quote':'高压除氧器的安全阀应为全启式。'}]}
    proposal={'id':'c','task_id':'t','supports':[{'evidence_id':'e','member_keys':['lead','item']}],
              'text':'高压除氧器的安全阀应为全启式。'}
    task={'id':'t','object':'除氧器','subquestion':'除氧器安装应符合什么规定'}
    accepted,rejected=filter_independent_source_claims(
        [proposal],[task],{'e':source},[statement],registry)
    assert not accepted and rejected[0]['claim']['id']=='c'
    origin={'evidence_id':'origin','members':[{'member_key':'o','role':'content',
             'quote':'除氧器安装应符合5.6.1至5.6.8的规定。'}]}
    accepted,rejected=filter_independent_source_claims(
        [proposal],[task],{'e':source,'origin':origin},[statement],registry,
        [{'evidence_id':'e','origin_evidence_id':'origin','reference_number':'5.6.1'}])
    assert not accepted and rejected
    accepted,rejected=filter_independent_source_claims(
        [proposal],[task],{'e':source,'origin':origin},[statement],registry,
        [{'evidence_id':'e','origin_evidence_id':'origin','reference_number':'5.6.5'}])
    assert not accepted and rejected
    origin['members'][0]['quote']='除氧器安装的安全阀检查应符合5.6.5的规定。'
    accepted,rejected=filter_independent_source_claims(
        [proposal],[task],{'e':source,'origin':origin},[statement],registry,
        [{'evidence_id':'e','origin_evidence_id':'origin','reference_number':'5.6.5'}])
    assert accepted==[proposal] and not rejected
    # The same source is in scope when the user names the component operation.
    task['subquestion']='除氧器安全阀安装应符合什么规定'
    accepted,rejected=filter_independent_source_claims(
        [proposal],[task],{'e':source},[statement],registry)
    assert accepted==[proposal] and not rejected
    # A component item under a clause that directly governs the requested
    # equipment remains in scope even if its own sentence omits the equipment.
    task['subquestion']='除氧器安装应符合什么规定'
    source['members'][0]['quote']='除氧器安装应符合下列规定：'
    accepted,rejected=filter_independent_source_claims(
        [proposal],[task],{'e':source},[statement],registry)
    assert accepted==[proposal] and not rejected


def test_an_unasked_different_object_class_does_not_supply_the_answer():
    registry=OntologyRegistry()
    statement={'supports':[{'evidence_id':'e','member_keys':['a']}],
               'entities':[{'class_id':'PressureVessel','label':'压力容器','role':'subject'}]}
    view={'evidence_id':'e','members':[{'member_key':'a','role':'content',
          'quote':'压力容器的严密性水压试验应符合要求。'}]}
    proposal={'id':'c','task_id':'t','text':'压力容器应试验。',
              'supports':[{'evidence_id':'e','member_keys':['a']}]}
    task={'id':'t','object':'除氧器','subquestion':'除氧器安装应符合什么规定'}
    kept,rejected=filter_independent_source_claims(
        [proposal],[task],{'e':view},[statement],registry)
    assert not kept and rejected
    task['subquestion']='除氧器作为压力容器进行水压试验应符合什么规定'
    kept,rejected=filter_independent_source_claims(
        [proposal],[task],{'e':view},[statement],registry)
    assert kept==[proposal] and not rejected


def test_model_subquestion_cannot_expand_the_user_question_scope():
    registry=OntologyRegistry()
    source={'evidence_id':'e','members':[{'member_key':'a','role':'content',
            'quote':'压力容器的严密性水压试验应符合要求。'}]}
    statement={'supports':[{'evidence_id':'e','member_keys':['a']}],
               'entities':[{'class_id':'PressureVessel','label':'压力容器','role':'subject'}]}
    claim={'id':'c','task_id':'t','text':'压力容器应试验。',
           'supports':[{'evidence_id':'e','member_keys':['a']}]}
    task={'id':'t','object':'压力容器','subquestion':'除氧器作为压力容器应如何试验'}
    kept,rejected=filter_independent_source_claims(
        [claim],[task],{'e':source},[statement],registry,question='除氧器安装应符合什么规定')
    assert not kept and rejected
    kept,rejected=filter_independent_source_claims(
        [claim],[task],{'e':source},[statement],registry,question='压力容器水压试验应符合什么规定')
    assert kept==[claim] and not rejected


def test_claim_must_bind_an_existing_member_before_semantic_review():
    leaked={**claim(),'supports':[{'evidence_id':'e','member_keys':['missing']}]}
    accepted,rejected=validate([leaked],tools(),facts={},tasks=[{'id':'t'}])
    assert not accepted and '越界' in rejected[0]['reason']


def test_malformed_model_member_addresses_are_rejected_without_aborting_other_claims():
    for support in ({'evidence_id':[],'member_keys':['a']},
                    {'evidence_id':'e','member_keys':[{}]},
                    {'evidence_id':'e','member_keys':'a'}):
        malformed={**claim(),'id':'bad','supports':[support]}
        assert proposed_source_bindings([malformed],sources())=={}
        unchanged=bind_source_context(malformed,sources())
        accepted,rejected=validate([unchanged,claim()],tools(),facts={},tasks=[{'id':'t'}])
        assert [item['id'] for item in accepted]==['c']
        assert len(rejected)==1 and rejected[0]['claim']['id']=='bad'


def test_preparation_is_not_rejected_by_literal_phase_suffix():
    accepted,rejected=validate([claim()],tools(),facts={},
                               tasks=[{'id':'t','subquestion':'设备安装中发现问题怎么办'}])
    assert len(accepted)==1 and not rejected


def test_independent_review_is_cached_and_requires_all_claim_verdicts():
    class Model:
        calls=0
        def call(self,system,user,budget):
            self.calls+=1
            assert '独立的回答核验请求' in system
            return {'tasks':[{'task_id':'t','complete':True,'claim_verdicts':[
                {'claim_id':'c','supported':True,'applicable':True,'conditions_preserved':True}],
                'missing_items':[]}]}
    model=Model();cache={};read=tools();task={'id':'t','required':True}
    for _ in range(2):
        result,error=review_answer_completeness('设备安装要求',[task],[claim()],read,model,Budget(),facts={},review_cache=cache)
        assert result['t']['complete'] and not error
    assert model.calls==1
    unknown={**claim(),'id':'other'}
    result,_=review_answer_completeness('设备安装要求',[task],[claim(),unknown],read,model,Budget(),facts={})
    assert not result['t']['complete']


def test_review_timeout_preserves_partial_answer_without_claiming_completeness():
    class Model:
        def call(self,system,user,budget):raise TimeoutError()
    result,error=review_answer_completeness('设备安装要求',[{'id':'t','required':True}],
                                           [claim()],tools(),Model(),Budget(),facts={})
    assert error and not result['t']['complete']
    assert result['t']['rejected_claims']==[]
    assert result['t']['review_failure']['kind']=='review_unfinished'


def test_source_projection_deduplicates_views_without_truncating_quotes():
    view=sources()['e']
    projected=model_source_views([view,view])
    assert len(projected)==1
    assert [member['quote'] for member in projected[0]['members']]==[member['quote'] for member in view['members']]


def test_reading_generic_preparation_does_not_hide_direct_object_source():
    registry=OntologyRegistry()
    task={'id':'t','object':'除氧器','activity':'安装','knowledge_item':'requirement','query_terms':['安装','变形']}
    preparation={'id':'prep','type':'requirement','text':'设备安装前应检查。',
        'entities':[{'class':'OntologyClass','class_id':'Installation','role':'activity'}],
        'supports':[{'evidence_id':'prep-source','member_keys':['a']}],
        'ontology_paths':[{'object_id':'equipment','kind':'ancestor'}]}
    direct={'id':'direct','type':'requirement','text':'除氧器变形时应检查。',
        'entities':[], 'supports':[{'evidence_id':'direct-source','member_keys':['a']}],
        'ontology_paths':[{'object_id':'deaerator','kind':'direct_class'}]}
    result={'tool':'navigate','task_ids':['t'],'result':{'items':[preparation,direct]}}
    assert candidate_priority(direct,result,[task],'除氧器安装时变形怎么办',registry)>candidate_priority(preparation,result,[task],'除氧器安装时变形怎么办',registry)
    assert competing_unread_sources(task,[result],{'prep-source':{}},'除氧器安装时变形怎么办',registry)


def test_semantically_inapplicable_claim_is_explicitly_returned_for_repair():
    class Model:
        def call(self,system,user,budget):
            return {'tasks':[{'task_id':'t','complete':True,'claim_verdicts':[
                {'claim_id':'c','supported':True,'applicable':False,'conditions_preserved':True,'reason':'属于后续活动'}]}]}
    result,_=review_answer_completeness('安装问题',[{'id':'t','required':True}],
                                       [claim()],tools(),Model(),Budget(),facts={})
    assert not result['t']['complete']
    assert result['t']['rejected_claims']==[{'claim_id':'c','reason':'属于后续活动'}]


def test_many_knowledge_directions_are_one_real_question_without_skills():
    kinds=['anomaly','cause','inspection','requirement','measure','procedure','verification']
    spec={'object':'凝汽器','subquestion':'凝汽器安装时壳体变形怎么办','activity':'安装','knowledge_items':kinds}
    registry=OntologyRegistry()
    tasks,skills,_=plan_tasks({'objects':['凝汽器'],'object_tasks':[spec]},Session(),registry,[],use_skills=False)
    assert len(tasks)==1 and tasks[0]['knowledge_items']==kinds
    assert set(tasks[0]['relation_ids'])==set(registry.navigation(kinds))
    assert not skills and not tasks[0]['skill_ids']
    tasks,_,_=plan_tasks({'objects':['凝汽器'],'object_tasks':[spec,{**spec,'subquestion':'凝汽器支座如何安装'}]},Session(),registry,[],use_skills=False)
    assert len(tasks)==2


def test_model_task_and_entity_projection_omit_internal_repeated_state():
    task={'id':'t','object':'设备','subquestion':'怎么处理','knowledge_items':['inspection','measure'],
          'coverage_supports':[{'quote':'重复内容'*1000}],'candidate_statement_ids':['old']*100,
          'answer_review_failure':{'reason':'old'},'required':True}
    projected=model_task_contracts([task])[0]
    assert set(projected)=={'id','object','subquestion','knowledge_items'}
    statement={'id':'s','type':'requirement','text':'设备应清洁。','entities':[
        {'id':'entity','class':'OntologyClass','class_id':'Equipment','label':'设备','role':'subject',
         'definition':'很长的重复本体定义'*1000,'ontology_binding':{'class_id':'Equipment','definition':'duplicate'}}],
        'supports':[{'evidence_id':'e','member_keys':['a'],'quote':'重复原文'}]}
    knowledge=model_grounding_context([statement],sources())
    assert knowledge[0]['entities']==[{'label':'设备','role':'subject'}]
    assert set(knowledge[0])=={'evidence_id','member_keys','entities'}
    assert '重复原文' not in str(knowledge)


def test_generation_knowledge_contains_only_fully_read_sources_and_keeps_relation_roles():
    statement={'id':'s','type':'requirement','text':'设备应清洁。','entities':[],
        'supports':[{'evidence_id':'e','member_keys':['a']}],
        'retrieval_role':'related_context','ontology_paths':[{'kind':'related_context','class_id':'Equipment','path':['large']}]}
    unread={**statement,'id':'unread','supports':[{'evidence_id':'unread-source','member_keys':['a']}]}
    knowledge=model_grounding_context([statement,unread],sources())
    assert len(knowledge)==1
    assert knowledge[0]['match_kinds']==['related_context']


def test_compound_measurement_unit_is_preserved_without_partial_unit_match():
    assert _quantities('4mm/m')=={(4.0,'mm/m')}
    assert _quantities('4mm/m')!=_quantities('4mm')
    assert _quantities('4mm/unknown')==set()
    assert _quantities('1cm/m；2m/m')=={(1.0,'cm/m'),(2.0,'m/m')}


def test_user_measurement_can_be_restated_without_a_successful_comparison():
    task={'id':'t','object':'设备','numeric_comparisons':[]}
    facts={'设备':{'弯曲度':{'value':'4','unit':'mm/m'}}}
    restatement={**claim(),'text':'对你提供的4mm/m，现有资料没有明确修复工艺。'}
    accepted,rejected=validate([restatement],tools(),facts=facts,tasks=[task])
    assert len(accepted)==1 and not rejected
    # Permitting user facts is not approval to invent a standard limit. The
    # independent semantic review rejects that meaning using the actual source.
    invented={**restatement,'text':'规范限值为4mm/m。'}
    class Model:
        def call(self,system,user,budget):
            return {'tasks':[{'task_id':'t','complete':False,'claim_verdicts':[
                {'claim_id':'c','supported':False,'applicable':True,'conditions_preserved':True,
                 'reason':'把用户测值当成规范阈值'}]}]}
    verdict,_=review_answer_completeness('设备实测4mm/m怎么办',[task],[invented],tools(),Model(),Budget(),facts=facts)
    assert verdict['t']['rejected_claims'][0]['reason']=='把用户测值当成规范阈值'


def test_inference_can_state_its_premise_in_text_without_an_extra_field():
    inference={**claim(),'basis':'inference','text':'若内部仍有杂物，建议先清理后再安装。'}
    inference.pop('assumptions')
    accepted,rejected=validate([inference],tools(),facts={},tasks=[{'id':'t'}])
    assert len(accepted)==1 and not rejected


def test_inference_cannot_bypass_numeric_or_reference_grounding():
    from turbine_kg.agent.claims import binding_error
    for text in ('建议将间隙调整到100mm。','建议按第9.9.9条执行。',
                 '建议改为DL/T 9876—2030中的工艺。'):
        invented={**claim(),'basis':'inference','text':text}
        assert binding_error(invented,sources(),{}) is not None


def test_inference_can_restate_user_values_without_a_matching_source_value():
    inference={**claim(),'basis':'inference','text':'根据你给出的4mm/m，建议先核实测量部位。'}
    accepted,rejected=validate([inference],tools(),facts={'设备':{'弯曲度':{'value':'4','unit':'mm/m'}}},
                               tasks=[{'id':'t','object':'设备'}])
    assert len(accepted)==1 and not rejected


def test_completed_program_quantities_are_available_only_with_their_source_binding():
    from turbine_kg.agent.claims import binding_error
    inference={**claim(),'basis':'inference','text':'程序比较采用的数值为4mm。'}
    check={'status':'satisfied','normalized_actual':'4','comparison_unit':'mm',
           'supports':[{'evidence_id':'e','member_keys':['a']}]}
    task={'id':'t','numeric_comparisons':[check]}
    assert binding_error(inference,sources(),{},task=task) is None
    assert binding_error(inference,sources(),{},task={**task,'id':'other'}) is not None
    check['status']='unknown'
    assert binding_error(inference,sources(),{},task=task) is not None
    check.update(status='satisfied',supports=[{'evidence_id':'e','member_keys':['b']}])
    assert binding_error(inference,sources(),{},task=task) is not None


def test_review_can_resolve_only_model_gaps_for_the_same_task():
    class Model:
        def call(self,system,user,budget):
            return {'tasks':[{'task_id':'t','complete':True,'claim_verdicts':[
                {'claim_id':'c','supported':True,'applicable':True,'conditions_preserved':True}],
                'resolved_gap_ids':['old','system','other']}]}
    gaps=[{'id':'old','task_id':'t','kind':'source_missing','provenance':'model'},
          {'id':'system','task_id':'t','kind':'system_incomplete','provenance':'model'},
          {'id':'other','task_id':'other-task','kind':'source_missing','provenance':'model'}]
    verdict,_=review_answer_completeness('设备安装',[{'id':'t'}],[claim()],tools(),Model(),Budget(),facts={},gaps=gaps)
    assert verdict['t']['resolved_gap_ids']==['old']


def test_unselected_later_activity_reference_does_not_enter_advice_bindings():
    reads=sources()
    reads['later']={'evidence_id':'later','document_id':'d','revision_id':'r',
        'members':[{'member_key':'later-key','source_id':'later-source','quote':'试运还应符合安装规定。'}],
        'context':{'reference_resolution':[{'status':'resolved','target_evidence_ids':['e'],'source_member_keys':['later-key']}]}}
    proposed=claim()
    selected=proposed_source_bindings([proposed],reads)
    bound=bind_source_context(proposed,reads,origin_bindings=selected['t'])
    assert bound['supports']==proposed['supports']


def test_partial_review_failure_is_visible_without_discarding_sourced_advice():
    advice=claim()
    result=AnswerResult('先检查清洁度。','partial',[advice],[],[],{
        'task_coverage':[{'answer_review_failure':{'kind':'review_unfinished'}}]})
    assert result.claims==[advice]
    assert '核验尚未完成' in result.answer
    normal=AnswerResult('先检查清洁度。','answered',[advice],[],[],{'task_coverage':[{}]})
    assert normal.answer=='先检查清洁度。'


def test_optional_image_source_kind_does_not_require_a_visual_answer():
    registry=OntologyRegistry()
    class Store:
        def snapshot(self,*args):return Snapshot((),registry.version,())
        def query(self,*args,**kwargs):return []
    class Model:
        def call(self,system,user,budget,**kwargs):
            if '理解原始问题' in system:
                return {'objects':['设备'],'knowledge_items':['requirement'],
                        'source_kinds':['text','table','image'],'visual_required':False}
            return {'calls':[],'stop':True}
    result=Agent(Store(),model=Model(),registry=registry).answer('设备安装有哪些要求',budget=Budget(seconds=10))
    assert not any(gap.get('kind')=='visual_unread' for gap in result.gaps)


def test_source_projection_preserves_cross_page_quotes_and_table_context():
    view=sources()['e'];view['members'][1]['locator']['physical_page']=2
    view['members'][0]['attributes']['internal_layout']='irrelevant'
    projection=model_source_views([view])[0]
    assert projection['physical_pages']==[1,2]
    assert [member['physical_page'] for member in projection['members']]==[1,2]
    assert all(set(member)=={'member_key','role','quote','physical_page'} for member in projection['members'])
    table={**view,'kind':'table','context':{'table_title':'尺寸要求'},'members':[
        {'member_key':'header','role':'header','quote':'允许偏差/mm','source_kind':'cell',
         'attributes':{'row_index':0,'column_index':1,'column_span':2,'bbox':[1,2,3,4]}},
        {'member_key':'note','role':'note','quote':'仅用于冷态','source_kind':'table_note',
         'attributes':{'coverage':{'rows':[1,2]}}}]}
    projected=model_source_views([table])[0]
    assert projected['context']['table_title']=='尺寸要求'
    assert projected['members'][0]['table']=={'row_index':0,'column_index':1,'column_span':2}
    assert projected['members'][1]['table']=={'coverage':{'rows':[1,2]}}


def test_shared_grounding_context_is_used_by_independent_review():
    context={'sources':model_source_views(sources().values()),'grounding_context':[
        {'evidence_id':'e','member_keys':['a'],'entities':[{'label':'设备','role':'subject'}]}]}
    class Model:
        def call(self,system,user,budget):
            import json
            payload=json.loads(user)
            assert payload['sources']==context['sources']
            assert payload['grounding_context']==context['grounding_context']
            return {'tasks':[]}
    review_answer_completeness('设备安装',[{'id':'t'}],[claim()],tools(),Model(),Budget(),facts={},source_context=context)


def test_equivalent_units_pass_but_wrong_dimensions_and_bare_numbers_do_not():
    from turbine_kg.agent.claims import binding_error
    reads=sources();reads['e']['members'][0]['quote']='偏差应不大于10mm。'
    assert binding_error({**claim(),'text':'偏差不大于1cm。'},reads,{}) is None
    assert binding_error({**claim(),'text':'偏差不大于10m。'},reads,{}) is not None
    assert binding_error({**claim(),'text':'还应检查11个点。'},reads,{}) is not None


def test_completeness_comes_from_actual_verdicts_omissions_and_dependencies():
    for scenario,expected in [('legacy_false',True),('missing',False),('no_verdict',False),('blocked',False)]:
        read=tools()
        if scenario=='blocked':
            read.reads['e']['context']['reference_resolution']=[{
                'status':'resolved','source_member_keys':['a'],'target_evidence_ids':['not-read']}]
        class Model:
            def call(self,system,user,budget):
                row={'task_id':'t','complete':False,'claim_verdicts':[
                    {'claim_id':'c','supported':True,'applicable':True,'conditions_preserved':True}],
                    'missing_items':[]}
                if scenario=='missing':row['missing_items']=[{'reason':'缺少使当前操作成立的必要检查',
                    'supports':[{'evidence_id':'e','member_keys':['b']}]}]
                if scenario=='no_verdict':row['claim_verdicts']=[]
                return {'tasks':[row]}
        result,error=review_answer_completeness('设备安装',[{'id':'t'}],[claim()],read,Model(),Budget(),facts={})
        assert not error
        assert result['t']['complete'] is expected,scenario
        if scenario=='blocked':assert result['t']['internal_dependency_pending']


def test_text_reference_wording_can_vary_without_changing_the_identifier():
    from turbine_kg.agent.claims import binding_error,binding_diagnostics
    reads=sources()
    reads['e']['members'][0]['quote']='设备安装还应符合5.1相关规定。'
    same={**claim(),'text':'设备安装还应符合第5.1节的规定。'}
    assert binding_error(same,reads,{}) is None
    assert binding_diagnostics(same,reads)['missing_references']==[]
    different={**same,'text':'设备安装还应符合第5.2节的规定。'}
    assert binding_error(different,reads,{}) is not None
    assert binding_diagnostics(different,reads)['missing_references']
    reads['e']['members'][0]['quote']='设备安装还应符合第5.1节规定。'
    assert binding_error({**same,'text':'设备安装还应符合第5.1条规定。'},reads,{}) is None


def test_table_and_figure_references_cannot_share_a_text_reference_namespace():
    from turbine_kg.agent.claims import binding_error,binding_diagnostics
    reads=sources();reads['e']['members'][0]['quote']='尺寸应符合表5.1规定。'
    same={**claim(),'text':'尺寸应符合表5.1规定。'}
    assert binding_error(same,reads,{}) is None
    for text in ('尺寸应符合图5.1规定。','尺寸应符合第5.1节规定。','尺寸应符合表5.2规定。'):
        changed={**same,'text':text}
        assert binding_error(changed,reads,{}) is not None
        assert binding_diagnostics(changed,reads)['missing_references']
