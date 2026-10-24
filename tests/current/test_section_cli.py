"""The public maintenance CLI can only enter explicitly configured sections."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from turbine_kg.cli import ingest as cli


@pytest.fixture
def entry(monkeypatch):
    scopes=[{'document_id':'doc','scope':'section','section':'7','physical_pages':[11,14],'sample_pages':[11,13],'description':'测试章节'}]
    calls=[]
    class Config:
        def __truediv__(self,key):return self
        def read_text(self,**kwargs):return json.dumps(scopes)
    class Store:
        def __init__(self,*args):calls.append(('store',))
        def __enter__(self):return self
        def __exit__(self,*args):return False
    def handler(kind):
        def call(path,store,*args,**kwargs):
            calls.append((kind,path,args,kwargs));return {'status':'processing'}
        return call
    monkeypatch.setattr(cli,'PROJECT_ROOT',Config())
    monkeypatch.setattr(cli.Settings,'from_environment',lambda:SimpleNamespace(source_root=Path('sources')))
    monkeypatch.setattr(cli,'document_id',lambda *args:'doc')
    monkeypatch.setattr(cli,'rows',lambda name:[] if name=='derived_asset_links.tsv' else [{'document_logical_id':'doc','relative_path':'source.pdf'}])
    monkeypatch.setattr(cli,'KnowledgeStore',Store)
    monkeypatch.setattr(cli,'ModelClient',lambda settings:'model')
    monkeypatch.setattr(cli,'OntologyRegistry',lambda:'registry')
    monkeypatch.setattr(cli,'prepare_section',handler('prepare'))
    monkeypatch.setattr(cli,'process_section',handler('process'))
    def invoke(arguments):
        monkeypatch.setattr('sys.argv',['ingest',*arguments]);return cli.main()
    return scopes,calls,invoke


def test_source_without_section_uniquely_matches_config_and_never_enters_full_book(entry):
    scopes,calls,invoke=entry
    assert invoke(['source.pdf','--workers','3'])==0
    kind,path,args,kwargs=calls[-1]
    assert kind=='process' and args[-1]==scopes[0]
    assert kwargs['work_pages'] is None and kwargs['workers']==3
    assert 'activate_effective' not in kwargs and 'pages' not in kwargs


def test_planned_preparation_and_explicit_sample_retry_use_scoped_handlers(entry):
    scopes,calls,invoke=entry
    assert invoke(['--planned','--prepare-only'])==0 and calls[-1][0]=='prepare'
    assert invoke(['source.pdf','--section','7','--retry-semantics','--work-pages','11,13'])==0
    assert calls[-1][0]=='process' and calls[-1][-1]['work_pages']==[11,13]
    assert calls[-1][-1]['retry_semantics']


@pytest.mark.parametrize('arguments',[
    ['source.pdf','--pages','1-141'],['source.pdf','--preview'],['source.pdf','--finalize'],
    ['source.pdf','--work-pages','10'],['source.pdf','--work-pages','14-13'],
    ['source.pdf','--section','8'],['--planned','--section','7'],
    ['source.pdf','--prepare-only','--retry-semantics']])
def test_old_full_controls_and_invalid_scope_are_rejected_before_database(entry,arguments):
    _,calls,invoke=entry
    with pytest.raises(SystemExit) as error:invoke(arguments)
    assert error.value.code==2 and calls==[]


def test_unconfigured_source_and_ambiguous_section_cannot_fall_back_to_full_book(entry):
    scopes,calls,invoke=entry
    scopes[0]['document_id']='other'
    with pytest.raises(SystemExit):invoke(['source.pdf'])
    assert not calls
    scopes[0]['document_id']='doc';scopes.append({**scopes[0],'section':'8'})
    with pytest.raises(SystemExit):invoke(['source.pdf'])
    assert not calls
    assert invoke(['source.pdf','--section','7'])==0


def test_non_section_configuration_cannot_enable_planned_full_book(entry):
    scopes,calls,invoke=entry;scopes[0]['scope']='full'
    with pytest.raises(SystemExit):invoke(['--planned'])
    assert calls==[]


def test_statement_sync_exposes_shared_helpers():
    from turbine_kg.knowledge import statement_sync
    assert all(callable(getattr(statement_sync,key)) for key in ('_table_context_sources','_support_closure','_discard_statements'))


def test_result_printing_preserves_json_in_a_gbk_terminal(entry,monkeypatch):
    from io import BytesIO,TextIOWrapper
    _,_,invoke=entry
    output=BytesIO();stream=TextIOWrapper(output,encoding='gbk')
    monkeypatch.setattr('sys.stdout',stream)
    monkeypatch.setattr(cli,'process_section',lambda *args,**kwargs:{'issues':['−20℃']})
    assert invoke(['source.pdf'])==0
    stream.flush()
    text=output.getvalue().decode('gbk')
    assert json.loads(text[text.index('{'):])=={'issues':['−20℃']}
