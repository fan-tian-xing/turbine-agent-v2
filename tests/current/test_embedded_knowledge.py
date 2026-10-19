from copy import deepcopy

import pytest
from rdflib import Namespace, RDF

from turbine_kg.knowledge.extract import persist, extract
from turbine_kg.ontology.registry import OntologyRegistry


class Store:
    def __init__(self): self.nodes={}; self.links={}
    def put(self,label,rows): self.nodes.setdefault(label,{}).update({row['id']:deepcopy(row) for row in rows})
    def edges(self,label,rows): self.links.setdefault(label,{}).update({row['key']:deepcopy(row) for row in rows})
    def get(self,ids,documents=None,budget=None):
        return [deepcopy(row) for label in ('Statement','Evidence') for row in self.nodes.get(label,{}).values() if row['id'] in ids and (documents is None or row.get('document_id') in documents)]


def statement(evidence='e',**extra):
    return {'text':'Object inspection is required.','type':'inspection','entities':[{'label':'Object','class':'Mention','role':'subject'}],
            'supports':[{'evidence_id':evidence,'member_keys':['source-note'],'quotes':[{'member_key':'source-note','text':'Object inspection is required.'}]}],**extra}


def test_repeated_fact_merges_wrappers_and_separate_batches_without_duplicate_fields():
    store=Store();registry=OntologyRegistry()
    first=statement(conditions=[{'text':'Cold state','expression':None}], quantities=[{'target':'Object','value':1}])
    second=statement('another-wrapper',conditions=deepcopy(first['conditions']),quantities=deepcopy(first['quantities']))
    assert persist(store,[first,second],'d',registry)==1
    assert set(store.nodes)=={'Statement'}
    saved=next(iter(store.nodes['Statement'].values()))
    assert len(saved['supports'])==2 and len(saved['quantities'])==1
    assert len(saved['quantities'][0]['source_supports'])==2
    persist(store,[statement('third-wrapper',conditions=deepcopy(first['conditions']),quantities=deepcopy(first['quantities']))],'d',registry)
    assert len(store.nodes['Statement'])==1 and len(next(iter(store.nodes['Statement'].values()))['supports'])==3
    assert len(store.links['SUPPORTS'])==3
    assert len(first['supports'])==1


def test_distinct_stage_or_source_occurrence_is_preserved():
    registry=OntologyRegistry();store=Store()
    first=statement(conditions=[{'text':'Before run','expression':None}])
    second=statement(conditions=[{'text':'During run','expression':None}])
    third=statement(supports=[{'evidence_id':'different','member_keys':['different-source'],'quotes':[{'member_key':'different-source','text':first['text']}]}])
    assert persist(store,[first,second,third],'d',registry)==3


def test_statement_relation_and_procedure_step_use_precise_supports():
    registry=OntologyRegistry();store=Store()
    refs=deepcopy(statement()['supports'])
    source=statement(extraction_id='procedure',type='procedure',semantic_relations=[
        {'relation_id':'has_step','source_ref':{'statement_id':'procedure'},
         'target_ref':{'statement_id':'step'},'supports':refs}])
    target=statement(extraction_id='step',type='step',text='First operation.')
    persist(store,[source,target],'d',registry)
    relationship=next(iter(store.links['HAS_STEP'].values()))
    assert store.nodes['Statement'][relationship['source']]['type']=='procedure'
    assert store.nodes['Statement'][relationship['target']]['type']=='step'
    assert relationship['data']['supports'][0]['member_keys']==['source-note']
    assert relationship['data']['inherit_values'] is False
    assert set(store.nodes)=={'Statement'}
    saved=store.nodes['Statement'][relationship['source']]
    assert 'extraction_id' not in saved
    assert all('source_id' in row and 'source_ref' not in row for row in saved['semantic_relations'])


