"""One-shot and real interactive CLI; diagnostics remain opt-in."""
import sys
from pathlib import Path

if __name__=='__main__':
    sys.dont_write_bytecode=True
    if not __package__:
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

from dataclasses import asdict
import argparse
import json
from turbine_kg.agent.runner import Agent,Session
from turbine_kg.knowledge.store import KnowledgeStore
from turbine_kg.settings import Settings


def _pages(values):
    if not values:return '未确认'
    spans=[];start=previous=None
    for value in values:
        if not isinstance(value,int):
            if start is not None:spans.append(str(start) if start==previous else f'{start}–{previous}');start=previous=None
            spans.append(str(value));continue
        if start is None:start=previous=value
        elif value==previous+1:previous=value
        else:spans.append(str(start) if start==previous else f'{start}–{previous}');start=previous=value
    if start is not None:spans.append(str(start) if start==previous else f'{start}–{previous}')
    return '、'.join(spans)


def display(result,json_mode=False,trace=False):
    if json_mode:
        print(json.dumps(asdict(result),ensure_ascii=False,indent=2));return
    print('建议：\n'+result.answer)
    limits=[]
    for gap in getattr(result,'gaps',[]):
        if gap.get('kind') not in ('source_missing','answer_incomplete','known_omission','answer_coverage') and gap.get('category')!='source_missing':continue
        if gap.get('kind')=='reference_deferred':continue
        reason=gap.get('reason')
        if reason and reason not in limits:limits.append(reason)
    for gap in getattr(result,'gaps',[]):
        if gap.get('kind')!='reference_deferred':continue
        number=gap.get('reference_number')
        if number and not any(str(number) in reason for reason in limits):
            kind={'table':'表','figure':'图'}.get(gap.get('reference_kind'),'条款')
            limits.append(f'引用的{kind}{number}正文尚未提供，相关要求未展开。')
    if limits:print('\n资料边界与未完成项：\n'+'\n'.join(limits))
    groups=getattr(result,'citation_groups',None)
    confirmed=bool(getattr(result,'claims',[]))
    print('\n依据来源：')
    if confirmed and groups:
        for index,group in enumerate(groups,1):
            location='PDF第'+_pages(group.get('physical_pages',[]))+'页'
            if group.get('logical_pages'):location+='（原文第'+_pages(group['logical_pages'])+'页）'
            print(f"[{index}] {group['title']}；{group['resource_title']}；{location}")
            if group['kind'] not in ('table','figure'):
                for quote in group.get('quotes',[]):print('原文：'+quote)
    elif confirmed and result.citations:
        for index,cite in enumerate(result.citations,1):
            loc=cite['locator']
            location='PDF第'+str(loc.get('physical_page','未确认'))+'页'
            if loc.get('logical_page'):location+='（原文第'+str(loc['logical_page'])+'页）'
            print(f"[{index}] {cite['title']}；版本 {cite['revision']}；{location}")
            if cite.get('quote'):print('原文：'+cite['quote'])
    else:print('暂无可用于回答的已核验依据。')
    if trace: print(json.dumps(result.search_summary,ensure_ascii=False,indent=2))


def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description='来源辅助问答')
    parser.add_argument('question',nargs='?');parser.add_argument('--json',action='store_true');parser.add_argument('--trace',action='store_true')
    args=parser.parse_args();session=Session()
    try:
        with KnowledgeStore(Settings.from_environment()) as store:
            agent=Agent(store)
            def answer(question):
                result=agent.answer(question,session)
                result.search_summary['source_mode']='current_document'
                display(result,args.json,args.trace)
            if args.question: answer(args.question);return
            print('输入工程问题；输入 /退出 结束。')
            while True:
                try: question=input('\n> ').strip()
                except (EOFError,KeyboardInterrupt): break
                if question in ('/退出','/exit','quit'): break
                if question: answer(question)
    except Exception as error:
        print('未完成本次回答：'+type(error).__name__+'：'+str(error));return 1
    return 0

if __name__=='__main__': raise SystemExit(main())
