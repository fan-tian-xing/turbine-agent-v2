"""Ontology class navigation preserves kinds, provenance and source statements."""
import pytest
from jsonschema import ValidationError, validate
from rdflib import OWL

from turbine_kg.ontology.registry import OntologyRegistry


def test_class_navigation_reaches_thing_without_inventing_equipment_instances():
    registry = OntologyRegistry()
    current = "Condenser"
    path = []
    while current is not None:
        assert current not in path
        path.append(current)
        current = registry.domain_class_by_id(current)["parent_class_id"]
    assert path == ["Condenser", "HeatExchanger", "Equipment", "PhysicalObject", "Thing"]
    assert registry.domain_class_by_id("Thing")["class_mapping"]["rdf"] == str(OWL.Thing)
    assert registry.domain_is_a("Condenser", "HeatExchanger")
    assert registry.domain_is_a("Condenser", "Thing")
    assert not registry.domain_is_a("Shell", "Condenser")


def test_first_two_pages_have_reviewed_object_and_activity_anchors():
    registry = OntologyRegistry()
    assert registry.domain_class("压力容器")["class_id"] == "PressureVessel"
    assert registry.domain_class("严密性水压试验")["class_id"] == "HydrostaticTightnessTest"
    assert registry.domain_is_a("HydrostaticTightnessTest", "HydrostaticTest")
    assert registry.domain_is_a("HydrostaticTightnessTest", "Activity")
    assert registry.domain_is_a("Assembly", "Activity")
    assert registry.domain_is_a("TubeInsertion", "Activity")
    assert registry.domain_class("疑似新设备名称") is None


def test_statement_contract_accepts_source_sentence_without_separate_modality():
    registry = OntologyRegistry()
    item = {"type": "requirement", "text": "穿管受阻时不得强行穿入。",
            "entities": [{"class": "Mention", "role": "subject", "label": "凝汽器冷却管"}],
            "supports": [{"evidence_id": "e1", "member_keys": ["m1"]}]}
    validate(item, registry.extraction_schema())
    assert not {"conditions", "quantities", "formulas"} & set(registry.extraction_schema()["properties"])
    assert registry.data["storage_policy"]["standalone_modality_fields"] is False
    assert registry.data["storage_policy"]["unreviewed_terms"] == "statement_only"
    with pytest.raises(ValidationError):
        validate({**item, "supports": [{"evidence_id": "e1", "member_keys": []}]},
                 registry.extraction_schema())
    for field in ("text", "supports"):
        with pytest.raises(ValidationError):
            validate({key: value for key, value in item.items() if key != field},
                     registry.extraction_schema())
    with pytest.raises(ValidationError):
        validate({**item, "text": "  \n "}, registry.extraction_schema())


def test_skill_can_retrieve_non_navigable_types_from_evidence():
    registry = OntologyRegistry()
    registry.validate_skill({"relation_ids": [], "required_items": ["fact", "limitation"]})
    with pytest.raises(ValueError, match="导航关系"):
        registry.validate_skill({"relation_ids": [], "required_items": ["requirement"]})
    with pytest.raises(ValueError, match="未注册陈述类型"):
        registry.validate_skill({"relation_ids": [], "required_items": ["invented"]})


def test_runtime_ontology_is_not_restricted_to_a_book_or_chapter():
    registry = OntologyRegistry()
    source_fields = {"source_clause", "source_document_id", "source_physical_page", "parent_provenance"}
    assert all(not source_fields.intersection(row) for row in registry.domain_classes.values())
