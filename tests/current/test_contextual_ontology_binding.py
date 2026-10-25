"""Class bindings need source mentions; device context is not a new component kind."""
import json
import pytest

from turbine_kg.knowledge.statement_extraction import extract
from turbine_kg.knowledge.statement_sync import persist
from turbine_kg.ontology.registry import OntologyRegistry


def source_view(section, body):
    return {"e": {"context": {"section": section}, "members": [
        {"member_key": "heading", "role": "section", "quote": section},
        {"member_key": "body", "role": "content", "quote": body},
    ]}}


def support():
    return [{"evidence_id": "e", "member_keys": ["body"]}]


@pytest.mark.parametrize("surface,class_id,section", [
    ("冷却管", "CoolingTube", "5.2 凝汽器"),
    ("冷却管", "CoolingTube", "其他换热设备"),
    ("管板", "TubeSheet", "安装要求"),
    ("设备", "Equipment", "安装要求"),
    ("活动", "Activity", "通用过程"),
])
def test_generic_kind_is_grounded_in_source_name_not_chapter(surface,class_id,section):
    basis=OntologyRegistry().ontology_binding(surface,class_id,support(),
                                              source_view(section,f"{surface}应检查。"))
    assert basis["method"]=="exact_source_name"
    assert basis["source_members"]==[{"evidence_id":"e","member_key":"body"}]
    assert "heading_member" not in basis


@pytest.mark.parametrize("surface,class_id,body", [
    ("冷却管", "CoolingTube", "管板应无缺陷。"),
    ("冷却管", "Condenser", "冷却管应无缺陷。"),
    ("冷却管", "UnreviewedKind", "冷却管应无缺陷。"),
])
def test_title_or_nearby_terms_cannot_fabricate_a_binding(surface,class_id,body):
    with pytest.raises(ValueError):
        OntologyRegistry().ontology_binding(surface,class_id,support(),source_view("5.2 凝汽器",body))


def test_exact_alias_requires_source_mention():
    registry=OntologyRegistry()
    basis=registry.ontology_binding("减温减压器","DesuperheatingPressureReducingDevice",support(),
                                    source_view("安装","减温减压器应检查。"))
    assert basis["method"]=="exact_source_name"
    with pytest.raises(ValueError):
        registry.ontology_binding("减温减压器","DesuperheatingPressureReducingDevice",support(),
                                  source_view("安装","装置应检查。"))


def test_qualified_part_name_keeps_device_context_without_a_compound_class():
    registry=OntologyRegistry()
    basis=registry.ontology_binding("凝汽器壳体","Shell",support(),
                                    source_view("安装","凝汽器壳体应检查。"))
    assert basis["method"]=="qualified_source_name"
    assert basis["context_class_id"]=="Condenser"
    assert registry.domain_class("凝汽器壳体") is None
    with pytest.raises(ValueError):
        registry.ontology_binding("高压加热器","Heater",support(),
                                  source_view("安装","高压加热器应检查。"))


def test_clause_lead_can_bind_a_qualified_part_without_a_model_class_guess():
    registry=OntologyRegistry()
    assert registry.qualified_class("凝汽器壳体")["class_id"]=="Shell"
    view={"evidence_id":"e","kind":"text","context":{"item_number":"2"},"members":[
        {"member_key":"lead","source_id":"lead","role":"section","source_kind":"list_item",
         "quote":"凝汽器壳体现场组装应符合下列规定："},
        {"member_key":"body","source_id":"body","role":"content","source_kind":"list_item",
         "quote":"壳体应垫平、垫实。"}]}
    class Model:
        def call(self,system,user,_budget):
            assert not system.startswith('独立核验')
            return {"statements":[{"type":"requirement","text":"凝汽器壳体现场组装时，壳体应垫平、垫实。",
                "entities":[{"label":"凝汽器壳体","class":"Mention","role":"subject"}],
                "supports":[{"evidence_id":"e0","member_keys":["e0m0","e0m1"]}]}],
                "evidence_status":[{"evidence_id":"e0","status":"complete"}]}
    statements,coverage=extract([view],Model(),registry,repair=True)
    assert coverage["evidence"]["e"]["status"]=="complete"
    bound=statements[0]["entities"][0]
    assert bound["ontology_class_id"]=="Shell"
    assert bound["ontology_binding"]["context_class_id"]=="Condenser"


class Store:
    def __init__(self): self.nodes={}; self.links={}
    def put(self,kind,rows): self.nodes.setdefault(kind,[]).extend(rows)
    def edges(self,kind,rows): self.links.setdefault(kind,[]).extend(rows)


