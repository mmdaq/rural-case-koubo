"""单元测试：校验与去重"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.dedup import SeenStore, title_hash
from utils.validator import (
    check_doc_no,
    check_rule_code,
    content_plausible,
    independent_sources,
    verify_case,
)


class TestValidator(unittest.TestCase):
    def test_rule_code_ok(self):
        self.assertTrue(check_rule_code("2024-07-2-044-005"))
        self.assertTrue(check_rule_code("2023-16-2-044-002"))

    def test_rule_code_bad(self):
        self.assertFalse(check_rule_code("2024-7-2-44-5"))
        self.assertFalse(check_rule_code("abcd"))
        self.assertFalse(check_rule_code(""))

    def test_doc_no_ok(self):
        self.assertTrue(check_doc_no("（2019）闽07民终1227号"))
        self.assertTrue(check_doc_no("(2020)苏0923民初2646号"))

    def test_doc_no_empty_allowed(self):
        self.assertTrue(check_doc_no(""))

    def test_plausible(self):
        case = {"facts": "法院判决集体经济组织成员应获得土地征收补偿款", "gist": "成员资格", "title": "x"}
        self.assertTrue(content_plausible(case))

    def test_independent_sources(self):
        case = {"source_urls": ["https://a.gov.cn/x", "https://b.gov.cn/y", "https://a.gov.cn/z"]}
        self.assertEqual(independent_sources(case), 2)

    def test_verify_full(self):
        case = {
            "rule_code": "2024-07-2-044-004",
            "title": "蔡某珠诉某村五组案",
            "facts": "法院认定集体经济组织成员资格，判决支付土地征收补偿款10000元",
            "reasoning": "判决理由",
            "gist": "裁判要旨：妇女不因离异丧失成员资格",
            "source_urls": ["https://a.gov.cn/x", "https://b.gov.cn/y"],
        }
        r = verify_case(case, min_sources=2)
        self.assertTrue(r["ok"], r["issues"])


class TestDedup(unittest.TestCase):
    def test_title_hash_stable(self):
        self.assertEqual(title_hash("离婚回村 股权证"), title_hash("离婚回村，股权证"))

    def test_seen_store(self, tmp="/tmp/test_seen.json"):
        if os.path.exists(tmp):
            os.remove(tmp)
        store = SeenStore(tmp)
        self.assertFalse(store.is_seen("2024-07-2-044-004", "蔡某珠案"))
        store.mark_seen("2024-07-2-044-004", "蔡某珠案")
        self.assertTrue(store.is_seen("2024-07-2-044-004", "蔡某珠案"))
        # 同编号不同标题视为不同案例
        self.assertFalse(store.is_seen("2024-07-2-044-004", "另一个标题"))
        os.remove(tmp)


if __name__ == "__main__":
    unittest.main()
