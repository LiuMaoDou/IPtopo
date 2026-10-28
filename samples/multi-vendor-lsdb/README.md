# 四厂商原生 LSDB 合成样例

每个文件都是明确标记为合成的 CLI 视图，用四种原生格式表达同一张 [六设备混合配置拓扑](../multi-vendor-igp/README.md)。不是现场采集，不作为厂商版本全覆盖或真实 FIB 证据。文件之间按独立批次导入，不能拼接成一个采集设备。

| 厂商 | 文件 | 采集设备 | OSPF 进程 | IS-IS 进程 |
| --- | --- | --- | --- | --- |
| Huawei | `huawei-ospf.txt` / `huawei-isis.txt` | IGP-D | 6 | 6 |
| Cisco | `cisco-ospf.txt` / `cisco-isis.txt` | IGP-S | 1 | CORE |
| Juniper | `juniper-ospf.txt` / `juniper-isis.txt` | IGP-A | default | default |
| Nokia | `nokia-ospf.txt` / `nokia-isis.txt` | IGP-B | 1 | 1 |

OSPF 选择 Area `0.0.0.0`；IS-IS 选择 Level `2`。配置 VRF 名称为 `default`。所有文件均有 6 条详细记录；勾选完整性声明后，S→D 得到开销 30、三条路径 50% / 25% / 25%。OSPF 反向 D→S 为 31，包含目的 Loopback 开销 1；IS-IS 反向为 30。

页面先创建“四厂商路径仿真示例”，再打开“LSDB 拓扑”，选择采集厂商并载入两种协议的示例。导入端支持自动识别；API 示例入口为 `POST /api/projects/{id}/lsdb/demo?vendor=huawei|cisco|juniper|nokia&protocol=ospf|isis`，请求 `{version}`。

[采集格式与限制](../../docs/LSDB.md)；[真正的公开文档原文、哈希与来源](../public-network/README.md)。
