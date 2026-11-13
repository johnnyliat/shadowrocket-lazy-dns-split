"""严格规则语法单元测试：no-resolve 精确判定与域名校验。

回归锚点：2026-11-13 外部审计复现的两种绕过写法（#no-resolve、
no-resolve-extra）必须判为无守卫；QX 表的组名第三字段（Apple/China/
Global/WeChat）必须接受，不得误杀。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from build_common import atomic_write_text, ip_rule_guard_status, validate_domain_value


class TestIpRuleGuardStatus(unittest.TestCase):
    def assert_status(self, line, expected):
        self.assertEqual(ip_rule_guard_status(line), expected, line)

    def test_audit_bypass_examples_are_unguarded(self):
        # 审计 F-01 的两个复现用例：子串判定会误放行，精确判定必须拦下
        self.assert_status("IP-CIDR,1.2.3.0/24,DIRECT,#no-resolve", False)
        self.assert_status("IP-CIDR,1.2.3.0/24,DIRECT,no-resolve-extra", False)
        self.assert_status("IP-CIDR,1.2.3.0/24,DIRECT,xno-resolve", False)

    def test_guarded_shapes(self):
        self.assert_status("IP-CIDR,1.2.3.0/24,DIRECT,no-resolve", True)
        self.assert_status("IP-CIDR,1.2.3.0/24,no-resolve", True)
        self.assert_status("IP-CIDR,1.2.3.0/24,Global,no-resolve", True)
        self.assert_status("IP-CIDR,1.2.3.0/24,Apple,no-resolve", True)
        self.assert_status("IP-CIDR,1.2.3.0/24,China,no-resolve", True)
        self.assert_status("IP-CIDR,1.2.3.0/24,WeChat,no-resolve", True)
        self.assert_status("IP-ASN,132203,WeChat,no-resolve", True)
        self.assert_status("IP-ASN,32934,no-resolve", True)
        self.assert_status("IP6-CIDR,2001:db8::/32,no-resolve", True)
        self.assert_status("IP-CIDR6,2001:db8::/32,no-resolve", True)
        self.assert_status("GEOIP,CN,DIRECT,no-resolve", True)
        self.assert_status("ip-cidr,1.2.3.0/24,direct,NO-RESOLVE", True)

    def test_unguarded_shapes(self):
        self.assert_status("IP-CIDR,1.2.3.0/24,DIRECT", False)
        self.assert_status("IP-CIDR,1.2.3.0/24,Global", False)
        self.assert_status("GEOIP,CN,DIRECT", False)

    def test_non_ip_lines(self):
        self.assertIsNone(ip_rule_guard_status("DOMAIN-SUFFIX,example.com,DIRECT"))
        self.assertIsNone(ip_rule_guard_status("RULE-SET,https://example.com/x.list,DIRECT"))
        self.assertIsNone(ip_rule_guard_status("# IP-CIDR,1.2.3.0/24,DIRECT"))
        self.assertIsNone(ip_rule_guard_status("// IP-CIDR,1.2.3.0/24,DIRECT"))
        self.assertIsNone(ip_rule_guard_status(""))
        self.assertIsNone(ip_rule_guard_status("USER-AGENT,IP-CIDR,DIRECT"))

    def test_logical_lines(self):
        self.assert_status(
            "AND,(IP-CIDR,1.2.3.0/24,no-resolve),(DOMAIN-SUFFIX,example.com),PROXY",
            True)
        self.assert_status(
            "AND,(IP-CIDR,1.2.3.0/24),(DOMAIN-SUFFIX,example.com),PROXY",
            False)
        # no-resolve 出现在域名值里不算 IP 条件的守卫（同组判定）
        self.assert_status(
            "AND,(DOMAIN,no-resolve.example.com),(IP-CIDR,1.2.3.0/24),PROXY",
            False)
        self.assertIsNone(
            ip_rule_guard_status("AND,(DOMAIN,example.com),(DOMAIN-SUFFIX,foo.com),PROXY"))

    def test_malformed_raises(self):
        for bad in [
            "IP-CIDR,999.1.1.0/24,DIRECT,no-resolve",
            "IP-CIDR,1.2.3.0/24,DIRECT,no-resolve,EXTRA",
            "IP-CIDR,1.2.3.0/24,DIRECT,no-resolve,no-resolve",
            "IP-ASN,abc,no-resolve",
            "IP-ASN,0,no-resolve",
            "GEOIP,CHN,DIRECT,no-resolve",
            "GEOIP,cn,DIRECT,no-resolve",
            "IP-CIDR,,DIRECT,no-resolve",
            "IP-CIDR,1.2.3.0/24,DI RECT,no-resolve",
            "AND,(IP-CIDR,1.2.3.0/24,no-resolve",
        ]:
            with self.assertRaises(ValueError, msg=bad):
                ip_rule_guard_status(bad)


class TestValidateDomainValue(unittest.TestCase):
    def test_valid(self):
        for v in ["example.com", "a.b.c.d", "x", "xn--fiqs8s", "a1-b2.example-1.com",
                  "a" * 63 + ".com", "1.2.3.4.example"]:
            self.assertTrue(validate_domain_value(v), v)

    def test_invalid(self):
        for v in ["", "EXAMPLE.COM", "exa_mple.com", "-example.com", "example-.com",
                  "example..com", ".example.com", "example.com.", "exa mple.com",
                  "example/com", "a" * 64 + ".com", "x" * 254,
                  "*.example.com", "example.com:8080"]:
            self.assertFalse(validate_domain_value(v), v)


class TestAtomicWriteText(unittest.TestCase):
    def test_write_and_replace(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.txt")
            atomic_write_text(path, "one\n")
            self.assertEqual(open(path, encoding="utf-8").read(), "one\n")
            atomic_write_text(path, "two\n")
            self.assertEqual(open(path, encoding="utf-8").read(), "two\n")
            leftovers = [n for n in os.listdir(d) if n.startswith(".tmp-")]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
