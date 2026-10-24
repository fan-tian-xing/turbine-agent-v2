"""Source-bearing document objects. Processing views stay in memory."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from unicodedata import normalize
from typing import Any


@dataclass
class SourceUnit:
    id: str
    kind: str
    text: str
    locator: dict[str, Any]
    text_origin: str
    order: int
    section: str = ""
    quality: str = "readable"
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class TableData:
    id: str
    title: str
    section: str
    locator: dict[str, Any]
    rows: int
    columns: int
    cells: list[SourceUnit]
    notes: list[SourceUnit] = field(default_factory=list)
    quality: str = "parsed"
    continuation: dict[str, Any] = field(default_factory=lambda: {"status": "unknown"})
    order: int = 0


@dataclass
class Picture:
    id: str
    path: str
    content_hash: str
    width: int
    height: int
    locator: dict[str, Any]
    title: str
    section: str
    links: list[dict[str, str]] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)
    order: int = 0


@dataclass
class ParsedPage:
    physical_page: int
    logical_page: str | None
    width: float
    height: float
    mapping: dict[str, Any]
    sources: list[SourceUnit] = field(default_factory=list)
    tables: list[TableData] = field(default_factory=list)
    pictures: list[Picture] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "parsed"


def bind_list_parents(units):
    """Retain real structural parents; this does not infer semantic inheritance.

    Some layout models flatten numbered clauses and their enumerations into one
    list group. An explicit clause number followed by a colon and simple numbered
    children can restore that relation without inspecting the clause's meaning.
    """
    refs={unit.attributes.get('layout_ref'):unit for unit in units if unit.attributes.get('layout_ref')}
    states={};previous=None
    def number(unit):
        marker=str(unit.attributes.get('list_marker') or '').strip().rstrip(').、')
        if not re.fullmatch(r'\d+(?:[.．]\d+)*',marker):
            match=re.match(r'^\s*(\d+(?:[.．]\d+)*)(?:[)、.]?\s+)',unit.text)
            marker=match.group(1) if match else ''
        return tuple(marker.replace('．','.').split('.')) if marker else ()
    def enumeration_style(unit):
        # Keep printed marker forms distinct even when the layout groups flatten
        # them: 1, 1), and (1) may represent different nesting levels.
        text=normalize('NFKC',unit.text).lstrip()
        for style,pattern in (('wrapped',r'^\((\d+)\)'),('suffix',r'^(\d+)\)'),
                              ('plain',r'^(\d+)(?:[.、]?\s+|$)')):
            match=re.match(pattern,text)
            if match:return style,int(match.group(1))
        return None,None
    for unit in sorted(units,key=lambda entry:entry.order):
        if unit.kind!='list_item':previous=unit;continue
        attrs=unit.attributes;parents=[];basis=[]
        for ref in reversed(attrs.get('layout_ancestor_refs',[])):
            parent=refs.get(ref)
            if parent and parent.id!=unit.id and parent.kind in ('paragraph','list_item'):
                parents.append(parent);basis.append('explicit_layout_ancestor')
        group=attrs.get('layout_parent_ref')
        if group:
            state=states.setdefault(group,{'clauses':[],'intros':[],'styles':[]})
            if not (previous and previous.kind=='list_item' and previous.attributes.get('layout_parent_ref')==group and previous.order+1==unit.order):
                state['clauses']=[];state['intros']=[];state['styles']=[]
            path=number(unit)
            if len(path)>1:
                state['clauses']=[(key,parent) for key,parent in state['clauses'] if len(key)<len(path) and path[:len(key)]==key]
                for _,parent in state['clauses']:parents.append(parent);basis.append('explicit_number_prefix')
                state['clauses'].append((path,unit))
                state['intros']=[(-1,unit)] if unit.text.rstrip().endswith((':','：')) else []
                state['styles']=[]
            else:
                style,ordinal=enumeration_style(unit)
                level=len(state['styles'])
                if style:
                    if style in state['styles']:
                        level=state['styles'].index(style)
                        state['styles']=state['styles'][:level+1]
                    else:
                        state['styles'].append(style)
                    # A sibling closes its predecessor and all nested scopes.
                    state['intros']=[entry for entry in state['intros'] if entry[0]<level]
                adjacent=previous and previous.order+1==unit.order
                if adjacent and previous.kind=='paragraph' and previous.text.rstrip().endswith((':','：')) and ordinal in (None,1):
                    state['intros'].append((-1,previous))
                if state['intros']:
                    parents.append(state['intros'][-1][1]);basis.append('same_list_group_numbered_introduction')
                if unit.text.rstrip().endswith((':','：')):
                    state['intros'].append((level,unit))
        unique=[]
        for parent in parents:
            if parent.id!=unit.id and parent.id not in {entry.id for entry in unique}:unique.append(parent)
        if unique:
            attrs['structural_parent_source_ids']=[parent.id for parent in unique]
            attrs['structural_context_basis']=list(dict.fromkeys(basis))
            attrs['semantic_inheritance']='not_inferred'
            contexts=attrs.setdefault('section_sources',[])
            for parent in unique:
                if not any(entry['id']==parent.id for entry in contexts):contexts.append(asdict(parent))
        previous=unit


_NUMBER = r'(?:[A-Za-z](?:\s*[.．]\s*)?)?\d+(?:\s*[.．]\s*\d+)*|[A-Za-z]'
_CLAUSE_START = re.compile(r'^\s*(\d+(?:[.．]\d+){2,})(?=\s|[\u4e00-\u9fff]|$)')


def normalized_number(value):
    return re.sub(r'\s+', '', normalize('NFKC', value)).upper()


def clause_number(text):
    """A printed clause identifier, never a scope inferred from its prefix."""
    match = _CLAUSE_START.match(normalize('NFKC', text))
    return normalized_number(match.group(1)) if match else None


def clause_ranges(text):
    """Offsets of printed clause starts inside a layout block, with no new text."""
    starts = []
    offset = 0
    for line in text.splitlines(keepends=True):
        number = clause_number(line)
        if number:
            starts.append((offset, number))
        offset += len(line)
    return [(start, starts[index + 1][0] if index + 1 < len(starts) else len(text), number)
            for index, (start, number) in enumerate(starts)]


def parse_references(text):
    """Parse explicit source references; numerical engineering values are excluded.

    Offsets and quotes refer to the original text, including full-width symbols.
    Range expansion belongs to the index and only selects actual source entries.
    """
    from turbine_kg.knowledge.quantities import UNIT_PATTERN, normalize_quantity_text
    number = '(?:' + _NUMBER + ')'
    suffix = rf'(?:\s*(?:条款|条|节)?\s*(?:至|到|～|~|—|–|－|-)\s*第?\s*(?P<endpoint>{number}))?'
    patterns = [
        (re.compile(rf'(?P<label>条款|章节|条|表|图|table|figure|clause)\s*(?P<number>{number}){suffix}(?![\d.．])', re.I), 'prefix'),
        (re.compile(rf'(?<![\d.．])(?P<number>{number}){suffix}\s*(?P<label>条款|条|表|图)(?!号)', re.I), 'suffix'),
        (re.compile(rf'(?:符合|按照?|参照|依据|参见|见)[^\d\n。；]{{0,18}}?(?P<number>\d+(?:\s*[.．]\s*\d+)+){suffix}(?![\d.．A-Za-z%℃])'), 'cue'),
    ]
    found = []
    for pattern, style in patterns:
        for match in pattern.finditer(text):
            if any(match.start() < row['end'] and match.end() > row['start'] for row in found):
                continue
            label = match.groupdict().get('label', '条').lower()
            kind = 'table' if label in ('表', 'table') else 'figure' if label in ('图', 'figure') else 'clause'
            value = normalized_number(match.group('number'))
            # "按1.25倍" and "低于0.10mm" describe quantities, not clauses.
            if style == 'cue' and re.match(r'\s*(?:倍|' + UNIT_PATTERN + r')(?![A-Za-z/])',
                                          normalize_quantity_text(text[match.end('number'):])):
                continue
            if style == 'cue' and re.search(r'[A-Z]{1,8}\s*/\s*T?\s*$', match.group()[:match.start('number')-match.start()]):
                continue
            if kind == 'clause' and '.' not in value:
                continue
            endpoint = normalized_number(match.group('endpoint')) if match.group('endpoint') else None
            sentence_start = max(text.rfind('。', 0, match.start()), text.rfind('；', 0, match.start()), text.rfind('\n', 0, match.start())) + 1
            external = bool(re.search(r'(?:[A-Z]{1,8}\s*/?\s*T?\s*\d{3,}|《[^》]+》)', text[sentence_start:match.start()]))
            found.append({'kind': kind, 'number': value, 'end_number': endpoint,
                          'quote': match.group(), 'start': match.start(), 'end': match.end(),
                          'external_document': external})
    return sorted(found, key=lambda entry: entry['start'])


class SourceIndex:
    """An in-memory structural index scoped by its caller's pinned source version."""
    def __init__(self, sources, tables=None, pictures=None):
        self.entries = []
        for source in sources:
            row = asdict(source) if isinstance(source, SourceUnit) else source
            ranges = clause_ranges(row.get('text', ''))
            if ranges and row.get('kind') not in ('caption', 'cell', 'table_note'):
                for start, end, number in ranges:
                    self.entries.append({'kind': 'clause', 'number': number, 'source_ids': [row['id']], 'char_range': [start, end],
                                     'resource_ids': [], 'section': row.get('section', ''),
                                     'physical_page': row.get('locator', {}).get('physical_page'),
                                     'order': row.get('order', 0)})
            if row.get('kind') == 'heading' and not ranges:
                heading = re.match(r'^\s*(\d+(?:[.．]\d+)*)(?=\s|[\u4e00-\u9fff]|$)', row.get('text', ''))
                if heading:
                    self.entries.append({'kind': 'section', 'number': normalized_number(heading.group(1)),
                                         'source_ids': [row['id']], 'resource_ids': [], 'section': row.get('section', ''),
                                         'physical_page': row.get('locator', {}).get('physical_page'), 'order': row.get('order', 0)})
        for kind, resources in (('table', tables or []), ('figure', pictures or [])):
            for resource in resources:
                row = asdict(resource) if hasattr(resource, '__dataclass_fields__') else resource
                references = [ref for ref in parse_references(row.get('title', '')) if ref['kind'] == kind]
                if len(references) == 1:
                    self.entries.append({'kind': kind, 'number': references[0]['number'], 'source_ids': [],
                                         'resource_ids': [row['id']], 'section': row.get('section', ''),
                                         'physical_page': row.get('locator', {}).get('physical_page'),
                                         'order': row.get('order', 0)})

    def resolve(self, reference, origin=None):
        if reference.get('external_document'):
            return {'status': 'external_document', 'source_ids': [], 'resource_ids': [], 'candidates': []}
        origin = origin or {}
        matches = [row for row in self.entries if row['kind'] == reference['kind'] and row['number'] == reference['number']]
        if reference['kind'] == 'clause' and not matches and not reference.get('end_number'):
            # Section references resolve their actual printed child clauses; this
            # is source assembly, not engineering-property inheritance.
            matches = [row for row in self.entries if row['kind'] == 'clause' and row['number'].startswith(reference['number'] + '.')]
            if not matches:
                matches = [row for row in self.entries if row['kind'] == 'section' and row['number'] == reference['number']]
        endpoint = reference.get('end_number')
        if endpoint:
            start_parts, end_parts = reference['number'].split('.'), endpoint.split('.')
            if start_parts[:-1] != end_parts[:-1] or not start_parts[-1].isdigit() or not end_parts[-1].isdigit():
                return {'status': 'ambiguous_range', 'source_ids': [], 'resource_ids': [], 'candidates': []}
            matches = [row for row in self.entries if row['kind'] == reference['kind'] and row['number'].split('.')[:-1] == start_parts[:-1]
                       and row['number'].split('.')[-1].isdigit() and int(start_parts[-1]) <= int(row['number'].split('.')[-1]) <= int(end_parts[-1])]
        selected = []
        for number in dict.fromkeys(row['number'] for row in matches):
            candidates = [row for row in matches if row['number'] == number]
            same_section = [row for row in candidates if origin.get('section') and row['section'] == origin['section']]
            if same_section:
                candidates = same_section
            page = origin.get('physical_page', origin.get('locator', {}).get('physical_page'))
            if len(candidates) > 1 and page is not None:
                distances = [(abs(row['physical_page'] - page), row) for row in candidates if row['physical_page'] is not None]
                if distances:
                    minimum = min(distance for distance, _ in distances)
                    candidates = [row for distance, row in distances if distance == minimum]
            if len(candidates) != 1:
                return {'status': 'ambiguous', 'source_ids': [], 'resource_ids': [], 'candidates': matches}
            selected.extend(candidates)
        if endpoint and (not any(row['number'] == reference['number'] for row in selected) or not any(row['number'] == endpoint for row in selected)):
            return {'status': 'missing_range_endpoint', 'source_ids': [], 'resource_ids': [], 'candidates': matches}
        return {'status': 'resolved' if selected else 'missing',
                'source_ids': [key for row in selected for key in row['source_ids']],
                'resource_ids': [key for row in selected for key in row['resource_ids']], 'candidates': selected}


