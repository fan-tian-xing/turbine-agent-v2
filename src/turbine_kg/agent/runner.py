"""Plan source retrieval and produce independently reviewed, grounded advice."""
from __future__ import annotations
from dataclasses import dataclass,field,asdict
from copy import deepcopy
import json
import re
import time
import unicodedata
from typing import TypedDict
from langgraph.graph import StateGraph,START,END
from turbine_kg.agent.claims import validate,citations_for,group_citations,support_dependencies,bind_source_context,binding_diagnostics,proposed_source_bindings,model_source_views,_references
from turbine_kg.agent.model import ModelClient,ModelError,call_model
from turbine_kg.agent.skills import load_catalog,task_skill_context
from turbine_kg.agent.prompts import (answer_prompt,answer_review_prompt,invalid_skill_repair_prompt,
                                       repair_prompt,retrieval_prompt,understanding_prompt)
from turbine_kg.knowledge.applicability import evaluate
from turbine_kg.ontology.registry import OntologyRegistry,normalized
from turbine_kg.retrieval.tools import Tools
from turbine_kg.runtime import Budget,BudgetExceeded,identity,json_text,fingerprint


@dataclass
class AnswerResult:
    answer: str
    retrieval_status: str
    claims: list[dict]
    citations: list[dict]
    gaps: list[dict]
    search_summary: dict
    citation_groups: list[dict]=field(default_factory=list)

    def __post_init__(self):
        pending=any(task.get('answer_review_failure') for task in self.search_summary.get('task_coverage',[]))
        pending=pending or any(gap.get('kind')=='system_incomplete' and gap.get('stage') in
            ('completeness_review','repair_completeness_review','repair_generation','repair_binding') for gap in self.gaps)
        if self.claims and pending:
            self.answer+='\n说明：部分依据补查或回答核验尚未完成，以上建议以已列来源及适用条件为限。'


@dataclass
class Session:
    facts: dict[str,dict]=field(default_factory=dict)
    last_objects: list[str]=field(default_factory=list)
    turns: list[dict]=field(default_factory=list)
    fact_provenance: dict[str,dict]=field(default_factory=dict)


class State(TypedDict,total=False):
    question: str
    tasks: list
    skills: list
    facts: dict
    results: list
    errors: list
    round: int
    stop: bool
    claims: list
    gaps: list
    visual_required: bool
    figure_ids: list
    stop_reason: str


def ranking_terms(value):
    """Keep supplied whole terms; tolerate a short whitespace-separated term list."""
    values=[value] if isinstance(value,str) else value if isinstance(value,list) else []
    terms=[]
    for term in values:
        if not isinstance(term,str) or not term.strip(): continue
        term=term.strip();parts=term.split()
        # Long questions and sentences are not tokenized into guesses. Retain
        # the phrase too, so a multiword concept still receives its exact hit.
        terms.append(term)
        if 1<len(parts)<=3 and len(term)<=48 and all(len(part)<=24 for part in parts) and not re.search(r'[，,。;；?？!！:：]',term): terms.extend(parts)
        # Chinese query planners may join the engineering action to a generic
        # question ending. Rank the action independently, without replacing
        # the original phrase or guessing a domain-specific synonym.
        for ending in ('规定','要求','方法'):
            if term.endswith(ending) and len(term)>len(ending)+1:
                terms.append(term[:-len(ending)])
                break
    return list(dict.fromkeys(terms))


def relevance_terms(value, broad=()):
    """Score source content by requested substance, not generic question words."""
    ignored={'规定','要求','方法'}
    excluded={unicodedata.normalize('NFKC',term).casefold() for term in broad}
    terms=[]
    for term in ranking_terms(value):
        word=unicodedata.normalize('NFKC',term).casefold()
        for ending in ignored:
            if word.endswith(ending) and len(word)>len(ending)+1:
                word=word[:-len(ending)]
                break
        if word and word not in excluded and word not in ignored:terms.append(word)
    return list(dict.fromkeys(terms))


def _entity_class_id(entity):
    """Resolve persisted ontology proxies as well as extraction-time entities."""
    if not isinstance(entity,dict):return None
    return (entity.get('class_id') or entity.get('ontology_class_id')
            or (entity.get('ontology_binding') or {}).get('class_id')
            or entity.get('class'))


def _entity_is_a(entity, upper_class, registry):
    """Use the registered ontology class when checking a task qualifier."""
    if not isinstance(entity, dict):
        return False
    class_id = _entity_class_id(entity)
    return isinstance(class_id, str) and registry.domain_is_a(class_id, upper_class)


def task_activity_match(statement, task, registry):
    """Classify an extracted statement against the task's explicit activity.

    The result is deliberately tri-valued.  A statement without an activity
    remains usable as a generic requirement; a statement with a registered,
    different activity is excluded from that task.  No equipment or clause
    name is embedded in this rule.
    """
    requested = task.get('activity') or task.get('phase')
    if not isinstance(requested, str) or not requested.strip():
        return 'unknown'
    requested = (registry.domain_class_by_id(requested)
                 if requested in getattr(registry, 'domain_classes', {})
                 else registry.domain_class(requested))
    if requested is None or not registry.domain_is_a(requested['class_id'], 'Activity'):
        return 'unknown'
    activities = [entity for entity in statement.get('entities', [])
                  if _entity_is_a(entity, 'Activity', registry)]
    if not activities:
        return 'unknown'
    for entity in activities:
        class_id = _entity_class_id(entity)
        if class_id == requested['class_id']:
            return 'applicable'
        # A specific registered activity is applicable to a task asking for
        # its broader activity class (for example a typed test under 试验).
        if registry.domain_is_a(class_id, requested['class_id']):
            return 'applicable'
    return 'not_applicable'


def task_subject_match(statement, task, registry):
    """Match the task object against a Statement's primary source entity.

    Primary source entities provide positive class matches. Other components
    remain candidates: their parent clause may establish applicability, which
    the independent answer review checks against the actual source context.
    """
    requested = task.get('object')
    if not isinstance(requested, str) or not requested.strip():
        return 'unknown'
    requested_class = registry.domain_class(requested)
    requested_labels = {normalized(requested), normalized(registry.canonical_label(requested))}
    if requested_class:
        requested_labels.update(normalized(value) for value in
                                [requested_class.get('label'), *requested_class.get('aliases', [])]
                                if isinstance(value, str) and value.strip())

    entities = [entity for entity in statement.get('entities', [])
                if isinstance(entity, dict)]
    primary = [entity for entity in entities
               if entity.get('role') in ('subject', 'quantity_target')]
    if not primary:
        primary = [entity for entity in entities if entity.get('role') == 'object']
    if not primary:
        return 'unknown'

    for entity in primary:
        labels = [entity.get('label')]
        occurrence = entity.get('occurrence')
        if isinstance(occurrence, dict):
            labels.append(occurrence.get('object'))
        if any(isinstance(label, str) and normalized(label) in requested_labels
               for label in labels):
            return 'applicable'
        entity_class = _entity_class_id(entity)
        if requested_class and isinstance(entity_class, str):
            if (registry.domain_is_a(entity_class, requested_class['class_id'])
                    or registry.domain_is_a(requested_class['class_id'], entity_class)):
                return 'applicable'
    # A component's primary entity can inherit its applicability from a parent
    # clause. Class mismatch alone does not establish a source contradiction.
    return 'unknown'


def task_phase_match(statement, task):
    """Use only explicit phase wording; missing wording stays unknown."""
    requested = task.get('phase')
    if not isinstance(requested, str) or not requested.strip():
        return 'unknown'
    text = unicodedata.normalize('NFKC', str(statement.get('text', '')))
    requested = unicodedata.normalize('NFKC', requested.strip())
    if requested and requested in text:
        return 'applicable'
    # A phase qualifier that is explicitly present in the source but differs
    # from the user's wording is not silently treated as applicable.  The
    # activity-class check remains the authoritative filter when available.
    return 'unknown'


def task_statement_applicability(statement, task, registry):
    """Return whether a candidate can support the current task."""
    subject = task_subject_match(statement, task, registry)
    activity = task_activity_match(statement, task, registry)
    phase = task_phase_match(statement, task)
    # Different activities can describe necessary prerequisites. Retain those
    # candidates for the semantic review rather than inferring exclusion here.
    if activity == 'not_applicable':return 'unknown'
    return 'applicable' if 'applicable' in (subject, activity, phase) else 'unknown'


def filter_independent_source_claims(claims, tasks, reads, statements, registry, reference_links=(), question=None):
    """Reject advice supported only by a separately scoped source operation.

    A related mention of the queried equipment does not make the source
    clause's own subject an obligation of that equipment. Keep clauses whose
    lead names the queried object, broader requirements, explicitly requested
    component work, and requirements reached through an explicit reference.
    Unclear cases remain for the independent semantic review.
    """
    by_task={task['id']:task for task in tasks}
    user_question=normalized(question) if isinstance(question,str) else None
    by_evidence={}
    for statement in statements:
        for support in statement.get('supports',[]):
            by_evidence.setdefault(support.get('evidence_id'),[]).append(statement)
    def clause_lead(evidence_id):
        return next((member.get('quote','') for member in
                     reads.get(evidence_id,{}).get('members',[])
                     if member.get('role')=='content'),'')
    def explicit_reference(link):
        number=link.get('reference_number')
        if not isinstance(number,str) or not number.strip():return False
        pattern=r'(?<![\d.])'+re.escape(normalized(number))+r'(?![\d.])'
        return any(re.search(pattern,normalized(member.get('quote','')))
                   for member in reads.get(link.get('origin_evidence_id'),{}).get('members',[])
                   if member.get('role')=='content')
    kept=[];rejected=[]
    for claim in claims:
        task=by_task.get(claim.get('task_id'))
        target=(registry.domain_class(task['object']) or registry.qualified_class(task['object'])) if task else None
        if not target:
            kept.append(claim);continue
        target_id=target['class_id']
        task_question=normalized(task.get('subquestion',''))
        asked=user_question if user_question is not None else task_question
        target_names=[target.get('label'),*target.get('aliases',[])]
        if user_question is not None and not any(name and normalized(name) in asked for name in target_names):
            rejected.append({'claim':claim,'reason':'任务对象未出现在用户问题中，不能由模型扩展为独立作业'})
            continue
        referenced={link.get('evidence_id') for link in reference_links
                    if link.get('reference_number')==reads.get(link.get('evidence_id'),{}).get('context',{}).get('clause_number')
                    and explicit_reference(link)
                    and any(name and normalized(name) in normalized(clause_lead(
                        link.get('origin_evidence_id'))) for name in target_names)}
        mismatch=False
        for support in claim.get('supports',[]):
            evidence_id=support.get('evidence_id')
            view=reads.get(evidence_id,{})
            if not view or evidence_id in referenced:continue
            source_statements=by_evidence.get(evidence_id,[])
            related=False;direct=False;other_owner=False;explicit_subject=False
            for statement in source_statements:
                for entity in statement.get('entities',[]):
                    if not isinstance(entity,dict):continue
                    role=entity.get('role')
                    class_id=_entity_class_id(entity)
                    if role=='related' and class_id==target_id:related=True
                    if role not in ('subject','quantity_target','object'):continue
                    if (isinstance(class_id,str) and class_id!='Mention' and
                            (registry.domain_is_a(class_id,target_id) or
                             registry.domain_is_a(target_id,class_id))):
                        direct=True
                    elif (isinstance(class_id,str) and
                          registry.domain_is_a(class_id,'PhysicalObject')):
                        other_owner=True
                    label=entity.get('surface_label') or entity.get('label')
                    if (isinstance(label,str) and len(normalized(label))>=2 and
                            normalized(label) in asked and
                            not (isinstance(class_id,str) and registry.domain_is_a(class_id,'Activity'))):
                        explicit_subject=True
            lead=clause_lead(evidence_id)
            if any(name and normalized(name) in normalized(lead) for name in target_names):
                continue
            if (related or other_owner) and not direct and not explicit_subject:
                mismatch=True;break
        if mismatch:
            rejected.append({'claim':claim,'reason':'来源条款的主要作业与问题不同；查询对象仅在该条款中被提及'})
        else:
            kept.append(claim)
    return kept,rejected


