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
    amount: str = ""          # 涉及金额（用于文案增强）
    facts: str = ""           # 基本案情
    reasoning: str = ""       # 裁判理由
    gist: str = ""            # 裁判要旨
    source_urls: list = field(default_factory=list)
    source_names: list = field(default_factory=list)
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Case":
        return Case(**{k: v for k, v in d.items() if k in Case.__dataclass_fields__})
