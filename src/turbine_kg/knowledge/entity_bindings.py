"""Bind entity classifications to their exact Evidence source members."""
from __future__ import annotations

from turbine_kg.ontology.registry import normalized
from turbine_kg.runtime import fingerprint


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
