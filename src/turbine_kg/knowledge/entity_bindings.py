"""Bind entity classifications to their exact Evidence source members."""
from __future__ import annotations

from copy import deepcopy
import re

from turbine_kg.ontology.registry import normalized
from turbine_kg.runtime import fingerprint, json_text


def _view_index(views):
    if isinstance(views, dict):
        return views
    return {view['evidence_id']: view for view in views}


def reviewed_entity_binding(surface_label, class_id, supports, views, registry,
                            reason, review_id):
    """Bind an explicitly reviewed interpretation to current source members.

    The caller supplies the semantic decision. This function verifies its
    registered target and literal source anchors; it does not infer synonyms.
    Fingerprints let a later extraction reject a review after source changes.
    """
    if (not isinstance(class_id, str) or class_id == 'Thing'
            or registry.domain_class_by_id(class_id) is None):
        raise ValueError('审定映射的目标不是已注册业务类')
    if not isinstance(surface_label, str) or not normalized(surface_label):
        raise ValueError('审定映射缺少原文名称')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('审定映射缺少审定理由')
    if not isinstance(review_id, str) or not review_id.strip():
        raise ValueError('审定映射缺少审查标识')
    indexed = _view_index(views)
    source_members = []
    source_versions = []
    seen = set()
    for support in supports:
        evidence_id = support['evidence_id']
        view = indexed.get(evidence_id)
        if view is None or view.get('evidence_id', evidence_id) != evidence_id:
            raise ValueError('审定映射缺少一致的Evidence来源视图')
        members = {member['member_key']: member for member in view['members']}
        selected = support['member_keys']
        if not selected or any(key not in members for key in selected):
            raise ValueError('审定映射的支持成员未知或为空')
        selected_fingerprint = fingerprint([
            {key: members[member_key].get(key) for key in
             ('member_key', 'source_id', 'quote', 'locator', 'role', 'source_kind')}
            for member_key in sorted(set(selected))])
        source_versions.append({
            'evidence_id': evidence_id,
            'document_id': view.get('document_id'),
            'revision_id': view.get('revision_id'),
            'member_fingerprint': view.get('member_fingerprint'),
            'selected_members_fingerprint': selected_fingerprint,
            'member_keys': sorted(set(selected)),
        })
        for member_key in selected:
            member = members[member_key]
            quote = member.get('quote', '')
            pair = (evidence_id, member_key)
            if (isinstance(quote, str)
                    and normalized(surface_label) in normalized(quote)
                    and pair not in seen):
                seen.add(pair)
                source_members.append({'evidence_id': evidence_id,
                                       'member_key': member_key})
    if not source_members:
        raise ValueError('审定名称未在Statement支持的原文成员中连续出现')
    return {'method': 'reviewed_source_mapping', 'class_id': class_id,
            'surface_label': surface_label, 'source_members': source_members,
            'source_versions': source_versions, 'ontology_version': registry.version,
            'reason': reason.strip(), 'review_id': review_id}


def restored_entity_binding(surface_label, supports, views, registry):
    """Restore one unique review only while its exact source scope survives.

    Snapshot-wide fingerprints may change with the ontology version. Reviewed
    member content, source revision and selected support anchors must not.
    """
    if not isinstance(surface_label, str) or not normalized(surface_label):
        return None
    indexed = _view_index(views)
    supported = {(support['evidence_id'], key) for support in supports
                 for key in support['member_keys']}
    candidates = {}
    for view in indexed.values():
        for review in view.get('entity_mapping_reviews', []):
            if (not isinstance(review, dict)
                    or review.get('method') != 'reviewed_source_mapping'
                    or not isinstance(review.get('surface_label'), str)
                    or normalized(review['surface_label']) != normalized(surface_label)):
                continue
            class_id = review.get('class_id')
            if (not isinstance(class_id, str) or class_id == 'Thing'
                    or registry.domain_class_by_id(class_id) is None):
                continue
            anchors = review.get('source_members')
            versions = review.get('source_versions')
            if not isinstance(anchors, list) or not anchors or not isinstance(versions, list) or not versions:
                continue
            try:
                if any((anchor['evidence_id'], anchor['member_key']) not in supported
                       for anchor in anchors):
                    continue
                verified = set()
                for version in versions:
                    evidence_id = version['evidence_id']
                    current = indexed.get(evidence_id)
                    if (current is None
                            or current.get('document_id') != version.get('document_id')
                            or current.get('revision_id') != version.get('revision_id')):
                        break
                    members = {member['member_key']: member for member in current['members']}
                    keys = version['member_keys']
                    if (not isinstance(keys, list) or not keys
                            or any(key not in members or (evidence_id, key) not in supported
                                   for key in keys)):
                        break
                    actual = fingerprint([
                        {key: members[member_key].get(key) for key in
                         ('member_key', 'source_id', 'quote', 'locator', 'role', 'source_kind')}
                        for member_key in sorted(set(keys))])
                    if actual != version.get('selected_members_fingerprint'):
                        break
                    verified.update((evidence_id, key) for key in keys)
                else:
                    if not all((anchor['evidence_id'], anchor['member_key']) in verified
                               for anchor in anchors):
                        continue
                    rebound = reviewed_entity_binding(
                        surface_label, class_id, supports, indexed, registry,
                        review['reason'], review['review_id'])
                    candidates.setdefault(class_id, rebound)
            except (KeyError, TypeError, ValueError):
                # Stale or malformed audit metadata is never a new assertion.
                continue
    return next(iter(candidates.values())) if len(candidates) == 1 else None


