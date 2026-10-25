"""Conservative, ephemeral numeric limits from already read Evidence members.

This is deliberately narrower than general language understanding. Ambiguous
source language is left for the answer to explain without a numeric verdict.
"""
import re
import unicodedata

from turbine_kg.agent.model import call_model
from turbine_kg.agent.prompts import numeric_alignment_prompt
from turbine_kg.knowledge.quantities import compare_quantity, quantity_in_text, UNIT_END_PATTERN
from turbine_kg.runtime import json_text


_LIMIT = re.compile(
    r'(?P<operator>不应大于|不得大于|不应超过|不得超过|不大于|不超过|应小于|小于|低于|'
    r'不应小于|不得小于|不小于|不低于|应大于|大于|高于|'
    r'应等于|等于)\s*'
    r'(?P<value>\d+(?:\.\d+)?)\s*'
    r'(?P<unit>mm/m|m/m|mm|cm|m|MPa|kPa|Pa|℃|°C|%|min|h|s|倍工作压力|倍额定压力|倍)' + UNIT_END_PATTERN,
    re.I,
)
_OPERATORS = {
    '不应大于':'le','不得大于':'le','不应超过':'le','不得超过':'le','不大于':'le','不超过':'le',
    '应小于':'lt','小于':'lt','低于':'lt',
    '不应小于':'ge','不得小于':'ge','不小于':'ge','不低于':'ge',
    '应大于':'gt','大于':'gt','高于':'gt','应等于':'eq','等于':'eq',
}
_CONDITION = re.compile(r'如果|若|当|只有|除非|在[^，。；]{1,16}时|无[^，。；]{1,16}时')
_QUALIFIER = re.compile(r'[\u4e00-\u9fffA-Za-z]{1,24}(?:前|后|时)|期间|阶段|工况')
_LOCATION = re.compile(r'表面|侧面|端面|底部|顶部|内壁|外壁|轴端|入口|出口|中心|边缘')


def _content_runs(members):
    """Rejoin adjacent source members split in the middle of a sentence."""
    text = ''
    keys = []
    for member in members:
        if member.get('role') not in ('content', 'requirement') or member.get('quality') in ('uncertain', 'needs_review', 'unreadable'):
            if keys:
                yield text, keys
                text, keys = '', []
            continue
        quote = member.get('quote', '')
        if keys and (text.rstrip().endswith(('。', '；', ';', '：', ':'))
                     or re.match(r'^\s*\d+(?:\.\d+)*\s+', quote)):
            yield text, keys
            text, keys = '', []
        text += quote
        keys.append(member['member_key'])
    if keys:
        yield text, keys


def _plain(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).casefold()


def _target_is_object_location(target, object_label, location, registry):
    if not location or not isinstance(target, str):
        return False
    target_text = _plain(target)
    place = _plain(location)
    if not target_text.endswith(place) or len(target_text) <= len(place):
        return False
    canonical = getattr(registry, 'canonical_label', lambda word: word)
    return _plain(canonical(target_text[:-len(place)])) == _plain(canonical(object_label))


def _target_is_measurement_phrase(target, object_label, location, parameter, parent_object):
    if not isinstance(target, str) or not location or not parameter:
        return False
    owners = {_plain(object_label)}
    if parent_object:
        owners.add(_plain(parent_object))
    return _plain(target) in {
        owner + _plain(location) + connector + _plain(parameter)
        for owner in owners for connector in ('', '的')
    }


def _parent_binding(view, task):
    """Bind a numbered item to one explicit parent object and activity."""
    parents = []
    for member in view.get('members', []):
        if member.get('role') not in ('section','content') or member.get('quality') in ('uncertain', 'needs_review', 'unreadable'):
            continue
        quote = _plain(member.get('quote', ''))
        match = re.fullmatch(r'\d+(?:\.\d+)*(.+?)应符合下列规定[:：]?', quote)
        if match and _plain(task['object']) in match.group(1):
            head = match.group(1)
            obj = _plain(task['object'])
            position = head.find(obj)
            source_object = head[:position + len(obj)]
            stage = head[position + len(obj):]
            if source_object not in _plain(task.get('subquestion', '')):
                continue
            if stage and stage not in _plain(task.get('subquestion', '')):
                continue
            parents.append(member)
    return parents[0] if len(parents) == 1 else None


