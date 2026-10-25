import pytest

from turbine_kg.knowledge.incremental import (
    content_fingerprint,
    evidence_logical_key,
    statement_logical_key,
)


def evidence(page, quote='原文'):
    return {
        'document_id': 'doc', 'kind': 'text', 'physical_page': page,
        'members': [{'source_id': f'source-{page}', 'role': 'content',
                     'locator': {'physical_page': page, 'char_range': [0, len(quote)]},
                     'quote': quote}],
        'context': {'primary_page': page},
    }


def statement(evidence_id, text):
    return {
        'document_id': 'doc', 'type': 'requirement', 'text': text,
        'supports': [{'evidence_id': evidence_id, 'member_keys': ['member']}],
        'source_anchors': [{'source_id': 'source-1', 'locator': {'physical_page': 1}}],
    }


def test_evidence_key_ignores_member_identity_and_quote_changes():
    first = evidence(37, '旧原文')
    second = evidence(37, '新原文')
    first['members'][0]['member_key'] = 'member-a'
    second['members'][0]['member_key'] = 'member-b'
    assert evidence_logical_key(first) == evidence_logical_key(second)


def test_statement_key_distinguishes_multiple_assertions_from_same_source():
    first = statement('evidence-old', '原始断言')
    second = statement('evidence-new', '修订后的同一来源断言')
    keys = {'evidence-old': 'evidence:stable', 'evidence-new': 'evidence:stable'}
    assert statement_logical_key(first, keys) != statement_logical_key(second, keys)
    second['text']=first['text']
    assert statement_logical_key(first, keys) == statement_logical_key(second, keys)


def test_content_fingerprint_ignores_storage_metadata():
    row = {'id': 'old', 'logical_key': 'stable', 'content_hash': 'cached', 'text': '同一内容'}
    changed = {**row, 'id': 'new', 'content_hash': 'new-cache', 'updated_at': 'now'}
    assert content_fingerprint(row) == content_fingerprint(changed)


def test_cypher_type_and_alias_properties_match_unpacked_records():
    from turbine_kg.knowledge.store import unpack
    graph=MemoryGraph()
    row={**statement('e','对象应检查。'),'id':'s'}
    graph.put('Statement',[row])
    assert graph.nodes['Statement']['s']['type']=='requirement'
    assert unpack(graph.nodes['Statement']['s'])['type']==row['type']
    graph.put('OntologyClass',[{'id':'c','class_id':'Equipment','label':'设备','aliases':['装备']}])
    assert graph.nodes['OntologyClass']['c']['aliases']==['装备']



