"""Minimal traceability projection for comparison with the JSON baseline."""

from __future__ import annotations

import json
import re
from typing import Any

from .applicability import match_scope
from .models import ApplicabilityScope, FixtureDocument, ScopeContext


def build_traceability_projection(documents: tuple[FixtureDocument, ...]) -> dict[str, Any]:
    node_by_id: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    edge_keys: set[tuple[str, str, str]] = set()

    def add_node(node: dict[str, Any]) -> None:
        node_by_id.setdefault(node["id"], node)

    def add_edge(edge: dict[str, str]) -> None:
        key = (edge["from"], edge["to"], edge["type"])
        if key not in edge_keys:
            edge_keys.add(key)
            edges.append(edge)

    for document in documents:
        add_node({"id": document.asset.asset_id, "type": "Asset"})
        add_node({"id": document.logical_document.document_logical_id, "type": "LogicalDocument"})
        add_node({"id": document.revision.revision_id, "type": "Revision"})
        add_edge({"from": document.asset.asset_id, "to": document.logical_document.document_logical_id, "type": "IDENTIFIES"})
        add_edge({"from": document.logical_document.document_logical_id, "to": document.revision.revision_id, "type": "HAS_REVISION"})
        for page in document.pages:
            add_node({"id": page.page_id, "type": "Page"})
            add_edge({"from": document.revision.revision_id, "to": page.page_id, "type": "HAS_PAGE"})
        for span in document.spans:
            add_node({"id": span.span_id, "type": "SourceSpan"})
            add_edge({"from": span.page_id, "to": span.span_id, "type": "HAS_SPAN"})
        for evidence in document.evidence:
            add_node({"id": evidence.evidence_id, "type": "Evidence"})
            for span_id in evidence.source_span_ids:
                add_edge({"from": span_id, "to": evidence.evidence_id, "type": "SUPPORTS"})
        for statement in document.statements:
            add_node({
                "id": statement.statement_id,
                "type": "EngineeringStatement",
                "text": statement.text,
                "object_id": statement.object_id,
                "document_title": document.title,
                "value": statement.value,
                "unit": statement.unit,
            })
            for evidence_id in statement.evidence_ids:
                add_edge({"from": evidence_id, "to": statement.statement_id, "type": "SUPPORTS"})
            equipment_id = f"equipment:{statement.object_id}"
            process_id = f"process:{statement.scope.activity or 'unspecified'}"
            procedure_id = f"procedure:{statement.scope.activity or 'unspecified'}"
            step_id = f"step:{statement.statement_id}"
            scope_id = f"scope:{statement.statement_id}"
            add_node({"id": equipment_id, "type": "Equipment"})
            add_node({"id": process_id, "type": "Process"})
            add_node({"id": procedure_id, "type": "Procedure", "activity": statement.scope.activity})
            add_node({"id": step_id, "type": "Step", "statement_id": statement.statement_id, "order": None})
            if statement.scope.equipment and statement.object_id != statement.scope.equipment:
                component_id = f"component:{statement.object_id}"
                add_node({"id": component_id, "type": "Component", "object_id": statement.object_id})
                add_edge({"from": statement.statement_id, "to": component_id, "type": "ABOUT_COMPONENT"})
            if statement.value is not None or statement.quantities:
                quantity_id = f"quantity:{statement.statement_id}"
                add_node({
                    "id": quantity_id,
                    "type": "QuantityValue",
                    "value": statement.value,
                    "unit": statement.unit,
                    "quantities": [
                        {"min": minimum, "max": maximum, "unit": unit}
                        for minimum, maximum, unit in statement.quantities
                    ],
                })
                add_edge({"from": statement.statement_id, "to": quantity_id, "type": "HAS_QUANTITY"})
            add_node({
                "id": scope_id,
                "type": "ApplicabilityScope",
                "value": json.dumps(statement.scope.as_dict(), ensure_ascii=False, sort_keys=True),
            })
            add_edge({"from": statement.statement_id, "to": equipment_id, "type": "ABOUT"})
            add_edge({"from": statement.statement_id, "to": process_id, "type": "APPLIES_TO_PROCESS"})
            add_edge({"from": statement.statement_id, "to": procedure_id, "type": "DEFINES_PROCEDURE"})
            add_edge({"from": procedure_id, "to": step_id, "type": "HAS_STEP"})
            add_edge({"from": statement.statement_id, "to": scope_id, "type": "HAS_APPLICABILITY_SCOPE"})
    return {"schema_version": 1, "nodes": list(node_by_id.values()), "edges": edges}


def _projection_terms(value: str) -> set[str]:
    stopwords = {"a", "an", "and", "for", "how", "in", "is", "of", "on", "or", "the", "to", "what"}
    terms = {
        item.lower()
        for item in re.findall(r"[A-Za-z0-9_-]+|[\u3400-\u9fff]{2,}", value)
        if item.lower() not in stopwords
    }
    for run in re.findall(r"[\u3400-\u9fff]{2,}", value):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms


def projected_retrieve(
    projection: dict[str, Any], *, question: str, context: ScopeContext
) -> tuple[str, ...]:
    """Retrieve directly from projected statement/scope nodes.

    This intentionally does not receive baseline candidate IDs; the benchmark
    can therefore compare two independently produced candidate sets.
    """
    nodes = {node["id"]: node for node in projection["nodes"]}
    scope_by_statement: dict[str, dict[str, Any]] = {}
    for edge in projection["edges"]:
        if edge["type"] != "HAS_APPLICABILITY_SCOPE":
            continue
        scope_node = nodes.get(edge["to"])
        if scope_node is not None:
            scope_by_statement[edge["from"]] = json.loads(scope_node["value"])
    query_terms = _projection_terms(question)
    candidates: list[tuple[float, str]] = []
    for statement_id, node in nodes.items():
        if node.get("type") != "EngineeringStatement":
            continue
        scope_result = match_scope(ApplicabilityScope.from_dict(scope_by_statement.get(statement_id, {})), context)
        if not scope_result.matched:
            continue
        searchable = _projection_terms(f"{node.get('text', '')} {node.get('document_title', '')}")
        overlap = len(query_terms & searchable)
        if query_terms and overlap == 0:
            continue
        candidates.append((float(overlap) + scope_result.specificity * 0.01, statement_id))
    return tuple(statement_id for _, statement_id in sorted(candidates, key=lambda item: (-item[0], item[1])))