def _parent_object(parent, object_label):
    if not parent:
        return None
    quote = _plain(parent.get('quote'))
    match = re.fullmatch(r'\d+(?:\.\d+)*(.+?)应符合下列规定[:：]?', quote)
    if not match:
        return None
    position = match.group(1).find(_plain(object_label))
    return match.group(1)[:position + len(_plain(object_label))] if position >= 0 else None


def _object_in_clause(clause, object_label, parent):
    normalized = _plain(clause)
    obj = _plain(object_label)
    if obj in normalized:
        return True
    # A parent can license an abbreviated subject in its numbered item, but
    # require an explicit multi-character suffix of that same object.
    return bool(parent and any(obj[-size:] in normalized for size in range(2, len(obj))))


def compare_read_evidence(task, reads, facts, registry):
    """Return one source-bound check per explicit field measurement, or unknown.

    A numbered item may inherit an explicitly bound parent object/activity.
    Each comma-delimited atomic clause must still have one unambiguous limit.
    Tables, OCR-uncertain members, and conditional clauses remain unknown.
    """
    own = facts.get(task['object'], {})
    results = []
    for parameter, actual in own.items():
        if not isinstance(actual, dict) or 'value' not in actual:
            continue
        candidates = []
        for evidence_id, view in reads.items():
            if view.get('kind') != 'text' or view.get('context', {}).get('quality') in ('uncertain', 'needs_review'):
                continue
            context = view.get('context', {})
            if any(context.get(key) for key in ('scope_constraints', 'missing_context_pages')):
                continue
            if any(_CONDITION.search(member.get('quote', '')) for member in view.get('members', [])):
                continue
            parent = _parent_binding(view, task)
            parent_required = any(member.get('role') in ('section','content') and
                                  re.fullmatch(r'\d+(?:\.\d+)*.+?应符合下列规定[:：]?', _plain(member.get('quote', '')))
                                  for member in view.get('members', []))
            if parent_required and parent is None:
                continue
            for quote, member_keys in _content_runs(view.get('members', [])):
                for clause in re.split(r'[，,。；;]', unicodedata.normalize('NFKC', quote)):
                    if not (_object_in_clause(clause, task['object'], parent) and _plain(parameter) in _plain(clause)):
                        continue
                    if _CONDITION.search(clause) or _QUALIFIER.search(clause):
                        continue
                    matches = list(_LIMIT.finditer(clause))
                    if len(matches) != 1:
                        continue
                    match = matches[0]
                    before = clause[:match.start()]
                    if _plain(parameter) not in _plain(before):
                        continue
                    # A source may restrict the measurement to a particular
                    # surface or position. The user's measured value must say
                    # the same position; the object name alone is insufficient.
                    locations = set(_LOCATION.findall(before))
                    if len(locations) > 1:
                        continue
                    location = next(iter(locations), None)
                    if location and actual.get('location') and location not in _plain(actual['location']):
                        continue
                    if location and location not in _plain(actual.get('location')) and location not in _plain(task.get('subquestion')):
                        continue
                    # A clause with other numeric thresholds or ranges needs
                    # semantic interpretation; do not select a convenient one.
                    remaining = clause[:match.start()] + clause[match.end():]
                    if re.search(r'\d+(?:\.\d+)?\s*(?:mm/m|m/m|mm|cm|m|MPa|kPa|Pa|℃|°C|%|min|h|s|倍)', remaining, re.I):
                        continue
                    quantity = {'target': task['object'], 'parameter': parameter, 'location': location,
                                'operator': _OPERATORS[match.group('operator')],
                                'value': match.group('value'), 'unit': match.group('unit')}
                    measured = dict(actual)
                    target_is_location = _target_is_object_location(actual.get('target'), task['object'], location, registry)
                    parent_object = _parent_object(parent, task['object'])
                    target_is_parent = bool(location and parent_object and
                        _plain(actual.get('target')) == _plain(parent_object) and
                        _plain(actual.get('location')) in {
                            _plain(location), _plain(task['object'] + location),
                            _plain(parent_object + location)})
                    target_is_phrase = bool(
                        _target_is_measurement_phrase(actual.get('target'), task['object'],
                                                      location, parameter, parent_object)
                        and _plain(actual.get('location')) in {
                            _plain(location), _plain(task['object'] + location),
                            _plain(parent_object + location) if parent_object else ''})
                    if target_is_location or target_is_parent or target_is_phrase:
                        measured['target'] = task['object']
                    if location:
                        measured['location'] = location
                    check = compare_quantity(quantity, {parameter: measured},
                                             object_label=task['object'], registry=registry,
                                             applicability='applicable')
                    keys = ([parent['member_key']] if parent else []) + member_keys
                    check.update(source='read_evidence', supports=[{'evidence_id': evidence_id,
                                                                     'member_keys': keys}])
                    if target_is_location:
                        check.update(target_alignment='object_plus_location', user_target=actual['target'])
                    elif target_is_parent:
                        check.update(target_alignment='parent_object_and_location',
                                     user_target=actual['target'], user_location=actual['location'])
                    elif target_is_phrase:
                        check.update(target_alignment='object_location_parameter',
                                     user_target=actual['target'], user_location=actual['location'])
                    candidates.append(check)
        if len(candidates) == 1:
            results.append(candidates[0])
        else:
            results.append({'status':'unknown', 'parameter':parameter, 'source':'read_evidence',
                            'reason':'已读来源无单一明确且适用的限值' if not candidates else '已读来源存在多个可能限值，不能自动选择',
                            'supports':[]})
    return results


