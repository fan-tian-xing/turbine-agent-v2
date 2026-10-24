from dataclasses import asdict
import time
import pytest
from turbine_kg.runtime import Budget,BudgetExceeded,Snapshot
from turbine_kg.evidence.context import make_evidence,member,table_evidence,ContextAssembler
from turbine_kg.ontology.registry import OntologyRegistry
from turbine_kg.knowledge.applicability import evaluate
from turbine_kg.agent.claims import binding_error


def cell(index,col,text,**attrs):
    return {'id':f'c-{index}-{col}','kind':'cell','text':text,'text_origin':'native_text','locator':{'physical_page':1,'bbox':[0,0,100,100]},'row_index':index,'column_index':col,'row_span':1,'column_span':1,**attrs}


def test_complete_merged_row_groups_keep_blank_cells_and_unknown_notes():
    cells=[cell(0,0,'类别',column_header=True),cell(0,1,'尺寸/mm',column_header=True),cell(1,0,'汽轮机',row_span=2,row_header=True),cell(1,1,'0.10'),cell(2,1,'',cell_state='blank')]
    note={'id':'n','kind':'table_note','text':'仅在冷态下','text_origin':'native_text','locator':{'physical_page':1},'attributes':{'coverage':{'status':'unknown'}}}
    rows=table_evidence({'id':'t','rows':3,'columns':2,'title':'表A'},cells+[note],'d')
    assert len(rows)==1
    assert rows[0]['context']['rows']==[1,2]
    assert {m['source_id'] for m in rows[0]['members']}=={c['id'] for c in cells}|{'n'}
    assert rows[0]['context']['continuation']['status']=='unknown'


def test_merged_stub_keeps_all_body_rows_even_when_layout_marks_it_as_header():
    cells=[cell(0,0,'静态刚性',row_span=3,column_header=True),cell(0,1,'低定位精度f≤S/500'),cell(1,1,'中定位精度f≤S/750'),cell(2,1,'高定位精度f≤S/1000')]
    rows=table_evidence({'id':'t','rows':3,'columns':2,'title':'表'},cells,'d')
    assert len(rows)==1 and rows[0]['context']['rows']==[0,1,2]
    assert {m['source_id'] for m in rows[0]['members']} == {c['id'] for c in cells}


class Store:
    def __init__(self,source,evidence): self.source=source;self.evidence=evidence
    def get(self,ids,documents=None,budget=None):
        nodes={self.source['id']:self.source,self.evidence['id']:self.evidence,'d':{'id':'d','title':'资料'}}
        return [nodes[key] for key in ids if key in nodes and (documents is None or nodes[key].get('document_id') in documents)]


def test_multiple_ranges_of_same_source_keep_independent_quotes_and_cache_refresh():
    source={'id':'p','kind':'paragraph','text':'甲条件。乙要求。','locator':{'physical_page':2},'document_id':'d','revision_id':'r'}
    evidence=make_evidence('text',[(source,'condition'),(source,'content')],'d')
    evidence.update(document_id='d',revision_id='r')
    evidence['members'][0]['locator']['char_range']=[0,4]
    evidence['members'][1]['locator']['char_range']=[4,8]
    store=Store(source,evidence);assembler=ContextAssembler(store);snapshot=Snapshot(('d',),'o',())
    first=assembler.read(evidence['id'],snapshot)
    assert [m['quote'] for m in first['members']]==['甲条件。','乙要求。']
    assert first['members'][0]['member_key']!=first['members'][1]['member_key']
    source['text']='丙条件。乙要求。'
    second=assembler.read(evidence['id'],snapshot)
    assert second['members'][0]['quote']=='丙条件。'
    assert second['member_fingerprint']!=first['member_fingerprint']
    with pytest.raises(ValueError): assembler.read(evidence['id'],Snapshot(('other',),'o',()))


def test_conditions_do_not_exclude_on_missing_or_unrecognized_user_data():
    registry=OntologyRegistry()
    assert evaluate({'op':'eq','parameter':'机型','value':'A'},{'机型':'B'},registry)=='unknown'
    assert evaluate({'op':'eq','parameter':'部件','value':'支持弹簧'},{'部件':'支撑弹簧'},registry)=='applicable'
    expr={'op':'le','parameter':'间隙','value':.1,'unit':'mm','location':'吊耳孔'}
    assert evaluate(expr,{},registry)=='unknown'
    assert evaluate(expr,{'间隙':{'value':.05,'unit':'mm','location':'销轴'}},registry)=='unknown'
    assert evaluate(expr,{'间隙':{'value':.00005,'unit':'m','location':'吊耳孔'}},registry)=='applicable'
    assert evaluate(expr,{'间隙':{'value':.2,'unit':'mm','location':'吊耳孔'}},registry)=='not_applicable'
    assert evaluate({'op':'or','items':[expr,{'op':'eq','parameter':'未填','value':'X'}]},{'间隙':{'value':.05,'unit':'mm','location':'吊耳孔'}},registry)=='applicable'


