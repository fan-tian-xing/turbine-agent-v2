"""Source mentions and replaceable ontology bindings; no model calls."""
from copy import deepcopy


def source_entity(entity):
    """Recover original wording from both old class proxies and raw mentions."""
    binding = entity.get('ontology_binding') or {}
    label = entity.get('surface_label') or binding.get('surface_label') or entity.get('label')
    kind = entity.get('class', 'Mention')
    if kind == 'OntologyClass':
        kind = 'Mention'
    role = entity.get('role', 'related')
    result = {'label': label, 'class': kind, 'role': role}
    for key in ('occurrence', 'ontology_class_id', 'ontology_binding', 'ontology_mapping_review'):
        if key in entity:
            result[key] = deepcopy(entity[key])
    if entity.get('class') == 'OntologyClass':
        result['ontology_class_id'] = entity.get('class_id') or binding.get('class_id')
    return result


def map_statement(statement, registry, views, *, retain_binding=False):
    """Rebind current source mentions without changing text, roles, or order."""
    from turbine_kg.knowledge.entity_bindings import restored_entity_binding
    result = deepcopy(statement)
    result['entities'] = [source_entity(entity) for entity in result.get('entities', [])]
    for entity in result['entities']:
        previous = entity.pop('ontology_binding', None) or {}
        requested = entity.pop('ontology_class_id', None) if retain_binding else None
        entity.pop('ontology_class_id', None)
        entity.pop('ontology_mapping_review', None)
        reviewed_views = views
        if retain_binding and previous.get('method') == 'reviewed_source_mapping':
            reviewed_views = deepcopy(views)
            for view in reviewed_views.values():
                view.setdefault('entity_mapping_reviews', []).append(previous)
        binding = restored_entity_binding(entity['label'], result['supports'], reviewed_views, registry)
        resolved = registry.resolve_entity_class(entity['label'], upper_class=entity['class'])
        class_id = resolved['class_id'] if resolved else requested
        if binding is None and class_id:
            try:
                binding = registry.ontology_binding(entity['label'], class_id, result['supports'], views)
            except ValueError:
                pass
        if binding is None and resolved is None:
            # Recover a short source name only when the supported headings
            # uniquely identify a registered class (the same binder used for
            # full names). Section co-occurrence alone never creates a match.
            candidates = {}
            for candidate in [*registry.business_classes, *registry.domain_classes]:
                if entity['class'] != 'Mention' and not registry.domain_is_a(candidate, entity['class']):
                    continue
                try:
                    candidates[candidate] = registry.ontology_binding(entity['label'], candidate, result['supports'], views)
                except ValueError:
                    continue
            specific = registry._most_specific_class_ids(set(candidates))
            if len(specific) == 1:
                binding = candidates[next(iter(specific))]
        if binding:
            entity['ontology_class_id'] = binding['class_id']
            entity['ontology_binding'] = binding
        else:
            entity['ontology_mapping_review'] = {'status': 'unmapped'}
    return result


def remap_document(store, document_id, registry):
    """Rebuild current projections in one transaction without re-extraction."""
    from turbine_kg.evidence.context import ContextAssembler
    from turbine_kg.knowledge.statement_extraction import persist
    from turbine_kg.knowledge.statement_sync import _write_transaction
    from turbine_kg.knowledge.store import unpack
    from turbine_kg.knowledge.incremental import content_fingerprint
    from turbine_kg.runtime import Snapshot, identity, json_text
    import json

    removed = ('statement_core', 'ontology_mapping', 'conditions', 'quantities',
               'formulas', 'missing_fields', 'completeness', '_ontology_context')
    with _write_transaction(store) as transaction:
        documents = transaction.get([document_id])
        if not documents:
            raise ValueError('重映射资料不存在')
        originals = [unpack(row['node']) for row in transaction.query(
            'MATCH (s:TK:Statement {document_id:$document}) RETURN properties(s) AS node',
            {'document': document_id})]
        indexed = {row['id']: row for row in originals}
        evidence_ids = sorted({support['evidence_id'] for row in originals for support in row['supports']})
        assembler = ContextAssembler(transaction)
        snapshot = Snapshot((document_id,), registry.version, ())
        views = {eid: assembler.read(eid, snapshot) for eid in evidence_ids}
        statements = []
        for original in originals:
            statement = map_statement(original, registry, views, retain_binding=True)
            statement['extraction_id'] = original['id']
            # Older graph records stored class-node endpoints only. Recover
            # their exact local entity reference before replacing projections.
            relations = []
            for relation in [*original.get('semantic_relations', []), *original.get('unmapped_relations', [])]:
                raw = deepcopy(relation)
                if 'source_ref' not in raw and 'source_entity' not in raw:
                    for side in ('source', 'target'):
                        endpoint = raw.get(side + '_id')
                        if endpoint in indexed:
                            raw[side + '_ref'] = {'statement_id': endpoint}
                        else:
                            matches = [i for i, entity in enumerate(original.get('entities', []))
                                       if entity.get('id') == endpoint]
                            if len(matches) != 1:
                                raise ValueError('旧语义关系无法唯一恢复原始实体引用；须局部核对Evidence')
                            raw[side + '_ref'] = {'statement_id': original['id'], 'entity_index': matches[0]}
                raw.pop('source_id', None)
                raw.pop('target_id', None)
                relations.append(raw)
            statement['semantic_relations'] = relations
            statement.pop('unmapped_relations', None)
            for field in removed:
                statement.pop(field, None)
            statements.append(statement)

        rows = transaction.query(
            'MATCH (a:TK)-[r]->(b:TK) WHERE r.document_id=$document '
            'RETURN elementId(r) AS id,type(r) AS kind,a.id AS source,b.id AS target,properties(r) AS properties',
            {'document': document_id})
        derived = []
        for row in rows:
            data = json.loads(row['properties'].get('data_json') or '{}')
            owner = data.get('statement_id')
            if owner not in indexed:
                continue
            key = row['properties'].get('key')
            nav = identity('nav', owner, row['source'], data.get('relation_id'), data.get('role'))
            structural = identity('structural', document_id, row['source'], row['target'], data.get('relation_id'), owner)
            if row['kind'] in ('MENTIONS', 'INSTANCE_OF') or key in (nav, structural):
                derived.append(row['id'])
        if derived:
            transaction.query('UNWIND $ids AS id MATCH ()-[r]->() WHERE elementId(r)=id DELETE r', {'ids': derived})
        count = persist(transaction, statements, document_id, registry, preserve_ids=True)
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
                if label == 'Evidence':
                    node.pop('extraction_attempts', None)
                    node.pop('extraction_attempt_count', None)
                node['ontology_version'] = registry.version
                node['content_hash'] = content_fingerprint(node)
            if nodes:
                transaction.put(label, nodes, replace=True)
        # Existing fact/reference edges retain endpoints and source bindings.
        updates = []
        for row in rows:
            if row['id'] in derived:
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
