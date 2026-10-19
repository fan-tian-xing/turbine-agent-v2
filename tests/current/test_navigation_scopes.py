"""Shared scope declarations participate in applicability without node duplication."""
from pathlib import Path
import pytest
from turbine_kg.knowledge.applicability import evaluate
from turbine_kg.ontology.registry import OntologyRegistry
from turbine_kg.retrieval.tools import Tools
from turbine_kg.runtime import Budget, Snapshot


def statement(key='statement'):
    return {'id':key,'document_id':'d','type':'requirement','scope_ids':['scope'],
            'conditions':[{'expression':{'op':'le','parameter':'间隙','value':.1,'unit':'mm','location':'接口'}}]}


def scope(**fields):
    return {'id':'scope','document_id':'d','scope':'section','condition_text':'本节适用于指定结构。',
            'physical_page_range':[7,9],**fields}


def tools_for(scopes,statements=None,error=None):
    statements=statements or [statement()]
    class Store:
        def __init__(self):self.read_calls=[];self.queries=[]
        def query(self,cypher,parameters,budget):
            self.queries.append((cypher,parameters))
            if 'RETURN count(s)' in cypher:return [{'total':len(statements)}]
            return [{'node':row,'matched_objects':[]} for row in statements]
        def get(self,ids,documents,budget):
            self.read_calls.append((ids,documents))
            if error:raise error
            return [row for row in scopes if row['id'] in ids]
    tools=Tools(Store(),OntologyRegistry(),Snapshot(('d',),'version',()),Budget())
    return tools


def test_raw_shared_scope_remains_unknown_and_is_returned_once():
    tools=tools_for([scope()],[statement('a'),statement('b')])
    result=tools.navigate('汽轮机',['has_requirement'])
    assert tools.store.read_calls==[(['scope'],('d',))]
    assert len(result['scopes'])==1 and result['scopes'][0]['condition_text']=='本节适用于指定结构。'
    for row in result['items']:
        assert row['scope_constraints']==[{'scope_id':'scope','expression':None,'status':'unknown'}]
        assert 'condition_text' not in row and '本节适用于' not in str(row)
        assert evaluate({'op':'and','items':[row['conditions'][0]['expression'],row['scope_constraints'][0]['expression']]},
                        {'间隙':{'value':.05,'unit':'mm','location':'接口'}},tools.registry)=='unknown'
    assert not tools.diagnostics


def test_local_false_with_unknown_scope_obeys_three_valued_and():
    tools=tools_for([scope()])
    row=tools.navigate('汽轮机',['has_requirement'])['items'][0]
    expression={'op':'and','items':[row['conditions'][0]['expression'],row['scope_constraints'][0]['expression']]}
    assert evaluate(expression,{'间隙':{'value':.2,'unit':'mm','location':'接口'}},tools.registry)=='not_applicable'


def test_explicit_parsed_scope_expression_can_be_evaluated_with_local_condition():
    expression={'op':'eq','parameter':'结构','value':'型式A','vocabulary':['型式A','型式B']}
    tools=tools_for([scope(expression=expression,expression_status='verified')])
    row=tools.navigate('汽轮机',['has_requirement'])['items'][0]
    assert row['scope_constraints'][0]['status']=='parsed'
    combined={'op':'and','items':[row['conditions'][0]['expression'],row['scope_constraints'][0]['expression']]}
    assert evaluate(combined,{'结构':'型式A','间隙':{'value':.05,'unit':'mm','location':'接口'}},tools.registry)=='applicable'
    assert evaluate(combined,{'结构':'型式B','间隙':{'value':.05,'unit':'mm','location':'接口'}},tools.registry)=='not_applicable'


def test_missing_wrong_version_or_failed_scope_reads_are_unknown_diagnostics():
    for declarations,error in [([],None),([scope(document_id='other')],None),([],RuntimeError('unavailable'))]:
        tools=tools_for(declarations,error=error)
        result=tools.navigate('汽轮机',['has_requirement'])
        assert len(result['items'])==1
        assert result['items'][0]['scope_constraints'][0]['expression'] is None
        assert result['items'][0]['scope_constraints'][0]['status']=='unknown'
        assert tools.diagnostics[-1]['kind']=='scope_metadata_unknown'
        assert not result['scopes']


def test_unknown_expression_status_does_not_upgrade_raw_scope_to_applicable():
    tools=tools_for([scope(expression={'op':'eq','parameter':'结构','value':'型式A'},expression_status='unverified')])
    result=tools.navigate('汽轮机',['has_requirement'])
    assert result['items'][0]['scope_constraints'][0]['expression'] is None
    assert result['scopes'][0]['status']=='unknown'


def test_scope_read_cache_is_shared_between_navigation_calls():
    tools=tools_for([scope()])
    tools.navigate('汽轮机',['has_requirement'])
    tools.navigate('汽轮机',['has_requirement'])
    assert tools.store.read_calls==[(['scope'],('d',))]


def test_navigation_uses_registered_edge_types():
    tools=tools_for([scope()])
    tools.navigate('汽轮机',['has_requirement','has_inspection'],hops=2)
    query,params=tools.store.queries[0]
    assert '[:HAS_REQUIREMENT|HAS_INSPECTION*1..2]' in query
    assert 'RELATES' not in query
    assert "type(r)=toUpper(r.relation_id)" in query
    assert "coalesce(r.role,'') <> 'related'" in query
    assert params['relations']==['has_requirement','has_inspection']


