"""Minimal Statements keep each requirement's scope in its own text."""

from turbine_kg.knowledge.extract import extract
from turbine_kg.ontology.registry import OntologyRegistry


def test_distinct_strengths_and_stage_remain_in_source_bound_text():
    view={'evidence_id':'e','kind':'text','context':{},'members':[{'member_key':'m','source_id':'s','role':'content',
        'quote':'水位宜位于基准以下15 mm，持续6 h应无渗漏。'}]}
    class Model:
        def call(self,system,payload,budget):
            assert 'conditions、quantities、formulas全部省略' in system
            return {'statements':[
                {'text':'水位宜位于基准以下15 mm。','type':'requirement',
                 'entities':[{'label':'水位','class':'Mention','role':'subject'}],
                 'supports':[{'evidence_id':'e0','member_keys':['e0m0']}]},
                {'text':'持续6 h应无渗漏。','type':'requirement',
                 'entities':[{'label':'水位','class':'Mention','role':'subject'}],
                 'supports':[{'evidence_id':'e0','member_keys':['e0m0']}]}],
                'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    statements,coverage=extract([view],Model(),OntologyRegistry())
    assert len(statements)==2 and coverage['evidence']['e']['status']=='complete'
    assert [item['text'] for item in statements]==['水位宜位于基准以下15 mm。','持续6 h应无渗漏。']
    assert all(not any(item.get(field) for field in ('conditions','quantities','formulas')) for item in statements)
