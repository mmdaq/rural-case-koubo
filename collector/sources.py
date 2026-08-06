"""数据源定义：人民法院案例库官网 / 最高法官网 / 搜索引擎转载检索"""
from .models import Case

# 人民法院案例库官网（需登录，接口不稳定，仅作探测）
RMFYALK_HOME = "https://rmfyalk.court.gov.cn"
RMFYALK_SEARCH = "https://rmfyalk.court.gov.cn/web/rmfyalk/search"
# 最高人民法院官网 · 涉农民事典型案例栏目
COURT_GOV_AGRICULTURE = "https://www.court.gov.cn/zixun/xiangqing/423762.html"
# 搜索引擎模板（检索案例库转载内容）
SEARCH_ENGINES = {
    "bing": "https://www.bing.com/search?q={q}&setlang=zh-hans",
    "baidu": "https://www.baidu.com/s?wd={q}",
}

# 每个入库案例应具备的来源（用于交叉验证）
SOURCE_HINTS = {
    "rmfyalk": "rmfyalk.court.gov.cn",
    "court_gov": "court.gov.cn",
}


def make_case(rule_code: str, title: str, **kwargs) -> Case:
    """统一构造 Case（保证字段完整）"""
    return Case(rule_code=rule_code, title=title, **kwargs)
