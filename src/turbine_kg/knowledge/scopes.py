"""Inherit only independently verified scope declarations with real source members."""
from copy import deepcopy
from dataclasses import asdict
import re
from unicodedata import normalize
from turbine_kg.runtime import identity
from turbine_kg.knowledge.store import unpack


def restore_scopes(store, document_id):
    rows=store.query('MATCH (s:TK:ScopeDeclaration {document_id:$id}) RETURN properties(s) AS node',{'id':document_id})
    declarations=[unpack(row['node']) for row in rows]
    source_ids=list(dict.fromkeys(source['id'] for scope in declarations for source in scope.get('sources',[])))
    current={source['id']:source for source in store.get(source_ids,(document_id,))} if source_ids else {}
    return [scope for scope in declarations if scope.get('sources') and all(
        source['id'] in current and source.get('text')==current[source['id']].get('text')
        and source.get('locator')==current[source['id']].get('locator')
        for source in scope['sources'])]


def native_scope_declarations(views):
    """Recognize explicit source declarations; never infer scope from a title."""
    declarations=[]
    for view in views:
        headings=[(member,_heading_structure({'id':member['source_id'],'kind':'heading',
                     'text':member.get('quote',''),'locator':member.get('locator',{}),
                     'attributes':member.get('attributes',{})}))
                  for member in view.get('members',[]) if member.get('source_kind')=='heading']
        for member in view.get('members',[]):
            if member.get('role')!='content':continue
            text=normalize('NFKC',member.get('quote',''))
            match=re.match(r'^\s*(?:\d+(?:\s*\.\s*\d+)*\s*)?本(章|节|文件|标准|规范)(?:仅)?适用于',
                           ''.join(text.split()))
            if not match:continue
            scope='section' if match[1] in ('章','节') else 'document'
            candidates=[(m,h) for m,h in headings if h and
                        (h['level']==1 if match[1]=='章' else h['level']>1)]
            if scope=='section' and not candidates:continue
            heading=max(candidates,key=lambda pair:pair[1]['level'])[0] if scope=='section' else None
            declarations.append({'evidence_id':view['evidence_id'],
                'member_keys':[member['member_key']],'scope':scope,
                'heading_source_id':heading['source_id'] if heading else None,
                'condition_text':member['quote'],
                'source_members':[{key:deepcopy(member[key]) for key in
                                  ('member_key','source_id','locator','quote','text_origin') if key in member}]})
    return declarations


def _heading_record(unit):
    return asdict(unit) if hasattr(unit,'__dataclass_fields__') else deepcopy(unit)


def _heading_structure(unit):
    row=_heading_record(unit);attrs=row.get('attributes',{})
    if row.get('kind')!='heading' or attrs.get('source_role') in ('header','footer','running_header','page_number'):
        return None
    if attrs.get('heading_level')==0 or attrs.get('hierarchy') in ('none','unstructured'):
        return None
    if attrs.get('heading_level') is None and attrs.get('hierarchy')=='unknown':
        return None
    text=normalize('NFKC',row.get('text','')).strip()
    number=re.match(r'^(\d+(?:\s*[.\-]\s*\d+)*)(?=\s|[\u4e00-\u9fff]|$)',text)
    chapter=re.match(r'^第[一二三四五六七八九十百千\d]+[章节篇](?=\s|[\u4e00-\u9fff]|$)',text)
    if number:
        # Printed numbering overrides inconsistent layout-model heading levels.
        parts=tuple(int(part) for part in re.split(r'\s*[.\-]\s*',number.group(1)))
        return {'level':len(parts),'number':parts,'title':''.join(text.split()),'record':row}
    if chapter:return {'level':1,'number':chapter.group(),'title':''.join(text.split()),'record':row}
    return None  # Unnumbered layout headings cannot terminate a numbered scope.


