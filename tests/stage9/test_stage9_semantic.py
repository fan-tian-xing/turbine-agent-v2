"""Exercise the Stage 9 authority package through its actual research consumer."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from rdflib import Dataset

from turbine_kg.ontology.research_adapter import research_payload, validate_research_documents
from turbine_kg.ontology.semantic import SemanticValidationError, json_to_rdf, validate_runtime_payload
from turbine_kg.stage3.corpus import load_corpus
from turbine_kg.stage3.projection import build_traceability_projection
from turbine_kg.stage3.real_trial import load_confirmed_real_corpus


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/stage3/corpus.json"
CONFIRMATION = ROOT / "data/stage3/real_trial_confirmation.json"
REAL_RUNTIME = ROOT / "var/stage3/real_trial_pages.json"
if not REAL_RUNTIME.is_file():
    REAL_RUNTIME = ROOT / "tests/fixtures/stage3/real_trial_pages.json"


@pytest.fixture
def documents():
    return load_corpus(FIXTURE)


@pytest.fixture
def payload(documents):
    return research_payload(documents)


def _node(payload, kind):
    return next(node for node in payload["nodes"] if node["type"] == kind)


def _fails(payload):
    report = validate_runtime_payload(payload)
    assert report["conforms"] is False
    assert report["failures"]
    assert all(set(failure) >= {"stage", "message"} for failure in report["failures"])
    return report


def test_public_corpus_is_a_real_dataset_and_reports_reconciled_counts(documents, payload):
    dataset = json_to_rdf(payload)
    assert isinstance(dataset, Dataset)
    report = validate_research_documents(documents)
    assert report["conforms"] is True, report
    assert report["failures"] == []
    assert report["dataset_triple_count"] > len(payload["nodes"])
    assert report["counts"]["nodes"] == len(payload["nodes"])
    assert report["counts"]["relations"] == len(payload["relations"])
    assert report["counts"]["statements"] == sum(len(d.statements) for d in documents)
    assert report["counts"]["evidence"] == sum(len(d.evidence) for d in documents)
    assert report["counts"]["source_spans"] == sum(len(d.spans) for d in documents)
    projection = build_traceability_projection(documents)
    assert sum(n["type"] == "EngineeringStatement" for n in projection["nodes"]) == report["counts"]["statements"]


@pytest.mark.parametrize("location", ["payload", "node", "relation"])
def test_unknown_runtime_schema_fields_are_rejected(payload, location):
    target = payload if location == "payload" else payload["nodes"][0] if location == "node" else payload["relations"][0]
    target["uncontracted"] = "ignored information must not disappear"
    _fails(payload)


@pytest.mark.parametrize("change", ["class", "datatype_property", "object_property", "iri"])
def test_owl_vocabulary_and_iri_validation_reject_unknown_input(payload, change):
    if change == "class":
        payload["nodes"][0]["type"] = "UnregisteredClass"
    elif change == "datatype_property":
        payload["nodes"][0]["properties"]["unregisteredProperty"] = "unexpected"
    elif change == "object_property":
        payload["relations"][0]["predicate"] = "unregisteredRelation"
    else:
        payload["nodes"][0]["id"] = "not an iri"
    _fails(payload)


@pytest.mark.parametrize("kind,property_name", [("EngineeringStatement", "statementText"), ("Evidence", "evidenceText"), ("SourceSpan", "physicalPage")])
def test_required_statement_evidence_and_locator_fields_are_enforced(payload, kind, property_name):
    _node(payload, kind)["properties"].pop(property_name)
    _fails(payload)


@pytest.mark.parametrize("value", [0, -1, "one", True])
def test_physical_pages_must_be_positive_integers(payload, value):
    _node(payload, "SourceSpan")["properties"]["physicalPage"] = value
    _fails(payload)


@pytest.mark.parametrize("statement_type", ["fact", "conditioned_inference", "candidate_recommendation", "action_authorization"])
def test_all_existing_statement_types_are_supported(payload, statement_type):
    _node(payload, "EngineeringStatement")["properties"]["statementType"] = statement_type
    report = validate_runtime_payload(payload)
    assert report["conforms"] is True, report


def test_unknown_statement_type_is_rejected(payload):
    _node(payload, "EngineeringStatement")["properties"]["statementType"] = "invented"
    _fails(payload)


@pytest.mark.parametrize("predicate", ["supportedBy", "sourceSpan", "hasApplicabilityScope"])
def test_semantic_relations_require_their_correct_endpoint_class(payload, predicate):
    relation = next(link for link in payload["relations"] if link["predicate"] == predicate)
    relation["target"] = _node(payload, "QuantityValue")["id"]
    _fails(payload)


def test_owl_relationship_domain_is_checked_as_well_as_range(payload):
    relation = next(link for link in payload["relations"] if link["predicate"] == "supportedBy")
    relation["source"] = _node(payload, "QuantityValue")["id"]
    _fails(payload)


def test_missing_reference_and_duplicate_id_cannot_be_lost_during_conversion(payload):
    dangling = deepcopy(payload)
    dangling["relations"][0]["target"] = "https://example.invalid/nonexistent"
    _fails(dangling)
    duplicated = deepcopy(payload)
    duplicated["nodes"].append(deepcopy(duplicated["nodes"][0]))
    _fails(duplicated)


def test_dataset_conversion_is_independent_of_transport_row_order(payload):
    reordered = deepcopy(payload)
    reordered["nodes"].reverse()
    reordered["relations"].reverse()
    assert set(json_to_rdf(payload).quads((None, None, None, None))) == set(json_to_rdf(reordered).quads((None, None, None, None)))


def test_logical_and_physical_pages_are_both_preserved_from_research_input(documents):
    original = documents[0]
    page = replace(original.pages[0], logical_page="A-1")
    changed = (replace(original, pages=(page, *original.pages[1:])), *documents[1:])
    nodes = research_payload(changed)["nodes"]
    span = next(node for node in nodes if node["type"] == "SourceSpan" and node["properties"].get("pageId") == page.page_id)
    assert span["properties"]["physicalPage"] == page.page_number
    assert span["properties"]["logicalPage"] == "A-1"
    assert validate_research_documents(changed)["conforms"] is True


@pytest.mark.parametrize("change", ["no_unit", "invalid_unit", "not_numeric", "multiple_values"])
def test_quantity_value_and_unit_contract_is_enforced(payload, change):
    properties = _node(payload, "QuantityValue")["properties"]
    if change == "no_unit":
        properties.pop("unitSymbol")
    elif change == "invalid_unit":
        properties["unitSymbol"] = "banana"
    elif change == "not_numeric":
        properties["numericValue"] = "not-a-number"
    else:
        properties["numericValue"] = [1, 2]
    _fails(payload)


def test_quantity_kind_and_optional_scope_do_not_reject_valid_research_input(payload):
    _node(payload, "QuantityValue")["properties"].pop("quantityKindLabel", None)
    for node in payload["nodes"]:
        if node["type"] == "ApplicabilityScope":
            node["properties"] = {}
    assert validate_runtime_payload(payload)["conforms"] is True


def test_quantity_and_capacity_ranges_are_validated(payload):
    quantity = _node(payload, "QuantityValue")["properties"]
    quantity.pop("numericValue")
    quantity.update(minimumValue=1, maximumValue=2)
    assert validate_runtime_payload(payload)["conforms"] is True
    quantity["minimumValue"] = 3
    _fails(payload)
    capacity = deepcopy(research_payload(load_corpus(FIXTURE)))
    _node(capacity, "ApplicabilityScope")["properties"].update(capacityMinimum=400, capacityMaximum=300)
    _fails(capacity)


@pytest.mark.parametrize("endpoint", ["minimumValue", "maximumValue"])
def test_one_sided_quantity_interval_is_supported(payload, endpoint):
    quantity = _node(payload, "QuantityValue")["properties"]
    quantity.pop("numericValue")
    quantity[endpoint] = 1.5
    assert validate_runtime_payload(payload)["conforms"] is True


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True])
def test_nonfinite_and_boolean_numbers_are_rejected(payload, value):
    _node(payload, "QuantityValue")["properties"]["numericValue"] = value
    _fails(payload)


@pytest.mark.parametrize("property_name", ["unitSymbol", "quantityKindLabel"])
def test_present_quantity_labels_cannot_be_empty(payload, property_name):
    _node(payload, "QuantityValue")["properties"][property_name] = ""
    _fails(payload)


def test_evidence_and_statement_must_bind_to_the_same_object(documents, payload):
    evidence = _node(payload, "Evidence")
    relation = next(link for link in payload["relations"] if link["source"] == evidence["id"] and link["predicate"] == "evidenceAboutEntity")
    alternative = next(node["id"] for node in payload["nodes"] if node["type"] == "PhysicalEntity" and node["id"] != relation["target"])
    relation["target"] = alternative
    _fails(payload)
    document = documents[0]
    changed = replace(document.evidence[0], object_id="auxiliary_pump")
    invalid = (replace(document, evidence=(changed, *document.evidence[1:])), *documents[1:])
    with pytest.raises(SemanticValidationError):
        build_traceability_projection(invalid)


def test_confirmed_real_research_sample_consumes_the_same_gate_and_preserves_locations():
    documents = load_confirmed_real_corpus(CONFIRMATION, REAL_RUNTIME)
    payload = research_payload(documents)
    report = validate_research_documents(documents)
    assert report["conforms"] is True, report
    assert report["counts"]["statements"] == len({s.statement_id for d in documents for s in d.statements})
    assert report["counts"]["evidence"] == len({e.evidence_id for d in documents for e in d.evidence})
    assert report["counts"]["source_spans"] == len({s.span_id for d in documents for s in d.spans})
    locators = {node["properties"]["pageId"]: node["properties"] for node in payload["nodes"] if node["type"] == "SourceSpan"}
    for document in documents:
        for page in document.pages:
            properties = locators[page.page_id]
            assert properties["physicalPage"] == page.page_number
            assert properties.get("logicalPage") == page.logical_page
            assert properties["revisionId"] == page.revision_id
            assert properties["documentId"] == document.logical_document.document_logical_id
    projection = build_traceability_projection(documents)
    assert sum(node["type"] == "EngineeringStatement" for node in projection["nodes"]) == report["counts"]["statements"]
    original = documents[0]
    broken = (replace(original, evidence=()), *documents[1:])
    assert validate_research_documents(broken)["conforms"] is False
    with pytest.raises(SemanticValidationError):
        build_traceability_projection(broken)


@pytest.mark.parametrize("change", ["revision_document", "fabricated_span"])
def test_source_identity_and_page_quote_are_checked_before_conversion(documents, change):
    document = documents[0]
    if change == "revision_document":
        document = replace(document, revision=replace(document.revision, document_logical_id="doc-unrelated"))
    else:
        document = replace(document, spans=(replace(document.spans[0], quote="Fabricated source text absent from the Page."), *document.spans[1:]))
    invalid = (document, *documents[1:])
    report = validate_research_documents(invalid)
    assert report["conforms"] is False
    assert report["failures"][0]["stage"] == "research_adapter"
    with pytest.raises(SemanticValidationError):
        build_traceability_projection(invalid)


@pytest.mark.parametrize("change", ["fabricated_evidence", "assembled_spans"])
def test_real_evidence_must_be_a_quote_in_one_bound_source_span(change):
    documents = load_confirmed_real_corpus(CONFIRMATION, REAL_RUNTIME)
    document = documents[0]
    evidence = document.evidence[0]
    if change == "fabricated_evidence":
        document = replace(document, evidence=(replace(evidence, text="Fabricated reviewed quote."), *document.evidence[1:]))
    else:
        # Both halves occur on the original Page. Their concatenation must not
        # be accepted as a quote existing in either single bound SourceSpan.
        original_span = next(span for span in document.spans if span.span_id == evidence.source_span_ids[0])
        midpoint = len(evidence.text) // 2
        first = replace(original_span, quote=evidence.text[:midpoint])
        second = replace(original_span, span_id=original_span.span_id + "-second", quote=evidence.text[midpoint:])
        evidence = replace(evidence, source_span_ids=(first.span_id, second.span_id))
        statement = next(s for s in document.statements if s.statement_id == evidence.statement_id)
        document = replace(document, spans=(first, second), evidence=(evidence,), statements=(statement,))
    invalid = (document, *documents[1:])
    report = validate_research_documents(invalid)
    assert report["conforms"] is False
    assert report["failures"][0]["stage"] == "research_adapter"
    assert "SourceSpan" in report["failures"][0]["message"]
    with pytest.raises(SemanticValidationError):
        build_traceability_projection(invalid)


@pytest.mark.parametrize("change", ["missing_evidence", "missing_span", "missing_page", "invalid_type", "invalid_unit", "widened_scope"])
def test_actual_projection_entry_blocks_invalid_research_batch(documents, change):
    document = documents[0]
    statement = document.statements[0]
    if change == "missing_evidence":
        document = replace(document, evidence=document.evidence[1:])
    elif change == "missing_span":
        document = replace(document, spans=document.spans[1:])
    elif change == "missing_page":
        document = replace(document, pages=document.pages[1:])
    elif change == "invalid_type":
        document = replace(document, statements=(replace(statement, statement_type="invented"), *document.statements[1:]))
    elif change == "invalid_unit":
        document = replace(document, statements=(replace(statement, unit="banana"), *document.statements[1:]))
    else:
        scope = replace(statement.scope, model="UNRELATED-MODEL")
        document = replace(document, statements=(replace(statement, scope=scope), *document.statements[1:]))
    invalid_documents = (document, *documents[1:])
    report = validate_research_documents(invalid_documents)
    assert report["conforms"] is False
    with pytest.raises(SemanticValidationError) as error:
        build_traceability_projection(invalid_documents)
    assert error.value.report["conforms"] is False
