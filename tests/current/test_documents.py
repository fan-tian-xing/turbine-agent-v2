"""Document geometry and preservation boundaries, without model downloads."""
import hashlib
from pathlib import Path
import pytest
import pymupdf as fitz

from turbine_kg.documents.ir import ParsedPage, SourceUnit, TableData
from turbine_kg.documents.mapping import PageMapper
from turbine_kg.documents.parser import DocumentParser


def test_layout_failure_marks_unverified_text_and_excludes_repeated_margin_sources():
    from turbine_kg.evidence.context import text_evidence
    from dataclasses import asdict
    original = fitz.open()
    for number in (1, 2):
        page = original.new_page(width=600, height=800)
        page.insert_text((50, 30), 'GENERIC INSTALLATION MANUAL')
        page.insert_text((50, 150), f'2.1.{number} Inspect the assembly before installation.')
        page.insert_text((50, 780), str(number))
    parser = DocumentParser.__new__(DocumentParser)
    parser.original = original; parser.derived = None; parser.mapper = PageMapper(original, None)
    parser.path = Path('memory.pdf'); parser.document_id = 'g'; parser.original_hash = 'hash'
    parser.rules = {}; parser.heading_index = []; parser.scope_sources = []
    def fail(*args): raise RuntimeError('layout unavailable')
    parser._convert = fail
    try:
        page = parser.page(1)
        assert page.mapping['layout_status'] == 'fallback_unverified'
        assert all(row.quality == 'pending_layout_review' for row in page.sources)
        evidence = text_evidence([asdict(row) for row in page.sources], 'g')
        assert len(evidence) == 1
        assert 'GENERIC INSTALLATION MANUAL' not in evidence[0]['text']
        assert evidence[0]['context']['clause_number'] == '2.1.1'
    finally:
        original.close()


def test_detached_markers_do_not_turn_single_column_into_multiple_columns():
    from types import SimpleNamespace
    def item(text, box):
        bounds=SimpleNamespace(as_tuple=lambda: box)
        provenance=SimpleNamespace(page_no=1, bbox=SimpleNamespace(to_top_left_origin=lambda **_: bounds))
        return SimpleNamespace(text=text, label=SimpleNamespace(value='list_item'), prov=[provenance])
    previous=item('11 前项要求。\n下一条正文。', (78, 61, 375, 86))
    marker=item('7.2.4', (57, 78, 77, 86))
    child=item('1 应清洁。', (78, 92, 280, 102))
    parser=DocumentParser.__new__(DocumentParser)
    parser.heading_index=[]
    parser.mapper=SimpleNamespace(display_box=lambda box, *_args, **_kwargs: box)
    page=SimpleNamespace(number=0, rect=SimpleNamespace(height=800))
    ordered=parser._layout_reading_order([(marker, 0), (previous, 0), (child, 0)], 1, page, derived=False)
    assert [entry.text for entry, _ in ordered] == [previous.text, marker.text, child.text]
    assert parser._page_single_column[1]


def test_centered_section_heading_applies_to_left_aligned_list_fragments():
    from types import SimpleNamespace
    parser=DocumentParser.__new__(DocumentParser)
    parser.original=[SimpleNamespace(rect=SimpleNamespace(width=600))]
    parser._page_single_column={1: False}
    heading=SourceUnit('h', 'heading', '7.2 Equipment',
                       {'physical_page': 1, 'bbox': [250, 10, 350, 20]},
                       'native_text', 0, '', 'detected', {'heading_level': 2})
    parser.heading_index=[heading]
    assert parser._headings_at(1, {'bbox': [50, 30, 100, 40]}) == [heading]


def navigation_parser(texts, *, derived_texts=None, mappings=None):
    """Local source text fixtures; no layout inference or generated files."""
    from types import SimpleNamespace
    def page(text):
        return SimpleNamespace(get_text=lambda: text, rect=SimpleNamespace(width=200, height=300))
    parser=DocumentParser.__new__(DocumentParser)
    parser.original=[page(text) for text in texts]
    parser.derived=[page(text) for text in derived_texts] if derived_texts else None
    parser.mapper=SimpleNamespace(mapping=mappings or {})
    parser._convert=lambda *args: pytest.fail('Navigation must exit before Docling')
    parser._picture=lambda *args: pytest.fail('Navigation must exit before image writes')
    return parser


