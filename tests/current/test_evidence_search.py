"""Chinese source recall remains usable without an extracted Statement."""
import json
import pytest
from turbine_kg.evidence.context import ContextAssembler, evidence_text, make_evidence
from turbine_kg.knowledge.store import KnowledgeStore
from turbine_kg.retrieval.search import SEARCH_VERSION, query_projection, search_tokens
from turbine_kg.retrieval.tools import Tools
from turbine_kg.runtime import Budget, Snapshot


def test_chinese_phrase_recall_without_word_segmentation_plugin():
    source=set(search_tokens('凝汽器壳体现场组装；弯曲度应符合要求。'))
    for phrase in ('凝汽器','壳体','现场组装','弯曲度'):
        assert set(search_tokens(phrase)) <= source
    assert not set(search_tokens('轴承间隙')) & source
    assert search_tokens('１．２５ MM') == search_tokens('1.25 mm')


def test_local_evidence_text_keeps_cross_page_lead_and_conditions_without_inherited_scope():
    context={'section':'5 汽轮机辅助设备 / 通用章节路径', 'clause_number':'5.2.2'}
    text=evidence_text(context,
        ['5 汽轮机辅助设备','凝汽器壳体现场组装应符合下列规定：','壳体弯曲度应小于3mm/m。',
         '仅在冷态安装时适用。','本章适用于凝汽器、除氧器、热交换器。'],
        ['section','section','content','condition','section_context'],
        ['heading','list_item','list_item','paragraph','paragraph'])
    assert '凝汽器壳体现场组装' in text
    assert '壳体弯曲度' in text and '仅在冷态安装' in text
    assert '本章适用于' not in text and '通用章节路径' not in text
    assert '5 汽轮机辅助设备' not in text
    assert set(search_tokens('弯曲度')) <= set(search_tokens(text))


def test_table_evidence_text_keeps_headers_values_notes_and_title():
    text=evidence_text({'section':'通用章节路径','table_title':'表A 冷却管要求'},
                           ['直径/mm','冷却管','25','只适用于铜管','本章适用于热交换器'],
                           ['header','row_label','value','note','section_context'],kind='table')
    for phrase in ('表A 冷却管要求','直径/mm','冷却管','25','只适用于铜管'):
        assert phrase in text
    assert '本章适用于' not in text and '通用章节路径' not in text


def test_new_evidence_uses_local_projection_without_changing_source_members():
    def source(key,text,page):
        return {'id':key,'text':text,'locator':{'physical_page':page},'kind':'paragraph'}
    sources=[(source('lead','现场组装应符合下列规定：',37),'section'),
             (source('item','壳体弯曲度应小于3mm/m。',38),'content'),
             (source('scope','本章适用于凝汽器、热交换器。',37),'section_context')]
    evidence=make_evidence('text',sources,'g',context={'section':'5 通用章节路径'})
    assert len(evidence['members'])==3
    assert evidence['members'][0]['locator']['physical_page']==37
    assert evidence['members'][1]['locator']['physical_page']==38
    assert '现场组装' in evidence['text'] and '弯曲度' in evidence['text']
    assert '本章适用于' not in evidence['text'] and '通用章节路径' not in evidence['text']