class MemoryGraph:
    from turbine_kg.knowledge.store import KnowledgeStore
    sync_document=KnowledgeStore.sync_document
    _sync_document=KnowledgeStore._sync_document
    put=KnowledgeStore.put
    sync_ontology=KnowledgeStore.sync_ontology

    def __init__(self, records=None):
        from types import SimpleNamespace
        self.settings=SimpleNamespace()
        from copy import deepcopy
        self.nodes=deepcopy(records or {});self.links={};self.writes=[]

    def transaction(self):
        from contextlib import contextmanager
        from copy import deepcopy
        @contextmanager
        def begin():
            saved=deepcopy((self.nodes,self.links))
            try:yield self
            except Exception:
                self.nodes,self.links=saved;raise
        return begin()

    def edges(self,kind,rows):
        from turbine_kg.runtime import identity
        self.links.setdefault(kind,{}).update({row.get('key') or identity('edge',kind,row['source'],row['target']):row for row in rows})

    def get(self,ids,documents=None,budget=None):
        from copy import deepcopy
        from turbine_kg.knowledge.store import unpack
        return [deepcopy(unpack(row)) for rows in self.nodes.values() for key,row in rows.items()
                if key in ids and (documents is None or unpack(row).get('document_id') in documents)]

    def query(self,text,parameters=None):
        import re
        from copy import deepcopy
        from turbine_kg.knowledge.store import unpack
        parameters=parameters or {}
        if text.startswith('MATCH (d:TK:Document) RETURN d.id'):
            return [{'id':key,'ontology_version':unpack(row).get('ontology_version'),
                     'stale_mapping':any(unpack(s).get('document_id')==key and
                         unpack(s).get('ontology_version')!=parameters['version']
                         for s in self.nodes.get('Statement',{}).values())}
                    for key,row in sorted(self.nodes.get('Document',{}).items())]
        if text.startswith('MATCH (s:TK:Statement {document_id:'):
            return [{'node':deepcopy(row)} for row in self.nodes.get('Statement',{}).values()
                    if unpack(row).get('document_id')==parameters['document']]
        if text.startswith('MATCH (a:TK:OntologyClass)-[r:SUBCLASS_OF]'):
            keep={row['key'] for row in parameters['keep']}
            self.links['SUBCLASS_OF']={key:row for key,row in self.links.get('SUBCLASS_OF',{}).items() if key in keep}
            return []
        if text.startswith('MATCH (n:TK:OntologyClass) WHERE NOT n.id IN'):
            connected={row[side] for rows in self.links.values() for row in rows.values() for side in ('source','target')}
            mentioned=''.join(str(row) for row in self.nodes.get('Statement',{}).values())
            self.nodes['OntologyClass']={key:row for key,row in self.nodes.get('OntologyClass',{}).items()
                                        if key in parameters['ids'] or key in connected or key in mentioned}
            return []
        if text.startswith('MATCH (c:TK:OntologyClass)-[r:MENTIONS]'):
            keep={row['key'] for row in parameters['keep']}
            self.links['MENTIONS']={key:row for key,row in self.links.get('MENTIONS',{}).items()
                                    if row['target'] not in parameters['ids'] or key in keep}
            return []
        if text.startswith('UNWIND $ids AS id MATCH ()-[r]->()'):
            for identifier in parameters['ids']:
                kind,key=identifier.split(':',1);self.links[kind].pop(key)
            return []
        if text.startswith('UNWIND $rows AS row MATCH ()-[r]->()'):
            import json
            for row in parameters['rows']:
                kind,key=row['id'].split(':',1)
                self.links[kind][key]['data']=json.loads(row['data'])
            return []
        if text.startswith('MATCH (n:TK:') and 'RETURN properties(n) AS node' in text:
            label=re.search(r'n:TK:(\w+)',text)[1]
            return [{'node':deepcopy(row)} for row in self.nodes.get(label,{}).values()
                    if (unpack(row).get('id')==parameters['document'] if 'WHERE n.id=$document' in text else
                        unpack(row).get('document_id')==parameters.get('document',parameters.get('id')))]
        if text.startswith('UNWIND $rows AS row MERGE (n:TK:'):
            label=re.search(r'n:TK:(\w+)',text)[1]
            self.nodes.setdefault(label,{})
            for row in parameters['rows']:
                if 'SET n = row' in text:self.nodes[label][row['id']]=deepcopy(row)
                else:self.nodes[label].setdefault(row['id'],{}).update(deepcopy(row))
            self.writes.append((label,len(parameters['rows'])))
            return []
        if text.startswith('MATCH (n:TK:') and 'DETACH DELETE n' in text:
            label=re.search(r'n:TK:(\w+)',text)[1]
            for key in parameters['ids']:self.nodes[label].pop(key,None)
            return []
        if text.startswith('MATCH (a:TK)-[r]->(b:TK)'):
            if 'RETURN elementId(r)' in text:
                import json
                return [{'id':kind+':'+key,'kind':kind,'source':edge['source'],'target':edge['target'],
                         'properties':{'key':key,'data_json':json.dumps(edge.get('data',{})),
                                       'ontology_version':edge.get('data',{}).get('ontology_version')}}
                        for kind,rows in self.links.items() for key,edge in rows.items()
                        if edge.get('data',{}).get('document_id')==parameters['document']]
            if 'RETURN type(r)' in text:
                return [{'kind':kind,'key':key,**edge,'data':edge.get('data',{})}
                        for kind,rows in self.links.items() for key,edge in rows.items()]
            keep={(row['kind'],row['key']) for row in parameters['keep']}
            self.links={kind:{key:row for key,row in rows.items() if (kind,key) in keep}
                        for kind,rows in self.links.items()}
            return []
        raise AssertionError(text)


