# shadowrocket-lazy-dns

**懒人配置 + DNS 国内外分流，自动同步、开箱即用。**

这是一个为 Shadowrocket（小火箭）准备的个人配置仓库：分流规则用社区久经考验的「懒人配置」，DNS 按国内外分开处理——国内域名在本地用加密 DNS 解析、直连走国内网络；国外域名不在本地解析、交给代理节点在远端解析。再加上可选的去广告版本，导入一个文件全部搞定，不用自己维护规则。

本仓库的成品文件由 GitHub Actions **每天自动重新生成**：上游规则一更新，这里第二天就跟上；DNS 分流的改动是在生成时自动打上的，永远不会被上游更新冲掉。

---

## 两个版本，选一个就行

| | 纯净版 | 去广告版 |
|---|---|---|
| 文件 | `lazy-dns.conf` | `lazy-dns-ad.conf` |
| 内容 | 懒人分流 + DNS 分流 | 与纯净版完全相同，再加全量去广告规则 |
| 去广告 | 无 | 约 5.3 万条，规则层拦截，不用开 MITM、不用装证书 |
| 适合 | 只想要干净分流的人 | 想顺手把 App 内广告、追踪域名一起干掉的人 |

两个版本不要同时导入，任选其一；随时可以换，设置互不影响。

---

## 快速导入

先在 Shadowrocket 首页添加并选中你自己的节点（本仓库的配置**不包含任何节点**），然后导入：

### 版本一 · 纯净版

- jsDelivr（国内可直达，有数小时缓存）：`https://cdn.jsdelivr.net/gh/johnnyliat/shadowrocket-lazy-dns@main/lazy-dns.conf`
- Raw（无缓存，更新最及时）：`https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns/main/lazy-dns.conf`

![扫码导入-纯净版](qrcode.png)

### 版本二 · 去广告版

- jsDelivr（国内可直达，有数小时缓存）：`https://cdn.jsdelivr.net/gh/johnnyliat/shadowrocket-lazy-dns@main/lazy-dns-ad.conf`
- Raw（无缓存，更新最及时）：`https://raw.githubusercontent.com/johnnyliat/shadowrocket-lazy-dns/main/lazy-dns-ad.conf`

![扫码导入-去广告版](qrcode-ad.png)

扫码方法：在 Shadowrocket 里点右上角的扫码图标，对准上面的二维码即可（二维码内容是对应的 jsDelivr 地址）。

导入后记得：全局路由选「配置」，并在「设置 → 更新」里给该配置开启自动更新，之后它会自己拉新版。

---

## DNS 分流是怎么工作的

普通配置有个隐患：所有域名都用本地 DNS 解析。国外域名在本地解析，既可能被污染、拿到错误 IP，又等于把浏览记录暴露给本地解析方。本仓库按 DuskWander87/shadowrocket-config 的思路处理，但把本地解析从明文 UDP 换成了加密 DoH：

- **国内域名（直连）**：命中直连规则的域名，用阿里 DoH 与腾讯 DoH 在本地解析——`dns-server`、`direct-dns-server`、`proxy-dns-server` 三处都是 `https://dns.alidns.com/dns-query` + `https://doh.pub/dns-query`。全程 TLS 加密，运营商看不到查了什么，伪造应答会被证书校验识破；拿到的是国内 CDN 就近 IP，直连速度不受影响。
- **国外域名（代理）**：规则层保证它们**不在本地解析**——IP 类规则带 `no-resolve`、`GEOIP,CN,DIRECT,no-resolve`、最后 `FINAL,PROXY,force-remote-dns` 把域名原样交给节点，由节点在 VPS 远端解析。本地不发生查询，自然无从泄露、无从污染。
- **没有备用回退**：已删除 `fallback-dns-server` 并设置 `dns-fallback-system = false`。主解析出问题时宁可失败，也绝不回退到系统（运营商）DNS——这是防泄露的 fail-closed 设计。双 DoH 本身互为冗余，两家同时不可用的概率极低。
- **IPv6 关闭**：`ipv6 = false`，防止 IPv6 通道绕过整套分流。
- **一处有意保留的例外**：`[Host]` 中 `*.apple.com`、`*.icloud.com` 的 `server:system` 是上游的兼容设计（让 Apple 服务用运营商 DNS 拿到最准的本地 CDN 节点），予以保留。详见下方常见问题。

