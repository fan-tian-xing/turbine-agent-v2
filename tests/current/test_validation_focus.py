"""Minimal extraction retains source wording and rejects unsupported structure."""

from turbine_kg.knowledge.statement_extraction import extract
from turbine_kg.ontology.registry import OntologyRegistry


def test_prohibition_with_subject_remains_a_navigable_requirement():
    view={'evidence_id':'e','kind':'text','context':{},'members':[
        {'member_key':'m','source_id':'s','role':'content','quote':'阀门不得开启。'}]}
    class Model:
        def call(self,system,payload,budget):
            return {'statements':[{'text':'阀门不得开启。','type':'requirement',
                'entities':[{'label':'阀门','class':'Mention','role':'subject'}],
                'supports':[{'evidence_id':'e0','member_keys':['e0m0']}]}],
                'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    registry=OntologyRegistry()
    statements,coverage=extract([view],Model(),registry)
    assert len(statements)==1 and coverage['evidence']['e']['status']=='complete'
    assert statements[0]['text']=='阀门不得开启。'
    assert 'has_requirement' in registry.navigation(['requirement'],roles=['subject'])


def test_duration_and_ambiguous_owner_stay_in_full_text_without_new_node():
    view={'evidence_id':'e','kind':'text','context':{},'members':[
        {'member_key':'m','source_id':'s','role':'content','quote':'液位宜低于基准点50mm，并保持12h无渗漏。'}]}
    class Model:
        def call(self,system,payload,budget):
            return {'statements':[{'text':'液位宜低于基准点50mm，并保持12h无渗漏。',
                'type':'requirement','entities':[],
                'supports':[{'evidence_id':'e0','member_keys':['e0m0']}]}],
                'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    statements,coverage=extract([view],Model(),OntologyRegistry())
    assert len(statements)==1 and coverage['evidence']['e']['status']=='complete'
    assert statements[0]['text']==view['members'][0]['quote']
    assert statements[0]['entities']==[] and statements[0].get('quantities',[])==[]


def test_answer_claim_binding_is_deterministic_and_does_not_call_a_second_model():
    from types import SimpleNamespace
    from turbine_kg.agent.claims import validate
    view={'evidence_id':'e','title':'source','document_id':'d','revision_id':'r','members':[
        {'member_key':'m','quote':'间隙应不大于0.10 mm。','locator':{},'text_origin':'native_text','role':'content'}]}
    claim={'basis':'source','text':'间隙应不大于0.10 mm。','supports':[{'evidence_id':'e','member_keys':['m']}]}
    kept,rejected=validate([claim],SimpleNamespace(reads={'e':view},visuals={},summary=[]))
    assert len(kept)==1 and not rejected
