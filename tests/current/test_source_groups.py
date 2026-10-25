"""Generic clause grouping and reference lookup, without model calls or files."""
import pytest
from turbine_kg.documents.ir import SourceIndex, parse_references
from turbine_kg.evidence.context import ContextAssembler, make_evidence, text_evidence, table_evidence
from turbine_kg.knowledge.formula_sources import confirm_formula_source, formula_source_binding
from turbine_kg.runtime import Snapshot


def source(key, text, page=1, order=0, section='2 Test', **attributes):
    return {'id': key, 'kind': 'paragraph', 'text': text, 'locator': {'physical_page': page},
            'text_origin': 'native_text', 'order': order, 'section': section,
            'quality': 'readable', 'attributes': attributes}


def test_complete_numbered_clause_spans_adjacent_pages_and_keeps_member_ids():
    rows = [source('a', '2.1.1 检查应符合下列规定：'), source('b', '1 阀门应灵活。', order=1),
            source('c', '2 管路应畅通。', page=2), source('d', '2.1.2 另项要求。', page=2, order=1)]
    evidence = text_evidence(rows, 'g')
    assert len(evidence) == 2
    first = evidence[0]
    assert first['context']['source_ids'] == ['a', 'b', 'c']
    assert first['context']['physical_pages'] == [1, 2]
    assert first['context']['clause_number'] == '2.1.1'
    assert [member['source_id'] for member in first['members']] == ['a', 'b', 'c']
    assert evidence[1]['context']['source_ids'] == ['d']
    assert evidence[1]['context']['physical_pages'] == [2]
    assert evidence[0]['context']['item_number'] is None


def test_heading_and_page_gap_prevent_invented_cross_page_parent():
    heading = {**source('h', '2.2 Other', order=1), 'kind': 'heading'}
    rows = [source('a', '2.1.1 规定：'), heading, source('b', '应记录。', order=2),
            source('c', '3 条件', page=4)]
    evidence = text_evidence(rows, 'g')
    assert len(evidence) == 3
    assert evidence[0]['context']['source_ids'] == ['a']


def test_fragment_number_is_bound_and_short_real_requirement_is_retained():
    rows = [source('n', '2.1.1'), source('a', '应接地。', order=1),
            source('mark', '；', order=2), source('b', '2.1.2 应清洁。', order=3)]
    evidence = text_evidence(rows, 'g')
    assert len(evidence) == 2
    assert [member['source_id'] for member in evidence[0]['members']] == ['a']
    assert evidence[0]['context']['source_ids'] == ['a']
    assert evidence[0]['context']['clause_number'] == '2.1.1'
    assert evidence[1]['context']['source_ids'] == ['b']
    assert text_evidence([source('isolated', '14'), source('punctuation', '；', order=1)], 'g') == []


def test_explicit_parent_keeps_unrelated_paragraphs_separate():
    rows = [source('a', 'Check the following:'), source('b', 'First condition.', order=1, structural_parent_source_ids=['a']),
            source('c', 'Independent paragraph.', order=2)]
    evidence = text_evidence(rows, 'g')
    assert len(evidence) == 2
    assert evidence[0]['context']['source_ids'] == ['a', 'b']


def test_reference_parser_supports_prefix_suffix_ranges_and_real_quotes():
    text = '按2.1.1～2.1.3条及表A.1规定，另见2.2.4表、图3.1和条款２．４．５；间隙0.05mm。'
    refs = parse_references(text)
    assert [(row['kind'], row['number'], row['end_number']) for row in refs] == [
        ('clause', '2.1.1', '2.1.3'), ('table', 'A.1', None), ('table', '2.2.4', None),
        ('figure', '3.1', None), ('clause', '2.4.5', None)]
    assert all(text[row['start']:row['end']] == row['quote'] for row in refs)
    assert parse_references('可参照表E。')[0]['number'] == 'E'


def test_reference_index_disambiguates_by_section_and_rejects_equal_candidates():
    rows = [source('a', '2.1.1 要求。', section='A'), source('b', '2.1.1 其他要求。', section='B')]
    index = SourceIndex(rows)
    ref = parse_references('按2.1.1条。')[0]
    assert index.resolve(ref, {'section': 'B'})['source_ids'] == ['b']
    assert index.resolve(ref, {'physical_page': 1})['status'] == 'ambiguous'
    external = parse_references('应符合DL/T 9999的2.1.1条。')[0]
    assert index.resolve(external)['status'] == 'external_document'


