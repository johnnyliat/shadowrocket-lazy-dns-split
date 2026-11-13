#!/usr/bin/env python3
"""build_common.py — 构建脚本共享逻辑。

抽取原因：patch.py 与 patch_ad.py 曾有约 300 行逐字重复（DNS 补丁、手动
规则校验、结构安检、自检断言），改一处漏一处是迟早的事；共享后 DNS 策略、
手动规则校验与结构安检只有一份真相。本模块同时收容严格规则语法校验
（no-resolve 精确判定、域名合法性），供 patch_lists.py / check_no_resolve.py /
compile_supplement.py 共用——校验逻辑本身不能再有两份实现互相漂移。

verify_routing.py 故意不 import 本模块：独立验证必须用独立实现，否则生成器
与验证器会共享同一个错误假设。

失败约定：各脚本在 import 后调用 set_fail_prefix() 设定自己的失败前缀
（如 "PATCH FAILED"），共享函数统一经 fail() 退出；失败信息只出现在构建
日志里，不影响任何产物内容。
"""
import ipaddress
import os
import re
import sys
import tempfile

# ---- 失败前缀（各脚本设定自己的，保持原有日志措辞）----
_FAIL_PREFIX = ["BUILD FAILED"]


def set_fail_prefix(prefix):
    _FAIL_PREFIX[0] = prefix


def fail(msg):
    print(f"{_FAIL_PREFIX[0]}: {msg}", file=sys.stderr)
    sys.exit(1)


# ---- 共享常量 ----
DUAL_DOH = "https://dns.alidns.com/dns-query,https://doh.pub/dns-query"
FOREIGN_DOH = "https://cloudflare-dns.com/dns-query#proxy,https://dns.google/dns-query#proxy"

SELF_BASE = "https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/"
BASE = os.path.dirname(os.path.abspath(__file__))
MIRRORS_FILE = os.path.join(BASE, "mirrors.txt")
SUPPLEMENT_A_URL = SELF_BASE + "rules/SupplementA.list"
SUPPLEMENT_A_FILE = os.path.join(BASE, "rules", "SupplementA.list")
SUPPLEMENT_P_URL = SELF_BASE + "rules/SupplementP.list"
SUPPLEMENT_P_FILE = os.path.join(BASE, "rules", "SupplementP.list")
SUPPLEMENT_AD_FILE = os.path.join(BASE, "rules", "SupplementAd.list")


def atomic_write_text(path, text):
    """原子写出：先写同目录临时文件、落盘后再替换，写一半的进程中断不会
    留下截断的目标文件。权限显式设为 0644，与普通 open("w") 行为一致。"""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---- 严格规则语法 ----
IP_TYPES = {"IP-CIDR", "IP-CIDR6", "IP6-CIDR", "IP-ASN", "GEOIP"}
LOGICAL_TYPES = {"AND", "OR", "NOT"}
_IP_TOKEN_RE = re.compile(r"(IP-CIDR6?|IP6-CIDR|IP-ASN|GEOIP)\s*,", re.I)
_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def validate_domain_value(value):
    """严格域名校验：全小写（调用方先 lowercase）、总长 ≤253、每标签 1–63
    字符、标签首尾为字母数字、中间可含连字符；无空标签、无下划线、无前导/
    尾随点号。单标签（如品牌顶级域）在此处合法，顶级域策略由编译器另行
    把关（allowlist），不在本函数内。"""
    if not value or len(value) > 253 or value != value.lower():
        return False
    for label in value.split("."):
        if not 1 <= len(label) <= 63 or not _LABEL_RE.match(label):
            return False
    return True


def _validate_ip_value(typ, value):
    if typ == "GEOIP":
        if not re.fullmatch(r"[A-Z]{2}", value.upper()) or value.upper() != value:
            raise ValueError(f"GEOIP 国家代码不合法: {value!r}")
        return
    if typ == "IP-ASN":
        m = re.fullmatch(r"(?i)(AS)?(\d+)", value)
        if not m or not 1 <= int(m.group(2)) <= 4294967295:
            raise ValueError(f"IP-ASN 值不合法: {value!r}")
        return
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        raise ValueError(f"IP 段不合法: {value!r}")


def _logical_ip_guarded(body):
    """逻辑组合行（AND/OR/NOT）：每个 IP 条件必须在自己同一括号组内带上
    精确的 no-resolve 修饰符，且修饰符须为该组内最后一个参数。无 IP 条件
    时返回 None（不是 IP 规则）。"""
    if body.count("(") != body.count(")"):
        raise ValueError("括号不平衡")
    guarded_any = False
    for m in _IP_TOKEN_RE.finditer(body):
        guarded_any = True
        span = body[m.end():]
        close = span.find(")")
        span = span if close < 0 else span[:close]
        fields = [f.strip().lower() for f in span.split(",") if f.strip() != ""]
        if not fields or fields[-1] != "no-resolve":
            return False
    return True if guarded_any else None


