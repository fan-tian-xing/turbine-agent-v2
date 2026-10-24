"""Contracts where ontology mistakes would change an engineering answer."""
import json
from copy import deepcopy
from pathlib import Path
import pytest
from jsonschema import validate, ValidationError
from rdflib import Graph, Namespace, RDF, RDFS, OWL
from rdflib.compare import isomorphic
from turbine_kg.ontology.registry import OntologyRegistry
from turbine_kg.settings import PROJECT_ROOT


def facts(source_class, relation, target_class):
    registry = OntologyRegistry()
    ns = Namespace(registry.data['namespace'])
    graph = Graph()
    graph.add((ns['a'], RDF.type, ns[source_class]))
    graph.add((ns['b'], RDF.type, ns[target_class]))
    graph.add((ns['a'], ns[relation], ns['b']))
    return graph


def test_hole_requirement_does_not_become_mating_pin_requirement():
    registry = OntologyRegistry()
    registry.validate_relation('has_requirement', 'Mention', 'Statement', role='subject', statement_type='parameter')
    with pytest.raises(ValueError, match='角色'):
        registry.validate_relation('has_requirement', 'Mention', 'Statement', role='object', statement_type='parameter')
    with pytest.raises(ValueError, match='类型'):
        registry.validate_relation('has_requirement', 'Mention', 'Statement', role='subject', statement_type='cause')
    assert registry.navigation(['parameter'], roles=['object']) == []
    assert registry.navigation(['parameter'], roles=['subject']) == ['has_requirement']


def test_procedure_and_steps_are_supported_statement_records():
    registry=OntologyRegistry()
    records={"p":{"type":"procedure","entities":[]},"s":{"type":"step","entities":[]}}
    relation={"relation_id":"has_step","source_ref":{"statement_id":"p"},
              "target_ref":{"statement_id":"s"},"supports":[{"evidence_id":"e","member_keys":["m"]}]}
    assert registry.validate_semantic_relation(relation,[],records)["target"]["class"]=="Statement"
    with pytest.raises(ValueError,match="陈述类型"):
        registry.validate_semantic_relation({**relation,"source_ref":{"statement_id":"s"}},[],records)
    assert "ProcedureDefinition" not in registry.classes and "StepDefinition" not in registry.classes



def test_step_order_is_not_an_assertion_of_actual_activity():
    registry=OntologyRegistry()
    registry.validate_relation("step_precedes","Statement","Statement")
    for relation,source,target in [("step_precedes","Activity","Activity"),
                                  ("has_step","Activity","Statement")]:
        with pytest.raises(ValueError):registry.validate_relation(relation,source,target)



def test_concept_composition_cannot_assert_real_component_ownership():
    registry = OntologyRegistry()
    registry.validate_relation('part_of','Component','Equipment')
    registry.validate_relation('part_of','Mention','Mention')
    registry.validate_relation('part_of','OntologyClass','OntologyClass')
    registry.validate_relation('part_of','Mention','OntologyClass')
    registry.validate_relation('part_of','OntologyClass','Mention')
    with pytest.raises(ValueError, match='端点组合'):
        registry.validate_relation('part_of','Mention','Equipment')
    with pytest.raises(ValueError, match='端点组合'):
        registry.validate_relation('part_of','OntologyClass','Equipment')
    assert not registry.validate_graph(facts('Mention','part_of','Equipment'))[0]
    assert not registry.validate_graph(facts('OntologyClass','part_of','Equipment'))[0]
    assert registry.validate_graph(facts('OntologyClass','part_of','OntologyClass'))[0]
    assert registry.validate_graph(facts('Component','part_of','Equipment'))[0]


def test_taxonomy_is_reviewed_not_generated_as_free_concept_edges():
    registry=OntologyRegistry()
    assert "broader_concept" not in registry.relations
    with pytest.raises(ValueError):
        registry.validate_relation("subclass_of","OntologyClass","OntologyClass")



