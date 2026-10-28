# 四厂商 LSDB 拓扑还原与配置融合

已实现离线采集批次导入、OSPFv2 / IS-IS 拓扑还原、配置身份与接口关联、最短路径及逐跳 ECMP。LSDB 描述采集视角下的协议连接，不证明当前运行状态或物理布线。

## 使用流程

1. 打开项目，点击工具栏 **LSDB 拓扑**。可以使用已有配置项目，也可以新建空项目仅导入 LSDB。
2. 点击 **导入 LSDB**，选择厂商（或自动识别）、协议、采集设备的本地进程 / 实例、Area / Level、配置 VRF 名称（公网为 `default`），上传同一设备、同一采集时刻的 TXT / LOG 输出。批次名称必填，采集设备与采集时间可留空；未知时间明确显示未知，不用上传时间代替。
3. 华为 OSPF 上传 `display ospf <进程号> lsdb router`、`display ospf <进程号> lsdb network` 详细输出；IS-IS 上传 `display isis <进程号> lsdb verbose no-name`（设备支持 no-name 时）。关闭分页，保留协议 / 进程 / Area / Level 标题和所有记录。一个文件可包含多个范围，导入只选定其中一个范围。
4. 确认已采集完整范围后勾选完整性声明。未勾选、仅摘要、分页截断、字段缺失、缺少反向声明等情况仍可查看数据与诊断，但停止完整路径计算。
5. **LSDB 观测**显示协议 ID；**结合配置**通过 Router ID / System ID、所选协议范围和配置 VRF 名称关联设备。进程号只用于采集设备内选择，不要求不同设备进程号相同。关联不唯一时不自动选择，可使用“手动关联设备身份”保存显式映射。
6. 选择源设备和目的设备发布的 IPv4 前缀，点击 **计算 LSDB 最短路径**。显示总开销、完整路径数、逐跳均匀分担、逐条高亮、出口与来源。未关联配置的节点仍可算协议路径，出口端口、下一跳 IP 和 ECMP 上限可保持未知。
7. 配置 / LSDB 对比显示一致、开销差异、状态差异、仅配置、仅 LSDB 和端口待关联。两侧来源按钮可定位原文。存在对应 /32 Loopback 配置时同时运行原配置仿真，比较两种结果。
8. 项目 JSON 备份包含 LSDB 批次和手动身份映射；选择包含原文后，恢复项目也能查看行级来源。LSDB 画布可导出 PNG；CSV / draw.io 目前仍是配置拓扑导出，因此 LSDB 模式中禁用。LSDB 布局仅保留在当前页面状态，不写入配置视图。

“合成示例”按所选厂商载入原生格式，与 [四厂商混合配置](../samples/multi-vendor-igp/README.md) 对应。源 IGP-S 到目的 IGP-D，总开销 30，三条路径分担 50% / 25% / 25%。可以先从路径仿真面板创建四厂商配置示例，再导入 LSDB 示例，或直接在空项目验证独立还原。

## 各厂商采集命令

| 厂商 | OSPFv2 | IS-IS | 默认实例输入 |
| --- | --- | --- | --- |
| Huawei VRP | `display ospf <进程> lsdb router` / `network` | `display isis <进程> lsdb verbose no-name`（若支持） | 1 |
| Cisco IOS/XE/XR | `show ip ospf <进程> database router` / `network`；XR 为 `show ospf ...` | `show isis [进程] database detail` / `verbose` | 按输出标题填写；无名进程可填 default |
| Juniper Junos | `show ospf database extensive` | `show isis database extensive` | default |
| Nokia SR OS | `show router ospf <实例> database detail` | `show router isis <实例> database detail` | 0 |

采集命令随版本有差异，以设备帮助为准。保持原始标题和完整详细正文；每批只属于一种厂商、一个采集设备、一个时刻。自动识别存在冲突或没有可靠标题时返回错误；显式厂商与文件冲突时保留阻断诊断。

IS-IS 动态主机名可以同时上传数字 System ID→名称映射表。没有映射表时，只在 LSP 明确携带 IPv4 地址且与同协议 / VRF / Level 的配置实例唯一匹配时关联；主机名本身只作标签。冲突需手动关联。缺少端口证据时端口保持待关联，不能凭设备名称构造接口配对。旧华为批次与备份缺少新增厂商字段时仍按华为加载。

## 数据隔离与融合规则

