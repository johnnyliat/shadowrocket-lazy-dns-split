#!/usr/bin/env python3
"""patch_lists.py — 把 blackmatrix7 的 4 张 QuantumultX 格式规则表做成自托管镜像。

背景（为什么需要这个脚本）:
  lazy.conf 引用的 Apple / China / Global / WeChat 四张表是 QuantumultX 格式，
  其 IP-CIDR / IP6-CIDR / IP-ASN 规则行不带 no-resolve。Shadowrocket 加载这类表后，
  一个没被任何域名规则命中的域名，会为了匹配这些 IP 规则先被本地解析一次
  （走配置里的国内 DoH）——境外域名因此在国内解析商处留下查询记录，
  DNS 泄露检测会因此报出国内 DNS 服务器，代理远端解析被这一步短路。
  同作者的 Shadowrocket 格式同名表虽全带 no-resolve，但只是骨架版
  （如 Global 仅 211 行 vs QX 版 36,025 行、China 仅 73 行 vs 3,765 行），
  换用会大面积丢失域名分流覆盖。故采用自托管镜像：与上游逐行一致，
  仅给 IP 类规则行末尾补上 ,no-resolve，其余字节不动。

用法: python3 patch_lists.py <QX 原版目录> <输出目录>
  输入目录需含 Apple.list / China.list / Global.list / WeChat.list

断言（任一不符 exit 1，不写出该文件）:
  - 每张表补上的条数不低于已知下限（上游删减 IP 规则到异常程度即报警）；
  - 补后全表 IP 类规则 100% 带 no-resolve；
  - 输出与输入逐行对应：除补后缀与首行镜像说明外不得有任何其他差异。
"""
import os
import re
import sys

IP_LINE = re.compile(r"^(IP-CIDR|IP-CIDR6|IP6-CIDR|IP-ASN),")
FILES = ["Apple", "China", "Global", "WeChat"]
MIN_PATCHED = {"Apple": 13, "China": 21, "Global": 119, "WeChat": 1}
SOURCE = "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/QuantumultX/{name}/{name}.list"

def fail(msg):
    print(f"PATCH-LISTS FAILED: {msg}", file=sys.stderr)
    sys.exit(1)

def build(name, src_path, out_path):
    lines = open(src_path, encoding="utf-8").read().splitlines()
    out, patched = [], 0
    for raw in lines:
        s = raw.strip()
        if IP_LINE.match(s) and "no-resolve" not in s:
            out.append(s + ",no-resolve")
            patched += 1
        else:
            out.append(raw)
    if patched < MIN_PATCHED[name]:
        fail(f"{name}: 只补上 {patched} 条（下限 {MIN_PATCHED[name]}），上游格式可能已变化")
    # 补后复查：IP 类规则必须全部带 no-resolve，且与输入逐行对应（仅允许补后缀）
    if len(out) != len(lines):
        fail(f"{name}: 行数变化 {len(lines)} -> {len(out)}")
    for a, b in zip(lines, out):
        if b != a and b != a.strip() + ",no-resolve":
            fail(f"{name}: 出现非预期改动: {a!r} -> {b!r}")
    bad = [l for l in out if IP_LINE.match(l.strip()) and "no-resolve" not in l]
    if bad:
        fail(f"{name}: 仍有 {len(bad)} 条 IP 规则无 no-resolve")
    header = (f"# Mirror of blackmatrix7 ios_rule_script rule/QuantumultX/{name}/{name}.list, "
              f"content identical except ',no-resolve' appended to IP-CIDR/IP6-CIDR/IP-ASN lines "
              f"(prevents local DNS resolution in Shadowrocket). Auto-synced daily. Source: {SOURCE.format(name=name)}")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + "\n" + "\n".join(out) + "\n")
    print(f"OK: {name}.list patched {patched} IP rules -> {out_path}")

def main(src_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name in FILES:
        src = os.path.join(src_dir, f"{name}.list")
        if not os.path.exists(src):
            fail(f"缺少输入文件: {src}")
        build(name, src, os.path.join(out_dir, f"{name}.list"))

if __name__ == "__main__":
    if len(sys.argv) != 3:
        fail("用法: python3 patch_lists.py <QX 原版目录> <输出目录>")
    main(sys.argv[1], sys.argv[2])
