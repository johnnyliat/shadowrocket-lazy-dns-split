"""补集守卫单元测试：单标签策略、父子重叠报告、覆盖判定边界。

单标签策略冻结：已审核清单内保留、通用顶级域失败、清单外丢弃。
覆盖判定边界冻结（审计 F-02 的如实记录）：父规则覆盖子候选，子规则
不否决父候选——后者是已知算法边界，由成品顺序保护（代理表在补集前），
测试将其冻结为文档化行为，防止未来"修复"时误改语义。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compile_supplement as cs


class TestClassifySingleLabel(unittest.TestCase):
    def test_allowed_kept(self):
        for v in ["alibaba", "tmall", "icbc", "yun", "xn--fiqs8s"]:
            self.assertEqual(cs.classify_single_label(v), "keep", v)

    def test_danger_fails(self):
        for v in ["com", "net", "org", "edu", "gov", "arpa", "biz", "info"]:
            self.assertEqual(cs.classify_single_label(v), "fail", v)

    def test_unknown_dropped(self):
        for v in ["example", "newbrand", "xn--newtld123"]:
            self.assertEqual(cs.classify_single_label(v), "drop", v)

    def test_allowlist_size(self):
        # 清单规模冻结：增减须经人工审核（改动此处即改动策略）
        self.assertEqual(len(cs.ALLOWED_SINGLE_LABELS), 44)


class TestDescendantReport(unittest.TestCase):
    def test_reports_proxy_children_under_direct_parent(self):
        entries = [("suffix", "example.com"), ("suffix", "other.org"),
                   ("exact", "solo.net")]
        hits = cs.descendant_report(entries, {"sub.example.com", "deep.sub.example.com"},
                                    {"api.example.com"})
        self.assertEqual(set(hits), {"example.com"})
        self.assertEqual(sorted(hits["example.com"]),
                         ["api.example.com", "deep.sub.example.com", "sub.example.com"])

    def test_parent_and_equal_do_not_report(self):
        entries = [("suffix", "sub.example.com")]
        hits = cs.descendant_report(entries, {"example.com"}, {"sub.example.com"})
        self.assertEqual(hits, {})

    def test_exact_entries_never_parents(self):
        entries = [("exact", "example.com")]
        hits = cs.descendant_report(entries, {"sub.example.com"}, set())
        self.assertEqual(hits, {})


class TestCoversCandidateBoundary(unittest.TestCase):
    def test_parent_suffix_covers_child(self):
        cov = cs.Coverage()
        cov.suffixes.add("example.com")
        self.assertTrue(cov.covers_candidate("suffix", "sub.example.com"))
        self.assertTrue(cov.covers_candidate("exact", "sub.example.com"))

    def test_child_does_not_block_parent(self):
        # 已知边界（F-02）：代理子域不否决父域直连候选；顺序保护兜底。
        cov = cs.Coverage()
        cov.suffixes.add("sub.example.com")
        cov.exacts.add("api.example.com")
        self.assertFalse(cov.covers_candidate("suffix", "example.com"))

    def test_keyword_covers(self):
        cov = cs.Coverage()
        cov.keywords.add("example")
        self.assertTrue(cov.covers_candidate("suffix", "myexample.com"))

    def test_exact_does_not_cover_suffix(self):
        cov = cs.Coverage()
        cov.exacts.add("example.com")
        self.assertFalse(cov.covers_candidate("suffix", "example.com"))


class TestReadPreviousEntries(unittest.TestCase):
    def test_missing_returns_none(self):
        self.assertIsNone(cs.read_previous_entries("/nonexistent/SupplementA.list"))


if __name__ == "__main__":
    unittest.main()
