"""Persist and retrieve the small confirmed trial through the v2 database."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from neo4j import GraphDatabase

from turbine_kg.settings import PROJECT_ROOT, Settings
from .applicability import match_scope
from .models import (
    ApplicabilityScope, Asset, Claim, EngineeringStatement, Evidence, FixtureDocument,
    LogicalDocument, Page, Revision, ScopeContext, SourceSpan,
)
from .projection import build_traceability_projection, _projection_terms
from .real_trial import load_confirmed_real_corpus
from .validation import validate_claim


BATCH = "stage3-confirmed-real"
NODE_TYPES = {
    "Asset", "LogicalDocument", "Revision", "Page", "SourceSpan", "Evidence",
    "EngineeringStatement", "Equipment", "Component", "Process", "Procedure", "Step",
    "QuantityValue", "ApplicabilityScope",
}
EDGE_TYPES = {
    "IDENTIFIES", "HAS_REVISION", "HAS_PAGE", "HAS_SPAN", "SUPPORTS", "ABOUT",
    "ABOUT_COMPONENT", "APPLIES_TO_PROCESS", "DEFINES_PROCEDURE", "HAS_STEP",
    "HAS_QUANTITY", "HAS_APPLICABILITY_SCOPE",
}
SEARCH = """
MATCH (batch:Stage3Batch {id: $batch})
MATCH (s:Stage3Node:EngineeringStatement {batch: $batch})
WHERE s.id IN batch.statement_ids
WITH s, [term IN $terms WHERE term IN s.search_terms] AS matched
WHERE size(matched) >= $minimum_matches
MATCH (a:Stage3Node:Asset {batch: $batch})-[:IDENTIFIES]->
      (d:Stage3Node:LogicalDocument {batch: $batch})-[:HAS_REVISION]->
      (r:Stage3Node:Revision {batch: $batch})-[:HAS_PAGE]->
      (p:Stage3Node:Page {batch: $batch})-[:HAS_SPAN]->
      (sp:Stage3Node:SourceSpan {batch: $batch})-[:SUPPORTS]->
      (e:Stage3Node:Evidence {batch: $batch})-[:SUPPORTS]->(s)
WHERE e.id IN s.evidence_ids AND sp.id IN e.source_span_ids
WITH s, a, d, r, matched,
     collect(DISTINCT {evidence: properties(e), span: properties(sp), page: properties(p)}) AS sources
RETURN properties(s) AS statement, properties(a) AS asset,
       properties(d) AS document, properties(r) AS revision, sources,
       size(matched) AS score
