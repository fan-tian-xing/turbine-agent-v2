import argparse
import json
import sys
from pathlib import Path
from turbine_kg.agent.model import ModelClient
from turbine_kg.knowledge.store import KnowledgeStore
from turbine_kg.ontology.registry import OntologyRegistry
from turbine_kg.settings import Settings
from turbine_kg.settings import PROJECT_ROOT
from turbine_kg.documents.catalog import rows,document_id
from turbine_kg.knowledge.chapter_processing import process_section, prepare_section


def parse_pages(value):
    result=[]
    for part in value.split(','):
        if '-' in part:
            start,end=map(int,part.split('-'))
            if end<start:raise ValueError('页面范围必须升序')
            result.extend(range(start,end+1))
        else:result.append(int(part))
    if not result or any(number<1 for number in result):raise ValueError('页码必须为正整数')
    return sorted(set(result))


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(errors='backslashreplace')
    parser=argparse.ArgumentParser(description='离线处理正式资料并更新当前知识图谱')
    parser.add_argument('source',nargs='?',help='正式来源；不指定--section时唯一匹配该来源已配置章节')
    parser.add_argument('--planned',action='store_true',help='处理processing_scopes配置的章节；每个章节默认全量处理')
    parser.add_argument('--retry-semantics',action='store_true',help='仅重新核验指定章节页面；未指定时按章节全量处理')
    parser.add_argument('--work-pages',help='仅抽取章节范围内指定物理页，例如25,26或25-28；不能改变已配置章节范围')
    parser.add_argument('--evidence-ids',help='逗号分隔的Evidence ID；只对这些证据定向重抽，须同时指定--work-pages')
    parser.add_argument('--section',help='匹配processing_scopes中的章节编号；未指定时须唯一匹配')
    parser.add_argument('--prepare-only',action='store_true',help='仅准备章节完整原文及证据，不调用LLM')
    parser.add_argument('--remap',action='store_true',help='按当前本体重新映射资料中已有Statement；不OCR、不调用LLM、不重抽原文')
    parser.add_argument('--workers',type=int,help='章节并行抽取数量，持久化仍串行')
    args=parser.parse_args();settings=Settings.from_environment()
    if args.remap and (args.prepare_only or args.retry_semantics or args.work_pages or args.evidence_ids or args.workers):
        parser.error('--remap只更新已有知识的本体映射，不与抽取或准备选项并用')
    if args.workers is not None and not 1<=args.workers<=8:parser.error('--workers必须为1至8')
    if args.prepare_only and (args.retry_semantics or args.work_pages or args.workers):parser.error('--prepare-only只准备完整配置章节，不与抽取或复验选项同时使用')
    if args.evidence_ids and (not args.work_pages or args.prepare_only or args.planned):parser.error('--evidence-ids须用于单个章节的--work-pages复验')
    if args.evidence_ids and args.retry_semantics:parser.error('--evidence-ids已指定复验范围，不与--retry-semantics并用')
    try:work_pages=parse_pages(args.work_pages) if args.work_pages else None
    except ValueError as error:parser.error(str(error))
    evidence_ids=args.evidence_ids.split(',') if args.evidence_ids else None
    if evidence_ids is not None and (any(not item.strip() or item!=item.strip() for item in evidence_ids) or len(set(evidence_ids))!=len(evidence_ids)):
        parser.error('--evidence-ids须为不重复的非空Evidence ID')
    scopes=json.loads((PROJECT_ROOT/'config/processing_scopes.json').read_text(encoding='utf-8'))
    if not isinstance(scopes,list) or not scopes or any(scope.get('scope')!='section' for scope in scopes):parser.error('离线入口只接受已配置章节，不提供全文处理')
    jobs=[]
    if args.planned:
        if args.source or args.section: parser.error('--planned不与单个来源或章节同时使用')
        derived_paths={row['derived_relative_path'] for row in rows('derived_asset_links.tsv')}
        catalogue={}
        for row in rows('document_identity.tsv'):
            if row['relative_path'] not in derived_paths:
                catalogue.setdefault(row['document_logical_id'],[]).append(row['relative_path'])
        for scope in scopes:
            candidates=catalogue.get(scope['document_id'],[])
            if len(candidates)!=1:parser.error('计划来源须唯一对应原始资产：'+scope['document_id'])
            jobs.append(((settings.source_root/candidates[0]).resolve(),scope))
    else:
        if not args.source:parser.error('请提供来源或--planned')
        path=Path(args.source)
        if not path.is_absolute():path=settings.source_root/path
        path=path.resolve();doc=document_id(path,settings)
        matches=[scope for scope in scopes if scope['document_id']==doc and (args.section is None or str(scope.get('section'))==args.section)]
        if len(matches)!=1:parser.error('来源须唯一匹配一个已配置章节；有多个章节时指定--section')
        jobs.append((path,matches[0]))
    from turbine_kg.knowledge.chapter_processing import scope_pages
    for _,scope in jobs:
        try:allowed=scope_pages(scope)
        except ValueError as error:parser.error(str(error))
        if work_pages and not set(work_pages).issubset(allowed):parser.error('--work-pages必须属于配置章节范围')
    with KnowledgeStore(settings) as store:
        registry=OntologyRegistry()
        if args.remap:
            from turbine_kg.knowledge.ontology_mapping import sync_ontology_mappings
            from turbine_kg.knowledge.chapter_processing import refresh_document_pages
            results=sync_ontology_mappings(store,registry,
                force_document_ids={scope['document_id'] for _,scope in jobs})
            for document,result in results.items():
                result.update(refresh_document_pages(store,document))
                print(json.dumps({'document_id':document,**result},ensure_ascii=False,indent=2),flush=True)
            return 0
        for path,scope in jobs:
            print(scope.get('description') or '配置章节 '+str(scope.get('section')),flush=True)
            if args.prepare_only:result=prepare_section(path,store,registry,scope,model=ModelClient(settings))
            else:result=process_section(path,store,ModelClient(settings),registry,scope,work_pages=work_pages,
                evidence_ids=evidence_ids,retry_semantics=args.retry_semantics,workers=args.workers)
            print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    return 0

if __name__=='__main__': raise SystemExit(main())