---

## 自动更新是怎么实现的

1. 每天北京时间约 09:07，GitHub Actions 自动运行（仓库 Actions 页也可以手动触发）。
2. 它下载上游的 `lazy.conf` 和 `sr_ad_only.conf` 两个最新文件，分别用 `patch.py`、`patch_ad.py` 重新生成两个成品。
3. 两个脚本的每处修改都带断言，生成后还要过一遍自检（DNS 参数齐全、去广告条数对得上、FINAL 在最后等）。任何一项对不上，任务直接失败并报警，**不会**把有问题的配置提交上来。
4. 内容有变化才提交。所以你订阅到的，永远是「上游最新版 + 本仓库的 DNS 设计」，两边互不耽误。

## 常见问题

**问：导入后连不上 / 没有网？**
答：先检查首页有没有添加并选中你自己的可用节点——本仓库只提供规则配置，不含节点。再确认全局路由选的是「配置」而不是「直连」。

**问：去广告版会不会误杀？**
答：去广告名单来自上游 `sr_ad_only`，覆盖面广，个别 App 的某个功能域名被误拦的概率存在。遇到某个 App 异常，先换回纯净版对比确认；确认是误杀，把域名发给上游项目反馈即可，本仓库每天同步上游的修正。

**问：为什么没被任何规则命中的小众国内网站走了代理？**
答：这是防泄露设计的固有取舍：判断「是不是国内 IP」需要先解析，而解析本身就是要避免的动作，所以未被域名规则收录的域名一律先走代理远端解析（`GEOIP` 的 `no-resolve` 与 `FINAL` 的共同作用）。日常常用服务都有专门的直连域名规则，不受影响。

**问：`[Host]` 里 Apple/iCloud 的 `server:system` 为什么保留？**
答：这是上游懒人配置有意的设计：Apple 的服务（App Store、iCloud、推送）在国内有本地 CDN，用系统（运营商）DNS 往往能拿到最准的就近节点，解析最稳最快，所以上游特意让 `*.apple.com`、`*.icloud.com` 继续走系统 DNS，本仓库原样保留。代价是这两组域名的查询是运营商可见的明文解析——它们仍是国内直连、解析结果仍是国内 IP，不影响国内外分流本身，只是全配置里唯一保留明文的例外。如果你更看重纯粹性，可以在 Shadowrocket 的「配置 → 主机」里把这两行删掉，Apple 域名就会改走上面的国内 DoH。

**问：对 Shadowrocket 版本有要求吗？**
答：需要 **2.2.92 或更高版本**。直连专用 DNS 字段 `direct-dns-server` 是这个版本才转正的功能，低版本会忽略它、分流效果打折。

## 仓库文件说明

| 文件 | 作用 |
|---|---|
| `lazy-dns.conf` | 成品一：纯净版（自动生成，请勿手改，手改也会被下次同步覆盖） |
| `lazy-dns-ad.conf` | 成品二：去广告版（同上） |
| `patch.py` | 纯净版构建脚本：上游 lazy.conf + DNS 补丁 |
| `patch_ad.py` | 去广告版构建脚本：DNS 补丁 + 去广告规则内嵌 |
| `.github/workflows/sync.yml` | 每日定时任务：拉上游 → 构建 → 自检 → 提交 |
| `qrcode.png` / `qrcode-ad.png` | 两个版本的扫码导入二维码 |

## 来源与声明

- 懒人配置与去广告规则：Johnshall 的 Shadowrocket-ADBlock-Rules-Forever（同步自 LOWERTOP/Shadowrocket，规则基于 blackmatrix7/ios_rule_script 生成），版权与署名归上游所有。
- DNS 分流思路：借鉴 DuskWander87/shadowrocket-config，本地解析改为加密 DoH 的部分为本仓库的设计。
- 本仓库为个人学习与自用，仅对上游做机械化的同步、合并与 DNS 参数修改。
