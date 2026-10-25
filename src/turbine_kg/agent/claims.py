"""Only validated claims are rendered; source quotes remain independent members."""
from copy import deepcopy
from turbine_kg.runtime import identity


class AnswerReferences:
    """Question-local S/E handles; durable IDs and member selection stay in code."""
    def __init__(self):
        self.aliases={}

    def context(self, statements, reads, *, include_evidence=False):
        def alias(kind,key):
            token=(kind,key)
            if token not in self.aliases:
                self.aliases[token]=kind+str(1+sum(k[0]==kind for k in self.aliases))
            return self.aliases[token]
        index={};facts={};covered=set()
        for statement in statements:
            supports=statement.get('supports',[])
            if not statement.get('id') or not supports or any(
                sup.get('evidence_id') not in reads or not sup.get('member_keys') or
                not set(sup['member_keys'])<={m['member_key'] for m in reads[sup['evidence_id']]['members']}
                for sup in supports):continue
            ref=alias('S',statement['id'])
            index[ref]={'statement_id':statement['id'],'supports':deepcopy(supports)}
            covered.update(s['evidence_id'] for s in supports)
            row=facts.setdefault(ref,{'id':ref,'text':statement['text'],'type':statement['type'],
                'entities':[{'label':e['label'],'role':e['role']} for e in statement.get('entities',[])],
                'evidence_refs':[alias('E',s['evidence_id']) for s in supports],'query_contexts':[]})
            context={k:statement[k] for k in ('_object','retrieval_role','retrieval_warning') if k in statement}
            paths=[{'class_id':p.get('class_id'),'kind':p['kind']} for p in statement.get('ontology_paths',[])]
            if paths:context['ontology_paths']=paths
            if context and context not in row['query_contexts']:row['query_contexts'].append(context)
        evidence=[];fallbacks=[]
        for eid,view in reads.items():
            ref=alias('E',eid)
            context=view.get('context',{})
            location={k:deepcopy(context[k]) for k in ('section','clause_number','table_title','figure_title') if context.get(k)}
            refs=[{k:deepcopy(r[k]) for k in ('kind','number','end_number','status') if k in r}
                  for r in context.get('reference_resolution',[])]
            if refs:location['references']=refs
            row={'id':ref,'title':view.get('title'),'context':location}
            evidence.append(row)
            if include_evidence or eid not in covered or view.get('source_status',{}).get('semantic_extraction')=='incomplete':
                fallbacks.append({**row,'text':view.get('text') or '\n'.join(m.get('quote','') for m in view['members'])})
                index[ref]={'supports':[{'evidence_id':eid,'member_keys':[m['member_key'] for m in view['members']]}]}
        return {'statements':list(facts.values()),'evidence_catalog':evidence,'evidence_fallbacks':fallbacks},index


def resolve_answer_references(response,index):
    """Expand only exact, available handles. Never repair a guessed long ID."""
    result=deepcopy(response)
    def bind(row):
        if not isinstance(row,dict):return
        row.pop('_reference_error',None)
        refs=row.get('references')
        row.pop('supports',None)
        row['supports']=[]
        if not isinstance(refs,list) or not refs or any(not isinstance(r,str) or r not in index for r in refs):
            row['_reference_error']='回答未引用有效的 Statement 或备用 Evidence 短编号'
            return
        members={}
        for ref in dict.fromkeys(refs):
            for support in index[ref]['supports']:
                members.setdefault(support['evidence_id'],[]).extend(support['member_keys'])
        row['supports']=[{'evidence_id':eid,'member_keys':list(dict.fromkeys(keys))} for eid,keys in members.items()]
    for row in result.get('claims',[]):bind(row)
    for operation in result.get('operations',[]):
        if isinstance(operation,dict):bind(operation.get('claim'))
    for row in result.get('gaps',[]):
        if isinstance(row,dict):
            row.pop('supports',None)
            if row.get('references'):bind(row)
    for task in result.get('tasks',[]):
        if isinstance(task,dict):
            for row in task.get('missing_items',[]):bind(row)
    return result


def model_answer_rows(rows):
    """Model-facing advice carries short handles, not backend member bindings."""
    return [{k:deepcopy(v) for k,v in row.items() if k not in ('supports','_reference_error')}
            for row in rows]


