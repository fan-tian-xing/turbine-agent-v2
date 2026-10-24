"""Correct formula source text while retaining the original OCR and location."""
from copy import deepcopy
from hashlib import sha256


def formula_source_binding(source):
    """Bind a review to the exact stored source version and original-page region."""
    locator=source.get('locator',{})
    binding={key:source.get(key) for key in ('document_id','revision_id')}
    binding.update(source_id=source.get('id'),original_hash=locator.get('original_hash'),
                   physical_page=locator.get('physical_page'),bbox=deepcopy(locator.get('bbox')))
    if any(not isinstance(binding[key],str) or not binding[key] for key in
           ('source_id','document_id','revision_id','original_hash')):
        raise ValueError('人工确认必须绑定来源 ID、原件 hash 与文档版本')
    box=binding['bbox']
    if type(binding['physical_page']) is not int or binding['physical_page']<1 or not isinstance(box,list) or len(box)!=4:
        raise ValueError('人工确认必须绑定物理页与原件 bbox')
    if any(type(value) not in (int,float) for value in box) or box[0]>box[2] or box[1]>box[3]:
        raise ValueError('人工确认 bbox 无效')
    return binding


def confirm_formula_source(source, *, binding, raw_text, verified_text, formula, user_confirmation):
    """Return one updated SourceText; preserve raw OCR and never write/create nodes."""
    if binding!=formula_source_binding(source) or raw_text!=source.get('text'):
        raise ValueError('人工确认的来源版本、定位或原始文本已变化')
    if not isinstance(user_confirmation,dict) or user_confirmation.get('confirmed') is not True or user_confirmation.get('actor')!='user' or not isinstance(user_confirmation.get('note'),str) or not user_confirmation['note'].strip():
        raise ValueError('公式更正需要用户明确确认内容')
    if not isinstance(verified_text,str) or not verified_text.strip() or not isinstance(formula,dict) or not isinstance(formula.get('expression'),str) or not formula['expression'].strip():
        raise ValueError('人工确认需要明确更正正文及公式表达式')
    # Source formula variables use string units; "1" means dimensionless.
    units=formula.get('units')
    if units is not None and (not isinstance(units,dict) or any(value is not None and not isinstance(value,str) for value in units.values())):
        raise ValueError('变量单位必须为字符串或 null；显式无量纲使用字符串 1')
    corrected={key:deepcopy(formula.get(key)) for key in ('expression','variables','units','conditions')}
    corrected.update(expression_status='confirmed',read_certainty='confirmed',calculation_status='not_calculated')
    result=deepcopy(source)
    result.setdefault('attributes',{})['human_verified_representation']={
        'status':'confirmed_user','binding':deepcopy(binding),'raw_text':raw_text,
        'verified_text':verified_text,'formula':corrected,'user_confirmation':deepcopy(user_confirmation)}
    return result


def verified_formula_representation(source):
    """Return confirmed text/structure only while every original binding still matches."""
    review=source.get('attributes',{}).get('human_verified_representation')
    if not isinstance(review,dict) or review.get('status')!='confirmed_user':return None
    try:
        current=confirm_formula_source(source,binding=review['binding'],raw_text=review['raw_text'],
            verified_text=review['verified_text'],formula=review['formula'],user_confirmation=review['user_confirmation'])
    except (KeyError,TypeError,ValueError):return None
    return deepcopy(current['attributes']['human_verified_representation'])


def apply_formula_reviews(source, annotations):
    """Rebind portable human reviews only to the exact original region and raw OCR."""
    locator=source.get('locator',{});original_hash=locator.get('original_hash')
    rules=annotations.get(original_hash,{}).get('formula_reviews',[])
    raw_text=source.get('text','')
    matches=[rule for rule in rules if rule.get('original_hash')==original_hash
        and rule.get('physical_page')==locator.get('physical_page')
        and rule.get('bbox_pdf_pt')==locator.get('bbox')
        and rule.get('raw_text_sha256')==sha256(raw_text.encode('utf-8')).hexdigest()]
    if not matches:return deepcopy(source)
    if len(matches)!=1:raise ValueError('同一原件区域存在重复公式确认规则')
    rule=matches[0]
    return confirm_formula_source(source,binding=formula_source_binding(source),raw_text=raw_text,
        verified_text=rule['verified_text'],formula=rule['formula'],user_confirmation=rule['user_confirmation'])
