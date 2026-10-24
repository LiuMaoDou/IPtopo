from pathlib import Path

import pytest

from backend.app.parser import SUPPORTED_DIALECTS, parse_config

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def parse(text, dialect=None, device="device-1"):
    return parse_config(text, "router.cfg", "source-1", device, dialect)


def addresses(result):
    return [a for i in result["interfaces"] for a in i["addresses"]]


def by_name(result, name):
    return next(i for i in result["interfaces"] if i["raw_name"] == name)


@pytest.mark.parametrize("filename,dialect,count", [
    ("demo/01-core-huawei.cfg", "huawei_vrp", 9),
    ("demo/02-core-cisco-xr.cfg", "cisco_xr", 8),
    ("demo/03-edge-junos-set.cfg", "junos_set", 8),
    ("demo/04-edge-nokia-classic.cfg", "nokia_classic", 6),
    ("demo/05-services-nxos.cfg", "cisco_nxos", 7),
    ("demo/06-branch-cisco-ios.cfg", "cisco_ios", 8),
    ("demo/07-branch-junos-hierarchy.cfg", "junos_hierarchical", 6),
    ("demo/08-access-nokia-md.cfg", "nokia_md_hierarchical", 4),
    ("fixtures/nokia-md-flat.cfg", "nokia_md_flat", 4),
])
def test_each_dialect_detects_parses_and_preserves_source(filename, dialect, count):
    text = (SAMPLES / filename).read_text()
    result = parse(text)
    assert result["status"] == "success", result["diagnostics"]
    assert len(addresses(result)) == count
    assert result["device"]["source_refs"][0]["parser"] == dialect
    assert all(i["oper_state"] == "unknown" for i in result["interfaces"])
    for a in addresses(result):
        assert a["source_refs"]
        ref = a["source_refs"][0]
        assert text.splitlines()[ref["line"] - 1] == ref["text"]
        if a["address"]:
            assert a["address"] in ref["text"]
    assert not any(d["code"] in ("parser_error", "upstream_template_unavailable") for d in result["diagnostics"])


def test_supported_dialects_are_unique():
    assert len(SUPPORTED_DIALECTS) == len({d["id"] for d in SUPPORTED_DIALECTS}) == 9


@pytest.mark.parametrize("dialect,prefix", [("cisco_ios", "ip"), ("cisco_xr", "ipv4"), ("cisco_nxos", "ip"), ("huawei_vrp", "ip")])
def test_masks_secondary_ipv6_dynamic_and_virtual(dialect, prefix):
    result = parse(f"""hostname edge
interface GigabitEthernet0/0
 {prefix} address 10.1.0.1 255.255.255.0
 {prefix} address 10.2.0.1 255.255.255.0 secondary
 ipv6 address 2001:db8::1/64
 ipv6 address fe80::1 link-local
 standby 1 ip 10.1.0.254
!
interface GigabitEthernet0/1
 ip address dhcp
!
end
""", dialect)
    assert result["status"] == "success", result["diagnostics"]
    found = addresses(result)
    assert [(a["family"], a["role"]) for a in found] == [(4, "primary"), (4, "secondary"), (6, "primary"), (6, "link_local"), (4, "virtual"), (4, "unknown")]
    assert found[1]["canonical_network"] == "10.2.0.0/24"
    assert found[-1]["address"] is None
    assert found[-1]["effective_state"] == "unresolved"
    assert found[-1]["acquisition_method"] == "dhcp"


@pytest.mark.parametrize("value", ["10.0.0.1 255.0.255.0", "10.0.0.1 0.0.0.255", "999.0.0.1/24", "10.0.0.1/99", "10.0.0.1"])
def test_invalid_masks_and_addresses_report_partial(value):
    result = parse(f"hostname edge\ninterface Gi0/0\n ip address {value}\n!\n")
    assert result["status"] == "partial"
    assert not addresses(result)
    assert any(d["code"] in ("invalid_netmask", "invalid_address", "missing_prefix") for d in result["diagnostics"])


def test_static_route_neighbor_dns_acl_are_not_interface_addresses():
    result = parse("""hostname edge
ip name-server 198.51.100.1
interface Gi0/0
 ip address 10.0.0.1 255.255.255.0
!
ip route 203.0.113.0 255.255.255.0 10.0.0.2
router bgp 65000
 neighbor 198.51.100.2 remote-as 65001
ip access-list standard TEST
 permit 192.0.2.0 0.0.0.255
end
""")
    assert [a["address"] for a in addresses(result)] == ["10.0.0.1"]


