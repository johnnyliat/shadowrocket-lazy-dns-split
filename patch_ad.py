#!/usr/bin/env python3
"""patch_ad.py — lazy.conf + DNS 补丁 + 直连补集 + sr_ad_only 去广告规则内嵌。

用法: python3 patch_ad.py <上游 lazy.conf> <上游 sr_ad_only.conf> <输出文件>
      [--dns-mode=legacy|new]（含义与 patch.py 相同，默认 legacy）

流水线（顺序固定）:
  1) DNS 补丁（与基础版 patch.py 完全相同，见 patch.py 文档）。
  2) 八组排序（与基础版相同，另加第 2 组去广告，位于手动规则之后、
     上游规则之前）:
       1A 手动直连 / 1B 手动代理（custom-rules.txt，权力最高）
       2  去广告规则（sr_ad_only 全量内嵌，REJECT）
       3  上游本地规则；4 代理规则表；5 直连规则表；
       6  A 层补集；7 GEOIP,CN；8 FINAL,PROXY,force-remote-dns
     注意: 不能用 RULE-SET 引用 sr_ad_only —— 它每行带策略且自带 [Rule]
     段头，引用必错，只能内嵌。
  3) 重叠守卫：去广告规则中"整个匹配范围都被手动规则盖住"的（即永远
     轮不到它、纯属死规则），剔除并记入构建日志；手动规则高于广告拦截，
     明确意图不被批量名单否决。部分重叠的保留（顺序会正确裁决）。
  4) 自检: 每一步都有断言与计数核对；任一不符即 exit 1，不写出文件。
"""
import ipaddress, os, re, sys

DUAL_DOH = "https://dns.alidns.com/dns-query,https://doh.pub/dns-query"
FOREIGN_DOH = "https://cloudflare-dns.com/dns-query#proxy,https://dns.google/dns-query#proxy"

SELF_BASE = "https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/"
BASE = os.path.dirname(os.path.abspath(__file__))
MIRRORS_FILE = os.path.join(BASE, "mirrors.txt")
SUPPLEMENT_A_URL = SELF_BASE + "rules/SupplementA.list"
SUPPLEMENT_A_FILE = os.path.join(BASE, "rules", "SupplementA.list")

def load_mirrors():
    """读 mirrors.txt，返回 {上游地址: 自家镜像地址}。成品引用的每张规则表
    都必须在清单内并改指自家镜像；清单外的表不会被改写，随后的自检会拦下。"""
    if not os.path.exists(MIRRORS_FILE):
        fail("缺少 mirrors.txt")
    m = {}
    for raw in open(MIRRORS_FILE, encoding="utf-8"):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) != 2:
            fail(f"mirrors.txt 行格式不对: {s!r}")
        m[parts[0]] = SELF_BASE + parts[1]
    if not m:
        fail("mirrors.txt 是空的")
    return m

CUSTOM_FILE = os.path.join(BASE, "custom-rules.txt")
CUSTOM_DOMAIN_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}
CUSTOM_IP_TYPES = {"IP-CIDR", "IP-CIDR6", "IP6-CIDR"}