```text
配置原文 → 配置适配器 → Snapshot.routing（RoutingFacts）
                                      ↘
LSDB 原文 → lsdb/adapters.py 注册表 → Batch → lsdb/analysis.py → 协议图与配置差异
                                                       ↓
                                                routing/spf.py
                                                       ↓
                                            路径、ECMP、证据与画布
```

- SQLite 新增 `lsdb_observations` 表，批次数据不可变，不覆盖配置快照，也不自动合并其他采集设备或时间批次。
- `Batch v1` 保存厂商、适配器版本、主机别名映射、采集/导入时间、协议范围、域标签、采集进程、完整性声明、来源文件、LSA / LSP 版本与中立连接/前缀；域标签用于组织独立批次，不能单独证明跨设备身份相同。
- 配置提供设备名称、实例、接口、开销对比、ECMP 上限。观测 SPF 使用 LSDB 开销；仅配置存在的节点和连接以补充图层呈现，不加入观测 SPF。
- 手动身份映射保存在 `Project.settings.lsdb_mappings`，与批次原始事实分离，并随 JSON 备份恢复。配置快照 ID 同时记录导入时与当前分析时的版本。若配置重导入使手动映射目标失效，分析会提示并临时回退自动关联，保留原始保存记录供重新关联。
- 邻接按单向出口度量保存，反向记录用于校验。OSPF Network LSA、IS-IS 伪节点保留在可视拓扑中，SPF 前投影为真实设备间转发步；伪节点不产生额外 ECMP 分支。
- 公共 `routing/spf.py` 不读 CLI / 厂商字段。配置仿真和 LSDB 仿真都调用它。路径列表截断不改变总计数和流量比例；设备 ECMP 配置上限不足时明确标记实际选中子集未知。
- 观测与配置不一致不自动修改任意一侧。原始文本仅存来源文件；统一事实和诊断不复制认证密码。

## 支持与边界

| 项目 | 当前行为 |
| --- | --- |
| Huawei OSPFv2 | Router / Network LSA 详细字段，P-2-P / TransNet / StubNet；支持 Attached Router 带或不带冒号 |
| Huawei IS-IS | 数字 LSP ID 的 verbose 输出，L1 / L2 分开选择，窄/宽邻接、Standard IPv4 前缀、LSP 分片、Overload、伪节点、可用的本地/远端链路 ID 与地址 |
| Cisco OSPFv2 / IS-IS | 原生 OSPF Router / Network 详细字段，IOS/XE/XR 同族格式；IS-IS detail/verbose 的 Tag/Level、数字或动态名称、窄/宽邻接和 IPv4 前缀，X/R 属性与外部/MT 前缀排除 |
| Juniper OSPFv2 / IS-IS | OSPF detail/extensive 的 Router / Network，IS-IS detail/extensive 的标准 IPv4 邻接 / 前缀；Level / Routing Instance 隔离，动态名称、详细 TLV 重复去重；IPv6 专用内容不进入 IPv4 SPF |
| Nokia OSPFv2 / IS-IS | SR OS database detail 的双列字段、逐链路计数、Attached Router；LSP 详细 TLV、TE IS Nbrs、内部 IPv4 前缀、数字/动态名称、逐邻接开销完整性 |
| 老化与版本 | OSPF 按有符号序号、校验和、MaxAge / MaxAgeDiff 选有效版本；IS-IS 按序号去重并排除 Holdtime 0，冲突版本明确阻止计算。分析保持采集时状态，不随当前墙钟自动老化 |
| 同时存在窄/宽邻接 | 同一邻居优先宽度量编码，避免把两种编码当成两条并行链路 |
| 并行链路 | 有可区分的地址或链路 ID 时保留；重复条目没有唯一链路标识时停止分担计算，不猜测数量 |
| 广播与零度量 | 伪节点出口 0 正常处理；IS-IS 零度量真实边可在无环最短路径子图中计算；最短路径子图含零度量环时返回 unsupported |
| 不完整输出 | 仅摘要、分页、Link count / Total LSP(s) 不符、缺失已知分片、缺少对端 / 反向记录等返回诊断；完整性声明不能替代结构校验 |
| 非内部前缀 | OSPF Summary / External / NSSA 不作为拓扑边；IS-IS 明确标记的外部 / 重分发前缀不作为内部目的前缀 |
| 多发布者前缀 | 当前不能将 Anycast / 多发布者共享前缀中的某一个发布者当作唯一目的，停止计算并说明原因 |
| 不支持 | OSPFv3 / IPv6、跨 Area / Level 路由、虚链路、Flex-Algo、MT 专用路径、LSP 扩展虚拟系统、TE 约束路径、真实 FIB Hash / 收敛时间 / ACL |

