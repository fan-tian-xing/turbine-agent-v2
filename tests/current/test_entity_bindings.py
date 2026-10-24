from copy import deepcopy

import pytest

from turbine_kg.knowledge.entity_bindings import (
    restored_entity_binding,
    reviewed_entity_binding,
)
from turbine_kg.ontology.registry import OntologyRegistry


@pytest.fixture
def registry():
    return OntologyRegistry()


def source_view(evidence_id='e-1', quote='排汽管道应检查严密性。'):
    return {
        'evidence_id': evidence_id,
        'document_id': 'document-1',
        'revision_id': 'revision-1',
        'member_fingerprint': 'snapshot-1',
        'members': [{
            'member_key': 'm-1', 'source_id': 'source-1',
            'quote': quote, 'locator': {'page': 46},
            'role': 'body', 'source_kind': 'text',
        }],
    }


def statement(evidence_id='e-1'):
    return {
        'id': 'statement-1', 'text': '排汽管道应检查严密性。',
        'type': 'requirement',
        'supports': [{'evidence_id': evidence_id, 'member_keys': ['m-1']}],
        'entities': [{'class': 'Mention', 'label': '排汽管道', 'role': 'object'}],
        'semantic_relations': [],
    }


def reviewed_view(registry):
    view = source_view()
    review = reviewed_entity_binding(
        '排汽管道', 'ExhaustPipe', statement()['supports'], [view], registry,
        '原文明确排汽管道对象，经逐项审定。', 'review-1',
    )
    view['entity_mapping_reviews'] = [review]
    return view


def test_review_rejects_surface_not_continuously_present_in_source(registry):
    with pytest.raises(ValueError, match='连续出现'):
        reviewed_entity_binding('排汽管道', 'ExhaustPipe', statement()['supports'],
                                [source_view(quote='排汽的管道应检查严密性。')],
                                registry, '原文名称核对', 'review-1')


def test_restoration_rejects_changed_quote_even_when_surface_still_matches(registry):
    view = reviewed_view(registry)
    supports = statement()['supports']
    restored = restored_entity_binding('排汽管道', supports, [view], registry)
    assert restored is not None and restored['class_id'] == 'ExhaustPipe'

    changed = deepcopy(view)
    changed['members'][0]['quote'] = '排汽管道不得进行原文未规定的试验。'
    assert restored_entity_binding('排汽管道', supports, [changed], registry) is None


def test_restoration_does_not_reuse_review_from_unrelated_evidence(registry):
    reviewed = reviewed_view(registry)
    unrelated = source_view(evidence_id='e-other')
    supports = statement(evidence_id='e-other')['supports']

    assert restored_entity_binding('排汽管道', supports,
                                   [reviewed, unrelated], registry) is None
