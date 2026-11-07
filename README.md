<h1 align="center">Shadowrocket 懒人配置 + DNS 国内外分流</h1>

<p align="center">国内双 DoH 直连解析 · 境外代理远端解析 · 每日自动同步上游 · 可选去广告</p>

<p align="center">
  <a href="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/sync.yml"><img src="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/sync.yml/badge.svg" alt="每日同步状态"></a>
  <a href="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/health.yml"><img src="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/health.yml/badge.svg" alt="每周健康检查状态"></a>
</p>

## 这是什么

基于 Johnshall 的 Shadowrocket 懒人配置（lazy.conf，无策略组版）制作。保留上游原有的国内外应用分流规则，只增加两样东西：DNS 国内外分流、自动化维护。

流量分流和 DNS 解析是两件事。规则决定流量走直连还是代理，DNS 决定域名在哪里被解析。原版懒人配置在匹配 IP 类规则时可能先在本地解析一次域名，境外域名因此仍会被本地 DNS 查询到。本项目处理的就是这一段：

- 国内解析：阿里 DNS 与 DNSPod 双 DoH，代替明文 UDP 解析。
- 境外解析：规则层补齐 `no-resolve`，境外域名不在本地解析，交代理节点远端处理。
- 规则修正：上游四张 QuantumultX 规则表的 IP 类规则原本没有 `no-resolve`，本项目自建镜像逐行补齐，规则内容一条不改、一条不少。
- 自动维护：每日同步上游、重新构建、强制自检，通过后才发布。

不改上游的分流体系，不加策略组，不要求额外模块。

## 两个版本

两份配置的 DNS 方案与基础分流规则完全相同，区别只有是否内嵌去广告规则。去广告规则来自上游 sr_ad_only.conf，构建时直接写入 `[Rule]` 段，不需要另外导入广告模块；拦截不依赖 HTTPS 解密，不用装根证书。

日常用去广告版。某个 App 或网站在去广告版下出现异常时，切到基础版对照即可定位是不是广告规则误伤。

本项目只提供配置与规则，不提供代理节点。导入前先准备好自己的节点。

### 版本一：懒人配置 + DNS 国内外分流

`lazy-dns.conf`，不含去广告规则。

| 线路 A · jsDelivr（CDN，国内可直达） | 线路 B · GitHub Raw（无缓存，即时更新） |
| :---: | :---: |
| <img src="qrcode.png" width="190" alt="jsDelivr 二维码"> | <img src="qrcode-raw.png" width="190" alt="Raw 二维码"> |

线路 A：`https://cdn.jsdelivr.net/gh/johnnyliat/shadowrocket-lazy-dns-split@main/lazy-dns.conf`
线路 B：`https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/lazy-dns.conf`

### 版本二：懒人配置 + DNS 国内外分流 + 去广告

`lazy-dns-ad.conf`，构建时内嵌 sr_ad_only 全部规则（当前 53,566 条，构建脚本逐条计数校验）。

| 线路 A · jsDelivr（CDN，国内可直达） | 线路 B · GitHub Raw（无缓存，即时更新） |
| :---: | :---: |
| <img src="qrcode-ad.png" width="190" alt="jsDelivr 二维码"> | <img src="qrcode-ad-raw.png" width="190" alt="Raw 二维码"> |

线路 A：`https://cdn.jsdelivr.net/gh/johnnyliat/shadowrocket-lazy-dns-split@main/lazy-dns-ad.conf`
线路 B：`https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/lazy-dns-ad.conf`

两条线路内容一致，以仓库当前提交为准。jsDelivr 每次同步后会主动刷新并逐文件校验哈希；Raw 直接读仓库，没有缓存延迟，但国内网络访问可能需要代理。

## 导入与启用

1. 扫码或复制链接，在 Shadowrocket 中从 URL 导入配置。
2. 选中导入的配置作为当前配置。
3. 首页「全局路由」选「配置」。
4. 选中自己的节点并连接。
5. 在 Shadowrocket 的设置中开启配置自动更新。

要求 Shadowrocket 2.2.92 或更高版本。配置用到的 `direct-dns-server` 字段在低版本上不生效。

## DNS 设计

三个解析通道各管一段，不要混为一谈：

