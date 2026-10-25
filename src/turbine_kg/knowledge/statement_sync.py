"""Shared source and replacement helpers for configured sections."""
from copy import deepcopy
from jsonschema import Draft202012Validator
from turbine_kg.runtime import identity, json_text
from turbine_kg.ontology.registry import normalized
from turbine_kg.knowledge.entity_bindings import source_entity, prepare_occurrences, context_class_members, map_statement
from turbine_kg.knowledge.incremental import evidence_logical_key, statement_logical_key, content_fingerprint
from turbine_kg.knowledge.store import write_transaction, unpack


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




def _replace_statements_atomically(store, document_id, evidence_ids, statements, registry):
    """Replace one verified support component without exposing an empty/partial graph.

    The old statements must be removed before persist() reads same-ID facts and
    merges their supports. Both operations therefore run inside one write
    transaction; an exception rolls back the removal and every partial write.
    """
    with write_transaction(store) as tx_store:
        _discard_statements(tx_store, document_id, evidence_ids)
        return persist(tx_store, statements, document_id, registry)


def _table_context_sources(table, store, document_id, requested_pages, *, source_rows=None):
    """Read explicit source links, expanding only verified complete source groups."""
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


def _merge_supports(rows):
    result={}
    for support in rows:
        eid=support['evidence_id']; merged=result.setdefault(eid, {'evidence_id':eid,'member_keys':[],'quotes':[]})
        merged['member_keys']=list(dict.fromkeys([*merged['member_keys'],*support['member_keys']]))
        merged['quotes']=list({json_text(quote):quote for quote in [*merged['quotes'],*support.get('quotes',[])]}.values())
    return list(result.values())



def _supported_scope_ids(supports, evidence):
    """Scope pointers require agreement of every supported Evidence, never model inference."""
    common=None
    for support in supports:
        ids=evidence.get(support['evidence_id'],{}).get('context',{}).get('scope_ids',[])
        current={value for value in ids if isinstance(value,str) and value} if isinstance(ids,list) else set()
        common=current if common is None else common & current
    return sorted(common or ())



def _statement_references(item,supplied):
    """Bind source reference declarations independently of model-generated relation prose."""
    references=[]
    for support in item['supports']:
        view=supplied[support['evidence_id']]
        for reference in view.get('context',{}).get('reference_resolution',[]):
            keys=[key for key in reference.get('source_member_keys',[]) if key in support['member_keys']]
            if not keys:continue
            references.append({**deepcopy(reference),'source_evidence_id':support['evidence_id'],'source_member_keys':keys})
    return list({json_text(ref):ref for ref in references}.values())



def statement_reference_edges(statements,document_id):
    """Project source-bound references from current Statement metadata."""
    edges=[]
    for node in statements:
        for ref in node.get('references',[]):
            if ref.get('status')!='resolved':continue
            supports={s['evidence_id']:s['member_keys'] for s in node['supports']}
            if not ref.get('source_member_keys') or not set(ref['source_member_keys'])<=set(supports.get(ref.get('source_evidence_id'),[])):
                raise ValueError('陈述条款引用没有绑定其自身支持成员')
            for target in ref.get('target_evidence_ids',[]):
                edges.append({'source':node['id'],'target':target,'key':identity('reference',document_id,node['id'],target,ref),
                    'data':{'document_id':document_id,'statement_id':node['id'],'source_evidence_id':ref['source_evidence_id'],
                            'source_member_keys':ref['source_member_keys'],'source_id':ref.get('source_id'),
                            'char_range':[ref.get('start'),ref.get('end')],'quote':ref.get('quote'),
                            'reference_kind':ref['kind'],'number':ref['number'],'end_number':ref.get('end_number'),'inherit_values':False}})
    return edges



