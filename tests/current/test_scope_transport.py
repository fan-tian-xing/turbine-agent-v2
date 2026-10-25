from dataclasses import asdict
from types import SimpleNamespace
import json
import turbine_kg.agent.model as transport
from urllib.error import HTTPError
from turbine_kg.documents.ir import SourceUnit,ParsedPage
from turbine_kg.knowledge.scopes import register_scopes,attach_evidence_scopes,restore_scopes
from turbine_kg.agent.model import ModelClient,ModelAuthenticationError,ModelError
from turbine_kg.runtime import Budget
import pytest


@pytest.mark.parametrize('complete',[True,False])
def test_table_context_reads_title_and_only_complete_verified_cross_page_sources(complete):
    from turbine_kg.knowledge.statement_sync import _table_context_sources
    from turbine_kg.evidence.context import table_evidence
    first={'id':'first','kind':'paragraph','text':'在冷态下测量，','document_id':'d','quality':'verified','locator':{'physical_page':1},'attributes':{'cross_page_group':'reviewed-group','cross_page_complete_pages':[1,2]}}
    second={**first,'id':'second','text':'无厂家要求时采用表7.8。','locator':{'physical_page':2}}
    title={'id':'title','kind':'heading','text':'表7.8 允许值','document_id':'d','locator':{'physical_page':2}}
    sources=[first,second] if complete else [second]
    class Store:
        def get(self,ids,*args):return [s for s in [title]+sources if s['id'] in ids]
        def query(self,*args):return [{'node':source} for source in sources]
    table=SimpleNamespace(id='table',locator={'title_source_ids':['title'],'related_body_source_ids':['second']},cells=[])
    context=_table_context_sources(table,Store(),'d',[1,2])
    assert next(s for s in context if s['id']=='title')['attributes']['table_context_role']=='table_title'
    if complete:
        assert {s['id'] for s in context}=={'title','first','second'}
        assert table.locator['body_context_status']=='verified_complete'
        cell={'id':'cell','kind':'cell','text':'0.10 mm','locator':{'physical_page':2},'row_index':0,'column_index':0,'row_span':1,'column_span':1}
        evidence=table_evidence({'id':'table','title':title['text'],'rows':1,'columns':1,'locator':table.locator},[cell],'d',context)[0]
        assert {m['source_id'] for m in evidence['members'] if m['role']=='explicit_table_reference'}=={'first','second'}
        assert evidence['context']['body_context_status']=='verified_complete'
    else:
        assert {s['id'] for s in context}=={'title'}
        assert table.locator['body_context_status']=='incomplete' and table.locator['missing_context_pages']==[1]


@pytest.mark.parametrize('field',['title_source_ids','related_body_source_ids','inherited_header_source_ids'])
def test_missing_explicit_table_source_cannot_be_verified_complete(field):
    from turbine_kg.knowledge.statement_sync import _table_context_sources
    table=SimpleNamespace(locator={field:['absent']},cells=[])
    store=SimpleNamespace(get=lambda *args:[])
    assert _table_context_sources(table,store,'g',[1,2])==[]
    assert table.locator['body_context_status']=='incomplete'
    assert table.locator['missing_context_source_ids']==['absent']


class ScopeStore:
    def __init__(self,source):self.nodes={source['id']:source};self.scopes=[]
    def get(self,ids,*args):return [self.nodes[key] for key in ids if key in self.nodes]
    def put(self,label,rows):
        if label=='ScopeDeclaration':self.scopes.extend(rows)
        else:self.nodes.update({row['id']:row for row in rows})
    def query(self,*args):return [{'node':row} for row in self.scopes]


def test_navigation_page_never_creates_graph_records():
    from turbine_kg.knowledge.store import KnowledgeStore
    store=KnowledgeStore.__new__(KnowledgeStore)
    store.query=lambda *args,**kwargs:pytest.fail('目录页不应调用图数据库')
    store.save_page(ParsedPage(1,None,1,1,{},status='excluded_navigation'),'d','r')




