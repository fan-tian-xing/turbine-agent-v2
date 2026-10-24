"""Source geometry and scope character binding without model or file output."""
from types import SimpleNamespace

import pytest

from turbine_kg.documents.ir import SourceUnit
from turbine_kg.documents.parser import DocumentParser
from turbine_kg.knowledge.statement_extraction import _bound_scope_text


def item(text, box, label='text'):
    bbox = SimpleNamespace(to_top_left_origin=lambda **kwargs: SimpleNamespace(as_tuple=lambda: box))
    return SimpleNamespace(text=text, label=SimpleNamespace(value=label), prov=[SimpleNamespace(page_no=1, bbox=bbox)])


def ordered(items, heading, level=1):
    parser = DocumentParser.__new__(DocumentParser)
    parser.heading_index = [SourceUnit('h', 'heading', heading.text, {'physical_page': 1, 'bbox': heading.prov[0].bbox.to_top_left_origin().as_tuple()},
                                      'native_text', 0, '', 'detected', {'heading_level': level})]
    parser.mapper = SimpleNamespace(display_box=lambda box, *args, **kwargs: box)
    page = SimpleNamespace(number=0, rect=SimpleNamespace(height=800))
    return [entry.text for entry, _ in parser._layout_reading_order([(entry, 1) for entry in items], 1, page, derived=False)]


def test_late_verified_heading_precedes_body_without_resorting_columns():
    heading = item('2 Terms', (250, 60, 340, 80), 'section_header')
    left = item('Left column', (30, 100, 220, 130))
    right = item('Right column', (350, 90, 550, 140))
    assert ordered([left, right, heading], heading) == ['2 Terms', 'Left column', 'Right column']


def test_verified_subheading_does_not_move_before_another_column_or_body_above():
    heading = item('2.1 Details', (30, 180, 200, 200), 'section_header')
    before = item('Earlier body', (30, 100, 200, 130))
    other = item('Other column', (350, 210, 550, 250))
    after = item('Later body', (30, 220, 200, 250))
    assert ordered([before, other, after, heading], heading, 2) == ['Earlier body', 'Other column', '2.1 Details', 'Later body']


def test_unknown_title_cannot_reorder_body():
    indexed = item('2 Terms', (250, 60, 340, 80), 'section_header')
    body = item('Body', (30, 100, 550, 130))
    unknown = item('Different title', (250, 60, 340, 80), 'section_header')
    assert ordered([body, unknown], indexed) == ['Body', 'Different title']


def test_printed_clause_mislabeled_header_moves_to_its_real_column_position():
    indexed = item('2 Terms', (250, 60, 340, 80), 'section_header')
    clause = item('2.1.4 Check the following:', (30, 180, 300, 200), 'section_header')
    before = item('Earlier body', (30, 100, 300, 130))
    after = item('1 First item', (30, 220, 300, 250))
    assert ordered([clause, before, after], indexed) == ['Earlier body', '2.1.4 Check the following:', '1 First item']


def test_early_later_section_header_is_restored_after_earlier_single_column_body():
    heading=item('2.2 Later section',(200,400,330,420),'section_header')
    before=item('2.1.1 Earlier requirement',(30,100,500,120))
    after=item('2.2.1 Later requirement',(30,450,500,470))
    assert ordered([heading,after,before],heading,2)==['2.1.1 Earlier requirement','2.2 Later section','2.2.1 Later requirement']


def test_single_column_detached_marker_uses_its_actual_line_position():
    heading=item('2 Requirements',(200,20,330,40),'section_header')
    intro=item('2.1.1 Check the following:',(30,60,500,80))
    value=item('Must be clear.',(55,100,220,112))
    marker=item('1',(30,102,36,110))
    assert ordered([heading,intro,value,marker],heading)==['2 Requirements','2.1.1 Check the following:','1','Must be clear.']


def test_section_context_depends_on_original_position_not_heading_iteration_order():
    parser=DocumentParser.__new__(DocumentParser)
    chapter=SourceUnit('root','heading','2 Installation',{'physical_page':1,'bbox':[200,20,330,40]},'native_text',0,attributes={'heading_level':1})
    early=SourceUnit('early','heading','2.1 Earlier',{'physical_page':2,'bbox':[200,20,330,40]},'native_text',1,attributes={'heading_level':2})
    late=SourceUnit('late','heading','2.2 Later',{'physical_page':2,'bbox':[200,400,330,420]},'native_text',2,attributes={'heading_level':2})
    parser.heading_index=[late,chapter,early];parser._page_single_column={2:True}
    assert [heading.id for heading in parser._headings_at(2,{'bbox':[30,100,500,120]})]==['root','early']
    assert [heading.id for heading in parser._headings_at(2,{'bbox':[30,450,500,470]})]==['root','late']


