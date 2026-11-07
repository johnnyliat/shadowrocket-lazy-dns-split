<h1 align="center">Shadowrocket 懒人配置 + DNS 国内外分流</h1>

<p align="center">国内双 DoH 直连解析 · 境外代理远端解析 · 每日自动同步上游 · 可选去广告</p>

<p align="center">
  <a href="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/sync.yml"><img src="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/sync.yml/badge.svg" alt="每日同步状态"></a>
  <a href="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/health.yml"><img src="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/health.yml/badge.svg" alt="每周健康检查状态"></a>
</p>

## 介绍

在 Johnshall 的懒人配置（lazy.conf，无策略组）基础上，只补了两块原版没处理的部分：DNS 解析的分流，和配置的自动维护。分流规则、分流顺序全部沿用上游，没有改动，也没有加策略组。

原版的情况是：流量该直连的直连、该代理的代理，分得没问题，但域名解析默认都发生在本地。境外域名在匹配 IP 规则时也会先被本地解析一次，解析记录留在本地网络里。这份配置把这一段补上：

- 直连域名：本地解析，解析服务器换成阿里和腾讯的 DoH，全程加密；
- 代理域名：本地不解析，域名原样交给节点，在服务器端解析；
- 每天自动跟上游同步，构建时带完整性检查，检查不过不发布。

## 两个版本

两个版本都可以导入，换着用。平时用去广告版；有些网站会检测广告拦截、拦下就限制功能，遇到这种站点切到基础版，用完再切回来。两份配置的 DNS 和分流规则完全一样，只是去广告版多内嵌了一套广告拦截规则，来自上游的 sr_ad_only.conf，写在规则最前面，不需要再单独装广告模块，也不用开 HTTPS 解密。

这个仓库只提供配置和规则，节点需要自己准备。

### 版本一：懒人配置 + DNS 国内外分流

| 线路 A · jsDelivr | 线路 B · GitHub Raw |
| :---: | :---: |
| <img src="qrcode.png" width="190" alt="版本一 jsDelivr 二维码"> | <img src="qrcode-raw.png" width="190" alt="版本一 Raw 二维码"> |

线路 A（CDN 分发，国内可以直接打开）：

```text
https://cdn.jsdelivr.net/gh/johnnyliat/shadowrocket-lazy-dns-split@main/lazy-dns.conf
```

线路 B（直接读仓库文件，没有缓存延迟，国内打开可能需要代理）：

```text
https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/lazy-dns.conf
```

### 版本二：懒人配置 + DNS 国内外分流 + 去广告

| 线路 A · jsDelivr | 线路 B · GitHub Raw |
| :---: | :---: |
| <img src="qrcode-ad.png" width="190" alt="版本二 jsDelivr 二维码"> | <img src="qrcode-ad-raw.png" width="190" alt="版本二 Raw 二维码"> |

线路 A（CDN 分发，国内可以直接打开）：

```text
https://cdn.jsdelivr.net/gh/johnnyliat/shadowrocket-lazy-dns-split@main/lazy-dns-ad.conf
```

线路 B（直接读仓库文件，没有缓存延迟，国内打开可能需要代理）：

```text
https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/lazy-dns-ad.conf
```

两条线路的内容是同一份文件。jsDelivr 这条每次同步后都会刷新缓存并校验内容，和仓库对不上会报错，不会悄悄发旧文件。

## 导入

1. 扫码，或者复制上面的地址，在 Shadowrocket 里从 URL 导入。
2. 选中导入的配置。
3. 首页「全局路由」选「配置」。
4. 选好自己的节点，连上。
5. 在 Shadowrocket 设置里打开配置自动更新，后面就不用再管了。

需要 Shadowrocket 2.2.92 以上版本，配置里用到的 `direct-dns-server` 在低版本上不生效。

## 导入后：测一下 DNS 泄露

导入配置、并在 Shadowrocket 里切换到这份配置文件之后，打开下面三个网站测一遍，三个都测，结果互相印证才可靠。原理不复杂：这些页面里藏着谁都没访问过的随机子域名，一打开，手机就必须现场查它的 IP，网站趁机记下到底是哪台 DNS 服务器在回答。

