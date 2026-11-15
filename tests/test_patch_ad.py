"""patch_ad 去广告规则值校验测试。

单标签域名值（如 com）会拦截整个顶级域：值校验须与补集/手动规则等
其他域名入口同口径。fail() 以 SystemExit 停发，测试中收敛 stderr。
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import patch_ad


class _FailHelper:
    def assert_fails(self, fn, *args):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                fn(*args)


class TestValidateAdValue(_FailHelper, unittest.TestCase):
    def test_valid_suffix_passes(self):
        self.assertEqual(
            patch_ad.validate_ad_value("DOMAIN-SUFFIX", "ads.example.com", "x"),
            "DOMAIN-SUFFIX,ads.example.com,REJECT")

    def test_uppercase_normalized(self):
        self.assertEqual(
            patch_ad.validate_ad_value("DOMAIN-SUFFIX", "Ads.Example.COM", "x"),
            "DOMAIN-SUFFIX,ads.example.com,REJECT")

    def test_single_label_rejected(self):
        for bad in ("com", "cn", "COM"):
            self.assert_fails(patch_ad.validate_ad_value,
                              "DOMAIN-SUFFIX", bad, "x")

    def test_malformed_values_rejected(self):
        for bad in ("foo_bar.com", "*.example.com", "example.com.",
                    ".example.com", "a..b.com", "exa mple.com",
                    "example.com:8080"):
            self.assert_fails(patch_ad.validate_ad_value,
                              "DOMAIN-SUFFIX", bad, "x")

    def test_ip_cidr_validated(self):
        self.assertEqual(
            patch_ad.validate_ad_value("IP-CIDR", "1.2.3.0/24", "x"),
            "IP-CIDR,1.2.3.0/24,REJECT,no-resolve")
        self.assert_fails(patch_ad.validate_ad_value,
                          "IP-CIDR", "999.1.1.0/24", "x")


class TestLoadAdRulesWiring(_FailHelper, unittest.TestCase):
    def test_bad_value_fails_fast(self):
        # 坏值在解析时即停发，不等到 5 万条计数检查。
        with tempfile.NamedTemporaryFile("w", encoding="utf-8",
                                         suffix=".conf", delete=False) as f:
            f.write("[Rule]\nDOMAIN-SUFFIX,ok.example.com,Reject\n"
                    "DOMAIN-SUFFIX,com,Reject\n")
            path = f.name
        try:
            self.assert_fails(patch_ad.load_ad_rules, path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
