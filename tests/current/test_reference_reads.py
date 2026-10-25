"""Pinned-scope references are queued before their original evidence is read."""
from types import SimpleNamespace
from copy import deepcopy
import json
from turbine_kg.documents.ir import parse_references
from turbine_kg.ontology.registry import OntologyRegistry
from turbine_kg.retrieval.tools import Tools
from turbine_kg.runtime import Budget, BudgetExceeded, Snapshot


def source(key, text, page=1, section='7 Test'):
    return {'id':key,'text':text,'kind':'paragraph','locator':{'physical_page':page},
            'section':section,'order':0,'document_id':'d'}


def evidence(key, number, source_key, page=1, section='7 Test', bounds=None):
    return {'id':key,'document_id':'d','revision_id':'r','kind':'text',
            'context':{'clause_number':number,'section':section,'primary_page':page,'clause_source_range':bounds},
            'members':[{'source_id':source_key}]}


def tools_for(sources, records):
    class Store:
        def get(self, ids, documents, budget):
            assert documents == ('d',)
            return [row for row in sources if row['id'] in ids]
    tools=Tools(Store(),OntologyRegistry(),Snapshot(('d',),'v'),Budget())
    tools._scope_records=records
    def read(key):
        tools._take()
        row={'evidence_id':key}
        tools.reads[key]=row
        tools.pending_reference_ids.discard(key)
        return {'items':[row]}
    tools.read_evidence=read
    return tools


def view(text, page=1, section='7 Test'):
    return {'evidence_id':'origin','document_id':'d','revision_id':'r',
            'context':{'primary_page':page,'section':section,'references':parse_references(text)},
            'members':[{'quote':text,'role':'content','locator':{'physical_page':page}}]}


def test_cue_reference_lists_complete_group_and_section_in_order():
    sources=[source('a','7.2.3 要求。'), source('b','7.4.1 要求。'), source('c','7.4.2 要求。')]
    records=[evidence('ea','7.2.3','a'),evidence('eb','7.4.1','b'),evidence('ec','7.4.2','c')]
    tools=tools_for(sources,records)
    result=tools._follow_references(view('应符合7.2.3 的规定，并参照7.4的规定。'))
    assert [row['evidence_id'] for row in result] == ['ea','eb','ec']
    assert tools.budget.calls == 0 and tools.pending_reference_ids=={'ea','eb','ec'}
    assert not tools.reads and not tools.reference_gaps
    assert not tools._follow_references(view('应符合7.2.3 的规定，并参照7.4的规定。'))


def test_section_reference_preserves_all_targets_without_a_model_prescreen():
    sources=[source(str(i),f'7.4.{i} 对象{i}安装要求。') for i in range(1,5)]
    records=[evidence(f'e{i}',f'7.4.{i}',str(i)) for i in range(1,5)]
    for record in records:record['members'][0]['role']='content'
    tools=tools_for(sources,records)
    origin=view('设备安装应符合7.4节规定。')
    class Model:
        def call(self,system,user,budget,**kwargs):
            raise AssertionError('引用展开不能触发额外模型核验')
    tools.model=Model()
    targets=tools._follow_references(origin)
    assert [row['evidence_id'] for row in targets]==['e1','e2','e3','e4']
    assert tools.pending_reference_ids=={'e1','e2','e3','e4'}
    assert tools.required_reference_ids=={'e1','e2'}


def test_multiclause_source_selects_correct_evidence_and_defers_outside_scope():
    text='7.2.3 要求。\n7.2.4 其他要求。'
    start=text.index('7.2.4')
    records=[evidence('ea','7.2.3','a',bounds=[0,start]), evidence('eb','7.2.4','a',bounds=[start,len(text)])]
    tools=tools_for([source('a',text)],records)
    result=tools._follow_references(view('应符合7.2.4，并按9.1.2的规定。'))
    assert [row['evidence_id'] for row in result] == ['eb']
    assert len(tools.reference_gaps) == 1 and tools.reference_gaps[0]['kind'] == 'reference_deferred'


def test_external_document_never_reads_same_number_local_clause():
    tools=tools_for([source('a','7.2.3 要求。')],[evidence('ea','7.2.3','a')])
    assert not tools._follow_references(view('应符合DL/T 9999的7.2.3条。'))
    assert tools.reference_gaps[0]['kind']=='reference_deferred'
    assert tools.budget.calls==0


def test_duplicate_clause_uses_source_section_and_keeps_real_ambiguity():
    tools=tools_for([source('a','7.2.3 甲。',section='A'), source('b','7.2.3 乙。',section='B')],
                    [evidence('ea','7.2.3','a',section='A'), evidence('eb','7.2.3','b',section='B')])
    assert tools._follow_references(view('按7.2.3条。',section='B'))[0]['evidence_id']=='eb'
    tools._references_seen.clear()
    assert not tools._follow_references(view('按7.2.3条。',section=''))
    assert tools.reference_gaps[-1]['kind']=='reference_ambiguous'