def test_deadline_and_tool_and_image_limits_are_shared():
    budget=Budget(seconds=.001);budget.started-=1
    with pytest.raises(BudgetExceeded): budget.remaining()
    budget=Budget(max_rounds=1,calls_per_round=2,max_images=1)
    budget.take(image=True)
    with pytest.raises(BudgetExceeded): budget.take(image=True)
    budget.take()
    with pytest.raises(BudgetExceeded): budget.take()


def test_image_caption_cannot_substitute_for_pixel_read():
    claim={'text':'孔径20mm','basis':'image','supports':[{'evidence_id':'e','member_keys':['image']}]}
    reads={'e':{'members':[{'member_key':'image','source_kind':'picture'}]}}
    assert binding_error(claim,reads,{})=='未成功读取图片像素'
    visual={'member_key':'image','status':'read','resource_read':True,'pixels_sent':True,'response_received':True}
    assert binding_error(claim,reads,{'e':[visual]}) is None
    assert binding_error(claim,reads,{'e':[{'member_key':'image','status':'read'}]})=='未成功读取图片像素'
    for flag in ('resource_read','pixels_sent','response_received'):
        assert binding_error(claim,reads,{'e':[{**visual,flag:False}]})=='未成功读取图片像素'


def test_structural_references_are_verified_separately_from_engineering_values():
    reads={'e':{'context':{'table_title':'表7.8.9 允许值'},'title':'资料','document_id':'d','revision_id':'r','evidence_id':'e',
                'members':[{'member_key':'m','quote':'冷态间隙应不大于0.10 mm，挠度f≤S/640（引用XY/Z1357-2024）。','locator':{'physical_page':3},'text_origin':'native_text','role':'content'}]}}
    claim={'basis':'source','text':'表7.8.9中，冷态间隙应不大于0.10 mm，挠度f≤S/640（引用XY/Z 1357—2024）。','supports':[{'evidence_id':'e','member_keys':['m']}]}
    assert binding_error(claim,reads,{}) is None
    assert binding_error({**claim,'text':claim['text'].replace('7.8.9','7.8.10')},reads,{})=='回答的表图或条款编号没有来源支持'
    # Standard identity/version meaning is checked by the independent source review.
    assert binding_error({**claim,'text':claim['text'].replace('2024','2025')},reads,{}) is None
    from turbine_kg.agent.claims import numeric_warning
    for text in (claim['text'].replace('/640','/641'),claim['text'].replace('0.10 mm','0.10 m')):
        changed={**claim,'text':text}
        assert binding_error(changed,reads,{}) is None
        assert numeric_warning(changed,reads)


def test_role_independent_entity_and_ontology_direction():
    registry=OntologyRegistry()
    assert registry.domain_class('支持弹簧')['id']==registry.domain_class('支撑弹簧')['id']
    registry.validate_relation('has_requirement','Component','Statement')
    with pytest.raises(ValueError): registry.validate_relation('has_requirement','Statement','Component')
    with pytest.raises(ValueError): registry.validate_relation('invented_relation','Mention','Statement')


def test_rotation_is_applied_once_and_pixel_crop_uses_display_coordinates():
    import pymupdf as fitz
    from turbine_kg.documents.page_mapping import PageMapper
    doc=fitz.open();page=doc.new_page(width=200,height=100);page.set_rotation(90)
    mapper=PageMapper.__new__(PageMapper);mapper.original=doc;mapper.mapping={}
    box=mapper.display_box([10,20,40,30],0)
    assert box==[70,10,80,40]
    pix=page.get_pixmap(clip=fitz.Rect(box))
    assert (pix.width,pix.height)==(10,30)
    with pytest.raises(ValueError):mapper.display_box([10,20,40,30],0,derived=True)
    doc.close()


def test_db_deadline_bounds_stalled_query_and_drops_late_result():
    from turbine_kg.knowledge.store import KnowledgeStore
    class Result:
        def data(self):time.sleep(.2);return [{'late':True}]
    class Driver:
        def session(self,**kw):return self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def run(self,*a):return Result()
    store=KnowledgeStore.__new__(KnowledgeStore);store.driver=Driver()
    store.settings=type('Settings',(),{'neo4j_database':'neo4j'})()
    start=time.monotonic()
    with pytest.raises(BudgetExceeded):store.query('RETURN 1',budget=Budget(seconds=.02))
    assert time.monotonic()-start<.15
