<h1 align="center">Shadowrocket 懒人配置 + DNS 国内外分流</h1>

<p align="center"><strong>防 DNS 泄露配置：懒人配置 + DNS 国内外分流，可选去广告，每日自动更新。</strong></p>

<p align="center">
  <a href="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/sync.yml"><img src="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/sync.yml/badge.svg" alt="每日同步状态"></a>
  <a href="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/health.yml"><img src="https://github.com/johnnyliat/shadowrocket-lazy-dns-split/actions/workflows/health.yml/badge.svg" alt="每周健康检查状态"></a>
</p>

导入即用：国内域名用国内 DNS 解析，境外域名交给 VPS 远端解析，没见过的域名先经代理查一下 IP 再决定，不在本地留下查询记录。

---

## 选择版本

| 版本 | 内容 |
|---|---|
| 基础版 | 懒人配置 + DNS 防泄露 |
| 去广告版 | 基础版 + 内嵌广告拦截规则 |

两个版本的 DNS 和分流规则完全一样，只差广告规则。广告规则来自上游 `sr_ad_only.conf` 加一份去广告补充，内嵌于手动规则之后、全部名单之前。

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

**通过标准**：三个站列出的 DNS 服务器应全部位于出口 IP 所在的国家（一般显示为 Cloudflare），不应出现其他区域的，尤其是阿里、腾讯和本地运营商。三个站结果一致，说明浏览器里这路查询没有泄露；浏览器之外、App 与系统服务的查询，检测站测不到（见「配置管不到的通道」）。

---

## 这是什么

一份现成的 Shadowrocket 配置文件，做了三件事：

1. **以「懒人配置」为底**：分流名单和分流顺序沿用上游，另加每天编译的直连补集与代理补集（见「补集」），开箱即用，不用自己写规则。
2. **多一层 DNS 防泄露**：上游是先解析、再分流的通用设计，本仓库针对「不想在本地留下查询记录」的需求，在它之上再做一层优化，这是本仓库的重点。
3. **提供去广告可选版**：内嵌一套广告拦截规则，不用另装模块，不用开 HTTPS 解密。

## 它解决什么问题：DNS 泄露

用了代理，流量已经绕道了，但**域名解析很可能还在本地发生**：

- 名单里的域名不会泄露：靠域名规则就能判定直连还是代理，全程不需要解析，判定走代理的，域名原样交给 VPS 解析。
- 泄露出在名单外的域名。它们的判定要靠 IP 类规则，而判 IP 归属的前提是先把域名解析成 IP，这一步只能在本地做。等判定结束，哪怕结论是走代理，本地这次查询也已经发生，运营商和国内 DNS 已经看到。**所以走了代理，不等于没泄露。**
- 这条路来自上游的设计本身：先解析、再按解析结果分流——国内的直连、国外的走 VPS 解析，分流准确，对绝大多数人是更合理的方案。懒人配置引用的四张规则表（Apple、China、Global、WeChat）是 QuantumultX 格式，IP 规则不带 `no-resolve` 是那个生态的常规写法。只是对专注隐私、不想泄露的人来说，问题恰恰出在「先解析」发生在本地；本地 DNS 若是明文，查询还能被看到、被篡改。

只改一个 DNS 服务器地址解决不了这些，得让解析发生在对的地方：直连的在国内解析，代理的在 VPS 端解析，拿不准的在代理通道里解析。

## 怎么解决的

核心思路只有一句：**该直连的在国内解析，该代理的在 VPS 端解析，不认识的经代理查一下再判。**

| 域名类型 | 怎么处理 |
|---|---|
| 国内域名（命中直连） | 本地解析，用阿里 + 腾讯的加密 DNS（DoH），运营商看不到查询内容 |
| 境外域名（命中代理） | 本地**不解析**，域名原样送出，在 VPS 远端解析 |
| 未知域名（没被任何规则命中） | 先经代理向国外加密 DNS 解析，拿到 IP 再判：国内 IP 直连，国外 IP 走代理；解析全程在代理通道里，本地不留查询记录 |

