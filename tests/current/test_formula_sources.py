from copy import deepcopy

import pytest

from turbine_kg.knowledge.statement_extraction import _statement_identity, extract
from turbine_kg.knowledge.formula_sources import (
    confirm_formula_source, formula_source_binding,
    verified_formula_representation, apply_formula_reviews,
)
from turbine_kg.ontology.registry import OntologyRegistry


def source():
    return {'id':'source-formula','document_id':'doc','revision_id':'rev',
            'kind':'paragraph','text':'OCR expression has uncertain symbols.',
            'locator':{'original_hash':'original-hash','physical_page':12,'bbox':[10,20,100,50]}}


def formula():
    return {'expression':'z=x*(1-r)','variables':[{'symbol':'z','meaning':'length result'},
        {'symbol':'x','meaning':'input length'},{'symbol':'r','meaning':'relative change','range':{'lower':0.01,'upper':0.03}}],
        'units':{'z':'mm','x':'mm','r':'1'},'conditions':None}


def reviewed_source(**overrides):
    original=source()
    arguments={'binding':formula_source_binding(original),'raw_text':original['text'],
               'verified_text':'Confirmed expression z=x*(1-r).','formula':formula(),
               'user_confirmation':{'confirmed':True,'actor':'user','note':'I confirm this expression and the supplied variable meanings and units.'}}
    arguments.update(overrides)
    return confirm_formula_source(original,**arguments)


def statement():
    return {'id':'same-existing-statement','document_id':'doc','revision_id':'rev','type':'fact','entities':[],
            'supports':[{'evidence_id':'evidence','member_keys':['native-member']}]}


@pytest.mark.parametrize('field', ['id','document_id','revision_id','text'])
def test_stale_source_review_is_not_usable_after_record_or_raw_content_changes(field):
    corrected=reviewed_source();corrected[field]='changed'
    assert verified_formula_representation(corrected) is None


@pytest.mark.parametrize('field,value', [('original_hash','new-hash'),('physical_page',13),('bbox',[0,0,10,10])])
def test_review_remains_bound_to_original_file_page_and_region(field,value):
    corrected=reviewed_source();corrected['locator'][field]=value
    assert verified_formula_representation(corrected) is None


def test_review_requires_explicit_user_confirmation_and_raw_content_binding():
    for proof in ({}, {'confirmed':True,'actor':'model','note':'guessed'}, {'confirmed':True,'actor':'user','note':''}):
        with pytest.raises(ValueError,match='用户明确确认'):reviewed_source(user_confirmation=proof)
    with pytest.raises(ValueError,match='原始文本'):reviewed_source(raw_text='other OCR')
    invalid=formula();invalid['units']['r']=1
    with pytest.raises(ValueError,match='字符串'):reviewed_source(formula=invalid)


def test_formula_review_metadata_does_not_create_a_new_fact_identity():
    registry=OntologyRegistry();first=statement();first['text']='formula method'
    second=deepcopy(first);second['text']='formula method'
    assert _statement_identity(first,'doc',registry)==_statement_identity(second,'doc',registry)


def portable_annotations():
    from hashlib import sha256
    original=source();review=reviewed_source()['attributes']['human_verified_representation']
    return {'original-hash':{'formula_reviews':[{'original_hash':'original-hash','physical_page':12,
        'bbox_pdf_pt':original['locator']['bbox'],'raw_text_sha256':sha256(original['text'].encode('utf-8')).hexdigest(),
        **{key:deepcopy(review[key]) for key in ('verified_text','formula','user_confirmation')}}]}}


def test_portable_review_rebinds_new_source_and_document_only_with_exact_raw_ocr_region():
    original=source();original.update(id='new-source',revision_id='new-rev',document_id='new-doc')
    updated=apply_formula_reviews(original,portable_annotations())
    review=verified_formula_representation(updated)
    assert review['binding']['source_id']=='new-source' and review['binding']['document_id']=='new-doc'
    assert updated['text']==original['text'] and original.get('attributes') is None
    for field,value in [('original_hash','other'),('physical_page',13),('bbox',[0,0,10,10])]:
        altered=deepcopy(original);altered['locator'][field]=value
        assert 'human_verified_representation' not in apply_formula_reviews(altered,portable_annotations()).get('attributes',{})
    altered=deepcopy(original);altered['text']+='changed OCR'
    assert 'human_verified_representation' not in apply_formula_reviews(altered,portable_annotations()).get('attributes',{})
    invalid=portable_annotations();invalid['original-hash']['formula_reviews'][0]['user_confirmation']['actor']='model'
    with pytest.raises(ValueError,match='用户明确确认'):apply_formula_reviews(original,invalid)


def test_save_page_applies_review_after_binding_persisted_source_versions(monkeypatch):
    from turbine_kg.documents.ir import ParsedPage, SourceUnit
    from turbine_kg.knowledge.store import KnowledgeStore
    import turbine_kg.knowledge.formula_sources as reviews
    original=source();captured=[]
    actual=reviews.apply_formula_reviews
    def apply(row,annotations):
        captured.append(row)
        return actual(row,portable_annotations())
    monkeypatch.setattr(reviews,'apply_formula_reviews',apply)
    class MemoryStore(KnowledgeStore):
        def __init__(self):self.saved={}
        def put(self,label,rows):self.saved.setdefault(label,[]).extend(deepcopy(rows))
        def edges(self,*args):pass
        def query(self,*args):return []
    store=MemoryStore();unit=SourceUnit('new-source','paragraph',original['text'],original['locator'],'ocr_text',0)
    store.save_page(ParsedPage(12,None,100,100,{},sources=[unit]),'doc','new-rev')
    saved=store.saved['SourceText'][0]
    assert captured[0]['document_id']=='doc'
    assert verified_formula_representation(saved)['binding']['revision_id']=='new-rev'
    assert saved['text']==original['text'] and set(store.saved)=={'Page','SourceText','Picture'}
