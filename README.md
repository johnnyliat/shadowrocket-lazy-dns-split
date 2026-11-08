<h1 align="center">Shadowrocket 懒人配置 + DNS 国内外分流</h1>

<p align="center"><strong>防 DNS 泄露配置：懒人配置 + DNS 国内外分流，可选去广告，每日自动更新。</strong></p>

<p align="center">
  <a href="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/sync.yml"><img src="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/sync.yml/badge.svg" alt="每日同步状态"></a>
  <a href="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/health.yml"><img src="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/health.yml/badge.svg" alt="每周健康检查状态"></a>
</p>

导入即用：国内域名用国内 DNS 解析，境外域名交给 VPS 远端解析，没见过的域名一律走代理，不在本地留下查询记录。

---

## 这是什么

一份现成的 Shadowrocket 配置文件，做了三件事：

1. **以「懒人配置」为底**：分流名单和分流顺序沿用上游，开箱即用，不用自己写规则。
2. **多一层 DNS 防泄露**：上游是先解析、再分流的通用设计，本仓库针对「不想在本地留下查询记录」的需求，在它之上再做一层优化，这是本仓库的重点。
3. **提供去广告可选版**：内嵌一套广告拦截规则，不用另装模块，不用开 HTTPS 解密。

## 它解决什么问题：DNS 泄露

用了代理，流量已经绕道了，但**域名解析很可能还在本地发生**：

- 名单里的域名不会泄露：靠域名规则就能判定直连还是代理，全程不需要解析，判定走代理的，域名原样交给 VPS 解析。
- 泄露出在名单外的域名。它们的判定要靠 IP 类规则，而判 IP 归属的前提是先把域名解析成 IP，这一步只能在本地做。等判定结束，哪怕结论是走代理，本地这次查询也已经发生，运营商和国内 DNS 已经看到。**所以走了代理，不等于没泄露。**
- 这条路来自上游的设计本身：先解析、再按解析结果分流——国内的直连、国外的走 VPS 解析，分流准确，对绝大多数人是更合理的方案。懒人配置引用的四张规则表（Apple、China、Global、WeChat）是 QuantumultX 格式，IP 规则不带 `no-resolve` 是那个生态的常规写法。只是对专注隐私、不想泄露的人来说，要省掉的恰恰是「先解析」这一步；本地 DNS 若是明文，查询还能被看到、被篡改。

只改一个 DNS 服务器地址解决不了这些，得从规则层把路堵住。

## 怎么解决的

核心思路只有一句：**该直连的在国内解析，该代理的在 VPS 端解析，不认识的当境外处理。**

| 域名类型 | 怎么处理 |
|---|---|
| 国内域名（命中直连） | 本地解析，用阿里 + 腾讯的加密 DNS（DoH），运营商看不到查询内容 |
| 境外域名（命中代理） | 本地**不解析**，域名原样送出，在 VPS 远端解析 |
| 未知域名（没被任何规则命中） | **一律走代理**，同样在远端解析，不会因为「不认识」而漏到本地 |

落到配置上，是这几处：

```ini
dns-server = https://dns.alidns.com/dns-query,https://doh.pub/dns-query
direct-dns-server = https://dns.alidns.com/dns-query,https://doh.pub/dns-query
proxy-dns-server = https://dns.alidns.com/dns-query,https://doh.pub/dns-query
dns-fallback-system = false
dns-direct-system = false
dns-direct-fallback-proxy = true
allow-dns-all = false
ipv6 = false

GEOIP,CN,DIRECT,no-resolve
FINAL,PROXY,force-remote-dns
```

- **兜底规则改为远端解析**：`FINAL,PROXY,force-remote-dns`，没被命中的域名全部走代理、远端解析。
- **IP 规则不再触发本地解析**：`GEOIP,CN,DIRECT,no-resolve`，以及自托管的规则表镜像（见下），保证匹配规则时不会顺手把域名解析一遍。
- **不退回运营商 DNS**：删掉 `fallback-dns-server` 并设 `dns-fallback-system = false`，国内加密 DNS 全挂了也不会悄悄换成明文的运营商 DNS。
- **关闭 IPv6 解析**：`ipv6 = false`，避免 IPv6 通道绕过分流。
- **冷门查询类型不外发**：`allow-dns-all = false`，A/AAAA 之外的查询（HTTPS、SVCB、TXT 等）直接返回空值，不转发给国内 DNS，免得境外域名换一种查询类型又在本地留痕。
- 两家 DoH 同时使用，一家出问题时解析不中断；查询内容走 HTTPS，但两家解析商本身看得到查询记录。

