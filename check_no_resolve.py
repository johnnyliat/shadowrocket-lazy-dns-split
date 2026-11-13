#!/usr/bin/env python3
"""check_no_resolve.py — 出厂检查：扫描成品配置的全部规则，断言每一条
IP 类规则（IP-CIDR / IP-CIDR6 / IP6-CIDR / IP-ASN / GEOIP）都带 no-resolve。

唯一的例外（DNS 配对联动锁的另一半）：成品的 GEOIP,CN,DIRECT 行在
dns-server 带有 #proxy 时允许不带 no-resolve——此时预解析经隧道走国外
DoH，国内 DNS 看不到查询，裸 GEOIP 是新架构有意的设计，不是漏洞。
配对关系本身由 patch.py 断言（GEOIP 无 no-resolve 当且仅当 dns-server
带 #proxy）；本守卫只认"带 #proxy 才放行裸 GEOIP"，其余一律零容忍，
且裸 GEOIP 行最多只能有一条（即成品自己的那条）。规则表里仍零容忍。

背景: 2026-11-08 的 DNS 泄露正是因为四张 QuantumultX 格式表的 IP 规则
不带 no-resolve、而原构建只检查主配置文本、没查远程表内容。此脚本把那次
人工全链审阅固化为每日自动守卫：本地规则行 + 全部 RULE-SET 表逐张扫描，
任何一处新出现无 no-resolve 的 IP 规则，当日构建即失败报警，
问题成品不会提交、不出厂。

扫描口径（2026-11-09 按外部审计建议收紧）:
  - 整行扫描：IP 类规则字样出现在一行中的任何位置（如 AND/OR 组合条件里
    嵌套的 IP 条件）都算数，整行没有 no-resolve 即违规，不再只看行首。
  - 自家镜像（本仓库 Raw 地址）直接读工作区里本次构建刚生成、尚未提交的
    本地文件，查的就是即将出厂的那一份，不再下载线上上一版。
  - 外部表联网下载，失败自动重试 3 次，避免网络抖动误报构建失败。

用法: python3 check_no_resolve.py <conf 路径> [<conf 路径> ...]
（须在仓库根目录运行：自家镜像按相对路径读本地文件。）
"""
import os
import re
import sys
import time
import urllib.request

SELF_PREFIX = "https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/"
IP_TOKEN = re.compile(r"(IP-CIDR6?|IP6-CIDR|IP-ASN|GEOIP)\s*,", re.I)
RULESET = re.compile(r"^RULE-SET,(https://[^,]+),")

def fail(msg):
    print(f"CHECK FAILED: {msg}", file=sys.stderr)
    sys.exit(1)

def fetch(url):
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (check_no_resolve)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace").splitlines()
        except Exception as e:
            last = e
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
    raise last

def load(url):
    if url.startswith(SELF_PREFIX):
        path = url[len(SELF_PREFIX):]
        if not os.path.exists(path):
            fail(f"自家规则集本地文件不存在: {path}（构建顺序异常）")
        return open(path, encoding="utf-8").read().splitlines()
    try:
        return fetch(url)
    except Exception as e:
        fail(f"规则集拉取失败 {url}: {e}")

def violations(lines):
    bad = []
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if IP_TOKEN.search(s) and "no-resolve" not in s.lower():
            bad.append(s)
    return bad

def main(paths):
    urls = set()
    for p in paths:
        sec, rule_lines, general_lines = None, [], []
        for raw in open(p, encoding="utf-8"):
            s = raw.strip()
            if s.startswith("[") and s.endswith("]"):
                sec = s
                continue
            if not s or s.startswith("#"):
                continue
            if sec == "[General]":
                general_lines.append(s)
            if sec != "[Rule]":
                continue
            rule_lines.append(s)
            m = RULESET.match(s)
            if m:
                urls.add(m.group(1))
        dns_has_proxy = any(l.startswith("dns-server =") and "#proxy" in l
                            for l in general_lines)
        bare_geoip = sum(1 for l in rule_lines if l == "GEOIP,CN,DIRECT")
        if bare_geoip > 1:
            fail(f"{p} 出现 {bare_geoip} 条裸 GEOIP 行（最多允许 1 条）")
        bad = violations(rule_lines)
        if dns_has_proxy:
            # 联动锁例外：仅成品自己的裸 GEOIP 行可放行，且仅在 #proxy 配对时
            bad = [b for b in bad if b != "GEOIP,CN,DIRECT"]
        if bad:
            fail(f"{p} 本地 IP 规则无 no-resolve: {bad[0]}（共 {len(bad)} 条）")
    total_bad = 0
    for u in sorted(urls):
        bad = violations(load(u))
        if bad:
            total_bad += len(bad)
            print(f"BAD {u}: {len(bad)} 条 IP 规则无 no-resolve，如 {bad[0]}")
    if total_bad:
        fail(f"共 {total_bad} 条 IP 规则无 no-resolve，拒绝出厂")
    print(f"OK: {len(urls)} 张表（自家镜像查本地新文件、外部表联网查）+ 本地规则，全部 IP 规则均带 no-resolve")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        fail("用法: python3 check_no_resolve.py <conf 路径> [...]")
    main(sys.argv[1:])
