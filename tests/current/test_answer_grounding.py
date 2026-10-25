"""Exercise persisted entity shape and the independent advice review boundary."""
from types import SimpleNamespace

from turbine_kg.knowledge.quantities import extract_quantities as _quantities
from turbine_kg.agent.claims import (model_source_views, validate,
                                     bind_source_context,proposed_source_bindings,citations_for,group_citations)
from turbine_kg.agent.runner import (candidate_priority, competing_unread_sources,
                                     review_answer_completeness, task_activity_match,
                                     task_statement_applicability, task_subject_match,
                                     model_task_contracts,model_grounding_context,plan_tasks,Session,AnswerResult,Agent)
from turbine_kg.ontology.registry import OntologyRegistry
from turbine_kg.runtime import Budget,Snapshot


def statement_reference_fixture():
    from turbine_kg.agent.claims import AnswerReferences
    row={'id':'stored-statement','text':'设备安装前应清洁。','type':'Requirement',
         'entities':[{'label':'设备','role':'subject'}],
         'supports':[{'evidence_id':'e','member_keys':['a']}]}
    return AnswerReferences(),row,sources()


def test_generation_uses_statement_handles_without_backend_member_ids():
    import json
    refs,row,reads=statement_reference_fixture()
    context,index=refs.context([row],reads)
    assert context['statements'][0]['id']=='S1'
    assert context['evidence_fallbacks']==[] and 'E1' not in index
    assert index['S1']['supports']==row['supports']
    assert all(key not in json.dumps(context) for key in ('member_keys','evidence_id','stored-statement'))


def test_statement_handles_are_stable_and_multiple_supports_merge_exactly():
    from turbine_kg.agent.claims import resolve_answer_references
    refs,row,reads=statement_reference_fixture()
    refs.context([row],reads)
    second={**row,'id':'another','supports':[{'evidence_id':'e','member_keys':['b','a']}]}
    context,index=refs.context([second,row],reads)
    assert [s['id'] for s in context['statements']]==['S2','S1']
    response=resolve_answer_references({'claims':[{'text':'完整建议','references':['S1','S2','S1']}]},index)
    assert response['claims'][0]['supports']==[{'evidence_id':'e','member_keys':['a','b']}]


def test_unread_or_invalid_statement_is_not_an_available_reference():
    refs,row,reads=statement_reference_fixture()
    row['supports']=[{'evidence_id':'e','member_keys':['missing']}]
    context,index=refs.context([row],reads)
    assert not context['statements'] and 'S1' not in index
    assert context['evidence_fallbacks'][0]['id']=='E1'
    assert index['E1']['supports'][0]['member_keys']==['a','b']
    context,index=refs.context([row],{})
    assert not context['statements'] and not index


def test_evidence_fallback_opens_only_when_needed_or_explicitly_requested():
    refs,row,reads=statement_reference_fixture()
    for incomplete in (False,True):
        reads['e']['source_status']={'semantic_extraction':'incomplete' if incomplete else 'complete'}
        context,index=refs.context([row],reads)
        assert bool(context['evidence_fallbacks']) is incomplete
        assert ('E1' in index) is incomplete
    context,index=refs.context([row],reads,include_evidence=True)
    assert context['evidence_fallbacks'][0]['text']=='设备安装前应清洁。\n部件应牢固。'


def test_unknown_short_reference_cannot_be_bypassed_by_real_source_ids():
    from turbine_kg.agent.claims import resolve_answer_references,binding_error
    refs,row,reads=statement_reference_fixture()
    _,index=refs.context([row],reads)
    for handles in (['S9'],['S1','S9'],['E1'],[]):
        result=resolve_answer_references({'claims':[{**claim(),'references':handles}]},index)
        proposed=result['claims'][0]
        assert proposed['supports']==[] and binding_error(proposed,reads,{})