def _references(text):
    from turbine_kg.documents.ir import parse_references
    result=set()
    for ref in parse_references(text):
        if ref.get('end_number'):
            start=ref['number'].split('.');end=ref['end_number'].split('.')
            if len(start)!=len(end) or start[:-1]!=end[:-1] or not start[-1].isdigit() or not end[-1].isdigit() or int(start[-1])>=int(end[-1]):continue
        result.add((ref['kind'],ref['number']))
        if ref.get('end_number'):result.add((ref['kind'],ref['end_number']))
    return result


def _missing_references(expected,available):
    """Allow textual clause/section wording for the same explicit identifier.

    This is an answer wording check only. Table and figure namespaces remain
    distinct, and it never guesses a document's numbering depth or changes
    retrieval targets.
    """
    def key(reference):
        kind,number=reference
        return ('text',number) if kind in ('clause','section') else reference
    supported={key(reference) for reference in available}
    return {reference for reference in expected if key(reference) not in supported}


def model_source_views(views):
    """One complete source text, with only citation and engineering context."""
    result=[]
    for view in {view['evidence_id']:view for view in views}.values():
        context=view.get('context',{});members=view.get('members',[])
        pages=sorted({member.get('locator',{}).get('physical_page') for member in members
                      if isinstance(member.get('locator',{}).get('physical_page'),int)})
        row={'evidence_id':view['evidence_id'],'title':view.get('title',''),'text':view.get('text',''),
             'kind':view.get('kind','text'),'physical_pages':pages}
        source_context={key:deepcopy(context[key]) for key in
                        ('section','clause_number','clause_title','table_title','figure_title') if context.get(key)}
        references=[{key:deepcopy(value) for key,value in ref.items() if value not in (None,[],{})
                     and key in ('kind','number','end_number','status','target_evidence_ids','source_member_keys')}
                    for ref in context.get('reference_resolution',[])]
        if references:source_context['references']=references
        reviews=[]
        for review in context.get('confirmed_semantic_reviews',[]):
            compact={key:deepcopy(review[key]) for key in ('field','interpretation','value','reason') if review.get(key)}
            if compact:
                compact['member_keys']=[member['member_key'] for member in review.get('binding',{}).get('members',[]) if member.get('member_key')]
                reviews.append(compact)
        if reviews:source_context['confirmed_semantic_reviews']=reviews
        if source_context:row['context']=source_context
        warnings=[warning for warning in view.get('source_warnings',[]) if warning.get('state') not in (None,'good','readable','verified')]
        if warnings:row['warnings']=deepcopy(warnings)
        row['members']=[]
        for member in members:
            item={key:member[key] for key in ('member_key','role','quote') if key in member}
            locator=member.get('locator',{})
            if len(pages)>1 and locator.get('physical_page') is not None:item['physical_page']=locator['physical_page']
            if member.get('quality') not in (None,'good','readable','verified'):item['quality']=member['quality']
            attrs=member.get('attributes',{})
            if view.get('kind')=='table' or member.get('source_kind') in ('cell','table_note'):
                table={key:deepcopy(attrs[key]) for key in ('row_index','column_index','row_span','column_span',
                       'column_header','row_header','coverage','applies_to') if attrs.get(key) not in (None,[],{})}
                if table:item['table']=table
            row['members'].append(item)
        result.append(row)
    return result


def _supports_are_read(supports,reads):
    """Validate model-provided source addresses before indexing dictionaries."""
    if not isinstance(supports,list) or not supports:return False
    for support in supports:
        if not isinstance(support,dict) or not isinstance(support.get('evidence_id'),str):return False
        keys=support.get('member_keys')
        if not isinstance(keys,list) or not keys or any(not isinstance(key,str) for key in keys):return False
        view=reads.get(support['evidence_id'])
        if not view or not set(keys)<={member['member_key'] for member in view['members']}:return False
    return True