def test_generic_part_and_device_context_survive_persistence_separately():
    registry=OntologyRegistry()
    views=source_view("安装","凝汽器的冷却管应检查。")
    entities=[]
    for label,class_id,role in [("冷却管","CoolingTube","subject"),("凝汽器","Condenser","related")]:
        entities.append({"label":label,"class":"Mention","role":role,"ontology_class_id":class_id,
                         "ontology_binding":registry.ontology_binding(label,class_id,support(),views)})
    store=Store()
    persist(store,[{"type":"requirement","text":"凝汽器的冷却管应检查。",
                    "entities":entities,"supports":support()}],"g",registry)
    saved=store.nodes["Statement"][0]
    assert {(item["ontology_class_id"],item["role"]) for item in saved["entities"]} == {
        ("CoolingTube","subject"),("Condenser","related")}
    assert "Mention" not in store.nodes
    assert any(item["class_id"]=="Thing" for item in store.nodes["OntologyClass"])
    targets={row["source"] for row in store.links["HAS_REQUIREMENT"]}
    assert registry.domain_class("冷却管")["id"] in targets
    assert registry.domain_class("凝汽器")["id"] not in targets
    mention=store.links["MENTIONS"][0]
    assert mention["source"]==registry.domain_class("凝汽器")["id"]
    assert mention["target"]==saved["id"]
    assert mention["data"]["role"]=="related"
    assert mention["data"]["source_members"]==[{"evidence_id":"e","member_key":"body"}]


def test_source_headings_add_context_without_turning_it_into_an_entity_or_obligation():
    from copy import deepcopy
    from turbine_kg.knowledge.entity_bindings import context_class_members
    registry=OntologyRegistry()
    views={'e':{'context':{'clause_source_id':'intro'},'members':[
        {'member_key':'h','role':'section','source_kind':'heading','quote':'7.4 间接空冷装置'},
        {'member_key':'lead','source_id':'intro','role':'content','source_kind':'list_item','quote':'系统水冲洗应符合下列规定：'},
        {'member_key':'m','role':'content','source_kind':'list_item','quote':'临时排水管截面积应符合要求。'},
        {'member_key':'scope','role':'section_context','source_kind':'paragraph','quote':'本章适用于凝汽器和除氧器。'}]}}
    fact={'text':'临时排水管截面积应符合要求。','type':'requirement',
          'entities':[{'label':'临时排水管','class':'Mention','role':'subject'}],
          'supports':[{'evidence_id':'e','member_keys':['lead','m']}]}
    original=deepcopy(fact)
    contexts=context_class_members(fact,views,registry)
    assert set(contexts)=={'IndirectAirCoolingSystem'}
    store=Store();persist(store,[fact],'d',registry,source_views=views)
    assert fact==original
    assert [{k:e[k] for k in ('label','class','role')} for e in store.nodes['Statement'][0]['entities']]==fact['entities']
    assert 'HAS_REQUIREMENT' not in store.links
    cue=store.links['MENTIONS'][0]
    assert cue['source']==registry.domain_class_by_id('IndirectAirCoolingSystem')['id']
    assert cue['data']['context_members']==[{'evidence_id':'e','member_key':'h','context_kind':'section'}]
    assert cue['data']['source_members']==[] and cue['data']['inherit_values'] is False


def test_unselected_clause_and_unverified_section_string_do_not_create_context_edges():
    from turbine_kg.knowledge.entity_bindings import context_class_members
    registry=OntologyRegistry()
    fact={'entities':[],'supports':[{'evidence_id':'e','member_keys':['body']}]}
    views={'e':{'context':{'section':'凝汽器','clause_source_id':'intro'},'members':[
        {'member_key':'lead','source_id':'intro','role':'content','quote':'除氧器应清扫。'},
        {'member_key':'body','role':'content','quote':'零部件应完好。'}]}}
    assert context_class_members(fact,views,registry)=={}
    fact['supports'][0]['member_keys'].append('lead')
    assert set(context_class_members(fact,views,registry))=={'Deaerator'}