配置默认值、管理状态和 FIB 单/多下一跳意图见 [默认行为说明](DEFAULTS.md)。缺少配置时默认转发上限保持未知，不能从逻辑 LSDB 证明现场 FIB。

未解析到的详细输出变体需要新增适配测试。CLI 没有标出的信息不能从文本可靠推定；例如没有片段清单时不能证明尾部分片未丢失，IPv4 扩展前缀没有外部标识时无法自行判断其真实来源。完整性声明和计算假设在界面中可见。

资源限制：每批 1–100 个文本文件，总计 16 MiB，支持 UTF-8 / GB18030；最多 20,000 条记录、10,000 个协议节点、100,000 条有向连接 / 展开后的转发步。默认展开 64 条路径，最多 256 条，并限制累计展开 20,000 跳。

## API

| 方法与路径（项目下） | 作用 |
| --- | --- |
| `GET /lsdb` | 列出独立批次元数据 |
| `POST /lsdb` | multipart 导入；`vendor`（auto/huawei/cisco/juniper/nokia，兼容旧客户端默认 huawei）、`files`、`expected_version`、`name`、`protocol`、`process_id`、`scope`、`context_name`、`domain`、`collector`、`collected_at`、`declared_complete` |
| `GET /lsdb/{batch_id}` | 读取不可变批次与记录 |
| `POST /lsdb/{batch_id}/analyze` | `{version, mode: "observed" | "fused", source?, destination?, path_limit?}`；source / destination 从分析结果的 nodes / destinations 中选取 |
| `PUT /lsdb/{batch_id}/mappings` | `{version, mappings: {协议节点ID: 配置实例ID}}`；空对象清除手动关联 |
| `POST /lsdb/demo?protocol=ospf\|isis&vendor=huawei\|cisco\|juniper\|nokia` | `{version}`；导入明确标记为合成的示例 |

分析可显式传入 `mappings` 进行临时覆盖，省略时读取已保存映射。版本冲突返回 409；无效参数或关联返回 422；跨项目批次访问返回 404。所有分析操作只读。

## 验证与来源

测试覆盖两种协议逐跳 ECMP、LSDB-only、独立快照、开销与连接差异、身份冲突、并行链路、广播伪节点、版本/老化、缺失分片、摘要/截断、路径列表截断、来源保密、批次隔离、版本冲突、映射持久化、配置重导入及含/不含原文的备份恢复。浏览器验收面向 PC 桌面窗口，验证导入、批次切换、观测/融合切换、路径高亮、开销差异、来源弹窗、刷新持久化及三栏布局。

验收包含合成 CLI、现有配置和四厂商公开文档输出。公开资料自验证见下节；尚无用户同一采集时刻的现场 LSDB / FIB 对照。

协议与输出依据：[Huawei OSPF Router LSA](https://info.support.huawei.com/network/ptmngsys/Web/tsrev_s/en/content/s/46_edesk_ospf_cannot_learn/edesk_ospf_cannot_learn_edesk005.html)、[Huawei Network LSA](https://info.support.huawei.com/network/ptmngsys/Web/tsrev_ne/cn/content/ne/22_edesk_ne_ospf_routing_oscillation_failure/edesk_ospf_routing_oscillation_failure_edesk003.html)、[Huawei IS-IS 命令参考](https://info.support.huawei.com/enterprise/en/doc/EDOC1100381010/4e0b2b50/is-is-configuration-commands)、[RFC 2328](https://www.rfc-editor.org/rfc/rfc2328.html)、[RFC 5305](https://www.rfc-editor.org/rfc/rfc5305.html)。

## 公开样本自验证（2026-10-28）

新增四厂商官方资料 [38 份公开样本](../samples/public-network/README.md) 和离线验证脚本。覆盖华为摘要/详细 LSA、IS-IS 旧/新格式、过期与不完整数据保护；独立 SPF 与 Cisco 官方公布的两个 Area 0 路由开销 74 一致。由样本修复 `* Link ID`、OSPF 摘要 Len 列、扩展前缀属性等兼容问题。四厂商原生 LSDB 产品适配器均已启用；附加验证包括 Cisco 原生适配器复算开销 74、Junos Network LSA/伪节点、Nokia 详细 LSP 与完整性保护。详见 [验证报告与边界](PUBLIC_NETWORK_VALIDATION.md)。
