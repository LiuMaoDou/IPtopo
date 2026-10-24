# Parser support and acceptance matrix

This is the implemented **configuration-field subset**, not a claim of full vendor configuration support. Acceptance uses synthetic fixtures shipped in this repository. No real-device backup corpus or live-device interoperability acceptance has been performed. Configured addresses never establish physical adjacency, operational state, or reachability.

## Runtime and integration

The tested runtime is Python 3.12.14 with TTP 0.10.1, `ttp-templates` 0.6.18, and `netutils` 1.19.2. Runtime versions are returned with every parsed snapshot. Upstream packages are consumed without local patches.

`backend.app.parser.parse_config(text, filename, source_file_id, device_id, dialect=None)` is a pure function with no source-file I/O. It returns the device, interfaces, contexts, diagnostics, parse status, dialect metadata and parser versions defined in [CONTRACT.md](CONTRACT.md). An explicit dialect overrides heuristic detection. The supported selection IDs are exported as `SUPPORTED_DIALECTS`.

Interface/context/address IDs derive from the **caller-provided device identity**, original interface/context names and normalized address facts. A hostname is a label and never a device-merge key. SR OS interface identity also includes its router/VPRN because identical interface names can occur in different services. Filename-to-device identity and rename reconciliation belong to the import service.

## Implemented dialects

Junos parsing also accepts interface-only brace exports and relative `set` statements when a `show configuration interfaces ...` command or `[edit interfaces ...]` header supplies the omitted scope. Common physical, aggregate, reth, loopback, IRB and VLAN interface subtrees can be detected without a wrapper. CLI prompts and routing-engine status markers are excluded without shifting source line numbers. Address containers (including empty `{}` blocks) retain their IPv4/IPv6 declaration and inactive state. These cases are covered by synthetic regression tests, including a real /31 + /127 inferred link; the reported user configuration has not yet been supplied for verification.

| Selection ID | Input | Validated fields | Acceptance fixture |
| --- | --- | --- | --- |
| `huawei_vrp` | VRP current-configuration | sysname; IPv4 mask/prefix, sub address; IPv6 prefix/link-local; VPN instance; RD/RT; Eth-Trunk members; Vlanif; description; shutdown/undo shutdown; dot1q; VRRP virtual IPv4 | [Huawei core](../samples/demo/01-core-huawei.cfg) and parameterized tests |
| `cisco_ios` | IOS/IOS-XE running-config | hostname; IPv4 mask/prefix/secondary; IPv6 prefix/link-local; DHCP/unnumbered; VRF; RD/RT; Port-channel members; subinterfaces/SVI; dot1q; description; shutdown/no shutdown; HSRP/VRRP IPv4 | [IOS branch](../samples/demo/06-branch-cisco-ios.cfg) and tests |
| `cisco_xr` | IOS-XR running-config | hostname; ipv4 address mask/prefix; IPv6 prefix; VRF; Bundle-Ether members; subinterfaces; dot1q; description; shutdown/no shutdown | [XR core](../samples/demo/02-core-cisco-xr.cfg) and tests |
| `cisco_nxos` | NX-OS running-config | hostname; IPv4 prefix/mask; IPv6 prefix; vrf member; Port-channel members; SVI; fabric forwarding anycast marker; description; shutdown/no shutdown | [NX-OS services](../samples/demo/05-services-nxos.cfg) and tests |
| `junos_set` | Junos display set | host-name; interface/unit; inet/inet6 address; routing-instance membership; RD/RT; ae member references; VLAN; description; disable; deactivate; virtual-address; DHCP/unnumbered | [Junos edge](../samples/demo/03-edge-junos-set.cfg) and tests |
| `junos_hierarchical` | Junos brace syntax | Same scoped interface fields; inactive interface/unit/address ancestry; brace validation | [Junos branch](../samples/demo/07-branch-junos-hierarchy.cfg) and equivalence/inactive tests |
| `nokia_classic` | SR OS Classic CLI | system name; router/Base; VPRN; named interfaces; IPv4 address/secondary; IPv6 address context; port/SAP; lag members; description; interface shutdown/no shutdown; RD/RT; basic VRRP backup address | [Classic edge](../samples/demo/04-edge-nokia-classic.cfg) and context/LAG tests |
| `nokia_md_flat` | SR OS MD-CLI absolute `/configure` paths | system name; router/Base and service/vprn interfaces; IPv4 primary/secondary address and prefix-length; IPv6 address keys and prefix-length; port/SAP/LAG; admin-state; description | [MD-CLI flat](../samples/fixtures/nokia-md-flat.cfg) and missing-prefix tests |
| `nokia_md_hierarchical` | SR OS MD-CLI braces | Same scoped MD fields, matching address/prefix leaf pairs and declaration provenance; brace validation | [MD-CLI access](../samples/demo/08-access-nokia-md.cfg) |

## Component reuse and source evidence

