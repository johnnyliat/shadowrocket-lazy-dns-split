# shadowrocket-lazy-dns

懒人配置（无策略组版）+ **DNS 国内外分流** 的自动同步仓库，含两个版本：**纯净版**（下方第一部分）与**去广告版**（下方第二部分），任选其一导入即可。

- 上游：<https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/lazy.conf>（懒人配置，同步自 LOWERTOP/Shadowrocket，版权与署名归上游所有）；去广告版另用同作者的 `sr_ad_only.conf`（仅去广告规则）
- 本仓库由 GitHub Actions **每天自动**拉取上游最新版：纯净版用 `patch.py` 只打 DNS 补丁生成 `lazy-dns.conf`；去广告版用 `patch_ad.py` 在此基础上再内嵌去广告规则生成 `lazy-dns-ad.conf`。上游一更新，这边自动跟上，DNS 改动不会被冲掉。

## DNS 设计

思路借鉴 DuskWander87/shadowrocket-config（规则层远端解析），但本地解析不用明文 UDP：

| 场景 | 方式 |
|---|---|
| 国内域名（直连） | 阿里 DoH + 腾讯 DoH 加密解析：`https://dns.alidns.com/dns-query`、`https://doh.pub/dns-query`（`dns-server` / `direct-dns-server` / `proxy-dns-server` 三处相同） |
| 国外域名（代理） | 本地不解析：`GEOIP,CN,DIRECT,no-resolve` + `FINAL,PROXY,force-remote-dns`，域名交节点在远端解析 |
| 备用解析 | 无。已删除 `fallback-dns-server`，`dns-fallback-system = false`，fail-closed 不回退运营商 DNS |
| IPv6 | `ipv6 = false`，防止 v6 通道绕过分流 |

## 在 Shadowrocket 里使用

先在首页添加并选中你自己的节点（本配置不含节点），再从下面任一地址导入配置，全局路由选「配置」：

- jsDelivr（国内可直达，有数小时缓存）：`https://cdn.jsdelivr.net/gh/johnnyliat/shadowrocket-lazy-dns@main/lazy-dns.conf`
- Raw（无缓存，更新最及时）：`https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns/main/lazy-dns.conf`

扫码导入（二维码内容为上面的 jsDelivr 地址，在 Shadowrocket 里点右上角扫码图标扫描即可）：

![扫码导入](qrcode.png)

导入后可在「设置 → 更新」里为该配置开启自动更新。

---

## 版本二：去广告版（懒人配置 + DNS 分流 + 去广告）

与上面的纯净版完全相同的懒人分流与 DNS 分流，只是在 `[Rule]` 最前面额外全量内嵌了 `sr_ad_only.conf` 的去广告规则（约 5.3 万条 REJECT，上游更新每日自动同步）。去广告在规则层完成，不需要开启 MITM、不需要安装证书。两个版本任选其一导入，不要同时使用。

- jsDelivr（国内可直达，有数小时缓存）：`https://cdn.jsdelivr.net/gh/johnnyliat/shadowrocket-lazy-dns@main/lazy-dns-ad.conf`
- Raw（无缓存，更新最及时）：`https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns/main/lazy-dns-ad.conf`

扫码导入（二维码内容为上面的 jsDelivr 地址）：

![扫码导入-去广告版](qrcode-ad.png)

## 工作原理

1. `.github/workflows/sync.yml`：每天北京时间约 09:07 触发（也可手动 Run workflow）
2. 下载上游 `lazy.conf` 与 `sr_ad_only.conf` → `patch.py` 生成纯净版 `lazy-dns.conf`，`patch_ad.py` 生成去广告版 `lazy-dns-ad.conf`
3. 两个脚本每处修改都有断言与生成后自检（含去广告条数核对）：上游若改了格式，任务会**失败报警**而不是提交错误配置
4. 内容有变化才提交两个成品文件

## 免责声明

个人学习与自用。上游配置的版权归其作者所有；本仓库仅对其做 DNS 参数层面的机械修改并注明出处。