def test_reassembled_projection_keeps_full_source_but_indexes_only_local_members():
    def source(key,text,page,kind='paragraph'):
        return {'id':key,'text':text,'locator':{'physical_page':page},'kind':kind,
                'document_id':'d','revision_id':'r'}
    sources=[(source('heading','5 汽轮机辅助设备',37,'heading'),'section'),
             (source('lead','凝汽器壳体组装应符合下列规定：',37,'list_item'),'section'),
             (source('item','弯曲度应小于3mm/m。',38,'list_item'),'content'),
             (source('scope','本章适用于凝汽器、热交换器。',37),'section_context')]
    evidence=make_evidence('text',sources,'d',context={'section':'5 汽轮机辅助设备'})
    evidence['document_id']='d'
    nodes={entry['id']:entry for entry,_ in sources}
    nodes.update({evidence['id']:evidence,'d':{'id':'d','title':'资料'}})
    class Store:
        def get(self,ids,documents=None,budget=None):
            return [nodes[key] for key in ids if key in nodes and
                    (documents is None or nodes[key].get('document_id') in documents)]
    assembler=ContextAssembler(Store())
    snapshot=Snapshot(('d',),'o',())
    view=assembler.read(evidence['id'],snapshot)
    assert len(view['members'])==4
    assert '本章适用于' in view['members'][-1]['quote']
    assert '凝汽器壳体组装' in view['text'] and '弯曲度' in view['text']
    assert '本章适用于' not in view['text'] and '5 汽轮机辅助设备' not in view['text']
    evidence['context']['reference_evidence_ids']=['new-reference']
    refreshed=assembler.read(evidence['id'],snapshot)
    assert refreshed['context']['reference_evidence_ids']==['new-reference']
    assert refreshed['members']==view['members']


def test_lucene_metacharacters_never_enter_search_syntax():
    query,terms=query_projection('凝汽器" OR *:* (压力)', ['壳体','压力'])
    assert '*' not in query and ':' not in query and '"' not in query
    assert terms==['壳体','压力']
    with pytest.raises(ValueError):query_projection('  ')
    with pytest.raises(ValueError):query_projection('压力','not a list')


def test_whole_phrase_conjunction_prevents_single_installation_token_recall():
    query,_=query_projection('除氧器安装应符合什么规定',['除氧器','安装'])
    object_group='('+' AND '.join(search_tokens('除氧器'))+')'
    activity_group='('+' AND '.join(search_tokens('安装'))+')'
    assert query==object_group+' AND '+activity_group
    alternatives,_=query_projection('对象',['除氧器','去氧器'],match='any')
    assert alternatives=='('+' AND '.join(search_tokens('除氧器'))+') OR ('+' AND '.join(search_tokens('去氧器'))+')'
    with pytest.raises(ValueError):query_projection('对象',match='unknown')


class SearchStore:
    def __init__(self,missing=0):self.queries=[];self.missing=missing
    def query(self,query,params,budget=None):
        self.queries.append((query,params))
        if 'count(e) AS total' in query:return [{'total':2}]
        if 'count(e) AS missing' in query:return [{'missing':self.missing}]
        return [
            {'evidence':{'id':'with-statement','document_id':'book','revision_id':'r'},
             'score':2,'statements':[{'id':'s','document_id':'book','text':'一项要求'}]},
            {'evidence':{'id':'without-statement','document_id':'book','revision_id':'r'},
             'score':1,'statements':[]},
        ]


def test_direct_recall_returns_statement_and_still_finds_unextracted_evidence():
    store=SearchStore(missing=1)
    tools=Tools(store,None,Snapshot(('book',),'ontology',()),Budget())
    result=tools.search_evidence('凝汽器壳体怎么办',['凝汽器','壳体'])
    assert [item['evidence_id'] for item in result['items']]==['with-statement','without-statement']
    assert result['items'][0]['statements'][0]['text']=='一项要求'
    assert result['items'][1]['statements']==[]
    assert all(item['requires_read'] for item in result['items'])
    assert all('text' not in item for item in result['items'])
    assert result['coverage']['unindexed']==1 and result['warnings']
    data_query=store.queries[1][0]
    assert data_query.index('e.document_id IN $documents')<data_query.index('SKIP $offset')
    assert 's.document_id=e.document_id' in data_query
    assert all(params['documents']==['book'] for _,params in store.queries)
    assert not tools.reads  # Snippets cannot become answer evidence implicitly.


def test_fulltext_cursor_is_bound_to_query_and_snapshot():
    tools=Tools(SearchStore(),None,Snapshot(('book',),'o',()),Budget())
    with pytest.raises(ValueError,match='分页游标'):
        tools.search_evidence('凝汽器',cursor={'key':'other-query','offset':1})