- The CLI adapter scopes interface blocks first, then invokes TTP with application-maintained field patterns. Each input record includes its original line number. TTP extracts the description, address expression, VRF, aggregation reference, and encapsulation fields. Address families are captured explicitly: numeric literals in TTP patterns must not be trusted to distinguish `ipv4` from `ipv6`.
- The installed `ttp_templates.get_template(path=...)` API is also invoked for the IOS, XR, NX-OS and Junos-set platform templates. Their address candidates are compared with source-aware normalized facts. A candidate absent from the normalized facts produces `template_coverage_gap`. They are **not blindly merged**: upstream output lacks original file-line references and has different default interface-state semantics.
- `ttp-templates` 0.6.18 has an `abspath`/`realpath` containment mismatch when Python runs through a symlinked virtual environment. For that specific error, the adapter loads the same four registered, bundled templates through `importlib.resources`; user-supplied paths are never accepted. Templates are cached. The dependency's installed files are unchanged, and tests exercise both the fallback and rejection of arbitrary template paths.
- Junos and Nokia use context-aware adapters for hierarchy, inactive flags, service membership and split address/prefix leaves. Hierarchical Junos is not converted into invented operational state. `netutils.interface.canonical_interface_name` supplies Cisco normalized names; original names remain intact.
- `ipaddress.ip_interface` normalizes addresses/networks; explicit netmask validation rejects noncontiguous masks and host masks supplied where a configured netmask is required.
- `ttp_sros_parser` and Batfish are **not installed or invoked**. They remain optional candidates; no Batfish API coverage is claimed. Nokia SR Linux is not supported by these SR OS parsers.

Primary references: [TTP API/documentation](https://ttp.readthedocs.io/en/latest/), [TTP templates repository](https://github.com/dmulyalin/ttp_templates), [IOS interface template](https://dmulyalin.github.io/ttp_templates/ttp_templates/platform.cisco_ios_show_running_config_pipe_section_interface/), [XR interface template](https://dmulyalin.github.io/ttp_templates/ttp_templates/platform.cisco_xr_show_running_config_interface/), [NX-OS interface template](https://dmulyalin.github.io/ttp_templates/ttp_templates/platform.cisco_nxos_show_running_config_interface/), [Junos set interface template](https://dmulyalin.github.io/ttp_templates/ttp_templates/platform.juniper_junos_show_configuration_interfaces_pipe_display_set/), [netutils source](https://github.com/networktocode/netutils).

## Semantics and limitations

- `oper_state` is always `unknown`. `admin_state` is populated only from explicit enable/disable configuration. `effective_state=active` means a parsed configuration statement is effective in the supported syntax; it does not mean the interface is up.
- Junos `deactivate` and `inactive:` are propagated only to the corresponding interface/unit/address. An inactive address does not deactivate sibling addresses. Parent interface `disable` propagates to its logical children.
- Junos `groups`/`apply-groups` are not expanded. They generate `unresolved_inheritance`; affected interfaces/addresses are marked unresolved, and inherited addresses are not guessed. The configuration should be exported with inheritance expanded for full effective-interface extraction.
- Cisco interface ranges/preconfigure, template inheritance, XR nested VRRP, complex QinQ rewriting, switchport VLAN expansion, HSRP/VRRP IPv6 variants and vendor-specific implicit defaults are not fully implemented. Unsupported address expressions in recognized interface address statements generate diagnostics; this is not a full configuration linter.
- Nokia MD-CLI flat input must use complete absolute `/configure` paths. Relative navigation, edit-session deltas, list-reset semantics and MD-CLI VRRP expansion are not supported. MD-CLI primary address and prefix-length leaves must both be present. Classic CLI expects ordinary full `configure`/`exit` structure; arbitrary session transcripts and all protocol subcontexts have not been accepted.
- RD and RT are descriptive context fields, not automatic evidence that two devices' VRFs share an address domain. Default contexts and explicit domain mappings are handled by inference, independently of parser labels.
- EUI-64 addresses, DHCP/SLAAC and unnumbered addresses never receive invented host addresses. They retain acquisition metadata and unresolved state. Explicit link-local addresses without prefix use `/64` for display with an informational diagnostic and remain excluded from topology matching. Virtual IPv4 addresses without an explicit prefix use the local primary prefix when available (otherwise `/32`) and retain `role=virtual`.
- Static routes, routing protocol neighbors, DNS/NTP and ACL addresses do not enter interface-address extraction. Overlapping prefixes and duplicate IPs are analyzed later by the inference service.
- ANSI escape removal and BOM/CRLF handling preserve line coordinates. MD split address/prefix records retain both source locations. Raw text is stored separately by the import service and is removed from the ordinary topology projection/export.
- Syntax errors, invalid addresses/masks, unresolved inheritance and detectable truncation produce `partial`. Unknown dialect/empty inputs produce `failed`. No-interface results are explicitly diagnosed. Zero addresses is reported separately from successful address extraction. A syntactically valid but cut-short backup cannot always be distinguished from an intentionally scoped config.

## Repeatable acceptance

```bash
.venv312/bin/python -m pytest backend/tests/test_parser.py -q
```

Tests assert all nine dialect selections, IPv4/IPv6 families and original source lines, IPv4 masks, secondary/virtual/link-local/anycast roles, dynamic exclusion metadata, VRF isolation, LAG/subinterface associations, Junos set/hierarchy equivalence, inactive ancestry, group diagnostics, invalid/empty/truncated input, static-route/neighbor/ACL exclusion, stable IDs, and unknown operational state.

The eight files in `samples/demo/` form one synthetic topology with dual-stack point-to-point links, parallel core links, a shared four-device segment, separately isolated VRFs, LAN stubs, loopbacks, and a dynamic WAN. `samples/fixtures/` is test data and is not part of the demo import.