def test_qualified_name_projects_owner_as_context_without_changing_subject():
    registry=OntologyRegistry();views=source_view('安装','凝汽器壳体应垫平。')
    entity={'label':'凝汽器壳体','class':'Mention','role':'subject','ontology_class_id':'Shell',
            'ontology_binding':registry.ontology_binding('凝汽器壳体','Shell',support(),views)}
    store=Store();persist(store,[{'text':'凝汽器壳体应垫平。','type':'requirement',
        'entities':[entity],'supports':support()}],'d',registry,source_views=views)
    assert {r['source'] for r in store.links['HAS_REQUIREMENT']}=={registry.domain_class_by_id('Shell')['id']}
    assert {r['source'] for r in store.links['MENTIONS']}=={registry.domain_class_by_id('Condenser')['id']}


@pytest.mark.parametrize('label,expected',[('凝汽器','Condenser'),('新支架',None)])
def test_persistence_rebinds_invalid_mapping_without_discarding_source_core(label,expected):
    registry=OntologyRegistry()
    quote=label+'应检查。'
    views={'e':{'members':[{'member_key':'m','role':'content','quote':quote}]}}
    fact={'id':'keep','text':quote,'type':'requirement','supports':[{'evidence_id':'e','member_keys':['m'],'quotes':[]}],
          'entities':[{'label':label,'class':'Mention','role':'subject','ontology_class_id':'Deaerator',
                       'ontology_binding':{'method':'exact_source_name','class_id':'Deaerator',
                                           'source_members':[{'evidence_id':'e','member_key':'m'}]}}]}
    store=Store();persist(store,[fact],'d',registry,preserve_ids=True,source_views=views)
    saved=store.nodes['Statement'][0]
    assert saved['id']=='keep' and saved['text']==quote and saved['supports']==fact['supports']
    assert saved['entities'][0].get('ontology_class_id')==expected
    assert not any(row['source']==registry.domain_class_by_id('Deaerator')['id'] for row in store.links.get('HAS_REQUIREMENT',[]))


@pytest.mark.parametrize('role,kind',[('object','requirement'),('subject','limitation')])
def test_mapped_entity_without_owner_navigation_retains_its_role_and_gets_recall_cue(role,kind):
    registry=OntologyRegistry()
    views={'e':{'members':[{'member_key':'m','role':'content','quote':'凝汽器应检查。'}]}}
    refs=[{'evidence_id':'e','member_keys':['m']}]
    entity={'label':'凝汽器','class':'Mention','role':role,'ontology_class_id':'Condenser',
            'ontology_binding':registry.ontology_binding('凝汽器','Condenser',refs,views)}
    store=Store();persist(store,[{'text':'凝汽器应检查。','type':kind,'entities':[entity],'supports':refs}],
                          'd',registry,source_views=views)
    assert not store.links.get('HAS_REQUIREMENT')
    assert store.nodes['Statement'][0]['entities'][0]['role']==role
    cue=store.links['MENTIONS'][0]
    assert cue['data']['entity_roles']==[role] and cue['data']['role']=='related'


@pytest.mark.parametrize("label,upper,proposed,reason", [
    ("新管件", "Mention", "UnregisteredKind", "未注册"),
    ("凝汽器", "Equipment", "Installation", "上层类别不符"),
])
def test_invalid_optional_mapping_preserves_supported_statement(label,upper,proposed,reason):
    occurrence={"identifier":"C1"} if upper=="Equipment" else None
    body=f"编号C1的{label}应检查。" if occurrence else f"{label}应检查。"
    view={"evidence_id":"e","kind":"text","context":{},
          "members":[{"member_key":"body","source_id":"s","role":"content","quote":body}]}
    class Model:
        def call(self,system,user,_budget):
            assert not system.startswith('独立核验')
            return {"statements":[{"type":"requirement","text":body,
                "entities":[{"label":label,"class":upper,"role":"subject","ontology_class_id":proposed,
                             **({"occurrence":occurrence} if occurrence else {})}],
                "supports":[{"evidence_id":"e","member_keys":["body"]}]}],
                "evidence_status":[{"evidence_id":"e","status":"complete","reason":""}]}
    statements,coverage=extract([view],Model(),OntologyRegistry(),seconds=15)
    assert len(statements)==1 and coverage["evidence"]["e"]["status"]=="complete"
    entity=statements[0]["entities"][0]
    if label == "新管件":
        assert "ontology_class_id" not in entity
        assert entity["ontology_mapping_review"]["status"] == "unmapped"
    else:
        assert entity["ontology_class_id"] == "Condenser"
    store=Store();persist(store,statements,"g",OntologyRegistry())
    assert "Mention" not in store.nodes
    assert store.nodes["Statement"][0]["text"]==body
    if occurrence: assert store.nodes["Equipment"][0]["occurrence"]["identifier"]=="C1"
