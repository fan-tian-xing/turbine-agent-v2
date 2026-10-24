"""Text and Markdown structure with original quotes and explicit source locations."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import re
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
from PIL import Image

from turbine_kg.documents.ir import ParsedPage, SourceUnit, TableData, Picture
from turbine_kg.runtime import identity,file_hash


def pipe_cells(line):
    """Split unescaped pipes outside code; retain each cell's exact source spelling."""
    spans=[];start=0;escaped=False;code=False
    for index,char in enumerate(line.rstrip('\r\n')):
        if escaped:escaped=False;continue
        if char=='\\':escaped=True;continue
        if char=='`':code=not code;continue
        if char=='|' and not code:spans.append((start,index));start=index+1
    spans.append((start,len(line.rstrip('\r\n'))))
    if len(spans)<2:return []
    if not line[:spans[0][1]].strip():spans.pop(0)
    if spans and not line[spans[-1][0]:spans[-1][1]].strip():spans.pop()
    return [(line[a:b].strip(),a,b) for a,b in spans]


def parse_text(text,path,document_id,original_hash,*,scope_sources=()):
    lines=text.splitlines(keepends=True)
    page=ParsedPage(1,None,0,0,{'status':'native_text','coordinate_system':'text_lines'})
    if not text.strip():page.status='blank';page.mapping['content_status']='verified_empty_text';return page
    offsets=[];offset=0
    for line in lines:offsets.append(offset);offset+=len(line)
    active=[];notes=[];references={};list_indents=[];index=0

    def locator(start,end):
        return {'physical_page':1,'line_range':[start+1,end],'coordinate_system':'text_lines','source_path':str(path.resolve()),'original_hash':original_hash}

    def context():
        return [asdict(unit) for unit in active]+list(scope_sources)

    def emit(kind,start,end,attrs=None):
        raw=''.join(lines[start:end]).rstrip('\r\n');loc=locator(start,end)
        attributes={'source_file_char_range':[offsets[start],offsets[end] if end<len(offsets) else len(text)],'section_sources':context(),**(attrs or {})}
        unit=SourceUnit(identity(kind,document_id,loc,raw),kind,raw,loc,'native_text',start,' / '.join(entry.attributes['display_text'] for entry in active),'readable' if '\ufffd' not in raw else 'unrecognized',attributes)
        page.sources.append(unit);return unit

    while index<len(lines):
        line=lines[index].rstrip('\r\n')
        if not line.strip():index+=1;list_indents=[];continue
        heading=re.match(r'^ {0,3}(#{1,6})[ \t]+(.+?)\s*$',line)
        setext=index+1<len(lines) and bool(re.match(r'^ {0,3}(?:=+|-{3,})\s*$',lines[index+1])) and not pipe_cells(line)
        if heading or setext:
            level=len(heading.group(1)) if heading else (1 if lines[index+1].lstrip().startswith('=') else 2)
            display=re.sub(r'[ \t]+#+[ \t]*$','',heading.group(2)) if heading else line.strip()
            unit=emit('heading',index,index+(1 if heading else 2),{'heading_level':level,'hierarchy':'explicit_markup','display_text':display,'semantic_status':'context_only'})
            active=[entry for entry in active if entry.attributes['heading_level']<level]+[unit]
            index+=1 if heading else 2;list_indents=[];continue
        fence=re.match(r'^ {0,3}(`{3,}|~{3,})\s*(\S*)',line)
        if fence:
            end=index+1
            while end<len(lines) and not re.match(r'^ {0,3}'+re.escape(fence.group(1)[0])+r'{'+str(len(fence.group(1)))+r',}\s*$',lines[end]):end+=1
            closed=end<len(lines);end+=1 if closed else 0
            emit('formula' if fence.group(2).lower() in ('math','latex','tex') else 'code',index,end,{'language':fence.group(2),'closed':closed,'semantic_status':'pending' if closed and fence.group(2).lower() in ('math','latex','tex') else 'context_only'})
            if not closed:page.warnings.append('代码或公式围栏未闭合，保留原文')
            index=end;continue
        if line.strip().startswith(('$$',r'\[')):
            delimiter='$$' if line.strip().startswith('$$') else r'\]'
            same_line=(line.strip().endswith(delimiter) and len(line.strip())>len(delimiter)*2)
            end=index+1
            if not same_line:
                while end<len(lines) and delimiter not in lines[end]:end+=1
                closed=end<len(lines);end+=1 if closed else 0
            else:closed=True
            emit('formula',index,end,{'math_format':'latex','closed':closed,'semantic_status':'pending' if closed else 'pending_review'})
            if not closed:page.warnings.append('公式块未闭合，保留原文并待复核')
            index=end;continue
        headers=pipe_cells(line);separator=pipe_cells(lines[index+1]) if index+1<len(lines) else []
        if headers and len(headers)==len(separator) and all(re.fullmatch(r':?-{3,}:?',cell[0].replace(' ','')) for cell in separator):
            end=index+2;rows=[(index,headers)]
            while end<len(lines) and (values:=pipe_cells(lines[end])):rows.append((end,values));end+=1
            columns=max(len(values) for _,values in rows);loc=locator(index,end);tid=identity('table',document_id,loc);cells=[]
            for row,(line_index,values) in enumerate(rows):
                for column in range(columns):
                    missing=column>=len(values);raw='' if missing else values[column][0]
                    attrs={'row_index':row,'column_index':column,'row_span':1,'column_span':1,'column_header':row==0,'row_header':False,'cell_state':'missing' if missing else 'blank' if not raw else 'unrecognized' if '\ufffd' in raw else 'readable','section_sources':context(),'display_text':raw.replace(r'\|','|'),'alignment':separator[column][0] if column<len(separator) else None}
                    cellloc=locator(line_index,line_index+1)
                    if not missing:cellloc['column_char_range']=[values[column][1],values[column][2]]
                    cells.append(SourceUnit(identity('cell',document_id,tid,row,column), 'cell',raw,cellloc,'native_text',len(cells),' / '.join(entry.attributes['display_text'] for entry in active),attrs['cell_state'],attrs))
            quality='parsed' if any(cell.text and not cell.attributes['column_header'] for cell in cells) else 'structure_only'
            page.tables.append(TableData(tid,'',' / '.join(entry.attributes['display_text'] for entry in active),loc,len(rows),columns,cells,quality=quality,continuation={'status':'not_indicated'},order=index))
            index=end;list_indents=[];continue
        footnote=re.match(r'^ {0,3}\[\^([^\]]+)\]:',line)
        if footnote:
            end=index+1
            while end<len(lines) and lines[end].startswith(('    ','\t')):end+=1
            notes.append(emit('paragraph',index,end,{'footnote_label':footnote.group(1),'semantic_status':'context_only'}));index=end;continue
        reference=re.match(r'^ {0,3}\[([^\]]+)\]:\s*(<[^>]+>|\S+)(?:\s+["\']([^"\']*)["\'])?\s*$',line)
        if reference:
            unit=emit('paragraph',index,index+1,{'reference_label':reference.group(1),'semantic_status':'context_only'})
            references[re.sub(r'\s+',' ',reference.group(1)).casefold()]=(reference.group(2).strip('<>'),reference.group(3),unit.id)
            index+=1;continue
        bullet=re.match(r'^(\s*)([-+*]|\d+[.)])[ \t]+',line)
        if bullet:
            indent=len(bullet.group(1).expandtabs(4))
            list_indents=[value for value in list_indents if value<=indent]
            if not list_indents or list_indents[-1]!=indent:list_indents.append(indent)
            end=index+1
            while end<len(lines) and lines[end].strip() and len(lines[end])-len(lines[end].lstrip())>indent and not re.match(r'^\s*(?:[-+*]|\d+[.)])[ \t]+',lines[end]):end+=1
            emit('list_item',index,end,{'list_level':len(list_indents),'list_marker':bullet.group(2),'ordered':bullet.group(2)[0].isdigit(),'semantic_status':'pending'});index=end;continue
        end=index+1
        while end<len(lines) and lines[end].strip():
            if re.match(r'^\s*(?:#{1,6}\s|[-+*]\s|\d+[.)]\s|\[\^|`{3,}|~{3,}|\$\$|\\\[)',lines[end]) or pipe_cells(lines[end]):break
            if end+1<len(lines) and re.match(r'^ {0,3}(?:=+|-{3,})\s*$',lines[end+1]):break
            end+=1
        unit=emit('paragraph',index,end,{'semantic_status':'pending'})
        inline=[match.group() for match in re.finditer(r'(?<!\\)\$[^$\n]+\$|\\\(.+?\\\)',unit.text)]
        if inline:unit.attributes['inline_formulas']=inline
        index=end;list_indents=[]

    image_pattern=re.compile(r'!\[([^\]]*)\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+["\']([^"\']*)["\'])?\s*\)')
    for unit in page.sources+[cell for table in page.tables for cell in table.cells]:
        if unit.kind in ('code','formula'):continue
        inline=[match.group() for match in re.finditer(r'(?<!\\)\$[^$\n]+\$|\\\(.+?\\\)',unit.text)]
        if inline:unit.attributes['inline_formulas']=inline
        image_matches=[(match,match.group(2).strip('<>'),match.group(3),None) for match in image_pattern.finditer(unit.text)]
        for match in re.finditer(r'!\[([^\]]*)\]\[([^\]]*)\]',unit.text):
            label=re.sub(r'\s+',' ',match.group(2) or match.group(1)).casefold()
            target,title,source_id=references.get(label,('',None,None))
            image_matches.append((match,target,title,source_id))
        for match,target,title,reference_source_id in image_matches:
            parsed=urlparse(target)
            reference={'target':target,'status':'unresolved'}
            unit.attributes.setdefault('image_references',[]).append(reference)
            if not target:page.warnings.append('图片引用缺少定义：'+match.group());continue
            if parsed.scheme and parsed.scheme!='file' and not Path(target).is_absolute():page.warnings.append('外部图片引用尚未读取像素：'+target);continue
            local=Path(url2pathname(parsed.path) if parsed.scheme=='file' else unquote(target))
            if not local.is_absolute():local=path.parent/local
            local=local.resolve()
            try:
                with Image.open(local) as asset:width,height=asset.size;asset.verify()
                digest=file_hash(local)
            except (OSError,ValueError):page.warnings.append('本地图片引用无法读取：'+target);continue
            pid=identity('picture',document_id,unit.id,match.start(),digest)
            loc={**unit.locator,'reference_char_range':[match.start(),match.end()],'image_origin':'referenced_original_asset'}
            caption=match.group(1);links=[{'source_id':unit.id,'relation':'explicit_image_reference'}]
            if reference_source_id:links.append({'source_id':reference_source_id,'relation':'explicit_image_reference_definition'})
            picture=Picture(pid,str(local),digest,width,height,loc,caption or title or '',unit.section,links,order=unit.order)
            page.pictures.append(picture);reference.update(status='local_pixels_available',picture_id=pid,content_hash=digest)
    for table in page.tables:
        for note in notes:
            relevant=[cell for cell in table.cells if '[^'+note.attributes['footnote_label']+']' in cell.text]
            if not relevant:continue
            attrs={**note.attributes,'coverage':{'status':'explicit_cell_references','cells':[cell.id for cell in relevant],'rows':sorted({cell.attributes['row_index'] for cell in relevant})}}
            table.notes.append(SourceUnit(identity('table_note',document_id,table.id,note.id),'table_note',note.text,dict(note.locator),note.text_origin,note.order,note.section,note.quality,attrs))
    return page
