# 四厂商公开配置与 LSDB 样本

本目录保存 2026-10-28 从四家厂商官方资料提取的 **38 份 CLI 样本**。CLI 地址、协议 ID 和度量保留来源值，文档中的不一致、缺失对端、摘要及截断也保留。这里是公开文档样例，不是客户生产采集，也不是同一个网络。

| 厂商 | 配置 | OSPF / IS-IS LSDB | 路由 / 邻居 / 名称验证输出 |
| --- | ---: | ---: | ---: |
| Cisco | 6 | 2 | 1 |
| Huawei | 4 | 7 | 0 |
| Juniper | 5 | 3 | 1 |
| Nokia | 5 | 3 | 1 |

[manifest.json](manifest.json) 逐文件记录来源 URL、提取位置/方式、内容 SHA-256、采集日期、平台、完整性和配对限制。HTML `<pre>` 原文可直接下载时还保留页面 SHA-256；部分 Cisco / Huawei 页面直接下载返回 403，使用搜索/网页工具的公开缓存正文，并在 `extraction.method` 中标明，未把缓存正文冒充原始 HTML。

只有文档自己的同一个配置案例才可配对。`ospf-r1/r2` 和 `isis-r1/r2` 等来自不同案例，文件名中的 R1 不代表同一台设备；不同文档的 LSDB 不得拼成一个“完整网络”。

## 自验证

在仓库根目录运行：

```sh
.venv/bin/python -m scripts.validate_public_network --report /tmp/iptopo-public-validation.json
.venv/bin/pytest -q backend/tests/test_public_network.py
```

验证不需要网络，不新增依赖。当前为 **123 项检查通过**：38 项来源文件完整性、85 项配置地址/拓扑、四厂商原生协议字段、缺失数据保护、异厂商格式保护和已公布路由开销对照。

- 17 份包含可核对地址的配置按来源独立核对；另外 3 份 Nokia 协议/策略上下文片段不具有完整接口地址，保留供后续适配，未计为完整配置解析成功。
- Cisco IS-IS、Juniper IS-IS/OSPF 配置案例分别重建 2、1、1 条同网段连接。
- Cisco 官方 OSPF Area 0 的两条记录通过独立的验证用读取函数转成中立 SPF 输入；`1.1.1.1 → 6.0.0.0/8` 和 `2.2.2.2 → 4.0.0.0/8` 均为 **74**，与同页公布的 `show ip route ospf` 一致。这没有增加产品级 Cisco LSDB 适配器。
- 华为缺少对端、已老化、只有摘要和内容不一致的公开输出均保留诊断。它们不是完整采集，不能据此宣称网络最短路径验证成功。
- 四厂商原生 LSDB 适配器均已启用，并通过这些原文回归。Cisco 产品适配器与独立参考读取器分别复算两个公开 Area 0 路由开销，均为 74。配置仿真另有九种方言和六设备混合组网验收；公开片段仍按实际完整性保留诊断。

## 文档提取和输入准备

HTML 实体解码、换行统一及网页引用标记移除在清单中记录，不改变 CLI 地址或度量。

两种额外准备只在验证脚本中进行，原始样本文件保持不变：

1. Cisco 2005 年 IS-IS 文档把 `interface` / `router` 与子命令渲染到相同缩进。脚本依据这些明确的子模式声明恢复缩进，CLI token 不变；地址断言来自官方表述，并不从解析器输出生成。
2. Nokia `ospf-router-context.cfg` 的提示符明确处于 `config>router`，原文省略祖先层。脚本补充 `configure/router` 外层，只验证其中原有两条地址，没有把其他文档的 OSPF / IS-IS 片段合入同一设备。补充外层会使准备后文本的行号产生偏移，因此这些解析来源定位属于准备后的输入。

Cisco 官方示例较旧（2005 年，IS-IS 为 IOS 12.1(5)T9），不能代表所有当前 IOS XE、IOS XR 或 NX-OS 版本。Nokia 采集包含 SAS/SAR 文档和 Classic/MD-CLI 片段，需要按具体格式适配；Huawei 完整配置样本是 IPv6 IS-IS，用于接口和地址解析，不表示已支持 IPv6 SPF。

## 主要来源

- Huawei：[OSPF Router LSA](https://info.support.huawei.com/network/ptmngsys/Web/tsrev_s/en/content/s/46_edesk_ospf_cannot_learn/edesk_ospf_cannot_learn_edesk005.html)、[Router LSA 故障样例](https://info.support.huawei.com/network/ptmngsys/Web/tsrev_s/ru/content/s/51_edesk_ospf_flapping/edesk_ospf_flapping_edesk001.html)、[Network LSA](https://info.support.huawei.com/network/ptmngsys/Web/tsrev_ne/cn/content/ne/22_edesk_ne_ospf_routing_oscillation_failure/edesk_ne_ospf_routing_oscillation_failure_edesk003.html)、[IS-IS LSDB](https://info.support.huawei.com/hedex/api/pages/EDOC1100331917/AZM1014P/06/resources/dc/display_isis_lsdb.html)、[扩展前缀属性](https://info.support.huawei.com/hedex/api/pages/EDOC1100363264/AEN0403J/05/resources/command/yunshan/DISPLAY-ISIS-LSDB%28ISISCOMMOM%29.html)、[IPv6 IS-IS 配置案例](https://info.support.huawei.com/enterprise/en/doc/EDOC1100389673/9ec00e90/example-for-configuring-basic-ipv6-is-is-functions)；
- Cisco：[OSPF 配置、LSDB 与路由表](https://www.cisco.com/c/en/us/support/docs/ip/open-shortest-path-first-ospf/47864-ospfdb5.html)、[IS-IS 配置与 LSDB](https://www.cisco.com/c/en/us/support/docs/ip/integrated-intermediate-system-to-intermediate-system-is-is/13795-is-is-ip-config.html)；
- Juniper：[IS-IS 配置](https://www.juniper.net/documentation/us/en/software/junos/is-is/topics/example/routing-protocol-is-is-security-configuring-cli.html)、[OSPF 配置](https://www.juniper.net/documentation/us/en/software/junos/ospf/topics/topic-map/configuring-ospf-areas.html)、[OSPF LSDB](https://www.juniper.net/documentation/us/en/software/junos/cli-reference/topics/ref/command/show-ospf-database.html)、[IS-IS LSDB](https://www.juniper.net/documentation/us/en/software/junos/cli-reference/topics/ref/command/show-isis-database.html)；
- Nokia：[OSPF 配置及 LSDB](https://documentation.nokia.com/sas/23-9/books/k-routing-protocols/ospf.html)、[IS-IS 配置及 LSDB](https://documentation.nokia.com/sas/23-3/books/k-routing-protocols/is-is.html)、[基本路由器配置](https://documentation.nokia.com/sar/25-4-1/books/router-config/ip-router-configuration.html)、[MD-CLI OSPF 片段](https://documentation.nokia.com/acg/24-7-2/books/router-configuration-md/m1078-lfa-policies-md-cli.html)。
