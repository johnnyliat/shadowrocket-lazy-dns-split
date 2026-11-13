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
    - fallback-dns-server 写明国内双 DoH，并加 dns-fallback-system = false（主查询失败时回落到加密的国内 DNS，不回落系统 DNS；实测留空会回落系统 DNS）
    - allow-dns-all = false（A/AAAA 之外的查询返回空值、不转发国内 DNS）
    - ipv6 = false（v6 通道会绕过 DNS 分流）
    - FINAL,PROXY -> FINAL,PROXY,force-remote-dns（境外域名交节点远端解析）
  局域网：tun-excluded-routes 与 skip-proxy 补上 100.64.0.0/10（运营商级
    NAT 网段），与既有私有网段同等 bypass。
  [Rule] 九组排序（先命中先赢，顺序即权力等级；组内上游相对顺序不动）:
    1A 手动直连（custom-rules.txt 的 DIRECT 规则）
    1B 手动代理（custom-rules.txt 的 PROXY 规则）
    2  去广告（仅去广告版，本文件无此组）
    3  上游本地规则（原文件 [Rule] 里的字面规则，如 3 条 PROXY）
    4  代理规则表（上游 RULE-SET，原版顺序）
    5  直连规则表（上游 RULE-SET，原版顺序，Global/China 兜底表居尾）
    6  A 层补集 RULE-SET（SupplementA.list，常用直连补集）
    7  P 层补集 RULE-SET（SupplementP.list，代理补集；排在直连判定之后、
       GEOIP 之前，外国服务即使解析出国内 IP 也走代理）
    8  GEOIP,CN,DIRECT（IP 推断；legacy 带 no-resolve，new 不带）
    9  FINAL,PROXY,force-remote-dns（兜底）
    补集表由 compile_supplement.py 每日编译生成（A 层：v2fly + ACL4SSR +
    DuskWander87，双边校验：有代理证据的一律不补；P 层：
    ACL4SSR 代理表 + GFWList，双边校验：已被已有判定覆盖的一律不补），
    本脚本只引用、不生产；表缺失即构建失败。
  结构安检: 生成后核对成品骨架（段落集合、[Proxy]/[Proxy Group] 无生效行、
    无 include、规则集地址白名单、[Host]/[MITM]/[URL Rewrite] 内容与基线一致、
    九组位置断言、DNS 配对联动锁），上游骨架有任何预期外变化即失败。

共享逻辑（DNS 补丁、手动规则校验、结构安检、自检断言）见 build_common.py，
本文件只留基础版特有的组装、自检编排与文件头；patch_ad.py 与本文件共用
同一份共享逻辑，两版本不可能悄悄漂移。

原则: 每处修改都有断言。锚点找不到就报错退出（exit 1），绝不生成半成品。
写出经原子替换（临时文件落盘后替换），写一半的进程中断不会留下截断文件。
"""
import os
import sys

import build_common
from build_common import (
    SUPPLEMENT_A_FILE,
    SUPPLEMENT_A_URL,
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


def main(src_path, out_path, dns_mode):
    lines = open(src_path, encoding="utf-8").read().splitlines()
    custom_direct, custom_proxy = load_custom_rules()
    mirrors = load_mirrors()
    for p in (SUPPLEMENT_A_FILE, SUPPLEMENT_P_FILE):
        if not os.path.exists(p):
            fail(f"补集表缺失（compile_supplement.py 须先运行）: {p}")
    n_a = count_active_rules(SUPPLEMENT_A_FILE)
    n_p = count_active_rules(SUPPLEMENT_P_FILE)
    out, local_rules, mirror_hits = apply_patches(lines, mirrors, dns_mode)

    # ---- 九组组装（自下而上拼接，索引不漂移）----
    a_line = f"RULE-SET,{SUPPLEMENT_A_URL},DIRECT"
    p_line = f"RULE-SET,{SUPPLEMENT_P_URL},PROXY"
    insert_tail_blocks(out, n_a, n_p, a_line, p_line)
    # [Rule] 段头之后：手动规则 1A/1B 两组
    rule_hdr = find_idx(out, lambda t: t == "[Rule]", "[Rule] 段头")
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
    dns_self_check(active, dns_mode)
    assert_structure(text)
    finals = [l for l in active if l.startswith("FINAL,")]
    if finals != ["FINAL,PROXY,force-remote-dns"]:
        fail(f"FINAL 异常: {finals}")
    check_geoip_interlock(active)
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
    check_supplement_positions(rule_active, a_line, p_line)
    check_ruleset_inventory(rule_active, mirrors, [SUPPLEMENT_A_URL, SUPPLEMENT_P_URL])
    if len(text.splitlines()) < 300:
        fail("输出行数异常（<300），疑似上游文件不完整")

    dns_note = (
        "#   国内: dns-server / direct-dns-server / proxy-dns-server = 阿里 DoH + 腾讯 DoH（加密）\n"
        "#   国外: 无本地解析 —— GEOIP 加 no-resolve、FINAL,PROXY,force-remote-dns 交节点远端解析；\n"
        "#         fallback-dns-server 写明国内双 DoH，dns-fallback-system = false（主查询失败时不回落系统 DNS）；\n"
        "#         allow-dns-all = false（A/AAAA 之外的查询返回空值、不转发国内 DNS）\n"
    ) if dns_mode == "legacy" else (
        "#   国外预解析: dns-server = Cloudflare DoH + Google DoH，经代理查询（#proxy），\n"
        "#         未知域名先经隧道查 IP 再按归属分流；GEOIP 去掉 no-resolve，与 #proxy 配对（联动锁断言）。\n"
        "#   国内: direct-dns-server = 阿里 DoH + 腾讯 DoH（加密）；proxy-dns-server 同为国内双 DoH（只解析节点域名）。\n"
        "#   fallback-dns-server 写明国内双 DoH，dns-fallback-system = false（主查询失败时不回落系统 DNS）；\n"
        "#         allow-dns-all = false（A/AAAA 之外的查询返回空值、不转发国内 DNS）\n"
    )
    header = (
        "# ============================================================\n"
        "# lazy.conf（无策略组版）+ DNS 国内外分流补丁 + 直连补集（自动生成，请勿手动编辑此文件）\n"
        "# 上游: https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/lazy.conf\n"
        "#       （懒人配置，同步自 LOWERTOP/Shadowrocket，版权与署名归上游所有）\n"
        "# 补丁: 仅 DNS 机制与规则排序，均以注释标出；名单内容与上游逐字一致。\n"
        "# 排序（先命中先赢，共九组，组内上游相对顺序不动）:\n"
        "#   1A 手动直连 / 1B 手动代理（custom-rules.txt，权力最高）\n"
        "#   3 上游本地规则；4 代理规则表；5 直连规则表；\n"
        f"#   6 A 层补集（{n_a} 条常用直连）；7 P 层补集（{n_p} 条代理）；8 GEOIP,CN；9 FINAL,PROXY,force-remote-dns。\n"
        f"# 补集由 compile_supplement.py 每日编译（A 层：v2fly data/cn + ACL4SSR 精选表 + DuskWander87 ChinaDirect，有代理证据的一律不补；P 层：ACL4SSR 代理表 + GFWList，已被已有判定覆盖的一律不补）。\n"
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
    atomic_write_text(out_path, header + text)
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