def section_bounds(heading,heading_index,page_count):
    """Infer bounds only from real heading numbering and position; keep the anchor ID."""
    anchor=_heading_structure(heading)
    if anchor is None:return None
    first=anchor['record'].get('locator',{}).get('physical_page')
    if type(first) is not int or type(page_count) is not int or not 1<=first<=page_count:return None
    groups={}
    for unit in [*heading_index,heading]:
        structural=_heading_structure(unit)
        if structural is None:continue
        row=structural['record'];page=row.get('locator',{}).get('physical_page')
        if type(page) is not int or not 1<=page<=page_count:continue
        groups.setdefault((page,structural['title']),[]).append(structural)
    def position(structural):
        row=structural['record'];locator=row.get('locator',{});box=locator.get('bbox')
        vertical=box[1] if isinstance(box,list) and len(box)>=2 and isinstance(box[1],(int,float)) else row.get('order',0)
        return locator['physical_page'],vertical
    canonical=[min(group,key=position) for group in groups.values()]
    current=next(row for row in canonical if row['record']['locator']['physical_page']==first and row['title']==anchor['title'])
    current_position=position(current)
    following=[]
    for row in canonical:
        if position(row)<=current_position or row['level']>anchor['level']:continue
        if row['number']==anchor['number'] and row['title']==anchor['title']:continue
        following.append(row)
    last=position(min(following,key=position))[0] if following else page_count
    return [first,last]


def refresh_scope_locations(declarations,parser,store,current_sources):
    """Keep a verified unchanged declaration while refreshing edition locators."""
    indexed={row['id']:row for row in current_sources};result=[]
    for original in declarations:
        previous=original.get('sources',[])
        if not previous or any(source['id'] not in indexed or
            ''.join(source.get('text','').split())!=''.join(indexed[source['id']].get('text','').split())
            for source in previous):continue
        scope=deepcopy(original)
        if scope['scope']=='section':
            heading=indexed.get(scope.get('heading_source_id'))
            bounds=section_bounds(heading,parser.heading_index,parser.page_count) if heading else None
            if bounds is None:continue
        else:bounds=[min(indexed[source['id']]['locator']['physical_page'] for source in previous),parser.page_count]
        scope['physical_page_range']=bounds;scope['sources']=[]
        for source in previous:
            current=deepcopy(indexed[source['id']])
            current['attributes']={**current.get('attributes',{}),'scope_source':True,'applies_to':scope['scope'],
                'physical_page_range':bounds,'scope_heading_id':scope.get('heading_source_id'),'semantic_status':'verified_scope'}
            current['attributes'].pop('section_sources',None);current['attributes'].pop('verified_scopes',None)
            scope['sources'].append(current)
        store.put('SourceText',scope['sources']);store.put('ScopeDeclaration',[scope]);result.append(scope)
    return result


def register_scopes(declarations, parser, store, document_id):
    result=[]
    existing=restore_scopes(store,document_id) if callable(getattr(store,'query',None)) else []
    headings={unit.id:unit for unit in parser.heading_index}
    for declaration in declarations:
        members=declaration.get('source_members',[])
        if not members: continue
        pages=[m['locator'].get('physical_page') for m in members]
        if not all(isinstance(page,int) for page in pages): continue
        first=min(pages); last=parser.page_count; heading=None
        if declaration['scope']=='section':
            heading=headings.get(declaration.get('heading_source_id'))
            if heading is None:
                declared=store.get([declaration.get('heading_source_id')],(document_id,))
                if declared:
                    source=declared[0]
                    matches=[unit for unit in parser.heading_index if unit.locator.get('physical_page')==source.get('locator',{}).get('physical_page') and ''.join(unit.text.split())==''.join(source.get('text','').split())]
                    if matches:
                        # The declaration remains attached to its original source anchor.
                        # Duplicate parser/layout representations only supply structural context.
                        heading=source
            if heading is None: continue  # Unknown section bounds cannot become inheritance.
            bounds=section_bounds(heading,parser.heading_index,parser.page_count)
            if bounds is None:continue
            if '本章' in normalize('NFKC',declaration['condition_text']):
                structure=_heading_structure(heading)
                if structure is None or structure['level']!=1:continue
            first,last=bounds
        heading_record=_heading_record(heading) if heading is not None else None
        sources=store.get(list(dict.fromkeys(m['source_id'] for m in members)),(document_id,))
        if len(sources)!=len({m['source_id'] for m in members}): continue
        if any(source.get('kind') not in ('paragraph','clause','list_item') for source in sources): continue
        scope={'id':identity('scope',document_id,declaration),'document_id':document_id,'scope':declaration['scope'],'heading_source_id':heading_record['id'] if heading_record else None,'declared_heading_source_id':declaration.get('heading_source_id'),'physical_page_range':[first,last],'condition_text':declaration['condition_text'],'sources':[]}
        unchanged=[old for old in existing if old.get('scope')==scope['scope']
            and old.get('heading_source_id')==scope['heading_source_id']
            and ''.join(old.get('condition_text','').split())==''.join(scope['condition_text'].split())
            and {source['id'] for source in old.get('sources',[])}=={source['id'] for source in sources}]
        if len(unchanged)==1:scope['id']=unchanged[0]['id']
        for source in sources:
            context=deepcopy(source)
            context['attributes']={**context.get('attributes',{}),'scope_source':True,'applies_to':declaration['scope'],'physical_page_range':[first,last],'scope_heading_id':scope['heading_source_id'],'semantic_status':'verified_scope'}
            context['attributes'].pop('section_sources',None)
            context['attributes'].pop('verified_scopes',None)
            scope['sources'].append(context)
        store.put('SourceText',scope['sources'])
        store.put('ScopeDeclaration',[scope])
        result.append(scope)
    return result