def test_reference_listing_does_not_spend_read_budget():
    tools=tools_for([source('a','7.2.3 要求。')],[evidence('ea','7.2.3','a')])
    def fail(key): raise BudgetExceeded('budget')
    tools.read_evidence=fail
    assert [row['evidence_id'] for row in tools._follow_references(view('见7.2.3。'))]==['ea']
    assert not tools.reference_gaps and tools.budget.calls==0
    assert tools.pending_reference_ids=={'ea'}


def test_resolved_ids_read_existing_target_without_copying_or_rebuilding_source_index():
    tools=tools_for([], [evidence('existing','7.2.3','target-source')])
    origin=view('按7.2.3条。')
    origin['context']['reference_resolution']=[{'kind':'clause','number':'7.2.3','status':'resolved','target_evidence_ids':['existing']}]
    before=deepcopy(origin)
    tools.store.get=lambda *args: (_ for _ in ()).throw(AssertionError('Native reference should not rebuild source index'))
    assert [row['evidence_id'] for row in tools._follow_references(origin)]==['existing']
    assert origin==before
    assert not tools._source_indexes and tools.summary[-1]['method']=='REFERENCES'


def test_native_references_edges_use_pinned_document_and_existing_target_ids():
    tools=tools_for([], [evidence('existing','7.2.3','target-source')])
    calls=[]
    def query(cypher, parameters, budget):
        calls.append((cypher,parameters))
        return [{'evidence_id':'existing','reference':{'document_id':'d','data_json':json.dumps({'reference_kind':'clause','number':'7.2.3','inherit_values':False})}}]
    tools.store.query=query
    tools.store.get=lambda *args: (_ for _ in ()).throw(AssertionError('Native edge should not rebuild index'))
    assert [row['evidence_id'] for row in tools._follow_references(view('应符合7.2.3的规定。'))]==['existing']
    assert calls[0][1]=={'id':'origin','document':'d'}
    assert 'target.document_id=$document' in calls[0][0]
    assert 'coalesce(r.document_id,origin.document_id)=origin.document_id' in calls[0][0]
    assert tools.budget.calls==0


def test_native_section_reference_retains_all_child_target_ids_in_budget_queue():
    tools=tools_for([], [evidence('first','7.4.1','a'),evidence('second','7.4.2','b')])
    origin=view('按7.4的规定。')
    origin['context']['reference_resolution']=[{'kind':'clause','number':'7.4','status':'resolved','target_evidence_ids':['first','second']}]
    tools.round_call_ceiling=1
    assert [row['evidence_id'] for row in tools._follow_references(origin)]==['first','second']
    assert tools.pending_reference_ids=={'first','second'}
    assert not tools.reference_gaps and tools.budget.calls==0


def test_section_reference_reads_clause_order_even_when_storage_order_is_reversed():
    tools=tools_for([], [evidence('later','7.4.3','c'),evidence('earlier','7.4.1','a')])
    origin=view('按7.4的规定。')
    origin['context']['reference_resolution']=[{'kind':'clause','number':'7.4','status':'resolved','target_evidence_ids':['later','earlier']}]
    assert [row['evidence_id'] for row in tools._follow_references(origin)]==['earlier','later']


def test_section_reference_first_reads_are_bounded_and_graph_ranked():
    records=[evidence(f'e{i}',f'7.4.{i}',f's{i}') for i in range(1,6)]
    tools=tools_for([],records)
    tools.ontology_paths={'设备':{'class-id':{'kind':'direct_class','path':[]}}}
    tools.focus_tasks=[{'object':'设备','relation_ids':['has_requirement']}]
    graph_queries=[]

    def query(cypher,parameters,budget):
        if 'MATCH (s:TK:Statement)-[:SUPPORTS]' in cypher:
            graph_queries.append((cypher,parameters))
            return [{'evidence_id':'e4','statements':['设备安装要求'],
                     'matched_object_ids':['class-id']}]
        return []

    tools.store.query=query
    origin=view('设备安装应符合7.4节的规定。')
    origin['context']['reference_resolution']=[{'kind':'clause','number':'7.4','status':'resolved',
                                               'target_evidence_ids':[row['id'] for row in records]}]
    targets=tools._follow_references(origin)
    assert [row['evidence_id'] for row in targets]==['e4','e1','e2','e3','e5']
    assert tools.required_reference_ids=={'e4','e1'}
    assert tools.pending_reference_ids=={'e1','e2','e3','e4','e5'}
    assert tools.pending_reference_order==['e4','e1']
    assert not tools.reads and tools.budget.calls==0
    assert graph_queries and 's:TK:Statement' in graph_queries[0][0]
    assert graph_queries[0][1]['objects']==['class-id']
    assert graph_queries[0][1]['documents']==['d']


