"""Deterministic measured-value checks against source-backed quantities.

Object/parameter alignment is deliberately exact after approved ontology alias
normalization. Similar words never authorize a numerical comparison.
"""
from decimal import Decimal, InvalidOperation
import re
import unicodedata


# Scale to a common unit within each dimension. Unknown units stay unknown.
UNITS = {
    'mm':('length','0.001'), 'cm':('length','0.01'), 'm':('length','1'),
    'pa':('pressure','1'), 'kpa':('pressure','1000'), 'mpa':('pressure','1000000'),
    's':('time','1'), 'min':('time','60'), 'h':('time','3600'),
    'n':('force','1'), 'kn':('force','1000'), 'kg':('mass','1'), 't':('mass','1000'),
    '1':('ratio','1'), '倍':('ratio','1'), '%':('ratio','0.01'),
    'mm/m':('length_ratio','0.001'), 'm/m':('length_ratio','1'),
    '°c':('temperature','1'), '℃':('temperature','1'),
    'cm/m':('length_ratio','0.01'),
    'kpa/h':('pressure_rate','1000'), 'mpa/h':('pressure_rate','1000000'),
    'pa/h':('pressure_rate','1'),
}


# One vocabulary shared by question facts, numeric comparison and claim checks.
UNIT_ALIASES = {
    '毫米/米':'mm/m', '厘米/米':'cm/m', '米/米':'m/m',
    '千帕/小时':'kPa/h', '兆帕/小时':'MPa/h', '帕/小时':'Pa/h',
    '毫米':'mm', '厘米':'cm', '米':'m',
    '兆帕':'MPa', '千帕':'kPa', '帕斯卡':'Pa', '帕':'Pa',
    '分钟':'min', '小时':'h', '秒钟':'s', '秒':'s',
    '摄氏度':'°C', '摄氏':'°C', '℃':'°C',
    '千牛':'kN', '牛顿':'N', '千克':'kg', '公斤':'kg', '吨':'t',
    '百分比':'%',
}
NUMBER_PATTERN = r'[-+]?(?:\d+(?:\.\d+)?|\.\d+)'
UNIT_PATTERN = r'(?:MPa/h|kPa/h|Pa/h|mm/m|cm/m|m/m|r/min|MPa|kPa|Pa|mm|cm|min|kN|kg|rpm|Hz|°C|%|m|N|t|s|h)'


def normalize_quantity_text(value):
    """Normalize common units/signs in a working copy, preserving source quotes."""
    text = unicodedata.normalize('NFKC', str('' if value is None else value)).replace('−', '-').replace('﹣', '-')
    text = re.sub(r'零下\s*(?=\d)', '-', text)
    text = re.sub(r'百分之\s*(' + NUMBER_PATTERN + r')', r'\1%', text)
    aliases = '|'.join(re.escape(key) for key in sorted(UNIT_ALIASES, key=len, reverse=True))
    return re.sub(r'(?<![A-Za-z\u3400-\u9fff])(?:' + aliases + ')',
                  lambda match: UNIT_ALIASES[match.group()], text)


def canonical_unit(value):
    """Keep unsupported units verbatim; do not invent a conversion."""
    text = re.sub(r'\s+', '', normalize_quantity_text(value))
    symbols = {'mpa':'MPa', 'kpa':'kPa', 'pa':'Pa', '°c':'°C', 'kn':'kN', 'n':'N'}
    return symbols.get(text.casefold(), text)


def extract_quantities(text):
    return {(float(value), unit) for value, unit in re.findall(
        '(' + NUMBER_PATTERN + r')\s*(' + UNIT_PATTERN + r')(?![A-Za-z/])',
        normalize_quantity_text(text))}


def _text(value):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(value or ''))).casefold()


def _name(value,registry):
    canonical=getattr(registry,'canonical_label',None)
    if callable(canonical):return _text(canonical(value))
    domain=getattr(registry,'domain_class',lambda _:None)(value)
    return _text(domain['label'] if domain else value)


def _number(value):
    if isinstance(value,bool) or value is None:raise ValueError('missing numeric value')
    value=Decimal(normalize_quantity_text(value))
    if not value.is_finite():raise ValueError('nonfinite numeric value')
    return value