def _same_object(left, right, registry):
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    canonical = getattr(registry, 'canonical_label', lambda value: value)
    return _plain(canonical(left)) == _plain(canonical(right))


def _source_operator(quote):
    text = _plain(quote)
    matches = [(phrase, operator) for phrase, operator in _OPERATORS.items()
               if _plain(phrase) in text]
    if not matches:
        return set()
    longest = max(len(_plain(phrase)) for phrase, _ in matches)
    return {operator for phrase, operator in matches if len(_plain(phrase)) == longest}


def _source_limit_supported(row, source_quote):
    """Check model-extracted limit fields against its exact bound source text."""
    limit_quote = row.get('limit_quote')
    unit = row.get('unit')
    operator = row.get('operator')
    value = row.get('value')
    if not all(isinstance(item, str) and item.strip() for item in (limit_quote, unit, operator)):
        return False
    source = _plain(source_quote)
    limit = _plain(limit_quote)
    if not limit or limit not in source:
        return False
    return _source_operator(limit_quote) == {operator} and quantity_in_text(value,unit,limit_quote)


def _source_excerpt_bound(view, member_keys, excerpt):
    selected = set(member_keys)
    ordered = [member for member in view.get('members', [])
               if member.get('member_key') in selected]
    runs = []
    current = []
    last_index = None
    all_members = view.get('members', [])
    positions = {member.get('member_key'): index for index, member in enumerate(all_members)}
    for member in ordered:
        position = positions[member.get('member_key')]
        if last_index is not None and position != last_index + 1:
            runs.append(''.join(current));current=[]
        current.append(member.get('quote', ''))
        last_index = position
    if current:
        runs.append(''.join(current))
    needle = _plain(excerpt)
    return bool(needle and any(needle in _plain(run) for run in runs))


