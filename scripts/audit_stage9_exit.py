"""Audit the Stage 9 authority package and its actual research projection consumer."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from copy import deepcopy
from dataclasses import replace
from importlib.metadata import version

ROOT = Path(__file__).resolve().parents[1]
INPUTS = (
    "data/stage8/stage8_exit_audit.json", "ontology/minimal_turbine.ttl",
    "ontology/stage9_core.ttl", "ontology/stage9_shapes.ttl",
    "src/turbine_kg/ontology/semantic.py", "src/turbine_kg/ontology/research_adapter.py",
    "src/turbine_kg/stage3/projection.py", "src/turbine_kg/stage3/corpus.py",
    "src/turbine_kg/stage3/models.py", "pyproject.toml", "uv.lock",
    "src/turbine_kg/stage3/real_trial.py", "data/stage3/real_trial_confirmation.json",
    "data/registry/source_assets.jsonl",
    "config/semantic_runtime.schema.json",
    "tests/fixtures/stage3/corpus.json", "tests/stage9/test_stage9_semantic.py",
    "scripts/audit_stage9_exit.py",
)
REVIEWED_INPUTS = (
    "ontology/minimal_turbine.ttl", "ontology/stage9_core.ttl", "ontology/stage9_shapes.ttl",
    "src/turbine_kg/ontology/semantic.py", "src/turbine_kg/ontology/research_adapter.py",
    "src/turbine_kg/stage3/projection.py",
    "config/semantic_runtime.schema.json",
)


def _sha(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def _read(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _audit(*, run_tests: bool = True) -> dict:
    sys.path.insert(0, str(ROOT / "src"))
    from rdflib import Dataset, Graph, OWL, RDF, RDFS, URIRef
    from turbine_kg.ontology.research_adapter import research_payload, validate_research_documents
    from turbine_kg.ontology.semantic import SemanticValidationError, json_to_rdf, load_authority, validate_runtime_payload
    from turbine_kg.stage3.corpus import load_corpus
    from turbine_kg.stage3.projection import build_traceability_projection
    from turbine_kg.stage3.real_trial import load_confirmed_real_corpus

    corpus = load_corpus(ROOT / "tests/fixtures/stage3/corpus.json")
    payload = research_payload(corpus)
    dataset = json_to_rdf(payload)
    report = validate_research_documents(corpus)
    projection = build_traceability_projection(corpus)
    real_relative = "var/stage3/real_trial_pages.json"
    if not (ROOT / real_relative).is_file():
        real_relative = "tests/fixtures/stage3/real_trial_pages.json"
    real_documents = load_confirmed_real_corpus(ROOT / "data/stage3/real_trial_confirmation.json", ROOT / real_relative)
    real_payload = research_payload(real_documents)
    real_report = validate_research_documents(real_documents)
    real_projection = build_traceability_projection(real_documents)
    ontology, _ = load_authority()
    core = Graph().parse(ROOT / "ontology/stage9_core.ttl", format="turtle")
    base_ontology = Graph().parse(ROOT / "ontology/minimal_turbine.ttl", format="turtle")
    term_types = (OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty)
    terms = {term for kind in term_types for term in ontology.subjects(RDF.type, kind)}
    chinese_semantics_present = all(
        any((label.language or "").lower().split("-")[0] == "zh" and str(label).strip() for label in ontology.objects(term, RDFS.label))
        and any((comment.language or "").lower().split("-")[0] == "zh" and str(comment).strip() for comment in ontology.objects(term, RDFS.comment))
        for term in terms
    )
    property_terms = {term for kind in (OWL.ObjectProperty, OWL.DatatypeProperty) for term in ontology.subjects(RDF.type, kind)}
    base_terms = {term for kind in term_types for term in base_ontology.subjects(RDF.type, kind)}
    core_terms = {term for kind in term_types for term in core.subjects(RDF.type, kind)}
    local_imports_only = set(core.objects(None, OWL.imports)) == {URIRef("https://example.invalid/turbine-v2#")} and not list(base_ontology.objects(None, OWL.imports))
    single_property_domains_and_ranges = all(
        len(list(ontology.objects(term, RDFS.domain))) == 1
        and len(list(ontology.objects(term, RDFS.range))) == 1 for term in property_terms
    )
    real_counts = {
        "document_groups": len(real_documents),
        "logical_documents": len({d.logical_document.document_logical_id for d in real_documents}),
        "pages": len({page.page_id for d in real_documents for page in d.pages}),
        "source_spans": len({span.span_id for d in real_documents for span in d.spans}),
        "evidence": len({e.evidence_id for d in real_documents for e in d.evidence}),
        "statements": len({s.statement_id for d in real_documents for s in d.statements}),
        "quantities": sum(n["type"] == "QuantityValue" for n in real_payload["nodes"]),
        "runtime_nodes": len(real_payload["nodes"]), "runtime_relations": len(real_payload["relations"]),
        "dataset_triples": real_report["dataset_triple_count"],
        "projection_nodes": len(real_projection["nodes"]), "projection_edges": len(real_projection["edges"]),
        "pages_with_logical_page": len({page.page_id for d in real_documents for page in d.pages if page.logical_page is not None}),
        "warning_count": len(real_report["warnings"]),
    }
    real_locators = {node["properties"]["pageId"]: node["properties"] for node in real_payload["nodes"] if node["type"] == "SourceSpan"}
    real_locations_match = all(
        real_locators[page.page_id]["physicalPage"] == page.page_number
        and real_locators[page.page_id].get("logicalPage") == page.logical_page
        and real_locators[page.page_id]["revisionId"] == page.revision_id
        and real_locators[page.page_id]["documentId"] == document.logical_document.document_logical_id
        for document in real_documents for page in document.pages
    )
    confirmation = _read("data/stage3/real_trial_confirmation.json")
    real_broken = (replace(real_documents[0], evidence=()), *real_documents[1:])
    real_projection_blocked = False
    try:
        build_traceability_projection(real_broken)
    except SemanticValidationError as error:
        real_projection_blocked = not error.report["conforms"] and bool(error.report["failures"])
    counts = {
        "documents": len(corpus), "pages": sum(len(d.pages) for d in corpus),
        "source_spans": sum(len(d.spans) for d in corpus),
        "evidence": sum(len(d.evidence) for d in corpus),
        "statements": sum(len(d.statements) for d in corpus),
        "quantities": sum(n["type"] == "QuantityValue" for n in payload["nodes"]),
        "runtime_nodes": len(payload["nodes"]), "runtime_relations": len(payload["relations"]),
        "dataset_triples": report["dataset_triple_count"],
        "projection_nodes": len(projection["nodes"]), "projection_edges": len(projection["edges"]),
        "warning_count": len(report["warnings"]),
    }
    optional_scope_payload = deepcopy(payload)
    for node in optional_scope_payload["nodes"]:
        if node["type"] == "ApplicabilityScope":
            node["properties"] = {}
    optional_scope_report = validate_runtime_payload(optional_scope_payload)
    optional_scope_node_ids = {node["id"] for node in optional_scope_payload["nodes"] if node["type"] == "ApplicabilityScope"}
    negatives = {}
    for label in ("unknown_schema_field", "unknown_class", "unknown_predicate", "invalid_statement_type", "wrong_evidence_endpoint", "invalid_unit", "missing_physical_page"):
        bad = deepcopy(payload)
        def node(kind):
            return next(n for n in bad["nodes"] if n["type"] == kind)
        if label == "unknown_schema_field": bad["uncontracted"] = True
        elif label == "unknown_class": bad["nodes"][0]["type"] = "UnregisteredClass"
        elif label == "unknown_predicate": bad["relations"][0]["predicate"] = "unregisteredRelation"
        elif label == "invalid_statement_type": node("EngineeringStatement")["properties"]["statementType"] = "invented"
        elif label == "wrong_evidence_endpoint":
            next(link for link in bad["relations"] if link["predicate"] == "supportedBy")["target"] = node("QuantityValue")["id"]
        elif label == "invalid_unit": node("QuantityValue")["properties"]["unitSymbol"] = "banana"
        else: node("SourceSpan")["properties"].pop("physicalPage")
        negative_report = validate_runtime_payload(bad)
        negatives[label] = not negative_report["conforms"] and bool(negative_report["failures"])
    broken_document = replace(corpus[0], evidence=corpus[0].evidence[1:])
    broken = (broken_document, *corpus[1:])
    broken_report = validate_research_documents(broken)
    blocked_projection = False
    try:
        build_traceability_projection(broken)
    except SemanticValidationError as error:
        blocked_projection = not error.report["conforms"] and bool(error.report["failures"])
    for label in ("revision_document_mismatch", "fabricated_page_span", "fabricated_reviewed_evidence", "assembled_reviewed_spans"):
        document = real_documents[0]
        evidence = document.evidence[0]
        if label == "revision_document_mismatch":
            document = replace(document, revision=replace(document.revision, document_logical_id="doc-unrelated"))
        elif label == "fabricated_page_span":
            document = replace(document, spans=(replace(document.spans[0], quote="Fabricated source text."), *document.spans[1:]))
        elif label == "fabricated_reviewed_evidence":
            document = replace(document, evidence=(replace(evidence, text="Fabricated reviewed quote."), *document.evidence[1:]))
        else:
            original_span = next(span for span in document.spans if span.span_id == evidence.source_span_ids[0])
            midpoint = len(evidence.text) // 2
            first = replace(original_span, quote=evidence.text[:midpoint])
            second = replace(original_span, span_id=original_span.span_id + "-second", quote=evidence.text[midpoint:])
            evidence = replace(evidence, source_span_ids=(first.span_id, second.span_id))
            statement = next(s for s in document.statements if s.statement_id == evidence.statement_id)
            document = replace(document, spans=(first, second), evidence=(evidence,), statements=(statement,))
        invalid = (document, *real_documents[1:])
        negative_report = validate_research_documents(invalid)
        rejected = False
        try:
            build_traceability_projection(invalid)
        except SemanticValidationError as error:
            rejected = not error.report["conforms"]
        negatives[label] = not negative_report["conforms"] and bool(negative_report["failures"]) and rejected
    source_audit = _read("data/stage8/stage8_exit_audit.json")
    stage8_inputs_current = all(
        _sha(value["path"]) == value["sha256"] for value in source_audit["inputs"].values()
    )
    review_path = ROOT / "data/stage9/stage9_semantic_review.json"
    review = _read("data/stage9/stage9_semantic_review.json") if review_path.exists() else {}
    review_matches = bool(review) and all(
        review.get("reviewed_inputs", {}).get(relative) == _sha(relative) for relative in REVIEWED_INPUTS
    )
    manual_review_ok = (
        review.get("status") == "passed" and review.get("scope") == "code_contract_runtime_chain"
        and bool(review.get("checks")) and all(review["checks"].values())
        and review.get("blockers") == [] and review_matches
    )
    project_python = ROOT.parent / "runtime-python/turbine-kg-env/Scripts/python.exe"
    locked = {package["name"]: package["version"] for package in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]}
    installed = {name: version(name) for name in ("rdflib", "pyshacl", "jsonschema")}
    checks = {
        "stage8_gate_and_input_fingerprints": source_audit["status"] == "complete" and source_audit["next_stage_allowed"] and stage8_inputs_current,
        "project_python_environment": Path(sys.executable).resolve() == project_python.resolve(),
        "semantic_dependencies_match_lock": all(locked.get(name) == installed[name] for name in installed),
        "actual_rdf_dataset": isinstance(dataset, Dataset),
        "owl_terms_have_chinese_labels_and_definitions": bool(terms) and chinese_semantics_present,
        "owl_local_stage8_import_only": local_imports_only,
        "owl_extension_has_no_duplicate_term_declarations": not (base_terms & core_terms),
        "owl_properties_have_single_domain_and_range": single_property_domains_and_ranges,
        "public_fixture_semantic_validation": report["conforms"] and report["failures"] == [],
        "optional_scope_is_allowed_and_warnings_are_consumed": optional_scope_report["conforms"] and bool(optional_scope_report["warnings"]) and {warning["node"] for warning in optional_scope_report["warnings"] if warning["stage"] == "applicability"} == optional_scope_node_ids,
        "runtime_dataset_counts_reconcile": all(report["counts"][key] == counts[key] for key in ("statements", "evidence", "source_spans", "quantities")) and report["counts"]["nodes"] == counts["runtime_nodes"] and report["counts"]["relations"] == counts["runtime_relations"] and report["dataset_triple_count"] == len(dataset),
        "projection_statement_counts_reconcile": sum(n["type"] == "EngineeringStatement" for n in projection["nodes"]) == counts["statements"],
        "projection_evidence_counts_reconcile": sum(n["type"] == "Evidence" for n in projection["nodes"]) == counts["evidence"],
        "confirmed_real_research_validation": real_report["conforms"] and real_report["failures"] == [],
        "confirmed_real_counts_reconcile": all(real_report["counts"][key] == real_counts[key] for key in ("statements", "evidence", "source_spans", "quantities")) and real_report["counts"]["nodes"] == real_counts["runtime_nodes"] and real_report["counts"]["relations"] == real_counts["runtime_relations"],
        "confirmed_real_projection_counts_reconcile": all(sum(n["type"] == kind for n in real_projection["nodes"]) == real_counts[key] for key, kind in (("statements", "EngineeringStatement"), ("evidence", "Evidence"))),
        "confirmed_real_page_dual_identifiers_preserved": real_locations_match,
        "confirmed_real_user_admission_is_preserved": confirmation.get("status") == "confirmed_by_user" and bool(confirmation.get("confirmed_groups")) and all(group.get("review_status") == "confirmed" and group.get("user_confirmation") is True for group in confirmation["confirmed_groups"]),
        "confirmed_real_invalid_batch_projection_blocked": real_projection_blocked,
        "negative_contract_tests": all(negatives.values()),
        "invalid_batch_preserved_and_projection_blocked": not broken_report["conforms"] and blocked_projection and len(broken[0].statements) == len(corpus[0].statements),
        "independent_code_contract_review": manual_review_ok,
    }
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/stage9", "tests/stage3", "tests/unit/test_project_state.py"]
    test_result = {"command": command, "project_python": sys.executable, "status": "not_run"}
    if run_tests:
        result = subprocess.run(command, cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True)
        checks["targeted_and_consumer_tests"] = result.returncode == 0
        test_result.update(status="passed" if result.returncode == 0 else "failed", returncode=result.returncode, stdout_tail=result.stdout[-4000:], stderr_tail=result.stderr[-2000:])
    else:
        checks["targeted_and_consumer_tests"] = False
    full_command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
    full_test_result = {"command": full_command, "project_python": sys.executable, "status": "not_run"}
    if run_tests and checks["targeted_and_consumer_tests"]:
        full_result = subprocess.run(full_command, cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True)
        checks["full_project_tests"] = full_result.returncode == 0
        full_test_result.update(status="passed" if full_result.returncode == 0 else "failed", returncode=full_result.returncode, stdout_tail=full_result.stdout[-5000:], stderr_tail=full_result.stderr[-2000:])
    else:
        checks["full_project_tests"] = False
    blockers = [key for key, passed in checks.items() if not passed]
    inputs = {relative: {"path": relative, "sha256": _sha(relative)} for relative in INPUTS}
    inputs[real_relative] = {"path": real_relative, "sha256": _sha(real_relative)}
    if review_path.exists():
        inputs["data/stage9/stage9_semantic_review.json"] = {"path": "data/stage9/stage9_semantic_review.json", "sha256": _sha("data/stage9/stage9_semantic_review.json")}
    return {
        "schema_version": 1, "stage": "9", "artifact_kind": "stage9_exit_audit",
        "status": "complete" if not blockers else "blocked", "formal_release": False,
        "producer": "scripts/audit_stage9_exit.py", "inputs": inputs,
        "input_scope": "Existing Stage 8 frozen ontology plus public synthetic corpus and already admitted, user-confirmed real research sample; research-only, not formal knowledge or Release. Only confirmed/runtime/Registry artifacts are read, no original materials, case data or blind-test inputs.",
        "versions": {"python": sys.version.split()[0], **installed, "runtime_schema": 1, "dependency_lock": "uv.lock"},
        "outputs": {"ontology_authority": ["ontology/minimal_turbine.ttl", "ontology/stage9_core.ttl"], "constraint_authority": "ontology/stage9_shapes.ttl", "runtime_schema_authority": "config/semantic_runtime.schema.json", "validation_report": "validate_runtime_payload / validate_research_documents return value consumed before stage3 projection", "review": "data/stage9/stage9_semantic_review.json", "exit_audit": "data/stage9/stage9_exit_audit.json"},
        "counts": {"public_synthetic": counts, "confirmed_real_research": real_counts}, "checks": checks, "negative_checks": negatives,
        "review": {"scope": "code_contract_runtime_chain", "status": review.get("status", "missing"), "fingerprints_current": review_matches, "knowledge_approval": False, "admitted_data_metadata": "Existing user-confirmed groups, runtime identity/quote binding and Registry applicability are validated by load_confirmed_real_corpus; no overlay or user confirmation is changed."},
        "test_result": {"targeted": test_result, "full_project": full_test_result}, "zero_tolerance_errors": blockers, "blockers": blockers,
        "failure_isolation": "Any schema, vocabulary, SHACL or research-adapter failure yields a nonconforming report and raises SemanticValidationError before Property Graph construction/Neo4j loading; retain the whole original batch unchanged.",
        "rollback": "Restore the previous verified OWL/SHACL/runtime authority package and rerun the same input batch; no bypass or database correction.",
        "next_stage_allowed": not blockers,
        "next_stage": "Stage 10 runtime provenance and reproducibility" if not blockers else "Stage 9 semantic chain completion",
        "next_stage_inputs": {"owl": ["ontology/minimal_turbine.ttl", "ontology/stage9_core.ttl"], "shacl": "ontology/stage9_shapes.ttl", "runtime_contract": "src/turbine_kg/ontology/semantic.py:validate_runtime_payload", "research_consumer": "src/turbine_kg/ontology/research_adapter.py:validate_research_documents", "report_fields": ["conforms", "failures", "warnings", "counts", "report_text", "dataset_triple_count"]},
    }


def main() -> int:
    try:
        audit = _audit()
    except Exception as error:
        audit = {
            "schema_version": 1, "stage": "9", "artifact_kind": "stage9_exit_audit",
            "status": "blocked", "formal_release": False,
            "producer": "scripts/audit_stage9_exit.py",
            "inputs": {relative: {"path": relative, "sha256": _sha(relative)} for relative in INPUTS if (ROOT / relative).is_file()},
            "checks": {"audit_completed": False}, "blockers": ["audit_execution_failed"],
            "zero_tolerance_errors": ["audit_execution_failed"],
            "failure": {"type": type(error).__name__, "message": str(error)},
            "failure_isolation": "No graph or database load is performed by this audit; keep all source inputs unchanged.",
            "rollback": "Restore the prior verified authority package and rerun the audit.",
            "next_stage_allowed": False, "next_stage": "Stage 9 semantic chain completion",
        }
    output = ROOT / "data/stage9/stage9_exit_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": audit["status"], "blockers": audit["blockers"], "next_stage_allowed": audit["next_stage_allowed"]}, ensure_ascii=False))
    return 0 if not audit["blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
