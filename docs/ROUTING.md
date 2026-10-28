# 四厂商 IGP 路径仿真

离线 OSPF / IS-IS 路径仿真适配 Huawei、Cisco、Juniper、Nokia。输入为完整配置快照；结果是配置条件与显式场景假设下的协议候选路径，不代表在线邻居、实时可达或硬件 FIB。

## 使用

1. 导入包含接口和协议进程的四厂商原生完整配置。旧快照未提取协议事实时需要重新导入。
2. 打开“路径仿真”，手动选择源设备、源端口、目的设备和目的端口；也可在配置画布右键设备设为源或目的，再手动选择端口。端口列表只包含配置中已使能所选协议的 IPv4 接口；协议尚未确定时显示共同可用协议的接口。切换协议会清除不兼容的端口选择，保留设备与仍兼容的端口。
3. 源端口提供源 IPv4 地址与本地路由上下文，第一跳出口由路由计算，不强制使用所选源端口。目的端口可以是普通三层接口或 Loopback，需有有效 IPv4 地址和对应的协议前缀发布。
4. 端点选定后立即在画布保留源 / 目的标记，点击“仿真”才显示路径。再次点击某条已选路径可恢复该组全部路径。关闭面板保留端点标记；更换端点清除旧路径，导入新配置清除端点与结果。
5. 协议选项取自实际配置，只有多种协议可用时显示协议选择。实例与 Area / Level 从配置匹配，界面不提供这些选择器、目的 Loopback 专用选择器或场景调整。多个独立路由范围分别计算，结果不合并为一个 ECMP 分布。

计算仍限于 IPv4、OSPF 单 Area / IS-IS 单 Level 内部路径。未配置协议、目的端口未发布、缺失开销或范围不兼容时明确提示，不猜测协议和路径。未知开销、退出接口等高级场景请求保留在原有 API / Agent 契约中，不显示在简化面板。

六设备四厂商样例同时配置 OSPF 和 IS-IS；IGP-S 到 IGP-D 的 Loopback 总开销 30，三条路径理论占比为 50%、25%、25%。普通目的端口会按所选 IPv4 地址计算连接到该端口的末跳，不重复累计目的接口发布成本。

## 诊断范围与自动开销

仿真检查所选两端的协议范围及可能参与转发的编号 IPv4 候选连接，而不是只检查两端端口。中间接口的未知开销可能改变最短路径，仍会阻止计算；没有候选对端的 Tunnel、其他范围或已关闭接口的诊断不阻断本次路径。相同来源的诊断去重，完整配置诊断仍保留在项目中。

Huawei `auto-cost enable [compatible]` 已按确认规则建模：显式 `isis cost` 优先于进程 `circuit-cost`，两者都没有时才使用自动开销；Loopback 不变。wide / wide-compatible 使用 `(bandwidth-reference / 接口带宽) × 10`，compatible 自动模式不乘 10；narrow / narrow-compatible / compatible 使用带宽分档表。当前 wide 自动模式要求配置中存在可解析的 `bandwidth-reference`，缺少时保留未知，不猜参考带宽。

配置未声明带宽时，按已确认的**配置推演假设**使用接口类型的标称速率，聚合口按全部配置成员求和，子接口继承父接口。不会把这些值当成实测协商速率或当前活动成员带宽。整数开销取整且最低为 1，并在计算假设中披露。无法唯一确定的双速端口仍需逐项确认；人工确认使用 `override_field / assumed_bandwidth_mbps` 保存为可撤销修正，随项目 JSON 备份恢复，不改写原配置。

接口详情提供“仿真速率”输入框（Gbit/s），可直接修改普通端口、聚合口和子接口。回车或移开焦点保存并重算开销，留空恢复自动；修改保存在可撤销记录中。设备详情可按接口名或地址筛选，诊断中的接口名称也能直接打开设置。速率修改保留当前源 / 目的选择，清除旧路径，重新仿真后显示新结果。

路径仿真保留平行链路的聚合状态，在聚合线上标记路径方向、开销和总分担比例，不会自动展开全部链路。双击某组链路可手动展开 / 收起；具体出接口仍可在逐跳详情中查看。

保留配置原文的旧快照会在读取时刷新路由派生字段，无需再次上传；快照和接口身份不变。JSON 备份携带刷新后的路由派生字段，即使不附带原文，也能重放已确认的带宽修正。缺少原文且没有新派生字段的旧备份仍保留未知状态。

