"""patch_lists 单元测试：镜像补丁语义与失败守卫（下载以夹具替换）。

覆盖：QX 表 IP 规则补 no-resolve、已守卫表零改动、HTML 错误页、
行数异常、行内注释与语法畸形、QX 补丁数下限、修饰符位置放错、
与上一版的差异计数、清单解析。网络 I/O 全部以夹具替换，不发真实请求。
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import patch_lists

URL = "https://example.com/upstream.list"


class PatchListsFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_base = patch_lists.BASE
        self.old_fetch = patch_lists.fetch
        patch_lists.BASE = self.tmp.name

    def tearDown(self):
        patch_lists.BASE = self.old_base
        patch_lists.fetch = self.old_fetch
        self.tmp.cleanup()

    def build_quiet(self, body, local="rules/Test.list"):
        patch_lists.fetch = lambda url: body
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            patch_lists.build(URL, local)
        return buf.getvalue()

    def assert_build_fails(self, body, local="rules/Test.list"):
        patch_lists.fetch = lambda url: body
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                patch_lists.build(URL, local)

    def read_out(self, local="rules/Test.list"):
        with open(os.path.join(self.tmp.name, local), encoding="utf-8") as f:
            return f.read()


class TestPatching(PatchListsFixture):
    def test_qx_lines_get_no_resolve(self):
        body = ("# comment\n"
                "DOMAIN-SUFFIX,example.com\n"
                "IP-CIDR,1.2.3.0/24,Global\n"
                "IP-ASN,12345,China\n"
                "GEOIP,CN,DIRECT\n")
        self.build_quiet(body)
        out = self.read_out().splitlines()
        self.assertEqual(len(out), len(body.splitlines()) + 1)  # 首行镜像说明
        self.assertIn("IP-CIDR,1.2.3.0/24,Global,no-resolve", out)
        self.assertIn("IP-ASN,12345,China,no-resolve", out)
        self.assertIn("GEOIP,CN,DIRECT,no-resolve", out)
        self.assertIn("DOMAIN-SUFFIX,example.com", out)
        self.assertTrue(out[0].startswith("# Mirror of "))

    def test_guarded_table_unchanged(self):
        body = ("DOMAIN-SUFFIX,example.com\n"
                "IP-CIDR,1.2.3.0/24,DIRECT,no-resolve\n"
                "IP-CIDR,10.0.0.0/8,Global,no-resolve\n")
        self.build_quiet(body)
        out = self.read_out().splitlines()[1:]
        self.assertEqual(out, body.splitlines())

    def test_misplaced_no_resolve_fails(self):
        # 修饰符位置放错：补丁后出现重复 no-resolve，补后断言必须拦下
        body = ("DOMAIN-SUFFIX,example.com\n"
                "IP-CIDR,1.2.3.0/24,no-resolve,DIRECT\n"
                "DOMAIN-SUFFIX,other.com\n")
        self.assert_build_fails(body)

    def test_diff_note_counts_changes(self):
        body_a = "# c\nDOMAIN-SUFFIX,a.com\nIP-CIDR,1.2.3.0/24,no-resolve\n"
        body_b = "# c\nDOMAIN-SUFFIX,b.com\nIP-CIDR,1.2.3.0/24,no-resolve\n"
        self.build_quiet(body_a)
        log = self.build_quiet(body_b)
        self.assertIn("diff +1/-1", log)


class TestFailureGuards(PatchListsFixture):
    def test_html_page_fails(self):
        self.assert_build_fails("<!DOCTYPE html>\n<html>\n404 Not Found\n")

    def test_short_table_fails(self):
        self.assert_build_fails("DOMAIN-SUFFIX,a.com\nDOMAIN-SUFFIX,b.com\n")

    def test_commented_unguarded_ip_fails(self):
        body = ("DOMAIN-SUFFIX,a.com\n"
                "IP-CIDR,1.2.3.0/24,DIRECT # 注释\n"
                "DOMAIN-SUFFIX,b.com\n")
        self.assert_build_fails(body)

    def test_malformed_ip_fails(self):
        body = ("DOMAIN-SUFFIX,a.com\n"
                "IP-CIDR,999.1.1.0/24,DIRECT\n"
                "DOMAIN-SUFFIX,b.com\n")
        self.assert_build_fails(body)

    def test_min_patched_floor(self):
        good = "".join(f"IP-CIDR,10.{i}.0.0/16,Apple\n" for i in range(13))
        good += "DOMAIN-SUFFIX,a.com\n"
        self.build_quiet(good, "rules/Apple.list")
        short = "".join(f"IP-CIDR,10.{i}.0.0/16,Apple\n" for i in range(12))
        short += "DOMAIN-SUFFIX,a.com\n"
        self.assert_build_fails(short, "rules/Apple.list")


class TestLoadManifest(PatchListsFixture):
    def test_parses_entries(self):
        with open(os.path.join(self.tmp.name, "mirrors.txt"),
                  "w", encoding="utf-8") as f:
            f.write("# comment\nhttps://up.example/a.list rules/A.list\n"
                    "https://up.example/b.list rules/B.list\n")
        self.assertEqual(patch_lists.load_manifest(),
                         [("https://up.example/a.list", "rules/A.list"),
                          ("https://up.example/b.list", "rules/B.list")])

    def test_duplicate_url_fails(self):
        with open(os.path.join(self.tmp.name, "mirrors.txt"),
                  "w", encoding="utf-8") as f:
            f.write("https://up.example/a.list rules/A.list\n"
                    "https://up.example/a.list rules/B.list\n")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                patch_lists.load_manifest()


if __name__ == "__main__":
    unittest.main()
