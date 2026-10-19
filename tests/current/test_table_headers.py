"""Header recovery relies on source units and column geometry, never book labels."""
from turbine_kg.documents.ir import SourceUnit, recognize_table_headers
from turbine_kg.evidence.context import table_evidence


def cell(row, column, text, **attributes):
    return {'id':f'c{row}-{column}','kind':'cell','text':text,'text_origin':'native_text',
            'locator':{'physical_page':1},'row_index':row,'column_index':column,'row_span':1,'column_span':1,
            'column_header':False,'row_header':False,'cell_state':'readable','attributes':{},**attributes}


def test_unit_label_numeric_column_recovers_headers_for_every_data_row():
    cells=[cell(0,0,'Item'),cell(0,1,'Tolerance (mm)'),cell(1,0,'Type A'),cell(1,1,'±3'),
           cell(2,0,'Type B'),cell(2,1,'5'),cell(3,0,'Type C'),cell(3,1,'10'),cell(4,0,'Type D'),cell(4,1,'3')]
    metadata=recognize_table_headers(cells,5,2)
    assert metadata['status']=='inferred' and metadata['unit_columns']==[{'column':1,'unit':'mm'}]
    assert all(row['column_header'] for row in cells[:2])
    assert all(row['row_header'] for row in cells if row['column_index']==0 and row['row_index']>0)
    evidence=table_evidence({'id':'table','title':'表7.2.4','rows':5,'columns':2},cells,'g')
    assert len(evidence)==4
    assert all(len([member for member in row['members'] if member['role']=='header'])==2 for row in evidence)
    assert all(row['context']['header_recognition']['basis']==['explicit_unit_label_with_numeric_column'] for row in evidence)


def test_existing_model_headers_are_preserved_without_numeric_requirement():
    cells=[cell(0,0,'Category',column_header=True,attributes={'header_basis':'docling_geometry_match'}),
           cell(1,0,'Text value'),cell(2,0,'Other text')]
    metadata=recognize_table_headers(cells,3,1)
    assert metadata=={'status':'known','basis':['docling_geometry_match'],'rows':[0]}
    assert cells[1]['column_header'] is False


def test_first_data_row_and_ambiguous_text_column_are_not_guessed_headers():
    data=[cell(0,0,'A'),cell(0,1,'3'),cell(1,0,'B'),cell(1,1,'4'),cell(2,0,'C'),cell(2,1,'5')]
    assert recognize_table_headers(data,3,2)['status']=='unknown'
    ambiguous=[cell(0,0,'A'),cell(0,1,'Value (mm)'),cell(1,0,'B'),cell(1,1,'Refer elsewhere'),
               cell(2,0,'C'),cell(2,1,'5')]
    assert recognize_table_headers(ambiguous,3,2)['status']=='unknown'
    assert not any(row['column_header'] for row in data+ambiguous)


def test_dataclass_cell_metadata_is_updated_without_changing_original_text_or_location():
    cells=[SourceUnit(str(row),'cell',value,{'physical_page':1,'bbox':[0,row,2,row+1]},'native_text',row,
                      attributes={'row_index':row,'column_index':0,'row_span':1,'column_span':1,'column_header':False})
           for row,value in enumerate(['Limit (MPa)','0.5','0.6'])]
    locations=[dict(cell.locator) for cell in cells]
    metadata=recognize_table_headers(cells,3,1)
    assert metadata['status']=='inferred'
    assert cells[0].attributes['column_header'] is True
    assert [cell.text for cell in cells]==['Limit (MPa)','0.5','0.6']
    assert [cell.locator for cell in cells]==locations
