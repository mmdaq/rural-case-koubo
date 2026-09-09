"""为 RMFYALK 案例补充详情（基本案情/裁判理由/裁判结果）"""
import sys
import time
import json
sys.path.insert(0, '.')
from collector.rmfyalk_bulk import _get_content, _clean_html, _fill_details
from collector.extrastore import ExtraStore
from utils.logger import get_logger
log = get_logger("fill_details")

extra = ExtraStore('data/extra_cases.json')
codes_with_no_facts = [
    code for code, w in extra.data.get('cases', {}).items()
    if len((w.get('case', {}).get('facts') or '').strip()) < 50
]
print(f'Cases needing facts: {len(codes_with_no_facts)}')

filled = 0
for i, code in enumerate(codes_with_no_facts):
    wrapper = extra.data['cases'][code]
    case = wrapper.get('case', wrapper)
    # _fill_details needs _last_item set, but we can do it manually
    # Just call the content API directly
    from collector.rmfyalk_bulk import SEARCH_API, HEADERS, CONTENT_API
    import requests
    try:
        gid = ''
        item = getattr(_fill_details, '_last_item', None)
        if item:
            gid = item.get('cpws_al_id', '')
        # Try to get gid from rule_code pattern - not possible, skip
        # Instead, search for this case to get its gid
        from collector.rmfyalk_bulk import _search
        data = _search('征地补偿', page=1)
        if data and data.get('code') == 0:
            for it in data.get('data', {}).get('datas', []):
                if it.get('cpws_al_no') == code:
                    gid = it.get('cpws_al_id', '')
                    break
        if not gid:
            continue
        content = _get_content(gid)
        if content:
            case['facts'] = _clean_html(content.get('cpws_al_jbaq') or '')
            case['reasoning'] = _clean_html(content.get('cpws_al_cply') or '')
            case['result'] = _clean_html(content.get('cpws_al_cpjg') or '')
            extra.data['cases'][code] = wrapper
            filled += 1
            print(f'  [{i+1}/{len(codes_with_no_facts)}] {code}: facts={len(case["facts"])} gist={len(case.get("gist",""))}')
            time.sleep(1)
    except Exception as e:
        print(f'  [{i+1}] {code}: ERROR {e}')
        time.sleep(2)

extra._save()
print(f'\nFilled facts for {filled} cases')
# Check how many now pass verification
import json
with open('data/extra_cases.json', encoding='utf-8') as f:
    ed = json.load(f)
passed = sum(1 for w in ed['cases'].values()
             if len((w.get('case',{}).get('facts','')) or '') >= 50
             and len((w.get('case',{}).get('gist','')) or '') > 20)
print(f'Cases with facts>=50 AND gist>20: {passed}/{len(ed["cases"])}')
