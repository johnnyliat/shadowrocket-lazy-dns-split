# shadowrocket-lazy-dns

懒人配置（无策略组版）+ **DNS 国内外分流** 的自动同步仓库。

- 上游：<https://johnshall.github.io/Shadowrocket-ADBlock-Rules-Forever/lazy.conf>（懒人配置，同步自 LOWERTOP/Shadowrocket，版权与署名归上游所有）
- 本仓库由 GitHub Actions **每天自动**拉取上游最新版，用 `patch.py` 只打 DNS 补丁后生成 `lazy-dns.conf`，其余内容与上游逐字一致。上游一更新，这边自动跟上，DNS 改动不会被冲掉。

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

导入后可在「设置 → 更新」里为该配置开启自动更新。

## 工作原理

1. `.github/workflows/sync.yml`：每天北京时间约 09:07 触发（也可手动 Run workflow）
2. 下载上游 `lazy.conf` → `python3 patch.py upstream-lazy.conf lazy-dns.conf`
3. `patch.py` 每处修改都有断言与生成后自检：上游若改了格式，任务会**失败报警**而不是提交错误配置
4. 内容有变化才提交 `lazy-dns.conf`

## 免责声明

个人学习与自用。上游配置的版权归其作者所有；本仓库仅对其做 DNS 参数层面的机械修改并注明出处。