@pytest.mark.parametrize('heading',['目  次','目录','Table of Contents','Contents'])
def test_navigation_requires_heading_and_real_page_references(heading):
    parser=navigation_parser([heading+'\n1 Introduction .... 1\n2 Installation .... 8\n3 Checks .... 12'])
    assert parser.is_navigation_page(1)
    parsed=parser.page(1)
    assert parsed.status=='excluded_navigation'
    assert not parsed.sources and not parsed.tables and not parsed.pictures


def test_navigation_continuation_requires_verified_previous_navigation():
    entries='\n'.join(f'{index} Chapter {index} .... {index*3}' for index in range(1,8))
    parser=navigation_parser(['Running header\nContents\n'+entries+'\nIV',
                              'Running header\nWrapped title\n'+entries+'\nV'])
    assert parser.is_navigation_page(2)
    assert not navigation_parser([entries]).is_navigation_page(1)
    assert not navigation_parser(['Ordinary paragraph',entries]).is_navigation_page(2)


@pytest.mark.parametrize('content',[
    '目录',
    '需要查阅目录。\n1 安装\n2 检查\n3 记录',
    'Contents\n1 Clearance 3 mm\n2 Pressure 8 MPa\n3 Temperature 12 ℃',
    '1 Installation\n1.1 Inspect\n1.2 Record\n1.3 Complete',
    'Terms and definitions\n1 Load .... 1\n2 Pressure .... 8\n3 Clearance .... 12',
    '目录\n1 安装 .... 1\n2 检查 .... 8\n3 记录 .... 12\n间隙应符合制造厂要求。',
])
def test_body_lists_tables_definitions_and_mixed_pages_are_not_navigation(content):
    assert not navigation_parser([content]).is_navigation_page(1)


def test_navigation_accepts_numbered_page_columns_only_with_heading():
    text='Contents\n1 Introduction 1\n2 Installation 8\n3 Checks 12'
    assert navigation_parser([text]).is_navigation_page(1)
    assert not navigation_parser([text.replace('Contents\n','')]).is_navigation_page(1)


def test_navigation_ocr_requires_verified_original_mapping():
    text='Contents\n1 Introduction .... 1\n2 Installation .... 8\n3 Checks .... 12'
    assert not navigation_parser([''],derived_texts=[text]).is_navigation_page(1)
    parser=navigation_parser([''],derived_texts=[text],mappings={0:{'status':'verified_pixel_match','derived_page':1}})
    assert parser.is_navigation_page(1)
    assert parser.page(1).status=='excluded_navigation'


def test_navigation_markdown_uses_same_whole_document_guard():
    from types import SimpleNamespace
    parser=DocumentParser.__new__(DocumentParser)
    parser.original=None
    parser.path=SimpleNamespace(read_text=lambda **kwargs: '# Contents\n1 Introduction .... 1\n2 Installation .... 8\n3 Checks .... 12')
    assert parser.is_navigation_page(1)
    assert parser.page(1).status=='excluded_navigation'


def test_ocr_page_mapping_uses_pixels_after_reordering():
    original=fitz.open()
    for rectangle in ((10,10,90,60),(100,120,190,180)):
        page=original.new_page(width=200,height=200)
        page.draw_rect(rectangle,fill=(0,0,0))
    derivative=fitz.open();derivative.insert_pdf(original,from_page=1,to_page=1);derivative.insert_pdf(original,from_page=0,to_page=0)
    mapper=PageMapper(original,derivative)
    assert mapper.mapping[0]['derived_page']==2
    assert mapper.mapping[1]['derived_page']==1
    original.close();derivative.close()