def test_junos_set_and_hierarchy_match_facts():
    set_result = parse("""set system host-name r1
set interfaces ge-0/0/0 unit 10 family inet address 10.0.0.1/24
set interfaces ge-0/0/0 unit 10 family inet6 address 2001:db8::1/64
set routing-instances BLUE interface ge-0/0/0.10
""")
    hier_result = parse("""system { host-name r1; }
interfaces { ge-0/0/0 { unit 10 {
family inet { address 10.0.0.1/24; }
family inet6 { address 2001:db8::1/64; }
} } }
routing-instances { BLUE { interface ge-0/0/0.10; } }
""")
    assert [(a["address"], a["canonical_network"]) for a in addresses(set_result)] == [(a["address"], a["canonical_network"]) for a in addresses(hier_result)]
    assert by_name(set_result, "ge-0/0/0.10")["routing_context_id"] == by_name(hier_result, "ge-0/0/0.10")["routing_context_id"]


def test_junos_inactive_unit_does_not_disable_sibling():
    result = parse("""system { host-name r1; }
interfaces { ge-0/0/0 {
inactive: unit 10 { family inet { address 10.0.0.1/24; } }
unit 20 { family inet { address 10.0.1.1/24; } }
} }
""")
    assert by_name(result, "ge-0/0/0.10")["effective_state"] == "inactive"
    assert by_name(result, "ge-0/0/0.20")["effective_state"] == "active"
    assert by_name(result, "ge-0/0/0.10")["addresses"][0]["effective_state"] == "inactive"


def test_junos_deactivated_address_does_not_disable_sibling():
    result = parse("""set system host-name r1
set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/24
set interfaces ge-0/0/0 unit 0 family inet address 10.0.1.1/24
deactivate interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/24
""")
    assert [a["effective_state"] for a in addresses(result)] == ["inactive", "active"]
    assert by_name(result, "ge-0/0/0.0")["effective_state"] == "active"


def test_junos_apply_groups_is_partial_and_unresolved():
    result = parse("""set system host-name r1
set apply-groups GLOBAL
set groups GLOBAL interfaces ge-0/0/0 unit 0 family inet address 10.0.1.1/24
set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/24
""", "junos_set")
    assert result["status"] == "partial"
    assert [a["address"] for a in addresses(result)] == ["10.0.0.1"]
    assert all(a["effective_state"] == "unresolved" for a in addresses(result))
    assert any(d["code"] == "unresolved_inheritance" for d in result["diagnostics"])


def test_junos_hierarchy_truncation_keeps_partial_facts():
    result = parse("system { host-name r1; }\ninterfaces { ge-0/0/0 { unit 0 { family inet { address 10.0.0.1/24;", "junos_hierarchical")
    assert result["status"] == "partial"
    assert addresses(result)[0]["address"] == "10.0.0.1"
    assert any(d["code"] == "truncated_configuration" for d in result["diagnostics"])


@pytest.mark.parametrize("wrapper", ["full", "subtree", "command", "edit", "relative_set"])
def test_junos_interface_exports_keep_dual_stack_addresses_and_source_lines(wrapper):
    body = """ge-0/0/0 {
    unit 0 {
        family inet { address 192.0.2.0/31 {} }
        family inet6 { address 2001:db8::/127 { preferred; } }
    }
}"""
    if wrapper == "full":
        text = "user@edge> show configuration\n{master:0}\nsystem { host-name edge; }\ninterfaces {\n" + body + "\n}\nuser@edge>"
    elif wrapper == "subtree":
        text = body
    elif wrapper == "command":
        text = "user@edge> show configuration interfaces ge-0/0/0\n" + body.split("{", 1)[1].rsplit("}", 1)[0]
    elif wrapper == "edit":
        text = "[edit interfaces ge-0/0/0 unit 0]\nuser@edge# show configuration interfaces ge-0/0/0 unit 0\nfamily inet { address 192.0.2.0/31 {} }\nfamily inet6 { address 2001:db8::/127; }"
    else:
        text = "[edit interfaces ge-0/0/0]\nset unit 0 family inet address 192.0.2.0/31\nset unit 0 family inet6 address 2001:db8::/127"
    result = parse(text)
    assert result["status"] == "success", result["diagnostics"]
    found = by_name(result, "ge-0/0/0.0")["addresses"]
    assert [(a["address"], a["prefix_length"]) for a in found] == [("192.0.2.0", 31), ("2001:db8::", 127)]
    for address in found:
        ref = address["source_refs"][0]
        assert ref["text"] == text.splitlines()[ref["line"] - 1]
        assert address["address"] in ref["text"]