| 通道 | 取值 | 负责什么 |
| --- | --- | --- |
| `dns-server` | 阿里 DoH + DNSPod DoH | 常规查询的默认解析 |
| `direct-dns-server` | 阿里 DoH + DNSPod DoH | 命中直连规则的域名 |
| `proxy-dns-server` | 阿里 DoH + DNSPod DoH | 只解析代理节点自身的域名，与目标网站无关 |

两个 DoH 之间是竞速关系，不是主备：两个服务商都可能收到查询，换来的是任一服务商故障时解析不中断。查询走 HTTPS 加密，本地网络与运营商看不到查询内容。

命中代理的域名不在本地解析。收尾两条规则保证这一点：

```
GEOIP,CN,DIRECT,no-resolve
FINAL,PROXY,force-remote-dns
```

- `no-resolve`：匹配 IP 与地理类规则时不主动发起本地解析，域名原样往下走。
- `force-remote-dns`：最终进代理的域名交节点在远端解析。远端最终用哪个 DNS 上游由服务器端（例如 Xray 的 DNS 设置）决定，不在本配置的控制范围内。

另外，配置关闭了系统回退：`fallback-dns-server` 已删除、`dns-fallback-system = false`，国内双 DoH 都不可达时也不会改用运营商的系统 DNS；IPv6 解析关闭（`ipv6 = false`）。

### 规则镜像

上游引用的 Apple、China、Global、WeChat 四张表是 QuantumultX 格式，其中 IP-CIDR、IP6-CIDR、IP-ASN 类规则没有 `no-resolve`。Shadowrocket 为匹配这些规则会先把域名在本地解析一次，未被任何域名规则命中的境外域名（例如 DNS 泄露测试使用的随机子域名）会在这里被本地解析出去。

处理办法是自建镜像：每日从上游原样下载这四张表，只给 IP 类规则行尾补 `,no-resolve`，其余逐行一致，配置改为引用仓库内的镜像。没有改用 blackmatrix7 的 Shadowrocket 同名表，因为那几张是精简骨架版（例如 Global 表 QX 版约 3.6 万行，SR 版只有 211 行），换用会大面积丢失分流覆盖。

构建最后一步 `check_no_resolve.py` 会把两份成品引用的全部远程规则集逐张下载扫描：任何一条 IP 类规则缺 `no-resolve`，构建直接失败，不生成、不提交。

## 已知例外

以下例外都是明确列出的，不隐藏：

- **Apple / iCloud 使用系统 DNS。** 上游在 `[Host]` 中设置了 `*.apple.com` 与 `*.icloud.com` 走 `server:system`，目的是让 Apple 的本地 CDN 调度、App Store 与 iCloud 同步保持上游验证过的行为。本项目保留这两行。代价是这两个域名范围的解析走当前网络的系统 DNS，不是国内 DoH；其余域名不受影响。介意的话可以自行删掉这两行，删后建议留意 App Store 下载与 iCloud 同步是否正常。
- **直连解析故障时允许经代理解析。** `dns-direct-fallback-proxy = true` 是上游原值：只有当国内双 DoH 全部不可达时，直连域名的解析才允许借代理完成，避免 DNS 故障直接等于断网。它不改变正常情况下的分工。
- **少数地址不进隧道。** 上游 `skip-proxy` 中的局域网地址段、三家银行域名（建行、农行、邮储）与个别门户站点不经过 Shadowrocket 隧道，按系统路径直连，这是上游为银行类 App 兼容做的设置，本项目原样保留。
- **配置管不到的通道。** 应用自带 DoH/DoT、iCloud Private Relay、其他 VPN 或描述文件接管的查询，不在本配置的控制范围内。

## 自动维护

成品不是手工维护的静态文件，由 GitHub Actions 每日生成。

每日同步（北京时间约 09:07）：

