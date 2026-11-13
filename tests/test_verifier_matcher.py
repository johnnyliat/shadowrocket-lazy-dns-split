"""独立验证引擎单元测试：在微型成品上验证裁决语义。

覆盖：精确/后缀/关键字/通配的首命中顺序、GEOIP 裸与带守卫两种模式、
FINAL 兜底、no-resolve 前提判定、DNS 联动锁。真实成品的回归由 CI 里
verify_routing.py 的全量运行与固定样本覆盖。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import verify_routing as vr

SELF = "https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/"

GENERAL = """[General]
dns-server = {dns}
direct-dns-server = https://dns.alidns.com/dns-query,https://doh.pub/dns-query
proxy-dns-server = https://dns.alidns.com/dns-query,https://doh.pub/dns-query
fallback-dns-server = https://dns.alidns.com/dns-query,https://doh.pub/dns-query
dns-fallback-system = false
ipv6 = false
allow-dns-all = false
dns-direct-system = false
dns-direct-fallback-proxy = true
private-ip-answer = true
"""

TABLE = """# fixture table
DOMAIN-SUFFIX,proxy.example
DOMAIN,exact.example
DOMAIN-KEYWORD,kwmark
DOMAIN-WILDCARD,*.wild.example
IP-CIDR,1.2.3.0/24,no-resolve
USER-AGENT,TestAgent
"""

RULES = """[Rule]
DOMAIN-SUFFIX,manual.example,DIRECT
RULE-SET,{self}rules/T1.list,PROXY
RULE-SET,{self}rules/SupplementA.list,DIRECT
RULE-SET,{self}rules/SupplementP.list,PROXY
{geoip}
FINAL,PROXY,force-remote-dns
"""


class VerifierFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.tmp.name)
        os.makedirs("rules", exist_ok=True)
        self._write("rules/T1.list", TABLE)
        self._write("rules/SupplementA.list", "DOMAIN-SUFFIX,supp.example\n")
        self._write("rules/SupplementP.list", "DOMAIN-SUFFIX,psupp.example\n")
        self._write("rules/SupplementAd.list", "DOMAIN-SUFFIX,ad.example\n")
        self._write("custom-rules.txt", "DOMAIN-SUFFIX,manual.example,DIRECT\n")

    @staticmethod
    def _write(relpath, text):
        with open(relpath, "w", encoding="utf-8") as f:
            f.write(text)

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def write_product(self, dns, geoip):
        text = GENERAL.format(dns=dns) + RULES.format(self=SELF, geoip=geoip)
        path = os.path.join(self.tmp.name, "prod.conf")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path


class TestDecide(VerifierFixture):
    def test_first_match_domain_types(self):
        p = vr.Product(self.write_product(vr.DUAL_DOH, "GEOIP,CN,DIRECT,no-resolve"))
        self.assertEqual(p.decide("manual.example"), "DIRECT")
        self.assertEqual(p.decide("www.manual.example"), "DIRECT")
        self.assertEqual(p.decide("www.proxy.example"), "PROXY")
        self.assertEqual(p.decide("exact.example"), "PROXY")
        self.assertEqual(p.decide("x.kwmark.example"), "PROXY")
        self.assertEqual(p.decide("a.wild.example"), "PROXY")
        self.assertEqual(p.decide("wild.example"), "PROXY")  # FINAL 兜底
        self.assertEqual(p.decide("www.supp.example"), "DIRECT")
        self.assertEqual(p.decide("www.psupp.example"), "PROXY")

    def test_guarded_geoip_skipped(self):
        p = vr.Product(self.write_product(vr.DUAL_DOH, "GEOIP,CN,DIRECT,no-resolve"))
        # GEOIP 带守卫：不触发解析，任何 IP 假设都落 FINAL
        for mock in (None, True, False):
            self.assertEqual(p.decide("unknown.example", mock), "PROXY")

    def test_bare_geoip_needs_ip(self):
        p = vr.Product(self.write_product(vr.FOREIGN_DOH, "GEOIP,CN,DIRECT"))
        self.assertEqual(p.decide("unknown.example", None), "NEED-IP")
        self.assertEqual(p.decide("unknown.example", True), "DIRECT")
        self.assertEqual(p.decide("unknown.example", False), "PROXY")
        # 已有域名规则命中时不受 GEOIP 影响
        self.assertEqual(p.decide("www.proxy.example", None), "PROXY")
        self.assertEqual(p.decide("www.supp.example", False), "DIRECT")


class TestPremise(unittest.TestCase):
    def test_valid_lines_pass(self):
        lines = ["IP-CIDR,1.2.3.0/24,DIRECT,no-resolve",
                 "IP-ASN,32934,no-resolve",
                 "DOMAIN-SUFFIX,example.com,DIRECT",
                 "# comment"]
        self.assertEqual(vr.check_no_resolve_premise(lines, True), [])

    def test_bypass_forms_fail(self):
        self.assertEqual(len(vr.check_no_resolve_premise(
            ["IP-CIDR,1.2.3.0/24,DIRECT,#no-resolve"], True)), 1)
        self.assertEqual(len(vr.check_no_resolve_premise(
            ["IP-CIDR,1.2.3.0/24,DIRECT,no-resolve-extra"], True)), 1)
        self.assertEqual(len(vr.check_no_resolve_premise(
            ["IP-CIDR,1.2.3.0/24,DIRECT"], True)), 1)

    def test_bare_geoip_rules(self):
        self.assertEqual(vr.check_no_resolve_premise(["GEOIP,CN,DIRECT"], True), [])
        self.assertEqual(len(vr.check_no_resolve_premise(["GEOIP,CN,DIRECT"], False)), 1)
        self.assertEqual(len(vr.check_no_resolve_premise(
            ["GEOIP,CN,DIRECT", "GEOIP,CN,DIRECT"], True)), 1)

    def test_misplaced_no_resolve_fails(self):
        self.assertEqual(len(vr.check_no_resolve_premise(
            ["IP-CIDR,1.2.3.0/24,no-resolve,DIRECT"], True)), 1)
        self.assertEqual(len(vr.check_no_resolve_premise(
            ["AND,(IP-CIDR,1.2.3.0/24,no-resolve,DIRECT),(DST-PORT,443),PROXY"],
            True)), 1)

    def test_nested_logical_premise(self):
        self.assertEqual(vr.check_no_resolve_premise(
            ["AND,(OR,(IP-CIDR,1.1.1.0/24,no-resolve),"
             "(IP-CIDR,2.2.2.0/24,no-resolve)),PROXY"], True), [])
        self.assertEqual(len(vr.check_no_resolve_premise(
            ["AND,(OR,(IP-CIDR,1.1.1.0/24,no-resolve),"
             "(IP-CIDR,2.2.2.0/24)),PROXY"], True)), 1)


class TestClassifyShadow(unittest.TestCase):
    def test_dangerous_direction_fails(self):
        self.assertEqual(vr.classify_shadow("DIRECT", "PROXY"), "fail")

    def test_other_directions_report_only(self):
        for shadow, expected in [("PROXY", "DIRECT"), ("REJECT", "DIRECT"),
                                 ("DIRECT", "REJECT"), ("PROXY", "REJECT"),
                                 ("DIRECT", "DIRECT"), ("PROXY", "PROXY"),
                                 ("REJECT", "REJECT")]:
            self.assertEqual(vr.classify_shadow(shadow, expected), "report",
                             (shadow, expected))


class TestLogicalDomainRejection(VerifierFixture):
    def test_table_logical_domain_line_rejected(self):
        self._write("rules/T1.list",
                    "AND,(DOMAIN-SUFFIX,log.example),(DST-PORT,443),PROXY\n")
        with self.assertRaises(vr.VerifyError):
            vr.Product(self.write_product(vr.DUAL_DOH, "GEOIP,CN,DIRECT,no-resolve"))

    def test_table_ip_only_logical_line_ok(self):
        self._write("rules/T1.list",
                    "AND,(IP-CIDR,1.2.3.0/24,no-resolve),(DST-PORT,443),PROXY\n")
        p = vr.Product(self.write_product(vr.DUAL_DOH, "GEOIP,CN,DIRECT,no-resolve"))
        self.assertEqual(p.decide("manual.example"), "DIRECT")

    def test_unknown_type_rejected(self):
        self._write("rules/T1.list", "FOO-BAR,example.com\n")
        with self.assertRaises(vr.VerifyError):
            vr.Product(self.write_product(vr.DUAL_DOH, "GEOIP,CN,DIRECT,no-resolve"))

    def test_inline_logical_domain_line_rejected(self):
        text = (GENERAL.format(dns=vr.DUAL_DOH)
                + "[Rule]\n"
                + "AND,(DOMAIN-SUFFIX,inline.example),(DST-PORT,443),PROXY\n"
                + "GEOIP,CN,DIRECT,no-resolve\n"
                + "FINAL,PROXY,force-remote-dns\n")
        path = os.path.join(self.tmp.name, "prod2.conf")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        with self.assertRaises(vr.VerifyError):
            vr.Product(path)


class TestDnsInterlock(VerifierFixture):
    def test_matched_pairs_pass(self):
        p = vr.Product(self.write_product(vr.FOREIGN_DOH, "GEOIP,CN,DIRECT"))
        self.assertTrue(vr.check_dns(p))
        p = vr.Product(self.write_product(vr.DUAL_DOH, "GEOIP,CN,DIRECT,no-resolve"))
        self.assertFalse(vr.check_dns(p))

    def test_mismatched_pair_fails(self):
        p = vr.Product(self.write_product(vr.FOREIGN_DOH, "GEOIP,CN,DIRECT,no-resolve"))
        with self.assertRaises(vr.VerifyError):
            vr.check_dns(p)
        p = vr.Product(self.write_product(vr.DUAL_DOH, "GEOIP,CN,DIRECT"))
        with self.assertRaises(vr.VerifyError):
            vr.check_dns(p)


if __name__ == "__main__":
    unittest.main()
