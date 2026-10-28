# 四厂商 IGP 路径仿真

离线 OSPF / IS-IS 路径仿真适配 Huawei、Cisco、Juniper、Nokia。输入为完整配置快照；结果是配置条件与显式场景假设下的协议候选路径，不代表在线邻居、实时可达或硬件 FIB。

## 使用

1. 导入包含接口和协议进程的四厂商原生完整配置。旧项目需重新导入，旧快照仍能打开。
2. 工具栏点击“路径仿真”，选择协议、源设备 / 本地实例、Area 或 Level、目的设备的已发布 /32 Loopback。
3. 计算后，画布展示方向、出口开销、理论流量占比；路径卡片可单独高亮。
4. “场景调整”支持未知开销假设、接口开销覆盖、接口退出。调整仅作用于本次计算，不改写快照与原配置，目前不跨刷新持久化。
5. 没有协议事实时可载入独立的四厂商混合仿真示例项目。六设备样例同时配置 OSPF 和 IS-IS；IGP-S 到 IGP-D 总开销 30，三条路径理论占比为 50%、25%、25%。

目的地址限于唯一的 /32 Loopback。OSPF 默认 Loopback 按主机路由发布；IS-IS 样例使用 /32 地址。共享子网目的地址、Anycast 和 IPv6 尚不支持。

## 厂商与数据解耦

```text
原始配置 → routing/adapters 注册表 → RoutingFacts v1
                                        ↓
                    有效拓扑 + 临时 Scenario
                                        ↓
                        routing/simulation.py
                                        ↓
                     SimulationResult → 画布 / 面板
```

- `routing/model.py` 是厂商无关的 Pydantic 契约。实例、协议接口、前缀发布、适配覆盖与诊断保存在快照 `routing` 中；设备 / 接口 / 路由上下文继续使用原有身份。
- `routing/adapters/__init__.py` 注册适配器。四家厂商输出相同契约；引擎不读取 vendor / OS 字段，也不导入适配器。
- OSPF Area、IS-IS Level、L1 Area 地址匹配等协议差异由计算层处理。本地进程号只用于设备内关联，不要求两端相同；VRF 名称不充当跨设备全局身份。
- `cost_origin` 区分显式值、已验证默认值、未知值。来源保留文件 ID / 行号；诊断不复制可能包含凭据的命令正文。
- `adapter_devices` 标记已完成路由适配的设备；未适配对端触发数据不足。适配覆盖不证明源配置本身完整。
- 原始快照不可变，现有人工修正产生有效拓扑，Scenario 再单独覆盖。结果绑定快照 ID、项目版本和引擎版本。
- 合并、替换、失败文件保护和 JSON 导出 / 恢复均保留正确的设备路由事实。项目格式仍为 v1，新增可选 `routing.schema_version=1`；旧快照视为空路由模型。

## 四厂商配置适配

| 厂商 | 方言 | 适配的协议字段 |
| --- | --- | --- |
| Huawei | VRP | 下表中的进程、Area/Level、NET、接口、开销、静默接口、ECMP |
| Cisco | IOS / XE、XR、NX-OS | router ospf/isis；接口启用或 OSPF network 通配符；XR Area / 接口层级与 IPv4 AF 隔离；router-id、NET、metric-style（支持按 Level）、is-type、cost/metric、P2P、passive、定时器、maximum-paths |
| Juniper | Junos set / hierarchical | routing-options router-id、ISO NET；OSPF area/interface、IS-IS interface/level；metric、wide-metrics-only、passive、P2P、disable/inactive/deactivate；实例与 VRF 隔离 |
| Nokia | SR OS Classic / MD-CLI flat / hierarchical | Base/VPRN；OSPF area/interface、IS-IS level/interface；router-id、system-id、area-id/area-address、level-capability、metric、wide-metrics-only、passive、P2P、admin-state/shutdown、router ecmp |

默认行为按厂商与方言分别建模；已核实的 IS-IS 普通成本通常为 10，NX-OS 平台差异和额外 Loopback 等无法确定的值保留未知。IOS/XE 和 XR 的窄度量、NX-OS 的宽度量、Junos/SR OS 的双编码分别记录。显式按 Level 配置优先。SR OS System ID 按显式 system-id、IS-IS router-id、router router-id、system IPv4 地址的优先级获取，派生使用每个 IPv4 八位组补齐三位十进制。没有可验证身份时保持缺失，不按主机名猜测。

未知语义、无效数值、未展开协议继承会保留来源诊断，阻止受影响配置路径。这里的“支持”覆盖表中普通 IPv4 内部路由语义，不表示所有 OS 版本、认证、策略和扩展功能均已实现。