def test_document_sync_preserves_node_identity_rewrites_links_and_removes_stale_fields():
    from turbine_kg.knowledge.store import unpack
    old={**evidence(1),'id':'old-evidence','logical_key':'stable-evidence','status':'old-field'}
    graph=MemoryGraph({'Evidence':{'old-evidence':old,'retired':{**evidence(2),'id':'retired'}}})
    incoming={**evidence(1),'id':'incoming-evidence','logical_key':'stable-evidence'}
    fact={**statement('incoming-evidence','检查应完成。'),'id':'statement'}
    fact['references']=[{'target_evidence_ids':['incoming-evidence']}]
    result=graph.sync_document('doc',{'Evidence':[incoming],'Statement':[fact]},
                               [{'kind':'SUPPORTS','source':'statement','target':'incoming-evidence'}])
    assert set(graph.nodes['Evidence'])=={'old-evidence'}
    assert 'status' not in graph.nodes['Evidence']['old-evidence']
    saved=unpack(graph.nodes['Statement']['statement'])
    assert saved['supports'][0]['evidence_id']=='old-evidence'
    assert saved['references'][0]['target_evidence_ids']==['old-evidence']
    assert next(iter(graph.links['SUPPORTS'].values()))['target']=='old-evidence'
    assert result['deleted']==1
    graph.writes.clear()
    graph.sync_document('doc',{'Evidence':[incoming]},delete_missing=False)
    assert not graph.writes


def test_document_sync_reconciles_legacy_same_source_statement_keys_without_losing_facts():
    first={**statement('e','甲应清洁。'),'id':'a','logical_key':'old-collision'}
    second={**statement('e','乙应牢固。'),'id':'b','logical_key':'old-collision'}
    graph=MemoryGraph({'Statement':{'a':first,'b':second}})
    result=graph.sync_document('doc',{'Statement':[first,second]})
    assert result['deleted']==0 and len(graph.nodes['Statement'])==2
    keys={row['logical_key'] for row in graph.nodes['Statement'].values()}
    assert len(keys)==2


def test_document_sync_can_remove_last_relationship_type_and_keeps_omitted_node_labels():
    graph=MemoryGraph({'Statement':{'keep':{**statement('e','应检查。'),'id':'keep'}}})
    graph.links={'SUPPORTS':{'old':{'source':'keep','target':'e','key':'old'}}}
    graph.sync_document('doc',{},[])
    assert graph.nodes['Statement']['keep']['id']=='keep'
    assert not graph.links['SUPPORTS']


def test_document_sync_validation_failure_rolls_back_all_prior_writes():
    import pytest
    graph=MemoryGraph()
    with pytest.raises(ValueError):
        graph.sync_document('doc',{'Evidence':[{**evidence(1),'id':'e'}]},
                            [{'kind':'unsafe;DELETE','source':'a','target':'b'}])
    assert graph.nodes=={}