def test_junos_empty_address_blocks_respect_inactive_and_ignore_unrelated_addresses():
    result = parse("""interfaces {
    ge-0/0/0 { unit 0 { family inet {
        inactive: address 192.0.2.0/31 {}
        address 192.0.2.2/31 {}
    } } }
}
routing-options { static { route 203.0.113.0/24 { next-hop 192.0.2.3; } } }
groups { TEMPLATE { interfaces { ge-0/0/1 { unit 0 { family inet { address 198.51.100.1/24 {} } } } } } }
""")
    assert [(a["address"], a["effective_state"]) for a in addresses(result)] == [("192.0.2.0", "inactive"), ("192.0.2.2", "active")]


def test_junos_subtree_addresses_form_actual_dual_stack_interconnection():
    from backend.app.inference import infer
    parsed = [parse(f"""user@edge{index}> show configuration interfaces
ge-0/0/0 {{ unit 0 {{
    family inet {{ address 192.0.2.{index}/31 {{}} }}
    family inet6 {{ address 2001:db8::{index}/127; }}
}} }}
""", device=f"d{index}") for index in range(2)]
    snapshot = {"devices": [r["device"] for r in parsed], "interfaces": [i for r in parsed for i in r["interfaces"]], "contexts": [c for r in parsed for c in r["contexts"]], "diagnostics": []}
    result = infer(snapshot)
    assert len(result["links"]) == 1
    assert set(result["links"][0]["families"]) == {4, 6}


def test_vrf_context_aggregation_and_subinterface():
    result = parse("""hostname r1
vrf definition BLUE
 rd 65000:1
!
interface Port-channel1
!
interface Gi0/0
 channel-group 1 mode active
!
interface Port-channel1.20
 vrf forwarding BLUE
 encapsulation dot1Q 20
 ip address 10.0.0.1 255.255.255.0
!
""")
    lag, member, sub = (by_name(result, n) for n in ("Port-channel1", "Gi0/0", "Port-channel1.20"))
    blue = next(c for c in result["contexts"] if c["local_name"] == "BLUE")
    assert blue["rd"] == "65000:1"
    assert sub["routing_context_id"] == blue["id"]
    assert sub["parent_interface_id"] == lag["id"]
    assert sub["encapsulation"]["vlan_id"] == "20"
    assert lag["aggregate_members"] == [member["id"]]


def test_nokia_contexts_do_not_merge_same_interface_name():
    result = parse("""# TiMOS
configure
 router Base
  interface "uplink"
   address 10.0.0.1/24
  exit
 exit
 service
  vprn 10 customer 1 create
   interface "uplink" create
    address 10.0.0.1/24
   exit
  exit
 exit
exit all
""")
    assert result["status"] == "success"
    assert len(result["interfaces"]) == 2
    assert len({i["id"] for i in result["interfaces"]}) == 2
    assert len({i["routing_context_id"] for i in result["interfaces"]}) == 2


def test_nokia_classic_lag_member_and_port_reference():
    result = parse("""# TiMOS
configure
 lag 1
  port 1/1/1
  port 1/1/2
 exit
 router Base
  interface "uplink"
   address 10.0.0.1/24
   port lag-1
  exit
 exit
exit all
""")
    lag = by_name(result, "lag-1")
    assert len(lag["aggregate_members"]) == 2
    assert by_name(result, "uplink")["parent_interface_id"] == lag["id"]


def test_nokia_md_missing_prefix_reports_partial():
    result = parse('/configure router "Base" interface "uplink" ipv4 primary address 10.0.0.1')
    assert result["status"] == "partial"
    assert not addresses(result)
    assert any(d["code"] == "incomplete_md_address" for d in result["diagnostics"])


def test_address_ids_stable_across_line_changes_not_across_devices():
    cfg = "hostname r1\ninterface Gi0/0\n ip address 10.0.0.1/24\n!\n"
    first, second, different = parse(cfg), parse("! comment\n" + cfg), parse(cfg, device="device-2")
    assert first["interfaces"][0]["id"] == second["interfaces"][0]["id"]
    assert addresses(first)[0]["id"] == addresses(second)[0]["id"]
    assert first["interfaces"][0]["id"] != different["interfaces"][0]["id"]


