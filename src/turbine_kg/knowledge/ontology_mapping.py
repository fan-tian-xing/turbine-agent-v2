"""Source mentions and replaceable ontology bindings; no model calls."""
from copy import deepcopy
from turbine_kg.knowledge.entity_bindings import map_statement


def sync_ontology_mappings(store, registry, *, force_document_ids=()):
    """Bring existing documents to one ontology without extracting source text.

    Also detect partially upgraded records whose document version was advanced
    before its Statements. Publish the new versions and projections together.
    """
    from turbine_kg.knowledge.store import write_transaction
    forced = set(force_document_ids)
    with write_transaction(store) as transaction:
        documents = transaction.query(
            'MATCH (d:TK:Document) RETURN d.id AS id,d.ontology_version AS ontology_version,'
            'EXISTS { MATCH (s:TK:Statement {document_id:d.id}) '
            'WHERE coalesce(s.ontology_version,"")<>$version } AS stale_mapping '
            'ORDER BY d.id', {'version': registry.version})
        if forced - {row['id'] for row in documents}:
            raise ValueError('重映射资料不存在')
        pending = [row['id'] for row in documents if row['id'] in forced
                   or row.get('ontology_version') != registry.version or row['stale_mapping']]
        results = {document: remap_document(transaction, document, registry) for document in pending}
        if pending:
            transaction.sync_ontology(registry)
    return results


def remap_document(store, document_id, registry):
    """Rebuild current projections in one transaction without re-extraction."""
    from turbine_kg.evidence.context import ContextAssembler
    from turbine_kg.knowledge.statement_sync import persist, statement_projection_ids
    from turbine_kg.knowledge.store import write_transaction
    from turbine_kg.knowledge.store import unpack
    from turbine_kg.knowledge.incremental import content_fingerprint
    from turbine_kg.runtime import Snapshot, json_text
    import json

    with write_transaction(store) as transaction:
        documents = transaction.get([document_id])
        if not documents:
            raise ValueError('重映射资料不存在')
        originals = [unpack(row['node']) for row in transaction.query(
            'MATCH (s:TK:Statement {document_id:$document}) RETURN properties(s) AS node',
            {'document': document_id})]
        indexed = {row['id']: row for row in originals}
        evidence_ids = sorted({support['evidence_id'] for row in originals for support in row['supports']})
        assembler = ContextAssembler(transaction)
        snapshot = Snapshot((document_id,), registry.version)
        views = {eid: assembler.read(eid, snapshot) for eid in evidence_ids}
        statements = []
        for original in originals:
            statement = map_statement(original, registry, views, retain_binding=True)
            statement['extraction_id'] = original['id']
            relations = []
            for relation in original.get('semantic_relations', []):
                raw = deepcopy(relation)
                raw.pop('source_id', None)
                raw.pop('target_id', None)
                relations.append(raw)
            statement['semantic_relations'] = relations
            statements.append(statement)

        rows = transaction.query(
            'MATCH (a:TK)-[r]->(b:TK) WHERE r.document_id=$document '
            'RETURN elementId(r) AS id,type(r) AS kind,a.id AS source,b.id AS target,properties(r) AS properties',
            {'document': document_id})
        derived = statement_projection_ids(rows,set(indexed))
        if derived:
            transaction.query('UNWIND $ids AS id MATCH ()-[r]->() WHERE elementId(r)=id DELETE r', {'ids': derived})
        count = persist(transaction, statements, document_id, registry, preserve_ids=True,source_views=views)
        written = {row['id']: row for row in transaction.get(list(indexed), (document_id,))}
        if set(written) != set(indexed) or any(
            written[sid]['text'] != indexed[sid]['text'] or
            written[sid]['supports'] != indexed[sid]['supports'] for sid in indexed):
            raise ValueError('重映射改变了原始Statement或支持，事务已取消')
        # Persist replaces current records, so obsolete JSON fields disappear.
        for label in ('Document', 'Evidence', 'ScopeDeclaration'):
            predicate = 'n.id=$document' if label == 'Document' else 'n.document_id=$document'
            nodes = [unpack(row['node']) for row in transaction.query(
                f'MATCH (n:TK:{label}) WHERE {predicate} RETURN properties(n) AS node', {'document': document_id})]
            for node in nodes:
                node['ontology_version'] = registry.version
                node['content_hash'] = content_fingerprint(node)
            if nodes:
                transaction.put(label, nodes, replace=True)
        # Existing fact/reference edges retain endpoints and source bindings.
        updates = []
        for row in rows:
            # persist() has just rebuilt these from current Core. Reapplying
            # the old JSON to update a version would restore stale anchors.
            if row['id'] in derived or row['kind'] in ('SUPPORTS','REFERENCES'):
                continue
            data = json.loads(row['properties'].get('data_json') or '{}')
            if row['properties'].get('ontology_version') or data.get('ontology_version'):
                data['ontology_version'] = registry.version
                updates.append({'id': row['id'], 'data': json_text(data)})
        if updates:
            transaction.query('UNWIND $rows AS row MATCH ()-[r]->() WHERE elementId(r)=row.id '
                              'SET r.ontology_version=$version,r.data_json=row.data',
                              {'rows': updates, 'version': registry.version})
    return {'statements': count, 'replaced_projection_edges': len(derived)}