@pytest.mark.parametrize('upgrade',[False,True])
def test_prepare_adds_a_chapter_with_same_ocr_and_preserves_existing_extraction(monkeypatch,upgrade):
    from pathlib import Path
    from turbine_kg.knowledge import chapter_processing as sections, scopes
    from turbine_kg.knowledge.store import KnowledgeStore, unpack
    from turbine_kg.documents.ir import ParsedPage, SourceUnit
    from turbine_kg.ontology.registry import OntologyRegistry
    graph=MemoryGraph()
    graph.initialize=lambda:None
    graph.save_page=KnowledgeStore.save_page.__get__(graph)
    graph.get=lambda ids,documents=None,budget=None:[unpack(row) for rows in graph.nodes.values() for key,row in rows.items()
        if key in ids and (documents is None or unpack(row).get('document_id') in documents)]
    class Parser:
        def __init__(self,*args,**kwargs):
            self.page_count=2;self.heading_index=[];self.scope_sources=[]
        def page(self,number):
            unit=SourceUnit(f's{number}','paragraph',f'2.{number}.1 设备应检查。',{'physical_page':number},'native_text',0,section=f'2.{number} 检查')
            return ParsedPage(number,None,100,100,{},sources=[unit])
        def close(self):pass
    monkeypatch.setattr(sections,'DocumentParser',Parser)
    monkeypatch.setattr(sections,'_inputs',lambda *args:('doc','revision',None,{'ocr_hash':'same','original_hash':'original','parser_config':{},'implementation':{}}))
    monkeypatch.setattr(sections,'register_scopes',lambda *args:[])
    monkeypatch.setattr(sections,'restore_scopes',lambda *args:[])
    monkeypatch.setattr(sections,'attach_evidence_scopes',lambda *args:set())
    monkeypatch.setattr(scopes,'native_scope_declarations',lambda *args:[])
    registry=OntologyRegistry()
    sections.prepare_section(Path('book.pdf'),graph,registry,{'scope':'section','physical_pages':[1,1]})
    first=next(iter(graph.nodes['Evidence']))
    record=unpack(graph.nodes['Evidence'][first]);record['semantic_coverage']={'status':'complete'}
    graph.put('Evidence',[record])
    from turbine_kg.knowledge.statement_sync import persist
    persist(graph,[{'id':'keep','type':'requirement','text':'设备应检查。',
        'entities':[{'label':'设备','class':'Mention','role':'subject'}],
        'supports':[{'evidence_id':first,'member_keys':[member['member_key'] for member in record['members']]}]}],
        'doc',registry,preserve_ids=True)
    original=graph.get(['keep'])[0]
    if upgrade:registry.version='2'*64
    result=sections.prepare_section(Path('book.pdf'),graph,registry,{'scope':'section','physical_pages':[2,2]})
    assert result['requested_pages']==[2]
    assert len(graph.nodes['Evidence'])==2
    assert unpack(graph.nodes['Document']['doc'])['requested_pages']==[1,2]
    assert unpack(graph.nodes['Evidence'][first])['semantic_coverage']['status']=='complete'
    assert set(graph.nodes['Statement'])=={'keep'}
    current=graph.get(['keep'])[0]
    assert current['text']==original['text'] and current['supports']==original['supports']
    assert current['ontology_version']==registry.version
    assert all(row['data']['ontology_version']==registry.version
               for row in graph.links.get('HAS_REQUIREMENT',{}).values())
    assert {row['status'] for row in graph.nodes['Page'].values()}=={'parsed'}


def test_query_timing_counts_completed_and_failed_database_calls():
    from contextlib import contextmanager
    from types import SimpleNamespace
    import pytest
    from turbine_kg.knowledge.store import KnowledgeStore
    from turbine_kg.runtime import Budget
    store=object.__new__(KnowledgeStore)
    fail=[False]
    @contextmanager
    def session(**kwargs):
        def data():
            if fail[0]:raise ValueError('database failure')
            return [{'ok':True}]
        yield SimpleNamespace(run=lambda *args:SimpleNamespace(data=data))
    store.driver=SimpleNamespace(session=session)
    store.settings=SimpleNamespace(neo4j_database='neo4j')
    budget=Budget()
    assert store.query('RETURN true',budget=budget)==[{'ok':True}]
    first=budget.database_seconds
    fail[0]=True
    with pytest.raises(ValueError,match='database failure'):store.query('RETURN true',budget=budget)
    assert budget.database_calls==2 and budget.database_seconds>=first>=0