@pytest.mark.parametrize("text,dialect", [("", None), ("random garbage", None), ("hostname router", "srlinux")])
def test_failed_inputs_are_honest(text, dialect):
    result = parse(text, dialect)
    assert result["status"] == "failed"
    assert result["diagnostics"]


def test_explicit_admin_state_and_unknown_operational_state():
    result = parse("hostname r1\ninterface Gi0/0\n shutdown\n ip address 10.0.0.1/24\n!\ninterface Gi0/1\n no shutdown\n!\ninterface Gi0/2\n!\n")
    assert [i["admin_state"] for i in result["interfaces"]] == ["disabled", "enabled", "unknown"]
    assert {i["oper_state"] for i in result["interfaces"]} == {"unknown"}


def test_eui64_is_unresolved_instead_of_inventing_host_address():
    result = parse("hostname r1\ninterface Gi0/0\n ipv6 address 2001:db8::/64 eui-64\n!\n")
    assert result["status"] == "partial"
    assert addresses(result)[0]["address"] is None
    assert addresses(result)[0]["effective_state"] == "unresolved"


def test_bom_ansi_crlf_preserve_line_provenance():
    result = parse('\ufeff\x1b[32mhostname r1\x1b[0m\r\ninterface Gi0/0\r\n ip address 10.0.0.1/24\r\n!\r\n')
    assert result["status"] == "success"
    assert addresses(result)[0]["source_refs"][0]["line"] == 3


def test_nxos_anycast_addresses_are_labeled():
    result = parse("hostname r1\nfeature interface-vlan\ninterface Vlan10\n ip address 10.0.0.1/24\n fabric forwarding mode anycast-gateway\n!\n")
    assert addresses(result)[0]["role"] == "anycast"


def test_junos_loopback_unit_keeps_loopback_kind():
    result = parse("set system host-name r1\nset interfaces lo0 unit 0 family inet address 10.1.1.1/24\n")
    assert by_name(result, "lo0.0")["kind"] == "loopback"


def test_md_hierarchical_slash_configure_is_not_misdetected_flat():
    text = (SAMPLES / "demo/08-access-nokia-md.cfg").read_text().replace("configure {", "/configure {", 1)
    result = parse(text)
    assert result["format"] == "md-hierarchical"
    assert result["status"] == "success"
    assert len(addresses(result)) == 4


def test_huawei_dhcp_alloc_preserves_dynamic_acquisition():
    result = parse("sysname r1\ninterface GigabitEthernet0/0/0\n ip address dhcp-alloc\n#\nreturn\n")
    assert addresses(result)[0]["address"] is None
    assert addresses(result)[0]["acquisition_method"] == "dhcp"


def test_junos_inactive_routing_instance_cannot_form_ordinary_relations():
    result = parse("""set system host-name r1
set interfaces ge-0/0/0 unit 0 family inet address 10.0.0.1/24
set routing-instances BLUE interface ge-0/0/0.0
deactivate routing-instances BLUE
""")
    assert result["status"] == "partial"
    assert addresses(result)[0]["effective_state"] == "unresolved"


def test_junos_interface_ranges_are_not_claimed_complete():
    result = parse("""set system host-name r1
set interfaces interface-range RANGE member ge-0/0/0
set interfaces interface-range RANGE unit 0 family inet address 10.0.0.1/24
""")
    assert result["status"] == "partial"
    assert not addresses(result)
    assert any(d["code"] == "unresolved_interface_range" for d in result["diagnostics"])


def test_missing_aggregate_declaration_is_reported():
    result = parse("hostname r1\ninterface Gi0/0\n channel-group 1 mode active\n!\n")
    assert result["status"] == "partial"
    assert any(d["code"] == "missing_aggregate_declaration" for d in result["diagnostics"])


def test_symlinked_venv_upstream_containment_bug_uses_trusted_package_resource(monkeypatch):
    import ttp_templates
    from backend.app.parser import _platform_template
    _platform_template.cache_clear()
    def bad_symlink_check(**kwargs):
        raise ValueError("Template path resolves outside the package directory.")
    monkeypatch.setattr(ttp_templates, "get_template", bad_symlink_check)
    result = parse((SAMPLES / "demo/06-branch-cisco-ios.cfg").read_text())
    assert result["status"] == "success"
    assert not any(d["code"] == "upstream_template_unavailable" for d in result["diagnostics"])
    with pytest.raises(ValueError, match="registered"):
        _platform_template("../../../../etc/passwd")
    _platform_template.cache_clear()
