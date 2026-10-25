"""Known graph source targets are read without another model planning round."""
import json
import pytest

from turbine_kg.agent.runner import Agent
from turbine_kg.ontology.registry import OntologyRegistry
from turbine_kg.retrieval.tools import Tools
from turbine_kg.runtime import Budget, Snapshot


def reference(payload,evidence_id):
    evidence=next(e for e in payload['evidence_catalog'] if e['title']==evidence_id)
    return next((s['id'] for s in payload['statements'] if evidence['id'] in s['evidence_refs']),evidence['id'])


def run_discovery(monkeypatch, *, known_sources, unused_draft=False, empty_repair=False, needs_expansion=False):
    registry=OntologyRegistry()
    events=[]

    def item(evidence_id, *, related=False):
        return {'id':'statement-'+evidence_id,'type':'requirement','text':'设备安装应清洁。',
                'entities':[{'class_id':'Equipment','label':'设备','role':'subject'}],
                'ontology_paths':[{'kind':'related_context' if related else 'direct_class'}],
                'supports':[{'evidence_id':evidence_id,'member_keys':['member-'+evidence_id]}]}

    class Store:
        def snapshot(self,*args):return Snapshot(('d',),registry.version)
        def query(self,*args,**kwargs):return []

    class SourceTools(Tools):
        def navigate(self,object_label,relation_ids,**kwargs):
            self._take()
            if needs_expansion:
                events.append(('navigate',kwargs.get('expand',False)))
                if not kwargs.get('expand'):return {'items':[],'cursor':None,'expansion_available':True}
            return {'items':[item('root'),item('weak',related=True)] if known_sources else [],'cursor':None}

        def search_evidence(self,query,**kwargs):
            self._take()
            return {'items':[{'evidence_id':'root','statements':[item('root')]}]
                    if not known_sources and query=='补查' else [],'cursor':None}

        def read_evidence(self,evidence_id):
            self._take();events.append(('read',evidence_id))
            view={'evidence_id':evidence_id,'document_id':'d','revision_id':'r','title':evidence_id,
                  'kind':'text','context':{},'members':[{
                      'member_key':'member-'+evidence_id,'source_id':'source-'+evidence_id,
                      'quote':'设备安装应清洁。','role':'content','text_origin':'native_text',
                      'locator':{'physical_page':1}}]}
            self.reads[evidence_id]=view
            self.pending_reference_ids.discard(evidence_id)
            self.required_reference_ids.discard(evidence_id)
            if known_sources and evidence_id=='root':
                references=['reference-'+str(i) for i in range(5)]
                self.pending_reference_ids.update(references)
                self.required_reference_ids.update(references)
                self.pending_reference_order.extend(references)
            return {'items':[view],'cursor':None}

    class Model:
        reviews=0
        def call(self,system,user,budget,**kwargs):
            payload=json.loads(user)
            if '理解原始问题' in system:
                return {'objects':['设备'],'knowledge_items':['requirement']}
            if 'registered_relations' in payload:
                events.append(('planner',None))
                if known_sources:raise AssertionError('已有原文或已解析引用，不应请求模型规划')
                return {'calls':[{'tool':'search_evidence','parameters':{'query':'补查','query_terms':['设备']}}]}
            if 'task_contracts' in payload:
                assert empty_repair
                events.append(('repair',None))
                return {'operations':[],'gaps':[]}
            task=payload['tasks'][0]
            if '独立的回答核验请求' in system:
                self.reviews+=1;events.append(('review',None)) if unused_draft or empty_repair else None
                if empty_repair:assert self.reviews==1
                return {'tasks':[{'task_id':task['id'],'complete':True,'claim_verdicts':[
                    {'claim_id':claim['id'],'supported':True,'applicable':True,'conditions_preserved':True}
                    for claim in payload['claims']],
                    'missing_items':[{'reason':'还需补充一个必要分项','references':[reference(payload,'root')]}]
                        if empty_repair else []}]}
            claims=[{'id':'advice','task_id':task['id'],'text':'设备安装应清洁。','basis':'source',
                'references':[reference(payload,'root')]}]
            if unused_draft:claims.append({**claims[0],'id':'unused','text':'重复的无效草稿','references':[]})
            return {'claims':claims,'gaps':[]}

    monkeypatch.setattr('turbine_kg.agent.runner.Tools',SourceTools)
    answer=Agent(Store(),registry=registry,model=Model()).answer(
        '设备安装有哪些要求',budget=Budget(seconds=100,calls_per_round=4,max_rounds=4))
    return answer,events