def test_reference_refresh_replaces_stale_statement_targets_without_changing_source_facts():
    from copy import deepcopy
    from turbine_kg.knowledge.chapter_processing import refresh_statement_references
    from turbine_kg.knowledge.store import unpack
    ref={'kind':'clause','number':'2.1.1','end_number':'2.1.3','status':'resolved',
         'source_member_keys':['m1'],'target_evidence_ids':['a','b','c']}
    oldref={**ref,'source_evidence_id':'e','target_evidence_ids':['a','c']}
    fact={**statement('e','设备应按引用条款检查。'),'id':'s','entities':[{'label':'设备','role':'subject'}],
          'references':[oldref]}
    fact['supports'][0]['member_keys']=['m1']
    class ReferenceGraph(MemoryGraph):
        def get(self,ids,documents=None,budget=None):
            return [unpack(row) for rows in self.nodes.values() for key,row in rows.items() if key in ids]
        def query(self,text,parameters=None):
            if text.startswith('MATCH (s:TK:Statement {document_id:'):
                return [{'node':row} for row in self.nodes.get('Statement',{}).values()]
            if text.startswith('MATCH (s:TK:Statement)-[r:REFERENCES]'):
                self.links['REFERENCES']={}
                return []
            return super().query(text,parameters)
    graph=ReferenceGraph({'Statement':{'s':deepcopy(fact)},'Evidence':{'e':{'id':'e','document_id':'doc',
        'context':{'reference_resolution':[ref]}}}})
    graph.links={'REFERENCES':{'stale':{'source':'s','target':'retired'}}}
    result=refresh_statement_references(graph,'doc',['e'])
    saved=unpack(graph.nodes['Statement']['s'])
    assert [saved[key] for key in ('text','supports','entities')]==[fact[key] for key in ('text','supports','entities')]
    assert saved['references'][0]['target_evidence_ids']==['a','b','c']
    assert {row['target'] for row in graph.links['REFERENCES'].values()}=={'a','b','c'}
    assert result=={'statements_updated':1,'reference_edges':3}