def test_declared_external_scope_reference_does_not_reparse_local_same_number():
    tools=tools_for([source('a','7.2.3 本地同编号要求。')],[evidence('local','7.2.3','a')])
    origin=view('按7.2.3条。')
    origin['context']['reference_resolution']=[{'kind':'clause','number':'7.2.3','status':'deferred_outside_scope','target_evidence_ids':[]}]
    assert not tools._follow_references(origin)
    assert tools.reference_gaps[-1]['kind']=='reference_deferred'
    assert tools.reference_gaps[-1]['origin_evidence_id']=='origin'
    assert not tools._source_indexes


def test_native_target_outside_pinned_scope_is_not_read():
    tools=tools_for([], [])
    origin=view('按7.2.3条。')
    origin['context']['reference_resolution']=[{'kind':'clause','number':'7.2.3','status':'resolved','target_evidence_ids':['foreign-target']}]
    assert not tools._follow_references(origin)
    assert not tools.reads and not tools.pending_reference_ids
    assert all(gap['origin_evidence_id']=='origin' for gap in tools.reference_gaps)


def test_native_cycle_queues_each_existing_evidence_without_recursive_read():
    records=[evidence('a','7.2.3','sa'),evidence('b','7.2.4','sb')]
    views={}
    for key, own, target_number, target in [('a','source-a','7.2.4','b'),('b','source-b','7.2.3','a')]:
        row=view('按'+target_number+'条。')
        row.update(evidence_id=key,kind='text',title='Test')
        row['members'][0].update(member_key=own,source_id=own,quality='readable',attributes={})
        row['context']['reference_resolution']=[{'kind':'clause','number':target_number,'status':'resolved','target_evidence_ids':[target]}]
        views[key]=row
    class Store:
        def query(self,cypher,parameters,budget):
            if 'RETURN properties(s) AS node' in cypher:return []
            if 'r:REFERENCES' in cypher:return []
            return [{'evidence':{'id':parameters['id'],'semantic_coverage':{'status':'complete'}},'page_semantic_status':'complete'}]
    tools=Tools(Store(),OntologyRegistry(),Snapshot(('d',),'v'),Budget())
    tools._scope_records=records
    tools.assembler=SimpleNamespace(read=lambda key,*args:views[key])
    result=tools.read_evidence('a')
    assert [row['evidence_id'] for row in result['items']]==['a']
    assert [row['evidence_id'] for row in result['references']]==['b']
    assert tools.budget.calls==1 and set(tools.reads)=={'a'}
    assert [member['source_id'] for member in tools.reads['a']['members']]==['source-a']
    assert tools.pending_reference_ids=={'b'}
    second=tools.read_evidence('b')
    assert second['references']==[]
    assert [member['source_id'] for member in tools.reads['b']['members']]==['source-b']
    assert not tools.pending_reference_ids and not tools.reference_gaps


def table_row_evidence(key, table_id, number):
    return {'id':key,'document_id':'d','revision_id':'r','kind':'table',
            'context':{'table_id':table_id,'table_number':number,'table_title':'表'+number,'section':'7 Test','primary_page':1},
            'members':[]}


def test_declared_self_reference_skips_source_index_and_other_table_row_groups():
    records=[table_row_evidence(str(i),'table','7.2.4') for i in range(4)]
    tools=tools_for([],records)
    origin=view('表7.2.4 应符合表7.2.4的规定。')
    origin['context'].update(table_id='table',reference_resolution=[{'kind':'table','number':'7.2.4','status':'self_reference','target_evidence_ids':[]}])
    tools.store.get=lambda *args: (_ for _ in ()).throw(AssertionError('Self reference must not use SourceIndex'))
    assert not tools._follow_references(origin)
    assert not tools.reads and not tools.reference_gaps and not tools.pending_reference_ids


def test_legacy_same_table_reference_does_not_read_other_row_groups():
    tools=tools_for([],[table_row_evidence(str(i),'table','7.2.4') for i in range(4)])
    origin=view('按表7.2.4的规定。');origin['context']['table_id']='table'
    assert not tools._follow_references(origin)
    assert not tools.reads and not tools.reference_gaps and not tools.pending_reference_ids


def test_body_reference_to_table_queues_all_four_existing_row_groups():
    tools=tools_for([],[table_row_evidence(str(i),'table','7.2.4') for i in range(4)])
    result=tools._follow_references(view('按表7.2.4的规定。'))
    assert [row['evidence_id'] for row in result]==['0','1','2','3']
    assert tools.budget.calls==0 and tools.pending_reference_ids=={'0','1','2','3'}
    assert not tools.reference_gaps


def test_legacy_same_picture_is_skipped_but_another_picture_is_read():
    records=[{'id':key,'document_id':'d','revision_id':'r','kind':'image',
              'context':{'picture_id':key,'figure_title':'图'+number,'section':'7 Test','primary_page':1},'members':[]}
             for key,number in [('own-picture','7.1'),('other-picture','7.2')]]
    tools=tools_for([],records)
    origin=view('见图7.1和图7.2。');origin['context']['picture_id']='own-picture'
    result=tools._follow_references(origin)
    assert [row['evidence_id'] for row in result]==['other-picture']
    assert tools.budget.calls==0 and not tools.reference_gaps
