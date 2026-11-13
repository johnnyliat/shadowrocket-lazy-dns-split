#!/usr/bin/env python3
"""build_manifest.py — 生成 manifest.json：本次构建的确定性来源记录。

内容：上游文件、仓库输入（mirrors.txt / custom-rules.txt）、构建脚本、
两份成品与全部规则表的 SHA256 与行数。不含时间戳与随机值：输入不变则
文件逐字节不变，git 只在内容真正变化时才提交它。

用途：构建溯源（某份成品由哪些输入、哪份代码生成，一查便知）；每周健
康检查用它核对线上成品的 SHA256 是否与构建记录一致（内容校验，不只看
HTTP 200）。

用法（在仓库根目录运行）: python3 build_manifest.py <上游 lazy.conf> <上游 sr_ad_only.conf>
"""
import hashlib
import json
import os
import sys

import build_common
from build_common import atomic_write_text, fail

build_common.set_fail_prefix("MANIFEST FAILED")

SCRIPTS = ["build_common.py", "patch.py", "patch_ad.py", "patch_lists.py",
           "compile_supplement.py", "check_no_resolve.py",
           "verify_routing.py", "build_manifest.py"]
PRODUCTS = ["lazy-dns.conf", "lazy-dns-ad.conf"]
INPUTS = ["mirrors.txt", "custom-rules.txt"]


def record(path):
    if not os.path.exists(path):
        fail(f"manifest 输入缺失: {path}")
    with open(path, "rb") as f:
        data = f.read()
    return {"sha256": hashlib.sha256(data).hexdigest(),
            "lines": data.count(b"\n")}


def build_manifest(upstream_lazy, upstream_ad, root="."):
    manifest = {"version": 1}
    manifest["inputs"] = {
        "lazy.conf": record(os.path.join(root, upstream_lazy)),
        "sr_ad_only.conf": record(os.path.join(root, upstream_ad)),
    }
    for name in INPUTS:
        manifest["inputs"][name] = record(os.path.join(root, name))
    manifest["scripts"] = {name: record(os.path.join(root, name))
                           for name in SCRIPTS}
    manifest["products"] = {name: record(os.path.join(root, name))
                            for name in PRODUCTS}
    rules_dir = os.path.join(root, "rules")
    if not os.path.isdir(rules_dir):
        fail(f"manifest 输入缺失: {rules_dir}")
    manifest["rules"] = {name: record(os.path.join(rules_dir, name))
                         for name in sorted(os.listdir(rules_dir))
                         if name.endswith(".list")}
    return manifest


def main(upstream_lazy, upstream_ad, root="."):
    manifest = build_manifest(upstream_lazy, upstream_ad, root)
    text = json.dumps(manifest, indent=2, sort_keys=True,
                      ensure_ascii=False) + "\n"
    out_path = os.path.join(root, "manifest.json")
    atomic_write_text(out_path, text)
    print(f"OK: manifest.json（{len(manifest['rules'])} 张规则表、"
          f"{len(manifest['scripts'])} 个脚本）-> {out_path}")
    return manifest


if __name__ == "__main__":
    if len(sys.argv) != 3:
        fail("用法: python3 build_manifest.py <上游 lazy.conf> <上游 sr_ad_only.conf>")
    main(sys.argv[1], sys.argv[2])