def test_pdfium_display_coordinates_are_not_rotated_again():
    original=fitz.open();page=original.new_page(width=200,height=100);page.set_rotation(90)
    mapper=PageMapper(original,None)
    assert mapper.display_box([70,10,80,40],0,already_display=True)==[70,10,80,40]
    assert mapper.display_box([10,20,40,30],0)==[70,10,80,40]
    original.close()


def test_duplicate_pixels_do_not_prove_a_unique_ocr_page():
    original=fitz.open()
    for _ in range(2):
        page=original.new_page(width=200,height=200);page.draw_rect((10,10,80,100),fill=(0,0,0))
    derivative=fitz.open();derivative.insert_pdf(original,from_page=0,to_page=0)
    mapper=PageMapper(original,derivative)
    assert not mapper.mapping
    assert mapper.unmapped_derived_pages==[1]
    original.close();derivative.close()


def test_unverified_review_split_keeps_the_entire_source():
    original=fitz.open();page=original.new_page(width=200,height=200);page.insert_text((20,40),'Original retained text')
    text=page.get_text().strip()
    parser=DocumentParser.__new__(DocumentParser)
    parser.original=original;parser.derived=None;parser.document_id='g';parser.section='';parser.original_hash='h'
    parser.rules={'reviewed_text_splits':[{'physical_page':1,'source_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'segments':[{'bbox_pdf_pt':[0,0,200,200],'text_sha256':'changed','extractability':'source_text'}]}]}
    source=SourceUnit('p','paragraph',text,{'physical_page':1,'bbox':[0,0,200,200]},'native_text',0)
    parsed=ParsedPage(1,None,200,200,{},sources=[source])
    parser._annotations(parsed)
    assert len(parsed.sources)==1 and parsed.sources[0].text==text
    assert parsed.sources[0].attributes['semantic_status']=='pending_review'
    original.close()


def test_missing_cell_with_table_bbox_cannot_bind_unrelated_cross_page_rows():
    parser=DocumentParser.__new__(DocumentParser);parser.document_id='g';parser.section='';parser.original_hash='h'
    parser.rules={'cross_page_units':[{'group':'g12','kind':'ordered_table_procedure','complete_pages':[1,2],'fragments':[{'physical_page':1,'source_boxes':[{'bbox_pdf_pt':[45,45,55,55]}]}]}]}
    cells=[SourceUnit('missing','cell','',{'physical_page':1,'bbox':[0,0,100,100]},'native_text',0,attributes={'cell_state':'missing'}),SourceUnit('other','cell','Other row',{'physical_page':1,'bbox':[10,10,30,20]},'native_text',1,attributes={'cell_state':'readable'}),SourceUnit('continued','cell','Continued row',{'physical_page':1,'bbox':[40,40,60,60]},'native_text',2,attributes={'cell_state':'readable'})]
    table=TableData('t','','',{'physical_page':1,'bbox':[0,0,100,100]},3,1,cells)
    parsed=ParsedPage(1,None,100,100,{},tables=[table])
    parser._annotations(parsed)
    assert [cell.id for cell in cells if cell.attributes.get('cross_page_group')]==['continued']


@pytest.mark.parametrize('kind',['ocr_box_edits','ocr_box_exclusions','ocr_margin_exclusions'])
def test_point_annotations_are_never_scaled_as_ocr_pixels(kind):
    parser=DocumentParser.__new__(DocumentParser);parser.document_id='g';parser.section='';parser.original_hash='h'
    rule={'physical_page':1,'coordinate_system':'original_display_top_left_pdf_points','x0':12,'y0':14,'x0_min':9,'y0_max':30,'expected_text':'old','replacement_text':'correct','reason':'reviewed'}
    parser.rules={kind:[rule]}
    source=SourceUnit('s','paragraph','old',{'physical_page':1,'bbox':[10,10,20,20]},'ocr_text',0)
    page=ParsedPage(1,None,100,100,{},sources=[source]);parser._annotations(page)
    if kind=='ocr_box_edits':assert source.text=='correct'
    elif kind=='ocr_box_exclusions':assert source.quality=='unrecognized'
    else:assert page.sources==[]


def test_missing_pixel_dpi_does_not_guess_a_scale():
    page=ParsedPage(1,None,100,100,{})
    assert DocumentParser._annotation_scale({'coordinate_system':'original_display_ocr_pixels'},page) is None
    assert page.warnings


def test_reviewed_row_boundaries_do_not_change_another_table_on_same_page():
    parser=DocumentParser.__new__(DocumentParser);parser.document_id='g';parser.section='';parser.original_hash='h'
    digest=hashlib.sha256(b'verified').hexdigest()
    parser.rules={'reviewed_table_text_regions':[{'physical_page':1,'coordinate_system':'original_display_top_left_pdf_points','table_bbox_pdf_pt':[0,0,80,100],'regions':[{'rectangles':[{'x0':10,'y0':y,'x1':70,'y1':y+20}],'bound_source_text_sha256':digest} for y in (30,60)]}]}
    parser._original_region_text=lambda number,rectangle:('verified','native_text')
    tables=[]
    for tid,x in [('reviewed',0),('other',110)]:
        cell=SourceUnit(tid+'-cell','cell','long content',{'physical_page':1,'bbox':[x+10,10,x+70,90]},'native_text',0,attributes={'row_index':0,'column_index':0,'row_span':1,'column_span':1,'column_header':False,'cell_state':'readable'})
        tables.append(TableData(tid,'','',{'physical_page':1,'bbox':[x,0,x+80,100]},1,1,[cell]))
    parsed=ParsedPage(1,None,200,100,{},tables=tables);parser._reviewed_row_boundaries(parsed)
    assert tables[0].rows==3
    assert tables[1].rows==1 and tables[1].cells[0].text=='long content'


def test_empty_white_source_page_records_verified_blank_status():
    original=fitz.open();original.new_page(width=200,height=100)
    parser=DocumentParser.__new__(DocumentParser);parser.original=original;parser.derived=None;parser.path=Path('in-memory.pdf');parser.document_id='g';parser.original_hash='h';parser.mapper=PageMapper(original,None);parser.heading_index=[]
    page=parser.page(1)
    assert page.status=='blank' and page.mapping['content_status']=='verified_blank'
    assert not page.sources and not page.tables and not page.pictures
    original.close()


def test_markdown_preserves_structure_and_distinguishes_blank_missing_cells():
    from turbine_kg.documents.text import parse_text
    content='# Assembly\n\nIntro $d<20$ applies here.\n\n## Checks\n\n- Inspect\n  - Record\n\n| Name | Value | Unit |\n| --- | --- | --- |\n| A\\|B | 20[^n] | mm |\n| C | |\n\n[^n]: At room temperature.\n\n$$\nf=S/500\n$$\n'
    page=parse_text(content,Path('manual.md'),'g','h')
    assert [unit.attributes['heading_level'] for unit in page.sources if unit.kind=='heading']==[1,2]
    assert [unit.attributes['list_level'] for unit in page.sources if unit.kind=='list_item']==[1,2]
    assert any(unit.attributes.get('inline_formulas')==['$d<20$'] for unit in page.sources)
    assert any(unit.kind=='formula' and unit.text=='$$\nf=S/500\n$$' for unit in page.sources)
    table=page.tables[0]
    assert (table.rows,table.columns)==(3,3)
    assert table.cells[3].text==r'A\|B' and table.cells[3].attributes['display_text']=='A|B'
    assert table.cells[7].attributes['cell_state']=='blank'
    assert table.cells[8].attributes['cell_state']=='missing'
    assert table.notes[0].attributes['coverage']['rows']==[1]
    assert table.notes[0].locator['line_range']==[15,15]
    assert table.cells[3].attributes['section_sources'][-1]['text']=='## Checks'


def test_text_repeated_paragraph_locations_are_distinct():
    from turbine_kg.documents.text import parse_text
    page=parse_text('Same\n\nSame\n',Path('source.txt'),'g','h')
    assert [unit.locator['line_range'] for unit in page.sources]==[[1,1],[3,3]]
    assert page.sources[0].id!=page.sources[1].id


def test_unresolved_markdown_images_keep_the_reference_without_network_reads():
    from turbine_kg.documents.text import parse_text
    page=parse_text('![Diagram](https://example.invalid/diagram.png)\n\n![Missing][figure]\n',Path('manual.md'),'g','h')
    assert not page.pictures
    assert len(page.warnings)==2
    assert all(unit.attributes['image_references'][0]['status']=='unresolved' for unit in page.sources)


def test_markdown_reference_image_links_both_text_and_definition(monkeypatch):
    from turbine_kg.documents import text as module
    class Asset:
        size=(32,24)
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def verify(self):pass
    monkeypatch.setattr(module.Image,'open',lambda path:Asset())
    monkeypatch.setattr(module,'file_hash',lambda path:'pixels-hash')
    page=module.parse_text('![Diagram][figure]\n\n[figure]: <images/diagram one.png>\n',Path('manual.md'),'g','h')
    picture=page.pictures[0]
    assert (picture.width,picture.height)==(32,24)
    assert picture.title=='Diagram'
    assert {link['relation'] for link in picture.links}=={'explicit_image_reference','explicit_image_reference_definition'}
    assert picture.locator['line_range']==[1,1]


def test_registered_scope_applies_only_to_its_explicit_section():
    parser=DocumentParser.__new__(DocumentParser);parser.scope_sources=[]
    condition=SourceUnit('condition','paragraph','Only for equipment X.',{'physical_page':2,'bbox':[0,0,20,20]},'native_text',0)
    parser.register_scope_sources([condition],applies_to='section',section_source_ids=['h1'])
    first=SourceUnit('h1','heading','Chapter one',{'physical_page':1},'native_text',0)
    second=SourceUnit('h2','heading','Chapter two',{'physical_page':3},'native_text',0)
    assert parser._applicable_scope_sources(2,[first])[0]['id']=='condition'
    assert not parser._applicable_scope_sources(3,[second])
    with pytest.raises(ValueError):parser.register_scope_sources([condition],applies_to='section')


def test_markdown_table_picture_keeps_its_cell_source_and_literal_heading(monkeypatch):
    from turbine_kg.documents import text as module
    class Asset:
        size=(32,24)
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def verify(self):pass
    monkeypatch.setattr(module.Image,'open',lambda path:Asset())
    monkeypatch.setattr(module,'file_hash',lambda path:'pixels-hash')
    page=module.parse_text('# C#\n\n| Figure |\n| --- |\n| ![Diagram](diagram.png) |\n',Path('manual.md'),'g','h')
    assert page.sources[0].attributes['display_text']=='C#'
    assert page.pictures[0].links[0]['source_id']==page.tables[0].cells[1].id
    assert page.tables[0].cells[1].attributes['image_references'][0]['status']=='local_pixels_available'


def test_flat_layout_lists_bind_actual_intro_and_stop_at_sibling_or_group_boundary():
    from turbine_kg.documents.ir import bind_list_parents
    texts=['7.2.1 Conditional introduction:','1 A value is 20 mm.','2 Another item.','7.2.2 Independent value is 30 mm.','1 New independent list.','1 Different group.']
    units=[SourceUnit(str(index),'list_item',text,{'physical_page':1,'bbox':[0,index*10,100,index*10+8]},'native_text',index,attributes={'layout_ref':f'#/texts/{index}','layout_parent_ref':'#/groups/0' if index<5 else '#/groups/1','section_sources':[]}) for index,text in enumerate(texts)]
    bind_list_parents(units)
    for unit in units[1:3]:
        assert unit.attributes['structural_parent_source_ids']==['0']
        assert unit.attributes['section_sources'][0]['text']==texts[0]
        assert unit.attributes['semantic_inheritance']=='not_inferred'
        assert unit.text==texts[unit.order]
    assert not units[3].attributes.get('structural_parent_source_ids')
    assert not units[4].attributes.get('structural_parent_source_ids')
    assert not units[5].attributes.get('structural_parent_source_ids')


def test_list_explicit_ancestors_and_number_prefix_do_not_bind_same_level_siblings():
    from turbine_kg.documents.ir import bind_list_parents
    units=[SourceUnit(str(index),'list_item',text,{'physical_page':1},'native_text',index,attributes={'layout_ref':f'#/texts/{index}','layout_parent_ref':'#/groups/0','section_sources':[]}) for index,text in enumerate(['8.1 Introductory clause.','8.1.1 Child.','8.1.2 Sibling.'])]
    units[1].attributes['layout_ancestor_refs']=['#/texts/0','#/body']
    bind_list_parents(units)
    assert units[1].attributes['structural_parent_source_ids']==['0']
    assert units[2].attributes['structural_parent_source_ids']==['0']
    assert '1' not in units[2].attributes['structural_parent_source_ids']


def test_flat_nested_enumerations_restore_parent_when_returning_to_sibling():
    from turbine_kg.documents.ir import bind_list_parents
    texts=['7.2.1 检验应符合下列规定：','1 铜管的检验：',
           '1）检查外观；','2）按规定比例抽样试验：','（1）压扁试验；',
           '（2）扩口试验。','2 钢管的检验：','1）检查表面；','2）检查尺寸。']
    units=[SourceUnit(str(index),'list_item',text,{'physical_page':1},'native_text',index,
        attributes={'layout_ref':f'#/texts/{index}','layout_parent_ref':'#/groups/0'})
        for index,text in enumerate(texts)]
    bind_list_parents(units)
    expected={1:'0',2:'1',3:'1',4:'3',5:'3',6:'0',7:'6',8:'6'}
    for index,parent in expected.items():
        assert units[index].attributes['structural_parent_source_ids']==[parent]
    assert all(row['id']!='3' for row in units[6].attributes['section_sources'])


def explicit_table_page(body_text='无制造厂要求时，应符合表7.2。'):
    heading={'id':'section-7','kind':'heading','text':'7 Installation','attributes':{'heading_level':1,'hierarchy':'explicit_numbering'}}
    locator={'physical_page':1,'bbox':[0,0,100,20],'original_hash':'original-hash'}
    attrs={'section_sources':[heading]}
    caption=SourceUnit('caption','heading','表7.2 安装限值',dict(locator),'native_text',0,'7 Installation',attributes=dict(attrs))
    body=SourceUnit('body','paragraph',body_text,dict(locator),'native_text',1,'7 Installation',attributes=dict(attrs))
    cell=SourceUnit('cell','cell','20 mm',dict(locator),'native_text',2,'7 Installation',attributes=dict(attrs))
    table=TableData('table',caption.text,'7 Installation',dict(locator),1,1,[cell])
    return ParsedPage(1,None,100,100,{},sources=[caption,body],tables=[table])


def test_explicit_table_reference_keeps_real_title_and_body_source():
    page=explicit_table_page()
    DocumentParser._attach_explicit_table_references(page)
    assert page.tables[0].locator['title_source_ids']==['caption']
    assert page.tables[0].locator['related_body_source_ids']==['body']
    assert page.tables[0].locator['related_body_association']=='explicit_table_reference_same_section'
    assert page.sources[1].text=='无制造厂要求时，应符合表7.2。'


@pytest.mark.parametrize('text',['无制造厂要求时采用本页限值。','应符合表7.20。','应符合表7.2.1。'])
def test_table_body_context_requires_complete_exact_reference(text):
    page=explicit_table_page(text)
    DocumentParser._attach_explicit_table_references(page)
    assert page.tables[0].locator['title_source_ids']==['caption']
    assert not page.tables[0].locator['related_body_source_ids']


@pytest.mark.parametrize('failure',['unknown_heading','other_section','pending_review','ambiguous_target','unverified_mapping','incomplete_fragment','derived_description'])
def test_explicit_table_reference_needs_unique_target_and_verified_section_source(failure):
    from copy import deepcopy
    page=explicit_table_page();body=page.sources[1]
    body.attributes=deepcopy(body.attributes)
    if failure=='unknown_heading':body.attributes['section_sources'][0]['attributes']['hierarchy']='unknown'
    elif failure=='other_section':body.attributes['section_sources'][0]['id']='another-section'
    elif failure=='pending_review':body.attributes['semantic_status']='pending_review'
    elif failure=='unverified_mapping':body.locator['mapping_status']='unknown'
    elif failure=='incomplete_fragment':body.attributes['semantic_status']='context_only'
    elif failure=='derived_description':body.text_origin='model_description'
    else:
        second=deepcopy(page.tables[0]);second.id='ambiguous-table';page.tables.append(second)
    DocumentParser._attach_explicit_table_references(page)
    assert all(not table.locator['related_body_source_ids'] for table in page.tables)


def test_reviewed_cross_page_table_reference_retains_its_group_for_complete_assembly():
    page=explicit_table_page();body=page.sources[1]
    body.quality='verified';body.attributes.update(semantic_status='context_only',cross_page_group='reviewed-group',cross_page_complete_pages=[1,2])
    DocumentParser._attach_explicit_table_references(page)
    assert page.tables[0].locator['related_body_source_ids']==['body']
    assert body.attributes['cross_page_group']=='reviewed-group'
    assert body.attributes['cross_page_complete_pages']==[1,2]


def test_cross_page_prose_keeps_source_orders_and_expected_item_count():
    parser=DocumentParser.__new__(DocumentParser);parser.document_id='g';parser.section='7 Installation';parser.original_hash='h'
    boxes=[{'text':text,'bbox_pdf_pt':[0,index*20,100,index*20+10],'group_order':index+1,'source_text_sha256':hashlib.sha256(text.encode()).hexdigest()} for index,text in enumerate(['The preceding conditions apply.','Refer to 表7.2.'])]
    parser.rules={'cross_page_units':[{'group':'reviewed-unit','kind':'clause','complete_pages':[1,2],'source_total_items':2,'fragments':[{'physical_page':2,'text_origin':'native_text','source_boxes':boxes}]}]}
    page=ParsedPage(2,None,100,100,{})
    parser._annotations(page)
    assert [unit.attributes['group_order'] for unit in page.sources]==[1,2]
    assert all(unit.attributes['source_total_items']==2 for unit in page.sources)
    assert all(unit.attributes['cross_page_complete_pages']==[1,2] for unit in page.sources)


@pytest.mark.parametrize('ambiguous',[False,True])
def test_reviewed_fragment_reuses_only_matching_actual_source_section(ambiguous):
    page=explicit_table_page();parser=DocumentParser.__new__(DocumentParser)
    parser.document_id='g';parser.section='8 Later section on the page';parser.original_hash='original-hash'
    text=page.sources[1].text
    parser.rules={'cross_page_units':[{'group':'reviewed-unit','kind':'clause','complete_pages':[1,2],'source_total_items':1,'fragments':[{'physical_page':1,'text_origin':'native_text','source_boxes':[{'text':text,'bbox_pdf_pt':[0,0,100,20],'group_order':1,'source_text_sha256':hashlib.sha256(text.encode()).hexdigest()}]}]}]}
    page.sources[1].attributes['semantic_status']='context_only'
    if ambiguous:
        from copy import deepcopy
        other=deepcopy(page.sources[1]);other.id='another-source';other.section='8 Another section';other.attributes['section_sources'][0]['id']='section-8';page.sources.append(other)
    parser._annotations(page)
    reviewed=page.sources[-1]
    if ambiguous:
        assert not reviewed.attributes.get('section_sources')
        assert reviewed.section==''
    else:
        assert reviewed.attributes['section_sources']==page.sources[1].attributes['section_sources']
        assert reviewed.attributes['structural_context_basis']=='actual_source_text_match'
        assert reviewed.section=='7 Installation'
    DocumentParser._attach_explicit_table_references(page)
    assert page.tables[0].locator['related_body_source_ids']==([] if ambiguous else [reviewed.id])
