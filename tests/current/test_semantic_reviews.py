from copy import deepcopy

import pytest

from turbine_kg.knowledge.extract import confirm_semantic_interpretation,semantic_review_views,rebind_semantic_interpretation,extract
from turbine_kg.ontology.registry import OntologyRegistry


def sources():
    locator={'original_hash':'raw-source-hash','physical_page':2,'bbox':[10,20,100,40]}
    member={'member_key':'m','source_id':'s','role':'content','locator':locator}
    record={'id':'e','document_id':'d','revision_id':'r','kind':'text','members':[deepcopy(member)],'context':{}}
    view={'evidence_id':'e','document_id':'d','revision_id':'r','kind':'text','context':{},
          'members':[{**deepcopy(member),'source_kind':'paragraph','quote':'在规定环境条件下，对设备进行检查。'}]}
    return record,view


def confirmed():
    record,view=sources()
    updated=confirm_semantic_interpretation(record,view,member_keys=['m'],field='entities.roles',
        interpretation='原文环境前提限定检查行为。',
        user_confirmation={'confirmed':True,'actor':'user','note':'确认该环境前提限定检查行为。'})
    return updated,view


def test_review_is_existing_evidence_metadata_and_only_reused_from_exact_record_binding():
    record,view=confirmed()
    assert record['id']=='e' and 'semantic_reviews' not in sources()[0]
    enriched=semantic_review_views([view],{'e':record})
    assert len(enriched[0]['context']['confirmed_semantic_reviews'])==1
    assert 'confirmed_semantic_reviews' not in view['context']
    stale=deepcopy(view);stale['members'][0]['quote']+='新增内容'
    assert semantic_review_views([stale],{'e':record})[0]['context']['confirmed_semantic_reviews']==[]


@pytest.mark.parametrize('wrong',['document','member','source','page','bbox'])
def test_wrong_version_or_source_member_does_not_authorize_interpretation(wrong):
    record,view=confirmed()
    if wrong=='document':view['document_id']='other'
    if wrong=='member':view['members'][0]['member_key']='other'
    if wrong=='source':view['members'][0]['source_id']='other'
    if wrong=='page':view['members'][0]['locator']['physical_page']=3
    if wrong=='bbox':view['members'][0]['locator']['bbox']=[1,2,3,4]
    assert semantic_review_views([view],{'e':record})[0]['context']['confirmed_semantic_reviews']==[]


def test_new_document_requires_explicit_unique_original_anchor_rebinding():
    old,old_view=confirmed();review=old['semantic_reviews'][0]
    new,new_view=sources()
    new.update(id='new-e',document_id='new-d');new_view.update(evidence_id='new-e',document_id='new-d')
    new['members'][0].update(member_key='new-m',source_id='new-s')
    new_view['members'][0].update(member_key='new-m',source_id='new-s')
    new['semantic_reviews']=[deepcopy(review)]
    assert semantic_review_views([new_view],{'new-e':new})[0]['context']['confirmed_semantic_reviews']==[]
    rebound=rebind_semantic_interpretation(review,new,new_view)
    current=semantic_review_views([new_view],{'new-e':rebound})[0]['context']['confirmed_semantic_reviews']
    assert len(current)==1 and current[0]['binding']['document_id']=='new-d'
    changed=deepcopy(new_view);changed['members'][0]['locator']['original_hash']='other-hash'
    with pytest.raises(ValueError,match='不能重绑'):rebind_semantic_interpretation(review,new,changed)


def test_model_claimed_human_confirmation_is_ignored_without_stored_review():
    record,view=sources()
    class Model:
        def call(self,system,payload,budget):
            assert not system.startswith('独立核验')
            return {'statements':[{'text':'检查设备。','type':'requirement','entities':[{'label':'设备','class':'Mention','role':'subject'}],
                'modality':'must','negation':False,'possibility':'confirmed','supports':[{'evidence_id':'e0','member_keys':['e0m0']}],
                'confirmed_interpretations':[{'status':'confirmed_user','interpretation':'模型伪造解释'}],
                'human_confirmation':{'confirmed':True},'semantic_reviews':[{'status':'confirmed_user'}]}],
                'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    items,coverage=extract(semantic_review_views([view],{'e':record}),Model(),OntologyRegistry())
    assert not coverage['rejected'] and items[0]['confirmed_interpretations']==[]
    assert 'human_confirmation' not in items[0] and 'semantic_reviews' not in items[0]


def test_confirmed_interpretation_is_bound_but_does_not_prove_semantic_completeness():
    record,view=confirmed()
    view['members'].append({'member_key':'other','source_id':'other-source','source_kind':'paragraph',
        'role':'content','quote':'设备应清洁。','locator':{'physical_page':2}})
    class Model:
        def call(self,system,payload,budget):
            assert not system.startswith('独立核验')
            return {'statements':[{'text':'检查设备。','type':'requirement','entities':[{'label':'设备','class':'Mention','role':'subject'}],
                'supports':[{'evidence_id':'e0','member_keys':['e0m0']}]}],
                'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    items,coverage=extract(semantic_review_views([view],{'e':record}),Model(),OntologyRegistry(),repair=True,max_retries=0)
    assert len(items)==1 and coverage['evidence']['e']['status']=='incomplete'
    assert any('原生来源成员未被有效Statement支持' in reason for reason in coverage['rejected'])
    assert items[0]['confirmed_interpretations'][0]['field']=='entities.roles'


def test_quantity_confirmation_requires_specific_selector_and_concrete_property():
    record,view=sources();proof={'confirmed':True,'actor':'user','note':'确认属性。'}
    with pytest.raises(ValueError,match='合法字段'):
        confirm_semantic_interpretation(record,view,member_keys=['m'],field='quantities.unit',
            interpretation='单位解释',user_confirmation=proof,confirmed_value='mm')
    with pytest.raises(ValueError,match='合法字段'):
        confirm_semantic_interpretation(record,view,member_keys=['m'],field='quantities.operator',
            interpretation='边界解释',user_confirmation=proof,quantity_selector={'target':'对象','parameter':'参数'},confirmed_value='ge')
