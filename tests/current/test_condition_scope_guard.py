import pytest

from turbine_kg.knowledge.extract import _check_condition_scope


def test_local_condition_cannot_limit_earlier_independent_requirement():
    item={'text':'冷却管穿胀前应检查；不合格管数量大于安装总数的1％时，应逐根试验。',
          'conditions':[{'text':'冷却管穿胀前'},
                        {'text':'不合格管数量大于安装总数的1％时'}]}
    with pytest.raises(ValueError,match='局部条件'):
        _check_condition_scope(item)


def test_condition_belongs_to_single_later_requirement():
    item={'text':'不合格管数量大于安装总数的1％时，应逐根试验。',
          'conditions':[{'text':'不合格管数量大于安装总数的1％时'}]}
    _check_condition_scope(item)
