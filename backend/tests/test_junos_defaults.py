"""Native Junos defaults: ISO activation, metric defaults and FIB policy."""
import pytest

from backend.app.parser import parse_config


CONFIG = '''set system host-name J
set interfaces lo0 unit 0 family inet address 10.255.1.1/32
set interfaces lo0 unit 0 family iso address 49.0001.0000.0000.0011.00
set interfaces ge-0/0/0 unit 0 family inet address 10.0.1.1/30
set interfaces ge-0/0/0 unit 0 family iso
set routing-options router-id 10.255.1.1
set protocols ospf area 0 interface lo0.0 passive
set protocols ospf area 0 interface ge-0/0/0.0
set protocols isis interface lo0.0
set protocols isis interface ge-0/0/0.0
'''


def parse(text=CONFIG,dialect='junos_set'):
    return parse_config(text,'junos.cfg','source','device',dialect)


def protocol_bindings(parsed,protocol):
    instances={i['id'] for i in parsed['routing']['instances'] if i['protocol']==protocol}
    return [b for b in parsed['routing']['bindings'] if b['instance_id'] in instances]


def test_documented_junos_igp_defaults_have_profile_provenance():
    parsed=parse()
    assert not parsed['routing']['issues']
    facts=parsed['routing'];interfaces={i['id']:i for i in parsed['interfaces']}
    assert {i['forwarding_mode'] for i in facts['instances']}=={'single_next_hop'}
    assert {i['max_paths'] for i in facts['instances']}=={1}
    assert all(i['defaults_profile'] for i in facts['instances'])
    ospf=protocol_bindings(parsed,'ospf')
    loop=next(b for b in ospf if interfaces[b['interface_id']]['kind']=='loopback')
    transit=next(b for b in ospf if interfaces[b['interface_id']]['raw_name']=='ge-0/0/0.0')
    assert loop['cost']==0 and loop['cost_origin']=='profile_default'
    assert transit['cost'] is None and transit['cost_origin']=='unknown'
    assert transit['network_type']=='broadcast' and (transit['hello'],transit['dead'])==(10,40)
    isis=protocol_bindings(parsed,'isis')
    assert {b['scope'] for b in isis}=={'1','2'}
    assert {b['cost'] for b in isis}=={0,10}
    assert {b['metric_limit'] for b in isis}=={63}
    assert all(b['passive'] for b in isis if interfaces[b['interface_id']]['kind']=='loopback')


@pytest.mark.parametrize('variant',[
    CONFIG.replace('set interfaces ge-0/0/0 unit 0 family iso\n',''),
    CONFIG+'deactivate interfaces ge-0/0/0 unit 0 family iso\n',
])
def test_missing_or_deactivated_iso_family_cannot_enable_transit_isis(variant):
    parsed=parse(variant)
    iface=next(i for i in parsed['interfaces'] if i['raw_name']=='ge-0/0/0.0')
    assert not [b for b in protocol_bindings(parsed,'isis') if b['interface_id']==iface['id']]
    issues=parsed['routing']['issues']
    assert any(i['code']=='missing_iso_family' and i['interface_id']==iface['id'] for i in issues)
    assert any(b['interface_id']==iface['id'] for b in protocol_bindings(parsed,'ospf'))


def test_bare_physical_protocol_reference_uses_units_iso_family():
    parsed=parse(CONFIG.replace('set protocols isis interface ge-0/0/0.0','set protocols isis interface ge-0/0/0'))
    assert len(protocol_bindings(parsed,'isis'))==4 and not parsed['routing']['issues']


def test_interface_all_checks_iso_family_on_each_numbered_unit():
    text=CONFIG.replace('set protocols isis interface lo0.0\n','').replace('set protocols isis interface ge-0/0/0.0\n','')+'set protocols isis interface all\n'
    assert len(protocol_bindings(parse(text),'isis'))==4
    text=text.replace('set interfaces ge-0/0/0 unit 0 family iso\n','')
    parsed=parse(text)
    assert len(protocol_bindings(parsed,'isis'))==2
    assert any(i['code']=='missing_iso_family' for i in parsed['routing']['issues'])


HIERARCHY='''system { host-name J; }
interfaces {
 lo0 { unit 0 { family inet { address 10.255.1.1/32; } family iso { address 49.0001.0000.0000.0011.00; } } }
 ge-0/0/0 { unit 0 { family inet { address 10.0.1.1/30; } family iso { } } }
}
routing-options { router-id 10.255.1.1; }
protocols {
 ospf { area 0 { interface lo0.0 { passive; } interface ge-0/0/0.0; } }
 isis { interface lo0.0; interface ge-0/0/0.0; }
}
'''