def test_shacl_rejects_wrong_direction_without_domain_inference():
    registry = OntologyRegistry()
    assert registry.validate_graph(facts('Component','has_requirement','Statement'))[0]
    assert not registry.validate_graph(facts('Statement','has_requirement','Component'))[0]
    ns = Namespace(registry.data['namespace'])
    graph = facts('Component','has_requirement','Statement')
    graph.remove((ns['a'], RDF.type, ns['Component']))
    # OWL domain would infer a class; validation must require asserted class membership.
    assert not registry.validate_graph(graph)[0]


def test_category_confusion_is_rejected_without_owl_reasoner():
    registry = OntologyRegistry(); ns = Namespace(registry.data['namespace'])
    graph = Graph()
    graph.add((ns['object'], RDF.type, ns['Activity']))
    graph.add((ns['object'], RDF.type, ns['Equipment']))
    assert not registry.validate_graph(graph)[0]


def test_semantic_relations_are_scoped_to_the_supporting_statement():
    registry = OntologyRegistry()
    entities = [{'label':'转子','class':'Mention','role':'subject'}, {'label':'汽轮机','class':'Mention','role':'related'}]
    row = {'relation_id':'part_of','source_entity':0,'target_entity':1}
    result = registry.validate_semantic_relation(row,entities)
    assert result['source']['label']=='转子' and result['target']['label']=='汽轮机'
    for bad in [{**row,'target_entity':2}, {**row,'source_entity':True}, {**row,'target_entity':0}, {**row,'relation_id':'has_measure'}]:
        with pytest.raises(ValueError): registry.validate_semantic_relation(bad, entities)


def test_extraction_schema_and_entity_validation_share_registry_vocabulary():
    registry = OntologyRegistry()
    item = {'type':'requirement','text':'卸卡应检查。',
            'supports':[{'evidence_id':'e','member_keys':['m']}],
            'entities':[{'label':'卸卡','class':'Mention','role':'subject'}]}
    validate(item,registry.extraction_schema()); registry.validate_entity(item['entities'][0])
    assert registry.canonical_label('支撑弹簧')=='支持弹簧'
    for bad in [{'label':'卸扣','class':'Invented','role':'subject'}, {'label':'卸扣','class':'Mention','role':'cause'}]:
        with pytest.raises(ValueError): registry.validate_entity(bad)
        with pytest.raises(ValidationError): validate({**item,'entities':[bad]},registry.extraction_schema())
    assert json.loads(json.dumps(registry.extraction_contract(),ensure_ascii=False))['ontology_version']==registry.version


def test_skill_required_items_have_real_registered_navigation():
    registry = OntologyRegistry()
    from turbine_kg.agent.skills import load_catalog
    skills = load_catalog(PROJECT_ROOT/'skills',registry=registry)
    for skill in skills:
        registry.validate_skill(skill)
        for item in skill['required_items']:
            navigation = set(registry.navigation([item]))
            if navigation:
                assert navigation & set(skill['relation_ids'])
            else:
                assert registry.data['navigation_policy']['evidence_fulltext_recall']
    with pytest.raises(ValueError): registry.validate_skill({'relation_ids':['invented'],'required_items':['cause']})
    with pytest.raises(ValueError): registry.validate_skill({'relation_ids':['has_requirement'],'required_items':['cause']})


def test_business_edge_write_uses_registered_type_and_rejects_mismatches():
    from turbine_kg.knowledge.store import KnowledgeStore
    store=object.__new__(KnowledgeStore)
    calls=[]
    store.query=lambda query,parameters: calls.append((query,parameters))
    row={'source':'concept','target':'statement','data':{'relation_id':'has_requirement','statement_id':'statement','document_id':'d','role':'subject','ontology_version':'v','support_evidence_ids':['e']}}
    store.edges('HAS_REQUIREMENT',[row])
    query,params=calls[0]
    assert '[r:HAS_REQUIREMENT' in query
    assert params['rows'][0]['relation_id']=='has_requirement'
    assert params['rows'][0]['document_id']=='d'
    assert params['rows'][0]['role']=='subject'
    assert params['rows'][0]['ontology_version']=='v'
    assert params['rows'][0]['support_evidence_ids']==['e']
    assert 'r.ontology_version=row.ontology_version' in query
    with pytest.raises(ValueError):store.edges('HAS_INSPECTION',[row])
    with pytest.raises(ValueError):store.edges('RELATES',[row])
    assert len(calls)==1


