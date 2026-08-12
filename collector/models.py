"""案例数据模型"""
from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class Case:
    rule_code: str            # 人民法院案例库入库编号，如 2024-07-2-044-005
    title: str                # 案名，如 蔡某珠诉某村五组侵害集体经济组织成员权益纠纷案
    keywords: list = field(default_factory=list)
    court: str = ""           # 裁判法院
    doc_no: str = ""          # 裁判文书号
    province: str = ""        # 省份
    scenario: str = ""        # 主题标签：离婚妇女/外嫁女/继承/分配方案/户籍/养女…
    subtype: str = ""         # 案件细分类型（七类）：征地补偿款分配争议/外嫁女成员资格…
    pain_points: list = field(default_factory=list)  # 对应用户痛点标签（信息不对称/民主决策虚置/…）
    amount: str = ""          # 涉及金额（用于文案增强）
    facts: str = ""           # 基本案情
    reasoning: str = ""       # 裁判理由
    gist: str = ""            # 裁判要旨
    result: str = ""          # 判决结果（真实裁判结论，禁止模板/LLM自行推断金额）
    official_link: str = ""   # 官方可查链接（法院官网/court.gov.cn/案例库原文，无则留空）
    official_verified: bool = False   # 是否已在人民法院案例库官网核对通过
    official_verify_at: str = ""      # 官网核对时间
    official_no: str = ""             # 官网核对返回的入库编号
    official_title: str = ""          # 官网核对返回的案例标题
    case_source: str = ""     # 案例参考来源：人民法院案例库 / 最高院典型案例 / 网络转载
    source_urls: list = field(default_factory=list)
    source_names: list = field(default_factory=list)
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Case":
        return Case(**{k: v for k, v in d.items() if k in Case.__dataclass_fields__})