相关接口输出的有效开销及来源可用 [Huawei `display isis cost`](https://info.support.huawei.com/hedex/api/pages/EDOC1100149311/AZJ0713J/17/resources/cli_vrp/display_isis_cost.html) 对照。标称带宽推演和真实设备有效开销必须区分。

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

未建模的高级配置语义默认忽略，保留警告和来源，继续按已解析的基础 IPv4 图计算。无效基础数值、未展开且可能包含基础协议字段的继承、缺失事实仍会限制计算。这里的“支持”覆盖表中普通 IPv4 内部路由语义，不表示所有 OS 版本、认证、策略和扩展功能均已实现。

[混合配置样例](../samples/multi-vendor-igp/README.md) 的 6 台设备、12 个协议实例通过同一计算引擎；S→D 总开销 30、三路径分担 50% / 25% / 25%。反向 OSPF D→S 为 31，因为目的 Loopback 显式开销为 1；IS-IS 反向为 30。

## 支持的 VRP 语法

| 范围 | 支持 |
| --- | --- |
| OSPF 实例 | `ospf [process-id] [router-id ...] [vpn-instance ...]`；`area`；`network address wildcard`；`silent-interface all/interface`、`undo silent-interface interface`；显式 `maximum load-balancing` |
| OSPF 接口 | `ospf enable [process-id] area ...`；`ospf cost`；`ospf network-type p2p/broadcast`；Hello / Dead 定时器 |
| IS-IS 实例 | `isis [process-id] [vpn-instance ...]`；`network-entity`；`is-level`；`cost-style narrow/wide`；`circuit-cost [level-1/level-2]`；显式 `maximum load-balancing` |
| IS-IS 接口 | `isis enable [process-id]`；`isis cost [level-1/level-2]`；`isis circuit-level`；`isis circuit-type p2p`；`isis silent` |
| 连接 | 编号的 IPv4 P2P、兼容广播网段、并行三层链路；聚合口参与三层计算，物理成员不重复计入 |

源配置需包含明确接口与进程上下文，增量配置、命令历史及任意缩写不在验收范围。认证、策略、重分发、汇总、Stub / NSSA、Overload、Flex-Algo、FRR、TE、SPF 调度或 LSP 生成定时器等未建模语义不再一律阻止基础路径。它们作为忽略警告与来源返回，结果明确说明未模拟相应效果；未知认证情况下仍假设认证与候选邻接正常，没有新增认证匹配推断。

### 忽略语义与事实不足

默认行为固定为忽略扩展语义，无需额外开关。诊断中的 `ignored=true`、`blocking=false`、`severity=warning` 表示“按基础 IPv4 图计算，未模拟该语义”。仍保留原问题代码、描述、设备 / 接口关联及来源行，且不复制可能含密钥的配置正文。

| 问题类型 | 基础 IPv4 计算行为 |
| --- | --- |
| `unsupported_protocol_command`、`unsupported_interface_command` | 忽略未建模扩展，保留警告 |
| `unsupported_protocol_statement`、`unsupported_area_statement` | 同上；不能据此断言高级策略与真实路由行为一致 |
| `unsupported_interface_statement`、`unsupported_area_command` | 兼容通用扩展命令 / statement 诊断，按同一忽略策略处理 |
| `unknown_protocol_command`、`unknown_protocol_statement` | 兼容旧快照的通用协议语义诊断，按同一忽略策略处理 |
| `unknown_interface_command`、`unknown_interface_statement` | 兼容旧快照的通用接口语义诊断，按同一忽略策略处理 |
| `unknown_area_command`、`unknown_area_statement` | 兼容旧快照的通用 Area 语义诊断，按同一忽略策略处理 |
| `unmodeled_authentication` | 不模拟认证；明确以认证与邻接正常为假设 |
| 未知 / 无效开销、未知显式网络类型、NET 或接口 / 实例缺失、重复目的或标识、配置截断、未展开基础协议继承 | 保留具体事实不足诊断，不能补造开销、身份或连接 |
| 已知 Hello / Dead、网络类型或 IS-IS L1 Area 不匹配 | 不建立相应候选邻接；没有其他路径时不可达 |
| 未确定的窄 / 宽编码互通、超出当前计算规模 | 继续返回 `unsupported` |

旧快照中保存的通用语义诊断即使仍标记 `blocking=true`，计算时也采用上述忽略策略，无须为了消除这类阻塞而重导入。原快照不被改写。新导入会把无法解析的显式成本、网络类型、编码、身份等基础问题归入独立问题代码，避免错误套用默认值。

成功结果仍只是基础图的静态 SPF。忽略重分发、汇总、Stub / NSSA、Overload 或 Flex-Algo 等语义，可能使结果与设备实际选路不同；需要验证这些效果时应提供相应运行态证据，而不是把 `ok` 当成实时邻接或硬件 FIB 证明。

OSPF 只计算所选 Area 内的路径，IS-IS 只计算选定 Level 内的路径。L1 要求邻接两端有共同 Area 地址，L2 不要求 Area 相同。双 Level 实例可单独分析一个 Level，结果不包含跨 Level 泄漏、缺省路由或 L1 / L2 路由竞争。IS-IS narrow / wide / both 按所选 Level 记录。收发编码策略不能唯一确定互通行为时返回 unsupported，不把编码字段当成物理邻接状态。静态 / BGP / 其他协议选路、ACL、硬件转发策略及收敛时间不参与计算。

## 默认值与未知值

完整差异、启用前提、继承、转发默认值与官方依据见 [默认行为说明](DEFAULTS.md)。

- OSPF transit Cost 未显式配置时保持未知，不根据接口名称猜测链路带宽。OSPF Loopback 默认成本：VRP / Junos / Nokia system 为 0，IOS/XE 为 1；XR、NX-OS 和其他未核实情形保持未知。P2P / Broadcast 常见默认 10 / 40，Cisco Dead 随有效 Hello 取四倍。
- IS-IS 使用已记录的 VRP 默认：Level-1-2、narrow、普通接口开销 10、Loopback 开销 0。显式接口开销优先于进程开销，再使用默认值。
- 未提供 NET、未知 / 无效开销、重复 Router ID / System ID 等保留具体事实诊断；未建模扩展语义按忽略警告处理。已知不匹配的网络类型、OSPF 定时器、IS-IS L1 Area 不建立候选邻接。
- Junos IS-IS 显式配置 `reference-bandwidth` 后，缺少显式接口开销和可靠带宽证据的 transit 接口保留未知，不能继续使用默认 10；已有显式开销优先，也可使用临时 Scenario 的未知开销假设。依据见 [Junos reference-bandwidth](https://www.juniper.net/documentation/us/en/software/junos/cli-reference/topics/ref/statement/reference-bandwidth-edit-protocols-isis.html)。
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

算法正确性使用合成样例验收，另对本机既有项目的 9 台华为设备配置做过只读解析抽查。实际配置包含 Tunnel、Flex-Algo、认证和重分发等语义：高级扩展现按上述策略忽略，无法确定网络类型、开销或基础协议范围的部分仍保留限制；未修改或重新导入这些原项目。尚无对应路由表 / LSDB 对照，不宣称真实设备选路一致性已验证。

## LSDB 观测输入

新增 [LSDB 拓扑还原与配置融合](LSDB.md)：独立采集批次、四厂商 OSPFv2 / IS-IS 详细输出、设备与接口关联、差异对比、逐跳 ECMP。配置图构建仍由 `routing/simulation.py` 处理，核心 SPF 已抽出到厂商无关的 `routing/spf.py`，两种输入共用。配置模式保留正 transit 开销约束；LSDB 模式另外支持伪节点投影和无环的零开销边。

公开资料回归：四厂商 [38 份官方文档 CLI](../samples/public-network/README.md)，123 项离线检查；Cisco 原生 LSDB 适配器和独立参考读取器分别复算两个公布的 OSPF 路由，开销均为 74。另有混合网络、九种配置方言、八种厂商/协议 LSDB 输入、身份关联、反向目的开销、摘要/缺失保护、来源与备份恢复的自动化验收。

新增默认参数依据：[Cisco NX-OS 度量](https://www.cisco.com/c/en/us/td/docs/switches/datacenter/nexus7000/sw/unicast/command/reference/n7k_unicast_cmds/l3_cmds_m.html)、[Cisco XR IS-IS](https://www.cisco.com/c/en/us/td/docs/iosxr/ncs5500/routing/26xx/configuration/guide/b-routing-cg-ncs5500-26xx/implementing-isis.html)、[Junos IS-IS metric](https://www.juniper.net/documentation/us/en/software/junos/cli-reference/topics/ref/statement/metric-edit-protocols-isis.html)、[Nokia System ID 优先级](https://documentation.nokia.com/sr/25-7/7750-sr/cli-books/classic-cli-command-reference/classic-s-commands-stat-5.html)。