def source_entity(entity):
    """Copy the source wording and its replaceable binding without class proxies."""
    result = {key:entity[key] for key in ('label','class','role')}
    for key in ('occurrence', 'ontology_class_id', 'ontology_binding', 'ontology_mapping_review'):
        if key in entity:
            result[key] = deepcopy(entity[key])
    return result


def context_class_members(statement, views, registry):
    """Source headings provide recall context, never entity types or obligations."""
    result = {}
    names = [(class_id, normalized(name)) for class_id, row in registry.domain_classes.items()
             if registry.domain_is_a(class_id, 'PhysicalObject')
             for name in [row['label'], *row.get('aliases', [])] if len(normalized(name)) >= 2]
    for support in statement.get('supports', []):
        eid = support['evidence_id']
        view = views.get(eid, {})
        selected = set(support['member_keys'])
        clause_source = view.get('context', {}).get('clause_source_id')
        has_body = bool(clause_source) and any(m.get('member_key') in selected and
            m.get('source_id') != clause_source and m.get('role') in ('content','value','row_label','note')
            for m in view.get('members', []))
        for member in view.get('members', []):
            heading = member.get('role') == 'section' and member.get('source_kind') == 'heading'
            lead = (member.get('member_key') in selected and
                    (has_body and member.get('source_id') == clause_source
                     or member.get('role') == 'section' and member.get('source_kind') in
                     ('paragraph', 'text', 'clause', 'list_item')))
            if not heading and not lead:
                continue
            text = normalized(re.sub(r'^\s*\d+(?:\.\d+)*\s*', '', member.get('quote', '')))
            matches = [(class_id, name) for class_id, name in names if name in text]
            # An embedded broader name must not add another classification.
            classes = {class_id for class_id, name in matches if not any(
                name != other and name in other for _, other in matches)}
            for class_id in classes:
                anchor = {'evidence_id': eid, 'member_key': member['member_key'],
                          'context_kind': 'section' if heading else 'clause'}
                result.setdefault(class_id, {})[(eid, member['member_key'])] = anchor
    # Qualified names already carry a source-bound owner independently of
    # whether the model emitted that owner as a separate Mention.
    supported = {(s['evidence_id'], key) for s in statement.get('supports', []) for key in s['member_keys']}
    for entity in statement.get('entities', []):
        binding = entity.get('ontology_binding') or {}
        class_id = binding.get('context_class_id')
        if class_id not in registry.domain_classes or not registry.domain_is_a(class_id, 'PhysicalObject'):
            continue
        for member in binding.get('source_members', []):
            pair = (member.get('evidence_id'), member.get('member_key'))
            if pair in supported:
                result.setdefault(class_id, {})[pair] = {**member, 'context_kind': 'qualified_name'}
    return {key: list(members.values()) for key, members in result.items()}



def prepare_occurrences(item, registry, supplied=None):
    """Source provenance bounds real instances; roles never contribute to their identity."""
    supplied = supplied or {}
    refs = [{'evidence_id': row['evidence_id'], 'member_keys': list(row['member_keys'])}
            for row in item.get('supports', [])]
    contexts = []
    anchors = []
    for support in refs:
        view = supplied.get(support['evidence_id'], {})
        if view.get('revision_id'): contexts.append({'document_id':view.get('document_id'), 'revision_id':view['revision_id']})
        members = {member['member_key']:member for member in view.get('members', [])}
        for key in support['member_keys']:
            source = members.get(key, {})
            anchors.append({'source_id':source.get('source_id', key), 'locator':source.get('locator', {})})
    contexts = sorted({json_text(context): context for context in contexts}.values(), key=json_text)
    anchors = sorted({json_text(anchor): anchor for anchor in anchors}.values(), key=json_text)
    for entity in item.get('entities', []):
        if not (registry.is_a(entity['class'], 'PhysicalObject') or registry.is_a(entity['class'], 'Activity')): continue
        occurrence = entity.get('occurrence')
        if occurrence is not None and not isinstance(occurrence, dict): raise ValueError('真实对象或活动身份必须为对象')
        occurrence = occurrence or {}
        details = {key: value if isinstance(value, str) and value.strip()
                   and value.strip().lower() not in ('unknown', 'null', 'none', '未知', '未确认') else None
                   for key in ('identifier', 'object', 'location', 'time')
                   for value in [occurrence.get(key)]}
        if not any(details.values()):
            # A generic object/activity mention is not a field instance. During
            # model extraction keep the wording as a Mention so a missing
            # identifier cannot discard an otherwise source-backed Statement.
            # Direct persistence remains strict for callers that explicitly
            # provide a real-instance class without identity.
            if supplied:
                entity['class'] = 'Mention'
                entity.pop('occurrence', None)
                continue
            raise ValueError('真实对象或活动缺少来源明确的身份信息；来源锚点不能作为实例身份')
        # Only extract() supplies provenance. persist() retains it and otherwise uses conservative source anchors.
        entity['occurrence'] = {**details, 'source_context': contexts or occurrence.get('source_context', []),
                                'source_anchors': anchors if supplied else occurrence.get('source_anchors', anchors),
                                'source_supports': deepcopy(refs), 'identity_quality':'source_scoped'}


def map_statement(statement, registry, views, *, retain_binding=False):
    """Rebind current source mentions without changing text, roles, or order."""
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