def _unit(value):
    name = _text(canonical_unit(value))
    known = UNITS.get(name)
    if known:
        return known
    # A ratio with an explicit baseline is comparable only to that baseline.
    # For example, 倍工作压力 must not be equated with a bare 倍 or 倍额定压力.
    if name.startswith('倍') and len(name) > 1:
        return ('ratio_ref:' + name[1:], '1')
    return None


def quantity_in_text(value, unit, text):
    """Bind a quantity to source wording, including a named ratio baseline."""
    try:
        expected=_number(value)
    except (ValueError, TypeError, InvalidOperation):
        return False
    if not isinstance(unit,str) or not unit.strip():return False
    source=_text(normalize_quantity_text(text));name=_text(canonical_unit(unit))
    expected_unit = _unit(unit)
    if expected_unit:
        for actual, actual_unit in extract_quantities(text):
            known = _unit(actual_unit)
            if known and known[0] == expected_unit[0] and Decimal(str(actual))*Decimal(known[1]) == expected*Decimal(expected_unit[1]):
                return True
    number=r'([-+]?(?:\d+(?:\.\d+)?|\.\d+))'
    patterns=[r'(?<![\d.])'+number+re.escape(name)+r'(?![A-Za-z/])']
    if name.startswith('倍') and len(name)>1:
        baseline=re.escape(name[1:])
        patterns.append(baseline+r'的?'+number+r'倍')
    for pattern in patterns:
        if any(_number(match)==expected for match in re.findall(pattern,source)):
            return True
    return False


def compare_quantity(quantity, facts, *, object_label, registry, applicability):
    """Return a traceable local result; never mutate supplied knowledge/facts."""
    result={'status':'unknown','reason':'','target':quantity.get('target'),
            'parameter':quantity.get('parameter'),'location':quantity.get('location'),
            'operator':quantity.get('operator'),'limit':quantity.get('value'),
            'unit':quantity.get('unit'),'applicability':applicability}
    def unknown(reason):
        return {**result,'reason':reason}
    if applicability=='not_applicable':
        return {**result,'status':'not_applicable','reason':'该要求的适用条件不成立'}
    if applicability!='applicable':return unknown('要求的适用条件尚未确定')
    if not quantity.get('parameter') or not quantity.get('target'):
        return unknown('来源未明确测量对象或参数')
    matches=[value for key,value in facts.items()
             if _name(key,registry)==_name(quantity['parameter'],registry)]
    if len(matches)!=1 or not isinstance(matches[0],dict):
        return unknown('现场参数缺失、存在歧义或未提供数值单位')
    actual=matches[0]
    result['actual']={key:actual[key] for key in ('value','unit','target','location') if key in actual}
    if _name(actual.get('target') or object_label,registry)!=_name(quantity['target'],registry):
        return unknown('现场数值与要求的测量对象不一致')
    if quantity.get('location') and _text(actual.get('location'))!=_text(quantity['location']):
        return unknown('现场测量位置缺失或与要求不一致')
    if quantity.get('completeness')=='incomplete':
        # Null location alone does not prevent a comparison when the source
        # applies to the whole object. All substantive unknowns remain blocked.
        missing=set(quantity.get('missing_fields',[]))-{'location'}
        if missing:return unknown('来源量值缺少必要字段：'+','.join(sorted(missing)))
    if not actual.get('unit') or not quantity.get('unit'):
        return unknown('现场数值或要求缺少明确单位')
    left_unit=_unit(actual['unit']);right_unit=_unit(quantity['unit'])
    if not left_unit or not right_unit or left_unit[0]!=right_unit[0]:
        return unknown('单位未知或量纲不一致')
    operator=quantity.get('operator')
    if operator not in ('lt','le','gt','ge','eq','ne'):
        return unknown('未提供可执行的比较符')
    try:
        actual_value=_number(actual['value'])
        left=actual_value*Decimal(left_unit[1]);right=_number(quantity['value'])*Decimal(right_unit[1])
    except (KeyError,ValueError,InvalidOperation):return unknown('数值不是可比较的有限标量')
    passed={'lt':left<right,'le':left<=right,'gt':left>right,'ge':left>=right,'eq':left==right,'ne':left!=right}[operator]
    return {**result,'status':'satisfied' if passed else 'not_satisfied','reason':'已按同一量纲执行比较',
            'normalized_actual':str(left/Decimal(right_unit[1])), 'comparison_unit':quantity['unit']}
