# 四厂商混合 IGP 合成样例

这些是手工设计的原生 CLI 测试配置，不是真实设备备份。四家厂商共享 OSPF Area 0 和 IS-IS Level 2，接口开销显式配置；NET / System ID、进程号使用各自语法。

| 设备 | 原生配置 | IPv4 Loopback | IS-IS System ID |
| --- | --- | --- | --- |
| IGP-S | Cisco IOS / XE | 10.255.0.1 | 0000.0000.0001 |
| IGP-A | Juniper Junos set | 10.255.0.2 | 0000.0000.0002 |
| IGP-B | Nokia SR OS Classic | 10.255.0.3 | 0000.0000.0003 |
| IGP-C | Cisco IOS XR | 10.255.0.4 | 0000.0000.0004 |
| IGP-E | Nokia SR OS MD-CLI hierarchy | 10.255.0.5 | 0000.0000.0005 |
| IGP-D | Huawei VRP | 10.255.0.6 | 0000.0000.0006 |

连接：S–A=10，A–D=20，S–B=10，B–C=10，C–D=10，B–E=10，E–D=10，两端开销相同。S 到 D 的路径 S–A–D、S–B–C–D、S–B–E–D 开销均为 30，逐跳 ECMP 分担 50% / 25% / 25%。

OSPF Loopback 开销：S/A/B/C/E 为 1，D 使用 VRP 默认 0；反向 D 到 S 的目的前缀开销计入后总开销为 31。IS-IS Loopback 开销均为 0，两个方向总开销均为 30。

[对应四种原生 LSDB 视图](../multi-vendor-lsdb/README.md)；[公开厂商资料及来源](../public-network/README.md)；[完整功能与边界](../../docs/ROUTING.md)。
