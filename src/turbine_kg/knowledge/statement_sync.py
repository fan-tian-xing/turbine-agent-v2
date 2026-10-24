"""Shared source and replacement helpers for configured sections."""
from contextlib import contextmanager
from turbine_kg.runtime import json_text


def _support_closure(store, document_id, evidence_ids):
    """Include all evidence whose shared statements must be retired together."""
    selected=set(evidence_ids)
    while selected:
        rows=store.query('MATCH (s:TK:Statement)-[:SUPPORTS]->(e:TK:Evidence), (s)-[:SUPPORTS]->(other:TK:Evidence) WHERE s.document_id=$document AND e.document_id=$document AND other.document_id=$document AND e.id IN $evidence RETURN DISTINCT other.id AS id',
                         {'document':document_id,'evidence':sorted(selected)})
        records=store.get(sorted(selected),(document_id,))
        durable={key for item in records for key in item.get('reextract_support_component',[]) }
        available={item['id'] for item in store.get(sorted(durable),(document_id,))}
        expanded=selected|{row['id'] for row in rows}|available
        if expanded==selected:break
        selected=expanded
    return selected


def _discard_statements(store, document_id, evidence_ids):
    """Remove only stale semantics owned by changed evidence in this document."""
    if not evidence_ids:return
    rows=store.query('MATCH (s:TK:Statement)-[:SUPPORTS]->(e:TK:Evidence) WHERE s.document_id=$document AND e.id IN $evidence RETURN DISTINCT s.id AS id',
                     {'document':document_id,'evidence':list(evidence_ids)})
    ids=[row['id'] for row in rows]
    if not ids:return
    # Roles without navigation edges can still be referenced in Statement JSON.
    # Capture only entities of the exact facts being replaced, then check every
    # retained fact and edge before retiring any entity.
    entity_ids=sorted({entity['id'] for statement in store.get(ids,(document_id,))
                       for entity in statement.get('entities',[]) if entity.get('id')})
    from turbine_kg.ontology.registry import OntologyRegistry
    registry=OntologyRegistry()
    business_types=[registry.relationship_type(key) for key,row in registry.relations.items() if row.get('extractable')]
    # Statement-owned entity edges store their binding in JSON, including
    # INSTANCE_OF whose endpoints both survive a Statement replacement.
    bindings=[json_text({'statement_id':sid})[1:-1] for sid in ids]
    store.query('MATCH (:TK)-[r]->(:TK) WHERE type(r) IN $types AND r.document_id=$document AND any(binding IN $bindings WHERE r.data_json CONTAINS binding) DELETE r',
                {'document':document_id,'bindings':bindings,'types':[*business_types,'INSTANCE_OF']})
    store.query('MATCH (s:TK:Statement) WHERE s.id IN $ids AND s.document_id=$document DETACH DELETE s',{'ids':ids,'document':document_id})
    if entity_ids:
        store.query('MATCH (n:TK) WHERE n.id IN $ids AND (n:PhysicalObject OR n:Equipment OR n:Component OR n:System OR n:Tool OR n:Activity) AND NOT (n)--() AND NOT EXISTS { MATCH (other:TK) WHERE other.id<>n.id AND other.data_json CONTAINS n.id } DELETE n',
                    {'ids':entity_ids})


@contextmanager
def _write_transaction(store):
    """Use one backend write transaction for a related group of graph changes."""
    if callable(getattr(store, 'transaction', None)):
        with store.transaction() as tx_store:
            yield tx_store
        return

    if not hasattr(store, 'driver') or not hasattr(store, 'settings'):
        raise RuntimeError('存储端不支持事务，不能进行成组图谱写入')

    from turbine_kg.knowledge.store import KnowledgeStore

    class TransactionStore:
        put = KnowledgeStore.put
        edges = KnowledgeStore.edges
        get = KnowledgeStore.get
        _sync_document = KnowledgeStore._sync_document
        save_page = KnowledgeStore.save_page
        table_sources = KnowledgeStore.table_sources

        def __init__(self, tx, settings):
            self.tx = tx
            self.settings = settings

        def query(self, text, parameters=None, budget=None):
            return self.tx.run(text, parameters or {}).data()

    with store.driver.session(database=store.settings.neo4j_database) as session:
        with session.begin_transaction() as tx:
            yield TransactionStore(tx, store.settings)
            tx.commit()