def bind_source_context(claim,reads,*,origin_evidence_ids=(),origin_bindings=None):
    """Add only real, already read structural parents and allowed reference origins."""
    result=deepcopy(claim)
    if not isinstance(result,dict) or not isinstance(result.get('supports'),list):return result
    if not _supports_are_read(result['supports'],reads):return result
    from turbine_kg.evidence.context import close_member_selection
    selected={}
    for support in result['supports']:
        if isinstance(support,dict) and support.get('evidence_id') in reads:
            selected.setdefault(support['evidence_id'],set()).update(
                close_member_selection(reads[support['evidence_id']],support.get('member_keys',[])))
    eligible=set(origin_evidence_ids)|set(selected)|set(origin_bindings or {})
    for _ in range(sum(len(v.get('members',[])) for v in reads.values())+1):
        changed=False
        for eid,keys in list(selected.items()):
            view=reads[eid]
            parents={parent for member in view['members'] if member['member_key'] in keys for parent in member.get('attributes',{}).get('structural_parent_source_ids',[])}
            for parent_id in parents:
                # A layout source can be split into different clauses. Its ID
                # alone is not a unique member address across Evidence views.
                matches=[(parent_eid,member) for parent_eid,parent_view in reads.items()
                         if all(view.get(field)==parent_view.get(field) for field in ('document_id','revision_id'))
                         for member in parent_view['members'] if member.get('source_id')==parent_id]
                local=[match for match in matches if match[0]==eid]
                # Local introductions and list boundaries have already been
                # resolved by close_member_selection; do not add a rejected
                # sibling back through the raw parent-source link.
                candidates=matches if not local and len(matches)==1 else []
                for parent_eid,member in candidates:
                    if member['member_key'] not in selected.get(parent_eid,set()):
                        selected.setdefault(parent_eid,set()).add(member['member_key']);changed=True
        for origin in eligible:
            origin_view=reads.get(origin)
            if not origin_view:continue
            for ref in origin_view.get('context',{}).get('reference_resolution',[]):
                targets=set(ref.get('target_evidence_ids',[]))&set(selected)
                if ref.get('status')!='resolved' or not targets:continue
                if any(any(origin_view.get(field)!=reads[target].get(field) for field in ('document_id','revision_id')) for target in targets):continue
                known={member['member_key'] for member in origin_view['members']}
                added=set(ref.get('source_member_keys',[]))&known
                if origin not in set(origin_evidence_ids):added&=selected.get(origin,set())|set((origin_bindings or {}).get(origin,[]))
                if added-selected.get(origin,set()):selected.setdefault(origin,set()).update(added);changed=True
        if not changed:break
    result['supports']=[{'evidence_id':eid,'member_keys':close_member_selection(reads[eid],list(keys))} for eid,keys in selected.items() if keys]
    return result


def proposed_source_bindings(claims,reads):
    """Chosen raw source members are context; generated facts are never trusted."""
    grouped={}
    for claim in claims:
        if not isinstance(claim,dict) or not isinstance(claim.get('task_id'),str) or not claim['task_id']:continue
        supports=claim.get('supports')
        if not _supports_are_read(supports,reads):continue
        closed=bind_source_context(claim,reads)
        for support in closed['supports']:grouped.setdefault(claim['task_id'],{}).setdefault(support['evidence_id'],set()).update(support['member_keys'])
    return grouped


def _structural_context_text(claim,reads):
    texts=[]
    for support in claim['supports']:
        context=reads[support['evidence_id']].get('context',{})
        for field in ('table_title','figure_title','section','clause_title'):
            value=context.get(field)
            if isinstance(value,str):texts.append(value)
        if context.get('clause_number'):texts.append('条款 '+str(context['clause_number']))
        bound=set(support['member_keys']);view=reads[support['evidence_id']]
        for ref in context.get('reference_resolution',[]):
            if ref.get('status')!='resolved' or not bound.intersection(ref.get('source_member_keys',[])):continue
            for target in ref.get('target_evidence_ids',[]):
                candidate=reads.get(target)
                if not candidate or any(candidate.get(field)!=view.get(field) for field in ('document_id','revision_id')):continue
                number=candidate.get('context',{}).get('clause_number')
                if number:texts.append('条款 '+str(number))
    return '\n'.join(texts)


def citations_for(claim,reads):
    result=[]
    for support in bind_source_context(claim,reads).get('supports',[]):
        view=reads[support['evidence_id']]
        selected={key for key in support['member_keys']}
        for member in view['members']:
            if member['member_key'] in selected:
                result.append({'id':identity('citation',view['evidence_id'],member['member_key']),'evidence_id':view['evidence_id'],'member_key':member['member_key'],'document_id':view.get('document_id'),'title':view['title'],'revision':view['revision_id'],'locator':member['locator'],'quote':member['quote'],'text_origin':member['text_origin'],'role':member['role'],'attributes':member.get('attributes',{}),'source_id':member.get('source_id'),'source_kind':member.get('source_kind'),'image':member.get('image')})
    return result