def test_hierarchy_can_expand_recall_but_has_no_value_inheritance_axiom():
    registry = OntologyRegistry(); ns = Namespace(registry.data['namespace'])
    core,_ = registry.authority()
    assert (ns['Thing'], RDF.type, OWL.Class) not in core
    assert set(core.subjects(RDFS.subClassOf,OWL.Thing))=={ns['PhysicalObject'],ns['Activity']}
    assert (ns['Activity'],RDFS.subClassOf,OWL.Thing) in core
    assert not list(core.triples((None,OWL.propertyChainAxiom,None)))
    assert registry.navigation(['measure'])==['has_measure']
    assert registry.data['navigation_policy']['inherit_measures'] is False
    assert 'inherit_quantities' not in registry.data['navigation_policy']


def test_checked_in_owl_and_shapes_match_current_registry():
    registry = OntologyRegistry()
    core,shapes = registry.authority()
    assert isomorphic(core,Graph().parse(PROJECT_ROOT/'ontology/core.ttl'))
    assert isomorphic(shapes,Graph().parse(PROJECT_ROOT/'ontology/shapes.ttl'))


def test_malformed_or_incomplete_numeric_conditions_remain_unknown():
    from turbine_kg.knowledge.applicability import evaluate
    registry=OntologyRegistry()
    expression={'op':'le','parameter':'孔径','value':20,'unit':'mm','location':'吊耳孔'}
    facts={'孔径':{'value':19,'unit':'mm','location':'吊耳孔'}}
    assert evaluate(expression,facts,registry)=='applicable'
    assert evaluate({key:value for key,value in expression.items() if key!='location'},
                    {'孔径':{'value':19,'unit':'mm'}},registry)=='unknown'
    assert evaluate('OCR未识别条件',facts,registry)=='unknown'
    assert evaluate({'op':'and','items':'unparsed'},facts,registry)=='unknown'
    for value in [float('nan'),float('inf'),True]:
        assert evaluate(expression,{'孔径':{**facts['孔径'],'value':value}},registry)=='unknown'
        assert evaluate({**expression,'value':value},facts,registry)=='unknown'
    assert evaluate({**expression,'parameter':None},facts,registry)=='unknown'
    assert evaluate(expression,None,registry)=='unknown'
    assert evaluate({'op':'eq','parameter':'孔径','value':20},{'孔径':20},registry)=='unknown'
    assert evaluate({'op':'eq','parameter':'机型','value':'A','vocabulary':None},{'机型':'B'},registry)=='unknown'
    assert evaluate({'op':'eq','parameter':'材料','value':'铜','vocabulary':['铜','钢']},{'材料':'钢'},registry)=='not_applicable'
    assert evaluate({'op':'eq','parameter':'孔径','value':.1,'unit':'mm','location':'吊耳孔'},
                    {'孔径':{'value':.0001,'unit':'m','location':'吊耳孔'}},registry)=='applicable'


def test_persisted_navigation_keeps_quantity_owner_and_statement_support():
    from turbine_kg.knowledge.statement_extraction import persist
    registry=OntologyRegistry();store=MemoryStore()
    item={"type":"parameter","text":"壳体尺寸按来源要求。", "entities":[
        {"label":"壳体","class":"Mention","role":"subject"},
        {"label":"管板","class":"Mention","role":"object"},
        {"label":"凝汽器","class":"Mention","role":"related"}],
        "supports":[{"evidence_id":"e","member_keys":["m"]}]}
    assert persist(store,[item],"g",registry)==1
    nav=store.links["HAS_REQUIREMENT"]
    assert registry.domain_class("壳体")["id"] in {row["source"] for row in nav}
    assert registry.domain_class("管板")["id"] not in {row["source"] for row in nav}
    assert registry.domain_class("凝汽器")["id"] not in {row["source"] for row in nav}
    assert store.links["SUPPORTS"][0]["target"]=="e"



