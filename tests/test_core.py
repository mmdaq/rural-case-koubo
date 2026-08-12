"""单元测试：校验与去重"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.dedup import SeenStore, title_hash
from collector.extrastore import CrawlStore
from utils.validator import (
    check_doc_no,
    check_rule_code,
    content_plausible,
    independent_sources,
    is_rural_collective_theme,
    official_anchor,
    verify_case,
)
from collector.fallback import SEED_CASES
from generator.painpoints import enrich_case
from pipeline import _select_candidates, render_markdown
from unittest import mock

from utils.online_verify import (
    OnlineVerifyUnavailable,
    verify_cases,
    verify_rule_code,
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


class TestThemeFilter(unittest.TestCase):
    def test_all_seeds_pass(self):
        """人工核实的内置种子案例必须全部通过主题过滤"""
        failed = [s["rule_code"] for s in SEED_CASES if not is_rural_collective_theme(s)]
        self.assertEqual(failed, [], f"种子案例被误杀: {failed}")

    def test_offtopic_cases_rejected(self):
        """刑事/劳动/环保/行政/安全保障等跑题案例必须全部拦截"""
        off_topic = [
            {"rule_code": "2017-18-1-156-001", "title": "指导性案例87号：郭明升、郭明锋、孙淑标假冒注册商标案",
             "facts": "未经注册商标所有人许可，在同一种商品上使用与其注册商标相同的商标，销售金额巨大。",
             "gist": "假冒注册商标罪。",
             },
            {"rule_code": "2018-18-1-286-001", "title": "指导性案例105号：洪小强、洪礼沃、洪清泉、李志荣开设赌场案",
             "facts": "在微信群中拉人参与赌博，赌资流水三百余万元，违法所得予以没收。",
             "gist": "开设赌场罪。",
             },
            {"rule_code": "2021-18-2-466-001", "title": "指导性案例173号：北京市朝阳区自然之友环境研究所诉中国水电顾问集团新平开发有限公司等生态环境保护民事公益诉讼案",
             "facts": "水电站建设项目可能影响绿孔雀栖息地，法院判决停止建设。",
             "gist": "生态环境保护民事公益诉讼。",
             },
            {"rule_code": "2022-18-1-207-003", "title": "指导性案例194号：熊昌恒等侵犯公民个人信息案",
             "facts": "批量添加微信好友并出售微信号，违法所得二十余万元。",
             "gist": "侵犯公民个人信息罪。",
             },
            {"rule_code": "2022-18-2-186-001", "title": "指导性案例179号：聂美兰诉北京林氏兄弟文化有限公司确认劳动关系案",
             "facts": "双方签订合作设立茶叶项目协议，实际形成劳动关系。",
             "gist": "劳动关系认定。",
             },
            {"rule_code": "2020-18-2-370-001", "title": "李秋月等诉广州市花都区梯面镇红山村村民委员会违反安全保障义务责任纠纷案",
             "facts": "村民私自爬上集体所有的杨梅树采摘摔亡，家属起诉村委会索赔。",
             "gist": "违反安全保障义务责任纠纷。",
             },
            {"rule_code": "2017-18-3-004-001", "title": "指导性案例88号：张道文、陶仁等诉四川省简阳市人民政府侵犯客运人力三轮车经营权案",
             "facts": "行政机关对客运人力三轮车经营权作出限制。",
             "gist": "行政案件。",
             },
            {"rule_code": "2017-18-3-006-001", "title": "指导性案例89号：\"北雁云依\"诉济南市公安局历下区分局燕山派出所公安行政登记案",
             "facts": "父母为女儿取名\"北雁云依\"被拒绝办理户籍登记。",
             "gist": "公安行政登记。",
             },
        ]
        passed = [c["rule_code"] for c in off_topic if is_rural_collective_theme(c)]
        self.assertEqual(passed, [], f"跑题案例被误放行: {passed}")

    def test_body_fallback_accepts_rural_case(self):
        """标题无法判断时，正文强主题词组合仍可放行真实农村集体资产案例"""
        case = {
            "rule_code": "2024-07-2-044-008",
            "title": "某案例",
            "facts": "村民小组以民主议定程序通过征地补偿款分配方案，外嫁女未能取得集体经济组织成员资格对应的份额。",
            "gist": "征地补偿款分配不得损害成员权益。",
        }
        self.assertTrue(is_rural_collective_theme(case))


class TestOnlineVerify(unittest.TestCase):
    def _mock_post(self, payload):
        m = mock.Mock()
        m.json.return_value = payload
        return m

    def test_found(self):
        resp = self._mock_post({
            "code": 0,
            "data": {
                "totalCount": 1,
                "datas": [{
                    "cpws_al_id": "abc123",
                    "cpws_al_no": "2024-07-2-044-005",
                    "title": "张某梅诉某村民小组案",
                }],
            },
        })
        with mock.patch("utils.online_verify.requests.post", return_value=resp) as p:
            r = verify_rule_code("2024-07-2-044-005", "fake-token")
        self.assertTrue(r["found"])
        self.assertIn("content.html?id=abc123", r["official_url"])
        self.assertEqual(r["official_no"], "2024-07-2-044-005")
        body = p.call_args.kwargs["json"]
        self.assertEqual(body["searchParams"]["cpws_al_no"], "2024-07-2-044-005")
        self.assertEqual(p.call_args.kwargs["headers"]["faxin-cpws-al-token"], "fake-token")

    def test_not_found(self):
        resp = self._mock_post({"code": 0, "data": {"totalCount": 0, "datas": []}})
        with mock.patch("utils.online_verify.requests.post", return_value=resp):
            r = verify_rule_code("2024-07-2-044-999", "fake-token")
        self.assertFalse(r["found"])

    def test_token_expired_raises(self):
        resp = self._mock_post({"code": 401, "msg": "未登录"})
        with mock.patch("utils.online_verify.requests.post", return_value=resp):
            with self.assertRaises(OnlineVerifyUnavailable):
                verify_rule_code("2024-07-2-044-005", "bad-token")

    def test_network_error_raises(self):
        with mock.patch(
            "utils.online_verify.requests.post",
            side_effect=requests.ConnectionError("conn reset"),
        ):
            with self.assertRaises(OnlineVerifyUnavailable):
                verify_rule_code("2024-07-2-044-005", "fake-token")

    def test_batch_filters_not_found(self):
        def fake_post(url, **kwargs):
            code = kwargs["json"]["searchParams"]["cpws_al_no"]
            if code == "OK-001":
                return self._mock_post({
                    "code": 0,
                    "data": {
                        "totalCount": 1,
                        "datas": [{"cpws_al_id": "id1", "cpws_al_no": code, "title": "真实案例"}],
                    },
                })
            return self._mock_post({"code": 0, "data": {"totalCount": 0, "datas": []}})

        cases = [
            {"rule_code": "OK-001", "title": "案A"},
            {"rule_code": "BAD-002", "title": "案B"},
        ]
        with mock.patch("utils.online_verify.requests.post", side_effect=fake_post):
            passed, rejected, unavailable = verify_cases(cases, "fake-token", delay=0)
        self.assertFalse(unavailable)
        self.assertEqual(len(passed), 1)
        self.assertTrue(passed[0]["official_verified"])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["rule_code"], "BAD-002")


class TestDedup(unittest.TestCase):
    def test_title_hash_stable(self):
        self.assertEqual(title_hash("离婚回村 股权证"), title_hash("离婚回村，股权证"))

    def test_seen_store(self):
        tmp = os.path.join(tempfile.mkdtemp(), "test_seen.json")
        store = SeenStore(tmp)
        self.assertFalse(store.is_seen("2024-07-2-044-004", "蔡某珠案"))
        store.mark_seen("2024-07-2-044-004", "蔡某珠案")
        self.assertTrue(store.is_seen("2024-07-2-044-004", "蔡某珠案"))
        # 同编号即同一案例，标题差异不再影响去重
        self.assertTrue(store.is_seen("2024-07-2-044-004", "另一个标题"))

    def test_seen_store_no_code(self):
        """无入库编号（仅官方链接的最高院典型案例）以标题哈希为键"""
        tmp = os.path.join(tempfile.mkdtemp(), "test_seen_nocode.json")
        store = SeenStore(tmp)
        store.mark_seen("", "最高人民法院涉农民事典型案例")
        self.assertTrue(store.is_seen("", "最高人民法院涉农民事典型案例"))


class TestSelection(unittest.TestCase):
    def _store_with_pushed(self, pushed_map: dict):
        """pushed_map: {rule_code: 距今天数}"""
        tmp = os.path.join(tempfile.mkdtemp(), "seen.json")
        store = SeenStore(tmp)
        now = datetime.now()
        for code, days in pushed_map.items():
            store.mark_seen(code, f"案{code}")
            store.data["cases"][code]["pushed_at"] = (now - timedelta(days=days)).isoformat()
        return store

    def test_recently_pushed_not_reselected_when_pool_enough(self):
        """昨天推送过的案例，在有足够未推送案例时不再选入"""
        store = self._store_with_pushed({c: 1 for c in "ABCDE"})
        cases = [{"rule_code": c, "title": f"案{c}"} for c in "ABCDEFGHIJKLM"]
        picked = _select_candidates(cases, store, 5, cooldown_days=7, min_gap_days=1)
        codes = [d["rule_code"] for d in picked]
        self.assertEqual(sorted(codes), sorted("FGHIJ"))

    def test_rotation_oldest_first_after_cooldown(self):
        """冷却期外的案例按最久未推送优先轮换，最近推送的兜底"""
        store = self._store_with_pushed({"A": 10, "B": 10, "C": 8, "D": 8, "E": 1, "F": 1})
        cases = [{"rule_code": c, "title": f"案{c}"} for c in "ABCDEF"]
        picked = _select_candidates(cases, store, 5, cooldown_days=7, min_gap_days=1)
        codes = [d["rule_code"] for d in picked]
        self.assertEqual(codes[:4], ["A", "B", "C", "D"])
        self.assertEqual(len(codes), 5)

    def test_no_repeat_same_code_different_title(self):
        """同入库编号即使标题不同也算同一案例；有替代案例时不重复选它"""
        store = SeenStore(os.path.join(tempfile.mkdtemp(), "seen.json"))
        store.mark_seen("2024-07-2-044-002", "游某乙诉某村第二村民小组案")
        cases = [{"rule_code": "2024-07-2-044-002", "title": "游某甲、游某乙诉某村第二村民小组案"}]
        cases += [{"rule_code": c, "title": f"案{c}"} for c in "ABCDEFGHIJKLM"]
        picked = _select_candidates(cases, store, 5, cooldown_days=7, min_gap_days=1)
        codes = [d["rule_code"] for d in picked]
        self.assertNotIn("2024-07-2-044-002", codes)
        self.assertEqual(len(codes), 5)


class TestCrawlStore(unittest.TestCase):
    def test_mark_and_persist(self):
        path = os.path.join(tempfile.mkdtemp(), "crawled.json")
        cs = CrawlStore(path)
        self.assertFalse(cs.is_crawled("https://a.example/x"))
        cs.mark_crawled("https://a.example/x")
        self.assertTrue(cs.is_crawled("https://a.example/x"))
        # 重新加载后仍记得（持久化）
        cs2 = CrawlStore(path)
        self.assertTrue(cs2.is_crawled("https://a.example/x"))


class TestOfficialAnchor(unittest.TestCase):
    def test_official_link(self):
        case = {
            "rule_code": "2024-07-2-044-003",
            "official_link": "https://www.court.gov.cn/zixun/xiangqing/423762.html",
            "source_urls": ["https://www.court.gov.cn/zixun/xiangqing/423762.html"],
        }
        ok, kind = official_anchor(case)
        self.assertTrue(ok)
        self.assertEqual(kind, "官方链接")

    def test_rule_code_plus_doc_no(self):
        case = {
            "rule_code": "2023-11-2-044-001",
            "doc_no": "（2021）鲁16民终1155号",
            "source_urls": ["https://taxdy.cn/h-nd-293634.html"],
        }
        ok, kind = official_anchor(case)
        self.assertTrue(ok)
        self.assertEqual(kind, "入库编号+裁判文书号")

    def test_rule_code_plus_two_sources(self):
        case = {
            "rule_code": "2024-07-2-044-001",
            "doc_no": "",
            "source_urls": [
                "https://www.055110.com/fl/3/5977.html",
                "https://taxdy.cn/h-nd-294147.html",
            ],
        }
        ok, kind = official_anchor(case)
        self.assertTrue(ok)
        self.assertEqual(kind, "入库编号+多源交叉")

    def test_no_anchor_fails(self):
        case = {
            "rule_code": "2024-07-2-044-001",
            "doc_no": "",
            "source_urls": ["https://www.055110.com/fl/3/5977.html"],
        }
        ok, _ = official_anchor(case)
        self.assertFalse(ok)

    def test_verify_requires_anchor(self):
        case = {
            "rule_code": "2024-07-2-044-001",
            "title": "张某诉某村委会案",
            "facts": "法院审理集体经济组织成员资格与土地承包经营权纠纷，判决驳回起诉",
            "reasoning": "裁判理由：不属于民事受案范围",
            "gist": "裁判要旨：未实际取得承包地争议应申请行政解决",
            "doc_no": "",
            "source_urls": ["https://www.055110.com/fl/3/5977.html"],
        }
        r = verify_case(case, require_official_anchor=True)
        self.assertFalse(r["ok"])
        self.assertTrue(any("官方可查锚点" in i for i in r["issues"]))

    def test_official_link_only_without_code_passes(self):
        case = {
            "rule_code": "",
            "title": "最高人民法院发布涉农民事典型案例",
            "facts": "法院审理集体经济组织收益分配纠纷并作出生效裁判，最高人民法院对外发布",
            "reasoning": "裁判理由",
            "gist": "裁判要旨：集体收益分配不得损害成员权益",
            "doc_no": "",
            "official_link": "https://www.court.gov.cn/zixun/xiangqing/423762.html",
            "source_urls": ["https://www.court.gov.cn/zixun/xiangqing/423762.html"],
        }
        r = verify_case(case, require_official_anchor=True)
        self.assertTrue(r["ok"], r["issues"])


class TestPainPoints(unittest.TestCase):
    def test_enrich_case(self):
        c = enrich_case({"scenario": "外嫁女·分红", "rule_code": "x"})
        self.assertEqual(c["subtype"], "外嫁女成员资格（集体收益分红）")
        self.assertIn("民主决策虚置", c["pain_points"])


class TestRenderFormat(unittest.TestCase):
    def test_render_pure_script_with_reference_line(self):
        scripts = [
            {
                "rule_code": "2024-07-2-044-005",
                "title": "离婚回村，村里说股权证没你名就没钱拿",
                "body": "正文内容。",
                "cta": "你们村有没有类似“离婚就不给分钱”的规矩？评论区说出来，我帮你看看合不合法。",
                "case": {
                    "title": "张某梅诉某村民小组案",
                    "subtype": "外嫁女成员资格（产权改革股权证）",
                    "official_link": "http://dyzy.sdcourt.gov.cn/x.pdf",
                    "source_names": ["东营中院转载案例全文"],
                },
            }
        ]
        md = render_markdown(scripts, "2026-08-11")
        # 编号/链接单独一行，位于标题前
        self.assertIn("入库编号：2024-07-2-044-005\n官方链接：http://dyzy.sdcourt.gov.cn/x.pdf\n标题：", md)
        # 评论区互动在正文之后
        self.assertIn("正文：正文内容。\n\n评论区互动：", md)
        # 纯口播：不包含案例核查记录附录
        self.assertNotIn("附：案例核查记录", md)


if __name__ == "__main__":
    unittest.main()