def explicit_activity_from_text(text, registry):
    """Resolve an activity only when one registered activity is explicit."""
    if not isinstance(text, str) or not text.strip():
        return None
    key = normalized(text)
    matches=[]
    for row in getattr(registry, 'domain_classes', {}).values():
        if not registry.domain_is_a(row['id'], 'Activity'):
            continue
        names=[row.get('label',''), *row.get('aliases',[])]
        if any(name and normalized(name) in key for name in names):
            matches.append(row)
    if not matches:
        return None
    longest=max(len(normalized(row.get('label',''))) for row in matches)
    best=[row for row in matches if len(normalized(row.get('label',''))) == longest]
    return best[0]['label'] if len(best)==1 else None


def plan_tasks(plan,session,registry,catalog,*,use_skills=True,question='',strict_facts=False):
    """Canonical objects merge aliases; user facts stay in their own object bucket."""
    objects=list(dict.fromkeys(registry.canonical_label(str(x)) for x in plan.get('objects',[]) if isinstance(x,str) and x.strip()))
    objects=objects or session.last_objects or [question]
    specifications=plan.get('object_tasks') or [{'object':obj,'subquestion':question,'skill_ids':plan.get('skill_ids',[]),'knowledge_items':plan.get('knowledge_items',[])} for obj in objects]
    skills=[];tasks=[]
    for spec in specifications:
        if not isinstance(spec,dict) or not isinstance(spec.get('object'),str): continue
        obj=registry.canonical_label(spec['object'])
        if obj not in objects: objects.append(obj)
        selected_ids=([s['id'] for s in catalog] if len(catalog)==1 else [s['id'] for s in catalog if s['id'] in spec.get('skill_ids',plan.get('skill_ids',[]))]) if use_skills else []
        chosen=catalog.load_selected(selected_ids) if selected_ids and hasattr(catalog,'load_selected') else [s for s in catalog if s['id'] in selected_ids]
        if not use_skills or not chosen:
            items=[item for item in spec.get('knowledge_items',plan.get('knowledge_items',[])) if item in registry.statement_types]
            items=list(dict.fromkeys(items)) or ['requirement']
            chosen=[{'id':'general','version':'1','required_items':items,'relation_ids':registry.navigation(items),'instructions':''}]
        subquestion=spec.get('subquestion') or question
        requested=[item for item in spec.get('knowledge_items',plan.get('knowledge_items',[])) if item in registry.statement_types]
        available=list(dict.fromkeys(item for skill in chosen for item in skill['required_items']))
        # A model may choose a broadly relevant Skill but name a narrower
        # knowledge item owned by another Skill. Preserve the explicit item
        # instead of silently expanding it to every item in the first Skill.
        if use_skills and requested:
            for kind in requested:
                if kind in available:continue
                owner=next((skill for skill in catalog if kind in skill['required_items']),None)
                if owner:
                    loaded=catalog.load_selected([owner['id']]) if hasattr(catalog,'load_selected') else [owner]
                    chosen.extend(loaded)
                    available.extend(item for skill in loaded for item in skill['required_items'] if item not in available)
        items=[item for item in requested if item in available] or (['requirement'] if 'requirement' in available else available[:1])
        if use_skills:skills.extend(chosen)
        for kind in (items if use_skills else items[:1]):
            covering=[skill for skill in chosen if kind in skill['required_items']]
            relation_ids=(list(dict.fromkeys(r for skill in covering for r in skill['relation_ids'] if r in registry.navigation([kind]) and registry.relations[r].get('navigation',True))) if use_skills else registry.navigation(items))
            activity=spec.get('activity')
            if isinstance(activity,str) and activity.strip():
                activity_row=(registry.domain_class_by_id(activity)
                              if activity in getattr(registry,'domain_classes',{})
                              else registry.domain_class(activity))
                activity=activity_row['label'] if activity_row else activity.strip()
            else:
                activity=explicit_activity_from_text(subquestion,registry)
            phase=spec.get('phase')
            phase=phase.strip() if isinstance(phase,str) and phase.strip() else None
            raw_phase_terms=spec.get('phase_terms',[])
            raw_phase_terms=raw_phase_terms if isinstance(raw_phase_terms,list) else []
            phase_terms=[term.strip() for term in raw_phase_terms if isinstance(term,str) and term.strip()]
            query_terms=ranking_terms(spec.get('query_terms',[]))
            query_terms=list(dict.fromkeys([*query_terms,*([activity] if activity else []),*(phase_terms or ([phase] if phase else []))]))
            tasks.append({'id':identity('task',obj,subquestion,activity,phase,kind if use_skills else None),'object':obj,'subquestion':subquestion,
                          'activity':activity,'phase':phase,'phase_terms':phase_terms,
                          'intent':spec.get('intent') if isinstance(spec.get('intent'),str) else None,
                          'query_terms':query_terms,'knowledge_item':kind,'knowledge_items':[kind] if use_skills else items,'skill_ids':[s['id'] for s in covering] if use_skills else [],'relation_ids':relation_ids,'required':True,'lookup_attempted':False,'graph_lookup_attempted':False,'text_lookup_attempted':False,'coverage':'graph_not_found','applicability':'unknown','statement_ids':[],'evidence_ids':[],'missing_parameters':[]})
            tasks[-1].update(requires_site_inputs=spec.get('requires_site_inputs') is True,calculation_required=spec.get('calculation_required') is True)
    for update in plan.get('fact_updates',[]):
        if not isinstance(update,dict) or not isinstance(update.get('object'),str): continue
        obj=registry.canonical_label(update['object'])
        if obj in objects and isinstance(update.get('key'),str) and 'value' in update:
            quote=update.get('source_text')
            if strict_facts and (not isinstance(quote,str) or not quote or quote not in question): continue
            stored_value=deepcopy(update['value'])
            if isinstance(stored_value,str):
                from turbine_kg.knowledge.quantities import _unit
                match=re.fullmatch(r'\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+))\s*(\S.*?)\s*',
                                   unicodedata.normalize('NFKC',stored_value))
                if match and _unit(match.group(2)):
                    stored_value={'value':match.group(1),'unit':match.group(2)}
            value=stored_value.get('value') if isinstance(stored_value,dict) else stored_value
            if strict_facts:
                from turbine_kg.ontology.registry import normalized
                from turbine_kg.knowledge.quantities import normalize_quantity_text, canonical_unit
                if isinstance(stored_value,dict) and stored_value.get('unit'):
                    from turbine_kg.knowledge.quantities import quantity_in_text
                    if not quantity_in_text(value,str(stored_value['unit']),quote):continue
                    stored_value['unit']=canonical_unit(stored_value['unit'])
                    if isinstance(value,str):stored_value['value']=normalize_quantity_text(value)
                else:
                    from turbine_kg.knowledge.quantities import NUMBER_PATTERN
                    try: present=float(normalize_quantity_text(value)) in {float(n) for n in re.findall(NUMBER_PATTERN,normalize_quantity_text(quote))}
                    except (TypeError,ValueError): present=normalized(str(value)) in normalized(quote)
                    if not present: continue
            bucket=session.facts.setdefault(obj,{})
            provenance=session.fact_provenance.setdefault(obj,{})
            for key in bucket: provenance.setdefault(key,{'parameter_id':identity('parameter',registry.canonical_label(key)),'origin':'user','source_text':None})
            supplied=update.get('parameter_id')
            existing=next((key for key,record in provenance.items() if supplied and record['parameter_id']==supplied),None)
            key=existing or registry.canonical_label(update['key'])
            if strict_facts and isinstance(stored_value,dict):
                for field in ('target','location'):
                    named=stored_value.get(field)
                    if not isinstance(named,str) or not named.strip():
                        stored_value.pop(field,None);continue
                    choices=[named,*registry.expand(named)] if field=='target' else [named]
                    if not any(normalized(choice) in normalized(question) for choice in choices if choice):
                        stored_value.pop(field,None)
            bucket[key]=stored_value
            provenance[key]={'parameter_id':provenance.get(key,{}).get('parameter_id') or identity('parameter',registry.canonical_label(key)),'origin':'user','source_text':quote,'question':question}
    skills=list({fingerprint(s):s for s in skills}.values())
    merged={}
    for task in tasks:
        existing=merged.setdefault(task['id'],task)
        if existing is not task:
            for field in ('knowledge_items','relation_ids','query_terms'):
                existing[field]=list(dict.fromkeys([*existing.get(field,[]),*task.get(field,[])]))
    tasks=list(merged.values())
    session.last_objects=objects
    return tasks,skills,{obj:session.facts.get(obj,{}) for obj in objects}


def missing_parameters(expression,facts):
    if not isinstance(expression,dict): return []
    if expression.get('op') in ('and','or'): return list(dict.fromkeys(key for child in expression.get('items',[]) for key in missing_parameters(child,facts)))
    if expression.get('op')=='not': return missing_parameters(expression.get('item'),facts)
    key=expression.get('parameter')
    return [key] if key and key not in facts else []


def figure_ids(text):
    text=unicodedata.normalize('NFKC',text).replace('–','-').replace('—','-')
    return list(dict.fromkeys(re.findall(r'(?:图|figure)\s*(\d+(?:[.\-]\d+)+)',text,re.IGNORECASE)))


def safe_model_error(error):
    # ModelError contains transport codes/parser categories, never raw responses.
    return type(error).__name__+(': '+str(error) if isinstance(error,(ModelError,json.JSONDecodeError)) else '')


def planning_summary(result):
    """Small discovery metadata; these previews are never treated as citations."""
    value=result['result'];items=[]
    for item in value.get('items',[])[:6]:
        if 'members' in item:
            body=[m for m in item['members'] if m.get('role') not in ('context','caption')]
            items.append({'evidence_id':item['evidence_id'],'kind':item.get('kind'),'title':item.get('title'),'context':item.get('context',{}),'member_keys':[m['member_key'] for m in item['members']],'read_preview':'\n'.join(m.get('quote','')[:200] for m in body[:3])})
        else:
            compact={key:item[key] for key in ('id','evidence_id','type','kind','text','context','status','member_key','retrieval_role','retrieval_warning') if key in item}
            if 'supports' in item: compact['supports']=[{'evidence_id':s['evidence_id'],'member_keys':s.get('member_keys',[])} for s in item['supports']]
            if item.get('ontology_paths'):
                compact['ontology_paths']=[{'object_id':path['object_id'],'kind':path['kind'],
                                            'class_id':path.get('class_id')}
                                           for path in item['ontology_paths']]
            items.append(compact)
    return {'tool':result['tool'],'parameters':result.get('parameters',{}),'items':items,'coverage':value.get('coverage'),'cursor':value.get('cursor'),'warnings':value.get('warnings',[])}


