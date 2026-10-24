from copy import deepcopy

import pytest

from turbine_kg.evidence.context import ContextAssembler, make_evidence
from turbine_kg.knowledge.formula_sources import confirm_formula_source, formula_source_binding
from turbine_kg.runtime import Snapshot


def fixture():
    raw = {'id':'formula-source','kind':'formula','text':'uncertain OCR',
           'document_id':'doc','revision_id':'rev',
           'locator':{'original_hash':'hash','physical_page':7,'bbox':[1,2,3,4]}}
    corrected = confirm_formula_source(raw, binding=formula_source_binding(raw),
        raw_text=raw['text'], verified_text='z = x(1-r)',
        formula={'expression':'z = x(1-r)','variables':[], 'units':{}, 'conditions':None},
        user_confirmation={'actor':'user','confirmed':True,'note':'I confirm the corrected formula.'})
    evidence = make_evidence('text', [(raw,'content')], 'doc'); evidence['document_id']='doc'; evidence['revision_id']='rev'
    class Store:
        source = corrected
        def get(self, ids, documents=None, budget=None):
            nodes = {self.source['id']:self.source,evidence['id']:evidence,'doc':{'title':'source document'}}
            return [deepcopy(nodes[key]) for key in ids if key in nodes]
    return Store(), evidence


def test_read_uses_confirmed_text_with_raw_provenance_and_invalidates_cache():
    store, evidence = fixture(); assembler = ContextAssembler(store)
    snap = Snapshot(('doc',), 'ontology', ())
    view = assembler.read(evidence['id'], snap)
    item = view['members'][0]
    assert item['quote'] == 'z = x(1-r)' and item['raw_quote'] == 'uncertain OCR'
    assert item['member_key'] == evidence['members'][0]['member_key']
    assert item['text_origin'] == 'verified_correction'
    assert 'human_verified_representation' in item['attributes']
    store.source['text'] = 'changed OCR'
    stale = assembler.read(evidence['id'], snap)
    assert stale['members'][0]['quote'] == 'changed OCR'
    assert 'human_verified_representation' not in stale['members'][0]['attributes']
    assert view['member_fingerprint'] != stale['member_fingerprint']


@pytest.mark.parametrize('locator_change', [{'char_range':[0,3]}, {'bbox':[0,0,5,5]}, {'physical_page':8}])
def test_partial_or_different_evidence_locator_cannot_apply_whole_source_confirmation(locator_change):
    store, evidence = fixture()
    evidence['members'][0]['locator'].update(locator_change)
    item = ContextAssembler(store).read(evidence['id'],Snapshot(('doc',),'ontology',()))['members'][0]
    assert item['quote'] == ('unc' if 'char_range' in locator_change else 'uncertain OCR')
    assert 'human_verified_representation' not in item['attributes']
    assert item['attributes']['human_review_status'] == 'stale_or_partial_binding'
