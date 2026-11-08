# shadowrocket-lazy-dns-split

**Shadowrocket 懒人配置 + DNS 国内外分流。** 国内域名本地加密解析，境外域名交给节点远端解析，每天自动同步上游，可选内嵌去广告。

[![每日同步](https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/sync.yml/badge.svg)](https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/sync.yml)
[![每周健康检查](https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/health.yml/badge.svg)](https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/health.yml)

---

## 它解决什么问题

用代理时，你以为「境外网站的查询都交给了节点」，实际上常常不是：

- **境外域名在本地被解析了一次。** 流量分流没问题，但域名解析默认发生在本地。你访问一个境外站，运营商或国内 DNS 已经记下了这条查询。
- **原因藏在规则表里。** 懒人配置引用的 Apple / China / Global / WeChat 四张表是 QuantumultX 格式，里面的 IP 规则不带 `no-resolve`。一个没被任何域名规则命中的域名，会为了去匹配这些 IP 规则**先在本地解析**，节点的远端解析因此被短路。只改 DNS 服务器解决不了这个问题。
- **DNS 查询是明文的。** 默认的系统 DNS 能被运营商看见，也能被篡改。
- **上游一变，你的配置就悄悄坏掉。** 手工维护的补丁，改一次上游就得重来。

## 这个仓库做了什么

在 Johnshall 的懒人配置（`lazy.conf`，无策略组）基础上，**只补 DNS 这一层和自动维护**，不加策略组，分流名单沿用上游。

| 问题 | 做法 |
|---|---|
| 国内域名解析被窥探、被篡改 | 直连域名用阿里 + 腾讯 DoH 本地解析，全程加密 |
| 境外域名在本地留下记录 | 代理域名本地不解析，原样交给节点，在服务器端解析 |
| 规则表里的 IP 规则触发本地解析 | 四张 QX 表自托管镜像，**逐行不动，只给 IP 规则补 `no-resolve`** |
| DNS 全挂时退回运营商 DNS | 删除系统 DNS 回退，`dns-fallback-system = false` |
| IPv6 通道绕过分流 | `ipv6 = false` |
| 上游更新后补丁失效 | 每天自动同步，自动重新打补丁 |
| 构建出错的配置被推给用户 | 带完整性检查，**检查不过不发布**，线上继续用上一版 |

## 优势

- **补的是根因。** 不止换 DNS 服务器，而是找到并修掉了「IP 规则触发本地解析」这个真正的泄露面。
- **不动你熟悉的东西。** 分流名单、分流顺序沿用上游，没有加策略组，上手成本和原版一样。
- **失败时保守。** 构建脚本对每个补丁点都做断言，上游改了格式、条数异常、规则漏了 `no-resolve`，整轮构建直接失败，不会生成半成品。
- **检查覆盖远程内容。** 构建的最后一步会把成品引用的**全部**远程规则集下载下来，逐条扫描 IP 规则是否带 `no-resolve`，只查主配置文本是不够的。
- **免维护。** 每天同步，每周还有一次健康检查，把上游文件、引用的规则集和自家文件逐个访问一遍。
- **两个版本，随时切换。** 平时用去广告版，遇到检测拦截的网站切到基础版。

---

## 快速开始

需要 Shadowrocket 2.2.92 或以上，`direct-dns-server` 在更低版本上不生效。

**选一个版本：**

| 版本 | 内容 |
|---|---|
| 基础版 | 懒人配置 + DNS 国内外分流 |
| 去广告版 | 基础版 + 内嵌广告拦截规则 |

两个版本的 DNS 和分流完全一样，去广告版只是多了一套来自上游 `sr_ad_only.conf` 的广告规则，写在规则最前面，不用单独装模块，也不用开 HTTPS 解密。

| 基础版 | 去广告版 |
|:--:|:--:|
| <img src="qrcode-raw.png" width="190" alt="基础版二维码"> | <img src="qrcode-ad-raw.png" width="190" alt="去广告版二维码"> |

基础版订阅地址：

```text
https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/lazy-dns.conf
```

去广告版订阅地址：

```text
https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/lazy-dns-ad.conf
```

**导入：**

1. 扫码，或复制地址，在 Shadowrocket 里从 URL 导入。
2. 选中导入的配置。
3. 首页「全局路由」选「配置」。
4. 选好节点，连上。
5. 设置里打开配置自动更新。

---

## 它是怎么工作的

### DNS 分工

