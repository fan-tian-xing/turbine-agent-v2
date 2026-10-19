"""Memory-only PDF/text parser and persistent, usable original figure assets."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import json
import os
import re
from unicodedata import category, normalize
from statistics import median
import pymupdf as fitz

from turbine_kg.documents.ir import ParsedPage, SourceUnit, TableData, Picture, bind_list_parents, clause_number, recognize_table_headers
from turbine_kg.documents.mapping import PageMapper
from turbine_kg.runtime import identity, file_hash
from turbine_kg.settings import PROJECT_ROOT


class DocumentParser:
    def __init__(self, path: Path, document_id: str, *, derived: Path | None = None):
        self.path, self.document_id = path, document_id
        self.original_hash = file_hash(path)
        self.annotations = json.loads((PROJECT_ROOT / 'config/source_annotations.json').read_text(encoding='utf-8'))
        self.rules = self.annotations.get(self.original_hash, {})
        self.original = fitz.open(path) if path.suffix.lower() == '.pdf' else None
        self.derived = fitz.open(derived) if derived else None
        self.mapper = PageMapper(self.original, self.derived) if self.original else None
        self.converter = None
        self.section = ''
        self._navigation_cache = {}
        self._navigation_text_cache = {}
        self._page_single_column = {}
        self.heading_index = self._heading_index() if self.original else []
        self.scope_sources = self._scope_sources() if self.original else []
        self.active_scope_sources = []

    def register_scope_sources(self, sources, *, applies_to, physical_page_range=None, section_source_ids=None):
        """Register scope meaning established by the caller, with its actual source.

        Titles never create applicability. The caller must explicitly state whether
        the discovered source applies to a document, page range, or heading scope.
        """
        if applies_to not in ('document','page_range','section'):raise ValueError('未知适用范围类型')
        if applies_to=='page_range' and (not physical_page_range or len(physical_page_range)!=2):raise ValueError('适用范围缺少明确页范围')
        if applies_to=='section' and not section_source_ids:raise ValueError('章节适用范围缺少实际标题来源ID')
        if not hasattr(self,'active_scope_sources'):self.active_scope_sources=[]
        for item in sources:
            record=asdict(item) if isinstance(item,SourceUnit) else dict(item)
            if not record.get('id') or not record.get('text') or not record.get('locator'):raise ValueError('适用范围必须绑定实际来源')
            record['attributes']={**record.get('attributes',{}),'scope_source':True,'semantic_status':'context_only','applies_to':applies_to,'physical_page_range':physical_page_range,'section_source_ids':list(section_source_ids or [])}
            self.active_scope_sources=[entry for entry in self.active_scope_sources if entry['id']!=record['id']]+[record]

    def _applicable_scope_sources(self, number, headings):
        records=[asdict(entry) for entry in self.scope_sources]+getattr(self,'active_scope_sources',[])
        heading_ids={entry.id for entry in headings}
        return [record for record in records if record['attributes'].get('applies_to')=='document' or (record['attributes'].get('applies_to')=='page_range' and record['attributes']['physical_page_range'][0]<=number<=record['attributes']['physical_page_range'][1]) or (record['attributes'].get('applies_to')=='section' and heading_ids.intersection(record['attributes'].get('section_source_ids',[])))]

    def _scope_sources(self):
        sources = []
        for rule in self.rules.get('scope_sources',[]):
            loc = {'physical_page':rule['physical_page'],'bbox':rule['bbox_pdf_pt'],'coordinate_system':'original_display_top_left_pdf_points','original_hash':self.original_hash}
            text = rule['text']
            sources.append(SourceUnit(identity('scope-source',self.document_id,loc,text),'paragraph',text,loc,rule.get('text_origin','verified_ocr_text'),len(sources),'','verified',{'semantic_status':'context_only','scope_source':True,'applies_to':rule.get('applies_to','document'),'physical_page_range':rule.get('physical_page_range')}))
        return sources

    def _heading_index(self):
        """Index source headings without running layout inference on every page.

        Only explicit section numbering or chapter titles are accepted. A paragraph's
        clause number never establishes a parent scope merely by matching its prefix.
        """
        headings = []
        for index, original in enumerate(self.original):
            if self.is_navigation_page(index + 1): continue
            record = self.mapper.mapping.get(index, {})
            use_derived = len(original.get_text().strip()) < 80 and record.get('status') == 'verified_pixel_match'
            processing = self.derived[record['derived_page'] - 1] if use_derived else original
            lines = []
            for block in processing.get_text('dict')['blocks']:
                for line in block.get('lines', []):
                    text = ''.join(span['text'] for span in line.get('spans', [])).strip()
                    box = self.mapper.display_box(line['bbox'],index,derived=use_derived)
                    lines.append({'text':text,'bbox':box,'spans':line.get('spans',[])})
            font_sizes = [span['size'] for line in lines for span in line['spans'] if len(span['text'].strip()) > 3]
            body_size = median(font_sizes) if font_sizes else 10
            # OCR sometimes stores a heading number and its title as two lines
            # in unrotated PDF space. Join only fragments sharing a display line.
            combined = []
            for line in sorted(lines,key=lambda entry:(entry['bbox'][1],entry['bbox'][0])):
                previous = next((entry for entry in reversed(combined) if abs(entry['bbox'][1]-line['bbox'][1]) < max(3,(line['bbox'][3]-line['bbox'][1])*.45) and ((0 <= line['bbox'][0]-entry['bbox'][2] < 80) or (0 <= entry['bbox'][0]-line['bbox'][2] < 80))),None)
                if previous:
                    if line['bbox'][0] < previous['bbox'][0]: previous['text']=line['text']+' '+previous['text'];previous['spans']=line['spans']+previous['spans']
                    else: previous['text'] += ' ' + line['text']; previous['spans'].extend(line['spans'])
                    previous['bbox']=list(fitz.Rect(previous['bbox']) | fitz.Rect(line['bbox']))
                else: combined.append(line)
            for line in combined:
                    text = line['text']
                    numbered = re.match(r'^(\d+(?:[.\-]\d+){0,2})[\s　]+(.{2,45})$', text)
                    centered = abs((line['bbox'][0]+line['bbox'][2])/2 - original.rect.width/2) < original.rect.width*.12
                    if not numbered and centered:
                        numbered = re.match(r'^(\d+(?:[.\-]\d+){0,2})([\u4e00-\u9fff]{2,20})$',text)
                    chapter = re.match(r'^第[一二三四五六七八九十百\d]+[章节篇]\s*[^。；;]{2,45}$', text)
                    if not (numbered or chapter) or re.search(r'[。；;]|应当|不得|必须|不应|应符合',text): continue
                    if numbered and not re.search(r'[\u4e00-\u9fffA-Za-z]',numbered.group(2)): continue
                    chinese_spans = [span for span in line['spans'] if re.search(r'[\u4e00-\u9fff]',span['text'])]
                    title_style = bool(chinese_spans) and all('hei' in span['font'].lower() or 'bold' in span['font'].lower() or span['flags'] & 16 or span['size'] > body_size * 1.15 for span in chinese_spans)
                    title_size = median([span['size'] for span in chinese_spans]) if chinese_spans else 0
                    level = 1 if chapter else len(re.split(r'[.\-]',numbered.group(1)))
                    if not chapter and level == 1 and not (centered and title_size > body_size*1.2): continue
                    if not chapter and not ((title_style and title_size > body_size*1.05) or (centered and numbered and level == 2 and len(numbered.group(2)) < 25)): continue
                    loc = {'physical_page': index + 1, 'bbox': line['bbox'], 'coordinate_system': 'original_display_top_left_pdf_points', 'original_hash': self.original_hash}
                    headings.append(SourceUnit(identity('heading',self.document_id,loc,text),'heading',text,loc,'ocr_text' if use_derived else 'native_text',len(headings),text,'detected',{'heading_level':level,'hierarchy':'explicit_numbering','semantic_status':'context_only'}))
        return headings

    def close(self):
        if self.original: self.original.close()
        if self.derived: self.derived.close()

    @property
    def page_count(self): return len(self.original) if self.original else 1

    def _navigation_lines(self, number):
        """Read original text, or OCR text only after its page mapping is verified."""
        cache = getattr(self, '_navigation_text_cache', None)
        if cache is None: self._navigation_text_cache = cache = {}
        if number not in cache:
            if not self.original:
                text = self.path.read_text(encoding='utf-8')
            else:
                text = self.original[number - 1].get_text().strip()
                mapped = self.mapper.mapping.get(number - 1, {})
                if (len(text) < 80 or '\ufffd' in text) and self.derived and mapped.get('status') == 'verified_pixel_match':
                    text = self.derived[mapped['derived_page'] - 1].get_text().strip()
            cache[number] = [normalize('NFKC', line).strip() for line in text.splitlines() if line.strip()]
        return cache[number]

    def is_navigation_page(self, number: int) -> bool:
        """Exclude a whole TOC only with a real heading and navigation structure.

        Titleless continuations require an immediately preceding verified TOC and
        repeated leader entries. Ordinary numbered clauses never establish this.
        The caches are local processing state, not knowledge-graph provenance.
        """
        if not 1 <= number <= self.page_count: raise ValueError('页面超出原件范围')
        cache = getattr(self, '_navigation_cache', None)
        if cache is None: self._navigation_cache = cache = {}
        if number in cache: return cache[number]
        lines = self._navigation_lines(number)
        heading_pattern = r'(?:目录|目次|tableofcontents|contents)(?:\(continued\)|\(续\)|续)?'
        heading_indices = {index for index, line in enumerate(lines) if re.fullmatch(heading_pattern, re.sub(r'\s+', '', line.lstrip('#').strip()).lower())}
        # Leaders distinguish navigational page references from engineering values.
        leaders = re.compile(r'^(.+?)[.·•…⋯]{3,}\s*(\d{1,6}|[ivxlcdm]+|[a-z]-\d+)\s*$', re.I)
        columns = re.compile(r'^((?:\d+(?:[.\-]\d+)*|[A-Za-z]|第[一二三四五六七八九十百\d]+[章节篇])\s+.+?)\s+(\d{1,6}|[ivxlcdm]+)\s*$', re.I)

        def title_text(text):
            return bool(re.search(r'[A-Za-z\u4e00-\u9fff]', text)) and len(text) <= 180 and not re.search(r'[。；;!?！？=<>≤≥]|\ufffd|\b\d+(?:\.\d+)?\s*(?:mm|cm|MPa|kPa|rpm|kg|N|%|℃)\b', text, re.I)

        entries = set(); leader_entries = set()
        for index, line in enumerate(lines):
            match = leaders.fullmatch(line)
            if match and title_text(match.group(1)):
                entries.add(index); leader_entries.add(index)
            elif heading_indices:
                match = columns.fullmatch(line)
                if match and title_text(match.group(1)): entries.add(index)
        found_heading = bool(heading_indices)
        enough = len(entries) >= (3 if found_heading else 6)
        if not enough or (not found_heading and len(leader_entries) < 6):
            cache[number] = False
            return False
        explained = entries | heading_indices
        for index, line in enumerate(lines):
            if re.fullmatch(r'(?:\d{1,6}|[ivxlcdm]+)', line, re.I): explained.add(index)
            # A running header must actually repeat on a neighboring source page.
            if index == 0 and self.original:
                neighbors = [self._navigation_lines(other) for other in (number - 1, number + 1) if 1 <= other <= self.page_count]
                if any(other and other[0] == line for other in neighbors): explained.add(index)
            # Wrapped titles are immediately followed by a leader/page reference.
            if index + 1 in leader_entries and title_text(line): explained.add(index)
        complete_structure = len(explained) == len(lines)
        result = complete_structure and (found_heading or (number > 1 and self.is_navigation_page(number - 1)))
        cache[number] = result
        return result

    def unmapped_ocr_sources(self):
        """Keep readable derivative text without inventing an original-page link."""
        if not self.derived: return []
        derivative_hash = file_hash(Path(self.derived.name))
        units = []
        verified_pages = {record['derived_page'] for record in self.mapper.mapping.values() if record.get('status') == 'verified_pixel_match'}
        for page in self.derived:
            if page.number + 1 in verified_pages: continue
            for block in page.get_text('blocks'):
                if block[6] != 0 or not block[4].strip(): continue
                loc = {'derived_physical_page':page.number + 1,'derived_path':str(Path(self.derived.name)),'derived_hash':derivative_hash,'bbox':list(fitz.Rect(block[:4]) * page.rotation_matrix),'coordinate_system':'derived_display_top_left_pdf_points','mapping_status':'unknown','original_physical_page':None}
                units.append(SourceUnit(identity('paragraph',self.document_id,loc,block[4]),'paragraph',block[4].strip(),loc,'ocr_text',len(units),'','unverified_mapping',{'semantic_status':'pending_review'}))
        return units

    def _convert(self, page_number, processing):
        if self.converter is None:
            configuration=json.loads((PROJECT_ROOT/'config/agent.json').read_text(encoding='utf-8')).get('parser',{})
            cache=configuration.get('model_cache')
            if cache:
                cache_path=(PROJECT_ROOT/cache).resolve()
                if cache_path.is_dir():
                    os.environ.setdefault('HF_HOME',str(cache_path.parent))
                    os.environ.setdefault('HF_HUB_CACHE',str(cache_path))
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
            options = PdfPipelineOptions()
            options.do_ocr = False
            options.do_table_structure = True
            options.heading_hierarchy_options.enabled = True
            options.generate_parsed_pages = True
            options.generate_page_images = False
            options.document_timeout = 240
            self.converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options, backend=PyPdfiumDocumentBackend)})
        return self.converter.convert(processing, page_range=(page_number, page_number)).document

    def _layout_reading_order(self, items, number, processing_page, *, derived):
        """Recover one-column geometry; retain model flow for separate columns."""
        positioned = []
        headings = [unit for unit in self.heading_index if unit.locator['physical_page'] == number]
        for item, depth in items:
            box = None
            if item.prov and item.prov[0].page_no == processing_page.number + 1:
                raw = item.prov[0].bbox.to_top_left_origin(page_height=processing_page.rect.height).as_tuple()
                box = fitz.Rect(self.mapper.display_box(raw, number - 1, derived=derived))
            indexed = None
            if box is not None and str(item.label.value) in ('section_header', 'title'):
                indexed = next((heading for heading in headings
                                if re.sub(r'\s', '', heading.text) == re.sub(r'\s', '', getattr(item, 'text', ''))
                                and (box & fitz.Rect(heading.locator['bbox'])).get_area() > 0), None)
            positioned.append((item, depth, box, indexed))
        body=[row for row in positioned if row[2] is not None and hasattr(row[0],'text')
              and (str(row[0].label.value) not in ('section_header','title','formula','caption','page_header','page_footer','footnote') or clause_number(getattr(row[0],'text','')))
              # Detached list markers and bare clause numbers are too narrow to
              # establish a separate text column.
              and row[2].width>max(40,row[2].height*2)]
        single_column=len(body)>=2 and max(row[2].x0 for row in body)<min(row[2].x1 for row in body)
        if not hasattr(self,'_page_single_column'):self._page_single_column={}
        self._page_single_column[number]=single_column
        if single_column:
            # Small detached enumeration markers share their actual line with
            # its text. Order them left-to-right on that line, not in the model's
            # separate marker block. Tables/formulas spanning lines stay intact.
            located=sorted((row for row in positioned if row[2] is not None),key=lambda row:(row[2].y0,row[2].x0))
            lines=[]
            for row in located:
                previous=lines[-1] if lines else []
                anchor=previous[0][2] if previous else None
                overlap=min(anchor.y1,row[2].y1)-max(anchor.y0,row[2].y0) if anchor else 0
                if anchor and overlap>=.5*min(anchor.height,row[2].height) and max(anchor.height,row[2].height)<=2*min(anchor.height,row[2].height):
                    previous.append(row)
                else:lines.append([row])
            ordered=iter(row for line in lines for row in sorted(line,key=lambda row:row[2].x0))
            return [(next(ordered)[0:2] if row[2] is not None else row[0:2]) for row in positioned]
        # Layout models can emit a numbered engineering clause as a header and
        # move it ahead of earlier body. Restore its position only in its actual
        # overlapping column; this leaves unrelated columns in model order.
        for clause_row in [row for row in positioned if row[2] is not None and str(row[0].label.value) in ('section_header', 'title') and clause_number(getattr(row[0], 'text', ''))]:
            box = clause_row[2]
            positioned.remove(clause_row)
            column = [(index, row) for index, row in enumerate(positioned) if row[2] is not None
                      and min(row[2].x1, box.x1) - max(row[2].x0, box.x0) > min(row[2].width, box.width) * .5]
            below = [index for index, row in column if row[2].y0 >= box.y1 - 1]
            above = [index for index, row in column if row[2].y1 <= box.y0 + 1]
            position = min(below) if below else max(above) + 1 if above else len(positioned)
            positioned.insert(position, clause_row)
        for heading_row in sorted((row for row in positioned if row[3]), key=lambda row: (row[2].y0, row[2].x0)):
            heading_box, heading = heading_row[2:]
            at = positioned.index(heading_row)
            preceding_body = [index for index, row in enumerate(positioned[:at])
                              if row[2] is not None and row[3] is None
                              and row[2].y0 >= heading_box.y1 - 1
                              and (heading.attributes['heading_level'] == 1
                                   or min(row[2].x1, heading_box.x1) > max(row[2].x0, heading_box.x0))]
            if preceding_body:
                positioned.pop(at)
                positioned.insert(min(preceding_body), heading_row)
        return [(item, depth) for item, depth, _, _ in positioned]

    def _headings_at(self, number, locator):
        """Bind source headings by original display geometry, never visit order."""
        box=fitz.Rect(locator['bbox']) if locator.get('bbox') else None
        single_column=getattr(self,'_page_single_column',{}).get(number,False)
        candidates=[]
        for heading in self.heading_index:
            page=heading.locator.get('physical_page')
            if not isinstance(page,int) or page>number:continue
            heading_box=fitz.Rect(heading.locator['bbox']) if heading.locator.get('bbox') else None
            if page==number:
                if box is None or heading_box is None or heading_box.y0>box.y0+1:continue
                global_heading=heading.attributes.get('heading_level')==1
                originals=getattr(self,'original',None)
                page_width=originals[number-1].rect.width if originals and len(originals)>=number else None
                centered_heading=(page_width is not None and
                                  abs((heading_box.x0+heading_box.x1)/2-page_width/2)<page_width*.12)
                intersects=min(heading_box.x1,box.x1)>max(heading_box.x0,box.x0)
                if not (single_column or global_heading or centered_heading or intersects):continue
            candidates.append(heading)
        candidates.sort(key=lambda heading:(heading.locator['physical_page'],heading.locator.get('bbox',[0,0])[1],heading.locator.get('bbox',[0,0])[0]))
        active=[]
        for heading in candidates:
            level=heading.attributes['heading_level']
            active=[entry for entry in active if entry.attributes['heading_level']<level]+[heading]
        return active

    def page(self, number: int) -> ParsedPage:
        if self.is_navigation_page(number):
            original = self.original[number - 1] if self.original else None
            return ParsedPage(number, None, original.rect.width if original else 0, original.rect.height if original else 0,
                              {'content_status':'excluded_navigation'}, status='excluded_navigation')
        if not self.original:
            if number!=1:raise ValueError('文本资料仅有一个逻辑页面，定位使用源文件行范围')
            from turbine_kg.documents.text import parse_text
            text = self.path.read_text(encoding='utf-8')
            parsed=parse_text(text,self.path,self.document_id,self.original_hash,scope_sources=self._applicable_scope_sources(1,[]))
            self.heading_index=[unit for unit in parsed.sources if unit.kind=='heading']
            self._attach_explicit_table_references(parsed)
            return parsed
        index = number - 1
        original = self.original[index]
        section_sources = [heading for heading in self.heading_index if heading.locator['physical_page'] < number]
        active_headings = []
        for heading in section_sources:
            level = heading.attributes['heading_level']
            active_headings = [entry for entry in active_headings if entry.attributes['heading_level'] < level] + [heading]
        self.section = ' / '.join(heading.text for heading in active_headings)
        mapped = self.mapper.mapping.get(index, {'status': 'unavailable'})
        native = original.get_text().strip()
        use_derived = (len(native) < 80 or '\ufffd' in native) and mapped['status'] == 'verified_pixel_match'
        processing_page = self.derived[mapped['derived_page'] - 1] if use_derived else original
        processing_path = Path(self.derived.name) if use_derived else self.path
        processing_number = processing_page.number + 1
        page = ParsedPage(number, None, original.rect.width, original.rect.height, {'status': 'original' if not use_derived else mapped['status'], 'ocr_mapping': mapped, 'original_rotation': original.rotation, 'coordinate_system': 'original_display_top_left_pdf_points'})
        if not native and not original.get_images() and not original.get_drawings():
            pix = original.get_pixmap(matrix=fitz.Matrix(.5,.5),colorspace=fitz.csGRAY,alpha=False)
            values = memoryview(pix.samples)
            minimum,maximum=min(values),max(values)
            if minimum >= 250 and maximum-minimum <= 1:
                page.status='blank';page.mapping['content_status']='verified_blank'
                page.mapping['blank_verification']={'native_characters':0,'image_objects':0,'drawing_objects':0,'minimum_gray':minimum,'maximum_gray':maximum,'render_scale':.5}
                return page
        origin = 'ocr_text' if use_derived else 'native_text'

        def location(box, *, already_display=True):
            # Docling/Pdfium provenance is display-page geometry; PyMuPDF text
            # and vector-grid coordinates are unrotated. Apply PDF rotation once.
            result = {'physical_page': number, 'bbox': self.mapper.display_box(box, index, derived=use_derived,already_display=already_display), 'coordinate_system': 'original_display_top_left_pdf_points', 'original_hash': self.original_hash}
            if use_derived: result['derived_page'] = processing_number; result['mapping'] = mapped
            return result

        def source(kind, text, loc, order, attributes=None):
            attrs = dict(attributes or {})
            headings=self._headings_at(number,loc)
            attrs['section_sources'] = [{'id': entry.id, 'kind': entry.kind, 'text': entry.text, 'locator': entry.locator, 'text_origin': entry.text_origin, **entry.attributes} for entry in headings] + self._applicable_scope_sources(number,headings)
            attrs['section_basis']='original_heading_geometry'
            section=' / '.join(heading.text for heading in headings)
            return SourceUnit(identity(kind, self.document_id, number, order, loc, text), kind, text, loc, origin, order, section, 'readable' if text.strip() and '\ufffd' not in text else 'unrecognized' if text else 'blank', attrs)

        try:
            doc = self._convert(processing_number, processing_path)
            items = self._layout_reading_order(list(doc.iterate_items()), number, processing_page, derived=use_derived)
            table_regions = []
            for order, (item, _) in enumerate(items):
                if not item.prov: continue
                prov = item.prov[0]
                if prov.page_no != processing_number: continue
                box = prov.bbox.to_top_left_origin(page_height=processing_page.rect.height).as_tuple()
                loc = location(box)
                active_headings=self._headings_at(number,loc)
                self.section=' / '.join(heading.text for heading in active_headings)
                label = str(item.label.value)
                if hasattr(item, 'data') and hasattr(item.data, 'table_cells'):
                    table_id = identity('table', self.document_id, number, loc)
                    title = '\n'.join(doc.texts[int(ref.cref.rsplit('/',1)[1])].text for ref in item.captions if ref.cref.startswith('#/texts/'))
                    cells = []
                    for cell_index, cell in enumerate(item.data.table_cells):
                        attrs = {'row_index': cell.start_row_offset_idx, 'column_index': cell.start_col_offset_idx, 'row_span': cell.row_span, 'column_span': cell.col_span, 'column_header': cell.column_header, 'row_header': cell.row_header, 'row_section': cell.row_section, 'cell_state': 'blank' if not cell.text else 'unrecognized' if '\ufffd' in cell.text else 'readable'}
                        cellloc = location(cell.bbox.to_top_left_origin(page_height=processing_page.rect.height).as_tuple()) if cell.bbox else {**loc, 'cell_location': 'table_only'}
                        cells.append(source('cell', cell.text, cellloc, cell_index, attrs))
                    occupied = {(r,c) for cell in cells for r in range(cell.attributes['row_index'],cell.attributes['row_index']+cell.attributes['row_span']) for c in range(cell.attributes['column_index'],cell.attributes['column_index']+cell.attributes['column_span'])}
                    for r in range(item.data.num_rows):
                        for c in range(item.data.num_cols):
                            if (r,c) not in occupied:
                                cells.append(source('cell', '', {**loc,'cell_location':'missing'},len(cells), {'row_index':r,'column_index':c,'row_span':1,'column_span':1,'column_header':False,'row_header':False,'cell_state':'missing'}))
                    notes = []
                    for ref in item.footnotes:
                        note = ref.resolve(doc)
                        note_loc = location(note.prov[0].bbox.to_top_left_origin(page_height=processing_page.rect.height).as_tuple()) if note.prov else {**loc,'note_location':'table_only'}
                        notes.append(source('table_note', note.text, note_loc, len(notes), {'coverage': {'status': 'unknown'}}))
                    # A vector grid can verify merged geometry the layout model missed.
                    # Scanned pages without vector rulings keep the model result and its quality.
                    grids = processing_page.find_tables(clip=fitz.Rect(box)).tables
                    grid = next((candidate for candidate in grids if (fitz.Rect(candidate.bbox)&fitz.Rect(box)).get_area()>.75*fitz.Rect(box).get_area()),None)
                    rows_count,cols_count=item.data.num_rows,item.data.num_cols
                    if grid:
                        rows_count,cols_count=grid.row_count,grid.col_count
                        ys=sorted({round(value,1) for cellbox in grid.cells if cellbox for value in (cellbox[1],cellbox[3])})
                        xs=sorted({round(value,1) for cellbox in grid.cells if cellbox for value in (cellbox[0],cellbox[2])})
                        if len(ys)==rows_count+1 and len(xs)==cols_count+1:
                            model_cells = cells
                            cells=[]
                            for cell_index,cellbox in enumerate(grid.cells):
                                if not cellbox: continue
                                r0=min(range(len(ys)),key=lambda i:abs(ys[i]-cellbox[1]));r1=min(range(len(ys)),key=lambda i:abs(ys[i]-cellbox[3]))
                                c0=min(range(len(xs)),key=lambda i:abs(xs[i]-cellbox[0]));c1=min(range(len(xs)),key=lambda i:abs(xs[i]-cellbox[2]))
                                text=processing_page.get_text('text',clip=fitz.Rect(cellbox)).strip()
                                display_cell = fitz.Rect(location(cellbox,already_display=False)['bbox'])
                                overlaps = [cell for cell in model_cells if fitz.Rect(cell.locator['bbox']).get_area() and (display_cell & fitz.Rect(cell.locator['bbox'])).get_area() > .6 * min(display_cell.get_area(),fitz.Rect(cell.locator['bbox']).get_area())]
                                attrs={'row_index':r0,'column_index':c0,'row_span':r1-r0,'column_span':c1-c0,'column_header':any(cell.attributes.get('column_header') for cell in overlaps),'row_header':any(cell.attributes.get('row_header') for cell in overlaps) or (c0==0 and r1-r0>1),'row_section':any(cell.attributes.get('row_section') for cell in overlaps),'cell_state':'blank' if not text else 'unrecognized' if '\ufffd' in text else 'readable','geometry':'verified_vector_grid'}
                                if attrs['column_header']:
                                    attrs['header_basis']='docling_geometry_match'
                                cells.append(source('cell',text,location(cellbox,already_display=False),cell_index,attrs))
                    loc['header_recognition']=recognize_table_headers(cells,rows_count,cols_count)
                    quality = 'parsed' if any(cell.text for cell in cells) else 'unrecognized'
                    page.tables.append(TableData(table_id, title, self.section, loc, rows_count, cols_count, cells, notes, quality))
                    table_regions.append(fitz.Rect(loc['bbox']))
                elif hasattr(item, 'text'):
                    text = item.text
                    # Native clipping retains exact source text when available.
                    if not use_derived:
                        clipped = original.get_text('text', clip=fitz.Rect(loc['bbox']) * original.derotation_matrix).strip()
                        if clipped and '\ufffd' not in clipped: text = clipped
                    kind = 'heading' if label in ('section_header','title') else 'caption' if label == 'caption' else 'list_item' if label == 'list_item' else 'formula' if label == 'formula' else 'paragraph'
                    level = getattr(item, 'level', None)
                    if kind == 'heading' and clause_number(text):
                        kind = 'list_item'; level = None
                    ancestors=[];parent=getattr(item,'parent',None);seen=set()
                    while parent and parent.cref not in seen:
                        seen.add(parent.cref);ancestors.append(parent.cref)
                        parent=getattr(parent.resolve(doc),'parent',None)
                    page.sources.append(source(kind, text, loc, order, {'hierarchy': 'detected' if kind == 'heading' and level else 'unknown','heading_level':level,'semantic_status':'context_only' if kind in ('heading','caption') else 'pending','layout_ref':item.self_ref,'layout_parent_ref':ancestors[0] if ancestors else None,'layout_ancestor_refs':ancestors,'layout_depth':_,'list_marker':getattr(item,'marker',None),'enumerated':getattr(item,'enumerated',None)}))
                elif label == 'picture':
                    caption = '\n'.join(ref.resolve(doc).text for ref in item.captions)
                    self._picture(page, loc['bbox'], caption, {'method': 'layout', 'reference': item.self_ref})
            if not page.sources and not page.tables: raise ValueError('版面解析未返回文字或表格')
        except Exception as error:
            page.warnings.append('版面或表格解析未完成：' + type(error).__name__)
            page.mapping['layout_status'] = 'fallback_unverified'
            for order, block in enumerate(processing_page.get_text('blocks')):
                if block[6] != 0 or not block[4].strip(): continue
                text = block[4].strip()
                loc = location(block[:4], already_display=False)
                attrs = {'layout': 'fallback', 'table_structure': 'unknown', 'requires_layout_review': True}
                kind = 'paragraph'
                compact = re.sub(r'\s+', '', text)
                heading = next((entry for entry in self.heading_index if entry.locator['physical_page'] == number
                                and re.sub(r'\s+', '', entry.text) == compact), None)
                if heading:
                    kind = 'heading'
                    attrs.update(semantic_status='context_only', heading_level=heading.attributes['heading_level'])
                margin = loc['bbox'][3] < page.height * .09 or loc['bbox'][1] > page.height * .93
                if margin:
                    neighbors = [self._navigation_lines(other) for other in (number - 1, number + 1) if 1 <= other <= self.page_count]
                    repeated = any(any(re.sub(r'\s+', '', line) == compact for line in lines) for lines in neighbors)
                    if repeated or re.fullmatch(r'\d{1,4}|[ivxlcdm]+', compact, re.I):
                        attrs.update(source_role='page_number' if compact.isdigit() else 'running_header', semantic_status='context_only')
                unit = source(kind, text, loc, order, attrs)
                unit.quality = 'pending_layout_review'
                page.sources.append(unit)
            if not page.sources:
                page.warnings.append('原页缺少可读文字，且OCR原页映射未验证')
                page.status = 'unreadable'
        self._attach_table_context(page)
        self._annotations(page)
        bind_list_parents(page.sources)
        self._attach_explicit_table_references(page)
        # Reviewed geometry augments layout output; captions are linked explicitly.
        for region in self.rules.get('pictures', []):
            if region['physical_page'] == number:
                box = region['figure_bbox_pdf_pt']
                title = region.get('caption', {}).get('text', '')
                self._picture(page, box, title if '\ufffd' not in title else '', {'method': 'reviewed_geometry', 'figure_label': region.get('figure_label'), 'caption_locator': region.get('caption', {})})
                caption=region.get('caption',{})
                if caption.get('text') and caption.get('bbox_pdf_pt'):
                    locator={'physical_page':caption['physical_page'],'logical_page':caption.get('logical_page'),'bbox':caption['bbox_pdf_pt'],'coordinate_system':'original_display_top_left_pdf_points','original_hash':self.original_hash}
                    caption_unit=SourceUnit(identity('caption',self.document_id,locator,caption['text']),'caption',caption['text'],locator,'verified_caption',len(page.sources),self.section,'verified',{'review_basis':'source_hash_bound_annotation'})
                    if not any(unit.kind=='caption' and unit.text==caption_unit.text for unit in page.sources):page.sources.append(caption_unit)
                    for picture in page.pictures:
                        if (fitz.Rect(picture.locator['bbox'])&fitz.Rect(box)).get_area()>.8*min(fitz.Rect(picture.locator['bbox']).get_area(),fitz.Rect(box).get_area()):picture.links.append({'source_id':caption_unit.id,'relation':'verified_caption'})
                if region.get('logical_page'): page.logical_page = region['logical_page']
        # Layout extraction can emit punctuation-only paragraph fragments.
        # They have no standalone source meaning and must not become nodes.
        page.sources = [unit for unit in page.sources if not (
            unit.kind == 'paragraph' and unit.text.strip() and
            all(char.isspace() or category(char).startswith('P') for char in unit.text)
        )]
        for picture in page.pictures:
            picture.links.extend({'source_id':unit.id,'relation':'caption'} for unit in page.sources if unit.kind == 'caption' and re.sub(r'\s','',unit.text) == re.sub(r'\s','',picture.title))
            label = re.search(r'[图圖]\s*(?:[A-Za-z]\s*[-—]\s*)?[\d.]+(?:[-—]\d+)?', picture.title)
            if label:
                picture.links.extend({'source_id': unit.id, 'relation': 'explicit_figure_reference'} for unit in page.sources if label.group().replace(' ','') in unit.text.replace(' ','') and unit.kind != 'caption')
        if page.logical_page:
            located = page.sources + [cell for table in page.tables for cell in table.cells] + [note for table in page.tables for note in table.notes] + page.pictures
            for unit in located:
                if unit.locator.get('physical_page') == page.physical_page and not unit.locator.get('logical_page'): unit.locator['logical_page'] = page.logical_page
        if not page.sources and not page.tables and not page.pictures:
            page.status='unreadable';page.mapping['content_status']='content_not_parsed'
        return page

    @staticmethod
    def _attach_explicit_table_references(page):
        """Bind actual table titles and explicit references, without inferring scope.

        Shared, verified heading identities establish the section. Proximity alone
        cannot link body text, and a reviewed cross-page fragment retains its group
        identity so ingestion can read the complete group before extraction.
        """
        from unicodedata import normalize
        reference=re.compile(r'(?:表|(?<![A-Za-z])table)\s*([A-Za-z]?(?:\s*[.\-]\s*)?\d+(?:\s*[.\-]\s*\d+)*(?:[A-Za-z])?)(?![A-Za-z\d]|[.\-]\d)',re.I)
        def normalized(text):
            return normalize('NFKC',text).replace('—','-').replace('–','-')
        def labels(text):
            return {re.sub(r'\s','',match.group(1)).lower() for match in reference.finditer(normalized(text))}
        def reliable(unit):
            attrs=unit.attributes
            return bool(unit.text.strip()) and '\ufffd' not in unit.text and unit.text_origin in ('native_text','ocr_text','verified_correction','verified_ocr_text','verified_caption') and unit.quality in ('readable','verified','detected') and attrs.get('semantic_status')!='pending_review' and unit.locator.get('mapping_status') not in ('unknown','unverified')
        def heading_path(unit):
            path=[]
            for entry in unit.attributes.get('section_sources',[]):
                if entry.get('kind')!='heading':continue
                attrs=entry.get('attributes',entry)
                level=attrs.get('heading_level')
                if attrs.get('hierarchy') not in ('explicit_numbering','explicit_markup') or not isinstance(level,int) or isinstance(level,bool) or level<1 or not entry.get('id'):
                    return None
                path.append(entry['id'])
            return tuple(path) or None
        indexed={unit.id:unit for unit in page.sources}
        targets={}
        for table in page.tables:
            table.locator['related_body_source_ids']=[]
            table.locator.pop('related_body_association',None)
            identifiers=labels(table.title)
            if len(identifiers)!=1:continue
            identifier=next(iter(identifiers))
            titles=[unit for unit in page.sources if unit.kind in ('caption','heading') and reliable(unit) and labels(unit.text)=={identifier} and reference.match(re.sub(r'^\s*#{1,6}\s*','',normalized(unit.text))) and unit.section==table.section and unit.locator.get('original_hash')==table.locator.get('original_hash')]
            existing=[indexed[key] for key in table.locator.get('title_source_ids',[]) if key in indexed and indexed[key] in titles]
            selected=existing or titles
            table.locator['title_source_ids']=[selected[0].id] if len(selected)==1 else []
            paths=[heading_path(cell) for cell in table.cells]
            if not paths or not paths[0] or any(path!=paths[0] for path in paths) or table.quality!='parsed' or table.locator.get('semantic_status')=='pending_review':continue
            targets.setdefault((identifier,paths[0]),[]).append(table)
        for unit in page.sources:
            if unit.kind not in ('paragraph','clause','list_item') or not reliable(unit):continue
            if unit.attributes.get('semantic_status')=='context_only' and not unit.attributes.get('cross_page_group'):continue
            path=heading_path(unit)
            if not path:continue
            for identifier in labels(unit.text):
                matches=targets.get((identifier,path),[])
                if len(matches)!=1:continue
                table=matches[0]
                if unit.section!=table.section or unit.locator.get('original_hash')!=table.locator.get('original_hash'):continue
                table.locator['related_body_source_ids'].append(unit.id)
                table.locator['related_body_association']='explicit_table_reference_same_section'

    def _attach_table_context(self, page):
        """Bind explicit table captions and note blocks when the model detached them."""
        consumed = set()
        ordered = sorted(page.sources,key=lambda unit:(unit.locator['bbox'][1],unit.locator['bbox'][0]))
        for table in page.tables:
            region = fitz.Rect(table.locator['bbox'])
            captions = [unit for unit in ordered if re.match(r'^(?:续\s*)?表\s*[\dA-Za-z]',unit.text.strip()) and 0 <= region.y0-unit.locator['bbox'][3] < 35 and region.x0-50 <= (unit.locator['bbox'][0]+unit.locator['bbox'][2])/2 <= region.x1+50]
            if captions:
                caption = max(captions,key=lambda unit:unit.locator['bbox'][3])
                if not table.title: table.title=caption.text
                table.locator['title_source_ids']=[caption.id]
                caption.attributes['semantic_status']='context_only'
            if table.notes: continue
            note_start = next((index for index,unit in enumerate(ordered) if re.match(r'^注(?:\s*\d+)?\s*[:：]',unit.text.strip()) and 0 <= unit.locator['bbox'][1]-region.y1 < 30 and fitz.Rect(unit.locator['bbox']).x0 <= region.x1),None)
            if note_start is None: continue
            previous = None
            for unit in ordered[note_start:]:
                if previous:
                    continuation = re.match(r'^(?:\d+[.、．]|[A-Za-zƒη]\s*[为—])',unit.text.strip())
                    if not continuation or unit.locator['bbox'][1]-previous.locator['bbox'][3] > 20 or abs(unit.locator['bbox'][0]-previous.locator['bbox'][0]) > 40: break
                note = SourceUnit(identity('table_note',self.document_id,table.id,unit.locator,unit.text),'table_note',unit.text,dict(unit.locator),unit.text_origin,len(table.notes),unit.section,unit.quality,{**unit.attributes,'coverage':{'status':'unknown'},'association':'adjacent_explicit_table_note','semantic_status':'context_only'})
                table.notes.append(note);consumed.add(unit.id);previous=unit
        page.sources=[unit for unit in page.sources if unit.id not in consumed]

    def _annotations(self, page):
        import hashlib
        filtered=[]
        for unit in page.sources:
            digest=hashlib.sha256(unit.text.encode()).hexdigest()
            exclusion = next((rule for rule in self.rules.get('source_fragment_exclusions',[]) if rule['physical_page']==page.physical_page and rule['expected_text_sha256']==digest),None)
            if exclusion:
                # An incomplete clause remains source material; only navigation noise
                # is omitted. Complete reviewed groups are assembled by the store.
                if exclusion.get('kind') == 'navigation_only': continue
                unit.attributes.update(semantic_status='context_only',review_reason=exclusion.get('reason',''))
            bounds=next((rule for rule in self.rules.get('reviewed_page_body_bounds',[]) if rule['physical_page']==page.physical_page),None)
            if bounds and unit.locator['bbox'][3]<bounds.get('y_min',0): continue
            if bounds and unit.locator['bbox'][1]>bounds.get('y_max',page.height): continue
            splits=next((rule for rule in self.rules.get('reviewed_text_splits',[]) if rule['physical_page']==page.physical_page and rule['source_text_sha256']==digest),None)
            if splits:
                segments = []
                for index,segment in enumerate(splits['segments']):
                    rectangle=fitz.Rect(segment['bbox_pdf_pt'])
                    text=self.original[page.physical_page-1].get_text('text',clip=rectangle*self.original[page.physical_page-1].derotation_matrix).strip()
                    if hashlib.sha256(text.encode()).hexdigest()!=segment['text_sha256']:
                        page.warnings.append('已复核分段与当前正文不一致，保留待复核状态');break
                    segments.append(SourceUnit(identity('paragraph',self.document_id,unit.id,index),unit.kind,text,{**unit.locator,'bbox':list(rectangle)},unit.text_origin,unit.order+index,unit.section,unit.quality,{**unit.attributes,'semantic_status':'context_only' if segment['extractability']=='context_only' else 'pending'}))
                if len(segments) == len(splits['segments']): filtered.extend(segments)
                else:
                    unit.attributes['semantic_status']='pending_review';filtered.append(unit)
            else: filtered.append(unit)
        page.sources=filtered
        self._reviewed_row_boundaries(page)
        for rule in self.rules.get('table_structure_reviews',[]):
            if rule['physical_page']!=page.physical_page:continue
            rectangle=fitz.Rect([rule['bbox'][key] for key in ('x0','y0','x1','y1')])
            for table in page.tables:
                overlap=(rectangle & fitz.Rect(table.locator['bbox'])).get_area()
                if overlap < .7*rectangle.get_area():continue
                table.locator['reviewed_header_hierarchy']=rule.get('header_hierarchy',[])
                table.locator['reviewed_leaf_headers']=rule.get('leaf_headers',[])
                if rule.get('logical_page'):table.locator['logical_page']=rule['logical_page'];page.logical_page=rule['logical_page']
                previous,nextpage=rule.get('continuation_from_physical_page'),rule.get('continuation_to_physical_page')
                table.continuation={'status':'verified','previous_physical_page':previous,'next_physical_page':nextpage}
                for cell in table.cells:
                    if any(cell.text.replace(' ','').replace('\n','').startswith(header.replace(' ','')) for header in rule.get('leaf_headers',[])):cell.attributes['column_header']=True
                table.locator['reviewed_header_source_ids'] = [cell.id for cell in table.cells if cell.attributes.get('column_header')]
                if rule.get('inherited_header_text') and previous:
                    header_sources = self._reviewed_headers(previous,rule.get('leaf_headers',[]))
                    table.locator['inherited_header_source_ids'] = [entry.id for entry in header_sources]
                    for entry in header_sources:
                        if all(unit.id != entry.id for unit in page.sources): page.sources.append(entry)
                    if not header_sources: page.warnings.append('已复核续表页关系存在，但前页表头尚未定位；表头保持 unknown')
        source_units = page.sources + [cell for table in page.tables for cell in table.cells] + [note for table in page.tables for note in table.notes]
        for rule in self.rules.get('layout_review_states',[]):
            if rule['physical_page'] != page.physical_page: continue
            for unit in source_units: unit.attributes.update(semantic_status=rule['status'],review_reason=rule['reason'])
            for table in page.tables:
                table.locator['semantic_status']=rule['status'];table.quality='pending_review'
            page.warnings.append(rule['reason'])
        for rule in self.rules.get('ocr_margin_exclusions',[]):
            if rule['physical_page'] != page.physical_page: continue
            scale=self._annotation_scale(rule,page)
            if scale is None:continue
            for unit in source_units:
                box=fitz.Rect(unit.locator['bbox'])
                if box.x0 >= rule['x0_min']*scale-1 and box.y0 <= rule['y0_max']*scale:
                    unit.attributes.update(semantic_status='context_only',source_role='navigation_only',review_basis='source_hash_bound_margin_region')
            page.sources=[unit for unit in page.sources if unit.attributes.get('source_role') != 'navigation_only']
        for rule in self.rules.get('ocr_box_edits', []):
            if rule['physical_page'] != page.physical_page: continue
            scale=self._annotation_scale(rule,page)
            if scale is None:continue
            point = fitz.Point(rule['x0'] * scale, rule['y0'] * scale)
            for unit in source_units:
                if rule['expected_text'] in unit.text and (fitz.Rect(unit.locator['bbox']) + (-4,-4,4,4)).contains(point):
                    unit.text = unit.text.replace(rule['expected_text'],rule['replacement_text']); unit.text_origin = 'verified_correction'
        for rule in self.rules.get('ocr_box_exclusions',[]):
            if rule['physical_page'] != page.physical_page: continue
            scale=self._annotation_scale(rule,page)
            if scale is None:continue
            point = fitz.Point(rule['x0'] * scale,rule['y0'] * scale)
            for unit in source_units:
                if rule['expected_text'] in unit.text and (fitz.Rect(unit.locator['bbox']) + (-4,-4,4,4)).contains(point):
                    unit.attributes.update(semantic_status='pending_review',unverified_text=rule['expected_text'],review_reason=rule['reason'])
                    unit.quality='unrecognized'
        for rule in self.rules.get('extractability_decisions', []) + self.rules.get('reviewed_context_evidence', []):
            if rule['physical_page'] == page.physical_page:
                for unit in source_units:
                    expected = rule.get('source_text_sha256')
                    if expected and hashlib.sha256(unit.text.encode()).hexdigest() != expected: continue
                    if rule.get('match') == 'text_prefix' and not unit.text.startswith(rule.get('value','')): continue
                    if rule.get('match','all') not in ('all','text_prefix') and rule.get('match') not in unit.text: continue
                    unit.attributes['semantic_status'] = rule['status']; unit.attributes['review_reason'] = rule['reason']
        for rule in self.rules.get('supplemental_prose_regions',[]):
            if rule['physical_page'] != page.physical_page: continue
            for y0,y1 in rule['y_ranges']:
                rectangle=fitz.Rect(0,y0,page.width,y1)
                if any((rectangle & fitz.Rect(unit.locator['bbox'])).get_area() > .75*fitz.Rect(unit.locator['bbox']).get_area() for unit in page.sources): continue
                text,origin=self._original_region_text(page.physical_page,rectangle)
                if not text: continue
                loc={'physical_page':page.physical_page,'bbox':list(rectangle),'coordinate_system':'original_display_top_left_pdf_points','original_hash':self.original_hash}
                page.sources.append(SourceUnit(identity('paragraph',self.document_id,loc,text),'paragraph',text,loc,origin,len(page.sources),self.section,'readable',{'layout':'reviewed_supplement','semantic_status':'pending','review_reason':rule['reason']}))
        for group in self.rules.get('cross_page_units',[]):
            fragment = next((entry for entry in group['fragments'] if entry['physical_page'] == page.physical_page),None)
            if not fragment: continue
            if fragment.get('logical_page'): page.logical_page=fragment['logical_page']
            attrs = {'cross_page_group':group['group'],'cross_page_complete_pages':group['complete_pages'],'cross_page_kind':group['kind'],'semantic_status':'context_only','review_basis':'source_hash_bound_annotation'}
            if isinstance(group.get('source_total_items'),int) and not isinstance(group['source_total_items'],bool) and group['source_total_items']>=0:
                attrs['source_total_items']=group['source_total_items']
            if group['kind'] == 'ordered_table_procedure':
                for table in page.tables:
                    for cell in table.cells:
                        if not cell.text or cell.attributes.get('cell_state') == 'missing': continue
                        cellbox = fitz.Rect(cell.locator['bbox'])
                        if any((cellbox & fitz.Rect(box['bbox_pdf_pt'])).get_area() > .6 * fitz.Rect(box['bbox_pdf_pt']).get_area() for box in fragment['source_boxes']): cell.attributes.update(attrs)
            else:
                for order, box in enumerate(fragment['source_boxes']):
                    loc = {'physical_page':page.physical_page,'logical_page':fragment.get('logical_page'),'bbox':box['bbox_pdf_pt'],'coordinate_system':'original_display_top_left_pdf_points','original_hash':self.original_hash}
                    text = box['text']
                    if hashlib.sha256(text.encode()).hexdigest() != box['source_text_sha256']:
                        page.warnings.append('跨页复核片段的文本校验不一致');continue
                    source_attrs=dict(attrs)
                    if isinstance(box.get('group_order'),int) and not isinstance(box['group_order'],bool) and box['group_order']>=0:
                        source_attrs['group_order']=box['group_order']
                    matched=[unit for unit in page.sources if unit.kind in ('paragraph','clause','list_item') and unit.quality in ('readable','verified') and unit.attributes.get('semantic_status')!='pending_review' and unit.locator.get('physical_page')==page.physical_page and re.sub(r'\s','',text) in re.sub(r'\s','',unit.text)]
                    contexts=[unit.attributes.get('section_sources',[]) for unit in matched]
                    paths=[tuple(entry.get('id') for entry in context if entry.get('kind')=='heading') for context in contexts]
                    sections={unit.section for unit in matched}
                    source_section=''
                    if paths and paths[0] and all(path==paths[0] for path in paths) and len(sections)==1 and next(iter(sections)):
                        source_attrs['section_sources']=list(contexts[0])
                        source_attrs['structural_context_basis']='actual_source_text_match'
                        source_section=next(iter(sections))
                    page.sources.append(SourceUnit(identity('paragraph',self.document_id,group['group'],order,loc),'paragraph',text,loc,fragment['text_origin'],len(page.sources),source_section,'verified',source_attrs))
        for group in self.rules.get('reviewed_context_groups',[]):
            if page.physical_page not in group['physical_pages'] or len(group['physical_pages']) != 1: continue
            fragments = [{'source_order':0,'source_quote':group['source_title'],'source_label':'title'}] + group['items']
            originals=list(page.sources)
            for fragment in fragments:
                target=re.sub(r'\s','',fragment['source_quote'])
                for unit in originals:
                    positions=[index for index,char in enumerate(unit.text) if not char.isspace()]
                    normalized=''.join(unit.text[index] for index in positions)
                    start=normalized.find(target)
                    if start<0 or not target:continue
                    char_start,char_end=positions[start],positions[start+len(target)-1]+1
                    text=unit.text[char_start:char_end]
                    attrs={**unit.attributes,'parent_source_id':unit.id,'source_char_range':[char_start,char_end],'cross_page_group':group['group'],'cross_page_complete_pages':group['physical_pages'],'cross_page_kind':group['kind'],'group_order':fragment['source_order'],'source_label':fragment.get('source_label'),'source_total_items':group['source_total_items'],'semantic_status':'context_only','review_basis':'reviewed_group_and_exact_source_text'}
                    page.sources.append(SourceUnit(identity('paragraph',self.document_id,group['group'],fragment['source_order'],unit.id),'paragraph',text,dict(unit.locator),unit.text_origin,len(page.sources),unit.section,'verified',attrs));break

    @staticmethod
    def _annotation_scale(rule,page):
        coordinate_system=rule.get('coordinate_system')
        if coordinate_system == 'original_display_top_left_pdf_points':return 1
        if coordinate_system == 'original_display_ocr_pixels' and isinstance(rule.get('ocr_dpi'),(int,float)) and rule['ocr_dpi']>0:return 72/rule['ocr_dpi']
        page.warnings.append('人工标注缺少明确坐标单位或有效OCR DPI，未应用该坐标规则')
        return None

    def _original_region_text(self, number, rectangle):
        original = self.original[number-1]
        record = self.mapper.mapping.get(number-1,{})
        if len(original.get_text().strip()) < 80 and record.get('status') == 'verified_pixel_match':
            processing=self.derived[record['derived_page']-1]
            return processing.get_text('text',clip=self.mapper.processing_box(rectangle,number-1)).strip(),'ocr_text'
        return original.get_text('text',clip=fitz.Rect(rectangle)*original.derotation_matrix).strip(),'native_text'

    def _reviewed_row_boundaries(self, page):
        import hashlib
        for rule in self.rules.get('reviewed_table_text_regions',[]):
            if rule['physical_page'] != page.physical_page: continue
            if rule.get('coordinate_system') != 'original_display_top_left_pdf_points':
                page.warnings.append('复核表格行界未声明原页PDF点坐标，未应用');continue
            row_entries=[entry for entry in rule['regions'] if len(entry['rectangles']) == 1]
            if not row_entries:continue
            row_regions=[fitz.Rect([entry['rectangles'][0][key] for key in ('x0','y0','x1','y1')]) for entry in row_entries]
            verified = True
            for entry,rectangle in zip(row_entries,row_regions):
                text,_=self._original_region_text(page.physical_page,rectangle)
                if not entry.get('bound_source_text_sha256') or hashlib.sha256(text.encode()).hexdigest()!=entry['bound_source_text_sha256']:
                    verified=False;break
            if not verified:
                page.warnings.append('复核表格行界的当前源文字未通过校验，保留模型表格结构');continue
            scope = fitz.Rect(rule['table_bbox_pdf_pt']) if rule.get('table_bbox_pdf_pt') else None
            for table in page.tables:
                tablebox=fitz.Rect(table.locator['bbox'])
                if tablebox.is_empty:continue
                if scope and ((tablebox & scope).get_area() < .7*scope.get_area() or (tablebox & scope).get_area() < .7*tablebox.get_area()):continue
                if any((tablebox & rectangle).get_area() < .8*rectangle.get_area() for rectangle in row_regions):continue
                boundaries=sorted({rectangle.y0 for rectangle in row_regions if tablebox.y0<rectangle.y0<tablebox.y1})
                body=[cell for cell in table.cells if not cell.attributes.get('column_header')]
                if not body or not any(sum(fitz.Rect(cell.locator['bbox']).y0 < y < fitz.Rect(cell.locator['bbox']).y1 for y in boundaries)>1 for cell in body): continue
                positioned=[cell for cell in body if cell.text and cell.attributes.get('cell_state') != 'missing']
                if not positioned:continue
                start=min(cell.locator['bbox'][1] for cell in positioned);end=max(cell.locator['bbox'][3] for cell in positioned)
                cuts=[start]+[y for y in boundaries if start<y<end]+[end]
                headers=[cell for cell in table.cells if cell.attributes.get('column_header')]
                extents=[]
                for column in range(table.columns):
                    boxes=[fitz.Rect(cell.locator['bbox']) for cell in table.cells if cell.text and cell.attributes['column_index']==column and cell.attributes.get('column_span',1)==1]
                    extents.append((min((box.x0 for box in boxes),default=table.locator['bbox'][0]),max((box.x1 for box in boxes),default=table.locator['bbox'][2])))
                column_cuts=[table.locator['bbox'][0]]+[(extents[c][1]+extents[c+1][0])/2 for c in range(table.columns-1)]+[table.locator['bbox'][2]]
                header_rows=max((cell.attributes['row_index']+cell.attributes.get('row_span',1) for cell in headers),default=0)
                rebuilt=list(headers)
                for row,(y0,y1) in enumerate(zip(cuts,cuts[1:]),header_rows):
                    for column in range(table.columns):
                        candidates=[cell for cell in body if cell.attributes['column_index'] <= column < cell.attributes['column_index']+cell.attributes.get('column_span',1)]
                        if not candidates: continue
                        cell=candidates[0];box=fitz.Rect(column_cuts[column],y0,column_cuts[column+1],y1)
                        text,origin=self._original_region_text(page.physical_page,box)
                        attrs={**cell.attributes,'row_index':row,'column_index':column,'row_span':1,'column_span':1,'geometry':'verified_reviewed_row_boundaries','cell_state':'readable' if text else 'blank'}
                        rebuilt.append(SourceUnit(identity('cell',self.document_id,table.id,row,column,list(box),text),'cell',text,{**cell.locator,'bbox':list(box)},origin,len(rebuilt),cell.section,'readable' if text else 'blank',attrs))
                table.cells=rebuilt;table.rows=header_rows+len(cuts)-1

    def _reviewed_headers(self, number, labels):
        original = self.original[number-1]
        record = self.mapper.mapping.get(number-1,{})
        derived = len(original.get_text().strip()) < 80 and record.get('status') == 'verified_pixel_match'
        processing = self.derived[record['derived_page']-1] if derived else original
        sources = []
        for label in labels:
            for rectangle in processing.search_for(label):
                loc = {'physical_page':number,'bbox':self.mapper.display_box(rectangle,number-1,derived=derived),'coordinate_system':'original_display_top_left_pdf_points','original_hash':self.original_hash}
                sources.append(SourceUnit(identity('table-header',self.document_id,loc,label),'paragraph',label,loc,'ocr_text' if derived else 'native_text',len(sources),'','verified',{'semantic_status':'context_only','source_role':'table_header','review_basis':'verified_continuation_and_source_text'}))
        return sources

    def _picture(self, page, box, title, link):
        if link.get('method') == 'layout':
            reviewed=next((region for region in self.rules.get('pictures',[]) if region['physical_page']==page.physical_page and (fitz.Rect(box)&fitz.Rect(region['figure_bbox_pdf_pt'])).get_area()>.8*min(fitz.Rect(box).get_area(),fitz.Rect(region['figure_bbox_pdf_pt']).get_area())),None)
            if reviewed:
                box=reviewed['figure_bbox_pdf_pt'];title=reviewed.get('caption',{}).get('text',title)
                link={'method':'reviewed_geometry','layout_reference':link.get('reference'),'figure_label':reviewed.get('figure_label'),'caption_locator':reviewed.get('caption',{})}
        duplicate = next((picture for picture in page.pictures if (fitz.Rect(picture.locator['bbox']) & fitz.Rect(box)).get_area() > .8 * min(fitz.Rect(picture.locator['bbox']).get_area(),fitz.Rect(box).get_area())),None)
        if duplicate:
            if link.get('method') == 'reviewed_geometry':
                if max(abs(a-b) for a,b in zip(duplicate.locator['bbox'],box)) < .05:
                    duplicate.title = title or duplicate.title;duplicate.links.append(link);return
                page.pictures.remove(duplicate)
            else: return
        source = self.original[page.physical_page - 1]
        box = list(fitz.Rect(box) & source.rect)
        if fitz.Rect(box).is_empty: page.warnings.append('图片区域位于原页边界外'); return
        loc = {'physical_page':page.physical_page,'bbox':box,'coordinate_system':'original_display_top_left_pdf_points','original_hash':self.original_hash,'pixel_transform':{'scale':2,'crop_display_bbox':box,'original_rotation':source.rotation}}
        pid = identity('picture',self.document_id,loc)
        root = PROJECT_ROOT / 'var/derived/images' / self.original_hash
        root.mkdir(parents=True,exist_ok=True)
        pix = source.get_pixmap(matrix=fitz.Matrix(2,2),clip=fitz.Rect(box),alpha=False)
        path = root / (pid + '.png')
        pix.save(path)
        details = []
        if pix.width > 1400 or pix.height > 1400:
            rect = fitz.Rect(box)
            for index, crop in enumerate((fitz.Rect(rect.x0,rect.y0,rect.x1,(rect.y0+rect.y1)/2),fitz.Rect(rect.x0,(rect.y0+rect.y1)/2,rect.x1,rect.y1))):
                detail = source.get_pixmap(matrix=fitz.Matrix(3,3),clip=crop,alpha=False)
                target = root / f'{pid}-detail-{index}.png'; detail.save(target)
                details.append({'path':str(target),'bbox':list(crop),'content_hash':file_hash(target),'scale':3,'width':detail.width,'height':detail.height,'coordinate_system':'original_display_top_left_pdf_points','original_rotation':source.rotation})
        page.pictures.append(Picture(pid,str(path),file_hash(path),pix.width,pix.height,loc,title,self.section,[link],details))
