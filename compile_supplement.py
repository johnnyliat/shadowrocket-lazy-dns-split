#!/usr/bin/env python3
"""compile_supplement.py — 编译直连补集：把多家上游的"国内直连"判断，
融合成一张自托管规则表（SupplementA.list），供成品引用。

为什么需要补集:
  懒人配置的直连名单（blackmatrix7 服务表 + QX China 表）覆盖有限，
  大量常见国内服务不在名单里，只能走 FINAL,PROXY 绕一圈，费流量、速度慢。
  但直接照搬别家的大表会误伤（把外国域名判直连 = 打不开 + DNS 泄露），
  所以本脚本不做"搬运"，做"编译 + 双边校验"。

输入源（全部每天现取）:
  - v2fly/domain-list-community 的 data/cn（递归展开 include:，
    即 tld-cn + geolocation-cn 下全部 *-cn 分类）
  - ACL4SSR 的 AccelerateDirectSites / GoogleCN / ChinaOneKeyLogin /
    ChinaDNS / ChinaNet 五张精选表

双边校验（不误伤的核心）:
  一条候选域名判直连，当且仅当——国内源主张它，并且没有任何一方反对它。
  反对证据 = 它已被直连表覆盖（重复）、被去广告表覆盖（广告域）、
  被手动规则或上游本地规则覆盖（已有明确判定），或被任何代理表覆盖
  （有国外证据的，一律判回代理）。代理证据一票否决，没有例外：
  同一个服务的端点必须同走代理、同走直连，不拆分——某服务的部分端点
  直连、其余走代理，会让该服务同时看到真实 IP 与代理出口 IP，起不到
  隔离作用。故本脚本不设任何凌驾于代理表之上的直连层。
  另：带 @ads 标记的条目（社区标注的广告/追踪域）不要。

丢弃（计数并报告，不静默）:
  - regexp: 条目：Shadowrocket 规则表表达不了正则；
  - keyword: 条目与各源的关键字规则：子串匹配最容易误伤；
  - IP 类条目：IP 判定需要先解析，那是泄露的源头，补集只含域名规则。

出厂守卫（任一不符 exit 1，整轮构建失败、线上保持上一版）:
  - 输出行只允许 DOMAIN-SUFFIX / DOMAIN 两种，且域名合法；
  - 与"已有判定"覆盖零重叠（减法算完再断言一遍）；
  - 数量下限/上限 + 与上一版相比波动超过 40% 即失败（防上游异常删减）。

用法（在仓库根目录运行；CI 里 patch_lists.py 之后、patch.py 之前）:
  python3 compile_supplement.py --v2fly-dir <domain-list-community/data>
      --lazy <上游 lazy.conf> --ad <上游 sr_ad_only.conf>
  v2fly 数据由工作流用 git clone --depth 1 现取（约 7MB），ACL4SSR 五张表
  由本脚本联网拉取（3 次重试）。输出写 rules/SupplementA.list
  （与成品引用的自托管镜像同目录）。
"""
import argparse
import ipaddress
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))

ACL4SSR_BASE = "https://raw.githubusercontent.com/ACL4SSR/ACL4SSR/master/Clash/"
# 五张精选表都在 Ruleset/ 子目录（GoogleCN 另有一份旧版在 Clash/ 直属，
# 内容是 Ruleset/ 版的子集，取 Ruleset/ 版）。
ACL4SSR_LISTS = ["Ruleset/AccelerateDirectSites.list", "Ruleset/GoogleCN.list",
                 "Ruleset/ChinaOneKeyLogin.list", "Ruleset/ChinaDNS.list",
                 "Ruleset/ChinaNet.list"]

# 数量守卫：下限/上限留足余量（2026-11-13 实测 A 层 3,047 条），
# 防的是上游断供/格式剧变，不是卡精确值；波动阈值取 40%（与 NZNL31 同口径）。
MIN_A, MAX_A = 800, 6000
MAX_DRIFT = 0.40

DOMAIN_RE = re.compile(r"^[a-z0-9._-]+$")
RULESET_RE = re.compile(r"^RULE-SET,(https://[^,]+),([^,]+)")


def fail(msg):
    print(f"SUPPLEMENT FAILED: {msg}", file=sys.stderr)
    sys.exit(1)


def fetch(url):
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compile_supplement)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
    fail(f"下载失败 {url}: {last}")