def group_citations(citations,reads):
    """A display view only: exact member citations remain the validation contract."""
    groups={};positions={};documents={}
    def source_order(cite):
        members=reads.get(cite['evidence_id'],{}).get('members',[])
        index=next((i for i,m in enumerate(members) if m['member_key']==cite['member_key']),0)
        member=members[index] if members else {}
        loc=cite.get('locator',{});page=loc.get('physical_page')
        order=member.get('source_order',member.get('order',index))
        bounds=loc.get('char_range') or [0]
        return (page if isinstance(page,int) else float('inf'),
                order if isinstance(order,(int,float)) else index,bounds[0])
    for cite in citations:
        view=reads.get(cite['evidence_id'],{});context=view.get('context',{});kind=view.get('kind','text')
        table_id=cite.get('attributes',{}).get('table_id')
        if table_id and cite.get('source_kind') in ('cell','table_note'):
            kind='table'
            context=next((other.get('context',{}) for other in reads.values() if other.get('context',{}).get('table_id')==table_id),{'table_id':table_id})
        resource=context.get('table_id') if kind=='table' else context.get('figure_id') or context.get('picture_id') if kind in ('image','picture') else context.get('clause_group_id') or context.get('clause_id')
        if kind in ('image','picture') and not resource:
            member=next((m for m in view.get('members',[]) if m['member_key']==cite['member_key'] and m.get('source_kind')=='picture'),None)
            resource=member.get('source_id') if member else None
        resource=resource or cite['evidence_id']
        kind={'image':'figure','picture':'figure'}.get(kind,kind)
        key=(cite.get('document_id') or view.get('document_id') or cite['title'],cite['revision'],kind,resource)
        documents.setdefault(key[:2],len(documents))
        label=context.get('table_title') if kind=='table' else context.get('figure_title') if kind=='figure' else context.get('clause_title') or context.get('clause_number') or context.get('section')
        row=groups.setdefault(key,{'id':identity('citation-group',key),'kind':kind,'resource_id':resource,'document_id':key[0],'title':cite['title'],'revision':cite['revision'],'resource_title':label or ('未编号表格' if kind=='table' else '未编号图片' if kind=='figure' else '原文条款'),'physical_pages':[],'logical_pages':[],'citation_ids':[],'evidence_ids':[],'member_keys':[],'quotes':[],'images':[]})
        for field,value in (('citation_ids',cite['id']),('evidence_ids',cite['evidence_id']),('member_keys',cite['member_key']),('quotes',cite.get('quote')),('images',cite.get('image')),('physical_pages',cite.get('locator',{}).get('physical_page')),('logical_pages',cite.get('locator',{}).get('logical_page'))):
            if value is not None and value!='' and value not in row[field]:row[field].append(value)
    for row in groups.values():
        chosen=sorted({cite['id']:cite for cite in citations if cite['id'] in row['citation_ids']}.values(),key=source_order)
        positions[row['id']]=source_order(chosen[0]) if chosen else (float('inf'),0,0)
        for field,key in (('citation_ids','id'),('evidence_ids','evidence_id'),('member_keys','member_key')):
            row[field]=list(dict.fromkeys(cite[key] for cite in chosen))
        for field in ('physical_pages','logical_pages'):row[field].sort(key=lambda value:(not isinstance(value,int),value if isinstance(value,int) else str(value)))
        if row['kind'] == 'text':
            from turbine_kg.evidence.context import evidence_text
            if chosen:
                context={**reads[chosen[0]['evidence_id']].get('context',{}),
                         'source_ids':[cite.get('source_id') for cite in chosen]}
                text=evidence_text(context,[cite.get('quote','') for cite in chosen],
                    [cite.get('role','content') for cite in chosen],
                    [cite.get('source_kind') for cite in chosen])
                if text:row['quotes']=[text]
    return sorted(groups.values(),key=lambda row:(documents[(row['document_id'],row['revision'])],positions[row['id']]))