def test_verified_section_scope_follows_parent_heading_and_survives_restart():
    parent=SourceUnit('h','heading','2 安装',{'physical_page':2},'native_text',0,attributes={'heading_level':1})
    child=SourceUnit('h-child','heading','2.1 步骤',{'physical_page':3},'native_text',0,attributes={'heading_level':2})
    peer=SourceUnit('h-peer','heading','3 运行',{'physical_page':5},'native_text',0,attributes={'heading_level':1})
    source=SourceUnit('s','paragraph','本章仅适用于冷态设备。',{'physical_page':2},'native_text',1)
    store=ScopeStore(asdict(source));parser=SimpleNamespace(heading_index=[parent,child,peer],page_count=8)
    scopes=register_scopes([{'scope':'section','heading_source_id':'h','condition_text':source.text,'source_members':[{'source_id':'s','locator':source.locator,'quote':source.text}]}],parser,store,'g')
    assert restore_scopes(store,'g')==scopes
    from turbine_kg.evidence.context import make_evidence
    content=asdict(SourceUnit('p','paragraph','应检查。',{'physical_page':4},'native_text',1))
    next_content=asdict(SourceUnit('q','paragraph','应复核。',{'physical_page':5},'native_text',1))
    records=[asdict(parent),asdict(child),asdict(peer),content,next_content]
    store.nodes.update({row['id']:row for row in records})
    item=make_evidence('text',[(asdict(parent),'section_context'),(asdict(child),'section_context'),(content,'content')],'g')
    item['document_id']='g'
    assert attach_evidence_scopes([item],store,scopes,'g')=={item['id']}
    assert item['context']['scope_ids']==[scopes[0]['id']]
    assert next(m for m in item['members'] if m['source_id']=='s')['locator']['physical_page']==2
    assert next(m for m in item['members'] if m['source_id']=='s')['role']=='section_context'
    peer_item=make_evidence('text',[(asdict(peer),'section_context'),(next_content,'content')],'g')
    peer_item['document_id']='g'
    attach_evidence_scopes([peer_item],store,scopes,'g')
    assert peer_item['context'].get('scope_ids',[])==[]
    assert all(m['source_id']!='s' for m in peer_item['members'])
    assert not register_scopes([{'scope':'section','heading_source_id':'unknown','condition_text':source.text,'source_members':[{'source_id':'s','locator':source.locator}]}],parser,store,'g')


def transport_client(monkeypatch,providers):
    config={'transport':{'transient_attempts':1,'backoff_seconds':0,'max_backoff_seconds':0},
            'model_routing':{'enabled_provider_prefixes':list(ModelClient.PROVIDER_PREFIXES)}}
    class ConfigPath:
        def __truediv__(self,_):return self
        def read_text(self,**_):return json.dumps(config)
    monkeypatch.setattr(transport,'PROJECT_ROOT',ConfigPath())
    settings=SimpleNamespace(llm_allow_evidence_send=True)
    for index,prefix in enumerate(ModelClient.PROVIDER_PREFIXES):
        values=providers[index] if index<len(providers) else ['', '', '', 10]
        for suffix,value in zip(('_base_url','_model','_api_key','_timeout_seconds'),values):
            setattr(settings,prefix+suffix,value)
    return ModelClient(settings)


def test_visual_failure_cache_is_scoped_to_endpoint_and_model(monkeypatch):
    model=transport_client(monkeypatch,[['https://first.test','same-model','secret',10],['https://second.test','same-model','secret',10]])
    calls=[]
    def request(request,timeout):
        calls.append(request.full_url)
        if 'first.test' in request.full_url:raise HTTPError(request.full_url,415,'unsupported',{},None)
        return {'choices':[{'message':{'content':'{"observation":"visible"}'}}]}
    model._request=request
    resource=SimpleNamespace(read_bytes=lambda:b'png-pixels')
    assert model.call('inspect','image',Budget(),images=[resource])['observation']=='visible'
    assert len(calls)==2 and model.vision_capability[('https://second.test/chat/completions','same-model')] is True
    assert model.vision_capability[('https://first.test/chat/completions','same-model')] is False




def test_auth_error_in_http_success_body_never_falls_back(monkeypatch):
    model=transport_client(monkeypatch,[['https://first.test','m','secret',10],['https://second.test','m','secret',10]])
    calls=[]
    def request(request,timeout):
        calls.append(request.full_url)
        return {'error':{'code':'invalid_api_key','message':'untrusted private response'}}
    model._request=request
    with pytest.raises(ModelAuthenticationError):model.call('system','input',Budget())
    assert len(calls)==1
    with pytest.raises(ModelError,match='缺少choices'):ModelClient._completion({'unexpected':True})