def test_invalid_statement_endpoint_or_missing_relation_support_blocks_writes():
    store=Store();registry=OntologyRegistry()
    relation={'relation_id':'has_step','source_ref':{'statement_id':'a'},'target_ref':{'statement_id':'missing'},'supports':statement()['supports']}
    with pytest.raises(ValueError,match='未知'): persist(store,[statement(extraction_id='a',type='procedure',semantic_relations=[relation])],'d',registry)
    assert store.nodes=={} and store.links=={}
    relation['target_ref']={'statement_id':'b'};relation.pop('supports')
    with pytest.raises(ValueError):
        persist(store,[statement(extraction_id='a',type='procedure',semantic_relations=[relation]),
                       statement(extraction_id='b',type='step')],'d',registry)
    assert store.nodes=={} and store.links=={}


def test_procedure_step_relation_rejects_wrong_statement_kind_before_writes():
    store=Store();registry=OntologyRegistry()
    relation={'relation_id':'has_step','source_ref':{'statement_id':'a'},
              'target_ref':{'statement_id':'b'},'supports':statement()['supports']}
    with pytest.raises(ValueError):
        persist(store,[statement(extraction_id='a',type='procedure',semantic_relations=[relation]),
                       statement(extraction_id='b',type='requirement')],'d',registry)
    assert store.nodes=={} and store.links=={}


def test_embedded_values_project_only_statement_type_and_keep_exact_source():
    registry=OntologyRegistry();store=Store()
    persist(store,[statement(conditions=[{'text':'Cold state','expression':None}],formulas=[{'expression':'x/y'}])],'d',registry)
    saved=next(iter(store.nodes['Statement'].values()))
    projected=registry.statement_projection([saved])
    assert registry.validate_graph(projected)[0]
    ns=Namespace(registry.data['namespace'])
    assert set(projected)=={(ns[saved['id']],RDF.type,ns['Statement'])}
    assert set(store.nodes)=={'Statement'}
    for field in ('conditions','formulas'):
        assert saved[field][0]['source_supports']==[{'evidence_id':ref['evidence_id'],'member_keys':ref['member_keys']} for ref in saved['supports']]


@pytest.mark.parametrize('field', ['conditions','quantities','formulas'])
@pytest.mark.parametrize('value', ['not-an-array',[None]])
def test_malformed_embedded_fields_are_rejected_before_writes(field,value):
    registry=OntologyRegistry();store=Store()
    item=statement(**{field:value})
    with pytest.raises(ValueError): persist(store,[item],'d',registry)
    assert store.nodes=={} and store.links=={}
    with pytest.raises(ValueError,match='对象数组'):
        registry.statement_projection([{**item,'id':'s'}])


def test_stable_approved_class_alias_identity_and_punctuation_fragment_rejection():
    registry=OntologyRegistry();before=registry.domain_class('支撑弹簧')['id'];registry.version='another-version'
    assert registry.domain_class('支持弹簧')['id']==before
    assert registry.canonical_label('支撑弹簧')==registry.domain_class('支持弹簧')['label']
    with pytest.raises(ValueError,match='碎片'):registry.validate_entity({'label':';','class':'Mention','role':'subject'})


def test_shared_note_anchors_ignore_other_row_group_members():
    registry=OntologyRegistry()
    views=[{'evidence_id':f'v{i}','kind':'table','context':{},'members':[
        {'member_key':'note','source_id':'same-note','role':'context','quote':'Object inspection is required.','locator':{'physical_page':1}},
        {'member_key':f'cell-{i}','source_id':f'cell-{i}','role':'content','quote':f'Other row {i}','locator':{'physical_page':1}}]} for i in range(2)]
    class Model:
        def call(self,system,payload,budget):
            assert not system.startswith('独立核验')
            return {'statements':[statement(f'e{i}',supports=[{'evidence_id':f'e{i}','member_keys':[f'e{i}m0',f'e{i}m1']}]) for i in range(2)],
                    'evidence_status':[{'evidence_id':f'e{i}','status':'complete'} for i in range(2)]}
    items,coverage=extract(views,Model(),registry)
    assert not coverage['rejected'] and len(items)==2
    assert items[0]['source_anchors']==items[1]['source_anchors']
    store=Store();assert persist(store,items,'d',registry)==1


