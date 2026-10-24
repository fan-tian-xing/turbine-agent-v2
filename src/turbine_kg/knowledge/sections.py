"""Prepare bounded document sections and extract selected source groups in memory."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

from turbine_kg.agent.model import ModelClient
from turbine_kg.documents.catalog import document_id, derivative, verified_source
from turbine_kg.documents.ir import SourceIndex, SourceUnit, parse_references
from turbine_kg.documents.parser import DocumentParser
from turbine_kg.evidence.context import ContextAssembler, make_evidence, table_evidence, text_evidence
from turbine_kg.knowledge.extract import (extract, semantic_review_views, _statement_references,
    statement_reference_edges, stored_statement_mentions, merge_statement_mentions)
from turbine_kg.knowledge.ingest import _table_context_sources, _support_closure, _replace_statements_atomically, _write_transaction, _discard_statements
from turbine_kg.knowledge.scopes import register_scopes, restore_scopes, attach_evidence_scopes, refresh_scope_locations
from turbine_kg.knowledge.alignment import plan_alignment, remap_ids, refresh_bound_locations, reuse_scope_member_keys
from turbine_kg.knowledge.store import unpack
from turbine_kg.knowledge.incremental import evidence_logical_key, content_fingerprint
from turbine_kg.runtime import Snapshot, file_hash, fingerprint, identity, json_text
from turbine_kg.settings import PROJECT_ROOT


def scope_pages(scope):
    bounds=scope.get('physical_pages')
    if scope.get('scope')!='section' or not isinstance(bounds,list) or len(bounds)!=2 or any(type(p) is not int or p<1 for p in bounds) or bounds[1]<bounds[0]:
        raise ValueError('章节候选必须声明有效physical_pages起止范围')
    return list(range(bounds[0],bounds[1]+1))


def _within(source,pages):
    return source.get('locator',{}).get('physical_page') in pages


def _restrict_source(source,pages):
    attrs=source.attributes
    attrs['section_sources']=[deepcopy(row) for row in attrs.get('section_sources',[]) if _within(row,pages)]
    for row in attrs['section_sources']:
        row.get('attributes',{}).pop('section_sources',None)
    return source


def _rows(store,label,document_id):
    return [unpack(row['node']) for row in store.query(f'MATCH (n:TK:{label} {{document_id:$id}}) RETURN properties(n) AS node',{'id':document_id})]


def refresh_document_pages(store, document_id):
    """Summarize actual source and semantic completion without parsing again."""
    records=store.get([document_id])
    if not records:raise ValueError('资料节点不存在')
    document=records[0]
    requested=sorted(set(document.get('requested_pages', [])))
    pages={row['physical_page']:row for row in _rows(store,'Page',document_id)}
    evidence=_rows(store,'Evidence',document_id)
    processed=[]; semantic=[]; finished=set()
    for number in requested:
        page=pages.get(number, {})
        if page.get('status') not in ('parsed','blank') or page.get('mapping',{}).get('layout_status')=='fallback_unverified':
            continue
        processed.append(number)
        items=[row for row in evidence if row.get('physical_page')==number]
        if page.get('status')=='blank' and not items:
            finished.add(number)
        elif items and all(row.get('semantic_coverage',{}).get('status') in ('complete','context_only') for row in items):
            semantic.append(number);finished.add(number)
    document.update(processed_pages=processed,semantic_pages=semantic,
                    unprocessed_pages=sorted(set(requested)-finished),
                    status='ready' if requested and set(requested)<=finished else 'processing')
    document['content_hash']=content_fingerprint(document)
    store.put('Document',[document])
    return {key:document[key] for key in ('requested_pages','processed_pages','semantic_pages','unprocessed_pages','status')}


def _inputs(path,store,registry,scope,model):
    if model is None:model=ModelClient(store.settings)
    settings=store.settings;digest=file_hash(path);verified_source(path,settings,digest)
    doc=document_id(path,settings)
    if scope.get('document_id') and scope['document_id']!=doc:raise ValueError('章节配置与实际文档身份不一致')
    ocr=derivative(path,settings)
    implementation={name:file_hash(PROJECT_ROOT/'src/turbine_kg'/name) for name in (
        'documents/parser.py','documents/catalog.py','documents/mapping.py','documents/ir.py','documents/text.py',
        'evidence/context.py','knowledge/extract.py','knowledge/reviews.py','knowledge/store.py','knowledge/sections.py','knowledge/ingest.py','knowledge/scopes.py','ontology/registry.py')}
    configuration=json.loads((PROJECT_ROOT/'config/agent.json').read_text(encoding='utf-8'))
    if configuration['parser'].get('do_ocr') is True or configuration['parser'].get('ocr') is True:raise ValueError('章节处理禁止重复OCR')
    inputs={'original_hash':digest,'ocr_hash':file_hash(ocr) if ocr else None,'annotations_hash':file_hash(PROJECT_ROOT/'config/source_annotations.json'),
            'implementation':implementation,'parser_config':configuration['parser'],'ontology_version':registry.version,
            'scope':{key:scope.get(key) for key in ('scope','section','physical_pages','external_references')},
            'model':settings.llm_model,'model_providers':[{'endpoint':p[0],'model':p[1]} for p in getattr(model,'providers',[])],
            'transport':getattr(model,'policy',{}),'representation':'compact_section_v1'}
    return doc,identity('revision',doc,digest),ocr,inputs


def _evidence_reference_index(evidence,sources,tables,pictures,document_id):
    """Use assembled clause anchors, including detached printed number markers.

    Assembly has already associated those markers with their body. Requiring
    the body SourceText to repeat that number loses otherwise complete clauses.
    Keep SourceIndex disambiguation by section and physical location, and its
    explicit external-document and incomplete-range handling.
    """
    def owned(row):return row.get('document_id') in (None,document_id)
    index=SourceIndex([row for row in sources if owned(row)],
                      [row for row in tables if owned(row)],
                      [row for row in pictures if owned(row)])
    assembled=[]
    for item in evidence:
        if item.get('document_id')!=document_id or item.get('kind')!='text':continue
        context=item.get('context',{});number=context.get('clause_number')
        if not number:continue
        assembled.append({'kind':'clause','number':number,'evidence_ids':[item['id']],
            'source_ids':context.get('source_ids',[]),'resource_ids':[],
            'section':context.get('section',''),'physical_page':context.get('primary_page',item.get('physical_page')),
            'order':min((member.get('order',0) for member in item.get('members',[])),default=0)})
    numbered={row['number'] for row in assembled}
    index.entries=[row for row in index.entries if row['kind']!='clause' or row['number'] not in numbered]+assembled
    return index


def _expand_references(evidence,sources,tables,pictures,pages):
    """Resolve direct structural links without copying target facts into the origin."""
    source_index=_reference_sources(sources,tables,pictures)
    for item in evidence:
        if 'references' in item.get('context',{}):continue
        refs=[]
        for m in item['members']:
            source=source_index.get(m['source_id'],{});text=source.get('text','');bounds=m.get('locator',{}).get('char_range')
            if bounds:text=text[bounds[0]:bounds[1]]
            for ref in parse_references(text):
                offset=bounds[0] if bounds else 0
                refs.append({**ref,'start':ref['start']+offset,'end':ref['end']+offset,'source_id':m['source_id']})
        item.setdefault('context',{})['references']=refs
    indexes={document_id:_evidence_reference_index(evidence,sources,tables,pictures,document_id)
             for document_id in {item.get('document_id') for item in evidence}}
    by_source={};by_resource={};by_id={item['id']:item for item in evidence}
    for item in deepcopy(evidence):
        for key in item.get('context',{}).get('source_ids',[]):by_source.setdefault(key,[]).append(item)
        context=item.get('context',{})
        for key in ('table_id','picture_id'):
            if context.get(key):by_resource.setdefault(context[key],[]).append(item)
    for item in evidence:
        origin=item['context'];seen=set();added=set();resolutions=[]
        for ref in origin.get('references',[]):
            token=(ref['kind'],ref['number'],ref.get('end_number'),ref.get('source_id'),ref.get('start'),ref.get('end'))
            if token in seen:continue
            seen.add(token)
            resolved=indexes[item.get('document_id')].resolve(ref,{**origin,'physical_page':origin.get('primary_page')})
            targets={}
            for current in resolved.get('candidates',[]) if resolved['status']=='resolved' else []:
                if current.get('evidence_ids'):
                    targets.update({key:by_id[key] for key in current['evidence_ids']})
                    continue
                for key in current['source_ids']:
                    for target in by_source.get(key,[]):
                        context=target.get('context',{})
                        # One parsed text block may hold multiple independent clauses.
                        if current.get('kind')=='clause' and context.get('clause_number')!=current['number']:continue
                        if current.get('char_range') and context.get('clause_source_id')==key and context.get('clause_source_range') not in (None,current['char_range']):continue
                        targets[target['id']]=target
            targets.update({row['id']:row for key in resolved['resource_ids'] for row in by_resource.get(key,[])})
            targets={key:row for key,row in targets.items() if row.get('document_id')==item.get('document_id')}
            state=resolved['status']
            if ref['kind']=='clause' and ref.get('end_number') and state=='resolved':
                first=ref['number'].split('.')
                last=ref['end_number'].split('.')
                actual={row.get('context',{}).get('clause_number') for row in targets.values()}
                expected={'.'.join([*first[:-1],str(number)]) for number in range(int(first[-1]),int(last[-1])+1)}
                if not expected<=actual:state='missing_range_target'
            own_resource=origin.get('table_id') if ref['kind']=='table' else origin.get('picture_id') if ref['kind']=='figure' else None
            if own_resource:
                targets={key:row for key,row in targets.items() if row.get('context',{}).get('table_id' if ref['kind']=='table' else 'picture_id')!=own_resource}
                if own_resource in resolved['resource_ids'] and not targets:state='self_reference'
            if state=='missing':state='deferred_outside_scope'
            targets.pop(item['id'],None)
            source_keys=[]
            for m in item['members']:
                if m['source_id']!=ref.get('source_id'):continue
                bounds=m.get('locator',{}).get('char_range')
                if bounds and not bounds[0]<=ref.get('start',bounds[0])<bounds[1]:continue
                source_keys.append(m['member_key'])
            resolutions.append({**ref,'status':state,'target_evidence_ids':sorted(targets),'source_member_keys':source_keys})
            added.update(targets)
        item['context']['reference_resolution']=resolutions
        item['context']['reference_evidence_ids']=sorted(added)
        item['context']['scope_physical_pages']=pages


def reference_edges(evidence):
    """Native Evidence-to-Evidence links with exact origin members and no new nodes."""
    result=[]
    for item in evidence:
        for ref in item.get('context',{}).get('reference_resolution',[]):
            if ref.get('status')!='resolved' or not ref.get('source_member_keys'):continue
            for target in ref.get('target_evidence_ids',[]):
                result.append({'source':item['id'],'target':target,'key':identity('reference',item['document_id'],item['id'],target,ref),
                    'data':{'document_id':item['document_id'],'reference_kind':ref['kind'],'number':ref['number'],
                            'end_number':ref.get('end_number'),'source_evidence_id':item['id'],
                            'source_member_keys':ref['source_member_keys'],'source_id':ref.get('source_id'),
                            'char_range':[ref.get('start'),ref.get('end')],'quote':ref.get('quote'),'inherit_values':False}})
    return result


def _refresh_statement_references(store,document_id,evidence_ids=None):
    rows=store.query('MATCH (s:TK:Statement {document_id:$document}) '
        'WHERE $all OR EXISTS { MATCH (s)-[:SUPPORTS]->(e:TK:Evidence) WHERE e.id IN $evidence } '
        'RETURN properties(s) AS node',
        {'document':document_id,'all':evidence_ids is None,'evidence':list(evidence_ids or [])})
    statements=[unpack(row['node']) for row in rows]
    if not statements:return {'statements_updated':0,'reference_edges':0}
    ids={support['evidence_id'] for item in statements for support in item['supports']}
    supplied={row['id']:row for row in store.get(sorted(ids),(document_id,))}
    if ids-set(supplied):raise ValueError('陈述引用刷新缺少当前来源Evidence')
    from turbine_kg.knowledge.incremental import content_fingerprint
    changed=[]
    for item in statements:
        refs=_statement_references(item,supplied)
        if item.get('references',[])!=refs:
            item['references']=refs;item['content_hash']=content_fingerprint(item);changed.append(item)
    edges=statement_reference_edges(statements,document_id)
    if changed:store.put('Statement',changed,replace=True)
    store.query('MATCH (s:TK:Statement)-[r:REFERENCES]->(:TK) '
        'WHERE s.document_id=$document AND s.id IN $ids DELETE r',
        {'document':document_id,'ids':[item['id'] for item in statements]})
    if edges:store.edges('REFERENCES',edges)
    return {'statements_updated':len(changed),'reference_edges':len(edges)}


def refresh_statement_references(store,document_id,evidence_ids=None):
    """Refresh derived references only; preserve text, supports, and entities."""
    with _write_transaction(store) as transaction:
        return _refresh_statement_references(transaction,document_id,evidence_ids)


def refresh_statement_mentions(store,document_id,registry,statement_ids=None):
    """Coalesce parallel class cues without changing their supported facts."""
    with _write_transaction(store) as transaction:
        statements=[unpack(row['node']) for row in transaction.query(
            'MATCH (s:TK:Statement {document_id:$document}) WHERE $all OR s.id IN $ids RETURN properties(s) AS node',
            {'document':document_id,'all':statement_ids is None,'ids':list(statement_ids or [])})]
        ids=[row['id'] for row in statements]
        edges=merge_statement_mentions(stored_statement_mentions(transaction,document_id,ids),
                                       statements,document_id,registry.version)
        if edges:transaction.edges('MENTIONS',edges)
        transaction.query('MATCH (c:TK:OntologyClass)-[r:MENTIONS]->(s:TK:Statement) '
            'WHERE s.document_id=$document AND s.id IN $ids '
            'AND NOT any(row IN $keep WHERE c.id=row.source AND s.id=row.target AND coalesce(r.key,"")=row.key) DELETE r',
            {'document':document_id,'ids':ids,'keep':edges})
    return {'statements':len(ids),'mention_edges':len(edges)}


def _reference_sources(sources,tables,pictures):
    result={source['id']:source for source in [*sources,*pictures]}
    for table in tables:
        for source in [*table.get('source_members',[]),*table.get('cells',[]),*table.get('notes',[])]:
            result[source['id']]=source
    return result


def _preparation_fingerprint(inputs):
    # Source parsing depends on source/layout inputs, not model choice or the
    # extraction prompt. Changing the latter must not invalidate good Evidence.
    source_files=('documents/parser.py','documents/catalog.py','documents/mapping.py',
                  'documents/ir.py','documents/text.py','evidence/context.py','knowledge/sections.py')
    return fingerprint({key:inputs.get(key) for key in ('original_hash','ocr_hash','annotations_hash','parser_config')} |
                       {'implementation':{key:inputs.get('implementation',{}).get(key) for key in source_files}})


def _local_member_fingerprint(view):
    """Compare native source content independently of attached scope context."""
    return fingerprint([(member['source_id'],member['locator'],member['role'],member['quote'])
                        for member in view['members'] if member['role']!='section_context'])


def _parsed_sources(parsed,document_id,revision_id):
    """Include text and embedded table members needed for pure alignment."""
    indexed={}
    def include(row):
        row={**deepcopy(row),'document_id':document_id,'revision_id':revision_id}
        indexed[row['id']]=row
        for parent in row.get('attributes',{}).get('section_sources',[]):include(parent)
    for page in parsed:
        for unit in [*page.sources,*[cell for table in page.tables for cell in table.cells],
                     *[note for table in page.tables for note in table.notes],*page.pictures]:
            row=asdict(unit)
            if isinstance(unit,SourceUnit):row.update(unit.attributes)
            else:row['kind']='picture'
            include(row)
    return list(indexed.values())


def _alignment_evidence(parsed,sources,document_id,pages):
    """Construct the same complete row groups before any source write."""
    indexed={row['id']:row for row in sources}
    memory=SimpleNamespace(get=lambda ids,documents=None:[indexed[key] for key in ids if key in indexed])
    evidence=text_evidence([asdict(unit) for page in parsed for unit in page.sources],document_id)
    for page in parsed:
        for table in page.tables:
            context=_table_context_sources(table,memory,document_id,pages,source_rows=sources)
            cells=[indexed[unit.id] for unit in [*table.cells,*table.notes]]
            evidence.extend(table_evidence(asdict(table),cells,document_id,context))
    return evidence


def _refresh_statement_locations(store,document_id,sources,locations,evidence):
    from turbine_kg.knowledge.incremental import content_fingerprint, statement_logical_key
    indexed={source['id']:source for source in sources};changed=[]
    for row in store.query('MATCH (s:TK:Statement {document_id:$document}) RETURN properties(s) AS node',{'document':document_id}):
        old=unpack(row['node']);current=refresh_bound_locations(old,indexed,locations)
        if current!=old:
            current['logical_key']=statement_logical_key(current,{item['id']:item['logical_key'] for item in evidence})
            current['content_hash']=content_fingerprint(current);changed.append(current)
    if changed:store.put('Statement',changed,replace=True)


class _PreparedSources:
    """Record the complete parsed source set inside the existing transaction."""
    def __init__(self,store):
        self.store=store; self.nodes={}; self.relationships={}
        self.settings=store.settings

    def put(self,label,rows,**kwargs):
        self.nodes.setdefault(label,{}).update({row['id']:deepcopy(row) for row in rows})
        existing={row['id']:row for row in self.store.get([row['id'] for row in rows])}
        from turbine_kg.knowledge.incremental import content_fingerprint
        changed=[row for row in rows if row['id'] not in existing or
                 content_fingerprint(existing[row['id']])!=content_fingerprint(row)]
        return self.store.put(label,changed,**kwargs)

    def edges(self,kind,rows):
        for row in rows:
            key=row.get('key') or identity('edge',kind,row['source'],row['target'])
            self.relationships[(kind,key)]={**deepcopy(row),'kind':kind,'key':key}
        return self.store.edges(kind,rows)

    def query(self,text,parameters=None,budget=None):
        if text=='MATCH (p:TK:Page {id:$id}) SET p.status=$status':
            row=self.nodes['Page'][parameters['id']]
            row['status']=parameters['status']
            self.store.put('Page',[row])
            return []
        return self.store.query(text,parameters,budget)

    def __getattr__(self,name):return getattr(self.store,name)


def prepare_section(path:Path,store,registry,scope:dict,*,model=None):
    """Refresh the prepared source set; unchanged facts do not need extraction."""
    requested=scope_pages(scope);doc,revision,ocr,inputs=_inputs(path,store,registry,scope,model)
    current=store.get([doc]);document=current[0] if current else {}
    signature=_preparation_fingerprint(inputs)
    previous_pages=set(document.get('requested_pages') or [])
    if (document.get('source_prepared') and document.get('preparation_fingerprint')==signature
            and set(requested)<=previous_pages):
        state=refresh_document_pages(store,doc)
        return {'document_id':doc,'revision_id':revision,**state,'requested_pages':requested,'prepared':True}
    # Previously prepared chapters remain part of the current book. Reparse
    # sources locally to resolve cross-page groups; only changed facts are later
    # extracted, and unchanged nodes are skipped by the document diff.
    pages=sorted(previous_pages|set(requested))
    store.initialize()
    parser=DocumentParser(path,doc,derived=ocr)
    try:
        if pages[-1]>parser.page_count:raise ValueError('章节页码超出来源范围')
        parser.heading_index=[s for s in parser.heading_index if s.locator.get('physical_page') in pages]
        parser.scope_sources=[s for s in parser.scope_sources if s.locator.get('physical_page') in pages]
        parsed=[];sources=[];tables=[];pictures=[]
        for number in pages:
            page=parser.page(number)
            if page.status=='excluded_navigation':raise ValueError('章节范围包含目录，请先调整范围')
            if page.mapping.get('layout_status')=='fallback_unverified':
                raise ValueError(f'物理页{number}版面解析未通过，须恢复表格与阅读顺序后再抽取')
            for unit in page.sources+[cell for table in page.tables for cell in table.cells]+[note for table in page.tables for note in table.notes]:_restrict_source(unit,pages)
            for table in page.tables:
                for key in ('related_body_pages','missing_context_pages'):
                    if key in table.locator:table.locator[key]=[p for p in table.locator[key] if p in pages]
            parsed.append(page);sources.extend(asdict(unit) for unit in page.sources)
            tables.extend(asdict(table) for table in page.tables);pictures.extend(asdict(picture) for picture in page.pictures)
        before={item['id']:item for item in _rows(store,'Evidence',doc)}
        snapshot=Snapshot((doc,),registry.version,())
        previous_assembler=ContextAssembler(store)
        # Capture the old original quotes before any SourceText is overwritten.
        # The persisted fingerprint may include derived section_context members.
        previous_views=[previous_assembler.read(key,snapshot) for key in before]
        local_before={view['evidence_id']:_local_member_fingerprint(view) for view in previous_views}
        previous_sources=_rows(store,'SourceText',doc)
        previous_sources.extend(source for table in _rows(store,'Table',doc) for source in table.get('source_members',[]))
        previous_scopes=restore_scopes(store,doc)
        current_sources=_parsed_sources(parsed,doc,revision)
        initial_plan=plan_alignment(_alignment_evidence(parsed,current_sources,doc,pages),current_sources,
                                    previous_views,previous_sources)
        remap_ids(parsed,initial_plan['id_map']);remap_ids(parser.heading_index,initial_plan['id_map'])
        sources=[asdict(unit) for page in parsed for unit in page.sources]
        tables=[asdict(table) for page in parsed for table in page.tables]
        pictures=[asdict(picture) for page in parsed for picture in page.pictures]
        evidence=text_evidence(sources,doc)
        with _write_transaction(store) as transaction:
            transaction.put('Document',[{**document,'id':doc,'title':path.stem,'kind':'document','ontology_version':registry.version}])
            staged=_PreparedSources(transaction)
            staged.put('Revision',[{'id':revision,'document_id':doc,'original_hash':inputs['original_hash'],'version':inputs['original_hash'][:12]}])
            for page in parsed:
                transaction.save_page.__func__(staged,page,doc,revision,compact_tables=True)
            for page in parsed:
                for table in page.tables:
                    contexts=_table_context_sources(table,staged,doc,pages,source_rows=staged.nodes.get('SourceText',{}).values())
                    contexts=[row for row in contexts if _within(row,pages)]
                    evidence.extend(table_evidence(asdict(table),staged.table_sources(table.id,(doc,)),doc,contexts))
                for picture in page.pictures:
                    selected=[(asdict(picture),'image')]
                    selected.extend((unit,'caption' if unit['kind']=='caption' else 'figure_reference') for unit in sources if any(link.get('source_id')==unit['id'] for link in picture.links))
                    evidence.append(make_evidence('image',selected,doc,context={'picture_id':picture.id,'figure_title':picture.title,'section':picture.section,
                        'primary_page':page.physical_page,'physical_pages':[page.physical_page],'visual_status':'pending_read'}))
            current_sources=_parsed_sources(parsed,doc,revision)
            aligned=plan_alignment(evidence,current_sources,previous_views,previous_sources)
            evidence=remap_ids(evidence,aligned['id_map'])
            _expand_references(evidence,sources,tables,pictures,pages)
            for item in evidence:
                primary=item['context'].get('primary_page') or min(m['locator']['physical_page'] for m in item['members'])
                item.update(document_id=doc,revision_id=revision,physical_page=primary,semantic_coverage={'status':'pending','reason':'未进行工程抽取'})
                item['logical_key']=evidence_logical_key(item)
                if any(m['locator'].get('physical_page') not in pages for m in item['members']):raise ValueError('证据含范围外来源')
            staged.put('Evidence',evidence)
            assembler=ContextAssembler(staged)
            changed=set(before)-{item['id'] for item in evidence}
            for index,item in enumerate(evidence):
                view=assembler.read(item['id'],snapshot)
                item['text']=view['text'];item['source_view_fingerprint']=view['member_fingerprint']
                item['member_fingerprint']=fingerprint([(m['source_id'],m['locator'],m['role'],m['quote']) for m in view['members']])
                old=before.get(item['id'])
                if old and (item['id'] in aligned['unchanged_evidence_ids'] or local_before[item['id']]==_local_member_fingerprint(view)):
                    evidence[index]={**old,**item,'semantic_coverage':old.get('semantic_coverage',item['semantic_coverage'])}
                elif old:changed.add(item['id'])
            if changed:
                affected=_support_closure(staged,doc,changed)
                _discard_statements(staged,doc,affected)
                for item in evidence:
                    if item['id'] in affected:item['semantic_coverage']={'status':'pending','reason':'来源内容变更，等待抽取'}
            from turbine_kg.knowledge.scopes import native_scope_declarations
            refresh_scope_locations(previous_scopes,parser,staged,doc,staged.nodes.get('SourceText',{}).values())
            views=[assembler.read(item['id'],snapshot) for item in evidence]
            register_scopes(native_scope_declarations(views),parser,staged,doc)
            source_ids=set(staged.nodes.get('SourceText',{}))
            valid_scopes=[row for row in restore_scopes(staged,doc)
                if all(source['id'] in source_ids for source in row.get('sources',[]))
                and (not row.get('heading_source_id') or row['heading_source_id'] in source_ids)]
            valid_scope_ids={row['id'] for row in valid_scopes}
            invalidated={item['id'] for item in evidence if
                set(before.get(item['id'],{}).get('context',{}).get('scope_ids',[]))-valid_scope_ids}
            if invalidated:
                affected=_support_closure(staged,doc,invalidated)
                _discard_statements(staged,doc,affected)
                for item in evidence:
                    if item['id'] in affected:item['semantic_coverage']={'status':'pending','reason':'原适用范围已失效，等待抽取'}
            attach_evidence_scopes(evidence,staged,valid_scopes,doc)
            aligned['locations'].update(reuse_scope_member_keys(evidence,previous_views,staged.nodes.get('SourceText',{}).values()))
            staged.put('Evidence',evidence)
            for item in evidence:
                view=assembler.read(item['id'],snapshot)
                item['text']=view['text'];item['source_view_fingerprint']=view['member_fingerprint']
                item['member_fingerprint']=fingerprint([(m['source_id'],m['locator'],m['role'],m['quote']) for m in view['members']])
            for page in staged.nodes.get('Page',{}).values():
                items=[item for item in evidence if item['physical_page']==page['physical_page'] and item['kind']!='image']
                states=[item.get('semantic_coverage',{}).get('status') for item in items]
                page['semantic_status']=('complete' if states and all(state in ('complete','context_only') for state in states)
                    else 'not_required' if not states and page.get('status')=='blank' else 'pending')
            staged.put('Evidence',evidence);staged.edges('REFERENCES',reference_edges(evidence))
            # Save-page writes are staged in this transaction. The full set for
            # each supplied label also removes deleted rows/cells/source blocks.
            labels=('Revision','Page','SourceText','Table','Row','Column','Cell','TableNote','Picture','Evidence')
            nodes={label:list(staged.nodes.get(label,{}).values()) for label in labels}
            nodes['ScopeDeclaration']=valid_scopes
            transaction._sync_document(doc,nodes,None,delete_missing=True)
            if before and aligned['unchanged_evidence_ids'] and any(json_text(location)!=key[1] for key,location in aligned['locations'].items()):
                _refresh_statement_locations(transaction,doc,staged.nodes.get('SourceText',{}).values(),aligned['locations'],evidence)
            reference_changes=[item['id'] for item in evidence if
                before.get(item['id'],{}).get('context',{}).get('reference_resolution',[])!=
                item.get('context',{}).get('reference_resolution',[])]
            if reference_changes:_refresh_statement_references(transaction,doc,reference_changes)
            # Replace only source structural edges. Statement/support/business
            # edges are preserved unless their source changed above.
            kinds=('HAS_REVISION','HAS_PAGE','CONTAINS','HAS_ROW','HAS_COLUMN','IN_ROW','IN_COLUMN','HAS_NOTE','HAS_PICTURE')
            staged.edges('HAS_REVISION',[{'source':doc,'target':revision}])
            edge_rows=list(staged.relationships.values())
            keep=[{'kind':row['kind'],'key':row['key']} for row in edge_rows]
            transaction.query('MATCH (a:TK)-[r]->(b:TK) WHERE r.document_id=$document '
                'AND (type(r) IN $types OR (type(r)="REFERENCES" AND a:Evidence)) '
                'AND NOT any(row IN $keep WHERE type(r)=row.kind AND coalesce(r.key,"")=row.key) DELETE r',
                {'document':doc,'types':list(kinds),'keep':keep})
            transaction.put('Document',[{**document,'id':doc,'title':path.stem,'original_path':str(path),'kind':'document',
                'ontology_version':registry.version,'source_fingerprint':inputs['ocr_hash'],'preparation_fingerprint':signature,
                'source_prepared':True,'evidence_ids':[item['id'] for item in evidence],'requested_pages':pages,
                'status':'ready' if evidence and all(item['kind']=='image' or item.get('semantic_coverage',{}).get('status') in ('complete','context_only') for item in evidence) else 'processing',
                'evidence_count':len(evidence),'table_count':len(tables),'picture_count':len(pictures)}],replace=True)
        state=refresh_document_pages(store,doc)
        return {'document_id':doc,'revision_id':revision,**state,'requested_pages':requested,'prepared':True,
                'evidence':len(evidence),'tables':len(tables),'pictures':len(pictures)}
    finally:parser.close()


def _batches(views):
    result=[]
    for view in views:
        source_size=sum(len(member.get('quote','')) for member in view['members'])
        if source_size>20000:raise ValueError('完整证据组超过单批抽取预算，不能截断来源')
        # One complete source group is the semantic replacement unit. Mixing
        # independent groups enlarges generated facts and validation output even
        # when the original quotes are short. Parallelize groups, never split
        # native numbered obligations or their shared parent and scope context.
        result.append([view])
    return result


def _offline_settings():
    return json.loads((PROJECT_ROOT/'config/agent.json').read_text(encoding='utf-8')).get('offline',{})


def _registered_scope_views(views,scopes,document_id):
    """Expose actual registered scope identity for reuse, never a new declaration."""
    for view in views:
        ids=set(view.get('context',{}).get('scope_ids',[]))
        view['context']['registered_scopes']=[{
            'id':scope['id'],'document_id':scope['document_id'],'scope':scope['scope'],
            'heading_source_id':scope.get('heading_source_id'),
            'condition_text':scope['condition_text'],
            'source_ids':[source['id'] for source in scope.get('sources',[])],
            'source_bindings':[{'source_id':source['id'],'locator':deepcopy(source['locator'])} for source in scope.get('sources',[])]}
            for scope in scopes if scope.get('document_id')==document_id and scope['id'] in ids]
    return views


def _verified_batch(batch, outcome, error):
    """Only complete source groups with valid source bindings can retire old facts."""
    if error or outcome is None:return False
    _,coverage=outcome
    ids={view['evidence_id'] for view in batch}
    details=coverage.get('rejected_details',[])
    if any(ids & set(detail.get('evidence_ids',[])) for detail in details):return False
    by_evidence=coverage.get('rejected_by_evidence')
    if isinstance(by_evidence,dict):
        if any(by_evidence.get(eid) for eid in ids):return False
    elif coverage.get('rejected'):return False
    statuses=coverage.get('evidence',{})
    return all(statuses.get(view['evidence_id'],{}).get('status') in ('complete','context_only') for view in batch)


def _component_statements(outcomes, component):
    """Combine independent extraction batches without colliding local statement IDs."""
    combined=[]
    for batch_index,(batch,outcome,error) in enumerate(outcomes):
        if not _verified_batch(batch,outcome,error):continue
        if not {view['evidence_id'] for view in batch}<=component:continue
        for original in outcome[0]:
            statement=deepcopy(original)
            statement['extraction_id']=f"batch{batch_index}:{statement['extraction_id']}"
            for relation in statement.get('semantic_relations',[]):
                for key in ('source_ref','target_ref'):
                    ref=relation.get(key)
                    if isinstance(ref,dict) and 'statement_id' in ref:
                        ref['statement_id']=f"batch{batch_index}:{ref['statement_id']}"
            combined.append(statement)
    return combined


def process_section(path:Path,store,model,registry,scope:dict,*,work_pages=None,evidence_ids=None,retry_semantics=False,workers=None):
    """Parallel model extraction, followed by serialized persistence."""
    result=prepare_section(path,store,registry,scope,model=model)
    document_id=result['document_id'];pages=result['requested_pages']
    # A configured section is processed as a whole by default.  A page list
    # is reserved for an explicit, bounded recheck (for example after an OCR
    # repair); configuration must not silently downgrade a chapter to samples.
    work=list(pages) if work_pages is None else list(work_pages)
    if not work or any(type(p) is not int or p not in pages for p in work):raise ValueError('处理页面必须属于当前章节范围')
    evidence=_rows(store,'Evidence',document_id);indexed={item['id']:item for item in evidence}
    requested_ids=set(evidence_ids or ())
    if evidence_ids is not None and (not requested_ids or len(requested_ids)!=len(evidence_ids)):
        raise ValueError('定向复验须提供非空且不重复的Evidence ID')
    if requested_ids and (requested_ids-set(indexed) or any(indexed[eid].get('physical_page') not in work or indexed[eid]['kind']=='image' for eid in requested_ids & set(indexed))):
        raise ValueError('定向复验的Evidence必须属于本当前资料、指定页面且为可抽取正文')
    selected=[item for item in evidence if item.get('physical_page') in work and item['kind']!='image' and
              (item['id'] in requested_ids if evidence_ids is not None else
               retry_semantics or item.get('semantic_coverage',{}).get('status') not in ('complete','context_only'))]
    selected_ids={item['id'] for item in selected}
    # Validate the exact replacement closure before making any provider calls.
    closure=_support_closure(store,document_id,selected_ids) if selected_ids else set()
    if closure-selected_ids:raise ValueError('复验会影响共用陈述的其他证据，请明确选择完整支持组件；本次未扩大抽取范围')
    assembler=ContextAssembler(store);snapshot=Snapshot((document_id,),registry.version,())
    views=_registered_scope_views([assembler.read(item['id'],snapshot) for item in selected],restore_scopes(store,document_id),document_id)
    views=semantic_review_views(views,indexed)
    batches=_batches(views)
    defaults=_offline_settings()
    worker_count=max(1,min(int(workers or defaults.get('semantic_workers',1)),8))
    semantic_seconds=max(1,int(defaults.get('semantic_seconds',180)))
    retry_count=int(defaults.get('extraction_retries',2))
    outcomes=[None]*len(batches)
    def extraction_job(batch):
        if isinstance(model,ModelClient):
            return extract(batch,ModelClient(store.settings),registry,repair=True,seconds=semantic_seconds,max_retries=retry_count)
        return extract(batch,model,registry,repair=True,seconds=semantic_seconds,max_retries=retry_count)
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        pending={pool.submit(extraction_job,batch):(index,batch) for index,batch in enumerate(batches)}
        for completed,future in enumerate(as_completed(pending),1):
            index,batch=pending[future]
            try:outcomes[index]=(batch,future.result(),None)
            except Exception as error:outcomes[index]=(batch,None,type(error).__name__+': '+str(error))
            if completed==1 or completed%5==0 or completed==len(batches):
                print(f'语义证据组已处理 {completed}/{len(batches)}',flush=True)
    headings=_rows(store,'SourceText',document_id)
    heading_index=[SourceUnit(**{key:row[key] for key in SourceUnit.__dataclass_fields__ if key in row}) for row in headings if row.get('kind')=='heading']
    heading_index.sort(key=lambda unit:(unit.locator['physical_page'],unit.order))
    parser_view=SimpleNamespace(heading_index=heading_index,page_count=max(pages))
    declarations=[declaration for batch,outcome,error in outcomes if _verified_batch(batch,outcome,error)
                  for declaration in outcome[1].get('source_scopes',[])]
    discovered=register_scopes(declarations,parser_view,store,document_id)
    changed=attach_evidence_scopes(evidence,store,restore_scopes(store,document_id),document_id)
    if changed:
        store.put('Evidence',[indexed[key] for key in changed])
        # Scope attachment changes the interpretation. Recheck only already selected affected batches once.
        revised=[]
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            jobs=[]
            for batch,outcome,error in outcomes:
                if not error and any(view['evidence_id'] in changed for view in batch):
                    refreshed=_registered_scope_views([ContextAssembler(store).read(view['evidence_id'],snapshot) for view in batch],restore_scopes(store,document_id),document_id)
                    refreshed=semantic_review_views(refreshed,indexed)
                    jobs.append((refreshed,pool.submit(extract,refreshed,ModelClient(store.settings) if isinstance(model,ModelClient) else model,registry,repair=True,seconds=semantic_seconds,max_retries=retry_count)))
                else:revised.append((batch,outcome,error))
            for batch,future in jobs:
                try:revised.append((batch,future.result(),None))
                except Exception as error:revised.append((batch,None,type(error).__name__+': '+str(error)))
        outcomes=revised
        for key in changed-selected_ids:
            indexed[key]['semantic_coverage']={'status':'pending','reason':'明确范围条件更新，等待选页抽取'}
    total=0;issues=[];replacement_errors={}
    eligible={view['evidence_id'] for batch,outcome,error in outcomes if _verified_batch(batch,outcome,error) for view in batch}
    remaining=set(eligible)
    while remaining:
        component=_support_closure(store,document_id,{min(remaining)})
        remaining-=component
        if component-eligible:
            replacement_errors.update({eid:'抽取失败涉及共享支持组件，保留旧事实并等待完整组件复验'
                                       for eid in component & eligible})
            continue
        statements=_component_statements(outcomes,component)
        try:
            graph=registry.statement_projection([{**statement,'id':f'validation-{offset}'} for offset,statement in enumerate(statements)])
            if not registry.validate_graph(graph)[0]:raise ValueError('内嵌本体表示未通过SHACL')
            total+=_replace_statements_atomically(store,document_id,sorted(component),statements,registry)
        except Exception as failure:
            replacement_errors.update({eid:type(failure).__name__+': '+str(failure) for eid in component})
    for batch,outcome,error in outcomes:
        ids=[view['evidence_id'] for view in batch]
        error=error or next((replacement_errors[eid] for eid in ids if eid in replacement_errors),None)
        for eid in ids:
            item=indexed[eid]
            item['semantic_coverage']={'status':'incomplete','reason':error} if error else outcome[1].get('evidence',{}).get(eid,{'status':'incomplete','reason':'抽取覆盖状态缺失'})
            if not error and not _verified_batch(batch,outcome,error) and item['semantic_coverage'].get('status') in ('complete','context_only'):
                item['semantic_coverage']={'status':'incomplete','reason':'抽取结构或来源覆盖未通过，保留旧事实'}
            coverage=outcome[1] if outcome else {}
            item.pop('extraction_attempts',None)
            item.pop('extraction_attempt_count',None)
            per_evidence=coverage.get('rejected_by_evidence') if not error else None
            item['semantic_issues']=[error] if error else per_evidence.get(eid,[]) if isinstance(per_evidence,dict) else coverage.get('rejected',[])
            if not error and not _verified_batch(batch,outcome,error) and not item['semantic_issues']:
                item['semantic_issues']=[item['semantic_coverage'].get('reason') or '抽取结构或来源覆盖未通过']
            if item['semantic_issues'] and item['semantic_coverage'].get('status')=='complete':item['semantic_coverage']['status']='incomplete'
            issues.extend(item['semantic_issues'])
    # A selected-page run must not write its stale snapshot of other pages.
    # Different pages can finish model work concurrently in the same document.
    store.put('Evidence',[indexed[key] for key in sorted(selected_ids | changed)],replace=True)
    evidence=_rows(store,'Evidence',document_id)
    page_index={row['physical_page']:row for row in _rows(store,'Page',document_id)}
    for number in pages:
        page_evidence=[item for item in evidence if item['physical_page']==number and item['kind']!='image']
        states=[item.get('semantic_coverage',{}).get('status') for item in page_evidence]
        semantic='complete' if states and all(state in ('complete','context_only') for state in states) else 'incomplete' if any(state=='incomplete' for state in states) else 'pending'
        page_record=page_index.get(number)
        if not states and page_record and page_record.get('status')=='blank':semantic='not_required'
        store.query('MATCH (p:TK:Page {document_id:$document,physical_page:$page}) SET p.semantic_status=$status',
                    {'document':document_id,'page':number,'status':semantic})
    ready=bool(evidence) and all(item['kind']=='image' or item.get('semantic_coverage',{}).get('status') in ('complete','context_only') for item in evidence)
    document_records=store.get([document_id])
    if not document_records:raise ValueError('章节资料节点缺失，不能更新抽取状态')
    store.put('Document',[{**document_records[0],'status':'ready' if ready else 'processing',
        'processed_evidence':sum(item.get('semantic_coverage',{}).get('status') in ('complete','context_only') for item in evidence),
        'incomplete_evidence':sum(item.get('semantic_coverage',{}).get('status')=='incomplete' for item in evidence)}])
    state=refresh_document_pages(store,document_id)
    return {**result,**state,'work_pages':sorted(set(work)),'extracted_evidence':len(selected),
            'batches':len(batches),'statements_written':total,'source_scopes':len(discovered),'issues':list(dict.fromkeys(issues)),
            'remaining_evidence':sum(item.get('semantic_coverage',{}).get('status') not in ('complete','context_only') for item in evidence if item['kind']!='image')}