def model_task_contracts(tasks):
    """Send the user's task and program checks, not mutable retrieval state."""
    fields=('id','object','subquestion','activity','phase','intent','query_terms',
            'knowledge_item','knowledge_items','calculation_required')
    result=[]
    for task in tasks:
        row={key:deepcopy(task[key]) for key in fields if task.get(key) is not None}
        if task.get('numeric_comparisons'):
            row['numeric_comparisons']=[{key:deepcopy(value) for key,value in check.items()
                if key in ('id','status','reason','target','parameter','location','actual','required',
                           'normalized_actual','comparison_unit','operator','limit','value','unit','supports')}
                for check in task['numeric_comparisons']]
            for check in row['numeric_comparisons']:
                if check.get('supports'):check['supports']=[{'evidence_id':sup['evidence_id'],'member_keys':sup.get('member_keys',[])} for sup in check['supports']]
        result.append(row)
    return result


def review_answer_completeness(question,tasks,claims,tools,model,budget,*,facts,reserve_seconds=0,gaps=None,retrieval_state=None,skills=None,review_cache=None,source_context=None):
    """Independently review advice against complete, already-read source units."""
    requested=[task for task in tasks if task.get('required') or any(c.get('task_id')==task['id'] for c in claims)]
    if not requested:return {},None
    payload={'question':question,'tasks':model_task_contracts(requested),'claims':claims,'gaps':gaps or [],
             'facts':facts,'retrieval_state':retrieval_state or {},
             **(source_context if source_context is not None else {'sources':model_source_views(tools.reads.values()),'grounding_context':[]})}
    key=fingerprint(payload)
    started=time.monotonic();cached=False
    try:
        if review_cache is not None and key in review_cache:
            response=review_cache[key];cached=True
        else:
            response=call_model(model,answer_review_prompt(use_skills=bool(skills)),json_text(payload),budget,reserve_seconds=reserve_seconds)
            if review_cache is not None:review_cache[key]=response
    except Exception as error:
        return {task['id']:{'complete':False,'claim_ids':[], 'rejected_claims':[],
            'missing_items':[],'external_dependency_pending':False,
            'internal_dependency_pending':False,
            'review_failure':{'kind':'review_unfinished','reason':'独立回答核验尚未完成，保留已有来源支持的建议'}}
            for task in requested},safe_model_error(error)
    finally:
        if hasattr(tools,'diagnostics'):
            tools.diagnostics.append({'kind':'answer_review','seconds':round(time.monotonic()-started,3),
                'input_characters':len(json_text(payload)),'evidence_count':len(payload['sources']),
                'claim_count':len(claims),'cached':cached})
    rows={row['task_id']:row for row in response.get('tasks',[]) if isinstance(row,dict) and row.get('task_id') in {task['id'] for task in requested}}
    result={}
    for task in requested:
        row=rows.get(task['id'],{});own=[claim for claim in claims if claim.get('task_id')==task['id']]
        verdicts={verdict['claim_id']:verdict for verdict in row.get('claim_verdicts',[]) if isinstance(verdict,dict) and verdict.get('claim_id') in {claim['id'] for claim in own}}
        accepted=[];rejected=[];unverified=[]
        for claim in own:
            verdict=verdicts.get(claim['id'])
            if not verdict:
                unverified.append(claim['id']);continue
            checks=[verdict.get(field) for field in ('supported','applicable','conditions_preserved')]
            if all(value is True for value in checks):accepted.append(claim['id'])
            elif any(value is False for value in checks):
                rejected.append({'claim_id':claim['id'],'reason':verdict.get('reason') or '独立核验发现来源支持或适用性不成立'})
            else:unverified.append(claim['id'])
        missing=[]
        for item in row.get('missing_items',[]):
            if not isinstance(item,dict) or not isinstance(item.get('reason'),str):continue
            supports=item.get('supports')
            if isinstance(supports,list) and supports and all(isinstance(sup,dict) and sup.get('evidence_id') in tools.reads and sup.get('member_keys') and set(sup['member_keys'])<={member['member_key'] for member in tools.reads[sup['evidence_id']]['members']} for sup in supports):
                missing.append({'reason':item['reason'],'supports':supports})
        supports=[support for claim in own if claim['id'] in accepted for support in claim.get('supports',[])]
        deferred,blocked=support_dependencies(supports,tools)
        complete=bool(own) and len(accepted)==len(own) and not missing and not blocked
        resolvable={gap.get('id') for gap in gaps or [] if gap.get('task_id')==task['id']
                    and gap.get('provenance')=='model' and gap.get('kind') in ('source_missing','answer_incomplete','known_omission','user_info_needed')}
        result[task['id']]={'complete':complete,'claim_ids':accepted,'rejected_claims':rejected,
            'resolved_gap_ids':[gid for gid in row.get('resolved_gap_ids',[]) if gid in resolvable] if accepted and not rejected and not unverified else [],
            'missing_items':missing,'external_dependency_pending':bool(deferred),
            'internal_dependency_pending':bool(blocked),'redundant_claims':row.get('redundant_claims',[]),
            'review_failure':({'kind':'review_incomplete','reason':'部分建议尚未完成独立核验'} if unverified or not row else
                              {'kind':'internal_dependency_pending','reason':'建议依赖的内部引用尚未读取'} if blocked else None)}
    return result,None


def apply_review_redundancy(claims,verdicts):
    """Remove only an explicitly declared duplicate with complete support retention."""
    accepted={claim['id']:claim for claim in claims if claim.get('id')};removed={}
    def bindings(claim):return {(s['evidence_id'],key) for s in claim.get('supports',[]) for key in s.get('member_keys',[])}
    for decision in verdicts.values():
        for row in decision.get('redundant_claims',[]):
            if not isinstance(row,dict) or row.get('fully_covered') is not True or row.get('conditions_preserved') is not True:continue
            cid=row.get('claim_id');cover=row.get('covered_by_claim_ids',[])
            if cid not in accepted or cid in removed or any(cid in covering for covering in removed.values()) or not isinstance(cover,list) or not cover or cid in cover or any(other not in accepted or other in removed for other in cover):continue
            source=accepted[cid]
            if any(accepted[other].get('task_id')!=source.get('task_id') or accepted[other].get('basis')!=source.get('basis') for other in cover):continue
            if not bindings(source)<={key for other in cover for key in bindings(accepted[other])}:continue
            removed[cid]=list(dict.fromkeys(cover))
    for decision in verdicts.values():
        decision['claim_ids']=list(dict.fromkeys(other for cid in decision.get('claim_ids',[]) for other in removed.get(cid,[cid])))
    return [claim for claim in claims if claim.get('id') not in removed],removed


def source_diagnostics(rows,reads):
    """Merge historical source diagnostics by document/version/page and kind."""
    result={}
    for row in rows:
        if not isinstance(row,dict):continue
        view=reads.get(row.get('evidence_id'),{})
        pages=[row['physical_page']] if row.get('physical_page') is not None else list(dict.fromkeys(m.get('locator',{}).get('physical_page') for m in view.get('members',[]) if m.get('locator',{}).get('physical_page') is not None))
        if not pages:
            key=fingerprint({k:v for k,v in row.items() if k!='evidence_id'});result.setdefault(key,dict(row));continue
        for page in pages:
            key=fingerprint((row.get('document_id') or view.get('document_id') or view.get('title'),row.get('revision_id') or view.get('revision_id'),page,row.get('kind'),row.get('state')))
            group=result.setdefault(key,{**row,'document_id':row.get('document_id') or view.get('document_id'),'revision_id':row.get('revision_id') or view.get('revision_id'),'physical_page':page,'evidence_ids':[],'reasons':[]})
            if row.get('evidence_id') and row['evidence_id'] not in group['evidence_ids']:group['evidence_ids'].append(row['evidence_id'])
            if row.get('reason') and row['reason'] not in group['reasons']:group['reasons'].append(row['reason'])
    return list(result.values())


def answer_source_ids(tasks,claims):
    relevant={support['evidence_id'] for claim in claims for support in claim.get('supports',[])}
    for task in tasks:
        if task.get('required') and task.get('answer_coverage')!='complete':
            checked=[*task.get('coverage_supports',[]),*[s for item in task.get('answer_missing_items',[]) for s in item.get('supports',[])]]
            relevant.update(support['evidence_id'] for support in checked)
            # A structural candidate is only a fallback until an independent
            # task check has named actual supporting or missing source members.
            if not checked:relevant.update(task.get('candidate_evidence_ids',[]))
    return relevant


def answer_reference_gaps(rows,tasks,claims,reads):
    relevant=answer_source_ids(tasks,claims);groups={};discarded=[]
    for row in rows:
        origin=row.get('origin_evidence_id')
        if origin is not None and origin not in relevant:
            discarded.append({**row,'kind':'reference_outside_answer','reference_gap_kind':row.get('kind')});continue
        view=reads.get(origin,{})
        key=fingerprint((view.get('document_id') or view.get('title'),view.get('revision_id'),row.get('kind'),row.get('reference_kind'),row.get('reference_number'),row.get('end_number')))
        group=groups.setdefault(key,{**row,'origin_evidence_ids':[]})
        if origin and origin not in group['origin_evidence_ids']:group['origin_evidence_ids'].append(origin)
    return list(groups.values()),discarded


def normalize_answer_gaps(rows,reference_gaps,reads):
    """Keep one known reference dependency and discard empty model placeholders."""
    placeholders={'','无','none','null','无缺口','无。'};result=[]
    canonical={}
    for ref in reference_gaps:
        if ref.get('kind')!='reference_deferred':continue
        token=(ref.get('reference_kind','clause'),str(ref.get('reference_number','')))
        canonical.setdefault(token,[]).append(ref)
    for original in rows:
        if not isinstance(original,dict):continue
        row={key:value for key,value in original.items() if key not in ('reason','question') or str(value).strip().lower() not in placeholders}
        if not any(str(row.get(key,'')).strip().lower() not in placeholders for key in ('reason','question','object')):continue
        tokens=_references(str(row.get('reason','')))
        if not tokens and any(word in str(row.get('reason','')) for word in ('引用','条款','章节')):
            tokens={token for token in canonical if re.search(r'(?<![\d.])'+re.escape(token[1])+r'(?![\d.])',str(row.get('reason','')))}
        # Merge only an explicitly identified pending reference, not missing
        # facts, user clarification, or a general statement about a whole page.
        question_tokens=_references(str(row.get('question','')))
        if not question_tokens and any(word in str(row.get('question','')) for word in ('引用','条款','条','规定')):
            question_tokens={token for token in canonical if re.search(r'(?<![\d.])'+re.escape(token[1])+r'(?![\d.])',str(row.get('question','')))}
        question=str(row.get('question',''))
        source_content_question=not row.get('reason') and bool(question_tokens) and bool(re.search(r'(规定|条文|内容|要求).*(是什么|有哪些|具体|说明)',question)) and not any(word in question for word in ('现场','当前','实测','实际'))
        if source_content_question:tokens=question_tokens
        reference_only_question=not row.get('question') or bool(question_tokens) and question_tokens<=tokens
        distinct_gap=row.get('kind') in ('user_info_needed','answer_incomplete','measure_condition_missing','measure_verification_missing') or bool(row.get('parameter')) or any(word in str(row.get('reason','')) for word in ('阶段错误','对象错误','数值错误','单位错误','不一致','不符合','正文遗漏'))
        if row.get('kind')!='reference_deferred' and reference_only_question and tokens and tokens<=canonical.keys() and not row.get('supports') and not distinct_gap:
            matching=[ref for token in tokens for ref in canonical[token]]
            documents={(reads.get(ref.get('origin_evidence_id'),{}).get('document_id'),reads.get(ref.get('origin_evidence_id'),{}).get('revision_id')) for ref in matching}
            if len(documents)==1 and (source_content_question or any(word in str(row.get('reason','')) for word in ('未','待','缺','暂','不完整'))):continue
        result.append(row)
    groups={}
    for row in result:
        if row.get('supports') and row.get('task_id'):
            kind='known_omission' if row.get('kind') in (None,'answer_incomplete','known_omission') else row.get('kind')
            key=fingerprint((row['task_id'],kind,sorted((support['evidence_id'],tuple(sorted(support.get('member_keys',[])))) for support in row['supports'])))
        else:key=fingerprint(row)
        group=groups.setdefault(key,dict(row))
        if row.get('reason') and group.get('reason')!=row['reason']:
            reasons=group.setdefault('reasons',[group.get('reason')]);
            if row['reason'] not in reasons:reasons.append(row['reason'])
    return list(groups.values())


