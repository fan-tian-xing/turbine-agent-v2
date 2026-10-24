from turbine_kg.ontology.registry import OntologyRegistry


def test_statement_schema_keeps_conditions_in_text_instead_of_structured_fields():
    schema = OntologyRegistry().extraction_schema()
    item = {
        'type': 'requirement',
        'text': '冷却管穿胀前应检查；不合格管数量大于安装总数的1％时，应逐根试验。',
        'entities': [],
        'supports': [{'evidence_id': 'e', 'member_keys': ['m']}],
        'conditions': [{'text': '不合格管数量大于安装总数的1％时'}],
    }
    assert not {'conditions', 'quantities', 'formulas'} & set(schema['properties'])
