"""Only validated claims are rendered; source quotes remain independent members."""
import re
from decimal import Decimal
from copy import deepcopy
from turbine_kg.runtime import identity
from turbine_kg.knowledge.quantities import NUMBER_PATTERN, UNIT_PATTERN


_LEADING_CLAUSE = re.compile(r'(?m)^\s*(?P<number>\d+(?:\.\d+){2,})(?![\d.A-Za-z])(?=\s|[\u4e00-\u9fff])')
_DOCUMENT_CODE = re.compile(r'(?<![A-Za-z])(?P<prefix>[A-Za-z]{1,10}(?:\s*/\s*[A-Za-z]{1,5})?)\s*(?P<number>\d+(?:\.\d+)*)\s*[-—–−:]\s*(?P<year>\d{4})(?!\d)')
_STANDARD_IDENTIFIER = re.compile(r'(?<![A-Za-z])(?P<prefix>[A-Z]{1,8}(?:\s*/\s*[A-Z]{1,5})?)\s*(?P<number>\d{3,}(?:\.\d+)*)(?:\s*[-—–−:]\s*\d{4})?(?![\d.])',re.IGNORECASE)


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


def _without_identifiers(text):
    from turbine_kg.documents.ir import parse_references
    for ref in reversed(parse_references(text)):
        if ref['kind'] in ('table','figure') or ref['number'].count('.')>=2:text=text[:ref['start']]+text[ref['end']:]
    for pattern in (_DOCUMENT_CODE,_LEADING_CLAUSE):text=pattern.sub('',text)
    return text


_NUMBER = NUMBER_PATTERN
_UNIT = UNIT_PATTERN


def _quantities(text):
    from turbine_kg.knowledge.quantities import extract_quantities
    return extract_quantities(text)


def _normalized_quantities(quantities):
    """Compare equivalent values through the existing unit registry."""
    from turbine_kg.knowledge.quantities import _unit
    result=set()
    for value,unit in quantities:
        known=_unit(unit)
        result.add((known[0],Decimal(str(value))*Decimal(known[1])) if known
                   else (unit,Decimal(str(value))))
    return result


def _bound_user_quantities(claim, task, user_facts):
    """User measurements can be restated without declaring a source threshold.

    Facts passed here have already been bound to the user's actual question by
    plan_tasks. Whether advice misuses them as limits is a semantic review task;
    a successful numeric comparison is not a prerequisite to acknowledge a value.
    """
    values=set();quantities=set()
    if not isinstance(task,dict) or claim.get('task_id')!=task.get('id') or not isinstance(user_facts,dict):
        return values,quantities
    facts=user_facts.get(task.get('object'),{})
    if not isinstance(facts,dict):return values,quantities
    for fact in facts.values():
        if not isinstance(fact,dict) or 'value' not in fact or not fact.get('unit'):continue
        try:value=float(fact['value'])
        except (ValueError,TypeError):continue
        import math
        if not math.isfinite(value):continue
        values.add(value);quantities.update(_quantities(f"{fact['value']} {fact['unit']}"))
    return values,quantities


def _bound_comparison_quantities(claim, task):
    """Admit only completed program comparisons bound to the cited members."""
    values=set();quantities=set()
    if not isinstance(task,dict) or claim.get('task_id')!=task.get('id'):
        return values,quantities
    cited={(support['evidence_id'],key) for support in claim['supports'] for key in support['member_keys']}
    for check in task.get('numeric_comparisons',[]):
        if not isinstance(check,dict) or check.get('status') not in ('satisfied','not_satisfied'):continue
        supports=check.get('supports',[])
        bound={(support.get('evidence_id'),key) for support in supports if isinstance(support,dict) for key in support.get('member_keys',[])}
        if not bound or not bound.issubset(cited):continue
        pairs=[(check.get('limit'),check.get('unit')),
               (check.get('normalized_actual'),check.get('comparison_unit'))]
        actual=check.get('actual',{})
        if isinstance(actual,dict):pairs.append((actual.get('value'),actual.get('unit')))
        for value,unit in pairs:
            if value is None or not isinstance(unit,str) or not unit:continue
            try:number=Decimal(str(value))
            except (ValueError,ArithmeticError):continue
            if not number.is_finite():continue
            values.add(float(number));quantities.update(_quantities(f'{number} {unit}'))
    return values,quantities


