"""模板引擎（兜底）：无 LLM API Key 时，用句式库 + 案例字段拼装口播文案

尽力贴近示例口吻：冲突开场 → 案情细节 → 判决结果 → 口语化法理 → 评论区互动。
"""
import random
from collector.models import Case
from generator.painpoints import SCENARIO_CTAS, DEFAULT_CTAS, PAIN_POINTS

# ---------------- 开场句（按主题场景） ----------------
OPENERS = {
    "离婚妇女": [
        "离婚了，村里的钱就没你份？{court}审结的这起案子，入选人民法院案例库。",
        "离婚后还在村里住，征地款却没你的份？人民法院案例库收录了这起真实案例。",
    ],
    "外嫁女": [
        "嫁出去的姑娘，村里的分红就没了？这是人民法院案例库收录的真实案例。",
        "户口没迁走，钱却被扣下？人民法院案例库这起案子说得很清楚。",
    ],
    "外嫁女·股权证": [
        "一张股权证，差点让她的征地款全打水漂。人民法院案例库这起案子，值得每个农村妇女看。",
    ],
    "承包方消亡继承": [
        "老人去世了，征地款就没她份？人民法院案例库这起案子给出了答案。",
    ],
    "分配方案": [
        "村里分钱只按地分，没地的村民就活该？人民法院案例库收录了这起案子。",
    ],
    "养女资格": [
        "不是亲生的，就不是村里人？人民法院案例库这起案子说：不一定！",
    ],
    "户籍迁出": [
        "户口迁出去，就不是村里人了？人民法院案例库这起案子给出了答案。",
    ],
    "征地补偿": [
        "征地补偿款发下来了，名单上却没有你？人民法院案例库收录了这起案子。",
    ],
    "承包地纠纷": [
        "承包地和村里起了纠纷，谁说了算？人民法院案例库这起案子说清楚了。",
    ],
    "外嫁女·分红": [
        "嫁出去的姑娘，分红就没了？这是人民法院案例库收录的真实案例。",
    ],
}
DEFAULT_OPENER = "人民法院案例库收录了一起农村集体资产的真实案例。"

# ---------------- 痛点开场钩子（按痛点定向切入） ----------------
PAIN_HOOKS = {
    "信息不对称": "村集体的家底你知道多少？征地款、分红、租金进了谁的口袋，很多村民根本看不见。",
    "民主决策虚置": "村里的钱怎么分，有些人连会都没开过、票都没投过，就被一纸方案定了。",
    "问题合同泛滥": "一亩地一年几块钱，一签就是几十年——这种合同你村有吗？",
    "证据毁灭取证难": "十几年的老账，票据、合同、会议记录全都不在，这钱还怎么算？",
    "群体性矛盾复杂": "村里分钱，有人拿得多、有人一分没有，凭啥？",
    "报复与压制": "告村里怕被穿小鞋，赢了官司怕输了日子——可该你的钱，不能就这么算了。",
    "执行落地难": "判决赢了，钱却一直执行不回来，这种情况你见过吗？",
}

# ---------------- 案情连接句 ----------------
FACT_LINKS = [
    "事情是这样的：",
    "案件经过是：",
    "这个案子，案情不复杂，但很典型：",
]

# ---------------- 判决句 ----------------
JUDGMENT_TMPLS = [
    "起诉到法院，{judge}。",
    "经两级法院审理，{judge}。",
    "官司一路打到法院，{judge}。",
]

# ---------------- 法理金句 ----------------
LEGAL_GISTS = [
    "法律的态度很明确：{gist_short}。",
    "法院判得很硬：{gist_short}。",
    "判决说了一句很重的话——{gist_short}。",
]

GIST_UPGRADE = [
    "村规民约再大，也大不过法律。",
    "投票决定、村民自治，都不能跟法律对着干。",
    "集体资产是全体成员的，谁也不能用一纸决议把它私分。",
]