def test_repair_and_review_missing_items_use_the_same_canonical_supports():
    from turbine_kg.agent.claims import resolve_answer_references,model_answer_rows
    refs,row,reads=statement_reference_fixture()
    _,index=refs.context([row],reads)
    original={'operations':[{'op':'replace','claim_id':'c','claim':{'text':row['text'],'references':['S1']}}],
              'tasks':[{'missing_items':[{'reason':'补齐','references':['S1']}]}]}
    result=resolve_answer_references(original,index)
    assert result['operations'][0]['claim']['supports']==row['supports']
    assert result['tasks'][0]['missing_items'][0]['supports']==row['supports']
    assert 'supports' not in original['operations'][0]['claim']
    outgoing=model_answer_rows([result['operations'][0]['claim']])[0]
    assert outgoing['references']==['S1'] and 'supports' not in outgoing


def test_review_short_references_resolve_and_invalid_omissions_do_not_mean_complete():
    refs,row,reads=statement_reference_fixture()
    _,index=refs.context([row],reads,include_evidence=True)
    class Model:
        reference='S1'
        def call(self,system,user,budget):
            return {'tasks':[{'task_id':'t','claim_verdicts':[{'claim_id':'c',
                'supported':True,'applicable':True,'conditions_preserved':True}],
                'missing_items':[{'reason':'仍需补充','references':[self.reference]}],
                'retrieval_request':None}]}
    model=Model()
    result,error=review_answer_completeness('设备要求',[{'id':'t'}],[claim()],tools(),model,Budget(),
        facts={},reference_index=index)
    assert error is None and result['t']['missing_items'][0]['supports']==row['supports']
    model.reference='S999'
    result,error=review_answer_completeness('设备要求',[{'id':'t'}],[claim()],tools(),model,Budget(),
        facts={},reference_index=index)
    assert error is None and result['t']['claim_ids']==['c']
    assert result['t']['complete'] is False and result['t']['review_failure']['kind']=='binding_incomplete'


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


def test_unused_rejected_draft_is_visible_but_does_not_force_answer_incomplete():
    import json
    bad={**claim(),'id':'missing-part','text':'部件应牢固。',
         'supports':[{'evidence_id':'e','member_keys':['bad-key']}]}
    class Model:
        def call(self,system,user,budget):
            payload=json.loads(user)
            assert payload['binding_rejections'][0]['id']=='missing-part'
            return {'tasks':[{'task_id':'t','claim_verdicts':[{'claim_id':'c','supported':True,
                'applicable':True,'conditions_preserved':True}],'missing_items':[],'retrieval_request':None}]}
    result,error=review_answer_completeness('设备与部件要求',[{'id':'t'}],[claim()],tools(),Model(),Budget(),
        facts={},rejected=[{'claim':bad,'reason':'成员绑定越界'}])
    assert error is None and result['t']['claim_ids']==['c']
    assert result['t']['complete'] is True
    assert result['t']['review_failure'] is None


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


def test_citation_text_uses_source_order_instead_of_answer_order():
    view=sources()['e'];view['context']={'clause_number':'7.1.1','clause_group_id':'clause'}
    view['members']=[{'member_key':key,'source_id':key,'order':i,'source_order':i,
        'quote':quote,'role':'section' if i==0 else 'content','source_kind':'list_item',
        'locator':{'physical_page':3},'text_origin':'native_text','attributes':{}}
        for i,(key,quote) in enumerate([('head','7.1.1 检查应符合下列规定：'),
            ('a','1 第一项；'),('b','2 第二项；'),('c','3 第三项。')])]
    reads={'e':view};citations=[]
    for key in ('c','a','b'):
        citations.extend(citations_for({'supports':[{'evidence_id':'e','member_keys':[key]}]},reads))
    grouped=group_citations(citations,reads)[0]
    assert grouped['member_keys']==['head','a','b','c']
    text=grouped['quotes'][0]
    assert text.index('1 第一项')<text.index('2 第二项')<text.index('3 第三项')