def gap_category(gap):
    if gap.get('category') in ('source_missing','site_input','known_omission','system_incomplete'):return gap['category']
    if gap.get('kind')=='user_info_needed':return 'site_input'
    if gap.get('supports') and gap.get('kind') in ('answer_incomplete','known_omission'):return 'known_omission'
    if gap.get('kind')=='reference_unread' and gap.get('target_evidence_ids'):return 'system_incomplete'
    if gap.get('kind') in ('evidence','visual_unread','reference_ambiguous'):return 'system_incomplete'
    if gap.get('kind') in ('source_missing','reference_deferred','reference_unread','context_incomplete','unreadable','image_detail_unread'):return 'source_missing'
    return 'system_incomplete'


def unresolved_answer_gaps(gaps,tasks,claims):
    """Resolve known omissions only from independently verified accepted proofs."""
    accepted={claim['id']:claim for claim in claims if claim.get('id')};proofs={}
    for task in tasks:
        ids=task.get('answer_claim_ids',[])
        if task.get('answer_coverage')=='complete' and ids and all(cid in accepted for cid in ids):
            proofs[task['id']]={(support['evidence_id'],key) for cid in ids for support in accepted[cid].get('supports',[]) for key in support.get('member_keys',[])}
    result=[]
    for gap in gaps:
        tid=gap.get('task_id');supports=gap.get('supports',[])
        if gap.get('kind') in ('answer_incomplete','known_omission') and tid in proofs and supports and all(support.get('member_keys') for support in supports) and all((support['evidence_id'],key) in proofs[tid] for support in supports for key in support.get('member_keys',[])):continue
        if tid in proofs and gap.get('kind')=='answer_coverage':continue
        result.append({**gap,'category':gap_category(gap)})
    return result


def candidate_priority(item,result,tasks,question,registry):
    """Rank graph Statements by the task and the original question."""
    def strings(value):
        if isinstance(value,str):return [value]
        if isinstance(value,list):return [s for part in value for s in strings(part)]
        if isinstance(value,dict):return [s for part in value.values() for s in strings(part)]
        return []
    original_body=unicodedata.normalize('NFKC',str(item.get('text',''))).casefold()
    body=original_body+'\n'+unicodedata.normalize('NFKC',json_text(item.get('conditions',[]))).casefold()
    condition_terms={s.casefold() for s in strings(item.get('conditions',[])) if len(s.strip())>=2 and s.casefold() in question.casefold()}
    priorities=[]
    for task in tasks:
        if task['id'] not in result.get('task_ids',[]):continue
        broad={s.casefold() for s in [task['object'],*registry.expand(task['object'])]}
        exact_names={task['object'].casefold()}
        resolver=getattr(registry,'domain_class',None)
        domain=resolver(task['object']) if callable(resolver) else None
        if domain:
            exact_names.update(s.casefold() for s in [domain['label'],*domain.get('aliases',[])])
        else:
            exact_names.add(registry.canonical_label(task['object']).casefold())
        # Document/section Scope may mention many other equipment types. Only
        # this candidate's actual paragraph/Statement body establishes its object.
        object_match=any(term and term in original_body for term in exact_names)
        specific=set(relevance_terms(task.get('query_terms',[]),broad))
        hits=sum(term in body for term in specific)
        phase_hits=sum(term in body for term in condition_terms-specific)
        explicit_numbers=set(re.findall(r'(?<![\d.])\d+(?:\.\d+){2,}(?![\d.])',unicodedata.normalize('NFKC','\n'.join([question,task.get('subquestion',''),*task.get('query_terms',[])]))))
        source_numbers='\n'.join(str((item.get('context') or {}).get(key,'')) for key in ('clause_number','table_number'))
        direct_reference=any(re.search(r'(?<![\d.])'+re.escape(number)+r'(?![\d.])',original_body+'\n'+source_numbers) for number in explicit_numbers)
        path_kinds={row.get('kind') for row in item.get('ontology_paths',[]) if isinstance(row,dict)}
        path_rank=(4 if 'direct_class' in path_kinds or 'direct' in path_kinds else
                   3 if 'ancestor' in path_kinds else 2 if 'descendant' in path_kinds else
                   -1 if path_kinds=={'related_context'} else 1 if path_kinds else 0)
        kind=item.get('type');wanted=set(task.get('knowledge_items') or [task.get('knowledge_item')])
        direct=kind in wanted or kind=='step' and 'procedure' in wanted or (
            'parameter' in wanted and (bool(item.get('quantities')) or
            kind=='requirement' and bool(re.search(r'\d|％|%|不小于|不大于|超过|低于|高于|偏差',item.get('text','')))))
        if item.get('retrieval_method')=='evidence_fulltext':
            hits=max(hits,item.get('exact_hits',0))
            object_match=object_match or hits>0
        activity_match=task_statement_applicability(item,task,registry)
        activity_rank={'applicable':2,'unknown':0,'not_applicable':-2}[activity_match]
        priorities.append((int(direct_reference),int(object_match),path_rank,hits+phase_hits,activity_rank,int(direct)))
    return max(priorities,default=(0,0,0,0,0,0))


def pending_internal_references(tools):
    """Resolved original references stay pending even if a tool queue omitted them."""
    if hasattr(tools,'required_reference_ids'):
        # ``required_reference_ids`` is the initial discovery window.  Once a
        # section reference has been resolved, every target remains pending so
        # the applicability check can decide which targets support the task;
        # otherwise the first ranked child would silently become the whole
        # referenced section.
        return (set(tools.required_reference_ids) | set(getattr(tools,'pending_reference_ids',set()))) - tools.reads.keys()
    targets=set(getattr(tools,'pending_reference_ids',set()))
    for eid,view in tools.reads.items():
        for ref in view.get('context',{}).get('reference_resolution',[]):
            if ref.get('status')=='resolved':
                targets.update(ref.get('target_evidence_ids',[]))
    return targets-tools.reads.keys()


def competing_unread_sources(task,results,reads,question,registry):
    """Read equally relevant alternatives before a lexical match stops discovery."""
    priorities={}
    for result in results:
        if task['id'] not in result.get('task_ids',[]):continue
        for item in result['result'].get('items',[]):
            priority=candidate_priority(item,result,[task],question,registry)
            ids=([item['evidence_id']] if item.get('evidence_id') else [])+[s['evidence_id'] for s in item.get('supports',[]) if s.get('evidence_id')]
            for eid in ids:priorities[eid]=max(priorities.get(eid,(0,0,0,0,0,0)),priority)
    read_priorities=[priority for eid,priority in priorities.items() if eid in reads]
    if not read_priorities:return False
    best=max(read_priorities)
    return any(eid not in reads and (priority[0] or priority[2]>=4 or priority[1] and priority[3]>0)
               and priority>=best for eid,priority in priorities.items())


def model_grounding_context(statements,reads):
    """Group source-bound subjects and query roles without repeating source text."""
    grouped={}
    for statement in statements:
        supports=statement.get('supports',[])
        if not supports or any(support.get('evidence_id') not in reads or not support.get('member_keys')
            or not set(support['member_keys'])<={member['member_key'] for member in reads[support['evidence_id']]['members']}
            for support in supports):continue
        entities=[{'label':entity.get('surface_label') or entity.get('label'),'role':entity.get('role')}
                  for entity in statement.get('entities',[]) if entity.get('label') and entity.get('role')]
        kinds=list(dict.fromkeys(path['kind'] for path in statement.get('ontology_paths',[]) if path.get('kind')))
        if statement.get('retrieval_role')=='related_context':kinds=['related_context']
        if not entities and not kinds:continue
        for support in supports:
            key=(support['evidence_id'],tuple(support['member_keys']),statement.get('_object'))
            row=grouped.setdefault(key,{'evidence_id':support['evidence_id'],'member_keys':support['member_keys'],'entities':[]})
            if statement.get('_object'):row['query_object']=statement['_object']
            for entity in entities:
                if entity not in row['entities']:row['entities'].append(entity)
            if kinds:row['match_kinds']=list(dict.fromkeys([*row.get('match_kinds',[]),*kinds]))
    return list(grouped.values())


def statement_applicability(statement,facts,registry):
    """Intersect local conditions with the linked source scopes using AND."""
    if statement.get('retrieval_role')=='related_context':
        return 'unknown',[],[]
    expressions=[condition.get('expression') if isinstance(condition,dict) else None for condition in statement.get('conditions',[])]
    scopes={row.get('scope_id'):row for row in statement.get('scope_constraints',[]) if isinstance(row,dict) and row.get('scope_id')}
    scope_ids=list(dict.fromkeys([*statement.get('scope_ids',[]),*scopes]))
    unresolved=[]
    for scope_id in scope_ids:
        scope=scopes.get(scope_id,{})
        expression=scope.get('expression') if scope.get('status')=='parsed' else None
        expressions.append(expression)
        if not isinstance(expression,dict) or not expression:unresolved.append(scope_id)
    values=[evaluate(expression,facts,registry) for expression in expressions]
    status='not_applicable' if 'not_applicable' in values else 'applicable' if values and all(value=='applicable' for value in values) else 'unknown'
    missing=list(dict.fromkeys(key for expression in expressions for key in missing_parameters(expression,facts)))
    return status,missing,unresolved