def compare_read_evidence_semantically(task, reads, facts, registry, model, budget, *,
                                       existing_checks, question='', reserve_seconds=0):
    """Resolve unknown numeric checks by source-bound model alignment, then code comparison.

    This call is ephemeral: only checks already marked unknown are sent, and
    extracted limits are returned in the result rather than persisted.
    """
    checks = [dict(row) for row in existing_checks if isinstance(row, dict)]
    own = facts.get(task.get('object'), {}) if isinstance(facts, dict) else {}
    unresolved = [row for row in checks if row.get('status') == 'unknown'
                  and isinstance(row.get('parameter'), str)
                  and isinstance(own.get(row['parameter']), dict)
                  and 'value' in own[row['parameter']]]
    if not unresolved:
        return checks
    parameter_names = list(dict.fromkeys(row['parameter'] for row in unresolved))
    source_views = []
    for evidence_id, view in reads.items():
        members = view.get('members', [])
        if not members:
            continue
        source_views.append({
            'evidence_id': evidence_id,
            'text': view.get('text') or '\n'.join(member.get('quote', '') for member in members),
            'members': [{key: member[key] for key in ('member_key', 'role', 'quote', 'quality') if key in member}
                        for member in members],
        })
    if not source_views:
        return checks
    prompt_question = question or task.get('subquestion', '')
    payload = {
        'question': prompt_question,
        'task': {'object': task.get('object'), 'subquestion': task.get('subquestion')},
        'user_facts': {name: own[name] for name in parameter_names},
        'unknown_checks': [{'parameter': row['parameter']} for row in unresolved],
        'read_evidence': source_views,
    }
    try:
        response = call_model(model, numeric_alignment_prompt(), json_text(payload), budget,
                              reserve_seconds=reserve_seconds)
    except Exception:
        return checks
    proposed = response.get('comparisons', []) if isinstance(response, dict) else []
    if not isinstance(proposed, list):
        return checks
    grouped = {}
    for row in proposed:
        if isinstance(row, dict) and isinstance(row.get('parameter'), str):
            grouped.setdefault(row['parameter'], []).append(row)

    replacements = {}
    for check in unresolved:
        parameter = check['parameter']
        rows = grouped.get(parameter, [])
        if len(rows) != 1:
            continue
        row = rows[0]
        evidence_id = row.get('evidence_id')
        keys = row.get('member_keys')
        if not isinstance(evidence_id, str) or not isinstance(keys, list) or not keys or any(not isinstance(key,str) for key in keys):
            continue
        view = reads.get(evidence_id)
        if not view or len(keys) != len(set(keys)):
            continue
        members = {member.get('member_key'): member for member in view.get('members', [])}
        if any(key not in members or members[key].get('quality') in ('uncertain', 'needs_review', 'unreadable')
               for key in keys):
            continue
        source_quote = row.get('source_quote')
        if not isinstance(source_quote, str) or not source_quote.strip():
            continue
        source_text = _plain(source_quote)
        ordered_keys = [member['member_key'] for member in view.get('members', [])
                        if member.get('member_key') in set(keys)]
        if not source_text or not _source_excerpt_bound(view, ordered_keys, source_quote):
            continue
        actual = own[parameter]
        target = row.get('target')
        user_target = actual.get('target') or task.get('object')
        selected_text = _plain(''.join(members[key].get('quote', '') for key in ordered_keys))
        if not _same_object(target, user_target, registry) or _plain(target) not in selected_text:
            continue
        # The parameter key names the user's measurement, not a required
        # verbatim substring of the book. Source interpretation is performed
        # by the alignment request; its numeric span is verified below.
        limit_quote = row.get('limit_quote')
        if not isinstance(limit_quote, str) or not _source_limit_supported(row, source_quote):
            continue
        if not _source_excerpt_bound(view, ordered_keys, limit_quote):
            continue
        location = row.get('location')
        if location is not None and (not isinstance(location, str) or _plain(location) not in source_text):
            continue
        source_condition = row.get('source_condition_quote', '')
        user_condition = row.get('user_condition_quote', '')
        if not isinstance(source_condition, str) or not isinstance(user_condition, str):
            continue
        if source_condition and not _source_excerpt_bound(view, ordered_keys, source_condition):
            continue
        if user_condition and _plain(user_condition) not in _plain(prompt_question):
            continue
        applicability = row.get('applicability')
        if applicability not in ('applicable', 'not_applicable', 'unknown'):
            continue
        # If the source names a condition but no user condition was bound,
        # applicability cannot be inferred from the numeric match alone.
        if source_condition and not user_condition:
            applicability = 'unknown'
        quantity = {'target': target, 'parameter': parameter, 'location': location,
                    'operator': row.get('operator'), 'value': row.get('value'), 'unit': row.get('unit')}
        result = compare_quantity(quantity, {parameter: actual}, object_label=task['object'],
                                  registry=registry, applicability=applicability)
        result.update(source='semantic_read_evidence', supports=[{'evidence_id': evidence_id,
                       'member_keys': keys}], source_quote=source_quote,
                       source_condition_quote=source_condition, user_condition_quote=user_condition)
        replacements[parameter] = result
    return [replacements.get(row.get('parameter'), row) if row.get('status') == 'unknown' else row
            for row in checks]
