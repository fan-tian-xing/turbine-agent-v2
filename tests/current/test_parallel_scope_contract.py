"""Independent branches keep their source conditions in complete text."""

from turbine_kg.knowledge.extract import extract
from turbine_kg.ontology.registry import OntologyRegistry


def test_parallel_subjects_keep_local_stage_and_number_on_own_branch():
    view={'evidence_id':'e','kind':'text','context':{},'members':[
        {'member_key':'m','source_id':'s','role':'content',
         'quote':'阀门和管道均应进行压力试验；其他在热态下的部件应进行压力试验，试验压力为2 MPa。'}]}
    class Model:
        def call(self,system,payload,budget):
            return {'statements':[
                {'text':'阀门和管道均应进行压力试验。','type':'requirement',
                 'entities':[{'label':'阀门','class':'Mention','role':'subject'},
                             {'label':'管道','class':'Mention','role':'subject'}],
                 'supports':[{'evidence_id':'e0','member_keys':['e0m0']}]},
                {'text':'其他在热态下的部件应进行压力试验，试验压力为2 MPa。','type':'requirement',
                 'entities':[{'label':'部件','class':'Mention','role':'subject'}],
                 'supports':[{'evidence_id':'e0','member_keys':['e0m0']}]}],
                'evidence_status':[{'evidence_id':'e0','status':'complete'}]}
    statements,coverage=extract([view],Model(),OntologyRegistry())
    assert len(statements)==2 and coverage['evidence']['e']['status']=='complete'
    assert '热态' not in statements[0]['text'] and '2 MPa' not in statements[0]['text']
    assert '热态' in statements[1]['text'] and '2 MPa' in statements[1]['text']


def test_structured_quantity_is_rejected_under_minimal_protocol():
    view={'evidence_id':'e','kind':'text','context':{},'members':[
        {'member_key':'m','source_id':'s','role':'content','quote':'部件压力应为2 MPa。'}]}
    class Model:
        def call(self,system,payload,budget):
            return {'statements':[{'text':'部件压力应为2 MPa。','type':'requirement',
                'entities':[{'label':'部件','class':'Mention','role':'subject'}],
                'quantities':[{'target':'部件','value':2,'unit':'MPa'}],
                'supports':[{'evidence_id':'e0','member_keys':['e0m0']}]}]}
    statements,coverage=extract([view],Model(),OntologyRegistry())
    assert not statements and coverage['evidence']['e']['status']=='incomplete'
    assert '不再保存' in coverage['rejected'][0]