**[BrowserLeaks](https://browserleaks.com/dns)**：
信息最全。直接列出执行解析的 DNS 服务器地址、所属运营商和所在国家，解析发生在哪里看得很清楚。

**[ipleak.net](https://ipleak.net)**：
一页同时显示出口 IP 和 DNS 服务器。出口在哪个国家、解析在哪个国家，并排对照最方便。

**[DNS Leak Test](https://www.dnsleaktest.com/)**：
老牌检测站，选 Extended test，探针更多、查得更细，适合放在最后做确认。

**通过标准**：三个站列出的 DNS 服务器应当全部位于出口 IP 所在的国家（一般显示为 Cloudflare），不应出现其他区域的，尤其是阿里、腾讯和本地运营商。三个站结果一致，就是没有泄露。个别 Apple 域名走系统 DNS 是配置里有意留的例外，检测时遇到不用管，细节见后面「例外」一节。

## DNS 的分工

| 场景 | 解析方式 |
| --- | --- |
| 命中直连的域名 | 阿里 DoH + 腾讯 DoH，在本地加密解析 |
| 命中代理的域名 | 本地不解析，交节点在服务器端解析 |
| 节点自己的域名 | 阿里 DoH + 腾讯 DoH（连接节点之前先要知道节点的地址） |

两个 DoH 是同时竞速的，不是主备关系，哪家先返回用哪家的结果。好处是其中一家出问题时解析不断，代价是两家都可能看到查询记录。查询内容走 HTTPS，本地网络和运营商看不到明文。

代理域名不在本地解析，靠的是规则收尾这两行：

```ini
GEOIP,CN,DIRECT,no-resolve
FINAL,PROXY,force-remote-dns
```

`no-resolve` 的意思是：拿 IP 和地理位置这类规则去匹配域名时，不许为了匹配先把域名解析一遍。`force-remote-dns` 是最后兜底：没被任何规则命中的域名一律走代理、远端解析。所以没收录的域名默认按境外处理，代价是少数没被收录的小众国内站点会绕一圈代理，常用国内应用在上游规则里都有单独分流，不受影响。

还有一处是上游规则表本身的问题。懒人配置引用的 Apple、China、Global、WeChat 四张表是 QuantumultX 格式，里面的 IP 规则没带 `no-resolve`，正是上面说的那种"匹配前先本地解析一次"会发生的地方。这个仓库每天把这四张表原样同步一份到 `rules/` 目录，只给 IP 规则补上 `no-resolve`，其余内容逐行不动，配置改成引用这份镜像。没有换成 blackmatrix7 的 Shadowrocket 同名表，那几张是精简版，条数差一个量级（Global 表完整版约 3.6 万行，精简版 211 行），换了分流覆盖会掉很多。

构建的最后一步会把成品引用的规则集全部下载下来逐条检查，IP 规则只要有一条漏了 `no-resolve`，这次构建直接失败，成品不发出。

系统 DNS 回退是关着的：配置里没有 `fallback-dns-server`，`dns-fallback-system` 也是 false。国内两家 DoH 都连不上时，也不会退回去用运营商的系统 DNS。IPv6 解析同样关着。

## 例外

有几处例外，写在这里，省得用的时候意外：

- **Apple 和 iCloud 走系统 DNS。** 上游在 `[Host]` 里给 `*.apple.com` 和 `*.icloud.com` 设置了 `server:system`，保的是 Apple 本地 CDN 的调度效果，App Store 和 iCloud 同步更稳。这里沿用了上游的设置。这两个范围的域名解析走的是当前网络的系统 DNS，不是上面的 DoH，其他域名不受影响。
- **直连解析全挂时，可以借代理完成解析。** `dns-direct-fallback-proxy = true` 也是上游原值，只在两家 DoH 都不可达时才触发，目的是别让 DNS 故障直接变成断网。正常情况下用不上。
- **少数地址不进隧道。** 上游的 `skip-proxy` 里有局域网地址段、三家银行（建行、农行、邮储）的域名和个别门户站点，这些连接按系统路径直走，是给银行类 App 做的兼容，原样保留。
- **App 自己内置的加密 DNS、iCloud Private Relay 这类通道，任何配置都管不到**，这份也不例外。

## 自动更新

成品文件都是 GitHub Actions 每天自动生成的，流程是固定的：

1. 拉上游最新的 lazy.conf 和 sr_ad_only.conf；
2. 拉 blackmatrix7 四张 QuantumultX 原表，生成补好 `no-resolve` 的镜像；
3. 打 DNS 补丁，生成基础版；再内嵌广告规则，生成去广告版，条数逐条点清；
4. 全量检查 IP 规则的 `no-resolve` 覆盖；
5. 全部通过才提交。任何一步对不上，整轮失败，线上继续用上一版；
6. 提交之后刷新 jsDelivr 缓存，再把六个文件（两份成品加四张镜像）逐个和仓库比对 SHA-256，不一致就重试，三轮还不一致，这次运行标失败。

每天北京时间约 09:07 跑一次。另外每周一约 11:17 有一次健康检查，把上游文件、配置引用的全部规则集、还有自家这六个分发文件逐个访问一遍，哪个打不开就报错。

自动检查管的是格式、条数、规则完整性和分发一致性，上游规则的内容本身没有人工逐条审核。GitHub 定时任务和 CDN 节点同步偶尔有几分钟延迟，属于平台层面的事，校验能发现、能报错，延迟本身消不掉。

## 文件一览

| 文件 | 说明 |
| --- | --- |
| `lazy-dns.conf` | 版本一成品，Actions 自动生成 |
| `lazy-dns-ad.conf` | 版本二成品，Actions 自动生成 |
| `rules/` | 四张规则镜像，Actions 自动生成 |
| `patch.py` | 基础版的构建脚本：DNS 补丁和镜像引用 |
| `patch_ad.py` | 去广告版的构建脚本：补丁加广告规则内嵌 |
| `patch_lists.py` | 镜像的构建脚本：给 IP 规则补 `no-resolve` |
| `check_no_resolve.py` | 构建最后的检查：全量扫 IP 规则 |
| `.github/workflows/sync.yml` | 每日同步 |
| `.github/workflows/health.yml` | 每周健康检查 |

成品是自动生成的，手改的内容会在下一次同步时被覆盖。要调整生成结果，改对应的构建脚本。

## 常见问题

**导入之后连不上？**
先看节点本身是不是可用、全局路由是不是选了「配置」。这个仓库不提供节点。

**DNS 检测网站上，境外域名显示的解析商是 Cloudflare，正常吗？**
正常。这说明域名是在服务器端解析的，检测网站看到的是你节点服务器用的 DNS 上游。显示哪家由服务器决定，想换的话改服务器端的 DNS 设置。

**DNS 检测里出现了系统 DNS，是不是泄露了？**
看是哪个域名触发的。检测用的如果是 Apple 相关的域名，命中的是上面写的例外；检测站自己的探针域名走的是代理那一路，显示的应该是服务器端的解析商。单个结果说明不了全部流量，得结合域名看。

**去广告版里某个 App 不正常？**
切到基础版试一下。基础版正常、去广告版不正常，多半是被广告规则误伤了，两个版本切换不需要改别的设置。

**为什么不用 Shadowrocket 格式的那几张同名规则表？**
条数差太多，前面「DNS 的分工」里写了，用镜像补 `no-resolve` 能保住完整的分流覆盖。

## 上游与版权

规则内容来自下面几个项目，这个仓库做的是 DNS 补丁、规则镜像和自动构建，规则的版权归各自上游所有：

- [Johnshall / Shadowrocket-ADBlock-Rules-Forever](https://github.com/Johnshall/Shadowrocket-ADBlock-Rules-Forever)：懒人配置和去广告规则
- [LOWERTOP / Shadowrocket](https://github.com/LOWERTOP/Shadowrocket)：懒人配置的原始出处
- [blackmatrix7 / ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)：应用分流规则集
- [DuskWander87 / shadowrocket-config](https://github.com/DuskWander87/shadowrocket-config)：DNS 分流思路的参考来源

仅供学习和个人使用。版权方面有问题请联系处理。
