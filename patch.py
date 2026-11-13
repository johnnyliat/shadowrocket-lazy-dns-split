#!/usr/bin/env python3
"""patch.py — 给上游 lazy.conf 打补丁，生成基础版成品 lazy-dns.conf。

用法: python3 patch.py <上游 lazy.conf 路径> <输出路径> [--dns-mode=legacy|new]
  --dns-mode=legacy（默认，旧模式）: 全部 DNS 走国内双 DoH，
    GEOIP 带 no-resolve（未知域名不解析、直接落 FINAL 走代理）。
  --dns-mode=new（线上现行）: dns-server 改国外双 DoH
    经代理查询（Cloudflare + Google，#proxy），GEOIP 去掉 no-resolve，
    未知域名先经隧道查 IP 再按归属分流；direct/proxy-dns-server 仍为
    国内双 DoH（直连域名与节点域名不依赖代理）。

补丁内容（除 DNS 与排序外，其余与上游逐字一致）:
  DNS（两模式公共部分）:
    - 删除 fallback-dns-server，并加 dns-fallback-system = false（fail-closed）
    - allow-dns-all = false（A/AAAA 之外的查询返回空值、不转发国内 DNS）
    - ipv6 = false（v6 通道会绕过 DNS 分流）
    - FINAL,PROXY -> FINAL,PROXY,force-remote-dns（境外域名交节点远端解析）
  局域网：tun-excluded-routes 与 skip-proxy 补上 100.64.0.0/10（运营商级
    NAT 网段），与既有私有网段同等 bypass。
  [Rule] 八组排序（先命中先赢，顺序即权力等级；组内上游相对顺序不动）:
    1A 手动直连（custom-rules.txt 的 DIRECT 规则）
    1B 手动代理（custom-rules.txt 的 PROXY 规则）
    2  去广告（仅去广告版，本文件无此组）
    3  上游本地规则（原文件 [Rule] 里的字面规则，如 3 条 PROXY）
    4  代理规则表（上游 RULE-SET，原版顺序）
    5  直连规则表（上游 RULE-SET，原版顺序，Global/China 兜底表居尾）
    6  A 层补集 RULE-SET（SupplementA.list，常用直连补集）
    7  GEOIP,CN,DIRECT（IP 推断；legacy 带 no-resolve，new 不带）
    8  FINAL,PROXY,force-remote-dns（兜底）
    补集表由 compile_supplement.py 每日编译生成（v2fly + ACL4SSR，
    双边校验：有代理证据的一律不补，服务不拆分），本脚本只引用、不生产；
    表缺失即构建失败。
  结构安检: 生成后核对成品骨架（段落集合、[Proxy]/[Proxy Group] 无生效行、
    无 include、规则集地址白名单、[Host]/[MITM]/[URL Rewrite] 内容与基线一致、
    八组位置断言、DNS 配对联动锁），上游骨架有任何预期外变化即失败。

原则: 每处修改都有断言。锚点找不到就报错退出（exit 1），绝不生成半成品。
"""
import ipaddress, os, sys

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

    文件不存在 -> 两组都空（功能关闭）；任一行不合规 -> fail。
    同一目标只能有一条规则（跨策略重复也算冲突），故直连/代理两组拆开
    摆放与合在一起行为完全相同。内嵌位置：[Rule] 最前面（1A/1B 两组），
    优先于一切含广告拦截——明确意图大于批量名单。"""
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


def fail(msg):
    print(f"PATCH FAILED: {msg}", file=sys.stderr)
    sys.exit(1)

# ---- 结构安检基线（2026-11-09 按线上成品实测值设定）----
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
    """结构安检：成品骨架与基线不符即 fail。"""
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

def count_active_rules(path):
    n = 0
    for raw in open(path, encoding="utf-8"):
        s = raw.strip()
        if s and not s.startswith("#"):
            n += 1
    return n

def main(src_path, out_path, dns_mode):
    lines = open(src_path, encoding="utf-8").read().splitlines()
    custom_direct, custom_proxy = load_custom_rules()
    mirrors = load_mirrors()
    for p in (SUPPLEMENT_A_FILE,):
        if not os.path.exists(p):
            fail(f"补集表缺失（compile_supplement.py 须先运行）: {p}")
    n_a = count_active_rules(SUPPLEMENT_A_FILE)
    mirror_hits = {}
    local_rules = []
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
            # 上游本地字面规则：原文件里散在规则表之间（如 litix.io 等 3 条），
            # 这里收集起来、组装时集中放到第 3 组。只收域名/IP 类规则，且
            # 全部为 PROXY 才允许上移：它越过的只有 PROXY 规则表（同策略），
            # 故不改变任何判定；出现其他类型或非 PROXY 规则即失败等人工确认
            #（USER-AGENT / DST-PORT / 逻辑组合等规则上移会改变裁决 precedence，
            # 不能悄悄归入第 3 组）。
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

    # ---- 八组组装（自下而上拼接，索引不漂移）----
    def find_idx(pred, what):
        for i, l in enumerate(out):
            if pred(l.strip()):
                return i
        fail(f"组装锚点缺失: {what}")

    rule_hdr = find_idx(lambda t: t == "[Rule]", "[Rule] 段头")
    first_rs = find_idx(lambda t: t.startswith("RULE-SET,"), "第一张规则表")
    geoip_idx = find_idx(lambda t: t in ("GEOIP,CN,DIRECT", "GEOIP,CN,DIRECT,no-resolve"), "GEOIP 行")

    a_line = f"RULE-SET,{SUPPLEMENT_A_URL},DIRECT"
    # GEOIP 之前：A 层 + 尾巴横幅
    out[geoip_idx:geoip_idx] = [
        f"# ===== 第 6 组：A 层补集（SupplementA.list，{n_a} 条常用直连，双边校验后仅含未被已有判定覆盖者）=====",
        a_line,
        "# ===== 第 7/8 组：IP 推断与兜底 =====",
    ]
    # 第一张上游规则表之前：上游表横幅（A 行插在更后方，不影响此处索引）
    out[first_rs:first_rs] = [
        "# ===== 第 4/5 组：上游规则表（原版顺序：代理判定在前、直连判定在后，Global/China 兜底表居尾）=====",
    ]
    # [Rule] 段头之后：手动规则 1A/1B 两组
    rule_hdr = find_idx(lambda t: t == "[Rule]", "[Rule] 段头")
    out[rule_hdr + 1:rule_hdr + 1] = (
        ["# ===== 第 1A 组：手动直连（custom-rules.txt 的 DIRECT 规则，权力最高）====="]
        + custom_direct
        + ["# ===== 第 1B 组：手动代理（custom-rules.txt 的 PROXY 规则，权力最高）====="]
        + custom_proxy
        + ["# ===== 第 3 组：上游本地规则（原文件散在规则表之间，全部 PROXY，集中于此不改变判定）====="]
        + local_rules
    )

    text = "\n".join(out) + "\n"

    # ---- 生成后自检（不过关就报错，不写出文件）----
    active = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    if sum(1 for l in active if l.startswith("dns-server =")) != 1:
        fail("dns-server 不是恰好一条")
    if any(l.startswith("fallback-dns-server =") for l in active):
        fail("fallback-dns-server 仍然存在")
    if dns_mode == "legacy":
        if f"dns-server = {DUAL_DOH}" not in active or f"direct-dns-server = {DUAL_DOH}" not in active \
           or f"proxy-dns-server = {DUAL_DOH}" not in active:
            fail("双 DoH 三行不完整（legacy）")
    else:
        if f"dns-server = {FOREIGN_DOH}" not in active or f"direct-dns-server = {DUAL_DOH}" not in active \
           or f"proxy-dns-server = {DUAL_DOH}" not in active:
            fail("DNS 三行不完整（new）")
    if "dns-fallback-system = false" not in active or "ipv6 = false" not in active:
        fail("fail-closed / ipv6 行缺失")
    # [General] 设计基线：这三行由上游原样透传、但本仓库的设计依赖它们，
    # 上游删改即失败（不能悄悄变行为）。
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
    finals = [l for l in active if l.startswith("FINAL,")]
    if finals != ["FINAL,PROXY,force-remote-dns"]:
        fail(f"FINAL 异常: {finals}")
    # DNS 配对联动锁：GEOIP 无 no-resolve 当且仅当 dns-server 带 #proxy
    geoip_lines = [l for l in active if l.startswith("GEOIP,")]
    dns_has_proxy = "#proxy" in [l for l in active if l.startswith("dns-server =")][0]
    geoip_bare = geoip_lines == ["GEOIP,CN,DIRECT"]
    geoip_guarded = geoip_lines == ["GEOIP,CN,DIRECT,no-resolve"]
    if not ((geoip_bare and dns_has_proxy) or (geoip_guarded and not dns_has_proxy)):
        fail(f"DNS 配对联动锁触发：GEOIP 行 {geoip_lines} 与 dns-server #proxy 配对不一致")
    # [Rule] 生效规则顺序断言
    rule_active = []
    sec = None
    for l in text.splitlines():
        t = l.strip()
        if t.startswith("[") and t.endswith("]"):
            sec = t
            continue
        if sec == "[Rule]" and t and not t.startswith("#"):
            rule_active.append(t)
    if not rule_active or rule_active[-1] != "FINAL,PROXY,force-remote-dns":
        fail("FINAL 不是 [Rule] 最后一条规则")
    n1 = len(custom_direct) + len(custom_proxy)
    if rule_active[:len(custom_direct)] != custom_direct or \
       rule_active[len(custom_direct):n1] != custom_proxy:
        fail("手动规则 1A/1B 两组不在 [Rule] 最前面或顺序被打乱")
    rs_idx = [i for i, l in enumerate(rule_active) if l.startswith(("RULE-SET,", "DOMAIN-SET,"))]
    if not rs_idx:
        fail("[Rule] 里没有规则表")
    first_rs_i = rs_idx[0]
    # 第 3 组：上游本地规则紧跟手动块，且之后直接就是第一张规则表
    if rule_active[n1:n1 + len(local_rules)] != local_rules:
        fail("第 3 组上游本地规则位置异常")
    if first_rs_i != n1 + len(local_rules):
        fail("第 3 组与第一张规则表之间出现多余规则")
    # 第 6 组：A 行紧贴 GEOIP 之前
    geoip_i = next(i for i, l in enumerate(rule_active) if l.startswith("GEOIP,"))
    if rule_active[geoip_i - 1] != a_line:
        fail("A 层补集行不在 GEOIP 之前")
    rs_urls = [rule_active[i].split(",", 2)[1] for i in rs_idx]
    if sorted(rs_urls) != sorted(list(mirrors.values()) + [SUPPLEMENT_A_URL]):
        fail("成品引用的规则表与清单不一致（34 张镜像 + 1 张补集，存在未镜像的表，或缺失/重复）")
    if len(rs_urls) != len(set(rs_urls)):
        fail("规则表引用有重复")
    if len(text.splitlines()) < 300:
        fail("输出行数异常（<300），疑似上游文件不完整")

    dns_note = (
        "#   国内: dns-server / direct-dns-server / proxy-dns-server = 阿里 DoH + 腾讯 DoH（加密）\n"
        "#   国外: 无本地解析 —— GEOIP 加 no-resolve、FINAL,PROXY,force-remote-dns 交节点远端解析；\n"
        "#         删除 fallback-dns-server 并设 dns-fallback-system = false（fail-closed）；\n"
        "#         allow-dns-all = false（A/AAAA 之外的查询返回空值、不转发国内 DNS）\n"
    ) if dns_mode == "legacy" else (
        "#   国外预解析: dns-server = Cloudflare DoH + Google DoH，经代理查询（#proxy），\n"
        "#         未知域名先经隧道查 IP 再按归属分流；GEOIP 去掉 no-resolve，与 #proxy 配对（联动锁断言）。\n"
        "#   国内: direct-dns-server = 阿里 DoH + 腾讯 DoH（加密）；proxy-dns-server 同为国内双 DoH（只解析节点域名）。\n"
        "#   删除 fallback-dns-server 并设 dns-fallback-system = false（fail-closed）；\n"
        "#         allow-dns-all = false（A/AAAA 之外的查询返回空值、不转发国内 DNS）\n"
    )
    header = (
        "# ============================================================\n"
        "# lazy.conf（无策略组版）+ DNS 国内外分流补丁 + 直连补集（自动生成，请勿手动编辑此文件）\n"
        "# 上游: https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/lazy.conf\n"
        "#       （懒人配置，同步自 LOWERTOP/Shadowrocket，版权与署名归上游所有）\n"
        "# 补丁: 仅 DNS 机制与规则排序，均以注释标出；名单内容与上游逐字一致。\n"
        "# 排序（先命中先赢，共八组，组内上游相对顺序不动）:\n"
        "#   1A 手动直连 / 1B 手动代理（custom-rules.txt，权力最高）\n"
        "#   3 上游本地规则；4 代理规则表；5 直连规则表；\n"
        f"#   6 A 层补集（{n_a} 条常用直连）；7 GEOIP,CN；8 FINAL,PROXY,force-remote-dns。\n"
        f"# 补集由 compile_supplement.py 每日编译（v2fly data/cn + ACL4SSR 精选表，双边校验：有代理证据的一律不补）。\n"
        "# 手动: 仓库根目录 custom-rules.txt 的自定义直连/代理规则，分 1A/1B 两组内嵌于 [Rule] 最前面；\n"
        "#       该文件为空时配置里只有标记注释，行为与无手动规则时完全一致。\n"
        + dns_note +
        "#   附带: ipv6 = false；tun-excluded-routes / skip-proxy 补上 100.64.0.0/10；\n"
        "#         全部 34 张规则表改引本仓库自托管镜像 rules/*.list\n"
        "#         （镜像与上游逐行一致、仅补 no-resolve；QX 格式四张表的 IP 规则原本无此标记、会触发本地解析）。\n"
        "#   注: [Host] 中 *.apple.com / *.icloud.com 的 server:system 是上游有意的兼容设计（保 Apple 服务解析质量），原样保留。\n"
        "# 本文件由 GitHub Actions 每日自动同步上游并重新打补丁生成。\n"
        "# ============================================================\n"
    )
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + text)
    print(f"OK: patched (dns-mode={dns_mode}) -> {out_path}")

if __name__ == "__main__":
    args = sys.argv[1:]
    dns_mode = "legacy"
    if args and args[-1].startswith("--dns-mode="):
        dns_mode = args.pop()[len("--dns-mode="):]
        if dns_mode not in ("legacy", "new"):
            fail("用法: python3 patch.py <input lazy.conf> <output> [--dns-mode=legacy|new]")
    if len(args) != 2:
        fail("用法: python3 patch.py <input lazy.conf> <output> [--dns-mode=legacy|new]")
    main(args[0], args[1], dns_mode)
