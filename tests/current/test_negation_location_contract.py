"""The minimal extraction contract keeps negation in Statement text."""

from turbine_kg.knowledge.statement_extraction import extract
from turbine_kg.ontology.registry import OntologyRegistry


def test_negative_requirement_is_preserved_without_modal_flags():
    view={'evidence_id':'e','kind':'text','context':{},'members':[
        {'member_key':'m','source_id':'s','role':'content','quote':'部件边缘应平整、无毛刺。'}]}
    class Model:
        def call(self,system,payload,budget):
            assert '否定' in system and 'conditions、quantities、formulas全部省略' in system
            return {'statements':[{'text':'部件边缘应平整、无毛刺。','type':'requirement',
                'entities':[{'label':'部件','class':'Mention','role':'subject'}],
                'supports':[{'evidence_id':'e0','member_keys':['e0m0']}]}],
                'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    statements,coverage=extract([view],Model(),OntologyRegistry())
    assert len(statements)==1 and coverage['evidence']['e']['status']=='complete'
    assert statements[0]['text']=='部件边缘应平整、无毛刺。'
    assert not {'modality','negation','possibility'} & statements[0].keys()
