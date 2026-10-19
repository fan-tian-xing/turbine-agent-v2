"""The same source assembler serves extraction and answers, without disk views."""
from __future__ import annotations

from copy import deepcopy
import re
from dataclasses import asdict

from turbine_kg.runtime import fingerprint, identity, Budget, Snapshot
from turbine_kg.documents.ir import clause_number, clause_ranges, parse_references, recognize_table_headers
from turbine_kg.knowledge.reviews import verified_formula_representation


def member(source: dict, role: str, order: int, *, locator: dict | None = None) -> dict:
    location = deepcopy(locator or source["locator"])
    return {"member_key": identity("member", source["id"], role, order, location), "source_id": source["id"], "order": order, "role": role, "locator": location, "text_origin": source.get("text_origin", "image")}


def make_evidence(kind: str, sources: list[tuple[dict, str]], document_id: str, *, context: dict | None = None) -> dict:
    # OCR can emit an isolated list marker or punctuation block as a separate
    # source.  It may help the grouping pass recognize a clause boundary, but
    # it is not useful evidence and must not be persisted as a member.
    sources = [(source, role) for source, role in sources if role != 'fragment']
    members = [member(source, role, index) for index, (source, role) in enumerate(sources)]
    if not members:
        raise ValueError("Evidence requires source members")
    if kind == "table" and not any(role == "value" for _, role in sources):
        raise ValueError("A table Evidence must contain a complete row group")
    eid = identity("evidence", document_id, kind, members)
    context = context or {}
    quotes = [_canonical_source_quote(source, item['locator']) for (source, _), item in zip(sources, members)]
    text = evidence_text(context, quotes, [role for _, role in sources],
                         [source.get('kind') for source, _ in sources], kind=kind)
    return {"id": eid, "kind": kind, "members": members, "context": context, "text": text,
            "member_fingerprint": fingerprint([(source["id"], source.get("text", source.get("content_hash", "")), m) for (source, _), m in zip(sources, members)])}


def _source_quote(source: dict, locator: dict | None = None) -> str:
    raw = source.get('text', '')
    bounds = (locator or source.get('locator', {})).get('char_range')
    if bounds:
        if not 0 <= bounds[0] <= bounds[1] <= len(raw):
            raise ValueError('来源字符范围越界')
        return raw[bounds[0]:bounds[1]]
    return raw


def _canonical_source_quote(source: dict, locator: dict) -> str:
    """Return the same source-bound display text used by ContextAssembler.read."""
    raw = _source_quote(source, locator)
    review = _verified_formula_for_locator(source, locator)
    return review['verified_text'] if review else raw


def _verified_formula_for_locator(source: dict, locator: dict) -> dict | None:
    review = verified_formula_representation(source)
    whole_source = locator.get('char_range') in (None, [0, len(source.get('text', ''))])
    matching_location = review and all(
        locator.get(field) == review['binding'][field]
        for field in ('original_hash', 'physical_page', 'bbox'))
    return review if review and whole_source and matching_location else None


_LIST_ITEM = re.compile(
    r'^\s*(?:'
    r'(?P<wrapped>[（(]\s*\d+\s*[)）])'
    r'|(?P<subitem>\d{1,3}\s*[)）](?!\d))'
    r'|(?P<plain>\d{1,3}\s*[、]\s*(?=\S)'
    r'|\d{1,3}\s*[.．](?:\s+(?=\S)|(?!\d)\s*(?=\S))'
    r'|\d{1,3}\s+(?=\S))'
    r'|(?P<chinese>[一二三四五六七八九十]+\s*[、.．]\s*(?=\S))'
    r')'
)
_HARD_END = re.compile(r'[。！？!?；;：:]\s*$')


_NON_BODY_ROLES = {'section_context', 'heading', 'header', 'inherited_header',
                   'row_label', 'value', 'note', 'caption', 'scope'}
_NON_BODY_KINDS = {'heading', 'cell', 'table_note', 'picture', 'caption'}


def _is_body_member(row: dict) -> bool:
    return (row.get('role') not in _NON_BODY_ROLES
            and row.get('source_kind') not in _NON_BODY_KINDS)