def test_resolved_references_precede_weak_candidates_without_planning(monkeypatch):
    answer,events=run_discovery(monkeypatch,known_sources=True)
    assert events==[('read','root'),*[('read','reference-'+str(i)) for i in range(5)]]
    assert not answer.search_summary['errors']
    assert answer.search_summary['source_counts']['read_evidence']==6


def test_planner_is_used_when_discovery_has_no_readable_target(monkeypatch):
    answer,events=run_discovery(monkeypatch,known_sources=False)
    assert events==[('planner',None),('read','root')]
    assert not answer.search_summary['errors']
    assert answer.claims


def test_missing_own_class_source_triggers_deferred_expansion(monkeypatch):
    answer,events=run_discovery(monkeypatch,known_sources=True,needs_expansion=True)
    assert [e for e in events if e[0]=='navigate']==[('navigate',False),('navigate',True)]
    assert answer.claims and not answer.search_summary['errors']


def test_unused_rejected_draft_is_removed_without_a_repair_loop(monkeypatch):
    answer,events=run_discovery(monkeypatch,known_sources=True,unused_draft=True)
    assert answer.retrieval_status=='answered' and not answer.gaps
    assert 'repair' not in answer.search_summary['stage_seconds']
    assert events.count(('review',None))==1
    assert not any(d['kind']=='discarded_claim' for d in answer.search_summary['diagnostics'])


def test_empty_repair_does_not_repeat_semantic_review(monkeypatch):
    answer,events=run_discovery(monkeypatch,known_sources=True,empty_repair=True)
    assert events.count(('repair',None))==1 and events.count(('review',None))==1
    assert answer.claims and answer.retrieval_status=='partial'


def test_multiple_objects_read_their_sources_before_draining_graph_pages(monkeypatch):
    registry=OntologyRegistry();events=[]
    def item(obj):
        return {'id':'s-'+obj,'type':'requirement','text':obj+'应符合要求。','entities':[],
                'ontology_paths':[{'kind':'direct_class'}],
                'supports':[{'evidence_id':obj,'member_keys':['m-'+obj]}]}
    class Store:
        def snapshot(self,*args):return Snapshot(('d',),registry.version)
        def query(self,*args,**kwargs):return []
    class SourceTools(Tools):
        def search_evidence(self,query,query_terms,**kwargs):
            self._take();obj=query_terms[0]
            return {'items':[{'evidence_id':obj,'statements':[item(obj)]}],'cursor':None}
        def navigate(self,object_label,relation_ids,cursor=None,**kwargs):
            self._take();events.append(('navigate',object_label,cursor))
            return {'items':[item(object_label)],'cursor':{'key':object_label,'offset':40}}
        def read_evidence(self,evidence_id):
            self._take();events.append(('read',evidence_id,None))
            view={'evidence_id':evidence_id,'title':evidence_id,'document_id':'d','revision_id':'r',
                'kind':'text','context':{},'statements':[item(evidence_id)],'members':[{
                'member_key':'m-'+evidence_id,'source_id':'src-'+evidence_id,'quote':evidence_id+'应符合要求。',
                'role':'content','text_origin':'native_text','locator':{'physical_page':1}}]}
            self.reads[evidence_id]=view
            return {'items':[view],'cursor':None}
    class Model:
        def call(self,system,user,budget,**kwargs):
            p=json.loads(user)
            if '理解原始问题' in system:return {'objects':['对象甲','对象乙'],'knowledge_items':['requirement']}
            if '独立的回答核验请求' in system:
                return {'tasks':[{'task_id':t['id'],'claim_verdicts':[{'claim_id':c['id'],'supported':True}
                    for c in p['claims'] if c['task_id']==t['id']]} for t in p['tasks']]}
            assert 'registered_relations' not in p
            return {'claims':[{'text':t['object']+'应符合要求。','task_id':t['id'],'basis':'source',
                'references':[reference(p,t['object'])]} for t in p['tasks']],'gaps':[]}
    monkeypatch.setattr('turbine_kg.agent.runner.Tools',SourceTools)
    result=Agent(Store(),model=Model(),registry=registry).answer('比较对象甲与对象乙的要求')
    assert result.retrieval_status=='answered' and not result.search_summary['errors']
    assert {e[1] for e in events if e[0]=='read'}=={'对象甲','对象乙'}
    assert not any(e[2] for e in events if e[0]=='navigate')
    assert all(t['evidence_ids']==[t['object']] for t in result.search_summary['task_coverage'])