def test_range_selects_actual_sources_and_requires_endpoints():
    index = SourceIndex([source(str(i), f'2.1.{i} 要求。') for i in (1, 2, 3)])
    ref = parse_references('按2.1.1～2.1.3条。')[0]
    assert index.resolve(ref)['source_ids'] == ['1', '2', '3']
    missing = SourceIndex([source('a', '2.1.1 要求。')]).resolve(ref)
    assert missing['status'] == 'missing_range_endpoint'


def test_reference_cues_without_suffix_and_section_children_are_generic():
    refs = parse_references('应符合7.2.3 的有关规定，参照7.4的规定；按7.5.1～7.5.3执行。')
    assert [(row['number'], row['end_number']) for row in refs] == [('7.2.3', None), ('7.4', None), ('7.5.1', '7.5.3')]
    assert not parse_references('7.2.3 应记录；偏差应符合0.04mm；应符合DL/T 1234.5。')
    index = SourceIndex([source('a', '7.4.1 要求。'), source('b', '7.4.2 检查。'), source('c', '7.5.1 其他。')])
    assert index.resolve(refs[1])['source_ids'] == ['a', 'b']


def test_multiclause_layout_block_uses_exact_source_character_ranges():
    block = source('a', '3 温度应低于1000℃。\n7.2.4 基础检查应符合下列规定：')
    rows = [source('previous', '7.2.3 校正应符合下列规定：'), block,
            source('b', '1 基础应合格。', order=1, structural_parent_source_ids=['previous'])]
    evidence = text_evidence(rows, 'g')
    assert [row['context']['clause_number'] for row in evidence] == ['7.2.3', '7.2.4']
    assert evidence[1]['context']['source_ids'] == ['a', 'b']
    slices = [block['text'][member['locator']['char_range'][0]:member['locator']['char_range'][1]]
              for row in evidence for member in row['members'] if member['source_id'] == 'a']
    assert ''.join(slices) == block['text']
    index = SourceIndex(rows)
    assert index.resolve(parse_references('见7.2.4。')[0])['source_ids'] == ['a']


def test_running_header_does_not_break_clause_but_mislabeled_clause_is_body():
    rows = [source('a', '7.2.3 规定：'), source('b', '1 记录。', order=1),
            {**source('header', 'DOCUMENT 123 — 2020', page=2), 'kind': 'heading'},
            source('c', '2 检查。', page=2, order=1),
            {**source('d', '7.2.4 规定：', page=2, order=2, semantic_status='context_only'), 'kind': 'heading'},
            source('e', '1 应清洁。', page=2, order=3)]
    evidence = text_evidence(rows, 'g')
    assert len(evidence) == 2
    assert evidence[0]['context']['source_ids'] == ['a', 'b', 'c']
    assert evidence[1]['context']['source_ids'] == ['d', 'e']


def test_numbered_item_keeps_cross_page_sentence_and_excludes_next_sibling():
    rows = [source('a', '5.2.3 检查应符合下列规定：'),
            source('b', '1 管板和隔板中心应拉', order=1),
            source('c', '钢丝复查合格；\n2 壳体应清洁。', page=2)]
    [first] = text_evidence(rows, 'g')
    assert first['context']['physical_pages'] == [1, 2]
    assert '钢丝复查合格' in first['text']
    assert '壳体应清洁' in first['text']
    assert '\n2 壳体应清洁' in first['text']
    assert first['context']['item_number'] is None


def test_single_source_slices_preserve_intro_nested_items_and_exact_quotes():
    raw = '5.2.4 检查应符合下列规定：\n1 铜管：\n1）压扁试验；\n2）扩口试验。\n2 钢管应检查。'
    row = source('a', raw)
    [first] = text_evidence([row], 'g')
    assert '2）扩口试验' in first['text']
    assert '钢管' in first['text']
    assert len(first['members']) == 1
    assert {m['source_id'] for m in first['members']} == {'a'}