def _marker_depth(row: dict, context: dict) -> int | None:
    """Return list nesting depth, including markers detached into OCR boxes."""
    if not _is_body_member(row):
        return None
    match = _LIST_ITEM.match(row.get('quote', '').lstrip())
    if match:
        if match.group('wrapped'):
            return 3
        if match.group('subitem'):
            return 2
        return 1
    source_id = row.get('source_id')
    detached = context.get('detached_markers', {})
    if source_id and source_id in detached:
        return 1
    return None


def _intro_cue(text: str) -> bool:
    value = text.strip()
    return bool(re.search(r'(?:下列|如下|以下).{0,12}(?:规定|要求|项目)|[：:]\s*$', value))


def _continues(left: dict, right: dict, context: dict | None = None) -> bool:
    """Whether two adjacent members appear to be layout-split text of one item."""
    context = context or {}
    left_text = left.get('quote', '').rstrip()
    right_text = right.get('quote', '').lstrip()
    if (not left_text or not right_text or not _is_body_member(left) or not _is_body_member(right)
            or left.get('role') == 'section' or right.get('role') == 'section'
            or _HARD_END.search(left_text) or _marker_depth(right, context)):
        return False
    return True


def _expand_continuations(members: list[dict], keys: set[str], context: dict) -> bool:
    changed = False
    for left, right in zip(members, members[1:]):
        left_key, right_key = left.get('member_key'), right.get('member_key')
        if not left_key or not right_key or not _continues(left, right, context):
            continue
        if left_key in keys and right_key not in keys:
            keys.add(right_key)
            changed = True
        elif right_key in keys and left_key not in keys:
            keys.add(left_key)
            changed = True
    return changed


def _parent_is_governing(parent: dict, child: dict, context: dict) -> bool:
    if not _is_body_member(parent):
        return False
    parent_depth = _marker_depth(parent, context)
    child_depth = _marker_depth(child, context)
    # A same-level (or deeper) list item is a sibling/descendant, never a
    # governing introduction. Unmarked explicitly linked source text may be
    # a continuation or lead-in to the child.
    return not (parent_depth is not None and child_depth is not None
                and parent_depth >= child_depth)


def close_member_selection(view: dict, selected: list[str]) -> list[str]:
    """Expand citation members to include complete source phrases and context.

    Closure is bounded by the already assembled Evidence. It joins adjacent
    layout fragments, preserves numbered-item boundaries, and includes a
    directly governing lead-in for a selected numbered item. Structural source
    parents are followed only when their members are present in this Evidence.
    """
    members = sorted(view.get('members', []), key=lambda row: row.get('order', 0))
    by_key = {row.get('member_key'): row for row in members if row.get('member_key')}
    keys = {key for key in selected if key in by_key}
    if not keys:
        return []
    context = view.get('context', {})
    if view.get('kind') == 'table':
        # Table cells and their headings have structured row/column linkage;
        # joining them as prose can cross both row and header boundaries.
        return [row['member_key'] for row in members if row.get('member_key') in keys]
    positions = {row['member_key']: index for index, row in enumerate(members)
                 if row.get('member_key')}

    # First close textual fragments. A list item's continuation belongs to
    # that item; closing it before looking for parents prevents a continuation
    # line from being mistaken for an introduction or sibling.
    while _expand_continuations(members, keys, context):
        pass

    source_members = {}
    for row in members:
        source_members.setdefault(row.get('source_id'), []).append(row)
    changed = True
    while changed:
        changed = False
        for key in list(keys):
            child = by_key[key]
            parent_ids = child.get('attributes', {}).get('structural_parent_source_ids', [])
            for parent_id in parent_ids:
                for parent in source_members.get(parent_id, []):
                    parent_key = parent.get('member_key')
                    if (parent_key and parent_key not in keys
                            and _parent_is_governing(parent, child, context)):
                        keys.add(parent_key)
                        changed = True

            if _marker_depth(child, context) is None:
                continue
            target = child
            while True:
                depth = _marker_depth(target, context)
                if depth is None:
                    break
                target_index = members.index(target)
                explicit_parents = []
                for parent_id in target.get('attributes', {}).get('structural_parent_source_ids', []):
                    explicit_parents.extend(source_members.get(parent_id, []))
                explicit_parents = [parent for parent in explicit_parents
                                    if parent.get('member_key') in positions
                                    and positions[parent['member_key']] < target_index
                                    and _parent_is_governing(parent, target, context)]
                if explicit_parents:
                    parent = max(explicit_parents, key=lambda row: positions[row['member_key']])
                    parent_key = parent['member_key']
                    if parent_key not in keys:
                        keys.add(parent_key)
                        changed = True
                    parent_depth = _marker_depth(parent, context)
                    if parent_depth is not None and parent_depth < depth:
                        target = parent
                        continue
                    break

                # Ignore the most recent same-level item and everything
                # belonging to it. Searching before that boundary prevents a
                # prior sibling's trailing continuation from becoming context.
                sibling_barrier = next((i for i in range(target_index - 1, -1, -1)
                                        if _marker_depth(members[i], context) == depth), None)
                scan_start = sibling_barrier - 1 if sibling_barrier is not None else target_index - 1
                governing_parent = None
                for previous_index in range(scan_start, -1, -1):
                    previous = members[previous_index]
                    if not _is_body_member(previous):
                        break
                    previous_depth = _marker_depth(previous, context)
                    if previous_depth is not None:
                        if previous_depth < depth:
                            governing_parent = previous
                            break
                        continue
                    if _intro_cue(previous.get('quote', '')):
                        previous_key = previous.get('member_key')
                        if previous_key and previous_key not in keys:
                            keys.add(previous_key)
                            changed = True
                    break
                if governing_parent is None:
                    break
                parent_key = governing_parent.get('member_key')
                if parent_key and parent_key not in keys:
                    keys.add(parent_key)
                    changed = True
                target = governing_parent

        if _expand_continuations(members, keys, context):
            changed = True
    return [row['member_key'] for row in members if row.get('member_key') in keys]