@pytest.mark.parametrize('mode',['known','search','missing_source','exhausted','bounded','reused','local_repair','outside_scope'])
def test_review_routes_back_to_retrieval_on_shared_budget_and_preserves_advice(monkeypatch,mode):
    registry=OntologyRegistry();events=[];model_budgets=[]
    quote={'root':'设备应清洁。设备试运应保持运行平稳。','extra':'设备试运前应检查阀门。'}
    if mode in ('reused','local_repair'):quote['root']+=quote['extra']
    if mode=='outside_scope':quote['root']+='设备试运前还须符合8.9.1条。'

    def item(eid,related=False):
        return {'id':'s-'+eid,'text':'部件应检查。' if related else quote['root'],'type':'requirement',
                'entities':[],'ontology_paths':[{'object_id':'equipment','kind':'related_context' if related else 'direct_class'}],
                'supports':[{'evidence_id':eid,'member_keys':['m-'+eid]}]}

    class Store:
        def snapshot(self,*args):return Snapshot(('d',),registry.version)
        def query(self,*args,**kwargs):return []

    class SourceTools(Tools):
        def navigate(self,*args,**kwargs):
            self._take()
            return {'items':[item('root'),*([item('extra',True)] if mode=='known' else [])],'cursor':None}
        def search_evidence(self,query,**kwargs):
            self._take();events.append(('search',query))
            found=(mode in ('search','missing_source','reused') and query!=question
                   or mode=='bounded' and query!=question)
            eid='root' if mode=='reused' else 'extra'+str(sum(event[0]=='search' for event in events)) if mode=='bounded' else 'extra'
            if found and eid!='root':quote[eid]=quote['extra']
            return {'items':[{'evidence_id':eid,'exact_hits':2,'retrieval_method':'evidence_fulltext',
                              'matched_phases':['试运前'],'matched_object_labels':['设备'],
                              'statements':[]}] if found else [],'cursor':None}
        def read_evidence(self,evidence_id):
            self._take();events.append(('read',evidence_id))
            view={'evidence_id':evidence_id,'document_id':'d','revision_id':'r','title':evidence_id,'kind':'text',
                  'context':{},'members':[{'member_key':'m-'+evidence_id,'source_id':'src-'+evidence_id,
                    'quote':quote[evidence_id],'role':'content','text_origin':'native_text','locator':{'physical_page':1}}]}
            self.reads[evidence_id]=view
            if mode=='outside_scope':view['context']['reference_resolution']=[{
                'kind':'clause','number':'8.9.1','status':'deferred_outside_scope',
                'source_member_keys':['m-root']}]
            return {'items':[view],'cursor':None}

    class Model:
        def call(self,system,user,budget,**kwargs):
            model_budgets.append(budget);payload=json.loads(user)
            if '理解原始问题' in system:
                return {'objects':['设备'],'object_tasks':[{'object':'设备','phase':'试运前','query_terms':['试运前'],'knowledge_items':['requirement']}]}
            if 'registered_relations' in payload:
                events.append(('planner',None));assert payload['review_feedback']
                if mode=='known':
                    candidate=next(row for row in payload['source_candidates'] if row['evidence_id']=='extra')
                    assert candidate['statement_previews'] and not candidate['read']
                    return {'calls':[{'tool':'read_evidence','parameters':{'evidence_id':'extra'}}]}
                return {'calls':[{'tool':'search_evidence','parameters':{
                    'query':'补查'+str(len(payload['read_evidence_ids'])),'query_terms':['设备','试运前']}}]}
            if 'task_contracts' in payload:
                if mode!='local_repair':return {'operations':[]}
                return {'operations':[{'op':'replace','claim_id':'wrong','claim':{'task_id':payload['task_contracts'][0]['id'],
                    'text':quote['extra'],'basis':'source',
                    'references':[reference(payload,'root')]}}]}
            task=payload['tasks'][0];sources={s.get('evidence_id',s['title']) for s in payload.get('sources',payload.get('evidence_catalog',[]))}
            if '独立的回答核验请求' in system:
                events.append(('review',tuple(sorted(sources))))
                row={'task_id':task['id'],'claim_verdicts':[
                    {'claim_id':c['id'],'supported':True,'applicable':c['text']!='设备试运应保持运行平稳。','conditions_preserved':True}
                    for c in payload['claims']]}
                if mode in ('missing_source','bounded','exhausted') and (mode=='bounded' or sources=={'root'}):
                    row['retrieval_request']={'reason':'尚缺用户阶段的依据','query_terms':['准备'+str(len(sources))]}
                if mode=='local_repair':row['retrieval_request']=None
                if mode=='outside_scope':row['retrieval_request']={
                    'reason':'尚缺8.9.1条的正文','query_terms':['8.9.1条']}
                return {'tasks':[row]}
            events.append(('generate',tuple(sorted(sources))))
            stable={'id':'stable','task_id':task['id'],'text':'设备应清洁。','basis':'source',
                    'references':[reference(payload,'root')]}
            def update(text,eid):
                operation={'op':'add','missing_item_id':payload['missing_items'][0]['id']}
                if any(r.get('claim',{}).get('id')=='wrong' for r in payload.get('rejected',[])):
                    operation={'op':'replace','claim_id':'wrong'}
                return {'operations':[{**operation,'claim':{**stable,'text':text,
                    'references':[reference(payload,eid)]}}],'gaps':[]}
            if mode=='reused' and payload.get('review_feedback'):
                return update(quote['extra'],'root')
            if sources=={'root'}:
                if payload.get('accepted_claims'):return {'operations':[],'gaps':[]}
                bad={**stable,'id':'wrong','text':'设备试运应保持运行平稳。'}
                return {'claims':[stable,*([] if mode in ('missing_source','bounded','outside_scope') else [bad])],'gaps':[]}
            assert any(c['id']=='stable' for c in payload['accepted_claims'])
            assert payload['review_feedback'] and task['phase']=='试运前'
            eid=next(eid for eid in sources if eid!='root')
            return update(quote[eid],eid)

    monkeypatch.setattr('turbine_kg.agent.runner.Tools',SourceTools)
    question='设备试运前有哪些要求'
    budget=Budget(seconds=100,max_rounds=4,calls_per_round=4)
    result=Agent(Store(),registry=registry,model=Model()).answer(question,budget=budget)
    assert not result.search_summary['errors']
    assert all(actual is budget for actual in model_budgets)
    assert budget.calls<=budget.max_rounds*budget.calls_per_round
    assert any(c['id']=='stable' for c in result.claims)
    assert not any(c['text']=='设备试运应保持运行平稳。' for c in result.claims)
    feedback=[row for row in result.search_summary['diagnostics'] if row['kind']=='review_retrieval']
    assert {'generate','review','finish'}<=result.search_summary['stage_seconds'].keys()
    if mode in ('local_repair','outside_scope'):
        assert not feedback
        if mode=='local_repair':
            assert 'repair' in result.search_summary['stage_seconds']
            assert any(c['text']==quote['extra'] for c in result.claims) and not result.gaps
        else:
            assert 'repair' not in result.search_summary['stage_seconds']
            assert result.retrieval_status=='partial'
            assert any('8.9.1' in gap.get('reason','') for gap in result.gaps)
        return
    assert 1<=len(feedback)<=2
    if mode=='known':
        assert ('read','extra') in events and sum(event[0]=='search' for event in events)==1
    if mode=='reused':assert [event for event in events if event[0]=='read']==[('read','root')]
    if mode not in ('exhausted','bounded'):
        assert any(c['text']==quote['extra'] for c in result.claims) and not result.gaps
    else:
        assert result.retrieval_status=='partial'
        assert any(gap['kind']=='source_missing' for gap in result.gaps)