def attach_evidence_scopes(evidence, store, scopes, document_id):
    """Refresh current-page evidence before accepting its semantic extraction."""
    from turbine_kg.evidence.context import evidence_text, member
    changed=[]
    known_ids={scope['id'] for scope in scopes if isinstance(scope.get('id'),str) and scope['id']}
    for item in evidence:
        if item['kind']=='image': continue
        context=item.setdefault('context',{})
        previous=context.get('scope_ids',[])
        applicable={value for value in previous if isinstance(value,str) and value} - known_ids if isinstance(previous,list) else set()
        sources=store.get([m['source_id'] for m in item['members']],(document_id,))
        heading_ids={s['id'] for s in sources if s.get('kind')=='heading'}
        # Applicability is bounded by the engineering content, never by the page
        # of a chapter title, table header or inherited scope declaration.
        content_roles={'content','value','row_label','note'}
        by_id={source['id']:source for source in sources}
        physical=[m['locator'].get('physical_page') for m in item['members']
                  if m['role'] in content_roles and by_id.get(m['source_id'],{}).get('kind') not in ('heading','caption')]
        selected_scopes=[]
        for scope in scopes:
            if scope.get('document_id',document_id)!=document_id:continue
            first,last=scope['physical_page_range']
            if not physical or not all(isinstance(p,int) and first<=p<=last for p in physical): continue
            if scope['scope']=='section' and scope['heading_source_id'] not in heading_ids: continue
            selected_scopes.append(scope)
            if isinstance(scope.get('id'),str) and scope['id']:applicable.add(scope['id'])
        managed_sources={source['id'] for scope in scopes for source in scope['sources']}
        selected_sources={source['id'] for scope in selected_scopes for source in scope['sources']}
        retained=[entry for entry in item['members'] if not (
            entry['role']=='section_context' and entry['source_id'] in managed_sources-selected_sources)]
        if len(retained)!=len(item['members']):
            item['members']=retained
            changed.append(item['id'])
        for scope in selected_scopes:
            for source in scope['sources']:
                if any(m['source_id']==source['id'] for m in item['members']): continue
                order=max((entry.get('order',index) for index,entry in enumerate(item['members'])),default=-1)+1
                item['members'].append(member(source,'section_context',order))
                changed.append(item['id'])
        scope_ids=sorted(applicable)
        if previous!=scope_ids:
            context['scope_ids']=scope_ids
            changed.append(item['id'])
        if item['id'] in changed:
            by_id.update({source['id']:source for scope in scopes for source in scope['sources']})
            quotes=[]
            for entry in item['members']:
                source=by_id.get(entry['source_id'],{})
                raw=source.get('text','')
                bounds=entry.get('locator',{}).get('char_range')
                quotes.append(raw[bounds[0]:bounds[1]] if bounds else raw)
            item['text']=evidence_text(context,quotes,[entry['role'] for entry in item['members']],
                                       [by_id.get(entry['source_id'],{}).get('kind') for entry in item['members']],
                                       kind=item['kind'])
    return set(changed)