def _normalize_source_quote(value: str) -> str:
    """Join layout line wraps for display without changing member quotations."""
    lines = [line.strip() for line in value.replace('\r', '').split('\n') if line.strip()]
    if not lines:
        return ''
    result = lines[0]
    for line in lines[1:]:
        left, right = result.rstrip(), line.lstrip()
        if _LIST_ITEM.match(right):
            result = left + '\n' + right
        elif (left and right and left[-1].isascii() and left[-1].isalnum()
                and right[0].isascii() and right[0].isalnum()
                and not (left[-1] == '.' and right[0].isdigit())):
            result = left + ' ' + right
        else:
            result = left + right
    return result


def evidence_text(context: dict, quotes: list[str], roles: list[str] | None = None,
                  source_kinds: list[str | None] | None = None, *, kind: str = 'text') -> str:
    """Assemble a readable, source-faithful text body from cited members."""
    if roles is None:
        roles = ['content'] * len(quotes)
    if source_kinds is None:
        source_kinds = [None] * len(quotes)
    if len(roles) != len(quotes) or len(source_kinds) != len(quotes):
        raise ValueError('Evidence原文与成员角色数量不一致')
    rows = []
    for index, (quote, role, source_kind) in enumerate(zip(quotes, roles, source_kinds)):
        value = _normalize_source_quote(quote)
        if not value or role == 'fragment' or role == 'section_context' or source_kind == 'heading':
            continue
        markers = context.get('detached_markers', {})
        source_ids = context.get('source_ids', [])
        marker = markers.get(source_ids[index]) if index < len(source_ids) else None
        if marker and not _LIST_ITEM.match(value):
            value = f'{marker} {value}'
        rows.append({'text': value, 'role': role})
    if not rows:
        return ''
    if kind == 'table':
        title = context.get('table_title')
        body = '\n'.join(row['text'] for row in rows)
        return '\n'.join(value for value in (title, body) if isinstance(value, str) and value.strip())
    result = rows[0]['text']
    for row in rows[1:]:
        left, right = result.rstrip(), row['text'].lstrip()
        if _LIST_ITEM.match(right) or _HARD_END.search(left):
            result = left + '\n' + right
        elif left and right and left[-1].isascii() and left[-1].isalnum() and right[0].isascii() and right[0].isalnum():
            result = left + ' ' + right
        else:
            result = left + right
    return result