[混合配置样例](../samples/multi-vendor-igp/README.md) 的 6 台设备、12 个协议实例通过同一计算引擎；S→D 总开销 30、三路径分担 50% / 25% / 25%。反向 OSPF D→S 为 31，因为目的 Loopback 显式开销为 1；IS-IS 反向为 30。

## 支持的 VRP 语法

| 范围 | 支持 |
| --- | --- |
| OSPF 实例 | `ospf [process-id] [router-id ...] [vpn-instance ...]`；`area`；`network address wildcard`；`silent-interface all/interface`、`undo silent-interface interface`；显式 `maximum load-balancing` |
| OSPF 接口 | `ospf enable [process-id] area ...`；`ospf cost`；`ospf network-type p2p/broadcast`；Hello / Dead 定时器 |
| IS-IS 实例 | `isis [process-id] [vpn-instance ...]`；`network-entity`；`is-level`；`cost-style narrow/wide`；`circuit-cost [level-1/level-2]`；显式 `maximum load-balancing` |
| IS-IS 接口 | `isis enable [process-id]`；`isis cost [level-1/level-2]`；`isis circuit-level`；`isis circuit-type p2p`；`isis silent` |
| 连接 | 编号的 IPv4 P2P、兼容广播网段、并行三层链路；聚合口参与三层计算，物理成员不重复计入 |

源配置需包含明确接口与进程上下文，增量配置、命令历史及任意缩写不在验收范围。认证、策略、重分发、汇总、Stub / NSSA、Overload、FRR、TE 等尚未适配的协议配置会给出诊断，并保守阻止受影响范围的计算。

OSPF 只计算所选 Area 内的路径，IS-IS 只计算选定 Level 内的路径。L1 要求邻接两端有共同 Area 地址，L2 不要求 Area 相同。双 Level 实例可单独分析一个 Level，结果不包含跨 Level 泄漏、缺省路由或 L1 / L2 路由竞争。IS-IS narrow / wide / both 按所选 Level 记录。收发编码策略不能唯一确定互通行为时返回 unsupported，不把编码字段当成物理邻接状态。静态 / BGP / 其他协议选路、ACL、硬件转发策略及收敛时间不参与计算。

## 默认值与未知值

完整差异、启用前提、继承、转发默认值与官方依据见 [默认行为说明](DEFAULTS.md)。

- OSPF transit Cost 未显式配置时保持未知，不根据接口名称猜测链路带宽。OSPF Loopback 默认成本：VRP / Junos / Nokia system 为 0，IOS/XE 为 1；XR、NX-OS 和其他未核实情形保持未知。P2P / Broadcast 常见默认 10 / 40，Cisco Dead 随有效 Hello 取四倍。
- IS-IS 使用已记录的 VRP 默认：Level-1-2、narrow、普通接口开销 10、Loopback 开销 0。显式接口开销优先于进程开销，再使用默认值。
- 未提供 NET、未知 / 无效开销、重复 Router ID / System ID、未适配语义等产生诊断。已知不匹配的网络类型、OSPF 定时器、IS-IS L1 Area 不建立候选邻接。
- 运行态仍为 unknown，仿真假设候选链路正常；人工确认不能当成运行态观察。