def _replace_statements_atomically(store, document_id, evidence_ids, statements, registry):
    """Replace one verified support component without exposing an empty/partial graph.

    The old statements must be removed before persist() reads same-ID facts and
    merges their supports. Both operations therefore run inside one write
    transaction; an exception rolls back the removal and every partial write.
    """
    from turbine_kg.knowledge.statement_extraction import persist
    with _write_transaction(store) as tx_store:
        _discard_statements(tx_store, document_id, evidence_ids)
        return persist(tx_store, statements, document_id, registry)


def _table_context_sources(table, store, document_id, requested_pages, *, source_rows=None):
    """Read explicit source links, expanding only verified complete source groups."""
    from turbine_kg.knowledge.store import unpack
    roles={source_id:'table_title' for source_id in table.locator.get('title_source_ids',[])}
    roles.update({source_id:'explicit_table_reference' for source_id in table.locator.get('related_body_source_ids',[])})
    ids=list(dict.fromkeys(list(roles)+table.locator.get('inherited_header_source_ids',[])+
                         [source['id'] for cell in table.cells for source in cell.attributes.get('section_sources',[])]))
    sources=store.get(ids,(document_id,));selected={};groups={};missing=set()
    required=set(roles)|set(table.locator.get('inherited_header_source_ids',[]))
    available={source['id'] for source in sources if source.get('quality') not in ('unreadable','unrecognized','pending_review') and source.get('attributes',{}).get('semantic_status')!='pending_review'}
    absent=required-available
    if absent:
        table.locator['body_context_status']='incomplete'
        table.locator['missing_context_source_ids']=sorted(absent)
    for source in sources:
        attrs=source.get('attributes',{});group=attrs.get('cross_page_group')
        if group and source['id'] in roles:
            groups[group]=source
            continue
        selected[source['id']]={**source,'attributes':{**attrs,**({'table_context_role':roles[source['id']]} if source['id'] in roles else {})}}
    for group,reference in groups.items():
        if source_rows is None:
            rows=store.query('MATCH (n:TK:SourceText {document_id:$document}) WHERE n.data_json CONTAINS $group RETURN properties(n) AS node',
                             {'document':document_id,'group':group})
            members=[unpack(row['node']) for row in rows]
        else:members=list(source_rows)
        members=[source for source in members if source.get('document_id')==document_id and source.get('attributes',{}).get('cross_page_group')==group
                 and source.get('quality') not in ('unreadable','unrecognized','pending_review') and source.get('attributes',{}).get('semantic_status')!='pending_review']
        expected=set(reference.get('attributes',{}).get('cross_page_complete_pages',[]))
        actual={source['locator'].get('physical_page') for source in members}
        expected_orders=set();actual_orders=set()
        for source in members:
            attrs=source.get('attributes',{})
            if isinstance(attrs.get('source_total_items'),int):expected_orders.update(range(attrs['source_total_items']+1))
            if isinstance(attrs.get('group_order'),int):actual_orders.add(attrs['group_order'])
        if not expected or not expected.issubset(set(requested_pages)) or not expected.issubset(actual) or not expected_orders.issubset(actual_orders):
            missing.update(expected-actual or expected-set(requested_pages))
            table.locator['body_context_status']='incomplete'
            continue
        for source in sorted(members,key=lambda source:(source['locator'].get('physical_page',0),source.get('attributes',{}).get('group_order',source.get('order',0)))):
            selected[source['id']]={**source,'attributes':{**source.get('attributes',{}),'table_context_role':'explicit_table_reference','table_context_group':group}}
    if groups and table.locator.get('body_context_status')!='incomplete':table.locator['body_context_status']='verified_complete'
    if missing:table.locator['missing_context_pages']=sorted(missing)
    return list(selected.values())
