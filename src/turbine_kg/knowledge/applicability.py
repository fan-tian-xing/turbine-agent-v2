"""Three-valued conditions; incomplete comparisons never exclude evidence."""
from __future__ import annotations
import math
from turbine_kg.ontology.registry import normalized

UNITS = {'mm':('length',.001),'cm':('length',.01),'m':('length',1),'kPa':('pressure',1000),'MPa':('pressure',1e6),'Pa':('pressure',1),'N':('force',1),'kN':('force',1000),'kg':('mass',1),'t':('mass',1000),'℃':('temperature',1),'°C':('temperature',1)}


def evaluate(expression: dict | None, facts: dict, registry) -> str:
    if not isinstance(expression,dict) or not expression or not isinstance(facts,dict): return 'unknown'
    operator = expression.get('op')
    if operator in ('and','or'):
        children = expression.get('items',[])
        if not isinstance(children,list): return 'unknown'
        items = [evaluate(child,facts,registry) for child in children]
        if not items: return 'unknown'
        if operator == 'and':
            return 'not_applicable' if 'not_applicable' in items else 'applicable' if all(item=='applicable' for item in items) else 'unknown'
        return 'applicable' if 'applicable' in items else 'not_applicable' if all(item=='not_applicable' for item in items) else 'unknown'
    if operator == 'not':
        value = evaluate(expression.get('item'),facts,registry)
        return {'applicable':'not_applicable','not_applicable':'applicable','unknown':'unknown'}[value]
    key = expression.get('parameter')
    if not isinstance(key,str) or not key.strip(): return 'unknown'
    actual = facts.get(key)
    expected = expression.get('value')
    if actual is None or expected is None: return 'unknown'
    if operator in ('eq','ne') and not expression.get('unit'):
        if not isinstance(actual,str) or not isinstance(expected,str): return 'unknown'
        left = registry.canonical_label(str(actual));right=registry.canonical_label(str(expected))
        # Unknown categoricals need a declared vocabulary before a mismatch is meaningful.
        vocabulary = expression.get('vocabulary',[])
        if not isinstance(vocabulary,list) or any(not isinstance(value,str) for value in vocabulary): return 'unknown'
        vocabulary={normalized(registry.canonical_label(value)) for value in vocabulary}
        if normalized(left)!=normalized(right) and not all(normalized(value) in vocabulary for value in (left,right)): return 'unknown'
        result = normalized(left)==normalized(right)
        if operator=='ne': result = not result
        return 'applicable' if result else 'not_applicable'
    if operator not in ('lt','le','gt','ge','eq','ne'): return 'unknown'
    location=expression.get('location')
    if not isinstance(actual,dict) or not isinstance(location,str) or not location.strip(): return 'unknown'
    if actual.get('location') != location: return 'unknown'
    try:
        au,eu = UNITS[actual['unit']],UNITS[expression['unit']]
        if au[0]!=eu[0]: return 'unknown'
        left,right=float(actual['value'])*au[1],float(expected)*eu[1]
        if isinstance(actual['value'],bool) or isinstance(expected,bool) or not math.isfinite(left) or not math.isfinite(right): return 'unknown'
        same=math.isclose(left,right,rel_tol=1e-12,abs_tol=0)
        result={'lt':left<right and not same,'le':left<right or same,'gt':left>right and not same,'ge':left>right or same,'eq':same,'ne':not same}.get(operator)
        return 'unknown' if result is None else 'applicable' if result else 'not_applicable'
    except (KeyError,ValueError,TypeError): return 'unknown'