def detached_list_markers(sources: list[dict], selected: list[tuple[dict, str]]) -> dict[str, str]:
    """Recover a printed list number split into a separate OCR box for search display."""
    markers = []
    for source in sources:
        value = source.get('text', '').strip()
        box = source.get('locator', {}).get('bbox')
        if source.get('kind') == 'list_item' and re.fullmatch(r'\d{1,3}', value) and box:
            markers.append((source, value, box))
    result = {}
    for source, role in selected:
        if role != 'content' or re.match(r'^\s*\d{1,3}\s+', _source_quote(source)):
            continue
        box = source.get('locator', {}).get('bbox')
        if not box:
            continue
        page = source.get('locator', {}).get('physical_page')
        candidates = [(marker, value) for marker, value, bounds in markers
                      if marker['id'] != source['id']
                      and marker.get('locator', {}).get('physical_page') == page
                      and marker.get('section') == source.get('section')
                      and 0 <= box[0] - bounds[2] <= 45
                      and min(box[3], bounds[3]) - max(box[1], bounds[1]) >= .5 * min(box[3] - box[1], bounds[3] - bounds[1])]
        if len(candidates) == 1:
            result[source['id']] = candidates[0][1]
    return result


def text_evidence(sources: list[dict], document_id: str) -> list[dict]:
    """Group real clause members; keep all quotes separate and source-addressable.

    A printed clause starts a group, its numbered children and contiguous body
    remain in that group until another clause or heading. Continuation across
    adjacent pages requires unchanged section context. Explicit reviewed groups
    and parent links also work for documents without dotted clause identifiers.
    No engineering meaning, minimum text length or book-specific rule is used.
    """
    original_records = [asdict(source) if hasattr(source, '__dataclass_fields__') else source for source in sources]
    records = []
    for source in original_records:
        # A layout block may straddle a detached printed clause number: its
        # first line still belongs to the preceding list, while later lines
        # begin the new clause. Keep both exact source slices addressable.
        raw = source.get('text', '')
        lines = raw.splitlines(keepends=True)
        box = source.get('locator', {}).get('bbox')
        if (len(lines) >= 2 and box and re.match(r'^\s*\d{1,2}(?:[).、]|\s+)\s*\S', lines[0])
                and not clause_number(lines[0]) and not re.match(r'^\s*\d', lines[1])):
            marker = next((row for row in original_records
                           if row is not source and row.get('section') == source.get('section')
                           and row.get('locator', {}).get('physical_page') == source['locator'].get('physical_page')
                           and clause_number(row.get('text', '').strip()) == row.get('text', '').strip()
                           and row.get('locator', {}).get('bbox')
                           and box[1] < row['locator']['bbox'][1] < box[3]), None)
            if marker is not None:
                split = len(lines[0])
                records.extend(({**source, 'locator': {**source['locator'], 'char_range': [0, split]},
                                 'order': marker.get('order', 0) - .5},
                                {**source, 'locator': {**source['locator'], 'char_range': [split, len(raw)]},
                                 'order': marker.get('order', 0) + .5}))
                continue
        ranges = clause_ranges(source.get('text', '')) if source.get('kind') not in ('cell', 'table_note', 'picture', 'caption') else []
        if ranges and (len(ranges) > 1 or ranges[0][0] > 0):
            if ranges[0][0] > 0:
                ranges.insert(0, (0, ranges[0][0], None))
            for index, (start, end, _) in enumerate(ranges):
                records.append({**source, 'locator': {**source['locator'], 'char_range': [start, end]},
                                'order': source.get('order', 0) + index / (len(ranges) + 1)})
        else:
            records.append(source)
    indexed = {source['id']: source for source in original_records}
    def quote(source):
        text = source.get('text', '')
        bounds = source.get('locator', {}).get('char_range')
        return text[bounds[0]:bounds[1]] if bounds else text
    def key(source):
        return (source.get('locator', {}).get('physical_page') or 0, source.get('order', 0))
    def fragment(source):
        text = quote(source).strip()
        return not re.search(r'[A-Za-z\u4e00-\u9fff]', text) and bool(re.fullmatch(r'[\s\d.．、;；:：()（）\-—]+', text))
    groups = []; current = None; explicit = {}; pending = []
    for source in sorted(records, key=key):
        attrs = source.get('attributes', {})
        if source.get('kind') in ('cell', 'table_note', 'picture'):
            continue
        number = clause_number(quote(source))
        if source.get('kind') in ('heading', 'caption') and not number:
            if source.get('kind') == 'heading' and re.match(r'^\s*(?:\d+(?:[.．]\d+)*[\s\u4e00-\u9fff]|第[一二三四五六七八九十百\d]+[章节篇])', quote(source)):
                current = None
            continue
        # Retain rejected/margin fragments as stored sources but never use them
        # as source quotations for extraction or answers.
        if source.get('quality') in ('excluded', 'unrecognized', 'blank') or attrs.get('source_role') in ('header', 'footer', 'running_header', 'page_number'):
            continue
        if attrs.get('semantic_status') == 'context_only' and not attrs.get('cross_page_group') and not number:
            continue
        group_name = attrs.get('cross_page_group')
        parents = attrs.get('structural_parent_source_ids', [])
        previous = current['members'][-1] if current else None
        same_scope = previous and source.get('section', '') == previous.get('section', '')
        adjacent = previous and 0 <= key(source)[0] - key(previous)[0] <= 1
        linked = next((group for group in groups if any(row['id'] in parents for row in group['members'])), None)
        if group_name:
            current = explicit.get(group_name)
            if current is None:
                current = {'members': [], 'number': number, 'basis': 'reviewed_cross_page_group', 'section': source.get('section', '')}
                groups.append(current); explicit[group_name] = current
        elif number:
            current = {'members': [], 'number': number, 'basis': 'printed_clause', 'section': source.get('section', '')}
            groups.append(current)
        elif linked and not (current and current['number'] and same_scope and adjacent):
            current = linked
        elif not current or not (same_scope and adjacent) or current['basis'] not in ('printed_clause', 'reviewed_cross_page_group', 'explicit_parent'):
            if fragment(source):
                pending.append(source)
                continue
            current = {'members': [], 'number': None, 'basis': 'source_paragraph', 'section': source.get('section', '')}
            groups.append(current)
        if pending:
            # Only immediately contiguous fragments in the same scope belong to
            # this source. Others stay in SourceText, outside searchable Evidence.
            attached = [row for row in pending if row.get('section', '') == source.get('section', '') and key(row)[0] == key(source)[0]]
            current['members'].extend(attached); pending = []
            if not current['number']:
                number_fragment = next((clause_number(row.get('text', '')) for row in attached if clause_number(row.get('text', ''))), None)
                if number_fragment:
                    current['number'] = number_fragment; current['basis'] = 'printed_clause'
        current['members'].append(source)
    # Evidence preserves the complete source boundary.  A printed clause with
    # a numbered list is one Evidence containing its lead sentence and all
    # listed items; semantic extraction splits the independent requirements
    # into Statements later.  Unnumbered prose remains grouped by the source
    # paragraph/explicit continuation rules above.
    expanded = []
    for group in groups:
        group_id = identity('clause-group', document_id, [row['id'] for row in group['members']])
        expanded.append({**group, 'members': group['members'], 'introduction': [],
                         'item_number': None, 'group_id': group_id,
                         'root': group['members'][0]})
    results = []
    for group in expanded:
        content = group['members']
        if not content or not any(not fragment(row) for row in content):
            continue
        selected = []; seen = set()
        def source_key(row):
            return (row['id'], tuple(row.get('locator', {}).get('char_range', [])))
        for row in group['introduction']:
            selected.append((row, 'section')); seen.add(source_key(row))
        for source in content:
            attrs = source.get('attributes', {})
            contexts = list(attrs.get('section_sources', []))
            contexts += [indexed[key] for key in attrs.get('structural_parent_source_ids', []) if key in indexed]
            for row in contexts:
                parent_number = clause_number(row.get('text', ''))
                if group['number'] and parent_number and parent_number != group['number'] and not group['number'].startswith(parent_number + '.'):
                    continue
                if source_key(row) not in seen and row['id'] not in {entry['id'] for entry in [*content, *group['introduction']]}:
                    selected.append((row, 'scope' if row.get('attributes', {}).get('scope_source') or row.get('scope_source') else 'section')); seen.add(source_key(row))
        references = []
        for source in content:
            if source_key(source) not in seen:
                selected.append((source, 'fragment' if fragment(source) else 'content')); seen.add(source_key(source))
        for source in [*group['introduction'], *content]:
            for reference in parse_references(quote(source)):
                offset = source.get('locator', {}).get('char_range', [0])[0]
                reference = {**reference, 'start': reference['start'] + offset, 'end': reference['end'] + offset}
                references.append({**reference, 'source_id': source['id']})
        pages = sorted({row.get('locator', {}).get('physical_page') for row in content if row.get('locator', {}).get('physical_page') is not None})
        root = group['root']
        group_id = group['group_id']
        context = {'section': group['section'], 'clause_number': group['number'], 'clause_source_id': root['id'],
                   'clause_source_range': root.get('locator', {}).get('char_range'),
                   'clause_group_id': group_id, 'item_number': group['item_number'],
                   'physical_pages': pages, 'primary_page': pages[0] if pages else None,
                   'context_pages': sorted({row.get('locator', {}).get('physical_page') for row in group['introduction'] if row.get('locator', {}).get('physical_page') is not None}),
                   'assembly_basis': group['basis'],
                   'source_ids': list(dict.fromkeys(source['id'] for source, role in selected if role != 'fragment')),
                   'references': references, 'completeness': 'available_source_members'}
        markers = detached_list_markers(original_records, selected)
        if markers:
            context['detached_markers'] = markers
        results.append(make_evidence('text', selected, document_id, context=context))
    return results