def test_object_procedure_step_path_keeps_each_record_source():
    from turbine_kg.knowledge.statement_extraction import persist
    registry=OntologyRegistry();store=MemoryStore()
    procedure=supported_item(extraction_id="p",type="procedure",text="壳体安装程序。",
        entities=[{"label":"壳体","class":"Mention","role":"subject"}],
        semantic_relations=[{"relation_id":"has_step","source_ref":{"statement_id":"p"},
          "target_ref":{"statement_id":"s"},"supports":[{"evidence_id":"e","member_keys":["m"]}]}])
    step=supported_item(extraction_id="s",type="step",text="检查壳体。",
        entities=[{"label":"壳体","class":"Mention","role":"subject"}])
    persist(store,[procedure,step],"g",registry)
    saved={row["type"]:row for row in store.nodes["Statement"]}
    edge=store.links["HAS_STEP"][0]
    assert edge["source"]==saved["procedure"]["id"] and edge["target"]==saved["step"]["id"]
    assert len(store.links["SUPPORTS"])==2
    assert "Activity" not in store.nodes and "Mention" not in store.nodes



class MemoryStore:
    def __init__(self): self.nodes={}; self.links={}
    def put(self,label,rows): self.nodes.setdefault(label,[]).extend(rows)
    def edges(self,kind,rows): self.links.setdefault(kind,[]).extend(rows)


def supported_item(**extra):
    return {'type':'fact', 'text':'来源事实', 'entities':[], 'modality':'unspecified', 'negation':False,
            'possibility':'confirmed', 'supports':[{'evidence_id':'e', 'member_keys':['m'], 'quotes':[{'member_key':'m', 'text':'来源事实'}]}], **extra}


def test_structured_values_are_rejected_before_persistence():
    from turbine_kg.knowledge.statement_extraction import persist
    registry=OntologyRegistry(); store=MemoryStore()
    item=supported_item(entities=[{'label':'部件','class':'Mention','role':'subject'}],
                        quantities=[{'target':'部件','value':.1,'unit':'mm'}],
                        formulas=[{'expression':'f=S/500'}])
    with pytest.raises(ValueError, match='不再保存'):
        persist(store,[item],'g',registry)
    assert store.nodes=={}


def test_source_formula_stays_in_statement_text():
    from turbine_kg.knowledge.statement_extraction import persist
    store=MemoryStore()
    persist(store,[supported_item(text='来源公式 f=S/500，适用于冷态。')],'g',OntologyRegistry())
    result=store.nodes['Statement'][0]
    assert result['text']=='来源公式 f=S/500，适用于冷态。'
    assert not {'conditions','quantities','formulas'} & set(result)


def test_real_occurrence_identity_uses_source_and_time_without_roles():
    from turbine_kg.knowledge.statement_extraction import persist
    registry=OntologyRegistry();store=MemoryStore()
    entities=[{"label":"设备1","class":"Equipment","role":"subject","occurrence":{"identifier":"1"}},
              {"label":"设备1","class":"Equipment","role":"object","occurrence":{"identifier":"1"}},
              {"label":"检查","class":"Activity","role":"related","occurrence":{"object":"设备1","time":"上午"}},
              {"label":"检查","class":"Activity","role":"related","occurrence":{"object":"设备1","time":"下午"}}]
    persist(store,[supported_item(entities=entities)],"g",registry)
    assert store.nodes["Equipment"][0]["id"]==store.nodes["Equipment"][1]["id"]
    assert store.nodes["Activity"][0]["id"]!=store.nodes["Activity"][1]["id"]



