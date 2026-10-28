# 默认配置与转发行为

适配器读取完整配置时，未出现某条命令不等于该功能关闭，也不等于所有厂商使用相同默认值。默认规则在 [defaults.py](../backend/app/routing/adapters/defaults.py) 和各原生配置适配器中；SPF 只读取中立事实。实例保存 defaults_profile，开销区分 explicit / profile_default / unknown。显式接口值优先于继承值和默认值，已知的 no / deactivate / shutdown 恢复或停用行为另行处理。

## 已建模的差异

| 范围 | 当前规则 |
| --- | --- |
| Huawei VRP | OSPF Loopback 开销 0；IS-IS 默认 L1-L2、narrow、普通接口 10 / Loopback 0；未显式给定 ECMP 上限时保持未知 |
| Cisco IOS / XE | OSPF Loopback 开销 1，默认发布 /32；IS-IS 普通接口默认 10，passive 接口默认 0，且该 0 优先于进程全局 metric，接口显式 metric 仍最高 |
| Cisco IOS XR | OSPF 进程→VRF→Area→接口继承；未配置 Dead 时为有效 Hello 的四倍；IS-IS 默认 L1-L2、narrow、普通接口 10 / Loopback 0；OSPF Loopback 固定默认成本证据不足时保持未知 |
| Cisco NX-OS | feature ospf / feature isis 必须启用；默认宽度量；普通接口和 Loopback 的隐式 IS-IS 成本存在平台/版本差异，通用 profile 保持未知，不能统一写成 10 |
| Junos | OSPF lo0 默认开销 0；Hello / Dead 默认 10 / 40，修改 Hello 不自动修改默认 Dead；IS-IS 默认 L1-L2、双编码，普通接口 10 / lo0 0；默认双编码的单链路度量上限 63，相应 Level 的 wide-metrics-only 才放宽；有效 family iso 是 IS-IS 接口前提 |
| Nokia SR OS Classic | OSPF 创建后默认启用，IS-IS 默认关闭；system 的 OSPF / IS-IS 默认成本 0；其他额外 Loopback 没有明确证据时保持未知；普通 IS-IS 默认 10 |
| Nokia SR OS MD-CLI | OSPF / IS-IS 实例的 admin-state 默认 disable；协议接口 admin-state 默认 enable；IS-IS 默认 L1-L2、双编码、IPv4 enabled；IPv4 关闭独立于管理状态 |
| Nokia 身份与 ECMP | OSPF Router ID 优先协议值→router router-id→system IPv4；IS-IS System ID 优先显式值→IS-IS router-id→router router-id→system IPv4。Router ECMP 默认 1；有效上限取 router cap 与显式 max-ecmp-routes 的较小值，版本相关上限不能假设 |

OSPF 广播/P2P 的默认 Hello / Dead 通常为 10 / 40，但 Cisco 在只改 Hello 时按四倍推导 Dead，Junos / Nokia 仍采用各自默认 Dead。Nokia Dead 小于 Hello 两倍的无效配置会给出阻断诊断。NBMA、P2MP 等不在当前有效仿真范围，不能套用广播默认值；例如 Serial 接口配置 Frame Relay 后，不能仅凭 Serial 名称继续视为 HDLC P2P。

普通 OSPF 接口开销依赖参考带宽和实际接口带宽。缺少可靠带宽证据时保持 unknown，允许用户在独立 Scenario 中给定假设。接口名称、配置地址、人工确认均不证明协商速率或运行态。动态 Router ID 选择依赖运行条件时也保留未知，不假定某个配置地址就是当前 Router ID。

## 未建模语义的默认处理

