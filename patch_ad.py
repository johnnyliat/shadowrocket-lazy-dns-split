#!/usr/bin/env python3
"""patch_ad.py — lazy.conf + DNS 国内外分流补丁 + sr_ad_only 去广告规则内嵌。

用法: python3 patch_ad.py <上游 lazy.conf> <上游 sr_ad_only.conf> <输出文件>

流水线（三步，顺序固定）:
  1) DNS 补丁（与基础版 patch.py 完全相同）:
     国内双 DoH（阿里+腾讯）写入 dns-server / direct-dns-server / proxy-dns-server；
     删除 fallback-dns-server 并加 dns-fallback-system = false（fail-closed）；
     ipv6 = false；GEOIP,CN,DIRECT 加 no-resolve；FINAL,PROXY 加 force-remote-dns；
     allow-dns-all = false（A/AAAA 之外的查询返回空值、不转发给国内 DNS）。
     生成后另有结构安检：段落集合、[Proxy]/[Proxy Group] 无生效行、无 include、
     规则集地址白名单、[Host]/[MITM]/[URL Rewrite] 与基线一致，防供应链篡改。
  2) 去广告内嵌: 取 sr_ad_only.conf 的全部规则（仅 DOMAIN-SUFFIX / IP-CIDR 两类），
     Reject -> REJECT，IP-CIDR 追加 no-resolve（避免为判 IP 规则触发本地解析），
     整段插入 [Rule] 最前面（优先级最高，先于一切分流规则命中）。
     注意: 不能用 RULE-SET 引用 sr_ad_only —— 它每行带策略且自带 [Rule] 段头，引用必错，只能内嵌。
  3) 自检: 每一步都有断言与计数核对；任一不符即 exit 1，不写出文件。
     这样任一上游改格式，Actions 会失败报警，而不是把半成品推给客户端。
"""
import ipaddress, os, re, sys

DUAL_DOH = "https://dns.alidns.com/dns-query,https://doh.pub/dns-query"

SELF_BASE = "https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/"
MIRRORS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mirrors.txt")

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
CUSTOM_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom-rules.txt")
CUSTOM_DOMAIN_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}
CUSTOM_IP_TYPES = {"IP-CIDR", "IP-CIDR6", "IP6-CIDR"}

def load_custom_rules():
    """读取仓库根目录的 custom-rules.txt（手动规则），严格校验后返回规则行。

    文件不存在 -> 空表（功能关闭）；文件存在但任一行不合规 -> fail，
    整轮构建失败、不出厂。内嵌位置：[Rule] 第一张远程规则表之前，
    优先于全部远程名单，但不越过上游本地功能规则（去广告版里也不越过广告拦截）。
    """
    if not os.path.exists(CUSTOM_FILE):
        return []
    rules, seen = [], {}
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
        rules.append(f"{typ},{value},{policy}" + (",no-resolve" if typ in CUSTOM_IP_TYPES else ""))
    return rules

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
    """结构安检：成品骨架与基线不符即 fail。
    防两件事：一是供应链篡改（塞节点、塞 include、把规则集换到陌生地址、
    改 [Host]/[MITM]/[URL Rewrite]）；二是上游正当改版悄悄改变成品结构。
    宁可停一天等人工确认，也不要自动放行没见过的新骨架。"""
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
        rules.append(f"{typ},{val},REJECT,no-resolve" if typ == "IP-CIDR"
                     else f"{typ},{val},REJECT")
    if len(rules) < 50000:
        fail(f"去广告规则数异常: {len(rules)}（预期 5 万以上，上游可能不完整）")
    # 上游本身带有少量重复行（其合并多张表所致），按首次出现顺序去重，功能等价
    seen, unique = set(), []
    for r in rules:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique

def main(lazy_path, ad_path, out_path):
    ad_rules = load_ad_rules(ad_path)
    custom_rules = load_custom_rules()
    mirrors = load_mirrors()
    mirror_hits = {}
    lines = open(lazy_path, encoding="utf-8").read().splitlines()
    out, section = [], None
    done = set()
    custom_done = False
    for raw in lines:
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
            out.append(raw)
            if s == "[Rule]":
                out.append(f"# ===== 去广告规则（内嵌自 sr_ad_only.conf，共 {len(ad_rules)} 条，每日自动同步）=====")
                out.extend(ad_rules)
                out.append("# ===== 去广告规则结束，以下为 lazy.conf 原有分流规则 =====")
                done.add("ad")
            continue

        if section == "[Rule]" and not custom_done and s.startswith("RULE-SET,"):
            out.append("# ===== 手动规则（custom-rules.txt，优先于全部远程规则表）=====")
            out.extend(custom_rules)
            out.append("# ===== 手动规则结束 =====")
            custom_done = True
            done.add("custom")

        if section == "[General]" and s.startswith("dns-server ="):
            out.append("# [DNS分流补丁] 本地解析全程加密 DoH（阿里+腾讯），不用明文 UDP：运营商看不到查询内容，伪造应答会被 TLS 识破。")
            out.append(f"dns-server = {DUAL_DOH}")
            out.append("# [DNS分流补丁] 直连域名专用 DNS（命中 DIRECT 的国内域名），同为国内双 DoH。")
            out.append(f"direct-dns-server = {DUAL_DOH}")
            out.append("# [DNS分流补丁] 只解析代理节点服务器域名，同为国内双 DoH（双路冗余防单点故障）。")
            out.append(f"proxy-dns-server = {DUAL_DOH}")
            done.add("dns")
            continue

        if section == "[General]" and s.startswith("fallback-dns-server ="):
            out.append("# [DNS分流补丁] 已删除原 fallback-dns-server（原版为 system，会回退运营商 DNS）；境外域名靠规则层交节点远端解析，不需要本地备用解析。")
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

        if section == "[Rule]" and s in ("GEOIP,CN,DIRECT", "GEOIP,CN,DIRECT,no-resolve"):
            out.append("GEOIP,CN,DIRECT,no-resolve")
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

        out.append(raw)

    missing = {"dns", "fallback", "ipv6", "dnsall", "geoip", "final", "ad", "custom"} - done
    if set(mirror_hits) != set(mirrors):
        fail(f"镜像改写不完整，未改写的表: {sorted(set(mirrors) - set(mirror_hits))}（上游可能已增删规则表，先更新 mirrors.txt）")
    if any(v != 1 for v in mirror_hits.values()):
        fail("同一张规则表被改写了不止一次")
    if missing:
        fail(f"锚点缺失，上游格式可能已变化，未完成的步骤: {sorted(missing)}")

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
    if any(l.startswith("fallback-dns-server =") for l in active):
        fail("fallback-dns-server 仍然存在")
    for key in ("dns-server", "direct-dns-server", "proxy-dns-server"):
        if f"{key} = {DUAL_DOH}" not in active:
            fail(f"{key} 双 DoH 缺失")
    if "dns-fallback-system = false" not in active or "ipv6 = false" not in active:
        fail("fail-closed / ipv6 行缺失")
    if [l for l in active if l.startswith("allow-dns-all")] != ["allow-dns-all = false"]:
        fail("allow-dns-all 不是恰好一条且为 false")
    assert_structure(text)
    rejects = [l for l in rule_active if l.endswith(",REJECT") or l.endswith(",REJECT,no-resolve")]
    if len(rejects) != len(ad_rules):
        fail(f"内嵌去广告计数不符: 文件内 {len(rejects)} vs 上游 {len(ad_rules)}")
    if rule_active[:len(ad_rules)] != ad_rules:
        fail("去广告规则不在 [Rule] 最前面或顺序被打乱")
    if not rule_active or rule_active[-1] != "FINAL,PROXY,force-remote-dns":
        fail("FINAL 不是 [Rule] 最后一条规则")
    rs_urls = [l.split(",", 2)[1] for l in rule_active if l.startswith(("RULE-SET,", "DOMAIN-SET,"))]
    if sorted(rs_urls) != sorted(mirrors.values()):
        fail("成品引用的规则表与 mirrors.txt 清单不一致（存在未镜像的表，或缺失/重复）")


    # 手动规则块必须存在、位于第一张远程规则表之前，且规则完整内嵌
    _h = text.find("# ===== 手动规则（custom-rules.txt")
    _f = text.find("# ===== 手动规则结束 =====")
    _rs = text.find("\nRULE-SET,")
    if _h < 0 or _f < 0 or not (_h < _f < _rs):
        fail("手动规则块缺失或位置异常（应在第一张远程规则表之前）")
    _inner = "\n" + text[_h:_f] + "\n"
    for _r in custom_rules:
        if f"\n{_r}\n" not in _inner:
            fail(f"手动规则未完整内嵌: {_r}")
    header = (
        "# ============================================================\n"
        "# lazy.conf（无策略组版）+ DNS 国内外分流补丁 + 去广告规则（自动生成，请勿手动编辑此文件）\n"
        "# 上游: https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/lazy.conf\n"
        "#       https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/sr_ad_only.conf\n"
        "#       （懒人配置同步自 LOWERTOP/Shadowrocket，版权与署名归上游所有）\n"
        f"# 内容: lazy 分流规则 + sr_ad_only 去广告规则 {len(ad_rules)} 条全量内嵌于 [Rule] 最前面（REJECT）；\n"
        "#       DNS 仅改机制（以 [DNS分流补丁] 注释标出），其余与上游逐字一致。\n"
        "#       另内嵌 custom-rules.txt 手动直连/代理规则于全部远程规则表之前（优先于名单，广告拦截仍更优先）。\n"
        "#   国内: dns-server / direct-dns-server / proxy-dns-server = 阿里 DoH + 腾讯 DoH（加密）\n"
        "#   国外: 无本地解析 —— GEOIP 加 no-resolve、FINAL,PROXY,force-remote-dns 交节点远端解析；\n"
        "#         删除 fallback-dns-server 并设 dns-fallback-system = false（fail-closed）；ipv6 = false；\n"
        "#         allow-dns-all = false（A/AAAA 之外的查询返回空值、不转发国内 DNS）；\n"
        "#         全部 34 张规则表改引自托管镜像 rules/*.list（与上游逐行一致、仅补 no-resolve；QX 格式四张表的 IP 规则原本无此标记、会触发本地解析）。\n"
        "#         注: [Host] 中 Apple/iCloud 的 server:system 为上游有意的兼容设计，原样保留。\n"
        "# 本文件由 GitHub Actions 每日自动同步两个上游并重新生成。\n"
        "# ============================================================\n"
    )
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + text)
    print(f"OK: ad_rules={len(ad_rules)} -> {out_path}")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        fail("用法: python3 patch.py <lazy.conf> <sr_ad_only.conf> <output>")
    main(sys.argv[1], sys.argv[2], sys.argv[3])
