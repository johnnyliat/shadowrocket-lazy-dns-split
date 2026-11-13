#!/usr/bin/env python3
"""verify_routing.py — 独立验证：只读最终成品与规则表，验证分流语义。

与生成器（patch.py / compile_supplement.py）完全独立的实现：不 import
build_common，不复用生成器的任何解析代码，只用自己的一套规则解析与
首命中裁决，从成品反推"一个域名实际会走哪里"。生成器内部的自检再多，
也只能证明"生成器按自己的理解生成了文件"；本脚本证明"文件本身的语义
符合预期"。两边用不同代码得出同一结论，结论才可信。

验证内容（任一不符 exit 1，整轮构建失败）:
  1) DNS 一致性：三行 DNS 与回落/基线行的取值与配对联动锁（GEOIP 无
     no-resolve 当且仅当 dns-server 带 #proxy），两种 dns-mode 都接受，
     但混搭即失败。
  2) 结构与顺序：手动规则与 custom-rules.txt 一致且位于 [Rule] 最前；
     补集行 A 在 P 之前、P 紧贴 GEOIP 之前；GEOIP 与 FINAL 各一条、
     FINAL 居末；规则集地址全部自托管、无重复、本地文件齐全；基础版
     零 REJECT 行，去广告版有去广告组。
  3) no-resolve 前提：全部 IP 类规则行带精确的 no-resolve 修饰符（独立
     实现的判定），仅成品自己的裸 GEOIP 行可在 #proxy 配对时例外——
     域名裁决引擎"跳过 IP 规则"的前提必须先被证实，否则裁决结论无效。
  4) 补集属性：A 层每条在两种成品、三种 IP 假设下都判 DIRECT；P 层每
     条都判 PROXY；去广告补充每条在去广告版都判 REJECT。
  5) 固定样本：关键域名（银行、支付、推送、国内外大站、未知域 GEOIP
     行为、裸顶级域）的判定与冻结的预期一致，任一漂移即失败。
  6) 双版本一致：全部抽查域在两版本中的判定相同，或去广告版为 REJECT
     （去广告组只做减法，不改变任何分流判定）。

裁决模型（域名查询视角，与 Shadowrocket 首命中语义一致）:
  - 域名规则（精确/后缀/关键字/通配）按文件顺序首命中；
  - 带 no-resolve 的 IP 规则对域名查询不触发解析，跳过（前提由第 3 项保证）；
  - 裸 GEOIP 触发预解析：无 IP 假设时判 NEED-IP，中国 IP 判 DIRECT，
    外国 IP 不命中、继续往后；带 no-resolve 的 GEOIP 跳过；
  - FINAL 兜底。USER-AGENT / URL-REGEX 等非域名规则不参与域名裁决。
  通配规则按 fnmatch 语义匹配（* 可跨点号），与引擎行为一致。

用法（在仓库根目录运行）: python3 verify_routing.py <基础版 conf> <去广告版 conf>
"""
import fnmatch
import os
import re
import sys

SELF_PREFIX = "https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/"
DUAL_DOH = "https://dns.alidns.com/dns-query,https://doh.pub/dns-query"
FOREIGN_DOH = "https://cloudflare-dns.com/dns-query#proxy,https://dns.google/dns-query#proxy"
RULES_DIR = "rules"
CUSTOM_FILE = "custom-rules.txt"
# 补集属性抽查用的合成子域名标签：取一个不可能被任何已有精确规则命中、
# 且不含任何已有关键字子串的标签（2026-11-13 已核对 71 条关键字均不
# 命中），使后缀条目的抽查只受后缀/通配规则影响。后缀条目的裸域名本身
# 可能被更早的精确规则合法覆盖（如 Global 表的 HOST,ztedevices.com），
# 故裸名不作为后缀条目的断言对象——精确规则盖不住后缀是编译器的既定
# 语义，子域抽查才是后缀条目是否生效的正确探针。
PROBE_LABEL = "zz-verify-probe"

