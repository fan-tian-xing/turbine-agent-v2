"""Neo4j is the durable store; all queries select the current document data."""
from __future__ import annotations

import json
import queue
import re
import threading
from time import monotonic
from dataclasses import asdict

from neo4j import GraphDatabase, Query

from turbine_kg.runtime import Budget, Snapshot, identity, json_text
from turbine_kg.settings import Settings

def unpack(properties: dict) -> dict:
    return {**json.loads(properties.get("data_json", "{}")), **{k: v for k, v in properties.items() if k != "data_json"}}


class KnowledgeStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password), connection_timeout=5, max_transaction_retry_time=0, notifications_min_severity='OFF')

    def close(self):
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def query(self, text: str, parameters: dict | None = None, budget: Budget | None = None) -> list[dict]:
        timeout = min(30, budget.remaining()) if budget else 60
        def execute():
            started=monotonic()
            try:
                with self.driver.session(database=self.settings.neo4j_database) as session:
                    return session.run(Query(text, timeout=timeout), parameters or {}).data()
            finally:
                if budget:
                    budget.database_seconds=getattr(budget,'database_seconds',0.0)+monotonic()-started
                    budget.database_calls=getattr(budget,'database_calls',0)+1
        if budget:
            result=queue.Queue(maxsize=1)
            def work():
                try: result.put((True,execute()))
                except Exception as error: result.put((False,error))
            worker=threading.Thread(target=work,daemon=True,name='turbine-query')
            worker.start();worker.join(budget.remaining())
            if worker.is_alive():
                from turbine_kg.runtime import BudgetExceeded
                raise BudgetExceeded('数据库查询超过本次剩余时间，晚到结果丢弃')
            ok,records=result.get_nowait()
            if not ok: raise records
        else: records=execute()
        if budget:
            budget.remaining()  # Discard any response that arrives after the deadline.
        return records

    def initialize(self):
        self.query("CREATE CONSTRAINT turbine_knowledge_id IF NOT EXISTS FOR (n:TK) REQUIRE n.id IS UNIQUE")
        self.query("CREATE INDEX turbine_document IF NOT EXISTS FOR (n:TK) ON (n.document_id)")
        self.query("CREATE INDEX turbine_label IF NOT EXISTS FOR (n:TK) ON (n.label)")
        self.query("CREATE INDEX turbine_class_id IF NOT EXISTS FOR (n:TK) ON (n.class_id)")
        self.initialize_evidence_search()

    def initialize_evidence_search(self):
        from turbine_kg.retrieval.search import SEARCH_INDEX
        self.query(f"CREATE FULLTEXT INDEX {SEARCH_INDEX} IF NOT EXISTS "
                   "FOR (e:Evidence) ON EACH [e.search_tokens] "
                   "OPTIONS {indexConfig: {`fulltext.analyzer`: 'whitespace', "
                   "`fulltext.eventually_consistent`: false}}")

    def sync_ontology(self, registry) -> dict:
        """Replace subclass assertions from the current reviewed ontology.

        Rebuild the complete axiom set before removing old edge identities;
        otherwise an axiom present only in an older persisted batch is lost.
        No Statement, Evidence, or source binding is changed.
        """
        from turbine_kg.knowledge.ingest import _write_transaction
        classes=[registry.domain_class_by_id(key) for key in
                 ['Thing',*registry.business_classes,*registry.domain_classes]]
        if any(row is None for row in classes):raise ValueError('本体类定义不完整')
        edges=[]
        for row in classes:
            for parent_id in row.get('parent_class_ids',[row.get('parent_class_id')]):
                if not parent_id:continue
                parent=registry.domain_class_by_id(parent_id)
                if parent is None:raise ValueError('本体父类定义缺失')
                edges.append({'source':row['id'],'target':parent['id'],
                    'key':identity('subclass',row['id'],parent['id']),
                    'data':{'relation_id':'subclass_of','ontology_version':registry.version,
                            'definition_sources':row.get('definition_sources',[]),'inherit_values':False}})
        with _write_transaction(self) as store:
            store.put('OntologyClass',[{**row,'class':'OntologyClass'} for row in classes],replace=True)
            store.edges('SUBCLASS_OF',edges)
            store.query('MATCH (a:TK:OntologyClass)-[r:SUBCLASS_OF]->(b:TK:OntologyClass) '
                        'WHERE NOT any(row IN $keep WHERE a.id=row.source AND b.id=row.target AND coalesce(r.key,"")=row.key) DELETE r',
                        {'keep':[{'source':row['source'],'target':row['target'],'key':row['key']} for row in edges]})
        return {'classes':len(classes),'subclass_edges':len(edges)}

    def rebuild_evidence_text(self, document_id: str, evidence_ids: list[str] | None = None) -> dict:
        """Explicit maintenance for one document, never a query-time write.

        Reassemble from Neo4j members; do not reopen source files or extract
        Statements. Existing knowledge content stays untouched.
        """
        from turbine_kg.evidence.context import ContextAssembler
        from turbine_kg.knowledge.incremental import content_fingerprint
        from turbine_kg.retrieval.search import SEARCH_VERSION
        if not document_id:
            raise ValueError('必须指定需要重建Evidence正文的资料标识')
        documents = self.query('MATCH (d:TK:Document {id:$id}) RETURN properties(d) AS node', {'id':document_id})
        if not documents:
            raise ValueError('资料不存在')
        snapshot = Snapshot((document_id,), unpack(documents[0]['node']).get('ontology_version', ''), ())
        assembler = ContextAssembler(self)
        if evidence_ids is not None and (not evidence_ids or len(set(evidence_ids)) != len(evidence_ids)):
            raise ValueError('定向刷新须提供非空且不重复的Evidence ID')
        rows = self.query('MATCH (e:TK:Evidence {document_id:$document}) WHERE '
                          + ('e.id IN $ids ' if evidence_ids is not None else 'true ')
                          + 'RETURN properties(e) AS node ORDER BY e.id',
                          {'document':document_id, **({'ids':evidence_ids} if evidence_ids is not None else {})})
        if evidence_ids is not None and len(rows)!=len(evidence_ids):
            raise ValueError('定向刷新包含不属于本资料的Evidence')
        count = 0
        for record in rows:
            evidence = unpack(record['node'])
            view = assembler.read(evidence['id'], snapshot)
            evidence.pop('search_text', None)
            evidence['text'] = view['text']
            evidence['content_hash'] = content_fingerprint(evidence)
            self.put('Evidence', [evidence], replace=True)
            count += 1
        self.initialize_evidence_search()
        return {'document_id':document_id,'updated':count,'search_version':SEARCH_VERSION}

    def backfill_document_relationships(self, document_id: str) -> dict:
        """Repair ownership metadata without re-extracting any business fact."""
        if not self.query('MATCH (d:TK:Document {id:$id}) RETURN d.id AS id',{'id':document_id}):
            raise ValueError('资料不存在')
        statements={row['id'] for row in self.query(
            'MATCH (s:TK:Statement {document_id:$document}) RETURN s.id AS id',{'document':document_id})}
        rows=self.query('MATCH (a:TK)-[r]->(b:TK) WHERE type(r)<>"SUBCLASS_OF" '
                        'RETURN elementId(r) AS id,a.document_id AS source_document,b.document_id AS target_document,properties(r) AS data')
        repairs=[]
        for row in rows:
            properties=row['data'];data=json.loads(properties.get('data_json') or '{}')
            owners={value for value in (row['source_document'],row['target_document'],
                                       properties.get('document_id'),data.get('document_id')) if value}
            if data.get('statement_id') in statements:owners.add(document_id)
            if document_id not in owners:continue
            if owners!={document_id}:raise ValueError('关系两端资料归属冲突，不能自动修复')
            if properties.get('document_id')==document_id and data.get('document_id')==document_id:continue
            repairs.append({'id':row['id'],'data':json_text({**data,'document_id':document_id})})
        if repairs:
            self.query('UNWIND $rows AS row MATCH ()-[r]->() WHERE elementId(r)=row.id '
                       'SET r.document_id=$document,r.data_json=row.data',{'document':document_id,'rows':repairs})
        return {'document_id':document_id,'relationships_updated':len(repairs)}

    def put(self, label: str, rows: list[dict], *, replace: bool = False):
        allowed = {"Document", "Revision", "Page", "SourceText", "Table", "Row", "Column", "Cell", "TableNote", "Picture", "Evidence", "Statement", "OntologyClass", "PhysicalObject", "Equipment", "Component", "System", "Tool", "Activity", "Registry", "ScopeDeclaration"}
        if label not in allowed:
            raise ValueError("Unknown knowledge storage label")
        if not rows:
            return
        projected = []
        for original in rows:
            row = original
            if label == 'Evidence' and 'search_text' in row:
                if 'text' not in row:
                    raise ValueError('Evidence正文必须使用text字段')
                row = {key:value for key,value in row.items() if key != 'search_text'}
            if label == 'Evidence' and 'text' in row:
                from turbine_kg.retrieval.search import SEARCH_VERSION, search_tokens
                row = {**row, 'search_tokens':' '.join(search_tokens(row['text'])),
                       'search_projection_version':SEARCH_VERSION}
            simple = {k: row[k] for k in ("id", "document_id", "revision_id", "text", "kind", "physical_page", "status", "ontology_version", "class_id", "parent_class_id", "row_index", "column_index", "table_id", "table_number", "evidence_id", "label", "name", "logical_key", "content_hash") if k in row}
            if label == 'Evidence':
                simple.update({key:row[key] for key in ('search_tokens','search_projection_version') if key in row})
            if label in ('OntologyClass','PhysicalObject','Equipment','Component','System','Tool','Activity','Statement'):
                simple.setdefault('name',row.get('label') or row.get('text',''))
            projected.append({**simple, "data_json": json_text({k: v for k, v in row.items() if k not in simple})})
        assignment = 'n = row' if replace else 'n += row'
        cleanup = ' REMOVE n.search_text' if label == 'Evidence' and not replace else ''
        self.query(f"UNWIND $rows AS row MERGE (n:TK:{label} {{id:row.id}}) SET {assignment}{cleanup}", {"rows": projected})

    def sync_document(self, document_id: str, nodes: dict[str, list[dict]],
                      relationships: list[dict] | None = None, *,
                      delete_missing: bool = True) -> dict:
        """Synchronize supplied document-owned labels in one transaction.

        With delete_missing, each supplied label is a complete current set for
        this document. Omitted labels are untouched. Relationships=None leaves
        edges untouched; a list declares the complete document-owned edge set.
        For a few explicit node edits use delete_missing=False. Shared ontology
        nodes and their subclass definitions are outside this operation.
        """
        from turbine_kg.knowledge.ingest import _write_transaction
        with _write_transaction(self) as store:
            return store._sync_document(document_id, nodes, relationships,
                                        delete_missing=delete_missing)

    def _sync_document(self, document_id, nodes, relationships, *, delete_missing):
        from copy import deepcopy
        from turbine_kg.knowledge.incremental import node_logical_key, content_fingerprint, evidence_logical_key
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError('增量同步必须指定资料标识')
        allowed = {"Revision", "Page", "SourceText", "Table", "Row", "Column",
                   "Cell", "TableNote", "Picture", "Evidence", "Statement", "ScopeDeclaration"}
        if set(nodes or {}) - allowed:
            raise ValueError('增量同步仅接受指定资料拥有的内容节点')
        result = {'document_id':document_id, 'created':0, 'updated':0,
                  'unchanged':0, 'deleted':0, 'relationships_created_or_updated':0,
                  'relationships_deleted':0}
        existing = {}
        prepared = {}
        remap = {}
        evidence_keys = {row['id']:row.get('logical_key') or evidence_logical_key(row)
                         for row in (nodes or {}).get('Evidence',[]) if row.get('id')}
        if 'Statement' in (nodes or {}):
            for record in self.query('MATCH (n:TK:Evidence {document_id:$document}) RETURN properties(n) AS node',
                                     {'document':document_id}):
                row=unpack(record['node'])
                evidence_keys.setdefault(row['id'],row.get('logical_key') or evidence_logical_key(row))
        # Determine every identity before replacing embedded support/reference
        # IDs or relationship endpoints. A same-ID explicit edit has priority.
        for label, rows in (nodes or {}).items():
            if not isinstance(rows, list):
                raise ValueError(f'{label} 增量数据必须为数组')
            before = [unpack(row['node']) for row in self.query(
                f'MATCH (n:TK:{label} {{document_id:$document}}) RETURN properties(n) AS node',
                {'document':document_id})]
            existing[label] = before
            by_id = {row['id']:row for row in before}
            by_key = {}
            for row in before:
                by_key.setdefault(node_logical_key(label,row,evidence_keys), []).append(row)
            keys = set(); ids = set(); after = []
            for original in rows:
                if not isinstance(original,dict) or original.get('document_id') != document_id:
                    raise ValueError(f'{label} 节点缺少一致的document_id')
                row = deepcopy(original)
                key = node_logical_key(label,row,evidence_keys)
                if key in keys:
                    raise ValueError(f'{label} 增量数据存在重复logical_key')
                keys.add(key)
                previous = by_id.get(row.get('id'))
                if previous is None:
                    matches = by_key.get(key, [])
                    if len(matches)>1:
                        raise ValueError(f'{label} 旧数据稳定标识不唯一，须先核对重复记录')
                    previous = matches[0] if matches else None
                node_id = previous['id'] if previous else row.get('id') or identity('node',label,document_id,key)
                if node_id in ids:
                    raise ValueError(f'{label} 多条输入指向同一节点')
                ids.add(node_id)
                if row.get('id'):
                    remap[row['id']] = node_id
                row.update(id=node_id,logical_key=key)
                after.append((row,previous))
            prepared[label] = after

        def mapped(value):
            if isinstance(value,dict): return {key:mapped(item) for key,item in value.items()}
            if isinstance(value,list): return [mapped(item) for item in value]
            if isinstance(value,str): return remap.get(value,value)
            return value

        edge_rows = []
        edge_keys = set()
        for original in relationships or []:
            edge = mapped(deepcopy(original)); kind = edge.get('kind','')
            if not re.fullmatch(r'[A-Z][A-Z0-9_]*',kind) or kind=='SUBCLASS_OF':
                raise ValueError('增量关系类型不允许：'+str(kind))
            if not edge.get('source') or not edge.get('target'):
                raise ValueError('增量关系缺少source或target')
            data = edge.get('data') or {}
            if edge.get('document_id',document_id)!=document_id or data.get('document_id',document_id)!=document_id:
                raise ValueError('增量关系资料归属不一致')
            data = {**data,'document_id':document_id}
            # Preserve the standard edge identity used by edges(), so later
            # semantic persistence cannot create a second parallel identity.
            key = edge.get('key') or identity('edge',kind,edge['source'],edge['target'])
            if (kind,key) in edge_keys:
                raise ValueError('增量关系标识重复')
            edge_keys.add((kind,key))
            edge_rows.append({**edge,'key':key,'data':data})

        for label, rows in prepared.items():
            written=[]
            for row,previous in rows:
                row=mapped(row); row['content_hash']=content_fingerprint(row)
                if previous is None:
                    result['created']+=1; written.append(row)
                elif content_fingerprint(previous)!=row['content_hash'] or previous.get('logical_key')!=row['logical_key'] or previous.get('content_hash')!=row['content_hash']:
                    result['updated']+=1; written.append(row)
                else: result['unchanged']+=1
            if written:self.put(label,written,replace=True)

        if relationships is not None:
            before=self.query('MATCH (a:TK)-[r]->(b:TK) WHERE r.document_id=$document '
                              'RETURN type(r) AS kind,r.key AS key,a.id AS source,b.id AS target,properties(r) AS data',
                              {'document':document_id})
            for kind in dict.fromkeys(row['kind'] for row in edge_rows):
                self.edges(kind,[row for row in edge_rows if row['kind']==kind])
            result['relationships_created_or_updated']=len(edge_rows)
            if delete_missing:
                keep=[{'kind':row['kind'],'key':row['key'],'source':row['source'],'target':row['target']} for row in edge_rows]
                self.query('MATCH (a:TK)-[r]->(b:TK) WHERE r.document_id=$document AND '
                           'NOT any(row IN $keep WHERE type(r)=row.kind AND coalesce(r.key,"")=row.key '
                           'AND a.id=row.source AND b.id=row.target) DELETE r',
                           {'document':document_id,'keep':keep})
                result['relationships_deleted']=sum((row['kind'],row['key']) not in edge_keys for row in before)
        if delete_missing:
            for label, rows in prepared.items():
                retained={row['id'] for row,_ in rows}
                stale=[row['id'] for row in existing[label] if row['id'] not in retained]
                if stale:
                    self.query(f'MATCH (n:TK:{label} {{document_id:$document}}) WHERE n.id IN $ids DETACH DELETE n',
                               {'document':document_id,'ids':stale})
                    result['deleted']+=len(stale)
        result['id_map']=remap
        return result

    def edges(self, kind: str, rows: list[dict]):
        structural = {"HAS_REVISION", "HAS_PAGE", "CONTAINS", "HAS_ROW", "HAS_COLUMN", "IN_ROW", "IN_COLUMN", "HAS_NOTE", "HAS_PICTURE", "SUPPORTS", "REFERENCES", "SUBCLASS_OF", "INSTANCE_OF", "MENTIONS"}
        if kind not in structural:
            from turbine_kg.ontology.registry import OntologyRegistry
            registry = OntologyRegistry()
            if any(registry.relationship_type(row.get('data', {}).get('relation_id')) != kind for row in rows):
                raise ValueError("业务边类型与本体关系标识不一致")
            if not rows:
                raise ValueError("Unknown storage relationship")
        if rows:
            clean_rows=[]
            for original in rows:
                row=original
                data=row.get('data', {})
                clean_rows.append({**row, "key": row.get("key", identity("edge", kind, row["source"], row["target"])),
                                   "data": json_text(data), "relation_id":data.get('relation_id'),
                                   "document_id":row.get('document_id') or data.get('document_id'),
                                   "logical_key":row.get('logical_key') or data.get('logical_key'),
                                   "role":data.get('role'), "ontology_version":data.get('ontology_version'),
                                   "support_evidence_ids":data.get('support_evidence_ids')})
            owner = 'row.document_id' if kind == 'SUBCLASS_OF' else 'coalesce(row.document_id,a.document_id,b.document_id)'
            self.query(f"UNWIND $rows AS row MATCH (a:TK {{id:row.source}}), (b:TK {{id:row.target}}) MERGE (a)-[r:{kind} {{key:row.key}}]->(b) SET r.data_json=row.data,r.relation_id=row.relation_id,r.document_id={owner},r.logical_key=row.logical_key,r.role=row.role,r.ontology_version=row.ontology_version,r.support_evidence_ids=row.support_evidence_ids", {"rows": clean_rows})

    def get(self, ids: list[str], documents: tuple[str, ...] | None = None, budget: Budget | None = None) -> list[dict]:
        records = self.query("MATCH (n:TK) WHERE n.id IN $ids AND ($unrestricted OR n.document_id IN $documents) RETURN properties(n) AS node", {"ids": ids, "unrestricted": documents is None, "documents": list(documents or ())}, budget)
        indexed = {row["node"]["id"]: unpack(row["node"]) for row in records}
        missing = [key for key in ids if key not in indexed]
        if missing:
            tables = self.query("MATCH (t:TK:Table) WHERE ($unrestricted OR t.document_id IN $documents) RETURN properties(t) AS node", {"unrestricted":documents is None,"documents":list(documents or ())}, budget)
            for row in tables:
                for member in unpack(row['node']).get('source_members', []):
                    if member['id'] in missing:
                        indexed[member['id']] = member
        return [indexed[key] for key in ids if key in indexed]

    def snapshot(self, ontology_version: str, skill_versions: tuple[str, ...], budget: Budget) -> Snapshot:
        rows = self.query("MATCH (d:TK:Document) RETURN d.id AS id,coalesce(d.ontology_version, $fallback) AS ontology", {'fallback': ontology_version}, budget)
        versions = {row["ontology"] for row in rows}
        if len(versions) > 1:
            raise ValueError("有效图谱同时包含多个本体版本，须按版本分别查询")
        pinned_version = next(iter(versions), ontology_version)
        if pinned_version != ontology_version:
            from turbine_kg.ontology.registry import OntologyRegistry
            OntologyRegistry.for_version(pinned_version)
        return Snapshot(tuple(sorted(row["id"] for row in rows)), pinned_version, skill_versions)

    def document_pages(self, document_id: str) -> dict[int, str]:
        return {row["page"]: row["status"] for row in self.query("MATCH (p:TK:Page {document_id:$id}) RETURN p.physical_page AS page,p.status AS status", {"id": document_id})}

    def save_page(self, page, document_id: str, revision_id: str, *, compact_tables=False):
        if page.status == 'excluded_navigation':
            return
        common = {"document_id": document_id, "revision_id": revision_id}
        from turbine_kg.knowledge.reviews import apply_formula_reviews
        from turbine_kg.settings import PROJECT_ROOT
        annotations_path=PROJECT_ROOT/'config/source_annotations.json'
        annotations=json.loads(annotations_path.read_text(encoding='utf-8')) if annotations_path.is_file() else {}
        def reviewed(row):return apply_formula_reviews(row,annotations)
        page_id = identity("page", document_id, page.physical_page)
        self.put("Page", [{**asdict(page), **common, "id": page_id, "sources": [], "tables": [], "pictures": [], "status": "writing"}])
        self.edges("HAS_PAGE", [{"source": document_id, "target": page_id}])
        source_rows = []
        for unit in page.sources + [cell for table in page.tables for cell in table.cells] + [note for table in page.tables for note in table.notes]:
            if unit in page.sources:
                source_rows.append(reviewed({**asdict(unit), **common,'physical_page':unit.locator.get('physical_page')}))
            for heading in unit.attributes.get('section_sources',[]):
                attributes=heading.get('attributes',{key:heading[key] for key in ('heading_level','scope_source','applies_to','physical_page_range','semantic_status') if key in heading})
                source_rows.append(reviewed({**heading,**common,'attributes':attributes,'kind':heading.get('kind','heading'),'text_origin':heading.get('text_origin','native_text'),'order':heading.get('order',0),'physical_page':heading['locator'].get('physical_page')}))
        self.put("SourceText", source_rows)
        self.edges("CONTAINS", [{"source": page_id, "target": unit.id} for unit in page.sources])
        for table in page.tables:
            from turbine_kg.documents.ir import parse_references
            table_number = next((ref['number'] for ref in parse_references(table.title) if ref['kind']=='table'),None)
            embedded = [reviewed({**asdict(unit), **unit.attributes, **common, 'table_id':table.id}) for unit in [*table.cells,*table.notes]] if compact_tables else []
            self.put("Table", [{**asdict(table), **common, "cells": [], "notes": [], "kind": "table", 'table_number':table_number, 'source_members':embedded}])
            self.edges("CONTAINS", [{"source": page_id, "target": table.id}])
            if compact_tables:
                continue
            rows = [{"id": identity("row", table.id, index), "table_id": table.id, "row_index": index, **common} for index in range(table.rows)]
            columns = [{"id": identity("column", table.id, index), "table_id": table.id, "column_index": index, **common} for index in range(table.columns)]
            self.put("Row", rows); self.put("Column", columns)
            self.edges("HAS_ROW", [{"source": table.id, "target": row["id"]} for row in rows])
            self.edges("HAS_COLUMN", [{"source": table.id, "target": column["id"]} for column in columns])
            self.put("Cell", [reviewed({**asdict(cell), **cell.attributes, **common, "table_id": table.id}) for cell in table.cells])
            self.put("TableNote", [reviewed({**asdict(note), **common, "table_id": table.id}) for note in table.notes])
            self.edges("CONTAINS", [{"source": table.id, "target": cell.id} for cell in table.cells])
            self.edges("HAS_NOTE", [{"source": table.id, "target": note.id} for note in table.notes])
            self.edges("IN_ROW", [{"source": cell.id, "target": rows[index]["id"]} for cell in table.cells for index in range(cell.attributes["row_index"], cell.attributes["row_index"] + cell.attributes["row_span"])])
            self.edges("IN_COLUMN", [{"source": cell.id, "target": columns[index]["id"]} for cell in table.cells for index in range(cell.attributes["column_index"], cell.attributes["column_index"] + cell.attributes["column_span"])])
        self.put("Picture", [{**asdict(picture), **common, "kind": "picture"} for picture in page.pictures])
        self.edges("HAS_PICTURE", [{"source": page_id, "target": picture.id} for picture in page.pictures])
        self.query("MATCH (p:TK:Page {id:$id}) SET p.status=$status", {"id": page_id, "status": page.status})

    def table_sources(self, table_id: str, documents: tuple[str, ...], budget: Budget | None = None) -> list[dict]:
        tables=self.get([table_id],documents,budget)
        if tables and tables[0].get('source_members'):
            return tables[0]['source_members']
        rows = self.query("MATCH (t:TK:Table {id:$id})-[:CONTAINS|HAS_NOTE]->(n:TK) WHERE t.document_id IN $documents RETURN properties(n) AS node ORDER BY n.row_index,n.column_index,n.id", {"id": table_id, "documents": list(documents)}, budget)
        return [unpack(row["node"]) for row in rows]

    def scope_sources(self, documents: tuple[str, ...], budget: Budget | None = None) -> list[dict]:
        """Sources of this pinned scope, including its not-yet-extracted clauses."""
        rows = self.query("MATCH (n:TK:SourceText) WHERE n.document_id IN $documents RETURN properties(n) AS node ORDER BY n.physical_page,n.id", {"documents":list(documents)}, budget)
        return [unpack(row['node']) for row in rows]