def reviewed_formula_numbers(claim,reads):
    """Only exact bound, independently source-verified review values are eligible."""
    from turbine_kg.knowledge.formula_sources import verified_formula_representation
    values=set();quantities=set();expressions=[]
    for support in claim['supports']:
        view=reads[support['evidence_id']]
        for member in view['members']:
            if member['member_key'] not in support['member_keys'] or member.get('text_origin')!='verified_correction' or 'raw_quote' not in member:continue
            source={'id':member.get('source_id'),'text':member['raw_quote'],'locator':member.get('locator',{}),'attributes':member.get('attributes',{}),**{field:view.get(field) for field in ('document_id','revision_id')}}
            review=verified_formula_representation(source)
            if not review or review['verified_text']!=member.get('quote'):continue
            formula=review['formula'];expressions.append(formula['expression'])
            for variable in formula.get('variables') or []:
                if not isinstance(variable,dict):continue
                bounds=variable.get('range')
                if not isinstance(bounds,dict):continue
                unit=bounds.get('unit') or (formula.get('units') or {}).get(variable.get('symbol'))
                for field in ('lower','upper'):
                    value=bounds.get(field)
                    if type(value) not in (int,float):continue
                    values.add(float(value))
                    if unit=='1':
                        quantities.add((float(value),'1'));quantities.add((float(value)*100,'%'));values.add(float(value)*100)
                    elif isinstance(unit,str):quantities.add((float(value),unit))
    return values,quantities,expressions


def _outside_reviewed_formulas(text,expressions):
    if not expressions:return text
    from unicodedata import normalize
    def compact(value):
        value=normalize('NFKC',value).replace('−','-').replace('–','-').replace('×','*')
        return re.sub(r'(?<=[A-Za-z])_(?=[A-Za-z0-9])','',re.sub(r'\s+','',value))
    value=compact(text)
    for expression in expressions:value=value.replace(compact(expression),'')
    return value


def _table_quantities(claim,reads):
    """Only bound cells inherit a unique unit from bound headers in their columns."""
    from unicodedata import normalize
    result=set()
    for support in claim['supports']:
        view=reads[support['evidence_id']]
        selected=set(support['member_keys'])
        members=[m for m in view['members'] if m['member_key'] in selected]
        headers=[m for m in members if m.get('role') in ('header','inherited_header') or m.get('attributes',{}).get('column_header')]
        for cell in members:
            if cell.get('source_kind')!='cell' or cell in headers:continue
            attrs=cell.get('attributes',{});column=attrs.get('column_index')
            if not isinstance(column,int):continue
            columns=set(range(column,column+attrs.get('column_span',1)))
            units=set()
            for header in headers:
                ha=header.get('attributes',{});hc=ha.get('column_index')
                if ha.get('table_id')!=attrs.get('table_id'):continue
                if not isinstance(hc,int):continue
                if not columns.issubset(set(range(hc,hc+ha.get('column_span',1)))):continue
                if isinstance(ha.get('row_index'),int) and isinstance(attrs.get('row_index'),int) and ha['row_index']>attrs['row_index']:continue
                units.update(re.findall(r'(?<![A-Za-z])('+_UNIT+r')(?![A-Za-z/])',normalize('NFKC',header.get('quote',''))))
            # Conflicting/ambiguous header units are left to a new complete read.
            if len(units)==1:
                values=re.findall(r'(?<![A-Za-z\d.])('+_NUMBER+r')(?![\d.])',_without_identifiers(normalize('NFKC',cell.get('quote',''))))
                result.update((float(value),next(iter(units))) for value in values)
    return result


def _outside_supported_standard_identifiers(text,source_context):
    """Do not count a cited standard ID as an engineering measurement."""
    supported={(match.group('prefix').split('/')[0].strip().upper(),match.group('number')) for match in _STANDARD_IDENTIFIER.finditer(source_context)}
    for match in reversed(list(_STANDARD_IDENTIFIER.finditer(text))):
        if (match.group('prefix').split('/')[0].strip().upper(),match.group('number')) in supported:
            text=text[:match.start()]+text[match.end():]
    return text


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
                        ('clause_number','clause_title','table_title','figure_title') if context.get(key)}
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
    groups={}
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
        label=context.get('table_title') if kind=='table' else context.get('figure_title') if kind=='figure' else context.get('clause_title') or context.get('clause_number') or context.get('section')
        row=groups.setdefault(key,{'id':identity('citation-group',key),'kind':kind,'resource_id':resource,'document_id':key[0],'title':cite['title'],'revision':cite['revision'],'resource_title':label or ('未编号表格' if kind=='table' else '未编号图片' if kind=='figure' else '原文条款'),'physical_pages':[],'logical_pages':[],'citation_ids':[],'evidence_ids':[],'member_keys':[],'quotes':[],'images':[]})
        for field,value in (('citation_ids',cite['id']),('evidence_ids',cite['evidence_id']),('member_keys',cite['member_key']),('quotes',cite.get('quote')),('images',cite.get('image')),('physical_pages',cite.get('locator',{}).get('physical_page')),('logical_pages',cite.get('locator',{}).get('logical_page'))):
            if value is not None and value!='' and value not in row[field]:row[field].append(value)
    for row in groups.values():
        for field in ('physical_pages','logical_pages'):row[field].sort(key=lambda value:(not isinstance(value,int),value if isinstance(value,int) else str(value)))
        if row['kind'] == 'text':
            from turbine_kg.evidence.context import evidence_text
            chosen=[cite for cite in citations if cite['id'] in row['citation_ids']]
            if chosen:
                context={**reads[chosen[0]['evidence_id']].get('context',{}),
                         'source_ids':[cite.get('source_id') for cite in chosen]}
                text=evidence_text(context,[cite.get('quote','') for cite in chosen],
                    [cite.get('role','content') for cite in chosen],
                    [cite.get('source_kind') for cite in chosen])
                if text:row['quotes']=[text]
    return list(groups.values())


