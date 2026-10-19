"""Known graph source targets are read without another model planning round."""
import json

from turbine_kg.agent.runner import Agent
from turbine_kg.ontology.registry import OntologyRegistry
from turbine_kg.retrieval.tools import Tools
from turbine_kg.runtime import Budget, Snapshot


def run_discovery(monkeypatch, *, known_sources):
    registry=OntologyRegistry()
    events=[]

    def item(evidence_id, *, related=False):
        return {'id':'statement-'+evidence_id,'type':'requirement','text':'设备安装应清洁。',
                'entities':[{'class_id':'Equipment','label':'设备','role':'subject'}],
                'ontology_paths':[{'kind':'related_context' if related else 'direct_class'}],
                'supports':[{'evidence_id':evidence_id,'member_keys':['member-'+evidence_id]}]}

    class Store:
        def snapshot(self,*args):return Snapshot(('d',),registry.version,())
        def query(self,*args,**kwargs):return []

    class SourceTools(Tools):
        def navigate(self,object_label,relation_ids,**kwargs):
            self._take()
            return {'items':[item('root'),item('weak',related=True)] if known_sources else [],'cursor':None}

        def search_evidence(self,query,**kwargs):
            self._take()
            return {'items':[{'evidence_id':'root','statements':[item('root')]}]
                    if not known_sources and query=='补查' else [],'cursor':None}

        def read_evidence(self,evidence_id):
            self._take();events.append(('read',evidence_id))
            view={'evidence_id':evidence_id,'document_id':'d','revision_id':'r','title':'资料',
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
        def call(self,system,user,budget,**kwargs):
            payload=json.loads(user)
            if '理解原始问题' in system:
                return {'objects':['设备'],'knowledge_items':['requirement']}
            if 'registered_relations' in payload:
                events.append(('planner',None))
                if known_sources:raise AssertionError('已有原文或已解析引用，不应请求模型规划')
                return {'calls':[{'tool':'search_evidence','parameters':{'query':'补查','query_terms':['设备']}}]}
            task=payload['tasks'][0]
            if '独立的回答核验请求' in system:
                return {'tasks':[{'task_id':task['id'],'complete':True,'claim_verdicts':[
                    {'claim_id':claim['id'],'supported':True,'applicable':True,'conditions_preserved':True}
                    for claim in payload['claims']]}]}
            return {'claims':[{'id':'advice','task_id':task['id'],'text':'设备安装应清洁。','basis':'source',
                'supports':[{'evidence_id':'root','member_keys':['member-root']}]}],'gaps':[]}

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