def table_evidence(table: dict, sources: list[dict], document_id: str, context_sources: list[dict] | None = None) -> list[dict]:
    cells = deepcopy([source for source in sources if source["kind"] == "cell"])
    header_recognition = recognize_table_headers(cells, table['rows'], table['columns'])
    notes = [source for source in sources if source["kind"] == "table_note"]
    headers = [cell for cell in cells if cell.get("column_header")]
    body = [cell for cell in cells if not cell.get("column_header")]
    groups: set[tuple[int, ...]] = set()
    for row in range(table["rows"]):
        group = {row}
        while True:
            expanded = set(group)
            for cell in cells:
                covered = set(range(cell["row_index"], cell["row_index"] + cell["row_span"]))
                if covered & group: expanded |= covered
            if expanded == group: break
            group = expanded
        if any(cell["row_index"] in group for cell in body): groups.add(tuple(sorted(group)))
    results = []
    for group in sorted(groups):
        values = [cell for cell in body if set(range(cell["row_index"], cell["row_index"] + cell["row_span"])) & set(group)]
        selected = [(cell, "header") for cell in headers]
        selected += [(cell, "row_label" if cell.get("row_header") else "value") for cell in values]
        if not any(role == "value" for _, role in selected):
            selected = [(cell, "value" if role == "row_label" else role) for cell, role in selected]
        for note in notes:
            coverage = note.get("attributes", {}).get("coverage", {"status": "unknown"})
            covered_rows = coverage.get("rows")
            if not covered_rows or set(covered_rows) & set(group): selected.append((note, "note"))
        inherited = [source for source in (context_sources or []) if source.get('attributes',{}).get('source_role') == 'table_header']
        selected += [(source, "inherited_header" if source in inherited else source.get('attributes',{}).get('table_context_role','context')) for source in (context_sources or [])]
        number = next((ref['number'] for ref in parse_references(table.get('title', '')) if ref['kind'] == 'table'), None)
        results.append(make_evidence("table", selected, document_id, context={"table_id": table["id"], "table_number": number, "table_title": table["title"], "section": table.get('section', ''), "physical_pages": sorted({source.get('locator',{}).get('physical_page') for source, role in selected if role in ('header', 'row_label', 'value', 'note', 'inherited_header') and source.get('locator',{}).get('physical_page') is not None}), "primary_page": table.get('locator',{}).get('physical_page'), "rows": list(group), "columns": table["columns"], "header_quality": "known" if headers or inherited else "unknown", "reviewed_header_hierarchy":table.get('locator',{}).get('reviewed_header_hierarchy',[]), "notes_coverage":[note.get('attributes',{}).get('coverage',{'status':'unknown'}) for note in notes], "continuation": table.get("continuation", {"status": "unknown"}), "quality": table.get("quality", "unknown"), "body_context_status":table.get('locator',{}).get('body_context_status','explicit_links_only'), "missing_context_pages":table.get('locator',{}).get('missing_context_pages',[])}))
        results[-1]['context']['header_recognition'] = header_recognition
        if header_recognition['status'] == 'inferred':
            results[-1]['context']['header_quality'] = 'inferred'
    return results


