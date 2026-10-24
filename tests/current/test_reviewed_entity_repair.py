from copy import deepcopy

import pytest

from turbine_kg.knowledge.repair import (
    apply_entity_patches,
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
        'knowledge_type': 'requirement',
        'supports': [{'evidence_id': evidence_id, 'member_keys': ['m-1']}],
        'entities': [{'class': 'Mention', 'id': None,
                      'label': '排汽管道', 'surface_label': '排汽管道',
                      'role': 'object', 'storage_status': 'statement_only'}],
        'conditions': [], 'quantities': [], 'semantic_relations': [],
    }


def patch(*, preserve=False, surface='排汽管道'):
    return {
        'statement_id': 'statement-1', 'entity_index': 0,
        'original_label': '排汽管道', 'preserve_mention': preserve,
        'targets': [{'class_id': 'ExhaustPipe', 'surface_label': surface}],
        'reason': '原文明确排汽管道对象，经逐项审定。',
    }


def reviewed_view(registry):
    view = source_view()
    review = reviewed_entity_binding(
        '排汽管道', 'ExhaustPipe', statement()['supports'], [view], registry,
        '原文明确排汽管道对象，经逐项审定。', 'review-1',
    )
    view['entity_mapping_reviews'] = [review]
    return view


def test_entity_replacement_retains_statement_identity_and_original_source(registry):
    original = statement()
    before = deepcopy(original)
    result = apply_entity_patches(original, [patch()], [source_view()],
                                  registry, 'review-1')

    assert original == before
    assert {key: value for key, value in result.items() if key != 'entities'} == {
        key: value for key, value in before.items() if key != 'entities'
    }
    assert len(result['entities']) == 1
    entity = result['entities'][0]
    assert entity['class'] == 'OntologyClass'
    assert entity['class_id'] == 'ExhaustPipe'
    assert entity['surface_label'] == '排汽管道'
    assert entity['ontology_binding']['source_members'] == [
        {'evidence_id': 'e-1', 'member_key': 'm-1'}
    ]


def test_preserve_mention_appends_class_without_changing_original_entity(registry):
    original = statement()
    result = apply_entity_patches(original, [patch(preserve=True)],
                                  [source_view()], registry, 'review-1')

    assert len(result['entities']) == 2
    assert result['entities'][0] == original['entities'][0]
    assert result['entities'][1]['class_id'] == 'ExhaustPipe'
    assert result['supports'] == original['supports']


def test_review_rejects_surface_not_continuously_present_in_source(registry):
    with pytest.raises(ValueError, match='连续出现'):
        apply_entity_patches(statement(), [patch()],
                             [source_view(quote='排汽的管道应检查严密性。')],
                             registry, 'review-1')


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