def load_custom_rules():
    """读取 custom-rules.txt（手动规则），严格校验后返回 (直连规则, 代理规则)。
    与 patch.py 完全一致：同一目标只能有一条规则；内嵌于 [Rule] 最前面
    （1A/1B 两组），优先于一切含广告拦截。"""
    if not os.path.exists(CUSTOM_FILE):
        return [], []
    direct, proxy, seen = [], [], {}
    for lineno, raw in enumerate(open(CUSTOM_FILE, encoding="utf-8"), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = [x.strip() for x in s.split(",")]
        typ = parts[0].upper()
        if len(parts) < 3 or typ not in CUSTOM_DOMAIN_TYPES | CUSTOM_IP_TYPES:
            fail(f"custom-rules.txt 第 {lineno} 行格式不支持: {s!r}")
        value, policy = parts[1], parts[2].upper()
        if policy not in ("DIRECT", "PROXY"):
            fail(f"custom-rules.txt 第 {lineno} 行策略只允许 DIRECT 或 PROXY: {s!r}")
        if typ in CUSTOM_IP_TYPES:
            if len(parts) != 4 or parts[3].lower() != "no-resolve":
                fail(f"custom-rules.txt 第 {lineno} 行 IP 规则必须写成 TYPE,段,DIRECT/PROXY,no-resolve: {s!r}")
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError:
                fail(f"custom-rules.txt 第 {lineno} 行 IP 段不合法: {s!r}")
        elif len(parts) != 3 or not value or " " in value or "/" in value:
            fail(f"custom-rules.txt 第 {lineno} 行域名规则格式不对: {s!r}")
        key = (typ, value.lower())
        if key in seen:
            fail(f"custom-rules.txt 第 {lineno} 行与第 {seen[key]} 行重复或冲突（同一目标只能有一条规则）: {s!r}")
        seen[key] = lineno
        line = f"{typ},{value},{policy}" + (",no-resolve" if typ in CUSTOM_IP_TYPES else "")
        (direct if policy == "DIRECT" else proxy).append(line)
    return direct, proxy

AD_LINE = re.compile(r"^(DOMAIN-SUFFIX|IP-CIDR),([^,]+),Reject$")

def fail(msg):
    print(f"PATCH FAILED: {msg}", file=sys.stderr)
    sys.exit(1)

# ---- 结构安检基线（2026-11-09 按线上成品实测值设定，与 patch.py 保持一致）----
ALLOWED_SECTIONS = {"[General]", "[Proxy]", "[Proxy Group]", "[Rule]", "[Host]", "[URL Rewrite]", "[MITM]"}
ALLOWED_RS_PREFIXES = (
    "https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/",
)
EXPECTED_HOST = {"*.apple.com = server:system", "*.icloud.com = server:system", "localhost = 127.0.0.1"}
EXPECTED_MITM = {"hostname = *.google.cn"}
EXPECTED_REWRITE = {
    "^https?://(www.)?g.cn https://www.google.com 302",
    "^https?://(www.)?google.cn https://www.google.com 302",
}

def assert_structure(text):
    """结构安检：成品骨架与基线不符即 fail（与 patch.py 一致）。"""
    sec, seen = None, {}
    for raw in text.splitlines():
        t = raw.strip()
        if t.startswith("[") and t.endswith("]"):
            sec = t
            if sec not in ALLOWED_SECTIONS:
                fail(f"结构安检：出现未知段 {sec}")
            continue
        if not t or t.startswith("#"):
            continue
        seen.setdefault(sec, []).append(t)
        if sec in ("[Proxy]", "[Proxy Group]"):
            fail(f"结构安检：{sec} 出现生效行: {t}")
        if sec == "[General]" and t.lower().startswith("include"):
            fail(f"结构安检：[General] 出现 include: {t}")
        if sec == "[Rule]":
            if t.startswith(("SCRIPT,", "URL-REGEX,")):
                fail(f"结构安检：不允许的规则类型: {t}")
            if t.startswith(("RULE-SET,", "DOMAIN-SET,")):
                url = t.split(",", 2)[1]
                if not url.startswith(ALLOWED_RS_PREFIXES):
                    fail(f"结构安检：规则集地址不在白名单: {url}")
    if set(seen.get("[Host]", [])) != EXPECTED_HOST:
        fail(f"结构安检：[Host] 内容变化: {seen.get('[Host]')}")
    if set(seen.get("[MITM]", [])) != EXPECTED_MITM:
        fail(f"结构安检：[MITM] 内容变化: {seen.get('[MITM]')}")
    if set(seen.get("[URL Rewrite]", [])) != EXPECTED_REWRITE:
        fail(f"结构安检：[URL Rewrite] 内容变化: {seen.get('[URL Rewrite]')}")

def load_ad_rules(path):
    rules, section = [], None
    for raw in open(path, encoding="utf-8"):
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
            continue
        if section != "[Rule]" or not s or s.startswith("#"):
            continue
        m = AD_LINE.match(s)
        if not m:
            fail(f"sr_ad_only 出现未预期的规则行格式: {s!r}")
        typ, val = m.groups()
        if typ == "IP-CIDR":
            try:
                ipaddress.ip_network(val, strict=False)
            except ValueError:
                fail(f"sr_ad_only 的 IP 段不合法: {s!r}")
            rules.append(f"{typ},{val},REJECT,no-resolve")
        else:
            rules.append(f"{typ},{val},REJECT")
    if len(rules) < 50000:
        fail(f"去广告规则数异常: {len(rules)}（预期 5 万以上，上游可能不完整）")
    # 上游本身带有少量重复行（其合并多张表所致），按首次出现顺序去重，功能等价
    seen, unique = set(), []
    for r in rules:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique

def drop_shadowed_ad_rules(ad_rules, custom_direct, custom_proxy):
    """重叠守卫：剔除"整个匹配范围都被手动规则盖住"的去广告规则（死规则）。
    域名后缀：父后缀在手动后缀集里，或手动关键字是其子串；IP 段：是手动
    IP 段的子网。部分重叠的保留（手动在上，顺序裁决正确）。返回 (保留, 剔除)。"""
    suffixes, keywords, nets = set(), set(), []
    for line in custom_direct + custom_proxy:
        parts = line.split(",")
        typ, val = parts[0].upper(), parts[1].lower()
        if typ == "DOMAIN-SUFFIX":
            suffixes.add(val)
        elif typ == "DOMAIN-KEYWORD":
            keywords.add(val)
        elif typ in ("IP-CIDR", "IP-CIDR6", "IP6-CIDR"):
            nets.append(ipaddress.ip_network(parts[1], strict=False))
    kept, dropped = [], []
    for r in ad_rules:
        typ, val = r.split(",")[0], r.split(",")[1]
        shadowed = False
        if typ == "DOMAIN-SUFFIX":
            v = val.lower()
            parts = v.split(".")
            if any(".".join(parts[i:]) in suffixes for i in range(len(parts))):
                shadowed = True
            elif any(k in v for k in keywords):
                shadowed = True
        elif typ == "IP-CIDR":
            net = ipaddress.ip_network(val, strict=False)
            if any(net.version == m.version and net.subnet_of(m) for m in nets):
                shadowed = True
        (dropped if shadowed else kept).append(r)
    return kept, dropped

def count_active_rules(path):
    n = 0
    for raw in open(path, encoding="utf-8"):
        s = raw.strip()
        if s and not s.startswith("#"):
            n += 1
    return n

def main(lazy_path, ad_path, out_path, dns_mode):
    ad_rules = load_ad_rules(ad_path)
    custom_direct, custom_proxy = load_custom_rules()
    ad_rules, dropped = drop_shadowed_ad_rules(ad_rules, custom_direct, custom_proxy)
    if dropped:
        print(f"重叠守卫：剔除被手动规则完全盖住的去广告规则 {len(dropped)} 条：")
        for r in dropped[:20]:
            print(f"  dropped: {r}")
        if len(dropped) > 20:
            print(f"  ... 及另外 {len(dropped) - 20} 条")
    mirrors = load_mirrors()
    for p in (SUPPLEMENT_A_FILE,):
        if not os.path.exists(p):
            fail(f"补集表缺失（compile_supplement.py 须先运行）: {p}")
    n_a = count_active_rules(SUPPLEMENT_A_FILE)
    mirror_hits = {}
    local_rules = []
    lines = open(lazy_path, encoding="utf-8").read().splitlines()
    out, section = [], None
    done = set()
    for raw in lines:
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
            out.append(raw)
            continue

        if section == "[General]" and s.startswith("dns-server ="):
            if dns_mode == "legacy":
                out.append("# [DNS分流补丁] 本地解析全程加密 DoH（阿里+腾讯），不用明文 UDP：运营商看不到查询内容，伪造应答会被 TLS 识破。")
                out.append(f"dns-server = {DUAL_DOH}")
                out.append("# [DNS分流补丁] 直连域名专用 DNS（命中 DIRECT 的国内域名），同为国内双 DoH。")
                out.append(f"direct-dns-server = {DUAL_DOH}")
                out.append("# [DNS分流补丁] 只解析代理节点服务器域名，同为国内双 DoH（双路冗余防单点故障）。")
                out.append(f"proxy-dns-server = {DUAL_DOH}")
            else:
                out.append("# [DNS分流补丁-new] dns-server 只负责 IP 规则触发的预解析：国外双 DoH（Cloudflare + Google）经代理查询（#proxy），查询全程在隧道内，国内 DNS 看不到；两家并行查询、快的赢。")
                out.append(f"dns-server = {FOREIGN_DOH}")
                out.append("# [DNS分流补丁-new] 直连域名专用 DNS（命中 DIRECT 的国内域名），国内双 DoH，不依赖代理。")
                out.append(f"direct-dns-server = {DUAL_DOH}")
                out.append("# [DNS分流补丁-new] 只解析代理节点服务器域名，国内双 DoH（建隧道前必须能解析，不走代理）。")
                out.append(f"proxy-dns-server = {DUAL_DOH}")
            done.add("dns")
            continue

        if section == "[General]" and s.startswith("fallback-dns-server ="):
            out.append("# [DNS分流补丁] fallback-dns-server 写明国内双 DoH（原版为 system）：主查询失败时回落到加密的国内 DNS，不回落系统 DNS；实测留空会在代理故障时回落系统 DNS。")
            out.append(f"fallback-dns-server = {DUAL_DOH}")
            out.append("dns-fallback-system = false")
            done.add("fallback")
            continue

        if section == "[General]" and s in ("ipv6 = true", "ipv6 = false"):
            if s == "ipv6 = true":
                out.append("# [DNS分流补丁] ipv6 由 true 改为 false：IPv6 通道会绕过 DNS 分流。")
            out.append("ipv6 = false")
            done.add("ipv6")
            continue

        if section == "[General]" and s == "# allow-dns-all =":
            out.append("# [DNS分流补丁] allow-dns-all 显式设为 false（上游此行原为注释、软件默认开启）：默认开启时 A/AAAA 之外的 HTTPS/SVCB/TXT 等查询会转发给上游 DNS（即国内 DoH），境外域名会以冷门查询类型在本地留痕；设为 false 后这类查询返回空值、不发出。")
            out.append("allow-dns-all = false")
            done.add("dnsall")
            continue

        if section == "[General]" and s.startswith("tun-excluded-routes ="):
            if "100.64.0.0/10" not in s:
                out.append("# [局域网补丁] tun-excluded-routes 补上 100.64.0.0/10（运营商级 NAT 网段），与既有私有网段同等 bypass。")
                out.append(s + ", 100.64.0.0/10")
            else:
                out.append(raw)
            done.add("tunex")
            continue

        if section == "[General]" and s.startswith("skip-proxy ="):
            if "100.64.0.0/10" not in s:
                out.append("# [局域网补丁] skip-proxy 补上 100.64.0.0/10（运营商级 NAT 网段）。")
                out.append(s + ",100.64.0.0/10")
            else:
                out.append(raw)
            done.add("skipproxy")
            continue

        if section == "[Rule]" and s in ("GEOIP,CN,DIRECT", "GEOIP,CN,DIRECT,no-resolve"):
            if dns_mode == "legacy":
                out.append("GEOIP,CN,DIRECT,no-resolve")
            else:
                out.append("# [DNS分流补丁-new] GEOIP 去掉 no-resolve：未知域名经 dns-server（国外 DoH #proxy）查 IP 后按归属分流；与 #proxy 配对才安全，联动锁在自检里断言。")
                out.append("GEOIP,CN,DIRECT")
            done.add("geoip")
            continue

        if section == "[Rule]" and s in ("FINAL,PROXY", "FINAL,PROXY,force-remote-dns"):
            out.append("FINAL,PROXY,force-remote-dns")
            done.add("final")
            continue

        if section == "[Rule]" and s.startswith("RULE-SET,"):
            url = s.split(",", 2)[1]
            if url in mirrors:
                out.append(s.replace(url, mirrors[url], 1))
                mirror_hits[url] = mirror_hits.get(url, 0) + 1
            else:
                out.append(raw)
            continue

        if section == "[Rule]" and s and not s.startswith("#") and not s.startswith("DOMAIN-SET,"):
            # 上游本地字面规则：与 patch.py 相同，收集后集中放到第 3 组；
            # 只收域名/IP 类规则，且全部为 PROXY 才允许上移（越过 PROXY 表
            # 不改变判定），否则失败等人工确认。
            parts = s.split(",")
            if parts[0].strip().upper() not in ("DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD",
                                                "DOMAIN-WILDCARD", "IP-CIDR", "IP-CIDR6", "IP6-CIDR"):
                fail(f"上游本地规则类型不在预期内，无法安全归入第 3 组，需人工确认: {s!r}")
            if len(parts) < 3 or parts[2].strip().upper() != "PROXY":
                fail(f"上游本地规则不是 PROXY 策略，无法安全归入第 3 组，需人工确认: {s!r}")
            local_rules.append(s)
            continue

        out.append(raw)

    missing = {"dns", "fallback", "ipv6", "dnsall", "geoip", "final", "tunex", "skipproxy"} - done
    if set(mirror_hits) != set(mirrors):
        fail(f"镜像改写不完整，未改写的表: {sorted(set(mirrors) - set(mirror_hits))}（上游可能已增删规则表，先更新 mirrors.txt）")
    if any(v != 1 for v in mirror_hits.values()):
        fail("同一张规则表被改写了不止一次")
    if missing:
        fail(f"锚点缺失，上游格式可能已变化，未打上的补丁: {sorted(missing)}")

    # ---- 八组组装（自下而上拼接，索引不漂移；与 patch.py 同构）----
    def find_idx(pred, what):
        for i, l in enumerate(out):
            if pred(l.strip()):
                return i
        fail(f"组装锚点缺失: {what}")

    first_rs = find_idx(lambda t: t.startswith("RULE-SET,"), "第一张规则表")
    geoip_idx = find_idx(lambda t: t in ("GEOIP,CN,DIRECT", "GEOIP,CN,DIRECT,no-resolve"), "GEOIP 行")

    a_line = f"RULE-SET,{SUPPLEMENT_A_URL},DIRECT"
    out[geoip_idx:geoip_idx] = [
        f"# ===== 第 6 组：A 层补集（SupplementA.list，{n_a} 条常用直连，双边校验后仅含未被已有判定覆盖者）=====",
        a_line,
        "# ===== 第 7/8 组：IP 推断与兜底 =====",
    ]
    out[first_rs:first_rs] = [
        "# ===== 第 4/5 组：上游规则表（原版顺序：代理判定在前、直连判定在后，Global/China 兜底表居尾）=====",
    ]
    rule_hdr = find_idx(lambda t: t == "[Rule]", "[Rule] 段头")
    out[rule_hdr + 1:rule_hdr + 1] = (
        ["# ===== 第 1A 组：手动直连（custom-rules.txt 的 DIRECT 规则，权力最高）====="]
        + custom_direct
        + ["# ===== 第 1B 组：手动代理（custom-rules.txt 的 PROXY 规则，权力最高）====="]
        + custom_proxy
        + [f"# ===== 第 2 组：去广告规则（内嵌自 sr_ad_only.conf，共 {len(ad_rules)} 条，每日自动同步；重叠守卫已剔除 {len(dropped)} 条）====="]
        + ad_rules
        + ["# ===== 去广告规则结束，以下为分流规则 ====="]
        + ["# ===== 第 3 组：上游本地规则（原文件散在规则表之间，全部 PROXY，集中于此不改变判定）====="]
        + local_rules
    )

    text = "\n".join(out) + "\n"

    # ---- 自检 ----
    active, sec = [], None
    rule_active = []
    for l in text.splitlines():
        t = l.strip()
        if t.startswith("[") and t.endswith("]"):
            sec = t
            continue
        if t and not t.startswith("#"):
            active.append(t)
            if sec == "[Rule]":
                rule_active.append(t)
    if sum(1 for l in active if l.startswith("dns-server =")) != 1:
        fail("dns-server 不是恰好一条")
    if sum(1 for l in active if l.startswith("fallback-dns-server =")) != 1 or f"fallback-dns-server = {DUAL_DOH}" not in active:
        fail("fallback-dns-server 不是恰好一条国内双 DoH")
    if dns_mode == "legacy":
        for key in ("dns-server", "direct-dns-server", "proxy-dns-server"):
            if f"{key} = {DUAL_DOH}" not in active:
                fail(f"{key} 双 DoH 缺失（legacy）")
    else:
        if f"dns-server = {FOREIGN_DOH}" not in active or f"direct-dns-server = {DUAL_DOH}" not in active \
           or f"proxy-dns-server = {DUAL_DOH}" not in active:
            fail("DNS 三行不完整（new）")
    if "dns-fallback-system = false" not in active or "ipv6 = false" not in active:
        fail("fail-closed / ipv6 行缺失")
    # [General] 设计基线：与 patch.py 一致（上游透传、设计依赖，删改即失败）。
    for baseline in ("dns-direct-system = false",
                     "dns-direct-fallback-proxy = true",
                     "private-ip-answer = true"):
        if sum(1 for l in active if l == baseline) != 1:
            fail(f"[General] 设计基线缺失或重复: {baseline}")
    if [l for l in active if l.startswith("allow-dns-all")] != ["allow-dns-all = false"]:
        fail("allow-dns-all 不是恰好一条且为 false")
    for key in ("tun-excluded-routes =", "skip-proxy ="):
        hits = [l for l in active if l.startswith(key)]
        if len(hits) != 1 or "100.64.0.0/10" not in hits[0]:
            fail(f"{key} 缺少 100.64.0.0/10")
    assert_structure(text)
    # DNS 配对联动锁：GEOIP 无 no-resolve 当且仅当 dns-server 带 #proxy
    geoip_lines = [l for l in active if l.startswith("GEOIP,")]
    dns_has_proxy = "#proxy" in [l for l in active if l.startswith("dns-server =")][0]
    geoip_bare = geoip_lines == ["GEOIP,CN,DIRECT"]
    geoip_guarded = geoip_lines == ["GEOIP,CN,DIRECT,no-resolve"]
    if not ((geoip_bare and dns_has_proxy) or (geoip_guarded and not dns_has_proxy)):
        fail(f"DNS 配对联动锁触发：GEOIP 行 {geoip_lines} 与 dns-server #proxy 配对不一致")
    n1 = len(custom_direct) + len(custom_proxy)
    if rule_active[:len(custom_direct)] != custom_direct or \
       rule_active[len(custom_direct):n1] != custom_proxy:
        fail("手动规则 1A/1B 两组不在 [Rule] 最前面或顺序被打乱")
    rejects = [l for l in rule_active if l.endswith(",REJECT") or l.endswith(",REJECT,no-resolve")]
    if len(rejects) != len(ad_rules):
        fail(f"内嵌去广告计数不符: 文件内 {len(rejects)} vs 应内嵌 {len(ad_rules)}")
    if rule_active[n1:n1 + len(ad_rules)] != ad_rules:
        fail("去广告规则不在手动规则之后或顺序被打乱")
    if not rule_active or rule_active[-1] != "FINAL,PROXY,force-remote-dns":
        fail("FINAL 不是 [Rule] 最后一条规则")
    rs_idx = [i for i, l in enumerate(rule_active) if l.startswith(("RULE-SET,", "DOMAIN-SET,"))]
    if not rs_idx:
        fail("[Rule] 里没有规则表")
    first_rs_i = rs_idx[0]
    prefix = n1 + len(ad_rules)
    # 第 3 组：上游本地规则紧跟去广告块，且之后直接就是第一张规则表
    if rule_active[prefix:prefix + len(local_rules)] != local_rules:
        fail("第 3 组上游本地规则位置异常")
    if first_rs_i != prefix + len(local_rules):
        fail("第 3 组与第一张规则表之间出现多余规则")
    geoip_i = next(i for i, l in enumerate(rule_active) if l.startswith("GEOIP,"))
    if rule_active[geoip_i - 1] != a_line:
        fail("A 层补集行不在 GEOIP 之前")
    rs_urls = [rule_active[i].split(",", 2)[1] for i in rs_idx]
    if sorted(rs_urls) != sorted(list(mirrors.values()) + [SUPPLEMENT_A_URL]):
        fail("成品引用的规则表与清单不一致（34 张镜像 + 1 张补集，存在未镜像的表，或缺失/重复）")
    if len(rs_urls) != len(set(rs_urls)):
        fail("规则表引用有重复")

    dns_note = (
        "#   国内: dns-server / direct-dns-server / proxy-dns-server = 阿里 DoH + 腾讯 DoH（加密）\n"
        "#   国外: 无本地解析 —— GEOIP 加 no-resolve、FINAL,PROXY,force-remote-dns 交节点远端解析；\n"
        "#         fallback-dns-server 写明国内双 DoH，dns-fallback-system = false（主查询失败时不回落系统 DNS）；ipv6 = false；\n"
        "#         allow-dns-all = false（A/AAAA 之外的查询返回空值、不转发国内 DNS）；\n"
    ) if dns_mode == "legacy" else (
        "#   国外预解析: dns-server = Cloudflare DoH + Google DoH，经代理查询（#proxy），\n"
        "#         未知域名先经隧道查 IP 再按归属分流；GEOIP 去掉 no-resolve，与 #proxy 配对（联动锁断言）。\n"
        "#   国内: direct-dns-server = 阿里 DoH + 腾讯 DoH（加密）；proxy-dns-server 同为国内双 DoH（只解析节点域名）。\n"
        "#   fallback-dns-server 写明国内双 DoH，dns-fallback-system = false（主查询失败时不回落系统 DNS）；ipv6 = false；\n"
        "#         allow-dns-all = false（A/AAAA 之外的查询返回空值、不转发国内 DNS）；\n"
    )
    header = (
        "# ============================================================\n"
        "# lazy.conf（无策略组版）+ DNS 国内外分流补丁 + 去广告规则 + 直连补集（自动生成，请勿手动编辑此文件）\n"
        "# 上游: https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/lazy.conf\n"
        "#       https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/sr_ad_only.conf\n"
        "#       （懒人配置同步自 LOWERTOP/Shadowrocket，版权与署名归上游所有）\n"
        f"# 内容: lazy 分流规则 + sr_ad_only 去广告规则 {len(ad_rules)} 条内嵌于第 2 组（REJECT；重叠守卫剔除 {len(dropped)} 条）；\n"
        "#       DNS 仅改机制、规则仅改排序（均以注释标出），名单内容与上游逐字一致。\n"
        "# 排序（先命中先赢，共八组，组内上游相对顺序不动）:\n"
        "#   1A 手动直连 / 1B 手动代理（custom-rules.txt，权力最高）；2 去广告；3 上游本地规则；\n"
        "#   4 代理规则表；5 直连规则表；\n"
        f"#   6 A 层补集（{n_a} 条常用直连）；7 GEOIP,CN；8 FINAL,PROXY,force-remote-dns。\n"
        f"# 补集由 compile_supplement.py 每日编译（v2fly data/cn + ACL4SSR 精选表，双边校验：有代理证据的一律不补）。\n"
        + dns_note +
        "#         全部 34 张规则表改引自托管镜像 rules/*.list（与上游逐行一致、仅补 no-resolve；QX 格式四张表的 IP 规则原本无此标记、会触发本地解析）。\n"
        "#         注: [Host] 中 Apple/iCloud 的 server:system 为上游有意的兼容设计，原样保留。\n"
        "# 本文件由 GitHub Actions 每日自动同步两个上游并重新生成。\n"
        "# ============================================================\n"
    )
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + text)
    print(f"OK: ad_rules={len(ad_rules)} dropped={len(dropped)} (dns-mode={dns_mode}) -> {out_path}")

if __name__ == "__main__":
    args = sys.argv[1:]
    dns_mode = "legacy"
    if args and args[-1].startswith("--dns-mode="):
        dns_mode = args.pop()[len("--dns-mode="):]
        if dns_mode not in ("legacy", "new"):
            fail("用法: python3 patch_ad.py <lazy.conf> <sr_ad_only.conf> <output> [--dns-mode=legacy|new]")
    if len(args) != 3:
        fail("用法: python3 patch_ad.py <lazy.conf> <sr_ad_only.conf> <output> [--dns-mode=legacy|new]")
    main(args[0], args[1], args[2], dns_mode)