def test_relation_is_removed_when_referenced_statement_fails_source_binding():
    view={'evidence_id':'real','kind':'text','context':{},'members':[
        {'member_key':'source-note','source_id':'source-note','role':'content','quote':'Object inspection is required.','locator':{'physical_page':1}}]}
    refs=[{'evidence_id':'e0','member_keys':['e0m0']}]
    relation={'relation_id':'has_step','source_ref':{'statement_id':'a'},'target_ref':{'statement_id':'b'},'supports':deepcopy(refs)}
    class Model:
        def call(self,system,payload,budget):
            assert not system.startswith('独立核验')
            bad=statement(extraction_id='b',type='step',supports=[{'evidence_id':'e0','member_keys':['missing']}])
            return {'statements':[statement(extraction_id='a',type='procedure',supports=deepcopy(refs),semantic_relations=[relation]),
                                  bad],
                    'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    items,coverage=extract([view],Model(),OntologyRegistry())
    assert len(items)==1 and items[0]['semantic_relations']==[]
    assert any('支持成员未知' in reason for reason in coverage['rejected'])
    assert any('未通过核验' in reason for reason in coverage['rejected'])


def test_model_payload_shares_lossless_quotes_without_geometry_or_repeated_views():
    import json
    views=[{'evidence_id':'real','kind':'table','context':{'table_number':'7.2.1','table_title':'Test values'},'members':[
        {'member_key':'header','source_id':'header-source','role':'header','quote':'Limit mm','quality':'readable',
         'locator':{'physical_page':2,'logical_page':'1','bbox':[1,2,3,4],'original_hash':'hash'},
         'attributes':{'row_index':0,'column_index':0,'column_span':2,'section_sources':[{'text':'x'*50000}]}},
        {'member_key':'value','source_id':'cell-source','role':'value','quote':'Object 1.0','quality':'verified',
         'locator':{'physical_page':2,'char_range':[0,10],'bbox':[1,2,3,4]},
         'attributes':{'row_index':1,'column_index':0,'row_span':1,'verified_scopes':[{'text':'x'*50000}]}}]}]
    captured=[]
    class Model:
        def call(self,system,payload,budget):
            data=json.loads(payload);captured.append(data)
            assert not system.startswith('独立核验')
            return {'statements':[statement(text=f'Fact {i}',supports=[{'evidence_id':'e0','member_keys':['e0m0','e0m1']}]) for i in range(12)],
                    'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    items,coverage=extract(views,Model(),OntologyRegistry())
    assert len(items)==12 and not coverage['rejected']
    initial=captured[0][0]['members']
    assert [row['quote'] for row in initial]==['Limit mm','Object 1.0']
    assert initial[0]['attributes']['column_span']==2
    assert 'locator' not in initial[1]
    assert 'locator' not in initial[0] and 'section_sources' not in initial[0]['attributes']
    assert [row['source_id'] for row in initial]==['u0','u1']
    assert len(captured)==1 and len(json.dumps(captured[0]))<15000
    assert items[0]['source_anchors'][0]['locator']['bbox']==[1,2,3,4]
    assert len(views[0]['members'][0]['attributes']['section_sources'][0]['text'])==50000


def test_extraction_preserves_native_reference_but_cannot_use_unread_target_members():
    import json
    reference={'kind':'clause','number':'7.1.2','status':'resolved','target_evidence_ids':['target-evidence'],
               'source_id':'origin-source','source_member_keys':['own'],'start':0,'end':10,'quote':'7.1.2条'}
    view={'evidence_id':'origin-evidence','kind':'text','context':{'reference_resolution':[reference],'reference_evidence_ids':['target-evidence']},'members':[
        {'member_key':'own','source_id':'origin-source','role':'content','quote':'Object shall comply with clause 7.1.2.', 'locator':{'physical_page':2}}]}
    sent=[]
    class Model:
        def call(self,system,payload,budget):
            data=json.loads(payload);sent.append(data)
            return {'statements':[statement(text='Object shall comply with clause 7.1.2.',supports=[{'evidence_id':'e0','member_keys':['e0m0']}]),
                                  statement(text='Other target rule.',supports=[{'evidence_id':'e0','member_keys':['copied']}])],
                    'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    items,coverage=extract([view],Model(),OntologyRegistry())
    assert len(items)==1 and '支持成员未知' in coverage['rejected']
    assert [member['quote'] for member in sent[0][0]['members']]==['Object shall comply with clause 7.1.2.']
    assert items[0]['references'][0]['target_evidence_ids']==['target-evidence']
    store=Store();persist(store,items,'d',OntologyRegistry())
    links=list(store.links['REFERENCES'].values())
    assert len(links)==1 and links[0]['target']=='target-evidence'
    assert links[0]['data']['source_member_keys']==['own']
    assert len(store.nodes['Statement'])==1


def test_scope_pointers_use_supported_evidence_intersection_and_conservative_batch_merge():
    class ScopedStore(Store):
        def get(self,ids,documents=None,budget=None):
            return [deepcopy(row) for label in ('Statement','Evidence') for row in self.nodes.get(label,{}).values() if row['id'] in ids and (documents is None or row.get('document_id') in documents)]
    store=ScopedStore();registry=OntologyRegistry()
    store.put('Evidence',[{'id':'one','document_id':'d','context':{'scope_ids':['chapter','local']}},
                          {'id':'two','document_id':'d','context':{'scope_ids':['chapter']}},
                          {'id':'unscoped','document_id':'d','context':{}},
                          {'id':'foreign','document_id':'other','context':{'scope_ids':['chapter']}}])
    persist(store,[statement('one',scope_ids=['forged'])],'d',registry)
    saved=next(iter(store.nodes['Statement'].values()))
    assert saved['scope_ids']==['chapter','local'] and 'conditions' not in saved
    persist(store,[statement('two',scope_ids=['forged'])],'d',registry)
    saved=next(iter(store.nodes['Statement'].values()))
    assert saved['scope_ids']==['chapter'] and len(saved['supports'])==2
    store.nodes['Evidence']['two']['context']['scope_ids']=['chapter','local']
    persist(store,[statement('two')],'d',registry)
    assert next(iter(store.nodes['Statement'].values()))['scope_ids']==['chapter']
    persist(store,[statement('unscoped')],'d',registry)
    assert next(iter(store.nodes['Statement'].values()))['scope_ids']==[]
    isolated=ScopedStore();isolated.put('Evidence',[store.nodes['Evidence']['foreign']])
    persist(isolated,[statement('foreign',scope_ids=['chapter'])],'d',registry)
    assert next(iter(isolated.nodes['Statement'].values()))['scope_ids']==[]


def test_extract_uses_scope_ids_from_all_supports_without_copying_declaration_text():
    views=[{'evidence_id':key,'kind':'text','context':context,'members':[
        {'member_key':f'{key}-own','source_id':f'{key}-source','role':'content','quote':'Object inspection is required.','locator':{'physical_page':3}}]}
        for key,context in [('one',{'scope_ids':['chapter','local']}),('two',{'scope_ids':['chapter']})]]
    class Model:
        def call(self,system,payload,budget):
            return {'statements':[statement(scope_ids=['forged'],supports=[{'evidence_id':f'e{i}','member_keys':[f'e{i}m0']} for i in range(2)])],
                    'evidence_status':[{'evidence_id':f'e{i}','status':'complete'} for i in range(2)]}
    items,coverage=extract(views,Model(),OntologyRegistry())
    assert not coverage['rejected'] and items[0]['scope_ids']==['chapter']
    assert 'conditions' not in items[0]


def test_same_evidence_mother_and_child_supports_union_preserves_mother_reference():
    reference={'kind':'clause','number':'7.4.2','status':'resolved','target_evidence_ids':['existing-target'],
               'source_id':'mother-source','source_member_keys':['mother'],'start':0,'end':12,'quote':'7.4.2'}
    view={'evidence_id':'own-evidence','kind':'text','context':{'reference_resolution':[reference]},'members':[
        {'member_key':'mother','source_id':'mother-source','role':'content','quote':'Object follows clause 7.4.2.', 'locator':{'physical_page':2}},
        {'member_key':'child','source_id':'child-source','role':'content','quote':'Object inspection is required.', 'locator':{'physical_page':2}}]}
    class Model:
        def call(self,system,payload,budget):
            return {'statements':[statement(supports=[{'evidence_id':'e0','member_keys':['e0m0']},
                                                      {'evidence_id':'e0','member_keys':['e0m1']}])],
                    'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    items,coverage=extract([view],Model(),OntologyRegistry())
    assert not coverage['rejected'] and items[0]['supports'][0]['member_keys']==['mother','child']
    assert len(items[0]['supports'])==1 and len(items[0]['supports'][0]['quotes'])==2
    store=Store();persist(store,items,'d',OntologyRegistry())
    assert next(iter(store.links['REFERENCES'].values()))['data']['source_member_keys']==['mother']
    saved=next(iter(store.nodes['Statement'].values()))
    assert len(saved['supports'])==1 and saved['supports'][0]['member_keys']==['mother','child']
    # Persistence also accepts older correctly bound rows without extraction-time normalization.
    original=deepcopy(items[0]);original['supports']=[
        {'evidence_id':'own-evidence','member_keys':['mother'],'quotes':items[0]['supports'][0]['quotes'][:1]},
        {'evidence_id':'own-evidence','member_keys':['child'],'quotes':items[0]['supports'][0]['quotes'][1:]}]
    separate=Store();persist(separate,[original],'d',OntologyRegistry())
    assert len(next(iter(separate.nodes['Statement'].values()))['supports'])==1
    invalid=deepcopy(original);invalid['references'][0]['source_member_keys']=['unbound']
    blocked=Store()
    with pytest.raises(ValueError,match='自身支持成员'):persist(blocked,[invalid],'d',OntologyRegistry())
    assert blocked.nodes=={} and blocked.links=={}


def test_model_transport_drops_layout_runtime_fields_but_keeps_parent_chain_and_all_children():
    from turbine_kg.knowledge.extract import _model_view
    import json
    view={'evidence_id':'e','kind':'text','context':{},'members':[
        {'member_key':'parent','source_id':'parent-source','role':'content','quote':'应符合下列规定。',
         'locator':{'physical_page':1},'attributes':{'layout_ref':'x'*10000,'layout_depth':1,'heading_level':2}},
        *[{'member_key':f'child-{index}','source_id':f'child-source-{index}','role':'content','quote':f'{index} 独立子项全文。',
            'locator':{'physical_page':1},'attributes':{'layout_parent_ref':'parent-layout','layout_ancestor_refs':['x'*10000],
                'section_basis':'runtime','structural_context_basis':'runtime','enumerated':True,
                'structural_parent_source_ids':['parent-source'],'list_marker':str(index),'scope_source':False}}
            for index in range(1,5)]]}
    result=_model_view(view)
    assert len(result['members'])==5 and [member['quote'] for member in result['members']]==[member['quote'] for member in view['members']]
    assert result['members'][0]['attributes']=={'heading_level':2}
    for index,member in enumerate(result['members'][1:],1):
        assert member['attributes']=={'structural_parent_source_ids':['u0'],'list_marker':str(index),'scope_source':False}
    assert len(json.dumps(result,ensure_ascii=False))<2000




def test_related_class_edges_coalesce_source_member_subsets_and_keep_all_supported_members():
    from turbine_kg.knowledge.extract import merge_statement_mentions
    node={'id':'s','supports':[{'evidence_id':'e','member_keys':['m1','m2']}]}
    m1={'evidence_id':'e','member_key':'m1','source_id':'a'}
    m2={'evidence_id':'e','member_key':'m2','source_id':'b'}
    def edge(members):return {'source':'class','target':'s','data':{'role':'related','source_members':members}}
    merged=merge_statement_mentions([edge([m1,m2]),edge([m2]),edge([{'evidence_id':'foreign','member_key':'bad'}])],[node],'d','v')
    assert len(merged)==1
    assert merged[0]['data']['source_members']==[m1,m2]
    subset=merge_statement_mentions([edge([m2])],[node],'d','v')
    assert subset[0]['key']==merged[0]['key']