路径仿真默认忽略尚未建模的协议 / 接口 / Area 扩展语义，继续按已解析的基础 IPv4 图计算，并返回 `ignored=true`、`blocking=false` 的来源警告。认证未建模时假设认证与候选邻接正常；策略、重分发、汇总、Stub / NSSA、Overload、FRR、TE、Flex-Algo 和 SPF / LSP 调度定时器等效果不参与静态 SPF。此行为也适用于旧快照的通用 `unknown_*` / `unsupported_*` 命令类诊断，不要求重导入；仅限 [明确列出的语义问题代码](ROUTING.md#忽略语义与事实不足)，不按整个前缀放宽。

忽略语义不会把缺少或非法的基础事实变为默认值。未知开销、未知显式网络类型或编码、NET / 实例 / 接口缺失、配置截断和未展开基础协议继承等仍按原状态报告。已知邻接条件不匹配时仍移除对应候选连接；不补造边，也不声称真实认证成功。

Junos IS-IS 配置 `reference-bandwidth` 时，默认成本依赖实际接口带宽。显式接口 metric 保留优先级；无显式值且没有可靠带宽证据时，transit cost 保留 unknown，不落回 10。用户可用既有 Scenario 显式提供未知开销假设，结果会注明假设。参考 [Junos 原生命令说明](https://www.juniper.net/documentation/us/en/software/junos/cli-reference/topics/ref/statement/reference-bandwidth-edit-protocols-isis.html)。

## 协议路径与默认 FIB 分开

Junos 通用默认行为是选择一个下一跳安装 FIB。明确、无条件的 forwarding-table export 策略含 load-balance per-flow / per-packet 时，才能记录多下一跳转发意图；复杂、缺失或继承策略保留 unknown。

中立实例保存 forwarding_mode 和有效 max_paths。所有协议最短路径仍可展示；候选下一跳超过有效上限时返回 limit_ambiguous，保留理论占比并明确说明实际选中子集未知。因此“发现三条等价路径”不能自动变成“设备默认分担三路流量”。

已知 narrow 的配置路径另受总成本 1023 的限制，目的前缀成本一起计入。双编码的总成本限制不能从单链路 63 的限制直接推出，未获得明确依据的情况不强加统一平台规则。窄/宽编码的收发策略不能仅由 emitted metric-style 推断；模型不能确定互通时返回 unsupported，需要版本、收发配置或详细 LSDB，不能直接断言物理邻接 Down。

## 验证

默认行为测试包括命令省略、继承和覆盖、恢复默认、管理/地址族状态分离、ISO family、窄/宽按 Level 限制、未知 NX-OS 度量、ECMP 默认单下一跳、FIB 策略、总路径度量边界。六设备混合样例刻意保留部分省略字段；Nokia system 使用默认 IS-IS 成本 0，没有构造 CLI 不允许的显式 metric 0。

```bash
.venv/bin/python -m pytest backend/tests/test_default_behaviors.py backend/tests/test_cisco_defaults.py backend/tests/test_junos_defaults.py backend/tests/test_nokia_defaults.py -q
```

## 官方依据

- [Huawei OSPF Cost](https://info.support.huawei.com/enterprise/en/doc/EDOC1100214494/c1494b74/ospf-cost)、[Huawei IS-IS 默认参数](https://info.support.huawei.com/hedex/api/pages/EDOC1100334321/AEM1020X/07/resources/dc/display_default-parameter_isis.html)。
- [Cisco IOS / Junos Loopback 对照](https://www.cisco.com/c/dam/en_us/about/ac123/ac147/archived_issues/ipj_10-3/ipj_10-3.pdf)、[Cisco OSPF Dead](https://www.cisco.com/c/en/us/td/docs/ios-xml/ios/iproute_ospf/command/iro-cr-book/m_ospf-a1.html)、[Cisco passive metric](https://www.cisco.com/c/en/us/td/docs/ios/iproute_isis/command/reference/irs_book/irs_is2.html)、[XR OSPF 继承](https://www.cisco.com/c/en/us/td/docs/ios_xr_sw/iosxr_r3-7/routing/command/reference/rr37ospf.html)。
- [NX-OS N7K 旧版 metric 10](https://www.cisco.com/c/en/us/td/docs/switches/datacenter/nexus7000/sw/unicast/command/reference/n7k_unicast_cmds/l3_cmds_i.pdf)、[NX-OS N9K 新版默认表](https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/106x/configuration/unicast-routing-configuration/cisco-nexus-9000-series-nx-os-unicast-routing-configuration-guide/m-n9k-configuring-is-is-101x.html)。
- [Junos ISO family](https://www.juniper.net/documentation/us/en/software/junos/is-is/topics/concept/isis-configuring-understanding.html)、[Junos Level / 度量](https://www.juniper.net/documentation/us/en/software/junos/cli-reference/topics/ref/statement/level-edit-protocols-isis.html)、[Junos 定时器](https://www.juniper.net/documentation/us/en/software/junos/ospf/topics/topic-map/configuring-ospf-timers.html)、[Junos 默认转发与负载分担](https://www.juniper.net/documentation/us/en/software/junos/sampling-forwarding-monitoring/topics/concept/policy-configuring-per-flow-load-balancing.html)。
- [Nokia MD-CLI 默认值](https://documentation.nokia.com/sr/26-3/7750-sr/books/md-cli-command-reference/router-router_0.html)、[Nokia Classic OSPF](https://documentation.nokia.com/sr/25-3/7x50-shared/unicast-routing-protocols/ospf.html)、[Nokia System ID](https://documentation.nokia.com/sr/25-3/7750-sr/cli-books/classic-cli-command-reference/classic-s-commands-stat-5.html)、[Nokia IS-IS 与 system 成本](https://documentation.nokia.com/sar/24-4-1/books/routing-protocols/is-is.html)。
- [ISO IS-IS 路径度量范围 RFC 1142](https://www.rfc-editor.org/rfc/rfc1142.html)。
