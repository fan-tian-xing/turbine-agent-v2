"""Read-only adapter from the frozen research contract to Stage 9 validation.

Record identifiers are reversibly encoded as RDF IRIs, not redefined. The
original projection and research knowledge retain their existing IDs/content.
"""

from __future__ import annotations

from urllib.parse import quote

from turbine_kg.stage3.applicability import statement_scope_within_source

from .semantic import SemanticValidationError, failure_report, validate_runtime_payload


def _iri(kind: str, identifier: str) -> str:
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError(f"{kind} identifier must be a non-empty string")
    return f"urn:turbine-v2:{kind}:" + quote(identifier, safe="")


def research_payload(documents) -> dict:
    nodes = {}
    relations = set()

    def add_node(identifier, class_name, properties):
        node = {"id": identifier, "type": class_name, "properties": properties}
        if identifier in nodes and nodes[identifier] != node:
            raise ValueError(f"conflicting data for the same record ID: {identifier}")
        nodes[identifier] = node

    def link(source, predicate, target):
        relations.add((source, predicate, target))

    try:
        for document in documents:
            if document.revision.document_logical_id != document.logical_document.document_logical_id:
                raise ValueError("Revision references a different LogicalDocument")
            pages = {page.page_id: page for page in document.pages}
            spans = {span.span_id: span for span in document.spans}
            evidence = {item.evidence_id: item for item in document.evidence}
            statements = {item.statement_id: item for item in document.statements}
            for name, values, keyed in (
                ("Page", document.pages, pages), ("SourceSpan", document.spans, spans),
                ("Evidence", document.evidence, evidence), ("Statement", document.statements, statements),
            ):
                if len(values) != len(keyed):
                    raise ValueError(f"duplicate {name} ID inside a research document")
            for span in document.spans:
                if span.page_id not in pages:
                    raise ValueError(f"SourceSpan references missing Page: {span.span_id}")
                page = pages[span.page_id]
                if page.revision_id != document.revision.revision_id:
                    raise ValueError(f"Page references wrong Revision: {page.page_id}")
                if not span.quote.strip() or "".join(span.quote.split()) not in "".join(page.text.split()):
                    raise ValueError(f"SourceSpan text is absent from its Page: {span.span_id}")
                properties = {
                    "spanText": span.quote, "physicalPage": page.page_number, "pageId": page.page_id,
                    "revisionId": page.revision_id, "documentId": document.logical_document.document_logical_id,
                }
                if page.logical_page is not None:
                    properties["logicalPage"] = page.logical_page
                add_node(_iri("record", span.span_id), "SourceSpan", properties)
            for item in document.evidence:
                if item.statement_id not in statements:
                    raise ValueError(f"Evidence references missing Statement: {item.evidence_id}")
                if not item.source_span_ids:
                    raise ValueError(f"Evidence requires a SourceSpan: {item.evidence_id}")
                eid = _iri("record", item.evidence_id)
                add_node(eid, "Evidence", {"evidenceText": item.text})
                # Do not create an object based on a dangling Evidence reference.
                link(eid, "evidenceAboutEntity", _iri("object", item.object_id))
                for span_id in item.source_span_ids:
                    if span_id not in spans:
                        raise ValueError(f"Evidence references missing SourceSpan: {span_id}")
                    link(eid, "sourceSpan", _iri("record", span_id))
                # Preserve the older synthetic fixture's descriptive Evidence
                # contract. Actual reviewed source quotes must be present in
                # one bound span, never assembled by joining separate spans.
                if document.asset.asset_kind != "synthetic_fixture" and not any(
                    "".join(item.text.split()) in "".join(spans[span_id].quote.split())
                    for span_id in item.source_span_ids
                ):
                    raise ValueError(f"Reviewed Evidence quote is absent from its SourceSpan: {item.evidence_id}")
            for statement in document.statements:
                valid, errors = statement_scope_within_source(document.source_scope, statement.scope)
                if not valid:
                    raise ValueError(f"Statement broadens source applicability: {errors}")
                sid = _iri("record", statement.statement_id)
                entity_id = _iri("object", statement.object_id)
                add_node(entity_id, "PhysicalEntity", {"objectKey": statement.object_id})
                add_node(sid, "EngineeringStatement", {
                    "statementText": statement.text, "statementType": statement.statement_type,
                })
                link(sid, "aboutEntity", entity_id)
                if not statement.evidence_ids:
                    raise ValueError(f"Statement requires Evidence: {statement.statement_id}")
                for evidence_id in statement.evidence_ids:
                    if evidence_id not in evidence:
                        raise ValueError(f"Statement references missing Evidence: {evidence_id}")
                    if evidence[evidence_id].statement_id != statement.statement_id:
                        raise ValueError(f"Evidence belongs to a different Statement: {evidence_id}")
                    link(sid, "supportedBy", _iri("record", evidence_id))
                scope_id = _iri("scope", statement.statement_id)
                scope_properties = {}
                scope = statement.scope.as_dict()
                for source_key, predicate in (
                    ("model", "model"), ("equipment", "equipment"), ("lifecycle_stage", "lifecycleStage"),
                    ("activity", "activity"), ("operating_state", "operatingState"), ("condition", "condition"),
                ):
                    if source_key in scope:
                        scope_properties[predicate] = scope[source_key]
                if scope.get("capacity_range"):
                    for key, predicate in (("min", "capacityMinimum"), ("max", "capacityMaximum")):
                        if key in scope["capacity_range"]:
                            scope_properties[predicate] = scope["capacity_range"][key]
                add_node(scope_id, "ApplicabilityScope", scope_properties)
                link(sid, "hasApplicabilityScope", scope_id)
                quantity_properties = []
                if statement.value is not None:
                    quantity_properties.append({"numericValue": statement.value, "unitSymbol": statement.unit})
                elif statement.unit is not None and not statement.quantities:
                    raise ValueError("a standalone quantity unit requires a numeric value or interval")
                for minimum, maximum, unit in statement.quantities:
                    properties = {"unitSymbol": unit}
                    if minimum is not None:
                        properties["minimumValue"] = minimum
                    if maximum is not None:
                        properties["maximumValue"] = maximum
                    quantity_properties.append(properties)
                for index, properties in enumerate(quantity_properties):
                    qid = _iri("quantity", f"{statement.statement_id}:{index}")
                    add_node(qid, "QuantityValue", properties)
                    link(sid, "hasQuantityValue", qid)
    except (ValueError, TypeError, AttributeError) as error:
        raise SemanticValidationError(failure_report("research_adapter", str(error))) from error
    return {
        "schema_version": 1,
        "nodes": [nodes[key] for key in sorted(nodes)],
        "relations": [{"source": source, "predicate": predicate, "target": target}
                      for source, predicate, target in sorted(relations)],
    }


def validate_research_documents(documents) -> dict:
    try:
        return validate_runtime_payload(research_payload(documents))
    except SemanticValidationError as error:
        return error.report