def test_cached_sibling_parent_cannot_import_other_items_conditions():
    from turbine_kg.runtime import Snapshot
    rows=[source('root','7.2.1 检验要求：'),source('copper','1 铜管检验：',order=1),
          source('sampling','1）按规定比例抽样进行试验：',order=2),
          source('test','（1）压扁；（2）扩口。',order=3),
          source('steel','2 钢管检验：',order=4,structural_parent_source_ids=['sampling']),
          source('steel-test','1）检查表面；2）检查尺寸。',order=5,structural_parent_source_ids=['steel'])]
    rows[4]['attributes']['section_sources']=[rows[2]]
    [first]=text_evidence(rows,'g')
    assert '按规定比例抽样' in first['text']
    assert '检查表面' in first['text']
    assert {m['source_id'] for m in first['members']}=={'root','copper','sampling','test','steel','steel-test'}
    records={row['id']:{**row,'document_id':'doc','revision_id':'rev'} for row in rows}
    records.update({first['id']:first,'doc':{'id':'doc','title':'Test'}})
    class Store:
        def get(self,ids,*args):return [records[key] for key in ids if key in records]
    view=ContextAssembler(Store()).read(first['id'],Snapshot(('g',),'v'))
    members={m['source_id']:m for m in view['members']}
    assert members['steel']['attributes']['structural_parent_source_ids']==['sampling']
    assert members['steel-test']['attributes']['structural_parent_source_ids']==['steel']
    assert rows[4]['attributes']['structural_parent_source_ids']==['sampling']


@pytest.mark.parametrize('marker_order,mixed_order', [(0, 1), (1, 0)])
def test_detached_clause_number_splits_overlapping_previous_list_line(marker_order, mixed_order):
    rows = [source('previous', '7.2.3 检查应符合下列规定：'),
            source('last', '10 前项应合格。', order=1),
            source('number', '7.2.4', page=2, order=marker_order, section='2 Test',
                   bbox=[50, 20, 80, 28]),
            source('mixed', '11 支座应平整。\n就位前应检查密封面。', page=2, order=mixed_order,
                   bbox=[80, 10, 370, 30]),
            source('child', '1 密封面应清洁。', page=2, order=2)]
    for row in rows:
        if 'bbox' in row['attributes']:
            row['locator']['bbox'] = row['attributes'].pop('bbox')
    evidence = text_evidence(rows, 'g')
    assert [item['context']['clause_number'] for item in evidence] == ['7.2.3', '7.2.4']
    first, second = evidence
    assert first['context']['source_ids'] == ['previous', 'last', 'mixed']
    assert second['context']['source_ids'] == ['mixed', 'child']
    assert next(member for member in first['members'] if member['source_id'] == 'mixed')['locator']['char_range'] == [0, len('11 支座应平整。\n')]
    assert next(member for member in second['members'] if member['source_id'] == 'mixed')['locator']['char_range'] == [len('11 支座应平整。\n'), len(rows[3]['text'])]


def test_table_context_preserves_headers_notes_and_display_number():
    header = {**source('h', '允许偏差mm'), 'kind': 'cell', 'column_header': True,
              'row_index': 0, 'row_span': 1, 'column_index': 0, 'column_span': 1}
    value = {**source('v', '0.04'), 'kind': 'cell', 'column_header': False,
             'row_index': 1, 'row_span': 1, 'column_index': 0, 'column_span': 1}
    note = {**source('n', '制造厂有规定时按制造厂要求。'), 'kind': 'table_note'}
    result = table_evidence({'id': 't', 'title': '表2.1.4 偏差', 'section': 'A', 'rows': 2, 'columns': 1,
                             'locator': {'physical_page': 1}}, [header, value, note], 'g')
    assert result[0]['context']['table_number'] == '2.1.4'
    assert {member['role'] for member in result[0]['members']} == {'header', 'value', 'note'}


def test_reference_range_with_repeated_clause_label_expands_all_members():
    from turbine_kg.knowledge.chapter_processing import _expand_references
    rows=[source('origin','2.2.1 应符合第2.1.1 条～第2.1.3 条的规定。')]
    rows += [source(str(i),f'2.1.{i} 检查要求。',order=i) for i in range(1,4)]
    refs=parse_references(rows[0]['text'])
    assert len(refs)==1 and refs[0]['end_number']=='2.1.3'
    evidence=text_evidence(rows,'d')
    _expand_references(evidence,rows,[],[],[1])
    origin=next(row for row in evidence if row['context']['clause_number']=='2.2.1')
    expected={row['id'] for row in evidence if row['context']['clause_number'].startswith('2.1.')}
    assert set(origin['context']['reference_evidence_ids'])==expected
    assert origin['context']['reference_resolution'][0]['status']=='resolved'


