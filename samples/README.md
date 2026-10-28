# Synthetic configurations

`demo/` contains eight independently importable configurations for the demo topology. These are artificial interface-focused examples, not production-ready device configurations. Addresses use private/documentation ranges. The sample names label devices; they are not identities used for deduplication.

The backbone contains two parallel CORE-BJ-01 ↔ CORE-SH-02 subnets, dual-stack edge transits, four shared services members, west/east branches, and an access router. BLUE and VPRN contexts remain isolated until the user explicitly maps them into a shared domain.

`fixtures/` contains additional dialect samples used by tests. [The parser support matrix](../docs/SUPPORT_MATRIX.md) describes accepted fields and limitations.

`huawei-igp/` is a separate six-device synthetic routing lab. Every device runs OSPF Area 0 and IS-IS Level 2 with explicit transit metrics and a /32 Loopback. Local process IDs deliberately differ across devices. From IGP-S (10.255.0.1) to IGP-D (10.255.0.6), cost is 30 and paths are S-A-D (50%), S-B-C-D (25%), S-B-E-D (25%) under ideal per-hop ECMP. Disabling S's GigabitEthernet0/0/0 removes the A branch and leaves two 50% paths. These are parser/simulation fixtures, not production configurations or live forwarding evidence.

`huawei-lsdb/ospf.txt` 与 `huawei-lsdb/isis.txt` 是明确标记为合成的详细 LSDB 输出，与 `huawei-igp` 六设备配置对应。导入时分别选择 OSPF 进程 1 / Area 0.0.0.0 或 IS-IS 进程 1 / Level 2，并勾选完整采集声明。IGP-S → IGP-D 为开销 30、三条路径 50% / 25% / 25%。它们不是来自真实设备的生产采集。

`public-network/`：四家厂商官方文档提取的 38 份配置、OSPF / IS-IS LSDB 与辅助验证输出，带逐文件来源、SHA-256、完整性和提取说明。可运行 `python -m scripts.validate_public_network` 离线复验。详见 [公开数据与验证边界](public-network/README.md)。

`multi-vendor-igp/` 与 `multi-vendor-lsdb/`：新增六设备四厂商原生配置和八种厂商/协议 LSDB 视图，均为明确标记的合成测试数据。验证 S→D 开销 30、ECMP 50% / 25% / 25%，OSPF 反向含目的开销为 31。支持独立导入 LSDB、混合配置关联和 JSON 恢复。详见 [配置说明](multi-vendor-igp/README.md) / [采集实例与格式](multi-vendor-lsdb/README.md)。
