from copy import deepcopy
from dataclasses import asdict

from turbine_kg.documents.ir import ParsedPage, SourceUnit, TableData
from turbine_kg.evidence.context import ContextAssembler, table_evidence
from turbine_kg.knowledge.store import KnowledgeStore
from turbine_kg.runtime import Snapshot


class MemoryStore(KnowledgeStore):
    def __init__(self): self.nodes={};self.labels={}
    def put(self,label,rows):
        for row in rows:
            self.nodes[row['id']]=deepcopy(row);self.labels[row['id']]=label
    def edges(self,*args): pass
    def query(self,text,parameters=None,budget=None):
        parameters=parameters or {}
        if 'RETURN properties(t) AS node' in text:
            return [{'node':row} for key,row in self.nodes.items() if self.labels[key]=='Table' and (parameters['unrestricted'] or row['document_id'] in parameters['documents'])]
        if 'RETURN properties(n) AS node' in text:
            return [{'node':self.nodes[key]} for key in parameters['ids'] if key in self.nodes and (parameters['unrestricted'] or self.nodes[key].get('document_id') in parameters['documents'])]
        return []


def test_embedded_table_is_readable_after_reload_without_cell_nodes_or_files():
    store=MemoryStore();cells=[]
    for index,(text,attributes) in enumerate([
        ('允许偏差/mm',{'row_index':0,'column_index':0,'column_header':True,'row_span':1,'column_span':2}),
        ('平面位置',{'row_index':1,'column_index':0,'row_header':True,'row_span':1,'column_span':1}),
        ('±10',{'row_index':1,'column_index':1,'row_span':1,'column_span':1})]):
        cells.append(SourceUnit('cell'+str(index),'cell',text,{'physical_page':3,'bbox':[0,index,10,index+1]},'native_text',index,attributes=attributes))
    note=SourceUnit('note','table_note','仅用于冷态。',{'physical_page':3},'native_text',3,attributes={'coverage':{'status':'known','rows':[1]}})
    table=TableData('table','表7.2 基础偏差','7.2',{'physical_page':3},2,2,cells,[note])
    store.save_page(ParsedPage(3,'2',100,100,{},tables=[table]),'doc','revision',compact_tables=True)
    store.put('Document',[{'id':'doc','title':'来源'}])
    assert set(store.labels.values())=={'Page','Table','Document'}
    saved=store.get(['table'],('doc',))[0]
    evidence=table_evidence(saved,store.table_sources('table',('doc',)),'doc')[0]
    store.put('Evidence',[{**evidence,'document_id':'doc'}])
    view=ContextAssembler(store).read(evidence['id'],Snapshot(('doc',),'ontology'))
    assert {member['quote'] for member in view['members']}=={'允许偏差/mm','平面位置','±10','仅用于冷态。'}
    header=next(member for member in view['members'] if member['role']=='header')
    assert header['attributes']['column_span']==2
    assert view['context']['table_number']=='7.2'
    assert '表7.2 基础偏差' in view['text']
    assert '允许偏差/mm' in view['text'] and '±10' in view['text']
    assert '仅用于冷态。' in view['text']
    assert store.get(['cell2'],('other',))==[]