def task_quantity_checks(tasks,statements,reads,facts,registry):
    """Compare explicit user measurements only after reading their sources."""
    from turbine_kg.knowledge.quantities import compare_quantity
    from turbine_kg.knowledge.evidence_limits import compare_read_evidence
    checks=[]
    for task in tasks:
        own=facts.get(task['object'],{})
        task['numeric_comparisons']=[]
        if not any(isinstance(value,dict) and 'value' in value for value in own.values()):continue
        for statement in statements:
            if statement.get('_object')!=task['object']:continue
            supports=statement.get('supports',[])
            if not supports or any(support.get('evidence_id') not in reads for support in supports):continue
            applicability,_,_=statement_applicability(statement,own,registry)
            if (statement.get('retrieval_role')!='related_context' and not statement.get('conditions')
                    and not statement.get('scope_ids') and not statement.get('scope_constraints')):
                applicability='applicable'
            for index,quantity in enumerate(statement.get('quantities',[])):
                result=compare_quantity(quantity,own,object_label=task['object'],
                                        registry=registry,applicability=applicability)
                result.update(id=identity('numeric-check',task['id'],statement['id'],index),
                              statement_id=statement['id'],task_id=task['id'],supports=supports)
                task['numeric_comparisons'].append(result);checks.append(result)
        if not task['numeric_comparisons']:
            for index,result in enumerate(compare_read_evidence(task,reads,facts,registry)):
                result.update(id=identity('numeric-check',task['id'],'evidence',index),task_id=task['id'])
                task['numeric_comparisons'].append(result);checks.append(result)
        if not task['numeric_comparisons']:
            result={'id':identity('numeric-check',task['id'],'missing'),'task_id':task['id'],
                    'status':'unknown','reason':'已读来源中尚无可供程序比较的明确限值','supports':[]}
            task['numeric_comparisons'].append(result);checks.append(result)
    return checks