def ip_rule_guard_status(line):
    """判定一行规则的 no-resolve 守卫状态。

    返回 True：IP 规则且带精确的独立 no-resolve 修饰符；
    返回 False：IP 规则但未带（或只带了 #no-resolve、no-resolve-extra
    这类非独立写法——2026-11-13 外部审计指出的子串判定绕过，本函数按
    逗号参数精确匹配，不再用子串包含判断）；
    返回 None：不是 IP 类规则（域名规则、RULE-SET、注释等）。
    语法畸形的 IP 规则行抛 ValueError（fail-closed，不猜测）。

    适用行形（实测现状）：TYPE,VALUE[,组名/策略][,no-resolve]，最多 4 个
    字段；组名/策略字段不限制取值（QX 表用 Apple/China/Global/WeChat
    等组名，限制取值会误杀上游合法内容）。no-resolve 须为最后一个参数，
    位置放错（如写在策略之前）同样判为未守卫。行内 # 之后按注释截断后再判
    （# 不可能合法出现在 IP 规则的字段里）。
    """
    s = line.strip().lstrip("\ufeff")
    if not s or s.startswith("#") or s.startswith("//"):
        return None
    head = s.split(",", 1)[0].strip().upper()
    body = s.split("#", 1)[0].strip().rstrip(",")
    if head in LOGICAL_TYPES:
        return _logical_ip_guarded(body)
    if head not in IP_TYPES:
        return None
    fields = [f.strip() for f in body.split(",")]
    if len(fields) > 4:
        raise ValueError(f"IP 规则字段过多（{len(fields)} 个）: {line.strip()!r}")
    if any(f == "" for f in fields):
        raise ValueError(f"IP 规则含空字段: {line.strip()!r}")
    if any(" " in f or "\t" in f for f in fields):
        raise ValueError(f"IP 规则字段含空格: {line.strip()!r}")
    if len(fields) < 2:
        raise ValueError(f"IP 规则缺少取值: {line.strip()!r}")
    _validate_ip_value(head, fields[1])
    rest = [f.lower() for f in fields[2:]]
    if rest.count("no-resolve") > 1:
        raise ValueError(f"no-resolve 重复出现: {line.strip()!r}")
    return bool(rest) and rest[-1] == "no-resolve"


