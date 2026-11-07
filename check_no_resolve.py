#!/usr/bin/env python3
"""check_no_resolve.py — 出厂检查：扫描成品配置的全部规则，断言每一条
IP 类规则（IP-CIDR / IP-CIDR6 / IP6-CIDR / IP-ASN）都带 no-resolve。

背景: 2026-11-08 的 DNS 泄露正是因为四张 QuantumultX 格式表的 IP 规则
不带 no-resolve、而原构建只检查主配置文本、没查远程表内容。此脚本把那次
人工全链审阅固化为每日自动守卫：本地规则行 + 全部 RULE-SET 远程表逐张
下载扫描，任何一处新出现无 no-resolve 的 IP 规则，当日构建即失败报警，
问题成品不会提交、不出厂。

用法: python3 check_no_resolve.py <conf 路径> [<conf 路径> ...]
"""
import re
import sys
import urllib.request

IP_LINE = re.compile(r"^(IP-CIDR|IP-CIDR6|IP6-CIDR|IP-ASN),", re.I)
RULESET = re.compile(r"^RULE-SET,(https://[^,]+),")

def fail(msg):
    print(f"CHECK FAILED: {msg}", file=sys.stderr)
    sys.exit(1)

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (check_no_resolve)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace").splitlines()

def main(paths):
    urls = set()
    for p in paths:
        sec = None
        for raw in open(p, encoding="utf-8"):
            s = raw.strip()
            if s.startswith("[") and s.endswith("]"):
                sec = s
                continue
            if sec != "[Rule]" or not s or s.startswith("#"):
                continue
            m = RULESET.match(s)
            if m:
                urls.add(m.group(1))
            elif IP_LINE.match(s) and "no-resolve" not in s.lower():
                fail(f"{p} 本地 IP 规则无 no-resolve: {s}")
    total_bad = 0
    for u in sorted(urls):
        try:
            body = fetch(u)
        except Exception as e:
            fail(f"规则集拉取失败 {u}: {e}")
        bad = [l.strip() for l in body
               if IP_LINE.match(l.strip()) and "no-resolve" not in l.lower()]
        if bad:
            total_bad += len(bad)
            print(f"BAD {u}: {len(bad)} 条 IP 规则无 no-resolve，如 {bad[0]}")
    if total_bad:
        fail(f"共 {total_bad} 条 IP 规则无 no-resolve，拒绝出厂")
    print(f"OK: {len(urls)} 张远程表 + 本地规则，全部 IP 规则均带 no-resolve")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        fail("用法: python3 check_no_resolve.py <conf 路径> [...]")
    main(sys.argv[1:])