def binding_error(claim,reads,visuals):
    if not isinstance(claim,dict): return '陈述合同无效'
    if claim.get('_reference_error'):return claim['_reference_error']
    if claim.get('basis') not in ('source','inference','image') or not claim.get('text') or not claim.get('supports'): return '缺少依据类别、内容或支持绑定'
    if not isinstance(claim['text'],str) or not isinstance(claim['supports'],list): return '陈述合同无效'
    image_read=False
    for support in claim['supports']:
        if (not isinstance(support,dict) or not isinstance(support.get('evidence_id'),str)
                or not isinstance(support.get('member_keys'),list)
                or any(not isinstance(key,str) for key in support['member_keys'])):return '支持绑定合同无效'
        view=reads.get(support.get('evidence_id'))
        if not view or not support.get('member_keys'): return '来源尚未读取'
        members={member['member_key']:member for member in view['members']}
        if any(key not in members for key in support['member_keys']): return '成员绑定越界'
        image_read |= any(row.get('status')=='read' and row.get('resource_read') is True and row.get('pixels_sent') is True and row.get('response_received') is True
                          and row.get('member_key') in support['member_keys'] and members[row['member_key']].get('source_kind')=='picture'
                          for row in visuals.get(support['evidence_id'],[]))
    if claim['basis']=='image' and not image_read: return '未成功读取图片像素'
    if claim['basis'] in ('source','inference'):
        from unicodedata import normalize
        cited='\n'.join(row['quote'] for row in citations_for(claim,reads))
        displayed='\n'.join([claim['text'],*[str(value) for field in ('conditions','verification','assumptions') for value in (claim.get(field) or [])]])
        if not cited.strip(): return '文字陈述没有原文支持，图片须另列图片解读'
        displayed=normalize('NFKC',displayed);cited=normalize('NFKC',cited)
        reference_sources=cited+'\n'+normalize('NFKC',_structural_context_text(claim,reads))+'\n'+'\n'.join(row['title'] for row in citations_for(claim,reads))
        if _missing_references(_references(displayed),_references(reference_sources)): return '回答的表图或条款编号没有来源支持'
    return None


def binding_diagnostics(claim,reads):
    """Inspect exact bound source and structural IDs without changing the verdict."""
    from unicodedata import normalize
    result={'text':claim.get('text'),'task_id':claim.get('task_id'),'supports':deepcopy(claim.get('supports',[]))}
    try:
        cited='\n'.join(row['quote'] for row in citations_for(claim,reads))
        displayed='\n'.join([str(claim.get('text','')),*[str(value) for field in ('conditions','verification','assumptions') for value in claim.get(field,[]) or []]])
        context=cited+'\n'+_structural_context_text(claim,reads)+'\n'+'\n'.join(row['title'] for row in citations_for(claim,reads))
        expected=_references(normalize('NFKC',displayed));available=_references(normalize('NFKC',context))
        result.update(displayed_references=sorted(expected),available_references=sorted(available),missing_references=sorted(_missing_references(expected,available)))
    except (KeyError,TypeError,ValueError):result['binding_status']='invalid_or_unread'
    return result


def validate(claims,tools,*,tasks=None):
    """Run deterministic answer checks and return source-bound claims.

    This function does not send claims back to a second model: source
    membership, task binding, provenance and image transport are checked by
    code. Semantic review checks engineering values and meaning.
    """
    pending=[];rejected=[]
    for index,claim in enumerate(claims):
        if not isinstance(claim,dict):
            rejected.append({'claim':claim,'reason':'陈述合同无效'});continue
        claim={**claim,'id':claim.get('id') or identity('claim',index,claim)}
        task=next((t for t in tasks or [] if t.get('id')==claim.get('task_id')),None)
        if tasks is not None and task is None:
            rejected.append({'claim':claim,'reason':'陈述未绑定有效对象子问题任务'});continue
        error=binding_error(claim,tools.reads,tools.visuals)
        if error: rejected.append({'claim':claim,'reason':error})
        else:
            pending.append(claim)
    return pending,rejected