### 规则表的镜像

这是整个方案里最关键的一步。四张表是 QuantumultX 格式，IP 规则没有 `no-resolve`，在 QuantumultX 里这是常规写法，配合本配置的判定顺序却会触发本地解析。官方 Shadowrocket 格式的同名表是精简版（Global 表只有 211 行，完整版约 3.6 万行），直接换过去会丢掉大量分流覆盖。

所以本仓库每天把四张原表同步一份到 `rules/`：**内容逐行保持一致，只给 IP 规则补上 `no-resolve`**。既保住完整的分流覆盖，又堵住了本地解析。其余 30 张表是 Shadowrocket 格式，IP 规则本就带 `no-resolve`，同样逐行镜像、不做改动——成品引用的全部 34 张表都在本仓库里，客户端取到的就是构建时检查过的那一份，上游的实时变化不会绕过检查直达客户端。构建脚本逐行对比输入输出，出现任何其他差异都会失败。

## 优势

- **专门解决 DNS 泄露**：不是换个 DNS 地址了事，而是国内解析、境外远端解析、未知域名走代理，三层一起堵。
- **开箱即用**：基于懒人配置，导入即可使用，分流名单和顺序与原版一致。
- **分流合理**：国内外分流沿用上游成熟的规则，常用国内外服务都有单独分流。
- **去广告可选**：两个版本随时切换，平时用去广告版，遇到检测拦截的网站切到基础版。
- **自动更新，省心**：每天自动同步上游、重新生成配置，只需在 Shadowrocket 里打开配置自动更新。
- **更新有保障**：生成过程带完整性检查，检查不过就不发布，线上继续用上一版。出厂检查会把成品引用的全部规则表逐条扫描，确认 IP 规则都带 `no-resolve`。

---

## 选择版本

| 版本 | 内容 |
|---|---|
| 基础版 | 懒人配置 + DNS 防泄露 |
| 去广告版 | 基础版 + 内嵌广告拦截规则 |

两个版本的 DNS 和分流规则完全一样，只差广告规则。广告规则来自上游 `sr_ad_only.conf`，写在规则最前面。

| 基础版 | 去广告版 |
|:--:|:--:|
| <img src="qrcode-raw.png" width="200" alt="基础版二维码"> | <img src="qrcode-ad-raw.png" width="200" alt="去广告版二维码"> |

基础版订阅地址：

```text
https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/lazy-dns.conf
```

去广告版订阅地址：

```text
https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns-split/main/lazy-dns-ad.conf
```

## 使用

需要 Shadowrocket 2.2.92 或以上，配置里用到的 `direct-dns-server` 在更低版本上不生效。

1. 扫码，或复制上面的地址，在 Shadowrocket 里从 URL 导入。
2. 选中导入的配置。
3. 首页「全局路由」选「配置」。
4. 回到首页，开启连接。
5. 在设置里打开配置自动更新，之后不用再管。

---

## 测一下有没有泄露

导入配置、并在 Shadowrocket 里切换到这份配置文件之后，把下面三个网站都测一遍，结果互相印证。原理：页面里藏着没人访问过的随机子域名，一打开，手机必须现场查询它的 IP，网站借此记下真正执行查询的 DNS 服务器。