| 场景 | 解析方式 |
|---|---|
| 命中直连的域名 | 阿里 DoH + 腾讯 DoH，本地加密解析 |
| 命中代理的域名 | 本地不解析，交节点在服务器端解析 |
| 没被任何规则命中的域名 | 按境外处理：走代理，远端解析 |
| 节点自己的域名 | 阿里 DoH + 腾讯 DoH（连接节点前要先知道它的地址） |

两家 DoH 配置为同时使用，一家出问题时解析不中断。查询内容走 HTTPS，本地网络和运营商看不到明文，但两家解析商本身看得到查询记录。

### 核心配置

```ini
dns-server = https://dns.alidns.com/dns-query,https://doh.pub/dns-query
direct-dns-server = https://dns.alidns.com/dns-query,https://doh.pub/dns-query
proxy-dns-server = https://dns.alidns.com/dns-query,https://doh.pub/dns-query
dns-fallback-system = false
dns-direct-system = false
ipv6 = false

GEOIP,CN,DIRECT,no-resolve
FINAL,PROXY,force-remote-dns
```

- `no-resolve`：IP 和地理位置类规则匹配域名时，不许为了匹配先把域名解析一遍。
- `force-remote-dns`：兜底规则，没被命中的域名一律走代理、远端解析。

### 为什么自托管四张规则表

blackmatrix7 提供了 Shadowrocket 格式的同名表，但那是精简版，条数差一个量级（Global 表完整版约 3.6 万行，精简版 211 行），直接换会丢掉大量分流覆盖。

所以仓库每天把四张 QX 原表同步到 `rules/`，**只给 IP 规则补 `,no-resolve`**，其余内容逐行不变。构建脚本会逐行对比输入输出，出现任何其他差异都会失败。

### 自动构建

由 GitHub Actions 每天北京时间约 09:07 执行：

1. 拉取上游最新的 `lazy.conf` 和 `sr_ad_only.conf`。
2. 拉取 blackmatrix7 四张 QX 原表，生成补好 `no-resolve` 的镜像。
3. 打 DNS 补丁生成基础版，再内嵌广告规则生成去广告版，条数逐条点清。
4. 全量检查成品引用的所有规则集，IP 规则必须全部带 `no-resolve`。
5. 全部通过才提交。任何一步对不上，整轮失败，线上继续用上一版。

提交即生效，订阅地址和镜像都直接读仓库文件。每周一约 11:17 另有一次健康检查。

---

## 验证 DNS 是否泄露

导入配置、并在 Shadowrocket 里切换到这份配置文件之后，把下面三个网站都测一遍，结果互相印证。原理：页面里藏着没人访问过的随机子域名，一打开，手机必须现场查询它的 IP，网站借此记下真正执行查询的 DNS 服务器。

