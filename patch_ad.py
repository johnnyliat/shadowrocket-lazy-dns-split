#!/usr/bin/env python3
"""patch.py — lazy.conf + DNS 国内外分流补丁 + sr_ad_only 去广告规则内嵌。

用法: python3 patch.py <上游 lazy.conf> <上游 sr_ad_only.conf> <输出文件>

流水线（三步，顺序固定）:
  1) DNS 补丁（与 shadowrocket-lazy-dns 仓库完全相同）:
     国内双 DoH（阿里+腾讯）写入 dns-server / direct-dns-server / proxy-dns-server；
     删除 fallback-dns-server 并加 dns-fallback-system = false（fail-closed）；
     ipv6 = false；GEOIP,CN,DIRECT 加 no-resolve；FINAL,PROXY 加 force-remote-dns。
  2) 去广告内嵌: 取 sr_ad_only.conf 的全部规则（仅 DOMAIN-SUFFIX / IP-CIDR 两类），
     Reject -> REJECT，IP-CIDR 追加 no-resolve（避免为判 IP 规则触发本地解析），
     整段插入 [Rule] 最前面（优先级最高，先于一切分流规则命中）。
     注意: 不能用 RULE-SET 引用 sr_ad_only —— 它每行带策略且自带 [Rule] 段头，引用必错，只能内嵌。
  3) 自检: 每一步都有断言与计数核对；任一不符即 exit 1，不写出文件。
     这样任一上游改格式，Actions 会失败报警，而不是把半成品推给客户端。
"""
import re
import sys

DUAL_DOH = "https://dns.alidns.com/dns-query,https://doh.pub/dns-query"

QX_LISTS = {
    "Apple": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/QuantumultX/Apple/Apple.list",
    "China": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/QuantumultX/China/China.list",
    "Global": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/QuantumultX/Global/Global.list",
    "WeChat": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/QuantumultX/WeChat/WeChat.list",
}
SELF_HOSTED = "https://cdn.jsdelivr.net/gh/johnnyliat/shadowrocket-lazy-dns-split@main/rules/{name}.list"
AD_LINE = re.compile(r"^(DOMAIN-SUFFIX|IP-CIDR),([^,]+),Reject$")

def fail(msg):
    print(f"PATCH FAILED: {msg}", file=sys.stderr)
    sys.exit(1)

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
    lines = open(lazy_path, encoding="utf-8").read().splitlines()
    out, section = [], None
    done = set()
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

        if section == "[Rule]" and s in ("GEOIP,CN,DIRECT", "GEOIP,CN,DIRECT,no-resolve"):
            out.append("GEOIP,CN,DIRECT,no-resolve")
            done.add("geoip")
            continue

        if section == "[Rule]" and s in ("FINAL,PROXY", "FINAL,PROXY,force-remote-dns"):
            out.append("FINAL,PROXY,force-remote-dns")
            done.add("final")
            continue

        if section == "[Rule]" and s.startswith("RULE-SET,"):
            hit = None
            for name, qx_url in QX_LISTS.items():
                if qx_url in s:
                    hit = name
                    break
            if hit:
                out.append(f"# [DNS分流补丁] {hit} 表原为 QuantumultX 格式，其 IP 规则不带 no-resolve，未命中域名会被本地解析一次（泄露面）；改引本仓库自托管镜像 rules/{hit}.list（与上游逐行一致、仅补 no-resolve，每日随上游同步）。")
                out.append(s.replace(QX_LISTS[hit], SELF_HOSTED.format(name=hit)))
                done.add(f"ruleset_{hit}")
            else:
                out.append(raw)
            continue

        out.append(raw)

    missing = {"dns", "fallback", "ipv6", "geoip", "final", "ad",
               "ruleset_Apple", "ruleset_China", "ruleset_Global", "ruleset_WeChat"} - done
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
    for name, qx_url in QX_LISTS.items():
        if any(qx_url in l for l in active):
            fail(f"{name} 仍在引用 QX 原表")
        if sum(1 for l in active if SELF_HOSTED.format(name=name) in l) != 1:
            fail(f"{name} 自托管镜像引用不是恰好一条")
    rejects = [l for l in rule_active if l.endswith(",REJECT") or l.endswith(",REJECT,no-resolve")]
    if len(rejects) != len(ad_rules):
        fail(f"内嵌去广告计数不符: 文件内 {len(rejects)} vs 上游 {len(ad_rules)}")
    if rule_active[:len(ad_rules)] != ad_rules:
        fail("去广告规则不在 [Rule] 最前面或顺序被打乱")
    if not rule_active or rule_active[-1] != "FINAL,PROXY,force-remote-dns":
        fail("FINAL 不是 [Rule] 最后一条规则")

    header = (
        "# ============================================================\n"
        "# lazy.conf（无策略组版）+ DNS 国内外分流补丁 + 去广告规则（自动生成，请勿手动编辑此文件）\n"
        "# 上游: https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/lazy.conf\n"
        "#       https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/sr_ad_only.conf\n"
        "#       （懒人配置同步自 LOWERTOP/Shadowrocket，版权与署名归上游所有）\n"
        f"# 内容: lazy 分流规则 + sr_ad_only 去广告规则 {len(ad_rules)} 条全量内嵌于 [Rule] 最前面（REJECT）；\n"
        "#       DNS 仅改机制（以 [DNS分流补丁] 注释标出），其余与上游逐字一致。\n"
        "#   国内: dns-server / direct-dns-server / proxy-dns-server = 阿里 DoH + 腾讯 DoH（加密）\n"
        "#   国外: 无本地解析 —— GEOIP 加 no-resolve、FINAL,PROXY,force-remote-dns 交节点远端解析；\n"
        "#         删除 fallback-dns-server 并设 dns-fallback-system = false（fail-closed）；ipv6 = false；\n"
        "#         Apple/China/Global/WeChat 四张表改引自托管镜像 rules/*.list（原 QX 格式 IP 规则无 no-resolve 会触发本地解析；镜像逐行一致、仅补 no-resolve）。\n"
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