def merge_statement_mentions(rows,statements,document_id,ontology_version,views=None):
    """One related-class cue per assertion, preserving all valid source members."""
    indexed={node['id']:node for node in statements};groups={}
    for row in rows:
        data=row.get('data',{});node=indexed.get(row['target'])
        if node is None or data.get('role')!='related':continue
        supported={(support['evidence_id'],key) for support in node.get('supports',[]) for key in support['member_keys']}
        members=[member for member in data.get('source_members',[]) if
                 (member.get('evidence_id'),member.get('member_key')) in supported]
        contexts=[member for member in data.get('context_members',[]) if
                  member.get('evidence_id') in {s['evidence_id'] for s in node.get('supports',[])} and
                  any(m['member_key']==member.get('member_key') for m in
                      (views or {}).get(member['evidence_id'],{}).get('members',[]))]
        if not members and not contexts:continue
        group=groups.setdefault((row['source'],row['target'],'related'),{'source_members':{},'context_members':{},'entity_roles':set()})
        if members:
            group['entity_roles'].update(role for role in data.get('entity_roles',['related'])
                                         if role in ('subject','object','related'))
        for member in members:
            group['source_members'][(member['evidence_id'],member['member_key'])]=deepcopy(member)
        for member in contexts:
            group['context_members'][(member['evidence_id'],member['member_key'])]=deepcopy(member)
    return [{'source':source,'target':target,'key':identity('mention',document_id,target,source,role),
             'data':{'document_id':document_id,'statement_id':target,'ontology_version':ontology_version,'role':role,
                     'source_members':[members['source_members'][key] for key in sorted(members['source_members'])],
                     **({'context_members':[members['context_members'][key] for key in sorted(members['context_members'])]}
                        if members['context_members'] else {}),
                     **({'entity_roles':sorted(members['entity_roles'])} if members['entity_roles'] else {}),
                     'inherit_values':False}}
            for (source,target,role),members in groups.items()]


def statement_projection_ids(rows, statement_ids, *, semantic=True):
    """Recognize managed projection edges, keeping independent fact/reference edges."""
    result=[]
    for row in rows:
        data=unpack(row['properties']);owner=data.get('statement_id')
        if owner not in statement_ids:continue
        nav=identity('nav',owner,row['source'],data.get('relation_id'),data.get('role'))
        structural=identity('structural',data.get('document_id'),row['source'],row['target'],data.get('relation_id'),owner)
        if row['kind'] in ('MENTIONS','INSTANCE_OF') or row['properties'].get('key')==nav or (
                semantic and row['properties'].get('key')==structural):result.append(row['id'])
    return result



def _entity_node(entity, registry, document_id):
    """Map a source mention to a registered class or retain it inside its Statement."""
    if entity['class']=='Mention':
        domain=(registry.domain_class_by_id(entity['ontology_class_id']) if entity.get('ontology_class_id')
                else None if entity.get('ontology_mapping_review') else getattr(registry, 'resolve_entity_class', registry.domain_class)(entity['label']))
        if domain is not None:
            if entity.get('ontology_class_id') and entity.get('ontology_binding',{}).get('class_id')!=domain['class_id']:
                raise ValueError('本体类别绑定未经来源验证')
            return {**domain,'class':'OntologyClass'}
        return {'id':None,'label':entity['label'],'class':'Mention','storage_status':'statement_only'}
    occurrence=entity['occurrence']
    explicit={key:occurrence[key] for key in ('identifier','object','location','time') if occurrence.get(key)}
    stable=(explicit.get('identifier') if registry.is_a(entity['class'],'PhysicalObject') else
            explicit.get('time') and (explicit.get('object') or explicit.get('location')))
    source=occurrence['source_context'] if stable and occurrence['source_context'] else occurrence['source_anchors']
    label=registry.canonical_label(entity['label'])
    return {'id':identity('occurrence',entity['class'],normalized(label),source or document_id,explicit),
            'label':label,'class':entity['class'],'occurrence':deepcopy(occurrence)}



def _semantic_value(value):
    """Provenance and calculated quality do not define an engineering fact."""
    excluded={'source_supports','source_anchors','source_context','completeness','missing_fields','value_kind','parse_quality','calculation_status','identity_quality','ontology_binding','ontology_mapping_review',
              'expression_status','read_certainty','display_status','source_review','value_binding','unit_binding'}
    if isinstance(value,dict): return {key:_semantic_value(val) for key,val in value.items() if key not in excluded}
    if isinstance(value,list): return sorted((_semantic_value(val) for val in value),key=json_text)
    if isinstance(value,str):return normalized(value)
    return value