def recognize_table_headers(cells, rows, columns):
    """Preserve model headers or conservatively recover a simple unit header row.

    Vector rulings verify geometry, not semantic header roles. Recovery requires
    an explicit unit in a nonnumeric first-row label and at least two numeric
    body rows in the same physical column. Complex or ambiguous layouts remain
    unknown rather than being guessed from book-specific words or titles.
    """
    def field(cell, name, default=None):
        if isinstance(cell, SourceUnit):
            return cell.attributes.get(name, default)
        return cell.get(name, cell.get('attributes', {}).get(name, default))
    def text(cell):
        return cell.text if isinstance(cell, SourceUnit) else cell.get('text', '')
    def set_field(cell, name, value):
        if isinstance(cell, SourceUnit):
            cell.attributes[name] = value
        else:
            cell.setdefault('attributes', {})[name] = value
            if name in cell:
                cell[name] = value
    existing = [cell for cell in cells if field(cell, 'column_header', False)]
    if existing:
        for cell in existing:
            if not field(cell, 'header_basis'):
                set_field(cell, 'header_basis', 'layout_model')
        return {'status': 'inferred' if any(field(cell, 'header_quality') == 'inferred' for cell in existing) else 'known',
                'basis': list(dict.fromkeys(field(cell, 'header_basis') for cell in existing)),
                'rows': sorted({field(cell, 'row_index') for cell in existing})}
    if rows < 3 or columns < 1:
        return {'status': 'unknown', 'basis': 'insufficient_header_structure'}
    first = [cell for cell in cells if field(cell, 'row_index') == 0]
    covered = {column for cell in first for column in range(field(cell, 'column_index', 0), field(cell, 'column_index', 0) + field(cell, 'column_span', 1))}
    if not first or covered != set(range(columns)) or any(field(cell, 'row_span', 1) != 1 or not text(cell).strip() or field(cell, 'cell_state') in ('missing', 'unrecognized') for cell in first):
        return {'status': 'unknown', 'basis': 'incomplete_or_merged_header_structure'}
    numeric = re.compile(r'^[<>≤≥≦≧±+−-]?\d+(?:[.,]\d+)?(?:[～~—–-][±+−-]?\d+(?:[.,]\d+)?)?[%％]?$')
    unit = re.compile(r'(?<![A-Za-z])(?:mm|cm|km|m|MPa|kPa|Pa|rpm|r/min|kg|mg|g|kN|N|ms|s|min|h|Hz|kW|W|V|A|℃|°C|%|％)(?:[²³23]|/[A-Za-z]+)?(?![A-Za-z])')
    if any(numeric.fullmatch(re.sub(r'\s+', '', normalize('NFKC', text(cell)))) for cell in first):
        return {'status': 'unknown', 'basis': 'first_row_contains_values'}
    unit_columns = []
    for header in first:
        marker = unit.search(normalize('NFKC', text(header)))
        if not marker:
            continue
        start = field(header, 'column_index', 0)
        end = start + field(header, 'column_span', 1)
        for column in range(start, end):
            body = [cell for cell in cells if field(cell, 'row_index', 0) > 0 and field(cell, 'column_index', 0) <= column < field(cell, 'column_index', 0) + field(cell, 'column_span', 1) and text(cell).strip()]
            if len({field(cell, 'row_index') for cell in body}) < 2:
                continue
            if all(field(cell, 'cell_state') not in ('missing', 'unrecognized') and numeric.fullmatch(re.sub(r'\s+', '', normalize('NFKC', text(cell)))) for cell in body):
                unit_columns.append({'column': column, 'unit': marker.group()})
    if not unit_columns:
        return {'status': 'unknown', 'basis': 'unit_column_numeric_body_not_confirmed'}
    for cell in first:
        set_field(cell, 'column_header', True)
        set_field(cell, 'header_basis', 'explicit_unit_label_with_numeric_column')
        set_field(cell, 'header_quality', 'inferred')
    for cell in cells:
        if field(cell, 'row_index', 0) > 0 and field(cell, 'column_index', 0) == 0 and field(cell, 'column_span', 1) == 1 and text(cell).strip() and not numeric.fullmatch(re.sub(r'\s+', '', normalize('NFKC', text(cell)))):
            set_field(cell, 'row_header', True)
            set_field(cell, 'row_header_basis', 'nonnumeric_first_column_under_header')
    return {'status': 'inferred', 'basis': ['explicit_unit_label_with_numeric_column'], 'rows': [0], 'unit_columns': unit_columns}