def test_prepare_keeps_unchanged_facts_with_real_attached_scope_when_adding_pages(monkeypatch):
    from dataclasses import asdict
    from pathlib import Path
    from turbine_kg.knowledge import chapter_processing as sections
    from turbine_kg.knowledge.store import KnowledgeStore, unpack
    from turbine_kg.documents.ir import ParsedPage, SourceUnit
    from turbine_kg.ontology.registry import OntologyRegistry
    from turbine_kg.runtime import identity
    class ScopeGraph(MemoryGraph):
        def query(self,text,parameters=None,budget=None):
            if text.startswith('MATCH (s:TK:ScopeDeclaration'):
                return [{'node':row} for row in self.nodes.get('ScopeDeclaration',{}).values()]
            if text.startswith('MATCH (s:TK:Statement {document_id:'):
                return [{'node':row} for row in self.nodes.get('Statement',{}).values()]
            return super().query(text,parameters)
    graph=ScopeGraph();graph.initialize=lambda:None
    graph.save_page=KnowledgeStore.save_page.__get__(graph)
    graph.get=lambda ids,documents=None,budget=None:[unpack(row) for rows in graph.nodes.values() for key,row in rows.items()
        if key in ids and (documents is None or unpack(row).get('document_id') in documents)]
    digest=['first-edition']
    body_text=['2.1.2 设备应检查。']
    class Parser:
        def __init__(self,*args,**kwargs):
            locator={'physical_page':1,'original_hash':digest[0]}
            heading=SourceUnit(identity('heading','doc',locator,'2 安装'),'heading','2 安装',locator,'native_text',0,
                section='2 安装',attributes={'heading_level':1,'hierarchy':'explicit_numbering'})
            self.heading=heading
            self.page_count=3;self.heading_index=[heading];self.scope_sources=[]
        def page(self,number):
            def unit(key,text,order):
                locator={'physical_page':number,'original_hash':digest[0]}
                return SourceUnit(identity('paragraph','doc',number,order,locator,text),'paragraph',text,locator,'native_text',order,
                    section='2 安装',attributes={'section_sources':[asdict(self.heading)]})
            sources=([self.heading,unit('scope','2.1.1 本章适用于试验设备。',1),unit('body',body_text[0],2)]
                     if number==1 else [unit(f'body-{number}',f'2.{number}.1 管路应检查。',1)])
            return ParsedPage(number,None,100,100,{},sources=sources)
        def close(self):pass
    monkeypatch.setattr(sections,'DocumentParser',Parser)
    monkeypatch.setattr(sections,'_inputs',lambda *args:('doc','revision-'+digest[0],None,{'ocr_hash':digest[0],'original_hash':digest[0],'parser_config':{},'implementation':{}}))
    registry=OntologyRegistry()
    sections.prepare_section(Path('book.pdf'),graph,registry,{'scope':'section','physical_pages':[1,1]})
    first=next(unpack(row) for row in graph.nodes['Evidence'].values()
               if unpack(row)['context']['clause_number']=='2.1.2')
    assert first['context']['scope_ids']
    assert any(member['role']=='section_context' for member in first['members'])
    first['semantic_coverage']={'status':'complete'}
    graph.put('Evidence',[first])
    fact={**statement(first['id'],'设备应检查。'),'id':'keep','scope_ids':first['context']['scope_ids'],'ontology_version':registry.version}
    fact['supports'][0]['member_keys']=[row['member_key'] for row in first['members']]
    fact['source_anchors']=[{'source_id':row['source_id'],'locator':row['locator']} for row in first['members']]
    graph.put('Statement',[fact])
    sections.prepare_section(Path('book.pdf'),graph,registry,{'scope':'section','physical_pages':[2,2]})
    kept=unpack(graph.nodes['Evidence'][first['id']])
    assert kept['semantic_coverage']['status']=='complete'
    assert kept['context']['scope_ids']==first['context']['scope_ids']
    assert any(member['role']=='section_context' for member in kept['members'])
    assert set(graph.nodes['Statement'])=={'keep'}
    assert len(graph.nodes['Evidence'])==3
    # Real parser identities include original_hash in locators. A new PDF
    # fingerprint alone must preserve source/member/Evidence/Statement IDs.
    digest[0]='second-edition'
    sections.prepare_section(Path('book.pdf'),graph,registry,{'scope':'section','physical_pages':[2,2]})
    renewed=unpack(graph.nodes['Evidence'][first['id']]);renewed_fact=unpack(graph.nodes['Statement']['keep'])
    assert renewed['semantic_coverage']['status']=='complete'
    assert renewed['context']['scope_ids']==first['context']['scope_ids']
    assert [row['member_key'] for row in renewed['members']]==[row['member_key'] for row in first['members']]
    assert renewed_fact['supports']==fact['supports'] and renewed_fact['text']==fact['text']
    assert all(row['locator']['original_hash']=='second-edition' for row in renewed_fact['source_anchors'])
    # A same-location content change must compare against quotes captured
    # before save_page overwrites SourceText, not reread the new text as old.
    body_text[0]='2.1.2 设备应清理。'
    document=unpack(graph.nodes['Document']['doc']);document['preparation_fingerprint']='force-refresh'
    graph.put('Document',[document])
    discarded=[]
    monkeypatch.setattr(sections,'_support_closure',lambda store,doc,ids:set(ids))
    monkeypatch.setattr(sections,'_discard_statements',lambda store,doc,ids:discarded.extend(ids))
    sections.prepare_section(Path('book.pdf'),graph,registry,{'scope':'section','physical_pages':[2,2]})
    assert discarded==[first['id']]
    changed=next(unpack(row) for row in graph.nodes['Evidence'].values() if unpack(row)['context']['clause_number']=='2.1.2')
    assert changed['semantic_coverage']['status']=='pending'


def mapping_graph():
    from turbine_kg.evidence.context import make_evidence
    from turbine_kg.knowledge.statement_sync import persist
    from turbine_kg.ontology.registry import OntologyRegistry, normalized
    from turbine_kg.runtime import fingerprint
    old=OntologyRegistry(); current=OntologyRegistry()
    current.business_classes['Equipment'].setdefault('aliases',[]).append('试验装置')
    current.domain_aliases[normalized('试验装置')]='Equipment'
    current.version=fingerprint(current.data)
    graph=MemoryGraph()
    for document in ('book-a','book-b'):
        source={'id':document+'-source','kind':'paragraph','text':'试验装置应清洁，检查完成后安装。',
                'document_id':document,'revision_id':'r','quality':'readable','locator':{'physical_page':1}}
        graph.put('Document',[{'id':document,'ontology_version':old.version}])
        graph.put('SourceText',[source])
        evidence=make_evidence('text',[(source,'content')],document)
        evidence.update(document_id=document,revision_id='r',semantic_coverage={'status':'complete'},ontology_version=old.version)
        graph.put('Evidence',[evidence])
        supports=[{'evidence_id':evidence['id'],'member_keys':[member['member_key'] for member in evidence['members']]}]
        facts=[{'extraction_id':'procedure','text':source['text'],'type':'procedure',
                'entities':[{'label':'试验装置','class':'Mention','role':'subject'}],'supports':supports,
                'semantic_relations':[{'relation_id':'has_step','source_ref':{'statement_id':'procedure'},
                                      'target_ref':{'statement_id':'step'},'supports':supports}]},
               {'extraction_id':'step','text':'试验装置应清洁。','type':'step',
                'entities':[{'label':'试验装置','class':'Mention','role':'subject'}],'supports':supports}]
        persist(graph,facts,document,old)
    return graph,current


