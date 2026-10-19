"""Conservative source alignment between two editions of one document.

Unchanged assertions can keep their identities when a PDF fingerprint or its
layout changes. Only unambiguous one-to-one source fragments are rebound;
different wording and changed fragmentation stay eligible for extraction.
"""
from copy import deepcopy
from dataclasses import fields, is_dataclass

from turbine_kg.runtime import json_text


def compact(text):
    return ''.join((text or '').split())


def remap_ids(value, mapping):
    """Rewrite exact identifiers, including source links nested in attributes."""
    if is_dataclass(value):
        for field in fields(value):setattr(value,field.name,remap_ids(getattr(value,field.name),mapping))
        return value
    if isinstance(value,dict):return {key:remap_ids(item,mapping) for key,item in value.items()}
    if isinstance(value,list):return [remap_ids(item,mapping) for item in value]
    if isinstance(value,tuple):return tuple(remap_ids(item,mapping) for item in value)
    return mapping.get(value,value) if isinstance(value,str) else value


def _position(locator):
    return tuple(json_text(locator.get(key)) for key in ('physical_page','bbox','char_range'))


def _anchor(item):
    context=item.get('context',{})
    return (item.get('kind'),compact(context.get('section')),
            context.get('clause_number') if item.get('kind')=='text' else
            context.get('table_number') or compact(context.get('table_title')))


def _members(item,sources):
    result=[]
    for member in item.get('members',[]):
        if member['role']=='section_context':continue
        source=sources.get(member['source_id'])
        if source is None:return []
        quote=source.get('text','');bounds=member.get('locator',{}).get('char_range')
        if bounds:quote=quote[bounds[0]:bounds[1]]
        result.append({**member,'quote':quote,'source_kind':source.get('kind')})
    return result


def _signature(members):
    # Keep fragment boundaries and roles. Equal concatenated text alone cannot
    # justify changing an existing Statement's member-level source bindings.
    return tuple((row['role'],row.get('source_kind'),compact(row.get('quote'))) for row in members)


def plan_alignment(incoming,sources,previous_views,previous_sources):
    """Return reusable identifiers without modifying data or calling a model."""
    source_index={row['id']:row for row in sources}
    previous_index={row['id']:row for row in previous_sources}
    groups={}
    for view in previous_views:
        members=[row for row in view['members'] if row['role']!='section_context']
        groups.setdefault((_anchor(view),_signature(members)),[]).append(view)
    proposals=[]
    for item in incoming:
        if item.get('kind') not in ('text','table'):continue
        members=_members(item,source_index)
        if not members:continue
        candidates=groups.get((_anchor(item),_signature(members)),[])
        if len(candidates)>1:
            position=[_position(row.get('locator',{})) for row in members]
            candidates=[view for view in candidates if
                [_position(row.get('locator',{})) for row in view['members'] if row['role']!='section_context']==position]
        if len(candidates)!=1:continue
        old=candidates[0];old_members=[row for row in old['members'] if row['role']!='section_context']
        pairs=list(zip(members,old_members))
        # Sharing a SourceText across two clauses is fine only when its entire
        # text is unchanged, not merely the slice selected by one clause.
        if any(new['source_id'] not in source_index or before['source_id'] not in previous_index or
               compact(source_index[new['source_id']].get('text'))!=compact(previous_index[before['source_id']].get('text'))
               for new,before in pairs):continue
        proposals.append((item,old,pairs))
    old_usage={};source_targets={};reverse_sources={};resource_targets={};reverse_resources={}
    for item,old,pairs in proposals:
        old_usage.setdefault(old['evidence_id'],set()).add(item['id'])
        for new,before in pairs:
            source_targets.setdefault(new['source_id'],set()).add(before['source_id'])
            reverse_sources.setdefault(before['source_id'],set()).add(new['source_id'])
        if item.get('kind')=='table':
            new_table=item.get('context',{}).get('table_id');old_table=old.get('context',{}).get('table_id')
            resource_targets.setdefault(new_table,set()).add(old_table)
            reverse_resources.setdefault(old_table,set()).add(new_table)
    mapping={};unchanged=set();locations={}
    for item,old,pairs in proposals:
        if len(old_usage[old['evidence_id']])!=1 or any(
            len(source_targets[new['source_id']])!=1 or len(reverse_sources[before['source_id']])!=1
            for new,before in pairs):continue
        if item.get('kind')=='table' and (len(resource_targets[item['context'].get('table_id')])!=1 or
                len(reverse_resources[old['context'].get('table_id')])!=1):continue
        mapping[item['id']]=old['evidence_id'];unchanged.add(old['evidence_id'])
        for new,before in pairs:
            mapping[new['source_id']]=before['source_id']
            mapping[new['member_key']]=before['member_key']
            locations[(before['source_id'],json_text(before.get('locator',{})))]=deepcopy(new['locator'])
        if item.get('kind')=='table':
            new_table=item.get('context',{}).get('table_id');old_table=old.get('context',{}).get('table_id')
            if new_table and old_table:
                mapping[new_table]=old_table
    return {'id_map':mapping,'unchanged_evidence_ids':unchanged,'locations':locations}


def reuse_scope_member_keys(evidence,previous_views,sources):
    previous={view['evidence_id']:view for view in previous_views};locations={}
    source_index={row['id']:row for row in sources}
    for item in evidence:
        old=previous.get(item['id'])
        if old is None:continue
        for member in item['members']:
            if member['role']!='section_context':continue
            matches=[row for row in old['members'] if row['role']=='section_context' and row['source_id']==member['source_id']]
            source=source_index.get(member['source_id'],{})
            if len(matches)!=1 or compact(matches[0].get('quote'))!=compact(source.get('text')):continue
            before=matches[0];member['member_key']=before['member_key']
            locations[(before['source_id'],json_text(before['locator']))]=deepcopy(member['locator'])
    return locations


def refresh_bound_locations(value,sources,locations):
    """Update source-bearing locator metadata, leaving all engineering text intact."""
    if isinstance(value,list):return [refresh_bound_locations(item,sources,locations) for item in value]
    if not isinstance(value,dict):return value
    result={key:refresh_bound_locations(item,sources,locations) for key,item in value.items()}
    source=sources.get(value.get('source_id'))
    if source is not None and isinstance(value.get('locator'),dict):
        known=locations.get((value['source_id'],json_text(value['locator'])))
        if known is not None:result['locator']=deepcopy(known)
        elif not value['locator'].get('char_range'):result['locator']=deepcopy(source['locator'])
    return result