def test_hierarchical_empty_iso_family_is_effective_and_inactive_iso_is_not():
    parsed=parse(HIERARCHY,'junos_hierarchical')
    assert len(protocol_bindings(parsed,'isis'))==4 and not parsed['routing']['issues']
    text=HIERARCHY.replace('family iso { }','inactive: family iso { }')
    parsed=parse(text,'junos_hierarchical')
    assert len(protocol_bindings(parsed,'isis'))==2
    assert any(i['code']=='missing_iso_family' for i in parsed['routing']['issues'])


@pytest.mark.parametrize('policy',[
    'set policy-options policy-statement LB then load-balance per-flow\n',
    'set policy-options policy-statement LB then load-balance per-packet\n',
    'set policy-options policy-statement LB term ALL then load-balance per-packet\nset policy-options policy-statement LB term ALL then accept\n',
])
def test_unconditional_forwarding_table_export_enables_ecmp(policy):
    parsed=parse(CONFIG+policy+'set routing-options forwarding-table export LB\n')
    assert not parsed['routing']['issues']
    assert {i['forwarding_mode'] for i in parsed['routing']['instances']}=={'ecmp'}
    assert all(i['max_paths'] is None for i in parsed['routing']['instances'])


@pytest.mark.parametrize('policy',[
    '',
    'set policy-options policy-statement LB term MATCH from protocol ospf\nset policy-options policy-statement LB term MATCH then load-balance per-packet\n',
    'set policy-options policy-statement LB term FIRST then reject\nset policy-options policy-statement LB term SECOND then load-balance per-flow\n',
    'set policy-options policy-statement LB then next policy\n',
    'set policy-options policy-statement LB then load-balance per-prefix\n',
    'set policy-options policy-statement LB apply-groups GROUP\n',
])
def test_unresolved_or_conditional_fib_policy_stays_unknown_without_blocking_spf(policy):
    parsed=parse(CONFIG+policy+'set routing-options forwarding-table export LB\n')
    assert not parsed['routing']['issues']
    assert {i['forwarding_mode'] for i in parsed['routing']['instances']}=={'unknown'}
    assert all(i['max_paths'] is None for i in parsed['routing']['instances'])
    assert protocol_bindings(parsed,'isis')


def test_policy_lists_and_inactive_policies_do_not_fabricate_ecmp_support():
    lb='set policy-options policy-statement LB then load-balance per-flow\n'
    text=CONFIG+lb+'set routing-options forwarding-table export [ LB OTHER ]\n'
    assert {i['forwarding_mode'] for i in parse(text)['routing']['instances']}=={'unknown'}
    text=CONFIG+lb+'set routing-options forwarding-table export [ LB ]\n'
    assert {i['forwarding_mode'] for i in parse(text)['routing']['instances']}=={'ecmp'}
    text+='deactivate policy-options policy-statement LB\n'
    assert {i['forwarding_mode'] for i in parse(text)['routing']['instances']}=={'unknown'}


def test_hierarchical_unconditional_export_policy_is_resolved():
    text=HIERARCHY+'''policy-options { policy-statement LB { then { load-balance per-packet; } } }
routing-options { forwarding-table { export LB; } }
'''
    assert {i['forwarding_mode'] for i in parse(text,'junos_hierarchical')['routing']['instances']}=={'ecmp'}


def test_routing_instance_does_not_inherit_default_instances_fib_export():
    text='''set system host-name VRF
set interfaces ge-0/0/0 unit 0 family inet address 10.0.1.1/30
set routing-instances BLUE instance-type vrf
set routing-instances BLUE interface ge-0/0/0.0
set routing-instances BLUE routing-options router-id 10.255.1.1
set routing-instances BLUE protocols ospf area 0 interface ge-0/0/0.0
set routing-options forwarding-table export LB
set policy-options policy-statement LB then load-balance per-flow
'''
    facts=parse(text)['routing']
    assert len(facts['instances'])==1 and facts['instances'][0]['forwarding_mode']=='single_next_hop'
    text+='set routing-instances BLUE routing-options forwarding-table export LB\n'
    assert parse(text)['routing']['instances'][0]['forwarding_mode']=='ecmp'


def test_junos_both_metric_limit_changes_only_on_explicit_wide_level():
    text=CONFIG+'set protocols isis interface ge-0/0/0.0 level 2 metric 64\n'
    assert any(i['code']=='metric_range' for i in parse(text)['routing']['issues'])
    text+='set protocols isis level 2 wide-metrics-only\n'
    parsed=parse(text)
    assert not parsed['routing']['issues']
    assert {b['metric_limit'] for b in protocol_bindings(parsed,'isis') if b['scope']=='1'}=={63}
    assert {b['cost'] for b in protocol_bindings(parsed,'isis') if b['scope']=='2'}=={0,64}


def test_custom_hello_does_not_rewrite_independent_default_dead_interval():
    parsed=parse(CONFIG+'set protocols ospf area 0 interface ge-0/0/0.0 hello-interval 2\n')
    transit=next(b for b in protocol_bindings(parsed,'ospf') if b['network_type']=='broadcast')
    assert (transit['hello'],transit['dead'])==(2,40)
