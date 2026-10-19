import json

import pytest

from turbine_kg.agent.model import ModelError
from turbine_kg.knowledge.extract import _safe_model_error, extract
from turbine_kg.ontology.registry import OntologyRegistry
from turbine_kg.runtime import BudgetExceeded


@pytest.mark.parametrize('error,expected',[
    (ModelError('配置的模型均未返回可用结果：TimeoutError'),'模型请求超时'),
    (BudgetExceeded('deadline reached'),'总预算或阶段截止时间已到'),
    (ModelError('配置的模型均未返回可用结果：JSONDecodeError'),'模型响应无法解析为JSON'),
    (ModelError('模型API响应缺少文字content'),'模型API响应缺少文字content'),
    (ModelError('HTTP 503 context_length_exceeded'),'HTTP 503'),
])
def test_model_failure_details_allow_only_known_safe_facts(error,expected):
    error.args=(str(error)+' https://secret-host.example/request Authorization: Bearer secret-token rawpayload={"source":"private-content"}',)
    result=_safe_model_error(error)
    assert expected in result
    assert all(value not in result for value in ('secret-host','secret-token','private-content','Authorization','rawpayload'))


@pytest.mark.parametrize('retry_limit',[0,1,2])
def test_source_bound_extraction_never_calls_a_second_model(retry_limit):
    view={'evidence_id':'actual','kind':'text','context':{},'members':[
        {'member_key':'member','source_id':'source','role':'content','quote':'设备应检查。'}]}
    calls=[]
    class Model:
        def call(self,system,payload,budget):
            calls.append(budget)
            if system.startswith('独立核验'):
                raise AssertionError('不得调用第二个模型核验')
            return {'statements':[{'text':'设备应检查。','type':'requirement',
                'entities':[{'label':'设备','class':'Mention','role':'subject'}],
                'supports':[{'evidence_id':'e0','member_keys':['e0m0']}]}],
                'evidence_status':[{'evidence_id':'e0','status':'complete','reason':'已覆盖'}]}
    items,coverage=extract([view],Model(),OntologyRegistry(),repair=True,seconds=20,max_retries=retry_limit)
    assert len(calls)==1 and len(items)==1
    assert coverage['attempt_count']==1 and len(coverage['attempt_history'])==1
    assert coverage['evidence']['actual']['status']=='complete'