class Coverage:
    """一类规则的域名覆盖：后缀集 + 精确集 + 关键字集。"""

    def __init__(self):
        self.suffixes, self.exacts, self.keywords = set(), set(), set()

    def add_line(self, line):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("//"):
            return
        # findall 不锚定行首：AND/OR 组合里的嵌套域名条件同样算作覆盖
        #（多算只会让候选判回代理，方向安全；少算则可能漏减）。
        for typ, val in re.findall(r"(DOMAIN-SUFFIX|HOST-SUFFIX|DOMAIN|HOST|DOMAIN-KEYWORD|HOST-KEYWORD),([A-Za-z0-9._-]+)", s, re.I):
            v = val.lower()
            t = typ.upper()
            if t in ("DOMAIN-SUFFIX", "HOST-SUFFIX"):
                self.suffixes.add(v)
            elif t in ("DOMAIN", "HOST"):
                self.exacts.add(v)
            else:
                self.keywords.add(v)

    def add_bare_domain(self, value):
        """无格式的裸域名行（个别 .txt 表）：按后缀计。"""
        v = value.strip().lower()
        if v and DOMAIN_RE.match(v):
            self.suffixes.add(v)

    def merge(self, other):
        self.suffixes |= other.suffixes
        self.exacts |= other.exacts
        self.keywords |= other.keywords

    def covers_candidate(self, kind, value):
        """候选（suffix=后缀规则，exact=精确规则）是否已被本覆盖完全包含。
        后缀候选只看父后缀与关键字（精确规则只管一个域名，盖不住一个后缀）；
        精确候选三者都看。"""
        v = value.lower()
        if any(k in v for k in self.keywords):
            return True
        parts = v.split(".")
        if kind == "suffix":
            return any(".".join(parts[i:]) in self.suffixes for i in range(len(parts)))
        if v in self.exacts:
            return True
        return any(".".join(parts[i:]) in self.suffixes for i in range(len(parts)))


def parse_rule_file(text, coverage):
    """解析一张规则表（QX/SR/Clash 格式均可），把域名条件并入 coverage。
    IP 类规则、注释、空行、段头一律跳过（补集只关心域名覆盖）。"""
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or s.startswith("//") or s.startswith("["):
            continue
        before = (len(coverage.suffixes), len(coverage.exacts), len(coverage.keywords))
        coverage.add_line(s)
        after = (len(coverage.suffixes), len(coverage.exacts), len(coverage.keywords))
        if before == after and "," not in s and DOMAIN_RE.match(s.lower()):
            coverage.add_bare_domain(s)


def load_mirrors(manifest_path):
    m = {}
    for raw in open(manifest_path, encoding="utf-8"):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) != 2:
            fail(f"mirrors.txt 行格式不对: {s!r}")
        m[parts[0]] = parts[1]
    if not m:
        fail("mirrors.txt 是空的")
    return m


