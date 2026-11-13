#!/usr/bin/env python3
"""patch_ad.py — lazy.conf + DNS 补丁 + 直连补集 + sr_ad_only 去广告规则内嵌。

用法: python3 patch_ad.py <上游 lazy.conf> <上游 sr_ad_only.conf> <输出文件>
      [--dns-mode=legacy|new]（含义与 patch.py 相同，默认 legacy）

流水线（顺序固定）:
  1) DNS 补丁（与基础版 patch.py 完全相同，见 patch.py 文档；实现共用
     build_common.py，两版本不可能悄悄漂移）。
  2) 九组排序（与基础版相同，另加第 2 组去广告，位于手动规则之后、
     上游规则之前）:
       1A 手动直连 / 1B 手动代理（custom-rules.txt，权力最高）
       2  去广告规则（sr_ad_only 全量内嵌 + DuskWander87 去广告补充，REJECT）
       3  上游本地规则；4 代理规则表；5 直连规则表；
       6  A 层补集；7 P 层补集；8 GEOIP,CN；9 FINAL,PROXY,force-remote-dns
     注意: 不能用 RULE-SET 引用 sr_ad_only —— 它每行带策略且自带 [Rule]
     段头，引用必错，只能内嵌。
  3) 重叠守卫：去广告规则中"整个匹配范围都被手动规则盖住"的（即永远
     轮不到它、纯属死规则），剔除并记入构建日志；手动规则高于广告拦截，
     明确意图不被批量名单否决。部分重叠的保留（顺序会正确裁决）。
  4) 自检: 每一步都有断言与计数核对；任一不符即 exit 1，不写出文件。
     写出经原子替换（临时文件落盘后替换），写一半的进程中断不会留下
     截断文件。
"""
import ipaddress
import os
import re
import sys

import build_common
from build_common import (
    SUPPLEMENT_A_FILE,
    SUPPLEMENT_A_URL,
    SUPPLEMENT_AD_FILE,
    SUPPLEMENT_P_FILE,
    SUPPLEMENT_P_URL,
    apply_patches,
    assert_structure,
    atomic_write_text,
    check_geoip_interlock,
    check_ruleset_inventory,
    check_supplement_positions,
    count_active_rules,
    dns_self_check,
    fail,
    find_idx,
    insert_tail_blocks,
    load_custom_rules,
    load_mirrors,
)

build_common.set_fail_prefix("PATCH FAILED")

AD_LINE = re.compile(r"^(DOMAIN-SUFFIX|IP-CIDR),([^,]+),Reject$")


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


def load_supplement_ad():
    """读取 compile_supplement.py 编译的去广告补充表（SupplementAd.list），
    渲染为 REJECT 内嵌规则。文件缺失即失败（编译器须先运行）；行格式只
    允许 DOMAIN-SUFFIX / DOMAIN（编译器出厂守卫已保证，此处再验一遍）。
    表为空是合法的（补充全部被已有判定覆盖时），返回空列表。"""
    if not os.path.exists(SUPPLEMENT_AD_FILE):
        fail(f"补集表缺失（compile_supplement.py 须先运行）: {SUPPLEMENT_AD_FILE}")
    rules = []
    for raw in open(SUPPLEMENT_AD_FILE, encoding="utf-8"):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = [x.strip() for x in s.split(",")]
        if len(parts) != 2 or parts[0].upper() not in ("DOMAIN-SUFFIX", "DOMAIN"):
            fail(f"SupplementAd.list 行格式不对: {s!r}")
        rules.append(f"{parts[0].upper()},{parts[1]},REJECT")
    return rules