class ContextAssembler:
    def __init__(self, store):
        self.store = store
        self.cache: dict[str, dict] = {}

    def read(self, evidence_id: str, snapshot: Snapshot, budget: Budget | None = None) -> dict:
        evidence_rows = self.store.get([evidence_id], snapshot.document_ids, budget)
        if not evidence_rows: raise ValueError("Evidence不属于本次固定版本")
        evidence = evidence_rows[0]
        members = evidence["members"]
        if len({item["member_key"] for item in members}) != len(members): raise ValueError("Evidence成员标识重复")
        sources = self.store.get([item["source_id"] for item in members], snapshot.document_ids, budget)
        by_id = {item["id"]: item for item in sources}
        if any(item["source_id"] not in by_id for item in members): raise ValueError("Evidence来源成员缺失")
        key = fingerprint((snapshot.key, [(item, by_id[item["source_id"]]) for item in members]))
        cache_key = fingerprint((key,evidence.get('kind'),evidence.get('context',{})))
        if cache_key in self.cache: return deepcopy(self.cache[cache_key])
        assembled = []
        for item in sorted(members, key=lambda row: row["order"]):
            source = by_id[item["source_id"]]
            loc = item["locator"]
            quote = _source_quote(source, loc)
            review = _verified_formula_for_locator(source, loc)
            attributes = {**source.get('attributes', {}), **{field: source[field] for field in ('row_index', 'column_index', 'row_span', 'column_span', 'column_header', 'row_header', 'table_id') if field in source}}
            # These sources are already explicit members of the complete evidence.
            # Repeating their full records inside every member adds no information.
            attributes.pop('section_sources', None)
            attributes.pop('verified_scopes', None)
            if 'structural_parent_source_ids' in attributes:
                attributes['structural_parent_source_ids']=[source_id for source_id in
                    attributes['structural_parent_source_ids'] if source_id in by_id]
            correction = {}
            if review:
                correction = {'raw_quote': quote, 'text_origin': 'verified_correction'}
                quote = review['verified_text']
            elif attributes.pop('human_verified_representation', None) is not None:
                attributes['human_review_status'] = 'stale_or_partial_binding'
            assembled.append({**item, "quote": quote, **correction, "quality": source.get("quality", "unknown"), "source_kind": source["kind"], "attributes": attributes, **({"image": {field: source.get(field) for field in ("path", "content_hash", "width", "height", "details", "links")}} if source["kind"] == "picture" else {})})
        documents = self.store.get([sources[0]["document_id"]], None, budget)
        # Older current-document nodes may not have a title after the
        # lifecycle-field migration.  Evidence assembly must remain usable
        # because the title is presentation metadata, not a required source
        # binding.
        title = (documents[0].get("title") or documents[0].get("id") or "资料标题未确认") if documents else "资料标题未确认"
        context = deepcopy(evidence.get('context', {}))
        if evidence['kind'] in ('picture', 'image'):
            picture = next((source for source in sources if source['kind'] == 'picture'), {})
            context.setdefault('picture_id', picture.get('id'))
            context.setdefault('figure_title', picture.get('title', ''))
            context.setdefault('figure_number', next((ref['number'] for ref in parse_references(picture.get('title', '')) if ref['kind'] == 'figure'), None))
        result = {"evidence_id": evidence_id, "document_id": sources[0]['document_id'], "revision_id": sources[0]["revision_id"], "title": title, "kind": evidence["kind"], "context": context, "members": assembled, "member_fingerprint": key,
                  "text": evidence_text(context, [item['quote'] for item in assembled],
                                        [item['role'] for item in assembled],
                                        [item['source_kind'] for item in assembled],
                                        kind=evidence['kind'])}
        self.cache[cache_key] = deepcopy(result)
        return result

    @staticmethod
    def text(view: dict) -> str:
        return view.get('text') or evidence_text(
            view.get('context', {}),
            [row.get('quote', '') for row in view.get('members', [])],
            [row.get('role', 'content') for row in view.get('members', [])],
            [row.get('source_kind') for row in view.get('members', [])],
            kind=view.get('kind', 'text'))