def load_earlier_coverage(lazy_path, ad_path, custom_path, manifest_path, rules_dir):
    """重建成品中"补集之前"全部规则的域名覆盖：镜像表（按上游策略分
    DIRECT/PROXY）+ 上游本地规则 + 手动规则 + 去广告表。补集的减法对着
    这份覆盖算，算的是"成品实际已有判定"，不是某个源的理论覆盖。"""
    mirrors = load_mirrors(manifest_path)
    direct, proxy, manual, local, ad = Coverage(), Coverage(), Coverage(), Coverage(), Coverage()
    policies = {}
    section = None
    for raw in open(lazy_path, encoding="utf-8"):
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
            continue
        if section != "[Rule]" or not s or s.startswith("#"):
            continue
        m = RULESET_RE.match(s)
        if m:
            policies[m.group(1)] = m.group(2).strip().upper()
        elif not s.startswith(("GEOIP,", "FINAL,")):
            local.add_line(s)
    if len(policies) != len(mirrors):
        fail(f"上游 RULE-SET 数 {len(policies)} 与 mirrors.txt 清单 {len(mirrors)} 不一致")
    for url, policy in policies.items():
        if url not in mirrors:
            fail(f"上游规则表不在 mirrors.txt 清单内: {url}")
        if policy not in ("DIRECT", "PROXY"):
            fail(f"上游规则表策略不是 DIRECT/PROXY，需人工确认覆盖归属: {url} -> {policy}")
        path = os.path.join(rules_dir, os.path.basename(mirrors[url]))
        if not os.path.exists(path):
            fail(f"镜像文件不存在（patch_lists.py 须先运行）: {path}")
        cov = Coverage()
        parse_rule_file(open(path, encoding="utf-8").read(), cov)
        (direct if policy == "DIRECT" else proxy).merge(cov)
    if os.path.exists(custom_path):
        for lineno, raw in enumerate(open(custom_path, encoding="utf-8"), 1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            parts = [x.strip() for x in s.split(",")]
            typ = parts[0].upper()
            if typ in ("DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"):
                if len(parts) != 3 or parts[2].upper() not in ("DIRECT", "PROXY"):
                    fail(f"custom-rules.txt 第 {lineno} 行格式不对: {s!r}")
                manual.add_line(s)
            elif typ in ("IP-CIDR", "IP-CIDR6", "IP6-CIDR"):
                continue  # IP 手动规则不影响域名覆盖
            else:
                fail(f"custom-rules.txt 第 {lineno} 行类型不支持: {s!r}")
    parse_rule_file(open(ad_path, encoding="utf-8").read(), ad)
    if "cn" not in direct.suffixes:
        fail("直连覆盖里没有 .cn 兜底（China 表结构可能已变化）")
    if not direct.suffixes or not proxy.suffixes:
        fail("直连/代理覆盖为空，镜像解析可能已失效")
    return {"direct": direct, "proxy": proxy, "manual": manual,
            "local": local, "ad": ad}


# ---------------- v2fly domain-list-community 解析 ----------------

def parse_v2fly_line(line, fname):
    """解析一行 v2fly 数据，返回 (kind, value, attrs) 或 None（注释/空行）。
    kind: suffix / exact / regexp / keyword / include。属性 @- 表示"清除继承
    属性"，单独处理。行尾可能带 " # 中文注释"，先截断再解析。
    未知语法即 fail（fail-closed，等人工确认）。"""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    s = re.split(r"\s+#", s, maxsplit=1)[0].strip()
    if not s:
        return None
    tokens = s.split()
    first, attrs = tokens[0], tokens[1:]
    for a in attrs:
        if not a.startswith("@"):
            fail(f"v2fly {fname} 行无法解析: {line!r}")
    if first.startswith("include:"):
        name = first[len("include:"):]
        if not name or "/" in name or "\\" in name:
            fail(f"v2fly {fname} 的 include 目标非法: {line!r}")
        return ("include", name, frozenset(attrs))
    if first.startswith("full:"):
        value = first[len("full:"):].lower()
        kind = "exact"
    elif first.startswith("domain:"):
        value = first[len("domain:"):].lower()
        kind = "suffix"
    elif first.startswith("regexp:"):
        return ("regexp", first[len("regexp:"):], frozenset(attrs))
    elif first.startswith("keyword:"):
        return ("keyword", first[len("keyword:"):].lower(), frozenset(attrs))
    elif ":" in first:
        fail(f"v2fly {fname} 出现未知前缀: {line!r}")
    else:
        value, kind = first.lower(), "suffix"
    if not DOMAIN_RE.match(value):
        fail(f"v2fly {fname} 域名不合法: {line!r}")
    if kind == "exact" and "." not in value:
        fail(f"v2fly {fname} 精确域名缺少点号: {line!r}")
    return (kind, value, frozenset(attrs))


def read_v2fly_file(data_dir, fname):
    path = os.path.join(data_dir, fname)
    if not os.path.exists(path):
        fail(f"v2fly 数据文件缺失: {fname}")
    entries = []
    for raw in open(path, encoding="utf-8"):
        parsed = parse_v2fly_line(raw, fname)
        if parsed:
            entries.append(parsed)
    return entries


def effective_attrs(own, inherited):
    """合并自有属性与继承属性。@- 清除全部继承属性；@-cn / @-ads 移除
    对应单个继承属性；@-!cn 这类其他否定标记与本脚本关心的属性无关，忽略。"""
    if "@-" in own:
        inherited = frozenset()
    eff = set(inherited) | set(own)
    if "@-cn" in own:
        eff.discard("@cn")
    if "@-ads" in own:
        eff.discard("@ads")
    eff -= {"@-", "@-cn", "@-ads"}
    return frozenset(eff)


def expand_cn_tree(data_dir):
    """从 data/cn 出发递归展开 include:，属性沿包含链继承（@- 清除继承）。
    返回 {(kind, value): attrs}，同一条目多条路径到达时属性取并集。"""
    collected = {}
    stack = [("cn", frozenset())]
    seen = set()
    while stack:
        fname, inherited = stack.pop()
        key = (fname, inherited)
        if key in seen:
            continue
        seen.add(key)
        for kind, value, attrs in read_v2fly_file(data_dir, fname):
            effective = effective_attrs(attrs, inherited)
            if kind == "include":
                stack.append((value, effective))
            else:
                k = (kind, value)
                collected[k] = collected.get(k, frozenset()) | effective
    return collected


def load_acl4ssr():
    """拉取 ACL4SSR 五张精选表，返回 [(kind, value)]（只留域名规则；
    IP 与关键字规则计数丢弃）。"""
    entries, dropped_ip, dropped_kw = [], 0, 0
    for name in ACL4SSR_LISTS:
        text = fetch(ACL4SSR_BASE + name)
        if text.lstrip().startswith("<"):
            fail(f"ACL4SSR {name} 返回的像是 HTML 错误页")
        n = 0
        for raw in text.splitlines():
            s = raw.strip()
            if not s or s.startswith("#") or s.startswith("//"):
                continue
            parts = [x.strip() for x in s.split(",")]
            typ = parts[0].upper()
            if typ in ("DOMAIN-SUFFIX", "DOMAIN"):
                if len(parts) < 2 or not DOMAIN_RE.match(parts[1].lower()):
                    fail(f"ACL4SSR {name} 行无法解析: {s!r}")
                entries.append(("suffix" if typ == "DOMAIN-SUFFIX" else "exact",
                                parts[1].lower()))
                n += 1
            elif typ.startswith("IP-"):
                dropped_ip += 1
            elif typ == "DOMAIN-KEYWORD":
                dropped_kw += 1
            else:
                fail(f"ACL4SSR {name} 出现未知规则类型: {s!r}")
        if n < 1:
            fail(f"ACL4SSR {name} 没有解析出任何域名规则")
    return entries, dropped_ip, dropped_kw


def collapse(entries):
    """折叠：同一层内，被另一条目覆盖的条目去掉（子后缀服从父后缀，
    精确服从覆盖它的后缀；同值去重）。返回排序后的 [(kind, value)]。"""
    cov = Coverage()
    for kind, value in entries:
        if kind == "suffix":
            cov.suffixes.add(value)
        else:
            cov.exacts.add(value)
    keep = []
    for kind, value in sorted(set(entries)):
        others = Coverage()
        others.suffixes = {s for s in cov.suffixes if s != value}
        others.exacts = {e for e in cov.exacts if e != value}
        # 同层内另有条目覆盖它（且不是它自己）-> 去掉
        if kind == "suffix" and others.covers_candidate("suffix", value):
            continue
        if kind == "exact" and others.covers_candidate("exact", value):
            continue
        keep.append((kind, value))
    # 再处理"精确与后缀同值"：后缀已含精确，去掉精确
    suffix_set = {v for k, v in keep if k == "suffix"}
    keep = [(k, v) for k, v in keep if not (k == "exact" and v in suffix_set)]
    return sorted(keep)


def read_previous(path):
    """读上一版已提交的补集表，返回条数（文件不存在 -> None，首轮跳过波动检查）。"""
    if not os.path.exists(path):
        return None
    n = 0
    for raw in open(path, encoding="utf-8"):
        s = raw.strip()
        if s and not s.startswith("#"):
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v2fly-dir", required=True)
    ap.add_argument("--lazy", required=True)
    ap.add_argument("--ad", required=True)
    ap.add_argument("--rules-dir", default=os.path.join(BASE, "rules"))
    ap.add_argument("--custom", default=os.path.join(BASE, "custom-rules.txt"))
    ap.add_argument("--manifest", default=os.path.join(BASE, "mirrors.txt"))
    args = ap.parse_args()

    cov = load_earlier_coverage(args.lazy, args.ad, args.custom,
                                args.manifest, args.rules_dir)
    print(f"已有判定覆盖: 直连后缀 {len(cov['direct'].suffixes)} / 代理后缀 "
          f"{len(cov['proxy'].suffixes)} / 去广告后缀 {len(cov['ad'].suffixes)}")

    # ---- 候选：v2fly cn 树 + ACL4SSR ----
    cn_tree = expand_cn_tree(args.v2fly_dir)
    acl, acl_ip, acl_kw = load_acl4ssr()
    print(f"v2fly cn 树: {len(cn_tree)} 条目；ACL4SSR: {len(acl)} 条"
          f"（丢弃 IP {acl_ip} / 关键字 {acl_kw}）")

    dropped_re, dropped_kw, dropped_ads = 0, 0, 0

    def usable(entries):
        """筛掉 regexp/keyword/@ads，返回 [(kind, value)]。"""
        nonlocal dropped_re, dropped_kw, dropped_ads
        out = []
        for (kind, value), attrs in entries.items():
            if kind == "regexp":
                dropped_re += 1
                continue
            if kind == "keyword":
                dropped_kw += 1
                continue
            if "@ads" in attrs:
                dropped_ads += 1
                continue
            out.append((kind, value))
        return out

    def blocked_by(covsets, kind, value):
        return any(c.covers_candidate(kind, value) for c in covsets)

    # 补集：cn 树 + ACL4SSR，减去全部已有判定（含代理覆盖——代理证据一票否决）
    a_raw = usable(cn_tree)
    a_blocked_proxy_only = a_blocked_other_only = a_blocked_both = 0
    layer_a = []
    for kind, value in a_raw:
        by_proxy = blocked_by([cov["proxy"]], kind, value)
        by_other = blocked_by([cov["direct"], cov["ad"], cov["manual"],
                               cov["local"]], kind, value)
        if by_proxy and by_other:
            a_blocked_both += 1
        elif by_proxy:
            a_blocked_proxy_only += 1
        elif by_other:
            a_blocked_other_only += 1
        else:
            layer_a.append((kind, value))
    acl_proxy_dropped, acl_blocked = 0, 0
    for kind, value in acl:
        if blocked_by([cov["direct"], cov["ad"], cov["manual"], cov["local"]],
                      kind, value):
            acl_blocked += 1
            continue
        if blocked_by([cov["proxy"]], kind, value):
            # 有代理证据：判回代理，不补（服务不拆分，无例外层）
            acl_proxy_dropped += 1
            continue
        layer_a.append((kind, value))
    print(f"丢弃: 正则 {dropped_re} / 关键字 {dropped_kw} / @ads {dropped_ads}；"
          f"cn 树拦下：仅代理证据 {a_blocked_proxy_only}、仅其他已有判定 "
          f"{a_blocked_other_only}、两者都有 {a_blocked_both}；ACL4SSR 被已有判定拦下 "
          f"{acl_blocked}、被代理证据拦下 {acl_proxy_dropped}")

    layer_a = collapse(layer_a)
    print(f"补集规模: A 层 {len(layer_a)} 条")

    # ---- 出厂守卫 ----
    for kind, value in layer_a:
        if kind not in ("suffix", "exact") or not DOMAIN_RE.match(value):
            fail(f"补集出现非法条目: {kind},{value}")
    for kind, value in layer_a:
        if blocked_by([cov["direct"], cov["proxy"], cov["ad"], cov["manual"],
                       cov["local"]], kind, value):
            fail(f"A 层与已有判定重叠: {kind},{value}")
    if not (MIN_A <= len(layer_a) <= MAX_A):
        fail(f"A 层数量 {len(layer_a)} 超出 [{MIN_A},{MAX_A}]")
    out_a = os.path.join(args.rules_dir, "SupplementA.list")
    prev = read_previous(out_a)
    if prev:
        drift = abs(len(layer_a) - prev) / prev
        print(f"A 层波动: 上一版 {prev} -> 本版 {len(layer_a)}（{drift:.0%}）")
        if drift > MAX_DRIFT:
            fail(f"A 层波动 {drift:.0%} 超过 {MAX_DRIFT:.0%}，疑似上游异常")

    # ---- 写出（全部校验通过后才写）----
    def render(entries, title, sources):
        lines = [
            f"# {title}：直连补集，由 compile_supplement.py 每日编译生成，请勿手动编辑。",
            f"# 来源：{sources}；双边校验后仅含未被已有判定覆盖的域名规则。",
            "# 格式：Shadowrocket RULE-SET（DOMAIN-SUFFIX / DOMAIN，无策略，策略由成品 RULE-SET 行指定）。",
            f"# 条数：{len(entries)}",
        ]
        for kind, value in entries:
            lines.append(f"{'DOMAIN-SUFFIX' if kind == 'suffix' else 'DOMAIN'},{value}")
        return "\n".join(lines) + "\n"

    os.makedirs(args.rules_dir, exist_ok=True)
    with open(out_a, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(layer_a, "SupplementA（常用直连补集）",
                       "v2fly/domain-list-community data/cn + ACL4SSR 精选表"))
    print(f"OK: SupplementA {len(layer_a)} -> {args.rules_dir}")


if __name__ == "__main__":
    main()