| 网站 | 网址 | 说明 |
| --- | --- | --- |
| [BrowserLeaks](https://browserleaks.com/dns) | https://browserleaks.com/dns | 信息最全，列出 DNS 服务器的地址、运营商和国家 |
| [ipleak.net](https://ipleak.net) | https://ipleak.net | 同页显示出口 IP 与 DNS 服务器，方便对照国家 |
| [DNS Leak Test](https://www.dnsleaktest.com) | https://www.dnsleaktest.com | 选 Extended test，探针更多，查得更细 |

**通过标准**：三个站列出的 DNS 服务器应全部位于出口 IP 所在的国家（一般显示为 Cloudflare），不应出现其他区域的，尤其是阿里、腾讯和本地运营商。三个站结果一致，就是没有泄露。

---

## 适用与边界

这份配置**专注于一件事：让境外域名不在本地解析**。使用前请了解：

**默认走代理的范围更大。** 没被规则表收录的域名都按境外处理。常用国内应用在上游规则里都有单独分流，不受影响；少数小众国内站点会绕一圈代理，个别站点可能因为出口 IP 在境外而受限。这是用一点覆盖率换隐私的取舍。

**有意保留的例外：**

- **Apple 和 iCloud 走系统 DNS。** 上游在 `[Host]` 里给 `*.apple.com` 和 `*.icloud.com` 设了 `server:system`，为的是 Apple 本地 CDN 的调度效果，App Store 和 iCloud 同步更稳。
- **两家 DoH 都不可达时，直连域名可借代理解析。** `dns-direct-fallback-proxy = true` 是上游原值，目的是别让 DNS 故障直接变成断网，正常情况下用不上。
- **少数地址不进隧道。** 上游 `skip-proxy` 里的局域网地址段、三家银行（建行、农行、邮储）域名和个别门户站点，按系统路径直走，是给银行类 App 做的兼容。

**这份配置管不到的：**

- DoH 地址本身（`dns.alidns.com`、`doh.pub`）是域名，首次连接前需要先解析，这一步不由本配置控制。
- 节点自己的域名由国内 DoH 解析，解析商能看到它。介意的话，节点直接用 IP。
- App 内置的加密 DNS、iCloud 专用代理（Private Relay）这类通道，任何配置都管不到。
- 浏览器之外的 App、系统服务产生的查询，上面的检测网站测不到。想验证得抓包。

**关于规则内容：** 自动检查管的是格式、条数和规则完整性，上游规则本身没有人工逐条审核。

---

## 手动规则

想让某个域名固定直连或走代理，改仓库根目录的 `custom-rules.txt`，一行一条：

```text
DOMAIN-SUFFIX,example.com,DIRECT
DOMAIN-SUFFIX,example.org,PROXY
```

构建时内嵌进两份成品，位置在全部远程规则表之前，优先级高于上游名单：名单里是直连的可以改成代理，反过来也一样。

成品里默认带着两组：汇丰集团的一批域名直连（`hsbc` 顶级域及各国后缀，含 `hsbc.com.hk`、`vv1865.com` 等），和 `ip.cn` 走代理。不需要就删掉 `custom-rules.txt` 里对应的行。

两条边界：

- 上游写死在配置里的本地功能规则（localhost、局域网 IP 段这类行）排在手动规则前面，不受影响；
- 去广告版里广告规则（REJECT）排在最前面，被广告规则命中的域名，手动指定直连也不会放行。

构建会逐条校验：策略只允许 DIRECT 或 PROXY，同一目标不许写两条，IP 段规则必须带 `no-resolve`。写错一行，整轮构建失败，线上继续用上一版。

---

## 文件

| 文件 | 说明 |
|---|---|
| `lazy-dns.conf` | 基础版成品，Actions 自动生成 |
| `lazy-dns-ad.conf` | 去广告版成品，Actions 自动生成 |
| `rules/` | 四张规则镜像，Actions 自动生成 |
| `custom-rules.txt` | 手动直连/代理规则，构建时内嵌进两份成品 |
| `patch.py` | 基础版构建脚本 |
| `patch_ad.py` | 去广告版构建脚本 |
| `patch_lists.py` | 规则镜像构建脚本 |
| `check_no_resolve.py` | 出厂检查：全量扫描 IP 规则 |
| `.github/workflows/` | `sync.yml` 每日同步，`health.yml` 每周健康检查 |

成品是自动生成的，手改的内容会在下一次同步时被覆盖。要调整生成结果，请改对应的构建脚本。

---

## 常见问题

**导入后连不上？**
先看节点本身是否可用，全局路由是否选了「配置」。

**国内某个网站很慢或打不开？**
多半是没被规则表收录、默认走了代理。在 `custom-rules.txt` 里给它加一条直连规则即可，格式见「手动规则」一节。

**检测站里境外域名显示的是 Cloudflare，正常吗？**
正常。这说明域名是在服务器端解析的，显示的是你的节点服务器所用的 DNS 上游，想换就改服务器端设置。

**检测里出现了系统 DNS，是泄露吗？**
看是哪个域名触发的。Apple 相关域名属于上面写的例外；检测站自己的探针域名走代理那一路，显示的应该是服务器端的解析商。单个结果说明不了全部流量，需要结合域名判断。

**去广告版里某个 App 不正常？**
切到基础版试一下。基础版正常，多半是被广告规则误伤，两个版本切换不需要改别的设置。

**为什么不直接用 Shadowrocket 格式的同名规则表？**
那几张是精简版，条数差一个量级，换了分流覆盖会掉很多。镜像补 `no-resolve` 能保住完整覆盖。

---

## 上游与版权

规则内容来自下面几个项目，本仓库做的是 DNS 补丁、规则镜像和自动构建，规则的版权归各自上游所有：

- [Johnshall / Shadowrocket-ADBlock-Rules-Forever](https://github.com/Johnshall/Shadowrocket-ADBlock-Rules-Forever)：懒人配置和去广告规则
- [LOWERTOP / Shadowrocket](https://github.com/LOWERTOP/Shadowrocket)：懒人配置的原始出处
- [blackmatrix7 / ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)：应用分流规则集
- [DuskWander87 / shadowrocket-config](https://github.com/DuskWander87/shadowrocket-config)：DNS 分流思路的参考来源

仅供学习和个人使用。版权方面有问题请联系处理。