# ---- 镜像清单与手动规则 ----

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
    同一目标只能有一条规则（跨策略重复与互相覆盖都算冲突），故直连/代理两组拆开
    摆放与合在一起行为完全相同。内嵌位置：[Rule] 最前面（1A/1B 两组），
    优先于一切含广告拦截——明确意图大于批量名单。域名值须通过严格校验
    （关键字规则是子串匹配，值不是域名，不在此列）。"""
    if not os.path.exists(CUSTOM_FILE):
        return [], []
    direct, proxy, seen, parsed = [], [], {}, []
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
        elif typ in ("DOMAIN", "DOMAIN-SUFFIX") and not validate_domain_value(value.lower()):
            fail(f"custom-rules.txt 第 {lineno} 行域名不合法: {s!r}")
        key = (typ, value.lower())
        if key in seen:
            fail(f"custom-rules.txt 第 {lineno} 行与第 {seen[key]} 行重复或冲突（同一目标只能有一条规则）: {s!r}")
        seen[key] = lineno
        if typ in CUSTOM_DOMAIN_TYPES:
            parsed.append((lineno, typ, value.lower(), policy))
        line = f"{typ},{value},{policy}" + (",no-resolve" if typ in CUSTOM_IP_TYPES else "")
        (direct if policy == "DIRECT" else proxy).append(line)
    # 跨策略后缀重叠断言：直连与代理两组的域名目标不得互相覆盖（父子后缀
    # 跨策略时，同一服务的判定取决于两组顺序，违背"明确意图唯一"；关键字
    # 规则静态无法判定重叠，不在此列，精确重复已由 seen 检查拦下）。
    def _covers(t1, v1, v2):
        if t1 == "DOMAIN-SUFFIX":
            return v2 == v1 or v2.endswith("." + v1)
        if t1 == "DOMAIN":
            return v2 == v1
        return False
    dom = [(ln, t, v, p) for (ln, t, v, p) in parsed if t in ("DOMAIN", "DOMAIN-SUFFIX")]
    for ln1, t1, v1, p1 in dom:
        for ln2, t2, v2, p2 in dom:
            if p1 != p2 and _covers(t1, v1, v2):
                fail(f"custom-rules.txt 第 {ln1} 行与第 {ln2} 行跨策略重叠"
                     f"（{t1},{v1},{p1} 盖住 {t2},{v2},{p2}）")
    return direct, proxy


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


# ---- DNS 补丁与组装 ----

def apply_patches(lines, mirrors, dns_mode):
    """对上游 lazy.conf 行做 DNS 补丁与镜像改写，收集上游本地规则。

    返回 (out, local_rules, mirror_hits)。锚点缺失或镜像改写不完整即 fail，
    绝不生成半成品。补丁文本与 patch.py 历史版本逐字一致，两版本共用。"""
    local_rules = []
    mirror_hits = {}
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
    return out, local_rules, mirror_hits


def find_idx(lines, pred, what):
    for i, l in enumerate(lines):
        if pred(l.strip()):
            return i
    fail(f"组装锚点缺失: {what}")


def insert_tail_blocks(out, n_a, n_p, a_line, p_line):
    """插入第 6/7 组（补集）与第 4/5 组横幅。先插 GEOIP 之前的块、再找第
    一张规则表位置插入横幅：前者位于后者之后，索引不漂移。"""
    geoip_idx = find_idx(out, lambda t: t in ("GEOIP,CN,DIRECT", "GEOIP,CN,DIRECT,no-resolve"), "GEOIP 行")
    out[geoip_idx:geoip_idx] = [
        f"# ===== 第 6 组：A 层补集（SupplementA.list，{n_a} 条常用直连，双边校验后仅含未被已有判定覆盖者）=====",
        a_line,
        f"# ===== 第 7 组：P 层补集（SupplementP.list，{n_p} 条代理补集，双边校验后仅含未被已有判定覆盖者）=====",
        p_line,
        "# ===== 第 8/9 组：IP 推断与兜底 =====",
    ]
    first_rs = find_idx(out, lambda t: t.startswith("RULE-SET,"), "第一张规则表")
    out[first_rs:first_rs] = [
        "# ===== 第 4/5 组：上游规则表（原版顺序：代理判定在前、直连判定在后，Global/China 兜底表居尾）=====",
    ]


def dns_self_check(active, dns_mode):
    """生成后 DNS 自检：三行 DNS、回落、基线、allow-dns-all、局域网补丁。"""
    if sum(1 for l in active if l.startswith("dns-server =")) != 1:
        fail("dns-server 不是恰好一条")
    if sum(1 for l in active if l.startswith("fallback-dns-server =")) != 1 or f"fallback-dns-server = {DUAL_DOH}" not in active:
        fail("fallback-dns-server 不是恰好一条国内双 DoH")
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


def check_geoip_interlock(active):
    """DNS 配对联动锁：GEOIP 无 no-resolve 当且仅当 dns-server 带 #proxy。"""
    geoip_lines = [l for l in active if l.startswith("GEOIP,")]
    dns_has_proxy = "#proxy" in [l for l in active if l.startswith("dns-server =")][0]
    geoip_bare = geoip_lines == ["GEOIP,CN,DIRECT"]
    geoip_guarded = geoip_lines == ["GEOIP,CN,DIRECT,no-resolve"]
    if not ((geoip_bare and dns_has_proxy) or (geoip_guarded and not dns_has_proxy)):
        fail(f"DNS 配对联动锁触发：GEOIP 行 {geoip_lines} 与 dns-server #proxy 配对不一致")


def check_supplement_positions(rule_active, a_line, p_line):
    """第 6/7 组：A 行与 P 行紧贴 GEOIP 之前，A 在 P 之前。"""
    geoip_i = next(i for i, l in enumerate(rule_active) if l.startswith("GEOIP,"))
    if rule_active[geoip_i - 1] != p_line:
        fail("P 层补集行不在 GEOIP 之前")
    if rule_active[geoip_i - 2] != a_line:
        fail("A 层补集行不在 P 层之前")


def check_ruleset_inventory(rule_active, mirrors, extra_urls):
    """成品引用的规则表与清单一致（34 张镜像 + 补集），无缺失、无重复。"""
    rs_idx = [i for i, l in enumerate(rule_active) if l.startswith(("RULE-SET,", "DOMAIN-SET,"))]
    if not rs_idx:
        fail("[Rule] 里没有规则表")
    rs_urls = [rule_active[i].split(",", 2)[1] for i in rs_idx]
    if sorted(rs_urls) != sorted(list(mirrors.values()) + list(extra_urls)):
        fail("成品引用的规则表与清单不一致（34 张镜像 + 2 张补集，存在未镜像的表，或缺失/重复）")
    if len(rs_urls) != len(set(rs_urls)):
        fail("规则表引用有重复")
    return rs_idx
