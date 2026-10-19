"""Unchanged edition content retains identities; ambiguous fragments do not."""
from copy import deepcopy

from turbine_kg.evidence.context import text_evidence, table_evidence
from turbine_kg.knowledge.alignment import plan_alignment, remap_ids
from turbine_kg.runtime import identity


def source(text,digest,order=0,*,kind='paragraph',box=None,**attributes):
    locator={'physical_page':1,'bbox':box or [0,order*20,100,order*20+15],'original_hash':digest}
    return {'id':identity(kind,'doc',locator,text),'kind':kind,'text':text,'locator':locator,
            'order':order,'section':'2 Test','text_origin':'native_text','quality':'readable',
            'attributes':attributes,**attributes}


def views(evidence,sources):
    indexed={row['id']:row for row in sources};result=[]
    for item in evidence:
        members=[]
        for row in item['members']:
            raw=indexed[row['source_id']];quote=raw['text'];bounds=row['locator'].get('char_range')
            if bounds:quote=quote[bounds[0]:bounds[1]]
            members.append({**row,'quote':quote,'source_kind':raw['kind']})
        result.append({'evidence_id':item['id'],'kind':item['kind'],'context':deepcopy(item['context']),'members':members})
    return result


def test_new_pdf_hash_keeps_unchanged_source_member_and_evidence_id():
    old=[source('2.1.1 Equipment shall be clean.','first')]
    new=[source('2.1.1 Equipment shall be clean.','second')]
    before=text_evidence(old,'doc');after=text_evidence(new,'doc')
    assert before[0]['id']!=after[0]['id']
    plan=plan_alignment(after,new,views(before,old),old)
    aligned=remap_ids(after,plan['id_map'])
    assert aligned[0]['id']==before[0]['id']
    assert aligned[0]['members'][0]['source_id']==old[0]['id']
    assert aligned[0]['members'][0]['member_key']==before[0]['members'][0]['member_key']
    assert aligned[0]['members'][0]['locator']['original_hash']=='second'


def test_only_changed_clause_is_left_for_extraction():
    old=[source('2.1.1 Equipment shall be clean.','first'),source('2.1.2 Pipe shall be dry.','first',1)]
    new=[source('2.1.1 Equipment shall be inspected.','second'),source('2.1.2 Pipe shall be dry.','second',1)]
    before=text_evidence(old,'doc');after=text_evidence(new,'doc')
    plan=plan_alignment(after,new,views(before,old),old)
    assert plan['unchanged_evidence_ids']=={before[1]['id']}
    assert after[0]['id'] not in plan['id_map']


def test_repeated_clause_and_text_without_unique_position_are_not_bound():
    old=[source('2.1.1 Equipment shall be clean.','first',0),source('2.1.1 Equipment shall be clean.','first',1)]
    new=[source('2.1.1 Equipment shall be clean.','second',3)]
    plan=plan_alignment(text_evidence(new,'doc'),new,views(text_evidence(old,'doc'),old),old)
    assert not plan['unchanged_evidence_ids']


def test_changed_fragmentation_does_not_rebind_member_supports_by_concatenation():
    old=[source('2.1.1 Equipment shall be clean and dry.','first')]
    first=source('2.1.1 Equipment shall be clean','second')
    new=[first,source(' and dry.','second',1,structural_parent_source_ids=[first['id']])]
    plan=plan_alignment(text_evidence(new,'doc'),new,views(text_evidence(old,'doc'),old),old)
    assert not plan['unchanged_evidence_ids']


def test_table_row_groups_keep_id_when_hash_changes_and_only_changed_row_is_reprocessed():
    def table(digest,last='5'):
        cells=[source('Allowed mm',digest,0,kind='cell',row_index=0,column_index=0,row_span=1,column_span=1,column_header=True),
               source('4',digest,1,kind='cell',row_index=1,column_index=0,row_span=1,column_span=1),
               source(last,digest,2,kind='cell',row_index=2,column_index=0,row_span=1,column_span=1)]
        data={'id':'table-'+digest,'title':'表2.1.1 Limits','section':'2 Test','rows':3,'columns':1,'locator':{'physical_page':1}}
        return table_evidence(data,cells,'doc'),cells
    before,old=table('first');after,new=table('second')
    plan=plan_alignment(after,new,views(before,old),old)
    assert plan['unchanged_evidence_ids']=={row['id'] for row in before}
    assert {row['context']['table_id'] for row in remap_ids(after,plan['id_map'])}=={'table-first'}
    changed,new=table('second','6')
    partial=plan_alignment(changed,new,views(before,old),old)
    assert partial['unchanged_evidence_ids']=={before[0]['id']}