def main(lazy_path, ad_path, out_path, dns_mode):
    ad_rules = load_ad_rules(ad_path)
    custom_direct, custom_proxy = load_custom_rules()
    ad_rules, dropped = drop_shadowed_ad_rules(ad_rules, custom_direct, custom_proxy)
    n_sr = len(ad_rules)
    supp_ad = load_supplement_ad()
    supp_ad, supp_dropped = drop_shadowed_ad_rules(supp_ad, custom_direct, custom_proxy)
    seen_ad = set(ad_rules)
    supp_new = [r for r in supp_ad if r not in seen_ad]
    n_supp_dup = len(supp_ad) - len(supp_new)
    ad_rules = ad_rules + supp_new
    if dropped:
        print(f"重叠守卫：剔除被手动规则完全盖住的去广告规则 {len(dropped)} 条：")
        for r in dropped[:20]:
            print(f"  dropped: {r}")
        if len(dropped) > 20:
            print(f"  ... 及另外 {len(dropped) - 20} 条")
    if supp_dropped or n_supp_dup:
        print(f"去广告补充守卫：剔除被手动规则盖住 {len(supp_dropped)} 条、"
              f"与 sr_ad_only 重复 {n_supp_dup} 条")
    mirrors = load_mirrors()
    for p in (SUPPLEMENT_A_FILE, SUPPLEMENT_P_FILE, SUPPLEMENT_AD_FILE):
        if not os.path.exists(p):
            fail(f"补集表缺失（compile_supplement.py 须先运行）: {p}")
    n_a = count_active_rules(SUPPLEMENT_A_FILE)
    n_p = count_active_rules(SUPPLEMENT_P_FILE)
    lines = open(lazy_path, encoding="utf-8").read().splitlines()
    out, local_rules, mirror_hits = apply_patches(lines, mirrors, dns_mode)

    # ---- 九组组装（自下而上拼接，索引不漂移；与 patch.py 同构）----
    a_line = f"RULE-SET,{SUPPLEMENT_A_URL},DIRECT"
    p_line = f"RULE-SET,{SUPPLEMENT_P_URL},PROXY"
    insert_tail_blocks(out, n_a, n_p, a_line, p_line)
    rule_hdr = find_idx(out, lambda t: t == "[Rule]", "[Rule] 段头")
    out[rule_hdr + 1:rule_hdr + 1] = (
        ["# ===== 第 1A 组：手动直连（custom-rules.txt 的 DIRECT 规则，权力最高）====="]
        + custom_direct
        + ["# ===== 第 1B 组：手动代理（custom-rules.txt 的 PROXY 规则，权力最高）====="]
        + custom_proxy
        + [f"# ===== 第 2 组：去广告规则（内嵌自 sr_ad_only.conf {n_sr} 条 + 去广告补充 {len(supp_new)} 条，共 {len(ad_rules)} 条，每日自动同步；重叠守卫已剔除 {len(dropped) + len(supp_dropped)} 条）====="]
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
    dns_self_check(active, dns_mode)
    assert_structure(text)
    # DNS 配对联动锁：GEOIP 无 no-resolve 当且仅当 dns-server 带 #proxy
    check_geoip_interlock(active)
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
    check_supplement_positions(rule_active, a_line, p_line)
    check_ruleset_inventory(rule_active, mirrors, [SUPPLEMENT_A_URL, SUPPLEMENT_P_URL])

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
        f"# 内容: lazy 分流规则 + sr_ad_only 去广告规则 {n_sr} 条 + 去广告补充 {len(supp_new)} 条，共 {len(ad_rules)} 条内嵌于第 2 组（REJECT；重叠守卫剔除 {len(dropped) + len(supp_dropped)} 条）；\n"
        "#       DNS 仅改机制、规则仅改排序（均以注释标出），名单内容与上游逐字一致。\n"
        "# 排序（先命中先赢，共九组，组内上游相对顺序不动）:\n"
        "#   1A 手动直连 / 1B 手动代理（custom-rules.txt，权力最高）；2 去广告；3 上游本地规则；\n"
        "#   4 代理规则表；5 直连规则表；\n"
        f"#   6 A 层补集（{n_a} 条常用直连）；7 P 层补集（{n_p} 条代理）；8 GEOIP,CN；9 FINAL,PROXY,force-remote-dns。\n"
        f"# 补集由 compile_supplement.py 每日编译（A 层：v2fly data/cn + ACL4SSR 精选表 + DuskWander87 ChinaDirect，有代理证据的一律不补；P 层：ACL4SSR 代理表 + GFWList，已被已有判定覆盖的一律不补；去广告补充：DuskWander87 Reject.list）。\n"
        + dns_note +
        "#         全部 34 张规则表改引自托管镜像 rules/*.list（与上游逐行一致、仅补 no-resolve；QX 格式四张表的 IP 规则原本无此标记、会触发本地解析）。\n"
        "#         注: [Host] 中 Apple/iCloud 的 server:system 为上游有意的兼容设计，原样保留。\n"
        "# 本文件由 GitHub Actions 每日自动同步两个上游并重新生成。\n"
        "# ============================================================\n"
    )
    atomic_write_text(out_path, header + text)
    print(f"OK: ad_rules={len(ad_rules)} (sr_ad_only {n_sr} + supplement {len(supp_new)}) dropped={len(dropped) + len(supp_dropped)} (dns-mode={dns_mode}) -> {out_path}")


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
