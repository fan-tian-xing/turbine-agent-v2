"""Read current graph counts and unresolved ontology mappings."""
import argparse
from turbine_kg.knowledge.store import KnowledgeStore, unpack
from turbine_kg.runtime import json_text
from turbine_kg.settings import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mapping-report', action='store_true', help='报告当前文档中未绑定本体类的实体标签，不修改图谱')
    parser.add_argument('--document', default='', help='映射报告限定的资料标识；不填时使用图中全部当前资料')
    args = parser.parse_args()
    with KnowledgeStore(Settings.from_environment()) as store:
        if args.mapping_report:
            rows = store.query(
                'MATCH (s:TK:Statement) WHERE ($document="" OR s.document_id=$document) RETURN s.data_json AS data',
                {'document': args.document})
            from collections import Counter
            counts = Counter()
            reviewed = Counter()
            for row in rows:
                statement = unpack({'data_json': row['data']})
                for entity in statement.get('entities', []):
                    if entity.get('class') != 'Mention' or entity.get('ontology_class_id'):
                        continue
                    label = entity.get('label', '').strip()
                    if not label:
                        continue
                    counts[label] += 1
                    if entity.get('ontology_mapping_review'):
                        reviewed[label] += 1
            print(json_text({'document_id': args.document or None, 'statements': len(rows),
                             'mention_entities': sum(counts.values()),
                             'distinct_labels': len(counts),
                             'labels': [{'label': label, 'count': count,
                                         'unresolved_count': reviewed.get(label, 0)}
                                        for label, count in counts.most_common()]}), flush=True)
        print(json_text({'nodes': store.query('MATCH (n) RETURN count(n) AS count')[0]['count'],
                         'relationships': store.query('MATCH ()-[r]->() RETURN count(r) AS count')[0]['count'],
                         'labels': store.query('MATCH (n) UNWIND labels(n) AS label WITH label,count(*) AS count WHERE label<>"TK" RETURN label,count ORDER BY count DESC')}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