ORDER BY score DESC, s.id
"""


def make_projection(documents, registry_assets: dict) -> dict:
    projection = build_traceability_projection(documents)
    nodes = {item["id"]: item for item in projection["nodes"]}
    for doc in documents:
        asset = registry_assets[doc.asset.asset_id]
        if any(asset[key] != value for key, value in {
            "document_logical_id": doc.logical_document.document_logical_id,
            "revision_id": doc.revision.revision_id,
            "relative_path": doc.asset.relative_path,
        }.items()):
            raise ValueError("confirmed corpus identity differs from Registry")
        registry_scope = tuple(asset.get("applicability_scope", ()))
        if not registry_scope or doc.asset.registry_applicability_scope != registry_scope:
            raise ValueError("confirmed source applicability differs from Registry")
        if (asset["admission_status"] not in {"admitted", "duplicate_or_derivative"}
                or asset["text_adapter_status"] not in {"native_text_available", "ocr_validated"}
                or asset["completeness_status"] != "complete"):
            raise ValueError("trial source is not ready for import")
        nodes[doc.asset.asset_id].update(asdict(doc.asset), sha256=asset["sha256"])
        nodes[doc.logical_document.document_logical_id].update(
            title=doc.title, source_role=doc.source_role,
            source_scope=json.dumps(doc.source_scope.as_dict(), ensure_ascii=False),
            source_applicability=asset["applicability_scope"],
        )
        nodes[doc.revision.revision_id].update(asdict(doc.revision))
        for page in doc.pages:
            # Only the confirmed source spans are persisted, not the sampled page body.
            nodes[page.page_id].update(
                page_number=page.page_number,
                physical_page=page.page_number,
                logical_page=page.logical_page,
                revision_id=page.revision_id,
            )
        for span in doc.spans:
            quotes = [ev.text for ev in doc.evidence if span.span_id in ev.source_span_ids]
            nodes[span.span_id].update(page_id=span.page_id, quote="\n".join(quotes), content_kind=span.content_kind)
        for ev in doc.evidence:
            props = asdict(ev)
            props["quantities"] = json.dumps(props["quantities"])
            nodes[ev.evidence_id].update(props)
        for statement in doc.statements:
            check = validate_claim(Claim(
                claim_id="import:" + statement.statement_id, claim_type="fact",
                text=statement.text, statement_id=statement.statement_id,
                evidence_ids=statement.evidence_ids, object_id=statement.object_id,
                context=ScopeContext.from_dict(statement.scope.as_dict()),
                value=statement.value, unit=statement.unit, quantities=statement.quantities,
            ), (doc,))
            if not check.allowed:
                raise ValueError(f"confirmed statement failed validation: {check.failures}")
            props = asdict(statement)
            props["scope"] = json.dumps(statement.scope.as_dict(), ensure_ascii=False)
            props["quantities"] = json.dumps(props["quantities"])
            props["search_terms"] = sorted(_projection_terms(statement.text + " " + doc.title))
            nodes[statement.statement_id].update(props)
    return projection


def hit_document(hit: dict) -> FixtureDocument:
    """Reconstruct only the retrieved knowledge, using database-returned fields."""
    a, d, r, s = (hit[key] for key in ("asset", "document", "revision", "statement"))
    statement = EngineeringStatement(
        s["id"], s["statement_type"], s["text"], tuple(s["evidence_ids"]), s["object_id"],
        ApplicabilityScope.from_dict(json.loads(s["scope"])), s.get("value"), s.get("unit"),
        quantities=tuple(tuple(q) for q in json.loads(s["quantities"])),
    )
    evidence, spans, pages = {}, {}, {}
    for source in hit["sources"]:
        e, sp, p = (source[key] for key in ("evidence", "span", "page"))
        evidence[e["id"]] = Evidence(
            e["id"], tuple(e["source_span_ids"]), e["statement_id"], e["object_id"], e["text"],
            e.get("value"), e.get("unit"), tuple(tuple(q) for q in json.loads(e["quantities"])),
        )
        spans[sp["id"]] = SourceSpan(sp["id"], p["id"], sp["quote"])
        pages[p["id"]] = Page(
            p["id"], r["id"], p["page_number"], sp["quote"], p.get("logical_page")
        )
    scope = ApplicabilityScope.from_dict(json.loads(d["source_scope"]))
    return FixtureDocument(
        Asset(
            a["id"],
            a["relative_path"],
            a["asset_kind"],
            a["source_root_id"],
            tuple(a.get("registry_applicability_scope", d.get("source_applicability", ()))),
        ),
        LogicalDocument(d["id"], d["title"], d["source_role"], scope),
        Revision(r["id"], d["id"], r["label"]), d["source_role"], d["title"], scope,
        tuple(pages.values()), tuple(spans.values()), tuple(evidence.values()), (statement,),
    )


class TrialGraph:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password),
            connection_timeout=5, max_transaction_retry_time=5,
        )

    def __enter__(self):
        self.driver.verify_connectivity()
        return self

    def __exit__(self, *_):
        self.driver.close()

    def import_confirmed(self, *, root: Path = PROJECT_ROOT) -> dict:
        corpus = load_confirmed_real_corpus(
            root / "data/stage3/real_trial_confirmation.json", root / "var/stage3/real_trial_pages.json",
        )
        assets = {row["asset_id"]: row for row in (
            json.loads(line) for line in (root / "data/registry/source_assets.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )}
        projection = make_projection(corpus, assets)
        statement_ids = [s.statement_id for d in corpus for s in d.statements]

        def neo4j_row(node):
            row = {**node, "batch": BATCH, "formal_release": False}
            if isinstance(row.get("quantities"), list) and any(isinstance(item, dict) for item in row["quantities"]):
                row["quantities"] = json.dumps(row["quantities"], ensure_ascii=False)
            return row

        def write(tx):
            for kind in sorted(NODE_TYPES):
                rows = [neo4j_row(n) for n in projection["nodes"] if n["type"] == kind]
                tx.run(
                    f"UNWIND $rows AS row MERGE (n:Stage3Node:{kind} {{batch: $batch, id: row.id}}) SET n = row",
                    rows=rows, batch=BATCH,
                ).consume()
            for kind in sorted(EDGE_TYPES):
                rows = [e for e in projection["edges"] if e["type"] == kind]
                tx.run(
                    "UNWIND $rows AS row MATCH (a:Stage3Node {batch: $batch, id: row.from}) "
                    "MATCH (b:Stage3Node {batch: $batch, id: row.to}) "
                    f"MERGE (a)-[:{kind}]->(b)", rows=rows, batch=BATCH,
                ).consume()
            tx.run(
                "MERGE (b:Stage3Batch {id: $batch}) SET b.statement_ids = $ids, b.formal_release = false",
                batch=BATCH, ids=statement_ids,
            ).consume()

        with self.driver.session(database=self.settings.neo4j_database) as session:
            session.execute_write(write)
        return self.status()

    def status(self) -> dict:
        with self.driver.session(database=self.settings.neo4j_database) as session:
            counts = session.execute_read(lambda tx: tx.run(
                "MATCH (n:Stage3Node {batch: $batch}) RETURN n.type AS type, count(n) AS count",
                batch=BATCH,
            ).data())
            edges = session.execute_read(lambda tx: tx.run(
                "MATCH (:Stage3Node {batch: $batch})-[r]->(:Stage3Node {batch: $batch}) RETURN count(r) AS count",
                batch=BATCH,
            ).single()["count"])
        return {"batch": BATCH, "formal_release": False, "nodes": {r["type"]: r["count"] for r in counts}, "edges": edges}

    def retrieve(self, question: str, context: ScopeContext | None = None, limit: int = 4) -> list[dict]:
        terms = sorted(_projection_terms(question))
        if not terms:
            return []
        with self.driver.session(database=self.settings.neo4j_database) as session:
            hits = session.execute_read(lambda tx: tx.run(
                SEARCH, batch=BATCH, terms=terms, minimum_matches=min(2, len(terms)),
            ).data())
        accepted = []
        for hit in hits:
            doc = hit_document(hit)
            match = match_scope(doc.statements[0].scope, context or ScopeContext())
            if any(reason.startswith("mismatch:") for reason in match.reasons):
                continue
            hit["applicability"] = "matched" if match.matched else "conditional_reference"
            hit["missing_context"] = list(match.reasons)
            accepted.append(hit)
        return accepted[:limit]