def test_cross_page_citation_groups_follow_pages_and_keep_only_selected_members():
    reads=sources();later={**sources()['e'],'evidence_id':'later','context':{'clause_group_id':'later'}}
    reads['e']['context']={'clause_group_id':'early'}
    reads['e']['members'][0]['locator']['physical_page']=2
    later['members'][0]['locator']['physical_page']=4
    reads['later']=later
    citations=[]
    for eid in ('later','e'):
        citations.extend(citations_for({'supports':[{'evidence_id':eid,'member_keys':['a']}]},reads))
    grouped=group_citations(citations,reads)
    assert [g['physical_pages'] for g in grouped]==[[2],[4]]
    assert all(g['member_keys']==['a'] for g in grouped)


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


def test_object_applicability_uses_semantic_review_instead_of_literal_name_checks():
    import json
    question='波纹补偿器可以拉伸对口吗，设计预拉伸是不是例外？'
    task={'id':'t','object':'管道补偿器','required':True}
    row={**claim(),'text':'除设计要求的预拉伸外，不得用波纹管变形调整安装偏差。'}
    read=tools();read.reads['e']['members'][0]['quote']=row['text']
    class Model:
        def call(self,system,user,budget):
            payload=json.loads(user)
            assert payload['question']==question
            assert '规范名称未逐字出现在问题中' in system
            return {'tasks':[{'task_id':'t','claim_verdicts':[
                {'claim_id':'c','supported':True,'applicable':True,'conditions_preserved':True}],
                'missing_items':[]}]}
    accepted,rejected=validate([row],read,tasks=[task])
    assert accepted and not rejected
    verdict,error=review_answer_completeness(question,[task],accepted,read,Model(),Budget(),facts={})
    assert not error and verdict['t']['complete']


def test_claim_must_bind_an_existing_member_before_semantic_review():
    leaked={**claim(),'supports':[{'evidence_id':'e','member_keys':['missing']}]}
    accepted,rejected=validate([leaked],tools(),tasks=[{'id':'t'}])
    assert not accepted and '越界' in rejected[0]['reason']


def test_malformed_model_member_addresses_are_rejected_without_aborting_other_claims():
    for support in ({'evidence_id':[],'member_keys':['a']},
                    {'evidence_id':'e','member_keys':[{}]},
                    {'evidence_id':'e','member_keys':'a'}):
        malformed={**claim(),'id':'bad','supports':[support]}
        assert proposed_source_bindings([malformed],sources())=={}
        unchanged=bind_source_context(malformed,sources())
        accepted,rejected=validate([unchanged,claim()],tools(),tasks=[{'id':'t'}])
        assert [item['id'] for item in accepted]==['c']
        assert len(rejected)==1 and rejected[0]['claim']['id']=='bad'


