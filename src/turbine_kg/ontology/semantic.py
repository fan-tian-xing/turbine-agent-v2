"""Stage 9 semantic authority consumed before the research graph projection.

JSON Schema owns transport structure. OWL owns classes/properties/endpoints;
SHACL owns cardinalities, controlled values and cross-property constraints.
Generated endpoint shapes and vocabulary catalogs exist only in memory and
are consumed by validation in the same call. No raw-source access occurs here.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from pyshacl import validate
from rdflib import BNode, Dataset, Graph, Literal, Namespace, OWL, RDF, RDFS, URIRef, XSD

ROOT = Path(__file__).resolve().parents[3]
TV2 = Namespace("https://example.invalid/turbine-v2#")
SH = Namespace("http://www.w3.org/ns/shacl#")
SCHEMA_PATH = ROOT / "config/semantic_runtime.schema.json"
ONTOLOGY_PATH = ROOT / "ontology/stage9_core.ttl"
SHAPES_PATH = ROOT / "ontology/stage9_shapes.ttl"


class SemanticValidationError(ValueError):
    """A failed report blocks the entire batch before projection or DB writes."""

    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__("semantic validation failed: " + "; ".join(
            item["message"] for item in report["failures"]
        ))


def failure_report(stage: str, message: str) -> dict[str, Any]:
    return {
        "conforms": False, "failures": [{"stage": stage, "message": message}],
        "warnings": [], "counts": {}, "dataset_triple_count": 0, "report_text": message,
    }


def _load_ontology(ontology_path: Path | None = None) -> Graph:
    ontology_path = ontology_path or ONTOLOGY_PATH
    ontology = Graph().parse(ontology_path, format="turtle")
    # Resolve the single Stage 8 import locally; never follow remote owl:imports.
    if (None, OWL.imports, URIRef(str(TV2))) in ontology:
        ontology.parse(ontology_path.parent / "minimal_turbine.ttl", format="turtle")
    return ontology


def load_authority(ontology_path: Path | None = None, shapes_path: Path | None = None) -> tuple[Graph, Graph]:
    return _load_ontology(ontology_path), Graph().parse(shapes_path or SHAPES_PATH, format="turtle")


def vocabulary_catalog(ontology: Graph) -> dict[str, Any]:
    """Derive only the catalogs actually consumed by conversion/validation."""
    classes = {str(iri).removeprefix(str(TV2)): iri for iri in ontology.subjects(RDF.type, OWL.Class)}
    object_properties = {}
    datatype_properties = {}
    for declaration, destination in ((OWL.ObjectProperty, object_properties), (OWL.DatatypeProperty, datatype_properties)):
        for iri in ontology.subjects(RDF.type, declaration):
            domains = list(ontology.objects(iri, RDFS.domain))
            ranges = list(ontology.objects(iri, RDFS.range))
            if len(domains) != 1 or len(ranges) != 1:
                raise ValueError(f"OWL property requires one declared domain/range: {iri}")
            destination[str(iri).removeprefix(str(TV2))] = {
                "iri": iri, "domain": domains[0], "range": ranges[0],
            }
    return {"classes": classes, "object_properties": object_properties, "datatype_properties": datatype_properties}


def _schema_check(payload: dict[str, Any]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda error: str(error.path))
    if errors:
        raise SemanticValidationError(failure_report("runtime_schema", "; ".join(
            f"{list(error.path)}: {error.message}" for error in errors
        )))
    for node in payload["nodes"]:
        for value in node["properties"].values():
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, float) and not math.isfinite(item):
                    raise SemanticValidationError(failure_report("runtime_schema", "numbers must be finite"))
    ids = [node["id"] for node in payload["nodes"]]
    if len(ids) != len(set(ids)):
        raise SemanticValidationError(failure_report("runtime_schema", "duplicate runtime node ID"))


def _dataset(payload: dict[str, Any], ontology: Graph, catalog: dict[str, Any]) -> Dataset:
    dataset = Dataset()
    graph = dataset.graph(URIRef("urn:turbine-v2:stage9:runtime"))
    for node in payload["nodes"]:
        if node["type"] not in catalog["classes"]:
            raise SemanticValidationError(failure_report("owl_vocabulary", f"unknown class: {node['type']}"))
        subject = URIRef(node["id"])
        node_class = catalog["classes"][node["type"]]
        graph.add((subject, RDF.type, node_class))
        # Materialize declared class ancestors only. Never infer endpoint types
        # from domain/range assertions, which could make dangling links valid.
        for ancestor in ontology.transitive_objects(node_class, RDFS.subClassOf):
            graph.add((subject, RDF.type, ancestor))
        for name, values in node["properties"].items():
            if name not in catalog["datatype_properties"]:
                raise SemanticValidationError(failure_report("owl_vocabulary", f"unknown datatype property: {name}"))
            spec = catalog["datatype_properties"][name]
            for value in values if isinstance(values, list) else [values]:
                datatype = None
                if isinstance(value, (int, float)) and spec["range"] == XSD.decimal:
                    datatype = XSD.decimal
                    value = Decimal(str(value))
                graph.add((subject, spec["iri"], Literal(value, datatype=datatype)))
    for relation in payload["relations"]:
        name = relation["predicate"]
        if name not in catalog["object_properties"]:
            raise SemanticValidationError(failure_report("owl_vocabulary", f"unknown object property: {name}"))
        graph.add((URIRef(relation["source"]), catalog["object_properties"][name]["iri"], URIRef(relation["target"])))
    return dataset


def json_to_rdf(payload: dict[str, Any], ontology_path: Path | None = None) -> Dataset:
    _schema_check(payload)
    ontology = _load_ontology(ontology_path)
    return _dataset(payload, ontology, vocabulary_catalog(ontology))


def _endpoint_shapes(shapes: Graph, catalog: dict[str, Any]) -> None:
    for kind in ("object_properties", "datatype_properties"):
        for spec in catalog[kind].values():
            shape, prop = BNode(), BNode()
            shapes.add((shape, RDF.type, SH.NodeShape))
            shapes.add((shape, SH.targetSubjectsOf, spec["iri"]))
            shapes.add((shape, SH["class"], spec["domain"]))
            shapes.add((shape, SH.property, prop))
            shapes.add((prop, SH.path, spec["iri"]))
            shapes.add((prop, SH.nodeKind, SH.IRI if kind == "object_properties" else SH.Literal))
            shapes.add((prop, SH["class"] if kind == "object_properties" else SH.datatype, spec["range"]))


def validate_runtime_payload(
    payload: dict[str, Any], shapes_path: Path | None = None, ontology_path: Path | None = None,
) -> dict[str, Any]:
    try:
        _schema_check(payload)
        ontology, shapes = load_authority(ontology_path, shapes_path)
        catalog = vocabulary_catalog(ontology)
        dataset = _dataset(payload, ontology, catalog)
        _endpoint_shapes(shapes, catalog)
        conforms, report_graph, report_text = validate(
            dataset, shacl_graph=shapes, ont_graph=ontology,
            inference="none", abort_on_first=False, do_owl_imports=False,
        )
    except SemanticValidationError as error:
        return error.report
    except (ValueError, TypeError) as error:
        return failure_report("authority_or_conversion", str(error))
    failures = []
    for result in report_graph.subjects(RDF.type, SH.ValidationResult):
        failures.append({
            "stage": "shacl",
            "message": str(report_graph.value(result, SH.resultMessage)),
            "focus_node": str(report_graph.value(result, SH.focusNode)),
            "path": str(report_graph.value(result, SH.resultPath) or ""),
            "severity": str(report_graph.value(result, SH.resultSeverity)),
        })
    warnings = []
    for node in payload["nodes"]:
        if node["type"] == "ApplicabilityScope":
            missing = sorted({"model", "equipment", "lifecycleStage", "activity", "operatingState", "condition"} - node["properties"].keys())
            if missing:
                warnings.append({"stage": "applicability", "node": node["id"], "missing_fields": missing,
                                 "message": "未记录的范围字段保持未知，不代表适用性匹配成功。"})
    counts = {
        "nodes": len(payload["nodes"]), "relations": len(payload["relations"]),
        **{name: sum(node["type"] == class_name for node in payload["nodes"]) for name, class_name in (
            ("statements", "EngineeringStatement"), ("evidence", "Evidence"),
            ("source_spans", "SourceSpan"), ("quantities", "QuantityValue"),
        )},
    }
    return {
        "conforms": bool(conforms), "failures": failures, "warnings": warnings,
        "counts": counts, "report_text": report_text,
        "dataset_triple_count": sum(1 for _ in dataset.quads((None, None, None, None))),
    }