def test_ontology_upgrade_rebinds_all_cores_and_relations_without_extraction():
    from copy import deepcopy
    from turbine_kg.knowledge.ontology_mapping import sync_ontology_mappings
    graph,current=mapping_graph()
    originals={row['id']:deepcopy(row) for row in graph.get(list(graph.nodes['Statement']))}
    source_nodes=deepcopy(graph.nodes['SourceText'])
    support_edges=deepcopy(graph.links['SUPPORTS'])
    # Even a prematurely stamped document must not hide its old Statements.
    graph.put('Document',[{'id':'book-a','ontology_version':current.version}])
    result=sync_ontology_mappings(graph,current)
    assert set(result)=={'book-a','book-b'}
    assert source_nodes==graph.nodes['SourceText'] and support_edges==graph.links['SUPPORTS']
    assert set(originals)==set(graph.nodes['Statement'])
    for row in graph.get(list(originals)):
        original=originals[row['id']]
        assert all(row.get(key)==original.get(key) for key in
                   ('text','type','supports','source_anchors','references','scope_ids','semantic_relations'))
        assert row['ontology_version']==current.version
        assert [(e['label'],e['role']) for e in row['entities']]==[(e['label'],e['role']) for e in original['entities']]
        assert row['entities'][0]['ontology_class_id']=='Equipment'
    for kind in ('HAS_PROCEDURE','HAS_STEP'):
        assert graph.links[kind]
        assert all(row['data']['ontology_version']==current.version for row in graph.links[kind].values())
    graph.writes.clear()
    assert sync_ontology_mappings(graph,current)=={} and not graph.writes


def test_multi_document_remapping_failure_rolls_back_versions_cores_and_edges(monkeypatch):
    from copy import deepcopy
    from turbine_kg.knowledge import ontology_mapping as mapping
    graph,current=mapping_graph();before=deepcopy((graph.nodes,graph.links))
    remap=mapping.remap_document
    def fail_second(store,document,registry):
        if document=='book-b':raise ValueError('映射失败')
        return remap(store,document,registry)
    monkeypatch.setattr(mapping,'remap_document',fail_second)
    with pytest.raises(ValueError,match='映射失败'):mapping.sync_ontology_mappings(graph,current)
    assert (graph.nodes,graph.links)==before


def test_ontology_sync_removes_only_unused_obsolete_class_nodes():
    graph,current=mapping_graph()
    for key in ('unused','referenced','embedded'):
        graph.put('OntologyClass',[{'id':key,'class_id':'Removed-'+key}])
    graph.edges('PART_OF',[{'key':'fact','source':'referenced','target':'other','data':{'document_id':'book-a'}}])
    graph.put('Statement',[{'id':'binding','document_id':'book-a','entities':[{'ontology_class_id':'embedded'}]}])
    graph.sync_ontology(current)
    assert 'unused' not in graph.nodes['OntologyClass']
    assert {'referenced','embedded'}<=graph.nodes['OntologyClass'].keys()