def test_navigation_ranks_ontology_paths_before_paging():
    registry=OntologyRegistry()
    tools=tools_for([scope()])
    tools.registry=registry
    tools.focus_tasks=[{'object':'水箱','query_terms':['安装','试验']}]
    tools.navigate('水箱',['has_requirement'])
    query,params=next((query,params) for query,params in tools.store.queries
                      if 'ORDER BY path_rank DESC' in query)
    assert 'SKIP $offset LIMIT $limit' in query
    assert registry.domain_class('水箱')['id'] in params['rank_5']
    assert registry.domain_class('箱罐')['id'] in params['rank_4']
    assert params['query_terms']==['安装','试验']


def test_navigation_excludes_related_only_paths_for_typed_edges():
    paths=[
        ('related-typed','HAS_REQUIREMENT','related'),
        ('owned-subject','HAS_REQUIREMENT','subject'),
        ('owned-quantity','HAS_REQUIREMENT','quantity_target'),
    ]

    class Store:
        def query(self,cypher,parameters,budget):
            assert "all(r IN relationships(path) WHERE" in cypher
            assert "coalesce(r.role,'') <> 'related'" in cypher
            matched=[(key,kind,role) for key,kind,role in paths
                     if kind=='HAS_REQUIREMENT' and role!='related'
                     and parameters['documents']==['d'] and parameters['relations']==['has_requirement']]
            if 'RETURN count(s)' in cypher:return [{'total':len(matched)}]
            return [{'node':{'id':key,'document_id':'d','type':'requirement','scope_ids':[]},
                     'matched_objects':[]} for key,_,_ in matched]

    tools=Tools(Store(),OntologyRegistry(),Snapshot(('d',),'version',()),Budget())
    result=tools.navigate('汽轮机',['has_requirement'])
    assert result['coverage']['matched']==2
    assert [item['id'] for item in result['items']]==['owned-subject','owned-quantity']


def test_domain_class_navigation_reaches_versioned_parent_before_statement():
    registry=OntologyRegistry()
    child=registry.domain_class('水箱')
    parent=registry.domain_class('箱罐')
    assert child and parent
    seen=[]

    class Store:
        def query(self,cypher,parameters,budget):
            seen.append((cypher,parameters))
            if 'SUBCLASS_OF' in cypher and 'RETURN related.id' in cypher:
                assert parameters['ontology_version']==registry.version
                assert parameters['documents']==['d']
                assert 'r.document_id IN $documents' not in cypher
                if 'MATCH path=(root' in cypher:
                    return [{'id':parent['id'],'class_id':parent['class_id'],
                             'semantic_edges':[{'document_id':'d','ontology_version':registry.version}]}]
                return []
            if 'r:PART_OF' in cypher or 'r:BROADER_CONCEPT' in cypher:return []
            if 'RETURN count(s)' in cypher:return [{'total':1}]
            assert child['id'] in parameters['ids'] and parent['id'] in parameters['ids']
            return [{'node':{'id':'general','document_id':'d','type':'requirement','scope_ids':[]},
                     'matched_objects':[{'id':parent['id'],'label':'箱罐','class_id':'Tank'}]}]

        def get(self,*args):return []

    tools=Tools(Store(),registry,Snapshot(('d',),registry.version,()),Budget())
    item=tools.navigate('水箱',['has_requirement'])['items'][0]
    assert item['ontology_paths'][0]['kind']=='ancestor'
    assert item['ontology_paths'][0]['object_id']==parent['id']
    assert all('Evidence' not in query for query,_ in seen)


def test_business_navigation_uses_statement_provenance_when_legacy_edge_omits_it():
    tools=tools_for([])
    tools.navigate('汽轮机',['has_requirement'])
    query=next(query for query,_ in tools.store.queries if 'RETURN count(s)' in query)
    assert 'coalesce(r.document_id,s.document_id)=s.document_id' in query
    assert 's.document_id IN $documents' in query
    assert 'r.ontology_version=$ontology_version' in query


@pytest.mark.parametrize('child_label,parent_label',[
    ('凝汽器','热交换器'),('高压加热器','加热器'),('高压加热器','热交换器'),
])
def test_registry_parent_axiom_recalls_parent_when_child_class_is_not_materialized(child_label,parent_label):
    registry=OntologyRegistry()
    child=registry.domain_class(child_label)
    parent=registry.domain_class(parent_label)
    assert child and parent

    class Store:
        def query(self,cypher,parameters,budget):
            if 'SUBCLASS_OF' in cypher and 'RETURN related.id' in cypher:return []
            if 'r:PART_OF' in cypher or 'r:BROADER_CONCEPT' in cypher:return []
            if 'RETURN count(s)' in cypher:
                assert parent['id'] in parameters['ids']
                return [{'total':1}]
            return [{'node':{'id':'parent-rule','document_id':'d','type':'requirement','scope_ids':[]},
                     'matched_objects':[{'id':parent['id'],'label':parent_label,'class_id':parent['class_id']}]}]

        def get(self,*args):return []

    tools=Tools(Store(),registry,Snapshot(('d',),registry.version,()),Budget())
    row=tools.navigate(child_label,['has_requirement'])['items'][0]
    assert row['ontology_paths'][0]['kind']=='ancestor'
    assert row['ontology_paths'][0]['path'][0]['basis']=='registry_axiom'
    assert row['ontology_paths'][0]['path'][0]['source_class_id']==child['class_id']


def test_unknown_words_do_not_create_concept_navigation_nodes():
    tools=tools_for([])
    tools.navigate('尚未审定的新词',['has_requirement'])
    queries=tools.store.queries
    assert all('BROADER_CONCEPT' not in query for query,_ in queries)
    assert all(params['ids']==[] for _,params in queries)
    assert all(params['object_labels']==['尚未审定的新词'] for _,params in queries)