@pytest.mark.parametrize('entity_class',['PhysicalObject','Equipment','Component','System','Tool','Activity'])
@pytest.mark.parametrize('occurrence',[None,{},
    {'identifier':None,'object':None,'location':None,'time':None,
     'source_context':[{'revision_id':'r'}],'source_anchors':[{'source_id':'source-1'}]}])
def test_source_provenance_does_not_create_an_unidentified_real_instance(entity_class,occurrence):
    from turbine_kg.knowledge.statement_extraction import persist
    registry=OntologyRegistry();store=MemoryStore()
    entity={'label':'来源通用名称','class':entity_class,'role':'related'}
    if occurrence is not None:entity['occurrence']=occurrence
    with pytest.raises(ValueError,match='身份'):
        persist(store,[supported_item(entities=[entity])],'g',registry)
    assert store.nodes=={} and store.links=={}


def test_report_with_explicit_equipment_component_tool_and_activity_identity_is_retained():
    from turbine_kg.knowledge.statement_extraction import extract,persist
    text='检修记录：08:30在A区使用扳手W2检查1号油泵的叶轮。'
    entities=[{'label':'油泵','class':'Equipment','role':'subject',
               'occurrence':{'identifier':'1号','object':'油泵','location':'A区'}},
              {'label':'叶轮','class':'Component','role':'object',
               'occurrence':{'object':'1号油泵','location':'A区'}},
              {'label':'扳手','class':'Tool','role':'object',
               'occurrence':{'identifier':'W2','location':'A区'}},
              {'label':'检查','class':'Activity','role':'related',
               'occurrence':{'object':'1号油泵','location':'A区','time':'08:30'}}]
    view={'evidence_id':'report-e','document_id':'report-doc','revision_id':'report-revision',
          'kind':'text','context':{},'members':[{'member_key':'report-body','source_id':'report-source',
          'role':'content','source_kind':'paragraph','quote':text,'locator':{'physical_page':11}}]}
    class Model:
        def call(self,system,payload,budget):
            assert not system.startswith('独立核验')
            return {'statements':[supported_item(text=text,entities=deepcopy(entities),
                supports=[{'evidence_id':'e0','member_keys':['e0m0']}])]}
    registry=OntologyRegistry();items,coverage=extract([view],Model(),registry)
    assert len(items)==1 and coverage['rejected']==[]
    store=MemoryStore();persist(store,items,'g',registry)
    for entity in entities:
        occurrence=store.nodes[entity['class']][0]['occurrence']
        assert all(occurrence[key]==value for key,value in entity['occurrence'].items())
        assert occurrence['source_context']==[{'document_id':'report-doc','revision_id':'report-revision'}]
        assert occurrence['source_anchors']==[{'source_id':'report-source','locator':{'physical_page':11}}]
    assert store.nodes['Statement'][0]['text']==text


def test_same_real_instance_final_id_self_loop_is_rejected():
    from turbine_kg.knowledge.statement_extraction import persist
    registry=OntologyRegistry(); store=MemoryStore()
    entities=[{'label':'设备1','class':'Equipment','role':'subject','occurrence':{'identifier':'1','source_context':[{'revision_id':'r'}],'source_anchors':[{'source_id':'s-one'}]}},
              {'label':'设备1','class':'Equipment','role':'related','occurrence':{'identifier':'1','source_context':[{'revision_id':'r'}],'source_anchors':[{'source_id':'s-two'}]}}]
    with pytest.raises(ValueError,match='自环'):
        persist(store,[supported_item(entities=entities, semantic_relations=[{'relation_id':'part_of','source_entity':0,'target_entity':1}])],'g',registry)
    assert store.nodes=={} and store.links=={}


def source_view():
    return {'evidence_id':'real-e','kind':'text','revision_id':'revision-A','context':{'section':'范围'},'members':[
        {'member_key':'real-heading','source_id':'heading-source','role':'context','source_kind':'heading','quote':'5 范围','locator':{'physical_page':1},'text_origin':'native_text'},
        {'member_key':'real-body','source_id':'body-source','role':'content','source_kind':'paragraph','quote':'本章适用于冷态检查。','locator':{'physical_page':2,'bbox':[1,2,3,4]},'text_origin':'derived_ocr'}]}


