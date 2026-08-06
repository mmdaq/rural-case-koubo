"""LLM 文案生成：OpenAI 兼容接口（DeepSeek 等），失败自动降级模板引擎"""
import json
import re

import requests

from generator import template
from collector.models import Case
from utils.logger import get_logger

log = get_logger("generator")

# 用户示例风格（作为 few-shot 范例，保持输出口吻一致）
STYLE_EXAMPLES = [
    {
        "rule_code": "2024-07-2-044-005",
        "title": "离婚回村，村里说股权证没你名就没钱拿",
        "body": (
            "海南高院2024年再审改判的案例，入选人民法院案例库。海口某村一位妇女，"
            "生在村里长在村里，户口从没迁过，土地承包经营权证上也有她名字。离婚后村里搞产权改革，"
            "发股权证没她的份。征地补偿款下来了，11万8，别人都有，就她没有。村里说了，"
            "村民大会定的，没股权证就不是股东，没资格分。她起诉到法院，一审二审都输了。"
            "海南高院再审改判，村里必须补给她6万2。法院判得很硬：判断是不是村集体成员，"
            "看的是户籍、土地、生活来源，不是一张股权证说了算。户口在、地在、人在，集体的钱就有你一份。"
        ),
        "cta": "你们村有没有类似“离婚就不给分钱”的规矩？评论区说出来，我帮你看看合不合法。",
    },
    {
        "rule_code": "2023-07-2-044-001",
        "title": "嫁出去的姑娘，村里的分红没了",
        "body": (
            "最高院审核入库的典型案例。江苏某村一位妇女，户口从没迁出过，土地承包经营权也有。"
            "村里把柴田、粮田对外发包收承包金，年年给村民分红，可村里说了，你是嫁出去的姑娘，"
            "分红没你的份。她起诉到法院。法院判得很干脆：村里必须给她补上分红款。判决说了一句很重的话"
            "——妇女在农村土地承包经营、集体收益分配上，和男子享有平等权利。任何组织和个人不得以妇女"
            "结婚为由，侵害她在村集体中的权益。为啥这个案子能入库？因为它说明了一个硬道理："
            "村规民约再大，也大不过法律。“婚出姑娘”四个字就想把妇女的权益抹掉，法律不认。"
        ),
        "cta": "你是外嫁女吗？分到钱了吗？评论区聊聊，我教你咋维权。",
    },
]

SYSTEM_PROMPT = """你是资深法律口播文案作者，专门为短视频平台撰写"农村集体资产维权"类口播文案。
你的读者是普通农村群众。要求：
1. 口语化、有冲突感、有具体数字和细节，像讲身边事；
2. 严格依据提供的真实案例事实，不虚构事实、不夸大金额；
3. 结尾必须点出法律态度（如"村规民约大不过法律"）；
4. 评论区互动要提问式、有钩子。

输出格式（严格按此格式，每个案例一段）：
入库编号：xxx
标题：xxx（一句抓眼球的话）
正文：xxx（150~250字，分2-4句）
评论区互动：xxx（一句话提问）"""

USER_PROMPT_TMPL = """请参考以下2篇范例的风格与格式（口吻、结构、互动话术）：

范例1：
{example1}

范例2：
{example2}

现在根据以下真实案例（人民法院案例库入库编号、案情、裁判要旨均为官方原文），撰写1篇口播文案：

入库编号：{rule_code}
案名：{title}
审理法院：{court}
裁判文书号：{doc_no}
涉及金额：{amount}
基本案情：{facts}
裁判理由：{reasoning}
裁判要旨：{gist}
"""


def _parse_output(text: str) -> dict | None:
    """解析 LLM 输出为 {rule_code,title,body,cta}"""
    fields = {}
    m = re.search(r"入库编号[:：]\s*([^\n]+)", text)
    if m:
        fields["rule_code"] = m.group(1).strip()
    m = re.search(r"标题[:：]\s*([^\n]+)", text)
    if m:
        fields["title"] = m.group(1).strip()
    m = re.search(r"正文[:：]\s*([\s\S]*?)(?=评论区互动|$)", text)
    if m:
        fields["body"] = m.group(1).strip()
    m = re.search(r"评论区互动[:：]\s*([^\n]+)", text)
    if m:
        fields["cta"] = m.group(1).strip()
    if fields.get("rule_code") and fields.get("body"):
        return fields
    return None


def generate_with_llm(case: Case, cfg: dict) -> dict | None:
    """调用 OpenAI 兼容接口生成文案；失败返回 None"""
    api_key = cfg.get("api_key") or ""
    if not api_key or cfg.get("provider") in (None, "none"):
        return None

    ex1 = "\n".join(f"{k}：{v}" for k, v in STYLE_EXAMPLES[0].items())
    ex2 = "\n".join(f"{k}：{v}" for k, v in STYLE_EXAMPLES[1].items())

    user_prompt = USER_PROMPT_TMPL.format(
        example1=ex1,
        example2=ex2,
        rule_code=case.rule_code,
        title=case.title,
        court=case.court,
        doc_no=case.doc_no,
        amount=case.amount,
        facts=case.facts,
        reasoning=case.reasoning,
        gist=case.gist,
    )

    try:
        resp = requests.post(
            f"{cfg.get('base_url', 'https://api.deepseek.com').rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": cfg.get("model", "deepseek-chat"),
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": float(cfg.get("temperature", 0.8)),
                "max_tokens": 800,
            },
            timeout=int(cfg.get("timeout", 60)),
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        parsed = _parse_output(text)
        if parsed:
            return parsed
        log.warning("LLM 输出无法解析，回退模板: %s", text[:120])
    except Exception as e:
        log.warning("LLM 调用失败，回退模板引擎: %s", e)
    return None


def generate_script(case: Case, cfg: dict) -> dict:
    """生成单篇文案：优先 LLM，失败降级模板"""
    script = generate_with_llm(case, cfg)
    if script:
        return script
    return template.generate_script(case)