def test_reference_range_keeps_clause_whose_printed_number_is_detached_from_body():
    from turbine_kg.knowledge.chapter_processing import _expand_references
    rows=[source('a','2.1.1 设备应清洁。'),source('marker','2.1.2',order=1),
          source('b','设备应接地。',order=2),source('c','2.1.3 管路应畅通。',order=3),
          source('origin','2.2.1 应符合第2.1.1条～第2.1.3条的规定。',order=4)]
    evidence=text_evidence(rows,'d')
    detached=next(row for row in evidence if row['context']['clause_number']=='2.1.2')
    assert detached['context']['source_ids']==['b']
    assert all(member['source_id']!='marker' for member in detached['members'])
    _expand_references(evidence,rows,[],[],[1])
    origin=next(row for row in evidence if row['context']['clause_number']=='2.2.1')
    targets={row['id'] for row in evidence if row['context']['clause_number'].startswith('2.1.')}
    assert len(targets)==3 and set(origin['context']['reference_evidence_ids'])==targets
    assert origin['context']['reference_resolution'][0]['status']=='resolved'


def test_assembled_clause_reference_respects_document_and_real_ambiguity():
    from copy import deepcopy
    from turbine_kg.knowledge.chapter_processing import _expand_references
    rows=[{**source('target','2.1.1 设备应检查。'),'document_id':'d'},
          {**source('origin','2.2.1 应符合2.1.1条的规定。',order=1),'document_id':'d'}]
    evidence=text_evidence(rows,'d')
    for row in evidence:row['document_id']='d'
    target=next(row for row in evidence if row['context']['clause_number']=='2.1.1')
    foreign={**deepcopy(target),'id':'foreign','document_id':'other'}
    _expand_references([*evidence,foreign],rows,[],[],[1])
    origin=next(row for row in evidence if row['context']['clause_number']=='2.2.1')
    assert origin['context']['reference_evidence_ids']==[target['id']]
    duplicate={**deepcopy(target),'id':'ambiguous'}
    _expand_references([*evidence,duplicate],rows,[],[],[1])
    assert origin['context']['reference_resolution'][0]['status']=='ambiguous'
    assert not origin['context']['reference_evidence_ids']


def test_reference_range_does_not_report_complete_when_middle_clause_is_absent():
    from turbine_kg.knowledge.chapter_processing import _expand_references
    rows=[source('a','2.1.1 设备应检查。'),source('c','2.1.3 管路应畅通。',order=1),
          source('origin','2.2.1 应符合第2.1.1条～第2.1.3条的规定。',order=2)]
    evidence=text_evidence(rows,'d')
    _expand_references(evidence,rows,[],[],[1])
    origin=next(row for row in evidence if row['context']['clause_number']=='2.2.1')
    assert origin['context']['reference_resolution'][0]['status']=='missing_range_target'


def test_created_and_reassembled_evidence_use_the_same_source_bound_formula_text():
    raw = {'id':'formula-source','kind':'formula','text':'uncertain OCR',
           'document_id':'doc','revision_id':'rev',
           'locator':{'original_hash':'hash','physical_page':7,'bbox':[1,2,3,4]}}
    corrected = confirm_formula_source(raw, binding=formula_source_binding(raw),
        raw_text=raw['text'], verified_text='z = x(1-r)',
        formula={'expression':'z = x(1-r)','variables':[], 'units':{}, 'conditions':None},
        user_confirmation={'actor':'user','confirmed':True,'note':'The corrected formula is confirmed.'})
    evidence = make_evidence('text', [(corrected,'content')], 'doc')
    evidence.update(document_id='doc', revision_id='rev')

    class Store:
        def get(self, ids, documents=None, budget=None):
            nodes = {corrected['id']:corrected, evidence['id']:evidence,
                     'doc':{'id':'doc','title':'source document'}}
            return [nodes[key] for key in ids if key in nodes]

    view = ContextAssembler(Store()).read(evidence['id'], Snapshot(('doc',),'ontology'))
    assert evidence['text'] == view['text'] == 'z = x(1-r)'
    assert view['members'][0]['quote'] == 'z = x(1-r)'
    assert view['members'][0]['raw_quote'] == 'uncertain OCR'
    assert corrected['text'] == raw['text'] == 'uncertain OCR'