class Agent:
    def __init__(self,store,*,model=None,registry=None):
        self.store=store;self.registry=registry or OntologyRegistry();self.model=model or ModelClient(store.settings)
        self.skill_catalog=load_catalog(registry=self.registry if hasattr(self.registry,'validate_skill') else None)

    def answer(self,question:str,session:Session|None=None,*,use_skills=False,budget:Budget|None=None,validation_snapshot=None):
        session=session or Session();budget=budget or Budget.configured()
        # The current document set keeps the ontology contract with which it
        # was built.  The code may already carry a newer registry.
        if (validation_snapshot is not None and validation_snapshot.ontology_version != self.registry.version
                and re.fullmatch(r'[0-9a-f]{64}',validation_snapshot.ontology_version)):
            pinned=OntologyRegistry.for_version(validation_snapshot.ontology_version)
            return Agent(self.store,model=self.model,registry=pinned).answer(
                question,session,use_skills=use_skills,budget=budget,validation_snapshot=validation_snapshot)
        versions=self.skill_catalog.versions() if use_skills else ()
        if validation_snapshot is not None:
            snapshot=validation_snapshot
        else:
            try:
                snapshot=self.store.snapshot(self.registry.version,versions,budget)
            except ValueError as error:
                if '有效知识与当前本体版本不一致' not in str(error):raise
                active=self.store.query(
                    "MATCH (d:TK:Document) RETURN DISTINCT d.ontology_version AS ontology",
                    budget=budget)
                versions_in_use={row.get('ontology') for row in active}
                if len(versions_in_use)!=1 or None in versions_in_use:raise
                pinned=OntologyRegistry.for_version(next(iter(versions_in_use)))
                return Agent(self.store,model=self.model,registry=pinned).answer(
                    question,session,use_skills=use_skills,budget=budget)
        if (snapshot.ontology_version != self.registry.version
                and re.fullmatch(r'[0-9a-f]{64}',snapshot.ontology_version)):
            pinned=OntologyRegistry.for_version(snapshot.ontology_version)
            return Agent(self.store,model=self.model,registry=pinned).answer(
                question,session,use_skills=use_skills,budget=budget,validation_snapshot=snapshot)
        tools=Tools(self.store,self.registry,snapshot,budget)
        seen=set();queue=[];statements={};answer_review_cache={};last_failed=None;repair_attempted=False;planning_failures=0;planning_error=None
        saved={'question':question,'results':[],'errors':[],'tasks':[],'skills':[],'facts':{},'claims':[],'gaps':[],'round':0}
        answer_reserve=min(150,budget.seconds*.625)

        def understand(state):
            try:
                instruction=understanding_prompt(use_skills=use_skills)
                payload={'question':question,'previous_objects':session.last_objects,'previous_user_facts':session.facts,'previous_fact_provenance':session.fact_provenance,'knowledge_types':self.registry.statement_types,'ontology_classes':[{'class_id':row['id'],'name':row.get('label',row.get('zh')),'definition':row.get('definition'),'parent':row.get('parent'),'aliases':row.get('aliases',[])} for row in [*getattr(self.registry,'business_classes',{}).values(),*getattr(self.registry,'domain_classes',{}).values()]],'skills':[{key:s[key] for key in ('id','description','required_items','relation_ids')} for s in self.skill_catalog] if use_skills else []}
                if not use_skills:payload.pop('skills')
                plan=call_model(self.model,instruction,json_text(payload),budget,reserve_seconds=answer_reserve)
                def selection_valid(value):
                    ids=[key for spec in value.get('object_tasks',[]) for key in spec.get('skill_ids',value.get('skill_ids',[]))] or value.get('skill_ids',[])
                    return bool(ids) and all(key in {s['id'] for s in self.skill_catalog} for key in ids)
                if use_skills and len(self.skill_catalog)>1 and not selection_valid(plan):
                    plan=call_model(self.model,instruction+invalid_skill_repair_prompt(),json_text({**payload,'invalid_plan':plan}),budget,reserve_seconds=answer_reserve)
                    if not selection_valid(plan):raise ValueError('Skill选择修复后仍无效，转原文任务')
            except BudgetExceeded: raise
            except Exception as error:
                state['errors'].append('问题理解模型：'+safe_model_error(error))
                terms=self.registry.expand(question)
                plan={'objects':list(dict.fromkeys(self.registry.canonical_label(t) for t in terms)) or session.last_objects or [question],'skill_ids':[],'knowledge_items':['requirement']}
            tasks,skills,facts=plan_tasks(plan,session,self.registry,self.skill_catalog,use_skills=use_skills,question=question,strict_facts=True)
            # Skill required_items are the same contract used for task creation and coverage.
            # A usable source kind is not a demand to interpret its pixels.
            required=plan.get('visual_required') is True
            figures=figure_ids(question)
            navigation=[]
            mentioned_labels=[]
            for domain in getattr(self.registry,'domain_classes',{}).values():
                if any(normalized(name) in normalized(question)
                       for name in [domain['label'],*domain.get('aliases',[]) ]):
                    mentioned_labels.append(domain['label'])
            relation_ids=list(dict.fromkeys(r for task in tasks for r in task['relation_ids']))
            if relation_ids and callable(getattr(self.store,'query',None)):
                try:
                    mentioned_labels.extend(row['label'] for row in self.store.query(
                        'MATCH (c:TK)-[r]->(s:TK:Statement) '
                        'WHERE r.document_id IN $documents AND r.relation_id IN $relations '
                        'AND c:OntologyClass '
                        'AND any(name IN [c.label]+coalesce(c.aliases,[]) '
                        'WHERE size(name)>1 AND $question CONTAINS name) '
                        'RETURN DISTINCT c.label AS label',
                        {'documents':list(snapshot.document_ids),'relations':relation_ids,'question':question},budget))
                except Exception:
                    pass
            mentioned_labels=list(dict.fromkeys(mentioned_labels))
            tools.focus_question=question
            tools.focus_tasks=tasks
            for obj in facts:
                own=[t for t in tasks if t['object']==obj];ids=[t['id'] for t in own]
                relations=list(dict.fromkeys(r for t in own for r in t['relation_ids']))
                if relations:
                    matches=[label for label in mentioned_labels if obj in label]
                    specific=[label for label in matches if not any(label!=other and label in other for other in matches)] or [obj]
                    for label in specific:
                        navigation.append({'tool':'navigate','parameters':{'object_label':label,'relation_ids':relations,'hops':budget.business_hops},'task_ids':ids})
            # Text recall has its own entrance even when classification or
            # Statement extraction missed the relevant knowledge.
            for task in tasks:
                queue.append({'tool':'search_evidence','parameters':{
                    'query':task['subquestion'] or question,
                    'query_terms':list(dict.fromkeys([task['object'],*task['query_terms']]))},
                    'task_ids':[task['id']]})
            queue.extend(navigation)
            distinct={}
            for call in queue:
                key=fingerprint((call['tool'],call['parameters']))
                if key in distinct: distinct[key]['task_ids']=list(dict.fromkeys(distinct[key].get('task_ids',[])+call.get('task_ids',[])))
                else: distinct[key]=call
            queue[:]=distinct.values()
            update={'tasks':tasks,'skills':skills,'facts':facts,'visual_required':required,'figure_ids':figures,'gaps':[],'round':0}
            state.update(update);saved.update(state)
            return update

        def candidates(state):
            priorities={};per_task={}
            for result in state['results']:
                for item in result['result'].get('items',[]):
                    priority=candidate_priority(item,result,state['tasks'],question,self.registry)
                    # Preserve the text retriever's ordering without presenting
                    # search projections as source quotations.
                    priority=(*priority,item.get('exact_hits',0),item.get('score',0))
                    values=[]
                    if item.get('evidence_id'):
                        values.append((item['evidence_id'],priority))
                    values += [(s['evidence_id'],priority) for s in item.get('supports',[]) if s.get('evidence_id')]
                    for eid,priority in values:
                        if eid not in priorities or priority>priorities[eid]: priorities[eid]=priority
                        for tid in result.get('task_ids',[]): per_task.setdefault(tid,set()).add(eid)
            pending_references=pending_internal_references(tools)
            references=[eid for eid in getattr(tools,'pending_reference_order',[]) if eid in pending_references]
            references.extend(sorted(pending_references-set(references)))
            ranked=list(dict.fromkeys(references+sorted(priorities,key=lambda eid:priorities[eid],reverse=True)))
            unread=[eid for eid in ranked if eid not in tools.reads and fingerprint({'tool':'read_evidence','parameters':{'evidence_id':eid}}) not in seen]
            fair=[];groups={}
            for task in state['tasks']:
                if task.get('coverage')!='evidence_found': groups.setdefault((task['object'],task['subquestion']),set()).update(per_task.get(task['id'],set()))
            for ids in sorted(groups.values(),key=lambda ids:sum(eid in tools.reads for eid in ids)):
                eid=next((eid for eid in unread if eid in ids and eid not in fair),None)
                if eid: fair.append(eid)
            required=[eid for eid in unread if eid in pending_references]
            return required+[eid for eid in fair if eid not in pending_references]+[eid for eid in unread if eid not in fair and eid not in pending_references]

        def coverage(state):
            """Track read candidates; semantic applicability is checked after generation."""
            for task in state['tasks']:
                relevant=[s for s in statements.values() if s.get('_object')==task['object'] and (
                    s.get('type') in (task.get('knowledge_items') or [task['knowledge_item']]) or
                    (s.get('type')=='step' and 'procedure' in (task.get('knowledge_items') or [task['knowledge_item']])) or
                    ('parameter' in (task.get('knowledge_items') or [task['knowledge_item']]) and (s.get('quantities') or
                     s.get('type')=='requirement' and re.search(r'\d|％|%|不小于|不大于|超过|低于|高于|偏差',s.get('text','')))))]
                ids={support['evidence_id'] for statement in relevant for support in statement.get('supports',[]) if support.get('evidence_id')}
                for result in state['results']:
                    if task['id'] not in result.get('task_ids',[]):continue
                    ids.update(item['evidence_id'] for item in result['result'].get('items',[]) if item.get('evidence_id'))
                supports=[{'evidence_id':eid,'member_keys':[member['member_key'] for member in tools.reads[eid]['members']]}
                          for eid in sorted(ids) if eid in tools.reads]
                read_ids={support['evidence_id'] for support in supports}
                read_statements=[s for s in relevant if s.get('supports') and all(support.get('evidence_id') in read_ids for support in s['supports'])]
                task.update(candidate_statement_ids=list(dict.fromkeys(s['id'] for s in relevant)),
                    candidate_evidence_ids=sorted(ids),coverage='evidence_found' if supports else 'pending_read' if ids else 'graph_not_found',
                    statement_ids=[s['id'] for s in read_statements],evidence_ids=sorted(read_ids),
                    coverage_supports=supports,
                    applicability='unknown',missing_parameters=[])
                applications=[];missing=[]
                for statement in read_statements:
                    status,needed,unresolved=statement_applicability(statement,state['facts'].get(task['object'],{}),self.registry)
                    applications.append(status);missing.extend(needed)
                    if unresolved:tools.diagnostics.append({'kind':'source_scope_unknown','object':task['object'],'statement_id':statement['id'],'scope_ids':unresolved,'reason':'来源范围条件保持未知，由回答及独立核验结合原文判断'})
                task['applicability']='applicable' if 'applicable' in applications else 'not_applicable' if applications and all(value=='not_applicable' for value in applications) else 'unknown'
                task['missing_parameters']=list(dict.fromkeys(missing))
            saved.update(state)

        def pending_visuals(state):
            if not state.get('visual_required'): return []
            pending=[]
            for eid,view in tools.reads.items():
                if not any(m.get('source_kind')=='picture' for m in view['members']) or eid in tools.visuals: continue
                labels=str(view.get('context',{}).get('figure_title') or '')+'\n'+'\n'.join(m.get('quote','') for m in view['members'] if m.get('role') in ('caption','figure_reference'))
                if state.get('figure_ids') and not set(state['figure_ids'])&set(figure_ids(labels)): continue
                if fingerprint({'tool':'read_visual','parameters':{'evidence_id':eid}}) not in seen: pending.append(eid)
            return pending

        def sources_sufficient(state):
            needed=[task for task in state['tasks'] if task.get('required')] or state['tasks']
            if not needed or not all(t.get('coverage')=='evidence_found' and t.get('lookup_attempted') for t in needed) or pending_visuals(state):return False
            if pending_navigation_pages(needed):return False
            if any(competing_unread_sources(task,state['results'],tools.reads,question,self.registry) for task in needed):return False
            origins={support['evidence_id'] for task in needed for support in task.get('coverage_supports',[])}
            pending=pending_internal_references(tools);known=set()
            for gap in tools.reference_gaps:
                targets=set(gap.get('target_evidence_ids',[]));known.update(targets)
                if gap.get('kind')=='reference_unread' and (gap.get('origin_evidence_id') is None or gap.get('origin_evidence_id') in origins) and (not targets or pending.intersection(targets)):return False
            return not (pending-known)

        def sources_ready_for_answer(state):
            """Generate from relevant complete raw views; final review decides completeness."""
            if pending_visuals(state) or pending_internal_references(tools):return False
            if (state.get('round',0)<2 and
                    any(eid not in tools.reads for eid in getattr(tools,'reference_targets',{}))):return False
            needed=[task for task in state['tasks'] if task.get('required')] or state['tasks']
            if pending_navigation_pages(needed):return False
            for task in needed:
                if not task.get('lookup_attempted'):return False
                if competing_unread_sources(task,state['results'],tools.reads,question,self.registry):return False
                if task.get('coverage')=='evidence_found':continue
                aliases=[term.casefold() for term in [task['object'],*self.registry.expand(task['object'])] if term]
                specific=relevance_terms(task.get('query_terms',[]),aliases)
                related=False
                for view in tools.reads.values():
                    if any(w.get('state') in ('unreadable','context_incomplete') for w in view.get('source_warnings',[])):continue
                    body='\n'.join(m.get('quote','') for m in view.get('members',[]) if m.get('role') in ('content','value','row_label','note')).casefold()
                    if any(term in body for term in aliases) and (not specific or any(term in body for term in specific)):related=True;break
                if not related:return False
            return bool(needed)

        def retrieval_time_available():
            return budget.remaining()>answer_reserve

        def pending_navigation_pages(tasks):
            task_ids={task['id'] for task in tasks}
            return any((call.get('tool')=='navigate' or call.get('tool')=='search_evidence' and not call.get('parameters',{}).get('cursor'))
                       and task_ids.intersection(call.get('task_ids',[]))
                       and fingerprint({'tool':call['tool'],'parameters':call['parameters']}) not in seen
                       for call in queue)

        def retrieval_state(state):
            pending=[call for call in queue if fingerprint({'tool':call['tool'],'parameters':call['parameters']}) not in seen]
            return {'stop_reason':state.get('stop_reason'),'unread_evidence_ids':candidates(state),
                    'unread_reference_targets':[eid for eid in getattr(tools,'reference_targets',{}) if eid not in tools.reads],
                    'pending_queries':[{'tool':call['tool'],'parameters':call['parameters'],'task_ids':call.get('task_ids',[])} for call in pending]}

        def execute(call,state,results):
            nonlocal last_failed,repair_attempted
            signature=fingerprint({'tool':call.get('tool'),'parameters':call.get('parameters',{})})
            if signature in seen: return False
            seen.add(signature)
            try:
                if call.get('tool') not in ('navigate','search_evidence','read_evidence','table_rows','read_visual'):
                    raise ValueError('未知来源工具')
                result=tools.dispatch(call['tool'],call.get('parameters',{}))
                task_ids=call.get('task_ids')
                if task_ids is None: task_ids=[t['id'] for t in state['tasks'] if call['tool']=='search_evidence' or call.get('parameters',{}).get('object_label')==t['object']]
                results.append({'tool':call['tool'],'parameters':call.get('parameters',{}),'task_ids':task_ids,'result':result})
                state['results'].append(results[-1])
                if call['tool'] in ('navigate','search_evidence'):
                    found=result.get('items',[]) if call['tool']=='navigate' else [stmt for item in result.get('items',[]) for stmt in item.get('statements',[])]
                    for stmt in found:
                        for task in state['tasks']:
                            if task['id'] in task_ids:
                                key=identity('candidate',stmt['id'],task['object'])
                                previous=statements.get(key,{})
                                candidate={**previous,**stmt,'_object':task['object']}
                                if call['tool']=='search_evidence' and previous.get('ontology_paths'):
                                    for field in ('ontology_paths','matched_objects','retrieval_role','retrieval_warning'):
                                        if field in previous:candidate[field]=previous[field]
                                        else:candidate.pop(field,None)
                                statements[key]=candidate
                    if result.get('cursor'):
                        queue.append({'tool':call['tool'],'parameters':{**call['parameters'],'cursor':result['cursor']},'task_ids':task_ids})
                for task in state['tasks']:
                    parameters=call.get('parameters',{})
                    if task['id'] in task_ids:
                        if call['tool']=='navigate' and set(task['relation_ids'])<=set(parameters.get('relation_ids',[])):
                            task['graph_lookup_attempted']=True
                        if call['tool']=='search_evidence':task['text_lookup_attempted']=True
                        task['lookup_attempted']=(task['text_lookup_attempted'] and
                            (task['graph_lookup_attempted'] or not task['relation_ids']))
                if repair_attempted: last_failed=None;repair_attempted=False
                return True
            except BudgetExceeded: raise
            except Exception as error:
                state['errors'].append(call.get('tool','unknown')+': '+type(error).__name__+': '+str(error))
                if not repair_attempted and last_failed is None: last_failed={'call':call,'error':str(error)}
                elif repair_attempted:last_failed=None;repair_attempted=False
                return False

        def retrieve(state):
            nonlocal repair_attempted,last_failed,planning_failures,planning_error
            round_number=state['round']+1;results=[];start=budget.calls;model_stop=False
            tools.round_call_ceiling=min(round_number*budget.calls_per_round,start+budget.calls_per_round)
            try:
                if sources_sufficient(state) or sources_ready_for_answer(state) or not retrieval_time_available():
                    state.update(round=state['round'],stop=True,stop_reason='sources_sufficient' if sources_sufficient(state) else 'answer_sources_ready' if sources_ready_for_answer(state) else 'answer_time_reserved')
                    saved.update(state)
                    return {'round':state['round'],'stop':True,'stop_reason':state['stop_reason'],'results':state['results'],'tasks':state['tasks'],'errors':state['errors']}
                # Required pixel reads are source obligations, independent of a
                # successful model-directed follow-up tool plan.
                for eid in pending_visuals(state):
                    if budget.calls-start>=budget.calls_per_round or not retrieval_time_available(): break
                    execute({'tool':'read_visual','parameters':{'evidence_id':eid}},state,results)
                # Navigate each requested object before reading its source groups.
                discovery=0
                while queue and discovery<2 and budget.calls-start<budget.calls_per_round and retrieval_time_available():
                    execute(queue.pop(0),state,results);discovery+=1
                unread=candidates(state)
                while unread and budget.calls-start<budget.calls_per_round and retrieval_time_available() and not sources_sufficient(state) and not sources_ready_for_answer(state):
                    execute({'tool':'read_evidence','parameters':{'evidence_id':unread.pop(0)}},state,results)
                    for eid in pending_visuals(state):
                        if budget.calls-start>=budget.calls_per_round or not retrieval_time_available(): break
                        execute({'tool':'read_visual','parameters':{'evidence_id':eid}},state,results)
                    unread=candidates(state)
                    if retrieval_time_available():coverage(state)
                if retrieval_time_available():coverage(state)
                # A genuine model-directed follow-up chooses graph paths, page cursors,
                # raw text or pixels based on the remaining per-object knowledge tasks.
                if round_number>1 and budget.calls-start<budget.calls_per_round and retrieval_time_available() and not candidates(state) and not sources_sufficient(state) and not sources_ready_for_answer(state):
                    try:
                        tool_description='search_evidence(query,query_terms,cursor)、read_evidence(evidence_id)、table_rows(table_id,cursor)、read_visual(evidence_id)、navigate(object_label,relation_ids,cursor,hops)'
                        instruction=retrieval_prompt(tool_description,use_skills=use_skills)
                        relations={key:{field:value[field] for field in ('zh','domain','range','statement_types') if field in value} for key,value in self.registry.relations.items() if value.get('navigation',True)}
                        decision=call_model(self.model,instruction,json_text({'question':question,'tasks':model_task_contracts(state['tasks']),'skills':task_skill_context(state['skills'],state['tasks']),'registered_relations':relations,'recent_results':[planning_summary(r) for r in state['results'][-6:]],'unread_candidates':candidates(state)[:8],
                            'unread_reference_targets':[{'evidence_id':eid,**target} for eid,target in getattr(tools,'reference_targets',{}).items() if eid not in tools.reads][:12],
                            'read_evidence_ids':list(tools.reads),'read_visual_ids':list(tools.visuals),'tool_error_to_repair_once':last_failed,'planning_error_to_repair_once':planning_error,'remaining_calls_this_round':budget.calls_per_round-(budget.calls-start),'remaining_seconds':budget.remaining()}),budget,reserve_seconds=answer_reserve)
                        planning_failures=0;planning_error=None
                        if last_failed: repair_attempted=True
                        model_stop=bool(decision.get('stop'))
                        for call in decision.get('calls',[]):
                            if budget.calls-start>=budget.calls_per_round or not retrieval_time_available(): break
                            if isinstance(call,dict): execute(call,state,results)
                        # Newly discovered sources can be read in this same round,
                        # including the last round, within its existing call limit.
                        for eid in candidates(state):
                            if budget.calls-start>=budget.calls_per_round or not retrieval_time_available() or sources_sufficient(state) or sources_ready_for_answer(state): break
                            execute({'tool':'read_evidence','parameters':{'evidence_id':eid}},state,results)
                            if retrieval_time_available():coverage(state)
                        if repair_attempted and last_failed:last_failed=None;repair_attempted=False
                    except BudgetExceeded: raise
                    except Exception as error:
                        state['errors'].append('工具规划：'+safe_model_error(error))
                        planning_failures+=1;planning_error=safe_model_error(error)
                        if last_failed or planning_failures>=2:
                            last_failed=None;repair_attempted=False;planning_error=None
                    if retrieval_time_available():coverage(state)
                unread=candidates(state)
                mandatory_pending=bool(unread) or any(t.get('required') and (not t['lookup_attempted'] or (t['coverage']!='evidence_found' and any(t['id'] in call.get('task_ids',[]) for call in queue))) for t in state['tasks'])
                stop_reason='sources_sufficient' if sources_sufficient(state) else 'answer_sources_ready' if sources_ready_for_answer(state) else 'answer_time_reserved' if not retrieval_time_available() else 'round_limit' if round_number>=budget.max_rounds else 'model_stop' if model_stop and not pending_visuals(state) and not mandatory_pending else 'no_progress' if not results and not queue and not unread and not pending_visuals(state) else None
                state.update(round=round_number,stop=stop_reason is not None,stop_reason=stop_reason)
            except BudgetExceeded as error:
                state['errors'].append(str(error));state.update(stop=True,round=round_number,stop_reason='budget_limit')
                coverage(state)
            saved.update(state)
            return {'round':state['round'],'stop':state['stop'],'stop_reason':state.get('stop_reason'),'results':state['results'],'tasks':state['tasks'],'errors':state['errors']}

        def generate(state):
            kept=[];rejected=[]
            remaining=retrieval_state(state)
            if any(t.get('required') and t['coverage']!='evidence_found' for t in state['tasks']) and (remaining['pending_queries'] or remaining['unread_evidence_ids']):
                state['gaps'].append({'kind':'retrieval_pending','reason':'本轮预算内仍有原文查询或候选未读，不能据此断言资料不存在','stop_reason':remaining['stop_reason'],'unread_evidence_ids':remaining['unread_evidence_ids'],'pending_queries':remaining['pending_queries']})
            if not tools.reads:
                state['gaps'].append({'kind':'evidence','reason':'当前检索尚未读到可引用来源'})
                return {'claims':[],'gaps':state['gaps']}
            phase='generation';proposed=[]
            try:
                task_quantity_checks(state['tasks'],list(statements.values()),
                                     tools.reads,state['facts'],self.registry)
                from turbine_kg.knowledge.evidence_limits import compare_read_evidence_semantically
                for task in state['tasks']:
                    original=task.get('numeric_comparisons',[])
                    aligned=compare_read_evidence_semantically(task,tools.reads,state['facts'],
                        self.registry,self.model,budget,existing_checks=original,question=question,
                        reserve_seconds=min(90,budget.seconds*.375))
                    task['numeric_comparisons']=[{**before,**after} for before,after in zip(original,aligned)]
                numeric_checks=[check for task in state['tasks'] for check in task.get('numeric_comparisons',[])]
                tools.diagnostics.extend({'kind':'numeric_comparison',**check} for check in numeric_checks)
                source_context={'sources':model_source_views(tools.reads.values()),
                                'grounding_context':model_grounding_context(statements.values(),tools.reads)}
                generation_system=answer_prompt(use_skills=bool(state['skills']))
                generation_payload=json_text({'question':question,'tasks':model_task_contracts(state['tasks']),'skills':task_skill_context(state['skills'],state['tasks']),'facts':state['facts'],'fact_provenance':{obj:session.fact_provenance.get(obj,{}) for obj in state['facts']},**source_context,'image_reads':tools.visuals,'retrieval_state':remaining})
                generation_reserve=min(60,budget.seconds*.25)
                response=call_model(self.model,generation_system,generation_payload,budget,reserve_seconds=generation_reserve)
                response['gaps']=[{**gap,'id':gap.get('id') or identity('model-gap',gap),'provenance':'model'} for gap in response.get('gaps',[]) if isinstance(gap,dict)]
                taskids={task['id'] for task in state['tasks']}
                proposed_context=proposed_source_bindings(response.get('claims',[]),tools.reads)
                proposed=[bind_source_context(c,tools.reads,origin_bindings=proposed_context.get(c.get('task_id'),{})) for c in response.get('claims',[]) if isinstance(c,dict) and c.get('task_id') in taskids]
                tools.diagnostics.append({'kind':'generation_contract','stage':'generation','attempt':1,'generated_claims':len(response.get('claims',[])),'task_bound_claims':len(proposed)})
                has_relevant_evidence=any(
                    task.get('required') and task.get('coverage')=='evidence_found'
                    and any(support.get('evidence_id') in tools.reads for support in task.get('coverage_supports',[]))
                    for task in state['tasks'])
                if not proposed and has_relevant_evidence:
                    tools.diagnostics.append({'kind':'empty_generation_retry','stage':'generation','status':'triggered'})
                    retry_system=generation_system+' 已读来源支持至少一个必需子问题，但上一轮没有生成与任务绑定的陈述。请仅针对已读来源和现有task_id重新生成一次；不能补造事实或跳过精确成员引用。'
                    response=call_model(self.model,retry_system,generation_payload,budget,reserve_seconds=generation_reserve)
                    response['gaps']=[{**gap,'id':gap.get('id') or identity('model-gap',gap),'provenance':'model'} for gap in response.get('gaps',[]) if isinstance(gap,dict)]
                    proposed_context=proposed_source_bindings(response.get('claims',[]),tools.reads)
                    proposed=[bind_source_context(c,tools.reads,origin_bindings=proposed_context.get(c.get('task_id'),{})) for c in response.get('claims',[]) if isinstance(c,dict) and c.get('task_id') in taskids]
                    tools.diagnostics.append({'kind':'generation_contract','stage':'generation','attempt':2,'generated_claims':len(response.get('claims',[])),'task_bound_claims':len(proposed)})
                    tools.diagnostics.append({'kind':'empty_generation_retry','stage':'generation','status':'produced' if proposed else 'empty','generated_claims':len(response.get('claims',[])),'task_bound_claims':len(proposed)})
                    if not proposed:
                        response['gaps']=[gap for gap in response['gaps'] if gap.get('kind')!='source_missing']
                phase='claim_binding'
                kept,rejected=validate(proposed,tools,facts=state['facts'],tasks=state['tasks'])
                kept,scope_rejected=filter_independent_source_claims(
                    kept,state['tasks'],tools.reads,statements.values(),self.registry,
                    tools.reference_target_links,question=question)
                rejected.extend(scope_rejected)
                tools.diagnostics.extend({'kind':'claim_binding_result','stage':phase,'claim_id':row.get('claim',{}).get('id'),'reason':row['reason'],**binding_diagnostics(row.get('claim',{}),tools.reads)} for row in rejected)
                state['claims']=kept;saved.update(state)
                phase='completeness_review'
                final_coverage,coverage_error=review_answer_completeness(question,state['tasks'],kept,tools,self.model,budget,facts=state['facts'],gaps=response['gaps'],retrieval_state=remaining,skills=state['skills'],review_cache=answer_review_cache,source_context=source_context)
                semantic_rejections={item['claim_id']:item['reason'] for row in final_coverage.values() for item in row.get('rejected_claims',[])}
                rejected.extend({'claim':claim,'reason':semantic_rejections[claim['id']]} for claim in kept if claim['id'] in semantic_rejections)
                kept=[claim for claim in kept if claim['id'] not in semantic_rejections]
                missing=[{'task_id':tid,**item} for tid,row in final_coverage.items() for item in row['missing_items']]
                for gap in response.get('gaps',[]):
                    if not isinstance(gap,dict) or gap.get('kind') not in ('answer_incomplete','known_omission') or gap.get('task_id') not in taskids:continue
                    supports=gap.get('supports',[])
                    if supports and all(isinstance(s,dict) and s.get('evidence_id') in tools.reads and s.get('member_keys') and set(s['member_keys'])<={m['member_key'] for m in tools.reads[s['evidence_id']]['members']} for s in supports):missing.append(gap)
                missing=normalize_answer_gaps(missing,[],tools.reads)
                if rejected or missing:
                    try:
                        budget.remaining()
                        phase='repair_generation'
                        repair_ids={row['task_id'] for row in missing}|{row.get('claim',{}).get('task_id') for row in rejected}
                        repair_tasks=[task for task in state['tasks'] if task['id'] in repair_ids]
                        repaired=call_model(self.model,repair_prompt(use_skills=bool(state['skills'])),json_text({'original_question':question,'task_contracts':model_task_contracts(repair_tasks),'selected_skills':task_skill_context(state['skills'],repair_tasks),'accepted_claims':kept,'rejected':rejected,'missing_items':missing,**source_context,'image_reads':tools.visuals,'facts':state['facts']}),budget,reserve_seconds=min(30,budget.seconds*.125))
                        repair_context=proposed_source_bindings([*kept,*repaired.get('claims',[])],tools.reads)
                        fixes=[bind_source_context(c,tools.reads,origin_bindings=repair_context.get(c.get('task_id'),{})) for c in repaired.get('claims',[]) if isinstance(c,dict) and c.get('task_id') in taskids]
                        phase='repair_binding'
                        fixed,failed=validate(fixes,tools,facts=state['facts'],tasks=state['tasks'])
                        fixed,scope_failed=filter_independent_source_claims(
                            fixed,state['tasks'],tools.reads,statements.values(),self.registry,
                            tools.reference_target_links,question=question)
                        failed.extend(scope_failed);kept.extend(fixed);rejected=failed
                        tools.diagnostics.extend({'kind':'claim_binding_result','stage':phase,'claim_id':row.get('claim',{}).get('id'),'reason':row['reason'],**binding_diagnostics(row.get('claim',{}),tools.reads)} for row in failed)
                        kept=list({fingerprint((c['text'],c['supports'])):c for c in kept}.values())
                        phase='repair_completeness_review'
                        final_coverage,coverage_error=review_answer_completeness(question,state['tasks'],kept,tools,self.model,budget,facts=state['facts'],gaps=response['gaps'],retrieval_state=remaining,skills=state['skills'],review_cache=answer_review_cache,source_context=source_context)
                    except Exception as error:
                        state['errors'].append('陈述修正：'+safe_model_error(error))
                        state['gaps'].append({'kind':'system_incomplete','stage':phase,'reason':'已知遗漏的自动补答尚未完成','error':safe_model_error(error)})
                semantic_rejections={item['claim_id']:item['reason'] for row in final_coverage.values() for item in row.get('rejected_claims',[])}
                rejected.extend({'claim':claim,'reason':semantic_rejections[claim['id']]} for claim in kept if claim['id'] in semantic_rejections)
                kept=[claim for claim in kept if claim['id'] not in semantic_rejections]
                kept,removed=apply_review_redundancy(kept,final_coverage)
                if removed:tools.diagnostics.append({'kind':'reviewed_redundancy','removed_claims':removed})
                resolved_gaps={gid for decision in final_coverage.values() for gid in decision.get('resolved_gap_ids',[])}
                response['gaps']=[gap for gap in response['gaps'] if gap.get('id') not in resolved_gaps]
                if coverage_error:
                    state['errors'].append('最终答案覆盖核验：'+coverage_error)
                    state['gaps'].append({'kind':'system_incomplete','stage':'completeness_review','reason':'最终答案完整性核验尚未完成，已逐条核验内容保留','error':coverage_error})
                for task in state['tasks']:
                    decision=final_coverage.get(task['id'])
                    task['answer_coverage']='complete' if decision and decision['complete'] else 'incomplete' if decision else 'unverified'
                    task['answer_missing_items']=decision['missing_items'] if decision else []
                    task['answer_claim_ids']=decision['claim_ids'] if decision else []
                    task['answer_external_dependency_pending']=decision['external_dependency_pending'] if decision else False
                    task['answer_review_failure']=decision.get('review_failure') if decision else None
                    if decision and decision['missing_items']:
                        state['gaps'].extend({'kind':'answer_incomplete','object':task['object'],'subquestion':task['subquestion'],'task_id':task['id'],**item} for item in decision['missing_items'])
                    elif task.get('required') and task['answer_coverage']!='complete':
                        review_failure=decision.get('review_failure') if decision else None
                        if review_failure:
                            state['gaps'].append({'kind':'system_incomplete','stage':'completeness_review','object':task['object'],'subquestion':task['subquestion'],'task_id':task['id'],'review_status':review_failure['kind'],'reason':review_failure['reason']})
                            state['errors'].append('最终答案覆盖核验合同：'+review_failure['reason'])
                        elif not (decision and (decision.get('external_dependency_pending') or decision.get('internal_dependency_pending'))):
                            state['gaps'].append({'kind':'answer_coverage','object':task['object'],'subquestion':task['subquestion'],'task_id':task['id'],'reason':'该子问题的最终回答尚未确认完整，已核验部分保留'})
                for gap in response.get('gaps',[]):
                    if not isinstance(gap,dict):continue
                    task=next((task for task in state['tasks'] if task['id']==gap.get('task_id')),None)
                    if gap.get('kind')=='user_info_needed':
                        tools.diagnostics.append({**gap,'kind':'unnecessary_site_input'});continue
                    state['gaps'].append(gap)
            except Exception as error:
                state['errors'].append('回答生成或核验：'+safe_model_error(error))
                state['gaps'].append({'kind':'system_incomplete','stage':phase,'reason':'本次回答处理未完成，已读来源保留','error':safe_model_error(error)})
            for task in state['tasks']:
                if task.get('answer_coverage')=='complete' and not set(task.get('answer_claim_ids',[])).issubset({c['id'] for c in kept}):task['answer_coverage']='unverified'
            for claim in kept:
                for task in state['tasks']:
                    if task['id']==claim.get('task_id'):
                        if claim.get('knowledge_item') not in (task.get('knowledge_items') or [task['knowledge_item']]):
                            claim['knowledge_item']=task['knowledge_item']
                        # A valid partial answer cannot replace verification of
                        # all knowledge requested by the corresponding task.
                        task.setdefault('validated_claim_ids',[]).append(claim['id'])
            tools.diagnostics.extend({'kind':'discarded_claim','stage':'claim_binding','reason':row['reason'],**binding_diagnostics(row.get('claim',{}),tools.reads)} for row in rejected)
            if not kept:
                    state['gaps'].append({'kind':'system_incomplete','stage':'claim_binding' if rejected or proposed else 'generation','reason':'本次尚无通过程序校验的答案；来源候选保留','rejections':[row['reason'] for row in rejected]})
            state['claims']=list({fingerprint((c['text'],c['supports'])):c for c in kept}.values())
            saved.update(state)
            return {'claims':state['claims'],'gaps':state['gaps'],'tasks':state['tasks'],'errors':state['errors']}

        stage_seconds={}
        def timed(name,operation):
            def run(state):
                started=time.monotonic()
                try:return operation(state)
                finally:stage_seconds[name]=stage_seconds.get(name,0)+time.monotonic()-started
            return run
        graph=StateGraph(State)
        graph.add_node('understand',timed('understand',understand));graph.add_node('retrieve',timed('retrieve',retrieve));graph.add_node('generate',timed('generate_and_review',generate))
        graph.add_edge(START,'understand');graph.add_edge('understand','retrieve')
        graph.add_conditional_edges('retrieve',lambda state:'generate' if state.get('stop') else 'retrieve')
        graph.add_edge('generate',END)
        try: state=graph.compile().invoke(saved,{'recursion_limit':20})
        except Exception as error:
            state=saved;state['errors'].append(type(error).__name__)
            state['gaps'].append({'kind':'execution','reason':'流程中断，已读依据保留'})
        claims=state.get('claims',[]);citations=[]
        for claim in claims: citations.extend(citations_for(claim,tools.reads))
        if not claims:
            for view in tools.reads.values():
                citations.extend(citations_for({'supports':[{'evidence_id':view['evidence_id'],'member_keys':[m['member_key'] for m in view['members']]}]},tools.reads))
        citations=list({row['id']:row for row in citations}.values())
        gaps=state['gaps']+[{'kind':'coverage','object':t['object'],'knowledge_item':t['knowledge_item'],'state':t['coverage'],'applicability':t['applicability']} for t in state['tasks'] if t['coverage']!='evidence_found' and not t.get('answer_coverage')]
        if state['tasks'] and (all(t.get('answer_coverage')=='complete' for t in state['tasks'] if t.get('required')) or state.get('stop_reason') in ('answer_sources_ready','sources_sufficient') and all(t.get('lookup_attempted') for t in state['tasks'] if t.get('required')) and not pending_internal_references(tools)):
            gaps=[gap for gap in gaps if gap.get('kind')!='retrieval_pending']
        unresolved_refs={}
        for target in getattr(tools,'reference_target_links',[]):
            eid=target['evidence_id']
            if eid in tools.reads:continue
            key=(target['origin_evidence_id'],target['reference_number'],target['reference_kind'])
            unresolved_refs.setdefault(key,[]).append(eid)
        for (origin,number,kind),ids in unresolved_refs.items():
            tools.reference_gaps.append({'kind':'reference_unread','origin_evidence_id':origin,
                'reference_number':number,'reference_kind':kind,'target_evidence_ids':ids,
                'reason':f'引用{number}仍有{len(ids)}组候选来源尚未读取，相关性与适用性待核实。'})
        reference_gaps,reference_diagnostics=answer_reference_gaps(tools.reference_gaps,state['tasks'],claims,tools.reads)
        gaps.extend(reference_gaps);tools.diagnostics.extend(reference_diagnostics)
        gaps.extend({'evidence_id':eid,**warning} for eid,view in tools.reads.items() for warning in view.get('source_warnings',[]) if warning.get('state') in ('unreadable','context_incomplete','pending_review'))
        gaps += [{'kind':'retrieval_pending','object':t['object'],'knowledge_item':t['knowledge_item'],'reason':'本轮尚未完成此必需知识项的补查'} for t in state['tasks'] if t.get('required') and not t['lookup_attempted'] and t.get('answer_coverage')!='complete']
        if state.get('visual_required'):
            for figure in state.get('figure_ids') or [None]:
                successful=False
                for eid,view in tools.reads.items():
                    labels=str(view.get('context',{}).get('figure_title') or '')+'\n'+'\n'.join(m.get('quote','') for m in view['members'] if m.get('role') in ('caption','figure_reference'))
                    if figure and figure not in figure_ids(labels): continue
                    successful |= any(row.get('status')=='read' and row.get('pixels_sent') and row.get('response_received') for row in tools.visuals.get(eid,[]))
                if not successful: gaps.append({'kind':'visual_unread','figure_id':figure,'reason':'原问题要求实际读图，尚未完成对应图片像素解读；文字依据不替代读图'})
            for rows in tools.visuals.values():
                for row in rows:
                    if row.get('status')=='read':
                        for text in row.get('description',{}).get('unreadable',[]): gaps.append({'kind':'image_detail_unread','reason':'图片中尚未读清：'+str(text)})
        answer_items=[]
        for claim in claims:
            text=('图片解读：' if claim['basis']=='image' else '工程推断：' if claim['basis']=='inference' else '')
            text+=('待验证候选措施：' if claim.get('certainty')=='unknown' and claim.get('knowledge_item')=='measure' else '')+claim['text']
            if claim.get('conditions'):text+='（条件：'+'；'.join(str(value) for value in claim['conditions'])+'）'
            if claim.get('verification'):text+='（验证：'+'；'.join(str(value) for value in claim['verification'])+'）'
            answer_items.append(text)
        answer='\n'.join((f'{index}. ' if len(answer_items)>1 else '')+text for index,text in enumerate(answer_items,1)) or '本次尚无经过来源核验的建议。'
        gaps=unresolved_answer_gaps(normalize_answer_gaps(gaps,reference_gaps,tools.reads),state['tasks'],claims)
        status='answered' if claims and not gaps else 'partial' if tools.reads else 'not_found'
        summary={'snapshot':asdict(snapshot),'rounds':state.get('round',0),'tools':tools.summary,'calls':budget.calls,'images':budget.images,'seconds':round(time.monotonic()-budget.started,2),'errors':state['errors'],'task_coverage':state['tasks'],**retrieval_state(state)}
        summary['stage_seconds']={name:round(value,3) for name,value in stage_seconds.items()}
        summary['stage_seconds']['database']=round(getattr(budget,'database_seconds',0),3)
        summary['database_calls']=getattr(budget,'database_calls',0)
        summary['source_counts']={'read_evidence':len(tools.reads),
            'read_members':sum(len(view.get('members',[])) for view in tools.reads.values()),
            'source_characters':sum(len(member.get('quote','')) for view in tools.reads.values() for member in view.get('members',[])),
            'task_candidate_evidence':{task['id']:len(task.get('candidate_evidence_ids',[])) for task in state['tasks']}}
        summary['source_status']=[{'evidence_id':eid,**view['source_status'],'warnings':view.get('source_warnings',[])} for eid,view in tools.reads.items() if 'source_status' in view]
        summary['diagnostics']=source_diagnostics(tools.diagnostics,tools.reads)
        summary['failure_stages']=list(dict.fromkeys(gap['stage'] for gap in gaps if gap.get('stage')))
        session.turns.append({'question':question,'snapshot':snapshot.key,'objects':session.last_objects})
        return AnswerResult(answer,status,claims,citations,gaps,summary,group_citations(citations,tools.reads))