落到配置上，是这几处：

```ini
dns-server = https://cloudflare-dns.com/dns-query#proxy,https://dns.google/dns-query#proxy
direct-dns-server = https://dns.alidns.com/dns-query,https://doh.pub/dns-query
proxy-dns-server = https://dns.alidns.com/dns-query,https://doh.pub/dns-query
fallback-dns-server = https://dns.alidns.com/dns-query,https://doh.pub/dns-query
dns-fallback-system = false
dns-direct-system = false
dns-direct-fallback-proxy = true
allow-dns-all = false
ipv6 = false
private-ip-answer = true

GEOIP,CN,DIRECT
FINAL,PROXY,force-remote-dns
```

- **兜底规则改为远端解析**：`FINAL,PROXY,force-remote-dns`，预解析后仍判国外的域名走代理、远端解析。
- **未知域名的预解析在代理通道里做**：`dns-server` 的两个国外 DoH 带 `#proxy`，查询经代理发出；`GEOIP,CN,DIRECT` 不带 `no-resolve`，拿到 IP 才按归属分流。两者配对出现，缺一个构建就失败。其余 IP 规则（自托管的规则表镜像，见下）全部带 `no-resolve`，不会顺手把域名解析一遍。
- **不退回运营商 DNS**：`fallback-dns-server` 写明国内双 DoH，并设 `dns-fallback-system = false`；主查询失败时回落到加密的国内 DNS，不会悄悄换成明文的运营商 DNS。
- **关闭 IPv6 解析**：`ipv6 = false`，避免 IPv6 通道绕过分流。
- **冷门查询类型不外发**：`allow-dns-all = false`，A/AAAA 之外的查询（HTTPS、SVCB、TXT 等）直接返回空值，不转发给国内 DNS，免得境外域名换一种查询类型又在本地留痕。
- **直连域名的私有地址照常返回**：`private-ip-answer = true`，局域网内的直连域名能正常解析到内网地址。
- 两对 DoH 各自同时使用，一家出问题时解析不中断；查询内容走 HTTPS，但解析商本身看得到经它查询的记录。

### 规则表的镜像

这是整个方案里最关键的一步。四张表是 QuantumultX 格式，IP 规则没有 `no-resolve`，在 QuantumultX 里这是常规写法，配合本配置的判定顺序却会触发本地解析。官方 Shadowrocket 格式的同名表是精简版（Global 表只有 211 行，完整版约 3.6 万行），直接换过去会丢掉大量分流覆盖。

所以本仓库每天把四张原表同步一份到 `rules/`：**内容逐行保持一致，只给 IP 规则补上 `no-resolve`**。既保住完整的分流覆盖，又堵住了本地解析。其余 30 张表是 Shadowrocket 格式，IP 规则本就带 `no-resolve`，同样逐行镜像、不做改动——成品引用的全部 34 张表都在本仓库里，客户端取到的就是构建时检查过的那一份，上游的实时变化不会绕过检查直达客户端。构建脚本逐行对比输入输出，出现任何其他差异都会失败。

### 补集

上游直连名单覆盖有限，大量常见国内服务不在名单里，只能走代理绕一圈；反过来，一些外国服务解析出的 IP 在国内，GEOIP 会误判直连。仓库每天编译三张补集表，只补没有的：