def apply_answer_operations(claims, response, tools, *, tasks, missing_items, rejected=()):
    """Apply explicit additions/replacements only after checking their sources.

    Replacement IDs refer to the current answer, never to knowledge graph nodes.
    An invalid replacement leaves its previously accepted claim intact.
    """
    current={claim['id']:claim for claim in claims}
    targets={row['claim']['id']:row['claim'] for row in rejected
             if isinstance(row.get('claim'),dict) and row['claim'].get('id')}
    targets.update(current)
    gaps={row['id']:row for row in missing_items}
    taskids={task['id'] for task in tasks}
    operations=response.get('operations')
    if not isinstance(operations,list):
        return list(claims),[{'claim':{},'reason':'补答必须返回operations，不能追加整份claims'}]
    pending=[];failed=[];actions={};replaced=set()
    for index,operation in enumerate(operations):
        claim=operation.get('claim') if isinstance(operation,dict) else None
        if not isinstance(claim,dict):
            failed.append({'claim':{},'reason':'补答操作缺少有效陈述'});continue
        kind=operation.get('op');target=operation.get('claim_id');gap=operation.get('missing_item_id')
        taskid=claim.get('task_id')
        if taskid not in taskids:
            failed.append({'claim':claim,'reason':'补答未绑定本次任务'});continue
        if kind=='replace':
            if (not isinstance(target,str) or target not in targets or target in replaced
                    or targets[target].get('task_id')!=taskid):
                failed.append({'claim':claim,'reason':'替换目标不存在、重复或不属于当前任务'});continue
            replaced.add(target);cid=target
        elif kind=='add':
            if not isinstance(gap,str) or gap not in gaps or gaps[gap].get('task_id')!=taskid:
                failed.append({'claim':claim,'reason':'新增建议未对应本次明确缺口'});continue
            cid=identity('answer-add',gap,index,claim)
        else:
            failed.append({'claim':claim,'reason':'补答只允许add或replace'});continue
        pending.append({**claim,'id':cid});actions[cid]=(kind,target)
    bindings=proposed_source_bindings([*claims,*pending],tools.reads)
    pending=[bind_source_context(claim,tools.reads,origin_bindings=bindings.get(claim['task_id'],{})) for claim in pending]
    accepted,invalid=validate(pending,tools,tasks=tasks)
    failed.extend(invalid)
    corrected={claim['id'] for claim in accepted}
    failed.extend(row for row in rejected if row.get('claim',{}).get('id') not in corrected)
    for claim in accepted:
        kind,target=actions[claim['id']]
        if kind=='replace':current[target]=claim
        else:
            # Exact wording already present needs no new slot even if a model
            # changed the citation set. Semantic equivalence is reviewed by LLM.
            if any(row['task_id']==claim['task_id'] and row['basis']==claim['basis']
                   and ''.join(row['text'].split())==''.join(claim['text'].split()) for row in current.values()):continue
            current[claim['id']]=claim
    return list(current.values()),failed

def coverage_reference_context(tools):
    """Expose pinned source scope and resolved reference states, never inferred gaps."""
    scopes=[];references=[]
    for eid,view in tools.reads.items():
        context=view.get('context',{})
        scope={key:view.get(key) for key in ('document_id','revision_id')}
        scope['physical_pages']=context.get('scope_physical_pages',[])
        if scope not in scopes:scopes.append(scope)
        for ref in context.get('reference_resolution',context.get('reference_resolutions',[])):
            row={'origin_evidence_id':eid,**ref}
            references.append(row)
    return {'current_scope':scopes,'reference_statuses':references,
            'reference_gaps':getattr(tools,'reference_gaps',[]),
            'pending_reference_ids':sorted(getattr(tools,'pending_reference_ids',set()))}


def support_dependencies(supports,tools,context=None):
    """Outside dependencies cannot complete an answer; internal unread ones block."""
    context=context or coverage_reference_context(tools)
    bound={}
    for support in supports:bound.setdefault(support['evidence_id'],set()).update(support.get('member_keys',[]))
    deferred=[];blocked=[]
    for ref in context['reference_statuses']:
        eid=ref.get('origin_evidence_id');keys=set(ref.get('source_member_keys',[]))
        if eid not in bound or keys and not keys.intersection(bound[eid]):continue
        status=ref.get('status')
        if status in ('deferred_outside_scope','external_document'):deferred.append(ref)
        elif status not in ('resolved','self_reference') or any(target not in tools.reads for target in ref.get('target_evidence_ids',[])):blocked.append(ref)
    for ref in context['reference_gaps']:
        if ref.get('origin_evidence_id') not in bound:continue
        if ref.get('kind') in ('reference_unread','reference_ambiguous'):blocked.append(ref)
        elif ref.get('kind')=='reference_deferred' and ref.get('resolution_status') in ('deferred_outside_scope','external_document'):
            if not any(r.get('origin_evidence_id')==ref.get('origin_evidence_id') and r.get('number')==ref.get('reference_number') for r in deferred):deferred.append(ref)
        elif ref.get('kind')=='reference_deferred' and ref.get('resolution_status') not in ('deferred_outside_scope','external_document'):
            if not any(r.get('origin_evidence_id')==ref.get('origin_evidence_id') and r.get('number')==ref.get('reference_number') for r in deferred):blocked.append(ref)
    return deferred,blocked