def binding_error(claim,reads,visuals,*,user_facts=None,task=None):
    if not isinstance(claim,dict): return '陈述合同无效'
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


def numeric_warning(claim, reads, *, user_facts=None, task=None):
    """Literal-number differences are review hints, never deletion decisions.

    Site values and arithmetic results need not occur in a quoted standard.
    The existing semantic review evaluates their engineering use in context.
    """
    if claim.get('basis') in ('source','inference'):
        from turbine_kg.knowledge.quantities import normalize_quantity_text
        cited=normalize_quantity_text('\n'.join(row['quote'] for row in citations_for(claim,reads)))
        displayed=normalize_quantity_text('\n'.join([claim['text'],*[str(value) for field in ('conditions','verification','assumptions') for value in (claim.get(field) or [])]]))
        reference_sources=cited+'\n'+_structural_context_text(claim,reads)
        # Verified structural identifiers are not engineering quantities. Keep
        # checking every remaining value against the explicitly bound members.
        values=lambda text:{float(value) for value in re.findall(r'(?<![A-Za-z\d.])('+_NUMBER+r')(?![\d.])',_without_identifiers(text))}
        reviewed_values,reviewed_quantities,reviewed_expressions=reviewed_formula_numbers(claim,reads)
        user_values,user_quantities=_bound_user_quantities(claim,task,user_facts)
        comparison_values,comparison_quantities=_bound_comparison_quantities(claim,task)
        percent_values={value/100 for value,unit in _quantities(cited) if unit=='%'}
        engineering_text=_outside_supported_standard_identifiers(displayed,reference_sources)
        # Pair a value with its unit; 0.10 mm cannot support a claim of 0.10 m.
        displayed_quantities=_quantities(_outside_reviewed_formulas(displayed,reviewed_expressions))
        supported_quantities=_quantities(_outside_reviewed_formulas(cited,reviewed_expressions))|_table_quantities(claim,reads)|reviewed_quantities|user_quantities|comparison_quantities
        if not _normalized_quantities(displayed_quantities).issubset(_normalized_quantities(supported_quantities)): return '回答数量与引用的数值单位组合不一致'
        # Unit-bearing spans have already passed their dimensional check. Bare
        # numbers still need direct support; no new arithmetic is inferred.
        bare=re.sub('('+_NUMBER+r')\s*('+_UNIT+r')(?![A-Za-z/])','',engineering_text)
        if not values(bare).issubset(values(cited)|reviewed_values|percent_values|user_values|comparison_values): return '回答包含引用成员没有的数值'
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


def validate(claims,tools,*,facts,tasks=None):
    """Run deterministic answer checks and return source-bound claims.

    This function does not send claims back to a second model: source
    membership, task binding, provenance and image transport are checked by
    code. Numeric differences are advisory; semantic review checks meaning.
    """
    pending=[];rejected=[]
    for index,claim in enumerate(claims):
        if not isinstance(claim,dict):
            rejected.append({'claim':claim,'reason':'陈述合同无效'});continue
        claim={**claim,'id':claim.get('id') or identity('claim',index,claim)}
        task=next((t for t in tasks or [] if t.get('id')==claim.get('task_id')),None)
        if tasks is not None and task is None:
            rejected.append({'claim':claim,'reason':'陈述未绑定有效对象子问题任务'});continue
        error=binding_error(claim,tools.reads,tools.visuals,user_facts=facts,task=task)
        if error: rejected.append({'claim':claim,'reason':error})
        else:
            pending.append(claim)
            warning=numeric_warning(claim,tools.reads,user_facts=facts,task=task)
            if warning and hasattr(tools,'diagnostics'):
                tools.diagnostics.append({'kind':'numeric_review_hint','claim_id':claim['id'],
                    'reason':warning,'blocking':False,'text':claim['text']})
    return pending,rejected

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
