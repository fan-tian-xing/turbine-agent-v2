"""Paged retrieval from a pinned Neo4j snapshot; no source-file reads."""
import json
import re
from turbine_kg.evidence.context import ContextAssembler
from turbine_kg.knowledge.store import unpack
from turbine_kg.runtime import fingerprint,BudgetExceeded,json_text,identity


class Tools:
    def __init__(self,store,registry,snapshot,budget):
        self.store,self.registry,self.snapshot,self.budget=store,registry,snapshot,budget
        self.assembler=ContextAssembler(store)
        self.reads={};self.visuals={};self.summary=[]
        self.text_used=0
        self.reference_gaps=[];self.diagnostics=[];self._references_seen=set();self._scope_records=None
        self._source_indexes={}
        self._reference_edges={}
        self.focus_tasks=[]
        self.pending_reference_ids=set()
        self.pending_reference_order=[]
        self.required_reference_ids=set()
        self.reference_targets={}
        self.reference_target_links=[]
        self.ontology_paths={}
        self.scope_declarations={}
        self.round_call_ceiling=budget.max_rounds*budget.calls_per_round

    def _take(self,*,image=False):
        if self.budget.calls>=self.round_call_ceiling: raise BudgetExceeded('本轮工具调用预算已用尽')
        self.budget.take(image=image)

    def _cursor(self,cursor,key):
        if not cursor: return 0
        if cursor.get('key')!=key or not isinstance(cursor.get('offset'),int) or cursor['offset']<0: raise ValueError('分页游标不属于当前查询或版本')
        return cursor['offset']

    def _ontology_objects(self,object_label):
        """Resolve an exact domain name and gather bounded, versioned paths.

        A related class or component is a recall candidate, never an inherited
        obligation. The returned path stays available to the answer verifier.
        """
        if object_label in self.ontology_paths:return self.ontology_paths[object_label]
        objects={}
        resolver=getattr(self.registry,'domain_class',None)
        domain=resolver(object_label) if callable(resolver) else None
        if domain is None:
            qualified=getattr(self.registry,'qualified_class',None)
            domain=qualified(object_label) if callable(qualified) else None
        if domain is None:
            # Source-specific mappings are not global ontology aliases. They
            # can nevertheless identify a class when the current documents
            # agree on this exact raw mention and its bound source members.
            from turbine_kg.ontology.registry import normalized
            rows=self.store.query('MATCH (s:TK:Statement) WHERE s.document_id IN $documents '
                'AND s.ontology_version=$version AND s.data_json CONTAINS $label '
                'RETURN properties(s) AS node',
                {'documents':list(self.snapshot.document_ids),'version':self.snapshot.ontology_version,
                 'label':object_label},self.budget)
            classes=set()
            for row in rows:
                if not row.get('node'):continue
                statement=unpack(row['node'])
                supported={(support['evidence_id'],key) for support in statement.get('supports',[])
                           for key in support['member_keys']}
                for entity in statement.get('entities',[]):
                    if normalized(entity.get('surface_label') or entity.get('label',''))!=normalized(object_label):continue
                    binding=entity.get('ontology_binding') or {}
                    members=binding.get('source_members',[])
                    if (members and entity.get('ontology_class_id')==binding.get('class_id') and
                        all((member.get('evidence_id'),member.get('member_key')) in supported for member in members)):
                        classes.add(binding['class_id'])
            if len(classes)==1:
                domain=self.registry.domain_class_by_id(next(iter(classes)))
        if domain is None:return objects
        class_id=domain.get('class_id',domain.get('id'))
        if not class_id:return objects
        root_id=identity('ontology-class',class_id)
        objects[root_id]={'kind':'direct_class','path':[],'class_id':class_id}
        params={'id':root_id,'documents':list(self.snapshot.document_ids),
                'ontology_version':self.snapshot.ontology_version,'limit':max(16,self.budget.statement_page*2)}
        depth=min(self.budget.business_hops,3)
        directions=(
            ('ancestor',f'MATCH path=(root:TK:OntologyClass {{id:$id}})-[:SUBCLASS_OF*1..{depth}]->(related:TK:OntologyClass)'),
            ('descendant',f'MATCH path=(related:TK:OntologyClass)-[:SUBCLASS_OF*1..{depth}]->(root:TK:OntologyClass {{id:$id}})'),
        )
        for kind,pattern in directions:
            rows=self.store.query(pattern+' WHERE all(r IN relationships(path) WHERE '
                'r.ontology_version=$ontology_version) '
                'RETURN related.id AS id,related.class_id AS class_id,'
                '[r IN relationships(path) | properties(r)] AS semantic_edges '
                'ORDER BY length(path),related.id LIMIT $limit',params,self.budget)
            for row in rows:
                if row.get('id') and row['id'] not in objects:
                    objects[row['id']]={'kind':kind,'path':row.get('semantic_edges',[]),'class_id':row.get('class_id')}
        # A reviewed class axiom belongs to the pinned registry even when a
        # source document never mentions the child in an extracted Statement.
        # This recalls the parent's Statement; applicability is checked later.
        parent_lookup=getattr(self.registry,'domain_class_by_id',None)
        pending=[(domain,[])]; visited={domain['id']}
        for current,chain in pending:
            if len(chain)>=depth:continue
            for parent_id in current.get('parent_class_ids', [current.get('parent_class_id',current.get('parent'))]):
                parent=parent_lookup(parent_id) if callable(parent_lookup) and parent_id else None
                if not parent or parent['id'] in visited:continue
                visited.add(parent['id'])
                provenance={**current,**current.get('parent_provenance',{}).get(parent_id,{})}
                path=chain+[{'relation_id':'subclass_of','basis':'registry_axiom',
                            'source_class_id':current['class_id'],'target_class_id':parent['class_id'],
                            'ontology_version':self.snapshot.ontology_version,
                            'source_clause':provenance.get('source_clause'),
                            'definition_sources':provenance.get('definition_sources',[])}]
                objects.setdefault(parent['id'],{'kind':'ancestor','class_id':parent['class_id'],
                    'path':path})
                pending.append((parent,path))
        # Composition is a separate, evidence-bearing association. It never
        # turns a whole's dimensions or conditions into a part's properties.
        rows=self.store.query(
            'MATCH (root:TK {id:$id})-[r:PART_OF]-(related:TK) '
            'WHERE r.document_id IN $documents AND related:OntologyClass '
            'AND r.ontology_version=$ontology_version '
            'RETURN related.id AS id,related.class_id AS class_id,'
            'startNode(r).id AS source_id,properties(r) AS relation '
            'ORDER BY related.id LIMIT $limit',params,self.budget)
        for row in rows:
            if row.get('id') and row['id'] not in objects:
                objects[row['id']]={'kind':'part_of' if row.get('source_id')==root_id else 'has_part',
                                    'path':[row.get('relation',{})],'class_id':row.get('class_id')}
        return objects

    def _reference_priority(self,matches):
        """Rank cited targets through their Statements and object paths only."""
        object_paths={oid:path for paths in self.ontology_paths.values() for oid,path in paths.items()}
        if not object_paths:return {}
        try:
            rows=self.store.query(
                'MATCH (s:TK:Statement)-[:SUPPORTS]->(e:TK:Evidence) '
                'WHERE e.id IN $ids AND s.document_id IN $documents '
                'AND e.document_id=s.document_id '
                'OPTIONAL MATCH (c:TK)-[r]->(s) '
                'WHERE c.id IN $objects AND coalesce(r.document_id,s.document_id)=s.document_id '
                'AND r.relation_id IN $relations AND coalesce(r.role,\'\') <> \'related\' '
                'RETURN e.id AS evidence_id,collect(DISTINCT s.text) AS statements,'
                'collect(DISTINCT c.id) AS matched_object_ids',
                {'ids':[item['id'] for item in matches],
                 'documents':list(self.snapshot.document_ids),
                 'objects':list(object_paths),
                 'relations':list(dict.fromkeys(relation for task in self.focus_tasks
                                                for relation in task.get('relation_ids',[])))},self.budget)
        except Exception as error:
            self.diagnostics.append({'kind':'reference_graph_priority_unavailable',
                                     'reason':type(error).__name__})
            return {}
        weight={'direct':5,'direct_class':5,'ancestor':4,'descendant':2,
                'part_of':1,'has_part':1}
        terms=list(dict.fromkeys(str(term).casefold() for task in self.focus_tasks
                                  for term in task.get('query_terms',[]) if isinstance(term,str) and len(term.strip())>1))
        ranked={}
        for row in rows:
            if not isinstance(row,dict) or not row.get('evidence_id'):continue
            statements=[value for value in row.get('statements',[]) if isinstance(value,str)]
            body='\n'.join(statements).casefold()
            ranked[row['evidence_id']]={'score':max((weight.get(object_paths[oid]['kind'],0)
                for oid in row.get('matched_object_ids',[]) if oid in object_paths),default=0),
                'condition_hits':sum(term in body for term in terms),
                'statements':statements[:3]}
        return ranked

    def navigate(self,object_label:str,relation_ids:list[str],cursor=None,hops=1):
        self._take()
        if not isinstance(object_label,str) or not object_label.strip():raise ValueError('查询对象须为非空名称')
        if not isinstance(relation_ids,list) or not relation_ids or any(not isinstance(key,str) for key in relation_ids):
            raise ValueError('必须明确至少一个本体关系标识')
        if type(hops) is not int or hops<1 or hops>self.budget.business_hops: raise ValueError('业务跳数超出预算')
        if any(key not in self.registry.relations for key in relation_ids): raise ValueError('未知本体关系标识')
        if any(not self.registry.relations[key].get('navigation',True) for key in relation_ids): raise ValueError('条件或来源关系不计入业务导航')
        canonical=self.registry.canonical_label(object_label)
        cid=identity('query-object',canonical)
        ontology_objects=self._ontology_objects(object_label)
        self.ontology_paths[object_label]=ontology_objects
        domain=self.registry.domain_class(object_label) or self.registry.qualified_class(object_label)
        labels=list(dict.fromkeys([object_label,canonical,*domain.get('aliases',[])] if domain else [object_label]))
        source_classes=[kind for kind in self.registry.classes if any(
            self.registry.is_a(kind,self.registry.relations[relation]['domain']) for relation in relation_ids)]
        key=fingerprint((self.snapshot.key,cid,labels,relation_ids,hops,sorted(ontology_objects)));offset=self._cursor(cursor,key)
        ranks={5:[],4:[],2:[],1:[]}
        for object_id,path in ontology_objects.items():
            rank={'direct':5,'direct_class':5,'ancestor':4,'descendant':2,
                  'part_of':1,'has_part':1}.get(path['kind'],0)
            if rank:ranks[rank].append(object_id)
        query_terms=list(dict.fromkeys(term.strip().casefold() for task in self.focus_tasks
            if task.get('object')==object_label for term in task.get('query_terms',[])
            if isinstance(term,str) and len(term.strip())>1))
        params={'ids':list(ontology_objects),'object_labels':labels,'source_classes':source_classes,
                'documents':list(self.snapshot.document_ids),'relations':relation_ids,
                'ontology_version':self.snapshot.ontology_version,
                'statement_types':list(dict.fromkeys(kind for relation in relation_ids
                    for kind in self.registry.relations[relation].get('statement_types',[]))),
                'rank_5':ranks[5],'rank_4':ranks[4],'rank_2':ranks[2],'rank_1':ranks[1],
                'query_terms':query_terms,'offset':offset,'limit':self.budget.statement_page}
        # Approved classes and actual occurrences are entries to pinned
        # Statement paths; unknown words do not become generic graph nodes.
        edge_types=[self.registry.relationship_type(relation) for relation in relation_ids]
        path_types='|'.join(dict.fromkeys(edge_types))
        query=(f"CALL {{ MATCH path=(c:TK)-[:{path_types}*1..{hops}]->(s:TK) "
            "WHERE (c.id IN $ids OR c.label IN $object_labels) AND any(kind IN labels(c) WHERE kind IN $source_classes) "
            "AND s:Statement AND all(r IN relationships(path) WHERE coalesce(r.document_id,s.document_id)=s.document_id "
            "AND r.ontology_version=$ontology_version AND r.relation_id IN $relations "
            "AND coalesce(r.role,'') <> 'related' AND type(r)=toUpper(r.relation_id)) AND s.document_id IN $documents "
            "RETURN s,c,1 AS owned_match,"
            "CASE WHEN c.id IN $rank_5 THEN 5 WHEN c.id IN $rank_4 THEN 4 WHEN c.id IN $rank_2 THEN 2 "
            "WHEN c.id IN $rank_1 THEN 1 ELSE 0 END AS path_rank "
            "UNION ALL MATCH (c:TK:OntologyClass)-[r:MENTIONS]->(s:TK:Statement) "
            "WHERE c.id IN $ids AND r.role='related' AND coalesce(r.document_id,s.document_id)=s.document_id "
            "AND s.document_id IN $documents AND r.ontology_version=$ontology_version AND s.type IN $statement_types "
            "RETURN s,c,0 AS owned_match,-1 AS path_rank } "
            "WITH s,collect(DISTINCT properties(c)) AS matched_objects,"
            "max(path_rank) AS path_rank,max(owned_match) AS owned_match ")
        total=self.store.query(query+'RETURN count(s) AS total',params,self.budget)[0]['total']
        rows=self.store.query(query+'RETURN properties(s) AS node,matched_objects,owned_match '
            'ORDER BY path_rank DESC,size([term IN $query_terms WHERE toLower(coalesce(s.text,\'\')) CONTAINS term]) DESC,s.id '
            'SKIP $offset LIMIT $limit',params,self.budget)
        items=[]
        for row in rows:
            item=unpack(row['node'])
            item['matched_objects']=[{key:source[key] for key in ('id','label','class','class_id','occurrence') if key in source}
                                     for properties in row.get('matched_objects',[]) for source in [unpack(properties)]]
            item['ontology_paths']=[{'object_id':source['id'],**ontology_objects[source['id']]}
                                    for source in item['matched_objects'] if source.get('id') in ontology_objects]
            if row.get('owned_match') == 0:
                item['retrieval_role']='related_context'
                item['retrieval_warning']='仅表示陈述提及该类别，不证明要求适用于查询对象；须阅读原文核对主体与条件'
                item['ontology_paths']=[{**path,'class_path_kind':path['kind'],'kind':'related_context'}
                                        for path in item['ontology_paths']]
            items.append(item)
        scope_rows=self._attach_scope_constraints(items)
        origins={source['id']:source for item in items for source in item['matched_objects']}
        instances=[source for source in origins.values() if self.registry.is_a(source.get('class'),['PhysicalObject','Activity'])]
        warnings=['命中多个同名真实实例；保留各自来源身份，适用对象与工况须分别核对'] if len(instances)>1 else []
        result={'items':items,'scopes':scope_rows,'completed_range':[offset,offset+len(items)],'coverage':{'matched':total,'scope':'registered_statement_navigation'},'cursor':{'key':key,'offset':offset+len(items)} if offset+len(items)<total else None,'warnings':warnings}
        self.summary.append({'tool':'navigate','object':object_label,'relation_ids':relation_ids,'matches':total,'returned':len(items),'has_more':bool(result['cursor'])})
        return result

    def _search_anchor_terms(self, query, terms):
        """Reviewed object names constrain recall; other phrases rank results.

        Activity words are not object anchors. A contextual requirement which
        omits an object's name is still discoverable through graph navigation
        and explicit references, independently of this lexical route.
        """
        from turbine_kg.ontology.registry import normalized
        text=normalized(' '.join([query,*terms]))
        task_objects=list(dict.fromkeys(task['object'] for task in self.focus_tasks
                    if isinstance(task.get('object'),str) and task['object'].strip()))
        def object_key(label):
            domain=self.registry.domain_class(label) if self.registry is not None else None
            return domain['class_id'] if domain else normalized(label)
        # Each task's initial lookup puts its object first in query_terms. The
        # shared question can name other tasks' objects; it is not their scope.
        explicit=[label for term in terms for label in task_objects if object_key(term)==object_key(label)]
        labels=explicit[:1] if explicit else [label for label in task_objects if normalized(label) in normalized(query)]
        registry=self.registry
        if registry is not None and not labels:
            candidates=[]
            for class_id,row in {**registry.business_classes,**registry.domain_classes}.items():
                if not registry.domain_is_a(class_id,'PhysicalObject'):continue
                for label in [row.get('label',row.get('zh','')),*row.get('aliases',[])]:
                    if len(normalized(label))>=2 and normalized(label) in text:candidates.append(label)
            # Prefer the most specific full name over its embedded name;
            # separate explicitly named objects remain separate anchors.
            labels.extend(label for label in candidates if not any(
                normalized(label)!=normalized(other) and normalized(label) in normalized(other)
                for other in candidates))
        anchors=[]
        for label in dict.fromkeys(labels):
            domain=registry.domain_class(label) if registry is not None else None
            if domain:anchors.extend([domain['label'],*domain.get('aliases',[])])
        return list(dict.fromkeys(anchors))

    def search_evidence(self,query:str,query_terms=None,cursor=None):
        """Recall source text independently of successful Statement extraction.

        Return source IDs and their linked Statements. The caller must use
        read_evidence before answering: Evidence.text is assembled for retrieval
        and reading, while member quotes preserve source-bound quotations.
        Snapshot filtering precedes paging.
        """
        from turbine_kg.retrieval.search import SEARCH_INDEX, SEARCH_VERSION, query_projection
        self._take()
        _,terms=query_projection(query,query_terms)
        anchors=self._search_anchor_terms(query,terms)
        lucene,_=query_projection(query,anchors or terms,match='any' if anchors else 'all')
        match_mode='object_anchored' if anchors else 'all_phrases'
        params={'index':SEARCH_INDEX,'query':lucene,'terms':list(dict.fromkeys([*anchors,*terms])),
                'documents':list(self.snapshot.document_ids),
                'limit':self.budget.statement_page,'search_version':SEARCH_VERSION}
        base=('CALL db.index.fulltext.queryNodes($index,$query) YIELD node AS e,score '
              'WHERE e:TK AND e:Evidence AND e.document_id IN $documents '
              'AND e.search_projection_version=$search_version ')
        try:
            total=self.store.query(base+'RETURN count(e) AS total',params,self.budget)[0]['total']
            if not total and not anchors and len(terms)>1:
                # Unknown objects have no reviewed anchor. Relax whole phrases
                # only after the conjunction failed, and expose that decision.
                lucene,_=query_projection(query,terms,match='any')
                params['query']=lucene
                match_mode='relaxed_phrases'
                total=self.store.query(base+'RETURN count(e) AS total',params,self.budget)[0]['total']
            key=fingerprint((self.snapshot.key,'search_evidence',lucene,terms))
            offset=self._cursor(cursor,key)
            params['offset']=offset
            rows=self.store.query(base+
                'WITH e,score,size([term IN $terms WHERE '
                'toLower(coalesce(e.text,\'\')) CONTAINS toLower(term)]) AS exact_hits '
                'ORDER BY exact_hits DESC,score DESC,e.id '
                'SKIP $offset LIMIT $limit '
                'OPTIONAL MATCH (s:TK:Statement)-[:SUPPORTS]->(e) '
                'WHERE s.document_id=e.document_id AND s.document_id IN $documents '
                ''
                'RETURN properties(e) AS evidence,score,exact_hits,collect(DISTINCT properties(s)) AS statements '
                'ORDER BY exact_hits DESC,score DESC,evidence.id',
                params,self.budget)
        except Exception as error:
            # Missing indexes require an explicit maintenance action. Never
            # silently substitute an unrestricted database scan or filesystem.
            if getattr(error,'code','') in ('Neo.ClientError.Procedure.ProcedureCallFailed',
                                            'Neo.ClientError.Schema.IndexNotFound') and (
                    'no such fulltext' in str(error).lower() or 'no such index' in str(error).lower()):
                self.diagnostics.append({'kind':'evidence_search_unavailable','reason':'Evidence全文索引未建立'})
                return {'items':[],'scopes':[],'completed_range':[0,0],
                        'coverage':{'matched':0,'scope':'evidence_fulltext','status':'unavailable'},
                        'cursor':None,'warnings':['Evidence全文索引未建立，需先执行检索投影维护']}
            raise
        missing=self.store.query('MATCH (e:TK:Evidence) WHERE e.document_id IN $documents '
            'AND '
            '(e.search_tokens IS NULL OR coalesce(e.search_projection_version,\'\')<>$search_version) '
            'RETURN count(e) AS missing',params,self.budget)[0]['missing']
        if missing:
            diagnostic={'kind':'evidence_search_incomplete','unindexed':missing,
                        'reason':'当前资料中有Evidence未建立当前检索投影，全文召回覆盖不完整'}
            if diagnostic not in self.diagnostics:self.diagnostics.append(diagnostic)
        items=[]
        statements=[]
        for row in rows:
            evidence=unpack(row['evidence'])
            linked=[unpack(item) for item in row.get('statements',[]) if item]
            statements.extend(linked)
            items.append({'evidence_id':evidence['id'],'score':row['score'],
                          'retrieval_method':'evidence_fulltext','exact_hits':row.get('exact_hits',0),
                          'document_id':evidence.get('document_id'),'revision_id':evidence.get('revision_id'),
                          'physical_page':evidence.get('physical_page'),
                          'kind':evidence.get('kind'),'context':evidence.get('context',{}),
                          'semantic_coverage':evidence.get('semantic_coverage',{}),
                          'statements':linked,'requires_read':True})
        scopes=self._attach_scope_constraints(statements)
        warnings=['部分Evidence尚未建立当前检索投影，全文召回覆盖不完整'] if missing else []
        if match_mode=='relaxed_phrases':warnings.append('未识别到本体对象且组合查询无结果；已放宽为完整词组匹配，候选须进一步核对相关性')
        result={'items':items,'scopes':scopes,'completed_range':[offset,offset+len(items)],
                'coverage':{'matched':total,'scope':'evidence_fulltext','unindexed':missing,
                            'match_mode':match_mode,'object_terms':anchors},
                'cursor':{'key':key,'offset':offset+len(items)} if offset+len(items)<total else None,
                'warnings':warnings}
        self.summary.append({'tool':'search_evidence','query':query,'query_terms':terms,
                             'match_mode':match_mode,'object_terms':anchors,
                             'matches':total,'returned':len(items),'has_more':bool(result['cursor'])})
        return result

    def _attach_scope_constraints(self,items):
        """Resolve shared scope IDs once without copying declarations into nodes.

        Raw verified scope text is useful context but is not a parsed predicate.
        Its expression remains None, so consumers combine it as unknown with
        local conditions using three-valued AND; it never excludes raw evidence.
        """
        statements=[*items,*[statement for item in items for statement in item.get('supporting_statements',[])]]
        ids=list(dict.fromkeys(key for statement in statements for key in statement.get('scope_ids',[]) if isinstance(key,str)))
        uncached=[key for key in ids if key not in self.scope_declarations]
        if uncached:
            try:
                rows=self.store.get(uncached,self.snapshot.document_ids,self.budget)
                by_id={row['id']:row for row in rows if row.get('id') in uncached and row.get('document_id') in self.snapshot.document_ids}
                for key in uncached:self.scope_declarations[key]=by_id.get(key)
            except Exception:
                for key in uncached:self.scope_declarations[key]=None
        summaries={}
        for statement in statements:
            constraints=[]
            for key in dict.fromkeys(statement.get('scope_ids',[])):
                scope=self.scope_declarations.get(key)
                expression=None
                state='unknown'
                if scope and (not statement.get('document_id') or scope.get('document_id')==statement['document_id']):
                    text=scope.get('condition_text')
                    bounds=scope.get('physical_page_range')
                    valid=isinstance(text,str) and bool(text.strip()) and isinstance(bounds,list) and len(bounds)==2 and all(type(page) is int and page>0 for page in bounds) and bounds[0]<=bounds[1]
                    candidate=scope.get('expression')
                    if valid and isinstance(candidate,dict) and candidate.get('op') in ('and','or','not','lt','le','gt','ge','eq','ne') and scope.get('expression_status') not in ('pending','unknown','unverified'):
                        expression=candidate;state='parsed'
                    summaries[key]={'id':key,'condition_text':text,'scope':scope.get('scope'),
                                    'physical_page_range':bounds,'expression':expression,'status':state}
                    if not valid:
                        self.diagnostics.append({'kind':'scope_metadata_unknown','state':'unknown','statement_id':statement['id'],'scope_id':key,
                                                 'reason':'既有适用范围的原文或范围元数据不完整，适用性保持未知'})
                else:
                    self.diagnostics.append({'kind':'scope_metadata_unknown','state':'unknown','statement_id':statement['id'],'scope_id':key,
                                             'reason':'既有适用范围未能在本次固定版本中读取，适用性保持未知'})
                constraints.append({'scope_id':key,'expression':expression,'status':state})
            statement['scope_constraints']=constraints
        return list(summaries.values())

    def _scope_evidence(self,view):
        key=(view.get('document_id'),view.get('revision_id'))
        if isinstance(self._scope_records,list):
            return [row for row in self._scope_records if
                    (row.get('document_id'),row.get('revision_id'))==key]
        if self._scope_records is None:self._scope_records={}
        if key not in self._scope_records:
            rows=self.store.query(
                'MATCH (e:TK:Evidence {document_id:$document,revision_id:$revision}) '
                'RETURN properties(e) AS node',
                {'document':key[0],'revision':key[1]},self.budget)
            self._scope_records[key]=[unpack(row['node']) for row in rows]
        return self._scope_records[key]

    @staticmethod
    def _is_section_reference(ref,matches):
        if ref.get('kind')=='section':return True
        number=ref.get('number')
        if ref.get('kind')!='clause' or not isinstance(number,str) or len(matches)<2:return False
        clause_numbers=[str(item.get('context',{}).get('clause_number','')) for item in matches]
        return (any(value.startswith(number+'.') for value in clause_numbers) and
                all(value==number or value.startswith(number+'.') for value in clause_numbers))

    def _follow_references(self,view):
        """Follow source structure within the pinned scope, never business inheritance."""
        from turbine_kg.documents.ir import SourceIndex,parse_references
        resolutions=view.get('context',{}).get('reference_resolution',[])
        refs=view.get('context',{}).get('references') or resolutions or parse_references('\n'.join(m['quote'] for m in view['members'] if m.get('quote')))
        if not refs:return []
        records=[row for row in self._scope_evidence(view)
                 if row.get('document_id') == view.get('document_id') and row.get('revision_id') == view.get('revision_id')
                 ]
        by_id={row['id']:row for row in records}
        def reference_key(ref):
            return ('clause' if ref.get('kind',ref.get('reference_kind'))=='section' else ref.get('kind',ref.get('reference_kind')),
                    ref.get('number'),ref.get('end_number'))
        native_targets={}
        edge_key=(view.get('evidence_id'),view.get('document_id'))
        if edge_key not in self._reference_edges:
            edges=[]
            if callable(getattr(self.store,'query',None)):
                try:
                    edges=self.store.query('MATCH (origin:TK:Evidence {id:$id})-[r:REFERENCES]->(target:TK:Evidence) '
                        'WHERE origin.document_id=$document AND target.document_id=$document '
                        'AND coalesce(r.document_id,origin.document_id)=origin.document_id '
                        'AND target.document_id=origin.document_id AND target.revision_id=origin.revision_id '
                        'RETURN target.id AS evidence_id,properties(r) AS reference',
                        {'id':view['evidence_id'],'document':view.get('document_id')},self.budget)
                except BudgetExceeded:raise
                except Exception:
                    self.diagnostics.append({'evidence_id':view['evidence_id'],'kind':'reference_lookup_failed','state':'fallback',
                                             'reason':'原生引用关系读取未完成，改用同范围来源结构解析'})
            self._reference_edges[edge_key]=edges
        for edge in self._reference_edges[edge_key]:
            data=edge.get('reference',{})
            if data.get('data_json'):
                data={**json.loads(data['data_json']),**{key:value for key,value in data.items() if key!='data_json'}}
            if edge.get('evidence_id') in by_id and data.get('number'):
                native_targets.setdefault(reference_key(data),[]).append(edge['evidence_id'])
        for resolved in resolutions:
            if resolved.get('status')=='resolved':
                native_targets.setdefault(reference_key(resolved),[]).extend(resolved.get('target_evidence_ids',[]))
        scope_key=(view.get('document_id'),view.get('revision_id'))
        def source_index():
            if scope_key in self._source_indexes:return self._source_indexes[scope_key]
            source_ids=list(dict.fromkeys(member['source_id'] for row in records for member in row.get('members',[])))
            sources=self.store.get(source_ids,self.snapshot.document_ids,self.budget) if source_ids else []
            # Only members of the pinned document scope enter this index.
            resources={}
            for row in records:
                context=row.get('context',{})
                resource_id=context.get('table_id') if row.get('kind')=='table' else context.get('figure_id',context.get('picture_id')) if row.get('kind') in ('image','picture') else None
                if resource_id:
                    resources[resource_id]={'id':resource_id,'title':context.get('table_title',context.get('figure_title','')),
                                            'section':context.get('section',''),'locator':{'physical_page':context.get('primary_page',row.get('physical_page'))},
                                            'resource_kind':'table' if row.get('kind')=='table' else 'figure'}
            index=SourceIndex(sources,[row for row in resources.values() if row['resource_kind']=='table'],
                              [row for row in resources.values() if row['resource_kind']=='figure'])
            self._source_indexes[scope_key]=index
            return index
        queued=[]
        origin={'section':view.get('context',{}).get('section',''),
                'physical_page':view.get('context',{}).get('primary_page') or next((member.get('locator',{}).get('physical_page') for member in view['members'] if member.get('role') in ('content','value')),None)}
        for ref in refs:
            kind=ref.get('kind');number=ref.get('number');end=ref.get('end_number')
            if not number:continue
            self.budget.remaining()
            key=(view.get('evidence_id'),kind,number,end,ref.get('external_document',False))
            if key in self._references_seen:continue
            self._references_seen.add(key)
            reference={**ref,'kind':'clause' if kind=='section' else kind}
            declared=next((row for row in resolutions if reference_key(row)==reference_key(reference)),None)
            if declared and declared.get('status')=='self_reference':continue
            target_ids=list(dict.fromkeys(native_targets.get(reference_key(reference),[]))) if not ref.get('external_document') else []
            if target_ids:
                matches=[by_id[target] for target in target_ids if target in by_id]
                missing=[target for target in target_ids if target not in by_id]
                if missing:
                    self.reference_gaps.append({'origin_evidence_id':view['evidence_id'],'kind':'reference_unread','reference_number':number,
                                               'reason':f'引用{number}的已有目标不属于本次固定来源范围，暂不读取。'})
                    if not matches:continue
                resolution={'status':'resolved','candidates':[]}
            else:
                matches=[]
                resolution={'status':declared['status'],'candidates':[]} if declared and declared.get('status') in ('ambiguous','ambiguous_range','external_document','deferred_outside_scope','missing','missing_range_endpoint') else source_index().resolve(reference,origin)
            if resolution['status'] in ('ambiguous','ambiguous_range'):
                self.reference_gaps.append({'origin_evidence_id':view['evidence_id'],'kind':'reference_ambiguous','resolution_status':resolution['status'],'reference_number':number,'reason':f'引用{number}存在多个原文位置或范围不能确定，尚未确认具体目标。'})
                continue
            if resolution['status'] != 'resolved':
                self.reference_gaps.append({'origin_evidence_id':view['evidence_id'],'kind':'reference_deferred','resolution_status':resolution['status'],'reference_kind':kind,'reference_number':number,
                                           'reason':f'引用{number}属于外部资料或尚未纳入当前知识范围，本轮暂不展开；不代表原文件缺页。'})
                continue
            for item in records:
                context=item.get('context',{})
                for candidate in resolution['candidates']:
                    if candidate['kind'] in ('table','figure'):
                        resource_id=context.get('table_id') if candidate['kind']=='table' else context.get('figure_id',context.get('picture_id'))
                        hit=resource_id in candidate['resource_ids']
                    else:
                        hit=context.get('clause_number')==candidate['number'] and any(member.get('source_id') in candidate['source_ids'] for member in item.get('members',[]))
                        bounds=context.get('clause_source_range')
                        if hit and bounds and candidate.get('char_range'):
                            hit=bounds[0]==candidate['char_range'][0]
                    if hit:
                        matches.append(item);break
            if matches:
                original_context=view.get('context',{})
                table_id=original_context.get('table_id')
                picture_id=original_context.get('figure_id',original_context.get('picture_id'))
                matches=[item for item in matches if not (
                    table_id and item.get('context',{}).get('table_id')==table_id
                    or picture_id and item.get('context',{}).get('figure_id',item.get('context',{}).get('picture_id'))==picture_id)]
                # A caption or table note can repeat its own resource number.
                # Different row groups of that same resource are not references.
                if not matches:continue
            if not matches:
                self.reference_gaps.append({'origin_evidence_id':view['evidence_id'],'kind':'reference_unread','reference_number':number,'reason':f'引用{number}的来源已定位，完整证据组尚未建立。'})
                continue
            # A section reference may cover many clauses. Read in document
            # order so an arbitrary database row order cannot start with a
            # later clause and its own nested references.
            matches.sort(key=lambda item:(tuple(int(part) for part in re.findall(r'\d+',str(item.get('context',{}).get('clause_number') or item.get('context',{}).get('table_number') or ''))),
                                          item.get('physical_page') or 0,item['id']))
            section_reference=self._is_section_reference(ref,matches)
            wide_reference=section_reference or kind=='table' and len(matches)>2
            priorities=self._reference_priority(matches) if wide_reference and len(matches)>2 else {}
            positions={item['id']:index for index,item in enumerate(matches)}
            ordered=sorted(matches,key=lambda item:(-priorities.get(item['id'],{}).get('score',0),
                                                    -priorities.get(item['id'],{}).get('condition_hits',0),
                                                    positions[item['id']]))
            high=[item for item in ordered if priorities.get(item['id'],{}).get('score',0)>=4]
            initial=list(dict.fromkeys(item['id'] for item in high[:4]))
            initial.extend(item['id'] for item in ordered if len(initial)<2 and item['id'] not in initial)
            if not wide_reference:initial=[item['id'] for item in matches]
            self.pending_reference_ids.update(item['id'] for item in matches if item['id'] not in self.reads)
            self.required_reference_ids.update(item_id for item_id in initial if item_id not in self.reads)
            self.pending_reference_order.extend(item_id for item_id in initial
                                                if item_id not in self.reads and item_id not in self.pending_reference_order)
            for item in ordered:
                target={'origin_evidence_id':view['evidence_id'],
                    'reference_number':number,'reference_kind':kind,
                    'clause_number':item.get('context',{}).get('clause_number'),
                    'priority':priorities.get(item['id'],{}).get('score',0),
                    'condition_hits':priorities.get(item['id'],{}).get('condition_hits',0),
                    'statements':priorities.get(item['id'],{}).get('statements',[])}
                self.reference_targets[item['id']]=target
                link={'evidence_id':item['id'],**target}
                if link not in self.reference_target_links:self.reference_target_links.append(link)
            self.summary.append({'tool':'follow_references','origin_evidence_id':view['evidence_id'],'reference_kind':kind,
                                 'reference_number':number,'target_evidence_ids':[item['id'] for item in matches],
                                 'method':'REFERENCES' if target_ids else 'source_index'})
            queued.extend({'evidence_id':item['id'],**self.reference_targets[item['id']]}
                          for item in ordered if item['id'] not in self.reads)
        return queued

    def read_evidence(self,evidence_id:str):
        if evidence_id in self.reads:
            view=self.reads[evidence_id]
            return {'items':[view],'completed_range':[0,1],'coverage':{'source_members':len(view['members']),'read':len(view['members'])},'cursor':None,'warnings':[]}
        self._take()
        view=self.assembler.read(evidence_id,self.snapshot,self.budget)
        length=sum(len(member['quote']) for member in view['members'])
        if evidence_id not in self.reads and self.text_used+length>self.budget.text_limit: raise BudgetExceeded('完整来源成员超出剩余文本预算；未截断表头、单位或条件')
        if evidence_id not in self.reads: self.text_used+=length
        self.reads[evidence_id]=view
        self.pending_reference_ids.discard(evidence_id)
        self.required_reference_ids.discard(evidence_id)
        remaining_gaps=[]
        for gap in self.reference_gaps:
            if gap.get('kind')=='reference_unread' and gap.get('target_evidence_ids'):
                pending=[key for key in gap['target_evidence_ids'] if key not in self.reads]
                if not pending:continue
                gap={**gap,'target_evidence_ids':pending}
            remaining_gaps.append(gap)
        self.reference_gaps=remaining_gaps
        view['source_warnings']=[]
        context=view.get('context',{})
        context.pop('confirmed_semantic_reviews',None)
        if context.get('semantic_status')=='pending_review' or any(member.get('attributes',{}).get('semantic_status')=='pending_review' for member in view['members']):
            view['source_warnings'].append({'kind':'source_quality','state':'pending_review','reason':'来源结构待复核；可读原文保留，不能视为已完成工程语义核验'})
        unreadable=[member['member_key'] for member in view['members'] if member.get('quality') in ('unreadable','unrecognized','unverified_mapping','pending_review') or member.get('attributes',{}).get('cell_state') in ('unrecognized','missing')]
        if unreadable or context.get('quality') in ('unreadable','unrecognized'):
            view['source_warnings'].append({'kind':'source_quality','state':'unreadable','member_keys':unreadable,'reason':'来源存在未识别、缺失或定位未确认的成员；不能按完整可读依据处理'})
        if context.get('body_context_status')=='incomplete' or context.get('missing_context_pages'):
            view['source_warnings'].append({'kind':'source_quality','state':'context_incomplete','missing_context_pages':context.get('missing_context_pages',[]),'reason':'表格相关正文或跨页条件未完整取得，适用前提保持未知'})
        excluded=view['kind']=='image' or context.get('semantic_status') in ('context_only','pending_review')
        view['source_status']={'semantic_extraction':'not_required' if excluded else 'unknown'}
        summary={'tool':'read_evidence','evidence_id':evidence_id,'members':len(view['members']),'chars':length,'kind':view['kind'],'source_status':view['source_status'],'warnings':view['source_warnings']}
        self.summary.append(summary)
        try:
            records=self.store.query('MATCH (e:TK:Evidence {id:$id}) WHERE e.document_id IN $documents OPTIONAL MATCH (p:TK:Page) WHERE p.document_id=e.document_id AND p.physical_page=e.physical_page RETURN properties(e) AS evidence,p.semantic_status AS page_semantic_status',
                                     {'id':evidence_id,'documents':list(self.snapshot.document_ids)},self.budget)
            if not records: raise ValueError('Evidence状态不属于本次固定版本')
            evidence=unpack(records[0]['evidence'])
            from turbine_kg.knowledge.extract import semantic_review_views
            context['confirmed_semantic_reviews']=semantic_review_views([view],{evidence_id:evidence})[0]['context']['confirmed_semantic_reviews']
            page_status=records[0].get('page_semantic_status')
            view['page_semantic_status']=page_status
            view['source_status']['page_semantic_status']=page_status
            for field in ('semantic_coverage','semantic_issues'):
                if field in evidence: view[field]=evidence[field]
            status=evidence.get('semantic_coverage',{}).get('status')
            view['source_status']['semantic_extraction']=status or ('not_required' if excluded else 'unknown')
            if status=='incomplete' or evidence.get('semantic_issues'):
                view['source_warnings'].append({'kind':'semantic_extraction_incomplete','state':'incomplete','reason':'此来源的工程陈述抽取或程序检查尚未完成；可读原文仍可作为回答依据，但需核对其具体含义'})
            elif status is None and not excluded:
                view['source_warnings'].append({'kind':'semantic_extraction_unknown','state':'unknown','reason':'此来源未提供工程语义抽取覆盖状态，不能宣称已全部完成'})
            if page_status=='incomplete' and status!='incomplete' and not evidence.get('semantic_issues'):
                view['source_warnings'].append({'kind':'page_semantic_incomplete','state':'incomplete','reason':'该页部分工程陈述尚未完成抽取与程序检查，不代表此 Evidence 本身的原文未读全；当前条目状态与整页状态分别保留'})
        except BudgetExceeded:
            view['source_warnings'].append({'kind':'semantic_extraction_unknown','state':'unknown','reason':'来源原文已读取，工程语义状态未在剩余预算内取得'})
            raise
        except Exception:
            view['source_warnings'].append({'kind':'semantic_extraction_unknown','state':'unknown','reason':'来源原文已读取，但工程语义状态读取未完成，不能宣称全部完成'})
        self.diagnostics.extend({'evidence_id':evidence_id,**warning} for warning in view['source_warnings'])
        references=self._follow_references(view)
        return {'items':[view],'references':references,'completed_range':[0,1],
                'coverage':{'source_members':len(view['members']),'read':len(view['members'])},'cursor':None,'warnings':[]}

    def table_rows(self,table_id:str,cursor=None):
        self._take()
        key=fingerprint((self.snapshot.key,'table',table_id));offset=self._cursor(cursor,key)
        rows=self.store.query("MATCH (e:TK:Evidence {kind:'table'}) WHERE e.document_id IN $documents RETURN properties(e) AS node ORDER BY e.physical_page,e.id",{'documents':list(self.snapshot.document_ids)},self.budget)
        items=[unpack(row['node']) for row in rows if unpack(row['node']).get('context',{}).get('table_id')==table_id]
        selected=items[offset:offset+self.budget.row_page]
        result={'items':[{'evidence_id':row['id'],'context':row['context']} for row in selected],'completed_range':[offset,offset+len(selected)],'coverage':{'matched':len(items),'scope':'complete_row_groups'},'cursor':{'key':key,'offset':offset+len(selected)} if offset+len(selected)<len(items) else None,'warnings':['每个结果需读取完整行组，未知续表关联保持未知']}
        self.summary.append({'tool':'table_rows','table_id':table_id,'matches':len(items),'returned':len(selected),'has_more':bool(result['cursor'])})
        return result

    def read_visual(self,evidence_id:str):
        """Report unavailable pixels without reopening a local source image."""
        if evidence_id not in self.reads:
            self.read_evidence(evidence_id)
        view=self.reads[evidence_id]
        items=[{'evidence_id':evidence_id,'member_key':member['member_key'],
                'resource_read':False,'pixels_sent':False,'response_received':False,
                'status':'unavailable','reason':'Neo4j中尚未保存可供问答读取的图片像素'}
               for member in view['members'] if member.get('source_kind')=='picture']
        self.visuals[evidence_id]=items
        self.summary.append({'tool':'read_visual','evidence_id':evidence_id,'results':items})
        return {'items':items,'completed_range':[0,len(items)],
                'coverage':{'requested':len(items),'read':0},'cursor':None,
                'warnings':['图片像素未入图，不能根据标题推断图片中的工程事实'] if items else []}

    def dispatch(self,name,parameters):
        allowed={'navigate':self.navigate,'search_evidence':self.search_evidence,'read_evidence':self.read_evidence,'table_rows':self.table_rows,'read_visual':self.read_visual}
        if name not in allowed: raise ValueError('未知工具')
        return allowed[name](**parameters)