def test_object_anchor_does_not_require_activity_or_turn_component_into_alternative():
    from turbine_kg.ontology.registry import OntologyRegistry
    store=SearchStore()
    tools=Tools(store,OntologyRegistry(),Snapshot(('book',),'o',()),Budget())
    tools.focus_tasks=[{'object':'凝汽器','query_terms':['凝汽器','壳体','安装']}]
    result=tools.search_evidence('凝汽器壳体安装问题',['凝汽器','壳体','安装'])
    params=store.queries[0][1]
    assert params['query']==query_projection('凝汽器',['凝汽器'],match='any')[0]
    assert params['terms']==['凝汽器','壳体','安装']
    assert result['coverage']['match_mode']=='object_anchored'
    assert result['coverage']['object_terms']==['凝汽器']


def test_unknown_object_relaxes_complete_phrases_only_after_empty_conjunction():
    from turbine_kg.ontology.registry import OntologyRegistry
    class Store(SearchStore):
        def query(self,query,params,budget=None):
            if 'count(e) AS total' in query and ' OR ' not in params['query']:
                self.queries.append((query,dict(params)))
                return [{'total':0}]
            return super().query(query,dict(params),budget)
    store=Store()
    tools=Tools(store,OntologyRegistry(),Snapshot(('book',),'o',()),Budget())
    tools.focus_tasks=[{'object':'新型设备'}]
    result=tools.search_evidence('新型设备发生异常',['新型设备','连接松动'])
    assert result['coverage']['match_mode']=='relaxed_phrases' and result['warnings']
    assert store.queries[0][1]['query']==query_projection('问题',['新型设备','连接松动'])[0]
    assert store.queries[1][1]['query']==query_projection('问题',['新型设备','连接松动'],match='any')[0]


def test_multitask_shared_question_uses_the_current_task_explicit_object():
    from turbine_kg.ontology.registry import OntologyRegistry
    tools=Tools(SearchStore(),OntologyRegistry(),Snapshot(('book',),'o',()),Budget())
    tools.focus_tasks=[{'object':'除氧器'},{'object':'凝汽器'}]
    question='除氧器和凝汽器安装前应准备什么'
    first=tools.search_evidence(question,['除氧器','安装前准备'])
    second=tools.search_evidence(question,['凝汽器','安装前准备'])
    assert first['coverage']['object_terms']==['除氧器']
    assert second['coverage']['object_terms']==['凝汽器']


def test_evidence_projection_is_indexed_separately_from_source_data():
    store=object.__new__(KnowledgeStore)
    calls=[]
    store.query=lambda text,params=None: calls.append((text,params))
    original={'id':'e','document_id':'book','members':[{'source_id':'source'}],
              'text':'凝汽器壳体弯曲度','search_text':'旧版检索投影'}
    store.put('Evidence',[original])
    written=calls[0][1]['rows'][0]
    assert written['text']=='凝汽器壳体弯曲度'
    assert written['search_projection_version']==SEARCH_VERSION
    assert set(search_tokens('壳体')) <= set(written['search_tokens'].split())
    assert 'search_tokens' not in original
    assert 'members' in written['data_json']
    assert 'search_text' not in written['data_json']
    assert 'REMOVE n.search_text' in calls[0][0]


def test_visual_read_never_opens_a_source_file(monkeypatch):
    tools=Tools(SearchStore(),None,Snapshot(('book',),'o',()),Budget())
    tools.reads['e']={'members':[{'member_key':'p','source_kind':'picture',
                               'image':{'path':'should-not-be-read.png'}}]}
    monkeypatch.setattr('pathlib.Path.open',lambda *a,**k:pytest.fail('filesystem read'))
    result=tools.read_visual('e')
    assert result['coverage']['read']==0
    assert result['items'][0]['status']=='unavailable'


