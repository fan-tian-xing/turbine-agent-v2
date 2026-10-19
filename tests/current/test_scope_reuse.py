from copy import deepcopy
import pytest

from turbine_kg.knowledge.extract import extract, _scope_declaration
from turbine_kg.ontology.registry import OntologyRegistry


def verified_view(role='section_context'):
    condition='本章仅适用于冷态设备。'
    locator={'physical_page':1,'bbox':[0,0,10,10],'original_hash':'original-hash'}
    return {'evidence_id':'evidence','document_id':'d','kind':'text','context':{'scope_ids':['existing-scope'],
        'registered_scopes':[{'id':'existing-scope','document_id':'d','scope':'section','heading_source_id':'heading',
            'condition_text':condition,'source_ids':['scope-source'],'source_bindings':[{'source_id':'scope-source','locator':deepcopy(locator)}]}]},
        'members':[{'member_key':'heading-key','source_id':'heading','source_kind':'heading','role':'section','quote':'3 安装',
                    'locator':{'physical_page':1}},
                   {'member_key':'scope-key','source_id':'scope-source','source_kind':'paragraph','role':role,'quote':condition,
                    'locator':locator,'attributes':{'scope_source':True,'semantic_status':'verified_scope',
                                                    'applies_to':'section','scope_heading_id':'heading'}},
                   {'member_key':'body-key','source_id':'body','source_kind':'paragraph','role':'content','quote':'设备应检查。',
                    'locator':{'physical_page':2}}]}


def proposal():
    return {'evidence_id':'evidence','member_keys':['scope-key'],'scope':'section','heading_source_id':'heading',
            'condition_text':'本章仅适用于冷态设备。'}


@pytest.mark.parametrize('role',['scope','section_context','content'])
def test_registered_verified_scope_repeated_by_model_is_reused_without_new_declaration_or_failure(role):
    view=verified_view(role)
    class Model:
        def call(self,system,payload,budget):
            declaration={**proposal(),'evidence_id':'e0','member_keys':['e0m1'],'heading_source_id':'e0m0'}
            return {'statements':[{'text':'设备应检查。','type':'requirement','entities':[{'label':'设备','class':'Mention','role':'subject'}],
                'supports':[{'evidence_id':'e0','member_keys':['e0m1','e0m2']}]}],
                'scope_declarations':[declaration],'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    items,coverage=extract([view],Model(),OntologyRegistry(),repair=True)
    assert len(items)==1 and coverage['source_scopes']==[] and coverage['rejected']==[]
    assert coverage['reused_scopes'][0]['reused_scope_ids']==['existing-scope']
    assert coverage['evidence']['evidence']['status']=='complete' and not coverage.get('repair_attempted')


@pytest.mark.parametrize('invalid',['unverified','fake_header','different_scope','different_heading','changed_condition','missing_registered_proof','changed_locator','changed_document'])
def test_inherited_scope_cannot_be_registered_reused_or_widened_without_exact_existing_proof(invalid):
    view=verified_view();row=proposal()
    if invalid=='unverified':view['members'][1]['attributes']['semantic_status']='unverified'
    if invalid=='fake_header':view['members'][1]['attributes']['source_role']='header'
    if invalid=='different_scope':row.update(scope='document',heading_source_id=None)
    if invalid=='different_heading':
        view['members'].append({'member_key':'other-heading','source_id':'other-heading','source_kind':'heading','role':'section','quote':'3.1 子节','locator':{'physical_page':2}})
        row['heading_source_id']='other-heading'
    if invalid=='changed_condition':row['condition_text']='仅适用于冷态设备'
    if invalid=='missing_registered_proof':view['context']['registered_scopes']=[]
    if invalid=='changed_locator':view['context']['registered_scopes'][0]['source_bindings'][0]['locator']['physical_page']=99
    if invalid=='changed_document':view['context']['registered_scopes'][0]['document_id']='other-document'
    with pytest.raises(ValueError):_scope_declaration(row,{'evidence':view},{},{})


def test_only_new_native_content_can_propose_a_new_scope_declaration():
    view=verified_view('content');view['context']={};view['members'][1]['attributes']={}
    result=_scope_declaration(proposal(),{'evidence':view},{},{})
    assert not result.get('reused') and result['source_members'][0]['source_id']=='scope-source'


def test_inherited_registered_scope_is_context_not_a_new_completeness_obligation():
    class Model:
        def call(self,system,payload,budget):
            assert not system.startswith('独立核验')
            return {'statements':[{'text':'设备应检查。','type':'requirement',
                'entities':[{'label':'设备','class':'Mention','role':'subject'}],
                'modality':'must','negation':False,'possibility':'confirmed',
                'supports':[{'evidence_id':'e0','member_keys':['e0m1','e0m2']}]}],
                'evidence_status':[{'evidence_id':'e0','status':'complete','reason':'已覆盖'}]}
    items,coverage=extract([verified_view()],Model(),OntologyRegistry(),repair=True)
    assert len(items)==1 and not coverage['rejected']
    assert coverage['evidence']['evidence']['status']=='complete'
    assert not coverage.get('repair_attempted')
