"""Suffix matching uses ontology specificity, never arbitrary substring guesses."""
from copy import deepcopy

import pytest

from turbine_kg.ontology.registry import OntologyRegistry


def source(label):
    return ([{'evidence_id': 'e', 'member_keys': ['body']}],
            {'e': {'members': [{'member_key': 'body', 'role': 'content',
                                'quote': label + '应检查。'}]}})


def test_related_suffixes_select_the_unique_most_specific_kind():
    registry = OntologyRegistry()
    label = '待安装的低压加热器'
    assert registry.resolve_entity_class(label)['class_id'] == 'LowPressureHeater'
    supports, views = source(label)
    binding = registry.ontology_binding(label, 'LowPressureHeater', supports, views)
    assert binding['method'] == 'suffix_source_name'
    with pytest.raises(ValueError):
        registry.ontology_binding(label, 'Heater', supports, views)


def test_unrelated_suffixes_still_require_disambiguation():
    registry = OntologyRegistry()
    registry.domain_classes['Tank'] = deepcopy(registry.domain_classes['Tank'])
    registry.domain_classes['Tank']['aliases'].append('压加热器')
    label = '待安装的低压加热器'
    assert registry.resolve_entity_class(label) is None
    supports, views = source(label)
    with pytest.raises(ValueError):
        registry.ontology_binding(label, 'LowPressureHeater', supports, views)


def test_body_part_and_unsupported_source_are_not_promoted_to_equipment():
    registry = OntologyRegistry()
    assert registry.resolve_entity_class('低压加热器外壁') is None
    supports, views = source('外壁')
    with pytest.raises(ValueError):
        registry.ontology_binding('待安装的低压加热器', 'LowPressureHeater', supports, views)
