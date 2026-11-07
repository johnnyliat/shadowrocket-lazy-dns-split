<div align="center">

# shadowrocket-lazy-dns

**懒人配置 + DNS 国内外分流 · 自动同步版**

国内直连解析 · 国外 VPS 远程解析 · 防 DNS 泄露

[![Sync status](https://github.com/johnnyliat/shadowrocket-lazy-dns/actions/workflows/sync.yml/badge.svg)](https://github.com/johnnyliat/shadowrocket-lazy-dns/actions)

</div>

---

## 这个仓库是做什么的

市面上的分流规则大多只解决「哪些流量走代理」，很少认真处理 DNS：国外域名也在本地解析，轻则被污染拿到错误 IP，重则查询记录直接暴露给本地网络。

本仓库在成熟的「懒人配置」之上补上这一课：

- **借鉴** [DuskWander87/shadowrocket-config](https://github.com/DuskWander87/shadowrocket-config) 的 DNS 分流思路——境外域名不在本地解析、交给节点远端处理；
- **改进** 其本地解析仍用明文 UDP 的问题——本仓库的本地解析全部换成加密 DoH；
- **自动化** 维护——GitHub Actions 每天自动拉取上游最新规则、重新打上 DNS 补丁并发布，导入一次，长期自动跟新。

## 两个版本

| 版本 | 名称 | 内容 |
|---|---|---|
| **版本一** | 懒人配置 + DNS 国内外分流 | 国内直连解析 · 国外 VPS 远程解析 · 防 DNS 泄露（文件 `lazy-dns.conf`） |
| **版本二** | 懒人配置 + DNS 国内外分流 + 去广告 | 与版本一完全一致，额外全量内嵌去广告规则约 5.3 万条（文件 `lazy-dns-ad.conf`） |

两个版本都可以导入到 Shadowrocket 里，平时用哪个、临时换哪个都很方便。一个很实际的场景：有些网站会检测广告拦截，被检测到就限制下载之类的功能，这时切到没有广告拦截的版本一，用完再切回版本二就行。去广告是在规则层完成的，不需要 MITM、不需要证书。

## 扫码导入

先在 Shadowrocket 首页**添加并选中你自己的节点**（本仓库不提供节点），再任选下方一个二维码，用 Shadowrocket 右上角的扫码功能导入。每个版本有两条线路，内容完全相同，扫任意一个即可：

- **线路 A · jsDelivr（推荐）**：国内可直达，没代理也能导入；CDN 有数小时缓存，更新稍有延迟。
- **线路 B · Raw（无缓存）**：GitHub 直出，更新即时生效；国内网络通常需要先有可用代理才能打开。

### 版本一 · 懒人配置 + DNS 国内外分流

| 线路 A · jsDelivr（推荐，有缓存） | 线路 B · Raw（无缓存，即时更新） |
| :---: | :---: |
| <img src="qrcode.png" width="200" alt="版本一 jsDelivr 二维码"> | <img src="qrcode-raw.png" width="200" alt="版本一 Raw 二维码"> |
| `https://cdn.jsdelivr.net/gh/johnnyliat/shadowrocket-lazy-dns@main/lazy-dns.conf` | `https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns/main/lazy-dns.conf` |

### 版本二 · 懒人配置 + DNS 国内外分流 + 去广告

| 线路 A · jsDelivr（推荐，有缓存） | 线路 B · Raw（无缓存，即时更新） |
| :---: | :---: |
| <img src="qrcode-ad.png" width="200" alt="版本二 jsDelivr 二维码"> | <img src="qrcode-ad-raw.png" width="200" alt="版本二 Raw 二维码"> |
| `https://cdn.jsdelivr.net/gh/johnnyliat/shadowrocket-lazy-dns@main/lazy-dns-ad.conf` | `https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns/main/lazy-dns-ad.conf` |

导入后，全局路由选「配置」；在「设置 → 更新」里给配置开启自动更新，之后就不用再管了。

---

## 分流与防泄露原理

| 场景 | 处理方式 |
|---|---|
| 国内域名（直连） | 阿里 DoH + 腾讯 DoH 本地加密解析（`dns-server` / `direct-dns-server` / `proxy-dns-server` 三处一致），拿国内 CDN 就近 IP |
| 国外域名（代理） | 本地不解析：IP 规则 `no-resolve` + `FINAL,PROXY,force-remote-dns`，域名原样交节点在 VPS 远端解析 |
| 解析故障时 | 没有备用回退：已删除 `fallback-dns-server`，`dns-fallback-system = false`，宁可失败也不把查询漏给运营商（fail-closed） |
| IPv6 | `ipv6 = false`，防止 v6 通道绕过分流 |
| Apple / iCloud | `[Host]` 中 `server:system` 是上游有意的兼容设计（保 Apple 本地 CDN 解析质量），原样保留；仍为国内直连，详见常见问题 |

## 自动更新

每天北京时间约 09:07，GitHub Actions 会下载上游的 `lazy.conf` 和 `sr_ad_only.conf`，用仓库里的两个构建脚本重新生成两个成品配置。脚本对每一处修改都做了校验，生成后还会自检一遍条数和关键参数，对不上就直接报错停下来，不会把有问题的配置发上来。所以这里的成品，始终是「上游最新规则 + 这套 DNS 设计」的组合。

## 常见问题

**导入后没网？** 先确认首页已经添加并选中了自己的可用节点，再确认全局路由选的是「配置」。

**需要什么版本？** Shadowrocket **2.2.92 或更高**。直连专用 DNS 字段 `direct-dns-server` 是这个版本才有的功能，版本低了会被忽略，分流效果打折。

**去广告误杀怎么办？** 名单来自上游 `sr_ad_only`，个别 App 的功能域名偶尔会被误拦。先切到版本一对比确认，再向上游反馈，本仓库每天同步上游的修正。

**小众国内网站走了代理？** 这是防泄露设计的取舍：判断一个域名是不是国内 IP 需要先解析，而解析本身正是要避免的动作，所以没被域名规则收录的站点一律先走代理远端解析。常用服务都有专门的直连规则，不受影响。

**Apple 的 `server:system` 是什么？** 这是上游让 `*.apple.com`、`*.icloud.com` 继续用系统（运营商）DNS 的写法，为的是拿到最准的 Apple 本地 CDN 节点，App Store 和 iCloud 的解析更稳。代价只有一点：这两组域名的查询是明文的，不过它们依然是国内直连、解析到的也是国内 IP。如果更在意这一点，在 Shadowrocket 的「配置 → 主机」里把这两行删掉就可以，删完 Apple 域名会改走上面的国内 DoH。

## 文件说明

| 文件 | 作用 |
|---|---|
| `lazy-dns.conf` / `lazy-dns-ad.conf` | 两个成品配置，由 Actions 每天跟着上游自动生成；想调整规则的话，改下面的构建脚本更合适 |
| `patch.py` / `patch_ad.py` | 两个构建脚本，负责 DNS 补丁（去广告版多一步规则内嵌） |
| `.github/workflows/sync.yml` | 每日定时同步任务 |
| `qrcode.png` / `qrcode-raw.png` | 版本一的 jsDelivr / Raw 导入二维码 |
| `qrcode-ad.png` / `qrcode-ad-raw.png` | 版本二的 jsDelivr / Raw 导入二维码 |

## 版权与出处

- **分流与去广告规则**：[Johnshall / Shadowrocket-ADBlock-Rules-Forever](https://github.com/Johnshall/Shadowrocket-ADBlock-Rules-Forever)（懒人配置同步自 [LOWERTOP/Shadowrocket](https://github.com/LOWERTOP/Shadowrocket)，规则基于 [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script) 生成），版权归原作者所有。
- **DNS 分流思路**：借鉴 [DuskWander87/shadowrocket-config](https://github.com/DuskWander87/shadowrocket-config)；本地解析改加密 DoH 和自动同步流水线，是本仓库在这之上的加工。
- 本仓库仅供个人学习与自用，不对上游规则主张任何权利；如有侵权请联系删除。