def test_removed_relation_keeps_source_semantics_and_can_be_reprojected_after_rename():
    from copy import deepcopy
    from turbine_kg.knowledge.ontology_mapping import sync_ontology_mappings
    from turbine_kg.runtime import fingerprint
    graph,current=mapping_graph()
    originals=deepcopy(graph.get(list(graph.nodes['Statement'])))
    supports=deepcopy(graph.links['SUPPORTS'])
    definition=current.relations.pop('has_step')
    current.version=fingerprint(current.relations)
    sync_ontology_mappings(graph,current)
    assert not graph.links.get('HAS_STEP')
    for row in graph.get([s['id'] for s in originals]):
        original=next(s for s in originals if s['id']==row['id'])
        assert row['text']==original['text'] and row['supports']==original['supports']
        assert [r['relation_id'] for r in row.get('semantic_relations',[])]==[r['relation_id'] for r in original.get('semantic_relations',[])]
    assert supports==graph.links['SUPPORTS']
    # A relation rename is explicit ontology configuration, never inferred.
    current.relations['includes_step']={**definition,'id':'includes_step','aliases':['has_step']}
    current.version=fingerprint(current.relations)
    sync_ontology_mappings(graph,current)
    assert graph.links.get('INCLUDES_STEP')
    assert all(r['data']['relation_id']=='includes_step' for r in graph.links['INCLUDES_STEP'].values())
    assert all(r['relation_id']=='has_step' for s in graph.get([s['id'] for s in originals]) for r in s.get('semantic_relations',[]))


def test_ambiguous_relation_alias_is_not_guessed():
    from turbine_kg.ontology.registry import OntologyRegistry
    registry=OntologyRegistry()
    registry.relations['new_a']={'aliases':['old_name']}
    registry.relations['new_b']={'aliases':['old_name']}
    assert registry.resolve_relation_id('old_name') is None
    assert registry.resolve_relation_id('has_step')=='has_step'


def test_repersist_removes_old_owner_edge_and_preserves_other_statements():
    from copy import deepcopy
    from turbine_kg.knowledge.statement_sync import persist
    from turbine_kg.ontology.registry import OntologyRegistry
    registry=OntologyRegistry();graph=MemoryGraph()
    views={'e':{'members':[{'member_key':'m','role':'content','quote':'设备应检查并清理。'}]}}
    supports=[{'evidence_id':'e','member_keys':['m']}]
    entity={'label':'设备','class':'Mention','role':'subject','ontology_class_id':'Equipment',
            'ontology_binding':registry.ontology_binding('设备','Equipment',supports,views)}
    facts=[{'id':sid,'type':'requirement','text':'设备应检查。' if sid=='a' else '设备应清理。','entities':[deepcopy(entity)],'supports':supports}
           for sid in ('a','b')]
    persist(graph,facts,'d',registry,preserve_ids=True,source_views=views)
    graph.edges('REFERENCES',[
        {'key':'stale','source':'a','target':'old-target','data':{'document_id':'d','statement_id':'a'}},
        {'key':'independent','source':'b','target':'kept-target','data':{'document_id':'d'}}])
    facts[0]['entities'][0]['role']='related'
    persist(graph,[facts[0]],'d',registry,preserve_ids=True,source_views=views)
    assert {row['target'] for row in graph.links['HAS_REQUIREMENT'].values()}=={'b'}
    assert {row['target'] for row in graph.links['MENTIONS'].values()}=={'a'}
    assert {row['source'] for row in graph.links['SUPPORTS'].values()}=={'a','b'}
    assert set(graph.links['REFERENCES'])=={'independent'}


def test_remapping_does_not_restore_stale_support_json_while_updating_versions():
    from turbine_kg.knowledge.ontology_mapping import sync_ontology_mappings
    graph,current=mapping_graph()
    for edge in graph.links['SUPPORTS'].values():
        edge['data'].update(member_keys=['retired-member'],quotes=[],ontology_version='old')
    sync_ontology_mappings(graph,current)
    facts={s['id']:s for s in graph.get(list(graph.nodes['Statement']))}
    for edge in graph.links['SUPPORTS'].values():
        support=next(s for s in facts[edge['source']]['supports'] if s['evidence_id']==edge['target'])
        assert edge['data']['member_keys']==support['member_keys']
        assert edge['data']['quotes']==support.get('quotes',[])