def test_backfill_rebuilds_only_requested_document_from_graph_members(monkeypatch):
    store=object.__new__(KnowledgeStore)
    calls=[]
    old_node={'id':'e','document_id':'book','revision_id':'r','physical_page':12,
              'kind':'text','text':'旧正文','search_text':'旧属性正文','content_hash':'old-hash',
              'data_json':json.dumps({'members':[{'source_id':'source','quote':'保留来源'}],
                                      'context':{'clause_number':'5.2'},'search_text':'旧JSON正文'})}
    def query(cypher,params=None):
        calls.append((cypher,params))
        if 'RETURN properties(d) AS node' in cypher:return [{'node':{'id':'book','ontology_version':'o'}}]
        if 'RETURN properties(e) AS node' in cypher:return [{'node':old_node}]
        return []
    store.query=query
    def read(self,evidence_id,snapshot):
        assert evidence_id=='e' and snapshot.document_ids==('book',)
        return {'text':'校正后的凝汽器条款','members':[{'quote':'原始OCR片段'}]}
    monkeypatch.setattr('turbine_kg.evidence.context.ContextAssembler.read',read)
    result=store.rebuild_evidence_text('book')
    updates=[(cypher,params) for cypher,params in calls if 'UNWIND $rows AS row' in cypher]
    assert len(updates)==1
    cypher,params=updates[0]
    stored=params['rows'][0]
    assert 'SET n = row' in cypher
    assert stored['text']=='校正后的凝汽器条款'
    assert stored['id']=='e' and stored['document_id']=='book'
    assert stored['content_hash']!='old-hash'
    assert 'search_text' not in stored
    stored_data=json.loads(stored['data_json'])
    assert 'search_text' not in stored_data
    assert stored_data['members']==[{'source_id':'source','quote':'保留来源'}]
    assert result['updated']==1
    assert not any('DELETE' in cypher for cypher,_ in calls)


def test_backfill_can_refresh_one_explicit_evidence_without_touching_siblings(monkeypatch):
    store=object.__new__(KnowledgeStore)
    calls=[]
    def query(cypher,params=None):
        calls.append((cypher,params))
        if 'RETURN properties(d) AS node' in cypher:return [{'node':{'id':'book','ontology_version':'o'}}]
        if 'RETURN properties(e) AS node' in cypher:return [{'node':{'id':'e','document_id':'book','kind':'text','data_json':'{}'}}]
        return []
    store.query=query
    monkeypatch.setattr('turbine_kg.evidence.context.ContextAssembler.read',
                        lambda self,evidence_id,snapshot:{'text':'已核对来源','members':[]})
    result=store.rebuild_evidence_text('book',['e'])
    assert result['updated']==1
    assert next(params for cypher,params in calls if 'RETURN properties(e) AS node' in cypher)['ids']==['e']
    assert len([1 for cypher,_ in calls if 'UNWIND $rows AS row' in cypher])==1


def test_backfill_does_not_reindex_inherited_scope_when_local_text_is_empty(monkeypatch):
    store=object.__new__(KnowledgeStore)
    calls=[]
    def query(cypher,params=None):
        calls.append((cypher,params))
        if 'RETURN properties(d) AS node' in cypher:return [{'node':{'id':'book','ontology_version':'o'}}]
        if 'RETURN properties(e) AS node' in cypher:return [{'node':{'id':'e','document_id':'book','kind':'text','data_json':'{}'}}]
        return []
    store.query=query
    monkeypatch.setattr('turbine_kg.evidence.context.ContextAssembler.read',
                        lambda self,evidence_id,snapshot:{'text':'',
                            'members':[{'quote':'本章适用于所有设备','role':'section_context'}]})
    store.rebuild_evidence_text('book')
    update=next(params for cypher,params in calls if 'UNWIND $rows AS row' in cypher)['rows'][0]
    assert update['text']=='' and update['search_tokens']==''
    assert update['search_projection_version']==SEARCH_VERSION
