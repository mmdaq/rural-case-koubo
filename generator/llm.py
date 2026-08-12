"""LLM 文案生成：OpenAI 兼容接口（DeepSeek 等），失败自动降级模板引擎"""
import json
import re

from generator import template
from generator.painpoints import PAIN_POINTS, SCENARIO_CTAS, REFERENCE_CTAS
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
你的读者是普通农村群众。硬性要求：
1. 【只写纯口播文案】只输出：标题、正文、评论区互动。不写任何法律分析、说明、案例核查记录、来源罗列等非口播内容。
2. 【案例必须真实】只依据提供的真实案例（入库编号/案情/裁判要旨）改编，严禁杜撰或编造案件，严禁虚构人名、地点、金额、判决结果；原文未交代的地点一律写"某地/某村"，人名脱敏用"某某"。
3. 【痛点+细分类型】正文要按给定【用户痛点】和【案件细分类型】定向写作：先戳中村民的痛点（如"村集体账目不透明""投票把权益投没了"），再讲真实案情，最后点出法律态度（如"村规民约再大，也大不过法律"）。
4. 【结尾留言引导】正文之后必须写"评论区互动"留言引导：一句话、提问式、有钩子，风格参考给定的话术池。
5. 口语化、有冲突感、有具体数字和细节，像讲身边事；不夸大金额、不编造细节。

输出格式（严格按此格式）：
入库编号：xxx
标题：xxx（一句抓眼球的话）
正文：xxx（150~250字）
评论区互动：xxx（一句话留言引导）"""

USER_PROMPT_TMPL = """请参考以下2篇范例的风格与格式（口吻、结构、互动话术）：

范例1：
{example1}

范例2：
{example2}

现在根据以下真实案例（人民法院案例库入库编号/官方链接可查，案情、裁判要旨为官方原文），
按【痛点】和【细分类型】撰写1篇纯口播文案：

入库编号：{rule_code}
案名：{title}
案件细分类型：{subtype}
对应用户痛点：{pain_points}
审理法院：{court}
裁判文书号：{doc_no}
涉及金额：{amount}
官方可查链接：{official_link}
基本案情：{facts}
裁判理由：{reasoning}
裁判要旨：{gist}
判决结果：{result}

注意：判决结果以【判决结果】字段为准，不得自行推断或夸大金额；正文提及金额、判决内容必须与判决结果、基本案情一致。

留言引导话术池（结尾的"评论区互动"从中选一条，或按同样风格自拟）：
{cta_pool}
"""


def _parse_output(text: str) -> dict | None:
    """解析 LLM 输出为 {rule_code,title,body,cta}（兼容多种字段命名）"""
    fields = {}
    m = re.search(r"(?:入库编号|rule_code|编号)[:：]\s*([^\n]+)", text)
    if m:
        fields["rule_code"] = m.group(1).strip()
    m = re.search(r"(?:标题|title)[:：]\s*([^\n]+)", text)
    if m:
        fields["title"] = m.group(1).strip()
    m = re.search(r"(?:正文|body)[:：]\s*([\s\S]*?)(?=(?:评论区互动|cta|互动)[:：]|$)", text)
    if m:
        fields["body"] = m.group(1).strip()
    m = re.search(r"(?:评论区互动|cta|互动)[:：]\s*([^\n]+)", text)
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
    cta_pool = "\n".join(f"- {c}" for c in SCENARIO_CTAS.get(case.scenario, REFERENCE_CTAS))
    pain_points = "；".join(PAIN_POINTS.get(p, p) for p in (case.pain_points or [])) or "村民对集体分配不公的普遍担忧"
    subtype = case.subtype or case.scenario or "农村集体资产纠纷"

    user_prompt = USER_PROMPT_TMPL.format(
        example1=ex1,
        example2=ex2,
        rule_code=case.rule_code,
        title=case.title,
        subtype=subtype,
        pain_points=pain_points,
        court=case.court,
        doc_no=case.doc_no,
        amount=case.amount,
        official_link=case.official_link or "（见入库编号，可在人民法院案例库检索）",
        facts=case.facts,
        reasoning=case.reasoning,
        gist=case.gist,
        result=case.result or "（见裁判要旨）",
        cta_pool=cta_pool,
    )

    try:
        import requests
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