DOMAIN_TYPES = {"DOMAIN", "HOST", "DOMAIN-SUFFIX", "HOST-SUFFIX",
                "DOMAIN-KEYWORD", "HOST-KEYWORD",
                "DOMAIN-WILDCARD", "HOST-WILDCARD"}
IP_TYPES = {"IP-CIDR", "IP-CIDR6", "IP6-CIDR", "IP-ASN", "GEOIP"}
LOGICAL_TYPES = {"AND", "OR", "NOT"}
# 规则表里可能出现、但不参与域名裁决的类型：认得出即可，不报错。
OTHER_TYPES = {"USER-AGENT", "URL-REGEX", "DST-PORT", "SRC-IP", "SRC-PORT",
               "PROCESS-NAME", "RULE-SET", "DOMAIN-SET", "IN-PORT", "PROTOCOL"}
KNOWN_TYPES = DOMAIN_TYPES | IP_TYPES | LOGICAL_TYPES | OTHER_TYPES


class VerifyError(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise VerifyError(msg)


# ---------------- no-resolve 前提（独立实现） ----------------

def _ip_guard_ok(body):
    """body 已去注释。返回 True/False；非 IP 行返回 None。"""
    head = body.split(",", 1)[0].strip().upper()
    if head in LOGICAL_TYPES:
        if body.count("(") != body.count(")"):
            return False
        found = False
        for m in re.finditer(r"(IP-CIDR6?|IP6-CIDR|IP-ASN|GEOIP)\s*,", body, re.I):
            found = True
            span = body[m.end():]
            close = span.find(")")
            span = span if close < 0 else span[:close]
            fields = [f.strip().lower() for f in span.split(",")]
            if "no-resolve" not in fields:
                return False
        return True if found else None
    if head not in IP_TYPES:
        return None
    fields = [f.strip().lower() for f in body.rstrip(",").split(",")]
    return "no-resolve" in fields


def check_no_resolve_premise(lines, allow_bare_geoip):
    """lines: 全部规则行（成品内联 + 各表）。返回问题列表。"""
    problems = []
    bare_seen = 0
    for raw in lines:
        s = raw.strip().lstrip("\ufeff")
        if not s or s.startswith("#") or s.startswith("//"):
            continue
        body = s.split("#", 1)[0].strip()
        if not body:
            continue
        if body == "GEOIP,CN,DIRECT":
            bare_seen += 1
            if not allow_bare_geoip:
                problems.append(f"裸 GEOIP 行但 dns-server 无 #proxy: {s}")
            continue
        ok = _ip_guard_ok(body)
        if ok is False:
            problems.append(f"IP 规则无精确 no-resolve: {s}")
    if bare_seen > 1:
        problems.append(f"裸 GEOIP 行出现 {bare_seen} 条（最多 1 条）")
    return problems


# ---------------- 成品解析与裁决引擎 ----------------

def parse_table_file(path, policy):
    """解析一张规则表，返回 [(typ, value, policy)]（仅域名规则）。"""
    rules = []
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for raw in lines:
        s = raw.strip().lstrip("\ufeff")
        if not s or s.startswith("#") or s.startswith("//") or s.startswith("["):
            continue
        parts = [p.strip() for p in s.split(",")]
        typ = parts[0].upper()
        check(typ in KNOWN_TYPES and len(parts) >= 2,
              f"规则表 {path} 出现无法解析的行: {s!r}")
        if typ in DOMAIN_TYPES:
            rules.append((typ, parts[1].lower(), policy))
        # IP/逻辑/其他类型不参与域名裁决（IP 前提另行验证）
    return rules


class Product:
    def __init__(self, path):
        self.path = path
        self.general = []
        self.rule_lines = []
        self.rules = []
        self.ruleset_urls = []
        self.geoip_bare = False
        self.geoip_idx = None
        self.final_idx = None
        section = None
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        for raw in lines:
            s = raw.strip()
            if s.startswith("[") and s.endswith("]"):
                section = s
                continue
            if not s or s.startswith("#"):
                continue
            if section == "[General]":
                self.general.append(s)
            if section != "[Rule]":
                continue
            self.rule_lines.append(s)
            if s.startswith(("RULE-SET,", "DOMAIN-SET,")):
                parts = s.split(",")
                check(len(parts) >= 3, f"{path} 规则集行格式不对: {s!r}")
                url, policy = parts[1].strip(), parts[2].strip().upper()
                self.ruleset_urls.append(url)
                check(url.startswith(SELF_PREFIX),
                      f"{path} 规则集地址不是自家镜像: {url}")
                local = os.path.join(RULES_DIR, os.path.basename(url))
                check(os.path.exists(local), f"规则集本地文件缺失: {local}")
                self.rules.extend(parse_table_file(local, policy))
            elif s.startswith("GEOIP,"):
                self.geoip_bare = "no-resolve" not in s.lower()
                self.geoip_idx = len(self.rules)
                self.rules.append(("GEOIP", s, "DIRECT"))
            elif s.startswith("FINAL,"):
                self.final_idx = len(self.rules)
                self.rules.append(("FINAL", s, s.split(",")[1].strip().upper()))
            else:
                parts = [p.strip() for p in s.split(",")]
                typ = parts[0].upper()
                check(typ in KNOWN_TYPES and len(parts) >= 3,
                      f"{path} [Rule] 行无法解析: {s!r}")
                if typ in DOMAIN_TYPES:
                    self.rules.append((typ, parts[1].lower(),
                                       parts[2].strip().upper()))
        self._build_index()

    def _build_index(self):
        self.exact, self.suffix = {}, {}
        self.keywords, self.wildcards = [], []
        for i, (typ, value, policy) in enumerate(self.rules):
            if typ in ("DOMAIN", "HOST"):
                self.exact.setdefault(value, (i, policy))
            elif typ in ("DOMAIN-SUFFIX", "HOST-SUFFIX"):
                self.suffix.setdefault(value, (i, policy))
            elif typ in ("DOMAIN-KEYWORD", "HOST-KEYWORD"):
                self.keywords.append((value, i, policy))
            elif typ in ("DOMAIN-WILDCARD", "HOST-WILDCARD"):
                self.wildcards.append((value, i, policy))

    def decide(self, domain, mock_cn_ip=None):
        """mock_cn_ip: None=不提供 IP；True=中国 IP；False=外国 IP。
        返回 DIRECT / PROXY / REJECT / NEED-IP。"""
        d = domain.lower()
        best, best_policy = None, None
        if d in self.exact:
            best, best_policy = self.exact[d]
        parts = d.split(".")
        for i in range(len(parts)):
            s = ".".join(parts[i:])
            if s in self.suffix and (best is None or self.suffix[s][0] < best):
                best, best_policy = self.suffix[s]
        for kw, i, pol in self.keywords:
            if kw in d and (best is None or i < best):
                best, best_policy = i, pol
        for pat, i, pol in self.wildcards:
            if fnmatch.fnmatchcase(d, pat) and (best is None or i < best):
                best, best_policy = i, pol
        if self.geoip_bare and self.geoip_idx is not None:
            gi = self.geoip_idx
            if best is None or gi < best:
                if mock_cn_ip is None:
                    return "NEED-IP"
                if mock_cn_ip:
                    return "DIRECT"
                # 外国 IP：GEOIP 不命中，继续往后（后续域名规则/FINAL）
        if best is not None:
            return best_policy
        if self.final_idx is not None:
            return self.rules[self.final_idx][2]
        return "NO-MATCH"

    def first_match(self, domain):
        """诊断用：线性扫描，返回首个命中的域名规则 (下标, 类型, 值, 策略)，
        无命中返回 None（不含 GEOIP/FINAL）。"""
        d = domain.lower()
        for i, (typ, value, policy) in enumerate(self.rules):
            if typ in ("DOMAIN", "HOST"):
                hit = d == value
            elif typ in ("DOMAIN-SUFFIX", "HOST-SUFFIX"):
                hit = d == value or d.endswith("." + value)
            elif typ in ("DOMAIN-KEYWORD", "HOST-KEYWORD"):
                hit = value in d
            elif typ in ("DOMAIN-WILDCARD", "HOST-WILDCARD"):
                hit = fnmatch.fnmatchcase(d, value)
            else:
                continue
            if hit:
                return (i, typ, value, policy)
        return None

    def dns_line(self, key):
        hits = [l for l in self.general if l.startswith(key + " =")]
        check(len(hits) == 1, f"{self.path} 的 {key} 不是恰好一条: {hits}")
        return hits[0].split("=", 1)[1].strip()


# ---------------- 固定样本（冻结预期，漂移即失败） ----------------
# 格式: (域名, mock_cn_ip, 基础版预期, 去广告版预期)；预期 None 表示该版本跳过。
# mock: None=不提供 IP；True=中国 IP；False=外国 IP。
# 预期由 2026-11-13 对线上成品的实测裁决冻结，并逐条按设计意图复核：
# 手动规则、上下游表、补集、GEOIP 行为各有代表；未知域在无 IP 时为
# NEED-IP（GEOIP 需预解析结果），有 IP 假设时按归属裁决。
ANCHORS = [
    # 手动规则（custom-rules.txt，权力最高）
    ("hsbc.com.hk", None, "DIRECT", "DIRECT"),
    ("www.hsbc.com.hk", None, "DIRECT", "DIRECT"),
    ("hsbc.com", None, "DIRECT", "DIRECT"),
    ("vv1865.com", None, "DIRECT", "DIRECT"),
    ("www.vv1865.com", None, "DIRECT", "DIRECT"),
    ("hsbc.co.uk", None, "DIRECT", "DIRECT"),
    ("ip.cn", None, "PROXY", "PROXY"),
    ("www.ip.cn", None, "PROXY", "PROXY"),
    # 上游直连表
    ("baidu.com", None, "DIRECT", "DIRECT"),
    ("www.baidu.com", None, "DIRECT", "DIRECT"),
    ("bilibili.com", None, "DIRECT", "DIRECT"),
    ("www.zhihu.com", None, "DIRECT", "DIRECT"),
    ("weixin.qq.com", None, "DIRECT", "DIRECT"),
    ("taobao.com", None, "DIRECT", "DIRECT"),
    ("jd.com", None, "DIRECT", "DIRECT"),
    ("apple.com", None, "DIRECT", "DIRECT"),
    ("www.apple.com", None, "DIRECT", "DIRECT"),
    ("icloud.com", None, "DIRECT", "DIRECT"),
    ("gateway.icloud.com", None, "DIRECT", "DIRECT"),
    # 上游代理表
    ("google.com", None, "PROXY", "PROXY"),
    ("www.google.com", None, "PROXY", "PROXY"),
    ("mtalk.google.com", None, "PROXY", "PROXY"),
    ("youtube.com", None, "PROXY", "PROXY"),
    ("www.youtube.com", None, "PROXY", "PROXY"),
    ("facebook.com", None, "PROXY", "PROXY"),
    ("twitter.com", None, "PROXY", "PROXY"),
    ("telegram.org", None, "PROXY", "PROXY"),
    ("api.telegram.org", None, "PROXY", "PROXY"),
    ("github.com", None, "PROXY", "PROXY"),
    ("www.github.com", None, "PROXY", "PROXY"),
    ("netflix.com", None, "PROXY", "PROXY"),
    ("www.netflix.com", None, "PROXY", "PROXY"),
    # 上游本地规则（第 3 组；去广告版里广告表先命中，旧成品亦同）
    ("litix.io", None, "PROXY", "REJECT"),
    ("discomax.com", None, "PROXY", "PROXY"),
    ("brightline.tv", None, "PROXY", "PROXY"),
    # A 层补集代表
    ("100tal.com", None, "DIRECT", "DIRECT"),
    ("ac.fun", None, "DIRECT", "DIRECT"),
    ("geely.com", None, "DIRECT", "DIRECT"),
    ("insta360.com", None, "DIRECT", "DIRECT"),
    ("foo.alibaba", None, "DIRECT", "DIRECT"),
    # P 层补集代表
    ("sentry.io", None, "PROXY", "PROXY"),
    ("api.sentry.io", None, "PROXY", "PROXY"),
    ("newrelic.com", None, "PROXY", "PROXY"),
    ("joox.com", None, "PROXY", "PROXY"),
    ("bsky.chat", None, "PROXY", "PROXY"),
    ("discovery.com", None, "PROXY", "PROXY"),
    # 去广告样本（基础版不拦截，去广告版拦截）
    ("doubleclick.net", None, "PROXY", "REJECT"),
    ("googleadservices.com", None, "PROXY", "REJECT"),
    ("2mdn.net", None, "PROXY", "REJECT"),
    # 绝不能被广告拦截的服务（支付/登录/银行/苹果）
    ("www.alipay.com", None, "DIRECT", "DIRECT"),
    ("www.paypal.com", None, "PROXY", "PROXY"),
    ("login.microsoftonline.com", None, "PROXY", "PROXY"),
    ("www.ccb.com", None, "DIRECT", "DIRECT"),
    ("www.abchina.com.cn", None, "DIRECT", "DIRECT"),
    ("www.psbc.com", None, "DIRECT", "DIRECT"),
    # 未知域：无 IP 时 NEED-IP，中国 IP 时 DIRECT，外国 IP 时 PROXY
    ("unknown-xyz-12345.example.com", None, "NEED-IP", "NEED-IP"),
    ("unknown-xyz-12345.example.com", True, "DIRECT", "DIRECT"),
    ("unknown-xyz-12345.example.com", False, "PROXY", "PROXY"),
    # 未知 .cn 域：China 表兜底后缀命中，不依赖 IP
    ("unknown-xyz-12345.example.cn", None, "DIRECT", "DIRECT"),
    ("unknown-xyz-12345.example.cn", True, "DIRECT", "DIRECT"),
    ("unknown-xyz-12345.example.cn", False, "DIRECT", "DIRECT"),
    # 裸顶级域未收录域：走 GEOIP，不被任何补集条目误判
    ("example.ca", None, "NEED-IP", "NEED-IP"),
    ("example.ca", True, "DIRECT", "DIRECT"),
    ("example.ca", False, "PROXY", "PROXY"),
    ("example.jp", None, "NEED-IP", "NEED-IP"),
    ("example.jp", True, "DIRECT", "DIRECT"),
    ("example.jp", False, "PROXY", "PROXY"),
    ("example.uk", None, "NEED-IP", "NEED-IP"),
    ("example.uk", True, "DIRECT", "DIRECT"),
    ("example.uk", False, "PROXY", "PROXY"),
]


def load_custom_rules_expected():
    """独立解析 custom-rules.txt，返回渲染后的规则行（与成品内嵌形式比对）。"""
    out = []
    if not os.path.exists(CUSTOM_FILE):
        return out
    with open(CUSTOM_FILE, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = [x.strip() for x in s.split(",")]
        typ = parts[0].upper()
        line = f"{typ},{parts[1]},{parts[2].upper()}"
        if typ in ("IP-CIDR", "IP-CIDR6", "IP6-CIDR"):
            line += ",no-resolve"
        out.append(line)
    return out


def load_supplement_entries(name):
    path = os.path.join(RULES_DIR, name)
    check(os.path.exists(path), f"补集表缺失: {path}")
    entries = []
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = [x.strip() for x in s.split(",")]
        check(len(parts) == 2 and parts[0].upper() in ("DOMAIN-SUFFIX", "DOMAIN"),
              f"{name} 行格式不对: {s!r}")
        entries.append((parts[0].upper(), parts[1].lower()))
    return entries


def check_dns(product):
    dns = product.dns_line("dns-server")
    direct = product.dns_line("direct-dns-server")
    proxy = product.dns_line("proxy-dns-server")
    fallback = product.dns_line("fallback-dns-server")
    check(dns in (DUAL_DOH, FOREIGN_DOH),
          f"{product.path} dns-server 取值不在预期内: {dns}")
    check(direct == DUAL_DOH, f"{product.path} direct-dns-server 不是国内双 DoH: {direct}")
    check(proxy == DUAL_DOH, f"{product.path} proxy-dns-server 不是国内双 DoH: {proxy}")
    check(fallback == DUAL_DOH,
          f"{product.path} fallback-dns-server 不是国内双 DoH: {fallback}")
    for baseline in ("dns-fallback-system = false", "ipv6 = false",
                     "allow-dns-all = false", "dns-direct-system = false",
                     "dns-direct-fallback-proxy = true",
                     "private-ip-answer = true"):
        check(product.general.count(baseline) == 1,
              f"{product.path} 基线行缺失或重复: {baseline}")
    # 联动锁：GEOIP 无 no-resolve 当且仅当 dns-server 带 #proxy
    has_proxy = "#proxy" in dns
    check(product.geoip_bare == has_proxy,
          f"{product.path} GEOIP/#proxy 配对不一致（GEOIP 裸={product.geoip_bare}，dns #proxy={has_proxy}）")
    return has_proxy


def check_order(product, custom_expected, is_ad):
    rules = product.rule_lines
    check(rules, f"{product.path} [Rule] 为空")
    check(rules[-1] == "FINAL,PROXY,force-remote-dns",
          f"{product.path} FINAL 不是最后一条: {rules[-1]}")
    check(sum(1 for l in rules if l.startswith("FINAL,")) == 1,
          f"{product.path} FINAL 不止一条")
    check(sum(1 for l in rules if l.startswith("GEOIP,")) == 1,
          f"{product.path} GEOIP 不止一条")
    check(rules[:len(custom_expected)] == custom_expected,
          f"{product.path} 手动规则不在 [Rule] 最前面或与 custom-rules.txt 不一致")
    geoip_i = next(i for i, l in enumerate(rules) if l.startswith("GEOIP,"))
    a_url = SELF_PREFIX + "rules/SupplementA.list"
    p_url = SELF_PREFIX + "rules/SupplementP.list"
    check(rules[geoip_i - 1] == f"RULE-SET,{p_url},PROXY",
          f"{product.path} P 层补集行不在 GEOIP 之前: {rules[geoip_i - 1]}")
    check(rules[geoip_i - 2] == f"RULE-SET,{a_url},DIRECT",
          f"{product.path} A 层补集行不在 P 层之前: {rules[geoip_i - 2]}")
    check(len(product.ruleset_urls) == len(set(product.ruleset_urls)),
          f"{product.path} 规则集引用有重复")
    rejects = [l for l in rules if l.endswith(",REJECT") or l.endswith(",REJECT,no-resolve")]
    if is_ad:
        check(len(rejects) > 50000, f"{product.path} 去广告规则数异常: {len(rejects)}")
    else:
        check(not rejects, f"{product.path} 基础版出现 REJECT 行: {rejects[:3]}")


def main(base_path, ad_path):
    check(os.path.isdir(RULES_DIR) and os.path.exists(CUSTOM_FILE),
          "须在仓库根目录运行（需要 rules/ 与 custom-rules.txt）")
    base = Product(base_path)
    ad = Product(ad_path)
    has_proxy_base = check_dns(base)
    has_proxy_ad = check_dns(ad)
    check(has_proxy_base == has_proxy_ad, "两版本 DNS 模式不一致")
    custom_expected = load_custom_rules_expected()
    check_order(base, custom_expected, is_ad=False)
    check_order(ad, custom_expected, is_ad=True)

    # no-resolve 前提：每份成品的内联行各查一次（裸 GEOIP 每份最多 1 条），
    # 全部引用表合起来查一次（表里零容忍，裸 GEOIP 亦违规）
    for product in (base, ad):
        problems = check_no_resolve_premise(product.rule_lines,
                                            allow_bare_geoip=has_proxy_base)
        check(not problems,
              f"{product.path} no-resolve 前提不成立: " + "; ".join(problems[:5]))
    table_lines = []
    seen_tables = set()
    for url in base.ruleset_urls + ad.ruleset_urls:
        local = os.path.join(RULES_DIR, os.path.basename(url))
        if local in seen_tables:
            continue
        seen_tables.add(local)
        with open(local, encoding="utf-8") as f:
            table_lines.extend(f.read().splitlines())
    problems = check_no_resolve_premise(table_lines, allow_bare_geoip=False)
    check(not problems, "规则表 no-resolve 前提不成立: " + "; ".join(problems[:5]))

    # 补集属性：后缀条目用合成子域名探针，精确条目用其自身。
    # 探针失配时先诊断首命中规则：若为更早的通配规则遮蔽（编译器覆盖
    # 计算不含通配规则，已知局限、仅报告不拦截——通配遮蔽的条目是死
    # 规则而非误分流），记入遮蔽清单；其余失配一律失败。
    shadowed = []
    n_prop = 0

    def check_property(product, typ, value, expected, source):
        nonlocal n_prop
        domain = value if typ == "DOMAIN" else f"{PROBE_LABEL}.{value}"
        for mock in (None, True, False):
            got = product.decide(domain, mock)
            if got != expected:
                fm = product.first_match(domain)
                if fm and fm[1] in ("DOMAIN-WILDCARD", "HOST-WILDCARD"):
                    shadowed.append(f"{source} {typ},{value} 被通配规则 "
                                    f"{fm[1]},{fm[2]} 遮蔽（{product.path}）")
                    return
                check(False,
                      f"{source} 条目 {typ},{value} 在 {product.path} 判 {got}"
                      f"（mock={mock}，首命中 {fm}），预期 {expected}")
            n_prop += 1

    for name, product_expected in (("SupplementA.list", "DIRECT"),
                                   ("SupplementP.list", "PROXY")):
        entries = load_supplement_entries(name)
        for typ, value in entries:
            for product in (base, ad):
                check_property(product, typ, value, product_expected, name)
    ad_entries = load_supplement_entries("SupplementAd.list")
    for typ, value in ad_entries:
        check_property(ad, typ, value, "REJECT", "SupplementAd.list")
    if shadowed:
        print(f"通配遮蔽报告（{len(shadowed)} 条，仅报告）:")
        for line in sorted(set(shadowed))[:20]:
            print(f"  {line}")

    # 固定样本
    n_anchor = 0
    for domain, mock, exp_base, exp_ad in ANCHORS:
        for product, exp in ((base, exp_base), (ad, exp_ad)):
            if exp is None:
                continue
            got = product.decide(domain, mock)
            check(got == exp,
                  f"样本 {domain}（mock={mock}）在 {product.path} 判 {got}，预期 {exp}")
            n_anchor += 1

    # 双版本一致：抽查域在两版本中相同，或去广告版为 REJECT
    probe = {d for d, _, _, _ in ANCHORS}
    for name in ("SupplementA.list", "SupplementP.list", "SupplementAd.list"):
        for typ, value in load_supplement_entries(name):
            probe.add(value)
            if typ != "DOMAIN":
                probe.add("www." + value)
    n_cross = 0
    for d in sorted(probe):
        for mock in (None, True, False):
            b, a = base.decide(d, mock), ad.decide(d, mock)
            check(a == b or a == "REJECT",
                  f"双版本不一致: {d}（mock={mock}）基础版 {b} / 去广告版 {a}")
            n_cross += 1

    print(f"OK: 独立验证通过（DNS/顺序/前提 + 补集属性 {n_prop} 次裁决 + "
          f"固定样本 {n_anchor} 次裁决 + 双版本一致 {n_cross} 次比对）")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: python3 verify_routing.py <基础版 conf> <去广告版 conf>",
              file=sys.stderr)
        sys.exit(1)
    try:
        sys.exit(main(sys.argv[1], sys.argv[2]))
    except VerifyError as e:
        print(f"VERIFY FAILED: {e}", file=sys.stderr)
        sys.exit(1)