def _statement_identity(item,document_id):
    semantic={'text':item.get('text'),'type':item.get('type'),'entities':[]}
    for original in item.get('entities', []):
        entity=source_entity(original)
        semantic['entities'].append({key:entity[key] for key in ('label','role','occurrence') if key in entity})
    anchors=item.get('source_anchors') or [{'source_id':key} for s in item['supports'] for key in s['member_keys']]
    anchors=sorted({json_text(a):a for a in anchors}.values(),key=json_text)
    return identity('statement',document_id,_semantic_value(semantic),anchors)



def persist(store, statements, document_id, registry, *, preserve_ids=False, source_views=None):
    """Persist one representation, with two-pass resolution of batch-local relations."""
    from turbine_kg.evidence.context import ContextAssembler
    from turbine_kg.runtime import Snapshot
    statements=list(statements)
    evidence_ids=list(dict.fromkeys(s['evidence_id'] for item in statements for s in item.get('supports',[])))
    evidence={row['id']:row for row in store.get(evidence_ids,(document_id,))} if hasattr(store,'get') else {}
    assembler=ContextAssembler(store)
    views=dict(source_views or {})
    for eid,row in evidence.items():
        if eid not in views and row.get('members'):
            views[eid]=assembler.read(eid,Snapshot((document_id,),registry.version))
    definition=registry.extraction_schema()
    if preserve_ids:
        # Existing Core relations survive changes in the current ontology.
        # Their addresses/supports still obey the same structural contract.
        definition['properties']['semantic_relations']['items']['properties']['relation_id']={'type':'string','minLength':1}
    nodes={}; edges=[]; concepts=[]; nav=[]; indexed={}; aliases={}; schema=Draft202012Validator(definition)
    domain_nodes={}; domain_edges={}; instance_edges=[]; mention_edges=[]
    existing_keys={}
    if callable(getattr(store, 'query', None)):
        for row in store.query('MATCH (s:TK:Statement {document_id:$document}) RETURN properties(s) AS node', {'document':document_id}):
            saved=unpack(row['node'])
            key=_statement_identity(saved,document_id)
            if key in existing_keys and existing_keys[key] != saved['id']:
                raise ValueError('现有Statement语义身份重复，不能自动合并')
            existing_keys[key]=saved['id']
    def include_domain_class(row):
        pending=[row]
        while pending:
            row=pending.pop()
            if row is None or row['id'] in domain_nodes:continue
            domain_nodes[row['id']]={**row, 'class':'OntologyClass'}
            for parent_id in row.get('parent_class_ids', [row.get('parent_class_id')]):
                if not parent_id:continue
                parent=registry.domain_class_by_id(parent_id)
                if parent is None:raise ValueError('领域类的父类未注册')
                key=identity('subclass',row['id'],parent['id'])
                domain_edges[key]={'source':row['id'],'target':parent['id'],'key':key,
                                   'data':{'relation_id':'subclass_of',
                                           'ontology_version':registry.version,
                                           'definition_sources':row.get('definition_sources',[]),
                                           'inherit_values':False}}
                pending.append(parent)
    for offset,original in enumerate(statements):
        item=deepcopy(original)
        if item.get('supports') and all(s['evidence_id'] in views for s in item['supports']):
            # Mapping is replaceable. A stale/forged binding must not become a
            # durable edge merely because its class ID and role are valid.
            item=map_statement(item,registry,views,retain_binding=True)
        if not item.get('confirmed_interpretations'):
            item.pop('confirmed_interpretations',None)
        forbidden = [field for field in ('conditions','quantities','formulas','missing_fields','completeness','statement_core','ontology_mapping') if field in item]
        if forbidden:
            raise ValueError('Statement 不再保存 conditions、quantities、formulas；请先完成迁移')
        schema_error=next(schema.iter_errors(item),None)
        if schema_error is not None:
            raise ValueError('持久化陈述不符合本体抽取schema：'+'.'.join(map(str,schema_error.absolute_path))+'；'+schema_error.message)
        item['supports']=_merge_supports(item.get('supports',[]))
        extraction_id=item.get('extraction_id',f's{offset}')
        if extraction_id in indexed:raise ValueError('批次陈述临时标识重复')
        for entity in item.get('entities',[]):registry.validate_entity(entity)
        prepare_occurrences(item,registry)
        core_key=_statement_identity(item,document_id)
        sid=item['id'] if preserve_ids else existing_keys.get(core_key,core_key)
        entity_nodes=[]; projected_entities=[]
        for entity in item['entities']:
            concept=_entity_node(entity,registry,document_id)
            projected_entities.append({**concept,'role':entity['role']})
            entity_nodes.append(deepcopy(source_entity(entity)))
            if concept['id'] is None:
                continue
            if concept['class']=='OntologyClass':
                include_domain_class(concept)
            else:
                concepts.append(concept)
                if registry.is_a(entity['class'],'PhysicalObject'):
                    domain=(registry.domain_class_by_id(entity['ontology_class_id']) if entity.get('ontology_class_id')
                            else None if entity.get('ontology_mapping_review') else getattr(registry, 'resolve_entity_class', registry.domain_class)(entity['label']))
                    if entity.get('ontology_class_id') and entity.get('ontology_binding',{}).get('class_id')!=entity['ontology_class_id']:
                        raise ValueError('本体类别绑定未经来源验证')
                    if domain is None and not entity.get('ontology_mapping_review'):
                        occurrence=entity.get('occurrence') or {}
                        domain=registry.domain_class(occurrence.get('object') or '')
                    if domain is not None:
                        include_domain_class(domain)
                        key=identity('instance-of',document_id,sid,concept['id'],domain['id'])
                        instance_edges.append({'source':concept['id'],'target':domain['id'],'key':key,
                                               'data':{'relation_id':'instance_of',
                                                       'ontology_version':registry.version,'statement_id':sid,
                                                       'supports':deepcopy(item['supports']),'inherit_values':False}})
            navigation=[]
            if entity['role']!='related':
                for relation in registry.navigation([item['type']]):
                    try:
                        registry.validate_relation(relation,entity['class'],'Statement',role=entity['role'],statement_type=item['type'])
                        registry.validate_relation(relation,concept['class'],'Statement',role=entity['role'],statement_type=item['type'])
                    except ValueError:continue
                    navigation.append({'source':concept['id'],'target':sid,'key':identity('nav',sid,concept['id'],relation,entity['role']),
                                       'data':{'relation_id':relation,'role':entity['role'],'statement_id':sid,
                                               'ontology_version':registry.version}})
            nav.extend(navigation)
            # Every source-bound entity can be a recall cue. Object roles and
            # types without navigation must not silently disappear; neither
            # becomes an owner merely to make it retrievable.
            if not navigation:
                binding=entity.get('ontology_binding') or {}
                source_members=binding.get('source_members') or []
                supported={(support['evidence_id'],key) for support in item['supports']
                           for key in support['member_keys']}
                if (concept['class']=='OntologyClass' and source_members
                        and all((member.get('evidence_id'),member.get('member_key')) in supported
                                for member in source_members)):
                    mention_edges.append({'source':concept['id'],'target':sid,
                        'key':identity('mention',document_id,sid,concept['id'],source_members),
                        'data':{'statement_id':sid,
                                'ontology_version':registry.version,'role':'related','entity_roles':[entity['role']],
                                'source_members':deepcopy(source_members)}})
        node={**item,'id':sid,'entities':entity_nodes,'document_id':document_id,'kind':'statement','ontology_version':registry.version}
        if sid in nodes:
            node['supports']=_merge_supports([*nodes[sid]['supports'],*item['supports']])
            node['semantic_relations']=[*nodes[sid].get('semantic_relations',[]),*item.get('semantic_relations',[])]
        nodes[sid]=node; indexed[extraction_id]=item; aliases[extraction_id]=(sid,projected_entities)
    # Keep evidence from earlier batches of this same document when merging a repeated fact.
    if hasattr(store,'get') and nodes:
        for existing in store.get(list(nodes),(document_id,)):
            if existing['id'] in nodes:
                node=nodes[existing['id']];node['supports']=_merge_supports([*existing.get('supports',[]),*node['supports']])
                node['_durable_relations']=deepcopy(existing.get('semantic_relations',[])) if not preserve_ids else []
                if 'scope_ids' in existing:node['_previous_scope_ids']=existing['scope_ids']
    evidence_ids=list(dict.fromkeys(support['evidence_id'] for node in nodes.values() for support in node['supports']))
    missing=[eid for eid in evidence_ids if eid not in evidence]
    if missing and hasattr(store,'get'):
        evidence.update({row['id']:row for row in store.get(missing,(document_id,))})
    for eid,row in evidence.items():
        if eid not in views and row.get('members'):
            views[eid]=assembler.read(eid,Snapshot((document_id,),registry.version))
    evidence_keys={key:(row.get('logical_key') or evidence_logical_key(row)) for key,row in evidence.items()}
    for node in nodes.values():
        node['logical_key']=statement_logical_key(node,evidence_keys)
        scope_ids=_supported_scope_ids(node['supports'],evidence)
        if '_previous_scope_ids' in node:
            previous=node.pop('_previous_scope_ids')
            scope_ids=sorted(set(scope_ids) & set(previous if isinstance(previous,list) else []))
        node['scope_ids']=scope_ids
        for support in node['supports']:
            if not support.get('member_keys'):raise ValueError('陈述支持成员为空')
            edges.append({'source':node['id'],'target':support['evidence_id'],'key':identity('support',node['id'],support['evidence_id']),
                          'data':{'member_keys':support['member_keys'],'support_type':'direct','quotes':support.get('quotes',[])}})
    for extraction_id,item in indexed.items():
        sid,entities=aliases[extraction_id]
        persisted_relations=[]
        for relation in item.get('semantic_relations',[]):
            relation_id=registry.resolve_relation_id(relation['relation_id'])
            resolved={**relation,'relation_id':relation_id}
            project=True
            try:registry.validate_semantic_relation(resolved,item['entities'],indexed,extraction_id)
            except ValueError:
                if not preserve_ids:raise
                project=False
            if 'source_ref' in relation:
                endpoints=[]
                endpoint_classes=[]
                for key in ('source_ref','target_ref'):
                    ref=relation[key]
                    if ref['statement_id'] not in aliases:raise ValueError('关系引用未知陈述')
                    owner,owner_entities=aliases[ref['statement_id']]
                    if 'entity_index' in ref and not 0<=ref['entity_index']<len(owner_entities):raise ValueError('关系实体端点越界')
                    endpoints.append(owner_entities[ref['entity_index']]['id'] if 'entity_index' in ref else owner)
                    endpoint_classes.append(owner_entities[ref['entity_index']]['class'] if 'entity_index' in ref else 'Statement')
                source,target=endpoints; supports=relation['supports']
            else:
                if any(not 0<=relation[key]<len(entities) for key in ('source_entity','target_entity')):raise ValueError('关系实体端点越界')
                source,target=entities[relation['source_entity']]['id'],entities[relation['target_entity']]['id']
                endpoint_classes=[entities[relation['source_entity']]['class'],entities[relation['target_entity']]['class']]
                supports=relation.get('supports',item['supports'])
            raw=deepcopy(relation)
            raw.pop('source_id',None);raw.pop('target_id',None)
            for ref in ('source_ref','target_ref'):
                if ref in raw: raw[ref]['statement_id']=aliases[raw[ref]['statement_id']][0]
            if not project or source is None or target is None:
                persisted_relations.append(raw)
                continue
            try:registry.validate_relation(relation_id,*endpoint_classes)
            except ValueError:
                if not preserve_ids:raise
                persisted_relations.append(raw);continue
            if source==target:raise ValueError('语义关系不允许实际身份自环')
            # Stored relations use durable IDs; temporary extraction IDs do not escape this batch.
            durable={**raw,'relation_id':relation['relation_id'],'source_id':source,'target_id':target,'supports':deepcopy(supports)}
            persisted_relations.append(durable)
            nav.append({'source':source,'target':target,'key':identity('structural',document_id,source,target,relation_id,sid),
                        'data':{'relation_id':relation_id,'statement_id':sid,
                                'ontology_version':registry.version,
                                'supports':supports,'support_evidence_ids':list(dict.fromkeys(s['evidence_id'] for s in supports)),
                                'inherit_values':False}})
        node=nodes[sid]
        node.setdefault('_durable_relations',[]).extend(persisted_relations)
    for node in nodes.values():
        node['semantic_relations']=list({json_text(r):r for r in node.pop('_durable_relations',[])}.values())
        node.pop('extraction_id',None)
        node['content_hash']=content_fingerprint(node)
    references=statement_reference_edges(nodes.values(),document_id)
    owned_pairs={(row['source'],row['target']) for row in nav}
    for node in nodes.values():
        for class_id,members in context_class_members(node,views,registry).items():
            concept=registry.domain_class_by_id(class_id)
            if (concept['id'],node['id']) in owned_pairs:continue
            include_domain_class(concept)
            mention_edges.append({'source':concept['id'],'target':node['id'],
                'data':{'role':'related','context_members':members}})
    mention_edges=merge_statement_mentions(
        mention_edges,
        list(nodes.values()),document_id,registry.version,views)
    if callable(getattr(store,'query',None)) and nodes:
        current=store.query('MATCH (a:TK)-[r]->(b:TK) WHERE r.document_id=$document '
            'RETURN elementId(r) AS id,type(r) AS kind,a.id AS source,b.id AS target,properties(r) AS properties',
            {'document':document_id})
        managed=set(statement_projection_ids(current,set(nodes),semantic=False))
        managed.update(row['id'] for row in current if row['kind']=='REFERENCES' and
            row['source'] in nodes and unpack(row['properties']).get('statement_id')==row['source'])
        keep={(kind,row['key'],row['source'],row['target']) for kind,rows in
              [('MENTIONS',mention_edges),('INSTANCE_OF',instance_edges),('REFERENCES',references)]
              for row in rows}
        keep.update((registry.relationship_type(row['data']['relation_id']),row['key'],row['source'],row['target']) for row in nav)
        stale=[row['id'] for row in current if row['id'] in managed and
               (row['kind'],row['properties'].get('key'),row['source'],row['target']) not in keep]
        if stale:store.query('UNWIND $ids AS id MATCH ()-[r]->() WHERE elementId(r)=id DELETE r',{'ids':stale})
    for kind in {concept['class'] for concept in concepts}:store.put(kind,[concept for concept in concepts if concept['class']==kind])
    if domain_nodes:store.put('OntologyClass',list(domain_nodes.values()))
    if callable(getattr(store,'query',None)):
        store.put('Statement',list(nodes.values()),replace=True)
    else:
        store.put('Statement',list(nodes.values()))
    # Every document assertion edge must be visible to document-scoped queries.
    # Ontology subclass definitions are shared and deliberately have no owner.
    for row in [*edges,*nav,*instance_edges,*mention_edges,*references]:
        row.setdefault('data',{})['document_id']=document_id
    store.edges('SUPPORTS',edges)
    for relation_id in dict.fromkeys(row['data']['relation_id'] for row in nav):
        store.edges(registry.relationship_type(relation_id),
                    [row for row in nav if row['data']['relation_id']==relation_id])
    if domain_edges:store.edges('SUBCLASS_OF',list(domain_edges.values()))
    if instance_edges:store.edges('INSTANCE_OF',list({row['key']:row for row in instance_edges}.values()))
    if mention_edges:store.edges('MENTIONS',mention_edges)
    if callable(getattr(store,'query',None)) and nodes:
        store.query('MATCH (c:TK:OntologyClass)-[r:MENTIONS]->(s:TK:Statement) '
            'WHERE s.document_id=$document AND s.id IN $ids '
            'AND NOT any(row IN $keep WHERE c.id=row.source AND s.id=row.target AND coalesce(r.key,"")=row.key) DELETE r',
            {'document':document_id,'ids':list(nodes),'keep':mention_edges})
    if references:store.edges('REFERENCES',references)
    return len(nodes)
