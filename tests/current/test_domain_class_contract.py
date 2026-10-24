"""Domain classes keep strict taxonomy separate from source terminology."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from rdflib import Namespace, RDF, RDFS, OWL, SKOS, Literal, URIRef

from turbine_kg.knowledge.store import KnowledgeStore
from turbine_kg.ontology.registry import OntologyRegistry
from turbine_kg.retrieval.tools import Tools
from turbine_kg.runtime import Budget


def test_domain_class_identity_is_stable_across_alias_and_version_changes():
    current = OntologyRegistry()
    original = current.domain_class("壳体")
    data = json.loads(json.dumps(current.data, ensure_ascii=False))
    row = next(item for item in data["domain_classes"] if item["id"] == "Shell")
    row["aliases"] = ["壳体"]
    row["label"] = "设备壳体"
    with patch.object(Path, "read_text", return_value=json.dumps(data, ensure_ascii=False)):
        revised = OntologyRegistry(Path("virtual-registry.json"))
    assert revised.version != current.version
    assert revised.domain_class("壳体")["id"] == original["id"]
    assert revised.canonical_label("壳体") == "设备壳体"


def test_unreviewed_terms_are_not_promoted_to_class_or_alias():
    registry = OntologyRegistry()
    assert registry.domain_class("尚未审定设备名称") is None
    assert registry.canonical_label("尚未审定设备名称") == "尚未审定设备名称"
    assert registry.expand("尚未审定术语") == []
    assert "concepts" not in registry.data


def test_entity_resolution_is_exact_or_unique_compound_only():
    registry = OntologyRegistry()
    assert registry.resolve_entity_class("凝汽器")["class_id"] == "Condenser"
    assert registry.resolve_entity_class("凝汽器壳体")["class_id"] == "Shell"
    assert registry.resolve_entity_class("凝汽器壳体", upper_class="Component")["class_id"] == "Shell"
    assert registry.resolve_entity_class("凝汽器壳体", upper_class="Activity") is None
    assert registry.resolve_entity_class("灌水试验")["class_id"] == "TestActivity"
    assert registry.resolve_entity_class("垫铁安装")["class_id"] == "Installation"
    assert registry.resolve_entity_class("尚未审定设备名称") is None


def test_strict_class_hierarchy_is_distinct_from_storage_types():
    registry = OntologyRegistry()
    ns = Namespace(registry.data["namespace"])
    core, shapes = registry.authority()
    assert (ns["Condenser"], RDFS.subClassOf, ns["HeatExchanger"]) in core
    assert (ns["WaterTank"], RDFS.subClassOf, ns["Tank"]) in core
    assert (ns["Shell"], RDFS.subClassOf, ns["Component"]) in core
    assert (ns["Shell"], RDFS.subClassOf, ns["Condenser"]) not in core
    assert (ns["PhysicalObject"], RDFS.subClassOf, OWL.Thing) in core
    for key in ("Statement", "Evidence", "Mention", "OntologyClass"):
        assert key not in registry.business_classes
        assert not list(core.triples((ns[key], RDFS.subClassOf, None)))
        assert (ns[key], RDF.type, RDFS.Class) in shapes
    assert registry.domain_class_by_id("Equipment")["parent_class_id"] == "PhysicalObject"
    assert registry.domain_class_by_id("OntologyClass") is None
    registry.validate_relation("has_requirement", "OntologyClass", "Statement",
                               role="subject", statement_type="requirement")
    with pytest.raises(ValueError):
        registry.validate_relation("has_requirement", "OntologyClass", "Statement",
                                   role="related", statement_type="requirement")
    with pytest.raises(ValueError):
        registry.validate_entity({"label": "壳体", "class": "OntologyClass", "role": "subject"})
    with pytest.raises(ValueError):
        registry.validate_semantic_relation({"relation_id": "subclass_of", "source_entity": 0,
                                             "target_entity": 1}, [
            {"label": "壳体", "class": "Mention", "role": "subject"},
            {"label": "凝汽器", "class": "Mention", "role": "object"}])


def test_multiple_parent_taxonomy_survives_contract_and_rejects_secondary_cycles():
    registry=OntologyRegistry(); ns=Namespace(registry.data['namespace'])
    core,_=registry.authority()
    for key in ('HighPressureHeater','LowPressureHeater'):
        assert registry.domain_is_a(key,'Equipment')
        assert (ns[key],RDFS.subClassOf,ns['Heater']) in core
        assert (ns[key],RDFS.subClassOf,ns['HeatExchanger']) in core
    assert (ns['Heater'],RDFS.subClassOf,ns['HeatExchanger']) not in core
    assert (ns['Condenser'],RDFS.subClassOf,ns['HeatExchanger']) in core
    data=json.loads(json.dumps(registry.data,ensure_ascii=False))
    next(row for row in data['domain_classes'] if row['id']=='HeatExchanger')['additional_parents']=['HighPressureHeater']
    with patch.object(Path,'read_text',return_value=json.dumps(data,ensure_ascii=False)):
        with pytest.raises(ValueError,match='有环'):OntologyRegistry(Path('virtual-registry.json'))


def test_engineering_definitions_notes_and_sources_have_distinct_rdf_annotations():
    registry=OntologyRegistry(); ns=Namespace(registry.data['namespace']); core,_=registry.authority()
    row=registry.domain_classes['WaterJetPump']
    assert (ns['WaterJetPump'],SKOS.definition,Literal(row['definition'],lang='zh-CN')) in core
    assert (ns['WaterJetPump'],SKOS.scopeNote,Literal(row['scope_note'],lang='zh-CN')) in core
    source=registry.domain_classes['WaterJetEjector']['definition_sources'][0]
    assert (ns['WaterJetEjector'],RDFS.seeAlso,URIRef(source)) in core
    assert all('第五章' not in row['definition'] for row in registry.domain_classes.values())


def test_only_current_registry_snapshot_is_available():
    old_version = "2af4d5480a10397f7610b978c43bc10d3857a40b8b56a566da991855ce4ede04"
    with pytest.raises(ValueError):
        OntologyRegistry.for_version(old_version)


def test_mixed_active_versions_need_separate_contracts():
    class ActiveStore:
        def query(self, *_args, **_kwargs):
            return [{"id": "old", "ontology": "old"}, {"id": "new", "ontology": "new"}]

    with pytest.raises(ValueError, match="多个本体版本"):
        KnowledgeStore.snapshot(ActiveStore(), OntologyRegistry().version, (), Budget())