| 网站 | 网址 | 说明 |
| --- | --- | --- |
| [BrowserLeaks](https://browserleaks.com/dns) | https://browserleaks.com/dns | 信息最全，列出 DNS 服务器的地址、运营商和国家 |
| [ipleak.net](https://ipleak.net) | https://ipleak.net | 同页显示出口 IP 与 DNS 服务器，方便对照国家 |
| [DNS Leak Test](https://www.dnsleaktest.com) | https://www.dnsleaktest.com | 选 Extended test，探针更多，查得更细 |

**通过标准**：三个站列出的 DNS 服务器应全部位于出口 IP 所在的国家（一般显示为 Cloudflare），不应出现其他区域的，尤其是阿里、腾讯和本地运营商。三个站结果一致，就是没有泄露。

---

## 工作方式与取舍

**自动更新的流程**（GitHub Actions，每天北京时间约 09:07）：

1. 拉取上游最新的 `lazy.conf` 和 `sr_ad_only.conf`。
2. 同步全部 34 张规则表做镜像，补好 `no-resolve`。
3. 打 DNS 补丁，生成基础版；再内嵌广告规则，生成去广告版。
4. 全量检查引用的规则表（IP 规则必须全部带 `no-resolve`），并做结构安检：成品骨架出现预期之外的变化即停。
5. 全部通过才提交，任何一步不对，整轮失败，线上保持上一版。

另外每周一约 11:17 有一次健康检查，把上游文件、引用的规则表和自家文件逐个访问一遍。

**使用前了解几点：**

- **未知域名默认走代理。** 这是防泄露的关键，也是一个取舍：常用国内应用在上游规则里都有单独分流，不受影响，但少数没被收录的小众国内站点会绕一圈代理，个别站点可能因为出口 IP 在境外而受限。
- **有几处有意保留的例外**：Apple 和 iCloud 域名走系统 DNS（保证 App Store、iCloud 的稳定）；两家加密 DNS 都不可达时，直连域名可借代理解析（避免 DNS 故障直接断网）；上游 `skip-proxy` 里的局域网段、三家银行域名和个别门户站点不发给代理服务器、由 TUN 接口按直连处理（银行 App 兼容）。
- **配置管不到的通道**：DoH 地址本身（`dns.alidns.com`、`doh.pub`）是域名，首次连接前要先解析，这一步不由本配置控制；App 内置的加密 DNS、iCloud 专用代理（Private Relay）等，任何配置都管不到；浏览器之外的 App、系统服务产生的查询，上面的检测网站测不到，想验证得抓包。
- **规则内容来自上游**：自动检查保证的是格式、结构、条数和规则完整性，上游规则本身没有人工逐条审核。
- **成品带有少量手动维护的附加规则**：个别域名指定直连或代理，清单在 `custom-rules.txt` 中公开可查。

---

## 文件

| 文件 | 说明 |
|---|---|
| `lazy-dns.conf` | 基础版成品，自动生成 |
| `lazy-dns-ad.conf` | 去广告版成品，自动生成 |
| `rules/` | 全部 34 张规则表的镜像，自动生成 |
| `mirrors.txt` | 规则表镜像清单 |
| `custom-rules.txt` | 附加规则，构建时内嵌进两份成品 |
| `patch.py` | 基础版构建脚本 |
| `patch_ad.py` | 去广告版构建脚本 |
| `patch_lists.py` | 规则镜像构建脚本 |
| `check_no_resolve.py` | 出厂检查：全量扫描 IP 规则 |
| `LICENSE` | 许可证（本仓库自有代码与文档） |
| `.github/workflows/` | 每日同步和每周健康检查 |

成品是自动生成的，手改的内容会在下一次同步时被覆盖。要调整生成结果，请改对应的构建脚本。

---

## 常见问题

**检测站里境外域名显示 Cloudflare，正常吗？**
正常，说明域名是在服务器端解析的，显示的是服务器端的 DNS 上游。

**检测里出现了系统 DNS，是泄露吗？**
看是哪个域名触发的。Apple 相关域名属于上面写的例外；检测站自己的探针域名走代理那一路，显示的应该是服务器端的解析商。单个结果说明不了全部流量，需要结合域名判断。

**去广告版里某个 App 不正常？**
切到基础版试一下，基础版正常就是被广告规则误伤，切换不需要改别的设置。

---

## 上游与版权

- [Johnshall / Shadowrocket-ADBlock-Rules-Forever](https://github.com/Johnshall/Shadowrocket-ADBlock-Rules-Forever)：懒人配置和去广告规则
- [LOWERTOP / Shadowrocket](https://github.com/LOWERTOP/Shadowrocket)：懒人配置的原始出处
- [blackmatrix7 / ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)：应用分流规则集
- [iab0x00 / ProxyRules](https://github.com/iab0x00/ProxyRules)：AI 分流规则表
- [DuskWander87 / shadowrocket-config](https://github.com/DuskWander87/shadowrocket-config)：DNS 分流思路的参考来源

规则内容的版权归各上游所有，本仓库做的是 DNS 补丁、规则镜像和自动构建。本仓库自有的脚本与文档以 MIT 许可证发布（见 `LICENSE` 文件）；上游配置、规则与数据仍归各自作者所有，遵循其原许可证。仅供学习和个人使用，版权方面有问题请联系处理。