- **直连补集** `rules/SupplementA.list`：来源是 v2fly/domain-list-community 的 `data/cn`（中国域名全集）、ACL4SSR 的五张精选直连表，加上 DuskWander87 的 `ChinaDirect.list`（国内厂商域名补充）。补进来的每条域名都经过双边校验：国内来源主张直连，并且没有任何一方反对——已被直连表、代理表、去广告表、手动规则覆盖的，一律不要；只要有代理表的证据就判回代理，没有例外。
- **代理补集** `rules/SupplementP.list`：来源是 ACL4SSR 的三张代理表（ProxyGFWlist、ProxyLite、ProxyMedia）加上 GFWList。补进来的域名同样只取未被任何已有判定覆盖的；它排在全部直连判定之后、GEOIP 之前，只替 GEOIP 拿主意，翻不了已有的直连判定。
- **去广告补充** `rules/SupplementAd.list`：来源是 DuskWander87 的 `Reject.list`（广告聚合与追踪 SDK 域名补充），与上游广告规则一起内嵌于去广告版；已被已有广告规则或手动规则覆盖的不要。

已有代理判定不被补集翻转：凡是有代理证据的域名，补集一律不收；补集父域下的代理子域仍按顺序优先（代理表排在补集之前），构建日志里有父子重叠报告。顶级域级的单标签条目只接受已审核的品牌/中文顶级域清单。补集只含域名规则，没有 IP 规则、关键字规则和正则。数量也有守卫：直连补集与上一版相比波动超过 40%，或低于下限，整轮构建失败、不出厂；代理补集与去广告补充只设上限。每次构建打印补集相对上一版的增减条目。

### 分流顺序

规则按先命中先赢，共九组，组内上游相对顺序不动：

1. 手动直连、手动代理（`custom-rules.txt`，权力最高）
2. 去广告规则（仅去广告版）
3. 上游本地规则（3 条，全部走代理）
4. 代理规则表（21 张，上游原版顺序）
5. 直连规则表（13 张，上游原版顺序，Global/China 兜底表居尾）
6. A 层补集（直连）
7. P 层补集（代理，排在直连判定之后、GEOIP 之前）
8. `GEOIP,CN,DIRECT`（预解析经代理通道）
9. `FINAL,PROXY,force-remote-dns`

顺序按「谁说了算」排，不按直连/代理分组：越是明确的指定、证据越硬的判定，越靠前；批量名单、IP 推断、兜底依次靠后。

## 优势

- **专门解决 DNS 泄露**：不是换个 DNS 地址了事，而是国内解析、境外远端解析、未知域名在代理通道里预解析，三层一起堵。
- **开箱即用**：基于懒人配置，导入即可使用，分流名单沿用上游原版，另加双边校验过的直连补集与代理补集。
- **分流合理**：国内外分流沿用上游成熟的规则，常用国内外服务都有单独分流。
- **去广告可选**：两个版本随时切换，平时用去广告版，遇到检测拦截的网站切到基础版。
- **自动更新，省心**：每天自动同步上游、重新生成配置，只需在 Shadowrocket 里打开配置自动更新。
- **更新有保障**：生成过程带完整性检查，检查不过就不发布，线上继续用上一版。出厂检查会把成品引用的全部规则表逐条扫描，确认 IP 规则都带 `no-resolve`；独立验证每天从成品反推关键域名的实际判定（银行、支付、推送、国内外大站），漂移即停发；补集数量波动超限也会停发。

---

## 工作方式与取舍

**自动更新的流程**（GitHub Actions，每天北京时间约 09:07）：

1. 拉取上游最新的 `lazy.conf` 和 `sr_ad_only.conf`。
2. 先跑单元测试（规则语法、补集守卫、GFWList 转换、验证引擎），不过就停。
3. 同步全部 34 张规则表做镜像，补好 `no-resolve`；编译直连补集、代理补集与去广告补充三张表（双边校验与数量守卫见上）。
4. 打 DNS 补丁、排九组顺序，生成基础版；再内嵌广告规则，生成去广告版。
5. 全量检查引用的规则表（IP 规则必须全部带 `no-resolve`，按逗号参数精确判定），并做结构安检与位置断言；独立验证从成品反推分流语义（见上）。成品骨架、规则顺序或关键判定出现预期之外的变化即停。
6. 全部通过才写构建记录 `manifest.json` 并提交，任何一步不对，整轮失败，线上保持上一版。

