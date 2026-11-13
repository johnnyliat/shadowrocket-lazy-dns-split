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


class TestCollapse(unittest.TestCase):
    def test_child_suffix_removed(self):
        entries = [("suffix", "example.com"), ("suffix", "sub.example.com")]
        self.assertEqual(cs.collapse(entries), [("suffix", "example.com")])

    def test_exact_covered_by_suffix_removed(self):
        entries = [("suffix", "example.com"), ("exact", "a.example.com")]
        self.assertEqual(cs.collapse(entries), [("suffix", "example.com")])

    def test_exact_same_value_as_suffix_removed(self):
        entries = [("suffix", "example.com"), ("exact", "example.com")]
        self.assertEqual(cs.collapse(entries), [("suffix", "example.com")])

    def test_siblings_and_reverse_kept(self):
        entries = [("suffix", "a.com"), ("suffix", "b.com"),
                   ("exact", "example.com"), ("suffix", "sub.example.com")]
        self.assertEqual(cs.collapse(entries), sorted(set(entries)))

    def test_duplicates_and_empty(self):
        self.assertEqual(cs.collapse([("suffix", "a.com"), ("suffix", "a.com")]),
                         [("suffix", "a.com")])
        self.assertEqual(cs.collapse([]), [])

    def test_multi_level_and_idempotent(self):
        entries = [("suffix", "x.y.example.com"), ("suffix", "y.example.com"),
                   ("suffix", "example.com"), ("exact", "z.example.com")]
        once = cs.collapse(entries)
        self.assertEqual(once, [("suffix", "example.com")])
        self.assertEqual(cs.collapse(once), once)


class TestContradictionReport(unittest.TestCase):
    def _cov(self, suffixes=(), exacts=()):
        cov = cs.Coverage()
        cov.suffixes.update(suffixes)
        cov.exacts.update(exacts)
        return cov

    def test_full_contradiction(self):
        direct = self._cov(["example.com"])
        full, partial = cs.contradiction_report(
            [("suffix", "example.com", "GFWList")], direct,
            self._cov(), self._cov())
        self.assertEqual(full, [("suffix", "example.com", "GFWList", "example.com")])
        self.assertEqual(partial, [])

    def test_child_of_direct_parent_reported_with_parent(self):
        direct = self._cov(["example.com"])
        full, _ = cs.contradiction_report(
            [("suffix", "sub.example.com", "ACL4SSR")], direct,
            self._cov(), self._cov())
        self.assertEqual(full, [("suffix", "sub.example.com", "ACL4SSR", "example.com")])

    def test_proxy_or_manual_coverage_excluded(self):
        direct = self._cov(["example.com"])
        proxy_side = self._cov(["example.com"])
        full, partial = cs.contradiction_report(
            [("suffix", "example.com", "GFWList")], direct,
            proxy_side, self._cov())
        self.assertEqual((full, partial), ([], []))
        manual = self._cov(["example.com"])
        full, partial = cs.contradiction_report(
            [("suffix", "example.com", "GFWList")], direct,
            self._cov(), manual)
        self.assertEqual((full, partial), ([], []))

    def test_partial_when_proxy_child_exists(self):
        direct = self._cov(["example.com"])
        proxy_side = self._cov(["sub.example.com"])
        full, partial = cs.contradiction_report(
            [("suffix", "example.com", "GFWList")], direct,
            proxy_side, self._cov())
        self.assertEqual(full, [])
        self.assertEqual(partial, [("suffix", "example.com", "GFWList", "example.com")])

    def test_bare_tld_skipped_and_duplicates_merged(self):
        direct = self._cov(["example.com"])
        full, partial = cs.contradiction_report(
            [("suffix", "hk", "GFWList"),
             ("suffix", "example.com", "ACL4SSR"),
             ("suffix", "example.com", "GFWList")],
            direct, self._cov(), self._cov())
        self.assertEqual(full, [("suffix", "example.com", "ACL4SSR", "example.com")])
        self.assertEqual(partial, [])

    def test_exact_entry_covered_by_direct_suffix(self):
        direct = self._cov(["example.com"])
        full, _ = cs.contradiction_report(
            [("exact", "a.example.com", "ACL4SSR")], direct,
            self._cov(), self._cov())
        self.assertEqual(full, [("exact", "a.example.com", "ACL4SSR", "example.com")])


if __name__ == "__main__":
    unittest.main()