def test_this_chapter_scope_cannot_bind_a_child_section_heading():
    from turbine_kg.knowledge.statement_extraction import _scope_declaration
    view=source_view()
    view['members'].append({'member_key':'child','source_id':'child-heading','role':'section',
        'source_kind':'heading','quote':'5.1 一般规定','locator':{'physical_page':1}})
    row={'evidence_id':'real-e','member_keys':['real-body'],'scope':'section',
         'heading_source_id':'child-heading','condition_text':'本章适用于冷态检查。'}
    with pytest.raises(ValueError,match='真实章标题'):
        _scope_declaration(row,{'real-e':view},{},{})


class ScopeModel:
    def __init__(self, declaration, *, statements=None): self.declaration=declaration; self.calls=[]; self.statements=statements or []
    def call(self,system,payload,budget):
        self.calls.append((system,json.loads(payload)))
        assert not system.startswith('独立核验')
        return {'statements':self.statements,'scope_declarations':[self.declaration],
                'evidence_status':[{'evidence_id':'e0','status':'complete','reason':'明确范围'}]}


def test_scope_aliases_and_exact_source_locator_survive_single_pass():
    from turbine_kg.knowledge.statement_extraction import extract
    view=source_view()
    model=ScopeModel({'evidence_id':'e0','member_keys':['e0m1'],'scope':'section','heading_source_id':'e0m0','condition_text':'本章适用于冷态检查。'})
    statements,coverage=extract([view],model,OntologyRegistry())
    assert statements==[] and coverage['rejected']==[] and len(model.calls)==1
    declaration=coverage['source_scopes'][0]
    assert declaration['evidence_id']=='real-e' and declaration['member_keys']==['real-body'] and declaration['heading_source_id']=='heading-source'
    assert declaration['source_members']==[{key:view['members'][1][key] for key in ('member_key','source_id','locator','quote','text_origin')}]
    assert model.calls[0][1][0]['members'][1]['member_key']=='e0m1'


@pytest.mark.parametrize('replacement',[{'member_keys':['e0m0'],'condition_text':'范围'}, {'heading_source_id':'invented'}, {'condition_text':'适用于所有工况'}])
def test_scope_cannot_be_inferred_from_title_unknown_heading_or_retyped_conditions(replacement):
    from turbine_kg.knowledge.statement_extraction import extract
    row={'evidence_id':'e0','member_keys':['e0m1'],'scope':'section','heading_source_id':'e0m0','condition_text':'本章适用于冷态检查。',**replacement}
    model=ScopeModel(row)
    _,coverage=extract([source_view()],model,OntologyRegistry())
    assert coverage['source_scopes']==[] and coverage['rejected'] and len(model.calls)==1


def test_scope_source_text_mismatch_blocks_inheritance():
    from turbine_kg.knowledge.statement_extraction import extract
    model=ScopeModel({'evidence_id':'e0','member_keys':['e0m1'],'scope':'section','heading_source_id':'heading-source','condition_text':'本章适用于全部工况。'})
    _,coverage=extract([source_view()],model,OntologyRegistry())
    assert coverage['source_scopes']==[] and coverage['rejected']


def test_statement_schema_does_not_define_structured_quantity_or_formula_fields():
    from turbine_kg.knowledge.statement_extraction import persist
    registry=OntologyRegistry();store=MemoryStore()
    assert not {'conditions','quantities','formulas'} & set(registry.extraction_schema()['properties'])
    invalid_store=MemoryStore()
    with pytest.raises(ValueError,match='不再保存'):
        persist(invalid_store,[supported_item(quantities=[{'target':'设备','value':.1,'unit':'mm'}])],'g',registry)
    assert invalid_store.nodes=={} and invalid_store.links=={}