def test_column_local_heading_does_not_leak_into_another_column_context():
    parser=DocumentParser.__new__(DocumentParser)
    root=SourceUnit('root','heading','2 Installation',{'physical_page':1,'bbox':[200,20,330,40]},'native_text',0,attributes={'heading_level':1})
    left=SourceUnit('left','heading','2.1 Left',{'physical_page':1,'bbox':[30,60,200,80]},'native_text',1,attributes={'heading_level':2})
    right=SourceUnit('right','heading','2.2 Right',{'physical_page':1,'bbox':[350,70,550,90]},'native_text',2,attributes={'heading_level':2})
    parser.heading_index=[root,left,right];parser._page_single_column={1:False}
    assert [heading.id for heading in parser._headings_at(1,{'bbox':[30,120,200,140]})]==['root','left']
    assert [heading.id for heading in parser._headings_at(1,{'bbox':[350,120,550,140]})]==['root','right']


def test_centered_table_caption_and_running_header_do_not_create_false_columns():
    heading=item('2.2 Later section',(200,150,330,170),'section_header')
    before=item('Earlier paragraph',(30,100,140,120))
    after=item('2.2.1 Requirement',(30,200,150,220))
    caption=item('Table 2.2.1',(250,250,400,270),'caption')
    running=item('DOCUMENT 123',(450,20,560,40),'page_header')
    parser=DocumentParser.__new__(DocumentParser)
    parser.heading_index=[SourceUnit('h','heading',heading.text,{'physical_page':1,'bbox':(200,150,330,170)},'native_text',0,attributes={'heading_level':2})]
    parser.mapper=SimpleNamespace(display_box=lambda box,*args,**kwargs:box)
    page=SimpleNamespace(number=0,rect=SimpleNamespace(height=800))
    result=parser._layout_reading_order([(entry,1) for entry in [heading,caption,after,before,running]],1,page,derived=False)
    assert [entry.text for entry,_ in result]==['DOCUMENT 123','Earlier paragraph','2.2 Later section','2.2.1 Requirement','Table 2.2.1']
    assert parser._page_single_column[1] is True
    assert [entry.id for entry in parser._headings_at(1,{'bbox':[30,200,150,220]})]==['h']


def member(key, quote):
    return {'member_key': key, 'source_id': key + '-source', 'quote': quote, 'locator': {'physical_page': 1}}


def test_scope_whitespace_match_returns_original_substring_and_member_range():
    source = member('m', '1.0.2 本部分适用于机组的施\n工。 其他文字。')
    raw, ranges = _bound_scope_text('本部分适用于机组的施工。', [source])
    assert raw == '本部分适用于机组的施\n工。'
    assert len(ranges) == 1 and ranges[0]['quote'] == raw
    start, end = ranges[0]['char_range']
    assert source['quote'][start:end] == raw and ranges[0]['source_id'] == 'm-source'


def test_scope_binding_across_members_retains_each_real_quote_range():
    sources = [member('a', '前言。本部分适用于'), member('b', '冷态检查。后文。')]
    raw, ranges = _bound_scope_text('本部分适用于冷态检查。', sources)
    assert raw == '本部分适用于\n冷态检查。'
    assert [row['quote'] for row in ranges] == ['本部分适用于', '冷态检查。']


@pytest.mark.parametrize('condition_text', ['本部分适用于热态检查。', '本部分包括冷态检查。', ''])
def test_scope_binding_does_not_accept_semantic_rewriting(condition_text):
    with pytest.raises(ValueError, match='未绑定连续原文'):
        _bound_scope_text(condition_text, [member('m', '本部分适用于冷态检查。')])


def test_scope_binding_rejects_ambiguous_member_location():
    with pytest.raises(ValueError, match='多个原文位置'):
        _bound_scope_text('冷态检查', [member('m', '冷态检查。冷态检查。')])
