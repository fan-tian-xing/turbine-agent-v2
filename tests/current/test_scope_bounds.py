from dataclasses import asdict
from types import SimpleNamespace

from turbine_kg.documents.ir import SourceUnit
from turbine_kg.knowledge.scopes import section_bounds, register_scopes, attach_evidence_scopes, native_scope_declarations, restore_scopes


def test_scope_from_replaced_source_is_not_restored():
    source={'id':'s','text':'本章仅适用于冷态设备。','locator':{'physical_page':1}}
    scope={'id':'scope','sources':[source]}
    class Store:
        def query(self,*args):return [{'node':scope}]
        def get(self,*args):return [{**source,'text':'本章仅适用于热态设备。'}]
    assert restore_scopes(Store(),'d')==[]


def test_native_scope_needs_explicit_declaration_and_the_correct_heading():
    def member(key,text,kind='paragraph',role='content'):
        return {'member_key':key,'source_id':key,'quote':text,'source_kind':kind,
                'role':role,'locator':{'physical_page':3},'attributes':{}}
    view={'evidence_id':'e','members':[
        member('chapter','8 设备','heading','section'),
        member('section','8.3 安装','heading','section'),
        member('scope','8.3.1 本节适用于冷态安装。'),
        member('local','安装时应检查部件。')]}
    declarations=native_scope_declarations([view])
    assert len(declarations)==1
    assert declarations[0]['heading_source_id']=='section'
    assert declarations[0]['source_members'][0]['quote']=='8.3.1 本节适用于冷态安装。'
    view['members'][2]['quote']='8.3.1 本章适用于设备施工。'
    assert native_scope_declarations([view])[0]['heading_source_id']=='chapter'
    view['members']=view['members'][1:]
    assert native_scope_declarations([view])==[]


def test_scope_removed_from_range_does_not_remain_in_answer_source_members():
    source={'id':'scope-source','kind':'paragraph','text':'本节仅适用于冷态设备。',
            'locator':{'physical_page':1}}
    body={'id':'body','kind':'paragraph','text':'应检查。','locator':{'physical_page':2}}
    from turbine_kg.evidence.context import make_evidence
    item=make_evidence('text',[(body,'content'),(source,'section_context')],'d')
    item['context']['scope_ids']=['scope']
    scope={'id':'scope','document_id':'d','scope':'document',
           'physical_page_range':[1,1],'sources':[source]}
    class Store:
        def get(self,ids,*args):return [row for row in (source,body) if row['id'] in ids]
    attach_evidence_scopes([item],Store(),[scope],'d')
    assert item['context']['scope_ids']==[]
    assert [m['source_id'] for m in item['members']]==['body']


def heading(key,text,page,level=1,*,y=10,role=None,hierarchy='detected'):
    attributes={'heading_level':level,'hierarchy':hierarchy}
    if role:attributes['source_role']=role
    return SourceUnit(key,'heading',text,{'physical_page':page,'bbox':[10,y,100,y+10]},'native_text',y,attributes=attributes)


def test_printed_numbering_overrides_layout_levels_and_duplicate_anchors():
    chapter=heading('chapter','7 Auxiliary equipment',10)
    duplicate=heading('chapter-layout','7 Auxiliary equipment',10)
    sub=heading('sub','7.1 First subsection',10,1,y=50)
    sub_duplicate=heading('sub-parser','7.1 First subsection',10,2,y=50)
    peer=heading('sub-peer','7.2 Second subsection',14,1,y=40)
    entries=[chapter,duplicate,sub,sub_duplicate,peer]
    assert section_bounds(chapter,entries,20)==[10,20]
    assert section_bounds(sub,entries,20)==[10,14]
    assert section_bounds(sub_duplicate,entries,20)==[10,14]


def test_headers_footers_and_unnumbered_titles_do_not_truncate_scope():
    chapter=heading('chapter','7 Auxiliary equipment',10)
    entries=[chapter,heading('hdr','1 Document identifier',11,role='header'),
             heading('footer','2 Footer',12,role='footer'),heading('number','12',12,role='page_number'),
             heading('layout','Document identification',13),heading('unknown','Appendix information',14,level=None,hierarchy='unknown'),
             heading('running','3 Document name',15,role='running_header'),heading('sub','7.4 Child',16,level=1)]
    assert section_bounds(chapter,entries,20)==[10,20]
    assert section_bounds(entries[5],entries,20) is None


def test_same_page_boundary_uses_position_and_preserves_prior_children():
    before=heading('before','7.6 Previous',14,y=10)
    scope=heading('scope','7.7 Target',14,y=100)
    duplicate=heading('scope-parser','7.7 Target',14,level=2,y=100)
    child=heading('child','7.7.1 Nested',14,level=1,y=150)
    following=heading('following','7.8 Next',15,level=1,y=50)
    assert section_bounds(scope,[before,scope,duplicate,child,following],20)==[14,15]


