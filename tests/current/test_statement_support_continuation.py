from turbine_kg.evidence.context import close_member_selection, evidence_text


def test_statement_support_includes_both_sides_of_a_split_source_word():
    view = {'members': [
        {'member_key': 'lead', 'role': 'section', 'quote': '检查应符合下列规定：'},
        {'member_key': 'first', 'role': 'content', 'quote': '应检查管'},
        {'member_key': 'second', 'role': 'content', 'quote': '板和隔板中心应复查合格；'},
        {'member_key': 'third', 'role': 'content', 'quote': '另一要求应检查。'},
    ]}
    assert close_member_selection(view, ['lead', 'second']) == ['lead', 'first', 'second']


def test_statement_support_does_not_include_an_independent_sentence():
    view = {'members': [
        {'member_key': 'first', 'role': 'content', 'quote': '端面应平整。'},
        {'member_key': 'second', 'role': 'content', 'quote': '螺栓应紧固。'},
    ]}
    assert close_member_selection(view, ['second']) == ['second']


def test_member_closure_includes_item_introduction_but_not_neighboring_item():
    view = {'members': [
        {'member_key': 'lead', 'order': 0, 'role': 'section', 'quote': '应提交下列施工记录：'},
        {'member_key': 'first', 'order': 1, 'role': 'content', 'quote': '1 凝汽器基础检查记录；'},
        {'member_key': 'second', 'order': 2, 'role': 'content', 'quote': '2 支持弹簧安装记录。'},
    ]}
    assert close_member_selection(view, ['second']) == ['lead', 'second']


def test_member_closure_completes_layout_split_without_crossing_numbered_boundary():
    view = {'members': [
        {'member_key': 'first', 'order': 0, 'role': 'content', 'quote': '试验压力不得小于工作'},
        {'member_key': 'continuation', 'order': 1, 'role': 'content', 'quote': '压力的1.25倍；'},
        {'member_key': 'sibling', 'order': 2, 'role': 'content', 'quote': '2 保压时间为10 min。'},
    ]}
    assert close_member_selection(view, ['continuation']) == ['first', 'continuation']


def test_decimal_at_start_of_text_is_not_treated_as_a_list_marker():
    view = {'members': [
        {'member_key': 'prefix', 'order': 0, 'role': 'content', 'quote': '间隙应为'},
        {'member_key': 'decimal', 'order': 1, 'role': 'content', 'quote': '0.20mm。'},
    ]}
    assert close_member_selection(view, ['decimal']) == ['prefix', 'decimal']


def test_parenthesized_number_marker_is_recognized_as_a_new_list_item():
    view = {'members': [
        {'member_key': 'first', 'order': 0, 'role': 'content', 'quote': '1) 第一项尚未结束'},
        {'member_key': 'second', 'order': 1, 'role': 'content', 'quote': '2) 第二项。'},
    ]}
    assert close_member_selection(view, ['second']) == ['second']


def test_period_list_marker_can_be_followed_by_numeric_requirement_text():
    view = {'members': [
        {'member_key': 'first', 'order': 0, 'role': 'content', 'quote': '1. 间隙不得大于0.20mm。'},
        {'member_key': 'second', 'order': 1, 'role': 'content', 'quote': '2. 试验压力不得低于1.25倍。'},
    ]}
    assert close_member_selection(view, ['second']) == ['second']


def test_detached_number_prevents_merging_adjacent_unmarked_item_text():
    view = {'context': {'detached_markers': {'second-source': '2'}}, 'members': [
        {'member_key': 'lead', 'source_id': 'lead-source', 'order': 0, 'role': 'section',
         'quote': '检查应符合下列规定：'},
        {'member_key': 'first', 'source_id': 'first-source', 'order': 1, 'role': 'content',
         'quote': '1 第一项未结束'},
        {'member_key': 'second', 'source_id': 'second-source', 'order': 2, 'role': 'content',
         'quote': '第二项内容。'},
    ]}
    assert close_member_selection(view, ['second']) == ['lead', 'second']


def test_item_continuation_is_closed_before_its_governing_introduction():
    view = {'members': [
        {'member_key': 'lead', 'order': 0, 'role': 'section', 'quote': '检查应符合下列规定：'},
        {'member_key': 'first', 'order': 1, 'role': 'content', 'quote': '1 第一项；'},
        {'member_key': 'second', 'order': 2, 'role': 'content', 'quote': '2 第二项尚未结束'},
        {'member_key': 'continuation', 'order': 3, 'role': 'content', 'quote': '的补充内容。'},
    ]}
    assert close_member_selection(view, ['continuation']) == ['lead', 'second', 'continuation']


def test_heading_and_section_context_are_not_joined_as_body_continuations():
    view = {'members': [
        {'member_key': 'heading', 'order': 0, 'role': 'content', 'source_kind': 'heading',
         'quote': '5.2 设备要求'},
        {'member_key': 'scope', 'order': 1, 'role': 'section_context', 'quote': '适用于本章设备'},
        {'member_key': 'body', 'order': 2, 'role': 'content', 'quote': '设备应检查。'},
    ]}
    assert close_member_selection(view, ['body']) == ['body']


def test_table_members_are_never_joined_as_prose():
    view = {'kind': 'table', 'members': [
        {'member_key': 'header', 'order': 0, 'role': 'header', 'quote': '允许偏差'},
        {'member_key': 'value', 'order': 1, 'role': 'value', 'quote': '0.20mm'},
    ]}
    assert close_member_selection(view, ['value']) == ['value']


def test_nested_item_context_does_not_include_a_previous_parent_sibling():
    view = {'context': {}, 'members': [
        {'member_key': 'lead', 'source_id': 'lead', 'order': 0, 'role': 'content',
         'quote': '检查应符合下列规定：'},
        {'member_key': 'copper', 'source_id': 'copper', 'order': 1, 'role': 'content',
         'quote': '1 铜管检查：'},
        {'member_key': 'copper-test', 'source_id': 'copper-test', 'order': 2, 'role': 'content',
         'quote': '1）压扁试验；', 'attributes': {'structural_parent_source_ids': ['copper']}},
        {'member_key': 'copper-other', 'source_id': 'copper-other', 'order': 3, 'role': 'content',
         'quote': '2）扩口试验。', 'attributes': {'structural_parent_source_ids': ['copper']}},
        {'member_key': 'steel', 'source_id': 'steel', 'order': 4, 'role': 'content',
         'quote': '2 钢管检查：'},
        {'member_key': 'steel-test', 'source_id': 'steel-test', 'order': 5, 'role': 'content',
         'quote': '1）检查表面。', 'attributes': {'structural_parent_source_ids': ['steel']}},
    ]}
    assert close_member_selection(view, ['steel-test']) == ['lead', 'steel', 'steel-test']


def test_evidence_text_joins_internal_layout_breaks_without_changing_source_quote():
    quote = '垂\n直偏差应符合要求。'
    view = {'members': [{'member_key': 'source', 'quote': quote, 'role': 'content'}]}
    assert evidence_text({}, [view['members'][0]['quote']]) == '垂直偏差应符合要求。'
    assert view['members'][0]['quote'] == quote
