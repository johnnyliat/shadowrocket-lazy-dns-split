#!/usr/bin/env python3
"""patch_lists.py — 把成品引用的全部规则表同步成本仓库的自托管镜像。

背景（为什么需要这个脚本）:
  lazy.conf 引用的 Apple / China / Global / WeChat 四张表是 QuantumultX 格式，
  其 IP 类规则行不带 no-resolve。Shadowrocket 加载这类表后，一个没被任何
  域名规则命中的域名，会为了匹配这些 IP 规则先被本地解析一次（走配置里的
  国内 DoH）——境外域名因此在国内解析商处留下查询记录，DNS 泄露检测会因此
  报出国内 DNS 服务器，代理远端解析被这一步短路。同作者的 Shadowrocket
  格式同名表虽全带 no-resolve，但只是骨架版（如 Global 仅 211 行 vs QX 版
  36,025 行），换用会大面积丢失域名分流覆盖。故采用自托管镜像：与上游
  逐行一致，仅给 IP 类规则行末尾补上 ,no-resolve，其余字节不动。
  其余 30 张 Shadowrocket 格式的表 IP 规则本就带 no-resolve，镜像时逐行
  照搬（断言保证零改动）；把它们一并镜像，是为了让客户端取到的每一张表
  都等于构建时被 check_no_resolve.py 检查过的那一份，上游实时内容的变化
  不会绕过出厂检查直达客户端。

用法: python3 patch_lists.py（无参数，在仓库根目录运行，清单读 mirrors.txt）

断言（任一不符 exit 1，整轮构建失败）:
  - 每张表下载成功且不像 HTML 错误页，行数不低于 3；
  - 输出与输入逐行对应：除给缺失的 IP 规则补 ,no-resolve 后缀与首行镜像
    说明外，不得有任何其他差异；
  - 补后全表行首 IP 类规则 100% 带 no-resolve；
  - QX 四张表补上的条数不低于已知下限（上游异常删减即报警）。
"""
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
IP_LINE = re.compile(r"^(IP-CIDR|IP-CIDR6|IP6-CIDR|IP-ASN|GEOIP),", re.I)
MIN_PATCHED = {"Apple": 13, "China": 21, "Global": 119, "WeChat": 1}

def fail(msg):
    print(f"PATCH-LISTS FAILED: {msg}", file=sys.stderr)
    sys.exit(1)

def fetch(url):
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (patch_lists)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
    fail(f"下载失败 {url}: {last}")

def load_manifest():
    path = os.path.join(BASE, "mirrors.txt")
    if not os.path.exists(path):
        fail("缺少 mirrors.txt")
    entries = []
    for raw in open(path, encoding="utf-8"):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) != 2:
            fail(f"mirrors.txt 行格式不对: {s!r}")
        entries.append((parts[0], parts[1]))
    if not entries:
        fail("mirrors.txt 是空的")
    if len({u for u, _ in entries}) != len(entries):
        fail("mirrors.txt 上游地址有重复")
    if len({p for _, p in entries}) != len(entries):
        fail("mirrors.txt 本地路径有重复")
    return entries

def build(url, local):
    body = fetch(url)
    if body.lstrip().startswith("<"):
        fail(f"{url} 返回的像是 HTML 错误页")
    lines = body.splitlines()
    if len(lines) < 3:
        fail(f"{url} 行数异常: {len(lines)}")
    out, patched = [], 0
    for raw in lines:
        s = raw.strip()
        if IP_LINE.match(s) and "no-resolve" not in s.lower():
            out.append(raw.rstrip() + ",no-resolve")
            patched += 1
        else:
            out.append(raw)
    name = os.path.basename(local)
    stem = name[:-5] if name.endswith(".list") else name
    if stem in MIN_PATCHED and patched < MIN_PATCHED[stem]:
        fail(f"{name}: 只补上 {patched} 条（下限 {MIN_PATCHED[stem]}），上游格式可能已变化")
    if len(out) != len(lines):
        fail(f"{name}: 行数变化 {len(lines)} -> {len(out)}")
    for a, b in zip(lines, out):
        if b != a and b != a.rstrip() + ",no-resolve":
            fail(f"{name}: 出现非预期改动: {a!r} -> {b!r}")
    bad = [l for l in out if IP_LINE.match(l.strip()) and "no-resolve" not in l.lower()]
    if bad:
        fail(f"{name}: 仍有 {len(bad)} 条 IP 规则无 no-resolve")
    path_desc = url[len("https://raw.githubusercontent.com/"):] if url.startswith("https://raw.githubusercontent.com/") else url
    header = (f"# Mirror of {path_desc}, content identical to upstream except ',no-resolve' "
              f"appended to IP rule lines lacking it (prevents local DNS resolution in "
              f"Shadowrocket). Auto-synced daily. Source: {url}")
    out_path = os.path.join(BASE, local)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + "\n" + "\n".join(out) + "\n")
    print(f"OK: {name} {len(lines)} lines, patched {patched} -> {local}")

def main():
    for url, local in load_manifest():
        build(url, local)

if __name__ == "__main__":
    main()