1. 下载上游最新 lazy.conf 与 sr_ad_only.conf。
2. 下载 blackmatrix7 四张 QuantumultX 原表。
3. `patch_lists.py` 生成带 `no-resolve` 的镜像（带条数下限与逐行一致性断言）。
4. `patch.py` 生成基础版；`patch_ad.py` 生成去广告版（内嵌条数断言）。
5. `check_no_resolve.py` 扫描两份成品及其引用的全部远程规则集。
6. 全部通过才提交；上游格式漂移、规则缺失或检查不过，整轮失败，线上继续用上一版。
7. 提交后刷新 jsDelivr 缓存，逐个比对六个文件（两份成品 + 四张镜像）的 SHA-256；不一致自动重试，三轮仍不一致则任务失败并报错。

每周健康检查（北京时间周一约 11:17）：探活上游两个文件、懒人配置当前引用的全部远程规则集（名单每次从最新上游现场提取，不写死），以及本仓库在 jsDelivr 上的六个分发文件。任一不可用，任务失败。

说明两点：一是自动检查覆盖的是格式、计数、规则完整性与分发一致性，不等于人工逐条审核上游规则内容；二是 GitHub 定时任务与 CDN 节点同步都可能有分钟级延迟，这是平台行为，校验步骤能发现不一致并报错，但不能让延迟消失。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `lazy-dns.conf` | 基础版成品（自动生成，勿手改） |
| `lazy-dns-ad.conf` | 去广告版成品（自动生成，勿手改） |
| `rules/*.list` | 四张规则镜像（自动生成，勿手改） |
| `patch.py` | 基础版构建：DNS 补丁 + 镜像引用改写，带断言 |
| `patch_ad.py` | 去广告版构建：DNS 补丁 + 广告规则内嵌与计数 |
| `patch_lists.py` | 镜像构建：给四张原表的 IP 规则补 `no-resolve` |
| `check_no_resolve.py` | 出厂守卫：全量扫描 IP 规则的 `no-resolve` 覆盖 |
| `.github/workflows/sync.yml` | 每日同步、构建、提交、CDN 刷新与校验 |
| `.github/workflows/health.yml` | 每周探活上游、规则集与自家分发文件 |

需要改生成逻辑时，改构建脚本，由 Actions 验证后再发布，不要直接编辑成品。

## 常见问题

**导入后无法联网？**
先确认已选中自己的可用节点、全局路由为「配置」，并确认配置与远程规则集加载成功。本项目不提供节点。

**为什么部分小众国内网站走了代理？**
未被任何域名规则收录的域名默认按境外处理：走代理、远端解析。这是防泄露的默认方向。代价是少数没被收录的小众国内站点会绕一圈代理。常用国内应用在上游规则里都有单独分流，不受影响。

**为什么 DNS 检测里境外域名显示的是 Cloudflare（或其他国外解析商）？**
那正是远端解析的结果：域名由节点服务器解析，检测站看到的是服务器端 DNS 上游。显示哪一家取决于服务器配置，与本配置无关。

**为什么 DNS 检测里可能出现系统 DNS？**
先看触发检测的域名属于哪一类：Apple/iCloud 命中上述例外时会走系统 DNS；应用自带加密 DNS 或 Private Relay 也可能产生独立的查询。单个检测结果不能代表全部流量，需要结合域名与连接记录判断。

**去广告版下某个 App 功能异常怎么办？**
切到基础版复测。基础版正常、去广告版异常，一般是广告规则误伤；两个版本规则相同，切换不需要改其他设置。

**为什么不直接用 Shadowrocket 格式的同名规则表？**
见「规则镜像」一节：同名 SR 表是骨架版，覆盖差一个数量级。

## 上游与版权

本仓库的规则内容来自以下项目，本仓库只增加 DNS 补丁、规则镜像与自动构建，不拥有上游规则的版权，相关权利与许可遵循各上游项目的说明：

- [Johnshall / Shadowrocket-ADBlock-Rules-Forever](https://github.com/Johnshall/Shadowrocket-ADBlock-Rules-Forever)：懒人配置与去广告规则，懒人配置同步自 LOWERTOP。
- [LOWERTOP / Shadowrocket](https://github.com/LOWERTOP/Shadowrocket)：懒人配置原始来源与 Shadowrocket 使用文档。
- [blackmatrix7 / ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)：应用分流规则集。
- [DuskWander87 / shadowrocket-config](https://github.com/DuskWander87/shadowrocket-config)：DNS 分流与远端解析的设计思路参考。

仅供学习与个人使用。如有版权问题，请联系处理。
