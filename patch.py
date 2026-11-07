#!/usr/bin/env python3
"""patch.py — 给上游 lazy.conf 打上 DNS 国内外分流补丁（且只打这个补丁）。

用法: python3 patch.py <上游 lazy.conf 路径> <输出路径>

设计（借鉴 DuskWander87/shadowrocket-config 的规则层远端解析思路，但本地解析不用明文 UDP）:
  国内（本地解析，全程加密 DoH）:
    dns-server / direct-dns-server / proxy-dns-server
      = https://dns.alidns.com/dns-query,https://doh.pub/dns-query
  国外（不在本地解析）:
    - 删除 fallback-dns-server，并加 dns-fallback-system = false（fail-closed，不回退系统 DNS）
    - GEOIP,CN,DIRECT -> GEOIP,CN,DIRECT,no-resolve
    - FINAL,PROXY     -> FINAL,PROXY,force-remote-dns（境外域名交节点在 VPS 远端解析）
  附带: ipv6 = true -> false（v6 通道会绕过 DNS 分流）
  附带: Apple/China/Global/WeChat 四张 QuantumultX 格式规则表的引用改指本仓库
        自托管镜像（rules/<Name>.list，由 patch_lists.py 每日同步生成）：QX 格式的
        IP 规则不带 no-resolve，未命中域名会被本地解析一次（泄露面）；同作者 SR 格式
        同名表只是骨架版不能换。镜像与上游逐行一致、仅给 IP 规则补 no-resolve。

原则: 每处修改都有断言。锚点找不到就报错退出（exit 1），绝不生成半成品，
      这样上游改格式时 Actions 会失败报警，而不是把错配置推给客户端。
"""
import sys

DUAL_DOH = "https://dns.alidns.com/dns-query,https://doh.pub/dns-query"

QX_LISTS = {
    "Apple": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/QuantumultX/Apple/Apple.list",
    "China": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/QuantumultX/China/China.list",
    "Global": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/QuantumultX/Global/Global.list",
    "WeChat": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/QuantumultX/WeChat/WeChat.list",
}
SELF_HOSTED = "https://cdn.jsdelivr.net/gh/johnnyliat/shadowrocket-lazy-dns-split@main/rules/{name}.list"

def fail(msg):
    print(f"PATCH FAILED: {msg}", file=sys.stderr)
    sys.exit(1)

def main(src_path, out_path):
    lines = open(src_path, encoding="utf-8").read().splitlines()
    out, section = [], None
    done = set()
    for raw in lines:
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
            out.append(raw)
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
            # 删除该行：不设 fallback，配合下面的 dns-fallback-system=false 实现 fail-closed
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

    missing = {"dns", "fallback", "ipv6", "geoip", "final",
               "ruleset_Apple", "ruleset_China", "ruleset_Global", "ruleset_WeChat"} - done
    if missing:
        fail(f"锚点缺失，上游格式可能已变化，未打上的补丁: {sorted(missing)}")

    text = "\n".join(out) + "\n"

    # ---- 生成后自检（不过关就报错，不写出文件）----
    active = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    if sum(1 for l in active if l.startswith("dns-server =")) != 1:
        fail("dns-server 不是恰好一条")
    if any(l.startswith("fallback-dns-server =") for l in active):
        fail("fallback-dns-server 仍然存在")
    if f"dns-server = {DUAL_DOH}" not in active or f"direct-dns-server = {DUAL_DOH}" not in active \
       or f"proxy-dns-server = {DUAL_DOH}" not in active:
        fail("双 DoH 三行不完整")
    if "dns-fallback-system = false" not in active or "ipv6 = false" not in active:
        fail("fail-closed / ipv6 行缺失")
    for name, qx_url in QX_LISTS.items():
        if any(qx_url in l for l in active):
            fail(f"{name} 仍在引用 QX 原表")
        if sum(1 for l in active if SELF_HOSTED.format(name=name) in l) != 1:
            fail(f"{name} 自托管镜像引用不是恰好一条")
    finals = [l for l in active if l.startswith("FINAL,")]
    if finals != ["FINAL,PROXY,force-remote-dns"]:
        fail(f"FINAL 异常: {finals}")
    # FINAL 必须是 [Rule] 段最后一条生效规则
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
    if len(text.splitlines()) < 300:
        fail("输出行数异常（<300），疑似上游文件不完整")

    header = (
        "# ============================================================\n"
        "# lazy.conf（无策略组版）+ DNS 国内外分流补丁（自动生成，请勿手动编辑此文件）\n"
        "# 上游: https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/lazy.conf\n"
        "#       （懒人配置，同步自 LOWERTOP/Shadowrocket，版权与署名归上游所有）\n"
        "# 补丁: 仅 DNS 机制，均以 [DNS分流补丁] 注释标出；其余与上游逐字一致。\n"
        "#   国内: dns-server / direct-dns-server / proxy-dns-server = 阿里 DoH + 腾讯 DoH（加密）\n"
        "#   国外: 无本地解析 —— GEOIP 加 no-resolve、FINAL,PROXY,force-remote-dns 交节点远端解析；\n"
        "#         删除 fallback-dns-server 并设 dns-fallback-system = false（fail-closed）\n"
        "#   附带: ipv6 = false；Apple/China/Global/WeChat 四张表改引本仓库自托管镜像 rules/*.list\n"
        "#         （原 QX 格式 IP 规则无 no-resolve 会触发本地解析；镜像逐行一致、仅补 no-resolve）。\n"
        "#   注: [Host] 中 *.apple.com / *.icloud.com 的 server:system 是上游有意的兼容设计（保 Apple 服务解析质量），原样保留。\n"
        "# 本文件由 GitHub Actions 每日自动同步上游并重新打补丁生成。\n"
        "# ============================================================\n"
    )
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + text)
    print(f"OK: patched -> {out_path}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        fail("用法: python3 patch.py <input lazy.conf> <output>")
    main(sys.argv[1], sys.argv[2])