def test_unknown_or_invalid_anchor_never_inherits_through_arbitrary_level():
    unknown=heading('unknown','Engineering notes',10,level=1)
    assert section_bounds(unknown,[unknown],20) is None
    invalid=heading('outside','7 Auxiliary equipment',21)
    assert section_bounds(invalid,[invalid],20) is None
    flat=heading('flat','7 Auxiliary equipment',10,level=0)
    assert section_bounds(flat,[flat],20) is None
    unstructured=heading('flat2','7 Auxiliary equipment',10,hierarchy='none')
    assert section_bounds(unstructured,[unstructured],20) is None


def test_scope_registration_preserves_declared_anchor_when_parser_has_duplicates():
    declared=heading('original-anchor','7 Auxiliary equipment',10)
    duplicate_one=heading('parser-one',declared.text,10)
    duplicate_two=heading('parser-two',declared.text,10)
    child=heading('child','7.1 Subsection',11,level=1)
    body=SourceUnit('body','paragraph','This chapter applies to cold equipment.',{'physical_page':10},'native_text',20)
    class Store:
        def __init__(self):self.nodes={declared.id:asdict(declared),body.id:asdict(body)};self.scopes=[]
        def get(self,ids,*args):return [self.nodes[key] for key in ids if key in self.nodes]
        def put(self,label,rows):
            self.nodes.update({row['id']:row for row in rows})
            if label=='ScopeDeclaration':self.scopes.extend(rows)
    store=Store();parser=SimpleNamespace(heading_index=[duplicate_one,duplicate_two,child],page_count=20)
    declaration={'scope':'section','heading_source_id':declared.id,'condition_text':body.text,
                 'source_members':[{'source_id':body.id,'locator':body.locator,'quote':body.text}]}
    result=register_scopes([declaration],parser,store,'d')
    assert result[0]['physical_page_range']==[10,20]
    assert result[0]['heading_source_id']==result[0]['declared_heading_source_id']=='original-anchor'
    assert result[0]['sources'][0]['attributes']['physical_page_range']==[10,20]


def test_child_scope_uses_body_pages_instead_of_ancestor_title_or_table_header_pages():
    ancestors=[{'id':'parent','kind':'heading','text':'7 Chapter','locator':{'physical_page':10}},
               {'id':'child','kind':'heading','text':'7.7 Section','locator':{'physical_page':14}},
               {'id':'header','kind':'cell','text':'Header mm','locator':{'physical_page':10}}]
    body={'id':'body','kind':'paragraph','text':'Requirement.','locator':{'physical_page':14}}
    value={'id':'value','kind':'cell','text':'1','locator':{'physical_page':15}}
    note={'id':'note','kind':'table_note','text':'Except note.','locator':{'physical_page':16}}
    declaration={'id':'declaration','kind':'paragraph','text':'This section applies to cold state.','locator':{'physical_page':14},'text_origin':'native_text'}
    indexed={source['id']:source for source in [*ancestors,body,value,note,declaration]}
    store=SimpleNamespace(get=lambda ids,*args:[indexed[key] for key in ids])
    scope={'id':'scope-child','document_id':'d','scope':'section','heading_source_id':'child','physical_page_range':[14,15],'sources':[declaration]}
    def evidence(key,roles):
        return {'id':key,'kind':'table' if 'value' in roles else 'text',
                'members':[{'source_id':source_id,'member_key':f'{key}-{source_id}','role':role,'locator':indexed[source_id]['locator']}
                           for source_id,role in roles.items()]}
    paragraph=evidence('text',{'parent':'section','child':'section','body':'content'})
    table=evidence('table',{'parent':'section','child':'section','header':'inherited_header','value':'value'})
    excluded=evidence('outside-note',{'parent':'section','child':'section','value':'value','note':'note'})
    changed=attach_evidence_scopes([paragraph,table,excluded],store,[scope],'d')
    assert changed=={'text','table'}
    assert paragraph['members'][-1]['source_id']==table['members'][-1]['source_id']=='declaration'
    assert paragraph['context']['scope_ids']==table['context']['scope_ids']==['scope-child']
    assert 'This section applies to cold state.' not in paragraph['text']
    assert paragraph['members'][-1]['role']=='section_context'
    assert excluded['context'].get('scope_ids',[])==[]
    assert all(member['source_id']!='declaration' for member in excluded['members'])
    assert attach_evidence_scopes([paragraph,table,excluded],store,[scope],'d')==set()
    paragraph['context']['scope_ids']=[]
    assert attach_evidence_scopes([paragraph],store,[scope],'d')=={'text'}
    assert paragraph['context']['scope_ids']==['scope-child']
    assert sum(member['source_id']=='declaration' for member in paragraph['members'])==1
    scope['physical_page_range']=[15,15]
    assert attach_evidence_scopes([paragraph],store,[scope],'d')=={'text'}
    assert paragraph['context']['scope_ids']==[]