另外每周一约 11:17 有一次健康检查，把上游文件、引用的规则表和自家文件逐个访问一遍，并核对线上成品的 SHA256 是否与构建记录一致、内容里是否含有关键 DNS 行。

**使用前了解几点：**

- **未知域名要多走一步预解析。** 没被规则命中的域名，先经代理查 IP 再分流：代理不可用时，这类域名打不开（直连域名不受影响）；个别小众国内站点如果解析结果不准，仍可能绕一圈代理。
- **有几处有意保留的例外**：Apple 和 iCloud 域名走系统 DNS（保证 App Store、iCloud 的稳定）；两家加密 DNS 都不可达时，直连域名可借代理解析（避免 DNS 故障直接断网）；上游 `skip-proxy` 里的局域网段、三家银行域名和个别门户站点不发给代理服务器、由 TUN 接口按直连处理（银行 App 兼容）。
- **配置管不到的通道**：DoH 地址本身（`dns.alidns.com`、`doh.pub`）是域名，首次连接前要先解析，这一步不由本配置控制；App 内置的加密 DNS、iCloud 专用代理（Private Relay）等，任何配置都管不到；浏览器之外的 App、系统服务产生的查询，上面的检测网站测不到，想验证得抓包。
- **规则内容来自上游**：自动检查保证的是格式、结构、条数、规则完整性与关键判定的语义，上游规则本身没有人工逐条审核。
- **成品带有少量手动维护的附加规则**：个别域名指定直连或代理，清单在 `custom-rules.txt` 中公开可查。

---

## 文件

| 文件 | 说明 |
|---|---|
| `lazy-dns.conf` | 基础版成品，自动生成 |
| `lazy-dns-ad.conf` | 去广告版成品，自动生成 |
| `manifest.json` | 构建记录：输入、脚本、成品与规则表的 SHA256，自动生成 |
| `rules/` | 全部 34 张规则表镜像 + 3 张补集表，自动生成 |
| `mirrors.txt` | 规则表镜像清单 |
| `custom-rules.txt` | 附加规则，构建时内嵌进两份成品 |
| `qrcode-raw.png`、`qrcode-ad-raw.png` | 两个版本的订阅二维码 |
| `build_common.py` | 构建脚本共享逻辑（DNS 补丁、手动规则校验、结构安检、严格规则语法） |
| `patch.py` | 基础版构建脚本 |
| `patch_ad.py` | 去广告版构建脚本 |
| `patch_lists.py` | 规则镜像构建脚本 |
| `compile_supplement.py` | 补集编译脚本（直连、代理、去广告补充） |
| `check_no_resolve.py` | 出厂检查：全量扫描 IP 规则 |
| `verify_routing.py` | 独立验证：从成品反推分流语义 |
| `build_manifest.py` | 构建记录生成脚本 |
| `tests/` | 单元测试（规则语法、补集守卫、GFWList 转换、验证引擎） |
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
- [v2fly / domain-list-community](https://github.com/v2fly/domain-list-community)：直连补集的国内域名数据
- [ACL4SSR / ACL4SSR](https://github.com/ACL4SSR/ACL4SSR)：补集的精选直连表与代理表
- [gfwlist / gfwlist](https://github.com/gfwlist/gfwlist)：代理补集的被墙域名数据
- [DuskWander87 / shadowrocket-config](https://github.com/DuskWander87/shadowrocket-config)：DNS 分流思路参考，直连补集与去广告补充的数据来源之一

规则内容的版权归各上游所有，本仓库做的是 DNS 补丁、规则镜像、补集编译和自动构建。本仓库自有的脚本与文档以 MIT 许可证发布（见 `LICENSE` 文件）；上游配置、规则与数据仍归各自作者所有，遵循其原许可证。仅供学习和个人使用，版权方面有问题请联系处理。