来源：[Huawei OSPF Cost](https://info.support.huawei.com/enterprise/en/doc/EDOC1100214494/c1494b74/ospf-cost)、[OSPF 启用方式](https://info.support.huawei.com/network/ptmngsys/Web/tsrev_s/cn/content/s/22_OSPF_Neighbors_Failing_to_Be_Established/edesk_OSPF_Neighbors_Failing_to_Be_Established_edesk003.html)、[IS-IS 默认参数](https://info.support.huawei.com/hedex/api/pages/EDOC1100334321/AEM1020X/07/resources/dc/display_default-parameter_isis.html)、[IS-IS 接口 Level](https://info.support.huawei.com/hedex/api/pages/EDOC1100334321/AEM1020X/08/resources/dc/dc_cfg_isisv6_0008.html)、[IS-IS 命令参考](https://info.support.huawei.com/enterprise/en/doc/EDOC1100381010/4e0b2b50/is-is-configuration-commands)。

## SPF 与 ECMP

以协议实例为顶点建立有向多重图，权重取发送端接口开销。共享广播网段按可用下一跳生成转发关系，每次发送只累计一次发送端开销。拒绝关系、退出接口、物理聚合成员不参与；画布折叠与筛选不改变计算拓扑。

反向 Dijkstra 得到到目的前缀的距离，保留所有满足最短距离等式的出边。Transit 开销必须为正值，因此后继关系无环。按完整后继图计算路径数量并逐跳分流，汇合点累加入流；路径清单单独有界展开，不使用枚举清单估计占比。

例如 S 向 A、B 各分 50%，B 再向 C、E 各分总流量的 25%。三条端到端路径因此是 50% / 25% / 25%。普通 ECMP 不自动按带宽加权；LAG 物理成员分担不计入 IGP 路径数。

ECMP 配置上限不足时保留全部协议候选路径，并标记 `forwarding_status=limit_ambiguous`，不猜测硬件保留的子集。未知上限、独立均匀 Hash、充分流量等假设随结果返回。占比不是单流轨迹或实测带宽。

参考：[OSPF RFC 2328 §16.8](https://www.rfc-editor.org/rfc/rfc2328.html#section-16.8)、[IS-IS RFC 1195](https://www.rfc-editor.org/rfc/rfc1195.html)、[ECMP RFC 2992](https://www.rfc-editor.org/rfc/rfc2992.html)。

## API

`POST /api/projects/{id}/simulate`

```json
{
  "version": 2,
  "protocol": "ospf",
  "scope": "0.0.0.0",
  "source_instance_id": "从 snapshot.routing.instances 选择",
  "destination_advertisement_id": "从 snapshot.routing.advertisements 选择",
  "scenario": {
    "disabled_interfaces": [],
    "metric_overrides": {},
    "unknown_cost": null
  },
  "path_limit": 64
}
```

IS-IS scope 为 `1` 或 `2`，OSPF 为规范点分十进制 Area。metric_overrides 键为协议接口 binding ID；退出按基础 interface ID，影响该接口两个方向。

响应 status：ok、incomplete、unreachable、unsupported。成功包含总开销、字符串形式的完整路径数、有限路径清单、逐跳接口 / 下一跳 / 开销 / 来源 / 占比、转发摘要、诊断及假设。版本变化返回 409，无效 ID / 场景返回 422。

路径默认 64、最大 256 条，并限制累计展开跳数；完整下一跳关系和分担独立计算。共享网段最多展开 128 个协议接口，最多 100,000 条有向邻接，超限明确返回 unsupported。

## 验收

回归覆盖逐跳分担、方向性开销、并行链路、路径截断、未知开销、接口退出、ECMP 上限、厂商元数据独立性及导入 / 合并 / 替换 / 恢复。前端验收面向 PC 桌面浏览器，验证路径高亮、场景调整清除旧结果与三栏布局。

算法正确性使用合成样例验收，另对本机既有项目的 9 台华为设备配置做了只读解析抽查。实际配置包含大量 Tunnel、Flex-Algo、认证和重分发等高级语义，仍会保守阻止相关范围的仿真；未修改或重新导入这些原项目。尚无对应路由表 / LSDB 对照，不宣称真实设备选路一致性已验证。

## LSDB 观测输入

新增 [LSDB 拓扑还原与配置融合](LSDB.md)：独立采集批次、四厂商 OSPFv2 / IS-IS 详细输出、设备与接口关联、差异对比、逐跳 ECMP。配置图构建仍由 `routing/simulation.py` 处理，核心 SPF 已抽出到厂商无关的 `routing/spf.py`，两种输入共用。配置模式保留正 transit 开销约束；LSDB 模式另外支持伪节点投影和无环的零开销边。

公开资料回归：四厂商 [38 份官方文档 CLI](../samples/public-network/README.md)，123 项离线检查；Cisco 原生 LSDB 适配器和独立参考读取器分别复算两个公布的 OSPF 路由，开销均为 74。另有混合网络、九种配置方言、八种厂商/协议 LSDB 输入、身份关联、反向目的开销、摘要/缺失保护、来源与备份恢复的自动化验收。

新增默认参数依据：[Cisco NX-OS 度量](https://www.cisco.com/c/en/us/td/docs/switches/datacenter/nexus7000/sw/unicast/command/reference/n7k_unicast_cmds/l3_cmds_m.html)、[Cisco XR IS-IS](https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/routing/26xx/configuration/guide/b-routing-cg-ncs5500-26xx/implementing-isis.html)、[Junos IS-IS metric](https://www.juniper.net/documentation/us/en/software/junos/cli-reference/topics/ref/statement/metric-edit-protocols-isis.html)、[Nokia System ID 优先级](https://documentation.nokia.com/sr/25-7/7750-sr/cli-books/classic-cli-command-reference/classic-s-commands-stat-5.html)。