def _shorten(text: str, limit: int = 90) -> str:
    """压缩裁判要旨为口语短句（截断 + 去书面腔，不以句号结尾，由模板补标点）"""
    t = (text or "").strip().lstrip("：:，,。 ").replace("\n", "").rstrip("。；;")
    if len(t) > limit:
        t = t[:limit].rsplit("，", 1)[0]
    return t.rstrip("。；;：:")


def generate_script(case: Case, style_no: int = 0) -> dict:
    """用模板生成一篇口播文案（返回 {rule_code, title, body, cta}）"""
    rnd = random.Random(hash((case.rule_code, style_no)) & 0xFFFF)
    scenario = case.scenario or "default"
    opener = rnd.choice(OPENERS.get(scenario, [DEFAULT_OPENER])).format(
        court=case.court or "法院"
    )
    pain_hook = ""
    for p in (case.pain_points or []):
        hook = PAIN_HOOKS.get(p)
        if hook:
            pain_hook = hook
            break
    if pain_hook:
        opener = f"{pain_hook}{opener}"
    fact_link = rnd.choice(FACT_LINKS)
    facts = (case.facts or "").strip().replace("\n", "")
    # 判决结果必须来自案例记录（result 字段），严禁按涉及金额自行推断
    if (case.result or "").strip():
        judge = case.result.strip().rstrip("。；;")
    else:
        judge = "法院依法作出裁判，该给的钱，一分不能少"
    judgment = rnd.choice(JUDGMENT_TMPLS).format(judge=judge)
    gist_short = _shorten(case.gist)
    legal = rnd.choice(LEGAL_GISTS).format(gist_short=gist_short)
    upgrade = rnd.choice(GIST_UPGRADE)
    cta = rnd.choice(SCENARIO_CTAS.get(scenario, DEFAULT_CTAS))

    title_hook = {
        "离婚妇女": [
            "离婚了，村里的钱就没你份？法院：不行！",
            "离了婚，村里就把她当外人？法院：身份不变，钱照分！",
        ],
        "外嫁女": [
            "嫁出去的姑娘，村里的分红没了？法院：得补！",
            "户口没迁走，分红凭什么没你？法院：一分不能少！",
        ],
        "外嫁女·股权证": [
            "一张股权证就想抹掉她的资格？法院：不认！",
            "没股权证就不是村里人？法院：看的是户口和土地！",
        ],
        "承包方消亡继承": [
            "老人去世，征地款就没了？法院：该给的还得给！",
            "人走了，承包地的钱还能要回来吗？法院：能！",
        ],
        "分配方案": [
            "村里分钱只按地分？法院：方案撤销！",
            "没地的人就该一分不得？法院：集体收益人人有份！",
        ],
        "养女资格": [
            "不是亲生就不给分钱？法院：她在村里生活就是村民！",
            "户口在、人在住，凭什么少分？法院：资格不看出生！",
        ],
        "户籍迁出": [
            "迁走户口就不是村里人？法院：资格不看户口本！",
            "为娃上学迁户口，回来就不认了？法院：综合认定，不能一刀切！",
        ],
        "外嫁女·分红": [
            "嫁出去的姑娘，分红就没你份？法院：得补上！",
            "户口没迁走，分红凭什么没你？法院：一分不能少！",
        ],
    }.get(scenario, [f"人民法院案例库真实案例：{_shorten(case.gist, 18)}"])

    # 用案例编号做种子，让同一场景的不同案例标题可复现但不重复
    seed_no = int(case.rule_code.replace("-", "")[-3:]) if case.rule_code else 0
    title = title_hook[seed_no % len(title_hook)]

    body = (
        f"{opener}{fact_link}{facts}"
        f"{judgment}{legal}{upgrade}"
    )
    return {
        "rule_code": case.rule_code,
        "title": title,
        "body": body,
        "cta": cta,
    }


def format_script(script: dict) -> str:
    """按用户示例格式排版"""
    return (
        f"入库编号：{script['rule_code']}\n"
        f"标题：{script['title']}\n"
        f"正文：\n{script['body']}\n\n"
        f"评论区互动：\n{script['cta']}"
    )