def test_preparation_is_not_rejected_by_literal_phase_suffix():
    accepted,rejected=validate([claim()],tools(),
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


def test_approved_claim_is_not_rejudged_after_another_claim_is_added():
    import json
    class Model:
        calls=0
        def call(self,system,user,budget):
            self.calls+=1;payload=json.loads(user)
            if self.calls==1:
                assert 'verified_claim_ids' not in payload
                verdicts=[{'claim_id':'c','supported':True}]
            else:
                assert payload['verified_claim_ids']==['c']
                # A later reviewer changing its mind about unchanged wording
                # cannot delete a previously approved numerical comparison.
                verdicts=[{'claim_id':'c','supported':False,'reason':'措辞不够严谨'},
                          {'claim_id':'new','supported':True}]
            return {'tasks':[{'task_id':'t','claim_verdicts':verdicts,'missing_items':[]}]}
    read=tools();cache={};model=Model();task={'id':'t','required':True}
    first,_=review_answer_completeness('设备要求',[task],[claim()],read,model,Budget(),facts={},review_cache=cache)
    assert first['t']['complete']
    extra={**claim(),'id':'new','text':'部件应牢固。','supports':[{'evidence_id':'e','member_keys':['b']}]}
    second,_=review_answer_completeness('设备要求',[task],[claim(),extra],read,model,Budget(),facts={},review_cache=cache)
    assert second['t']['complete'] and second['t']['claim_ids']==['c','new']
    assert not second['t']['rejected_claims']


def test_changed_source_or_user_question_invalidates_an_earlier_approval():
    import json
    for changed in ('source','question','facts','claim'):
        class Model:
            calls=0
            def call(self,system,user,budget):
                self.calls+=1
                assert 'verified_claim_ids' not in json.loads(user)
                return {'tasks':[{'task_id':'t','claim_verdicts':[{'claim_id':'c',
                    'supported':self.calls==1,'reason':'新依据或问题已改变适用结论'}]}]}
        read=tools();model=Model();cache={};row=claim();q='设备要求';facts={}
        task={'id':'t','required':True}
        review_answer_completeness(q,[task],[row],read,model,Budget(),facts=facts,review_cache=cache)
        if changed=='source':read.reads['e']['members'][0]['quote']='设备安装前不得采用该方法。'
        if changed=='question':q='设备试运时的要求'
        if changed=='facts':facts={'设备':{'压力':'另一个工况'}}
        if changed=='claim':row={**row,'text':'设备应先试运。'}
        result,_=review_answer_completeness(q,[task],[row],read,model,Budget(),facts=facts,review_cache=cache)
        assert not result['t']['complete'] and result['t']['rejected_claims']


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


def test_direct_object_source_is_read_even_when_generic_preparation_scores_higher():
    registry=OntologyRegistry()
    task={'id':'t','object':'风机','knowledge_item':'requirement','query_terms':['检查','试运']}
    general={'evidence_id':'general','retrieval_method':'evidence_fulltext','exact_hits':5,
             'statements':[{'text':'设备试运前应检查。'}]}
    direct={'evidence_id':'fan','retrieval_method':'evidence_fulltext','exact_hits':1,
            'matched_object_labels':['风机'],'statements':[{'text':'风机安装应符合另一节规定。'}]}
    result={'task_ids':['t'],'result':{'items':[general,direct]}}
    assert competing_unread_sources(task,[result],{'general':{}},'风机试运依据在哪',registry)
    assert not competing_unread_sources(task,[result],{'general':{},'fan':{}},'风机试运依据在哪',registry)


def test_generic_keyword_hits_do_not_establish_object_identity():
    registry=OntologyRegistry()
    task={'id':'t','object':'风机','knowledge_item':'requirement','query_terms':['检查']}
    generic={'evidence_id':'generic','retrieval_method':'evidence_fulltext','exact_hits':8,
             'statements':[{'text':'其他设备检查应符合规定。'}]}
    assert candidate_priority(generic,{'task_ids':['t']},[task],'风机检查',registry)[2]==0


def test_lower_relevance_generic_object_does_not_force_reading_other_chapters():
    registry=OntologyRegistry()
    task={'id':'t','object':'管道','knowledge_item':'requirement','query_terms':['冲洗','排水管']}
    flush={'evidence_id':'flush','retrieval_method':'evidence_fulltext','exact_hits':3,
           'matched_object_labels':['管道'],'statements':[{'text':'系统水冲洗临时排水管应符合要求。'}]}
    other={'evidence_id':'other','retrieval_method':'evidence_fulltext','exact_hits':1,
           'matched_object_labels':['管道'],'statements':[{'text':'其他设备连接管道应符合安装要求。'}]}
    result={'task_ids':['t'],'result':{'items':[flush,other]}}
    assert not competing_unread_sources(task,[result],{'flush':{}},'管道冲洗排水管要求',registry)


def test_direct_class_short_name_can_outrank_an_unrelated_full_name():
    registry=OntologyRegistry()
    task={'id':'t','object':'凝汽器冷却管','knowledge_item':'requirement','query_terms':['检查']}
    shared={'type':'requirement','entities':[],
            'ontology_paths':[{'kind':'direct_class','class_id':'CoolingTube'}]}
    transport={**shared,'text':'凝汽器冷却管运输时不得踩踏。',
               'supports':[{'evidence_id':'transport','member_keys':['a']}]}
    inspection={**shared,'text':'冷却管穿胀前应检查。',
                'supports':[{'evidence_id':'inspection','member_keys':['b']}]}
    result={'tool':'navigate','task_ids':['t'],'result':{'items':[transport,inspection]}}
    assert candidate_priority(inspection,result,[task],'冷却管检查要求',registry)>candidate_priority(transport,result,[task],'冷却管检查要求',registry)
    assert competing_unread_sources(task,[result],{'transport':{}},'冷却管检查要求',registry)


def test_explicit_phase_precedes_direct_graph_role_and_keeps_all_matching_clauses():
    registry=OntologyRegistry()
    task={'id':'t','object':'设备','phase':'试运前','knowledge_item':'requirement','query_terms':['试运前']}
    during={'text':'设备试运应平稳。','type':'requirement','ontology_paths':[{'kind':'direct_class'}],
            'supports':[{'evidence_id':'during','member_keys':['a']}]}
    before={'text':'设备试运前应检查阀门。','type':'requirement','ontology_paths':[{'kind':'related_context'}],
            'supports':[{'evidence_id':'before','member_keys':['b']}]}
    other={**before,'text':'设备试运前应检查水位。','supports':[{'evidence_id':'other','member_keys':['c']}]}
    result={'task_ids':['t'],'result':{'items':[during,before,other]}}
    assert candidate_priority(before,result,[task],'设备试运前有哪些要求',registry)>candidate_priority(during,result,[task],'设备试运前有哪些要求',registry)
    assert competing_unread_sources(task,[result],{'during':{},'before':{}},'设备试运前有哪些要求',registry)
    # An Evidence without extracted Statements still participates in phase ranking.
    source={'evidence_id':'raw','retrieval_method':'evidence_fulltext','exact_hits':2,'matched_phases':['试运前']}
    assert candidate_priority(source,result,[task],'设备试运前有哪些要求',registry)>candidate_priority(during,result,[task],'设备试运前有哪些要求',registry)
    assert task_statement_applicability(during,task,registry)=='unknown'


def test_specific_content_precedes_generic_graph_proximity():
    registry=OntologyRegistry()
    task={'id':'t','object':'散热器','knowledge_item':'requirement','query_terms':['气压试验','水压试验','试验压力','试验时间']}
    generic={'text':'散热器严密性试验压力应符合要求。','ontology_paths':[{'kind':'direct_class'}]}
    specific={'evidence_id':'specific','retrieval_method':'evidence_fulltext','exact_hits':4,
              'matched_object_labels':['散热器']}
    result={'task_ids':['t']}
    assert candidate_priority(specific,result,[task],'散热器气压和水压试验的压力时间',registry)>candidate_priority(generic,result,[task],'散热器气压和水压试验的压力时间',registry)


def test_semantically_inapplicable_claim_is_explicitly_returned_for_repair():
    class Model:
        def call(self,system,user,budget):
            return {'tasks':[{'task_id':'t','complete':True,'claim_verdicts':[
                {'claim_id':'c','supported':True,'applicable':False,'conditions_preserved':True,'reason':'属于后续活动'}]}]}
    result,_=review_answer_completeness('安装问题',[{'id':'t','required':True}],
                                       [claim()],tools(),Model(),Budget(),facts={})
    assert not result['t']['complete']
    assert result['t']['rejected_claims']==[{'claim_id':'c','reason':'属于后续活动'}]


def test_review_can_request_wording_repair_when_current_sources_are_sufficient():
    class Model:
        def call(self,system,user,budget):
            return {'tasks':[{'task_id':'t','retrieval_request':None,'claim_verdicts':[
                {'claim_id':'c','supported':True,'applicable':False,'conditions_preserved':True}],
                'missing_items':[{'reason':'采用已读的准备要求','supports':claim()['supports']}]}]}
    result,_=review_answer_completeness('设备安装前要求',[{'id':'t','phase':'安装前'}],
                                       [claim()],tools(),Model(),Budget(),facts={})
    assert result['t']['retrieval_request'] is None
    assert result['t']['missing_items'] and result['t']['rejected_claims']


def test_known_outside_scope_reference_keeps_gap_without_unrelated_retrieval():
    read=tools();read.reference_gaps=[]
    read.reads['e']['context']['reference_resolution']=[{'kind':'clause','number':'8.9.1',
        'status':'deferred_outside_scope','source_member_keys':['a']}]
    class Model:
        def call(self,system,user,budget):
            return {'tasks':[{'task_id':'t','claim_verdicts':[{'claim_id':'c','supported':True,
                'applicable':True,'conditions_preserved':True}],
                'retrieval_request':{'reason':'还须符合8.9.1条，但该条未提供','query_terms':['8.9.1条']}}]}
    result,_=review_answer_completeness('设备安装要求',[{'id':'t'}],[claim()],read,Model(),Budget(),facts={})
    assert result['t']['claim_ids']==['c'] and result['t']['external_dependency_pending']
    assert result['t']['retrieval_request']['outside_scope'] and not result['t']['complete']


def test_incomplete_answer_does_not_inherit_unused_candidates_reference_gaps():
    from turbine_kg.agent.runner import answer_source_ids
    task={'id':'t','required':True,'answer_coverage':'incomplete','candidate_evidence_ids':['unused'],
          'coverage_supports':[{'evidence_id':'unused','member_keys':['m']}],
          'answer_missing_items':[{'supports':[{'evidence_id':'needed','member_keys':['n']}]}]}
    assert answer_source_ids([task],[claim()])=={'e','needed'}


def test_many_knowledge_directions_are_one_real_question():
    kinds=['anomaly','cause','inspection','requirement','measure','procedure','verification']
    spec={'object':'凝汽器','subquestion':'凝汽器安装时壳体变形怎么办','activity':'安装','knowledge_items':kinds}
    registry=OntologyRegistry()
    tasks,_=plan_tasks({'objects':['凝汽器'],'object_tasks':[spec]},Session(),registry)
    assert len(tasks)==1 and tasks[0]['knowledge_items']==kinds
    assert set(tasks[0]['relation_ids'])==set(registry.navigation(kinds))
    tasks,_=plan_tasks({'objects':['凝汽器'],'object_tasks':[spec,{**spec,'subquestion':'凝汽器支座如何安装'}]},Session(),registry)
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
    accepted,rejected=validate([restatement],tools(),tasks=[task])
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
    accepted,rejected=validate([inference],tools(),tasks=[{'id':'t'}])
    assert len(accepted)==1 and not rejected


def test_numeric_values_do_not_block_but_invalid_references_still_do():
    from turbine_kg.agent.claims import binding_error
    numeric={**claim(),'basis':'inference','text':'建议将间隙调整到100mm。'}
    assert binding_error(numeric,sources(),{}) is None
    invented={**claim(),'basis':'inference','text':'建议按第9.9.9条执行。'}
    assert binding_error(invented,sources(),{}) is not None
    # File titles are not authoritative standard IDs; their meaning belongs
    # to the full-source semantic review, along with engineering correctness.
    identifier={**claim(),'basis':'inference','text':'建议改为DL/T 9876—2030中的工艺。'}
    assert binding_error(identifier,sources(),{}) is None


def test_inference_can_restate_user_values_without_a_matching_source_value():
    inference={**claim(),'basis':'inference','text':'根据你给出的4mm/m，建议先核实测量部位。'}
    accepted,rejected=validate([inference],tools(),
                               tasks=[{'id':'t','object':'设备'}])
    assert len(accepted)==1 and not rejected


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
        def snapshot(self,*args):return Snapshot((),registry.version)
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


def test_unit_and_numeric_meaning_is_left_to_semantic_review():
    from turbine_kg.agent.claims import binding_error
    reads=sources();reads['e']['members'][0]['quote']='偏差应不大于10mm。'
    assert binding_error({**claim(),'text':'偏差不大于1cm。'},reads,{}) is None
    for text in ('偏差不大于10m。','还应检查11个点。'):
        advice={**claim(),'text':text}
        assert binding_error(advice,reads,{}) is None


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


def test_answer_operations_replace_by_id_and_append_only_an_explicit_gap():
    from turbine_kg.agent.claims import apply_answer_operations
    original=claim();original['text']='设备清洁要求待进一步核实。'
    replacement={**claim(),'text':'设备安装前应清洁。'}
    addition={**claim(),'text':'部件应牢固。','supports':[{'evidence_id':'e','member_keys':['b']}]}
    updated,failed=apply_answer_operations([original],{'operations':[
        {'op':'replace','claim_id':'c','claim':replacement},
        {'op':'add','missing_item_id':'g','claim':addition}]},tools(),
        tasks=[{'id':'t'}],missing_items=[{'id':'g','task_id':'t'}])
    assert not failed and len(updated)==2
    assert updated[0]['id']=='c' and updated[0]['text']==replacement['text']
    assert updated[1]['id']!='c' and updated[1]['text']==addition['text']
    assert original['text']=='设备清洁要求待进一步核实。'


def test_invalid_answer_operations_keep_existing_advice_and_valid_independent_additions():
    from turbine_kg.agent.claims import apply_answer_operations
    original=claim();bad={**original,'supports':[{'evidence_id':'e','member_keys':['missing']}]}
    addition={**original,'text':'部件应牢固。','supports':[{'evidence_id':'e','member_keys':['b']}]}
    operations=[{'op':'replace','claim_id':'c','claim':bad},
        {'op':'replace','claim_id':'absent','claim':original},
        {'op':'replace','claim_id':'c','claim':original},
        {'op':'add','missing_item_id':'absent','claim':addition},
        {'op':'add','missing_item_id':'g','claim':{**addition,'task_id':'other'}},
        {'op':'add','missing_item_id':'g','claim':addition}]
    updated,failed=apply_answer_operations([original],{'operations':operations},tools(),
        tasks=[{'id':'t'},{'id':'other'}],missing_items=[{'id':'g','task_id':'t'}])
    assert len(failed)==5 and updated[0]==original and len(updated)==2


def test_legacy_full_answer_is_not_appended_and_empty_patch_is_a_noop():
    from turbine_kg.agent.claims import apply_answer_operations
    original=claim();kwargs=dict(tasks=[{'id':'t'}],missing_items=[])
    updated,failed=apply_answer_operations([original],{'claims':[original]},tools(),**kwargs)
    assert updated==[original] and failed
    assert apply_answer_operations([original],{'operations':[]},tools(),**kwargs)==([original],[])


def test_rejected_claim_can_be_replaced_and_exact_duplicate_add_does_not_create_a_slot():
    from turbine_kg.agent.claims import apply_answer_operations
    original=claim();rejected={**original,'id':'old','text':'错误表述'}
    updated,failed=apply_answer_operations([original],{'operations':[
        {'op':'add','missing_item_id':'g','claim':original},
        {'op':'replace','claim_id':'old','claim':{**original,'text':'部件应牢固。',
            'supports':[{'evidence_id':'e','member_keys':['b']}]}}]},tools(),
        tasks=[{'id':'t'}],missing_items=[{'id':'g','task_id':'t'}],
        rejected=[{'claim':rejected,'reason':'不适用'}])
    assert not failed and [row['id'] for row in updated]==['c','old']
